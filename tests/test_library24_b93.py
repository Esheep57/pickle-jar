"""b93：角色 ⇒ 作品、暫時性錯誤、回應速度、背景工作登記表、事件流、縮圖快取、快速標籤學習、ascii2d 提示。"""
import json
import threading
import time

import numpy as np


def _wipe(c, mids, tids):
    from app import db
    with db.write_lock:
        ph, th = ",".join("?" * len(mids)), ",".join("?" * len(tids))
        c.execute(f"DELETE FROM media_tags WHERE media_id IN ({ph}) AND tag_id IN ({th})", [*mids, *tids])
        c.commit()


def test_character_implies_its_copyrights(client):
    """確認角色 → 資料卡裡的所屬作品一起確認；否決角色 → 只因為它才出現的作品候選一起收掉。"""
    from app import db, implies
    from tests.conftest import ids_by_name
    c = db.conn()
    ids = ids_by_name(client)
    m1, m2 = ids["img_09.jpg"], ids["img_10.jpg"]
    with db.write_lock:
        ch, ch2, var = (db.tag_id(n, "character") for n in ("cpa_char", "cpb_char", "cpa_char_(swim)"))
        s1, s2, s3 = (db.tag_id(n, "copyright") for n in ("cpa_series", "cpa_series_sub", "cpb_series"))
        for t, cps in ((ch, ["cpa_series", "cpa_series_sub", "not_local_series"]), (ch2, ["cpb_series"]), (var, ["cpa_series"])):
            c.execute("INSERT OR REPLACE INTO tag_info(tag_id, status, copyright, copyrights, post_count, fetched_at) VALUES(?, 'ok', ?, ?, 10, ?)",
                      (t, cps[0], json.dumps(cps), time.time()))
        c.execute("INSERT OR REPLACE INTO tag_implies(tag_id, implies_id, src, created_at) VALUES(?,?,'danbooru',?)", (var, ch, time.time()))
        c.commit()
    implies.invalidate()
    r = implies.sync_copyrights()
    assert r["added"] >= 4
    rules = {(a, b): s for a, b, s in c.execute("SELECT tag_id, implies_id, src FROM tag_implies WHERE tag_id IN (?,?,?)", (ch, ch2, var))}
    assert rules[(ch, s1)] == rules[(ch, s2)] == "danbooru_copy", "圖庫裡沒有的作品（not_local_series）不收"
    assert len([k for k in rules if k[0] == ch]) == 2
    try:
        # 確認造型 ⇒ 本體 ⇒ 作品（一路帶上），一筆撤銷
        client.post("/api/tags/apply", json={"ids": [m1], "action": "add", "tag": "cpa_char_(swim)", "category": "character"})
        st = lambda m, t: (c.execute("SELECT state, source FROM media_tags WHERE media_id=? AND tag_id=?", (m, t)).fetchone() or [None, None])
        assert st(m1, ch)[0] == 1 and st(m1, s1)[0] == 1 and st(m1, s2)[0] == 1 and st(m1, s1)[1] == "implied"
        client.post("/api/undo", json={})
        assert st(m1, s1)[0] is None and st(m1, ch)[0] is None
        # 否決過的作品不動
        with db.write_lock:
            c.execute("INSERT INTO media_tags(media_id, tag_id, source, state) VALUES(?,?,'manual',-1)", (m1, s2))
            c.commit()
        client.post("/api/tags/apply", json={"ids": [m1], "action": "add", "tag": "cpa_char", "category": "character"})
        assert st(m1, s1)[0] == 1 and st(m1, s2)[0] == -1
        # 否決候選角色：只因為它才在的作品候選收掉；別的角色也屬於的留著；確認的不動
        with db.write_lock:
            for t, s in ((ch, 0), (ch2, 0), (s1, 0), (s2, 0), (s3, 0)):
                c.execute("""INSERT INTO media_tags(media_id, tag_id, source, state, score) VALUES(?,?,'model',?,0.9)
                             ON CONFLICT(media_id, tag_id) DO UPDATE SET state=excluded.state, source='model'""", (m2, t, s))
            c.commit()
        client.post("/api/tags/apply", json={"ids": [m2], "action": "reject", "tag": "cpa_char"})
        assert st(m2, ch)[0] == -1
        assert st(m2, s1)[0] is None and st(m2, s2)[0] is None, "只因為 cpa_char 才有的作品候選收掉（刪掉，不是否決）"
        assert st(m2, s3)[0] == 0, "cpb_series 是別的角色（cpb_char 候選）的作品：留著"
        client.post("/api/undo", json={})
        assert st(m2, s1)[0] == 0 and st(m2, ch)[0] == 0
        # 資料卡改了：規則跟著改；你關掉的（off）不加回來
        implies.set_off("cpa_char", "cpa_series_sub")
        with db.write_lock:
            c.execute("UPDATE tag_info SET copyrights=? WHERE tag_id=?", (json.dumps(["cpa_series"]), ch))
            c.commit()
        implies.sync_copyrights([ch])
        rules = {(a, b): s for a, b, s in c.execute("SELECT tag_id, implies_id, src FROM tag_implies WHERE tag_id=?", (ch,))}
        assert rules[(ch, s2)] == "off" and rules[(ch, s1)] == "danbooru_copy"
        # 角色 ⇒ 作品不影響「Danbooru 說不是同一個人」（只看角色 ⇒ 角色）
        with db.write_lock:
            c.execute("INSERT OR REPLACE INTO implies_checked(tag_id, checked_at) VALUES(?,?)", (ch2, time.time()))
            c.commit()
        assert ch2 in implies.danbooru_says_no()
        assert implies.summary()["danbooru_copy"] >= 3
    finally:
        _wipe(c, [m1, m2], [ch, ch2, var, s1, s2, s3])
        with db.write_lock:
            c.execute("DELETE FROM tag_implies WHERE tag_id IN (?,?,?)", (ch, ch2, var))
            c.execute("DELETE FROM implies_checked WHERE tag_id=?", (ch2,))
            c.execute("DELETE FROM tag_info WHERE tag_id IN (?,?,?)", (ch, ch2, var))
            c.commit()
        implies.invalidate()


def test_transient_errors_are_not_program_errors(client, monkeypatch):
    """Danbooru 503／連不上：搜源記成 error（之後重搜），但不進「程式出錯」。"""
    from app import db, errlog, online
    from app.httpclient import TransientError
    from tests.conftest import ids_by_name
    mid = ids_by_name(client)["img_08.jpg"]
    before = len(errlog.recent()) if hasattr(errlog, "recent") else len(client.get("/api/diag").json()["server_errors"])
    def boom(*a, **k):
        raise TransientError("danbooru 暫時無法使用（HTTP 503）")
    monkeypatch.setattr(online, "lookup_source", boom)
    j = online.get_jobs()
    j._source_one(mid, False, set())
    row = db.conn().execute("SELECT status, error FROM media_source WHERE media_id=?", (mid,)).fetchone()
    assert row["status"] == "error" and "503" in row["error"]
    after = len(errlog.recent()) if hasattr(errlog, "recent") else len(client.get("/api/diag").json()["server_errors"])
    assert after == before
    with db.write_lock:
        db.conn().execute("DELETE FROM media_source WHERE media_id=?", (mid,))
        db.conn().commit()


def test_perf_records_requests_and_lock(client):
    from app import db, perf
    client.get("/api/sidebar")
    with db.write_lock:
        time.sleep(0.01)
    assert any(r["route"] == "GET /api/sidebar" for r in perf.summary(limit=10000)["routes"])
    p = client.get("/api/diag/perf").json()
    assert p["lock"]["acquired"] > 0 and p["lock"]["max_hold_ms"] >= 10
    # 慢請求進清單和 slow.log
    perf.record("GET", "/api/_slow_test", "/api/_slow_test", 200, 1234.0, {"lock_wait": 800.0})
    p = client.get("/api/diag/perf").json()
    assert p["slow"][0]["route"] == "/api/_slow_test" and p["slow"][0]["lock_wait_ms"] == 800.0
    assert "/api/_slow_test" in (db.DATA_DIR / "slow.log").read_text("utf-8")


def test_background_task_registry(client):
    from app import db, jobsview, tasks
    gate = threading.Event()
    seen = {"n": 0}
    st = {"done": 0, "total": 3}
    def work(x):
        seen["n"] += x
        st["done"] = 2
        gate.wait(5)
    t = tasks.spawn("test", "測試工作", work, 1, probe=tasks.probe_of(st), cancel=gate.set)
    time.sleep(0.2)
    ov = jobsview.overview()
    row = next(r for r in ov["running"] if r.get("queue") == "task" and r.get("id") == t.id)
    assert row["done"] == 2 and row["total"] == 3 and row["can_cancel"]
    assert client.post("/api/jobs/cancel", json={"queue": "task", "index": t.id}).json()["ok"]
    for _ in range(50):
        if t.state != "running":
            break
        time.sleep(0.05)
    assert t.state == "cancelled" and seen["n"] == 1
    assert any(h["label"] == "測試工作" and h["stopped"] for h in jobsview.overview()["history"])
    # 失敗：記下錯誤，不讓執行緒默默死掉
    t2 = tasks.spawn("test", "會失敗的", lambda: 1 / 0)
    for _ in range(50):
        if t2.state != "running":
            break
        time.sleep(0.05)
    assert t2.state == "failed" and "ZeroDivisionError" in t2.error
    # 重的工作同時最多 2 個
    g2 = threading.Event()
    hs = [tasks.spawn("test", f"重的 {i}", g2.wait, 5, heavy=True) for i in range(3)]
    time.sleep(0.3)
    assert sorted(h.state for h in hs) == ["running", "running", "waiting"]
    g2.set()
    for _ in range(50):
        if all(h.state == "done" for h in hs):
            break
        time.sleep(0.05)
    assert all(h.state == "done" for h in hs)
    # 重開程式：還在跑的 → 中斷了
    g3 = threading.Event()
    t3 = tasks.spawn("test", "做到一半", g3.wait, 5)
    tasks.on_startup()
    assert any(x["label"] == "做到一半" for x in jobsview.overview()["interrupted"])
    client.post("/api/jobs/interrupted-seen", json={})
    assert not jobsview.overview()["interrupted"]
    g3.set()


def test_events_stream_once(client):
    r = client.get("/api/events", params={"once": 1})
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream")
    ev = {}
    for block in r.text.split("\n\n"):
        lines = dict(l.split(": ", 1) for l in block.splitlines() if ": " in l and not l.startswith(":"))
        if "event" in lines:
            ev[lines["event"]] = json.loads(lines["data"])
    assert {"status", "tasks", "knn"} <= set(ev)
    assert "pending" in ev["status"] and "v" in ev["tasks"] and "version" in ev["knn"]


def test_thumb_versioned_url_is_cached(client):
    items = client.get("/api/media", params={"nsfw": "show", "limit": 5}).json()["items"]
    it = next(x for x in items if x["tv"])
    r = client.get(f"/t/{it['id']}", params={"v": it["tv"]})
    assert r.status_code == 200 and "immutable" in r.headers["cache-control"]
    assert client.get(f"/t/{it['id']}").headers["cache-control"] == "no-cache"


def test_quick_tag_learning(client, monkeypatch):
    """快速標籤從你的標記學：guess:名字 找得到 AI 覺得也是的；你拿掉的不再建議。"""
    from app import db, qtlearn, quicktags
    c = db.conn()
    rows = c.execute("SELECT id FROM media WHERE status='done' AND embedding IS NOT NULL ORDER BY id").fetchall()
    ids = [r[0] for r in rows]
    assert len(ids) >= 10
    # 假的特徵：前一半是「A 類」（某個方向很大），後一半是別的；只標 A 類的一部分
    rng = np.random.default_rng(0)
    old = {m: e for m, e in c.execute(f"SELECT id, embedding FROM media WHERE id IN ({','.join('?' * len(ids))})", ids)}
    half = ids[: len(ids) // 2]
    dim = len(np.frombuffer(next(iter(old.values())), dtype=np.float16))
    try:
        with db.write_lock:
            for m in ids:
                v = rng.normal(0, 1, dim).astype(np.float16)
                if m in half:
                    v[:8] += 6
                c.execute("UPDATE media SET embedding=? WHERE id=?", (v.tobytes(), m))
            c.commit()
        name = "qtl測試"
        labeled = half[:-2]
        monkeypatch.setattr(qtlearn, "MIN_POS", 3)
        # 測試圖庫只有二十幾張、特徵 1024 維：任何線性模型都會把訓練標籤背下來（沒標的 A 類被當成「不是」）。
        # 準確度在開發時的資料上量過（qtlearn.py 開頭）；這裡換成「看前 8 維」的模型，只測流程：存、查、拿掉的不再建議。
        def fake_fit(X, y, **k):
            w = np.zeros(X.shape[1], np.float32); w[:8] = 1.0
            return w, -4.0
        monkeypatch.setattr(qtlearn, "_fit", fake_fit)
        monkeypatch.setattr(quicktags, "names", lambda: [name])
        monkeypatch.setattr(qtlearn, "schedule", lambda *a, **k: None)
        quicktags.toggle(labeled, name, on=True)
        res = qtlearn.run([name])
        assert res[name] >= 1
        got = {x["id"] for x in client.get("/api/media", params={"q": f"guess:{name}", "nsfw": "show", "limit": 100}).json()["items"]}
        assert got and got <= set(half) - set(labeled), "建議的都是 A 類、還沒標的"
        assert quicktags.summary(nsfw="show")[0]["hint"] == f"guess:{name}"
        # 拿掉的：之後不建議
        quicktags.toggle([labeled[0]], name, on=False)
        qtlearn.run([name])
        got2 = {x["id"] for x in client.get("/api/media", params={"q": f"guess:{name}", "nsfw": "show", "limit": 100}).json()["items"]}
        assert labeled[0] not in got2
        # 再加回去：不再當成「不是」
        quicktags.toggle([labeled[0]], name, on=True)
        assert not c.execute("SELECT 1 FROM qt_no WHERE media_id=? AND tag_id=?", (labeled[0], db.tag_id(name, create=False))).fetchone()
    finally:
        with db.write_lock:
            for m, e in old.items():
                c.execute("UPDATE media SET embedding=? WHERE id=?", (e, m))
            tid = db.tag_id("qtl測試", create=False)
            if tid:
                c.execute("DELETE FROM media_tags WHERE tag_id=?", (tid,))
                c.execute("DELETE FROM qt_pred WHERE tag_id=?", (tid,))
                c.execute("DELETE FROM qt_no WHERE tag_id=?", (tid,))
            c.execute("UPDATE media SET unrec_skip=0 WHERE unrec_skip=2")
            c.commit()


def test_home_ascii2d_tip(client, monkeypatch):
    from app import ascii2d, db
    from app.api import system
    system._A2_CACHE.clear()
    db.clear_read_cache()
    monkeypatch.setattr(ascii2d, "targets", lambda *a, **k: list(range(1000, 1050)))
    monkeypatch.setattr(ascii2d, "forecast", lambda ids: {"expect": 12, "minutes": 30})
    tips = client.get("/api/home", params={"nsfw": "show"}).json()["tips"]
    t = next(x for x in tips if x["kind"] == "ascii2d")
    assert t["n"] == 50 and "12" in t["text"]
    system._A2_CACHE.clear()
    db.clear_read_cache()
    monkeypatch.setattr(ascii2d, "targets", lambda *a, **k: [])
    assert not any(x["kind"] == "ascii2d" for x in client.get("/api/home", params={"nsfw": "show"}).json()["tips"])
    system._A2_CACHE.clear()
    db.clear_read_cache()


def test_read_cache_b96(client, monkeypatch):
    """b96：側欄等重讀取有快取；資料一變（WAL）或你按了什麼（非 GET）就重算。"""
    from app import db
    monkeypatch.setattr(db, "BUSY", lambda: False)     # 測試時背景可能有工作在跑（那時 grace 秒內沿用）
    monkeypatch.setattr(db, "_bg_at", 0.0)              # 瀏覽器腳本剛送過東西也一樣（b97）
    db.clear_read_cache()
    n = [0]

    def f():
        n[0] += 1
        return n[0]
    assert db.read_cache("t", (1,), f) == 1
    assert db.read_cache("t", (1,), f) == 1          # 沒變：沿用
    assert db.read_cache("t", (2,), f) == 2          # 參數不同：分開算
    db.user_wrote()
    assert db.read_cache("t", (1,), f) == 3          # 你改了東西：重算
    c = db.conn()
    with db.write_lock:
        c.execute("INSERT OR REPLACE INTO settings(key, value) VALUES('_rc_probe', ?)", (str(n[0]),))
    assert db.read_cache("t", (1,), f) == 4          # 背景寫入（WAL 變了）：重算
    s1 = client.get("/api/sidebar", params={"nsfw": "show"}).json()
    assert client.get("/api/sidebar", params={"nsfw": "show"}).json() == s1
    with db.write_lock:
        c.execute("DELETE FROM settings WHERE key='_rc_probe'")
