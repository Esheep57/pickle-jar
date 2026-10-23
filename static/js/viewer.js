// ES module（2026-10）：用到別的檔案的東西都要在這裡 import；瀏覽器會在載入時檢查名字對不對。
import { $, $$, IDENTITY, MTYPES, MTYPE_SHORT, OFFICIAL, S, SRC_LEGEND, TIER, api, catName, esc, fmtAgo, fmtDur, fmtN, fmtSize, safeUrl, srcBadge, store, svg, toast } from "./core.js";
import { go, loadNsfwIds, loadSidebar, quickTag } from "./shell.js";
import { loadMore, refreshInPlace, render, runSearch, sourceHTML } from "./grid.js";
import { archiveModal, confirmTrash, doAction, removeCells, shareCopy, undo } from "./actions.js";
import { dropImages } from "./review.js";
import { pickTag } from "./picker.js";
import { workModal } from "./work.js";
import { gifModal, quickGif } from "./gif.js";
import { srcUrlModal } from "./srcurl.js";
/* ============ 大圖檢視 ============ */
export let V = null;
export async function openViewer(id) {
  V = { id };
  const vw = $("#viewer"); vw.classList.remove("hidden");
  let d;
  try { d = await api(`/api/media/${id}`); }
  catch (e) { if (V && V.id === id) { closeViewer(); toast("打不開這張圖：" + e.message); } return; }
  if (!V || V.id !== id) return;
  V.d = d;
  const media = d.kind === "video" ? `<video src="/file/${id}" poster="/t/${id}?size=l" controls autoplay loop></video><div class="vfail hidden">這個影片格式瀏覽器播不了（常見是 iPhone 的 HEVC）。<button class="btn sm" id="v-sys2">用系統播放器開啟</button></div>`
    : `<div class="zw"><img src="/file/${id}?display=1${d.upgrade?.done_at ? "&u=" + Math.round(d.upgrade.done_at) : ""}" draggable="false"></div>`;
  const rs = d.rating_scores || {};
  const byCat = { character: [], artist: [], copyright: [], custom: [], general: [], rej: [], cand: [] };
  const qnames = new Set((S.quick || []).map(q => q.name));
  const qon = new Set(d.tags.filter(t => t.state === 1 && qnames.has(t.name)).map(t => t.name));
  d._qon = [...qon];
  d.tags = d.tags.filter(t => !(t.category === "custom" && t.state === 1 && qnames.has(t.name)));   // 快速標籤在上面那排按鈕，不在「自訂」重複列
  d.tags.forEach(t => (t.state < 0 ? byCat.rej : (IDENTITY.includes(t.category) && t.state === 0) ? byCat.cand : (byCat[t.category] || byCat.general)).push(t));
  byCat.cand.sort((a, b) => (a.tier || 9) - (b.tier || 9));
  // 本體＋造型都是候選：合成一列（確認造型時後端會一起確認本體）。以前要按兩次
  const candNames = new Set(byCat.cand.map(t => t.name));
  const mergedBase = new Map();     // 本體 → 造型
  byCat.cand.forEach(t => { if (t.base && candNames.has(t.base) && !mergedBase.has(t.base)) mergedBase.set(t.base, t); });
  const allCands = byCat.cand.slice();
  byCat.cand = byCat.cand.filter(t => !mergedBase.has(t.name));
  V.cands = byCat.cand; V.allCands = allCands;
  const zhOn = (S.settings?.general_lang || "zh") === "zh";
  const chip = t => `<span class="chip ${t.category} ${t.state === 1 ? "c1" : ""} ${t.state < 0 ? "rej" : ""}" data-t="${esc(t.name)}">
    ${srcBadge(t)}${t.state === 0 && IDENTITY.includes(t.category) ? `<span class="cat ${esc(t.category)}">${catName(t.category)}</span> ` : ""}<span class="nm" title="${esc([(t.zh && zhOn) || (t.display && t.display !== t.name) ? t.name : "", t.reason || ""].filter(Boolean).join(" · "))}">${esc(t.display || (t.zh && zhOn ? t.zh : t.name))}</span>${t.state === 0 && t.tier ? `<span class="sc">${esc(TIER[t.tier] || "")}</span>` : t.score != null && t.state === 0 ? `<span class="sc">${Math.round(t.score * 100)}</span>` : ""}
    ${t.state === 1 && (t.category === "character" || t.category === "artist") ? `<button data-cover="1" title="設為${t.category === "artist" ? "這位創作者" : "這個角色"}的封面">★</button>` : ""}${t.state === 1 && t.category === "copyright" ? `<button data-cover="series" title="設為這部作品的封面（書架和作品頁的第一張）">★</button>` : ""}
    ${t.state < 0 ? `<button data-op="unreject" title="取消否決">↺</button>` : `${t.state === 0 ? `<button data-op="confirm" title="確認">✓</button>` : ""}<button data-op="reject" title="不是">✕</button>`}</span>`;
  const grp = (k, label, fold) => byCat[k].length ? (fold
    ? `<details class="fold"><summary class="grp-title">${label} <span>${byCat[k].length}</span></summary><div class="chips">${byCat[k].map(chip).join("")}</div></details>`
    : `<div class="grp-title">${label} <span>${byCat[k].length}</span></div><div class="chips">${byCat[k].map(chip).join("")}</div>`) : "";
  const nsfwState = d.nsfw_manual == null ? "auto" : String(d.nsfw_manual);
  const noIdent = !byCat.character.length && !byCat.artist.length && !byCat.copyright.length && !byCat.cand.length;
  // 身分列（浮在圖的下緣）：角色 · 創作者 · 作品 · 候選 · 快速標籤；其他資訊在「詳細」抽屜（I）
  // 身分字幕（圖下面一行字）：角色 · 角色 — 創作者 · 作品；候選綠字加「？」；★ ✕ 滑過名字才出現。快速標籤收進「詳細」
  const capChip = t => chip(t);
  // 第幾個候選（b93）：Shift＋數字＝是、Alt＋數字＝不是（數字鍵本身是快速標籤）
  const candNum = t => { const n = byCat.cand.indexOf(t); return n >= 0 && n < 9 && byCat.cand.length > 1 ? `<kbd class="cnum" title="Shift+${n + 1} 是、Alt+${n + 1} 不是">${n + 1}</kbd>` : ""; };
  const candChip = t => `<span class="chip crow ${t.category}" data-t="${esc(t.name)}" title="${esc([catName(t.category), TIER[t.tier] || "", t.score != null ? Math.round(t.score * 100) + "%" : "", [...mergedBase].some(([, v]) => v === t) ? "確認造型時本體一起確認" : "", t.reason || ""].filter(Boolean).join(" · "))}"><span class="nm">${candNum(t)}${esc(t.display || t.name)}？</span><button data-op="confirm" title="是（確認）">✓</button><button data-op="reject" title="不是">✕</button></span>`;
  // 字幕最多列幾個名字：角色、候選各 5 個（另一種沒有時 6 個）、創作者和作品各 2 個；多的收進「還有 N 個」往上開的浮層，
  // 一張「Alice 大集合」有上百個角色也不會把圖擠掉
  const DOT = '<span class="dot">·</span>';
  const capGroup = (items, render, max, unit) => !items.length ? "" : items.slice(0, max).map(render).join(DOT)
    + (items.length > max ? `<details class="capmore"><summary>還有 ${items.length - max} ${unit}</summary><div class="capmorebox">${items.slice(max).map(render).join("")}</div></details>` : "");
  const nC = byCat.cand.length ? 5 : 6, nK = byCat.character.length ? 5 : 6;
  const left = capGroup(byCat.character, capChip, nC, "個角色");
  const cands = capGroup(byCat.cand, candChip, nK, "個候選");
  const right = [capGroup(byCat.artist, capChip, 2, "位創作者"), capGroup(byCat.copyright, capChip, 2, "部作品")].filter(Boolean).join(DOT);
  const qonList = [...qon];
  const idbar = `<div class="idbar cap${noIdent ? " empty" : ""}" id="v-id">
    <div class="capline">${left}${cands ? `${left ? DOT : ""}<span class="candbox">${cands}</span>` : ""}${right ? `<span class="dash">—</span>${right}` : ""}${noIdent ? `<span class="capnone">還沒有身分</span>` : ""}
      <span class="capadd"><button class="addc" data-vadd="character" title="加角色（A）">＋角色</button><button class="addc" data-vadd="artist" title="加創作者（R）">＋創作者</button><button class="addc" data-vadd="copyright" title="加作品">＋作品</button><button class="addc" data-vadd="custom" title="編輯標籤（G）">＋標籤</button></span></div>
    <div class="capsub">${byCat.cand.length ? `<span class="ckeys">第一個候選：<kbd>Enter</kbd> 是 <kbd>⌫</kbd> 不是${byCat.cand.length > 1 ? `　第 N 個：<kbd>Shift</kbd>+數字 是 <kbd>Alt</kbd>+數字 不是` : ""}${byCat.cand.length > 1 ? `　<button class="lnkb" id="v-allyes" title="這張的候選全部確認（多人圖一次標完）"><kbd>Shift</kbd>+<kbd>Enter</kbd> 全部是</button>` : ""}</span>` : ""}${qonList.length ? `<span class="capq" title="快速標籤（數字鍵加上／拿掉）">${qonList.map(esc).join("、")}</span>` : ""}${crowdRow(d)}${artistRow(d, byCat)}${d.pixiv_work?.pages >= 2 ? `<button class="lnkb vwork" id="v-work" title="Pixiv 的標籤是整篇共用的：多頁作品每一頁都會被標上全部角色。在這裡一頁一頁改">整篇貼文 ${d.pixiv_work.pages} 頁：逐頁標角色</button>` : ""}</div>
  </div>`;
  const qrow = (S.quick || []).length ? `<section class="vcard"><div class="dch"><b>快速標籤</b><span>按 1–${S.quick.length} 加上／拿掉</span></div><div class="qrow">${S.quick.map((q, i) => `<button class="qt ${qon.has(q.name) ? "on" : ""}" data-qt="${i}" title="按 ${i + 1} 加上／拿掉">${qon.has(q.name) ? "✓ " : ""}${esc(q.name)} <kbd>${i + 1}</kbd></button>`).join("")}</div></section>` : "";
  vw.classList.toggle("drawer", store.get("vDrawer", false));
  vw.innerHTML = `<div class="stage">${media}<button class="close" title="Esc">✕</button><button class="nav prev">‹</button><button class="nav next">›</button>
    <div class="vtop"><span class="vpos">${(() => { const ids = [...new Set(S.items.map(x => x.id))], k = ids.indexOf(id); return k >= 0 ? `${k + 1} / ${fmtN(S.view === "review" ? ids.length : (S.total || ids.length))}` : ""; })()}</span>
      ${d.upgrade?.status === "offer" ? `<button class="vdbtn vup" id="v-uppill" title="出處有同一張、比較大的版本：${d.upgrade.width}×${d.upgrade.height}（你的 ${d.width}×${d.height}）">↑ 高清</button>` : ""}
      <button class="vdbtn" id="v-drawer" title="詳細：分類、來源、描述標籤、檔案（I）">詳細 <kbd>I</kbd></button></div>
    <div class="vtools">${d.kind === "image" ? `<button id="v-zoom" title="放大／縮回（Z），滾輪縮放、拖曳移動">${svg("zoomin", 18)}</button>` : ""}<button id="v-share" title="分享：複製這張，到 QQ、Messenger 的聊天視窗按 Ctrl+V（Ctrl+C）">${svg("share", 18)}</button>${d.kind === "video" ? `<button id="v-gif" title="轉成 GIF：選一段，存進圖庫或複製分享（Shift＋點：整部直接轉好複製，Ctrl+Shift+C）">${svg("gif", 18)}</button>` : ""}<button id="v-sys" title="用系統預設程式開啟${d.kind === "video" ? "（你習慣的播放器）" : ""}">${svg("external", 18)}</button></div>
    ${idbar}</div>
   <div class="info">
    <div class="dhero"><div class="dfn" title="${esc(d.path)}">${esc(d.name)}</div>
      <div class="dchips"><span>${d.width || "?"}×${d.height || "?"}</span><span>${fmtSize(d.size)}</span>${d.kind === "video" ? `<span>${fmtDur(d.duration)}</span>` : ""}
        ${d.media_type && d.media_type !== "illustration" && !d.official ? `<span title="${d.media_src === "manual" ? "你標的媒材" : "AI 判斷的媒材" + (d.media_conf != null ? `（信心 ${Math.round(d.media_conf * 100)}%）` : "")}">${esc(MTYPES[d.media_type] || d.media_type)}</span>` : ""}
        ${d.official ? `<span>${esc(OFFICIAL[d.official] || d.official)}</span>` : ""}
        ${d.ai_gen ? `<span class="hl" title="檔案裡有生成工具留下的資料">AI 生成 · ${esc(d.ai_gen)}</span>` : ""}
        <span class="${(d.nsfw_manual ?? d.nsfw_auto) ? "nsfw" : ""}">${(d.nsfw_manual ?? d.nsfw_auto) ? "NSFW" : "安全"}</span></div>
      ${(d.albums || []).map(a => `<a class="dalbum" data-goalbum="${a.id}">${svg("albums", 14)} ${esc(a.title)}<span>第 ${a.page} 頁</span></a>`).join("")}</div>
    ${qrow}
    <section class="vcard"><div class="dch"><b>分類</b></div>
      <div class="drow"><span>NSFW <kbd>N</kbd></span><span class="seg" id="v-nsfw">${[["auto", `自動（${d.nsfw_auto ? "NSFW" : "安全"}）`], ["1", "NSFW"], ["0", "安全"]].map(([v, l]) => `<button data-v="${v}" class="${v === nsfwState ? "on" : ""}">${l}</button>`).join("")}</span></div>
      ${d.kind === "image" ? `<div class="drow"><span>官方 <kbd>K</kbd></span><span class="seg" id="v-official">${[["", "不是"], ...Object.entries(OFFICIAL)].map(([k, l]) => `<button data-k="${k}" class="${k === (d.official || "") ? "on" : ""}" ${k === (d.official || "") && d.official_src && d.official_src !== "manual" ? `title="${d.official_src === "trace" ? "trace.moe 判斷的" : "Danbooru 標的"}，按其他的可以改"` : ""}>${l.replace("截圖", "").replace("插圖", "")}</button>`).join("")}</span></div>` : ""}
      <div class="drow dstack"><span>媒材 <kbd>M</kbd></span><span class="seg" id="v-mtype">${Object.entries(MTYPE_SHORT).map(([k, l]) => {
        const on = k === d.media_type, mine = on && d.media_src === "manual";
        const tip = MTYPES[k] + (on ? (mine ? "：你標的" : `：AI 判斷${d.media_conf != null ? `，信心 ${Math.round(d.media_conf * 100)}%` : ""}，點一下確認`) : "");
        return `<button data-k="${k}" class="${on ? "on" : ""}${on && !mine ? " ai" : ""}" title="${tip}">${l}</button>`; }).join("")}${d.media_src === "manual" ? `<button data-k="" title="拿掉你標的，交給 AI">自動</button>` : ""}</span></div>
      ${grp("custom", "自訂標籤")}</section>
    <section class="vcard dsrc"><div class="dch"><b>出處</b>${S.remote ? "" : `<button class="ghost sm" id="v-srcurl" title="知道是哪篇 X／Pixiv 貼文：貼網址，比對後記出處、帶上作者和角色">貼網址</button>`}${d.source && d.source.status !== "error" ? `<button class="ghost sm" id="v-src2" title="重新找出處（S）">重找 <kbd>S</kbd></button>` : ""}</div>${sourceHTML(d)}${upHTML(d)}</section>
    ${d.kind === "image" ? `<section class="vcard"><div class="dch"><b>找出處的步驟</b><span>新圖自動跑過的</span></div>
      <div class="tline" id="v-trace"><div class="mute">載入中…</div></div>
      <div class="dsub">自己找 <span>點了才會上傳這一張</span></div>
      <div class="rsgrid">${RSEARCH.map(e => `<button class="rsb" data-rs="${e.k}" title="${esc(e.tip)}">${esc(e.name)}</button>`).join("")}
        <button class="rsb" data-site="e621" title="用檔案指紋查 e621 上有沒有同一個原檔（不上傳圖片）">e621 原檔</button><button class="rsb" data-site="e621iqdb" title="上傳縮小的這一張到 e621 找相似圖（furry 圖最準）">e621 以圖</button><button class="rsb" data-site="r34" title="用檔案指紋查 Rule34 上有沒有同一個原檔（不上傳圖片）">Rule34 原檔</button></div>
      <div class="rsextra">${r34Link(d)}<details class="rsman"><summary>其他網站…</summary><div><a class="btn sm" href="/file/${id}" download title="下載原檔，再拖進下面的網站">下載這張</a>${RSEARCH_MANUAL.map(([n, u]) => `<a class="lnk" href="${u}" target="_blank" rel="noopener">${n} ↗</a>`).join("")}
        <span class="mute">這幾個不接受程式送圖：下載後把檔案拖進網頁</span></div></details></div>
      <div id="v-rsres" class="rsres"></div></section>` : ""}
    ${aiMetaHTML(d)}
    <section class="vcard"><div class="dch"><b>AI 看到的</b><span>WD Tagger</span></div>
      <div class="rstack" title="分級（模型）">${["general", "sensitive", "questionable", "explicit"].map(k => `<i class="r-${k} ${k === d.rating ? "on" : ""}" style="flex-grow:${Math.max(0.001, rs[k] || 0)}" title="${k} ${Math.round((rs[k] || 0) * 100)}%"></i>`).join("")}</div>
      <div class="rlegend">${["general", "sensitive", "questionable", "explicit"].map(k => `<span class="${k === d.rating ? "on" : ""}"><i class="r-${k}"></i>${k} ${Math.round((rs[k] || 0) * 100)}%</span>`).join("")}</div>
      ${grp("general", "描述標籤", true)}${grp("rej", "已否決", true)}</section>
    <section class="vcard dfile"><div class="dch"><b>檔案</b></div><div class="meta">${esc(d.path)}${d.error ? `<br><span class="bad">${esc(d.error)}</span>` : ""}</div>
    <div class="dbtns"><button class="btn sm" id="v-reveal">在資料夾中顯示</button><button class="btn sm" id="v-open">在新分頁開原檔</button><button class="btn sm" id="v-retag">重新標記</button>${/[\\/](iCloud Photos|iCloudPhotos|iCloud Drive|iCloudDrive)[\\/]/i.test(d.path) ? `<button class="btn sm" id="v-archive" title="留在電腦、iCloud 刪掉">移出 iCloud</button>` : ""}</div></section>
   </div>`;
  $("#v-drawer", vw).onclick = toggleVDrawer;
  bindUpgrade(vw, d, id);
  const fitId = () => { const ib = $("#v-id", vw); if (ib) vw.style.setProperty("--idh", ib.offsetHeight + "px"); };   // 圖放在身分列上面，不要被蓋住
  fitId(); requestAnimationFrame(fitId);
  $(".close", vw).onclick = closeViewer;
  const vai = $("#v-ai", vw);
  if (vai) {
    vai.open = store.get("aiOpen", true); vai.addEventListener("toggle", () => store.set("aiOpen", vai.open));
    $$("[data-copy]", vai).forEach(b => b.onclick = async () => { try { await navigator.clipboard.writeText(d.ai_meta.prompt); toast("已複製提示詞"); } catch { toast("複製失敗"); } });
    $$("[data-aiq]", vai).forEach(a => a.onclick = () => { closeViewer(); runSearch(a.dataset.aiq.replace(/\s+/g, "_")); });
  }
  $$("[data-goalbum]", vw).forEach(a => a.onclick = () => { closeViewer(); go("album", a.dataset.goalbum); });
  const adn = $("#v-artist-done", vw); if (adn) adn.onclick = async () => {
    await api("/api/media/artist-done", { ids: [id], value: !d.artist_done });
    toast(d.artist_done ? "取消「不用再找作者」：會回到缺創作者" : "不用再找作者：不再列在缺創作者", { label: "撤銷", fn: undo });
    markDirty(); openViewer(id);
  };
  $$(".qrow [data-qt]", vw).forEach(b => b.onclick = () => quickTag(+b.dataset.qt, [id]));
  $(".prev", vw).onclick = () => stepViewer(-1);
  $(".next", vw).onclick = () => stepViewer(1);
  $(".stage", vw).addEventListener("click", e => { if (e.target.classList.contains("stage")) closeViewer(); });
  $("#v-share").onclick = () => shareCopy([id]);
  const vg = $("#v-gif", vw); if (vg) vg.onclick = e => { const v = $(".stage video", vw); if (e.shiftKey) return quickGif(id, d); if (v) v.pause(); gifModal(id, d, v ? v.currentTime : 0); };
  $("#v-reveal").onclick = () => api(`/api/media/${id}/reveal`, {});
  $$(".pixtags [data-ptag]", vw).forEach(b => b.onclick = () => pickTag({ title: `Pixiv 標籤「${b.dataset.ptag}」是哪個角色？`, category: "character", only: "character", ids: [id] }, async t => {
    const r = await api("/api/online/pixiv-map", { pixiv_tag: b.dataset.ptag, character: t.name });
    toast(`已記住：${b.dataset.ptag} → ${t.name}（套用到 ${r.applied} 筆 Pixiv 作品）`); openViewer(id); markDirty();
  }));
  const vs = $("#v-src") || $("#v-src2", vw); if (vs) vs.onclick = srcAgain;
  const vsu = $("#v-srcurl", vw); if (vsu) vsu.onclick = () => srcUrlModal([id], () => { markDirty(); if (V?.id === id) openViewer(id); });   // 還沒找過＝出處卡片裡的大按鈕；找過＝標題列的「重找」
  async function srcAgain() {
    vs.disabled = true;
    if (vs.classList.contains("icon-btn")) {   // 小的 ↻ 按鈕：轉圈圈＋旁邊顯示文字，不要把字塞進按鈕裡（會變成直排）
      vs.classList.add("spinning");
      vs.insertAdjacentHTML("afterend", `<span class="srcbusy">搜尋中…</span>`);
    } else vs.textContent = "搜尋中…";
    try { await api("/api/online/source", { ids: [id], force: true }); await waitSource(id, d.source?.fetched_at); } catch (e) { toast(e.message); }
    if (V?.id === id) openViewer(id); markDirty();
  };
  const applySrc = async (list) => {
    for (const t of list) await api("/api/tags/apply", { ids: [id], action: "add", tag: t.name, category: t.category });
    toast(`已套用 ${list.map(t => t.name).join(", ")}`, { label: "撤銷", fn: undo }); openViewer(id); markDirty();
  };
  $$("[data-src-add]", vw).forEach(b => b.onclick = e => { e.stopPropagation(); const c = b.closest(".chip"); applySrc([{ name: c.dataset.t, category: c.dataset.c }]); });
  const va = $("#v-src-all"); if (va) va.onclick = () => applySrc($$(".srcbox .chip", vw).filter(c => !c.classList.contains("c1")).map(c => ({ name: c.dataset.t, category: c.dataset.c })));
  $("#v-open").onclick = () => window.open(`/file/${id}`, "_blank");
  $$("[data-rs]", vw).forEach(b => b.onclick = () => reverseSearch(b.dataset.rs, id));
  const vm = $("#v-more", vw);
  if (vm) { vm.open = store.get("vmore", false); vm.addEventListener("toggle", () => store.set("vmore", vm.open)); }
  const tr = $("#v-trace", vw);
  if (tr) (async () => {          // 處理紀錄：時間軸（✓ 找到、✕ 查過沒有、– 沒做），打開大圖就畫
    try {
      const t = await api(`/api/media/${id}/trace`);
      if (!tr.isConnected) return;
      tr.innerHTML = t.steps.map(x => `<div class="tl ${x.ok === true ? "ok" : x.ok === false ? "no" : "na"}"><i>${x.ok === true ? "✓" : x.ok === false ? "✕" : ""}</i>
        <div><b>${esc(x.step)}</b><span>${esc(x.result)}${x.at ? ` · ${fmtAgo(x.at)}` : ""}</span></div></div>`).join("");
    } catch (e) { tr.textContent = e.message; }
  })();
  $$("[data-site]", vw).forEach(b => b.onclick = async () => {
    const k = b.dataset.site, label = b.textContent;
    const url = { e621: `/api/online/e621/check/${id}`, e621iqdb: `/api/online/e621/iqdb/${id}`, r34: `/api/online/r34/check/${id}` }[k];
    const site = k === "r34" ? "Rule34" : "e621";
    b.disabled = true; b.textContent = "查詢中…";
    try {
      const r = await api(url, {});
      if (!r.found) toast(k === "e621iqdb" ? (r.best ? `e621 最像的只有 ${r.best.score}%（#${r.best.post_id}），不採用` : "e621 找不到相似的圖")
        : `${site} 上沒有同一個原檔（重新存過、截圖的圖對不上${k === "e621" ? "，可以試「e621 以圖搜圖」" : ""}）`);
      else { toast(`找到 ${site} #${r.post_id}${r.score ? `（${r.score}%）` : ""}${r.added ? `，${r.added} 個標籤放進候選` : ""}`); if (V?.id === id) openViewer(id); markDirty(); return; }
    } catch (e) { toast(e.message); }
    b.disabled = false; b.textContent = label;
  });
  const rsRow = $(".rsearch", vw);
  if (rsRow) ["pointerenter", "touchstart", "focusin"].forEach(ev => rsRow.addEventListener(ev, () => rsPrepare(id), { passive: true }));
  const vid = $(".stage video", vw);
  if (vid) {
    const bad = () => { $(".vfail", vw).classList.remove("hidden"); };
    vid.addEventListener("error", bad);
    vid.addEventListener("loadedmetadata", () => { if (!vid.videoWidth) bad(); });  // 有聲音沒畫面 = 解不了 HEVC
    const s2 = $("#v-sys2", vw); if (s2) s2.onclick = () => api(`/api/media/${id}/open`, {});
  }
  const vim = $(".zw img", vw);
  if (vim) vim.addEventListener("error", () => { $(".zw", vw).outerHTML = `<div class="vfail">讀不到原檔（可能被搬走、刪除，或 iCloud 還沒下載）。<button class="btn sm" id="v-sys3">用系統程式開啟</button></div>`;
    const s3 = $("#v-sys3", vw); if (s3) s3.onclick = () => api(`/api/media/${id}/open`, {}).catch(e => toast(e.message)); });
  const varc = $("#v-archive"); if (varc) varc.onclick = () => archiveModal([id]);
  $("#v-sys").onclick = () => api(`/api/media/${id}/open`, {}).catch(e => toast(e.message));
  const zb = $("#v-zoom"); if (zb) zb.onclick = toggleZoom;
  initZoom();
  $("#v-retag").onclick = async () => { await api("/api/jobs", { kind: "retag", ids: [id] }); toast("已排入重新標記"); };
  const vca = $("#v-crowd-add"); if (vca) vca.onclick = () => pickTag({ title: "這張還有誰？", category: "character", ids: [id] }, async t => {
    await api("/api/tags/apply", { ids: [id], action: "add", tag: t.name, category: t.category }); toast(`加上 ${t.name}`, { label: "撤銷", fn: undo }); openViewer(id); markDirty();
  });
  $$("[data-crowd-sug]", vw).forEach(b => b.onclick = async () => {
    await api("/api/tags/apply", { ids: [id], action: "add", tag: b.dataset.crowdSug, category: "character" }); toast(`加上 ${b.dataset.crowdSug}`, { label: "撤銷", fn: undo }); openViewer(id); markDirty();
  });
  const vcd = $("#v-crowd-done"); if (vcd) vcd.onclick = async () => {
    await api("/api/media/people-done", { ids: [id], value: !d.crowd.done }); toast(d.crowd.done ? "重新提醒這張還有誰" : "這張不用再找角色（不再提醒）", { label: "撤銷", fn: undo }); openViewer(id); markDirty();
  };
  $$("#v-mtype button").forEach(b => b.onclick = async () => {
    await api("/api/media/mediatype", { ids: [id], label: b.dataset.k || null });
    toast(b.dataset.k ? `媒材：${MTYPES[b.dataset.k]}` : "媒材改回自動", { label: "撤銷", fn: undo }); openViewer(id); markDirty();
  });
  $$("#v-official button").forEach(b => b.onclick = async () => {
    await api("/api/media/official", { ids: [id], kind: b.dataset.k || null }); openViewer(id); markDirty();
  });
  $$("#v-nsfw button").forEach(b => b.onclick = async () => {
    const value = b.dataset.v === "auto" ? null : +b.dataset.v;
    await api("/api/media/nsfw", { ids: [id], value }); openViewer(id); markDirty();
  });
  const chipClick = async e => {
    const c = e.target.closest(".chip"); if (!c) return;
    const b = e.target.closest("button");
    if (b && b.dataset.cover === "series") { await api(`/api/series/${encodeURIComponent(c.dataset.t)}/cover`, { media_id: id }); S.seriesData = null; toast(`已設為「${c.dataset.t}」的作品封面`); return; }
    if (b && b.dataset.cover) { await api(`/api/tag/${encodeURIComponent(c.dataset.t)}`, { cover: id }); toast(`已設為 ${c.dataset.t} 的封面`); return; }
    if (b && !b.dataset.op) return;
    if (b) {
      await api("/api/tags/apply", { ids: [id], action: b.dataset.op, tag: c.dataset.t }); markDirty();
      // 否決了 Pixiv 標的角色、而且這篇作品有好幾頁：多半是整篇共用標籤的問題 → 提示一次整理
      const src = (V?.d?.tags || []).find(t => t.name === c.dataset.t)?.source;
      if (b.dataset.op === "reject" && src === "pixiv" && V?.d?.pixiv_work?.pages >= 2)
        toast(`這篇 Pixiv 作品有 ${V.d.pixiv_work.pages} 頁，角色可能每頁都被標上了`, { label: "逐頁整理", fn: () => workModal(id, () => { markDirty(); if (V?.id === id) openViewer(id); }) });
      if (b.dataset.op === "confirm" || b.dataset.op === "reject") return afterDecide(id);
      openViewer(id);
    }
    else if (e.target.closest(".nm")) { closeViewer(); go("tag", c.dataset.t); }
  };
  const ay = $("#v-allyes", vw); if (ay) ay.onclick = () => vKey("allyes");
  const vwk = $("#v-work", vw); if (vwk) vwk.onclick = () => workModal(id, () => { markDirty(); if (V?.id === id) openViewer(id); });
  $(".info", vw).addEventListener("click", chipClick);
  $("#v-id", vw).addEventListener("click", chipClick);
  $$("[data-vadd]", vw).forEach(b => b.onclick = () => pickTag({ title: `這張加上${catName(b.dataset.vadd)}…`, category: b.dataset.vadd, ids: [id] }, async t => {
    await api("/api/tags/apply", { ids: [id], action: "add", tag: t.name, category: t.category }); markDirty(); if (V?.id === id) openViewer(id);
  }));
}
// 這張的候選都處理完了（而且多人圖沒有人還沒標）→ 自動跳下一張。只在待確認頁：
// 在圖庫的大圖裡標標籤時一直跳走很煩（使用者回報），留在這張，自己按 → 換下一張
async function afterDecide(id) {
  const nd = await api(`/api/media/${id}`).catch(() => null);
  if (V?.id !== id) return;
  if (S.view === "review" && nd && !nd.tags.some(t => t.state === 0 && IDENTITY.includes(t.category)) && !nd.crowd?.short) {
    const ids = [...new Set(S.items.map(x => x.id))], i = ids.indexOf(id), nx = ids[i + 1];
    if (i >= 0 && nx != null) { toast("這張處理完了，下一張", { label: "回上一張", fn: () => openViewer(id) }); return openViewer(nx); }
  }
  openViewer(id);
}
// 大圖的鍵盤：Enter／⌫ 決定第一個候選、Shift+Enter 全部是、A 加角色、R 創作者、G 標籤、N NSFW、K 官方、S 搜來源
let vBusy = false;
export async function vKey(k) {
  if (!V?.d || vBusy) return false;
  const id = V.id, vw = $("#viewer");
  const click = sel => { const el = $(sel, vw); if (el) el.click(); return !!el; };
  if (typeof k === "object" && k?.pick != null) {     // Shift／Alt＋數字：第 N 個候選（b93）
    const t = (V.cands || [])[k.pick]; if (!t) return false;
    return decideCand(id, t, k.yes ? "yes" : "no");
  }
  if (k === "yes" || k === "no") {
    const t = (V.cands || [])[0]; if (!t) return false;
    return decideCand(id, t, k);
  }
  return vKey2(k, id, vw, click);
}
async function decideCand(id, t, k) {
  vBusy = true;
  try {
    await api("/api/tags/apply", { ids: [id], action: k === "yes" ? "confirm" : "reject", tag: t.name });
    toast(`${k === "yes" ? "是" : "不是"} ${t.display || t.name}`, { label: "撤銷", fn: undo }); markDirty(); loadSidebar();
    await afterDecide(id);
  } catch (e) { toast(e.message); } finally { vBusy = false; }
  return true;
}
async function vKey2(k, id, vw, click) {
  if (k === "allyes") {
    const all = V.allCands || []; if (!all.length) return false;
    vBusy = true;
    try {
      const r = await api("/api/review/decide", { pairs: all.map(t => [id, t.name]), accept: true });
      toast(`這張全部是：${all.map(t => t.display || t.name).join("、")}`, { label: "撤銷", fn: undo }); markDirty(); loadSidebar();
      if (r.followup?.length) openViewer(id); else await afterDecide(id);
    } catch (e) { toast(e.message); } finally { vBusy = false; }
    return true;
  }
  if (k === "trash") {        // 刪除這張：問過再丟資源回收筒，接著看下一張（沒有下一張就關掉）
    const ids = [...new Set(S.items.map(x => x.id))], i = ids.indexOf(id);
    const nx = i >= 0 ? (ids[i + 1] ?? ids[i - 1]) : null;
    confirmTrash([id], () => {
      if (S.view === "review") dropImages([id]); else removeCells([id]);
      if (nx != null) openViewer(nx); else closeViewer();
    });
    return true;
  }
  // 跟格子同一張鍵表：L／X 這張不用整理、B 本子、O 原創
  if (k === "l" || k === "x" || k === "b" || k === "o") {
    await doAction({ l: "skip", x: "skip", b: "album", o: "original" }[k], [id]);
    if (k !== "b") { markDirty(); if (V?.id === id) openViewer(id); }
    return true;
  }
  if (k === "a") return click('[data-vadd="character"]');
  if (k === "r") return click('[data-vadd="artist"]') || (pickTag({ title: "這張的創作者是…", category: "artist", only: "artist", ids: [id] }, async t => {
    await api("/api/tags/apply", { ids: [id], action: "add", tag: t.name, category: t.category }); markDirty(); if (V?.id === id) openViewer(id); }), true);
  if (k === "g") return click('[data-vadd="custom"]');
  if (k === "s") { if (!vw.classList.contains("drawer")) toggleVDrawer(); return click("#v-src"); }
  if (k === "n") {
    const value = V.d.nsfw ? 0 : 1;
    await api("/api/media/nsfw", { ids: [id], value }); loadNsfwIds(true);
    toast(value ? "標為 NSFW" : "標為安全", { label: "撤銷", fn: undo }); markDirty(); openViewer(id); return true;
  }
  if (k === "m") { if (!vw.classList.contains("drawer")) toggleVDrawer(); const el = $("#v-mtype", vw); if (el) { el.scrollIntoView({ block: "center", behavior: "smooth" }); el.classList.add("flash"); setTimeout(() => el.classList.remove("flash"), 1200); } return !!el; }
  if (k === "k") { if (!vw.classList.contains("drawer")) toggleVDrawer(); const el = $("#v-official", vw); if (el) { el.scrollIntoView({ block: "center", behavior: "smooth" }); el.classList.add("flash"); setTimeout(() => el.classList.remove("flash"), 1200); } return !!el; }
  return false;
}
// 換成高清版（app/upgrade.py）：出處有同一張、比較大的 → 換／不用；換過的顯示原本多大；任何圖都可以貼貼文網址換
function upHTML(d) {
  if (d.kind !== "image") return "";
  const u = d.upgrade || {};
  const post = u.post_url ? ` <a class="lnk" href="${esc(safeUrl(u.post_url))}" target="_blank" rel="noopener">原貼文 ↗</a>` : "";
  const box = u.status === "offer" ? `<div class="upbox offer"><b>有更高清的版本</b>
      <div>你的 ${d.width}×${d.height} → ${esc(u.site_name || "")} <b>${u.width}×${u.height}</b>（${(Math.max(u.width, u.height) / Math.max(d.width, d.height)).toFixed(1)} 倍）${post}</div>
      <div class="mute">換之前會下載下來逐像素確認是同一張；標記全部保留，舊檔丟資源回收筒。</div>
      <div class="upact"><button class="btn sm primary" id="v-up-do">換成高清</button><button class="btn sm" id="v-up-no">不用換</button></div></div>`
    : u.status === "done" ? `<div class="upbox done">已換成 ${esc(u.site_name || "")} 高清版${u.old_w ? `（原本 ${u.old_w}×${u.old_h}）` : ""}${post}</div>`
    : u.status === "failed" ? `<div class="upbox failed"><b>上次換高清沒換成</b><div class="mute">${esc(u.error || "")}</div>
      <div class="upact"><button class="btn sm" id="v-up-do">再試一次</button><button class="btn sm" id="v-up-no">不用換</button></div></div>` : "";
  return `${box}<details class="fold upurl" id="v-upurl"><summary class="grp-title">換成高清版 <span>貼貼文網址</span></summary>
    <div class="upform"><input id="v-up-url" placeholder="X、Pixiv、Danbooru、e621、Rule34 的貼文網址" autocomplete="off"><button class="btn sm" id="v-up-chk">檢查</button>
    ${u.status !== "offer" && d.source?.status === "found" ? `<button class="btn sm ghost" id="v-up-re" title="用已知的出處再查一次">重查出處</button>` : ""}</div>
    <div class="mute" id="v-up-msg">${u.status === "none" ? "出處的圖查過了，沒有比你的大。" : u.status === "dismissed" ? "你選了不用換。" : ""}</div></details>`;
}
function bindUpgrade(vw, d, id) {
  const go_ = async () => {
    const b = $("#v-up-do", vw); if (b) { b.disabled = true; b.textContent = "下載、比對中…"; }
    try {
      const r = await api(`/api/upgrade/${id}/replace`, {});
      toast(`已換成 ${r.site_name} 高清版 ${r.w}×${r.h}（原本 ${r.old[0]}×${r.old[1]}）${r.old_left ? "；舊檔沒丟進回收筒，還在資料夾裡" : "，舊檔在資源回收筒"}`);
      markDirty();
    } catch (e) { toast("沒換：" + e.message); }
    if (V?.id === id) openViewer(id);
  };
  const doB = $("#v-up-do", vw); if (doB) doB.onclick = go_;
  const no = $("#v-up-no", vw); if (no) no.onclick = async () => { await api("/api/upgrade/dismiss", { ids: [id] }); toast("這張不換高清（不再提醒）", { label: "撤銷", fn: undo }); markDirty(); openViewer(id); };
  const pill = $("#v-uppill", vw); if (pill) pill.onclick = () => {
    if (!vw.classList.contains("drawer")) toggleVDrawer();
    $(".upbox", vw)?.scrollIntoView({ block: "center", behavior: "smooth" });
  };
  const msg = $("#v-up-msg", vw);
  const show = (r) => {
    if (r.status === "offer") { toast(`有更高清的：${r.best.site_name} ${r.best.w}×${r.best.h}`); openViewer(id); return; }
    const s = (r.seen || []).map(x => `${x.w}×${x.h}`).join("、");
    msg.textContent = r.status === "dismissed" ? "你選過不用換，而且沒有找到更大的。" : `沒有比你的 ${r.local?.[0]}×${r.local?.[1]} 大的${s ? `（那邊是 ${s}）` : ""}。` + (r.errors?.length ? " " + r.errors.join("；") : "");
  };
  const chk = $("#v-up-chk", vw), inp = $("#v-up-url", vw);
  if (chk) {
    const run = async () => {
      const url = inp.value.trim(); if (!url) return inp.focus();
      chk.disabled = true; msg.textContent = "查詢中…";
      try { show(await api(`/api/upgrade/${id}/url`, { url })); } catch (e) { msg.textContent = e.message; }
      chk.disabled = false;
    };
    chk.onclick = run;
    inp.addEventListener("keydown", e => { e.stopPropagation(); if (e.key === "Enter") run(); });
  }
  const re = $("#v-up-re", vw); if (re) re.onclick = async () => {
    re.disabled = true; msg.textContent = "查詢中…";
    try { show(await api(`/api/upgrade/${id}/check`, {})); } catch (e) { msg.textContent = e.message; }
    re.disabled = false;
  };
}
// 多人圖：WD 看到幾個人、標了幾個角色；還有人沒標時給「＋ 另一個角色」和「不用再找角色」
// 沒有作者：可以標「不用找作者」（自己畫的、AI 生成、找不到也沒關係），就不會一直留在「缺創作者」
function artistRow(d, byCat) {
  if (byCat.artist.length || d.kind !== "image") return "";
  const na = new Set((S.settings?.no_artist_tags || ["梗圖", "表情包", "截圖"]));
  const byTag = d.tags.find(t => t.state === 1 && na.has(t.name)) || (d._qon || []).find(n => na.has(n));
  if (byTag && !d.artist_done) return `<div class="crowd"><span class="mute">有「${esc(byTag.name || byTag)}」標籤：不用找作者，不算缺創作者</span></div>`;
  return `<div class="crowd"><span>${d.artist_done ? "已標「不用再找作者」" : d.ai_gen ? "AI 生成的圖，沒有畫師可找" : "還沒有創作者"}</span>
    <button class="btn sm ghost" id="v-artist-done" title="${d.artist_done ? "回到缺創作者" : "表情包、梗圖、轉了好幾手的圖：不再列在缺創作者（之後還是可以標作者）"}">${d.artist_done ? "取消" : "不用再找作者"}</button></div>`;
}
// AI 生成資料：模型、LoRA、種子、提示詞（可以複製、點提示詞裡的字搜尋同樣提示詞的圖）
function aiMetaHTML(d) {
  const a = d.ai_meta; if (!a) return "";
  const p = a.params || {};
  const kv = [["工具", a.tool], ["模型", a.model], ["LoRA", (a.loras || []).join("、")], ["種子", a.seed],
    ["步數", p.Steps || p.steps], ["取樣", p.Sampler || p.sampler_name || p.sampler], ["CFG", p["CFG scale"] || p.cfg || p.scale], ["尺寸", p.Size]].filter(x => x[1] != null && x[1] !== "");
  return `<details class="fold aimeta" id="v-ai"><summary class="grp-title">AI 生成資訊 <span>${esc(a.tool || "")}${a.model ? " · " + esc(a.model.replace(/\.(safetensors|ckpt|gguf)$/i, "")) : ""}</span></summary>
    <table class="diag">${kv.map(([k, v]) => `<tr><th>${k}</th><td>${k === "模型" || k === "LoRA" ? `<a class="lnk" data-aiq="${k === "模型" ? "model" : "lora"}:${esc(String(v).split("、")[0])}" title="找同樣${k}的圖">${esc(String(v))}</a>` : esc(String(v))}</td></tr>`).join("")}</table>
    ${a.prompt ? `<div class="aiprompt"><div class="mute">提示詞 <button class="btn sm" data-copy="prompt">複製</button></div><div class="ptxt">${esc(a.prompt)}</div></div>` : ""}
    ${a.negative ? `<details class="aineg"><summary class="mute">負面提示詞</summary><div class="ptxt">${esc(a.negative)}</div></details>` : ""}</details>`;
}
function crowdRow(d) {
  const c = d.crowd; if (!c || c.people < 2) return "";
  const cands = d.tags.filter(t => t.category === "character" && t.state === 0 && t.source === "knn").map(t => t.name);
  return `<div class="crowd ${c.short ? "short" : ""}"><span>WD 看到 ${c.people} 人 · 已標 ${c.chars} 個角色${c.done ? " · 已標完" : c.site ? " · 角色是 Danbooru／Pixiv 標的，其他人當路人" : ""}</span>
    ${c.short || c.chars === 0 ? `<button class="btn sm" id="v-crowd-add">＋ 另一個角色</button>` : ""}
    ${c.chars >= 1 ? `<button class="btn sm ghost" id="v-crowd-done" title="${c.done ? "重新提醒還有人沒標" : "其他人是路人或不認識：不再提醒"}">${c.done ? "取消「不用再找角色」" : "不用再找角色"}</button>` : ""}
    ${c.short && cands.length ? `<div class="mute" style="width:100%;font-size:var(--fs1)">上面的候選裡有「另一個人」的推薦（參考）</div>` : ""}</div>`;
}
async function waitSource(id, prev) {
  for (let i = 0; i < 40; i++) {
    await new Promise(r => setTimeout(r, 1500));
    const d = await api(`/api/media/${id}`);
    if (d.source && d.source.fetched_at !== prev) return;
  }
}
export function toggleVDrawer() { const on = !store.get("vDrawer", false); store.set("vDrawer", on); $("#viewer").classList.toggle("drawer", on); }
let viewerDirty = false;
export const markDirty = () => { viewerDirty = true; };
export function stepViewer(dir) {
  if (!V) return;
  // 待確認頁一張圖有好幾個候選＝清單裡佔好幾筆：先去重，不然「下一張」永遠是同一張（以前會卡住）
  const ids = [...new Set(S.items.map(x => x.id))]; const i = ids.indexOf(V.id);
  if (i < 0) return;   // 目前這張不在清單裡（參考圖、剛被處理掉的）→ 不要跳到不相干的圖
  const nx = ids[i + dir];
  if (nx != null) openViewer(nx);
  if (dir > 0 && i + dir >= ids.length - 5) loadMore(S.gen);
}
export function closeViewer() {
  const vw = $("#viewer"), last = V?.id; vw.classList.add("hidden"); vw.innerHTML = ""; V = null;
  if (viewerDirty) { viewerDirty = false; refreshInPlace(last); loadSidebar(); }   // 原地更新、留在原本的位置（以前整頁重畫，回到最上面）
}

/* ============ 大圖縮放 / 拖曳 ============ */
const Z = { s: 1, x: 0, y: 0, drag: null, moved: false };
function applyZoom() {
  const w = $("#viewer .zw"); if (!w) return;
  w.style.transform = `translate(${Z.x}px, ${Z.y}px) scale(${Z.s})`;
  $("#viewer .stage").classList.toggle("zoomed", Z.s > 1.01);
}
function initZoom() {
  Object.assign(Z, { s: 1, x: 0, y: 0, drag: null });
  const st = $("#viewer .stage"), w = $("#viewer .zw"); if (!w) return;
  applyZoom();
  st.onwheel = e => {
    e.preventDefault();
    const r = w.getBoundingClientRect();
    const f = Math.exp(-e.deltaY * 0.0015), ns = Math.min(12, Math.max(1, Z.s * f));
    // 以滑鼠位置為中心縮放
    const cx = e.clientX - (r.left + r.width / 2), cy = e.clientY - (r.top + r.height / 2);
    Z.x -= cx * (ns / Z.s - 1); Z.y -= cy * (ns / Z.s - 1); Z.s = ns;
    if (Z.s === 1) { Z.x = 0; Z.y = 0; }
    applyZoom();
  };
  w.onmousedown = e => { if (e.button) return; e.preventDefault(); Z.drag = [e.clientX - Z.x, e.clientY - Z.y]; Z.moved = false; };
  w.ondblclick = e => { e.stopPropagation(); toggleZoom(e); };
}
window.addEventListener("mousemove", e => { if (!Z.drag) return; Z.x = e.clientX - Z.drag[0]; Z.y = e.clientY - Z.drag[1]; Z.moved = true; applyZoom(); });
window.addEventListener("mouseup", () => { Z.drag = null; setTimeout(() => { Z.moved = false; }, 0); });
export function toggleZoom(e) {
  const w = $("#viewer .zw"); if (!w) return;
  if (Z.s > 1.01) Object.assign(Z, { s: 1, x: 0, y: 0 });
  else {
    const img = $("img", w), r = img.getBoundingClientRect();
    Z.s = Math.max(2, (img.naturalWidth || r.width) / r.width);  // 放大到原始尺寸（至少 2 倍）
    if (e && e.clientX) { const cx = e.clientX - (r.left + r.width / 2), cy = e.clientY - (r.top + r.height / 2); Z.x = -cx * (Z.s - 1); Z.y = -cy * (Z.s - 1); }
  }
  applyZoom();
}


/* ============ 手機／平板：手勢 ============
   沒放大時：左右滑＝上一張／下一張，往下滑＝關閉；兩指捏＝縮放；放大後一指拖曳移動；點兩下＝放大／縮回 */
const T = { pts: null, start: null, pinch: null, lastTap: 0 };
const tdist = (a, b) => Math.hypot(a.clientX - b.clientX, a.clientY - b.clientY);
document.addEventListener("touchstart", e => {
  const st = e.target.closest && e.target.closest("#viewer .stage"); if (!st || e.target.closest("button, a, .idbar, video")) return;
  if (e.touches.length === 2) {
    T.pinch = { d: tdist(e.touches[0], e.touches[1]), s: Z.s }; T.start = null;
  } else if (e.touches.length === 1) {
    const t = e.touches[0];
    T.start = { x: t.clientX, y: t.clientY, zx: Z.x, zy: Z.y, at: Date.now() }; T.pinch = null;
  }
}, { passive: true });
document.addEventListener("touchmove", e => {
  if (!T.start && !T.pinch) return;
  if (!(e.target.closest && e.target.closest("#viewer .stage"))) return;
  if (T.pinch && e.touches.length === 2) {
    e.preventDefault();
    Z.s = Math.min(12, Math.max(1, T.pinch.s * tdist(e.touches[0], e.touches[1]) / T.pinch.d));
    if (Z.s === 1) { Z.x = 0; Z.y = 0; }
    applyZoom();
  } else if (T.start && e.touches.length === 1 && Z.s > 1.01) {
    e.preventDefault();
    const t = e.touches[0];
    Z.x = T.start.zx + t.clientX - T.start.x; Z.y = T.start.zy + t.clientY - T.start.y; applyZoom();
  }
}, { passive: false });
document.addEventListener("touchend", e => {
  if (T.pinch) { if (e.touches.length < 2) T.pinch = null; return; }
  const s0 = T.start; T.start = null;
  if (!s0 || !V) return;
  const t = e.changedTouches[0], dx = t.clientX - s0.x, dy = t.clientY - s0.y;
  if (Math.abs(dx) < 10 && Math.abs(dy) < 10 && Date.now() - s0.at < 300) {      // 點一下
    if (Date.now() - T.lastTap < 300) { T.lastTap = 0; toggleZoom({ clientX: t.clientX, clientY: t.clientY }); }
    else T.lastTap = Date.now();
    return;
  }
  if (Z.s > 1.01) return;                                  // 放大中：剛剛是在拖曳
  if (Math.abs(dx) > 60 && Math.abs(dx) > Math.abs(dy) * 1.5) stepViewer(dx < 0 ? 1 : -1);
  else if (dy > 100 && dy > Math.abs(dx) * 1.5) closeViewer();
}, { passive: true });

/* ============ 以圖搜圖（手動，點了才會上傳這一張） ============
   post：直接把圖送過去（網站接受從別的網頁送圖）；paste：網站不接受 → 複製圖片、開網頁，在那裡按 Ctrl+V */
const RSEARCH = [
  { k: "saucenao", name: "SauceNAO", how: "post", url: "https://saucenao.com/search.php", field: "file", tip: "Pixiv、Danbooru、X 等（2023 後的圖較少）" },
  { k: "ascii2d", name: "ascii2d", how: "post", url: "https://ascii2d.net/search/file", field: "file", extra: { utf8: "✓" }, tip: "X（Twitter）上的圖最強；會先有機器人驗證，點一下就好" },
  { k: "iqdb", name: "IQDB", how: "post", url: "https://iqdb.org/", field: "file", tip: "Danbooru、Gelbooru、Zerochan 等圖站" },
  { k: "tineye", name: "TinEye", how: "server", tip: "找網路上最早的出處（程式上傳，結果列在下面）" },
  { k: "fluffle", name: "Fluffle", how: "server", tip: "furry 圖專用：e621、FurAffinity、Twitter…（程式上傳，結果列在下面）" },
];
// Google Lens、Bing、Yandex 不接受程式或別的網頁送圖（實測：Lens 結果頁 403、Bing 400、Yandex 擋）→ 下載後自己拖進去
const RSEARCH_MANUAL = [["Google Lens", "https://lens.google.com/"], ["Bing", "https://www.bing.com/visualsearch"], ["Yandex", "https://yandex.com/images/"]];

async function _rsImage(id, type = "image/jpeg", max = 2000) {
  // 縮到 2000px 以內（網站有檔案大小限制），HEIC 等格式也一起轉好
  const blob = await fetch(`/file/${id}?display=1`).then(r => { if (!r.ok) throw new Error("讀不到圖片"); return r.blob(); });
  const bmp = await createImageBitmap(blob);
  const s = Math.min(1, max / Math.max(bmp.width, bmp.height));
  const cv = document.createElement("canvas"); cv.width = Math.round(bmp.width * s); cv.height = Math.round(bmp.height * s);
  const g = cv.getContext("2d");
  if (type === "image/jpeg") { g.fillStyle = "#fff"; g.fillRect(0, 0, cv.width, cv.height); }
  g.drawImage(bmp, 0, 0, cv.width, cv.height);
  return await new Promise(r => cv.toBlob(r, type, 0.92));
}

// 圖片先準備好（滑到按鈕上就開始），點下去時全部同步完成：開新分頁、送出表單、寫入剪貼簿都要在「點擊當下」，
// 不然瀏覽器會把新分頁當成彈出視窗擋掉
const _rsPrep = {};
function rsPrepare(id) {
  if (_rsPrep.id !== id) {
    _rsPrep.id = id; _rsPrep.jpg = null;
    _rsPrep.pJpg = _rsImage(id).then(b => (_rsPrep.id === id && (_rsPrep.jpg = b), b));
    _rsPrep.pJpg.catch(() => {});
  }
  return _rsPrep;
}

async function serverSearch(e, id) {
  const box = $("#v-rsres"); if (!box) return;
  box.innerHTML = `<div class="mute">${esc(e.name)} 搜尋中…</div>`;
  try {
    const r = await api(`/api/rsearch/${e.k}/${id}`, {});
    const res = r.results || [];
    box.innerHTML = `<div class="rsh"><b>${esc(e.name)}</b>${res.length ? ` 找到 ${res.length} 筆` : " 沒有找到"}${r.url && !/fluffle\.xyz\/$/.test(r.url) ? ` · <a href="${esc(safeUrl(r.url))}" target="_blank" rel="noopener">完整結果 ↗</a>` : ""}</div>
      ${res.map(x => `<a class="rsr" href="${esc(safeUrl(x.url || "#"))}" target="_blank" rel="noopener">
        ${x.thumb ? `<img loading="lazy" src="${esc(safeUrl(x.thumb))}" referrerpolicy="no-referrer" class="${x.sfw === false ? "nz" : ""}">` : "<span></span>"}
        <div><b>${esc(x.site || "")}</b>${x.match ? ` <span class="rm rm-${esc(x.match)}">${{ exact: "同一張", probable: "很可能", unlikely: "不太像" }[x.match] || esc(x.match)}</span>` : ""}${x.score != null ? ` <span class="mute">${esc(String(x.score))}${e.k === "fluffle" ? "%" : ""}</span>` : ""}
        ${x.authors?.length ? `<br><span class="mute">${esc(x.authors.join("、"))}</span>` : ""}</div></a>`).join("")}`;
  } catch (err) { box.innerHTML = `<div class="bad">${esc(e.name)}：${esc(err.message)}</div>`; }
}

function _rsSubmit(e, blob, target) {
  const f = document.createElement("form");
  f.method = "POST"; f.enctype = "multipart/form-data"; f.action = e.url; f.target = target; f.style.display = "none";
  for (const [n, v] of Object.entries(e.extra || {})) { const h = document.createElement("input"); h.type = "hidden"; h.name = n; h.value = v; f.appendChild(h); }
  const inp = document.createElement("input"); inp.type = "file"; inp.name = e.field;
  const dt = new DataTransfer(); dt.items.add(new File([blob], "image.jpg", { type: "image/jpeg" })); inp.files = dt.files;
  f.appendChild(inp); document.body.appendChild(f); f.submit(); f.remove();
}

function reverseSearch(k, id) {
  const e = RSEARCH.find(x => x.k === k); if (!e) return;
  if (e.how === "server") return serverSearch(e, id);
  const P = rsPrepare(id);
  if (P.jpg) return _rsSubmit(e, P.jpg, "_blank");
  // 圖還沒準備好：點擊當下先開好新分頁（不會被當成彈出視窗擋掉），準備好再把表單送進那個分頁 → 點一次就好
  const name = `rs_${e.k}_${Date.now()}`;
  const w = window.open("about:blank", name);
  if (!w) { toast("瀏覽器擋住了新分頁，請再點一次"); return; }
  try { w.document.title = `${e.name}…`; w.document.body.style.cssText = "background:#1d221e;color:#aaa;font:16px sans-serif;display:grid;place-items:center;height:100vh;margin:0"; w.document.body.textContent = `正在把圖送到 ${e.name}…`; } catch {}
  P.pJpg.then(b => { if (!w.closed) _rsSubmit(e, b, name); })
    .catch(err => { try { w.close(); } catch {} toast(`讀不到圖片：${err.message}`); });
}


// Rule34 用角色／畫師標籤搜尋（開 Rule34 的搜尋頁，自己找同一張）：已確認的優先，其次候選
function r34Link(d) {
  const pick = cat => d.tags.filter(t => t.category === cat && t.state >= 0).sort((a, b) => b.state - a.state || (b.score || 0) - (a.score || 0));
  const q = [...pick("artist").slice(0, 1), ...pick("character").slice(0, 2)].map(t => t.name);
  if (!q.length) return "";
  return `<a class="btn sm rs-long" target="_blank" rel="noopener" href="https://rule34.xxx/index.php?page=post&s=list&tags=${encodeURIComponent(q.join(" "))}"
    title="在 Rule34 搜尋：${esc(q.join(" "))}（Rule34 的名稱不一定跟這裡一樣）">搜 ${esc(q.join(" + "))} ↗</a>`;
}
// 「還有 N 個候選」浮層：點別的地方就收起來
document.addEventListener("mousedown", e => { if (!e.target.closest(".capmore")) document.querySelectorAll("#viewer .capmore[open]").forEach(d => d.open = false); });
