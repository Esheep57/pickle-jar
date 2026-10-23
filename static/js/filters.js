// ES module（2026-10）：用到別的檔案的東西都要在這裡 import；瀏覽器會在載入時檢查名字對不對。
import { $, $$, MISS_LABEL, MTYPES, OFFICIAL, S, TIER, api, catName, esc, fmtDur, fmtN, srcBadge, store, svg, typeLabel } from "./core.js";
import { applyNsfwUI, go, loadSidebar, openShortcuts, toggleXray } from "./shell.js";
import { loadAllItems, render, runSearch } from "./grid.js";
import { gridParams } from "./layout.js";
import { ART_BUCKETS, BUCKETS, PILES } from "./unrec.js";
import { reviewParams } from "./review.js";
import { findModal } from "./pages.js";
/* ============ 目前生效的篩選 ============
   規則：任何會讓畫面「變少」的條件都要列在這裡。畫面空的時候一定把它們列出來、可以一個個拿掉，
   而且有篩選時絕不說「都處理完了」——之前好幾次空畫面都是藏起來的篩選造成的。
   新增篩選時：在這裡加一行，並在 FILTER_KEYS 加上它的「沒篩選」值。 */
const FILTER_KEYS = { ftag: "", q: "", src: "", seriesChar: "", seriesMiss: "", bucket: "", mtype: "", nsfw: "show", rtag: "", rcat: "all", rsrc: "", rmin: 0, rctx: "", rseg: "" };
function activeFilters() {
  const f = [], v = S.view, add = (label, clear) => f.push({ label, clear });
  if (v === "review") {
    if (S.rmode === "focus" && S.ftag) add(`專注：${S.ftag}`, () => { S.ftag = ""; });
    if (S.rtag) add(`只看 ${S.rtag}`, () => { S.rtag = ""; store.set("rtag", ""); });
    if (S.rcat && S.rcat !== "all") add(`類別：${catName(S.rcat)}`, () => { S.rcat = "all"; store.set("rcat", "all"); });
    if (S.rtier === "source" && S.rsrc) add(`來源：${({ danbooru: "Danbooru", e621: "e621", rule34: "Rule34", signature: "圖上簽名", artist_scan: "從畫師反推" })[S.rsrc] || S.rsrc}`, () => { S.rsrc = ""; });
    if (S.rmin) add(`高信心 ≥ ${Math.round(S.rmin * 100)}%`, () => { S.rmin = 0; S.rctx = ""; S.rseg = ""; });
  } else {
    if (S.q) add(`搜尋「${S.q}」`, () => { S.q = ""; });
    if (v === "tag" && S.src) add("候選來源", () => { S.src = ""; });
    if (v === "series" && S.seriesChar) add(`只看 ${S.seriesChar}`, () => { S.seriesChar = ""; });
    if (v === "series" && S.seriesMiss) add(S.seriesMiss === "noartist" ? "沒標作者的" : "沒標角色的", () => { S.seriesMiss = ""; });
    if (v === "unrecognized" && S.bucket) add(`${S.bucket.startsWith("p_") ? "分堆" : "分流"}：${([...BUCKETS, ...PILES, ...ART_BUCKETS].find(b => b[0] === S.bucket) || [])[1] || S.bucket}`, () => { S.bucket = ""; store.set("ubucket", ""); });
  }
  if (S.mtype) add(`媒材：${MTYPES[S.mtype] || OFFICIAL[S.mtype] || (S.mtype === "unsure" ? "未定" : S.mtype)}`, () => { S.mtype = ""; });
  if (S.nsfw === "hide") add("NSFW 已隱藏", () => { S.nsfw = "blur"; store.set("nsfw", "blur"); applyNsfwUI(); loadSidebar(); });
  return f;
}
// 把篩選全部拿掉（搜尋字除外，搜尋框本來就看得到）時會有幾筆：告訴你「被擋住了 N 筆」
async function unfilteredCount() {
  const save = Object.fromEntries(Object.keys(FILTER_KEYS).map(k => [k, S[k]]));
  const off = S.rOffset, items = S.items;
  let p;
  // 搜尋頁：搜尋字本身就是目的，保留；角色頁、作品頁…：帶過來的搜尋字也是篩選（以前會說「這裡沒有項目」，其實是被搜尋字擋住）
  try { Object.assign(S, FILTER_KEYS, { q: S.view === "all" ? S.q : "" }); S.rOffset = 0; S.items = []; p = S.view === "review" ? reviewParams() : gridParams(); }
  finally { Object.assign(S, save); S.rOffset = off; S.items = items; }
  p.set("limit", 1); p.set("offset", 0);
  try { return (await api((S.view === "review" ? "/api/review?" : "/api/media?") + p)).total; } catch { return null; }
}
// 空畫面：沒有篩選 → 顯示該頁自己的說明（done）；有篩選 → 列出篩選、可以拿掉，不說「都完成了」
export function emptyHTML(done) {
  const f = activeFilters();
  S.emptyDone = done || (S.view === "all" ? "" : "這裡沒有項目");
  if (!f.length) {
    if (done) return `<div class="empty">${done}</div>`;
    if (S.view === "all") return `<div class="empty"><b>圖庫是空的</b><br>到左下角「設定」加入圖片/影片資料夾，程式會自動掃描並標記。</div>`;
    return `<div class="empty">這裡沒有項目</div>`;
  }
  return `<div class="empty filt-empty">目前的篩選下沒有項目<span id="emp-n"></span>
    <div class="fchips">${f.map((x, i) => `<button class="btn sm" data-fx="${i}">${esc(x.label)} ✕</button>`).join("")}</div>
    ${f.length > 1 ? `<button class="btn primary sm" data-fx="all">全部拿掉</button>` : ""}<div id="emp-why"></div></div>`;
}
// 搜尋沒有結果：告訴你是哪個字找不到，列出「你是不是要找」（以前只說「沒有項目」）
async function explainEmpty(gen) {
  if (!S.q || !$("#emp-why")) return;
  let ex; try { ex = await api(`/api/query/explain?q=${encodeURIComponent(S.q)}`); } catch { return; }
  if (gen !== S.gen || !$("#emp-why")) return;
  const rows = ex.map(e => {
    const st = !e.found ? `找不到叫「${esc(e.term)}」的角色、作品或標籤` : e.n ? `單獨搜有 ${fmtN(e.n)} 張` : "沒有圖";
    const sug = e.suggest?.length ? `<div class="sugs">你是不是要找：${e.suggest.map(x => `<button class="btn sm" data-sug="${esc(x.name)}" data-term="${esc(e.term)}" title="${esc(x.name)}">${esc(x.display || x.name)}<span class="mute"> · ${catName(x.category)}</span></button>`).join("")}</div>` : "";
    return `<div class="why"><b>${esc(e.term)}</b>：${st}${sug}</div>`;
  }).join("");
  const el = $("#emp-why"); el.innerHTML = rows;
  $$("[data-sug]", el).forEach(b => b.onclick = () => {
    const t = b.dataset.term, neg = t.startsWith("-") ? "-" : "";
    const q = ex.length === 1 ? neg + b.dataset.sug : S.q.split(/\s+/).join(" ").replace(t, neg + b.dataset.sug);
    runSearch(q);
  });
}
export function bindEmpty(gen) {
  const box = $(".filt-empty"); if (!box) return;
  explainEmpty(gen);
  const f = activeFilters();
  $$("[data-fx]", box).forEach(b => b.onclick = () => {
    (b.dataset.fx === "all" ? f : [f[+b.dataset.fx]]).forEach(x => x.clear());
    render();
  });
  unfilteredCount().then(n => {
    if (gen !== S.gen || !$(".filt-empty")) return;
    if (n) $("#emp-n").innerHTML = `，但拿掉篩選後有 <b>${fmtN(n)}</b> 筆`;
    else if (n === 0 && S.emptyDone) $(".filt-empty").outerHTML = `<div class="empty">${S.emptyDone}</div>`;   // 拿掉篩選也是空的 → 真的做完了
  });
}
/* ============ 查詢列：條件是 token，點一下展開分面；Enter 把打的字加進搜尋 ============ */
const SCOPE_LABEL = { videos: "影片", nsfw: "NSFW", official: "官方截圖", excluded: "以前排除的", errors: "讀取失敗" };
export function qboxHTML() {
  const toks = [];
  if (["tag", "series", "album"].includes(S.view)) {    // 角色／標籤頁、作品頁、本子：同一條底部查詢列，範圍是這一頁
    const nm = S.view === "tag" ? (S.tagInfo?.display || S.tag) : S.view === "album" ? (S.album?.id === S.albumId ? S.album.title : "") : (S.seriesData?.display || S.seriesKey || "");
    toks.push(`<span class="qtok scope"><i>${S.view === "tag" ? (S.tagInfo?.category ? catName(S.tagInfo.category) : "標籤") : { series: "作品", album: "本子" }[S.view]}</i>${esc(nm)}</span>`);
  }   // 圖庫的分頁（影片、NSFW、官方…）在標題下，查詢列不再重複
  activeFilters().forEach((f, i) => { if (f.label !== "NSFW 已隱藏") toks.push(`<span class="qtok"><span>${esc(f.label)}</span><button data-qx="${i}" title="拿掉這個條件">×</button></span>`); });
  const find = S.view === "unrecognized" ? `<button class="qlink" id="q-find" title="像素指紋、圖上簽名、e621、Rule34、以圖搜源">找出處</button>` : "";
  const star = S.q ? `<button class="qic" id="q-star" title="存成智慧相簿（之後新進的圖符合就自動出現）">${svg("saved", 16)}</button>` : "";
  const ph = ["tag", "series", "album"].includes(S.view) ? "在這裡面找…" : toks.length ? "加條件…" : "加條件，或打 is:multi、作品名、角色名…";
  return `<div class="qbox" id="qbox">${svg("search", 16)}${toks.join("")}<input id="qin" autocomplete="off" placeholder="${ph}"></div>${find}${star}`;
}
export function bindQbar() {
  const tb = $("#toolbar"), fac = $("#facets"), vm = $("#viewmenu"), inp = $("#qin");
  const open = (el, on) => { if (!el) return; el.classList.toggle("hidden", !on); if (on) (el === fac ? vm : fac)?.classList.add("hidden"); };
  const hasFacets = () => fac && fac.textContent.trim();
  $("#viewbtn", tb).onclick = e => { e.stopPropagation(); open(vm, vm.classList.contains("hidden")); };
  $$("[data-xray]", tb).forEach(b => b.onclick = () => toggleXray());
  $("[data-open-facets]", tb)?.addEventListener("click", e => { e.stopPropagation(); open(fac, fac.classList.contains("hidden")); });
  $("[data-scope-x]", tb)?.addEventListener("click", () => go("all"));
  $$("[data-qx]", tb).forEach(b => b.onclick = e => { e.stopPropagation(); const f = activeFilters().filter(x => x.label !== "NSFW 已隱藏")[+b.dataset.qx]; f?.clear(); if (S.view === "all" && !S.q) go("all"); else render(); });
  $("#q-find")?.addEventListener("click", () => findModal(S.miss));
  $("#q-star")?.addEventListener("click", () => $("#q-save")?.click());
  $("#q-help")?.addEventListener("click", () => openShortcuts());
  $("#qbox")?.addEventListener("click", e => { if (e.target.closest("button")) return; inp?.focus(); });
  if (inp) {
    inp.addEventListener("focus", () => { if (hasFacets()) open(fac, true); });
    inp.addEventListener("input", () => {   // 打字時分面跟著篩：只留名字有這幾個字的按鈕
      const t = inp.value.trim().toLowerCase();
      $$("#facets button[data-b], #facets button[data-m], #facets button[data-lv], #facets button[data-off], #facets button[data-ser]").forEach(b => b.classList.toggle("fhide", !!t && !b.textContent.toLowerCase().includes(t)));
    });
    inp.addEventListener("keydown", e => {
      e.stopPropagation();
      if (e.key === "Escape") { inp.value = ""; inp.blur(); open(fac, false); return; }
      if (e.key === "Backspace" && !inp.value) { const x = $$("[data-qx]", tb).pop(); if (x) x.click(); return; }
      if (e.key !== "Enter") return;
      const t = inp.value.trim(); if (!t) return;
      const vis = $$("#facets button:not(.fhide)[data-b], #facets button:not(.fhide)[data-ser], #facets button:not(.fhide)[data-m], #facets button:not(.fhide)[data-lv]");
      if (vis.length === 1 && !/[:\-]/.test(t)) { vis[0].click(); return; }   // 打的字剛好是一個分面 → 直接套用
      const q = [S.q, t].filter(Boolean).join(" ");
      if (S.view === "all") runSearch(q); else { S.q = q; render(); }
    });
  }
  if (!S._qbarDoc) {
    S._qbarDoc = true;
    document.addEventListener("mousedown", e => {
      if (e.target.closest("#facets, #viewmenu, #viewbtn, #qbox, [data-open-facets]")) return;
      $("#facets")?.classList.add("hidden"); $("#viewmenu")?.classList.add("hidden");
    });
  }
}

export function cellHTML(it, i, style = "", virt = false) {
  const cls = ["cell"]; if (it.nsfw) cls.push("nsfw"); if (S.selected.has(it.id)) cls.push("selected"); if (it.kind === "video") cls.push("vid");
  let extra = it.kind === "video" ? `<span class="vg" aria-hidden="true"></span>` : "";
  const mt = typeLabel(it);
  if (it.kind === "video" || mt) extra += `<span class="badge">${it.kind === "video" ? `▶ ${fmtDur(it.duration)}` : ""}${it.kind === "video" && mt ? " · " : ""}${mt}</span>`;
  if (S.view === "tag" && it.state === 0 && it.tier) {
    extra += `<span class="cap" title="${esc(it.reason || "")}">${srcBadge(it)} ${TIER[it.tier] || ""}</span>`;
  } else if (S.view === "tag" && it.state != null) {
    extra += `<span class="st s${it.state}" title="${it.state === 1 ? "已確認" : it.state < 0 ? "已否決" : "候選"}"></span>`;
    const low = it.state === 0 && it.score != null && it.score < 0.9;
    if (it.state === 0) extra += `<span class="mb ${low ? "low" : ""}">${srcBadge(it)}${it.score != null ? ` ${Math.round(it.score * 100)}` : ""}</span>`;
  }
  if (it.have) extra += `<span class="cap crowdcap" title="已標：${esc(it.have.join("、"))}（WD 看到 ${it.people} 人）">${it.have.length}/${it.people}<span class="nm"> · ${esc(it.have.join("、"))}</span></span>`;
  const big = S.size > 260 || (style && /height:(\d{3})/.test(style) && +style.match(/height:(\d+)/)[1] > 300);
  return `<div class="${cls.join(" ")}" data-id="${it.id}" data-i="${i}" ${style ? `style="${style}"` : ""}><img loading="${virt ? "eager" : "lazy"}" decoding="async" src="/t/${it.id}${big ? "?size=l" : ""}${it.tv ? `${big ? "&" : "?"}v=${it.tv}` : ""}" onerror="this.parentNode.classList.add('missing-thumb')"><span class="sel"></span>${extra}</div>`;
}
export function updateFooter() {
  const el = $("#foot-l"); if (!el) return;
  const more = S.view !== "review" && S.layout && !S.done && S.items.length < S.total;
  el.innerHTML = `${S.total.toLocaleString()} 張 · 已載入 ${S.items.length.toLocaleString()}` + (S.selected.size ? ` · 已選 ${S.selected.size}` : "")
    + (S.albumHidden && S.view !== "album" && S.view !== "review" ? ` · <span class="mute" title="本子裡的頁不放在這裡：到「本子」整本看，或在 ≡ 選單勾「也顯示本子裡的頁」">另 ${S.albumHidden.toLocaleString()} 張在本子裡</span>` : "")
    + (more ? (S.loadAll ? ` · <span class="mute">全部載入中…</span>` : ` · <a class="lnk" id="load-all">全部載入</a>`) : "");
  const la = $("#load-all"); if (la) la.onclick = loadAllItems;
}
