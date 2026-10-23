// ES module（2026-10）：用到別的檔案的東西都要在這裡 import；瀏覽器會在載入時檢查名字對不對。
import { $, $$, S, fmtN, toast } from "./core.js";
import { loadAllItems } from "./grid.js";
import { updateFooter } from "./filters.js";
import { updateActionBar } from "./actions.js";
import { openViewer } from "./viewer.js";
/* ============ 選取（點擊 / 拖曳 / Shift 範圍） ============ */
let drag = null;
// 手機／平板：點一下＝看大圖（滑鼠是點一下選取、點兩下看大圖）；要選取點左上角的圈圈
let lastTouch = 0;
$("#content").addEventListener("touchstart", () => { lastTouch = Date.now(); }, { passive: true });
$("#content").addEventListener("mousedown", e => {
  if (e.button !== 0) return;
  const cell = e.target.closest(".cell:not(.fu)"); if (!cell) return;
  e.preventDefault();
  const id = +cell.dataset.id;
  if (S.remote || Date.now() - lastTouch < 800) {
    if (S.remote || !e.target.closest(".sel")) { openViewer(cell.dataset.mid ? +cell.dataset.mid : id); return; }
    setSel(id, !S.selected.has(id)); S.anchor = id; return;
  }
  if (e.shiftKey && S.anchor != null) { rangeSelect(S.anchor, id); return; }
  const on = !S.selected.has(id);
  drag = { on, start: id, moved: false };
  setSel(id, on); S.anchor = id;
});
$("#content").addEventListener("mouseover", e => {
  const cell = e.target.closest(".cell:not(.fu)");
  S.hover = cell ? +cell.dataset.id : null;
  if (!drag || !cell) return;
  drag.moved = true;
  setSel(+cell.dataset.id, drag.on);
});
window.addEventListener("mouseup", () => { drag = null; });
// 滑鼠離開圖片區（移到側欄、選取列、頂列）：不再「指著」任何一張。以前會留著最後指過的那張，
// 這時按數字鍵會把快速標籤加到一張你已經沒在看的圖上
$("#content").addEventListener("mouseleave", () => { S.hover = null; });
$("#content").addEventListener("dblclick", e => {
  const cell = e.target.closest(".cell:not(.fu)"); if (!cell) return;
  const id = +cell.dataset.id;
  setSel(id, false);
  openViewer(cell.dataset.mid ? +cell.dataset.mid : id);
});
export function setSel(id, on) {
  if (on) S.selected.add(id); else S.selected.delete(id);
  $$(`.cell[data-id="${id}"]`).forEach(c => c.classList.toggle("selected", on));
  updateActionBar(); updateFooter();
}
function rangeSelect(a, b) {
  const ids = S.layout ? S.items.map(x => x.id) : $$("#content .cell:not(.fu)").map(c => +c.dataset.id);
  let i = ids.indexOf(a), j = ids.indexOf(b); if (i < 0 || j < 0) return;
  if (i > j) [i, j] = [j, i];
  for (let k = i; k <= j; k++) setSel(ids[k], true);
}
export async function selectAll() {
  // 「全選」＝這一頁的全部，不是只有已經載入的那幾百張
  if (S.layout && !S.done && S.items.length < S.total) { toast(`載入全部 ${fmtN(S.total)} 張…`); if (!(await loadAllItems())) return; }
  const ids = S.layout ? S.items.map(x => x.id) : $$("#content .cell:not(.fu)").map(c => +c.dataset.id);
  const all = ids.every(id => S.selected.has(id));
  ids.forEach(id => { if (all) S.selected.delete(id); else S.selected.add(id); });
  $$("#content .cell:not(.fu)").forEach(c => c.classList.toggle("selected", S.selected.has(+c.dataset.id)));
  updateActionBar(); updateFooter();
}
export function clearSel() { [...S.selected].forEach(id => setSel(id, false)); }
