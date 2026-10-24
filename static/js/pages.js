// ES module（2026-10）：用到別的檔案的東西都要在這裡 import；瀏覽器會在載入時檢查名字對不對。
import { $, $$, api, apiNsfw, ask, closeModal, debounce, esc, fmtAgo, fmtEta, fmtN, fmtSize, follow, MISS_LABEL, modal, modalOpen, noAlbumQS, S, store, svg, toast } from "./core.js";
import { applyNsfwUI, go, loadSidebar, openJobs, pollOnce } from "./shell.js";
import { render, runSearch, sourceConfirm } from "./grid.js";
import { cellHTML } from "./filters.js";
import { setSel } from "./selection.js";
import { confirmTrash } from "./actions.js";
import { bindLibTabs, libTabs } from "./unrec.js";
import { datasetModal, impliesModal } from "./dataset.js";
import { openViewer } from "./viewer.js";
import { openSettings } from "./settings.js";
import { sweepModal } from "./sweep.js";
import { openRerun } from "./steps.js";
/* ============ 首頁 ============ */
export async function renderHome(gen) {
  $("#crumb").innerHTML = `<b>收件匣</b>`;
  $("#head").innerHTML = `<div><h1>收件匣</h1><div class="sub">要處理的事。自動流程、工具、背景工作在側欄的「系統」。</div></div>`;
  $("#content").innerHTML = `<div class="empty">載入中…</div>`;
  let d, sb = null;
  try {
    [d, sb, S.homeSt] = await Promise.all([api(`/api/home?nsfw=${apiNsfw()}${noAlbumQS()}`), api(`/api/sidebar?nsfw=${apiNsfw()}`).catch(() => null),
      api("/api/status").catch(() => ({}))]);
    d.saved = await api(`/api/saved?nsfw=${apiNsfw()}`).catch(() => []);
  } catch (e) { $("#content").innerHTML = `<div class="empty">${esc(e.message)}</div>`; return; }
  if (gen !== S.gen) return;
  if (!d.setup_done && !d.total) { openWizard(); }
  const cand = Object.values(d.cand).reduce((a, b) => a + b, 0), un = d.miss.all || 0;
  const newN = d.week >= d.total && d.total ? d.today : d.week, newL = d.week >= d.total && d.total ? "今天" : "這週";
  const hero = !d.total ? `<h2>先加入圖片資料夾</h2><p class="mute">程式會在背景掃描並用 AI 模型標記。第一次會下載模型（約 1.2 GB）。</p><div class="ibact"><button class="btn primary big" id="h-setup">開始設定</button></div>`
    : cand && d.batch?.n ? `<h2>有 <em>${fmtN(cand)}</em> 筆候選等你點頭${un ? `，另外 ${fmtN(un)} 張還沒有身分資訊` : ""}。</h2>
      <div class="ibact"><button class="btn primary big" data-rgo="batch" title="AI 很有把握的（≥ 0.9）同一個名字一面看完，只挑錯的；剩下的再逐張看">先整批看 ${fmtN(d.batch.n)} 筆 <kbd>Enter</kbd></button><button class="btn big ghost" data-rgo="focus">逐張看</button></div>`
    : cand ? `<h2>有 <em>${fmtN(cand)}</em> 筆候選等你點頭${un ? `，另外 ${fmtN(un)} 張還沒有身分資訊` : ""}。</h2>
      <div class="ibact"><button class="btn primary big" data-rgo="focus" title="一次一個角色：Enter 是、⌫ 不是、L 不用整理">開始確認 <kbd>Enter</kbd></button></div>`
    : un ? `<h2>候選都看完了。還有 <em>${fmtN(un)}</em> 張沒有身分資訊。</h2><div class="ibact"><button class="btn primary big" data-go="unrecognized">整理未識別 <kbd>Enter</kbd></button></div>`
    : `<h2>都整理好了。</h2><div class="ibact"><button class="btn big" data-go="all">看圖庫</button></div>`;
  const tips = [...d.tips.map(t => ({ kind: t.kind, tier: t.tier || "", text: t.text })),
    ...(sb?.counts?.duplicates ? [{ kind: "duplicates", text: `${fmtN(sb.counts.duplicates)} 張圖有重複` }] : []),
    ...(sb?.counts?.errors ? [{ kind: "errors", text: `${fmtN(sb.counts.errors)} 張讀取失敗` }] : [])]
    .filter(t => !(t.kind === "review" && cand));     // 主按鈕已經是「開始確認」，不重複
  $("#content").innerHTML = `<div class="inbox">
    ${homeAlerts({ ...(S.homeSt || {}), health: true }, d)}
    ${d.danbooru.online && d.danbooru.iqdb ? danBanner(d.danbooru) : ""}
    <div class="ibhero" title="${newL}進來 ${fmtN(newN)} 張 · 共 ${fmtN(d.total)} 張">${hero}</div>
    ${tips.length ? `<div class="ibq"><div class="ibh">之後</div>${tips.slice(0, 3).map(t => `<button class="ibrow" data-tip="${esc(t.kind)}" data-tier="${esc(t.tier || "")}"><span>${esc(t.text)}</span><b>›</b></button>`).join("")}</div>` : ""}
    ${d.saved?.length ? `<div class="ibq"><div class="ibh">智慧相簿</div><div class="chiprow">${d.saved.map(x => `<button class="chip2" data-q="${esc(x.query)}" title="${esc(x.query)}">${esc(x.name)} <i>${x.n ?? ""}</i></button>`).join("")}</div></div>` : ""}
    ${false && d.chars.length ? `<div class="ibq"><div class="ibh">最近在收 <a class="lnk right" data-go="characters">全部角色 ›</a></div>
      <div class="avrow">${d.chars.map(c => `<div class="av" data-tag="${esc(c.name)}" title="${esc(c.name)}"><div class="ring">${c.cover ? `<img loading="lazy" src="/t/${c.cover}">` : ""}</div><b>${esc(c.display)}</b><span>${fmtN(c.n)} 張</span></div>`).join("")}</div></div>` : ""}
    ${d.recent.length ? `<div class="ibq"><div class="ibh">最近加入 <a class="lnk right" data-go="all">圖庫 ›</a></div>
      <div class="hrecent">${d.recent.slice(0, 12).map(m => `<img loading="lazy" src="/t/${m}" data-open="${m}">`).join("")}</div></div>` : ""}
    <div class="hfoot mute">上次備份 ${fmtAgo(d.last_backup)} · ${researchLine(d.research)}<a class="lnk" data-go="system">自動流程與工具 ›</a></div>
  </div>`;
  bindHubClicks(d.recent);
  bindHomeAlerts();
  $$("[data-rgo]").forEach(b => b.onclick = () => { S.rmode = b.dataset.rgo; store.set("rmode", S.rmode); go("review"); });
  const hs = $("#h-setup"); if (hs) hs.onclick = openWizard;
  const primary = $(".ibact .btn.primary");
  S.homeEnter = primary ? () => primary.click() : null;
  $("#foot-l").textContent = `${fmtN(d.total)} 張`;
}
// 收件匣最上面的警示：掃描時突然找不到很多張（先暫停標記）、備份只在同一顆硬碟
function homeAlerts(st, d) {
  const out = [];
  const h = st.health ? null : st.missing_hold;    // 收件匣不放警示（在側欄的健康燈）；健康燈的彈出框才列
  if (h) out.push(`<div class="ibalert warn"><b>這次掃描有 ${fmtN(h.n)} 張突然找不到（共 ${fmtN(h.of)} 張）</b>
    <div class="mute">多半是資料夾暫時讀不到（iCloud 同步中、隨身碟、網路磁碟），所以先不標成「找不到檔案」。確定是你刪掉或搬走的再按確認。</div>
    <div class="ibact"><button class="btn sm" data-mh="confirm">確認，真的不見了</button><button class="btn sm" data-mh="dismiss">先不要，等下次掃描</button></div></div>`);
  if (!st.health && d.backup_offsite === false && Date.now() > +store.get("bkNagUntil", 0)) out.push(`<div class="ibalert"><b>備份只在這顆硬碟上</b>
    <div class="mute">硬碟壞掉時，圖片、資料庫和每天的備份會一起不見。插上外接硬碟，按「備份到外接硬碟」整份複製過去。</div>
    <div class="ibact"><button class="btn sm" data-drive-bk>備份到外接硬碟</button><button class="btn sm" data-bk-later>30 天後再提醒</button></div></div>`);
  else if (!st.health && d.drive_overdue && Date.now() > +store.get("bkDriveUntil", 0)) out.push(`<div class="ibalert"><b>上次備份到外接硬碟是 ${d.drive_overdue} 天前</b>
    <div class="mute">插上硬碟按一次就好：只複製新的和改過的，通常幾分鐘。</div>
    <div class="ibact"><button class="btn sm" data-drive-bk>備份到外接硬碟</button><button class="btn sm" data-bk-drive-later>7 天後再提醒</button></div></div>`);
  return out.join("");
}
// 健康燈（側欄左下）：備份、掃描暫停、工作失敗這類「要處理但不急著每次看」的事，平常只是一顆燈
export function drawHealth(s) {
  const btn = $("#health"); if (!btn) return;
  S.healthSt = s;
  const backup = (s.backup_offsite === false && Date.now() > +store.get("bkNagUntil", 0)) || (!!s.drive_overdue && Date.now() > +store.get("bkDriveUntil", 0));
  const n = (s.missing_hold ? 1 : 0) + (backup ? 1 : 0) + (s.error ? 1 : 0);
  btn.classList.toggle("hidden", !n);
  btn.classList.toggle("bad", !!(s.missing_hold || s.error));
  btn.title = n ? `${n} 件事要處理（點開看）` : "";
  if (!n) $("#healthpop")?.remove();
}
export function openHealth() {
  const s = S.healthSt || {};
  let pop = $("#healthpop");
  if (pop) { pop.remove(); return; }
  pop = document.createElement("div"); pop.id = "healthpop";
  const parts = homeAlerts({ ...s, health: false }, { backup_offsite: s.backup_offsite, drive_overdue: s.drive_overdue });
  pop.innerHTML = (parts || "") + (s.error ? `<div class="ibalert warn"><b>上次背景工作失敗</b><div class="mute">${esc(s.error)}</div><div class="ibact"><button class="btn sm" data-hp-sys>看系統</button></div></div>` : "")
    + `<div class="hpfoot mute">${s.running ? `背景：${esc(s.phase || "")} ${fmtN(s.done || 0)}/${fmtN(s.total || 0)}` : "其他都正常"}</div>`;
  document.body.append(pop);
  bindHomeAlerts(pop);
  $("[data-hp-sys]", pop)?.addEventListener("click", () => { pop.remove(); go("system"); });
  $$("[data-bk-later], [data-bk-drive-later], [data-drive-bk], [data-go-settings], [data-mh]", pop).forEach(b => b.addEventListener("click", () => setTimeout(() => { pop.remove(); drawHealth(S.healthSt || {}); }, 50)));
  setTimeout(() => document.addEventListener("mousedown", function off(e) { if (!e.target.closest("#healthpop, #health")) { pop.remove(); document.removeEventListener("mousedown", off); } }), 0);
}
function bindHomeAlerts(root = document) {
  $$("[data-mh]", root).forEach(b => b.onclick = async () => {
    const r = await api("/api/scan/confirm-missing", b.dataset.mh === "dismiss" ? { dismiss: true } : {});
    toast(b.dataset.mh === "dismiss" ? "好，這次先不標記" : `已標記 ${fmtN(r.marked)} 張找不到檔案`); render();
  });
  $$("[data-go-settings]", root).forEach(b => b.onclick = () => openSettings("data"));
  $$("[data-bk-later]", root).forEach(b => b.onclick = () => { store.set("bkNagUntil", Date.now() + 30 * 864e5); b.closest(".ibalert")?.remove(); });
  $$("[data-bk-drive-later]", root).forEach(b => b.onclick = () => { store.set("bkDriveUntil", Date.now() + 7 * 864e5); b.closest(".ibalert")?.remove(); });
  $$("[data-drive-bk]", root).forEach(b => b.onclick = () => openDriveBackup());
}
// 系統：自動流程（每一步的開關和找到幾張）、工具、背景工作、備份與診斷（以前都擠在首頁）
export async function renderSystem(gen) {
  $("#crumb").innerHTML = `<b>系統</b>`;
  $("#head").innerHTML = `<div><h1>系統</h1><div class="sub">自動流程在做什麼、所有工具、背景工作、備份與診斷。</div></div>`;
  $("#content").innerHTML = `<div class="empty">載入中…</div>`;
  let pl = null, d = null;
  try { [pl, d] = await Promise.all([api("/api/pipeline").catch(() => null), api(`/api/home?nsfw=${apiNsfw()}`).catch(() => null)]); } catch { }
  if (gen !== S.gen) return;
  $("#content").innerHTML = `<div class="home system">
    ${pl ? pipelineCard(pl) : ""}
    <h3 class="sech">工具</h3>
    ${toolsHTML(pl)}
    ${d ? `<div class="hfoot mute">上次備份 ${fmtAgo(d.last_backup)} · ${researchLine(d.research)}<a class="lnk" id="h-diag">系統狀態</a> · <a class="lnk" id="h-phone">手機／平板</a></div>` : ""}
  </div>`;
  const c = $("#content");
  bindHubClicks([]);
  $("#foot-l").textContent = "";
  const hd = $("#h-diag"); if (hd) hd.onclick = openDiag;
  const hp = $("#h-phone"); if (hp) hp.onclick = openPhone;
}
function bindHubClicks(recent) {
  const c = $("#content");
  c.onclick = e => {
    const tl = e.target.closest("[data-tool]"); if (tl) return HOME_TOOL_FN[tl.dataset.tool]?.();
    const fs = e.target.closest(".fstep[data-stepkey]"); if (fs && fs.dataset.stepkey) { S.stepFocus = fs.dataset.stepkey; return openSettings("steps"); }
    if (e.target.closest("[data-auto], label.pst")) return;
    const g = e.target.closest("[data-go]"); if (g) { const v = g.dataset.go; return v.startsWith("search:") ? runSearch(v.slice(7)) : go(v); }
    const t = e.target.closest("[data-tag]"); if (t) return go("tag", t.dataset.tag);
    const q = e.target.closest("[data-q]"); if (q) return runSearch(q.dataset.q);
    const o = e.target.closest("[data-open]"); if (o) { S.items = recent.map(id => ({ id })); return openViewer(+o.dataset.open); }
    const tip = e.target.closest("[data-tip]");
    if (tip) {
      const k = tip.dataset.tip;
      if (k === "review") { S.rtier = tip.dataset.tier; store.set("rtier", S.rtier); S.rmode = "grid"; store.set("rmode", "grid"); go("review"); }
      else if (k === "source") sourceConfirm(null, "unrecognized", "all");
      else if (k === "icloud_clear") icloudClear();
      else if (k === "people") go("unrecognized", "people");
      else if (k === "upgrade") runSearch("is:upgrade");
      else if (k === "ascii2d") ascii2dModal(null, { all: true });
      else if (k === "piles") { store.set("ubucket", "p_meme"); go("unrecognized", "all"); }
      else if (k === "jobs") openJobs();
      else if (k === "research_maybe") api("/api/online/research-maybe", {}).then(r => { toast(`已排入背景補搜 ${fmtN(r.queued)} 張（每張 2–3 秒，可以繼續做別的）`); render(); }).catch(e => toast(e.message));
      else go(k);
    }
  };
}

function researchLine(r) {
  if (!r || !r.notfound) return "";
  const nx = r.due ? "現在有 " + fmtN(r.due) + " 張到期" : r.next_at ? `下次 ${new Date(r.next_at * 1000).toLocaleDateString()}` : "";
  return `<span title="找不到出處的 ${fmtN(r.notfound)} 張，每 ${r.days} 天自動重搜一次（Danbooru 一直在新增）${r.last_run ? `；上次檢查 ${fmtAgo(r.last_run)}` : ""}">找不到出處的重搜：${nx}</span> · `;
}

/* ---- 系統：自動流程（照新圖真正跑的順序分段；每一步你的找到率）---- */
const STAGES = [["local", "在你的電腦上", "不連網，每張新圖都做"], ["exact", "Danbooru：一模一樣", "對上就直接確認"],
  ["similar", "相似圖", "上面沒對上才做"], ["fallback", "Danbooru 找不到時", "結果放待確認"], ["after", "找到出處之後", ""],
  ["manual", "要你動手的", "在瀏覽器或另外開始"]];
const pct = r => r == null ? "" : r >= 0.1 ? `${Math.round(r * 100)}%` : r >= 0.01 ? `${(r * 100).toFixed(1)}%` : `${(r * 100).toFixed(2)}%`;
// v4.4 流程圖：每一步一列，長條＝你的圖庫在這一步找到多少（平方根比例，小的也看得到）；點一列打開那一步的設定
const STEP_OF = { tagger: "wd", pixai: "pixai", md5: "exact", pixel: "pixelhash", iqdb: "iqdb", sig: "sig", r34: "r34", e621: "e621", trace: "trace", upgrade: "upgrade", ascii2d: "ascii2d", sweep: "sweep" };
function pipelineCard(pl) {
  const vis = pl.steps.filter(s => !s.rare);
  const maxF = Math.max(1, ...vis.map(s => s.found || 0));
  const row = st => {
    const off = !st.enabled || (st.configured === false && st.key === "r34");
    const f = st.found || 0, w = st.found == null ? 0 : Math.max(f ? 3 : 0, Math.round(Math.sqrt(f / maxF) * 100));
    const num = st.found == null ? `<b>${fmtN(st.checked)}</b><span>張</span>`
      : `<b>${fmtN(f)}</b><span>${st.rate != null ? pct(st.rate) : st.checked_label ? `${fmtN(st.checked)} ${st.checked_label}` : st.found_label || "找到"}</span>`;
    return `<div class="fstep ${off ? "off" : ""} ${st.tool ? "manual" : ""}" data-stepkey="${STEP_OF[st.key] || ""}" title="${esc(st.desc)}${st.checked ? `（試過 ${fmtN(st.checked)} 張）` : ""}">
      <span class="fdot"></span>
      <div class="fname"><b>${esc(st.name)}</b><span>${esc(st.desc)}${st.note ? ` · <em>${esc(st.note)}</em>` : ""}</span></div>
      <div class="fbar"><i style="width:${w}%"></i></div>
      <div class="fnum">${num}</div>
      <div class="fact">${st.tool ? `<button class="btn sm" data-tool="${st.tool}">打開</button>` : `<span class="fgo">${off ? "關著" : ""} ›</span>`}</div></div>`;
  };
  const rare = pl.steps.filter(s => s.rare);
  const groups = STAGES.map(([k, t, sub]) => {
    const ss = vis.filter(s => s.stage === k);
    return ss.length ? `<div class="fstage"><div class="fsth"><span>${t}</span>${sub ? `<em>${sub}</em>` : ""}</div>${ss.map(row).join("")}</div>` : "";
  }).join("");
  const tot = pl.total || 0, pc = n => tot ? `${Math.round(n / tot * 100)}%` : "";
  const kpi = (n, l, sub, cls = "") => `<div class="kpi ${cls}"><b>${fmtN(n)}</b><span>${l}</span>${sub ? `<em>${sub}</em>` : ""}</div>`;
  return `<section class="flow">
    <div class="flowh"><div><h3>找出處的流程</h3><p>新圖由上往下做，找到就停。長條＝你的圖庫在這一步找到多少；點一列看那一步的設定。</p></div>
      <div class="flowact"><button class="btn sm" data-tool="steps">每一步的設定</button><button class="btn sm primary" data-tool="find">補跑舊圖…</button></div></div>
    <div class="kpis">${kpi(tot, "張圖")}${kpi(pl.searched || 0, "找過出處", pc(pl.searched || 0))}${kpi(pl.sourced || 0, "找到出處", pc(pl.sourced || 0), "good")}${kpi(pl.noid || 0, "還沒有身分", pc(pl.noid || 0), pl.noid ? "warn" : "")}</div>
    ${!pl.online ? `<div class="pwarn">線上功能已關閉：只會做本機的 AI 標記</div>` : !pl.auto ? `<div class="pwarn">「新圖自動找出處」已關閉：新圖只會做本機的 AI 標記</div>` : ""}
    <div class="flowbody">${groups}</div>
    ${rare.length ? `<details class="prare"><summary>很少找到、已經關掉的 ${rare.length} 個</summary><div class="flowbody">${rare.map(row).join("")}</div></details>` : ""}</section>`;
}
// 工具：依用途分組；很少用的收在「更多」（Ctrl K 也都叫得到）
const TOOL_GROUPS = [
  ["找出處", [
    ["find", "search", "找出處（補跑舊圖）", "像素指紋、以圖搜圖、簽名、Rule34，背景一次跑完"]]],   // ascii2d、畫師作品比對在上面流程表那一列「打開」（v4：不在兩處重複）
  ["整理", [
    ["upgrade", "zoomin", "換成高清版", "出處有同一張、比較大的版本"],
    ["dups", "duplicates", "重複的圖", "看起來一樣的圖和局部截圖，挑一張留"]]],
  ["把圖收進來", [
    ["import", "download", "從網站匯入", "貼 X、Pixiv、Danbooru… 網址，原圖和資料一起存"],
    ["phone", "phone", "手機／平板", "同一個 Wi-Fi 下用手機看圖庫、傳圖進來"]]],
  ["維護", [
    ["jobs", "clock", "背景工作", "正在做什麼、排隊中、排程"],
    ["backup", "db", "備份與還原", "每天自動備份資料庫"],
    ["drive", "db", "備份到外接硬碟", "圖片、程式、資料庫整份複製到另一顆硬碟"],
    ["oplog", "undo", "操作紀錄", "你做過什麼、最常做的動作；可以下載成 CSV"],
    ["diag", "pulse", "系統狀態", "GPU、模型、資料庫"]]],
];
const MORE_TOOLS = [
  ["artist", "從畫師反推（Danbooru）", "下載畫師在 Danbooru 的作品縮圖比對；流量大"],
  ["noanime", "篩選非動漫圖", "iCloud 裡的照片、截圖、梗圖"],
  ["implies", "標籤蘊含", "造型 ⇒ 本體這類「A 也代表 B」的規則；Danbooru 有的自動抓"],
  ["dataset", "匯出訓練集", "圖片＋同名 .txt 標註（LoRA 訓練用），用你確認過的標籤"],
];
function toolsHTML(pl) {
  const o = pl?.other || {};
  const moreNote = { artist: o.artist_scan != null ? `你找到 ${fmtN(o.artist_scan)} 張` : "" };
  return TOOL_GROUPS.map(([g, ts]) => `<div class="tgrp"><div class="tgh">${g}</div><div class="tools">${ts.map(([k, ic, name, desc]) =>
      `<button class="tool" data-tool="${k}"><span class="ti">${svg(ic, 20)}</span><b>${name}</b><span>${desc}</span></button>`).join("")}</div></div>`).join("")
    + `<details class="tmore"><summary>更多工具</summary><div class="tmlist">${MORE_TOOLS.map(([k, name, desc]) =>
      `<button class="tml" data-tool="${k}"><b>${name}</b><span>${desc}${moreNote[k] ? ` · ${moreNote[k]}` : ""}</span></button>`).join("")}</div></details>`;
}
const HOME_TOOL_FN = {
  sweep: () => sweepModal(),
  import: () => importModal(), find: () => findModal(), steps: () => openSettings("steps"), ascii2d: () => ascii2dModal(), review: () => go("review"), artist: () => artistScanModal(),
  audit: () => { const m = modal(`<h3>準確率抽查</h3><div class="mbody"><p class="mute" style="margin-top:0">隨機抽 40 張已確認的圖，點出不對的，會算出各種找法的準確率並撤銷標錯的。</p></div>
    <div class="mfoot"><button class="btn" data-close>取消</button><button class="btn" id="au-c">抽查角色</button><button class="btn primary" id="au-a">抽查創作者</button></div>`);
    $("#au-c", m).onclick = () => { closeModal(); auditModal("character"); }; $("#au-a", m).onclick = () => { closeModal(); auditModal("artist"); }; },
  upgrade: () => upgradeModal(), implies: () => impliesModal(), dataset: () => datasetModal(),
  noanime: () => runSearch("is:icloud is:noanime"), dups: () => go("duplicates"), jobs: () => openJobs(), phone: () => openPhone(), backup: () => openBackup(), drive: () => openDriveBackup(), diag: () => openDiag(), oplog: () => oplogModal(),
};

/* ============ 換成高清版（app/upgrade.py）============ */
export async function upgradeModal() {
  let d;
  try { d = await api("/api/upgrade/summary"); } catch (e) { return toast(e.message); }
  const m = modal(`<h3>換成高清版</h3><div class="mbody">
    <p class="mute" style="margin-top:0">找到出處後，看 Danbooru／Pixiv／X／e621／Rule34 上的那張是不是比你的大（長邊多 10% 以上、比例一樣）。只提醒，換不換你決定；換之前會下載下來逐像素確認是同一張。</p>
    <table class="diag">
      <tr><th>可以換</th><td><b>${fmtN(d.offer)}</b> 張 ${d.offer ? `<a class="lnk" data-upq="is:upgrade">看清單 ›</a>` : ""}</td></tr>
      <tr><th>換過了</th><td>${fmtN(d.done)} 張 ${d.done ? `<a class="lnk" data-upq="is:upgraded">看看 ›</a>` : ""}</td></tr>
      <tr><th>換的時候失敗</th><td>${fmtN(d.failed)} 張 <span class="mute">（多半是差分：網站上的不是同一張）</span></td></tr>
      <tr><th>查過、沒有更大的</th><td>${fmtN(d.none)} 張</td></tr>
      <tr><th>你說不用換</th><td>${fmtN(d.dismissed)} 張</td></tr>
      <tr><th>有出處、還沒查</th><td>${fmtN(d.unchecked)} 張</td></tr>
    </table>
    <p class="mute" style="margin-bottom:0">新找到出處的圖會自動查（系統 › 自動流程「找更高清的版本」可以關）。單張：大圖 › 詳細 › 來源，也可以貼貼文網址換。</p></div>
    <div class="mfoot"><button class="btn" data-close>關閉</button>
    ${d.unchecked ? `<button class="btn" id="up-check">補查 ${fmtN(d.unchecked)} 張</button>` : ""}
    ${d.offer ? `<button class="btn primary" data-upq="is:upgrade">看可以換的 ${fmtN(d.offer)} 張</button>` : ""}</div>`);
  $$("[data-upq]", m).forEach(b => b.onclick = () => { closeModal(); runSearch(b.dataset.upq); });
  const uc = $("#up-check", m); if (uc) uc.onclick = async () => {
    try { const r = await api("/api/upgrade/check", {}); toast(`已排入背景：查 ${fmtN(r.queued)} 張有沒有高清版（Danbooru 的不用連網，很快；Pixiv、X 每張 1–2 秒）`); closeModal(); }
    catch (e) { toast(e.message); }
  };
}

/* ============ ascii2d 半自動找出處（app/ascii2d.py、油猴腳本 1.5）============ */
const fmtDur = min => min >= 90 ? `${Math.round(min / 6) / 10} 小時` : `${min} 分鐘`;
export async function ascii2dModal(ids, opts = {}) {
  const [d, mons] = await Promise.all([api("/api/ascii2d").catch(e => ({ error: e.message })), api("/api/ascii2d/months").catch(() => [])]);
  if (d.error) return toast(d.error);
  const sel = ids?.length ? ids : null;
  const m = modal(`<h3>ascii2d 半自動找出處</h3><div class="mbody a2m">
    <p class="mute" style="margin-top:0">X 上的原創圖 Danbooru 沒收，自動搜源找不到；ascii2d 找得到，但有機器人驗證，只能在你的瀏覽器裡一張一張按 Enter。先選這次要找的範圍。</p>
    <div class="a2scope">
      ${sel ? `<label><input type="radio" name="a2s" value="sel" checked> 選取的 <b>${fmtN(sel.length)}</b> 張</label>` : ""}
      <label><input type="radio" name="a2s" value="q" ${sel ? "" : "checked"}> 搜尋條件</label>
      <div class="a2q"><input id="a2-q" placeholder="例如 date:2026-07、path:2026-07、type:comic、-tag:梗圖（空白＝全部）" value="${esc(S.view === "all" && S.q ? S.q : "")}" autocomplete="off">
        ${mons.length ? `<div class="chiprow a2mon">${mons.map(x => `<button class="chip2" data-q="date:${esc(x.month)}">${esc(x.month)} <span class="mute">${fmtN(x.n)}</span></button>`).join("")}</div>` : ""}</div>
    </div>
    <div class="a2opts">
      <label><input type="checkbox" id="a2-miss" checked> 只找還沒有創作者、ascii2d 還沒找過的</label>
      <label title="漫畫、有字、沒有人、梗圖、照片：你過去的找到率 2–9%（插畫 16–47%）"><input type="checkbox" id="a2-low"> 也找找到率低的（漫畫、有字、梗圖…）</label>
      <label>最多 <input type="number" id="a2-lim" min="1" value="50" style="width:72px"> 張（空白＝不限；一次 30–50 張剛好）</label>
      <label><input type="checkbox" id="a2-add"> 加在目前的佇列後面（不勾＝換成這次的範圍）</label>
      <label title="比對到的結果把找到的創作者、角色、作品全部勾起來，1.5 秒後自動按 Enter（這 1.5 秒內按任何鍵就停下來）。需要腳本 1.9">自動確認 <select id="a2-auto">${[["off", "不要，我自己按 Enter"], ["same", "比對到「同一張」時"], ["likely", "「同一張」或「很像」時"], ["full", "全自動（什麼情況都不停）"]].map(([k, l]) => `<option value="${k}" ${k === (S.settings?.ascii2d_auto_accept || "off") ? "selected" : ""}>${l}</option>`).join("")}</select></label>
    </div>
    <p id="a2-pv" class="a2pv">計算中…</p>
    <p class="mute" style="margin:6px 0 0">目前佇列：還剩 ${fmtN(d.remaining)} 張${d.remaining && d.queue ? `（預估找到約 ${fmtN(d.queue.expect)} 張、約 ${fmtDur(d.queue.minutes)}）` : ""} · 累計找到 ${fmtN(d.found)} · 沒找到 ${fmtN(d.none)} · 跳過 ${fmtN(d.skipped)}
      ${d.remaining ? ` · <a class="lnk" id="a2-clear">清空佇列</a>` : ""}</p>
    <p class="mute" style="margin:6px 0 0">在 ascii2d 上：<kbd>Enter</kbd> 送出／確認勾選的並下一張、<kbd>N</kbd> 沒找到、<kbd>S</kbd> 先跳過、<kbd>B</kbd> 特徵檢索、<kbd>Esc</kbd> 暫停。
      需要油猴腳本 1.5 以上（1.8 起「都不太像」會自動記沒找到、跳下一張；1.9 起可以自動確認）：${d.script?.version ? `<span class="okc">最近連上：${esc(d.script.version)}（${fmtAgo(d.script.at)}）</span>` : "還沒看到腳本連上來（第一次用時正常）"}</p>
    </div><div class="mfoot"><button class="btn" id="a2-script">安裝／更新腳本</button><span style="flex:1"></span>
    ${d.active ? `<button class="btn" id="a2-stop">停止</button>` : ""}<button class="btn" data-close>關閉</button>
    <button class="btn primary" id="a2-go" disabled>開始</button></div>`, null, "wide");
  const scope = () => $("input[name=a2s]:checked", m)?.value || "q";
  const body = () => {
    const lim = parseInt($("#a2-lim", m).value, 10);
    return { ...(scope() === "sel" ? { ids: sel } : { q: $("#a2-q", m).value.trim() }), only_missing: $("#a2-miss", m).checked,
             limit: lim > 0 ? lim : null, replace: !$("#a2-add", m).checked, low: $("#a2-low", m).checked };
  };
  const pv = $("#a2-pv", m), go_ = $("#a2-go", m);
  const refresh = debounce(async () => {
    const b = body();
    if (b.ids) {    // 選取的：數字在後端算（要扣掉已經有創作者的）
      pv.textContent = `選取 ${fmtN(b.ids.length)} 張${b.only_missing ? "，只排還沒有創作者的" : ""}${b.limit ? `，最多 ${fmtN(b.limit)} 張` : ""}`;
      go_.disabled = false; go_.textContent = "開始"; return;
    }
    try {
      const r = await api(`/api/ascii2d/preview?q=${encodeURIComponent(b.q)}&only_missing=${b.only_missing}&low=${b.low}${b.limit ? `&limit=${b.limit}` : ""}`);
      pv.innerHTML = `符合 <b>${fmtN(r.matched)}</b> 張${b.only_missing ? `，其中還沒有創作者的 <b>${fmtN(r.eligible - (r.x_extra || 0))}</b> 張` : ""}${r.x_extra ? `，另外加上從 X 匯入的 <b>${fmtN(r.x_extra)}</b> 張` : ""} → 這次排 <b>${fmtN(r.count)}</b> 張${r.count ? `，預估找到約 <b>${fmtN(r.expect)}</b> 張、約 ${fmtDur(r.minutes)}` : ""}${r.low_out ? `<br><span class="mute">另有 ${fmtN(r.low_out)} 張是漫畫、有字、梗圖這類找到率低的，沒排（要的話勾上面「也找找到率低的」）</span>` : ""}`;
      go_.disabled = !r.count; go_.textContent = r.count ? `開始（${fmtN(r.count)} 張）` : "開始";
    } catch (e) { pv.textContent = e.message; go_.disabled = true; }
  }, 250);
  $("#a2-q", m).addEventListener("keydown", e => e.stopPropagation());
  $("#a2-q", m).addEventListener("input", () => { const r = $("input[name=a2s][value=q]", m); if (r) r.checked = true; refresh(); });
  $$(".a2mon [data-q]", m).forEach(b => b.onclick = () => { $("#a2-q", m).value = b.dataset.q; $("input[name=a2s][value=q]", m).checked = true; refresh(); });
  $$("input[name=a2s], #a2-miss, #a2-low, #a2-lim, #a2-add", m).forEach(x => x.addEventListener("change", refresh));
  $("#a2-auto", m).onchange = async e => {      // 馬上存（正在 ascii2d 上連續搜也會立刻套用）
    try { S.settings = await api("/api/settings", { ascii2d_auto_accept: e.target.value }); toast(e.target.value === "off" ? "自動確認關掉了" : "已開啟自動確認"); } catch (err) { toast(err.message); }
  };
  $("#a2-lim", m).addEventListener("input", refresh);
  if (opts.all) { $("#a2-lim", m).value = ""; if (d.remaining) $("#a2-add", m).checked = true; }   // 收件匣「還沒找過的」：全部、加在佇列後面（b93）
  refresh();
  $("#a2-script", m).onclick = () => window.open("/api/fetch-url/userscript.user.js", "_blank");
  const cl = $("#a2-clear", m); if (cl) cl.onclick = async () => { await api("/api/ascii2d/clear", {}); toast("佇列清空了"); closeModal(); ascii2dModal(ids); };
  const st = $("#a2-stop", m); if (st) st.onclick = async () => { await api("/api/ascii2d/stop", {}); closeModal(); toast("已停止（排好的圖留著，下次接著做）"); };
  go_.onclick = async () => {
    const w = window.open("https://ascii2d.net/", "ascii2d");   // 要在點擊當下開，不然會被當成彈出視窗擋掉
    try {
      const r = await api("/api/ascii2d/start", body());
      closeModal(); toast(`佇列裡有 ${fmtN(r.remaining)} 張（預估找到約 ${fmtN(r.expect ?? 0)} 張）：到 ascii2d 那一頁按 Enter 開始`);
      if (!w) toast("瀏覽器擋住了新分頁：請自己打開 ascii2d.net");
    } catch (e) { toast(e.message); }
  };
}

/* ============ 重複：並排比較 ============ */
export async function renderDuplicates(gen) {
  $("#crumb").innerHTML = `<b>重複</b>`;
  $("#head").innerHTML = `<div><h1>重複</h1><div class="sub">看起來一模一樣的圖（解析度、格式不同）和局部截圖。改了表情、對話的差分不算。每組標出「建議保留」，其餘可一鍵選取刪除。</div><div class="pagetabs">${libTabs()}</div>
    <div class="pagetabs"><span class="seg" id="dup-dist">${[[0, "完全相同"], [3, "非常相似"], [6, "相似（較寬鬆）"]].map(([v, l]) => `<button data-d="${v}" class="${v === store.get("dupDist", 3) ? "on" : ""}">${l}</button>`).join("")}</span></div></div>`;
  bindLibTabs();
  $("#toolbar").innerHTML = "";
  const dist = store.get("dupDist", 3);
  $$("#dup-dist button").forEach(b => b.onclick = () => { store.set("dupDist", +b.dataset.d); render(); });
  $("#content").innerHTML = `<div class="empty">計算中…</div>`;
  let d;
  try { d = await api(`/api/duplicates?nsfw=${apiNsfw()}&dist=${dist}`); }
  catch (e) { if (gen === S.gen) $("#content").innerHTML = `<div class="empty">找重複失敗：${esc(e.message)}</div>`; return; }
  if (gen !== S.gen) return;
  S.items = d.groups.flat();
  const score = x => (x.crop_of ? -1e12 : 0) + (x.width || 0) * (x.height || 0) * 10 + (x.confirmed || 0) * 1e6 + (x.source === "found" ? 5e5 : 0) + (x.size || 0) / 1e6;
  const cs = d.crop_scan || {};
  const scanNote = cs.running ? `<div class="note" id="crop-note">還在比對局部截圖（${cs.checked}/${cs.total}），完成後這頁會自動更新</div>` : "";
  if (cs.running) setTimeout(async () => { if (gen !== S.gen) return; let st; try { st = await api(`/api/duplicates?nsfw=${apiNsfw()}&dist=${dist}`); } catch { return; } if (gen === S.gen && !st.crop_scan?.running) render(); else if (gen === S.gen) { const n = $("#crop-note"); if (n) n.textContent = `還在比對局部截圖（${st.crop_scan.checked}/${st.crop_scan.total}），完成後這頁會自動更新`; } }, 4000);
  const fmtDate = t => t ? new Date(t * 1000).toLocaleDateString() : "";
  const posOf = (g, id) => g.findIndex(x => x.id === id) + 1;
  $("#content").innerHTML = scanNote + (d.groups.length ? d.groups.map((g, gi) => {
    const best = g.reduce((a, b) => score(b) > score(a) ? b : a, g[0]);
    const maxPx = Math.max(...g.map(x => (x.width || 0) * (x.height || 0)));
    return `<div class="dgroup"><div class="dh">第 ${gi + 1} 組 · ${g.length} 張
      <button class="btn sm" data-keep="${best.id}" data-all="${g.map(x => x.id).join(",")}">保留建議的，選取其餘</button>
      <button class="btn sm" data-cmp="${g.map(x => x.id).join(",")}">並排比較</button></div>
      <div class="dcards">${g.map(it => `<div class="dcard ${it.id === best.id ? "best" : ""}">
        ${cellHTML(it, 0)}
        ${it.id === best.id ? `<span class="bestb">建議保留</span>` : ""}
        ${it.crop_of ? `<span class="cropb" title="這張只是另一張的一部分，建議保留完整的那張">局部截圖${posOf(g, it.crop_of) ? `（第 ${posOf(g, it.crop_of)} 張的局部）` : ""}</span>` : ""}
        <table class="dmeta"><tr><td>解析度</td><td class="${(it.width || 0) * (it.height || 0) === maxPx ? "hi" : ""}">${it.width}×${it.height}</td></tr>
        <tr><td>大小</td><td>${fmtSize(it.size)} · ${esc((it.ext || "").toUpperCase())}</td></tr>
        <tr><td>日期</td><td>${fmtDate(it.mtime)}</td></tr>
        <tr><td>已整理</td><td>${it.confirmed ? `${it.confirmed} 個標籤` : "—"}${it.source === "found" ? " · 有來源" : ""}</td></tr>
        <tr><td>位置</td><td class="path" title="${esc(it.path)}">${esc(it.folder)}</td></tr></table></div>`).join("")}</div></div>`;
  }).join("") : `<div class="empty">沒有找到重複的圖片</div>`);
  $$("[data-keep]").forEach(b => b.onclick = () => b.dataset.all.split(",").filter(x => x !== b.dataset.keep).forEach(id => setSel(+id, true)));
  $$("[data-cmp]").forEach(b => b.onclick = () => compareModal(b.dataset.cmp.split(",").map(Number), d.groups.flat()));
  $("#foot-l").textContent = `${d.groups.length} 組 · ${d.count} 張`;
  $$('[data-count="duplicates"]').forEach(el => el.textContent = d.count || "");
}
function compareModal(ids, all) {
  const items = ids.map(id => all.find(x => x.id === id)).filter(Boolean);
  let a = 0, b = 1;
  const m = modal(`<h3>並排比較</h3><div class="cmp" id="cmp"></div>
    <div class="mfoot"><span class="mute" style="margin-right:auto">滑鼠移到圖上會同步放大同一個位置</span><button class="btn" data-close>關閉</button></div>`, null, "full");
  const draw = () => {
    const side = (it, k) => `<div class="cside"><div class="cpick">${items.map((x, i) => `<button data-side="${k}" data-i="${i}" class="${items[k === 0 ? a : b] === x ? "on" : ""}">${i + 1}</button>`).join("")}</div>
      <div class="cimg"><img src="/file/${it.id}"></div>
      <div class="cinfo">${it.width}×${it.height} · ${fmtSize(it.size)} · ${esc(it.name)}<br><span class="mute">${esc(it.folder)}</span></div>
      <button class="btn sm primary" data-keep1="${it.id}">保留這張，刪除其他 ${items.length - 1} 張</button></div>`;
    $("#cmp", m).innerHTML = side(items[a], 0) + side(items[b], 1);
    const imgs = $$(".cimg img", m);
    $$(".cimg", m).forEach(box => {
      box.onmousemove = e => { const r = box.getBoundingClientRect(); const px = (e.clientX - r.left) / r.width * 100, py = (e.clientY - r.top) / r.height * 100; imgs.forEach(im => { im.style.transformOrigin = `${px}% ${py}%`; im.style.transform = "scale(2.5)"; }); };
      box.onmouseleave = () => imgs.forEach(im => { im.style.transform = ""; });
    });
    $$("[data-side]", m).forEach(x => x.onclick = () => { if (x.dataset.side === "0") a = +x.dataset.i; else b = +x.dataset.i; draw(); });
    $$("[data-keep1]", m).forEach(x => x.onclick = () => { const keep = +x.dataset.keep1; closeModal(); confirmTrash(ids.filter(i => i !== keep)); });
  };
  draw();
}

/* ============ 操作紀錄（app/db.py op_log）============ */
export async function oplogModal(days = 30) {
  const m = modal(`<h3>操作紀錄</h3><div class="mbody" id="ol">讀取中…</div>
    <div class="mfoot"><select id="ol-days">${[7, 30, 90, 365].map(d => `<option value="${d}" ${d === days ? "selected" : ""}>最近 ${d} 天</option>`).join("")}</select>
    <a class="btn" href="/api/oplog.csv?days=3650" download>下載 CSV</a><span style="flex:1"></span><button class="btn" data-close>關閉</button></div>`, null, "wide");
  $("#ol-days", m).onchange = e => oplogModal(+e.target.value);
  let d;
  try { d = await api(`/api/oplog/summary?days=${days}`); } catch (e) { $("#ol", m).textContent = e.message; return; }
  if (!d.ops) { $("#ol", m).innerHTML = `<p class="mute">這段時間沒有操作紀錄${d.first ? "" : "（從這一版開始記）"}。</p>`; return; }
  const pct = (a, b) => b ? Math.round(100 * a / b) + "%" : "—";
  const via = d.via || {}, viaN = (via.key || 0) + (via.mouse || 0) + (via.touch || 0) + (via.pen || 0);
  const peak = d.hours.map((n, h) => [n, h]).sort((a, b) => b[0] - a[0]).slice(0, 3).filter(x => x[0]).map(x => `${x[1]} 點`).join("、");
  $("#ol", m).innerHTML = `<p class="mute" style="margin-top:0">每個會改資料的操作記一筆（撤銷紀錄只留最後 100 筆，這份一直留著）。${d.first ? `從 ${new Date(d.first * 1000).toLocaleDateString()} 開始。` : ""}</p>
    <table class="diag">
      <tr><th>總計</th><td>${fmtN(d.ops)} 次操作 · 動到 ${fmtN(d.images)} 張 · 撤銷 ${fmtN(d.undone)} 次（${pct(d.undone, d.ops)}）</td></tr>
      <tr><th>怎麼做的</th><td>鍵盤 ${pct(via.key || 0, viaN)} · 滑鼠 ${pct((via.mouse || 0) + (via.pen || 0), viaN)}${via.touch ? ` · 觸控 ${pct(via.touch, viaN)}` : ""}${via.script ? ` · 瀏覽器腳本 ${fmtN(via.script)} 次` : ""}${via[""] ? ` · 背景 ${fmtN(via[""])} 次` : ""}</td></tr>
      ${peak ? `<tr><th>最常整理</th><td>${peak}</td></tr>` : ""}
      <tr><th>最常做的</th><td>${d.kinds.slice(0, 15).map(k => `<div>${esc(k.kind)} <span class="mute">· ${fmtN(k.ops)} 次${k.images !== k.ops ? `、${fmtN(k.images)} 張` : ""}${k.undone ? ` · 撤銷 ${k.undone}` : ""}${k.by_key ? ` · 鍵盤 ${pct(k.by_key, k.ops)}` : ""}</span></div>`).join("")}</td></tr>
      <tr><th>在哪一頁</th><td>${d.views.map(([v, n]) => `${esc(v)} <span class="mute">${fmtN(n)}</span>`).join(" · ")}</td></tr>
      <tr><th>每天</th><td>${d.per_day.slice(0, 31).map(([day, n, imgs]) => `<div>${day} <span class="mute">· ${fmtN(n)} 次、${fmtN(imgs)} 張</span></div>`).join("")}</td></tr>
    </table>`;
}

/* ============ 診斷 ============ */
export async function openDiag() {
  const m = modal(`<h3>系統狀態與診斷</h3><div class="mbody" id="dg">讀取中…</div>
    <div class="mfoot"><button class="btn" id="dg-net">測試網路</button><button class="btn" id="dg-bench">模型健檢／測速</button><button class="btn" id="dg-perf">回應速度</button><button class="btn" id="dg-audit" title="隨機抽 40 張已確認的，看各種找法準不準（b96 從「更多工具」移過來）">準確率抽查</button>
    <button class="btn" id="dg-fp">補算檔案指紋</button><span style="flex:1"></span><button class="btn" data-close>關閉</button></div>`, null, "wide");
  $("#dg-audit", m).onclick = () => HOME_TOOL_FN.audit();
  const d = await api("/api/diag").catch(e => ({ error: e.message }));
  const st = await api("/api/status").catch(() => ({}));
  if (d.error) { $("#dg", m).textContent = d.error; return; }
  const gpu = d.provider_active ? d.provider_active.replace("ExecutionProvider", "") : null;
  const ok = (v, good, bad) => `<span class="${v ? "okc" : "warnc"}">${v ? good : bad}</span>`;
  const mf = d.model_files;
  $("#dg", m).innerHTML = `<table class="diag">
    <tr><th>背景工作</th><td>${st.running ? `${esc(st.phase)} ${st.done}/${st.total}${st.eta ? ` · 剩 ${fmtEta(st.eta)}` : ""}${st.paused ? "（已暫停）" : ""}` : "閒置"}${st.error ? `<br><span class="bad">${esc(st.error)}</span>` : ""}
      ${st.running ? `<button class="btn sm" id="dg-pause">${st.paused ? "繼續" : "暫停"}</button> <button class="btn sm" id="dg-stop">停止</button>` : ""}</td></tr>
    <tr><th>推論裝置</th><td>${gpu ? ok(gpu !== "CPU", gpu, "CPU（很慢）") : `<span class="mute">模型還沒載入</span>`}<br><small class="mute">可用：${esc(d.providers_available.join(", ") || "—")} · ${esc(d.ort_package || "")} ${esc(d.onnxruntime)}</small></td></tr>
    <tr><th>模型</th><td>${esc(d.model)}<br>${ok(mf["model.onnx"], `model.onnx ${fmtSize(mf["model.onnx"])}`, "尚未下載")}${mf["model_emb.onnx"] ? " · 已加入特徵輸出" : ""}${d.model_tags ? ` · ${d.model_tags.toLocaleString()} 個 tag` : ""}
      <br><small class="mute">下載來源：${esc(d.hf_endpoint)}（設定可改鏡像站）</small></td></tr>
    <tr><th>以圖搜源</th><td>${{ ok: `<span class="okc">Danbooru 相似圖搜尋（快）</span>`, no_auth: `<span class="warnc">沒有 Danbooru 帳號，只能用很慢的 IQDB</span>`, bad_auth: `<span class="bad">Danbooru 帳號或 API key 錯誤</span>`, fallback: `<span class="warnc">Danbooru 相似圖搜尋不能用，改用 IQDB</span>` }[st.online?.mode?.state] || "—"}
      ${st.online?.mode?.state !== "ok" ? ` <a class="lnk" data-dan-setup>設定帳號</a>` : ""}</td></tr>
    <tr><th>格式支援</th><td>HEIC/HEIF ${ok(d.heif, "✓", "✕（pip install pillow-heif）")} · 影片 ${ok(d.opencv, "OpenCV " + (d.opencv || ""), "✕")}</td></tr>
    <tr><th>資料</th><td>${d.counts.media.toLocaleString()} 筆 · 資料庫 ${fmtSize(d.db_size)} · 磁碟剩 ${fmtSize(d.disk_free)}
      ${d.counts.missing ? `<br>${d.counts.missing} 筆找不到檔案（搬走或刪除；搬回來會自動接回） <a class="lnk" id="dg-purge">清掉這些紀錄</a>` : ""}${d.counts.no_fp ? `<br>${d.counts.no_fp} 筆還沒有檔案指紋（搬家接回用）` : ""}</td></tr>
    <tr><th>備份</th><td>${d.backups.length ? `最近：${esc(d.backups[0].name)}（${fmtAgo(d.backups[0].mtime)}）· 共 ${d.backups.length} 份` : "還沒有"} <a class="lnk" id="dg-bk">管理</a></td></tr>
    <tr><th>監看</th><td>${d.watching ? "監看中（新檔自動處理）" : "未監看"}</td></tr>
    <tr><th>系統</th><td><small>Python ${esc(d.python)} · ${esc(d.platform)}</small></td></tr>
    <tr><th>程式出錯</th><td>${d.server_errors?.length ? d.server_errors.map(e => `<div class="bad" style="font-size:var(--fs1)">${fmtAgo(e.at)} · ${esc(e.method)} ${esc(e.path)}<br><small>${esc(e.error)}</small></div>`).join("") + `<small class="mute">詳細內容在 data/errors.log，回報問題時可以附上</small>` : `<span class="okc">這次啟動後沒有</span>`}</td></tr>
  </table><div id="dg-out"></div>`;
  const out = $("#dg-out", m);
  const pz = $("#dg-pause", m); if (pz) pz.onclick = async () => { await api("/api/jobs", { kind: st.paused ? "resume" : "pause" }); openDiag(); };
  const sp = $("#dg-stop", m); if (sp) sp.onclick = async () => { await api("/api/jobs", { kind: "stop" }); toast("已要求停止"); openDiag(); };
  $("#dg-bk", m).onclick = openBackup;
  const pg = $("#dg-purge", m); if (pg) pg.onclick = async () => {
    if (!await ask(`清掉 ${d.counts.missing} 筆找不到檔案的紀錄？`, "檔案本身早就不在了；清之前會自動備份資料庫，後悔可以從備份還原。", "清掉", true)) return;
    try { const r = await api("/api/media/purge-missing", {}); toast(`已清掉 ${r.purged} 筆`); openDiag(); } catch (e) { toast(e.message); }
  };
  $("#dg-fp", m).onclick = async () => { await api("/api/jobs", { kind: "fingerprint" }); toast("已排入"); };
  $("#dg-net", m).onclick = async () => {
    out.innerHTML = `<p class="mute">測試中…</p>`;
    const r = await api("/api/diag/net", {});
    out.innerHTML = `<h5>網路</h5><table class="diag">${r.map(x => `<tr><th>${esc(x.name)}</th><td>${x.ok ? `<span class="okc">✓ ${x.code} · ${x.ms} ms</span>` : `<span class="bad">✕ ${esc(x.error || "HTTP " + x.code)}</span>`}</td></tr>`).join("")}</table>
      <p class="mute">模型下載失敗時，可以在設定填鏡像站（例如 https://hf-mirror.com），或手動把 model.onnx、selected_tags.csv 放進 models 資料夾。</p>`;
  };
  $("#dg-perf", m).onclick = async () => {      // 回應速度、寫入鎖（b93，app/perf.py）
    const p = await api("/api/diag/perf").catch(e => ({ error: e.message }));
    if (p.error) { out.innerHTML = `<p class="bad">${esc(p.error)}</p>`; return; }
    const L = p.lock;
    out.innerHTML = `<h5>回應速度 <small class="mute">（這次啟動後；超過 ${p.slow_ms} ms 的也寫在 data/slow.log）</small></h5><table class="diag">
      <tr><th>寫入鎖</th><td>${fmtN(L.acquired)} 次 · 平均等 ${L.avg_wait_ms} ms · 最久等 <b>${Math.round(L.max_wait_ms)}</b> ms${L.max_wait_thread ? `（${esc(L.max_wait_thread)}）` : ""}
        <br><small class="mute">抱最久：${Math.round(L.max_hold_ms)} ms（${esc(L.max_hold_thread || "—")}）${p.holds.length ? ` · 超過 ${p.hold_ms} ms 的 ${p.holds.length} 次：${p.holds.slice(0, 6).map(h => `${esc(h.thread)} ${h.ms}`).join("、")}` : ""}</small></td></tr>
      <tr><th>最慢的路由</th><td>${p.routes.slice(0, 10).map(r => `<div style="font-size:var(--fs1)">${esc(r.route)} · 中位數 ${r.p50} · p90 <b>${r.p90}</b> · 最久 ${r.max} ms <span class="mute">（${r.n} 次）</span></div>`).join("") || "—"}</td></tr>
      <tr><th>慢的請求</th><td>${p.slow.slice(0, 15).map(r => `<div style="font-size:var(--fs1)">${fmtAgo(r.at)} · <b>${r.ms}</b> ms${r.lock_wait_ms >= 50 ? `（等鎖 ${Math.round(r.lock_wait_ms)}）` : ""} · ${esc(r.method)} ${esc(r.path)}</div>`).join("") || `<span class="okc">沒有</span>`}</td></tr>
    </table>`;
  };
  $("#dg-bench", m).onclick = async () => {
    out.innerHTML = `<p class="mute">載入模型並測速中（第一次可能要下載）…</p>`;
    try {
      const r = await api("/api/diag/bench", {});
      out.innerHTML = `<h5>模型健檢</h5><table class="diag"><tr><th>結果</th><td>${r.sane ? `<span class="okc">輸出正常</span>` : `<span class="bad">輸出異常</span>`} · 特徵 ${r.embedding_dim} 維</td></tr>
        <tr><th>速度</th><td><b>${r.images_per_s}</b> 張/秒（${r.per_image_ms} ms/張，${esc(r.provider.replace("ExecutionProvider", ""))}）· 載入 ${r.load_s} 秒</td></tr>
        ${r.hint ? `<tr><th></th><td class="warnc">${esc(r.hint)}</td></tr>` : ""}</table>`;
    } catch (e) { out.innerHTML = `<p class="bad">${esc(e.message)}</p>`; }
  };
}

/* ============ 手機／平板（區網：看圖庫、上傳）============ */
export async function openPhone() {
  const s = await api("/api/settings");
  const info = await api("/api/lan/info");
  modal(`<h3>手機／平板</h3><div class="mbody">
    ${!s.lan_enabled ? `<p>手機、平板跟電腦連同一個 Wi-Fi，就能用瀏覽器<b>看圖庫</b>（圖庫、角色、本子、大圖左右滑翻頁），也能把照片、影片傳進來。不用裝 App，iPhone 的 HEIC 也可以。</p>
      <p class="mute">開啟後，同一個網路裡的裝置要輸入 PIN 才進得來；進來以後只能看和上傳，確認、刪除這些整理的動作只能在電腦上做。帳號金鑰、設定、備份都不會給手機看到。電腦要開著。</p>
      <button class="btn primary" id="ph-on">開啟手機／平板連線</button>`
      : `<div class="phone"><div class="qr">${s._lan_bound ? `<img src="/api/lan/qr.svg?t=${Date.now()}" alt="QR">` : `<div class="mute" style="padding:30px 10px">重新啟動程式後<br>才會出現 QR code</div>`}</div>
      <div><p>用手機或平板的相機掃 QR code，或在瀏覽器輸入：<br><b class="mono">${esc(info.url)}</b></p>
      <p>PIN：<b class="mono big">${esc(info.pin)}</b> <a class="lnk" id="ph-pin">換一組</a></p>
      <p class="mute">上傳的檔案存到：${esc(info.dir)}<br>（會自動掃描、標記）</p>
      ${s._lan_bound ? "" : `<p class="warnc">設定已開啟，但要<b>關掉程式再重開</b>才會開放區網連線。</p>`}
      <p class="mute">Windows 第一次可能會跳出防火牆詢問，請允許「私人網路」。</p>
      <button class="btn" id="ph-off">關閉手機／平板連線</button></div></div>`}
    <h5>其他方式</h5><ul class="mute" style="line-height:1.8;margin:0;padding-left:18px">
      <li>iCloud：在 Windows 裝「iCloud」並開啟照片同步，把 iCloud 照片資料夾加入圖庫。</li>
      <li>Android：Google 相簿用 Google Takeout 匯出，或用 USB / Nearby Share 傳到圖庫資料夾。</li></ul>
  </div><div class="mfoot"><button class="btn" data-close>關閉</button></div>`, m => {
    const on = $("#ph-on", m); if (on) on.onclick = async () => { await api("/api/settings", { lan_enabled: true }); toast("已開啟，請重新啟動程式"); openPhone(); };
    const off = $("#ph-off", m); if (off) off.onclick = async () => { await api("/api/settings", { lan_enabled: false }); toast("已關閉（重新啟動後生效）"); openPhone(); };
    const pin = $("#ph-pin", m); if (pin) pin.onclick = async () => { await api("/api/lan/pin", {}); openPhone(); };
  }, "wide");
}

/* ============ 備份 / 匯出匯入 ============ */
export async function openBackup() {
  const list = await api("/api/backups");
  modal(`<h3>備份與還原</h3><div class="mbody">
    <p class="mute" style="margin-top:0">每天自動備份資料庫（標籤、確認/否決、設定），保留最近幾份（設定可改）。圖片本身不會被複製或修改。</p>
    <table class="diag">${list.map(b => `<tr><th>${esc(b.name)}</th><td>${fmtSize(b.size)} · ${fmtAgo(b.mtime)} <button class="btn sm" data-restore="${esc(b.name)}">還原到這份</button></td></tr>`).join("") || `<tr><td>還沒有備份</td></tr>`}</table>
    <h5>備份到外接硬碟</h5>
    <p class="mute">上面這些備份都在這顆硬碟上。圖片、程式、資料庫整份複製到外接硬碟，硬碟壞了也救得回來。</p>
    <button class="btn" id="bk-drive">備份到外接硬碟…</button>
    <h5>匯出／匯入整理結果</h5>
    <p class="mute">匯出你手動做的確認、否決、NSFW、排除、合併與智慧相簿（JSON）。檔案用內容指紋對應，換電腦或重新整理資料夾後也能匯回。</p>
    <div style="display:flex;gap:8px;flex-wrap:wrap"><a class="btn" href="/api/export">匯出 JSON</a><label class="btn">匯入 JSON…<input type="file" id="bk-imp" accept=".json,application/json" hidden></label>
    <button class="btn" id="bk-xmp" title="在圖片旁邊寫 .xmp（Lightroom、digiKam、Eagle 等讀得到）">寫出 XMP 側車檔</button></div>
  </div><div class="mfoot"><button class="btn" id="bk-now" style="margin-right:auto">立即備份</button><button class="btn" data-close>關閉</button></div>`, m => {
    $("#bk-now", m).onclick = async () => { await api("/api/backups", {}); toast("已備份"); openBackup(); };
    $("#bk-drive", m).onclick = () => { closeModal(); openDriveBackup(); };
    $$("[data-restore]", m).forEach(b => b.onclick = async () => {
      if (!await ask(`還原到 ${b.dataset.restore}？`, "目前的資料會先另存一份。還原會在下次啟動時套用。", "還原", true)) return;
      await api("/api/backups/restore", { name: b.dataset.restore }); toast("已排定：請關閉程式再重新啟動");
    });
    $("#bk-imp", m).onchange = async e => {
      const f = e.target.files[0]; if (!f) return;
      try {
        const r = await fetch("/api/import", { method: "POST", body: await f.text(), headers: { "Content-Type": "application/json" } });
        const d = await r.json(); if (!r.ok) throw new Error(d.detail || r.statusText);
        toast(`已匯入：對上 ${d.matched} 張（${d.tags_set} 個標籤），找不到 ${d.unmatched} 張`); loadSidebar(); render();
      } catch (err) { toast("匯入失敗：" + err.message); }
    };
    $("#bk-xmp", m).onclick = async () => {
      if (!await ask("寫出 XMP？", "在每張有已確認標籤的圖片旁邊寫一個 .xmp 檔（不會改動圖片）。", "寫出")) return;
      const r = await api("/api/export/xmp", {}); toast(`已寫出 ${r.written} 個 XMP${r.skipped ? `，略過 ${r.skipped} 個（同名不同副檔名，或已有別的軟體的 XMP）` : ""}${r.failed ? `，失敗 ${r.failed}` : ""}`);
    };
  });
}

/* ============ 備份到外接硬碟（app/drivebackup.py）============ */
const GB = n => fmtSize(n || 0);
export async function openDriveBackup() {
  let d;
  try { d = await api("/api/drive-backup"); } catch (e) { return toast(e.message); }
  const lt = d.last;
  const drv = d.drives.filter(x => !x.same_disk || x.has_backup);
  const pick = d.dest || drv.find(x => x.has_backup)?.path || "";
  const known = drv.some(x => x.path === pick || pick.startsWith(x.path));
  const m = modal(`<h3>備份到外接硬碟</h3><div class="mbody form">
    <p class="mute" style="margin-top:0">把圖庫資料夾、程式和資料庫整份複製到另一顆硬碟。只複製新的和改過的；電腦上刪掉的、被改過的舊檔，在備份裡先留 ${d.hold_days} 天。做完拔掉收好，電腦壞了也救得回來。</p>
    ${lt ? `<div class="note" style="margin:0 0 10px">上次備份 <b>${fmtAgo(lt.at)}</b>：複製 ${fmtN(lt.copied)} 個檔案（${GB(lt.copied_bytes)}）${lt.errors ? `，<span class="bad">${fmtN(lt.errors)} 個錯誤</span>` : ""} → ${esc(lt.base)}</div>` : ""}
    <div class="field"><label>備份到</label><div>
      ${drv.map(x => `<div><label class="chkl"><input type="radio" name="dbk-d" value="${esc(x.path)}" ${x.path === pick || (known && pick.startsWith(x.path)) ? "checked" : ""}> <b>${esc(x.path)}</b> ${esc(x.label || "")}　<span class="mute">剩 ${GB(x.free)}／共 ${GB(x.total)}${x.has_backup ? " · 有之前的備份" : ""}${x.same_disk ? " · 跟資料庫同一顆" : ""}</span></label></div>`).join("") || `<div class="mute">沒看到其他硬碟：插上外接硬碟後按「重新整理」</div>`}
      <div><label class="chkl"><input type="radio" name="dbk-d" value="" ${pick && !known ? "checked" : ""}> 其他位置 <input type="text" id="dbk-dest" value="${pick && !known ? esc(pick) : ""}" placeholder="例如 E:\\" style="width:220px"> <button class="btn sm" id="dbk-browse">選資料夾…</button></label></div>
      <div id="dbk-fs"></div></div></div>
    <div class="field"><label>這次</label><div id="dbk-plan" class="mute">${pick ? "算一下…" : "選一顆硬碟"}</div></div>
    <div class="bar" id="dbk-bar" style="display:none"><i></i></div>
  </div><div class="mfoot"><button class="btn" id="dbk-refresh" style="margin-right:auto">重新整理</button><span class="mute" id="dbk-sum"></span><button class="btn" data-close>關閉</button><button class="btn" id="dbk-stop" style="display:none">停止</button><button class="btn primary" id="dbk-go" disabled>開始備份</button></div>`, null, "wide");
  const dest = () => { const r = $("[name=dbk-d]:checked", m); return r ? (r.value || $("#dbk-dest", m).value.trim().replace(/^"|"$/g, "")) : ""; };
  let seq = 0, running = false;
  const showPlan = debounce(async () => {
    if (running) return;
    const my = ++seq, ds = dest();
    $("#dbk-go", m).disabled = true;
    if (!ds) { $("#dbk-plan", m).textContent = "選一顆硬碟"; return; }
    $("#dbk-plan", m).textContent = "算一下…（第一次要掃過整個圖庫，大概幾秒到一分鐘）";
    let p; try { p = await api("/api/drive-backup/plan", { dest: ds }); } catch (e) { if (my === seq) $("#dbk-plan", m).textContent = e.message; return; }
    if (my !== seq || running || !document.body.contains(m)) return;
    if (!p.ok) { $("#dbk-plan", m).innerHTML = p.errors.map(e => `<div class="bad">${esc(e)}</div>`).join(""); return; }
    const rows = p.sources.map(x => `<tr><th>${esc(x.area === "圖片" ? x.label : x.area)}</th><td>${x.missing ? `<span class="bad">讀不到 ${esc(x.path)}</span>` : `${fmtN(x.files)} 個 · ${GB(x.bytes)}${x.copy ? ` · <b>要複製 ${fmtN(x.copy)} 個（${GB(x.copy_bytes)}）</b>` : " · 沒有變動"}<br><span class="mute">${esc(x.path)}</span>`}</td></tr>`).join("");
    $("#dbk-plan", m).innerHTML = `<table class="diag">${rows}</table>
      <div style="margin-top:6px">${p.first ? "第一次備份：" : ""}要複製 <b>${fmtN(p.copy)}</b> 個檔案（${GB(p.copy_bytes)}）＋資料庫 ${GB(p.db_bytes)}；硬碟剩 ${GB(p.free)}${p.enough ? "" : ` <span class="bad">— 空間不夠</span>`}</div>
      ${p.gone ? `<div class="mute">電腦上已經不在的 ${fmtN(p.gone)} 個檔案：移到備份裡的「_刪除保留」，${d.hold_days} 天後才清</div>` : ""}
      ${p.cloud ? `<div class="mute">${fmtN(p.cloud)} 個檔案只在 iCloud 雲端、還沒下載到電腦：這次不複製（不會觸發下載）</div>` : ""}
      ${p.warnings.map(w => `<div class="bad">${esc(w)}</div>`).join("")}`;
    $("#dbk-go", m).disabled = !p.enough;
  }, 300);
  const watch = () => {
    running = true;
    ["#dbk-go", "#dbk-refresh"].forEach(k => $(k, m).disabled = true);
    $("#dbk-stop", m).style.display = ""; $("#dbk-bar", m).style.display = "";
    follow(() => api("/api/drive-backup/job"), j => {
      if (!document.body.contains(m)) {
        if (j.state === "running") return;
        toast(j.state === "done" ? `外接硬碟備份完成：複製 ${fmtN(j.result.copied)} 個檔案` : `外接硬碟備份${j.state === "stopped" ? "已停止" : "失敗：" + (j.error || "")}`);
        pollOnce(); return false;
      }
      $("#dbk-bar i", m).style.width = `${j.state !== "running" ? 100 : j.bytes_total ? Math.round(j.bytes_done / j.bytes_total * 100) : 0}%`;
      if (j.state === "running") {
        $("#dbk-sum", m).textContent = j.phase === "複製" ? `複製中 ${fmtN(j.done)}／${fmtN(j.total)}（${GB(j.bytes_done)}／${GB(j.bytes_total)}）…關掉視窗也會繼續` : `${j.phase || "準備中"}…`;
        return;
      }
      $("#dbk-stop", m).style.display = "none"; $("#dbk-refresh", m).disabled = false; running = false;
      const r = j.result;
      if (j.state === "done") {
        $("#dbk-sum", m).textContent = ""; $("#dbk-go", m).style.display = "none";
        $("#dbk-plan", m).innerHTML = `<div class="note" style="margin:0"><b>${r.ok ? "備份完成" : "備份完成，但有些地方要看一下"}</b>（${r.seconds} 秒）<br>
          複製 ${fmtN(r.copied)} 個（${GB(r.copied_bytes)}）· 沒變 ${fmtN(r.unchanged)} 個${r.held ? ` · ${fmtN(r.held)} 個移到 _刪除保留` : ""}${r.replaced ? ` · ${fmtN(r.replaced)} 個改過的舊版先留著` : ""}<br>
          抽查 ${fmtN(r.verified)} 個檔案${r.verify_bad ? `，<span class="bad">${r.verify_bad} 個不一樣</span>` : "，內容都一樣"} · 資料庫快照 ${esc(r.db?.name || "")}
          ${r.cloud ? `<br><span class="mute">${fmtN(r.cloud)} 個只在 iCloud 雲端的檔案沒有複製</span>` : ""}
          ${r.skipped_dirs.length ? `<br><span class="bad">讀不到、這次沒備份：${r.skipped_dirs.map(esc).join("、")}</span>` : ""}
          ${r.guarded.length ? `<br><span class="bad">${r.guarded.map(g => esc(g.path)).join("、")} 一次少了很多檔案，像是暫時讀不到，這次沒有把備份裡的移走</span>` : ""}
          ${r.error_list?.length ? `<details style="margin-top:6px"><summary class="bad">${fmtN(r.errors)} 個檔案沒有複製成功</summary>${r.error_list.map(e => `<div class="mute">${esc(e.path)}：${esc(e.error)}</div>`).join("")}</details>` : ""}
          <br>拔硬碟前，先在 Windows 右下角的「安全地移除硬體」退出。</div>`;
      } else {
        $("#dbk-sum", m).textContent = j.state === "stopped" ? "已停止（已經複製的會留著，下次接著做）" : `失敗：${j.error || ""}`;
        showPlan();
      }
      pollOnce();
      return false;
    }, { every: 800 });
  };
  m.addEventListener("change", e => { if (e.target.name === "dbk-d" || e.target.id === "dbk-dest") showPlan(); });
  $("#dbk-dest", m).addEventListener("input", () => { $("[name=dbk-d][value='']", m).checked = true; showPlan(); });
  $("#dbk-browse", m).onclick = () => folderBrowser($("#dbk-fs", m), p => { $("#dbk-dest", m).value = p; $("[name=dbk-d][value='']", m).checked = true; $("#dbk-fs", m).innerHTML = ""; showPlan(); });
  $("#dbk-refresh", m).onclick = () => { closeModal(); openDriveBackup(); };
  $("#dbk-stop", m).onclick = async () => { await api("/api/drive-backup/cancel", {}); $("#dbk-sum", m).textContent = "停止中…"; };
  $("#dbk-go", m).onclick = async () => {
    try { await api("/api/drive-backup/start", { dest: dest() }); } catch (e) { return toast(e.message); }
    watch();
  };
  if (d.job.state === "running") watch(); else if (pick) showPlan();
}

/* ============ 首次設定精靈 ============ */
let wizardShown = false;
function openWizard() {
  if (wizardShown && modalOpen()) return; wizardShown = true;
  const W = { step: 0, roots: [], model: "SmilingWolf/wd-eva02-large-tagger-v3", online: true, nsfw: S.nsfw, mirror: "" };
  const m = modal(`<div class="wiz" id="wiz"></div>`, null, "wide");
  const steps = ["資料夾", "模型", "線上與隱私"];
  const draw = () => {
    const body = [
      () => `<p>選擇要整理的圖片／影片資料夾（可以多個，子資料夾會一起掃描）。程式<b>不會移動或修改</b>你的檔案。</p>
        <div class="roots">${W.roots.map((r, i) => `<div class="rootrow"><span>${svg("folder", 16)} ${esc(r)}</span><button data-rm="${i}">✕</button></div>`).join("") || `<div class="mute">還沒選擇</div>`}</div>
        <div id="fsb"></div>`,
      () => `<p>AI 模型會描述每張圖（髮色、服裝…）、判斷分級，並推薦可能的角色（只當候選，你確認才算）。</p>
        ${[["SmilingWolf/wd-eva02-large-tagger-v3", "最準（推薦）", "約 1.2 GB，建議有獨立顯卡"], ["SmilingWolf/wd-vit-tagger-v3", "較輕", "約 380 MB，內顯或舊電腦用"]].map(([k, l, sub]) => `<label class="opt2 ${W.model === k ? "on" : ""}"><input type="radio" name="wm" value="${k}" ${W.model === k ? "checked" : ""}><b>${l}</b><span>${sub}</span></label>`).join("")}
        <div class="field2"><label>下載鏡像站（選填，下載很慢或失敗時用）</label><input id="w-mirror" placeholder="例如 https://hf-mirror.com" value="${esc(W.mirror)}"></div>`,
      () => `<label class="opt2 ${W.online ? "on" : ""}"><input type="checkbox" id="w-online" ${W.online ? "checked" : ""}><b>使用 Danbooru / Pixiv 找來源</b><span>找得到原圖時，角色、作品、畫師最準。IQDB 以圖搜圖會上傳縮小的圖片（可在設定關閉）。</span></label>
        ${W.online ? `<div class="danwarn no_auth" style="margin-top:10px"><div><b>強烈建議填 Danbooru 帳號</b><div class="mute">${DAN_WHY}<br>
          <a href="https://danbooru.donmai.us/users/new" target="_blank" rel="noopener">註冊</a> → <a href="https://danbooru.donmai.us/api_keys" target="_blank" rel="noopener">API Keys</a> → Add（權限全部不勾）</div>
          <div class="wdan"><input id="w-dl" placeholder="帳號名稱" value="${esc(W.dl || "")}"><input id="w-dk" type="password" placeholder="API key" value="${esc(W.dk || "")}"></div></div></div>` : ""}
        <p style="margin-top:14px">NSFW 內容預設：</p><div class="seg">${[["hide", "隱藏"], ["blur", "模糊"], ["show", "顯示"]].map(([k, l]) => `<button data-ns="${k}" class="${W.nsfw === k ? "on" : ""}">${l}</button>`).join("")}</div>
        <p class="mute">隨時可以按 H 切換。</p>`,
    ][W.step]();
    $("#wiz", m).innerHTML = `<h3>開始設定 <small class="mute">${W.step + 1} / 3 · ${steps[W.step]}</small></h3><div class="mbody">${body}</div>
      <div class="mfoot">${W.step ? `<button class="btn" id="w-back">上一步</button>` : `<button class="btn" data-close>稍後再說</button>`}<span style="flex:1"></span>
      <button class="btn primary" id="w-next" ${W.step === 0 && !W.roots.length ? "disabled" : ""}>${W.step === 2 ? "完成，開始整理" : "下一步"}</button></div>`;
    if (W.step === 0) {
      folderBrowser($("#fsb", m), p => { if (!W.roots.includes(p)) W.roots.push(p); draw(); });
      $$("[data-rm]", m).forEach(b => b.onclick = () => { W.roots.splice(+b.dataset.rm, 1); draw(); });
    }
    $$("input[name=wm]", m).forEach(r => r.onchange = () => { W.model = r.value; draw(); });
    const mi = $("#w-mirror", m); if (mi) mi.oninput = () => { W.mirror = mi.value.trim(); };
    const on = $("#w-online", m); if (on) on.onchange = () => { W.online = on.checked; draw(); };
    const dl = $("#w-dl", m); if (dl) { dl.oninput = () => { W.dl = dl.value.trim(); }; dl.onkeydown = e => e.stopPropagation(); }
    const dk = $("#w-dk", m); if (dk) { dk.oninput = () => { W.dk = dk.value.trim(); }; dk.onkeydown = e => e.stopPropagation(); }
    $$("[data-ns]", m).forEach(b => b.onclick = () => { W.nsfw = b.dataset.ns; draw(); });
    const bk = $("#w-back", m); if (bk) bk.onclick = () => { W.step--; draw(); };
    $("#w-next", m).onclick = async () => {
      if (W.step < 2) { W.step++; draw(); return; }
      await api("/api/settings", { roots: W.roots, model_repo: W.model, hf_endpoint: W.mirror, online_enabled: W.online, setup_done: true,
        ...(W.dl && W.dk ? { danbooru_login: W.dl, danbooru_api_key: W.dk } : {}) });
      if (W.online && !(W.dl && W.dk)) toast("沒填 Danbooru 帳號：以圖搜源會很慢，首頁有設定提示");
      S.nsfw = W.nsfw; store.set("nsfw", S.nsfw); applyNsfwUI();
      closeModal(); toast("開始掃描！第一次會先下載模型，進度在右上角"); go("home"); pollOnce();
    };
  };
  draw();
}

/* ---- 資料夾瀏覽器 ---- */
export async function folderBrowser(el, onPick, path = "") {
  let d;
  try { d = await api(`/api/fs?path=${encodeURIComponent(path)}`); } catch (e) { el.innerHTML = `<div class="bad">${esc(e.message)}</div>`; return; }
  const row = (x, extra = "") => `<div class="fsrow" data-p="${esc(x.path)}">${svg("folder", 16)}<span>${esc(x.name)}</span>${x.n ? `<i>${x.n}${x.n > 3000 ? "+" : ""} 張</i>` : ""}${extra}</div>`;
  el.innerHTML = `<div class="fsb">
    <div class="fspath">${d.path ? `<button data-up="${esc(d.parent || "")}">‹</button><b>${esc(d.path)}</b>${d.n ? `<i>${d.n} 張</i>` : ""}<button class="btn sm primary" data-add="${esc(d.path)}">加入這個資料夾</button>` : `<b>選擇位置</b>`}</div>
    <div class="fslist">${d.path ? (d.dirs.map(x => row(x)).join("") || `<div class="mute" style="padding:10px">沒有子資料夾</div>`)
      : `${d.places.map(x => row(x)).join("")}<div class="fsh">磁碟</div>${d.drives.map(x => row(x)).join("")}`}</div>
    <div class="fsman"><input placeholder="或直接貼上路徑，例如 D:\\Pictures" id="fs-in"><button class="btn sm" id="fs-go">前往</button></div></div>`;
  $$(".fsrow", el).forEach(r => r.onclick = () => folderBrowser(el, onPick, r.dataset.p));
  const up = $("[data-up]", el); if (up) up.onclick = () => folderBrowser(el, onPick, up.dataset.up);
  const add = $("[data-add]", el); if (add) add.onclick = () => onPick(add.dataset.add);
  const inp = $("#fs-in", el);
  inp.addEventListener("keydown", e => { e.stopPropagation(); if (e.key === "Enter") $("#fs-go", el).click(); });
  $("#fs-go", el).onclick = () => { const v = inp.value.trim().replace(/^"|"$/g, ""); if (v) folderBrowser(el, onPick, v); };
}


/* ============ Danbooru 帳號：沒有的話以圖搜源會非常慢 ============ */
export const DAN_WHY = `沒有 Danbooru 帳號時，以圖搜源只能用公開的 IQDB，一張常要 <b>30–60 秒</b>，5,000 張要跑<b>兩天以上</b>，而且常逾時。
  填了免費帳號的 API key，就能用 Danbooru 自己的相似圖搜尋，快很多。`;
function danBanner(mode, where = "home") {
  if (!mode || mode.state === "ok") return "";
  const t = { no_auth: ["還沒設定 Danbooru 帳號，以圖搜源會非常慢", DAN_WHY],
    bad_auth: ["Danbooru 帳號或 API key 錯誤", "Danbooru 拒絕了登入，以圖搜源退回很慢的 IQDB。請檢查帳號名稱（不是 email）和 API key。"],
    fallback: ["Danbooru 相似圖搜尋暫時不能用，先用較慢的 IQDB", `Danbooru 回應失敗${mode.reason ? `（${esc(mode.reason)}）` : ""}，通常是暫時的（限速或伺服器忙）。${mode.retry_in ? `約 ${Math.ceil(mode.retry_in / 60)} 分鐘後會自動再試。` : "會自動再試。"}`] }[mode.state];
  if (!t) return "";
  return `<div class="danwarn ${mode.state}"><div><b>${t[0]}</b><div class="mute">${t[1]}</div></div><button class="btn primary sm" data-dan-setup>${mode.state === "no_auth" ? "設定帳號（免費，2 分鐘）" : mode.state === "fallback" ? "立即重試" : "檢查設定"}</button></div>`;
}
document.addEventListener("click", e => { if (e.target.closest("[data-dan-setup]")) openDanbooruSetup(); });
async function openDanbooruSetup() {
  const s = await api("/api/settings");
  modal(`<h3>設定 Danbooru 帳號</h3><div class="mbody">
    <p style="margin-top:0">${DAN_WHY}</p>
    <ol class="steps">
      <li>到 Danbooru <a href="https://danbooru.donmai.us/users/new" target="_blank" rel="noopener">註冊免費帳號</a>（已經有就直接登入）</li>
      <li>打開 <a href="https://danbooru.donmai.us/api_keys" target="_blank" rel="noopener">API Keys 頁面</a> → <b>Add</b> → 權限可以全部不勾（只需要讀取）→ 建立</li>
      <li>把<b>帳號名稱</b>（不是 email）和產生的 <b>API key</b> 貼到下面，按「儲存並測試」</li>
    </ol>
    <div class="form"><div class="field"><label>帳號名稱</label><input type="text" id="dz-login" value="${esc(s.danbooru_login || "")}" autocomplete="off"></div>
    <div class="field"><label>API key</label><input type="password" id="dz-key" value="${esc(s.danbooru_api_key || "")}" autocomplete="off"></div></div>
    <div id="dz-out" class="dzout"></div>
    <p class="mute" style="font-size:var(--fs1)">只存在你電腦的 data/library.db，只用來向 Danbooru 查詢。匯出整理結果時不會包含。</p>
  </div><div class="mfoot"><button class="btn" data-close>稍後再說</button><button class="btn primary" id="dz-go">儲存並測試</button></div>`, m => {
    $$("input", m).forEach(i => i.addEventListener("keydown", e => e.stopPropagation()));
    $("#dz-go", m).onclick = async () => {
      const out = $("#dz-out", m); out.className = "dzout"; out.textContent = "測試中…";
      await api("/api/settings", { danbooru_login: $("#dz-login", m).value.trim(), danbooru_api_key: $("#dz-key", m).value.trim() });
      try {
        const r = await api("/api/online/test-danbooru", {});
        out.className = "dzout " + (r.ok && r.iqdb ? "ok" : r.ok ? "warn" : "bad");
        out.textContent = (r.ok && r.iqdb ? "✓ " : "✕ ") + r.msg;
        if (r.ok) { S.settings = await api("/api/settings"); pollOnce(); if (S.view === "home") setTimeout(render, 800); }
      } catch (e) { out.className = "dzout bad"; out.textContent = "✕ " + e.message; }
    };
  });
}

/* ============ iCloud 中轉：提醒清空「最近刪除」 ============ */
export function icloudClear() {
  const m = modal(`<h3>清空 iCloud「最近刪除」</h3><div class="mbody">
    移出的圖已經安全存在電腦上，但 iCloud 會把它們留在「最近刪除」30 天，這段時間<b>照樣占 iCloud 空間</b>，iPad 就一直顯示空間已滿。
    <ol style="margin:10px 0 0 18px;line-height:1.8">
      <li>用瀏覽器打開 <a href="https://www.icloud.com/photos/" target="_blank" rel="noopener">icloud.com/photos</a> → 左邊「最近刪除」</li>
      <li>點第一張，捲下去按住 <kbd>Shift</kbd> 點另一張（上方會顯示已選幾項，<b>不要超過 1,000</b>）→「刪除」</li>
      <li>重複到清空，然後按下面「我清好了」</li>
    </ol>
    <div class="note">永久刪除後救不回來；這些圖在電腦的保存資料夾都有完整一份。</div></div>
    <div class="mfoot"><button class="btn" id="ic-later">晚點再說</button><button class="btn primary" id="ic-done">我清好了</button></div>`);
  $("#ic-later", m).onclick = closeModal;
  $("#ic-done", m).onclick = async () => { await api("/api/settings", { relay_uncleared: 0, relay_uncleared_bytes: 0 }); closeModal(); toast("好，已重設提醒"); render(); };
}

/* ============ 從畫師反推出處 ============ */
export function artistScanModal() {
  const m = modal(`<h3>從畫師反推出處</h3><div class="mbody">
    <p class="mute" style="margin-top:0">從你已經確認的畫師出發，抓他們在 Danbooru 上的作品小縮圖（每張約 8 KB），在本機跟還沒找到出處的圖比對。<b>不會上傳你的任何圖片。</b></p>
    <div class="form">
      <div class="field"><label>畫師數</label><input type="number" id="as-n" value="40" min="1" max="2000"></div>
      <div class="field"><label>每位畫師最多看幾張作品</label><input type="number" id="as-p" value="300" min="10" max="5000"></div>
      <div class="field"><label>流量上限（MB）</label><input type="number" id="as-mb" value="150" min="1" max="2000"></div>
      <div class="field"><label>只統計</label><label><input type="checkbox" id="as-dry"> 只看能找到多少，不寫入圖庫</label></div>
    </div>
    <div id="as-prog"></div></div>
    <div class="mfoot"><button class="btn" id="as-stop">停止</button><button class="btn" data-close>關閉</button><button class="btn primary" id="as-go">開始</button></div>`);
  const prog = $("#as-prog", m);
  const bar = (label, v, max, extra = "") => `<div class="asrow"><span>${label}</span><div class="asbar"><i style="width:${max ? Math.min(100, 100 * v / max).toFixed(1) : 0}%"></i></div><b>${extra || `${fmtN(v)} / ${fmtN(max)}`}</b></div>`;
  let stopF = null, maxMb = 150;
  const draw = async () => {
    let a; try { a = await api("/api/online/artistscan"); } catch { return; }
    if (!a || !a.started) { prog.innerHTML = ""; return "done"; }
    const done = !!a.finished;
    prog.innerHTML = `<div class="asbox"><div class="ashead"><b>${done ? "完成" : "正在找"}${a.dry_run ? "（只統計）" : ""}</b>
        <span class="mute">${done ? `花了 ${fmtEta(a.seconds)}` : ""}</span></div>
      ${bar("畫師", a.artists_done, a.artists_total, `${a.artists_done} / ${a.artists_total}`)}
      ${bar("流量", a.mb, maxMb, `${a.mb} / ${maxMb} MB`)}
      <div class="asstats"><span>看過作品 <b>${fmtN(a.posts)}</b></span><span>下載縮圖 <b>${fmtN(a.downloaded)}</b></span>
        <span>已經有的 <b>${fmtN(a.skipped_have)}</b></span><span class="acc">對上 <b>${fmtN(a.matched)}</b>${a.by_emb ? `（特徵 ${a.by_emb}）` : ""}</span>
        <span>還沒找到出處的圖 <b>${fmtN(a.targets)}</b></span></div></div>`;
    if (done) { loadSidebar(); return "done"; }
  };
  const poll = () => { stopF?.(); stopF = follow(draw, r => r !== "done", { every: 2000, alive: () => document.body.contains(m) }); };
  $("#as-go", m).onclick = async () => {
    maxMb = +$("#as-mb", m).value || 150;
    try {
      await api("/api/online/artistscan", { max_artists: +$("#as-n", m).value, max_posts: +$("#as-p", m).value, max_mb: maxMb,
        dry_run: $("#as-dry", m).checked });
      toast("開始從畫師反推（背景進行）"); setTimeout(poll, 800);
    } catch (e) { toast(e.message); }
  };
  $("#as-stop", m).onclick = async () => { await api("/api/online/stop", {}); toast("已要求停止"); };
  poll();
}

/* ============ 找出處（補跑舊圖）============
   b69：跟設定裡的「補跑」同一個視窗（選範圍＋選步驟）；以前這裡另有一套像素指紋／簽名／Rule34／IQDB 的勾選。 */
export function findModal(miss) {
  return openRerun({ steps: ["source", "sig"], preset: miss === "artist" ? "noartist" : "unrec" });
}

/* ============ 準確率抽查 ============ */
const METHOD_NAME = { md5: "原檔 MD5", md5_name: "檔名 MD5", pixel_hash: "像素指紋", x_media: "X 圖片編號", tweet: "推文", pixiv_id: "Pixiv 作品 ID", iqdb: "IQDB 相似圖",
  pixiv: "Pixiv", duplicate: "重複圖沿用", artist_scan: "從畫師反推", signature: "圖上簽名", r34_md5: "Rule34 原檔", e621_md5: "e621 原檔", e621_iqdb: "e621 以圖搜圖", pixel_hash: "像素指紋", manual: "手動／AI 確認" };
export async function auditModal(kind = "artist") {
  let items; try { items = await api(`/api/audit/sample?n=40&kind=${kind}&nsfw=${apiNsfw()}`); } catch (e) { return toast(e.message); }
  if (!items.length) return toast("還沒有已確認的標籤可以抽查");
  const bad = new Set();
  const m = modal(`<h3>準確率抽查：${kind === "artist" ? "創作者" : "角色"}</h3><div class="mbody">
    <p class="mute" style="margin-top:0">隨機抽了 ${items.length} 張。標錯的點一下（變紅），全部看完按「完成」：會算出每種找法的準確率，並把標錯的否決掉（可撤銷）。</p>
    <div class="audit">${items.map((it, i) => `<div class="au" data-i="${i}"><img loading="lazy" src="/t/${it.id}" alt=""><div class="aut"><b>${esc(it.tag)}</b><span>${esc(METHOD_NAME[it.method] || it.method)}</span></div><span class="aux">✕ 不對</span></div>`).join("")}</div></div>
    <div class="mfoot"><span class="mute" id="au-n" style="margin-right:auto"></span><button class="btn" data-close>取消</button><button class="btn primary" id="au-ok">完成</button></div>`, null, "wide");
  const upd = () => { $("#au-n", m).textContent = `標為不對：${bad.size} / ${items.length}`; };
  $(".audit", m).onclick = e => {
    const c = e.target.closest(".au"); if (!c) return;
    if (e.detail === 2) return openViewer(items[+c.dataset.i].id);
    const i = +c.dataset.i; bad.has(i) ? bad.delete(i) : bad.add(i); c.classList.toggle("bad", bad.has(i)); upd();
  };
  upd();
  $("#au-ok", m).onclick = async () => {
    const r = await api("/api/audit/result", { results: items.map((it, i) => ({ ...it, ok: !bad.has(i) })) });
    const rows = Object.entries(r.by_method).sort((a, b) => (b[1].ok + b[1].bad) - (a[1].ok + a[1].bad))
      .map(([k, v]) => `<tr><td>${esc(METHOD_NAME[k] || k)}</td><td>${v.ok + v.bad}</td><td><b>${Math.round(100 * v.ok / (v.ok + v.bad))}%</b></td></tr>`).join("");
    modal(`<h3>抽查結果</h3><div class="mbody"><table class="clist"><thead><tr><th>找法</th><th>張數</th><th>準確率</th></tr></thead><tbody>${rows}</tbody></table>
      ${r.rejected ? `<p class="mute">已否決 ${r.rejected} 個標錯的（右上角可撤銷）。</p>` : ""}</div><div class="mfoot"><button class="btn primary" data-close>好</button></div>`);
    loadSidebar();
  };
}


/* ============ 從網站匯入 ============ */
const IMPORT_SITES = { x: "X", pixiv: "Pixiv", danbooru: "Danbooru", e621: "e621", r34: "Rule34", ehentai: "E-Hentai", nhentai: "nhentai" };
const IMPORT_RX = /https?:\/\/(?:www\.|mobile\.)?(?:(?:twitter|x|fxtwitter|vxtwitter|fixupx)\.com\/\w+\/status\/\d+\S*|pixiv\.net\/\S*(?:artworks\/|illust_id=)\d+\S*|(?:danbooru|safebooru)\.donmai\.us\/posts\/\d+\S*|(?:e621|e926)\.net\/posts\/\d+\S*|rule34\.xxx\/index\.php\?\S*id=\d+\S*|i\.pximg\.net\/\S*?\d+(?:-[0-9a-f]{32})?_p\d+\S*|(?:e-|ex)hentai\.org\/g\/\d+\/[0-9a-f]{10}\S*|nhentai\.net\/g\/\d+\S*)/gi;
// 其他網站（gallery-dl）：貼文頁的網址都收，伺服器決定認不認得
const ANY_URL_RX = /https?:\/\/[^\s<>"'「」『』（）()\[\]]+/gi;
// 整本匯入（E-Hentai、nhentai）、gallery-dl 的背景下載進度：視窗開著就每 2 秒更新一次
function galleryWatch(m, row, url) {
  const tick = async () => {
    const j = ((await api("/api/fetch-url/galleries").catch(() => ({ jobs: [] }))).jobs || []).find(x => x.url === url);
    if (!j) return "end";
    const u = $(".u", row);
    if (j.kind === "gdl") {          // gallery-dl（其他網站）
      if (j.state === "error") { $(".st", row).innerHTML = `<span class="warnc">✕ 失敗</span>`; u.textContent = `${j.label}：${j.error}`; return "end"; }
      if (j.state === "done" || j.state === "stopped") {
        $(".st", row).innerHTML = `<span class="okc">✓ ${esc(j.label)}</span>`;
        u.textContent = `${j.saved ? `存了 ${j.saved} 個檔案` : j.skipped ? "沒有新的（以前都抓過了）" : "圖庫裡已經有了"}${j.existing && j.saved ? `（${j.existing} 個本來就有）` : ""}${j.skipped && j.saved ? `（${j.skipped} 個以前抓過）` : ""}${j.error ? `。${j.error}` : ""}`; return "end"; }
      u.textContent = `${j.label}：gallery-dl 下載中，已抓 ${j.done} 個、放進圖庫 ${j.saved} 個${j.skipped ? `（${j.skipped} 個以前抓過）` : ""}${j.limit ? `，最新 ${j.limit} 篇` : ""}…（關掉視窗也會繼續）`;
      return;
    }
    if (j.state === "error") { $(".st", row).innerHTML = `<span class="warnc">✕ 中斷</span>`; u.textContent = `${j.title}：下載到第 ${j.done}／${j.total} 頁時出錯：${j.error}（再匯入一次會從中斷的地方接著下載）`; return "end"; }
    if (j.state === "done" || j.state === "stopped") { $(".st", row).innerHTML = `<span class="okc">✓ 完成</span>`; u.textContent = `${j.title}：${j.done} 頁${j.skipped ? `（${j.skipped} 頁本來就有）` : ""}。標記完會自動建立本子`; return "end"; }
    u.textContent = `${j.title}：背景下載 ${j.done}／${j.total} 頁…（關掉視窗也會繼續）`;
  };
  setTimeout(() => follow(tick, r => r !== "end", { every: 1500, alive: () => m.isConnected }), 600);
}
export function importModal(prefill = "") {
  const m = modal(`<h3>從網站匯入</h3><div class="mbody">
    <p class="mute" style="margin-top:0">貼上貼文網址（一行一個，也可以整段文字直接貼，會自動挑出網址）。原圖會存到圖庫資料夾裡的 <b>匯入／網站名稱</b>，然後跟新圖一樣自動標記、找出處。</p>
    <textarea id="im-urls" rows="5" placeholder="https://x.com/…/status/…&#10;https://www.pixiv.net/artworks/…&#10;https://danbooru.donmai.us/posts/…" style="width:100%;box-sizing:border-box">${esc(prefill)}</textarea>
    <div class="mute" style="font-size:var(--fs1);margin:6px 0 0">支援：X（不用登入）、Pixiv（R-18 要在設定填登入 cookie；貼某一頁的圖片網址只存那一頁）、E-Hentai／nhentai 整本（背景下載，完成後自動建立本子；nhentai 建議用瀏覽器按鈕）、Danbooru、e621、Rule34（要先填 API key）。
      <span id="im-gdl">其他網站（Bluesky、DeviantArt、ArtStation、Gelbooru、yande.re、Misskey…）交給 gallery-dl。</span></div>
    <div id="im-res" class="imres"></div>
    <details class="imext"><summary><b>瀏覽器按鈕</b>：在網站上直接按「⤓ 存到圖庫」</summary>
      <ol class="steps">
        <li>瀏覽器裝 <a href="https://www.tampermonkey.net/" target="_blank" rel="noopener">Tampermonkey</a> 擴充功能</li>
        <li>點 <a href="/api/fetch-url/userscript.user.js" target="_blank" rel="noopener">安裝「存到圖庫」腳本</a>，在跳出的頁面按「安裝」</li>
        <li>之後在 X、Pixiv、Danbooru、e621、Rule34 的貼文頁右下角會有「⤓ 存到圖庫」；X 的時間軸上每則有圖的貼文也有小按鈕，不用點進去。Pixiv 多頁作品會先讓你選要存哪幾頁，作品頁裡每一頁大圖上也有「⤓ 存這張」</li>
      </ol>
      <div class="note">腳本裡有一把只屬於你的配對金鑰，只有裝了腳本的瀏覽器能把圖送進來。覺得外流了就 <a class="lnk" id="im-rekey">重新產生金鑰</a>（舊腳本會失效，再裝一次）。</div>
    </details></div>
    <div class="mfoot"><button class="btn" data-close>關閉</button><button class="btn primary" id="im-go">匯入</button></div>`, null, "wide");
  const ta = $("#im-urls", m), res = $("#im-res", m);
  $("#im-rekey", m).onclick = async () => { await api("/api/fetch-url/key/reset", {}); toast("已重新產生金鑰：請再安裝一次腳本"); };
  api("/api/gdl").then(g => {          // gallery-dl 還沒裝：說一聲、給一顆安裝鈕
    const el = $("#im-gdl", m); if (!el) return;
    if (g.available) { el.textContent += `（gallery-dl ${g.version}；整個帳號的網址${g.max_posts ? `只抓最新 ${g.max_posts} 篇` : "全部抓"}，再貼一次只抓新的）`; return; }
    el.innerHTML = `其他網站（Bluesky、DeviantArt…）要用 gallery-dl，還沒安裝。<a class="lnk" id="im-gdl-inst">安裝 gallery-dl</a>`;
    $("#im-gdl-inst", m).onclick = async e => { e.target.textContent = "安裝中…（約半分鐘）";
      try { const r = await api("/api/gdl/install", {}); el.textContent = `已安裝 gallery-dl ${r.version}，其他網站的網址也可以貼了。`; }
      catch (err) { el.textContent = err.message; } };
  }).catch(() => {});
  $("#im-go", m).onclick = async () => {
    const known = (ta.value.match(IMPORT_RX) || []).map(u => u.replace(/[)\]」』>,，。]+$/, ""));
    const other = (ta.value.match(ANY_URL_RX) || []).map(u => u.replace(/[)\]」』>,，。.]+$/, "")).filter(u => !known.some(k => k.startsWith(u) || u.startsWith(k)));
    const urls = [...new Set([...known, ...other])];
    if (!urls.length) { res.innerHTML = `<div class="warnc">沒有找到支援的網址</div>`; return; }
    $("#im-go", m).disabled = true;
    res.innerHTML = urls.map((u, i) => `<div class="imrow" id="imr${i}"><span class="st">等待中</span><span class="u">${esc(u)}</span></div>`).join("");
    let saved = 0, fail = 0;
    for (let i = 0; i < urls.length; i++) {
      const row = $("#imr" + i, m); if (!row) break;
      $(".st", row).textContent = "下載中…";
      try {
        const r = await api("/api/fetch-url", { url: urls[i] });
        if (r.ok && r.queued) { $(".st", row).innerHTML = `<span class="okc">⤓ ${esc(IMPORT_SITES[r.site] || r.label || r.site)}</span>`;
          $(".u", row).textContent = r.kind === "gdl" ? `${r.label}：gallery-dl 下載中…` : `${r.title || ""}：背景下載 ${r.count} 頁…`; galleryWatch(m, row, r.post_url); continue; }
        if (r.ok) { saved += r.saved.length; $(".st", row).innerHTML = `<span class="okc">✓ ${IMPORT_SITES[r.site] || r.site}</span>`;
          $(".u", row).textContent = `${r.saved.length ? `存了 ${r.saved.length} 張` : "圖庫裡已經有了"}${r.existing.length && r.saved.length ? `（${r.existing.length} 張本來就有）` : ""}${r.author ? ` · ${r.author}` : ""}`; }
        else { fail++; $(".st", row).innerHTML = `<span class="warnc">✕ 失敗</span>`; $(".u", row).textContent = r.error; }
      } catch (e) { fail++; $(".st", row).innerHTML = `<span class="warnc">✕ 失敗</span>`; $(".u", row).textContent = e.message; }
    }
    $("#im-go", m).disabled = false;
    if (saved) { res.insertAdjacentHTML("beforeend", `<div class="note" style="margin-top:8px">已存 ${saved} 張，背景標記、找出處中（右上角看進度）。<a class="lnk" id="im-see">看匯入的圖 ›</a></div>`);
      $("#im-see", m).onclick = () => { closeModal(); runSearch("path:匯入"); }; }
  };
  if (prefill) setTimeout(() => $("#im-go", m).click(), 50);
}
// 在程式裡任何地方（不是輸入框）貼上貼文網址 → 直接打開匯入
document.addEventListener("paste", e => {
  if (e.target.closest && e.target.closest("input, textarea, [contenteditable]")) return;
  const t = e.clipboardData?.getData("text") || "";
  if ((t.match(IMPORT_RX) || []).length) { e.preventDefault(); importModal(t); }
});
