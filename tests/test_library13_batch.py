"""待確認 › 整批看（b58）：AI（PixAI／WD）高分、不像梗圖的同名候選一面看完；專注模式不再重複；
一次送出 是／不是／略過，一筆撤銷。"""
from tests.conftest import ids_by_name


def test_batch_review(client):
    from app import db
    c = db.conn()
    ids = sorted(ids_by_name(client).values())[:6]
    tid = db.tag_id("batch_girl_(tst)", "character")
    prev = {i: c.execute("SELECT media_type, unrec_skip FROM media WHERE id=?", (i,)).fetchone() for i in ids}
    with db.write_lock:
        c.execute("DELETE FROM media_tags WHERE tag_id=?", (tid,))
        for i, (sc, src) in zip(ids, [(0.97, "pixai"), (0.95, "pixai"), (0.93, "model"), (0.92, "pixai"), (0.8, "pixai"), (0.99, "pixai")]):
            c.execute("INSERT INTO media_tags(media_id, tag_id, score, source, state, tier, reason) VALUES(?,?,?,?,0,4,'t')", (i, tid, sc, src))
            c.execute("UPDATE media SET media_type='illustration', unrec_skip=0 WHERE id=?", (i,))
        c.execute("UPDATE media SET media_type='photo' WHERE id=?", (ids[5],))        # 照片：不放進整批
        c.execute("DELETE FROM media_tags WHERE media_id IN (%s) AND source='manual' AND tag_id IN (SELECT id FROM tags WHERE name IN ('梗圖','截圖'))" % ",".join(map(str, ids)))
    try:
        g = client.get("/api/review/batch", params={"nsfw": "show", "min": 0.9}).json()
        mine = [x for x in g["groups"] if x["name"] == "batch_girl_(tst)"]
        assert mine and mine[0]["n"] == 4
        items = client.get("/api/review/batch/items", params={"tag": "batch_girl_(tst)", "nsfw": "show", "min": 0.9}).json()["items"]
        assert [it["id"] for it in items] == [ids[3], ids[2], ids[1], ids[0]]            # 分數低的在前
        # 專注模式：整批看那 4 筆不再出現，0.8 和照片那張還在
        foc = client.get("/api/review", params={"nsfw": "show", "tag": "batch_girl_(tst)", "nobatch": 0.9, "limit": 50}).json()["items"]
        assert sorted(it["id"] for it in foc) == sorted([ids[4], ids[5]])
        r = client.post("/api/review/batch/apply", json={"tag": "batch_girl_(tst)", "yes": [ids[0], ids[1]], "no": [ids[2]], "skip": [ids[3]]}).json()
        assert (r["yes"], r["no"], r["skip"]) == (2, 1, 1)
        st = lambda i: (c.execute("SELECT state FROM media_tags WHERE media_id=? AND tag_id=?", (i, tid)).fetchone() or [None])[0]
        assert st(ids[0]) == 1 and st(ids[1]) == 1 and st(ids[2]) == -1 and st(ids[3]) is None
        assert c.execute("SELECT unrec_skip FROM media WHERE id=?", (ids[3],)).fetchone()[0] == 1
        client.post("/api/undo", json={})                                                 # 一筆撤銷全部回來
        assert [st(i) for i in ids[:4]] == [0, 0, 0, 0]
        assert c.execute("SELECT unrec_skip FROM media WHERE id=?", (ids[3],)).fetchone()[0] == 0
    finally:
        with db.write_lock:
            c.execute("DELETE FROM media_tags WHERE tag_id=?", (tid,))
            for i, p in prev.items():
                c.execute("UPDATE media SET media_type=?, unrec_skip=? WHERE id=?", (p[0], p[1], i))
