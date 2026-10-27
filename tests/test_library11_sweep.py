"""從畫師作品全集反查（b57）：Pixiv 帳號從資料卡連結找；作品縮圖跟本機圖比對 → 記出處、確認畫師；
看過的不再下載；X 由腳本送網址；不是圖庫裡的帳號不收。"""
import json
import time

import httpx

from tests.conftest import MOCK_PORT, ids_by_name, wait_idle

BASE = f"http://127.0.0.1:{MOCK_PORT}"


def _setup(c, db, mid_seed):
    art = db.tag_id("sweep_painter_(tst)", "artist")
    from app.danbooru import save_tag_info
    save_tag_info(art, {"status": "ok", "copyright": None, "copyrights": [], "summary": "", "post_count": None, "other_names": [],
                        "url": "https://www.pixiv.net/users/4242", "links": ["https://www.pixiv.net/users/4242", "https://x.com/SweepPainter"]})
    with db.write_lock:
        c.execute("INSERT OR REPLACE INTO media_tags(media_id, tag_id, score, source, state) VALUES(?,?,NULL,'manual',1)", (mid_seed, art))
    return art


def test_pixiv_and_x_sweep(client):
    from app import db, sweep
    c = db.conn()
    names = ids_by_name(client)
    seed, target, xtarget = names["img_02.jpg"], names["img_05.jpg"], names["img_10.jpg"]   # 普通的圖（假網站不認得、沒有出處）
    paths = {r[0]: r[1] for r in c.execute("SELECT id, path FROM media")}
    with db.write_lock:
        # 記成「搜過、沒找到」：背景自動搜源不會先去搜這兩張（不然有時候搜源先找到出處，比對就沒對象了）
        for m in (target, xtarget):
            c.execute("INSERT OR REPLACE INTO media_source(media_id, status, method, fetched_at, data) VALUES(?,'notfound','iqdb',?,'{}')",
                      (m, time.time()))
    art = _setup(c, db, seed)
    was = db.get_settings()
    was_online, was_auto = was["online_enabled"], was.get("online_auto_source", True)
    try:
        a = {x["key"]: x for x in sweep.artists("pixiv")}
        assert a["4242"]["artist"] == "sweep_painter_(tst)"
        assert "sweeppainter" in {x["key"] for x in sweep.artists("x")}
        httpx.post(BASE + "/_sweep", json={"801_p0": paths[target], "SWXMEDIA0001": paths[xtarget]})
        client.post("/api/settings", json={"online_enabled": True, "online_auto_source": False})
        r = client.post("/api/sweep/pixiv", json={"keys": ["4242"]}).json()
        assert r["queued"] == 1
        for _ in range(600):
            time.sleep(0.2)
            # 出處先記、畫師標籤後補：兩個都有了才往下
            if c.execute("SELECT 1 FROM media_source WHERE media_id=? AND method='pixiv_scan'", (target,)).fetchone() and \
                    c.execute("SELECT 1 FROM media_tags WHERE media_id=? AND tag_id=? AND source='pixiv_scan'", (target, art)).fetchone():
                break
        src = c.execute("SELECT status, pixiv_id, post_url FROM media_source WHERE media_id=?", (target,)).fetchone()
        assert tuple(src) == ("found", 801, "https://www.pixiv.net/artworks/801")
        st = c.execute("SELECT state, source FROM media_tags WHERE media_id=? AND tag_id=?", (target, art)).fetchone()
        assert tuple(st) == (1, "pixiv_scan")
        for _ in range(100):
            time.sleep(0.1)
            r = c.execute("SELECT thumbs FROM sweep_artist WHERE site='pixiv' AND key='4242'").fetchone()
            if r and r[0] >= 4:          # 整位畫師看完（列先建、張數後補；只等到「有這列」偶爾會比太早）
                break
        row = c.execute("SELECT works, thumbs, matched, last_max FROM sweep_artist WHERE site='pixiv' AND key='4242'").fetchone()
        assert row[0] == 3 and row[1] == 4 and row[2] == 1 and row[3] == "803"        # 3 件作品 4 頁；最新 803
        assert {r[0] for r in c.execute("SELECT item FROM sweep_seen WHERE site='pixiv'")} >= {"801_p0", "802_p0", "802_p1", "803_p0"}
        # X：腳本送來網址；不是圖庫裡的帳號 → 404
        from app import importer
        H = {"X-Import-Key": importer.import_key()}
        assert client.post("/api/sweep/ext/x/items", headers=H, json={"handle": "nobody", "items": []}).status_code == 404
        assert client.post("/api/sweep/ext/x/check", headers=H, json={"handle": "SweepPainter"}).json()["artist"] == "sweep_painter_(tst)"
        assert client.post("/api/sweep/x/queue", json={"handles": ["SweepPainter"]}).json()["queued"] == 1
        assert client.post("/api/sweep/ext/x/next", headers=H).json()["handle"] == "sweeppainter"
        items = [{"tweet_id": "1900000000000000001", "url": "https://pbs.twimg.com/media/SWXMEDIA0001?format=jpg&name=small"},
                 {"tweet_id": "1900000000000000002", "url": "https://pbs.twimg.com/media/SWXMEDIA0002?format=jpg&name=360x360"}]
        assert client.post("/api/sweep/ext/x/items", headers=H, json={"handle": "SweepPainter", "items": items, "final": True}).status_code == 200
        for _ in range(600):   # 比對要載模型、算特徵，慢的機器（CI）要等比較久
            time.sleep(0.2)
            if c.execute("SELECT 1 FROM media_source WHERE media_id=? AND method='x_scan'", (xtarget,)).fetchone():
                break
        xs = c.execute("SELECT post_url FROM media_source WHERE media_id=?", (xtarget,)).fetchone()
        assert xs[0] == "https://x.com/SweepPainter/status/1900000000000000001"
        for _ in range(50):
            time.sleep(0.1)
            if c.execute("SELECT status FROM sweep_xqueue WHERE handle='sweeppainter'").fetchone()[0] == "done":
                break
        assert c.execute("SELECT status FROM sweep_xqueue WHERE handle='sweeppainter'").fetchone()[0] == "done"
        assert client.post("/api/sweep/ext/x/next", headers=H).json()["handle"] is None
        assert client.post("/api/sweep/ext/x/next").status_code == 403          # 沒帶金鑰
        s = client.get("/api/sweep").json()
        assert s["pixiv"]["found_images"] >= 1 and s["x"]["found_images"] >= 1
    finally:
        with db.write_lock:
            c.execute("DELETE FROM media_tags WHERE tag_id=?", (art,))
            c.execute("DELETE FROM media_source WHERE media_id IN (?,?)", (target, xtarget))
            c.execute("DELETE FROM sweep_artist")
            c.execute("DELETE FROM sweep_seen")
            c.execute("DELETE FROM sweep_xqueue")
        client.post("/api/settings", json={"online_enabled": was_online, "online_auto_source": was_auto})
        wait_idle(client)
