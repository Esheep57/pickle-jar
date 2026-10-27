"""背景工作：掃描資料夾 → 解碼/縮圖/dHash/指紋 → WD tagger → 寫入 DB；監看新檔、搬家接回、定期掃描。"""
from __future__ import annotations

import collections
import json
import logging
import os
import queue
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np

from . import candidates, db, errlog, media, tagger
from .matcher import matcher, rematch_all

log = logging.getLogger("pipeline")

# Windows 的路徑不分大小寫：IMG_1.jpg 和 IMG_1.JPG 是同一個檔案（b98）。以前程式當成兩張圖各記一筆，
# 「重複的圖」把它們列成重複，刪掉一筆就把唯一的檔案丟進回收筒（真的發生過）。
CASE_FOLD = os.name == "nt" or bool(os.environ.get("LIB_CASE_FOLD"))


def pkey(p: str) -> str:
    """比對路徑用的鍵：Windows 不分大小寫。"""
    p = os.path.normpath(p or "")
    return p.lower() if CASE_FOLD else p

SEVERITY = ["general", "sensitive", "questionable", "explicit"]
LIVE_PHOTO_STILL = {".heic", ".heif", ".jpg", ".jpeg"}


def _argsize(arg):
    if isinstance(arg, (list, tuple, set)):
        return len(arg)
    if isinstance(arg, dict):
        return arg.get("limit") or None
    return None


class Jobs:
    def __init__(self):
        self.q: queue.Queue = queue.Queue()
        self.state = {"running": False, "phase": "閒置", "done": 0, "total": 0, "rate": 0.0, "eta": None,
                      "paused": False, "provider": None, "model": None, "error": None, "matcher": {},
                      "last_scan": None, "relinked": 0, "download": None}
        self.stop_flag = threading.Event()
        self.resume = threading.Event()
        self.resume.set()
        self.history: collections.deque = collections.deque(maxlen=20)   # 最近完成的工作（背景工作面板）
        self.observer = None
        self._pending_paths: set[str] = set()
        self._debounce: threading.Timer | None = None
        self._lock = threading.Lock()
        self._queued_kinds: set[str] = set()
        threading.Thread(target=self._loop, daemon=True, name="jobs").start()
        threading.Thread(target=self._timer, daemon=True, name="jobs-timer").start()

    # ---------- 佇列 ----------
    def submit(self, kind: str, arg=None):
        # 沒有參數的工作（sweep、rematch、整庫 scan…）已經在排隊就不再排：效果一樣，重複排只會整庫重算好幾次
        # （長時間背景搜源時，每 10 張就排一次 sweep，曾經排到上百個）
        if arg is None:
            with self._lock:
                if kind in self._queued_kinds:
                    return
                self._queued_kinds.add(kind)
        self.q.put((kind, arg))

    def pause(self, on: bool):
        self.state["paused"] = on
        (self.resume.clear if on else self.resume.set)()

    def checkpoint(self) -> bool:
        """處理迴圈每批呼叫：暫停就等；回傳 False 表示要停止。"""
        if not self.resume.is_set():
            ph = self.state["phase"]
            self.state["phase"] = "已暫停"
            while not self.resume.wait(0.5):
                if self.stop_flag.is_set():
                    break
            self.state["phase"] = ph
        return not self.stop_flag.is_set()

    def progress(self, done: int, total: int | None = None, t0: float | None = None):
        st = self.state
        st["done"] = done
        if total is not None:
            st["total"] = total
        if t0:
            rate = done / max(time.time() - t0, 1e-6)
            st["rate"] = round(rate, 1)
            st["eta"] = round((st["total"] - done) / rate) if rate > 0 and st["total"] > done else None

    def _loop(self):
        while True:
            kind, arg = self.q.get()
            if arg is None:
                with self._lock:
                    self._queued_kinds.discard(kind)
            self.stop_flag.clear()
            t0 = time.time()
            self.state.update(running=True, error=None, eta=None, done=0, total=0, kind=kind, started=t0)
            try:
                if kind == "scan":
                    scan(self, arg)
                elif kind in ("rematch", "sweep"):
                    self.state.update(phase="套用否決", done=0, total=0)
                    rematch_all(lambda d, t: self.state.update(done=d, total=t))
                elif kind == "retag":
                    with db.write_lock:
                        c = db.conn()
                        if arg:
                            c.executemany("UPDATE media SET status='pending' WHERE id=?", [(i,) for i in arg])
                        else:
                            c.execute("UPDATE media SET status='pending' WHERE status IN ('done','error')")
                        c.commit()
                    process_pending(self)
                elif kind == "fingerprint":
                    backfill_fingerprints(self)
                elif kind == "archive":
                    from . import archive
                    archive.run(self, arg)
                elif kind == "upgrade":
                    from . import upgrade
                    upgrade.run(self, arg)
                elif kind == "pixai":
                    from . import pixai
                    pixai.run(self, arg)
                elif kind == "clip":
                    from . import clipemb
                    clipemb.run(self, arg)
            except Exception as e:  # noqa
                log.exception("工作失敗")
                self.state["error"] = f"{type(e).__name__}: {e}"
            finally:
                self.history.appendleft({"kind": kind, "phase": self.state.get("phase"), "n": _argsize(arg), "done": self.state.get("done"),
                                         "total": self.state.get("total"), "started": t0, "ended": time.time(),
                                         "error": self.state.get("error"), "stopped": self.stop_flag.is_set()})
                self.state.update(running=False, phase="閒置", matcher=matcher.stats, eta=None, download=None, kind=None)

    def _timer(self):
        """定期完整掃描 + 每日備份。"""
        last_scan = time.time()
        self.state["last_scan_timer"] = last_scan
        backup_retry = 0.0        # 備份失敗（磁碟滿、權限）：30 分鐘後再試，不要每分鐘記一次錯誤把其他錯誤洗掉
        last_maint = time.time()
        while True:
            time.sleep(60)
            if time.time() >= backup_retry:
                try:
                    if db.backup():
                        purge_trashed()          # 一天一次（跟每日備份一起：刪紀錄前一定有當天的備份）
                    backup_retry = 0.0
                except Exception as e:  # noqa
                    log.warning("備份失敗：%s", e)
                    errlog.record_bg("每日備份", e)
                    backup_retry = time.time() + 1800
            if time.time() - last_maint > 6 * 3600:      # 長時間開著：定期整理資料庫（見 maintenance.py）
                last_maint = time.time()
                try:
                    from . import maintenance
                    maintenance.periodic()
                except Exception as e:  # noqa
                    errlog.record_bg("定期整理", e)
            try:
                s = db.get_settings()
                h = float(s.get("scan_interval_h") or 0)
                if h > 0 and s.get("roots") and time.time() - last_scan > h * 3600 and self.q.empty() \
                        and not self.state["running"]:
                    last_scan = time.time()
                    self.state["last_scan_timer"] = last_scan
                    self.submit("scan")
            except Exception as e:  # noqa  計時器不能因為一個壞設定就整個停掉（每日備份也靠它）
                log.warning("定期掃描排程失敗：%s", e)

    # ---------- 監看 ----------
    def restart_watch(self):
        if self.observer:
            try:
                self.observer.stop()
            except Exception:
                pass
            self.observer = None
        s = db.get_settings()
        if not s["watch"] or not s["roots"]:
            return
        try:
            from watchdog.events import FileSystemEventHandler
            from watchdog.observers import Observer
        except ImportError:
            return
        jobs = self

        class H(FileSystemEventHandler):
            def on_created(self, e):
                if not e.is_directory:
                    jobs._touch(e.src_path)

            def on_moved(self, e):
                # 在圖庫內搬家/改名：直接改路徑，標記不會掉
                if not relink_moved(e.src_path, e.dest_path, e.is_directory) and not e.is_directory:
                    jobs._touch(e.dest_path)

            def on_modified(self, e):
                if not e.is_directory:
                    jobs._touch(e.src_path)

        obs = Observer()
        for r in s["roots"]:
            if os.path.isdir(r):
                obs.schedule(H(), r, recursive=True)
        obs.daemon = True
        obs.start()
        self.observer = obs
        log.info("監看中：%s", s["roots"])

    def _touch(self, p: str):
        if media.kind_of(Path(p)) is None:
            return
        with self._lock:
            self._pending_paths.add(p)
            if self._debounce:
                self._debounce.cancel()
            self._debounce = threading.Timer(4.0, self._flush)  # 等檔案寫完
            self._debounce.daemon = True
            self._debounce.start()

    def _flush(self):
        with self._lock:
            paths, self._pending_paths = list(self._pending_paths), set()
        if paths:
            self.submit("scan", paths)


def relink_moved(src: str, dst: str, is_dir: bool) -> bool:
    """監看到搬移事件：把 DB 裡的路徑改過去。回傳是否有處理。"""
    src, dst = os.path.normpath(src), os.path.normpath(dst)
    with db.write_lock:
        c = db.conn()
        if is_dir:
            pre = src + os.sep
            rows = c.execute("SELECT id, path FROM media WHERE substr(path, 1, ?)=?", (len(pre), pre)).fetchall()
            for r in rows:
                newp = dst + r["path"][len(src):]
                if not c.execute("SELECT 1 FROM media WHERE path=?", (newp,)).fetchone():
                    c.execute("UPDATE media SET path=? WHERE id=?", (newp, r["id"]))
            c.commit()
            return bool(rows)
        r = c.execute("SELECT id FROM media WHERE path=?", (src,)).fetchone()
        if not r or c.execute("SELECT 1 FROM media WHERE path=?", (dst,)).fetchone():
            return False
        c.execute("UPDATE media SET path=? WHERE id=?", (dst, r["id"]))
        c.commit()
        log.info("搬移：%s → %s", src, dst)
        return True


def _excluded(name: str, exclude: list[str]) -> bool:
    return name.startswith(".") or name in exclude


def walk(root: str, exclude: list[str], skip_live: bool = True):
    stack = [root]
    while stack:
        d = stack.pop()
        try:
            files, dirs = [], []
            with os.scandir(d) as it:
                # 照名稱排序：不同電腦、檔案系統列出的順序不一樣（ext4、NTFS、網路磁碟），新圖的編號和處理順序才會固定
                for e in sorted(it, key=lambda e: e.name):
                    if _excluded(e.name, exclude):
                        continue
                    if e.is_dir(follow_symlinks=False):
                        dirs.append(e.path)
                    elif e.is_file():
                        k = media.kind_of(Path(e.name))
                        if k:
                            files.append((e, k))
            stack.extend(reversed(dirs))
            stills = {os.path.splitext(e.name)[0].lower() for e, k in files
                      if os.path.splitext(e.name)[1].lower() in LIVE_PHOTO_STILL} if skip_live else set()
            for e, k in files:
                stem, ext = os.path.splitext(e.name)
                if skip_live and ext.lower() == ".mov" and stem.lower() in stills:
                    continue  # iPhone 原況照片的動態部分
                st = e.stat()
                yield os.path.normpath(e.path), k, st.st_size, st.st_mtime
        except OSError:
            continue


def _is_live_companion(p: str) -> bool:
    stem, ext = os.path.splitext(p)
    return ext.lower() == ".mov" and any(os.path.exists(stem + x) or os.path.exists(stem + x.upper())
                                         for x in LIVE_PHOTO_STILL)


def scan(jobs: Jobs, paths: list[str] | None = None):
    s = db.get_settings()
    jobs.state.update(phase="掃描資料夾", done=0, total=0)
    c = db.conn()
    found = []
    if paths is None:
        for r in s["roots"]:
            if os.path.isdir(r):
                for item in walk(r, s["exclude"], s.get("skip_live_photo", True)):
                    found.append(item)
                    if len(found) % 2000 == 0:
                        jobs.state["done"] = len(found)
    else:
        for p in paths:
            p = os.path.normpath(p)
            k = media.kind_of(Path(p))
            if k and os.path.isfile(p) and not (s.get("skip_live_photo", True) and _is_live_companion(p)):
                st = os.stat(p)
                found.append((p, k, st.st_size, st.st_mtime))
    existing: dict = {}
    for r in c.execute("SELECT id, path, size, mtime, status, tagged_at, fp FROM media ORDER BY status IN ('missing','trashed'), id"):
        existing.setdefault(pkey(r["path"]), r)      # 同一個檔案（只差大小寫）有兩筆時，用還在的那筆；多的由 merge_case_twins 收掉
    seen = {pkey(p) for p, *_ in found}
    # 只看「現在找得到」的圖庫資料夾：隨身碟拔掉時，裡面的圖不要全部變成「找不到檔案」
    roots = [os.path.join(os.path.normpath(r), "") for r in s["roots"] if os.path.isdir(r)]
    # 可能被搬走/改名的舊紀錄：已標記消失，或（完整掃描時）這次沒看到
    if paths is None:
        rk = [pkey(rt) + os.sep for rt in roots]
        gone = [r for p, r in existing.items() if p not in seen and any(p.startswith(k) for k in rk)]
    else:
        gone = [r for r in existing.values() if r["status"] in ("missing", "trashed")]
        new_sizes = {size for p, k, size, m in found if pkey(p) not in existing}
        gone += [r for p, r in existing.items() if r["status"] not in ("missing", "trashed") and r["size"] in new_sizes
                 and not os.path.exists(r["path"])]
    by_size: dict[int, list] = {}
    for r in gone:
        by_size.setdefault(r["size"], []).append(r)

    # 內容指紋（整個檔案讀一遍）在鎖外先算好，不要卡住其他寫入
    fps: dict[str, str | None] = {}
    for p, k, size, mtime in found:
        if pkey(p) not in existing and by_size.get(size):
            try:
                fps[p] = media.fingerprint(p, k)
            except OSError:
                fps[p] = None
    now = time.time()
    new = changed = relinked = 0
    relinked_ids = set()
    with db.write_lock:
        for p, k, size, mtime in found:
            r = existing.get(pkey(p))
            if r is not None and r["path"] != p and paths is None:
                # 只差大小寫：完整掃描讀到的是磁碟上真正的名字 → 紀錄改成它（監看事件給的名字不一定對，不改）
                c.execute("UPDATE OR IGNORE media SET path=? WHERE id=?", (p, r["id"]))
            if r is None:
                # 先試著接回：同大小的消失紀錄 → 比對內容指紋
                cands = [g for g in by_size.get(size, []) if g["id"] not in relinked_ids]
                hit = None
                if cands:
                    fp = fps.get(p)
                    for g in cands:
                        gfp = g["fp"]
                        if gfp is None and os.path.exists(g["path"]):
                            continue
                        if gfp and gfp == fp:
                            hit = g
                            break
                if hit:
                    c.execute("UPDATE media SET path=?, mtime=?, status=?, trashed_at=NULL WHERE id=?",
                              (p, mtime, "done" if hit["tagged_at"] else "pending", hit["id"]))
                    relinked_ids.add(hit["id"])
                    relinked += 1
                    continue
                c.execute("""INSERT OR IGNORE INTO media(id, path, kind, size, mtime, status, added_at)
                             VALUES(MAX((SELECT IFNULL(MAX(id), 0) FROM media), (SELECT hi FROM media_seq WHERE k=1)) + 1,
                                    ?,?,?,?, 'pending', ?)""", (p, k, size, mtime, now))
                new += 1
            elif r["size"] != size or abs((r["mtime"] or 0) - mtime) > 1:
                c.execute("UPDATE media SET size=?, mtime=?, status='pending', fp=NULL WHERE id=?", (size, mtime, r["id"]))
                changed += 1
            elif r["status"] in ("missing", "trashed"):   # 檔案回來了（例如從資源回收筒還原）
                c.execute("UPDATE media SET status=?, trashed_at=NULL WHERE id=?", ("done" if r["tagged_at"] else "pending", r["id"]))
        if paths is None:  # 完整掃描才標記消失的檔案
            vanish = [r["id"] for r in gone if r["id"] not in relinked_ids and r["status"] not in ("missing", "trashed")]
            in_roots = sum(1 for p in existing if any(p.startswith(k) for k in rk))
            if len(vanish) > max(MISSING_HOLD_MIN, MISSING_HOLD_RATIO * in_roots):
                # 一次消失太多：多半是子資料夾暫時讀不到（iCloud 同步中、權限、網路磁碟），不是你真的刪了。
                # 先不標，等你在「系統狀態」確認（以前會全部變成找不到檔案，再按一次清除就連紀錄都沒了）
                jobs.state["missing_hold"] = {"ids": vanish, "at": time.time(), "of": in_roots}
                log.warning("這次掃描有 %d 張（共 %d 張）突然找不到，先不標記，等你確認", len(vanish), in_roots)
            else:
                jobs.state.pop("missing_hold", None)
                c.executemany("UPDATE media SET status='missing' WHERE id=?", [(i,) for i in vanish])
        c.commit()
    jobs.state["relinked"] = jobs.state.get("relinked", 0) + relinked
    if paths is None:
        jobs.state["last_scan"] = time.time()
    log.info("掃描完成：新增 %d、變更 %d、搬家接回 %d", new, changed, relinked)
    process_pending(jobs)
    if paths is None:
        backfill_fingerprints(jobs)
    if paths is None:
        from . import online
        online.get_jobs().auto_source()


TRASH_KEEP_DAYS = 30


def forget_media(ids: list[int]):
    """真的從資料庫刪掉這些圖的紀錄（連同縮圖、預覽、提到它們的撤銷紀錄）。"""
    if not ids:
        return
    c = db.conn()
    with db.write_lock:
        for i in range(0, len(ids), 500):
            ch = ids[i:i + 500]
            c.execute(f"DELETE FROM media WHERE id IN ({','.join('?' * len(ch))})", ch)
        db.purge_actions(mids=set(ids))
        for i in range(0, len(ids), 500):       # 拿這些圖當封面的本子、角色／作品：清掉，改用自動挑（b99.4）
            ch = ids[i:i + 500]
            c.execute(f"UPDATE albums SET cover=NULL WHERE cover IN ({','.join('?' * len(ch))})", ch)
            c.execute(f"UPDATE tags SET cover=NULL WHERE cover IN ({','.join('?' * len(ch))})", ch)
    for mid in ids:
        media.thumb_path(mid).unlink(missing_ok=True)
        media.thumb_path(mid, large=True).unlink(missing_ok=True)
        (db.DATA_DIR / "preview" / f"{mid}.jpg").unlink(missing_ok=True)
    dup_cache.clear()


def forget_removed_roots(old_roots: list[str], new_roots: list[str]) -> int:
    """圖庫資料夾拿掉時（b97）：那個資料夾底下「找不到檔案」的紀錄一起清掉（檔案早就不在了，留著只會一直列在找不到裡）。
    還找得到的圖不動（照樣看得到，只是不再監看、掃描）；被留下的資料夾蓋到的也不動。清之前先備份。"""
    norm = lambda p: os.path.normcase(os.path.join(os.path.normpath(p), ""))
    keep = [norm(r) for r in new_roots]
    gone = [norm(r) for r in old_roots if norm(r) not in keep and not any(norm(r).startswith(k) for k in keep)]
    if not gone:
        return 0
    ids = [r[0] for r in db.conn().execute("SELECT id, path FROM media WHERE status='missing'")
           if any(os.path.normcase(r[1]).startswith(g) for g in gone) and not any(os.path.normcase(r[1]).startswith(k) for k in keep)]
    if ids:
        db.backup(force=True)
        forget_media(ids)
    return len(ids)


MERGE_COLS = ("nsfw_manual", "excluded", "unrec_skip", "official", "official_src")


def merge_records(keep: int, others: list[int]) -> dict:
    """同一個檔案的好幾筆紀錄合成一筆（b98）：others 上你確認／否決過的標籤、出處、手動設定搬到 keep，再刪掉 others 的紀錄。
    keep 已經有的判斷優先（兩邊衝突時不動 keep 的）；候選（state=0）不搬，之後會重新產生。不碰檔案。"""
    c = db.conn()
    moved = conflicts = 0
    with db.write_lock:
        for o in others:
            for r in c.execute("SELECT tag_id, score, source, state, tier, reason FROM media_tags WHERE media_id=? AND state<>0", (o,)).fetchall():
                k = c.execute("SELECT state FROM media_tags WHERE media_id=? AND tag_id=?", (keep, r["tag_id"])).fetchone()
                if k is None or k["state"] == 0:
                    c.execute("""INSERT INTO media_tags(media_id, tag_id, score, source, state, tier, reason) VALUES(?,?,?,?,?,?,?)
                        ON CONFLICT(media_id, tag_id) DO UPDATE SET score=excluded.score, source=excluded.source, state=excluded.state,
                        tier=excluded.tier, reason=excluded.reason""", (keep, r["tag_id"], r["score"], r["source"], r["state"], r["tier"], r["reason"]))
                    moved += 1
                elif k["state"] != r["state"]:
                    conflicts += 1
            if not c.execute("SELECT 1 FROM media_source WHERE media_id=? AND status='found'", (keep,)).fetchone():
                s = c.execute("SELECT * FROM media_source WHERE media_id=? AND status='found'", (o,)).fetchone()
                if s:
                    d = dict(s)
                    d["media_id"] = keep
                    c.execute(f"INSERT OR REPLACE INTO media_source({','.join(d)}) VALUES({','.join('?' * len(d))})", list(d.values()))
            om = c.execute(f"SELECT {', '.join(MERGE_COLS)}, media_type, media_src FROM media WHERE id=?", (o,)).fetchone()
            km = c.execute(f"SELECT {', '.join(MERGE_COLS)}, media_type, media_src FROM media WHERE id=?", (keep,)).fetchone()
            if om and km:
                for col in MERGE_COLS:
                    if om[col] and not km[col]:
                        c.execute(f"UPDATE media SET {col}=? WHERE id=?", (om[col], keep))
                if om["media_src"] == "manual" and km["media_src"] != "manual":
                    c.execute("UPDATE media SET media_type=?, media_src='manual' WHERE id=?", (om["media_type"], keep))
            c.execute("UPDATE OR IGNORE album_items SET media_id=? WHERE media_id=?", (keep, o))
        c.commit()
    forget_media(list(others))
    return {"moved": moved, "conflicts": conflicts}


def merge_case_twins() -> dict:
    """路徑只差大小寫的紀錄（Windows 上是同一個檔案）合成一筆：留還在的那筆（都在就留編號小的）。"""
    if not CASE_FOLD:
        return {"groups": 0}
    groups: dict = {}
    for r in db.conn().execute("SELECT id, path, status FROM media"):
        groups.setdefault(pkey(r["path"]), []).append(r)
    out = {"groups": 0, "moved": 0, "conflicts": 0}
    ids = []
    for rs in groups.values():
        if len(rs) < 2:
            continue
        rs.sort(key=lambda r: (r["status"] in ("missing", "trashed"), r["id"]))
        res = merge_records(rs[0]["id"], [r["id"] for r in rs[1:]])
        out["groups"] += 1
        out["moved"] += res["moved"]
        out["conflicts"] += res["conflicts"]
        ids.append(rs[0]["id"])
    if ids:
        db.log_op(f"合併同一個檔案的兩筆紀錄（{len(ids)} 張，只差副檔名大小寫）", ids, keys="merge-twins")
        dup_cache.clear()
        log.info("合併只差大小寫的紀錄：%s", out)
    return out


def purge_trashed(days: float = TRASH_KEEP_DAYS) -> int:
    """丟到資源回收筒超過 days 天、檔案也沒回來的：清掉紀錄。"""
    ids = [r[0] for r in db.conn().execute("SELECT id FROM media WHERE status='trashed' AND trashed_at < ?",
                                           (time.time() - days * 86400,))]
    forget_media(ids)
    return len(ids)


MISSING_HOLD_MIN = 50       # 一次消失超過 50 張、而且超過 5%：先不標「找不到檔案」
MISSING_HOLD_RATIO = 0.05


def confirm_missing(jobs: Jobs) -> int:
    """你確認那些圖真的不見了：再檢查一次檔案還在不在，不在的才標成找不到。"""
    hold = jobs.state.pop("missing_hold", None)
    if not hold:
        return 0
    c = db.conn()
    ids = hold["ids"]
    gone = []
    for i in range(0, len(ids), 500):
        ch = ids[i:i + 500]
        gone += [r[0] for r in c.execute(f"SELECT id, path FROM media WHERE id IN ({','.join('?' * len(ch))})", ch)
                 if not os.path.exists(r[1])]
    with db.write_lock:
        c.executemany("UPDATE media SET status='missing' WHERE id=? AND status NOT IN ('missing','trashed')", [(i,) for i in gone])
    return len(gone)


def backfill_fingerprints(jobs: Jobs, limit: int | None = None):
    """舊資料沒有指紋 → 背景補算（只讀檔，不會改檔案）。"""
    c = db.conn()
    rows = c.execute("SELECT id, path, kind FROM media WHERE fp IS NULL AND status='done' ORDER BY id"
                     + (f" LIMIT {int(limit)}" if limit else "")).fetchall()
    if not rows:
        return
    jobs.state.update(phase="計算檔案指紋", done=0, total=len(rows))
    t0 = time.time()
    buf = []
    for i, r in enumerate(rows):
        if i % 50 == 0 and not jobs.checkpoint():
            break
        try:
            buf.append((media.fingerprint(r["path"], r["kind"]), r["id"]))
        except OSError:
            pass
        if len(buf) >= 200 or i == len(rows) - 1:
            with db.write_lock:
                c.executemany("UPDATE media SET fp=? WHERE id=?", buf)
                c.commit()
            buf = []
        jobs.progress(i + 1, t0=t0)
    if buf:
        with db.write_lock:
            c.executemany("UPDATE media SET fp=? WHERE id=?", buf)
            c.commit()


# ---------- 處理 ----------
def _prepare_image(tg: tagger.Tagger, mid: int, path: str):
    try:
        img = media.load_image(Path(path))
        try:
            w, h = media.image_size(path)        # 真正的尺寸（img.size 可能是 draft 縮小解碼後的）
        except Exception:  # noqa
            w, h = img.size
        media.save_thumb(img, mid)
        return mid, tg.preprocess(img), {"width": w, "height": h, "dhash": media.dhash(img),
                                          "taken_at": media.exif_time(Path(path)),
                                          "fp": media.fingerprint(path, "image")}, None
    except Exception as e:  # noqa
        return mid, None, None, f"{type(e).__name__}: {e}"


def _write(mid: int, probs: np.ndarray, emb: np.ndarray, meta: dict, tg: tagger.Tagger, s: dict,
           frame_ratings: list[str] | None = None):
    dec = tg.decode(probs, s["general_threshold"], s["character_threshold"])
    ratings = dec["ratings"]
    rating = max(ratings, key=ratings.get) if ratings else None
    if frame_ratings:  # 影片：足夠多影格是 NSFW 就取最嚴重的
        nsfw_frames = [r for r in frame_ratings if r in s["nsfw_ratings"]]
        if len(nsfw_frames) >= max(1, round(0.2 * len(frame_ratings))):
            rating = max(nsfw_frames, key=SEVERITY.index)
    c = db.conn()
    c.execute("""UPDATE media SET width=?, height=?, duration=?, dhash=?, rating=?, rating_scores=?, nsfw_auto=?,
                 status='done', error=NULL, tagged_at=?, embedding=?, fp=COALESCE(?, fp) WHERE id=?""",
              (meta.get("width"), meta.get("height"), meta.get("duration"), meta.get("dhash"), rating,
               json.dumps({k: round(v, 4) for k, v in ratings.items()}), int(rating in s["nsfw_ratings"]),
               time.time(), emb.astype(np.float16).tobytes(), meta.get("fp"), mid))
    mtype = candidates.media_type(dec["general"])
    # 你手動標的媒材不蓋掉（b77）；其他先用規則，整批處理完再由 mediaclf 用學到的模型重算
    c.execute("""UPDATE media SET media_type=CASE WHEN media_src='manual' THEN media_type ELSE ? END,
                 media_src=CASE WHEN media_src='manual' THEN media_src ELSE 'rule' END,
                 media_conf=CASE WHEN media_src='manual' THEN media_conf ELSE NULL END,
                 taken_at=COALESCE(?, taken_at) WHERE id=?""", (mtype, meta.get("taken_at"), mid))  # 換成高清版的新檔沒有 EXIF：拍攝時間沿用
    c.execute("DELETE FROM media_tags WHERE media_id=? AND state=0 AND source IN ('model','match')", (mid,))
    # 一般描述 tag：直接當 tag（可搜尋，不是身分）；跳過停止自動標註 / 跟否決樣本太像的
    for t, sc in matcher.filter_auto(mid, dec["general"], emb).items():
        c.execute("INSERT OR IGNORE INTO media_tags(media_id, tag_id, score, source, state) VALUES(?,?,?, 'model', 0)",
                  (mid, db.tag_id(t, "general"), round(sc, 4)))
    # 角色：只進候選（tier 4）；搜源已經有結果的圖就不再推薦（搜源最優先）
    if s.get("ai_hide_when_sourced", True) and c.execute(
            "SELECT 1 FROM media_source WHERE media_id=? AND status='found'", (mid,)).fetchone():
        return
    for t, sc in dec["character"].items():
        why = f"AI 模型 {round(sc * 100)}%" + ("（真人/公仔照片，參考就好）" if mtype in ("cosplay", "photo", "figure") else "")
        candidates.add(mid, t, "character", candidates.T_AI, "model", round(sc, 4), why, emb=emb)


def _infer_and_write(tg: tagger.Tagger, s: dict, b: list):
    """一批推論＋寫入。一張出問題只把那一張標成失敗，其他照常寫入：以前任何一張出錯會撤回整批、
    整個工作中止，剩下的圖留在排隊中，下次同樣的順序又撞到同一張。"""
    try:
        probs, embs = tg.run(np.stack([x[1] for x in b]))   # GPU 推論時不占寫入鎖，介面操作不會卡
        res = list(zip(b, probs, embs))
    except Exception as e:  # noqa  整批推論失敗：一張一張重試，找出是哪一張
        if len(b) == 1:
            with db.write_lock:
                _mark_error(b[0][0], f"推論失敗：{type(e).__name__}: {e}")
            log.warning("推論失敗 %s：%s", b[0][0], e)
            return
        for x in b:
            _infer_and_write(tg, s, [x])
        return
    c = db.conn()
    with db.write_lock:
        for (mid, _, meta, _), p, e in res:
            c.execute("SAVEPOINT w1")
            try:
                _write(mid, p, e, meta, tg, s)
                c.execute("RELEASE w1")
            except Exception as ex:  # noqa
                c.execute("ROLLBACK TO w1")
                c.execute("RELEASE w1")
                _mark_error(mid, f"寫入失敗：{type(ex).__name__}: {ex}")
                log.warning("寫入失敗 %s：%s", mid, ex)


def _mark_error(mid: int, err: str):
    db.conn().execute("UPDATE media SET status='error', error=? WHERE id=?", (err[:500], mid))


def load_tagger(jobs: Jobs, repo: str) -> tagger.Tagger:
    jobs.state.update(phase="載入模型")

    def dl(got, total, f):
        jobs.state.update(phase=f"下載模型（{f}）", download={"file": f, "got": got, "total": total})
    tg = tagger.get(repo, progress=dl)
    jobs.state.update(provider=tg.provider, model=repo, download=None)
    return tg


def process_pending(jobs: Jobs):
    s = db.get_settings()
    c = db.conn()
    # 新的先處理：剛丟進來的圖最快能看到結果
    rows = c.execute("SELECT id, path, kind FROM media WHERE status='pending' ORDER BY mtime DESC, id DESC").fetchall()
    if not rows:
        if not matcher.built:
            matcher.build()
        # 沒有要標記的圖也要做：匯入的檔案跟圖庫裡「消失的那張」一模一樣時，掃描會直接接回舊紀錄（已經標記過），
        # 不會經過下面的標記 → 以前貼文作者（X 帳號）永遠不會補上
        try:
            from . import importer
            importer.after_processed()
        except Exception as e:  # noqa
            log.warning("匯入後處理失敗：%s", e)
        return
    tg = load_tagger(jobs, s["model_repo"])
    jobs.state.update(done=0, total=len(rows))
    matcher.build()
    imgs = [r for r in rows if r["kind"] == "image"]
    vids = [r for r in rows if r["kind"] == "video"]
    bs, done, t0 = int(s["batch_size"]), 0, time.time()
    jobs.state["phase"] = "標記圖片"

    with ThreadPoolExecutor(max_workers=int(s["workers"])) as ex:
        chunk = bs * 4
        futs = None
        for i in range(0, len(imgs) + chunk, chunk):
            stop = not jobs.checkpoint()
            # 預先提交下一批解碼（CPU 解碼與 GPU 推論重疊）
            nxt = [ex.submit(_prepare_image, tg, r["id"], r["path"]) for r in imgs[i:i + chunk]] \
                if i < len(imgs) and not stop else None
            if futs:
                results = [f.result() for f in futs]
                ok = [r for r in results if r[1] is not None]
                with db.write_lock:
                    for mid, _, _, err in results:
                        if err:
                            _mark_error(mid, err)
                for j in range(0, len(ok), bs):
                    b = ok[j:j + bs]
                    _infer_and_write(tg, s, b)
                done += len(results)
                jobs.progress(done, t0=t0)
            futs = nxt
            if stop:
                break

    jobs.state["phase"] = "標記影片"
    for r in vids:
        if not jobs.checkpoint():
            break
        try:
            frames, dur, w, h = media.video_frames(Path(r["path"]), int(s["video_max_frames"]))
            media.save_thumb(frames[len(frames) // 2], r["id"])
            arr = np.stack([tg.preprocess(f) for f in frames])
            P, E = [], []
            for j in range(0, len(arr), bs):
                p, e = tg.run(arr[j:j + bs])
                P.append(p)
                E.append(e)
            P, E = np.concatenate(P), np.concatenate(E)
            agg = 0.5 * P.max(0) + 0.5 * P.mean(0)
            agg[tg.rating_idx] = P[:, tg.rating_idx].mean(0)
            fr = [tg.names[tg.rating_idx[int(np.argmax(p[tg.rating_idx]))]] for p in P]
            emb = E.mean(0)
            meta = {"width": w, "height": h, "duration": dur, "dhash": media.dhash(frames[len(frames) // 2]),
                    "fp": media.fingerprint(r["path"], "video")}
            with db.write_lock:
                _write(r["id"], agg, emb, meta, tg, s, frame_ratings=fr)
                db.conn().commit()
        except Exception as e:  # noqa
            log.warning("影片失敗 %s: %s", r["path"], e)
            with db.write_lock:
                _mark_error(r["id"], f"{type(e).__name__}: {e}")
                db.conn().commit()
        done += 1
        jobs.progress(done, t0=t0)
    matcher.build()
    dup_cache.clear()
    candidates.compute_sets()
    if imgs:
        from . import charknn, people
        people.refresh_bases()                     # 新的造型 tag：算人數時併進本體
        people.refresh([r["id"] for r in imgs])   # 多人圖：人數
        from . import mediaclf
        mediaclf.run(only=[r["id"] for r in rows])   # 新圖的媒材（b77；分堆會用到，所以排在前面）
        from . import piles
        piles.refresh([r["id"] for r in rows])     # 未識別的分堆
        try:
            from . import aigen
            aigen.refresh([r["id"] for r in imgs])  # AI 生成的圖
        except Exception as e:  # noqa
            log.info("AI 生成判斷失敗：%s", e)
        charknn.run(only=[r["id"] for r in rows])   # 新圖（含影片）：用你確認過的圖推薦角色（只算新圖）
    _queue_character_info()
    from . import online
    online.get_jobs().auto_source()
    try:
        from . import importer, sitetags
        importer.after_processed()          # 從網站匯入的圖：補上網站提供的資訊
        sitetags.after_processed()          # 重新標記完：Danbooru 一般標籤再套一次
    except Exception as e:  # noqa
        log.warning("匯入後處理失敗：%s", e)
    if s.get("pixai_enabled"):
        jobs.submit("pixai")          # 新圖也跑 PixAI（一塊一塊做，不會卡住掃描）
    if s.get("media_clip", True):
        jobs.submit("clip")           # 新圖的 CLIP 特徵（媒材分類，b78）


def _queue_character_info():
    """新出現的角色 → 背景抓 Danbooru 角色資料卡。"""
    s = db.get_settings()
    if not (s["online_enabled"] and s["online_auto_charinfo"]):
        return
    from . import online
    names = [r[0] for r in db.conn().execute("""SELECT t.name FROM tags t LEFT JOIN tag_info ti ON ti.tag_id=t.id
        WHERE t.category='character' AND ti.tag_id IS NULL
          AND EXISTS (SELECT 1 FROM media_tags x WHERE x.tag_id=t.id AND x.state>=0)
        ORDER BY (SELECT COUNT(*) FROM media_tags x WHERE x.tag_id=t.id) DESC""")]
    online.get_jobs().queue_tag_info(names)


# ---------- 重複偵測 ----------
dup_cache: dict = {}


# 「同一張圖」的判斷（重複頁、沿用重複圖的搜源結果、否決傳播都用這一個）
# 只看 dHash 不夠：dHash 是縮成 9×8 灰階比明暗，白底＋中間一個人的圖很容易撞在一起（實際發生過：不同的圖只差 2 bit）。
SAME_ASPECT = 0.04          # 長寬比差超過 4% → 不是同一張（裁切過的算不同張）
SAME_EMB = 0.85             # 內容特徵（WD tagger 的 embedding）相似度。實測：縮成 400px 的複本 0.92、撞 dHash 的不同圖 0.05


def _aspect(w, h):
    return (w / h) if w and h else None


def emb_cos(a: bytes | None, b: bytes | None) -> float | None:
    if a is None or b is None or len(a) != len(b):
        return None
    import numpy as np
    from .matcher import _emb, matcher
    x, y = _emb(a), _emb(b)
    mu = matcher.mu if matcher.mu is not None and matcher.mu.shape == x.shape else 0
    x, y = x - mu, y - mu
    return float(x @ y / (np.linalg.norm(x) * np.linalg.norm(y) + 1e-8))


def _rid(r):
    """資料列的圖片編號（有的查詢叫 id、搜源表叫 media_id）。"""
    try:
        ks = r.keys()
    except AttributeError:
        return None
    return r["id"] if "id" in ks else (r["media_id"] if "media_id" in ks else None)


def same_image(a, b, max_dist: int = 3) -> bool:
    """a、b：有 dhash、width、height、embedding 的資料列。三項都要過：dHash 夠近、長寬比一樣、內容特徵夠像；
    資料列帶圖片編號時，最後再逐像素驗證（dupverify：眨眼、改表情、對話框換字的差分不算同一張）。"""
    if a["dhash"] is None or b["dhash"] is None:
        return False
    if bin((a["dhash"] ^ b["dhash"]) & 0xFFFFFFFFFFFFFFFF).count("1") > max_dist:
        return False
    ra, rb = _aspect(a["width"], a["height"]), _aspect(b["width"], b["height"])
    if ra and rb and abs(ra / rb - 1) > SAME_ASPECT:
        return False
    cos = emb_cos(a["embedding"], b["embedding"])
    if cos is not None and cos < SAME_EMB:
        return False
    ia, ib = _rid(a), _rid(b)
    if ia is not None and ib is not None and ia != ib and pixel_same(ia, ib) is False:
        return False
    return True


def _fps(ids) -> dict:
    ids = list(ids)
    return {r[0]: r[1] or "" for r in db.conn().execute(
        f"SELECT id, fp FROM media WHERE id IN ({','.join('?' * len(ids))})", ids)} if ids else {}


def pixel_same(ia: int, ib: int) -> bool | None:
    """逐像素驗證（結果存在 dup_check，檔案沒換就不重算）。縮圖讀不到 → None（沒辦法判斷，交給前面的條件）。"""
    from . import dupverify
    a, b = sorted((ia, ib))
    fp = _fps((a, b))
    c = db.conn()
    r = c.execute("SELECT kind, fa, fb FROM dup_check WHERE a=? AND b=? AND kind IN ('same','diff')", (a, b)).fetchone()
    if r and r["fa"] == fp.get(a) and r["fb"] == fp.get(b):
        return r["kind"] == "same"
    A, B = dupverify.load(media.thumb_path(a)), dupverify.load(media.thumb_path(b))
    if A is None or B is None:
        return None
    ok, score = dupverify.same(A, B)
    with db.write_lock:
        c.execute("""INSERT OR REPLACE INTO dup_check(a, b, fa, fb, kind, score) VALUES(?,?,?,?,?,?)""",
                  (a, b, fp.get(a), fp.get(b), "same" if ok else "diff", round(score, 2)))
    return ok


# ---------- 局部截圖 ----------
CROP_COS = 0.80          # 候選：整體特徵相似度（實測 0.85–0.98）
CROP_PAIRS = 300         # 每次最多驗證幾對（依相似度排序）
crop_state = {"running": False, "sig": None, "checked": 0, "total": 0}
_crop_lock = threading.Lock()


def _crop_candidates() -> list[tuple[int, int]]:
    """長寬比不同（>4%）但整體特徵很像的圖對。"""
    import numpy as np
    from .matcher import _emb, matcher
    rows = db.conn().execute("""SELECT id, width, height, embedding FROM media
        WHERE embedding IS NOT NULL AND status='done' AND kind='image' AND width>0 AND height>0
          AND id NOT IN (SELECT media_id FROM album_items)""").fetchall()
    if len(rows) < 2:
        return []
    dim = len(rows[-1]["embedding"]) // 2
    rows = [r for r in rows if len(r["embedding"]) // 2 == dim]
    ids = np.array([r["id"] for r in rows])
    asp = np.array([r["width"] / r["height"] for r in rows], dtype=np.float32)
    E = np.stack([_emb(r["embedding"]) for r in rows])
    mu = matcher.mu if matcher.mu is not None and matcher.mu.shape[-1] == dim else E.mean(0)
    E = E - mu
    E /= np.linalg.norm(E, axis=1, keepdims=True) + 1e-8
    out = []
    for i in range(0, len(E), 1000):
        S = E[i:i + 1000] @ E.T
        n = S.shape[0]
        S[np.arange(len(E))[None, :] <= np.arange(i, i + n)[:, None]] = -np.inf          # 每對只算一次、不跟自己比
        S[np.abs(asp[i:i + n, None] / asp[None, :] - 1) <= SAME_ASPECT] = -np.inf         # 長寬比一樣的交給一般重複判斷
        ii, jj = np.nonzero(S >= CROP_COS)
        out += [(float(S[a, b]), int(ids[i + a]), int(ids[b])) for a, b in zip(ii, jj)]
    # 已經驗證過（檔案沒換）的對先拿掉，再取前 CROP_PAIRS 對。以前是先取全圖庫前 300 對再挑沒驗過的：
    # 300 對驗完之後，新圖的局部截圖除非擠進全圖庫前 300 名，否則永遠不會被檢查
    done = _verified_crop_pairs()
    out = [x for x in out if (min(x[1], x[2]), max(x[1], x[2])) not in done]
    out.sort(reverse=True)
    return [(a, b) for _, a, b in out[:CROP_PAIRS]]


def _verified_crop_pairs() -> set[tuple[int, int]]:
    """dup_check 裡已經有局部截圖結果、而且兩張的檔案指紋都沒變的配對。"""
    return {(r[0], r[1]) for r in db.conn().execute("""SELECT d.a, d.b FROM dup_check d
        JOIN media ma ON ma.id=d.a AND ma.fp IS d.fa JOIN media mb ON mb.id=d.b AND mb.fp IS d.fb
        WHERE d.kind IN ('crop','nocrop')""")}


def crop_scan(wait: bool = False):
    """背景：驗證局部截圖的候選對（每對約 40 ms；結果存起來，下次只算新的）。"""
    from . import dupverify
    if not _crop_lock.acquire(blocking=wait):
        return
    try:
        crop_state["running"] = True
        c = db.conn()
        pairs = _crop_candidates()
        fp = _fps({x for p in pairs for x in p})
        todo = []
        for a, b in pairs:
            a, b = sorted((a, b))
            r = c.execute("SELECT fa, fb FROM dup_check WHERE a=? AND b=? AND kind IN ('crop','nocrop')", (a, b)).fetchone()
            if not (r and r["fa"] == fp.get(a) and r["fb"] == fp.get(b)):
                todo.append((a, b))
        crop_state.update(checked=0, total=len(todo))
        for a, b in todo:
            A, B = dupverify.load(media.thumb_path(a)), dupverify.load(media.thumb_path(b))
            res = None
            if A is not None and B is not None:
                for big, small, bi, si in ((A, B, a, b), (B, A, b, a)):
                    r = dupverify.crop_of(big, small)
                    if r:
                        res = (si, r)
                        break
            with db.write_lock:
                if res:
                    si, (score, box) = res
                    c.execute("INSERT OR REPLACE INTO dup_check(a, b, fa, fb, kind, score, small, box) VALUES(?,?,?,?,'crop',?,?,?)",
                              (a, b, fp.get(a), fp.get(b), round(score, 2), si, json.dumps([round(x, 3) for x in box])))
                else:
                    c.execute("INSERT OR REPLACE INTO dup_check(a, b, fa, fb, kind) VALUES(?,?,?,?,'nocrop')",
                              (a, b, fp.get(a), fp.get(b)))
                c.commit()
            crop_state["checked"] += 1
        if todo:
            dup_cache.clear()
        if len(pairs) >= CROP_PAIRS:
            crop_state["sig"] = None      # 還有沒驗完的：下次打開重複頁接著驗下一批
    except Exception as e:  # noqa
        log.warning("局部截圖比對失敗：%s", e)
    finally:
        crop_state["running"] = False
        _crop_lock.release()


def crop_links() -> list[dict]:
    """已驗證的局部截圖：[{small, big, score}]（small 是 big 的局部）。圖庫有變動時在背景補算。"""
    c = db.conn()
    sig = tuple(c.execute("SELECT COUNT(*), IFNULL(MAX(id), 0) FROM media WHERE status='done'").fetchone())
    if crop_state["sig"] != sig and not crop_state["running"]:
        crop_state["sig"] = sig
        threading.Thread(target=crop_scan, daemon=True, name="crop-scan").start()
    out = []
    for r in c.execute("""SELECT d.a, d.b, d.small, d.score FROM dup_check d
            JOIN media ma ON ma.id=d.a AND ma.fp IS d.fa AND ma.status='done'
            JOIN media mb ON mb.id=d.b AND mb.fp IS d.fb AND mb.status='done' WHERE d.kind='crop'"""):
        out.append({"small": r["small"], "big": r["b"] if r["small"] == r["a"] else r["a"], "score": r["score"]})
    return out


def _cliques(adj: dict, same, key=lambda i: i) -> list[list]:
    """貪婪分組：連得最多的當中心，只收「跟組裡每一張都直接比對通過」的點。回傳每組（至少兩個）。"""
    where: set = set()
    out: list[list] = []
    for c0 in sorted(adj, key=lambda i: (-len(adj[i]), key(i))):
        if c0 in where:
            continue
        g = [c0]
        for nb in sorted(adj[c0], key=lambda i: (-len(adj.get(i, ())), key(i))):
            if nb not in where and all(same(nb, m) for m in g[1:]):
                g.append(nb)
        if len(g) > 1:
            where.update(g)
            out.append(g)
    return out


def find_duplicates(max_dist: int = 3) -> list[list[int]]:
    """dHash 分 4 段各 16 bit；漢明距離 ≤ 3 必有一段相同 → 候選對，再用 same_image 確認（長寬比、內容特徵、逐像素）。

    分組：同一組裡**每兩張都直接比對過是同一張**（以前用並查集，A≈B、B≈C 就把 A、C 放同一組，
    差分可能被串進來，再按「保留建議的，選取其餘」就會刪到不是重複的圖）。局部截圖掛在它的完整圖那一組。"""
    alb = tuple(db.conn().execute("SELECT COUNT(*), IFNULL(SUM(media_id), 0) FROM album_items").fetchone())
    if dup_cache.get("album_sig") != alb:       # 本子收了／拿掉頁：重算
        dup_cache.clear()
        dup_cache["album_sig"] = alb
    if max_dist in dup_cache:
        return dup_cache[max_dist]
    # 逐像素驗證的結果存在資料表：第一次會比較久（每對約 5 ms），之後只算新的
    # 本子裡的頁不算重複（b92：同一篇的差分、封面和內頁常常很像；本子是照原樣收的）
    rows = db.conn().execute("""SELECT id, dhash, width, height, embedding, path FROM media WHERE dhash IS NOT NULL AND status='done'
        AND id NOT IN (SELECT media_id FROM album_items)""").fetchall()
    ids = [r[0] for r in rows]
    keys = [pkey(r["path"]) for r in rows]      # 同一個檔案的兩筆紀錄不是「重複」：刪一筆就是刪掉唯一的檔案（b98）
    hs = [r[1] & 0xFFFFFFFFFFFFFFFF for r in rows]
    adj: dict[int, set[int]] = {}
    checked: dict[tuple[int, int], bool] = {}

    def same(i, j):
        k = (i, j) if i < j else (j, i)
        if k not in checked:
            checked[k] = keys[i] != keys[j] and bin(hs[i] ^ hs[j]).count("1") <= max_dist and same_image(rows[i], rows[j], max_dist)
        return checked[k]

    bands = 4 if max_dist <= 3 else 8  # 鴿籠原理：段數 > 距離 才保證有一段完全相同
    width = 64 // bands
    mask = (1 << width) - 1
    for b in range(bands):
        buckets: dict[int, list[int]] = {}
        for i, h in enumerate(hs):
            buckets.setdefault((h >> (b * width)) & mask, []).append(i)
        for group in buckets.values():
            if len(group) < 2 or len(group) > 400:  # 超大桶多半是純色圖，略過
                continue
            for x in range(len(group)):
                for y in range(x + 1, len(group)):
                    i, j = group[x], group[y]
                    if j not in adj.get(i, ()) and same(i, j):
                        adj.setdefault(i, set()).add(j)
                        adj.setdefault(j, set()).add(i)
    groups_i = _cliques(adj, same, key=lambda i: ids[i])
    where = {m: gi for gi, g in enumerate(groups_i) for m in g}
    pos = {m: i for i, m in enumerate(ids)}
    crops = {}
    for l in crop_links():                  # 局部截圖：掛到完整那張的組（重複頁會標出「這張是那張的局部」）
        si, bi = pos.get(l["small"]), pos.get(l["big"])
        if si is None or bi is None or si in where:
            continue
        if bi not in where:
            where[bi] = len(groups_i)
            groups_i.append([bi])
        where[si] = where[bi]
        groups_i[where[bi]].append(si)
        crops[l["small"]] = l["big"]
    out = sorted((sorted(ids[i] for i in g) for g in groups_i if len(g) > 1), key=len, reverse=True)
    dup_cache[max_dist] = out
    dup_cache[("crops", max_dist)] = crops
    return out


jobs: Jobs | None = None


def get_jobs() -> Jobs:
    global jobs
    if jobs is None:
        jobs = Jobs()
    return jobs
