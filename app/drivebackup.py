"""備份到外接硬碟：圖片、程式、資料庫整份複製到另一顆硬碟，插上按一次就好。

每天的資料庫自動備份跟資料庫放在同一顆硬碟上：那顆硬碟壞掉，圖片、資料庫、7 份備份會一起不見。
這裡把「整理成果能完整接回來」需要的東西都放到外接硬碟：

    <外接硬碟>\\Pickle Jar 備份\\
        圖片\\<資料夾名稱>\\…       每個圖庫資料夾一份（照原本的子資料夾）
        程式\\…                     程式本身（不含 .venv、tools、models、data：裝回去會自己下載、重建）
        資料\\…                     data 資料夾裡縮圖、暫存、每日備份以外的東西（gallery-dl 下載紀錄、自己的譯名校對…）
        資料庫\\library-….db        資料庫快照（標籤、確認、否決、本子、設定），留最近 KEEP_DB 份
        _刪除保留\\<日期>\\…        電腦上刪掉、或內容被改過的舊檔：先放這裡，HOLD_DAYS 天後才清
        manifest.json               備份紀錄（每個資料夾原本的位置、上次備份的結果）
        還原說明.txt

- 增量：大小、修改時間都一樣的檔案不再複製（第一次久，之後通常幾分鐘）
- 不跟著刪：電腦上不見的檔案先移到 _刪除保留，被改過的檔案舊版也先移過去（誤刪、檔案壞掉、勒索病毒加密時救得回來）
- 一個資料夾整個讀不到、或一次少了一半以上：當成暫時讀不到（iCloud、隨身碟沒插），這次不移任何檔案
- iCloud 只存在雲端、還沒下載的檔案（Windows 的預留位置）不複製，不會因為備份觸發大量下載
- 複製完抽查：隨機挑一批檔案，比對兩邊內容（MD5）；資料庫快照跑 quick_check
"""
from __future__ import annotations

import datetime
import hashlib
import json
import logging
import os
import random
import re
import shutil
import sqlite3
import string
import threading
import time
from pathlib import Path

from . import db

log = logging.getLogger("drivebackup")
BASE = "Pickle Jar 備份"
HOLD_DAYS = 30
KEEP_DB = 10
REMIND_DAYS = 14
SAMPLE = 40
PROGRAM_DIR = Path(os.environ.get("LIB_PROGRAM_DIR") or Path(__file__).resolve().parent.parent)
PROGRAM_SKIP = {".venv", "venv", "tools", "models", "data", "__pycache__", "node_modules", ".pytest_cache",
                ".mypy_cache", ".ruff_cache"}
DATA_SKIP = {"thumbs", "preview", "backups", "gif_tmp", "gdl_tmp", "share_tmp", "upgrade_tmp", "library.db", "library.db-wal",
             "library.db-shm", "library.db-journal", "restore-pending.db", "__pycache__"}
PART = ".pjpart"
# Windows 的雲端預留位置：FILE_ATTRIBUTE_OFFLINE、RECALL_ON_OPEN、RECALL_ON_DATA_ACCESS
_CLOUD_ATTRS = 0x1000 | 0x40000 | 0x400000

JOB: dict = {}
_lock = threading.Lock()


class BackupError(Exception):
    pass


# ---------- 位置 ----------
def _L(p) -> str:
    """Windows 超過 260 字的路徑要加 \\\\?\\ 才讀寫得到（外接硬碟裡多一層「Pickle Jar 備份\\圖片\\…」很容易超過）。"""
    s = str(p)
    if os.name != "nt" or s.startswith("\\\\?\\") or len(s) < 240:
        return s
    s = os.path.abspath(s)
    return "\\\\?\\UNC\\" + s[2:] if s.startswith("\\\\") else "\\\\?\\" + s


def base_of(dest: str) -> Path:
    p = Path(os.path.normpath(dest.strip().strip('"')))
    return p if p.name == BASE else p / BASE


def _norm(p: str) -> str:
    return os.path.normcase(os.path.join(os.path.normpath(p), ""))


def _same_disk(a: str, b: str) -> bool:
    try:
        return os.stat(a).st_dev == os.stat(b).st_dev
    except OSError:
        return False


def drives() -> list[dict]:
    """可以拿來備份的磁碟（Windows：每個磁碟機代號；其他系統：/Volumes、/media、/mnt 底下）。"""
    env = os.environ.get("LIB_DRIVES")
    if env is not None:
        paths = [p for p in env.split(os.pathsep) if p]
    elif os.name == "nt":
        import ctypes
        mask = ctypes.windll.kernel32.GetLogicalDrives()
        paths = [f"{c}:\\" for i, c in enumerate(string.ascii_uppercase) if mask >> i & 1]
    else:
        paths = []
        for top in ("/Volumes", "/media", "/mnt", f"/media/{os.environ.get('USER', '')}", f"/run/media/{os.environ.get('USER', '')}"):
            try:
                paths += [os.path.join(top, n) for n in os.listdir(top) if os.path.isdir(os.path.join(top, n))]
            except OSError:
                pass
    out = []
    for p in dict.fromkeys(paths):
        kind, label = _drive_info(p)
        if kind in ("cdrom", "none"):
            continue
        try:
            du = shutil.disk_usage(p)
        except OSError:
            continue
        out.append({"path": p, "label": label, "kind": kind, "total": du.total, "free": du.free,
                    "same_disk": _same_disk(p, str(db.DATA_DIR)),
                    "has_backup": (Path(p) / BASE / "manifest.json").is_file()})
    return out


def _drive_info(p: str) -> tuple[str, str]:
    if os.name != "nt" or os.environ.get("LIB_DRIVES") is not None:
        return "fixed", os.path.basename(os.path.normpath(p)) or p
    import ctypes
    t = ctypes.windll.kernel32.GetDriveTypeW(p)
    kind = {2: "removable", 3: "fixed", 4: "network", 5: "cdrom", 6: "ram"}.get(t, "none" if t == 1 else "fixed")
    buf = ctypes.create_unicode_buffer(261)
    ok = ctypes.windll.kernel32.GetVolumeInformationW(p, buf, 261, None, None, None, None, 0)
    return kind, (buf.value if ok else "")


def find_existing() -> str | None:
    """插著的硬碟裡已經有之前的備份（磁碟機代號換了也找得到）。"""
    for d in drives():
        if d["has_backup"]:
            return d["path"]
    return None


def check_dest(dest: str) -> list[str]:
    """回傳錯誤；空的＝可以。"""
    if not dest or not dest.strip():
        return ["請選擇外接硬碟"]
    base = base_of(dest)
    top = base.parent
    if not os.path.isdir(top):
        return [f"找不到 {top}（硬碟沒插好？）"]
    from .archive import SYNCED
    if SYNCED.search(str(base) + os.sep):
        return ["不能備份到 iCloud／OneDrive／Google Drive／Dropbox 這類同步資料夾（要一份不會跟著同步刪除的）"]
    errs = []
    nb = _norm(str(base))
    for r in [*db.get_settings()["roots"], str(db.DATA_DIR), str(PROGRAM_DIR)]:
        nr = _norm(r)
        if nb.startswith(nr) or nr.startswith(nb):
            errs.append(f"備份位置不能在圖庫、資料或程式資料夾裡面，也不能包住它們（{r}）")
            break
    return errs


def warnings_for(dest: str) -> list[str]:
    top = str(base_of(dest).parent)
    w = []
    if _same_disk(top, str(db.DATA_DIR)):
        w.append("這個位置跟資料庫在同一顆硬碟上：那顆硬碟壞掉時備份會一起不見。最好選另一顆硬碟")
    return w


# ---------- 要備份的東西 ----------
def _label(name: str, used: set[str]) -> str:
    s = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip(" .") or "資料夾"
    out, i = s, 2
    while out.lower() in used:
        out, i = f"{s} ({i})", i + 1
    used.add(out.lower())
    return out


def sources(manifest: dict) -> list[dict]:
    """[{area, label, path}]：圖庫資料夾（包在別的資料夾裡的不重複算）＋程式。label 沿用上次的，換了位置也不會多一份。"""
    roots = [os.path.normpath(r) for r in db.get_settings()["roots"]]
    roots = [r for r in roots if not any(o != r and _norm(r).startswith(_norm(o)) for o in roots)]
    old = {_norm(s["path"]): s["label"] for s in manifest.get("sources", []) if s.get("area") == "圖片"}
    used = {v.lower() for v in old.values()}
    out = []
    for r in roots:
        lb = old.get(_norm(r))
        if not lb:
            parts = Path(r).parts
            name = parts[-1] if parts else "資料夾"
            if name.lower() in ("photos", "pictures", "images", "圖片", "照片") and len(parts) > 1:
                name = f"{parts[-2]} {name}"         # iCloud Photos\\Photos → 「iCloud Photos Photos」比「Photos」好認
            lb = _label(name, used)
        out.append({"area": "圖片", "label": lb, "path": r})
    out.append({"area": "程式", "label": "", "path": str(PROGRAM_DIR)})
    out.append({"area": "資料", "label": "", "path": str(db.DATA_DIR)})
    return out


def _cloud_only(st) -> bool:
    return bool(getattr(st, "st_file_attributes", 0) & _CLOUD_ATTRS)


def _list(root: str, area: str, exclude: list[str], skip_cloud: bool) -> tuple[dict, int]:
    """{相對路徑: (大小, 修改時間)}、只在雲端的檔案數。"""
    files, cloud = {}, 0
    stack = [root]
    skip = set(exclude) | {"程式": PROGRAM_SKIP, "資料": DATA_SKIP}.get(area, set())
    while stack:
        d = stack.pop()
        try:
            with os.scandir(_L(d)) as it:
                for e in it:
                    if e.name in skip or (area == "圖片" and e.name.startswith(".")) or e.name.endswith(PART):
                        continue
                    try:
                        if e.is_dir(follow_symlinks=False):
                            stack.append(os.path.join(d, e.name))
                        elif e.is_file(follow_symlinks=False):
                            st = e.stat(follow_symlinks=False)
                            if skip_cloud and _cloud_only(st):
                                cloud += 1
                                continue
                            rel = os.path.relpath(os.path.join(d, e.name), root)
                            files[rel] = (st.st_size, st.st_mtime)
                    except OSError:
                        continue
        except OSError:
            continue
    return files, cloud


def _same(a: tuple, b: tuple) -> bool:
    return a[0] == b[0] and abs(a[1] - b[1]) <= 2      # exFAT／FAT 的修改時間只到 2 秒


def _target_dir(base: Path, s: dict) -> Path:
    return base / "圖片" / s["label"] if s["area"] == "圖片" else base / s["area"]


def load_manifest(base: Path) -> dict:
    try:
        return json.loads((base / "manifest.json").read_text("utf-8"))
    except (OSError, ValueError):
        return {}


def plan(dest: str) -> dict:
    """要複製幾個檔案、多大；外接硬碟夠不夠放（設定視窗預覽用）。"""
    errs = check_dest(dest)
    if errs:
        return {"ok": False, "errors": errs}
    base = base_of(dest)
    man = load_manifest(base)
    s = db.get_settings()
    rows, n_copy, b_copy, n_all, b_all, cloud, hold = [], 0, 0, 0, 0, 0, 0
    for src in sources(man):
        if not os.path.isdir(src["path"]):
            rows.append({**src, "missing": True})
            continue
        sf, cl = _list(src["path"], src["area"], s["exclude"], src["area"] == "圖片")
        df, _ = _list(str(_target_dir(base, src)), src["area"], [], False) if (_target_dir(base, src)).is_dir() else ({}, 0)
        nc = [r for r, v in sf.items() if r not in df or not _same(v, df[r])]
        bc = sum(sf[r][0] for r in nc)
        nh = sum(1 for r in df if r not in sf)
        rows.append({**src, "files": len(sf), "bytes": sum(v[0] for v in sf.values()), "copy": len(nc), "copy_bytes": bc,
                     "gone": nh, "cloud": cl})
        n_copy, b_copy, n_all, cloud, hold = n_copy + len(nc), b_copy + bc, n_all + len(sf), cloud + cl, hold + nh
        b_all += rows[-1]["bytes"]
    db_size = db.DB_PATH.stat().st_size if db.DB_PATH.exists() else 0
    free = shutil.disk_usage(base.parent).free
    need = b_copy + db_size
    return {"ok": True, "errors": [], "warnings": warnings_for(dest), "base": str(base), "sources": rows,
            "files": n_all, "bytes": b_all, "copy": n_copy, "copy_bytes": b_copy, "gone": hold, "cloud": cloud,
            "db_bytes": db_size, "free": free, "enough": free > need * 1.02 + (200 << 20), "first": not man,
            "last": man.get("last")}


# ---------- 執行 ----------
def start(dest: str) -> dict:
    errs = check_dest(dest)
    if errs:
        raise BackupError(errs[0])
    with _lock:
        if JOB.get("state") == "running":
            raise BackupError("已經在備份了")
        JOB.clear()
        JOB.update({"state": "running", "phase": "整理要複製的檔案", "dest": str(base_of(dest)), "done": 0, "total": 0,
                    "bytes_done": 0, "bytes_total": 0, "started": time.time(), "stop": False, "error": None})
    db.update_settings({"drive_backup_dir": str(base_of(dest).parent)})
    from . import tasks
    tasks.spawn("drive-backup", "備份到外接硬碟", _run, dest, probe=lambda: {"done": JOB.get("done"), "total": JOB.get("total"),
                                                                           "note": JOB.get("phase")},
                cancel=cancel)
    return status()


def cancel() -> bool:
    if JOB.get("state") != "running":
        return False
    JOB["stop"] = True
    return True


def status() -> dict:
    j = {k: v for k, v in JOB.items() if k != "stop"}
    return j or {"state": "idle"}


def _hold(base: Path, area_dir: str, rel: str, today: str) -> None:
    """備份裡的檔案移到 _刪除保留\\<今天>\\…（同一顆硬碟，只是改名，很快）。"""
    src = base / area_dir / rel
    dst = base / "_刪除保留" / today / area_dir / rel
    os.makedirs(_L(dst.parent), exist_ok=True)
    if os.path.exists(_L(dst)):
        stem, ext = os.path.splitext(dst.name)
        i = 2
        while os.path.exists(_L(dst.with_name(f"{stem} ({i}){ext}"))):
            i += 1
        dst = dst.with_name(f"{stem} ({i}){ext}")
    os.replace(_L(src), _L(dst))


def _purge_hold(base: Path) -> int:
    h = base / "_刪除保留"
    if not h.is_dir():
        return 0
    cutoff = datetime.date.today() - datetime.timedelta(days=HOLD_DAYS)
    n = 0
    for d in h.iterdir():
        try:
            day = datetime.date.fromisoformat(d.name)
        except ValueError:
            continue
        if day < cutoff:
            shutil.rmtree(_L(d), ignore_errors=True)
            n += 1
    return n


def _md5(p: str) -> str:
    h = hashlib.md5()
    with open(_L(p), "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def _db_snapshot(base: Path) -> dict:
    d = base / "資料庫"
    d.mkdir(parents=True, exist_ok=True)
    name = f"library-{datetime.datetime.now():%Y%m%d-%H%M%S}.db"
    tmp = d / (name + PART)
    src = sqlite3.connect(db.DB_PATH, timeout=30)
    out = sqlite3.connect(tmp)
    try:
        src.backup(out)
        ok = out.execute("PRAGMA quick_check").fetchone()[0] == "ok"
    finally:
        out.close()
        src.close()
    if not ok:
        tmp.unlink(missing_ok=True)
        raise BackupError("資料庫快照檢查沒過（quick_check）")
    os.replace(tmp, d / name)
    olds = sorted(d.glob("library-*.db"))
    for p in olds[:-KEEP_DB]:
        p.unlink(missing_ok=True)
    return {"name": name, "size": (d / name).stat().st_size}


def _run(dest: str):
    j = JOB
    base = base_of(dest)
    t0 = time.time()
    res = {"copied": 0, "copied_bytes": 0, "unchanged": 0, "held": 0, "replaced": 0, "cloud": 0, "errors": [], "skipped_dirs": [],
           "guarded": [], "verified": 0, "verify_bad": 0, "db": None, "purged": 0}
    try:
        base.mkdir(parents=True, exist_ok=True)
        man = load_manifest(base)
        s = db.get_settings()
        today = datetime.date.today().isoformat()
        work = []       # (source, 目標資料夾, 要複製的相對路徑, 要移到保留區的相對路徑)
        for src in sources(man):
            if j["stop"]:
                break
            tdir = _target_dir(base, src)
            area_dir = os.path.relpath(tdir, base)
            if not os.path.isdir(src["path"]):
                res["skipped_dirs"].append(src["path"])
                continue
            sf, cl = _list(src["path"], src["area"], s["exclude"], src["area"] == "圖片")
            res["cloud"] += cl
            df, _ = _list(str(tdir), src["area"], [], False) if tdir.is_dir() else ({}, 0)
            copy = [r for r, v in sf.items() if r not in df or not _same(v, df[r])]
            res["unchanged"] += len(sf) - len(copy)
            gone = [r for r in df if r not in sf]
            # 一次少了一半以上：多半是資料夾暫時讀不到（iCloud 同步中、權限），這次不移任何檔案
            if gone and (not sf or (len(df) > 200 and len(sf) < len(df) * 0.5)):
                res["guarded"].append({"path": src["path"], "gone": len(gone)})
                gone = []
            work.append((src, tdir, area_dir, sf, copy, gone, df))
        j.update(phase="複製", total=sum(len(w[4]) for w in work), bytes_total=sum(sum(w[3][r][0] for r in w[4]) for w in work))
        need = j["bytes_total"] + (db.DB_PATH.stat().st_size if db.DB_PATH.exists() else 0)
        free = shutil.disk_usage(base).free
        if free < need * 1.02 + (200 << 20):
            raise BackupError(f"外接硬碟空間不夠：要 {need / 1e9:.1f} GB，剩 {free / 1e9:.1f} GB")
        copied_paths = []
        for src, tdir, area_dir, sf, copy, gone, df in work:
            for rel in copy:
                if j["stop"]:
                    break
                a = os.path.join(src["path"], rel)
                b = tdir / rel
                try:
                    os.makedirs(_L(b.parent), exist_ok=True)
                    tmp = b.with_name(b.name + PART)
                    shutil.copy2(_L(a), _L(tmp))
                    if rel in df:                      # 內容被改過：舊的那份先留著（檔案壞掉、被加密時救得回來）
                        _hold(base, area_dir, rel, today)
                        res["replaced"] += 1
                    os.replace(_L(tmp), _L(b))
                    res["copied"] += 1
                    res["copied_bytes"] += sf[rel][0]
                    copied_paths.append((a, str(b), sf[rel][0]))
                except Exception as e:  # noqa  一個檔案失敗不影響其他
                    try:
                        Path(_L(b.with_name(b.name + PART))).unlink(missing_ok=True)
                    except OSError:
                        pass
                    if len(res["errors"]) < 200:
                        res["errors"].append({"path": a, "error": f"{type(e).__name__}: {e}"[:200]})
                j["done"] += 1
                j["bytes_done"] += sf[rel][0]
            for rel in gone:
                if j["stop"]:
                    break
                try:
                    _hold(base, area_dir, rel, today)
                    res["held"] += 1
                except OSError as e:
                    if len(res["errors"]) < 200:
                        res["errors"].append({"path": str(tdir / rel), "error": f"移到 _刪除保留 失敗：{e}"[:200]})
        if j["stop"]:
            j["state"] = "stopped"
            return
        j["phase"] = "資料庫"
        res["db"] = _db_snapshot(base)
        j["phase"] = "抽查"
        pool = [x for x in copied_paths if x[2] < 300 << 20]
        sample = random.sample(pool, min(SAMPLE, len(pool)))
        if len(sample) < SAMPLE:              # 這次複製的不多：再從備份裡隨便挑幾個舊的比
            olds = [(os.path.join(w[0]["path"], r), str(w[1] / r)) for w in work for r in w[3] if r not in w[4]]
            sample += [(a, b, 0) for a, b in random.sample(olds, min(SAMPLE - len(sample), len(olds)))]
        for a, b, _ in sample:
            try:
                same = _md5(a) == _md5(b)
            except OSError:
                same = os.path.exists(_L(b)) and not os.path.exists(_L(a))    # 抽查時原檔剛好被刪：不算錯
            res["verified"] += 1
            if not same:
                res["verify_bad"] += 1
                res["errors"].append({"path": b, "error": "抽查：備份跟原檔內容不一樣"})
        res["purged"] = _purge_hold(base)
        srcs = [{k: w[0][k] for k in ("area", "label", "path")} for w in work]
        srcs += [x for x in man.get("sources", []) if _norm(x["path"]) not in {_norm(w["path"]) for w in srcs}]
        summary = {"at": time.time(), "seconds": round(time.time() - t0), "base": str(base), "copied": res["copied"],
                   "copied_bytes": res["copied_bytes"], "unchanged": res["unchanged"], "held": res["held"], "replaced": res["replaced"],
                   "cloud": res["cloud"], "errors": len(res["errors"]), "verified": res["verified"], "verify_bad": res["verify_bad"],
                   "guarded": res["guarded"], "skipped_dirs": res["skipped_dirs"], "purged": res["purged"],
                   "ok": not res["errors"] and not res["skipped_dirs"]}
        man.update({"app": "pickle-jar", "version": 1, "sources": srcs, "data_dir": str(db.DATA_DIR), "program_dir": str(PROGRAM_DIR),
                    "last": summary, "history": ([summary] + man.get("history", []))[:30]})
        tmp = base / ("manifest.json" + PART)
        tmp.write_text(json.dumps(man, ensure_ascii=False, indent=1), "utf-8")
        os.replace(tmp, base / "manifest.json")
        (base / "還原說明.txt").write_text(restore_text(man), "utf-8-sig")
        db.update_settings({"drive_backup_last": summary})
        j.update(state="done", phase="完成", result={**summary, "error_list": res["errors"][:50], "db": res["db"]})
        log.info("備份到外接硬碟：複製 %d 個（%.1f GB）、沒變 %d、移到保留區 %d、錯誤 %d、%d 秒",
                 res["copied"], res["copied_bytes"] / 1e9, res["unchanged"], res["held"], len(res["errors"]), summary["seconds"])
        db.log_op(f"備份到外接硬碟（{res['copied']:,} 個檔案）", keys="drive_backup")
    except Exception as e:  # noqa
        log.warning("備份到外接硬碟失敗：%s", e, exc_info=not isinstance(e, BackupError))
        j.update(state="error", error=str(e) if isinstance(e, BackupError) else f"{type(e).__name__}: {e}"[:300])


def restore_text(man: dict) -> str:
    lines = [
        "Pickle Jar 備份：怎麼還原",
        "=" * 30,
        "",
        f"上次備份：{datetime.datetime.fromtimestamp(man['last']['at']):%Y-%m-%d %H:%M}" if man.get("last") else "",
        "",
        "這個資料夾裡有：",
        "  圖片\\        你的圖庫資料夾，一個資料夾一份（照原本的子資料夾）",
        "  程式\\        Pickle Jar 程式本身",
        "  資料\\        程式 data 資料夾裡的其他檔案（下載紀錄、譯名校對）",
        "  資料庫\\      資料庫快照：所有標籤、確認、否決、本子、設定都在這裡面（最新的在最後面）",
        "  _刪除保留\\   電腦上刪掉、或被改過的舊檔，保留 30 天",
        "",
        "整台電腦壞掉、換電腦時：",
        "",
        "1. 把「程式」整個資料夾複製回電腦（例如 C:\\Users\\你\\Desktop\\pickle-jar）。",
        "2. 把「圖片」裡的每個資料夾複製回原本的位置（下面有對照表）。",
        "   放到別的位置也可以：程式會用檔案內容認圖，掃描後自動接回原本的標籤。",
        "3. 在程式資料夾裡建一個 data 資料夾，把「資料」裡的東西都複製進去，",
        "   再把「資料庫」裡最新的 library-日期.db 也複製進去，改名成 library.db。",
        "4. 雙擊 start.bat。第一次會重新下載 Python 和 AI 模型（要幾分鐘）、重算縮圖。",
        "5. 圖片放的位置跟原本不一樣的話：設定 › 圖庫資料夾 改成新的位置，再按「重新掃描」。",
        "",
        "只是不小心刪了幾張圖：到「圖片」或「_刪除保留\\日期」裡找，複製回原本的資料夾，",
        "下次掃描就會接回原本的標籤。",
        "",
        "原本的位置對照：",
    ]
    for s in man.get("sources", []):
        tgt = f"圖片\\{s['label']}" if s["area"] == "圖片" else s["area"]
        lines.append(f"  {tgt}  ←  {s['path']}")
    return "\r\n".join(lines) + "\r\n"


def last() -> dict | None:
    v = db.get_settings().get("drive_backup_last") or None
    return v if isinstance(v, dict) and v.get("at") else None


def overdue() -> int | None:
    """上次備份到外接硬碟是幾天前（超過 REMIND_DAYS 才回天數；沒備份過＝None）。"""
    lt = last()
    if not lt:
        return None
    days = int((time.time() - lt["at"]) // 86400)
    return days if days >= REMIND_DAYS else None


def recent_ok(days: int = 30) -> bool:
    lt = last()
    return bool(lt and time.time() - lt["at"] < days * 86400)
