/* 匯出訓練集（app/dataset.py）、標籤蘊含（app/implies.py）的工具視窗（b84） */
import { $, $$, api, closeModal, debounce, esc, fmtN, follow, modal, store, toast } from "./core.js";
import { folderBrowser } from "./pages.js";
import { undo } from "./actions.js";
import { pageQuery } from "./steps.js";

/* ============ 匯出訓練集：圖片＋同名 .txt 標註 ============ */
const CAT_LABEL = { character: "角色", copyright: "作品", artist: "創作者", general: "一般描述標籤", custom: "自訂標籤（梗圖…）" };
export function datasetModal(opts = {}) {
  const ids = opts.ids?.length ? [...opts.ids] : null, pq = opts.query || pageQuery();
  const pageName = ($("#head h1")?.textContent || "這一頁").trim();
  const P = { ...{ cats: ["character", "copyright", "artist", "general"], trigger: "", spaces: true, escape: true, general_min: 0.35, names: "raw", skip_nsfw: false }, ...store.get("datasetOpts", {}) };
  const m = modal(`<h3>匯出訓練集</h3><div class="mbody form">
    <p class="mute" style="margin-top:0">把圖複製到一個資料夾，每張旁邊一個同名的 <code>.txt</code> 標註（LoRA 訓練程式讀的格式）。標註用你確認過的角色、作品、創作者，加上描述標籤。</p>
    <div class="field"><label>範圍</label><div>
      ${ids ? `<label class="chkl"><input type="radio" name="ds-r" value="sel" checked> 選取的 ${fmtN(ids.length)} 張</label>` : ""}
      ${pq ? `<label class="chkl"><input type="radio" name="ds-r" value="page" ${ids ? "" : "checked"}> 這一頁的全部：${esc(pageName)}${pq.q ? ` <code>${esc(pq.q)}</code>` : ""}</label>` : ""}
      <label class="chkl"><input type="radio" name="ds-r" value="q" ${ids || pq ? "" : "checked"}> 搜尋 <input type="text" id="ds-q" placeholder="例如 akiyama_mizuki -tag:梗圖" autocomplete="off"></label></div></div>
    <div class="field"><label>匯出到</label><div><div style="display:flex;gap:6px"><input type="text" id="ds-dest" value="${esc(store.get("datasetDest", ""))}" placeholder="例如 D:\\lora\\mizuki（圖庫以外的資料夾）"><button class="btn sm" id="ds-browse">選資料夾…</button></div><div id="ds-fs"></div></div></div>
    <div class="field"><label>標註要有</label><div>${Object.entries(CAT_LABEL).map(([k, l]) => `<label class="chkl"><input type="checkbox" data-dc="${k}" ${P.cats.includes(k) ? "checked" : ""}> ${l}</label>`).join("")}
      <div class="mute" style="font-size:var(--fs1)">描述標籤分數至少 <input type="number" id="ds-min" min="0" max="1" step="0.05" value="${P.general_min}" style="width:64px">（AI 標的；Danbooru 來的一律放）</div></div></div>
    <div class="field"><label>觸發詞</label><input type="text" id="ds-trig" value="${esc(P.trigger)}" placeholder="選填：放在每段標註最前面，例如 mzk_style"></div>
    <div class="field"><label>格式</label><div>
      <label class="chkl"><input type="checkbox" id="ds-sp" ${P.spaces ? "checked" : ""}> 底線換成空格（long hair）</label>
      <label class="chkl"><input type="checkbox" id="ds-esc" ${P.escape ? "checked" : ""}> 括號加反斜線（akiyama mizuki \\(project sekai\\)）</label>
      <label class="chkl"><input type="checkbox" id="ds-disp" ${P.names === "display" ? "checked" : ""}> 角色、作品、創作者用中文顯示名稱</label>
      <label class="chkl"><input type="checkbox" id="ds-nsfw" ${P.skip_nsfw ? "checked" : ""}> 略過 NSFW</label></div></div>
    <div class="field"><label>預覽</label><div id="ds-prev" class="mute">算一下…</div></div>
  </div><div class="mfoot"><span class="mute" id="ds-sum" style="margin-right:auto"></span><button class="btn" data-close>取消</button><button class="btn primary" id="ds-go" disabled>匯出</button></div>`, null, "wide");
  const body = () => {
    const r = $("[name=ds-r]:checked", m)?.value;
    const o = { cats: $$("[data-dc]", m).filter(x => x.checked).map(x => x.dataset.dc), trigger: $("#ds-trig", m).value, spaces: $("#ds-sp", m).checked,
      escape: $("#ds-esc", m).checked, general_min: +$("#ds-min", m).value || 0, names: $("#ds-disp", m).checked ? "display" : "raw", skip_nsfw: $("#ds-nsfw", m).checked };
    const b = { opts: o, dest: $("#ds-dest", m).value.trim().replace(/^"|"$/g, "") };
    if (r === "sel") b.ids = ids; else if (r === "page") b.query = { ...pq, nsfw: "show" }; else b.query = { view: "all", q: $("#ds-q", m).value.trim(), nsfw: "show" };
    return b;
  };
  let seq = 0, started = false;
  const prev = debounce(async () => {
    if (started) return;                // 開始匯出後不再重算預覽（不然會蓋掉進度）
    const my = ++seq, b = body();
    let r; try { r = await api("/api/dataset/export", { ...b, dry: true }); } catch (e) { if (my === seq) $("#ds-prev", m).textContent = e.message; return; }
    if (my !== seq || started || !document.body.contains(m)) return;
    const sk = r.skipped, skip = [sk.video && `影片 ${sk.video}`, sk.missing && `找不到檔案 ${sk.missing}`, sk.nsfw && `NSFW ${sk.nsfw}`].filter(Boolean);
    $("#ds-prev", m).innerHTML = r.samples.length ? r.samples.map(x => `<div class="note" style="margin:0 0 6px;font-size:var(--fs1)"><b>${x.id}.txt</b>　${esc(x.caption) || "<i>（空的：這張還沒有確認的標籤）</i>"}</div>`).join("") : "沒有可以匯出的圖";
    $("#ds-sum", m).textContent = `${fmtN(r.n)} 張${skip.length ? `（略過 ${skip.join("、")}）` : ""}${r.too_many ? "，一次最多 20,000 張" : ""}`;
    $("#ds-go", m).disabled = !r.n;
  }, 250);
  m.addEventListener("change", e => { if (e.target.id !== "ds-dest") prev(); }); m.addEventListener("input", e => { if (e.target.id === "ds-q") $("[name=ds-r][value=q]", m).checked = true; if (e.target.id !== "ds-dest") prev(); });
  $("#ds-browse", m).onclick = () => folderBrowser($("#ds-fs", m), p => { $("#ds-dest", m).value = p; $("#ds-fs", m).innerHTML = ""; });
  $("#ds-go", m).onclick = async () => {
    const b = body();
    if (!b.dest) { toast("先選要匯出到哪個資料夾"); return; }
    $("#ds-go", m).disabled = true;
    let j; try { j = await api("/api/dataset/export", b); } catch (e) { toast(e.message); $("#ds-go", m).disabled = false; return; }
    started = true; seq++;
    store.set("datasetOpts", b.opts); store.set("datasetDest", b.dest);
    follow(() => api(`/api/dataset/job/${j.id}`), s => {      // 視窗關掉也繼續跟，做完跳提示
      if (!document.body.contains(m)) { if (s.state === "running") return; toast(`訓練集匯出完成：${fmtN(s.done)} 張 → ${s.dest}`); return false; }
      $("#ds-sum", m).textContent = s.state === "running" ? `匯出中 ${fmtN(s.done)}／${fmtN(s.total)}…（關掉視窗也會繼續）`
        : s.state === "error" ? `失敗：${s.error}` : `完成：${fmtN(s.done)} 張${s.converted ? `（${s.converted} 張轉成 PNG）` : ""}${s.failed ? `，${s.failed} 張失敗` : ""} → ${s.dest}`;
      if (s.state !== "running") { $("#ds-go", m).textContent = "完成"; return false; }
    }, { every: 800 });
  };
  prev();
}

/* ============ 標籤蘊含：規則、Danbooru、套用到舊圖 ============ */
export async function impliesModal() {
  let d; try { d = await api("/api/implies"); } catch (e) { return toast(e.message); }
  const m = modal(`<h3>標籤蘊含</h3><div class="mbody">
    <p class="mute" style="margin-top:0">「A 也代表 B」：確認 A 的圖自動帶上 B（例如造型 asuna_(bunny)_(blue_archive) ⇒ 本體 asuna_(blue_archive)）。你否決過的不會被改。單一標籤的規則在標籤頁 ⋯ ›「改名、合併、資料卡」改。</p>
    <table class="diag">
      <tr><th>來自 Danbooru</th><td><b>${fmtN(d.danbooru)}</b> 條（角色 ⇒ 角色）　<span class="mute">問過 ${fmtN(d.checked)} 個角色</span></td></tr>
      <tr><th>角色 ⇒ 所屬作品</th><td><b>${fmtN(d.danbooru_copy || 0)}</b> 條　<span class="mute">照 Danbooru 資料卡的「所屬作品」，資料卡更新時跟著改</span></td></tr>
      <tr><th>你訂的</th><td>${fmtN(d.manual)} 條</td></tr>
      <tr><th>你說「不是」的</th><td>${fmtN(d.off)} 條</td></tr>
      <tr><th>已確認的舊圖</th><td>${d.images ? `<b>${fmtN(d.images)}</b> 張還少了蘊含的標籤（共 ${fmtN(d.rows)} 個）${d.rejected ? `；${fmtN(d.rejected)} 個你否決過，不會動` : ""}` : "都套用過了"}</td></tr>
    </table>
    ${d.list.length ? `<details style="margin-top:10px"><summary>你訂的、關掉的規則（${d.list.length}）</summary><div class="chips" style="margin-top:6px">${d.list.map(x => `<a class="chip" data-goto="${esc(x.name)}">${esc(x.name)} ${x.src === "off" ? "⇏" : "⇒"} ${esc(x.implies)}</a>`).join("")}</div></details>` : ""}
  </div><div class="mfoot"><button class="btn" id="im-sync" style="margin-right:auto">從 Danbooru 更新</button><button class="btn" data-close>關閉</button>
    ${d.images ? `<button class="btn primary" id="im-fill">套用到 ${fmtN(d.images)} 張舊圖</button>` : ""}</div>`);
  $$("[data-goto]", m).forEach(a => a.onclick = () => { closeModal(); location.hash = `#/tag/${encodeURIComponent(a.dataset.goto)}`; });
  $("#im-sync", m).onclick = async () => { try { await api("/api/implies/sync", {}); toast("背景問 Danbooru 中（一次 40 個角色），過一會再打開這裡看"); closeModal(); } catch (e) { toast(e.message); } };
  const f = $("#im-fill", m); if (f) f.onclick = async () => {
    f.disabled = true;
    try { const r = await api("/api/implies/backfill", { dry: false }); closeModal(); toast(`已套用：${fmtN(r.images)} 張多了 ${fmtN(r.rows)} 個標籤`, { label: "撤銷", fn: undo }); }
    catch (e) { toast(e.message); f.disabled = false; }
  };
}
