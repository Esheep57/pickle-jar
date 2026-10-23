"""影片轉 GIF：選一段（開始、結束）、每秒幾格、寬度 → 先做在 data/gif_tmp 給你看，滿意再「存進圖庫」或「複製分享」。

解碼用 OpenCV（掃描影片本來就用它，不用另外裝 ffmpeg），GIF 用 Pillow 寫：
- 調色盤整段共用一份（從這一段平均抽 16 格算 256 色）：每格各算各的會閃（同一塊顏色每格不一樣）
- 抖色（Floyd–Steinberg）可以關：動畫風的大色塊不抖比較乾淨、檔案也小；漸層多的抖了才不會一圈一圈
- 每格的時間累加後四捨五入到 GIF 的 1/100 秒，15 fps 不會變成 14.3 或 16.7
長片段（b83 起最多 2 分鐘）：讀出來的格先寫到 data/gif_tmp 的暫存檔（numpy memmap），量化成 256 色後才放記憶體（每像素 1 byte），
不會因為格數多就吃掉幾 GB 記憶體；暫存檔轉完就刪。
做好後可以自動「複製分享」或「存進圖庫」（after）：一鍵就到剪貼簿，不用再按一次。
存進圖庫：放在影片旁邊（iCloud 照片資料夾裡的影片 → 匯入資料夾的「GIF」），檔名「影片名_開始-結束.gif」；
馬上建紀錄、帶上影片已確認的角色／創作者／作品／自訂標籤和手動 NSFW，再交給掃描標記（跟新圖一樣）。
"""
from __future__ import annotations

import logging
import os
import re
import shutil
import threading
import time
import uuid
from pathlib import Path

from PIL import Image

from . import db, errlog

log = logging.getLogger(__name__)

MAX_SECONDS = 120.0        # 一段最長幾秒（GIF 很肥：480 寬 15 fps 30 秒就十幾 MB，長的會提醒）
MAX_FRAMES = 3600          # 2 分鐘 × 30 fps
MAX_PIXELS = 400_000_000   # 格數 × 寬 × 高：量化後每像素 1 byte 放記憶體，暫存檔是 3 倍
AFTER_CHOICES = ("share", "save")
MIN_WIDTH, MAX_WIDTH = 120, 1280
FPS_CHOICES = (5, 8, 10, 12, 15, 20, 24, 30)
TMP_KEEP = 24 * 3600
PALETTE_SAMPLES = 16       # 至少抽幾格算調色盤；長片段每 40 格多抽一格，最多 48
INHERIT_CATS = ("character", "artist", "copyright", "custom")

JOBS: dict[str, dict] = {}
_lock = threading.Lock()        # 一次轉一個（解碼＋量化都吃 CPU，同時開好幾個只會一起變慢）


class GifError(Exception):
    pass


def tmp_dir() -> Path:
    return db.DATA_DIR / "gif_tmp"


def clean_tmp():
    d = tmp_dir()
    if not d.is_dir():
        return
    now = time.time()
    for p in d.iterdir():
        try:
            if p.suffix == ".npy" and not any(j["status"] in ("queued", "running") for j in JOBS.values()) \
                    and now - p.stat().st_mtime > 3600:
                p.unlink()          # 轉到一半當掉留下的暫存檔
            elif now - p.stat().st_mtime > TMP_KEEP:
                p.unlink()
        except OSError:
            pass
    for k in [k for k, j in JOBS.items() if now - j["created"] > TMP_KEEP]:
        JOBS.pop(k, None)


def _video(mid: int):
    r = db.conn().execute("SELECT id, path, kind, status, duration, width, height FROM media WHERE id=?", (mid,)).fetchone()
    if not r or r["kind"] != "video":
        raise GifError("不是影片")
    if r["status"] in ("trashed", "missing") or not os.path.isfile(r["path"]):
        raise GifError("找不到影片檔案（被搬走、刪掉，或 iCloud 還沒下載）")
    return r


def plan(duration: float, src_w: int, src_h: int, start: float, end: float, fps: int, width: int) -> dict:
    """檢查參數、算出實際用的值（給前端預估、也給轉檔用）。"""
    if fps not in FPS_CHOICES:
        raise GifError(f"每秒格數只能是 {'、'.join(map(str, FPS_CHOICES))}")
    start = max(0.0, float(start))
    end = float(end)
    if duration and duration > 0:
        end = min(end, duration)
    if end - start < 0.1:
        raise GifError("這一段太短（至少 0.1 秒）")
    if end - start > MAX_SECONDS + 0.05:
        raise GifError(f"一次最多 {int(MAX_SECONDS)} 秒（GIF 很大，長的請分段）")
    n = max(1, round((end - start) * fps))
    if n > MAX_FRAMES:
        raise GifError(f"太多格了（{n} 格，最多 {MAX_FRAMES}）：降低每秒格數或縮短一段")
    width = int(width)
    if src_w and width > src_w:
        width = src_w                       # 不放大
    width = max(MIN_WIDTH if not src_w else min(MIN_WIDTH, src_w), min(MAX_WIDTH, width))
    height = round(width * src_h / src_w) if src_w and src_h else 0
    width -= width % 2
    height -= height % 2
    if n * width * (height or width * 9 // 16) > MAX_PIXELS:
        raise GifError(f"太大了（{n} 格 × {width} 寬）：降低寬度或每秒格數，或縮短一段")
    return {"start": round(start, 3), "end": round(end, 3), "fps": fps, "frames": n, "width": width, "height": height}


def frame_durations(n: int, fps: int) -> list[int]:
    """每格幾毫秒（GIF 只存到 1/100 秒）：累加後四捨五入，總長度不會越跑越偏。"""
    cs = [round((k + 1) * 100 / fps) - round(k * 100 / fps) for k in range(n)]
    return [max(2, c) * 10 for c in cs]


def read_frames(path: str, start: float, end: float, fps: int, width: int, progress=None, cancelled=None) -> list[Image.Image]:
    """從 start 讀到 end，每 1/fps 秒取一格（取時間最接近、不早於目標的那格），縮到 width。"""
    return list(iter_frames(path, start, end, fps, width, progress, cancelled))


def iter_frames(path: str, start: float, end: float, fps: int, width: int, progress=None, cancelled=None):
    """read_frames 的一格一格版本（長片段不用全部放記憶體）。"""
    import cv2
    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        raise GifError("打不開這個影片（缺少解碼器？）")
    try:
        vfps = cap.get(cv2.CAP_PROP_FPS) or 30.0
        if vfps <= 0 or vfps > 1000:
            vfps = 30.0
        if start > 0:
            cap.set(cv2.CAP_PROP_POS_MSEC, start * 1000)
        n = max(1, round((end - start) * fps))
        targets = [start + k / fps for k in range(n)]
        half = 0.5 / vfps
        got = 0
        k = 0
        last = None
        idx_guess = None
        while k < n:
            if cancelled and cancelled():
                raise GifError("已取消")
            ok, fr = cap.read()
            if not ok:
                break
            ts = cap.get(cv2.CAP_PROP_POS_MSEC) / 1000.0
            if ts <= 0 and idx_guess is not None:      # 有些格式讀不到時間：用格數推
                ts = idx_guess / vfps
            idx_guess = (idx_guess if idx_guess is not None else round(ts * vfps)) + 1
            if ts + half < targets[k]:
                continue
            img = None
            while k < n and ts + half >= targets[k]:
                if img is None:
                    img = _resize(cv2.cvtColor(fr, cv2.COLOR_BGR2RGB), width)
                yield img
                got += 1
                k += 1
            last = img
            if progress:
                progress(k, n)
        if not got:
            raise GifError("這一段讀不到畫面")
        while got < n and last is not None and got >= n - 2:   # 最後一兩格剛好在影片結尾之外：補最後一格
            yield last
            got += 1
    finally:
        cap.release()


def _resize(rgb, width: int) -> Image.Image:
    im = Image.fromarray(rgb)
    if im.width != width:
        h = max(2, round(im.height * width / im.width))
        im = im.resize((width, h), Image.LANCZOS)
    return im


def build_palette(frames: list[Image.Image]) -> Image.Image:
    """整段共用的 256 色：平均抽幾格、縮小拼在一起算。"""
    k = min(max(PALETTE_SAMPLES, min(48, len(frames) // 40)), len(frames))
    picks = [frames[round(i * (len(frames) - 1) / max(1, k - 1))] for i in range(k)]
    w = min(200, picks[0].width)
    small = [p.resize((w, max(1, round(p.height * w / p.width))), Image.BILINEAR) for p in picks]
    sheet = Image.new("RGB", (w, sum(s.height for s in small)))
    y = 0
    for s in small:
        sheet.paste(s, (0, y))
        y += s.height
    return sheet.quantize(256, method=Image.Quantize.MEDIANCUT)


class _Spool:
    """讀出來的格先寫進暫存檔（memmap），用的時候一格一格拿出來：像 list 一樣可以 len()、[i]。"""

    def __init__(self, path: Path, n: int):
        self.path, self.cap, self.n, self.mm = path, n, 0, None

    def add(self, img: Image.Image):
        import numpy as np
        a = np.asarray(img.convert("RGB"))
        if self.mm is None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.mm = np.lib.format.open_memmap(self.path, mode="w+", dtype=np.uint8, shape=(self.cap, *a.shape))
        if self.n < self.cap:
            self.mm[self.n] = a
            self.n += 1

    def __len__(self):
        return self.n

    def __getitem__(self, i: int) -> Image.Image:
        if i < 0:
            i += self.n
        if not 0 <= i < self.n:
            raise IndexError(i)
        return Image.fromarray(self.mm[i])

    def close(self):
        mm, self.mm = self.mm, None
        if mm is not None:
            mm._mmap.close()        # Windows：不先關掉，檔案刪不掉
            del mm
        try:
            self.path.unlink()
        except OSError:
            pass


def encode(frames, fps: int, out: Path, dither: bool = True, progress=None, cancelled=None):
    pal = build_palette(frames)
    d = Image.Dither.FLOYDSTEINBERG if dither else Image.Dither.NONE
    q = []
    for i, f in enumerate(frames):
        if cancelled and cancelled():
            raise GifError("已取消")
        q.append(f.quantize(palette=pal, dither=d))
        if progress:
            progress(i + 1, len(frames))
    out.parent.mkdir(parents=True, exist_ok=True)
    part = out.with_name(out.name + ".part")
    q[0].save(part, "GIF", save_all=True, append_images=q[1:], duration=frame_durations(len(q), fps),
              loop=0, optimize=False, disposal=1)
    os.replace(part, out)


# ---------- 背景工作 ----------
def start(mid: int, start_s: float, end_s: float, fps: int, width: int, dither: bool = True, after: str | None = None) -> dict:
    clean_tmp()
    if after not in (None, *AFTER_CHOICES):
        raise GifError("做好後只能自動「share」或「save」")
    v = _video(mid)
    p = plan(v["duration"] or 0, v["width"] or 0, v["height"] or 0, start_s, end_s, fps, width)
    jid = uuid.uuid4().hex[:12]
    job = {"id": jid, "mid": mid, "path": v["path"], **p, "dither": bool(dither), "status": "queued",
           "phase": "排隊中", "done": 0, "total": p["frames"], "created": time.time(), "file": None, "size": None,
           "error": None, "cancel": False, "saved": None, "after": after, "shared": None, "after_error": None}
    JOBS[jid] = job
    from . import tasks
    tasks.spawn("gif", "影片轉 GIF", _run, job, probe=tasks.probe_of(job, "phase"), cancel=lambda: job.__setitem__("cancel", True), name=f"gif-{jid}")
    return public(job)


def public(job: dict) -> dict:
    return {k: job[k] for k in ("id", "mid", "start", "end", "fps", "frames", "width", "height", "dither", "status",
                                "phase", "done", "total", "size", "error", "saved",
                                "after", "shared", "after_error")}


def get(jid: str) -> dict:
    j = JOBS.get(jid)
    if not j:
        raise GifError("找不到這個 GIF（超過一天會清掉，重新轉一次）")
    return j


def cancel(jid: str):
    j = JOBS.get(jid)
    if j:
        j["cancel"] = True


def _run(job: dict):
    cancelled = lambda: job["cancel"]  # noqa: E731
    with _lock:
        if job["cancel"]:
            job.update(status="cancelled", phase="已取消")
            return
        t0 = time.time()
        frames = _Spool(tmp_dir() / f"{job['id']}.frames.npy", job["frames"] + 2)
        try:
            job.update(status="running", phase="讀影片")
            for img in iter_frames(job["path"], job["start"], job["end"], job["fps"], job["width"],
                                   progress=lambda d, n: job.update(done=d, total=n), cancelled=cancelled):
                frames.add(img)
            job.update(phase="轉成 GIF", done=0, total=len(frames), height=frames[0].height, frames=len(frames))
            out = tmp_dir() / f"{job['id']}.gif"
            encode(frames, job["fps"], out, job["dither"], progress=lambda d, n: job.update(done=d), cancelled=cancelled)
            frames.close()
            job.update(status="after" if job["after"] else "done",
                       phase={"share": "複製中", "save": "存進圖庫"}.get(job["after"], "完成"),
                       file=str(out), size=out.stat().st_size)
            log.info("GIF %s：影片 %s %.1f–%.1f 秒，%d 格，%.1f MB，%.1f 秒", job["id"], job["mid"], job["start"],
                     job["end"], len(frames), job["size"] / 1e6, time.time() - t0)
        except GifError as e:
            job.update(status="cancelled" if job["cancel"] else "error", phase=str(e), error=str(e))
        except Exception as e:  # noqa: BLE001
            job.update(status="error", phase="失敗", error=f"{type(e).__name__}: {e}")
            errlog.record_bg("影片轉 GIF", e)
        finally:
            frames.close()
    if job["status"] == "after":
        _after(job)


def _after(job: dict):
    """做好後自動複製／存：失敗不算 GIF 失敗（GIF 還在，可以手動再按），錯誤放 after_error。"""
    try:
        if job["after"] == "share":
            r = share(job["id"])
            job["shared"] = r
            if not r.get("ok"):
                job["after_error"] = "這台電腦不是 Windows，不能直接複製：請用「下載」"
        else:
            save(job["id"])
    except (GifError, RuntimeError) as e:
        job["after_error"] = str(e)
    except Exception as e:  # noqa: BLE001
        job["after_error"] = f"{type(e).__name__}: {e}"
        errlog.record_bg("影片轉 GIF（做好後）", e)
    job.update(status="done", phase="完成")


def _ready(j: dict) -> bool:
    return j["status"] in ("done", "after") and bool(j["file"]) and os.path.isfile(j["file"])


# ---------- 存進圖庫 ----------
def _tsname(s: float) -> str:
    m, sec = divmod(s, 60)
    return f"{int(m)}m{sec:04.1f}s".replace(".", "_") if m else f"{sec:.1f}s".replace(".", "_")


def dest_for(video_path: str, start_s: float, end_s: float) -> Path:
    from . import archive
    vp = Path(video_path)
    d = vp.parent
    if archive.ICLOUD.search(str(vp)) or not os.access(d, os.W_OK):
        s = db.get_settings()
        if (s.get("import_dir") or "").strip():
            d = Path(s["import_dir"]) / "GIF"
        else:
            root = next((r for r in s["roots"] if os.path.isdir(r) and not archive.ICLOUD.search(os.path.join(r, ""))), None)
            if not root:
                raise GifError("沒有可以放 GIF 的圖庫資料夾")
            d = Path(root) / "匯入" / "GIF"
    stem = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "_", vp.stem)[:120]
    base = f"{stem}_{_tsname(start_s)}-{_tsname(end_s)}"
    p = d / f"{base}.gif"
    i = 2
    while p.exists():
        p = d / f"{base}_{i}.gif"
        i += 1
    return p


def save(jid: str) -> dict:
    """把做好的 GIF 放進圖庫，建紀錄、帶上影片確認過的標籤、交給掃描標記。同一個 GIF 存第二次 → 回傳第一次的。"""
    j = get(jid)
    if not _ready(j):
        raise GifError("GIF 還沒做好")
    if j["saved"]:
        return j["saved"]
    v = _video(j["mid"])
    dst = dest_for(v["path"], j["start"], j["end"])
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(j["file"], dst)
    st = dst.stat()
    p = os.path.normpath(str(dst))
    c = db.conn()
    with db.write_lock:
        c.execute("""INSERT OR IGNORE INTO media(id, path, kind, size, mtime, status, added_at)
                     VALUES(MAX((SELECT IFNULL(MAX(id), 0) FROM media), (SELECT hi FROM media_seq WHERE k=1)) + 1,
                            ?, 'image', ?, ?, 'pending', ?)""", (p, st.st_size, st.st_mtime, time.time()))
        new = c.execute("SELECT id FROM media WHERE path=?", (p,)).fetchone()[0]
        q = ",".join("?" * len(INHERIT_CATS))
        rows = c.execute(f"""SELECT mt.tag_id FROM media_tags mt JOIN tags t ON t.id=mt.tag_id
                             WHERE mt.media_id=? AND mt.state=1 AND t.category IN ({q})""", (j["mid"], *INHERIT_CATS)).fetchall()
        why = f"從影片 #{j['mid']} 轉的 GIF"
        c.executemany("""INSERT INTO media_tags(media_id, tag_id, score, source, state, reason) VALUES(?,?,NULL,'gif',1,?)
                         ON CONFLICT(media_id, tag_id) DO UPDATE SET state=1, source='gif', reason=excluded.reason""",
                      [(new, r[0], why) for r in rows])
        nm = c.execute("SELECT nsfw_manual FROM media WHERE id=?", (j["mid"],)).fetchone()[0]
        if nm is not None:
            c.execute("UPDATE media SET nsfw_manual=? WHERE id=?", (nm, new))
        c.execute("UPDATE media SET edited_at=? WHERE id IN (?, ?)", (time.time(), new, j["mid"]))
        c.commit()
    db.log_op(f"影片轉 GIF：{dst.name}", mids=[j["mid"], new], tids=[r[0] for r in rows])
    from . import pipeline
    pipeline.get_jobs().submit("scan", [p])      # 跟監看到新檔一樣：標記、搜源
    j["saved"] = {"id": new, "path": p, "name": dst.name, "tags": len(rows)}
    return j["saved"]


def share(jid: str) -> dict:
    """複製到剪貼簿（到 QQ／Messenger 按 Ctrl+V），不用先存進圖庫。"""
    from . import share as sh
    j = get(jid)
    if not _ready(j):
        raise GifError("GIF 還沒做好")
    if not sh.supported():
        return {"ok": False, "reason": "unsupported"}
    # 剪貼簿的檔名就是聊天室裡看到的檔名：用好認的名字放一份
    nice = tmp_dir() / dest_for_name(j)
    if not nice.exists():
        shutil.copyfile(j["file"], nice)
    sh.set_clipboard([str(nice)], None)
    return {"ok": True, "size": j["size"]}


def dest_for_name(j: dict) -> str:
    stem = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "_", Path(j["path"]).stem)[:80]
    return f"{stem}_{_tsname(j['start'])}-{_tsname(j['end'])}.gif"
