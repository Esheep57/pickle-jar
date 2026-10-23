"""2026-10 工程審查後的修正：每一個都是「以前真的會出錯」的情況。"""
import os
import shutil
import threading
import time

import numpy as np

from tests.conftest import LIB, ids_by_name, make_image, wait_idle


def test_trash_keeps_labels_and_relinks_when_restored(client):
    """丟到資源回收筒後從回收筒還原：同一筆紀錄接回，你確認過的標籤都還在（以前紀錄一起刪掉，還原的檔案被當新圖）。"""
    from app import db, pipeline
    p = make_image(LIB / "trashme" / "keep_me.jpg", 7101)
    raw = p.read_bytes()
    client.post("/api/jobs", json={"kind": "scan"})
    wait_idle(client)
    mid = ids_by_name(client)["keep_me.jpg"]
    client.post("/api/tags/apply", json={"ids": [mid], "action": "add", "tag": "trash_test_char", "category": "character"})
    assert client.post("/api/media/trash", json={"ids": [mid]}).json()["trashed"] == 1
    c = db.conn()
    assert c.execute("SELECT status FROM media WHERE id=?", (mid,)).fetchone()[0] == "trashed"
    lst = client.get("/api/media", params={"view": "all", "nsfw": "show", "limit": 2000}).json()
    assert not any(x["id"] == mid for x in lst["items"]), "回收筒裡的圖不出現在圖庫"
    # 還原到別的名字（同內容）→ 用指紋接回同一筆
    q = LIB / "trashme" / "restored_elsewhere.jpg"
    q.write_bytes(raw)
    client.post("/api/jobs", json={"kind": "scan"})
    wait_idle(client)
    r = c.execute("SELECT id, status, path FROM media WHERE path=?", (str(q),)).fetchone()
    assert r and r["id"] == mid and r["status"] == "done", dict(r) if r else None
    assert c.execute("""SELECT mt.state FROM media_tags mt JOIN tags t ON t.id=mt.tag_id
                        WHERE mt.media_id=? AND t.name='trash_test_char'""", (mid,)).fetchone()[0] == 1
    # 30 天後沒回來的才清紀錄
    client.post("/api/media/trash", json={"ids": [mid]})
    assert pipeline.purge_trashed() == 0
    with db.write_lock:
        c.execute("UPDATE media SET trashed_at=? WHERE id=?", (time.time() - 31 * 86400, mid))
    assert pipeline.purge_trashed() == 1
    assert not c.execute("SELECT 1 FROM media WHERE id=?", (mid,)).fetchone()
    shutil.rmtree(LIB / "trashme", ignore_errors=True)


def test_mass_vanish_is_held_until_confirmed(client):
    """一次消失很多張（子資料夾暫時讀不到）：先不標「找不到檔案」，等你確認。"""
    from app import db, pipeline
    d = LIB / "flaky_drive"
    for i in range(60):
        make_image(d / f"fd_{i:02d}.jpg", 7200 + i, size=(64, 64))
    client.post("/api/jobs", json={"kind": "scan"})
    wait_idle(client)
    old = pipeline.MISSING_HOLD_MIN
    pipeline.MISSING_HOLD_MIN = 50
    hidden = LIB.parent / "flaky_drive_hidden"
    try:
        shutil.move(str(d), str(hidden))                  # 「讀不到」
        client.post("/api/jobs", json={"kind": "scan"})
        wait_idle(client)
        c = db.conn()
        n_missing = c.execute("SELECT COUNT(*) FROM media WHERE path LIKE ? AND status='missing'", (str(d) + "%",)).fetchone()[0]
        assert n_missing == 0, "一次消失太多：先不標"
        st = client.get("/api/status").json()
        assert st["missing_hold"]["n"] >= 60 and "ids" not in st["missing_hold"]
        # 回來了 → 下一次掃描自己恢復，不用做什麼
        shutil.move(str(hidden), str(d))
        client.post("/api/jobs", json={"kind": "scan"})
        wait_idle(client)
        assert "missing_hold" not in client.get("/api/status").json()
        # 真的刪了 → 你確認後才標
        shutil.move(str(d), str(hidden))
        client.post("/api/jobs", json={"kind": "scan"})
        wait_idle(client)
        assert client.post("/api/scan/confirm-missing", json={}).json()["marked"] >= 60
        assert c.execute("SELECT COUNT(*) FROM media WHERE path LIKE ? AND status='missing'", (str(d) + "%",)).fetchone()[0] >= 60
    finally:
        pipeline.MISSING_HOLD_MIN = old
        if hidden.exists():
            shutil.move(str(hidden), str(d))
        client.post("/api/jobs", json={"kind": "scan"})
        wait_idle(client)
        shutil.rmtree(d, ignore_errors=True)
        client.post("/api/jobs", json={"kind": "scan"})
        wait_idle(client)
        client.post("/api/media/purge-missing")


def test_duplicate_groups_do_not_chain():
    """A 跟 B 一樣、B 跟 C 一樣、但 A 跟 C 不一樣（差分）：不能三張放同一組（以前並查集會串起來）。"""
    from app.pipeline import _cliques
    same_pairs = {("A", "B"), ("B", "C")}
    same = lambda x, y: (x, y) in same_pairs or (y, x) in same_pairs
    adj = {"A": {"B"}, "B": {"A", "C"}, "C": {"B"}}
    groups = _cliques(adj, same)
    assert all(not {"A", "C"} <= set(g) for g in groups), groups
    assert any(set(g) == {"A", "B"} or set(g) == {"B", "C"} for g in groups)
    # 真的每張都一樣：放同一組
    same_pairs |= {("A", "C")}
    adj = {"A": {"B", "C"}, "B": {"A", "C"}, "C": {"A", "B"}}
    assert [sorted(g) for g in _cliques(adj, same)] == [["A", "B", "C"]]


def test_crop_scan_moves_past_verified_pairs(client):
    """局部截圖：前 300 對驗完之後，下一次要接著驗後面的（以前永遠只看同樣的前 300 對）。"""
    from app import db, pipeline
    old = pipeline.CROP_PAIRS, pipeline.CROP_COS, pipeline.SAME_ASPECT
    pipeline.CROP_PAIRS, pipeline.CROP_COS, pipeline.SAME_ASPECT = 3, -2.0, -1.0   # 每一對都是候選、每次只取 3 對
    try:
        seen = []
        for _ in range(3):
            got = [tuple(sorted(p)) for p in pipeline._crop_candidates()]
            assert len(got) == 3, "還有沒驗過的對，就要拿得到"
            assert not set(got) & set(seen), "驗過的不能再出現"
            seen += got
            fp = pipeline._fps({x for p in got for x in p})
            with db.write_lock:
                for a, b in got:
                    db.conn().execute("INSERT OR REPLACE INTO dup_check(a, b, fa, fb, kind) VALUES(?,?,?,?,'nocrop')",
                                      (a, b, fp.get(a), fp.get(b)))
    finally:
        pipeline.CROP_PAIRS, pipeline.CROP_COS, pipeline.SAME_ASPECT = old
        with db.write_lock:
            db.conn().execute("DELETE FROM dup_check WHERE kind='nocrop'")
        pipeline.crop_state["sig"] = None


def test_knn_rerun_when_asked_during_run(client, monkeypatch):
    """新角色辨識正在算的時候又被要求重算（你剛確認了一張）：算完要再算一次，不能把要求丟掉。"""
    from app import charknn
    calls = []
    real = charknn._labeled

    def slow():
        calls.append(time.time())
        time.sleep(0.4)
        return real()
    monkeypatch.setattr(charknn, "_labeled", slow)
    t = threading.Thread(target=charknn.run)
    t.start()
    time.sleep(0.1)
    assert charknn.run() == {"busy": True}
    t.join()
    for _ in range(50):
        if len(calls) >= 2 and not charknn._state["running"]:
            break
        time.sleep(0.1)
    assert len(calls) >= 2, "忙碌時的要求要在算完後補算"
    charknn.run(block=True)


def test_one_bad_image_does_not_abort_the_batch(client, monkeypatch):
    """寫入某一張出錯：只有那一張標失敗，同一批其他張照常完成（以前整批撤回、整個工作中止）。"""
    from app import db, pipeline
    d = LIB / "poison"
    for i in range(5):
        make_image(d / f"pz_{i}.jpg", 7300 + i, size=(96, 96))
    real = pipeline._write
    bad = {}

    def write(mid, *a, **k):
        if mid == bad.get("id"):
            raise RuntimeError("故意的")
        return real(mid, *a, **k)
    # 先登記進資料庫，找出其中一張當壞的
    monkeypatch.setattr(pipeline, "_write", write)
    c = db.conn()
    orig_process = pipeline.process_pending

    def pick_then_process(jobs):
        r = c.execute("SELECT id FROM media WHERE path LIKE ? ORDER BY id LIMIT 1", (str(d) + "%",)).fetchone()
        bad["id"] = r[0] if r else None
        return orig_process(jobs)
    monkeypatch.setattr(pipeline, "process_pending", pick_then_process)
    client.post("/api/jobs", json={"kind": "scan"})
    wait_idle(client)
    st = {r["path"]: r["status"] for r in c.execute("SELECT path, status FROM media WHERE path LIKE ?", (str(d) + "%",))}
    assert sorted(st.values()) == ["done", "done", "done", "done", "error"], st
    monkeypatch.undo()
    shutil.rmtree(d, ignore_errors=True)
    client.post("/api/jobs", json={"kind": "scan"})
    wait_idle(client)
    client.post("/api/media/purge-missing")


def test_backup_mirror(client, tmp_path):
    """每日備份另存一份到另一個資料夾；沒設定時收件匣會提醒。"""
    from app import db
    assert client.get("/api/home", params={"nsfw": "show"}).json()["backup_offsite"] is False
    client.post("/api/settings", json={"backup_mirror_dir": str(tmp_path)})
    try:
        db.backup(force=True)
        got = list((tmp_path / "pickle-jar-backups").glob("library-*.db"))
        assert got and got[0].stat().st_size > 0
    finally:
        client.post("/api/settings", json={"backup_mirror_dir": ""})


def test_background_failures_are_recorded(client):
    """背景步驟失敗（以前 except: pass 或只印在命令列）：記在「程式出錯」。"""
    import logging
    from app import errlog
    errlog.install()
    with errlog.bg("測試步驟"):
        raise ValueError("背景故意的")
    logging.getLogger("online").warning("搜源 %s 失敗：%s", 1, "測試警告")
    paths = [e["path"] for e in errlog.recent(50)]
    assert "測試步驟" in paths and any("測試警告" in p for p in paths)


def test_online_queue_is_persistent_and_prioritized(client):
    """線上佇列：存在資料庫（重啟後還在）、大工作切小塊、你手動要的插隊到背景補搜前面。"""
    from app import db, jobqueue, online
    assert not db.get_settings()["online_enabled"]           # 關著：佇列暫停，不會真的跑
    oj = online.get_jobs()
    for g in oj.q.groups():                                   # 前面的測試留下的（線上關著不會跑）先清掉，順序才比得準
        oj.q.cancel_group(g["grp"])
    oj._queued_src.clear()
    bg_ids = list(range(900001, 900025))
    oj.queue_sources(bg_ids)                                  # 背景自動搜源：24 張 → 3 塊
    grp_bg = max(g["grp"] for g in oj.q.groups() if g["kind"] == "source")
    assert len(oj.q.group_rows(grp_bg)) == 3 and oj.q.groups()[-1]["prio"] == jobqueue.BACKGROUND
    oj.queue_tag_info(["zz_prio_tag_(tst)"])
    assert oj.queue_sources([900003, 900004], manual=True) == 2, "手動的要能插隊，即使已經排在背景裡"
    gs = oj.q.groups()
    assert gs[0]["kind"] == "source" and gs[0]["prio"] == jobqueue.MANUAL and gs[0]["n"] == 2
    assert gs[1]["kind"] == "taginfo" and gs[-1]["grp"] == grp_bg
    assert sum(len(r["arg"]) for r in oj.q.group_rows(grp_bg)) == 22, "從背景那組拿掉，不會搜兩次"
    # 重啟：新的佇列物件（同一個資料庫）還看得到
    again = jobqueue.PQueue("online")
    assert [g["grp"] for g in again.groups()] == [g["grp"] for g in gs]
    # 背景工作面板：一組一項，可以整組取消
    d = client.get("/api/jobs/overview").json()
    mine = [x for x in d["queued"] if x["queue"] == "online"]
    assert mine[0]["note"] and mine[0]["n"] == 2
    for _ in range(len(mine)):
        assert client.post("/api/jobs/cancel", json={"queue": "online", "index": 0}).json()["ok"]
    assert oj.q.empty() and not (set(bg_ids) & oj._queued_src)


def test_knn_batch_prediction_matches_loop():
    """新角色辨識的批次計算（一次算一塊矩陣）跟一張一張算的結果一樣。"""
    from app import charknn as K
    rng = np.random.default_rng(3)
    C, L, U, dim = 20, 300, 400, 64
    cent = rng.standard_normal((C, dim)).astype(np.float32)
    lab = rng.integers(0, C, L)
    E = cent[lab] + 0.6 * rng.standard_normal((L, dim)).astype(np.float32)
    E /= np.linalg.norm(E, axis=1, keepdims=True)
    M = np.zeros((L, C), bool)
    M[np.arange(L), lab] = True
    Cn, cnt = K._centroids(E, M)
    valid = cnt >= 3
    Q = cent[rng.integers(0, C, U)] + 0.8 * rng.standard_normal((U, dim)).astype(np.float32)
    Q /= np.linalg.norm(Q, axis=1, keepdims=True)
    a = [K._predict(q, E, M, Cn, valid) for q in Q]
    b = K._predict_many(Q, E, M, Cn, valid, chunk=97)
    for x, y in zip(a, b):
        assert (x is None) == (y is None)
        if x:
            assert x[0] == y[0] and abs(x[1] - y[1]) < 1e-5 and abs(x[2] - y[2]) < 1e-5 and set(x[3]) == set(y[3])


def test_knn_only_new_images_keeps_others(client):
    """新圖處理完只算新圖：別的圖原本的推薦不會被收掉。"""
    from app import charknn, db
    c = db.conn()
    charknn.run(block=True)
    before = {tuple(r) for r in c.execute("SELECT media_id, tag_id FROM media_tags WHERE source='knn' AND state=0")}
    some = sorted({m for m, _ in before})[:1] or [c.execute("SELECT MAX(id) FROM media").fetchone()[0]]
    other = c.execute("SELECT id FROM media WHERE status='done' ORDER BY id DESC LIMIT 1").fetchone()[0]
    r = charknn.run(block=True, only=[other])
    assert "error" not in r and (r.get("only") == 1 or r == {"added": 0}), r
    after = {tuple(x) for x in c.execute("SELECT media_id, tag_id FROM media_tags WHERE source='knn' AND state=0")}
    assert {x for x in before if x[0] != other} <= after, "只算新圖時不能收掉別張的推薦"
    assert some


def test_chinese_names():
    """中文名：咲、夢、百合這些字不再把正常的名字擋掉；寶可夢用官方台灣譯名（確定是寶可夢才用）；候選去重轉繁體。"""
    from app import names
    assert names.display("kitagawa_marin", ["喜多川海夢", "海夢", "喜多川海梦"], None, "zh") == "喜多川海夢"
    assert names.display("chisa_(wuthering_waves)", ["Chisa", "千咲", "朽葉千咲"], None, "zh") == "千咲"
    assert names.display("seia_(swimsuit)_(blue_archive)", ["百合园圣娅(泳装)", "セイア(水着)"], None, "zh") == "百合園聖婭(泳裝)"
    assert names.display("x_(tst)", ["夢女", "百合", "腐向け"], None, "zh") != "夢女"
    assert names.display("gardevoir", ["サーナイト", "Gardevoir"], None, "zh") == "沙奈朵"
    assert names.display("lucario_(pokemon)", [], None, "zh") == "路卡利歐"
    assert names.display("golem", ["ゴーレム"], None, "zh") != "隆隆岩", "不確定是寶可夢就不套"
    assert names.display("gardevoir", ["サーナイト"], "我的沙奈", "zh") == "我的沙奈", "你取的名字永遠優先"
    assert names.candidates(["若葉睦", "若叶睦", "素睦", "夢女", "Mutsumi"]) == ["若葉睦", "素睦"]


def test_names_review(client):
    """中文名整理：列出沒中文／好幾個譯名的角色，改名後移到「你取過名的」。"""
    from app import db
    from app.api.common import _apply
    ids = sorted(ids_by_name(client).values())[:1]
    with db.write_lock:
        _apply("add", "nm_test_(tst)", ids, "character")
        db.conn().commit()
    tid = db.tag_id("nm_test_(tst)", create=False)
    from app.danbooru import save_tag_info
    save_tag_info(tid, {"status": "ok", "copyright": None, "copyrights": [], "summary": "", "post_count": None,
                        "other_names": ["テスト子", "测试子", "測試子", "小测"], "url": None, "links": []})
    r = client.get("/api/names/review", params={"kind": "multi"}).json()
    it = next(x for x in r["items"] if x["name"] == "nm_test_(tst)")
    assert it["candidates"][:2] == ["測試子", "小測"] and it["display"] == "測試子", it
    assert client.post("/api/tag/nm_test_(tst)", json={"display_name": "小測"}).status_code == 200
    done = client.get("/api/names/review", params={"kind": "done"}).json()["items"]
    assert any(x["name"] == "nm_test_(tst)" and x["display"] == "小測" and x["auto"] == "測試子" for x in done)
    client.post("/api/tag/nm_test_(tst)", json={"display_name": ""})
    assert client.get("/api/tag/nm_test_(tst)").json()["display"] == "測試子"


def test_knn_learns_from_yes_no():
    """「你的圖庫」從你的是／不是學習：像你否決過的 → 機率低、不推；資料不夠時只用規則（更像否決過的圖就不推）。"""
    import numpy as np
    from app import charknn as K
    rnd = np.random.default_rng(0)
    n = 200
    y = (rnd.random(n) < 0.8).astype(float)
    X = np.zeros((n, len(K.FEATS)))
    X[:, 0] = np.clip(0.85 + 0.1 * rnd.standard_normal(n), 0, 1)             # share：是／不是差不多
    X[:, 1] = 0.45 + 0.05 * rnd.standard_normal(n)                            # cen
    X[:, 5] = np.where(y == 1, 0.1, 0.6) + 0.1 * rnd.standard_normal(n)      # neg：不是的都很像某張否決過的圖
    X[:, 6] = X[:, 5] - 0.5
    m = {"mode": "learned", **K._fit(X, y)}
    good, bad = X[y == 1].mean(0), X[y == 0].mean(0)
    assert K._accept(m, good)[0] and K._prob(m, good) > 0.8
    assert not K._accept(m, bad)[0] and K._prob(m, bad) < 0.3
    rules = {"mode": "rules"}
    x = [0.9, 0.4, 0.4, 2.0, 0.0, 0.5, 0.1, 0.1]       # 比起確認過的圖，更像否決過的（negm > 0）
    assert K._accept(rules, x) == (False, None)
    x[6] = -0.2
    assert K._accept(rules, x) == (True, None)


def test_x_import_of_existing_image_gets_author(client):
    """從 X 匯入、但圖庫裡已經有這張（或檔案跟消失的那張一樣、被接回舊紀錄）：一樣要補上貼文作者（待確認）。
    使用者回報：從 X 匯入的圖寫「從 X 貼文匯入」卻沒有創作者——檔案被接回已經標記過的舊紀錄，匯入後處理從沒跑。"""
    import time as _t
    from app import db, importer, pipeline
    from tests.conftest import ids_by_name
    client.post("/api/settings", json={"online_enabled": True})
    try:
        ids = sorted(ids_by_name(client).values())
        a, b = ids[0], ids[1]
        c = db.conn()
        q = """SELECT t.name, mt.state FROM media_tags mt JOIN tags t ON t.id=mt.tag_id WHERE mt.media_id=? AND t.category='artist' AND mt.source='x'"""
        # 1) 匯入時發現圖庫已經有：記下貼文資訊、馬上補作者
        meta = {"handle": "Later_Art", "author": "Later Art", "tweet_id": "1790000000000000002"}
        importer._remember_url(a, {"site": "x", "url": "https://x.com/Later_Art/status/1790000000000000002"}, meta)
        importer.after_processed()
        # 2) 掃描接回舊紀錄（status 已經是 done）：沒有要標記的圖時也要做匯入後處理
        path = c.execute("SELECT path FROM media WHERE id=?", (b,)).fetchone()[0]
        with db.write_lock:
            c.execute("INSERT OR REPLACE INTO import_meta(path, site, post_url, data, created_at, applied) VALUES(?,?,?,?,?,0)",
                      (path, "x", "https://x.com/Later_Art/status/1790000000000000003", '{"handle": "Later_Art"}', _t.time()))
            c.commit()
        pipeline.process_pending(pipeline.get_jobs())
        for _ in range(60):
            ra, rb = c.execute(q, (a,)).fetchone(), c.execute(q, (b,)).fetchone()
            if ra and rb:
                break
            _t.sleep(0.5)
        assert ra and rb and ra[1] == 0 and rb[1] == 0, (ra, rb)
        # 已經有完整資訊的紀錄不會被蓋掉
        importer._remember_url(a, {"site": "x", "url": "https://x.com/other/status/1"}, {"handle": "other"})
        assert c.execute("SELECT post_url FROM import_meta WHERE path=(SELECT path FROM media WHERE id=?)", (a,)).fetchone()[0].endswith("0002")
    finally:
        client.post("/api/settings", json={"online_enabled": False})


def test_source_search_skips_trashed(client):
    """排隊搜源之後圖被刪了（移到回收筒）：靜靜跳過，不寫錯誤紀錄、也不記成搜過失敗。
    使用者回報：刪了一批重複圖，錯誤紀錄跳出幾十筆「搜源 xxx 失敗：找不到檔案」。"""
    from app import db, errlog, online
    from tests.conftest import ids_by_name
    mid = sorted(ids_by_name(client).values())[-1]
    c = db.conn()
    with db.write_lock:
        c.execute("DELETE FROM media_source WHERE media_id=?", (mid,))
        c.execute("UPDATE media SET status='trashed', trashed_at=0 WHERE id=?", (mid,))
        c.commit()
    try:
        before = len(errlog.recent(100000))
        online.get_jobs()._source_one(mid, True, set())
        assert not c.execute("SELECT 1 FROM media_source WHERE media_id=?", (mid,)).fetchone(), "不記成搜過失敗"
        if before is not None:
            assert len(errlog.recent(100000)) == before, "不寫進錯誤紀錄"
    finally:
        with db.write_lock:
            c.execute("UPDATE media SET status='done', trashed_at=NULL WHERE id=?", (mid,))
            c.commit()
