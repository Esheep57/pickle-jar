// ES module（2026-10）：用到別的檔案的東西都要在這裡 import；瀏覽器會在載入時檢查名字對不對。
import { $, $$, api, closeModal, esc, modal, toast } from "./core.js";
import { loadSidebar } from "./shell.js";
import { undo } from "./actions.js";
/* ============ 整篇 Pixiv 作品逐頁標角色（app/api/work.py）============
   Pixiv 的標籤是整篇共用的：多頁、多角色的作品，搜源會把每個角色都確認到每一頁上。很少發生，所以不改自動流程，
   發生了就在這裡一次整理：每一列一頁、每一欄一個角色，點格子切換「是 ✓／不是 ✕」，Shift+點＝清掉，存一次、撤銷一次。 */
export async function workModal(mid, onSaved) {
  let d; try { d = await api(`/api/pixiv-work/${mid}`); } catch (e) { return toast(e.message); }
  const chars = d.chars.slice();
  const st = new Map();                       // "mid|角色" → 目前（含你改的）狀態
  const orig = new Map();
  let cur = 0;                                // 預覽中的那一頁
  d.pages.forEach(p => chars.forEach(c => { const v = p.cells[c.name]; const k = p.id + "|" + c.name; orig.set(k, v ?? null); st.set(k, v ?? null); }));
  const mark = v => v === 1 ? "✓" : v === -1 ? "✕" : v === 0 ? "?" : "·";
  const m = modal(`<h3>整篇貼文逐頁標角色 <a class="lnk" href="${esc(d.url)}" target="_blank" rel="noopener">Pixiv ${d.pixiv_id} ↗</a></h3>
    <div class="mbody wk"><p class="mute" style="margin-top:0">Pixiv 的標籤是整篇共用的，每一頁都被標上全部角色。點格子切換 <b>✓ 是</b>／<b>✕ 不是</b>（<kbd>Shift</kbd>+點＝清掉），欄位標題的「全 ✓」「全 ✕」整欄一起改；滑到縮圖上看大圖。改好按「存」，一次撤銷就能全部還原。</p>
    <div class="wkwrap"><table class="wktab"><thead></thead><tbody></tbody></table><div class="wkprev"></div></div></div>
    <div class="mfoot"><span class="wkadd"><input id="wk-add" placeholder="＋ 加一個角色欄（輸入名字）" autocomplete="off"><span class="wksug"></span></span><span class="mute" id="wk-n" style="margin-right:auto"></span><button class="btn" data-close>取消</button><button class="btn primary" id="wk-save">存</button></div>`, null, "wide");
  const changed = () => [...st].filter(([k, v]) => v !== orig.get(k) && !(v == null && orig.get(k) == null));
  const draw = () => {
    $("thead", m).innerHTML = `<tr><th>頁</th>${chars.map((c, i) => `<th title="${esc(c.name)}"><div class="wkc">${esc(c.display || c.name)}</div>
      <div class="wkall"><button data-all="${i}" data-v="1">全 ✓</button><button data-all="${i}" data-v="-1">全 ✕</button></div></th>`).join("")}</tr>`;
    $("tbody", m).innerHTML = d.pages.map(p => `<tr data-prev="${p.id}"><td class="wkp"><img src="/t/${p.id}" alt=""><span>${p.page != null ? `p${p.page}` : esc(p.name)}</span></td>
      ${chars.map((c, i) => { const v = st.get(p.id + "|" + c.name); const ch = v !== orig.get(p.id + "|" + c.name) && !(v == null && orig.get(p.id + "|" + c.name) == null);
        return `<td><button class="wkx s${v ?? "n"} ${ch ? "chg" : ""}" data-m="${p.id}" data-c="${i}">${mark(v)}</button></td>`; }).join("")}</tr>`).join("");
    const n = changed().length; $("#wk-n", m).textContent = n ? `改了 ${n} 格` : "";
    $$("tbody tr", m).forEach(tr => tr.classList.toggle("on", +tr.dataset.prev === cur));
  };
  draw();
  m.addEventListener("click", e => {
    const x = e.target.closest(".wkx");
    if (x) { const k = x.dataset.m + "|" + chars[+x.dataset.c].name, v = st.get(k); st.set(k, e.shiftKey ? (orig.get(k) == null ? null : 0) : v === 1 ? -1 : 1); return draw(); }
    const a = e.target.closest("[data-all]");
    if (a) { const c = chars[+a.dataset.all]; d.pages.forEach(p => st.set(p.id + "|" + c.name, +a.dataset.v)); return draw(); }
  });
  // 預覽：滑到哪一列（不只縮圖）就看那一頁；預覽框和角色名稱列都黏在畫面上，捲到後面的頁也看得到（使用者回報：以前都留在最上面）
  const preview = id => {
    if (!id || +id === cur) return;
    cur = +id;
    const p = d.pages.find(x => x.id === cur);
    $(".wkprev", m).innerHTML = `<img src="/t/${cur}?size=l" alt=""><div class="mute">${p && p.page != null ? `p${p.page}` : esc(p?.name || "")}</div>`;
    $$("tbody tr", m).forEach(tr => tr.classList.toggle("on", +tr.dataset.prev === cur));
  };
  m.addEventListener("mouseover", e => { const tr = e.target.closest("tr[data-prev]"); if (tr) preview(tr.dataset.prev); });
  preview(d.pages[0]?.id);
  const inp = $("#wk-add", m), sug = $(".wksug", m);
  const addCol = t => {
    if (!chars.some(c => c.name === t.name)) { chars.push({ name: t.name, display: t.display || t.zh || t.name }); d.pages.forEach(p => { orig.set(p.id + "|" + t.name, null); st.set(p.id + "|" + t.name, null); }); }
    inp.value = ""; sug.innerHTML = ""; draw();
  };
  let tmr = 0;
  inp.addEventListener("keydown", e => { e.stopPropagation(); if (e.key === "Enter") { const b = $("button", sug); if (b) b.click(); } else if (e.key === "Escape") { inp.value = ""; sug.innerHTML = ""; } });
  inp.addEventListener("input", () => { clearTimeout(tmr); tmr = setTimeout(async () => {
    const q = inp.value.trim(); if (!q) { sug.innerHTML = ""; return; }
    const r = await api(`/api/tags?category=character&limit=8&q=${encodeURIComponent(q)}`).catch(() => []);
    sug.innerHTML = r.map((t, i) => `<button data-i="${i}">${esc(t.display || t.zh || t.name)}${(t.display || t.zh) ? ` <span class="mute">${esc(t.name)}</span>` : ""}</button>`).join("");
    $$("button", sug).forEach(b => b.onclick = () => addCol(r[+b.dataset.i]));
  }, 150); });
  $("#wk-save", m).onclick = async () => {
    const ch = changed().map(([k, v]) => { const [id, ...rest] = k.split("|"); return [+id, rest.join("|"), v == null ? 0 : v]; });
    if (!ch.length) return closeModal();
    try { await api("/api/pixiv-work/apply", { changes: ch }); } catch (e) { return toast(e.message); }
    closeModal(); toast(`已更新 ${ch.length} 格`, { label: "撤銷", fn: undo }); loadSidebar(); onSaved?.();
  };
}
