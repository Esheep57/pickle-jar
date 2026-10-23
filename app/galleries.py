"""整本匯入：E-Hentai、nhentai（同人本、漫畫，幾十到幾百頁）。

- 背景下載（一頁一頁、照網站限速），進度在「從網站匯入」視窗看得到（/api/fetch-url/galleries）。
- 每一本收在自己的小資料夾：匯入/e-hentai/「標題 [eh123]」/001.jpg …
- 全部頁數標記完之後自動建立作品集（kind=同人本），網站的標籤對到 Danbooru 的變成「整本的標籤」：
  作者／社團、作品（parody）、角色；對不上的作者／社團用網站上的名字建立創作者（之後 Danbooru 登記了自動合併）。
- E-Hentai：伺服器自己抓（不用登入；ExHentai 的網址改用 e-hentai.org，只有 ExHentai 才有的本抓不到）。
  圖是網站顯示的版本（最長邊 1280 左右）；原檔要登入、扣 GP，不抓。
- nhentai：網站有 Cloudflare，程式直接問常被擋 → 主要靠瀏覽器腳本在網頁上解析（同網域的 API），把每頁網址和標籤送來。
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading
import time

from . import db

log = logging.getLogger("galleries")
SITES = ("ehentai", "nhentai")
EH = os.environ.get("LIB_EHENTAI", "https://e-hentai.org")
NH = os.environ.get("LIB_NHENTAI", "https://nhentai.net")
NH_IMG_BASE = os.environ.get("LIB_NHENTAI_IMG", "")       # 測試用：假的圖片伺服器
NH_IMG = re.compile(r"^(?:https://i\d?\.nhentai\.net|" + re.escape(NH_IMG_BASE or "https://none.invalid") +
                    r")/galleries/\d+/\d+\.(?:jpg|jpeg|png|gif|webp)$", re.I)
MAX_PAGES = 2000
JOBS: dict[str, dict] = {}            # 網址 → 進度（給匯入視窗看；最多留 30 筆）
_lock = threading.Lock()


class GalleryError(Exception):
    pass


def _ie(msg):
    from .importer import ImportError_
    return ImportError_(msg)


# ---------------- E-Hentai ----------------
def _eh_get(url: str):
    from .httpclient import BROWSER_UA, _req
    r = _req("ehentai", "GET", url, headers={"User-Agent": BROWSER_UA}, cookies={"nw": "1"})
    if r.status_code == 404:
        raise _ie("E-Hentai 找不到這本（被刪除、或是只有 ExHentai 才有）")
    if r.status_code >= 400:
        raise _ie(f"E-Hentai 讀不到（HTTP {r.status_code}）")
    t = r.text
    if "This gallery has been removed" in t or "Key missing, or incorrect key provided" in t:
        raise _ie("E-Hentai：這本被刪除了，或網址不對")
    if "temporarily banned" in t.lower():
        raise _ie("E-Hentai 暫時封鎖了你的 IP（請求太快）：過一陣子再試")
    return t


def _unesc(s: str) -> str:
    import html
    return html.unescape(s or "").strip()


def eh_meta(p: dict) -> dict:
    page = _eh_get(f"{EH}/g/{p['id']}/{p['token']}/")
    title = _unesc((re.search(r'<h1 id="gn">([^<]*)', page) or [None, ""])[1])
    title_jp = _unesc((re.search(r'<h1 id="gj">([^<]*)', page) or [None, ""])[1])
    n = int((re.search(r'>Length:</td><td class="gdt2">(\d+)', page) or [None, "0"])[1])
    tags = []
    for m in re.finditer(r'hentai\.org/tag/([^"]+)"', page):
        t = _unesc(m.group(1)).replace("+", " ")
        from urllib.parse import unquote
        t = unquote(t)
        ns, _, name = t.partition(":") if ":" in t else ("misc", "", t)
        if (ns, name) not in tags:
            tags.append((ns, name))
    return {"title": title, "title_jp": title_jp, "count": n, "tags": tags, "first": page}


def eh_pages(p: dict, meta: dict, stop) -> list[str]:
    """每一頁的網址（/s/<token>/<gid>-<n>）：縮圖頁一頁 20～40 張，翻到收齊為止。"""
    out, seen, gp = [], set(), 0
    page = meta["first"]
    while True:
        for m in re.finditer(r'hentai\.org/s/([0-9a-f]{10})/(\d+)-(\d+)', page):
            if m.group(2) == str(p["id"]) and m.group(3) not in seen:
                seen.add(m.group(3))
                out.append((int(m.group(3)), f"{EH}/s/{m.group(1)}/{m.group(2)}-{m.group(3)}"))
        if len(out) >= meta["count"] or stop() or gp > 200:
            break
        gp += 1
        nxt = _eh_get(f"{EH}/g/{p['id']}/{p['token']}/?p={gp}")
        if nxt == page or not re.search(r'hentai\.org/s/', nxt):
            break
        page = nxt
    return [u for _, u in sorted(out)][:MAX_PAGES]


def eh_image(url: str) -> str:
    page = _eh_get(url)
    m = re.search(r'<img id="img" src="([^"]+)"', page)
    if not m:
        raise _ie("E-Hentai：讀不到圖片網址")
    u = _unesc(m.group(1))
    if "509.gif" in u or "/509" in u:
        raise _ie("E-Hentai 的圖片額度用完了（509）：過幾個小時再接著匯入（已經下載的頁不會重抓）")
    return u


# ---------------- nhentai ----------------
def nh_from_ext(ext: dict) -> dict:
    info = ext.get("info") or {}
    pages = [u for u in (ext.get("pages") or []) if isinstance(u, str) and NH_IMG.match(u)]
    if not pages:
        raise _ie("nhentai：腳本沒有送來可以下載的頁")
    title = info.get("title") or {}
    if isinstance(title, dict):
        title_s, title_jp = title.get("pretty") or title.get("english") or "", title.get("japanese") or ""
    else:
        title_s, title_jp = str(title), ""
    tags = [(t.get("type") or "tag", t.get("name") or "") for t in info.get("tags") or [] if isinstance(t, dict) and t.get("name")]
    return {"title": title_s, "title_jp": title_jp, "count": len(pages), "tags": tags, "pages": pages[:MAX_PAGES]}


def nh_meta(p: dict) -> dict:
    """程式自己問（沒有腳本時）。nhentai 常用 Cloudflare 擋程式：被擋就請你用瀏覽器按鈕。"""
    import random
    from .httpclient import BROWSER_UA, _req
    r = _req("nhentai", "GET", f"{NH}/api/v2/galleries/{p['id']}", headers={"User-Agent": BROWSER_UA, "Referer": NH + "/"})
    if r.status_code in (403, 503) or "json" not in r.headers.get("content-type", ""):
        raise _ie("nhentai 擋了程式（Cloudflare）：請在 nhentai 網頁上用瀏覽器按鈕「⤓ 存到圖庫」匯入")
    if r.status_code == 404:
        raise _ie("nhentai 找不到這本")
    if r.status_code >= 400:
        raise _ie(f"nhentai 讀不到（HTTP {r.status_code}）")
    d = r.json() or {}
    pages = [f"{NH_IMG_BASE or f'https://i{random.randint(1, 4)}.nhentai.net'}/{pg['path'].lstrip('/')}" for pg in d.get("pages") or [] if pg.get("path")]
    return nh_from_ext({"info": d, "pages": pages})


# ---------------- 下載工作 ----------------
def folder_name(site: str, gid: str, title: str) -> str:
    from .importer import _safe_name
    tag = {"ehentai": "eh", "nhentai": "nh"}[site]
    t = re.sub(r"\s+", " ", title or "").strip()
    return (_safe_name(f"{t[:90]} [{tag}{gid}]") if t else f"[{tag}{gid}]").strip()


def start(p: dict, ext: dict | None = None) -> dict:
    """驗證＋讀資訊（很快），下載排到背景。回傳給匯入視窗／腳本。"""
    key = p["url"]
    with _lock:
        j = JOBS.get(key)
        if j and j["state"] in ("queued", "running"):
            return {"site": p["site"], "post_url": key, "saved": [], "existing": [], "queued": True,
                    "count": j.get("total") or 0, "title": j.get("title"), "author": None, "already": True}
    if p["site"] == "nhentai":
        meta = nh_from_ext(ext) if ext else nh_meta(p)
    else:
        meta = eh_meta(p)
    if not meta["count"]:
        raise _ie("這本沒有頁")
    job = {"url": key, "site": p["site"], "title": meta["title"] or meta["title_jp"], "total": meta["count"], "done": 0,
           "skipped": 0, "state": "queued", "error": None, "started": time.time(), "folder": None}
    with _lock:
        JOBS[key] = job
        for k in list(JOBS)[:-30]:
            if JOBS[k]["state"] not in ("queued", "running"):
                JOBS.pop(k, None)
    from . import tasks
    tasks.spawn("gallery", f"整本匯入：{job['title'] or key}"[:80], _run, p, meta, job, probe=tasks.probe_of(job),
                cancel=lambda: cancel(key), name=f"gallery-{p['site']}-{p['id']}")
    return {"site": p["site"], "post_url": key, "saved": [], "existing": [], "queued": True, "count": meta["count"],
            "title": job["title"], "author": None}


def _run(p: dict, meta: dict, job: dict):
    from . import errlog, importer, pipeline
    job["state"] = "running"
    saved = []
    try:
        d = importer.dest_dir(p["site"]) / folder_name(p["site"], p["id"], meta["title"] or meta["title_jp"])
        d.mkdir(parents=True, exist_ok=True)
        job["folder"] = str(d)
        stop = lambda: job.get("cancel")
        if p["site"] == "ehentai":
            pages = eh_pages(p, meta, stop)
            job["total"] = len(pages)
        else:
            pages = meta["pages"]
        width = max(3, len(str(len(pages))))
        info = {"title": meta["title"], "title_jp": meta["title_jp"], "gallery": p["url"], "tags": meta["tags"],
                "site": p["site"], "gid": p["id"], "count": len(pages)}
        c = db.conn()
        for i, src in enumerate(pages, 1):
            if stop():
                job["state"] = "stopped"
                break
            stem = f"{i:0{width}d}"
            have = [f for f in d.glob(stem + ".*") if not f.name.endswith(".part")]
            if have:
                job["skipped"] += 1
                job["done"] = i
                continue
            url = eh_image(src) if p["site"] == "ehentai" else src
            ext = (re.search(r"\.(jpe?g|png|gif|webp)(?:$|\?)", url.split(";")[-1].split("?")[0], re.I) or
                   re.search(r"\.(jpe?g|png|gif|webp)", url, re.I))
            dst = d / f"{stem}.{(ext.group(1).lower() if ext else 'jpg').replace('jpeg', 'jpg')}"
            importer._download(url, dst, {"Referer": (EH if p["site"] == "ehentai" else NH) + "/"}, p["site"])
            saved.append(str(dst))
            with db.write_lock:
                c.execute("INSERT OR REPLACE INTO import_meta(path, site, post_url, data, created_at, applied) VALUES(?,?,?,?,?,0)",
                          (os.path.normpath(str(dst)), p["site"], p["url"], json.dumps({**info, "page": i}, ensure_ascii=False), time.time()))
                c.commit()
            job["done"] = i
            if len(saved) % 20 == 0:          # 邊下載邊標記，不用等整本下完
                pipeline.get_jobs().submit("scan", saved[-20:])
        if job["state"] == "running":
            job["state"] = "done"
    except Exception as e:  # noqa
        job["state"], job["error"] = "error", str(e)[:300]
        errlog.record_bg(f"整本匯入 {p['url']}", e)
    finally:
        rest = saved[len(saved) // 20 * 20:]
        if rest:
            pipeline.get_jobs().submit("scan", rest)
        job["ended"] = time.time()


def status() -> list[dict]:
    with _lock:
        return [dict(j) for j in JOBS.values()][::-1]


def cancel(url: str) -> bool:
    j = JOBS.get(url)
    if j and j["state"] in ("queued", "running"):
        j["cancel"] = True
        return True
    return False


# ---------------- 標記完：建立作品集、整本的標籤 ----------------
LINK = {"ehentai": "https://e-hentai.org/tag/{}", "nhentai": "https://nhentai.net/{}/"}


def map_tags(site: str, tags: list) -> list[dict]:
    """網站的 namespace 標籤 → 整本的標籤 [{name, category}]。作者／社團 → 創作者；parody → 作品；character → 角色。"""
    from . import siteartists
    from .r34 import dan_aliases, dan_categories
    want = {"artist": "artist", "group": "artist", "parody": "copyright", "character": "character"}
    raw = [(ns, re.sub(r"\s+", "_", name.strip().lower())) for ns, name in tags if ns in want and name.strip()]
    raw = [(ns, n) for ns, n in raw if n not in ("original", "various")]
    names = list(dict.fromkeys(n for _, n in raw))
    cats = dan_categories(names) if names else {}
    miss = [n for n in names if not cats.get(n)]
    alias = dan_aliases(miss) if miss else {}
    acats = dan_categories(list(set(alias.values()))) if alias else {}
    out, seen = [], set()
    for ns, n in raw:
        name, cat = n, cats.get(n, "")
        if not cat and n in alias:
            name, cat = alias[n], acats.get(alias[n], "")
        if cat != want[ns]:
            if want[ns] != "artist":
                continue                      # 作品、角色對不上 Danbooru 的不收（羅馬拼音常常跟 Danbooru 的寫法不同，猜錯比沒標糟）
            try:
                name = siteartists.create(site, n, n.replace("_", " "))
            except Exception as e:  # noqa
                log.info("建立創作者 %s 失敗：%s", n, e)
                continue
            cat = "artist"
        if name not in seen:
            seen.add(name)
            out.append({"name": name, "category": cat})
    return out


def after_processed(urls):
    """importer._apply_rows 呼叫：這次處理到的整本，所有頁都標記完了 → 建立作品集（已經有就補頁）。"""
    from . import albums
    c = db.conn()
    for url in urls:
        if c.execute("SELECT 1 FROM import_meta WHERE post_url=? AND applied=0", (url,)).fetchone():
            continue                          # 還有頁沒標完
        j = JOBS.get(url)
        if j and j["state"] in ("queued", "running"):
            continue                          # 還在下載
        pages = []
        for path, data in c.execute("SELECT path, data FROM import_meta WHERE post_url=?", (url,)):
            m = c.execute("SELECT id FROM media WHERE path=?", (path,)).fetchone()
            if m:
                pages.append((json.loads(data or "{}").get("page") or 0, m[0], json.loads(data or "{}")))
        if not pages:
            continue
        pages.sort()
        ids = [m for _, m, _ in pages]
        info = pages[0][2]
        a = c.execute("SELECT id FROM albums WHERE source_url=?", (url,)).fetchone()
        try:
            if a:
                albums.add_items(a[0], ids)
                albums.reorder(a[0], ids)
            else:
                tags = map_tags(info.get("site") or "", [tuple(t) for t in info.get("tags") or []])
                title = info.get("title_jp") or info.get("title") or url
                folder = os.path.dirname(c.execute("SELECT path FROM media WHERE id=?", (ids[0],)).fetchone()[0])
                albums.create(title, ids, "doujin", tags, folder=None, source_url=url,
                              note=info.get("title") if info.get("title_jp") and info.get("title") != title else None)
                with db.write_lock:
                    c.execute("UPDATE albums SET folder=? WHERE source_url=?", (folder, url))
        except Exception as e:  # noqa
            from . import errlog
            errlog.record_bg(f"整本匯入：建立作品集 {url}", e)
