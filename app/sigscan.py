"""簽名辨識：讀圖上的 @帳號／網址 → 對應到 Danbooru 上已登記的畫師 → 放進「待確認」。

- 文字辨識完全在本機（ocr.py），只把讀到的帳號拿去問 Danbooru「這個網址是哪位畫師」
- 不會新建畫師標籤：Danbooru 沒登記、本機也沒有的帳號只列出來給你參考
- 辨識結果按檔案 MD5 快取，重跑不用重讀
"""
from __future__ import annotations

import json
import logging
import re
import time

from . import db

log = logging.getLogger("sigscan")
SCHEMA = """CREATE TABLE IF NOT EXISTS ocr_text(fp TEXT PRIMARY KEY, texts TEXT, at REAL);
CREATE TABLE IF NOT EXISTS sig_handle2(key TEXT PRIMARY KEY, artist TEXT, checked_at REAL);"""
# WD 會標出「圖上有帳號／簽名」的描述 tag：預設只讀這些圖
SIG_TAGS = ("twitter_username", "artist_name", "signature", "watermark", "web_address", "patreon_username",
            "pixiv_username", "character_name")
STOP = {"twitter", "pixiv", "gmail", "yahoo", "hotmail", "outlook", "icloud", "home", "everyone", "here", "all",
        "fanbox", "patreon", "skeb", "booth", "youtube", "instagram", "tiktok", "twitch", "discord", "weibo"}
MIN_CONF = 0.85

_PATTERNS = [
    ("x", re.compile(r"(?:twitter\.com|x\.com)\s*/\s*@?([A-Za-z0-9_]{2,15})", re.I)),
    ("pixiv", re.compile(r"pixiv\.net/(?:[a-z]{2}/)?users/(\d{3,10})", re.I)),
    ("pixiv", re.compile(r"pixiv\.me/([A-Za-z0-9_-]{3,32})", re.I)),
    ("fanbox", re.compile(r"([a-z0-9][a-z0-9-]{2,30})\.fanbox\.cc", re.I)),
    ("skeb", re.compile(r"skeb\.jp/@([A-Za-z0-9_]{2,32})", re.I)),
    ("patreon", re.compile(r"patreon\.com/([A-Za-z0-9_]{3,40})", re.I)),
    ("bsky", re.compile(r"@?([a-z0-9][a-z0-9-]{2,30})\.bsky\.social", re.I)),
    ("x", re.compile(r"(?<![A-Za-z0-9_.@/])@([A-Za-z0-9_]{3,15})(?![A-Za-z0-9_]|\.bsky)")),
]
URL = {"x": "https://twitter.com/{}", "pixiv": "https://www.pixiv.net/users/{}", "fanbox": "https://{}.fanbox.cc",
       "skeb": "https://skeb.jp/@{}", "patreon": "https://www.patreon.com/{}", "bsky": "https://bsky.app/profile/{}.bsky.social"}


def _ensure():
    with db.write_lock:
        db.conn().executescript(SCHEMA)


def handles(texts: list[tuple[str, float]]) -> list[tuple[str, str]]:
    """從辨識出的文字找帳號：(網站, 帳號)。"""
    out = []
    for t, conf in texts:
        if conf < MIN_CONF:
            continue
        t = re.sub(r"@\s+", "@", re.sub(r"\s*([/.])\s*", r"\1", t))   # 「twitter . com / abc」「@ abc」
        for site, rx in _PATTERNS:
            for m in rx.finditer(t):
                h = m.group(1)
                site_ = "pixiv_me" if site == "pixiv" and not h.isdigit() else site
                if h.lower() in STOP or (h.isdigit() and site_ != "pixiv"):
                    continue
                if (site_, h.lower()) not in {(s, x.lower()) for s, x in out}:
                    out.append((site_, h))
    return out


def _url_has(u: str, site: str, h: str) -> bool:
    """這個網址真的是這個帳號（不分大小寫），避免模糊比對到別人。"""
    u, h = u.lower().rstrip("/"), h.lower()
    if site == "fanbox":
        return f"//{h}.fanbox.cc" in u
    if site == "bsky":
        return f"/{h}.bsky.social" in u
    return u.endswith("/" + h) or u.endswith("/@" + h)


def resolve(site: str, h: str) -> str | None:
    """帳號 → 畫師 tag 名稱（本機已知的連結優先，其次問 Danbooru）。查過的記下來。"""
    from .danbooru import dan_get
    c = db.conn()
    key = f"{site}:{h.lower()}"
    r = c.execute("SELECT artist, checked_at FROM sig_handle2 WHERE key=?", (key,)).fetchone()
    if r and (r[0] or time.time() - r[1] < 30 * 86400):
        return r[0]
    urls = {"x": [f"twitter.com/{h}", f"x.com/{h}"], "pixiv": [f"pixiv.net/users/{h}"],
            "pixiv_me": [f"pixiv.me/{h}"], "fanbox": [f"{h}.fanbox.cc"], "skeb": [f"skeb.jp/@{h}"],
            "patreon": [f"patreon.com/{h}"], "bsky": [f"{h}.bsky.social"]}[site]
    name = None
    for u in urls:      # 本機畫師資料卡裡的連結（LIKE 不分大小寫）
        row = c.execute("""SELECT t.name FROM tag_info ti JOIN tags t ON t.id=ti.tag_id
            WHERE t.category='artist' AND (ti.links LIKE ? OR ti.links LIKE ?)""", (f'%/{u}"%', f'%/{u}/"%')).fetchone()
        if row:
            name = row[0]
            break
    if not name:
        full = URL[site].format(h) if site in URL else f"https://pixiv.me/{h}"
        arts = [a for a in dan_get("/artists.json", {"search[url_matches]": full, "limit": 5, "only": "id,name,is_deleted,other_names,urls"}) or []
                if not a.get("is_deleted") and any(_url_has(x.get("url") or "", site, h) for x in a.get("urls", []))]
        if len(arts) == 1:
            name = arts[0]["name"]
    with db.write_lock:
        c.execute("INSERT OR REPLACE INTO sig_handle2(key, artist, checked_at) VALUES(?,?,?)", (key, name, time.time()))
    return name


def targets(all_images: bool) -> list:
    c = db.conn()
    extra = "" if all_images else f"""AND EXISTS (SELECT 1 FROM media_tags g JOIN tags gt ON gt.id=g.tag_id
        WHERE g.media_id=m.id AND g.state>=0 AND gt.name IN ({",".join("?" * len(SIG_TAGS))}))"""
    return c.execute(f"""SELECT m.id, m.path, m.fp FROM media m
        WHERE m.kind='image' AND m.status='done' AND COALESCE(m.excluded,0)=0
          AND m.id NOT IN (SELECT media_id FROM album_items)
          AND NOT EXISTS (SELECT 1 FROM media_tags x JOIN tags y ON y.id=x.tag_id
                          WHERE x.media_id=m.id AND x.state=1 AND y.category='artist')
          {extra} ORDER BY m.id DESC""", () if all_images else SIG_TAGS).fetchall()


def _texts(r) -> list:
    from . import ocr
    c = db.conn()
    if r["fp"]:
        row = c.execute("SELECT texts FROM ocr_text WHERE fp=?", (r["fp"],)).fetchone()
        if row:
            return json.loads(row[0])
    t = [(s, round(cf, 3)) for s, cf in ocr.text(r["path"])]
    if r["fp"]:
        with db.write_lock:
            c.execute("INSERT OR REPLACE INTO ocr_text(fp, texts, at) VALUES(?,?,?)",
                      (r["fp"], json.dumps(t, ensure_ascii=False), time.time()))
    return t


def _is_character_account(mid: int, artist: str) -> bool:
    """圖上寫的是「圖中角色本人」的帳號（例如 VTuber 同人圖標了 @houshoumarine）→ 那是角色，不是畫這張圖的人。"""
    base = re.sub(r"_\(artist\)$", "", artist)
    if base == artist:
        return False
    return bool(db.conn().execute("""SELECT 1 FROM media_tags mt JOIN tags t ON t.id=mt.tag_id
        WHERE mt.media_id=? AND mt.state>=0 AND t.category='character' AND (t.name=? OR t.name LIKE ?)""",
                                  (mid, base, base + "_(%")).fetchone())


def _cleanup():
    """之前放進待確認、但其實是角色本人帳號的簽名候選 → 拿掉。"""
    c = db.conn()
    rows = c.execute("""SELECT mt.media_id, mt.tag_id, t.name FROM media_tags mt JOIN tags t ON t.id=mt.tag_id
        WHERE mt.source='signature' AND mt.state=0""").fetchall()
    bad = [(r[0], r[1]) for r in rows if _is_character_account(r[0], r[2])]
    if bad:
        with db.write_lock:
            c.executemany("DELETE FROM media_tags WHERE media_id=? AND tag_id=? AND state=0", bad)
    return len(bad)


def run(state: dict, stop, all_images: bool = False, dry_run: bool = False):
    from . import candidates
    _ensure()
    if not dry_run:
        _cleanup()
    st = state.setdefault("sigscan", {})
    rows = targets(all_images)
    st.update(images=len(rows), read=0, with_handle=0, resolved=0, added=0, matches=[], unknown={},
              dry_run=dry_run, all_images=all_images, started=time.time(), finished=None, error=None)
    state.update(phase="讀圖上的簽名", total=len(rows), done=0)
    t0 = time.time()
    for i, r in enumerate(rows):
        if stop():
            break
        try:
            hs = handles(_texts(r))
        except Exception as e:  # noqa
            log.info("文字辨識失敗 %s：%s", r["path"], e)
            if "下載" in str(e) or "HTTP" in type(e).__name__:
                st["error"] = f"文字辨識模型下載失敗：{e}"
                break
            hs = []
        st["read"] += 1
        state["done"] = i + 1
        if i >= 5:
            state["eta"] = round((time.time() - t0) / (i + 1) * (len(rows) - i - 1))
        if not hs:
            continue
        st["with_handle"] += 1
        for site, h in hs:
            try:
                name = resolve(site, h)
            except Exception as e:  # noqa
                log.info("帳號反查失敗 %s：%s", h, e)
                continue
            if not name:
                k = f"{site}:{h}"
                st["unknown"][k] = st["unknown"].get(k, 0) + 1
                continue
            if _is_character_account(r["id"], name):
                st["character_account"] = st.get("character_account", 0) + 1
                continue
            st["resolved"] += 1
            if len(st["matches"]) < 500:
                st["matches"].append({"id": r["id"], "handle": f"{site}:{h}", "artist": name})
            if not dry_run:
                with db.write_lock:
                    if candidates.add(r["id"], name, "artist", candidates.T_DANBOORU, "signature",
                                      reason=f"圖上的簽名：{h}"):
                        st["added"] += 1
            break      # 一張圖對到一位就夠了
    st["unknown"] = dict(sorted(st["unknown"].items(), key=lambda kv: -kv[1])[:100])
    st["finished"] = time.time()
    st["seconds"] = round(st["finished"] - st["started"])
    log.info("簽名辨識完成：%s", {k: v for k, v in st.items() if k not in ("matches", "unknown")})


def one(mid: int) -> str | None:
    """新圖自動搜源時順便做：這張圖（AI 判斷圖上有簽名、還沒有畫師）讀簽名 → 對得上就放進待確認。回傳畫師名稱。"""
    from . import candidates
    _ensure()
    c = db.conn()
    r = c.execute(f"""SELECT m.id, m.path, m.fp FROM media m WHERE m.id=? AND m.kind='image'
          AND NOT EXISTS (SELECT 1 FROM media_tags x JOIN tags y ON y.id=x.tag_id
                          WHERE x.media_id=m.id AND x.state=1 AND y.category='artist')
          AND EXISTS (SELECT 1 FROM media_tags g JOIN tags gt ON gt.id=g.tag_id
                      WHERE g.media_id=m.id AND g.state>=0 AND gt.name IN ({",".join("?" * len(SIG_TAGS))}))""",
                  (mid, *SIG_TAGS)).fetchone()
    if not r:
        return None
    for site, h in handles(_texts(r)):
        name = resolve(site, h)
        if name and not _is_character_account(mid, name):
            with db.write_lock:
                candidates.add(mid, name, "artist", candidates.T_DANBOORU, "signature", reason=f"圖上的簽名：{h}")
            return name
    return None
