"""從畫師反推出處：已經知道的畫師 → 抓他在 Danbooru 上的作品縮圖 → 在本機跟還沒找到出處的圖比對。

為什麼：IQDB 一張一張上傳很慢、截圖／重存檔常失手；但同一位畫師的圖常常一次存很多張。
只下載 Danbooru 的 180px 小縮圖（每張約 5–15 KB），不上傳你的任何圖片。

比對（先便宜後昂貴）：
1. 長寬比要接近（±3%）—— 大部分作品在這一步就不用下載
2. 下載縮圖算 dHash，跟本機圖的 dHash 比（海明距離）
3. 距離不夠小的，再用 WD 特徵（縮圖跑一次模型）確認
"""
from __future__ import annotations

import io
import logging
import time

import numpy as np

from . import db, media

log = logging.getLogger("artistscan")


def _popcount(x: np.ndarray) -> np.ndarray:
    x = x.astype(np.uint64)
    out = np.zeros(x.shape, dtype=np.uint64)
    for _ in range(64):
        out += x & np.uint64(1)
        x >>= np.uint64(1)
    return out


def targets() -> dict:
    """還沒找到出處的圖片：id、dhash、長寬比、特徵向量。"""
    c = db.conn()
    rows = c.execute("""SELECT m.id, m.dhash, m.width, m.height, m.embedding, m.fp FROM media m
        WHERE m.kind='image' AND m.status='done' AND COALESCE(m.excluded,0)=0 AND m.dhash IS NOT NULL
          AND m.id NOT IN (SELECT media_id FROM album_items)
          AND m.width > 0 AND m.height > 0
          AND NOT EXISTS (SELECT 1 FROM media_source s WHERE s.media_id=m.id AND s.status='found')""").fetchall()
    ids = np.array([r["id"] for r in rows], dtype=np.int64)
    dh = np.array([r["dhash"] & ((1 << 64) - 1) for r in rows], dtype=np.uint64)
    ratio = np.array([r["width"] / r["height"] for r in rows], dtype=np.float64)
    emb = {r["id"]: r["embedding"] for r in rows if r["embedding"]}
    fps = {r["fp"] for r in c.execute("SELECT fp FROM media WHERE fp IS NOT NULL")}
    return {"ids": ids, "dh": dh, "ratio": ratio, "emb": emb, "fps": fps}


def seed_artists(limit: int) -> list[tuple[str, int]]:
    """已確認的創作者，按你收藏的張數排序（收越多的越可能還有沒找到的）。"""
    return [(r[0], r[1]) for r in db.conn().execute("""SELECT t.name, COUNT(*) n FROM media_tags mt JOIN tags t ON t.id=mt.tag_id
        WHERE t.category='artist' AND mt.state=1 GROUP BY t.id ORDER BY n DESC LIMIT ?""", (limit,))]


def _fetch(url: str) -> bytes | None:
    """縮圖在 Danbooru 的 CDN 上（靜態檔），不佔 API 的速率限制，可以幾張一起抓。"""
    from .httpclient import client
    try:
        r = client().get(url, timeout=20)
        return r.content if r.status_code < 400 else None
    except Exception:  # noqa
        return None


def _target_matrix(T: dict):
    """所有目標圖的特徵向量（中心化＋正規化），一次算好，之後每張縮圖只要一次矩陣乘法。"""
    from .matcher import _emb, matcher
    ids, vecs = [], []
    for k, mid in enumerate(T["ids"]):
        b = T["emb"].get(int(mid))
        if b:
            ids.append(k)
            vecs.append(_emb(b))
    if not vecs:
        return None, None
    dim = max({len(v) for v in vecs}, key=lambda d: sum(len(v) == d for v in vecs))
    keep = [(k, v) for k, v in zip(ids, vecs) if len(v) == dim]
    M = np.stack([v for _, v in keep])
    mu = matcher.mu if matcher.mu is not None and matcher.mu.shape[-1] == dim else M.mean(0)
    M = M - mu
    M /= np.linalg.norm(M, axis=1, keepdims=True) + 1e-8
    return np.array([k for k, _ in keep]), (M.astype(np.float32), mu)


PRE_MIN = 0.55      # 縮圖（180px）的 WD 特徵相似度：同一張常見 0.68–0.9（差分圖會偏低），不同圖多半 < 0.2；真正把關的是下面的大圖確認
CONFIRM_MIN = 0.90  # 下載 Danbooru 大圖再比一次：真的同一張 0.92–0.99
HASH_CAND = 10      # dHash 距離 ≤ 10 的才當候選（實測 dHash 單獨判斷很不可靠：距離 1–6 也常是不同的圖）


def run(state: dict, stop, max_artists: int = 40, max_posts: int = 300, max_mb: float = 100, dry_run: bool = False,
        deep: bool = False, emb_min: float = PRE_MIN):
    """每張縮圖：dHash 找候選（deep 模式再加上「特徵最像的那張」）→ 縮圖特徵相似度夠高 → 下載大圖確認 → 才算找到。
    deep=True 每張縮圖都跑模型（慢，但裁切、加邊框、截圖也找得到）；False 只有 dHash 接近的才跑。"""
    from concurrent.futures import ThreadPoolExecutor
    from PIL import Image
    from . import tagger
    from .danbooru import _post_summary, dan_get, verify_similar, DANBOORU
    from .online import save_source, auto_apply
    T = targets()
    artists = seed_artists(max_artists)
    st = state.setdefault("artistscan", {})
    st.update(artists_total=len(artists), artists_done=0, posts=0, skipped_have=0, downloaded=0, mb=0.0,
              matched=0, by_hash=0, by_emb=0, candidates=0, confirm_fail=0, targets=len(T["ids"]), matches=[], near=[],
              dry_run=dry_run, deep=deep, started=time.time(), finished=None)
    if not len(T["ids"]) or not artists:
        st["finished"] = time.time()
        return
    tg = tagger.ensure()
    if tg is None:
        st.update(finished=time.time(), error="模型沒有載入，無法比對")
        return
    kidx, MM = _target_matrix(T)
    if MM is None:
        st.update(finished=time.time(), error="沒有特徵向量可以比對")
        return
    M, mu = MM
    row_of = {int(k): j for j, k in enumerate(kidx)}     # T 的索引 → 特徵矩陣的列
    c = db.conn()
    paths = {r[0]: r[1] for r in c.execute("SELECT id, path FROM media WHERE kind='image'")}
    linked = {r[0] for r in c.execute("SELECT post_url FROM media_source WHERE status='found' AND post_url IS NOT NULL")}
    taken: set[int] = set()
    t0 = time.time()
    pool = ThreadPoolExecutor(max_workers=6, thread_name_prefix="artistscan")

    def accept(p, k, dist, sim, conf, artist, how):
        mid = int(T["ids"][k])
        taken.add(mid)
        st["matched"] += 1
        st[how] += 1
        post = _post_summary(p)
        if len(st["matches"]) < 500:
            st["matches"].append({"id": mid, "post_id": p.get("id"), "artist": artist, "dist": dist,
                                  "sim": round(sim, 3), "confirm": round(conf, 3), "how": how})
        if not dry_run:
            res = {"status": "found", "method": "artist_scan", "similarity": round(conf * 100, 1),
                   "post_url": f"{DANBOORU}/posts/{p.get('id')}", "source_url": post["source"] or None,
                   "pixiv_id": post.get("pixiv_id"),
                   "data": {"danbooru": post, "verified": conf,
                            "artist_scan": {"artist": artist, "dhash": dist, "sim": sim, "confirm": conf}}}
            save_source(mid, res)
            auto_apply(mid, res)

    for ai, (artist, _n) in enumerate(artists):
        if stop() or st["mb"] >= max_mb:
            break
        state["phase"] = f"從畫師反推：{artist}"
        page, seen = 1, 0
        while seen < max_posts and not stop() and st["mb"] < max_mb:
            try:
                posts = dan_get("/posts.json", {"tags": artist, "limit": 100, "page": page,
                                                "only": "id,md5,image_width,image_height,preview_file_url,source,rating,"
                                                        "pixiv_id,tag_string_character,tag_string_copyright,tag_string_artist,tag_string_general"})
            except Exception as e:  # noqa
                log.info("抓 %s 的作品失敗：%s", artist, e)
                break
            if not posts:
                break
            todo = []
            for p in posts[: max_posts - seen]:
                if p.get("md5") in T["fps"] or f"{DANBOORU}/posts/{p.get('id')}" in linked:
                    st["skipped_have"] += 1      # 這張你已經有、或已經對上了
                    continue
                if p.get("preview_file_url") and p.get("image_width") and p.get("image_height"):
                    todo.append(p)
            seen += len(posts)
            st["posts"] += len(posts)
            batch = []   # (post, img, 候選 dHash 索引, 距離陣列)
            for p, data in zip(todo, pool.map(lambda p: _fetch(p["preview_file_url"]), todo)):
                if stop() or not data:
                    continue
                st["downloaded"] += 1
                st["mb"] = round(st["mb"] + len(data) / 1e6, 2)
                try:
                    img = Image.open(io.BytesIO(data))
                    img.load()
                    d = media.dhash(img) & ((1 << 64) - 1)
                except Exception:  # noqa
                    continue
                dist = _popcount(T["dh"] ^ np.uint64(d)).astype(int)
                near = [int(k) for k in np.argsort(dist)[:3] if dist[k] <= HASH_CAND]
                if near or deep:
                    batch.append((p, img, near, dist))
            # 需要的縮圖一次 16 張跑模型
            for b in range(0, len(batch), 16):
                if stop():
                    break
                chunk = batch[b:b + 16]
                _, E = tg.run(np.stack([tg.preprocess(media.to_rgb(im)) for _, im, _, _ in chunk]))
                E = E - mu
                E /= np.linalg.norm(E, axis=1, keepdims=True) + 1e-8
                E = E.astype(np.float32)
                for (p, _im, near, dist), e in zip(chunk, E):
                    cands = {k for k in near if k in row_of}
                    if deep:
                        row = M @ e
                        cands.add(int(kidx[int(np.argmax(row))]))
                    scored = sorted(((float(M[row_of[k]] @ e), k) for k in cands if int(T["ids"][k]) not in taken), reverse=True)
                    if not scored:
                        continue
                    sim, k = scored[0]
                    mid = int(T["ids"][k])
                    if sim >= 0.45 and len(st["near"]) < 400:
                        st["near"].append({"id": mid, "post_id": p.get("id"), "dist": int(dist[k]), "sim": round(sim, 3)})
                    if sim < emb_min:
                        continue
                    st["candidates"] += 1
                    try:
                        conf = verify_similar(paths.get(mid, ""), p["id"])   # 下載大圖再確認一次
                    except Exception:  # noqa
                        conf = None
                    if conf is None or conf < CONFIRM_MIN:
                        st["confirm_fail"] += 1
                        continue
                    accept(p, k, int(dist[k]), sim, conf, artist, "by_hash" if k in near else "by_emb")
            if len(posts) < 100:
                break
            page += 1
        st["artists_done"] = ai + 1
        state.update(done=ai + 1, total=len(artists))
        el = time.time() - t0
        state["eta"] = round(el / (ai + 1) * (len(artists) - ai - 1)) if ai else None
    pool.shutdown(wait=False)
    st["finished"] = time.time()
    st["seconds"] = round(st["finished"] - st["started"])
    log.info("從畫師反推完成：%s", {k: v for k, v in st.items() if k not in ("matches", "near")})
