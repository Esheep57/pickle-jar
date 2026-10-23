// ES module（2026-10）：用到別的檔案的東西都要在這裡 import；瀏覽器會在載入時檢查名字對不對。
import { $, $$, api, closeModal, esc, modal, toast } from "./core.js";
import { undo } from "./actions.js";
/* ============ 貼出處網址（b94，app/srcurl.py）============
   圖庫裡已經有的圖，貼 X／Pixiv 的貼文網址 → 程式抓那篇的圖跟你的圖比對 → 你勾好再寫入（出處、創作者、作品、角色）。
   跟 ascii2d 同一套比對和候選規則：標籤推的角色預設不勾；跟你已確認的角色衝突的不勾；不太像的不列候選。 */
const MATCH = { same: ["同一張", "okc"], likely: ["很像", "okc"], unlikely: ["不太像", "bad"], null: ["沒辦法比對畫面", "warnc"] };

export function srcUrlModal(ids, after) {
  ids = [...new Set(ids)];
  const m = modal(`<h3>貼出處網址</h3><div class="mbody suw">
    <p class="mute" style="margin-top:0">貼 X 或 Pixiv 的貼文網址：程式抓那篇的圖跟${ids.length > 1 ? `這 ${ids.length} 張` : "這張"}比對，對得上就記成出處、帶上作者和角色（你勾的才算）。不同解析度、裁切過的也認得；改了表情或字的差分會說「不太像」。</p>
    <div class="surow"><input id="su-url" placeholder="https://x.com/…/status/…　或　https://www.pixiv.net/artworks/…" autocomplete="off"><button class="btn" id="su-go">比對</button></div>
    <div id="su-out"></div></div>
    <div class="mfoot"><span class="mute" id="su-note" style="margin-right:auto"></span><button class="btn" data-close>取消</button><button class="btn primary" id="su-ok" disabled>確認 <kbd>Enter</kbd></button></div>`, null, "wide");
  const inp = $("#su-url", m), out = $("#su-out", m), ok = $("#su-ok", m), note = $("#su-note", m);
  let res = null;
  inp.focus();
  const run = async () => {
    const url = inp.value.trim(); if (!url) return;
    out.innerHTML = `<p class="mute">讀貼文、比對中…（Pixiv 多頁的作品要多等一下）</p>`; ok.disabled = true; res = null;
    try { res = await api("/api/media/source-url/check", { ids, url }); }
    catch (e) { out.innerHTML = `<p class="bad">${esc(e.message)}</p>`; return; }
    draw();
  };
  const draw = () => {
    const unit = res.site === "pixiv" ? "頁" : "張";
    out.innerHTML = `<p class="mute">${res.site === "pixiv" ? "Pixiv" : "X"} · ${esc(res.author || "")} · 貼文裡 ${res.pages} ${unit}${res.compared < res.pages ? `（${res.pages - res.compared} ${unit}抓不到）` : ""}</p>`
      + res.items.map(it => {
        if (it.error) return `<div class="surow2"><img src="/t/${it.id}" alt=""><div class="bad">${esc(it.error)}</div></div>`;
        const [lab, cls] = MATCH[it.match] || MATCH.null;
        const on = it.match === "same" || it.match === "likely";
        return `<div class="surow2" data-mid="${it.id}"><img src="/t/${it.id}" alt=""><div>
          <label class="sumatch"><input type="checkbox" data-rec ${on ? "checked" : ""}> 記成出處 <b class="${cls}">${lab}${it.score != null ? ` ${Math.round(it.score * 100)}%` : ""}</b>
            <span class="mute">${it.score != null ? `跟第 ${res.site === "pixiv" ? it.page + 1 : it.page} ${unit}比（${esc(it.verify_by)}）` : ""}</span></label>
          ${it.conflict ? `<div class="warnc">Danbooru 上這篇的角色是 ${esc(it.conflict.join("、"))}，跟這張已確認的不一樣：那些角色先不勾</div>` : ""}
          ${it.tags.length ? `<div class="sutags">${it.tags.map(t => `<label title="${esc(t.why)}"><input type="checkbox" data-t="${esc(t.name)}" ${t.checked ? "checked" : ""}><span class="cat ${esc(t.category)}">${{ artist: "創作者", character: "角色", copyright: "作品" }[t.category] || ""}</span>${esc(t.display || t.name)}</label>`).join("")}</div>`
            : it.match === "unlikely" ? `<div class="mute">不像同一張：不列標籤。確定是的話還是可以勾「記成出處」</div>` : `<div class="mute">沒有可以加的標籤（只會記出處）</div>`}
        </div></div>`;
      }).join("");
    $$("[data-rec]", out).forEach(x => x.onchange = sync);
    sync();
  };
  const sync = () => {
    const n = $$("[data-rec]:checked", out).length;
    ok.disabled = !n;
    note.textContent = n ? `${n} 張記出處、${$$(".surow2", out).filter(r => r.querySelector("[data-rec]:checked")).reduce((a, r) => a + r.querySelectorAll("[data-t]:checked").length, 0)} 個標籤` : "";
  };
  const apply = async () => {
    if (!res || ok.disabled) return;
    const picks = {};
    $$(".surow2[data-mid]", out).forEach(r => { if (r.querySelector("[data-rec]:checked")) picks[r.dataset.mid] = $$("[data-t]:checked", r).map(x => x.dataset.t); });
    ok.disabled = true;
    try {
      const r = await api("/api/media/source-url/apply", { token: res.token, picks });
      closeModal();
      toast(`已記出處 ${r.applied} 張${r.tags ? `、確認 ${r.tags} 個標籤` : ""}`, r.tags ? { label: "撤銷", fn: undo } : undefined);
      after?.();
    } catch (e) { toast(e.message); ok.disabled = false; }
  };
  $("#su-go", m).onclick = run;
  ok.onclick = apply;
  inp.addEventListener("keydown", e => { e.stopPropagation(); if (e.key === "Enter") { e.preventDefault(); run(); } });
  inp.addEventListener("paste", () => setTimeout(run, 0));      // 貼上就開始比對
  m.addEventListener("keydown", e => { if (e.key === "Enter" && e.target !== inp && !ok.disabled) { e.preventDefault(); apply(); } });
}
