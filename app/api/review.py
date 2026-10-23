"""待確認：候選佇列、是／不是、高信心 AI 候選一次確認、撤銷。"""
from __future__ import annotations

import json
import threading

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .. import candidates, charknn, db, names, pixiv
from ..query import IDENT_IDS, NSFW
from ..matcher import matcher
from .common import _apply, _schedule_rematch

router = APIRouter()

# ---------- 待確認佇列 ----------
# 待確認的名字順序：角色 → 創作者 → 作品（使用者的佇列一半是 PixAI 的作品候選，「原創」「Twitter」這類排最前面很浪費時間；2026-10-05）
CAT_RANK = "CASE t.category WHEN 'character' THEN 0 WHEN 'artist' THEN 1 ELSE 2 END"

@router.get("/api/review")
def review(nsfw: str = "hide", tier: str = "all", category: str = "all", mtype: str = "",
           offset: int = 0, limit: int = 120, tag: str = "", src: str = "", min: float = 0, ctx: str = "", seg: str = "",
           nobatch: float = 0):
    """所有身分標籤的候選（一張圖＋一個候選名稱為一筆），依優先序排列：搜源 → 同組 → AI。"""
    c = db.conn()
    w = ["mt.state=0", f"mt.tag_id IN {IDENT_IDS}", "m.status='done'", "COALESCE(m.excluded,0)=0", "t.category IN ('character','artist','copyright')"]
    p: list = []
    if nsfw == "hide":
        w.append(f"{NSFW}=0")
    if tier in candidates.TIER_FILTER:
        w.append(candidates.TIER_FILTER[tier])
    if category in candidates.IDENTITY:
        w.append("t.category=?")
        p.append(category)
    if mtype:
        from ..query import mtype_cond
        sq, sp = mtype_cond(mtype)
        w.append(sq); p += sp
    if tag:
        w.append("t.name=?")
        p.append(tag)
    if src:      # 「找到出處」分頁裡再分：Danbooru／e621／Rule34／簽名…
        w.append("mt.source=?")
        p.append(src)
    if min:      # 高信心 AI 候選：先檢查再確認
        w.append("mt.source='model' AND mt.score>=?")
        p.append(min)
    if ctx in CTX:
        w.append(CTX[ctx])
    if seg in SEGMENTS:
        w.append(SEGMENTS[seg])
    nb = ""
    if nobatch:   # 專注模式：「整批看」那些（AI 高分、不像梗圖）不再一張一張出現
        nb = f" AND NOT (mt.source IN ({','.join(repr(x) for x in BATCH_SOURCES)}) AND mt.score>={float(nobatch)} AND NOT {MEMEISH})"
        w.append(nb[5:])
    where = " AND ".join(w)
    base = f"FROM media_tags mt JOIN tags t ON t.id=mt.tag_id JOIN media m ON m.id=mt.media_id WHERE {where}"
    total = c.execute(f"SELECT COUNT(*) {base}", p).fetchone()[0]
    counts = {k: c.execute(f"""SELECT COUNT(*) FROM media_tags mt JOIN tags t ON t.id=mt.tag_id JOIN media m ON m.id=mt.media_id
                WHERE mt.state=0 AND mt.tag_id IN {IDENT_IDS} AND m.status='done' AND COALESCE(m.excluded,0)=0 AND t.category IN ('character','artist','copyright')
                {f"AND {NSFW}=0" if nsfw == "hide" else ""} AND {cond}""").fetchone()[0]
              for k, cond in candidates.TIER_FILTER.items()}
    rows = c.execute(f"""SELECT m.id, m.kind, m.duration, m.media_type, {NSFW} AS nsfw, t.name AS tag, t.category,
        mt.tier, mt.source, mt.score, mt.reason {base}
        ORDER BY COALESCE(mt.tier, 9), mt.score DESC, m.id LIMIT ? OFFSET ?""", [*p, limit, offset]).fetchall()
    nf = f"AND {NSFW}=0" if nsfw == "hide" else ""
    # 依角色逐一確認：候選最多的角色清單 + 選定角色的已確認參考圖
    by_tag = [dict(r) for r in c.execute(f"""SELECT t.name, t.category, COUNT(*) n FROM media_tags mt JOIN tags t ON t.id=mt.tag_id
        JOIN media m ON m.id=mt.media_id WHERE mt.state=0 AND mt.tag_id IN {IDENT_IDS} AND m.status='done' AND COALESCE(m.excluded,0)=0
        AND t.category IN ('character','artist','copyright') {nf}{nb} GROUP BY t.id ORDER BY {CAT_RANK}, n DESC LIMIT 60""")]
    lang = db.get_settings()["display_lang"]
    for d in by_tag:
        d["display"] = _disp(d["name"], lang)
    refs = []
    if tag:
        refs = [r[0] for r in c.execute(f"""SELECT m.id FROM media_tags mt JOIN tags t ON t.id=mt.tag_id JOIN media m ON m.id=mt.media_id
            WHERE t.name=? AND mt.state=1 AND m.status='done' {nf} ORDER BY COALESCE(m.mtime, m.added_at) DESC LIMIT 14""", (tag,))]
    src_counts = {r[0]: r[1] for r in c.execute(f"""SELECT mt.source, COUNT(*) FROM media_tags mt JOIN media m ON m.id=mt.media_id
        WHERE mt.state=0 AND mt.tag_id IN {IDENT_IDS} AND mt.tier<=2 AND m.status='done' AND COALESCE(m.excluded,0)=0
        {f"AND {NSFW}=0" if nsfw == "hide" else ""} GROUP BY mt.source""")}
    items = [dict(r) for r in rows]
    kr = charknn.refs_for([(it["id"], it["tag"]) for it in items if it["source"] == "knn"])
    for it in items:
        if (it["id"], it["tag"]) in kr:
            it["refs"] = kr[(it["id"], it["tag"])]      # 跟你確認過的哪幾張很像
    disp = {n: _disp(n, lang) for n in {it["tag"] for it in items}}
    for it in items:
        it["display"] = disp[it["tag"]]
    return {"total": total, "counts": counts, "src_counts": src_counts, "items": items, "by_tag": by_tag, "refs": refs,
            "tag_display": _disp(tag, lang) if tag else None, "knn_version": charknn._state["version"]}



def _disp(name: str, lang: str) -> str:
    r = db.conn().execute("""SELECT t.category, ti.other_names, ti.display_name FROM tags t LEFT JOIN tag_info ti ON ti.tag_id=t.id
                             WHERE t.name=?""", (name,)).fetchone()
    aliases = json.loads(r["other_names"] or "[]") if r and r["other_names"] else []
    if r and r["category"] == "copyright":     # 作品：內建譯名表／維基譯名（跟書架一致）
        from .. import wikiname
        return names.series_display(name, aliases, r["display_name"], lang, wikiname.cached([name]).get(name))
    return names.display(name, aliases, r["display_name"] if r else None, lang)



class Decide(BaseModel):
    pairs: list[list]      # [[media_id, tag_name], ...]
    accept: bool



@router.post("/api/review/decide")
def review_decide(body: Decide):
    by_tag: dict[str, list[int]] = {}
    for mid, tag in body.pairs:
        by_tag.setdefault(tag, []).append(int(mid))
    all_rows, prop, set_added = [], 0, 0
    with db.write_lock:
        for tag, ids in by_tag.items():
            rows, p1, s1 = _apply("confirm" if body.accept else "reject", tag, ids)
            all_rows += rows
            prop += p1
            set_added += s1
        db.push_action(f"{'確認' if body.accept else '否決'}候選（{len(body.pairs)} 筆）", {"tag_rows": all_rows},
                       touched=[int(m) for m, _ in body.pairs])
        db.conn().commit()
    _schedule_rematch()
    return {"ok": True, "propagated": prop, "set_added": set_added,
            "followup": _crowd_followup(body.pairs) if body.accept else []}



def _crowd_followup(pairs) -> list[dict]:
    """確認角色後：多人圖還有別人沒標 → 告訴畫面（附上「可能是誰」），不要讓這張就這樣消失。"""
    from .. import people
    c = db.conn()
    out, seen = [], set()
    for mid, tag in pairs:
        mid = int(mid)
        if mid in seen or len(out) >= 12:
            continue
        seen.add(mid)
        r = c.execute("SELECT category FROM tags WHERE name=?", (tag,)).fetchone()
        if not r or r[0] != "character":
            continue
        inf = people.info(mid)
        if not inf["short"]:
            continue
        have = [x[0] for x in c.execute("""SELECT t.name FROM media_tags x JOIN tags t ON t.id=x.tag_id
            WHERE x.media_id=? AND x.state=1 AND t.category='character'""", (mid,))]
        pend = [x[0] for x in c.execute("""SELECT t.name FROM media_tags x JOIN tags t ON t.id=x.tag_id
            WHERE x.media_id=? AND x.state=0 AND t.category='character'""", (mid,))]
        try:
            sug = [x for x in charknn.suggest_other(mid) if x["name"] not in pend]
        except Exception:  # noqa
            sug = []
        lang = db.get_settings()["display_lang"]
        out.append({"id": mid, **inf, "have": [{"name": n, "display": _disp(n, lang)} for n in have], "pending": pend,
                    "suggest": [{**x, "display": _disp(x["name"], lang)} for x in sug]})
    return out



# ---------- 高信心 AI 候選：用你自己的確認紀錄估計準確率 ----------
_CONF_LEVELS = (0.97, 0.95, 0.9)

# WD Tagger 是用 Danbooru（動漫）訓練的；furry 圖上的準確率不一定一樣 → 分開算，各自要有足夠的紀錄
FURRY_TAGS = ("furry", "anthro", "furry_female", "furry_male", "furry_with_non-furry", "kemono")

_FURRY = f"""EXISTS (SELECT 1 FROM media_tags g WHERE g.media_id=mt.media_id AND g.state>=0
            AND g.tag_id IN (SELECT id FROM tags WHERE name IN ({",".join(f"'{t}'" for t in FURRY_TAGS)})))"""

SEGMENTS = {"anime": f"NOT {_FURRY}", "furry": _FURRY}

# 可能是梗圖／日常截圖（剛好有角色，但你不一定想標）：用你的紀錄挑出來的描述 tag——
# 這些 tag 出現在「還沒確認的高信心候選」45%，在「你確認過的」只有 10%
MEMEISH_TAGS = ("fake_screenshot", "chinese_text", "parody", "chat_log", "meme", "user_interface", "card_(medium)",
                "emoji", "subtitled", "text_focus", "trading_card", "screenshot")

MEMEISH = f"""(m.media_type IN ('photo','cosplay','figure') OR EXISTS (SELECT 1 FROM media_tags g WHERE g.media_id=mt.media_id
            AND g.state>=0 AND COALESCE(g.score,1)>=0.35
            AND g.tag_id IN (SELECT id FROM tags WHERE name IN ({",".join(f"'{t}'" for t in MEMEISH_TAGS)}))))"""

CTX = {"clean": f"NOT {MEMEISH}", "meme": MEMEISH}



def _model_pending(min_score: float, nsfw: str, segment: str = "anime", ctx: str = ""):
    nf = f" AND {NSFW}=0" if nsfw == "hide" else ""
    cx = f" AND {CTX[ctx]}" if ctx in CTX else ""
    return db.conn().execute(f"""SELECT mt.media_id, t.name FROM tags t CROSS JOIN media_tags mt ON mt.tag_id=t.id
        CROSS JOIN media m ON m.id=mt.media_id
        WHERE t.category='character' AND mt.state=0 AND mt.source='model' AND mt.score>=?
          AND m.status='done' AND COALESCE(m.excluded,0)=0{nf} AND {SEGMENTS[segment]}{cx}""", (min_score,)).fetchall()



@router.get("/api/review/confidence")
def review_confidence(nsfw: str = "hide"):
    """WD Tagger 角色候選：你過去在各信心分數以上「確認 vs 否決」的比例，和現在還有幾筆沒看。
    動漫圖、furry 圖分開算；準確率夠高（過去至少 50 筆、≥ 98%）才建議一鍵確認。"""
    c = db.conn()
    out = {}
    for seg, cond in SEGMENTS.items():
        lv_out = []
        for lv in _CONF_LEVELS:
            ok, bad = c.execute(f"""SELECT COALESCE(SUM(mt.state=1),0), COALESCE(SUM(mt.state=-1),0) FROM tags t
                CROSS JOIN media_tags mt ON mt.tag_id=t.id
                WHERE t.category='character' AND mt.source='model' AND mt.state!=0 AND mt.score>=? AND {cond}""", (lv,)).fetchone()
            pending = len(_model_pending(lv, nsfw, seg))
            clean = len(_model_pending(lv, nsfw, seg, "clean"))
            prec = ok / (ok + bad) if ok + bad else None
            lv_out.append({"min": lv, "confirmed": ok, "rejected": bad, "precision": prec, "pending": pending, "segment": seg,
                           "clean": clean, "meme": pending - clean,
                           "recommend": bool(prec is not None and ok + bad >= 50 and prec >= 0.98 and pending)})
        out[seg] = lv_out
    return {"levels": out["anime"], "furry": out["furry"]}



class ConfirmConfident(BaseModel):
    min_score: float
    nsfw: str = "hide"
    segment: str = "anime"
    ctx: str = ""            # clean：只確認「像插畫」的那些（你已經在畫面上看過、梗圖按 L 略過了）
    reviewed: bool = False   # 你已經在畫面上看過這一批 → 不用再看過去的準確率



@router.post("/api/review/confirm-confident")
def review_confirm_confident(body: ConfirmConfident):
    """一鍵確認信心分數 ≥ min_score 的 WD Tagger 角色候選（可以撤銷）。只做紀錄夠、準確率夠的那一類圖。"""
    ms = max(0.9, min(body.min_score, 1.0))
    seg = body.segment if body.segment in SEGMENTS else "anime"
    lv = next((x for x in review_confidence(body.nsfw)["levels" if seg == "anime" else "furry"] if x["min"] <= ms), None)
    if not body.reviewed and (not lv or not lv["recommend"]):
        raise HTTPException(400, "這類圖的確認紀錄還不夠，先手動確認一些")
    pairs = [[r[0], r[1]] for r in _model_pending(ms, body.nsfw, seg, body.ctx)]
    if not pairs:
        return {"ok": True, "confirmed": 0}
    review_decide(Decide(pairs=pairs, accept=True))
    return {"ok": True, "confirmed": len(pairs)}



# ---------- 整批看：AI 高分候選，同一個名字一面看完、只挑錯的 ----------
# 使用者的紀錄（2026-10-05）：PixAI 角色候選 0.9 以上 354 是／4 不是（99%），0.7–0.9 45 是／8 不是（85%）。
# 所以 ≥ 門檻的同名候選排成一面：預設都是「是」，錯的點掉（不是／略過），一次送出；門檻以下的照舊逐張（專注模式）。
# 像梗圖、截圖、照片的（MEMEISH）不放進來：那些常常是「角色沒認錯、但你不想標」，留給逐張看。
BATCH_SOURCES = ("pixai", "model")


def _batch_where(min_score: float, nsfw: str) -> tuple[str, list]:
    w = [f"mt.state=0", f"mt.tag_id IN {IDENT_IDS}", "m.status='done'", "COALESCE(m.excluded,0)=0",
         f"mt.source IN ({','.join(repr(x) for x in BATCH_SOURCES)})", "mt.score>=?", f"NOT {MEMEISH}",
         "t.category IN ('character','artist','copyright')"]
    if nsfw == "hide":
        w.append(f"{NSFW}=0")
    return " AND ".join(w), [min_score]


@router.get("/api/review/batch")
def batch_groups(nsfw: str = "hide", min: float = 0.9):
    """整批看的名單：每個名字幾張（多的先）、已確認的參考圖；還有留給逐張看的（門檻以下、像梗圖的）有多少。"""
    c = db.conn()
    w, p = _batch_where(max(0.5, min), nsfw)
    lang = db.get_settings()["display_lang"]
    nf = f"AND {NSFW}=0" if nsfw == "hide" else ""
    groups = []
    for r in c.execute(f"""SELECT t.name, t.category, COUNT(*) n, MIN(mt.score) lo FROM media_tags mt JOIN tags t ON t.id=mt.tag_id
            JOIN media m ON m.id=mt.media_id WHERE {w} GROUP BY t.id ORDER BY {CAT_RANK}, n DESC, t.name LIMIT 400""", p).fetchall():
        refs = [x[0] for x in c.execute(f"""SELECT m.id FROM media_tags mt JOIN tags t ON t.id=mt.tag_id JOIN media m ON m.id=mt.media_id
            WHERE t.name=? AND mt.state=1 AND m.status='done' {nf} ORDER BY COALESCE(m.mtime, m.added_at) DESC LIMIT 6""", (r["name"],))]
        groups.append({"name": r["name"], "category": r["category"], "n": r["n"], "display": _disp(r["name"], lang), "refs": refs})
    total = sum(g["n"] for g in groups)
    wl, pl = _batch_where(0, nsfw)
    below = c.execute(f"""SELECT COUNT(*) FROM media_tags mt JOIN tags t ON t.id=mt.tag_id JOIN media m ON m.id=mt.media_id
        WHERE {wl.replace(f'NOT {MEMEISH}', '1')} AND mt.score<?""", [0, max(0.5, min)]).fetchone()[0]
    meme = c.execute(f"""SELECT COUNT(*) FROM media_tags mt JOIN tags t ON t.id=mt.tag_id JOIN media m ON m.id=mt.media_id
        WHERE {wl.replace(f'NOT {MEMEISH}', MEMEISH)}""", [max(0.5, min)]).fetchone()[0]
    return {"groups": groups, "total": total, "below": below, "memeish": meme, "min": max(0.5, min)}


@router.get("/api/review/batch/items")
def batch_items(tag: str, nsfw: str = "hide", min: float = 0.9):
    c = db.conn()
    w, p = _batch_where(max(0.5, min), nsfw)
    rows = c.execute(f"""SELECT m.id, m.kind, m.width, m.height, m.media_type, {NSFW} AS nsfw, mt.score, mt.source, mt.reason
        FROM media_tags mt JOIN tags t ON t.id=mt.tag_id JOIN media m ON m.id=mt.media_id WHERE {w} AND t.name=?
        ORDER BY mt.score ASC, m.id LIMIT 600""", [*p, tag]).fetchall()     # 分數低的在前面：最可能錯的先看到
    return {"tag": tag, "items": [dict(r) for r in rows]}


class BatchApply(BaseModel):
    tag: str
    yes: list[int] = []
    no: list[int] = []
    skip: list[int] = []


@router.post("/api/review/batch/apply")
def batch_apply(body: BatchApply):
    """一面送出：是的確認、不是的否決（AI 會記住）、略過的拿掉 AI 候選（不教 AI）。一筆撤銷。"""
    from .library import _skip
    yes = [int(i) for i in body.yes]
    no = [int(i) for i in body.no if int(i) not in set(yes)]
    skip = [int(i) for i in body.skip if int(i) not in set(yes) | set(no)]
    rows, prev = [], []
    with db.write_lock:
        if yes:
            rows += _apply("confirm", body.tag, yes)[0]
        if no:
            rows += _apply("reject", body.tag, no)[0]
        if skip:
            prev, r2 = _skip(db.conn(), skip, True)
            rows += r2
        payload = {"tag_rows": rows}
        if prev:
            payload["skip"] = prev
        db.push_action(f"整批：{body.tag} 是 {len(yes)}、不是 {len(no)}、略過 {len(skip)}", payload, touched=yes + no + skip)
        db.conn().commit()
    _schedule_rematch()
    return {"ok": True, "yes": len(yes), "no": len(no), "skip": len(skip),
            "followup": _crowd_followup([[m, body.tag] for m in yes]) if yes else []}


class PixivMap(BaseModel):
    pixiv_tag: str
    character: str



@router.post("/api/online/pixiv-map")
def pixiv_map(body: PixivMap):
    """把 Pixiv 標籤對應到角色（記成別名），之後所有帶此標籤的 Pixiv 作品都會自動對上。"""
    n = pixiv.map_pixiv_tag(body.pixiv_tag.strip(), body.character.strip().replace(" ", "_"))
    return {"ok": True, "applied": n}



class ConfirmAll(BaseModel):
    tag: str
    nsfw: str = "hide"
    dry_run: bool = False



@router.post("/api/tags/confirm-all")
def confirm_all(body: ConfirmAll):
    """角色頁「候選全部確認」／一般標籤「標為已檢查」。
    角色、創作者、作品：只確認像插畫的——梗圖、截圖、照片（MEMEISH）、你略過或排除的圖不動。
    你的習慣是「有角色的梗圖留著不確認」，以前這個按鈕會把它們一起確認掉。dry_run＝先數給你看。"""
    r = db.conn().execute("SELECT id, category FROM tags WHERE name=?", (body.tag,)).fetchone()
    if r is None:
        raise HTTPException(404)
    tid, ident = r[0], r[1] in candidates.IDENTITY
    c = db.conn()
    nf = f" AND {NSFW}=0" if body.nsfw == "hide" else ""
    base = f"""FROM media_tags mt JOIN media m ON m.id=mt.media_id
        WHERE mt.tag_id=? AND mt.state=0 AND m.status='done'{nf}"""
    if ident:
        ids = [x[0] for x in c.execute(f"""SELECT mt.media_id {base} AND COALESCE(m.excluded,0)=0 AND COALESCE(m.unrec_skip,0)=0
            AND NOT {MEMEISH}""", (tid,))]
        total = c.execute(f"SELECT COUNT(*) {base}", (tid,)).fetchone()[0]
    else:
        ids = [x[0] for x in c.execute(f"SELECT mt.media_id {base}", (tid,))]
        total = len(ids)
    left = total - len(ids)
    if body.dry_run or not ids:
        return {"ok": True, "n": len(ids) if body.dry_run else 0, "left": left}
    with db.write_lock:
        if ident:
            rows, _, _ = _apply("confirm", body.tag, ids)
        else:
            rows = db.snapshot_tag_rows(ids, tid)
            c.executemany("UPDATE media_tags SET state=1 WHERE media_id=? AND tag_id=?", [(i, tid) for i in ids])
        db.push_action(f"{'候選全部確認' if ident else '標為已檢查'} {body.tag}（{len(ids)} 張）", {"tag_rows": rows})
        c.commit()
    _schedule_rematch()
    if ident and r[1] == "character":
        from .. import people
        people.refresh_bases()
    return {"ok": True, "n": len(ids), "left": left}



_rb_timer: threading.Timer | None = None
_rb_lock = threading.Lock()


def _rebuild_later():
    """連按好幾次 Ctrl+Z：1 秒內的合成一次重建（以前每按一次就同時開一個整庫重建）。"""
    global _rb_timer
    with _rb_lock:
        if _rb_timer:
            _rb_timer.cancel()
        _rb_timer = threading.Timer(1.0, matcher.build)
        _rb_timer.daemon = True
        _rb_timer.start()


@router.post("/api/undo")
def undo():
    label = db.undo_last()
    if label:
        _rebuild_later()      # 還原的否決/停止標註要反映到比對
    return {"undone": label}
