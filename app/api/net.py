"""線上：搜源、角色資料卡。"""
from __future__ import annotations


from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .. import db, online
from .. import danbooru
from ..query import where as _where

router = APIRouter()


class OnlineReq(BaseModel):
    ids: list[int] | None = None
    view: str | None = None        # 'unrecognized'：整個未識別頁
    miss: str | None = None
    names: list[str] | None = None
    all_missing: bool = False
    force: bool = False
    nsfw: str = "show"


@router.post("/api/online/research-maybe")
def research_maybe(dry_run: bool = False):
    """補搜：預判「可能」但因為設定只搜「很可能」而沒上傳 IQDB 的圖（背景慢慢跑，每張 2–3 秒）。"""
    if not db.get_settings()["online_enabled"]:
        raise HTTPException(400, "線上功能已在設定中關閉")
    ids = online.skipped_maybe_ids()
    if dry_run:
        return {"n": len(ids)}
    return {"n": len(ids), "queued": online.get_jobs().queue_sources(ids, manual=True)}


@router.get("/api/online/research-status")
def research_status():
    return online.research_status()


@router.post("/api/online/source")
def online_source(req: OnlineReq):
    if not db.get_settings()["online_enabled"]:
        raise HTTPException(400, "線上功能已在設定中關閉")
    c = db.conn()
    ids = req.ids or []
    if req.view:
        join, where, params = _where(req.view, "show", "", None, req.miss or "all")  # 搜源不管 NSFW 顯示設定
        ids = [r[0] for r in c.execute(f"SELECT m.id FROM media m {join} WHERE {where} AND m.kind='image'", params)]
    if not req.force:
        done = set()
        for i in range(0, len(ids), 800):
            ch = ids[i:i + 800]
            done |= {r[0] for r in c.execute(
                f"SELECT media_id FROM media_source WHERE status!='error' AND media_id IN ({','.join('?' * len(ch))})", ch)}
        ids = [i for i in ids if i not in done]
    n = online.get_jobs().queue_sources(ids, manual=True) if ids else 0
    return {"queued": n}


@router.post("/api/online/taginfo")
def online_taginfo(req: OnlineReq):
    if not db.get_settings()["online_enabled"]:
        raise HTTPException(400, "線上功能已在設定中關閉")
    names = req.names or []
    if req.all_missing:
        names = [r[0] for r in db.conn().execute("""SELECT t.name FROM tags t LEFT JOIN tag_info ti ON ti.tag_id=t.id
            WHERE t.category IN ('character','artist','copyright') AND (ti.tag_id IS NULL OR (ti.status='error'))
              AND EXISTS (SELECT 1 FROM media_tags x WHERE x.tag_id=t.id AND x.state>=0)""")]
    online.get_jobs().queue_tag_info(names)
    return {"queued": len(names)}


@router.post("/api/online/test-danbooru")
def online_test_danbooru():
    return danbooru.test_danbooru()


@router.post("/api/online/stop")
def online_stop():
    online.get_jobs().stop_flag.set()
    return {"ok": True}


class ArtistScanReq(BaseModel):
    max_artists: int = 40
    max_posts: int = 300
    max_mb: float = 100
    dry_run: bool = False
    deep: bool = False
    emb_min: float = 0.55


@router.post("/api/online/artistscan")
def artistscan_start(req: ArtistScanReq):
    """從畫師反推出處（見 artistscan.py）。dry_run：只統計、不寫入，用來測效果。"""
    if not db.get_settings()["online_enabled"]:
        raise HTTPException(400, "線上功能已在設定中關閉")
    arg = {"max_artists": max(1, min(req.max_artists, 2000)), "max_posts": max(10, min(req.max_posts, 5000)),
           "max_mb": max(1.0, min(req.max_mb, 2000.0)), "dry_run": req.dry_run,
           "deep": req.deep, "emb_min": max(0.4, min(req.emb_min, 0.99))}
    online.get_jobs().submit("artistscan", arg)
    return {"queued": True, **arg}


@router.post("/api/online/e621/check/{mid}")
def e621_check_one(mid: int):
    from .. import e621
    try:
        return e621.check_one(mid)
    except e621.AuthError as e:
        raise HTTPException(400, str(e))


@router.post("/api/online/e621/iqdb/{mid}")
def e621_iqdb_one(mid: int):
    """e621 以圖搜圖（會上傳縮小成 1000px 的這一張）。"""
    from .. import e621
    s = db.get_settings()
    if not s["online_enabled"] or not s["online_iqdb"]:
        raise HTTPException(400, "設定裡關閉了上傳圖片搜尋（線上資料 → 允許上傳到 IQDB 反查）")
    try:
        return e621.iqdb(mid)
    except e621.AuthError as e:
        raise HTTPException(400, str(e))
    except RuntimeError as e:
        raise HTTPException(429 if "限速" in str(e) else 502, str(e))


@router.post("/api/rsearch/{engine}/{mid}")
def rsearch_one(engine: str, mid: int):
    """以圖搜圖（手動按才做）：程式上傳這一張，回傳結果頁網址（Fluffle、TinEye 另外附上結果清單）。"""
    from .. import rsearch
    s = db.get_settings()
    if not s["online_enabled"]:
        raise HTTPException(400, "線上功能已在設定中關閉")
    r = db.conn().execute("SELECT path FROM media WHERE id=?", (mid,)).fetchone()
    if not r:
        raise HTTPException(404)
    try:
        return rsearch.search(engine, r[0])
    except rsearch.SearchError as e:
        raise HTTPException(502, str(e))
    except Exception as e:  # noqa
        raise HTTPException(502, f"{type(e).__name__}: {e}"[:200])


@router.post("/api/online/test-e621")
def online_test_e621():
    from .. import e621
    return e621.test()


@router.post("/api/online/r34/check/{mid}")
def r34_check_one(mid: int):
    from .. import r34
    if not r34.configured():
        raise HTTPException(400, "請先在設定 → 網站帳號填 Rule34 的 user ID 和 API key")
    try:
        return r34.check_one(mid)
    except r34.AuthError as e:
        raise HTTPException(400, str(e))


@router.post("/api/online/test-r34")
def online_test_r34():
    from .. import r34
    return r34.test()


@router.get("/api/online/artistscan")
def artistscan_state():
    return online.get_jobs().state.get("artistscan") or {}


# ---------- 準確率抽查 ----------
@router.get("/api/audit/sample")
def audit_sample(n: int = 50, kind: str = "artist", method: str | None = None, nsfw: str = "show"):
    """隨機抽 n 張已確認身分（創作者／角色）的圖，讓你逐張判斷對不對；附上是怎麼找到的（搜源方法）。"""
    import json as _json
    from ..query import NSFW as NSFW_SQL   # 隱藏 NSFW 模式：只抽看得到的圖，不然一半是空白格
    c = db.conn()
    cat = "artist" if kind == "artist" else "character"
    q = f"""SELECT mt.media_id, t.name, mt.source, s.method, s.similarity, s.data FROM media_tags mt
            JOIN tags t ON t.id=mt.tag_id AND t.category='{cat}'
            JOIN media m ON m.id=mt.media_id AND m.status='done' AND COALESCE(m.excluded,0)=0
            LEFT JOIN media_source s ON s.media_id=mt.media_id AND s.status='found'
            WHERE mt.state=1 {"AND s.method=?" if method else ""} {f"AND {NSFW_SQL}=0" if nsfw == "hide" else ""}
            ORDER BY RANDOM() LIMIT ?"""
    rows = c.execute(q, ([method] if method else []) + [max(1, min(n, 200))]).fetchall()
    out = []
    for r in rows:
        d = _json.loads(r["data"] or "{}") if r["data"] else {}
        out.append({"id": r["media_id"], "tag": r["name"], "tag_source": r["source"], "method": r["method"] or ("signature" if r["source"] == "signature" else "manual"),
                    "similarity": r["similarity"], "post_url": (d.get("danbooru") or {}).get("post_url")})
    return out


class AuditResult(BaseModel):
    results: list[dict]   # [{id, tag, method, ok}]


@router.post("/api/audit/result")
def audit_result(body: AuditResult):
    """記錄抽查結果（依找到的方法算準確率），並把判斷「不對」的標籤否決掉。"""
    import json as _json
    import time as _t
    from .common import _apply
    by = {}
    for r in body.results:
        m = r.get("method") or "manual"
        b = by.setdefault(m, {"ok": 0, "bad": 0})
        b["ok" if r.get("ok") else "bad"] += 1
    bad = [r for r in body.results if not r.get("ok")]
    with db.write_lock:
        rows = []
        for r in bad:
            rows += _apply("reject", r["tag"], [int(r["id"])], None, None)[0]
        if rows:
            db.push_action(f"抽查：否決 {len(bad)} 個錯的標籤", {"tag_rows": rows})
        db.conn().execute("CREATE TABLE IF NOT EXISTS audit_log(ts REAL, data TEXT)")
        db.conn().execute("INSERT INTO audit_log(ts, data) VALUES(?, ?)", (_t.time(), _json.dumps(by)))
    return {"by_method": by, "rejected": len(bad)}


class RepairReq(BaseModel):
    dry_run: bool = True


@router.post("/api/online/repair-dup-reuse")
def repair_dup_reuse(body: RepairReq):
    """重新檢查「沿用重複圖的結果」，沿用錯的撤掉並重新搜源（dry_run 只回報）。"""
    return online.repair_duplicate_reuse(body.dry_run)


@router.post("/api/online/backfill-site-tags")
def backfill_site_tags():
    """以前找到出處的圖：補上 Danbooru 的一般標籤；Pixiv 標籤重新對應角色／作品（候選）。"""
    import json as _json
    from .. import sitetags
    gen = sitetags.backfill_danbooru_general()
    c = db.conn()
    rows = c.execute("SELECT * FROM media_source WHERE status='found' AND data LIKE '%\"pixiv\"%'").fetchall()
    remapped = 0
    for r in rows:
        res = {k: r[k] for k in ("status", "method", "similarity", "post_url", "source_url", "pixiv_id")}
        res["data"] = _json.loads(r["data"] or "{}")
        if not res["data"].get("pixiv"):
            continue
        before = {t["name"] for t in res["data"].get("suggested", [])}
        online.save_source(r["media_id"], res)
        new = [t for t in res["data"]["suggested"] if t["name"] not in before]
        if new and db.get_settings()["online_auto_apply"]:
            online.auto_apply(r["media_id"], res)
            remapped += len(new)
    return {"general": gen, "pixiv_remapped": remapped, "pixiv_checked": len(rows)}


@router.post("/api/online/artist-links")
def artist_links(body: dict):
    """Danbooru 還沒登記的創作者：到其他網站找其他帳號加進連結。{tag?, dry_run?, limit?}"""
    from .. import artistlinks
    if body.get("tag"):
        return {"results": [artistlinks.enrich(body["tag"], dry_run=bool(body.get("dry_run")), force=bool(body.get("force")))]}
    return {"results": artistlinks.backfill(limit=int(body.get("limit") or 200), dry_run=bool(body.get("dry_run")))}


@router.post("/api/online/recheck-artists")
def recheck_artists():
    """用網站帳號建立的創作者：現在就問 Danbooru 登記了沒，登記了就合併。"""
    from .. import siteartists
    return {"merged": siteartists.recheck(force=True)}
