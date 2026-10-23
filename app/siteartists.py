"""Danbooru 還沒登記的畫師：先用網站上的帳號建立創作者標籤，之後 Danbooru 登記了就自動合併過去。

- X：用 @帳號（例如 someart）；Pixiv：用暱稱（原本就這樣做）；Rule34／e621：用那個網站的畫師名稱
- 名稱撞到角色／一般標籤時加上 _(x)、_(pixiv)、_(r34)、_(e621)
- 資料卡記下帳號連結（X／Pixiv 個人頁、Rule34／e621 的畫師搜尋），點得到、自己搜得到
- 每 7 天問一次 Danbooru：這個帳號（X／Pixiv 用網址反查，Rule34／e621 用名稱）有沒有登記了？
  有 → 合併到 Danbooru 的正式名稱（圖、確認紀錄一起搬，舊名稱變別名，之後再出現也會導向新名稱）
"""
from __future__ import annotations

import collections
import json
import logging
import re
import time

from . import db

log = logging.getLogger("siteartists")
SCHEMA = """CREATE TABLE IF NOT EXISTS pending_artist(tag TEXT PRIMARY KEY, site TEXT, key TEXT, created_at REAL, checked_at REAL)"""
RECHECK_DAYS = 7
LINK = {"x": "https://x.com/{}", "pixiv": "https://www.pixiv.net/users/{}",
        "r34": "https://rule34.xxx/index.php?page=post&s=list&tags={}", "e621": "https://e621.net/posts?tags={}",
        "ehentai": "https://e-hentai.org/?f_search={}", "nhentai": "https://nhentai.net/search/?q={}",
        "fanbox": "https://{}.fanbox.cc"}
_ready = False


def _ensure():
    global _ready
    if not _ready:
        with db.write_lock:
            c = db.conn()
            c.execute(SCHEMA)
            if "enriched_at" not in [r[1] for r in c.execute("PRAGMA table_info(pending_artist)")]:
                c.execute("ALTER TABLE pending_artist ADD COLUMN enriched_at REAL")   # 找過其他網站帳號的時間
            # 以前用 Pixiv 暱稱建立的創作者也登記進來（之後一起檢查 Danbooru）
            c.execute("""INSERT OR IGNORE INTO pending_artist(tag, site, key, created_at, checked_at)
                         SELECT t.name, 'pixiv', substr(ti.url, length('https://www.pixiv.net/users/') + 1), strftime('%s','now'), 0
                         FROM tags t JOIN tag_info ti ON ti.tag_id=t.id
                         WHERE t.category='artist' AND ti.url LIKE 'https://www.pixiv.net/users/%'""")
            c.commit()
        _ready = True


# 網站上當「畫師」但其實是狀態標記的名稱：不能變成創作者
META = re.compile(r"(request|unknown|anonymous|conditional_dnp|avoid_posting|third-party|ai_generated|ai-generated|"
                  r"sound_warning|artist_upload|uncredited|unidentified|edit$|^tagme$)", re.I)


def is_meta(name: str) -> bool:
    return bool(META.search(name or ""))


def _slug(s: str) -> str:
    s = re.sub(r"\s+", "_", (s or "").strip()).strip("_")
    return s.lower() if s.isascii() else s


def create(site: str, key: str, display: str | None = None) -> str:
    """建立（或找回）這個網站帳號的創作者標籤，回傳標籤名稱。"""
    from .danbooru import save_tag_info
    _ensure()
    c = db.conn()
    link = LINK[site].format(key)
    r = c.execute("""SELECT t.name FROM tag_info ti JOIN tags t ON t.id=ti.tag_id
                     WHERE t.category='artist' AND (ti.links LIKE ? OR ti.url=?)""", (f'%"{link}"%', link)).fetchone()
    if r:
        return r[0]
    name = _slug(key if site != "pixiv" else (display or key))
    ex = c.execute("SELECT category FROM tags WHERE name=?", (name,)).fetchone()
    if ex and ex["category"] != "artist" or not name:
        name = f"{name or key}_({site})"
    tid = db.tag_id(name, "artist")
    names = [x for x in dict.fromkeys([key if site != "pixiv" else None, display]) if x and x != name]
    save_tag_info(tid, {"status": "ok", "copyright": None, "copyrights": [], "summary": "", "post_count": None,
                        "other_names": names, "url": link, "links": [link]})
    with db.write_lock:
        c.execute("INSERT OR IGNORE INTO pending_artist(tag, site, key, created_at, checked_at) VALUES(?,?,?,?,?)",
                  (name, site, key, time.time(), time.time()))
        c.commit()
    from . import artistlinks          # Danbooru 沒有：到其他網站找這個人的其他帳號（背景）
    artistlinks.enrich_later(name)
    return name


def danbooru_name(site: str, key: str) -> str | None:
    """Danbooru 上這個帳號的畫師名稱（還沒登記 → None）。"""
    from .danbooru import dan_get
    if site in ("x", "pixiv"):
        from .sigscan import _url_has
        urls = [f"https://twitter.com/{key}", f"https://x.com/{key}"] if site == "x" else [LINK["pixiv"].format(key)]
        for u in urls:
            arts = [a for a in (dan_get("/artists.json", {"search[url_matches]": u, "limit": 5,
                                                          "only": "id,name,is_deleted,urls"}) or [])
                    if not a.get("is_deleted") and any((_url_has(x.get("url") or "", "x", key) if site == "x"
                                                        else (x.get("url") or "").rstrip("/").endswith(f"/users/{key}"))
                                                       for x in a.get("urls", []))]
            if len(arts) == 1:
                return arts[0]["name"]
        return None
    if site == "fanbox":       # Danbooru 畫師資料登記的 FANBOX 網址（新版 www.fanbox.cc/@名字、舊版 名字.fanbox.cc）
        k = key.lower()
        for u in (f"https://{key}.fanbox.cc", f"https://www.fanbox.cc/@{key}"):
            arts = [a for a in (dan_get("/artists.json", {"search[url_matches]": u, "limit": 5, "only": "id,name,is_deleted,urls"}) or [])
                    if not a.get("is_deleted") and any(re.search(rf"(//{re.escape(k)}\.fanbox\.cc|fanbox\.cc/@{re.escape(k)})(/|$)", (x.get("url") or "").lower())
                                                       for x in a.get("urls", []))]
            if len(arts) == 1:
                return arts[0]["name"]
        return None
    # Rule34／e621：畫師名稱多半跟 Danbooru 一樣（或是 Danbooru 的別名）
    from .r34 import dan_aliases, dan_categories
    if dan_categories([key]).get(key) == "artist":
        return key
    a = dan_aliases([key]).get(key)
    return a if a and dan_categories([a]).get(a) == "artist" else None


def recheck(force: bool = False) -> list[tuple[str, str]]:
    """檢查還沒登記的畫師；Danbooru 有了就合併。回傳 [(舊名稱, 新名稱)]。"""
    s = db.get_settings()
    if not s.get("online_enabled"):
        return []
    _ensure()
    c = db.conn()
    cutoff = time.time() - (0 if force else RECHECK_DAYS * 86400)
    try:                                   # 以前建立、還沒找過其他帳號的，順便補（一次最多 30 個）
        from . import artistlinks
        artistlinks.backfill(limit=30)
    except Exception as e:  # noqa
        log.info("補連結失敗：%s", e)
    rows = c.execute("SELECT tag, site, key FROM pending_artist WHERE checked_at <= ?", (cutoff,)).fetchall()
    merged = []
    for r in rows:
        tag, site, key = r["tag"], r["site"], r["key"]
        if not c.execute("SELECT 1 FROM tags WHERE name=? AND category='artist'", (tag,)).fetchone():
            with db.write_lock:                    # 你已經自己改名／合併／刪掉了
                c.execute("DELETE FROM pending_artist WHERE tag=?", (tag,))
                c.commit()
            continue
        try:
            dn = danbooru_name(site, key)
        except Exception as e:  # noqa
            log.info("檢查 %s 失敗：%s", tag, e)
            continue
        if dn and dn != tag:
            from .api.tags import merge_tags
            try:
                merge_tags(tag, dn)
                merged.append((tag, dn))
                log.info("畫師 %s 在 Danbooru 登記了 → 合併到 %s", tag, dn)
            except Exception as e:  # noqa
                log.warning("合併 %s → %s 失敗：%s", tag, dn, e)
                continue
        with db.write_lock:
            if dn:
                c.execute("DELETE FROM pending_artist WHERE tag=?", (tag,))
            else:
                c.execute("UPDATE pending_artist SET checked_at=? WHERE tag=?", (time.time(), tag))
            c.commit()
    return merged


# ── 本機的重複創作者（b100）─────────────────────────────────────────────
# 同一個人在圖庫裡變成兩個創作者：例如先從 X 匯入用帳號名建了一個，後來 Danbooru 的正式名稱也進來了，
# 而它的資料卡就登記著那個 X 帳號。每 7 天的 Danbooru 檢查只問「這個帳號登記了沒」，問不到（或還沒輪到）就一直分開。
# 這裡只看本機資料卡：兩個創作者的 X／Pixiv／FANBOX 帳號一樣 → 弱的併進強的（可撤銷）。
_ACC = [
    (re.compile(r"(?:x|twitter)\.com/(?!i/|intent/|home|search|hashtag)([A-Za-z0-9_]{1,15})(?:[/?#]|$)", re.I), "x"),
    (re.compile(r"(?:x|twitter)\.com/i/user/(\d+)", re.I), "xid"),
    (re.compile(r"pixiv\.net/(?:en/)?(?:users/|member\.php\?id=|fanbox/creator/)(\d+)", re.I), "pixiv"),
    (re.compile(r"//([a-z0-9_-]+)\.fanbox\.cc", re.I), "fanbox"),
    (re.compile(r"fanbox\.cc/@([a-z0-9_-]+)", re.I), "fanbox"),
]


def accounts(urls) -> set[tuple[str, str]]:
    out = set()
    for u in urls:
        for rx, k in _ACC:
            m = rx.search(u or "")
            if m:
                out.add((k, m.group(1).lower()))
    return out


def local_dupes() -> list[tuple[str, str]]:
    """[(併掉的, 留下的)]。兩個都是 Danbooru 正式登記的不動（Danbooru 自己分開的）；一個帳號對到三個以上也不動（可能是工作室、合同帳號）。
    Rule34／e621 名字建的：名字剛好是另一位 Danbooru 創作者的別名（只有一位）→ 併過去（那兩個網站的畫師名多半照 Danbooru）。"""
    _ensure()
    c = db.conn()
    pend = {r[0]: r[1] for r in c.execute("SELECT tag, site FROM pending_artist")}
    rows = {}
    for r in c.execute("""SELECT t.id, t.name, ti.links, ti.url, ti.status, ti.post_count, ti.other_names,
            (SELECT COUNT(*) FROM media_tags x WHERE x.tag_id=t.id AND x.state=1) AS n
            FROM tags t LEFT JOIN tag_info ti ON ti.tag_id=t.id WHERE t.category='artist'"""):
        rows[r["id"]] = dict(id=r["id"], name=r["name"], n=r["n"], site=pend.get(r["name"]), pending=r["name"] in pend,
                             status=r["status"], post_count=r["post_count"], other=json.loads(r["other_names"] or "[]"),
                             urls=json.loads(r["links"] or "[]") + ([r["url"]] if r["url"] else []))

    def weak(r):               # 用網站帳號建的、或 Danbooru 查不到的（多數正式創作者沒有 post_count，不能拿來判斷）
        return r["pending"] or r["status"] in (None, "notfound")

    def strength(r):
        return (not r["pending"]) + (r["status"] == "ok") + bool(r["post_count"])

    by = collections.defaultdict(set)
    for r in rows.values():
        for a in accounts(r["urls"]):
            by[a].add(r["id"])
    pairs: dict[int, int] = {}
    for s in by.values():
        if len(s) != 2:
            continue
        x, y = (rows[i] for i in s)
        if not weak(x) and not weak(y):
            continue
        src, dst = sorted([x, y], key=lambda r: (strength(r), r["site"] == "pixiv", r["n"], -r["id"]))
        if src["id"] not in pairs and dst["id"] not in pairs:
            pairs[src["id"]] = dst["id"]
    alias = collections.defaultdict(set)
    for r in rows.values():
        if not weak(r):
            for o in r["other"] + [r["name"]]:
                alias[o.lower()].add(r["id"])
    for r in rows.values():
        if r["site"] in ("r34", "e621") and r["id"] not in pairs:
            hit = alias.get(r["name"].lower(), set()) - {r["id"]}
            if len(hit) == 1:
                d = hit.pop()
                if d not in pairs:
                    pairs[r["id"]] = d
    return [(rows[s]["name"], rows[d]["name"]) for s, d in pairs.items()]


def merge_local_dupes() -> list[tuple[str, str]]:
    from .api.tags import merge_tags
    done = []
    for src, dst in local_dupes():
        try:
            merge_tags(src, dst, queue_info=False)
        except Exception as e:  # noqa
            log.warning("合併重複的創作者 %s → %s 失敗：%s", src, dst, e)
            continue
        with db.write_lock:
            c = db.conn()
            c.execute("DELETE FROM pending_artist WHERE tag=?", (src,))
            c.commit()
        log.info("重複的創作者 %s → %s", src, dst)
        done.append((src, dst))
    return done
