// ES module（2026-10）：用到別的檔案的東西都要在這裡 import；瀏覽器會在載入時檢查名字對不對。
import { $, $$, api, apiNsfw, catName, closeModal, esc, fmtN, follow, IDENTITY, MISS_LABEL, modal, MTYPE_UNSURE, MTYPES, noAlbumQS, OFFICIAL, PAGE, pageMenu, S, safeUrl, store, svg, toast } from "./core.js";
import { go, loadNsfwIds, loadSidebar, quickFacetHTML } from "./shell.js";
import { drawWindow, gridMode, gridParams, relayout } from "./layout.js";
import { bindEmpty, bindQbar, emptyHTML, qboxHTML, updateFooter } from "./filters.js";
import { clearSel, selectAll } from "./selection.js";
import { undo, updateActionBar } from "./actions.js";
import { artistBucketAction, bindLibTabs, bucketBar, libTabs, loadBuckets, pileAction, pileBar, quickHint } from "./unrec.js";
import { loadReview, renderReview } from "./review.js";
import { renderCards, renderShelf, seriesHead, tagCoverPicker } from "./shelf.js";
import { albumHead, renderAlbums } from "./albums.js";
import { openPalette } from "./palette.js";
import { findModal, renderDuplicates, renderHome, renderSystem } from "./pages.js";
import { renderSettings } from "./settings.js";
/* ============ 主畫面 ============ */
export async function render() {
  const gen = ++S.gen;
  $("#pagetitle").innerHTML = ""; $("#topslot").innerHTML = "";
  S.items = []; S.total = 0; S.done = false; S.tagInfo = null; S.rOffset = 0; S.layout = null; S.loadAll = false; S.bindFacets = null; S.facetRows = "";
  // 這次瀏覽的隨機種子、時間點（見 gridParams）；種子存在這一筆歷史紀錄裡，按「上一頁」回來是同一個順序（才捲得回原位）
  { const hs = history.state || {}; S.seed = hs.seed || Math.floor(Math.random() * 2 ** 31) + 1; S.until = Date.now() / 1000 + 5;
    if (hs.seed !== S.seed) try { history.replaceState({ ...hs, seed: S.seed }, ""); } catch { } }
  updateActionBar();
  const head = $("#head"), tb = $("#toolbar"), content = $("#content");
  head.innerHTML = ""; tb.innerHTML = ""; tb.className = ""; content.innerHTML = ""; content.scrollTop = 0;
  content.onclick = null;   // 上一頁（首頁、書架…）掛的點擊處理不能留著，不然會在別的頁面誤觸發
  for (const el of [content, head]) { el.classList.remove("enter"); void el.offsetWidth; el.classList.add("enter"); }   // 換頁淡入
  loadNsfwIds();
  drawQchip();
  const crumbs = { official: "官方截圖", home: "收件匣", system: "系統", excluded: "以前排除的", review: "待確認", all: "圖庫", characters: "角色", unrecognized: "未識別", videos: "影片", duplicates: "重複", tags: "作品與標籤", artists: "創作者", nsfw: "NSFW", errors: "讀取失敗" };
  if (S.view === "characters") return renderShelf(gen);
  if (S.view === "artists") return renderCards(gen, "artist");
  if (S.view === "tags") { const tc = store.get("tagsCat", "copyright"); return renderCards(gen, tc === "artist" ? "custom" : tc); }   // 預設看作品（大部分人沒有自訂標籤）
  if (S.view === "duplicates") return renderDuplicates(gen);
  if (S.view === "albums") return renderAlbums(gen);
  if (S.view === "review") return renderReview(gen);
  if (S.view === "home") return renderHome(gen);
  if (S.view === "system") return renderSystem(gen);
  if (S.view === "settings") return renderSettings(gen);

  if (S.view === "tag") {
    $("#crumb").innerHTML = `標籤 / <b>${esc(S.tag)}</b>`;
    try { S.tagInfo = await api(`/api/tag/${encodeURIComponent(S.tag)}?nsfw=${apiNsfw()}${noAlbumQS()}`); } catch { S.tagInfo = null; }
    if (gen !== S.gen) return;
    if (S.tagInfo?.category === "copyright") {   // 作品標籤 → 作品頁（整部作品的圖庫＋角色列表）
      location.replace(`#/series/${encodeURIComponent(S.tag)}`);
      return;
    }
    const t = S.tagInfo;
    const idt = t && IDENTITY.includes(t.category);
    head.innerHTML = t ? `${t.cover_id && IDENTITY.includes(t.category) ? `<img class="thero-bg" src="/t/${t.cover_id}?size=l" alt="">` : ""}<div class="headmain"><h1>${esc(t.display || S.tag)}</h1>${t.display && t.display !== S.tag ? `<div class="rawname">${esc(S.tag)}</div>` : ""}${infoLine(t)}
      ${t.base ? `<div class="skins">造型，本體：<a data-goto="${esc(t.base)}">${esc(t.base)}</a></div>` : ""}
      ${t.skins?.length ? `<div class="skins">造型：${t.skins.map(k => `<a data-goto="${esc(k.name)}">${esc(k.name)} <i>${k.n}</i></a>`).join("")}</div>` : ""}
      ${t.implies?.implies?.length ? `<div class="skins" title="確認這個標籤的圖會自動帶上（標籤蘊含；⋯ › 改名、合併、資料卡 可以改）">也代表：${t.implies.implies.map(k => `<a data-goto="${esc(k.name)}">${esc(k.name)}</a>`).join("")}</div>` : ""}<div class="sub"><span class="cat ${esc(t.category)}">${catName(t.category)}</span> ·
      ${idt ? `已確認 <b>${t.confirmed || 0}</b> 張${t.unchecked ? ` · <span class="warnc">候選 ${t.unchecked}</span>（${[["Danbooru", t.cand_source], ["Pixiv", t.cand_pixiv], ["AI", t.cand_ai]].filter(x => x[1]).map(x => x.join(" ")).join(" · ")}）` : ""}`
        : `${t.n || 0} 張 · ${t.unchecked ? `<span class="warnc">候選 ${t.unchecked}</span>` : `<span class="okc">全部已確認</span>`}`}</div>
      ${t.auto_block ? `<div class="banner">⏸ 已停止自動標註：這個標籤只會出現在你手動標記的圖上。<a id="unblock">恢復自動標註</a></div>`
        : (t.rejected >= 5 && t.rejected > (t.confirmed || 0)) ? `<div class="banner warn">你已經否決這個標籤 ${t.rejected} 次，比確認的還多。<a id="block">停止自動標註</a>（只保留手動標記）</div>` : ""}
      </div>
      <div class="actions">${!idt || S.src === "candidates" ? `<button class="ghost" id="confirm-all-btn" ${t.unchecked ? "" : "disabled"}>${idt ? "候選全部確認" : "全部確認"}</button>` : ""}
      <button class="ghost" id="pin-btn">${t.pinned ? "★ 已置頂" : "☆ 置頂"}</button>
      ${pageMenu("tag-menu", [idt ? `<button class="ghost" id="cover-btn" title="選一張當封面">${t.cover ? "換封面（目前是自選的）" : "選封面"}</button>` : "", `<button class="ghost" id="tagmenu-btn">改名、合併、資料卡…</button>`])}</div>${infoCard(t)}`
      : `<div><h1>${esc(S.tag)}</h1><div class="sub">沒有這個標籤</div></div>`;
    if (t) {
      if (t.info_pending) watchInfo();   // 資料卡還在背景抓 → 抓好自動刷新
      $("#pin-btn").onclick = async () => { await api(`/api/tag/${encodeURIComponent(S.tag)}`, { pinned: !t.pinned }); loadSidebar(); render(); };
      const cab = $("#confirm-all-btn"); if (cab) cab.onclick = async () => {
        // 先數：角色候選裡梗圖、截圖、照片、略過／排除的不會被確認（你的習慣是有角色的梗圖留著不確認）
        let d; try { d = await api("/api/tags/confirm-all", { tag: S.tag, nsfw: apiNsfw(), dry_run: true }); } catch (e) { return toast(e.message); }
        if (!d.n) return toast(d.left ? `剩下的 ${d.left} 張都像梗圖／截圖、或是標了不用整理的，沒有可以一次確認的` : "沒有候選了");
        modal(`<h3>${idt ? `確認 ${fmtN(d.n)} 張是 ${esc(t.display || S.tag)}？` : `${fmtN(d.n)} 張全部確認？`}</h3><div class="mbody">
          ${idt ? `<p style="margin-top:0">只確認<b>像插畫的</b> ${fmtN(d.n)} 張。</p>${d.left ? `<p class="mute">另外 ${fmtN(d.left)} 張可能是梗圖、截圖、照片，或是標了不用整理的：不會動，還是留在候選裡給你自己看。</p>` : ""}` : ""}
          <p class="mute">可以用「撤銷」一次還原。</p></div>
          <div class="mfoot"><button class="btn" data-close>取消</button><button class="btn primary" id="ca-go">確認 ${fmtN(d.n)} 張</button></div>`, m => {
          $("#ca-go", m).onclick = async () => {
            try { const r = await api("/api/tags/confirm-all", { tag: S.tag, nsfw: apiNsfw() }); closeModal();
              toast(`已確認 ${fmtN(r.n)} 張${r.left ? `，留下 ${fmtN(r.left)} 張像梗圖／截圖的` : ""}`, { label: "撤銷", fn: undo }); loadSidebar(); render(); }
            catch (e) { toast(e.message); }
          };
        });
      };
      $("#tagmenu-btn").onclick = () => tagMenu(t);
      const cvb = $("#cover-btn"); if (cvb) cvb.onclick = () => tagCoverPicker(t);
      $$("[data-goto]", head).forEach(a => a.onclick = () => go("tag", a.dataset.goto));
      const setBlock = async v => {
        await api(`/api/tag/${encodeURIComponent(S.tag)}`, { auto_block: v });
        toast(v ? "已停止自動標註，並移除還沒確認的自動標註" : "已恢復自動標註（下次標記/比對時生效）", v ? { label: "撤銷", fn: undo } : null); render();
      };
      const ub = $("#unblock", head); if (ub) ub.onclick = () => setBlock(false);
      const bb = $("#block", head); if (bb) bb.onclick = () => setBlock(true);
      $$("[data-series]", head).forEach(a => a.onclick = () => go("series", a.dataset.series));
      const more = $("#info-more", head); if (more) more.onclick = () => $(".infocard", head).classList.toggle("open");
      quickHint(gen, head);
    }
    loadSidebar();
  } else if (S.view === "album") {
    await albumHead(gen);
    if (gen !== S.gen) return;
  } else if (S.view === "series") {
    await seriesHead(gen);
    if (gen !== S.gen) return;
  } else {
    $("#crumb").innerHTML = `<b>${crumbs[S.view] || ""}</b>`;
    const MISS_SUB = {
      all: "沒有任何身分資訊的圖：沒有角色、沒有創作者、也沒有作品（包括原創）。標了其中任何一項就會離開這裡。",
      character: "還沒有角色的圖。非同人作品可以按 O 標為「原創」，就不會再出現在這裡。",
      artist: "還沒有創作者的圖。可以「搜尋來源」自動找出畫師，或選取後按 R 手動標記。自己畫的、AI 生成的、找不到也沒關係的，按「不用找作者」就不再列在這裡。下面「原創作品」「AI 生成」可以單獨挑出來整批處理。",
      people: "WD 看到 2 人以上、但標的角色比人數少的圖（例如只標了一個人）。選取後按 A 加上另一個角色；其他人是路人就按「不用再找角色」。",
    };
    const SKIP_TIP = "認不出來、也不想再管的圖，選取後按 L「不用整理」：圖留在圖庫，只是不再算進這裡的待辦。";
    const SHORT = { all: "沒有角色、創作者、作品的圖", character: "還沒有角色的圖", artist: "還沒有創作者的圖", people: "多人圖只標了一部分角色" };
    const sub = { unrecognized: `${SHORT[S.miss] || SHORT.all}，選取後標記就會離開這裡。<span class="info" tabindex="0" title="${esc(((MISS_SUB[S.miss] || MISS_SUB.all) + "\n" + SKIP_TIP).replace(/<[^>]+>/g, ""))}">ⓘ</span>`, errors: "無法讀取或解碼的檔案，可在設定中重新標記。", nsfw: "所有被判定或標記為 NSFW 的項目。",
      official: "標成動畫截圖、漫畫截圖、小說插圖、電影截圖的圖（選取後按 K 標記）。這些都算已經有作者，不會出現在「缺創作者」和「完全未識別」。" }[S.view];
    const title = S.view === "all" && S.q ? "搜尋結果" : crumbs[S.view] || "";
    const offSeg = S.view === "official" ? `<div class="tabs" id="off-tabs">${[["", "全部", "official"], ["anime", "動畫截圖", "official_anime"], ["manga", "漫畫截圖", "official_manga"], ["novel", "小說插圖", "official_novel"], ["movie", "電影截圖", "official_movie"]]
      .map(([k, l, c]) => `<button data-off="${k}" class="${k === (S.mtype || "") ? "on" : ""}">${l}<b data-count="${c}">${S.sbCounts?.[c] != null ? fmtN(S.sbCounts[c]) : ""}</b></button>`).join("")}</div>` : "";
    // 分頁、分流、分堆、依作品：收進查詢列的「分面」面板（點查詢列才展開），畫面上只留條件 token
    S.facetRows = `
      ${S.view === "unrecognized" ? bucketBar() : ""}`;
    const missTabs = S.view === "unrecognized" ? `<div class="tabs" id="miss-tabs">${[["all", "完全未識別", "unrecognized"], ["character", "缺角色", "miss_character"], ["people", "多人圖缺角色", "miss_people"], ["artist", "缺創作者", "miss_artist"]].map(([k, l, c]) => `<button data-m="${k}" class="${k === S.miss ? "on" : ""}">${l}<b data-count="${c}">${S.sbCounts?.[c] != null ? fmtN(S.sbCounts[c]) : ""}</b></button>`).join("")}</div>` : "";
    head.innerHTML = `<div><h1>${esc(title)}</h1>${sub ? `<div class="sub">${sub}</div>` : ""}${libTabs() ? `<div class="pagetabs">${libTabs()}</div>` : ""}${offSeg ? `<div class="pagetabs">${offSeg}</div>` : ""}${missTabs ? `<div class="pagetabs">${missTabs}</div>` : ""}${S.view === "unrecognized" ? pileBar() : ""}</div>`
;
    S.bindFacets = () => {
      bindLibTabs();
      $$("#off-tabs [data-off]").forEach(b => b.onclick = () => { S.mtype = b.dataset.off; render(); });
      if (S.view === "unrecognized") {
        $("#find-src").onclick = () => findModal(S.miss);
        $$("#miss-tabs button").forEach(b => b.onclick = () => go("unrecognized", b.dataset.m));
        $$("#ubk button[data-b], #piles button[data-b]").forEach(b => b.onclick = () => { S.bucket = S.bucket === b.dataset.b && (b.dataset.b.startsWith("p_") || ["orig", "aigen"].includes(b.dataset.b)) ? "" : b.dataset.b; store.set("ubucket", S.bucket); render(); });
        $$("#pilebar [data-pa]").forEach(b => b.onclick = () => pileAction(b.dataset.pa));
        $$("#pilebar [data-aa]").forEach(b => b.onclick = () => artistBucketAction(b.dataset.aa));
        loadSidebar(); loadBuckets(gen);
      }
    };
  }
  // toolbar
  const sorts = S.view === "album" ? [["page", "頁序"]] : S.view === "tag" ? [["suspicious", "可疑優先"], ["confident", "信心最高"], ["edited", "最近操作"], ["new", "最新加入"], ["old", "最舊"], ["path", "路徑"], ["random", "隨機"]]
    : [["new", "最新加入"], ["edited", "最近操作"], ["old", "最舊"], ["taken", "拍攝/建立時間"], ["size", "解析度"], ["path", "路徑"], ["random", "隨機"]];
  let sort = S.view === "tag" ? store.get("sortTag", "suspicious") : S.sort;
  if (!sorts.some(s => s[0] === sort)) sort = sorts[0][0];
  const t0 = S.tagInfo;
  const segDefs = t0 && IDENTITY.includes(t0.category)
    ? [["", "已確認", t0.confirmed], ["candidates", "候選", t0.unchecked], ["rejected", "已否決", t0.rejected]]
    : [["", "全部", t0?.n], ["unchecked", "候選", t0?.unchecked], ["confirmed", "已確認", t0?.confirmed], ["model", "AI 模型", t0?.model], ["online", "搜源", t0?.online], ["rejected", "已否決", t0?.rejected]];
  const srcSeg = S.view === "tag" && t0 ? `<span class="seg srcseg" id="src-seg">${segDefs
    .filter(([k, , n]) => k === "" || k === S.src || n).map(([k, l, n]) => `<button data-s="${k}" class="${k === S.src ? "on" : ""}">${l} <i>${n || 0}</i></button>`).join("")}</span><span style="flex:1"></span>` : "";
  const listView = !["tag", "series", "album"].includes(S.view);
  tb.className = "qtb";
  // 每一頁同一條底部查詢列（v4.2）：範圍 token＋條件＋輸入框｜透視｜≡；分頁、狀態、分流都在點查詢列才開的面板裡
  const stateRow = srcSeg ? `<div class="fgrp"><span class="flbl">狀態</span>${srcSeg.replace('<span style="flex:1"></span>', "")}</div>` : "";
  tb.innerHTML = `<div class="qrow2">${qboxHTML()}
      <button class="qic ${S.xrayPin ? "on" : ""}" data-xray title="透視：顯示每張圖的媒材、NSFW、候選、尺寸（按住 Alt 也可以）">透視 <kbd>Alt</kbd></button>
      <button class="qic" id="viewbtn" title="排序、排列方式、縮圖大小、全選" aria-haspopup="true">${svg("menu", 17)}</button>
      <div id="viewmenu" class="popmenu hidden">
        <div class="pmrow"><span>排序</span><select id="sort" ${S.src === "candidates" ? "disabled title=\"候選依可信度排序\"" : ""}>${sorts.map(([k, v]) => `<option value="${k}" ${k === sort ? "selected" : ""}>${v}</option>`).join("")}</select>
          <button class="ghost" id="sortdir" title="正序／倒序：整個清單倒過來（例如最近操作的倒序＝沒動過的在前、最早動過的接著、最近動過的在最後）" ${sort === "random" || S.src === "candidates" ? "disabled" : ""}>${S.sortRev ? "↑ 倒序" : "↓ 正序"}</button></div>
        <div class="pmrow"><span>排列</span><span class="seg" id="gmode" title="格狀排列">${[["justified", "原比例"], ["square", "方格"]].map(([k, l]) => `<button data-g="${k}" class="${k === gridMode() ? "on" : ""}">${l}</button>`).join("")}</span></div>
        <div class="pmrow"><span>大小</span><input type="range" id="size" min="90" max="360" step="10" value="${S.size}" title="縮圖大小"></div>
        ${albumHideApplies() ? `<div class="pmrow"><label title="本子（同人本、圖包）的每一頁平常不放在圖庫裡，到「本子」整本看"><input type="checkbox" id="showalb" ${store.get("showAlbumPages", false) ? "checked" : ""}> 也顯示本子裡的頁</label></div>` : ""}
        <div class="pmrow"><button class="ghost" id="selall">全選 <kbd>Ctrl A</kbd></button></div>
      </div>
      ${true ? `<div id="facets" class="popmenu facets hidden"><div id="qtfacet">${quickFacetHTML()}</div>${listView ? S.facetRows || "" : stateRow}<div class="fgrp"><span class="flbl">媒材</span>${mtypeSelect()}</div>
        <div class="fhint">也可以直接打：<code>is:multi</code> <code>series:blue_archive</code> <code>date:2024</code> <code>w:&gt;=1920</code> <code>-tag:梗圖</code>　<a class="lnk" id="q-help">全部語法</a></div></div>` : ""}</div>`;
  bindQbar();
  if (listView && S.bindFacets) { const f = S.bindFacets; S.bindFacets = null; f(); }
  $("#sort").onchange = e => { if (S.view === "tag") store.set("sortTag", e.target.value); else { S.sort = e.target.value; store.set("sort", S.sort); } render(); };
  $("#sortdir").onclick = () => { S.sortRev = !S.sortRev; store.set("sortRev", S.sortRev); render(); };
  $("#selall").onclick = selectAll;
  $$("#src-seg button").forEach(b => b.onclick = () => { S.src = b.dataset.s; render(); });
  bindMtype();
  $("#size").oninput = e => { S.size = +e.target.value; document.documentElement.style.setProperty("--size", S.size + "px"); store.set("size", S.size); relayout(); };
  const sa = $("#showalb"); if (sa) sa.onchange = () => { store.set("showAlbumPages", sa.checked); loadSidebar(); render(); };
  $$("#gmode button").forEach(b => b.onclick = () => { store.set("gridMode", b.dataset.g); $$("#gmode button").forEach(x => x.classList.toggle("on", x === b)); relayout(); });
  S.curSort = sort;
  content.innerHTML = `<div class="jgrid" id="grid"></div>`;
  S.layout = null;
  await loadMore(gen);
}
/* ---- 角色資料卡 ---- */
const SITES = [[/donmai\.us/, "Danbooru"], [/e621\.net/, "e621"], [/pixiv\.net|pximg/, "Pixiv"], [/twitter\.com|x\.com/, "X"], [/iwara\.tv/, "Iwara"], [/fanbox\.cc/, "Fanbox"], [/fantia\.jp/, "Fantia"], [/patreon/, "Patreon"],
  [/skeb\.jp/, "Skeb"], [/bsky\.app/, "Bluesky"], [/tumblr/, "Tumblr"], [/artstation/, "ArtStation"], [/deviantart/, "DeviantArt"], [/instagram/, "IG"], [/youtube/, "YouTube"], [/nicovideo|seiga/, "Nico"], [/lofter/, "Lofter"], [/weibo/, "Weibo"], [/bilibili/, "bilibili"]];
const siteName = u => (SITES.find(([re]) => re.test(u)) || [null, (u.match(/^https?:\/\/(?:www\.)?([^/]+)/) || [])[1] || "連結"])[1];
export function linkIcons(links, small) {
  if (!links?.length) return "";
  const seen = new Set(); const out = [];
  for (const u of links) { const n = siteName(u); if (seen.has(n) || out.length >= (small ? 3 : 10)) continue; seen.add(n); out.push(`<a class="lk" data-s="${esc(n.toLowerCase())}" href="${esc(safeUrl(u))}" target="_blank" rel="noopener" title="${esc(u)}" onclick="event.stopPropagation()">${esc(n)}</a>`); }
  return `<span class="links-inline">${out.join("")}</span>`;
}
function infoLine(t) {
  const i = t.info;
  if (t.category === "artist") {
    if (!i) return t.info_pending ? `<div class="series mute">正在從 Danbooru 抓取資料…</div>` : "";
    const names = (i.other_names || []).slice(0, 5);
    // Danbooru 跟 X、Pixiv 合成一排，Danbooru 放第一個（以前分在兩個地方）
    const all = [...new Set([wikiUrl(i), ...(i.links || [])].filter(Boolean))].sort((a, b) => /donmai\.us/.test(b) - /donmai\.us/.test(a));
    return `<div class="series">${names.length ? `<span class="alias">${names.map(esc).join("、")}</span>` : ""}${linkIcons(all)}</div>`;
  }
  if (!i) return t.info_pending ? `<div class="series mute">正在從 Danbooru 抓取資料…</div>` : "";
  const links = t.series_links?.length ? t.series_links
    : (i.copyrights?.length ? i.copyrights : (i.copyright ? [i.copyright] : [])).map(k => ({ key: k, display: k.replace(/_/g, " ") }));
  const names = (i.other_names || []).slice(0, 4);
  if (!links.length && !names.length) return i.status === "error" ? `<div class="series mute" title="${esc(i.error || "")}">資料抓取失敗（⋯ 可重試或手動填寫）</div>` : "";
  return `<div class="series">${links.map(l => `<a class="serlink" data-series="${esc(l.key)}" title="打開作品頁：${esc(l.key)}">${esc(l.display)} ›</a>`).join("")}${names.length ? `<span class="alias">${names.map(esc).join("、")}</span>` : ""}</div>`;
}
const wikiUrl = i => i && i.url && !(i.status === "notfound" && !i.manual) ? i.url : "";   // Danbooru 查不到的 wiki 頁不顯示（點了是 404）
function infoCard(t) {
  const i = t.info, url = t.category === "artist" ? "" : wikiUrl(i), extra = t.category !== "artist" ? linkIcons((i?.links || []).filter(u => u !== url)) : "";
  if (!i || (!i.summary && !url && !extra)) return "";
  const urlName = /donmai\.us\/wiki_pages/.test(url) ? "Danbooru wiki" : /donmai\.us\//.test(url) ? "Danbooru" : siteName(url);
  return `<div class="infocard">${i.summary ? `<p>${esc(i.summary)}</p>` : ""}<div class="links">${i.post_count != null ? `Danbooru ${i.post_count.toLocaleString()} 張 · ` : ""}${url ? `<a href="${esc(safeUrl(url))}" target="_blank" rel="noopener">${esc(urlName)} ↗</a>` : ""}${extra}${i.summary ? ` · <a id="info-more">展開</a>` : ""}</div></div>`;
}
let infoStop = null;
function watchInfo() {  // 等背景抓完資料卡再刷新標題（線上工作有進度時事件流會通知）
  infoStop?.();
  const tag = S.tag; let n = 0;
  infoStop = follow(() => api(`/api/tag/${encodeURIComponent(tag)}?nsfw=${apiNsfw()}`), t => {
    if (t?.info && !t.info_pending) { if (S.tag === tag) render(); return false; }
  }, { every: 3000, alive: () => S.view === "tag" && S.tag === tag && n++ < 40 });
}
function searchFor(q) { runSearch(q); }
export function runSearch(q, view = "all") {
  S.q = (q || "").trim();
  if (S.q) go("search", S.q); else go(view || "all");
}
function drawQchip() {
  const el = $("#qchip");
  if (!S.q || !["all", "unrecognized", "videos", "nsfw", "tag", "series"].includes(S.view)) { el.classList.add("hidden"); return; }
  el.classList.remove("hidden");
  el.innerHTML = `<span class="qtxt" title="點一下修改">${svg("search", 15)} ${esc(S.q)}</span><button id="q-save" title="儲存成智慧相簿">${svg("saved", 15)}</button><button id="q-x" title="清除搜尋">✕</button>`;
  $(".qtxt", el).onclick = () => openPalette(S.q);
  $("#q-x", el).onclick = () => { S.q = ""; S.view === "all" ? go("all") : render(); };
  $("#q-save", el).onclick = () => {
    modal(`<h3>儲存成智慧相簿</h3><div class="mbody form"><div class="field"><label>名稱</label><input type="text" id="sv-name" value="${esc(S.q)}"></div>
      <div class="note">智慧相簿會一直套用這個搜尋：之後新進的圖符合條件就會自動出現在裡面。</div></div>
      <div class="mfoot"><button class="btn" data-close>取消</button><button class="btn primary" id="sv-ok">儲存</button></div>`, m => {
      const inp = $("#sv-name", m); inp.focus(); inp.select();
      inp.addEventListener("keydown", e => { e.stopPropagation(); if (e.key === "Enter") $("#sv-ok", m).click(); });
      $("#sv-ok", m).onclick = async () => { await api("/api/saved", { name: inp.value, query: S.q, view: "all" }); closeModal(); toast("已儲存到側欄「智慧相簿」"); loadSidebar(); };
    });
  };
}

/* ---- 以圖搜源 ---- */
export function sourceConfirm(ids, view = "unrecognized", miss = "all") {
  const what = ids ? `${ids.length} 張圖` : `「未識別 › ${MISS_LABEL[miss] || ""}」裡所有圖片`;
  modal(`<h3>搜尋來源（${what}）</h3><div class="mbody" style="line-height:1.8">
    依序嘗試：<b>Danbooru MD5</b>（原檔完全相符）→ <b>IQDB</b> 相似圖搜尋 → <b>Pixiv</b> 作品資訊（檔名含 Pixiv ID 時）。<br>
    找到後會${S.settings?.online_auto_apply === false ? "只存下結果，不自動套用" : "自動套用角色/作品/創作者 tag（MD5 相符、或檔名就是 Pixiv 作品 ID 時直接確認，其餘標為候選）"}。<br>
    <span class="mute">IQDB 需要上傳縮小後的圖片（1000px）。為了不給網站造成負擔，每張約間隔 2–3 秒，大量圖片會花一些時間；已搜過的會跳過。</span></div>
    <div class="mfoot"><label style="margin-right:auto;font-size:var(--fs2)"><input type="checkbox" id="src-force"> 重新搜尋已搜過的</label><button class="btn" data-close>取消</button><button class="btn primary" id="src-go">開始</button></div>`, m => {
    $("#src-go", m).onclick = async () => {
      try {
        const body = ids ? { ids, force: $("#src-force", m).checked } : { view, miss, nsfw: apiNsfw(), force: $("#src-force", m).checked };
        const r = await api("/api/online/source", body); closeModal();
        toast(r.queued ? `已排入搜源 ${r.queued} 張` : "都搜過了（可勾選重新搜尋）"); clearSel();
      } catch (e) { toast(e.message); }
    };
  });
}
export function sourceHTML(d) {
  const s = d.source;
  const lk = d.likely ? `<div class="likely ${d.likely.level}" title="本地預判（不連網）：${esc(d.likely.why.join("、") || "沒有特別線索")}">${esc(d.likely.label)}${d.likely.why.length ? `<span>・${esc(d.likely.why.slice(0, 3).join("、"))}</span>` : ""}</div>` : "";
  const im = d.imported, imName = im ? ({ x: "X 貼文", pixiv: "Pixiv 作品", danbooru: "Danbooru 貼文", e621: "e621 貼文", r34: "Rule34 貼文" }[im.site] || "原貼文") : "";
  const imp = im ? `<a class="srcpill imp" href="${esc(safeUrl(im.post_url))}" target="_blank" rel="noopener" title="${esc(im.post_url)}">⤓ 從 ${imName} 匯入 ↗</a>` : "";
  if (!s) return `<div class="srcbox"><div class="srcstat st-none"><span class="sic">?</span><div class="stx"><b>還沒找過出處</b>${lk}${imp ? `<div class="srcpills">${imp}</div>` : ""}</div></div><button class="btn sm primary" id="v-src">找出處（Danbooru／IQDB／Pixiv）<kbd>S</kbd></button></div>`;
  if (s.status === "error") return `<div class="srcbox"><div class="bad">搜源失敗：${esc(s.error || "")}</div><button class="btn sm" id="v-src">重試</button></div>`;
  const dd = s.data || {}, dan = dd.danbooru, pix = dd.pixiv;
  const methodLabel = { md5: "原檔完全相符（最可信）", md5_name: "檔名就是 Danbooru 原檔 MD5（最可信）", x_media: "X 圖片編號對上 Danbooru（最可信）",
    tweet: "同一則推文（最可信）", tweet_multi: "同一則推文（多張圖）", pixiv_id: "同一個 Pixiv 作品（最可信）",
    r34_md5: "Rule34 原檔完全相符（標籤僅供參考）", e621_md5: "e621 原檔完全相符", e621_iqdb: `e621 以圖搜圖 ${Math.round(s.similarity)}%`,
    iqdb: `IQDB 相似度 ${Math.round(s.similarity)}%${dd.verified ? `・下載原圖比對確認 ${Math.round(dd.verified * 100)}%` : ""}`, pixiv: "Pixiv" }[s.method]
    || (dd.skipped ? `沒有上傳 IQDB（${dd.skipped}）` : im ? `出處：${imName}（匯入的）· Danbooru／IQDB 沒有這張` : "找不到來源");   // 從網站匯入的：出處本來就知道，以前寫「找不到來源」很誤導
  const methodExtra = { duplicate: "（沿用重複圖的結果）" }[s.method] || "";
  const have = new Set(d.tags.filter(t => t.state >= 0).map(t => t.name));
  const disp = new Map(d.tags.filter(t => t.display).map(t => [t.name, t.display]));
  const sug = (dd.suggested || []).map(t => `<span class="chip ${t.category} ${have.has(t.name) ? "c1" : ""}" data-t="${esc(t.name)}" data-c="${t.category}"><span class="cat ${esc(t.category)}">${catName(t.category)}</span> <span class="nm" title="${esc(t.name)}">${esc(disp.get(t.name) || t.name)}</span>${t.via === "pixiv" ? `<span class="src" title="由 Pixiv 標籤對應">px</span>` : ""}${t.via === "pixiv-author" ? `<span class="src" title="Pixiv 作者">px</span>` : ""}${t.via === "r34" ? `<span class="src" title="來自 Rule34（Danbooru 上有同名標籤）">r34</span>` : ""}${t.via === "e621" ? `<span class="src" title="來自 e621（Danbooru 上有同名標籤）">e6</span>` : ""}${have.has(t.name) ? "" : `<button data-src-add title="套用並確認">＋</button>`}</span>`).join("");
  const pending = (dd.suggested || []).filter(t => !have.has(t.name)).length;
  const iq = !dan && dd.iqdb?.length ? `<div class="nearest"><div class="nh">IQDB 最接近的（${dd.verify?.length ? "下載比對後不像，" : ""}都沒採用）</div>${dd.iqdb.slice(0, 3).map(h => `<div class="nr"><a href="https://danbooru.donmai.us/posts/${h.post_id}" target="_blank" rel="noopener">#${h.post_id}</a><span class="nb"><i style="width:${Math.round(h.similarity)}%"></i></span><em>${Math.round(h.similarity)}%</em></div>`).join("")}</div>` : "";
  const st = s.status === "found" ? "found" : im ? "imp" : dd.skipped ? "skip" : "none";
  const ic = { found: "✓", imp: "⤓", skip: "–", none: "?" }[st];
  const pills = [dan ? `<a class="srcpill" href="${esc(safeUrl(dan.post_url))}" target="_blank" rel="noopener">Danbooru #${esc(dan.post_id)} ↗</a>` : "",
    dd.e621 ? `<a class="srcpill" href="${esc(safeUrl(dd.e621.post_url))}" target="_blank" rel="noopener">e621 #${esc(dd.e621.post_id)} ↗</a>` : "",
    dd.rule34 ? `<a class="srcpill" href="${esc(safeUrl(dd.rule34.post_url))}" target="_blank" rel="noopener">Rule34 #${esc(dd.rule34.post_id)} ↗</a>` : "",
    s.source_url ? `<a class="srcpill" href="${esc(safeUrl(s.source_url))}" target="_blank" rel="noopener" title="${esc(s.source_url)}">原始來源 ↗</a>` : "",
    pix ? `<a class="srcpill" href="${esc(safeUrl(pix.url))}" target="_blank" rel="noopener">Pixiv ↗</a>` : "", imp].filter(Boolean).join("");
  return `<div class="srcbox"><div class="srcstat st-${st}" ${im && s.status !== "found" ? `title="Danbooru、IQDB 上找不到同一張，所以沒有從那裡自動帶角色和作品；創作者用匯入時的貼文作者"` : ""}>
      <span class="sic">${ic}</span><div class="stx"><b>${st === "imp" && s.status !== "found" ? `出處是匯入的 ${imName}` : esc(methodLabel)}${methodExtra}</b>${st === "imp" && s.status !== "found" ? `<span>Danbooru、IQDB 沒有這張</span>` : ""}
      ${pills ? `<div class="srcpills">${pills}</div>` : ""}</div></div>
    ${pix ? `<details class="pix" ${pending ? "open" : ""}><summary><b>${esc(pix.title || "")}</b> — ${esc(pix.user || "")}${pix.x_restrict ? ` <span class="r18">R-18</span>` : ""}${pix.ai ? ` <span class="r18" style="background:#666">AI</span>` : ""} <span class="mute">${esc(pix.date || "")}</span></summary>
      <div class="pixtags">${(pix.tags || []).map(t => `<button data-ptag="${esc(t.tag)}" title="指定這個 Pixiv 標籤是哪個角色（之後自動對應）">${esc(t.tag)}${t.en ? ` <i>${esc(t.en)}</i>` : ""}</button>`).join("")}</div>
      <div class="mute" style="font-size:var(--fs1);margin-top:4px">點 Pixiv 標籤可以指定它是哪個角色，之後所有帶這個標籤的 Pixiv 作品都會自動對上。</div></details>` : ""}
    ${dd.pixiv_error ? `<div class="mute" style="font-size:var(--fs1)">Pixiv：${esc(dd.pixiv_error)}</div>` : ""}
    ${iq}
    ${dd.e621?.unmapped?.length ? `<div class="mute" style="font-size:var(--fs1);margin-top:6px">e621 上的其他標籤（Danbooru 沒有同名的，不會自動套用）：${dd.e621.unmapped.map(t => esc(t.name)).join("、")}</div>` : ""}
    ${dd.rule34?.unmapped?.length ? `<div class="mute" style="font-size:var(--fs1);margin-top:6px">Rule34 上的其他標籤（Danbooru 沒有同名的，不會自動套用）：${dd.rule34.unmapped.map(t => esc(t.name)).join("、")}</div>` : ""}
    ${sug ? `<div class="chips" style="margin-top:8px">${sug}</div>${pending ? `<button class="btn sm" id="v-src-all" style="margin-top:8px">全部套用並確認（${pending}）</button>` : `<div class="mute" style="font-size:var(--fs1);margin-top:4px">出處的標籤都已經套用了</div>`}` : ""}</div>`;
}


function tagMenu(t) {
  modal(`<h3>標籤：${esc(t.name)}</h3><div class="mbody form">
    <div class="field"><label>重新命名</label><input type="text" id="tm-name" value="${esc(t.name)}"></div>
    <div class="field"><label>類別</label><select id="tm-cat">${["character", "artist", "copyright", "custom", "general"].map(c => `<option value="${c}" ${c === t.category ? "selected" : ""}>${catName(c)}</option>`).join("")}</select></div>
    <div class="field"><label>顯示名稱</label><input type="text" id="tm-disp" value="${esc(t.info?.display_name || "")}" placeholder="${esc(t.display || "")}（留空＝自動）"></div>
    <div class="field"><label>合併到</label><div style="display:flex;gap:6px"><input type="text" id="tm-merge" placeholder="另一個標籤名稱（例如 Danbooru 正式名稱）"><button class="btn sm" id="tm-merge-btn">合併</button></div></div>
    <div class="field"><label>自動標註</label><label><input type="checkbox" id="tm-block" ${t.auto_block ? "checked" : ""}> 停止自動標註（只保留手動標記）</label></div>
    <div class="field"><label>也代表<br><small>標籤蘊含</small></label><div><input type="text" id="tm-imp" value="${esc((t.implies?.implies || []).map(x => x.name).join(", "))}" placeholder="例如造型的本體 asuna_(blue_archive)、表情包 → 梗圖">
      <div class="mute" style="font-size:var(--fs1);margin-top:4px">確認「${esc(t.name)}」的圖會自動帶上這些（你否決過的不動）。${(t.implies?.implies || []).some(x => x.src.startsWith("danbooru")) ? "有些來自 Danbooru（造型 ⇒ 本體、角色 ⇒ 所屬作品）；拿掉就不再套用。" : ""}${t.implies?.implied_by?.length ? `<br>${t.implies.implied_by.length} 個標籤也代表它：${t.implies.implied_by.slice(0, 8).map(x => esc(x.name)).join("、")}${t.implies.implied_by.length > 8 ? "…" : ""}` : ""}</div>
      ${t.implies?.guessed ? `<label style="display:block;margin-top:4px"><input type="checkbox" id="tm-guess-off"> 跟「${esc(t.implies.guessed)}」不是同一個人<span class="mute">（程式從名字猜是造型，算人數時當同一個）</span></label>` : ""}</div></div>
    <h5>資料卡 ${t.info?.manual ? "（已手動修改）" : t.info ? "（來自 Danbooru）" : ""}</h5>
    <div class="field"><label>所屬作品</label><input type="text" id="tm-cp" value="${esc(t.info?.copyright || "")}" placeholder="例如 project_sekai"></div>
    <div class="field"><label>其他名稱<br><small>逗號分隔，可用來搜尋</small></label><input type="text" id="tm-on" value="${esc((t.info?.other_names || []).join(", "))}"></div>
    <div class="field"><label>簡介</label><textarea id="tm-sum">${esc(t.info?.summary || "")}</textarea></div>
    <div class="field"><label>連結<br><small>一行一個，可刪可加</small></label><textarea id="tm-links" placeholder="https://x.com/帳號&#10;https://www.pixiv.net/users/…">${esc([...new Set([wikiUrl(t.info), ...(t.info?.links || [])].filter(Boolean))].join("\n"))}</textarea></div>
  </div><div class="mfoot"><button class="btn danger" id="tm-del">刪除標籤</button><button class="btn" id="tm-refetch" ${t.category === "artist" ? "" : `style="margin-right:auto"`}>從 Danbooru 重新抓取</button>${t.category === "artist" ? `<button class="btn" id="tm-links-find" style="margin-right:auto" title="到 X、Pixiv、e621 找這個人的其他帳號，加進連結">從其他網站找連結</button>` : ""}<button class="btn" data-close>取消</button><button class="btn primary" id="tm-save">儲存</button></div>`, m => {
    $("#tm-merge-btn", m).onclick = async () => {
      const into = $("#tm-merge", m).value.trim(); if (!into) return;
      try { const r = await api(`/api/tag/${encodeURIComponent(t.name)}/merge`, { into }); closeModal(); toast(`已合併到 ${r.into}`); loadSidebar(); go("tag", r.into); }
      catch (e) { toast(e.message); }
    };
    $("#tm-del", m).onclick = () => { closeModal(); tagDelete(t); };
    const lf = $("#tm-links-find", m);
    if (lf) lf.onclick = async () => {
      lf.disabled = true; lf.textContent = "找連結中…";
      try {
        const r = (await api("/api/online/artist-links", { tag: t.name, force: true })).results[0] || {};
        if (r.skipped) toast(r.skipped);
        else toast(r.added?.length ? `加了 ${r.added.length} 個連結${r.merged_into ? `，並合併到 ${r.merged_into}` : ""}` : "沒有找到新的連結");
        closeModal(); go("tag", r.merged_into || t.name);
      } catch (e) { toast(e.message); lf.disabled = false; lf.textContent = "從其他網站找連結"; }
    };
    $("#tm-refetch", m).onclick = async () => {
      try { await api(`/api/tag/${encodeURIComponent(t.name)}/info`, { refetch: true }); closeModal(); toast("已排入抓取"); watchInfo(); } catch (e) { toast(e.message); }
    };
    $("#tm-save", m).onclick = async () => {
      const name = $("#tm-name", m).value.trim(), category = $("#tm-cat", m).value;
      const info = { copyright: $("#tm-cp", m).value, other_names: $("#tm-on", m).value.split(/[,，、]/).map(x => x.trim()).filter(Boolean), summary: $("#tm-sum", m).value };
      const oldLinks = [...new Set([wikiUrl(t.info), ...(t.info?.links || [])].filter(Boolean))].join("\n");
      const newLinks = $("#tm-links", m).value.split(/\s*\n\s*/).map(x => x.trim()).filter(Boolean);
      const bad = newLinks.filter(u => !/^https?:\/\/\S+$/i.test(u));
      if (bad.length) { toast("連結要是 http:// 或 https:// 開頭：" + bad[0]); return; }
      if (newLinks.join("\n") !== oldLinks) info.links = newLinks;
      const changed = !!info.links || info.copyright !== (t.info?.copyright || "") || info.other_names.join(",") !== (t.info?.other_names || []).join(",") || info.summary !== (t.info?.summary || "");
      const imp = $("#tm-imp", m).value.split(/[,，、\n]/).map(x => x.trim()).filter(Boolean);
      try {
        if (imp.join(",") !== (t.implies?.implies || []).map(x => x.name).join(","))
          await api(`/api/tag/${encodeURIComponent(t.name)}/implies`, { implies: imp });
        if ($("#tm-guess-off", m)?.checked) await api(`/api/tag/${encodeURIComponent(t.name)}/implies-off`, { other: t.implies.guessed, off: true });
        if (changed) await api(`/api/tag/${encodeURIComponent(t.name)}/info`, info);
        await api(`/api/tag/${encodeURIComponent(t.name)}`, { rename: name, category, display_name: $("#tm-disp", m).value, auto_block: $("#tm-block", m).checked !== !!t.auto_block ? $("#tm-block", m).checked : undefined }); closeModal(); loadSidebar(); go("tag", name.replace(/ /g, "_")); }
      catch (e) { toast(e.message); }
    };
  });
}

// 刪除標籤（角色、創作者、作品都是標籤）。opts.after：刪完要做什麼（預設回上一頁）；opts.note：多一段說明
export async function tagDelete(t, opts = {}) {
  let n = t.stats?.n ?? t.n, conf = t.confirmed;
  if (n == null) { try { const x = await api(`/api/tag/${encodeURIComponent(t.name)}?nsfw=show`); n = x.n || 0; conf = x.confirmed || 0; } catch { n = 0; } }
  const what = t.category === "copyright" ? "作品" : "標籤";
  modal(`<h3>刪除${what}「${esc(t.display || t.name)}」？</h3><div class="mbody">
    會從 <b>${fmtN(n)}</b> 張圖上拿掉這個${what}${conf ? `（其中 ${fmtN(conf)} 張是確認過的）` : ""}，圖片本身不會動，資料卡和別名也一起刪掉。刪完按 <kbd>Ctrl</kbd>+<kbd>Z</kbd> 或提示裡的「撤銷」可以整個還原。
    ${opts.note ? `<p class="mute">${opts.note}</p>` : ""}
    <label style="display:block;margin-top:12px"><input type="checkbox" id="td-block" checked> 以後也不要自動標上
      <div class="mute" style="font-size:var(--fs1);margin-left:22px">AI 標註、Danbooru／e621／Rule34 下次又產生這個標籤時直接略過。不勾的話，下次整理可能又冒出來。</div></label>
    <div class="note" style="margin-top:10px">只是想改名或跟另一個標籤合併？用上一頁的「重新命名」或「合併到」，紀錄會保留。</div>
  </div><div class="mfoot"><button class="btn" data-close>取消</button><button class="btn danger" id="td-ok">刪除</button></div>`, m => {
    $("#td-ok", m).onclick = async () => {
      try {
        const r = await api(`/api/tag/${encodeURIComponent(t.name)}/delete`, { block: $("#td-block", m).checked });
        closeModal(); loadSidebar();
        if (opts.after) opts.after(); else history.length > 1 ? history.back() : go("home");
        toast(`已刪除「${t.display || t.name}」（${fmtN(r.removed)} 筆）`, { label: "撤銷", fn: undo });
      } catch (e) { toast(e.message); }
    };
  });
}

// 圖庫（全部、影片、NSFW、官方）不放本子裡的頁：一本 170 頁會把圖庫洗版；到「本子」整本看（使用者要求 2026-10-06）
export const LIB_ALBUM_HIDE = ["all", "videos", "nsfw", "official"];
// 角色、創作者、作品頁也不放（整本標了角色，角色頁不會多出一百多頁）；搜尋時照樣找得到
export const albumHideApplies = () => (LIB_ALBUM_HIDE.includes(S.view) && !S.q) || S.view === "tag" || S.view === "series";
export const albumHidden = () => albumHideApplies() && !store.get("showAlbumPages", false);
export function mtypeSelect() {
  const opt = ([k, v]) => `<option value="${k}" ${k === (S.mtype || "") ? "selected" : ""}>${v}</option>`;
  return `<select id="mtype" title="媒材（AI 沒把握的不列在各類裡，集中在「未定」；選圖按 M 改）">${[["", "全部媒材"], ...Object.entries(MTYPES), ["unsure", MTYPE_UNSURE]].map(opt).join("")}<optgroup label="官方作品">${Object.entries(OFFICIAL).map(opt).join("")}</optgroup></select>`;
}
export function bindMtype() {
  const m = $("#mtype"); if (!m) return;
  m.classList.toggle("on", !!S.mtype);   // 有篩選時亮起來，提醒「目前只看某種媒材」
  m.onchange = e => {
    S.mtype = e.target.value; render();
  };   // 只在這一頁有效，換頁就回到全部媒材
}
/* 大圖關掉時（裡面改過標籤）：在原地更新，不要整頁重畫回到最上面。
   重新拿一次「目前已載入的這麼多張」：不再符合條件的拿掉、標籤更新；捲動位置對準剛剛看的那張
   （它被拿掉的話就是原本排在它後面、還在的那張），在畫面上的高度跟關掉前一樣。 */
export async function refreshInPlace(anchorId) {
  const grid = $("#grid"), c = $("#content");
  if (!S.layout || !grid || !grid.classList.contains("jgrid") || S.view === "review" || anchorId == null) return render();
  const gen = S.gen;
  const oldIds = S.items.map(x => x.id);
  const rowY = id => {
    const i = S.items.findIndex(x => x.id === id); if (i < 0) return null;
    const r = S.layout.rows.find(r => r.cells.some(cc => cc.i === i)); return r ? r.y : null;
  };
  let off = null; const y0 = rowY(anchorId);
  if (y0 != null) { off = y0 + grid.offsetTop - c.scrollTop; if (off < -50 || off > c.clientHeight) off = null; }
  if (off == null) off = Math.round(c.clientHeight / 3);     // 在大圖裡往後翻了很多張：把它放在畫面上方三分之一
  const lim = Math.max(PAGE * 2, oldIds.length);
  const p = gridParams(); p.set("offset", 0); p.set("limit", lim);
  let d; try { d = await api("/api/media?" + p); } catch { return render(); }
  if (gen !== S.gen) return;
  if (!d.items.length) return render();
  S.items = d.items; S.total = d.total; S.done = d.items.length < lim;
  const now = new Set(S.items.map(x => x.id));
  [...S.selected].forEach(id => { if (!now.has(id)) S.selected.delete(id); });
  relayout();
  const k = oldIds.indexOf(anchorId);
  const cand = k < 0 ? [anchorId] : [...oldIds.slice(k), ...oldIds.slice(0, k).reverse()];
  const target = cand.find(id => now.has(id));
  const y = target != null ? rowY(target) : null;
  if (y != null) c.scrollTop = Math.max(0, y + grid.offsetTop - off);
  drawWindow(true); updateFooter(); updateActionBar();
  if (S.view === "unrecognized") loadBuckets();
}
let _ac = null, _acGen = null;
export async function loadMore(gen) {
  if (S.view === "review") return loadReview(gen);
  if (S.loading === S.gen || S.done || !$("#grid")) return;
  S.loading = gen;   // 記下是哪一次畫面在載入：切換頁面後舊的請求不會把新頁面卡住
  try {
    // 全部載入：一次拿 1000 筆；返回上一頁要捲回第 N 張：一次載到 N 張附近（S.wantN，shell.js restoreScroll）
    const lim = S.loadAll ? 1000 : Math.min(1000, Math.max(PAGE * 2, (S.wantN || 0) - S.items.length + PAGE));
    const p = gridParams(); p.set("limit", lim);
    // 換了頁面（gen 變了）：上一頁還在等的清單直接取消，伺服器不用白做（b93）
    if (_ac && _acGen !== gen) _ac.abort();
    _ac = new AbortController(); _acGen = gen;
    const d = await api("/api/media?" + p, undefined, undefined, { signal: _ac.signal });
    if (gen !== S.gen) return;
    S.total = d.total;
    if (p.get("offset") === "0") S.albumHidden = d.album_hidden || 0;     // 這一頁藏起來的本子頁（頁尾說一聲）
    if (S.view === "series" && !S.seriesChar && !S.seriesMiss && !S.q) { const a = $("#sr-n"), b = $("#sr-n2"); if (a) a.textContent = fmtN(d.total); if (b) b.textContent = fmtN(d.total); }
    S.items.push(...d.items);
    if (d.items.length < lim) S.done = true;
    if (!S.items.length) { $("#content").innerHTML = emptyHTML(); bindEmpty(gen); updateFooter(); return; }
    relayout();
    updateFooter();
  } catch (e) { if (e.name !== "AbortError") toast("載入失敗：" + e.message); S.loadAll = false; }
  finally { if (S.loading === gen) S.loading = false; }
  // 載入中捲到底的話，那次捲動會被略過，而到底之後就不會再有捲動事件 → 以前會卡在「已載入 240」。載完再檢查一次
  if (gen === S.gen && !S.done && S.layout && $("#grid")) {
    const c = $("#content"), bot = c.scrollTop - $("#grid").offsetTop + c.clientHeight;
    if (S.loadAll || bot + 1500 > S.layout.height) return loadMore(gen);
  }
  if (S.done) S.loadAll = false;
  updateFooter();
}
export async function loadAllItems() {
  const gen = S.gen;
  if (S.done) return true;
  S.loadAll = true; updateFooter();
  if (S.loading !== S.gen) await loadMore(gen);
  while (gen === S.gen && !S.done && S.loadAll) await new Promise(r => setTimeout(r, 100));   // 已經有一批在載入：等它接著載完
  return gen === S.gen && S.done;
}
