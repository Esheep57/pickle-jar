// 簡體模式（設定「中文：繁體／简体」）：把畫面上的文字換成大陸用語＋簡體字。
// 一般的 script（不是 module），放在 <head>、字典（zhcn-dict.js）後面：在畫面畫出來之前就開始轉，不會先閃一下繁體。
// 只改「看得到的字」（文字、title、placeholder…），不改輸入框的值、不改送回伺服器的資料；角色／作品名稱由伺服器給大陸譯名。
// 模式記在 localStorage（開頁面時馬上知道）；main.js 拿到伺服器設定後對一次，不一樣就重新整理。
(function () {
  "use strict";
  var KEY = "pj_zh_mode";
  var mode = "tw";
  try { mode = localStorage.getItem(KEY) === "cn" ? "cn" : "tw"; } catch (e) { /* 私密視窗、被擋：照繁體 */ }
  var api = {
    mode: mode,
    // 伺服器設定（S.settings.zh_mode）跟記住的不一樣：記下來、重新載入（已經轉過的字沒辦法轉回去）
    sync: function (m) {
      m = m === "cn" ? "cn" : "tw";
      if (m === mode) return false;
      try { localStorage.setItem(KEY, m); } catch (e) { return false; }
      location.reload();
      return true;
    },
    conv: function (s) { return s; },
  };
  window.zhcn = api;
  if (mode !== "cn" || !window.ZHCN_DICT) return;

  var TW = window.ZHCN_DICT.TW, TS = window.ZHCN_DICT.TS;
  var maxLen = function (d) { var m = 1; for (var k in d) if (k.length > m) m = k.length; return m; };
  var TWMAX = maxLen(TW), TSMAX = maxLen(TS);
  var HAN = /[㐀-鿿豈-﫿]/;
  // 最長比對：從每個位置試最長的詞，對不到就一個字一個字
  function pass(s, d, mx) {
    var out = "", i = 0, n = s.length;
    while (i < n) {
      var hit = null, len = 0;
      for (var L = Math.min(mx, n - i); L >= 1; L--) {
        var w = s.substr(i, L);
        if (Object.prototype.hasOwnProperty.call(d, w)) { hit = d[w]; len = L; break; }
      }
      if (hit === null) { out += s[i]; i++; } else { out += hit; i += len; }
    }
    return out;
  }
  var cache = new Map();
  function conv(s) {
    if (!s || !HAN.test(s)) return s;
    var r = cache.get(s);
    if (r !== undefined) return r;
    r = pass(pass(s, TW, TWMAX), TS, TSMAX);
    if (cache.size > 20000) cache.clear();
    cache.set(s, r);
    return r;
  }
  api.conv = conv;

  var ATTRS = ["title", "placeholder", "aria-label", "data-tip", "alt", "label"];
  var SKIP = { SCRIPT: 1, STYLE: 1, TEXTAREA: 1, CODE: 1, PRE: 1, NOSCRIPT: 1 };
  function skipEl(el) {
    // .nocv／translate="no"：資料夾路徑、檔名這類要照原樣顯示的
    return !el || SKIP[el.nodeName] || el.isContentEditable || (el.closest && el.closest(".nocv,[translate=no]"));
  }
  var done = new WeakMap();      // 自己改過的：改完會再收到一次通知，同樣的字不要再轉一次
  function doText(t) {
    var v = t.data;
    if (done.get(t) === v || !HAN.test(v) || skipEl(t.parentElement)) return;
    var r = conv(v);
    done.set(t, r);
    if (r !== v) t.data = r;
  }
  function doAttrs(el) {
    for (var i = 0; i < ATTRS.length; i++) {
      var a = ATTRS[i], v = el.getAttribute(a);
      if (v && HAN.test(v)) { var r = conv(v); if (r !== v && conv(r) === r) el.setAttribute(a, r); }
    }
    if (el.nodeName === "INPUT" && (el.type === "button" || el.type === "submit") && HAN.test(el.value)) el.value = conv(el.value);
  }
  function walk(root) {
    if (root.nodeType === 3) { doText(root); return; }
    if (root.nodeType !== 1 && root.nodeType !== 11) return;
    if (root.nodeType === 1) { if (skipEl(root)) return; doAttrs(root); }
    var it = document.createTreeWalker(root, 5 /* 元素＋文字 */, {
      acceptNode: function (n) { return n.nodeType === 1 && skipEl(n) ? 2 /* 整段跳過 */ : 1; },
    });
    var n;
    while ((n = it.nextNode())) { if (n.nodeType === 3) doText(n); else doAttrs(n); }
  }
  var mo = new MutationObserver(function (list) {
    for (var i = 0; i < list.length; i++) {
      var m = list[i];
      if (m.type === "childList") { for (var j = 0; j < m.addedNodes.length; j++) walk(m.addedNodes[j]); }
      else if (m.type === "characterData") doText(m.target);
      else if (m.type === "attributes" && !skipEl(m.target)) doAttrs(m.target);
    }
  });
  var root = document.documentElement;
  root.setAttribute("lang", "zh-Hans");
  root.classList.add("zh-cn");
  mo.observe(root, { childList: true, subtree: true, characterData: true, attributes: true, attributeFilter: ATTRS.concat(["value"]) });
  walk(root);
  // <title> 也換（分頁標題）
  if (document.title) document.title = conv(document.title);
})();
