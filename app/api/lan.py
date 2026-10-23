"""手機／平板：同一個 Wi-Fi 下，掃 QR code 打開 /m，輸入 PIN 後可以看圖庫（只讀）、把照片/影片傳進圖庫。

- 需要在設定開啟「手機／平板」並重新啟動（伺服器才會綁到區網位址）。
- 區網：/m 與 /api/m/* 隨時可用；輸入 PIN 後可以 GET 看圖需要的 API（server.LAN_READ_PREFIX），其他一律拒絕（見 server.guard）。
- PIN 錯誤過多會暫時鎖住該 IP。
"""
from __future__ import annotations

import hashlib
import hmac
import io
import os
import re
import secrets
import socket
import time
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, Response

from .. import db, media, pipeline

router = APIRouter()
STATIC = Path(__file__).resolve().parent.parent.parent / "static"
_SECRET: bytes | None = None


def _secret() -> bytes:
    """登入 cookie 的簽章金鑰：存在 data/lan_secret，程式重開（更新）後手機不用重新輸入 PIN。換 PIN 一樣會讓舊的登入失效。"""
    global _SECRET
    if _SECRET is None:
        f = db.DATA_DIR / "lan_secret"
        try:
            _SECRET = f.read_bytes()
        except OSError:
            _SECRET = b""
        if len(_SECRET) < 32:
            _SECRET = secrets.token_bytes(32)
            try:
                f.write_bytes(_SECRET)
            except OSError:
                pass
    return _SECRET
_fails: dict[str, list[float]] = {}
MAX_UPLOAD = 2 << 30                       # 單檔上限 2GB


def enabled() -> bool:
    s = db.get_settings()
    return bool(s.get("lan_enabled") and s.get("lan_pin"))


def lan_ip() -> str:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))  # 不會真的送封包，只是讓系統挑出對外網卡
            return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"


def _token() -> str:
    pin = db.get_settings().get("lan_pin") or ""
    return hmac.new(_secret(), pin.encode(), hashlib.sha256).hexdigest()


def _authed(request: Request) -> bool:
    client = request.client.host if request.client else ""
    if client in ("127.0.0.1", "::1", "testclient"):
        return True
    return enabled() and hmac.compare_digest(request.cookies.get("lan") or "", _token())


@router.get("/m")
def mobile_page():
    return FileResponse(STATIC / "m.html", headers={"Cache-Control": "no-store"})


@router.post("/api/m/login")
async def mobile_login(request: Request):
    ip = request.client.host if request.client else "?"
    now = time.time()
    recent = [t for t in _fails.get(ip, []) if now - t < 300]
    if len(recent) >= 5:
        raise HTTPException(429, "錯誤太多次，請 5 分鐘後再試")
    body = await request.json()
    pin = str(body.get("pin") or "")
    if not enabled() or not hmac.compare_digest(pin, db.get_settings()["lan_pin"]):
        _fails[ip] = recent + [now]
        raise HTTPException(401, "PIN 不正確")
    _fails.pop(ip, None)
    r = Response('{"ok":true}', media_type="application/json")
    r.set_cookie("lan", _token(), httponly=True, samesite="strict", max_age=30 * 86400)
    return r


@router.get("/api/m/me")
def mobile_me(request: Request):
    """remote：從手機／平板（區網）連進來的 → 圖庫畫面切成「只看」模式。"""
    client = request.client.host if request.client else ""
    return {"ok": _authed(request), "dir": str(upload_dir()), "remote": client not in ("127.0.0.1", "::1", "testclient")}


def upload_dir() -> Path:
    s = db.get_settings()
    if s.get("upload_dir"):
        return Path(s["upload_dir"])
    if s["roots"]:
        return Path(s["roots"][0]) / "手機上傳"
    return db.DATA_DIR / "uploads"


_SAFE = re.compile(r'[\\/:*?"<>|\x00-\x1f]')


@router.put("/api/m/upload")
async def mobile_upload(request: Request):
    """一次一個檔案，原始 bytes 放 body，檔名放 X-Filename（URL 編碼）。"""
    if not _authed(request):
        raise HTTPException(401, "請先輸入 PIN")
    from urllib.parse import unquote
    name = _SAFE.sub("_", os.path.basename(unquote(request.headers.get("x-filename") or "upload.jpg"))).strip() or "upload.jpg"
    if media.kind_of(Path(name)) is None:
        raise HTTPException(400, f"不支援的檔案類型：{name}")
    d = upload_dir() / time.strftime("%Y-%m")
    d.mkdir(parents=True, exist_ok=True)
    stem, ext = os.path.splitext(name)
    import uuid
    tmp = d / f".upload-{uuid.uuid4().hex}.part"   # 每個上傳自己的暫存檔：兩支手機同時傳同名檔也不會混在一起
    size = 0
    try:
        with open(tmp, "wb") as f:
            async for chunk in request.stream():
                size += len(chunk)
                if size > MAX_UPLOAD:
                    raise HTTPException(413, "檔案太大")
                f.write(chunk)
        # 最後才挑檔名，用 O_EXCL 保證不會兩個上傳搶到同一個名字
        i = 0
        while True:
            dst = d / (name if i == 0 else f"{stem} ({i}){ext}")
            try:
                os.close(os.open(dst, os.O_CREAT | os.O_EXCL | os.O_WRONLY))
                break
            except FileExistsError:
                i += 1
        os.replace(tmp, dst)
    finally:
        tmp.unlink(missing_ok=True)   # 失敗或中斷時清掉暫存檔
    # 不在圖庫資料夾內（例如 data/uploads）→ 直接掃描這個檔案
    pipeline.get_jobs().submit("scan", [str(dst)])
    return {"ok": True, "saved": dst.name, "size": size}


@router.get("/api/lan/info")
def lan_info(request: Request):
    s = db.get_settings()
    port = request.url.port or 7860
    url = f"http://{lan_ip()}:{port}/m"
    return {"enabled": bool(s.get("lan_enabled")), "pin": s.get("lan_pin") or "", "url": url,
            "active": request.url.hostname not in ("127.0.0.1", "localhost") or _bound_lan(),
            "dir": str(upload_dir())}


def _bound_lan() -> bool:
    return os.environ.get("LIB_HOST", "127.0.0.1") not in ("127.0.0.1", "localhost")


@router.post("/api/lan/pin")
def new_pin():
    pin = f"{secrets.randbelow(10 ** 6):06d}"
    db.update_settings({"lan_pin": pin})
    return {"pin": pin}


@router.get("/api/lan/qr.svg")
def lan_qr(request: Request):
    try:
        import qrcode
        import qrcode.image.svg
    except ImportError:
        raise HTTPException(501, "沒有安裝 qrcode 套件")
    port = request.url.port or 7860
    img = qrcode.make(f"http://{lan_ip()}:{port}/m", image_factory=qrcode.image.svg.SvgPathImage, box_size=12)
    buf = io.BytesIO()
    img.save(buf)
    return Response(buf.getvalue(), media_type="image/svg+xml")
