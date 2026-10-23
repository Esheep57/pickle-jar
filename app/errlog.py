"""伺服器出錯的紀錄：以前某個請求出錯（500）只在瀏覽器主控台看得到一行「500」，不知道是哪裡、為什麼。
現在記在 data/errors.log（最多約 200 KB）和記憶體裡最近 50 筆，系統狀態頁看得到。"""
from __future__ import annotations

import collections
import time
import traceback

from . import db

RECENT: collections.deque = collections.deque(maxlen=50)
MAX_BYTES = 200_000


def record(method: str, path: str, exc: BaseException):
    tb = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))[-4000:]
    e = {"at": time.time(), "method": method, "path": path[:300], "error": f"{type(exc).__name__}: {exc}"[:500], "trace": tb}
    RECENT.appendleft(e)
    try:
        p = db.DATA_DIR / "errors.log"
        if p.exists() and p.stat().st_size > MAX_BYTES:
            p.replace(db.DATA_DIR / "errors.old.log")
        with open(p, "a", encoding="utf-8") as f:
            f.write(f"\n=== {time.strftime('%Y-%m-%d %H:%M:%S')} {method} {path}\n{tb}")
    except Exception:  # noqa
        pass


def recent(n: int = 20) -> list[dict]:
    return list(RECENT)[:n]


def record_bg(step: str, exc: BaseException):
    """背景工作（啟動整理、搜源的某一步、排程）失敗：以前多半 except: pass 吞掉，畫面上看不到。現在一樣記進來。"""
    record("背景", step, exc)


class bg:
    """with errlog.bg("步驟名稱"): ...  出錯時記下來、不往外丟（背景的一步失敗不要拖垮其他步驟）。"""

    def __init__(self, step: str):
        self.step = step

    def __enter__(self):
        return self

    def __exit__(self, et, ev, tb):
        if et is not None and issubclass(et, Exception):
            record_bg(self.step, ev)
            return True
        return False


def install():
    """程式各處的 log.warning／log.exception（背景搜源失敗、影片解不開、定期工作失敗…）也記進「程式出錯」。
    以前只印在命令列視窗，關掉就沒了。只收 WARNING 以上；一般的「查過、沒有」是 INFO，不會進來。"""
    import logging

    class H(logging.Handler):
        def emit(self, rec: logging.LogRecord):
            try:
                if _noise(rec):
                    return
                if rec.exc_info and rec.exc_info[1] is not None:
                    exc = rec.exc_info[1]
                else:
                    exc = RuntimeError(rec.getMessage())
                record("背景", f"{rec.name}：{rec.getMessage()}"[:300], exc)
            except Exception:  # noqa  記錄失敗不能讓原本的程式出錯
                pass

    def _noise(rec) -> bool:
        """不是程式的錯：Windows 上瀏覽器關掉連線時 asyncio 會印 ConnectionResetError（WinError 10054）。
        以前「程式出錯」裡 306 行有 17 筆都是這個，把真的錯誤淹掉。"""
        exc = rec.exc_info[1] if rec.exc_info else None
        return rec.name == "asyncio" and (isinstance(exc, ConnectionResetError) or "_call_connection_lost" in rec.getMessage())

    root = logging.getLogger()
    if not any(getattr(h, "_errlog", False) for h in root.handlers):
        h = H(logging.WARNING)
        h._errlog = True
        root.addHandler(h)
