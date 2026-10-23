"""Rule34（rule34.xxx）原檔比對：用檔案 MD5 查，只送出指紋，不上傳圖片。

Rule34 收很多 Danbooru 不收的 NSFW 圖，但它的標籤是自由填寫、命名常跟 Danbooru 不同，所以：
- 對上了 → 記成來源（連結到 Rule34 貼文），算「找到出處」
- 畫師／角色／作品標籤：只有 Danbooru 上真的有同名、同類別的標籤，才放進「待確認」
- 對不上的只列在來源區塊給你參考，不會新建標籤
需要 Rule34 帳號的 API key 和 user ID（rule34.xxx 從 2025 年起 API 一律要帶）。
"""
from __future__ import annotations

import logging
import os
import re
import time

from . import db

log = logging.getLogger("r34")
API = os.environ.get("LIB_R34", "https://api.rule34.xxx")
SITE = "https://rule34.xxx"
SCHEMA = """CREATE TABLE IF NOT EXISTS r34_checked(fp TEXT PRIMARY KEY, post_id INTEGER, checked_at REAL);
CREATE TABLE IF NOT EXISTS r34_tagtype(name TEXT PRIMARY KEY, type INTEGER, dan TEXT, checked_at REAL);"""
RETRY_DAYS = 30
_ready = False


class AuthError(Exception):
    pass


def _ensure():
    global _ready
    if not _ready:
        with db.write_lock:
            db.conn().executescript(SCHEMA)
        _ready = True


def configured() -> bool:
    s = db.get_settings()
    return bool(s.get("r34_user_id") and s.get("r34_api_key"))


def _get(params: dict):
    from .httpclient import _req
    s = db.get_settings()
    r = _req("r34", "GET", API + "/index.php", params={"page": "dapi", **params,
                                                       "api_key": s.get("r34_api_key", ""), "user_id": s.get("r34_user_id", "")})
    txt = r.text.strip()
    if r.status_code in (401, 403) or "authentication" in txt[:200].lower() or "api key" in txt[:200].lower():
        raise AuthError("Rule34 的 API key 或 user ID 不正確")
    if r.status_code >= 400:
        raise RuntimeError(f"Rule34 HTTP {r.status_code}")
    return txt


def by_md5(md5: str) -> dict | None:
    import json
    txt = _get({"s": "post", "q": "index", "json": 1, "limit": 1, "tags": f"md5:{md5}"})
    if not txt or txt in ("[]", '""'):
        return None
    try:
        d = json.loads(txt)
    except ValueError:
        return None
    return d[0] if isinstance(d, list) and d else None


def dan_categories(names: list[str]) -> dict[str, str]:
    """Danbooru 上這些標籤的類別（artist / character / copyright / general…；Danbooru 沒有的 → ""）。
    Rule34 的標籤 API 一次只能查一個（每秒 1 次太慢），而且我們本來就只收 Danbooru 也有的標籤，所以直接問 Danbooru。
    查過的記在 r34_tagtype（30 天）。"""
    from .danbooru import dan_get
    _ensure()
    c = db.conn()
    cutoff = time.time() - RETRY_DAYS * 86400
    out = {}
    for i in range(0, len(names), 400):
        ch = names[i:i + 400]
        out.update({r[0]: r[1] for r in c.execute(
            f"SELECT name, dan FROM r34_tagtype WHERE dan IS NOT NULL AND checked_at > ? AND name IN ({','.join('?' * len(ch))})",
            [cutoff, *ch])})
    miss = [n for n in names if n not in out]
    for n in list(miss):          # 本機已經有、類別明確的，不用問
        r = c.execute("SELECT category FROM tags WHERE name=? AND category IN ('artist','character','copyright')", (n,)).fetchone()
        if r:
            out[n] = r[0]
            miss.remove(n)
    names_cat = {0: "general", 1: "artist", 3: "copyright", 4: "character", 5: "meta"}
    for i in range(0, len(miss), 40):
        ch = miss[i:i + 40]
        rows = dan_get("/tags.json", {"search[name_comma]": ",".join(ch), "limit": 100,
                                      "only": "name,category,post_count"}) or []
        got = {r["name"]: (names_cat.get(r.get("category"), "") if (r.get("post_count") or 0) > 0 else "") for r in rows}
        now = time.time()
        with db.write_lock:
            c.executemany("INSERT OR REPLACE INTO r34_tagtype(name, type, dan, checked_at) VALUES(?,NULL,?,?)",
                          [(n, got.get(n, ""), now) for n in ch])
        out.update({n: got.get(n, "") for n in ch})
    return out


# 平台名稱在 Danbooru 也是「作品」類，但不是作品
PLATFORMS = {"patreon", "fanbox", "pixiv_fanbox", "twitter", "pixiv", "fantia", "gumroad", "skeb", "ko-fi", "subscribestar",
             "onlyfans", "deviantart", "tumblr", "instagram", "youtube", "tiktok", "discord", "boosty", "ci-en"}


def dan_aliases(names: list[str]) -> dict[str, str]:
    """Danbooru 的別名：asuka_langley_sohryu → souryuu_asuka_langley（Rule34 常用的是別名）。"""
    from .danbooru import dan_get
    out = {}
    for i in range(0, len(names), 40):
        ch = names[i:i + 40]
        try:
            rows = dan_get("/tag_aliases.json", {"search[antecedent_name_comma]": ",".join(ch), "search[status]": "active",
                                                 "limit": 100, "only": "antecedent_name,consequent_name"}) or []
        except Exception:  # noqa
            continue
        out.update({r["antecedent_name"]: r["consequent_name"] for r in rows if r.get("antecedent_name") in ch})
    return out


def summarize(p: dict) -> dict:
    tags = [t for t in (p.get("tags") or "").split() if t]
    cats = dan_categories(tags)
    miss = [t for t in tags if cats.get(t) == ""]
    alias = dan_aliases(miss) if miss else {}
    acats = dan_categories(list(set(alias.values()))) if alias else {}
    mapped, unmapped, seen = [], [], set()
    for t in tags:
        name, cat = (t, cats.get(t)) if cats.get(t) else (alias.get(t), acats.get(alias.get(t), "")) if t in alias else (t, "")
        if name != t and cat:
            # 別名對過去的要多確認一次：Rule34 的 chilling（一般詞）在 Danbooru 是某位畫師的別名 → 不能用。
            # 只收角色／作品，而且 Rule34 自己也要說它是同一類；original_character 不等於原創作品
            if cat not in ("character", "copyright") or name == "original" or r34_type(t) != cat:
                cat = ""
                name = t
        if cat in ("artist", "character", "copyright") and name not in PLATFORMS:
            if name not in seen:
                seen.add(name)
                mapped.append({"name": name, "category": cat, **({"r34": t} if name != t else {})})
        elif not cat:
            unmapped.append({"name": t, "category": ""})   # 多半是 Rule34 自己的角色／畫師寫法，列出來參考
    # Danbooru 沒有的：問 Rule34 這個標籤是什麼類別（一次只能問一個，所以只問前 25 個；只有找到出處時才會走到這裡），
    # 只留畫師／角色／作品給你參考，一般描述詞不列
    keep = []
    for t in list(dict.fromkeys(x["name"] for x in unmapped))[:25]:
        cat = r34_type(t)
        if cat:
            keep.append({"name": t, "category": cat})
    return {"post_id": p.get("id"), "post_url": f"{SITE}/index.php?page=post&s=view&id={p.get('id')}",
            "rating": p.get("rating"), "source": p.get("source") or "", "mapped": mapped, "unmapped": keep,
            "general": [t for t in tags if cats.get(t) == "general"]}     # Danbooru 也有、而且是一般標籤的


_TAG_TYPE = {1: "artist", 3: "copyright", 4: "character"}


def r34_type(name: str) -> str:
    """Rule34 上這個標籤的類別（畫師／作品／角色，其他 → ""），查過的記下來。"""
    import html
    c = db.conn()
    r = c.execute("SELECT type FROM r34_tagtype WHERE name=? AND type IS NOT NULL", (name,)).fetchone()
    if r:
        return _TAG_TYPE.get(r[0], "")
    txt = _get({"s": "tag", "q": "index", "limit": 5, "name": name})
    typ = 0
    for m in re.finditer(r"<tag\b([^>]*)/?>", txt):
        a = dict(re.findall(r'(\w+)="([^"]*)"', m.group(1)))
        if html.unescape(a.get("name", "")) == name:
            typ = int(a.get("type") or 0)
    with db.write_lock:
        c.execute("""INSERT INTO r34_tagtype(name, type, dan, checked_at) VALUES(?,?,NULL,?)
                     ON CONFLICT(name) DO UPDATE SET type=excluded.type""", (name, typ, time.time()))
    return _TAG_TYPE.get(typ, "")


def apply(mid: int, info: dict, dry_run: bool = False) -> int:
    """記成來源＋把對得上的標籤放進待確認。回傳放進待確認的數量。"""
    from . import candidates
    from .online import save_source
    if dry_run:
        return len(info["mapped"])
    src = info["source"].split()[0] if info["source"] else None
    save_source(mid, {"status": "found", "method": "r34_md5", "similarity": 100.0, "post_url": info["post_url"],
                      "source_url": src if src and src.startswith("http") else None, "pixiv_id": None,
                      "data": {"rule34": info}})
    n = 0
    with db.write_lock:
        for t in info["mapped"]:
            n += bool(candidates.add(mid, t["name"], t["category"], candidates.T_DANBOORU, "rule34",
                                     reason="Rule34 原檔（標籤僅供參考）"))
        if info.get("general"):
            from . import sitetags
            sitetags.apply_general(mid, info["general"], "rule34", exact=False)
    if not any(t["category"] == "artist" for t in info["mapped"]):
        from . import siteartists       # Danbooru 還沒登記的畫師：先用 Rule34 的名稱，之後登記了自動合併
        for u in info.get("unmapped", []):
            if u.get("category") == "artist" and not siteartists.is_meta(u["name"]):
                name = siteartists.create("r34", u["name"])
                with db.write_lock:
                    n += bool(candidates.add(mid, name, "artist", candidates.T_DANBOORU, "rule34",
                                             reason="Rule34 畫師（Danbooru 還沒登記）"))
                break
    return n


def targets(nsfw_only: bool) -> list:
    """還沒找到出處、最近沒查過 Rule34 的圖（NSFW 優先）。"""
    from .query import NSFW
    _ensure()
    cutoff = time.time() - RETRY_DAYS * 86400
    extra = f"AND {NSFW}=1" if nsfw_only else ""
    return db.conn().execute(f"""SELECT m.id, m.fp, {NSFW} nsfw FROM media m
        WHERE m.kind='image' AND m.status='done' AND COALESCE(m.excluded,0)=0 AND m.fp IS NOT NULL AND m.fp NOT LIKE 'v:%'
          AND m.id NOT IN (SELECT media_id FROM album_items)
          AND NOT EXISTS (SELECT 1 FROM media_source s WHERE s.media_id=m.id AND s.status='found')
          AND NOT EXISTS (SELECT 1 FROM r34_checked k WHERE k.fp=m.fp AND k.checked_at > ?) {extra}
        ORDER BY nsfw DESC, m.id DESC""", (cutoff,)).fetchall()


def run(state: dict, stop, nsfw_only: bool = False, dry_run: bool = False, limit: int = 0):
    _ensure()
    st = state.setdefault("r34scan", {})
    rows = targets(nsfw_only)
    if limit:
        rows = rows[:limit]
    st.update(images=len(rows), checked=0, found=0, tags_added=0, matches=[], dry_run=dry_run, nsfw_only=nsfw_only,
              started=time.time(), finished=None, error=None)
    state.update(phase="Rule34 原檔比對", total=len(rows), done=0)
    t0 = time.time()
    c = db.conn()
    for i, r in enumerate(rows):
        if stop():
            break
        try:
            p = by_md5(r["fp"])
            info = summarize(p) if p else None
        except AuthError as e:
            st["error"] = str(e)
            break
        except Exception as e:  # noqa
            log.info("Rule34 查詢失敗：%s", e)
            st["error"] = str(e)[:200]
            continue
        st["checked"] += 1
        state["done"] = i + 1
        if i >= 5:
            state["eta"] = round((time.time() - t0) / (i + 1) * (len(rows) - i - 1))
        if not dry_run:
            with db.write_lock:
                c.execute("INSERT OR REPLACE INTO r34_checked(fp, post_id, checked_at) VALUES(?,?,?)",
                          (r["fp"], info["post_id"] if info else None, time.time()))
        if not info:
            continue
        st["found"] += 1
        st["tags_added"] += apply(r["id"], info, dry_run)
        if len(st["matches"]) < 500:
            st["matches"].append({"id": r["id"], "post_id": info["post_id"],
                                  "mapped": [t["name"] for t in info["mapped"]],
                                  "unmapped": [t["name"] for t in info["unmapped"]]})
    st["finished"] = time.time()
    st["seconds"] = round(st["finished"] - st["started"])
    log.info("Rule34 比對完成：%s", {k: v for k, v in st.items() if k != "matches"})


def selftest() -> dict:
    """拿最新一篇貼文的 MD5 反查自己：確認 MD5 搜尋正常（診斷用）。"""
    import json
    d = json.loads(_get({"s": "post", "q": "index", "json": 1, "limit": 1}) or "[]")
    if not d:
        return {"ok": False, "msg": "拿不到任何貼文"}
    p = d[0]
    back = by_md5(p.get("hash") or "")
    info = summarize(p)
    return {"ok": bool(back and back.get("id") == p.get("id")), "post_id": p.get("id"),
            "mapped": info["mapped"], "unmapped": [t["name"] for t in info["unmapped"]]}


def check_one(mid: int) -> dict:
    """大圖檢視的「Rule34」按鈕：查這一張（不看 30 天內查過的紀錄）。"""
    _ensure()
    r = db.conn().execute("SELECT fp FROM media WHERE id=?", (mid,)).fetchone()
    if not r or not r[0] or r[0].startswith("v:"):
        return {"found": False, "msg": "這個檔案沒有指紋"}
    p = by_md5(r[0])
    with db.write_lock:
        db.conn().execute("INSERT OR REPLACE INTO r34_checked(fp, post_id, checked_at) VALUES(?,?,?)",
                          (r[0], p.get("id") if p else None, time.time()))
    if not p:
        return {"found": False}
    info = summarize(p)
    n = apply(mid, info)
    return {"found": True, "post_id": info["post_id"], "added": n, "mapped": info["mapped"], "unmapped": info["unmapped"]}


def test() -> dict:
    """設定頁的「測試」：查一個一定存在的 MD5 以外的簡單請求，確認 key 可用。"""
    try:
        _get({"s": "post", "q": "index", "json": 1, "limit": 1, "tags": "rating:safe"})
        return {"ok": True, "msg": "Rule34 連線成功"}
    except AuthError as e:
        return {"ok": False, "msg": str(e)}
    except Exception as e:  # noqa
        return {"ok": False, "msg": f"連不上 Rule34：{e}"}
