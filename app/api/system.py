"""狀態、工作、設定、首頁、診斷、資料夾瀏覽、備份、匯出/匯入。"""
from __future__ import annotations

import json
import logging
import os
import re
import platform
import shutil
import string
import sys
import time
from pathlib import Path
from xml.sax.saxutils import escape

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel

from .. import candidates, db, media, names, online, pipeline, tagger
from .. import danbooru
from .. import httpclient
from .. import pixiv
from ..matcher import matcher
from ..query import IDENT_IDS, MISSING, NSFW

log = logging.getLogger("system")

router = APIRouter()
ROOT = Path(__file__).resolve().parent.parent.parent


# ---------- 工作 / 設定 ----------
@router.get("/api/status")
def status():
    j = pipeline.get_jobs()
    c = db.conn()
    b = db.list_backups()
    st = dict(j.state)
    hold = st.pop("missing_hold", None)
    if hold:
        st["missing_hold"] = {"n": len(hold["ids"]), "of": hold["of"], "at": hold["at"]}
    return {**st, "pending": c.execute("SELECT COUNT(*) FROM media WHERE status='pending'").fetchone()[0],
            "queued": j.q.qsize(), "matcher": matcher.stats, "watching": bool(j.observer),
            "online": {**online.get_jobs().state, "mode": danbooru.search_mode(), "queued": online.get_jobs().queued_count()}, "last_backup": b[0]["mtime"] if b else None,
            "backup_offsite": db.backup_offsite(), "drive_overdue": _drive_overdue()}


@router.post("/api/scan/confirm-missing")
def confirm_missing(body: dict | None = None):
    """掃描時一次消失太多張、先暫停標記的那些：你確認真的不見了 → 標成找不到檔案；按「先不要」→ 丟掉這次的暫停。"""
    j = pipeline.get_jobs()
    if (body or {}).get("dismiss"):
        j.state.pop("missing_hold", None)
        return {"marked": 0}
    return {"marked": pipeline.confirm_missing(j)}


@router.get("/api/jobs/overview")
def jobs_overview():
    """背景工作面板：正在做、排隊中、排程、最近完成。"""
    from .. import jobsview
    return jobsview.overview()


class CancelReq(BaseModel):
    queue: str
    index: int


@router.post("/api/jobs/cancel")
def jobs_cancel(body: CancelReq):
    from .. import jobsview
    return {"ok": jobsview.cancel(body.queue, body.index)}


@router.post("/api/jobs/interrupted-seen")
def jobs_interrupted_seen():
    from .. import tasks
    tasks.dismiss_interrupted()
    return {"ok": True}


class JobReq(BaseModel):
    kind: str
    ids: list[int] | None = None


@router.post("/api/jobs")
def start_job(req: JobReq):
    j = pipeline.get_jobs()
    if req.kind == "stop":
        j.stop_flag.set()
        j.pause(False)
    elif req.kind == "pause":
        j.pause(True)
    elif req.kind == "resume":
        j.pause(False)
    elif req.kind in ("scan", "rematch", "sweep", "fingerprint"):
        j.submit(req.kind)
    elif req.kind in ("retag", "clip"):
        j.submit(req.kind, req.ids)
    else:
        raise HTTPException(400)
    return {"ok": True}


def _drive_overdue():
    from .. import drivebackup
    return drivebackup.overdue()


SECRET_KEYS = ("danbooru_api_key", "pixiv_phpsessid", "lan_pin", "r34_api_key", "e621_api_key", "import_key")


@router.get("/api/settings")
def get_settings(request: Request = None):
    s = dict(db.get_settings())
    s.pop("import_key", None)           # 瀏覽器按鈕的金鑰只放在腳本裡，設定頁不需要
    host = request.client.host if request and request.client else "127.0.0.1"
    if host not in ("127.0.0.1", "::1", "testclient", "localhost"):
        for k in SECRET_KEYS:           # 手機／平板（區網）：帳號金鑰、Pixiv cookie、PIN 一律不給
            s.pop(k, None)
        s["roots"] = []
    s["_roots_ok"] = {r: os.path.isdir(r) for r in s["roots"]}
    s["_lan_bound"] = os.environ.get("LIB_HOST", "127.0.0.1") != "127.0.0.1"
    s["_heif"] = media.HEIF_OK
    return s


# 數值設定的合理範圍（超出就擋下，避免一個錯字讓背景工作整個停掉）
RANGES = {"batch_size": (1, 256), "workers": (1, 32), "video_max_frames": (1, 64), "scan_interval_h": (0, 720),
          "backup_keep": (1, 365), "general_threshold": (0.01, 1), "character_threshold": (0.01, 1), "neg_sim": (0.5, 1),
          "online_min_similarity": (1, 100), "online_verify_min": (1, 100), "online_retry_days": (1, 3650),
          "pixai_char_threshold": (0.1, 1), "pixai_artist_threshold": (0.1, 1),
          "pixai_copyright_threshold": (0.1, 1), "knn_p_min": (0.05, 0.95)}


def _validate(patch: dict) -> dict:
    out = {}
    for k, v in patch.items():
        if k not in db.DEFAULT_SETTINGS:
            continue
        d = db.DEFAULT_SETTINGS[k]
        try:
            if isinstance(d, bool):
                v = bool(v)
            elif isinstance(d, (int, float)):
                v = (int if isinstance(d, int) else float)(v)
                lo, hi = RANGES.get(k, (float("-inf"), float("inf")))
                if not lo <= v <= hi:
                    raise ValueError(f"要在 {lo}～{hi} 之間")
            elif isinstance(d, list):
                if not isinstance(v, list) or not all(isinstance(x, str) for x in v):
                    raise ValueError("格式不對")
            elif isinstance(d, str):
                v = "" if v is None else str(v)
        except (TypeError, ValueError) as e:
            raise HTTPException(400, f"設定「{k}」不正確：{e}")
        out[k] = v
    from .. import steps as _steps
    for k in [k for k in out if k.startswith("act_")]:
        try:
            out[k] = _steps.validate_act(k, out[k])
        except ValueError as e:
            raise HTTPException(400, f"設定「{k}」不正確：{e}")
    if "zh_mode" in out and out["zh_mode"] not in ("tw", "cn"):
        raise HTTPException(400, "設定「zh_mode」要是 tw 或 cn")
    if out.get("lan_pin") and not re.fullmatch(r"\d{4,8}", out["lan_pin"]):
        raise HTTPException(400, "PIN 要是 4～8 位數字")
    return out


@router.post("/api/settings")
def post_settings(patch: dict):
    old = db.get_settings()
    patch = _validate(patch)
    if "pixai_candidates" in patch and "act_pixai" not in patch:     # 舊的開關（瀏覽器腳本、測試）：換成新的「找到時怎麼辦」
        patch["act_pixai"] = "candidate" if patch["pixai_candidates"] else "record"
    if "roots" in patch:
        patch["roots"] = [os.path.normpath(r.strip().strip('"')) for r in patch["roots"] if r.strip()]
    if (patch.get("import_dir") or "").strip():
        # 匯入資料夾一定要在圖庫資料夾裡（才會被監看、定期掃描）；不在就自動加進去
        d = os.path.normpath(patch["import_dir"].strip().strip('"'))
        patch["import_dir"] = d
        roots = patch.get("roots", old["roots"])
        norm = lambda p: os.path.normcase(os.path.join(os.path.normpath(p), ""))
        if not any(norm(d).startswith(norm(r)) for r in roots):
            patch["roots"] = list(roots) + [d]
        os.makedirs(d, exist_ok=True)
    if (patch.get("archive_dir") or "").strip():
        from .. import archive
        patch["archive_dir"] = os.path.normpath(patch["archive_dir"].strip().strip('"'))
        err = archive.check_dest(patch["archive_dir"])
        if err:
            raise HTTPException(400, err)
    if patch.get("lan_enabled") and not (patch.get("lan_pin") or old.get("lan_pin")):
        import secrets
        patch["lan_pin"] = f"{secrets.randbelow(10 ** 6):06d}"
    new = db.update_settings(patch)
    j = pipeline.get_jobs()
    if new["nsfw_ratings"] != old["nsfw_ratings"]:
        db.recompute_nsfw_auto()
    if new["roots"] != old["roots"] or new["watch"] != old["watch"]:
        j.restart_watch()
    if new["roots"] != old["roots"]:
        pipeline.forget_removed_roots(old["roots"], new["roots"])
        j.submit("scan")
    if new["neg_sim"] != old["neg_sim"]:
        j.submit("sweep")
    if new.get("zh_mode") != old.get("zh_mode"):
        from .. import nameindex
        nameindex.invalidate()
        db.user_wrote()                       # 書架等快取的名字跟著換
        if new["zh_mode"] == "cn":
            from .. import online
            online.get_jobs().queue_series_cn()          # 以前查到台灣譯名的作品，補查大陸譯名
    if new.get("no_artist_tags") != old.get("no_artist_tags"):
        from .. import quicktags
        quicktags.sync_no_artist()
    if new.get("pixai_enabled") and not old.get("pixai_enabled"):
        j.submit("pixai")
    if new.get("media_clip") != old.get("media_clip"):
        if new.get("media_clip"):
            j.submit("clip")
        from .. import mediaclf
        mediaclf.schedule(0.5)
    if any(new.get(k) != old.get(k) for k in ("knn_p_min", "act_knn", "knn_enabled")):
        from .. import charknn
        charknn.schedule()
    if any(new.get(k) != old.get(k) for k in ("act_pixai", "pixai_candidates", "pixai_artists", "pixai_char_threshold",
                                              "pixai_artist_threshold", "pixai_copyright_threshold", "ai_hide_when_sourced")):
        from .. import pixai
        import threading as _th
        _th.Thread(target=pixai.apply_all, daemon=True, name="pixai-apply").start()   # 用存著的結果重算候選，不用重跑模型
    if new.get("no_char_tags") != old.get("no_char_tags"):
        from .. import charknn, quicktags
        with db.write_lock:
            quicktags.purge_candidates(db.conn())
        charknn.schedule()
    return get_settings()


# ---------- 資料夾瀏覽（首次設定精靈用）----------
def _quick_count(d: str, limit: int = 3000) -> int:
    n = 0
    try:
        with os.scandir(d) as it:
            for i, e in enumerate(it):
                if i > limit:
                    break
                if e.is_file() and media.kind_of(Path(e.name)):
                    n += 1
    except OSError:
        pass
    return n


@router.get("/api/fs")
def fs(path: str = ""):
    """列出資料夾。空白路徑 → 常用位置 + 磁碟機。"""
    if not path:
        home = Path.home()
        places = []
        for label, p in (("圖片", home / "Pictures"), ("下載", home / "Downloads"), ("桌面", home / "Desktop"),
                         ("OneDrive 圖片", home / "OneDrive" / "Pictures"), ("iCloud 照片", home / "Pictures" / "iCloud Photos"),
                         ("文件", home / "Documents"), ("家目錄", home)):
            if p.is_dir():
                places.append({"name": label, "path": str(p), "n": _quick_count(str(p))})
        drives = []
        if sys.platform == "win32":
            drives = [{"name": f"{L}:", "path": f"{L}:\\"} for L in string.ascii_uppercase if os.path.exists(f"{L}:\\")]
        else:
            drives = [{"name": "/", "path": "/"}] + [{"name": p.name, "path": str(p)} for p in Path("/Volumes").glob("*")] \
                if Path("/Volumes").exists() else [{"name": "/", "path": "/"}]
        return {"path": "", "parent": None, "places": places, "drives": drives, "dirs": []}
    p = os.path.normpath(path)
    if not os.path.isdir(p):
        raise HTTPException(404, "資料夾不存在")
    dirs = []
    try:
        with os.scandir(p) as it:
            for e in it:
                if e.is_dir(follow_symlinks=False) and not e.name.startswith((".", "$")):
                    dirs.append({"name": e.name, "path": os.path.join(p, e.name)})
    except PermissionError:
        raise HTTPException(403, "沒有權限讀取這個資料夾")
    dirs.sort(key=lambda d: d["name"].lower())
    parent = os.path.dirname(p.rstrip("\\/")) if os.path.dirname(p.rstrip("\\/")) != p else None
    return {"path": p, "parent": parent or None, "dirs": dirs[:500], "n": _quick_count(p)}


# ---------- 首頁 ----------
_LK_CACHE: dict = {}
@router.get("/api/pipeline")
def pipeline_status():
    """整理中心的「自動流程」：新圖進來會跑哪些步驟、開了沒、找到多少。"""
    from .. import r34
    c = db.conn()
    s = db.get_settings()
    q1 = lambda sql, *a: (c.execute(sql, a).fetchone() or [0])[0] or 0

    def tbl(name):
        return c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None
    by_method = {r[0]: r[1] for r in c.execute("SELECT method, COUNT(*) FROM media_source WHERE status='found' GROUP BY method")}
    total = q1("SELECT COUNT(*) FROM media WHERE status='done' AND kind='image'")
    searched = q1("SELECT COUNT(*) FROM media_source")
    a2d = dict(c.execute("SELECT status, COUNT(*) FROM ascii2d GROUP BY status").fetchall()) if tbl("ascii2d") else {}
    # 順序＝新圖真正跑的順序（online.search_one → _extras），分成幾段；使用者 2026-10-05 要求依實際使用重排
    steps = [
        {"key": "tagger", "stage": "local", "name": "WD Tagger 標記", "desc": "描述、分級（NSFW）、推薦角色。所有新圖一定會做", "always": True,
         "checked": q1("SELECT COUNT(*) FROM media WHERE status='done'"), "found": None},
        {"key": "pixai", "stage": "local", "name": "PixAI 認角色／畫師", "setting": "pixai_enabled",
         "desc": "第二個 AI 模型（認得 8,308 個角色、4,917 位畫師的畫風），在你的電腦上跑，結果放待確認",
         "checked": q1("SELECT COUNT(*) FROM pixai_pred") if tbl("pixai_pred") else 0,
         "found": q1("SELECT COUNT(DISTINCT media_id) FROM media_tags WHERE source='pixai' AND state=1"), "found_label": "你確認了"},
        {"key": "md5", "stage": "exact", "name": "Danbooru 原檔", "desc": "檔案、檔名 MD5、Pixiv 作品 ID、推文 ID 對得上 → 直接確認", "always": True,
         "checked": searched, "found": sum(by_method.get(k, 0) for k in ("md5", "md5_name", "x_media", "pixiv_id", "tweet", "tweet_multi"))},
        {"key": "pixel", "stage": "exact", "name": "像素指紋", "setting": "auto_pixelhash", "desc": "像素跟 Danbooru 原檔一樣（重存的 PNG、去掉 EXIF）→ 直接確認",
         "checked": q1("SELECT COUNT(*) FROM pixhash") if tbl("pixhash") else 0, "found": by_method.get("pixel_hash", 0)},
        {"key": "iqdb", "stage": "similar", "name": "以圖搜圖（IQDB）", "setting": "online_iqdb", "desc": "上傳縮小的圖找相似的（找到最多）",
         "checked": searched, "found": by_method.get("iqdb", 0) + by_method.get("duplicate", 0),
         "note": {"likely": "只搜很可能的", "maybe": "很可能＋可能", "all": "全部都搜"}.get(s.get("online_auto_level"), "")},
        {"key": "sig", "stage": "fallback", "name": "讀圖上的簽名", "setting": "auto_signature", "desc": "本機辨識 @帳號，對到畫師 → 待確認",
         "checked": q1("SELECT COUNT(*) FROM ocr_text") if tbl("ocr_text") else 0,
         "found": q1("SELECT COUNT(DISTINCT media_id) FROM media_tags WHERE source='signature'")},
        {"key": "r34", "stage": "fallback", "name": "Rule34 原檔", "setting": "auto_r34", "desc": "Danbooru 沒收的 NSFW 圖，只送指紋 → 待確認",
         "checked": q1("SELECT COUNT(*) FROM r34_checked") if tbl("r34_checked") else 0, "found": by_method.get("r34_md5", 0),
         "configured": r34.configured(), "note": "" if r34.configured() else "要先在設定填 API key"},
        {"key": "upgrade", "stage": "after", "name": "找更高清的版本", "setting": "auto_upgrade",
         "desc": "出處上的同一張比你的大 → 只提醒，換不換你決定",
         "checked": q1("SELECT COUNT(*) FROM upgrade"), "found": q1("SELECT COUNT(*) FROM upgrade WHERE status IN ('offer','done')"),
         "note": "已換 %d 張" % q1("SELECT COUNT(*) FROM upgrade WHERE status='done'")},
        {"key": "ascii2d", "stage": "manual", "name": "ascii2d 半自動", "tool": "ascii2d", "always": True,
         "desc": "Danbooru 沒有的 X／Pixiv 圖：你在 ascii2d 按 Enter，程式比對、整理作者",
         "checked": a2d.get("done", 0) + a2d.get("none", 0), "found": by_method.get("ascii2d", 0),
         "note": ("排隊 %d 張" % a2d["queued"]) if a2d.get("queued") else ""},
        {"key": "sweep", "stage": "manual", "name": "畫師作品比對", "tool": "sweep", "always": True,
         "desc": "已知畫師在 Pixiv／X 的全部作品跟沒出處的圖比，找到直接確認畫師",
         "checked": q1("SELECT COUNT(*) FROM sweep_artist") if tbl("sweep_artist") else 0, "checked_label": "位畫師",
         "found": by_method.get("pixiv_scan", 0) + by_method.get("x_scan", 0)},
    ]
    for st in steps:
        st["enabled"] = True if st.get("always") else bool(s.get(st["setting"], True))
        if st.get("configured") is False and st["key"] == "r34":
            st["enabled"] = False
        ck, fd = st.get("checked") or 0, st.get("found") or 0
        st["rate"] = round(fd / ck, 4) if ck and st.get("found") is not None and st["key"] not in ("sweep", "pixai") else None
        # 很少找到、又已經關掉的：收起來（找到率 < 0.5%，或你否決的比找到的多）
        st["rare"] = (not st["enabled"]) and ck >= 100 and (fd / ck < 0.005 or (st.get("rejected") or 0) > fd)
    sourced = q1("SELECT COUNT(*) FROM media_source s JOIN media m ON m.id=s.media_id WHERE s.status='found' AND m.status='done'")
    noid = q1("""SELECT COUNT(*) FROM media m WHERE m.status='done' AND m.kind='image' AND NOT EXISTS (SELECT 1 FROM media_tags x JOIN tags t ON t.id=x.tag_id
        WHERE x.media_id=m.id AND x.state=1 AND t.category IN ('character','artist','copyright'))""")
    return {"online": s["online_enabled"], "auto": s.get("online_auto_source", True), "total": total, "steps": steps,
            "sourced": sourced, "searched": searched, "noid": noid,
            "other": {"artist_scan": by_method.get("artist_scan", 0)}}


@router.get("/api/home")
def home(nsfw: str = "hide", noalbum: bool = False):
    """（b96）資料沒變就沿用上次算的：見 db.read_cache。"""
    return db.read_cache("home", (nsfw, noalbum,), lambda: _home(nsfw, noalbum), extra=(bool(pipeline.get_jobs().state.get("running")), bool(online.get_jobs().state.get("running")), online.get_jobs().state.get("kind")))


def _home(nsfw: str = "hide", noalbum: bool = False):
    c = db.conn()
    nf = f" AND {NSFW}=0" if nsfw == "hide" else ""
    base = f"FROM media m WHERE m.status='done' AND COALESCE(m.excluded,0)=0{nf}"
    # 計數合併成一次掃描
    total, week, today, miss_all, miss_artist = c.execute(f"""SELECT COUNT(*), COALESCE(SUM(m.added_at >= ?),0), COALESCE(SUM(m.added_at >= ?),0),
        COALESCE(SUM({MISSING['all']}),0), COALESCE(SUM({MISSING['artist']}),0) {base}""",
                                                          (time.time() - 7 * 86400, time.time() - 86400)).fetchone()
    tf = candidates.TIER_FILTER
    row = c.execute(f"""SELECT {", ".join(f"COALESCE(SUM({cond}),0)" for cond in tf.values())}
                FROM media_tags mt JOIN media m ON m.id=mt.media_id
                WHERE mt.state=0 AND mt.tag_id IN {IDENT_IDS} AND m.status='done' AND COALESCE(m.excluded,0)=0{nf}""").fetchone()
    cand = dict(zip(tf.keys(), row))
    recent = [r[0] for r in c.execute(f"""SELECT m.id {base} AND m.id NOT IN (SELECT media_id FROM album_items)
        ORDER BY m.added_at DESC, m.id DESC LIMIT 18""")]     # 跟圖庫一樣：作品集的頁不放（整本匯入會洗掉「最近加入」）
    lang = db.get_settings()["display_lang"]
    na = " AND m.id NOT IN (SELECT media_id FROM album_items)" if noalbum else ""   # 最近在收的張數：跟角色頁一致
    top = []
    # 最近收的角色：先分組排序取前 24，封面只替這 24 個查（不是每個角色都查一次）
    for r in c.execute(f"""SELECT t.id, t.name, ti.other_names, ti.display_name, t.cover, COUNT(*) n,
            MAX(COALESCE(m.mtime, m.added_at)) last
            FROM media_tags mt JOIN tags t ON t.id=mt.tag_id JOIN media m ON m.id=mt.media_id
            LEFT JOIN tag_info ti ON ti.tag_id=t.id
            WHERE t.category='character' AND mt.state=1 AND m.status='done'{nf}{na}
            GROUP BY t.id ORDER BY last DESC LIMIT 24""").fetchall():
        latest = r["cover"] or (c.execute(f"""SELECT x.media_id FROM media_tags x JOIN media m ON m.id=x.media_id
            WHERE x.tag_id=? AND x.state=1 AND m.status='done'{nf} ORDER BY COALESCE(m.mtime, m.added_at) DESC LIMIT 1""",
                                          (r["id"],)).fetchone() or [None])[0]
        top.append({"name": r["name"], "n": r["n"], "cover": latest,
                    "display": names.display(r["name"], json.loads(r["other_names"] or "[]"), r["display_name"], lang)})
    have = {x["name"] for x in top}
    top = [x for x in top if names.base_of(x["name"], have) is None][:12]   # 造型併入本體
    miss = {"all": miss_all, "artist": miss_artist}
    uns_ids = [r[0] for r in c.execute(f"""SELECT m.id {base} AND m.kind='image' AND {MISSING['all']}
        AND NOT EXISTS (SELECT 1 FROM media_source s WHERE s.media_id=m.id)""")]
    unsourced = len(uns_ids)
    from .. import likely
    key = (len(uns_ids), sum(uns_ids))
    if _LK_CACHE.get("key") == key and time.time() - _LK_CACHE.get("at", 0) < 600:
        lk = _LK_CACHE["v"]      # 預判要掃全部未搜源的圖，首頁每次打開都算太慢 → 10 分鐘內沿用
    else:
        lk = likely.summary(uns_ids) if uns_ids else {"likely": 0, "maybe": 0, "unlikely": 0}
        _LK_CACHE.update(key=key, v=lk, at=time.time())
    b = db.list_backups()
    j = pipeline.get_jobs().state
    from .review import _batch_where
    bw, bp = _batch_where(0.9, nsfw)
    batch_n, batch_names = c.execute(f"""SELECT COUNT(*), COUNT(DISTINCT t.id) FROM media_tags mt JOIN tags t ON t.id=mt.tag_id
        JOIN media m ON m.id=mt.media_id WHERE {bw}""", bp).fetchone()
    tips = []
    if cand["source"]:
        tips.append({"kind": "review", "tier": "source", "text": f"{cand['source']} 筆 Danbooru 找到的候選等你確認（最可信，確認最快）"})
    if unsourced and db.get_settings()["online_enabled"]:
        o = online.get_jobs().state
        busy = o.get("running") and o.get("phase") == "以圖搜源"
        tips.append({"kind": "source", "text": f"{unsourced} 張未識別的圖還沒搜源：預判很可能 {lk['likely']}、可能 {lk['maybe']}、"
                     f"不太可能 {lk['unlikely']}（只做快速比對）" + ("・背景正在搜" if busy else "")})
    if cand.get("prompt"):
        tips.append({"kind": "review", "tier": "prompt", "text": f"{cand['prompt']} 筆 AI 生成圖的提示詞裡寫了角色／作品：看一看是不是真的畫出來了"})
    if cand["pixiv"]:
        tips.append({"kind": "review", "tier": "pixiv", "text": f"{cand['pixiv']} 筆 Pixiv 找到的候選"})
    if cand.get("library"):
        tips.append({"kind": "review", "tier": "library", "text": f"{cand['library']} 筆「你的圖庫」推薦：跟你確認過的圖很像（WD 不認得的角色也有）"})
    if cand["ai"]:
        tips.append({"kind": "review", "tier": "ai", "text": f"{cand['ai']} 筆 WD Tagger 推薦（參考就好）"})
    if db.get_settings()["online_enabled"]:
        if _LK_CACHE.get("maybe_at", 0) < time.time() - 600:
            _LK_CACHE.update(maybe=len(online.skipped_maybe_ids()), maybe_at=time.time())
        o = online.get_jobs().state
        if _LK_CACHE["maybe"] and o.get("running") and o.get("kind") == "source":
            tips.append({"kind": "jobs", "text": f"背景正在以圖搜源（{o.get('done', 0)}/{o.get('total', 0)}，找到 {o.get('found', 0)}）：點這裡看進度"})
        elif _LK_CACHE["maybe"]:
            tips.append({"kind": "research_maybe", "n": _LK_CACHE["maybe"],
                         "text": f"{_LK_CACHE['maybe']} 張預判「可能」在 Danbooru、但還沒用以圖搜源找過：背景補搜（每張 2–3 秒）"})
    if miss["all"]:
        tips.append({"kind": "unrecognized", "text": f"{miss['all']} 張完全沒有身分資訊"})
        # 未識別裡的梗圖／截圖、有字的圖：分堆後可以整堆處理（不用一張一張略過）
        memes = c.execute(f"SELECT COUNT(*) {base} AND {MISSING['all']} AND m.pile IN ('meme','texty')").fetchone()[0]
        if memes >= 20:
            tips.append({"kind": "piles", "text": f"未識別裡約 {memes} 張看起來是梗圖、截圖或有字的圖：到「分堆」看過一遍，整堆略過（最快清掉的一批）"})
    a2 = _a2_tip()
    if a2:
        tips.append(a2)
    mp = c.execute(f"SELECT COUNT(*) {base} AND {MISSING['people']}").fetchone()[0]
    if mp:
        tips.append({"kind": "people", "text": f"{mp} 張多人圖只標了一部分角色（WD 看到的人比已標的多）：補標另一個人，或按「標完了」"})
    up = c.execute(f"SELECT COUNT(*) {base} AND m.id IN (SELECT media_id FROM upgrade WHERE status='offer') AND m.id NOT IN (SELECT media_id FROM album_items)").fetchone()[0]
    if up:
        tips.append({"kind": "upgrade", "text": f"{up} 張在出處有更高清的版本（同一張、比較大）：看過再決定要不要換"})
    s = db.get_settings()
    if int(s.get("relay_uncleared") or 0) > 0:
        gb = int(s.get("relay_uncleared_bytes") or 0) / 1e9
        tips.insert(0, {"kind": "icloud_clear", "text": f"{s['relay_uncleared']:,} 張（約 {gb:.1f} GB）已移到電腦，但還在 iCloud「最近刪除」裡占空間："
                        "到 icloud.com → 照片 → 最近刪除 清掉（一次最多 1,000 張），iPad 才能繼續同步"})
    # 待辦順序（2026-10-05 依使用情況）：要你清的 → 確認 → 整堆處理最快的 → 未識別 → 高清版 → 背景補搜（點一下就好）→ 多人圖補角色（最慢、最不急）
    # b93：ascii2d（找創作者＝主要目標）排在確認之後
    rank = {"icloud_clear": 0, "review": 1, "ascii2d": 2, "piles": 2, "unrecognized": 3, "upgrade": 4, "source": 5, "research_maybe": 5, "jobs": 5, "people": 6}
    tips.sort(key=lambda t: rank.get(t["kind"], 5))
    return {"danbooru": {**danbooru.search_mode(), "online": s["online_enabled"], "iqdb": s["online_iqdb"]},
            "total": total, "week": week, "today": today, "cand": cand, "batch": {"n": batch_n, "names": batch_names}, "recent": recent, "chars": top, "miss": miss,
            "unsourced": unsourced, "tips": tips, "last_backup": b[0]["mtime"] if b else None, "backup_offsite": db.backup_offsite(), "drive_overdue": _drive_overdue(),
            "research": online.research_status(),
            "job": {k: j.get(k) for k in ("running", "phase", "done", "total", "eta", "paused")},
            "setup_done": db.get_settings().get("setup_done")}


# ---------- 操作紀錄（db.op_log） ----------
def _op_since(days: int) -> float:
    return time.time() - max(1, min(int(days), 3650)) * 86400


@router.get("/api/oplog")
def oplog(days: int = 7, limit: int = 500, kind: str = ""):
    """最近的操作（新的在前）。kind：只看某一種（去掉張數的說明，例如「拿掉「梗圖」」）。"""
    c = db.conn()
    q = "SELECT id, ts, label, kind, keys, n, mids, view, via, undone_at FROM op_log WHERE ts >= ?"
    args: list = [_op_since(days)]
    if kind:
        q += " AND kind = ?"; args.append(kind)
    rows = c.execute(q + " ORDER BY id DESC LIMIT ?", (*args, max(1, min(limit, 5000)))).fetchall()
    return [{**dict(r), "mids": json.loads(r["mids"] or "[]")} for r in rows]


@router.get("/api/oplog/summary")
def oplog_summary(days: int = 30):
    """操作紀錄統計：每天幾次、最常做的動作、在哪一頁、鍵盤還是滑鼠、撤銷率、幾點最常整理。"""
    c = db.conn()
    since = _op_since(days)
    w = "FROM op_log WHERE ts >= ? AND keys IS NOT 'undo'"
    tot = c.execute(f"SELECT COUNT(*), COALESCE(SUM(n), 0), COUNT(undone_at), MIN(ts) {w}", (since,)).fetchone()
    first = c.execute("SELECT MIN(ts) FROM op_log").fetchone()[0]
    per_day = c.execute(f"""SELECT date(ts, 'unixepoch', 'localtime') d, COUNT(*), COALESCE(SUM(n), 0) {w}
                            GROUP BY d ORDER BY d DESC""", (since,)).fetchall()
    kinds = c.execute(f"""SELECT kind, COUNT(*) k, COALESCE(SUM(n), 0), COUNT(undone_at), SUM(via = 'key'), MAX(ts) {w}
                          GROUP BY kind ORDER BY k DESC LIMIT 25""", (since,)).fetchall()
    views = c.execute(f"SELECT COALESCE(view, '（背景）') v, COUNT(*) k {w} GROUP BY v ORDER BY k DESC LIMIT 15", (since,)).fetchall()
    via = c.execute(f"SELECT COALESCE(via, '') v, COUNT(*) {w} GROUP BY v", (since,)).fetchall()
    hours = [0] * 24
    for h, n in c.execute(f"SELECT CAST(strftime('%H', ts, 'unixepoch', 'localtime') AS INTEGER), COUNT(*) {w} GROUP BY 1", (since,)):
        hours[h] = n
    return {"days": days, "ops": tot[0], "images": tot[1], "undone": tot[2], "since": tot[3], "first": first,
            "per_day": [list(r) for r in per_day],
            "kinds": [{"kind": r[0], "ops": r[1], "images": r[2], "undone": r[3], "by_key": r[4] or 0, "last": r[5]} for r in kinds],
            "views": [list(r) for r in views], "via": {r[0]: r[1] for r in via}, "hours": hours}


@router.get("/api/oplog.csv")
def oplog_csv(days: int = 365):
    """整份操作紀錄（CSV，Excel 打得開）：時間、說明、種類、幾張、哪一頁、鍵盤／滑鼠、有沒有撤銷、圖片編號。"""
    import csv
    import io
    from datetime import datetime
    buf = io.StringIO()
    wr = csv.writer(buf)
    wr.writerow(["時間", "說明", "種類", "資料", "張數", "頁面", "方式", "撤銷時間", "圖片編號（最多 50）"])
    for r in db.conn().execute("SELECT * FROM op_log WHERE ts >= ? ORDER BY id", (_op_since(days),)):
        t = lambda x: datetime.fromtimestamp(x).strftime("%Y-%m-%d %H:%M:%S") if x else ""
        wr.writerow([t(r["ts"]), r["label"], r["kind"], r["keys"] or "", r["n"], r["view"] or "", r["via"] or "", t(r["undone_at"]),
                     " ".join(map(str, json.loads(r["mids"] or "[]")))])
    return Response("\ufeff" + buf.getvalue(), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": "attachment; filename=pickle-jar-oplog.csv"})


# ---------- 診斷 ----------
_A2_CACHE: dict = {}


def _a2_tip() -> dict | None:
    """收件匣（b93）：ascii2d 還有值得找、從沒排進去的圖（找到率高的那幾層），或佇列停著好幾天。10 分鐘算一次。"""
    from .. import ascii2d
    now = time.time()
    if _A2_CACHE.get("at", 0) > now - 600:
        return _A2_CACHE.get("tip")
    tip = None
    try:
        c = db.conn()
        queued = {r[0] for r in c.execute("SELECT media_id FROM ascii2d WHERE status='queued'")}
        fresh = [i for i in ascii2d.targets() if i not in queued]
        last = c.execute("SELECT MAX(COALESCE(decided_at, searched_at)) FROM ascii2d").fetchone()[0]
        idle_d = (now - last) / 86400 if last else None
        if len(fresh) >= 20:
            f = ascii2d.forecast(fresh)
            tip = {"kind": "ascii2d", "n": len(fresh), "expect": f["expect"], "queued": len(queued),
                   "text": f"ascii2d：{len(fresh):,} 張插畫還沒找過創作者（照你過去的找到率，約可找到 {f['expect']:,} 位、全自動約 "
                           f"{max(1, round(f['minutes'] / 60))} 小時）" + (f"；佇列裡還有 {len(queued):,} 張" if queued else "")}
        elif len(queued) >= 10 and idle_d is not None and idle_d >= 2:
            tip = {"kind": "ascii2d", "n": 0, "queued": len(queued),
                   "text": f"ascii2d 佇列還有 {len(queued):,} 張，{int(idle_d)} 天沒動了：到 ascii2d.net 接著跑"}
    except Exception as e:  # noqa
        log.info("ascii2d 提示失敗：%s", e)
    _A2_CACHE.update(at=now, tip=tip)
    return tip


@router.get("/api/diag/perf")
def diag_perf():
    """回應速度與寫入鎖（app/perf.py）：慢的請求、各路由的中位數／p90、誰抱寫入鎖抱很久。"""
    from .. import perf
    return perf.summary()


@router.get("/api/diag")
def diag():
    s = db.get_settings()
    t = tagger.loaded()
    try:
        import onnxruntime as ort
        ort_v = ort.__version__
        pkg = next((n for n in ("onnxruntime-directml", "onnxruntime-gpu", "onnxruntime") if _has_dist(n)), "onnxruntime")
    except Exception as e:  # noqa
        ort_v, pkg = f"未安裝（{e}）", None
    try:
        import cv2
        cv = cv2.__version__
    except Exception:  # noqa
        cv = None
    from .. import errlog
    du = shutil.disk_usage(db.DATA_DIR)
    c = db.conn()
    return {
        "python": sys.version.split()[0], "platform": platform.platform(), "onnxruntime": ort_v, "ort_package": pkg,
        "providers_available": tagger.available_providers(),
        "provider_active": t.provider if t else None, "model": s["model_repo"],
        "model_files": tagger.model_files(s["model_repo"]),
        "model_loaded": bool(t), "model_embedding": t.has_emb if t else None, "model_tags": len(t.names) if t else None,
        "opencv": cv, "heif": media.HEIF_OK,
        "db_size": sum(p.stat().st_size for p in (db.DB_PATH, Path(str(db.DB_PATH) + "-wal")) if p.exists()),
        "disk_free": du.free, "backups": db.list_backups()[:10],
        "watching": bool(pipeline.get_jobs().observer),
        "counts": {k: c.execute(q).fetchone()[0] for k, q in (
            ("media", "SELECT COUNT(*) FROM media"), ("no_fp", "SELECT COUNT(*) FROM media WHERE fp IS NULL AND status='done'"),
            ("errors", "SELECT COUNT(*) FROM media WHERE status='error'"),
            ("missing", "SELECT COUNT(*) FROM media WHERE status='missing'"))},
        "hf_endpoint": s.get("hf_endpoint") or os.environ.get("HF_ENDPOINT") or "https://huggingface.co",
        "server_errors": [{k: e[k] for k in ("at", "method", "path", "error")} for e in errlog.recent(10)],
    }


def _has_dist(name: str) -> bool:
    try:
        from importlib.metadata import version
        version(name)
        return True
    except Exception:  # noqa
        return False


@router.post("/api/diag/net")
def diag_net():
    """測試各線上服務連得到嗎（不送出你的圖片）。"""
    import httpx
    s = db.get_settings()
    hf = (s.get("hf_endpoint") or os.environ.get("HF_ENDPOINT") or "https://huggingface.co").rstrip("/")
    targets = {"Hugging Face（模型下載）": f"{hf}/{s['model_repo']}/resolve/main/selected_tags.csv",
               "Danbooru": f"{danbooru.DANBOORU}/posts.json?limit=1",
               "IQDB": danbooru.IQDB, "Pixiv": f"{pixiv.PIXIV}/"}
    out = []
    for name, url in targets.items():
        t0 = time.time()
        try:
            r = httpx.head(url, timeout=8, follow_redirects=True, headers={"User-Agent": httpclient.UA})
            if r.status_code == 405:
                r = httpx.get(url, timeout=8, follow_redirects=True, headers={"User-Agent": httpclient.UA})
            # 有回應就算連得到；403 通常是被防火牆/Cloudflare 擋
            out.append({"name": name, "ok": r.status_code < 500 and r.status_code != 403, "code": r.status_code,
                        "ms": round((time.time() - t0) * 1000)})
        except Exception as e:  # noqa
            out.append({"name": name, "ok": False, "error": f"{type(e).__name__}: {e}"[:200]})
    return out


@router.post("/api/diag/bench")
def diag_bench():
    """模型健檢：載入模型、跑一張測試圖、量速度。"""
    import numpy as np
    from PIL import Image
    s = db.get_settings()
    t0 = time.time()
    tg = tagger.get(s["model_repo"])
    load = time.time() - t0
    img = Image.new("RGB", (512, 512), (240, 200, 210))
    x = np.stack([tg.preprocess(img)] * int(s["batch_size"]))
    tg.run(x[:1])  # 暖機
    t1 = time.time()
    n = 0
    while time.time() - t1 < 3 and n < 8:
        probs, emb = tg.run(x)
        n += 1
    dt = time.time() - t1
    ok = bool(np.isfinite(probs).all() and probs.shape[1] == len(tg.names))
    return {"provider": tg.provider, "load_s": round(load, 2), "per_image_ms": round(dt / (n * len(x)) * 1000, 1),
            "images_per_s": round(n * len(x) / dt, 1), "embedding_dim": int(emb.shape[1]), "sane": ok,
            "hint": None if tg.provider != "CPUExecutionProvider" else "目前用 CPU 在跑，會很慢。Windows 請確認裝的是 onnxruntime-directml（執行 use-directml.bat）。"}


# ---------- 備份 ----------
@router.get("/api/backups")
def backups():
    return db.list_backups()


@router.post("/api/backups")
def backup_now():
    p = db.backup(force=True)
    return {"ok": True, "name": p.name if p else None}


class RestoreReq(BaseModel):
    name: str


@router.post("/api/backups/restore")
def backup_restore(body: RestoreReq):
    """還原要在重新啟動時做（避免跟正在寫的資料庫打架）：先排定，下次啟動套用。"""
    src = db.BACKUP_DIR / os.path.basename(body.name)
    if not src.exists():
        raise HTTPException(404)
    shutil.copy2(src, db.DATA_DIR / "restore-pending.db")   # 先複製（下一步的備份可能會清掉最舊的那份）
    db.backup(force=True)  # 還原前先把目前的也存一份
    return {"ok": True, "restart": True}


# ---------- 匯出 / 匯入 ----------
@router.get("/api/export")
def export(full: bool = False):
    """把你手動做的整理（確認/否決、NSFW、排除、合併、顯示名稱…）匯出成 JSON。
    以檔案內容指紋（MD5）對應，換電腦、重新整理資料夾後都能匯回。"""
    c = db.conn()
    s = {k: v for k, v in db.get_settings().items() if k not in SECRET_KEYS}
    tags = []
    for r in c.execute("""SELECT t.*, ti.display_name, ti.other_names, ti.copyright, ti.manual FROM tags t
                          LEFT JOIN tag_info ti ON ti.tag_id=t.id
                          WHERE t.category!='general' OR t.pinned=1 OR t.auto_block=1"""):
        cover = c.execute("SELECT fp FROM media WHERE id=?", (r["cover"],)).fetchone() if r["cover"] else None
        tags.append({"name": r["name"], "category": r["category"], "pinned": r["pinned"], "auto_block": r["auto_block"],
                     "cover_fp": cover[0] if cover else None, "display_name": r["display_name"],
                     "other_names": json.loads(r["other_names"] or "[]") if r["manual"] else None,
                     "copyright": r["copyright"] if r["manual"] else None})
    items = []
    cond = "" if full else """AND (m.nsfw_manual IS NOT NULL OR m.excluded=1
        OR EXISTS (SELECT 1 FROM media_tags x WHERE x.media_id=m.id AND x.state!=0))"""
    for m in c.execute(f"SELECT m.id, m.path, m.fp, m.nsfw_manual, m.excluded FROM media m WHERE m.status!='missing' {cond}"):
        t = [[r[0], r[1], r[2]] for r in c.execute("""SELECT t.name, t.category, mt.state FROM media_tags mt JOIN tags t ON t.id=mt.tag_id
                                                      WHERE mt.media_id=? AND mt.state!=0""", (m["id"],))]
        items.append({"fp": m["fp"], "path": m["path"], "nsfw": m["nsfw_manual"], "excluded": m["excluded"], "tags": t})
    data = {"app": "pickle-jar", "version": 2, "exported_at": time.time(), "settings": s, "tags": tags,
            "aliases": [[r[0], r[1]] for r in c.execute("SELECT a.name, t.name FROM tag_alias a JOIN tags t ON t.id=a.tag_id")],
            "saved": [[r[0], r[1], r[2]] for r in c.execute("SELECT name, query, view FROM saved_search")],
            "media": items}
    fn = f"library-export-{time.strftime('%Y%m%d')}.json"
    return Response(json.dumps(data, ensure_ascii=False), media_type="application/json",
                    headers={"Content-Disposition": f'attachment; filename="{fn}"'})


@router.post("/api/import")
async def import_(request: Request):
    """匯入 export 的 JSON：先用指紋對應，再用路徑；已確認/已否決的不覆蓋（你現在的判斷優先）。
    實際工作丟到背景執行緒，不卡住整個伺服器；中途出錯會整批撤回（write_lock 會 rollback）。"""
    if "json" not in (request.headers.get("content-type") or ""):
        raise HTTPException(415, "要用 JSON 上傳")
    try:
        data = json.loads(await request.body())
    except ValueError:
        raise HTTPException(400, "檔案不是正確的 JSON")
    from starlette.concurrency import run_in_threadpool
    try:
        return await run_in_threadpool(_import_data, data)
    except (KeyError, TypeError, ValueError, AttributeError) as e:
        raise HTTPException(400, f"檔案內容格式不對，已全部撤回：{type(e).__name__}: {e}")


def _import_data(data: dict):
    if not isinstance(data, dict) or data.get("app") != "pickle-jar":
        raise HTTPException(400, "不是圖庫匯出的檔案")
    c = db.conn()
    stats = {"matched": 0, "unmatched": 0, "tags_set": 0}
    with db.write_lock:
        db.backup(force=True)
        for t in data.get("tags", []):
            tid = db.tag_id(t["name"], t["category"])
            c.execute("UPDATE tags SET pinned=MAX(pinned, ?), auto_block=MAX(auto_block, ?) WHERE id=?",
                      (t.get("pinned") or 0, t.get("auto_block") or 0, tid))
            if t.get("display_name") or t.get("other_names"):
                c.execute("""INSERT INTO tag_info(tag_id, status, display_name, other_names, copyright, manual) VALUES(?, 'ok', ?, ?, ?, ?)
                    ON CONFLICT(tag_id) DO UPDATE SET display_name=COALESCE(tag_info.display_name, excluded.display_name)""",
                          (tid, t.get("display_name"), json.dumps(t.get("other_names") or [], ensure_ascii=False),
                           t.get("copyright"), 1 if t.get("other_names") else 0))
        for name, target in data.get("aliases", []):
            c.execute("INSERT OR IGNORE INTO tag_alias(name, tag_id) VALUES(?, ?)", (name, db.tag_id(target)))
        for name, q, view in data.get("saved", []):
            if not c.execute("SELECT 1 FROM saved_search WHERE query=?", (q,)).fetchone():
                c.execute("INSERT INTO saved_search(name, query, view, created_at) VALUES(?,?,?,?)", (name, q, view, time.time()))
        for it in data.get("media", []):
            r = (c.execute("SELECT id FROM media WHERE fp=?", (it["fp"],)).fetchone() if it.get("fp") else None) or \
                c.execute("SELECT id FROM media WHERE path=?", (it.get("path") or "",)).fetchone()
            if not r:
                stats["unmatched"] += 1
                continue
            stats["matched"] += 1
            mid = r[0]
            if it.get("nsfw") is not None:
                c.execute("UPDATE media SET nsfw_manual=COALESCE(nsfw_manual, ?) WHERE id=?", (it["nsfw"], mid))
            if it.get("excluded"):
                c.execute("UPDATE media SET excluded=1 WHERE id=?", (mid,))
            for name, cat, state in it.get("tags", []):
                tid = db.tag_id(name, cat)
                cur = c.execute("""INSERT INTO media_tags(media_id, tag_id, source, state, tier, reason) VALUES(?,?,'manual',?,NULL,'匯入')
                    ON CONFLICT(media_id, tag_id) DO UPDATE SET state=excluded.state WHERE media_tags.state=0""", (mid, tid, state))
                stats["tags_set"] += cur.rowcount
        for t in data.get("tags", []):
            if t.get("cover_fp"):
                r = c.execute("SELECT id FROM media WHERE fp=?", (t["cover_fp"],)).fetchone()
                if r:
                    c.execute("UPDATE tags SET cover=COALESCE(cover, ?) WHERE name=?", (r[0], t["name"]))
        c.commit()
    matcher.build()
    return stats


class XmpReq(BaseModel):
    ids: list[int] | None = None


XMP_MARK = "PickleJar"
XMP_MARKS_OLD = ("IllustrationLibrary",)   # 改名前寫的側車檔也算自己的


@router.post("/api/export/xmp")
def export_xmp(body: XmpReq):
    """在圖片旁邊寫 .xmp 側車檔（Lightroom / digiKam / Eagle 等讀得到），只寫已確認的角色/創作者/作品/自訂標籤。
    不會改動圖片本身。"""
    c = db.conn()
    ids = body.ids or [r[0] for r in c.execute("""SELECT DISTINCT mt.media_id FROM media_tags mt JOIN tags t ON t.id=mt.tag_id
        WHERE mt.state=1 AND t.category IN ('character','artist','copyright','custom')""")]
    lang = db.get_settings()["display_lang"]
    label = {"character": "角色", "artist": "創作者", "copyright": "作品", "custom": "自訂"}
    written = failed = skipped = 0
    # 同一個資料夾裡 a.jpg 和 a.png 會共用 a.xmp → 這種就跳過，免得互相蓋掉
    from collections import Counter
    stems = Counter(os.path.splitext(r[0])[0].lower() for r in c.execute("SELECT path FROM media WHERE status!='missing'"))
    for mid in ids:
        m = c.execute("SELECT path FROM media WHERE id=? AND status!='missing'", (mid,)).fetchone()
        if not m:
            continue
        side = Path(os.path.splitext(m["path"])[0] + ".xmp")
        if stems[os.path.splitext(m["path"])[0].lower()] > 1:
            skipped += 1
            continue
        if side.exists():
            try:
                txt = side.read_text(encoding="utf-8", errors="ignore")
                mine = any(k in txt for k in (XMP_MARK, *XMP_MARKS_OLD))
            except OSError:
                mine = False
            if not mine:   # 別的軟體寫的側車檔，不要蓋掉
                skipped += 1
                continue
        rows = c.execute("""SELECT t.name, t.category, ti.other_names, ti.display_name FROM media_tags mt JOIN tags t ON t.id=mt.tag_id
            LEFT JOIN tag_info ti ON ti.tag_id=t.id WHERE mt.media_id=? AND mt.state=1
            AND t.category IN ('character','artist','copyright','custom')""", (mid,)).fetchall()
        if not rows:
            continue
        subj = []
        hier = []
        for r in rows:
            disp = names.display(r["name"], json.loads(r["other_names"] or "[]"), r["display_name"], lang)
            subj += [r["name"]] + ([disp] if disp != r["name"] else [])
            hier.append(f"{label[r['category']]}|{disp}")
        li = lambda xs: "".join(f"<rdf:li>{escape(x)}</rdf:li>" for x in xs)  # noqa: E731
        xmp = f"""<?xpacket begin="﻿" id="W5M0MpCehiHzreSzNTczkc9d"?>
<x:xmpmeta xmlns:x="adobe:ns:meta/"><rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">
<rdf:Description rdf:about="" xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:lr="http://ns.adobe.com/lightroom/1.0/" xmlns:xmp="http://ns.adobe.com/xap/1.0/" xmp:CreatorTool="{XMP_MARK}">
<dc:subject><rdf:Bag>{li(subj)}</rdf:Bag></dc:subject>
<lr:hierarchicalSubject><rdf:Bag>{li(hier)}</rdf:Bag></lr:hierarchicalSubject>
</rdf:Description></rdf:RDF></x:xmpmeta>
<?xpacket end="w"?>"""
        try:
            side.write_text(xmp, encoding="utf-8")
            written += 1
        except OSError:
            failed += 1
    return {"written": written, "failed": failed, "skipped": skipped}


@router.post("/api/system/restart")
def restart():
    """重新啟動程式（套用更新後的後端程式碼）：開一個新的行程接手，舊的結束。"""
    import subprocess
    import threading
    root = Path(__file__).resolve().parent.parent.parent
    args = [sys.executable, str(root / "run.py"), "--no-browser", "--wait-port",
            "--port", os.environ.get("LIB_PORT", "7860")]
    subprocess.Popen(args, cwd=str(root))
    threading.Timer(0.8, lambda: os._exit(0)).start()
    return {"restarting": True}


@router.get("/api/diag/eval-pixai")
def eval_pixai():
    """PixAI 的準確度（唯讀）：拿你確認／否決過的身分當答案；Danbooru 來的答案另外算（模型可能看過）。"""
    from .. import pixai
    return {"stats": pixai.stats(), "eval": pixai.evaluate()}


@router.post("/api/pixai/run")
def pixai_run(body: dict | None = None):
    """手動：指定的圖（ids）立刻跑 PixAI；沒給就接著做還沒跑的。"""
    from .. import pixai
    if not db.get_settings().get("pixai_enabled"):
        raise HTTPException(400, "先到 系統 › 自動流程 打開「PixAI 認角色／畫師」")
    ids = [int(i) for i in (body or {}).get("ids", [])][:500] or None
    pipeline.get_jobs().submit("pixai", ids)
    return {"ok": True, "pending": len(pixai.pending_ids())}


@router.get("/api/diag/eval-tagger")
def eval_tagger():
    """驗證 WD Tagger 角色準確度（唯讀）：用非模型來源確認的角色當答案，搜不到也算沒中。"""
    from .. import evaltagger
    return {"tagger": evaltagger.evaluate(), "search": evaltagger.search_rates()}


@router.get("/api/diag/eval-knn")
def eval_knn():
    """新角色辨識的留一驗證（唯讀；同一個資料夾的圖不算）。"""
    from .. import charknn
    return charknn.evaluate()


@router.get("/api/diag/eval-media")
def eval_media():
    """媒材分類的 5 折交叉驗證（唯讀）：用你標的（和起始標註）算準確率、各類別的召回／精確。"""
    from .. import mediaclf
    return mediaclf.evaluate()


@router.get("/api/mediaclf/status")
def mediaclf_status():
    from .. import clipemb, mediaclf
    c = db.conn()
    lab = dict(c.execute("SELECT src, COUNT(*) FROM media_label GROUP BY src").fetchall())
    return {"live": mediaclf.live(), "clip": clipemb.stats(), "labels": lab}


def _event_snapshot() -> dict:
    """事件流每一輪要比的東西：狀態列（同 /api/status）、背景工作登記表的版本、「你的圖庫」推薦的版本。"""
    from .. import charknn, tasks
    return {"status": status(), "tasks": {"v": tasks.version(), "run": tasks.live_progress()}, "knn": charknn.live()}


@router.get("/api/events")
async def events(request: Request, once: int = 0):
    """事件流（SSE，b93）：取代前端好幾個各自的定時輪詢（狀態列 1.5–4 秒、待確認頁 4 秒、背景工作面板 2 秒）。
    伺服器每 1 秒（有工作在跑）／3 秒（閒置）看一次，有變才送；20 秒沒送東西送一行註解保持連線。
    once=1：送完第一批就結束（測試用）。手機（區網）不開放，前端會退回輪詢。"""
    import asyncio
    import json as _json
    from starlette.concurrency import run_in_threadpool
    from fastapi.responses import StreamingResponse

    async def gen():
        last: dict[str, str] = {}
        quiet = 0.0
        yield "retry: 3000\n\n"
        while True:
            try:
                snap = await run_in_threadpool(_event_snapshot)
            except Exception as e:  # noqa  一次讀失敗不要斷線
                snap = {"error": {"detail": str(e)[:200]}}
            sent = False
            for topic, payload in snap.items():
                key = _json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
                if last.get(topic) != key:
                    last[topic] = key
                    sent = True
                    yield f"event: {topic}\ndata: {key}\n\n"
            if once:
                return
            st = snap.get("status") or {}
            busy = st.get("running") or (st.get("online") or {}).get("running") or (snap.get("knn") or {}).get("running") \
                or (snap.get("tasks") or {}).get("run")
            wait = 1.0 if busy else 3.0
            quiet = 0.0 if sent else quiet + wait
            if quiet >= 20:
                quiet = 0.0
                yield ": ping\n\n"
            await asyncio.sleep(wait)
            if await request.is_disconnected():
                return

    return StreamingResponse(gen(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.get("/api/knn/live")
def knn_live():
    """待確認頁輪詢：「你的圖庫」推薦有沒有變。"""
    from .. import charknn
    return charknn.live()


@router.get("/api/diag/eval-general")
def eval_general():
    """驗證 WD Tagger 一般標籤的準確度（唯讀）：跟 Danbooru 原檔的人工標籤比。"""
    from .. import evaltagger
    return evaltagger.general_eval()
