"""ascii2d 半自動找出處：解析結果頁、比對 X 貼文的圖、你按確認才寫入。ascii2d 本身不會被連線（結果由測試直接送）。"""
import time

import httpx

from tests.conftest import LIB, MOCK_PORT, ids_by_name, wait_idle


def test_parse_ascii2d_items():
    from app import ascii2d
    items = [
        {"thumb": "https://ascii2d.net/thumbnail/q.jpg", "links": []},                     # 第一格是你上傳的圖：沒有連結
        {"thumb": "https://ascii2d.net/thumbnail/a.jpg", "links": [
            {"href": "https://twitter.com/Some_Art/status/1790000000000000001", "text": "タイトル"},
            {"href": "https://twitter.com/intent/user?user_id=123", "text": "繪師名"}]},
        {"links": [{"href": "https://www.pixiv.net/artworks/98765432", "text": "作品"},
                   {"href": "https://www.pixiv.net/users/555", "text": "畫家"},
                   {"href": "https://twitter.com/Some_Art/status/1790000000000000001", "text": "重複"}]},
        {"links": [{"href": "javascript:alert(1)", "text": "x"}, {"href": "https://example.com/", "text": "y"}, "垃圾"]},
    ]
    r = ascii2d.parse(items)
    assert [x["kind"] for x in r] == ["x", "pixiv"], r
    assert r[0]["handle"] == "Some_Art" and r[0]["tweet_id"] == "1790000000000000001" and r[0]["author"] == "繪師名"
    assert r[0]["url"] == "https://x.com/Some_Art/status/1790000000000000001"
    assert r[1]["pixiv_id"] == 98765432 and r[1]["author"] == "畫家"
    assert ascii2d.parse(None) == [] and ascii2d.parse([{"links": None}]) == []


def test_ascii2d_flow(client):
    """排隊 → 腳本拿圖 → 送回結果 → 比對 → 確認寫入（創作者確認、出處記下）；沒找到／跳過。"""
    from app import ascii2d, db, importer
    img = httpx.get(f"http://127.0.0.1:{MOCK_PORT}/media/AbCdEfGh12345Xy.jpg").content   # 就是 X 貼文上的那張圖
    (LIB / "a2d").mkdir(exist_ok=True)
    (LIB / "a2d" / "IMG_9001.jpg").write_bytes(img)
    (LIB / "a2d" / "IMG_9002.jpg").write_bytes(httpx.get(f"http://127.0.0.1:{MOCK_PORT}/media/ZzZzZzZz99999Qq.jpg").content)
    client.post("/api/jobs", json={"kind": "scan"})
    wait_idle(client)
    ids = ids_by_name(client)
    a, b = ids["IMG_9001.jpg"], ids["IMG_9002.jpg"]
    H = {"X-Import-Key": importer.import_key()}
    # 金鑰：沒有就拒絕；跨站（ascii2d.net）送來、有金鑰就放行
    assert client.get("/api/ascii2d/ext/state").status_code == 403
    assert client.post("/api/ascii2d/ext/next", json={}, headers={"Origin": "https://ascii2d.net", "Sec-Fetch-Site": "cross-site"}).status_code == 403
    assert client.post("/api/ascii2d/start", json={"ids": [a, b], "only_missing": False}).json()["remaining"] == 2   # 照你給的順序
    n = client.post("/api/ascii2d/ext/next", json={}, headers={**H, "Origin": "https://ascii2d.net", "Sec-Fetch-Site": "cross-site"})
    assert n.status_code == 200 and n.json()["mid"] == a, n.text
    up = client.get(f"/api/ascii2d/ext/image/{a}", headers=H)
    assert up.status_code == 200 and up.content[:2] == b"\xff\xd8"
    items = [{"links": []}, {"links": [{"href": "https://twitter.com/someart/status/1790000000000000001", "text": "新圖"},
                                       {"href": "https://twitter.com/intent/user?user_id=1", "text": "Some Art"}]}]
    assert client.post("/api/ascii2d/ext/result", json={"mid": a, "page_url": "https://ascii2d.net/search/color/abc", "items": items},
                       headers=H).json()["results"] == 1
    for _ in range(100):
        r = client.get(f"/api/ascii2d/ext/result/{a}", headers=H).json()
        if r["status"] == "ready":
            break
        time.sleep(0.1)
    assert r["status"] == "ready", r
    best = r["results"][r["best"]]
    assert best["match"] == "same" and best["handle"] == "someart", best
    artist = [t for t in r["tags"] if t["category"] == "artist"]
    assert artist and artist[0]["checked"], r["tags"]
    chars = [t for t in r["tags"] if t["category"] == "character"]
    assert all(not t["checked"] for t in chars if "的標籤" in t["why"]), ("推文標籤推的角色預設不勾", r["tags"])
    # 還沒按確認：資料庫不能有這個創作者
    c = db.conn()
    q = """SELECT mt.state, mt.source FROM media_tags mt JOIN tags t ON t.id=mt.tag_id WHERE mt.media_id=? AND t.name=?"""
    assert not c.execute(q, (a, artist[0]["name"])).fetchone()
    d = client.post("/api/ascii2d/ext/decide", json={"mid": a, "outcome": "found", "accept": [artist[0]["name"], "not_offered_tag"]}, headers=H).json()
    assert d["found"] >= 1
    assert tuple(c.execute(q, (a, artist[0]["name"])).fetchone()) == (1, "ascii2d")
    assert not c.execute("SELECT 1 FROM tags WHERE name='not_offered_tag'").fetchone(), "只能確認面板上列出的"
    src = c.execute("SELECT status, method, source_url FROM media_source WHERE media_id=?", (a,)).fetchone()
    assert tuple(src) == ("found", "ascii2d", "https://x.com/someart/status/1790000000000000001")
    assert client.post("/api/undo", json={}).json()["undone"]            # 可以撤銷
    assert not c.execute(q + " AND mt.state=1", (a, artist[0]["name"])).fetchone()
    # 下一張：沒找到 → 不再排；跳過 → 重新開始時再排
    assert client.post("/api/ascii2d/ext/next", json={}, headers=H).json()["mid"] == b
    client.post("/api/ascii2d/ext/decide", json={"mid": b, "outcome": "skip"}, headers=H)
    assert client.post("/api/ascii2d/ext/next", json={}, headers=H).json()["mid"] is None
    client.post("/api/ascii2d/start", json={"ids": [b]})
    assert client.post("/api/ascii2d/ext/next", json={}, headers=H).json()["mid"] == b
    client.post("/api/ascii2d/ext/decide", json={"mid": b, "outcome": "none"}, headers=H)
    assert b not in ascii2d.targets() and a not in ascii2d.targets()
    assert client.post("/api/ascii2d/ext/decide", json={"mid": b, "outcome": "bogus"}, headers=H).status_code == 400
    assert client.get("/api/ascii2d").json()["none"] >= 1
    client.post("/api/ascii2d/stop", json={})


def test_userscript_has_ascii2d_mode(client):
    js = client.get("/api/fetch-url/userscript.user.js").text
    assert "@match        https://ascii2d.net/*" in js and "@version      3.3" in js
    assert "/api/ascii2d/ext/next" in js and "@updateURL" in js and "__UPDATE__" not in js


def test_ascii2d_x_only_and_mismatch(client):
    """Danbooru 沒收的推文：用 X 帳號當創作者；畫面不像的結果不給候選。"""
    from app import ascii2d
    img = httpx.get(f"http://127.0.0.1:{MOCK_PORT}/media/LaTeRaRt12345Xy.jpg").content
    (LIB / "a2d" / "IMG_9003.jpg").write_bytes(img)
    client.post("/api/jobs", json={"kind": "scan"})
    wait_idle(client)
    ids = ids_by_name(client)
    x = {"rank": 1, "kind": "x", "handle": "Later_Art", "tweet_id": "1790000000000000002", "author": None,
         "url": "https://x.com/Later_Art/status/1790000000000000002", "thumb": None}
    r = ascii2d.resolve(ids["IMG_9003.jpg"], [x])
    best = r["results"][r["best"]]
    assert best["match"] == "same" and not best.get("danbooru"), best
    art = [t for t in r["tags"] if t["category"] == "artist"]
    assert art and "的作者" in art[0]["why"] and art[0]["checked"], r["tags"]
    r2 = ascii2d.resolve(ids["IMG_9002.jpg"], [x])
    assert r2["results"][0]["match"] == "unlikely" and r2["tags"] == [], r2


def test_ascii2d_choose_scope(client):
    """自己選範圍：搜尋條件、只找沒創作者的、最多幾張、換掉或加在後面、清空佇列。"""
    from app import ascii2d
    client.post("/api/ascii2d/clear", json={})
    everything = client.get("/api/ascii2d/preview", params={"q": ""}).json()
    assert everything["eligible"] >= 2 and everything["count"] == everything["eligible"]
    pv = client.get("/api/ascii2d/preview", params={"q": "path:a2d", "limit": 1}).json()
    assert pv["matched"] >= 1 and pv["count"] == 1, pv
    allp = client.get("/api/ascii2d/preview", params={"q": "path:a2d", "only_missing": False}).json()
    assert allp["count"] == allp["matched"] >= 3, "不限還沒有創作者：路徑裡的圖全部算"
    r = client.post("/api/ascii2d/start", json={"q": "path:a2d", "only_missing": False, "limit": 2}).json()
    assert r["remaining"] == 2, r
    r = client.post("/api/ascii2d/start", json={"q": "path:a2d", "only_missing": False, "limit": 3, "replace": False}).json()
    assert r["remaining"] == 3, "加在後面（已經排的不會重複）"
    r = client.post("/api/ascii2d/start", json={"q": "path:a2d", "only_missing": False, "limit": 1}).json()
    assert r["remaining"] == 1, "換成這次的範圍"
    assert client.post("/api/ascii2d/start", json={"q": "path:zzz_no_such_folder"}).status_code == 400
    assert client.post("/api/ascii2d/clear", json={}).json()["remaining"] == 0
    assert isinstance(client.get("/api/ascii2d/months").json(), list)
    ascii2d.stop()


def test_ascii2d_pixiv_and_thumbnail_verify(client):
    """Pixiv 的結果也要比對（抓 Pixiv 的圖，要帶 Referer）；抓不到就用 ascii2d 頁面送來的縮圖；
    挑結果時「比不了、但 ascii2d 排第一」的要勝過「比對過、根本不像」的。"""
    import base64
    from app import ascii2d
    png = httpx.get(f"http://127.0.0.1:{MOCK_PORT}/img/55556666_p0.png", headers={"Referer": "https://www.pixiv.net/"}).content
    (LIB / "a2d" / "IMG_9004.png").write_bytes(png)
    client.post("/api/jobs", json={"kind": "scan"})
    wait_idle(client)
    mid = ids_by_name(client)["IMG_9004.png"]
    r = ascii2d.resolve(mid, [{"rank": 1, "kind": "pixiv", "pixiv_id": 55556666, "url": "https://www.pixiv.net/artworks/55556666"}])
    best = r["results"][r["best"]]
    assert best["match"] == "same" and best["verify_by"] == "Pixiv 的圖", best
    # 作品不存在（刪了、R-18）：用 ascii2d 的縮圖
    data = "data:image/png;base64," + base64.b64encode(png).decode()
    r = ascii2d.resolve(mid, [{"rank": 1, "kind": "pixiv", "pixiv_id": 99999999, "url": "u", "thumb_data": data}])
    best = r["results"][r["best"]]
    assert best["match"] == "same" and best["verify_by"] == "ascii2d 的縮圖" and "thumb_data" not in best, best
    # 比不了的第一名 vs 比對過但不像的第三名 → 選第一名
    x_other = {"rank": 3, "kind": "x", "handle": "someart", "tweet_id": "1790000000000000001", "url": "x"}
    r = ascii2d.resolve(mid, [{"rank": 1, "kind": "pixiv", "pixiv_id": 99999999, "url": "u"}, x_other])
    assert r["results"][1]["match"] == "unlikely" and r["best"] == 0, r


def test_ascii2d_prefers_pixiv(client, monkeypatch):
    """Pixiv 和 X 都對得上 → 用 Pixiv；X 先找到同一張也要繼續看後面的 Pixiv；Pixiv 的作者還沒登記時用 X 的作者補。"""
    from app import ascii2d
    mid = ids_by_name(client)["IMG_9001.jpg"]
    seen = []

    def fake_x(r, local):
        seen.append(r["rank"])
        return {**r, "score": 1.0, "verify_by": "X 的圖"}

    def fake_pixiv(r, local):
        seen.append(r["rank"])
        return {**r, "score": 0.9, "verify_by": "Pixiv 的圖", "pixiv": {}}
    monkeypatch.setattr(ascii2d, "_check_x", fake_x)
    monkeypatch.setattr(ascii2d, "_check_pixiv", fake_pixiv)
    monkeypatch.setattr(ascii2d, "_same_threshold", lambda: 0.8)
    res = [{"rank": 1, "kind": "x", "handle": "Later_Art", "tweet_id": "1", "url": "https://x.com/Later_Art/status/1"},
           {"rank": 2, "kind": "x", "handle": "someone", "tweet_id": "2", "url": "u2"},
           {"rank": 3, "kind": "pixiv", "pixiv_id": 98765432, "url": "https://www.pixiv.net/artworks/98765432"}]
    r = ascii2d.resolve(mid, res)
    assert seen == [1, 3], ("X 找到同一張後：別的 X 不查，Pixiv 照查", seen)
    best = r["results"][r["best"]]
    assert best["kind"] == "pixiv", r
    art = [t for t in r["tags"] if t["category"] == "artist"]
    assert art and "X @Later_Art" in art[0]["why"], ("Pixiv 沒有作者 → 用 X 的作者", r["tags"])


def test_ascii2d_auto_none_and_pixiv_upgrade(client, monkeypatch):
    """都「不太像」→ 結果標 auto=none（腳本自動記沒找到、跳下一張）；Pixiv 有同一張 → 馬上用那篇比大小（換高清）；
    確認時其他對得上的貼文也記下來，換高清會一起比。"""
    import json as _j
    from app import ascii2d, db, upgrade
    mid = ids_by_name(client)["IMG_9002.jpg"]
    with db.write_lock:      # 真的流程裡 receive() 會先建這一列，resolve 只更新它
        db.conn().execute("INSERT OR REPLACE INTO ascii2d(media_id, seq, status) VALUES(?, 0, 'searching')", (mid,))
    monkeypatch.setattr(ascii2d, "_same_threshold", lambda: 0.8)
    monkeypatch.setattr(ascii2d, "_check_x", lambda r, local: {**r, "score": 0.3})
    monkeypatch.setattr(ascii2d, "_check_pixiv", lambda r, local: {**r, "score": 0.2, "pixiv": {}})
    res = [{"rank": 1, "kind": "x", "handle": "nobody", "tweet_id": "1905417273344753695", "url": "https://x.com/nobody/status/1905417273344753695"},
           {"rank": 2, "kind": "pixiv", "pixiv_id": 1234567, "url": "https://www.pixiv.net/artworks/1234567"}]
    r = ascii2d.resolve(mid, res)
    assert r.get("auto") == "none" and not r["tags"], r
    calls = []
    monkeypatch.setattr(ascii2d, "_upgrade_check", lambda m, extra: calls.append((m, extra)))
    monkeypatch.setattr(ascii2d, "_check_x", lambda r, local: {**r, "score": 0.99})
    monkeypatch.setattr(ascii2d, "_check_pixiv", lambda r, local: {**r, "score": 0.99, "pixiv": {}})
    r = ascii2d.resolve(mid, res)
    assert "auto" not in r
    import time
    for _ in range(20):
        if calls:
            break
        time.sleep(0.05)
    assert calls and calls[0][1] == [{"site": "pixiv", "id": "1234567"}], calls
    ascii2d.decide(mid, "found", [t["name"] for t in r["tags"]][:1] or ["x"])
    d = _j.loads(db.conn().execute("SELECT data FROM media_source WHERE media_id=?", (mid,)).fetchone()[0])
    assert d.get("also") == ["https://x.com/nobody/status/1905417273344753695"], d.get("also")
    sites = {(x["site"], x["id"]) for x in upgrade.refs(mid)}
    assert ("pixiv", "1234567") in sites and ("x", "1905417273344753695") in sites, sites


def test_ascii2d_skips_imported(client):
    """b95：從網站匯入的圖（X、Pixiv…）出處早就知道：不在 ascii2d 的範圍、佇列裡的會被清掉；你自己挑的照找。
    （以前從 X 匯入的排最前面找 Pixiv 版：開發時的紀錄 602 張只多找到 4 張）"""
    import os
    import time
    from app import ascii2d, db
    c = db.conn()
    a, b = [r[0] for r in c.execute("SELECT id FROM media WHERE status='done' AND kind='image' ORDER BY id DESC LIMIT 2")]
    pa = c.execute("SELECT path FROM media WHERE id=?", (a,)).fetchone()[0]
    with db.write_lock:
        for m in (a, b):
            c.execute("DELETE FROM ascii2d WHERE media_id=?", (m,))
            c.execute("DELETE FROM media_source WHERE media_id=?", (m,))
            c.execute("DELETE FROM media_tags WHERE media_id=? AND tag_id IN (SELECT id FROM tags WHERE category='artist')", (m,))
        c.execute("UPDATE media SET unrec_skip=0, artist_done=0, excluded=0, official=NULL, media_type='illustration' WHERE id IN (?,?)", (a, b))
        c.execute("INSERT OR REPLACE INTO import_meta(path, site, post_url, data, created_at, applied) VALUES(?, 'x', 'https://x.com/a/status/5', '{}', ?, 1)",
                  (os.path.normpath(pa), time.time()))
        c.commit()
    try:
        t = ascii2d.targets(low=True)
        assert a not in t and b in t
        assert a not in ascii2d.select("", None, True, None, include_x=True)["ids"]
        # 已經在佇列裡（以前排的）：整理時拿掉
        with db.write_lock:
            c.execute("INSERT INTO ascii2d(media_id, seq, status, picked) VALUES(?, 1, 'queued', 0)", (a,))
            c.commit()
        assert ascii2d.prune() >= 1
        assert c.execute("SELECT status FROM ascii2d WHERE media_id=?", (a,)).fetchone()[0] == "skipped"
        # 你自己選、不勾「只找沒有創作者的」：照找
        ascii2d.start([a], replace=False, picked=True)
        ascii2d.prune()
        assert c.execute("SELECT status FROM ascii2d WHERE media_id=?", (a,)).fetchone()[0] == "queued"
    finally:
        with db.write_lock:
            c.execute("DELETE FROM ascii2d WHERE media_id IN (?,?)", (a, b))
            c.execute("DELETE FROM import_meta WHERE path=?", (os.path.normpath(pa),))
            c.commit()
        ascii2d._state["active"] = False


def test_ascii2d_auto_accept_setting(client):
    """設定「自動確認」：選中的結果是同一張（或很像，看設定）而且有標籤 → result 帶 auto=accept（腳本全部勾選後自動按 Enter）。"""
    import json as _j
    from app import ascii2d, db
    mid = ids_by_name(client)["IMG_9001.jpg"]
    res = {"results": [{"rank": 1, "kind": "x", "url": "u", "match": "same"}], "best": 0,
           "tags": [{"name": "aa_(tst)", "category": "artist", "why": "", "checked": True}]}
    with db.write_lock:
        db.conn().execute("INSERT OR REPLACE INTO ascii2d(media_id, seq, status, result) VALUES(?, 0, 'ready', ?)", (mid, _j.dumps(res)))
    try:
        assert ascii2d.result(mid).get("auto") is None, "預設關閉"
        client.post("/api/settings", json={"ascii2d_auto_accept": "same"})
        assert ascii2d.result(mid)["auto"] == "accept"
        res["results"][0]["match"] = "likely"
        with db.write_lock:
            db.conn().execute("UPDATE ascii2d SET result=? WHERE media_id=?", (_j.dumps(res), mid))
        assert ascii2d.result(mid).get("auto") is None, "只有同一張才自動"
        client.post("/api/settings", json={"ascii2d_auto_accept": "likely"})
        assert ascii2d.result(mid)["auto"] == "accept"
        res["tags"] = []
        with db.write_lock:
            db.conn().execute("UPDATE ascii2d SET result=? WHERE media_id=?", (_j.dumps(res), mid))
        assert ascii2d.result(mid)["auto"] == "accept", "沒有標籤可加（作品被刪）也自動：只記出處、下一張"
        res["results"][0]["match"] = "unlikely"
        with db.write_lock:
            db.conn().execute("UPDATE ascii2d SET result=? WHERE media_id=?", (_j.dumps(res), mid))
        assert ascii2d.result(mid).get("auto") != "accept", "比對不上：不自動確認"
        js = client.get("/api/fetch-url/userscript.user.js").text
        assert "@version      3.3" in js and 'r.auto === "accept"' in js
    finally:
        client.post("/api/settings", json={"ascii2d_auto_accept": "off"})
        with db.write_lock:
            db.conn().execute("DELETE FROM ascii2d WHERE media_id=?", (mid,))


def test_ascii2d_decide_counts_as_edited(client):
    """ascii2d 查過（沒找到也算）→ 排序「最近操作」排在前面；舊資料用 decided_at 補（遷移 v10）。"""
    import sqlite3
    from app import ascii2d, db
    mid = ids_by_name(client)["IMG_9003.jpg"]
    with db.write_lock:
        db.conn().execute("INSERT OR REPLACE INTO ascii2d(media_id, seq, status, result) VALUES(?, 0, 'ready', '{}')", (mid,))
        db.conn().execute("UPDATE media SET edited_at=NULL WHERE id=?", (mid,))
    ascii2d.decide(mid, "none")
    top = client.get("/api/media", params={"view": "all", "nsfw": "show", "sort": "edited", "limit": 1}).json()["items"][0]["id"]
    assert top == mid
    c = sqlite3.connect(":memory:")
    c.executescript("CREATE TABLE media(id INTEGER PRIMARY KEY, edited_at REAL); CREATE TABLE ascii2d(media_id INTEGER, decided_at REAL);"
                    "INSERT INTO media VALUES(1, NULL),(2, 50),(3, 5); INSERT INTO ascii2d VALUES(1, 10),(2, 20),(3, NULL);")
    db._m10_edited_ascii2d(c)
    assert dict(c.execute("SELECT id, edited_at FROM media")) == {1: 10, 2: 50, 3: 5}


def test_ascii2d_pixiv_gone_uses_author_link(client):
    """Pixiv 作品被刪、帳號被封：ascii2d 頁面上還有作者連結（pixiv.net/users/ID）→ 用它找作者（Danbooru 反查，沒登記就用暱稱建立）；
    沒勾任何標籤時確認＝只記出處。"""
    from app import ascii2d, db
    items = [{"links": [{"href": "https://www.pixiv.net/artworks/99990001", "text": "魔法少女"},
                        {"href": "https://www.pixiv.net/users/2", "text": "マップさん"}]}]
    r = ascii2d.parse(items)[0]
    assert r["kind"] == "pixiv" and r["user_id"] == "2" and r["author"] == "マップさん"
    mid = ids_by_name(client)["IMG_9001.jpg"]
    out = ascii2d._check_pixiv(r, ascii2d._local_image(mid))
    assert out.get("pixiv_error") and out["pixiv"]["artist_tag"] == "mapped_artist" and out["pixiv"]["gone"]
    out["match"] = "likely"
    tags = ascii2d._tags_for(mid, out)
    assert [t["name"] for t in tags] == ["mapped_artist"] and "ascii2d 上的作者連結" in tags[0]["why"]
    js = client.get("/api/fetch-url/userscript.user.js").text
    assert "if (a.length || matched)" in js
    assert "done.has(key)" in js, "縮圖名額每篇作品只算一次"


def test_ascii2d_auto_none_gone_pixiv_below_unlikely(client):
    """比不了的只有抓不到的 Pixiv 作品、排在不太像的後面 → 自動記沒找到；比不了的是 X、或排在最前面 → 停下來給你看。"""
    import json as _j
    from app import ascii2d, db
    mid = ids_by_name(client)["IMG_9001.jpg"]
    def run(rs):
        res = {"results": rs, "best": len(rs) - 1, "tags": [{"name": "zz_(tst)", "category": "artist", "why": "", "checked": False}]}
        with db.write_lock:
            db.conn().execute("INSERT OR REPLACE INTO ascii2d(media_id, seq, status, result) VALUES(?, 0, 'ready', ?)", (mid, _j.dumps(res)))
        return ascii2d.result(mid).get("auto")
    U = lambda rank, kind="pixiv": {"rank": rank, "kind": kind, "url": "u", "match": "unlikely", "score": 0.05}
    gone = {"rank": 5, "kind": "pixiv", "url": "u", "match": None, "pixiv_error": "作品不存在"}
    try:
        assert run([U(1), U(3, "x"), gone]) == "none"
        assert run([{**gone, "rank": 1}, U(3)]) is None, "比不了的排在最前面：停下來"
        assert run([U(1), {**gone, "kind": "x"}]) == "none", "比不了的是 X（推文被刪、縮圖壞掉）也一樣"
        assert run([{**gone, "rank": 1}, {**gone, "rank": 2}]) is None, "全部都比不了：停下來"
        assert run([]) == "none", "這頁沒有 X、Pixiv 的結果：自動沒找到"
        # 全自動（b80）：上面停下來的情況也自己決定
        client.post("/api/settings", json={"ascii2d_auto_accept": "full"})
        assert run([{**gone, "rank": 1}, U(3)]) == "none", "全自動：排第一的比不了、其他不像 → 沒找到"
        assert run([{**gone, "rank": 1}, {**gone, "rank": 2}]) == "none"
        res = {"results": [{"rank": 1, "kind": "x", "url": "u"}], "error": "ConnectionError: x"}   # 比對過程出錯（沒有 best）→ 先跳過
        with db.write_lock:
            db.conn().execute("UPDATE ascii2d SET result=? WHERE media_id=?", (_j.dumps(res), mid))
        r = ascii2d.result(mid)
        assert r["auto"] == "skip" and r["auto_note"]
        assert ascii2d.counts()["full"] is True
    finally:
        client.post("/api/settings", json={"ascii2d_auto_accept": "off"})
        with db.write_lock:
            db.conn().execute("DELETE FROM ascii2d WHERE media_id=?", (mid,))


def test_ascii2d_old_script_nag(client):
    """腳本比程式舊：結果裡帶提醒（舊腳本也會顯示 error），新版不帶。"""
    import json as _j
    from app import db
    from app.importer import import_key
    mid = ids_by_name(client)["IMG_9001.jpg"]
    with db.write_lock:
        db.conn().execute("INSERT OR REPLACE INTO ascii2d(media_id, seq, status, result) VALUES(?, 0, 'ready', ?)",
                          (mid, _j.dumps({"results": [], "best": None, "tags": []})))
    try:
        h = {"X-Import-Key": import_key()}
        assert "舊版 1.9" in client.get(f"/api/ascii2d/ext/result/{mid}", headers={**h, "X-Script-Version": "1.9"}).json()["error"]
        assert "error" not in client.get(f"/api/ascii2d/ext/result/{mid}", headers={**h, "X-Script-Version": "3.3"}).json()
    finally:
        with db.write_lock:
            db.conn().execute("DELETE FROM ascii2d WHERE media_id=?", (mid,))


def test_ascii2d_queue_skips_images_you_marked_meanwhile(client):
    """排進佇列以後才標了梗圖（作者不可考）、按了略過或「不用找作者」→ 輪到時直接跳過（b55）。"""
    from app import ascii2d, db
    c = db.conn()
    names = ids_by_name(client)
    ids = sorted(i for i in names.values())[:3]
    meme = db.tag_id("梗圖", "custom")
    with db.write_lock:
        c.execute("DELETE FROM ascii2d WHERE status IN ('queued','searching','ready')")
        prev = {i: c.execute("SELECT unrec_skip, artist_done FROM media WHERE id=?", (i,)).fetchone() for i in ids}
        prevnoart = c.execute("SELECT no_artist FROM tags WHERE id=?", (meme,)).fetchone()[0]
        for k, i in enumerate(ids):
            # picked：前面的測試可能給了這幾張創作者（b75 起有創作者的會被拿掉），這裡測的是其他理由
            c.execute("INSERT OR REPLACE INTO ascii2d(media_id, seq, status, picked) VALUES(?, ?, 'queued', 1)", (i, 9e6 + k))
        c.execute("UPDATE media SET unrec_skip=0, artist_done=0 WHERE id IN (?,?,?)", ids)
        c.execute("UPDATE tags SET no_artist=1 WHERE id=?", (meme,))
        c.execute("INSERT OR REPLACE INTO media_tags(media_id, tag_id, score, source, state) VALUES(?,?,NULL,'manual',1)", (ids[0], meme))
        c.execute("UPDATE media SET artist_done=1 WHERE id=?", (ids[1],))
        c.commit()
    try:
        assert ascii2d.next_item()["mid"] == ids[2]
        st = dict(c.execute(f"SELECT media_id, status FROM ascii2d WHERE media_id IN ({ids[0]},{ids[1]})").fetchall())
        assert st == {ids[0]: "skipped", ids[1]: "skipped"}
    finally:
        with db.write_lock:
            c.execute("DELETE FROM media_tags WHERE media_id=? AND tag_id=?", (ids[0], meme))
            c.execute("UPDATE tags SET no_artist=? WHERE id=?", (prevnoart, meme))
            for i, (u, ad) in prev.items():
                c.execute("UPDATE media SET unrec_skip=?, artist_done=? WHERE id=?", (u, ad, i))
            c.execute(f"DELETE FROM ascii2d WHERE media_id IN ({','.join(map(str, ids))})")
            c.commit()


def test_ascii2d_queue_finds_likely_first(client):
    """依找到率分層（b56）：插畫（手機存的 IMG_xxxx 最前）→ 有字／漫畫 → 梗圖；範圍照這個順序。"""
    from app import ascii2d, db
    c = db.conn()
    ids = sorted(ids_by_name(client).values())[:4]
    prev = {i: c.execute("SELECT pile FROM media WHERE id=?", (i,)).fetchone()[0] for i in ids}
    with db.write_lock:
        c.execute("DELETE FROM ascii2d WHERE status IN ('queued','searching','ready')")
        for pile, i in zip(("meme", "texty", "illust", "meme"), ids):
            c.execute("UPDATE media SET pile=? WHERE id=?", (pile, i))
            c.execute("INSERT OR REPLACE INTO ascii2d(media_id, seq, status) VALUES(?, ?, 'queued')", (i, 8e6 + ids.index(i)))
        c.commit()
    try:
        tier = ascii2d.yield_tiers(ids)
        assert tier[ids[2]] in (0, 1) and tier[ids[1]] == 2 and tier[ids[0]] == tier[ids[3]] == 3
        t = [i for i in ascii2d.targets() if i in ids]
        assert t.index(ids[2]) < t.index(ids[0]) if ids[2] in t and ids[0] in t else True
    finally:
        with db.write_lock:
            for i, p in prev.items():
                c.execute("UPDATE media SET pile=? WHERE id=?", (p, i))
            c.execute(f"DELETE FROM ascii2d WHERE media_id IN ({','.join(map(str, ids))})")
            c.commit()


def test_ascii2d_full_auto_character_conflict(client):
    """全自動（b80）：Danbooru 的角色跟你確認的不一樣 → 不停下來：收創作者，衝突的角色不勾（auto=False）。
    其他模式照舊停下來給你看。"""
    import json as _j
    from app import ascii2d, db
    mid = ids_by_name(client)["IMG_9001.jpg"]
    mine = db.tag_id("mine_(tst)", "character")
    res = {"results": [{"rank": 1, "kind": "x", "url": "u", "match": "same", "danbooru": {"characters": ["other_(tst)"]}}], "best": 0,
           "tags": [{"name": "aa_(tst)", "category": "artist", "why": "", "checked": True},
                    {"name": "other_(tst)", "category": "character", "why": "Danbooru", "checked": True}]}
    with db.write_lock:
        db.conn().execute("INSERT OR REPLACE INTO ascii2d(media_id, seq, status, result) VALUES(?, 0, 'ready', ?)", (mid, _j.dumps(res)))
        db.conn().execute("INSERT OR REPLACE INTO media_tags(media_id, tag_id, source, state) VALUES(?,?,'manual',1)", (mid, mine))
        db.conn().commit()
    try:
        client.post("/api/settings", json={"ascii2d_auto_accept": "likely"})
        r = ascii2d.result(mid)
        assert r.get("auto") is None and "已確認的是" in r["error"]
        client.post("/api/settings", json={"ascii2d_auto_accept": "full"})
        r = ascii2d.result(mid)
        assert r["auto"] == "accept" and r["auto_note"]
        t = {x["name"]: x for x in r["tags"]}
        assert t["other_(tst)"].get("auto") is False and t["aa_(tst)"].get("auto") is not False
    finally:
        client.post("/api/settings", json={"ascii2d_auto_accept": "off"})
        with db.write_lock:
            db.conn().execute("DELETE FROM ascii2d WHERE media_id=?", (mid,))
            db.conn().execute("DELETE FROM media_tags WHERE media_id=? AND tag_id=?", (mid, mine))
            db.conn().commit()


def test_ascii2d_thumbnail_prefilter(client, monkeypatch):
    """縮圖先比（b81）：ascii2d 縮圖明顯不像 → 直接「不太像」，不去 Danbooru／X／Pixiv 查；像的照常查。"""
    import base64
    import io

    from PIL import Image

    from app import ascii2d, db, media
    mid = ids_by_name(client)["IMG_9001.jpg"]
    path = db.conn().execute("SELECT path FROM media WHERE id=?", (mid,)).fetchone()[0]

    def durl(img):
        b = io.BytesIO(); img.convert("RGB").save(b, "JPEG"); return "data:image/jpeg;base64," + base64.b64encode(b.getvalue()).decode()
    same = media.load_image(__import__("pathlib").Path(path)).copy(); same.thumbnail((200, 200))
    import numpy as np
    noise = Image.fromarray((np.random.default_rng(0).random((200, 150, 3)) * 255).astype("uint8"))
    from app import danbooru       # 測試的假模型分不出雜訊圖：比對結果用尺寸決定（雜訊圖 150 寬）
    monkeypatch.setattr(danbooru, "compare_images", lambda a, b: 0.1 if b.size[0] == 150 else 0.99)
    calls = []
    monkeypatch.setattr(ascii2d, "_check_x", lambda r, l: calls.append(r["tweet_id"]) or {**r, "score": 0.99})
    monkeypatch.setattr(ascii2d, "_check_pixiv", lambda r, l: calls.append(r["pixiv_id"]) or {**r, "score": 0.99})
    rs = [{"rank": 0, "kind": "x", "tweet_id": "1", "handle": "a", "url": "u1", "thumb_data": durl(noise)},
          {"rank": 1, "kind": "x", "tweet_id": "2", "handle": "b", "url": "u2", "thumb_data": durl(same)},
          {"rank": 2, "kind": "pixiv", "pixiv_id": 3, "url": "u3"}]
    with db.write_lock:
        db.conn().execute("INSERT OR REPLACE INTO ascii2d(media_id, seq, status) VALUES(?, 0, 'searching')", (mid,))
    try:
        res = ascii2d.resolve(mid, rs)
        got = {c["url"]: c for c in res["results"]}
        assert "1" not in calls, "縮圖明顯不像：不再查"
        assert got["u1"]["match"] == "unlikely" and got["u1"].get("quick")
        assert "2" in calls, "縮圖像的照常查"
        assert all("thumb_data" not in c for c in res["results"])
    finally:
        with db.write_lock:
            db.conn().execute("DELETE FROM ascii2d WHERE media_id=?", (mid,))
