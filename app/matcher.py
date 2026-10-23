"""否決過濾：讓你按過的「不是 X」和「停止自動標註」對所有自動來源都有效。

- 否決（「不是 X」）對「所有」自動來源有效：模型、同組傳播、Danbooru/Pixiv 搜源。
  新圖和某張被否決 X 的圖夠像（特徵相似度 ≥ neg_sim，或 dHash 幾乎相同），就不會再被自動標上 X。
- 標籤可設為「停止自動標註」，之後只接受手動標記。
- 舊版的「整張圖 kNN 推薦」已移除：畫風、同框角色干擾太大，誤判比找到的多。
特徵向量 = WD tagger 分類頭前的 embedding，先減去全庫平均再做 L2 正規化（中心化 cosine）。
"""
from __future__ import annotations

import logging
import threading
from collections import defaultdict

import numpy as np

from . import db

log = logging.getLogger("matcher")
AUTO_SOURCES = ("model", "match", "online", "danbooru", "pixiv", "set", "ccip")


def _emb(blob: bytes) -> np.ndarray:
    return np.frombuffer(blob, dtype=np.float16).astype(np.float32)


class Matcher:
    def __init__(self):
        self.lock = threading.Lock()
        self.ready = False
        self.built = False          # 已建立（含否決資料）
        self.stats = {}
        self.neg: dict[str, np.ndarray] = {}   # tag → 被否決圖片的向量
        self.blocked: set[str] = set()         # 停止自動標註的 tag
        self.dim = -1
        self.mu = None

    # ---------- 建立索引 ----------
    def build(self):
        c = db.conn()
        blocked = {r[0] for r in c.execute("SELECT name FROM tags WHERE auto_block=1")}
        one = c.execute("SELECT embedding FROM media WHERE embedding IS NOT NULL ORDER BY id DESC LIMIT 1").fetchone()
        if one is None:
            with self.lock:
                self.blocked, self.ready, self.built = blocked, False, True
            return
        mu, dim = self._mean(c)

        rows = c.execute("""SELECT mt.media_id, t.name, m.embedding
            FROM media_tags mt JOIN tags t ON t.id=mt.tag_id JOIN media m ON m.id=mt.media_id
            WHERE m.embedding IS NOT NULL AND m.status!='missing' AND mt.state=-1""").fetchall()
        neg: dict[str, dict[int, np.ndarray]] = defaultdict(dict)
        for r in rows:
            e = _emb(r["embedding"])
            if len(e) == dim:
                neg[r["name"]][r["media_id"]] = e
        with self.lock:
            self.mu, self.dim = mu, dim
            self.neg = {}
            for t, d in neg.items():
                ms = sorted(d)
                M = np.stack([d[m] for m in ms]) - mu
                M /= np.linalg.norm(M, axis=1, keepdims=True) + 1e-8
                self.neg[t] = (np.array(ms), M)
            self.blocked = blocked
            self.ready, self.built = True, True
            self.stats = {"rejected_tags": len(self.neg), "rejected_samples": len(rows), "blocked": len(blocked)}
        log.info("比對索引：%s", self.stats)

    def _mean(self, c):
        """全庫平均向量（中心化用）。每次重建都用「固定」的樣本重算：全部，超過 5000 張就每隔幾張取一張（依編號）。
        以前第一次算完就一直沿用，而且是隨機抽樣：第一次剛好在標記到一半時算，之後所有比對（新角色辨識、否決傳播、
        同一張圖）都用那個偏掉的平均，結果還會因為時間點不同而不同（新角色辨識的測試偶爾失敗就是這個）。"""
        n = c.execute("SELECT COUNT(*) FROM media WHERE embedding IS NOT NULL").fetchone()[0]
        step = max(1, -(-n // 5000))
        sample = [_emb(r[0]) for r in c.execute(
            "SELECT embedding FROM media WHERE embedding IS NOT NULL AND id % ? = 0 ORDER BY id", (step,))]
        if not sample:
            sample = [_emb(r[0]) for r in c.execute("SELECT embedding FROM media WHERE embedding IS NOT NULL LIMIT 5000")]
        dims = {len(e) for e in sample}
        dim = max(dims, key=lambda d: sum(len(e) == d for e in sample))
        return np.mean([e for e in sample if len(e) == dim], axis=0), dim

    # ---------- 否決過濾 ----------
    def norm(self, embs: np.ndarray) -> np.ndarray | None:
        if self.mu is None or embs.shape[-1] != self.dim:
            return None
        q = embs - self.mu
        return q / (np.linalg.norm(q, axis=-1, keepdims=True) + 1e-8)

    def rejected_sim(self, tag: str, qn: np.ndarray, exclude_mid: int | None = None) -> float:
        """q 與 tag 的否決樣本的最高相似度（-1 表示沒有）。"""
        n = self.neg.get(tag)
        if n is None or qn is None:
            return -1.0
        mids, M = n
        sims = M @ qn
        if exclude_mid is not None:
            sims = np.where(mids == exclude_mid, -1.0, sims)
        return float(sims.max()) if len(sims) else -1.0

    def filter_auto(self, mid: int, tags: dict[str, float], emb: np.ndarray | None) -> dict[str, float]:
        """移除「停止自動標註」的 tag，以及跟被否決樣本太像的 tag。"""
        if not tags:
            return tags
        thr = float(db.get_settings()["neg_sim"])
        with self.lock:
            qn = self.norm(emb[None])[0] if emb is not None and self.mu is not None and emb.shape[-1] == self.dim else None
            out = {}
            for t, sc in tags.items():
                if t in self.blocked:
                    continue
                if qn is not None and t in self.neg and self.rejected_sim(t, qn, mid) >= thr:
                    continue
                out[t] = sc
            return out

    # ---------- 否決傳播 ----------
    def similar_auto_rows(self, tid: int, rejected_ids: list[int]) -> list[int]:
        """找出其他圖上、跟剛被否決的圖很像、且是自動加上（未檢查）的同一個 tag。"""
        thr = float(db.get_settings()["neg_sim"])
        c = db.conn()
        ph = ",".join("?" * len(rejected_ids))
        ref = c.execute(f"SELECT embedding, dhash, width, height, id FROM media WHERE id IN ({ph})", rejected_ids).fetchall()
        R = [_emb(r[0]) for r in ref if r[0] is not None and len(r[0]) // 2 == self.dim]
        H = [r[1] & 0xFFFFFFFFFFFFFFFF for r in ref if r[1] is not None]
        rows = c.execute(f"""SELECT mt.media_id, m.embedding, m.dhash, m.width, m.height, m.id FROM media_tags mt JOIN media m ON m.id=mt.media_id
            WHERE mt.tag_id=? AND mt.state=0 AND mt.source IN ({','.join('?' * len(AUTO_SOURCES))})
              AND mt.media_id NOT IN ({ph})""", [tid, *AUTO_SOURCES, *rejected_ids]).fetchall()
        out = set()
        # 1) 同一張圖（不同尺寸/格式的複本）：跟重複判斷用同一個標準（dHash＋長寬比＋內容特徵）
        if H:
            from .pipeline import same_image
            for r in rows:
                if any(same_image(r, x) for x in ref):
                    out.add(r[0])
        # 2) 特徵很像的圖（差分、裁切、同系列）
        if R and self.mu is not None:
            er = [r for r in rows if r[1] is not None and len(r[1]) // 2 == self.dim]
            if er:
                Q = self.norm(np.stack([_emb(r[1]) for r in er]))
                best = (Q @ self.norm(np.stack(R)).T).max(1)
                out.update(r[0] for r, b in zip(er, best) if b >= thr)
        return sorted(out)


matcher = Matcher()


def rematch_all(progress=None):
    """重建否決索引並把否決傳播到全庫（舊名稱保留相容）。"""
    matcher.build()
    n = sweep_rejections()
    if progress:
        progress(1, 1)
    return n


def sweep_rejections() -> int:
    """清掉「停止自動標註」tag 的自動紀錄，以及跟否決樣本太像的自動紀錄。"""
    c = db.conn()
    removed = 0
    with db.write_lock:
        ph = ",".join("?" * len(AUTO_SOURCES))
        for name in matcher.blocked:
            cur = c.execute(f"""DELETE FROM media_tags WHERE state=0 AND source IN ({ph})
                AND tag_id=(SELECT id FROM tags WHERE name=?)""", [*AUTO_SOURCES, name])
            removed += cur.rowcount
        for name, (mids, _) in list(matcher.neg.items()):
            tid = db.tag_id(name, create=False)
            if tid is None:
                continue
            for mid in matcher.similar_auto_rows(tid, [int(x) for x in mids]):
                c.execute("DELETE FROM media_tags WHERE media_id=? AND tag_id=? AND state=0", (mid, tid))
                removed += 1
        c.commit()
    if removed:
        log.info("否決傳播：移除 %d 筆自動標註", removed)
    return removed
