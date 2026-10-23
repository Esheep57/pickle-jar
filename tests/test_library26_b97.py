"""b97：拿掉圖庫資料夾時清掉那裡「找不到檔案」的紀錄；瀏覽器腳本送來的匯入不打斷讀取快取。"""
import shutil
import time

from tests.conftest import LIB, TMP, ids_by_name, wait_idle


def test_remove_root_forgets_missing(client):
    from app import db
    extra = TMP / "extra_root"
    extra.mkdir(exist_ok=True)
    src = next(p for p in LIB.iterdir() if p.suffix.lower() in (".jpg", ".png"))
    shutil.copy(src, extra / "gone_one.jpg")
    shutil.copy(src, extra / "kept_one.png")
    (extra / "kept_one.png").write_bytes((extra / "kept_one.png").read_bytes() + b"\0")   # 不同檔案（不被當成搬家）
    old = db.get_settings()["roots"]
    try:
        client.post("/api/settings", json={"roots": old + [str(extra)]})
        wait_idle(client)
        ids = ids_by_name(client)
        g, k = ids["gone_one.jpg"], ids["kept_one.png"]
        (extra / "gone_one.jpg").unlink()
        client.post("/api/jobs", json={"kind": "scan"})
        wait_idle(client)
        c = db.conn()
        assert c.execute("SELECT status FROM media WHERE id=?", (g,)).fetchone()[0] == "missing"
        n_missing_other = c.execute("SELECT COUNT(*) FROM media WHERE status='missing' AND id<>?", (g,)).fetchone()[0]
        client.post("/api/settings", json={"roots": old})
        wait_idle(client)
        assert c.execute("SELECT 1 FROM media WHERE id=?", (g,)).fetchone() is None, "拿掉的資料夾裡找不到的紀錄要清掉"
        assert c.execute("SELECT status FROM media WHERE id=?", (k,)).fetchone()[0] == "done", "還在的圖不動"
        assert c.execute("SELECT COUNT(*) FROM media WHERE status='missing'").fetchone()[0] == n_missing_other, "別的資料夾的不動"
    finally:
        client.post("/api/settings", json={"roots": old})
        wait_idle(client)
        shutil.rmtree(extra, ignore_errors=True)


def test_script_import_keeps_read_cache(client, monkeypatch):
    from app import db, importer
    monkeypatch.setattr(db, "READ_CACHE", True)
    gen = db._user_gen
    client.post("/api/fetch-url/ext/seen", headers={"X-Import-Key": importer.import_key()}, json={"urls": []})
    assert db._user_gen == gen, "腳本送來的不算你按的"
    assert db._bg_at > 0
    client.post("/api/media/share", json={"ids": []})
    assert db._user_gen == gen, "分享（複製到剪貼簿）不改資料庫"
    client.post("/api/settings", json={})
    assert db._user_gen > gen


def test_restore_missing_from_source(client):
    """b97.2：找不到檔案、但知道出處 → 從出處下載回原本的資料夾，標記都留著；不是同一張的不放。"""
    import json
    import os
    import time
    from PIL import Image, ImageDraw
    from app import db, upgrade
    from tests import mock_online
    from tests.conftest import make_image
    hi = TMP / "remote_rs"
    hi.mkdir(exist_ok=True)
    (LIB / "rs").mkdir(exist_ok=True)
    make_image(hi / "a.png", 7101, size=(800, 1120))
    Image.open(hi / "a.png").resize((400, 560), Image.LANCZOS).save(LIB / "rs" / "IMG_7101.jpg", quality=88)
    make_image(hi / "b_src.png", 7102, size=(800, 1120))
    Image.open(hi / "b_src.png").resize((400, 560), Image.LANCZOS).save(LIB / "rs" / "IMG_7102.jpg", quality=88)
    im = Image.open(hi / "b_src.png").convert("RGB")
    ImageDraw.Draw(im).rectangle((300, 300, 520, 420), fill=(250, 250, 250))
    im.save(hi / "b.png")                       # 網站上的是差分 → 不能當成這張
    mock_online.UP.update({5161: {"file": str(hi / "a.png"), "w": 800, "h": 1120, "ext": "png"},
                           5162: {"file": str(hi / "b.png"), "w": 800, "h": 1120, "ext": "png"}})
    client.post("/api/jobs", json={"kind": "scan"})
    wait_idle(client)
    ids = ids_by_name(client)
    a, b = ids["IMG_7101.jpg"], ids["IMG_7102.jpg"]
    with db.write_lock:
        for mid, pid in ((a, 5161), (b, 5162)):
            db.conn().execute("""INSERT OR REPLACE INTO media_source(media_id, status, method, similarity, post_url, data, fetched_at)
                VALUES(?, 'found', 'md5', 100, ?, ?, ?)""", (mid, f"http://x/posts/{pid}",
                                                          json.dumps({"danbooru": {"post_id": pid, "size": [800, 1120]}}), time.time()))
    client.post("/api/tags/apply", json={"ids": [a], "action": "add", "tag": "restore_girl_(tst)", "category": "character"})
    mt = os.path.getmtime(LIB / "rs" / "IMG_7101.jpg")
    (LIB / "rs" / "IMG_7101.jpg").unlink()
    (LIB / "rs" / "IMG_7102.jpg").unlink()
    client.post("/api/jobs", json={"kind": "scan"})
    wait_idle(client)
    c = db.conn()
    assert c.execute("SELECT status FROM media WHERE id=?", (a,)).fetchone()[0] == "missing"
    assert {a, b} <= set(upgrade.restorable_ids())
    r = upgrade.restore_missing(a)
    assert r["ok"] and os.path.isfile(r["path"]) and os.path.dirname(r["path"]) == str(LIB / "rs"), r
    assert abs(os.path.getmtime(r["path"]) - mt) < 2, "日期沿用原本的"
    row = c.execute("SELECT status, width FROM media WHERE id=?", (a,)).fetchone()
    assert row[0] in ("pending", "done") and row[1] == 800
    assert c.execute("""SELECT x.state FROM media_tags x JOIN tags t ON t.id=x.tag_id WHERE x.media_id=? AND t.name='restore_girl_(tst)'""",
                     (a,)).fetchone()[0] == 1, "標記留著"
    try:
        upgrade.restore_missing(b)
        raise AssertionError("差分不能放回去")
    except upgrade.UpgradeError as e:
        assert "不是同一張" in str(e)
    assert not any(p.name.startswith("IMG_7102") for p in (LIB / "rs").iterdir())
    # 透過 API（背景）：剩下的 b 失敗、有原因
    assert client.post("/api/restore-missing", json={"ids": [b]}).json()["queued"] == 1
    for _ in range(50):
        st = client.get("/api/restore-missing").json()
        if not st.get("running"):
            break
        time.sleep(0.1)
    assert st["results"][0]["ok"] is False and st["results"][0]["error"]
    wait_idle(client)


def test_case_twins_b98(client, monkeypatch):
    """b98：Windows 上 IMG_1.jpg 和 IMG_1.JPG 是同一個檔案。掃描不再多記一筆；已經有的兩筆合成一筆（標記搬過去）；
    重複頁不列；丟回收筒時還有別筆指著同一個檔案 → 檔案保留，只把這筆併過去。"""
    from app import db, pipeline
    from tests.conftest import make_image
    monkeypatch.setattr(pipeline, "CASE_FOLD", True)
    (LIB / "ct").mkdir(exist_ok=True)
    make_image(LIB / "ct" / "IMG_8801.jpg", 8801)
    client.post("/api/jobs", json={"kind": "scan"})
    wait_idle(client)
    real = ids_by_name(client)["IMG_8801.jpg"]
    c = db.conn()
    up = str(LIB / "ct" / "IMG_8801.JPG")
    row = dict(c.execute("SELECT kind, size, mtime, fp, dhash, width, height, embedding, tagged_at FROM media WHERE id=?", (real,)).fetchone())
    with db.write_lock:      # 模擬以前多記的那筆（只差大小寫），你在它身上確認了角色
        c.execute("""INSERT INTO media(path, kind, size, mtime, status, added_at, fp, dhash, width, height, embedding, tagged_at)
                     VALUES(?,?,?,?, 'done', ?, ?,?,?,?,?,?)""", (up, row["kind"], row["size"], row["mtime"], time.time(), row["fp"],
                                                                  row["dhash"], row["width"], row["height"], row["embedding"], row["tagged_at"]))
        twin = c.execute("SELECT id FROM media WHERE path=?", (up,)).fetchone()[0]
        c.commit()
    client.post("/api/tags/apply", json={"ids": [twin], "action": "add", "tag": "twin_girl_(tst)", "category": "character"})
    pipeline.dup_cache.clear()
    assert not any({real, twin} <= set(g) for g in pipeline.find_duplicates()), "同一個檔案不算重複"
    # 掃描：不會再多一筆，也不會把其中一筆標成找不到
    client.post("/api/jobs", json={"kind": "scan"})
    wait_idle(client)
    assert c.execute("SELECT COUNT(*) FROM media WHERE lower(path)=lower(?)", (up,)).fetchone()[0] == 2
    # 丟回收筒（丟的是多的那筆）→ 檔案還在，標記併到另一筆
    r = client.post("/api/media/trash", json={"ids": [twin]}).json()
    assert r["merged"] == 1 and r["trashed"] == 0
    assert (LIB / "ct" / "IMG_8801.jpg").exists(), "檔案不能丟"
    assert c.execute("SELECT 1 FROM media WHERE id=?", (twin,)).fetchone() is None
    assert c.execute("""SELECT x.state FROM media_tags x JOIN tags t ON t.id=x.tag_id WHERE x.media_id=? AND t.name='twin_girl_(tst)'""",
                     (real,)).fetchone()[0] == 1
    # 啟動時合併：找不到的那筆身上的標記搬到還在的那筆
    with db.write_lock:
        c.execute("""INSERT INTO media(path, kind, size, mtime, status, added_at, fp) VALUES(?,?,?,?, 'missing', ?, ?)""",
                  (up, row["kind"], row["size"], row["mtime"], time.time(), row["fp"]))
        twin2 = c.execute("SELECT id FROM media WHERE path=?", (up,)).fetchone()[0]
        tid = db.tag_id("twin_artist_(tst)", "artist")
        c.execute("INSERT INTO media_tags(media_id, tag_id, source, state) VALUES(?, ?, 'manual', 1)", (twin2, tid))
        c.commit()
    res = pipeline.merge_case_twins()
    assert res["groups"] >= 1 and res["moved"] >= 1
    assert c.execute("SELECT 1 FROM media WHERE id=?", (twin2,)).fetchone() is None
    assert c.execute("SELECT state FROM media_tags WHERE media_id=? AND tag_id=?", (real, tid)).fetchone()[0] == 1
    assert c.execute("SELECT status FROM media WHERE id=?", (real,)).fetchone()[0] == "done"


def test_trash_preview_whole_dup_group(client):
    """b98：重複頁整組每一張都選了要刪 → 刪除視窗提醒（一張都不留）。"""
    from app import db, pipeline
    a, b = [r[0] for r in db.conn().execute("SELECT id FROM media WHERE status='done' ORDER BY id LIMIT 2")]
    old = pipeline.dup_cache.get(3)
    pipeline.dup_cache[3] = [[a, b]]
    try:
        assert client.post("/api/media/archive/preview", json={"ids": [a, b]}).json()["dup_all"] == 1
        assert client.post("/api/media/archive/preview", json={"ids": [a]}).json()["dup_all"] == 0
    finally:
        if old is None:
            pipeline.dup_cache.pop(3, None)
        else:
            pipeline.dup_cache[3] = old


def test_album_cover_fallback_b994(client):
    """b99.4：本子封面的圖被清掉、丟回收筒 → 用第一頁，不顯示壞掉的圖；清掉的紀錄順便把封面欄位清乾淨。"""
    from app import albums, db, maintenance, pipeline
    c = db.conn()
    ids = [r[0] for r in c.execute("SELECT id FROM media WHERE status='done' AND id NOT IN (SELECT media_id FROM album_items) ORDER BY id LIMIT 3")]
    aid = albums.create("cover_test_(tst)", ids, "other", [])
    try:
        with db.write_lock:
            c.execute("UPDATE albums SET cover=? WHERE id=?", (ids[1], aid))
            c.commit()
        get = lambda: next(a for a in albums.listing("show") if a["id"] == aid)["cover"]
        assert get() == ids[1]
        with db.write_lock:
            c.execute("UPDATE albums SET cover=999999 WHERE id=?", (aid,))       # 指到不存在的圖
            c.commit()
        assert get() == ids[0], "壞掉的封面改用第一頁"
        assert maintenance._dangling_covers() >= 1
        assert c.execute("SELECT cover FROM albums WHERE id=?", (aid,)).fetchone()[0] is None
        with db.write_lock:
            c.execute("UPDATE albums SET cover=? WHERE id=?", (ids[2], aid))
            c.execute("UPDATE media SET status='trashed' WHERE id=?", (ids[2],))
            c.commit()
        assert get() == ids[0], "丟回收筒的封面也不用"
        with db.write_lock:
            c.execute("UPDATE media SET status='done' WHERE id=?", (ids[2],))
            c.commit()
    finally:
        with db.write_lock:
            c.execute("DELETE FROM album_items WHERE album_id=?", (aid,))
            c.execute("DELETE FROM albums WHERE id=?", (aid,))
            c.commit()


def test_artist_local_dupes_b100(client):
    """同一個人變成兩個創作者（帳號連結一樣）→ 弱的併進 Danbooru 正式登記的；兩個都正式登記的不動。"""
    from app import db, siteartists
    from app.danbooru import save_tag_info
    c = db.conn()
    mid = c.execute("SELECT id FROM media WHERE status='done' ORDER BY id LIMIT 1").fetchone()[0]

    def mk(name, links, status="ok", post_count=None, other=()):
        tid = db.tag_id(name, "artist")
        save_tag_info(tid, {"status": status, "copyright": None, "copyrights": [], "summary": "", "post_count": post_count,
                            "other_names": list(other), "url": None, "links": links})
        with db.write_lock:
            c.execute("UPDATE tag_info SET status=? WHERE tag_id=?", (status, tid))
            c.commit()
        return tid
    site = mk("b100_site", ["https://x.com/B100Art"], status="notfound")
    dan = mk("b100_dan", ["https://www.pixiv.net/users/9100", "https://x.com/b100art"], post_count=40)
    with db.write_lock:
        c.execute("INSERT OR REPLACE INTO media_tags(media_id, tag_id, state, source) VALUES(?,?,1,'manual')", (mid, site))
        c.commit()
    a1 = mk("b100_twin_a", ["https://www.pixiv.net/users/9200"], post_count=10)        # 兩個都正式登記
    a2 = mk("b100_twin_b", ["https://www.pixiv.net/member.php?id=9200"], post_count=12)
    r34 = db.tag_id("b100_r34name", "artist")
    with db.write_lock:
        c.execute("INSERT OR REPLACE INTO pending_artist(tag, site, key, created_at, checked_at) VALUES('b100_r34name','r34','b100_r34name',0,0)")
        c.commit()
    mk("b100_dan2", ["https://www.pixiv.net/users/9300"], post_count=5, other=["B100_R34Name"])
    pairs = dict(siteartists.local_dupes())
    assert pairs.get("b100_site") == "b100_dan"
    assert pairs.get("b100_r34name") == "b100_dan2"
    assert "b100_twin_a" not in pairs and "b100_twin_b" not in pairs
    done = dict(siteartists.merge_local_dupes())
    assert done.get("b100_site") == "b100_dan"
    assert c.execute("SELECT 1 FROM tags WHERE name='b100_site'").fetchone() is None
    assert c.execute("SELECT state FROM media_tags WHERE media_id=? AND tag_id=?", (mid, dan)).fetchone()[0] == 1
    assert db.tag_id("b100_site", create=False) == dan, "舊名字變別名"
    assert c.execute("SELECT 1 FROM pending_artist WHERE tag='b100_r34name'").fetchone() is None
    assert not siteartists.local_dupes() or "b100_site" not in dict(siteartists.local_dupes())
    assert {a1, a2} <= {r[0] for r in c.execute("SELECT id FROM tags WHERE name LIKE 'b100_twin_%'")}
