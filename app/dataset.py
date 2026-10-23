"""匯出訓練集（b84）：選的圖 → 一個資料夾，每張圖旁邊一個同名 .txt 標註（LoRA／kohya、OneTrainer 這類訓練程式讀的格式）。

標註的內容是你整理過的資料，不是重跑模型：
- 角色、作品、創作者：只用**確認過**的（state=1）。角色有所屬作品、圖上沒標作品時補上作品（Danbooru 的習慣）。
- 一般描述標籤：WD Tagger／Danbooru 的（你否決過的不算），模型的要分數 ≥ 門檻（預設 0.35）。
- 順序：觸發詞 → 角色 → 作品 → 創作者 → 一般（分數高的在前）。底線換空格、括號加反斜線都可以選。
- 圖片照原檔複製；HEIC／AVIF／TIFF 這類訓練程式多半讀不了的轉成 PNG。影片不匯出。檔名是圖片編號（重匯出會蓋掉同一張）。
- 不能匯出到圖庫資料夾裡面（會被當成新圖再掃一次）。
"""
from __future__ import annotations

import logging
import os
import shutil
import threading
import time
import uuid
from pathlib import Path

from . import db

log = logging.getLogger("dataset")
CONVERT = {".heic", ".heif", ".avif", ".tif", ".tiff", ".jfif", ".bmp"}
KEEP = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
MAX = 20000
CATS = ("character", "copyright", "artist", "general", "custom")
JOBS: dict[str, dict] = {}
_lock = threading.Lock()


class DatasetError(Exception):
    pass


def defaults() -> dict:
    return {"cats": ["character", "copyright", "artist", "general"], "trigger": "", "spaces": True, "escape": True,
            "general_min": 0.35, "names": "raw", "skip_nsfw": False}


def _opts(o: dict | None) -> dict:
    d = defaults()
    for k, v in (o or {}).items():
        if k in d and v is not None:
            d[k] = v
    d["cats"] = [c for c in d["cats"] if c in CATS]
    d["general_min"] = min(1.0, max(0.0, float(d["general_min"])))
    d["trigger"] = str(d["trigger"] or "").strip()[:200]
    d["names"] = "display" if d["names"] == "display" else "raw"
    return d


def _fmt(name: str, o: dict) -> str:
    t = name.replace("_", " ") if o["spaces"] and len(name) > 3 else name     # ^_^、o_o 這種顏文字保留底線
    if o["escape"]:
        t = t.replace("(", r"\(").replace(")", r"\)")
    return t


def caption(mid: int, o: dict, _disp: dict | None = None) -> str:
    c = db.conn()
    rows = c.execute("""SELECT t.id, t.name, t.category, mt.state, mt.score, mt.source FROM media_tags mt JOIN tags t ON t.id=mt.tag_id
                        WHERE mt.media_id=? AND mt.state>=0""", (mid,)).fetchall()
    by = {k: [] for k in CATS}
    for r in rows:
        cat = r["category"]
        if cat not in o["cats"]:
            continue
        if cat in ("character", "copyright", "artist", "custom"):
            if r["state"] == 1:
                by[cat].append((1.0, r["name"]))
        elif cat == "general":
            sc = r["score"]
            if r["state"] == 1 or r["source"] != "model" or sc is None or sc >= o["general_min"]:
                by[cat].append((sc if sc is not None else 1.0, r["name"]))
    if "copyright" in o["cats"] and by["character"]:          # 角色的作品（圖上沒標作品時補上）
        have = {n for _, n in by["copyright"]}
        ph = ",".join("?" * len(by["character"]))
        for (cp,) in c.execute(f"""SELECT DISTINCT ti.copyright FROM tags t JOIN tag_info ti ON ti.tag_id=t.id
                                  WHERE t.name IN ({ph}) AND ti.copyright IS NOT NULL AND ti.copyright!=''""", [n for _, n in by["character"]]):
            if cp not in have:
                by["copyright"].append((0.9, cp))
                have.add(cp)
    out = [o["trigger"]] if o["trigger"] else []
    for cat in ("character", "copyright", "artist", "custom", "general"):
        items = sorted(by[cat], key=lambda x: (-x[0], x[1]))
        for _, n in items:
            shown = (_disp or {}).get(n) or n if o["names"] == "display" and cat != "general" else n
            t = _fmt(shown, o)
            if t not in out:
                out.append(t)
    return ", ".join(out)


def _display_names(mids: list[int]) -> dict:
    """中文顯示名稱（names: display 時用；一般標籤照原名）。"""
    from . import names
    import json
    c = db.conn()
    s = db.get_settings()
    out = {}
    ph = ",".join("?" * len(mids))
    for r in c.execute(f"""SELECT DISTINCT t.name, t.category, ti.other_names, ti.display_name FROM media_tags mt JOIN tags t ON t.id=mt.tag_id
            LEFT JOIN tag_info ti ON ti.tag_id=t.id WHERE mt.media_id IN ({ph}) AND mt.state=1 AND t.category IN ('character','copyright','artist')""", mids):
        on = json.loads(r["other_names"] or "[]")
        out[r["name"]] = names.series_display(r["name"], on, r["display_name"], s["display_lang"]) if r["category"] == "copyright" \
            else names.display(r["name"], on, r["display_name"], s["display_lang"])
    return out


def check_dest(dest: str) -> Path:
    if not dest or not os.path.isabs(dest):
        raise DatasetError("請選一個資料夾（完整路徑）")
    p = Path(os.path.normpath(dest))
    s = db.get_settings()
    inside = [r for r in [*s.get("roots", []), s.get("import_dir") or "", str(db.DATA_DIR)] if r]
    for r in inside:
        rp = os.path.normcase(os.path.normpath(r))
        dp = os.path.normcase(str(p))
        if dp == rp or dp.startswith(rp.rstrip("\\/") + os.sep):
            raise DatasetError(f"不能匯出到圖庫資料夾裡面（{r}）：會被當成新圖再掃一次。請選圖庫以外的資料夾")
    return p


def _rows(ids: list[int], o: dict):
    c = db.conn()
    out, skipped = [], {"video": 0, "missing": 0, "nsfw": 0}
    nsfw = db.nsfw_expr("m")
    for i in range(0, len(ids), 500):
        ch = ids[i:i + 500]
        for r in c.execute(f"SELECT m.id, m.path, m.kind, m.status, {nsfw} AS nsfw FROM media m WHERE m.id IN ({','.join('?' * len(ch))})", ch):
            if r["kind"] != "image":
                skipped["video"] += 1
            elif r["status"] in ("trashed", "missing") or not os.path.isfile(r["path"]):
                skipped["missing"] += 1
            elif o["skip_nsfw"] and r["nsfw"]:
                skipped["nsfw"] += 1
            else:
                out.append((r["id"], r["path"]))
    order = {m: k for k, m in enumerate(ids)}
    out.sort(key=lambda x: order.get(x[0], 0))
    return out, skipped


def preview(ids: list[int], opts: dict | None) -> dict:
    o = _opts(opts)
    rows, skipped = _rows(ids[:MAX], o)
    disp = _display_names([m for m, _ in rows[:5]]) if o["names"] == "display" and rows else None
    samples = [{"id": m, "caption": caption(m, o, disp)} for m, _ in rows[:3]]
    return {"n": len(rows), "skipped": skipped, "samples": samples, "opts": o, "too_many": len(ids) > MAX}


def start(ids: list[int], dest: str, opts: dict | None) -> dict:
    o = _opts(opts)
    d = check_dest(dest)
    rows, skipped = _rows(ids[:MAX], o)
    if not rows:
        raise DatasetError("沒有可以匯出的圖片（影片、找不到檔案的不匯出）")
    jid = uuid.uuid4().hex[:10]
    j = {"id": jid, "dest": str(d), "total": len(rows), "done": 0, "converted": 0, "state": "running", "error": None,
         "skipped": skipped, "started": time.time(), "stop": False}
    with _lock:
        JOBS[jid] = j
        while len(JOBS) > 20:
            JOBS.pop(next(iter(JOBS)))
    from . import tasks
    tasks.spawn("dataset", f"匯出訓練集（{len(rows):,} 張）", _run, j, rows, o, probe=tasks.probe_of(j), cancel=lambda: cancel(jid), heavy=True)
    return {k: v for k, v in j.items() if k != "stop"}


def _run(j: dict, rows, o: dict):
    try:
        d = Path(j["dest"])
        d.mkdir(parents=True, exist_ok=True)
        disp = None
        for k in range(0, len(rows), 200):
            if j["stop"]:
                j["state"] = "stopped"
                return
            chunk = rows[k:k + 200]
            if o["names"] == "display":
                disp = _display_names([m for m, _ in chunk])
            for mid, path in chunk:
                if j["stop"]:
                    break
                src = Path(path)
                ext = src.suffix.lower()
                try:
                    if ext in KEEP:
                        out = d / f"{mid}{'.jpg' if ext == '.jpeg' else ext}"
                        shutil.copy2(src, out)
                    else:
                        out = d / f"{mid}.png"
                        from PIL import Image, ImageOps
                        from . import media  # noqa: F401  註冊 HEIC／AVIF 讀取
                        with Image.open(src) as im:
                            im = ImageOps.exif_transpose(im)
                            im.convert("RGBA" if im.mode in ("RGBA", "LA", "PA", "P") else "RGB").save(out, "PNG")
                        j["converted"] += 1
                    out.with_suffix(".txt").write_text(caption(mid, o, disp), encoding="utf-8")
                except Exception as e:  # noqa  一張壞掉只影響一張
                    log.warning("匯出訓練集：%s 失敗：%s", path, e)
                    j.setdefault("failed", 0)
                    j["failed"] += 1
                j["done"] += 1
        j["state"] = "stopped" if j["stop"] else "done"
        db.log_op(f"匯出訓練集 {j['done']} 張 → {j['dest']}", [m for m, _ in rows[:50]], keys="dataset")
    except Exception as e:  # noqa
        log.warning("匯出訓練集失敗：%s", e)
        j["state"] = "error"
        j["error"] = str(e)[:300]


def status(jid: str) -> dict | None:
    j = JOBS.get(jid)
    return {k: v for k, v in j.items() if k != "stop"} if j else None


def cancel(jid: str) -> bool:
    j = JOBS.get(jid)
    if j and j["state"] == "running":
        j["stop"] = True
        return True
    return False
