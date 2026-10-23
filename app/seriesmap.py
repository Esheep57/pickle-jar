"""作品的母系列：用 Danbooru 的 tag implication（limbus_company → project_moon、fate/grand_order → fate_(series)…）。

Danbooru 上「A 一定也是 B」的關係是人工審核過的，比用名字猜可靠，新作品也不用手動加規則。
查過的存在 series_parent 資料表（沒有母系列也記下來，60 天後再查）；沒連網時 names.series_parent 仍會用名字規則。
"""
from __future__ import annotations

import logging
import time

from . import db

log = logging.getLogger("seriesmap")
SCHEMA = "CREATE TABLE IF NOT EXISTS series_parent(child TEXT PRIMARY KEY, parent TEXT, checked_at REAL)"
RETRY_DAYS = 60
_ready = False
_cache: dict = {}


def _ensure():
    global _ready
    if not _ready:
        with db.write_lock:
            db.conn().execute(SCHEMA)
        _ready = True


def parents() -> dict[str, str]:
    """child → parent（30 秒快取）。"""
    if time.time() - _cache.get("at", 0) > 30:
        _ensure()
        _cache["v"] = {r[0]: r[1] for r in db.conn().execute("SELECT child, parent FROM series_parent WHERE parent IS NOT NULL")}
        _cache["at"] = time.time()
    return _cache["v"]


def ancestors(key: str) -> list[str]:
    """由近到遠的母系列（最多 5 層，防止循環）。"""
    p, out = parents(), []
    k = key
    while k in p and len(out) < 5:
        k = p[k]
        if k in out or k == key:
            break
        out.append(k)
    return out


def need_lookup(keys) -> list[str]:
    _ensure()
    cutoff = time.time() - RETRY_DAYS * 86400
    done = {r[0] for r in db.conn().execute("SELECT child FROM series_parent WHERE checked_at > ?", (cutoff,))}
    return [k for k in keys if k and k not in done]


def run(keys: list[str], stop=None, progress=None):
    """向 Danbooru 查這些作品的母系列（一次 40 個），母系列本身再往上查一層層。"""
    from .danbooru import dan_get
    _ensure()
    todo, seen, n = list(dict.fromkeys(keys)), set(), 0
    while todo:
        if stop and stop():
            break
        ch = [k for k in todo[:40] if k not in seen]
        todo = todo[40:]
        if not ch:
            continue
        seen.update(ch)
        try:
            rows = dan_get("/tag_implications.json", {"search[antecedent_name_comma]": ",".join(ch),
                                                      "search[status]": "active", "limit": 200,
                                                      "only": "antecedent_name,consequent_name"}) or []
        except Exception as e:  # noqa
            log.info("查母系列失敗：%s", e)
            break
        found: dict[str, str] = {}
        for r in rows:
            a, c = r.get("antecedent_name"), r.get("consequent_name")
            if a in ch and c and c != a and a not in found:
                found[a] = c
        now = time.time()
        with db.write_lock:
            db.conn().executemany("INSERT OR REPLACE INTO series_parent(child, parent, checked_at) VALUES(?,?,?)",
                                  [(k, found.get(k), now) for k in ch])
        for p in found.values():      # 母系列的母系列
            if p not in seen and p not in todo and need_lookup([p]):
                todo.append(p)
        n += len(ch)
        if progress:
            progress(n)
    _cache.clear()
