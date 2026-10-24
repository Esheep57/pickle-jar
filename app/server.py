"""FastAPI 伺服器：組合各路由 + 靜態前端。

安全：預設只綁 127.0.0.1。開啟「手機上傳」後才綁區網，但區網來的請求只能用 /m（上傳頁），且要先輸入 PIN；
本機請求也檢查 Host，擋掉 DNS rebinding（惡意網頁假裝成 localhost 來讀你的圖庫）。
"""
from __future__ import annotations

import ipaddress
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from . import candidates, db, importer, online, perf, pipeline

try:     # 原生程式庫（ONNX Runtime、OpenCV）讓程式直接崩潰時，至少留下 Python 堆疊：data/crash.log
    import faulthandler as _fh
    db.DATA_DIR.mkdir(parents=True, exist_ok=True)
    _fh.enable(file=open(db.DATA_DIR / "crash.log", "a", encoding="utf-8"), all_threads=True)
except Exception:  # noqa
    pass
from .api import steps as steps_api, sweep as sweep_api, albums as albums_api, ascii2d as ascii2d_api, upgrade as upgrade_api, work as work_api, gif as gif_api, dataset as dataset_api, drivebackup as drivebackup_api, imports, lan, library, net, review, saved, shelf, system, tags
from .matcher import matcher

STATIC = Path(__file__).resolve().parent.parent / "static"


@asynccontextmanager
async def lifespan(_app):
    _startup()
    yield


app = FastAPI(title="Pickle Jar", lifespan=lifespan)
for r in (library.router, tags.router, review.router, shelf.router, saved.router, net.router, system.router, lan.router,
          imports.router, albums_api.router, ascii2d_api.router, upgrade_api.router, work_api.router, sweep_api.router, steps_api.router, gif_api.router, dataset_api.router, drivebackup_api.router):
    app.include_router(r)

LOCAL_HOSTS = {"127.0.0.1", "localhost", "[::1]", "::1"}
NO_WRITE_POSTS = {"/api/media/share"}


def _is_loopback(host: str | None) -> bool:
    try:
        return ipaddress.ip_address(host or "").is_loopback
    except ValueError:
        return host in ("localhost", "testclient")


def _op_ctx(request: Request, path: str, ext_import: bool) -> dict:
    """操作紀錄用：這個請求是在哪一頁（前端送 X-Lib-View，網址編碼）、用鍵盤還是滑鼠（X-Lib-Via）做的。"""
    from urllib.parse import unquote
    view = unquote(request.headers.get("x-lib-view") or "")[:80] or None
    via = (request.headers.get("x-lib-via") or "")[:10] or None
    if ext_import:
        via, view = "script", view or "瀏覽器腳本"
    elif path.startswith("/api/m/"):
        view = view or "手機"
    return {"view": view, "via": via}


@app.middleware("http")
async def guard(request: Request, call_next):
    client = request.client.host if request.client else None
    path = request.url.path
    # 擋跨站請求（CSRF）：別的網站不能叫你的瀏覽器對這個程式送出會改資料的請求
    ext_import = (path in ("/api/fetch-url/ext", "/api/fetch-url/ext/seen") or path.startswith("/api/ascii2d/ext/") or path.startswith("/api/sweep/ext/")) and request.method == "POST" \
        and importer.check_key(request.headers.get("x-import-key"))
    db.OP_CTX.set(_op_ctx(request, path, ext_import))
    # 讀取快取（b97）：瀏覽器腳本送來的匯入、ascii2d、畫師比對算「背景寫入」（不打斷快取，5 秒內沿用）；
    # 分享（複製到剪貼簿）、在檔案總管顯示這些不改資料庫的 POST 不算
    wrote = request.method not in ("GET", "HEAD", "OPTIONS") and not ext_import and path not in NO_WRITE_POSTS \
        and not path.endswith("/reveal")
    if ext_import:
        db.bg_wrote()
    if wrote:
        db.user_wrote()          # 讀取快取：你改了東西 → 側欄、首頁…下次一定重算（b96；回應後再一次，免得中途的讀取存到舊的）
    if request.method not in ("GET", "HEAD", "OPTIONS") and not ext_import:   # 瀏覽器按鈕：只有這個網址、而且配對金鑰正確才放行
        origin = request.headers.get("origin")
        if request.headers.get("sec-fetch-site") == "cross-site" or \
                (origin and origin != "null" and origin.split("://", 1)[-1] != request.headers.get("host", "")):
            return JSONResponse({"detail": "拒絕來自其他網站的請求"}, 403)
    if _is_loopback(client):
        host = (request.headers.get("host") or "").rsplit(":", 1)[0]
        if host and host not in LOCAL_HOSTS and host != "testserver":
            return JSONResponse({"detail": "只接受 localhost 存取"}, 403)
        pd, t0 = perf.begin(), time.perf_counter()
        try:
            resp = await call_next(request)
        except Exception as e:   # 出錯的請求記下來（系統狀態頁看得到），畫面上顯示看得懂的訊息
            from . import errlog
            errlog.record(request.method, path, e)
            return JSONResponse({"detail": f"伺服器出錯了：{type(e).__name__}（已記在「系統狀態」）"}, 500)
        finally:
            if wrote:
                db.user_wrote()
        if not path.startswith(("/static/", "/api/events")):     # 回應速度（app/perf.py；SSE 是長連線，不算）
            route = getattr(request.scope.get("route"), "path", None) or path
            perf.record(request.method, route, path, resp.status_code, (time.perf_counter() - t0) * 1000, pd,
                        (db.OP_CTX.get() or {}).get("view"))
        if path.startswith("/static/"):
            resp.headers["Cache-Control"] = "no-cache"  # 更新程式後瀏覽器一定拿到新版（沒改的檔案仍走 304）
        return resp
    # 區網（手機、平板）：上傳頁 /m 隨時可以開；輸入 PIN 以後可以「看」圖庫（只讀），改東西一律在電腦上做
    if not lan.enabled():
        return JSONResponse({"detail": "沒有開放區網連線"}, 403)
    if path == "/m" or path.startswith("/m/") or path.startswith("/api/m/"):
        return await call_next(request)
    if not lan._authed(request):
        if request.method == "GET" and (path in ("/", "/index.html") or path.startswith("/static/")):
            return RedirectResponse("/m", 302)          # 先輸入 PIN
        return JSONResponse({"detail": "請先在 /m 輸入 PIN"}, 401)
    if path == "/":
        return RedirectResponse("/static/index.html#/all", 302)
    if request.method not in ("GET", "HEAD") or not lan_readable(path):
        return JSONResponse({"detail": "手機／平板上只能看圖和上傳；整理請在電腦上做"}, 403)
    return await call_next(request)


# 區網可以讀的：看圖需要的就好。設定（有金鑰）、備份、匯出、資料夾瀏覽、瀏覽器腳本（有配對金鑰）、PIN 都不給
LAN_READ_PREFIX = ("/static/", "/t/", "/thumb/", "/file/", "/api/media", "/api/tag/", "/api/tags", "/api/characters", "/api/shelf",
                   "/api/series/", "/api/albums", "/api/sidebar", "/api/status", "/api/quicktags", "/api/saved", "/api/nsfw-ids",
                   "/api/query/", "/api/pixiv-work/", "/api/upgrade/", "/api/settings")


def lan_readable(path: str) -> bool:
    return path.startswith(LAN_READ_PREFIX) and not path.startswith(("/api/upgrade/summary",))


def _startup():
    from . import errlog
    errlog.install()
    db.init()
    from . import tasks
    tasks.on_startup()        # 上次還在跑的背景工作 → 中斷了（b93）
    db.BUSY = lambda: bool(pipeline.get_jobs().state.get("running") or online.get_jobs().state.get("running")
                           or tasks.live_progress())
    jobs = pipeline.get_jobs()
    jobs.restart_watch()
    online.get_jobs()

    def _prep():  # 舊資料：補媒材、當天備份、各種補查。每一步失敗都記在「系統狀態 › 程式出錯」，不影響下一步
        from . import errlog
        with errlog.bg("啟動：補媒材"):
            candidates.backfill_media_types()
        with errlog.bg("啟動：媒材分類"):        # b77：用你標的媒材學（起始標註＋你按 M 標的）
            from . import mediaclf
            mediaclf.load_seed()
            mediaclf.run()
        with errlog.bg("啟動：每日備份"):
            db.backup()
        with errlog.bg("啟動：合併同一個檔案的兩筆紀錄"):   # b98：只差副檔名大小寫（Windows 上是同一個檔案）
            pipeline.merge_case_twins()
        with errlog.bg("啟動：ascii2d 佇列整理"):   # 已經不用找的拿掉（b75、b95）
            from . import ascii2d as _a2d
            _a2d.prune()
        with errlog.bg("啟動：媒材 CLIP 接著做"):     # b78：還沒算 CLIP 特徵的圖（第一次會算整個圖庫）
            from . import clipemb
            clipemb.check_crash()                     # b79：上次用 GPU 時崩潰 → 改用 CPU
            if db.get_settings().get("media_clip", True):
                pipeline.get_jobs().submit("clip")
        with errlog.bg("啟動：PixAI 接著做"):
            if db.get_settings().get("pixai_enabled"):
                pipeline.get_jobs().submit("pixai")
        with errlog.bg("啟動：未登記畫師重查"):   # Danbooru 還沒登記的畫師：看看登記了沒（每個 7 天查一次）
            from . import siteartists
            siteartists.recheck()
        with errlog.bg("啟動：補查資料卡"):      # 還沒查過 Danbooru 資料卡的角色／作品
            online.get_jobs().queue_missing_taginfo()
        with errlog.bg("啟動：中文名（Bangumi）"):   # 還沒查過的角色／作品排進背景；造型用本體的名字
            from . import zhname
            zhname.queue_on_start()
        with errlog.bg("啟動：標籤蘊含"):
            from . import implies
            implies.schedule(delay=90)        # 還沒問過 Danbooru 的角色（第一次約 3,000 個、一次 40 個；之後只問新的）
            implies.sync_copyrights()         # 角色 ⇒ 作品照資料卡（b93，不連網）
        with errlog.bg("啟動：快速標籤學習"):
            from . import qtlearn
            qtlearn.schedule(delay=180)       # 啟動 3 分鐘後（不跟啟動的其他整理搶）
        with errlog.bg("啟動：多人圖人數"):
            from . import people
            people.refresh_bases()
            people.refresh()
        with errlog.bg("啟動：未識別分堆"):
            from . import piles
            piles.refresh()
        with errlog.bg("啟動：新角色辨識"):
            from . import charknn
            charknn.run()
        with errlog.bg("啟動：作者不可考標籤"):   # 梗圖、表情包、截圖
            from . import quicktags
            quicktags.sync_no_artist()
        with errlog.bg("啟動：梗圖、截圖不留角色候選"):   # 設定 no_char_tags；以前加標籤前就有的候選
            from . import quicktags
            with db.write_lock:
                quicktags.purge_candidates(db.conn())
        with errlog.bg("啟動：AI 生成判斷"):      # ComfyUI、Stable Diffusion…：讀檔頭，只檢查還沒檢查過的
            from . import aigen
            aigen.refresh()
            aigen.resuggest_all()
        with errlog.bg("啟動：定期整理"):          # 孤兒紀錄、轉檔快取上限、備份暫存檔（之後每 6 小時一次）
            from . import maintenance
            maintenance.periodic()
        with errlog.bg("啟動：整理 WAL"):          # 把 WAL 寫回主檔、截短（WAL 太大讀取會變慢）
            with db.write_lock:
                db.conn().execute("PRAGMA wal_checkpoint(TRUNCATE)")
    threading.Thread(target=_prep, daemon=True).start()
    online.periodic_research()
    if db.get_settings()["roots"]:
        jobs.submit("scan")
    else:
        threading.Thread(target=matcher.build, daemon=True).start()


@app.get("/")
def index():
    return RedirectResponse("/static/index.html")


app.mount("/static", StaticFiles(directory=STATIC), name="static")
