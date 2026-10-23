"""Danbooru 還沒登記的創作者：到其他網站找這個人的其他帳號，加進資料卡的連結。

- X 帳號 → FxTwitter 的個人資料：「網站」欄位＋自我介紹裡的創作平台網址（Pixiv、Fanbox、Skeb…）
- Pixiv 使用者 → Pixiv 個人資料：社群帳號欄位（X 等）、網站、自我介紹裡的網址
- Rule34／e621 畫師名稱 → e621 同名畫師登記的網址
- 找到的 X／Pixiv 帳號順便再問一次 Danbooru：本人登記的欄位（X 網站欄、Pixiv 社群欄、e621 畫師資料）
  對上 Danbooru 的畫師 → 合併到正式名稱（跟每 7 天的檢查一樣）；自我介紹裡的網址只當參考連結，不拿來合併
- 你手動編輯過連結的創作者不動
"""
from __future__ import annotations

import json
import logging
import re
import threading
import time

from . import db

log = logging.getLogger("artistlinks")
_lock = threading.Lock()
_q: list[str] = []
_q_names: set[str] = set()
_q_lock = threading.Lock()
_worker = None      # tasks.Task（b93）

# 只收創作者常用的平台（自我介紹裡其他網址多半是別人或無關的）
PLATFORM = re.compile(r"(pixiv\.net/(?:users|member\.php)|fanbox\.cc|skeb\.jp/@|fantia\.jp/(?:fanclubs|[\w-]+$)|patreon\.com/|"
                      r"bsky\.app/profile/|misskey\.io/@|ci-en\.(?:net|dlsite\.com)/creator/|booth\.pm|"
                      r"lit\.link/|linktr\.ee/|potofu\.me/|profcard\.info/|tumblr\.com|artstation\.com/|deviantart\.com/|"
                      r"instagram\.com/|youtube\.com/@|twitch\.tv/|weibo\.com/|lofter\.com|bilibili\.com|(?:twitter|x)\.com/)", re.I)
URL_RX = re.compile(r"(?:https?://)?(?:www\.)?(?:[a-z0-9-]+\.)+[a-z]{2,}(?:/[^\s<>\"'）」、，,]*)?", re.I)


def _norm(u: str) -> str | None:
    u = u.strip().rstrip(".,;:!?)）」")
    if not u or re.search(r"pixiv\.net/stacc|sketch\.pixiv\.net", u, re.I):   # 已停用的舊服務
        return None
    if not re.match(r"^https?://", u, re.I):
        u = "https://" + u
    u = re.sub(r"^http://", "https://", u, flags=re.I)
    u = re.sub(r"^https://(?:www\.|mobile\.)?twitter\.com/", "https://x.com/", u, flags=re.I)
    u = re.sub(r"^https://(?:mobile\.)?x\.com/", "https://x.com/", u, flags=re.I)
    u = re.sub(r"^https://pixiv\.net/", "https://www.pixiv.net/", u, flags=re.I)
    u = re.sub(r"^https://www\.pixiv\.net/(?:en/)?member\.php\?id=(\d+).*", r"https://www.pixiv.net/users/\1", u, flags=re.I)
    u = re.sub(r"^https://www\.pixiv\.net/en/users/", "https://www.pixiv.net/users/", u, flags=re.I)
    m = re.match(r"^https://x\.com/([A-Za-z0-9_]{1,15})(?:[/?#].*)?$", u)
    if m and m.group(1).lower() not in ("i", "intent", "home", "share", "search", "hashtag"):
        u = "https://x.com/" + m.group(1)                     # x.com/i/user/123 這種保留原樣
    return u.rstrip("/")


def _expand_tco(u: str) -> str:
    """t.co 短網址 → 真正的網址（只看轉址，不下載內容）。"""
    if not re.match(r"^https?://t\.co/", u, re.I):
        return u
    from .httpclient import _req
    try:
        r = _req("fxtwitter", "HEAD", u, follow_redirects=False)
        return r.headers.get("location") or u
    except Exception:  # noqa
        return u


def _platform_urls(text: str) -> list[str]:
    out = []
    for m in URL_RX.finditer(text or ""):
        u = _expand_tco(m.group(0)) if "t.co/" in m.group(0) else m.group(0)
        if PLATFORM.search(u):
            n = _norm(u)
            if n:
                out.append(n)
    return out


# ---------- 各網站 ----------
def from_x(handle: str) -> dict:
    from .importer import FX_API
    from .httpclient import _req
    r = _req("fxtwitter", "GET", f"{FX_API}/{handle}")
    if r.status_code != 200:
        return {}
    u = (r.json() or {}).get("user") or {}
    if not u:
        return {}
    web = (u.get("website") or {}).get("url") or (u.get("website") or {}).get("display_url") or ""
    web = _norm(_expand_tco(web)) if web else None
    return {"name": u.get("name"), "own": [x for x in [web] if x], "ref": _platform_urls(u.get("description") or "")}


def from_pixiv(uid: str) -> dict:
    from .httpclient import BROWSER_UA, _req
    from .pixiv import PIXIV
    s = db.get_settings()
    headers = {"User-Agent": BROWSER_UA, "Referer": "https://www.pixiv.net/", "Accept-Language": "ja,en;q=0.8"}
    cookies = {"PHPSESSID": s["pixiv_phpsessid"]} if s.get("pixiv_phpsessid") else None
    r = _req("pixiv", "GET", f"{PIXIV}/ajax/user/{uid}", params={"full": 1}, headers=headers, cookies=cookies)
    try:
        b = (r.json() or {}).get("body") or {}
    except Exception:  # noqa
        return {}
    if not b:
        return {}
    own = []
    social = b.get("social") or {}
    if isinstance(social, dict):
        own += [v.get("url") for v in social.values() if isinstance(v, dict) and v.get("url")]
    if b.get("webpage"):
        own.append(b["webpage"])
    own = [n for n in (_norm(x) for x in own) if n]
    return {"name": b.get("name"), "own": own, "ref": _platform_urls(b.get("comment") or "")}


def from_e621(name: str) -> dict:
    from . import e621
    arts = e621._get("/artists.json", {"search[name]": name, "limit": 1}) or []
    arts = [a for a in arts if isinstance(a, dict) and (a.get("name") or "").lower() == name.lower()]
    if not arts:
        return {}
    a = arts[0]
    urls = a.get("urls")
    if urls is None and a.get("id"):
        urls = (e621._get(f"/artists/{a['id']}.json", {}) or {}).get("urls") or []
    own = [n for n in (_norm(x.get("url") or "") for x in (urls or []) if x.get("is_active", True)) if n]
    return {"name": None, "own": own + [f"https://e621.net/artists/show_or_new?name={name}"], "ref": []}


def _site_key(u: str) -> tuple[str, str] | None:
    m = re.match(r"^https://x\.com/([A-Za-z0-9_]{1,15})$", u)
    if m and m.group(1).lower() not in ("home", "intent", "share", "i", "search", "hashtag"):
        return "x", m.group(1)
    m = re.match(r"^https://www\.pixiv\.net/users/(\d+)", u)
    return ("pixiv", m.group(1)) if m else None


# ---------- 主流程 ----------
def find(site: str, key: str) -> dict:
    """從這個帳號出發找其他帳號。回傳 {own:[本人登記的], ref:[自我介紹裡的], names:[]}。
    X ↔ Pixiv 會再往下查一層（X 網站欄寫了 Pixiv → 再看 Pixiv 的社群欄）。"""
    fn = {"x": from_x, "pixiv": from_pixiv, "r34": from_e621, "e621": from_e621}[site]
    seen = {(site, key.lower())}
    own, ref, names = [], [], []
    # Rule34 的畫師名稱拿去 e621 找同名的：可能不是同一個人 → 只當參考連結，不往下查、不合併
    todo = [(site, key, fn, site != "r34")]
    while todo:
        s, k, f, trusted = todo.pop(0)
        try:
            d = f(k) or {}
        except Exception as e:  # noqa
            log.info("找 %s:%s 的其他帳號失敗：%s", s, k, e)
            continue
        if d.get("name"):
            names.append(d["name"])
        for u in d.get("own", []):
            (own if trusted else ref).append(u)
        ref += d.get("ref", [])
        if trusted:                                   # 只從本人登記的欄位往下查
            for u in d.get("own", []):
                sk = _site_key(u)
                if sk and (sk[0], sk[1].lower()) not in seen and len(seen) < 4:
                    seen.add((sk[0], sk[1].lower()))
                    todo.append((sk[0], sk[1], {"x": from_x, "pixiv": from_pixiv}[sk[0]], True))
    dedup = lambda xs: list(dict.fromkeys(xs))
    own = dedup(own)
    return {"own": own, "ref": [u for u in dedup(ref) if u not in own], "names": dedup(names)}


def enrich(tag: str, dry_run: bool = False, force: bool = False) -> dict:
    """幫一個「Danbooru 還沒登記」的創作者補連結；對上 Danbooru 的話合併。"""
    from . import siteartists
    siteartists._ensure()
    c = db.conn()
    r = c.execute("SELECT site, key FROM pending_artist WHERE tag=?", (tag,)).fetchone()
    if not r:
        return enrich_name(tag, dry_run, force)
    tid = db.tag_id(tag, create=False)
    info = c.execute("SELECT links, other_names, manual FROM tag_info WHERE tag_id=?", (tid,)).fetchone() if tid else None
    res = find(r["site"], r["key"])
    out = {"tag": tag, **res, "added": [], "merged_into": None}
    # 本人登記的 X／Pixiv 帳號對上 Danbooru → 合併
    dans = set()
    for u in res["own"]:
        sk = _site_key(u)
        if sk:
            try:
                dn = siteartists.danbooru_name(*sk)
            except Exception:  # noqa
                dn = None
            if dn:
                dans.add(dn)
    if len(dans) == 1 and not dry_run:
        dn = dans.pop()
        if dn != tag:
            from .api.tags import merge_tags
            try:
                merge_tags(tag, dn)
                with db.write_lock:
                    c.execute("DELETE FROM pending_artist WHERE tag=?", (tag,))
                    c.commit()
                out["merged_into"] = dn
                log.info("畫師 %s：其他帳號在 Danbooru 登記為 %s → 合併", tag, dn)
                tag, tid = dn, db.tag_id(dn, create=False)
                info = c.execute("SELECT links, other_names, manual FROM tag_info WHERE tag_id=?", (tid,)).fetchone()
            except Exception as e:  # noqa
                log.warning("合併 %s → %s 失敗：%s", tag, dn, e)
    elif len(dans) == 1:
        out["merged_into"] = f"（會合併到）{dans.pop()}"
    elif len(dans) > 1:
        out["conflict"] = sorted(dans)                 # 對到好幾個 Danbooru 畫師：不自動合併
    if info is not None and info["manual"] and not force:
        out["skipped"] = "連結手動編輯過，不自動加"
    elif tid:
        old = json.loads((info["links"] if info else None) or "[]")
        new = [u for u in res["own"] + res["ref"] if u not in old]
        names = json.loads((info["other_names"] if info else None) or "[]")
        add_names = [n for n in res["names"] if n and n not in names and n != tag]
        out["added"] = new
        if not dry_run and (new or add_names):
            with db.write_lock:
                c.execute("UPDATE tag_info SET links=?, other_names=? WHERE tag_id=?",
                          (json.dumps(old + new, ensure_ascii=False), json.dumps(names + add_names, ensure_ascii=False), tid))
                c.commit()
    if not dry_run:
        with db.write_lock:
            c.execute("UPDATE pending_artist SET enriched_at=? WHERE tag=?", (time.time(), out["tag"]))
            c.commit()
    return out


HANDLE = re.compile(r"^[A-Za-z0-9_]{3,15}$")


def enrich_name(tag: str, dry_run: bool = False, force: bool = False) -> dict:
    """手動新增、Danbooru 查不到的創作者：只有名字，用名字去找。
    名字剛好是某個 X 帳號／e621 畫師不代表就是同一個人 → 全部只當參考連結、不合併，錯了在 ⋯ 裡刪掉就好。"""
    c = db.conn()
    t = c.execute("SELECT id, category FROM tags WHERE name=?", (tag,)).fetchone()
    if not t or t["category"] != "artist":
        return {"tag": tag, "skipped": "不是創作者"}
    info = c.execute("SELECT links, other_names, manual FROM tag_info WHERE tag_id=?", (t["id"],)).fetchone()
    if info is not None and info["manual"] and not force:
        return {"tag": tag, "skipped": "連結手動編輯過，不自動加"}
    old = json.loads((info["links"] if info else None) or "[]")
    names = json.loads((info["other_names"] if info else None) or "[]")
    found, got_names = [], []
    handles = list(dict.fromkeys(n for n in [tag, tag.replace("_", "")] + names if HANDLE.match(n or "")))[:3]
    for h in handles:
        try:
            d = from_x(h)
        except Exception as e:  # noqa
            log.info("X @%s 查詢失敗：%s", h, e)
            continue
        if d:
            found += [f"https://x.com/{h}"] + d.get("own", []) + d.get("ref", [])
            if d.get("name"):
                got_names.append(d["name"])
            break
    for n in list(dict.fromkeys([tag] + [x for x in names if x.isascii()]))[:2]:
        try:
            d = from_e621(n)
        except Exception as e:  # noqa
            log.info("e621 %s 查詢失敗：%s", n, e)
            continue
        if d:
            found += d.get("own", [])
            break
    new = [u for u in dict.fromkeys(found) if u not in old]
    add_names = [n for n in dict.fromkeys(got_names) if n not in names and n != tag]
    if not dry_run and (new or add_names):
        with db.write_lock:
            if info is None:
                c.execute("INSERT OR IGNORE INTO tag_info(tag_id, status, fetched_at) VALUES(?, 'notfound', ?)", (t["id"], time.time()))
            c.execute("UPDATE tag_info SET links=?, other_names=? WHERE tag_id=?",
                      (json.dumps(old + new, ensure_ascii=False), json.dumps(names + add_names, ensure_ascii=False), t["id"]))
            c.commit()
    return {"tag": tag, "by_name": True, "added": new, "names": add_names}


def enrich_later(tag: str):
    """新建立的創作者：背景補連結（不擋住匯入）。"""
    if not db.get_settings().get("online_enabled"):
        return
    def run():
        with _lock:
            try:
                enrich(tag)
            except Exception as e:  # noqa
                log.info("補連結失敗 %s：%s", tag, e)
    threading.Thread(target=run, daemon=True, name="artist-links").start()


def enrich_name_later(tid: int):
    c = db.conn()
    t = c.execute("SELECT name, category FROM tags WHERE id=?", (tid,)).fetchone()
    if not t or t["category"] != "artist" or not db.get_settings().get("online_enabled"):
        return
    from . import siteartists
    siteartists._ensure()
    if c.execute("SELECT 1 FROM pending_artist WHERE tag=?", (t["name"],)).fetchone():
        return                                         # 網站帳號建立的另外處理
    # 一個背景執行緒依序做（以前每個名字開一個執行緒，一次 40 個在鎖前排隊、各開一個資料庫連線）
    with _q_lock:
        if t["name"] in _q_names:
            return
        _q_names.add(t["name"])
        _q.append(t["name"])
        global _worker
        if _worker is None or _worker.state not in ("running", "waiting"):
            from . import tasks
            _worker = tasks.spawn("artist-links", "補創作者連結", _drain, probe=lambda: {"note": f"還有 {len(_q)} 位"})


def _drain():
    global _worker
    while True:
        with _q_lock:
            if not _q:
                _worker = None
                return
            name = _q.pop(0)
            _q_names.discard(name)
        with _lock:
            try:
                enrich_name(name)
            except Exception as e:  # noqa
                log.info("補連結失敗 %s：%s", name, e)


# 常用的放前面（資料卡最多顯示 10 個網站）
ORDER = ["x.com/", "pixiv.net/users", "fanbox.cc", "pixiv.net/fanbox", "skeb.jp", "fantia.jp", "patreon.com", "bsky.app", "misskey", "ci-en", "booth.pm",
         "lit.link", "linktr.ee", "potofu.me", "instagram.com", "tumblr.com", "artstation.com", "deviantart.com", "youtube.com"]


def _rank(u: str) -> tuple:
    return (next((i for i, k in enumerate(ORDER) if k in u), len(ORDER)), "x.com/i/" in u)


def backfill(limit: int = 200, dry_run: bool = False) -> list[dict]:
    """以前建立、還沒找過其他帳號的創作者。"""
    from . import siteartists
    siteartists._ensure()
    rows = db.conn().execute("SELECT tag FROM pending_artist WHERE enriched_at IS NULL ORDER BY created_at DESC LIMIT ?",
                             (limit,)).fetchall()
    out = []
    with _lock:
        for r in rows:
            try:
                out.append(enrich(r["tag"], dry_run=dry_run))
            except Exception as e:  # noqa
                out.append({"tag": r["tag"], "error": str(e)})
    return out
