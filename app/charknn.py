"""新角色辨識：用你確認過的圖推薦角色（WD Tagger 不認得的角色也可以）。

做法：新圖跟「你確認過角色的圖」比整體特徵（WD tagger 的 embedding）：
1. 最像的 10 張依相似度投票（權重 exp((相似度 − 第一名) / 0.05)：越接近第一名的票越重）
2. 每個角色所有確認圖的平均特徵（中心）也比一次
兩邊都指向同一個角色、票數集中（≥ 80%）、跟中心夠像（≥ 0.35）才推薦。只進待確認（「你的圖庫」分頁），你按「是」才算。
3. 從你的「是／不是」學習（2026-10，見 FEATS）：通過門檻的再算「你會按是」的機率，太低就不推（梗圖、截圖、
   跟你否決過的圖很像的）。你按完約 3 秒重算、重新學習，待確認頁自己更新（/api/knn/live）。

留一驗證（使用者圖庫，1,400 張確認圖、237 個角色；同一個資料夾的圖不算，避免同一套圖互相作弊）：
  推薦的準確率 99.3%、能認出 40%（投票單獨 top-1 73%、中心單獨 79%、兩者一致時 90%）。
  以前的「整張圖相似度 match」只有 38%：差在投票＋兩種方法一致＋門檻。
數字可以在「系統狀態與診斷」重新驗證（GET /api/diag/eval-knn），確認的圖越多會越準。
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from collections import defaultdict

import numpy as np

from . import candidates, db

log = logging.getLogger("lib.knn")

K = 10
TAU = 0.05
MIN_SHARE = 0.80
MIN_CENTROID = 0.35
MIN_CONFIRMED = 3
SOURCE = "knn"
# 多人圖的「另一個人」：已經確認一個角色後，把那個角色的票拿掉再投一次。
# 實測（930 組「知道一個、猜另一個」）：準確率 90%、猜得出 60%（比第一個人的 99% 低，所以理由會寫「參考」）
SECOND_SHARE = 0.60
SECOND_CENTROID = 0.30
SECOND_RANK = 2

# ---- 從你的「是／不是」學習（2026-10）----
# 通過上面的門檻只是「候選」；再用你按過是／不是的紀錄訓練一個小的邏輯迴歸，算「你會按是」的機率，
# 低於 P_MIN 就不推薦。每次重算都重新訓練，所以你按得越多越貼近你的標準。
# 開發時的圖庫實測（536 筆決定：446 是、90 不是；依角色分 5 組交叉驗證）：
#   門檻 0.4 時留下 94% 的「是」、擋掉 90% 的「不是」（以前的門檻只能全收）。
#   最有用的是「跟你否決過的圖像不像」（奶龍梗圖、截圖這類，否決幾張之後同類的就不再推）；
#   依圖的種類（梗圖／截圖…）直接扣分反而更差（過度擬合），所以不用。
FEATS = ("share", "cen", "sx3", "logn", "noppl", "neg", "negm", "margin")
P_MIN = 0.40      # 預設；實際用設定 knn_p_min（設定 → 自動標記與搜源 → 你的圖庫）


def _pmin() -> float:
    try:
        return float(db.get_settings().get("knn_p_min", P_MIN))
    except (TypeError, ValueError):
        return P_MIN
LEARN_MIN = (40, 10)      # 至少 40 筆決定、其中 10 筆「不是」才用你的紀錄；不夠時只用規則：比起你確認過的，更像你否決過的 → 不推
L2 = 1 / 0.3
# 有學習時門檻可以放寬一點（多出來的由學到的機率把關）。留一驗證（2,072 張你確認過的圖）：
#   原本 0.80／0.35：推薦 677、錯 8；放寬到 0.70／0.32＋學習：推薦 705、錯 12，而且梗圖／截圖這類會被擋掉
LEARNED_SHARE, LEARNED_CENTROID = 0.70, 0.32
_model: dict = {"mode": "rules"}

_lock = threading.Lock()
_state = {"sig": None, "running": False, "last": None, "pending": False, "version": 0}
_stopping = False       # 測試結束／程式關閉時不要再排重算


def _emb(b):
    return np.frombuffer(b, dtype=np.float16).astype(np.float32)


def _mean(dim: int) -> np.ndarray:
    """特徵的平均（正規化用）。每次計算只取一次：以前確認圖和新圖各取一次，中間背景重建 matcher（或沒有 matcher 時各抽一次樣本）
    兩邊就用了不同的平均，推薦會隨機少掉（測試偶爾失敗就是這個）。"""
    from .matcher import matcher
    mu = matcher.mu
    if mu is not None and mu.shape[-1] == dim:
        return np.array(mu, dtype=np.float32, copy=True)
    s = db.conn().execute("SELECT embedding FROM media WHERE embedding IS NOT NULL ORDER BY id LIMIT 3000").fetchall()
    return np.mean([_emb(r[0]) for r in s if len(r[0]) // 2 == dim], axis=0)


def _normalize(E: np.ndarray, dim: int, mu: np.ndarray) -> np.ndarray:
    E = E - mu
    return E / (np.linalg.norm(E, axis=1, keepdims=True) + 1e-8)


def _labeled():
    """你確認過角色的圖：(ids, 資料夾, 正規化特徵, 標籤矩陣, 角色名)。"""
    c = db.conn()
    rows = c.execute("""SELECT m.id, m.path, m.embedding, t.name FROM media_tags mt JOIN tags t ON t.id=mt.tag_id
        JOIN media m ON m.id=mt.media_id WHERE t.category='character' AND mt.state=1 AND m.embedding IS NOT NULL
        AND m.status='done'""").fetchall()
    if not rows:
        return None
    dim = max({len(r["embedding"]) // 2 for r in rows}, key=lambda d: sum(len(r["embedding"]) // 2 == d for r in rows))
    by_m: dict[int, dict] = {}
    for r in rows:
        if len(r["embedding"]) // 2 != dim:
            continue
        d = by_m.setdefault(r["id"], {"path": r["path"], "emb": r["embedding"], "tags": set()})
        d["tags"].add(r["name"])
    cnt = defaultdict(int)
    for d in by_m.values():
        for t in d["tags"]:
            cnt[t] += 1
    chars = sorted(t for t, n in cnt.items() if n >= MIN_CONFIRMED)
    ci = {t: i for i, t in enumerate(chars)}
    mids = [m for m, d in by_m.items() if d["tags"] & set(chars)]
    if not mids or not chars:
        return None
    mu = _mean(dim)
    E = _normalize(np.stack([_emb(by_m[m]["emb"]) for m in mids]), dim, mu)
    M = np.zeros((len(mids), len(chars)), bool)
    for i, m in enumerate(mids):
        for t in by_m[m]["tags"]:
            if t in ci:
                M[i, ci[t]] = True
    folders = np.array([os.path.dirname(by_m[m]["path"].replace("\\", "/")) for m in mids])
    return np.array(mids), folders, E, M, chars, dim, mu


def _predict(q: np.ndarray, E: np.ndarray, M: np.ndarray, C: np.ndarray, valid: np.ndarray, exclude=None):
    """一張圖 → (角色 index, 票數比例, 中心相似度, 最像的幾張 index) 或 None。"""
    s = E @ q
    if exclude is not None:
        s = np.where(exclude, -9, s)
    top = np.argsort(-s)[:K]
    top = top[s[top] > -1]
    if not len(top):
        return None
    w = np.exp((s[top] - s[top[0]]) / TAU)
    votes = (M[top] * w[:, None]).sum(0) / w.sum()
    kt = int(votes.argmax())
    cs = np.where(valid, C @ q, -9)
    ct = int(cs.argmax())
    if kt != ct:
        return None
    return kt, float(votes[kt]), float(cs[ct]), [int(j) for j in top if M[j, kt]][:4]


def _predict_many(Q: np.ndarray, E: np.ndarray, M: np.ndarray, C: np.ndarray, valid: np.ndarray, chunk: int = 512) -> list:
    """_predict 的批次版（結果一樣）：一次算一塊矩陣，不用一張一張跑 Python 迴圈。
    實測（假資料，確認圖 8,000 張、沒角色的圖 40,000 張）：30 秒 → 2 秒左右。"""
    out: list = []
    k = min(K, len(E))
    if not len(E) or not len(Q):
        return [None] * len(Q)
    for i in range(0, len(Q), chunk):
        q = Q[i:i + chunk]
        S = q @ E.T
        top = np.argpartition(-S, k - 1, axis=1)[:, :k]
        st = np.take_along_axis(S, top, 1)
        order = np.argsort(-st, axis=1, kind="stable")
        top, st = np.take_along_axis(top, order, 1), np.take_along_axis(st, order, 1)
        w = np.exp((st - st[:, :1]) / TAU)
        votes = (M[top] * w[:, :, None]).sum(1) / w.sum(1, keepdims=True)
        kt = votes.argmax(1)
        cs = np.where(valid[None, :], q @ C.T, -9)
        ct = cs.argmax(1)
        for r in range(len(q)):
            a = int(kt[r])
            if a != int(ct[r]):
                out.append(None)
                continue
            out.append((a, float(votes[r, a]), float(cs[r, a]), [int(j) for j in top[r] if M[j, a]][:4]))
    return out


def _predict_other(q, E, M, C, valid, known: list[int], exclude=None):
    """已經知道圖上有 known 這幾個角色 → 猜「還有誰」。"""
    s = E @ q
    if exclude is not None:
        s = np.where(exclude, -9, s)
    top = np.argsort(-s)[:K]
    w = np.exp((s[top] - s[top[0]]) / TAU)
    votes = (M[top] * w[:, None]).sum(0) / w.sum()
    rest = 1.0 - float(votes[known].sum()) if known else 1.0
    votes[known] = 0
    if rest <= 1e-6 or votes.max() <= 0:
        return None
    votes = votes / rest
    cs = np.where(valid, C @ q, -9)
    cs[known] = -9
    kt = int(votes.argmax())
    if kt not in np.argsort(-cs)[:SECOND_RANK]:
        return None
    return kt, float(votes[kt]), float(cs[kt]), [int(j) for j in top if M[j, kt]][:4]


def _centroids(E, M):
    C = M.T.astype(np.float32) @ E
    n = M.sum(0)
    C /= np.linalg.norm(C, axis=1, keepdims=True) + 1e-8
    return C, n


def _negatives(chars, dim, mu) -> dict:
    """你否決過的「不是 X」（任何來源）：角色 → (圖編號, 正規化特徵)。"""
    ci = {t: i for i, t in enumerate(chars)}
    by: dict[int, list] = defaultdict(list)
    for mid, name, e in db.conn().execute("""SELECT mt.media_id, t.name, m.embedding FROM media_tags mt JOIN tags t ON t.id=mt.tag_id
            JOIN media m ON m.id=mt.media_id WHERE t.category='character' AND mt.state=-1 AND m.embedding IS NOT NULL"""):
        if name in ci and len(e) // 2 == dim:
            by[ci[name]].append((mid, _emb(e)))
    return {k: (np.array([m for m, _ in v]), _normalize(np.stack([x for _, x in v]), dim, mu)) for k, v in by.items()}


def _feat(q, s, cs, M, k, negs, people, self_mid=None) -> list[float]:
    """一筆推薦（圖 q、角色 k）的特徵。s＝跟每張確認圖的相似度（不能用的設 -9），cs＝跟每個角色中心的相似度（不能用的 -9）。"""
    kk = min(K, len(s))
    top = np.argpartition(-s, kk - 1)[:kk]
    top = top[np.argsort(-s[top])]
    w = np.exp((s[top] - s[top[0]]) / TAU)
    share = float((M[top, k] * w).sum() / w.sum())
    px = s[M[:, k]]
    px = np.sort(px[px > -2])
    sx1 = float(px[-1]) if len(px) else -1.0
    sx3 = float(px[-3:].mean()) if len(px) else -1.0
    other = np.delete(cs, k)
    margin = float(cs[k] - (other.max() if len(other) else -1))
    neg = -1.0
    if k in negs:
        nm, NE = negs[k]
        ns = NE @ q
        if self_mid is not None:
            ns = ns[nm != self_mid]
        if len(ns):
            neg = float(ns.max())
    return [share, float(cs[k]), sx3, float(np.log(max(len(px), 1))), float((people or 0) == 0),
            max(neg, 0.0), (neg - sx1) if neg > -1 else -0.5, margin]


def _fit(X: np.ndarray, y: np.ndarray) -> dict:
    """邏輯迴歸（標準化、L2、是／不是各佔一半權重），Newton 法；不用 sklearn。"""
    mu, sd = X.mean(0), X.std(0) + 1e-6
    Z = np.hstack([(X - mu) / sd, np.ones((len(X), 1))])
    npos, nneg = max(1, int(y.sum())), max(1, int((1 - y).sum()))
    sw = np.where(y == 1, len(y) / (2 * npos), len(y) / (2 * nneg))
    reg = np.full(Z.shape[1], L2)
    reg[-1] = 0
    b = np.zeros(Z.shape[1])
    for _ in range(30):
        p = 1 / (1 + np.exp(-(Z @ b)))
        g = Z.T @ (sw * (p - y)) + reg * b
        H = Z.T @ (Z * (sw * p * (1 - p))[:, None]) + np.diag(reg + 1e-9)
        step = np.linalg.solve(H, g)
        b -= step
        if np.abs(step).max() < 1e-6:
            break
    return {"mu": mu, "sd": sd, "coef": b[:-1], "bias": float(b[-1])}


def _prob(m: dict, x) -> float:
    z = ((np.asarray(x) - m["mu"]) / m["sd"]) @ m["coef"] + m["bias"]
    return float(1 / (1 + np.exp(-z)))


def _decisions(L, C, cnt, negs):
    """你按過是／不是的「你的圖庫」推薦（只算第一個人的推薦；「另一個人」規則不同）→ 特徵、答案、角色。
    每一筆都假裝還沒決定：這張圖自己不算確認圖（留一），也不算否決樣本。"""
    mids, folders, E, M, chars, dim, mu = L
    ci = {t: i for i, t in enumerate(chars)}
    rows = [r for r in db.conn().execute("""SELECT mt.media_id, t.name, mt.state, mt.reason, m.embedding, m.people
            FROM media_tags mt JOIN tags t ON t.id=mt.tag_id JOIN media m ON m.id=mt.media_id
            WHERE mt.source=? AND mt.state IN (1,-1) AND t.category='character' AND m.embedding IS NOT NULL""", (SOURCE,))
            if r["name"] in ci and len(r["embedding"]) // 2 == dim and not (r["reason"] or "").startswith("另一個人")]
    if not rows:
        return np.zeros((0, len(FEATS))), np.zeros(0), []
    pos = {int(m): i for i, m in enumerate(mids)}
    Craw = M.T.astype(np.float32) @ E
    n2 = (Craw ** 2).sum(1)
    Q = _normalize(np.stack([_emb(r["embedding"]) for r in rows]), dim, mu)
    S = Q @ E.T
    X, y, g = [], [], []
    for r, q, s in zip(rows, Q, S):
        k = ci[r["name"]]
        cr, nn, cv = Craw @ q, n2, cnt
        i = pos.get(r["media_id"])
        if i is not None:
            s = s.copy()
            s[i] = -9
            mk = M[i].astype(np.float32)
            cr = cr - mk * float(E[i] @ q)
            nn = n2 - 2 * mk * (Craw @ E[i]) + mk
            cv = cnt - M[i]
        cs = np.where(cv >= MIN_CONFIRMED, cr / (np.sqrt(np.maximum(nn, 1e-12))), -9)
        X.append(_feat(q, s, cs, M, k, negs, r["people"], self_mid=r["media_id"]))
        y.append(1.0 if r["state"] == 1 else 0.0)
        g.append(r["name"])
    return np.array(X), np.array(y), g


def _train(L, C, cnt, negs) -> dict:
    X, y, g = _decisions(L, C, cnt, negs)
    n_yes, n_no = int(y.sum()), int(len(y) - y.sum())
    info = {"decisions": len(y), "yes": n_yes, "no": n_no, "p_min": _pmin()}
    if len(y) < LEARN_MIN[0] or n_no < LEARN_MIN[1] or n_yes < LEARN_MIN[1]:
        return {"mode": "rules", **info, "why": f"要至少 {LEARN_MIN[0]} 筆決定、{LEARN_MIN[1]} 筆「不是」才用你的紀錄"}
    m = _fit(X, y)
    p = np.array([_prob(m, x) for x in X])
    info.update(kept_yes=int(((p >= P_MIN) & (y == 1)).sum()), kept_no=int(((p >= P_MIN) & (y == 0)).sum()),
                weights={f: round(float(w / s), 3) for f, w, s in zip(FEATS, m["coef"], m["sd"])})
    return {"mode": "learned", **m, **info, "_X": X, "_y": y, "_g": g}


def _accept(m: dict, x) -> tuple[bool, float | None]:
    """(要不要推薦, 你會按是的機率)。資料不夠時：比起你確認過的圖，更像你否決過的 → 不推。"""
    if m.get("mode") == "learned":
        p = _prob(m, x)
        return p >= _pmin(), p
    return x[FEATS.index("negm")] <= 0, None


def model_info(cv: bool = False) -> dict:
    """目前的學習狀態（系統狀態與診斷用）。cv=True：依角色分 5 組交叉驗證（每組的角色不在訓練資料裡）。"""
    m = _model
    if cv:                           # 診斷頁：用現在的資料重新訓練一次（唯讀）
        L = _labeled()
        if L is None:
            return {"mode": "rules", "decisions": 0}
        C, cnt = _centroids(L[2], L[3])
        m = _train(L, C, cnt, _negatives(L[4], L[5], L[6]))
    out = {k: v for k, v in m.items() if not k.startswith("_") and k not in ("mu", "sd", "coef", "bias")}
    if cv and m.get("mode") == "learned":
        X, y, g = m["_X"], m["_y"], m["_g"]
        names = sorted(set(g))
        fold = {n: i % 5 for i, n in enumerate(names)}
        f = np.array([fold[x] for x in g])
        p = np.zeros(len(y))
        for k in range(5):
            tr = f != k
            if tr.all() or not tr.any() or len(set(y[tr])) < 2:
                continue
            mk = _fit(X[tr], y[tr])
            p[~tr] = [_prob(mk, x) for x in X[~tr]]
        out["cv"] = {"kept_yes": int(((p >= P_MIN) & (y == 1)).sum()), "kept_no": int(((p >= P_MIN) & (y == 0)).sum()),
                     "yes": int(y.sum()), "no": int(len(y) - y.sum())}
    return out


def evaluate() -> dict:
    """留一驗證（唯讀）：每張確認過的圖假裝沒標過，用其他資料夾的圖猜（同一個資料夾的圖不算，避免同一套圖互相作弊）。

    為了不被「倖存者偏差」騙：
    - 從「你的圖庫」推薦確認的圖不拿來算（那些本來就是它有把握的，會把數字灌高：召回 85%、準確率 100%）
    - 分開算單人圖／多人圖、WD 不認得的角色
    - 開放集：只確認過 1–2 張的角色（它學不到）的圖，它不該推薦任何人 → 看誤報率
    - 多人圖「另一個人」：知道一個、猜另一個
    """
    L = _labeled()
    if L is None:
        return {"n": 0}
    mids, folders, E, M, chars, dim, mu = L
    c = db.conn()
    wd = {r[0] for r in c.execute("""SELECT DISTINCT t.name FROM media_tags mt JOIN tags t ON t.id=mt.tag_id
        WHERE t.category='character' AND mt.source='model'""")}
    via_knn = {r[0] for r in c.execute("""SELECT mt.media_id FROM media_tags mt JOIN tags t ON t.id=mt.tag_id
        WHERE t.category='character' AND mt.state=1 AND mt.source=?""", (SOURCE,))}
    ppl = {r[0]: r[1] or 0 for r in c.execute("SELECT id, people FROM media")}
    buckets = {k: [0, 0, 0] for k in ("all", "single", "multi", "wd_unknown")}   # n, 推薦, 對
    sec = [0, 0, 0]
    cache = {}
    for i in np.argsort(folders, kind="stable"):     # 同資料夾排在一起：中心只要每個資料夾算一次
        if int(mids[i]) in via_knn:
            continue
        ex = folders == folders[i]
        key = folders[i]
        if key not in cache:
            keep = ~ex
            C, cnt = _centroids(E[keep], M[keep])
            cache = {key: (C, cnt >= 2)}
        C, valid = cache[key]
        p = _predict(E[i], E, M, C, valid, exclude=ex)
        hit = p and p[1] >= MIN_SHARE and p[2] >= MIN_CENTROID
        ok = bool(hit and M[i, p[0]])
        tags = np.nonzero(M[i])[0]
        ks = ["all", "multi" if ppl.get(int(mids[i]), 0) >= 2 else "single"]
        if not any(chars[j] in wd for j in tags):
            ks.append("wd_unknown")
        for k in ks:
            buckets[k][0] += 1
            buckets[k][1] += bool(hit)
            buckets[k][2] += ok
        if len(tags) >= 2:                   # 多人圖：知道一個，猜另一個
            for known in tags:
                sec[0] += 1
                q = _predict_other(E[i], E, M, C, valid, [int(known)], exclude=ex)
                if q and q[1] >= SECOND_SHARE and q[2] >= SECOND_CENTROID:
                    sec[1] += 1
                    sec[2] += bool(M[i, q[0]])
    # 開放集：只確認過 1–2 張的角色（學不到）→ 不該推薦
    rare = [r for r in c.execute("""SELECT m.id, m.path, m.embedding FROM media m WHERE m.embedding IS NOT NULL AND m.status='done'
        AND m.id IN (SELECT x.media_id FROM media_tags x JOIN tags t ON t.id=x.tag_id WHERE x.state=1 AND t.category='character')""")
            if int(r[0]) not in set(mids.tolist()) and len(r[2]) // 2 == dim]
    fp = 0
    if rare:
        C, cnt = _centroids(E, M)
        Q = _normalize(np.stack([_emb(r[2]) for r in rare]), dim, mu)
        for r, q in zip(rare, Q):
            ex = folders == os.path.dirname(r[1].replace("\\", "/"))
            p = _predict(q, E, M, C, cnt >= 2, exclude=ex)
            fp += bool(p and p[1] >= MIN_SHARE and p[2] >= MIN_CENTROID)
    f = lambda b: {"n": b[0], "suggested": b[1], "precision": round(b[2] / b[1], 3) if b[1] else None,
                   "recall": round(b[1] / b[0], 3) if b[0] else None}
    out = {k: f(v) for k, v in buckets.items()}
    return {**out["all"], "chars": len(chars), "excluded_knn_confirmed": len(via_knn & set(mids.tolist())),
            "single": out["single"], "multi": out["multi"], "wd_unknown": out["wd_unknown"],
            "second_person": {"pairs": sec[0], "suggested": sec[1], "precision": round(sec[2] / sec[1], 3) if sec[1] else None,
                              "recall": round(sec[1] / sec[0], 3) if sec[0] else None},
            "open_set": {"n": len(rare), "false_suggestions": fp, "rate": round(fp / len(rare), 3) if rare else None},
            "thresholds": {"k": K, "share": MIN_SHARE, "centroid": MIN_CENTROID, "min_confirmed": MIN_CONFIRMED,
                           "second_share": SECOND_SHARE, "second_centroid": SECOND_CENTROID,
                           "learned_share": LEARNED_SHARE, "learned_centroid": LEARNED_CENTROID},
            "learning": model_info(cv=True)}


def run(block: bool = False, only: list[int] | None = None) -> dict:
    """幫還沒有角色的圖推薦角色（放進待確認）；不再符合條件的舊推薦收掉（你已經決定過的不動）。
    block=True：正在算就等它算完再算一次（測試、需要馬上看到結果的地方用）。
    only=新圖的編號：只算這幾張（新圖處理完用；你確認過的圖沒變，其他圖的推薦也不會變）。
    以前每批新圖都整庫重算，4 萬張時一次要半分鐘。"""
    if only is not None and not only:
        return {"added": 0}
    if only is not None and len(only) > 5000:     # 第一次掃描幾萬張：整庫算一次比較快
        only = None
    if not _lock.acquire(blocking=block):
        _state["pending"] = True
        return {"busy": True}
    t0 = time.time()
    try:
        _state["running"] = True
        _state["pending"] = False
        if not db.get_settings().get("knn_enabled", True):
            return {"disabled": True}
        L = _labeled()
        c = db.conn()
        if L is None:
            return {"added": 0}
        mids, folders, E, M, chars, dim, mu = L
        C, cnt = _centroids(E, M)
        valid = cnt >= MIN_CONFIRMED
        # 還沒有已確認角色的圖（略過、排除的不推薦）。
        # 用 NOT IN 而不是 NOT EXISTS：SQLite 會替相關子查詢挑錯索引（state），實測 4 千張要 9 秒；NOT IN 只算一次

        from . import quicktags
        nc_sql, nc_p = quicktags.no_char_ids_sql()      # 梗圖、截圖：不推薦角色
        rows = c.execute("""SELECT m.id, m.embedding, m.people FROM media m WHERE m.status='done' AND m.embedding IS NOT NULL
            AND COALESCE(m.excluded,0)=0 AND COALESCE(m.unrec_skip,0)=0
            AND m.id NOT IN (SELECT x.media_id FROM media_tags x JOIN tags t ON t.id=x.tag_id
                             WHERE x.state=1 AND t.category='character')
            AND m.id NOT IN (""" + nc_sql + ")" + _only_sql(only), [*nc_p, *(only or [])]).fetchall()
        rows = [r for r in rows if len(r["embedding"]) // 2 == dim]
        # 從你的是／否學習：每次重算都重新訓練（你剛按的也算進去）
        global _model
        negs = _negatives(chars, dim, mu)
        model = _train(L, C, cnt, negs)
        _model = model
        want: dict[tuple[int, str], dict] = {}
        held = 0
        if rows:
            Q = _normalize(np.stack([_emb(r["embedding"]) for r in rows]), dim, mu)
            g_share, g_cen = (LEARNED_SHARE, LEARNED_CENTROID) if model["mode"] == "learned" else (MIN_SHARE, MIN_CENTROID)
            for r, q, p in zip(rows, Q, _predict_many(Q, E, M, C, valid)):
                if p and p[1] >= g_share and p[2] >= g_cen:
                    kt, share, cen, refs = p
                    x = _feat(q, E @ q, np.where(valid, C @ q, -9), M, kt, negs, r["people"])
                    ok, prob = _accept(model, x)
                    if not ok:
                        held += 1
                        continue
                    want[(r["id"], chars[kt])] = {"share": share, "cen": cen, "prob": prob, "refs": [int(mids[j]) for j in refs],
                                                  "emb": _emb(r["embedding"])}
        # 多人圖：已經標了一個（或幾個）角色、但比人數少 → 猜另一個人
        from . import people
        want.update(_second_people(c, mids, E, M, chars, C, valid, dim, mu, people.SHORT_SQL, only=only))
        added = removed = 0
        with db.write_lock:
            old = c.execute("""SELECT mt.media_id, t.name FROM media_tags mt JOIN tags t ON t.id=mt.tag_id
                               WHERE mt.source=? AND mt.state=0""" + _only_sql(only, "mt.media_id"), [SOURCE, *(only or [])]).fetchall()
            for r in old:
                if (r[0], r[1]) not in want:
                    c.execute("DELETE FROM media_tags WHERE media_id=? AND tag_id=(SELECT id FROM tags WHERE name=?) AND source=? AND state=0",
                              (r[0], r[1], SOURCE))
                    removed += 1
            have = {(r[0], r[1]) for r in old}
            from . import names
            lang = db.get_settings()["display_lang"]
            for (mid, name), d in want.items():
                n_ref = len(d["refs"])
                reason = (f"另一個人（參考）：已標 {'、'.join(names.display(k, [], None, lang) for k in d.get('known', [])[:2]) or '一個角色'}，跟你確認過的 {n_ref} 張很像"
                          if d.get("second") else
                          f"跟你確認過的 {n_ref} 張{('最像' if n_ref else '')}（{round(d['share'] * 100)}% 同意）"
                          + (f"，依你的是／否估 {round(d['prob'] * 100)}% 會是" if d.get("prob") is not None else ""))
                score = round(d["prob"] if d.get("prob") is not None else d["cen"], 3)
                if (mid, name) not in have:
                    if candidates.add(mid, name, "character", candidates.T_AI, SOURCE, score=score,
                                      reason=reason, emb=d["emb"]):
                        added += 1
                    else:
                        continue
                else:
                    c.execute("""UPDATE media_tags SET score=?, reason=? WHERE media_id=? AND source=? AND state=0
                                 AND tag_id=(SELECT id FROM tags WHERE name=?)""", (score, reason, mid, SOURCE, name))
                c.execute("INSERT OR REPLACE INTO knn_refs(media_id, tag, refs) VALUES(?,?,?)", (mid, name, json.dumps(d["refs"])))
            c.commit()
        res = {"added": added, "removed": removed, "suggested": len(want), "held_back": held, "labeled": len(mids), "chars": len(chars),
               "learn": model["mode"], "seconds": round(time.time() - t0, 1)}
        if added or removed:          # 頁面看到版本變了就自己更新（不用重新整理）
            _state["version"] += 1
            _state["change"] = {"added": added, "removed": removed, "at": time.time()}
        if only is None:          # 背景工作面板顯示的是整庫的數字
            _state["last"] = {**res, "at": time.time()}
        else:
            res["only"] = len(only)
        log.info("新角色辨識：%s", res)
        return res
    except Exception as e:  # noqa
        log.warning("新角色辨識失敗：%s", e)
        return {"error": str(e)}
    finally:
        _state["running"] = False
        _lock.release()
        # 跑的時候又有人要求重算（你剛確認／否決、新圖處理完）：以前這個要求直接丟掉，要等下一次別的事件才會反映
        if _state["pending"] and not _stopping:
            threading.Thread(target=run, daemon=True, name="knn-rerun").start()


def live() -> dict:
    """頁面輪詢用：版本（推薦有增減就 +1）、是不是正在算／等一下要算。"""
    return {"version": _state["version"], "running": _state["running"],
            "scheduled": bool(_timer is not None and _timer.is_alive()) or _state["pending"],
            "change": _state.get("change"), "learn": _model.get("mode")}


def _only_sql(only, col: str = "m.id") -> str:
    return f" AND {col} IN ({','.join('?' * len(only))})" if only else ""


def _second_people(c, mids, E, M, chars, C, valid, dim, mu, where_sql, only: list[int] | None = None) -> dict:
    ci = {t: i for i, t in enumerate(chars)}
    extra = f" AND m.id IN ({','.join('?' * len(only))})" if only else ""
    rows = c.execute(f"""SELECT m.id, m.embedding FROM media m WHERE m.status='done' AND m.embedding IS NOT NULL
        AND COALESCE(m.excluded,0)=0 AND {where_sql}{extra}""", only or []).fetchall()
    rows = [r for r in rows if len(r["embedding"]) // 2 == dim]
    if not rows:
        return {}
    known: dict[int, list[int]] = {}
    ids = [r["id"] for r in rows]
    for i in range(0, len(ids), 800):
        ch = ids[i:i + 800]
        for mid, name in c.execute(f"""SELECT x.media_id, t.name FROM media_tags x JOIN tags t ON t.id=x.tag_id
                WHERE x.state=1 AND t.category='character' AND x.media_id IN ({','.join('?' * len(ch))})""", ch):
            if name in ci:
                known.setdefault(mid, []).append(ci[name])
    out = {}
    Q = _normalize(np.stack([_emb(r["embedding"]) for r in rows]), dim, mu)
    for r, q in zip(rows, Q):
        p = _predict_other(q, E, M, C, valid, known.get(r["id"], []))
        if p and p[1] >= SECOND_SHARE and p[2] >= SECOND_CENTROID:
            kt, share, cen, refs = p
            out[(r["id"], chars[kt])] = {"share": share, "cen": cen, "refs": [int(mids[j]) for j in refs],
                                         "emb": _emb(r["embedding"]), "second": True,
                                         "known": [chars[k] for k in known.get(r["id"], [])]}
    return out


def suggest_other(mid: int) -> list[dict]:
    """確認一個角色之後馬上用：這張多人圖「還有誰」（只給畫面參考，不寫進資料庫）。"""
    L = _labeled()
    if L is None:
        return []
    mids, folders, E, M, chars, dim, mu = L
    C, cnt = _centroids(E, M)
    got = _second_people(db.conn(), mids, E, M, chars, C, cnt >= MIN_CONFIRMED, dim, mu, "1=1", only=[mid])
    return [{"name": n, "share": round(d["share"], 2), "refs": d["refs"]} for (m, n), d in got.items()]


_timer: threading.Timer | None = None


def schedule(delay: float = 3.0):
    """你確認／否決之後過一會兒重算（連續操作時只算一次）。整庫重算現在不到 1 秒（6,800 張），
    所以只等 3 秒：待確認頁會看到版本變了，自己把新推薦加進來、收回的拿掉。"""
    global _timer
    if _timer is not None:
        _timer.cancel()
    _timer = threading.Timer(delay, run)
    _timer.daemon = True
    _timer.start()


def refs_for(pairs: list[tuple[int, str]]) -> dict:
    """待確認頁：每筆推薦「跟哪幾張很像」。"""
    out = {}
    c = db.conn()
    for mid, name in pairs:
        r = c.execute("SELECT refs FROM knn_refs WHERE media_id=? AND tag=?", (mid, name)).fetchone()
        if r:
            out[(mid, name)] = json.loads(r[0])
    return out
