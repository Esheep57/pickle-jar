"""Danbooru 角色表（app/charcat.py）：狀態、下載／更新、刪除。"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .. import charcat, db, online

router = APIRouter()


@router.get("/api/charcat")
def charcat_status():
    j = online.get_jobs()
    st = j.state
    rows = j.q.rows()
    return {**charcat.status(), "tiers": list(charcat.TIERS), "zh": db.get_settings().get("charcat_zh", True),
            "online": db.get_settings()["online_enabled"],
            "running": bool(st.get("running") and st.get("kind") == "charcat"), "phase": st.get("phase") if st.get("kind") == "charcat" else "",
            "queued": any(r["kind"] == "charcat" for r in rows),
            "cp_queued": sum(len(r["arg"]) for r in rows if r["kind"] == "charcat_cp" and isinstance(r["arg"], list))}


class DlReq(BaseModel):
    min: int = 10
    zh: bool | None = None


@router.post("/api/charcat/download")
def charcat_download(body: DlReq):
    if not db.get_settings()["online_enabled"]:
        raise HTTPException(400, "線上功能關著：先到 設定 › 自動標記與搜源 打開")
    if body.min not in charcat.TIERS:
        raise HTTPException(400, f"門檻要是 {'、'.join(map(str, charcat.TIERS))} 其中一個")
    if body.zh is not None:
        db.update_settings({"charcat_zh": bool(body.zh)})
    from .. import jobqueue
    j = online.get_jobs()
    if not any(r["kind"] == "charcat" for r in j.q.rows()):
        j.submit("charcat", {"min": body.min}, jobqueue.MANUAL)
    return {"ok": True}


@router.post("/api/charcat/delete")
def charcat_delete():
    charcat.clear()
    return {"ok": True}
