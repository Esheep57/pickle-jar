"""背景工作登記表（b93）：散落在各模組、各自 `threading.Thread(...)` 開的背景工作統一登記。

以前：本機、線上兩條佇列之外，還有 gdl 匯入、轉 GIF、匯出訓練集、整本匯入、匯入後處理、
標籤蘊含同步、新角色辨識重算、媒材重算、畫師連結…各自開執行緒、各自記進度。結果：
- 重開程式這些就消失了，畫面上也看不到「上次做到一半」
- 背景工作面板只看得到兩條佇列，其他的要到各自的視窗看
- 幾個重的（整庫重算、匯出）同時跑會互搶寫入鎖和 GPU

現在：`spawn(kind, label, target, *args, probe=..., cancel=..., heavy=...)` 取代 `threading.Thread(...).start()`：
- 執行緒照舊（不改各模組的邏輯），多了登記：名稱、開始時間、進度（`probe()` 回傳 {done,total,note}，讀各模組原本的進度）、
  能不能取消（`cancel()`）、結果。
- heavy＝會掃整個圖庫或吃 GPU 的：同時最多 HEAVY_MAX 個，其餘排隊（狀態 waiting）。
- 存在資料表 bg_tasks：重開程式後，上次還在跑的會顯示「中斷了」（之後可以自己再按一次）。只留最近 KEEP 筆。
- 背景工作面板（jobsview）、事件流（/api/events）都讀這裡。
"""
from __future__ import annotations

import logging
import threading
import time
from collections import deque
from typing import Callable

from . import db

log = logging.getLogger("tasks")
HEAVY_MAX = 2
KEEP = 60
SCHEMA = """CREATE TABLE IF NOT EXISTS bg_tasks(id INTEGER PRIMARY KEY, kind TEXT NOT NULL, label TEXT, state TEXT NOT NULL,
  started REAL, ended REAL, done INTEGER, total INTEGER, note TEXT, error TEXT)"""

_lock = threading.Lock()
_heavy = threading.Semaphore(HEAVY_MAX)
_live: dict[int, "Task"] = {}
_recent: deque = deque(maxlen=30)
_seq = 0
_version = 0          # 有變化就 +1（事件流用）


def ensure_schema(c):
    c.execute(SCHEMA)


def version() -> int:
    return _version


def _bump():
    global _version
    _version += 1


class Task:
    def __init__(self, kind: str, label: str, probe: Callable | None, cancel: Callable | None, heavy: bool):
        global _seq
        self.kind, self.label, self.probe, self.cancel_fn, self.heavy = kind, label, probe, cancel, heavy
        self.state = "waiting" if heavy else "running"
        self.started, self.ended = time.time(), None
        self.error: str | None = None
        self.cancelled = False
        self.id = 0
        try:
            with db.write_lock:
                c = db.conn()
                self.id = c.execute("INSERT INTO bg_tasks(kind, label, state, started) VALUES(?,?,?,?)",
                                    (kind, label, self.state, self.started)).lastrowid
                c.commit()
        except Exception:  # noqa  舊資料庫還沒遷移：照樣跑，只是不存
            with _lock:
                _seq -= 1
                self.id = _seq

    def progress(self) -> dict:
        if not self.probe:
            return {}
        try:
            p = self.probe() or {}
            return {k: p.get(k) for k in ("done", "total", "note") if p.get(k) is not None}
        except Exception:  # noqa
            return {}

    def as_dict(self) -> dict:
        d = {"id": self.id, "kind": self.kind, "label": self.label, "state": self.state, "started": self.started,
             "ended": self.ended, "error": self.error, "can_cancel": bool(self.cancel_fn) and self.state in ("running", "waiting")}
        if self.state in ("running", "waiting"):
            d.update(self.progress())
        return d

    def _finish(self, state: str, error: str | None = None):
        p = self.progress()          # 先讀進度（狀態一變成結束，as_dict 就不再帶進度）
        self.error, self.ended = error, time.time()
        try:
            if self.id > 0:
                with db.write_lock:
                    c = db.conn()
                    c.execute("UPDATE bg_tasks SET state=?, ended=?, error=?, done=?, total=?, note=? WHERE id=?",
                              (state, self.ended, error, p.get("done"), p.get("total"), p.get("note"), self.id))
                    c.commit()
        except Exception:  # noqa
            pass
        with _lock:          # 狀態最後才改：外面看到「結束了」時，最近完成的清單裡一定已經有它
            _live.pop(self.id, None)
            _recent.appendleft({**self.as_dict(), "state": state, "can_cancel": False, **p})
            self.state = state
            _bump()


def spawn(kind: str, label: str, target: Callable, *args, probe: Callable | None = None, cancel: Callable | None = None,
          heavy: bool = False, name: str | None = None, **kwargs) -> Task:
    """開一個登記過的背景執行緒（取代 threading.Thread(target=…, daemon=True).start()）。"""
    t = Task(kind, label, probe, cancel, heavy)
    with _lock:
        _live[t.id] = t
        _bump()

    def run():
        try:
            if heavy:
                _heavy.acquire()
                if t.cancelled:
                    _heavy.release()
                    return t._finish("cancelled")
                t.state = "running"
                with _lock:
                    _bump()
            try:
                target(*args, **kwargs)
            finally:
                if heavy:
                    _heavy.release()
            t._finish("cancelled" if t.cancelled else "done")
        except Exception as e:  # noqa  記進「程式出錯」，執行緒照樣結束
            log.warning("背景工作「%s」失敗：%s", label, e, exc_info=True)
            t._finish("failed", f"{type(e).__name__}: {e}"[:300])

    threading.Thread(target=run, daemon=True, name=name or kind).start()
    return t


def probe_of(d: dict, note_key: str | None = None) -> Callable:
    """各模組原本的進度 dict（done、total）→ probe。"""
    return lambda: {"done": d.get("done"), "total": d.get("total"), "note": d.get(note_key) if note_key else None}


def cancel(tid: int) -> bool:
    t = _live.get(tid)
    if not t or not t.cancel_fn:
        return False
    t.cancelled = True
    try:
        t.cancel_fn()
    except Exception as e:  # noqa
        log.info("取消 %s 失敗：%s", t.label, e)
    with _lock:
        _bump()
    return True


def live_progress() -> list:
    """事件流用（b96）：還在跑的工作的進度 [id, done, total, state]；進度一動事件流就推，前端 follow() 跟著問一次。"""
    with _lock:
        ts = list(_live.values())
    out = []
    for t in ts:
        p = t.progress() if t.state == "running" else {}
        out.append([t.id, p.get("done"), p.get("total"), t.state])
    return out


def snapshot() -> dict:
    with _lock:
        live = [t.as_dict() for t in _live.values()]
        recent = list(_recent)
    interrupted = []
    try:
        interrupted = [dict(r) for r in db.conn().execute(
            "SELECT id, kind, label, state, started, ended, done, total, note, error FROM bg_tasks WHERE state='interrupted' "
            "AND ended > ? ORDER BY id DESC LIMIT 10", (time.time() - 3 * 86400,))]
    except Exception:  # noqa
        pass
    return {"running": live, "recent": recent[:15], "interrupted": interrupted}


def on_startup():
    """上次還在跑的（程式被關掉、當掉）→ 中斷了；只留最近 KEEP 筆。"""
    try:
        with db.write_lock:
            c = db.conn()
            c.execute("UPDATE bg_tasks SET state='interrupted', ended=? WHERE state IN ('running','waiting')", (time.time(),))
            c.execute("DELETE FROM bg_tasks WHERE id NOT IN (SELECT id FROM bg_tasks ORDER BY id DESC LIMIT ?)", (KEEP,))
            c.commit()
    except Exception as e:  # noqa
        log.info("背景工作紀錄整理失敗：%s", e)


def dismiss_interrupted():
    with db.write_lock:
        db.conn().execute("UPDATE bg_tasks SET state='interrupted_seen' WHERE state='interrupted'")
        db.conn().commit()
    with _lock:
        _bump()
