"""操作紀錄（op_log）：每個會改資料的操作都記一筆、不會被清掉；記得在哪一頁、用鍵盤還是滑鼠；撤銷會標記。"""
import json


def _mids(n=2):
    from app import db
    return [r[0] for r in db.conn().execute("SELECT id FROM media WHERE status='done' AND kind='image' ORDER BY id LIMIT ?", (n,))]


def _last(kind=None):
    from app import db
    q = "SELECT * FROM op_log" + (" WHERE kind=?" if kind else "") + " ORDER BY id DESC LIMIT 1"
    return db.conn().execute(q, (kind,) if kind else ()).fetchone()


def test_op_kind_strips_counts():
    from app.db import op_kind
    assert op_kind("拿掉「梗圖」（1 張）") == "拿掉「梗圖」"
    assert op_kind("加上 cartethyia_(wuthering_waves)（12 張）＋相似 3 張") == "加上 cartethyia_(wuthering_waves)"
    assert op_kind("確認候選（6 筆）") == "確認候選"
    assert op_kind("ascii2d 確認（2 個標籤）") == "ascii2d 確認"
    assert op_kind("合併 a → b") == "合併 a → b"


def test_every_undoable_action_is_logged_with_page_and_input(client):
    from app import db
    a, b = _mids()
    r = client.post("/api/tags/apply", json={"ids": [a, b], "action": "add", "tag": "oplog_mark_(tst)", "category": "custom"},
                    headers={"X-Lib-View": "tag%2F%E8%8B%A5%E8%91%89%E7%9D%A6", "X-Lib-Via": "key"})
    assert r.status_code == 200
    row = _last("加上 oplog_mark_(tst)")
    assert row, "加標籤要記進操作紀錄"
    assert row["n"] == 2 and sorted(json.loads(row["mids"])) == sorted([a, b])
    assert row["view"] == "tag/若葉睦", "頁面要解碼回中文"
    assert row["via"] == "key"
    assert row["keys"] == "tag_rows" and row["action_id"]
    assert json.loads(row["tids"]) == [db.tag_id("oplog_mark_(tst)", create=False)]
    # 撤銷：原本那筆標上撤銷時間，另外記一筆「撤銷：…」
    assert "oplog_mark" in client.post("/api/undo", json={}).json()["undone"]
    assert db.conn().execute("SELECT undone_at FROM op_log WHERE id=?", (row["id"],)).fetchone()[0]
    u = _last()
    assert u["label"].startswith("撤銷：加上 oplog_mark_(tst)") and u["keys"] == "undo"


def test_log_survives_undo_history_limit(client):
    """撤銷紀錄只留 100 筆；操作紀錄一直留著。"""
    from app import db
    a = _mids(1)[0]
    c = db.conn()
    before = c.execute("SELECT COUNT(*) FROM op_log").fetchone()[0]
    for i in range(105):
        db.push_action(f"測試 {i}（1 張）", {"skip": [[a, 0]]})
    assert c.execute("SELECT COUNT(*) FROM actions").fetchone()[0] == 100
    assert c.execute("SELECT COUNT(*) FROM op_log").fetchone()[0] == before + 105
    rows = c.execute("SELECT view, via FROM op_log ORDER BY id DESC LIMIT 1").fetchone()
    assert rows["view"] is None and rows["via"] is None, "背景（不是從畫面來的）不記頁面"
    with db.write_lock:
        c.execute("DELETE FROM actions WHERE label LIKE '測試 %'")
        c.execute("DELETE FROM op_log WHERE label LIKE '測試 %'")


def test_trash_and_summary_and_csv(client, tmp_path):
    from app import db
    from tests.conftest import make_image, wait_idle, LIB
    p = make_image(LIB / "oplog_trash.jpg", 777)
    client.post("/api/jobs", json={"kind": "scan"})
    wait_idle(client)
    mid = db.conn().execute("SELECT id FROM media WHERE path LIKE ?", ("%oplog_trash.jpg",)).fetchone()[0]
    assert client.post("/api/media/trash", json={"ids": [mid]}, headers={"X-Lib-View": "all", "X-Lib-Via": "mouse"}).json()["trashed"] == 1
    row = _last("移到回收筒")
    assert row and json.loads(row["mids"]) == [mid] and row["view"] == "all" and row["via"] == "mouse"
    s = client.get("/api/oplog/summary?days=7").json()
    assert s["ops"] >= 2 and s["per_day"] and any(k["kind"] == "移到回收筒" for k in s["kinds"])
    assert sum(s["hours"]) == s["ops"]
    lst = client.get("/api/oplog?days=7&limit=5").json()
    assert lst[0]["label"].startswith("移到回收筒") and lst[0]["mids"] == [mid]
    csv = client.get("/api/oplog.csv").text
    assert csv.lstrip("﻿").startswith("時間,說明,種類") and "移到回收筒" in csv
    assert not p.exists()


def test_migration_backfills_from_undo_history(tmp_path):
    """v14 用現有的撤銷紀錄補操作紀錄。"""
    import sqlite3
    from app import db
    c = sqlite3.connect(tmp_path / "x.db")
    c.row_factory = sqlite3.Row
    c.executescript("CREATE TABLE actions(id INTEGER PRIMARY KEY, ts REAL, label TEXT, payload TEXT);")
    c.execute("INSERT INTO actions(ts, label, payload) VALUES(1000, '拿掉「梗圖」（1 張）', ?)", (json.dumps({"tag_rows": [[5, 9, None]]}),))
    c.execute("INSERT INTO actions(ts, label, payload) VALUES(2000, '標完了（2 張）', ?)", (json.dumps({"people_done": [[7, 0], [8, 0]]}),))
    db._m14_op_log(c)
    rows = [dict(r) for r in c.execute("SELECT ts, kind, n, mids, tids, keys, action_id FROM op_log ORDER BY id")]
    assert rows[0] == {"ts": 1000, "kind": "拿掉「梗圖」", "n": 1, "mids": "[5]", "tids": "[9]", "keys": "tag_rows", "action_id": 1}
    assert rows[1]["n"] == 2 and rows[1]["keys"] == "people_done"


def test_frontend_sends_page_and_input_headers():
    src = open("static/js/core.js", encoding="utf-8").read()
    assert '"X-Lib-View"' in src and '"X-Lib-Via"' in src
