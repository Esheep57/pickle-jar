import json
import time
import os
import shutil
import sys
import types

import numpy as np
import pytest

from tests.conftest import LIB, ids_by_name, make_image, wait_idle
from app import danbooru, httpclient, pixiv  # noqa: E402


def test_scan_tags_and_fingerprints(client):
    from app import db
    c = db.conn()
    rows = c.execute("SELECT path, status, fp, media_type, width FROM media").fetchall()
    names = {os.path.basename(r["path"]) for r in rows}
    assert "img_00.jpg" in names and "DSC_0100.jpg" in names
    assert "IMG_0001.MOV" not in names, "原況照片的 .MOV 應該被略過"
    done = [r for r in rows if r["status"] == "done"]
    assert len(done) >= 16
    assert all(r["fp"] for r in done), "每張處理完都要有內容指紋"
    assert all(r["media_type"] for r in done)


def test_query_syntax(client):
    from app import query
    assert query.tokenize("a b|c OR d -e") == [["a"], ["b", "c", "d"], ["-e"]]
    ids = ids_by_name(client)
    r = client.post("/api/tags/apply", json={"ids": [ids["img_01.jpg"], ids["img_02.jpg"]], "action": "add", "tag": "hatsune_miku", "category": "character"})
    assert r.status_code == 200
    client.post("/api/tags/apply", json={"ids": [ids["img_03.jpg"]], "action": "add", "tag": "izmir", "category": "character"})
    client.post("/api/tags/apply", json={"ids": [ids["img_04.jpg"]], "action": "add", "tag": "twintails", "category": "general"})

    def total(q):
        return client.get("/api/media", params={"q": q, "nsfw": "show"}).json()["total"]
    everything = total("")
    assert total("hatsune_miku") == 2
    assert total("hatsune_miku|izmir") == 3
    assert total("hatsune_miku OR izmir") == 3
    assert total("-hatsune_miku") == everything - 2
    assert total("雙馬尾") >= 1, "一般 tag 的中文對照要搜得到"
    assert total("ratio:landscape") == 5   # 4 張橫圖＋等比例縮小的複本
    assert total("ratio:portrait") >= 10
    assert total("w:>=640") == 4
    assert total("added:1d") == everything
    assert total("date:1990") == 0
    assert total("chars:>=1") == 3
    assert total("nonexistent_tag_xyz") == 0
    assert total("rating:safe") + total("rating:nsfw") == everything


def test_reject_is_respected_on_retag(client):
    from app import db
    ids = ids_by_name(client)
    mid = ids["img_05.jpg"]
    client.post("/api/tags/apply", json={"ids": [mid], "action": "reject", "tag": "akiyama_mizuki", "category": "character"})
    client.post("/api/jobs", json={"kind": "retag", "ids": [mid]})
    wait_idle(client)
    st = db.conn().execute("""SELECT mt.state FROM media_tags mt JOIN tags t ON t.id=mt.tag_id
                              WHERE mt.media_id=? AND t.name='akiyama_mizuki'""", (mid,)).fetchone()
    assert st["state"] == -1, "否決過的角色重新標記後不能又變回候選"


def test_relink_after_move(client):
    from app import db
    ids = ids_by_name(client)
    mid = ids["img_06.jpg"]
    client.post("/api/tags/apply", json={"ids": [mid], "action": "add", "tag": "gladiia_(arknights)", "category": "character"})
    dst = LIB / "moved" / "renamed_06.jpg"
    dst.parent.mkdir(exist_ok=True)
    shutil.move(str(LIB / "img_06.jpg"), dst)
    client.post("/api/jobs", json={"kind": "scan"})
    wait_idle(client)
    r = db.conn().execute("SELECT id, path, status FROM media WHERE path=?", (str(dst),)).fetchone()
    assert r and r["id"] == mid and r["status"] == "done", "搬家後要接回同一筆紀錄"
    t = client.get("/api/media", params={"q": "gladiia_(arknights)", "nsfw": "show"}).json()
    assert [x["id"] for x in t["items"]] == [mid]


def test_merge_and_undo(client):
    ids = ids_by_name(client)
    client.post("/api/tags/apply", json={"ids": [ids["img_07.jpg"]], "action": "add", "tag": "miku_custom", "category": "character"})
    r = client.post("/api/tag/miku_custom/merge", json={"into": "hatsune_miku"})
    assert r.json().get("ok"), r.json()
    assert client.get("/api/media", params={"q": "hatsune_miku", "nsfw": "show"}).json()["total"] == 3
    u = client.post("/api/undo", json={}).json()
    assert "合併" in (u["undone"] or "")
    assert client.get("/api/media", params={"q": "hatsune_miku", "nsfw": "show"}).json()["total"] == 2
    assert client.get("/api/media", params={"q": "miku_custom", "nsfw": "show"}).json()["total"] == 1


def test_export_import_roundtrip(client):
    from app import db
    ids = ids_by_name(client)
    exp = client.get("/api/export").json()
    assert exp["app"] == "pickle-jar" and exp["media"]
    assert "danbooru_api_key" not in exp["settings"]
    # 把一個確認拿掉，再匯入 → 回來
    mid = ids["img_03.jpg"]
    tid = db.tag_id("izmir", create=False)
    with db.write_lock:
        db.conn().execute("DELETE FROM media_tags WHERE media_id=? AND tag_id=?", (mid, tid))
        db.conn().commit()
    r = client.post("/api/import", content=json.dumps(exp), headers={"Content-Type": "application/json"}).json()
    assert r["matched"] >= 1
    assert client.get("/api/media", params={"q": "izmir", "nsfw": "show"}).json()["total"] == 1


def test_saved_search_and_home(client):
    r = client.post("/api/saved", json={"name": "橫圖", "query": "ratio:landscape"}).json()
    lst = client.get("/api/saved").json()
    assert any(x["id"] == r["id"] and x["n"] == 5 for x in lst)
    h = client.get("/api/home").json()
    assert h["total"] >= 16 and "cand" in h
    assert client.get("/api/sidebar").json()["saved"]


def test_backup_and_diag(client):
    from app import db
    p = db.backup(force=True)
    assert p and p.exists()
    assert client.get("/api/backups").json()
    d = client.get("/api/diag").json()
    assert d["model_files"]["model.onnx"] and "providers_available" in d
    b = client.post("/api/diag/bench").json()
    assert b["sane"] and b["images_per_s"] > 0


def test_duplicates_found(client):
    d = client.get("/api/duplicates", params={"nsfw": "show", "dist": 3}).json()
    names = [{x["name"] for x in g} for g in d["groups"]]
    assert any({"img_00.jpg", "copy_small.png"} <= g for g in names)


def test_security_guards(client):
    from fastapi.testclient import TestClient
    from app.server import app
    # DNS rebinding：本機連線但 Host 是別的網域
    assert client.get("/api/status", headers={"Host": "evil.example"}).status_code == 403
    # 區網：沒開手機上傳 → 全擋
    lan = TestClient(app, client=("192.168.1.50", 5555))
    assert lan.get("/api/status").status_code == 403
    assert lan.get("/m").status_code == 403
    client.post("/api/settings", json={"lan_enabled": True})
    pin = client.get("/api/settings").json()["lan_pin"]
    assert len(pin) == 6
    assert lan.get("/api/status").status_code == 401, "還沒輸入 PIN：除了 /m 什麼都不給"
    r = lan.get("/static/index.html", follow_redirects=False)
    assert r.status_code == 302 and r.headers["location"] == "/m"
    assert lan.get("/m").status_code == 200
    assert lan.put("/api/m/upload", content=b"x", headers={"X-Filename": "a.jpg"}).status_code == 401
    assert lan.post("/api/m/login", json={"pin": "000000" if pin != "000000" else "111111"}).status_code == 401
    assert lan.post("/api/m/login", json={"pin": pin}).status_code == 200
    img = make_image(LIB.parent / "phone.jpg", 999)
    r = lan.put("/api/m/upload", content=img.read_bytes(), headers={"X-Filename": "phone%20pic.jpg"})
    assert r.status_code == 200, r.text
    wait_idle(client)
    from app import db
    row = db.conn().execute("SELECT status FROM media WHERE path LIKE ?", ("%phone pic.jpg",)).fetchone()
    assert row and row["status"] == "done"
    assert lan.put("/api/m/upload", content=b"x", headers={"X-Filename": "evil.exe"}).status_code == 400
    # 輸入 PIN 以後：可以「看」圖庫，不能改；金鑰、設定、備份、匯出、資料夾、瀏覽器腳本都不給
    assert lan.get("/api/m/me").json()["remote"] is True and client.get("/api/m/me").json()["remote"] is False
    assert lan.get("/static/index.html").status_code == 200
    from app.api import lan as lan_mod
    lan_mod._SECRET = None                     # 模擬程式重開：登入還有效（金鑰存在 data/lan_secret）
    assert lan.get("/api/status").status_code == 200
    items = lan.get("/api/media", params={"view": "all", "nsfw": "show"}).json()["items"]
    assert items and lan.get(f"/t/{items[0]['id']}").status_code == 200 and lan.get(f"/file/{items[0]['id']}").status_code == 200
    for ok in ("/api/status", "/api/sidebar", "/api/albums", "/api/shelf", f"/api/media/{items[0]['id']}"):
        assert lan.get(ok).status_code == 200, ok
    st = lan.get("/api/settings").json()
    assert "lan_pin" not in st and "danbooru_api_key" not in st and "pixiv_phpsessid" not in st and st["roots"] == []
    for bad in ("/api/export", "/api/fs", "/api/backups", "/api/lan/info", "/api/fetch-url/userscript.user.js", "/api/diag", "/api/home"):
        assert lan.get(bad).status_code == 403, bad
    assert lan.post("/api/settings", json={"lan_enabled": False}).status_code == 403
    assert lan.post("/api/media/nsfw", json={"ids": [items[0]["id"]], "value": 1}).status_code == 403
    assert lan.post("/api/undo", json={}).status_code == 403
    client.post("/api/settings", json={"lan_enabled": False})


def test_online_source_priority(client):
    """搜源最優先：MD5 原檔相符 → 直接確認；AI 的角色候選收掉。Pixiv 檔名 → 同一作品。"""
    from app import db, online
    ids = ids_by_name(client)
    db.update_settings({"online_enabled": True, "online_iqdb": False})
    try:
        mid = ids["danbooru_original.jpg"]
        r = client.post("/api/online/source", json={"ids": [mid, ids["98765432_p0.jpg"]], "force": True}).json()
        assert r["queued"] == 2
        import time
        for _ in range(100):
            if not online.get_jobs().state["running"] and online.get_jobs().q.empty():
                break
            time.sleep(0.1)
        time.sleep(0.5)
        rows = {r["name"]: (r["state"], r["tier"], r["source"]) for r in db.conn().execute(
            """SELECT t.name, mt.state, mt.tier, mt.source FROM media_tags mt JOIN tags t ON t.id=mt.tag_id
               WHERE mt.media_id=? AND t.category IN ('character','artist','copyright')""", (mid,))}
        assert rows["akiyama_mizuki"][0] == 1, rows
        assert rows["some_artist"][0] == 1
        assert not any(v[2] == "model" and v[0] == 0 for v in rows.values()), "搜源找到後不該再有 AI 角色候選"
        p = {r["name"]: r["state"] for r in db.conn().execute(
            """SELECT t.name, mt.state FROM media_tags mt JOIN tags t ON t.id=mt.tag_id WHERE mt.media_id=?""", (ids["98765432_p0.jpg"],))}
        assert p.get("akiyama_mizuki") == 1 and p.get("mizuki_painter") == 1, p
    finally:
        db.update_settings({"online_enabled": False})


def test_sets_removed(client):
    """「同一組圖」已移除：不分組、確認後也不會傳播候選。"""
    from app import candidates, db
    for n in ("55556666_p0.jpg", "55556666_p1.jpg"):
        make_image(LIB / "sets" / n, hash(n) % 1000)
    client.post("/api/jobs", json={"kind": "scan"})
    wait_idle(client)
    candidates.compute_sets()
    assert not db.conn().execute("SELECT 1 FROM media WHERE set_key IS NOT NULL").fetchone()
    ids = ids_by_name(client)
    r = client.post("/api/tags/apply", json={"ids": [ids["55556666_p0.jpg"]], "action": "add", "tag": "some_painter", "category": "artist"}).json()
    assert r["set_added"] == 0
    assert not db.conn().execute("SELECT 1 FROM media_tags WHERE source='set'").fetchone()


def test_source_filename_clues_and_skip(client):
    """檔名是 MD5 → 就算檔案被轉存過也對得上 Danbooru；本機 AI 生成圖不上傳 IQDB；截圖邊框會裁掉。"""
    import time
    from PIL import Image
    from app import db, online
    md5 = os.environ["MOCK_MD5"]
    renamed = make_image(LIB / "clues" / f"{md5.upper()}.jpg", 777)          # 內容不同，但檔名是原檔 MD5
    ai = make_image(LIB / "clues" / "ComfyUI_00001_.png", 778)
    client.post("/api/jobs", json={"kind": "scan"})
    wait_idle(client)
    ids = ids_by_name(client)
    db.update_settings({"online_enabled": True})
    try:
        online.get_jobs().queue_sources([ids[renamed.name], ids[ai.name]])
        for _ in range(100):
            if not online.get_jobs().state["running"] and online.get_jobs().q.empty():
                break
            time.sleep(0.1)
        time.sleep(0.3)
        rows = {r[0]: (r[1], r[2], r[3]) for r in db.conn().execute(
            "SELECT media_id, status, method, data FROM media_source WHERE media_id IN (?,?)", (ids[renamed.name], ids[ai.name]))}
        assert rows[ids[renamed.name]][:2] == ("found", "md5_name")
        assert rows[ids[ai.name]][0] == "notfound" and '"skipped"' in rows[ids[ai.name]][2]
    finally:
        db.update_settings({"online_enabled": False})
    # 截圖：白邊 + 中間的圖 → 只留中間
    img = Image.new("RGB", (600, 1200), (255, 255, 255))
    img.paste(Image.open(LIB / "img_00.jpg").resize((500, 700)), (50, 250))
    t = danbooru.trim_borders(img)
    assert abs(t.size[0] - 500) <= 6 and abs(t.size[1] - 700) <= 6
    assert online.unsearched_ids()  # 還有沒搜過的圖會被背景自動搜源


def test_danbooru_account_warning(client):
    """沒有 Danbooru 帳號 → 首頁和狀態都要提示；帳號錯誤 / 正確也要分得出來。"""
    from app import db, online
    db.update_settings({"online_enabled": True, "danbooru_login": "", "danbooru_api_key": ""})
    try:
        h = client.get("/api/home").json()
        assert h["danbooru"]["state"] == "no_auth"
        assert client.get("/api/status").json()["online"]["mode"]["state"] == "no_auth"
        assert client.post("/api/online/test-danbooru").json()["ok"] is False
        db.update_settings({"danbooru_login": "me", "danbooru_api_key": "wrong"})
        r = client.post("/api/online/test-danbooru").json()
        assert r["ok"] is False and "錯誤" in r["msg"]
        assert danbooru.search_mode()["state"] == "bad_auth"
        db.update_settings({"danbooru_api_key": "good"})
        r = client.post("/api/online/test-danbooru").json()
        assert r["ok"] and r["iqdb"], r
        assert danbooru.search_mode()["state"] == "ok"
        assert danbooru.iqdb_search(str(LIB / "img_00.jpg"))[0]["kind"] == "danbooru"
    finally:
        db.update_settings({"online_enabled": False, "danbooru_login": "", "danbooru_api_key": ""})
        danbooru._dan_iqdb_ok = None


def test_likelihood_precheck():
    """本地預判：同人圖（有角色、簽名）> 一般插畫 > 梗圖/截圖/照片。"""
    from app import likely
    base = {"path": "C:/x/IMG_0001.JPG", "width": 1000, "height": 1400, "media_type": "illustration"}
    fan, _ = likely.score(base, {"signature": 0.8}, True)
    plain, _ = likely.score(base, {}, False)
    meme, why = likely.score({**base, "width": 1170, "height": 2532}, {"meme": 0.6, "text_focus": 0.5}, False)
    photo, _ = likely.score({**base, "media_type": "photo"}, {}, True)
    clue, _ = likely.score({**base, "path": "C:/x/0123456789abcdef0123456789abcdef.jpg"}, {"meme": 0.9}, False)
    assert fan >= likely.LIKELY and likely.MAYBE <= plain < likely.LIKELY
    assert meme < likely.MAYBE and "手機截圖尺寸" in why
    assert photo == 0 and clue == 100


def test_danbooru_iqdb_survives_transient_errors(client, monkeypatch):
    """一次失敗不會永久停用 Danbooru 相似圖搜尋；連續 3 次才暫停，時間到會再試。"""
    import httpx
    from app import db, online
    db.update_settings({"danbooru_login": "me", "danbooru_api_key": "good"})
    real = httpclient.client().post
    calls = {"n": 0}

    def flaky(*a, **k):
        calls["n"] += 1
        if calls["n"] == 1:
            raise httpx.ReadTimeout("slow")
        return real(*a, **k)
    monkeypatch.setattr(httpclient.client(), "post", flaky)
    danbooru._dan_iqdb_ok, danbooru._dan_iqdb_fails, danbooru._dan_iqdb_retry_at = None, 0, 0.0
    try:
        assert danbooru._dan_iqdb(b"x") is None          # 第一次逾時：這張改走 IQDB
        assert danbooru._dan_iqdb_ok is not False          # 但沒有停用
        assert danbooru._dan_iqdb(b"x")[0]["kind"] == "danbooru"
        danbooru._dan_iqdb_ok, danbooru._dan_iqdb_retry_at = False, 0.0   # 暫停時間到 → 會再試
        assert danbooru._dan_iqdb(b"x") is not None and danbooru._dan_iqdb_ok is True
    finally:
        db.update_settings({"danbooru_login": "", "danbooru_api_key": ""})
        danbooru._dan_iqdb_ok = None


def test_archive_out_of_icloud(client, tmp_path):
    """移出 iCloud：複製到電腦 → 比對 → 紀錄改指向新位置（標籤保留）→ iCloud 那份丟回收筒。"""
    from app import archive, db
    src = make_image(LIB / "iCloud Photos" / "Photos" / "IMG_7777.JPG", 4242)
    client.post("/api/jobs", json={"kind": "scan"})
    wait_idle(client)
    ids = ids_by_name(client)
    mid = ids["IMG_7777.JPG"]
    client.post("/api/tags/apply", json={"ids": [mid], "action": "add", "tag": "izmir", "category": "character"})
    assert client.get("/api/media", params={"q": "is:icloud", "nsfw": "show"}).json()["total"] == 1
    pv = client.post("/api/media/archive/preview", json={"ids": [mid, ids["img_00.jpg"]]}).json()
    assert pv["icloud"] == 1 and pv["others"] == 1
    assert client.post("/api/media/archive", json={"ids": [mid], "dest": str(tmp_path / "OneDrive" / "x")}).status_code == 400
    dest = tmp_path / "keep"
    assert client.post("/api/media/archive", json={"ids": [mid, ids["img_00.jpg"]], "dest": str(dest)}).json()["ok"]
    wait_idle(client)
    row = db.conn().execute("SELECT path, status FROM media WHERE id=?", (mid,)).fetchone()
    assert row["path"].startswith(str(dest)) and os.path.exists(row["path"]) and not src.exists()
    assert client.get("/api/media", params={"q": "izmir", "nsfw": "show"}).json()["total"] >= 1
    assert any(x["id"] == mid for x in client.get("/api/media", params={"q": "izmir", "nsfw": "show"}).json()["items"])
    assert os.path.exists(LIB / "img_00.jpg"), "不在 iCloud 的圖不能被動到"
    st = client.get("/api/status").json()
    assert st["archive"]["moved"] == 1 and not st["archive"]["failed"]
    assert str(dest) in db.get_settings()["roots"]


def test_file_display_converts_heic_and_sniffs(client):
    """大圖檢視：HEIC 轉成 JPEG；副檔名跟內容不符（.png 其實是 JPEG）照內容給格式。"""
    from PIL import Image
    import pillow_heif  # noqa: F401
    heic = LIB / "phone" / "IMG_5555.HEIC"
    heic.parent.mkdir(exist_ok=True)
    Image.open(LIB / "img_01.jpg").save(heic, format="HEIF")
    fake = LIB / "phone" / "IMG_5556.PNG"
    (LIB / "img_02.jpg").read_bytes()
    fake.write_bytes((LIB / "img_02.jpg").read_bytes())
    client.post("/api/jobs", json={"kind": "scan"})
    wait_idle(client)
    ids = ids_by_name(client)
    r = client.get(f"/file/{ids['IMG_5555.HEIC']}?display=1")
    assert r.status_code == 200 and r.headers["content-type"] == "image/jpeg" and r.content[:3] == b"\xff\xd8\xff"
    r = client.get(f"/file/{ids['IMG_5556.PNG']}?display=1")
    assert r.headers["content-type"] == "image/jpeg"


def test_zz_all_writes_hold_the_lock(client):
    """所有寫入都要在 db.write_lock 裡（離開時自動 commit），否則會出現 database is locked。"""
    from app import db
    bad = {sql: where for sql, where in db.UNLOCKED_WRITES}
    assert not bad, "\n\n".join(f"{k}\n{v}" for k, v in list(bad.items())[:5])


def test_unrecognized_skip_buckets_groups(client):
    ids = ids_by_name(client)
    def unrec(**kw):
        return client.get("/api/media", params={"view": "unrecognized", "nsfw": "show", **kw}).json()["total"]
    before = unrec()
    s = client.get("/api/unrec/summary", params={"nsfw": "show"}).json()
    assert s["all"] == before and s["skipped"] == 0
    mid = ids["img_05.jpg"]
    assert client.post("/api/media/skip", json={"ids": [mid], "value": True}).json()["ok"]
    assert unrec() == before - 1
    assert unrec(bucket="skipped") == 1
    assert client.get("/api/media", params={"nsfw": "show"}).json()["total"] >= 1   # 還在圖庫
    client.post("/api/undo", json={})
    assert unrec() == before


def test_display_names_and_series_merge():
    from app import names
    assert names.display("houshou_marine", ["宝鐘マリン", "沈没後悔日記", "宝钟玛琳"], None, "zh") == "寶鍾瑪琳"
    assert names.display("nahida_(genshin_impact)", ["纳西妲", "Nahida", "布耶尔"], None, "zh") == "納西妲"
    assert names.display("ganyu_(genshin_impact)", ["甘雨(原神)", "甘雨"], None, "zh") == "甘雨"
    assert names.display("someone_(blue_archive)", ["竜華キサキ", "妃咲"], None, "zh") == "妃咲"   # 咲 中文音譯名也用（Danbooru 別名；妃咲本人有官方譯名表）
    assert names.display("minato_aqua", ["湊あくあ", "阿夸"], None, "zh") == "阿夸"
    assert names.series_display("genshin_impact", ["原神同人"], None, "zh") == "原神"
    keys = {"fate_(series)", "fate/grand_order", "bang_dream!", "bang_dream!_it's_mygo!!!!!", "blue_archive"}
    assert names.series_parent("fate/grand_order", keys) == "fate_(series)"
    assert names.series_parent("bang_dream!_it's_mygo!!!!!", keys) == "bang_dream!"
    assert names.series_parent("blue_archive", keys) is None


def test_wiki_series_name(client):
    """維基查譯名：完全同名才採用；被導向到別的條目就放棄。"""
    from app import db, online, wikiname
    for n in ("project_sekai", "vocaloid"):
        tid = db.tag_id(n, "copyright")
        danbooru.save_tag_info(tid, danbooru.fetch_tag_info(n))
    wikiname.run(["project_sekai", "vocaloid"])
    got = wikiname.cached(["project_sekai", "vocaloid"])
    assert got == {"project_sekai": "世界計畫 彩色舞台 feat. 初音未來"}
    assert wikiname.need_lookup(["project_sekai", "vocaloid", "blue_archive"]) == []   # 查過的、內建表有的都不再查


def test_review_fixes(client):
    """系統檢查修掉的問題：CSRF、壞搜尋條件、合併到自己的別名、刪圖後撤銷、設定檢查。"""
    from app import db
    # 跨站請求一律拒絕
    assert client.post("/api/undo", json={}, headers={"Origin": "http://evil.example"}).status_code == 403
    assert client.post("/api/undo", json={}, headers={"Sec-Fetch-Site": "cross-site"}).status_code == 403
    # 打錯的搜尋條件 → 空結果，不是 500
    for q in ("w:1.2.3", "date:9999", "ratio:.", "chars:>."):
        assert client.get("/api/media", params={"q": q, "nsfw": "show"}).status_code == 200
    # 合併到自己的別名 → 400，標籤還在
    ids = ids_by_name(client)
    client.post("/api/tags/apply", json={"ids": [ids["img_08.jpg"]], "action": "add", "tag": "aaa_tag", "category": "custom"})
    client.post("/api/tags/apply", json={"ids": [ids["img_08.jpg"]], "action": "add", "tag": "bbb_tag", "category": "custom"})
    assert client.post("/api/tag/aaa_tag/merge", json={"into": "bbb_tag"}).json()["ok"]
    assert client.post("/api/tag/bbb_tag/merge", json={"into": "aaa_tag"}).status_code == 400
    assert db.tag_id("bbb_tag", create=False)
    # 設定：不合理的值擋下
    assert client.post("/api/settings", json={"scan_interval_h": "abc"}).status_code == 400
    assert client.post("/api/settings", json={"workers": 0}).status_code == 400
    # 刪圖後撤銷不會卡死
    mid = ids["img_09.jpg"]
    client.post("/api/tags/apply", json={"ids": [mid], "action": "add", "tag": "zzz_tag", "category": "custom"})
    client.post("/api/media/trash", json={"ids": [mid]})
    assert client.post("/api/undo", json={}).status_code == 200
    assert client.post("/api/undo", json={}).status_code == 200


def test_series_page_and_slash_tags(client):
    from app import db, online
    ids = ids_by_name(client)
    client.post("/api/tags/apply", json={"ids": [ids["img_10.jpg"]], "action": "add", "tag": "fate/grand_order", "category": "copyright"})
    r = client.get("/api/tag/fate/grand_order", params={"nsfw": "show"})
    assert r.status_code == 200 and r.json()["category"] == "copyright"
    assert client.post("/api/tag/fate/grand_order", json={"display_name": "FGO"}).status_code == 200
    d = client.get("/api/series/fate/grand_order", params={"nsfw": "show"}).json()
    assert d["key"] == "fate/grand_order"
    assert client.get("/api/media", params={"q": "series:fate/grand_order", "nsfw": "show"}).json()["total"] >= 1


def test_danbooru_renamed_tag_merges(client):
    """WD 給的是 Danbooru 改名前的舊名字（gojou_satoru）→ 抓資料時自動合併到新名字，拿到中文名。"""
    from app import db, names, online
    ids = ids_by_name(client)
    client.post("/api/tags/apply", json={"ids": [ids["img_11.jpg"]], "action": "add", "tag": "gojou_satoru", "category": "character"})
    j = online.get_jobs()
    j._taginfo(["gojou_satoru"])
    assert db.conn().execute("SELECT 1 FROM tags WHERE name='gojou_satoru'").fetchone() is None
    t = client.get("/api/tag/gojo_satoru", params={"nsfw": "show"}).json()
    assert t["confirmed"] == 1 and t["display"] == "五條悟"
    assert db.tag_id("gojou_satoru", create=False) == db.tag_id("gojo_satoru", create=False)   # 舊名字自動導向


def test_artist_scan(client):
    """從畫師反推：縮圖跟本機某張還沒找到出處的圖一樣 → 找到；不一樣的不算。"""
    import io
    from PIL import Image
    from app import artistscan, db
    from tests import mock_online
    ids = ids_by_name(client)
    target = ids["img_07.jpg"]
    client.post("/api/tags/apply", json={"ids": [ids["img_08.jpg"]], "action": "add", "tag": "scan_artist", "category": "artist"})
    path = db.conn().execute("SELECT path FROM media WHERE id=?", (target,)).fetchone()[0]
    from tests.conftest import make_image
    other = make_image(LIB.parent / "not_in_library.jpg", 31337, size=(400, 560))
    for k, src in enumerate([path, str(other)]):
        im = Image.open(src).convert("RGB"); im.thumbnail((180, 180)); b = io.BytesIO(); im.save(b, "JPEG", quality=85)
        mock_online.PREVIEWS[k] = b.getvalue()
    state = {}
    artistscan.run(state, lambda: False, max_artists=5, dry_run=True)
    st = state["artistscan"]
    assert [m["id"] for m in st["matches"]] == [target], st["matches"]
    artistscan.run(state, lambda: False, max_artists=5, dry_run=True, deep=True)   # deep 模式至少要能跑完
    assert state["artistscan"]["finished"]
    artistscan.run(state, lambda: False, max_artists=5)
    r = db.conn().execute("SELECT status, method FROM media_source WHERE media_id=?", (target,)).fetchone()
    assert tuple(r) == ("found", "artist_scan")


def test_pixel_hash(client, tmp_path):
    """像素指紋：同樣的像素、不同的檔案（例如 PNG 重新存檔）→ 指紋一樣；對上 Danbooru → 找到出處。"""
    from PIL import Image
    from app import db, hashscan
    from tests import mock_online
    ids = ids_by_name(client)
    target = ids["img_04.jpg"]
    path = db.conn().execute("SELECT path FROM media WHERE id=?", (target,)).fetchone()[0]
    ph = hashscan.pixel_hash(path)
    im = Image.open(path).convert("RGB")
    a, b = tmp_path / "a.png", tmp_path / "b.png"
    im.save(a, compress_level=1); im.save(b, compress_level=9, optimize=True)
    assert ph and hashscan.pixel_hash(str(a)) == hashscan.pixel_hash(str(b))
    assert open(a, "rb").read() != open(b, "rb").read()
    mock_online.PIXHASH[ph] = 4242
    state = {}
    hashscan.run(state, lambda: False, dry_run=True)
    assert [m["id"] for m in state["pixelhash"]["matches"]] == [target]
    hashscan.run(state, lambda: False)
    assert state["pixelhash"]["hashed"] == 0            # 第二次用快取
    r = db.conn().execute("SELECT status, method FROM media_source WHERE media_id=?", (target,)).fetchone()
    assert tuple(r) == ("found", "pixel_hash")


def test_signature_scan(client, monkeypatch):
    """圖上的 @帳號 → Danbooru 已登記的畫師 → 待確認；查不到的只列出、不新建標籤；模糊比對到別人的不採用。"""
    from app import db, ocr, sigscan
    ids = ids_by_name(client)
    a, b, c = ids["img_05.jpg"], ids["img_03.jpg"], ids["img_11.jpg"]
    paths = {r[0]: r[1] for r in db.conn().execute("SELECT id, path FROM media")}
    fake = {paths[a]: [("illust by @someart", 0.95)], paths[b]: [("@nobody_here", 0.97)], paths[c]: [("x.com/fuzzy", 0.97)]}
    monkeypatch.setattr(ocr, "text", lambda p, max_side=1600: fake.get(p, []))
    n_tags = db.conn().execute("SELECT COUNT(*) FROM tags WHERE category='artist'").fetchone()[0]
    state = {}
    sigscan.run(state, lambda: False, all_images=True)
    st = state["sigscan"]
    assert [m["id"] for m in st["matches"]] == [a] and st["added"] == 1, st
    assert "x:nobody_here" in st["unknown"] and "x:fuzzy" in st["unknown"]
    r = db.conn().execute("""SELECT mt.state, mt.tier, mt.source FROM media_tags mt JOIN tags t ON t.id=mt.tag_id
        WHERE mt.media_id=? AND t.name='some_artist'""", (a,)).fetchone()
    assert tuple(r) == (0, 2, "signature")          # 待確認，不是直接確認
    assert db.conn().execute("SELECT COUNT(*) FROM tags WHERE category='artist'").fetchone()[0] <= n_tags + 1
    assert db.tag_id("nobody_here", create=False) is None
    # 圖上寫的是角色本人的帳號（xxx_(artist) 而圖中角色就是 xxx）→ 不算畫師
    client.post("/api/tags/apply", json={"ids": [a], "action": "add", "tag": "some", "category": "character"})
    assert sigscan._is_character_account(a, "some_(artist)") and not sigscan._is_character_account(a, "some_artist")
    # 第二次：辨識結果和帳號反查都用快取
    monkeypatch.setattr(ocr, "text", lambda *a, **k: (_ for _ in ()).throw(AssertionError("不該再辨識")))
    sigscan.run(state, lambda: False, all_images=True, dry_run=True)
    assert state["sigscan"]["resolved"] == 1


def test_series_parent_from_danbooru(client):
    """Danbooru 的 tag implication：limbus_company → project_moon，兩個作品頁合成一個；只有角色 tag 的圖也在作品頁裡；
    只有待確認圖的角色也列出來。"""
    from app import db, online, seriesmap
    ids = ids_by_name(client)
    a, b, c = ids["img_10.jpg"], ids["img_07.jpg"], ids["img_01.jpg"]
    ap = lambda i, t, cat: client.post("/api/tags/apply", json={"ids": [i], "action": "add", "tag": t, "category": cat})
    ap(a, "faust_(project_moon)", "character")                 # 只有角色 tag
    ap(b, "outis_(project_moon)", "character"); ap(b, "project_moon", "copyright"); ap(b, "limbus_company", "copyright")
    client.post("/api/tag/faust_(project_moon)/info", json={"copyright": "limbus_company"})
    client.post("/api/tag/outis_(project_moon)/info", json={"copyright": "project_moon"})
    with db.write_lock:   # 只有候選（待確認）的角色
        from app import candidates
        candidates.add(c, "sinclair_(project_moon)", "character", candidates.T_AI, "model", score=0.8)
    client.post("/api/tag/sinclair_(project_moon)/info", json={"copyright": "limbus_company"})
    seriesmap.run(["limbus_company", "project_moon"])
    assert seriesmap.ancestors("limbus_company") == ["project_moon"]
    sh = client.get("/api/shelf", params={"nsfw": "show"}).json()
    keys = [g["key"] for g in sh["series"]]
    assert "project_moon" in keys and "limbus_company" not in keys
    d = client.get("/api/series/project_moon", params={"nsfw": "show"}).json()
    assert {ch["name"] for ch in d["chars"]} >= {"faust_(project_moon)", "outis_(project_moon)"}
    assert [p["name"] for p in d["pending"]] == ["sinclair_(project_moon)"]
    from app import query
    query._CP_CACHE.clear()
    got = {m["id"] for m in client.get("/api/media", params={"q": "series:project_moon", "nsfw": "show"}).json()["items"]}
    assert {a, b} <= got
    assert client.get("/api/series/limbus_company", params={"nsfw": "show"}).json().get("parent") == "project_moon"


def test_rule34_md5(client, monkeypatch):
    """Rule34 原檔比對：對上 → 記成來源；只有 Danbooru 也有的標籤放進待確認；其他只列出來、不新建。"""
    from app import db, online, r34
    monkeypatch.setitem(httpclient.MIN_INTERVAL, "r34", 0)
    from tests import mock_online
    ids = ids_by_name(client)
    mid = ids["img_08.jpg"]
    fp = db.conn().execute("SELECT fp FROM media WHERE id=?", (mid,)).fetchone()[0]
    client.post("/api/settings", json={"r34_user_id": "42", "r34_api_key": "wrong"})
    assert not r34.test()["ok"]
    client.post("/api/settings", json={"r34_user_id": "42", "r34_api_key": "r34key"})
    assert r34.test()["ok"]
    assert "r34_api_key" not in client.get("/api/export").text
    mock_online.R34_MD5[fp] = 7777
    state = {}
    r34.run(state, lambda: False, limit=100000)
    st = state["r34scan"]
    assert st["found"] == 1 and st["matches"][0]["id"] == mid, st
    assert {"miku_(r34_style)", "weird_artist"} <= set(st["matches"][0]["unmapped"])
    assert st["matches"][0]["mapped"] == ["some_artist", "hatsune_miku", "vocaloid"]   # patreon 不算作品；old_miku_name → 別名合併
    src = client.get(f"/api/media/{mid}").json()["source"]
    assert src["status"] == "found" and src["method"] == "r34_md5" and "7777" in src["post_url"]
    got = {(r[0], r[1], r[2]) for r in db.conn().execute("""SELECT t.name, mt.state, mt.source FROM media_tags mt
        JOIN tags t ON t.id=mt.tag_id WHERE mt.media_id=? AND t.category IN ('artist','character','copyright')""", (mid,))}
    assert ("hatsune_miku", 0, "rule34") in got or ("hatsune_miku", 1, "manual") in got
    assert db.tag_id("weird_artist", create=False) is None and db.tag_id("miku_(r34_style)", create=False) is None
    # 查過的不再查
    r34.run(state, lambda: False)
    assert state["r34scan"]["images"] == 0 or all(m["id"] != mid for m in state["r34scan"]["matches"])


def test_purge_missing(client):
    """找不到檔案的紀錄可以一次清掉（先備份）。"""
    from app import db
    ids = ids_by_name(client)
    with db.write_lock:
        db.conn().execute("UPDATE media SET status='missing' WHERE id=?", (ids["img_11.jpg"],))
    r = client.post("/api/media/purge-missing").json()
    assert r["purged"] == 1
    assert db.conn().execute("SELECT 1 FROM media WHERE id=?", (ids["img_11.jpg"],)).fetchone() is None
    assert client.post("/api/media/purge-missing").json()["purged"] == 0


def test_confirm_confident(client):
    """高信心 AI 候選：用過去的確認紀錄算準確率；樣本不夠不建議；一鍵確認可以撤銷。"""
    from app import candidates, db
    ids = list(ids_by_name(client).values())
    with db.write_lock:
        for i, mid in enumerate(ids[:6]):
            candidates.add(mid, "conf_char", "character", candidates.T_AI, "model", score=0.99)
    tid = db.tag_id("conf_char", create=False)
    n = db.conn().execute("""SELECT COUNT(*) FROM media_tags mt JOIN media m ON m.id=mt.media_id
        WHERE mt.tag_id=? AND mt.state=0 AND m.status='done'""", (tid,)).fetchone()[0]
    assert n >= 3
    d = client.get("/api/review/confidence", params={"nsfw": "show"}).json()
    top = d["levels"][0]
    assert top["pending"] >= n and not top["recommend"]          # 過去沒有紀錄 → 不建議
    assert client.post("/api/review/confirm-confident", json={"min_score": 0.97, "nsfw": "show"}).status_code == 400
    with db.write_lock:     # 造出過去的紀錄：60 筆高信心 AI 候選都被你確認過
        c = db.conn()
        for k in range(4):
            htid = db.tag_id(f"hist_char_{k}", "character")
            c.executemany("INSERT OR REPLACE INTO media_tags(media_id, tag_id, score, source, state, tier) VALUES(?,?,0.99,'model',1,4)",
                          [(m, htid) for m in ids])
    d = client.get("/api/review/confidence", params={"nsfw": "show"}).json()
    assert d["levels"][0]["recommend"]
    r = client.post("/api/review/confirm-confident", json={"min_score": 0.97, "nsfw": "show"}).json()
    assert r["confirmed"] >= n
    assert db.conn().execute("SELECT COUNT(*) FROM media_tags WHERE tag_id=? AND state=1", (tid,)).fetchone()[0] == n
    client.post("/api/undo")
    assert db.conn().execute("SELECT COUNT(*) FROM media_tags WHERE tag_id=? AND state=1", (tid,)).fetchone()[0] == 0


def test_e621(client, monkeypatch):
    """e621：MD5 查原檔；物種 lucario → Danbooru 角色；公司名、非畫師標記不算；以圖搜圖 ≥ 90 分才採用。"""
    from app import db, e621, online
    from tests import mock_online
    monkeypatch.setitem(httpclient.MIN_INTERVAL, "e621", 0)
    monkeypatch.setitem(httpclient.MIN_INTERVAL, "e621_iqdb", 0)
    ids = ids_by_name(client)
    mid = ids["img_07.jpg"]
    fp = db.conn().execute("SELECT fp FROM media WHERE id=?", (mid,)).fetchone()[0]
    with db.write_lock:
        db.conn().execute("DELETE FROM media_source WHERE media_id=?", (mid,))
    mock_online.E621_MD5[fp] = 621
    r = e621.check_one(mid)          # b96：自動比對拿掉，單張（匯入 e621 網址後）還是這個
    assert r["found"] and r["post_id"] == 621, r
    names = {t["name"] for t in client.get(f"/api/media/{mid}").json()["tags"] if t["source"] == "e621"}
    assert names == {"some_artist", "lucario", "pokemon"}, names          # nintendo、conditional_dnp 不算
    src = client.get(f"/api/media/{mid}").json()["source"]
    assert src["method"] == "e621_md5" and src["post_url"].endswith("/posts/621")
    assert any(u["name"] == "oc_fox_(character)" for u in src["data"]["e621"]["unmapped"])
    assert db.tag_id("oc_fox_(character)", create=False) is None
    r = db.conn().execute("""SELECT mt.state, t.category FROM media_tags mt JOIN tags t ON t.id=mt.tag_id
        WHERE mt.media_id=? AND t.name='lucario'""", (mid,)).fetchone()
    assert tuple(r) == (0, "character")
    # 以圖搜圖：分數不夠不採用，夠才採用
    client.post("/api/settings", json={"online_iqdb": True, "online_enabled": True})
    other = ids["img_05.jpg"]
    mock_online.E621_IQDB[:] = [{"score": 70.0, "post": {"posts": mock_online.e621_post(622)}}]
    r0 = client.post(f"/api/online/e621/iqdb/{other}").json()
    assert r0.get("found") is False, r0
    mock_online.E621_IQDB[:] = [{"score": 95.5, "post": {"posts": mock_online.e621_post(623)}}]
    r = client.post(f"/api/online/e621/iqdb/{other}").json()
    assert r["found"] and r["post_id"] == 623
    assert client.get(f"/api/media/{other}").json()["source"]["method"] == "e621_iqdb"


def test_skip_clears_ai_candidates(client):
    """略過（梗圖、截圖剛好有角色）：拿掉 AI 角色候選、之後不再推薦；不像「不是」會否決；可以撤銷。"""
    from app import candidates, db
    mid = ids_by_name(client)["img_10.jpg"]
    with db.write_lock:
        candidates.add(mid, "meme_char", "character", candidates.T_AI, "model", score=0.99)
    tid = db.tag_id("meme_char", create=False)
    r = client.post("/api/media/skip", json={"ids": [mid], "value": True}).json()
    assert r["cleared"] >= 1
    assert db.conn().execute("SELECT 1 FROM media_tags WHERE media_id=? AND tag_id=?", (mid, tid)).fetchone() is None
    with db.write_lock:
        assert not candidates.add(mid, "meme_char", "character", candidates.T_AI, "model", score=0.99)   # 不再推薦
        assert candidates.add(mid, "meme_char", "character", candidates.T_DANBOORU, "danbooru")          # 找到出處的照樣可以
        db.conn().execute("DELETE FROM media_tags WHERE media_id=? AND tag_id=?", (mid, tid))
    client.post("/api/undo")
    st = db.conn().execute("SELECT state FROM media_tags WHERE media_id=? AND tag_id=?", (mid, tid)).fetchone()
    assert st and st[0] == 0      # 撤銷：候選回來、不是否決
    assert db.conn().execute("SELECT unrec_skip FROM media WHERE id=?", (mid,)).fetchone()[0] == 0


def test_pipeline_and_trace(client):
    """整理中心的自動流程（每一步開關、找到多少）＋每張圖的處理紀錄。"""
    d = client.get("/api/pipeline").json()
    keys = [s["key"] for s in d["steps"]]
    # 照新圖真正跑的順序：本機 → 一模一樣 → 相似圖 → 找不到時 → 之後 → 半自動
    assert keys == ["tagger", "pixai", "md5", "pixel", "iqdb", "sig", "r34", "upgrade", "ascii2d", "sweep"]
    stages = [s["stage"] for s in d["steps"]]
    assert stages == sorted(stages, key=["local", "exact", "similar", "fallback", "after", "manual"].index)
    assert all("rate" in s and "rare" in s for s in d["steps"])
    assert client.post("/api/settings", json={"auto_r34": False}).status_code == 200
    r3 = next(s for s in client.get("/api/pipeline").json()["steps"] if s["key"] == "r34")
    assert r3["enabled"] is False
    client.post("/api/settings", json={"auto_r34": True})
    mid = ids_by_name(client)["img_07.jpg"]
    t = client.get(f"/api/media/{mid}/trace").json()["steps"]
    assert t[0]["step"] == "WD Tagger 標記" and t[0]["ok"] is True
    assert any(x["step"] in ("找出處", "Danbooru 原檔") for x in t)


def test_rsearch_endpoint(client):
    """以圖搜圖：不支援的網站回 502、不存在的圖回 404（不會真的連網）。"""
    mid = ids_by_name(client)["img_07.jpg"]
    assert client.post(f"/api/rsearch/google/{mid}").status_code == 502
    assert client.post("/api/rsearch/tineye/999999").status_code == 404


def test_audit(client):
    ids = ids_by_name(client)
    client.post("/api/tags/apply", json={"ids": [ids["img_04.jpg"], ids["img_05.jpg"]], "action": "add", "tag": "audit_artist", "category": "artist"})
    s = client.get("/api/audit/sample", params={"n": 50}).json()
    assert client.get("/api/audit/sample", params={"n": 50, "nsfw": "hide"}).status_code == 200
    got = [x for x in s if x["tag"] == "audit_artist"]
    assert len(got) == 2 and got[0]["method"]
    r = client.post("/api/audit/result", json={"results": [{**got[0], "ok": True}, {**got[1], "ok": False}]}).json()
    assert r["rejected"] == 1
    t = client.get("/api/tag/audit_artist", params={"nsfw": "show"}).json()
    assert t["confirmed"] == 1 and t["rejected"] == 1


def test_delete_tag_and_undo(client):
    """刪除標籤：預設留一個「停止自動標註」空殼（AI 不會再標回來）；撤銷全部還原。"""
    from app import db
    from app.matcher import matcher
    ids = ids_by_name(client)
    a, b = ids["img_07.jpg"], ids["img_08.jpg"]
    client.post("/api/tags/apply", json={"ids": [a, b], "action": "add", "tag": "junk_tag", "category": "custom"})
    client.post("/api/tag/junk_tag/info", json={"other_names": ["垃圾"], "summary": "x", "copyright": ""})
    r = client.post("/api/tag/junk_tag/delete", json={})
    assert r.status_code == 200 and r.json()["removed"] == 2, r.text
    assert client.get("/api/media", params={"q": "junk_tag", "nsfw": "show"}).json()["total"] == 0
    tid = db.tag_id("junk_tag", create=False)
    assert tid and "junk_tag" in matcher.blocked
    assert not db.conn().execute("SELECT 1 FROM tag_info WHERE tag_id=?", (tid,)).fetchone()
    u = client.post("/api/undo", json={}).json()
    assert "刪除標籤" in (u["undone"] or "")
    assert client.get("/api/media", params={"q": "junk_tag", "nsfw": "show"}).json()["total"] == 2
    assert "junk_tag" not in matcher.blocked
    assert "垃圾" in db.conn().execute("SELECT other_names FROM tag_info WHERE tag_id=?", (tid,)).fetchone()[0]
    # 完全刪除（不留空殼）→ 撤銷用同一個編號重建
    assert client.post("/api/tag/junk_tag/delete", json={"block": False}).json()["ok"]
    assert db.tag_id("junk_tag", create=False) is None
    client.post("/api/undo", json={})
    assert db.tag_id("junk_tag", create=False) == tid
    assert client.get("/api/media", params={"q": "junk_tag", "nsfw": "show"}).json()["total"] == 2
    # 名稱裡有斜線的標籤（fate/grand_order）也要能刪
    client.post("/api/tags/apply", json={"ids": [a], "action": "add", "tag": "x/y_tag", "category": "custom"})
    assert client.post("/api/tag/x/y_tag/delete", json={"block": False}).json()["removed"] == 1


def test_every_filter_is_listed():
    """防呆：畫面查詢用到的每個「會讓結果變少」的狀態，都要登記在 FILTER_KEYS 和 activeFilters()，
    空畫面才會把它列出來、可以拿掉。新增篩選卻忘了登記 → 這個測試會失敗。"""
    import re
    from pathlib import Path
    js = Path(__file__).resolve().parent.parent / "static" / "js"
    grid = "\n".join(p.read_text(encoding="utf-8") for p in sorted(js.glob("*.js")))   # 圖庫頁拆成好幾個模組（grid、filters…）
    review = (js / "review.js").read_text(encoding="utf-8")

    def body(src, name):
        i = src.index(f"function {name}(")
        return src[i:src.index("\n}", i)]
    used = set(re.findall(r"\bS\.(\w+)", body(grid, "gridParams") + body(review, "reviewParams")))
    # 不是篩選：目前在哪一頁、哪個分頁、排序、分頁位置
    not_filters = {"rmode", "view", "seriesKey", "seriesData", "curSort", "items", "tag", "miss", "rtier", "rOffset", "albumId", "seed", "until", "sortRev"}   # seed、until＝分頁穩定用（同一次瀏覽固定），不是篩選   # albumId＝在哪一本（頁面本身）
    keys = set(re.findall(r"(\w+):", re.search(r"const FILTER_KEYS = \{([^}]*)\}", grid).group(1)))
    af = body(grid, "activeFilters")
    missing = [k for k in used - not_filters if k not in keys or f"S.{k}" not in af]
    assert not missing, f"這些篩選沒有登記（空畫面會看不到、拿不掉）：{missing}"


def test_series_covers_distinct_and_custom(client):
    """作品封面：多人合照同時是好幾個角色的封面時，三張仍要是不同的圖；自選封面放第一張，可恢復自動。"""
    ids = ids_by_name(client)
    group = ids["img_02.jpg"]
    solo = {"aa_(tst)": ids["img_03.jpg"], "bb_(tst)": ids["img_04.jpg"], "cc_(tst)": ids["img_05.jpg"]}
    for ch, own in solo.items():
        client.post("/api/tags/apply", json={"ids": [group, own], "action": "add", "tag": ch, "category": "character"})
        client.post(f"/api/tag/{ch}/info", json={"copyright": "tst_series", "other_names": [], "summary": ""})
    from app import db
    with db.write_lock:     # 合照設成最新的一張 → 三個角色預設封面都會是它
        db.conn().execute("UPDATE media SET mtime=? WHERE id=?", (time.time() + 999, group))
    g = next(x for x in client.get("/api/shelf", params={"nsfw": "show"}).json()["series"] if x["key"] == "tst_series")
    assert len(g["covers"]) == 3 and len(set(g["covers"])) == 3, g["covers"]
    assert group in g["covers"] and len(g["top_chars"]) == 3
    # 自選封面
    pick = solo["cc_(tst)"]
    assert client.post("/api/series/tst_series/cover", json={"media_id": pick}).json()["ok"]
    g = next(x for x in client.get("/api/shelf", params={"nsfw": "show"}).json()["series"] if x["key"] == "tst_series")
    assert g["covers"][0] == pick and g["custom_cover"] == pick and len(set(g["covers"])) == 3
    assert client.get("/api/series/tst_series", params={"nsfw": "show"}).json()["custom_cover"] == pick
    client.post("/api/series/tst_series/cover", json={"media_id": None})
    g = next(x for x in client.get("/api/shelf", params={"nsfw": "show"}).json()["series"] if x["key"] == "tst_series")
    assert g["custom_cover"] is None
    assert client.post("/api/series//cover", json={"media_id": pick}).status_code in (400, 404, 405)


def test_character_custom_cover(client):
    """角色／創作者自選封面：角色頁、書架、卡片列表都用自選的那張；0 = 恢復自動。"""
    ids = sorted(ids_by_name(client).values())
    a, b = ids[2], ids[3]
    client.post("/api/tags/apply", json={"ids": [a, b], "action": "add", "tag": "cov_(tst)", "category": "character"})
    assert client.post("/api/tag/cov_(tst)", json={"cover": b}).json()["ok"]
    assert client.get("/api/tag/cov_(tst)", params={"nsfw": "show"}).json()["cover_id"] == b
    card = next(x for x in client.get("/api/characters", params={"nsfw": "show", "q": "cov_"}).json() if x["name"] == "cov_(tst)")
    assert card["cover"] == b
    ch = next(x for x in client.get("/api/shelf", params={"nsfw": "show"}).json()["chars"] if x["name"] == "cov_(tst)")
    assert ch["cover"] == b
    client.post("/api/tag/cov_(tst)", json={"cover": 0})
    assert client.get("/api/tag/cov_(tst)", params={"nsfw": "show"}).json()["cover"] is None


def test_duplicate_reuse_is_strict(client):
    """「沿用重複圖的結果」只給真正的複本：dHash 撞在一起的不同圖（長寬比不同／內容不同）不能沿用；
    推測來的結果（從畫師反推等）不能再傳下去；以前沿用錯的可以修正。"""
    import json as _j
    from app import db, online, pipeline
    c = db.conn()
    ids = sorted(ids_by_name(client).values())
    a, b, cc = ids[9], ids[10], ids[11]
    ra = c.execute("SELECT dhash, embedding FROM media WHERE id=?", (a,)).fetchone()
    post = {"characters": ["wrong_char_(tst)"], "copyrights": [], "artists": [], "post_url": "x", "source": ""}
    with db.write_lock:
        # b：dHash 跟 a 一樣、內容一樣，但長寬比差很多（實際發生的情況：1600×2000 對上 1200×2035）
        c.execute("UPDATE media SET dhash=?, embedding=?, width=1600, height=2000 WHERE id=?", (ra[0], ra[1], a))
        c.execute("UPDATE media SET dhash=?, embedding=?, width=1200, height=2035 WHERE id=?", (ra[0], ra[1], b))
        # cc：真正的複本（同 dHash、同比例、同內容）
        c.execute("UPDATE media SET dhash=?, embedding=?, width=800, height=1000 WHERE id=?", (ra[0], ra[1], cc))
        c.execute("DELETE FROM media_source WHERE media_id IN (?,?,?)", (a, b, cc))
        c.execute("""INSERT INTO media_source(media_id, status, method, similarity, data, fetched_at)
                     VALUES(?, 'found', 'artist_scan', 95, ?, 0)""", (b, _j.dumps({"danbooru": post})))
        c.commit()
    import shutil as _sh
    from app import media as _media
    _sh.copyfile(_media.thumb_path(a), _media.thumb_path(cc))   # 真正的複本：畫面也要一樣（逐像素驗證）
    pipeline.dup_cache.clear()
    row = lambda m: c.execute("SELECT id, dhash, width, height, embedding FROM media WHERE id=?", (m,)).fetchone()
    assert not pipeline.same_image(row(a), row(b)), "長寬比不同不能算同一張"
    assert pipeline.same_image(row(a), row(cc))
    assert not any(a in g and b in g for g in pipeline.find_duplicates(3)), "重複頁也不能把它們放一起"
    assert online.duplicate_source(a) is None, "b 不是同一張，而且它的結果是推測的"
    # 真正的複本有確定的結果 → 可以沿用
    with db.write_lock:
        c.execute("""INSERT INTO media_source(media_id, status, method, similarity, data, fetched_at)
                     VALUES(?, 'found', 'iqdb', 96, ?, 0)""", (cc, _j.dumps({"danbooru": post})))
        c.commit()
    pipeline.dup_cache.clear()
    got = online.duplicate_source(a)
    assert got and got["media_id"] == cc
    # 內容不同（embedding 差很多）也不行
    import numpy as np
    other = (np.random.default_rng(1).standard_normal(len(ra[1]) // 2)).astype(np.float16).tobytes()
    with db.write_lock:
        c.execute("UPDATE media SET embedding=? WHERE id=?", (other, cc)); c.commit()
    pipeline.dup_cache.clear()
    assert online.duplicate_source(a) is None
    # 修正以前沿用錯的：a 以前沿用了 b 的結果，還帶來一個未確認的候選
    with db.write_lock:
        c.execute("""INSERT OR REPLACE INTO media_source(media_id, status, method, similarity, data, fetched_at)
                     VALUES(?, 'found', 'duplicate', 98, ?, 0)""", (a, _j.dumps({"danbooru": post, "dup_of": b})))
        tid = db.tag_id("wrong_char_(tst)", "character")
        c.execute("INSERT OR REPLACE INTO media_tags(media_id, tag_id, score, source, state, tier) VALUES(?,?,0.98,'danbooru',0,2)", (a, tid))
        c.commit()
    rep = client.post("/api/online/repair-dup-reuse", json={"dry_run": True}).json()
    hit = [x for x in rep["bad"] if x["id"] == a]
    assert hit and hit[0]["pending"] == ["wrong_char_(tst)"]
    assert c.execute("SELECT 1 FROM media_source WHERE media_id=?", (a,)).fetchone(), "dry_run 不能改東西"
    client.post("/api/online/repair-dup-reuse", json={"dry_run": False})
    assert not c.execute("SELECT 1 FROM media_source WHERE media_id=? AND method='duplicate'", (a,)).fetchone()
    assert not c.execute("SELECT 1 FROM media_tags WHERE media_id=? AND tag_id=? AND state=0", (a, tid)).fetchone()
    wait_idle(client)


def test_eval_tagger_runs(client):
    """驗證工具：能跑完（唯讀），重算的分數要跟模型存的分數一致才報告。"""
    r = client.get("/api/diag/eval-tagger")
    assert r.status_code == 200, r.text
    d = r.json()
    assert "search" in d and d["search"]["anime_images"] > 0
    t = d["tagger"]
    assert "error" in t or (t["check"]["mean_abs_diff"] <= 0.02 and "0.85" in t["by_threshold"])


def test_import_from_sites(client):
    """從網站匯入：只收支援網站的貼文網址；檔名讓現有流程認得（X 推文 → Danbooru、Pixiv ID）；
    X 的 @帳號對到 Danbooru 登記的畫師；同一個檔案不重複存；瀏覽器按鈕要配對金鑰。"""
    import os as _os
    from app import db, importer
    client.post("/api/settings", json={"online_enabled": True})
    try:
        # 不支援的網址（不能拿來替任意網址下載）
        for bad in ("https://example.com/a.jpg", "https://x.com/someart", "file:///etc/passwd", "http://127.0.0.1:7860/api/x"):
            r = client.post("/api/fetch-url", json={"url": bad}).json()
            assert not r["ok"] and "不支援" in r["error"], bad
        assert importer.parse("https://x.com/someart/status/1790000000000000001/photo/2")["index"] == 2
        assert importer.parse("https://rule34.xxx/index.php?page=post&s=view&id=123")["id"] == "123"
        # X：兩張圖、敏感內容
        r = client.post("/api/fetch-url", json={"url": "https://x.com/someart/status/1790000000000000001"}).json()
        assert r["ok"] and len(r["saved"]) == 2 and r["author"] == "someart", r
        assert all(os.sep + "匯入" + os.sep + "x" + os.sep in p for p in r["saved"])
        assert _os.path.basename(r["saved"][0]) == "x_1790000000000000001_AbCdEfGh12345Xy.jpg"
        # Pixiv（要帶 Referer 才下載得到）
        r2 = client.post("/api/fetch-url", json={"url": "https://www.pixiv.net/artworks/55556666"}).json()
        assert r2["ok"] and _os.path.basename(r2["saved"][0]) == "55556666_p0.png", r2
        # Danbooru 原檔跟圖庫裡某張一模一樣 → 不重複存
        r3 = client.post("/api/fetch-url", json={"url": "https://danbooru.donmai.us/posts/4242"}).json()
        assert r3["ok"] and not r3["saved"] and r3["existing"], r3
        # Danbooru 新圖：圖片伺服器會擋「假裝成瀏覽器」的下載 → 要能存下來
        r4 = client.post("/api/fetch-url", json={"url": "https://danbooru.donmai.us/posts/4343?q=test"}).json()
        assert r4["ok"] and _os.path.basename(r4["saved"][0]) == "c" * 32 + ".png", r4
        assert not _os.path.exists(r4["saved"][0] + ".part")
        # 同一篇再匯入一次 → 已經有了
        again = client.post("/api/fetch-url", json={"url": "https://x.com/someart/status/1790000000000000001"}).json()
        assert again["ok"] and not again["saved"] and len(again["existing"]) == 2
        wait_idle(client)
        import time as _t
        for _ in range(60):          # 背景：搜源、畫師對應
            c = db.conn()
            mid = c.execute("SELECT id FROM media WHERE path=?", (r["saved"][0],)).fetchone()
            done = mid and c.execute("SELECT 1 FROM media_source WHERE media_id=?", (mid[0],)).fetchone()
            if done:
                break
            _t.sleep(0.5)
        mid = mid[0]
        d = client.get(f"/api/media/{mid}").json()
        names = {(t["name"], t["source"], t["state"]) for t in d["tags"]}
        assert ("ceres_fauna", "danbooru", 1) in names, names          # 推文 ID → Danbooru 同一則推文 → 直接確認
        assert d["nsfw"], "X 標成敏感內容 → NSFW"
        pm = c.execute("SELECT id FROM media WHERE path=?", (r2["saved"][0],)).fetchone()[0]
        for _ in range(60):
            ps = c.execute("SELECT pixiv_id FROM media_source WHERE media_id=?", (pm,)).fetchone()
            if ps:
                break
            _t.sleep(0.5)
        dbg = [tuple(x) for x in c.execute("SELECT i.site, i.applied, m.status, m.id FROM import_meta i LEFT JOIN media m ON m.path=i.path")]
        assert ps and ps[0] == 55556666, (dbg, [tuple(x) for x in c.execute("SELECT media_id, status, method, error FROM media_source WHERE media_id=?", (pm,))])
    finally:
        client.post("/api/settings", json={"online_enabled": False})
    # 瀏覽器按鈕：沒金鑰、錯金鑰都擋；有金鑰就算是別的網站送來的也收
    xs = {"Origin": "https://x.com", "Sec-Fetch-Site": "cross-site"}
    assert client.post("/api/fetch-url/ext", json={"url": "https://x.com/a/status/1"}, headers=xs).status_code == 403
    assert client.post("/api/fetch-url/ext", json={"url": "https://x.com/a/status/1"}, headers={**xs, "X-Import-Key": "wrong"}).status_code == 403
    k = importer.import_key()
    ok = client.post("/api/fetch-url/ext", json={"url": "https://example.com/x.jpg"}, headers={**xs, "X-Import-Key": k})
    assert ok.status_code == 200 and not ok.json()["ok"]
    # 別的 /api 網址不會因為帶了金鑰就放行跨站請求
    assert client.post("/api/undo", json={}, headers={**xs, "X-Import-Key": k}).status_code == 403
    # 腳本：帶金鑰、純文字、不能被別的網站用 <script> 載入
    us = client.get("/api/fetch-url/userscript.user.js")
    assert us.status_code == 200 and k in us.text and us.headers["x-content-type-options"] == "nosniff" and us.headers["content-type"].startswith("text/plain")
    assert client.get("/api/fetch-url/userscript.user.js", headers={"Sec-Fetch-Dest": "script"}).status_code == 403
    assert "import_key" not in client.get("/api/settings").json()


def test_site_tags(client):
    """網站的標籤：Danbooru 一般標籤（同一張圖 → 已檢查並收掉 AI 猜錯的；不動你否決過／手動加的）、
    日文標籤 → Danbooru 角色（只當候選）、以前找到出處的圖可以補抓。"""
    import json as _j
    from app import db, importer, sitetags
    c = db.conn()
    ids = sorted(ids_by_name(client).values())
    mid = ids[12]
    with db.write_lock:
        for name, src, st, sc in (("red_hair", "model", 0, 0.5), ("pink_hair", "model", -1, 0.5), ("smile", "manual", 1, 0.5), ("1girl", "model", 0, 0.5),
                                  ("blush", "model", 0, 0.93)):
            tid = db.tag_id(name, "general")
            c.execute("INSERT OR REPLACE INTO media_tags(media_id, tag_id, score, source, state) VALUES(?,?,?,?,?)", (mid, tid, sc, src, st))
        r = sitetags.apply_general(mid, ["1girl", "pink_hair", "twintails"], "danbooru", exact=True)
        c.commit()
    rows = {x[0]: (x[1], x[2]) for x in c.execute("""SELECT t.name, mt.source, mt.state FROM media_tags mt JOIN tags t ON t.id=mt.tag_id
                                                     WHERE mt.media_id=? AND t.category='general'""", (mid,))}
    assert rows["1girl"] == ("danbooru", 1) and rows["twintails"] == ("danbooru", 1)
    assert rows["pink_hair"] == ("model", -1), "你否決過的不能被 Danbooru 蓋掉"
    assert rows["smile"] == ("manual", 1), "你手動加的不動"
    assert "red_hair" not in rows, "同一張圖：AI 低分猜、Danbooru 沒有的收掉"
    assert rows["blush"] == ("model", 0), "AI 高分（≥0.8）的留著：Danbooru 常漏標"
    assert r["removed"] >= 1
    # 相似圖（不是同一張）：加進來但未檢查，AI 的不動
    mid2 = ids[13]
    with db.write_lock:
        c.execute("INSERT OR REPLACE INTO media_tags(media_id, tag_id, score, source, state) VALUES(?,?,0.5,'model',0)", (mid2, db.tag_id("red_hair", "general")))
        sitetags.apply_general(mid2, ["1girl"], "danbooru", exact=False)
        c.commit()
    rows2 = {x[0]: (x[1], x[2]) for x in c.execute("""SELECT t.name, mt.source, mt.state FROM media_tags mt JOIN tags t ON t.id=mt.tag_id
                                                      WHERE mt.media_id=? AND t.category='general'""", (mid2,))}
    assert rows2["red_hair"] == ("model", 0) and rows2["1girl"][0] in ("danbooru", "model") and rows2["1girl"][1] == 0
    # 日文標籤 → Danbooru 角色；「らくがき」這種一般詞不查
    client.post("/api/settings", json={"online_enabled": True})
    try:
        m = sitetags.jp_to_danbooru(["#初音ミク", "らくがき", "完全不存在的標籤"])
        assert m == {"初音ミク": [{"name": "hatsune_miku", "category": "character"}]}, m
        # X hashtag → 候選（Danbooru 沒給角色的圖）
        n = importer._apply_hashtags(ids[14], ["初音ミク", "らくがき"])
        t = c.execute("""SELECT mt.source, mt.state, mt.reason FROM media_tags mt JOIN tags t ON t.id=mt.tag_id
                         WHERE mt.media_id=? AND t.name='hatsune_miku'""", (ids[14],)).fetchone()
        dbg = [tuple(x) for x in c.execute("SELECT t.name, mt.source, mt.state, mt.reason FROM media_tags mt JOIN tags t ON t.id=mt.tag_id WHERE mt.media_id=? AND mt.source='x'", (ids[14],))]
        assert n == 1 and t and t[0] == "x" and t[1] == 0 and "#初音ミク" in t[2], (n, dbg[:8])
        # 補抓：以前找到 Danbooru 出處、還沒有一般標籤的圖
        with db.write_lock:
            c.execute("""INSERT OR REPLACE INTO media_source(media_id, status, method, similarity, data, fetched_at)
                         VALUES(?, 'found', 'md5', 100, ?, 0)""", (ids[15], _j.dumps({"danbooru": {"post_id": 31337, "characters": []}})))
            c.commit()
        rep = client.post("/api/online/backfill-site-tags").json()
        assert rep["general"]["images"] >= 1
        got = {x[0]: x[1] for x in c.execute("""SELECT t.name, mt.state FROM media_tags mt JOIN tags t ON t.id=mt.tag_id
                                                WHERE mt.media_id=? AND mt.source='danbooru' AND t.category='general'""", (ids[15],))}
        assert got.get("1girl") == 1 and got.get("pink_hair") == 1, got
    finally:
        client.post("/api/settings", json={"online_enabled": False})


def test_eval_general_runs(client):
    r = client.get("/api/diag/eval-general")
    assert r.status_code == 200, r.text
    assert "images" in r.json() or "error" in r.json()


def test_unregistered_artist_then_merge(client):
    """Danbooru 還沒登記的畫師：先用網站帳號建立創作者（放進待確認），之後 Danbooru 登記了就自動合併。"""
    import httpx
    from app import db, siteartists
    from tests.conftest import MOCK_PORT
    client.post("/api/settings", json={"online_enabled": True})
    try:
        r = client.post("/api/fetch-url", json={"url": "https://x.com/Later_Art/status/1790000000000000002"}).json()
        assert r["ok"] and r["saved"], r
        c = db.conn()
        import time as _t
        for _ in range(60):
            row = c.execute("""SELECT t.name, mt.state, mt.reason FROM media_tags mt JOIN tags t ON t.id=mt.tag_id JOIN media m ON m.id=mt.media_id
                               WHERE m.path=? AND t.category='artist'""", (r["saved"][0],)).fetchone()
            if row:
                break
            _t.sleep(0.5)
        assert row and row[0] == "later_art" and row[1] == 0 and "還沒登記" in row[2], row
        info = client.get("/api/tag/later_art").json()["info"]
        assert "https://x.com/Later_Art" in info["links"], "資料卡要有 X 帳號連結"
        # 還沒登記 → 不動
        assert client.post("/api/online/recheck-artists").json()["merged"] == []
        # Danbooru 登記了 → 合併到正式名稱，舊名稱變別名
        httpx.post(f"http://127.0.0.1:{MOCK_PORT}/_later")
        merged = client.post("/api/online/recheck-artists").json()["merged"]
        assert ["later_art", "later_official"] in merged, merged
        assert db.tag_id("later_art", create=False) == db.tag_id("later_official", create=False)
        # 狀態標記不能變成畫師
        assert siteartists.is_meta("artist_request") and siteartists.is_meta("anonymous_artist") and not siteartists.is_meta("sakimichan")
    finally:
        client.post("/api/settings", json={"online_enabled": False})


def test_official_kinds(client):
    """官方種類（動畫截圖、小說插圖…）：手動設定優先、自動的不蓋掉；不算缺創作者；可撤銷；Danbooru 標了就自動判斷。
    （b96 拿掉 trace.moe：只剩手動和 Danbooru 一般標籤兩個來源）"""
    from app import db, official
    from tests.conftest import make_image
    make_image(LIB / "anime" / "frame_1080p.jpg", 900, size=(1280, 720))
    make_image(LIB / "anime" / "tall.jpg", 901, size=(400, 700))
    client.post("/api/jobs", json={"kind": "scan"})
    wait_idle(client)
    ids = ids_by_name(client)
    fr, tall = ids["frame_1080p.jpg"], ids["tall.jpg"]
    c = db.conn()
    assert official.from_danbooru(fr, ["1girl", "anime_screencap"]) == "anime"
    assert tuple(c.execute("SELECT official, official_src FROM media WHERE id=?", (fr,)).fetchone()) == ("anime", "danbooru")
    found = lambda q: {it["id"] for it in client.get("/api/media", params={"q": q, "limit": 500, "nsfw": "show"}).json()["items"]}
    assert fr in found("type:anime") and fr in found("is:official") and tall not in found("type:anime")
    miss = {it["id"] for it in client.get("/api/media", params={"view": "unrecognized", "miss": "artist", "limit": 500, "nsfw": "show"}).json()["items"]}
    assert fr not in miss and tall in miss
    # 手動改成小說插圖 → 自動的不會蓋回去；撤銷回到自動判斷
    assert client.post("/api/media/official", json={"ids": [fr], "kind": "novel"}).json()["n"] == 1
    official.set_kind([fr], "anime", "danbooru")
    assert c.execute("SELECT official FROM media WHERE id=?", (fr,)).fetchone()[0] == "novel"
    assert fr not in {it["id"] for it in client.get("/api/media", params={"view": "unrecognized", "miss": "artist", "limit": 500, "nsfw": "show"}).json()["items"]}
    assert fr in {it["id"] for it in client.get("/api/media", params={"view": "official", "mtype": "novel", "limit": 500, "nsfw": "show"}).json()["items"]}
    assert fr not in {it["id"] for it in client.get("/api/media", params={"view": "unrecognized", "miss": "all", "limit": 500, "nsfw": "show"}).json()["items"]}
    client.post("/api/undo")
    assert tuple(c.execute("SELECT official, official_src FROM media WHERE id=?", (fr,)).fetchone()) == ("anime", "danbooru")
    assert official.from_danbooru(tall, ["1girl", "novel_illustration"]) == "novel"
    assert c.execute("SELECT official FROM media WHERE id=?", (tall,)).fetchone()[0] == "novel"
    with db.write_lock:
        c.execute("UPDATE media SET official=NULL, official_src=NULL WHERE id IN (?,?)", (fr, tall))
        c.commit()


def test_duplicate_pixel_and_crop(client):
    """重複圖：解析度不同算重複；只改一小塊（表情、對話框）不算；局部截圖另外標出來、建議保留完整的。"""
    from PIL import Image, ImageDraw
    from app import pipeline
    from tests.conftest import make_image
    d = LIB / "dupv"
    base = make_image(d / "base.jpg", 310, size=(500, 700))
    im = Image.open(base)
    im.resize((250, 350), Image.LANCZOS).save(d / "base_small.png")
    v = im.copy()
    ImageDraw.Draw(v).rectangle((200, 240, 260, 262), fill=(250, 250, 250))    # 對話框換字
    v.save(d / "variant.jpg", quality=92)
    im.crop((60, 90, 360, 450)).resize((450, 540), Image.LANCZOS).save(d / "crop.jpg", quality=92)
    client.post("/api/jobs", json={"kind": "scan"})
    wait_idle(client)
    ids = ids_by_name(client)
    b, s, var, cr = ids["base.jpg"], ids["base_small.png"], ids["variant.jpg"], ids["crop.jpg"]
    assert pipeline.pixel_same(b, s) is True
    assert pipeline.pixel_same(b, var) is False, "差分不算重複"
    old = pipeline.CROP_COS, pipeline._crop_candidates
    pipeline.CROP_COS = -1.0            # 假模型的特徵不準：測試只看像素驗證這一段
    # 只驗證這個資料夾的圖（測試圖庫越長越大，全部兩兩比會跑好幾分鐘）
    mine = {b, s, var, cr}
    pipeline._crop_candidates = lambda: [(x, y) for x in sorted(mine) for y in sorted(mine) if x < y]
    try:
        pipeline.crop_scan(wait=True)
    finally:
        pipeline.CROP_COS, pipeline._crop_candidates = old
    pipeline.dup_cache.clear()
    groups = pipeline.find_duplicates(3)
    g = next(g for g in groups if b in g)
    assert s in g and var not in g and cr in g, (g, var)
    r = client.get("/api/duplicates", params={"nsfw": "show"}).json()
    grp = next(x for x in r["groups"] if any(i["id"] == b for i in x))
    crop_item = next(i for i in grp if i["id"] == cr)
    assert crop_item["crop_of"] in (b, s) and grp[-1]["id"] == cr, ("局部截圖排最後、不會被建議保留", grp, pipeline.crop_links())
    assert not any(l["small"] == var or l["big"] == var for l in pipeline.crop_links())


def test_character_knn(client):
    """新角色辨識：用你確認過的圖推薦（WD 不認得的角色也可以）；只進待確認；否決過的不會再推薦。"""
    import random as _r
    from PIL import Image, ImageDraw
    from app import charknn, db
    from app.api.common import _apply

    COL = {610: (235, 30, 40), 620: (30, 40, 235)}

    def fam(path, seed, k):             # 同一個角色：同樣的底圖，細節不同（假模型只看得出顏色 → 底色差很多）
        img = Image.open(make_image(path, seed, size=(420, 560), color=COL[seed]))
        rnd = _r.Random(seed * 100 + k)
        d = ImageDraw.Draw(img)
        for _ in range(3):
            x, y = rnd.randrange(420), rnd.randrange(560)
            d.ellipse((x - 12, y - 12, x + 12, y + 12), fill=tuple(rnd.randrange(256) for _ in range(3)))
        img.save(path, quality=92)
    from tests.conftest import make_image
    for k in range(4):
        fam(LIB / "knn" / f"f{k % 2}" / f"a{k}.jpg", 610, k)
        fam(LIB / "knn" / f"f{k % 2}" / f"b{k}.jpg", 620, k)
    fam(LIB / "knn" / "new" / "a_new.jpg", 610, 9)
    make_image(LIB / "knn" / "new" / "other.jpg", 630, size=(420, 560), color=(20, 200, 30))
    client.post("/api/jobs", json={"kind": "scan"})
    wait_idle(client)
    ids = ids_by_name(client)
    with db.write_lock:
        _apply("add", "oc_alpha_(test)", [ids[f"a{k}.jpg"] for k in range(4)], "character")
        _apply("add", "oc_beta_(test)", [ids[f"b{k}.jpg"] for k in range(4)], "character")
        db.conn().commit()
    ev = charknn.evaluate()
    assert ev["n"] >= 8 and "precision" in ev and ev["thresholds"]["k"] == 10, ev
    assert ev["learning"]["mode"] == "rules", "按過的是／不是太少：只用規則"
    v0 = client.get("/api/knn/live").json()["version"]
    r = charknn.run(block=True)
    assert "error" not in r, r
    new, other = ids["a_new.jpg"], ids["other.jpg"]
    rv = client.get("/api/review", params={"tier": "library", "nsfw": "show", "limit": 500}).json()
    mine = [it for it in rv["items"] if it["id"] == new]
    dbg = [tuple(x) for x in db.conn().execute("SELECT status, method, substr(data,1,200) FROM media_source WHERE media_id=?", (new,))]
    dbg2 = [tuple(x) for x in db.conn().execute("SELECT t.name, mt.state, mt.source, mt.tier FROM media_tags mt JOIN tags t ON t.id=mt.tag_id WHERE mt.media_id=? AND t.category!='general'", (new,))]
    assert mine and mine[0]["tag"] == "oc_alpha_(test)" and mine[0]["source"] == "knn", (r, rv["counts"], dbg, dbg2)
    assert rv["knn_version"] > v0 and client.get("/api/knn/live").json()["version"] == rv["knn_version"], "推薦有變 → 版本 +1（待確認頁靠這個自己更新）"
    assert mine[0]["refs"] and set(mine[0]["refs"]) <= {ids[f"a{k}.jpg"] for k in range(4)}
    assert not any(it["id"] == other and it["tag"].startswith("oc_") for it in rv["items"]), "不像這兩個角色 → 不推薦它們"
    assert rv["counts"]["library"] >= 1 and not any(it["source"] == "knn" for it in
                                                   client.get("/api/review", params={"tier": "ai", "nsfw": "show", "limit": 500}).json()["items"])
    # 否決 → 重算也不會再推薦
    client.post("/api/review/decide", json={"pairs": [[new, "oc_alpha_(test)"]], "accept": False})
    charknn.run(block=True)
    st = db.conn().execute("""SELECT mt.state FROM media_tags mt JOIN tags t ON t.id=mt.tag_id
                              WHERE mt.media_id=? AND t.name='oc_alpha_(test)'""", (new,)).fetchone()
    assert st[0] == -1


def test_series_without_characters_rename(client):
    """只有作品標籤、沒有角色的作品（例如原創系列）：作品頁可以改名，也看得到張數。"""
    from app import db
    from app.api.common import _apply
    ids = sorted(ids_by_name(client).values())[:3]
    with db.write_lock:
        _apply("add", "Solo_Work_Tst", ids, "copyright")
        db.conn().commit()
    assert client.post("/api/tag/Solo_Work_Tst", json={"display_name": "我的原創系列"}).status_code == 200
    d = client.get("/api/series/Solo_Work_Tst", params={"nsfw": "show"}).json()
    assert d["display"] == "我的原創系列" and d["n"] == 3 and d["covers"], d


def test_multi_person_images(client):
    """多人圖：確認一個角色後不會就此消失——待確認回傳「還有誰」、未識別有「多人圖缺角色」、可以按「標完了」。"""
    from app import candidates, db, people
    ids = sorted(ids_by_name(client).values())
    mid = ids[5]
    c = db.conn()
    with db.write_lock:
        # 前面的測試可能讓這幾張「在 Danbooru 找到出處」；那種圖不提醒缺人（見下面 mid3），這裡先清掉
        c.execute("DELETE FROM media_source WHERE media_id IN (?,?,?)", (ids[5], ids[6], ids[7]))
        tid = db.tag_id("2girls", "general")
        c.execute("INSERT OR REPLACE INTO media_tags(media_id, tag_id, score, source, state) VALUES(?,?,0.93,'model',0)", (mid, tid))
        c.execute("DELETE FROM media_tags WHERE media_id=? AND tag_id IN (SELECT id FROM tags WHERE category='character')", (mid,))
        candidates.add(mid, "duo_left_(tst)", "character", candidates.T_AI, "model", score=0.9)
        c.commit()
    people.refresh([mid])
    assert people.info(mid)["people"] == 2
    r = client.post("/api/review/decide", json={"pairs": [[mid, "duo_left_(tst)"]], "accept": True}).json()
    fu = [f for f in r["followup"] if f["id"] == mid]
    assert fu and fu[0]["people"] == 2 and fu[0]["chars"] == 1 and fu[0]["have"][0]["name"] == "duo_left_(tst)", r
    d = client.get(f"/api/media/{mid}").json()
    assert d["crowd"]["short"] and d["crowd"]["people"] == 2
    inpeople = lambda: mid in {it["id"] for it in client.get("/api/media", params={"view": "unrecognized", "miss": "people", "nsfw": "show", "limit": 1000}).json()["items"]}
    assert inpeople()
    assert client.get("/api/sidebar", params={"nsfw": "show"}).json()["counts"]["miss_people"] >= 1
    # 加上第二個角色 → 不再缺人
    r2 = client.post("/api/tags/apply", json={"ids": [mid], "action": "add", "tag": "duo_right_(tst)", "category": "character"}).json()
    assert r2["crowd_short"] == 0 and not inpeople()
    # 另一張：只標一個，按「標完了」
    mid2 = ids[6]
    with db.write_lock:
        c.execute("INSERT OR REPLACE INTO media_tags(media_id, tag_id, score, source, state) VALUES(?,?,0.93,'model',0)", (mid2, db.tag_id("multiple_girls", "general")))
        c.execute("DELETE FROM media_tags WHERE media_id=? AND tag_id IN (SELECT id FROM tags WHERE category='character')", (mid2,))
        c.execute("UPDATE media SET people_done=0 WHERE id=?", (mid2,))
        c.commit()
    people.refresh([mid2])
    r3 = client.post("/api/tags/apply", json={"ids": [mid2], "action": "add", "tag": "duo_left_(tst)", "category": "character"}).json()
    assert r3["crowd_short"] == 1
    assert client.post("/api/media/people-done", json={"ids": [mid2]}).json()["n"] == 1
    assert not people.info(mid2)["short"]
    assert people.estimate({"1girl", "1boy"}) == 2 and people.estimate({"solo"}) == 1 and people.estimate({"no_humans", "2girls"}) == 0
    # Danbooru／Pixiv 標好角色的圖：其他人當路人，不提醒（使用者：「基本都是路人」）
    mid3 = ids[7]
    with db.write_lock:
        c.execute("INSERT OR REPLACE INTO media_tags(media_id, tag_id, score, source, state) VALUES(?,?,0.93,'model',0)", (mid3, db.tag_id("3girls", "general")))
        c.execute("DELETE FROM media_tags WHERE media_id=? AND tag_id IN (SELECT id FROM tags WHERE category='character')", (mid3,))
        c.execute("UPDATE media SET people_done=0 WHERE id=?", (mid3,))
        c.execute("INSERT OR REPLACE INTO media_tags(media_id, tag_id, score, source, state) VALUES(?,?,NULL,'danbooru',1)",
                  (mid3, db.tag_id("duo_left_(tst)", "character")))
        c.commit()
    people.refresh([mid3])
    info = people.info(mid3)
    assert info["people"] == 3 and not info["short"] and info["site"]
    assert mid3 not in {it["id"] for it in client.get("/api/media", params={"view": "unrecognized", "miss": "people", "nsfw": "show", "limit": 1000}).json()["items"]}
    with db.write_lock:   # 同一張圖如果是手動標的 → 照樣提醒
        c.execute("UPDATE media_tags SET source='manual' WHERE media_id=? AND tag_id=?", (mid3, db.tag_id("duo_left_(tst)", create=False)))
        c.commit()
    assert people.info(mid3)["short"]


def test_jobs_overview(client):
    """背景工作面板：正在做、排隊中、排程、最近完成；排隊中的可以取消。"""
    from app import online
    d = client.get("/api/jobs/overview").json()
    assert {"running", "queued", "scheduled", "history"} <= set(d) and any("備份" in s["label"] for s in d["scheduled"])
    oj = online.get_jobs()
    oj.submit("taginfo", ["zz_fake_(tst)"])   # 線上功能關著：佇列暫停，會一直排著
    d = client.get("/api/jobs/overview").json()
    q = [x for x in d["queued"] if x["kind"] == "taginfo"]
    mine = q[-1]              # 前面的測試可能也排了（線上功能關著，會一直排著）：取消剛剛加的最後一項
    assert mine["label"] == "抓取角色資料" and mine["n"] == 1
    assert client.post("/api/jobs/cancel", json={"queue": "online", "index": mine["index"]}).json()["ok"]
    assert len([x for x in client.get("/api/jobs/overview").json()["queued"] if x["kind"] == "taginfo"]) == len(q) - 1


def test_series_guess_for_unknown_characters(client):
    """還不知道作品的角色：名字尾巴是作品、或至少 2 張同圖都是同一部作品 → 書架先推測分組（標「推測」）；只有 1 張不推。"""
    from app import db, seriesguess
    from app.api.common import _apply
    ids = sorted(ids_by_name(client).values())[-4:]
    with db.write_lock:
        ph = ",".join("?" * len(ids))
        db.conn().execute(f"DELETE FROM media_tags WHERE media_id IN ({ph}) AND tag_id IN (SELECT id FROM tags WHERE category='copyright')", ids)
        _apply("add", "tstseries_x", ids[:3], "copyright")
        _apply("add", "karen_(tstseries_x)", ids[:1], "character")        # 名字尾巴
        _apply("add", "oc_girl_(tst_artist)", ids[:2], "character")       # 2 張都是 tstseries_x
        _apply("add", "lonely_cosplay_(tst)", ids[2:3], "character")      # 只有 1 張：不推
        db.conn().commit()
    seriesguess._cache["sig"] = None
    g = seriesguess.guess_all()
    assert g.get("karen_(tstseries_x)") == "tstseries_x" and g.get("oc_girl_(tst_artist)") == "tstseries_x"
    assert "lonely_cosplay_(tst)" not in g
    sh = client.get("/api/shelf", params={"nsfw": "show"}).json()
    k = next(c for c in sh["chars"] if c["name"] == "karen_(tstseries_x)")
    assert k["series"] == "tstseries_x" and k["series_guess"] and "unknown" in sh and "unfetched" in sh
    lst = client.get("/api/characters", params={"nsfw": "show", "q": "karen_(tst"}).json()
    assert lst and lst[0]["copyright"] == "tstseries_x" and lst[0]["copyright_guess"]


def test_tag_suggest(client):
    """加標籤的建議：這幾張的候選、同作品的角色；沒有 1girl 這種描述。"""
    from app import candidates, db
    from app.api.common import _apply
    ids = sorted(ids_by_name(client).values())[:2]
    with db.write_lock:
        _apply("add", "sugg_series_(tst)", ids, "copyright")
        _apply("add", "mate_a_(tst)", ids[:1], "character")
        c = db.conn()
        c.execute("INSERT OR REPLACE INTO tag_info(tag_id, status, copyright) VALUES(?, 'ok', 'sugg_series_(tst)')", (db.tag_id("mate_a_(tst)"),))
        c.execute("INSERT OR REPLACE INTO tag_info(tag_id, status, copyright) VALUES(?, 'ok', 'sugg_series_(tst)')", (db.tag_id("mate_b_(tst)", "character"),))
        _apply("add", "mate_b_(tst)", [sorted(ids_by_name(client).values())[-1]], "character")
        c.execute("INSERT OR REPLACE INTO media_tags(media_id, tag_id, score, source, state, tier) VALUES(?,?,0.9,'model',0,4)",
                  (ids[1], db.tag_id("cand_c_(tst)", "character")))
        c.commit()
    d = client.post("/api/tags/suggest", json={"ids": ids, "category": "character"}).json()
    assert any(t["name"] == "cand_c_(tst)" for t in d["candidates"])
    assert "sugg_series_(tst)" in d["series"] and any(t["name"] == "mate_b_(tst)" for t in d["mates"])
    assert all(t["category"] == "character" for t in d["candidates"] + d["mates"] + d["pinned"])
    r = client.get("/api/tags", params={"q": "mate_", "category": "character"}).json()
    assert r and all(t["category"] == "character" for t in r) and "display" in r[0]


def test_unrecognized_piles(client):
    """未識別分堆：依描述標籤分類；整堆略過＋標梗圖、留下勾起來的、一次撤銷全部還原。"""
    from app import db, piles
    assert piles.classify("video", None, {}) == "video"
    assert piles.classify("image", None, {"chat_log": 0.9, "1girl": 0.9}) == "meme"
    assert piles.classify("image", None, {"english_text": 0.8}) == "meme"
    assert piles.classify("image", None, {"english_text": 0.8, "1girl": 0.9}) == "texty"
    assert piles.classify("image", None, {"realistic": 0.7, "1girl": 0.9}) == "photo"
    assert piles.classify("image", "photo", {}) == "photo"
    assert piles.classify("image", None, {"no_humans": 0.9}) == "nohuman"
    assert piles.classify("image", None, {"comic": 0.8, "english_text": 0.9}) == "comic"
    assert piles.classify("image", None, {"1girl": 0.9, "long_hair": 0.8}) == "illust"
    piles.refresh()
    c = db.conn()
    unrec = lambda **kw: client.get("/api/media", params={"view": "unrecognized", "nsfw": "show", **kw}).json()["total"]
    base = unrec()
    ids = [r[0] for r in c.execute("SELECT id FROM media WHERE status='done' ORDER BY id")]
    s = client.get("/api/unrec/summary", params={"nsfw": "show"}).json()
    assert sum(s[f"p_{k}"] for k, _ in piles.PILES) == s["all"] and len(s["piles"]) == len(piles.PILES)
    # 把 3 張未識別的圖放進「梗圖」堆
    un = [i["id"] for i in client.get("/api/media", params={"view": "unrecognized", "nsfw": "show", "limit": 50}).json()["items"]][:3]
    assert len(un) == 3
    prev_pile = {i: c.execute("SELECT pile FROM media WHERE id=?", (i,)).fetchone()[0] for i in un}
    with db.write_lock:
        c.executemany("UPDATE media SET pile='meme' WHERE id=?", [(i,) for i in un]); c.commit()
    n0 = unrec(bucket="p_meme")
    assert n0 >= 3
    assert client.post("/api/unrec/pile-action?dry_run=true", json={"bucket": "p_meme", "action": "skip_tag", "nsfw": "show", "except_ids": [un[0]]}).json()["n"] == n0 - 1
    assert client.post("/api/unrec/pile-action", json={"bucket": "cand", "action": "skip"}).status_code == 400
    r = client.post("/api/unrec/pile-action", json={"bucket": "p_meme", "action": "skip_tag", "nsfw": "show", "except_ids": [un[0]]}).json()
    assert r["n"] == n0 - 1
    assert unrec(bucket="p_meme") == 1 and unrec() == base - (n0 - 1)
    tid = db.tag_id("梗圖", create=False)
    assert tid and c.execute("SELECT category FROM tags WHERE id=?", (tid,)).fetchone()[0] == "custom"
    got = {r[0] for r in c.execute("SELECT media_id FROM media_tags WHERE tag_id=? AND state=1", (tid,))}
    assert set(un[1:]) <= got and un[0] not in got
    client.post("/api/undo", json={})
    assert unrec() == base and unrec(bucket="p_meme") == n0
    assert not c.execute("SELECT COUNT(*) FROM media_tags WHERE tag_id=? AND state=1", (tid,)).fetchone()[0]
    # 排除也能撤銷
    client.post("/api/unrec/pile-action", json={"bucket": "p_meme", "action": "exclude", "nsfw": "show"})
    assert unrec(bucket="p_meme") == 0 and all(c.execute("SELECT excluded FROM media WHERE id=?", (i,)).fetchone()[0] for i in un)
    client.post("/api/undo", json={})
    assert unrec(bucket="p_meme") == n0
    with db.write_lock:
        c.executemany("UPDATE media SET pile=? WHERE id=?", [(p, i) for i, p in prev_pile.items()]); c.commit()


def test_search_like_a_picky_user(client):
    """刁鑽用戶的搜尋：畫面上的中文名（繁體）、有空白的英文名、% 和 _、作品搜尋跟作品頁一致、chars: 很快。"""
    import json as _j
    import time as _t
    from app import db, nameindex
    ids = sorted(ids_by_name(client).values())
    c = db.conn()
    with db.write_lock:
        fr = db.tag_id("frieren_test", "character")
        cp = db.tag_id("sousou_no_frieren_test", "copyright")
        ba = db.tag_id("blue_archive_test", "copyright")
        c.execute("INSERT OR REPLACE INTO tag_info(tag_id, status, other_names, copyright) VALUES(?, 'ok', ?, 'sousou_no_frieren_test')",
                  (fr, _j.dumps(["フリーレン", "芙莉莲"], ensure_ascii=False)))
        c.execute("INSERT OR REPLACE INTO media_tags(media_id, tag_id, score, source, state) VALUES(?,?,1,'manual',1)", (ids[0], fr))
        c.execute("INSERT OR REPLACE INTO media_tags(media_id, tag_id, score, source, state) VALUES(?,?,1,'manual',1)", (ids[1], cp))
        c.execute("INSERT OR REPLACE INTO media_tags(media_id, tag_id, score, source, state) VALUES(?,?,1,'manual',1)", (ids[2], ba))
        c.commit()
    nameindex.invalidate()
    tot = lambda q: client.get("/api/media", params={"view": "all", "nsfw": "show", "q": q}).json()["total"]
    assert tot("芙莉蓮") == 1 and tot("芙莉莲") == 1 and tot("Frieren Test") == 1
    assert tot("blue archive test") == 1 and tot("Blue_Archive_Test") == 1
    # 作品：有作品標籤的圖＋角色屬於這部作品的圖（跟作品頁一樣）
    assert tot("sousou no frieren test") == 2 and tot("series:sousou_no_frieren_test") == 2
    assert tot("%") == 0 and tot("_") == 0 and tot("path:%") == 0
    # 加標籤／搜尋選單也找得到繁體名
    assert any(t["name"] == "frieren_test" for t in client.get("/api/tags", params={"q": "芙莉蓮"}).json())
    # 多人：以前每張各數一次，現在一次算完；結果跟逐張數一樣
    t0 = _t.time()
    n1 = tot("chars:>=1")
    assert _t.time() - t0 < 2
    real = c.execute("""SELECT COUNT(DISTINCT m.id) FROM media m JOIN media_tags x ON x.media_id=m.id JOIN tags t ON t.id=x.tag_id
        WHERE x.state=1 AND t.category='character' AND m.status='done' AND COALESCE(m.excluded,0)=0""").fetchone()[0]
    assert n1 == real
    assert tot("chars:0") + n1 == tot("")
    assert tot("chars:<1") == tot("chars:0")
    # 找不到的字：告訴你哪個字、給建議
    ex = client.get("/api/query/explain", params={"q": "芙莉 nosuchthing_xyz"}).json()
    assert [e["found"] for e in ex] == [True, False]
    ex = client.get("/api/query/explain", params={"q": "frieren"}).json()
    assert any(s["name"] == "frieren_test" for s in ex[0]["suggest"])
    with db.write_lock:
        c.execute("DELETE FROM media_tags WHERE tag_id IN (?,?,?)", (fr, cp, ba))
        c.execute("DELETE FROM tag_info WHERE tag_id=?", (fr,))
        c.commit()


def test_server_errors_are_recorded(client):
    """出錯的請求：回看得懂的訊息、記在系統狀態和 data/errors.log。"""
    from app import db, errlog
    from app.server import app

    def boom():
        raise RuntimeError("故意的")
    app.add_api_route("/api/_boom_test", boom)
    from fastapi.testclient import TestClient
    with TestClient(app, raise_server_exceptions=False) as c2:
        r = c2.get("/api/_boom_test")
    assert r.status_code == 500 and "RuntimeError" in r.json()["detail"]
    assert any(e["path"] == "/api/_boom_test" for e in errlog.recent(50))   # 背景的警告也會記進來，不一定是第一筆
    assert (db.DATA_DIR / "errors.log").exists()
    assert any(e["path"] == "/api/_boom_test" for e in client.get("/api/diag").json()["server_errors"])


def test_shelf_series_counts_are_distinct(client):
    """作品卡片的張數＝不重複的圖（跟作品頁、搜尋一致），多人圖不重複算。"""
    import json as _j
    from app import db
    ids = sorted(ids_by_name(client).values())
    c = db.conn()
    with db.write_lock:
        a, b = db.tag_id("aa_(dup_series)", "character"), db.tag_id("bb_(dup_series)", "character")
        for t in (a, b):
            c.execute("INSERT OR REPLACE INTO tag_info(tag_id, status, other_names, copyright) VALUES(?, 'ok', '[]', 'dup_series')", (t,))
        for mid in ids[:3]:
            c.execute("INSERT OR REPLACE INTO media_tags(media_id, tag_id, score, source, state) VALUES(?,?,1,'manual',1)", (mid, a))
        for mid in ids[1:3]:
            c.execute("INSERT OR REPLACE INTO media_tags(media_id, tag_id, score, source, state) VALUES(?,?,1,'manual',1)", (mid, b))
        c.commit()
    g = next(x for x in client.get("/api/shelf", params={"nsfw": "show"}).json()["series"] if x["key"] == "dup_series")
    assert g["n"] == 3 and g["chars"] == 2
    assert client.get("/api/series/dup_series", params={"nsfw": "show"}).json()["n"] == 3
    assert client.get("/api/media", params={"view": "all", "nsfw": "show", "q": "series:dup_series"}).json()["total"] == 3
    # 作品卡片（作品與標籤頁）也一樣：作品標籤＋角色屬於這部作品的圖
    with db.write_lock:
        cp = db.tag_id("dup_series", "copyright")
        c.execute("INSERT OR REPLACE INTO media_tags(media_id, tag_id, score, source, state) VALUES(?,?,1,'manual',1)", (ids[5], cp))
        c.commit()
    card = next(x for x in client.get("/api/characters", params={"nsfw": "show", "category": "copyright"}).json() if x["name"] == "dup_series")
    assert card["n"] == 4
    assert client.get("/api/characters", params={"nsfw": "show", "q": "%"}).json() == []
    with db.write_lock:
        c.execute("DELETE FROM media_tags WHERE tag_id IN (?,?,?)", (a, b, cp)); c.commit()


def test_quick_tags(client):
    """快速標籤：一鍵加上（同時離開未識別）、再按一次拿掉（回到未識別）、撤銷、AI 提示、側欄。"""
    from app import db
    unrec = lambda: [i["id"] for i in client.get("/api/media", params={"view": "unrecognized", "nsfw": "show", "limit": 200}).json()["items"]]
    before = unrec()
    ids = before[:2]
    r = client.post("/api/quicktag", json={"ids": ids, "name": "梗圖"}).json()
    assert r["on"] and r["n"] == 2
    tid = db.tag_id("梗圖", create=False)
    assert db.conn().execute("SELECT category FROM tags WHERE id=?", (tid,)).fetchone()[0] == "custom"
    assert not set(ids) & set(unrec())
    assert client.get("/api/media", params={"view": "all", "nsfw": "show", "q": "tag:梗圖"}).json()["total"] == 2
    side = client.get("/api/sidebar", params={"nsfw": "show"}).json()["quick"]
    assert [q["name"] for q in side] == ["梗圖", "真人", "截圖"] and side[0]["n"] == 2 and side[0]["key"] == 1
    # 其中一張再按一次 → 拿掉、回到未識別
    r = client.post("/api/quicktag", json={"ids": ids[:1], "name": "梗圖"}).json()
    assert r["on"] is False and ids[0] in unrec() and ids[1] not in unrec()
    client.post("/api/undo", json={})
    assert ids[0] not in unrec()
    client.post("/api/undo", json={})
    assert set(ids) <= set(unrec())
    assert not db.conn().execute("SELECT COUNT(*) FROM media_tags WHERE tag_id=? AND state=1", (tid,)).fetchone()[0]
    # 不在「離開未識別」清單的快速標籤：只加標籤
    client.post("/api/settings", json={"quick_tags": ["梗圖", "真人", "截圖", "喜歡"]})
    client.post("/api/quicktag", json={"ids": ids[:1], "name": "喜歡", "on": True})
    assert ids[0] in unrec()
    q = client.get("/api/quicktags", params={"nsfw": "show"}).json()
    assert [x["name"] for x in q][-1] == "喜歡" and q[1]["hint"] == "is:real" and q[1]["hint_n"] is not None
    client.post("/api/undo", json={})
    client.post("/api/settings", json={"quick_tags": ["梗圖", "真人", "截圖"]})
    assert client.post("/api/quicktag", json={"ids": ids, "name": "  "}).status_code == 400


def test_original_without_artist(client, tmp_path):
    """原創但沒有作者：缺創作者裡有「原創作品」「AI 生成」分流；可以標「不用找作者」（單張、整批、撤銷）；認得 ComfyUI 的圖。"""
    from PIL import Image, PngImagePlugin
    from app import aigen, db
    # ComfyUI／WebUI 的 PNG：檔案裡的生成資料
    p1 = tmp_path / "x.png"
    meta = PngImagePlugin.PngInfo(); meta.add_text("prompt", '{"3": {"class_type": "KSampler"}}'); meta.add_text("workflow", "{}")
    Image.new("RGB", (8, 8)).save(p1, pnginfo=meta)
    p2 = tmp_path / "y.png"
    meta2 = PngImagePlugin.PngInfo(); meta2.add_text("parameters", "1girl\nSteps: 20, Sampler: Euler a, CFG scale: 7")
    Image.new("RGB", (8, 8)).save(p2, pnginfo=meta2)
    p3 = tmp_path / "plain.png"
    Image.new("RGB", (8, 8)).save(p3)
    assert aigen.detect(str(p1)) == "ComfyUI" and aigen.detect(str(p2)) == "Stable Diffusion" and aigen.detect(str(p3)) == ""
    ids = sorted(ids_by_name(client).values())
    c = db.conn()
    a, b = ids[8], ids[9]
    with db.write_lock:
        c.execute("DELETE FROM media_tags WHERE media_id IN (?,?) AND tag_id IN (SELECT id FROM tags WHERE category='artist')", (a, b))
        c.execute("UPDATE media SET artist_done=0, ai_gen=NULL WHERE id IN (?,?)", (a, b))
        c.execute("UPDATE media SET ai_gen='ComfyUI' WHERE id=?", (b,))
        c.execute("INSERT OR REPLACE INTO media_tags(media_id, tag_id, score, source, state) VALUES(?,?,NULL,'manual',1)", (a, db.tag_id("original", "copyright")))
        c.commit()
    art = lambda **kw: {i["id"] for i in client.get("/api/media", params={"view": "unrecognized", "miss": "artist", "nsfw": "show", "limit": 1000, **kw}).json()["items"]}
    assert a in art(bucket="orig") and b not in art(bucket="orig") and b in art(bucket="aigen")
    s = client.get("/api/unrec/summary", params={"miss": "artist", "nsfw": "show"}).json()
    assert s["orig"] >= 1 and s["aigen"] >= 1
    assert client.get("/api/media", params={"view": "all", "nsfw": "show", "q": "is:aigen"}).json()["total"] >= 1
    # 單張：不用找作者 → 離開缺創作者；撤銷回來
    client.post("/api/media/artist-done", json={"ids": [a]})
    assert a not in art()
    assert client.get(f"/api/media/{a}").json()["artist_done"] == 1
    client.post("/api/undo", json={})
    assert a in art()
    # 整批（AI 生成那一堆）
    r = client.post("/api/unrec/pile-action", json={"bucket": "aigen", "action": "artist_done", "miss": "artist", "nsfw": "show"}).json()
    assert r["n"] >= 1 and b not in art()
    client.post("/api/undo", json={})
    assert b in art()
    assert client.post("/api/unrec/pile-action", json={"bucket": "orig", "action": "skip", "miss": "artist"}).status_code == 400
    with db.write_lock:
        c.execute("UPDATE media SET ai_gen=NULL WHERE id=?", (b,))
        c.execute("DELETE FROM media_tags WHERE media_id=? AND tag_id=?", (a, db.tag_id("original", create=False)))
        c.commit()


def test_series_missing_char_or_artist(client):
    """標了作品、卻沒有角色／作者的圖：作品頁有「沒標角色／沒標作者」，未識別有「依作品」。"""
    from app import db
    ids = sorted(ids_by_name(client).values())
    c = db.conn()
    x, y = ids[10], ids[11]
    with db.write_lock:
        cp = db.tag_id("lonely_series", "copyright")
        ch = db.tag_id("lonely_char_(lonely_series)", "character")
        c.execute("INSERT OR REPLACE INTO tag_info(tag_id, status, other_names, copyright) VALUES(?, 'ok', '[]', 'lonely_series')", (ch,))
        for mid in (x, y):
            c.execute("DELETE FROM media_tags WHERE media_id=? AND tag_id IN (SELECT id FROM tags WHERE category IN ('character','artist'))", (mid,))
            c.execute("UPDATE media SET artist_done=0, official=NULL, unrec_skip=0 WHERE id=?", (mid,))
        c.execute("INSERT OR REPLACE INTO media_tags(media_id, tag_id, score, source, state) VALUES(?,?,NULL,'manual',1)", (x, cp))
        c.execute("INSERT OR REPLACE INTO media_tags(media_id, tag_id, score, source, state) VALUES(?,?,NULL,'manual',1)", (y, ch))
        c.commit()
    d = client.get("/api/series/lonely_series", params={"nsfw": "show"}).json()
    assert d["miss"] == {"nochar": 1, "noartist": 2}
    tot = lambda q: {i["id"] for i in client.get("/api/media", params={"view": "all", "nsfw": "show", "q": q, "limit": 500}).json()["items"]}
    assert tot("series:lonely_series is:nochar") == {x} and tot("series:lonely_series is:missartist") == {x, y}
    bs = {r["key"]: r["n"] for r in client.get("/api/unrec/by-series", params={"miss": "character", "nsfw": "show"}).json()}
    assert bs.get("lonely_series") == 1
    bs = {r["key"]: r["n"] for r in client.get("/api/unrec/by-series", params={"miss": "artist", "nsfw": "show", "limit": 50}).json()}
    assert bs.get("lonely_series") == 2
    with db.write_lock:
        c.execute("DELETE FROM media_tags WHERE tag_id IN (?,?)", (cp, ch)); c.commit()


def test_albums(client):
    """作品集：建立（整本的標籤套到每一頁、不蓋掉否決）、加頁自動套、移出只拿掉作品集加的、拿掉整本標籤、解散、撤銷；資料夾名解析。"""
    from app import albums, db
    assert albums.parse_name("(C104) [Circle (Artist Name)] Some Title (Blue Archive) [中国翻訳]") == {
        "event": "C104", "circle": "Circle", "artist": "Artist Name", "title": "Some Title", "parody": "Blue Archive"}
    assert albums.parse_name("3967042-[Philia] 机械之爱 Mechanical Love 1-2 [松果个人汉化]")["circle"] == "Philia"
    ids = sorted(ids_by_name(client).values())
    pages = ids[:4]
    c = db.conn()
    with db.write_lock:
        art = db.tag_id("album_painter_(tst)", "artist")
        c.execute("DELETE FROM media_tags WHERE tag_id=?", (art,))
        # 第 2 頁你否決過這個作者：不能被整本的標籤蓋掉；第 3 頁你自己手動標了
        c.execute("INSERT OR REPLACE INTO media_tags(media_id, tag_id, score, source, state) VALUES(?,?,NULL,'manual',-1)", (pages[1], art))
        c.execute("INSERT OR REPLACE INTO media_tags(media_id, tag_id, score, source, state) VALUES(?,?,NULL,'manual',1)", (pages[2], art))
        c.commit()
    st = lambda mid: (c.execute("SELECT state, source FROM media_tags WHERE media_id=? AND tag_id=?", (mid, art)).fetchone() or [None, None])
    aid = client.post("/api/albums", json={"title": "測試本", "ids": pages[:3], "kind": "doujin",
                                           "tags": [{"name": "album_painter_(tst)", "category": "artist"}]}).json()["id"]
    assert tuple(st(pages[0])) == (1, "album") and st(pages[1])[0] == -1 and tuple(st(pages[2])) == (1, "manual")
    d = client.get(f"/api/albums/{aid}").json()
    assert d["n"] == 3 and d["kind"] == "doujin" and d["tags"][0]["name"] == "album_painter_(tst)"
    got = [i["id"] for i in client.get("/api/media", params={"view": "all", "nsfw": "show", "album": aid, "sort": "page"}).json()["items"]]
    assert sorted(got) == pages[:3] and got == albums.sorted_ids(c, aid, "smart")   # 建立時就智慧排序（b54），不是選的順序
    assert client.get("/api/media", params={"view": "all", "nsfw": "show", "q": f"album:{aid}"}).json()["total"] == 3
    # 加頁：自動套上整本的標籤；大圖看得到在哪本、第幾頁
    assert client.post(f"/api/albums/{aid}/add", json={"ids": [pages[3], pages[0]]}).json()["n"] == 1
    assert tuple(st(pages[3])) == (1, "album")
    # 原本是智慧排序 → 新頁放到該在的位置（b54），不一定是第 4 頁
    want = albums.sorted_ids(c, aid, "smart").index(pages[3]) + 1
    assert client.get(f"/api/media/{pages[3]}").json()["albums"] == [{"id": aid, "title": "測試本", "page": want}]
    # 移出：只拿掉作品集加的
    client.post(f"/api/albums/{aid}/remove", json={"ids": [pages[3], pages[2]]})
    assert st(pages[3])[0] is None and tuple(st(pages[2])) == (1, "manual")
    client.post("/api/undo", json={})
    assert tuple(st(pages[3])) == (1, "album")
    # 整本加角色、拿掉作者
    client.post(f"/api/albums/{aid}/tag", json={"name": "album_heroine_(tst)", "category": "character"})
    assert client.get("/api/media", params={"view": "all", "nsfw": "show", "q": "album_heroine_(tst)"}).json()["total"] == 4
    client.post(f"/api/albums/{aid}/tag", json={"name": "album_painter_(tst)", "on": False})
    assert st(pages[0])[0] is None and tuple(st(pages[2])) == (1, "manual") and st(pages[1])[0] == -1
    client.post("/api/undo", json={})
    assert tuple(st(pages[0])) == (1, "album")
    # 解散（一併拿掉）→ 撤銷回來
    client.post(f"/api/albums/{aid}/delete?untag=true", json={})
    assert client.get(f"/api/albums/{aid}").status_code == 404 and st(pages[0])[0] is None
    client.post("/api/undo", json={})
    assert client.get(f"/api/albums/{aid}").json()["n"] == 4 and tuple(st(pages[0])) == (1, "album")
    assert any(a["id"] == aid for a in client.get("/api/albums", params={"nsfw": "show"}).json())
    assert client.get("/api/sidebar", params={"nsfw": "show"}).json()["counts"]["albums"] >= 1
    # 建立本身也能撤銷
    client.post("/api/undo", json={}); client.post("/api/undo", json={}); client.post("/api/undo", json={})
    client.post("/api/undo", json={}); client.post("/api/undo", json={})
    assert client.get(f"/api/albums/{aid}").status_code == 404 and st(pages[0])[0] is None
    # 資料夾建議：測試圖庫的 set/ 資料夾只有 3 張，不夠 → 沒有；min_n=3 就有
    assert any(f["name"] == "set" for f in albums.folder_suggestions(min_n=3))
    assert client.post("/api/albums", json={"title": "x", "ids": []}).status_code == 400
    assert client.post("/api/albums/99999/add", json={"ids": [1]}).status_code == 404


def test_no_artist_tags(client):
    """表情包、梗圖、截圖：作者不可考，有這些標籤的圖不算缺創作者；設定可改；標籤拿掉就回來。"""
    from app import db, quicktags
    quicktags.sync_no_artist()
    c = db.conn()
    art = lambda: {i["id"] for i in client.get("/api/media", params={"view": "unrecognized", "miss": "artist", "nsfw": "show", "limit": 1000}).json()["items"]}
    # 挑一張普通的圖（不靠「第幾張」：別的測試留下的狀態、掃描順序都會變）
    for mid in [i for n, i in sorted(ids_by_name(client).items()) if n.startswith("img_")]:
        with db.write_lock:
            c.execute("DELETE FROM media_tags WHERE media_id=? AND tag_id IN (SELECT id FROM tags WHERE category='artist')", (mid,))
            c.execute("UPDATE media SET artist_done=0, official=NULL, unrec_skip=0, excluded=0 WHERE id=?", (mid,))
            c.execute("DELETE FROM media_tags WHERE media_id=? AND tag_id IN (SELECT id FROM tags WHERE no_artist=1)", (mid,))
            c.commit()
        if mid in art():
            break
    assert mid in art()
    client.post("/api/quicktag", json={"ids": [mid], "name": "梗圖", "on": True})
    assert mid not in art()
    assert mid in {i["id"] for i in client.get("/api/media", params={"view": "all", "nsfw": "show", "q": "is:artistdone", "limit": 1000}).json()["items"]}
    # 設定拿掉「梗圖」→ 又算缺創作者（梗圖同時「略過」了未識別，所以用 is:missartist 看）
    miss = lambda: mid in {i["id"] for i in client.get("/api/media", params={"view": "all", "nsfw": "show", "q": "is:missartist", "limit": 1000}).json()["items"]}
    assert not miss()
    client.post("/api/settings", json={"no_artist_tags": ["表情包"]})
    assert miss()
    client.post("/api/settings", json={"no_artist_tags": ["梗圖", "表情包", "截圖"]})
    assert not miss()
    client.post("/api/quicktag", json={"ids": [mid], "name": "梗圖", "on": False})
    assert mid in art()


def test_ai_generation_metadata(client):
    """AI 生成的圖：讀出提示詞、模型、LoRA、種子；提示詞裡有圖庫認得的角色 → 待確認（來源「生成提示詞」，不自動確認）；可以搜尋。"""
    from PIL import Image, PngImagePlugin
    from app import aigen, db
    from tests.conftest import LIB, wait_idle
    db.tag_id("prompt_girl_(tst)", "character")
    meta = PngImagePlugin.PngInfo()
    meta.add_text("parameters", "masterpiece, (prompt girl \\(tst\\):1.2), <lora:shiny_style:0.8>, 1girl\n"
                                "Negative prompt: lowres, bad hands\nSteps: 28, Sampler: Euler a, CFG scale: 6, Seed: 42, Size: 64x64, Model: animagine_xl")
    img = Image.new("RGB", (64, 64), (200, 40, 90))
    p = LIB / "ai" / "00012-42.png"
    p.parent.mkdir(parents=True, exist_ok=True)
    img.save(p, pnginfo=meta)
    # ComfyUI 節點圖
    g = {"3": {"class_type": "KSampler", "inputs": {"seed": 7, "steps": 20, "cfg": 5, "sampler_name": "euler", "positive": ["6", 0], "negative": ["7", 0], "model": ["4", 0]}},
         "4": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "noob.safetensors"}},
         "6": {"class_type": "CLIPTextEncode", "inputs": {"text": "1girl, prompt_girl_(tst)", "clip": ["4", 1]}},
         "7": {"class_type": "CLIPTextEncode", "inputs": {"text": "worst quality", "clip": ["4", 1]}},
         "9": {"class_type": "LoraLoader", "inputs": {"lora_name": "detail.safetensors"}}}
    import json as _j
    m2 = PngImagePlugin.PngInfo(); m2.add_text("prompt", _j.dumps(g)); m2.add_text("workflow", "{}")
    Image.new("RGB", (64, 64), (20, 140, 90)).save(LIB / "ai" / "ComfyUI_00001_.png", pnginfo=m2)
    t, d = aigen.read_meta(str(LIB / "ai" / "ComfyUI_00001_.png"))
    assert t == "ComfyUI" and d["prompt"] == "1girl, prompt_girl_(tst)" and d["negative"] == "worst quality"
    assert d["model"] == "noob.safetensors" and d["loras"] == ["detail.safetensors"] and d["seed"] == "7"
    client.post("/api/jobs", json={"kind": "scan"})
    wait_idle(client)
    from tests.conftest import ids_by_name
    mid = ids_by_name(client)["00012-42.png"]
    aigen.refresh([mid])
    det = client.get(f"/api/media/{mid}").json()
    assert det["ai_gen"] == "Stable Diffusion"
    a = det["ai_meta"]
    assert a["model"] == "animagine_xl" and a["loras"] == ["shiny_style"] and a["seed"] == "42" and "lowres" in a["negative"]
    row = db.conn().execute("""SELECT x.state, x.source FROM media_tags x JOIN tags t ON t.id=x.tag_id
        WHERE x.media_id=? AND t.name='prompt_girl_(tst)'""", (mid,)).fetchone()
    assert tuple(row) == (1, "aimeta")      # b97.1：使用者要「抓得到提示詞就直接確認」（過去 53 筆全部按是）
    # 自己加的作品括號、中文逗號：「Prompt Girl(Some Series)，blush」也對得到 prompt_girl
    db.tag_id("paren_girl", "character")
    with db.write_lock:
        db.conn().execute("INSERT OR REPLACE INTO ai_meta(media_id, tool, prompt) VALUES(?, 'NovelAI', ?)", (mid, "Paren Girl(Some Series)，blush"))
        db.conn().commit()
    aigen.resuggest_all()
    assert db.conn().execute("""SELECT x.source FROM media_tags x JOIN tags t ON t.id=x.tag_id WHERE x.media_id=? AND t.name='paren_girl'""", (mid,)).fetchone()[0] == "aimeta"
    # 設定改回「放待確認」：進待確認的「生成提示詞」分頁
    client.post("/api/settings", json={"act_aimeta": "candidate"})
    with db.write_lock:
        db.conn().execute("DELETE FROM media_tags WHERE media_id=? AND source='aimeta'", (mid,))
        db.conn().commit()
    aigen.refresh([mid])      # 讀回檔案裡原本的資料
    rv = client.get("/api/review", params={"tier": "prompt", "nsfw": "show"}).json()
    assert any(it["id"] == mid for it in rv["items"])
    client.post("/api/settings", json={"act_aimeta": "confirm"})
    for q in ("prompt:prompt_girl", "model:animagine", "lora:shiny"):
        assert mid in {i["id"] for i in client.get("/api/media", params={"view": "all", "nsfw": "show", "q": q}).json()["items"]}, q
