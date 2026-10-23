// 影片轉 GIF（app/togif.py）：大圖看影片時右上角的底片按鈕。
// 選一段（拖時間軸兩端，或播到想要的地方按 I／O）→ 每秒格數、寬度、顏色 → 轉成 GIF（Enter）→ 看結果 → 存進圖庫（Enter）／複製分享／下載
// 「做好後」可以設成直接複製／直接存（記住），Enter 一次就好。
// 快速轉（quickGif）：Shift＋點底片按鈕或 Ctrl+Shift+C → 不開視窗，用上次的設定把整部影片（≤ 2 分鐘）轉好直接複製
import { $, $$, api, esc, fmtSize, follow, modal, svg, toast } from "./core.js";
import { markDirty } from "./viewer.js";

const FPS = [10, 15, 20, 24];
const WIDTHS = [320, 480, 640];
const PREF_KEY = "gifPrefs";
const DEF = { fps: 15, width: 480, dither: true, after: "share" };
const AFTER = [["", "先看看"], ["share", "直接複製"], ["save", "直接存"]];
const loadPrefs = () => { try { return { ...DEF, ...JSON.parse(localStorage.getItem(PREF_KEY) || "{}") }; } catch { return { ...DEF }; } };
const savePrefs = p => { try { localStorage.setItem(PREF_KEY, JSON.stringify(p)); } catch { } };
const t1 = s => (Math.round(s * 10) / 10).toFixed(1);
const MAXS = 120;           // app/togif.py MAX_SECONDS
const BIG = 20e6;           // 格數 × 寬 × 高超過這個，GIF 大概會超過 10 MB（實測真的影片約 0.5 byte／像素；抖色、雜訊多的更大）
const WHOLE = 20;           // 20 秒以下的影片：打開就選整部
const DEFLEN = 10;          // 比較長的：從現在播到的地方取 10 秒
const mb = n => fmtSize(n);
const doneMsg = j => j.after_error ? `GIF 做好了（${mb(j.size)}），但${j.after === "share" ? "複製" : "存"}失敗：${j.after_error}`
  : j.after === "share" ? `已複製 GIF（${mb(j.size)}）：到 QQ 或 Messenger 的聊天視窗按 Ctrl+V${j.size > 10e6 ? "（超過 10 MB，有些聊天軟體會傳不了）" : ""}`
  : j.after === "save" ? `已存進圖庫：${j.saved?.name || ""}` : "";

// 快速轉：不開視窗。整部影片超過 2 分鐘 → 打開視窗讓你選一段
let _quick = null;
export async function quickGif(id, d) {
  const dur = d.duration || 0;
  if (!dur || dur > MAXS) { toast(dur ? `影片超過 ${MAXS / 60} 分鐘：選一段再轉` : "讀不到影片長度：選一段再轉"); return gifModal(id, d, 0); }
  if (_quick) { toast("上一個 GIF 還在轉"); return; }
  const p = loadPrefs();
  const W = d.width || 0, H = d.height || 0, w = W ? Math.min(p.width, W) : p.width;
  if (dur * p.fps * w * (W && H ? w * H / W : w * 9 / 16) > BIG) {   // 整部轉出來會很大（幾十 MB，QQ 傳不了）：打開視窗選一段
    toast("整部轉成 GIF 會太大：選一段，或降低每秒格數、寬度"); return gifModal(id, d, 0);
  }
  let j;
  try { j = await api("/api/gif/make", { id, start: 0, end: dur, fps: p.fps, width: p.width, dither: p.dither, after: "share" }); }
  catch (e) { toast(`轉不成 GIF：${e.message}`); return; }
  _quick = j.id;
  toast(`轉 GIF 中（${t1(dur)} 秒，${j.frames} 格）：好了會自動複製`);
  const jid = j.id;
  follow(() => api(`/api/gif/job/${jid}`), x => {
    if (x.status === "done") { _quick = null; toast(doneMsg(x)); markDirty(); return false; }
    if (x.status === "error" || x.status === "cancelled") { _quick = null; toast(`轉不成 GIF：${x.error || x.phase}`); return false; }
  }, { every: 400 });
}

// d：/api/media/{id} 的資料；t：大圖影片目前播到哪（秒）
export function gifModal(id, d, t = 0) {
  const dur = d.duration || 0, W = d.width || 0, H = d.height || 0;
  const pref = loadPrefs();
  const st = {
    start: dur <= WHOLE ? 0 : Math.max(0, Math.min(t, dur - 1)),
    end: 0, fps: pref.fps, width: pref.width, dither: pref.dither, after: pref.after || "", job: null, poll: null,
  };
  st.end = dur <= WHOLE ? dur : Math.min(dur, st.start + DEFLEN);
  const widths = [...WIDTHS.filter(w => !W || w < W), W && W <= 1280 ? W : 0].filter(Boolean);
  if (W && W > 1280) widths.push(1280);
  if (!widths.includes(st.width)) st.width = widths.reduce((a, b) => Math.abs(b - st.width) < Math.abs(a - st.width) ? b : a, widths[0]);

  const m = modal(`<h3>轉成 GIF</h3><div class="mbody gfbody">
    <div class="gfstage"><video class="gfvid" src="/file/${id}" muted playsinline preload="auto"></video><img class="gfout hidden" alt=""></div>
    <div class="gfedit">
      <div class="gftrack" title="點一下跳到那裡；拖兩端調整開始、結束"><div class="gfsel"><span class="gfh" data-h="s"></span><span class="gfh" data-h="e"></span></div><i class="gfph"></i></div>
      <div class="gftimes">
        <label>開始 <input type="number" id="gf-s" min="0" step="0.1"></label><button class="lnkb" id="gf-si" title="把開始設在現在播到的地方">現在 <kbd>I</kbd></button>
        <span class="gflen" id="gf-len"></span>
        <label>結束 <input type="number" id="gf-e" min="0" step="0.1"></label><button class="lnkb" id="gf-eo" title="把結束設在現在播到的地方">現在 <kbd>O</kbd></button>
        <button class="lnkb" id="gf-play" title="播放這一段（Space）">${svg("play", 14)} 播這段</button>
      </div>
      <div class="gfopts">
        <div><span class="flbl">每秒</span><span class="seg sm" id="gf-fps">${FPS.map(f => `<button data-v="${f}">${f}</button>`).join("")}</span></div>
        <div><span class="flbl">寬度</span><span class="seg sm" id="gf-w">${widths.map(w => `<button data-v="${w}">${w === W ? `原尺寸 ${w}` : w}</button>`).join("")}</span></div>
        <div><span class="flbl">顏色</span><span class="seg sm" id="gf-d"><button data-v="1" title="抖色：漸層、照片比較平順">平滑</button><button data-v="0" title="不抖色：動畫風的大色塊比較乾淨、檔案也比較小">乾淨</button></span></div>
        <div><span class="flbl">做好後</span><span class="seg sm" id="gf-a">${AFTER.map(([v, l]) => `<button data-v="${v}">${l}</button>`).join("")}</span></div>
      </div>
      <div class="gfinfo" id="gf-info"></div>
    </div>
    <div class="gfres hidden" id="gf-res"></div>
  </div>
  <div class="mfoot" id="gf-foot"></div>`, null, "wide gifm");

  const vid = $(".gfvid", m), track = $(".gftrack", m), sel = $(".gfsel", m), ph = $(".gfph", m);
  const inS = $("#gf-s", m), inE = $("#gf-e", m);
  const total = () => dur || vid.duration || 0;
  let playing = false;

  function draw() {
    const T = total() || 1;
    sel.style.left = (st.start / T * 100) + "%";
    sel.style.width = (Math.max(0, st.end - st.start) / T * 100) + "%";
    if (document.activeElement !== inS) inS.value = t1(st.start);
    if (document.activeElement !== inE) inE.value = t1(st.end);
    inS.max = inE.max = t1(T);
    const len = st.end - st.start, n = Math.round(len * st.fps);
    const h = W && H ? Math.round(st.width * H / W) : 0;
    $("#gf-len", m).textContent = `${t1(len)} 秒`;
    const px = n * st.width * (h || st.width * 9 / 16);   // 跟 quickGif 用同一個門檻（BIG）
    const warn = len > MAXS ? `一次最多 ${MAXS} 秒` : len < 0.1 ? "這一段太短" : px > BIG ? "會很大（可能超過 10 MB）：降低每秒格數、寬度，或縮短一段" : "";
    $("#gf-info", m).innerHTML = `${n} 格 · ${st.width}${h ? "×" + h : ""}${warn ? ` · <span class="gfwarn">${esc(warn)}</span>` : ""}`;
    $$("#gf-fps button", m).forEach(b => b.classList.toggle("on", +b.dataset.v === st.fps));
    $$("#gf-w button", m).forEach(b => b.classList.toggle("on", +b.dataset.v === st.width));
    $$("#gf-d button", m).forEach(b => b.classList.toggle("on", (b.dataset.v === "1") === st.dither));
    $$("#gf-a button", m).forEach(b => b.classList.toggle("on", b.dataset.v === st.after));
    const gl = $("#gf-go", m); if (gl) gl.innerHTML = `${st.after === "share" ? "轉好直接複製" : st.after === "save" ? "轉好直接存" : "轉成 GIF"} <kbd>Enter</kbd>`;
    const go = $("#gf-go", m); if (go) go.disabled = !!warn && !warn.startsWith("會很大");
  }
  const clamp = () => {
    const T = total();
    st.start = Math.max(0, Math.min(st.start, T ? T - 0.1 : st.start));
    if (T) st.end = Math.min(st.end, T);
    if (st.end < st.start + 0.1) st.end = Math.min(T || st.start + 0.1, st.start + 0.1);
  };
  const seek = s => { try { vid.currentTime = Math.max(0, s); } catch { } };
  const setStart = s => { st.start = s; if (st.end - st.start > MAXS) st.end = st.start + MAXS; clamp(); seek(st.start); draw(); };
  const setEnd = s => { st.end = s; if (st.end - st.start > MAXS) st.start = st.end - MAXS; clamp(); seek(Math.max(st.start, st.end - 0.05)); draw(); };

  vid.addEventListener("loadedmetadata", () => { if (!dur) { st.end = Math.min(vid.duration, st.start + 4); } clamp(); draw(); seek(st.start); });
  vid.addEventListener("error", () => { $(".gfstage", m).insertAdjacentHTML("beforeend", `<div class="gfnoplay">瀏覽器播不了這個影片（常見是 iPhone 的 HEVC）：直接輸入秒數也可以轉</div>`); });
  vid.addEventListener("timeupdate", () => {
    ph.style.left = (vid.currentTime / (total() || 1) * 100) + "%";
    if (playing && vid.currentTime >= st.end) seek(st.start);     // 播這段：在選的範圍裡循環
  });
  const togglePlay = () => {
    if (playing) { vid.pause(); playing = false; }
    else { if (vid.currentTime < st.start || vid.currentTime >= st.end) seek(st.start); vid.play().catch(() => { }); playing = true; }
    $("#gf-play", m).innerHTML = `${svg(playing ? "pause" : "play", 14)} ${playing ? "暫停" : "播這段"}`;
  };
  $("#gf-play", m).onclick = togglePlay;
  $("#gf-si", m).onclick = () => setStart(vid.currentTime || 0);
  $("#gf-eo", m).onclick = () => setEnd(vid.currentTime || 0);
  inS.onchange = () => setStart(+inS.value || 0);
  inE.onchange = () => setEnd(+inE.value || 0);
  $$("#gf-fps button", m).forEach(b => b.onclick = () => { st.fps = +b.dataset.v; draw(); });
  $$("#gf-w button", m).forEach(b => b.onclick = () => { st.width = +b.dataset.v; draw(); });
  $$("#gf-d button", m).forEach(b => b.onclick = () => { st.dither = b.dataset.v === "1"; draw(); });
  $$("#gf-a button", m).forEach(b => b.onclick = () => { st.after = b.dataset.v; savePrefs({ fps: st.fps, width: st.width, dither: st.dither, after: st.after }); draw(); });

  // 時間軸：點一下跳過去；拖兩端（或拖整段）調範圍
  track.addEventListener("pointerdown", e => {
    const r = track.getBoundingClientRect(), T = total();
    if (!T) return;
    const at = x => Math.max(0, Math.min(T, (x - r.left) / r.width * T));
    const h = e.target.closest(".gfh")?.dataset.h || (e.target === sel ? "move" : null);
    if (!h) { seek(at(e.clientX)); return; }
    e.preventDefault(); track.setPointerCapture(e.pointerId);
    const x0 = at(e.clientX), s0 = st.start, e0 = st.end;
    const mv = ev => {
      const x = at(ev.clientX);
      if (h === "s") setStart(Math.min(x, st.end - 0.1));
      else if (h === "e") setEnd(Math.max(x, st.start + 0.1));
      else { const dx = Math.max(-s0, Math.min(T - e0, x - x0)); st.start = s0 + dx; st.end = e0 + dx; seek(st.start); draw(); }
    };
    const up = () => { track.removeEventListener("pointermove", mv); track.removeEventListener("pointerup", up); track.removeEventListener("pointercancel", up); };
    track.addEventListener("pointermove", mv); track.addEventListener("pointerup", up); track.addEventListener("pointercancel", up);
  });

  function editFoot() {
    $("#gf-foot", m).innerHTML = `<button class="btn" data-close>取消</button><button class="btn primary" id="gf-go">轉成 GIF <kbd>Enter</kbd></button>`;
    $("#gf-go", m).onclick = make;
    draw();
  }
  function showEdit() {
    stopPoll();
    $(".gfedit", m).classList.remove("hidden"); vid.classList.remove("hidden"); m.classList.remove("gfresult");
    $(".gfout", m).classList.add("hidden"); $(".gfout", m).removeAttribute("src");
    $("#gf-res", m).classList.add("hidden");
    editFoot();
    m.focus();
  }
  const stopPoll = () => { clearTimeout(st.poll); st.poll = null; };

  async function make() {
    if (st.job && st.job.status === "running") return;
    savePrefs({ fps: st.fps, width: st.width, dither: st.dither, after: st.after });
    vid.pause(); playing = false;
    let j;
    try { j = await api("/api/gif/make", { id, start: st.start, end: st.end, fps: st.fps, width: st.width, dither: st.dither, after: st.after || null }); }
    catch (e) { toast(e.message); return; }
    st.job = j;
    $(".gfedit", m).classList.add("hidden");
    m.focus();
    const res = $("#gf-res", m); res.classList.remove("hidden");
    $("#gf-foot", m).innerHTML = `<button class="btn" id="gf-stop">停止</button>`;
    $("#gf-stop", m).onclick = async () => { await api(`/api/gif/job/${j.id}/cancel`, {}).catch(() => { }); showEdit(); };
    const jid = j.id;
    st.stopFollow?.();
    st.stopFollow = follow(() => api(`/api/gif/job/${jid}`), x => {
      j = st.job = x;
      if (x.status === "done") { done(x); return false; }
      if (x.status === "error" || x.status === "cancelled") { toast(`轉不成 GIF：${x.error || x.phase}`); showEdit(); return false; }
      res.innerHTML = `<div class="gfprog"><b>${esc(x.phase)}</b><span>${x.done} / ${x.total}</span><i style="--p:${x.total ? x.done / x.total : 0}"></i></div>`;
    }, { every: 300, alive: () => m.isConnected });
  }
  function done(j) {
    vid.classList.add("hidden"); m.classList.add("gfresult");
    const out = $(".gfout", m); out.src = `/api/gif/job/${j.id}/file?t=${Date.now()}`; out.classList.remove("hidden");
    const big = j.size > 10e6;
    $("#gf-res", m).innerHTML = `<div class="gfdone"><b>${fmtSize(j.size)}</b> · ${j.frames} 格 · ${j.width}×${j.height} · ${t1(j.end - j.start)} 秒${big ? ` · <span class="gfwarn">超過 10 MB，有些聊天軟體會傳不了</span>` : ""}</div><div class="gfsaved" id="gf-saved"></div>`;
    $("#gf-foot", m).innerHTML = `<button class="btn" id="gf-back">重新調整</button>
      <a class="btn" href="/api/gif/job/${j.id}/file" download title="下載這個 GIF">${svg("download", 15)} 下載</a>
      <button class="btn" id="gf-share" title="複製到剪貼簿：到 QQ、Messenger 的聊天視窗按 Ctrl+V">${svg("share", 15)} 複製分享</button>
      <button class="btn primary" id="gf-save" title="放在影片旁邊，帶上影片已確認的角色、創作者、作品">存進圖庫 <kbd>Enter</kbd></button>`;
    $("#gf-back", m).onclick = showEdit;
    m.focus();     // 剛剛有焦點的按鈕藏起來了：焦點拉回視窗，Enter 才會到這裡
    $("#gf-share", m).onclick = async () => {
      try {
        const r = await api(`/api/gif/job/${j.id}/share`, {});
        toast(r.ok ? "已複製 GIF：到 QQ 或 Messenger 的聊天視窗按 Ctrl+V" : "這台電腦不是 Windows：請用「下載」");
      } catch (e) { toast(`複製失敗：${e.message}`); }
    };
    $("#gf-save", m).onclick = async () => {
      const b = $("#gf-save", m); if (b.disabled) return;
      b.disabled = true;
      try {
        const r = await api(`/api/gif/job/${j.id}/save`, {});
        $("#gf-saved", m).innerHTML = `已存進圖庫：<b>${esc(r.name)}</b>${r.tags ? `（帶上 ${r.tags} 個標籤）` : ""} <button class="lnkb" id="gf-reveal">在資料夾中顯示</button>`;
        $("#gf-reveal", m).onclick = () => api(`/api/media/${r.id}/reveal`, {}).catch(e => toast(e.message));
        b.textContent = "已存進圖庫";
        markDirty();
      } catch (e) { toast(e.message); b.disabled = false; }
    };
    if (j.after) {        // 做好後已經自動複製／存了：主按鈕變「完成」，Enter 關掉
      const msg = doneMsg(j);
      $("#gf-saved", m).innerHTML = `${j.after_error ? `<span class="gfwarn">${esc(msg)}</span>` : esc(msg)}${j.saved ? ` <button class="lnkb" id="gf-reveal">在資料夾中顯示</button>` : ""}`;
      if (j.saved) { $("#gf-reveal", m).onclick = () => api(`/api/media/${j.saved.id}/reveal`, {}).catch(e => toast(e.message)); markDirty(); }
      if (!j.after_error) toast(msg);
      const sv = $("#gf-save", m);
      sv.classList.remove("primary"); sv.innerHTML = j.saved ? "已存進圖庫" : "存進圖庫";
      if (j.saved) sv.disabled = true;
      $("#gf-foot", m).insertAdjacentHTML("beforeend", `<button class="btn primary" id="gf-fin" data-close>完成 <kbd>Enter</kbd></button>`);
    }
  }

  m.addEventListener("keydown", e => {
    const typing = e.target.tagName === "INPUT" && e.target.type === "number";
    if (e.key === "Enter") {
      if (typing) { e.target.dispatchEvent(new Event("change")); }
      e.preventDefault();
      const b = $("#gf-fin", m) || $("#gf-save", m) || $("#gf-go", m);
      if (b && !b.disabled) b.click();
      return;
    }
    if (typing || $(".gfedit", m).classList.contains("hidden")) return;
    if (e.key === "i" || e.key === "I") { e.preventDefault(); $("#gf-si", m).click(); }
    else if (e.key === "o" || e.key === "O") { e.preventDefault(); $("#gf-eo", m).click(); }
    else if (e.key === " ") { e.preventDefault(); togglePlay(); }
    else if (e.key === "ArrowLeft" || e.key === "ArrowRight") { e.preventDefault(); vid.pause(); playing = false; seek(vid.currentTime + (e.key === "ArrowLeft" ? -1 : 1) / (e.shiftKey ? 1 : 30)); }
  });
  m.tabIndex = -1; m.focus();
  const mo = new MutationObserver(() => { if (!m.isConnected) { mo.disconnect(); stopPoll(); vid.pause(); if (st.job && st.job.status !== "done") api(`/api/gif/job/${st.job.id}/cancel`, {}).catch(() => { }); } });
  mo.observe($("#modal-root"), { childList: true });
  editFoot();
  return m;
}
