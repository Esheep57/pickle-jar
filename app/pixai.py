"""PixAI Tagger v1.0（第二個 AI 模型）：比 WD v3 新（資料到 2026-05）、認得的角色多（8,308 個 vs WD 約 2,900）、
還會猜畫風（4,917 位畫師）。

跟 WD 的分工：
- WD 照舊負責一般標籤、分級、比對用的特徵（同圖判斷、你的圖庫推薦都靠它的 embedding），不換掉
- PixAI 只補身分：角色、作品、畫師。結果全部先存在 pixai_pred（機率 ≥ 0.1 的都留，評估時才能調門檻）
- 只有評估過、你開了「放進待確認」（pixai_candidates）才變成候選：tier 4（跟 WD 同一層）、source 'pixai'，一律要你按「是」

模型：Mexes/pixai-tagger-v1.0-onnx-fp32-fp16-int8（官方 safetensors 的 ONNX 轉檔，Apache 2.0）。
有 GPU（CUDA／DirectML）用 FP16（947 MB），只有 CPU 用 INT8（523 MB）。輸入 1008×1008、等比縮放置中、黑邊、(x/255-0.5)/0.5、NCHW。

背景工作 kind 'pixai'：一次做一塊（CHUNK 張）就讓出佇列，掃描新圖不會被卡幾個小時；還有沒做的就再排自己。
順序：沒有已確認角色的插畫先（最有用），再來其他圖；影片不做。
"""
from __future__ import annotations

import csv
import json
import logging
import os
import threading
import time
from pathlib import Path

import numpy as np
from PIL import Image

from . import db, media, tagger

log = logging.getLogger("pixai")

REPO = "Mexes/pixai-tagger-v1.0-onnx-fp32-fp16-int8"
SIZE = 1008
KEEP = 0.10            # 存下來的最低機率（評估要看門檻以下的）
CHUNK = 64
CATS = {1: "artist", 3: "copyright", 4: "character"}
SRC = "pixai"

_inst = None
_lock = threading.Lock()
_RUN = threading.Lock()


def _files(gpu: bool) -> tuple[str, str]:
    return ("model_fp16.onnx" if gpu else "model_int8.onnx"), "selected_tags.csv"


def _download(name: str, progress=None) -> Path:
    d = tagger.MODELS_DIR / REPO.replace("/", "__")
    d.mkdir(parents=True, exist_ok=True)
    p = d / name
    if p.exists():
        return p
    import httpx
    base = (db.get_settings().get("hf_endpoint") or os.environ.get("HF_ENDPOINT") or "https://huggingface.co").rstrip("/")
    url = f"{base}/{REPO}/resolve/main/{name}"
    tmp = d / (name + ".part")
    log.info("下載 %s", url)
    with httpx.stream("GET", url, follow_redirects=True, timeout=120) as r:
        r.raise_for_status()
        total = int(r.headers.get("content-length") or 0)
        got = 0
        with open(tmp, "wb") as out:
            for chunk in r.iter_bytes(1 << 20):
                out.write(chunk)
                got += len(chunk)
                if progress:
                    progress(got, total, name)
    tmp.replace(p)
    return p


class PixAI:
    def __init__(self, progress=None):
        import onnxruntime as ort
        provs = tagger._providers()
        gpu = bool(provs) and provs[0] in ("CUDAExecutionProvider", "DmlExecutionProvider")
        mname, tname = _files(gpu)
        tags_path = _download(tname, progress)
        model_path = _download(mname, progress)
        rows = list(csv.DictReader(open(tags_path, encoding="utf-8")))
        self.names = [r["name"] for r in rows]
        cats = np.array([int(r["category"]) for r in rows])
        self.idx = {cat: np.where(cats == k)[0] for k, cat in CATS.items()}
        so = ort.SessionOptions()
        so.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        if provs and provs[0] == "DmlExecutionProvider":
            so.enable_mem_pattern = False
            so.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
        self.sess = ort.InferenceSession(str(model_path), so, providers=provs)
        self.provider = self.sess.get_providers()[0]
        self.model_file = mname
        self.input_name = self.sess.get_inputs()[0].name
        log.info("PixAI 就緒：%s, provider=%s", mname, self.provider)

    @staticmethod
    def preprocess(img: Image.Image) -> np.ndarray:
        img = img.convert("RGBA") if img.mode in ("P", "LA", "RGBA") else img.convert("RGB")
        if img.mode == "RGBA":            # 透明背景：跟官方一樣貼在黑底上之前先合成白底（透明區塊當白色比較接近網站上看到的）
            bg = Image.new("RGBA", img.size, (255, 255, 255, 255))
            bg.alpha_composite(img)
            img = bg.convert("RGB")
        s = SIZE / max(img.size)
        w, h = max(1, round(img.width * s)), max(1, round(img.height * s))
        img = img.resize((w, h), Image.BICUBIC)
        canvas = Image.new("RGB", (SIZE, SIZE))
        canvas.paste(img, ((SIZE - w) // 2, (SIZE - h) // 2))
        x = (np.asarray(canvas, dtype=np.float32) / 255.0 - 0.5) / 0.5
        return np.ascontiguousarray(x.transpose(2, 0, 1))

    def run(self, batch: np.ndarray) -> np.ndarray:
        with _RUN:
            p = self.sess.run(None, {self.input_name: batch})[0]
        if p.min() < 0 or p.max() > 1:
            p = 1 / (1 + np.exp(-p))
        return p

    def decode(self, probs: np.ndarray, keep: float = KEEP) -> dict:
        out = {}
        for cat, ix in self.idx.items():
            sel = ix[probs[ix] >= keep]
            out[cat] = {self.names[i]: round(float(probs[i]), 4) for i in sel[np.argsort(-probs[sel])][:15]}
        return out


def get(progress=None) -> PixAI:
    global _inst
    with _lock:
        if _inst is None:
            _inst = PixAI(progress)
        return _inst


def loaded() -> PixAI | None:
    return _inst


# ---------------- 要做哪些圖 ----------------
def pending_ids(limit: int | None = None) -> list[int]:
    """還沒跑過（或檔案換過）的圖片。沒有已確認角色的插畫先，其他的後；同一類新的先。"""
    c = db.conn()
    has_char = {r[0] for r in c.execute("""SELECT DISTINCT mt.media_id FROM media_tags mt JOIN tags t ON t.id=mt.tag_id
        WHERE mt.state=1 AND t.category='character'""")}
    rows = c.execute("""SELECT m.id, m.pile FROM media m LEFT JOIN pixai_pred p ON p.media_id=m.id
        WHERE m.status='done' AND m.kind='image' AND COALESCE(m.excluded,0)=0
          AND m.id NOT IN (SELECT media_id FROM album_items)
          AND (p.media_id IS NULL OR COALESCE(p.fp,'') != COALESCE(m.fp,''))
        ORDER BY m.id DESC""").fetchall()
    rank = lambda r: (r[0] in has_char, {"illust": 0, "comic": 1, "texty": 2}.get(r[1], 3))
    ids = [r[0] for r in sorted(rows, key=rank)]
    return ids[:limit] if limit else ids


def stats() -> dict:
    c = db.conn()
    done = c.execute("SELECT COUNT(*) FROM pixai_pred p JOIN media m ON m.id=p.media_id WHERE m.status='done'").fetchone()[0]
    total = c.execute("SELECT COUNT(*) FROM media WHERE status='done' AND kind='image' AND COALESCE(excluded,0)=0").fetchone()[0]
    cands = c.execute("SELECT COUNT(*) FROM media_tags WHERE source=? AND state=0", (SRC,)).fetchone()[0]
    conf = c.execute("SELECT COUNT(*) FROM media_tags WHERE source=? AND state=1", (SRC,)).fetchone()[0]
    return {"done": done, "total": total, "candidates": cands, "confirmed": conf,
            "provider": STATUS.get("provider"), "model": STATUS.get("model"), "last_error": STATUS.get("last_error"),
            "crashes": db.get_settings().get("_pixai_crashes") or 0, "disabled": STATUS.get("disabled")}


# ---------------- 背景工作 ----------------
def run(jobs, ids: list[int] | None = None):
    """背景工作：做一塊（CHUNK 張；指定 ids 就做那些）。還有沒做的就再排一次自己，讓其他工作（掃描新圖）插得進來。

    推論在獨立的子程式（app/pixai_worker.py）裡做：第一次上線時 GPU 推論讓整個 Pickle Jar 直接崩潰（沒有 Python 錯誤），
    分開以後就算子程式死掉，主程式照常開著；連續兩次死掉就自動關掉 PixAI，原因記在「程式出錯」和 data/pixai_crash.log。"""
    import queue as _q
    import subprocess
    import sys
    s = db.get_settings()
    if not s.get("pixai_enabled"):
        return
    explicit = ids is not None
    todo = [int(i) for i in ids] if explicit else pending_ids(CHUNK)
    if not todo:
        return
    jobs.state.update(phase="PixAI：準備模型")

    def dl(got, total, f):
        jobs.state.update(phase=f"PixAI：下載模型（{f}）", download={"file": f, "got": got, "total": total})
    provs = tagger._providers()
    gpu = bool(provs) and provs[0] in ("CUDAExecutionProvider", "DmlExecutionProvider")
    for f in _files(gpu)[::-1]:
        _download(f, dl)
    jobs.state.update(download=None)
    c = db.conn()
    paths = {r[0]: r[1] for r in c.execute(
        f"SELECT id, path FROM media WHERE id IN ({','.join('?' * len(todo))}) AND status='done' AND kind='image'", todo)}
    todo = [i for i in todo if i in paths]
    remaining = len(pending_ids()) if not explicit else len(todo)
    root = Path(__file__).resolve().parent.parent
    env = dict(os.environ, PYTHONIOENCODING="utf-8", LIB_DATA_DIR=str(db.DATA_DIR), LIB_MODELS_DIR=str(tagger.MODELS_DIR))
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    errf = open(Path(db.DATA_DIR) / "pixai_worker.log", "a", encoding="utf-8", errors="replace")   # ONNX Runtime 的訊息（崩潰前最後幾行）
    errf.write(f"\n=== {time.strftime('%Y-%m-%d %H:%M:%S')} 開始 {len(todo)} 張\n")
    errf.flush()
    proc = subprocess.Popen([sys.executable, "-m", "app.pixai_worker"], cwd=str(root), env=env, stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE, stderr=errf, text=True, encoding="utf-8", creationflags=flags)
    lines: _q.Queue = _q.Queue()
    import threading as _th
    _th.Thread(target=lambda: [lines.put(l) for l in proc.stdout] + [lines.put(None)], daemon=True, name="pixai-read").start()

    def read(timeout):
        try:
            l = lines.get(timeout=timeout)
        except _q.Empty:
            return "timeout"
        return json.loads(l) if l else None

    try:
        hello = read(600)              # 載入模型（第一次可能要一分鐘）
        if not isinstance(hello, dict) or not hello.get("ready"):
            raise _WorkerDied(hello.get("error") if isinstance(hello, dict) else ("載入模型逾時" if hello == "timeout" else "子程式結束了"))
        STATUS.update(provider=hello.get("provider"), model=hello.get("model"))
        jobs.state.update(phase=f"PixAI 認角色／畫師（還有 {remaining:,} 張，{hello.get('provider')}）")
        t0 = time.time()
        for n, mid in enumerate(todo):
            if not jobs.checkpoint():
                break
            proc.stdin.write(json.dumps({"mid": mid, "path": paths[mid]}, ensure_ascii=False) + "\n")
            proc.stdin.flush()
            r = read(180)
            if not isinstance(r, dict):
                _save(mid, None, "PixAI 子程式在這張圖結束了" if r is None else "PixAI 逾時")
                raise _WorkerDied("在 media %s 結束（%s）" % (mid, "逾時" if r == "timeout" else f"code {proc.poll()}"))
            _save(int(r["mid"]), r.get("pred"), r.get("error"), s)
            jobs.progress(n + 1, len(todo), t0)
        _crash(reset=True)
    except (_WorkerDied, BrokenPipeError, OSError) as e:
        _crash(str(e))
        return
    finally:
        try:
            proc.stdin.close()
        except Exception:  # noqa
            pass
        try:
            proc.wait(timeout=20)
        except Exception:  # noqa
            proc.kill()
        errf.close()
    if not explicit and not jobs.stop_flag.is_set() and pending_ids(1):
        jobs.submit("pixai")


class _WorkerDied(Exception):
    pass


STATUS: dict = {}


def _crash(msg: str | None = None, reset: bool = False):
    """子程式死掉的次數：連續兩次就關掉 PixAI（不然每次啟動、每批都在同一個地方崩潰）。"""
    from . import errlog
    n = 0 if reset else int(db.get_settings().get("_pixai_crashes") or 0) + 1
    if not reset or db.get_settings().get("_pixai_crashes"):
        db.update_settings({"_pixai_crashes": n})
    if reset:
        return
    tail = ""
    for fn in ("pixai_crash.log", "pixai_worker.log"):
        try:
            tail += (Path(db.DATA_DIR) / fn).read_text(encoding="utf-8", errors="replace")[-1200:]
        except Exception:  # noqa
            pass
    errlog.record_bg("PixAI 子程式", RuntimeError(f"{msg}（第 {n} 次）{chr(10) + tail if tail else ''}"))
    STATUS.update(last_error=msg, crashes=n)
    if n >= 2:
        db.update_settings({"pixai_enabled": False})
        STATUS["disabled"] = "連續兩次崩潰，已自動關掉 PixAI"


def _save(mid: int, pred: dict | None, err: str | None, s: dict | None = None):
    c = db.conn()
    with db.write_lock:
        fp = (c.execute("SELECT fp FROM media WHERE id=?", (mid,)).fetchone() or [None])[0]
        c.execute("""INSERT OR REPLACE INTO pixai_pred(media_id, fp, chars, artists, copyrights, error, at) VALUES(?,?,?,?,?,?,?)""",
                  (mid, fp, json.dumps((pred or {}).get("character") or {}, ensure_ascii=False),
                   json.dumps((pred or {}).get("artist") or {}, ensure_ascii=False),
                   json.dumps((pred or {}).get("copyright") or {}, ensure_ascii=False), err, time.time()))
        if pred and _wanted(s or db.get_settings()):
            apply_candidates(mid, pred, s or db.get_settings())


def _wanted(s: dict) -> bool:
    """PixAI 的結果要不要放進標籤（設定 → 自動標記與搜源 → PixAI「找到時怎麼辦」；只記下＝只存分數）。"""
    from . import steps
    return steps.action("pixai", s) != "record"


def apply_candidates(mid: int, pred: dict, s: dict) -> int:
    """PixAI 的結果 → 待確認（tier 4、source pixai）。在 write_lock 內呼叫。搜源已經找到的圖不推薦（跟 WD 一樣）。"""
    from . import candidates
    c = db.conn()
    if s.get("ai_hide_when_sourced", True) and c.execute(
            "SELECT 1 FROM media_source WHERE media_id=? AND status='found'", (mid,)).fetchone():
        return 0
    emb_b = (c.execute("SELECT embedding FROM media WHERE id=?", (mid,)).fetchone() or [None])[0]
    emb = np.frombuffer(emb_b, dtype=np.float16).astype(np.float32) if emb_b else None
    thr = {"character": s.get("pixai_char_threshold", 0.7), "artist": s.get("pixai_artist_threshold", 0.8),
           "copyright": s.get("pixai_copyright_threshold", 0.8)}
    n = 0
    have = {r[0] for r in c.execute("""SELECT DISTINCT t.category FROM media_tags mt JOIN tags t ON t.id=mt.tag_id
        WHERE mt.media_id=? AND mt.state=1 AND t.category IN ('character','copyright','artist')""", (mid,))}
    for cat in ("character", "copyright", "artist"):
        if cat == "artist" and not s.get("pixai_artists", True):
            continue
        # 作品：已經有確認的角色或作品就不推（角色本來就帶出作品；上線實測 1,246 個作品候選裡 706 個是已確認角色的作品，只是多按）
        if cat == "copyright" and have & {"character", "copyright"}:
            continue
        for name, p in (pred.get(cat) or {}).items():
            if p < thr[cat]:
                continue
            # 名字都是 Danbooru 的標籤（不是從簽名猜的帳號），不在圖庫裡的畫師也可以建立；已確認／否決的 candidates.add 不會動
            what = {"character": "角色", "artist": "畫風像", "copyright": "作品"}[cat]
            if candidates.add(mid, name, cat, candidates.T_AI, SRC, round(p, 4), f"PixAI {what} {round(p * 100)}%", emb=emb):
                n += 1
    return n


def apply_all(s: dict | None = None) -> int:
    """開了「放進待確認」或改了門檻：用存著的結果重新產生候選（不用重跑模型）。"""
    s = s or db.get_settings()
    c = db.conn()
    n = 0
    with db.write_lock:
        c.execute("DELETE FROM media_tags WHERE source=? AND state=0", (SRC,))
        if _wanted(s):
            for mid, ch, ar, co in c.execute("""SELECT p.media_id, p.chars, p.artists, p.copyrights FROM pixai_pred p
                    JOIN media m ON m.id=p.media_id WHERE m.status='done' AND p.error IS NULL""").fetchall():
                n += apply_candidates(mid, {"character": json.loads(ch or "{}"), "artist": json.loads(ar or "{}"),
                                            "copyright": json.loads(co or "{}")}, s)
        c.commit()
    return n


# ---------------- 評估（唯讀） ----------------
def evaluate(thresholds=(0.3, 0.5, 0.6, 0.7, 0.8, 0.9)) -> dict:
    """拿你已經確認／否決的身分當答案，看 PixAI 在各門檻的表現。
    - 準確：PixAI 說有（≥ 門檻）的角色裡，你確認過的比例；你否決過的算錯；其他（你沒表示）另外算「不知道」
      （多人圖你可能還沒標完，所以不知道的不直接算錯）
    - 找到：你確認過、PixAI 認得的角色裡，它有說出來的比例
    - 依答案的來源分開算：Danbooru 來的答案 PixAI 可能訓練時看過（偏高）；你手動、ascii2d、你的圖庫確認的才是真的考試
    - 新發現：還沒有已確認角色的圖裡，有幾張 PixAI 會給候選
    """
    c = db.conn()
    vocab = set()
    if _inst:
        vocab = set(_inst.names)
    else:
        p = tagger.MODELS_DIR / REPO.replace("/", "__") / "selected_tags.csv"
        if p.exists():
            vocab = {r["name"] for r in csv.DictReader(open(p, encoding="utf-8"))}
    truth: dict[int, dict] = {}
    for mid, name, cat, state, src in c.execute("""SELECT mt.media_id, t.name, t.category, mt.state, mt.source FROM media_tags mt
            JOIN tags t ON t.id=mt.tag_id JOIN media m ON m.id=mt.media_id
            WHERE m.status='done' AND mt.state IN (1,-1) AND t.category IN ('character','artist','copyright') AND mt.source != ?""", (SRC,)):
        d = truth.setdefault(mid, {})
        d.setdefault((cat, state), {})[name] = src
    preds = {mid: {"character": json.loads(a or "{}"), "artist": json.loads(b or "{}"), "copyright": json.loads(cc or "{}")}
             for mid, a, b, cc in c.execute("SELECT media_id, chars, artists, copyrights FROM pixai_pred WHERE error IS NULL")}
    out = {"images": len(preds), "vocab": len(vocab)}
    # 答案的來源分三組：Danbooru 類（模型可能訓練時看過）、WD（WD 認得的角色，比較容易）、你自己（手動、ascii2d、你的圖庫…，最公平）
    group = lambda src: "danbooru" if src in ("danbooru", "pixiv", "rule34", "e621") else "wd" if src in ("model", "ccip") else "yours"
    for cat in ("character", "artist", "copyright"):
        res = {}
        for thr in thresholds:
            agg = {g: {"pred": 0, "right": 0, "wrong": 0, "truth": 0, "in_vocab": 0, "found": 0} for g in ("danbooru", "wd", "yours")}
            new_imgs = 0
            for mid, pr in preds.items():
                t = truth.get(mid, {})
                yes = t.get((cat, 1), {})
                no = t.get((cat, -1), {})
                said = {n for n, p in pr[cat].items() if p >= thr}
                if not yes and not no:
                    if said and cat == "character":
                        new_imgs += 1
                    continue
                srcs = {group(s) for s in (yes or no).values()}
                g = "danbooru" if "danbooru" in srcs else "wd" if "wd" in srcs and "yours" not in srcs else "yours"
                a = agg[g]
                a["pred"] += len(said)
                a["right"] += len(said & set(yes))
                a["wrong"] += len(said & set(no))
                a["truth"] += len(yes)
                iv = {n for n in yes if n in vocab} if vocab else set(yes)
                a["in_vocab"] += len(iv)
                a["found"] += len(said & iv)
            for g, a in agg.items():
                known = a["right"] + a["wrong"]
                a["precision"] = round(a["right"] / a["pred"], 3) if a["pred"] else None    # 沒表示的算錯（下限）
                a["precision_known"] = round(a["right"] / known, 3) if known else None      # 只看你表示過的
                a["recall"] = round(a["found"] / a["in_vocab"], 3) if a["in_vocab"] else None
            res[str(thr)] = {**agg, "new_images": new_imgs}
        out[cat] = res
    # 認得多少你的角色：你確認過的角色有幾個在 PixAI／WD 的字典裡
    chars = {r[0] for r in c.execute("""SELECT DISTINCT t.name FROM media_tags mt JOIN tags t ON t.id=mt.tag_id
        WHERE mt.state=1 AND t.category='character'""")}
    wd = set()
    try:
        tg = tagger.loaded()
        wd = set(tg.names) if tg else set()
    except Exception:  # noqa
        pass
    out["your_characters"] = {"total": len(chars), "pixai_knows": len(chars & vocab) if vocab else None,
                              "wd_knows": len(chars & wd) if wd else None}
    return out
