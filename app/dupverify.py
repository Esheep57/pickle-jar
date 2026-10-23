"""逐對像素驗證：兩張圖是不是「看起來一模一樣」，或其中一張是另一張的局部截圖。

使用者的規則：解析度不同、局部截圖才算重複；改了表情、對話框的字、打碼就不算。
dHash／長寬比／整體特徵只看「大概樣子」，對眨眼、對話框這種小範圍改動完全不敏感（漫畫連續頁、差分會被當成重複），
所以候選對最後都要過這一關：

1. 兩張縮到同樣大小（去掉純色邊框、容許幾個像素的裁切位移，自動對齊）
2. 每個像素容許 2 像素內的位移（重新取樣誤差）：a 的值落在 b 附近 5×5 的最小～最大值之間就算 0
3. 在 9×9 的小窗裡取平均，找「差最多的地方」＝分數（0–255 灰階）

實測（使用者圖庫 400px 縮圖，183 對 dHash 候選）：真正的複本 0–4，重新壓縮的梗圖 10.7；
差分 ≥ 17（改表情 17.0、對話框「咸粽→甜粽」17.5、眨眼 27.7、打碼 29.4）。門檻 SAME_PIXEL = 12 落在中間的空隙。
局部截圖：加黑邊＋縮小 7.7、透明底 vs 白底 10.3。
另外兩道保險：平均顏色差 > 12 不算（灰底 vs 米色底的空白頁）；幾乎純色的圖要 < 1.5 才算。

局部截圖：長寬比不同但整體特徵很像的圖，在大圖上做多尺度樣板比對找到位置，再用同樣的方法驗證那一塊。
"""
from __future__ import annotations

import numpy as np

SAME_PIXEL = 12.0       # 9×9 小窗最大差異 < 12 → 同一張圖
CROP_MIN_SIDE = 0.25    # 局部截圖至少要佔原圖寬、高的 25%（太小的一塊比對不可靠）
SIDE = 400              # 比對用的大小（長邊；＝縮圖大小，實測 256 會把眨眼、對話框的差別糊掉）


def _cv2():
    import cv2
    return cv2


class Img:
    __slots__ = ("gray", "mean")

    def __init__(self, gray: np.ndarray, mean: np.ndarray):
        self.gray, self.mean = gray, mean


def load(path) -> Img | None:
    """縮圖 → 灰階 float32＋平均顏色（灰階看不出「灰色 vs 米色」的空白頁）。"""
    try:
        from PIL import Image
        with Image.open(path) as im:
            im = im.convert("RGBA")
            bg = Image.new("RGBA", im.size, (255, 255, 255, 255))
            bg.alpha_composite(im)
            rgb = bg.convert("RGB")
            return Img(np.asarray(rgb.convert("L"), dtype=np.float32),
                       np.asarray(rgb, dtype=np.float32).reshape(-1, 3).mean(0))
    except Exception:  # noqa
        return None


COLOR_DIFF = 12.0       # 平均顏色差太多 → 不是同一張（例如灰底 vs 米色底的空白頁）
FLAT_STD = 6.0          # 幾乎純色的圖：細節太少，要幾乎完全一樣才算


def same(a: Img, b: Img) -> tuple[bool, float]:
    """兩張長寬比差不多的圖是不是同一張。回傳 (是否, 分數)。"""
    s = pixel_score(a.gray, b.gray)
    if float(np.abs(a.mean - b.mean).max()) > COLOR_DIFF:
        return False, max(s, 99.0)
    flat = min(float(a.gray.std()), float(b.gray.std())) < FLAT_STD
    return s < (1.5 if flat else SAME_PIXEL), s


def trim(g: np.ndarray, tol: float = 10.0) -> np.ndarray:
    """去掉四周純色的邊（黑邊、白邊）：不同來源的同一張圖常常只差在邊框。"""
    if g.size == 0:
        return g
    h, w = g.shape
    t, b, l, r = 0, h, 0, w
    while t < b - 8 and np.ptp(g[t, l:r]) < tol and abs(float(g[t, l:r].mean()) - float(g[0, 0])) < tol:
        t += 1
    while b > t + 8 and np.ptp(g[b - 1, l:r]) < tol and abs(float(g[b - 1, l:r].mean()) - float(g[h - 1, w - 1])) < tol:
        b -= 1
    while l < r - 8 and np.ptp(g[t:b, l]) < tol and abs(float(g[t:b, l].mean()) - float(g[0, 0])) < tol:
        l += 1
    while r > l + 8 and np.ptp(g[t:b, r - 1]) < tol and abs(float(g[t:b, r - 1].mean()) - float(g[h - 1, w - 1])) < tol:
        r -= 1
    out = g[t:b, l:r]
    return out if out.shape[0] >= 16 and out.shape[1] >= 16 else g


def _resize(g: np.ndarray, w: int, h: int) -> np.ndarray:
    cv2 = _cv2()
    inter = cv2.INTER_AREA if w < g.shape[1] else cv2.INTER_LINEAR
    return cv2.resize(g, (w, h), interpolation=inter)


def _tolerant_diff(a: np.ndarray, b: np.ndarray, r: int = 2) -> np.ndarray:
    """每個像素：a 落在 b 附近 (2r+1)² 的最小～最大值之間就算 0，否則算差多少；兩個方向取大的。"""
    cv2 = _cv2()
    k = np.ones((2 * r + 1, 2 * r + 1), np.uint8)
    bmax, bmin = cv2.dilate(b, k), cv2.erode(b, k)
    amax, amin = cv2.dilate(a, k), cv2.erode(a, k)
    d1 = np.maximum(np.maximum(a - bmax, bmin - a), 0)
    d2 = np.maximum(np.maximum(b - amax, amin - b), 0)
    return np.maximum(d1, d2)


def _window_max(d: np.ndarray, win: int = 9) -> float:
    cv2 = _cv2()
    m = cv2.blur(d, (win, win), borderType=cv2.BORDER_REFLECT)
    e = win // 2 + 2          # 邊緣幾個像素不算（對齊、縮放在邊上最容易出錯）
    if m.shape[0] > 2 * e + 4 and m.shape[1] > 2 * e + 4:
        m = m[e:-e, e:-e]
    return float(m.max())


def _align(a: np.ndarray, b: np.ndarray, s: int = 4) -> tuple[np.ndarray, np.ndarray]:
    """b 相對 a 在 ±s 像素內找平均差最小的位移，兩張都裁成重疊的部分。"""
    h, w = a.shape
    best, bd = None, None
    for dy in range(-s, s + 1):
        for dx in range(-s, s + 1):
            aa = a[max(0, dy):h + min(0, dy), max(0, dx):w + min(0, dx)]
            bb = b[max(0, -dy):h + min(0, -dy), max(0, -dx):w + min(0, -dx)]
            v = float(np.abs(aa[::2, ::2] - bb[::2, ::2]).mean())
            if bd is None or v < bd:
                bd, best = v, (aa, bb)
    return best


def _blur(g: np.ndarray) -> np.ndarray:
    return _cv2().GaussianBlur(g, (3, 3), 0.8)


def pixel_score(ga: np.ndarray, gb: np.ndarray) -> float:
    """兩張長寬比差不多的圖：小窗最大差異（越小越像；< SAME_PIXEL 算同一張）。"""
    ga, gb = trim(ga), trim(gb)
    h, w = ga.shape
    sc = SIDE / max(h, w)
    W, H = max(16, round(w * sc)), max(16, round(h * sc))
    a, b = _blur(_resize(ga, W, H)), _blur(_resize(gb, W, H))
    a, b = _align(a, b)
    return _window_max(_tolerant_diff(a, b))


def crop_of(big: Img, small: Img) -> tuple[float, tuple] | None:
    """small 是 big 的局部截圖 → (分數, 位置)；不是 → None。"""
    if min(float(big.gray.std()), float(small.gray.std())) < FLAT_STD * 2:
        return None
    r = crop_score(big.gray, small.gray)
    return r if r and r[0] < SAME_PIXEL else None


def crop_score(big: np.ndarray, small: np.ndarray) -> tuple[float, tuple] | None:
    """small 是不是 big 的一部分：多尺度樣板比對找位置，再用 pixel_score 同樣的方法驗證那一塊。
    回傳 (分數, (x, y, w, h) 在 big 裡的比例位置)；找不到合理位置回傳 None。"""
    cv2 = _cv2()
    big, small = trim(big), trim(small)
    H, W = big.shape
    sc = 384 / max(H, W)
    B = _blur(_resize(big, max(16, round(W * sc)), max(16, round(H * sc))))
    H, W = B.shape
    sh, sw = small.shape
    kmax = min(W / sw, H / sh)                  # small 縮到剛好塞進 big
    kmin = kmax * CROP_MIN_SIDE / max(sw * kmax / W, sh * kmax / H)
    kmin = max(kmin, max(W * CROP_MIN_SIDE / sw, H * CROP_MIN_SIDE / sh), 24 / min(sw, sh))
    if kmin > kmax:
        return None
    best = None
    for k in np.geomspace(kmax, kmin, 28):
        tw, th = int(round(sw * k)), int(round(sh * k))
        if tw < 16 or th < 16 or tw > W or th > H:
            continue
        T = _blur(_resize(small, tw, th))
        if float(T.std()) < 4:
            return None                          # 幾乎純色：比不出來
        res = cv2.matchTemplate(B, T, cv2.TM_CCOEFF_NORMED)
        _, mv, _, loc = cv2.minMaxLoc(res)
        if best is None or mv > best[0]:
            best = (mv, loc, tw, th)
    if best is None or best[0] < 0.85:
        return None
    _, (x, y), tw, th = best
    # 驗證：在原本解析度把那一塊切出來，跟 small 比
    oh, ow = big.shape
    fx, fy = ow / W, oh / H
    region = big[int(y * fy):int((y + th) * fy), int(x * fx):int((x + tw) * fx)]
    if region.shape[0] < 16 or region.shape[1] < 16:
        return None
    s = pixel_score(region, small)
    return s, (x / W, y / H, tw / W, th / H)
