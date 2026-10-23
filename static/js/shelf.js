// ES module（2026-10）：用到別的檔案的東西都要在這裡 import；瀏覽器會在載入時檢查名字對不對。
import { $, $$, api, apiNsfw, closeModal, debounce, esc, fmtN, modal, noAlbumQS, pageMenu, S, safeUrl, store, svg, toast } from "./core.js";
import { go, loadSidebar } from "./shell.js";
import { linkIcons, render, tagDelete } from "./grid.js";
import { bindLibTabs, libTabs, reloadGrid } from "./unrec.js";
/* ============ 角色書架 ============ */
export async function renderShelf(gen) {
  $("#crumb").innerHTML = `<b>角色</b>`;
  $("#content").innerHTML = `<div class="empty">載入中…</div>`;
  let d;
  try { d = await api(`/api/shelf?nsfw=${S.nsfw}${noAlbumQS()}`); } catch (e) { toast(e.message); return; }
  if (gen !== S.gen) return;
  S.shelf = d;
  S.shelfTab = S.shelfTab || "overview";
  S.shelfMode = store.get("shelfMode", "shelf");
  const recentChars = d.chars.filter(c => c.recent > 0);
  $("#head").innerHTML = `<div class="headmain"><h1 title="${fmtN(d.chars.length)} 位角色 · ${fmtN(d.series.length)} 部作品">角色</h1>
      <div class="sub">${d.only_candidates ? `<a class="lnk" id="go-review">${d.only_candidates} 位只有候選</a>` : ""}
        ${d.unknown || d.unfetched ? `${d.only_candidates ? " · " : ""}<span title="${d.guessed ? `另外 ${d.guessed} 位先用名字或同一張圖上的作品推測（標「推測」），Danbooru 查到就以它為準` : ""}">${d.unknown} 位不知道作品${d.unfetched ? `（${d.unfetched} 位還沒查 Danbooru）` : ""}</span>${d.unfetched ? ` <a class="lnk" id="fetch-missing">現在補查</a>` : ""}` : ""}</div></div>`;
  $("#topslot").innerHTML = d.suggest.length ? `<a class="tophint" id="sug-btn">${d.suggest.length} 個自建角色能對上 Danbooru</a>` : "";
  // v4.2 每頁同一個骨架：上面是這頁是什麼（標題、頁內分頁）；下面一條查詢列（找名字｜≡ 顯示方式與工具）
  $("#head").insertAdjacentHTML("beforeend", `<div class="chiprow pagetabs">
      <button class="chip2 ${S.shelfTab === "overview" ? "on" : ""}" data-tab="overview">概況</button>
      <button class="chip2 ${S.shelfTab === "recent" ? "on" : ""}" data-tab="recent">近期新增 <i>${recentChars.length}</i></button>
      <button class="chip2 ${S.shelfTab === "series-all" ? "on" : ""}" data-tab="series-all">依作品 <i>${d.series.length}</i></button>
      <select id="all-series" title="跳到某部作品"><option value="">跳到作品… ▾</option>${d.series.map(g => `<option value="s:${esc(g.key)}" ${S.shelfTab === "series:" + g.key ? "selected" : ""}>${esc(g.display)}（${fmtN(g.n)}）</option>`).join("")}</select>
    </div>`);
  $("#toolbar").className = "qtb";
  $("#toolbar").innerHTML = `<div class="qrow2"><div class="qbox" id="qbox">${svg("search", 16)}<span class="qtok scope"><i>找</i>角色或作品</span><input id="shelf-q" autocomplete="off" placeholder="輸入角色或作品名（中文、日文、英文都行）" value="${esc(S.shelfQ || "")}"></div>
      <span class="moreq"><button class="qic" id="shelf-more" title="顯示方式、中文名整理、從 Danbooru 抓名字" aria-haspopup="true">${svg("menu", 17)}</button><span class="popmenu hidden" id="shelf-pop">
        <div class="pmrow"><span>顯示</span><span class="seg" id="shelf-mode">${[["shelf", "書架"], ["list", "列表"]].map(([k, l]) => `<button data-m="${k}" class="${k === S.shelfMode ? "on" : ""}">${l}</button>`).join("")}</span></div>
        <button class="ghost" id="name-fix">中文名整理</button><button class="ghost" id="fetch-info" title="從 Danbooru 抓角色與作品的中文／日文名">從 Danbooru 抓名字</button></span></span></div>`;
  const setTab = t => {
    if (t.startsWith("series:") && t.length > 7) return go("series", t.slice(7));   // 作品 → 作品頁（該作品的圖庫＋角色）
    S.shelfTab = t; S.shelfQ = ""; drawShelf(); $$("#head .pagetabs .chip2").forEach(b => b.classList.toggle("on", b.dataset.tab === t));
  };
  $$("#head .pagetabs .chip2").forEach(b => b.onclick = () => setTab(b.dataset.tab));
  $("#shelf-more").onclick = e => { e.stopPropagation(); $("#shelf-pop").classList.toggle("hidden"); };
  document.addEventListener("mousedown", e => { if (!e.target.closest(".moreq")) $("#shelf-pop")?.classList.add("hidden"); });
  $("#all-series").onchange = e => setTab(e.target.value ? "series:" + e.target.value.slice(2) : "overview");
  $("#shelf-q").oninput = debounce(e => { S.shelfQ = e.target.value.trim(); drawShelf(); }, 150);
  $("#shelf-q").addEventListener("keydown", e => e.stopPropagation());
  $$("#shelf-mode button").forEach(b => b.onclick = () => { S.shelfMode = b.dataset.m; store.set("shelfMode", S.shelfMode); $$("#shelf-mode button").forEach(x => x.classList.toggle("on", x === b)); drawShelf(); });
  const gr = $("#go-review"); if (gr) gr.onclick = () => { S.rcat = "character"; go("review"); };
  const fm = $("#fetch-missing"); if (fm) fm.onclick = async () => { try { const r = await api("/api/online/taginfo", { all_missing: true }); toast(`已排入 ${r.queued} 個角色／作品到 Danbooru 查資料（約每秒 1 個，背景進行；點右上角進度看排程）`); } catch (e) { toast(e.message); } };
  const sb = $("#sug-btn"); if (sb) sb.onclick = suggestModal;
  const nf = $("#name-fix"); if (nf) nf.onclick = () => namesModal();
  $("#fetch-info").onclick = async () => {
    try { const r = await api("/api/online/taginfo", { all_missing: true }); toast(r.queued ? `已排入 ${r.queued} 個（背景進行，約每秒 1 個）` : "都已有資料"); } catch (e) { toast(e.message); }
  };
  $("#content").onclick = e => {
    const cp = e.target.closest("[data-cover-pick]"); if (cp) { e.stopPropagation(); return coverPicker(cp.dataset.coverPick); }
    const so = e.target.closest("[data-ssort]"); if (so) { store.set("seriesSort", so.dataset.ssort); return drawShelf(); }
    const t = e.target.closest("[data-tag]"); if (t) return go("tag", t.dataset.tag);
    const sr = e.target.closest("[data-series-tab]"); if (sr) { setTab("series:" + sr.dataset.seriesTab); $("#content").scrollTop = 0; return; }
    const all = e.target.closest("[data-see]"); if (all) return go("series", all.dataset.see);
    const more = e.target.closest("[data-tabgo]"); if (more) return setTab(more.dataset.tabgo);
  };
  drawShelf();
  $("#foot-l").textContent = `${fmtN(d.chars.length)} 位角色 · ${fmtN(d.series.length)} 部作品`;
}
// 角色卡：方形封面，名字、作品、張數寫在下面
const pcard = c => `<div class="pcard" data-tag="${esc(c.name)}" title="${esc(c.name)}">
  <div class="pc-img">${c.cover ? `<img loading="lazy" src="/t/${c.cover}">` : ""}</div>
  <div class="pc-cap"><b>${esc(c.display)}</b><span>${c.series_display ? `${esc(c.series_display)} · ` : ""}${fmtN(c.n)} 張${c.recent ? ` <em title="近期新增">+${c.recent}</em>` : ""}${c.cand ? ` · 候選 ${c.cand}` : ""}${c.skins.length ? ` · ${c.skins.length} 個造型` : ""}</span></div></div>`;
// 收藏排行：一排六張直式封面，名次用宋體數字寫在名字前面（不疊在圖上）
const rankList = list => `<div class="rgrid">${list.map((c, i) => `<div class="rcard" data-tag="${esc(c.name)}" title="${esc(c.name)}">
    <div class="pc-img">${c.cover ? `<img loading="lazy" src="/t/${c.cover}${i < 6 ? "?size=l" : ""}">` : ""}</div>
    <div class="rc-cap"><span class="rc-n">${i + 1}</span><div class="rc-t"><b>${esc(c.display)}</b>
      <span>${c.series_display ? `${esc(c.series_display)} · ` : ""}${fmtN(c.n)} 張${c.recent ? ` <em title="近期新增">+${c.recent}</em>` : ""}</span></div></div></div>`).join("")}</div>`;
// 近期新增：對齊的四欄小卡（直式小圖、名字、作品、新增幾張）
const newChip = c => `<div class="nchip" data-tag="${esc(c.name)}" title="${esc(c.name)}">${c.cover ? `<img loading="lazy" src="/t/${c.cover}">` : `<span class="nc-ph"></span>`}
  <span class="nc-t"><b>${esc(c.display)}</b><small>${esc(c.series_display || "")}</small></span><em>+${c.recent}</em></div>`;
// 作品的封面拼貼：最多四張（作品封面＋主要角色），一個框裡排成 2×2
const mosaicOf = (g, cls = "") => {
  const ids = [...new Set([...(g.covers || []), ...(g.top_chars || []).map(c => c.cover)].filter(Boolean))].slice(0, 4);
  return `<div class="smos ${cls}" data-n="${ids.length}">${ids.map((m, i) => `<img loading="lazy" src="/t/${m}${i === 0 && cls !== "small" ? "?size=l" : ""}" alt="">`).join("")}`;
};
const stile = g => `<div class="stile" tabindex="0" role="link" data-series-tab="${esc(g.key)}" title="${esc(g.key || "未知作品")}">
  ${mosaicOf(g)}${g.key ? `<button class="stk-cover" data-cover-pick="${esc(g.key)}" title="選一張當這部作品的封面">${g.custom_cover ? "✓ 自選封面" : "換封面"}</button>` : ""}</div>
  <div class="st-t"><b>${g.pinned ? `<span class="stk-pin">★</span>` : ""}${esc(g.display)}</b><span>角色 ${g.chars}｜圖 ${fmtN(g.n)}${g.recent ? `｜<em title="近期新增">+${g.recent}</em>` : ""}</span></div></div>`;
const SERIES_SORT = [["n", "張數"], ["recent", "最近新增"], ["chars", "角色數"], ["name", "名稱"]];
function sortedSeries(list) {
  const k = store.get("seriesSort", "n"), a = list.slice();
  const f = { n: (x, y) => y.n - x.n, recent: (x, y) => y.recent - x.recent || y.n - x.n, chars: (x, y) => y.chars - x.chars || y.n - x.n,
    name: (x, y) => String(x.display).localeCompare(String(y.display), "zh-Hant") }[k] || ((x, y) => y.n - x.n);
  return a.sort((x, y) => (y.pinned ? 1 : 0) - (x.pinned ? 1 : 0) || (!x.key) - (!y.key) || f(x, y));   // 置頂的在前、未知作品在最後
}
const seriesSortBar = () => `<span class="seg sm right" id="ssort">${SERIES_SORT.map(([k, l]) => `<button data-ssort="${k}" class="${store.get("seriesSort", "n") === k ? "on" : ""}">${l}</button>`).join("")}</span>`;
// 選封面（作品／角色／創作者共用）：列出這個對象的圖，點一張就設定
async function pickCover({ title, hint, params, current, save }) {
  const m = modal(`<h3>${esc(title)}</h3><div class="mbody"><p class="mute" style="margin-top:0">${hint}</p>
    <div class="cpick" id="cpick"><div class="empty">載入中…</div></div></div>
    <div class="mfoot">${current ? `<button class="btn" id="cp-auto" style="margin-right:auto">恢復自動</button>` : ""}<button class="btn" data-close>取消</button></div>`, null, "wide");
  const set = async id => {
    try { await save(id); closeModal(); toast(id ? "已設為封面" : "已恢復自動挑選封面"); S.seriesData = null; render(); }
    catch (e) { toast(e.message); }
  };
  const au = $("#cp-auto", m); if (au) au.onclick = () => set(null);
  let d; try { d = await api("/api/media?" + new URLSearchParams({ view: "all", nsfw: apiNsfw(), limit: 300, ...params })); } catch (e) { toast(e.message); return; }
  const box = $("#cpick", m); if (!box) return;
  box.innerHTML = d.items.length ? d.items.map(it => `<img loading="lazy" src="/t/${it.id}" data-id="${it.id}" class="${it.id === current ? "on" : ""}${it.nsfw ? " nsfw" : ""}">`).join("")
    + (d.total > d.items.length ? `<div class="mute" style="grid-column:1/-1;text-align:center">只列出前 ${d.items.length} 張；其他的圖可以在大圖檢視裡按標籤旁的 ★</div>` : "") : `<div class="empty">還沒有已確認的圖</div>`;
  box.onclick = e => { const im = e.target.closest("img[data-id]"); if (im) set(+im.dataset.id); };
}
function coverPicker(key) {
  const g = S.shelf?.series.find(x => x.key === key) || S.seriesData || { key, display: key };
  return pickCover({ title: `「${g.display || key}」的封面`, current: g.custom_cover, params: { q: `series:${key}` },
    hint: "點一張圖當作品封面（書架和作品頁的第一張），另外兩張會自動從不同角色挑、不會重複。也可以在大圖檢視裡按作品標籤旁的 ★。",
    save: id => api(`/api/series/${encodeURIComponent(key)}/cover`, { media_id: id }) });
}
export function tagCoverPicker(t) {
  const what = t.category === "artist" ? "創作者" : t.category === "copyright" ? "作品" : "角色";
  if (t.category === "copyright") return coverPicker(t.name);
  return pickCover({ title: `「${t.display || t.name}」的封面`, current: t.cover, params: { q: t.name },
    hint: `點一張圖當${what}封面（這一頁上方的背景、角色書架和列表的卡片）。只列出已確認的圖。也可以在大圖檢視裡按${what}標籤旁的 ★。`,
    save: id => api(`/api/tag/${encodeURIComponent(t.name)}`, { cover: id || 0 }) });
}
function shelfMatch(c, q) {
  q = q.toLowerCase();
  return [c.name, c.display, c.series || "", c.series_display || "", ...c.aliases, ...(S.shelf.series.find(g => g.key === (c.series || ""))?.aliases || [])]
    .some(x => String(x).toLowerCase().replace(/_/g, " ").includes(q.replace(/_/g, " ")));
}
function shelfList(list) {
  return `<table class="clist"><thead><tr><th></th><th>角色</th><th>作品</th><th>張數</th><th>近 ${S.shelf.days} 天</th><th>候選</th></tr></thead><tbody>
    ${list.map(c => `<tr data-tag="${esc(c.name)}"><td>${c.cover ? `<img loading="lazy" src="/t/${c.cover}">` : ""}</td><td><b>${esc(c.display)}</b><br><small>${esc(c.name)}${c.skins.length ? ` · ${c.skins.length} 個造型` : ""}</small></td>
      <td>${esc(c.series_display || "")}</td><td>${fmtN(c.n)}</td><td>${c.recent ? `<em>+${c.recent}</em>` : ""}</td><td>${c.cand || ""}</td></tr>`).join("")}</tbody></table>`;
}
function drawShelf() {
  const d = S.shelf, $c = $("#content"), list = S.shelfMode === "list";
  const el = { set innerHTML(h) { $c.innerHTML = `<div class="shelfv">${h}</div>`; } };   // 書架的樣式只套在這頁（#content 是各頁共用的）
  if (!d.chars.length) { el.innerHTML = `<div class="empty"><b>還沒有已確認的角色</b><br>到「待確認」確認搜源或 AI 找到的角色，或在圖庫選取圖片按 A 加上角色。</div>`; return; }
  if (S.shelfQ) {
    const cs = d.chars.filter(c => shelfMatch(c, S.shelfQ));
    const ss = d.series.filter(g => [g.key, g.display, ...g.aliases].some(x => String(x).toLowerCase().replace(/_/g, " ").includes(S.shelfQ.toLowerCase())));
    el.innerHTML = (ss.length ? `<h3 class="sech">作品 <small>${ss.length}</small></h3><div class="stiles">${ss.map(stile).join("")}</div>` : "")
      + `<h3 class="sech">角色 <small>${cs.length}</small></h3>` + (list ? shelfList(cs) : `<div class="pgrid">${cs.map(c => pcard(c)).join("")}</div>`)
      + (!cs.length && !ss.length ? `<div class="empty">找不到「${esc(S.shelfQ)}」</div>` : "");
    return;
  }
  if (S.shelfTab === "recent") {
    const cs = d.chars.filter(c => c.recent > 0).sort((a, b) => b.recent - a.recent);
    el.innerHTML = `<h3 class="sech">近期新增 <small>近 ${d.days} 天有新圖的角色 · ${cs.length} 位</small></h3>` + (list ? shelfList(cs) : `<div class="pgrid">${cs.map(c => pcard(c)).join("")}</div>`);
    return;
  }
  if (S.shelfTab === "series-all") {
    el.innerHTML = `<h3 class="sech">依作品 <small>${d.series.length} 部</small>${seriesSortBar()}</h3><div class="stiles">${sortedSeries(d.series).map(stile).join("")}</div>`;
    return;
  }
  if (S.shelfTab.startsWith("series:")) {
    const key = S.shelfTab.slice(7), g = d.series.find(x => x.key === key);
    if (!g) { S.shelfTab = "overview"; return drawShelf(); }
    const cs = d.chars.filter(c => (c.series || "") === key);
    const tiers = [["主力", "20 張以上", c => c.n >= 20], ["常見", "5–19 張", c => c.n >= 5 && c.n < 20], ["零星", "1–4 張", c => c.n < 5]];
    el.innerHTML = `<div class="shead">${mosaicOf(g, "small")}</div>
      <div><h2>${esc(g.display)}</h2><div class="pills"><span>${g.chars} 位角色</span><span>${fmtN(g.n)} 張</span>${g.recent ? `<span class="acc">近 ${d.days} 天 +${g.recent}</span>` : ""}</div>
      ${g.key && g.display !== g.key ? `<small class="mute">${esc(g.key)}</small>` : ""}</div>
      ${g.key ? `<button class="btn primary" data-see="${esc(g.key)}">在圖庫裡看全部</button>` : ""}</div>`
      + (list ? shelfList(cs) : tiers.map(([t, sub, f]) => { const x = cs.filter(f); return x.length ? `<h3 class="sech">${t} <small>${sub} · ${x.length} 位</small></h3><div class="pgrid">${x.map(c => pcard(c)).join("")}</div>` : ""; }).join(""));
    return;
  }
  // 總覽
  if (list) { el.innerHTML = shelfList(d.chars); return; }
  const top = d.chars.slice(0, 12), rec = d.chars.filter(c => c.recent > 0).sort((a, b) => b.recent - a.recent);
  el.innerHTML = `<h3 class="sech">收藏排行 <small>依已確認張數</small></h3>
    ${rankList(top)}
    ${rec.length ? `<h3 class="sech">近期新增 <small>近 ${d.days} 天有新圖的 ${fmtN(rec.length)} 位</small><a class="lnk right" data-tabgo="recent">看全部 ›</a></h3>
      <div class="nchips">${rec.slice(0, 12).map(newChip).join("")}</div>` : ""}
    <h3 class="sech">作品 <small>${d.series.length} 部</small>${d.series.length > 12 ? `<a class="lnk right" data-tabgo="series-all">看全部 ›</a>` : ""}</h3>
    <div class="stiles">${sortedSeries(d.series).slice(0, 12).map(stile).join("")}</div>`;
}
function suggestModal() {
  const d = S.shelf;
  modal(`<h3>自建角色 → Danbooru 正式名稱</h3><div class="mbody">
    <p class="mute" style="margin-top:0">這些角色是你自己建立的，Danbooru 上有別名相符的角色。合併後會改用正式名稱（能抓到作品、中日文名），舊名稱會自動導向新名稱，搜尋也找得到。</p>
    ${d.suggest.map(x => `<div class="sugrow" data-n="${esc(x.name)}"><div><b>${esc(x.display)}</b> <small class="mute">${esc(x.name)} · ${x.n} 張</small></div>
      <select>${x.to.map(t => `<option>${esc(t)}</option>`).join("")}</select><button class="btn sm primary" data-do="merge">合併</button><button class="btn sm" data-do="skip">不是</button></div>`).join("")}
  </div><div class="mfoot"><button class="btn" data-close>完成</button></div>`, m => {
    m.addEventListener("click", async e => {
      const b = e.target.closest("[data-do]"); if (!b) return;
      const row = b.closest(".sugrow"), n = row.dataset.n;
      if (b.dataset.do === "merge") {
        const into = $("select", row).value;
        await api(`/api/tag/${encodeURIComponent(n)}/merge`, { into }); toast(`已合併：${n} → ${into}`);
      } else await api(`/api/tag/${encodeURIComponent(n)}`, { dismiss_suggest: true });
      row.remove();
      if (!$(".sugrow", m)) { closeModal(); render(); }
    });
  }, "wide");
}

/* ============ 卡片頁（角色 / 自訂標籤） ============ */
export async function renderCards(gen, category) {
  const title = { character: "角色", artist: "創作者" }[category] || "作品與標籤";
  $("#crumb").innerHTML = `<b>${title}</b>`;
  $("#head").innerHTML = `<div><h1>${title}</h1><div class="sub">${category === "artist" ? "來自 Danbooru / Pixiv 搜源，或你手動標記的創作者（畫師）。" : category === "character" ? "WD tagger 認得的角色 + 你自己加的角色。每個角色確認 3 張以上後，新圖會用特徵比對自動匹配。" : "作品，和你建立的自訂標籤（例如「喜歡」「桌布」）。"}</div>${S.view === "tags" ? `<div class="pagetabs">${libTabs()}</div>` : ""}</div>`;
  if (S.view === "tags") bindLibTabs();
  // v4.2 同一個骨架：下面一條查詢列（找名字｜≡ 排序、分類、工具）
  const scopeL = category === "artist" ? "創作者" : category === "character" ? "角色" : "標籤";
  $("#toolbar").className = "qtb";
  $("#toolbar").innerHTML = `<div class="qrow2"><div class="qbox" id="qbox">${svg("search", 16)}<span class="qtok scope"><i>找</i>${scopeL}</span><input id="card-q" autocomplete="off" placeholder="名字、別名…"></div>
    <span class="moreq"><button class="qic" id="card-more" title="排序、分類、工具" aria-haspopup="true">${svg("menu", 17)}</button><span class="popmenu hidden" id="card-pop">
      <div class="pmrow"><span>排序</span><select id="card-sort"><option value="count">張數</option><option value="unchecked">候選最多</option><option value="name">名稱</option>${category === "character" ? `<option value="series">依作品分組</option>` : ""}</select></div>
      ${category !== "artist" && category !== "character" ? `<div class="pmrow"><span>分類</span><span class="seg" id="card-cat">${[["custom", "自訂"], ["copyright", "作品"]].map(([c, l]) => `<button data-c="${c}" class="${c === category ? "on" : ""}">${l}</button>`).join("")}</span></div>` : ""}
      ${category === "artist" ? `<button class="ghost" id="goto-noartist">缺創作者的圖 <b id="noartist-n"></b></button>` : ""}
      ${category === "artist" || category === "character" ? `<button class="ghost" id="fetch-info" title="從 Danbooru 抓別名與 Pixiv／X 連結">從 Danbooru 抓${category === "artist" ? "創作者" : "角色"}資料</button>` : ""}
    </span></span></div>`;
  $("#card-more").onclick = e => { e.stopPropagation(); $("#card-pop").classList.toggle("hidden"); };
  document.addEventListener("mousedown", e => { if (!e.target.closest(".moreq")) $("#card-pop")?.classList.add("hidden"); });
  $$("#card-cat button").forEach(b => b.onclick = () => { store.set("tagsCat", b.dataset.c); render(); });
  const fi = $("#fetch-info"); if (fi) fi.onclick = async () => {
    try { const r = await api("/api/online/taginfo", { all_missing: true }); toast(r.queued ? `已排入 ${r.queued} 個角色/創作者（背景進行，約每秒 1 個）` : "都已有資料"); } catch (e) { toast(e.message); }
  };
  const gn = $("#goto-noartist"); if (gn) { gn.onclick = () => go("unrecognized", "artist"); api(`/api/sidebar?nsfw=${apiNsfw()}`).then(d => { $("#noartist-n").textContent = d.counts.miss_artist; }); }
  const cs = store.get("cardSort", "count"); $("#card-sort").value = cs; if (!$("#card-sort").value) $("#card-sort").value = "count";
  const load = async () => {
    const q = $("#card-q").value.trim(), sort = $("#card-sort").value; store.set("cardSort", sort);
    const list = await api(`/api/characters?${new URLSearchParams({ nsfw: apiNsfw(), q, sort, category })}${noAlbumQS()}`);
    if (gen !== S.gen) return;
    // v4.4 拼貼卡：大封面＋最近兩張（/api/covers 一批一批補），名字、別名、張數、候選
    const card = c => `<div class="card mcard" data-tag="${esc(c.name)}">
      <div class="mcov" data-cov="${esc(c.name)}">${c.cover ? `<img class="m0" loading="lazy" src="/t/${c.cover}" data-id="${c.cover}">` : `<span class="m0 empty">${esc((c.display || c.name).slice(0, 1).toUpperCase())}</span>`}<span class="m1"></span><span class="m2"></span></div>
      <div class="mbody"><div class="nm" title="${esc([c.name, ...(c.other_names || [])].join("、"))}">${c.pinned ? `<i class="pin" title="釘選">★</i>` : ""}${esc(c.display || c.name)}</div>
      ${category === "character" ? `<div class="cp">${c.copyright ? esc(c.copyright.replace(/_/g, " ")) + (c.copyright_guess ? ` <span class="guess" title="Danbooru 還沒查到，先用名字或同一張圖上的作品推測">推測</span>` : "") : (c.info_status ? `<span class="mute">未知作品</span>` : `<span class="mute" title="還沒查 Danbooru 資料卡，背景會補查">還沒查 Danbooru</span>`)}</div>` : ""}
      ${category === "artist" ? `<div class="cp">${c.other_names?.[0] && c.other_names[0] !== (c.display || c.name) ? `<span class="alias">${esc(c.other_names[0])}</span>` : ""}${linkIcons(c.links, true)}</div>` : ""}
      <div class="mfoot">${c.n ? `<span class="cnt"><b>${fmtN(c.n)}</b> 張</span>` : `<span class="mute">尚未確認</span>`}${c.unchecked ? `<span class="cand" title="待確認的候選${c.best_tier && c.best_tier <= 2 ? "（有搜源）" : ""}">候選 ${fmtN(c.unchecked)}</span>` : ""}</div></div></div>`;
    const fillCovers = async root => {
      const els = $$(".mcov[data-cov]:not(.filled)", root); if (!els.length) return;
      els.forEach(e => e.classList.add("filled"));
      let got; try { got = await api("/api/covers", { names: els.map(e => e.dataset.cov), n: 3, nsfw: apiNsfw(), noalbum: !!noAlbumQS() }); } catch { return; }
      els.forEach(e => {
        const ids = (got[e.dataset.cov] || []).filter(i => String(i) !== e.querySelector(".m0")?.dataset.id);
        const n = ids.length; e.classList.add(n >= 2 ? "three" : n === 1 ? "two" : "one");
        ["m1", "m2"].forEach((k, j) => { if (ids[j] != null) $("." + k, e).outerHTML = `<img class="${k}" loading="lazy" src="/t/${ids[j]}">`; });
      });
    };
    let html;
    if (sort === "series") {
      const groups = new Map();
      list.forEach(c => { const k = c.copyright || ""; if (!groups.has(k)) groups.set(k, []); groups.get(k).push(c); });
      html = [...groups].map(([k, arr]) => `<h3 class="grouph">${k ? `<a data-series="${esc(k)}">${esc(k.replace(/_/g, " "))}</a>` : "（未知作品）"} <span>${arr.length} 角色 · ${arr.reduce((a, c) => a + c.n, 0)} 張</span></h3><div class="cards mcards">${arr.map(card).join("")}</div>`).join("");
    } else {   // 一次先畫 150 張卡，捲到下面再畫下一批（737 位創作者全部一次畫會變慢）
      html = `<div class="cards mcards" id="cardsbox">${list.slice(0, CARD_CHUNK).map(card).join("")}</div>${list.length > CARD_CHUNK ? `<div id="cards-more" style="height:40px"></div>` : ""}`;
    }
    const emptyMsg = { artist: "還沒有創作者。到「未標創作者的圖」用「搜尋來源」自動找，或選取圖片後按 R 手動標記。", custom: "還沒有自訂標籤。選取圖片後按 A 或 G 就能建立。", copyright: "還沒有作品標籤。用「搜尋來源」找到的作品會出現在這裡。", artist: "還沒有畫師標籤。用「搜尋來源」找到的畫師會出現在這裡。", character: "還沒有角色標籤" };
    $("#content").innerHTML = list.length ? html : `<div class="empty">${emptyMsg[category]}</div>`;
    fillCovers($("#content"));
    $$("[data-series]", $("#content")).forEach(a => a.onclick = e => { e.stopPropagation(); go("series", a.dataset.series); });
    const more = $("#cards-more");
    if (more) {
      let shown = CARD_CHUNK;
      const io = new IntersectionObserver(es => {
        if (gen !== S.gen || !more.isConnected) { io.disconnect(); return; }
        if (!es.some(x => x.isIntersecting)) return;
        $("#cardsbox").insertAdjacentHTML("beforeend", list.slice(shown, shown + CARD_CHUNK).map(card).join(""));
        fillCovers($("#cardsbox"));
        shown += CARD_CHUNK;
        if (shown >= list.length) { io.disconnect(); more.remove(); }
      }, { root: $("#content"), rootMargin: "900px" });
      io.observe(more);
    }
    $("#foot-l").textContent = `${list.length} 個${title}`;
  };
  $("#card-q").oninput = debounce(load, 200);
  $("#card-sort").onchange = load;
  $("#content").onclick = e => { const c = e.target.closest(".card"); if (c) go("tag", c.dataset.tag); };
  await load();
}

const CARD_CHUNK = 150;

/* ============ 作品頁：作品資訊＋角色列表＋這部作品的圖庫 ============ */
export async function seriesHead(gen) {
  const key = S.seriesKey;
  const ck = `${key}|${S.nsfw}${noAlbumQS()}`;
  if (!S.seriesData || S.seriesData._ck !== ck) {
    try { S.seriesData = await api(`/api/series/${encodeURIComponent(key)}?nsfw=${S.nsfw}${noAlbumQS()}`); S.seriesData._ck = ck; }
    catch (e) { toast(e.message); S.seriesData = { key, display: key, chars: [], covers: [], subs: [], n: 0, _ck: ck }; }
    if (gen !== S.gen) return;
  }
  if (S.seriesData.parent && S.seriesData.parent !== key) {   // 子作品（例如 Limbus Company）→ 到母系列的作品頁
    location.replace(`#/series/${encodeURIComponent(S.seriesData.parent)}`);
    return;
  }
  const d = S.seriesData, head = $("#head");
  $("#crumb").innerHTML = `<a data-back>角色</a> / <b>${esc(d.display)}</b>`;
  const bg = d.covers[0];
  const chars = d.chars;
  const sel = S.seriesChar;
  head.innerHTML = `<div class="shero">
      ${bg ? `<img class="shero-bg" src="/t/${bg}?size=l" alt="">` : ""}<div class="shero-shade"></div>
      <div class="shero-in">
        ${mosaicOf(d, "hero")}</div>
        <div class="shero-txt">
          <div class="eyebrow">作品</div>
          <h2>${esc(d.display)}</h2>
          ${d.display !== d.key ? `<div class="shero-raw">${esc(d.key)}</div>` : ""}
          ${d.summary ? `<div class="shero-sum" title="${esc(d.summary)}">${esc(d.summary)}</div>` : ""}
          <div class="shero-pills"><span><b>${fmtN(chars.length)}</b> 位角色</span><span><b id="sr-n">${fmtN(d.n)}</b> 張</span>
            ${d.recent ? `<span class="acc">近 ${d.days} 天 +${fmtN(d.recent)}</span>` : ""}
            ${d.subs.length ? `<span title="${esc(d.subs.join("、"))}">含 ${d.subs.map(k => esc(d.sub_display[k] || k)).slice(0, 3).join("、")}${d.subs.length > 3 ? "…" : ""}</span>` : ""}</div>
        </div>
        <div class="shero-act">
          <button class="btn ghostw" id="sr-pin">${d.pinned ? "★ 已置頂" : "☆ 置頂"}</button>
          ${pageMenu("sr-menu", [d.url ? `<a class="ghost" href="${esc(safeUrl(d.url))}" target="_blank" rel="noopener">在 Danbooru 打開 ↗</a>` : "",
            `<button class="ghost" id="sr-rename" title="自己取中文名">改名</button>`,
            d.key ? `<button class="ghost" id="sr-cover" title="選一張當作品封面">${d.custom_cover ? "換封面（目前是自選的）" : "選封面"}</button>` : "",
            d.key ? `<button class="ghost danger" id="sr-del" title="從所有圖上拿掉這個作品標籤（可撤銷）">刪除作品…</button>` : ""])}
        </div>
      </div></div>
    ${chars.length || (d.pending || []).length ? `<div class="cstrip" id="cstrip">
      <button class="cchip ${sel ? "" : "on"}" data-c=""><span class="cav all">${svg("all", 18)}</span><span class="cn">全部</span><i id="sr-n2">${fmtN(d.n)}</i></button>
      ${chars.map((c, i) => `<button class="cchip ${c.name === sel ? "on" : ""}${i >= 10 && c.name !== sel ? " more" : ""}" data-c="${esc(c.name)}" title="${esc(c.name)}（雙擊打開角色頁）">
        <span class="cav">${c.cover ? `<img loading="lazy" src="/t/${c.cover}" alt="">` : ""}</span><span class="cn">${esc(c.display)}</span><i>${fmtN(c.n)}</i></button>`).join("")}
      ${chars.length > 10 ? `<button class="cchip cmore" data-cmore title="列出這部作品的全部角色">還有 ${fmtN(chars.length - 10)} 位 ▾</button>` : ""}
      ${(d.pending || []).map(c => `<button class="cchip pend more" data-p="${esc(c.name)}" title="${esc(c.name)}：還沒有確認的圖，${c.cand} 張等你確認（點一下去看）">
        <span class="cav">${c.cover ? `<img loading="lazy" src="/t/${c.cover}" alt="">` : ""}</span><span class="cn">${esc(c.display)}</span><i>待確認 ${fmtN(c.cand)}</i></button>`).join("")}
    </div>` : ""}
    ${d.miss && (d.miss.nochar || d.miss.noartist || S.seriesMiss) ? `<div class="smiss" id="smiss"><span class="mute">還沒整理完：</span>
      <button class="chip2 ${S.seriesMiss === "nochar" ? "on" : ""}" data-sm="nochar" title="這部作品的圖裡，還沒有確認角色的">沒標角色 <i>${fmtN(d.miss.nochar)}</i></button>
      <button class="chip2 ${S.seriesMiss === "noartist" ? "on" : ""}" data-sm="noartist" title="這部作品的圖裡，還沒有創作者的（不含官方截圖、不用找作者的）">沒標作者 <i>${fmtN(d.miss.noartist)}</i></button></div>` : ""}
    <div class="cselbar" id="cselbar"></div>`;
  $("[data-back]", $("#crumb")).onclick = () => go("characters");
  const sdel = $("#sr-del"); if (sdel) sdel.onclick = () => tagDelete({ name: d.key, display: d.display, category: "copyright" }, {
    after: () => { S.seriesData = null; go("characters"); },
    note: chars.length ? `這部作品底下的 ${fmtN(chars.length)} 位角色不會刪；角色從 Danbooru 查到的作品資料還在，角色頁可能還會把他們歸在這部作品下。${d.subs.length ? `子作品（${d.subs.map(k => esc(d.sub_display[k] || k)).join("、")}）也不會刪。` : ""}` : "" });
  const strip = $("#cstrip");
  if (strip) {
    const on = strip.querySelector(".cchip.on"); if (on && on.dataset.c) on.scrollIntoView({ inline: "center", block: "nearest" });
    // 單擊：馬上篩選（只重載下面的圖，頁首不重畫）；雙擊：角色頁
    strip.onclick = e => {
      const b = e.target.closest(".cchip"); if (!b) return;
      if (b.dataset.cmore != null) { strip.classList.toggle("open"); b.textContent = strip.classList.contains("open") ? "收起 ▴" : `還有 ${fmtN(chars.length - 10)} 位 ▾`; strip.scrollTop = 0; strip.scrollLeft = 0; return; }
      if (b.dataset.p) return go("tag", b.dataset.p);   // 只有待確認的角色 → 角色頁（在那裡確認）
      S.seriesChar = b.dataset.c === S.seriesChar ? "" : b.dataset.c;
      if (S.seriesChar) { S.seriesMiss = ""; $$("#smiss [data-sm]").forEach(x => x.classList.remove("on")); }
      seriesFilterUI(); reloadGrid();
    };
    strip.ondblclick = e => { const b = e.target.closest(".cchip"); if (b && b.dataset.c) go("tag", b.dataset.c); };
    // 滾輪直接左右捲動角色列
    strip.addEventListener("wheel", e => { if (strip.classList.contains("open")) return; if (Math.abs(e.deltaY) > Math.abs(e.deltaX)) { strip.scrollLeft += e.deltaY; e.preventDefault(); } }, { passive: false });
  }
  const sm = $("#smiss");
  if (sm) sm.onclick = e => {
    const b = e.target.closest("[data-sm]"); if (!b) return;
    S.seriesMiss = S.seriesMiss === b.dataset.sm ? "" : b.dataset.sm;
    if (S.seriesMiss) S.seriesChar = "";
    $$("[data-sm]", sm).forEach(x => x.classList.toggle("on", x.dataset.sm === S.seriesMiss));
    seriesFilterUI(); reloadGrid();
  };
  $("#cselbar").onclick = e => {
    if (e.target.closest("[data-open-char]")) go("tag", S.seriesChar);
    else if (e.target.closest("[data-c-clear]")) { S.seriesChar = ""; seriesFilterUI(); reloadGrid(); }
  };
  seriesFilterUI();
  $("#sr-pin").onclick = async () => {
    await api(`/api/tag/${encodeURIComponent(d.key)}`, { pinned: !d.pinned });
    d.pinned = !d.pinned; $("#sr-pin").textContent = d.pinned ? "★ 已置頂" : "☆ 置頂"; loadSidebar();
  };
  const sc = $("#sr-cover"); if (sc) sc.onclick = () => coverPicker(d.key);
  $("#sr-rename").onclick = () => {
    const m = modal(`<h3>作品名稱</h3><div class="mbody form"><div class="field"><label>顯示名稱</label><input type="text" id="sr-name" value="${esc(d.display)}"></div>
      <div class="note">留空 = 恢復自動（內建譯名表／維基百科／Danbooru 別名）。</div></div>
      <div class="mfoot"><button class="btn" data-close>取消</button><button class="btn primary" id="sr-ok">儲存</button></div>`);
    $("#sr-ok", m).onclick = async () => {
      await api(`/api/tag/${encodeURIComponent(d.key)}`, { display_name: $("#sr-name", m).value.trim() });
      closeModal(); S.seriesData = null; render(); toast("已更新作品名稱");
    };
  };
}

function seriesFilterUI() {
  const sel = S.seriesChar, c = (S.seriesData?.chars || []).find(x => x.name === sel);
  $$("#cstrip .cchip").forEach(b => b.classList.toggle("on", b.dataset.c === (sel || "")));
  const bar = $("#cselbar"); if (!bar) return;
  bar.innerHTML = c ? `只看 <b>${esc(c.display)}</b>${c.skins?.length ? `（含 ${c.skins.length} 個造型）` : ""} <a data-open-char>角色頁 ›</a> <a data-c-clear>顯示全部</a>` : "";
  bar.classList.toggle("hidden", !c);
}


/* ============ 中文名整理：沒有中文名、或有好幾個譯名的角色，點一下選你習慣的 ============ */
export async function namesModal(kind, cat) {
  cat = cat || store.get("nmCat", "character"); store.set("nmCat", cat);
  let d;
  try { d = await api(`/api/names/review?kind=${kind || "auto"}&cat=${cat}`); } catch (e) { return toast(e.message); }
  if (!kind) {   // 沒指定分頁：先開有東西要看的那一頁
    kind = ["fixed", "auto", "pick", "nozh", "multi", "done"].find(k => d.counts[k]) || "nozh";
    if (kind !== "auto") return namesModal(kind, cat);
  }
  const T = [["fixed", "校對改過的"], ["auto", "Bangumi 自動換的"], ["pick", "同名好幾個，你挑"], ["nozh", "沒有中文名"], ["multi", "有好幾個譯名"], ["done", "你取過名的"]];
  const bg = d.bangumi || {};
  const desc = {
    fixed: "2026-10 把有確認圖的角色、作品的譯名全部校對過一次（台灣譯名優先），這裡是改掉的：原本 → 現在。不喜歡按「改回」或點別的，不用每個都看。",
    auto: "Danbooru 沒有中文名、或中文別名是暱稱的，到 Bangumi（番組計畫）找到官方中文名，已經換上（簡體自動轉繁體）。不喜歡就按「改回」或點別的；看過的按「好」。沒看過的排前面。",
    pick: "Bangumi 上有好幾個同名的角色／作品，對不上你圖庫裡的作品，不敢自動換。點對的那個就好。",
    nozh: "Danbooru 和 Bangumi 都沒有中文名（顯示成日文或英文）。有候選就點一下，沒有就直接打你習慣的名字，Enter 存。",
    multi: "Danbooru 有好幾個中文別名，Bangumi 沒找到，程式自動挑了第一個。點你習慣的那個就換掉（綠色＝目前顯示的）。",
    done: "你自己取過名的。按「恢復自動」回到程式挑的名字。" }[kind];
  const m = modal(`<h3>中文名整理</h3><div class="mbody">
    <div class="chiprow" style="margin-bottom:6px"><span class="seg" id="nm-cat">${[["character", "角色"], ["copyright", "作品"]].map(([k, l]) => `<button data-cat="${k}" class="${k === cat ? "on" : ""}">${l}</button>`).join("")}</span>
      ${T.map(([k, l]) => `<button class="chip2 ${k === kind ? "on" : ""}" data-k="${k}">${l} <i>${fmtN(d.counts[k] || 0)}</i></button>`).join("")}</div>
    <p class="mute" style="margin:0 0 8px">${desc}依張數排，多的先。</p>
    <div class="nmlist">${d.items.map(x => {
      const b = x.bgm || {};
      const autoRow = kind === "auto" ? `<div class="nmbgm"><span class="mute">原本：</span>${esc(b.prev || "")}
        <a class="lnk" href="https://bgm.tv/${esc((b.ref || "").replace("bgm:", ""))}" target="_blank" rel="noopener" title="Bangumi 上的資料">Bangumi ↗</a>
        ${b.seen ? `<span class="okc">已看過</span>` : `<button class="btn sm" data-seen>好</button>`}<button class="btn sm ghost" data-revert="${esc(b.prev || "")}">改回「${esc(b.prev || "")}」</button></div>` : "";
      const fixRow = kind === "fixed" && x.before ? `<div class="nmbgm"><span class="mute">原本：</span>${esc(x.before)}
        <button class="btn sm ghost" data-revert="${esc(x.before)}">改回「${esc(x.before)}」</button></div>` : "";
      const pickRow = kind === "pick" && b.options?.length ? `<div class="chiprow">${b.options.map(o => `<button class="chip2" data-pick="${esc(o.zh)}" title="${esc((o.subjects || []).join("、"))}">${esc(o.zh)}${o.subjects?.length ? ` <span class="mute">${esc(o.subjects[0] || "")}</span>` : ""}</button>`).join("")}</div>` : "";
      return `<div class="nmrow" data-n="${esc(x.name)}">
      ${x.cover ? `<img src="/t/${x.cover}" loading="lazy">` : `<span class="nmimg"></span>`}
      <div class="nmmain"><div><b class="nmcur">${esc(x.display)}</b> <span class="mute">${esc(x.name)} · ${fmtN(x.n)} 張</span></div>
        ${autoRow}${fixRow}${pickRow}
        ${kind !== "pick" ? `<div class="chiprow">${x.candidates.map(c => `<button class="chip2 ${c === x.display ? "on" : ""}" data-pick="${esc(c)}">${esc(c)}</button>`).join("")}
          ${x.others.length ? `<span class="mute" title="${esc(x.others.join("、"))}">其他別名：${esc(x.others.slice(0, 3).join("、"))}</span>` : ""}</div>` : ""}
        <div class="nmedit"><input placeholder="自己取名，Enter 存" value="${esc(x.manual || "")}">${x.manual ? `<button class="btn sm" data-reset>恢復自動（${esc(x.auto)}）</button>` : ""}</div>
      </div></div>`; }).join("") || `<div class="empty">這裡沒有了</div>`}</div></div>
    <div class="mute" style="margin:8px 0 0">校對：${d.unreviewed ? `新的角色／作品還有 <b>${fmtN(d.unreviewed)}</b> 個沒校對` : "全部校對過了"}
      · <a class="lnk" id="nm-copy">複製清單給 Claude 校對</a> · <a class="lnk" id="nm-paste">貼上校對結果</a></div>
    <div class="mfoot" style="justify-content:space-between"><span class="mute nmbg">Bangumi：${bg.unchecked ? `還有 ${fmtN(bg.unchecked)} 個沒查（背景慢慢查）` : "都查過了"}
      · <a class="lnk" id="nm-bgm">${bg.unchecked ? "現在查" : "全部重查"}</a> · <label><input type="checkbox" id="nm-auto" ${bg.auto_on ? "checked" : ""}> 新角色自動查</label>
      ${kind === "auto" && d.items.some(x => !x.bgm?.seen) ? ` · <a class="lnk" id="nm-seenall">這頁都好</a>` : ""}</span>
      <button class="btn" data-close>關閉</button></div>`, null, "wide");
  $$(".chiprow [data-k]", m).forEach(b => b.onclick = () => { closeModal(); namesModal(b.dataset.k, cat); });
  $$("#nm-cat [data-cat]", m).forEach(b => b.onclick = () => { closeModal(); namesModal(null, b.dataset.cat); });
  const save = async (row, value) => {
    const name = row.dataset.n;
    try { await api(`/api/tag/${encodeURIComponent(name)}`, { display_name: value }); } catch (e) { return toast(e.message); }
    $(".nmcur", row).textContent = value || "（自動）";
    $$("[data-pick]", row).forEach(b => b.classList.toggle("on", b.dataset.pick === value));
    row.classList.add("nmdone");
    toast(value ? `已改成「${value}」` : "已恢復自動");
  };
  $$(".nmrow", m).forEach(row => {
    $$("[data-pick]", row).forEach(b => b.onclick = () => save(row, b.dataset.pick));
    const inp = $(".nmedit input", row);
    inp.addEventListener("keydown", e => { e.stopPropagation(); if (e.key === "Enter" && inp.value.trim()) save(row, inp.value.trim()); });
    const rs = $("[data-reset]", row); if (rs) rs.onclick = () => save(row, "");
    const rv = $("[data-revert]", row); if (rv) rv.onclick = () => save(row, rv.dataset.revert);
    const sn = $("[data-seen]", row); if (sn) sn.onclick = async () => { await api("/api/names/seen", { names: [row.dataset.n] }); sn.outerHTML = `<span class="okc">已看過</span>`; };
  });
  const sa = $("#nm-seenall", m); if (sa) sa.onclick = async () => {
    await api("/api/names/seen", { names: d.items.map(x => x.name) }); toast(`${d.items.length} 個都好`); closeModal(); namesModal("auto", cat);
  };
  $("#nm-bgm", m).onclick = async () => {
    try { const r = await api("/api/names/bangumi", { force: !bg.unchecked }); toast(`已排入背景：到 Bangumi 查 ${fmtN(r.queued)} 個（每個約 1 秒，查完再打開這裡看）`); }
    catch (e) { toast(e.message); }
  };
  $("#nm-copy", m).onclick = async () => {
    let r; try { r = await api("/api/names/unreviewed?limit=150"); } catch (e) { return toast(e.message); }
    if (!r.count) return toast("沒有還沒校對的名字");
    try { await navigator.clipboard.writeText(r.text); toast(`已複製 ${fmtN(r.shown)} 個（共 ${fmtN(r.count)} 個沒校對）：貼到 Claude，把回覆整段貼回「貼上校對結果」`); }
    catch (e) { closeModal(); modal(`<h3>複製給 Claude</h3><div class="mbody"><textarea style="width:100%;height:50vh">${esc(r.text)}</textarea></div><div class="mfoot"><button class="btn" data-close>關閉</button></div>`, null, "wide"); }
  };
  $("#nm-paste", m).onclick = () => {
    closeModal();
    const p = modal(`<h3>貼上校對結果</h3><div class="mbody"><p class="mute" style="margin:0 0 8px">把 Claude 的回覆整段貼進來（每行「tag 名字」，名字是 - 的＝沒有中文名）。存在 data 資料夾，程式更新不會蓋掉；之後想改還是可以在這裡或標籤頁改名。</p>
      <textarea id="nm-ptext" style="width:100%;height:45vh" placeholder="sensei_(blue_archive)	老師"></textarea></div>
      <div class="mfoot"><button class="btn ghost" data-close>取消</button><button class="btn" id="nm-psave">存</button></div>`, null, "wide");
    $("#nm-ptext", p).addEventListener("keydown", e => e.stopPropagation());
    $("#nm-psave", p).onclick = async () => {
      let r; try { r = await api("/api/names/curated", { text: $("#nm-ptext", p).value }); } catch (e) { return toast(e.message); }
      toast(`存了 ${fmtN(r.saved)} 個譯名、${fmtN(r.none)} 個標成沒有中文名${r.unknown.length ? `；${r.unknown.length} 行看不懂：${r.unknown.slice(0, 3).join("、")}` : ""}`);
      closeModal(); namesModal("fixed", cat);
    };
  };
  $("#nm-auto", m).onchange = async e => { await api("/api/settings", { auto_zhname: e.target.checked }); toast(e.target.checked ? "新角色會自動到 Bangumi 找中文名" : "不再自動查"); };
}
// 作品卡可以用 Tab 移過去、Enter 打開
document.addEventListener("keydown", e => { const st = e.target.closest && e.target.closest(".stack[data-series-tab]"); if (st && e.key === "Enter") { e.preventDefault(); e.stopPropagation(); st.click(); } }, true);
