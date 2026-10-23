"""作品集的圖隔絕（b59）：放進作品集的圖不再自動推角色／作者／作品、不自動搜源；不在未識別的待辦裡；
建立作品集時清掉這些圖上還沒確認的自動候選（可撤銷）。整本的標籤、手動加的照常。"""
from tests.conftest import ids_by_name  # noqa


def _unrec(client):
    return {it["id"] for it in client.get("/api/media", params={"view": "unrecognized", "miss": "artist", "nsfw": "show", "limit": 5000}).json()["items"]}


def test_album_images_are_isolated(client):
    from app import albums, artistscan, ascii2d, candidates, db, online, pixai
    from tests.conftest import LIB, make_image, wait_idle
    c = db.conn()
    for k in range(3):        # 自己放三張新的（前面的測試會刪圖、排除）
        make_image(LIB / "isolate" / f"iso_{k}.jpg", 8300 + k)
    client.post("/api/jobs", json={"kind": "scan"})
    wait_idle(client)
    names = ids_by_name(client)
    ids = [names[f"iso_{k}.jpg"] for k in range(3)]
    ph = ",".join(map(str, ids))
    prev = {i: c.execute("SELECT pile, unrec_skip, artist_done, media_type FROM media WHERE id=?", (i,)).fetchone() for i in ids}
    cand = db.tag_id("iso_girl_(tst)", "character")
    rej = db.tag_id("iso_rejected_(tst)", "character")
    conf = db.tag_id("iso_confirmed_(tst)", "character")
    desc = db.tag_id("iso_hat", "general")
    with db.write_lock:
        c.execute(f"DELETE FROM album_items WHERE media_id IN ({ph})")
        c.execute(f"DELETE FROM media_tags WHERE media_id IN ({ph}) AND tag_id IN (SELECT id FROM tags WHERE category IN ('character','artist','copyright') OR no_artist=1)")
        c.execute(f"UPDATE media SET pile='comic', unrec_skip=0, artist_done=0, media_type='illustration' WHERE id IN ({ph})")
        c.execute(f"DELETE FROM ascii2d WHERE media_id IN ({ph})")
        c.execute(f"DELETE FROM pixai_pred WHERE media_id IN ({ph})")
        c.execute(f"DELETE FROM media_source WHERE media_id IN ({ph})")
        for i in ids:
            c.execute("INSERT INTO media_tags(media_id, tag_id, score, source, state, tier, reason) VALUES(?,?,0.95,'pixai',0,4,'t')", (i, cand))
            c.execute("INSERT INTO media_tags(media_id, tag_id, score, source, state, tier, reason) VALUES(?,?,0.9,'model',0,4,'t')", (i, desc))
        c.execute("INSERT INTO media_tags(media_id, tag_id, score, source, state, tier) VALUES(?,?,0.9,'pixai',-1,4)", (ids[0], rej))
        c.execute("INSERT INTO media_tags(media_id, tag_id, score, source, state, tier) VALUES(?,?,NULL,'manual',1,1)", (ids[1], conf))
    aid = None
    try:
        # 放進作品集前：都在待辦、掃描名單裡
        assert set(ids) <= _unrec(client)
        assert set(ids) <= set(ascii2d.targets(low=True)) and set(ids) <= set(pixai.pending_ids())
        assert set(ids) <= set(online.unsearched_ids())
        assert set(ids) <= set(int(x) for x in artistscan.targets()["ids"])

        aid = client.post("/api/albums", json={"title": "隔絕測試", "ids": ids, "tags": [{"name": "iso_series_(tst)", "category": "copyright"}]}).json()["id"]
        st = lambda i, t: (c.execute("SELECT state, source FROM media_tags WHERE media_id=? AND tag_id=?", (i, t)).fetchone() or [None, None])
        # 自動候選清掉；否決、確認、描述標籤不動；整本的標籤加上
        assert all(st(i, cand)[0] is None for i in ids)
        assert st(ids[0], rej)[0] == -1 and st(ids[1], conf)[0] == 1
        assert all(st(i, desc)[0] == 0 for i in ids)
        series = db.tag_id("iso_series_(tst)", "copyright")
        assert all(tuple(st(i, series)) == (1, "album") for i in ids)

        # 不在待辦、不在任何掃描名單
        assert not set(ids) & _unrec(client)
        assert not set(ids) & set(ascii2d.targets(low=True)) and not set(ids) & set(pixai.pending_ids())
        assert not set(ids) & set(online.unsearched_ids())
        assert not set(ids) & set(int(x) for x in artistscan.targets()["ids"])

        # 自動來源不再推；確認（使用者按的）、手動、整本的照常
        with db.write_lock:
            assert candidates.add(ids[2], "iso_new_(tst)", "character", candidates.T_AI, "pixai", 0.99, "t") is False
            assert candidates.add(ids[2], "iso_new2_(tst)", "artist", candidates.T_DANBOORU, "ehentai", None, "t") is False
        assert st(ids[2], db.tag_id("iso_new_(tst)", "character"))[0] is None

        # 撤銷建立：作品集沒了，候選回來
        client.post("/api/undo", json={})
        assert not c.execute("SELECT 1 FROM albums WHERE id=?", (aid,)).fetchone()
        aid = None
        assert all(st(i, cand)[0] == 0 for i in ids)
        assert set(ids) <= _unrec(client)

    finally:
        with db.write_lock:
            if aid:
                c.execute("DELETE FROM album_items WHERE album_id=?", (aid,))
                c.execute("DELETE FROM album_tags WHERE album_id=?", (aid,))
                c.execute("DELETE FROM albums WHERE id=?", (aid,))
            c.execute(f"DELETE FROM media_tags WHERE media_id IN ({ph}) AND tag_id IN (SELECT id FROM tags WHERE name LIKE 'iso_%')")
            for i, p in prev.items():
                c.execute("UPDATE media SET pile=?, unrec_skip=?, artist_done=?, media_type=? WHERE id=?", (p[0], p[1], p[2], p[3], i))


def test_add_nothing_leaves_no_undo(client):
    """再匯入同一本（頁都已經在作品集裡）：不留「加入 0 張」的撤銷紀錄。"""
    from app import albums, db
    ids = sorted(ids_by_name(client).values())[:2]
    aid = albums.create("空加入測試", ids)
    try:
        last = db.conn().execute("SELECT MAX(id) FROM actions").fetchone()[0]
        assert albums.add_items(aid, ids) == 0
        assert db.conn().execute("SELECT MAX(id) FROM actions").fetchone()[0] == last
    finally:
        albums.delete(aid)


def test_library_hides_album_pages(client):
    """圖庫（noalbum=1）不放作品集裡的頁；作品集頁、角色頁、搜尋照樣看得到。"""
    from app import albums
    ids = sorted(ids_by_name(client).values())[:3]
    aid = albums.create("圖庫隱藏測試", ids)
    try:
        got = lambda **kw: {it["id"] for it in client.get("/api/media", params={"view": "all", "nsfw": "show", "limit": 1000, **kw}).json()["items"]}
        assert set(ids) <= got()
        assert not set(ids) & got(noalbum=1)
        assert set(ids) <= got(noalbum=1, album=aid)
        # 張數對得起來：藏了幾張要說；側欄的圖庫張數也不算作品集的頁
        d = client.get("/api/media", params={"view": "all", "nsfw": "show", "limit": 1, "noalbum": 1}).json()
        d_all = client.get("/api/media", params={"view": "all", "nsfw": "show", "limit": 1}).json()
        assert d["album_hidden"] >= 3 and d["total"] + d["album_hidden"] == d_all["total"]
        sb = lambda **kw: client.get("/api/sidebar", params={"nsfw": "show", **kw}).json()["counts"]["all"]
        assert sb(noalbum=1) == d["total"] and sb() == d_all["total"]
        # 角色頁：整本標的角色不會把角色頁洗版
        client.post(f"/api/albums/{aid}/tag", json={"name": "alb_hide_char_(tst)", "category": "character", "on": True})
        tag = lambda **kw: {it["id"] for it in client.get("/api/media", params={"view": "all", "tag": "alb_hide_char_(tst)", "nsfw": "show", **kw}).json()["items"]}
        assert set(ids) <= tag() and not set(ids) & tag(noalbum=1)
        # 角色的張數也一樣：角色頁上方、角色書架、創作者／角色列表都不算作品集的頁
        ti = lambda **kw: client.get("/api/tag/alb_hide_char_(tst)", params={"nsfw": "show", **kw}).json()["confirmed"] or 0
        assert ti() == len(ids) and ti(noalbum=1) == 0
        shelf = lambda **kw: {ch["name"]: ch["n"] for ch in client.get("/api/shelf", params={"nsfw": "show", **kw}).json()["chars"]}
        assert shelf().get("alb_hide_char_(tst)") == len(ids) and shelf(noalbum=1).get("alb_hide_char_(tst)", 0) == 0
        lst = lambda **kw: {x["name"]: x["n"] for x in client.get("/api/characters", params={"nsfw": "show", **kw}).json()}
        assert lst().get("alb_hide_char_(tst)") == len(ids) and lst(noalbum=1).get("alb_hide_char_(tst)", 0) == 0
    finally:
        albums.delete(aid)
