"""b94：貼出處網址——圖庫裡已經有的圖，貼 X／Pixiv 貼文網址，比對後記出處、上標籤。"""
import httpx

from tests.conftest import LIB, MOCK_PORT, ids_by_name, wait_idle


def test_paste_source_url(client):
    from app import db
    img = httpx.get(f"http://127.0.0.1:{MOCK_PORT}/media/ZyXwVu98765AbCd.jpg").content       # X 貼文的第 2 張
    pimg = httpx.get(f"http://127.0.0.1:{MOCK_PORT}/img/55556666_p0.png", headers={"Referer": "https://www.pixiv.net/"}).content
    (LIB / "su").mkdir(exist_ok=True)
    (LIB / "su" / "IMG_7301.jpg").write_bytes(img)
    (LIB / "su" / "IMG_7302.png").write_bytes(pimg)
    client.post("/api/jobs", json={"kind": "scan"})
    wait_idle(client)
    ids = ids_by_name(client)
    a, b, other = ids["IMG_7301.jpg"], ids["IMG_7302.png"], ids["img_03.jpg"]
    c = db.conn()
    try:
        # 不支援的網址
        r = client.post("/api/media/source-url/check", json={"ids": [a], "url": "https://example.com/x"})
        assert r.status_code == 400
        # X：兩張選的圖，一張是貼文第 2 張、一張不是
        url = "https://twitter.com/someart/status/1790000000000000001"
        r = client.post("/api/media/source-url/check", json={"ids": [a, other], "url": url})
        assert r.status_code == 200, r.text
        d = r.json()
        it = {x["id"]: x for x in d["items"]}
        assert it[a]["match"] == "same" and it[a]["page"] == 2, it[a]
        assert it[other]["match"] == "unlikely" and it[other]["tags"] == []
        names = {t["name"]: t for t in it[a]["tags"]}
        assert {"ceres_fauna", "hololive"} <= set(names) and names["ceres_fauna"]["checked"], names   # Danbooru 有收：預設勾
        assert d["pages"] == 2 and d["url"] == "https://x.com/someart/status/1790000000000000001"
        # 寫入：勾的才確認、記出處、NSFW（貼文標了敏感）、一筆撤銷
        r = client.post("/api/media/source-url/apply", json={"token": d["token"], "picks": {str(a): ["ceres_fauna"]}})
        assert r.json() == {"applied": 1, "tags": 1}
        st = lambda m, n: c.execute("SELECT state, source FROM media_tags mt JOIN tags t ON t.id=mt.tag_id WHERE mt.media_id=? AND t.name=?", (m, n)).fetchone()
        assert tuple(st(a, "ceres_fauna")) == (1, "url")
        assert st(a, "hololive") is None or st(a, "hololive")[0] != 1 or st(a, "hololive")[1] == "implied"
        src = c.execute("SELECT status, method, source_url FROM media_source WHERE media_id=?", (a,)).fetchone()
        assert tuple(src) == ("found", "url", "https://x.com/someart/status/1790000000000000001")
        assert c.execute("SELECT nsfw_manual FROM media WHERE id=?", (a,)).fetchone()[0] == 1
        assert c.execute("SELECT post_url FROM import_meta WHERE path LIKE '%IMG_7301.jpg'").fetchone()[0].endswith("1790000000000000001")
        assert c.execute("SELECT COUNT(*) FROM media WHERE path LIKE '%IMG_7301%'").fetchone()[0] == 1, "不會另外下載一張"
        client.post("/api/undo", json={})
        assert st(a, "ceres_fauna") is None or st(a, "ceres_fauna")[0] != 1
        # 過期／不存在的 token
        assert client.post("/api/media/source-url/apply", json={"token": "nope", "picks": {str(a): []}}).status_code == 400
        # Pixiv：作者（Danbooru 沒收時用 Pixiv 作者）；只記出處也可以
        r = client.post("/api/media/source-url/check", json={"ids": [b], "url": "https://www.pixiv.net/artworks/55556666"})
        d = r.json()
        x = d["items"][0]
        assert x["match"] == "same" and x["page"] == 0, x
        r = client.post("/api/media/source-url/apply", json={"token": d["token"], "picks": {str(b): []}})
        assert r.json() == {"applied": 1, "tags": 0}
        assert tuple(c.execute("SELECT method, pixiv_id FROM media_source WHERE media_id=?", (b,)).fetchone()) == ("url", 55556666)
        # 抓不到的 Pixiv 作品：看得懂的錯誤
        r = client.post("/api/media/source-url/check", json={"ids": [b], "url": "https://www.pixiv.net/artworks/12121212"})
        assert r.status_code == 400 and "Pixiv" in r.json()["detail"]
    finally:
        with db.write_lock:
            for m in (a, b):
                c.execute("DELETE FROM media_source WHERE media_id=?", (m,))
                c.execute("DELETE FROM media_tags WHERE media_id=? AND source IN ('url','implied')", (m,))
                c.execute("UPDATE media SET nsfw_manual=NULL WHERE id=?", (m,))
            c.commit()
