"""換成高清版（upgrade.py）：查、換、不用換、清單。"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .. import db, pipeline, upgrade
from .common import Ids

router = APIRouter()


class UrlReq(BaseModel):
    url: str


class CheckReq(BaseModel):
    ids: list[int] | None = None


@router.get("/api/upgrade/summary")
def summary():
    return upgrade.summary()


@router.post("/api/upgrade/check")
def check_all(body: CheckReq):
    """補查：沒給 ids → 全部有出處、還沒查過的圖。排進線上佇列（背景，可以停）。"""
    if not db.get_settings()["online_enabled"]:
        raise HTTPException(400, "線上功能關著（設定 › 線上）")
    return {"queued": upgrade.queue_unchecked(body.ids)}


@router.post("/api/upgrade/replace")
def replace_many(body: Ids):
    """整批換（背景工作，看得到進度）。"""
    ids = [int(i) for i in body.ids][:5000]
    if not ids:
        return {"queued": 0}
    pipeline.get_jobs().submit("upgrade", {"ids": ids})
    return {"queued": len(ids)}


@router.post("/api/upgrade/dismiss")
def dismiss(body: Ids):
    return {"dismissed": upgrade.dismiss(body.ids)}


@router.get("/api/upgrade/{mid}")
def get_one(mid: int):
    return upgrade.info(mid) or {"status": None}


@router.post("/api/upgrade/{mid}/check")
def check_one(mid: int):
    """馬上查這一張（已知的出處）。"""
    try:
        r = upgrade.check(mid)
    except Exception as e:  # noqa
        raise HTTPException(502, f"查不到：{e}")
    return {**r, "info": upgrade.info(mid)}


@router.post("/api/upgrade/{mid}/url")
def check_url(mid: int, body: UrlReq):
    """你貼的貼文網址：只查那一篇，比較大就變成可以換。"""
    try:
        r = upgrade.check_url(mid, body.url)
    except upgrade.UpgradeError as e:
        raise HTTPException(400, str(e))
    except Exception as e:  # noqa
        raise HTTPException(502, f"讀不到這篇貼文：{e}")
    return {**r, "info": upgrade.info(mid)}


class RestoreReq(BaseModel):
    ids: list[int] | None = None


_RESTORE: dict = {}


@router.post("/api/restore-missing")
def restore_missing(body: RestoreReq):
    """找不到檔案的圖：從搜源找到的出處重新下載、接回（b97.2）。ids 沒給＝全部有出處的。背景做，進度 GET 同網址。"""
    from .. import tasks
    ids = body.ids if body.ids is not None else upgrade.restorable_ids()
    if _RESTORE.get("running"):
        raise HTTPException(409, "已經在救了")
    _RESTORE.clear()
    _RESTORE.update(running=True, done=0, total=len(ids), results=[])

    def _run():
        try:
            upgrade.restore_run(ids, _RESTORE)
        finally:
            _RESTORE["running"] = False
    tasks.spawn("restore", f"從出處救回（{len(ids)} 張）", _run, probe=tasks.probe_of(_RESTORE))
    return {"queued": len(ids)}


@router.get("/api/restore-missing")
def restore_status():
    return {"restorable": len(upgrade.restorable_ids()), **_RESTORE}


@router.post("/api/upgrade/{mid}/replace")
def replace_one(mid: int):
    """換這一張（馬上做，大圖用）。換好後重新標記（縮圖、特徵），你確認過的都不動。"""
    try:
        r = upgrade.replace(mid)
    except upgrade.UpgradeError as e:
        raise HTTPException(400, str(e))
    pipeline.get_jobs().submit("retag", [mid])
    return {**r, "info": upgrade.info(mid)}
