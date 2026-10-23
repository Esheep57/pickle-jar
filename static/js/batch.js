// ES module（2026-10）：用到別的檔案的東西都要在這裡 import；瀏覽器會在載入時檢查名字對不對。
import { $, $$, S, api, apiNsfw, catName, esc, fmtN, modalOpen, srcBadge, store, toast } from "./core.js";
import { loadSidebar } from "./shell.js";
import { render, tagDelete } from "./grid.js";
import { undo } from "./actions.js";
import { openViewer } from "./viewer.js";
/* ============ 待確認 › 整批看 ============
   AI 高分候選（預設 ≥ 0.9）同一個名字排成一面：預設全部「是」，錯的點一下變「不是」、再點變「不用整理」，Enter 一次送出、跳下一個名字。
   分數低的排前面（最可能錯的先看到）。門檻以下、像梗圖的不在這裡，留給專注模式逐張看。 */
const B = { groups: [], tag: null, items: [], mark: new Map(), hover: null, min: 0.9 };
const NEXT = { yes: "no", no: "skip", skip: "yes" };
const LABEL = { no: "不是", skip: "不用整理" };

export async function renderBatch(gen, modeSeg) {
  B.min = +store.get("batchMin", 0.9);
  $("#toolbar").className = "qtb";
  $("#toolbar").innerHTML = "";
  ($("#head .pagetabs") || $("#toolbar")).insertAdjacentHTML("beforeend", `<span class="mute bminw" title="點一下＝不是、再點＝不用整理；滑鼠指著按 ⌫ 不是、L 不用整理、Space 大圖；Enter 送出">分數 ≥ <select id="bmin">${[0.95, 0.9, 0.85, 0.8].map(v => `<option value="${v}" ${v === B.min ? "selected" : ""}>${v}</option>`).join("")}</select></span>`);
  $("#bmin").onchange = e => { store.set("batchMin", +e.target.value); B.tag = null; render(); };
  $("#content").innerHTML = `<div class="rbatch"><aside class="bqueue" id="bqueue"><div class="mute">載入中…</div></aside><section class="bstage" id="bstage"></section></div>`;
  let d;
  try { d = await api(`/api/review/batch?nsfw=${apiNsfw()}&min=${B.min}`); } catch (e) { return toast(e.message); }
  if (gen !== S.gen) return;
  B.groups = d.groups;
  if (!B.tag || !d.groups.some(g => g.name === B.tag)) B.tag = d.groups[0]?.name || null;
  const q = $("#bqueue");
  q.innerHTML = `<div class="bqhead">${fmtN(d.total)} 筆、${fmtN(d.groups.length)} 個名字</div>
    ${d.groups.map((g, i) => (i === 0 || d.groups[i - 1].category !== g.category ? `<div class="bqcat">${catName(g.category)}</div>` : "")
      + `<button class="bq ${g.name === B.tag ? "on" : ""}" data-bt="${esc(g.name)}"><span class="nm">${esc(g.display || g.name)}</span><b>${g.n}</b></button>`).join("")}
    <div class="bqfoot mute">逐張看（專注模式）：分數低於 ${d.min} 的 ${fmtN(d.below)} 筆、像梗圖／截圖的 ${fmtN(d.memeish)} 筆</div>`;
  $$("[data-bt]", q).forEach(b => b.onclick = () => { B.tag = b.dataset.bt; drawGroup(gen); });
  S.total = d.total;
  $("#foot-l").textContent = `整批看：${fmtN(d.total)} 筆`;
  await drawGroup(gen);
}

async function drawGroup(gen) {
  const st = $("#bstage"); if (!st) return;
  $$("#bqueue [data-bt]").forEach(b => b.classList.toggle("on", b.dataset.bt === B.tag));
  if (!B.tag) { st.innerHTML = `<div class="empty">分數 ≥ ${B.min} 的都看完了。<br><span class="mute">剩下的在「專注」模式逐張看。</span></div>`; return; }
  const g = B.groups.find(x => x.name === B.tag) || { name: B.tag, display: B.tag, refs: [] };
  let d;
  const tag = B.tag;
  try { d = await api(`/api/review/batch/items?tag=${encodeURIComponent(tag)}&nsfw=${apiNsfw()}&min=${B.min}`); } catch (e) { return toast(e.message); }
  if (gen !== S.gen || tag !== B.tag) return;     // 載入中又點了別的名字：舊的回來不要蓋掉新的
  B.items = d.items; B.mark = new Map(); B.hover = null;
  st.innerHTML = `<div class="bhead"><div><h2>${esc(g.display || g.name)}</h2><span class="mute">${esc(g.name)} · ${catName(g.category)} · ${B.items.length} 張（分數低的在前面）${g.category === "copyright" ? ` · <a class="lnk danger" id="b-del" title="不是作品（例如 Twitter、YouTube）：從所有圖上拿掉，以後也不自動標">刪除這個作品…</a>` : ""}</span></div>
      ${g.refs?.length ? `<div class="brefs"><span class="mute">已確認的：</span>${g.refs.map(i => `<img src="/t/${i}" data-ref="${i}">`).join("")}</div>` : `<div class="mute">還沒有確認過的圖可以對照</div>`}</div>
    <div class="bgrid" id="bgrid">${B.items.map((it, i) => `<div class="bc${it.nsfw && S.nsfw !== "show" ? " nsfw" : ""}" data-i="${i}" title="${Math.round(it.score * 100)}% · ${esc(it.reason || "")}">
      <img loading="lazy" src="/t/${it.id}"><span class="bs">${srcBadge(it)} ${Math.round(it.score * 100)}</span><span class="bm"></span></div>`).join("")}</div>
    <div class="bbar"><span id="bsum"></span><span style="flex:1"></span><button class="btn" id="ball-skip" title="這個名字整面都不用整理（例如全是梗圖）">全部不用整理</button><button class="btn primary big" id="bgo">送出 <kbd>Enter</kbd></button></div>`;
  const grid = $("#bgrid");
  grid.onclick = e => { const c = e.target.closest(".bc"); if (c) cycle(+c.dataset.i); };
  grid.onmouseover = e => { const c = e.target.closest(".bc"); B.hover = c ? +c.dataset.i : null; };
  grid.onmouseleave = () => { B.hover = null; };
  $$("[data-ref]", st).forEach(im => im.onclick = () => openViewer(+im.dataset.ref));
  $("#bgo").onclick = () => submit(gen);
  const bd = $("#b-del", st); if (bd) bd.onclick = () => tagDelete({ name: g.name, display: g.display, category: "copyright" }, { after: () => { B.tag = null; render(); } });
  $("#ball-skip").onclick = () => { B.items.forEach((_, i) => B.mark.set(i, "skip")); paint(); };
  paint();
}

function cycle(i, to) {
  const cur = B.mark.get(i) || "yes";
  const nx = to ? (cur === to ? "yes" : to) : NEXT[cur];
  if (nx === "yes") B.mark.delete(i); else B.mark.set(i, nx);
  paint();
}

function paint() {
  $$("#bgrid .bc").forEach(c => {
    const m = B.mark.get(+c.dataset.i);
    c.classList.toggle("no", m === "no"); c.classList.toggle("skip", m === "skip");
    c.querySelector(".bm").textContent = m ? LABEL[m] : "";
  });
  const n = { yes: 0, no: 0, skip: 0 };
  B.items.forEach((_, i) => n[B.mark.get(i) || "yes"]++);
  const s = $("#bsum"); if (s) s.innerHTML = `是 <b>${n.yes}</b>　不是 <b>${n.no}</b>　不用整理 <b>${n.skip}</b>`;
}

async function submit(gen) {
  if (!B.items.length) return;
  const by = { yes: [], no: [], skip: [] };
  B.items.forEach((it, i) => by[B.mark.get(i) || "yes"].push(it.id));
  const tag = B.tag;
  try {
    const r = await api("/api/review/batch/apply", { tag, ...by });
    toast(`${tag}：是 ${r.yes}、不是 ${r.no}、不用整理 ${r.skip}`, { label: "撤銷", fn: undo });
  } catch (e) { return toast(e.message); }
  // 下一個名字：清單裡的下一個（剛送出的這個會消失）
  const idx = B.groups.findIndex(g => g.name === tag);
  B.tag = (B.groups[idx + 1] || B.groups[idx - 1] || {}).name || null;
  loadSidebar();
  render();
}

// shell.js 的鍵盤：整批看模式先問這裡
export function batchKey(k, e) {
  if (modalOpen() || !$("#bgrid")) return false;
  if (k === "enter") { e.preventDefault(); $("#bgo")?.click(); return true; }
  if (B.hover == null) return false;
  if (k === "backspace") { e.preventDefault(); cycle(B.hover, "no"); return true; }
  if (k === "l") { e.preventDefault(); cycle(B.hover, "skip"); return true; }
  if (k === " ") { e.preventDefault(); openViewer(B.items[B.hover].id); return true; }
  return false;
}
