"""第二次審查（2026-10）修正：撤銷要撤到對的那一個、候選全部確認不掃梗圖、拿掉梗圖不把你略過的拉回來。"""
import json


def _mids(client, n=3):
    from app import db
    c = db.conn()
    return [r[0] for r in c.execute("SELECT id FROM media WHERE status='done' AND kind='image' ORDER BY id LIMIT ?", (n,))]


def _undo(client):
    return client.post("/api/undo", json={}).json()["undone"]


def test_people_done_is_undoable(client):
    """「標完了」以前沒有撤銷紀錄：按完再 Ctrl+Z，撤銷的是更早那個不相干的動作。"""
    from app import candidates, charknn, db
    # 背景的「你的圖庫」重算會收掉這裡手動放的推薦：先關掉（跟 test_review_live_knn_update 一樣）
    client.post("/api/settings", json={"knn_enabled": False})
    if charknn._timer is not None:
        charknn._timer.cancel()
    a, b = _mids(client, 2)
    c = db.conn()
    with db.write_lock:
        c.execute("UPDATE media SET people=3, people_done=0, unrec_skip=0, excluded=0 WHERE id=?", (a,))   # 前面的測試可能略過了這張（略過的圖不收候選）
        c.execute("INSERT OR REPLACE INTO media_tags(media_id, tag_id, score, source, state, tier, reason) VALUES(?,?,0.5,'knn',0,4,'另一個人（參考）')",
                  (a, db.tag_id("pd_other_(tst)", "character")))
    assert c.execute("SELECT 1 FROM media_tags WHERE media_id=? AND tag_id=?", (a, db.tag_id("pd_other_(tst)", create=False))).fetchone()
    client.post("/api/tags/apply", json={"ids": [b], "action": "add", "tag": "pd_marker_(tst)", "category": "custom"})
    assert client.post("/api/media/people-done", json={"ids": [a]}).json()["ok"]
    assert c.execute("SELECT people_done FROM media WHERE id=?", (a,)).fetchone()[0] == 1
    tid = db.tag_id("pd_other_(tst)", create=False)
    assert not c.execute("SELECT 1 FROM media_tags WHERE media_id=? AND tag_id=?", (a, tid)).fetchone()
    assert "標完了" in _undo(client)
    assert c.execute("SELECT people_done FROM media WHERE id=?", (a,)).fetchone()[0] == 0
    assert c.execute("SELECT state FROM media_tags WHERE media_id=? AND tag_id=?", (a, tid)).fetchone()[0] == 0, "猜的另一個人放回來"
    mk = db.tag_id("pd_marker_(tst)", create=False)
    assert c.execute("SELECT state FROM media_tags WHERE media_id=? AND tag_id=?", (b, mk)).fetchone()[0] == 1, "前一個動作沒被撤掉"
    _undo(client)
    with db.write_lock:
        c.execute("UPDATE media SET people=0 WHERE id=?", (a,))
        c.execute("DELETE FROM media_tags WHERE tag_id=?", (tid,))
    client.post("/api/settings", json={"knn_enabled": True})


def test_upgrade_dismiss_is_undoable(client):
    from app import db
    a = _mids(client, 1)[0]
    c = db.conn()
    with db.write_lock:
        c.execute("INSERT OR REPLACE INTO upgrade(media_id, status, site, width, height) VALUES(?, 'offer', 'danbooru', 4000, 4000)", (a,))
    assert client.post("/api/upgrade/dismiss", json={"ids": [a]}).json()["dismissed"] == 1
    assert c.execute("SELECT status FROM upgrade WHERE media_id=?", (a,)).fetchone()[0] == "dismissed"
    assert "不用換高清" in _undo(client)
    assert c.execute("SELECT status FROM upgrade WHERE media_id=?", (a,)).fetchone()[0] == "offer"
    with db.write_lock:
        c.execute("DELETE FROM upgrade WHERE media_id=?", (a,))


def test_confirm_all_leaves_memes(client):
    """角色頁「候選全部確認」：梗圖／截圖、你略過的、排除的不確認（以前全部一起確認）。"""
    from app import candidates, db
    ids = _mids(client, 4)
    illu, meme, skipped, excl = ids
    c = db.conn()
    with db.write_lock:
        for m in ids:     # 直接寫（candidates.add 會因為前面測試留下的搜源結果、略過而不收）
            c.execute("INSERT OR REPLACE INTO media_tags(media_id, tag_id, score, source, state, tier) VALUES(?,?,0.95,'model',0,4)",
                      (m, db.tag_id("ca_char_(tst)", "character")))
        c.execute("DELETE FROM media_tags WHERE media_id IN (?,?,?,?) AND tag_id IN (SELECT id FROM tags WHERE name IN ('chat_log','fake_screenshot','chinese_text','parody','meme','user_interface','screenshot','text_focus','emoji','subtitled'))", ids)
        c.execute("UPDATE media SET unrec_skip=0, excluded=0, media_type='illustration' WHERE id IN (?,?,?,?)", ids)
        c.execute("INSERT OR REPLACE INTO media_tags(media_id, tag_id, score, source, state) VALUES(?,?,0.9,'model',0)",
                  (meme, db.tag_id("chat_log", "general")))
        c.execute("UPDATE media SET unrec_skip=1 WHERE id=?", (skipped,))
        c.execute("UPDATE media SET excluded=1 WHERE id=?", (excl,))
    tid = db.tag_id("ca_char_(tst)", create=False)
    others = c.execute("SELECT COUNT(*) FROM media_tags WHERE tag_id=? AND state=0", (tid,)).fetchone()[0]
    d = client.post("/api/tags/confirm-all", json={"tag": "ca_char_(tst)", "nsfw": "show", "dry_run": True}).json()
    assert d["n"] == 1 and d["left"] == others - 1, d
    assert c.execute("SELECT state FROM media_tags WHERE media_id=? AND tag_id=?", (illu, tid)).fetchone()[0] == 0, "dry_run 不改"
    r = client.post("/api/tags/confirm-all", json={"tag": "ca_char_(tst)", "nsfw": "show"}).json()
    assert r["n"] == 1
    st = dict(c.execute("SELECT media_id, state FROM media_tags WHERE tag_id=?", (tid,)).fetchall())
    assert st[illu] == 1 and st[meme] == 0 and st[skipped] == 0 and st[excl] == 0, st
    _undo(client)
    assert c.execute("SELECT state FROM media_tags WHERE media_id=? AND tag_id=?", (illu, tid)).fetchone()[0] == 0
    with db.write_lock:
        c.execute("DELETE FROM media_tags WHERE tag_id IN (?, ?)", (tid, db.tag_id("chat_log", create=False)))
        c.execute("UPDATE media SET unrec_skip=0, excluded=0 WHERE id IN (?,?,?,?)", ids)


def test_removing_meme_keeps_your_own_skip(client):
    """拿掉「梗圖」：你自己按過 L 略過的留在已略過；因為梗圖才略過的回到未識別。"""
    from app import db
    mine, by_tag = _mids(client, 2)
    c = db.conn()
    with db.write_lock:
        c.execute("UPDATE media SET unrec_skip=0 WHERE id IN (?,?)", (mine, by_tag))
    client.post("/api/media/skip", json={"ids": [mine], "value": True})
    client.post("/api/quicktag", json={"ids": [mine, by_tag], "name": "梗圖"})
    sk = lambda m: c.execute("SELECT unrec_skip FROM media WHERE id=?", (m,)).fetchone()[0]
    assert sk(mine) == 1 and sk(by_tag) == 2
    client.post("/api/quicktag", json={"ids": [mine, by_tag], "name": "梗圖"})     # 拿掉
    assert sk(mine) == 1, "你自己略過的不要拉回未識別"
    assert sk(by_tag) == 0
    with db.write_lock:
        c.execute("UPDATE media SET unrec_skip=0 WHERE id IN (?,?)", (mine, by_tag))


def test_skipped_bucket_includes_tag_skips(client):
    """unrec_skip=2（因為梗圖才略過）也要出現在「已略過」。"""
    import inspect
    from app import query
    assert "{SKIP}=1" not in inspect.getsource(query) and "{SKIP}>0" in inspect.getsource(query)


def test_migration_v8_backfill():
    import sqlite3
    from app import db
    c = sqlite3.connect(":memory:")
    c.executescript("""CREATE TABLE settings(key TEXT PRIMARY KEY, value TEXT);
        CREATE TABLE tags(id INTEGER PRIMARY KEY, name TEXT); CREATE TABLE media(id INTEGER PRIMARY KEY, unrec_skip INT);
        CREATE TABLE media_tags(media_id INT, tag_id INT, state INT);
        INSERT INTO tags VALUES(1,'梗圖'),(2,'other');
        INSERT INTO media VALUES(1,1),(2,1),(3,0);
        INSERT INTO media_tags VALUES(1,1,1),(2,2,1),(3,1,1);""")
    db._m8_skip_by_tag(c)
    assert dict(c.execute("SELECT id, unrec_skip FROM media")) == {1: 2, 2: 1, 3: 0}


def test_confirming_variant_confirms_base_candidate(client):
    """確認造型時，同一張圖上的本體候選一起確認（以前要按兩次）；撤銷一次兩個都回到候選。反過來不做。"""
    from app import db, people
    a, b = _mids(client, 2)
    c = db.conn()
    base, var = "vb_koyori_(tst)", "vb_koyori_(1st_costume)_(tst)"
    with db.write_lock:
        for m in (a, b):
            for t in (base, var):
                c.execute("INSERT OR REPLACE INTO media_tags(media_id, tag_id, score, source, state, tier) VALUES(?,?,0.9,'model',0,4)",
                          (m, db.tag_id(t, "character")))
    people.refresh_bases()
    tb, tv = db.tag_id(base, create=False), db.tag_id(var, create=False)
    assert c.execute("SELECT base_id FROM char_base WHERE tag_id=?", (tv,)).fetchone()[0] == tb
    det = client.get(f"/api/media/{a}").json()
    assert next(t for t in det["tags"] if t["name"] == var)["base"] == base
    client.post("/api/tags/apply", json={"ids": [a], "action": "confirm", "tag": var})
    st = lambda m, t: c.execute("SELECT state FROM media_tags WHERE media_id=? AND tag_id=?", (m, t)).fetchone()[0]
    assert st(a, tv) == 1 and st(a, tb) == 1
    _undo(client)
    assert st(a, tv) == 0 and st(a, tb) == 0
    client.post("/api/tags/apply", json={"ids": [b], "action": "confirm", "tag": base})
    assert st(b, tb) == 1 and st(b, tv) == 0, "確認本體不會連造型一起確認"
    _undo(client)
    with db.write_lock:
        c.execute("DELETE FROM media_tags WHERE tag_id IN (?,?)", (tb, tv))


def test_true_image_size_not_draft(tmp_path):
    """大 JPEG：load_image 會縮小解碼（draft），image_size 要回傳真正的尺寸（以前把縮小後的存進資料庫）。"""
    from PIL import Image
    from app import media
    p = tmp_path / "big.jpg"
    Image.new("RGB", (3000, 4200), (200, 100, 50)).save(p, quality=80)
    assert media.load_image(p).size != (3000, 4200), "前提：load_image 會縮小"
    assert media.image_size(p) == (3000, 4200)


def test_random_sort_pages_do_not_repeat(client):
    """隨機排序：同一個種子每一頁接得起來（以前 RANDOM() 每一頁重新洗牌，往下捲會重複、漏圖）。"""
    tot = client.get("/api/media", params={"view": "all", "nsfw": "show", "limit": 1}).json()["total"]
    ids = []
    for off in range(0, tot, 7):
        ids += [i["id"] for i in client.get("/api/media", params={"view": "all", "nsfw": "show", "sort": "random", "seed": 12345, "limit": 7, "offset": off}).json()["items"]]
    assert len(ids) == tot == len(set(ids))
    other = [i["id"] for i in client.get("/api/media", params={"view": "all", "nsfw": "show", "sort": "random", "seed": 999, "limit": tot}).json()["items"]]
    assert other != ids and set(other) == set(ids), "換種子＝換順序"


def test_until_hides_images_added_later(client):
    from app import db
    c = db.conn()
    mid, t = c.execute("SELECT id, added_at FROM media WHERE status='done' ORDER BY added_at DESC LIMIT 1").fetchone()
    got = [i["id"] for i in client.get("/api/media", params={"view": "all", "nsfw": "show", "limit": 1000, "until": t - 0.001}).json()["items"]]
    assert mid not in got


def test_errlog_ignores_windows_connection_reset():
    import logging
    from app import errlog
    errlog.install()
    before = errlog.recent(1)
    try:
        raise ConnectionResetError(10054, "遠端主機已強制關閉一個現存的連線。")
    except ConnectionResetError:
        logging.getLogger("asyncio").error("Exception in callback _ProactorBasePipeTransport._call_connection_lost(None)", exc_info=True)
    assert errlog.recent(1) == before, "這種連線被關掉的訊息不算程式出錯"


def test_pixiv_hashed_filename_and_import_url(client):
    """Pixiv 新作品的原圖檔名有雜湊（146228286-<md5>_p0.jpg）：要認得出作品 ID；
    從 Pixiv 匯入的圖就算檔名認不出，也用匯入時的貼文網址（以前顯示「Danbooru／IQDB 沒有這張」、什麼資料都沒抓）。"""
    import os
    import time
    from app import db, importer, online, pixiv, upgrade
    assert pixiv.pixiv_id_from_name("146228286-5554454f6e3725bbf3a85840a7ab75b4_p0.jpg") == 146228286
    assert pixiv.pixiv_id_from_url("https://i.pximg.net/img-original/img/2026/10/01/00/00/00/146228286-5554454f6e3725bbf3a85840a7ab75b4_p3.jpg") == 146228286
    assert upgrade._PAGE.search("146228286-5554454f6e3725bbf3a85840a7ab75b4_p3").group(2) == "3"
    importer._ensure()
    c = db.conn()
    mid, path = c.execute("SELECT id, path FROM media WHERE status='done' AND kind='image' ORDER BY id DESC LIMIT 1").fetchone()
    assert not pixiv.pixiv_id_from_name(os.path.basename(path))
    with db.write_lock:
        c.execute("INSERT OR REPLACE INTO import_meta(path, site, post_url, data, created_at, applied) VALUES(?, 'pixiv', 'https://www.pixiv.net/artworks/55556666', '{}', ?, 1)",
                  (os.path.normpath(path), time.time()))
    try:
        res = online.lookup_source(mid, quick="test")
        assert res["pixiv_id"] == 55556666, res
    finally:
        with db.write_lock:
            c.execute("DELETE FROM import_meta WHERE path=?", (os.path.normpath(path),))


def test_meme_screenshot_tags_stop_character_checks(client):
    """標了梗圖、截圖（no_char_tags）：還沒決定的候選拿掉（可撤銷）、之後不再收候選、不自動以圖搜圖；已確認的不動。"""
    from app import candidates, db, online, quicktags
    c = db.conn()
    a = c.execute("SELECT id FROM media WHERE status='done' AND kind='image' ORDER BY id LIMIT 1 OFFSET 5").fetchone()[0]
    with db.write_lock:
        c.execute("UPDATE media SET unrec_skip=0 WHERE id=?", (a,))
        for t, st in (("nc_cand_(tst)", 0), ("nc_kept_(tst)", 1)):
            c.execute("INSERT OR REPLACE INTO media_tags(media_id, tag_id, score, source, state, tier) VALUES(?,?,0.9,'knn',?,4)",
                      (a, db.tag_id(t, "character"), st))
        c.execute("DELETE FROM media_tags WHERE media_id=? AND tag_id IN (SELECT id FROM tags WHERE name IN ('梗圖','截圖'))", (a,))
    st = lambda t: (c.execute("SELECT state FROM media_tags WHERE media_id=? AND tag_id=?", (a, db.tag_id(t, create=False))).fetchone() or [None])[0]
    r = client.post("/api/quicktag", json={"ids": [a], "name": "梗圖", "on": True}).json()
    assert r["no_char"] and r["cleared"] >= 1
    assert st("nc_cand_(tst)") is None and st("nc_kept_(tst)") == 1, "候選拿掉、確認的不動"
    with db.write_lock:
        assert not candidates.add(a, "nc_new_(tst)", "character", candidates.T_DANBOORU, "danbooru")
        assert candidates.add(a, "nc_sure_(tst)", "character", candidates.T_DANBOORU, "danbooru", confirm=True), "確定的（MD5）還是可以確認"
    assert online.should_skip(a)
    assert "梗圖" in client.post("/api/undo", json={}).json()["undone"]
    assert st("nc_cand_(tst)") == 0, "撤銷：候選放回來"
    # 一般「加上」標籤（不是快速標籤按鈕）也一樣
    client.post("/api/tags/apply", json={"ids": [a], "action": "add", "tag": "截圖", "category": "custom"})
    assert st("nc_cand_(tst)") is None
    client.post("/api/undo", json={})
    with db.write_lock:
        c.execute("DELETE FROM media_tags WHERE media_id=? AND tag_id IN (SELECT id FROM tags WHERE name LIKE 'nc\\_%' ESCAPE '\\' OR name IN ('梗圖','截圖'))", (a,))
        c.execute("UPDATE media SET unrec_skip=0 WHERE id=?", (a,))


def test_sort_by_recently_edited_and_reverse(client):
    """排序「最近操作」：你剛動過的在最前面（否決傳播連帶改到的不算），沒動過的在最後；rev=1 倒過來（沒動過的還是在最後）。"""
    from app import db, query
    c = db.conn()
    a, b = [r[0] for r in c.execute("SELECT id FROM media WHERE status='done' ORDER BY id LIMIT 2 OFFSET 10")]
    with db.write_lock:
        c.execute("UPDATE media SET edited_at=NULL")
    client.post("/api/tags/apply", json={"ids": [a], "action": "add", "tag": "ed_one_(tst)", "category": "custom"})
    import time
    time.sleep(0.01)
    client.post("/api/tags/apply", json={"ids": [b], "action": "add", "tag": "ed_two_(tst)", "category": "custom"})
    got = [i["id"] for i in client.get("/api/media", params={"view": "all", "nsfw": "show", "sort": "edited", "limit": 1000}).json()["items"]]
    assert got[:2] == [b, a], got[:3]
    rev = [i["id"] for i in client.get("/api/media", params={"view": "all", "nsfw": "show", "sort": "edited", "rev": 1, "limit": 1000}).json()["items"]]
    assert rev == list(reversed(got)), "倒序＝整個倒過來：沒動過的在前、動過的在最後（最早動的先）"
    assert rev[-2:] == [a, b]
    new = [i["id"] for i in client.get("/api/media", params={"view": "all", "nsfw": "show", "sort": "new", "limit": 1000}).json()["items"]]
    old = [i["id"] for i in client.get("/api/media", params={"view": "all", "nsfw": "show", "sort": "new", "rev": 1, "limit": 1000}).json()["items"]]
    assert old == list(reversed(new))
    assert query.order_by("(COALESCE(m.width,0) * COALESCE(m.height,0)) DESC, m.id", True) == "(COALESCE(m.width,0) * COALESCE(m.height,0)) ASC, m.id DESC"
    client.post("/api/undo", json={}); client.post("/api/undo", json={})


def test_migration_v9_edited_at():
    import json as _j
    import sqlite3
    from app import db
    c = sqlite3.connect(":memory:")
    c.executescript("CREATE TABLE media(id INTEGER PRIMARY KEY); CREATE TABLE actions(id INTEGER PRIMARY KEY, ts REAL, label TEXT, payload TEXT); INSERT INTO media VALUES(1),(2);")
    c.execute("INSERT INTO actions(ts, label, payload) VALUES(5, 'x', ?)", (_j.dumps({"skip": [[2, 0]]}),))
    db._m9_edited_at(c)
    assert dict(c.execute("SELECT id, edited_at FROM media")) == {1: None, 2: 5}


def test_reverse_is_exact_reverse_for_every_sort(client):
    """每個排序的倒序都剛好是正序整個反過來（不會有一群固定在前面）。"""
    from app import db
    with db.write_lock:
        db.conn().execute("UPDATE media SET edited_at=NULL")
        db.conn().execute("UPDATE media SET edited_at=id WHERE id % 3 = 0")
    for sort in ("new", "old", "edited", "taken", "size", "path"):
        q = {"view": "all", "nsfw": "show", "sort": sort, "limit": 1000}
        fwd = [i["id"] for i in client.get("/api/media", params=q).json()["items"]]
        rev = [i["id"] for i in client.get("/api/media", params={**q, "rev": 1}).json()["items"]]
        assert rev == list(reversed(fwd)), sort
    tag = client.get("/api/tags", params={"limit": 1, "category": "general"}).json()
    if tag:
        for sort in ("suspicious", "confident"):
            q = {"view": "all", "nsfw": "show", "sort": sort, "limit": 1000, "tag": tag[0]["name"]}
            fwd = [i["id"] for i in client.get("/api/media", params=q).json()["items"]]
            rev = [i["id"] for i in client.get("/api/media", params={**q, "rev": 1}).json()["items"]]
            assert rev == list(reversed(fwd)), sort


def _fake_work(c, db, mids, pid=77700001):
    with db.write_lock:
        for m in mids:
            c.execute("INSERT OR REPLACE INTO media_source(media_id, status, method, pixiv_id, data, fetched_at) VALUES(?, 'found', 'pixiv', ?, '{}', 0)", (m, pid))
            for t in ("wk_a_(tst)", "wk_b_(tst)"):
                c.execute("INSERT OR REPLACE INTO media_tags(media_id, tag_id, source, state) VALUES(?,?,'pixiv',1)", (m, db.tag_id(t, "character")))
    return pid


def test_pixiv_work_page_matrix(client):
    """整篇 Pixiv 作品逐頁標角色：列出每一頁、每個角色的狀態；一次存、一次撤銷；不做否決傳播。"""
    from app import db
    c = db.conn()
    mids = [r[0] for r in c.execute("SELECT id FROM media WHERE status='done' AND kind='image' ORDER BY id DESC LIMIT 3")]
    olds = {m: c.execute("SELECT * FROM media_source WHERE media_id=?", (m,)).fetchone() for m in mids}
    pid = _fake_work(c, db, mids)
    try:
        d = client.get(f"/api/pixiv-work/{mids[0]}").json()
        assert d["pixiv_id"] == pid and {p["id"] for p in d["pages"]} == set(mids)
        assert {c_["name"] for c_ in d["chars"]} >= {"wk_a_(tst)", "wk_b_(tst)"}
        assert client.get(f"/api/media/{mids[0]}").json()["pixiv_work"]["pages"] == 3
        ch = [[mids[0], "wk_b_(tst)", -1], [mids[1], "wk_a_(tst)", -1], [mids[2], "wk_new_(tst)", 1]]
        assert client.post("/api/pixiv-work/apply", json={"changes": ch}).json()["n"] == 3
        st = lambda m, t: (c.execute("SELECT state FROM media_tags WHERE media_id=? AND tag_id=?", (m, db.tag_id(t, create=False))).fetchone() or [None])[0]
        assert st(mids[0], "wk_b_(tst)") == -1 and st(mids[0], "wk_a_(tst)") == 1 and st(mids[1], "wk_a_(tst)") == -1 and st(mids[2], "wk_new_(tst)") == 1
        assert "逐頁" in client.post("/api/undo", json={}).json()["undone"]
        assert st(mids[0], "wk_b_(tst)") == 1 and st(mids[2], "wk_new_(tst)") is None
        assert client.post("/api/pixiv-work/apply", json={"changes": [[mids[0], "x", 5]]}).status_code == 400
    finally:
        with db.write_lock:
            c.execute("DELETE FROM media_tags WHERE tag_id IN (SELECT id FROM tags WHERE name LIKE 'wk\\_%' ESCAPE '\\')")
            for m, r in olds.items():
                c.execute("DELETE FROM media_source WHERE media_id=?", (m,))
                if r:
                    c.execute(f"INSERT INTO media_source({','.join(r.keys())}) VALUES({','.join('?' * len(r.keys()))})", tuple(r))


def test_pixiv_import_pick_pages(client):
    """Pixiv 多頁作品可以只存其中幾頁：API 帶 pages（0 起算），或貼某一頁的圖片網址（i.pximg.net，含新式雜湊檔名）。"""
    import os
    from tests.conftest import wait_idle
    from app import importer
    p = importer.parse("https://i.pximg.net/img-original/img/2026/10/01/00/00/00/146228286-5554454f6e3725bbf3a85840a7ab75b4_p3.jpg")
    assert p["site"] == "pixiv" and p["id"] == "146228286" and p["pages"] == [3] and p["url"].endswith("/artworks/146228286")
    assert importer.parse("https://i.pximg.net/c/250x250_80_a2/img-master/img/2026/10/01/00/00/00/55557777_p1_square1200.jpg")["pages"] == [1]
    assert "pages" not in importer.parse("https://www.pixiv.net/artworks/55557777")
    client.post("/api/settings", json={"online_enabled": True})
    try:
        r = client.post("/api/fetch-url", json={"url": "https://www.pixiv.net/artworks/55557777", "pages": [1]}).json()
        assert r["ok"] and [os.path.basename(x) for x in r["saved"]] == ["55557777_p1.png"], r
        r = client.post("/api/fetch-url", json={"url": "https://i.pximg.net/img-original/img/2026/01/01/00/00/00/55557777-" + "ab" * 16 + "_p2.png"}).json()
        assert r["ok"] and [os.path.basename(x) for x in r["saved"]] == ["55557777_p2.png"], r
        r = client.post("/api/fetch-url", json={"url": "https://www.pixiv.net/artworks/55557777", "pages": [9]}).json()
        assert not r["ok"] and "只有 3 頁" in r["error"], r
        r = client.post("/api/fetch-url", json={"url": "https://x.com/someart/status/1790000000000000001", "pages": [0]}).json()
        assert not r["ok"] and "只有 Pixiv" in r["error"], r
        # 之後整篇存：只補上還沒有的那一頁
        r = client.post("/api/fetch-url", json={"url": "https://www.pixiv.net/artworks/55557777"}).json()
        assert r["ok"] and [os.path.basename(x) for x in r["saved"]] == ["55557777_p0.png"] and len(r["existing"]) == 2, r
    finally:
        client.post("/api/settings", json={"online_enabled": False})
        wait_idle(client)
    js = client.get("/api/fetch-url/userscript.user.js").text
    assert "@version      3.2" in js and "pickPages(" in js and "存這張" in js


def test_merge_artist_two_identities(client):
    """同一個作者在 Danbooru 有兩個不互通的身分：合併到其中一個後，兩邊的連結都留在資料卡上，
    之後從另一個身分來的候選（Danbooru 回舊名）自動落在合併後的標籤；可以撤銷。"""
    import json as _j
    from app import candidates, db
    c = db.conn()
    mid = c.execute("SELECT id FROM media WHERE status='done' AND kind='image' ORDER BY id DESC LIMIT 1 OFFSET 2").fetchone()[0]
    with db.write_lock:
        a, b = db.tag_id("twoid_main_(tst)", "artist"), db.tag_id("twoid_alt_(tst)", "artist")
        c.execute("INSERT OR REPLACE INTO tag_info(tag_id, status, other_names, links) VALUES(?, 'ok', '[]', ?)", (a, _j.dumps(["https://x.com/main_acc"])))
        c.execute("INSERT OR REPLACE INTO tag_info(tag_id, status, other_names, links) VALUES(?, 'ok', '[\"別名B\"]', ?)", (b, _j.dumps(["https://x.com/alt_acc"])))
        c.execute("INSERT OR REPLACE INTO media_tags(media_id, tag_id, score, source, state) VALUES(?,?,NULL,'manual',1)", (mid, b))
        c.commit()
    assert client.post("/api/tag/twoid_alt_(tst)/merge", json={"into": "twoid_main_(tst)"}).json()["ok"]
    info = c.execute("SELECT links, other_names FROM tag_info WHERE tag_id=?", (a,)).fetchone()
    assert set(_j.loads(info["links"])) == {"https://x.com/main_acc", "https://x.com/alt_acc"}
    assert {"twoid_alt_(tst)", "別名B"} <= set(_j.loads(info["other_names"]))
    assert db.tag_id("twoid_alt_(tst)", create=False) == a
    assert c.execute("SELECT state FROM media_tags WHERE media_id=? AND tag_id=?", (mid, a)).fetchone()[0] == 1
    other = c.execute("SELECT id FROM media WHERE status='done' AND kind='image' ORDER BY id DESC LIMIT 1 OFFSET 3").fetchone()[0]
    with db.write_lock:
        c.execute("DELETE FROM media_tags WHERE media_id=? AND tag_id=?", (other, a))
        candidates.add(other, "twoid_alt_(tst)", "artist", candidates.T_DANBOORU, "danbooru", reason="test")
        c.commit()
    assert c.execute("SELECT 1 FROM media_tags WHERE media_id=? AND tag_id=?", (other, a)).fetchone(), "舊身分的候選要落在合併後的標籤"
    with db.write_lock:
        c.execute("DELETE FROM media_tags WHERE media_id=? AND tag_id=?", (other, a))
        c.commit()
    client.post("/api/undo")
    assert db.tag_id("twoid_alt_(tst)", create=False) != a
    assert _j.loads(c.execute("SELECT links FROM tag_info WHERE tag_id=?", (a,)).fetchone()[0]) == ["https://x.com/main_acc"]


def test_gallery_import_ehentai_nhentai(client):
    """整本匯入：E-Hentai（伺服器翻縮圖頁、一頁一頁抓）、nhentai（腳本送來的頁或程式自己問）。
    收在自己的小資料夾、背景下載、全部標記完自動建立作品集，網站標籤對到的變成整本的標籤。"""
    import os
    import time
    from app import db, galleries, importer
    from tests.conftest import wait_idle
    assert importer.parse("https://exhentai.org/g/123/abcdef0123/")["url"] == "https://e-hentai.org/g/123/abcdef0123/"
    assert importer.parse("https://nhentai.net/g/4567/2/")["url"] == "https://nhentai.net/g/4567/"
    client.post("/api/settings", json={"online_enabled": True})

    def wait_job(url):
        for _ in range(120):
            j = next((x for x in client.get("/api/fetch-url/galleries").json()["jobs"] if x["url"] == url), None)
            if j and j["state"] not in ("queued", "running"):
                return j
            time.sleep(0.25)
        raise AssertionError("背景下載沒有結束")

    def album_of(url):
        c = db.conn()
        for _ in range(120):
            a = c.execute("SELECT id, title, kind, folder FROM albums WHERE source_url=?", (url,)).fetchone()
            if a:
                return a
            time.sleep(0.25)
        raise AssertionError("沒有建立作品集")

    try:
        r = client.post("/api/fetch-url", json={"url": "https://e-hentai.org/g/777001/abcdef0123/"}).json()
        assert r["ok"] and r["queued"] and r["count"] == 3, r
        j = wait_job(r["post_url"])
        assert j["state"] == "done" and j["done"] == 3, j
        files = sorted(os.listdir(j["folder"]))
        assert files == ["001.webp", "002.webp", "003.webp"] and j["folder"].endswith("[eh777001]"), files
        wait_idle(client)
        a = album_of(r["post_url"])
        assert a["title"] == "テスト本" and a["kind"] == "doujin" and a["folder"] == j["folder"]
        c = db.conn()
        tags = {row[0]: row[1] for row in c.execute(
            "SELECT t.name, t.category FROM album_tags x JOIN tags t ON t.id=x.tag_id WHERE x.album_id=?", (a["id"],))}
        assert tags.get("vocaloid") == "copyright" and tags.get("hatsune_miku") == "character" and tags.get("some_artist") == "artist", tags
        assert tags.get("circle_nameless") == "artist", "Danbooru 沒有的社團：用網站上的名字建立創作者"
        assert len(c.execute("SELECT 1 FROM album_items WHERE album_id=?", (a["id"],)).fetchall()) == 3
        # 再匯入一次：已經有的頁不重抓
        r2 = client.post("/api/fetch-url", json={"url": "https://e-hentai.org/g/777001/abcdef0123/"}).json()
        assert wait_job(r2["post_url"])["skipped"] == 3
        # 刪掉的本／錯的網址
        r3 = client.post("/api/fetch-url", json={"url": "https://e-hentai.org/g/404404/abcdef0123/"}).json()
        assert not r3["ok"] and "刪除" in r3["error"]
        # nhentai：程式自己問
        r4 = client.post("/api/fetch-url", json={"url": "https://nhentai.net/g/888001/"}).json()
        assert r4["ok"] and r4["queued"] and r4["count"] == 2, r4
        j4 = wait_job(r4["post_url"])
        assert sorted(os.listdir(j4["folder"])) == ["001.jpg", "002.jpg"] and j4["folder"].endswith("[nh888001]")
        # 被 Cloudflare 擋 → 請用瀏覽器按鈕
        r5 = client.post("/api/fetch-url", json={"url": "https://nhentai.net/g/403403/"}).json()
        assert not r5["ok"] and "瀏覽器按鈕" in r5["error"]
        # 腳本送來的頁只收 nhentai 的圖片伺服器
        from app.importer import import_key
        bad = {"info": {"id": 1, "title": {"english": "x"}}, "pages": ["http://127.0.0.1:7860/api/x.jpg"]}
        r6 = client.post("/api/fetch-url/ext", json={"url": "https://nhentai.net/g/888002/", "nhentai": bad},
                         headers={"X-Import-Key": import_key()}).json()
        assert not r6["ok"] and "沒有送來" in r6["error"]
        wait_idle(client)
        album_of(r4["post_url"])
    finally:
        client.post("/api/settings", json={"online_enabled": False})   # 不然背景搜源會一直跑，干擾後面的測試
        wait_idle(client)
    js = client.get("/api/fetch-url/userscript.user.js").text
    assert "nhSave(" in js and "e-hentai.org/*" in js


def test_multi_file_post_goes_to_subfolder(client):
    """一篇有好幾個檔案（X 兩張圖）：收在這篇自己的小資料夾；單張的照舊放在網站資料夾。"""
    import os
    from app import importer
    client.post("/api/settings", json={"online_enabled": True})
    r = client.post("/api/fetch-url", json={"url": "https://x.com/someart/status/1790000000000000001"}).json()
    paths = r["saved"] or [importer.db.conn().execute("SELECT path FROM media WHERE id=?", (i,)).fetchone()[0] for i in r["existing"] if isinstance(i, int)]
    assert r["ok"]
    r2 = client.post("/api/fetch-url", json={"url": "https://www.pixiv.net/artworks/55556666"}).json()
    assert r2["ok"]
    assert importer.post_folder({"site": "x", "id": "1790000000000000001"}, {"handle": "someart"}) == "1790000000000000001 @someart"
    assert importer.post_folder({"site": "pixiv", "id": "55557777"}, {"title": "t3"}) == "55557777 t3"
    single = (r2["saved"] or [None])[0]
    client.post("/api/settings", json={"online_enabled": False})
    if single:
        assert os.path.basename(os.path.dirname(single)) == "pixiv"
