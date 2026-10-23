"""影片轉 GIF：取格的時間、每格長度、存進圖庫（放在影片旁邊、帶上確認過的標籤）、複製分享、錯誤。"""
import os
import time

import numpy as np
import pytest

from tests.conftest import LIB


def make_video(path, seconds=4.0, fps=30, size=(320, 180)):
    """每一格的顏色編碼格數（R＝格數 × 2），用來檢查取的是哪一格。"""
    import cv2
    path.parent.mkdir(parents=True, exist_ok=True)
    w = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), fps, size)
    assert w.isOpened(), "這台機器的 OpenCV 不能寫 mp4"
    for i in range(int(seconds * fps)):
        fr = np.zeros((size[1], size[0], 3), np.uint8)
        fr[:, :, 2] = min(255, i * 2)        # BGR：紅色＝格數
        fr[:, :, 1] = 80
        w.write(fr)
    w.release()
    return path


@pytest.fixture(scope="module")
def video(client):
    from app import db, pipeline
    p = os.path.normpath(str(make_video(LIB / "gif_src" / "clip_gif.mp4")))
    pipeline.get_jobs().submit("scan", [p])
    for _ in range(240):
        r = db.conn().execute("SELECT id, status FROM media WHERE path=?", (p,)).fetchone()
        if r and r["status"] == "done":
            break
        time.sleep(0.25)
    assert r and r["status"] == "done", "測試影片沒有標記完"
    return r["id"], p


def _wait(client, jid, timeout=60):
    for _ in range(int(timeout / 0.1)):
        j = client.get(f"/api/gif/job/{jid}").json()
        if j["status"] in ("done", "error", "cancelled"):
            return j
        time.sleep(0.1)
    raise AssertionError("GIF 一直沒做好")


def test_frame_durations_add_up():
    from app import togif
    for fps in togif.FPS_CHOICES:
        d = togif.frame_durations(fps * 3, fps)
        assert sum(d) == 3000, (fps, sum(d))
        assert min(d) >= 20


def test_read_frames_picks_the_right_times(video):
    from app import togif
    mid, path = video
    fr = togif.read_frames(path, 1.0, 2.0, 10, 160)
    assert len(fr) == 10 and fr[0].size == (160, 90)
    reds = [np.asarray(f)[:, :, 0].mean() / 2 for f in fr]       # 回推格數
    want = [30 + 3 * k for k in range(10)]                        # 1.0 秒＝第 30 格，每 0.1 秒＝3 格
    assert all(abs(a - b) <= 1.5 for a, b in zip(reds, want)), (reds, want)


def test_plan_limits():
    from app import togif
    with pytest.raises(togif.GifError):
        togif.plan(300, 1280, 720, 0, 125, 15, 480)       # 太長（最多 2 分鐘）
    with pytest.raises(togif.GifError):
        togif.plan(300, 1920, 1080, 0, 120, 30, 1280)     # 太大（格數 × 像素）
    assert togif.plan(300, 1280, 720, 0, 120, 15, 480)["frames"] == 1800, "2 分鐘可以"
    with pytest.raises(togif.GifError):
        togif.plan(100, 1280, 720, 5, 5.05, 15, 480)      # 太短
    with pytest.raises(togif.GifError):
        togif.plan(100, 1280, 720, 0, 5, 7, 480)          # 不是可選的格數
    p = togif.plan(3, 320, 180, 1, 9, 15, 2000)
    assert p["end"] == 3 and p["width"] == 320 and p["height"] == 180, "不超過影片長度、不放大"


def test_make_save_and_share(client, video, monkeypatch):
    from PIL import Image
    from app import db, share
    mid, path = video
    tag = "gif_test_char"
    assert client.post("/api/tags/apply", json={"ids": [mid], "action": "add", "tag": tag, "category": "character"}).status_code == 200
    with db.write_lock:
        db.conn().execute("UPDATE media SET nsfw_manual=1 WHERE id=?", (mid,))
        db.conn().commit()

    j = client.post("/api/gif/make", json={"id": mid, "start": 0.5, "end": 2.5, "fps": 10, "width": 160, "dither": False}).json()
    j = _wait(client, j["id"])
    assert j["status"] == "done", j
    assert j["frames"] == 20 and (j["width"], j["height"]) == (160, 90)
    r = client.get(f"/api/gif/job/{j['id']}/file")
    assert r.status_code == 200 and r.headers["content-type"] == "image/gif"
    out = LIB / "gif_src" / "_dl.gif"
    out.write_bytes(r.content)
    with Image.open(out) as im:
        assert 1 < im.n_frames <= 20 and im.info.get("loop") == 0   # 一樣的連續格 Pillow 會合併（時間加在一起）
        total = 0
        for i in range(im.n_frames):
            im.seek(i)
            total += im.info["duration"]
        assert total == 2000
    out.unlink()

    # 複製分享：不用先存，剪貼簿放一個好認名字的 GIF
    got = []
    monkeypatch.setattr(share, "supported", lambda: True)
    monkeypatch.setattr(share, "set_clipboard", lambda files, image: got.append((files, image)))
    s = client.post(f"/api/gif/job/{j['id']}/share").json()
    assert s["ok"] and got and got[-1][1] is None
    assert os.path.basename(got[-1][0][0]) == "clip_gif_0_5s-2_5s.gif"

    # 存進圖庫：影片旁邊、帶上確認過的角色和手動 NSFW、交給掃描標記
    sv = client.post(f"/api/gif/job/{j['id']}/save").json()
    assert sv["name"] == "clip_gif_0_5s-2_5s.gif" and sv["tags"] >= 1
    assert os.path.dirname(sv["path"]) == os.path.dirname(path) and os.path.isfile(sv["path"])
    again = client.post(f"/api/gif/job/{j['id']}/save").json()
    assert again["id"] == sv["id"], "按兩次不會存兩份"
    c = db.conn()
    row = c.execute("""SELECT mt.state, mt.source FROM media_tags mt JOIN tags t ON t.id=mt.tag_id
                       WHERE mt.media_id=? AND t.name=?""", (sv["id"], tag)).fetchone()
    assert row and row["state"] == 1 and row["source"] == "gif"
    for _ in range(240):
        m = c.execute("SELECT status, kind, nsfw_manual FROM media WHERE id=?", (sv["id"],)).fetchone()
        if m["status"] == "done":
            break
        time.sleep(0.25)
    assert m["status"] == "done" and m["kind"] == "image" and m["nsfw_manual"] == 1
    assert c.execute("SELECT state FROM media_tags mt JOIN tags t ON t.id=mt.tag_id WHERE mt.media_id=? AND t.name=?",
                     (sv["id"], tag)).fetchone()[0] == 1, "標記完確認的標籤還在"
    assert c.execute("SELECT COUNT(*) FROM op_log WHERE label LIKE '影片轉 GIF%'").fetchone()[0] >= 1

    # 再存一段同樣時間的：檔名不撞
    j2 = _wait(client, client.post("/api/gif/make", json={"id": mid, "start": 0.5, "end": 2.5, "fps": 10, "width": 160}).json()["id"])
    sv2 = client.post(f"/api/gif/job/{j2['id']}/save").json()
    assert sv2["name"] == "clip_gif_0_5s-2_5s_2.gif" and sv2["id"] != sv["id"]


def test_errors(client, video):
    mid, _ = video
    img = client.get("/api/media", params={"view": "all", "limit": 1, "nsfw": "show"}).json()["items"][0]["id"]
    assert client.post("/api/gif/make", json={"id": img, "start": 0, "end": 1}).status_code == 400
    assert client.post("/api/gif/make", json={"id": mid, "start": 0, "end": 1, "fps": 7}).status_code == 400
    assert client.get("/api/gif/job/nope").status_code == 404
    assert client.post("/api/gif/job/nope/save").status_code == 400
    j = client.post("/api/gif/make", json={"id": mid, "start": 0, "end": 3, "fps": 30, "width": 320}).json()
    client.post(f"/api/gif/job/{j['id']}/cancel")
    j = _wait(client, j["id"])
    assert j["status"] in ("cancelled", "done")
    if j["status"] == "cancelled":
        assert client.get(f"/api/gif/job/{j['id']}/file").status_code == 409


def test_long_clip_and_after_share(client, tmp_path, monkeypatch):
    """長片段（> 30 秒）一格一格寫暫存檔、轉完刪掉；after=share 做好就直接放進剪貼簿。"""
    from app import db, pipeline, share, togif
    p = os.path.normpath(str(make_video(LIB / "gif_src" / "long_gif.mp4", seconds=45, fps=10, size=(160, 90))))
    pipeline.get_jobs().submit("scan", [p])
    for _ in range(240):
        r = db.conn().execute("SELECT id, status FROM media WHERE path=?", (p,)).fetchone()
        if r and r["status"] == "done":
            break
        time.sleep(0.25)
    got = []
    monkeypatch.setattr(share, "supported", lambda: True)
    monkeypatch.setattr(share, "set_clipboard", lambda files, image: got.append(files))
    j = client.post("/api/gif/make", json={"id": r["id"], "start": 0, "end": 45, "fps": 10, "width": 160, "after": "share"}).json()
    assert j["frames"] == 450 and j["after"] == "share"
    j = _wait(client, j["id"])
    assert j["status"] == "done" and j["shared"]["ok"] and not j["after_error"], j
    assert got and got[-1][0].endswith("long_gif_0_0s-45_0s.gif")
    assert not list(togif.tmp_dir().glob("*.npy")), "暫存的格轉完要刪掉"
    assert client.post("/api/gif/make", json={"id": r["id"], "start": 0, "end": 2, "after": "mail"}).status_code == 400
    # after=save：存進圖庫
    j = _wait(client, client.post("/api/gif/make", json={"id": r["id"], "start": 1, "end": 3, "fps": 10, "width": 160, "after": "save"}).json()["id"])
    assert j["status"] == "done" and j["saved"] and os.path.isfile(j["saved"]["path"])
