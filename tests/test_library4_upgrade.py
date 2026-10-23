"""換成高清版：找到比較大的同一張 → 換掉檔案、紀錄（標記、確認）全部留著；差分不換；你說不用就不再提醒。"""
import json
import os
import time

from PIL import Image, ImageDraw

from tests.conftest import LIB, TMP, ids_by_name, make_image, wait_idle


def _source(mid, post_id, size, method="iqdb"):
    from app import db
    with db.write_lock:
        db.conn().execute("""INSERT OR REPLACE INTO media_source(media_id, status, method, similarity, post_url, data, fetched_at)
                             VALUES(?, 'found', ?, 95, ?, ?, ?)""",
                          (mid, method, f"http://x/posts/{post_id}",
                           json.dumps({"danbooru": {"post_id": post_id, "size": list(size)}}), time.time()))


def test_upgrade_replace_keeps_record(client):
    from app import db, upgrade
    from tests import mock_online
    hi = TMP / "remote"
    hi.mkdir(exist_ok=True)
    # a：網站上是同一張的 2 倍大 PNG
    make_image(hi / "a.png", 7001, size=(800, 1120))
    (LIB / "up").mkdir(exist_ok=True)
    Image.open(hi / "a.png").resize((400, 560), Image.LANCZOS).save(LIB / "up" / "IMG_7001.jpg", quality=88)
    # b：網站上的是差分（多了一塊）→ 不能換
    make_image(hi / "b_src.png", 7002, size=(800, 1120))
    Image.open(hi / "b_src.png").resize((400, 560), Image.LANCZOS).save(LIB / "up" / "IMG_7002.jpg", quality=88)
    im = Image.open(hi / "b_src.png").convert("RGB")
    ImageDraw.Draw(im).rectangle((300, 300, 520, 420), fill=(250, 250, 250))
    im.save(hi / "b.png")
    # c：網站上的一樣大 → 不提醒；之後你貼另一篇（大的）網址
    make_image(LIB / "up" / "IMG_7003.jpg", 7003, size=(400, 560))
    Image.open(LIB / "up" / "IMG_7003.jpg").resize((1200, 1680), Image.LANCZOS).save(hi / "c.png")
    mock_online.UP.update({5151: {"file": str(hi / "a.png"), "w": 800, "h": 1120, "ext": "png"},
                           5152: {"file": str(hi / "b.png"), "w": 800, "h": 1120, "ext": "png"},
                           5154: {"file": str(hi / "c.png"), "w": 1200, "h": 1680, "ext": "png"}})
    old_mtime = os.path.getmtime(LIB / "up" / "IMG_7001.jpg") - 86400 * 30
    os.utime(LIB / "up" / "IMG_7001.jpg", (old_mtime, old_mtime))
    client.post("/api/jobs", json={"kind": "scan"})
    wait_idle(client)
    ids = ids_by_name(client)
    a, b, c_ = ids["IMG_7001.jpg"], ids["IMG_7002.jpg"], ids["IMG_7003.jpg"]
    _source(a, 5151, (800, 1120))
    _source(b, 5152, (800, 1120))
    _source(c_, 5153, (400, 560))
    assert client.post("/api/tags/apply", json={"ids": [a], "action": "add", "tag": "hatsune_miku", "category": "character"}).status_code == 200
    con = db.conn()
    old_fp = con.execute("SELECT fp FROM media WHERE id=?", (a,)).fetchone()[0]

    # 查：a、b 有比較大的；c 一樣大 → 沒有
    assert upgrade.check(a)["status"] == "offer"
    assert upgrade.check(b)["status"] == "offer"
    assert upgrade.check(c_)["status"] == "none"
    got = {i["id"] for i in client.get("/api/media", params={"q": "is:upgrade", "limit": 500}).json()["items"]}
    assert {a, b} <= got and c_ not in got
    one = client.get(f"/api/media/{a}").json()["upgrade"]
    assert one["status"] == "offer" and (one["width"], one["height"]) == (800, 1120) and one["site_name"] == "Danbooru"
    assert upgrade.unchecked_ids() == [] or a not in upgrade.unchecked_ids()

    # 換 a：同一張 → 換掉檔案，紀錄（id、確認的角色）不變，日期沿用
    r = client.post(f"/api/upgrade/{a}/replace")
    assert r.status_code == 200, r.text
    assert r.json()["w"] == 800
    new = LIB / "up" / "IMG_7001.png"
    assert new.exists() and not (LIB / "up" / "IMG_7001.jpg").exists()
    assert Image.open(new).size == (800, 1120)
    assert abs(os.path.getmtime(new) - old_mtime) < 2
    wait_idle(client)
    m = con.execute("SELECT path, width, height, fp, status FROM media WHERE id=?", (a,)).fetchone()
    assert os.path.normpath(m["path"]) == os.path.normpath(str(new)) and (m["width"], m["height"]) == (800, 1120)
    assert m["fp"] != old_fp and m["status"] == "done"
    assert con.execute("""SELECT mt.state FROM media_tags mt JOIN tags t ON t.id=mt.tag_id
                          WHERE mt.media_id=? AND t.name='hatsune_miku'""", (a,)).fetchone()[0] == 1
    assert not con.execute("SELECT 1 FROM media WHERE path LIKE '%IMG_7001%' AND id<>?", (a,)).fetchone(), "不能多出一筆"
    u = client.get(f"/api/upgrade/{a}").json()
    assert u["status"] == "done" and (u["old_w"], u["old_h"]) == (400, 560)
    trace = client.get(f"/api/media/{a}/trace").json()
    assert any(s["step"] == "找更高清的版本" and "已換成" in s["result"] for s in trace["steps"]), trace
    # 換過了再查：沒有更大的 → 還是「換過了」
    assert upgrade.check(a)["status"] == "done"

    # b：差分 → 不換，原檔不動
    r = client.post(f"/api/upgrade/{b}/replace")
    assert r.status_code == 400 and "不是同一張" in r.json()["detail"], r.text
    assert (LIB / "up" / "IMG_7002.jpg").exists() and not (LIB / "up" / "IMG_7002.png").exists()
    assert client.get(f"/api/upgrade/{b}").json()["status"] == "failed"
    # 你說不用 → 再查也不提醒
    client.post("/api/upgrade/dismiss", json={"ids": [b]})
    assert upgrade.check(b)["status"] == "dismissed"

    # c：貼網址（別的網站不收；Danbooru 那篇比較大）→ 可以換 → 整批換（背景工作）
    assert client.post(f"/api/upgrade/{c_}/url", json={"url": "https://example.com/a.png"}).status_code == 400
    r = client.post(f"/api/upgrade/{c_}/url", json={"url": "https://danbooru.donmai.us/posts/5154"})
    assert r.status_code == 200 and r.json()["status"] == "offer", r.text
    assert client.post("/api/upgrade/replace", json={"ids": [c_]}).json()["queued"] == 1
    wait_idle(client)
    assert client.get(f"/api/upgrade/{c_}").json()["status"] == "done"
    assert con.execute("SELECT width FROM media WHERE id=?", (c_,)).fetchone()[0] == 1200
    assert client.get("/api/upgrade/summary").json()["done"] >= 2
    assert not list((TMP / "data" / "upgrade_tmp").glob("*")), "暫存檔要清掉"


def test_upgrade_better_rule():
    from app.upgrade import better
    assert better({"w": 880, "h": 1232}, 800, 1120)            # 長邊多 10%
    assert not better({"w": 860, "h": 1204}, 800, 1120)        # 不到 10%
    assert not better({"w": 1600, "h": 1600}, 800, 1120)       # 比例不同（裁切過／別張）
    assert not better({"w": None, "h": 100}, 800, 1120)
