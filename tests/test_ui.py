"""畫面冒煙測試：用真的瀏覽器（無視窗）把每一頁打開一遍，任何 JavaScript 錯誤或「載入失敗」都算失敗。

語法檢查抓不到「變數沒定義」這種錯（例如 series is not defined），只有真的跑起來才知道。
需要 Playwright（在 requirements-dev.txt）：pip install -r requirements-dev.txt && python -m playwright install chromium
沒裝會**失敗**，不是跳過：以前自動跳過，新電腦上這 9 個防呆測試會靜靜消失、結果照樣全綠。
真的沒辦法裝瀏覽器時，明確設定 LIB_ALLOW_NO_UI=1 才跳過。
"""
import socket
import threading
import time

import pytest

import os

_ALLOW_SKIP = os.environ.get("LIB_ALLOW_NO_UI") == "1"


def _missing(why: str):
    if _ALLOW_SKIP:
        pytest.skip(why)
    pytest.fail(f"畫面測試跑不起來：{why}\n（安裝：pip install -r requirements-dev.txt && python -m playwright install chromium；"
                "真的不能裝時設 LIB_ALLOW_NO_UI=1 明確跳過）", pytrace=False)


try:
    import playwright.sync_api as pw
except ImportError:  # 模組層級不能 fail：改成每個測試開頭檢查
    pw = None

PAGES = ["home", "settings", "review", "unrecognized", "unrecognized/character", "unrecognized/artist", "all", "videos", "nsfw",
         "duplicates", "tags", "excluded", "errors", "characters", "artists", "tag/hatsune_miku", "tag/no_such_tag",
         "series/vocaloid", "search/hatsune_miku", "search/zzz_nothing", "unrecognized/people", "albums", "album/999", "official", "system"]

# 開頁前先記下所有提示訊息（提示幾秒後就消失，最後再查會漏掉）
WATCH = """window.__toasts = [];
new MutationObserver(ms => ms.forEach(m => m.addedNodes.forEach(n => n.classList && n.classList.contains('toast') && window.__toasts.push(n.textContent))))
  .observe(document.getElementById('toasts') || document.body, {childList: true, subtree: true});"""


def _open(page, base, view):
    page.goto(base + "/static/index.html#/" + view)
    page.wait_for_load_state("networkidle")
    page.evaluate(WATCH)        # 頁面載入後才掛上（太早掛會找不到提示區）


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


@pytest.fixture(scope="module")
def base(client):
    if pw is None:
        _missing("沒有安裝 Playwright")
    import uvicorn
    from app.server import app
    port = _free_port()
    srv = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", lifespan="off"))
    threading.Thread(target=srv.run, daemon=True).start()
    for _ in range(100):
        if srv.started:
            break
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}"
    srv.should_exit = True


def _browser(p):
    try:
        return p.chromium.launch()
    except Exception as e:  # noqa
        _missing(f"沒有可用的 Chromium：{e}")


def _problems(page, errors):
    toasts = page.evaluate("window.__toasts || []")
    return errors + [f"提示：{t}" for t in toasts if "失敗" in t or "錯誤" in t or "not defined" in t]


def test_every_page_opens_without_errors(base):
    bad = {}
    with pw.sync_playwright() as p:
        b = _browser(p)
        page = b.new_page()
        errors: list = []
        page.on("pageerror", lambda e: errors.append(f"JS 錯誤：{e}"))
        _open(page, base, "home")
        for view in PAGES:
            errors.clear()
            page.evaluate("window.__toasts = []")
            page.evaluate(f"location.hash = '#/{view}'")
            page.wait_for_timeout(300)
            page.wait_for_load_state("networkidle")
            page.wait_for_timeout(150)
            found = _problems(page, errors)
            if found:
                bad[view] = found
        b.close()
    assert not bad, f"這些頁面出錯：{bad}"


def test_filtered_empty_state(base):
    """篩選把東西全擋住時：要列出篩選、能拿掉，拿掉後圖回來。"""
    with pw.sync_playwright() as p:
        b = _browser(p)
        page = b.new_page()
        errors: list = []
        page.on("pageerror", lambda e: errors.append(f"JS 錯誤：{e}"))
        _open(page, base, "all")
        page.evaluate("lib.S.nsfw = 'show'; lib.S.mtype = 'cosplay'; lib.render()")
        page.wait_for_selector(".filt-empty", timeout=5000)
        page.wait_for_selector("#emp-n b", timeout=5000)          # 「拿掉篩選後有 N 筆」
        page.click(".filt-empty [data-fx='0']")
        page.wait_for_selector("#grid .cell", timeout=5000)
        assert not _problems(page, errors)
        b.close()


def test_shelf_series_tab_and_cover_picker(base, client):
    from tests.conftest import ids_by_name
    two = sorted(ids_by_name(client).values())[:2]      # 自己準備一部作品，不靠其他測試留下的資料
    client.post("/api/tags/apply", json={"ids": two, "action": "add", "tag": "ui_(uitest)", "category": "character"})
    client.post("/api/tag/ui_(uitest)/info", json={"copyright": "uitest_series", "other_names": [], "summary": ""})
    with pw.sync_playwright() as p:
        b = _browser(p)
        page = b.new_page()
        errors: list = []
        page.on("pageerror", lambda e: errors.append(f"JS 錯誤：{e}"))
        _open(page, base, "characters")
        page.click("[data-tab='series-all']")
        page.wait_for_selector(".stiles .stile")
        page.click("[data-ssort='name']")
        page.wait_for_selector("[data-ssort='name'].on")
        page.hover(".stile [data-cover-pick]")
        page.click(".stile [data-cover-pick]")
        page.wait_for_selector(".cpick img", timeout=5000)
        page.click(".cpick img")
        page.wait_for_selector(".stk-cover:has-text('自選封面')", state="attached", timeout=5000)
        assert not _problems(page, errors)
        b.close()


def test_character_cover_picker(base, client):
    from tests.conftest import ids_by_name
    ids = sorted(ids_by_name(client).values())[4:6]
    client.post("/api/tags/apply", json={"ids": ids, "action": "add", "tag": "uicov_(uitest)", "category": "character"})
    with pw.sync_playwright() as p:
        b = _browser(p)
        page = b.new_page()
        errors: list = []
        page.on("pageerror", lambda e: errors.append(f"JS 錯誤：{e}"))
        _open(page, base, "tag/uicov_(uitest)")
        page.click('[data-pmenu="tag-menu"]')
        page.click("#cover-btn")
        page.wait_for_selector(".cpick img", timeout=5000)
        assert page.locator(".cpick img").count() == 2      # 只列這個角色確認過的圖
        page.click(".cpick img >> nth=1")
        page.wait_for_selector("#cover-btn:has-text('自選')", state="attached", timeout=5000)
        assert not _problems(page, errors)
        b.close()


def test_layout_regressions(base, client):
    """排版防呆：待確認卡片的字不能超出卡片、NSFW 標記不能蓋到選取圈；創作者的 ⋯ 選單要顯示「創作者」（以前會顯示成角色，按儲存就被改掉）。"""
    from tests.conftest import ids_by_name
    from app import db, candidates
    ids = sorted(ids_by_name(client).values())
    long = "hoshikawa_sara_(really_long_costume_name)_(nijisanji)"
    client.post("/api/tags/apply", json={"ids": ids[6:8], "action": "add", "tag": "layout_artist_with_long_handle", "category": "artist"})
    with db.write_lock:
        candidates.add(ids[8], long, "character", 2, "signature", reason="圖上的簽名：a_very_long_twitter_handle_here")
        db.conn().execute("UPDATE media SET nsfw_manual=1 WHERE id=?", (ids[8],))
    with pw.sync_playwright() as p:
        b = _browser(p)
        page = b.new_page(viewport={"width": 1280, "height": 900})
        errors: list = []
        page.on("pageerror", lambda e: errors.append(f"JS 錯誤：{e}"))
        _open(page, base, "review")
        page.evaluate("lib.S.nsfw='show'; lib.S.rmode='grid'; lib.S.rtier='source'; lib.S.rsrc=''; lib.S.rtag=''; lib.S.rcat='all'; lib.render()")
        page.wait_for_selector(".cell.rc .rcap")
        bad = page.evaluate("""() => [...document.querySelectorAll('.cell.rc')].flatMap(c => {
            const r = c.getBoundingClientRect(), out = [];
            c.querySelectorAll('.rcap *').forEach(e => { const q = e.getBoundingClientRect(); if (q.width && (q.right > r.right + 1 || q.bottom > r.bottom + 1)) out.push('超出：' + e.textContent.slice(0, 30)); });
            const sel = c.querySelector('.sel')?.getBoundingClientRect();
            if (c.classList.contains('nsfw') && sel) { const b = getComputedStyle(c, '::before'); const left = r.left + parseFloat(b.left); if (left < sel.right) out.push('NSFW 蓋到選取圈'); }
            return out; })""")
        assert not bad, bad
        page.evaluate("location.hash = '#/tag/layout_artist_with_long_handle'")
        page.wait_for_selector('[data-pmenu="tag-menu"]')
        page.click('[data-pmenu="tag-menu"]')
        page.click("#tagmenu-btn")
        assert page.eval_on_selector("#tm-cat", "e => e.value") == "artist"
        assert not _problems(page, errors)
        b.close()


def test_loading_never_stalls_and_select_all(base, client):
    """一批只載 2 張（?page=1）時：畫面不用捲也要自動載完；「全選」要選到全部，不是只有已載入的。"""
    total = client.get("/api/media", params={"view": "all", "nsfw": "show", "limit": 1, "noalbum": 1}).json()["total"]   # 圖庫不放作品集的頁
    assert total > 6
    with pw.sync_playwright() as p:
        b = _browser(p)
        page = b.new_page(viewport={"width": 1600, "height": 2400})
        errors: list = []
        page.on("pageerror", lambda e: errors.append(f"JS 錯誤：{e}"))
        page.goto(base + "/static/index.html?page=1#/all")
        page.wait_for_load_state("networkidle")
        page.evaluate("lib.S.nsfw='show'; lib.render()")
        page.wait_for_function(f"lib.S.items.length === {total}", timeout=10000)   # 以前會停在第一批
        page.evaluate("lib.render()")
        page.wait_for_function("lib.S.items.length > 0")
        page.evaluate("lib.S.done = false")
        page.evaluate("lib.selectAll()")
        page.wait_for_function(f"lib.S.selected.size === {total}", timeout=10000)
        assert not errors, errors
        b.close()


def test_tag_picker(base, client):
    """加標籤：還沒打字時不列 1girl 這種描述（列候選、同作品角色）；可以換分類；Shift+Enter 連續加。"""
    from tests.conftest import ids_by_name
    ids = sorted(ids_by_name(client).values())
    mid = ids[0]
    client.post("/api/tags/apply", json={"ids": ids[1:3], "action": "add", "tag": "picker_target_(tst)", "category": "character"})
    with pw.sync_playwright() as p:
        b = _browser(p)
        page = b.new_page(viewport={"width": 1400, "height": 900})
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        _open(page, base, "all")
        page.evaluate(f"lib.pickTag({{title: 't', category: 'character', ids: [{mid}]}}, t => window.__picked = (window.__picked || []).concat(t.name))")
        page.wait_for_selector(".picker2 .opts")
        page.wait_for_timeout(400)
        txt = page.inner_text(".picker2 .opts")
        assert "1girl" not in txt and "gtag_" not in txt, txt
        assert page.inner_text(".pk-cats button.on") == "角色"
        page.keyboard.press("Tab")
        assert page.inner_text(".pk-cats button.on") == "創作者"
        page.keyboard.press("Shift+Tab")
        page.fill(".pk-inbox input", "brand_new_char_(tst)")
        page.wait_for_timeout(400)
        assert "建立成新的「角色」" in page.inner_text(".picker2 .opts")
        page.keyboard.press("Shift+Enter")
        page.wait_for_timeout(300)
        assert page.is_visible(".picker2"), "Shift+Enter 之後選擇器還開著，可以繼續加"
        assert page.evaluate("window.__picked") == ["brand_new_char_(tst)"]
        page.fill(".pk-inbox input", "picker_tar")
        page.wait_for_timeout(400)
        page.keyboard.press("Enter")
        page.wait_for_timeout(200)
        assert not page.is_visible(".picker2")
        assert page.evaluate("window.__picked")[-1] == "picker_target_(tst)"
        assert not errors, errors
        b.close()


def test_quick_tag_keys(base, client):
    """快速標籤：不常駐在側欄（v4）；大圖裡按 1 加上「梗圖」（畫面中央閃一下、詳細裡的按鈕變成已加上），再按一次拿掉。"""
    from app import db
    from tests.conftest import ids_by_name
    mid = sorted(ids_by_name(client).values())[4]
    with pw.sync_playwright() as p:
        b = _browser(p)
        page = b.new_page(viewport={"width": 1400, "height": 900})
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        _open(page, base, "all")
        page.wait_for_function("document.querySelectorAll('#quick a').length > 0")
        assert not page.is_visible("#quick"), "快速標籤不該常駐在側欄"
        page.click("#qin")                    # 底部搜尋列的面板：快速標籤一排，點一下到標籤頁
        page.click('#facets [data-qtag="梗圖"]')
        page.wait_for_function("location.hash.includes('/tag/')")
        _open(page, base, "all")
        page.evaluate(f"lib.openViewer({mid})")
        page.wait_for_function("document.querySelector('#viewer .qrow .qt') !== null")
        page.keyboard.press("1")
        page.wait_for_selector("#hud.on")
        assert "梗圖" in page.inner_text("#hud")
        page.wait_for_function("document.querySelector('#viewer .qrow .qt.on') !== null")
        tid = db.tag_id("梗圖", create=False)
        assert db.conn().execute("SELECT state FROM media_tags WHERE media_id=? AND tag_id=?", (mid, tid)).fetchone()[0] == 1
        page.keyboard.press("1")
        page.wait_for_function("document.querySelector('#viewer .qrow .qt.on') === null")
        assert not db.conn().execute("SELECT 1 FROM media_tags WHERE media_id=? AND tag_id=?", (mid, tid)).fetchone()
        assert not errors, errors
        b.close()


def test_action_bar_matches_page(base, client):
    """每一頁選一張圖：選取列要是這一頁的按鈕（待確認頁是「是／不是／略過」），不能留著上一頁的
    （使用者回報：待確認頁問「是不是奶龍」，選取列卻是上一個角色頁的「不是 kazemaru_ichirouta」）。"""
    from app import candidates
    from tests.conftest import ids_by_name
    ids = sorted(ids_by_name(client).values())
    client.post("/api/tags/apply", json={"ids": ids[:2], "action": "add", "tag": "bar_char_(tst)", "category": "character"})
    from app import db
    with db.write_lock:     # 沒包在鎖裡會留著沒 commit 的交易，後面的請求全部卡住
        candidates.add(ids[3], "bar_cand_(tst)", "character", candidates.T_AI, "model", score=0.9)
    with pw.sync_playwright() as p:
        b = _browser(p)
        page = b.new_page(viewport={"width": 1400, "height": 900})
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on("console", lambda m: m.type == "error" and errors.append(m.text))
        for view, want, not_want in [("tag/bar_char_(tst)", "移除「bar_char_(tst)」", None), ("review", "是", "bar_char_(tst)"),
                                     ("all", "＋角色", "bar_char_(tst)"), ("unrecognized", "不用整理", "bar_char_(tst)"),
                                     ("series/vocaloid", "＋角色", "bar_char_(tst)"), ("official", None, "bar_char_(tst)")]:
            page.goto(base + "/static/index.html#/" + view, wait_until="domcontentloaded")
            page.wait_for_timeout(1500)
            has = page.evaluate("lib.S.items.length")
            if not has:
                continue
            page.evaluate("lib.setSel(lib.S.view === 'review' ? 0 : lib.S.items[0].id, true)")
            txt = page.inner_text("#actionbar")
            assert "已選取" in txt, (view, txt)
            if want:
                assert want in txt, (view, txt)
            if not_want:
                assert not_want not in txt, (view, txt)
            page.evaluate("lib.clearSel()")
        assert not [e for e in errors if "Failed to load resource" not in e], errors
        b.close()


A2D_HOME = """<!doctype html><html><head><title>二次元画像詳細検索</title></head><body>
<form action="/search/file" method="post" enctype="multipart/form-data"><input type="hidden" name="utf8" value="✓">
<input type="file" name="file"><input type="submit" name="search"></form></body></html>"""
# 仿 ascii2d 結果頁的結構（第一格是你上傳的圖，後面是結果）
A2D_RESULT = """<!doctype html><html><head><title>二次元画像詳細検索</title></head><body>
<form action="/search/file" method="post" enctype="multipart/form-data"><input type="file" name="file"></form>
<div class="row item-box"><div class="image-box"><img src="/thumbnail/q.jpg"></div><div class="info-box"><div class="hash">abc</div></div></div>
<div class="row item-box"><div class="image-box"><img src="/thumbnail/r.jpg"></div><div class="info-box"><div class="detail-box gray-link">
<h6><a href="https://twitter.com/someart/status/1790000000000000001" target="_blank">新圖</a>
<a href="https://twitter.com/intent/user?user_id=1" target="_blank">Some Art</a><small>twitter</small></h6></div></div></div>
</body></html>"""
GM_SHIM = """window.GM_registerMenuCommand = () => {};
window.GM_xmlhttpRequest = o => { window.__gm({ method: o.method, url: o.url, headers: o.headers || {}, data: o.data || null })
  .then(r => { const resp = { status: r.status, responseText: r.text };
    if (o.responseType === 'blob') resp.response = new Blob([Uint8Array.from(atob(r.b64), c => c.charCodeAt(0))], { type: r.ctype });
    o.onload(resp); }).catch(() => o.onerror && o.onerror()); };"""


def test_userscript_ascii2d_panel(base, client):
    """油猴腳本的 ascii2d 模式（用假的 ascii2d 頁面，不連網）：Enter 送出下一張 → 結果頁送回、比對 → Enter 確認 → 找完。"""
    import base64
    import httpx
    from app import ascii2d, db
    from tests.conftest import LIB, MOCK_PORT, ids_by_name, wait_idle
    (LIB / "a2dui").mkdir(exist_ok=True)
    (LIB / "a2dui" / "IMG_9101.jpg").write_bytes(httpx.get(f"http://127.0.0.1:{MOCK_PORT}/media/AbCdEfGh12345Xy.jpg").content)
    client.post("/api/jobs", json={"kind": "scan"})
    wait_idle(client)
    mid = ids_by_name(client)["IMG_9101.jpg"]
    with db.write_lock:        # 只排這一張（前面測試排過的清掉）
        db.conn().execute("DELETE FROM ascii2d")
    ascii2d.start([mid])
    js = client.get("/api/fetch-url/userscript.user.js").text
    posted = []

    def gm(req):
        u = req["url"].replace("http://testserver", base)
        r = httpx.request(req["method"], u, headers=req["headers"], content=req["data"])
        return {"status": r.status_code, "text": r.text if "json" in r.headers.get("content-type", "") or r.status_code >= 400 else "",
                "b64": base64.b64encode(r.content).decode(), "ctype": r.headers.get("content-type", "")}

    def route(rt):
        req = rt.request
        if req.method == "POST" and req.url.endswith("/search/file"):
            posted.append(len(req.post_data_buffer or b""))
            # 真的 ascii2d 會 302 轉到 /search/color/<hash>；Playwright 攔不到轉址後的請求，改用頁面跳轉
            return rt.fulfill(status=200, content_type="text/html", body="<script>location.replace('/search/color/abc')</script>")
        if "/search/color/" in req.url:
            return rt.fulfill(status=200, content_type="text/html; charset=utf-8", body=A2D_RESULT)
        if "/thumbnail/" in req.url:
            return rt.fulfill(status=404, body="")
        return rt.fulfill(status=200, content_type="text/html; charset=utf-8", body=A2D_HOME)

    with pw.sync_playwright() as p:
        b = _browser(p)
        page = b.new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.expose_function("__gm", gm)
        page.add_init_script(GM_SHIM)
        page.add_init_script(js.split("// ==/UserScript==", 1)[1])
        import re as _re
        page.route(_re.compile(r"^https://ascii2d\.net(/.*)?$"), route)
        page.goto("https://ascii2d.net/")
        page.on("console", lambda m: errors.append("console:" + m.text) if m.type in ("error", "warning") else None)
        try:
            page.wait_for_function("document.documentElement.innerText.includes('開始 (Enter)')", timeout=10000)
        except Exception:
            raise AssertionError((page.evaluate("document.documentElement.innerText"), errors))
        page.keyboard.press("Enter")
        page.wait_for_url("**/search/color/abc", timeout=15000)
        page.wait_for_function("document.documentElement.innerText.includes('同一張')", timeout=20000)
        assert posted and posted[0] > 1000, "要真的把圖送出去"
        txt = page.inner_text("html")
        assert "X @someart" in txt and "確認勾選的 (Enter)" in txt, txt[-600:]
        page.keyboard.press("Enter")
        page.wait_for_function("document.documentElement.innerText.includes('全部找完了')", timeout=15000)
        assert not [e for e in errors if "Failed to load resource" not in e], errors
        b.close()
    row = db.conn().execute("""SELECT mt.state, mt.source FROM media_tags mt JOIN tags t ON t.id=mt.tag_id
        WHERE mt.media_id=? AND t.category='artist'""", (mid,)).fetchone()
    assert row and tuple(row) == (1, "ascii2d"), row
    ascii2d.stop()


def test_userscript_ascii2d_full_auto(base, client):
    """全自動（b80）：按一次開始以後不用再按任何鍵：上傳被退回首頁 → 自己再送；比對到同一張 → 自動確認、下一張 → 找完。"""
    import base64
    import httpx
    from app import ascii2d, db
    from tests.conftest import LIB, MOCK_PORT, ids_by_name, wait_idle
    (LIB / "a2dfull").mkdir(exist_ok=True)
    (LIB / "a2dfull" / "IMG_9201.jpg").write_bytes(httpx.get(f"http://127.0.0.1:{MOCK_PORT}/media/AbCdEfGh12345Xy.jpg").content)
    client.post("/api/jobs", json={"kind": "scan"})
    wait_idle(client)
    mid = ids_by_name(client)["IMG_9201.jpg"]
    with db.write_lock:
        db.conn().execute("DELETE FROM ascii2d")
        db.conn().execute("DELETE FROM media_tags WHERE media_id=? AND source='ascii2d'", (mid,))
    ascii2d.start([mid])
    client.post("/api/settings", json={"ascii2d_auto_accept": "full"})
    js = client.get("/api/fetch-url/userscript.user.js").text
    posted = []

    def gm(req):
        u = req["url"].replace("http://testserver", base)
        r = httpx.request(req["method"], u, headers=req["headers"], content=req["data"])
        return {"status": r.status_code, "text": r.text if "json" in r.headers.get("content-type", "") or r.status_code >= 400 else "",
                "b64": base64.b64encode(r.content).decode(), "ctype": r.headers.get("content-type", "")}

    def route(rt):
        req = rt.request
        if req.method == "POST" and req.url.endswith("/search/file"):
            posted.append(1)
            if len(posted) == 1:      # 第一次：ascii2d 把上傳退回首頁
                return rt.fulfill(status=200, content_type="text/html", body="<script>location.replace('/')</script>")
            return rt.fulfill(status=200, content_type="text/html", body="<script>location.replace('/search/color/abc')</script>")
        if "/search/color/" in req.url:
            return rt.fulfill(status=200, content_type="text/html; charset=utf-8", body=A2D_RESULT)
        if "/thumbnail/" in req.url:
            return rt.fulfill(status=404, body="")
        return rt.fulfill(status=200, content_type="text/html; charset=utf-8", body=A2D_HOME)

    try:
        with pw.sync_playwright() as p:
            b = _browser(p)
            page = b.new_page()
            errors = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.expose_function("__gm", gm)
            page.add_init_script(GM_SHIM)
            page.add_init_script(js.split("// ==/UserScript==", 1)[1])
            import re as _re
            page.route(_re.compile(r"^https://ascii2d\.net(/.*)?$"), route)
            page.goto("https://ascii2d.net/")
            page.wait_for_function("document.documentElement.innerText.includes('開始 (Enter)')", timeout=10000)
            page.keyboard.press("Enter")                     # 只按這一次
            page.wait_for_function("document.documentElement.innerText.includes('全部找完了')", timeout=40000)
            assert len(posted) == 2, posted
            assert not [e for e in errors if "Failed to load resource" not in e], errors
            b.close()
        row = db.conn().execute("""SELECT mt.state, mt.source FROM media_tags mt JOIN tags t ON t.id=mt.tag_id
            WHERE mt.media_id=? AND t.category='artist'""", (mid,)).fetchone()
        assert row and tuple(row) == (1, "ascii2d"), row
    finally:
        client.post("/api/settings", json={"ascii2d_auto_accept": "off"})
        ascii2d.stop()


def _full_auto_run(base, client, name, init, hang_after_decide):
    """全自動跑兩張；hang_after_decide＝第一次決定之後，下一個「要下一張」的請求完全不回應（模擬 Tampermonkey 卡住）。"""
    import base64
    import httpx
    from app import ascii2d, db
    from tests.conftest import LIB, MOCK_PORT, ids_by_name, wait_idle
    (LIB / name).mkdir(exist_ok=True)
    src = httpx.get(f"http://127.0.0.1:{MOCK_PORT}/media/AbCdEfGh12345Xy.jpg").content
    for k in (1, 2):
        (LIB / name / f"IMG_{name}_{k}.jpg").write_bytes(src + bytes([k]))
    client.post("/api/jobs", json={"kind": "scan"})
    wait_idle(client)
    ids = [ids_by_name(client)[f"IMG_{name}_{k}.jpg"] for k in (1, 2)]
    with db.write_lock:
        db.conn().execute("DELETE FROM ascii2d")
    ascii2d.start(ids)
    client.post("/api/settings", json={"ascii2d_auto_accept": "full"})
    js = client.get("/api/fetch-url/userscript.user.js").text
    st = {"decided": 0, "hung": 0}

    def gm(req):
        u = req["url"].replace("http://testserver", base)
        if "/ext/decide" in u:
            st["decided"] += 1
        if hang_after_decide and "/ext/next" in u and st["decided"] == 1 and not st["hung"]:
            st["hung"] = 1
            return None               # 不回應
        r = httpx.request(req["method"], u, headers=req["headers"], content=req["data"])
        return {"status": r.status_code, "text": r.text if "json" in r.headers.get("content-type", "") or r.status_code >= 400 else "",
                "b64": base64.b64encode(r.content).decode(), "ctype": r.headers.get("content-type", "")}

    def route(rt):
        req = rt.request
        if req.method == "POST" and req.url.endswith("/search/file"):
            return rt.fulfill(status=200, content_type="text/html", body="<script>location.replace('/search/color/abc')</script>")
        if "/search/color/" in req.url:
            return rt.fulfill(status=200, content_type="text/html; charset=utf-8", body=A2D_RESULT)
        if "/thumbnail/" in req.url:
            return rt.fulfill(status=404, body="")
        return rt.fulfill(status=200, content_type="text/html; charset=utf-8", body=A2D_HOME)

    shim = GM_SHIM.replace(".then(r => {", ".then(r => { if (r === null) return;")
    try:
        with pw.sync_playwright() as p:
            b = _browser(p)
            page = b.new_page()
            errors = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.expose_function("__gm", gm)
            page.add_init_script(init)
            page.add_init_script(shim)
            page.add_init_script(js.split("// ==/UserScript==", 1)[1])
            import re as _re
            page.route(_re.compile(r"^https://ascii2d\.net(/.*)?$"), route)
            page.goto("https://ascii2d.net/")
            page.wait_for_function("document.documentElement.innerText.includes('開始 (Enter)')", timeout=10000)
            page.keyboard.press("Enter")
            page.wait_for_function("document.documentElement.innerText.includes('全部找完了')", timeout=60000)
            assert not [e for e in errors if "Failed to load resource" not in e], errors
            b.close()
        assert st["hung"] == (1 if hang_after_decide else 0)
        got = dict(db.conn().execute(f"SELECT media_id, status FROM ascii2d WHERE media_id IN ({ids[0]},{ids[1]})").fetchall())
        assert set(got.values()) <= {"done", "none"} and len(got) == 2, got
    finally:
        client.post("/api/settings", json={"ascii2d_auto_accept": "off"})
        ascii2d.stop()


def test_userscript_full_auto_request_timeout(base, client):
    """腳本 3.0：請求完全沒回應 → 自己的逾時到了就重送，接著做完（2026-10-16 11:37 就是這樣停住的）。"""
    _full_auto_run(base, client, "a2dto", "window.__a2dReqTimeout = 1500; window.__a2dWatchdog = 600000;", True)


def test_userscript_full_auto_watchdog(base, client):
    """腳本 3.0：卡住、自己的逾時又很長 → 看門狗回首頁重新開始，接著做完。"""
    _full_auto_run(base, client, "a2ddog", "window.__a2dReqTimeout = 600000; window.__a2dWatchdog = 3000;", True)


def test_ascii2d_scope_modal(base, client):
    """ascii2d 選範圍的視窗：打開就算出張數；輸入搜尋條件會即時更新；選取的圖也可以直接用。"""
    with pw.sync_playwright() as p:
        b = _browser(p)
        page = b.new_page(viewport={"width": 1400, "height": 900})
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        _open(page, base, "system")
        page.click('[data-tool="ascii2d"]')
        page.wait_for_function("document.querySelector('#a2-pv') && document.querySelector('#a2-pv').innerText.includes('這次排')", timeout=10000)
        if page.query_selector("#a2-x"):      # 前面的測試匯入過 X 的圖：這裡只測搜尋條件
            page.uncheck("#a2-x")
        page.fill("#a2-q", "path:zzz_nothing_here")
        page.wait_for_function("document.querySelector('#a2-pv').innerText.includes('符合 0')", timeout=10000)
        assert page.is_disabled("#a2-go")
        page.fill("#a2-q", "")
        page.fill("#a2-lim", "2")
        page.wait_for_function("document.querySelector('#a2-go').innerText.includes('2 張')", timeout=10000)
        assert not [e for e in errors], errors
        b.close()


def test_names_modal(base, client):
    """中文名整理的視窗：三個分頁都打得開，沒有 JS 錯誤。"""
    with pw.sync_playwright() as p:
        b = _browser(p)
        page = b.new_page(viewport={"width": 1400, "height": 900})
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        _open(page, base, "characters")
        page.click("#shelf-more")
        page.click("#name-fix")
        page.wait_for_selector(".nmlist", timeout=10000)
        for k in ("multi", "done", "nozh"):
            page.click(f'.chiprow [data-k="{k}"]')
            page.wait_for_selector(f'.chiprow [data-k="{k}"].on', timeout=10000)
        assert not errors, errors
        b.close()


def test_review_live_knn_update(base, client):
    """「你的圖庫」推薦有變 → 待確認頁自己補上新的、拿掉收回的（不用重新整理；專注模式眼前那張不會被換掉）。"""
    from app import candidates, charknn, db
    from tests.conftest import ids_by_name
    # 挑三張待確認頁看得到的圖（不是 NSFW、沒被排除、沒略過）
    with db.write_lock:
        for m in sorted(ids_by_name(client).values()):
            candidates.add(m, "live_char_(tst)", "character", candidates.T_AI, "knn", score=0.5)
        db.conn().commit()
    vis = [it["id"] for it in client.get("/api/review", params={"tier": "library", "tag": "live_char_(tst)", "limit": 500}).json()["items"]]
    assert len(vis) >= 3, vis
    a, b, c3 = vis[:3]
    with db.write_lock:
        db.conn().execute("DELETE FROM media_tags WHERE source='knn' AND state=0 AND tag_id=(SELECT id FROM tags WHERE name='live_char_(tst)')")
        db.conn().commit()
    # 背景的真重算會把這裡手動放的推薦收掉（它們不在它算出來的清單裡）：先關掉
    client.post("/api/settings", json={"knn_enabled": False})
    if charknn._timer is not None:
        charknn._timer.cancel()
    with db.write_lock:
        candidates.add(a, "live_char_(tst)", "character", candidates.T_AI, "knn", score=0.9)
        candidates.add(b, "live_char_(tst)", "character", candidates.T_AI, "knn", score=0.8)
        db.conn().commit()
    with pw.sync_playwright() as p:
        br = _browser(p)
        page = br.new_page(viewport={"width": 1400, "height": 900})
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        for mode in ("grid", "focus"):
            page.goto(base + "/static/index.html#/home", wait_until="domcontentloaded")
            page.evaluate(f"""localStorage.setItem('lib:rmode', '"{mode}"'); localStorage.setItem('lib:rtier', '"library"');
                              localStorage.setItem('lib:rtag', '"live_char_(tst)"')""")
            page.goto(base + "/static/index.html#/review", wait_until="domcontentloaded")
            page.wait_for_timeout(1200)
            if mode == "focus":
                page.evaluate("lib.S.ftag = 'live_char_(tst)'; lib.render()")
                page.wait_for_timeout(800)
            page.evaluate(WATCH)
            live = lambda: page.evaluate("lib.S.items.filter(x => x.tag === 'live_char_(tst)' && (lib.S.rmode === 'focus' || document.querySelector(`.cell[data-id='${lib.S.items.indexOf(x)}']`))).map(x => x.id)")
            before = live()
            assert set(before) >= {a, b}, (mode, before)
            cur = page.evaluate("lib.S.items[0] && lib.S.items[0].id") if mode == "focus" else None
            with db.write_lock:     # 後端重算：多一筆、收回一筆
                c = db.conn()
                gone = b if cur != b else a
                c.execute("DELETE FROM media_tags WHERE media_id=? AND source='knn' AND tag_id=(SELECT id FROM tags WHERE name='live_char_(tst)')", (gone,))
                candidates.add(c3, "live_char_(tst)", "character", candidates.T_AI, "knn", score=0.7)
                c.commit()
            charknn._state["version"] += 1
            page.wait_for_function("(window.__toasts || []).some(t => t.includes('你的圖庫推薦更新'))", timeout=12000)
            after = live()
            assert c3 in after and gone not in after, (mode, before, after)
            if mode == "focus":
                assert page.evaluate("lib.S.items[0].id") == cur or cur == gone, "眼前這張不會被換掉"
            with db.write_lock:     # 復原給下一輪
                c = db.conn()
                c.execute("DELETE FROM media_tags WHERE media_id=? AND source='knn' AND tag_id=(SELECT id FROM tags WHERE name='live_char_(tst)')", (c3,))
                candidates.add(gone, "live_char_(tst)", "character", candidates.T_AI, "knn", score=0.8)
                c.commit()
        assert not errors, errors
        br.close()
    client.post("/api/settings", json={"knn_enabled": True})


def test_viewer_close_keeps_scroll(base, client):
    """大圖裡改了標籤、這張不再符合目前的檢索 → 關掉大圖時原地更新：拿掉它，捲動位置留在原地（以前整頁重畫回到最上面）。"""
    with pw.sync_playwright() as p:
        br = _browser(p)
        page = br.new_page(viewport={"width": 900, "height": 420})
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(base + "/static/index.html#/home", wait_until="domcontentloaded")
        page.evaluate("localStorage.setItem('lib:size', '90')")
        page.goto(base + "/static/index.html#/unrecognized", wait_until="domcontentloaded")
        page.wait_for_timeout(1500)
        n = page.evaluate("lib.S.items.length")
        assert n >= 10, n
        info = page.evaluate("""() => { const c = document.querySelector('#content'); c.scrollTop = Math.min(400, c.scrollHeight - c.clientHeight);
            return new Promise(r => setTimeout(() => { const g = document.querySelector('#grid');
              const cells = [...document.querySelectorAll('#grid .cell')].filter(e => { const b = e.getBoundingClientRect(); return b.top > 120 && b.bottom < 400; });
              const id = +cells[0].dataset.id; const ids = lib.S.items.map(x => x.id);
              r({ top: c.scrollTop, id, next: ids[ids.indexOf(id) + 1], y: cells[0].getBoundingClientRect().top }); }, 300)); }""")
        assert info["top"] > 50, info
        page.evaluate(f"lib.openViewer({info['id']})")
        page.wait_for_timeout(500)
        client.post("/api/tags/apply", json={"ids": [info["id"]], "action": "add", "tag": "keep_scroll_(tst)", "category": "character"})
        page.evaluate("import('/static/js/viewer.js').then(m => { m.markDirty(); m.closeViewer(); })")
        page.wait_for_timeout(1500)
        after = page.evaluate(f"""() => {{ const c = document.querySelector('#content'); const el = document.querySelector('#grid .cell[data-id="{info['next']}"]');
            return {{ top: c.scrollTop, gone: !lib.S.items.some(x => x.id === {info['id']}), y: el ? el.getBoundingClientRect().top : null }}; }}""")
        assert after["gone"] and after["top"] > 50, (info, after)
        assert after["y"] is not None and abs(after["y"] - info["y"]) < 150, ("原本後面那張還在畫面上差不多的位置", info, after)
        assert not errors, errors
        br.close()


def test_upgrade_viewer_and_list(base, client):
    """換成高清版：大圖上方的「↑ 高清」、詳細裡的換／不用、貼網址；is:upgrade 清單的選取列預設是「換成高清」。"""
    import json as _json
    from app import db
    from tests.conftest import ids_by_name
    ids = ids_by_name(client)
    mid = ids["img_03.jpg"]
    with db.write_lock:
        db.conn().execute("""INSERT OR REPLACE INTO upgrade(media_id, status, site, post_url, width, height, cands, local_w, local_h, checked_at)
                             VALUES(?, 'offer', 'danbooru', 'https://danbooru.donmai.us/posts/1', 800, 1120, ?, 400, 560, ?)""",
                          (mid, _json.dumps([{"site": "danbooru", "post_url": "https://danbooru.donmai.us/posts/1", "url": None, "w": 800, "h": 1120}]), time.time()))
    try:
        with pw.sync_playwright() as p:
            br = _browser(p)
            page = br.new_page()
            errors = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            _open(page, base, "search/is:upgrade")
            page.wait_for_timeout(800)
            assert mid in page.evaluate("lib.S.items.map(x => x.id)")
            page.evaluate(f"lib.setSel({mid}, true)")
            page.wait_for_timeout(300)
            bar = page.inner_text("#actionbar")
            assert "換成高清" in bar and "不用換高清" in bar, bar
            page.evaluate(f"lib.openViewer({mid})")
            page.wait_for_selector("#v-uppill", timeout=5000)
            page.click("#v-uppill")
            page.wait_for_timeout(300)
            assert page.evaluate("document.querySelector('#viewer').classList.contains('drawer')")
            box = page.inner_text(".upbox.offer")
            assert "800×1120" in box and "Danbooru" in box, box
            # 貼網址：不支援的網站 → 說明（不是錯誤畫面）
            page.evaluate("document.querySelector('#v-upurl').open = true")
            page.fill("#v-up-url", "https://example.com/x.png")
            page.click("#v-up-chk")
            page.wait_for_timeout(800)
            assert "只接受" in page.inner_text("#v-up-msg")
            done = ids.get("IMG_7001.png") or ids.get("IMG_7001.jpg")
            if done:          # 換成高清的那張（test_library4_upgrade）：顯示原本多大
                page.evaluate(f"lib.openViewer({done})")
                page.wait_for_timeout(800)
                assert "原本 400×560" in page.inner_text(".upbox.done")
            assert not errors, errors
            br.close()
    finally:
        with db.write_lock:
            db.conn().execute("UPDATE upgrade SET status='dismissed' WHERE media_id=?", (mid,))


def test_back_restores_scroll(base, client):
    """從圖庫捲到下面、點進別頁、按返回：回到原本的位置（以前回到最上面）。"""
    with pw.sync_playwright() as p:
        br = _browser(p)
        page = br.new_page(viewport={"width": 900, "height": 420})
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(base + "/static/index.html#/home", wait_until="domcontentloaded")
        page.evaluate("localStorage.setItem('lib:size', '90')")
        page.goto(base + "/static/index.html#/all", wait_until="domcontentloaded")
        page.wait_for_timeout(1500)
        before = page.evaluate("""() => new Promise(r => { const c = document.querySelector('#content');
            c.scrollTop = Math.min(500, c.scrollHeight - c.clientHeight);
            setTimeout(() => { const cells = [...document.querySelectorAll('#grid .cell')].filter(e => e.getBoundingClientRect().top > 60);
              cells.sort((a, b) => a.getBoundingClientRect().top - b.getBoundingClientRect().top);
              r({ top: c.scrollTop, id: cells[0] && cells[0].dataset.id, y: cells[0] && cells[0].getBoundingClientRect().top }); }, 400); })""")
        assert before["top"] > 50, before
        page.evaluate("location.hash = '#/characters'")
        page.wait_for_timeout(1200)
        assert page.evaluate("document.querySelector('#content').scrollTop") < 5, "新的一頁從最上面開始"
        page.go_back()
        page.wait_for_timeout(2000)
        after = page.evaluate(f"""() => {{ const c = document.querySelector('#content'); const el = document.querySelector('#grid .cell[data-id="{before['id']}"]');
            return {{ top: c.scrollTop, y: el ? el.getBoundingClientRect().top : null }}; }}""")
        assert after["y"] is not None and abs(after["y"] - before["y"]) < 40, (before, after)
        # 點目前這一頁（同一個網址）＝回到最上面
        page.evaluate("import('/static/js/shell.js').then(m => m.go('all'))")
        page.wait_for_timeout(1200)
        assert page.evaluate("document.querySelector('#content').scrollTop") < 5
        assert not errors, errors
        br.close()


def test_review_viewer_steps_past_multi_candidate(base, client):
    """待確認（網格）開大圖按 →：一張圖有好幾個候選（清單裡佔好幾筆）也要能翻到下一張（以前卡在同一張）。"""
    from app import candidates, db
    client.post("/api/settings", json={"knn_enabled": False})
    vis = [it["id"] for it in client.get("/api/media", params={"view": "all", "limit": 50, "nsfw": "hide", "noalbum": 1}).json()["items"]]
    m1 = vis[0]
    with db.write_lock:
        for t in ("step_a_(tst)", "step_b_(tst)", "step_c_(tst)"):     # 直接寫（candidates.add 會因為前面測試留下的搜源結果而不收）
            db.conn().execute("INSERT OR REPLACE INTO media_tags(media_id, tag_id, score, source, state, tier) VALUES(?,?,0.999,'model',0,4)",
                              (m1, db.tag_id(t, "character")))
    try:
        with pw.sync_playwright() as p:
            br = _browser(p)
            page = br.new_page(viewport={"width": 1400, "height": 900})
            page.goto(base + "/static/index.html#/home", wait_until="domcontentloaded")
            page.evaluate("""localStorage.setItem('lib:rmode','"grid"'); localStorage.setItem('lib:rtier','"all"'); localStorage.setItem('lib:rtag','""')""")
            page.goto(base + "/static/index.html#/review", wait_until="domcontentloaded")
            page.wait_for_function("lib.S.items.length > 3")
            ids = page.evaluate("[...new Set(lib.S.items.map(x => x.id))]")
            # 清單裡出現兩次以上（＝有好幾個候選）、後面還有別張的圖：通常就是上面放的 m1，不在第一批時用別張
            m1 = page.evaluate("(() => { const a = lib.S.items.map(x => x.id), u = [...new Set(a)]; return u.find((id, i) => i + 1 < u.length && a.filter(x => x === id).length > 1); })()")
            assert m1 is not None, "測試資料：要有一張圖有好幾個候選"
            k = ids.index(m1)
            assert k + 1 < len(ids), "測試資料：後面要還有別張"
            page.evaluate(f"lib.openViewer({m1})")
            page.wait_for_selector("#viewer .zw img")
            page.keyboard.press("ArrowRight")
            page.wait_for_function(f"!(document.querySelector('#viewer .zw img')?.src || '').includes('/file/{m1}?')")
            assert f"/file/{ids[k + 1]}?" in page.evaluate("document.querySelector('#viewer .zw img').src")
            assert page.inner_text(".vpos").startswith(f"{k + 2} / {len(ids)}")
            br.close()
    finally:
        with db.write_lock:
            db.conn().execute("DELETE FROM media_tags WHERE tag_id IN (SELECT id FROM tags WHERE name LIKE 'step\\__\\_(tst)' ESCAPE '\\')")
        client.post("/api/settings", json={"knn_enabled": True})


def test_quick_tag_ignores_image_you_left(base, client):
    """滑鼠指過一張、移到側欄再按 1：不能把快速標籤加到那張已經沒在看的圖上。"""
    from app import db
    with pw.sync_playwright() as p:
        br = _browser(p)
        page = br.new_page(viewport={"width": 1400, "height": 900})
        _open(page, base, "all")
        page.wait_for_selector("#grid .cell")
        cell = page.query_selector("#grid .cell")
        mid = int(cell.get_attribute("data-id"))
        cell.hover()
        assert page.evaluate("lib.S.hover") == mid
        page.hover("#nav a[data-view='characters']")
        page.keyboard.press("1")
        page.wait_for_timeout(500)
        q1 = page.evaluate("lib.S.quick[0].name")
        tid = db.tag_id(q1, create=False)
        assert not (tid and db.conn().execute("SELECT 1 FROM media_tags WHERE media_id=? AND tag_id=? AND state=1", (mid, tid)).fetchone())
        assert page.evaluate("lib.S.hover") is None
        br.close()


def test_viewer_keyboard_workbench(base, client):
    """大圖：Enter＝第一個候選是、Shift+Enter＝全部是、做完自動下一張；A 打開加角色；本體＋造型合成一列。"""
    from app import db, people
    client.post("/api/settings", json={"knn_enabled": False})
    vis = [it["id"] for it in client.get("/api/media", params={"view": "all", "limit": 50, "nsfw": "hide", "noalbum": 1}).json()["items"]]
    m1, m2 = vis[0], vis[1]
    names = ["kb_a_(tst)", "kb_b_(tst)", "kb_b_(swimsuit)_(tst)", "kb_c_(tst)"]
    with db.write_lock:
        c = db.conn()
        c.execute("UPDATE media SET people=0 WHERE id=?", (m1,))
        for i, t in enumerate(names):
            c.execute("INSERT OR REPLACE INTO media_tags(media_id, tag_id, score, source, state, tier) VALUES(?,?,?,'model',0,4)",
                      (m1, db.tag_id(t, "character"), 0.99 - i * 0.01))
    people.refresh_bases()
    st = lambda t: db.conn().execute("SELECT state FROM media_tags WHERE media_id=? AND tag_id=?", (m1, db.tag_id(t, create=False))).fetchone()[0]
    try:
        with pw.sync_playwright() as p:
            br = _browser(p)
            page = br.new_page(viewport={"width": 1400, "height": 900})
            _open(page, base, "all")
            page.wait_for_selector("#grid .cell")
            page.evaluate(f"lib.openViewer({m1})")
            page.wait_for_selector("#v-id .candbox")
            rows = page.evaluate("[...document.querySelectorAll('#v-id .crow')].map(r => r.dataset.t)")
            assert "kb_b_(tst)" not in rows and "kb_b_(swimsuit)_(tst)" in rows, rows
            page.keyboard.press("a")
            page.wait_for_selector("#modal-root .picker2")
            page.keyboard.press("Escape")
            first = rows[0]
            page.keyboard.press("Enter")
            page.wait_for_function(f"![...document.querySelectorAll('#v-id .crow')].some(r => r.dataset.t === '{first}')")
            assert st(first) == 1
            page.keyboard.press("Shift+Enter")
            page.wait_for_function("!document.querySelector('#v-id .candbox')", timeout=8000)
            assert all(st(t) == 1 for t in names), [st(t) for t in names]
            page.wait_for_timeout(500)
            assert f"/file/{m1}?" in page.evaluate("document.querySelector('#viewer .zw img').src"), "圖庫頁：處理完留在這張（不自動跳）"
            page.keyboard.press("ArrowRight")
            page.wait_for_function(f"(document.querySelector('#viewer .zw img')?.src || '').includes('/file/{m2}?')", timeout=8000)
            br.close()
    finally:
        with db.write_lock:
            db.conn().execute("DELETE FROM media_tags WHERE tag_id IN (SELECT id FROM tags WHERE name LIKE 'kb\\_%' ESCAPE '\\')")
        client.post("/api/settings", json={"knn_enabled": True})


def test_delete_key_trashes_backspace_rejects(base, client):
    """Delete＝刪除（先跳確認框，Enter 確定、Esc 取消）、⌫＝不是：以前兩個鍵都是「不是」，圖庫頁按 Delete 沒反應。"""
    from app import db
    vis = [it["id"] for it in client.get("/api/media", params={"view": "all", "limit": 5, "nsfw": "hide"}).json()["items"]]
    with pw.sync_playwright() as p:
        br = _browser(p)
        page = br.new_page(viewport={"width": 1400, "height": 900})
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        _open(page, base, "all")
        page.wait_for_selector("#grid .cell")
        page.click("#grid .cell")
        page.keyboard.press("Delete")
        page.wait_for_selector("#modal-root #do-trash")
        assert page.evaluate("document.activeElement && document.activeElement.id") == "do-trash", "Enter 就能確定"
        page.keyboard.press("Escape")
        page.wait_for_function("!document.querySelector('#modal-root .modal')")
        page.keyboard.press("Escape")
        page.evaluate(f"lib.openViewer({vis[0]})")
        page.wait_for_selector("#viewer .zw img")
        page.keyboard.press("Backspace")
        page.wait_for_timeout(300)
        assert not page.query_selector("#modal-root #do-trash"), "⌫ 不是刪除"
        page.keyboard.press("Delete")
        page.wait_for_selector("#modal-root #do-trash")
        page.keyboard.press("Escape")
        assert db.conn().execute("SELECT status FROM media WHERE id=?", (vis[0],)).fetchone()[0] == "done", "取消就不刪"
        assert not errors, errors
        br.close()


def test_pixiv_work_matrix_ui(base, client):
    """大圖「整篇作品 N 頁：逐頁標角色」：點格子切換、存了寫進資料庫。"""
    from app import db
    from tests.test_library7_review2 import _fake_work
    c = db.conn()
    vis = [it["id"] for it in client.get("/api/media", params={"view": "all", "limit": 12, "nsfw": "hide"}).json()["items"]]
    olds = {m: c.execute("SELECT * FROM media_source WHERE media_id=?", (m,)).fetchone() for m in vis}
    _fake_work(c, db, vis, pid=77700002)
    try:
        with pw.sync_playwright() as p:
            br = _browser(p)
            page = br.new_page(viewport={"width": 1400, "height": 900})
            errors = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            _open(page, base, "all")
            page.evaluate(f"lib.openViewer({vis[0]})")
            page.wait_for_selector("#v-work")
            page.click("#v-work")
            page.wait_for_selector(".wktab .wkx")
            # 捲到最下面：角色名稱列、預覽框還在畫面上；滑到最後一列，預覽換成那一頁
            body = page.query_selector(".modal .mbody")
            page.evaluate("document.querySelector('.modal .mbody').scrollTop = 99999")
            last = page.query_selector_all(".wktab tbody tr")[-1]
            last.hover()
            box = body.bounding_box()
            th = page.query_selector(".wktab thead th").bounding_box()
            pv = page.query_selector(".wkprev img").bounding_box()
            assert box["y"] - 2 <= th["y"] <= box["y"] + 20, (box, th)
            assert box["y"] - 2 <= pv["y"] < box["y"] + box["height"] - 40, (box, pv)
            assert page.get_attribute(".wkprev img", "src").startswith(f"/t/{last.get_attribute('data-prev')}")
            page.evaluate("document.querySelector('.modal .mbody').scrollTop = 0")
            # 指定 wk_a 那一欄（以前拿第一欄：圖本身的角色也會出現在表裡，排序並列時第一欄不一定是測試的角色 → 偶爾失敗）
            name = "wk_a_(tst)"
            col = page.evaluate(f"[...document.querySelectorAll('.wktab thead th')].findIndex(th => th.title === {name!r}) - 1")
            btn = page.query_selector(f'.wkx[data-m="{vis[1]}"][data-c="{col}"]')
            btn.click()
            assert "改了 1 格" in page.inner_text("#wk-n")
            page.click("#wk-save")
            page.wait_for_function("!document.querySelector('.wktab')")
            assert c.execute("SELECT state FROM media_tags WHERE media_id=? AND tag_id=?", (vis[1], db.tag_id(name, create=False))).fetchone()[0] == -1
            assert not errors, errors
            br.close()
    finally:
        with db.write_lock:
            c.execute("DELETE FROM media_tags WHERE tag_id IN (SELECT id FROM tags WHERE name LIKE 'wk\\_%' ESCAPE '\\')")
            for m, r in olds.items():
                c.execute("DELETE FROM media_source WHERE media_id=?", (m,))
                if r:
                    c.execute(f"INSERT INTO media_source({','.join(r.keys())}) VALUES({','.join('?' * len(r.keys()))})", tuple(r))


def test_album_books_sort_and_arrange_ui(base, client):
    """作品集：好幾本 → 目錄＋換本分隔；排頁序視窗有預覽；手動調整「移到最前」存得進去；拖曳也能換位置。"""
    import os
    from app import db
    c = db.conn()
    roots = {os.path.normcase(os.path.normpath(r)) for r in db.get_settings()["roots"]}
    paths = {r[0]: r[1] for r in c.execute("SELECT id, path FROM media WHERE status='done' AND kind='image'")}
    in_set = sorted(i for i, p in paths.items() if os.path.basename(os.path.dirname(p)) == "set")
    loose = sorted(i for i, p in paths.items() if os.path.normcase(os.path.normpath(os.path.dirname(p))) in roots)[:3]
    a0 = c.execute("SELECT COALESCE(MAX(id), 0) FROM actions").fetchone()[0]
    aid = client.post("/api/albums", json={"title": "UI 頁序", "ids": loose + in_set}).json()["id"]
    try:
        order = lambda: [p["id"] for p in client.get(f"/api/albums/{aid}/pages").json()]
        smart = order()
        with pw.sync_playwright() as p:
            br = _browser(p)
            page = br.new_page(viewport={"width": 1400, "height": 900})
            errors = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            _open(page, base, f"album/{aid}")
            page.wait_for_selector("#grid .cell")
            page.wait_for_selector(".booksec")
            assert len(page.query_selector_all(".booksec")) == 2 and "第 1 本" in page.inner_text(".booksec")
            assert len(page.query_selector_all(".albooks .bk")) == 2
            # 排頁序：預覽
            page.click('[data-pmenu="al-menu"]')
            page.click("#al-sort")
            page.wait_for_selector("#so-prev b")
            assert "會分成 2 本" in page.inner_text("#so-prev")
            page.click('#so-by button[data-by="reverse"]')
            page.wait_for_function("document.querySelector('#so-prev').textContent.includes('換位置')")
            page.click("#so-go")
            page.wait_for_function("!document.querySelector('#so-prev')")
            assert order() == smart[::-1]
            assert not page.query_selector(".alwarn")          # 整本倒過來：每本還是連著的，不算交錯
            client.post(f"/api/albums/{aid}/order", json={"ids": [smart[0], smart[-1], smart[1]]})
            page.evaluate("lib.render()")
            page.wait_for_selector(".alwarn #al-fix")          # 交錯了 → 提示＋一鍵智慧排序、不畫分隔
            assert not page.query_selector(".booksec")
            page.click('[data-pmenu="al-menu"]')
            page.click("#al-arrange")
            page.wait_for_selector(".arrc")
            assert not page.query_selector(".arrsec")          # 手動調整也一樣：交錯時不畫
            page.click(".modal [data-close]")
            page.click("#al-fix")
            page.wait_for_function("!document.querySelector('.alwarn')")
            assert order() == smart
            # 手動調整：選最後一張 → 移到最前 → 儲存
            page.click('[data-pmenu="al-menu"]')
            page.click("#al-arrange")
            page.wait_for_selector(".arrc")
            assert len(page.query_selector_all(".arrsec")) == 2
            page.query_selector_all(".arrc")[-1].click()
            page.click('[data-mv="first"]')
            assert page.query_selector_all(".arrc")[0].get_attribute("data-id") == str(smart[-1])
            assert "還沒儲存" in page.inner_text("#ar-dirty")
            page.click("#ar-save")
            page.wait_for_function("!document.querySelector('.arrc')")
            assert order() == [smart[-1]] + smart[:-1]
            # 拖曳：第 1 張拖到第 3 張後面
            page.click('[data-pmenu="al-menu"]')
            page.click("#al-arrange")
            page.wait_for_selector(".arrc")
            cells = page.query_selector_all(".arrc")
            first = cells[0].get_attribute("data-id")
            b3 = cells[2].bounding_box()
            page.locator(".arrc").nth(0).drag_to(page.locator(".arrc").nth(2), target_position={"x": b3["width"] - 5, "y": b3["height"] / 2})
            ids = [x.get_attribute("data-id") for x in page.query_selector_all(".arrc")]
            assert ids.index(first) == 2, ids
            page.click("#ar-save")
            page.wait_for_function("!document.querySelector('.arrc')")
            assert order()[2] == int(first)
            assert not errors, errors
            br.close()
    finally:
        with db.write_lock:
            c.execute("DELETE FROM album_items WHERE album_id=?", (aid,))
            c.execute("DELETE FROM albums WHERE id=?", (aid,))
            c.execute("DELETE FROM actions WHERE id>?", (a0,))
            c.commit()


def test_userscript_x_media_sweep(base, client):
    """腳本在 x.com/<帳號>/media：圖庫有這位畫師 → 左下角按鈕；按了往下捲、把圖片網址送回來，本機比對到 → 出處是那則推文。"""
    import base64
    import re as _re
    import time
    import httpx
    from app import db
    from app.danbooru import save_tag_info
    from tests.conftest import MOCK_PORT, ids_by_name, wait_idle
    c = db.conn()
    ids = sorted(ids_by_name(client).values())
    # 比對對象：新放一張圖進圖庫（前面的測試會換掉某些圖的檔案、加影片；自己放的最乾淨）
    from tests.conftest import LIB, make_image
    make_image(LIB / "sweepui" / "IMG_7001.jpg", 7001)
    client.post("/api/jobs", json={"kind": "scan"})
    wait_idle(client)
    target = ids_by_name(client)["IMG_7001.jpg"]
    paths = {r[0]: r[1] for r in c.execute("SELECT id, path FROM media")}
    art = db.tag_id("x_sweep_ui_(tst)", "artist")
    save_tag_info(art, {"status": "ok", "copyright": None, "copyrights": [], "summary": "", "post_count": None, "other_names": [],
                        "url": "https://x.com/SweepUi", "links": ["https://x.com/SweepUi"]})
    with db.write_lock:
        c.execute("INSERT OR REPLACE INTO media_tags(media_id, tag_id, score, source, state) VALUES(?,?,NULL,'manual',1)", (ids[1], art))
        c.execute("DELETE FROM media_source WHERE media_id=?", (target,))
    httpx.post(f"http://127.0.0.1:{MOCK_PORT}/_sweep", json={"XUIMEDIA00001": paths[target]})
    was_online = db.get_settings()["online_enabled"]
    client.post("/api/settings", json={"online_enabled": True})
    js = client.get("/api/fetch-url/userscript.user.js").text

    def gm(req):
        u = req["url"].replace("http://testserver", base)
        r = httpx.request(req["method"], u, headers=req["headers"], content=req["data"])
        return {"status": r.status_code, "text": r.text, "b64": base64.b64encode(r.content).decode(), "ctype": r.headers.get("content-type", "")}
    page_html = """<html><body style="margin:0"><div style="height:600px">
      <a href="/SweepUi/status/1911111111111111111/photo/1"><img src="https://pbs.twimg.com/media/XUIMEDIA00001?format=jpg&name=360x360" width=200 height=200></a>
      <a href="/SweepUi/status/1911111111111111112/photo/1"><img src="https://pbs.twimg.com/media/XUIMEDIA00002?format=jpg&name=360x360" width=200 height=200></a>
    </div></body></html>"""
    try:
        with pw.sync_playwright() as p:
            b = _browser(p)
            page = b.new_page(viewport={"width": 1200, "height": 800})
            errors = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.expose_function("__gm", gm)
            page.add_init_script(GM_SHIM)
            # 真的油猴腳本是 document-idle 才跑（有 body）；init script 太早，等 DOMContentLoaded
            page.add_init_script("document.addEventListener('DOMContentLoaded', () => {" + js.split("// ==/UserScript==", 1)[1] + "});")
            page.route(_re.compile(r"^https://pbs\.twimg\.com/.*"), lambda rt: rt.fulfill(status=200, content_type="image/png",
                                                                                           body=httpx.get(f"http://127.0.0.1:{MOCK_PORT}/media/a.jpg").content))
            page.route(_re.compile(r"^https://x\.com/.*"), lambda rt: rt.fulfill(status=200, content_type="text/html; charset=utf-8", body=page_html))
            page.on("console", lambda m: errors.append("console:" + m.text))
            page.goto("https://x.com/SweepUi/media")
            try:
                page.wait_for_function("document.documentElement.innerText.includes('開始比對')", timeout=15000)
            except Exception:
                from app import importer
                raise AssertionError((page.evaluate("document.documentElement.innerText"), errors,
                                      client.post("/api/sweep/ext/x/check", headers={"X-Import-Key": importer.import_key()}, json={"handle": "SweepUi"}).text))
            assert "x_sweep_ui_(tst)" in page.inner_text("html")
            with db.write_lock:     # 背景搜源可能剛好替這張找到出處（就不是比對對象了）：再清一次
                c.execute("DELETE FROM media_source WHERE media_id=?", (target,))
            page.get_by_text("開始比對").click()
            page.wait_for_function("document.documentElement.innerText.includes('完成：送了 2 張')", timeout=30000)
            assert not errors, errors
            b.close()
        for _ in range(300):
            time.sleep(0.2)
            r = c.execute("SELECT post_url FROM media_source WHERE media_id=? AND method='x_scan'", (target,)).fetchone()
            if r:
                break
        from app import online, sweep as _sw
        if not r:
            import io as _io
            from PIL import Image as _Im
            M = _sw.Matcher()
            im = _Im.open(_io.BytesIO(httpx.get(f"http://127.0.0.1:{MOCK_PORT}/ximg/media/XUIMEDIA00001").content)); im.load()
            raise AssertionError(("in_targets", target in set(int(x) for x in M.T["ids"]), "best", M.best([im]), "verify", M.verify(target, im),
                                  "sha", httpx.get(f"http://127.0.0.1:{MOCK_PORT}/ximg/media/XUIMEDIA00001").content == open(paths[target], "rb").read()))
        assert r and r[0] == "https://x.com/SweepUi/status/1911111111111111111", (
            [tuple(x) for x in c.execute("SELECT * FROM sweep_artist")], {k: v for k, v in _sw.STATE.items() if k != "matches"},
            online.get_jobs().state, [tuple(x)[:6] for x in c.execute("SELECT id, kind, prio, grp, manual, created FROM job_queue")][:10],
            tuple(c.execute("SELECT status, method, post_url FROM media_source WHERE media_id=?", (target,)).fetchone() or ()))
    finally:
        with db.write_lock:
            c.execute("DELETE FROM media_tags WHERE tag_id=?", (art,))
            c.execute("DELETE FROM media_source WHERE media_id=?", (target,))
            c.execute("DELETE FROM sweep_artist")
            c.execute("DELETE FROM sweep_seen")
        client.post("/api/settings", json={"online_enabled": was_online})
        wait_idle(client)


def test_sweep_modal_and_oplog(base, client):
    """系統 › 工具：畫師作品比對打得開、數字有出來；自動流程有 PixAI 那一列；操作紀錄。"""
    with pw.sync_playwright() as p:
        b = _browser(p)
        page = b.new_page(viewport={"width": 1400, "height": 900})
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        _open(page, base, "system")
        page.wait_for_selector('[data-tool="sweep"]')
        assert "PixAI" in page.inner_text("#content")
        page.click('[data-tool="sweep"]')
        page.wait_for_function("(document.querySelector('#sw-pinfo')||{}).textContent?.includes('有帳號的畫師')")
        page.click(".modal [data-close]")
        # 操作紀錄：畫面上做一個動作（鍵盤）→ 紀錄裡看得到、記得是在哪一頁、用鍵盤
        from app import db
        n0 = db.conn().execute("SELECT COUNT(*) FROM op_log").fetchone()[0]
        _open(page, base, "all")
        page.wait_for_selector("#content .cell")
        page.hover("#content .cell")
        page.keyboard.press("l")                     # 不用整理（指著的那張）
        page.wait_for_function(f"fetch('/api/oplog?days=1&limit=1').then(r => r.json()).then(x => x.length && x[0].id > {n0})", polling=300)
        row = db.conn().execute("SELECT view, via FROM op_log ORDER BY id DESC LIMIT 1").fetchone()
        assert (row["view"], row["via"]) == ("all", "key"), dict(row)
        page.keyboard.press("Control+z")
        page.wait_for_timeout(400)
        _open(page, base, "system")
        page.click('[data-tool="oplog"]')
        page.wait_for_function("(document.querySelector('#ol')||{}).textContent?.includes('最常做的')")
        assert "鍵盤" in page.inner_text("#ol")
        page.select_option("#ol-days", "7")
        page.wait_for_function("(document.querySelector('#ol')||{}).textContent?.includes('最常做的')")
        page.click(".modal [data-close]")
        assert not errors, errors
        b.close()


def test_review_batch_mode_ui(base, client):
    """待確認 › 整批看：點一下變「不是」、再點變「略過」；Enter 送出，寫進資料庫、換下一個名字。"""
    from app import db
    from tests.conftest import LIB, ids_by_name, make_image, wait_idle
    c = db.conn()
    for k in range(3):        # 自己放三張新的（前面的測試會刪圖、排除、標 NSFW）
        make_image(LIB / "batchui" / f"bu_{k}.jpg", 8100 + k)
    client.post("/api/jobs", json={"kind": "scan"})
    wait_idle(client)
    names = ids_by_name(client)
    ids = [names[f"bu_{k}.jpg"] for k in range(3)]
    tid = db.tag_id("batch_ui_(tst)", "character")
    with db.write_lock:
        c.execute("DELETE FROM media_tags WHERE tag_id=?", (tid,))
        from app.api.review import MEMEISH_TAGS
        prev_nsfw = {i: c.execute("SELECT nsfw_manual FROM media WHERE id=?", (i,)).fetchone()[0] for i in ids}
        for i, sc in zip(ids, (0.99, 0.98, 0.97)):
            c.execute("INSERT INTO media_tags(media_id, tag_id, score, source, state, tier, reason) VALUES(?,?,?,'pixai',0,4,'t')", (i, tid, sc))
            # 前面的測試可能把這幾張標成 NSFW、或留下像梗圖的標籤：這裡要的是「一般插畫」
            c.execute("UPDATE media SET media_type='illustration', unrec_skip=0, nsfw_manual=0 WHERE id=?", (i,))
            c.execute(f"DELETE FROM media_tags WHERE media_id=? AND tag_id IN (SELECT id FROM tags WHERE name IN ({','.join('?' * len(MEMEISH_TAGS))}))",
                      (i, *MEMEISH_TAGS))
    try:
        with pw.sync_playwright() as p:
            b = _browser(p)
            page = b.new_page(viewport={"width": 1400, "height": 900})
            errors = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            _open(page, base, "review")
            page.evaluate("localStorage.setItem('lib:batchMin', '0.9')")
            page.click('#rmode [data-rm="batch"]')
            page.wait_for_selector("#bqueue [data-bt]")
            page.click('#bqueue [data-bt="batch_ui_(tst)"]')
            # 前一個名字的格子可能還在（換名字是非同步的）：等到換成這個名字的 3 張
            page.wait_for_function("document.querySelectorAll('#bgrid .bc').length === 3", timeout=8000)
            cells = page.query_selector_all("#bgrid .bc")
            assert len(cells) == 3
            cells[0].click()                      # 分數最低的（ids[2]）→ 不是
            cells[1].click(); cells[1].click()    # ids[1] → 略過
            assert "不是 1" in page.inner_text("#bsum").replace("　", " ").replace("  ", " ") or "不是 <b>1</b>" in page.inner_html("#bsum")
            page.mouse.move(5, 5)
            page.keyboard.press("Enter")
            page.wait_for_function("!document.querySelector('[data-bt=\"batch_ui_(tst)\"]')")
            st = lambda i: (c.execute("SELECT state FROM media_tags WHERE media_id=? AND tag_id=?", (i, tid)).fetchone() or [None])[0]
            assert st(ids[0]) == 1 and st(ids[2]) == -1 and st(ids[1]) is None
            assert not errors, errors
            page.click('#rmode [data-rm="focus"]')
            b.close()
    finally:
        with db.write_lock:
            c.execute("DELETE FROM media_tags WHERE tag_id=?", (tid,))
            c.executemany("UPDATE media SET unrec_skip=0, nsfw_manual=? WHERE id=?", [(prev_nsfw[i], i) for i in ids])


def test_delete_series(base, client):
    """作品頁「刪除作品」：從所有圖上拿掉這個作品標籤、回到角色頁；撤銷全部回來。待確認裡的作品候選也有「刪除這個作品…」。"""
    from tests.conftest import ids_by_name
    from app import db
    c = db.conn()
    ids = sorted(ids_by_name(client).values())[:2]
    tid = db.tag_id("junk_site_(tst)", "copyright")
    with db.write_lock:
        c.execute("DELETE FROM media_tags WHERE tag_id=?", (tid,))
        c.executemany("INSERT INTO media_tags(media_id, tag_id, score, source, state, tier) VALUES(?,?,NULL,'manual',1,1)", [(i, tid) for i in ids])
        c.execute("UPDATE tags SET auto_block=0 WHERE id=?", (tid,))
    try:
        with pw.sync_playwright() as p:
            b = _browser(p)
            page = b.new_page(viewport={"width": 1400, "height": 900})
            errors = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            _open(page, base, "series/junk_site_(tst)")
            page.click('[data-pmenu="sr-menu"]')
            page.click("#sr-del")
            page.wait_for_function("document.querySelector('.modal')?.innerText.includes('刪除作品')")
            assert "2" in page.inner_text(".modal .mbody")
            page.click("#td-ok")
            page.wait_for_function("location.hash.startsWith('#/characters')")
            assert not errors, errors
            b.close()
        assert c.execute("SELECT COUNT(*) FROM media_tags WHERE tag_id=?", (tid,)).fetchone()[0] == 0
        client.post("/api/undo", json={})
        assert c.execute("SELECT COUNT(*) FROM media_tags WHERE tag_id=? AND state=1", (tid,)).fetchone()[0] == 2
    finally:
        with db.write_lock:
            c.execute("DELETE FROM media_tags WHERE tag_id=?", (tid,))


def test_phone_remote_browse(client):
    """手機／平板（區網）：輸入 PIN → 看圖庫（只看：整理的入口藏起來）→ 大圖左右滑換張、往下滑關閉。"""
    if pw is None:
        _missing("沒有安裝 Playwright")
    import uvicorn
    from app.api.lan import lan_ip
    from app.server import app
    ip = lan_ip()
    if ip.startswith("127."):
        pytest.skip("這台機器沒有區網位址")
    port = _free_port()
    srv = uvicorn.Server(uvicorn.Config(app, host="0.0.0.0", port=port, log_level="warning", lifespan="off"))
    threading.Thread(target=srv.run, daemon=True).start()
    for _ in range(100):
        if srv.started:
            break
        time.sleep(0.05)
    client.post("/api/settings", json={"lan_enabled": True})
    pin = client.get("/api/settings").json()["lan_pin"]
    try:
        with pw.sync_playwright() as p:
            b = _browser(p)
            ctx = b.new_context(viewport={"width": 390, "height": 844}, has_touch=True, is_mobile=True)
            page = ctx.new_page()
            errors = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.goto(f"http://{ip}:{port}/static/index.html")
            page.wait_for_url("**/m")                      # 還沒輸入 PIN → 上傳／登入頁
            page.fill("#pin", pin)
            page.click("#go")
            page.click("a.pick.view")                      # 看圖庫
            page.wait_for_selector("#grid .cell")
            assert "#/all" in page.url
            assert not page.is_visible('#nav a[data-view="review"]') and not page.is_visible("#system-btn")
            page.goto(f"http://{ip}:{port}/static/index.html#/review")     # 整理的頁面 → 回圖庫
            page.wait_for_function("location.hash === '#/all'")
            page.wait_for_selector("#grid .cell")
            page.wait_for_timeout(600)
            page.locator("#grid .cell img").first.tap(force=True)       # 點一下就看大圖
            page.wait_for_selector("#viewer:not(.hidden) .stage")
            page.wait_for_function("window.lib && document.querySelector('#viewer .vpos')?.textContent.startsWith('1 /')")
            swipe = """([x0, y0, x1, y1]) => {
              const el = document.querySelector('#viewer .stage');
              const t = (x, y) => new Touch({identifier: 1, target: el, clientX: x, clientY: y});
              el.dispatchEvent(new TouchEvent('touchstart', {touches: [t(x0, y0)], changedTouches: [t(x0, y0)], bubbles: true}));
              el.dispatchEvent(new TouchEvent('touchmove', {touches: [t(x1, y1)], changedTouches: [t(x1, y1)], bubbles: true, cancelable: true}));
              el.dispatchEvent(new TouchEvent('touchend', {touches: [], changedTouches: [t(x1, y1)], bubbles: true}));
            }"""
            page.evaluate(swipe, [300, 400, 80, 410])        # 往左滑 → 下一張
            page.wait_for_function("document.querySelector('#viewer .vpos')?.textContent.startsWith('2 /')")
            page.evaluate(swipe, [200, 200, 205, 450])       # 往下滑 → 關閉
            page.wait_for_selector("#viewer.hidden", state="attached")
            assert not errors, errors
            b.close()
    finally:
        client.post("/api/settings", json={"lan_enabled": False})
        srv.should_exit = True


# ---- v4 噪音預算：功能一直加，畫面不能一直長控制項（2026-10-06 實測：待確認 30 個、收件匣第一張圖前 22 個） ----
_VIS = """el => { const r = el.getBoundingClientRect(); const s = getComputedStyle(el);
  return r.width > 2 && r.height > 2 && s.visibility !== 'hidden' && s.display !== 'none' && +s.opacity > 0.05 && r.bottom > 0 && r.top < innerHeight; }"""
_CTRL = "button, a, input, select, summary, [data-act]"


def test_noise_budget(base, client):
    """每頁看得到的控制項有上限；樣式表的字級只能用 token。"""
    import re
    from pathlib import Path
    css = (Path(__file__).resolve().parent.parent / "static" / "style.css").read_text(encoding="utf-8")
    sizes = set(re.findall(r"font-size:\s*([^;}\"!]+)", css))
    bad = {x.strip() for x in sizes} - {"var(--fs1)", "var(--fs2)", "var(--fs3)", "var(--fs4)", "0", "40px", "34px", "72px", "110px", "clamp(22px, 3vw, 34px)", "inherit"}
    assert not bad, f"樣式表出現 token 以外的字級：{bad}（用 --fs1..4）"
    budget = {"home": 9, "all": 5, "unrecognized": 7, "review": 12, "characters": 12}
    with pw.sync_playwright() as p:
        b = _browser(p)
        page = b.new_page(viewport={"width": 1400, "height": 900})
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        _open(page, base, "home")
        page.evaluate("""localStorage.setItem('lib:rmode', '"focus"')""")
        for view, lim in budget.items():
            _open(page, base, view)
            page.wait_for_timeout(900)
            if view == "review":
                page.wait_for_selector(".rq2, .rstage .empty, .rdone", timeout=8000)
            # 圖、卡片、格子本身不算（它們是內容）；算頁首、工具列、側欄以外的控制
            n = page.evaluate(f"""() => {{ const vis = {_VIS};
              const skip = '#grid, .cell, .rankl, .nchips, .stiles, .cards, .hrecent, #sidebar, .rqueue, .rq2peek, .bgrid, .pagetabs';
              return [...document.querySelectorAll('#main {_CTRL.replace(", ", ", #main ")}')].filter(e => !e.closest(skip) && vis(e)).map(e => (e.innerText || e.placeholder || e.title || e.tagName).trim().slice(0, 20)); }}""")
            print("BUDGET", view, len(n), n)
            assert len(n) <= lim, f"{view}：{len(n)} 個控制 > {lim}：{n}"
        assert not errors, errors
        b.close()


def test_viewer_caption_and_review_question(base, client):
    """大圖：身分是一行字幕，★ ✕ 平常看不到；待確認：一個問句＋三個大鍵。"""
    from tests.conftest import ids_by_name
    mid = sorted(ids_by_name(client).values())[2]
    with pw.sync_playwright() as p:
        b = _browser(p)
        page = b.new_page(viewport={"width": 1400, "height": 900})
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        _open(page, base, "all")
        page.wait_for_selector("#grid .cell")
        page.evaluate(f"localStorage.setItem('lib:vDrawer', 'false'); lib.openViewer({mid})")
        page.wait_for_selector("#v-id.cap .capline")
        hidden = page.evaluate(f"""() => {{ const vis = {_VIS};
          return [...document.querySelectorAll('#v-id .chip button')].filter(vis).length; }}""")
        assert hidden == 0, "★ ✕ 應該滑過名字才出現"
        assert page.evaluate("!document.querySelector('#v-id .qrow')"), "快速標籤不在字幕裡"
        page.keyboard.press("Escape")
        _open(page, base, "review")
        page.evaluate("lib.S.rmode = 'focus'; localStorage.setItem('lib:rmode', JSON.stringify('focus')); lib.render()")
        page.wait_for_timeout(1500)
        if page.query_selector(".rq2"):
            assert "嗎" in page.inner_text(".rq2q")
            assert len(page.query_selector_all(".rq2keys [data-rf]")) == 3
            assert not page.is_visible("#rqueue")
            page.click("#rq-toggle")
            assert page.is_visible("#rqueue")
        assert not errors, errors
        b.close()


def test_steps_settings_and_rerun(base, client):
    """設定 → 自動標記與搜源：每一步一列、改了馬上存；補跑視窗算得出張數、排得進去。"""
    with pw.sync_playwright() as p:
        b = _browser(p)
        page = b.new_page(viewport={"width": 1400, "height": 900})
        errors: list = []
        page.on("pageerror", lambda e: errors.append(f"JS 錯誤：{e}"))
        _open(page, base, "home")
        page.evaluate("localStorage.setItem('lib:setSec', JSON.stringify('online'))")   # 舊的分類名字：要導到新的
        page.evaluate("location.hash = '#/settings'")
        page.wait_for_selector(".setbody section.on .stp-row[data-step=wd]", timeout=8000)
        page.click(".stp-row[data-step=wd] [data-toggle]")
        page.wait_for_selector(".stp-row[data-step=wd].open input[type=range]", timeout=3000)
        page.select_option(".stp-row[data-step=iqdb] select.stp-act", "record")
        page.wait_for_timeout(700)
        assert client.get("/api/settings").json()["act_iqdb"] == "record"
        page.wait_for_selector(".stp-row[data-step=iqdb] .stp-mod", timeout=3000)
        sent = []

        def fake(route):          # 真的開始的那一次不送到後端（不要在其他測試跑的時候背景補跑）
            body = route.request.post_data_json or {}
            if body.get("dry"):
                return route.continue_()
            sent.append(body)
            route.fulfill(json={"n": 1, "per": {}, "why": {}, "queued": {"aimeta": 1}})
        page.route("**/api/rerun", fake)
        page.click("#stp-rerun")
        page.wait_for_selector(".rr-modal #rr-n b", timeout=5000)
        page.check("[name=rr-r][value=all]")
        page.check("[data-rs=aimeta]")
        page.wait_for_function("!document.querySelector('#rr-go').disabled", timeout=5000)
        page.click("#rr-go")
        page.wait_for_timeout(300)
        assert not page.query_selector(".rr-modal")
        assert sent and sent[0]["query"]["view"] == "all" and "aimeta" in sent[0]["steps"], sent
        client.post("/api/steps/reset")
        assert not _problems(page, errors), _problems(page, errors)
        b.close()


def test_share_button_and_ctrl_c(base, client, monkeypatch):
    """分享：大圖的分享鈕、大圖 Ctrl+C＝這張；圖庫 Ctrl+C＝選的圖（沒選就是滑鼠指著的）；有反白文字時 Ctrl+C 照常複製文字。"""
    from app import share
    got = []
    monkeypatch.setattr(share, "supported", lambda: True)
    monkeypatch.setattr(share, "set_clipboard", lambda files, image: got.append(files))
    vis = [it["id"] for it in client.get("/api/media", params={"view": "all", "limit": 5, "nsfw": "hide", "noalbum": 1}).json()["items"]]
    with pw.sync_playwright() as p:
        br = _browser(p)
        page = br.new_page(viewport={"width": 1400, "height": 900})
        _open(page, base, "all")
        page.wait_for_selector("#grid .cell")
        page.evaluate(f"lib.openViewer({vis[0]})")
        page.wait_for_selector("#v-share")
        page.click("#v-share")
        page.wait_for_function("(window.__toasts || []).some(t => t.includes('已複製 1'))", timeout=8000)
        assert len(got) == 1
        page.keyboard.press("Control+c")
        page.wait_for_timeout(600)
        assert len(got) == 2
        page.keyboard.press("Escape")
        page.evaluate(f"lib.setSel({vis[1]}, true); lib.setSel({vis[2]}, true)")
        page.keyboard.press("Control+c")
        page.wait_for_function("(window.__toasts || []).some(t => t.includes('已複製 2'))", timeout=8000)
        assert len(got) == 3 and len(got[-1]) == 2
        # 選取列也有「分享」
        page.fill("#cap-in", "分享")
        assert page.locator("#actionbar .caplist button[data-a=share]").count() == 1
        page.keyboard.press("Escape"); page.keyboard.press("Escape")
        # 反白文字時不搶 Ctrl+C
        page.evaluate("lib.clearSel()")
        page.evaluate("(() => { const r = document.createRange(); r.selectNodeContents(document.querySelector('#head h1') || document.body); getSelection().removeAllRanges(); getSelection().addRange(r); })()")
        page.keyboard.press("Control+c")
        page.wait_for_timeout(500)
        assert len(got) == 3
        br.close()


def test_video_to_gif_modal(base, client, monkeypatch):
    """影片轉 GIF：大圖的底片按鈕 → 時間軸、I／O 設範圍 → Enter 轉 → 看結果 → Enter 存進圖庫。"""
    import os
    from app import db, pipeline
    from tests.conftest import LIB
    from tests.test_library18_gif import make_video
    p = os.path.normpath(str(make_video(LIB / "gif_ui" / "ui_clip.mp4", seconds=8)))
    pipeline.get_jobs().submit("scan", [p])
    for _ in range(240):
        r = db.conn().execute("SELECT id, status FROM media WHERE path=?", (p,)).fetchone()
        if r and r["status"] == "done":
            break
        time.sleep(0.25)
    mid = r["id"]
    with pw.sync_playwright() as pl:
        br = _browser(pl)
        page = br.new_page(viewport={"width": 1400, "height": 900})
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        _open(page, base, "all")
        page.evaluate(f"lib.openViewer({mid})")
        page.wait_for_selector("#v-gif")
        page.click("#v-gif")
        page.wait_for_selector(".gifm .gftrack")
        page.wait_for_function("document.querySelector('#gf-e').value !== ''")
        # 預設：20 秒以下的影片整部；I／O 在播放位置設開始、結束
        assert page.input_value("#gf-s") == "0.0" and page.input_value("#gf-e") == "8.0"
        # 做好後：預設直接複製（按鈕字跟著變）；這裡改成「先看看」測原本的流程
        assert "直接複製" in page.inner_text("#gf-go")
        page.click("#gf-a button[data-v='']")
        assert "轉成 GIF" in page.inner_text("#gf-go")
        page.evaluate("document.querySelector('.gfvid').currentTime = 2")
        page.wait_for_timeout(300)
        page.keyboard.press("i")
        assert page.input_value("#gf-s") == "2.0"
        page.evaluate("document.querySelector('.gfvid').currentTime = 3.5")
        page.wait_for_timeout(300)
        page.keyboard.press("o")
        assert page.input_value("#gf-e") == "3.5"
        page.click("#gf-fps button[data-v='10']")
        assert "15 格" in page.inner_text("#gf-info")
        # 拖右端到最後
        box = page.locator(".gftrack").bounding_box()
        h = page.locator(".gfh[data-h=e]").bounding_box()
        page.mouse.move(h["x"] + h["width"] / 2, h["y"] + h["height"] / 2)
        page.mouse.down(); page.mouse.move(box["x"] + box["width"] * 0.75, h["y"] + 5); page.mouse.up()
        assert abs(float(page.input_value("#gf-e")) - 6.0) < 0.3
        page.keyboard.press("Enter")
        page.wait_for_selector(".gfout:not(.hidden)", timeout=30000)
        assert "格" in page.inner_text(".gfdone")
        page.keyboard.press("Enter")
        page.wait_for_function("document.querySelector('#gf-saved')?.textContent.includes('已存進圖庫')", timeout=8000)
        assert "ui_clip_2_0s-6_0s" in page.inner_text("#gf-saved")
        assert os.path.isfile(os.path.join(os.path.dirname(p), [f for f in os.listdir(os.path.dirname(p)) if f.endswith(".gif")][0]))
        page.keyboard.press("Escape")
        assert not errors, errors
        br.close()


def test_b84_dataset_implies_import_ui(base, client, tmp_path):
    """b84：系統 › 更多工具 的「匯出訓練集」「標籤蘊含」打得開；匯出視窗的預覽有標註、匯出寫出 .txt；
    標籤頁 ⋯ 裡改「也代表」存得進去、頁首出現；匯入視窗提到 gallery-dl。"""
    from app import db
    with db.write_lock:
        db.tag_id("uiimp_child", "custom"); db.tag_id("uiimp_parent", "custom")
        db.conn().commit()
    import os
    mid = next(r[0] for r in db.conn().execute("SELECT id, path FROM media WHERE kind='image' AND status='done' AND COALESCE(excluded,0)=0 ORDER BY id DESC")
               if os.path.isfile(r[1]))
    client.post("/api/tags/apply", json={"ids": [mid], "action": "add", "tag": "uiimp_child", "category": "custom"})
    with pw.sync_playwright() as p:
        b = _browser(p)
        page = b.new_page(viewport={"width": 1400, "height": 900})
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        _open(page, base, "system")
        page.wait_for_selector('[data-tool="find"]')
        page.click(".tmore summary")
        page.click('.tmore [data-tool="implies"]')
        page.wait_for_selector(".modal #im-sync")
        page.click(".modal [data-close]")
        page.click('.tmore [data-tool="dataset"]')          # 「更多工具」還開著
        page.wait_for_selector(".modal #ds-q")
        page.fill("#ds-q", "tag:uiimp_child")
        page.check('[data-dc="custom"]')                    # 自訂標籤預設不放
        page.wait_for_function("(document.querySelector('#ds-prev')||{}).textContent?.includes('uiimp child')")
        page.fill("#ds-dest", str(tmp_path / "ds"))
        page.click("#ds-go")
        page.wait_for_function("(document.querySelector('#ds-sum')||{}).textContent?.startsWith('完成')")
        assert (tmp_path / "ds" / f"{mid}.txt").read_text("utf-8").startswith("uiimp child")
        page.click(".modal [data-close]")
        # 標籤頁 ⋯ ›「也代表」
        _open(page, base, "tag/uiimp_child")
        page.click('[data-pmenu="tag-menu"]')
        page.click("#tagmenu-btn")
        page.fill("#tm-imp", "uiimp_parent")
        page.click("#tm-save")
        page.wait_for_function("document.querySelector('#head')?.textContent.includes('也代表')")
        r = client.get("/api/tag/uiimp_child").json()
        assert [x["name"] for x in r["implies"]["implies"]] == ["uiimp_parent"]
        # 匯入視窗：gallery-dl 的說明
        _open(page, base, "system")
        page.click('[data-tool="import"]')
        page.wait_for_function("(document.querySelector('#im-gdl')||{}).textContent?.includes('gallery-dl')")
        page.click(".modal [data-close]")
        assert not errors, errors
        b.close()


def test_b93_grid_keeps_images_viewer_number_keys_and_events(base, client):
    """b93：捲動／重畫時留在畫面上的縮圖不重建（以前整片重建＋淡入，一閃一閃）；大圖 Shift／Alt＋數字＝第 N 個候選
    是／不是；事件流（?sse=1）連上後狀態列由伺服器推。"""
    from app import db
    client.post("/api/settings", json={"knn_enabled": False})
    vis = [it["id"] for it in client.get("/api/media", params={"view": "all", "limit": 50, "nsfw": "hide", "noalbum": 1}).json()["items"]]
    m1 = vis[2]
    names = ["nk_a_(tst)", "nk_b_(tst)", "nk_c_(tst)"]
    with db.write_lock:
        c = db.conn()
        for i, t in enumerate(names):
            c.execute("INSERT OR REPLACE INTO media_tags(media_id, tag_id, score, source, state, tier) VALUES(?,?,?,'model',0,4)",
                      (m1, db.tag_id(t, "character"), 0.99 - i * 0.01))
    st = lambda t: (db.conn().execute("SELECT state FROM media_tags WHERE media_id=? AND tag_id=?", (m1, db.tag_id(t, create=False))).fetchone() or [None])[0]
    try:
        with pw.sync_playwright() as p:
            br = _browser(p)
            page = br.new_page(viewport={"width": 1400, "height": 900})
            errors = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            _open(page, base, "all")
            page.wait_for_selector("#grid .cell img")
            page.wait_for_timeout(400)
            # 同一個 <img> 元素：重畫、改大小、選取之後都還是它（不重新淡入）
            page.evaluate("window.__keep = document.querySelector('#grid .cell img')")
            page.evaluate("import('/static/js/layout.js').then(m => { m.drawWindow(true); m.relayout(); })")
            page.wait_for_timeout(200)
            page.click("#grid .cell")
            page.wait_for_timeout(200)
            assert page.evaluate("document.contains(window.__keep) && window.__keep.classList.contains('ld')"), "留在畫面上的縮圖不重建"
            assert page.evaluate("window.__keep.closest('.cell').classList.contains('selected')"), "選取狀態照樣更新"
            assert "v=" in page.evaluate("window.__keep.getAttribute('src')"), "縮圖網址帶版本（可以快取）"
            page.keyboard.press("Escape")
            # 大圖：候選前面有數字；Shift+2＝第二個是、Alt+1＝第一個不是
            page.evaluate(f"lib.openViewer({m1})")
            page.wait_for_selector("#v-id .candbox .cnum")
            order = page.evaluate("[...document.querySelectorAll('#v-id .crow')].map(r => r.dataset.t)")
            page.keyboard.press("Shift+Digit2")
            page.wait_for_function(f"![...document.querySelectorAll('#v-id .crow')].some(r => r.dataset.t === '{order[1]}')")
            assert st(order[1]) == 1 and st(order[0]) == 0 and st(order[2]) == 0
            page.keyboard.press("Alt+Digit1")
            page.wait_for_function(f"![...document.querySelectorAll('#v-id .crow')].some(r => r.dataset.t === '{order[0]}')")
            assert st(order[0]) == -1 and st(order[2]) == 0
            assert not any(q for q in page.evaluate("[...document.querySelectorAll('#hud .hm')].map(x => x.textContent)") if "nk_" in q), "數字鍵本身還是快速標籤，不會被當成候選"
            page.keyboard.press("Escape")
            assert not errors, errors
            # 事件流：?sse=1 才在自動化瀏覽器裡開
            page.goto(base + "/static/index.html?sse=1#/all", wait_until="domcontentloaded")
            page.wait_for_function("document.getElementById('st-text') && document.getElementById('st-text').textContent.length > 0", timeout=10000)
            page.wait_for_function("import('/static/js/core.js').then(m => m.sseOk())", timeout=10000)
            page.wait_for_function("import('/static/js/core.js').then(m => !!m.lastEvent.status)", timeout=10000)
            br.close()
    finally:
        with db.write_lock:
            db.conn().execute("DELETE FROM media_tags WHERE tag_id IN (SELECT id FROM tags WHERE name LIKE 'nk\\_%' ESCAPE '\\')")
        client.post("/api/settings", json={"knn_enabled": True})


def test_srcurl_modal_from_viewer(base, client):
    """b94：大圖 › 詳細 › 出處「貼網址」：貼上就比對；不像的不勾，自己勾「記成出處」再確認。"""
    from app import db
    vis = [it["id"] for it in client.get("/api/media", params={"view": "all", "limit": 10, "nsfw": "hide", "noalbum": 1}).json()["items"]]
    mid = vis[3]
    try:
        with pw.sync_playwright() as p:
            br = _browser(p)
            page = br.new_page(viewport={"width": 1400, "height": 900})
            errors = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            _open(page, base, "all")
            page.wait_for_selector("#grid .cell")
            page.evaluate(f"lib.openViewer({mid})")
            page.wait_for_selector("#viewer")
            if not page.evaluate("document.getElementById('viewer').classList.contains('drawer')"):
                page.keyboard.press("i")
            page.wait_for_selector("#v-srcurl")
            page.click("#v-srcurl")
            page.wait_for_selector("#su-url")
            page.fill("#su-url", "https://twitter.com/someart/status/1790000000000000001")
            page.keyboard.press("Enter")
            page.wait_for_selector(".surow2[data-mid]", timeout=15000)
            assert page.evaluate("document.querySelector('[data-rec]').checked") is False, "不太像：預設不記出處"
            assert page.evaluate("document.getElementById('su-ok').disabled")
            page.check("[data-rec]")
            page.click("#su-ok")
            page.wait_for_selector("#su-url", state="detached")
            r = db.conn().execute("SELECT method FROM media_source WHERE media_id=?", (mid,)).fetchone()
            assert r and r[0] == "url"
            assert not errors, errors
            br.close()
    finally:
        with db.write_lock:
            db.conn().execute("DELETE FROM media_source WHERE media_id=?", (mid,))
            db.conn().execute("UPDATE media SET nsfw_manual=NULL WHERE id=?", (mid,))


def test_b101_drive_backup_ui(base, client, tmp_path, monkeypatch):
    """b101：系統 ›「備份到外接硬碟」：列出硬碟、算出要複製幾個、按開始做完；收件匣在太久沒備份時提醒。"""
    from app import db, drivebackup as dbk
    from tests.conftest import make_image
    src, prog, ext = tmp_path / "src", tmp_path / "prog", tmp_path / "ext"
    for p in (src, prog, ext):
        p.mkdir()
    for i in range(3):
        make_image(src / f"u{i}.jpg", 4100 + i)
    (prog / "run.py").write_text("x", "utf-8")
    monkeypatch.setenv("LIB_DRIVES", str(ext))
    monkeypatch.setattr(dbk, "sources", lambda man: [{"area": "圖片", "label": "示範", "path": str(src)},
                                                     {"area": "程式", "label": "", "path": str(prog)}])
    with pw.sync_playwright() as p:
        b = _browser(p)
        page = b.new_page(viewport={"width": 1400, "height": 900})
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        _open(page, base, "system")
        page.click('[data-tool="drive"]')
        page.wait_for_selector(".modal #dbk-dest")
        assert "沒看到其他硬碟" in page.inner_text(".modal"), "跟資料庫同一顆硬碟的不列（測試的暫存資料夾就是）"
        page.fill("#dbk-dest", str(ext))
        page.wait_for_function("(document.querySelector('#dbk-plan')||{}).textContent?.includes('要複製')")
        page.click("#dbk-go")
        page.wait_for_function("(document.querySelector('#dbk-plan')||{}).textContent?.includes('備份完成')", timeout=30000)
        assert (ext / dbk.BASE / "圖片" / "示範" / "u1.jpg").exists()
        page.click(".modal [data-close]")
        db.update_settings({"drive_backup_last": {"at": time.time() - 20 * 86400}})
        page.evaluate("localStorage.removeItem('pj:bkDriveUntil')")
        assert client.get("/api/status").json()["drive_overdue"] == 20
        for _ in range(20):           # 狀態有讀取快取（剛做完背景工作時會晚幾秒）
            page.evaluate("document.querySelector('#healthpop')?.remove()")
            page.reload()
            page.wait_for_function("document.querySelector('#health') && !document.querySelector('#health').classList.contains('hidden')", timeout=15000)
            page.click("#health")
            page.wait_for_selector("#healthpop")
            if "20 天前" in page.inner_text("#healthpop"):
                break
            time.sleep(1)
        assert "20 天前" in page.inner_text("#healthpop")
        page.click("#healthpop [data-drive-bk]")
        page.wait_for_selector(".modal #dbk-go")
        assert not _problems(page, errors), _problems(page, errors)
        b.close()
    db.update_settings({"drive_backup_last": {}})
