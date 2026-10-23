"""Pixiv：作品資訊、作者對應到 Danbooru 畫師、Pixiv 標籤對應角色。"""
from __future__ import annotations

import logging
import json
import os
import re
import time

from . import db
from .httpclient import BROWSER_UA, NotFound, _req
from .danbooru import DANBOORU, dan_get, save_tag_info

log = logging.getLogger("online.pixiv")

PIXIV = os.environ.get("LIB_PIXIV", "https://www.pixiv.net")



# ---------- Pixiv ----------
TWEET_ID = re.compile(r"(?:^|[^\d])(1\d{17,19})(?:[^\d]|$)")  # X/Twitter 貼文 ID（snowflake）


# Pixiv 新作品（2025 起）的原圖網址多了一段雜湊：146228286-5554454f…b4_p0.jpg，以前認不出作品 ID
PIXIV_PATTERNS = [
    re.compile(r"(?:^|[^\d])(\d{5,10})(?:-[0-9a-f]{32})?_p\d+", re.I),
    re.compile(r"(?:illust_id=|artworks/|pixiv[_\- ]?|illust_)(\d{5,10})", re.I),
]



def pixiv_id_from_name(name: str) -> int | None:
    for p in PIXIV_PATTERNS:
        m = p.search(name)
        if m:
            return int(m.group(1))
    return None



def pixiv_id_from_url(url: str | None) -> int | None:
    if not url:
        return None
    m = re.search(r"pixiv\.net/(?:en/)?artworks/(\d+)|illust_id=(\d+)|pximg\.net/.*/(\d+)(?:-[0-9a-f]{32})?_p\d+", url)
    return int(next(g for g in m.groups() if g)) if m else None



def fetch_pixiv(pid: int) -> dict:
    s = db.get_settings()
    headers = {"User-Agent": BROWSER_UA, "Referer": "https://www.pixiv.net/", "Accept-Language": "ja,en;q=0.8"}
    cookies = {"PHPSESSID": s["pixiv_phpsessid"]} if s["pixiv_phpsessid"] else None
    r = _req("pixiv", "GET", f"{PIXIV}/ajax/illust/{pid}", headers=headers, cookies=cookies)
    try:
        d = r.json()
    except Exception:
        raise RuntimeError(f"Pixiv HTTP {r.status_code}")
    if d.get("error") or not d.get("body"):
        msg = d.get("message") or "作品不存在或需要登入"
        raise NotFound(msg)
    b = d["body"]
    return {
        "id": pid, "title": b.get("illustTitle") or b.get("title"), "user": b.get("userName"), "user_id": b.get("userId"),
        "x_restrict": int(b.get("xRestrict") or 0), "ai": int(b.get("aiType") or 0) == 2,
        "date": (b.get("createDate") or "")[:10],
        "tags": [{"tag": t.get("tag"), "en": (t.get("translation") or {}).get("en")} for t in (b.get("tags") or {}).get("tags", [])],
        "url": f"https://www.pixiv.net/artworks/{pid}",
    }



# ---------- Pixiv 作者 → 創作者 tag ----------
_artist_cache: dict[str, str] = {}



def _slug(name: str) -> str:
    return re.sub(r"\s+", "_", name.strip()).strip("_") or "unknown_artist"



def resolve_pixiv_artist(user_id: str | int | None, user_name: str | None) -> str | None:
    """Pixiv 使用者 → 創作者 tag 名稱。
    順序：本地已知連結 → Danbooru artist（用 Pixiv 網址反查）→ 用 Pixiv 暱稱新建。"""
    if not user_id:
        return None
    uid = str(user_id)
    if uid in _artist_cache:
        return _artist_cache[uid]
    url = f"https://www.pixiv.net/users/{uid}"
    c = db.conn()
    r = c.execute("""SELECT t.name FROM tag_info ti JOIN tags t ON t.id=ti.tag_id
                     WHERE t.category='artist' AND ti.links LIKE ?""", (f'%pixiv.net/users/{uid}"%',)).fetchone()
    if r:
        _artist_cache[uid] = r["name"]
        return r["name"]
    name, info = None, None
    try:
        arts = dan_get("/artists.json", {"search[url_matches]": url, "limit": 1, "only": "id,name,is_deleted,other_names,urls"})
        arts = [a for a in arts if not a.get("is_deleted")]
        if arts:
            a = arts[0]
            name = a["name"]
            info = {"status": "ok", "copyright": None, "copyrights": [], "summary": "", "post_count": None,
                    "other_names": a.get("other_names") or [], "url": f"{DANBOORU}/artists/{a.get('id')}",
                    "links": [u["url"] for u in a.get("urls", []) if u.get("url")] + [url]}
    except Exception as e:  # noqa
        log.info("Danbooru artist 反查失敗：%s", e)
    if not name:
        if not user_name:
            return None
        from . import siteartists            # Danbooru 還沒登記：先用 Pixiv 暱稱，之後登記了自動合併過去
        name = siteartists.create("pixiv", uid, user_name)
        _artist_cache[uid] = name
        return name
    tid = db.tag_id(name, "artist")
    save_tag_info(tid, info)
    _artist_cache[uid] = name
    return name



def _pixiv_character_matches(tags: list[dict]) -> list[str]:
    """把 Pixiv 標籤（常是日文角色名）對應到本地角色：比對 tag 名與角色資料卡的別名。"""
    c = db.conn()
    names = set()
    for t in tags:
        for v in (t.get("tag"), t.get("en")):
            if not v or len(v) < 2:
                continue
            key = v.strip()
            r = c.execute("SELECT name FROM tags WHERE category='character' AND lower(name)=lower(?)",
                          (key.replace(" ", "_"),)).fetchone()
            if r:
                names.add(r["name"])
                continue
            for r in c.execute("""SELECT t.name, ti.other_names FROM tag_info ti JOIN tags t ON t.id=ti.tag_id
                                  WHERE t.category='character' AND ti.other_names LIKE ?""", (f"%{key}%",)):
                if key in json.loads(r["other_names"] or "[]"):
                    names.add(r["name"])
    return sorted(names)



def map_pixiv_tag(pixiv_tag: str, character: str) -> int:
    """把一個 Pixiv 標籤（例如「暁山瑞希」）記為某角色的別名，並重新套用所有帶這個標籤的搜源結果。"""
    from .online import auto_apply, save_source   # 搜源結果怎麼套用在 online.py（避免互相 import）
    tid = db.tag_id(character, "character")
    with db.write_lock:
        c = db.conn()
        r = c.execute("SELECT other_names FROM tag_info WHERE tag_id=?", (tid,)).fetchone()
        if r:
            names = json.loads(r["other_names"] or "[]")
            if pixiv_tag not in names:
                names.append(pixiv_tag)
            c.execute("UPDATE tag_info SET other_names=? WHERE tag_id=?", (json.dumps(names, ensure_ascii=False), tid))
        else:
            c.execute("INSERT INTO tag_info(tag_id, status, other_names, fetched_at) VALUES(?, 'ok', ?, ?)",
                      (tid, json.dumps([pixiv_tag], ensure_ascii=False), time.time()))
        c.commit()
    n = 0
    rows = db.conn().execute("SELECT * FROM media_source WHERE status='found' AND data LIKE ?",
                             (f"%{json.dumps(pixiv_tag, ensure_ascii=False)[1:-1]}%",)).fetchall()
    for r in rows:
        res = {k: r[k] for k in ("status", "method", "similarity", "post_url", "source_url", "pixiv_id")}
        res["data"] = json.loads(r["data"] or "{}")
        pix = res["data"].get("pixiv") or {}
        if not any(t.get("tag") == pixiv_tag for t in pix.get("tags", [])):
            continue
        save_source(r["media_id"], res)  # 重新計算 suggested
        if db.get_settings()["online_auto_apply"]:
            n += len(auto_apply(r["media_id"], res))
    return n
