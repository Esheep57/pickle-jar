"""Danbooru 角色表（b109）：把 Danbooru 上的角色（預設「至少 10 張圖」，約 10 萬個）下載到本機。

用途：加標籤時可以直接挑圖庫裡還沒有的角色（中文、日文、英文名都搜得到）；同作品的角色列得出來。
不影響 AI 認角色（那是模型自己的字彙）。

下載（線上佇列 kind 'charcat'，一次約 250 個請求、幾分鐘）：
  1. 作品（copyright）至少 10 張圖的：名字＋張數（作品熱度）
  2. 指到作品的別名（kancolle → kantai_collection）：認角色名字括號裡的作品用
  3. 角色：照張數排、到門檻為止
  4. 角色的維基別名（日文、中文、暱稱）：照張數排、整頁都不是要的角色就停
  5. 所屬作品：圖庫裡已經查過的 > 名字括號（mika_(blue_archive)）> 熱門角色抽樣貼文（kind 'charcat_cp'，一個角色一次請求，慢慢做）
Danbooru 沒有能一次下載的「角色 → 作品」資料，所以名字沒帶作品、又不熱門的角色，作品留白（真的加到圖上時，資料卡會查）。

中文名（照作品熱度，使用者 2026-10 決定）：圖庫裡有的作品、Danbooru 前 200 熱門作品裡的角色（角色表收的 ≥10 張全部），
排進 Bangumi 中文名查詢（zhname，背景、每個約 1 秒）；其他角色先用 Danbooru 別名裡的中文。
"""
from __future__ import annotations

import json
import logging
import re
import time
from collections import Counter

from . import db

log = logging.getLogger("charcat")
SCHEMA = """
CREATE TABLE IF NOT EXISTS char_catalog(
  name TEXT PRIMARY KEY,
  posts INTEGER,
  other_names TEXT,            -- JSON：Danbooru 維基的別名
  copyright TEXT,              -- 所屬作品（可能是空的）
  cp_src TEXT,                 -- library 圖庫查過的 | suffix 名字括號 | posts 抽樣貼文 | none 抽樣也沒有
  keys TEXT                    -- 搜尋用：名字、去括號的名字、別名（小寫、底線換空白），一行一個
);
CREATE INDEX IF NOT EXISTS idx_charcat_cp ON char_catalog(copyright);
CREATE TABLE IF NOT EXISTS copyright_catalog(name TEXT PRIMARY KEY, posts INTEGER);
CREATE TABLE IF NOT EXISTS catalog_meta(k TEXT PRIMARY KEY, v TEXT);
"""
TIERS = (1, 10, 100)            # 設定裡可以選的門檻（至少幾張圖）
CP_MIN = 10                     # 作品至少幾張圖才收（認括號、熱度用）
SAMPLE_MIN = 300                # 名字沒帶作品的角色：至少這麼多張圖才抽樣貼文查作品
ZH_TOP_WORKS = 200              # 中文名：Danbooru 前幾名熱門作品
ZH_TOP_MIN = 10                 # …裡至少幾張圖的角色（b110.5：50 → 10，角色表本來就只收 ≥10 張的）
ZH_LIB_MIN = 10                 # 圖庫裡有的作品：至少幾張圖的角色（b110.5：30 → 10）（使用者圖庫 883 部作品：≥10 張有 2 萬個、≥30 張 1.2 萬個）
ZH_CAP = 15000
REFRESH_DAYS = 30
_PAREN = re.compile(r"_\(([^()]+)\)$")
_ready = False


def _ensure():
    global _ready
    if not _ready:
        with db.write_lock:
            db.conn().executescript(SCHEMA)
        _ready = True


def _meta(k: str, default=None):
    _ensure()
    r = db.conn().execute("SELECT v FROM catalog_meta WHERE k=?", (k,)).fetchone()
    return json.loads(r[0]) if r else default


def _set_meta(**kv):
    with db.write_lock:
        db.conn().executemany("INSERT OR REPLACE INTO catalog_meta(k, v) VALUES(?, ?)",
                              [(k, json.dumps(v, ensure_ascii=False)) for k, v in kv.items()])


def norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").replace("_", " ")).strip().lower()


def _keys(name: str, other: list[str]) -> str:
    ks = [norm(name)]
    base = _PAREN.sub("", name)
    if base != name:
        ks.append(norm(base))
    ks += [norm(n) for n in other if n and len(n) <= 40]
    return "\n".join(dict.fromkeys(k for k in ks if k))


# ---------- 下載 ----------
def _pages(path: str, params: dict, stop, state: dict, label: str, until=None, max_pages: int = 1000):
    """一頁 1000 筆一直翻；until(這一頁) 回傳 True 就停。"""
    from .danbooru import dan_get
    for page in range(1, max_pages + 1):
        if stop():
            return
        state["phase"] = f"下載 Danbooru 角色表：{label}（第 {page} 頁）"
        rows = dan_get(path, {**params, "limit": 1000, "page": page})
        if not rows:
            return
        yield rows
        if until and until(rows):
            return


def download(state: dict, stop, min_posts: int = 10) -> dict:
    """整份重抓（之前抽樣查到的作品留著）。回傳 {characters, copyrights, ...}。"""
    _ensure()
    min_posts = max(1, int(min_posts))
    t0 = time.time()
    state.update(done=0, total=0)
    # 1. 作品
    cps: dict[str, int] = {}
    for rows in _pages("/tags.json", {"search[category]": 3, "search[order]": "count", "only": "name,post_count"}, stop, state,
                       "作品", until=lambda rows: rows[-1]["post_count"] < CP_MIN):
        cps.update({r["name"]: r["post_count"] for r in rows if r["post_count"] >= CP_MIN})
    # 2. 指到作品的別名（括號裡常用簡稱：kancolle、fate）
    alias: dict[str, str] = {}
    for rows in _pages("/tag_aliases.json", {"search[consequent_tag][category]": 3, "search[status]": "active",
                                              "only": "antecedent_name,consequent_name"}, stop, state, "作品別名"):
        alias.update({r["antecedent_name"]: r["consequent_name"] for r in rows})
    # 3. 角色
    chars: dict[str, int] = {}
    for rows in _pages("/tags.json", {"search[category]": 4, "search[order]": "count", "only": "name,post_count"}, stop, state,
                       "角色", until=lambda rows: rows[-1]["post_count"] < min_posts):
        chars.update({r["name"]: r["post_count"] for r in rows if r["post_count"] >= min_posts})
    if stop() or not chars:
        return {"stopped": True}
    # 4. 維基別名（照張數排：整頁都不是要的角色就停）
    other: dict[str, list[str]] = {}
    for rows in _pages("/wiki_pages.json", {"search[tag][category]": 4, "search[order]": "post_count", "only": "title,other_names"},
                       stop, state, "角色別名", until=lambda rows: not any(r["title"] in chars for r in rows)):
        for r in rows:
            if r["title"] in chars:
                other[r["title"]] = [n for n in (r.get("other_names") or []) if n][:30]
    if stop():
        return {"stopped": True}
    # 5. 所屬作品：圖庫查過的 > 名字括號 > 之前抽樣查到的
    c = db.conn()
    lib = {r[0]: r[1] for r in c.execute("""SELECT t.name, ti.copyright FROM tags t JOIN tag_info ti ON ti.tag_id=t.id
                                            WHERE t.category='character' AND ti.copyright IS NOT NULL""")}
    old = {r[0]: (r[1], r[2]) for r in c.execute("SELECT name, copyright, cp_src FROM char_catalog WHERE cp_src IN ('posts','none')")}
    rows = []
    for name, posts in chars.items():
        cp, src = lib.get(name), "library"
        if not cp:
            cp, src = suffix_copyright(name, cps, alias), "suffix"
        if not cp and name in old:
            cp, src = old[name]
        if not cp:
            src = None
        on = other.get(name, [])
        rows.append((name, posts, json.dumps(on, ensure_ascii=False), cp, src, _keys(name, on)))
    with db.write_lock:
        c.execute("DELETE FROM char_catalog")
        c.executemany("INSERT INTO char_catalog(name, posts, other_names, copyright, cp_src, keys) VALUES(?,?,?,?,?,?)", rows)
        c.execute("DELETE FROM copyright_catalog")
        c.executemany("INSERT INTO copyright_catalog(name, posts) VALUES(?,?)", list(cps.items()))
    out = {"characters": len(rows), "copyrights": len(cps), "with_copyright": sum(1 for r in rows if r[3]),
           "with_names": sum(1 for r in rows if r[2] != "[]"), "seconds": round(time.time() - t0)}
    _set_meta(updated_at=time.time(), min_posts=min_posts, stats=out)
    log.info("Danbooru 角色表：%s", out)
    return out


def suffix_copyright(name: str, cps: dict | set, alias: dict) -> str | None:
    """名字括號裡的作品：mika_(swimsuit)_(blue_archive) → blue_archive；kancolle → kantai_collection；fate → fate_(series)。"""
    m = _PAREN.search(name)
    if not m:
        return None
    q = m.group(1)
    for k in (q, alias.get(q), f"{q}_(series)", f"{q}_(game)", f"{q}_(franchise)"):
        if k and k in cps:
            return k
    return None


# ---------- 熱門角色抽樣貼文查作品（kind 'charcat_cp'） ----------
def cp_pending(limit: int = 100000) -> list[str]:
    _ensure()
    return [r[0] for r in db.conn().execute(
        "SELECT name FROM char_catalog WHERE copyright IS NULL AND cp_src IS NULL AND posts>=? ORDER BY posts DESC LIMIT ?",
        (SAMPLE_MIN, limit))]


def sample_copyrights(names_: list[str], stop) -> int:
    """每個角色看最近 30 篇貼文，最常一起出現的作品（original 也算）。"""
    from .danbooru import dan_get
    found = 0
    for name in names_:
        if stop():
            break
        try:
            posts = dan_get("/posts.json", {"tags": name, "limit": 30, "only": "tag_string_copyright"})
        except Exception as e:  # noqa  連不上：留著下次
            log.info("角色表查作品 %s 失敗：%s", name, e)
            continue
        cnt = Counter(cp for p in posts for cp in (p.get("tag_string_copyright") or "").split())
        cp = cnt.most_common(1)[0][0] if cnt else None
        with db.write_lock:
            db.conn().execute("UPDATE char_catalog SET copyright=?, cp_src=? WHERE name=?", (cp, "posts" if cp else "none", name))
        found += bool(cp)
    return found


# ---------- 中文名：照作品熱度 ----------
def zh_targets(limit: int = ZH_CAP) -> list[str]:
    """要查中文名的角色（圖庫裡還沒有的；圖庫裡有的照舊由 zhname.pending 查）：
    圖庫裡有的作品的角色先，再來 Danbooru 前 200 熱門作品的角色；各自照張數排。"""
    _ensure()
    c = db.conn()
    lib_works = {r[0] for r in c.execute("""SELECT DISTINCT t.name FROM tags t JOIN media_tags x ON x.tag_id=t.id AND x.state=1
                                            WHERE t.category='copyright'""")}
    lib_works |= {r[0] for r in c.execute("""SELECT DISTINCT ti.copyright FROM tags t JOIN tag_info ti ON ti.tag_id=t.id
            WHERE t.category='character' AND ti.copyright IS NOT NULL AND EXISTS (SELECT 1 FROM media_tags x WHERE x.tag_id=t.id AND x.state=1)""")}
    top = {r[0] for r in c.execute("SELECT name FROM copyright_catalog ORDER BY posts DESC LIMIT ?", (ZH_TOP_WORKS,))}
    # 圖庫裡有確認圖的（zhname.pending 會查）；只是建過標籤、沒有圖的（加標籤選單點過等）還是要在這裡查
    have = {r[0] for r in c.execute("""SELECT DISTINCT t.name FROM tags t JOIN media_tags x ON x.tag_id=t.id AND x.state=1
                                       WHERE t.category='character'""")}
    done = {r[0] for r in c.execute("SELECT name FROM name_zh")} if _has_table("name_zh") else set()
    from . import names
    cur = set(names._curated())
    a, b = [], []
    for name, posts, cp in c.execute("SELECT name, posts, copyright FROM char_catalog WHERE copyright IS NOT NULL ORDER BY posts DESC"):
        if name in have or name in done or name in cur:
            continue
        if cp in lib_works and posts >= ZH_LIB_MIN:
            a.append(name)
        elif cp in top and posts >= ZH_TOP_MIN:
            b.append(name)
    return (a + b)[:limit]


def _has_table(t: str) -> bool:
    return bool(db.conn().execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (t,)).fetchone())


def queue_followups() -> dict:
    """下載完（或啟動時）：造型套本體的中文名（不連網）；熱門角色查作品、照作品熱度查中文名，排進背景。"""
    from . import jobqueue, online, zhname
    try:
        zhname._CV["at"] = 0
        zhname.refresh_variants()
    except Exception as e:  # noqa
        log.warning("角色表造型中文名失敗：%s", e)
    s = db.get_settings()
    if not s.get("online_enabled"):
        return {"cp": 0, "zh": 0}
    q = {n for r in online.get_jobs().q.rows() if r["kind"] in ("charcat_cp", "zhname") and isinstance(r["arg"], list) for n in r["arg"]}
    cp = [n for n in cp_pending() if n not in q]
    if cp:
        online.get_jobs().submit("charcat_cp", cp, jobqueue.BACKGROUND)
    zh = []
    want = set(zh_targets()) if s.get("charcat_zh", True) else set()
    # 已經排著、但現在不在名單裡的角色表角色（門檻改了、關掉了）：整組拿掉（圖庫裡的角色不動）
    j = online.get_jobs()
    have = {r[0] for r in db.conn().execute("SELECT name FROM tags")}
    for g in j.q.groups():
        if g["kind"] != "zhname":
            continue
        names_ = [n for r in j.q.group_rows(g["grp"]) if isinstance(r["arg"], list) for n in r["arg"]]
        if names_ and all(n not in have and n not in want for n in names_):
            for r in j.q.cancel_group(g["grp"]):
                j._forget(r["kind"], r["arg"] if isinstance(r["arg"], list) else [])
    q = {n for r in j.q.rows() if r["kind"] in ("charcat_cp", "zhname") and isinstance(r["arg"], list) for n in r["arg"]}
    if want:
        zh = [n for n in zh_targets() if n not in q]
        if zh:
            j.submit("zhname", zh, jobqueue.BACKGROUND)
    return {"cp": len(cp), "zh": len(zh)}


# ---------- 給其他地方用 ----------
def info(name: str) -> dict | None:
    """角色表裡的一筆：{posts, other_names, copyright}（沒下載、沒有這個角色 → None）。"""
    if not _has_table("char_catalog"):
        return None
    r = db.conn().execute("SELECT posts, other_names, copyright FROM char_catalog WHERE name=?", (name,)).fetchone()
    return {"posts": r[0], "other_names": json.loads(r[1] or "[]"), "copyright": r[2]} if r else None


def search(q: str, limit: int = 12, exclude: set | None = None) -> list[dict]:
    """名字、別名（含簡繁、中文譯名）包含 q 的角色，張數多的先。"""
    if not q or not _has_table("char_catalog"):
        return []
    from . import nameindex
    c = db.conn()
    vs = [v for v in nameindex.variants(q) if v][:4]
    if not vs:
        return []
    like = lambda v: "%" + v.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
    hit: dict[str, int] = {}
    w = " OR ".join("keys LIKE ? ESCAPE '\\'" for _ in vs)
    for name, posts in c.execute(f"SELECT name, posts FROM char_catalog WHERE {w} ORDER BY posts DESC LIMIT ?",
                                 [*map(like, vs), limit * 3]):
        hit[name] = posts
    if _has_table("name_zh") and any(nameindex.CJK.search(v) for v in vs):      # Bangumi 查到的中文名
        w2 = " OR ".join("z.zh LIKE ? ESCAPE '\\' OR z.raw LIKE ? ESCAPE '\\'" for _ in vs)
        for name, posts in c.execute(f"""SELECT k.name, k.posts FROM name_zh z JOIN char_catalog k ON k.name=z.name
                                         WHERE {w2} ORDER BY k.posts DESC LIMIT ?""", [x for v in vs for x in (like(v), like(v))] + [limit * 3]):
            hit[name] = posts
    exclude = exclude or set()
    names_ = [n for n, _ in sorted(hit.items(), key=lambda kv: -kv[1]) if n not in exclude][:limit]
    return rows(names_)


def mates(series: set[str], exclude: set[str], limit: int = 8) -> list[dict]:
    """同作品、圖庫裡還沒有的角色（熱門的先）。"""
    if not series or not _has_table("char_catalog"):
        return []
    sp = ",".join("?" * len(series))
    got = [r[0] for r in db.conn().execute(f"SELECT name FROM char_catalog WHERE copyright IN ({sp}) ORDER BY posts DESC LIMIT ?",
                                           [*series, limit + len(exclude)])]
    return rows([n for n in got if n not in exclude][:limit])


def rows(names_: list[str]) -> list[dict]:
    """加標籤選單用的格式（跟 /api/tags 一樣），catalog＝True。"""
    if not names_:
        return []
    from . import names
    lang = db.get_settings()["display_lang"]
    ph = ",".join("?" * len(names_))
    got = {r[0]: r for r in db.conn().execute(f"SELECT name, posts, other_names, copyright FROM char_catalog WHERE name IN ({ph})", names_)}
    out = []
    for n in names_:
        r = got.get(n)
        if not r:
            continue
        on = json.loads(r[2] or "[]")
        out.append({"name": n, "category": "character", "pinned": 0, "copyright": r[3], "alias": None, "zh": None, "n": None,
                    "posts": r[1], "catalog": True, "display": names.display(n, on, None, lang)})
    return out


def status() -> dict:
    _ensure()
    c = db.conn()
    n = c.execute("SELECT COUNT(*) FROM char_catalog").fetchone()[0]
    out = {"count": n, "updated_at": _meta("updated_at"), "min_posts": _meta("min_posts"), "stats": _meta("stats")}
    if n:
        out["with_copyright"] = c.execute("SELECT COUNT(*) FROM char_catalog WHERE copyright IS NOT NULL").fetchone()[0]
        out["cp_pending"] = len(cp_pending())
        if _has_table("name_zh"):
            out["zh_done"] = c.execute("""SELECT COUNT(*) FROM name_zh z JOIN char_catalog k ON k.name=z.name
                                          WHERE z.status IN ('found','kept','variant')""").fetchone()[0]
        out["zh_pending"] = len(zh_targets())
    return out


def clear():
    _ensure()
    with db.write_lock:
        c = db.conn()
        c.execute("DELETE FROM char_catalog")
        c.execute("DELETE FROM copyright_catalog")
        c.execute("DELETE FROM catalog_meta")


def refresh_due() -> bool:
    """下載過、超過 30 天：啟動時自動更新一次（只抓新的門檻同樣的整份）。"""
    at = _meta("updated_at")
    return bool(at) and time.time() - at > REFRESH_DAYS * 86400
