// ES module（2026-10）：用到別的檔案的東西都要在這裡 import；瀏覽器會在載入時檢查名字對不對。
/* ============ 圖示（線條 SVG） ============ */
const ICON = {
  review: '<circle cx="12" cy="12" r="9"/><path d="M8 12.5l2.7 2.7L16.5 9.5"/>',
  all: '<rect x="4" y="4" width="7" height="7" rx="2"/><rect x="13" y="4" width="7" height="7" rx="2"/><rect x="4" y="13" width="7" height="7" rx="2"/><rect x="13" y="13" width="7" height="7" rx="2"/>',
  characters: '<circle cx="12" cy="8.5" r="3.8"/><path d="M4.5 20c1.3-3.7 4.2-5.6 7.5-5.6s6.2 1.9 7.5 5.6"/>',
  user: '<circle cx="12" cy="8.5" r="3.8"/><path d="M4.5 20c1.3-3.7 4.2-5.6 7.5-5.6s6.2 1.9 7.5 5.6"/>',
  artists: '<path d="M4 20l1-4.2L15.6 5.2a2 2 0 0 1 2.8 0l.4.4a2 2 0 0 1 0 2.8L8.2 19z"/><path d="M13.5 7.3l3.2 3.2"/>',
  unrecognized: '<circle cx="12" cy="12" r="9"/><path d="M9.6 9.4a2.5 2.5 0 1 1 3.6 2.3c-.8.4-1.2.9-1.2 1.7v.4"/><circle cx="12" cy="16.9" r=".7" fill="currentColor"/>',
  videos: '<rect x="3.5" y="5.5" width="17" height="13" rx="3"/><path d="M10.5 9.5v5l4.2-2.5z" fill="currentColor"/>',
  duplicates: '<rect x="8" y="8" width="11.5" height="11.5" rx="2.5"/><path d="M5 15.5V6.5A2 2 0 0 1 7 4.5h8.5"/>',
  tags: '<path d="M4 12.6V5.2A1.2 1.2 0 0 1 5.2 4h7.4l7.4 7.4a1.5 1.5 0 0 1 0 2.1l-6 6a1.5 1.5 0 0 1-2.1 0z"/><circle cx="8.5" cy="8.5" r="1.3"/>',
  nsfw: '<path d="M3 3l18 18"/><path d="M10.6 5.1A9.8 9.8 0 0 1 12 5c5 0 8.5 4.5 9.5 7-.4 1-1.3 2.5-2.7 3.9M6.4 6.5C4.5 7.8 3.1 9.8 2.5 12c1 2.5 4.5 7 9.5 7 1.7 0 3.2-.5 4.5-1.3"/><path d="M9.9 9.9a3 3 0 0 0 4.2 4.2"/>',
  excluded: '<circle cx="12" cy="12" r="9"/><path d="M5.6 5.6l12.8 12.8"/>',
  errors: '<path d="M12 4l9 16H3z"/><path d="M12 10v4.5"/><circle cx="12" cy="17.3" r=".7" fill="currentColor"/>',
  settings: '<circle cx="12" cy="12" r="3.2"/><path d="M12 2.8v2.4M12 18.8v2.4M2.8 12h2.4M18.8 12h2.4M5.5 5.5l1.7 1.7M16.8 16.8l1.7 1.7M5.5 18.5l1.7-1.7M16.8 7.2l1.7-1.7"/>',
  undo: '<path d="M9 7L4.5 11.5 9 16"/><path d="M5 11.5h9a5.5 5.5 0 0 1 0 11h-2" transform="translate(0 -3)"/>',
  help: '<circle cx="12" cy="12" r="9"/><path d="M9.6 9.4a2.5 2.5 0 1 1 3.6 2.3c-.8.4-1.2.9-1.2 1.7v.4"/><circle cx="12" cy="16.9" r=".7" fill="currentColor"/>',
  search: '<circle cx="11" cy="11" r="6.5"/><path d="M16 16l4 4"/>',
  refresh: '<path d="M20 11a8 8 0 0 0-14.3-4.9L4 8"/><path d="M4 4v4h4"/><path d="M4 13a8 8 0 0 0 14.3 4.9L20 16"/><path d="M20 20v-4h-4"/>',
  menu: '<path d="M4 7h16M4 12h16M4 17h16"/>',
  eye: '<path d="M2.5 12C3.5 9.5 7 5 12 5s8.5 4.5 9.5 7c-1 2.5-4.5 7-9.5 7s-8.5-4.5-9.5-7z"/><circle cx="12" cy="12" r="3"/>',
  eyeoff: '<path d="M3 3l18 18"/><path d="M10.6 5.1A9.8 9.8 0 0 1 12 5c5 0 8.5 4.5 9.5 7-.4 1-1.3 2.5-2.7 3.9M6.4 6.5C4.5 7.8 3.1 9.8 2.5 12c1 2.5 4.5 7 9.5 7 1.7 0 3.2-.5 4.5-1.3"/><path d="M9.9 9.9a3 3 0 0 0 4.2 4.2"/>',
  home: '<path d="M4 10.5L12 4l8 6.5V19a1.5 1.5 0 0 1-1.5 1.5H15v-5.5h-6v5.5H5.5A1.5 1.5 0 0 1 4 19z"/>',
  saved: '<path d="M6.5 4h11A1.5 1.5 0 0 1 19 5.5V20l-7-4.2L5 20V5.5A1.5 1.5 0 0 1 6.5 4z"/>',
  phone: '<rect x="7" y="3" width="10" height="18" rx="2.5"/><path d="M11 17.5h2"/>',
  pause: '<path d="M9 6v12M15 6v12"/>',
  play: '<path d="M8 5.5v13l10-6.5z"/>',
  zoomin: '<circle cx="11" cy="11" r="6.5"/><path d="M16 16l4 4M8.5 11h5M11 8.5v5"/>',
  external: '<path d="M14 4h6v6"/><path d="M20 4l-9 9"/><path d="M18 14v4.5A1.5 1.5 0 0 1 16.5 20h-11A1.5 1.5 0 0 1 4 18.5v-11A1.5 1.5 0 0 1 5.5 6H10"/>',
  folder: '<path d="M3.5 7.5A1.5 1.5 0 0 1 5 6h4.5l2 2H19a1.5 1.5 0 0 1 1.5 1.5v8A1.5 1.5 0 0 1 19 19H5a1.5 1.5 0 0 1-1.5-1.5z"/>',
  albums: '<path d="M6 4.5h10.5A1.5 1.5 0 0 1 18 6v13.5H7.5A1.5 1.5 0 0 1 6 18z"/><path d="M6 18a1.5 1.5 0 0 1 1.5-1.5H18"/><path d="M9.5 8h5"/>',
  pulse: '<path d="M3 12h4l2.5-6 5 12 2.5-6h4"/>',
  share: '<circle cx="17.5" cy="5.5" r="2.5"/><circle cx="6.5" cy="12" r="2.5"/><circle cx="17.5" cy="18.5" r="2.5"/><path d="M8.7 10.8l6.6-4M8.7 13.2l6.6 4"/>',
  gif: '<rect x="3.5" y="5.5" width="17" height="13" rx="2"/><path d="M3.5 9h17M3.5 15h17M7.5 5.5V9M12 5.5V9M16.5 5.5V9M7.5 15v3.5M12 15v3.5M16.5 15v3.5"/>',
  download: '<path d="M12 4v11"/><path d="M7 10.5l5 5 5-5"/><path d="M5 19.5h14"/>',
  target: '<circle cx="12" cy="12" r="8.5"/><circle cx="12" cy="12" r="4.5"/><circle cx="12" cy="12" r="1" fill="currentColor"/>',
  filter: '<path d="M4 5h16l-6 7.5V19l-4-2v-4.5z"/>',
  clock: '<circle cx="12" cy="12" r="8.5"/><path d="M12 7.5V12l3 2"/>',
  db: '<ellipse cx="12" cy="6" rx="7" ry="2.5"/><path d="M5 6v12c0 1.4 3.1 2.5 7 2.5s7-1.1 7-2.5V6"/><path d="M5 12c0 1.4 3.1 2.5 7 2.5s7-1.1 7-2.5"/>',
  system: '<path d="M3 12h4l2.5-6 5 12 2.5-6h4"/>',
  eyeblur: '<path d="M2.5 12C3.5 9.5 7 5 12 5s8.5 4.5 9.5 7c-1 2.5-4.5 7-9.5 7s-8.5-4.5-9.5-7z" stroke-dasharray="2.5 2.5"/><circle cx="12" cy="12" r="3" fill="currentColor" opacity=".35"/>',
};
export const svg = (k, size = 20) => `<svg width="${size}" height="${size}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${ICON[k] || ""}</svg>`;
document.querySelectorAll("#nav a[data-view]").forEach(a => { const ic = a.querySelector(".ic"); if (ic && ICON[a.dataset.view]) ic.innerHTML = svg(a.dataset.view, 22); });
document.querySelectorAll("[data-icon]").forEach(el => { el.innerHTML = svg(el.dataset.icon, el.closest("#sidebar") ? 22 : 19); });

/* ============ 基礎工具 ============ */
export const $ = (s, el = document) => el.querySelector(s);
export const $$ = (s, el = document) => [...el.querySelectorAll(s)];
export const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const h = (html) => { const t = document.createElement("template"); t.innerHTML = html.trim(); return t.content.firstElementChild; };
export const store = {
  get(k, d) { try { const v = localStorage.getItem("lib:" + k); return v == null ? d : JSON.parse(v); } catch { return d; } },
  set(k, v) { try { localStorage.setItem("lib:" + k, JSON.stringify(v)); } catch { } },
};
// 本子裡的頁：預設不算進圖庫、角色、作品的圖和張數（≡ 選單「也顯示本子裡的頁」打開才算）
export const noAlbumQS = () => store.get("showAlbumPages", false) ? "" : "&noalbum=1";
// 操作紀錄（伺服器的 op_log）：每個請求帶上「在哪一頁」「剛剛用鍵盤還是滑鼠」，分析使用習慣用
let _via = "", _viaT = 0;
document.addEventListener("keydown", () => { _via = "key"; _viaT = Date.now(); }, true);
document.addEventListener("pointerdown", e => { _via = e.pointerType || "mouse"; _viaT = Date.now(); }, true);
function opHeaders() {
  return { "X-Lib-View": encodeURIComponent((location.hash || "#/").slice(2) || "home"), "X-Lib-Via": Date.now() - _viaT < 5000 ? _via : "" };
}
// 請求（b93）：
// - 會改資料的請求（POST…）同一個還沒回來時再送一模一樣的（連按兩下、按鍵連發）→ 不重送，回同一個結果
// - 超過 1.5 秒還沒回來 → 畫面上方「處理中…」，不然看起來像沒按到、會再按一次
// - signal：換頁時取消舊的載入（grid.loadMore）
const _inflight = new Map();
let _slowN = 0;
function _busy(on) {
  _slowN += on ? 1 : -1;
  let el = document.getElementById("busy");
  if (_slowN > 0 && !el) { el = document.createElement("div"); el.id = "busy"; el.setAttribute("role", "status"); el.textContent = "處理中…"; document.body.append(el); }
  else if (_slowN <= 0 && el) el.remove();
}
export function api(path, body, method, { signal } = {}) {
  const m = method || (body === undefined ? "GET" : "POST");
  if (m === "GET" || path.startsWith("/api/undo")) return _api(path, body, m, signal);     // 連按 Ctrl+Z＝要撤銷好幾步，不合併
  const key = m + " " + path + " " + (body === undefined ? "" : JSON.stringify(body));
  if (_inflight.has(key)) return _inflight.get(key);
  let slow = false;
  const t = setTimeout(() => { slow = true; _busy(true); }, 1500);
  const p = _api(path, body, m, signal).finally(() => { clearTimeout(t); if (slow) _busy(false); _inflight.delete(key); });
  _inflight.set(key, p);
  return p;
}
async function _api(path, body, method, signal) {
  const opt = body === undefined ? { method, headers: opHeaders(), signal } : { method, headers: { "Content-Type": "application/json", ...opHeaders() }, body: JSON.stringify(body), signal };
  const r = await fetch(path, opt);
  if (!r.ok) { let m = r.statusText; try { m = (await r.json()).detail || m; } catch { } throw new Error(m); }
  return r.json();
}
/* ============ 事件流（b93）：伺服器有變化才推過來（/api/events，SSE）。以前狀態列、待確認頁、背景工作面板各自定時問 ============
   連不上（手機區網、舊瀏覽器、伺服器重啟中）時 sseOk() 是 false，各處退回原本的輪詢；EventSource 會自己重連。 */
const _subs = {};
let _es = null, _sseOk = false;
export const lastEvent = {};
export const sseOk = () => _sseOk;
export function onEvent(topic, fn) { (_subs[topic] ||= new Set()).add(fn); return () => _subs[topic].delete(fn); }
function _emit(topic, d) { for (const fn of [...(_subs[topic] || [])]) { try { fn(d); } catch (e) { console.error(e); } } }
export function startEvents() {
  if (_es || !window.EventSource) return;
  _es = new window.EventSource("/api/events");
  _es.onopen = () => { _sseOk = true; _emit("_open"); };
  _es.onerror = () => { const was = _sseOk; _sseOk = false; if (was) _emit("_down"); if (_es.readyState === 2) { _es = null; setTimeout(startEvents, 5000); } };
  for (const t of ["status", "tasks", "knn"]) _es.addEventListener(t, e => { let d; try { d = JSON.parse(e.data); } catch { return; } lastEvent[t] = d; _emit(t, d); });
}
// 跟著一件背景工作的進度（b96）：以前 GIF、匯出訓練集、整本匯入、畫師比對、資料卡…各自寫一個 setTimeout 迴圈每 0.3–3 秒問。
// 現在：事件流有推「背景工作／狀態變了」就問一次；事件流連不上才照 every 自己問（連著時另外每 10 秒保險一次）。
// fetchFn 拿資料、onData 畫出來，回傳 false＝結束；alive() 回傳 false（視窗關了）也結束。回傳 stop()。
export function follow(fetchFn, onData, { every = 2000, alive = () => true } = {}) {
  let stopped = false, busy = false, timer = null, last = 0;
  const offs = [];
  const stop = () => { stopped = true; clearTimeout(timer); offs.forEach(f => f()); };
  const schedule = () => { clearTimeout(timer); if (!stopped) timer = setTimeout(run, _sseOk ? Math.max(every, 10000) : every); };
  async function run() {
    if (stopped) return;
    if (!alive()) return stop();
    if (busy) return;
    busy = true; last = Date.now();
    let d, ok = true;
    try { d = await fetchFn(); } catch { ok = false; }
    busy = false;
    if (stopped) return;
    if (ok && onData(d) === false) return stop();
    schedule();
  }
  const kick = () => { if (!busy && Date.now() - last > 400) run(); };
  offs.push(onEvent("tasks", kick), onEvent("status", kick));
  run();
  return stop;
}
export function toast(msg, action) {
  const el = h(`<div class="toast">${esc(msg)}</div>`);
  if (action) { const b = h(`<button>${esc(action.label)}</button>`); b.onclick = () => { action.fn(); el.remove(); }; el.append(b); }
  $("#toasts").append(el);
  setTimeout(() => el.remove(), action ? 6000 : 3000);
}
// 數字鍵回饋：畫面中央閃一下（快速標籤不再常駐在側欄、大圖），附撤銷
let _hudT;
export function hud(key, msg, sub, undo) {
  let el = document.getElementById("hud");
  if (!el) { el = document.createElement("div"); el.id = "hud"; el.setAttribute("role", "status"); document.body.append(el); }
  el.innerHTML = `<span class="hk">${esc(String(key))}</span><span class="hm">${esc(msg)}${sub ? `<small>${esc(sub)}</small>` : ""}</span>${undo ? `<button type="button">復原 <kbd>Ctrl Z</kbd></button>` : ""}`;
  const b = el.querySelector("button"); if (b) b.onclick = () => { undo(); el.classList.remove("on"); };
  el.classList.remove("on"); void el.offsetWidth; el.classList.add("on");
  clearTimeout(_hudT);
  const hide = () => { _hudT = setTimeout(() => el.classList.remove("on"), 1600); };
  el.onmouseenter = () => clearTimeout(_hudT); el.onmouseleave = hide; hide();
}
export const fmtDur = s => { s = Math.round(s || 0); const m = Math.floor(s / 60); return `${m}:${String(s % 60).padStart(2, "0")}`; };
export const fmtSize = b => b > 1e9 ? (b / 1e9).toFixed(2) + " GB" : b > 1e6 ? (b / 1e6).toFixed(1) + " MB" : Math.round((b || 0) / 1e3) + " KB";
export const debounce = (fn, ms) => { let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); }; };

/* ============ 狀態 ============ */
export const NSFW_MODES = ["hide", "blur", "show"];
export const NSFW_LABEL = { hide: "NSFW 已隱藏", blur: "NSFW 模糊", show: "NSFW 顯示中" };
export const S = {
  view: "home", tag: null, q: "", sort: store.get("sort", "new"), sortRev: store.get("sortRev", false), nsfw: store.get("nsfw", "hide"),
  items: [], total: 0, loading: false, done: false, selected: new Set(), anchor: null, hover: null,
  size: store.get("size", 150), tagInfo: null, gen: 0,
};
export const apiNsfw = () => (S.nsfw === "hide" ? "hide" : "show");
export const PAGE = +(new URLSearchParams(location.search).get("page") || 120);   // 一批幾張（?page= 只給測試用）

/* ============ Modal / Tag 選擇器 ============ */
export function modal(html, init, cls = "") {
  closeModal();
  const ov = h(`<div class="overlay"><div class="modal ${cls}">${html}<button class="mclose" data-close aria-label="關閉" title="關閉（Esc）">✕</button></div></div>`);
  ov.addEventListener("mousedown", e => { if (e.target === ov) closeModal(); });
  ov.addEventListener("click", e => { if (e.target.closest("[data-close]")) closeModal(); });
  $("#modal-root").append(ov);
  init?.(ov.firstElementChild);
  return ov.firstElementChild;
}
export function closeModal() { $("#modal-root").innerHTML = ""; }
export const modalOpen = () => !!$("#modal-root").firstChild;
// 確認框（取代瀏覽器原生 confirm）：Enter 確定、Esc／點外面取消；危險的動作按鈕是紅的
export function ask(title, body = "", ok = "確定", danger = false) {
  return new Promise(res => {
    let done = false;
    const m = modal(`<h3>${esc(title)}</h3>${body ? `<div class="mbody"><p style="margin-top:0;white-space:pre-line">${esc(body)}</p></div>` : ""}
      <div class="mfoot"><button class="btn" data-close>取消</button><button class="btn primary${danger ? " danger" : ""}" id="ask-ok">${esc(ok)}</button></div>`);
    const ov = m.parentElement;
    const okb = m.querySelector("#ask-ok"); okb.focus();
    okb.onclick = () => { done = true; closeModal(); res(true); };
    m.addEventListener("keydown", e => { if (e.key === "Enter") { e.preventDefault(); okb.click(); } });
    const mo = new MutationObserver(() => { if (!ov.isConnected) { mo.disconnect(); if (!done) res(false); } });
    mo.observe($("#modal-root"), { childList: true });
  });
}
/* ---- 標籤來源標示 ---- */
export const IDENTITY = ["character", "artist", "copyright"];
export const TIER = { 1: "Danbooru・原檔", 2: "Danbooru", 3: "Pixiv", 4: "WD Tagger" };
export const MTYPES = { illustration: "插畫", chibi: "Q 版", comic: "漫畫", "3d": "3D", cosplay: "Cosplay", photo: "真人照片", figure: "公仔/娃娃", other: "截圖／文字" };
export const MTYPE_SHORT = { illustration: "插畫", chibi: "Q 版", comic: "漫畫", "3d": "3D", cosplay: "Cos", photo: "照片", figure: "公仔", other: "截圖" };
export const MTYPE_UNSURE = "未定（AI 沒把握）";
export const OFFICIAL = { anime: "動畫截圖", manga: "漫畫截圖", novel: "小說插圖", movie: "電影截圖" };   // 官方作品（跟媒材分開記）
export const typeLabel = it => OFFICIAL[it.official] || (it.media_type && it.media_type !== "illustration" ? MTYPES[it.media_type] || it.media_type : "");
const SRC = {
  model: ["AI", "WD tagger 模型"], knn: ["庫", "你的圖庫（跟你確認過的圖很像）"], match: ["≈", "整張圖相似度"], ccip: ["臉", "CCIP 逐人比對"],
  album: ["集", "本子（整本的標籤）"], aimeta: ["詞", "AI 生成圖的提示詞裡有這個名字"], signature: ["簽", "圖上的簽名"], rule34: ["34", "Rule34 原檔（標籤僅供參考）"], e621: ["e6", "e621"], artist_scan: ["畫", "從畫師反推"],
  tracemoe: ["動", "trace.moe（以圖搜動畫）"], x: ["X", "X 貼文作者（從網站匯入）"], pixai: ["Px", "PixAI 模型（第二個 AI）"], pixiv_scan: ["P畫", "從畫師的 Pixiv 作品比對到"], x_scan: ["X畫", "從畫師的 X 媒體頁比對到"],
  ehentai: ["EH", "E-Hentai 以圖搜圖找到的本子"], gdl: ["dl", "貼文作者（gallery-dl 匯入）"], implied: ["⇒", "標籤蘊含（確認了另一個標籤，自動帶上）"], danbooru: ["D", "Danbooru 搜源"], online: ["D", "線上搜源"], pixiv: ["P", "Pixiv"], manual: ["手", "你手動加的"],
};
export function srcBadge(t) {
  const [lb, title] = SRC[t.source] || ["?", t.source || ""];
  const st = t.state === 1 ? "已確認" : t.state < 0 ? "已否決" : "候選";
  return `<span class="sb sb-${esc(t.source || "x")}" title="${esc(title)} · ${st}${t.score != null && t.state === 0 ? ` · ${Math.round(t.score * 100)}%` : ""}">${lb}</span>`;
}
export const SRC_LEGEND = `<div class="legend">${["model", "danbooru", "pixiv", "manual"].map(k => `${srcBadge({ source: k, state: 1 })} ${SRC[k][1].replace(/（.*）/, "")}`).join("")}<span class="lg-ok">綠底＝已確認</span></div>`;

export const MISS_LABEL = { all: "完全未識別", character: "缺角色", artist: "缺創作者", people: "多人圖缺角色" };
const optExtra = t => (t.zh ? `<span class="alias">${esc(t.zh)}</span>` : "") + (t.alias ? `<span class="alias">${esc(t.alias)}</span>` : "") + (t.copyright ? `<span class="alias">· ${esc(t.copyright.replace(/_/g, " "))}</span>` : "");
export const fmtAgo = ts => { if (!ts) return "—"; const d = (Date.now() / 1000 - ts); return d < 90 ? "剛剛" : d < 3600 ? `${Math.round(d / 60)} 分鐘前` : d < 86400 ? `${Math.round(d / 3600)} 小時前` : `${Math.round(d / 86400)} 天前`; };
export const fmtEta = s => s == null ? "" : s < 60 ? `${s} 秒` : s < 3600 ? `${Math.round(s / 60)} 分鐘` : `${(s / 3600).toFixed(1)} 小時`;
export const catName = c => ({ character: "角色", custom: "自訂", general: "一般", copyright: "作品", artist: "創作者" }[c] || c);
export const fmtN = n => (n || 0).toLocaleString();
// 外部資料（Danbooru 來源欄位等）的網址只接受 http/https，擋掉 javascript: 這類連結
export const safeUrl = u => /^https?:\/\//i.test(String(u || "")) ? u : "#";

export const QUERY_HELP = `<div class="qhelp"><b>搜尋語法</b>（空白＝而且）
  <div><code>hatsune_miku</code> <code>初音</code> <code>雙馬尾</code> 標籤、角色的中日文名、一般 tag 的中文</div>
  <div><code>-tag</code> 排除　<code>a|b</code> 或 <code>a OR b</code> 任一　<code>*_(arknights)</code> 萬用字元</div>
  <div><code>rating:safe</code> <code>rating:nsfw</code> <code>rating:q,e</code>　<code>type:chibi</code> <code>type:cosplay</code>　<code>is:video</code> <code>is:multi</code></div>
  <div><code>date:2024</code> <code>date:2024-05</code> <code>date:2023..2024-06</code> <code>added:7d</code>（拍攝/加入時間）</div>
  <div><code>w:>=1920</code> <code>h:<800</code> <code>ratio:portrait</code> <code>ratio:>1.5</code>　<code>chars:>=2</code>（角色數）</div>
  <div><code>path:桌布</code> <code>series:project_sekai</code> <code>src:none</code> <code>src:pixiv</code> <code>is:noartist</code> <code>is:candidate</code> <code>is:icloud</code></div>
  <div><code>is:noanime</code> 非動漫（照片＋截圖＋梗圖）　<code>is:real</code> 照片　<code>is:screenshot</code> 截圖　<code>is:meme</code> 梗圖　<code>is:anime</code> 動漫圖　<code>guess:表情包</code> AI 從你的快速標籤學、覺得也是的</div></div>`;

/* 圖片載好才淡入（CSS 的 img.ld）：load 事件不冒泡，用捕獲階段一次處理全部 */
document.documentElement.classList.add("fx");   // 有這段程式才啟用淡入（避免舊版快取的 JS 配新版 CSS 時縮圖全部看不到）
document.addEventListener("load", e => { if (e.target.tagName === "IMG") e.target.classList.add("ld"); }, true);
document.addEventListener("error", e => { if (e.target.tagName === "IMG") e.target.classList.add("ld"); }, true);

/* 沒接住的錯誤：以前按了沒反應、只在主控台有一行紅字 → 至少跳一個提示（同一個訊息 10 秒內只跳一次） */
{
  let last = "", at = 0;
  window.addEventListener("unhandledrejection", e => {
    const msg = e.reason?.message || String(e.reason || "");
    if (!msg || /AbortError|aborted|Failed to fetch|NetworkError|Load failed/i.test(msg)) return;   // 連不上伺服器：左下角已經會顯示
    if (msg === last && Date.now() - at < 10000) return;
    last = msg; at = Date.now();
    try { toast(`出了點問題：${msg}`); } catch { }
  });
}

/* ============ 頁面動作（v4.2）：常用的 1–2 個直接放，其他（含刪除、解散）收進 ⋯ ============ */
export const pageMenu = (id, items) => `<span class="moreq"><button class="ghost" data-pmenu="${id}" aria-haspopup="true" title="更多">⋯</button><span class="popmenu hidden" id="${id}">${items.filter(Boolean).join("")}</span></span>`;
document.addEventListener("click", e => {
  const b = e.target.closest("[data-pmenu]");
  if (b) { e.stopPropagation(); const m = document.getElementById(b.dataset.pmenu); document.querySelectorAll(".moreq .popmenu").forEach(x => { if (x !== m) x.classList.add("hidden"); }); m?.classList.toggle("hidden"); return; }
  if (e.target.closest(".moreq .popmenu button, .moreq .popmenu a")) document.querySelectorAll(".moreq .popmenu").forEach(x => x.classList.add("hidden"));
  else if (!e.target.closest(".moreq")) document.querySelectorAll(".moreq .popmenu").forEach(x => x.classList.add("hidden"));
});
