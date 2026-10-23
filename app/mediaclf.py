"""媒材分類（b77）：用你標過的圖學，不再只靠 WD 標籤套規則。

為什麼改：舊的 `candidates.media_type()` 是「WD 標籤分數 ≥ 0.5 就是某媒材」。WD 是拿 Danbooru 插畫訓練的，
`photo_(medium)`、`cosplay`、`3d` 這些在 Danbooru 本來就標得不齊。2026-10-15 隨機抽 300 張人工判斷：
真人照片只認出 28%（40 張有 28 張被當插畫）、3D 0/8、被判成 Cosplay／公仔的八成不是。

做法：每張圖已經有 WD 的 embedding（1024 維），上面接一個多類別 logistic regression（numpy，幾十毫秒），
訓練資料＝你在大圖／M 鍵標的媒材；可以另外放一份起始標註 media_seed.json（{檔案MD5: 媒材}，src='seed'，你標的一律優先）。
你每標一次，3 秒後重新訓練、整庫重算（約 1 秒）。

- 信心 < CONF 的圖媒材照樣寫最可能的那個（其他程式要用），但篩選媒材時不列出來，集中在「未定」給你看。
- 你手動標的（media_src='manual'）永遠不會被蓋掉，重新標記（WD 重跑）也不會。
- 訓練資料太少（< MIN_TOTAL 張）時退回舊規則。某一類少於 MIN_PER_CLASS 張時，那一類先不分出來。
"""
from __future__ import annotations

import json
import logging
import threading
import time
from pathlib import Path

import numpy as np

from . import db

log = logging.getLogger("mediaclf")

CLASSES = ["illustration", "chibi", "comic", "3d", "cosplay", "photo", "figure", "other"]
LABELS = {"illustration": "插畫", "chibi": "Q 版", "comic": "漫畫", "3d": "3D", "cosplay": "Cosplay",
          "photo": "真人照片", "figure": "公仔/娃娃", "other": "截圖／文字"}
CONF = 0.6            # 抽樣驗證：信心 ≥ 0.6 的約 95% 對（隨機樣本），涵蓋約六成；標得越多涵蓋越廣
MIN_TOTAL = 40
MIN_PER_CLASS = 3
L2 = 1e-2
SEED_FILE = Path(__file__).with_name("media_seed.json")

_lock = threading.Lock()
_timer: threading.Timer | None = None
_state: dict = {"version": 0, "running": False, "pending": False, "last": None}


def ensure_schema(c):
    cols = {r[1] for r in c.execute("PRAGMA table_info(media)")}
    if "media_src" not in cols:
        c.execute("ALTER TABLE media ADD COLUMN media_src TEXT")      # manual／model／rule（NULL＝舊規則）
        c.execute("ALTER TABLE media ADD COLUMN media_conf REAL")
    c.execute("""CREATE TABLE IF NOT EXISTS media_label(media_id INTEGER PRIMARY KEY, fp TEXT, label TEXT NOT NULL,
                 src TEXT NOT NULL, at REAL)""")


def load_seed() -> int:
    """起始標註：用內容指紋（MD5）對到圖，換電腦、搬檔都對得上。已經有標的（尤其是你標的）不動。"""
    if not SEED_FILE.exists():
        return 0
    seed = json.loads(SEED_FILE.read_text("utf-8"))
    c = db.conn()
    have = {r[0] for r in c.execute("SELECT media_id FROM media_label")}
    rows = []
    for mid, fp in c.execute("SELECT id, fp FROM media WHERE fp IS NOT NULL"):
        lab = seed.get(fp)
        if lab and mid not in have:
            rows.append((mid, fp, lab, "seed", time.time()))
    if rows:
        with db.write_lock:
            c.executemany("INSERT OR IGNORE INTO media_label(media_id, fp, label, src, at) VALUES(?,?,?,?,?)", rows)
            c.commit()
    return len(rows)


# ---------- 模型 ----------
def _norm(E: np.ndarray) -> np.ndarray:
    return E / np.maximum(np.linalg.norm(E, axis=1, keepdims=True), 1e-6)


def _emb(blob: bytes) -> np.ndarray:
    return np.frombuffer(blob, dtype=np.float16).astype(np.float32)


def fit(X: np.ndarray, y: np.ndarray, k: int, iters: int = 200, lr: float = 0.05) -> tuple[np.ndarray, np.ndarray]:
    """多類別 logistic regression（softmax＋L2，類別平衡權重，Adam 全批次）。X 已正規化。"""
    n, d = X.shape
    cnt = np.bincount(y, minlength=k).astype(np.float32)
    sw = (n / (k * np.maximum(cnt, 1)))[y]                     # 少數類別加權（真人、3D 不會被插畫淹沒）
    sw = sw / sw.sum()
    Y = np.eye(k, dtype=np.float32)[y]
    W = np.zeros((d, k), np.float32); b = np.log(np.maximum(cnt, 1) / n).astype(np.float32)
    mW = np.zeros_like(W); vW = np.zeros_like(W); mb = np.zeros_like(b); vb = np.zeros_like(b)
    Xs = X * 8.0                                                # 正規化後的向量很小，放大一點收斂快
    for t in range(1, iters + 1):
        Z = Xs @ W + b
        Z -= Z.max(1, keepdims=True)
        P = np.exp(Z); P /= P.sum(1, keepdims=True)
        G = (P - Y) * sw[:, None]
        gW = Xs.T @ G + L2 * W; gb = G.sum(0)
        for p, g, m, v in ((W, gW, mW, vW), (b, gb, mb, vb)):
            m *= 0.9; m += 0.1 * g
            v *= 0.999; v += 0.001 * g * g
            p -= lr * (m / (1 - 0.9 ** t)) / (np.sqrt(v / (1 - 0.999 ** t)) + 1e-8)
    return W * 8.0, b


def proba(X: np.ndarray, W: np.ndarray, b: np.ndarray) -> np.ndarray:
    Z = X @ W + b
    Z -= Z.max(1, keepdims=True)
    P = np.exp(Z)
    return P / P.sum(1, keepdims=True)


def _feat(wd: bytes, clip: bytes | None) -> np.ndarray:
    """WD（1024）各自正規化；有 CLIP 時接在後面（512）。"""
    a = _emb(wd); a = a / max(float(np.linalg.norm(a)), 1e-6)
    if clip is None:
        return a
    b = _emb(clip); b = b / max(float(np.linalg.norm(b)), 1e-6)
    return np.concatenate([a, b])


def _training():
    """[(kind, ids, X, y, classes)]：kind 'wd'＝只用 WD（每張都有）；'clip'＝WD＋CLIP（有 CLIP 特徵的標註夠多時）。"""
    c = db.conn()
    rows = c.execute("""SELECT l.media_id, l.label, m.embedding, k.emb FROM media_label l JOIN media m ON m.id=l.media_id
                        LEFT JOIN media_clip k ON k.media_id=m.id
                        WHERE m.embedding IS NOT NULL AND m.status='done'""").fetchall()
    rows = [r for r in rows if r[1] in CLASSES]
    out = []
    for kind, rs in (("wd", rows), ("clip", [r for r in rows if r[3] is not None])):
        if len(rs) < MIN_TOTAL:
            continue
        cnt = {k: sum(r[1] == k for r in rs) for k in CLASSES}
        cls = [k for k in CLASSES if cnt[k] >= MIN_PER_CLASS]
        rs = [r for r in rs if r[1] in cls]
        X = np.stack([_feat(r[2], r[3] if kind == "clip" else None) for r in rs])
        y = np.array([cls.index(r[1]) for r in rs])
        out.append((kind, [r[0] for r in rs], X, y, cls))
    return out


_model: dict = {}      # kind → (W, b, classes)；run() 訓練完放這裡，快速標媒材排順序用


def train() -> dict:
    T = _training()
    m = {}
    for kind, ids, X, y, cls in T:
        W, b = fit(X, y, len(cls))
        m[kind] = (W, b, cls, len(ids))
    _model.clear(); _model.update(m)
    return m


def _stack(blobs: list[bytes]) -> np.ndarray:
    """一次把很多個 fp16 blob 轉成正規化的矩陣（比一張一張轉快很多）。維度看 blob 長度（測試的假模型不是 1024）。"""
    X = np.frombuffer(b"".join(blobs), dtype=np.float16).reshape(len(blobs), len(blobs[0]) // 2).astype(np.float32)
    return X / np.maximum(np.linalg.norm(X, axis=1, keepdims=True), 1e-6)


def predict(rows) -> list[tuple[dict, str]]:
    """rows：(wd blob, clip blob 或 None)。回傳每張 {類別: 機率} 和用了哪個模型。"""
    out: list = [None] * len(rows)
    for kind in ("clip", "wd"):
        if kind not in _model:
            continue
        W, b, cls, _ = _model[kind]
        idx = [i for i, r in enumerate(rows) if out[i] is None and (kind == "wd" or r[1] is not None)]
        for k in range(0, len(idx), 4000):
            ch = idx[k:k + 4000]
            X = _stack([rows[i][0] for i in ch])
            if kind == "clip":
                X = np.hstack([X, _stack([rows[i][1] for i in ch])])
            P = proba(X, W, b)
            for i, p in zip(ch, P):
                out[i] = ({cls[j]: float(p[j]) for j in range(len(cls))}, kind)
    return out


def run(only: list[int] | None = None) -> dict:
    """訓練，然後重算（only＝只算這幾張新圖）。手動標的不動。"""
    if not _lock.acquire(blocking=False):
        _state["pending"] = True
        return {"busy": True}
    _state["running"] = True; _state["pending"] = False
    t0 = time.time()
    try:
        m = train()
        if "wd" not in m:
            return {"skipped": "訓練資料不夠"}
        c = db.conn()
        q = """SELECT m.id, m.embedding, k.emb, m.media_type, m.media_conf, m.media_src FROM media m
               LEFT JOIN media_clip k ON k.media_id=m.id
               WHERE m.embedding IS NOT NULL AND m.status='done' AND COALESCE(m.media_src,'')<>'manual'"""
        args: list = []
        if only:
            q += f" AND m.id IN ({','.join('?' * len(only))})"; args = list(only)
        rows = c.execute(q, args).fetchall()
        preds = predict([(r[1], r[2]) for r in rows])
        upd = []
        used = {"wd": 0, "clip": 0}
        for r, (P, kind) in zip(rows, preds):
            t = max(P, key=P.get); cf = round(P[t], 3)
            used[kind] += 1
            if r[3] != t or r[5] != "model" or r[4] is None or abs(r[4] - cf) > 0.02:
                upd.append((t, cf, r[0]))
        if upd:
            with db.write_lock:
                c.executemany("UPDATE media SET media_type=?, media_conf=?, media_src='model' WHERE id=?", upd)
                c.commit()
            try:        # 未識別的分堆會看媒材（照片、漫畫各一堆）
                from . import piles
                piles.refresh([u[2] for u in upd])
            except Exception as e:  # noqa
                log.info("分堆更新失敗：%s", e)
        res = {"labels": m["wd"][3], "labels_clip": m["clip"][3] if "clip" in m else 0, "classes": m["wd"][2],
               "changed": len(upd), "rows": len(rows), "used": used, "sec": round(time.time() - t0, 2)}
        _state["version"] += 1
        if not only:
            _state["last"] = {**res, "at": time.time()}
        log.info("媒材分類：%s", res)
        return res
    except Exception as e:  # noqa
        log.exception("媒材分類失敗")
        return {"error": str(e)}
    finally:
        _state["running"] = False
        _lock.release()
        if _state["pending"]:
            threading.Thread(target=run, daemon=True, name="mediaclf-rerun").start()


def schedule(delay: float = 3.0):
    """你標了媒材之後過一會兒重新訓練（連續標時只算一次）。"""
    global _timer
    if _timer is not None:
        _timer.cancel()
    _timer = threading.Timer(delay, run)
    _timer.daemon = True
    _timer.start()


# ---------- 你標的 ----------
def set_label(ids: list[int], label: str | None) -> int:
    """label=None＝改回自動。可以撤銷。"""
    if label is not None and label not in CLASSES:
        raise ValueError(label)
    c = db.conn()
    ph = ",".join("?" * len(ids))
    with db.write_lock:
        prev_m = c.execute(f"SELECT id, media_type, media_src, media_conf, fp FROM media WHERE id IN ({ph})", ids).fetchall()
        prev_l = {r[0]: [r[1], r[2], r[3]] for r in c.execute(f"SELECT media_id, label, src, at FROM media_label WHERE media_id IN ({ph})", ids)}
        undo = [[r[0], r[1], r[2], r[3], prev_l.get(r[0])] for r in prev_m]
        if label:
            c.executemany("INSERT OR REPLACE INTO media_label(media_id, fp, label, src, at) VALUES(?,?,?,'user',?)",
                          [(r[0], r[4], label, time.time()) for r in prev_m])
            c.executemany("UPDATE media SET media_type=?, media_src='manual', media_conf=1 WHERE id=?", [(label, r[0]) for r in prev_m])
        else:   # 改回自動：拿掉你的標註（起始標註也一起拿掉，免得「改回自動」又被拉回原樣）
            c.executemany("DELETE FROM media_label WHERE media_id=?", [(r[0],) for r in prev_m])
            c.executemany("UPDATE media SET media_src=NULL, media_conf=NULL WHERE id=?", [(r[0],) for r in prev_m])
        db.push_action(f"{'媒材改成' + LABELS[label] if label else '媒材改回自動'}（{len(ids)} 張）", {"media_label": undo})
        c.commit()
    schedule(0.5 if not label else 3.0)
    return len(prev_m)


def undo_rows(c, rows):
    """db._apply_undo 用。"""
    for mid, mt, src, conf, lab in rows:
        c.execute("UPDATE media SET media_type=?, media_src=?, media_conf=? WHERE id=?", (mt, src, conf, mid))
        if lab:
            c.execute("INSERT OR REPLACE INTO media_label(media_id, fp, label, src, at) VALUES(?, (SELECT fp FROM media WHERE id=?), ?, ?, ?)",
                      (mid, mid, lab[0], lab[1], lab[2]))
        else:
            c.execute("DELETE FROM media_label WHERE media_id=?", (mid,))
    schedule()


# ---------- 驗證 ----------
def _cv(X, y, cls, folds=5):
    rng = np.random.default_rng(0)
    order = rng.permutation(len(y)); fold = np.empty(len(y), int); fold[order] = np.arange(len(y)) % folds
    P = np.zeros((len(y), len(cls)), np.float32)
    for f in range(folds):
        tr = fold != f
        W, b = fit(X[tr], y[tr], len(cls))
        P[~tr] = proba(X[~tr], W, b)
    pred = P.argmax(1); conf = P.max(1); hi = conf >= CONF
    per = {}
    for k, name in enumerate(cls):
        g = y == k; p = pred == k
        per[name] = {"labels": int(g.sum()), "recall": round(float((pred[g] == k).mean()), 3) if g.any() else None,
                     "precision": round(float((y[p] == k).mean()), 3) if p.any() else None}
    return {"labels": len(y), "accuracy": round(float((pred == y).mean()), 3),
            "confident": {"threshold": CONF, "coverage": round(float(hi.mean()), 3),
                          "accuracy": round(float((pred[hi] == y[hi]).mean()), 3) if hi.any() else None},
            "classes": per,
            "confusion": {cls[a]: {cls[b]: int(((y == a) & (pred == b)).sum()) for b in range(len(cls)) if ((y == a) & (pred == b)).any()}
                          for a in range(len(cls))}}


def evaluate(folds: int = 5) -> dict:
    """k 折交叉驗證（唯讀）：只拿另外幾折訓練去猜這一折，不會自己考自己。
    'clip'＝WD＋CLIP（有 CLIP 特徵的圖用這個），'wd'＝只用 WD（還沒算 CLIP 的圖）。"""
    T = _training()
    if not T:
        return {"error": f"標註少於 {MIN_TOTAL} 張"}
    src = dict(db.conn().execute("SELECT src, COUNT(*) FROM media_label GROUP BY src").fetchall())
    out = {"by_src": src}
    for kind, ids, X, y, cls in T:
        out[kind] = _cv(X, y, cls, folds)
    main = out.get("clip") or out["wd"]
    out.update(labels=main["labels"], accuracy=main["accuracy"], confident=main["confident"], classes=main["classes"])
    return out


# ---------- 快速標媒材：AI 最拿不準的排前面 ----------
RARE = ("3d", "cosplay", "figure")


def live() -> dict:
    return {"version": _state["version"], "running": _state["running"], "last": _state["last"],
            "scheduled": bool(_timer is not None and _timer.is_alive()) or _state["pending"]}
