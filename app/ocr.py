"""輕量文字辨識（PP-OCR 的 ONNX 模型），只用來讀圖上的簽名：@帳號、網址。

不另外裝 OCR 套件（rapidocr 會多裝一份 opencv、和現有的衝突），直接用已經有的 onnxruntime + opencv：
偵測（DBNet）找出文字框 → 辨識（CRNN + CTC）讀出文字。
模型第一次用到時從 PyPI 上 rapidocr 的安裝檔取出（固定版本、約 27 MB，下載一次），放在 models/ocr/。
"""
from __future__ import annotations

import io
import logging
import threading
import zipfile
from pathlib import Path

import numpy as np

log = logging.getLogger("ocr")
ROOT = Path(__file__).resolve().parent.parent / "models" / "ocr"
WHEEL = ("https://files.pythonhosted.org/packages/py3/r/rapidocr/rapidocr-3.9.2-py3-none-any.whl")
FILES = {"det.onnx": "rapidocr/models/PP-OCRv6_det_small.onnx", "rec.onnx": "rapidocr/models/PP-OCRv6_rec_small.onnx"}

_lock = threading.Lock()
_eng = None


def _download():
    from .httpclient import client
    ROOT.mkdir(parents=True, exist_ok=True)
    log.info("下載文字辨識模型（約 27 MB，只需一次）")
    r = client().get(WHEEL, timeout=300, follow_redirects=True)
    r.raise_for_status()
    z = zipfile.ZipFile(io.BytesIO(r.content))
    for dst, src in FILES.items():
        tmp = ROOT / (dst + ".part")
        tmp.write_bytes(z.read(src))
        tmp.replace(ROOT / dst)


class _Engine:
    def __init__(self):
        import onnxruntime as ort
        if not all((ROOT / f).exists() for f in FILES):
            _download()
        so = ort.SessionOptions()
        so.intra_op_num_threads = 4
        self.det = ort.InferenceSession(str(ROOT / "det.onnx"), so, providers=["CPUExecutionProvider"])
        self.rec = ort.InferenceSession(str(ROOT / "rec.onnx"), so, providers=["CPUExecutionProvider"])
        chars = self.rec.get_modelmeta().custom_metadata_map["character"].split("\n")
        self.chars = ["\0"] + chars + [" "]      # 0 = CTC 空白

    # ---- 偵測 ----
    def boxes(self, rgb: np.ndarray, max_side: int = 1600) -> list[np.ndarray]:
        import cv2
        h, w = rgb.shape[:2]
        s = min(1.0, max_side / max(h, w))
        nh, nw = max(32, int(round(h * s / 32)) * 32), max(32, int(round(w * s / 32)) * 32)
        x = cv2.resize(rgb, (nw, nh)).astype(np.float32) / 255.0
        x = ((x - 0.5) / 0.5).transpose(2, 0, 1)[None]
        prob = self.det.run(None, {"x": x})[0][0, 0]
        mask = (prob > 0.3).astype(np.uint8)
        cnts, _ = cv2.findContours(mask, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
        out = []
        for c in cnts[:1000]:
            if cv2.contourArea(c) < 6:
                continue
            (cx, cy), (bw, bh), ang = cv2.minAreaRect(c)
            if min(bw, bh) < 3:
                continue
            x0, y0, cw, ch = cv2.boundingRect(c)
            m = np.zeros((ch, cw), np.uint8)
            cv2.drawContours(m, [c - [x0, y0]], -1, 1, -1)
            if float(prob[y0:y0 + ch, x0:x0 + cw][m > 0].mean()) < 0.5:
                continue
            d = bw * bh * 1.6 / (2 * (bw + bh))      # unclip：把框往外擴（DBNet 的框比字小）
            pts = cv2.boxPoints(((cx, cy), (bw + 2 * d, bh + 2 * d), ang))
            pts[:, 0] *= w / nw
            pts[:, 1] *= h / nh
            out.append(pts)
        return out

    # ---- 辨識 ----
    @staticmethod
    def _crop(rgb, pts):
        import cv2
        # 左上、右上、右下、左下
        s = pts.sum(1)
        d = np.diff(pts, axis=1).ravel()
        p = np.array([pts[np.argmin(s)], pts[np.argmin(d)], pts[np.argmax(s)], pts[np.argmax(d)]], np.float32)
        wd = int(max(np.linalg.norm(p[0] - p[1]), np.linalg.norm(p[2] - p[3])))
        ht = int(max(np.linalg.norm(p[0] - p[3]), np.linalg.norm(p[1] - p[2])))
        if wd < 2 or ht < 2:
            return None
        M = cv2.getPerspectiveTransform(p, np.array([[0, 0], [wd, 0], [wd, ht], [0, ht]], np.float32))
        im = cv2.warpPerspective(rgb, M, (wd, ht), borderMode=cv2.BORDER_REPLICATE)
        if ht / wd >= 1.5:
            im = np.rot90(im)          # 直書
        return im

    def read(self, rgb: np.ndarray, boxes) -> list[tuple[str, float]]:
        import cv2
        crops = [c for c in (self._crop(rgb, b) for b in boxes) if c is not None]
        out = []
        for i in range(0, len(crops), 8):
            ch = crops[i:i + 8]
            ws = [min(1600, max(16, int(np.ceil(48 * c.shape[1] / c.shape[0])))) for c in ch]
            W = max(ws)
            X = np.zeros((len(ch), 3, 48, W), np.float32)
            for j, (c, w) in enumerate(zip(ch, ws)):
                im = cv2.resize(c, (w, 48)).astype(np.float32) / 255.0
                X[j, :, :, :w] = ((im - 0.5) / 0.5).transpose(2, 0, 1)
            P = self.rec.run(None, {"x": X})[0]
            for pr in P:
                idx, conf = pr.argmax(1), pr.max(1)
                txt, cs, prev = [], [], -1
                for k, cf in zip(idx, conf):
                    if k != prev and k != 0 and k < len(self.chars):
                        txt.append(self.chars[k])
                        cs.append(cf)
                    prev = k
                if txt:
                    out.append(("".join(txt), float(np.mean(cs))))
        return out


def engine() -> _Engine:
    global _eng
    with _lock:
        if _eng is None:
            _eng = _Engine()
        return _eng


def text(path_or_img, max_side: int = 1600) -> list[tuple[str, float]]:
    """圖上的文字（每個文字框一行，附信心分數）。"""
    from . import media
    img = path_or_img if not isinstance(path_or_img, (str, Path)) else media.load_image(Path(path_or_img))
    rgb = np.asarray(media.to_rgb(img))
    e = engine()
    return e.read(rgb, e.boxes(rgb, max_side))
