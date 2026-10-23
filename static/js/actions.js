// ES module（2026-10）：用到別的檔案的東西都要在這裡 import；瀏覽器會在載入時檢查名字對不對。
import { $, $$, MTYPES, OFFICIAL, S, api, catName, closeModal, esc, fmtSize, modal, modalOpen, store, svg, toast } from "./core.js";
import { go, loadNsfwIds, loadSidebar, pollOnce, quickTag } from "./shell.js";
import { render, sourceConfirm } from "./grid.js";
import { drawWindow, relayout } from "./layout.js";
import { cellHTML, updateFooter } from "./filters.js";
import { clearSel, selectAll } from "./selection.js";
import { openRerun } from "./steps.js";
import { datasetModal } from "./dataset.js";
import { loadBuckets } from "./unrec.js";
import { pickTag } from "./picker.js";
import { decideReview, dropImages } from "./review.js";
import { albumMenu } from "./albums.js";
import { V, markDirty, openViewer } from "./viewer.js";
import { ascii2dModal, folderBrowser } from "./pages.js";
import { srcUrlModal } from "./srcurl.js";
/* ============ 動作列 ============ */
export function updateActionBar() {
  // 選取列畫錯時，寧可藏起來也不要留著上一頁的按鈕（會按到跟這張圖無關的動作）
  try { _updateActionBar(); } catch (e) { console.error(e); const bar = $("#actionbar"); bar.innerHTML = ""; bar.classList.add("hidden"); }
}
const SHARE_TIP = "複製到剪貼簿，到 QQ、Messenger 的聊天視窗按 Ctrl+V 貼上（原檔；GIF 會動、影片也可以）";
// 選取後的動作：一份清單（按鍵照舊），畫面上只露出這個情境最常用的 3 個，其餘在「輸入動作…」打字或按 ⋯
function actionList() {
  const L = [];
  const add = (a, label, key = "", title = "", extra = {}) => L.push({ a, label, key, title, ...extra });
  (S.quick || []).forEach((q, i) => L.push({ qt: i, a: "qt:" + i, label: q.name, key: String(i + 1), title: `加上／拿掉「${q.name}」${q.skip ? "（同時離開未識別）" : ""}` }));
  if (S.view === "review") {
    add("yes", "是", "Enter", "確認這個候選"); add("no", "不是", "⌫", "AI 認錯了（會記住，相似的圖也不再推薦這個角色）");
    add("rskip", "不用整理", "L", "角色沒認錯，但這張不想標（梗圖、日常截圖）：拿掉 AI 的角色候選、離開待辦，圖留在圖庫");
    add("share", "分享", "Ctrl C", SHARE_TIP);
    add("trash", "刪除", "Delete", "移到資源回收筒（會先問）", { danger: true });
    add("clear", "取消選取", "Esc");
    return L;
  }
  const inTag = S.view === "tag" && S.tag, rejMode = inTag && S.src === "rejected", candMode = inTag && S.src === "candidates";
  if (candMode) { add("confirm", `是 ${S.tag}`, "Enter"); add("reject", "不是", "⌫"); }
  else if (rejMode) { add("unreject", "取消否決"); add("confirm", `其實是 ${S.tag}`); }
  else if (inTag) { add("reject", `移除「${S.tag}」`, "⌫"); add("confirm", "確認", "Enter"); add("replace", "改標成…", "C"); }
  add("also", "＋角色", "A", "加角色（也可以加作品、標籤）");
  add("artist", "＋創作者", "R"); add("original", "原創", "O", "非同人／原創作品，沒有特定角色");
  add("official", "官方…", "K", "動畫、漫畫、電影截圖、小說插圖（都算已有作者）"); add("mediatype", "媒材…", "M", "插畫、Q 版、漫畫、3D、照片…：你標的會教 AI，整庫跟著重算"); add("tags", "＋標籤", "G");
  add("album", "本子…", "B", "同人本、cos 圖包、畫集：建立或加入本子");
  add("nsfw", "NSFW／安全", "N"); add("source", "搜尋來源", "S", "Danbooru / IQDB / Pixiv");
  add("ascii2d", "ascii2d 連續搜", "", "Danbooru 沒有的 X／Pixiv 原創圖：在 ascii2d 一張一張按 Enter 找作者");
  add("srcurl", "貼出處網址…", "", "知道是哪篇 X／Pixiv 貼文：貼網址，比對後記出處、帶上作者和角色");
  add("rerun", "補跑…", "", "重新跑自動標記、搜源的某幾步（WD、PixAI、E-Hentai、e621…）");
  add("dataset", "匯出訓練集…", "", "複製到一個資料夾，每張旁邊一個 .txt 標註（LoRA 訓練用）");
  // 「不用再提醒」只有兩種（2026-10-07）：這張不用整理（L／X）、這項不用再找（作者、角色）
  if (S.view === "unrecognized" && S.bucket === "skipped") add("unskip", "放回待辦", "L", "回到未識別、待確認的待辦");
  else if (S.view === "excluded") add("unexclude", "放回圖庫", "X");
  else add("skip", "不用整理", "L", "梗圖、截圖、日常照片：留在圖庫，但不再出現在未識別、待確認的待辦；AI 的身分候選拿掉（不是「不是」，不會教 AI）");
  add("artistdone", "不用再找作者", "", "表情包、梗圖、轉了好幾手的圖：不再列在「缺創作者」");
  if (S.view === "unrecognized" && S.miss === "people") add("crowddone", "不用再找角色", "", "多人圖的其他人是路人或不認識：不再列在這裡");
  add("upgrade", "換成高清", "", "出處有同一張、比較大的版本才會換（搜尋 is:upgrade 看全部）；舊檔丟資源回收筒，標記全部保留");
  add("upgradeno", "不用換高清", "", "這幾張不換：之後不再提醒（除非找到更大的）");
  add("share", "分享", "Ctrl C", SHARE_TIP);
  add("archive", "移出 iCloud", "", "複製到電腦、刪掉 iCloud 那份（標籤保留）");
  add("trash", "刪除", "Delete", "移到資源回收筒（會先問；紀錄保留 30 天，檔案還原就接回）", { danger: true });
  add("selall", "全選", "Ctrl A"); add("clear", "取消選取", "Esc");
  return L;
}
const actCtx = () => [S.view, S.view === "unrecognized" ? S.miss : "", S.view === "tag" ? S.src || "" : "", /(^|\s)is:upgrade(\s|$)/.test(S.q || "") ? "up" : ""].join(":");
function actDefaults() {
  const v = S.view, inTag = v === "tag" && S.tag;
  if (v === "review") return ["yes", "no", "rskip"];
  if (inTag) return S.src === "candidates" ? ["confirm", "reject", "also"] : S.src === "rejected" ? ["unreject", "confirm", "also"] : ["reject", "confirm", "replace"];
  if (v === "unrecognized") {
    const sk = S.bucket === "skipped" ? "unskip" : "skip";
    return { artist: ["artist", "artistdone", sk], character: ["also", "original", sk], people: ["also", "crowddone", sk] }[S.miss] || ["also", sk, "artist"];
  }
  if (v === "excluded") return ["unexclude", "also", "trash"];
  if (/(^|\s)is:upgrade(\s|$)/.test(S.q || "")) return ["upgrade", "upgradeno", "also"];   // 可換高清的清單
  return ["also", "artist", "source"];
}
export function recordUse(a) {
  if (!a || ["clear", "selall"].includes(a)) return;
  const u = store.get("actUse", {}), k = actCtx(); u[k] = u[k] || {}; u[k][a] = (u[k][a] || 0) + 1; store.set("actUse", u);
}
function topActions(list) {
  // 預設的 3 個先上；你在這個情境按某個動作很多次，它就會擠進前 3（依你自己的紀錄）
  const def = actDefaults(), use = store.get("actUse", {})[actCtx()] || {};
  const score = x => (use[x.a] || 0) + (def.includes(x.a) ? (3 - def.indexOf(x.a)) * 4 : 0);
  return list.filter(x => !["clear", "selall"].includes(x.a)).map(x => [score(x), x]).filter(([sc]) => sc > 0)
    .sort((a, b) => b[0] - a[0]).slice(0, 3).map(([, x]) => x);
}
const actBtn = (x, cls = "") => `<button ${x.qt != null ? `data-qt="${x.qt}"` : `data-a="${x.a}"`} class="${cls}${x.danger ? " danger" : ""}" title="${esc(x.title || "")}">${esc(x.label)}${x.key ? ` <kbd>${esc(x.key)}</kbd>` : ""}</button>`;
function _updateActionBar() {
  const bar = $("#actionbar"); const n = S.selected.size;
  document.body.classList.toggle("selecting", n > 0);     // 選取圈：平常藏起來，滑過或已經在選的時候才出現
  if (!n) { bar.classList.add("hidden"); bar.innerHTML = ""; bar.dataset.ctx = ""; return; }
  bar.classList.remove("hidden");
  const unit = S.view === "review" ? "筆" : "張";
  if (bar.dataset.ctx === actCtx() + "|" + S.gen && $("#cap-in", bar)) {   // 同一頁繼續選圖：只改數字，不要把正在打的字清掉
    $(".count", bar).textContent = `${n} ${unit}已選取`;
    return;
  }
  const list = actionList(), top = topActions(list);
  bar.dataset.ctx = actCtx() + "|" + S.gen;
  bar.innerHTML = `<span class="count">${n} ${unit}已選取</span><span class="sep"></span>
    ${top.map((x, i) => actBtn(x, i === 0 && ["yes", "confirm", "unreject"].includes(x.a) ? "primary" : "")).join("")}
    <div class="capcmd"><input id="cap-in" autocomplete="off" placeholder="輸入動作…（共 ${list.length - 1} 個）" aria-label="輸入動作"><div class="caplist hidden" role="listbox"></div></div>
    <button class="more" data-cap-more title="所有動作">⋯</button><button data-a="clear" class="cx" title="取消選取（Esc）">取消</button>`;
  const inp = $("#cap-in", bar), box = $(".caplist", bar);
  let hl = 0, shown = [];
  const paint = () => { box.classList.toggle("hidden", !shown.length); box.innerHTML = shown.map((x, i) => actBtn(x, i === hl ? "hl" : "")).join(""); };
  const draw = all => {
    const t = inp.value.trim().toLowerCase();
    shown = all ? list : !t ? [] : list.filter(x => x.label.toLowerCase().includes(t) || (x.key || "").toLowerCase() === t || (x.title || "").toLowerCase().includes(t));
    hl = 0; paint();
  };
  inp.addEventListener("input", () => draw(false));
  inp.addEventListener("keydown", e => {
    e.stopPropagation();
    if (e.key === "Escape") { if (inp.value || !box.classList.contains("hidden")) { inp.value = ""; draw(false); } else { inp.blur(); clearSel(); } return; }
    if (e.key === "ArrowDown" || e.key === "ArrowUp") { e.preventDefault(); if (!shown.length) draw(true); else { hl = (hl + (e.key === "ArrowDown" ? 1 : shown.length - 1)) % shown.length; paint(); } return; }
    if (e.key === "Enter" && shown[hl]) { e.preventDefault(); const x = shown[hl]; inp.value = ""; shown = []; paint(); inp.blur(); runAct(x); }
  });
  $("[data-cap-more]", bar).onclick = e => { e.stopPropagation(); if (box.classList.contains("hidden")) draw(true); else { shown = []; paint(); } };
}
function runAct(x) { if (x.qt != null) quickTag(x.qt); else if (x.a === "selall") selectAll(); else doAction(x.a); }
$("#actionbar").addEventListener("click", e => {
  const b = e.target.closest("button"); if (!b || b.dataset.capMore != null) return;
  $(".caplist", e.currentTarget)?.classList.add("hidden");
  if (b.dataset.qt != null) quickTag(+b.dataset.qt); else if (b.dataset.a === "selall") selectAll(); else if (b.dataset.a) doAction(b.dataset.a);
});
document.addEventListener("mousedown", e => { if (!e.target.closest("#actionbar")) $("#actionbar .caplist")?.classList.add("hidden"); });

export async function doAction(a, idsIn) {   // idsIn：沒選取時用滑鼠指著的那張（快捷鍵）、大圖用眼前這張
  const ids = idsIn || [...S.selected]; if (!ids.length && a !== "clear") return;
  recordUse(a);
  switch (a) {
    case "clear": clearSel(); break;
    case "exclude": case "unexclude": {
      await api("/api/media/exclude", { ids, value: a === "exclude" });
      toast(`${a === "exclude" ? "以前排除的" : "已取消排除"} ${ids.length} 張`, { label: "撤銷", fn: undo }); removeCells(ids); loadSidebar(); break;
    }
    case "skip": case "unskip": {
      await api("/api/media/skip", { ids, value: a === "skip" });
      toast(`${a === "skip" ? "不用整理" : "放回待辦"} ${ids.length} 張`, { label: "撤銷", fn: undo }); if (S.view === "unrecognized" || a === "unskip") removeCells(ids); else clearSel(); loadSidebar(); loadBuckets(); break;
    }
    case "yes": case "no": await decideReview(ids, a === "yes"); break;
    case "rskip": {   // 待確認頁：選的是「第幾筆」→ 換成圖片 id；同一張圖的其他 AI 候選也一起拿掉
      const mids = [...new Set(ids.map(i => S.items[i]?.id).filter(Boolean))];
      const r = await api("/api/media/skip", { ids: mids, value: true });
      $$(".cell.rc").forEach(c => { if (mids.includes(+c.dataset.mid) && S.items[+c.dataset.id]?.source !== undefined && ["model", "ccip"].includes(S.items[+c.dataset.id].source)) { c.remove(); S.selected.delete(+c.dataset.id); } });
      toast(`不用整理 ${mids.length} 張（拿掉 ${r.cleared} 個 AI 候選）`, { label: "撤銷", fn: undo }); updateActionBar(); updateFooter(); loadSidebar(); break;
    }
    case "reject": await tagOp("reject", S.tag, ids, { removeFromView: true }); break;
    case "unreject": await tagOp("unreject", S.tag, ids, { removeFromView: true }); break;
    case "confirm": await tagOp("confirm", S.tag, ids, { removeFromView: ["rejected", "unchecked", "candidates"].includes(S.src) }); break;
    case "original": await tagOp("add", "original", ids, { category: "copyright", removeFromView: S.view === "unrecognized" && S.miss !== "artist" }); break;
    case "replace": pickTag({ title: `把 ${ids.length} 張的 ${S.tag} 改標成…`, category: S.tagInfo?.category || "character", ids },
      t => tagOp("replace", t.name, ids, { from: S.tag, category: t.category, removeFromView: true })); break;
    case "also": pickTag({ title: `${ids.length} 張也加上…`, ids, category: S.view === "tag" ? (S.tagInfo?.category || "character") : (S.view === "unrecognized" && S.miss !== "artist" ? "character" : S.view === "unrecognized" ? "artist" : "character") },
      t => tagOp("add", t.name, ids, { category: t.category, removeFromView: leavesUnrecognized(t.category) })); break;
    case "artist": pickTag({ title: `${ids.length} 張的創作者是…（可輸入新名字）`, category: "artist", only: "artist", ids },
      t => tagOp("add", t.name, ids, { category: t.category, removeFromView: leavesUnrecognized(t.category) })); break;
    case "tags": editTags(ids); break;
    case "nsfw": nsfwMenu(ids); break;
    case "official": officialMenu(ids); break;
    case "mediatype": mediaMenu(ids); break;
    case "source": sourceConfirm(ids); break;
    case "ascii2d": ascii2dModal(ids); break;
    case "srcurl": srcUrlModal(ids, () => render()); break;
    case "album": albumMenu(ids); break;
    case "artistdone": {
      await api("/api/media/artist-done", { ids });
      toast(`${ids.length} 張不用再找作者`, { label: "撤銷", fn: undo });
      if ((S.view === "unrecognized" && S.miss === "artist") || (S.view === "series" && S.seriesMiss === "noartist")) removeCells(ids); else clearSel();
      loadSidebar(); loadBuckets(); break;
    }
    case "crowddone": await api("/api/media/people-done", { ids }); toast(`${ids.length} 張不用再找角色`, { label: "撤銷", fn: undo }); removeCells(ids); loadSidebar(); break;
    case "retag": await api("/api/jobs", { kind: "retag", ids }); toast(`已排入重新標記 ${ids.length} 張`); clearSel(); break;
    case "dataset": datasetModal({ ids }); break;
    case "rerun": openRerun({ ids: S.view === "review" ? [...new Set(ids.map(i => S.items[i]?.id).filter(x => x != null))] : ids }); break;
    case "trash": {
      if (S.view === "review") {   // 待確認頁選的是「第幾筆」→ 換成圖片 id；刪掉後這張的候選全部收掉
        const mids = [...new Set(ids.map(i => S.items[i]?.id).filter(x => x != null))];
        confirmTrash(mids, () => dropImages(mids));
      } else confirmTrash(ids);
      break;
    }
    case "share": await shareCopy(S.view === "review" ? [...new Set(ids.map(i => S.items[i]?.id).filter(x => x != null))] : ids); break;
    case "archive": archiveModal(ids); break;
    case "upgrade": upgradeConfirm(ids); break;
    case "upgradeno": {
      const r = await api("/api/upgrade/dismiss", { ids });
      toast(`${r.dismissed} 張不換高清（不再提醒）`, { label: "撤銷", fn: undo });
      if (/(^|\s)is:upgrade(\s|$)/.test(S.q || "")) removeCells(ids); else clearSel();
      break;
    }
  }
}
// 分享：伺服器把圖放到 Windows 剪貼簿（檔案；只有一張靜態圖時再加上圖片），到 QQ／Messenger 按 Ctrl+V（app/share.py）。
// 不是 Windows 時：瀏覽器自己複製一張（PNG；GIF 會變靜態、影片不行）
const MESSENGER = "https://www.messenger.com/";
let _shareBusy = false;
export async function shareCopy(ids) {
  ids = [...new Set(ids)].filter(x => x != null);
  if (!ids.length || _shareBusy) return;
  _shareBusy = true;
  try {
    const r = await api("/api/media/share", { ids });
    if (r.ok) {
      const extra = [r.converted ? `${r.converted} 張轉成 JPG／PNG` : "", r.missing ? `${r.missing} 張找不到檔案` : "", r.capped ? "一次最多 50 張" : ""].filter(Boolean).join("，");
      toast(`已複製 ${r.n} ${r.videos === r.n ? "個影片" : "張"}：到 QQ 或 Messenger 的聊天視窗按 Ctrl+V${extra ? `（${extra}）` : ""}`, { label: "開 Messenger", fn: () => window.open(MESSENGER, "_blank", "noopener") });
    } else if (r.reason === "unsupported" && ids.length === 1) await browserCopy(ids[0]);
    else if (r.reason === "unsupported") toast("這台電腦不是 Windows：一次只能複製一張（選一張再按一次）");
    else toast("找不到檔案（被搬走、刪掉，或 iCloud 還沒下載）");
  } catch (e) { toast(`複製失敗：${e.message}`); }
  finally { _shareBusy = false; }
}
async function browserCopy(id) {
  const blob = await fetch(`/file/${id}?display=1`).then(r => { if (!r.ok) throw new Error("讀不到圖片"); return r.blob(); });
  const bmp = await createImageBitmap(blob);
  const cv = document.createElement("canvas"); cv.width = bmp.width; cv.height = bmp.height;
  cv.getContext("2d").drawImage(bmp, 0, 0);
  const png = await new Promise(r => cv.toBlob(r, "image/png"));
  await navigator.clipboard.write([new ClipboardItem({ "image/png": png })]);
  toast("已複製圖片：到聊天視窗按 Ctrl+V", { label: "開 Messenger", fn: () => window.open(MESSENGER, "_blank", "noopener") });
}
// 整批換成高清：背景工作（可以看進度、停止）。只換「有更高清版本」的；下載後逐像素確認是同一張才換
async function upgradeConfirm(ids) {
  modal(`<h3>換成高清版（${ids.length} 張）</h3><div class="mbody">
    <p style="margin-top:0">選的圖裡，出處有<b>同一張、比較大</b>的會換掉（沒有的會略過）：</p>
    <ul class="mute" style="margin:0;padding-left:20px;line-height:1.7">
      <li>先下載，逐像素確認是同一張（差分、改過字的不換）</li>
      <li>新檔放在原本的資料夾、檔名不變（副檔名照新檔，例如 .png）；日期沿用舊檔</li>
      <li>角色、創作者、作品、標籤、本子頁序全部保留</li>
      <li>舊檔丟到<b>資源回收筒</b>（還原的話會變成圖庫裡另一張）</li></ul></div>
    <div class="mfoot"><button class="btn" data-close>取消</button><button class="btn primary" id="do-up">換成高清</button></div>`, m => {
    $("#do-up", m).onclick = async () => {
      try { await api("/api/upgrade/replace", { ids }); } catch (e) { return toast(e.message); }
      closeModal(); S.upgradeWatch = Date.now() / 1000 - 1;
      toast(`已排入：換成高清 ${ids.length} 張（背景工作，做完會通知）`); clearSel();
    };
  });
}

function leavesUnrecognized(cat) {  // 在「未識別」頁加了這類 tag 後，圖會離開目前的分頁
  if (S.view !== "unrecognized") return false;
  if (S.miss === "people") return false;        // 多人圖：加了一個角色不一定就標完（伺服器重算後再決定）
  if (S.miss === "all") return ["character", "artist", "copyright"].includes(cat);
  return cat === S.miss;
}
async function tagOp(action, tag, ids, o = {}) {
  let r;
  try {
    r = await api("/api/tags/apply", { ids, action, tag, from_tag: o.from, category: o.category });
  } catch (e) { toast("失敗：" + e.message); return; }
  const verb = { reject: `不是 ${tag}`, confirm: `確認 ${tag}`, add: `加上 ${tag}`, replace: `換成 ${tag}`, unreject: `取消否決 ${tag}` }[action];
  toast(`${verb}（${ids.length} 張）` + (r?.propagated ? `，並移除 ${r.propagated} 張相似圖上的自動標註` : "")
    + (r?.crowd_short && !(S.view === "unrecognized" && S.miss === "people") ? `。其中 ${r.crowd_short} 張是多人圖、還有人沒標 → 在「未識別 › 多人圖缺角色」` : "")
    , { label: "撤銷", fn: undo });
  if (o.removeFromView) removeCells(ids);
  else if (action === "confirm" && S.view === "tag") { const set = new Set(ids); S.items.forEach(it => { if (set.has(it.id)) it.state = 1; }); drawWindow(true); }
  clearSel(); loadSidebar(); if (S.view === "unrecognized") loadBuckets();
}
export function removeCells(ids) {
  const set = new Set(ids);
  S.items = S.items.filter(it => !set.has(it.id));
  ids.forEach(id => { $$(`.cell[data-id="${id}"]`).forEach(c => c.remove()); S.selected.delete(id); });
  S.total = Math.max(0, S.total - ids.length); updateFooter(); updateActionBar();
  if (S.layout) relayout();
}
export async function undo() {
  const r = await api("/api/undo", {});
  toast(r.undone ? `已撤銷：${r.undone}` : "沒有可撤銷的動作");
  if (r.undone) {
    if (V) { markDirty(); openViewer(V.id); } else render();   // 在大圖裡撤銷：只重畫大圖，關掉時再更新格子
    loadSidebar();
  }
}
$("#undo-btn").onclick = undo;

function officialMenu(ids) {
  modal(`<h3>官方作品（${ids.length} 張）</h3><div class="mbody">動畫、漫畫、電影截圖和小說插圖都算已經有作者：不會出現在「缺創作者」和「完全未識別」，標了作品＋角色就算整理完。圖庫的「官方截圖」分頁可以找到全部。
    <div class="mute" style="font-size:var(--fs1);margin-top:6px">搜尋：<code>type:anime</code>、<code>type:manga</code>、<code>type:novel</code>、<code>type:movie</code>、<code>is:official</code></div></div>
    <div class="mfoot"><button class="btn" data-k="">不是官方</button>${Object.entries(OFFICIAL).map(([k, v]) => `<button class="btn primary" data-k="${k}">${v}</button>`).join("")}</div>`, m => {
    $$("[data-k]", m).forEach(b => b.onclick = async () => {
      const kind = b.dataset.k || null;
      await api("/api/media/official", { ids, kind }); closeModal();
      toast(kind ? `已標為${OFFICIAL[kind]}（${ids.length} 張）` : `已取消官方種類（${ids.length} 張）`, { label: "撤銷", fn: undo });
      if (S.view === "unrecognized" && S.miss === "artist" && (kind === "anime" || kind === "manga")) removeCells(ids);
      else if (S.mtype && S.mtype !== kind && OFFICIAL[S.mtype]) removeCells(ids);
      else {                // 格子上的標示跟著換
        const set = new Set(ids);
        S.items.forEach(it => { if (set.has(it.id)) { it.official = kind; const c = $(`.cell[data-id="${it.id}"]`); if (c) c.outerHTML = cellHTML(it, S.items.indexOf(it), c.getAttribute("style") || ""); } });
      }
      clearSel(); loadSidebar();
    });
  });
}
function mediaMenu(ids) {
  modal(`<h3>媒材（${ids.length} 張）</h3><div class="mbody">你標的一律照你的；AI 會拿你標的重新學，幾秒後整個圖庫重算。AI 沒把握的圖在媒材選單的「未定」。
    <div class="mute" style="font-size:var(--fs1);margin-top:6px">數字鍵也可以：1 插畫、2 Q 版、3 漫畫、4 3D、5 Cosplay、6 照片、7 公仔、8 截圖。你標的會拿來教 AI</div></div>
    <div class="mfoot"><button class="btn" data-k="">改回自動</button>${Object.entries(MTYPES).map(([k, v]) => `<button class="btn" data-k="${k}">${v}</button>`).join("")}</div>`, m => {
    const pick = async kind => {
      await api("/api/media/mediatype", { ids, label: kind }); closeModal();
      toast(kind ? `媒材改成${MTYPES[kind]}（${ids.length} 張）· AI 重新學` : `媒材改回自動（${ids.length} 張）`, { label: "撤銷", fn: undo });
      if (S.mtype && !OFFICIAL[S.mtype] && S.mtype !== kind) removeCells(ids);
      else {
        const set = new Set(ids);
        S.items.forEach(it => { if (set.has(it.id) && kind) { it.media_type = kind; const c = $(`.cell[data-id="${it.id}"]`); if (c) c.outerHTML = cellHTML(it, S.items.indexOf(it), c.getAttribute("style") || ""); } });
      }
      clearSel();
    };
    $$("[data-k]", m).forEach(b => b.onclick = () => pick(b.dataset.k || null));
    const keys = Object.keys(MTYPES);
    m.addEventListener("keydown", e => { const n = +e.key; if (n >= 1 && n <= keys.length) { e.preventDefault(); e.stopPropagation(); pick(keys[n - 1]); } });
    m.tabIndex = -1; m.focus();
  });
}
function nsfwMenu(ids) {
  modal(`<h3>NSFW 標記（${ids.length} 張）</h3><div class="mbody">手動標記會覆蓋模型判定。</div>
    <div class="mfoot"><button class="btn" data-v="null">改回自動判定</button><button class="btn" data-v="0">標為安全</button><button class="btn primary" data-v="1">標為 NSFW</button></div>`, m => {
    $$("[data-v]", m).forEach(b => b.onclick = async () => {
      const value = JSON.parse(b.dataset.v);
      await api("/api/media/nsfw", { ids, value }); closeModal(); loadNsfwIds(true);
      toast(value === 1 ? `已標為 NSFW（${ids.length}）` : value === 0 ? `已標為安全（${ids.length}）` : "已改回自動", { label: "撤銷", fn: undo });
      if (value === 1 && S.nsfw === "hide") removeCells(ids);
      else { const set = new Set(ids); S.items.forEach(it => { if (set.has(it.id)) it.nsfw = value === 1 ? 1 : 0; }); ids.forEach(id => $$(`.cell[data-id="${id}"]`).forEach(c => c.classList.toggle("nsfw", value === 1))); }
      clearSel(); loadSidebar();
    });
  });
}
export async function confirmTrash(ids, onDone) {
  const pv = await api("/api/media/archive/preview", { ids }).catch(() => ({ icloud: 0 }));
  modal(`<h3>刪除 ${ids.length} 個檔案？</h3><div class="mbody">檔案會移到系統的<b>資源回收筒</b>，並從圖庫移除。<br><span class="mute">標籤和整理紀錄會保留 30 天：從資源回收筒還原檔案後，下次掃描會自動接回，你確認過的角色、作者都還在。</span>
    ${pv.icloud ? `<div class="danwarn bad_auth" style="margin:12px 0 0"><div><b>其中 ${pv.icloud} 張在 iCloud 照片資料夾</b><div class="mute">刪掉會同步刪除 iPad／iPhone 和 iCloud 上的這些照片。
      如果只是想省 iCloud 空間、電腦上要留著，請改用「移出 iCloud」。</div></div><button class="btn sm" id="to-archive">改用移出 iCloud</button></div>` : ""}
    ${pv.dup_all ? `<div class="danwarn bad_auth" style="margin:12px 0 0"><div><b>有 ${pv.dup_all} 組重複的圖，整組每一張都選了</b><div class="mute">刪掉後這幾張一張都不留。通常每組留一張就好。</div></div></div>` : ""}</div>
    <div class="mfoot"><button class="btn" data-close>取消</button><button class="btn primary danger" id="do-trash">移到回收筒</button></div>`, m => {
    const ta = $("#to-archive", m); if (ta) ta.onclick = () => archiveModal(ids);
    $("#do-trash", m).onclick = async () => {
      const r = await api("/api/media/trash", { ids }); closeModal();
      toast(`已移到回收筒 ${r.trashed} 個` + (r.merged ? `；${r.merged} 個是同一個檔案的另一筆紀錄，檔案留著` : "") + (r.failed.length ? `，失敗 ${r.failed.length}` : ""));
      if (onDone) onDone(r);
      else if (S.view === "duplicates") render(); else removeCells(ids);
      loadSidebar();
    };
    $("#do-trash", m).focus();      // Enter＝確定刪除、Esc＝取消（按 Delete 叫出來後不用碰滑鼠）
  });
}




async function editTags(ids) {
  const d = await api("/api/media/tags-summary", { ids });
  const n = d.total;
  const m = modal(`<h3>標籤（${n} 張）</h3><div class="mbody" id="et-body"></div>
    <div class="mfoot" style="justify-content:space-between"><div class="addbtns">${[["character", "角色"], ["artist", "創作者"], ["copyright", "作品"], ["custom", "標籤"]].map(([k, l]) => `<button class="btn sm" data-add-cat="${k}">＋ ${l}</button>`).join("")}</div><button class="btn" data-close>完成</button></div>`, null, "wide");
  const body = $("#et-body", m);
  const draw = (tags) => {
    const groups = { character: [], artist: [], copyright: [], custom: [], general: [] };
    tags.forEach(t => (groups[t.category] || groups.general).push(t));
    body.innerHTML = Object.entries(groups).filter(([, a]) => a.length).map(([c, a]) => `<div class="grp-title">${catName(c)} <span>${a.length}</span></div><div class="chips">${a.map(t => `
      <span class="chip ${t.category} ${t.n && t.confirmed === t.n ? "c1" : ""} ${!t.n ? "rej" : ""}" data-t="${esc(t.name)}" data-c="${t.category}" title="${t.n}/${n} 張有 · ${t.confirmed} 張已確認${t.rejected ? ` · ${t.rejected} 張否決` : ""}">
      <span class="nm">${esc(t.name)}</span><span class="sc">${t.n === n ? "" : t.n}</span>
      ${t.n ? `<button data-op="confirm" title="全部確認">✓</button><button data-op="reject" title="全部否決（不是）">✕</button>` : `<button data-op="unreject" title="取消否決">↺</button>`}</span>`).join("")}</div>`).join("") || `<div class="hint">沒有標籤</div>`;
  };
  draw(d.tags);
  const refresh = async () => draw((await api("/api/media/tags-summary", { ids })).tags);
  body.addEventListener("click", async e => {
    const chip = e.target.closest(".chip"); if (!chip) return;
    const b = e.target.closest("button");
    if (b) { await api("/api/tags/apply", { ids, action: b.dataset.op, tag: chip.dataset.t }); refresh(); loadSidebar(); }
    else if (e.target.closest(".nm")) { closeModal(); go("tag", chip.dataset.t); }
  });
  $$("[data-add-cat]", m).forEach(b => b.onclick = () => pickTag({ title: `${n} 張加上${catName(b.dataset.addCat)}…`, category: b.dataset.addCat, ids },
    async t => { await api("/api/tags/apply", { ids, action: "add", tag: t.name, category: t.category }); loadSidebar(); setTimeout(() => { if (!modalOpen()) editTags(ids); }, 0); }));
}


/* ============ 移出 iCloud：留在電腦、iCloud 刪掉（省空間） ============ */
export async function archiveModal(ids) {
  const pv = await api("/api/media/archive/preview", { ids });
  if (!pv.icloud) { toast("選取的圖都不在 iCloud 照片資料夾裡"); return; }
  modal(`<h3>移出 iCloud（${pv.icloud} 張，約 ${fmtSize(pv.size)}）</h3><div class="mbody">
    <p style="margin-top:0">把這些圖<b>留在電腦</b>，並從 <b>iCloud／iPad</b> 刪掉以節省空間。標籤、確認、搜源結果都會保留。${pv.others ? `<br><span class="mute">另外 ${pv.others} 張不在 iCloud 裡，不會動。</span>` : ""}</p>
    <ol class="steps"><li>複製到下面的電腦資料夾（依年月分資料夾）</li><li>逐張比對內容指紋，確認一模一樣</li><li>圖庫改指向電腦上的那份</li><li>最後才把 iCloud 資料夾裡的那份丟到資源回收筒 → iCloud 同步刪除</li></ol>
    <div class="form"><div class="field"><label>電腦保存資料夾</label><div><input type="text" id="ar-dest" value="${esc(pv.dest)}"><button class="btn sm" id="ar-browse" style="margin-top:6px">${svg("folder", 15)} 瀏覽…</button><div id="ar-fsb"></div></div></div></div>
    <p class="mute" style="font-size:var(--fs1)">任何一張複製或比對失敗，就原封不動留在 iCloud。刪掉的照片會先進 iPad「照片 › 最近刪除」30 天，要馬上釋放空間請到那裡清空。</p>
  </div><div class="mfoot"><button class="btn" data-close>取消</button><button class="btn primary" id="ar-go">開始移出</button></div>`, m => {
    const inp = $("#ar-dest", m);
    inp.addEventListener("keydown", e => e.stopPropagation());
    $("#ar-browse", m).onclick = () => folderBrowser($("#ar-fsb", m), p => { inp.value = p; $("#ar-fsb", m).innerHTML = ""; });
    $("#ar-go", m).onclick = async () => {
      try {
        await api("/api/media/archive", { ids, dest: inp.value });
        closeModal(); clearSel(); S.archiveWatch = Date.now() / 1000;
        toast("開始移出 iCloud，進度在右上角"); pollOnce();
      } catch (e) { toast(e.message); }
    };
  }, "wide");
}
