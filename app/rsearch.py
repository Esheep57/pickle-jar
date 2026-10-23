"""以圖搜圖（手動按才會做，只上傳這一張、縮到 1600px 以內）：由程式在背景上傳，拿到結果頁網址再開給你看。

為什麼不在瀏覽器裡直接送：Google Lens 等網站拒絕從別的網頁送來的圖（403），
「複製圖片再貼上」又常因為開新分頁時頁面失去焦點而複製失敗。改由程式上傳：
- Fluffle（furry 圖）：官方 API，結果直接列在大圖檢視裡
- TinEye：結果列在大圖檢視裡，也附完整結果頁連結
"""
from __future__ import annotations

import io
import json
import logging
import re
import time
from pathlib import Path

log = logging.getLogger("rsearch")
BROWSER_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0 Safari/537.36"
FLUFFLE_UA = "PickleJar/1.2 (local image organizer)"


class SearchError(Exception):
    pass


def _client():
    import httpx
    return httpx.Client(timeout=40, follow_redirects=False, headers={"User-Agent": BROWSER_UA, "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.8"})


def image_bytes(path_or_img, max_side: int = 1600, fmt: str = "JPEG", max_bytes: int = 0) -> bytes:
    from PIL import Image
    from . import media
    img = path_or_img if not isinstance(path_or_img, (str, Path)) else media.load_image(Path(path_or_img))
    img = media.to_rgb(img)
    img.thumbnail((max_side, max_side), Image.LANCZOS)
    q = 90
    while True:
        buf = io.BytesIO()
        img.save(buf, fmt, quality=q)
        b = buf.getvalue()
        if not max_bytes or len(b) <= max_bytes or q <= 50:
            return b
        q -= 10


def tineye(data: bytes) -> dict:
    with _client() as c:
        r = c.post("https://tineye.com/api/v1/result_json/", params={"sort": "score", "order": "desc"},
                   files={"image": ("image.jpg", data, "image/jpeg")},
                   headers={"Origin": "https://tineye.com", "Referer": "https://tineye.com/"})
        if r.status_code >= 400:
            raise SearchError(f"TinEye HTTP {r.status_code}")
        d = r.json()
        qh = d.get("query_hash") or (d.get("query") or {}).get("hash")
        out = {"url": f"https://tineye.com/search/{qh}/" if qh else "https://tineye.com/", "total": d.get("num_matches")}
        res = []
        for m in (d.get("matches") or [])[:10]:
            b = (m.get("backlinks") or [{}])[0]
            res.append({"url": b.get("backlink") or b.get("url"), "site": m.get("domain"), "score": m.get("score"),
                        "thumb": m.get("image_url")})
        out["results"] = res
        return out


def fluffle(data: bytes) -> dict:
    import httpx
    r = httpx.post("https://api.fluffle.xyz/exact-search-by-file", timeout=60, headers={"User-Agent": FLUFFLE_UA},
                   files={"file": ("image.jpg", data, "image/jpeg")}, data={"limit": "12"})
    if r.status_code == 429:
        raise SearchError("Fluffle 忙線中，稍後再試")
    if r.status_code >= 400:
        raise SearchError(f"Fluffle HTTP {r.status_code}：{r.text[:120]}")
    d = r.json()
    res = [{"url": x.get("url"), "site": x.get("platform"), "match": x.get("match"), "score": round((1 - float(x.get("distance") or 0)) * 100),
            "thumb": (x.get("thumbnail") or {}).get("url"), "authors": [a.get("name") for a in x.get("authors") or []], "sfw": x.get("isSfw")}
           for x in d.get("results") or []]
    return {"url": "https://fluffle.xyz/", "results": res}


# Google Lens（結果頁綁定上傳的連線，瀏覽器開是 403）、Bing（上傳格式已改，400）、Yandex（擋程式上傳）實測都不行 → 大圖檢視改成「下載後自己拖進去」
ENGINES = {"tineye": tineye, "fluffle": fluffle}


def search(engine: str, path: str) -> dict:
    fn = ENGINES.get(engine)
    if not fn:
        raise SearchError("不支援的網站")
    data = image_bytes(path, 1600 if engine != "fluffle" else 1000, max_bytes=3_800_000 if engine == "fluffle" else 8_000_000)
    return fn(data)


def selftest(engine: str) -> dict:
    """診斷用：用程式產生的測試圖（不是你的圖）試上傳，看每個網站現在還收不收。"""
    from PIL import Image, ImageDraw
    img = Image.new("RGB", (512, 512), (240, 235, 225))
    d = ImageDraw.Draw(img)
    for i in range(0, 512, 32):
        d.ellipse((i, i // 2, i + 120, i // 2 + 120), outline=(i % 255, 80, 200 - i % 200), width=6)
    d.rectangle((150, 300, 380, 420), fill=(40, 90, 160))
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=90)
    t = time.time()
    try:
        out = ENGINES[engine](buf.getvalue())
        return {"ok": True, "url": out.get("url"), "n": len(out.get("results") or []), "sec": round(time.time() - t, 1)}
    except Exception as e:  # noqa
        return {"ok": False, "error": f"{type(e).__name__}: {e}"[:300], "sec": round(time.time() - t, 1)}
