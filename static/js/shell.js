// ES module（2026-10）：用到別的檔案的東西都要在這裡 import；瀏覽器會在載入時檢查名字對不對。
import { $, $$, NSFW_LABEL, NSFW_MODES, QUERY_HELP, S, api, apiNsfw, closeModal, esc, fmtEta, fmtN, fmtSize, hud, modal, modalOpen, onEvent, sseOk, store, svg, toast } from "./core.js";
import { loadMore, render, runSearch } from "./grid.js";
import { drawWindow } from "./layout.js";
import { clearSel, selectAll } from "./selection.js";
import { doAction, doAction as doActionIds, recordUse, removeCells, shareCopy, undo } from "./actions.js";
import { LIB_VIEWS, loadBuckets } from "./unrec.js";
import { RF, dropImages, focusKey } from "./review.js";
import { batchKey } from "./batch.js";
import { V, closeViewer, markDirty, openViewer, stepViewer, toggleVDrawer, toggleZoom, vKey } from "./viewer.js";
import { openPalette } from "./palette.js";
import { drawHealth, icloudClear, openDiag, openHealth } from "./pages.js";
import { openSettings } from "./settings.js";
/* ============ 路由 ============ */
export function go(view, arg) {
  const hash = arg ? `#/${view}/${encodeURIComponent(arg)}` : `#/${view}`;
  if (location.hash === hash) { S.noRestore = true; route(); } else location.hash = hash;   // 點目前這一頁＝重新整理、回到最上面
}
const VIEWS = ["albums", "album", "official", "home", "review", "unrecognized", "all", "characters", "artists", "tags", "tag", "series", "search", "videos", "nsfw",
  "duplicates", "excluded", "errors", "noartist", "system", "settings"];
const REMOTE_HIDE = ["home", "review", "unrecognized", "noartist", "system", "settings", "duplicates", "excluded", "errors"];
export function route() {
  $("#healthpop")?.remove();
  clearTimeout(saveT);                       // 上一頁還沒存的捲動位置，不能存到這一頁的紀錄上
  const back = S.noRestore ? null : history.state?.scroll;   // 按「上一頁」回來（或重新整理）：這一筆紀錄有存位置
  S.noRestore = false;
  let [, view = "home", arg] = location.hash.split("/");
  // 打錯網址（#/settings、舊書籤）→ 回整理中心，不要顯示一個莫名其妙的圖庫
  if (view && !VIEWS.includes(view)) { location.replace(S.remote ? "#/all" : "#/home"); return; }
  // 手機／平板（區網，只看）：整理用的頁面不開，回圖庫
  if (S.remote && (!view || REMOTE_HIDE.includes(view))) { location.replace("#/all"); return; }
  if (view === "noartist") { view = "unrecognized"; arg = "artist"; }
  S.view = view || "home";
  if (view === "search") { S.q = decodeURIComponent(arg || ""); S.view = "all"; }
  S.tag = view === "tag" ? decodeURIComponent(arg || "") : null;
  if (view === "series") {
    const k = decodeURIComponent(arg || "");
    if (k !== S.seriesKey) { S.seriesKey = k; S.seriesChar = ""; S.q = ""; S.seriesMiss = ""; }
    if (S.nextSeriesMiss) { S.seriesMiss = S.nextSeriesMiss; S.seriesChar = ""; S.nextSeriesMiss = ""; }   // 從未識別「依作品」點過來
  }
  // 側欄點「未識別」：回到上次看的分頁，但「多人圖缺角色」是從收件匣點進去的支線，不記（不然側欄寫 2,741 張、打開卻是另外 613 張）
  S.miss = view === "unrecognized" ? (arg || ((m) => m === "people" ? "all" : m)(store.get("miss", "all"))) : "all";
  S.bucket = view === "unrecognized" ? store.get("ubucket", "") : "";
  // 分堆只在「完全未識別」、原創／AI 生成只在「缺創作者」：換分頁時不要把看不到的篩選帶過去
  if (S.bucket && ((S.bucket.startsWith("p_") && S.miss === "artist") || (["orig", "aigen"].includes(S.bucket) && S.miss !== "artist"))) { S.bucket = ""; store.set("ubucket", ""); }
  if (view === "unrecognized") store.set("miss", S.miss);
  if (view !== "tag" || S.srcTag !== S.tag) { S.src = ""; S.srcTag = S.tag; }
  if (S.view !== S.lastView || S.tag !== S.lastTag) S.mtype = "";   // 媒材篩選不跨頁保留（不然換到別的角色會莫名其妙沒圖）
  S.lastView = S.view; S.lastTag = S.tag;
  S.selected.clear(); S.anchor = null;
  $("#sidebar").classList.remove("open");
  S.albumId = view === "album" ? +decodeURIComponent(arg || "0") : null;
  const navView = S.view === "series" ? "characters" : S.view === "album" ? "albums" : LIB_VIEWS.includes(S.view) ? "all" : S.view;
  $$("#nav a, #system-btn").forEach(a => a.classList.toggle("on", a.dataset.view === navView && !(S.view === "all" && S.q)));
  $("#settings-btn")?.classList.toggle("on", S.view === "settings");
  $$(".taglist a").forEach(a => a.classList.toggle("on", a.dataset.tag === S.tag || (a.dataset.q != null && a.dataset.q === S.q && S.view === "all")));
  render();
  if (back && back.sy > 0) restoreScroll(back);
}
window.addEventListener("hashchange", route);

/* ---- 返回上一頁時回到原本的位置 ----
   以前每次換頁都整頁重畫、捲到最上面：從圖庫點進角色頁再按返回，要重新捲很久。
   現在捲動時把位置（和畫面最上面那一列是哪張圖）存在瀏覽器的這一筆歷史紀錄裡（history.state），
   返回時：圖庫格子先載入到那張圖、讓它停在原本的高度；書架、首頁這類頁面等內容長出來後捲回原本的位置。
   恢復途中你自己滾動或按鍵就停止，不跟你搶。 */
let saveT = 0;
function scrollState() {
  const c = $("#content"), grid = $("#grid");
  const st = { sy: Math.round(c.scrollTop), n: S.items?.length || 0 };
  if (S.layout && grid && S.items?.length) {
    const top = c.scrollTop - grid.offsetTop;
    const r = S.layout.rows.find(r => r.y + r.h > top);
    if (r && S.items[r.cells[0].i]) { st.aid = S.items[r.cells[0].i].id; st.aoff = Math.round(r.y + grid.offsetTop - c.scrollTop); }
  }
  return st;
}
export function saveScroll() {
  if (S.restoring) return;      // 還在捲回原位的途中：不要把半路的位置存起來
  try { history.replaceState({ ...(history.state || {}), scroll: scrollState() }, ""); } catch { }
}
$("#content").addEventListener("scroll", () => { clearTimeout(saveT); saveT = setTimeout(saveScroll, 150); }, { passive: true });
document.addEventListener("pointerdown", saveScroll, true);     // 點連結換頁前先存（捲完馬上點，計時器還沒到）
async function restoreScroll(st) {
  const gen = S.gen, c = $("#content"), t0 = Date.now();
  let user = false;
  const stop = () => { user = true; };
  const evs = [[c, "wheel"], [c, "touchstart"], [c, "mousedown"], [window, "keydown"]];
  evs.forEach(([el, e]) => el.addEventListener(e, stop, { passive: true }));
  S.restoring = true; S.wantN = st.n || 0;
  const done = () => gen !== S.gen || user;
  try {
    while (Date.now() - t0 < 8000 && !done()) {
      await new Promise(r => setTimeout(r, 60));
      if (done()) return;
      const grid = $("#grid");
      if (st.aid != null) {                       // 圖庫格子：找到那張圖
        if (!(S.layout && grid && S.items.length)) continue;
        const i = S.items.findIndex(x => x.id === st.aid);
        const r = i >= 0 ? S.layout.rows.find(r => r.cells.some(cc => cc.i === i)) : null;
        if (r) { c.scrollTop = Math.max(0, r.y + grid.offsetTop - (st.aoff || 0)); drawWindow(true); return; }
        if (S.done || S.items.length >= (st.n || 0) + 2000) { c.scrollTop = st.sy; drawWindow(true); return; }   // 那張不在了（被標走、刪掉）
        if (S.loading !== S.gen) loadMore(gen);
        continue;
      }
      if (c.scrollHeight >= st.sy + c.clientHeight - 4) {   // 書架、首頁、卡片：內容夠高了
        c.scrollTop = st.sy;
        if (Math.abs(c.scrollTop - st.sy) < 4) return;
      }
    }
    if (!done()) c.scrollTop = st.sy;            // 時間到：盡量捲回去
  } finally {
    S.restoring = false; S.wantN = 0;
    evs.forEach(([el, e]) => el.removeEventListener(e, stop));
  }
}

/* ============ 側欄 ============ */
/* ---- NSFW 封面：任何地方的縮圖（書架、首頁、卡片…）只要是 NSFW 就模糊／隱藏 ---- */
let NSFW_IDS = new Set(), nsfwAt = 0;
export async function loadNsfwIds(force) {
  if (!force && Date.now() - nsfwAt < 15000) return;
  nsfwAt = Date.now();
  try { NSFW_IDS = new Set(await api("/api/nsfw-ids")); markNsfw(document.body); } catch { }
}
function markNsfw(root) {
  if (!root || !root.querySelectorAll) return;
  const imgs = root.tagName === "IMG" ? [root] : root.querySelectorAll('img[src*="/t/"]');
  imgs.forEach(img => { const m = /\/(?:thumb|t)\/(\d+)/.exec(img.getAttribute("src") || ""); if (m) img.classList.toggle("nz", NSFW_IDS.has(+m[1])); });
}
new MutationObserver(ms => {
  for (const m of ms) {
    if (m.target.id === "grid") continue;   // 圖庫格子本身就有 .nsfw 處理，捲動時不用每格再查
    m.addedNodes.forEach(n => n.nodeType === 1 && markNsfw(n));
  }
})
  .observe(document.body, { childList: true, subtree: true });
// 連續操作（例如確認好幾張）會連叫很多次：150ms 內的合併成一次，不要每次都重算所有數字
let _sbTimer = null, _sbWait = [];
export function loadSidebar() {
  clearTimeout(_sbTimer);
  return new Promise(res => { _sbWait.push(res); _sbTimer = setTimeout(async () => { const w = _sbWait; _sbWait = []; await _loadSidebar(); w.forEach(f => f()); }, 150); });
}
async function _loadSidebar() {
  loadNsfwIds();
  try {
    const d = await api(`/api/sidebar?nsfw=${apiNsfw()}${store.get("showAlbumPages", false) ? "" : "&noalbum=1"}`);
    S.sbCounts = d.counts;
    $$("#libtabs button").forEach(b => { const n = d.counts[{ all: "all", videos: "videos", nsfw: "nsfw", official: "official", duplicates: "duplicates", excluded: "excluded", errors: "errors" }[b.dataset.lv]]; const el = b.querySelector("b"); if (el && n != null) el.textContent = fmtN(n); });
    for (const [k, v] of Object.entries(d.counts)) {
      // 窄側欄只放得下短數字：1,183 → 1.2k（滑鼠指著看完整數字）
      $$(`[data-count="${k}"]`).forEach(el => { el.textContent = v == null ? "" : v.toLocaleString(); el.classList.toggle("zero", !v); el.closest("a")?.setAttribute("title", `${el.closest("a").querySelector(".lb")?.textContent || ""} · ${v == null ? "" : v.toLocaleString()}`); el.dataset.short = v == null ? "" : v >= 10000 ? Math.round(v / 1000) + "k" : v >= 1000 ? (v / 1000).toFixed(1).replace(/\.0$/, "") + "k" : String(v); });
    }
    const list = (arr, empty) => arr.length ? arr.map(t => `<a data-tag="${esc(t.name)}" class="${t.name === S.tag ? "on" : ""}" title="${esc(t.name)}"><span>${esc(t.display || t.name)}</span><i>${t.n}</i></a>`).join("") : empty;
    $("#pinned").innerHTML = list(d.pinned, "");
    // 快速標籤：梗圖、真人、截圖…（數字鍵 1–9）
    S.quick = d.quick || [];
    { const qf = $("#qtfacet"); if (qf) qf.innerHTML = quickFacetHTML(); }
    $("#quick").innerHTML = S.quick.length ? S.quick.map(q => `<a data-tag="${esc(q.name)}" class="${q.name === S.tag ? "on" : ""}" title="選取圖片後按 ${q.key}（或在大圖裡按 ${q.key}）加上／拿掉"><span><kbd class="qk">${q.key}</kbd> ${esc(q.name)}</span><i>${q.n}</i></a>`).join("")
      : `<div class="hint">在設定裡加上常用的分類（梗圖、真人…）</div>`;
    // 最近看過：預設 5 個（以前 12 個、大多只有 1 張，佔掉大半側欄），可以展開
    $("#recent").innerHTML = list(d.recent.slice(0, 12), "");
    $("#recent-box").classList.toggle("hidden", !d.recent.length);
    { const rb = $("#recent-box"); if (!rb.dataset.b) { rb.dataset.b = 1; rb.open = store.get("recentOpen", false); rb.addEventListener("toggle", () => store.set("recentOpen", rb.open)); } }
    $("#saved").innerHTML = d.saved.length ? d.saved.map(x => `<a data-q="${esc(x.query)}" data-sv="${esc(x.view || "all")}" class="${x.query === S.q ? "on" : ""}" title="${esc(x.query)}"><span>${esc(x.name)}</span><i data-del-saved="${x.id}" title="移除">×</i></a>`).join("")
      : "";
  } catch (e) { console.warn(e); }
}
$("#sidebar").addEventListener("click", async e => {
  if (e.target.closest("[data-recent-more]")) { store.set("recentOpen", !store.get("recentOpen", false)); _loadSidebar(); return; }
  const del = e.target.closest("[data-del-saved]");
  if (del) { e.stopPropagation(); await api(`/api/saved/${del.dataset.delSaved}`, undefined, "DELETE"); loadSidebar(); return; }
  const a = e.target.closest("a"); if (!a) return;
  if (a.dataset.view) { if (a.dataset.view === "all") S.q = ""; go(a.dataset.view); }
  else if (a.dataset.tag) go("tag", a.dataset.tag);
  else if (a.dataset.q != null) runSearch(a.dataset.q, a.dataset.sv);
});
$("#sidebar").addEventListener("mouseover", e => { const b = e.target.closest("#nav b.quiet"); if (b) b.closest("a").title = `${b.closest("a").textContent.replace(/[\d,]+$/, "").trim()} · ${b.textContent}`; });
$("#menu-btn").onclick = () => $("#sidebar").classList.toggle("open");

/* ============ 狀態列（背景工作） ============ */
let lastRunning = false, lastOnline = false, polling = false;
// 狀態列：事件流連著就等它推（有變才推），連不上才每 1.5–4 秒自己問（b93）
export async function pollStatus() {
  if (sseOk()) { polling = false; return; }
  polling = true;
  try { drawStatus(await api("/api/status")); }
  catch { $("#st-text").textContent = "伺服器未連線"; $("#status").classList.add("err"); }
  setTimeout(pollStatus, lastRunning || lastOnline ? 1500 : 4000);
}
onEvent("status", s => drawStatus(s));
onEvent("_down", () => { if (!polling) pollStatus(); });
function drawStatus(s) {
  try {
    const st = $("#status");
    const sd = $("#settings-btn .sdot"); if (sd) { sd.className = "sdot" + (s.running || s.online?.running ? " busy" : s.error ? " err" : ""); }
    $("#settings-btn").title = `設定 · ${s.running ? `${s.phase} ${s.done}/${s.total}` : s.error ? "上次工作失敗" : "本地圖庫"}`;
    st.classList.toggle("busy", s.running);
    st.classList.toggle("err", !!s.error);
    if (s.running) {
      $("#st-text").textContent = s.total ? `${s.phase} ${s.done.toLocaleString()}/${s.total.toLocaleString()}` : s.phase;
      $("#st-bar").style.width = s.total ? (100 * s.done / s.total).toFixed(1) + "%" : "10%";
      $("#st-sub").textContent = [s.paused ? "已暫停" : "", s.rate ? `${s.rate} 張/秒` : "", s.eta ? `剩 ${fmtEta(s.eta)}` : "", s.provider ? s.provider.replace("ExecutionProvider", "") : ""].filter(Boolean).join(" · ");
    } else {
      $("#st-text").textContent = s.error ? "上次工作失敗" : s.pending ? `待處理 ${s.pending}` : "本地圖庫";
      $("#st-sub").textContent = s.error ? s.error : "僅此裝置" + (s.watching ? " · 監看中" : "");
    }
    drawJobPill(s);
    drawHealth(s);
    const o = s.online || {};
    $("#st-online").textContent = o.running ? `線上：${o.phase} ${o.done}/${o.total}${o.found ? ` · 找到 ${o.found}` : ""}` : (o.error ? `線上：${o.error}` : "");
    $("#st-online").classList.toggle("hidden", !o.running && !o.error);
    if (lastOnline && !o.running) { loadSidebar(); }
    // 背景工作一直在跑（搜源、補資料卡）時，側欄數字也會一直變：每 30 秒更新一次，不要等到全部做完
    else if ((o.running || s.running) && Date.now() - (S.sbAt || 0) > 30000) { S.sbAt = Date.now(); loadSidebar(); }
    lastOnline = !!o.running;
    if (s.archive && S.archiveWatch && s.archive.at >= S.archiveWatch) {
      const a = s.archive; S.archiveWatch = null;
      toast(`已移出 iCloud ${a.moved} 張（約 ${fmtSize(a.freed)}）` + (a.failed.length ? `，${a.failed.length} 張失敗、原封不動` : "") + "。記得到 icloud.com「最近刪除」清空，iCloud 空間才會釋放", { label: "怎麼清", fn: icloudClear });
    }
    if (s.upgrade && S.upgradeWatch && s.upgrade.at >= S.upgradeWatch) {
      const u = s.upgrade; S.upgradeWatch = null;
      toast(`已換成高清 ${u.done} 張` + (u.failed.length ? `；${u.failed.length} 張沒換（${u.failed[0].error}${u.failed.length > 1 ? " 等" : ""}）` : ""));
      if (/(^|\s)is:upgrade(\s|$)/.test(S.q || "")) render();
    }
    if (lastRunning && !s.running) { loadSidebar(); if (["all", "unrecognized", "videos", "tag", "nsfw", "errors"].includes(S.view) && !S.items.length) render(); }
    lastRunning = s.running;
  } catch (e) { console.error(e); }
}

function drawJobPill(s) {
  // 本機和線上工作同時在跑時兩個都顯示；後面加「＋N 排隊」。點一下打開背景工作面板（看排程、排隊、最近完成）
  const el = $("#jobpill");
  const dl = s.download;
  const o = s.online || {};
  const qn = (s.queued || 0) + (o.queued || 0);
  const parts = [];
  if (s.running || s.paused) parts.push(dl ? `下載模型 ${fmtSize(dl.got)}${dl.total ? " / " + fmtSize(dl.total) : ""}`
    : `${s.paused ? "已暫停 · " : ""}${s.phase}${s.total ? ` ${s.done.toLocaleString()}/${s.total.toLocaleString()}` : ""}${s.eta && !s.paused ? ` · 剩 ${fmtEta(s.eta)}` : ""}`);
  if (o.running) parts.push(`${o.phase} ${o.done.toLocaleString()}/${o.total.toLocaleString()} · 找到 ${o.found}${o.eta ? ` · 剩 ${fmtEta(o.eta)}` : ""}`);
  if (!parts.length) {
    if (qn) { parts.push(`${qn} 項工作排隊中`); $("#jobline")?.classList.add("hidden"); }
    else { el.classList.add("hidden"); $("#jobline")?.classList.add("hidden"); if (S.view === "home" && lastRunning) render(); return; }
  }
  el.classList.remove("hidden");
  const main = s.running || s.paused ? s : o;
  const pct = dl && dl.total ? 100 * dl.got / dl.total : main.total ? 100 * main.done / main.total : 0;
  const jl = $("#jobline"); if (jl) { jl.classList.remove("hidden"); $("i", jl).style.width = Math.max(2, pct).toFixed(1) + "%"; jl.title = parts.join("　｜　"); jl.onclick = () => openJobs(); }
  const slow = o.running && o.mode && o.mode.state !== "ok" && o.phase === "以圖搜源";
  el.innerHTML = `<i style="width:${pct.toFixed(1)}%"></i><span>${esc(parts.join("　｜　"))}</span>${qn && (s.running || o.running) ? `<b class="jp-q" title="還有 ${qn} 項在排隊">＋${qn}</b>` : ""}${slow ? `<b class="jp-slow" title="沒有 Danbooru 帳號，只能用很慢的 IQDB">慢速</b>` : ""}<b class="jp-more" title="背景工作：排程、排隊、最近完成">⋯</b>`;
  el.classList.toggle("slow", !!slow);
  el.title = "點一下看背景工作（排程、排隊中、最近完成），可以暫停、停止或取消";
  el.onclick = () => openJobs();
}

/* ============ 背景工作面板 ============ */
export async function openJobs() {
  const m = modal(`<h3>背景工作</h3><div class="mbody" id="jb">讀取中…</div>
    <div class="mfoot"><span class="mute" style="margin-right:auto">自動更新</span><button class="btn" id="jb-diag">系統狀態</button><button class="btn" data-close>關閉</button></div>`, null, "wide jobs");
  $("#jb-diag", m).onclick = () => openDiag();
  const when = t => { if (!t) return "—"; const d = t - Date.now() / 1000; if (Math.abs(d) < 60) return d >= 0 ? "馬上" : "剛剛"; const a = Math.abs(d);
    const txt = a < 3600 ? `${Math.round(a / 60)} 分鐘` : a < 86400 ? `${(a / 3600).toFixed(a < 36000 ? 1 : 0)} 小時` : `${Math.round(a / 86400)} 天`; return d >= 0 ? `${txt}後` : `${txt}前`; };
  const dur = (a, b) => { const d = Math.max(0, (b || Date.now() / 1000) - a); return d < 60 ? `${Math.round(d)} 秒` : d < 3600 ? `${Math.round(d / 60)} 分鐘` : `${(d / 3600).toFixed(1)} 小時`; };
  const Q = { local: "本機", online: "線上", bg: "背景", task: "背景" };
  const qc = q => q === "task" ? "bg" : q;
  const draw = async () => {
    if (!m.isConnected) return;
    let d; try { d = await api("/api/jobs/overview"); } catch (e) { $("#jb", m).textContent = e.message; return; }
    const run = d.running.length ? d.running.map(r => `<div class="jrow run"><span class="jq ${qc(r.queue)}">${Q[r.queue]}</span><div class="jmain"><b>${esc(r.label || r.phase || "")}</b>
        <span class="mute">${r.total ? `${fmtN(r.done)} / ${fmtN(r.total)}` : ""}${r.found != null ? ` · 找到 ${fmtN(r.found)}` : ""}${r.eta ? ` · 剩 ${fmtEta(r.eta)}` : ""}${r.started ? ` · 已經 ${dur(r.started)}` : ""}${r.paused ? " · 已暫停" : ""}</span>
        ${r.total ? `<div class="bar"><i style="width:${(100 * r.done / r.total).toFixed(1)}%"></i></div>` : ""}</div>
        ${r.queue === "local" ? `<button class="btn sm" data-jp="${r.paused ? "resume" : "pause"}">${r.paused ? "繼續" : "暫停"}</button><button class="btn sm" data-jstop="local">停止</button>` : r.queue === "online" ? `<button class="btn sm" data-jstop="online">停止</button>` : r.can_cancel ? `<button class="btn sm" data-jc="task:${r.id}">停止</button>` : ""}</div>`).join("")
      : `<div class="mute jempty">目前沒有在跑的工作</div>`;
    const qd = d.queued.length ? d.queued.map(q => `<div class="jrow"><span class="jq ${q.queue}">${Q[q.queue]}</span><div class="jmain"><b>${esc(q.label)}</b><span class="mute">${q.n != null ? `${fmtN(q.n)} 項` : ""}${q.note ? ` · ${esc(q.note)}` : ""}</span></div>
        <button class="btn sm ghost" data-jc="${q.queue}:${q.index}" title="還沒開始，拿掉">取消</button></div>`).join("") : `<div class="mute jempty">沒有排隊中的工作</div>`;
    const sc = d.scheduled.map(x => `<div class="jrow"><span class="jq sched">排程</span><div class="jmain"><b>${esc(x.label)}</b><span class="mute">${esc(x.every)}${x.note ? ` · ${esc(x.note)}` : ""}</span></div>
        <div class="jwhen">${x.next ? `<span>下次 ${when(x.next)}</span>` : ""}${x.last ? `<span class="mute">上次 ${when(x.last)}</span>` : ""}</div></div>`).join("");
    const hs = d.history.length ? d.history.map(h => `<div class="jrow done ${h.error ? "bad" : ""}"><span class="jq ${qc(h.queue)}">${Q[h.queue]}</span><div class="jmain"><b>${esc(h.label)}</b>
        <span class="mute">${h.error ? esc(h.error) : h.stopped ? "被停止" : "完成"}${h.total ? ` · ${fmtN(h.done)}/${fmtN(h.total)}` : h.n ? ` · ${fmtN(h.n)} 項` : ""}${h.found ? ` · 找到 ${fmtN(h.found)}` : ""} · 花了 ${dur(h.started, h.ended)}</span></div>
        <div class="jwhen"><span class="mute">${when(h.ended)}</span></div></div>`).join("") : `<div class="mute jempty">這次開啟程式後還沒有完成的工作</div>`;
    // 上次程式關掉（或當掉）時還在跑的：重新按一次就會接著做（存過的不重抓）
    const it = (d.interrupted || []).length ? `<h4 class="jh">上次中斷的</h4>${d.interrupted.map(x => `<div class="jrow done bad"><span class="jq bg">背景</span><div class="jmain"><b>${esc(x.label)}</b><span class="mute">程式關掉時還在做 · ${when(x.started)}開始</span></div></div>`).join("")}<div class="mute" style="font-size:var(--fs1)">要的話再按一次（匯入會跳過存過的） <a class="lnk" id="jb-int-ok">知道了</a></div>` : "";
    $("#jb", m).innerHTML = `${it}<h4 class="jh">正在進行</h4>${run}<h4 class="jh">排隊中 <span>${d.queued.length || ""}</span></h4>${qd}<h4 class="jh">排程（自動會做的事）</h4>${sc}<h4 class="jh">最近完成</h4>${hs}`;
    $$("[data-jp]", m).forEach(b => b.onclick = async () => { await api("/api/jobs", { kind: b.dataset.jp }); draw(); });
    $$("[data-jstop]", m).forEach(b => b.onclick = async () => { await api(b.dataset.jstop === "online" ? "/api/online/stop" : "/api/jobs", b.dataset.jstop === "online" ? {} : { kind: "stop" }); toast("已停止"); draw(); });
    $$("[data-jc]", m).forEach(b => b.onclick = async () => { const [q, i] = b.dataset.jc.split(":"); await api("/api/jobs/cancel", { queue: q, index: +i }); draw(); });
    const io = $("#jb-int-ok", m); if (io) io.onclick = async () => { await api("/api/jobs/interrupted-seen", {}); draw(); };
  };
  await draw();
  // 事件流連著：工作有變（登記表版本、狀態列）就重畫，另外每 10 秒更新「幾分鐘後」；連不上：每 2 秒問一次
  let last = 0;
  const kick = () => { if (Date.now() - last > 700) { last = Date.now(); draw(); } };
  const offs = [onEvent("tasks", kick), onEvent("status", kick)];
  const t = setInterval(() => { if (!m.isConnected) { clearInterval(t); offs.forEach(f => f()); } else if (!sseOk() || Date.now() - last > 10000) kick(); }, 2000);
}
export const pollOnce = () => api("/api/status").then(drawJobPill).catch(() => { });

/* ============ NSFW 開關 ============ */
export function applyNsfwUI() {
  const b = $("#nsfw-btn");
  b.className = "sbline nsfw " + S.nsfw;
  b.innerHTML = `${svg({ hide: "eyeoff", blur: "eyeblur", show: "eye" }[S.nsfw], 16)}<span class="lb">${{ hide: "NSFW 隱藏", blur: "NSFW 模糊", show: "NSFW 顯示" }[S.nsfw]}</span><kbd>H</kbd>`;
  b.title = `${NSFW_LABEL[S.nsfw]}（按 H 切換：隱藏 → 模糊 → 顯示）`;
  document.body.classList.toggle("nsfw-blur", S.nsfw === "blur");
  document.body.classList.toggle("nsfw-hide", S.nsfw === "hide");
}
export function cycleNsfw() {
  const prevApi = apiNsfw();
  S.nsfw = NSFW_MODES[(NSFW_MODES.indexOf(S.nsfw) + 1) % 3];
  store.set("nsfw", S.nsfw);
  applyNsfwUI();
  toast(NSFW_LABEL[S.nsfw]);
  if (apiNsfw() !== prevApi) { loadSidebar(); render(); }
}
$("#nsfw-btn").onclick = cycleNsfw;

/* ============ 說明 ============ */
export function openShortcuts() {
  return modal(`<h3>快捷鍵</h3><div class="mbody"><table class="help">
  ${[["點擊 / 拖曳", "選取（沒選取時，快捷鍵作用在滑鼠指著的那張）"], ["Shift+點擊", "範圍選取"], ["Ctrl+A", "全選已載入"], ["雙擊 / Space", "看大圖（←→ 切換）"], ["1–9", `快速標籤：${(S.quick || []).map(q => `${q.key} ${q.name}`).join("、") || "在設定加上"}（選取的圖、滑鼠指著的圖、或大圖）`], ["⌫", "從目前角色移除（否決）"], ["Delete", "刪除：丟到資源回收筒（會先問；紀錄保留 30 天）"], ["Enter", "確認目前角色"], ["C", "改標成…"], ["A", "＋角色"], ["G", "＋標籤"], ["B", "本子：建立或加入（同人本、cos 圖包）"], ["N", "NSFW 標記"], ["S", "搜尋來源（Danbooru/IQDB/Pixiv）"], ["R", "＋創作者"], ["O", "標為原創（非同人，無角色）"], ["K", "標為官方作品（動畫／漫畫截圖、小說插圖）"], ["M", "媒材（插畫、Q 版、漫畫、3D、照片…；你標的會教 AI）"], ["L／X", "不用整理（留在圖庫，不再列入待辦）"], ["H", "切換 NSFW：隱藏 → 模糊 → 顯示"], ["Ctrl+K 或 /", "搜尋、前往角色、執行指令"], ["Z（大圖）", "放大 / 縮回，滾輪縮放、拖曳移動"], ["Enter / ⌫（大圖）", "第一個候選：是 / 不是（待確認頁：這張處理完自動下一張）"], ["Delete（大圖）", "刪除這張，接著看下一張"], ["Shift+Enter（大圖）", "這張的候選全部是"], ["Shift+數字 / Alt+數字（大圖）", "第 N 個候選：是 / 不是（候選前面的小數字）"], ["字母鍵（大圖）", "跟格子同一張表：A R G B O L N K M S；沒選取時作用在滑鼠指著的那張"], ["I（大圖）", "詳細抽屜"], ["Ctrl+C", "分享：複製選的圖（沒選就是滑鼠指著的、大圖是眼前這張），到 QQ／Messenger 的聊天視窗按 Ctrl+V"], ["Ctrl+Shift+C（大圖影片）", "整部影片轉成 GIF，好了直接複製（2 分鐘以內，用上次的設定）"], ["Ctrl+Z", "撤銷"], ["?", "打開這張快捷鍵表"], ["Esc", "取消選取 / 關閉"]].map(([k, v]) => `<tr><td><kbd>${k}</kbd></td><td>${v}</td></tr>`).join("")}
  </table>${QUERY_HELP}</div>
  <div class="mfoot"><button class="btn" data-close>關閉</button></div>`);
}
$("#help-btn").onclick = openShortcuts;

/* ============ 鍵盤 ============ */
const textSelected = () => !!String(window.getSelection?.() || "").trim();   // 有反白文字時 Ctrl+C 照常複製文字
document.addEventListener("keydown", e => {
  const ae = document.activeElement;   // 滑桿、勾選框不算「正在打字」，不然調完縮圖大小後快捷鍵全失效
  const typing = !!ae && (ae.isContentEditable || /TEXTAREA|SELECT/.test(ae.tagName)
    || (ae.tagName === "INPUT" && !["range", "checkbox", "radio", "button"].includes(ae.type)));
  if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") { e.preventDefault(); openPalette(); return; }
  if (e.key === "Alt" && !typing) { e.preventDefault(); document.body.classList.add("xray"); return; }   // 按住 Alt：格子上的資訊全部浮出來（透視）
  if (typing) return;
  if (modalOpen()) { if (e.key === "Escape") closeModal(); return; }
  if (V) {
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "z") { e.preventDefault(); undo(); return; }   // 大圖裡也能撤銷
    if ((e.ctrlKey || e.metaKey) && !e.shiftKey && e.key.toLowerCase() === "c" && !textSelected()) { e.preventDefault(); shareCopy([V.id]); return; }   // 分享：複製這張
    if ((e.ctrlKey || e.metaKey) && e.shiftKey && e.key.toLowerCase() === "c" && $("#v-gif")) { e.preventDefault(); $("#v-gif").dispatchEvent(new window.MouseEvent("click", { shiftKey: true })); return; }   // 影片：整部轉 GIF 直接複製
    // Shift＋數字＝第 N 個候選「是」、Alt＋數字＝「不是」（b93；數字鍵本身還是快速標籤）。看 e.code：Shift+1 的 e.key 是「!」
    const dn = /^(?:Digit|Numpad)([1-9])$/.exec(e.code || "");
    if (dn && !e.ctrlKey && !e.metaKey && e.shiftKey !== e.altKey) { e.preventDefault(); vKey({ pick: +dn[1] - 1, yes: e.shiftKey }); return; }
    if (!e.ctrlKey && !e.metaKey && !e.altKey && !e.shiftKey && /^[1-9]$/.test(e.key)) { e.preventDefault(); quickTag(+e.key - 1); return; }
    if (e.key === "i" || e.key === "I") { e.preventDefault(); toggleVDrawer(); return; }
    if (!e.ctrlKey && !e.metaKey && !e.altKey) {     // 大圖是主要的工作台：候選、加標籤都不用動滑鼠
      // ⌫＝不是（否決第一個候選）、Delete＝刪除這張（丟資源回收筒，會先問）：以前兩個鍵都是「不是」
      const vk = e.key === "Enter" ? (e.shiftKey ? "allyes" : "yes") : e.key === "Backspace" ? "no" : e.key === "Delete" ? "trash" : ["a", "r", "g", "n", "k", "m", "s", "l", "x", "b", "o"].includes(e.key.toLowerCase()) ? e.key.toLowerCase() : null;
      if (vk) { e.preventDefault(); vKey(vk); return; }
    }
    if (e.key === "z" || e.key === "Z") { e.preventDefault(); toggleZoom(); }
    else if (e.key === "Escape" || e.key === " ") { e.preventDefault(); closeViewer(); }
    else if (e.key === "ArrowRight") stepViewer(1);
    else if (e.key === "ArrowLeft") stepViewer(-1);
    return;
  }
  if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "z") { e.preventDefault(); undo(); return; }
  if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "a") { e.preventDefault(); selectAll(); return; }
  if ((e.ctrlKey || e.metaKey) && !e.shiftKey && e.key.toLowerCase() === "c" && !textSelected()) {   // 分享：選的圖（沒選就是滑鼠指著的那張）
    const ids = S.selected.size ? [...S.selected] : S.hover != null ? [S.hover] : [];
    if (ids.length) { e.preventDefault(); doActionIds("share", ids); }
    return;
  }
  if (e.ctrlKey || e.metaKey || e.altKey) return;
  if (/^[1-9]$/.test(e.key)) { e.preventDefault(); quickTag(+e.key - 1); return; }   // 快速標籤：選取的圖，沒選就是滑鼠指著的那張
  const k = e.key.toLowerCase();
  if (k === "escape") clearSel();
  else if (k === "/") { e.preventDefault(); openPalette(); }
  else if (e.key === "?") { e.preventDefault(); openShortcuts(); }
  else if (k === "h") cycleNsfw();
  else if (k === " ") {
    e.preventDefault();
    let id = S.hover ?? [...S.selected].pop();
    if (id != null && S.view === "review") id = S.items[id]?.id;   // 待確認頁的格子編號是清單位置，不是圖片 id
    if (id == null && S.view === "review" && RF.cur) id = RF.cur.id;
    if (id != null) openViewer(id);
  }
  if (S.view === "review" && S.rmode === "focus" && !S.selected.size && focusKey(k, e)) return;
  if (S.view === "review" && S.rmode === "batch" && batchKey(k, e)) return;
  if (S.view === "home" && k === "enter" && !S.selected.size && S.homeEnter) { e.preventDefault(); S.homeEnter(); return; }   // 專注模式：Enter／⌫／L／C／A 直接作用在眼前這一張
  // 沒有選取時，字母鍵跟數字鍵一樣作用在滑鼠指著的那張（以前數字鍵可以、字母鍵要先選取）
  const target = !S.selected.size && S.hover != null && !(S.view === "review" && S.rmode !== "grid") ? [S.hover] : null;
  if ((!S.selected.size && !target) || ["escape", "/", "h", " ", "?"].includes(k)) return;
  const doAction = (a) => doActionIds(a, target || undefined);
  // 動作鍵：阻止預設，避免字母被打進接著打開的輸入框
  if (k.length === 1 && "cagnsroxlkbm".includes(k)) e.preventDefault();
  if (k === "delete") { e.preventDefault(); doAction("trash"); }     // Delete 在每一頁都是刪除（會先問）；⌫ 才是「不是」
  else if (k === "backspace" && S.view === "review") { e.preventDefault(); doAction("no"); }
  else if (k === "enter" && S.view === "review") doAction("yes");
  else if (S.view === "review") return;
  else if (k === "backspace" && S.view === "tag") { e.preventDefault(); doAction("reject"); }
  else if (k === "enter" && S.view === "tag") doAction("confirm");
  else if (k === "c" && S.view === "tag") doAction("replace");
  else if (k === "a") doAction("also");
  else if (k === "g") doAction("tags");
  else if (k === "b") doAction("album");
  else if (k === "n") doAction("nsfw");
  else if (k === "s") doAction("source");
  else if (k === "r") doAction("artist");
  else if (k === "o") doAction("original");
  else if (k === "k") doAction("official");
  else if (k === "m") doAction("mediatype");
  else if (k === "x" || k === "l") doAction(S.view === "excluded" ? "unexclude" : S.view === "unrecognized" && S.bucket === "skipped" ? "unskip" : "skip");
  else if (k === "l" && S.view === "review") doAction("rskip");
});

const QUIET_SUB = ["home", "review", "unrecognized", "errors", "nsfw", "official", "excluded", "videos", "duplicates", "system", "all", "artists", "tags", "albums"];
document.addEventListener("keyup", e => { if (e.key === "Alt") document.body.classList.toggle("xray", !!S.xrayPin); });
window.addEventListener("blur", () => document.body.classList.toggle("xray", !!S.xrayPin));
export function toggleXray() { S.xrayPin = !S.xrayPin; document.body.classList.toggle("xray", S.xrayPin); $$("[data-xray]").forEach(b => b.classList.toggle("on", S.xrayPin)); }

/* ============ 頁面標題移到頂列（標題＋摘要，跟右邊的工具按鈕同一行） ============ */
new MutationObserver(() => {
  const head = $("#head"), pt = $("#pagetitle");
  const h1 = $("h1", head); if (!h1) return;
  pt.innerHTML = "";
  const nodes = [h1];
  let n = h1.nextElementSibling;
  if (n && n.classList.contains("rawname")) { nodes.push(n); n = n.nextElementSibling; }
  if (S.view !== "tag" && n && n.classList.contains("sub")) {
    // 說明文字不常駐：只留 ⓘ（滑過看說明）；有數字的摘要（角色、創作者、作品頁）照舊顯示
    if (QUIET_SUB.includes(S.view)) {
      const tip = n.textContent.replace(/ⓘ\s*$/, "").trim() + ((n.querySelector(".info")?.title) ? "\n" + n.querySelector(".info").title : "");
      n.remove();
      if (tip) { const i = document.createElement("span"); i.className = "info ptinfo"; i.tabIndex = 0; i.title = tip; i.textContent = "ⓘ"; h1.append(i); }
    } else nodes.push(n);
  }
  nodes.forEach(x => pt.append(x));
}).observe($("#head"), { childList: true, subtree: true });

/* ============ 底色：淡綠（預設，Pickle Jar 的顏色）/ 白 ============ */
export function applyTint() { document.body.classList.toggle("tint", store.get("tint", true)); }
applyTint();

/* ============ 側欄：精簡圖示列 / 完整 ============ */
function applyRail() { document.body.classList.toggle("rail", store.get("rail", false)); }
$(".brand").title = "點一下切換側欄（精簡圖示列 / 完整）";
$(".brand").onclick = () => { store.set("rail", !store.get("rail", false)); applyRail(); };
$$("#nav a").forEach(a => { if (!a.title) a.title = a.textContent.trim().replace(/[\d,]+$/, ""); });
applyRail();
{ const n = store.get("visits", 0) + 1; store.set("visits", n); if (n > 5) document.body.classList.add("seasoned"); }
{ const fk = document.getElementById("foot-keys"); if (fk) { fk.style.cursor = "pointer"; fk.onclick = openShortcuts; } }

/* ============ 快速標籤（梗圖、真人、截圖…）============ */
function quickIds() {
  if (V) return [V.id];
  if (S.view === "review" && S.rmode === "focus" && !S.selected.size) return RF.cur ? [RF.cur.id] : [];   // 專注模式：眼前這一張
  const toId = x => S.view === "review" ? S.items[x]?.id : x;   // 待確認頁的格子編號是清單位置
  if (S.selected.size) return [...new Set([...S.selected].map(toId).filter(x => x != null))];
  const h = S.hover != null ? toId(S.hover) : null;
  return h != null ? [h] : [];
}
export async function quickTag(i, ids) {
  const q = (S.quick || [])[i]; if (!q) { if (i < 9) toast(`還沒有第 ${i + 1} 個快速標籤：左邊「快速標籤」旁按「編輯」加上`); return; }
  ids = ids || quickIds();
  if (!ids.length) return toast(`先選圖（或把滑鼠移到圖上）再按 ${i + 1}`);
  let r; try { r = await api("/api/quicktag", { ids, name: q.name }); } catch (e) { return toast(e.message); }
  hud(i + 1, `${q.name} ${r.on ? "✓" : "拿掉了"}${ids.length > 1 ? `　${ids.length} 張` : ""}`, [r.on && q.skip && S.view === "unrecognized" ? "離開未識別" : "", r.cleared ? `拿掉 ${r.cleared} 個角色候選，不再認角色` : ""].filter(Boolean).join("，"), undo);
  if (r.no_char && S.view === "review" && !V) dropImages(ids);
  if (!V && S.selected.size) recordUse("qt:" + i);
  if (V) { markDirty(); openViewer(V.id); }
  else if (r.on && q.skip && S.view === "unrecognized" && S.bucket !== "skipped") { removeCells(ids); loadBuckets(); }
  else if (!r.on && S.view === "tag" && S.tag === q.name) removeCells(ids);
  else if (r.on && q.no_artist && ((S.view === "series" && S.seriesMiss === "noartist") || (S.view === "unrecognized" && S.miss === "artist"))) removeCells(ids);   // 梗圖＝作者不可考
  loadSidebar();
}
$("#qt-edit").onclick = e => { e.stopPropagation(); openSettings("display"); };
$("#health").onclick = e => { e.stopPropagation(); openHealth(); };
// 專注退場：連續按 3 個鍵、滑鼠沒動 → 側欄、標題、工具淡出（body.zen，只改透明度，按鈕照樣能點）；滑鼠一動就回來。設定 › 顯示可以關
{
  let n = 0, t;
  document.addEventListener("keydown", e => {
    if (["Shift", "Control", "Alt", "Meta", "Escape"].includes(e.key) || e.ctrlKey || e.metaKey) return;
    if (e.target.closest && e.target.closest("input, textarea, select, [contenteditable]")) return;
    if (modalOpen() || store.get("zen", true) === false) return;
    if (++n >= 3) document.body.classList.add("zen");
    clearTimeout(t); t = setTimeout(() => { n = 0; }, 4000);
  }, true);
  document.addEventListener("mousemove", e => {
    if (Math.abs(e.movementX) + Math.abs(e.movementY) < 4) return;
    n = 0; document.body.classList.remove("zen");
  }, { passive: true });
}

// 快速標籤（v4 不常駐在側欄）：底部搜尋列的面板裡一排，點一下到那個標籤的頁面
export const quickFacetHTML = () => (S.quick || []).length ? `<div class="fgrp"><span class="flbl">快速標籤</span><div class="tabs">${S.quick.map(q => `<button data-qtag="${esc(q.name)}" title="看全部「${esc(q.name)}」的圖（數字鍵 ${q.key} 加上／拿掉）">${esc(q.name)} <i>${fmtN(q.n)}</i></button>`).join("")}</div></div>` : "";
document.addEventListener("click", e => { const b = e.target.closest("#facets [data-qtag]"); if (b) { $("#facets")?.classList.add("hidden"); go("tag", b.dataset.qtag); } });
