"""ascii2d 半自動找出處（見 app/ascii2d.py）。

/api/ascii2d/*       圖庫頁面用（同源）
/api/ascii2d/ext/*   ascii2d 頁面上的油猴腳本用：一律要帶配對金鑰（X-Import-Key），server.py 的跨站檢查只對這些放行
"""
from __future__ import annotations

from fastapi import APIRouter, Header, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel

from .. import ascii2d, importer

router = APIRouter()


class StartReq(BaseModel):
    ids: list[int] | None = None
    q: str | None = None
    only_missing: bool = True
    limit: int | None = None
    replace: bool = True
    include_x: bool = False
    low: bool = False


@router.get("/api/ascii2d")
def overview():
    ascii2d.prune()          # 排進來以後已經不用找的先拿掉，「還剩幾張」才準（b75）
    first, second = ascii2d.targets(split=True)
    return {**ascii2d.counts(), "targets": len(first) + len(second), "no_char": len(first), "has_char": len(second),
            "script": dict(ascii2d.SEEN), "queue": ascii2d.queue_forecast()}


@router.get("/api/ascii2d/preview")
def preview(q: str = "", only_missing: bool = True, limit: int | None = None, include_x: bool = False, low: bool = False):
    """選範圍時即時顯示：符合幾張、其中還沒有創作者的幾張、會排幾張、預估找到幾張和要多久。"""
    r = ascii2d.select(q, None, only_missing, limit, include_x, low)
    return {k: v for k, v in r.items() if k != "ids"}


@router.get("/api/ascii2d/months")
def months():
    return ascii2d.months()


@router.post("/api/ascii2d/start")
def start(body: StartReq):
    """只排你選的範圍：選取的圖（ids）或搜尋條件（q），可以限制張數。什麼都沒給＝全部還沒有創作者的圖。"""
    if body.ids or body.q is not None:
        sel = ascii2d.select(body.q or "", body.ids, body.only_missing, body.limit, body.include_x, body.low)
        if not sel["ids"]:
            raise HTTPException(400, "這個範圍裡沒有要找的圖")
        return {**ascii2d.start(sel["ids"], body.replace, picked=not body.only_missing), "matched": sel["matched"],
                **ascii2d.queue_forecast()}
    return ascii2d.start(None, body.replace)


@router.post("/api/ascii2d/clear")
def clear():
    return ascii2d.clear()


@router.post("/api/ascii2d/stop")
def stop():
    ascii2d.stop()
    return ascii2d.counts()


# ---------------- 油猴腳本用 ----------------
def _key(k: str | None):
    if not importer.check_key(k):
        raise HTTPException(403, "配對金鑰不正確：到 設定 › 從網站匯入 重新安裝腳本")


@router.get("/api/ascii2d/ext/state")
def ext_state(x_import_key: str | None = Header(default=None), x_script_version: str | None = Header(default=None)):
    _key(x_import_key)
    import time
    ascii2d.SEEN.update(version=(x_script_version or "?")[:10], at=time.time())   # 圖庫的說明視窗顯示「腳本有連上」
    return ascii2d.counts()


@router.post("/api/ascii2d/ext/next")
def ext_next(x_import_key: str | None = Header(default=None)):
    _key(x_import_key)
    return ascii2d.next_item() or {"mid": None, **ascii2d.counts()}


@router.get("/api/ascii2d/ext/image/{mid}")
def ext_image(mid: int, x_import_key: str | None = Header(default=None)):
    _key(x_import_key)
    try:
        return Response(ascii2d.upload_image(mid), media_type="image/jpeg", headers={"Cache-Control": "no-store"})
    except FileNotFoundError:
        raise HTTPException(404)


@router.get("/api/ascii2d/ext/thumb/{mid}")
def ext_thumb(mid: int, x_import_key: str | None = Header(default=None)):
    """面板上顯示「你的圖」用的小縮圖。"""
    _key(x_import_key)
    from .. import media
    p = media.thumb_path(mid)
    if not p.exists():
        raise HTTPException(404)
    return Response(p.read_bytes(), media_type="image/jpeg", headers={"Cache-Control": "no-store"})


class ResultReq(BaseModel):
    mid: int
    page_url: str = ""
    items: list[dict] = []
    debug: str | None = None


@router.post("/api/ascii2d/ext/result")
def ext_result(body: ResultReq, x_import_key: str | None = Header(default=None)):
    _key(x_import_key)
    try:
        return ascii2d.receive(body.mid, body.page_url, body.items, body.debug)
    except KeyError:
        raise HTTPException(404, "圖庫裡沒有這張圖")


def _old_script(v: str | None) -> str | None:
    """腳本比程式舊 → 面板上要顯示的提醒（舊腳本也會顯示 result 的 error 欄位）。
    Tampermonkey 一天才檢查一次更新，使用者常常一直用舊版（2026-10 實際：程式 2.2、腳本還是 1.9）。"""
    import re
    from .imports import USERSCRIPT
    latest = re.search(r"@version\s+(\S+)", USERSCRIPT).group(1)
    try:
        if tuple(map(int, (v or "0").split("."))) >= tuple(map(int, latest.split("."))):
            return None
    except ValueError:
        pass
    import os
    port = os.environ.get("LIB_PORT", "7860")
    return f"油猴腳本是舊版 {v or '?'}（最新 {latest}），有些修正沒有生效：開 http://127.0.0.1:{port}/api/fetch-url/userscript.user.js 按「更新」，再重新整理這頁"


@router.get("/api/ascii2d/ext/result/{mid}")
def ext_get_result(mid: int, x_import_key: str | None = Header(default=None), x_script_version: str | None = Header(default=None)):
    _key(x_import_key)
    r = ascii2d.result(mid)
    old = _old_script(x_script_version)
    if old:
        r["error"] = (r["error"] + "\n" if r.get("error") else "") + old
    return r


class DecideReq(BaseModel):
    mid: int
    outcome: str
    accept: list[str] = []


@router.post("/api/ascii2d/ext/decide")
def ext_decide(body: DecideReq, x_import_key: str | None = Header(default=None)):
    _key(x_import_key)
    try:
        return ascii2d.decide(body.mid, body.outcome, body.accept)
    except ValueError:
        raise HTTPException(400, "outcome 要是 found／none／skip")
