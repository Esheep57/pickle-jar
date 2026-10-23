"""PixAI Tagger（b57）：關著不跑；開了一塊一塊做、結果存著；評估分 Danbooru／你的答案；
開「放進待確認」才變候選（tier 4、source pixai），門檻以下不列、你否決過的不再列、梗圖不列；換門檻不用重跑。"""
import json

from tests.conftest import ids_by_name, wait_idle


def test_pixai_runs_stores_and_candidates(client):
    from app import db, pixai
    c = db.conn()
    # 第一張要是「還沒有確認角色／作品」的圖（已經有的不會再推作品）；前面的測試會在一些圖上確認東西
    known = {r[0] for r in c.execute("""SELECT mt.media_id FROM media_tags mt JOIN tags t ON t.id=mt.tag_id
                                        WHERE mt.state=1 AND t.category IN ('character','copyright')""")}
    known |= {r[0] for r in c.execute("SELECT id FROM media WHERE COALESCE(unrec_skip,0)>0")}
    known |= {r[0] for r in c.execute("""SELECT mt.media_id FROM media_tags mt JOIN tags t ON t.id=mt.tag_id
                                         WHERE mt.state=1 AND t.name IN ('梗圖','截圖')""")}
    allids = [i for i in sorted(ids_by_name(client).values())
              if tuple(c.execute("SELECT kind, status FROM media WHERE id=?", (i,)).fetchone()) == ("image", "done")]
    ids = [next(i for i in allids if i not in known)] + [i for i in allids if i not in known][1:6]
    c0 = c.execute("SELECT COUNT(*) FROM pixai_pred").fetchone()[0]
    assert client.post("/api/pixai/run", json={}).status_code == 400          # 關著：不跑
    client.post("/api/settings", json={"pixai_enabled": True})
    try:
        client.post("/api/pixai/run", json={"ids": ids})
        wait_idle(client, timeout=120)
        rows = {r[0]: r for r in c.execute(f"SELECT media_id, chars, artists, copyrights, error FROM pixai_pred WHERE media_id IN ({','.join(map(str, ids))})")}
        assert set(rows) >= {i for i in ids if c.execute("SELECT kind FROM media WHERE id=?", (i,)).fetchone()[0] == "image"}
        r = rows[ids[0]]
        assert json.loads(r[1])["hatsune_miku"] >= 0.9 and "kagamine_rin" in json.loads(r[1]) and json.loads(r[2]) == {"pix_painter_(tst)": 0.9}
        assert c.execute("SELECT COUNT(*) FROM media_tags WHERE source='pixai'").fetchone()[0] == 0   # 還沒開「放進待確認」
        # 你否決過其中一張的 miku；另一張標了梗圖
        miku = db.tag_id("hatsune_miku", "character")
        meme = db.tag_id("梗圖", "custom")
        with db.write_lock:
            c.execute("INSERT OR REPLACE INTO media_tags(media_id, tag_id, score, source, state) VALUES(?,?,NULL,'manual',-1)", (ids[1], miku))
            c.execute("INSERT OR REPLACE INTO media_tags(media_id, tag_id, score, source, state) VALUES(?,?,NULL,'manual',1)", (ids[2], meme))
            c.execute("INSERT OR REPLACE INTO media_tags(media_id, tag_id, score, source, state) VALUES(?,?,NULL,'manual',1)", (ids[3], miku))
            c.execute("DELETE FROM media_source WHERE media_id IN (?,?,?,?)", ids[:4])
        client.post("/api/settings", json={"pixai_candidates": True})
        for _ in range(50):
            import time; time.sleep(0.1)
            if c.execute("SELECT COUNT(*) FROM media_tags WHERE source='pixai' AND media_id=?", (ids[0],)).fetchone()[0] >= 2:
                break
        got = lambda mid: {r[0]: (r[1], r[2]) for r in c.execute("""SELECT t.name, mt.state, mt.tier FROM media_tags mt JOIN tags t ON t.id=mt.tag_id
                                                        WHERE mt.media_id=? AND mt.source='pixai'""", (mid,))}
        g0 = got(ids[0])
        assert g0.get("hatsune_miku") == (0, 4) and g0.get("pix_painter_(tst)") == (0, 4)
        other_vocaloid = c.execute("""SELECT 1 FROM media_tags mt JOIN tags t ON t.id=mt.tag_id WHERE mt.media_id=? AND t.name='vocaloid'
                                      AND mt.source!='pixai'""", (ids[0],)).fetchone()
        assert g0.get("vocaloid") == (0, 4) or other_vocaloid       # 前面的測試可能已經用別的來源放了 vocaloid
        assert "kagamine_rin" not in g0                                 # 0.5 < 門檻 0.7
        assert "hatsune_miku" not in got(ids[1])                        # 你否決過
        assert "vocaloid" not in got(ids[3])                            # 已經確認了角色：作品不用再推
        assert got(ids[2]) == {} or "hatsune_miku" not in got(ids[2])   # 梗圖不認角色
        # 門檻放低：不用重跑模型就多出 rin
        client.post("/api/settings", json={"pixai_char_threshold": 0.4})
        for _ in range(50):
            time.sleep(0.1)
            if "kagamine_rin" in got(ids[0]):
                break
        assert "kagamine_rin" in got(ids[0])
        ev = client.get("/api/diag/eval-pixai").json()
        e = ev["eval"]["character"]["0.7"]
        assert e["yours"]["right"] + e["wd"]["right"] >= 1 and e["yours"]["wrong"] >= 1     # ids[3] 你確認的 miku 對了；ids[1] 你否決的算錯
        assert ev["stats"]["done"] >= len(ids) - 1 and ev["eval"]["your_characters"]["pixai_knows"] >= 1
    finally:
        client.post("/api/settings", json={"pixai_enabled": False, "pixai_candidates": False, "pixai_char_threshold": 0.7})
        with db.write_lock:
            c.execute("DELETE FROM media_tags WHERE source='pixai'")
            c.execute(f"DELETE FROM media_tags WHERE media_id IN ({','.join(map(str, ids[:4]))}) AND source='manual' AND tag_id IN (?,?)",
                      (db.tag_id("hatsune_miku", "character"), db.tag_id("梗圖", "custom")))
            c.execute("DELETE FROM pixai_pred")
        wait_idle(client)


def test_pixai_worker_crash_does_not_kill_app(client, monkeypatch):
    """子程式在某張圖崩潰：主程式照常、那張記失敗；連續兩次就自動關掉 PixAI、原因進「程式出錯」。"""
    from app import db, pixai
    c = db.conn()
    ids = sorted(ids_by_name(client).values())[:3]
    monkeypatch.setenv("LIB_PIXAI_CRASH_ON", str(ids[1]))
    client.post("/api/settings", json={"pixai_enabled": True})
    try:
        with db.write_lock:
            c.execute("DELETE FROM pixai_pred")
        db.update_settings({"_pixai_crashes": 0})
        for _ in range(2):
            client.post("/api/pixai/run", json={"ids": ids})
            wait_idle(client, timeout=120)
        assert client.get("/api/status").status_code == 200                      # 主程式還活著
        rows = dict(c.execute(f"SELECT media_id, error FROM pixai_pred WHERE media_id IN ({','.join(map(str, ids))})").fetchall())
        assert rows.get(ids[0]) is None and "結束" in (rows.get(ids[1]) or "")    # 第一張好好的，崩潰那張記失敗
        assert db.get_settings()["pixai_enabled"] is False and db.get_settings()["_pixai_crashes"] >= 2
        assert pixai.stats()["disabled"]
    finally:
        client.post("/api/settings", json={"pixai_enabled": False})
        db.update_settings({"_pixai_crashes": 0})
        with db.write_lock:
            c.execute("DELETE FROM pixai_pred")
        wait_idle(client)
