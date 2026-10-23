"""讀圖、縮圖、感知雜湊（dHash）、影片抽幀。"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path

import numpy as np
from PIL import Image, ImageOps

Image.MAX_IMAGE_PIXELS = 300_000_000

# iPhone 的 HEIC/HEIF（以及舊版 Pillow 的 AVIF）：有裝 pillow-heif 就支援
HEIF_OK = False
try:
    import pillow_heif
    pillow_heif.register_heif_opener()
    HEIF_OK = True
    try:
        pillow_heif.register_avif_opener()
    except Exception:
        pass
except Exception:
    pass

IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".tif", ".tiff", ".avif", ".jfif"} | (
    {".heic", ".heif"} if HEIF_OK else set())
VIDEO_EXT = {".mp4", ".webm", ".mkv", ".mov", ".avi", ".m4v", ".wmv", ".flv"}

THUMB_DIR = Path(os.environ.get("LIB_DATA_DIR") or Path(__file__).resolve().parent.parent / "data") / "thumbs"
THUMB_SIZE = 400


def kind_of(path: Path) -> str | None:
    e = path.suffix.lower()
    if e in IMAGE_EXT:
        return "image"
    if e in VIDEO_EXT:
        return "video"
    return None


def fingerprint(path: str | Path, kind: str) -> str:
    """檔案內容指紋：圖片用完整 MD5（也拿來查 Danbooru 原檔）；影片太大，用 大小＋頭尾各 4MB 的 MD5。
    檔案搬家或改名後，用這個把原本的標記接回來。"""
    h = hashlib.md5()
    if kind == "image":
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        return h.hexdigest()
    size = os.path.getsize(path)
    h.update(str(size).encode())
    with open(path, "rb") as f:
        h.update(f.read(4 << 20))
        if size > 8 << 20:
            f.seek(-(4 << 20), os.SEEK_END)
            h.update(f.read())
    return "v:" + h.hexdigest()


def thumb_path(mid: int, large: bool = False) -> Path:
    return THUMB_DIR / f"{mid // 1000:04d}" / (f"{mid}_l.webp" if large else f"{mid}.webp")


def image_size(path) -> tuple[int, int]:
    """檔案真正的尺寸（只讀檔頭，考慮 EXIF 旋轉）。
    不能用 load_image() 的 img.size：大 JPEG 會用 draft 縮小解碼（1/2、1/4），以前把縮小後的尺寸存成圖的尺寸，
    圖庫裡約一成的 JPEG 記成實際的一半，「換高清」因此把同一張原檔當成比較大的版本。"""
    with Image.open(path) as im:
        w, h = im.size
        try:
            o = im.getexif().get(0x0112)
        except Exception:  # noqa
            o = None
    return (h, w) if o in (5, 6, 7, 8) else (w, h)


def load_image(path: Path) -> Image.Image:
    img = Image.open(path)
    if img.format == "JPEG":
        img.draft("RGB", (1024, 1024))  # 大 JPEG 快速降採樣解碼
    img = ImageOps.exif_transpose(img) if img.format in ("JPEG", "TIFF", "WEBP") else img
    if getattr(img, "is_animated", False):
        img.seek(0)
    img.load()
    if img.mode in ("CMYK", "I;16", "I", "F", "YCbCr", "L", "1"):
        img = img.convert("RGB")
    return img


def to_rgb(img: Image.Image) -> Image.Image:
    if img.mode in ("RGBA", "LA", "P"):
        img = img.convert("RGBA")
        bg = Image.new("RGB", img.size, (255, 255, 255))
        bg.paste(img, mask=img.split()[-1])
        return bg
    return img.convert("RGB")


def save_thumb(img: Image.Image, mid: int, large: bool = False):
    p = thumb_path(mid, large)
    p.parent.mkdir(parents=True, exist_ok=True)
    t = to_rgb(img).copy()
    n = 900 if large else THUMB_SIZE
    t.thumbnail((n, n), Image.LANCZOS)
    t.save(p, "WEBP", quality=82, method=4)


def dhash(img: Image.Image) -> int:
    """64-bit difference hash，回傳有號 64 位整數（方便存 SQLite）。"""
    g = to_rgb(img).convert("L").resize((9, 8), Image.BILINEAR)
    a = np.asarray(g, dtype=np.int16)
    bits = (a[:, 1:] > a[:, :-1]).flatten()
    v = 0
    for b in bits:
        v = (v << 1) | int(b)
    return v - (1 << 64) if v >= (1 << 63) else v


def exif_time(path: Path) -> float | None:
    """相機拍攝時間（EXIF DateTimeOriginal），沒有就回傳 None。"""
    try:
        import datetime
        with Image.open(path) as im:
            ex = im.getexif()
            v = ex.get_ifd(0x8769).get(0x9003) or ex.get(0x0132)
        if v:
            return datetime.datetime.strptime(str(v).strip()[:19], "%Y:%m:%d %H:%M:%S").timestamp()
    except Exception:
        pass
    return None


def video_frames(path: Path, max_frames: int):
    """均勻抽幀。回傳 (frames[list of PIL], duration_sec, width, height)。"""
    import cv2
    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        raise RuntimeError("無法開啟影片（缺少解碼器？）")
    n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    fps = cap.get(cv2.CAP_PROP_FPS) or 0
    w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
    h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
    dur = n / fps if fps > 0 and n > 0 else 0.0
    k = int(min(max_frames, max(4, dur / 10))) if dur else max_frames
    frames = []
    if n > 0:
        positions = np.linspace(n * 0.05, n * 0.95, k).astype(int)
        for pos in positions:
            cap.set(cv2.CAP_PROP_POS_FRAMES, int(pos))
            ok, fr = cap.read()
            if ok:
                frames.append(Image.fromarray(cv2.cvtColor(fr, cv2.COLOR_BGR2RGB)))
    if not frames:  # 無法定位（部分格式）→ 順序讀取
        cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
        i = 0
        while len(frames) < k:
            ok, fr = cap.read()
            if not ok:
                break
            if i % 30 == 0:
                frames.append(Image.fromarray(cv2.cvtColor(fr, cv2.COLOR_BGR2RGB)))
            i += 1
    cap.release()
    if not frames:
        raise RuntimeError("讀不到影格")
    return frames, dur, w, h
