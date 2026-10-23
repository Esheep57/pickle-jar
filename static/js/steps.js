// ES module（2026-10）：用到別的檔案的東西都要在這裡 import；瀏覽器會在載入時檢查名字對不對。
import { $, $$, S, api, ask, closeModal, debounce, esc, fmtN, modal, toast } from "./core.js";
import { gridParams } from "./layout.js";
/* ============ 設定 → 自動標記與搜源（b69）============
   每一步：開／關、找到時怎麼辦（直接確認／放待確認／只記下）、門檻、準確度參考（你按過的是／不是）。
   後端是 app/steps.py（一張表），這裡照表畫；改了馬上存、馬上重算準確度。
   「補跑」：選範圍（選取的、這一頁、未識別、最近加入、搜尋語法、全部）＋選步驟，背景跑。 */

const pct = p => p == null ? "—" : `${Math.round(p * 100)}%`;
const SAME = (a, b) => String(a) === String(b);
let open = new Set();

function accLine(st) {
  const x = st.stats; if (!x) return "";
  const cls = x.precision == null ? "" : x.precision >= 0.9 ? "good" : x.precision < 0.7 ? "warn" : "";
  if (st.default_act === "confirm" || st.act === "confirm") {
    const tot = x.auto + x.yes + x.no;
    if (!tot) return `<span class="stp-acc">還沒有紀錄</span>`;
    return `<span class="stp-acc ${cls}" title="直接確認的標籤，你後來按「不是」的比例">確認 ${fmtN(x.auto + x.yes)}<i>·</i>你否決 ${fmtN(x.no)}${x.precision != null ? `<i>·</i><b>${pct(x.precision)}</b> 對` : ""}</span>`;
  }
  const n = x.yes + x.no;
  if (!n) return `<span class="stp-acc">${x.pending ? `待確認 ${fmtN(x.pending)}<i>·</i>` : ""}還沒有你的紀錄</span>`;
  return `<span class="stp-acc ${cls}" title="你按過 ${fmtN(n)} 次：是 ${fmtN(x.yes)}、不是 ${fmtN(x.no)}">你按「是」<b>${pct(x.precision)}</b><i>·</i>${fmtN(n)} 次${x.pending ? `<i>·</i>待確認 ${fmtN(x.pending)}` : ""}</span>`;
}

function curveHTML(st, t) {
  const cv = (st.stats?.curves || []).find(c => c.k === t.k); if (!cv) return "";
  const c = cv.cur;
  const pts = cv.pts.map(p => `<span class="cv-pt ${SAME(p.v, t.v) ? "cur" : ""}" title="門檻 ${p.v}${t.unit}：留下 ${fmtN(p.n)} 筆，你按「是」${pct(p.p)}"><i style="height:${Math.max(4, Math.round(p.p * 100))}%"></i><em>${p.v}</em></span>`).join("");
  return `<div class="stpcv"><div class="cv-bars">${pts}</div><div class="cv-txt">目前 ${esc(String(t.v))}${t.unit}：你按過的 ${fmtN(cv.of)} 筆裡留下 ${fmtN(c.n)} 筆，<b>${pct(c.p)}</b> 是對的。柱子＝每個門檻下你按「是」的比例。</div></div>`;
}

function modified(st) {
  return (st.act && st.act !== st.default_act) || st.thr.some(t => !SAME(t.v, t.default)) || st.extra.some(e => e.v !== e.default)
    || (st.on && st.on_default !== undefined && st.enabled !== st.on_default) || (st.choice && !SAME(st.choice.v, st.choice.default));
}

function stepHTML(st) {
  const lead = st.on ? `<label class="switch sm" title="${st.enabled ? "開著" : "關著"}"><input type="checkbox" data-sk="${st.on}" ${st.enabled ? "checked" : ""}><i></i></label>`
    : `<span class="stp-fixed" title="一定會做（沒有開關）"></span>`;
  const act = st.acts.length ? `<select class="stp-act" data-sk="act_${st.key}" title="找到時怎麼辦">${st.acts.map(([k, l]) => `<option value="${k}" ${k === st.act ? "selected" : ""}>${l}</option>`).join("")}</select>`
    : st.key === "pixelhash" ? `<span class="stp-same">同 Danbooru 原檔</span>` : "";
  const thr = st.thr.map(t => `<div class="stp-thr"><label><span>${esc(t.label)}</span>
      <input type="range" data-sk="${t.k}" min="${t.min}" max="${t.max}" step="${t.step}" value="${t.v}">
      <output>${esc(String(t.v))}${t.unit}</output>${!SAME(t.v, t.default) ? `<button class="lnk" data-def="${t.k}" data-v="${t.default}">預設 ${t.default}${t.unit}</button>` : ""}</label>
      ${t.desc ? `<div class="snote">${esc(t.desc)}</div>` : ""}${curveHTML(st, t)}</div>`).join("");
  const extra = st.extra.map(e => `<label class="chkl"><input type="checkbox" data-sk="${e.k}" ${e.v ? "checked" : ""}> ${esc(e.label)}</label>`).join("");
  const choice = st.choice ? `<label class="stp-choice">${esc(st.choice.label)} <select data-sk="${st.choice.k}">${st.choice.opts.map(([v, l]) => `<option value="${v}" ${SAME(v, st.choice.v) ? "selected" : ""}>${l}</option>`).join("")}</select></label>` : "";
  const confirmWarn = st.act === "confirm" && st.default_act !== "confirm" ? `<div class="snote warnc">直接確認時，你的否決、「不用整理」、梗圖這些規則一樣有效；改了只影響之後找到的，舊圖用「補跑」。</div>` : "";
  const recNote = st.act === "record" ? `<div class="snote">只記下：結果照存（大圖「詳細」看得到），但不加標籤、不進待確認。</div>` : "";
  return `<div class="stp-row ${st.enabled ? "" : "off"} ${open.has(st.key) ? "open" : ""}" data-step="${st.key}">
    <div class="stp-main">${lead}<button class="stp-name" data-toggle>${esc(st.name)}${modified(st) ? `<i class="stp-mod" title="跟預設不一樣"></i>` : ""}</button>
      ${st.needs ? `<span class="stp-need">${esc(st.needs)}</span>` : accLine(st)}${act}</div>
    <div class="stp-detail"><div class="snote">${esc(st.desc)}</div>${recNote}${confirmWarn}${thr}${extra}${choice}
      <div class="stp-foot">${modified(st) ? `<button class="btn sm" data-reset="${st.key}">這一步恢復預設</button>` : ""}${st.rerun ? `<button class="btn sm" data-rerun="${st.rerun}">補跑這一步…</button>` : ""}</div></div>
  </div>`;
}

function commonHTML(c, def) {
  const sw = (k, label, desc) => `<div class="srow"><div class="slabel"><b>${label}</b><div class="snote">${desc}</div></div><div class="sctl"><label class="switch"><input type="checkbox" data-sk="${k}" ${c[k] ? "checked" : ""}><i></i></label></div></div>`;
  const num = (k, label, desc, step, unit = "") => `<div class="srow"><div class="slabel"><b>${label}</b><div class="snote">${desc}</div></div><div class="sctl"><span class="numw"><input type="number" data-sk="${k}" step="${step}" value="${c[k]}">${unit ? `<em>${unit}</em>` : ""}</span></div></div>`;
  return `<div class="stp-common">
    ${sw("online_enabled", "線上功能（總開關）", "關掉後不連任何網站：只做本機的那幾步。")}
    ${sw("online_auto_source", "新圖自動找出處", "新圖、還沒搜過的圖在背景慢慢找。手動按 S 不受影響。")}
    <div class="srow"><div class="slabel"><b>自動找出處前先預判</b><div class="snote">不連網先判斷值不值得上傳：開發時的圖庫實測找到率 很可能 30%、可能 4%、不太可能約 1%。真人照片一律不自動上傳。</div></div>
      <div class="sctl"><select data-sk="online_auto_level">${[["likely", "只搜「很可能」的"], ["maybe", "也搜「可能」的（推薦）"], ["all", "全部都搜（很慢）"]].map(([v, l]) => `<option value="${v}" ${v === c.online_auto_level ? "selected" : ""}>${l}</option>`).join("")}</select></div></div>
    ${num("online_retry_days", "找不到的隔幾天重搜", "Danbooru 每天都有新圖。", 1, "天")}
    ${sw("ai_hide_when_sourced", "找到出處後收掉 AI 的角色候選", "Danbooru 的角色清單是人工整理的，比 AI 可靠。")}
    ${sw("auto_rematch", "否決後清掉相似圖上的同一個候選", "你按「不是 X」，其他很像的圖上自動加的 X 也一起拿掉。")}
    ${num("neg_sim", "否決傳播的相似度", "跟被否決的圖相似度 ≥ 這個值，不會再自動標同一個（0.9≈同一張圖的變體）。", 0.01)}
  </div>`;
}

export async function drawSteps(root) {
  let d;
  try { d = await api("/api/steps"); } catch (e) { root.innerHTML = `<div class="snote">載入失敗：${esc(e.message)}</div>`; return; }
  if (!document.body.contains(root)) return;
  const groups = d.groups.map(g => {
    const ss = d.steps.filter(s => s.group === g.key); if (!ss.length) return "";
    return `<div class="stp-group"><h6 class="stp-g">${esc(g.name)}<span>${esc(g.desc)}</span></h6>${ss.map(stepHTML).join("")}</div>`;
  }).join("");
  root.innerHTML = `<div class="stp">
    <div class="stp-intro"><div class="snote">新圖由上往下做：本機的先，Danbooru 找到同一張就停。每一步都能關掉、改門檻、決定找到時怎麼辦；預設是目前實測最穩定的做法。改了只影響之後，舊圖用「補跑」。</div>
      <div class="stp-btns"><button class="btn sm primary" id="stp-rerun">補跑舊圖…</button><button class="btn sm" id="stp-reset">全部恢復預設</button></div></div>
    ${d.common.online_enabled ? "" : `<div class="pwarn">線上功能關著：Danbooru、其他圖站、找到之後的步驟都不會做（最下面「共同規則」可以打開）</div>`}
    <div class="stp-head"><span>步驟</span><span>準確度參考</span><span>找到時</span></div>
    ${groups}
    <div class="stp-group"><h6 class="stp-g">共同規則<span>不屬於某一步</span></h6>${commonHTML(d.common, d.common_default)}</div></div>`;
  bindSteps(root, d);
  if (S.stepFocus) {        // 從系統頁的流程圖點過來：展開那一步、捲過去
    const row = $(`.stp-row[data-step="${S.stepFocus}"]`, root); S.stepFocus = null;
    if (row) { row.classList.add("open", "flash"); open.add(row.dataset.step); row.scrollIntoView({ block: "center" }); setTimeout(() => row.classList.remove("flash"), 1600); }
  }
}

function bindSteps(root, d) {
  const redraw = debounce(() => drawSteps(root), 500);
  const save = async (k, v) => {
    try { S.settings = await api("/api/settings", { [k]: v }); redraw(); }
    catch (e) { toast("儲存失敗：" + e.message); redraw(); }
  };
  root.onclick = async e => {
    const tg = e.target.closest("[data-toggle]");
    if (tg) { const row = tg.closest(".stp-row"); row.classList.toggle("open"); open[row.classList.contains("open") ? "add" : "delete"](row.dataset.step); return; }
    const df = e.target.closest("[data-def]"); if (df) { const n = +df.dataset.v; return save(df.dataset.def, isNaN(n) ? df.dataset.v : n); }
    const rs = e.target.closest("[data-reset]");
    if (rs) {
      const st = d.steps.find(s => s.key === rs.dataset.reset); const p = {};
      if (st.acts.length) p[`act_${st.key}`] = st.default_act;
      if (st.on && st.on_default !== undefined) p[st.on] = st.on_default;
      st.thr.forEach(t => p[t.k] = t.default); st.extra.forEach(x => p[x.k] = x.default); if (st.choice) p[st.choice.k] = st.choice.default;
      try { S.settings = await api("/api/settings", p); toast(`${st.name}：恢復預設`); drawSteps(root); } catch (err) { toast(err.message); }
      return;
    }
    const rr = e.target.closest("[data-rerun]"); if (rr) return openRerun({ steps: [rr.dataset.rerun] });
    if (e.target.closest("#stp-rerun")) return openRerun({});
    if (e.target.closest("#stp-reset")) {
      if (!(await ask("自動標記與搜源全部恢復預設？", "開關、找到時怎麼辦、門檻都回到目前實測最穩定的設定（線上功能總開關不動）。", "恢復預設"))) return;
      try { await api("/api/steps/reset", {}); toast("已恢復預設"); drawSteps(root); } catch (err) { toast(err.message); }
    }
  };
  root.oninput = e => { const r = e.target.closest("input[type=range]"); if (r) { const o = r.parentElement.querySelector("output"); const t = d.steps.flatMap(s => s.thr).find(x => x.k === r.dataset.sk); if (o) o.textContent = r.value + (t?.unit || ""); } };
  root.onchange = e => {
    const el = e.target.closest("[data-sk]"); if (!el) return;
    e.stopPropagation();
    const v = el.type === "checkbox" ? el.checked : el.type === "range" || el.type === "number" ? +el.value : el.value;
    save(el.dataset.sk, v);
  };
}

/* ============ 補跑：選範圍 ＋ 選步驟 ============ */
export function pageQuery() {
  if (!S.layout || ["review", "settings", "system", "home", "dups"].includes(S.view)) return null;
  const p = Object.fromEntries(gridParams());
  ["offset", "limit", "sort", "seed", "until", "rev"].forEach(k => delete p[k]);
  return p;
}

export async function openRerun(opts = {}) {
  let d; try { d = await api("/api/steps?stats=0"); } catch (e) { return toast(e.message); }
  const ids = opts.ids?.length ? [...opts.ids] : null;
  const pq = opts.query || pageQuery();
  const pageName = ($("#head h1")?.textContent || "這一頁").trim();
  const R = [];
  if (ids) R.push(["sel", `選取的 ${fmtN(ids.length)} 張`]);
  if (pq) R.push(["page", `目前這頁的條件：${esc(pageName)}${pq.q ? `　<code>${esc(pq.q)}</code>` : ""}`]);
  R.push(["unrec", "未識別（完全沒有角色、作者、作品）"], ["noartist", "缺創作者"], ["unsourced", "還沒找到出處"],
    ["recent", `最近加入的 <select id="rr-days">${[1, 3, 7, 14, 30, 90].map(n => `<option value="${n}" ${n === 7 ? "selected" : ""}>${n} 天</option>`).join("")}</select>`],
    ["q", `搜尋語法 <input id="rr-q" placeholder="例如 is:noartist added:30d rating:safe" autocomplete="off">`], ["all", "整個圖庫"]);
  const want = new Set(opts.steps?.length ? opts.steps : ["source"]);
  const preset = opts.preset || (ids ? "sel" : pq ? "page" : "unrec");
  const m = modal(`<h3>補跑</h3><div class="mbody rr">
    <div class="rr-col"><div class="rr-h">範圍</div>
      ${R.map(([k, l]) => `<label class="rr-opt"><input type="radio" name="rr-r" value="${k}" ${k === preset ? "checked" : ""}><span>${l}</span></label>`).join("")}
      <label class="chkl rr-nsfw"><input type="checkbox" id="rr-nsfw" checked> 包含 NSFW</label>
      <div class="rr-n" id="rr-n">算一下有幾張…</div></div>
    <div class="rr-col"><div class="rr-h">要跑哪幾步</div>
      ${d.rerun.map(r => `<label class="rr-step"><input type="checkbox" data-rs="${r.key}" ${want.has(r.key) ? "checked" : ""}><span><b>${esc(r.name)}</b><em>${esc(r.desc)}</em></span><i data-rc="${r.key}"></i></label>
        ${r.key === "source" ? `<label class="chkl rr-sub"><input type="checkbox" id="rr-force"> 已經找到出處的也重搜</label>` : ""}`).join("")}
      <div class="snote">結果照設定裡每一步的「找到時怎麼辦」處理；你確認、否決、手動加的不會被改。都在背景做，可以關掉這個視窗。</div></div>
  </div><div class="mfoot"><span class="mute" id="rr-sum" style="margin-right:auto"></span><button class="btn" data-close>取消</button><button class="btn primary" id="rr-go" disabled>開始</button></div>`, null, "rr-modal");
  const body = () => {
    const r = $("[name=rr-r]:checked", m)?.value, nsfw = $("#rr-nsfw", m).checked ? "show" : "hide";
    const steps = $$("[data-rs]", m).filter(x => x.checked).map(x => x.dataset.rs);
    const b = { steps, resource: $("#rr-force", m)?.checked || false };
    if (r === "sel") b.ids = ids;
    else if (r === "page") b.query = { ...pq, nsfw };
    else if (r === "unrec") b.query = { view: "unrecognized", miss: "all", nsfw };
    else if (r === "noartist") b.query = { view: "unrecognized", miss: "artist", nsfw };
    else if (r === "unsourced") b.query = { view: "all", q: "is:unsourced", nsfw };
    else if (r === "recent") b.query = { view: "all", q: `added:${$("#rr-days", m).value}d`, nsfw };
    else if (r === "q") b.query = { view: "all", q: $("#rr-q", m).value.trim(), nsfw };
    else b.query = { view: "all", nsfw };
    return b;
  };
  let seq = 0;
  const count = debounce(async () => {
    const my = ++seq, b = body();
    $("#rr-n", m).textContent = "算一下有幾張…";
    let r; try { r = await api("/api/rerun", { ...b, steps: b.steps.length ? b.steps : ["wd"], dry: true }); } catch (e) { if (my === seq) $("#rr-n", m).textContent = e.message; return; }
    if (my !== seq || !document.body.contains(m)) return;
    $("#rr-n", m).innerHTML = `共 <b>${fmtN(r.n)}</b> 張`;
    $$("[data-rc]", m).forEach(i => { const k = i.dataset.rc; i.textContent = !b.steps.includes(k) ? "" : r.why[k] ? r.why[k] : `${fmtN(r.per[k] || 0)} 張`; i.classList.toggle("bad", !!r.why[k]); });
    const tot = b.steps.reduce((a, k) => a + (r.why[k] ? 0 : r.per[k] || 0), 0);
    $("#rr-sum", m).textContent = b.steps.length ? `${b.steps.length} 步，合計 ${fmtN(tot)} 張次` : "選至少一步";
    $("#rr-go", m).disabled = !b.steps.length || !tot;
  }, 250);
  m.addEventListener("change", count); m.addEventListener("input", e => { if (e.target.id === "rr-q") { $("[name=rr-r][value=q]", m).checked = true; count(); } });
  $("#rr-days", m).addEventListener("change", () => { $("[name=rr-r][value=recent]", m).checked = true; });
  $("#rr-q", m).addEventListener("focus", () => { $("[name=rr-r][value=q]", m).checked = true; count(); });
  $("#rr-go", m).onclick = async () => {
    $("#rr-go", m).disabled = true;
    try {
      const r = await api("/api/rerun", body());
      const names = Object.entries(r.queued || {}).map(([k, n]) => `${d.rerun.find(x => x.key === k)?.name || k} ${fmtN(n)} 張`);
      toast(names.length ? `已排入補跑：${names.join("、")}（背景進行）` : "沒有要補跑的圖");
      closeModal();
    } catch (e) { toast(e.message); $("#rr-go", m).disabled = false; }
  };
  count();
}
