"""存在資料庫裡、有優先序的工作佇列（線上工作用：搜源、抓資料卡、各種找出處）。

以前是記憶體裡的一條先進先出佇列：
- 程式重啟就全部消失（每次重啟都要手動「補搜可能的圖」）
- 1,000 張補搜排在前面時，新角色的資料卡、你手動按的搜源都要等好幾個小時

現在：
- 每一項存在 job_queue 資料表，重啟後接著做
- 大工作切成小塊（搜源 10 張一塊），塊跟塊之間會先做優先序高的：
  0 你手動要的（按 S、匯入）＞ 1 資料卡、作品名稱、你按的工具 ＞ 2 背景自動搜源
- 同一次送進來的小塊是同一「組」：停止、取消都是整組
"""
from __future__ import annotations

import json
import threading
import time

from . import db

SCHEMA = """CREATE TABLE IF NOT EXISTS job_queue(
  id INTEGER PRIMARY KEY, queue TEXT NOT NULL, kind TEXT NOT NULL, arg TEXT, prio INTEGER NOT NULL DEFAULT 1,
  grp INTEGER NOT NULL, manual INTEGER DEFAULT 0, created REAL);
CREATE INDEX IF NOT EXISTS ix_job_queue ON job_queue(queue, prio, id);"""

MANUAL, NORMAL, BACKGROUND = 0, 1, 2
CHUNK = {"rerun": 10, "pixivsweep": 1, "zhname": 20, "upgradecheck": 20, "source": 10, "taginfo": 40, "seriesname": 40, "seriesparent": 40, "charcat_cp": 40, "cpshare": 20}


def _size(arg) -> int | None:
    return len(arg) if isinstance(arg, (list, tuple)) else None


class PQueue:
    def __init__(self, name: str):
        self.name = name
        self.cv = threading.Condition()     # 資料表由 db.init() 的遷移建立（db._m3_job_queue）

    # ---- 放進去 ----
    def put(self, kind: str, arg, prio: int = NORMAL, manual: bool = False) -> int:
        """放一項（清單會切成小塊）；回傳組別編號。"""
        n = CHUNK.get(kind)
        parts = [arg[i:i + n] for i in range(0, len(arg), n)] if n and isinstance(arg, (list, tuple)) and arg else [arg]
        now = time.time()
        with db.write_lock:
            c = db.conn()
            # 組別編號只增不減（用毫秒時間）：做完的組刪掉後，新的組不會拿到同一個編號被當成同一件工作
            grp = max((c.execute("SELECT IFNULL(MAX(grp), 0) FROM job_queue").fetchone()[0] or 0) + 1, int(now * 1000))
            c.executemany("INSERT INTO job_queue(queue, kind, arg, prio, grp, manual, created) VALUES(?,?,?,?,?,?,?)",
                          [(self.name, kind, json.dumps(p, ensure_ascii=False), int(prio), grp, int(manual), now) for p in parts])
        with self.cv:
            self.cv.notify_all()
        return grp

    # ---- 拿出來 ----
    def peek(self) -> dict | None:
        r = db.conn().execute("SELECT * FROM job_queue WHERE queue=? ORDER BY prio, id LIMIT 1", (self.name,)).fetchone()
        return self._row(r) if r else None

    def wait(self, timeout: float = 5.0):
        with self.cv:
            self.cv.wait(timeout)

    def done(self, row_id: int):
        with db.write_lock:
            db.conn().execute("DELETE FROM job_queue WHERE id=?", (row_id,))

    # ---- 看、取消 ----
    def rows(self) -> list[dict]:
        return [self._row(r) for r in db.conn().execute("SELECT * FROM job_queue WHERE queue=? ORDER BY prio, id", (self.name,))]

    def groups(self) -> list[dict]:
        """排隊中的工作（每組一項，依要做的順序）：{grp, kind, prio, n, manual}。"""
        out: dict[int, dict] = {}
        for r in self.rows():
            g = out.setdefault(r["grp"], {"grp": r["grp"], "kind": r["kind"], "prio": r["prio"], "n": 0, "manual": r["manual"],
                                          "arg": r["arg"] if not isinstance(r["arg"], list) else None})
            s = _size(r["arg"])
            g["n"] = (g["n"] or 0) + s if s is not None else None
        return list(out.values())

    def group_rows(self, grp: int) -> list[dict]:
        return [self._row(r) for r in db.conn().execute("SELECT * FROM job_queue WHERE queue=? AND grp=?", (self.name, grp))]

    def cancel_group(self, grp: int) -> list[dict]:
        rows = self.group_rows(grp)
        with db.write_lock:
            db.conn().execute("DELETE FROM job_queue WHERE queue=? AND grp=?", (self.name, grp))
        return rows

    def qsize(self) -> int:
        return db.conn().execute("SELECT COUNT(DISTINCT grp) FROM job_queue WHERE queue=?", (self.name,)).fetchone()[0]

    def empty(self) -> bool:
        return self.qsize() == 0

    @staticmethod
    def _row(r) -> dict:
        try:
            arg = json.loads(r["arg"]) if r["arg"] is not None else None
        except ValueError:
            arg = None
        return {"id": r["id"], "kind": r["kind"], "arg": arg, "prio": r["prio"], "grp": r["grp"], "manual": bool(r["manual"])}


def remove_ids(q: "PQueue", kind: str, ids: set, below_prio: int) -> set:
    """把這些 id 從優先序比 below_prio 低的排隊項目裡拿掉（例如你手動按 S 的圖，本來排在背景補搜的後面）。"""
    removed: set = set()
    with db.write_lock:
        c = db.conn()
        for r in c.execute("SELECT id, arg FROM job_queue WHERE queue=? AND kind=? AND prio>?", (q.name, kind, below_prio)).fetchall():
            try:
                arg = json.loads(r["arg"])
            except ValueError:
                continue
            if not isinstance(arg, list):
                continue
            keep = [x for x in arg if x not in ids]
            if len(keep) != len(arg):
                removed.update(x for x in arg if x in ids)
                if keep:
                    c.execute("UPDATE job_queue SET arg=? WHERE id=?", (json.dumps(keep), r["id"]))
                else:
                    c.execute("DELETE FROM job_queue WHERE id=?", (r["id"],))
    return removed
