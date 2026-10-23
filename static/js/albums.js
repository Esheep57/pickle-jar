// ES module（2026-10）：用到別的檔案的東西都要在這裡 import；瀏覽器會在載入時檢查名字對不對。
import { $, $$, S, api, apiNsfw, catName, closeModal, debounce, esc, fmtN, modal, safeUrl, toast, pageMenu, svg } from "./core.js";
import { go, loadSidebar } from "./shell.js";
import { render } from "./grid.js";
import { clearSel } from "./selection.js";
import { undo } from "./actions.js";
import { pickTag } from "./picker.js";
import { openViewer } from "./viewer.js";
/* ============ 本子：同人本、cos 圖包、畫集 ============
   - 本子頁：卡片＋「可能是一整本的資料夾」建議（一鍵建立）
   - 單本頁：資訊、整本的標籤（套到每一頁）、依頁序的圖（大圖 ←→ 就是翻頁）
   - 任何地方選圖後按 B（或選取列「本子…」）：建立新的或加進已有的 */
const ALBUM_KINDS = [["doujin", "同人本"], ["cos", "cos 圖包"], ["artbook", "畫集"], ["comic", "漫畫"], ["other", "其他"]];

export async function renderAlbums(gen) {
  $("#crumb").innerHTML = `<b>本子</b>`;
  $("#head").innerHTML = `<div><h1>本子</h1><div class="sub">一整本同人本、一套 cos 圖包、一本畫集。整本的作者／角色／作品一次標好，會套到每一頁；大圖裡 ←→ 就是翻頁。選圖後按 <kbd>B</kbd> 建立或加入。</div></div>`;
  $("#toolbar").className = "qtb";
  $("#toolbar").innerHTML = `<div class="qrow2"><div class="qbox" id="qbox">${svg("search", 16)}<span class="qtok scope"><i>找</i>本子</span><input id="al-q" autocomplete="off" placeholder="書名、作者…"></div></div>`;
  $("#content").innerHTML = `<div class="empty mute">載入中…</div>`;
  let list, sug;
  try { [list, sug] = await Promise.all([api(`/api/albums?nsfw=${apiNsfw()}`), api("/api/albums/suggest-folders").catch(() => [])]); } catch (e) { toast(e.message); return; }
  if (gen !== S.gen) return;
  $("#foot-l").textContent = `${fmtN(list.length)} 本本子`;   // 以前沒設：留著上一頁的「1667 個創作者」
  // v4.4 書封卡：直式 3:4 封面（書背＋紙張的厚度）、類型標在封面下面不蓋圖
  const KC = { doujin: "k-doujin", cos: "k-cos", artbook: "k-art", comic: "k-comic", other: "k-other" };
  const card = a => `<div class="card bcard" data-album="${a.id}">
      <div class="book ${a.n >= 60 ? "thick" : a.n >= 20 ? "mid" : ""}">${a.cover ? `<img loading="lazy" src="/t/${a.cover}">` : `<span class="noimg">${esc((a.title || "?").slice(0, 1))}</span>`}<i class="spine"></i></div>
      <div class="bmeta"><span class="bkind ${KC[a.kind] || "k-other"}">${esc(a.kind_label)}</span><span class="bn">${fmtN(a.n)} 頁</span></div>
      <div class="nm" title="${esc(a.title)}">${esc(a.title)}</div>
      <div class="cp">${a.tags.filter(t => t.category !== "general").slice(0, 3).map(t => esc(t.display || t.name)).join("、") || `<span class="mute">還沒有整本的標籤</span>`}</div></div>`;
  const sugCard = (f, i) => `<div class="sugc" data-sug="${i}" title="${esc(f.folder)}">
      <img loading="lazy" src="/t/${f.cover}"><div><div class="nm">${esc(f.title)}</div>
      <div class="cp">${fmtN(f.n)} 張 · ${f.tags.map(t => esc(t.raw)).join("、") || esc(f.name)}</div></div><button class="btn sm">建立</button></div>`;
  if (S.remote) sug = [];              // 手機／平板只看：不顯示「建立本子」建議
  const draw = () => {
    const q = ($("#al-q").value || "").trim().toLowerCase();
    const L = list.filter(a => !q || (a.title + " " + a.tags.map(t => t.display + t.name).join(" ")).toLowerCase().includes(q));
    $("#content").innerHTML = (L.length ? `<div class="cards bcards">${L.map(card).join("")}</div>`
      : `<div class="empty">${list.length ? "沒有符合的本子" : "還沒有本子。選圖後按 <kbd>B</kbd> 建立，或從下面的資料夾建議一鍵建立。"}</div>`)
      + (sug.length ? `<div class="sugwrap"><div class="sugh"><b>可能是一整本的資料夾</b><span>圖很多、檔名連號、不是按月份分的；按「建立」一鍵變成本子</span></div>
        <div class="sugstrip">${sug.map(sugCard).join("")}</div></div>` : "");
  };
  draw();
  $("#al-q").oninput = debounce(draw, 120);
  $("#content").onclick = e => {
    const s = e.target.closest("[data-sug]"); if (s) return albumCreateModal({ ids: sug[+s.dataset.sug].ids, folder: sug[+s.dataset.sug].folder, name: sug[+s.dataset.sug].name, tags: sug[+s.dataset.sug].tags, title: sug[+s.dataset.sug].title });
    const a = e.target.closest("[data-album]"); if (a) go("album", a.dataset.album);
  };
}

// 建立本子（從資料夾建議，或從選取的圖）
async function albumCreateModal({ ids, folder = null, name = "", tags = null, title = "" }) {
  if (!tags && name) { try { const r = await api(`/api/albums/parse?name=${encodeURIComponent(name)}`); tags = r.tags; title = title || r.parsed.title; } catch { } }
  tags = tags || [];
  const guessKind = /cos|コス|写真|寫真|photo/i.test(name + title) ? "cos" : /\[[^\]]+\]/.test(name) ? "doujin" : "other";
  const m = modal(`<h3>建立本子（${fmtN(ids.length)} 張）</h3><div class="mbody form">
    <div class="field"><label>名稱</label><input type="text" id="ac-title" value="${esc(title || name)}"></div>
    <div class="field"><label>類型</label><span class="seg" id="ac-kind">${ALBUM_KINDS.map(([k, l]) => `<button data-k="${k}" class="${k === guessKind ? "on" : ""}">${l}</button>`).join("")}</span></div>
    ${tags.length ? `<div class="field"><label>整本的標籤</label><div>${tags.map((t, i) => `<label class="acbox"><input type="checkbox" data-t="${i}" ${t.checked ? "checked" : ""}> ${catName(t.category)}：<b title="${esc(t.name)}">${esc(t.display || t.name)}</b>${t.known ? (t.display !== t.name ? ` <span class="mute">${esc(t.name)}</span>` : "") : ` <span class="mute">（新標籤，資料夾名的「${esc(t.raw)}」）</span>`}</label>`).join("")}</div></div>` : ""}
    <div class="note">整本的標籤會加到每一頁（之後加進來的頁也會）。建立後也可以在本子頁再加作者、角色、作品。${folder ? `<br>資料夾：${esc(folder)}` : ""}</div></div>
    <div class="mfoot"><button class="btn" data-close>取消</button><button class="btn primary" id="ac-go">建立</button></div>`);
  let kind = guessKind;
  $$("#ac-kind button", m).forEach(b => b.onclick = () => { kind = b.dataset.k; $$("#ac-kind button", m).forEach(x => x.classList.toggle("on", x === b)); });
  $("#ac-title", m).addEventListener("keydown", e => e.stopPropagation());
  $("#ac-go", m).onclick = async () => {
    const pick = $$("[data-t]", m).filter(x => x.checked).map(x => tags[+x.dataset.t]).map(t => ({ name: t.name, category: t.category }));
    try {
      const r = await api("/api/albums", { title: $("#ac-title", m).value.trim(), ids, kind, tags: pick, folder });
      closeModal(); clearSel(); loadSidebar();
      toast(`已建立本子（${fmtN(ids.length)} 張）`, { label: "撤銷", fn: undo });
      go("album", r.id);
    } catch (e) { toast(e.message); }
  };
}

// 選取列「本子…」（B）：加入已有的，或建立新的
export async function albumMenu(ids) {
  if (!ids.length) return toast("先選圖");
  let list = []; try { list = await api(`/api/albums?nsfw=show`); } catch { }
  const m = modal(`<h3>本子（${fmtN(ids.length)} 張）</h3><div class="mbody">
    <button class="btn primary" id="am-new">＋ 用這 ${fmtN(ids.length)} 張建立新的本子</button>
    ${list.length ? `<div class="sech" style="margin:14px 0 6px">或加進已有的</div><input id="am-q" placeholder="篩選…" style="width:100%;margin-bottom:8px">
      <div class="amlist">${list.map(a => `<div class="amrow" data-add="${a.id}">${a.cover ? `<img src="/t/${a.cover}">` : `<span class="noimg"></span>`}<span><b>${esc(a.title)}</b><br><span class="mute">${esc(a.kind_label)} · ${fmtN(a.n)} 張</span></span></div>`).join("")}</div>` : ""}
    ${S.view === "album" && S.albumId ? `<div class="sech" style="margin:14px 0 6px">這一本</div><button class="btn" id="am-remove">從這本移出 ${fmtN(ids.length)} 張</button> <button class="btn" id="am-cover">設為封面</button>` : ""}
    </div><div class="mfoot"><button class="btn" data-close>關閉</button></div>`);
  $("#am-new", m).onclick = async () => {
    // 同一個資料夾的圖：用資料夾名當預設名稱（會解析作者、作品）
    let name = ""; try { const d = await api(`/api/media/${ids[0]}`); const parts = d.path.split(/[\\/]/); name = parts[parts.length - 2] || ""; } catch { }
    albumCreateModal({ ids, name: /^\d{4}[-_]?\d{1,2}$|^Downloads$/i.test(name) ? "" : name });
  };
  const q = $("#am-q", m); if (q) { q.addEventListener("keydown", e => e.stopPropagation()); q.oninput = () => $$(".amrow", m).forEach(r => r.classList.toggle("hidden", !r.textContent.toLowerCase().includes(q.value.trim().toLowerCase()))); }
  $$("[data-add]", m).forEach(r => r.onclick = async () => {
    const x = await api(`/api/albums/${r.dataset.add}/add`, { ids }); closeModal(); clearSel(); loadSidebar();
    toast(`已加入 ${fmtN(x.n)} 張${x.n < ids.length ? `（${ids.length - x.n} 張本來就在裡面）` : ""}`, { label: "撤銷", fn: undo });
    if (S.view === "album") render();
  });
  const rm = $("#am-remove", m); if (rm) rm.onclick = async () => { await api(`/api/albums/${S.albumId}/remove`, { ids }); closeModal(); clearSel(); toast(`已移出 ${ids.length} 張`, { label: "撤銷", fn: undo }); render(); };
  const cv = $("#am-cover", m); if (cv) cv.onclick = async () => { await api(`/api/albums/${S.albumId}`, { cover: ids[0] }); closeModal(); toast("已設為封面", { label: "撤銷", fn: undo }); render(); };
}

export async function albumHead(gen) {
  let d; try { d = await api(`/api/albums/${S.albumId}`); } catch (e) { $("#head").innerHTML = `<div><h1>本子</h1><div class="sub">${esc(e.message)}</div></div>`; return; }
  if (gen !== S.gen) return;
  S.album = d;
  $("#crumb").innerHTML = `<a data-back>本子</a> / <b>${esc(d.title)}</b>`;
  $("[data-back]", $("#crumb")).onclick = () => go("albums");
  const head = $("#head");
  head.innerHTML = `<div class="headmain"><h1>${esc(d.title)}</h1>
    <div class="sub"><span class="pill2">${esc(d.kind_label)}</span> · ${fmtN(d.n)} 張${d.folder ? ` · <span class="mute" title="${esc(d.folder)}">資料夾建立</span>` : ""}${d.source_url ? ` · <a href="${esc(safeUrl(d.source_url))}" target="_blank" rel="noopener">來源 ↗</a>` : ""}</div>
    <div class="altags"><span class="mute">整本的標籤（每一頁都有）：</span>${d.tags.map(t => `<span class="chip ${esc(t.category)} c1" data-t="${esc(t.name)}"><span class="cat ${esc(t.category)}">${catName(t.category)}</span> <span class="nm" title="${esc(t.name)}">${esc(t.display || t.name)}</span><button data-untag="${esc(t.name)}" title="從整本拿掉（只拿掉因為本子才加上的）">✕</button></span>`).join("")}
      ${[["artist", "作者"], ["character", "角色"], ["copyright", "作品"], ["custom", "標籤"]].map(([k, l]) => `<button class="btn sm" data-altag="${k}">＋ ${l}</button>`).join("")}</div>
    ${d.note ? `<div class="mute" style="margin-top:6px">${esc(d.note)}</div>` : ""}
    ${d.books?.length ? `<div class="albooks"><span class="mute">${d.books.length} 本：</span>${d.books.map((b, i) => `<button class="bk" data-bk="${b.first}" title="${esc(b.label)}（${b.n} 頁）：從這本第一頁看"><i>${i + 1}</i>${esc(b.vol ? `${b.vol}` : (b.post?.title || b.label).length > 18 ? (b.post?.title || b.label).slice(0, 18) + "…" : (b.post?.title || b.label))}</button>`).join("")}</div>
      ${d.books_contiguous ? "" : `<div class="alwarn">這本由 ${d.books.length} 本組成，但頁序交錯了（各本的第 1 頁排在一起）。<button class="btn sm primary" id="al-fix">智慧排序</button></div>`}` : ""}</div>
    <div class="actions"><button class="ghost" id="al-read">▶ 從第一頁看</button>${pageMenu("al-menu", [`<button class="ghost" id="al-sort" title="智慧排序、依檔名、依拍攝時間、依加入時間、整本倒過來">排頁序…</button>`, `<button class="ghost" id="al-arrange" title="拖曳調整頁序（選了圖就先選好那幾張）">手動調整頁序</button>`, `<button class="ghost" id="al-edit">改名／類型</button>`, `<button class="ghost danger" id="al-del">解散這本…</button>`])}</div>`;
  $$("[data-altag]", head).forEach(b => b.onclick = () => pickTag({ title: `整本加上${b.textContent.replace("＋ ", "")}（${fmtN(d.n)} 張）`, category: b.dataset.altag, only: b.dataset.altag, ids: [] }, async t => {
    await api(`/api/albums/${d.id}/tag`, { name: t.name, category: t.category || b.dataset.altag, on: true });
    toast(`整本加上 ${t.display || t.name}（${fmtN(d.n)} 張）`, { label: "撤銷", fn: undo }); loadSidebar(); render();
  }));
  $$("[data-untag]", head).forEach(b => b.onclick = async e => {
    e.stopPropagation();
    await api(`/api/albums/${d.id}/tag`, { name: b.dataset.untag, on: false });
    toast(`已從整本拿掉 ${b.dataset.untag}`, { label: "撤銷", fn: undo }); loadSidebar(); render();
  });
  $$(".altags .chip .nm", head).forEach(n => n.onclick = () => go("tag", n.closest(".chip").dataset.t));
  $("#al-read").onclick = () => { if (S.items.length) openViewer(S.items[0].id); };
  $("#al-sort").onclick = () => sortModal(d);
  $("#al-arrange").onclick = () => arrangeModal(d);
  $$("[data-bk]", head).forEach(b => b.onclick = () => openViewer(+b.dataset.bk));
  bindPostText();
  const fix = $("#al-fix", head); if (fix) fix.onclick = () => applySort(d, "smart");
  $("#al-edit").onclick = () => {
    const m = modal(`<h3>本子</h3><div class="mbody form"><div class="field"><label>名稱</label><input type="text" id="ae-t" value="${esc(d.title)}"></div>
      <div class="field"><label>類型</label><span class="seg" id="ae-k">${ALBUM_KINDS.map(([k, l]) => `<button data-k="${k}" class="${k === d.kind ? "on" : ""}">${l}</button>`).join("")}</span></div>
      <div class="field"><label>來源網址</label><input type="text" id="ae-u" value="${esc(d.source_url || "")}" placeholder="https://…（選填）"></div>
      <div class="field"><label>備註</label><input type="text" id="ae-n" value="${esc(d.note || "")}" placeholder="（選填）"></div></div>
      <div class="mfoot"><button class="btn" data-close>取消</button><button class="btn primary" id="ae-ok">儲存</button></div>`);
    let kind = d.kind;
    $$("#ae-k button", m).forEach(b => b.onclick = () => { kind = b.dataset.k; $$("#ae-k button", m).forEach(x => x.classList.toggle("on", x === b)); });
    $$("input", m).forEach(i => i.addEventListener("keydown", e => e.stopPropagation()));
    $("#ae-ok", m).onclick = async () => {
      await api(`/api/albums/${d.id}`, { title: $("#ae-t", m).value.trim() || d.title, kind, source_url: $("#ae-u", m).value.trim(), note: $("#ae-n", m).value.trim() });
      closeModal(); toast("已更新", { label: "撤銷", fn: undo }); render();
    };
  };
  $("#al-del").onclick = () => {
    const m = modal(`<h3>解散「${esc(d.title)}」？</h3><div class="mbody"><p>圖本身不會動，只是不再是一本本子。</p>
      <label class="acbox"><input type="checkbox" id="ad-untag" checked> 一併拿掉因為本子才加上的標籤（你在單頁上自己標的不動）</label></div>
      <div class="mfoot"><button class="btn" data-close>取消</button><button class="btn primary danger" id="ad-ok">解散</button></div>`);
    $("#ad-ok", m).onclick = async () => {
      await api(`/api/albums/${d.id}/delete?untag=${$("#ad-untag", m).checked}`, {});
      closeModal(); toast("已解散", { label: "撤銷", fn: undo }); loadSidebar(); go("albums");
    };
  };
}

// ---------- 頁序 ----------
const SORT_HELP = {
  smart: "好幾本合在一起：先分本（照集數 1、2、3…完結篇），每本裡照檔名；手機照片照 IMG 編號（9999 之後的 0001 接在後面），影片插在時間最接近的那張後面。",
  name: "只看檔名（1、2…10 自然排序），不分資料夾。",
  taken: "照拍攝時間（沒有就用檔案時間）。",
  added: "照加進圖庫的時間。",
  reverse: "把現在的順序整個倒過來。",
};
async function applySort(d, by) {
  try {
    const r = await api(`/api/albums/${d.id}/sort`, { by });
    toast(r.moved ? `已${d.sorts?.[by] || "排序"}（${fmtN(r.moved)} 頁換了位置）` : "頁序本來就是這樣", r.moved ? { label: "撤銷", fn: undo } : undefined);
    closeModal(); render();
  } catch (e) { toast(e.message); }
}
function sortModal(d) {
  const sorts = Object.entries(d.sorts || { smart: "智慧排序", name: "依檔名", taken: "依拍攝時間", added: "依加入時間", reverse: "整本倒過來" });
  let by = "smart";
  const m = modal(`<h3>排頁序（${fmtN(d.n)} 頁）</h3><div class="mbody">
    <div class="seg" id="so-by">${sorts.map(([k, l]) => `<button data-by="${k}" class="${k === by ? "on" : ""}">${esc(l)}${k === "smart" ? "（建議）" : ""}</button>`).join("")}</div>
    <p class="mute" id="so-help" style="margin:10px 0 6px"></p><div id="so-prev" class="soprev">…</div>
    <div class="note">排完可以 Ctrl+Z 撤銷；要自己調哪幾頁，用「手動調整」。</div></div>
    <div class="mfoot"><button class="btn" data-close>取消</button><button class="btn" id="so-arr">手動調整…</button><button class="btn primary" id="so-go">套用</button></div>`);
  const show = async () => {
    $$("#so-by button", m).forEach(b => b.classList.toggle("on", b.dataset.by === by));
    $("#so-help", m).textContent = SORT_HELP[by] || "";
    $("#so-prev", m).innerHTML = `<span class="mute">計算中…</span>`;
    try {
      const p = await api(`/api/albums/${d.id}/sort-preview?by=${by}`);
      if (p.by !== by) return;
      $("#so-prev", m).innerHTML = (p.books?.length ? `<div><b>會分成 ${p.books.length} 本：</b><ol>${p.books.map(b => `<li>${esc(b.label)} <span class="mute">· ${b.n} 頁</span></li>`).join("")}</ol></div>` : "")
        + `<div><b>前幾頁：</b>${p.first.map(esc).join("、")}${d.n > p.first.length ? "…" : ""}</div>`
        + `<div class="${p.moved ? "" : "mute"}">${p.moved ? `${fmtN(p.moved)} 頁會換位置` : "跟現在的頁序一樣"}</div>`;
    } catch (e) { $("#so-prev", m).textContent = e.message; }
  };
  $$("#so-by button", m).forEach(b => b.onclick = () => { by = b.dataset.by; show(); });
  $("#so-go", m).onclick = () => applySort(d, by);
  $("#so-arr", m).onclick = () => arrangeModal(d);
  show();
}

// 手動調整：所有頁的縮圖，拖曳（可以一次拖好幾張）、或選好後「移到最前／最後／第 N 頁前」
export async function arrangeModal(d) {
  let pages; try { pages = await api(`/api/albums/${d.id}/pages`); } catch (e) { return toast(e.message); }
  const orig = pages.map(p => p.id).join(",");
  const sel = new Set([...S.selected].filter(id => pages.some(p => p.id === id)));
  let anchor = null;
  const hideNsfw = S.nsfw !== "show";
  const m = modal(`<h3>手動調整頁序 <span class="mute" style="font-weight:400;font-size:var(--fs1)">拖曳縮圖；點選、Shift 連選、Ctrl 加選，可以一次拖好幾張</span></h3>
    <div class="arrbar"><span id="ar-n" class="mute"></span>
      <button class="btn sm" data-mv="first">移到最前</button><button class="btn sm" data-mv="last">移到最後</button>
      <span>移到第 <input id="ar-at" type="number" min="1" max="${pages.length}" style="width:64px"> 頁前</span><button class="btn sm" data-mv="at">移</button>
      <button class="btn sm" id="ar-none">取消選取</button></div>
    <div class="mbody"><div class="arrgrid" id="ar-g"></div></div>
    <div class="mfoot"><span class="mute" id="ar-dirty" style="margin-right:auto"></span><button class="btn" data-close>取消</button><button class="btn primary" id="ar-save">儲存頁序</button></div>`, null, "full arrange");
  const g = $("#ar-g", m);
  const books = d.books || [];
  const draw = () => {
    let html = "", sec;
    // 一本一本連著才畫分隔（交錯時每換一次就一條，反而看不清楚）
    const runs = pages.reduce((n, p, i) => n + (i && p.sec !== pages[i - 1].sec ? 1 : 0), pages.length ? 1 : 0);
    const showSec = books.length > 1 && runs === books.length;
    pages.forEach((p, i) => {
      if (showSec && p.sec != null && p.sec !== sec) { sec = p.sec; html += `<div class="arrsec"><b>第 ${sec + 1} 本</b> ${esc(books[sec]?.label || "")}</div>`; }
      html += `<div class="arrc${sel.has(p.id) ? " on" : ""}${p.nsfw && hideNsfw ? " nsfw" : ""}" draggable="true" data-id="${p.id}" data-i="${i}" title="${esc(p.name)}">
        <img loading="lazy" src="/t/${p.id}" draggable="false"><span class="pg">${i + 1}</span>${p.kind === "video" ? `<span class="vg"></span>` : ""}</div>`;
    });
    g.innerHTML = html;
    $("#ar-n", m).textContent = sel.size ? `已選 ${sel.size} 張` : "沒有選取";
    const dirty = pages.map(p => p.id).join(",") !== orig;
    $("#ar-dirty", m).textContent = dirty ? "還沒儲存" : "";
  };
  const moveTo = idx => {          // 把選取的移到 idx（原本清單的位置）之前
    if (!sel.size) return toast("先選要移的頁");
    const moving = pages.filter(p => sel.has(p.id));
    const before = pages.slice(0, idx).filter(p => !sel.has(p.id)).length;
    const rest = pages.filter(p => !sel.has(p.id));
    pages.splice(0, pages.length, ...rest.slice(0, before), ...moving, ...rest.slice(before));
    draw();
  };
  g.onclick = e => {
    const c = e.target.closest(".arrc"); if (!c) return;
    const id = +c.dataset.id, i = +c.dataset.i;
    if (e.shiftKey && anchor != null) {
      const a = pages.findIndex(p => p.id === anchor);
      if (!e.ctrlKey && !e.metaKey) sel.clear();
      for (let k = Math.min(a, i); k <= Math.max(a, i); k++) sel.add(pages[k].id);
    } else if (e.ctrlKey || e.metaKey) { sel.has(id) ? sel.delete(id) : sel.add(id); anchor = id; }
    else { const only = sel.size === 1 && sel.has(id); sel.clear(); if (!only) sel.add(id); anchor = id; }
    draw();
  };
  g.ondblclick = e => { const c = e.target.closest(".arrc"); if (c) { closeModal(); openViewer(+c.dataset.id); } };
  let drop = null;
  const mark = (c, after) => { $$(".arrc.db,.arrc.da", g).forEach(x => x.classList.remove("db", "da")); if (c) c.classList.add(after ? "da" : "db"); };
  g.ondragstart = e => {
    const c = e.target.closest(".arrc"); if (!c) return;
    const id = +c.dataset.id;
    if (!sel.has(id)) { sel.clear(); sel.add(id); anchor = id; $$(".arrc.on", g).forEach(x => x.classList.remove("on")); c.classList.add("on"); }
    e.dataTransfer.effectAllowed = "move"; e.dataTransfer.setData("text/plain", String(id));
    $$(".arrc.on", g).forEach(x => x.classList.add("drag"));
  };
  g.ondragover = e => {
    const c = e.target.closest(".arrc"); if (!c) return;
    e.preventDefault();
    const r = c.getBoundingClientRect(), after = e.clientX > r.left + r.width / 2;
    drop = +c.dataset.i + (after ? 1 : 0); mark(c, after);
    // 拖到邊緣自動捲動
    const body = g.parentElement, br = body.getBoundingClientRect();
    if (e.clientY < br.top + 50) body.scrollTop -= 18; else if (e.clientY > br.bottom - 50) body.scrollTop += 18;
  };
  g.ondragend = () => { mark(null); $$(".arrc.drag", g).forEach(x => x.classList.remove("drag")); };
  g.ondrop = e => { e.preventDefault(); mark(null); if (drop != null) moveTo(drop); drop = null; };
  $$("[data-mv]", m).forEach(b => b.onclick = () => {
    const k = b.dataset.mv;
    if (k === "first") moveTo(0); else if (k === "last") moveTo(pages.length);
    else { const n = +$("#ar-at", m).value; if (!n || n < 1) return toast("填第幾頁"); moveTo(Math.min(n, pages.length + 1) - 1); }
  });
  $("#ar-at", m).addEventListener("keydown", e => { e.stopPropagation(); if (e.key === "Enter") $('[data-mv="at"]', m).click(); });
  $("#ar-none", m).onclick = () => { sel.clear(); draw(); };
  $("#ar-save", m).onclick = async () => {
    if (pages.map(p => p.id).join(",") === orig) { closeModal(); return; }
    try { await api(`/api/albums/${d.id}/order`, { ids: pages.map(p => p.id) }); closeModal(); clearSel(); toast("已儲存頁序", { label: "撤銷", fn: undo }); render(); }
    catch (e) { toast(e.message); }
  };
  draw();
}


/* 分段標題的「內文」：從網站匯入的貼文（Pixiv 多頁…）的標題、日期、內文、原文連結（b88） */
let _postBound = false;
function bindPostText() {
  if (_postBound) return;
  _postBound = true;
  document.addEventListener("click", e => {
    const b = e.target.closest && e.target.closest("[data-bkpost]");
    if (!b || S.view !== "album") return;
    e.preventDefault(); e.stopPropagation();
    const p = (S.album?.books || [])[+b.dataset.bkpost]?.post;
    if (!p) return;
    modal(`<h3>${esc(p.title || "（沒有標題）")}</h3><div class="mbody">
      <div class="mute" style="font-size:var(--fs1);margin-bottom:10px">${[p.published ? esc(String(p.published).replace("T", " ").slice(0, 16)) : "", p.fee ? `¥${p.fee} 方案` : "",
        (p.tags || []).map(t => "#" + esc(t)).join(" ")].filter(Boolean).join(" · ")}</div>
      <div class="posttext">${esc(p.text || "（這篇沒有內文）")}</div></div>
      <div class="mfoot">${p.url ? `<a class="btn" href="${esc(safeUrl(p.url))}" target="_blank" rel="noopener">打開原文 ↗</a>` : ""}<button class="btn primary" data-close>關閉</button></div>`, null, "wide");
  }, true);
}
