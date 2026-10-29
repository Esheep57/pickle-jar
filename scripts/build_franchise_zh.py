"""Fate（Atlas Academy：台服／國服／日服／美服）、寶可夢人物（52poke）的官方中文名 → Danbooru 角色 tag。只用標準函式庫。
用法：python3 build_franchise.py <library.db> <out.json>"""
import json, re, sqlite3, sys, time, unicodedata, urllib.parse, urllib.request
UA = "PickleJarNames/1.0 (personal tool)"
db, out_p = sys.argv[1], sys.argv[2]
c = sqlite3.connect(db)
rows = c.execute("SELECT name, posts, other_names, copyright FROM char_catalog").fetchall()
lib = c.execute("""SELECT t.name, ti.other_names, ti.copyright FROM tags t LEFT JOIN tag_info ti ON ti.tag_id=t.id WHERE t.category='character'""").fetchall()
chars = {n: (json.loads(on or "[]"), cp) for n, _, on, cp in rows}
for n, on, cp in lib:
    chars.setdefault(n, (json.loads(on or "[]"), cp))
def norm(s): return re.sub(r"[\s・･·=＝\-_]+", "", unicodedata.normalize("NFKC", s or "")).lower()
def jp_core(s): return norm(re.sub(r"[（(〔\[][^）)〕\]]*[）)〕\]]", "", s or ""))
def get(url):
    for k in range(3):
        try:
            return json.load(urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": UA}), timeout=60))
        except Exception as e:
            print("ERR", url[:80], e); time.sleep(3)
    return None
out = {}
# ---------- Fate ----------
reg = {r: {s["collectionNo"]: s for s in get(f"https://api.atlasacademy.io/export/{r}/basic_servant.json") if s["type"] in ("normal", "heroine")}
       for r in ("JP", "NA", "CN", "TW")}
fate = {n: v for n, v in chars.items() if (v[1] or "").startswith("fate") or n.endswith("_(fate)") or "_(fate/" in n}
by_jp, by_jp2, by_en, by_en2 = {}, {}, {}, {}      # 完全一樣的優先；去掉〔オルタ〕(Saber) 的是第二順位
for no, s in reg["JP"].items():
    by_jp.setdefault(norm(s["name"]), []).append(no)
    by_jp2.setdefault(jp_core(s["name"]), []).append(no)
    if no in reg["NA"]:
        en = reg["NA"][no]["name"]
        by_en.setdefault(norm(en), []).append(no)
        by_en2.setdefault(norm(re.sub(r"\s*\([^)]*\)$", "", en)), []).append(no)
fhit = 0
for n, (on, cp) in fate.items():
    base = re.sub(r"_\(fate[^()]*\)$", "", n)
    ja = [a for a in on if re.search(r"[぀-ヿ一-鿿]", a)]
    cands = set()
    for a in ja:
        for k in (norm(a), norm(re.sub(r"\s*[（(][^（）()]*[）)]$", "", a))):
            cands |= set(by_jp.get(k, []))
    eb = norm(base.replace("_", " "))
    if not cands:
        cands |= set(by_en.get(eb, []))
    if not cands:
        for a in ja:
            cands |= set(by_jp2.get(jp_core(a), []))
    if not cands:
        cands |= set(by_en2.get(eb, []))
    if len(cands) > 1:     # 同名的從者（兩位迪盧木多、兩位吉爾·德·雷）：tag 有職階就照職階挑；中文名都一樣也可以
        cls = re.findall(r"_\(([a-z_]+)\)", n)
        by_cls = [no for no in cands if reg["JP"][no].get("className", "").lower() in cls]
        if len(by_cls) == 1:
            cands = set(by_cls)
        elif len({(reg["TW"].get(no, {}).get("name"), reg["CN"].get(no, {}).get("name")) for no in cands}) == 1:
            cands = {min(cands)}
    if len(cands) != 1:
        continue
    no = cands.pop()
    tw = reg["TW"].get(no, {}).get("name"); cn = reg["CN"].get(no, {}).get("name")
    if tw or cn:
        out[n] = {"tw": tw, "cn": cn, "src": f"fgo:{no}", "jp": reg["JP"][no]["name"]}
        fhit += 1
print("fate tags", len(fate), "matched", fhit)
# ---------- 寶可夢人物（52poke） ----------
pk = {n: v for n, v in chars.items() if (v[1] or "").startswith("pokemon") or n.endswith("_(pokemon)")}
def en_title(n):
    b = re.sub(r"_\([^()]*\)$", "", n)
    return " ".join(w[:1].upper() + w[1:] for w in b.split("_"))
todo = {}
for n, (on, cp) in pk.items():
    if n in out:
        continue
    todo.setdefault(en_title(n), []).append(n)
titles = list(todo)
phit = 0
again = {}
def batches():
    for i in range(0, len(titles), 50):
        yield titles[i:i + 50]
    if again:
        todo.update(again)
        t2 = list(again)
        for i in range(0, len(t2), 50):
            yield t2[i:i + 50]
for part in batches():
    q = urllib.parse.urlencode({"action": "query", "format": "json", "redirects": 1, "titles": "|".join(part),
                                "prop": "langlinks|categories", "lllang": "ja", "lllimit": "max", "cllimit": "max",
                                "clcategories": "Category:消歧义页面|Category:消歧義頁面|Category:Masters登場人物"})
    d = get("https://wiki.52poke.com/api.php?" + q) or {}
    qq = d.get("query", {})
    red = {r["from"]: r["to"] for r in qq.get("redirects", [])}
    norm_ = {r["from"]: r["to"] for r in qq.get("normalized", [])}
    pages = {p.get("title"): p for p in qq.get("pages", {}).values()}
    for t in part:
        t2 = norm_.get(t, t); dest = red.get(t2, t2)
        p = pages.get(dest)
        if not p or "missing" in p:
            continue
        cats = [x["title"] for x in p.get("categories", [])]
        if any("消歧义" in x or "消歧義" in x for x in cats):
            if not t.endswith(" (game)"):
                again[t + " (game)"] = todo[t]     # 赤紅：Red → 消歧義頁 → Red (game)
            continue
        ja = [l["*"] for l in p.get("langlinks", [])]
        if not ja:
            continue
        jac = jp_core(ja[0])
        for n in todo[t]:
            if re.match(r"^(.+)_\(([^()]+)\)_\(([^()]+)\)$", n):
                continue        # 服裝版本（marnie_(summer_2021)_(pokemon)）：交給造型規則（瑪俐（Summer 2021））
            on = chars[n][0]
            if any(jp_core(a) == jac for a in on if a):
                out[n] = {"zh": dest, "src": "52poke:" + dest, "jp": ja[0], "masters": any("Masters" in x for x in cats)}
                phit += 1
    time.sleep(1)
print("pokemon tags", len(pk), "titles", len(titles), "matched", phit)
json.dump(out, open(out_p, "w", encoding="utf-8"), ensure_ascii=False, indent=0)
print("done", len(out))
