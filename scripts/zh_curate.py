"""譯名校對表（app/data/zh_curated.json）的維護工具（開發用；給下一次校對的人或 Claude 用）。

  python scripts/zh_curate.py export 資料庫 輸出.tsv
      列出「有確認圖、但還沒在校對表裡」的角色和作品（依作品分組，圖多的先）：
      tag、目前顯示的名字、來源、張數、Danbooru 的中日文別名。拿去校對。

  python scripts/zh_curate.py merge 校對好的.tsv 資料庫
      每行「tag<TAB>名字」：加進校對表（名字留空＝從表裡拿掉）；資料庫用來查是角色還是作品。

不用開程式；資料庫請先複製一份（程式開著時直接讀會鎖住）。
"""
from __future__ import annotations

import json
import re
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TABLE = ROOT / "app" / "data" / "zh_curated.json"
CJK = re.compile(r"[぀-ヿ㐀-鿿]")


def _load() -> dict:
    return json.loads(TABLE.read_text("utf-8"))


def export(db: str, out: str) -> None:
    sys.path.insert(0, str(ROOT))
    from app import names
    cur = names._curated()
    c = sqlite3.connect(db)
    rows = c.execute("""SELECT t.name, t.category, ti.other_names, ti.display_name, ti.copyright, COUNT(*)
        FROM tags t JOIN media_tags x ON x.tag_id=t.id AND x.state=1 LEFT JOIN tag_info ti ON ti.tag_id=t.id
        WHERE t.category IN ('character','copyright') GROUP BY t.id""").fetchall()
    existing = {r[0] for r in rows if r[1] == "character"}
    groups = defaultdict(list)
    for name, cat, on, dn, series, n in rows:
        if dn or name in cur:
            continue
        on = json.loads(on or "[]")
        if cat == "character" and (names.pokemon_zh(name, on) or names.base_of(name, existing)):
            continue        # 寶可夢官方譯名、造型（跟著本體）不用看
        disp = names.series_display(name, on, None, "zh") if cat == "copyright" else names.display(name, on, None, "zh")
        al = " / ".join([a for a in on if CJK.search(a)][:5])
        groups[(cat, series or "")].append((n, f"{name}\t{disp}\t{cat}\t{n}\t{al}"))
    lines = []
    for (cat, series), items in sorted(groups.items(), key=lambda kv: -sum(n for n, _ in kv[1])):
        lines.append(f"## {cat} {series}")
        lines += [s for _, s in sorted(items, key=lambda t: -t[0])]
    Path(out).write_text("\n".join(lines) + "\n", "utf-8")
    print(f"{sum(len(v) for v in groups.values())} 個還沒校對 → {out}")


def merge(tsv: str, db: str) -> None:
    d = _load()
    cat_of = dict(sqlite3.connect(db).execute("SELECT name, category FROM tags WHERE category IN ('character','copyright')"))
    n = 0
    for line in Path(tsv).read_text("utf-8").splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        tag, _, zh = line.partition("\t")
        cat = cat_of.get(tag.strip())
        if not cat:
            print("找不到 tag：", tag)
            continue
        if zh.strip():
            d[cat][tag.strip()] = zh.strip()
        else:
            d[cat].pop(tag.strip(), None)
        n += 1
    for k in ("character", "copyright"):
        d[k] = dict(sorted(d[k].items()))
    TABLE.write_text(json.dumps(d, ensure_ascii=False, indent=0), "utf-8")
    print(f"更新 {n} 個")


if __name__ == "__main__":
    if len(sys.argv) >= 4 and sys.argv[1] == "export":
        export(sys.argv[2], sys.argv[3])
    elif len(sys.argv) >= 4 and sys.argv[1] == "merge":
        merge(sys.argv[2], sys.argv[3])
    else:
        print(__doc__)
