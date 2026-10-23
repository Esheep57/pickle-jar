"""分享：把選的圖放到 Windows 剪貼簿，到 QQ／Messenger 的聊天視窗按 Ctrl+V 貼上。

剪貼簿裡放兩種東西（同一次）：
- 檔案（跟在檔案總管按 Ctrl+C 一樣）：原檔、GIF 會動、影片也可以；可以好幾張
- 只有一張靜態圖時，另外放「圖片」（點陣圖＋PNG）：只收圖片、不收檔案的地方（網頁版 Messenger 等）也貼得上

聊天軟體多半不收的格式（HEIC、AVIF、TIFF、靜態 WebP）先轉成 JPG／PNG 放在 data/share_tmp（一天後清掉），原檔不動。
瀏覽器的剪貼簿 API 只能放 PNG（GIF 會變靜態、影片不行、大圖變很大），所以在伺服器這邊做（伺服器就在你的電腦上）。
"""
from __future__ import annotations

import base64
import logging
import os
import subprocess
import sys
import time
from pathlib import Path

from . import db

log = logging.getLogger(__name__)

MAX_FILES = 50                     # 一次最多幾張（聊天軟體一次也送不了更多）
BITMAP_MAX_PIXELS = 40_000_000     # 太大的圖只放檔案（點陣圖會吃掉好幾百 MB 記憶體）
CHAT_OK = {".jpg", ".jpeg", ".png", ".gif", ".bmp"}
CONVERT = {".heic", ".heif", ".avif", ".tif", ".tiff", ".webp", ".jfif"}
VIDEO = {".mp4", ".mov", ".webm", ".mkv", ".avi", ".m4v"}
TMP_KEEP = 24 * 3600


def tmp_dir() -> Path:
    return db.DATA_DIR / "share_tmp"


def _clean_tmp():
    d = tmp_dir()
    if not d.is_dir():
        return
    now = time.time()
    for p in d.iterdir():
        try:
            if now - p.stat().st_mtime > TMP_KEEP:
                p.unlink()
        except OSError:
            pass


def _animated(im) -> bool:
    return bool(getattr(im, "is_animated", False)) and getattr(im, "n_frames", 1) > 1


def _convert(mid: int, path: Path) -> Path | None:
    """聊天軟體不收的格式 → JPG（有透明就 PNG）。動態 WebP 不轉（轉了就不會動）。失敗回 None（照原檔送）。"""
    from PIL import Image, ImageOps
    from . import media  # noqa: F401  註冊 HEIC／AVIF
    try:
        with Image.open(path) as im:
            if _animated(im):
                return None
            im = ImageOps.exif_transpose(im)
            alpha = im.mode in ("RGBA", "LA", "PA") or (im.mode == "P" and "transparency" in im.info)
            d = tmp_dir()
            d.mkdir(parents=True, exist_ok=True)
            out = d / f"{path.stem}{'.png' if alpha else '.jpg'}"
            if out.exists() and out.stat().st_mtime >= path.stat().st_mtime:   # 同一張剛轉過
                return out
            if alpha:
                im.convert("RGBA").save(out, "PNG")
            else:
                im.convert("RGB").save(out, "JPEG", quality=95)
            return out
    except Exception as e:  # noqa: BLE001
        log.info("share: %s 轉檔失敗，照原檔送：%s", path, e)
        return None


def _bitmap_ok(path: Path) -> bool:
    from . import media
    if path.suffix.lower() not in {".jpg", ".jpeg", ".png", ".bmp"}:
        return False
    try:
        w, h = media.image_size(path)
    except Exception:  # noqa: BLE001
        return False
    return 0 < w * h <= BITMAP_MAX_PIXELS


def prepare(ids: list[int]) -> dict:
    """決定要放進剪貼簿的檔案。回 {files, image, missing, converted}。"""
    _clean_tmp()
    ids = list(dict.fromkeys(int(i) for i in ids))[:MAX_FILES]
    rows = {}
    if ids:
        q = ",".join("?" * len(ids))
        rows = {r["id"]: r for r in db.conn().execute(
            f"SELECT id, path, status FROM media WHERE id IN ({q})", ids)}
    files, missing, converted, kinds = [], 0, 0, []
    for mid in ids:
        r = rows.get(mid)
        if not r or r["status"] in ("trashed", "missing") or not os.path.isfile(r["path"]):
            missing += 1
            continue
        p = Path(r["path"])
        ext = p.suffix.lower()
        if ext in CONVERT:
            out = _convert(mid, p)
            if out:
                p, converted = out, converted + 1
        files.append(str(p))
        kinds.append("video" if ext in VIDEO else "gif" if p.suffix.lower() == ".gif" else "image")
    image = files[0] if len(files) == 1 and kinds[0] == "image" and _bitmap_ok(Path(files[0])) else None
    return {"files": files, "image": image, "missing": missing, "converted": converted,
            "videos": kinds.count("video"), "gifs": kinds.count("gif")}


# PowerShell（-STA：剪貼簿要單執行緒）。路徑從環境變數傳，不用處理引號、中文。
# SetDataObject(..., $true)：程式結束後剪貼簿的內容還在
_PS = r"""
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
$do = New-Object System.Windows.Forms.DataObject
$sc = New-Object System.Collections.Specialized.StringCollection
foreach ($f in ($env:PJ_SHARE_FILES -split "`n")) { if ($f) { [void]$sc.Add($f) } }
$do.SetFileDropList($sc)
$do.SetData('Preferred DropEffect', (New-Object System.IO.MemoryStream (,[byte[]](1,0,0,0))))
if ($env:PJ_SHARE_IMAGE) {
  $img = [System.Drawing.Image]::FromFile($env:PJ_SHARE_IMAGE)
  $do.SetImage($img)
  $ms = New-Object System.IO.MemoryStream
  $img.Save($ms, [System.Drawing.Imaging.ImageFormat]::Png)
  $ms.Position = 0
  $do.SetData('PNG', $ms)
}
for ($i = 0; $i -lt 5; $i++) {
  try { [System.Windows.Forms.Clipboard]::SetDataObject($do, $true, 5, 100); break }
  catch { if ($i -eq 4) { throw }; Start-Sleep -Milliseconds 200 }
}
"""


def supported() -> bool:
    return sys.platform == "win32"


def set_clipboard(files: list[str], image: str | None) -> None:
    """放進 Windows 剪貼簿；失敗丟 RuntimeError（訊息給使用者看）。"""
    env = dict(os.environ, PJ_SHARE_FILES="\n".join(files), PJ_SHARE_IMAGE=image or "")
    enc = base64.b64encode(_PS.encode("utf-16-le")).decode()
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-STA", "-ExecutionPolicy", "Bypass",
                            "-EncodedCommand", enc], env=env, capture_output=True, timeout=30,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except FileNotFoundError:
        raise RuntimeError("找不到 PowerShell")
    except subprocess.TimeoutExpired:
        raise RuntimeError("剪貼簿沒有回應（別的程式正在用？再試一次）")
    if r.returncode != 0:
        err = (r.stderr or b"").decode("utf-8", "replace").strip().splitlines()
        log.warning("share: PowerShell 失敗：%s", " ".join(err)[:500])
        raise RuntimeError("放不進剪貼簿" + (f"：{err[0][:120]}" if err else ""))


def copy(ids: list[int]) -> dict:
    plan = prepare(ids)
    if not plan["files"]:
        return {"ok": False, "reason": "nofile", "missing": plan["missing"]}
    if not supported():
        return {"ok": False, "reason": "unsupported", **{k: plan[k] for k in ("missing", "converted")}}
    set_clipboard(plan["files"], plan["image"])
    return {"ok": True, "n": len(plan["files"]), "image": bool(plan["image"]),
            **{k: plan[k] for k in ("missing", "converted", "videos", "gifs")},
            "capped": len(dict.fromkeys(ids)) > MAX_FILES}
