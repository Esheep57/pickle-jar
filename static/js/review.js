// ES module（2026-10）：用到別的檔案的東西都要在這裡 import；瀏覽器會在載入時檢查名字對不對。
import { $, $$, api, apiNsfw, ask, catName, esc, fmtN, lastEvent, modalOpen, onEvent, PAGE, S, srcBadge, sseOk, store, svg, TIER, toast, typeLabel } from "./core.js";
import { go, loadSidebar } from "./shell.js";
import { bindMtype, mtypeSelect, render, tagDelete } from "./grid.js";
import { bindEmpty, emptyHTML, updateFooter } from "./filters.js";
import { selectAll } from "./selection.js";
import { confirmTrash, undo, updateActionBar } from "./actions.js";
import { pickTag } from "./picker.js";
import { openViewer } from "./viewer.js";
import { renderBatch } from "./batch.js";
/* ============ 待確認佇列 ============ */
export async function renderReview(gen) {
  $("#crumb").innerHTML = `<b>待確認</b>`;
  S.rtier = S.rtier || store.get("rtier", "source");
  S.rcat = S.rcat || store.get("rcat", "all");
  S.rtag = S.rtag ?? store.get("rtag", "");
  S.rmode = S.rmode || store.get("rmode", "focus");
  const focus = S.rmode === "focus";
  RF.who = null; RF.i = 0;
  $("#head").innerHTML = `<div><h1>待確認</h1>
    <div class="sub">自動找到的候選，按「是」才算數。<span class="info" tabindex="0" title="找到出處的（Danbooru、e621、Rule34、圖上簽名）最可信，其次 Pixiv；「你的圖庫」是跟你確認過的圖很像的推薦（WD Tagger 不認得的角色也有）；最後是 WD Tagger。&#10;可以在右上角選一個角色專心確認，旁邊有已確認的參考圖對照。">ⓘ</span></div>
    ${focus ? "" : `<div class="tabs" id="rtabs">${[["source", "找到出處"], ["pixiv", "Pixiv"], ["prompt", "生成提示詞"], ["library", "你的圖庫"], ["ai", "AI 模型"], ["all", "全部"]].map(([k, l]) => `<button data-t="${k}" class="${k === S.rtier ? "on" : ""}">${l}<b data-rc="${k}"></b></button>`).join("")}</div>
`}<div class="srcchips" id="srcchips"></div></div>`;
  const modeSeg = `<span class="seg" id="rmode" title="整批看：AI 高分的同名候選一面看完、只挑錯的；專注：一次一個角色、一張一張看；網格：一次看很多張、可以多選">${[["batch", "整批看"], ["focus", "專注"], ["grid", "網格"]].map(([k, l]) => `<button data-rm="${k}" class="${k === S.rmode ? "on" : ""}">${l}</button>`).join("")}</span>`;
  const headTabs = sub => `<div><h1>待確認</h1>${sub ? `<div class="sub">${sub}</div>` : ""}<div class="pagetabs">${modeSeg}</div></div>`;
  if (S.rmode === "batch") {
    $("#head").innerHTML = headTabs("AI 高分的候選，同一個名字一面看完，只挑錯的");
    await renderBatch(gen, "");
    $$("#rmode button").forEach(b => b.onclick = () => { S.rmode = b.dataset.rm; store.set("rmode", S.rmode); render(); });
    return;
  }
  $("#toolbar").className = "qtb";
  // 頁內分頁（整批看／專注／網格＋網格的來源分頁）在標題下；網格的篩選在底部查詢列，全選、大小在 ≡（跟圖庫一樣）
  if (focus) $("#head").innerHTML = headTabs("");
  else { const tabs = $("#rtabs")?.outerHTML || "", chips = $("#srcchips")?.outerHTML || ""; $("#head").innerHTML = headTabs(""); $("#head .pagetabs").insertAdjacentHTML("beforeend", tabs); $("#head > div").insertAdjacentHTML("beforeend", chips); }
  $("#toolbar").innerHTML = focus ? `<div class="qrow2 rq2tb"><span style="flex:1"></span><span class="knnlive" id="knnlive"></span></div>`
    : `<div class="qrow2"><div class="qbox" id="qbox">${svg("search", 16)}<span class="qtok scope"><i>只看</i></span><select id="rtag" title="一次只確認一個角色"><option value="">所有角色／創作者</option></select><select id="rcat">${[["all", "全部類別"], ["character", "角色"], ["artist", "創作者"], ["copyright", "作品"]].map(([k, l]) => `<option value="${k}" ${k === S.rcat ? "selected" : ""}>${l}</option>`).join("")}</select>
    ${mtypeSelect()}</div><span class="knnlive" id="knnlive"></span>
    <span class="moreq"><button class="qic" id="rv-more" title="全選、縮圖大小" aria-haspopup="true">${svg("menu", 17)}</button><span class="popmenu hidden" id="rv-pop">
      <div class="pmrow"><span>大小</span><input type="range" id="size" min="90" max="320" step="10" value="${S.size}" title="縮圖大小"></div>
      <button class="ghost" id="selall">全選 <kbd>Ctrl A</kbd></button></span></span></div>`;
  $("#rv-more")?.addEventListener("click", e => { e.stopPropagation(); $("#rv-pop").classList.toggle("hidden"); });
  $$("#rmode button").forEach(b => b.onclick = () => { S.rmode = b.dataset.rm; store.set("rmode", S.rmode); render(); });
  $$("#rtabs button").forEach(b => b.onclick = () => { S.rtier = b.dataset.t; store.set("rtier", S.rtier); render(); });
  if (!focus) {
    $("#rcat").onchange = e => { S.rcat = e.target.value; store.set("rcat", S.rcat); render(); };
    $("#rtag").onchange = e => { S.rtag = e.target.value; store.set("rtag", S.rtag); render(); };
    bindMtype();
    $("#selall").onclick = selectAll;
    $("#size").oninput = e => { S.size = +e.target.value; document.documentElement.style.setProperty("--size", S.size + "px"); store.set("size", S.size); };
  }
  $("#content").innerHTML = focus ? `<div class="rfocus" id="rfocus"><aside class="rqueue" id="rqueue"></aside><section class="rstage" id="rstage"><div class="empty">載入中…</div></section></div><div class="grid review hidden" id="grid"></div>`
    : `<div id="confbar"></div><div id="refs"></div><div class="grid review" id="grid"></div>`;
  if (!focus) { if (S.rmin) filterBar(); else if (S.rtier === "ai" || S.rtier === "all") confidenceBar(gen); }
  startLive(gen);
  await loadReview(gen);
}
export function reviewParams() {
  if (S.rmode === "focus") return new URLSearchParams({ nsfw: apiNsfw(), tier: "all", category: "all", offset: S.rOffset || 0, limit: PAGE, tag: S.ftag || "",
    nobatch: store.get("batchMin", 0.9) });   // 整批看那些（AI 高分）不在這裡重複出現
  return new URLSearchParams({ nsfw: apiNsfw(), tier: S.rtier, category: S.rcat, mtype: S.mtype || "", offset: S.rOffset || 0, limit: PAGE, tag: S.rtag || "", src: S.rtier === "source" ? (S.rsrc || "") : "",
    min: S.rmin || 0, ctx: S.rctx || "", seg: S.rseg || "" });
}
export async function loadReview(gen) {
  if (S.loading === S.gen || S.done || !$("#grid")) return;
  S.loading = gen;   // 記下是哪一次畫面在載入：切換頁面後舊的請求不會把新頁面卡住
  try {
    const d = await api("/api/review?" + reviewParams());
    if (gen !== S.gen) return;
    if (!S.rOffset && d.knn_version != null) LIVE.v = d.knn_version;
    // 之前選的來源篩選（例如 e621）已經全部處理完 → 自動回到「全部」，不然篩選列會藏起來、畫面空白又關不掉
    if (S.rtier === "source" && S.rsrc && !(d.src_counts || {})[S.rsrc] && !S.rOffset) { S.rsrc = ""; S.loading = false; return render(); }
    if (S.rmode === "focus" && !S.rOffset) {
      S.rByTag = d.by_tag; S.rRefs = d.refs; S.rTagDisplay = d.tag_display;
      // 還沒選角色，或選的角色已經確認完 → 換到候選最多的那一個
      if (!S.ftag || (!d.total && d.by_tag.length && !d.by_tag.some(t => t.name === S.ftag))) {
        const nx = d.by_tag.find(t => t.name !== S.ftag);
        if (nx) { S.ftag = nx.name; S.loading = false; return render(); }
      }
    }
    S.total = d.total;
    for (const [k, v] of Object.entries(d.counts)) $$(`[data-rc="${k}"]`).forEach(el => el.textContent = v);
    const all = $('[data-rc="all"]'); if (all) all.textContent = Object.values(d.counts).reduce((a, b) => a + b, 0);
    for (const k of ["pixiv", "prompt", "library"]) { const b = $(`#rtabs [data-t="${k}"]`); if (b) b.style.display = d.counts[k] || S.rtier === k ? "" : "none"; }   // 沒有候選就不佔位置
    // 這個分頁是空的（而且沒有另外篩選）→ 自動跳到第一個有東西的分頁，不要一打開就是空的
    if (!d.total && !S.rOffset && !S.rtag && !S.rsrc && !S.rmin && S.rcat === "all" && !S.mtype && S.rtier !== "all") {
      const nx = ["source", "pixiv", "library", "ai"].find(k => d.counts[k]);
      if (nx && nx !== S.rtier) { S.rtier = nx; store.set("rtier", nx); S.loading = false; return render(); }
    }
    const chips = $("#srcchips");
    if (chips && !S.rOffset) {
      const SN = { danbooru: "Danbooru", online: "Danbooru", e621: "e621", rule34: "Rule34", signature: "圖上簽名", artist_scan: "從畫師反推", pixiv: "Pixiv" };
      const ent = Object.entries(d.src_counts || {}).filter(([, n]) => n);
      chips.innerHTML = S.rtier === "source" && (ent.length > 1 || S.rsrc) ? [["", "全部", ent.reduce((a, [, n]) => a + n, 0)], ...ent.map(([k, n]) => [k, SN[k] || k, n])]
        .map(([k, l, n]) => `<button data-src="${esc(k)}" class="${(S.rsrc || "") === k ? "on" : ""}">${esc(l)} <b>${fmtN(n)}</b></button>`).join("") : "";
      $$("button", chips).forEach(b => b.onclick = () => { S.rsrc = b.dataset.src; render(); });
    }
    if (!S.rOffset) {
      const sel = $("#rtag");
      if (sel) {
        const opts = d.by_tag.slice();
        if (S.rtag && !opts.some(o => o.name === S.rtag)) opts.unshift({ name: S.rtag, display: d.tag_display || S.rtag, n: 0, category: "character" });
        sel.innerHTML = `<option value="">所有角色／創作者</option>` + opts.map(o => `<option value="${esc(o.name)}" ${o.name === S.rtag ? "selected" : ""}>${esc(o.display)}${o.display !== o.name ? ` (${esc(o.name)})` : ""} · ${o.n}</option>`).join("");
      }
      const refs = $("#refs");
      if (refs) refs.innerHTML = S.rtag ? `<div class="refbar"><div class="reft"><b>${esc(d.tag_display || S.rtag)}</b> 已確認的參考圖 <span class="mute">${d.refs.length ? "（跟候選對照看：髮型、瞳色、配件）" : "— 還沒有已確認的圖，先確認最有把握的幾張"}</span>
          <a class="lnk" data-goto-tag="${esc(S.rtag)}">角色頁 ›</a><a class="lnk" id="rtag-x">看全部候選</a></div>
          <div class="refstrip">${d.refs.map(m => `<img src="/t/${m}" data-ref="${m}" loading="lazy">`).join("")}</div></div>` : "";
      if (refs && S.rtag) {
        $$("[data-ref]", refs).forEach(im => im.ondblclick = im.onclick = () => openViewer(+im.dataset.ref));
        $("[data-goto-tag]", refs).onclick = () => go("tag", S.rtag);
        $("#rtag-x", refs).onclick = () => { S.rtag = ""; store.set("rtag", ""); render(); };
      }
    }
    const start = S.items.length;
    S.items.push(...d.items);
    S.rOffset = (S.rOffset || 0) + d.items.length;
    if (d.items.length < PAGE) S.done = true;
    $("#grid").insertAdjacentHTML("beforeend", d.items.map((it, i) => reviewCell(it, start + i)).join(""));
    if (S.rmode === "focus") { updateFooter(); drawFocus(); return; }
    if (!S.items.length) { $("#grid").outerHTML = emptyHTML(S.rtier === "source" ? "找到出處的候選都確認完了。<br>到「未識別」頁按「找出處」再找一批，或選取圖片按 S。" : "這裡沒有待確認的候選"); bindEmpty(gen); }
    updateFooter();
  } catch (e) { toast("載入失敗：" + e.message); }
  finally { if (S.loading === gen) S.loading = false; }
  const c = $("#content");
  if (!S.done && gen === S.gen && $("#grid") && c.scrollHeight <= c.clientHeight + 200) loadReview(gen);
}
function reviewCell(it, i) {
  const cls = ["cell", "rc"]; if (it.nsfw) cls.push("nsfw"); if (S.selected.has(i)) cls.push("selected");
  const mt = typeLabel(it);
  const refs = (it.refs || []).length ? `<span class="krefs" title="跟你確認過的這幾張很像">${it.refs.slice(0, 3).map(m => `<img src="/t/${m}" loading="lazy" alt="">`).join("")}</span>` : "";
  return `<div class="${cls.join(" ")}" data-id="${i}" data-mid="${it.id}"><img loading="lazy" src="/t/${it.id}" onerror="this.parentNode.classList.add('missing-thumb')"><span class="sel"></span>${refs}
    ${mt || it.kind === "video" ? `<span class="badge">${it.kind === "video" ? "▶ " : ""}${mt}</span>` : ""}
    <span class="qa"><button data-q="1" title="是">✓</button><button data-q="0" title="不是">✕</button></span>
    <div class="rcap" title="${esc([it.display && it.display !== it.tag ? `${it.display}（${it.tag}）` : it.tag, it.reason || TIER[it.tier] || ""].filter(Boolean).join("\n"))}">
      <div class="r1"><b>${esc(it.display || it.tag)}</b>${it.category !== "character" ? `<span class="rcat">${catName(it.category)}</span>` : ""}</div>
      <div class="r2">${srcBadge({ ...it, state: 0 })}<span class="why">${esc(it.reason || TIER[it.tier] || "")}</span></div></div>
    ${it.score != null || it.tier <= 2 ? `<span class="rconf" style="width:${Math.round((it.score != null ? it.score : 1) * 100)}%"></span>` : ""}</div>`;
}
$("#content").addEventListener("mousedown", e => {
  const q = e.target.closest(".qa button"); if (!q) return;
  e.stopPropagation(); e.preventDefault();
  decideReview([+q.closest(".cell").dataset.id], q.dataset.q === "1");
}, true);
export async function decideReview(idx, accept) {
  const pairs = idx.map(i => S.items[i]).filter(Boolean).map(it => [it.id, it.tag]);
  if (!pairs.length) return;
  const r = await api("/api/review/decide", { pairs, accept });
  // 多人圖：確認了一個角色、但 WD 看到的人比已標的角色多 → 原地留一張「還有誰？」卡片，不要就這樣消失
  const fu = new Map((r.followup || []).map(f => [f.id, f]));
  const anchor = {};
  idx.forEach(i => { const it = S.items[i]; if (it && fu.has(it.id) && !anchor[it.id]) anchor[it.id] = $(`.cell[data-id="${i}"]`); });
  for (const [mid, el] of Object.entries(anchor)) if (el && !$(`.fu[data-mid="${mid}"]`)) el.insertAdjacentHTML("beforebegin", crowdCard(fu.get(+mid)));
  bindCrowdCards();
  idx.forEach(i => { $$(`.cell[data-id="${i}"]`).forEach(c => c.remove()); S.selected.delete(i); });
  S.rOffset = Math.max(0, (S.rOffset || 0) - pairs.length);  // 已處理的會從佇列消失
  S.total = Math.max(0, S.total - pairs.length);
  toast(`${accept ? "已確認" : "已否決"} ${pairs.length} 筆` + (r.propagated ? `，並移除 ${r.propagated} 張相似圖上的同一候選` : ""), { label: "撤銷", fn: undo });
  updateActionBar(); updateFooter(); loadSidebar();
  if (!$("#grid .cell")) render();
}


// WD Tagger 的高信心角色候選：先檢查再確認。
// 你只確認了對的、梗圖和日常截圖（剛好有角色）還沒處理 → 不能盲目全部確認；先分成「像插畫」和「可能是梗圖／截圖」讓你看過
async function confidenceBar(gen) {
  let d; try { d = await api(`/api/review/confidence?nsfw=${apiNsfw()}`); } catch { return; }
  if (gen !== S.gen) return;
  const bar = $("#confbar"); if (!bar) return;
  const pct = x => (x * 100).toFixed(x > 0.995 ? 1 : 0);
  const rows = [["anime", "", d.levels], ["furry", "furry 圖", d.furry]].map(([seg, name, lvs]) => {
    const lv = (lvs || []).find(l => l.recommend);
    if (lv) return `<div class="confbar"><div><b>${fmtN(lv.pending)} 筆${name ? ` ${name}` : ""} WD Tagger 角色候選信心 ≥ ${Math.round(lv.min * 100)}%</b>
        <span class="mute">你確認過同樣分數以上的 ${fmtN(lv.confirmed + lv.rejected)} 筆裡 ${pct(lv.precision)}% 是對的。其中 ${fmtN(lv.clean)} 筆像插畫、${fmtN(lv.meme)} 筆可能是梗圖／截圖</span></div>
      <div class="cbtns">${lv.clean ? `<button class="btn primary sm" data-f="clean" data-seg="${seg}" data-min="${lv.min}">檢查 ${fmtN(lv.clean)} 筆插畫</button>` : ""}${lv.meme ? `<button class="btn sm" data-f="meme" data-seg="${seg}" data-min="${lv.min}">看 ${fmtN(lv.meme)} 筆梗圖／截圖</button>` : ""}</div></div>`;
    const top = (lvs || [])[0];
    if (seg === "furry" && top && top.pending) {
      const n = top.confirmed + top.rejected;
      return `<div class="confbar mute"><div><b>${fmtN(top.pending)} 筆 furry 圖的高信心候選另外算</b>
        <span>AI 是用動漫圖訓練的，furry 圖上準不準要看你自己的紀錄：目前 ${fmtN(n)} 筆${n ? `（${pct(top.precision)}% 對）` : ""}，累積 50 筆以上、98% 以上才會出現批次確認</span></div></div>`;
    }
    return "";
  }).join("");
  bar.innerHTML = rows;
  $$("[data-f]", bar).forEach(b => b.onclick = () => { S.rmin = +b.dataset.min; S.rctx = b.dataset.f; S.rseg = b.dataset.seg; S.rtier = "ai"; render(); });
}

// 篩選中：說明要做什麼＋「全部確認剩下的」
function filterBar() {
  const bar = $("#confbar"); if (!bar) return;
  const clean = S.rctx === "clean";
  bar.innerHTML = `<div class="confbar on"><div><b>信心 ≥ ${Math.round(S.rmin * 100)}% 的 AI 角色候選 · ${clean ? "像插畫的" : "可能是梗圖／截圖的"}</b>
      <span class="mute">${clean ? "往下看一遍：不想標角色的（梗圖、日常截圖）選起來按 <kbd>L</kbd> 不用整理；認錯的按 <kbd>⌫</kbd>。剩下的沒問題就按右邊一次確認。"
        : "角色有認對、但不想標的，全選按 <kbd>L</kbd> 不用整理（之後 AI 不會再推薦這些圖的角色，也不會影響其他圖）；想標的按 <kbd>↵</kbd>。"}</span></div>
    <div class="cbtns">${clean ? `<button class="btn primary sm" id="f-all">確認剩下的全部</button>` : ""}<button class="btn sm" id="f-x">離開篩選</button></div></div>`;
  $("#f-x", bar).onclick = () => { S.rmin = 0; S.rctx = ""; S.rseg = ""; render(); };
  const fa = $("#f-all", bar); if (fa) fa.onclick = async () => {
    if (!await ask(`確認剩下的 ${S.total} 個？`, "可以用「撤銷」復原。", "全部確認")) return;
    try { const r = await api("/api/review/confirm-confident", { min_score: S.rmin, nsfw: apiNsfw(), segment: S.rseg || "anime", ctx: "clean", reviewed: true });
      toast(`已確認 ${r.confirmed} 筆`, { label: "撤銷", fn: undo }); S.rmin = 0; S.rctx = ""; loadSidebar(); render(); } catch (e) { toast(e.message); }
  };
}

// 加了梗圖、截圖（不認角色的標籤）：這幾張圖的候選後端已經拿掉 → 待確認頁上也收掉，專注模式直接看下一張
export function dropImages(mids) {
  const set = new Set(mids);
  if (S.rmode === "focus") {
    const cur = RF.cur;
    const before = S.items.length;
    S.items = S.items.filter(x => !set.has(x.id));
    const n = before - S.items.length;
    if (!n) return;
    if (cur && set.has(cur.id)) RF.who = null;
    RF.i = Math.min(RF.i, Math.max(0, S.items.length - 1));
    S.total = Math.max(0, S.total - n); S.rOffset = Math.max(0, (S.rOffset || 0) - n);
    updateFooter(); drawFocus(); return;
  }
  let n = 0;
  $$(".cell.rc").forEach(c => { if (set.has(+c.dataset.mid)) { S.selected.delete(+c.dataset.id); c.remove(); n++; } });
  S.total = Math.max(0, S.total - n); S.rOffset = Math.max(0, (S.rOffset || 0) - n);
  updateActionBar(); updateFooter();
}

/* ---- 多人圖「還有誰？」 ---- */
function crowdCard(f) {
  const have = f.have.map(h => esc(h.display || h.name)).join("、");
  return `<div class="cell rc fu" data-mid="${f.id}"><img loading="lazy" src="/t/${f.id}" data-open="${f.id}" title="看大圖">
    <div class="rcap"><div class="r1"><span class="cat character">多人</span><b>WD 看到 ${f.people} 人</b></div>
      <div class="r2 why" title="已標：${have}">已標：${have}</div>
      ${f.suggest.length ? `<div class="fusug">${f.suggest.map(x => `<button class="chipb" data-fu-add="${esc(x.name)}" title="跟你確認過的圖比，可能是他（參考，約九成準）">＋ ${esc(x.display || x.name)}</button>`).join("")}</div>` : ""}
      <div class="fuact"><button class="btn sm" data-fu-pick>＋ 其他角色</button><button class="btn sm ghost" data-fu-done title="其他人是路人或不認識：不再提醒">不用再找角色</button></div></div></div>`;
}
function bindCrowdCards() {
  $$(".fu:not([data-b])").forEach(card => {
    card.dataset.b = "1";
    const mid = +card.dataset.mid;
    const refresh = async () => {   // 再問一次：還缺人就更新卡片，不缺就收掉
      const d = await api(`/api/media/${mid}`).catch(() => null);
      if (!d || !d.crowd.short) { card.remove(); return; }
      toast(`已標 ${d.crowd.chars} 個角色，WD 看到 ${d.crowd.people} 人`);
    };
    const add = async name => { await api("/api/tags/apply", { ids: [mid], action: "add", tag: name, category: "character" }); toast(`加上 ${name}`, { label: "撤銷", fn: undo }); loadSidebar(); refresh(); };
    $$("[data-fu-add]", card).forEach(b => b.onclick = () => { b.disabled = true; add(b.dataset.fuAdd); });
    $("[data-fu-pick]", card).onclick = () => pickTag({ title: "這張還有誰？", category: "character", ids: [mid] }, t => add(t.name));
    $("[data-fu-done]", card).onclick = async () => { await api("/api/media/people-done", { ids: [mid] }); card.remove(); toast("這張不用再找角色（不再提醒）", { label: "撤銷", fn: undo }); };
    $("img", card).ondblclick = $("img", card).onclick = () => openViewer(mid);
  });
}

/* ============ 專注模式：一次一個角色、一張一張看 ============
   左邊是角色佇列（候選最多的在上面），中間一張大圖＋你確認過的參考圖，按 Enter／⌫／L 決定、自動下一張。
   多人圖確認後原地問「還有誰？」。網格模式照舊（可以多選、批次處理）。 */
export const RF = { i: 0, cur: null, who: null, busy: false };
function drawFocus() {
  const q = $("#rqueue"), st = $("#rstage"); if (!q || !st) return;
  const tags = S.rByTag || [];
  // 角色 → 創作者 → 作品（後端已經照這個順序排）；每類一個小標
  let lastCat = null;
  q.innerHTML = tags.map(t => {
    const h = t.category !== lastCat ? `<div class="rqh">${{ character: "角色", artist: "創作者", copyright: "作品" }[t.category] || catName(t.category)}</div>` : "";
    lastCat = t.category;
    return h + `<button data-ft="${esc(t.name)}" class="${t.name === S.ftag ? "on" : ""}" title="${esc(t.name)}"><span>${esc(t.display || t.name)}</span><i>${t.name === S.ftag ? fmtN(S.total) : fmtN(t.n)}</i></button>`;
  }).join("") + `<div class="rqfoot"><a class="lnk" id="rq-grid">AI 高分的：整批看比較快 ›</a></div>`;
  $$("[data-ft]", q).forEach(b => b.onclick = () => { S.ftag = b.dataset.ft; RF.i = 0; $("#rfocus")?.classList.remove("qopen"); render(); });
  $("#rq-grid", q).onclick = () => { S.rmode = "batch"; store.set("rmode", "batch"); render(); };
  if (RF.i >= S.items.length) RF.i = Math.max(0, S.items.length - 1);
  const it = S.items[RF.i];
  RF.cur = it || null;
  S.hover = it ? RF.i : null;     // 數字鍵（快速標籤）和 Space（大圖）作用在眼前這一張
  const t = tags.find(x => x.name === S.ftag) || {};
  const disp = S.rTagDisplay || t.display || S.ftag || "";
  if (!it) {
    const nx = tags.find(x => x.name !== S.ftag);
    st.innerHTML = !tags.length ? `<div class="empty">沒有待確認的候選了</div>`
      : `<div class="rdone"><h2>${esc(disp)} 這一輪看完了</h2><p class="mute">你的是／不是幾秒後就會回饋給「你的圖庫」推薦，新的推薦會自己出現在這裡。</p>
        ${nx ? `<button class="btn primary" id="rf-next">下一個：${esc(nx.display || nx.name)}（${fmtN(nx.n)}）</button>` : ""}</div>`;
    const b = $("#rf-next", st); if (b) b.onclick = () => { S.ftag = nx.name; RF.i = 0; render(); };
    return;
  }
  const pct = x => x == null ? "" : `${Math.round(x * 100)}%`;
  const SRCN = { model: "WD Tagger", knn: "你的圖庫", danbooru: "Danbooru", online: "Danbooru", pixiv: "Pixiv", e621: "e621", rule34: "Rule34", signature: "圖上簽名", aimeta: "生成提示詞", ccip: "逐人比對", tracemoe: "trace.moe", x: "X", artist_scan: "從畫師反推" };
  const conf = it.score != null ? it.score : it.tier <= 2 ? 1 : null;
  const refs = S.rRefs || [];
  const kindL = { character: "角色", artist: "創作者", copyright: "作品" }[it.category] || catName(it.category);
  st.innerHTML = `<div class="rprog"><i style="width:${S.total ? Math.max(2, 100 * RF.i / Math.max(1, S.total)) : 0}%"></i></div>
    <div class="rq2">
      <div class="rq2img rimg ${it.nsfw ? "nsfw" : ""}" data-open="${it.id}" title="看大圖（Space）"><img src="/t/${it.id}?size=l" alt=""></div>
      <div class="rq2side">
        <div class="rq2kind"><button class="lnkb" id="rq-toggle" title="換一個名字確認（${tags.length} 個名字有候選）">${esc(kindL)} ▾</button><span class="mono">${RF.i + 1} / ${fmtN(S.total)}</span></div>
        <h1 class="rq2q">這是<br><span class="rq2name" tabindex="0">${esc(disp)}${refs.length ? `<span class="rq2peek">${refs.slice(0, 6).map(m => `<img src="/t/${m}" data-ref="${m}" loading="lazy" alt="">`).join("")}<em>你確認過的</em></span>` : ""}</span> 嗎？</h1>
        <div class="rq2why rmeta" title="${esc(it.reason || "")}">${esc(SRCN[it.source] || TIER[it.tier] || it.source || "")}${it.score != null ? ` ${pct(it.score)}` : ""}${it.reason && !/^AI 模型/.test(it.reason) ? `　${esc(it.reason)}` : ""}${refs.length ? "" : `　<span class="mute">還沒有確認過的圖</span>`}</div>
        <div id="rwho"></div>
        <div class="ract rq2keys" id="ract">
          <div class="rbtns"><button class="btn primary big" data-rf="yes">是 <kbd>Enter</kbd></button><button class="btn big" data-rf="no" title="AI 認錯了：會記住，相似的圖也不再推薦">不是 <kbd>⌫</kbd></button><button class="btn big" data-rf="skip" title="角色沒認錯、但這張不想標（梗圖、日常截圖）：不會教 AI「這不是他」">不用整理 <kbd>L</kbd></button></div>
        </div>
        <div class="rq2more rlinks"><button class="lnkb" data-rf="replace">換成 <kbd>C</kbd></button><button class="lnkb" data-rf="also">＋角色 <kbd>A</kbd></button><button class="lnkb" data-rf="open">大圖 <kbd>Space</kbd></button>${RF.i > 0 ? `<button class="lnkb" data-rf="back">上一張</button>` : ""}<a class="lnk" data-goto-tag="${esc(S.ftag)}">${it.category === "copyright" ? "作品頁" : it.category === "artist" ? "創作者頁" : "角色頁"} ›</a>${it.category === "copyright" ? `<a class="lnk danger" id="rf-del" title="不是作品（例如 Twitter、YouTube）：從所有圖上拿掉，以後也不自動標">刪除這個作品…</a>` : ""}</div>
      </div>
    </div>`;
  $("#rq-toggle", st).onclick = e => { e.stopPropagation(); $("#rfocus")?.classList.toggle("qopen"); };
  $$("[data-rf]", st).forEach(b => b.onclick = () => focusAct(b.dataset.rf));
  $$("[data-ref]", st).forEach(im => im.onclick = () => openViewer(+im.dataset.ref));
  $("[data-open]", st).onclick = () => openViewer(it.id);
  $("[data-goto-tag]", st).onclick = () => go(it.category === "copyright" ? "series" : "tag", S.ftag);
  const rfd = $("#rf-del", st); if (rfd) rfd.onclick = () => tagDelete({ name: S.ftag, display: disp, category: "copyright" }, { after: () => { S.ftag = ""; RF.i = 0; render(); } });
  if (RF.who) drawWho();
  // 快看完了 → 先載下一批
  if (!S.done && RF.i + 8 > S.items.length && S.loading !== S.gen) loadReview(S.gen);
}
function focusAdvance() {
  S.items.splice(RF.i, 1); S.total = Math.max(0, S.total - 1); S.rOffset = Math.max(0, (S.rOffset || 0) - 1);
  RF.who = null; loadSidebar(); updateFooter(); drawFocus();
}
async function focusAct(a) {
  const it = RF.cur; if (!it || RF.busy) return;
  if (a === "open") return openViewer(it.id);
  if (a === "trash") return confirmTrash([it.id], () => dropImages([it.id]));
  if (a === "back") { RF.i = Math.max(0, RF.i - 1); RF.who = null; return drawFocus(); }
  if (a === "also") return pickTag({ title: "這張也是…", category: it.category, ids: [it.id] }, async t => {
    await api("/api/tags/apply", { ids: [it.id], action: "add", tag: t.name, category: t.category }); toast(`加上 ${t.name}`, { label: "撤銷", fn: undo }); loadSidebar();
  });
  if (a === "replace") return pickTag({ title: `把 ${it.display || it.tag} 改標成…`, category: it.category, ids: [it.id] }, async t => {
    await api("/api/tags/apply", { ids: [it.id], action: "replace", tag: t.name, from_tag: it.tag, category: t.category });
    toast(`換成 ${t.name}`, { label: "撤銷", fn: undo }); focusAdvance();
  });
  RF.busy = true;
  try {
    if (a === "skip") {
      const r = await api("/api/media/skip", { ids: [it.id], value: true });
      toast(`不用整理（拿掉 ${r.cleared} 個 AI 候選）`, { label: "撤銷", fn: undo });
      // 同一張圖的其他 AI 候選也拿掉了：佇列裡同一張圖的都收掉
      S.items = S.items.filter((x, k) => k === RF.i || x.id !== it.id || !["model", "ccip", "knn"].includes(x.source));
      focusAdvance();
    } else {
      const accept = a === "yes";
      const r = await api("/api/review/decide", { pairs: [[it.id, it.tag]], accept });
      toast(`${accept ? "是" : "不是"} ${it.display || it.tag}` + (r.propagated ? `，並移除 ${r.propagated} 張相似圖上的同一候選` : ""), { label: "撤銷", fn: undo });
      const f = (r.followup || []).find(x => x.id === it.id);
      if (accept && f) { RF.who = f; loadSidebar(); drawWho(); }
      else focusAdvance();
    }
  } catch (e) { toast(e.message); }
  finally { RF.busy = false; }
}
// 多人圖：確認一個之後原地問「還有誰？」，Enter＝標完了
function drawWho() {
  const f = RF.who, box = $("#rwho"), act = $("#ract"); if (!box) return;
  if (!f) { box.innerHTML = ""; act?.classList.remove("hidden"); return; }
  act?.classList.add("hidden");
  box.innerHTML = `<div class="rwho"><b>還有誰？</b> <span class="mute">WD 看到 ${f.people} 人，已標 ${f.have.map(h => esc(h.display || h.name)).join("、")}</span>
    <div class="rwbtns">${f.suggest.map(x => `<button class="btn" data-who-add="${esc(x.name)}" title="跟你確認過的圖比，可能是他（參考）">${esc(x.display || x.name)} <span class="mute">你的圖庫猜的</span></button>`).join("")}
    <button class="btn" data-who-pick>其他角色… <kbd>A</kbd></button><button class="btn primary" data-who-done>不用再找角色 <kbd>Enter</kbd></button></div></div>`;
  const mid = f.id;
  const after = async () => { const d = await api(`/api/media/${mid}`).catch(() => null); if (!d || !d.crowd?.short) focusAdvance(); else { RF.who = { ...f, have: d.tags.filter(t => t.category === "character" && t.state === 1).map(t => ({ name: t.name, display: t.display })) }; drawWho(); } };
  const add = async name => { await api("/api/tags/apply", { ids: [mid], action: "add", tag: name, category: "character" }); toast(`加上 ${name}`, { label: "撤銷", fn: undo }); after(); };
  $$("[data-who-add]", box).forEach(b => b.onclick = () => add(b.dataset.whoAdd));
  $("[data-who-pick]", box).onclick = () => pickTag({ title: "這張還有誰？", category: "character", ids: [mid] }, t => add(t.name));
  $("[data-who-done]", box).onclick = async () => { await api("/api/media/people-done", { ids: [mid] }); toast("這張不用再找角色", { label: "撤銷", fn: undo }); focusAdvance(); };
}
export function focusKey(k, e) {
  if (!RF.cur || modalOpen()) return false;
  if (RF.who) {
    if (k === "enter") { e.preventDefault(); $("#rwho [data-who-done]")?.click(); return true; }
    if (k === "a") { e.preventDefault(); $("#rwho [data-who-pick]")?.click(); return true; }
    return false;
  }
  const m = { enter: "yes", backspace: "no", delete: "trash", l: "skip", c: "replace", a: "also" }[k];
  if (!m) return false;
  e.preventDefault(); focusAct(m); return true;
}

/* ============ 「你的圖庫」推薦即時更新 ============
   你按是／不是之後，後端約 3 秒後重算（也會從你的是／不是重新學習）；這裡每 4 秒問一次版本，
   變了就在原地補上新推薦、拿掉收回的，不用重新整理，也不會換掉你正在看的這一張。 */
export const LIVE = { v: null, timer: null, busy: false };
function startLive(gen) {
  clearInterval(LIVE.timer);
  LIVE.v = null;
  // 事件流連著（b93）：推薦一有變就由伺服器推過來（onEvent knn），不用每 4 秒問；連不上才自己問
  const tick = async (pushed) => {
    if (gen !== S.gen || !$("#grid")) { clearInterval(LIVE.timer); LIVE.off?.(); return; }
    let d = pushed || (sseOk() ? lastEvent.knn : null);
    if (!d) { try { d = await api("/api/knn/live"); } catch { return; } }
    const ind = $("#knnlive");
    if (ind) ind.textContent = d.running || d.scheduled ? "你的圖庫推薦更新中…" : "";
    if (LIVE.v == null) { LIVE.v = d.version; return; }
    if (d.version === LIVE.v || d.running || LIVE.busy || RF.busy || S.loading === gen) return;
    LIVE.busy = true;
    try { if (await softRefresh(gen)) LIVE.v = d.version; } finally { LIVE.busy = false; }
  };
  LIVE.off?.();
  LIVE.off = onEvent("knn", d => tick(d));
  LIVE.timer = setInterval(() => { if (!sseOk()) tick(); }, 4000);     // 事件流連著就等它推（b96）
}
const rkey = it => it.id + "|" + it.tag;
async function softRefresh(gen) {
  const n = Math.max(PAGE, S.items.length + 40);
  const p = reviewParams(); p.set("offset", 0); p.set("limit", n);
  let d; try { d = await api("/api/review?" + p); } catch { return false; }
  if (gen !== S.gen) return false;
  const fresh = new Map(d.items.map(it => [rkey(it), it]));
  const complete = d.items.length < n;          // 拿到的是完整清單才能判斷「收回」
  let added = 0, removed = 0;
  for (const [k, v] of Object.entries(d.counts)) $$(`[data-rc="${k}"]`).forEach(el => el.textContent = v);
  if (S.rmode === "focus") {
    const cur = RF.cur, old = new Set(S.items.map(rkey));
    const items = d.items.slice();
    let i = cur ? items.findIndex(x => rkey(x) === rkey(cur)) : 0;
    if (cur && i < 0) { i = Math.min(RF.i, items.length); items.splice(i, 0, cur); }   // 眼前這張被收回：留著讓你決定
    added = items.filter(x => !old.has(rkey(x))).length;
    removed = complete ? [...old].filter(k => !fresh.has(k) && (!cur || k !== rkey(cur))).length : 0;
    if (!added && !removed) return true;
    S.items = items; RF.i = Math.max(0, i);
    S.total = d.total + (cur && !fresh.has(rkey(cur)) ? 1 : 0);
    S.rOffset = d.items.length; S.done = complete; S.rByTag = d.by_tag;
    if (!RF.who) drawFocus();
  } else {
    const have = new Set();
    S.items.forEach((it, i) => {
      const el = it && $(`.cell[data-id="${i}"]`); if (!el) return;
      if (complete && it.source === "knn" && !fresh.has(rkey(it)) && !S.selected.has(i)) { el.remove(); removed++; }
      else have.add(rkey(it));
    });
    const add = d.items.filter(x => !have.has(rkey(x)));
    const start = S.items.length;
    S.items.push(...add); added = add.length;
    if (add.length) $("#grid")?.insertAdjacentHTML("beforeend", add.map((it, k) => reviewCell(it, start + k)).join(""));
    S.total = d.total; S.rOffset = d.items.length; S.done = complete;
    if (added || removed) updateFooter();
  }
  if (added || removed) toast(`你的圖庫推薦更新：${added ? `新增 ${added} 筆` : ""}${added && removed ? "、" : ""}${removed ? `收回 ${removed} 筆` : ""}`);
  return true;
}
