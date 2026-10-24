"""b101：備份到外接硬碟——增量複製、刪掉的先保留、整個資料夾讀不到時不動、資料庫快照、抽查、提醒。"""
import datetime
import os
import time
from pathlib import Path

from tests.conftest import TMP, make_image


def _wait(dbk, t=60):
    end = time.time() + t
    while time.time() < end:
        s = dbk.status()
        if s.get("state") != "running":
            return s
        time.sleep(0.1)
    raise AssertionError("備份沒有結束")


def _setup(monkeypatch, name):
    from app import drivebackup as dbk
    root = TMP / f"dbk_{name}"
    src, prog, ext = root / "src", root / "prog", root / "ext"
    for p in (src / "sub", prog / ".venv", prog / "app", ext):
        p.mkdir(parents=True, exist_ok=True)
    for i in range(6):
        make_image(src / ("sub" if i % 2 else "") / f"a{i}.jpg", 900 + i)
    (src / "note.txt").write_text("hello", "utf-8")
    (prog / "app" / "main.py").write_text("print(1)\n", "utf-8")
    (prog / ".venv" / "big.bin").write_bytes(b"x" * 1000)
    monkeypatch.setattr(dbk, "PROGRAM_DIR", prog)
    monkeypatch.setattr(dbk, "sources", lambda man: [{"area": "圖片", "label": "我的圖", "path": str(src)},
                                                     {"area": "程式", "label": "", "path": str(prog)}])
    monkeypatch.setenv("LIB_DRIVES", str(ext))
    return dbk, src, prog, ext


def test_drive_backup_incremental_b101(client, monkeypatch):
    from app import db
    dbk, src, prog, ext = _setup(monkeypatch, "inc")
    base = ext / dbk.BASE
    p = client.post("/api/drive-backup/plan", json={"dest": str(ext)}).json()
    assert p["ok"] and p["copy"] == 8 and p["first"], p             # 6 張圖＋note.txt＋程式 1 個（.venv 不算）
    info = client.get("/api/drive-backup").json()
    assert [d["path"] for d in info["drives"]] == [str(ext)]
    r = client.post("/api/drive-backup/start", json={"dest": str(ext)})
    assert r.status_code == 200, r.text
    s = _wait(dbk)
    assert s["state"] == "done", s
    res = s["result"]
    assert res["copied"] == 8 and res["errors"] == 0 and res["verified"] >= 8 and res["verify_bad"] == 0, res
    assert (base / "圖片" / "我的圖" / "sub" / "a1.jpg").read_bytes() == (src / "sub" / "a1.jpg").read_bytes()
    assert (base / "程式" / "app" / "main.py").exists() and not (base / "程式" / ".venv").exists()
    dbs = list((base / "資料庫").glob("library-*.db"))
    assert len(dbs) == 1 and dbs[0].stat().st_size > 0
    assert "我的圖" in (base / "還原說明.txt").read_text("utf-8-sig")
    assert db.get_settings()["drive_backup_dir"] == str(ext)
    assert client.get("/api/drive-backup").json()["found"] == str(ext), "插著的硬碟裡找得到之前的備份"
    assert db.backup_offsite(), "30 天內備份過外接硬碟＝有一份不在這顆硬碟上"

    # 第二次：沒變的不複製；刪掉的移到保留區；改過的舊版也先留
    (src / "a0.jpg").unlink()
    make_image(src / "sub" / "a1.jpg", 1999, size=(300, 300))
    make_image(src / "new.jpg", 2000)
    dbk.start(str(ext))
    res = _wait(dbk)["result"]
    assert res["copied"] == 2 and res["unchanged"] == 6 and res["held"] == 1 and res["replaced"] == 1, res
    today = datetime.date.today().isoformat()
    hold = base / "_刪除保留" / today / "圖片" / "我的圖"
    assert (hold / "a0.jpg").exists() and (hold / "sub" / "a1.jpg").exists()
    assert not (base / "圖片" / "我的圖" / "a0.jpg").exists()
    assert (base / "圖片" / "我的圖" / "sub" / "a1.jpg").read_bytes() == (src / "sub" / "a1.jpg").read_bytes()
    assert not list(base.rglob("*" + dbk.PART)), "沒有留下複製到一半的暫存檔"


def test_drive_backup_guards_b101(client, monkeypatch):
    dbk, src, prog, ext = _setup(monkeypatch, "guard")
    base = ext / dbk.BASE
    assert dbk.check_dest("") and dbk.check_dest(str(TMP / "nope" / "x"))
    from app import db
    root0 = db.get_settings()["roots"][0]
    assert any("圖庫" in e for e in dbk.check_dest(root0))
    # 只在雲端的檔案不複製
    (src / "cloud.bin").write_bytes(b"c" * 12345)
    monkeypatch.setattr(dbk, "_cloud_only", lambda st: st.st_size == 12345)
    dbk.start(str(ext))
    res = _wait(dbk)["result"]
    assert res["cloud"] == 1 and not (base / "圖片" / "我的圖" / "cloud.bin").exists(), res
    monkeypatch.setattr(dbk, "_cloud_only", lambda st: False)
    # 舊的保留區超過 30 天就清
    old = base / "_刪除保留" / "2000-01-01"
    old.mkdir(parents=True)
    (old / "x.jpg").write_bytes(b"1")
    # 整個資料夾讀不到（全部不見）：這次不移任何檔案
    moved = TMP / "dbk_guard_moved"
    os.replace(src, moved)
    src.mkdir()
    dbk.start(str(ext))
    res = _wait(dbk)["result"]
    assert res["held"] == 0 and res["guarded"] and res["purged"] == 1, res
    assert (base / "圖片" / "我的圖" / "a0.jpg").exists()
    # 空間不夠：直接停，不複製一半
    os.rmdir(src)
    os.replace(moved, src)
    make_image(src / "more.jpg", 3000)
    monkeypatch.setattr(dbk.shutil, "disk_usage", lambda p: type("U", (), {"free": 10, "total": 10, "used": 0})())
    dbk.start(str(ext))
    s = _wait(dbk)
    assert s["state"] == "error" and "空間不夠" in s["error"], s


def test_drive_backup_labels_and_remind_b101(client, monkeypatch):
    from app import db, drivebackup as dbk
    real = db.get_settings

    def fake():
        s = real()
        s["roots"] = [str(Path("/u/Pictures/iCloud Photos/Photos")), str(Path("/u/Pictures/圖庫")), str(Path("/d/圖庫")),
                      str(Path("/u/Pictures/圖庫/子資料夾"))]
        return s
    monkeypatch.setattr(db, "get_settings", fake)
    src = dbk.sources({})
    labels = [s["label"] for s in src if s["area"] == "圖片"]
    assert labels == ["iCloud Photos Photos", "圖庫", "圖庫 (2)"], labels         # 包在別的資料夾裡的不重複算
    d = str(Path("/d/圖庫"))
    again = dbk.sources({"sources": [{"area": "圖片", "label": "舊名字", "path": d}]})
    assert [s["label"] for s in again if s["path"] == d] == ["舊名字"], "沿用上次的名字"
    monkeypatch.setattr(db, "get_settings", real)
    db.update_settings({"drive_backup_last": {"at": time.time() - 20 * 86400}})
    assert dbk.overdue() == 20
    assert client.get("/api/status").json()["drive_overdue"] == 20
    db.update_settings({"drive_backup_last": {"at": time.time() - 3 * 86400}})
    assert dbk.overdue() is None
    db.update_settings({"drive_backup_last": {}})
    assert dbk.overdue() is None and dbk.last() is None
    assert Path(dbk.__file__).exists()
