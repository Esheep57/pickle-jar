"""快速標籤從你的標記學（b93）：表情包、真人、梗圖、截圖…你按數字鍵標過的圖當答案，在 WD 特徵上各訓練一個分類器，
找出「AI 覺得也是」但你還沒標的圖。只是建議：標籤頁「AI 覺得還有 N 張可能是」、搜尋 `guess:表情包`，你選了按數字鍵才算。

實驗（開發時的資料 10-19，5 折交叉驗證；沒標的一律當成「不是」，所以準確度是下限）：
| 快速標籤 | 你標的 | 門檻 0.9 抓回 | 準確度（下限） | 新建議 |
| 梗圖 | 5,668 | 50% | 90% | 331 |
| 截圖 | 1,530 | 73% | 87% | 167 |
| 真人 | 386 | 65% | 58% | 178 |
| 表情包 | 327 | 64% | 67% | 102 |
| 3D | 36 | 33% | 43% | 16 |
抽查「新建議」（模型說是、你沒標）：表情包 12 張裡約 10 張真的是表情包／反應圖、真人 12 張裡 10 張是真人照片
→ 下限低是因為很多真的還沒標，不是模型錯。3D 標太少，不夠學。

- 標過 MIN_POS 張以上的快速標籤才訓練；你「拿掉」過的（表 qt_no）當成「不是」，之後也不再建議同一張。
- 結果存 qt_pred(media_id, tag_id, p)（只存 p ≥ 0.5、還沒標的圖）；門檻 THRESHOLD。
- 什麼時候重算：啟動後、快速標籤加上／拿掉後 2 分鐘（合併成一次）。整庫一個標籤約幾秒（numpy，CPU）。
- 不碰本子裡的頁。
"""
from __future__ import annotations

import logging
import math
import threading
import time

import numpy as np

from . import db

log = logging.getLogger("qtlearn")
MIN_POS = 50       # 3D 只有 36 張：準確度下限 43%，不夠學
THRESHOLD = 0.9
SCHEMA = """CREATE TABLE IF NOT EXISTS qt_pred(media_id INTEGER NOT NULL, tag_id INTEGER NOT NULL, p REAL NOT NULL, PRIMARY KEY(tag_id, media_id));
CREATE TABLE IF NOT EXISTS qt_no(media_id INTEGER NOT NULL, tag_id INTEGER NOT NULL, at REAL, PRIMARY KEY(tag_id, media_id))"""
_state: dict = {"running": False, "last": None}
_timer: threading.Timer | None = None
_lock = threading.Lock()


def ensure_schema(c):
    c.executescript(SCHEMA)


def _fit(X: np.ndarray, y: np.ndarray, steps: int = 300, l2: float = 1e-2, lr: float = 0.05):
    """二元 logistic regression（Adam、全批次、正例加權）。X 已標準化。"""
    w = np.zeros(X.shape[1], np.float32); b = 0.0
    pw = (len(y) - y.sum()) / max(y.sum(), 1)
    sw = np.where(y == 1, pw, 1.0).astype(np.float32); sw /= sw.mean()
    m = np.zeros_like(w); v = np.zeros_like(w); mb = vb = 0.0
    for t in range(1, steps + 1):
        z = np.clip(X @ w + np.float32(b), -30, 30)
        g = (1 / (1 + np.exp(-z)) - y) * sw
        gw = X.T @ g / len(y) + l2 * w; gb = float(g.mean())
        m = .9 * m + .1 * gw; v = .999 * v + .001 * gw * gw
        mb = .9 * mb + .1 * gb; vb = .999 * vb + .001 * gb * gb
        w -= lr * (m / (1 - .9 ** t)) / (np.sqrt(v / (1 - .999 ** t)) + 1e-8)
        b -= lr * (mb / (1 - .9 ** t)) / (math.sqrt(vb / (1 - .999 ** t)) + 1e-8)   # math：np.sqrt 回 float64，整條算式會變成 float64、慢 30 倍
    return w, b


def run(names: list[str] | None = None) -> dict:
    """重算全部快速標籤（或指定的）。回傳 {名字: 建議張數}。"""
    from . import quicktags
    if not _lock.acquire(blocking=False):
        return {"busy": True}
    _state["running"] = True
    out: dict = {}
    try:
        c = db.conn()
        rows = c.execute("""SELECT id, embedding FROM media WHERE status='done' AND embedding IS NOT NULL
            AND id NOT IN (SELECT media_id FROM album_items)""").fetchall()
        if not rows:
            return {}
        ids = np.array([r[0] for r in rows])
        E = np.stack([np.frombuffer(r[1], dtype=np.float16).astype(np.float32) for r in rows])
        mu, sd = E.mean(0), E.std(0) + 1e-6
        X = (E - mu) / sd
        del E
        for n in (names or quicktags.names()):
            tid = db.tag_id(n, "custom", create=False)
            if not tid:
                continue
            pos = {r[0] for r in c.execute("SELECT media_id FROM media_tags WHERE tag_id=? AND state=1", (tid,))}
            no = {r[0] for r in c.execute("SELECT media_id FROM qt_no WHERE tag_id=?", (tid,))}
            y = np.fromiter((1.0 if m in pos else 0.0 for m in ids), np.float32, len(ids))
            if y.sum() < MIN_POS:
                with db.write_lock:
                    c.execute("DELETE FROM qt_pred WHERE tag_id=?", (tid,))
                out[n] = None
                continue
            w, b = _fit(X, y)
            p = 1 / (1 + np.exp(-np.clip(X @ w + b, -30, 30)))
            keep = [(int(m), tid, float(pp)) for m, pp, yy in zip(ids, p, y) if yy == 0 and pp >= 0.5 and int(m) not in no]
            with db.write_lock:
                c.execute("DELETE FROM qt_pred WHERE tag_id=?", (tid,))
                c.executemany("INSERT INTO qt_pred(media_id, tag_id, p) VALUES(?,?,?)", keep)
                c.commit()
            out[n] = sum(1 for k in keep if k[2] >= THRESHOLD)
        _state["last"] = {"at": time.time(), "result": out}
        return out
    finally:
        _state["running"] = False
        _lock.release()


def schedule(delay: float = 120.0):
    """快速標籤加上／拿掉後：等一下（連續標很多張時合併）再重算。"""
    global _timer
    if _timer:
        _timer.cancel()

    def go():
        from . import tasks
        tasks.spawn("qtlearn", "快速標籤：從你的標記學", run, heavy=True)
    _timer = threading.Timer(delay, go)
    _timer.daemon = True
    _timer.start()


def removed(ids: list[int], tid: int):
    """你拿掉了快速標籤：當成「不是」，不再建議這幾張（在 write_lock 內呼叫）。"""
    db.conn().executemany("INSERT OR REPLACE INTO qt_no(media_id, tag_id, at) VALUES(?,?,?)", [(i, tid, time.time()) for i in ids])
    db.conn().executemany("DELETE FROM qt_pred WHERE media_id=? AND tag_id=?", [(i, tid) for i in ids])


def guess_sql(name: str) -> tuple[str, list]:
    """搜尋 guess:表情包：AI 覺得是、你還沒標的。"""
    tid = db.tag_id(name.replace(" ", "_"), create=False)
    if not tid:
        return "0", []
    return (f"m.id IN (SELECT media_id FROM qt_pred WHERE tag_id={int(tid)} AND p>=?) "
            f"AND m.id NOT IN (SELECT media_id FROM media_tags WHERE tag_id={int(tid)} AND state>=0)"), [THRESHOLD]


def has_model(tid: int) -> bool:
    try:
        return bool(db.conn().execute("SELECT 1 FROM qt_pred WHERE tag_id=? LIMIT 1", (tid,)).fetchone())
    except Exception:  # noqa  還沒遷移
        return False
