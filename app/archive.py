"""移出 iCloud：把圖從 iCloud 照片資料夾搬到電腦上的普通資料夾，並讓 iCloud 刪掉那份（省 iCloud / iPad 空間）。

安全順序（任何一步失敗，那張圖就原封不動）：
1. 複製到保存資料夾（iCloud 只放預留位置的檔案，讀取時會自動下載）
2. 用內容指紋（MD5）比對複製出來的檔案，確認一模一樣
3. 圖庫紀錄改指向新位置 → 標籤、確認、搜源結果全部保留
4. 最後才把 iCloud 資料夾裡的那份移到資源回收筒 → iCloud 同步刪除（iPad「最近刪除」可再救 30 天）
"""
from __future__ import annotations

import logging
import os
import re
import shutil
import time
from pathlib import Path

from . import db, media

log = logging.getLogger("archive")
ICLOUD = re.compile(r"[\\/](iCloud Photos|iCloudPhotos|iCloudDrive|iCloud Drive)[\\/]", re.I)
SYNCED = re.compile(r"[\\/](iCloud Photos|iCloudPhotos|iCloudDrive|iCloud Drive|OneDrive[^\\/]*|Google Drive|Dropbox)([\\/]|$)", re.I)


def is_icloud(path: str) -> bool:
    return bool(ICLOUD.search(path or ""))


def default_dir() -> str:
    s = db.get_settings()
    if s.get("archive_dir"):
        return s["archive_dir"]
    return str(Path.home() / "Pictures" / "圖庫保存（從 iCloud 移出）")


def check_dest(dest: str) -> str | None:
    """保存資料夾不能在任何雲端同步資料夾裡（否則等於沒移出）。回傳錯誤訊息或 None。"""
    if not dest:
        return "請選擇保存資料夾"
    d = os.path.normpath(dest)
    if SYNCED.search(d + os.sep):
        return "保存資料夾不能放在 iCloud / OneDrive / Google Drive / Dropbox 等同步資料夾裡"
    return None


def preview(ids: list[int]) -> dict:
    c = db.conn()
    n = size = 0
    for i in range(0, len(ids), 800):
        ch = ids[i:i + 800]
        for r in c.execute(f"SELECT path, size FROM media WHERE id IN ({','.join('?' * len(ch))})", ch):
            if is_icloud(r["path"]):
                n += 1
                size += r["size"] or 0
    return {"icloud": n, "others": len(ids) - n, "size": size, "dest": default_dir()}


def _target(dest: Path, path: str, when: float | None) -> Path:
    sub = time.strftime("%Y-%m", time.localtime(when)) if when else "未知日期"
    d = dest / sub
    d.mkdir(parents=True, exist_ok=True)
    name = os.path.basename(path)
    stem, ext = os.path.splitext(name)
    t, i = d / name, 1
    while t.exists():
        t = d / f"{stem} ({i}){ext}"
        i += 1
    return t


def run(jobs, arg: dict):
    """背景工作：arg = {"ids": [...], "dest": "..."}。"""
    from send2trash import send2trash
    dest = Path(arg["dest"])
    err = check_dest(str(dest))
    if err:
        jobs.state["error"] = err
        return
    dest.mkdir(parents=True, exist_ok=True)
    s = db.get_settings()
    roots = [os.path.normpath(r) for r in s["roots"]]
    if not any(os.path.normpath(str(dest)).startswith(r) for r in roots):
        db.update_settings({"roots": s["roots"] + [str(dest)]})   # 保存資料夾加入圖庫，之後也會被監看
        jobs.restart_watch()
    db.update_settings({"archive_dir": str(dest)})
    c = db.conn()
    rows = []
    for mid in arg["ids"]:
        r = c.execute("SELECT id, path, kind, fp, size, COALESCE(taken_at, mtime) AS t FROM media WHERE id=?", (mid,)).fetchone()
        if r and is_icloud(r["path"]):
            rows.append(dict(r))
    need = sum(r["size"] or 0 for r in rows)
    if shutil.disk_usage(dest).free < need * 1.1 + (200 << 20):
        jobs.state["error"] = f"保存資料夾所在磁碟空間不足（需要約 {need / 1e9:.1f} GB）"
        return
    res = {"moved": 0, "failed": [], "freed": 0}
    t0 = time.time()
    jobs.state.update(phase="移出 iCloud", done=0, total=len(rows))
    for i, r in enumerate(rows):
        if not jobs.checkpoint():
            break
        src, tgt = r["path"], None
        try:
            if not os.path.exists(src):
                raise FileNotFoundError("iCloud 資料夾裡已經找不到這個檔案")
            tgt = _target(dest, src, r["t"])
            shutil.copy2(src, tgt)                     # 預留位置的檔案會在這一步下載
            want = r["fp"] or media.fingerprint(src, r["kind"])
            got = media.fingerprint(str(tgt), r["kind"])
            if got != want:
                raise IOError("複製出來的檔案跟原檔不一樣（下載不完整？）")
            with db.write_lock:
                c.execute("UPDATE media SET path=?, fp=?, mtime=? WHERE id=?",
                          (str(tgt), got, os.path.getmtime(tgt), r["id"]))
            try:
                send2trash(src)                        # iCloud 同步刪除
            except Exception as e:  # noqa
                raise IOError(f"已複製到電腦，但刪除 iCloud 那份失敗：{e}")
            res["moved"] += 1
            res["freed"] += r["size"] or 0
        except Exception as e:  # noqa
            log.warning("移出 iCloud 失敗 %s：%s", src, e)
            # 還沒改紀錄就失敗 → 刪掉不完整的複本；已經改紀錄（只剩刪原檔失敗）→ 保留複本
            if tgt is not None and tgt.exists():
                cur = c.execute("SELECT path FROM media WHERE id=?", (r["id"],)).fetchone()
                if cur and cur["path"] != str(tgt):
                    tgt.unlink(missing_ok=True)
            res["failed"].append({"id": r["id"], "name": os.path.basename(src), "error": str(e)[:200]})
        jobs.progress(i + 1, t0=t0)
    if res["moved"]:
        s2 = db.get_settings()
        db.update_settings({"relay_uncleared": int(s2.get("relay_uncleared") or 0) + res["moved"],
                            "relay_uncleared_bytes": int(s2.get("relay_uncleared_bytes") or 0) + res["freed"]})
    jobs.state["archive"] = {**res, "dest": str(dest), "at": time.time(), "auto": bool(arg.get("auto"))}
    log.info("移出 iCloud：%d 張，失敗 %d", res["moved"], len(res["failed"]))

