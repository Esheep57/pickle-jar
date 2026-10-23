"""作品集頁序（b54）：智慧排序（分本、集數、手機編號 9999→0001、影片插在時間最近的後面）、各種排序、
手動調整（只傳一部分也不撞號）、好幾本合成一本時每頁帶「第幾本」。"""
import os

from tests.conftest import ids_by_name


def _rows(paths, mt=None):
    return [{"id": i + 1, "path": p, "mtime": (mt or {}).get(p, 0)} for i, p in enumerate(paths)]


def test_smart_order_books_and_volumes():
    from app import albums
    B = "C:\\Users\\U\\Pictures\\匯入\\e-hentai\\"
    books = ["[Hiro] 《兽X王X兽》猴子戏法——人间篇 完 [eh3668586]", "[Hiro] 《兽X王X兽》2 庭院野花 [eh3043523]",
             "[Hiro] 《兽X王X兽》 [eh3006096]", "[Hiro] 《兽X王X兽》4 增殖之饵 [eh3336375]", "[Hiro] 《兽X王X兽》3 权利放弃 [eh3181902]"]
    paths = [B + b + f"\\{n:03d}.webp" for n in (2, 1, 10) for b in books]     # 交錯、10 在 2 後面
    rows = _rows(paths)
    p = albums.plan(rows, roots={os.path.normcase(os.path.normpath("C:\\Users\\U\\Pictures\\匯入"))})
    name = {r["id"]: r["path"] for r in rows}
    got = [(os.path.basename(os.path.dirname(name[i].replace("\\", "/"))), os.path.basename(name[i].replace("\\", "/"))) for i in p["order"]]
    want_books = [books[2], books[1], books[4], books[3], books[0]]       # 1、2、3、4、完
    assert got == [(b, f) for b in want_books for f in ("001.webp", "002.webp", "010.webp")]
    assert [b["vol"] for b in p["books"]] == ["", "2", "3", "4", "完"]
    assert p["books"][0]["label"] == "[Hiro] 《兽X王X兽》" and p["books"][0]["n"] == 3
    # 中文集數、番外排最後
    assert albums._volume("第三卷")[0] == 3 and albums._volume("十二 話")[0] == 12 and albums._volume("番外")[0] > albums._volume("完")[0]


def test_smart_order_phone_numbers_wrap_and_videos():
    from app import albums
    M = "C:\\Users\\U\\Pictures\\圖庫\\"
    paths = [M + "2025-01\\IMG_0257.PNG", M + "2024-10\\IMG_9169.JPG", M + "2025-01\\IMG_9407.JPG",
             M + "2025-01\\IMG_0293.JPG", M + "2025-01\\clip.mp4", M + "2024-12\\IMG_9230.JPG"]
    mt = {paths[0]: 100, paths[3]: 300, paths[4]: 110, paths[1]: 5, paths[2]: 50, paths[5]: 20}
    rows = _rows(paths, mt)
    p = albums.plan(rows, roots={os.path.normcase(os.path.normpath(M[:-1]))})
    name = {r["id"]: r["path"].split("\\")[-1] for r in rows}
    assert [name[i] for i in p["order"]] == ["IMG_9169.JPG", "IMG_9230.JPG", "IMG_9407.JPG", "IMG_0257.PNG", "clip.mp4", "IMG_0293.JPG"]
    assert p["books"] == []          # 按月份的資料夾不算一本
    # 沒有繞一圈：照編號
    p2 = albums.plan(_rows([M + "2025-02\\IMG_8391.JPG", M + "2024-04\\IMG_8042.PNG", M + "2025-02\\IMG_8388.PNG"]), roots=set())
    assert p2["order"] == [2, 3, 1]


def test_album_sort_api_and_sections(client):
    from app import db
    c = db.conn()
    paths = {r[0]: r[1] for r in c.execute("SELECT id, path FROM media WHERE status='done' AND kind='image'")}
    in_set = sorted(i for i, p in paths.items() if os.path.basename(os.path.dirname(p)) == "set")
    roots = {os.path.normcase(os.path.normpath(r)) for r in db.get_settings()["roots"]}
    loose = sorted(i for i, p in paths.items() if os.path.normcase(os.path.normpath(os.path.dirname(p))) in roots)[:3]
    assert len(in_set) >= 2 and len(loose) == 3
    ids = list(reversed(loose + in_set))
    a0 = c.execute("SELECT COALESCE(MAX(id), 0) FROM actions").fetchone()[0]
    aid = client.post("/api/albums", json={"title": "頁序測試", "ids": ids}).json()["id"]
    try:
        d = client.get(f"/api/albums/{aid}").json()
        # 建立時就智慧排序：set 這本在前（照檔名），不在書資料夾的接在後面；有兩段 → 目錄、每頁帶 sec
        assert d["is_smart"] and len(d["books"]) == 2 and d["books"][0]["n"] == len(in_set) and d["books_contiguous"]
        items = client.get("/api/media", params={"view": "all", "nsfw": "show", "album": aid, "sort": "page", "limit": 100}).json()["items"]
        assert [it["sec"] for it in items] == [0] * len(in_set) + [1] * 3
        assert {it["id"] for it in items[:len(in_set)]} == set(in_set)
        smart = [it["id"] for it in items]
        # 倒過來 → 交錯了就不畫分隔、提示；撤銷回來
        r = client.post(f"/api/albums/{aid}/sort", json={"by": "reverse"}).json()
        assert r["moved"] >= 2
        assert [it["id"] for it in client.get("/api/media", params={"view": "all", "nsfw": "show", "album": aid, "limit": 100}).json()["items"]] == smart[::-1]
        client.post("/api/undo", json={})
        assert [p["id"] for p in client.get(f"/api/albums/{aid}/pages").json()] == smart
        # 手動：只傳一張 → 移到最前，其他照原本順序，不撞號
        client.post(f"/api/albums/{aid}/order", json={"ids": [smart[-1], 999999]})
        pg = client.get(f"/api/albums/{aid}/pages").json()
        assert [p["id"] for p in pg] == [smart[-1]] + smart[:-1]
        assert len({r[0] for r in c.execute("SELECT pos FROM album_items WHERE album_id=?", (aid,))}) == len(smart)
        d = client.get(f"/api/albums/{aid}").json()
        assert not d["is_smart"] and not d["books_contiguous"]
        assert "sec" not in client.get("/api/media", params={"view": "all", "nsfw": "show", "album": aid, "limit": 100}).json()["items"][0]
        pv = client.get(f"/api/albums/{aid}/sort-preview", params={"by": "smart"}).json()
        assert pv["moved"] > 0 and len(pv["books"]) == 2 and pv["first"]
        assert client.post(f"/api/albums/{aid}/sort", json={"by": "smart"}).json()["moved"] > 0
        assert [p["id"] for p in client.get(f"/api/albums/{aid}/pages").json()] == smart
        for by in ("name", "taken", "added"):
            assert client.post(f"/api/albums/{aid}/sort", json={"by": by}).status_code == 200
        assert client.post(f"/api/albums/{aid}/sort", json={"by": "nope"}).status_code == 400
        assert client.get("/api/albums/99999/pages").status_code == 404
        # 原本是智慧排序 → 加頁會放到該在的位置
        client.post(f"/api/albums/{aid}/sort", json={"by": "smart"})
        client.post(f"/api/albums/{aid}/remove", json={"ids": [in_set[0]]})
        client.post(f"/api/albums/{aid}/add", json={"ids": [in_set[0]]})
        assert [p["id"] for p in client.get(f"/api/albums/{aid}/pages").json()] == smart
    finally:
        with db.write_lock:
            c.execute("DELETE FROM album_items WHERE album_id=?", (aid,))
            c.execute("DELETE FROM album_tags WHERE album_id=?", (aid,))
            c.execute("DELETE FROM albums WHERE id=?", (aid,))
            c.execute("DELETE FROM actions WHERE id>?", (a0,))     # 不留撤銷紀錄：後面的測試按 Ctrl+Z 不能撤到這本
            c.commit()
