"""名字索引：用任何「看得到的名字」都能找到角色／創作者／作品。

以前搜尋只比對 tag 原名和 Danbooru 別名原文，所以：
- 畫面上顯示「芙莉蓮」（別名是簡體「芙莉莲」轉成繁體），搜「芙莉蓮」卻 0 張
- 「blue archive」「hatsune miku」（有空白、沒底線）0 張
- 作品的內建台灣譯名、維基查到的譯名、手動改的名字都搜不到
這裡把每個身分標籤的所有名字（原名、去掉括號的名字、顯示名稱、別名，全部再做簡繁互轉、小寫、底線變空白）
建成一張表，搜尋和加標籤的選單都用它。整個圖庫約 2,500 個身分標籤，重建 < 0.3 秒，資料有變才重建。
"""
from __future__ import annotations

import json
import re
import threading
import time

from . import db, names

_LOCK = threading.Lock()
_IDX: dict = {"sig": None, "checked": 0.0, "keys": {}, "rows": []}
CJK = re.compile(r"[㐀-鿿豈-﫿]")


def norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").replace("_", " ")).strip().lower()


def variants(s: str) -> set[str]:
    """一個名字的所有寫法：原樣、繁體、簡體（都小寫、底線當空白）。"""
    out = {norm(s)}
    if CJK.search(s or ""):
        st = names._st()
        if st:
            s2t, t2s = st
            out |= {norm(s2t.convert(s)), norm(t2s.convert(s))}
        out.add(norm(names.to_trad(s)))
    out.discard("")
    return out


def _signature(c) -> tuple:
    a = c.execute("SELECT COUNT(*), MAX(id) FROM tags WHERE category!='general'").fetchone()
    b = c.execute("SELECT COUNT(*), MAX(fetched_at), SUM(LENGTH(COALESCE(display_name,''))) FROM tag_info").fetchone()
    try:        # Bangumi 中文名（zhname.py）：查到新的也要重建，搜得到「常闇永遠」
        z = tuple(c.execute("SELECT COUNT(*), MAX(checked_at) FROM name_zh").fetchone())
    except Exception:  # noqa
        z = ()
    return tuple(a) + tuple(b) + z + (db.get_settings().get("display_lang"),)


def _build(c):
    lang = db.get_settings()["display_lang"]
    rows = c.execute("""SELECT t.id, t.name, t.category, ti.other_names, ti.display_name FROM tags t
        LEFT JOIN tag_info ti ON ti.tag_id=t.id WHERE t.category!='general'""").fetchall()
    try:
        from . import wikiname
        wiki = wikiname.cached([r[1] for r in rows if r[2] == "copyright"])
    except Exception:  # noqa
        wiki = {}
    keys: dict[str, set[int]] = {}
    base: dict[str, set[int]] = {}
    out = []
    for tid, name, cat, on, dn in rows:
        other = json.loads(on or "[]")
        disp = (names.series_display(name, other, dn, lang, wiki.get(name)) if cat == "copyright"
                else names.display(name, other, dn, lang))
        ks: set[str] = set()
        for s in [name, disp, dn or "", names.series_display(name, other, dn, "zh", wiki.get(name)) if cat == "copyright" else "", *other]:
            if s:
                ks |= variants(s)
        for k in ks:
            keys.setdefault(k, set()).add(tid)
        # 去掉括號的本名（frieren_(summer) → frieren）：只在沒有「完全同名」的標籤時才用（見 exact）
        b = re.sub(r"_\([^()]*\)$", "", name)
        bk = variants(b) - ks if b != name else set()
        for k in bk:
            base.setdefault(k, set()).add(tid)
        out.append((tid, name, cat, disp, tuple(sorted(ks | bk, key=len))))
    keys["\0base"] = base   # type: ignore[assignment]
    return keys, out


def _get():
    now = time.time()
    with _LOCK:
        if now - _IDX["checked"] > 3 or _IDX["sig"] is None:
            c = db.conn()
            sig = _signature(c)
            _IDX["checked"] = now
            if sig != _IDX["sig"]:
                _IDX["keys"], _IDX["rows"] = _build(c)
                _IDX["sig"] = sig
        return _IDX["keys"], _IDX["rows"]


def invalidate():
    with _LOCK:
        _IDX["sig"] = None


def exact(tok: str) -> set[int]:
    """完全等於某個名字（任何寫法）的標籤。「fern」只對到本名剛好是 fern 的，不會對到所有 *_(fern)。"""
    keys, _ = _get()
    out: set[int] = set()
    vs = variants(tok)
    for v in vs:
        out |= keys.get(v, set())
    if not out:   # 沒有完全同名的 → 用去掉括號的本名（「frieren」也找到 frieren_(summer) 這種造型）
        base = keys.get("\0base", {})
        for v in vs:
            out |= base.get(v, set())
    return out


def search(q: str, category: str | None = None, limit: int = 40) -> list[tuple[int, str, str, str, int]]:
    """包含這段文字的標籤：(id, 原名, 類別, 顯示名稱, 排序分數)。完全相同 > 開頭相同 > 包含。"""
    _, rows = _get()
    qs = variants(q)
    if not qs:
        return []
    hits = []
    for tid, name, cat, disp, ks in rows:
        if category and cat != category:
            continue
        best = 9
        for k in ks:
            for v in qs:
                if v == k:
                    best = 0
                elif best > 1 and k.startswith(v):
                    best = 1
                elif best > 2 and v in k:
                    best = 2
            if best == 0:
                break
        if best < 9:
            hits.append((tid, name, cat, disp, best))
    hits.sort(key=lambda h: h[4])
    return hits[:limit * 4]
