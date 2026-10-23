"""換成高清版：找到出處時順便看「那邊的圖是不是比你的大」，你決定要不要換。

使用者的規則（2026-10-01）：
- 像素比較多才算（長邊至少大 10%）；同尺寸 JPG → PNG 不算
- 長寬比要一樣（差 ≤ 4%）：比例不同多半是裁切過或另一張圖
- 換之前一定下載下來逐像素比對（dupverify，跟重複圖同一個標準）：IQDB 找到的可能是差分（改表情、換字），差分不換
- 換的時候：新檔放在舊檔的資料夾、檔名主幹不變（副檔名照新檔）；圖庫紀錄改指向新檔，
  標記、確認、作品集頁序、搜源結果全部保留；舊檔丟到資源回收筒
- 檔案日期沿用舊檔（不然依日期排序會跑到最前面）

哪裡找得到高清版：搜源結果裡的 Danbooru 貼文、Pixiv 作品、推文、e621／Rule34 貼文、從網站匯入的原貼文。
Danbooru 的尺寸搜源時就知道了（不用再連網）；Pixiv、X、e621、Rule34 要問一次網站。

什麼時候查：
- 搜源找到出處時（設定 auto_upgrade，預設開）：先記在記憶體，每 10 秒合成一組丟進線上佇列（背景、可停止）
- 你按「補查已有出處的圖」：全部有出處、還沒查過（或出處更新過）的圖
- 你在大圖貼網址：只查那篇貼文
"""
from __future__ import annotations

import json
import logging
import os
import re
import shutil
import threading
import time
from pathlib import Path

from . import db

log = logging.getLogger("upgrade")

SCHEMA = """CREATE TABLE IF NOT EXISTS upgrade(
  media_id INTEGER PRIMARY KEY REFERENCES media(id) ON DELETE CASCADE,
  status TEXT NOT NULL,        -- offer 有更高清的 | none 查過、沒有 | done 換好了 | failed 換失敗 | dismissed 你說不用換
  site TEXT, post_url TEXT, width INTEGER, height INTEGER,     -- 最好的那個
  cands TEXT,                  -- 可以換的全部（JSON，好的在前；換的時候第一個不是同一張就試下一個）
  local_w INTEGER, local_h INTEGER, checked_at REAL, error TEXT,
  old_path TEXT, old_fp TEXT, old_w INTEGER, old_h INTEGER, old_size INTEGER, done_at REAL);
CREATE INDEX IF NOT EXISTS ix_upgrade_status ON upgrade(status)"""

MIN_GAIN = 1.10          # 長邊至少大 10%
ASPECT = 0.04            # 長寬比差 ≤ 4%（跟 same_image 一樣）
MAX_TRY = 4              # 換的時候最多下載幾個候選
OK_EXT = {"jpg", "jpeg", "png", "webp"}
SITE_ORDER = {"pixiv": 0, "danbooru": 1, "x": 2, "e621": 3, "r34": 4}     # 一樣大時 Pixiv 優先（使用者要求）
SITE_NAME = {"pixiv": "Pixiv", "danbooru": "Danbooru", "x": "X", "e621": "e621", "r34": "Rule34"}
_PAGE = re.compile(r"(\d{3,12})(?:-[0-9a-f]{32})?_p(\d+)")


class UpgradeError(Exception):
    pass


# ---------- 這張圖的出處（貼文） ----------
def refs(mid: int) -> list[dict]:
    """這張圖已知的貼文：[{site, id, size?, page?, index?}]。"""
    from . import importer
    c = db.conn()
    m = c.execute("SELECT path FROM media WHERE id=?", (mid,)).fetchone()
    if not m:
        return []
    out: dict = {}

    def add(site, pid, **kw):
        if pid:
            r = out.setdefault((site, str(pid)), {"site": site, "id": str(pid)})
            r.update({k: v for k, v in kw.items() if v is not None and k not in r})

    page = None
    ms = c.execute("SELECT status, pixiv_id, post_url, source_url, data FROM media_source WHERE media_id=?", (mid,)).fetchone()
    if ms and ms["status"] == "found":
        d = json.loads(ms["data"] or "{}")
        dan = d.get("danbooru") or {}
        for u in (dan.get("source"), ms["source_url"], os.path.basename(m["path"])):
            pm = _PAGE.search(u or "")
            if pm and (not ms["pixiv_id"] or pm.group(1) == str(ms["pixiv_id"])):
                page = int(pm.group(2))
                break
        if dan.get("post_id"):
            add("danbooru", dan["post_id"], size=dan.get("size"))
        if ms["pixiv_id"]:
            add("pixiv", ms["pixiv_id"], page=page)
        if d.get("tweet_id"):
            add("x", d["tweet_id"])
        if (d.get("e621") or {}).get("post_id"):
            add("e621", d["e621"]["post_id"])
        if (d.get("rule34") or {}).get("post_id"):
            add("r34", d["rule34"]["post_id"])
        for u in (ms["post_url"], ms["source_url"], dan.get("source"), *(d.get("also") or [])):
            p = importer.parse(u or "")
            if p:
                add(p["site"], p["id"], index=p.get("index"))
    try:
        im = importer.post_url_of(m["path"])
    except Exception:  # noqa
        im = None
    if im:
        p = importer.parse(im["post_url"] or "")
        if p:
            add(p["site"], p["id"], index=p.get("index"))
    return list(out.values())


# ---------- 貼文上的圖（尺寸、原檔網址） ----------
def _ext(url: str, default: str = "jpg") -> str:
    m = re.search(r"\.(jpe?g|png|webp|gif|mp4|webm|zip|avif)(?:[?#:]|$)", url or "", re.I)
    return (m.group(1).lower() if m else default)


def files(ref: dict, need_url: bool = False) -> list[dict]:
    """一篇貼文上的圖：[{site, post_url, url, w, h, ext, headers, page}]。只回傳圖片（不含動圖、影片）。"""
    site, pid = ref["site"], ref["id"]
    if site == "danbooru":
        from .danbooru import DANBOORU, dan_get
        purl = f"{DANBOORU}/posts/{pid}"
        size = ref.get("size") or []
        if not need_url and len(size) == 2 and size[0] and size[1]:
            return [{"site": site, "post_url": purl, "url": None, "w": size[0], "h": size[1], "ext": None}]
        p = dan_get(f"/posts/{pid}.json")
        ext = (p.get("file_ext") or "").lower()
        if ext not in OK_EXT or not p.get("file_url"):
            return []
        return [{"site": site, "post_url": purl, "url": p["file_url"], "w": p.get("image_width"),
                 "h": p.get("image_height"), "ext": ext}]
    if site == "pixiv":
        from .httpclient import BROWSER_UA, _req
        from .pixiv import PIXIV
        s = db.get_settings()
        headers = {"User-Agent": BROWSER_UA, "Referer": "https://www.pixiv.net/"}
        cookies = {"PHPSESSID": s["pixiv_phpsessid"]} if s.get("pixiv_phpsessid") else None
        r = _req("pixiv", "GET", f"{PIXIV}/ajax/illust/{pid}/pages", headers=headers, cookies=cookies)
        body = (r.json() or {}).get("body") or [] if r.status_code < 400 else []
        out = []
        for i, pg in enumerate(body):
            if ref.get("page") is not None and i != ref["page"]:
                continue
            u = (pg.get("urls") or {}).get("original")
            if u and _ext(u) in OK_EXT:
                out.append({"site": site, "post_url": f"https://www.pixiv.net/artworks/{pid}", "url": u, "w": pg.get("width"),
                            "h": pg.get("height"), "ext": _ext(u), "headers": {"Referer": "https://www.pixiv.net/"}, "page": i})
        return out
    if site == "x":
        from .httpclient import _req
        from .importer import FX_API
        r = _req("fxtwitter", "GET", f"{FX_API}/status/{pid}")
        if r.status_code >= 400:
            return []
        t = (r.json() or {}).get("tweet") or {}
        user = (t.get("author") or {}).get("screen_name") or "i"
        out = []
        for i, m in enumerate((t.get("media") or {}).get("all") or [], 1):
            if m.get("type") != "photo" or not m.get("url") or (ref.get("index") and i != ref["index"]):
                continue
            base = m["url"].split("?")[0]
            out.append({"site": site, "post_url": f"https://x.com/{user}/status/{pid}", "url": base + "?name=orig",
                        "w": m.get("width"), "h": m.get("height"), "ext": _ext(base), "page": i})
        return out
    if site == "e621":
        from . import e621
        f = (((e621._get(f"/posts/{pid}.json", {}) or {}).get("post")) or {}).get("file") or {}
        ext = (f.get("ext") or "").lower()
        if not f.get("url") or ext not in OK_EXT:
            return []
        return [{"site": site, "post_url": f"https://e621.net/posts/{pid}", "url": f["url"], "w": f.get("width"),
                 "h": f.get("height"), "ext": ext}]
    if site == "r34":
        from . import r34
        if not r34.configured():
            return []
        txt = r34._get({"s": "post", "q": "index", "json": 1, "id": pid})
        try:
            d = json.loads(txt) if txt and txt not in ("[]", '""') else []
        except ValueError:
            d = []
        p = (d[0] if isinstance(d, list) and d else None) or {}
        u = p.get("file_url")
        if not u or _ext(u) not in OK_EXT:
            return []
        return [{"site": site, "post_url": f"https://rule34.xxx/index.php?page=post&s=view&id={pid}", "url": u,
                 "w": p.get("width"), "h": p.get("height"), "ext": _ext(u)}]
    return []


def better(f: dict, lw: int, lh: int) -> bool:
    """f 比你的 (lw, lh) 大，而且長寬比一樣。"""
    w, h = f.get("w") or 0, f.get("h") or 0
    if not (w and h and lw and lh):
        return False
    if abs(w / h - lw / lh) > ASPECT * (lw / lh):
        return False
    return max(w, h) >= MIN_GAIN * max(lw, lh)


def _rank(f: dict):
    return (-(f["w"] * f["h"]), SITE_ORDER.get(f["site"], 9))


def _local(mid: int):
    return db.conn().execute("SELECT id, path, kind, fp, width, height, size, status, mtime FROM media WHERE id=?", (mid,)).fetchone()


def check(mid: int, only: list[dict] | None = None) -> dict:
    """查這張圖有沒有更高清的版本，寫進 upgrade 表。only：只查這幾篇貼文（手動貼網址）。"""
    m = _local(mid)
    if not m or m["kind"] != "image":
        return {"status": "skip", "reason": "不是圖片"}
    lw, lh = _true_local(m)
    if not (lw and lh):
        return {"status": "skip", "reason": "還沒標記完（不知道尺寸）"}
    rs = only if only is not None else refs(mid)
    cands, errors, seen = [], [], []
    for ref in rs:
        try:
            fs = files(ref)
        except Exception as e:  # noqa  一個網站讀不到不影響其他的
            log.info("找高清版 %s %s:%s 失敗：%s", mid, ref["site"], ref["id"], e)
            errors.append(f"{SITE_NAME.get(ref['site'], ref['site'])}：{str(e)[:80]}")
            continue
        for f in fs:
            seen.append({"site": f["site"], "w": f.get("w"), "h": f.get("h"), "post_url": f["post_url"]})
            if better(f, lw, lh):
                cands.append(f)
    cands.sort(key=_rank)
    cands = cands[:6]
    c = db.conn()
    with db.write_lock:
        prev = c.execute("SELECT * FROM upgrade WHERE media_id=?", (mid,)).fetchone()
        best = cands[0] if cands else None
        if prev and prev["status"] == "dismissed" and only is None and (
                not best or best["w"] * best["h"] <= (prev["width"] or 0) * (prev["height"] or 0)):
            status = "dismissed"           # 你說過不用換：同樣（或更小）的不再提醒
        elif best:
            status = "offer"
        elif prev and prev["status"] == "done":
            status = "done"                # 換過了，現在已經是最大的
        else:
            status = "none"
        keep_best = status == "dismissed" or (status == "done" and not best)
        c.execute("""INSERT INTO upgrade(media_id, status, site, post_url, width, height, cands, local_w, local_h, checked_at, error)
                     VALUES(?,?,?,?,?,?,?,?,?,?,?)
                     ON CONFLICT(media_id) DO UPDATE SET status=excluded.status, cands=excluded.cands, local_w=excluded.local_w,
                       local_h=excluded.local_h, checked_at=excluded.checked_at, error=excluded.error,
                       site=CASE WHEN ? THEN upgrade.site ELSE excluded.site END,
                       post_url=CASE WHEN ? THEN upgrade.post_url ELSE excluded.post_url END,
                       width=CASE WHEN ? THEN upgrade.width ELSE excluded.width END,
                       height=CASE WHEN ? THEN upgrade.height ELSE excluded.height END""",
                  (mid, status, best and best["site"], best and best["post_url"], best and best["w"], best and best["h"],
                   json.dumps(cands, ensure_ascii=False), lw, lh, time.time(), "；".join(errors)[:300] or None,
                   keep_best, keep_best, keep_best, keep_best))
    return {"status": status, "best": _pub(best), "local": [lw, lh], "seen": seen, "errors": errors, "refs": len(rs)}


def _pub(f: dict | None) -> dict | None:
    if not f:
        return None
    return {"site": f["site"], "site_name": SITE_NAME.get(f["site"], f["site"]), "post_url": f["post_url"], "w": f["w"], "h": f["h"],
            "ext": f.get("ext")}


def check_url(mid: int, url: str) -> dict:
    """你在大圖貼的貼文網址：只查那一篇。"""
    from . import importer
    p = importer.parse(url)
    if not p:
        raise UpgradeError("只接受 X、Pixiv、Danbooru、e621、Rule34 的貼文網址")
    ref = {"site": p["site"], "id": str(p["id"])}
    if p.get("index"):
        ref["index"] = p["index"]
    res = check(mid, only=[ref])
    if res["status"] in ("skip",):
        raise UpgradeError(res["reason"])
    if res["errors"] and not res["seen"]:
        raise UpgradeError("讀不到這篇貼文：" + res["errors"][0])
    if not res["seen"]:
        raise UpgradeError("這篇貼文沒有可以用的圖（動圖、影片不算）")
    return res


def info(mid: int) -> dict | None:
    r = db.conn().execute("SELECT * FROM upgrade WHERE media_id=?", (mid,)).fetchone()
    if not r:
        return None
    d = {k: r[k] for k in ("status", "site", "post_url", "width", "height", "local_w", "local_h", "checked_at", "error",
                           "old_path", "old_w", "old_h", "done_at")}
    d["site_name"] = SITE_NAME.get(r["site"] or "", r["site"])
    d["n_cands"] = len(json.loads(r["cands"] or "[]"))
    return d


def dismiss(ids: list[int]) -> int:
    """「不用換高清」（可以撤銷）。"""
    ids = [int(i) for i in ids]
    with db.write_lock:
        c = db.conn()
        prev = []
        for i in range(0, len(ids), 800):
            ch = ids[i:i + 800]
            prev += [[r[0], r[1]] for r in c.execute(
                f"SELECT media_id, status FROM upgrade WHERE status IN ('offer','failed') AND media_id IN ({','.join('?' * len(ch))})", ch)]
        c.executemany("UPDATE upgrade SET status='dismissed' WHERE media_id=?", [(m,) for m, _ in prev])
        if prev:
            db.push_action(f"不用換高清（{len(prev)} 張）", {"upgrade_status": prev})
    return len(prev)


def summary() -> dict:
    c = db.conn()
    by = {r[0]: r[1] for r in c.execute("""SELECT u.status, COUNT(*) FROM upgrade u JOIN media m ON m.id=u.media_id
                                            WHERE m.status NOT IN ('missing','trashed')
                                              AND m.id NOT IN (SELECT media_id FROM album_items) GROUP BY u.status""")}
    return {"offer": by.get("offer", 0), "done": by.get("done", 0), "failed": by.get("failed", 0),
            "dismissed": by.get("dismissed", 0), "none": by.get("none", 0), "unchecked": len(unchecked_ids())}


def unchecked_ids() -> list[int]:
    """有出處、但還沒查過高清版（或出處在上次查之後更新過）的圖。"""
    return [r[0] for r in db.conn().execute(
        """SELECT m.id FROM media m JOIN media_source s ON s.media_id=m.id AND s.status='found'
           LEFT JOIN upgrade u ON u.media_id=m.id
           WHERE m.kind='image' AND m.status='done' AND (u.media_id IS NULL OR u.checked_at < s.fetched_at)
             AND m.id NOT IN (SELECT media_id FROM album_items)      -- 本子裡的頁不找高清版（b92）
           ORDER BY m.id DESC""")]


# ---------- 搜源找到出處時：排進背景檢查 ----------
_pending: set[int] = set()
_plock = threading.Lock()
_thread: threading.Thread | None = None
FLUSH_SEC = 10.0


def schedule(mid: int):
    """搜源找到出處時呼叫（不連網、不等）：記下來，每 10 秒合成一組排進線上佇列。"""
    global _thread
    s = db.get_settings()
    if not (s.get("online_enabled") and s.get("auto_upgrade", True)):
        return
    with _plock:
        _pending.add(int(mid))
        if _thread is None or not _thread.is_alive():
            _thread = threading.Thread(target=_flusher, daemon=True, name="upgrade-schedule")
            _thread.start()


def _flusher():
    while True:
        time.sleep(FLUSH_SEC)
        with _plock:
            ids = sorted(_pending)
            _pending.clear()
        if ids:
            try:
                from . import jobqueue, online
                online.get_jobs().submit("upgradecheck", ids, jobqueue.BACKGROUND)
            except Exception as e:  # noqa
                log.warning("排高清版檢查失敗：%s", e)


def queue_unchecked(ids: list[int] | None = None) -> int:
    """你按「補查」：全部（或指定的）有出處的圖排進線上佇列。"""
    from . import jobqueue, online
    ids = [int(i) for i in ids] if ids else unchecked_ids()
    if ids:
        online.get_jobs().submit("upgradecheck", ids, jobqueue.NORMAL, manual=True)
    return len(ids)


def in_album(ids) -> set[int]:
    ids = [int(i) for i in ids]
    out: set[int] = set()
    for i in range(0, len(ids), 800):
        ch = ids[i:i + 800]
        out |= {r[0] for r in db.conn().execute(
            f"SELECT media_id FROM album_items WHERE media_id IN ({','.join('?' * len(ch))})", ch)}
    return out


def run_checks(ids: list[int], state: dict, stop) -> None:
    """線上佇列的一小塊（jobqueue CHUNK）。本子裡的頁跳過（b92：排進去之後才收進本子的也一樣）。"""
    alb = in_album(ids)
    for mid in ids:
        if stop():
            break
        if int(mid) in alb:
            state["done"] = (state.get("done") or 0) + 1
            continue
        try:
            r = check(int(mid))
            if r.get("status") == "offer":
                state["found"] = state.get("found", 0) + 1
        except Exception as e:  # noqa
            log.info("找高清版 %s 失敗：%s", mid, e)
        state["done"] = (state.get("done") or 0) + 1


# ---------- 換 ----------
def _tmpdir() -> Path:
    d = db.DATA_DIR / "upgrade_tmp"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _image_size(path: Path) -> tuple[int, int]:
    from . import media
    return media.image_size(path)


def _true_local(m) -> tuple[int, int]:
    """這張圖現在真正多大：讀檔頭（資料庫的尺寸以前會是一半，見 media.image_size）。讀不到才用資料庫的。"""
    try:
        if m["path"] and os.path.isfile(m["path"]):
            return _image_size(Path(m["path"]))
    except Exception:  # noqa
        pass
    return m["width"], m["height"]


def _same_picture(mid: int, new: Path) -> tuple[bool, float | None, str]:
    """新下載的圖跟你的是不是同一張（逐像素，跟重複圖同一個標準）。"""
    from . import dupverify, media
    thumb = media.thumb_path(mid)
    A = dupverify.load(thumb) if thumb.exists() else None
    if A is None:
        m = _local(mid)
        A = dupverify.load(m["path"]) if m else None
    tmp = new.with_name(new.stem + "_cmp.webp")
    try:
        img = media.to_rgb(media.load_image(new)).copy()
        from PIL import Image
        img.thumbnail((media.THUMB_SIZE, media.THUMB_SIZE), Image.LANCZOS)
        img.save(tmp, "WEBP", quality=82, method=4)
        B = dupverify.load(tmp)
    finally:
        tmp.unlink(missing_ok=True)
    if A is None or B is None:
        return False, None, "讀不到圖，沒辦法比對"
    ok, score = dupverify.same(A, B)
    return ok, score, "" if ok else f"不是同一張（差異 {score:.0f}，可能是差分或改過的版本）"


def _final_path(old: str, ext: str) -> str:
    d, name = os.path.split(old)
    stem = os.path.splitext(name)[0]
    final = os.path.join(d, stem + "." + ext)
    if os.path.normcase(final) == os.path.normcase(old) or not os.path.exists(final):
        return final
    i = 1
    while True:
        f = os.path.join(d, f"{stem} (高清{'' if i == 1 else i}).{ext}")
        if not os.path.exists(f):
            return f
        i += 1


def replace(mid: int) -> dict:
    """把這張換成高清版。成功回傳 {ok, path, w, h, site}；失敗丟 UpgradeError（紀錄寫成 failed）。"""
    from . import archive, importer, media, pipeline
    from send2trash import send2trash
    m = _local(mid)
    if not m:
        raise UpgradeError("找不到這張圖")
    c = db.conn()
    up = c.execute("SELECT * FROM upgrade WHERE media_id=?", (mid,)).fetchone()
    if not up or up["status"] not in ("offer", "failed") or not json.loads(up["cands"] or "[]"):
        raise UpgradeError("這張沒有可以換的高清版")
    if m["status"] in ("missing", "trashed") or not os.path.isfile(m["path"]):
        raise UpgradeError("原檔不在了")
    if archive.is_icloud(m["path"]):
        raise UpgradeError("iCloud 照片資料夾裡的圖請先「移出 iCloud」再換（不然 iPad 上的照片也會被換掉）")
    cands = json.loads(up["cands"])
    lw, lh = _true_local(m)          # 換之前讀一次真正的尺寸（舊檔等一下會丟進回收筒）
    tmp, got, errors = None, None, []
    try:
        for f in cands[:MAX_TRY]:
            try:
                if not f.get("url"):         # Danbooru：搜源時只知道尺寸，現在才問原檔網址
                    ref = {"site": f["site"], "id": f["post_url"].rstrip("/").rsplit("/", 1)[-1]}
                    full = [x for x in files(ref, need_url=True) if x.get("url")]
                    if not full:
                        raise UpgradeError("網站沒有提供原檔")
                    f = {**f, **full[0]}
                tmp = _tmpdir() / f"{mid}.{f['ext'] or _ext(f['url'])}"
                tmp.unlink(missing_ok=True)
                importer._download(f["url"], tmp, f.get("headers"), f["site"])
                w, h = _image_size(tmp)
                if not better({"w": w, "h": h}, lw, lh):
                    raise UpgradeError(f"下載下來是 {w}×{h}，沒有比較大")
                ok, _score, why = _same_picture(mid, tmp)
                if not ok:
                    raise UpgradeError(why)
                got = {**f, "w": w, "h": h, "ext": f.get("ext") or _ext(f["url"])}
                break
            except Exception as e:  # noqa  這個不行就試下一個
                errors.append(f"{SITE_NAME.get(f['site'], f['site'])}：{getattr(e, 'args', [e])[0] if isinstance(e, UpgradeError) else e}")
                if tmp is not None:
                    tmp.unlink(missing_ok=True)
                tmp = None
        if not got:
            raise UpgradeError("；".join(errors)[:300] or "沒有可以換的")
        new_fp = media.fingerprint(str(tmp), "image")
        dup = c.execute("SELECT id FROM media WHERE fp=? AND id<>? AND status NOT IN ('missing','trashed')", (new_fp, mid)).fetchone()
        if dup:
            raise UpgradeError(f"圖庫裡已經有這張高清版（#{dup[0]}），可以到「重複的圖」處理")

        old = m["path"]
        st_old = os.stat(old)
        final = _final_path(old, "jpg" if got["ext"] == "jpeg" and old.lower().endswith(".jpg") else got["ext"])
        same_name = os.path.normcase(final) == os.path.normcase(old)
        if same_name:                       # 同檔名：先把舊的丟回收筒才放得進去
            try:
                send2trash(old)
            except Exception as e:  # noqa
                raise UpgradeError(f"舊檔丟不進資源回收筒：{e}")
            try:
                shutil.move(str(tmp), final)
            except Exception as e:  # noqa
                keep = _tmpdir() / f"未放回_{mid}_{os.path.basename(final)}"
                shutil.move(str(tmp), keep)
                log.warning("換高清：舊檔已丟回收筒，但新檔放不進去 %s：%s（新檔在 %s）", final, e, keep)
                raise UpgradeError(f"舊檔已在資源回收筒，但新檔放不進資料夾（{e}）；新檔在 {keep}")
        else:
            shutil.move(str(tmp), final)
        tmp = None
        os.utime(final, (st_old.st_atime, st_old.st_mtime))      # 日期沿用舊檔
        st = os.stat(final)
        with db.write_lock:
            c.execute("""UPDATE media SET path=?, size=?, mtime=?, fp=?, width=?, height=?, status='pending', error=NULL
                         WHERE id=?""", (os.path.normpath(final), st.st_size, st.st_mtime, new_fp, got["w"], got["h"], mid))
            c.execute("UPDATE OR IGNORE import_meta SET path=? WHERE path=?", (os.path.normpath(final), os.path.normpath(old)))
            c.execute("""UPDATE upgrade SET status='done', site=?, post_url=?, width=?, height=?, error=NULL, old_path=?, old_fp=?,
                         old_w=?, old_h=?, old_size=?, done_at=? WHERE media_id=?""",
                      (got["site"], got["post_url"], got["w"], got["h"], old, m["fp"], lw, lh, m["size"],
                       time.time(), mid))
            db.log_op(f"換成高清（{SITE_NAME.get(got['site'], got['site'])}）", [mid], keys="upgrade")
        old_left = None
        if not same_name:
            try:
                send2trash(old)
            except Exception as e:  # noqa
                old_left = str(e)
                log.warning("換高清：新檔放好了，但舊檔丟不進資源回收筒 %s：%s", old, e)
        for p in (media.thumb_path(mid, large=True), db.DATA_DIR / "preview" / f"{mid}.jpg"):
            p.unlink(missing_ok=True)
        pipeline.dup_cache.clear()
        log.info("換高清：%s %s×%s → %s×%s（%s）", mid, lw, lh, got["w"], got["h"], got["site"])
        return {"ok": True, "id": mid, "path": final, "w": got["w"], "h": got["h"], "site": got["site"],
                "site_name": SITE_NAME.get(got["site"], got["site"]), "old": [lw, lh], "old_left": old_left}
    except UpgradeError as e:
        with db.write_lock:
            c.execute("UPDATE upgrade SET status='failed', error=? WHERE media_id=?", (str(e)[:300], mid))
        raise
    finally:
        if tmp is not None:
            tmp.unlink(missing_ok=True)


def run(jobs, arg: dict):
    """背景工作（pipeline Jobs，kind=upgrade）：arg = {"ids": [...]}。換完重新標記（縮圖、特徵）。"""
    from . import pipeline
    ids = [int(i) for i in arg.get("ids") or []]
    res = {"done": 0, "failed": [], "at": None}
    t0 = time.time()
    jobs.state.update(phase="換成高清版", done=0, total=len(ids))
    for i, mid in enumerate(ids):
        if not jobs.checkpoint():
            break
        try:
            replace(mid)
            res["done"] += 1
        except Exception as e:  # noqa
            res["failed"].append({"id": mid, "error": str(e)[:200]})
            if not isinstance(e, UpgradeError):
                log.warning("換高清 %s 失敗：%s", mid, e)
        jobs.progress(i + 1, t0=t0)
    res["at"] = time.time()
    jobs.state["upgrade"] = res
    if res["done"]:
        pipeline.process_pending(jobs)

# ---------- 從出處救回（b97.2） ----------
RESTORE_TRY = 8          # 一張最多下載幾個候選（X 多張、Pixiv 多頁時要一個一個比）


def restore_missing(mid: int) -> dict:
    """「找不到檔案」的圖，搜源找到過出處：從出處重新下載原檔，放回原本的資料夾，接回這筆紀錄（標籤、出處都在）。
    怎麼知道是同一張：跟程式留著的縮圖逐像素比（跟換高清同一個標準）；長寬比要一樣。
    起因：同一個檔案被記成兩筆（副檔名大小寫不同），在「重複的圖」刪掉一筆時檔案就真的沒了，資源回收筒也清了。"""
    from . import importer, media, pipeline
    m = _local(mid)
    if not m:
        raise UpgradeError("找不到這筆紀錄")
    if m["status"] != "missing":
        raise UpgradeError("這張不是「找不到檔案」")
    if os.path.isfile(m["path"]):
        raise UpgradeError("檔案已經在了，重新掃描就會接回")
    if not db.conn().execute("SELECT 1 FROM media_source WHERE media_id=? AND status='found'", (mid,)).fetchone():
        raise UpgradeError("不知道出處，沒辦法重新下載")
    folder = os.path.dirname(m["path"])
    if not os.path.isdir(folder):
        raise UpgradeError(f"原本的資料夾不在了：{folder}")
    if not media.thumb_path(mid).exists():
        raise UpgradeError("沒有縮圖可以比對，沒辦法確定是同一張")
    lw, lh = m["width"] or 0, m["height"] or 0
    cands, errors = [], []
    for ref in refs(mid):
        try:
            cands += [f for f in files(ref, need_url=True) if f.get("url")]
        except Exception as e:  # noqa
            errors.append(f"{SITE_NAME.get(ref['site'], ref['site'])}：{e}")
    if not cands:
        raise UpgradeError("出處上拿不到原檔" + (f"（{'；'.join(errors)[:200]}）" if errors else ""))

    def aspect_ok(f):
        w, h = f.get("w") or 0, f.get("h") or 0
        return not (w and h and lw and lh) or abs(w / h - lw / lh) <= ASPECT * (lw / lh)
    cands = sorted([f for f in cands if aspect_ok(f)], key=_rank)
    tmp = None
    try:
        for f in cands[:RESTORE_TRY]:
            ext = f.get("ext") or _ext(f["url"])
            tmp = _tmpdir() / f"restore_{mid}.{ext}"
            tmp.unlink(missing_ok=True)
            try:
                importer._download(f["url"], tmp, f.get("headers"), f["site"])
                ok, _score, why = _same_picture(mid, tmp)
                if ok:
                    break
                errors.append(f"{SITE_NAME.get(f['site'], f['site'])}：{why}")
            except Exception as e:  # noqa
                errors.append(f"{SITE_NAME.get(f['site'], f['site'])}：{getattr(e, 'args', [e])[0]}")
            tmp.unlink(missing_ok=True)
            tmp = None
        if tmp is None:
            raise UpgradeError("；".join(errors)[:300] or "出處上的圖都對不上")
        stem = os.path.splitext(os.path.basename(m["path"]))[0]
        ext = "jpg" if ext == "jpeg" else ext
        final = os.path.join(folder, f"{stem}.{ext}")
        i = 1
        while os.path.exists(final):          # Windows 不分大小寫：同名（只差大小寫）也算有了
            final = os.path.join(folder, f"{stem} ({i}).{ext}")
            i += 1
        shutil.move(str(tmp), final)
        tmp = None
        if m["mtime"]:
            os.utime(final, (m["mtime"], m["mtime"]))            # 日期沿用原本的（圖庫依日期排）
        w, h = _image_size(Path(final))
        st = os.stat(final)
        fp = media.fingerprint(final, "image")
        c = db.conn()
        # 同一個檔案的另一筆（只差大小寫、在回收筒或找不到）：先併進這筆（不然路徑撞到）
        keys = {pipeline.pkey(final), pipeline.pkey(m["path"])}
        twins = [r[0] for r in c.execute("SELECT id, path FROM media WHERE id<>? AND status IN ('missing','trashed')", (mid,))
                 if pipeline.pkey(r[1]) in keys]
        if twins:
            pipeline.merge_records(mid, twins)
        with db.write_lock:
            c.execute("""UPDATE media SET path=?, size=?, mtime=?, fp=?, width=?, height=?, status='pending', error=NULL
                         WHERE id=?""", (os.path.normpath(final), st.st_size, st.st_mtime, fp, w, h, mid))
            db.log_op(f"從出處救回（{SITE_NAME.get(f['site'], f['site'])}）", [mid], keys="restore")
        for p in (media.thumb_path(mid, large=True), db.DATA_DIR / "preview" / f"{mid}.jpg"):
            p.unlink(missing_ok=True)
        pipeline.dup_cache.clear()
        log.info("從出處救回：%s → %s（%s，%s×%s）", mid, final, f["site"], w, h)
        return {"ok": True, "id": mid, "path": final, "site": f["site"], "post_url": f.get("post_url"), "w": w, "h": h,
                "old": [lw, lh]}
    finally:
        if tmp is not None:
            tmp.unlink(missing_ok=True)


def restorable_ids() -> list[int]:
    """找不到檔案、但搜源找到過出處的圖。"""
    return [r[0] for r in db.conn().execute("""SELECT m.id FROM media m JOIN media_source s ON s.media_id=m.id
        WHERE m.status='missing' AND s.status='found' ORDER BY m.id""")]


def restore_run(ids: list[int], state: dict, stop=lambda: False) -> None:
    """背景工作：一張一張救；結果放 state["results"]。"""
    from . import pipeline
    state.update(done=0, total=len(ids), results=[])
    for mid in ids:
        if stop():
            break
        try:
            r = restore_missing(mid)
        except Exception as e:  # noqa
            r = {"ok": False, "id": mid, "error": str(getattr(e, "args", [e])[0])[:300]}
        state["results"].append(r)
        state["done"] += 1
    ok = [r["id"] for r in state["results"] if r.get("ok")]
    if ok:
        pipeline.get_jobs().submit("retag", ok)       # 重做縮圖、特徵；你確認過的標籤不動
