"""第六次檢查（b75）：ascii2d 佇列整理、找到率低的不排、預估、已確認角色當證據；Danbooru 反向別名、名字有 % 的資料卡。"""
import json

from tests.conftest import ids_by_name


def _ids(client, n):
    return sorted(ids_by_name(client).values())[:n]


def test_ascii2d_prune_and_low_yield(client):
    """排進來以後有了創作者／標成官方 → 不用找（你特地選的除外）；漫畫、有字、梗圖預設不排；一次性拿出佇列。"""
    from app import ascii2d, db
    c = db.conn()
    ids = _ids(client, 5)
    art = db.tag_id("prune_artist_(tst)", "artist")
    prev = {i: tuple(c.execute("SELECT pile, official FROM media WHERE id=?", (i,)).fetchone()) for i in ids}
    with db.write_lock:
        c.execute("DELETE FROM ascii2d WHERE status IN ('queued','searching','ready')")
        for k, i in enumerate(ids):
            c.execute("INSERT OR REPLACE INTO ascii2d(media_id, seq, status, picked) VALUES(?, ?, 'queued', ?)", (i, 7e6 + k, int(k == 1)))
            c.execute("UPDATE media SET pile='illust', official=NULL WHERE id=?", (i,))
        for i in ids[:2]:     # 0：後來有了創作者；1：有創作者但是你特地選的
            c.execute("INSERT OR REPLACE INTO media_tags(media_id, tag_id, source, state) VALUES(?,?,'manual',1)", (i, art))
        c.execute("UPDATE media SET official='anime' WHERE id=?", (ids[2],))
        c.execute("UPDATE media SET pile='comic' WHERE id=?", (ids[3],))
        c.commit()
    try:
        assert ascii2d.prune() >= 2
        st = dict(c.execute(f"SELECT media_id, status FROM ascii2d WHERE media_id IN ({','.join(map(str, ids))})").fetchall())
        assert st[ids[0]] == "skipped" and st[ids[2]] == "skipped", st
        assert st[ids[1]] == "queued", "你自己選的（沒勾只找沒有創作者的）照樣找"
        assert st[ids[3]] == st[ids[4]] == "queued"
        # 預設範圍不排漫畫；勾「也找找到率低的」才排；預估有數字
        with db.write_lock:
            c.execute(f"DELETE FROM ascii2d WHERE media_id IN ({','.join(map(str, ids))})")
            c.commit()
        pv = ascii2d.select("", None, True, None)
        assert ids[3] not in pv["ids"] and pv["low_out"] >= 1
        assert "expect" in pv and pv["minutes"] >= 1
        assert ids[3] in ascii2d.select("", None, True, None, low=True)["ids"]
        assert ids[3] in ascii2d.select("", [ids[3]], True, None)["ids"], "你選的圖不管找到率"
        r = client.get("/api/ascii2d/preview", params={"q": "", "low": True}).json()
        assert r["low_out"] == 0 and "expect" in r
        assert "queue" in client.get("/api/ascii2d").json()
        f = ascii2d.forecast(ids)
        assert 0 <= f["expect"] <= len(ids) and f["minutes"] >= 1
        assert ascii2d.forecast([]) == {"expect": 0, "minutes": 0}
    finally:
        with db.write_lock:
            for i, (p, o) in prev.items():
                c.execute("UPDATE media SET pile=?, official=? WHERE id=?", (p, o, i))
            c.execute("DELETE FROM media_tags WHERE tag_id=?", (art,))
            c.execute(f"DELETE FROM ascii2d WHERE media_id IN ({','.join(map(str, ids))})")
            c.commit()


def test_ascii2d_character_evidence(client):
    """已確認的角色當證據：一樣 →「很像」也自動確認（設定只有同一張時）；不一樣 → 不自動、提醒；角色標齊時標籤推的角色自動確認不勾。"""
    from app import ascii2d, db
    c = db.conn()
    mid = _ids(client, 6)[5]
    ch = db.tag_id("ev_char_(tst)", "character")
    prev_people = c.execute("SELECT people FROM media WHERE id=?", (mid,)).fetchone()[0]
    res = {"results": [{"rank": 1, "kind": "pixiv", "url": "u", "match": "likely", "score": 0.9,
                        "danbooru": {"characters": ["ev_char_(tst)"]}}], "best": 0,
           "tags": [{"name": "ev_char_(tst)", "category": "character", "why": "d", "checked": True},
                    {"name": "ev_other_(tst)", "category": "character", "why": "ascii2d → Pixiv 1 的標籤", "checked": False}]}

    def put(r):
        with db.write_lock:
            c.execute("INSERT OR REPLACE INTO ascii2d(media_id, seq, status, result) VALUES(?, 0, 'ready', ?)", (mid, json.dumps(r)))
            c.commit()
    with db.write_lock:
        c.execute("INSERT OR REPLACE INTO media_tags(media_id, tag_id, source, state) VALUES(?,?,'manual',1)", (mid, ch))
        c.execute("UPDATE media SET people=1 WHERE id=?", (mid,))
        c.commit()
    put(res)
    try:
        client.post("/api/settings", json={"ascii2d_auto_accept": "same"})
        r = ascii2d.result(mid)
        assert r["auto"] == "accept", "很像＋角色跟你確認的一樣"
        assert "跟你已確認的角色一樣" in r["tags"][0]["why"]
        assert r["tags"][1].get("auto") is False, "角色已經標齊：標籤推的其他角色自動確認不勾"
        res["results"][0]["danbooru"]["characters"] = ["somebody_else_(tst)"]
        res["tags"][0] = {"name": "somebody_else_(tst)", "category": "character", "why": "d", "checked": True}
        put(res)
        client.post("/api/settings", json={"ascii2d_auto_accept": "likely"})
        r = ascii2d.result(mid)
        assert r.get("auto") != "accept" and "ev_char_(tst)" in r["error"], r
        assert r["tags"][0]["checked"] is False, "很像但角色不同：先不勾"
        res["results"][0]["match"] = "same"
        put(res)
        r = ascii2d.result(mid)
        assert r.get("auto") != "accept" and r["tags"][0]["checked"] is True, "同一張：留著勾，但停下來給你看"
        js = client.get("/api/fetch-url/userscript.user.js").text
        assert "t.auto !== false" in js
        # 確認時，本來就確認的角色保留原本的來源
        ascii2d.decide(mid, "found", ["somebody_else_(tst)"])
        put({**res, "tags": [{"name": "ev_char_(tst)", "category": "character", "why": "d", "checked": True}]})
        ascii2d.decide(mid, "found", ["ev_char_(tst)"])
        src = c.execute("SELECT source FROM media_tags WHERE media_id=? AND tag_id=?", (mid, ch)).fetchone()[0]
        assert src == "manual"
        assert c.execute("SELECT source FROM media_tags WHERE media_id=? AND tag_id=?",
                         (mid, db.tag_id("somebody_else_(tst)", create=False))).fetchone()[0] == "ascii2d"
    finally:
        client.post("/api/settings", json={"ascii2d_auto_accept": "off"})
        with db.write_lock:
            c.execute("DELETE FROM media_tags WHERE media_id=? AND tag_id IN (SELECT id FROM tags WHERE name LIKE '%(tst)')", (mid,))
            c.execute("DELETE FROM ascii2d WHERE media_id=?", (mid,))
            c.execute("DELETE FROM media_source WHERE media_id=? AND method='ascii2d'", (mid,))
            c.execute("UPDATE media SET people=? WHERE id=?", (prev_people, mid))
            c.commit()


def test_taginfo_keeps_your_reverse_merge(client, monkeypatch):
    """你把 Danbooru 的新名字合併進舊名字（方向相反）：重抓資料卡時不再反向合併、不記錯誤。"""
    from app import danbooru, db, errlog, online
    from app.api.tags import merge_tags
    a, b = db.tag_id("rev_main_(tst)", "character"), db.tag_id("rev_new_(tst)", "character")
    merge_tags("rev_new_(tst)", "rev_main_(tst)", queue_info=False)
    assert db.tag_id("rev_new_(tst)", create=False) == a
    monkeypatch.setattr(online, "fetch_tag_info", lambda n: {"status": "ok", "alias_to": "rev_new_(tst)", "copyrights": [], "copyright": None, "post_count": 1,
                                                             "other_names": [], "links": [], "summary": "", "url": "u"})
    called = []
    import app.api.tags as T
    monkeypatch.setattr(T, "merge_tags", lambda *a, **k: called.append(a))
    before = errlog.recent(200)
    online.get_jobs()._taginfo_inner(["rev_main_(tst)"])
    assert not called
    assert not [e for e in errlog.recent(200) if e not in before and "rev_main" in json.dumps(e, ensure_ascii=False)]


def test_wiki_name_with_percent_is_encoded(monkeypatch):
    from app import danbooru
    paths = []

    def fake(path, params=None):
        paths.append(path)
        if path == "/tags.json":
            return [{"name": "ざか90%", "category": 4, "post_count": 3}]
        if path == "/posts.json":
            return []
        return {"is_deleted": False, "other_names": [], "body": ""}
    monkeypatch.setattr(danbooru, "dan_get", fake)
    info = danbooru.fetch_tag_info("ざか90%")
    assert any(p.startswith("/wiki_pages/") and "%25" in p and "90%." not in p for p in paths), paths
    assert "90%25" in info["url"]
    danbooru.fetch_tag_info("fate/grand_order")
    assert "/wiki_pages/fate/grand_order.json" in paths
