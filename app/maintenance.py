"""長時間開著的定期整理（pipeline 的計時器每 6 小時呼叫一次；啟動時也跑一次）。

每一步都很便宜、失敗也不影響其他步驟：
- WAL 寫回主檔（PASSIVE：不等、不擋別人）＋ PRAGMA optimize（讓 SQLite 依實際資料更新查詢計畫用的統計）
- 刪掉已經不存在的圖留下的紀錄（沒有外鍵、不會自動刪的表）
- data/preview（HEIC／TIFF 轉 JPEG 的快取）超過上限就刪最久沒用的
- 失敗留下的備份暫存檔（*.part）
- 操作紀錄（op_log）超過 2 年的
- 同一個人變成兩個創作者（帳號連結一樣）→ 合併
"""
from __future__ import annotations

import logging
import time

from . import db

log = logging.getLogger("maintenance")
PREVIEW_MAX = 1 << 30            # 轉檔快取最多 1 GB（一張 4096px JPEG 幾 MB；刪掉的下次看大圖會重轉）
ORPHAN_TABLES = ("ascii2d", "knn_refs")   # media_id 欄位、沒有外鍵


def _orphans() -> dict:
    c = db.conn()
    out = {}
    for t in ORPHAN_TABLES:
        if not c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (t,)).fetchone():
            continue
        with db.write_lock:
            n = c.execute(f"DELETE FROM {t} WHERE media_id NOT IN (SELECT id FROM media)").rowcount
        if n:
            out[t] = n
    return out


def _preview_cap() -> int:
    from .api.library import PREVIEW_DIR
    if not PREVIEW_DIR.exists():
        return 0
    files = sorted(PREVIEW_DIR.glob("*.jpg"), key=lambda p: p.stat().st_atime if p.exists() else 0)
    total = sum(p.stat().st_size for p in files)
    removed = 0
    for p in files:
        if total <= PREVIEW_MAX:
            break
        try:
            sz = p.stat().st_size
            p.unlink()
            total -= sz
            removed += 1
        except OSError:
            pass
    return removed


def _stale_parts() -> int:
    n = 0
    for p in db.BACKUP_DIR.glob("*.part") if db.BACKUP_DIR.exists() else []:
        try:
            if time.time() - p.stat().st_mtime > 3600:
                p.unlink()
                n += 1
        except OSError:
            pass
    return n


def _gif_tmp() -> int:
    """影片轉 GIF 的暫存（data/gif_tmp）：一天前的刪掉。"""
    from . import togif
    togif.clean_tmp()
    return 0


def _ascii2d_raw() -> int:
    """ascii2d 結果頁的原始 HTML 只留最近 200 列（b96：以前一直留著，長到 42 MB）。"""
    with db.write_lock:
        return db.trim_ascii2d_raw(db.conn())


def _dangling_covers() -> int:
    """封面指到已經不存在的圖（紀錄被清掉）：清掉，改用自動挑（b99.4）。"""
    c = db.conn()
    with db.write_lock:
        n = c.execute("UPDATE albums SET cover=NULL WHERE cover IS NOT NULL AND cover NOT IN (SELECT id FROM media)").rowcount
        n += c.execute("UPDATE tags SET cover=NULL WHERE cover IS NOT NULL AND cover NOT IN (SELECT id FROM media)").rowcount
        c.commit()
    return n


def _artist_dupes() -> int:
    """同一個人變成兩個創作者（X／Pixiv／FANBOX 帳號一樣）→ 併起來（b100）。"""
    from . import siteartists
    return len(siteartists.merge_local_dupes())


def periodic() -> dict:
    from . import errlog
    out = {}
    for name, fn in (("orphans", _orphans), ("preview", _preview_cap), ("parts", _stale_parts), ("oplog", db.prune_op_log), ("gif_tmp", _gif_tmp), ("ascii2d_raw", _ascii2d_raw), ("covers", _dangling_covers), ("artist_dupes", _artist_dupes)):
        with errlog.bg(f"定期整理：{name}"):
            out[name] = fn()
    with errlog.bg("定期整理：資料庫"):
        c = db.conn()
        with db.write_lock:
            c.execute("PRAGMA optimize")
        out["wal"] = list(c.execute("PRAGMA wal_checkpoint(PASSIVE)").fetchone())
    log.info("定期整理：%s", out)
    return out
