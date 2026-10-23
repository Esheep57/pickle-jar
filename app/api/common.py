"""跨路由共用：套用標籤動作、否決傳播排程。"""
from __future__ import annotations

import threading

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .. import candidates, db, pipeline
from ..matcher import matcher

router = APIRouter()
# ---------- 編輯 ----------
class TagOp(BaseModel):
    ids: list[int]
    action: str            # confirm | add | reject | replace | unreject
    tag: str
    from_tag: str | None = None
    category: str | None = None


_rematch_timer: threading.Timer | None = None


def _schedule_rematch():
    global _rematch_timer
    from .. import charknn
    charknn.schedule()          # 確認／否決之後：新角色辨識重算（你確認的圖就是它的教材）
    if not db.get_settings()["auto_rematch"]:
        return
    if _rematch_timer:
        _rematch_timer.cancel()
    _rematch_timer = threading.Timer(20.0, lambda: pipeline.get_jobs().submit("sweep"))
    _rematch_timer.daemon = True
    _rematch_timer.start()


def _set_state(c, ids, tid, state):
    for mid in ids:
        c.execute("""INSERT INTO media_tags(media_id, tag_id, score, source, state) VALUES(?,?,NULL,'manual',?)
            ON CONFLICT(media_id, tag_id) DO UPDATE SET state=excluded.state""", (mid, tid, state))


def _apply(action: str, name: str, ids: list[int], category: str | None = None, from_tag: str | None = None):
    """套用一個標籤動作（需在 write_lock 內）。回傳 (撤銷用紀錄, 否決傳播數, 同組候選數)。"""
    c = db.conn()
    tid = db.tag_id(name, category or "custom")
    cat = c.execute("SELECT category FROM tags WHERE id=?", (tid,)).fetchone()[0]
    rows = db.snapshot_tag_rows(ids, tid)
    propagated = set_added = 0
    if action in ("confirm", "add", "replace"):
        if action == "replace":
            if not from_tag:
                raise HTTPException(400)
            ftid = db.tag_id(from_tag, create=False)
            if ftid:
                rows += db.snapshot_tag_rows(ids, ftid)
                _set_state(c, ids, ftid, -1)
        _set_state(c, ids, tid, 1)
        if cat == "character":
            rows += _confirm_base(c, ids, tid)
        from .. import implies
        rows += implies.apply(c, ids, tid)          # 標籤蘊含：A 也代表 B（b84）
        from .. import quicktags
        if name in quicktags.no_char_names():    # 加上梗圖、截圖：還沒決定的身分候選拿掉
            rows += quicktags.purge_candidates(c, ids)
    elif action == "reject":
        _set_state(c, ids, tid, -1)
        from .. import implies
        rows += implies.unapply(c, ids, tid)        # 只因為它才帶上的標籤一起拿掉
        # 否決傳播：其他很像的圖上、自動加上的同一個 tag 一起移除
        if matcher.built:
            sim_ids = matcher.similar_auto_rows(tid, ids)
            if sim_ids:
                rows += db.snapshot_tag_rows(sim_ids, tid)
                c.executemany("DELETE FROM media_tags WHERE media_id=? AND tag_id=? AND state=0",
                              [(i, tid) for i in sim_ids])
                propagated = len(sim_ids)
    elif action == "unreject":
        c.executemany("DELETE FROM media_tags WHERE media_id=? AND tag_id=? AND state=-1", [(i, tid) for i in ids])
    else:
        raise HTTPException(400, "unknown action")
    return rows, propagated, set_added


def _confirm_base(c, ids: list[int], tid: int) -> list:
    """確認造型（hakui_koyori_(1st_costume)）時，同一張圖上還沒決定的本體候選（hakui_koyori）一起確認。
    以前要按兩次：開發時的紀錄 636 張圖本體和造型都是分開確認的。只做「造型 → 本體」，反過來不做（造型比較具體，可能認錯）。"""
    try:
        r = c.execute("SELECT base_id FROM char_base WHERE tag_id=?", (tid,)).fetchone()
    except Exception:  # noqa  舊資料庫還沒有 char_base
        return []
    if not r or r[0] == tid:
        return []
    bid = r[0]
    ph = ",".join("?" * len(ids))
    todo = [x[0] for x in c.execute(f"SELECT media_id FROM media_tags WHERE tag_id=? AND state=0 AND media_id IN ({ph})", [bid, *ids])]
    if not todo:
        return []
    rows = db.snapshot_tag_rows(todo, bid)
    c.executemany("UPDATE media_tags SET state=1 WHERE media_id=? AND tag_id=?", [(m, bid) for m in todo])
    return rows


LABELS = {"confirm": "確認", "add": "加上", "reject": "不是", "replace": "換成", "unreject": "取消否決"}


class Ids(BaseModel):
    ids: list[int]
