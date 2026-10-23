"""WD Tagger v3（ONNX）推論，同時取出分類頭前的特徵向量作為比對用 embedding。"""
from __future__ import annotations

import csv
import os
import logging
from pathlib import Path

import numpy as np
from PIL import Image
import threading

_RUN_LOCK = threading.Lock()

log = logging.getLogger("tagger")
MODELS_DIR = Path(os.environ.get("LIB_MODELS_DIR") or Path(__file__).resolve().parent.parent / "models")

RATING_NAMES = ["general", "sensitive", "questionable", "explicit"]


def _download(repo: str, progress=None) -> Path:
    """模型放在 models/<repo 名稱>/ 下；沒有就從 Hugging Face 下載（顯示進度；可設定鏡像站）。
    progress(已下載 bytes, 總 bytes, 檔名)"""
    d = MODELS_DIR / repo.replace("/", "__")
    d.mkdir(parents=True, exist_ok=True)
    need = [f for f in ("selected_tags.csv", "model.onnx") if not (d / f).exists()]
    if not need:
        return d
    import httpx
    from . import db
    base = (db.get_settings().get("hf_endpoint") or os.environ.get("HF_ENDPOINT") or "https://huggingface.co").rstrip("/")
    for f in need:
        url = f"{base}/{repo}/resolve/main/{f}"
        tmp = d / (f + ".part")
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
                        progress(got, total, f)
        tmp.replace(d / f)
    return d


def _with_embedding_output(src: Path, dst: Path) -> bool:
    """在 ONNX 圖上把分類頭（最後的 Gemm/MatMul）的輸入加為額外輸出。"""
    try:
        import onnx
        from onnx import TensorProto, helper
    except ImportError:
        return False
    m = onnx.load(str(src))
    g = m.graph
    producers = {o: n for n in g.node for o in n.output}
    name = g.output[0].name
    for _ in range(12):  # 從輸出往回找
        n = producers.get(name)
        if n is None:
            return False
        if n.op_type in ("Gemm", "MatMul"):
            feat = n.input[0]
            break
        name = n.input[0]
    else:
        return False
    if feat not in {o.name for o in g.output}:
        g.output.append(helper.make_tensor_value_info(feat, TensorProto.FLOAT, None))
    tmp = dst.with_suffix(".part")          # 先寫暫存檔再改名：寫到一半當機不會留下壞掉的模型
    onnx.save(m, str(tmp))
    os.replace(tmp, dst)
    log.info("已加入 embedding 輸出：%s", feat)
    return True


def _providers():
    import onnxruntime as ort
    try:  # onnxruntime-gpu >= 1.21：載入 pip 裝的 CUDA/cuDNN DLL
        ort.preload_dlls()
    except Exception:
        pass
    avail = ort.get_available_providers()
    order = ["CUDAExecutionProvider", "DmlExecutionProvider", "CPUExecutionProvider"]
    return [p for p in order if p in avail]


class Tagger:
    def __init__(self, repo: str, progress=None):
        import onnxruntime as ort
        self.repo = repo
        d = _download(repo, progress)
        tags = list(csv.DictReader(open(d / "selected_tags.csv", encoding="utf-8")))
        self.names = [t["name"] for t in tags]
        cats = np.array([int(t["category"]) for t in tags])
        self.rating_idx = np.where(cats == 9)[0]
        self.general_idx = np.where(cats == 0)[0]
        self.char_idx = np.where(cats == 4)[0]

        emb_path = d / "model_emb.onnx"
        path = d / "model.onnx"
        if emb_path.exists() or _with_embedding_output(path, emb_path):
            path = emb_path
        so = ort.SessionOptions()
        so.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        provs = _providers()
        if provs and provs[0] == "DmlExecutionProvider":  # DirectML 需求
            so.enable_mem_pattern = False
            so.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
        self.sess = ort.InferenceSession(str(path), so, providers=provs)
        self.provider = self.sess.get_providers()[0]
        inp = self.sess.get_inputs()[0]
        self.input_name = inp.name
        self.size = int(inp.shape[1]) if isinstance(inp.shape[1], int) else 448
        outs = self.sess.get_outputs()
        self.out_names = [o.name for o in outs]
        self.has_emb = len(outs) > 1
        log.info("Tagger 就緒：%s, provider=%s, size=%d, embedding=%s",
                 repo, self.provider, self.size, self.has_emb)

    # ---------- 前處理 ----------
    def preprocess(self, img: Image.Image) -> np.ndarray:
        img = img.convert("RGBA") if img.mode in ("P", "LA", "RGBA") else img.convert("RGB")
        if img.mode == "RGBA":
            bg = Image.new("RGBA", img.size, (255, 255, 255, 255))
            bg.alpha_composite(img)
            img = bg.convert("RGB")
        w, h = img.size
        s = max(w, h)
        canvas = Image.new("RGB", (s, s), (255, 255, 255))
        canvas.paste(img, ((s - w) // 2, (s - h) // 2))
        canvas = canvas.resize((self.size, self.size), Image.BICUBIC)
        arr = np.asarray(canvas, dtype=np.float32)[:, :, ::-1]  # RGB -> BGR
        return np.ascontiguousarray(arr)

    # ---------- 推論 ----------
    def run(self, batch: np.ndarray):
        """回傳 (probs[N, T], emb[N, D])。"""
        with _RUN_LOCK:  # DirectML 不支援同一個 session 同時跑兩個推論（例如背景標記 + 診斷的速度測試）
            outs = self.sess.run(self.out_names, {self.input_name: batch})
        probs = outs[0]
        if probs.min() < 0 or probs.max() > 1:  # 保險：某些匯出版沒有 sigmoid
            probs = 1 / (1 + np.exp(-probs))
        if self.has_emb:
            emb = outs[1]
            if emb.ndim > 2:  # [N, tokens, D] -> 平均
                emb = emb.reshape(emb.shape[0], -1, emb.shape[-1]).mean(1)
        else:  # 沒有 embedding 時退回用一般 tag 機率當特徵
            emb = probs[:, self.general_idx[:1536]]
        return probs, emb.astype(np.float32)

    def decode(self, probs: np.ndarray, gen_thr: float, char_thr: float) -> dict:
        r = probs[self.rating_idx]
        ratings = {self.names[i]: float(p) for i, p in zip(self.rating_idx, r)}
        gen = {self.names[i]: float(probs[i]) for i in self.general_idx if probs[i] >= gen_thr}
        chars = {self.names[i]: float(probs[i]) for i in self.char_idx if probs[i] >= char_thr}
        return {"ratings": ratings, "general": gen, "character": chars}


_instance: Tagger | None = None
_lock = __import__("threading").Lock()


def get(repo: str, progress=None) -> Tagger:
    global _instance
    with _lock:
        if _instance is None or _instance.repo != repo:
            _instance = Tagger(repo, progress)
        return _instance


def loaded() -> Tagger | None:
    return _instance


def ensure() -> Tagger | None:
    """需要特徵比對時用：模型還沒載入（例如剛重開、還沒有新圖要標）就載入。載入失敗回傳 None。"""
    if _instance is not None:
        return _instance
    try:
        from . import db
        return get(db.get_settings()["model_repo"])
    except Exception as e:  # noqa
        log.warning("載入模型失敗：%s", e)
        return None


def model_files(repo: str) -> dict:
    d = MODELS_DIR / repo.replace("/", "__")
    return {f: (d / f).stat().st_size if (d / f).exists() else None
            for f in ("model.onnx", "model_emb.onnx", "selected_tags.csv")}


def available_providers() -> list[str]:
    try:
        import onnxruntime as ort
        return ort.get_available_providers()
    except Exception:  # noqa
        return []
