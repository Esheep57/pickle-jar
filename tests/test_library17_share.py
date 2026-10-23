"""分享（複製到剪貼簿給 QQ／Messenger）：放哪些檔案、什麼時候加上圖片、哪些格式先轉檔。"""
import os

import pytest


def _mids(n=3, ext=None):
    from app import db
    q = "SELECT id, path FROM media WHERE status='done' AND kind='image'" + (" AND lower(path) LIKE ?" if ext else "") + " ORDER BY id LIMIT ?"
    return [(r[0], r[1]) for r in db.conn().execute(q, ((f"%{ext}", n) if ext else (n,)))]


@pytest.fixture
def fake_clip(monkeypatch):
    from app import share
    got = []
    monkeypatch.setattr(share, "supported", lambda: True)
    monkeypatch.setattr(share, "set_clipboard", lambda files, image: got.append((files, image)))
    return got


def test_share_one_image_puts_file_and_picture(client, fake_clip):
    (mid, path), = _mids(1)
    r = client.post("/api/media/share", json={"ids": [mid]}).json()
    assert r["ok"] and r["n"] == 1
    files, image = fake_clip[-1]
    assert len(files) == 1 and os.path.samefile(files[0], path)
    if path.lower().endswith((".jpg", ".jpeg", ".png")):
        assert image == files[0], "一張靜態圖：剪貼簿也放圖片（只收圖片的地方也貼得上）"


def test_share_many_only_files_and_skips_missing(client, fake_clip):
    from app import db
    ids = [m for m, _ in _mids(3)]
    r = client.post("/api/media/share", json={"ids": ids + [ids[0], 99999999]}).json()
    assert r["ok"] and r["n"] == 3 and r["missing"] == 1
    files, image = fake_clip[-1]
    assert len(files) == 3 and image is None, "好幾張只放檔案"


def test_share_converts_formats_chat_apps_reject(client, fake_clip, tmp_path):
    from PIL import Image
    from app import db, share
    (mid, path), = _mids(1)
    src = tmp_path / "share_test.webp"
    Image.new("RGB", (64, 48), (200, 30, 30)).save(src, "WEBP")
    with db.write_lock:
        db.conn().execute("UPDATE media SET path=? WHERE id=?", (str(src), mid))
    try:
        r = client.post("/api/media/share", json={"ids": [mid]}).json()
        assert r["ok"] and r["converted"] == 1
        files, image = fake_clip[-1]
        out = files[0]
        assert out.endswith(".jpg") and os.path.dirname(out) == str(share.tmp_dir())
        assert Image.open(out).size == (64, 48) and image == out
        assert src.exists(), "原檔不動"
    finally:
        with db.write_lock:
            db.conn().execute("UPDATE media SET path=? WHERE id=?", (path, mid))


def test_share_gif_keeps_animation(client, fake_clip, tmp_path):
    from PIL import Image
    from app import db
    (mid, path), = _mids(1)
    src = tmp_path / "anim.gif"
    fr = [Image.new("P", (20, 20), i) for i in (1, 2)]
    fr[0].save(src, save_all=True, append_images=fr[1:], duration=100, loop=0)
    with db.write_lock:
        db.conn().execute("UPDATE media SET path=? WHERE id=?", (str(src), mid))
    try:
        r = client.post("/api/media/share", json={"ids": [mid]}).json()
        files, image = fake_clip[-1]
        assert files == [str(src)] and image is None and r["gifs"] == 1, "GIF 只放檔案（放圖片會貼成靜態的）"
    finally:
        with db.write_lock:
            db.conn().execute("UPDATE media SET path=? WHERE id=?", (path, mid))


def test_share_not_windows_and_errors(client, monkeypatch):
    from app import share
    (mid, _), = _mids(1)
    monkeypatch.setattr(share, "supported", lambda: False)
    assert client.post("/api/media/share", json={"ids": [mid]}).json() == {"ok": False, "reason": "unsupported", "missing": 0, "converted": 0}
    assert client.post("/api/media/share", json={"ids": []}).status_code == 400
    monkeypatch.setattr(share, "supported", lambda: True)
    def boom(files, image): raise RuntimeError("剪貼簿沒有回應")
    monkeypatch.setattr(share, "set_clipboard", boom)
    r = client.post("/api/media/share", json={"ids": [mid]})
    assert r.status_code == 409 and "剪貼簿" in r.json()["detail"]
