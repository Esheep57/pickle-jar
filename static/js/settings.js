// ES module（2026-10）：用到別的檔案的東西都要在這裡 import；瀏覽器會在載入時檢查名字對不對。
import { $, $$, ask, S, api, closeModal, esc, modal, store, svg, toast } from "./core.js";
import { applyTint, go, loadSidebar, openJobs, pollOnce } from "./shell.js";
import { render } from "./grid.js";
import { DAN_WHY, folderBrowser, importModal, openBackup, openDiag, openDriveBackup, openPhone } from "./pages.js";
import { drawSteps } from "./steps.js";
/* ============ 設定 ============
   左邊分類、右邊一次一類；上面可以搜尋（輸入「門檻」「帳號」就跳到那幾項）。
   每一項：名稱＋一句說明＋控制項；改過還沒存的會標出來，底部顯示「N 項未儲存」。 */
$("#settings-btn").onclick = () => openSettings();
// 設定是一頁（v4.2，跟「系統」一樣）：返回鍵回得去；以前是彈出視窗
export function renderSettings() {
  $("#crumb").innerHTML = `<b>設定</b>`;
  $("#head").innerHTML = `<div><h1>設定</h1></div>`;
  $("#toolbar").className = ""; $("#toolbar").innerHTML = ""; $("#foot-l").textContent = "";
  return openSettings(S.setSecWant || null, true);
}

const SET_SECTIONS = [
  ["folders", "folder", "資料夾與掃描"],
  ["steps", "target", "自動標記與搜源"],
  ["model", "tags", "AI 模型"],
  ["accounts", "user", "網站帳號"],
  ["import", "download", "從網站匯入"],
  ["display", "eye", "顯示"],
  ["data", "db", "資料與備份"],
  ["perf", "pulse", "效能"],
  ["jobs", "clock", "背景工作"],
];

export async function openSettings(section, asPage) {
  if (!asPage) { S.setSecWant = section || null; if (section) store.set("setSec", section); return go("settings"); }
  S.setSecWant = null;
  const s = await api("/api/settings"); S.settings = s;
  const note = t => t ? `<div class="snote">${t}</div>` : "";
  const row = (label, ctl, desc = "", extra = "") => `<div class="srow" ${extra}><div class="slabel"><b>${label}</b>${note(desc)}</div><div class="sctl">${ctl}</div></div>`;
  const chk = (k, label, desc) => row(label, `<label class="switch"><input type="checkbox" data-k="${k}" ${s[k] ? "checked" : ""}><i></i></label>`, desc);
  const num = (k, label, step, desc, unit = "") => row(label, `<span class="numw"><input type="number" data-k="${k}" step="${step}" value="${s[k]}">${unit ? `<em>${unit}</em>` : ""}</span>`, desc);
  const sel = (k, label, opts, desc) => row(label, `<select data-k="${k}">${opts.map(([v, l]) => `<option value="${v}" ${String(s[k]) === String(v) ? "selected" : ""}>${l}</option>`).join("")}</select>`, desc);
  const txt = (k, label, ph, desc, type = "text") => row(label, `<input type="${type}" data-k="${k}" value="${esc(s[k] || "")}" placeholder="${esc(ph)}" autocomplete="off">`, desc);
  const key = (k, label, testId, desc) => row(label, `<div class="keyw"><input type="password" data-k="${k}" value="${esc(s[k] || "")}" autocomplete="off"><button class="btn sm" id="${testId}" type="button">測試</button></div><div class="tout" id="${testId}-out"></div>`, desc);
  const roots = s.roots.map(r => r + (s._roots_ok[r] ? "" : "   ← 找不到")).join("\n");
  const danOk = s.danbooru_login && s.danbooru_api_key;
  const SEC = {
    folders: `
      ${row("圖庫資料夾", `<textarea data-k="roots" rows="4" placeholder="D:\\Pictures\\illust&#10;E:\\Videos">${esc(roots)}</textarea><div class="srowbtn"><button class="btn sm" id="s-browse">${svg("folder", 15)} 瀏覽加入…</button></div><div id="s-fsb"></div>`, "每行一個。找不到的資料夾會標「← 找不到」。")}
      ${txt("exclude", "略過的資料夾名", "例如：temp, .thumbnails", "用逗號分隔，名字完全一樣的資料夾整個不掃。")}
      ${chk("watch", "監看新檔案", "資料夾裡有新圖就馬上處理（標記 → 找出處）。")}
      ${num("scan_interval_h", "定期完整掃描", 1, "0 = 關閉。監看偶爾會漏（網路磁碟、休眠），定期掃描會補上；搬家／改名的檔案會用內容指紋接回原本的標記。", "小時")}
      ${chk("skip_live_photo", "略過 iPhone 原況照片的 .MOV", "同資料夾有同名照片時，不把影片那一半當成另一張。")}`,
    model: `
      ${sel("model_repo", "模型", ["SmilingWolf/wd-eva02-large-tagger-v3", "SmilingWolf/wd-vit-large-tagger-v3", "SmilingWolf/wd-swinv2-tagger-v3", "SmilingWolf/wd-vit-tagger-v3", "SmilingWolf/wd-convnext-tagger-v3"].map(r => [r, r.replace("SmilingWolf/", "") + (r.includes("eva02") ? "（最準，推薦）" : "")]), "換模型後要「重新標記全部」才會套用到舊圖。")}
      ${txt("hf_endpoint", "模型下載鏡像", "空白＝官方 huggingface.co", "下載很慢或連不上時可以填，例如 https://hf-mirror.com")}
      ${row("門檻、找到時怎麼辦", `<button class="btn sm" data-sec-go="steps">自動標記與搜源…</button>`, "WD、PixAI、你的圖庫的門檻和開關都在「自動標記與搜源」。")}
      ${row("視為 NSFW 的分級", ["sensitive", "questionable", "explicit"].map(r => `<label class="chkl"><input type="checkbox" data-nsfw="${r}" ${s.nsfw_ratings.includes(r) ? "checked" : ""}> ${r}</label>`).join(""), "模型判的分級落在這些就當 NSFW；每張圖也可以在大圖裡手動改。")}`,
    steps: `<div id="steps-root"><div class="snote">載入中…</div></div>`,
    accounts: `
      ${!danOk ? `<div class="danwarn no_auth"><div><b>沒有 Danbooru 帳號，以圖搜源會非常慢</b><div class="mute">${DAN_WHY}</div></div></div>` : ""}
      ${txt("danbooru_login", "Danbooru 帳號", "帳號名稱（不是 email）", `強烈建議。<a href="https://danbooru.donmai.us/users/new" target="_blank" rel="noopener">免費註冊</a> → <a href="https://danbooru.donmai.us/api_keys" target="_blank" rel="noopener">API Keys</a> → Add（權限全部不勾）`)}
      ${key("danbooru_api_key", "Danbooru API key", "s-dantest", "只存在你電腦的 data/library.db。")}
      ${txt("e621_login", "e621 帳號名稱", "登入用的名稱", "選填。填了以圖搜圖從每分鐘 1 張放寬到每 10 秒 6 張。")}
      ${key("e621_api_key", "e621 API key", "s-e6test", "e621 → 帳號設定 → Manage API Access 產生。")}
      ${txt("r34_user_id", "Rule34 user ID", "數字", "選填。rule34.xxx 登入 → My Account → Options → Generate New Key。")}
      ${key("r34_api_key", "Rule34 API key", "s-r34test", "原檔比對用（只送出檔案指紋）。")}
      ${row("Pixiv PHPSESSID", `<input type="password" data-k="pixiv_phpsessid" value="${esc(s.pixiv_phpsessid || "")}" autocomplete="off">`, "選填。Pixiv 登入 cookie，才能讀 R-18 作品資訊。")}
      <div class="snote" style="margin-top:8px">這些帳號資料只存在你電腦的 data/library.db，匯出時不會帶出去。</div>`,
    import: `
      ${txt("import_dir", "匯入的圖存到", "空白＝第一個圖庫資料夾\\匯入", "底下自動分 x、pixiv、danbooru…；不在圖庫資料夾裡會自動加進去。")}
      ${num("gdl_max_posts", "整個帳號只抓最新幾篇", 10, "gallery-dl 匯入（Bluesky、DeviantArt…）貼的是整個帳號、標籤、收藏的網址時。0＝全部。再匯入同一個帳號只會抓新的。", "篇")}
      ${row("瀏覽器按鈕", `<button class="btn sm" id="s-import">安裝／說明…</button>`, "Tampermonkey 腳本：在 X、Pixiv、Danbooru、e621、Rule34 上直接按「⤓ 存到圖庫」。")}`,
    display: `
      ${row("中文", `<span class="seg" id="zh-seg"><button data-z="tw" class="${s.zh_mode === "cn" ? "" : "on"}">繁體</button><button data-z="cn" class="${s.zh_mode === "cn" ? "on" : ""}">简体</button></span>`, "简体：介面用大陸用語（文件夹、视频、默认），角色和作品用大陸常用譯名（Bangumi、維基百科大陆简体、寶可夢官方簡中）。你自己取的名字只轉成簡體字。切換後重新載入頁面。")}
      ${txt("quick_tags", "快速標籤", "梗圖, 真人, 截圖", "你最常用的分類，用逗號分隔（最多 9 個，依序對應數字鍵 1–9）。按數字鍵加上／拿掉；選取列、大圖「詳細」裡也有按鈕。")}
      ${txt("no_artist_tags", "不用找作者的標籤", "梗圖, 表情包, 截圖", "有這些標籤的圖不算「缺創作者」（表情包、梗圖這種轉了好幾手、找不到作者的）。標籤拿掉就會回來。")}
      ${txt("no_char_tags", "不認角色的標籤", "梗圖, 截圖", "有這些標籤的圖不再認角色、不再把候選放進待確認（AI、搜源、你的圖庫都一樣），也不自動上傳以圖搜圖。加上標籤時，還沒決定的候選會拿掉（可以撤銷）；已經確認的角色不動。")}
      ${txt("quick_tags_skip", "加上後離開未識別", "梗圖, 真人, 截圖", "這些快速標籤加上後，圖會離開「未識別」（不用再認角色）。不會刪掉 AI 的角色候選。")}
      ${row("底色", `<span class="seg" id="tint-seg"><button data-t="0" class="${store.get("tint", true) ? "" : "on"}">白（簡潔）</button><button data-t="1" class="${store.get("tint", true) ? "on" : ""}">淡綠</button></span>`, "只影響這個瀏覽器，馬上套用。")}
      ${row("專注退場", `<span class="seg" id="zen-seg"><button data-z="1" class="${store.get("zen", true) !== false ? "on" : ""}">開</button><button data-z="0" class="${store.get("zen", true) === false ? "on" : ""}">關</button></span>`, "連續按鍵、滑鼠沒動時，側欄、標題、工具列淡出；動一下滑鼠就回來。只影響這個瀏覽器。")}
      ${sel("grid_mode", "格狀排列", [["justified", "依原比例（像相簿）"], ["square", "正方形"]], "圖庫右上也可以隨時切換。")}
      ${sel("general_lang", "一般描述標籤", [["zh", "中文對照（常見的）"], ["raw", "原始英文"]])}
      ${sel("display_lang", "角色／作品名稱", [["zh", "中文（繁體／简体照上面的「中文」）"], ["ja", "日文"], ["raw", "原始 tag"]], "你自己取的名字永遠優先。")}`,
    data: `
      ${num("backup_keep", "自動備份保留", 1, "每天自動備份資料庫一次。", "份")}
      ${txt("backup_mirror_dir", "備份另存一份到", "例如 D:\\備份 或 OneDrive 裡的資料夾", "每天的備份再複製一份到另一顆硬碟或雲端資料夾（底下會建 pickle-jar-backups）。只放備份檔，程式本身不要搬進同步資料夾。")}
      ${row("備份到外接硬碟", `<button class="btn sm" id="s-drive">備份到外接硬碟…</button>`, "圖片、程式、資料庫整份複製到另一顆硬碟；插上按一次，只複製新的。")}
      ${row("備份與還原", `<button class="btn sm" id="s-backup">備份與還原／匯出匯入…</button>`)}
      ${row("系統狀態", `<button class="btn sm" id="s-diag">診斷…</button>`, "模型、GPU、網路、準確率抽查。")}
      ${row("手機／平板", `<button class="btn sm" id="s-phone">設定…</button>`, "同一個 Wi-Fi 下用手機、平板看圖庫、把圖傳進來（要 PIN）。")}`,
    perf: `
      ${num("batch_size", "GPU 批次大小", 1, "16GB 顯卡用 eva02-large 建議 16–32；顯示卡記憶體不夠時調低。")}
      ${num("workers", "解碼執行緒", 1, "讀圖、縮圖的平行數量。")}
      ${num("video_max_frames", "每支影片最多抽幀", 1, "影片取幾張畫面來標記。")}`,
    jobs: `
      ${row("背景工作", `<button class="btn sm" id="s-jobs">打開背景工作面板…</button>`, "正在做什麼、排隊中、排程（定期掃描、備份、重搜）、最近完成。")}
      ${row("立即掃描資料夾", `<button class="btn sm" id="j-scan">掃描</button>`)}
      ${row("暫停／繼續本機工作", `<button class="btn sm" id="j-pause">暫停／繼續</button><button class="btn sm" id="j-stop">停止</button>`)}
      ${row("補跑舊圖", `<button class="btn sm" id="j-rerun">補跑…</button>`, "換模型、改門檻或開了新的步驟後，對舊圖重跑某幾步（可以選範圍）。你確認、否決、手動加的標籤都不會被改。")}`,
  };
  const OLD = { online: "steps", review: "steps", lab: "steps" };      // b69：這三類併進「自動標記與搜源」
  let cur = section || store.get("setSec", "folders"); cur = OLD[cur] || cur;
  if (!SET_SECTIONS.some(([k]) => k === cur)) cur = "folders";
  $("#content").innerHTML = `<div class="modal settings setpage"><div class="setwrap"><div class="setnav"><div class="setsearch"><input id="s-q" placeholder="搜尋設定…（例如：門檻、帳號）" autocomplete="off"></div>
      ${SET_SECTIONS.map(([k, ic, l]) => `<button data-sec="${k}" class="${k === cur ? "on" : ""}"><span>${svg(ic, 16)}</span>${l}<i data-dirty="${k}"></i></button>`).join("")}</div>
    <div class="setbody">${SET_SECTIONS.map(([k, ic, l]) => `<section data-sec="${k}" class="${k === cur ? "on" : ""}"><h3>${l}</h3>${SEC[k]}</section>`).join("")}
      <div class="setnone mute hidden">沒有符合的設定</div></div></div>
    <div class="mfoot"><span class="mute" id="s-dirty" style="margin-right:auto"></span><button class="btn primary" id="s-save">完成</button></div></div>`;
  const m = $("#content .setpage");
  $$("[data-go-albums]", m).forEach(b => b.addEventListener("click", () => go("albums")));   // 以前是 onclick="go(...)"：模組化後沒有全域函式
  // 分類切換、搜尋
  const show = k => { store.set("setSec", k); $$(".setnav [data-sec]", m).forEach(b => b.classList.toggle("on", b.dataset.sec === k)); $$(".setbody section", m).forEach(x => x.classList.toggle("on", x.dataset.sec === k)); $(".setbody", m).scrollTop = 0; };
  $$(".setnav [data-sec]", m).forEach(b => b.onclick = () => { $("#s-q", m).value = ""; filter(""); show(b.dataset.sec); });
  const filter = q => {
    q = q.trim().toLowerCase();
    m.classList.toggle("searching", !!q);
    let any = false;
    $$(".setbody section", m).forEach(sec => {
      let hit = 0;
      $$(".srow, .stp-row", sec).forEach(r => { const ok = !q || r.textContent.toLowerCase().includes(q); r.classList.toggle("hidden", !ok); hit += ok; });
      sec.classList.toggle("hit", !!q && hit > 0); any ||= hit > 0;
    });
    $(".setnone", m).classList.toggle("hidden", !q || any);
  };
  $("#s-q", m).oninput = e => filter(e.target.value);
  // 未儲存的變更
  const orig = new Map();
  const val = el => el.type === "checkbox" ? el.checked : el.value;
  $$("[data-k], [data-nsfw]", m).forEach(el => orig.set(el, val(el)));
  const markDirty = () => {
    const dirty = new Set(); let n = 0;
    orig.forEach((v, el) => { const d = val(el) !== v; el.closest(".srow")?.classList.toggle("dirty", d); if (d) { n++; dirty.add(el.closest("section")?.dataset.sec); } });
    $$("[data-dirty]", m).forEach(i => i.classList.toggle("on", dirty.has(i.dataset.dirty)));
    $("#s-dirty", m).textContent = n ? `${n} 項儲存中…` : "改了會自動儲存";
  };
  m.addEventListener("input", markDirty); m.addEventListener("change", markDirty);
  // 改了就存（跟「底色」「專注退場」一樣馬上生效；以前要按儲存，有的又不用，很亂）
  let autoT; m.addEventListener("change", e => { if (!e.target.closest("[data-k], [data-nsfw]")) return; clearTimeout(autoT); autoT = setTimeout(() => saveAll(false), 400); });
  // 按鈕
  $$("#zen-seg button", m).forEach(b => b.onclick = () => { store.set("zen", b.dataset.z === "1"); $$("#zen-seg button", m).forEach(x => x.classList.toggle("on", x === b)); });
  $$("#zh-seg button", m).forEach(b => b.onclick = () => setZhMode(b.dataset.z));
  $$("#tint-seg button", m).forEach(b => b.onclick = () => { store.set("tint", b.dataset.t === "1"); applyTint(); $$("#tint-seg button", m).forEach(x => x.classList.toggle("on", x === b)); });
  const job = (kind, msg) => async () => { await api("/api/jobs", { kind }); toast(msg); };
  $("#j-scan", m).onclick = job("scan", "開始掃描");
  $("#j-pause", m).onclick = async () => { const st = await api("/api/status"); await api("/api/jobs", { kind: st.paused ? "resume" : "pause" }); toast(st.paused ? "已繼續" : "已暫停"); pollOnce(); };
  $("#j-stop", m).onclick = job("stop", "已要求停止");
  $("#j-rerun", m).onclick = () => import("./steps.js").then(x => x.openRerun({ steps: ["wd"] }));
  $$("[data-sec-go]", m).forEach(b => b.onclick = () => show(b.dataset.secGo));
  drawSteps($("#steps-root", m));
  $("#s-jobs", m).onclick = () => openJobs();
  $("#s-backup", m).onclick = openBackup;
  $("#s-drive", m).onclick = openDriveBackup;
  $("#s-diag", m).onclick = openDiag;
  $("#s-phone", m).onclick = openPhone;
  $("#s-import", m).onclick = () => importModal();
  const tester = (id, save, url, okKey = "ok") => $("#" + id, m).onclick = async () => {
    const out = $(`#${id}-out`, m); out.textContent = "測試中…";
    await api("/api/settings", save());
    try { const r = await api(url, {}); out.innerHTML = `<b class="${r[okKey] && (okKey !== "ok" || r.iqdb !== false) ? "okc" : "bad"}">${esc(r.msg)}</b>`; pollOnce(); }
    catch (e) { out.innerHTML = `<b class="bad">${esc(e.message)}</b>`; }
  };
  const v = k => $(`[data-k=${k}]`, m).value.trim();
  tester("s-dantest", () => ({ danbooru_login: v("danbooru_login"), danbooru_api_key: v("danbooru_api_key") }), "/api/online/test-danbooru");
  tester("s-e6test", () => ({ e621_login: v("e621_login"), e621_api_key: v("e621_api_key") }), "/api/online/test-e621");
  tester("s-r34test", () => ({ r34_user_id: v("r34_user_id"), r34_api_key: v("r34_api_key") }), "/api/online/test-r34");
  $("#s-browse", m).onclick = () => folderBrowser($("#s-fsb", m), p => {
    const ta = $("[data-k=roots]", m); const lines = ta.value.split("\n").map(x => x.trim()).filter(Boolean);
    if (!lines.includes(p)) lines.push(p); ta.value = lines.join("\n"); markDirty(); saveAll(false); toast("已加入");
  });
  $("#s-save", m).onclick = () => saveAll(true);
  async function saveAll(close) {
    const patch = {};
    $$("[data-k]", m).forEach(el => {
      const k = el.dataset.k;
      if (el.type === "checkbox") patch[k] = el.checked;
      else if (el.type === "number") patch[k] = +el.value;
      else if (el.tagName === "DIV") return;
      else if (k === "roots") patch[k] = el.value.split("\n").map(x => x.replace(/\s+← 找不到$/, "").trim()).filter(Boolean);
      else if (["exclude", "quick_tags", "quick_tags_skip", "no_artist_tags", "no_char_tags"].includes(k)) patch[k] = el.value.split(/[,，、]/).map(x => x.trim()).filter(Boolean);
      else patch[k] = el.value;
    });
    patch.nsfw_ratings = $$("[data-nsfw]", m).filter(x => x.checked).map(x => x.dataset.nsfw);
    const retagNeeded = ["model_repo"].some(k => patch[k] !== undefined && String(patch[k]) !== String(s[k]));
    try {
      const r = await api("/api/settings", patch);
      const bad = Object.entries(r._roots_ok).filter(([, ok]) => !ok).map(([p]) => p);
      if (bad.length) toast(`找不到資料夾：${bad.join(", ")}`);
      if (retagNeeded) toast("換了模型：要套用到舊圖，到「自動標記與搜源」按「補跑舊圖」");
      Object.assign(s, r); S.settings = r; orig.forEach((v, el) => orig.set(el, val(el))); markDirty();
      if (close) { loadSidebar(); if (history.length > 1) history.back(); else go("all"); }
    } catch (e) { toast("儲存失敗：" + e.message); }
  }
}

// 繁體／简体（介面用語＋角色作品譯名）：存到伺服器設定，重新載入（已經轉過的字沒辦法轉回去）
export async function setZhMode(mode) {
  if ((S.settings?.zh_mode || "tw") === mode && window.zhcn?.mode === mode) return;
  try { S.settings = await api("/api/settings", { zh_mode: mode }); } catch (e) { return toast(e.message); }
  if (!window.zhcn?.sync(mode)) location.reload();
}
