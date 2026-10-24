"""備份到外接硬碟（app/drivebackup.py）。"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .. import db, drivebackup as dbk

router = APIRouter()


class DestReq(BaseModel):
    dest: str = ""


@router.get("/api/drive-backup")
def drive_backup_info():
    """插著的硬碟、上次備份到哪裡、上次的結果、正在跑的工作。"""
    s = db.get_settings()
    found = dbk.find_existing()
    dest = s.get("drive_backup_dir") or ""
    import os
    if found and (not dest or not os.path.isdir(dest)):
        dest = found                      # 磁碟機代號換了：用找到舊備份的那顆
    return {"drives": dbk.drives(), "dest": dest, "found": found, "last": dbk.last(), "job": dbk.status(),
            "remind_days": dbk.REMIND_DAYS, "hold_days": dbk.HOLD_DAYS}


@router.post("/api/drive-backup/plan")
def drive_backup_plan(body: DestReq):
    return dbk.plan(body.dest)


@router.post("/api/drive-backup/start")
def drive_backup_start(body: DestReq):
    try:
        return dbk.start(body.dest)
    except dbk.BackupError as e:
        raise HTTPException(400, str(e))


@router.get("/api/drive-backup/job")
def drive_backup_job():
    return dbk.status()


@router.post("/api/drive-backup/cancel")
def drive_backup_cancel():
    return {"ok": dbk.cancel()}
