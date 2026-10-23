"""效能紀錄（b93）：每個請求花多久、等寫入鎖等多久、誰把寫入鎖抱很久。

為什麼：所有寫入共用一把 db.write_lock。背景工作（搜源、重算）抱著鎖的時候，你按「確認」要等它放開——
以前沒有任何紀錄，無從得知會不會卡。這裡只記數字，不改行為；有數字再決定要不要把長迴圈拆成小段 commit。

- 請求：中介層（server.guard）量總時間；寫入鎖的等待時間用 contextvar 帶回來（FastAPI 的同步路由在執行緒池跑，
  contextvar 會跟著複製過去，指向同一個 dict）。
- 慢請求（≥ SLOW_MS）記進 data/slow.log（超過 1 MB 換一份 .1）和記憶體裡最近 200 筆。
- 每個路由最近 300 次的時間（算中位數、p90、最大）。
- 寫入鎖：最外層取得時算等了多久、放開時算抱了多久；抱超過 HOLD_MS 的記下執行緒名稱（背景工作的執行緒都有名字）。
- `GET /api/diag/perf` 看全部；系統 › 系統狀態 ›「回應速度」。
"""
from __future__ import annotations

import contextvars
import threading
import time
from collections import deque

SLOW_MS = 300
HOLD_MS = 500
_KEEP = 300

REQ: contextvars.ContextVar[dict | None] = contextvars.ContextVar("pj_req", default=None)
_lock = threading.Lock()
_routes: dict[str, deque] = {}
_slow: deque = deque(maxlen=200)
_holds: deque = deque(maxlen=200)
_lockstat = {"acquired": 0, "waited_ms": 0.0, "max_wait_ms": 0.0, "max_wait_at": None, "max_wait_thread": None,
             "held_ms": 0.0, "max_hold_ms": 0.0, "max_hold_at": None, "max_hold_thread": None}
_since = time.time()


def begin() -> dict:
    d = {"lock_wait": 0.0, "lock_n": 0}
    REQ.set(d)
    return d


# ---------- 寫入鎖（db._WriteLock 呼叫；只在最外層） ----------
def lock_waited(ms: float):
    d = REQ.get()
    if d is not None:
        d["lock_wait"] += ms
        d["lock_n"] += 1
    with _lock:
        _lockstat["acquired"] += 1
        _lockstat["waited_ms"] += ms
        if ms > _lockstat["max_wait_ms"]:
            _lockstat.update(max_wait_ms=ms, max_wait_at=time.time(), max_wait_thread=threading.current_thread().name)


def lock_held(ms: float):
    name = threading.current_thread().name
    with _lock:
        _lockstat["held_ms"] += ms
        if ms > _lockstat["max_hold_ms"]:
            _lockstat.update(max_hold_ms=ms, max_hold_at=time.time(), max_hold_thread=name)
        if ms >= HOLD_MS:
            _holds.append({"at": time.time(), "ms": round(ms), "thread": name})


# ---------- 請求 ----------
def record(method: str, route: str, path: str, status: int, ms: float, d: dict | None, view: str | None = None):
    wait = round((d or {}).get("lock_wait", 0.0), 1)
    key = f"{method} {route}"
    with _lock:
        q = _routes.get(key)
        if q is None:
            q = _routes[key] = deque(maxlen=_KEEP)
        q.append(ms)
        if ms >= SLOW_MS:
            row = {"at": time.time(), "method": method, "path": path[:200], "route": route, "status": status,
                   "ms": round(ms), "lock_wait_ms": wait, "view": view}
            _slow.append(row)
        else:
            row = None
    if row:
        _write_slow(row)


def _write_slow(row: dict):
    try:
        from . import db
        p = db.DATA_DIR / "slow.log"
        if p.exists() and p.stat().st_size > 1_000_000:
            p.replace(p.with_suffix(".log.1"))
        with open(p, "a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(row['at']))}\t{row['ms']} ms\t"
                    f"等鎖 {row['lock_wait_ms']} ms\t{row['status']}\t{row['method']} {row['path']}\t{row['view'] or ''}\n")
    except Exception:  # noqa  紀錄失敗不影響請求
        pass


def _pct(xs: list[float], p: float) -> float:
    if not xs:
        return 0.0
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(round(p * (len(xs) - 1))))]


def summary(limit: int = 40) -> dict:
    with _lock:
        routes = {k: list(v) for k, v in _routes.items()}
        slow = list(_slow)[-60:][::-1]
        holds = list(_holds)[-40:][::-1]
        ls = dict(_lockstat)
    rows = [{"route": k, "n": len(v), "p50": round(_pct(v, .5), 1), "p90": round(_pct(v, .9), 1), "max": round(max(v), 1)}
            for k, v in routes.items() if v]
    rows.sort(key=lambda r: -r["p90"])
    n = ls["acquired"] or 1
    return {"since": _since, "slow_ms": SLOW_MS, "hold_ms": HOLD_MS, "routes": rows[:limit], "slow": slow, "holds": holds,
            "lock": {**{k: (round(v, 1) if isinstance(v, float) else v) for k, v in ls.items()},
                     "avg_wait_ms": round(ls["waited_ms"] / n, 2)}}
