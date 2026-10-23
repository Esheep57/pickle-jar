"""b92：名字像造型、但 Danbooru 上沒有蘊含的（女體化等另一個角色）不算同一個人。"""
import time


def test_danbooru_no_implication_beats_name_guess(client, monkeypatch):
    from app import db, implies, people
    c = db.conn()
    with db.write_lock:
        for n in ("gsbase", "gsbase_(redtst)", "gsbase_(unknowntst)", "gsbase_(costumetst)"):
            db.tag_id(n, "character")
        c.commit()
    tid = lambda n: db.tag_id(n, create=False)
    base, fem, unk, cos = (tid(n) for n in ("gsbase", "gsbase_(redtst)", "gsbase_(unknowntst)", "gsbase_(costumetst)"))
    people.refresh_bases()
    cb = lambda: dict(c.execute("SELECT tag_id, base_id FROM char_base"))
    assert cb().get(fem) == base and cb().get(unk) == base, "還沒問過 Danbooru：照名字猜"
    with db.write_lock:
        # fem：Danbooru 有這個角色（有圖）；unk：Danbooru 上沒有（post_count 0）
        for t, pc in ((fem, 3704), (unk, 0), (cos, 50)):
            c.execute("INSERT OR REPLACE INTO tag_info(tag_id, status, post_count, fetched_at) VALUES(?, 'ok', ?, ?)", (t, pc, time.time()))
        c.commit()
    res = implies.sync_danbooru([fem, unk])
    assert res["asked"] == 2 and res["added"] == 0
    m = cb()
    assert fem not in m, "Danbooru 有這個角色卻沒有蘊含：不是同一個人"
    assert m.get(unk) == base, "Danbooru 沒這個角色：照舊用猜的"
    assert m.get(cos) == base, "還沒問過的照舊用猜的"
    # 你自己訂的規則還是算
    client.post("/api/tag/gsbase_(redtst)/implies", json={"implies": ["gsbase"]})
    assert cb().get(fem) == base
    client.post("/api/tag/gsbase_(redtst)/implies", json={"implies": []})
    assert fem not in cb()
    # 新的本體加進來：名字像它造型的也排進去重問
    asked = []
    monkeypatch.setattr(implies, "sync_danbooru", lambda tids=None, **k: asked.append(sorted(tids or [])))
    real = db.get_settings
    monkeypatch.setattr(db, "get_settings", lambda: {**real(), "online_enabled": True})
    implies.schedule([base], delay=0.01)
    for _ in range(100):
        if asked:
            break
        time.sleep(0.05)
    monkeypatch.undo()
    assert asked and {base, fem, unk, cos} <= set(asked[0])
    with db.write_lock:
        c.execute("DELETE FROM tag_implies WHERE tag_id IN (?,?,?)", (fem, unk, cos))
        c.execute("DELETE FROM implies_checked WHERE tag_id IN (?,?,?)", (fem, unk, cos))
        c.commit()
    implies.invalidate()
    people.refresh_bases()


def test_album_pages_skip_dups_and_upgrade(client):
    """本子裡的頁不出現在「重複」和「換高清」的清單（也不去查高清版）。"""
    import json
    from app import db, upgrade
    from tests.conftest import ids_by_name
    ids = ids_by_name(client)
    a, b = ids["img_00.jpg"], ids["copy_small.png"]
    dup = lambda: [{x["id"] for x in g} for g in client.get("/api/duplicates", params={"nsfw": "show", "dist": 3}).json()["groups"]]
    assert any({a, b} <= g for g in dup())
    c = db.conn()
    with db.write_lock:
        c.execute("INSERT OR REPLACE INTO upgrade(media_id, status, cands, checked_at) VALUES(?, 'offer', ?, ?)", (a, json.dumps([]), time.time()))
        c.commit()
    q = lambda: {x["id"] for x in client.get("/api/media", params={"q": "is:upgrade", "nsfw": "show"}).json()["items"]}
    assert a in q()
    off0 = upgrade.summary()["offer"]
    with db.write_lock:     # 直接放進本子（不經 albums.create，免得動到這張圖的候選、影響別的測試）
        aid = c.execute("INSERT INTO albums(title, kind, created_at) VALUES('b92 test', 'other', ?)", (time.time(),)).lastrowid
        c.execute("INSERT INTO album_items(album_id, media_id, pos) VALUES(?,?,0)", (aid, a))
        c.commit()
    try:
        assert not any(a in g for g in dup()), "收進本子：不算重複"
        assert a not in q(), "收進本子：不在可以換高清的清單"
        assert upgrade.summary()["offer"] == off0 - 1
        assert a not in upgrade.unchecked_ids()
        st = {}
        called = []
        orig = upgrade.check
        upgrade.check = lambda mid, only=None: called.append(mid) or {}
        try:
            upgrade.run_checks([a], st, lambda: False)
        finally:
            upgrade.check = orig
        assert called == [] and st["done"] == 1, "排進佇列之後才收進本子的也跳過"
    finally:
        with db.write_lock:
            c.execute("DELETE FROM album_items WHERE album_id=?", (aid,))
            c.execute("DELETE FROM albums WHERE id=?", (aid,))
            c.execute("DELETE FROM upgrade WHERE media_id=?", (a,))
            c.commit()
    assert any({a, b} <= g for g in dup()), "拿出本子：又算重複"
