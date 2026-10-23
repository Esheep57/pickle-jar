"""像素指紋比對：跟 Danbooru 原檔「像素完全一樣」的圖（只是檔案不同：去掉 EXIF、PNG 重新壓縮、改檔名…）。

Danbooru 的 pixel_hash ＝ MD5( PAM 標頭 + 轉成 sRGB、補上透明通道後的原始 RGBA 像素 )。
我們用同樣的方式在本機算，然後一次查 50 個（posts.json?tags=pixelhash:a,b,c…）。
- 只送出指紋（32 個字元），不上傳任何圖片
- 對上就是同一張圖，不會誤判；對不上（JPEG 被重新壓縮過）就是沒有，不影響其他方法
- 算過的指紋按檔案 MD5 快取，下次不用重算
"""
from __future__ import annotations

import hashlib
import logging
import time

from . import db

log = logging.getLogger("hashscan")
SCHEMA = "CREATE TABLE IF NOT EXISTS pixhash(fp TEXT PRIMARY KEY, ph TEXT)"
BATCH = 50
_ready = False


def _ensure():
    global _ready
    if not _ready:
        with db.write_lock:
            db.conn().execute(SCHEMA)
        _ready = True


def pixel_hash(path: str) -> str | None:
    """跟 Danbooru MediaFile::Image#pixel_hash 同樣的算法。有內嵌色彩描述檔（ICC）或 16 位元的圖算不準 → 回傳 None。"""
    from PIL import Image, ImageOps
    try:
        with Image.open(path) as im:
            if getattr(im, "is_animated", False) and getattr(im, "n_frames", 1) > 1:
                return None          # 動圖 Danbooru 用檔案 MD5，一般搜源已經查過
            if im.info.get("icc_profile"):
                return None          # Danbooru 會用 libvips 做色彩轉換，本機重現不了
            if im.mode in ("I", "I;16", "I;16B", "I;16L", "F") or (im.mode == "RGB" and im.info.get("bits", 8) > 8):
                return None
            if im.format == "JPEG" or im.format == "PNG":
                im = ImageOps.exif_transpose(im)
            if im.mode == "CMYK":
                return None
            im = im.convert("RGBA")
            w, h = im.size
            head = f"P7\nWIDTH {w}\nHEIGHT {h}\nDEPTH 4\nMAXVAL 255\nTUPLTYPE RGB_ALPHA\nENDHDR\n".encode()
            return hashlib.md5(head + im.tobytes()).hexdigest()
    except Exception:  # noqa
        return None


def cached_hash(fp: str | None, path: str) -> str | None:
    """單張圖的像素指紋（搜源時用），有快取就用快取。"""
    _ensure()
    c = db.conn()
    if fp:
        r = c.execute("SELECT ph FROM pixhash WHERE fp=?", (fp,)).fetchone()
        if r:
            return r[0]
    ph = pixel_hash(path)
    if fp:
        with db.write_lock:
            c.execute("INSERT OR REPLACE INTO pixhash(fp, ph) VALUES(?,?)", (fp, ph))
    return ph


def _hashes(rows, stop, st) -> dict[int, str]:
    """media id → 像素指紋（有快取就用快取）。"""
    _ensure()
    c = db.conn()
    fps = [r["fp"] for r in rows if r["fp"]]
    cache = {}
    for i in range(0, len(fps), 800):
        ch = fps[i:i + 800]
        cache.update({r[0]: r[1] for r in c.execute(f"SELECT fp, ph FROM pixhash WHERE fp IN ({','.join('?' * len(ch))})", ch)})
    out, new = {}, []
    for r in rows:
        if stop():
            break
        if r["fp"] in cache:
            ph = cache[r["fp"]]
        else:
            ph = pixel_hash(r["path"])
            st["hashed"] += 1
            if r["fp"]:
                new.append((r["fp"], ph))
            if len(new) >= 200:
                with db.write_lock:
                    c.executemany("INSERT OR REPLACE INTO pixhash(fp, ph) VALUES(?,?)", new)
                new = []
        if ph:
            out[r["id"]] = ph
        st["checked"] += 1
    if new:
        with db.write_lock:
            c.executemany("INSERT OR REPLACE INTO pixhash(fp, ph) VALUES(?,?)", new)
    return out


FIELDS = ("id,md5,source,rating,pixiv_id,image_width,image_height,tag_string_character,tag_string_copyright,"
          "tag_string_artist,tag_string_general,media_asset")


def run(state: dict, stop, dry_run: bool = False, verify: int = 0):
    """verify > 0：先拿已經對上 Danbooru 的圖，檢查本機算的指紋跟 Danbooru 的一不一致（驗證算法）。"""
    from .danbooru import DANBOORU, _post_summary, dan_get
    from .online import auto_apply, save_source
    st = state.setdefault("pixelhash", {})
    st.update(checked=0, hashed=0, queried=0, matched=0, matches=[], verify_ok=0, verify_bad=0, verify_skip=0,
              dry_run=dry_run, started=time.time(), finished=None, error=None)
    c = db.conn()
    if verify:
        rows = c.execute("""SELECT m.id, m.path, m.fp, s.post_url FROM media m JOIN media_source s ON s.media_id=m.id
            WHERE m.kind='image' AND s.status='found' AND s.method IN ('md5','md5_name')
            ORDER BY RANDOM() LIMIT ?""", (verify,)).fetchall()
        state["phase"] = "驗證像素指紋算法"
        for r in rows:
            if stop():
                break
            try:
                pid = int(r["post_url"].rstrip("/").rsplit("/", 1)[1])
                p = dan_get(f"/posts/{pid}.json", {"only": "id,media_asset"})
            except Exception:  # noqa
                st["verify_skip"] += 1
                continue
            mine = pixel_hash(r["path"])
            theirs = (p.get("media_asset") or {}).get("pixel_hash")
            if not mine or not theirs:
                st["verify_skip"] += 1
            elif mine == theirs:
                st["verify_ok"] += 1
            else:
                st["verify_bad"] += 1
    rows = c.execute("""SELECT m.id, m.path, m.fp FROM media m
        WHERE m.kind='image' AND m.status='done' AND COALESCE(m.excluded,0)=0
          AND m.id NOT IN (SELECT media_id FROM album_items)
          AND NOT EXISTS (SELECT 1 FROM media_source s WHERE s.media_id=m.id AND s.status='found')""").fetchall()
    state.update(phase="計算像素指紋", total=len(rows), done=0)
    st["targets"] = len(rows)
    hs = _hashes(rows, stop, st)
    by_ph: dict[str, list[int]] = {}
    for mid, ph in hs.items():
        by_ph.setdefault(ph, []).append(mid)
    keys = list(by_ph)
    state.update(phase="向 Danbooru 查像素指紋", total=len(keys), done=0)
    for i in range(0, len(keys), BATCH):
        if stop():
            break
        ch = keys[i:i + BATCH]
        try:
            posts = dan_get("/posts.json", {"tags": "pixelhash:" + ",".join(ch), "limit": BATCH, "only": FIELDS})
        except Exception as e:  # noqa
            st["error"] = str(e)[:200]
            break
        st["queried"] += len(ch)
        state["done"] = min(len(keys), i + BATCH)
        for p in posts or []:
            ph = (p.get("media_asset") or {}).get("pixel_hash")
            for mid in by_ph.get(ph, []):
                st["matched"] += 1
                if len(st["matches"]) < 500:
                    st["matches"].append({"id": mid, "post_id": p.get("id")})
                if dry_run:
                    continue
                post = _post_summary(p)
                res = {"status": "found", "method": "pixel_hash", "similarity": 100.0,
                       "post_url": f"{DANBOORU}/posts/{p.get('id')}", "source_url": post["source"] or None,
                       "pixiv_id": post.get("pixiv_id"), "data": {"danbooru": post}}
                save_source(mid, res)
                auto_apply(mid, res)
    st["finished"] = time.time()
    st["seconds"] = round(st["finished"] - st["started"])
    log.info("像素指紋比對完成：%s", {k: v for k, v in st.items() if k != "matches"})
