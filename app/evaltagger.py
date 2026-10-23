"""驗證 WD Tagger 的角色辨識準確度（唯讀，不改任何資料）。

做法：資料庫裡存的 embedding 就是模型最後一層（分類頭）的輸入，所以把分類頭的權重從 ONNX 取出來，
就能對每張圖重算角色分數，不用重新讀圖。拿「不是模型給的」確認角色（Danbooru 搜源、你手動加的、Rule34、簽名）當答案，
算每張圖：全對／有認錯／沒認出來（搜不到也算沒中）。
重算的分數會先跟模型當初存下的分數比對，差太多就不報告（避免拿錯的數字下結論）。
"""
from __future__ import annotations

import json
import logging

import numpy as np

from . import db

log = logging.getLogger("evaltagger")
_head: dict = {}
FURRY = ("furry", "anthro", "furry_female", "furry_male", "furry_with_non-furry", "kemono")
THRESHOLDS = (0.5, 0.7, 0.8, 0.85, 0.9, 0.95, 0.97)


def _load_head():
    """分類頭：最後一個 Gemm（或 MatMul＋Add）的權重和偏差。"""
    if _head:
        return _head
    import onnx
    from onnx import numpy_helper
    from . import tagger
    tg = tagger.ensure()
    if tg is None:
        raise RuntimeError("模型沒有載入")
    d = tagger._download(tg.repo)
    m = onnx.load(str(d / "model.onnx"))
    g = m.graph
    inits = {i.name: i for i in g.initializer}
    producers = {o: n for n in g.node for o in n.output}
    name, bias = g.output[0].name, None
    for _ in range(12):
        n = producers.get(name)
        if n is None:
            break
        if n.op_type == "Add" and bias is None:
            b = [x for x in n.input if x in inits]
            if b:
                bias = numpy_helper.to_array(inits[b[0]]).astype(np.float32)
                name = [x for x in n.input if x not in inits][0]
                continue
        if n.op_type in ("Gemm", "MatMul"):
            w = numpy_helper.to_array(inits[n.input[1]]).astype(np.float32)
            trans_b = any(a.name == "transB" and a.i == 1 for a in n.attribute)
            if trans_b:
                w = w.T
            if n.op_type == "Gemm" and len(n.input) > 2 and n.input[2] in inits:
                bias = numpy_helper.to_array(inits[n.input[2]]).astype(np.float32)
            _head.update(w=w, b=bias if bias is not None else np.zeros(w.shape[1], np.float32),
                         names=tg.names, char_idx=tg.char_idx, gen_idx=tg.general_idx)
            return _head
        name = n.input[0]
    raise RuntimeError("找不到分類頭")


def _char_probs(blob: bytes, h: dict) -> np.ndarray:
    e = np.frombuffer(blob, dtype=np.float16).astype(np.float32)
    if e.shape[0] != h["w"].shape[0]:
        return None
    z = e @ h["w"][:, h["char_idx"]] + h["b"][h["char_idx"]]
    return 1 / (1 + np.exp(-z))


_alias_cache: dict = {}


def _dan_aliases(names_: list[str]) -> dict[str, str]:
    """Danbooru 改過名的角色 tag（模型是用舊名字訓練的，例如 gojou_satoru → gojo_satoru）。查一次記住。"""
    todo = [n for n in names_ if n not in _alias_cache]
    if todo:
        from .danbooru import dan_get
        for i in range(0, len(todo), 100):
            ch = todo[i:i + 100]
            try:
                rows = dan_get("/tag_aliases.json", {"search[antecedent_name_comma]": ",".join(ch), "search[status]": "active",
                                                     "limit": 200, "only": "antecedent_name,consequent_name"}) or []
            except Exception as e:  # noqa
                log.info("查 Danbooru 改名失敗：%s", e)
                rows = []
            got = {r["antecedent_name"]: r["consequent_name"] for r in rows}
            for n in ch:
                _alias_cache[n] = got.get(n, n)
    return {n: _alias_cache.get(n, n) for n in names_}


def evaluate() -> dict:
    from . import names as nm
    h = _load_head()
    c = db.conn()
    local = {r[0]: r[1] for r in c.execute("SELECT a.name, t.name FROM tag_alias a JOIN tags t ON t.id=a.tag_id")}

    def canon(n: str) -> str:
        return local.get(n, n)
    ph = ",".join("?" * len(FURRY))
    anime = [r[0] for r in c.execute(f"""SELECT m.id FROM media m WHERE m.kind='image' AND m.status='done' AND COALESCE(m.excluded,0)=0
        AND COALESCE(m.media_type,'illustration') NOT IN ('photo','cosplay','figure') AND m.embedding IS NOT NULL
        AND NOT EXISTS (SELECT 1 FROM media_tags g JOIN tags gt ON gt.id=g.tag_id WHERE g.media_id=m.id AND g.state>=0 AND gt.name IN ({ph}))""", FURRY)]
    aset = set(anime)
    raw = [h["names"][i] for i in h["char_idx"]]
    dal = _dan_aliases(raw)
    cnames = [canon(dal[n]) for n in raw]          # 模型的名字 → Danbooru 現在的名字 → 你圖庫裡合併後的名字
    renamed = sum(1 for a, b in zip(raw, cnames) if a != b)
    known = set(cnames)
    # 答案：確認過的角色（記下來源，只有非模型來源的才算「獨立答案」）
    truth: dict[int, dict[str, str]] = {}
    for r in c.execute("""SELECT mt.media_id, t.name, mt.source FROM media_tags mt JOIN tags t ON t.id=mt.tag_id
                          WHERE mt.state=1 AND t.category='character'"""):
        if r[0] in aset:
            truth.setdefault(r[0], {})[canon(r[1])] = r[2]
    # 自我檢查：重算分數 vs 模型當初存的分數
    diffs = []
    for r in c.execute("""SELECT mt.media_id, t.name, mt.score, m.embedding FROM media_tags mt JOIN tags t ON t.id=mt.tag_id
                          JOIN media m ON m.id=mt.media_id WHERE mt.source='model' AND t.category='character' AND mt.score IS NOT NULL
                          ORDER BY mt.media_id DESC LIMIT 300"""):
        if r[1] in raw and r[3] is not None:
            p = _char_probs(r[3], h)
            if p is not None:
                diffs.append(abs(float(p[raw.index(r[1])]) - float(r[2])))
    check = {"n": len(diffs), "mean_abs_diff": round(float(np.mean(diffs)), 4) if diffs else None,
             "max_abs_diff": round(float(np.max(diffs)), 4) if diffs else None}
    if not diffs or check["mean_abs_diff"] > 0.02:
        return {"error": "重算的分數跟模型存的分數對不上，不報告結果", "check": check}

    def outcome(preds: list[str], ans: set[str]) -> str:
        ok = lambda p: p in ans or any(nm.base_of(a, {p}) == p for a in ans)   # 預測本體、答案是造型也算對
        if not preds:
            return "miss"
        return "correct" if all(ok(p) for p in preds) else ("partial" if any(ok(p) for p in preds) else "wrong")

    indep = {mid: {n for n, s in t.items()} for mid, t in truth.items() if any(s != "model" for s in t.values())}
    is_known = lambda a: a in known or bool(nm.base_of(a, known))
    res = {t: {"correct": 0, "partial": 0, "wrong": 0, "miss": 0} for t in THRESHOLDS}       # 答案角色模型都認識的圖
    res_all = {t: {"correct": 0, "partial": 0, "wrong": 0, "miss": 0} for t in THRESHOLDS}   # 全部有答案的圖（不認識的也算沒中）
    unknown_names: dict[str, int] = {}
    not_in_vocab = 0
    char_hit = {t: [0, 0] for t in THRESHOLDS}      # 角色層級召回：答案裡的角色有幾個被認出
    cover = {t: 0 for t in THRESHOLDS}               # 全部動漫圖：模型至少給一個角色的比例
    examples = {"wrong": [], "miss": []}
    rows = c.execute(f"SELECT id, embedding FROM media WHERE id IN ({','.join(map(str, anime))})").fetchall() if anime else []
    for mid, blob in rows:
        p = _char_probs(blob, h)
        if p is None:
            continue
        order = np.argsort(-p)[:10]
        for t in THRESHOLDS:
            if p[order[0]] >= t:
                cover[t] += 1
        if mid not in indep:
            continue
        ans = indep[mid]
        all_known = all(is_known(a) for a in ans)
        if not all_known:
            not_in_vocab += 1
            for a in ans:
                if not is_known(a):
                    unknown_names[a] = unknown_names.get(a, 0) + 1
        for t in THRESHOLDS:
            preds = [cnames[i] for i in order if p[i] >= t]
            o = outcome(preds, ans)
            res_all[t][o] += 1
            if not all_known:
                continue
            res[t][o] += 1
            for a in ans:
                char_hit[t][1] += 1
                char_hit[t][0] += any(q == a or nm.base_of(a, {q}) == q for q in preds)
            if t == 0.85 and o in ("wrong", "miss", "partial") and len(examples.setdefault(o, [])) < 10:
                examples[o].append({"id": mid, "answer": sorted(ans), "pred": [(cnames[i], round(float(p[i]), 3)) for i in order[:3]]})
    n = len(indep)
    return {"check": check, "anime_images": len(anime), "answer_images": n, "answer_not_in_model_vocab": not_in_vocab,
            "model_names_renamed": renamed, "all_answer_images": res_all,
            "unknown_top": sorted(unknown_names.items(), key=lambda kv: -kv[1])[:25],
            "by_threshold": {str(t): {**v, "char_recall": round(char_hit[t][0] / max(1, char_hit[t][1]), 3),
                                      "coverage_all_anime": round(cover[t] / max(1, len(anime)), 3)} for t, v in res.items()},
            "examples": examples}


def search_rates() -> dict:
    """搜源（搜不到也算沒中）：動漫圖裡每一種方法找到幾張、找到的有沒有被你否決過。"""
    c = db.conn()
    ph = ",".join("?" * len(FURRY))
    anime = {r[0] for r in c.execute(f"""SELECT m.id FROM media m WHERE m.kind='image' AND m.status='done' AND COALESCE(m.excluded,0)=0
        AND COALESCE(m.media_type,'illustration') NOT IN ('photo','cosplay','figure')
        AND NOT EXISTS (SELECT 1 FROM media_tags g JOIN tags gt ON gt.id=g.tag_id WHERE g.media_id=m.id AND g.state>=0 AND gt.name IN ({ph}))""", FURRY)}
    rej = {r[0] for r in c.execute("""SELECT DISTINCT mt.media_id FROM media_tags mt JOIN tags t ON t.id=mt.tag_id
        WHERE mt.state=-1 AND mt.source IN ('danbooru','rule34','e621','signature') AND t.category IN ('character','artist','copyright')""")}
    out: dict = {}
    iqdb_tried = 0
    for r in c.execute("SELECT media_id, status, method, data FROM media_source"):
        if r[0] not in anime:
            continue
        d = json.loads(r[3] or "{}")
        if "iqdb_ms" in d:
            iqdb_tried += 1
        k = r[2] if r[1] == "found" else "notfound"
        o = out.setdefault(k, {"n": 0, "rejected": 0})
        o["n"] += 1
        o["rejected"] += r[0] in rej
    sig = c.execute("""SELECT COUNT(DISTINCT mt.media_id) FROM media_tags mt WHERE mt.source='signature' AND mt.state=1""").fetchone()[0]
    return {"anime_images": len(anime), "iqdb_uploads": iqdb_tried, "by_method": out, "signature_confirmed_images": sig}


def general_eval() -> dict:
    """一般標籤：WD Tagger（你設定的門檻）跟 Danbooru 人工標籤比。只看跟 Danbooru 原檔完全一樣的圖（答案可靠）。"""
    h = _load_head()
    c = db.conn()
    thr = float(db.get_settings()["general_threshold"])
    gidx = h["gen_idx"]
    gnames = [h["names"][i] for i in gidx]
    tp = fp = fn = 0
    per_img = []
    bands = {b: [0, 0] for b in ((0.35, 0.5), (0.5, 0.65), (0.65, 0.8), (0.8, 0.9), (0.9, 1.01))}   # 各分數段：在 Danbooru 上／不在
    common_fp: dict[str, int] = {}
    common_fn: dict[str, int] = {}
    vocab = set(gnames)
    for r in c.execute("""SELECT s.data, m.embedding FROM media_source s JOIN media m ON m.id=s.media_id
                          WHERE s.status='found' AND s.method IN ('md5','md5_name','pixel_hash','x_media') AND m.embedding IS NOT NULL"""):
        dan = (json.loads(r[0] or "{}").get("danbooru") or {})
        if "general" not in dan:
            continue
        e = np.frombuffer(r[1], dtype=np.float16).astype(np.float32)
        if e.shape[0] != h["w"].shape[0]:
            continue
        z = e @ h["w"][:, gidx] + h["b"][gidx]
        p = 1 / (1 + np.exp(-z))
        pred = {gnames[i] for i in np.where(p >= thr)[0]}
        ans = set(dan["general"]) & vocab          # 只比模型認得的標籤（模型不可能猜出它不知道的詞）
        for i in np.where(p >= 0.35)[0]:
            for (lo, hi), v in bands.items():
                if lo <= p[i] < hi:
                    v[0 if gnames[i] in ans else 1] += 1
        a, b_, c_ = len(pred & ans), len(pred - ans), len(ans - pred)
        tp += a; fp += b_; fn += c_
        per_img.append((a, b_, c_, len(set(dan["general"]) - vocab)))
        for t in pred - ans:
            common_fp[t] = common_fp.get(t, 0) + 1
        for t in ans - pred:
            common_fn[t] = common_fn.get(t, 0) + 1
    n = len(per_img)
    return {"images": n, "threshold": thr,
            "precision": round(tp / max(1, tp + fp), 3), "recall": round(tp / max(1, tp + fn), 3),
            "avg_per_image": {"wd_correct": round(tp / max(1, n), 1), "wd_wrong": round(fp / max(1, n), 1),
                              "wd_missed": round(fn / max(1, n), 1), "danbooru_outside_wd_vocab": round(sum(x[3] for x in per_img) / max(1, n), 1)},
            "by_score": {f"{lo}-{min(hi, 1)}": {"on_danbooru": v[0], "not_on_danbooru": v[1], "agree": round(v[0] / max(1, sum(v)), 3)}
                         for (lo, hi), v in bands.items()},
            "top_wrong": sorted(common_fp.items(), key=lambda kv: -kv[1])[:12],
            "top_missed": sorted(common_fn.items(), key=lambda kv: -kv[1])[:12]}
