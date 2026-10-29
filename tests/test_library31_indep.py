"""b110 子作品獨立顯示：設定成獨立的子作品（預設終末地）不併入母系列；作品頁可以切換；相關作品互相連。"""
from app import db, names


def test_independent_default_and_toggle(client):
    from app import seriesmap
    with db.write_lock:      # Danbooru 的 tag implication：終末地 ⇒ 明日方舟
        db.conn().execute(seriesmap.SCHEMA)
        db.conn().execute("INSERT OR REPLACE INTO series_parent(child, parent, checked_at) VALUES('arknights:_endfield', 'arknights', 0)")
    seriesmap._cache["at"] = 0
    keys = {"arknights", "arknights:_endfield", "testfam_(series)", "testfam_spinoff"}
    assert "arknights:_endfield" in db.get_settings()["series_independent"]
    assert names.series_parent("arknights:_endfield", keys) is None                         # 預設獨立
    assert names.series_parent("arknights:_endfield", keys, ignore_independent=True) == "arknights"
    assert names.series_parent("testfam_spinoff", keys) == "testfam_(series)"
    assert client.post("/api/series/testfam_spinoff/independent", json={"on": True}).json()["independent"] is True
    assert names.series_parent("testfam_spinoff", keys) is None
    assert client.post("/api/series/testfam_spinoff/independent", json={"on": False}).json()["independent"] is False
    assert names.series_parent("testfam_spinoff", keys) == "testfam_(series)"
    assert "testfam_spinoff" not in db.get_settings()["series_independent"]


def test_independent_child_images_and_chars(client):
    """終末地的圖因為蘊含也有「明日方舟」：方舟的作品頁、series: 只放有方舟角色的圖；終末地的角色（資料卡主要作品記成方舟）歸終末地。
    同時有兩邊角色的圖兩邊都有。"""
    import json
    from app import seriesmap
    from tests.conftest import ids_by_name
    c = db.conn()
    ids = sorted(ids_by_name(client).values())[-3:]
    m1, m2, m3 = ids
    P, C = "cfam_parent", "cfam_child"
    tp, tc = db.tag_id(P, "copyright"), db.tag_id(C, "copyright")
    a, b = db.tag_id("cfam_op_(tst)", "character"), db.tag_id("cfam_new_(tst)", "character")
    with db.write_lock:
        c.execute(seriesmap.SCHEMA)
        c.execute("INSERT OR REPLACE INTO series_parent(child, parent, checked_at) VALUES(?,?,0)", (C, P))
        for t, cp, cps in ((a, P, [P]), (b, P, [P, C])):      # 終末地角色：主要作品記成母系列
            c.execute("INSERT OR REPLACE INTO tag_info(tag_id, status, copyright, copyrights, other_names) VALUES(?, 'ok', ?, ?, '[]')",
                      (t, cp, json.dumps(cps)))
        rows = [(m1, a), (m1, tp), (m2, b), (m2, tp), (m2, tc), (m3, a), (m3, b), (m3, tp), (m3, tc)]
        c.executemany("INSERT OR REPLACE INTO media_tags(media_id, tag_id, score, source, state) VALUES(?,?,NULL,'manual',1)", rows)
    seriesmap._cache["at"] = 0           # 母系列表 30 秒快取
    old = db.get_settings()["series_independent"]
    try:
        client.post(f"/api/series/{C}/independent", json={"on": True})
        got = lambda k: {it["id"] for it in client.get("/api/media", params={"q": f"series:{k}", "nsfw": "show", "limit": 500}).json()["items"]}
        assert got(P) == {m1, m3}, "方舟：只有方舟角色的圖（m2 的方舟標籤是蘊含帶上的）"
        assert got(C) == {m2, m3}
        sh = client.get("/api/shelf", params={"nsfw": "show"}).json()
        ser = {g["key"]: g for g in sh["series"]}
        chs = {ch["name"]: ch["series"] for ch in sh["chars"]}
        assert chs["cfam_new_(tst)"] == C and chs["cfam_op_(tst)"] == P
        assert ser[P]["n"] == 2 and ser[C]["n"] == 2
        d = client.get(f"/api/series/{P}", params={"nsfw": "show"}).json()
        assert [r["key"] for r in d["related"]] == [C]
        d = client.get(f"/api/series/{C}", params={"nsfw": "show"}).json()
        assert d["independent"] and d["family_parent"]["key"] == P
        # 併回：終末地的圖、角色都回到方舟
        client.post(f"/api/series/{C}/independent", json={"on": False})
        assert got(P) == {m1, m2, m3}
    finally:
        db.update_settings({"series_independent": old})
        with db.write_lock:
            c.execute("DELETE FROM media_tags WHERE tag_id IN (?,?,?,?)", (tp, tc, a, b))
            c.execute("DELETE FROM series_parent WHERE child=?", (C,))


def test_returning_character_split(client):
    """再旅角色（同一個角色標籤兩款遊戲都用，Danbooru 上只有一部分貼文標了子作品）：
    有子作品標籤（不是蘊含帶上的）的圖算子作品，其他算母系列；兩個作品頁都列這個角色、各算各的。"""
    import json
    from app import family, seriesmap
    from tests.conftest import ids_by_name
    c = db.conn()
    m4, m5, m6 = sorted(ids_by_name(client).values())[:3]
    P, C = "rfam_parent", "rfam_child"
    tp, tc = db.tag_id(P, "copyright"), db.tag_id(C, "copyright")
    d, nat = db.tag_id("rfam_returning_(tst)", "character"), db.tag_id("rfam_native_(tst)", "character")
    family._ensure()
    with db.write_lock:
        c.execute(seriesmap.SCHEMA)
        c.execute("INSERT OR REPLACE INTO series_parent(child, parent, checked_at) VALUES(?,?,0)", (C, P))
        c.execute("INSERT OR REPLACE INTO tag_info(tag_id, status, copyright, copyrights, other_names) VALUES(?, 'ok', ?, ?, '[]')",
                  (d, P, json.dumps([P, C])))
        c.execute("INSERT OR REPLACE INTO tag_info(tag_id, status, copyright, copyrights, other_names) VALUES(?, 'ok', ?, ?, '[]')",
                  (nat, P, json.dumps([P, C])))
        c.execute("INSERT OR REPLACE INTO char_cp_share(name, shares, posts, checked_at) VALUES('rfam_returning_(tst)', ?, 100, ?)",
                  (json.dumps({P: 1.0, C: 0.3}), __import__("time").time()))
        c.execute("INSERT OR REPLACE INTO char_cp_share(name, shares, posts, checked_at) VALUES('rfam_native_(tst)', ?, 100, ?)",
                  (json.dumps({P: 1.0, C: 1.0}), __import__("time").time()))
        rows = [(m4, d, "manual"), (m4, tc, "online"), (m4, tp, "implied"),        # 再旅角色的子作品服裝圖
                (m5, d, "manual"), (m5, tp, "implied"), (m5, tc, "implied"),       # 子作品標籤是蘊含帶上的 → 母系列
                (m6, nat, "manual"), (m6, tp, "implied")]                           # 原生角色
        c.executemany("INSERT OR REPLACE INTO media_tags(media_id, tag_id, score, source, state) VALUES(?,?,NULL,?,1)", rows)
    seriesmap._cache["at"] = 0
    old = db.get_settings()["series_independent"]
    try:
        client.post(f"/api/series/{C}/independent", json={"on": True})
        got = lambda k: {it["id"] for it in client.get("/api/media", params={"q": f"series:{k}", "nsfw": "show", "limit": 500}).json()["items"]}
        assert got(C) == {m4, m6}
        assert got(P) == {m5}
        dp = client.get(f"/api/series/{P}", params={"nsfw": "show"}).json()
        dc = client.get(f"/api/series/{C}", params={"nsfw": "show"}).json()
        cp_ = {ch["name"]: ch["n"] for ch in dp["chars"]}
        cc_ = {ch["name"]: ch["n"] for ch in dc["chars"]}
        assert cp_.get("rfam_returning_(tst)") == 1 and cc_.get("rfam_returning_(tst)") == 1, (cp_, cc_)
        assert "rfam_native_(tst)" in cc_ and "rfam_native_(tst)" not in cp_
    finally:
        db.update_settings({"series_independent": old})
        with db.write_lock:
            c.execute("DELETE FROM media_tags WHERE tag_id IN (?,?,?,?)", (tp, tc, d, nat))
            c.execute("DELETE FROM series_parent WHERE child=?", (C,))
            c.execute("DELETE FROM char_cp_share WHERE name LIKE 'rfam%'")


def test_returning_as_variant_tag(client):
    """再旅像皮膚一樣另外一個標籤（xxx_(endfield)_(tst)，Danbooru 上的貼文都標了子作品）：
    不併進母系列的本體角色，自己在子作品；蘊含帶上的本體標籤不算同框。"""
    import json
    from app import family, seriesmap
    from tests.conftest import ids_by_name
    c = db.conn()
    m7, m8 = sorted(ids_by_name(client).values())[3:5]
    P, C = "vfam_parent", "vfam_child"
    tp, tc = db.tag_id(P, "copyright"), db.tag_id(C, "copyright")
    base, var = db.tag_id("vop_(tst)", "character"), db.tag_id("vop_(endfield)_(tst)", "character")
    family._ensure()
    with db.write_lock:
        c.execute(seriesmap.SCHEMA)
        c.execute("INSERT OR REPLACE INTO series_parent(child, parent, checked_at) VALUES(?,?,0)", (C, P))
        c.execute("INSERT OR REPLACE INTO tag_info(tag_id, status, copyright, copyrights, other_names) VALUES(?, 'ok', ?, ?, '[]')", (base, P, json.dumps([P])))
        c.execute("INSERT OR REPLACE INTO tag_info(tag_id, status, copyright, copyrights, other_names) VALUES(?, 'ok', ?, ?, '[]')", (var, P, json.dumps([P, C])))
        rows = [(m7, base, "manual"), (m7, tp, "implied"),
                (m8, var, "manual"), (m8, base, "implied"), (m8, tp, "implied"), (m8, tc, "implied")]
        c.executemany("INSERT OR REPLACE INTO media_tags(media_id, tag_id, score, source, state) VALUES(?,?,NULL,?,1)", rows)
    seriesmap._cache["at"] = 0
    old = db.get_settings()["series_independent"]
    try:
        client.post(f"/api/series/{C}/independent", json={"on": True})
        got = lambda k: {it["id"] for it in client.get("/api/media", params={"q": f"series:{k}", "nsfw": "show", "limit": 500}).json()["items"]}
        assert got(P) == {m7} and got(C) == {m8}
        sh = client.get("/api/shelf", params={"nsfw": "show"}).json()
        chs = {ch["name"]: ch for ch in sh["chars"]}
        assert chs["vop_(endfield)_(tst)"]["series"] == C, "再旅版本自己一個角色、在子作品"
        assert chs["vop_(tst)"]["series"] == P and chs["vop_(tst)"]["n"] == 2 - 1 + 1     # 本體：m7、m8（蘊含帶上的）
    finally:
        db.update_settings({"series_independent": old})
        with db.write_lock:
            c.execute("DELETE FROM media_tags WHERE tag_id IN (?,?,?,?)", (tp, tc, base, var))
            c.execute("DELETE FROM series_parent WHERE child=?", (C,))


def test_implied_partner_not_crossover(client):
    """Danbooru：typhoeus ⇒ typhon（終末地的堤豐每篇都帶上方舟的提豐）。圖上的提豐就算不是蘊含帶上的（從 Danbooru 抓來的）、
    只要同一張圖有帶上它的角色，就不算方舟同框 → 只在終末地。"""
    import json, time
    from app import family, seriesmap
    from tests.conftest import ids_by_name
    c = db.conn()
    m9 = sorted(ids_by_name(client).values())[5]
    P, C = "tfam_parent", "tfam_child"
    tp, tc = db.tag_id(P, "copyright"), db.tag_id(C, "copyright")
    x, y = db.tag_id("tfam_typhon_(tst)", "character"), db.tag_id("tfam_typhoeus_(tst)", "character")
    family._ensure()
    with db.write_lock:
        c.execute(seriesmap.SCHEMA)
        c.execute("INSERT OR REPLACE INTO series_parent(child, parent, checked_at) VALUES(?,?,0)", (C, P))
        # 提豐：資料卡的主要作品記成子作品（最近的貼文大多是堤豐帶上的），但自己的貼文沒有子作品
        c.execute("INSERT OR REPLACE INTO tag_info(tag_id, status, copyright, copyrights, other_names) VALUES(?, 'ok', ?, ?, '[]')", (x, C, json.dumps([C, P])))
        c.execute("INSERT OR REPLACE INTO tag_info(tag_id, status, copyright, copyrights, other_names) VALUES(?, 'ok', ?, ?, '[]')", (y, P, json.dumps([P, C])))
        c.execute("INSERT OR REPLACE INTO char_cp_share(name, shares, posts, checked_at) VALUES('tfam_typhon_(tst)', ?, 30, ?)", (json.dumps({P: 1.0}), time.time()))
        c.execute("INSERT OR REPLACE INTO char_cp_share(name, shares, posts, checked_at) VALUES('tfam_typhoeus_(tst)', ?, 100, ?)", (json.dumps({P: 1.0, C: 0.97}), time.time()))
        c.execute("INSERT OR REPLACE INTO tag_implies(tag_id, implies_id, src, created_at) VALUES(?,?,'danbooru',0)", (y, x))
        c.executemany("INSERT OR REPLACE INTO media_tags(media_id, tag_id, score, source, state) VALUES(?,?,NULL,'online',1)",
                      [(m9, y), (m9, x), (m9, tp), (m9, tc)])
    seriesmap._cache["at"] = 0
    old = db.get_settings()["series_independent"]
    try:
        client.post(f"/api/series/{C}/independent", json={"on": True})
        got = lambda k: {it["id"] for it in client.get("/api/media", params={"q": f"series:{k}", "nsfw": "show", "limit": 500}).json()["items"]}
        assert m9 in got(C) and m9 not in got(P)
        chs = {ch["name"]: ch["series"] for ch in client.get("/api/shelf", params={"nsfw": "show"}).json()["chars"]}
        assert chs["tfam_typhon_(tst)"] == P and chs["tfam_typhoeus_(tst)"] == C
    finally:
        db.update_settings({"series_independent": old})
        with db.write_lock:
            c.execute("DELETE FROM media_tags WHERE tag_id IN (?,?,?,?)", (tp, tc, x, y))
            c.execute("DELETE FROM tag_implies WHERE tag_id=?", (y,))
            c.execute("DELETE FROM series_parent WHERE child=?", (C,))
            c.execute("DELETE FROM char_cp_share WHERE name LIKE 'tfam%'")
