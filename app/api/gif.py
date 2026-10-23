"""影片轉 GIF（app/togif.py）：開始轉、看進度、預覽、存進圖庫、複製分享、取消。"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from .. import togif

router = APIRouter()


class MakeReq(BaseModel):
    id: int
    start: float
    end: float
    fps: int = 15
    width: int = 480
    dither: bool = True
    after: str | None = None      # 做好後自動：share（複製分享）／save（存進圖庫）


def _err(e: togif.GifError):
    return HTTPException(400, str(e))


@router.post("/api/gif/make")
def make(body: MakeReq):
    try:
        return togif.start(body.id, body.start, body.end, body.fps, body.width, body.dither, body.after)
    except togif.GifError as e:
        raise _err(e)


@router.get("/api/gif/job/{jid}")
def job(jid: str):
    try:
        return togif.public(togif.get(jid))
    except togif.GifError as e:
        raise HTTPException(404, str(e))


@router.get("/api/gif/job/{jid}/file")
def job_file(jid: str):
    try:
        j = togif.get(jid)
    except togif.GifError as e:
        raise HTTPException(404, str(e))
    if j["status"] not in ("done", "after") or not j["file"]:
        raise HTTPException(409, "GIF 還沒做好")
    return FileResponse(j["file"], media_type="image/gif", filename=togif.dest_for_name(j),
                        content_disposition_type="inline", headers={"Cache-Control": "no-store"})


@router.post("/api/gif/job/{jid}/cancel")
def job_cancel(jid: str):
    togif.cancel(jid)
    return {"ok": True}


@router.post("/api/gif/job/{jid}/save")
def job_save(jid: str):
    try:
        return togif.save(jid)
    except togif.GifError as e:
        raise _err(e)


@router.post("/api/gif/job/{jid}/share")
def job_share(jid: str):
    try:
        return togif.share(jid)
    except togif.GifError as e:
        raise _err(e)
    except RuntimeError as e:
        raise HTTPException(409, str(e))
