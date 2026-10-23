"""媒材分類（b77）：用你標的學；手動的不被蓋掉；沒把握的集中在「未定」；可以撤銷。"""
import json

import numpy as np

from tests.conftest import ids_by_name


def test_fit_learns_separable():
    """純數學：三群分得開的點，訓練完要分得出來，信心也要高。"""
    from app import mediaclf
    rng = np.random.default_rng(1)
    C = rng.normal(size=(3, 64))
    X = np.concatenate([C[k] + 0.3 * rng.normal(size=(30, 64)) for k in range(3)]).astype(np.float32)
    y = np.repeat(np.arange(3), 30)
    X = mediaclf._norm(X)
    W, b = mediaclf.fit(X, y, 3)
    P = mediaclf.proba(X, W, b)
    assert (P.argmax(1) == y).mean() > 0.97
    assert np.median(P.max(1)) > 0.6


def test_label_filter_train_undo(client, monkeypatch):
    from app import db, mediaclf
    c = db.conn()
    ids = sorted(i for i in ids_by_name(client).values()
                 if c.execute("SELECT 1 FROM media WHERE id=? AND kind='image' AND status='done' AND embedding IS NOT NULL", (i,)).fetchone())
    assert len(ids) >= 6
    prev = {i: tuple(c.execute("SELECT media_type, media_src, media_conf FROM media WHERE id=?", (i,)).fetchone()) for i in ids}
    monkeypatch.setattr(mediaclf, "MIN_TOTAL", 4)
    monkeypatch.setattr(mediaclf, "MIN_PER_CLASS", 1)
    try:
        # 你標的：馬上生效、記成 manual
        r = client.post("/api/media/mediatype", json={"ids": ids[:2], "label": "photo"}).json()
        assert r["n"] == 2
        assert tuple(c.execute("SELECT media_type, media_src FROM media WHERE id=?", (ids[0],)).fetchone()) == ("photo", "manual")
        assert client.post("/api/media/mediatype", json={"ids": ids[:1], "label": "nope"}).status_code == 400
        client.post("/api/media/mediatype", json={"ids": ids[2:4], "label": "comic"})
        # 篩選：手動的照列；AI 沒把握的只在「未定」
        with db.write_lock:
            c.execute("UPDATE media SET media_type='photo', media_src='model', media_conf=0.3 WHERE id=?", (ids[5],))
            c.commit()
        got = {x["id"] for x in client.get("/api/media", params={"mtype": "photo", "limit": 500, "nsfw": "show"}).json()["items"]}
        assert ids[0] in got and ids[5] not in got
        uns = {x["id"] for x in client.get("/api/media", params={"mtype": "unsure", "limit": 500, "nsfw": "show"}).json()["items"]}
        assert ids[5] in uns and ids[0] not in uns
        q = {x["id"] for x in client.get("/api/media", params={"q": "type:photo", "limit": 500, "nsfw": "show"}).json()["items"]}
        assert ids[0] in q and ids[5] not in q
        # 訓練＋重算：手動的不動，其他的都有信心值
        res = mediaclf.run()
        assert res.get("labels", 0) >= 4, res
        assert tuple(c.execute("SELECT media_type, media_src FROM media WHERE id=?", (ids[0],)).fetchone()) == ("photo", "manual")
        rows = c.execute(f"SELECT media_src, media_conf FROM media WHERE id IN ({','.join(map(str, ids[4:]))})").fetchall()
        assert all(r[0] == "model" and 0 < r[1] <= 1 for r in rows), rows
        assert set(res["classes"]) == {"photo", "comic"}
        ev = client.get("/api/diag/eval-media").json()
        assert ev["labels"] >= 4 and "photo" in ev["classes"] and "confident" in ev
        # 撤銷「標成漫畫」：回到原本
        client.post("/api/undo")
        assert c.execute("SELECT media_src FROM media WHERE id=?", (ids[2],)).fetchone()[0] != "manual"
        assert not c.execute("SELECT 1 FROM media_label WHERE media_id=?", (ids[2],)).fetchone()
        # 改回自動：拿掉你的標註
        client.post("/api/media/mediatype", json={"ids": ids[:1], "label": None})
        assert c.execute("SELECT media_src FROM media WHERE id=?", (ids[0],)).fetchone()[0] is None
        assert not c.execute("SELECT 1 FROM media_label WHERE media_id=?", (ids[0],)).fetchone()
        # 大圖：媒材來源、信心帶出來
        d = client.get(f"/api/media/{ids[1]}").json()
        assert d["media_src"] == "manual" and d["media_type"] == "photo"
    finally:
        if mediaclf._timer:
            mediaclf._timer.cancel()
        with db.write_lock:
            c.execute(f"DELETE FROM media_label WHERE media_id IN ({','.join(map(str, ids))})")
            c.executemany("UPDATE media SET media_type=?, media_src=?, media_conf=? WHERE id=?", [(*v, i) for i, v in prev.items()])
            c.commit()
        from app import piles
        piles.refresh(ids)      # 重算時分堆跟著媒材變了：還原，不影響後面的測試


def test_seed_matches_by_fingerprint(client, monkeypatch, tmp_path):
    """起始標註用 MD5 對圖；你已經標過的不被起始標註蓋掉。"""
    from app import db, mediaclf
    c = db.conn()
    rows = c.execute("SELECT id, fp FROM media WHERE fp IS NOT NULL AND kind='image' LIMIT 2").fetchall()
    assert len(rows) == 2
    (a, fa), (b, fb) = rows
    f = tmp_path / "seed.json"
    f.write_text(json.dumps({fa: "figure", fb: "figure"}), "utf-8")
    monkeypatch.setattr(mediaclf, "SEED_FILE", f)
    try:
        with db.write_lock:
            c.execute("DELETE FROM media_label WHERE media_id IN (?,?)", (a, b))
            c.execute("INSERT INTO media_label(media_id, fp, label, src, at) VALUES(?,?,?,?,0)", (b, fb, "photo", "user"))
            c.commit()
        assert mediaclf.load_seed() == 1
        lab = dict(c.execute("SELECT media_id, label || '/' || src FROM media_label WHERE media_id IN (?,?)", (a, b)).fetchall())
        assert lab == {a: "figure/seed", b: "photo/user"}
    finally:
        with db.write_lock:
            c.execute("DELETE FROM media_label WHERE media_id IN (?,?)", (a, b))
            c.commit()
