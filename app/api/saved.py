"""智慧相簿（已存的搜尋）與搜尋說明。"""
from __future__ import annotations

from fastapi import APIRouter

import time

from pydantic import BaseModel

from .. import db, query
from ..query import where as _where

router = APIRouter()

# ---------- 已存的搜尋（智慧相簿）----------
class SavedReq(BaseModel):
    name: str
    query: str
    view: str | None = None


@router.get("/api/saved")
def saved_list(nsfw: str = "hide"):
    c = db.conn()
    out = []
    for r in c.execute("SELECT * FROM saved_search ORDER BY id"):
        d = dict(r)
        try:
            join, where, params = _where(r["view"] or "all", nsfw, r["query"], None)
            d["n"] = c.execute(f"SELECT COUNT(*) FROM media m {join} WHERE {where}", params).fetchone()[0]
        except Exception:  # noqa
            d["n"] = None
        out.append(d)
    return out


@router.post("/api/saved")
def saved_add(body: SavedReq):
    with db.write_lock:
        c = db.conn()
        cur = c.execute("INSERT INTO saved_search(name, query, view, created_at) VALUES(?,?,?,?)",
                        (body.name.strip() or body.query, body.query, body.view, time.time()))
        c.commit()
    return {"id": cur.lastrowid}


@router.delete("/api/saved/{sid}")
def saved_del(sid: int):
    with db.write_lock:
        db.conn().execute("DELETE FROM saved_search WHERE id=?", (sid,))
        db.conn().commit()
    return {"ok": True}


@router.get("/api/query/explain")
def query_explain(q: str = ""):
    """搜尋沒有結果：哪個字找不到、你是不是要找…"""
    return query.explain(q[:300])


# 標籤名稱可能含「/」（fate/grand_order），所以用 {name:path}；更具體的 /info、/merge、/delete 要排在前面先比對
router.routes.sort(key=lambda r: 0 if getattr(r, "path", "").endswith(("/info", "/merge", "/delete")) else 1)
