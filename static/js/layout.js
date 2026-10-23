// ES module（2026-10）：用到別的檔案的東西都要在這裡 import；瀏覽器會在載入時檢查名字對不對。
import { $, PAGE, S, apiNsfw, debounce, esc, store } from "./core.js";
import { albumHidden, loadMore } from "./grid.js";
import { cellHTML } from "./filters.js";
import { loadReview } from "./review.js";
/* ============ 依原比例排列（justified）＋ 只畫看得到的列（虛擬捲動） ============ */
const GAP = 6, SECH = 34;
export const gridMode = () => store.get("gridMode", S.settings?.grid_mode || "justified");
export function relayout() {
  const grid = $("#grid"); if (!grid || !grid.classList.contains("jgrid")) return;
  const W = grid.clientWidth || $("#content").clientWidth - 40;
  const target = S.size, square = gridMode() === "square";
  const rows = []; let row = [], sum = 0, y = 0;
  const flush = (last) => {
    if (!row.length) return;
    let hgt = (W - GAP * (row.length - 1)) / sum;
    // 最後一列（或只剩一張的列）不撐滿：保持原本大小，不要被拉得很大
    if (last || row.length === 1 || hgt > target * 1.6) hgt = Math.min(hgt, target);
    let x = 0;
    const cells = row.map(([i, r]) => { const w = r * hgt; const c = { i, x, w }; x += w + GAP; return c; });
    rows.push({ y, h: hgt, cells }); y += hgt + GAP; row = []; sum = 0;
  };
  // 本子由好幾本組成：換本的地方另起一列、畫一條分隔（本名、頁數）
  const books = S.view === "album" ? (S.album?.books || []) : [];
  let sec;
  S.items.forEach((it, i) => {
    if (books.length && it.sec != null && it.sec !== sec) {
      flush(true);
      sec = it.sec;
      const b = books[sec] || {};
      rows.push({ y, h: SECH, cells: [], hdr: { n: sec + 1, label: b.post?.title || b.label || "", pages: b.n || 0, first: b.first,
        date: (b.post?.published || "").slice(0, 10), text: !!b.post?.text, sec } });
      y += SECH + GAP;
    }
    let r = square || !it.width || !it.height ? 1 : it.width / it.height;
    r = Math.max(0.4, Math.min(r, 3));  // 超長條圖不要把整列撐爆
    row.push([i, r]); sum += r;
    if (sum * target + GAP * (row.length - 1) >= W) flush(false);
  });
  flush(true);
  S.layout = { rows, height: y, key: "" };
  grid.style.height = y + "px";
  drawWindow(true);
}
export function drawWindow(force) {
  const grid = $("#grid"), c = $("#content"); if (!grid || !S.layout) return;
  const top = c.scrollTop - grid.offsetTop, bot = top + c.clientHeight, pad = 900;
  const rs = S.layout.rows;
  let a = 0; while (a < rs.length && rs[a].y + rs[a].h < top - pad) a++;
  let b = a; while (b < rs.length && rs[b].y < bot + pad) b++;
  const key = a + ":" + b;
  if (!force && key === S.layout.key) return;
  S.layout.key = key;
  const want = [];
  for (let r = a; r < b; r++) if (rs[r].hdr) {
    const h = rs[r].hdr;
    want.push(["h" + h.sec, `<div class="booksec" style="top:${rs[r].y.toFixed(1)}px;height:${SECH}px" data-first="${h.first}"><b>第 ${h.n} 本</b><span class="nm">${esc(h.label)}</span>${h.date ? `<span class="bkdate">${esc(h.date)}</span>` : ""}${h.text ? `<button class="lnk bkpost" data-bkpost="${h.sec}">內文</button>` : ""}<span class="mute">${h.pages} 頁</span></div>`]);
  }
  for (let r = a; r < b; r++) for (const cc of rs[r].cells)
    want.push(["c" + S.items[cc.i].id, cellHTML(S.items[cc.i], cc.i, `left:${cc.x.toFixed(1)}px;top:${rs[r].y.toFixed(1)}px;width:${cc.w.toFixed(1)}px;height:${rs[r].h.toFixed(1)}px`, true)]);
  patchChildren(grid, want);
  // 接近底部 → 載入下一頁
  if (!S.done && S.loading !== S.gen && bot + 1500 > S.layout.height) loadMore(S.gen);
}
/* 只動有變的格子（b93）：以前每次都 innerHTML 整片重建，已經載好的縮圖也變成新的 <img>、重新淡入，
   捲動時整個畫面一閃一閃。現在用 data-k（圖片 id）對應：留在畫面上的格子原封不動；
   同一張只是位置、選取、角標變了 → 改屬性、換角標，<img> 保留（不重新解碼、不再淡入）。 */
const _tpl = document.createElement("template");
function _el(html) { _tpl.innerHTML = html; return _tpl.content.firstElementChild; }
export function patchChildren(box, want) {
  const old = new Map();
  for (const el of [...box.children]) { if (el.dataset.k && !old.has(el.dataset.k)) old.set(el.dataset.k, el); else el.remove(); }
  const seen = new Set();
  for (let [k, html] of want) {
    if (seen.has(k)) k += "#" + seen.size;   // 同一張圖出現兩次（理論上不會）
    seen.add(k);
    const cur = old.get(k);
    if (cur) {
      old.delete(k);
      if (cur._h === html) continue;
      const nu = _el(html);
      const ci = cur.querySelector(":scope > img"), ni = nu.querySelector(":scope > img");
      if (ci && ni && ci.getAttribute("src") === ni.getAttribute("src")) {
        const miss = cur.classList.contains("missing-thumb");
        for (const at of [...cur.attributes]) if (!nu.hasAttribute(at.name)) cur.removeAttribute(at.name);
        for (const at of [...nu.attributes]) if (cur.getAttribute(at.name) !== at.value) cur.setAttribute(at.name, at.value);
        for (const ch of [...cur.childNodes]) if (ch !== ci) ch.remove();
        for (const ch of [...nu.childNodes]) if (ch !== ni) cur.append(ch);
        if (miss) cur.classList.add("missing-thumb");
        cur.dataset.k = k; cur._h = html;
        continue;
      }
      cur.remove();
    }
    const el = _el(html); el.dataset.k = k; el._h = html;
    const img = el.querySelector(":scope > img");
    box.append(el);
    // 瀏覽器快取裡已經有的縮圖：直接顯示，不要從透明淡入
    if (img && img.complete && img.naturalWidth) img.classList.add("ld");
  }
  for (const el of old.values()) el.remove();
}
$("#content").addEventListener("scroll", () => {
  if (S.layout) requestAnimationFrame(() => drawWindow(false));
  else if (S.view === "review") { const c = $("#content"); if (c.scrollTop + c.clientHeight + 1200 > c.scrollHeight) loadReview(S.gen); }
}, { passive: true });
window.addEventListener("resize", debounce(() => { if (S.layout) relayout(); }, 120));

export function gridParams() {
  const series = S.view === "series";
  // 選了角色：連同他的造型一起（asuna_(blue_archive)|asuna_(bunny)_(blue_archive)…，跟角色列表上的張數一致）
  const selC = series && S.seriesChar ? (S.seriesData?.chars || []).find(c => c.name === S.seriesChar) : null;
  const charQ = selC ? [selC.name, ...(selC.skins || []).map(k => k.name)].join("|") : S.seriesChar;
  const q = series ? [`series:${S.seriesKey}`, charQ, { nochar: "is:nochar", noartist: "is:missartist" }[S.seriesMiss], S.q].filter(Boolean).join(" ") : S.q;
  const p = new URLSearchParams({ view: S.view === "tag" || series || S.view === "album" ? "all" : S.view, q, sort: S.view === "album" ? "page" : S.curSort, nsfw: apiNsfw(), offset: S.items.length, limit: PAGE * 2 });
  if (S.view === "album" && S.albumId) p.set("album", S.albumId);
  if (S.tag) p.set("tag", S.tag);
  if (S.tag && S.src) p.set("src", S.src);
  if (S.view === "unrecognized") { p.set("miss", S.miss); if (S.bucket) p.set("bucket", S.bucket); }
  if (S.mtype) p.set("mtype", S.mtype);
  if (albumHidden()) p.set("noalbum", 1);   // 圖庫、角色頁、作品頁不放本子裡的頁；搜尋時照樣找得到
  if (S.curSort === "random") p.set("seed", S.seed || 0);
  else if (S.sortRev && S.curSort !== "page") p.set("rev", 1);   // 正序／倒序（≡ 選單）     // 同一次瀏覽每一頁同一個種子：不重複、不漏
  if (S.until && S.view !== "album") p.set("until", S.until);   // 背景加進來的新圖不插進正在捲的清單（重新整理才出現）
  return p;
}
