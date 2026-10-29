"""Danbooru 角色表（b109）：下載角色（照張數門檻）、維基別名、所屬作品（括號、作品別名、熱門角色抽樣）、
照作品熱度查中文名；加標籤選單搜得到圖庫裡還沒有的角色。"""
import time

from app import charcat, db


def _wait(cond, timeout=60):
    t0 = time.time()
    while time.time() - t0 < timeout:
        if cond():
            return True
        time.sleep(0.3)
    return False


def test_download_and_search(client):
    was = db.get_settings()["online_enabled"]
    client.post("/api/settings", json={"online_enabled": True, "charcat_zh": True})
    try:
        assert client.post("/api/charcat/download", json={"min": 7}).status_code == 400      # 只能選 1／10／100
        assert client.post("/api/charcat/download", json={"min": 10}).status_code == 200
        assert _wait(lambda: client.get("/api/charcat").json()["count"] > 0)
        st = client.get("/api/charcat").json()
        assert st["count"] == 4 and st["min_posts"] == 10                                    # cat_rare（5 張）不收
        rows = {r[0]: (r[1], r[2]) for r in db.conn().execute("SELECT name, copyright, cp_src FROM char_catalog")}
        assert rows["cat_hero_(blue_archive)"] == ("blue_archive", "suffix")                 # 名字括號
        assert rows["cat_ship_(kancolle)"] == ("kantai_collection", "suffix")                # 括號是作品的別名
        assert rows["cat_obscure"] == (None, None)                                            # 不熱門：不抽樣，留白
        assert charcat.info("cat_hero_(blue_archive)")["other_names"] == ["聖園ミカ", "Mika"]
        # 熱門角色（≥300 張）沒帶作品：背景抽樣貼文
        assert _wait(lambda: charcat.info("cat_popular")["copyright"] == "vocaloid")
        # 照作品熱度查中文名（Bangumi）：熱門作品裡的角色
        assert _wait(lambda: db.conn().execute("SELECT zh FROM name_zh WHERE name='cat_hero_(blue_archive)'").fetchone() is not None)
        assert db.conn().execute("SELECT zh FROM name_zh WHERE name='cat_hero_(blue_archive)'").fetchone()[0] == "聖園未花"
        from app import zhname
        zhname.reload()
        # 加標籤選單：圖庫裡還沒有的角色接在後面（中文譯名、別名、簡繁都搜得到）
        for q in ("未花", "圣园", "人氣", "人气", "cat hero", "Mika"):
            res = client.get("/api/tags", params={"q": q, "category": "character"}).json()
            cat = [t for t in res if t.get("catalog")]
            assert cat, q
        hero = [t for t in client.get("/api/tags", params={"q": "未花", "category": "character"}).json() if t.get("catalog")][0]
        assert hero["name"] == "cat_hero_(blue_archive)" and hero["display"] == "聖園未花" and hero["posts"] == 20000
        assert not [t for t in client.get("/api/tags", params={"q": "未花", "category": "artist"}).json() if t.get("catalog")]
        # 同作品的角色
        assert [m["name"] for m in charcat.mates({"blue_archive"}, set())] == ["cat_hero_(blue_archive)"]
        # 挑了以後：變成圖庫的標籤，選單不再當成「角色表」列
        mid = db.conn().execute("SELECT id FROM media WHERE status='done' ORDER BY id LIMIT 1").fetchone()[0]
        assert client.post("/api/tags/apply", json={"action": "add", "tag": "cat_hero_(blue_archive)", "ids": [mid],
                                                    "category": "character"}).status_code == 200
        res = client.get("/api/tags", params={"q": "未花", "category": "character"}).json()
        assert any(t["name"] == "cat_hero_(blue_archive)" and not t.get("catalog") for t in res)
        client.post("/api/tags/apply", json={"action": "remove", "tag": "cat_hero_(blue_archive)", "ids": [mid], "category": "character"})
        # 刪除
        assert client.post("/api/charcat/delete", json={}).status_code == 200
        assert client.get("/api/charcat").json()["count"] == 0
        assert not [t for t in client.get("/api/tags", params={"q": "人氣", "category": "character"}).json() if t.get("catalog")]
    finally:
        from tests.conftest import wait_idle
        client.post("/api/settings", json={"online_enabled": was})
        wait_idle(client)


def test_suffix_rules():
    cps = {"blue_archive", "kantai_collection", "fate_(series)", "pokemon"}
    al = {"kancolle": "kantai_collection"}
    assert charcat.suffix_copyright("mika_(swimsuit)_(blue_archive)", cps, al) == "blue_archive"
    assert charcat.suffix_copyright("fubuki_(kancolle)", cps, al) == "kantai_collection"
    assert charcat.suffix_copyright("saber_(fate)", cps, al) == "fate_(series)"
    assert charcat.suffix_copyright("hatsune_miku", cps, al) is None
    assert charcat.suffix_copyright("someone_(cosplay)", cps, al) is None


def test_jp_kanji_and_catalog_variants(client):
    """b109.5：沒有中文別名、只有日文漢字名的角色：換成中文字形顯示（暁 → 曉）；角色表裡的造型套本體的中文名。"""
    from app import names, zhname
    assert names.pick(["暁", "暁(艦隊これくしょん)"], "zh") == "曉"
    assert names.pick(["桜井桃華"], "zh") == "櫻井桃華"
    assert names.pick(["アキ", "晓"], "zh") == "曉"            # 有中文別名：照舊用中文的
    assert names.pick(["イカガール"], "zh") is None
    charcat._ensure()
    with db.write_lock:
        c = db.conn()
        c.executemany("INSERT OR REPLACE INTO char_catalog(name, posts, other_names, copyright, cp_src, keys) VALUES(?,?,?,?,?,?)",
                      [("cv_base_(tst)", 500, '["暁"]', None, None, "cv base"),
                       ("cv_base_(swimsuit)_(tst)", 50, '[]', None, None, "cv base swimsuit")])
    try:
        zhname._CV["at"] = 0
        zhname.refresh_variants()
        zhname.reload()
        assert names.display("cv_base_(swimsuit)_(tst)", [], None, "zh", cn=False) == "曉（泳裝）"
        assert names.display("cv_base_(swimsuit)_(tst)", [], None, "zh", cn=True) == "晓（泳装）"
    finally:
        with db.write_lock:
            db.conn().execute("DELETE FROM char_catalog WHERE name LIKE 'cv_base%'")
            db.conn().execute("DELETE FROM name_zh WHERE name LIKE 'cv_base%'")
        zhname.reload()


def test_franchise_official_names():
    """b109.6：作品官方中文名（FGO 台服／國服、寶可夢人物）：繁體用台服、簡體用國服。"""
    from app import names
    assert names.display("van_gogh_(fate)", [], None, "zh", cn=False) == "梵谷"
    assert names.display("van_gogh_(fate)", [], None, "zh", cn=True) == "梵高"
    assert names.display("diarmuid_ua_duibhne_(lancer)_(fate)", [], None, "zh", cn=True) == "迪尔姆德·奥迪那"
    assert names.display("rika_(pokemon)", ["チリ"], None, "zh", cn=False) == "辛俐"
    assert names.display("van_gogh_(fate)", [], "我取的", "zh", cn=False) == "我取的"      # 你取的名字永遠優先


def test_catalog_variant_overrides_base_name():
    """角色表的造型：Bangumi 用日文名查到本人（跟本體同名）→ 改成「本體（造型）」。"""
    from app import names, zhname
    charcat._ensure()
    with db.write_lock:
        c = db.conn()
        c.executemany("INSERT OR REPLACE INTO char_catalog(name, posts, other_names, copyright, cp_src, keys) VALUES(?,?,?,?,?,?)",
                      [("cvb_(tst)", 500, '["暁"]', None, None, "cvb"), ("cvb_(summer_2021)_(tst)", 50, '["暁"]', None, None, "cvb summer")])
        c.execute("INSERT OR REPLACE INTO name_zh(name, category, status, zh, raw, seen, checked_at) VALUES('cvb_(summer_2021)_(tst)','character','found','曉','晓',0,0)")
    try:
        zhname._CV["at"] = 0
        zhname.refresh_variants()
        zhname.reload()
        assert names.display("cvb_(summer_2021)_(tst)", [], None, "zh", cn=False) == "曉（Summer 2021）"
    finally:
        with db.write_lock:
            db.conn().execute("DELETE FROM char_catalog WHERE name LIKE 'cvb%'")
            db.conn().execute("DELETE FROM name_zh WHERE name LIKE 'cvb%'")
        zhname.reload()


def test_variant_survives_recheck():
    """造型改成「本體（造型）」之後，重新檢查 Bangumi 譯名（recheck_local）不能把它蓋回本體的名字。"""
    from app import names, zhname
    charcat._ensure()
    with db.write_lock:
        c = db.conn()
        c.executemany("INSERT OR REPLACE INTO char_catalog(name, posts, other_names, copyright, cp_src, keys) VALUES(?,?,?,?,?,?)",
                      [("cvr_(tst)", 500, '["暁"]', None, None, "cvr"), ("cvr_(champion)_(tst)", 50, '["暁"]', None, None, "cvr c")])
        c.execute("""INSERT OR REPLACE INTO name_zh(name, category, status, zh, raw, ref, seen, checked_at)
                     VALUES('cvr_(champion)_(tst)','character','found','曉','晓','bgm:character/9',0,0)""")
    try:
        zhname._CV["at"] = 0
        zhname.refresh_variants()
        zhname.recheck_local()
        zhname.reload()
        assert names.display("cvr_(champion)_(tst)", [], None, "zh", cn=False) == "曉（Champion）"
    finally:
        with db.write_lock:
            db.conn().execute("DELETE FROM char_catalog WHERE name LIKE 'cvr%'")
            db.conn().execute("DELETE FROM name_zh WHERE name LIKE 'cvr%'")
        zhname.reload()
