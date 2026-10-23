"""從畫師作品全集反查（app/sweep.py）：Pixiv（伺服器自己抓）、X（瀏覽器腳本在媒體頁送來）。
/api/sweep/ext/*   x.com 上的油猴腳本用：一律要帶配對金鑰（X-Import-Key），server.py 的跨站檢查只對這些放行"""
from __future__ import annotations

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel

from .. import db, importer, online, sweep

router = APIRouter()


def _online():
    if not db.get_settings()["online_enabled"]:
        raise HTTPException(400, "線上功能已在設定中關閉")


@router.get("/api/sweep")
def state():
    return sweep.summary()


class PixivReq(BaseModel):
    keys: list[str] = []
    max_artists: int = 30
    rescan: bool = False


@router.post("/api/sweep/pixiv")
def start_pixiv(req: PixivReq):
    _online()
    keys = [k for k in (str(x).strip() for x in req.keys) if k.isdigit()][:500]
    n = max(1, min(req.max_artists, 2000))
    # 一位畫師一塊：做完一位就讓出佇列（你按的搜源、資料卡插得進來）；停止＝整組取消
    if keys:
        todo = keys
    else:
        import time
        old = time.time() - sweep.RESCAN_DAYS * 86400
        todo = [a["key"] for a in sweep.artists("pixiv") if req.rescan or not a["scanned_at"] or a["scanned_at"] < old][:n]
    if not todo:
        return {"queued": 0}
    online.get_jobs().submit("pixivsweep", [{"key": k, "rescan": req.rescan} for k in todo])
    return {"queued": len(todo)}


class XQueueReq(BaseModel):
    handles: list[str] = []
    limit: int = 200


@router.post("/api/sweep/x/queue")
def x_queue(req: XQueueReq):
    return {"queued": sweep.x_queue([h.strip().lstrip("@") for h in req.handles if h.strip()] or None, max(1, min(req.limit, 2000)))}


@router.get("/api/sweep/x/next")
def x_next_local():
    return sweep.x_next() or {"handle": None, "left": 0}


# ---------------- x.com 上的腳本 ----------------
def _key(k: str | None):
    if not importer.check_key(k):
        raise HTTPException(403, "配對金鑰不正確：到 設定 › 從網站匯入 重新安裝腳本")


@router.post("/api/sweep/ext/x/next")
def ext_next(x_import_key: str | None = Header(default=None)):
    _key(x_import_key)
    return sweep.x_next() or {"handle": None, "left": 0}


@router.post("/api/sweep/ext/x/check")
def ext_check(body: dict, x_import_key: str | None = Header(default=None)):
    """腳本打開某個帳號的媒體頁時問：圖庫裡有這位畫師嗎？"""
    _key(x_import_key)
    h = str(body.get("handle") or "").lstrip("@")
    a = sweep.x_artist(h)
    return {"handle": h, "artist": a}


class XItems(BaseModel):
    handle: str
    items: list[dict] = []
    final: bool = False


@router.post("/api/sweep/ext/x/items")
def ext_items(body: XItems, x_import_key: str | None = Header(default=None)):
    _key(x_import_key)
    _online()
    h = body.handle.strip().lstrip("@")
    if not h or not sweep.x_artist(h):
        raise HTTPException(404, "圖庫裡沒有這個帳號的畫師")
    items = [{"tweet_id": str(i.get("tweet_id") or "")[:25], "url": str(i.get("url") or "")[:300]} for i in body.items[:400]]
    from .. import jobqueue
    # 你正在 X 上看著：插隊（不然排在背景搜源後面，要等很久才比對）
    online.get_jobs().submit("xsweep", {"handle": h, "items": items, "final": body.final}, jobqueue.MANUAL)
    return {"queued": len(items), **sweep.summary()["x"]}
