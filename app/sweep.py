"""從畫師的作品全集反查出處（Pixiv、X）。

你已經知道作者的圖很多（Danbooru、ascii2d、匯入…），但同一位畫師的其他圖常常也在你的圖庫裡、只是認不出來
（手機從 X 存的 IMG_xxxx、截圖、重新存檔）。這裡反過來：拿畫師在網站上的全部作品縮圖，在本機跟還沒找到出處的圖比。

- Pixiv（伺服器自己抓）：/ajax/user/{id}/profile/all 拿作品編號 → profile/illusts 每次 48 件拿日期路徑、頁數
  → 每一頁的小縮圖 i.pximg.net/c/360x360_70/img-master/…_p{n}_master1200.jpg（約 25 KB，不裁切）。
  有填 PHPSESSID（設定 › 帳號）就看得到 R-18。
- X（要你登入，伺服器拿不到）：瀏覽器腳本在 x.com/<帳號>/media 一邊往下捲、一邊把圖片網址送回來
  （api：/api/sweep/ext/x），這裡下載 name=small（約 50 KB）比對。
- 比對（跟「從畫師反推」同一套）：dHash 接近的、或 WD 特徵最像的那張（每張縮圖都跑模型，裁切、截圖也找得到）
  → 縮圖特徵 ≥ 0.55 → 下載大一點的版本再比一次 ≥ 0.90 才算找到。
- 找到：記下出處（Pixiv 作品／推文），這位畫師直接確認（是在他自己的作品裡逐像素比到的，跟 Pixiv ID 一樣確定）；
  Pixiv 標籤對應的角色只放待確認；Danbooru 也有這個 Pixiv 作品就用 Danbooru 的標籤（原檔）；排「換高清」。
- 記住看過的作品（sweep_seen），下次只看新的；每位畫師記下看了多少、找到幾張（sweep_artist）。
"""
from __future__ import annotations

import io
import json
import logging
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor

import numpy as np

from . import db, media

log = logging.getLogger("sweep")
PIXIV = os.environ.get("LIB_PIXIV", "https://www.pixiv.net")
PXIMG = os.environ.get("LIB_PXIMG", "https://i.pximg.net")
XIMG = os.environ.get("LIB_XIMG", "https://pbs.twimg.com")
SRC = {"pixiv": "pixiv_scan", "x": "x_scan"}
MAX_WORKS = 400          # 每位畫師最多看幾件（新的先）
MAX_PAGES = 6            # 每件最多看幾頁（漫畫幾十頁的只看前面）
RESCAN_DAYS = 30
SCHEMA = """CREATE TABLE IF NOT EXISTS sweep_artist(site TEXT, key TEXT, artist TEXT, scanned_at REAL, works INTEGER,
  thumbs INTEGER, matched INTEGER, last_max TEXT, error TEXT, PRIMARY KEY(site, key));
CREATE TABLE IF NOT EXISTS sweep_seen(site TEXT, item TEXT, PRIMARY KEY(site, item));
CREATE TABLE IF NOT EXISTS sweep_xqueue(handle TEXT PRIMARY KEY, artist TEXT, seq REAL, status TEXT, at REAL)"""
_PIX_USER = re.compile(r"pixiv\.net/(?:en/)?(?:users/|member(?:_illust)?\.php\?id=)(\d+)")
_X_USER = re.compile(r"(?:twitter|x)\.com/(?!i/|intent|home|search|hashtag)([A-Za-z0-9_]{1,15})(?:[/?#\"]|$)")
_ready = False
STATE: dict = {}


def _ensure():
    global _ready
    if not _ready:
        from . import siteartists
        siteartists._ensure()          # pending_artist（網站帳號建立的畫師）
        with db.write_lock:
            db.conn().executescript(SCHEMA)
        _ready = True


# ---------------- 哪些畫師、他們的帳號 ----------------
def artists(site: str) -> list[dict]:
    """有這個網站帳號的創作者，依你收藏的張數排（收越多的越可能還有沒認出來的）。"""
    _ensure()
    c = db.conn()
    rx = _PIX_USER if site == "pixiv" else _X_USER
    out: dict[str, dict] = {}
    for name, links, url, n in c.execute("""SELECT t.name, ti.links, ti.url,
            (SELECT COUNT(*) FROM media_tags mt WHERE mt.tag_id=t.id AND mt.state=1) n
            FROM tags t JOIN tag_info ti ON ti.tag_id=t.id WHERE t.category='artist'"""):
        if not n:
            continue
        keys = set()
        for u in (json.loads(links or "[]") if links else []) + ([url] if url else []):
            m = rx.search(str(u) + "/")
            if m:
                keys.add(m.group(1).lower() if site == "x" else m.group(1))
        for k in keys:
            if k not in out or out[k]["n"] < n:
                out[k] = {"key": k, "artist": name, "n": n}
    for tag, key in c.execute("SELECT tag, key FROM pending_artist WHERE site=?", (site,)):
        k = key.lower() if site == "x" else key
        n = c.execute("""SELECT COUNT(*) FROM media_tags mt JOIN tags t ON t.id=mt.tag_id WHERE t.name=? AND mt.state=1""", (tag,)).fetchone()[0]
        if n and k not in out:
            out[k] = {"key": k, "artist": tag, "n": n}
    done = {r[0]: r for r in c.execute("SELECT key, scanned_at, works, thumbs, matched, error FROM sweep_artist WHERE site=?", (site,))}
    rows = sorted(out.values(), key=lambda a: -a["n"])
    for a in rows:
        d = done.get(a["key"])
        a.update(scanned_at=d[1] if d else None, works=d[2] if d else None, matched=d[4] if d else None, error=d[5] if d else None)
    return rows


def summary() -> dict:
    _ensure()
    c = db.conn()
    out = {}
    for site in ("pixiv", "x"):
        a = artists(site)
        out[site] = {"artists": len(a), "scanned": sum(1 for x in a if x["scanned_at"]),
                     "matched": c.execute("SELECT COALESCE(SUM(matched),0) FROM sweep_artist WHERE site=?", (site,)).fetchone()[0],
                     "found_images": c.execute("SELECT COUNT(*) FROM media_source WHERE method=? AND status='found'", (SRC[site],)).fetchone()[0]}
    out["x"]["queue"] = c.execute("SELECT COUNT(*) FROM sweep_xqueue WHERE status='queued'").fetchone()[0]
    out["state"] = {k: v for k, v in STATE.items() if k != "matches"}
    out["matches"] = STATE.get("matches", [])[-30:]
    return out


# ---------------- 比對（共用） ----------------
class Matcher:
    """一次載入「還沒找到出處的圖」的 dHash、特徵矩陣；之後每張縮圖只要一次矩陣乘法。"""

    def __init__(self):
        from . import artistscan, tagger
        self.T = artistscan.targets()
        self.tg = tagger.ensure()
        self.kidx, MM = artistscan._target_matrix(self.T) if self.tg is not None else (None, None)
        self.M, self.mu = MM if MM else (None, None)
        self.row_of = {int(k): j for j, k in enumerate(self.kidx)} if self.kidx is not None else {}
        self.paths = {r[0]: r[1] for r in db.conn().execute("SELECT id, path FROM media WHERE kind='image'")}
        self.taken: set[int] = set()

    @property
    def ok(self) -> bool:
        return self.M is not None and len(self.T["ids"]) > 0

    def best(self, imgs: list) -> list[tuple[int, float, int] | None]:
        """每張縮圖 → (本機圖 id, 縮圖特徵相似度, dHash 距離) 或 None。"""
        from . import artistscan
        out = [None] * len(imgs)
        feats = []
        for i, im in enumerate(imgs):
            try:
                d = media.dhash(im) & ((1 << 64) - 1)
            except Exception:  # noqa
                d = None
            feats.append(d)
        for b in range(0, len(imgs), 16):
            chunk = list(range(b, min(b + 16, len(imgs))))
            _, E = self.tg.run(np.stack([self.tg.preprocess(media.to_rgb(imgs[i])) for i in chunk]))
            E = E - self.mu
            E /= np.linalg.norm(E, axis=1, keepdims=True) + 1e-8
            E = E.astype(np.float32)
            for i, e in zip(chunk, E):
                cands = set()
                dist = None
                if feats[i] is not None:
                    dist = artistscan._popcount(self.T["dh"] ^ np.uint64(feats[i])).astype(int)
                    cands |= {int(k) for k in np.argsort(dist)[:3] if dist[k] <= artistscan.HASH_CAND}
                row = self.M @ e
                cands.add(int(self.kidx[int(np.argmax(row))]))
                scored = sorted(((float(self.M[self.row_of[k]] @ e), k) for k in cands
                                 if k in self.row_of and int(self.T["ids"][k]) not in self.taken), reverse=True)
                if scored:
                    sim, k = scored[0]
                    out[i] = (int(self.T["ids"][k]), sim, int(dist[k]) if dist is not None else -1)
        return out

    def verify(self, mid: int, big) -> float | None:
        from pathlib import Path
        from .danbooru import compare_images, trim_borders
        try:
            local = trim_borders(media.load_image(Path(self.paths.get(mid, ""))))
            return compare_images(local, media.to_rgb(big))
        except Exception as e:  # noqa
            log.info("確認 %s 失敗：%s", mid, e)
            return None


def _get_img(url: str, referer: str | None = None):
    from PIL import Image
    from .httpclient import client
    try:
        r = client().get(url, timeout=25, headers={"Referer": referer} if referer else None)
        if r.status_code >= 400 or not r.content:
            return None, 0
        im = Image.open(io.BytesIO(r.content))
        im.load()
        return im, len(r.content)
    except Exception:  # noqa
        return None, 0


def _seen(site: str) -> set[str]:
    return {r[0] for r in db.conn().execute("SELECT item FROM sweep_seen WHERE site=?", (site,))}


def _mark_seen(site: str, items: list[str]):
    with db.write_lock:
        db.conn().executemany("INSERT OR IGNORE INTO sweep_seen(site, item) VALUES(?,?)", [(site, i) for i in items])


def _compare(site: str, M: Matcher, thumbs: list[dict], stop, st: dict) -> list[dict]:
    """thumbs：[{item, thumb, big, referer, ...}]。下載、比對、確認；回傳找到的 [{mid, item, sim, conf}]。"""
    found = []
    pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix=f"sweep-{site}")
    try:
        for b in range(0, len(thumbs), 32):
            if stop():
                break
            chunk = thumbs[b:b + 32]
            got = list(pool.map(lambda t: _get_img(t["thumb"], t.get("referer")), chunk))
            imgs, keep = [], []
            for t, (im, n) in zip(chunk, got):
                st["mb"] = round(st.get("mb", 0) + n / 1e6, 2)
                if im is not None:
                    imgs.append(im)
                    keep.append(t)
            st["thumbs"] = st.get("thumbs", 0) + len(imgs)
            if not imgs:
                continue
            from .artistscan import PRE_MIN, CONFIRM_MIN
            # 同一張本機圖可能被好幾張縮圖對到（同一張圖在不同推文裡重貼、差分）：最像的先確認，確認到了其他的就不算
            pairs = sorted(((r[1], i, r) for i, r in enumerate(M.best(imgs)) if r and r[1] >= PRE_MIN), key=lambda x: -x[0])
            for _, i, r in pairs:
                t = keep[i]
                mid, sim, dist = r
                if mid in M.taken:
                    continue
                st["checked"] = st.get("checked", 0) + 1
                big, n = _get_img(t["big"], t.get("referer"))
                st["mb"] = round(st.get("mb", 0) + n / 1e6, 2)
                conf = M.verify(mid, big) if big is not None else None
                if conf is None or conf < CONFIRM_MIN:
                    continue
                M.taken.add(mid)
                found.append({"mid": mid, "sim": round(sim, 3), "conf": round(conf, 3), "dist": dist, **t})
            _mark_seen(site, [t["item"] for t in keep])
    finally:
        pool.shutdown(wait=False)
    return found


def _accept(site: str, artist: str, f: dict) -> None:
    """找到了：記出處、確認這位畫師、套用標籤（Pixiv 作品在 Danbooru 上也有就用 Danbooru 的）、排換高清。"""
    from . import candidates, upgrade
    from .online import auto_apply, save_source, suggested_tags
    mid = f["mid"]
    if site == "pixiv":
        pid = int(f["pid"])
        res = {"status": "found", "method": SRC[site], "similarity": round(f["conf"] * 100, 1),
               "post_url": f"https://www.pixiv.net/artworks/{pid}", "source_url": f"https://www.pixiv.net/artworks/{pid}",
               "pixiv_id": pid, "data": {"sweep": {"artist": artist, "page": f.get("page"), "sim": f["sim"], "confirm": f["conf"]}}}
        try:
            from .danbooru import _post_summary, dan_get
            posts = dan_get("/posts.json", {"tags": f"pixiv:{pid}", "limit": 1})
            if posts:
                post = _post_summary(posts[0])
                res["data"]["danbooru"] = post
        except Exception as e:  # noqa
            log.info("Danbooru 查 pixiv:%s 失敗：%s", pid, e)
        try:
            from .pixiv import fetch_pixiv
            res["data"]["pixiv"] = fetch_pixiv(pid)
            res["data"]["pixiv"]["artist_tag"] = artist
        except Exception as e:  # noqa
            res["data"]["pixiv_error"] = str(e)[:200]
    else:
        res = {"status": "found", "method": SRC[site], "similarity": round(f["conf"] * 100, 1),
               "post_url": f"https://x.com/{f['handle']}/status/{f['tweet_id']}", "source_url": f"https://x.com/{f['handle']}/status/{f['tweet_id']}",
               "pixiv_id": None, "data": {"sweep": {"artist": artist, "media": f["item"], "sim": f["sim"], "confirm": f["conf"]},
                                          "x": {"handle": f["handle"], "tweet_id": f["tweet_id"], "media": f["item"]}}}
    res["data"]["suggested"] = [t for t in suggested_tags(res) if not (t["category"] == "artist" and t["name"] != artist)]
    save_source(mid, res)
    auto_apply(mid, res)
    with db.write_lock:
        candidates.add(mid, artist, "artist", candidates.T_DANBOORU_EXACT, SRC[site], f["conf"],
                       f"在這位畫師的{'Pixiv 作品' if site == 'pixiv' else ' X 媒體頁'}裡比對到（{round(f['conf'] * 100)}%）", confirm=True)
        db.conn().execute("UPDATE media SET edited_at=? WHERE id=?", (time.time(), mid))
    try:
        upgrade.schedule(mid)
    except Exception:  # noqa
        pass


def _record(site: str, key: str, artist: str, works: int, thumbs: int, matched: int, last_max: str | None, error: str | None = None):
    with db.write_lock:
        db.conn().execute("""INSERT INTO sweep_artist(site, key, artist, scanned_at, works, thumbs, matched, last_max, error)
            VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(site, key) DO UPDATE SET artist=excluded.artist, scanned_at=excluded.scanned_at,
            works=excluded.works, thumbs=sweep_artist.thumbs+excluded.thumbs, matched=sweep_artist.matched+excluded.matched,
            last_max=COALESCE(excluded.last_max, sweep_artist.last_max), error=excluded.error""",
                          (site, key, artist, time.time(), works, thumbs, matched, last_max, error))


# ---------------- Pixiv ----------------
def _pix_get(path: str, params=None) -> dict:
    from .httpclient import BROWSER_UA, _req
    s = db.get_settings()
    headers = {"User-Agent": BROWSER_UA, "Referer": "https://www.pixiv.net/", "Accept-Language": "ja,en;q=0.8"}
    cookies = {"PHPSESSID": s["pixiv_phpsessid"]} if s.get("pixiv_phpsessid") else None
    r = _req("pixiv", "GET", f"{PIXIV}{path}", params=params, headers=headers, cookies=cookies)
    d = r.json()
    if d.get("error"):
        raise RuntimeError(d.get("message") or f"Pixiv HTTP {r.status_code}")
    return d.get("body") or {}


_DATEPATH = re.compile(r"/img/(\d{4}/\d{2}/\d{2}/\d{2}/\d{2}/\d{2})/(\d+(?:-[0-9a-f]{32})?)_p0")


def pixiv_thumbs(uid: str, since: int = 0, limit: int = MAX_WORKS, seen: set | None = None) -> tuple[list[dict], int, int]:
    """這位作者的作品縮圖（每頁一個）。回傳 (thumbs, 作品數, 最新作品編號)。"""
    body = _pix_get(f"/ajax/user/{uid}/profile/all")
    ids = sorted({int(i) for k in ("illusts", "manga") for i in (body.get(k) or {}) or []}, reverse=True)
    newest = ids[0] if ids else 0
    ids = [i for i in ids if i > since][:limit]
    have = {int(r[0]) for r in db.conn().execute("SELECT pixiv_id FROM media_source WHERE status='found' AND pixiv_id IS NOT NULL")}
    ids = [i for i in ids if i not in have]          # 你已經有（出處已知）的作品不用比
    seen = seen or set()
    out = []
    for b in range(0, len(ids), 48):
        part = ids[b:b + 48]
        params = [("ids[]", str(i)) for i in part] + [("work_category", "illustManga"), ("is_first_page", "0")]
        works = (_pix_get(f"/ajax/user/{uid}/profile/illusts", params) or {}).get("works") or {}
        for i in part:
            w = works.get(str(i))
            if not w:
                continue
            m = _DATEPATH.search(w.get("url") or "")
            if not m:
                continue
            for pg in range(min(int(w.get("pageCount") or 1), MAX_PAGES)):
                item = f"{i}_p{pg}"
                if item in seen:
                    continue
                stem = f"img-master/img/{m.group(1)}/{m.group(2)}_p{pg}_master1200.jpg"
                out.append({"item": item, "pid": i, "page": pg, "referer": "https://www.pixiv.net/",
                            "thumb": f"{PXIMG}/c/360x360_70/{stem}", "big": f"{PXIMG}/c/600x1200_90/{stem}"})
    return out, len(ids), newest


def run_pixiv(state: dict, stop, keys: list[str] | None = None, max_artists: int = 30, rescan: bool = False):
    """線上工作 kind 'pixivsweep'：一次做幾位畫師（arg keys＝指定的 Pixiv 帳號；沒給就照收藏張數挑還沒看過的）。"""
    _ensure()
    st = STATE
    all_a = artists("pixiv")
    if keys:
        ks = set(keys)
        todo = [a for a in all_a if a["key"] in ks]
    else:
        old = time.time() - RESCAN_DAYS * 86400
        todo = [a for a in all_a if rescan or not a["scanned_at"] or a["scanned_at"] < old][:max_artists]
    st.update(site="pixiv", running=True, artists_total=len(todo), artists_done=0, thumbs=0, checked=0, matched=0, mb=0.0,
              started=time.time(), finished=None, error=None, current=None)
    st.setdefault("matches", [])
    if not todo:
        st.update(running=False, finished=time.time())
        return
    M = Matcher()
    if not M.ok:
        st.update(running=False, finished=time.time(), error="模型沒有載入或沒有要比對的圖")
        return
    seen = _seen("pixiv")
    last = {r[0]: r[1] for r in db.conn().execute("SELECT key, last_max FROM sweep_artist WHERE site='pixiv'")}
    for n, a in enumerate(todo):
        if stop():
            break
        st["current"] = a["artist"]
        state["phase"] = f"Pixiv 畫師作品比對：{a['artist']}"
        try:
            since = int(last.get(a["key"]) or 0) if not rescan else 0
            thumbs, works, newest = pixiv_thumbs(a["key"], since, seen=seen)
        except Exception as e:  # noqa
            _record("pixiv", a["key"], a["artist"], 0, 0, 0, None, str(e)[:200])
            continue
        t0 = st["thumbs"]
        found = _compare("pixiv", M, thumbs, stop, st)
        for f in found:
            try:
                _accept("pixiv", a["artist"], f)
                st["matched"] += 1
                st["matches"].append({"id": f["mid"], "artist": a["artist"], "site": "pixiv", "pid": f["pid"], "conf": f["conf"]})
            except Exception as e:  # noqa
                log.warning("套用 Pixiv 比對結果失敗 %s：%s", f["mid"], e)
        st["matches"] = st["matches"][-200:]
        if not stop():
            _record("pixiv", a["key"], a["artist"], works, st["thumbs"] - t0, len(found), str(newest) if newest else None)
        st["artists_done"] = n + 1
        state.update(done=n + 1, total=len(todo))
    st.update(running=False, finished=time.time(), current=None)


# ---------------- X（瀏覽器腳本送來） ----------------
_XMEDIA = re.compile(r"/media/([A-Za-z0-9_-]{8,})")


def x_artist(handle: str) -> str | None:
    h = handle.lower()
    for a in artists("x"):
        if a["key"] == h:
            return a["artist"]
    return None


def x_queue(handles: list[str] | None = None, limit: int = 200) -> int:
    """X 要你在瀏覽器裡一位一位看：排一個名單給腳本（收藏多的先）。"""
    _ensure()
    rows = artists("x")
    if handles:
        hs = {h.lower() for h in handles}
        rows = [a for a in rows if a["key"] in hs]
    else:
        old = time.time() - RESCAN_DAYS * 86400
        rows = [a for a in rows if not a["scanned_at"] or a["scanned_at"] < old][:limit]
    with db.write_lock:
        c = db.conn()
        c.execute("DELETE FROM sweep_xqueue WHERE status='queued'")
        c.executemany("INSERT OR REPLACE INTO sweep_xqueue(handle, artist, seq, status, at) VALUES(?,?,?,'queued',?)",
                      [(a["key"], a["artist"], i, time.time()) for i, a in enumerate(rows)])
    return len(rows)


def x_next() -> dict | None:
    _ensure()
    r = db.conn().execute("SELECT handle, artist FROM sweep_xqueue WHERE status='queued' ORDER BY seq LIMIT 1").fetchone()
    left = db.conn().execute("SELECT COUNT(*) FROM sweep_xqueue WHERE status='queued'").fetchone()[0]
    return {"handle": r[0], "artist": r[1], "left": left} if r else None


def x_items(handle: str, items: list[dict]) -> list[dict]:
    """腳本送來的 [{tweet_id, url}] → 要比對的縮圖（看過的跳過）。"""
    seen = _seen("x")
    out = []
    for it in items:
        m = _XMEDIA.search(str(it.get("url") or ""))
        tid = re.sub(r"\D", "", str(it.get("tweet_id") or ""))
        if not m or not tid or m.group(1) in seen:
            continue
        key = m.group(1)
        out.append({"item": key, "tweet_id": tid, "handle": handle,
                    "thumb": f"{XIMG}/media/{key}?format=jpg&name=small", "big": f"{XIMG}/media/{key}?format=jpg&name=medium"})
    return list({o["item"]: o for o in out}.values())


def run_x(state: dict, stop, handle: str, items: list[dict], final: bool = False):
    """線上工作 kind 'xsweep'：腳本送來的一批圖片。final＝這位畫師看完了（記錄、從名單拿掉）。"""
    _ensure()
    st = STATE
    artist = x_artist(handle)
    st.update(site="x", running=True, current=artist or handle, error=None)
    st.setdefault("matches", [])
    for k in ("thumbs", "checked", "matched", "mb"):
        st.setdefault(k, 0)
    thumbs = x_items(handle, items) if artist else []
    found = []
    if thumbs:
        M = Matcher()
        if M.ok:
            state["phase"] = f"X 畫師媒體比對：@{handle}"
            found = _compare("x", M, thumbs, stop, st)
            for f in found:
                try:
                    _accept("x", artist, f)
                    st["matched"] += 1
                    st["matches"].append({"id": f["mid"], "artist": artist, "site": "x", "tweet_id": f["tweet_id"], "conf": f["conf"]})
                except Exception as e:  # noqa
                    log.warning("套用 X 比對結果失敗 %s：%s", f["mid"], e)
    _record("x", handle.lower(), artist or handle, len(items), len(thumbs), len(found), None, None if artist else "圖庫裡沒有這個帳號的畫師")
    if final:
        with db.write_lock:
            db.conn().execute("UPDATE sweep_xqueue SET status='done', at=? WHERE handle=?", (time.time(), handle.lower()))
    st.update(running=False, finished=time.time())
