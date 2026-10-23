"""b84：其他網站交給 gallery-dl、標籤蘊含、匯出訓練集。"""
import os
import sys
import time
from pathlib import Path

from tests.conftest import LIB, TMP, ids_by_name, make_image, wait_idle

FAKE = f'"{sys.executable}" "{Path(__file__).with_name("fake_gdl.py")}"'


def _wait_gdl(client, url):
    for _ in range(200):
        j = next((x for x in client.get("/api/fetch-url/galleries").json()["jobs"] if x["url"] == url), None)
        if j and j["state"] != "running":
            return j
        time.sleep(0.1)
    raise AssertionError("gallery-dl 工作沒有結束")


def test_gdl_match_rules():
    from app import gdl, importer
    assert gdl.available()
    m = gdl.match("https://bsky.app/profile/someone.bsky.social/post/3kabc")
    assert m and m["category"] == "bluesky" and m["single"]
    assert gdl.match("https://bsky.app/profile/someone.bsky.social")["single"] is False      # 整個帳號
    assert gdl.match("https://example.com/a.jpg") is None, "directlink（任意圖片網址）不接"
    assert gdl.match("https://example.com/page") is None
    assert gdl.match("javascript:alert(1)") is None
    # 自己寫的網站照舊走 importer（不交給 gallery-dl）
    assert importer.parse("https://x.com/a/status/1234567")["site"] == "x"
    assert gdl.match("https://x.com/someart") is None and gdl.match("https://www.pixiv.net/users/123") is None, "自己匯入的網站不交給 gallery-dl"
    assert gdl.site_label("bluesky") == "Bluesky"
    meta = gdl.meta_of("bluesky", {"author": {"handle": "abc.bsky.social", "displayName": "ABC"}, "post_id": "9", "tags": ["a", "b"],
                                   "sensitive": True}, "https://u")
    assert meta["author"] == "ABC" and meta["handle"] == "abc" and meta["tags"] == ["a", "b"] and meta["nsfw"] and meta["post_id"] == "9"


def test_gdl_import_flow(client, monkeypatch):
    from app import db, gdl
    monkeypatch.setenv("LIB_GDL_CMD", FAKE)
    c = db.conn()
    # 這位畫師的資料卡裡有 Bluesky 連結 → 匯入的圖會有創作者候選
    with db.write_lock:
        tid = db.tag_id("gdl_painter_(tst)", "artist")
        c.execute("INSERT OR REPLACE INTO tag_info(tag_id, status, links, fetched_at) VALUES(?, 'ok', ?, ?)",
                  (tid, '["https://fakeartist.bsky.social"]', time.time()))
        c.commit()
    # 單篇兩張 → 收進小資料夾
    url = "https://bsky.app/profile/fakeartist.bsky.social/post/multi"
    r = client.post("/api/fetch-url", json={"url": url}).json()
    assert r["ok"] and r["queued"] and r["kind"] == "gdl" and r["label"] == "Bluesky", r
    j = _wait_gdl(client, url)
    assert j["state"] == "done" and j["saved"] == 2, j
    folder = LIB / "匯入" / "bluesky"
    sub = [p for p in folder.iterdir() if p.is_dir()]
    assert len(sub) == 1 and sub[0].name.startswith("multi") and len(list(sub[0].glob("*.png"))) == 2
    assert not list(folder.rglob("*.json")), "metadata JSON 不留在圖庫"
    rows = c.execute("SELECT site, post_url, data FROM import_meta WHERE site='gdl:bluesky'").fetchall()
    assert len(rows) == 2 and rows[0][1].endswith("/post/multi")
    wait_idle(client)
    for _ in range(100):
        cand = [tuple(x) for x in c.execute("""SELECT mt.state, mt.source FROM media_tags mt JOIN media m ON m.id=mt.media_id
                            WHERE mt.tag_id=? AND m.path LIKE ?""", (tid, f"%{os.sep}multi%"))]
        if len(cand) == 2:
            break
        time.sleep(0.1)
    assert sorted(cand) == [(0, "gdl"), (0, "gdl")], cand
    # 整個帳號：預設全部；gdl_max_posts 只抓最新幾「篇」（第一篇有兩張：按篇算是 3 篇 4 張）
    client.post("/api/settings", json={"gdl_max_posts": 3})
    try:
        url2 = "https://bsky.app/profile/fakeartist.bsky.social"
        r2 = client.post("/api/fetch-url", json={"url": url2}).json()
        assert r2["ok"] and r2["limit"] == 3 and not r2["single"]
        j2 = _wait_gdl(client, url2)
        assert j2["saved"] == 4 and j2["done"] == 4, j2
    finally:
        client.post("/api/settings", json={"gdl_max_posts": 0})
    # 再貼一次（全部）：抓過的 4 張跳過（下載紀錄），只抓新的 2 篇
    r2b = client.post("/api/fetch-url", json={"url": url2}).json()
    assert r2b["limit"] is None
    j2b = _wait_gdl(client, url2)
    assert j2b["skipped"] == 4 and j2b["saved"] == 2, j2b
    # 圖庫裡已經有的（MD5 一樣）：不再存一份
    monkeypatch.setenv("FAKE_GDL_DUP", str(LIB / "img_05.jpg"))
    url3 = "https://bsky.app/profile/fakeartist.bsky.social/post/dup"
    client.post("/api/fetch-url", json={"url": url3})
    j3 = _wait_gdl(client, url3)
    assert j3["saved"] == 0 and j3["existing"] == 1, j3
    # 要登入：說清楚
    url4 = "https://bsky.app/profile/fakeartist.bsky.social/post/fail"
    client.post("/api/fetch-url", json={"url": url4})
    j4 = _wait_gdl(client, url4)
    assert j4["state"] == "error" and "403" in j4["error"], j4
    # 付費內容平台（FANBOX）、轉貼站不接
    r6 = client.post("/api/fetch-url", json={"url": "https://www.fanbox.cc/@someone/posts"}).json()
    assert not r6["ok"] and "不支援" in r6["error"], r6
    assert gdl.match("https://www.fanbox.cc/@someone/posts") is None
    # gallery-dl 也不認得 → 原本的錯誤訊息
    r5 = client.post("/api/fetch-url", json={"url": "https://example.com/nothing"}).json()
    assert not r5["ok"] and "不支援" in r5["error"]
    info = client.get("/api/gdl", params={"url": "https://bsky.app/profile/a.bsky.social/post/1"}).json()
    assert info["available"] and info["match"]["label"] == "Bluesky"
    wait_idle(client)


def test_tag_implications(client):
    from app import db, implies, people
    ids = sorted(ids_by_name(client).items())
    a, b, cimg = [i for n, i in ids if n.startswith("img_")][8:11]
    c = db.conn()
    with db.write_lock:
        for n in ("impvar_(bunny)_(tst)", "impbase_(tst)"):
            db.tag_id(n, "character")
        for n in ("imp_sticker_(tst)", "imp_meme_(tst)"):
            db.tag_id(n, "custom")
        c.execute("DELETE FROM media_tags WHERE media_id IN (?,?,?)", (a, b, cimg))
        c.commit()
    # 你訂的規則：任何類別都可以
    r = client.post("/api/tag/imp_sticker_(tst)/implies", json={"implies": ["imp_meme_(tst)"]}).json()
    assert [x["name"] for x in r["implies"]] == ["imp_meme_(tst)"] and r["implies"][0]["src"] == "manual"
    assert client.post("/api/tag/imp_meme_(tst)/implies", json={"implies": ["imp_sticker_(tst)"]}).status_code == 400, "繞一圈要擋"
    assert client.post("/api/tag/imp_sticker_(tst)/implies", json={"implies": ["no_such_tag_x"]}).status_code == 400
    # 確認 A → B 一起確認；你否決過 B 的那張不動
    client.post("/api/tags/apply", json={"ids": [cimg], "action": "reject", "tag": "imp_meme_(tst)", "category": "custom"})
    client.post("/api/tags/apply", json={"ids": [a, cimg], "action": "add", "tag": "imp_sticker_(tst)", "category": "custom"})
    st = lambda mid, n: c.execute("SELECT state, source FROM media_tags mt JOIN tags t ON t.id=mt.tag_id WHERE mt.media_id=? AND t.name=?", (mid, n)).fetchone()
    assert tuple(st(a, "imp_meme_(tst)")) == (1, "implied")
    assert st(cimg, "imp_meme_(tst)")[0] == -1
    # 撤銷：兩個一起回去
    client.post("/api/undo", json={})
    assert st(a, "imp_sticker_(tst)") is None and st(a, "imp_meme_(tst)") is None
    client.post("/api/tags/apply", json={"ids": [a], "action": "add", "tag": "imp_sticker_(tst)", "category": "custom"})
    # 否決 A：只因為 A 帶上的 B 一起拿掉
    client.post("/api/tags/apply", json={"ids": [a], "action": "reject", "tag": "imp_sticker_(tst)", "category": "custom"})
    assert st(a, "imp_meme_(tst)") is None
    # Danbooru：角色 ⇒ 角色（假 Danbooru 有 impvar_(bunny)_(tst) ⇒ impbase_(tst)）
    var = db.tag_id("impvar_(bunny)_(tst)", create=False)
    base = db.tag_id("impbase_(tst)", create=False)
    with db.write_lock:     # 舊圖：已經確認了造型、還沒有本體
        c.execute("INSERT INTO media_tags(media_id, tag_id, source, state) VALUES(?,?, 'manual', 1)", (b, var))
        c.commit()
    res = implies.sync_danbooru([var])
    assert res["added"] == 1, res
    assert (var, base) in set(map(tuple, c.execute("SELECT tag_id, base_id FROM char_base"))), "算人數時造型併進本體"
    d = client.post("/api/implies/backfill", json={"dry": True}).json()
    assert d["images"] >= 1 and d["rows"] >= 1
    assert st(b, "impbase_(tst)") is None, "dry 不改資料"
    client.post("/api/implies/backfill", json={"dry": False})
    assert tuple(st(b, "impbase_(tst)")) == (1, "implied")
    client.post("/api/undo", json={})
    assert st(b, "impbase_(tst)") is None, "套用舊圖可以撤銷"
    # 「不是同一個人」：Danbooru 的也能關掉，之後不套用、Danbooru 更新也不加回來
    tag = client.get("/api/tag/impvar_(bunny)_(tst)").json()
    assert [x["name"] for x in tag["implies"]["implies"]] == ["impbase_(tst)"] and tag["implies"]["implies"][0]["src"] == "danbooru"
    client.post("/api/tag/impvar_(bunny)_(tst)/implies", json={"implies": []})
    implies.sync_danbooru([var], force=True)
    assert implies.closure(var) == []
    assert (var, base) not in set(map(tuple, c.execute("SELECT tag_id, base_id FROM char_base")))
    s = client.get("/api/implies").json()
    assert s["off"] >= 1 and s["manual"] >= 1 and any(x["name"] == "impvar_(bunny)_(tst)" and x["src"] == "off" for x in s["list"])
    # 收回「不是」
    client.post("/api/tag/impvar_(bunny)_(tst)/implies-off", json={"other": "impbase_(tst)", "off": False})
    implies.sync_danbooru([var], force=True)
    assert implies.closure(var) == [base]
    people.refresh_bases()


def test_dataset_export(client, tmp_path):
    from app import db
    c = db.conn()
    alive = [r[0] for r in c.execute("SELECT id, path FROM media WHERE kind='image' AND status='done' ORDER BY id") if os.path.isfile(r[1])
             and r[1].lower().endswith(".jpg")]
    mid, other = alive[0], alive[1]
    src = Path(c.execute("SELECT path FROM media WHERE id=?", (mid,)).fetchone()[0])
    with db.write_lock:
        c.execute("DELETE FROM media_tags WHERE media_id=?", (mid,))
        ch = db.tag_id("ds_girl_(ds_series)", "character")
        cp = db.tag_id("ds_series", "copyright")
        ar = db.tag_id("ds_painter", "artist")
        g1, g2, g3, g4 = (db.tag_id(n, "general") for n in ("long_hair", "smile", "blurry", "^_^"))
        c.execute("INSERT OR REPLACE INTO tag_info(tag_id, status, copyright, fetched_at) VALUES(?, 'ok', 'ds_series', 0)", (ch,))
        rows = [(ch, None, "manual", 1), (ar, None, "manual", 1), (g1, 0.9, "model", 0), (g2, 0.5, "model", 0),
                (g3, 0.2, "model", 0), (g4, 0.8, "model", 0), (db.tag_id("ds_rejected", "general"), 0.95, "model", -1)]
        c.executemany("INSERT INTO media_tags(media_id, tag_id, score, source, state) VALUES(?,?,?,?,?)", [(mid, *r) for r in rows])
        c.commit()
    dest = tmp_path / "lora set"
    body = {"ids": [mid, other], "dest": str(dest), "opts": {"trigger": "dsx"}}
    p = client.post("/api/dataset/export", json={**body, "dry": True}).json()
    assert p["n"] == 2
    cap = p["samples"][0]["caption"]
    assert cap == r"dsx, ds girl \(ds series\), ds series, ds painter, long hair, ^_^, smile", cap
    # 選項：不要一般標籤、不跳脫、不換空格
    p2 = client.post("/api/dataset/export", json={**body, "dry": True, "opts": {"cats": ["character", "artist"], "escape": False, "spaces": False}}).json()
    assert p2["samples"][0]["caption"] == "ds_girl_(ds_series), ds_painter"
    # 圖庫資料夾裡面不行
    bad = client.post("/api/dataset/export", json={**body, "dest": str(LIB / "out")})
    assert bad.status_code == 400 and "圖庫" in bad.json()["detail"]
    assert client.post("/api/dataset/export", json={**body, "dest": "relative/path"}).status_code == 400
    j = client.post("/api/dataset/export", json=body).json()
    for _ in range(100):
        s = client.get(f"/api/dataset/job/{j['id']}").json()
        if s["state"] != "running":
            break
        time.sleep(0.1)
    assert s["state"] == "done" and s["done"] == 2, s
    assert (dest / f"{mid}.jpg").read_bytes() == src.read_bytes()
    assert (dest / f"{mid}.txt").read_text("utf-8") == cap
    assert (dest / f"{other}.txt").exists()
    # HEIC／TIFF 這類轉成 PNG
    tif = make_image(TMP / "ds_extra" / "x.tiff", 77)
    with db.write_lock:
        cur = c.execute("INSERT INTO media(path, kind, status, fp) VALUES(?, 'image', 'done', 'dsfp1')", (str(tif),))
        tmid = cur.lastrowid
        c.commit()
    j2 = client.post("/api/dataset/export", json={"ids": [tmid], "dest": str(dest)}).json()
    for _ in range(100):
        s2 = client.get(f"/api/dataset/job/{j2['id']}").json()
        if s2["state"] != "running":
            break
        time.sleep(0.1)
    assert s2["converted"] == 1 and (dest / f"{tmid}.png").exists()
    with db.write_lock:
        c.execute("DELETE FROM media WHERE id=?", (tmid,))
        c.commit()


def test_download_retries_on_disconnect(monkeypatch, tmp_path):
    """下載途中伺服器斷線：等一下再試（偶爾 RemoteProtocolError）；一直斷才算失敗。"""
    import httpx
    from app import importer
    calls = []
    monkeypatch.setattr(importer.time, "sleep", lambda s: None)

    def flaky(url, dst, headers, site, max_bytes):
        calls.append(1)
        if len(calls) < 3:
            raise httpx.RemoteProtocolError("Server disconnected without sending a response.")
        dst.write_bytes(b"ok")
    monkeypatch.setattr(importer, "_download1", flaky)
    importer._download("https://example.com/x.png", tmp_path / "x.png")
    assert len(calls) == 3 and (tmp_path / "x.png").read_bytes() == b"ok"
    calls.clear()

    def dead(*a):
        calls.append(1)
        raise httpx.ConnectError("refused")
    monkeypatch.setattr(importer, "_download1", dead)
    try:
        importer._download("https://example.com/y.png", tmp_path / "y.png")
        raise AssertionError("要失敗")
    except importer.ImportError_ as e:
        assert "試了 3 次" in str(e) and len(calls) == 3
