// ES module（2026-10）：用到別的檔案的東西都要在這裡 import；瀏覽器會在載入時檢查名字對不對。
import { $, $$, S, api, apiNsfw, closeModal, esc, fmtN, modal, toast } from "./core.js";
import { go, loadSidebar } from "./shell.js";
import { loadMore, render, runSearch } from "./grid.js";
import { clearSel } from "./selection.js";
import { undo, updateActionBar } from "./actions.js";
/* ============ 未識別頁：分流 & 相似成組 ============ */
export const BUCKETS = [["", "全部", "all", "所有未識別的圖"], ["cand", "有 AI 候選", "cand", "AI 有猜角色／作品，確認最快"], ["nocand", "沒有候選", "nocand", "AI 也猜不出來，要自己認"],
  ["unsearched", "還沒搜來源", "unsearched", "按旁邊的「全部找」自動找出處"], ["video", "影片", "video", ""], ["skipped", "不用整理的", "skipped", "按過 L 不用整理的圖"]];
// 分堆（app/piles.py）：依描述標籤自動分成幾堆，一堆一堆決定。影片已經在上面，不重複
export const PILES = [["p_meme", "梗圖／截圖", "聊天紀錄、假截圖、meme、大字的圖（抽查約九成是梗圖或截圖）"],
  ["p_texty", "有字的插畫", "有人物、圖上有中文字／英文字／emoji：多半是表情包、梗圖，也有翻譯過的同人圖，先看一遍"],
  ["p_photo", "照片／cosplay", "真人照片、cosplay、公仔"], ["p_nohuman", "沒有人物", "食物、動物、風景、物品"],
  ["p_comic", "漫畫", "漫畫頁、四格"], ["p_illust", "插畫", "有人物的插畫：真正要認角色的（仍有一兩成梗圖混在裡面）"]];
// 缺創作者的分流：原創（標了 original 卻沒有作者，以前混在五千多張裡）、AI 生成（ComfyUI 等，檔案裡有生成資料）
export const ART_BUCKETS = [["orig", "原創作品", "你標了「原創」、但還沒有作者的圖"],
  ["aigen", "AI 生成", "檔案裡有 ComfyUI／Stable Diffusion／NovelAI 的生成資料：沒有畫師可找"]];
export async function artistBucketAction(action) {
  const b = ART_BUCKETS.find(x => x[0] === S.bucket); if (!b) return;
  const body = { bucket: S.bucket, action, miss: S.miss, nsfw: apiNsfw(), except_ids: [...S.selected] };
  let n; try { n = (await api("/api/unrec/pile-action?dry_run=true", body)).n; } catch (e) { return toast(e.message); }
  if (!n) return toast("這裡已經沒有圖了");
  if (action === "source") {
    const r = await api("/api/unrec/pile-action", body).catch(e => ({ error: e.message }));
    return toast(r.error || `已排入找作者 ${fmtN(r.queued ?? n)} 張（背景進行，右上角看進度）`);
  }
  modal(`<h3>${esc(b[1])}：${fmtN(n)} 張不用再找作者？</h3><div class="mbody"><p>這 <b>${fmtN(n)}</b> 張不再列在「缺創作者」。之後標了作者也沒關係；隨時可以撤銷，或在大圖裡取消。</p>
    ${body.except_ids.length ? `<p>你勾起來的 <b>${body.except_ids.length}</b> 張會留下來。</p>` : ""}</div>
    <div class="mfoot"><button class="btn" data-close>取消</button><button class="btn primary" id="aa-go">確定</button></div>`, m => {
    $("#aa-go", m).onclick = async () => {
      try { await api("/api/unrec/pile-action", body); } catch (e) { return toast(e.message); }
      closeModal(); clearSel(); toast(`不用再找作者 ${fmtN(n)} 張`, { label: "撤銷", fn: undo }); loadSidebar(); render();
    };
  });
}
const PILE_ACT = { p_meme: "skip_tag", p_texty: "skip_tag", p_photo: "skip", p_nohuman: "skip", p_comic: "skip", p_illust: "skip" };
export function bucketBar() {
  // 分面面板裡的幾排：分流、依作品、分堆／缺作者的（「全部找」放在「還沒搜來源」旁邊）
  return `<div class="fgrp"><span class="flbl">分流</span><div class="ubk" id="ubk">${BUCKETS.map(([k, l, c, t]) => `<button data-b="${k}" class="${k === (S.bucket || "") ? "on" : ""}${k === "skipped" ? " dim" : ""}" title="${t}">${l}<b data-ub="${c}"></b></button>`
    + (k === "unsearched" ? `<button class="findall" id="find-src" title="像素指紋、圖上簽名、e621、Rule34、以圖搜源，一次跑完">全部找出處</button>` : "")).join("")}</div></div>
    ${["character", "artist"].includes(S.miss) ? `<div class="fgrp"><span class="flbl" title="標了作品（或角色屬於這部作品）、但還沒有${S.miss === "artist" ? "作者" : "角色"}的圖。點一下到作品頁，只看這些">依作品</span><div class="ubk piles byser" id="byseries"><span class="mute" style="font-size:var(--fs1)">載入中…</span></div></div>` : ""}
    ${S.miss === "artist" ? `<div class="fgrp"><span class="flbl">缺作者的</span><div class="ubk piles" id="piles">${ART_BUCKETS.map(([k, l, t]) => `<button data-b="${k}" class="${k === S.bucket ? "on" : ""}" title="${esc(t)}">${l}<b data-ub="${k}"></b></button>`).join("")}</div></div>`
    : `<div class="fgrp"><span class="flbl" title="依 AI 看到的內容自動分堆：一堆一堆看過，整堆標「不用整理」">分堆</span><div class="ubk piles" id="piles">${PILES.map(([k, l, t]) => `<button data-b="${k}" class="${k === S.bucket ? "on" : ""}" title="${esc(t)}">${l}<b data-ub="${k}"></b></button>`).join("")}</div></div>`}`;
}
// 選了一堆（分堆、原創作品、AI 生成）：整堆處理的說明和按鈕，留在畫面上
export function pileBar() {
  const pile = PILES.find(p => p[0] === S.bucket);
  const abk = S.miss === "artist" ? ART_BUCKETS.find(p => p[0] === S.bucket) : null;
  if (abk) return `<div class="pilebar" id="pilebar"><div class="pt"><b>${abk[1]}</b>　${esc(abk[2])}<br><span class="mute">勾起來的圖會留下不處理。都可以撤銷，之後也隨時可以再標作者。</span></div>
      <div class="pa"><button class="btn primary" data-aa="source" title="像素指紋、Danbooru、以圖搜圖…（不做預判，一定搜）">全部找作者</button>
      <button class="btn" data-aa="artist_done" title="自己畫的、AI 生成的、找不到也沒關係：不再列在缺創作者">全部「不用再找作者」</button></div></div>`;
  if (pile && S.miss !== "artist") return `<div class="pilebar" id="pilebar"><div class="pt"><b>${pile[1]}</b>　${esc(pile[2])}<br><span class="mute">先捲一遍，把要認角色的圖勾起來（它們會被留下），再整堆處理。都可以撤銷。</span></div>
      <div class="pa"><button class="btn ${PILE_ACT[pile[0]] === "skip_tag" ? "primary" : ""}" data-pa="skip_tag" title="不再出現在未識別，並加上自訂標籤「梗圖」，之後搜得到">全部不用整理＋標「梗圖」</button>
      <button class="btn ${PILE_ACT[pile[0]] === "skip" ? "primary" : ""}" data-pa="skip" title="留在圖庫，只是不再出現在未識別">全部不用整理</button></div></div>`;
  return "";
}
export async function pileAction(action) {
  const bucket = S.bucket, pile = PILES.find(p => p[0] === bucket); if (!pile) return;
  const except_ids = [...S.selected];
  const body = { bucket, action, miss: S.miss, nsfw: apiNsfw(), except_ids };
  let n; try { n = (await api("/api/unrec/pile-action?dry_run=true", body)).n; } catch (e) { return toast(e.message); }
  if (!n) return toast("這一堆已經沒有圖了");
  const what = { skip_tag: "標成「不用整理」，並加上「梗圖」標籤", skip: "標成「不用整理」（留在圖庫，不再出現在未識別）" }[action];
  modal(`<h3>${esc(pile[1])}：${fmtN(n)} 張</h3><div class="mbody"><p>把這一堆的 <b>${fmtN(n)}</b> 張${what}。</p>
    ${except_ids.length ? `<p>你勾起來的 <b>${except_ids.length}</b> 張會留下來，不會處理。</p>` : `<p class="mute">要留下哪幾張，可以先取消、把它們勾起來再按一次。</p>`}
    <p class="mute">AI 對這些圖的角色候選會拿掉（不是否決，不會影響其他圖）。一次「撤銷」就能全部還原。</p></div>
    <div class="mfoot"><button class="btn" data-close>取消</button><button class="btn primary" id="pa-go">確定</button></div>`, m => {
    $("#pa-go", m).onclick = async () => {
      try { await api("/api/unrec/pile-action", body); } catch (e) { return toast(e.message); }
      closeModal(); clearSel();
      toast(`已處理 ${fmtN(n)} 張${except_ids.length ? `，留下 ${except_ids.length} 張` : ""}`, { label: "撤銷", fn: undo });
      loadSidebar(); render();
    };
  });
}
async function loadBySeries(gen) {
  const el = $("#byseries"); if (!el) return;
  let d; try { d = await api(`/api/unrec/by-series?miss=${S.miss}&nsfw=${apiNsfw()}`); } catch { return; }
  if (gen != null && gen !== S.gen) return;
  const miss = S.miss === "artist" ? "noartist" : "nochar";
  el.innerHTML = (d.length ? d.map(x => `<button data-ser="${esc(x.key)}" title="${esc(x.key)}：到作品頁，只看還沒標${miss === "noartist" ? "作者" : "角色"}的">${esc(x.display)}<b>${fmtN(x.n)}</b></button>`).join("")
    : `<span class="mute" style="font-size:var(--fs1)">沒有標了作品的</span>`);
  $$("[data-ser]", el).forEach(b => b.onclick = () => { S.nextSeriesMiss = miss; go("series", b.dataset.ser); });
}
export async function loadBuckets(gen) {
  if (S.view !== "unrecognized") return;
  loadBySeries(gen);
  let d; try { d = await api(`/api/unrec/summary?miss=${S.miss}&nsfw=${apiNsfw()}`); } catch { return; }
  if (gen != null && gen !== S.gen) return;
  for (const [k, v] of Object.entries(d)) if (typeof v === "number") $$(`[data-ub="${k}"]`).forEach(el => el.textContent = fmtN(v));
  $$("#ubk button[data-b], #piles button[data-b]").forEach(b => {
    const c = (BUCKETS.find(x => x[0] === b.dataset.b) || [0, 0, b.dataset.b])[2];
    b.classList.toggle("zero", !d[c] && b.dataset.b !== "" && !b.classList.contains("on"));
  });
}

/* 只重載下面的圖（篩選條件變了但頁首不用重畫時用，例如作品頁切換角色） */
export function reloadGrid() {
  const gen = ++S.gen;
  S.items = []; S.total = 0; S.done = false; S.layout = null; S.rOffset = 0; S.loading = false; S.loadAll = false;
  S.selected.clear(); S.anchor = null; updateActionBar();
  const c = $("#content"); c.scrollTop = 0;
  c.innerHTML = `<div class="jgrid" id="grid"></div>`;
  c.classList.remove("enter"); void c.offsetWidth; c.classList.add("enter");
  return loadMore(gen);
}


/* ============ 圖庫的分頁：影片、NSFW、重複、作品與標籤、以前排除的、讀取失敗（原本各自佔側欄一格） ============ */
export const LIB_VIEWS = ["all", "videos", "nsfw", "official", "duplicates", "tags", "excluded", "errors"];
export function libTabs() {
  if (!LIB_VIEWS.includes(S.view) || (S.view === "all" && S.q)) return "";
  const c = S.sbCounts || {};
  const tabs = [["all", "全部", c.all], ["videos", "影片", c.videos], ["nsfw", "NSFW", c.nsfw], ["official", "官方截圖", c.official], ["duplicates", "重複", c.duplicates],
    ["tags", "作品與標籤", null], ["excluded", "以前排除的", c.excluded], ["errors", "讀取失敗", c.errors]]
    .filter(([k, , n]) => !["excluded", "errors"].includes(k) || n || S.view === k);
  return `<div class="tabs libtabs" id="libtabs">${tabs.map(([k, l, n]) => `<button data-lv="${k}" class="${k === S.view ? "on" : ""}">${l}${n != null ? `<b>${fmtN(n)}</b>` : ""}</button>`).join("")}</div>`;
}
export function bindLibTabs() { $$("#libtabs button").forEach(b => b.onclick = () => go(b.dataset.lv)); }

/* 快速標籤頁（梗圖、真人…）：AI 覺得還有幾張可能是、還沒標 → 一鍵去看 */
export async function quickHint(gen, head) {
  // 不靠 S.quick（第一次打開頁面時側欄可能還沒載入完）
  let list; try { list = await api(`/api/quicktags?nsfw=${apiNsfw()}`); } catch { return; }
  if (gen !== S.gen) return;
  const i = list.findIndex(x => x.name === S.tag); if (i < 0) return;
  const q = list[i];
  const hm = $(".headmain", head); if (!hm) return;
  hm.insertAdjacentHTML("beforeend", `<div class="sub qhint" title="選圖後按 ${i + 1}（大圖裡也可以）加上／拿掉${q.skip ? "；加上的圖會離開未識別" : ""}">快速標籤 <kbd>${i + 1}</kbd>${q.hint_n ? `　·　AI 覺得還有 ${fmtN(q.hint_n)} 張可能是 <a class="lnk" id="qh-go">看一看 ›</a>` : ""}</div>`);
  const a = $("#qh-go", hm); if (a) a.onclick = () => runSearch(`${q.hint} -tag:${q.name}`);
}
