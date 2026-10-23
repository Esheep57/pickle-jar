"""官方作品的圖：動畫截圖、漫畫截圖、小說插圖。

- 另外記一個「官方」種類（media.official = anime / manga / novel），跟媒材分開；可以篩選、搜尋（type:anime）
- 動畫、漫畫截圖不算「缺創作者」（沒有同人意義上的畫師）；小說插圖的插畫家照常當創作者
- 誰說了算：你手動設定的 > Danbooru 自動判斷的（自動的不會蓋掉你設定的）
- b96 拿掉 trace.moe 認動畫截圖（找到 8 張；你手動標了 174 張），表 anime_frame／anilist_map 在遷移 v20 刪掉
"""
from __future__ import annotations

import logging

from . import db

log = logging.getLogger("official")
KINDS = {"anime": "動畫截圖", "manga": "漫畫截圖", "novel": "小說插圖", "movie": "電影截圖"}
_ready = False


def _ensure():
    global _ready
    if _ready:
        return
    with db.write_lock:
        c = db.conn()
        cols = {r[1] for r in c.execute("PRAGMA table_info(media)")}
        if "official" not in cols:
            c.execute("ALTER TABLE media ADD COLUMN official TEXT")
            c.execute("ALTER TABLE media ADD COLUMN official_src TEXT")
        c.commit()
    _ready = True


# ---------- 設定種類 ----------
def set_kind(ids: list[int], kind: str | None, src: str = "manual") -> int:
    """設定官方種類。src='manual' 一律照你的；自動的不蓋掉手動設定。可以撤銷（手動的）。"""
    _ensure()
    if kind is not None and kind not in KINDS:
        raise ValueError(kind)
    c = db.conn()
    with db.write_lock:
        ph = ",".join("?" * len(ids))
        prev = [[r[0], r[1], r[2]] for r in c.execute(f"SELECT id, official, official_src FROM media WHERE id IN ({ph})", ids)]
        todo = [p for p in prev if src == "manual" or p[2] != "manual"]
        c.executemany("UPDATE media SET official=?, official_src=? WHERE id=?", [(kind, src if kind else (src if src == "manual" else None), p[0]) for p in todo])
        if src == "manual" and todo:
            db.push_action(f"{'標為' + KINDS[kind] if kind else '取消官方種類'}（{len(todo)} 張）", {"official": todo})
        c.commit()
    return len(todo)


# Danbooru 上的一般標籤 → 官方種類
DAN_KIND = [("anime", ("anime_screenshot", "anime_screencap")),
            ("novel", ("novel_illustration", "novel_cover", "light_novel_illustration")),
            ("manga", ("manga_panel", "official_comic")),
            ("movie", ("movie_screenshot", "movie_screencap", "film_screenshot"))]


def from_danbooru(mid: int, general: list[str]):
    g = set(general or [])
    for kind, names in DAN_KIND:
        if g & set(names):
            set_kind([mid], kind, "danbooru")
            return kind
    return None
