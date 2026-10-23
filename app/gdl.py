"""其他網站交給 gallery-dl 下載（b84）。

自己寫的匯入（importer.py）只管 X、Pixiv、Danbooru、e621、Rule34、E-Hentai、nhentai：那幾個網站有額外的整理
（Pixiv 選頁、X 作者、整本建本子…），而且 gallery-dl 抓 X、Pixiv 要登入（X 要 cookie、Pixiv 要 OAuth），不適合拿來換。
其他網站（Bluesky、DeviantArt、ArtStation、Gelbooru、yande.re、Misskey…，gallery-dl 支援的上百個）
都交給 gallery-dl：網站改版時更新 gallery-dl 就好，不用改這裡。

做法：
- 網址先問 gallery-dl 有沒有對應的 extractor（`gallery_dl.extractor.find`）。`directlink`（任意圖片網址）、`generic`、
  `recursive` 這類「什麼網址都收」的不接（跟原本「只替認得的網站下載」的原則一樣）。
- 用子程式跑 `python -m gallery_dl`（gallery-dl 自己的設定檔、cookie 照用），下載到 data/gdl_tmp/<工作>/，
  每個檔案旁邊有 `--write-metadata` 的 JSON；全部下載完才搬進 匯入/<網站>/（監看不會看到下載到一半的檔案）。
- 一個網址是整個帳號、標籤、收藏（不是單篇）時：預設全部抓；設定 gdl_max_posts 可以只抓最新幾「篇」。
  邊下載邊放進圖庫（每 20 個檔案一批）；下載紀錄存在 data/gdl_archive.sqlite3，再匯入同一個帳號只抓新的。
- 登入：照 gallery-dl 自己的設定檔（這個程式不保存、不轉交任何網站的登入資訊）。
- 不接付費內容平台（FANBOX）和付費內容的轉貼站（Kemono、Coomer）、Iwara：見 DENY。
- 已經在圖庫的（檔案 MD5 一樣）不再存一份，只補記貼文網址。
- 記在 import_meta（site＝gdl:<網站>、貼文網址、作者、網站標籤）；匯入後處理：Bluesky 等網站的帳號對到
  Danbooru 登記的畫師 → 創作者候選（對不上就不建立新畫師）；一般流程照樣跑（MD5、以圖搜圖…）。
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shlex
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

from . import db

log = logging.getLogger("gdl")
# 什麼網址都收的 extractor：不接（任意網址＝可以拿來替別人下載任何東西）
DENY = {"directlink", "generic", "recursive", "noop", "test", "oauth", "ytdl", "imagehosts",
        "fanbox", "kemono", "kemonoparty", "coomer", "coomerparty", "iwara"}    # 付費內容平台、付費內容轉貼站、Iwara：不接
# 程式自己匯入的網站：其他形式的網址（X 個人頁、Pixiv 使用者頁…）也不交給 gallery-dl（要登入，而且會整個帳號一起抓）
NATIVE = {"twitter", "pixiv", "danbooru", "e621", "e926", "rule34", "exhentai", "nhentai"}
# 單篇（一則貼文、一件作品）的 subcategory；其他的（user、favorite、search、tag…）是整批
SINGLE = {"post", "tweet", "deviation", "image", "note", "work", "artwork", "illust", "status", "show", "view",
          "pin", "submission", "entry", "file", "single", "gallery_image", "media", "photo", "video", "story"}
VIDEO_EXT = {"mp4", "webm", "mov", "m4v", "mkv"}
IMAGE_EXT = {"jpg", "jpeg", "png", "gif", "webp", "avif", "heic", "heif", "bmp", "jfif"}
# gallery-dl 的網站名稱 → sigscan.resolve 認得的帳號種類
ACCOUNT = {"bluesky": "bsky", "skeb": "skeb", "patreon": "patreon"}
JOBS: dict[str, dict] = {}
_lock = threading.Lock()
NEED_LOGIN: dict = {}       # 網站 → (設定鍵, 沒填時的訊息)
DEFAULT_UA = ""             # 空白＝gallery-dl 自己的（設定 gdl_user_agent 可以指定）
PLACE_EVERY = 20            # 每下載幾個檔案就先放進圖庫（大量下載中途斷掉不會全丟）


class GdlError(Exception):
    pass


def available() -> bool:
    try:
        import gallery_dl  # noqa: F401
        return True
    except ImportError:
        return False


def version() -> str | None:
    try:
        from gallery_dl import version as v
        return v.__version__
    except Exception:  # noqa
        return None


def match(url: str) -> dict | None:
    """網址 → {category, sub, single}；gallery-dl 不認得、或是「什麼都收」的 extractor → None。"""
    url = (url or "").strip()
    if not re.match(r"^https?://", url, re.I) or len(url) > 2000 or not available():
        return None
    try:
        from gallery_dl import extractor
        ex = extractor.find(url)
    except Exception:  # noqa  gallery-dl 對某些網址會丟例外（壞掉的網址）
        return None
    if not ex or ex.category in DENY | NATIVE or getattr(ex, "basecategory", "") in DENY:
        return None
    return {"category": ex.category, "sub": ex.subcategory, "single": ex.subcategory in SINGLE}


def site_label(cat: str) -> str:
    return {"bluesky": "Bluesky", "deviantart": "DeviantArt", "artstation": "ArtStation",
            "gelbooru": "Gelbooru", "yandere": "yande.re", "konachan": "Konachan", "kemono": "Kemono",
            "misskey.io": "Misskey", "tumblr": "Tumblr", "instagram": "Instagram", "skeb": "Skeb",
            "patreon": "Patreon", "safebooru": "Safebooru", "sankaku": "Sankaku", "weibo": "微博",
            "lofter": "LOFTER", "bilibili": "bilibili", "newgrounds": "Newgrounds", "furaffinity": "FurAffinity",
            "inkbunny": "Inkbunny", "reddit": "Reddit", "pinterest": "Pinterest", "mastodon": "Mastodon"}.get(cat, cat)


def _cmd() -> list[str]:
    o = os.environ.get("LIB_GDL_CMD")          # 測試用：假的 gallery-dl
    return shlex.split(o) if o else [sys.executable, "-m", "gallery_dl"]


def _tmp_root() -> Path:
    return db.DATA_DIR / "gdl_tmp"


# ---------- 從 gallery-dl 的 metadata 讀資訊（每個網站欄位名稱不一樣，挑常見的） ----------
def _name_of(v) -> str | None:
    if isinstance(v, str):
        return v.strip() or None
    if isinstance(v, dict):
        for k in ("name", "nick", "nickname", "display_name", "displayName", "username", "handle", "screen_name"):
            if isinstance(v.get(k), str) and v[k].strip():
                return v[k].strip()
    return None


def _handle_of(cat: str, kw: dict) -> str | None:
    """網站帳號（給 sigscan.resolve 對 Danbooru 畫師用）。"""
    if cat == "bluesky":
        a = kw.get("author") or {}
        h = a.get("handle") if isinstance(a, dict) else None
        return re.sub(r"\.bsky\.social$", "", h) if h else None
    if cat in ("skeb", "patreon"):
        a = kw.get("creator") or kw.get("author") or kw.get("user")
        return (a.get("screen_name") or a.get("vanity") or a.get("name")) if isinstance(a, dict) else (a if isinstance(a, str) else None)
    return None


def _tags_of(kw: dict) -> list[str]:
    t = kw.get("tags") or kw.get("tag_string") or []
    if isinstance(t, str):
        t = t.split()
    elif isinstance(t, dict):        # 有些網站分類：{"artist": [...], "character": [...]}
        t = [x for v in t.values() for x in (v if isinstance(v, list) else [v])]
    out = []
    for x in t if isinstance(t, list) else []:
        n = x.get("name") if isinstance(x, dict) else x
        if isinstance(n, str) and n.strip() and n not in out:
            out.append(n.strip())
    return out[:80]


def meta_of(cat: str, kw: dict, url: str) -> dict:
    author = None
    for k in ("author", "user", "artist", "creator", "uploader", "owner", "account"):
        author = _name_of(kw.get(k))
        if author:
            break
    rating = str(kw.get("rating") or kw.get("x_restrict") or "").lower()
    nsfw = bool(kw.get("sensitive") or kw.get("nsfw") or kw.get("is_mature") or kw.get("adult")
                or rating in ("q", "e", "questionable", "explicit", "1", "2", "r-18", "r18"))
    post = kw.get("post_url") or kw.get("url_post") or kw.get("page_url") or url
    return {"category": cat, "author": author, "handle": _handle_of(cat, kw),
            "title": str(kw.get("title") or "")[:200] or None, "tags": _tags_of(kw), "nsfw": nsfw,
            "post_id": str(kw.get("post_id") or kw.get("id") or "") or None, "post_url": post if isinstance(post, str) else url}


# ---------- 下載 ----------
def start(url: str) -> dict:
    """排一個 gallery-dl 下載（背景）。回傳給匯入視窗看的資訊。"""
    if not available():
        raise GdlError("這個網站要用 gallery-dl 下載，但還沒安裝：到 系統 › 工具 ›「從網站匯入」按「安裝 gallery-dl」，"
                       "或關掉 start.bat 的視窗再開一次（會自動安裝）")
    if not db.get_settings().get("gdl_enabled", True):
        raise GdlError("gallery-dl 匯入關著（設定 › 從網站匯入）")
    m = match(url)
    if not m:
        raise GdlError("不支援的網址：gallery-dl 也不認得這個網站")
    need = NEED_LOGIN.get(m["category"])
    if need and not (db.get_settings().get(need[0]) or "").strip():
        raise GdlError(need[1])
    with _lock:
        j = JOBS.get(url)
        if j and j["state"] == "running":
            return _public(j)
        limit = int(db.get_settings().get("gdl_max_posts") or 0)     # 整個帳號的網址：最新幾篇（0＝全部）
        j = {"url": url, "kind": "gdl", "site": m["category"], "label": site_label(m["category"]), "single": m["single"],
             "limit": None if m["single"] or limit <= 0 else limit, "state": "running", "done": 0, "total": 0, "saved": 0,
             "existing": 0, "skipped": 0, "post_skipped": 0, "error": None, "started": time.time(),
             "title": site_label(m["category"]), "stop": False, "post_dir": {}}
        JOBS[url] = j
        while len(JOBS) > 30:
            JOBS.pop(next(k for k, v in JOBS.items() if v["state"] != "running") if any(v["state"] != "running" for v in JOBS.values()) else next(iter(JOBS)))
    from . import tasks
    tasks.spawn("gdl", f"匯入（gallery-dl）：{j['label']}", _run, j, probe=tasks.probe_of(j), cancel=lambda: cancel(url))
    return _public(j)


def _public(j: dict) -> dict:
    return {k: v for k, v in j.items() if k not in ("stop", "proc", "post_dir", "_cfg")}


def status() -> list[dict]:
    return [_public(j) for j in JOBS.values()]


def cancel(url: str) -> bool:
    j = JOBS.get(url)
    if not j or j["state"] != "running":
        return False
    j["stop"] = True
    p = j.get("proc")
    if p and p.poll() is None:
        try:
            p.terminate()
        except OSError:
            pass
    return True


def _config(s: dict) -> dict:
    """這次執行的 gallery-dl 設定（加在使用者自己的設定檔之上）。"""
    ex: dict = {}
    ua = (s.get("gdl_user_agent") or "").strip() or DEFAULT_UA
    if ua:
        ex["user-agent"] = ua
    return {"extractor": ex}


def _args(j: dict, work: Path) -> list[str]:
    s = db.get_settings()
    cfg = work.parent / f"{work.name}.cfg.json"           # 有登入 cookie：不放在暫存下載資料夾裡（那裡的檔案會被當成下載的）
    cfg.write_text(json.dumps(_config(s), ensure_ascii=False), "utf-8")
    j["_cfg"] = str(cfg)
    args = [*_cmd(), "-c", str(cfg), "--write-metadata", "--no-part", "-D", str(work),
            "--download-archive", str(db.DATA_DIR / "gdl_archive.sqlite3")]   # 再匯入同一個帳號：抓過的不再下載
    if j["limit"]:
        args += ["--post-range", f"1-{j['limit']}"]        # 按「篇」算（以前 --range 是按檔案算）
    return [*args, j["url"]]


def _run(j: dict):
    work = _tmp_root() / hashlib.sha1(f"{j['url']}{time.time()}".encode()).hexdigest()[:12]
    try:
        work.mkdir(parents=True, exist_ok=True)
        env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
        p = subprocess.Popen(_args(j, work), stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=str(work), env=env,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        j["proc"] = p
        err_lines: list[str] = []

        def _drain_err():
            for ln in iter(p.stderr.readline, b""):
                s = ln.decode("utf-8", "replace").strip()
                if s:
                    if "skipping post" in s.lower():
                        j["post_skipped"] += 1
                    err_lines.append(s)
                    del err_lines[:-20]

        th = threading.Thread(target=_drain_err, daemon=True)
        th.start()
        since, last = 0, time.time()
        for ln in iter(p.stdout.readline, b""):       # gallery-dl 每存一個檔案印一行路徑；抓過的（下載紀錄裡有）印「# 路徑」
            t = ln.decode("utf-8", "replace").strip()
            if not t:
                continue
            if t.startswith("#"):
                j["skipped"] += 1
                continue
            j["done"] += 1
            since += 1
            if since >= PLACE_EVERY or time.time() - last > 15:
                _place_ready(j, work)
                since, last = 0, time.time()
        rc = p.wait()
        th.join(5)
        _place_ready(j, work, final=True)
        if j["stop"]:
            j["state"] = "stopped"
            return
        if not (j["saved"] or j["existing"] or j["skipped"]):
            j["state"] = "error"
            j["error"] = _explain(err_lines, rc, j)
            return
        if rc != 0 and err_lines:
            j["error"] = "有些沒抓到：" + _explain(err_lines, rc, j)
        j["state"] = "done"
    except Exception as e:  # noqa
        log.warning("gallery-dl 匯入失敗 %s：%s", j["url"], e)
        j["state"] = "error"
        j["error"] = str(e)[:300]
    finally:
        j.pop("proc", None)
        shutil.rmtree(work, ignore_errors=True)
        try:
            os.remove(j.pop("_cfg", "") or "\0")
        except OSError:
            pass


def _explain(lines: list[str], rc, j: dict | None = None) -> str:
    t = " ".join(lines[-4:])
    low = t.lower()
    last = re.sub(r"^\[[^\]]*\]\[\w+\]\s*", "", lines[-1]) if lines else ""
    skipped = f"（{j['post_skipped']} 篇被跳過）" if j and j.get("post_skipped") else ""
    if "unsupported url" in low:
        return "gallery-dl 不支援這個網址"
    if "403" in low:
        login = "需要登入的內容請在 gallery-dl 的設定檔填 cookie；"
        return f"網站擋下了請求（HTTP 403）{skipped}。{login}也可能是網站的機器人防護。最後一行：{last[:160]}"
    if "401" in low or "login required" in low or "authorization" in low:
        return f"這個網站要登入：請在 gallery-dl 的設定檔填 cookie 或帳號。最後一行：{last[:160]}"
    if "404" in low or "not found" in low:
        return "找不到這篇（可能被刪除）"
    return (last[:240] or f"gallery-dl 結束碼 {rc}，沒有下載到檔案") + skipped


def _md5(p: Path) -> str:
    h = hashlib.md5()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def _place_ready(j: dict, work: Path, final: bool = False):
    """暫存資料夾裡下載完的檔案（metadata JSON 已經寫好的）先放進圖庫；final＝最後一次，剩下的全部放。"""
    try:
        files = sorted(f for f in work.iterdir() if f.is_file() and not f.name.endswith((".json", ".part"))
                       and (final or f.with_name(f.name + ".json").exists()))
    except OSError:
        return
    if files:
        _place(j, files)


def _place(j: dict, files: list[Path]):
    """暫存資料夾 → 匯入/<網站>/；同一篇有好幾個檔案收進一個小資料夾。記 import_meta、送去標記。
    會分好幾批呼叫（邊下載邊放）：同一篇的資料夾記在 j["post_dir"]，後面的批次放同一個地方。"""
    from . import importer
    importer._ensure()
    cat = j["site"]
    base = importer.dest_dir("gdl:" + cat)
    c = db.conn()
    metas, counts = {}, {}
    for f in files:
        kw = {}
        mf = f.with_name(f.name + ".json")
        if mf.exists():
            try:
                kw = json.loads(mf.read_text("utf-8"))
            except ValueError:
                kw = {}
        kw = kw if isinstance(kw, dict) else {}
        metas[f] = meta_of(cat, kw, j["url"])
        counts[f] = kw.get("count") if isinstance(kw.get("count"), int) else None
    per_post: dict[str, int] = {}
    for f in files:
        per_post[metas[f]["post_id"] or ""] = per_post.get(metas[f]["post_id"] or "", 0) + 1
    saved, rows = [], []
    j["total"] += len(files)
    for f in files:
        meta = metas[f]
        ext = f.suffix.lower().lstrip(".")
        if ext not in IMAGE_EXT | VIDEO_EXT:
            _drop(f)
            continue
        pid = meta["post_id"] or ""
        n_post = max(counts[f] or 0, per_post.get(pid, 1))
        md5 = _md5(f)
        dup = c.execute("SELECT id FROM media WHERE fp=?", (md5,)).fetchone()
        p = {"site": "gdl:" + cat, "url": meta["post_url"]}
        if dup:
            j["existing"] += 1
            importer._remember_url(dup[0], p, {**meta, "post_files": n_post})
            _drop(f)
            continue
        d = j["post_dir"].get(pid)
        if d is None:
            d = base
            if n_post > 1 and pid:
                tail = meta["title"] or (meta["author"] and "@" + meta["author"]) or ""
                d = base / (importer._safe_name(re.sub(r"\s+", " ", f"{pid} {tail}").strip())[:80].rstrip(" .") or pid)
            j["post_dir"][pid] = d
        d.mkdir(parents=True, exist_ok=True)
        dst = d / importer._safe_name(f.name)
        if dst.exists():
            j["existing"] += 1
            _drop(f)
            continue
        shutil.move(str(f), str(dst))
        _drop(f, file=False)
        saved.append(str(dst))
        rows.append((os.path.normpath(str(dst)), "gdl:" + cat, meta["post_url"],
                     json.dumps({**meta, "post_files": n_post}, ensure_ascii=False), time.time()))
    j["saved"] += len(saved)
    if saved:
        with db.write_lock:
            c.executemany("INSERT OR REPLACE INTO import_meta(path, site, post_url, data, created_at, applied) VALUES(?,?,?,?,?,0)", rows)
            c.commit()
        from . import pipeline
        pipeline.get_jobs().submit("scan", saved)
        db.log_op(f"gallery-dl 匯入 {site_label(cat)} {len(saved)} 張", keys="gdl_import")
    if j["existing"]:
        importer.after_processed()


def _drop(f: Path, file: bool = True):
    """處理過的暫存檔（和它的 metadata）刪掉，下一批不會再處理一次。"""
    for x in ([f] if file else []) + [f.with_name(f.name + ".json")]:
        try:
            x.unlink()
        except OSError:
            pass


def after_processed(mid: int, meta: dict):
    """標記完：網站帳號對到 Danbooru 登記的畫師 → 創作者候選（不建立新畫師）。"""
    site = ACCOUNT.get(meta.get("category") or "")
    h = meta.get("handle")
    if not (site and h):
        return
    from . import candidates, sigscan
    sigscan._ensure()
    artist = sigscan.resolve(site, h)
    if artist and not sigscan._is_character_account(mid, artist):
        with db.write_lock:
            candidates.add(mid, artist, "artist", candidates.T_DANBOORU, "gdl",
                           reason=f"{site_label(meta['category'])} 貼文作者 {h}")
            db.conn().commit()


# ---------- 安裝（使用者電腦：start.bat 只在啟動時裝，程式裡按一下就能裝） ----------
def install() -> dict:
    root = Path(__file__).resolve().parent.parent
    req = next((ln.strip() for ln in (root / "requirements.txt").read_text("utf-8").splitlines()
                if ln.strip().lower().startswith("gallery-dl")), "gallery-dl")
    uv = next((p for p in (root / "tools" / "uv.exe", root / "tools" / "bin" / "uv.exe", root / "tools" / "uv") if p.exists()), None)
    cmd = [str(uv), "pip", "install", "--python", sys.executable, req] if uv else [sys.executable, "-m", "pip", "install", req]
    env = {**os.environ}
    if uv:
        env.update({"UV_CACHE_DIR": str(root / "tools" / "cache"), "UV_LINK_MODE": "copy"})
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=600, env=env,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    import importlib
    importlib.invalidate_caches()
    ok = available()
    return {"ok": ok, "version": version(), "log": (r.stdout + r.stderr)[-1500:]}
