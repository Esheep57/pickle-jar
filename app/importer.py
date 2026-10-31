"""從網站直接匯入：貼上貼文網址（或瀏覽器上的「存到圖庫」按鈕）→ 下載原圖到 匯入/<網站>/ → 走一般的處理流程。

只接受這幾個網站的貼文網址（不會替任何網址下載東西）：
- X（twitter.com / x.com）：用 FxTwitter 的公開 API 拿圖片網址和作者，不用登入 X
- Pixiv：作品的原圖（R-18 需要在設定填 Pixiv 登入 cookie）
- Danbooru / e621 / Rule34：貼文的原檔
- 其他網站：交給 gallery-dl（app/gdl.py；b84），只接 gallery-dl 有專屬 extractor 的網站

檔名刻意取成現有流程認得的樣子，匯入後的比對和一般的圖完全一樣（不另外走捷徑）：
- Danbooru：原檔 MD5 當檔名 → Danbooru 原檔比對，直接確認
- Pixiv：<作品ID>_p<頁>  → 用 Pixiv ID 找 Danbooru、Pixiv 作者
- X：x_<推文ID>_<圖片 key> → 用圖片 key／推文 ID 找 Danbooru
- e621 / Rule34：處理完用 MD5 查回原貼文
另外記下「這張是誰貼的」：X 的 @帳號會拿去對 Danbooru 登記的畫師（對不上就不建立新畫師）。
"""
from __future__ import annotations

import json
import logging
import os
import re
import secrets
import threading
import time
from pathlib import Path

from . import db

log = logging.getLogger("importer")
SCHEMA = """CREATE TABLE IF NOT EXISTS import_meta(path TEXT PRIMARY KEY, site TEXT, post_url TEXT, data TEXT,
            created_at REAL, applied INTEGER DEFAULT 0)"""
FX_API = os.environ.get("LIB_FXTWITTER", "https://api.fxtwitter.com")
MAX_BYTES = 300 * 1024 * 1024
SITE_DIR = {"x": "x", "pixiv": "pixiv", "danbooru": "danbooru", "e621": "e621", "r34": "rule34",
            "ehentai": "e-hentai", "nhentai": "nhentai"}

_URL = [
    ("x", re.compile(r"^https?://(?:www\.|mobile\.)?(?:twitter|x|fxtwitter|vxtwitter|fixupx|fixvx)\.com/([A-Za-z0-9_]{1,15})/status/(\d{5,25})(?:/(?:photo|video)/(\d))?", re.I)),
    ("pixiv", re.compile(r"^https?://(?:www\.)?pixiv\.net/(?:[a-z]{2}/)?artworks/(\d{3,12})", re.I)),
    ("pixiv", re.compile(r"^https?://(?:www\.)?pixiv\.net/member_illust\.php\?.*?illust_id=(\d{3,12})", re.I)),
    # 圖片網址（右鍵「複製圖片網址」）：只存那一頁。原圖、master、縮圖都認得；新作品檔名有雜湊
    ("pixiv", re.compile(r"^https?://i\.pximg\.net/.*?/(\d{3,12})(?:-[0-9a-f]{32})?_p(\d+)", re.I)),
    ("danbooru", re.compile(r"^https?://(?:danbooru|safebooru)\.donmai\.us/posts/(\d+)", re.I)),
    ("ehentai", re.compile(r"^https?://(?:e-|ex|g\.e-)hentai\.org/g/(\d+)/([0-9a-f]{10})", re.I)),
    ("nhentai", re.compile(r"^https?://(?:www\.)?nhentai\.net/g/(\d+)", re.I)),
    ("e621", re.compile(r"^https?://(?:e621|e926)\.net/posts/(\d+)", re.I)),
    ("r34", re.compile(r"^https?://(?:www\.)?rule34\.xxx/index\.php\?(?=.*[?&]?page=post)(?=.*[?&]id=(\d+))", re.I)),
]


class ImportError_(Exception):
    pass


def _ensure():
    with db.write_lock:
        db.conn().execute(SCHEMA)


def parse(url: str) -> dict | None:
    """網址 → {site, id, ...}；不是支援的貼文網址就回傳 None。"""
    url = (url or "").strip()
    for site, rx in _URL:
        m = rx.search(url)
        if m:
            if site == "x":
                return {"site": "x", "user": m.group(1), "id": m.group(2), "index": int(m.group(3)) if m.group(3) else None,
                        "url": f"https://x.com/{m.group(1)}/status/{m.group(2)}"}
            if site == "ehentai":
                return {"site": "ehentai", "id": m.group(1), "token": m.group(2).lower(),
                        "url": f"https://e-hentai.org/g/{m.group(1)}/{m.group(2).lower()}/"}
            if site == "nhentai":
                return {"site": "nhentai", "id": m.group(1), "url": f"https://nhentai.net/g/{m.group(1)}/"}
            pid = m.group(1)
            if site == "pixiv" and m.lastindex and m.lastindex >= 2:
                return {"site": "pixiv", "id": pid, "pages": [int(m.group(2))], "url": f"https://www.pixiv.net/artworks/{pid}"}
            return {"site": site, "id": pid, "url": {"pixiv": f"https://www.pixiv.net/artworks/{pid}",
                                                     "danbooru": f"https://danbooru.donmai.us/posts/{pid}",
                                                     "e621": f"https://e621.net/posts/{pid}",
                                                     "r34": f"https://rule34.xxx/index.php?page=post&s=view&id={pid}"}[site]}
    return None


# ---------- 各網站：列出要下載的檔案 ----------
def _items_x(p: dict) -> tuple[list[dict], dict]:
    from .httpclient import _req
    r = _req("fxtwitter", "GET", f"{FX_API}/status/{p['id']}")
    if r.status_code == 404:
        raise ImportError_("找不到這則貼文（可能被刪除或設為私人）")
    if r.status_code >= 400:
        raise ImportError_(f"讀不到貼文（FxTwitter HTTP {r.status_code}）")
    t = (r.json() or {}).get("tweet") or {}
    media = (t.get("media") or {}).get("all") or []
    if not media:
        raise ImportError_("這則貼文沒有圖片或影片")
    handle = ((t.get("author") or {}).get("screen_name") or p["user"])
    items = []
    for i, m in enumerate(media, 1):
        if p["index"] and i != p["index"]:
            continue
        u = m.get("url") or ""
        if m.get("type") == "photo":
            key = re.search(r"/media/([A-Za-z0-9_-]{15})", u)
            ext = (re.search(r"\.(jpe?g|png|webp)", u) or [None, "jpg"])[1]
            base = u.split("?")[0]
            items.append({"url": base + "?name=orig", "name": f"x_{p['id']}_{key.group(1) if key else i}.{ext}"})
        elif m.get("type") in ("video", "gif") and u:
            items.append({"url": u, "name": f"x_{p['id']}_{i}.mp4"})
    from . import sitetags
    meta = {"handle": handle, "author": (t.get("author") or {}).get("name"), "text": (t.get("text") or "")[:300],
            "hashtags": sitetags.hashtags(t.get("text") or ""),
            "nsfw": bool(t.get("possibly_sensitive")), "tweet_id": p["id"], "post_files": len(media)}
    return items, meta


def _items_pixiv(p: dict) -> tuple[list[dict], dict]:
    from .httpclient import BROWSER_UA, NotFound, _req
    from .pixiv import PIXIV, fetch_pixiv
    s = db.get_settings()
    try:
        info = fetch_pixiv(int(p["id"]))
    except NotFound as e:
        raise ImportError_(f"Pixiv：{e}（R-18 作品需要在設定填 Pixiv 登入 cookie）")
    headers = {"User-Agent": BROWSER_UA, "Referer": "https://www.pixiv.net/"}
    cookies = {"PHPSESSID": s["pixiv_phpsessid"]} if s.get("pixiv_phpsessid") else None
    r = _req("pixiv", "GET", f"{PIXIV}/ajax/illust/{p['id']}/pages", headers=headers, cookies=cookies)
    body = (r.json() or {}).get("body") or []
    want = set(p.get("pages") or [])          # 只存其中幾頁（0 起算，跟檔名 _p0 一樣）；沒指定 = 整篇
    if want and not any(i in want for i in range(len(body))):
        raise ImportError_(f"這篇作品只有 {len(body)} 頁，沒有第 {min(want) + 1} 頁" if body else "Pixiv 沒有回傳這篇作品的頁面")
    items = []
    for i, pg in enumerate(body):
        if want and i not in want:
            continue
        u = (pg.get("urls") or {}).get("original")
        if u:
            # 新作品的網址有雜湊（146228286-5554…_p0.jpg）：存成一般的 146228286_p0.jpg，檔名一看就知道是哪個作品
            name = re.sub(r"-[0-9a-f]{32}(?=_p\d+)", "", u.rsplit("/", 1)[-1])
            items.append({"url": u, "name": name, "headers": {"Referer": "https://www.pixiv.net/"}})
    if not items:
        raise ImportError_("Pixiv 沒有回傳原圖網址（可能需要登入）")
    return items, {"author": info.get("user"), "user_id": info.get("user_id"), "nsfw": info.get("x_restrict", 0) > 0,
                   "title": info.get("title"), "ai": info.get("ai"), "post_files": len(body), "illust_type": info.get("illust_type", 0),
                   "date": info.get("date")}


def _items_danbooru(p: dict) -> tuple[list[dict], dict]:
    from .danbooru import dan_get
    post = dan_get(f"/posts/{p['id']}.json")
    u, md5, ext = post.get("file_url"), post.get("md5"), post.get("file_ext")
    if not (u and md5):
        raise ImportError_("Danbooru 沒有提供原檔（可能需要帳號權限）")
    return [{"url": u, "name": f"{md5}.{ext}"}], {"nsfw": post.get("rating") in ("q", "e"), "md5": md5}


def _items_e621(p: dict) -> tuple[list[dict], dict]:
    from . import e621
    d = e621._get(f"/posts/{p['id']}.json", {})
    post = (d or {}).get("post") or {}
    f = post.get("file") or {}
    if not f.get("url"):
        raise ImportError_("e621 沒有提供原檔網址（部分內容要登入 e621 帳號才看得到）")
    return [{"url": f["url"], "name": f"e621_{p['id']}.{f.get('ext') or 'jpg'}"}], {"nsfw": post.get("rating") != "s", "md5": f.get("md5")}


def _items_r34(p: dict) -> tuple[list[dict], dict]:
    from . import r34
    if not r34.configured():
        raise ImportError_("Rule34 需要先在設定填 API key")
    txt = r34._get({"s": "post", "q": "index", "json": 1, "id": p["id"]})
    try:
        d = json.loads(txt) if txt and txt not in ("[]", '""') else []
    except ValueError:
        d = []
    post = (d[0] if isinstance(d, list) and d else None) or {}
    u = post.get("file_url")
    if not u:
        raise ImportError_("Rule34 找不到這篇貼文")
    ext = u.rsplit(".", 1)[-1].split("?")[0]
    return [{"url": u, "name": f"r34_{p['id']}.{ext}"}], {"nsfw": post.get("rating") != "general", "md5": post.get("hash")}


ITEMS = {"x": _items_x, "pixiv": _items_pixiv, "danbooru": _items_danbooru, "e621": _items_e621, "r34": _items_r34}


# ---------- 下載 ----------
def dest_dir(site: str) -> Path:
    s = db.get_settings()
    if (s.get("import_dir") or "").strip():
        d = Path(s["import_dir"]) / _site_folder(site)
    else:
        root = next((r for r in s["roots"] if os.path.isdir(r)), None)
        if not root:
            raise ImportError_("還沒有設定圖庫資料夾")
        d = Path(root) / "匯入" / _site_folder(site)
    d.mkdir(parents=True, exist_ok=True)
    return d


def _site_folder(site: str) -> str:
    """網站 → 匯入資料夾裡的子資料夾名稱（gallery-dl 的網站：gdl:bluesky → bluesky）。"""
    return SITE_DIR.get(site) or _safe_name(site.split(":", 1)[-1]) or "other"


def _safe_name(name: str) -> str:
    name = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "_", name).strip(" .")
    return name[:150] or "image"


# Danbooru／e621 的圖片伺服器（Cloudflare）會擋「自稱是瀏覽器、但其實不是」的下載 → 用程式自己的名字。
# Pixiv／X 反過來需要像瀏覽器。第一個被拒（403/429/503）時換另一個再試一次。
_APP_UA_SITES = ("danbooru", "e621", "r34")


def _download(url: str, dst: Path, headers: dict | None = None, site: str = "", max_bytes: int = MAX_BYTES, tries: int = 3):
    """下載；網路斷掉（伺服器沒回應就斷線、逾時、連不上）等一下再試，最多 tries 次（有些網站偶爾
    「Server disconnected without sending a response」，整篇就失敗）。HTTP 錯誤、檔案太大不重試。"""
    import httpx
    for i in range(tries):
        try:
            return _download1(url, dst, headers, site, max_bytes)
        except (httpx.TransportError, httpx.TimeoutException) as e:
            if i == tries - 1:
                raise ImportError_(f"下載失敗（{type(e).__name__}，試了 {tries} 次）：{e}") from e
            log.info("下載斷線，%s 秒後再試：%s（%s）", 3 * (i + 1), url, e)
            time.sleep(3 * (i + 1))


def _download1(url: str, dst: Path, headers: dict | None = None, site: str = "", max_bytes: int = MAX_BYTES):
    from .httpclient import BROWSER_UA, UA, client
    tmp = dst.with_name(dst.name + ".part")
    uas = [UA, BROWSER_UA] if site in _APP_UA_SITES else [BROWSER_UA, UA]
    host = re.sub(r"^https?://([^/]+).*$", r"\1", url)
    last = None
    try:
        for ua in uas:
            n = 0
            with client().stream("GET", url, headers={**(headers or {}), "User-Agent": ua}, timeout=60) as r:
                if r.status_code in (403, 429, 503):
                    last = r.status_code
                    continue
                if r.status_code >= 400:
                    raise ImportError_(f"下載失敗（{host} 回 HTTP {r.status_code}）")
                with open(tmp, "wb") as f:
                    for chunk in r.iter_bytes(1 << 16):
                        n += len(chunk)
                        if n > max_bytes:
                            raise ImportError_(f"檔案太大（超過 {max_bytes >> 20} MB）")
                        f.write(chunk)
            os.replace(tmp, dst)
            return
        raise ImportError_(f"下載被 {host} 拒絕（HTTP {last}）")
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass


def want_album(site: str, meta: dict, n: int, album: bool | None = None) -> bool:
    """Pixiv 多頁作品要不要存成本子（b110.16）：這次指定的（瀏覽器腳本「存成本子」）> 設定（預設：Pixiv 標成漫畫的）。"""
    if site != "pixiv" or n < 2:
        return False
    if album is not None:
        return bool(album)
    rule = db.get_settings().get("pixiv_album", "manga")
    return rule == "multi" or (rule == "manga" and meta.get("illust_type") == 1)


def pixiv_album(url: str, meta: dict) -> int | None:
    """Pixiv 多頁作品 → 一本（b110.16）。這篇的頁都標記完才建（還有沒處理完的就等下一批）；已經有就補頁。
    頁序照 _p0、_p1…；整本的標籤：Pixiv 作者（Danbooru 登記的畫師，沒有就用 Pixiv 暱稱建立）。"""
    from . import albums
    c = db.conn()
    if c.execute("SELECT 1 FROM import_meta WHERE post_url=? AND applied=0", (url,)).fetchone():
        return None
    ids = {r[0]: r[1] for r in c.execute("""SELECT m.id, m.path FROM import_meta i JOIN media m ON m.path=i.path
            WHERE i.post_url=? AND m.status NOT IN ('trashed','missing')""", (url,))}
    for mid in meta.get("album_existing") or []:
        r = c.execute("SELECT path FROM media WHERE id=? AND status NOT IN ('trashed','missing')", (mid,)).fetchone()
        if r:
            ids[mid] = r[0]
    if len(ids) < 2 and not c.execute("SELECT 1 FROM albums WHERE source_url=?", (url,)).fetchone():
        return None
    page = lambda p: int(m.group(1)) if (m := re.search(r"_p(\d+)\.\w+$", os.path.basename(p))) else 0
    ordered = [mid for mid, _ in sorted(ids.items(), key=lambda kv: (page(kv[1]), kv[1]))]
    a = c.execute("SELECT id FROM albums WHERE source_url=?", (url,)).fetchone()
    if a:
        albums.add_items(a[0], ordered)
        albums.reorder(a[0], [r[0] for r in c.execute("SELECT media_id FROM album_items WHERE album_id=?", (a[0],))
                              if r[0] not in ordered] + ordered)
        return a[0]
    tags = []
    try:
        from .pixiv import resolve_pixiv_artist
        artist = resolve_pixiv_artist(meta.get("user_id"), meta.get("author"))
        if artist:
            tags.append({"name": artist, "category": "artist"})
    except Exception as e:  # noqa
        log.info("Pixiv 本子的作者標籤失敗 %s：%s", url, e)
    title = meta.get("title") or url.rsplit("/", 1)[-1]
    note = "Pixiv" + (f"：{meta['author']}" if meta.get("author") else "") + (f"（{meta['date']}）" if meta.get("date") else "")
    aid = albums.create(title, ordered, "comic" if meta.get("illust_type") == 1 else "artbook", tags, source_url=url,
                        note=note, keep_order=True)
    first = c.execute("SELECT path FROM media WHERE id=?", (ordered[0],)).fetchone()[0]
    with db.write_lock:
        c.execute("UPDATE albums SET folder=? WHERE id=?", (os.path.dirname(first), aid))
        c.commit()
    return aid


def import_url(url: str, pages: list[int] | None = None, ext: dict | None = None, album: bool | None = None) -> dict:
    """匯入一篇貼文。pages：Pixiv 只存其中幾頁（0 起算）。album：Pixiv 多頁存成本子（None＝照設定）。
    回傳 {site, post_url, saved:[路徑], existing:[media id], ...}。"""
    _ensure()
    p = parse(url)
    if not p:
        from . import gdl
        if gdl.match(url):         # 其他網站：gallery-dl 認得就交給它（背景下載）
            try:
                j = gdl.start(url.strip())
            except gdl.GdlError as e:
                raise ImportError_(str(e))
            return {"queued": True, "kind": "gdl", "site": "gdl:" + j["site"], "label": j["label"], "post_url": j["url"],
                    "title": j["label"], "count": j["limit"] or 0, "single": j["single"], "limit": j["limit"]}
        raise ImportError_("不支援的網址：X、Pixiv、Danbooru、e621、Rule34、E-Hentai、nhentai 以外的網站要 gallery-dl 認得"
                           + ("" if gdl.available() else "（還沒安裝 gallery-dl）"))
    from . import galleries
    if p["site"] in galleries.SITES:            # 整本：背景下載（幾十到幾百頁），完成後自動建立作品集
        return galleries.start(p, ext)
    if pages:
        if p["site"] != "pixiv":
            raise ImportError_("只有 Pixiv 能選頁")
        p["pages"] = sorted({int(x) for x in pages if 0 <= int(x) < 1000})
    items, meta = ITEMS[p["site"]](p)
    if want_album(p["site"], meta, len(items), album):
        meta["album"] = True
    base = dest_dir(p["site"])
    # 一篇有好幾個檔案（Pixiv 多頁、X 多張）：收在這篇自己的小資料夾
    d = base / post_folder(p, meta) if (meta.get("post_files") or len(items)) > 1 else base
    c = db.conn()
    saved, existing = [], []
    for it in items:
        dst = d / _safe_name(it["name"])
        old = base / _safe_name(it["name"])       # 以前沒有分資料夾時存的位置
        # 丟進回收筒、找不到的紀錄不算（檔案不在了：重新下載，掃描時接回原本的紀錄）
        row = c.execute("SELECT id FROM media WHERE path IN (?, ?) AND status NOT IN ('trashed','missing')", (str(dst), str(old))).fetchone()
        if row or dst.exists() or old.exists():
            existing.append(row[0] if row else str(dst if dst.exists() else old))
            _remember_url(row[0] if row else None, p, meta)
            continue
        if meta.get("md5"):
            dup = c.execute("SELECT id FROM media WHERE fp=?", (meta["md5"],)).fetchone()
            if dup:                                         # 同一個檔案已經在圖庫（別的資料夾）
                existing.append(dup[0])
                _remember_url(dup[0], p, meta)
                continue
        d.mkdir(parents=True, exist_ok=True)
        try:
            _download(it["url"], dst, it.get("headers"), p["site"], it.get("max") or MAX_BYTES)
        except Exception:
            if saved:           # 下載到一半出錯：已經下載的照樣記下（不然它們變成沒有出處的圖，也進不了本子），再回報錯誤
                _record(saved, p, meta)
            raise
        saved.append(str(dst))
    if existing:            # 本來就在圖庫的圖：馬上補上貼文作者（不用等標記，它們早就標記完了）
        after_processed()
    if meta.get("album"):       # 本來就在圖庫的頁也進這本（等新的頁標記完一起整理；都是舊的就馬上建）
        meta["album_existing"] = [x for x in existing if isinstance(x, int)]
    if saved:
        _record(saved, p, meta)
    elif meta.get("album") and meta["album_existing"]:
        try:
            pixiv_album(p["url"], meta)
        except Exception as e:  # noqa
            log.warning("Pixiv 存成本子失敗 %s：%s", p["url"], e)
    return {"site": p["site"], "post_url": p["url"], "saved": saved, "existing": existing,
            "author": meta.get("handle") or meta.get("author"), "count": len(items), "album": bool(meta.get("album"))}


def _record(saved: list[str], p: dict, meta: dict):
    """記下這些檔案是從哪篇貼文匯入的，送去標記（跟監看到新檔一樣：標記 → 搜源／整理進本子）。"""
    c = db.conn()
    with db.write_lock:
        c.executemany("INSERT OR REPLACE INTO import_meta(path, site, post_url, data, created_at, applied) VALUES(?,?,?,?,?,0)",
                      [(os.path.normpath(s), p["site"], p["url"], json.dumps(meta, ensure_ascii=False), time.time()) for s in saved])
        c.commit()
    from . import pipeline
    pipeline.get_jobs().submit("scan", list(saved))


def post_folder(p: dict, meta: dict) -> str:
    """一篇多檔的貼文的小資料夾名稱：ID 在前（不會撞名、排序穩定），後面接標題或作者方便認。"""
    tail = meta.get("title") or meta.get("handle") or meta.get("author") or ""
    name = f"{p['id']} {tail}".strip() if p["site"] == "pixiv" else f"{p['id']} @{tail}" if p["site"] == "x" and tail else str(p["id"])
    return _safe_name(re.sub(r"\s+", " ", name))[:80].rstrip(" .") or str(p["id"])


def _remember_url(mid, p, meta: dict | None = None):
    """圖本來就在圖庫：補記貼文網址，也記下貼文的資訊（X 帳號…），讓 after_processed 補上作者。
    以前只記網址（data='{}'、applied=1）：從 X 匯入一張圖庫裡已經有的圖，作者永遠不會補上。
    已經有完整匯入紀錄的不動；只有網址、沒有資訊的（例如 ascii2d 找到時記的）會補上資訊。"""
    if not mid:
        return
    c = db.conn()
    r = c.execute("SELECT path FROM media WHERE id=?", (mid,)).fetchone()
    if not r:
        return
    data = json.dumps(meta or {}, ensure_ascii=False)
    with db.write_lock:
        c.execute("""INSERT INTO import_meta(path, site, post_url, data, created_at, applied) VALUES(?,?,?,?,?,?)
                     ON CONFLICT(path) DO UPDATE SET data=excluded.data, applied=0, site=excluded.site, post_url=excluded.post_url
                     WHERE (import_meta.data IS NULL OR import_meta.data IN ('', '{}')) AND excluded.data NOT IN ('', '{}')""",
                  (os.path.normpath(r[0]), p["site"], p["url"], data, time.time(), 0 if meta else 1))
        c.commit()


def post_url_of(path: str) -> dict | None:
    """這張圖是從哪篇貼文匯入的。檔案搬過家時用檔名找（匯入的檔名帶貼文 ID，不會撞名）。"""
    _ensure()
    c = db.conn()
    r = c.execute("SELECT site, post_url FROM import_meta WHERE path=?", (os.path.normpath(path),)).fetchone()
    if not r:
        name = os.path.basename(path)
        for row in c.execute("SELECT path, site, post_url FROM import_meta WHERE path LIKE ? ESCAPE '!'",
                             ("%" + name.replace("!", "!!").replace("%", "!%").replace("_", "!_"),)):
            if os.path.basename(row[0].replace("\\", "/")) == name or os.path.basename(row[0]) == name:
                r = row[1:]
                with db.write_lock:     # 順便把路徑改成新的
                    c.execute("UPDATE OR IGNORE import_meta SET path=? WHERE path=?", (os.path.normpath(path), row[0]))
                    c.commit()
                break
    return {"site": r[0], "post_url": r[1]} if r and r[1] else None


# ---------- 處理完之後：補上網站提供、但一般流程不知道的資訊 ----------
_apply_lock = threading.Lock()


_APPLY_SQL = """SELECT i.path, i.site, i.data, m.id FROM import_meta i JOIN media m ON m.path=i.path
               WHERE i.applied=0 AND m.status IN ('done','error')"""


def after_processed():
    """新匯入的圖標記完之後呼叫（背景）。每次掃描都會呼叫：已經有一個在跑／在排就不再開執行緒。"""
    _ensure()
    if _apply_lock.locked():
        return              # 正在跑的那個做完會再查一次（_apply_rows 的迴圈）
    if db.conn().execute(_APPLY_SQL + " LIMIT 1").fetchone():
        from . import tasks
        tasks.spawn("import-apply", "匯入後處理（搜源、創作者、本子）", _apply_rows, name="import-apply")


def _apply_rows():
    from . import candidates, online
    _apply_lock.acquire()      # 排在正在跑的後面（最多一兩個在等：after_processed 看到在跑就不開新的）
    try:
        seen: set[str] = set()
        while True:     # 跑的時候又有新的匯入標記完：接著做（after_processed 看到在跑就不會另開）；失敗的這次不再重試
            rows = [tuple(r) for r in db.conn().execute(_APPLY_SQL) if r[0] not in seen]
            if not rows:
                break
            seen.update(r[0] for r in rows)
            _apply_rows_locked(rows, candidates, online)
    finally:
        _apply_lock.release()


def _apply_rows_locked(rows, candidates, online):
    # 在鎖裡面才查：以前在鎖外查，兩個執行緒會拿到同一批、各做一次（重複搜源、重複查 e621）
    if True:
        search = []
        pixiv_albums: dict[str, dict] = {}
        for path, site, data, mid in rows:
            meta = json.loads(data or "{}")
            try:
                with db.write_lock:
                    c = db.conn()
                    if meta.get("nsfw"):        # 網站標成成人／敏感 → NSFW（不覆蓋你手動設定的）
                        c.execute("UPDATE media SET nsfw_manual=1 WHERE id=? AND nsfw_manual IS NULL", (mid,))
                    c.execute("UPDATE import_meta SET applied=1 WHERE path=?", (path,))
                    c.commit()
                if site == "e621":
                    from . import e621
                    e621.check_one(mid)
                elif site == "r34":
                    from . import r34
                    if r34.configured():
                        r34.check_one(mid)
                if site == "pixiv" and meta.get("album"):      # 存成本子的 Pixiv 多頁作品：跟同人本一樣不搜源
                    pixiv_albums[c.execute("SELECT post_url FROM import_meta WHERE path=?", (path,)).fetchone()[0]] = meta
                elif site in ("x", "pixiv", "danbooru") or (site or "").startswith("gdl:"):
                    search.append(mid)
                if (site or "").startswith("gdl:"):     # 網站帳號 → Danbooru 登記的畫師
                    from . import gdl
                    gdl.after_processed(mid, meta)
                if site == "x" and meta.get("handle"):
                    from . import sigscan
                    sigscan._ensure()
                    artist = sigscan.resolve("x", meta["handle"])
                    why = f"X 貼文作者 @{meta['handle']}"
                    if not artist:          # Danbooru 還沒登記：先用 X 帳號，之後登記了自動合併
                        from . import siteartists
                        artist = siteartists.create("x", meta["handle"], meta.get("author"))
                        why += "（Danbooru 還沒登記，先用 X 帳號）"
                    if artist and not sigscan._is_character_account(mid, artist):
                        with db.write_lock:
                            candidates.add(mid, artist, "artist", candidates.T_DANBOORU, "x", reason=why)
                            db.conn().commit()
            except Exception as e:  # noqa
                log.warning("匯入後處理失敗 %s：%s", path, e)
        for url, meta in pixiv_albums.items():   # Pixiv 多頁作品存成本子
            try:
                pixiv_album(url, meta)
            except Exception as e:  # noqa
                log.warning("Pixiv 存成本子失敗 %s：%s", url, e)
        gal = {json.loads(data or "{}").get("gallery") for path, site, data, mid in rows if site in ("ehentai", "nhentai")}
        if gal - {None}:            # 整本匯入：全部頁標記完 → 建立作品集、整本的標籤
            from . import galleries
            try:
                galleries.after_processed(gal - {None})
            except Exception as e:  # noqa
                log.warning("整本匯入建立作品集失敗：%s", e)
        jobs = online.get_jobs()
        for mid in search:          # 馬上搜（不排在背景幾百張的後面）；這裡本來就是匯入自己的背景執行緒
            try:
                jobs.source_now(mid)
            except Exception as e:  # noqa
                log.warning("匯入的圖搜源失敗 %s：%s", mid, e)
        # b95：以前從 X 匯入、Danbooru 沒有的會排到 ascii2d 佇列最前面（找 Pixiv 上更大的版本）。
        # 開發時的紀錄 602 張只多找到 4 張→ 不排了；想找的話自己選圖用 ascii2d 或「貼出處網址」
        for path, site, data, mid in rows:      # X 的 hashtag → 角色／作品（Danbooru 沒找到角色時才用）
            meta = json.loads(data or "{}")
            if site == "x" and meta.get("hashtags"):
                try:
                    _apply_hashtags(mid, meta["hashtags"])
                except Exception as e:  # noqa
                    log.warning("hashtag 對應失敗 %s：%s", mid, e)


def _apply_hashtags(mid: int, tags: list[str]) -> int:
    from . import candidates, sitetags
    from .pixiv import _pixiv_character_matches
    c = db.conn()
    if c.execute("""SELECT 1 FROM media_tags mt JOIN tags t ON t.id=mt.tag_id WHERE mt.media_id=? AND t.category='character'
                    AND mt.state>=0 AND mt.source='danbooru'""", (mid,)).fetchone():
        return 0                   # Danbooru 已經給了角色（人工整理的）→ 以它為準
    found = [(n, "character", f"#{h}") for h in tags for n in _pixiv_character_matches([{"tag": h}])]
    for h, fs in sitetags.jp_to_danbooru(tags).items():
        found += [(f["name"], f["category"], f"#{h}") for f in fs]
    n = 0
    with db.write_lock:
        for name, cat, why in found:
            n += bool(candidates.add(mid, name, cat, candidates.T_PIXIV, "x", reason=f"X hashtag {why}"))
        db.conn().commit()
    return n


# ---------- 瀏覽器按鈕用的配對金鑰 ----------
def import_key() -> str:
    s = db.get_settings()
    k = s.get("import_key") or ""
    if not k:
        k = secrets.token_urlsafe(24)
        db.update_settings({"import_key": k})
    return k


def check_key(k: str | None) -> bool:
    return bool(k) and secrets.compare_digest(k, import_key())
