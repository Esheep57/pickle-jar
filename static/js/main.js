// ES module（2026-10）：用到別的檔案的東西都要在這裡 import；瀏覽器會在載入時檢查名字對不對。
import { S, api, startEvents } from "./core.js";
import { applyNsfwUI, go, loadSidebar, pollStatus, route } from "./shell.js";
import { render } from "./grid.js";
import { clearSel, selectAll, setSel } from "./selection.js";
import { pickTag } from "./picker.js";
import { openViewer } from "./viewer.js";
// 每個模組都要載入（有些只註冊事件，沒有名字被 import）
import "./core.js";
import "./shell.js";
import "./grid.js";
import "./layout.js";
import "./filters.js";
import "./selection.js";
import "./actions.js";
import "./unrec.js";
import "./picker.js";
import "./review.js";
import "./shelf.js";
import "./albums.js";
import "./viewer.js";
import "./palette.js";
import "./pages.js";
import "./settings.js";
import "./work.js";
import "./srcurl.js";

// 除錯／畫面測試用：在瀏覽器主控台打 lib.S、lib.render()…（模組化以後這些不再是全域變數）
window.lib = { S, api, go, render, selectAll, setSel, clearSel, pickTag, openViewer };
/* ============ 啟動 ============ */
S.mtype = "";
// 從手機／平板（區網）連進來：只看模式（整理的頁面、選取、編輯都藏起來；伺服器那邊也只給讀）
try { const me = await api("/api/m/me"); if (me.remote) { S.remote = true; document.body.classList.add("remote"); } } catch { }
api("/api/settings").then(s => { S.settings = s; window.zhcn?.sync(s.zh_mode); }).catch(() => { });   // 繁簡模式跟伺服器不一樣：重新載入
document.documentElement.style.setProperty("--size", S.size + "px");
applyNsfwUI();
loadSidebar();
// 事件流（b93）；手機（區網）沒開放，照舊輪詢。自動化的瀏覽器（畫面測試）預設不開：一直開著的連線會讓「等網路閒下來」永遠等不到；
// 網址帶 ?sse=1 才開（測試事件流本身用）
if (!S.remote && (!navigator.webdriver || new URLSearchParams(location.search).get("sse") === "1")) startEvents();
pollStatus();
route();
