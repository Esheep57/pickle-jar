"""不同作品的角色、顯示名字一樣（紫苑：轉生史萊姆／碧藍幻想；梅；背包）：名字後面加作品名（b110.7，使用者要求「容易重名的加上系列名」）。

背景算一次：圖庫裡有圖的角色＋角色表裡有作品的角色（約 4 萬個，約 2 秒），繁體、簡體模式各算一份「哪些名字不只一部作品在用」。
同一系列（母作品相同，例如 Fate 各作）不算重名。資料有變（查到新中文名、加了新角色）才重算。
"""
from __future__ import annotations

import json
import logging
import threading
import time

from . import db, names

log = logging.getLogger("dupnames")
CATALOG_MIN = 10
_S: dict = {"dup": {False: set(), True: set()}, "cp": {}, "sig": None, "at": 0.0}
_LOCK = threading.Lock()
_running = False


def suffix(name: str, disp: str, cn: bool) -> str:
    if disp not in _S["dup"].get(cn, ()):
        return disp
    cp = _S["cp"].get(name)
    if not cp:
        return disp
    s = names.series_display(cp, None, None, "zh", cn=cn)
    return f"{disp}（{s}）" if s and s != disp else disp


def _signature(c) -> tuple:
    a = c.execute("SELECT COUNT(*), MAX(id) FROM tags WHERE category='character'").fetchone()
    try:
        z = c.execute("SELECT COUNT(*), MAX(checked_at) FROM name_zh").fetchone()
    except Exception:  # noqa
        z = (0, 0)
    try:
        k = c.execute("SELECT COUNT(*) FROM char_catalog").fetchone()
    except Exception:  # noqa
        k = (0,)
    return tuple(a) + tuple(z) + tuple(k) + (db._user_gen,)


def compute() -> int:
    c = db.conn()
    sig = _signature(c)
    if sig == _S["sig"]:
        return -1
    rows = {}
    for n, cp, on, dn in c.execute("""SELECT t.name, ti.copyright, ti.other_names, ti.display_name FROM tags t
            JOIN tag_info ti ON ti.tag_id=t.id WHERE t.category='character' AND ti.copyright IS NOT NULL
            AND EXISTS (SELECT 1 FROM media_tags x WHERE x.tag_id=t.id AND x.state>=0)"""):
        rows[n] = (cp, json.loads(on or "[]"), dn)
    try:
        for n, cp, on in c.execute("SELECT name, copyright, other_names FROM char_catalog WHERE copyright IS NOT NULL AND posts >= ?",
                                   (CATALOG_MIN,)):
            rows.setdefault(n, (cp, json.loads(on or "[]"), None))
    except Exception:  # noqa  沒下載角色表
        pass
    allcp = {r[0] for r in rows.values()}
    root_of: dict[str, str] = {}

    def root(cp: str) -> str:
        if cp not in root_of:
            k, seen = cp, set()
            while k and k not in seen:
                seen.add(k)
                p = names.series_parent(k, allcp - {k})
                if not p:
                    break
                k = p
            root_of[cp] = k
        return root_of[cp]

    dup = {}
    for cn in (False, True):
        by: dict[str, set] = {}
        for n, (cp, on, dn) in rows.items():
            if dn:
                continue                       # 你自己取的名字不動
            d = names._display_core(n, on, None, "zh", cn=cn)
            if not names.HAN_CHAR.search(d or ""):
                continue                       # 英文名（沒有中文）不加
            by.setdefault(d, set()).add(root(cp))
        dup[cn] = {d for d, cps in by.items() if len(cps) > 1}
    with _LOCK:
        _S.update(dup=dup, cp={n: r[0] for n, r in rows.items()}, sig=sig, at=time.time())
    try:
        from . import nameindex
        nameindex.invalidate()
    except Exception:  # noqa
        pass
    return len(dup[False])


def start(delay: float = 60, every: float = 1800):
    """啟動後 delay 秒算第一次，之後每 every 秒看資料有沒有變。"""
    global _running
    if _running:
        return
    _running = True

    def loop():
        time.sleep(delay)
        while True:
            try:
                t0 = time.time()
                n = compute()
                if n >= 0:
                    log.info("同名角色：%d 個名字（%.1f 秒）", n, time.time() - t0)
            except Exception as e:  # noqa
                log.info("同名角色計算失敗：%s", e)
            time.sleep(every)

    threading.Thread(target=loop, name="dupnames", daemon=True).start()
