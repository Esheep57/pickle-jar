// ES module（2026-10）：用到別的檔案的東西都要在這裡 import；瀏覽器會在載入時檢查名字對不對。
import { $, $$, S, api, catName, closeModal, debounce, esc, fmtN, modal, svg, toast } from "./core.js";
import { V } from "./viewer.js";
/* ============ 加標籤（所有地方共用）============
   - 上面分類：角色／創作者／作品／自訂／一般（輸入 c: a: s: 也能切換）；只能選某一類時鎖住
   - 還沒打字：這幾張圖的候選、同作品的其他角色、最近用過、置頂——不再列 1girl、long_hair 這種描述
   - 打字：只搜目前分類，顯示中文名＋原始名稱＋作品，已經有的打勾
   - Enter 加上並關閉；Shift+Enter 加上後繼續加下一個（多人圖一次標完） */
const PICK_CATS = [["character", "角色"], ["artist", "創作者"], ["copyright", "作品"], ["custom", "自訂"], ["general", "一般"]];
const PICK_PREFIX = { "c:": "character", "a:": "artist", "s:": "copyright", "w:": "copyright", "g:": "general", "t:": "custom" };
const pickRecent = {
  get: cat => { try { return JSON.parse(localStorage.getItem("lib:pickRecent:" + cat) || "[]"); } catch { return []; } },
  add: t => { try { const k = "lib:pickRecent:" + t.category; const a = pickRecent.get(t.category).filter(x => x.name !== t.name); a.unshift({ name: t.name, category: t.category, display: t.display || "" }); localStorage.setItem(k, JSON.stringify(a.slice(0, 12))); } catch { } },
};

export function pickTag({ title, category = "custom", navigate = false, only = null, ids = null, context = null }, onPick) {
  if (!ids) ids = S.selected?.size && S.view !== "review" ? [...S.selected] : (V ? [V.id] : []);
  if (context == null && S.view === "tag") context = S.tag;
  let cat = only || (navigate ? "all" : (category === "general" ? "general" : category));
  const cats = navigate ? [["all", "全部"], ...PICK_CATS] : PICK_CATS;
  const thumbs = (ids || []).slice(0, 5).map(id => `<img src="/t/${id}" alt="">`).join("") + (ids.length > 5 ? `<span>+${ids.length - 5}</span>` : "");
  const m = modal(`<div class="picker2">
      <div class="pk-head"><div class="pk-title">${esc(title)}</div>${ids.length ? `<div class="pk-thumbs" title="${ids.length} 張">${thumbs}</div>` : ""}</div>
      <div class="pk-cats">${cats.map(([k, l]) => `<button data-c="${k}" class="${k === cat ? "on" : ""}" ${only && k !== only ? "disabled" : ""}>${l}</button>`).join("")}</div>
      <div class="pk-inbox">${svg("search", 16)}<input placeholder="輸入名稱、中文或日文名…（c: 角色 a: 創作者 s: 作品）" autocomplete="off"></div>
      <div class="pk-added hidden"></div>
      <div class="opts"></div>
      <div class="pk-foot"><span><kbd>↑↓</kbd> 選擇</span><span><kbd>Enter</kbd> 加上</span>${navigate ? "" : `<span><kbd>Shift</kbd>+<kbd>Enter</kbd> 加上、繼續加下一個</span>`}<span><kbd>Tab</kbd> 換分類</span><span><kbd>Esc</kbd> 取消</span></div></div>`, null, "pickm");
  const inp = $("input", m), opts = $(".opts", m), added = $(".pk-added", m);
  let rows = [], hl = 0, sug = null, addedList = [];
  const have = new Set();
  const row = (t, extra = "", noSer = false) => {
    const disp = t.display && t.display !== t.name ? t.display : (t.zh || "");
    const on = have.has(t.name);
    return { t, html: `<span class="cat ${esc(t.category)}">${catName(t.category)}</span><span class="nm">${esc(disp || t.name)}</span>${disp ? `<span class="raw">${esc(t.name)}</span>` : ""}${t.copyright && !noSer ? `<span class="ser">${esc(t.copyright.replace(/_/g, " "))}</span>` : ""}${t.alias ? `<span class="ser">${esc(t.alias)}</span>` : ""}${extra}${on ? `<span class="hv" title="這幾張都已經有了">✓ 已有</span>` : ""}<span class="n">${t.n ?? ""}</span>` };
  };
  // Danbooru 角色表的角色（圖庫裡還沒有）：標出來、張數是 Danbooru 上的
  const catTag = t => `<span class="src" title="Danbooru 上 ${fmtN(t.posts)} 張；圖庫裡還沒有">Danbooru ${fmtN(t.posts)}</span>`;
  const SRCN = { model: "AI", knn: "你的圖庫", danbooru: "Danbooru", pixiv: "Pixiv", e621: "e621", rule34: "Rule34", signature: "簽名", tracemoe: "trace.moe", x: "X", ccip: "CCIP" };
  const draw = () => {
    const q = inp.value.trim();
    const qn = q.replace(/ /g, "_");
    let html = "", idx = 0;
    const sect = (label, list) => {
      if (!list.length) return;
      html += `<div class="pk-sec">${label}</div>`;
      for (const r of list) { rows[idx] = r.t; html += `<div class="opt ${idx === hl ? "hl" : ""}" data-i="${idx}">${r.html}</div>`; idx++; }
    };
    rows = [];
    if (!q && sug) {
      const fit = t => cat === "all" || t.category === cat;
      if (cat === "custom" || cat === "all") sect("快速標籤（數字鍵）", (S.quick || []).map(x => row({ name: x.name, category: "custom", n: x.n }, `<span class="src">${x.key}</span>`)));
      sect(`這${ids.length > 1 ? ` ${ids.length} 張` : "張"}的候選`, sug.candidates.filter(fit).map(t => row(t, `<span class="src">${esc((t.sources || "").split(",").map(x => SRCN[x] || x).join("・"))}${ids.length > 1 ? ` · ${t.on_n}/${ids.length} 張` : ""}</span>`)));
      sect(`同作品的角色${sug.series.length ? `（${esc(sug.series.map(x => x.replace(/_/g, " ")).join("、"))}）` : ""}`, sug.mates.filter(fit).map(t => row(t, t.catalog ? catTag(t) : "", true)));
      const rec = pickRecent.get(cat === "all" ? "character" : cat).filter(t => !have.has(t.name));
      sect("最近用過", rec.map(t => row(t)));
      sect("置頂", sug.pinned.filter(fit).map(t => row(t)));
      if (!idx) html = `<div class="hint pk-empty">${cat === "general" ? "輸入以搜尋描述標籤（例如：雙馬尾、twintails）" : "輸入名字搜尋（中文、日文、英文都可以）"}</div>`;
    } else {
      const res = sug?.results || [];
      sect(q ? "搜尋結果" : "", res.filter(t => !t.catalog).map(t => row(t)));
      sect("Danbooru 角色表（圖庫裡還沒有）", res.filter(t => t.catalog).map(t => row(t, catTag(t))));
      if (!navigate && qn && !(sug?.results || []).some(t => t.name === qn)) {
        const nc = cat === "all" || cat === "general" ? "custom" : cat;
        rows[idx] = { name: qn, category: nc, _new: true };
        html += `<div class="opt new ${idx === hl ? "hl" : ""}" data-i="${idx}"><span class="cat">新增</span><span class="nm">${esc(qn)}</span><span class="ser">建立成新的「${catName(nc)}」標籤</span></div>`;
        idx++;
      }
      if (!idx) html = `<div class="hint pk-empty">找不到</div>`;
    }
    opts.innerHTML = html;
    const h = $(".opt.hl", opts); if (h) h.scrollIntoView({ block: "nearest" });
  };
  const loadSug = async () => {
    try { sug = { ...(await api("/api/tags/suggest", { ids, category: cat === "all" ? null : cat, context })), results: [] }; } catch { sug = { candidates: [], have: [], mates: [], pinned: [], series: [], results: [] }; }
    have.clear(); if (ids.length) sug.have.filter(t => t.on_n === ids.length).forEach(t => have.add(t.name));
    hl = 0; draw();
  };
  const search = debounce(async () => {
    let q = inp.value.trim();
    const pre = Object.keys(PICK_PREFIX).find(p => q.toLowerCase().startsWith(p));
    if (pre && !only) { setCat(PICK_PREFIX[pre], false); q = q.slice(pre.length).trim(); inp.value = q; }
    if (!q) { hl = 0; if (sug) sug.results = []; draw(); return; }
    const p = new URLSearchParams({ q, limit: 40 }); if (cat !== "all") p.set("category", cat);
    const res = await api("/api/tags?" + p).catch(() => []);
    if (inp.value.trim() !== q) return;
    sug = sug || { candidates: [], have: [], mates: [], pinned: [], series: [] };
    sug.results = res; hl = 0; draw();
  }, 110);
  const setCat = (c, reload = true) => {
    if (only) return;
    cat = c; $$(".pk-cats button", m).forEach(b => b.classList.toggle("on", b.dataset.c === c));
    if (reload) { loadSug().then(() => inp.value.trim() && search()); }
  };
  const choose = async (i, keep) => {
    const t = rows[i]; if (!t) return;
    if (!navigate && have.has(t.name) && !keep) { toast("這幾張都已經有這個標籤了"); return; }
    pickRecent.add(t);
    if (!keep || navigate) { closeModal(); onPick(t); return; }
    await onPick(t);
    addedList.push(t.display || t.name); have.add(t.name);
    added.classList.remove("hidden"); added.innerHTML = `已加上：${addedList.map(esc).join("、")}`;
    inp.value = ""; if (sug) sug.results = []; hl = 0; loadSug(); inp.focus();
  };
  $$(".pk-cats button", m).forEach(b => b.onclick = () => { setCat(b.dataset.c); inp.focus(); });
  inp.addEventListener("input", search);
  inp.addEventListener("keydown", e => {
    const max = rows.length;
    if (e.key === "ArrowDown") { hl = Math.min(hl + 1, max - 1); draw(); e.preventDefault(); }
    else if (e.key === "ArrowUp") { hl = Math.max(hl - 1, 0); draw(); e.preventDefault(); }
    else if (e.key === "Enter") { choose(hl, e.shiftKey); e.preventDefault(); }
    else if (e.key === "Tab" && !only) { const ks = cats.map(c => c[0]); setCat(ks[(ks.indexOf(cat) + (e.shiftKey ? ks.length - 1 : 1)) % ks.length]); e.preventDefault(); }
    else if (e.key === "Escape") closeModal();
    e.stopPropagation();
  });
  opts.addEventListener("click", e => { const o = e.target.closest(".opt"); if (o) choose(+o.dataset.i, e.shiftKey); });
  loadSug(); inp.focus();
}
