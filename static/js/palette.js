// ES module（2026-10）：用到別的檔案的東西都要在這裡 import；瀏覽器會在載入時檢查名字對不對。
import { $, $$, IDENTITY, QUERY_HELP, S, api, apiNsfw, catName, closeModal, debounce, esc, modal, svg, toast } from "./core.js";
import { cycleNsfw, go, openJobs, openShortcuts, toggleXray } from "./shell.js";
import { runSearch } from "./grid.js";
import { namesModal } from "./shelf.js";
import { undo } from "./actions.js";
import { sweepModal } from "./sweep.js";
import { datasetModal, impliesModal } from "./dataset.js";
import { artistScanModal, ascii2dModal, auditModal, findModal, importModal, openBackup, openDiag, openDriveBackup, openPhone, upgradeModal } from "./pages.js";
import { openSettings, setZhMode } from "./settings.js";
/* ============ 指令面板（Ctrl+K / /）：搜尋圖片、前往角色/標籤、切換頁面、執行指令 ============ */
const PAGES = [["home", "收件匣（首頁、整理中心）"], ["system", "系統（自動流程、工具）"], ["review", "待確認"], ["unrecognized", "未識別"], ["all", "圖庫"], ["characters", "角色"], ["artists", "創作者"],
  ["videos", "影片"], ["duplicates", "重複"], ["tags", "作品與標籤"], ["nsfw", "NSFW"], ["excluded", "以前排除的"], ["errors", "讀取失敗"]];
const COMMANDS = [
  ["設定", "settings", () => openSettings()],
  ["撤銷上一個動作", "undo 撤銷 復原 ctrl z", () => undo()],
  ["快捷鍵與搜尋語法", "help 說明 快捷鍵 ? keys", () => openShortcuts()],
  ["切換 NSFW：隱藏 → 模糊 → 顯示", "nsfw h 隱藏 模糊", () => cycleNsfw()],
  ["切換繁體／简体（介面用語、角色作品譯名）", "简体 簡體 繁體 繁体 大陸 大陆 台灣 中文 chinese zh cn tw 譯名 用語", () => setZhMode(window.zhcn?.mode === "cn" ? "tw" : "cn")],
  ["透視：顯示格子上的所有資訊（按住 Alt）", "xray 透視 alt 角標", () => toggleXray()],
  ["診斷／系統狀態", "diag", () => openDiag()],
  ["背景工作（排程、排隊中、最近完成）", "jobs 排程 背景 佇列 queue", () => openJobs()],
  ["多人圖缺角色", "people 多人 雙人 缺人", () => go("unrecognized", "people")],
  ["中文名整理（沒中文、譯名不對的角色和作品）", "中文名 譯名 名字 改名 翻譯 name bangumi", () => namesModal()],
  ["ascii2d 半自動找出處（X／Pixiv 原創圖）", "ascii2d 出處 作者 畫師 x twitter 推特 找作者", () => ascii2dModal()],
  ["立即掃描資料夾", "scan", async () => { await api("/api/jobs", { kind: "scan" }); toast("開始掃描"); }],
  ["暫停背景工作", "pause", async () => { await api("/api/jobs", { kind: "pause" }); toast("已暫停"); }],
  ["繼續背景工作", "resume", async () => { await api("/api/jobs", { kind: "resume" }); toast("已繼續"); }],
  ["篩選 iCloud 裡的非動漫圖（照片、截圖、梗圖）", "noanime icloud 非動漫", () => runSearch("is:icloud is:noanime")],
  ["重新啟動程式", "restart 重開 重新啟動", async () => {
    try { await api("/api/system/restart", {}); } catch (e) { return toast(e.message); }
    toast("重新啟動中…");
    const t0 = Date.now(); let down = false;   // 先等舊的關掉、再等新的起來
    const wait = async () => {
      await new Promise(r => setTimeout(r, 1500));
      try { const r = await fetch("/api/status", { cache: "no-store" }); if (r.ok && (down || Date.now() - t0 > 15000)) return location.reload(); } catch { down = true; }
      if (Date.now() - t0 < 90000) wait(); else toast("程式還沒重新啟動，請手動開啟");
    };
    wait();
  }],
  ["從網站匯入（X、Pixiv、Danbooru、e621、Rule34）", "import 匯入 下載 twitter x pixiv danbooru e621 rule34 r34 網址", () => importModal()],
  ["找出處（像素指紋、圖上簽名、Rule34、以圖搜源）", "find source 出處 搜源 像素 簽名 rule34 r34 pixel signature", () => findModal()],
  ["從畫師反推出處（下載畫師作品縮圖在本機比對）", "artist scan 畫師 反推", () => artistScanModal()],
  ["畫師作品比對（Pixiv／X 全部作品跟圖庫比）", "sweep pixiv x twitter 畫師 作品 比對 全集", () => sweepModal()],
  ["換成高清版（出處有同一張、比較大的）", "upgrade 高清 大圖 換 解析度", () => upgradeModal()],
  ["匯出訓練集（圖片＋.txt 標註）", "dataset lora 訓練 匯出 export caption txt 資料集", () => datasetModal({ ids: S.view !== "review" && S.selected?.size ? [...S.selected] : null })],
  ["標籤蘊含（A 也代表 B）", "implies implication 蘊含 造型 本體 規則", () => impliesModal()],
  ["準確率抽查：創作者", "audit 抽查 準確", () => auditModal("artist")],
  ["準確率抽查：角色", "audit 抽查 準確 角色", () => auditModal("character")],
  ["手機／平板（看圖庫、上傳，QR code）", "phone 手機 平板 ipad iphone 上傳 區網 wifi", () => openPhone()],
  ["備份與還原", "backup", () => openBackup()],
  ["備份到外接硬碟", "backup drive hdd usb 外接 硬碟 備份", () => openDriveBackup()],
  ["匯出整理結果（JSON）", "export", () => { location.href = "/api/export"; }],
  ["切換 NSFW 顯示", "nsfw", () => cycleNsfw()],
  ["撤銷上一步", "undo", () => undo()],
  ["快捷鍵與搜尋語法", "help", () => $("#help-btn").click()],
];
const QUICK = ["rating:safe", "is:video", "is:multi", "type:chibi", "type:cosplay", "added:7d", "ratio:landscape", "w:>=1920", "is:noartist", "src:none", "is:icloud", "is:icloud is:noanime", "is:real", "is:screenshot", "date:2024"];

export function openPalette(initial = "") {
  const m = modal(`<div class="palette"><div class="pin"><span>${svg("search", 18)}</span><input id="pal-q" placeholder="搜尋圖片（標籤、中文、rating:safe、date:2024…）、前往角色、或輸入指令" autocomplete="off" value="${esc(initial)}"></div>
    <div class="pal-list" id="pal-list"></div>
    <div class="pal-foot"><span>↑↓ 選擇 · Enter 執行 · Tab 補完標籤 · Esc 關閉</span><a id="pal-help">搜尋語法</a></div></div>`, null, "palm");
  const inp = $("#pal-q", m), list = $("#pal-list", m);
  let rows = [], hl = 0, seq = 0, stale = false;
  const lastTok = () => inp.value.split(" ").pop().replace(/^-/, "");
  const build = async () => {
    const my = ++seq;
    const q = inp.value.trim(), last = lastTok();
    const out = [];
    const searchRow = q ? { kind: "search", label: `搜尋圖片「${q}」`, sub: "在圖庫裡找符合全部條件的圖", run: () => runSearch(q) } : null;
    let tags = [], whole = [];
    let multi = q.includes(" ");
    // 「blue archive」「hatsune miku」：整句剛好是一個名字 → 當一個名字（以前只查最後一個字 archive）
    if (multi && !/[:|*\-]/.test(q)) {
      try { whole = (await api(`/api/tags?${new URLSearchParams({ q, limit: 6 })}`)).filter(t => IDENTITY.includes(t.category) || t.category === "custom" || (t.n || 0) > 0); } catch { }
      if (my !== seq) return;
    }
    if (last && !/^[a-z]+:/.test(last) && !whole.length) {
      try { tags = await api(`/api/tags?${new URLSearchParams({ q: last, limit: 8 })}`); } catch { }
      if (my !== seq) return;
    }
    if (whole.length) { tags = whole; multi = false; }
    const lab = t => t.display && t.display !== t.name ? t.display : (t.zh || t.name);
    tags.forEach(t => out.push({
      kind: "tag", t, label: lab(t), sub: [catName(t.category), lab(t) !== t.name ? t.name : "", t.alias, t.copyright?.replace(/_/g, " ")].filter(Boolean).join(" · "), n: t.n,
      run: () => multi ? complete(t.name) : (IDENTITY.includes(t.category) || t.category === "custom" ? go("tag", t.name) : runSearch(t.name)),
      alt: multi ? "補完" : IDENTITY.includes(t.category) ? "打開" : "搜尋",
    }));
    // 單一個字、而且剛好對到角色/標籤 → 先列標籤（跳過去）；多個條件或語法 → 先列「搜尋圖片」
    if (searchRow) { if (!multi && tags.length && !/[:|*]/.test(q)) out.splice(Math.min(3, out.length), 0, searchRow); else out.unshift(searchRow); }
    const ql = q.toLowerCase();
    const cv = s => window.zhcn ? window.zhcn.conv(s) : s;   // 簡體模式：畫面上看到的是簡體，打簡體也要對得到
    if (!multi) {
      PAGES.filter(([k, l]) => !q || l.includes(q) || cv(l).includes(q) || k.includes(ql)).slice(0, q ? 3 : 12).forEach(([k, l]) => out.push({ kind: "page", label: l, sub: "頁面", run: () => { S.q = ""; go(k); } }));
      COMMANDS.filter(([l, k]) => q && (l.includes(q) || k.includes(ql) || cv(l).includes(q) || cv(k).includes(ql))).forEach(([l, , fn]) => out.push({ kind: "cmd", label: l, sub: "指令", run: fn }));
    }
    if (!q) {
      try {
        const sb = await api(`/api/sidebar?nsfw=${apiNsfw()}`);
        if (my !== seq) return;
        sb.saved.forEach(x => out.unshift({ kind: "saved", label: x.name, sub: x.query, run: () => runSearch(x.query) }));
      } catch { }
    }
    rows = out; hl = 0; stale = false; draw();
  };
  const icon = { search: "search", tag: "tags", page: "all", cmd: "settings", saved: "saved" };
  const draw = () => {
    list.innerHTML = rows.map((r, i) => `<div class="prow ${i === hl ? "hl" : ""}" data-i="${i}"><span class="pic">${svg(r.t && r.t.category === "character" ? "characters" : r.t && r.t.category === "artist" ? "artists" : icon[r.kind], 17)}</span>
      <span class="pl">${esc(r.label)}</span><span class="ps">${esc(r.sub || "")}</span>${r.n != null ? `<span class="pn">${r.n}</span>` : ""}${r.alt ? `<span class="pa">${r.alt}</span>` : ""}</div>`).join("")
      + (!inp.value.trim() ? `<div class="pquick">${[...(S.quick || []).map(q => "tag:" + q.name), ...QUICK].map(x => `<button data-quick="${esc(x)}">${esc(x)}</button>`).join("")}</div>` : "");
    const el = $(".prow.hl", list); if (el) el.scrollIntoView({ block: "nearest" });
  };
  const complete = name => {
    const parts = inp.value.split(" "); const neg = parts[parts.length - 1].startsWith("-");
    parts[parts.length - 1] = (neg ? "-" : "") + name; inp.value = parts.join(" ") + " "; inp.focus(); build();
  };
  const exec = i => { const r = rows[i]; if (!r) return; if (r.kind === "tag" && q2multi()) { r.run(); return; } closeModal(); r.run(); };
  const q2multi = () => inp.value.trim().includes(" ");
  const deb = debounce(build, 110);
  inp.addEventListener("input", () => { stale = true; deb(); });
  inp.addEventListener("keydown", e => {
    e.stopPropagation();
    if (e.key === "ArrowDown") { hl = Math.min(hl + 1, rows.length - 1); draw(); e.preventDefault(); }
    else if (e.key === "ArrowUp") { hl = Math.max(hl - 1, 0); draw(); e.preventDefault(); }
    else if (e.key === "Tab") { const t = rows.find((r, i) => i >= hl && r.kind === "tag") || rows.find(r => r.kind === "tag"); if (t) complete(t.t.name); e.preventDefault(); }
    else if (e.key === "Enter") {   // 打完字馬上按 Enter：先用新的字把清單算好再執行，不要跑到上一次的結果
      e.preventDefault();
      if (stale) build().then(() => exec(0)); else exec(hl);
    }
    else if (e.key === "Escape") closeModal();
  });
  list.addEventListener("mousemove", e => { const r = e.target.closest(".prow"); if (r && +r.dataset.i !== hl) { hl = +r.dataset.i; $$(".prow", list).forEach(x => x.classList.toggle("hl", +x.dataset.i === hl)); } });
  list.addEventListener("click", e => {
    const qb = e.target.closest("[data-quick]"); if (qb) { inp.value = qb.dataset.quick + " "; inp.focus(); build(); return; }
    const r = e.target.closest(".prow"); if (r) exec(+r.dataset.i);
  });
  $("#pal-help", m).onclick = () => { list.innerHTML = QUERY_HELP; };
  inp.focus(); inp.setSelectionRange(inp.value.length, inp.value.length);
  build();
}
$("#cmd-btn").onclick = () => openPalette();
