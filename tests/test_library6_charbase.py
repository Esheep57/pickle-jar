"""本體＋造型只算一個角色：多人圖缺角色、chars:、大圖的「已標幾個角色」都跟 WD 人數對得上。"""


def test_variant_counts_as_one(client):
    from app import db, people
    c = db.conn()
    mid = c.execute("SELECT id FROM media WHERE status='done' ORDER BY id DESC LIMIT 1").fetchone()[0]
    with db.write_lock:
        for name in ("asuna_(blue_archive)", "asuna_(bunny)_(blue_archive)", "jojo_test", "jojo_test_(jojolion_test)"):
            tid = db.tag_id(name, "character")
            c.execute("INSERT OR REPLACE INTO media_tags(media_id, tag_id, source, state) VALUES(?,?, 'manual', 1)", (mid, tid))
        db.tag_id("jojolion_test", "copyright")
        c.execute("UPDATE media SET people=3, people_done=0 WHERE id=?", (mid,))
        c.execute("DELETE FROM media_source WHERE media_id=?", (mid,))
    people.refresh_bases()
    base = {r[0] for r in c.execute("""SELECT t.name FROM char_base cb JOIN tags t ON t.id=cb.tag_id""")}
    assert "asuna_(bunny)_(blue_archive)" in base
    assert "jojo_test_(jojolion_test)" not in base, "括號是作品：可能是另一個人，不併"
    inf = people.info(mid)
    assert inf["chars"] == 3 and inf["short"] is False, inf          # 3 個 tag 是 2 個人＋jojolion 那個另算 = 3
    with db.write_lock:
        c.execute("UPDATE media SET people=4 WHERE id=?", (mid,))
    assert people.info(mid)["short"] is True, "4 個人、只標了 3 個角色（本體＋造型算一個）→ 還有人沒標"
    got = {i["id"] for i in client.get("/api/media", params={"q": "chars:=3", "limit": 1000}).json()["items"]}
    assert mid in got
    with db.write_lock:
        c.execute("UPDATE media SET people=0 WHERE id=?", (mid,))
        c.execute("DELETE FROM media_tags WHERE media_id=? AND tag_id IN (SELECT id FROM tags WHERE name IN (?,?,?,?))",
                  (mid, "asuna_(blue_archive)", "asuna_(bunny)_(blue_archive)", "jojo_test", "jojo_test_(jojolion_test)"))
