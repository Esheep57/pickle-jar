// ES module（2026-10）：用到別的檔案的東西都要在這裡 import；瀏覽器會在載入時檢查名字對不對。
import { $, $$, api, closeModal, esc, fmtAgo, fmtN, follow, modal, toast } from "./core.js";
import { openViewer } from "./viewer.js";
/* ============ 畫師作品比對：拿已知畫師在 Pixiv／X 上的全部作品，跟圖庫裡還沒找到出處的圖比 ============
   Pixiv：程式自己抓（背景，一位一位做，可停止）；X：要你登入，所以由瀏覽器腳本在 x.com/<帳號>/media 往下捲、送網址回來。 */
export async function sweepModal() {
  let s;
  try { s = await api("/api/sweep"); } catch (e) { return toast(e.message); }
  const m = modal(`<h3>畫師作品比對</h3><div class="mbody">
    <p class="mute" style="margin-top:0">已經知道作者的圖，常常同一位畫師還有別張在你的圖庫裡、只是認不出來（手機從 X 存的、截圖）。
      這裡把畫師在網站上的作品縮圖抓回來，在你的電腦上比對；找到的記下出處、確認這位畫師（是在他自己的作品裡比到的），角色放待確認。不上傳你的圖。</p>
    <div class="swsec"><h4>Pixiv <span class="mute" id="sw-pinfo"></span></h4>
      <div class="swrow">這次看 <input type="number" id="sw-n" value="30" min="1" max="2000" style="width:72px"> 位（收藏多的先、看過的跳過；每位最多 400 件作品，30 天後再看新作品）
        <button class="btn primary sm" id="sw-go">開始</button><button class="btn sm" id="sw-stop">停止</button></div>
      <div id="sw-prog"></div></div>
    <div class="swsec"><h4>X <span class="mute" id="sw-xinfo"></span></h4>
      <div class="mute">X 要登入才看得到，所以在你的瀏覽器裡做：油猴腳本 2.6 以上，打開畫師的「媒體」分頁，左下角按「開始比對」，腳本會慢慢往下捲、把圖片網址送回來。
        排一份名單就可以「連續模式」一位接一位看（X 有流量限制，一次幾十位就好）。</div>
      <div class="swrow"><button class="btn sm" id="sw-xq">排名單（收藏多的先 50 位）</button><button class="btn sm primary" id="sw-xopen">打開名單第一位</button></div></div>
    <div id="sw-found"></div></div>
    <div class="mfoot"><button class="btn" data-close>關閉</button></div>`, null, "wide");
  const draw = d => {
    $("#sw-pinfo", m).textContent = `有帳號的畫師 ${fmtN(d.pixiv.artists)} 位 · 看過 ${fmtN(d.pixiv.scanned)} 位 · 找到 ${fmtN(d.pixiv.found_images)} 張`;
    $("#sw-xinfo", m).textContent = `有帳號的畫師 ${fmtN(d.x.artists)} 位 · 看過 ${fmtN(d.x.scanned)} 位 · 找到 ${fmtN(d.x.found_images)} 張 · 名單剩 ${fmtN(d.x.queue)} 位`;
    const st = d.state || {};
    $("#sw-prog", m).innerHTML = st.started ? `<div class="asbox"><div class="ashead"><b>${st.running ? `正在比對${st.current ? `：${esc(st.current)}` : ""}` : "上一次"}</b>
        <span class="mute">${st.finished ? fmtAgo(st.finished) : ""}${st.error ? ` · ${esc(st.error)}` : ""}</span></div>
      <div class="asstats">${st.artists_total ? `<span>畫師 <b>${fmtN(st.artists_done)} / ${fmtN(st.artists_total)}</b></span>` : ""}<span>看過縮圖 <b>${fmtN(st.thumbs)}</b></span>
        <span>仔細比對 <b>${fmtN(st.checked)}</b></span><span class="acc">找到 <b>${fmtN(st.matched)}</b></span><span>流量 <b>${st.mb || 0} MB</b></span></div></div>` : "";
    const ms = (d.matches || []).slice().reverse();
    $("#sw-found", m).innerHTML = ms.length ? `<h4 style="margin:14px 0 6px">最近找到的（點開看）</h4><div class="swthumbs">${ms.map(x =>
      `<img src="/t/${x.id}" data-id="${x.id}" title="${esc(x.artist)} · ${x.site === "pixiv" ? `Pixiv ${x.pid}` : `X ${x.tweet_id}`} · ${Math.round(x.conf * 100)}%">`).join("")}</div>` : "";
    $$("[data-id]", m).forEach(i => i.onclick = () => { closeModal(); openViewer(+i.dataset.id); });
  };
  draw(s);
  follow(() => api("/api/sweep"), draw, { every: 2500, alive: () => document.body.contains(m) });
  $("#sw-n", m).addEventListener("keydown", e => e.stopPropagation());
  $("#sw-go", m).onclick = async () => {
    try { const r = await api("/api/sweep/pixiv", { max_artists: +$("#sw-n", m).value || 30 }); toast(r.queued ? `排了 ${r.queued} 位畫師（背景進行）` : "沒有要看的畫師（都看過了）"); }
    catch (e) { toast(e.message); }
  };
  $("#sw-stop", m).onclick = async () => { await api("/api/online/stop", {}); toast("已要求停止"); };
  $("#sw-xq", m).onclick = async () => { const r = await api("/api/sweep/x/queue", { limit: 50 }); toast(`名單排好了：${r.queued} 位`); draw(await api("/api/sweep")); };
  $("#sw-xopen", m).onclick = async () => {
    const n = await api("/api/sweep/x/next");
    if (!n.handle) return toast("名單是空的：先排名單");
    window.open(`https://x.com/${n.handle}/media#pjsweep`, "_blank", "noopener");    // #pjsweep：腳本直接開始、看完自動下一位
  };
}

