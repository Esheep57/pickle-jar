"""媒材 CLIP 特徵與快速標媒材（b78）：有 CLIP 特徵的圖用 WD＋CLIP 模型；排序；背景工作（假模型，不下載）。"""
import numpy as np

from tests.conftest import ids_by_name


def _imgs(client):
    from app import db
    c = db.conn()
    return sorted(i for i in ids_by_name(client).values()
                  if c.execute("SELECT 1 FROM media WHERE id=? AND kind='image' AND status='done' AND embedding IS NOT NULL", (i,)).fetchone())


def test_clip_model_queue_and_job(client, monkeypatch):
    from app import clipemb, db, mediaclf, pipeline
    c = db.conn()
    ids = _imgs(client)
    assert len(ids) >= 8
    prev = {i: tuple(c.execute("SELECT media_type, media_src, media_conf FROM media WHERE id=?", (i,)).fetchone()) for i in ids}
    monkeypatch.setattr(mediaclf, "MIN_TOTAL", 4)
    monkeypatch.setattr(mediaclf, "MIN_PER_CLASS", 1)
    rng = np.random.default_rng(3)
    try:
        # 假的 CLIP 特徵：前一半「照片」群、後一半「插畫」群
        half = len(ids) // 2
        C = rng.normal(size=(2, 512))
        with db.write_lock:
            for k, i in enumerate(ids):
                e = (C[0 if k < half else 1] + 0.1 * rng.normal(size=512)).astype(np.float16)
                c.execute("INSERT OR REPLACE INTO media_clip(media_id, fp, emb, at) VALUES(?, (SELECT fp FROM media WHERE id=?), ?, 0)", (i, i, e.tobytes()))
            c.commit()
        client.post("/api/media/mediatype", json={"ids": ids[:3], "label": "photo"})
        client.post("/api/media/mediatype", json={"ids": ids[half:half + 3], "label": "illustration"})
        res = mediaclf.run()
        assert res["labels_clip"] >= 6 and res["used"]["clip"] >= 1, res
        got = dict(c.execute(f"SELECT id, media_type FROM media WHERE id IN ({','.join(map(str, ids))})").fetchall())
        assert got[ids[half - 1]] == "photo" and got[ids[-1]] == "illustration", got
        ev = client.get("/api/diag/eval-media").json()
        assert "clip" in ev and "wd" in ev and ev["labels"] >= 6
        st = client.get("/api/mediaclf/status").json()
        assert st["clip"]["done"] >= len(ids) and st["labels"].get("user", 0) >= 6
        # 背景工作：假模型算特徵（不下載），檔案換過（fp 不同）就重算
        monkeypatch.setenv("LIB_MEDIA_CLIP", "1")
        monkeypatch.setattr(clipemb, "session", lambda progress=None: None)
        monkeypatch.setattr(clipemb, "embed", lambda imgs: np.ones((len(imgs), 512), np.float32) / np.sqrt(512))
        with db.write_lock:
            c.execute("UPDATE media_clip SET fp='old' WHERE media_id=?", (ids[0],))
            c.commit()
        assert ids[0] in clipemb.pending_ids()
        clipemb.run(pipeline.get_jobs(), [ids[0]])
        assert ids[0] not in clipemb.pending_ids()
        e = np.frombuffer(c.execute("SELECT emb FROM media_clip WHERE media_id=?", (ids[0],)).fetchone()[0], np.float16)
        assert e.shape == (512,) and abs(float(e[0]) - 1 / np.sqrt(512)) < 1e-3
    finally:
        if mediaclf._timer:
            mediaclf._timer.cancel()
        mediaclf._model.clear()
        with db.write_lock:
            c.execute(f"DELETE FROM media_label WHERE media_id IN ({','.join(map(str, ids))})")
            c.execute(f"DELETE FROM media_clip WHERE media_id IN ({','.join(map(str, ids))})")
            c.executemany("UPDATE media SET media_type=?, media_src=?, media_conf=? WHERE id=?", [(*v, i) for i, v in prev.items()])
            c.commit()
        from app import piles
        piles.refresh(ids)


def test_clip_preprocess_shape():
    from PIL import Image

    from app import clipemb
    x = clipemb.preprocess(Image.new("RGB", (300, 500), (255, 0, 0)))
    assert x.shape == (3, 224, 224) and x.dtype == np.float32
    assert abs(float(x[0, 0, 0]) - (1 - clipemb.MEAN[0]) / clipemb.STD[0]) < 1e-4


def test_clip_gpu_crash_guard(client, monkeypatch):
    """第一次用 GPU 時先記「試用中」；下次啟動還是「試用中」＝上次崩潰 → 改用 CPU；GPU 出 Python 錯誤也改 CPU。"""
    from app import clipemb, db, tagger
    monkeypatch.setattr(clipemb, "_download", lambda progress=None: clipemb.model_path())
    made = []

    class Sess:
        def __init__(self, gpu, fail):
            self.gpu, self.fail = gpu, fail

        def run(self, out, feeds):
            if self.fail:
                raise RuntimeError("DML 壞了")
            return [np.ones((feeds["pixel_values"].shape[0], 512), np.float32)]

        def get_providers(self):
            return ["DmlExecutionProvider" if self.gpu else "CPUExecutionProvider"]

    fail = {"gpu": False}

    def make(path, gpu):
        made.append(gpu)
        return Sess(gpu, gpu and fail["gpu"])
    monkeypatch.setattr(clipemb, "_make", make)
    monkeypatch.setattr(tagger, "_providers", lambda: ["DmlExecutionProvider", "CPUExecutionProvider"])
    try:
        db.update_settings({"_clip_gpu": ""})
        clipemb._sess = None
        assert clipemb.session().get_providers()[0] == "DmlExecutionProvider"
        assert db.get_settings()["_clip_gpu"] == "trying"
        clipemb.check_crash()                          # 假裝程式崩潰後重開
        assert db.get_settings()["_clip_gpu"] == "crashed"
        clipemb._sess = None
        assert clipemb.session().get_providers()[0] == "CPUExecutionProvider"
        # Python 層的錯誤：當場改 CPU，記 failed
        db.update_settings({"_clip_gpu": ""})
        fail["gpu"] = True
        clipemb._sess = None
        assert clipemb.session().get_providers()[0] == "CPUExecutionProvider"
        assert db.get_settings()["_clip_gpu"] == "failed"
        # ok 之後重開不會被當成崩潰
        db.update_settings({"_clip_gpu": "ok"})
        clipemb.check_crash()
        assert db.get_settings()["_clip_gpu"] == "ok"
        # 不滿一批時補滿再切掉
        fail["gpu"] = False
        clipemb._sess = None
        from PIL import Image
        e = clipemb.embed([Image.new("RGB", (64, 64))] * 3)
        assert e.shape == (3, 512)
    finally:
        clipemb._sess = None
        clipemb.STATUS.pop("provider", None)
        db.update_settings({"_clip_gpu": ""})
