"""網站上的標籤：
1. 一般描述標籤（雙馬尾、泳裝…）：Danbooru 的是人工整理的，比 WD Tagger 猜的準。
   - 原檔完全一樣（MD5、像素指紋、X 圖片 key）→ 直接用 Danbooru 的，AI 猜、你還沒檢查過的一般標籤裡 Danbooru 沒有的收掉
   - 相似圖（IQDB）、同一篇推文／Pixiv 作品（可能是別頁）→ 加進來但標成「未檢查」，AI 的不動
   - Rule34／e621：只收 Danbooru 也有、而且在 Danbooru 是一般標籤的名稱，標成未檢查
   你否決過的、設成停止自動標註的、你手動加的都不會被動到。
2. 日文標籤 → 角色／作品（Pixiv 標籤、X 的 hashtag）：用 Danbooru wiki 的「其他名稱」對應
   （暁山瑞希 → akiyama_mizuki、ブルアカ → blue_archive）。只收 Danbooru 上是角色或作品的，當候選等你確認。
   其他名稱裡常有配對名（みずえな），所以一律只是候選，不直接確認。查過的記下來。
"""
from __future__ import annotations

import json
import logging
import re
import time

from . import db

log = logging.getLogger("sitetags")
SCHEMA = "CREATE TABLE IF NOT EXISTS jp_tag_map(text TEXT PRIMARY KEY, names TEXT, checked_at REAL)"
EXACT_METHODS = ("md5", "md5_name", "pixel_hash", "x_media")     # 這些就是同一張圖：Danbooru 的標籤直接算數
# Danbooru 沒標、AI 分數很高的不收掉：Danbooru 常漏標（blush、smile…）。開發時的圖庫實測：
# AI 0.9 以上的一般標籤有 85% Danbooru 也有、0.8–0.9 是 65%，0.65 以下只有 24–38%
KEEP_WD = 0.8
RETRY_DAYS = 30
_ready = False


def _ensure():
    global _ready
    if not _ready:
        with db.write_lock:
            db.conn().execute(SCHEMA)
        _ready = True


# ---------- 1. 一般標籤 ----------
def apply_general(mid: int, names: list[str], source: str, exact: bool) -> dict:
    """把網站的一般標籤寫進這張圖。exact=True：同一張圖、Danbooru 人工整理 → 已檢查，並收掉 AI 猜、Danbooru 沒有的。
    必須在 db.write_lock 內呼叫。"""
    from .matcher import matcher
    c = db.conn()
    names = [n for n in dict.fromkeys(names) if n and n not in matcher.blocked]
    have = {r["name"]: r for r in c.execute("""SELECT t.name, mt.state, mt.source, mt.score FROM media_tags mt JOIN tags t ON t.id=mt.tag_id
                                              WHERE mt.media_id=? AND t.category='general'""", (mid,))}
    added = confirmed = removed = 0
    state = 1 if exact else 0
    for n in names:
        h = have.get(n)
        if h is not None and (h["state"] == -1 or h["source"] == "manual" or h["state"] >= state):
            continue                      # 你否決過／手動加的／已經是同樣或更確定的 → 不動
        tid = db.tag_id(n, "general")
        if db.conn().execute("SELECT category FROM tags WHERE id=?", (tid,)).fetchone()[0] != "general":
            continue                      # 本機同名的是角色／作品等身分標籤 → 不當一般標籤
        c.execute("""INSERT OR REPLACE INTO media_tags(media_id, tag_id, score, source, state, tier, reason)
                     VALUES(?,?,1.0,?,?,NULL,?)""", (mid, tid, source, state, "Danbooru 原檔的標籤" if exact else f"{source} 的標籤"))
        added += h is None
        confirmed += h is not None
    if exact and names:
        keep = set(names)
        for n, h in have.items():
            if n not in keep and h["state"] == 0 and h["source"] == "model" and (h["score"] or 0) < KEEP_WD:
                c.execute("DELETE FROM media_tags WHERE media_id=? AND tag_id=(SELECT id FROM tags WHERE name=?)", (mid, n))
                removed += 1
    return {"added": added, "confirmed": confirmed, "removed": removed}


def danbooru_general_for(res: dict) -> tuple[list[str], bool] | None:
    dan = (res.get("data") or {}).get("danbooru") or {}
    if "general" not in dan:
        return None
    return dan["general"], res.get("method") in EXACT_METHODS


def backfill_danbooru_general(progress=None, stop=None) -> dict:
    """以前找到 Danbooru 出處的圖：補抓 Danbooru 的一般標籤（一次 100 篇）並套用。"""
    from .danbooru import dan_get
    c = db.conn()
    rows = c.execute("SELECT media_id, method, data FROM media_source WHERE status='found'").fetchall()
    todo: dict[int, list] = {}
    for r in rows:
        d = json.loads(r["data"] or "{}")
        dan = d.get("danbooru") or {}
        if dan.get("post_id") and "general" not in dan:
            todo.setdefault(int(dan["post_id"]), []).append((r["media_id"], r["method"], d))
    ids = sorted(todo)
    stats = {"posts": len(ids), "images": sum(len(v) for v in todo.values()), "added": 0, "confirmed": 0, "removed": 0, "exact": 0}
    for i in range(0, len(ids), 100):
        if stop and stop():
            break
        ch = ids[i:i + 100]
        try:
            posts = dan_get("/posts.json", {"tags": "id:" + ",".join(map(str, ch)), "limit": 100, "only": "id,tag_string_general"}) or []
        except Exception as e:  # noqa
            log.info("補抓一般標籤失敗：%s", e)
            continue
        got = {p["id"]: [t for t in (p.get("tag_string_general") or "").split() if t] for p in posts}
        with db.write_lock:
            for pid in ch:
                if pid not in got:
                    continue
                for mid, method, d in todo[pid]:
                    d["danbooru"]["general"] = got[pid]
                    c.execute("UPDATE media_source SET data=? WHERE media_id=?", (json.dumps(d, ensure_ascii=False), mid))
                    exact = method in EXACT_METHODS
                    st = apply_general(mid, got[pid], "danbooru", exact)
                    stats["exact"] += exact
                    for k in ("added", "confirmed", "removed"):
                        stats[k] += st[k]
            c.commit()
        if progress:
            progress(min(i + 100, len(ids)), len(ids))
    return stats


# ---------- 2. 日文標籤 → 角色／作品 ----------
SKIP = re.compile(r"(users入り|000users|user|R-?18|R18G|漫画|イラスト|落書き|らくがき|版権|女の子|男の子|創作|FA$|fanart|art$)", re.I)


def jp_to_danbooru(texts: list[str]) -> dict[str, list[dict]]:
    """日文（或任何語言）的標籤 → Danbooru 角色／作品 [{name, category}]。查 Danbooru wiki 的其他名稱，結果記下來。"""
    from .danbooru import dan_get
    from .r34 import dan_categories
    _ensure()
    c = db.conn()
    out: dict[str, list[dict]] = {}
    cutoff = time.time() - RETRY_DAYS * 86400
    for t in dict.fromkeys(x.strip().lstrip("#＃") for x in texts if x and x.strip()):
        if len(t) < 2 or len(t) > 40 or SKIP.search(t) or t.isdigit():
            continue
        r = c.execute("SELECT names, checked_at FROM jp_tag_map WHERE text=?", (t,)).fetchone()
        if r and (r["names"] or r["checked_at"] > cutoff):
            got = json.loads(r["names"]) if r["names"] else []
            if len(got) == 1:           # 只收「只對到一個」的（舊的快取也照這個規則）
                out[t] = got
            continue
        found: list[dict] = []
        try:
            pages = dan_get("/wiki_pages.json", {"search[other_names_include_any]": t, "limit": 6, "only": "title,is_deleted"}) or []
            titles = [p["title"] for p in pages if not p.get("is_deleted")]
            if 0 < len(titles) <= 3:
                cats = dan_categories(titles)
                found = [{"name": n, "category": cats[n]} for n in titles if cats.get(n) in ("character", "copyright")]
            # 對到好幾個角色（「아인」「インキュバス」這種常見名字／普通詞）→ 太模糊，不採用；開發時的圖庫實測會對錯
        except Exception as e:  # noqa
            log.info("查日文標籤失敗 %s：%s", t, e)
            continue
        with db.write_lock:
            c.execute("INSERT OR REPLACE INTO jp_tag_map(text, names, checked_at) VALUES(?,?,?)",
                      (t, json.dumps(found, ensure_ascii=False) if found else None, time.time()))
        if len(found) == 1:
            out[t] = found
    return out


def hashtags(text: str) -> list[str]:
    return re.findall(r"[#＃]([^\s#＃,，。、!！?？「」()（）\[\]]{2,40})", text or "")


# ---------- 把以前收掉的高分 AI 標籤找回來（規則改成保留 ≥ KEEP_WD 之後用） ----------
_pending_reapply: set[int] = set()


def after_processed():
    if not _pending_reapply:
        return
    c = db.conn()
    done = []
    with db.write_lock:
        for mid in list(_pending_reapply):
            r = c.execute("""SELECT s.data FROM media_source s JOIN media m ON m.id=s.media_id
                             WHERE s.media_id=? AND m.status='done'""", (mid,)).fetchone()
            if not r:
                st = c.execute("SELECT status FROM media WHERE id=?", (mid,)).fetchone()
                if not st or st[0] not in ("pending", "processing"):
                    done.append(mid)        # 刪掉、出錯、沒有搜源紀錄：不會再等到了，以前會永遠留在清單裡每次重查
                continue
            gen = ((json.loads(r[0] or "{}").get("danbooru")) or {}).get("general")
            if gen:
                apply_general(mid, gen, "danbooru", exact=True)
            done.append(mid)
        c.commit()
    _pending_reapply.difference_update(done)
