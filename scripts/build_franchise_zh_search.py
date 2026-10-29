"""寶可夢人物：英文名沒有導向頁的（新角色），用 52poke 站內搜尋：內文寫「X（日文︰ユカリ，英文︰Jacinthe）」，日文、英文都對上才收。"""
import json, re, sqlite3, sys, time, unicodedata, urllib.parse, urllib.request
UA = "PickleJarNames/1.0 (personal tool)"
db, prev_p, out_p, budget = sys.argv[1], sys.argv[2], sys.argv[3], float(sys.argv[4])
c = sqlite3.connect(db)
prev = json.load(open(prev_p, encoding="utf-8"))
try: out = json.load(open(out_p, encoding="utf-8"))
except Exception: out = {}
def norm(s): return re.sub(r"[\s・･·=＝\-_]+", "", unicodedata.normalize("NFKC", s or "")).lower()
def jp_core(s): return norm(re.sub(r"[（(〔\[][^）)〕\]]*[）)〕\]]", "", s or ""))
rows = c.execute("SELECT name, other_names, copyright FROM char_catalog").fetchall()
rows += c.execute("SELECT t.name, ti.other_names, ti.copyright FROM tags t LEFT JOIN tag_info ti ON ti.tag_id=t.id WHERE t.category='character'").fetchall()
seen = set(); todo = []
for n, on, cp in rows:
    if n in seen or n in prev or n in out: continue
    seen.add(n)
    if not ((cp or "").startswith("pokemon") or n.endswith("_(pokemon)")): continue
    if re.match(r"^(.+)_\(([^()]+)\)_\(([^()]+)\)$", n): continue
    ja = [a for a in json.loads(on or "[]") if re.search(r"[぀-ヿ]", a or "")]
    if not ja: continue
    en = " ".join(w[:1].upper() + w[1:] for w in re.sub(r"_\([^()]*\)$", "", n).split("_"))
    todo.append((n, en, {jp_core(a) for a in ja}))
print("todo", len(todo), flush=True)
t0 = time.time(); k = 0
for n, en, jas in todo:
    if time.time() - t0 > budget: break
    q = urllib.parse.urlencode({"action": "query", "format": "json", "list": "search", "srsearch": f'"{en}"', "srlimit": 10})
    try:
        d = json.load(urllib.request.urlopen(urllib.request.Request("https://wiki.52poke.com/api.php?" + q, headers={"User-Agent": UA}), timeout=30))
    except Exception as e:
        print("ERR", e); time.sleep(3); continue
    hit = None
    for r in d.get("query", {}).get("search", []):
        sn = re.sub(r"<[^>]+>", "", r.get("snippet", ""))
        m = re.search(r"（日文[︰:]([^，,）]+)[，,]\s*英文[︰:]([^，,）]+)", sn)
        if m and jp_core(m.group(1)) in jas and norm(m.group(2)) == norm(en):
            t = re.sub(r"（[^（）]*）$", "", r["title"]).strip()      # 由紫（TCG）→ 由紫
            if not hit or len(t) < len(hit): hit = t
    out[n] = {"zh": hit, "src": "52poke-search:" + (hit or ""), "en": en} if hit else {"zh": None}
    k += 1
    if hit: print(n, "->", hit, flush=True)
    time.sleep(0.5)
json.dump(out, open(out_p, "w", encoding="utf-8"), ensure_ascii=False, indent=0)
print("done", k, "found", sum(1 for v in out.values() if v.get("zh")), "left", len(todo) - k)
