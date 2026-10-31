"""從網站匯入：app 裡貼網址、瀏覽器「存到圖庫」按鈕（Tampermonkey 腳本）。"""
from __future__ import annotations

import json

from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel

from .. import importer

router = APIRouter()


class ImportReq(BaseModel):
    url: str
    pages: list[int] | None = None      # Pixiv：只存這幾頁（0 起算）
    nhentai: dict | None = None         # nhentai：腳本在網頁上解析好的 {info, pages:[網址]}（只接受 i*.nhentai.net/galleries/…）
    album: bool | None = None           # Pixiv 多頁：存成本子（None＝照設定 pixiv_album）


def _run(url: str, pages: list[int] | None = None, ext: dict | None = None, album: bool | None = None) -> dict:
    try:
        return {"ok": True, **importer.import_url(url, pages, ext, album)}
    except importer.ImportError_ as e:
        return {"ok": False, "error": str(e), "url": url}
    except Exception as e:  # noqa
        return {"ok": False, "error": f"{type(e).__name__}: {e}"[:300], "url": url}


@router.post("/api/fetch-url")
def import_one(body: ImportReq):
    """app 裡貼上的網址（一次一個，畫面上逐一顯示進度）。"""
    return _run(body.url, body.pages, album=body.album)


@router.get("/api/fetch-url/parse")
def import_parse(url: str):
    p = importer.parse(url)
    return {"ok": bool(p), **(p or {})}


@router.post("/api/fetch-url/ext")
def import_ext(body: ImportReq, x_import_key: str | None = Header(default=None)):
    """瀏覽器按鈕用：要帶配對金鑰（server.py 的跨站檢查只對這個網址、而且金鑰正確時放行）。"""
    if not importer.check_key(x_import_key):
        raise HTTPException(403, "配對金鑰不正確：請到 設定 › 從網站匯入 重新安裝腳本")
    return _run(body.url, body.pages, body.nhentai, body.album)


@router.get("/api/fetch-url/galleries")
def gallery_jobs():
    """背景下載的進度：整本匯入（E-Hentai、nhentai）、gallery-dl（kind=gdl）。"""
    from .. import galleries, gdl
    return {"jobs": galleries.status() + gdl.status()}


class GalleryCancel(BaseModel):
    url: str


@router.post("/api/fetch-url/galleries/cancel")
def gallery_cancel(body: GalleryCancel):
    from .. import galleries, gdl
    return {"ok": galleries.cancel(body.url) or gdl.cancel(body.url)}


@router.get("/api/gdl")
def gdl_info(url: str = ""):
    """gallery-dl：有沒有裝、版本；帶 url 時回答這個網址能不能用 gallery-dl 匯入。"""
    from .. import gdl
    m = gdl.match(url) if url else None
    return {"available": gdl.available(), "version": gdl.version(), "enabled": db_settings().get("gdl_enabled", True),
            "max_posts": db_settings().get("gdl_max_posts", 0),
            "match": ({**m, "label": gdl.site_label(m["category"])} if m else None)}


def db_settings():
    from .. import db
    return db.get_settings()


@router.post("/api/gdl/install")
def gdl_install():
    """在程式裡安裝 gallery-dl（start.bat 只在啟動時裝；用同一個 uv、同一個版本）。"""
    from .. import db, gdl
    if gdl.available():
        return {"ok": True, "version": gdl.version(), "already": True}
    r = gdl.install()
    db.log_op("安裝 gallery-dl" + ("" if r["ok"] else "（失敗）"), keys="gdl_install")
    if not r["ok"]:
        raise HTTPException(500, "安裝失敗：" + r["log"][-400:])
    return r


@router.post("/api/fetch-url/key/reset")
def import_key_reset():
    from .. import db
    db.update_settings({"import_key": ""})
    importer.import_key()
    return {"ok": True}


USERSCRIPT = r"""// ==UserScript==
// @name         Pickle Jar：存到圖庫
// @namespace    pickle-jar-local
// @version      3.3
// @description  在 X、Pixiv、Danbooru、e621、Rule34、E-Hentai、nhentai 的貼文或縮圖上按一下，原圖和資料直接存進本機的 Pickle Jar；ascii2d 半自動找出處
// @match        https://x.com/*
// @match        https://twitter.com/*
// @match        https://www.pixiv.net/*
// @match        https://danbooru.donmai.us/*
// @match        https://e621.net/*
// @match        https://rule34.xxx/*
// @match        https://e-hentai.org/*
// @match        https://exhentai.org/*
// @match        https://nhentai.net/*
// @match        https://ascii2d.net/*
// @grant        GM_xmlhttpRequest
// @grant        GM_registerMenuCommand
// @connect      127.0.0.1
// @connect      localhost
// @run-at       document-idle
// @updateURL    __UPDATE__
// @downloadURL  __UPDATE__
// ==/UserScript==
(() => {
  "use strict";
  const API = __API__, KEY = __KEY__;
  const POST = [/^https:\/\/(x|twitter)\.com\/[^/]+\/status\/\d+/, /^https:\/\/www\.pixiv\.net\/(?:[a-z]{2}\/)?artworks\/\d+/,
    /^https:\/\/danbooru\.donmai\.us\/posts\/\d+/, /^https:\/\/e621\.net\/posts\/\d+/, /^https:\/\/rule34\.xxx\/index\.php\?.*[?&]id=\d+/,
    /^https:\/\/(?:e-|ex)hentai\.org\/g\/\d+\/[0-9a-f]{10}/, /^https:\/\/nhentai\.net\/g\/\d+/];
  const isPost = u => POST.some(r => r.test(u));
  const IS_X = /^(x|twitter)\.com$/.test(location.hostname);
  const saved = new Set();                       // 這次瀏覽已經存過的貼文
  const keyOf = u => { const m = u.match(/(?:status|artworks|posts)\/(\d+)|[?&]id=(\d+)|\/g\/(\d+)/); return m ? location.hostname + ":" + (m[1] || m[2] || m[3]) : u; };
  const css = "position:fixed;z-index:2147483647;font:600 14px system-ui,sans-serif;border:0;border-radius:999px;cursor:pointer;box-shadow:0 4px 14px rgba(0,0,0,.35)";
  function toast(msg, ok) {
    const t = document.createElement("div");
    t.textContent = msg;
    t.style.cssText = css + ";right:24px;bottom:150px;padding:10px 16px;cursor:default;max-width:360px;color:#fff;background:" + (ok ? "#2e7d32" : "#b3261e");
    document.documentElement.appendChild(t); setTimeout(() => t.remove(), ok ? 3000 : 7000);
  }
  function send(url, btn, done, pages, extra) {
    const txt = btn ? btn.textContent : "";
    if (btn) { btn.disabled = true; btn.textContent = "存檔中…"; }
    const reset = () => { if (btn) { btn.disabled = false; btn.textContent = txt; } };
    GM_xmlhttpRequest({
      method: "POST", url: API, headers: { "Content-Type": "application/json", "X-Import-Key": KEY },
      data: JSON.stringify({ url, ...(pages ? { pages } : {}), ...(extra || {}) }), timeout: 120000,
      onload: r => {
        let d = {}; try { d = JSON.parse(r.responseText); } catch (e) {}
        reset();
        if (r.status === 200 && d.ok && d.queued) {        // 整本（E-Hentai、nhentai）：背景下載
          saved.add(keyOf(url)); if (done) done();
          toast(d.already ? "這本已經在下載了" : `已排進背景下載：${d.title || ""}（${d.count} 頁）。下載完、標記完會自動建立作品集`, true);
        } else if (r.status === 200 && d.ok) {
          if (pages) pages.forEach(n => saved.add(keyOf(url) + ":p" + n)); else saved.add(keyOf(url));
          if (done) done();
          toast(d.saved.length ? `已存到圖庫 ${d.saved.length} ${"張"}` + (d.existing.length ? `（${d.existing.length} 張本來就有）` : "") + (d.album ? "，標記完會整理成一本" : "") : "圖庫裡已經有了", true);
        } else toast("存檔失敗：" + (d.error || d.detail || ("HTTP " + r.status)), false);
      },
      onerror: () => { reset(); toast("連不到 Pickle Jar（程式有開著嗎？）", false); },
      ontimeout: () => { reset(); toast("逾時：檔案可能很大，稍後到圖庫看看有沒有進來", false); },
    });
  }

  // ===== nhentai：網站有 Cloudflare，程式直接問會被擋 → 在網頁上用同網域的 API 解析，把每頁網址和標籤送去 =====
  const IS_NH = location.hostname === "nhentai.net";
  async function nhSave(url, btn, done) {
    let extra = null;
    try {
      const id = (url.match(/\/g\/(\d+)/) || [])[1];
      const d = await fetch(`/api/v2/galleries/${id}`, { credentials: "include" }).then(r => r.ok ? r.json() : Promise.reject(new Error("HTTP " + r.status)));
      const pages = (d.pages || []).map(p => `https://i${1 + Math.floor(Math.random() * 4)}.nhentai.net/${String(p.path).replace(/^\//, "")}`);
      extra = { nhentai: { info: { id: d.id, title: d.title, tags: (d.tags || []).map(t => ({ type: t.type, name: t.name })) }, pages } };
    } catch (e) { toast("nhentai 網頁上解析失敗：" + e.message + "（改由 Pickle Jar 直接抓，可能會被擋）", false); }
    send(url.split("?")[0], btn, done, null, extra);
  }


  // ===== Pixiv：多頁作品先選要存哪幾頁 =====
  const IS_PIXIV = location.hostname === "www.pixiv.net";
  const pidOf = u => { const m = String(u).match(/artworks\/(\d+)/); return m ? m[1] : null; };
  async function pixivSave(url, btn, done) {
    const pid = pidOf(url);
    let pages = null;
    try {
      const r = await fetch(`/ajax/illust/${pid}/pages`, { credentials: "include" });
      pages = (await r.json()).body;
    } catch (e) {}
    if (!pid || !Array.isArray(pages) || pages.length <= 1) return send(url, btn, done);   // 單頁（或問不到頁數）：直接整篇存
    pickPages(url, pages, btn, done);
  }
  function pickPages(url, pages, btn, done) {
    const key = keyOf(url), sel = new Set();
    let last = -1;
    const box = document.createElement("div");
    box.style.cssText = "position:fixed;inset:0;z-index:2147483647;background:rgba(0,0,0,.6);display:flex;align-items:center;justify-content:center;font:14px system-ui,sans-serif";
    const panel = document.createElement("div");
    panel.style.cssText = "background:#1f1f1e;color:#eee;border-radius:14px;padding:16px 18px;max-width:min(920px,94vw);max-height:88vh;display:flex;flex-direction:column;gap:12px;box-shadow:0 10px 40px rgba(0,0,0,.5)";
    const head = document.createElement("div");
    head.style.cssText = "display:flex;align-items:center;gap:10px;flex-wrap:wrap";
    const title = document.createElement("b"); title.textContent = `這篇有 ${pages.length} 頁：點選要存的（Shift 連選）`;
    title.style.marginRight = "auto";
    const mk = (t, bg) => { const b = document.createElement("button"); b.textContent = t; b.style.cssText = `border:0;border-radius:999px;padding:7px 14px;font:600 13px system-ui;cursor:pointer;color:#fff;background:${bg}`; return b; };
    const bAll = mk("全選", "#444"), bNone = mk("全不選", "#444");
    head.append(title, bAll, bNone);
    const grid = document.createElement("div");
    grid.style.cssText = "display:grid;grid-template-columns:repeat(auto-fill,minmax(130px,1fr));gap:8px;overflow:auto;padding:2px";
    const cells = pages.map((pg, i) => {
      const c = document.createElement("div");
      c.style.cssText = "position:relative;cursor:pointer;border-radius:8px;overflow:hidden;outline:3px solid transparent;background:#000;aspect-ratio:1";
      const img = document.createElement("img");
      img.src = (pg.urls && (pg.urls.small || pg.urls.thumb_mini)) || "";
      img.style.cssText = "width:100%;height:100%;object-fit:cover;display:block";
      const tag = document.createElement("span");
      tag.style.cssText = "position:absolute;left:6px;top:6px;padding:1px 7px;border-radius:999px;font:600 12px system-ui;background:rgba(0,0,0,.7);color:#fff";
      tag.textContent = (i + 1) + (saved.has(key + ":p" + i) ? "　已存" : "");
      c.append(img, tag);
      c.onclick = e => {
        if (e.shiftKey && last >= 0) { const [a, b] = [Math.min(last, i), Math.max(last, i)]; for (let k = a; k <= b; k++) sel.add(k); }
        else if (sel.has(i)) sel.delete(i); else sel.add(i);
        last = i; paint();
      };
      grid.appendChild(c);
      return c;
    });
    const foot = document.createElement("div");
    foot.style.cssText = "display:flex;gap:10px;justify-content:flex-end;flex-wrap:wrap";
    const bCancel = mk("取消", "#444"), bWhole = mk(`整篇存（${pages.length} 張）`, "#555"), bBook = mk("整篇存成本子", "#5a4a8a"), bGo = mk("", "#3a7d44");
    foot.append(bCancel, bWhole, bBook, bGo);
    panel.append(head, grid, foot); box.appendChild(panel);
    const paint = () => {
      cells.forEach((c, i) => { c.style.outlineColor = sel.has(i) ? "#5cc26a" : "transparent"; c.style.opacity = sel.size && !sel.has(i) ? ".55" : "1"; });
      bGo.textContent = sel.size ? `存選取的 ${sel.size} 張` : "先點選要存的頁";
      bGo.disabled = !sel.size; bGo.style.opacity = sel.size ? "1" : ".5";
    };
    const close = () => { box.remove(); removeEventListener("keydown", onKey, true); };
    const onKey = e => {
      if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); close(); }
      else if (e.key === "Enter" && sel.size) { e.preventDefault(); e.stopPropagation(); bGo.click(); }
    };
    addEventListener("keydown", onKey, true);
    box.onclick = e => { if (e.target === box) close(); };
    bAll.onclick = () => { pages.forEach((_, i) => sel.add(i)); paint(); };
    bNone.onclick = () => { sel.clear(); paint(); };
    bCancel.onclick = close;
    bWhole.onclick = () => { close(); send(url, btn, done); };
    bBook.onclick = () => { close(); send(url, btn, done, null, { album: true }); };    // 3.3：整篇存成一本（照頁序，整本的標籤是作者）
    bGo.onclick = () => { if (!sel.size) return; const ps = [...sel].sort((a, b) => a - b); close(); send(url, btn, done, ps); };
    paint();
    document.documentElement.appendChild(box);
  }

  // ===== ascii2d：半自動找出處（圖庫的「ascii2d 連續搜」）=====
  // 一張一張由你按 Enter：腳本向本機要下一張圖、用 ascii2d 自己的上傳表單送出；結果頁把連結送回本機比對，
  // 你看比對結果決定。Cloudflare 驗證出現時由你自己點。
  function a2d() {
    const BASE = API.replace(/\/api\/.*$/, "");
    // 連不上（不是 HTTP 錯誤）時自動再試：程式剛好在重新啟動、電腦剛醒來時常常第一下連不到
    const gm = async (method, path, body, type) => {
      for (let i = 0; ; i++) {
        try { return await gm1(method, path, body, type); }
        catch (e) { if (!e.net || i >= 3) throw e; await new Promise(r => setTimeout(r, 2000 * (i + 1))); }
      }
    };
    const netErr = msg => Object.assign(new Error(msg), { net: true });
    // 3.0：自己加一個逾時。Tampermonkey 偶爾整個請求沒有任何回應（連 ontimeout 都不來），以前腳本就永遠等在那裡（2026-10-16 11:37 停住）
    const gm1 = (method, path, body, type) => new Promise((ok0, bad0) => {
      let doneFlag = false;
      const ok = v => { if (!doneFlag) { doneFlag = true; clearTimeout(t); ok0(v); } };
      const bad = e => { if (!doneFlag) { doneFlag = true; clearTimeout(t); bad0(e); } };
      const t = setTimeout(() => bad(netErr("圖庫沒有回應（逾時）")), window.__a2dReqTimeout || 100000);
      GM_xmlhttpRequest({
      method, url: BASE + path, headers: body ? { "X-Import-Key": KEY, "X-Script-Version": "3.3", "Content-Type": "application/json" } : { "X-Import-Key": KEY, "X-Script-Version": "3.3" },
      data: body ? JSON.stringify(body) : undefined, responseType: type || "text", timeout: 90000,
      onload: r => {
        if (r.status >= 400) { let d = {}; try { d = JSON.parse(r.responseText); } catch (e) {} return bad(new Error(d.detail || ("HTTP " + r.status))); }
        if (type) return ok(r.response);
        try { ok(JSON.parse(r.responseText || "{}")); } catch (e) { bad(e); }
      },
      onerror: () => bad(netErr("連不到 Pickle Jar（程式有開著嗎？）")), ontimeout: () => bad(netErr("逾時")),
    }); });
    const SS = "libA2d";
    const ss = { get: () => { try { return JSON.parse(sessionStorage.getItem(SS) || "{}"); } catch (e) { return {}; } },
                 set: v => { try { sessionStorage.setItem(SS, JSON.stringify(v)); } catch (e) {} } };
    // 這個分頁有在跑連續搜（重新整理、換頁都記得；按暫停、找完才清掉）
    const RUN = "libA2dRun";
    const running = v => { try { if (v === undefined) return sessionStorage.getItem(RUN) === "1"; v ? sessionStorage.setItem(RUN, "1") : sessionStorage.removeItem(RUN); } catch (e) { return false; } };
    const el = (tag, style, text) => { const e = document.createElement(tag); if (style) e.style.cssText = style; if (text != null) e.textContent = text; return e; };
    const box = el("div", "position:fixed;z-index:2147483647;right:16px;bottom:16px;width:380px;max-height:80vh;overflow:auto;background:#1b1c1d;color:#ecebe7;"
      + "font:14px/1.5 system-ui,sans-serif;border-radius:14px;box-shadow:0 12px 40px rgba(0,0,0,.45);padding:14px 16px");
    const btn = (label, fn, primary) => { const b = el("button", "margin:4px 6px 0 0;padding:6px 12px;border:0;border-radius:999px;cursor:pointer;font:600 13px system-ui;"
      + (primary ? "background:#3f7a3b;color:#fff" : "background:#2e2f33;color:#ecebe7"), label); b.onclick = fn; return b; };
    let keys = {}, busy = false, FULL = false, lastAct = Date.now(), held = false, challenged = false;   // FULL：圖庫設成「全自動」（2.8）：什麼情況都自己處理，不停下來
    // 全自動遇到錯誤：等一下自己再試（次數記在 sessionStorage，換頁也記得）；試太多次就把這張「先跳過」
    const retry = (what, mid, wait, max, again) => {
      const s = ss.get(), k = "try_" + what + "_" + (mid || 0), n = (s[k] || 0) + 1;
      ss.set({ ...s, [k]: n });
      if (mid && n > max) return decide(mid, "skip");
      render(head(`全自動：${Math.round(wait / 1000)} 秒後自己再試（第 ${n} 次）`), el("div", "color:#9a9994;font-size:12px", "按 Esc 暫停"));
      keys = { Escape: pause };
      setTimeout(() => { if (!ss.get().paused) again(); }, wait);
    };
    const DOG = window.__a2dWatchdog || 120000;
    // 全自動的看門狗：兩分鐘畫面都沒動（又卡在哪個請求），就回 ascii2d 首頁重新開始（會接著做同一張）。
    // 你按鍵停下來看結果、暫停、Cloudflare 驗證時不動。
    setInterval(() => {
      if (FULL && running() && !held && !challenged && !ss.get().paused && Date.now() - lastAct > DOG) { lastAct = Date.now(); location.href = "https://ascii2d.net/"; }
    }, Math.min(15000, DOG / 3));
    const render = (...nodes) => { lastAct = Date.now(); box.replaceChildren(...nodes); if (!box.isConnected) document.documentElement.appendChild(box); };
    const head = (t, st) => { const h = el("div", "font-weight:700;margin-bottom:6px", t);
      if (st) h.appendChild(el("span", "font-weight:400;color:#9a9994;margin-left:8px", `剩 ${st.remaining} 張 · 找到 ${st.found}`)); return h; };
    document.addEventListener("keydown", e => {
      if (e.ctrlKey || e.metaKey || e.altKey || /INPUT|TEXTAREA|SELECT/.test((e.target && e.target.tagName) || "")) return;
      const f = keys[e.key.length === 1 ? e.key.toLowerCase() : e.key];
      if (f) { e.preventDefault(); e.stopPropagation(); f(); }
    }, true);

    async function next() {
      if (busy) return; busy = true; running(true); held = false;
      try {
        const n = await gm("POST", "/api/ascii2d/ext/next", {});
        if (!n.mid) { ss.set({}); running(false); keys = {}; return render(head("ascii2d 連續搜：全部找完了", n), el("div", "color:#9a9994", "回到圖庫看待確認和結果。")); }
        const wait = (ss.get().last || 0) + 4000 - Date.now();       // 每次至少隔 4 秒，對 ascii2d 客氣一點
        if (wait > 0) { render(head("準備下一張…", n)); await new Promise(r => setTimeout(r, wait)); }
        ss.set({ ...ss.get(), mid: n.mid });
        const blob = await gm("GET", `/api/ascii2d/ext/image/${n.mid}`, null, "blob");
        const form = document.querySelector('form[action="/search/file"]');
        ss.set({ mid: n.mid, last: Date.now(), go: !form, full: FULL });
        if (!form) { location.href = "https://ascii2d.net/"; return; }     // 這一頁沒有上傳表單：回首頁再送
        const inp = form.querySelector('input[type="file"]');
        const dt = new DataTransfer(); dt.items.add(new File([blob], `lib_${n.mid}.jpg`, { type: "image/jpeg" })); inp.files = dt.files;
        render(head("搜尋中…", n));
        form.submit();
      } catch (e) {
        if (FULL) { busy = false; const mid = ss.get().mid; return e.message === "HTTP 404" && mid ? decide(mid, "skip") : retry("next", null, 15000, 0, next); }
        render(head("ascii2d 連續搜"), el("div", "color:#ef8a7c", e.message), btn("再試一次 (Enter)", () => { busy = false; next(); }, true)); keys = { Enter: () => { busy = false; next(); } }; }
      finally { busy = false; }
    }

    async function decide(mid, outcome, accept) {
      if (busy) return; busy = true;
      try { await gm("POST", "/api/ascii2d/ext/decide", { mid, outcome, accept: accept || [] }); ss.set({ last: ss.get().last, full: FULL }); }
      catch (e) { busy = false;
        if (FULL) return retry("decide", null, 10000, 0, () => decide(mid, outcome, accept));
        return render(head("ascii2d 連續搜"), el("div", "color:#ef8a7c", "存不回圖庫：" + e.message)); }
      busy = false; next();
    }

    function showResult(mid, r) {
      const nodes = [head("ascii2d → 圖庫", r)];
      const top = el("div", "display:flex;gap:10px;align-items:flex-start;margin:6px 0");
      const mine = el("img", "width:84px;height:84px;object-fit:cover;border-radius:8px;background:#2e2f33;flex-shrink:0");
      gm("GET", `/api/ascii2d/ext/thumb/${mid}`, null, "blob").then(b => { mine.src = URL.createObjectURL(b); }).catch(() => {});
      top.appendChild(mine);
      const info = el("div", "min-width:0");
      const res = r.results || [];
      const best = r.best != null ? res[r.best] : null;
      if (!res.length) info.appendChild(el("div", "color:#9a9994", "這頁沒有 X 或 Pixiv 的結果。可以按 B 試特徵檢索，或按 N 沒找到。"));
      res.forEach((x, i) => {
        const line = el("div", "margin-bottom:4px;" + (x === best ? "" : "opacity:.6"));
        const a = el("a", "color:#8fd08a", x.kind === "x" ? `X @${x.handle}` : `Pixiv ${x.pixiv_id}`); a.href = x.url; a.target = "_blank"; a.rel = "noopener";
        line.appendChild(a);
        if (x.author) line.appendChild(el("span", "color:#9a9994", ` ${x.author}`));
        const m = { same: ["同一張", "#3f7a3b"], likely: ["很像", "#3f7a3b"], unlikely: ["不太像", "#b8453a"] }[x.match] || ["沒辦法比對", "#555"];
        const badge = el("span", `margin-left:6px;padding:1px 7px;border-radius:999px;font-size:12px;background:${m[1]}`,
          m[0] + (x.score != null ? ` ${Math.round(x.score * 100)}%` : ""));
        badge.title = x.verify_by ? `跟${x.verify_by}比對` : (x.verify_error || x.pixiv_error || x.x_error || "抓不到對方的圖");
        line.appendChild(badge);
        if (x.danbooru) line.appendChild(el("span", "margin-left:6px;font-size:12px;color:#e8b36a", "Danbooru 有收"));
        if (x.x_error || x.pixiv_error) line.appendChild(el("div", "font-size:12px;color:#9a9994", x.x_error || x.pixiv_error));
        info.appendChild(line);
      });
      top.appendChild(info); nodes.push(top);
      const checks = [];
      (r.tags || []).forEach((t, i) => {
        const row = el("label", "display:flex;gap:8px;align-items:baseline;margin:3px 0;cursor:pointer");
        const cb = el("input"); cb.type = "checkbox"; cb.checked = !!t.checked; checks.push([cb, t.name, t]);
        row.title = t.why || "";
        row.append(cb, el("span", "", `${i < 9 ? (i + 1) + " " : ""}${t.display || t.name}`),
          el("span", "color:#9a9994;font-size:12px", { artist: "創作者", character: "角色", copyright: "作品" }[t.category] || t.category));
        nodes.push(row);
      });
      if (r.error) nodes.push(el("div", "color:#ef8a7c;font-size:12px", r.error));
      const accept = () => checks.filter(([cb]) => cb.checked).map(([, n]) => n);
      // 沒勾任何標籤：選中的結果比對得上（同一張／很像）就只記出處；比對不上才擋下來
      const matched = r.best != null && r.results && ["same", "likely"].includes((r.results[r.best] || {}).match);
      const ok = () => { const a = accept(); if (a.length || matched) decide(mid, "found", a); else hint.textContent = "沒有勾選：確認不了。沒找到按 N，先跳過按 S。"; };
      const hint = el("div", "color:#e8b36a;font-size:12px;min-height:0");
      const bar = el("div", "margin-top:8px");
      bar.append(btn("確認勾選的 (Enter)", ok, true), btn("沒找到 (N)", () => decide(mid, "none")), btn("先跳過 (S)", () => decide(mid, "skip")));
      const bovw = location.pathname.startsWith("/search/color/");
      if (bovw) bar.append(btn("特徵檢索 (B)", () => { location.href = location.href.replace("/search/color/", "/search/bovw/"); }));
      bar.append(btn("暫停 (Esc)", pause));
      nodes.push(bar, hint);
      render(...nodes);
      keys = { Enter: ok, n: () => decide(mid, "none"), s: () => decide(mid, "skip"), Escape: pause };
      if (r.auto === "accept") {   // 設定「自動確認」：比對到同一張 → 全部勾選，1.5 秒後自動確認（按任何鍵停）；沒有標籤就只記出處
        checks.forEach(([cb, , t]) => { cb.checked = t.auto !== false; });   // 2.7：角色已經標齊時，標籤推的其他角色不勾
        const note = el("div", "color:#8fd08a;font-size:12px;margin-top:6px", (r.auto_note ? r.auto_note + "。" : "") + (checks.length ? "比對到同一張：全部勾選，1.5 秒後自動確認、下一張（按任何鍵停下來）"
          : "比對到同一張，但沒有標籤可加：1.5 秒後只記下出處、下一張（按任何鍵停下來）"));
        box.appendChild(note);
        let stopped = false;
        const stop = () => { stopped = true; held = true; note.textContent = "已停下來，請自己決定。"; document.removeEventListener("keydown", stop, true); box.removeEventListener("mousedown", stop, true); };
        document.addEventListener("keydown", stop, true); box.addEventListener("mousedown", stop, true);
        setTimeout(() => { document.removeEventListener("keydown", stop, true); box.removeEventListener("mousedown", stop, true); if (!stopped && box.isConnected) ok(); }, 1500);
      }
      if (r.auto === "none" || r.auto === "skip") {   // 都不太像（全自動：比不了、比對出錯也是）：1.5 秒後自動記「沒找到」／先跳過、下一張（按任何鍵就停下來自己看）
        const out = r.auto;
        const note = el("div", "color:#e8b36a;font-size:12px;margin-top:6px", (r.auto_note || "都不太像：記「沒找到」") + (out === "skip" ? "，1.5 秒後先跳過、下一張" : "，1.5 秒後下一張") + "（按任何鍵停下來）");
        box.appendChild(note);
        let stopped = false;
        const stop = () => { stopped = true; held = true; note.textContent = "已停下來，請自己決定。"; document.removeEventListener("keydown", stop, true); box.removeEventListener("mousedown", stop, true); };
        document.addEventListener("keydown", stop, true); box.addEventListener("mousedown", stop, true);
        setTimeout(() => { document.removeEventListener("keydown", stop, true); box.removeEventListener("mousedown", stop, true); if (!stopped && box.isConnected) decide(mid, out); }, 1500);
      }
      if (bovw) keys.b = () => { location.href = location.href.replace("/search/color/", "/search/bovw/"); };
      checks.slice(0, 9).forEach(([cb], i) => { keys[String(i + 1)] = () => { cb.checked = !cb.checked; }; });
    }

    function pause() {
      ss.set({ ...ss.get(), paused: true }); running(false);
      keys = { Enter: resume };
      render(el("div", "font-weight:700", "ascii2d 連續搜：暫停中"), btn("繼續 (Enter)", resume, true));
    }
    function resume() { const s = ss.get(); delete s.paused; ss.set(s); start(); }

    async function start() {
      let st;
      try { st = await gm("GET", "/api/ascii2d/ext/state"); } catch (e) {          // 圖庫沒開：不打擾（全自動在跑的時候：等程式回來）
        const s0 = ss.get();
        if (s0.full && s0.mid && !s0.paused) { render(head("全自動：連不到 Pickle Jar，30 秒後再試")); setTimeout(start, 30000); }
        return; }
      FULL = !!st.full;
      const s = ss.get();
      if (s.full !== FULL) ss.set({ ...s, full: FULL });
      if (!st.active && !s.mid) return;                  // 沒有在連續搜：什麼都不顯示
      if (s.paused) return pause();
      const challenge = /請稍候|Just a moment|しばらくお待ち/i.test(document.title) || document.querySelector("#challenge-form,.cf-turnstile,#cf-challenge-running");
      challenged = !!challenge;
      if (challenge) return render(head("ascii2d 連續搜", st), el("div", "", "請先完成 Cloudflare 驗證（點一下就好）。通過後會自動接著處理這一張。"));
      const isResult = /^\/search\/(color|bovw)\//.test(location.pathname);
      if (isResult && s.mid) {
        render(head("讀取結果、比對中…", st));
        const boxes = [...document.querySelectorAll(".item-box")];
        const items = (boxes.length ? boxes : [document.body]).map(b => ({
          thumb: (b.querySelector("img") || {}).src || null, text: (b.innerText || "").slice(0, 400),
          links: [...b.querySelectorAll("a[href]")].slice(0, 30).map(a => ({ href: a.href, text: (a.textContent || "").trim().slice(0, 80) })) }));
        // 有 X／Pixiv 連結的前幾個結果：把 ascii2d 上的縮圖一起送回去（對方的圖被刪、R-18 要登入時，用它來比對）
        const toData = blob => new Promise(res => { const fr = new FileReader(); fr.onload = () => res(fr.result); fr.onerror = () => res(null); fr.readAsDataURL(blob); });
        // 同一篇作品在 ascii2d 上常常連續出現好幾次（不同尺寸）：每篇只抓第一個的縮圖，名額留給後面不同的結果
        // （以前 6 個同一篇 Pixiv 把 5 個名額用光，後面真正要比的結果「沒辦法比對」，自動跳過就停下來）
        // 2.9：8 張縮圖同時抓（以前一張抓完才抓下一張）
        const done = new Set(), want = [];
        for (const it of items) {
          const m = it.links.map(l => l.href.match(/(?:twitter|x)\.com\/[^/]+\/status\/(\d+)|pixiv\.net\/(?:\w+\/)?artworks\/(\d+)/)).find(Boolean);
          if (want.length >= 8 || !it.thumb || !m) continue;
          const key = m[1] ? "x" + m[1] : "p" + m[2];
          if (done.has(key)) continue;
          done.add(key); want.push(it);
        }
        await Promise.all(want.map(async it => {
          try { const r = await fetch(it.thumb); if (r.ok) { const b = await r.blob(); if (b.size < 250000) it.thumb_data = await toData(b); } } catch (e) {}
        }));
        let debug = null;
        try { if (!localStorage.getItem("libA2dDbg")) { debug = boxes.slice(0, 4).map(b => b.outerHTML).join("\n").slice(0, 30000); localStorage.setItem("libA2dDbg", "1"); } } catch (e) {}
        try { await gm("POST", "/api/ascii2d/ext/result", { mid: s.mid, page_url: location.href, items, debug }); }
        catch (e) {
          if (FULL) return retry("send", s.mid, 10000, 3, () => location.reload());
          keys = { Enter: () => location.reload() };
          return render(head("ascii2d 連續搜", st), el("div", "color:#ef8a7c", "送不回圖庫：" + e.message), btn("再試一次 (Enter)", () => location.reload(), true)); }
        for (let i = 0; i < 180; i++) {                   // 比對通常一兩秒（縮圖先比，不像的不再查）；每 0.5 秒問一次
          const r = await gm("GET", `/api/ascii2d/ext/result/${s.mid}`);
          if (r.status === "ready") return showResult(s.mid, r);
          await new Promise(res => setTimeout(res, 500));
        }
        if (FULL) return retry("wait", s.mid, 5000, 2, () => location.reload());   // 再等兩輪；還是不行就先跳過
        return render(head("ascii2d 連續搜", st), el("div", "color:#ef8a7c", "比對太久了，按 Enter 重新整理這頁再試"));
      }
      // 送出去了卻不是結果頁（ascii2d 出錯、太多人在用、上傳失敗）：全自動就等一下再送同一張，越等越久，三次都不行就先跳過
      if (FULL && s.mid && !s.go && !isResult) {
        // 回到首頁（重新整理、上傳被退回）：3 秒後接著送；其他頁（ascii2d 出錯、太多人在用）：20、40、60 秒後再送
        const base = location.pathname === "/" ? 3000 : 20000;
        return retry("page", s.mid, base * ((s["try_page_" + s.mid] || 0) + 1), 3, next);
      }
      if (s.go && s.mid) { ss.set({ ...s, go: false }); return next(); }   // 剛剛那頁沒有上傳表單，回到首頁接著送
      if (FULL && running() && !isResult) { render(head("全自動：接著做…", st)); return setTimeout(next, 1500); }   // 看門狗重新整理後、或結果頁之外的地方：接著做
      keys = { Enter: next, Escape: pause };
      render(head("ascii2d 連續搜", st), el("div", "color:#9a9994", "一張一張找 X／Pixiv 的出處。找到的會跟你的圖比對，你確認才算。"),
        btn(s.mid ? "繼續 (Enter)" : "開始 (Enter)", next, true), btn("暫停 (Esc)", pause));
    }
    start();
  }
  if (location.hostname === "ascii2d.net") { a2d(); return; }

  // 貼文頁：右側一個大按鈕（離角落遠一點，比較好點）
  const main = document.createElement("button");
  main.textContent = "⤓ 存到圖庫";
  main.style.cssText = css + ";right:24px;bottom:90px;padding:13px 22px;font-size:15px;background:#3a7d44;color:#fff;display:none";
  main.onclick = () => (IS_PIXIV && pidOf(location.href) ? pixivSave : IS_NH ? nhSave : send)(location.href, main, () => { main.textContent = "✓ 已存到圖庫"; });
  const mount = () => { if (!main.isConnected) document.documentElement.appendChild(main); };   // 掛在 <html>：換頁重畫 body 也不會被清掉
  mount();
  let last = "";
  setInterval(() => {
    mount();
    if (location.href !== last) {
      last = location.href;
      main.style.display = isPost(last) ? "" : "none";
      main.textContent = saved.has(keyOf(last)) ? "✓ 已存到圖庫" : "⤓ 存到圖庫";
    }
  }, 700);

  // 瀏覽推薦、搜尋結果、排行榜時：滑鼠移到縮圖上，右上角出現「⤓ 圖庫」（不用點進去）
  if (!IS_X) {
    const hov = document.createElement("button");
    hov.style.cssText = css + ";padding:7px 13px;font-size:13px;background:#3a7d44;color:#fff;display:none";
    document.documentElement.appendChild(hov);
    let cur = null, hideT = 0;
    // Pixiv 作品頁裡展開的每一頁大圖：「⤓ 存這張」只存那一頁
    const pageImg = el => {
      if (!IS_PIXIV || !el || el.tagName !== "IMG") return null;
      const pid = pidOf(location.href), m = (el.currentSrc || el.src || "").match(/i\.pximg\.net\/.*?\/(\d+)(?:-[0-9a-f]{32})?_p(\d+)/);
      if (!pid || !m || m[1] !== pid || el.width < 150 || el.height < 150) return null;
      return { a: el, url: location.href.split("#")[0], img: el, pages: [+m[2]] };
    };
    const thumbLink = el => {
      const pg = pageImg(el);
      if (pg) return pg;
      const a = el && el.closest && el.closest("a[href]");
      if (!a || a === hov) return null;
      const url = new URL(a.getAttribute("href"), location.href).href;
      if (!isPost(url) || url.split("#")[0] === location.href.split("#")[0]) return null;
      const img = [...a.querySelectorAll("img")].sort((p, q) => q.width * q.height - p.width * p.height)[0];
      if (!img || img.width < 60 || img.height < 60) return null;
      return { a, url, img };
    };
    const place = () => {
      if (!cur || !cur.img.isConnected) { hov.style.display = "none"; return; }
      const r = cur.img.getBoundingClientRect();
      if (r.bottom < 0 || r.top > innerHeight) { hov.style.display = "none"; return; }
      hov.style.display = "";
      hov.style.left = Math.max(4, r.right - hov.offsetWidth - 6) + "px";
      hov.style.top = Math.max(4, r.top + 6) + "px";
    };
    document.addEventListener("mouseover", e => {
      if (e.target === hov) { clearTimeout(hideT); return; }
      const t = thumbLink(e.target);
      if (t) {
        clearTimeout(hideT);
        if (!cur || cur.a !== t.a) {
          cur = t;
          const done = saved.has(keyOf(t.url) + (t.pages ? ":p" + t.pages[0] : ""));
          hov.textContent = done ? "✓ 已存" : t.pages ? `⤓ 存這張（第 ${t.pages[0] + 1} 頁）` : "⤓ 圖庫";
          hov.style.background = done ? "#555" : "#3a7d44";
          hov.disabled = false;
        }
        place();
      } else if (cur) {
        clearTimeout(hideT);
        hideT = setTimeout(() => { hov.style.display = "none"; cur = null; }, 250);
      }
    }, true);
    addEventListener("scroll", place, { passive: true, capture: true });
    hov.addEventListener("click", e => {
      e.preventDefault(); e.stopPropagation();
      if (!cur) return;
      const c = cur;
      const ok = () => { if (cur === c) { hov.textContent = "✓ 已存"; hov.style.background = "#555"; } };
      if (c.pages) send(c.url, hov, ok, c.pages);
      else if (IS_PIXIV && pidOf(c.url)) pixivSave(c.url, hov, ok);   // 列表裡的多頁作品：一樣先選頁
      else if (IS_NH) nhSave(c.url, hov, ok);
      else send(c.url, hov, ok);
    }, true);
  }

  // X 的時間軸：每則有圖的貼文旁邊一個小按鈕（不用點進去）
  if (IS_X) {
    new MutationObserver(() => {
      document.querySelectorAll('article[data-testid="tweet"]:not([data-il])').forEach(a => {
        // X 會先畫出貼文、圖片晚一點才載入：還沒準備好就先不標記，下次 DOM 變動再檢查
        if (!a.querySelector('[data-testid="tweetPhoto"], video')) return;
        const link = [...a.querySelectorAll('a[href*="/status/"]')].find(x => x.querySelector("time"));
        const bar = [...a.querySelectorAll('[role="group"]')].pop();
        if (!link || !bar) return;
        a.dataset.il = "1";
        const b = document.createElement("button");
        const url = new URL(link.getAttribute("href"), location.origin).href;
        b.textContent = saved.has(keyOf(url)) ? "✓ 已存" : "⤓ 圖庫"; b.title = "存到 Pickle Jar";
        b.style.cssText = "border:1px solid rgba(128,128,128,.4);background:none;color:inherit;border-radius:999px;padding:4px 12px;font:600 13px system-ui;cursor:pointer;margin-left:6px";
        b.onclick = e => { e.preventDefault(); e.stopPropagation(); send(url, b, () => { b.textContent = "✓ 已存"; }); };
        bar.appendChild(b);
      });
    }).observe(document.body, { childList: true, subtree: true });
  }
  // ===== X 媒體頁：拿這位畫師的全部圖跟圖庫比對（圖庫的「畫師作品比對 › X」）=====
  // 打開 x.com/<帳號>/media：圖庫裡有這位畫師 → 左下角出現按鈕。按了（或連續模式）就慢慢往下捲、把圖片網址送回本機；
  // 本機下載小圖比對，找到的記下出處（推文）。看完、或沒有新圖了就結束；連續模式自動開下一位。
  if (IS_X) {
    const BASE = API.replace(/\/api\/.*$/, "");
    const gx = (path, body) => new Promise((ok, bad) => GM_xmlhttpRequest({
      method: "POST", url: BASE + path, headers: { "X-Import-Key": KEY, "X-Script-Version": "3.3", "Content-Type": "application/json" },
      data: JSON.stringify(body || {}), timeout: 60000,
      onload: r => { let d = {}; try { d = JSON.parse(r.responseText || "{}"); } catch (e) {} r.status >= 400 ? bad(new Error(d.detail || ("HTTP " + r.status))) : ok(d); },
      onerror: () => bad(new Error("連不到 Pickle Jar（程式有開著嗎？）")), ontimeout: () => bad(new Error("逾時")),
    }));
    const XS = "libXsweep";
    const xs = { get: () => { try { return JSON.parse(sessionStorage.getItem(XS) || "{}"); } catch (e) { return {}; } },
                 set: v => { try { sessionStorage.setItem(XS, JSON.stringify(v)); } catch (e) {} } };
    const panel = document.createElement("div");
    panel.style.cssText = "position:fixed;z-index:2147483647;left:16px;bottom:16px;width:320px;background:#1b1c1d;color:#ecebe7;font:13px/1.5 system-ui,sans-serif;"
      + "border-radius:14px;box-shadow:0 12px 40px rgba(0,0,0,.45);padding:12px 14px;display:none";
    const pbtn = (label, fn, primary) => { const b = document.createElement("button"); b.textContent = label;
      b.style.cssText = "margin:6px 6px 0 0;padding:6px 12px;border:0;border-radius:999px;cursor:pointer;font:600 13px system-ui;" + (primary ? "background:#3f7a3b;color:#fff" : "background:#2e2f33;color:#ecebe7");
      b.onclick = fn; return b; };
    const show = (...nodes) => { panel.replaceChildren(...nodes); panel.style.display = ""; if (!panel.isConnected) document.documentElement.appendChild(panel); };
    const line = (t, st) => { const d = document.createElement("div"); d.textContent = t; if (st) d.style.cssText = st; return d; };
    const handleOf = () => { const m = location.pathname.match(/^\/([A-Za-z0-9_]{1,15})\/media\/?$/); return m ? m[1] : null; };
    let run = null, checked = "";
    async function offer() {
      const h = handleOf();
      if (!h) { if (!run) panel.style.display = "none"; checked = ""; return; }
      if (run || checked === h) return;
      checked = h;
      let r; try { r = await gx("/api/sweep/ext/x/check", { handle: h }); } catch (e) { return; }
      if (!r.artist) { panel.style.display = "none"; return; }
      if (location.hash.includes("pjsweep")) xs.set({ auto: true, handle: h });   // 圖庫「打開名單第一位」開的：直接開始、看完自動下一位
      const st = xs.get();
      if (st.auto && st.handle && st.handle.toLowerCase() === h.toLowerCase()) return start(h, r.artist, true);
      show(line(`圖庫裡有這位畫師：${r.artist}`, "font-weight:700"), line("往下捲完他的媒體頁，把圖跟圖庫比對（找到的記下出處）。", "color:#9a9994"),
           pbtn("開始比對", () => start(h, r.artist, false), true), pbtn("✕", () => { panel.style.display = "none"; }));
    }
    async function start(h, artist, auto) {
      if (run) return;
      const items = new Map(); let sent = 0, idle = 0, stopped = false, lastY = -1;
      run = { stop: () => { stopped = true; } };
      const status = line(""), stopB = pbtn("停止", () => { stopped = true; xs.set({}); });
      const draw = msg => show(line(`比對 @${h}（${artist}）`, "font-weight:700"), status, stopB);
      const flush = async final => {
        const batch = [...items.values()].slice(sent);
        if (!batch.length && !final) return;
        try { await gx("/api/sweep/ext/x/items", { handle: h, items: batch, final }); sent += batch.length; }
        catch (e) { status.textContent = "送不回圖庫：" + e.message; }
      };
      draw();
      while (!stopped) {
        document.querySelectorAll('a[href*="/status/"] img[src*="pbs.twimg.com/media/"]').forEach(img => {
          const a = img.closest('a[href*="/status/"]'); const m = a && a.getAttribute("href").match(/\/status\/(\d+)/);
          const key = (img.src.match(/\/media\/([A-Za-z0-9_-]+)/) || [])[1];
          if (m && key && !items.has(key)) items.set(key, { tweet_id: m[1], url: img.src });
        });
        status.textContent = `收集到 ${items.size} 張，已送 ${sent} 張（比對在背景進行）`;
        if (items.size - sent >= 40) await flush(false);
        if (items.size >= 1500) break;
        const y = scrollY;
        idle = (y === lastY) ? idle + 1 : 0; lastY = y;
        if (idle >= 6) break;                                // 捲不動了＝到底
        scrollBy(0, innerHeight * 0.85);
        await new Promise(r => setTimeout(r, 1300 + Math.random() * 700));
      }
      await flush(true);
      run = null;
      if (stopped) { show(line(`已停止：@${h} 送了 ${sent} 張`), pbtn("關閉", () => { panel.style.display = "none"; })); return; }
      if (auto) {
        let n; try { n = await gx("/api/sweep/ext/x/next", {}); } catch (e) { n = {}; }
        if (n.handle) { xs.set({ auto: true, handle: n.handle }); status.textContent = `@${h} 完成（${sent} 張）。下一位：@${n.handle}（剩 ${n.left} 位）…`;
          setTimeout(() => { location.href = `https://x.com/${n.handle}/media`; }, 2500); return; }
        xs.set({});
        return show(line("名單上的畫師都看完了", "font-weight:700"), line("回到圖庫看找到的結果。"), pbtn("關閉", () => { panel.style.display = "none"; }));
      }
      show(line(`@${h} 完成：送了 ${sent} 張`, "font-weight:700"), line("比對在背景進行，找到的會出現在圖庫（來源：X 畫師媒體）。", "color:#9a9994"),
           pbtn("連續模式：看名單下一位", async () => { let n; try { n = await gx("/api/sweep/ext/x/next", {}); } catch (e) { return toast(e.message); }
             if (!n.handle) return toast("名單是空的：到圖庫 › 畫師作品比對 › X 排名單", false);
             xs.set({ auto: true, handle: n.handle }); location.href = `https://x.com/${n.handle}/media`; }, true),
           pbtn("關閉", () => { panel.style.display = "none"; }));
    }
    setInterval(offer, 1200);
  }
  GM_registerMenuCommand("存到圖庫（這一頁）", () => send(location.href, null));
})();
"""


@router.get("/api/fetch-url/userscript.user.js")
def userscript(request: Request, sec_fetch_dest: str | None = Header(default=None)):
    """產生帶有配對金鑰的腳本。純文字＋nosniff：別的網站不能用 <script> 載入來偷金鑰。"""
    if sec_fetch_dest in ("script", "worker", "sharedworker", "serviceworker"):
        raise HTTPException(403)
    host = request.headers.get("host") or "127.0.0.1:7860"
    body = USERSCRIPT.replace("__API__", json.dumps(f"http://{host}/api/fetch-url/ext")).replace("__KEY__", json.dumps(importer.import_key())) \
        .replace("__UPDATE__", f"http://{host}/api/fetch-url/userscript.user.js")
    return PlainTextResponse(body, headers={"X-Content-Type-Options": "nosniff", "Cache-Control": "no-store"})
