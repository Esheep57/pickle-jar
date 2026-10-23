"""樣式表的防呆（2026-10-07 整理成一份之後）：

- 每條規則都在某一段（檔頭說明的分段）裡；同一個選擇器只寫一次（少數幾條因為先後順序必須分開，列在 SPLIT_OK）。
- 樣式表裡每個 class／id 都要有程式在用（舊版留下來沒人用的規則不要留著）。
- 新的 class 名稱不能跟別的畫面撞名：同一個 class 被兩個功能檔建立，就要確定是同一個意思（列在 SHARED），
  不然就換名字。b69.1（.cv）、b70.1（.mosaic／.sugrow／.dcard）都是這樣出錯的。
- !important 不能再變多（要蓋過 JS 設的行內樣式、或權重比較高的舊規則才用；能改選擇器就改選擇器）。
"""
import glob
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CSS = os.path.join(ROOT, "static", "style.css")
JS = sorted(glob.glob(os.path.join(ROOT, "static", "js", "*.js")))
HTML = os.path.join(ROOT, "static", "index.html")

IMPORTANT_MAX = 300            # 2026-10-14 b76（h1 改基礎規則後）；只能變少
# 必須分成兩處的選擇器（權重一樣、會套到同一個元素的別條規則夾在中間；合併會改變結果）
SPLIT_OK = {
    "#viewer .close", "#viewer .nav", ".btn", ".chip .cat", "body.rail .railbtn",
}
# 好幾個功能檔都會建立、而且是同一個意思的 class（共用元件、狀態）
SHARED = set("""acc actions alias asbox ashead asstats av avrow bad badge big btn cap card cards cat chip chip2 chiprow chips
chkl clist cp cpick danger danwarn diag empty fgrp field flbl form ghost grp-title headmain hidden hint info jgrid lnk lnkb mbody
mfoot mono more moreq mute nm note numw okc pa pagetabs pin pmrow popmenu primary pwarn qbox qic qrow2 qtb qtok right ring sc
scope sctl sech seg sel slabel sm snote src srow st steps sub switch tabs vg warn warnc why""".split())
# 程式用字串組出來的 class（sb-${來源}、f${i}…）
DYNAMIC = re.compile(r"^(sb-|st-|r-|rm-|k-)|^(f|s)-?\d+$")


def _rules():
    """回傳 [(段落, 條件, 選擇器清單, 宣告)]"""
    src = open(CSS, encoding="utf-8").read()
    tok = re.compile(r"/\*\s*(=+|-{4})\s*(.*?)\s*(?:=+|-{4})?\s*\*/|/\*[\s\S]*?\*/|((?:(?!/\*)[^{}])+)\{|\}")
    out, stack, sec, pos = [], [], None, 0
    while True:
        m = tok.search(src, pos)
        if not m:
            break
        pos = m.end()
        if m.group(1):
            sec = re.split(r"[：（]", m.group(2))[0].strip()
            continue
        if m.group(0).startswith("/*"):
            continue
        if m.group(3) is not None:
            head = m.group(3).strip()
            if head.startswith("@keyframes"):
                depth = 1
                while depth:
                    c = src[pos]; depth += c == "{"; depth -= c == "}"; pos += 1
                continue
            if head.startswith("@"):
                stack.append(head); continue
            end = src.index("}", pos)
            out.append((sec, stack[-1] if stack else None, [s.strip() for s in _split(head)], src[pos:end]))
            pos = end + 1
            continue
        if stack:
            stack.pop()
    return out


def _split(sel):
    out, depth, cur = [], 0, ""
    for ch in sel:
        depth += ch in "(["; depth -= ch in ")]"
        if ch == "," and depth == 0:
            out.append(cur); cur = ""
        else:
            cur += ch
    return [x for x in out + [cur] if x.strip()]


def _norm(s):
    return re.sub(r"\s+", " ", s.strip())


def test_css_is_parsable_and_every_rule_has_a_section():
    src = open(CSS, encoding="utf-8").read()
    no_comments = re.sub(r"/\*[\s\S]*?\*/", "", src)
    assert no_comments.count("{") == no_comments.count("}"), "大括號沒有成對"
    rules = _rules()
    assert len(rules) > 1000
    assert all(sec for sec, *_ in rules), "每條規則都要在某一段裡（檔案開頭說明的分段）"


def test_each_selector_is_written_once():
    seen, dup = {}, set()
    for sec, ctx, sels, _ in _rules():
        for s in sels:
            k = (ctx, _norm(s))
            if k in seen:
                dup.add(_norm(s))
            seen[k] = sec
    new = dup - SPLIT_OK
    assert not new, f"同一個選擇器寫了兩次（加在原本那條就好）：{sorted(new)}"


def test_every_class_and_id_is_used():
    src = "\n".join(open(f, encoding="utf-8").read() for f in JS + [HTML])
    body = re.sub(r"/\*[\s\S]*?\*/", "", open(CSS, encoding="utf-8").read())
    body = re.sub(r"\{[^{}]*\}", "{}", body)                 # 只看選擇器
    toks = set(re.findall(r"[.#]([A-Za-z_][\w-]*)", body))
    unused = sorted(t for t in toks if not DYNAMIC.search(t)
                    and not re.search(r"(?<![\w-])" + re.escape(t) + r"(?![\w-])", src))
    assert not unused, f"樣式表裡有程式沒在用的 class／id（舊版留下的就刪掉）：{unused}"


def test_class_names_do_not_collide():
    """同一個 class 被兩個功能檔建立：要嘛是共用的同一個東西（加進 SHARED），要嘛換個名字。"""
    css = re.sub(r"\{[^{}]*\}", "{}", re.sub(r"/\*[\s\S]*?\*/", "", open(CSS, encoding="utf-8").read()))
    css_cls = set(re.findall(r"\.([A-Za-z_][\w-]*)", css))
    made = {}
    for f in JS:
        s = open(f, encoding="utf-8").read()
        name = os.path.basename(f)
        if name in ("core.js", "shell.js", "main.js"):
            continue
        for m in re.finditer(r'class="((?:[^"$]|\$\{(?:[^{}]|\{[^{}]*\})*\})*)"', s):
            for w in re.findall(r"[A-Za-z_][\w-]*", re.sub(r"\$\{(?:[^{}]|\{[^{}]*\})*\}", " ", m.group(1))):
                made.setdefault(w, set()).add(name)
        for m in re.finditer(r'className\s*=\s*"([^"]*)"', s):
            for w in m.group(1).split():
                made.setdefault(w, set()).add(name)
    clash = {c: sorted(fs) for c, fs in made.items() if len(fs) >= 2 and c in css_cls and c not in SHARED}
    assert not clash, ("這些 class 在不同功能檔都有建立、樣式會互相影響：換一個沒人用過的名字，"
                       f"或確定是同一個東西後加進 SHARED：{clash}")


def test_important_does_not_grow():
    n = len(re.findall(r"!important", re.sub(r"/\*[\s\S]*?\*/", "", open(CSS, encoding="utf-8").read())))
    assert n <= IMPORTANT_MAX, f"!important 變多了（{n} > {IMPORTANT_MAX}）：先試著改選擇器、或把被蓋掉的舊規則刪掉"
