def test_steps_api(client):
    d = client.get("/api/steps").json()
    keys = [s["key"] for s in d["steps"]]
    assert "wd" in keys and "iqdb" in keys and d["rerun"]
    wd = next(s for s in d["steps"] if s["key"] == "wd")
    assert wd["act"] == "candidate" and wd["thr"][0]["k"] == "character_threshold"
    r = client.post("/api/settings", json={"act_wd": "bogus"})
    assert r.status_code == 400
    r = client.post("/api/rerun", json={"steps": ["wd", "source", "r34"], "query": {"view": "all"}, "dry": True}).json()
    assert r["n"] > 0 and "wd" in r["per"], r
    r2 = client.post("/api/rerun", json={"steps": ["r34"], "ids": [1, 2], "dry": True}).json()
    assert r2["n"] == 2
    q = client.get("/api/media?view=all&ids_only=1&nsfw=show").json()
    assert q["total"] == len(q["ids"])
    # 只記下：WD 不加角色候選
    from app import candidates, db, quicktags
    mid = next(r[0] for r in db.conn().execute("""SELECT id FROM media WHERE status='done' AND COALESCE(unrec_skip,0)=0
        AND id NOT IN (SELECT media_id FROM album_items) AND id NOT IN (SELECT media_id FROM media_source WHERE status='found') ORDER BY id""") if not quicktags.has_no_char(db.conn(), r[0]))
    try:
        _policy(client, candidates, db, mid)
    finally:
        client.post("/api/steps/reset")
    r = client.post("/api/rerun", json={"steps": ["nope"], "ids": [1]})
    assert r.status_code == 400


def _policy(client, candidates, db, mid):
    client.post("/api/settings", json={"act_wd": "record"})
    with db.write_lock:
        assert candidates.add(mid, "zz_test_char", "character", candidates.T_AI, "model", 0.99, "t") is False
    client.post("/api/settings", json={"act_wd": "confirm"})
    with db.write_lock:
        assert candidates.add(mid, "zz_test_char", "character", candidates.T_AI, "model", 0.99, "t") is True
        st = db.conn().execute("SELECT state, reason FROM media_tags mt JOIN tags t ON t.id=mt.tag_id WHERE mt.media_id=? AND t.name='zz_test_char'", (mid,)).fetchone()
        assert st["state"] == 1 and "自動確認" in st["reason"]
        db.conn().execute("DELETE FROM media_tags WHERE tag_id=(SELECT id FROM tags WHERE name='zz_test_char')")
    client.post("/api/steps/reset")
    assert client.get("/api/settings").json()["act_wd"] == "candidate"
