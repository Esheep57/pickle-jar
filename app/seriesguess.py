"""角色還不知道屬於哪部作品時（Danbooru 還沒查、查不到）先推測，書架不用全部擠在「未知作品」。

開發時的圖庫實測（72 個「未知作品」的角色）：40 個根本還沒查過 Danbooru（重啟時線上佇列會清空、以前只排新角色），
28 個是畫師的原創角色（Danbooru 上只有 original，以前被當成「沒有作品」）。
推測只用兩種比較可靠的：
1. 名字尾巴就是作品：karen_(blue_archive) → blue_archive（圖庫裡要真的有這個作品標籤）
2. 同一張圖上你確認過的作品：至少 2 張、6 成以上是同一部（只有 1 張不算：
   實測 felix_argyle、fujisaki_chihiro 只有一張 cosplay／串場圖，會被推成別的作品）
推測的結果只用來分組顯示（標「推測」），不寫進資料卡；Danbooru 查到了就以 Danbooru 為準。
"""
from __future__ import annotations

import re
import time
from collections import Counter, defaultdict

from . import db

_SUFFIX = re.compile(r"_\(([^()]+(?:\([^()]*\))?)\)$")
_cache: dict = {"sig": None, "at": 0.0, "map": {}}


def guess_all() -> dict[str, str]:
    """{角色名稱: 推測的作品}（只列資料卡沒有作品的角色）。"""
    c = db.conn()
    sig = c.execute("SELECT COUNT(*), IFNULL(MAX(rowid),0) FROM media_tags WHERE state=1").fetchone()
    sig = (tuple(sig), c.execute("SELECT COUNT(*) FROM tag_info WHERE COALESCE(copyright,'')!=''").fetchone()[0])
    if _cache["sig"] == sig and time.time() - _cache["at"] < 600:
        return _cache["map"]
    cps = {r[0] for r in c.execute("SELECT name FROM tags WHERE category='copyright'")}
    todo = {r[0]: r[1] for r in c.execute("""SELECT t.id, t.name FROM tags t LEFT JOIN tag_info ti ON ti.tag_id=t.id
        WHERE t.category='character' AND COALESCE(ti.copyright,'')=''
          AND EXISTS (SELECT 1 FROM media_tags x WHERE x.tag_id=t.id AND x.state=1)""")}
    out: dict[str, str] = {}
    need = []
    for tid, name in todo.items():
        m = _SUFFIX.search(name)
        if m and m.group(1) in cps:
            out[name] = m.group(1)
        else:
            need.append(tid)
    if need:
        co: dict[int, Counter] = defaultdict(Counter)
        n: Counter = Counter()
        for i in range(0, len(need), 500):
            ch = need[i:i + 500]
            ph = ",".join("?" * len(ch))
            for tid, k in c.execute(f"SELECT tag_id, COUNT(*) FROM media_tags WHERE state=1 AND tag_id IN ({ph}) GROUP BY tag_id", ch):
                n[tid] = k
            for tid, cp in c.execute(f"""SELECT a.tag_id, t2.name FROM media_tags a JOIN media_tags b ON b.media_id=a.media_id AND b.state=1
                    JOIN tags t2 ON t2.id=b.tag_id AND t2.category='copyright' WHERE a.state=1 AND a.tag_id IN ({ph})""", ch):
                co[tid][cp] += 1
        for tid in need:
            if not co[tid]:
                continue
            cp, k = co[tid].most_common(1)[0]
            if k >= 2 and k >= 0.6 * n[tid]:
                out[todo[tid]] = cp
    _cache.update(sig=sig, at=time.time(), map=out)
    return out
