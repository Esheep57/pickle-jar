"""匯出訓練集（app/dataset.py）：圖片＋同名 .txt 標註。"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .. import dataset
from .steps import Range, resolve

router = APIRouter()


class DatasetReq(Range):
    dest: str = ""
    opts: dict | None = None
    dry: bool = False


@router.post("/api/dataset/export")
def dataset_export(req: DatasetReq):
    """dry：張數、略過幾張、前三張的標註預覽；否則開始匯出（背景），回 job。"""
    ids = resolve(req)
    try:
        if req.dry:
            return dataset.preview(ids, req.opts)
        return dataset.start(ids, req.dest, req.opts)
    except dataset.DatasetError as e:
        raise HTTPException(400, str(e))


@router.get("/api/dataset/job/{jid}")
def dataset_job(jid: str):
    j = dataset.status(jid)
    if not j:
        raise HTTPException(404, "沒有這個匯出工作")
    return j


class JobId(BaseModel):
    id: str


