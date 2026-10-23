// 前端模組檢查（開發用，需要 Node）：cd scripts && npm install && node jscheck.js
// 模組化後的檢查：每個檔案沒有未宣告（也沒 import）的名字、沒有重新指派 import 進來的名字
const acorn=require('acorn'),escope=require('eslint-scope'),fs=require('fs');
const dir=process.argv[2]||require('path').join(__dirname,'..','static','js'); let bad=0;
const G=new Set(Object.getOwnPropertyNames(globalThis).concat(["window","document","location","navigator","localStorage","sessionStorage","fetch","history","getComputedStyle","requestAnimationFrame","cancelAnimationFrame","IntersectionObserver","ResizeObserver","MutationObserver","Image","Event","CustomEvent","HTMLElement","Node","FormData","Blob","File","FileReader","URL","URLSearchParams","AbortController","alert","confirm","prompt","innerWidth","innerHeight","devicePixelRatio","matchMedia","performance","DOMParser","DataTransfer","Option","screen","open","TextDecoder","TextEncoder","structuredClone","queueMicrotask","CSS","getSelection","HTMLInputElement","HTMLTextAreaElement","console","setTimeout","clearTimeout","setInterval","clearInterval","crypto","ClipboardItem","atob","btoa","scrollY","scrollX","createImageBitmap","KeyboardEvent","navigation","visualViewport"]));
for(const f of fs.readdirSync(dir).filter(x=>x.endsWith('.js'))){
  const ast=acorn.parse(fs.readFileSync(dir+'/'+f,'utf8'),{ecmaVersion:2023,sourceType:'module',ranges:true});
  const sm=escope.analyze(ast,{ecmaVersion:2022,sourceType:'module'});
  const mod=sm.scopes.find(s=>s.type==='module');
  for(const r of mod.through){ if(!G.has(r.identifier.name)){bad++;console.log(f,'undefined:',r.identifier.name);} }
  for(const v of mod.variables){ if(v.defs.some(d=>d.type==='ImportBinding') && v.references.some(r=>r.isWrite())){bad++;console.log(f,'writes import:',v.name);} }
}
console.log(bad?`FAIL ${bad}`:'OK');process.exit(bad?1:0);
