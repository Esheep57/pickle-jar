"""長時間執行才會出現的問題（2026-10 稽核）。"""
from tests.conftest import ids_by_name, wait_idle


def test_argless_jobs_not_queued_twice(client):
    """沒有參數的工作（sweep…）已經在排隊就不再排：以前背景搜源每 10 張排一次，排到上百個整庫重算。"""
    from app import pipeline
    j = pipeline.get_jobs()
    wait_idle(client)
    j.pause(True)
    try:
        before = j.q.qsize()
        for _ in range(5):
            j.submit("sweep")
        assert j.q.qsize() == before + 1
        j.submit("retag", [1])
        j.submit("retag", [1])
        assert j.q.qsize() == before + 3, "有參數的照排"
    finally:
        j.pause(False)
    wait_idle(client)
    j.submit("sweep")
    wait_idle(client)
    assert "sweep" not in j._queued_kinds


def test_maintenance_cleans_orphans_and_preview(client, tmp_path, monkeypatch):
    """定期整理：刪掉已經不存在的圖留下的紀錄；轉檔快取超過上限刪最舊的；WAL 寫回。"""
    import json
    import os
    import time
    from app import db, maintenance
    from app.api import library
    c = db.conn()
    from app import ascii2d
    ascii2d._ensure() if hasattr(ascii2d, "_ensure") else None
    with db.write_lock:
        c.execute("INSERT OR REPLACE INTO ascii2d(media_id, seq, status, result) VALUES(987654321, 0, 'none', ?)", (json.dumps({}),))
        c.execute("INSERT OR REPLACE INTO knn_refs(media_id, tag, refs) VALUES(987654321, 'x', '[]')")
    monkeypatch.setattr(library, "PREVIEW_DIR", tmp_path)
    monkeypatch.setattr(maintenance, "PREVIEW_MAX", 250)
    for i in range(3):
        p = tmp_path / f"{i}.jpg"
        p.write_bytes(b"x" * 100)
        os.utime(p, (time.time() - 100 + i, time.time() - 100 + i))
    out = maintenance.periodic()
    assert out["orphans"].get("ascii2d", 0) >= 1 and out["orphans"].get("knn_refs", 0) >= 1
    assert not c.execute("SELECT 1 FROM ascii2d WHERE media_id=987654321").fetchone()
    assert out["preview"] == 1 and not (tmp_path / "0.jpg").exists() and (tmp_path / "2.jpg").exists()
    assert len(out["wal"]) == 3
