"""e621（furry 圖最大的圖站）：原檔比對＋以圖搜圖。

- 原檔比對：用檔案 MD5 查，一次最多 100 個（e621 的 md5: 可以逗號分隔），只送出指紋、不上傳圖片
- 以圖搜圖（e621 自己的 IQDB）：上傳縮小的圖；有帳號時每 10 秒 6 張，沒帳號每分鐘 1 張
- 標籤：e621 的標籤有分類（畫師／角色／物種／作品）。只有 Danbooru 上也有同名、同類別的才放進「待確認」；
  寶可夢等 e621 當成「物種」的（lucario），Danbooru 是「角色」→ 也可以對應。其他只列出來參考，不新建標籤。
需要 e621 帳號名稱＋API key（帳號設定 → API Keys）；e621 規定要帶能辨識的 User-Agent。
"""
from __future__ import annotations

import io
import logging
import os
import time

from . import db

log = logging.getLogger("e621")
API = os.environ.get("LIB_E621", "https://e621.net")
SCHEMA = "CREATE TABLE IF NOT EXISTS e621_checked(fp TEXT PRIMARY KEY, post_id INTEGER, checked_at REAL)"
RETRY_DAYS = 30
BATCH = 100
# e621 的「畫師」欄裡有一些不是畫師的標記
META_ARTISTS = {"conditional_dnp", "avoid_posting", "unknown_artist", "anonymous_artist", "sound_warning",
                "third-party_edit", "epilepsy_warning", "unknown_artist_signature", "e621", "creator_unknown"}
# 公司名在 Danbooru 也是「作品」類，但不是作品
COMPANIES = {"nintendo", "game_freak", "the_pokemon_company", "creatures_inc.", "sega", "sony", "microsoft", "disney",
             "warner_brothers", "dreamworks", "hasbro", "cartoon_network", "netflix", "valve", "blizzard_entertainment",
             "fromsoftware", "bandai_namco", "capcom", "square_enix", "hoyoverse", "mihoyo", "cygames", "nexon"}
_ready = False


class AuthError(Exception):
    pass


def _ensure():
    global _ready
    if not _ready:
        with db.write_lock:
            db.conn().execute(SCHEMA)
        _ready = True


def configured() -> bool:
    s = db.get_settings()
    return bool(s.get("e621_login") and s.get("e621_api_key"))


def _auth():
    s = db.get_settings()
    return (s["e621_login"], s["e621_api_key"]) if configured() else None


def _ua() -> str:
    s = db.get_settings()
    who = s.get("e621_login") or "anonymous"
    return f"PickleJar/1.1 (local image organizer; by {who} on e621)"


def _check(r):
    if r.status_code in (401, 403):
        raise AuthError("e621 的帳號名稱或 API key 不正確")
    if r.status_code == 429 or r.status_code == 503:
        raise RuntimeError("e621 限速中，稍後再試")
    if r.status_code >= 400:
        raise RuntimeError(f"e621 HTTP {r.status_code}")


def _get(path: str, params: dict):
    from .httpclient import _req
    r = _req("e621", "GET", API + path, params=params, auth=_auth(), headers={"User-Agent": _ua()})
    _check(r)
    return r.json()


def by_md5(md5s: list[str]) -> dict[str, dict]:
    """MD5 → e621 貼文（一次最多 100 個）。"""
    out = {}
    for i in range(0, len(md5s), BATCH):
        ch = md5s[i:i + BATCH]
        d = _get("/posts.json", {"tags": "md5:" + ",".join(ch), "limit": len(ch)})
        for p in d.get("posts", []):
            m = ((p.get("file") or {}).get("md5") or "").lower()
            if m:
                out[m] = p
    return out


def summarize(p: dict) -> dict:
    """e621 貼文 → 來源資訊＋可以對應到 Danbooru 的標籤。"""
    from .r34 import PLATFORMS, dan_aliases, dan_categories
    tg = p.get("tags") or {}
    raw = [(t, "artist") for t in tg.get("artist", []) if t not in META_ARTISTS]
    raw += [(t, "character") for t in tg.get("character", [])]
    raw += [(t, "species") for t in tg.get("species", [])]
    raw += [(t, "copyright") for t in tg.get("copyright", []) if t not in COMPANIES and t not in PLATFORMS]
    names = [t for t, _ in raw]
    cats = dan_categories(names) if names else {}
    miss = [t for t, c in raw if not cats.get(t)]
    alias = dan_aliases(miss) if miss else {}
    acats = dan_categories(list(set(alias.values()))) if alias else {}
    mapped, unmapped, seen = [], [], set()
    for t, ecat in raw:
        name, dcat = t, cats.get(t, "")
        if not dcat and t in alias:
            name, dcat = alias[t], acats.get(alias[t], "")
        # 同名但類別要合：e621 的物種（lucario）在 Danbooru 是角色可以；一般詞、畫師↔角色對不上的不收
        want = {"artist": ("artist",), "character": ("character",), "species": ("character",),
                "copyright": ("copyright",)}[ecat]
        if dcat in want and name not in COMPANIES and name != "original":
            if name not in seen:
                seen.add(name)
                mapped.append({"name": name, "category": dcat, **({"e621": t} if name != t else {})})
        elif ecat != "species":        # 物種對不上的多半是一般動物（wolf、fox），不列
            unmapped.append({"name": t, "category": ecat})
    src = [u for u in (p.get("sources") or []) if isinstance(u, str) and u.startswith("http")]
    gen = tg.get("general", [])[:120]
    gcats = dan_categories(gen) if gen else {}
    return {"post_id": p.get("id"), "post_url": f"{API}/posts/{p.get('id')}", "rating": p.get("rating"),
            "source": src[0] if src else "", "mapped": mapped, "unmapped": unmapped[:30],
            "general": [t for t in gen if gcats.get(t) == "general"]}     # e621 用語跟 Danbooru 不同：只收 Danbooru 也有的


def apply(mid: int, info: dict, method: str = "e621_md5", similarity: float = 100.0) -> int:
    from . import candidates
    from .online import save_source
    save_source(mid, {"status": "found", "method": method, "similarity": similarity, "post_url": info["post_url"],
                      "source_url": info["source"] or None, "pixiv_id": None, "data": {"e621": info}})
    n = 0
    with db.write_lock:
        for t in info["mapped"]:
            n += bool(candidates.add(mid, t["name"], t["category"], candidates.T_DANBOORU, "e621",
                                     reason="e621 原檔" if method == "e621_md5" else f"e621 以圖搜圖 {similarity:.0f}%"))
        if info.get("general"):
            from . import sitetags
            sitetags.apply_general(mid, info["general"], "e621", exact=False)
        if info.get("rating") == "e":       # e621 標為 explicit → NSFW（不覆蓋手動設定）
            db.conn().execute("UPDATE media SET nsfw_manual=1 WHERE id=? AND nsfw_manual IS NULL", (mid,))
    if not any(t["category"] == "artist" for t in info["mapped"]):
        from . import siteartists       # Danbooru 還沒登記的畫師：先用 e621 的名稱，之後登記了自動合併
        for u in info.get("unmapped", []):
            if u.get("category") == "artist" and not siteartists.is_meta(u["name"]):
                name = siteartists.create("e621", u["name"])
                with db.write_lock:
                    n += bool(candidates.add(mid, name, "artist", candidates.T_DANBOORU, "e621",
                                             reason="e621 畫師（Danbooru 還沒登記）"))
                break
    return n


def check_one(mid: int) -> dict:
    """單張：先 MD5；找不到就不動（以圖搜圖另外按）。"""
    _ensure()
    r = db.conn().execute("SELECT fp FROM media WHERE id=?", (mid,)).fetchone()
    if not r or not r[0] or r[0].startswith("v:"):
        return {"found": False}
    p = by_md5([r[0]]).get(r[0])
    with db.write_lock:
        db.conn().execute("INSERT OR REPLACE INTO e621_checked(fp, post_id, checked_at) VALUES(?,?,?)",
                          (r[0], p.get("id") if p else None, time.time()))
    if not p:
        return {"found": False}
    info = summarize(p)
    return {"found": True, "post_id": info["post_id"], "added": apply(mid, info), "method": "e621_md5"}


IQDB_MIN = 90      # e621 IQDB 分數（0–100）：90 以上才採用


def iqdb(mid: int) -> dict:
    """單張以圖搜圖：上傳縮小到 1000px 的 JPEG 到 e621 的 IQDB。找到 ≥ 90 分的就記成出處。"""
    from PIL import Image
    from . import media
    from .httpclient import _req
    r = db.conn().execute("SELECT path FROM media WHERE id=?", (mid,)).fetchone()
    if not r:
        return {"found": False}
    im = media.to_rgb(media.load_image(__import__("pathlib").Path(r[0])))
    im.thumbnail((1000, 1000), Image.LANCZOS)
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=90)
    resp = _req("e621_iqdb", "POST", API + "/iqdb_queries.json", auth=_auth(), headers={"User-Agent": _ua()},
                files={"file": ("image.jpg", buf.getvalue(), "image/jpeg")})
    _check(resp)
    hits = resp.json()
    if isinstance(hits, dict):
        hits = hits.get("posts") or hits.get("matches") or []
    best = None
    for h in hits or []:
        post = h.get("post", {}).get("posts") if isinstance(h.get("post"), dict) and "posts" in h.get("post", {}) else h.get("post") or h
        score = float(h.get("score") or 0)
        if post and post.get("id") and (best is None or score > best[0]):
            best = (score, post)
    if not best:
        return {"found": False, "best": None}
    score, post = best
    if score < IQDB_MIN:
        return {"found": False, "best": {"post_id": post.get("id"), "score": round(score, 1)}}
    if not post.get("tags"):
        post = _get(f"/posts/{post['id']}.json", {}).get("post") or post
    info = summarize(post)
    return {"found": True, "post_id": info["post_id"], "score": round(score, 1),
            "added": apply(mid, info, "e621_iqdb", round(score, 1)), "method": "e621_iqdb"}


def selftest() -> dict:
    """診斷用：拿最新一篇貼文的 MD5，混在其他假的 MD5 裡一起查，確認批次查詢正常。"""
    d = _get("/posts.json", {"limit": 1})
    p = (d.get("posts") or [{}])[0]
    m = (p.get("file") or {}).get("md5")
    if not m:
        return {"ok": False, "msg": "拿不到貼文"}
    got = by_md5(["0" * 32, m, "f" * 32])
    return {"ok": m in got and got[m].get("id") == p.get("id"), "post_id": p.get("id"),
            "mapped": summarize(p)["mapped"], "unmapped": [t["name"] for t in summarize(p)["unmapped"]][:10]}


def test() -> dict:
    try:
        _get("/posts.json", {"tags": "id:1", "limit": 1})
        if not configured():
            return {"ok": True, "msg": "連得上 e621（沒有帳號：以圖搜圖每分鐘只能 1 張）"}
        return {"ok": True, "msg": "e621 連線成功"}
    except AuthError as e:
        return {"ok": False, "msg": str(e)}
    except Exception as e:  # noqa
        return {"ok": False, "msg": f"連不上 e621：{e}"}
