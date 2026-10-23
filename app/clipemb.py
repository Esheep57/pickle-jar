"""CLIP 圖像特徵（b78）：給媒材分類用的第二種特徵。

WD 是拿動漫插畫訓練的，分得出插畫和照片，但 3D 算圖、公仔照片、cosplay 照片分不太開。CLIP 是拿一般網路圖片訓練的。
2026-10-15 用 450 張起始標註 5 折驗證（縮圖當輸入，跟這裡一樣）：
  只用 WD 80.6%；WD＋CLIP ViT-B/32 84.6%（隨機樣本 84.4% → 88.2%）；B/32 量化版 83.1%、B/16 82.7%（反而比較差）。

- 模型：Xenova/clip-vit-base-patch32 的 vision_model.onnx（約 350 MB，第一次下載，放 models/）
- 輸入：程式已經有的縮圖（data/thumbs，影片也有），不用重讀原檔；中心裁成 224×224
- 跟 WD 一樣用 GPU（CUDA／DirectML，WD 在同一個程式裡用 GPU 一直很穩；崩潰過的是大很多的 PixAI）。
  保險：第一次用 GPU 時先記「試用中」，算完一塊才改成「沒問題」。萬一中途整個程式崩潰，下次開程式看到還是「試用中」
  就自動改用 CPU（設定 _clip_gpu）。Python 層的錯誤（載入失敗、推論出錯）當場改回 CPU。
- 存在 media_clip（fp16，512 維）；fp 跟 media.fp 不同＝檔案換過（換高清），重算
"""
from __future__ import annotations

import logging
import os
import time
from pathlib import Path

import numpy as np

from . import db

log = logging.getLogger("clipemb")

REPO = "Xenova/clip-vit-base-patch32"
FILE = "onnx/vision_model.onnx"
CHUNK = 400
BATCH = 16
MEAN = np.array([0.48145466, 0.4578275, 0.40821073], np.float32)
STD = np.array([0.26862954, 0.26130258, 0.27577711], np.float32)

_sess = None
STATUS: dict = {}
_bad: set[int] = set()      # 這次開程式讀不到縮圖的：不要一直重排


def ensure_schema(c):
    c.execute("CREATE TABLE IF NOT EXISTS media_clip(media_id INTEGER PRIMARY KEY, fp TEXT, emb BLOB NOT NULL, at REAL)")


def enabled() -> bool:
    if os.environ.get("LIB_MEDIA_CLIP") == "0":     # 測試：不下載 350 MB 的模型
        return False
    return bool(db.get_settings().get("media_clip", True))


def model_path() -> Path:
    from .tagger import MODELS_DIR
    return MODELS_DIR / REPO.replace("/", "__") / "vision_model.onnx"


def _download(progress=None) -> Path:
    p = model_path()
    if p.exists():
        return p
    import httpx
    p.parent.mkdir(parents=True, exist_ok=True)
    base = (db.get_settings().get("hf_endpoint") or os.environ.get("HF_ENDPOINT") or "https://huggingface.co").rstrip("/")
    url = f"{base}/{REPO}/resolve/main/{FILE}"
    tmp = p.with_suffix(".part")
    log.info("下載 %s", url)
    with httpx.stream("GET", url, follow_redirects=True, timeout=60) as r:
        r.raise_for_status()
        total = int(r.headers.get("content-length") or 0)
        got = 0
        with open(tmp, "wb") as out:
            for chunk in r.iter_bytes(1 << 20):
                out.write(chunk)
                got += len(chunk)
                if progress:
                    progress(got, total)
    tmp.replace(p)
    return p


def _gpu_state() -> str:
    return db.get_settings().get("_clip_gpu") or ""


def _set_gpu_state(v: str):
    db.update_settings({"_clip_gpu": v})


def check_crash():
    """啟動時：上次用 GPU 算到一半程式就沒了（還是「試用中」）→ 這台電腦改用 CPU。"""
    if _gpu_state() == "trying":
        _set_gpu_state("crashed")
        log.warning("CLIP 上次用 GPU 時程式中斷，改用 CPU")


def _make(path: Path, gpu: bool):
    import onnxruntime as ort
    from . import tagger
    so = ort.SessionOptions()
    so.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
    provs = tagger._providers() if gpu else ["CPUExecutionProvider"]
    if provs and provs[0] == "DmlExecutionProvider":   # DirectML 需求（跟 WD 一樣）
        so.enable_mem_pattern = False
        so.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
    return ort.InferenceSession(str(path), so, providers=provs)


def session(progress=None):
    global _sess
    if _sess is None:
        path = _download(progress)
        from . import tagger
        provs = tagger._providers()
        want_gpu = bool(provs) and provs[0] in ("CUDAExecutionProvider", "DmlExecutionProvider") and _gpu_state() not in ("crashed", "failed")
        if want_gpu:
            try:
                if _gpu_state() != "ok":
                    _set_gpu_state("trying")
                s = _make(path, True)
                s.run(None, {"pixel_values": np.zeros((BATCH, 3, 224, 224), np.float32)})   # 試跑一批
                _sess = s
            except Exception as e:  # noqa
                log.warning("CLIP 用 GPU 失敗，改用 CPU：%s", e)
                _set_gpu_state("failed")
        if _sess is None:
            _sess = _make(path, False)
        STATUS["provider"] = _sess.get_providers()[0]
    return _sess


def preprocess(img) -> np.ndarray:
    from PIL import Image
    img = img.convert("RGB")
    w, h = img.size
    s = 224 / min(w, h)
    img = img.resize((max(224, round(w * s)), max(224, round(h * s))), Image.BICUBIC)
    w, h = img.size
    l, t = (w - 224) // 2, (h - 224) // 2
    a = np.asarray(img.crop((l, t, l + 224, t + 224)), np.float32) / 255.0
    return ((a - MEAN) / STD).transpose(2, 0, 1)


def embed(imgs: list) -> np.ndarray:
    global _sess
    x = np.stack([preprocess(i) for i in imgs]).astype(np.float32)
    n = len(x)
    if n < BATCH:     # GPU 上固定批次大小比較穩（不用每次重編譯）
        x = np.concatenate([x, np.zeros((BATCH - n, 3, 224, 224), np.float32)])
    s = session()
    try:
        out = s.run(None, {"pixel_values": x})[0][:n]
    except Exception as e:  # noqa
        if s.get_providers()[0] == "CPUExecutionProvider":
            raise
        log.warning("CLIP GPU 推論出錯，改用 CPU：%s", e)
        _set_gpu_state("failed")
        _sess = None
        out = session().run(None, {"pixel_values": x})[0][:n]
    return out / np.maximum(np.linalg.norm(out, axis=1, keepdims=True), 1e-6)


def pending_ids(limit: int | None = None) -> list[int]:
    q = """SELECT m.id FROM media m LEFT JOIN media_clip k ON k.media_id=m.id
           WHERE m.status='done' AND m.trashed_at IS NULL AND (k.media_id IS NULL OR COALESCE(k.fp,'')<>COALESCE(m.fp,''))
           ORDER BY m.id DESC"""
    if limit:
        q += f" LIMIT {int(limit)}"
    return [r[0] for r in db.conn().execute(q)]


def stats() -> dict:
    c = db.conn()
    done = c.execute("SELECT COUNT(*) FROM media_clip k JOIN media m ON m.id=k.media_id WHERE m.status='done'").fetchone()[0]
    return {"done": done, "pending": len(pending_ids()), "enabled": enabled(), "model": model_path().exists(),
            "gpu": _gpu_state() or None, **STATUS}


def run(jobs, ids: list[int] | None = None):
    """背景工作（本機佇列）：做一塊，還有就再排一次自己（掃描新圖插得進來）。做完一塊就讓媒材分類重算這些圖。"""
    if not enabled():
        return
    from . import media
    explicit = ids is not None
    todo = [int(i) for i in ids] if explicit else [i for i in pending_ids(CHUNK + len(_bad)) if i not in _bad][:CHUNK]
    if not todo:
        return
    left = len(todo) if explicit else len(pending_ids())

    def dl(got, total):
        jobs.state.update(phase=f"媒材 CLIP：下載模型（{got >> 20} / {total >> 20} MB）")
    jobs.state.update(phase="媒材 CLIP：準備模型")
    try:
        session(dl)
    except Exception as e:  # noqa
        STATUS["error"] = f"{type(e).__name__}: {e}"
        log.warning("CLIP 模型載入失敗：%s", e)
        return
    STATUS.pop("error", None)
    from PIL import Image
    c = db.conn()
    fps = dict(c.execute(f"SELECT id, fp FROM media WHERE id IN ({','.join('?' * len(todo))})", todo).fetchall())
    jobs.state.update(phase=f"媒材 CLIP 特徵（還有 {left:,} 張）")
    t0 = time.time()
    done_ids: list[int] = []
    for k in range(0, len(todo), BATCH):
        if not jobs.checkpoint():
            break
        batch, imgs = [], []
        for mid in todo[k:k + BATCH]:
            p = media.thumb_path(mid)
            try:
                with Image.open(p) as im:
                    imgs.append(im.convert("RGB"))
                batch.append(mid)
            except Exception:  # noqa  沒有縮圖（還沒做、讀不到）：這次開程式先跳過
                _bad.add(mid)
                continue
        if batch:
            E = embed(imgs).astype(np.float16)
            with db.write_lock:
                c.executemany("INSERT OR REPLACE INTO media_clip(media_id, fp, emb, at) VALUES(?,?,?,?)",
                              [(m, fps.get(m), e.tobytes(), time.time()) for m, e in zip(batch, E)])
                c.commit()
            done_ids += batch
        jobs.progress(min(k + BATCH, len(todo)), len(todo), t0)
    if done_ids and _gpu_state() == "trying" and _sess is not None and _sess.get_providers()[0] != "CPUExecutionProvider":
        _set_gpu_state("ok")       # GPU 算完一整塊都沒事
    if done_ids:
        from . import mediaclf
        mediaclf.run(only=done_ids)
    more = [i for i in pending_ids(len(_bad) + 1) if i not in _bad]
    if not explicit and not jobs.stop_flag.is_set() and more:
        jobs.submit("clip")
    elif not explicit and not more:
        from . import mediaclf
        mediaclf.schedule(1.0)       # 全部做完：用新特徵重新訓練一次
