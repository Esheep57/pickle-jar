"""中文名（Bangumi 番組計畫）：Danbooru 沒有中文別名、或中文別名是暱稱／錯的角色和作品，到 Bangumi 找中文名。

為什麼要另外找（使用者圖庫，2026-10-01）：有確認圖的角色 1,779 位裡 713 位顯示成日文或英文；
有中文的也常常是 Danbooru 別名裡的暱稱或錯字（常闇トワ →「常盤香菜」、沙花叉クロヱ →「飼育日記」、湊あくあ →「阿夸」）。
萌娘百科的 API 擋程式呼叫（Unauthorized API call），所以只用 Bangumi 的公開 API（api.bgm.tv）。

怎麼對（寧可查不到，也不要查錯）：
- 角色：Danbooru 的日文名（去掉括號）到 Bangumi 搜，**名字完全一樣**（全形半形、空白、・不計）才算
  - 只有一個、名字夠長（≥4 字或有・）→ 採用
  - 名字很短（ビオラ、リン）或同名好幾個 → 看 Bangumi 上這個角色出現在哪些作品，要跟你圖庫裡這個角色的作品對得上
  - 對不上、或好幾個都對得上而且譯名不同 → 不自動用，列成候選讓你挑
- 作品：日文名／英文名到 Bangumi 搜作品，名字完全一樣才用（內建台灣譯名表、維基百科台灣譯名優先）
- 中文名是簡體 → 轉成繁體台灣用字（OpenCC s2twp）
- 造型（minato_aqua_(1st_costume)、mika_(swimsuit)_(blue_archive)）：本體的名字＋造型（湊阿庫婭（初始服裝））

顯示順序（names.display）：你取的名字 > 寶可夢官方譯名 > Bangumi > Danbooru 中文別名 > 日文。
使用者選擇「新來源優先、列出來給我看」：換掉的在「中文名整理 › 自動換的」看得到，可以一鍵改回。
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
import unicodedata

from . import db, names

log = logging.getLogger("zhname")
BGM = os.environ.get("LIB_BANGUMI", "https://api.bgm.tv")
RETRY_DAYS = 60
SCHEMA = """CREATE TABLE IF NOT EXISTS name_zh(
  name TEXT PRIMARY KEY,       -- tag 原名
  category TEXT,
  status TEXT,                 -- found 找到 | none 沒有 | ambiguous 好幾個（options 讓你挑）| variant 造型（本體＋造型）| error
  zh TEXT,                     -- 顯示用（繁體）
  raw TEXT,                    -- Bangumi 上的原文（簡體）
  ref TEXT,                    -- bgm:character/123、bgm:subject/45
  options TEXT,                -- ambiguous：[{zh, ref, subjects}]
  prev TEXT,                   -- 找到之前顯示的名字（Danbooru 中文別名或日文），「改回」用
  seen INTEGER DEFAULT 0,      -- 你在「中文名整理 › 自動換的」看過了
  checked_at REAL)"""


# ---------- 給 names.display 用的對照表（記憶體快取，寫入後 reload） ----------
_MAP: dict | None = None
_MAP_LOCK = threading.Lock()


def auto_map() -> dict:
    global _MAP
    m = _MAP
    if m is None:
        with _MAP_LOCK:
            if _MAP is None:
                try:
                    _MAP = {r[0]: r[1] for r in db.conn().execute(
                        "SELECT name, zh FROM name_zh WHERE status IN ('found','variant') AND zh IS NOT NULL")}
                except Exception:  # noqa  資料表還沒建（遷移前）
                    return {}
            m = _MAP
    return m


_MAP_CN: dict | None = None


def auto_map_cn() -> dict:
    """簡體模式用：Bangumi 的原文（大陸譯名，本來就是簡體）；造型（本體＋造型）把顯示用的名字轉簡體字。"""
    global _MAP_CN
    m = _MAP_CN
    if m is None:
        with _MAP_LOCK:
            if _MAP_CN is None:
                try:
                    rows = db.conn().execute("""SELECT name, status, zh, raw FROM name_zh
                        WHERE status IN ('found','variant') AND zh IS NOT NULL""").fetchall()
                except Exception:  # noqa
                    return {}
                _MAP_CN = {r[0]: names.to_simp(r[3] if r[1] == "found" and r[3] else r[2]) for r in rows}
            m = _MAP_CN
    return m


def reload():
    global _MAP, _MAP_CN
    _MAP = None
    _MAP_CN = None
    try:
        from . import nameindex
        nameindex.invalidate()
    except Exception:  # noqa
        pass


# ---------- 名字比對 ----------
_PUNCT = re.compile(r"[\s・･·•.,，、:：;；!！?？~〜～\-－—_'’\"“”「」『』【】\[\]()（）/／☆★♪&＆+＋]+")


def norm(s: str) -> str:
    """比對用：全形半形、大小寫、標點都不計；拉丁字母去掉重音（Pokémon = Pokemon）；× 當 x（SPY×FAMILY）。"""
    s = unicodedata.normalize("NFKD", (s or "").replace("×", "x"))
    s = unicodedata.normalize("NFKC", re.sub(r"[\u0300-\u036f]", "", s))
    return _PUNCT.sub("", s).lower()


def _simp(s: str) -> str:
    st = names._st()
    return st[1].convert(s) if st and s else (s or "")


def _paren_split(n: str) -> tuple[str, str | None]:
    m = re.match(r"^(.+?)\s*[（(]([^（）()]+)[）)]\s*$", n)
    return (m.group(1).strip(), m.group(2).strip()) if m else (n.strip(), None)


def ja_names(other_names: list[str]) -> list[str]:
    """Danbooru 別名裡**確定是日文**的名字（去掉括號），照 Danbooru 的順序，最多 3 個：
    有假名的，或純漢字但用了日文寫法（豊川祥子、雷電将軍）。
    純漢字、中文也通的（雷电将军、国崩、千夏）不拿去查：原神這種中國遊戲在 Bangumi 上的「原名」就是中文，
    拿中文別名去查會對到別的稱呼（雷電將軍 → 巴爾澤布、流浪者 → 國崩）。"""
    plain, paren = [], []
    for n in other_names or []:
        n = (n or "").strip()
        if not n or names.HANGUL.search(n) or names.JUNK.search(n) or len(n) > 30:
            continue
        core, q = _paren_split(n)
        han_only = bool(names.HAN_ONLY.match(core) and names.HAN_CHAR.search(core))
        if not (names.KANA.search(core) or (han_only and not names._chinese_ok(core))):
            continue
        if len(norm(core)) < 2 or core in plain or core in paren:
            continue
        (paren if q else plain).append(core)
    # 帶括號的別名多半是「通用名字＋作品」（キャスター(Fate/EXTRA)、千夏(ゼンゼロ)）：去掉括號就變成別人的名字
    # （b20 實測：玉藻前 → 用「キャスター」對到「新聞主播」）。有不帶括號的日文名就只用那些
    return (plain or paren)[:3]


# ---------- Bangumi ----------
def _bgm(method: str, path: str, **kw):
    from .httpclient import UA, _req
    r = _req("bangumi", method, BGM + path, headers={"User-Agent": UA + " (+bangumi zh names)", "Accept": "application/json"}, **kw)
    if r.status_code == 404:
        return None
    if r.status_code >= 400:
        raise RuntimeError(f"Bangumi HTTP {r.status_code}")
    return r.json()


def _info(item: dict, key: str) -> str | None:
    for x in item.get("infobox") or []:
        if x.get("key") == key:
            v = x.get("value")
            if isinstance(v, list):
                v = next((y.get("v") for y in v if isinstance(y, dict) and y.get("v")), None)
            return (v or "").strip() or None
    return None


def _ok_zh(s: str | None) -> bool:
    return bool(s) and bool(names.HAN_CHAR.search(s)) and not names.KANA.search(s) and not names.JUNK.search(s) and len(s) <= 24


def search_characters(keyword: str) -> list[dict]:
    d = _bgm("POST", "/v0/search/characters?limit=10", json={"keyword": keyword}) or {}
    k = norm(keyword)
    out = []
    for c in d.get("data") or []:
        if norm(c.get("name") or "") == k:
            cn = _info(c, "简体中文名")
            out.append({"id": c.get("id"), "name": c.get("name"), "cn": cn if _ok_zh(cn) else None})
    return out


def character_subjects(cid) -> list[dict]:
    return _bgm("GET", f"/v0/characters/{int(cid)}/subjects") or []


def search_subjects(keyword: str) -> list[dict]:
    d = _bgm("POST", "/v0/search/subjects?limit=10", json={"keyword": keyword}) or {}
    k = norm(keyword)
    out = []
    for s in d.get("data") or []:
        if k and (norm(s.get("name") or "") == k or norm(_simp(s.get("name_cn") or "")) == norm(_simp(keyword))):
            cn = (s.get("name_cn") or "").strip()
            out.append({"id": s.get("id"), "name": s.get("name"), "cn": cn if _ok_zh(cn) else None, "type": s.get("type")})
    return out


# ---------- 這個角色在你圖庫裡屬於哪些作品（用來確認 Bangumi 的同名角色是不是同一個） ----------
def series_hints(tag: str) -> set[str]:
    c = db.conn()
    keys = set()
    m = re.search(r"_\(([^()]+)\)$", tag)
    if m:
        keys.add(m.group(1))
    r = c.execute("SELECT ti.copyright, ti.copyrights FROM tags t JOIN tag_info ti ON ti.tag_id=t.id WHERE t.name=?", (tag,)).fetchone()
    if r:
        if r[0]:
            keys.add(r[0])
        try:
            keys |= set(json.loads(r[1] or "[]")[:5])
        except (TypeError, ValueError):
            pass
    for (k,) in c.execute("""SELECT t2.name FROM media_tags a JOIN tags t1 ON t1.id=a.tag_id AND t1.name=?
                             JOIN media_tags b ON b.media_id=a.media_id AND b.state=1 JOIN tags t2 ON t2.id=b.tag_id AND t2.category='copyright'
                             WHERE a.state=1 GROUP BY t2.name ORDER BY COUNT(*) DESC LIMIT 4""", (tag,)):
        keys.add(k)
    out = set()
    for k in keys:
        out.add(norm(names.pretty_raw(k)))
        out.add(norm(k.replace("_", " ")))
        zh = names._series_zh().get(k)
        if zh:
            out.add(norm(_simp(zh)))
        row = c.execute("SELECT ti.other_names FROM tags t JOIN tag_info ti ON ti.tag_id=t.id WHERE t.name=?", (k,)).fetchone()
        for n in json.loads(row[0] or "[]") if row else []:
            if not names.JUNK.search(n or ""):
                out.add(norm(_simp(n)))
    return {h for h in out if len(h) >= 2}


def _subject_match(subjects: list[dict], hints: set[str]) -> bool:
    for s in subjects:
        for n in (s.get("name"), s.get("name_cn")):
            k = norm(_simp(n or ""))
            if len(k) < 2:
                continue
            for h in hints:
                if k == h or (len(h) >= 3 and (h in k or k in h)):
                    return True
    return False


# ---------- 查一個 ----------
def _queries(tag: str, other_names: list[str]) -> list[tuple[str, bool]]:
    """要拿去查的名字：(名字, 是不是英文)。日文名（見 ja_names）先；有作品括號的 tag 再加英文名（Kirito、Spider-Man 這種）。"""
    qs = [(q, False) for q in ja_names(other_names)]
    en = names.pretty_raw(tag)
    if re.fullmatch(r"[A-Za-z0-9 .'\-]+", en) and len(norm(en)) >= 3:
        qs.append((en, True))
    return qs


def lookup_character(tag: str, other_names: list[str]) -> dict:
    """Bangumi：每個名字依序查，找到就停（b23 以前只看第一個「有同名」的名字：アーニャ 對到別人就放棄，
    沒再查 アーニャ・フォージャー；キリト 只有同人角色，沒再查 桐ヶ谷和人）。回傳 {status, zh?, raw?, ref?, options?}。"""
    hints = series_hints(tag)
    best = {"status": "none"}
    for q, english in _queries(tag, other_names):
        if english and not hints:
            continue                       # 英文名一定要對得上作品
        r = _bgm_one(q, english, hints)
        if r["status"] == "found":
            return r
        if r["status"] == "ambiguous" and best["status"] != "ambiguous":
            best = r
    return best


def _bgm_one(q: str, english: bool, hints: set[str]) -> dict:
    hits = search_characters(q)
    if not hits:
        return {"status": "none"}
    short = len(norm(q)) < 4 and "・" not in q
    with_cn = [h for h in hits if h["cn"]]
    if not with_cn:
        return {"status": "none", "note": "Bangumi 有這個角色，但沒有中文名"}
    found = lambda h: {"status": "found", "raw": h["cn"], "zh": names.to_trad(h["cn"]), "ref": f"bgm:character/{h['id']}"}
    if not hints:            # 不知道這個角色屬於哪部作品：只有「一個、名字夠長」才用
        if len(with_cn) == 1 and not (english or short):
            return found(with_cn[0])
        return {"status": "ambiguous", "options": _opts(with_cn, {})}
    # 一律看作品：b17 只有一個同名就直接用，結果「ドクター」對到別部的醫生、「雷」對到黑色雷霆
    ok, subs_of, empty = [], {}, 0
    for h in with_cn[:5]:
        try:
            subs = character_subjects(h["id"])
        except Exception as e:  # noqa
            log.info("Bangumi 角色作品 %s 失敗：%s", h["id"], e)
            subs = []
        subs_of[h["id"]] = subs
        empty += not subs
        if subs and _subject_match(subs, hints):
            ok.append(h)
    if len({h["cn"] for h in ok}) == 1:
        return found(ok[0])
    if not ok and len(with_cn) == 1 and empty == 1 and not (english or short):
        return found(with_cn[0])      # Bangumi 上這個角色沒有登記作品：沒辦法對，名字夠長就用
    if ok:                            # 好幾個都對得上作品、譯名不同 → 你挑
        return {"status": "ambiguous", "options": _opts(ok, subs_of)}
    unknown = [h for h in with_cn[:5] if not subs_of.get(h["id"])]
    if unknown:                       # 對不上的不列；沒登記作品、沒辦法確認的列出來讓你挑
        return {"status": "ambiguous", "options": _opts(unknown, subs_of)}
    return {"status": "none", "note": "Bangumi 上同名的角色都不是這部作品的"}


# ---------- Wikidata：有台灣譯名（zh-tw）的話最好 ----------
WIKIDATA = os.environ.get("LIB_WIKIDATA", "https://www.wikidata.org")
# 「這是虛構角色／VTuber」：instance of（P31）或英文說明
CHAR_P31 = {"Q95074", "Q15632617", "Q80447738", "Q87576284", "Q3658341", "Q1569167", "Q15711870", "Q1114461", "Q15773347",
            "Q30168244", "Q30168533", "Q3050815", "Q4271324", "Q21070568", "Q55155641", "Q28020127", "Q34315358", "Q113050807"}
_CHAR_DESC = re.compile(r"fictional|character|virtual youtuber|vtuber|キャラクター|バーチャルYouTuber|角色|虛擬|虚拟", re.I)


def _wd(params: dict) -> dict:
    from .httpclient import WIKI_UA, _req
    r = _req("wikidata", "GET", WIKIDATA + "/w/api.php", params={**params, "format": "json"}, headers={"User-Agent": WIKI_UA})
    if r.status_code >= 400:
        raise RuntimeError(f"Wikidata HTTP {r.status_code}")
    return r.json()


def _wd_label(ent: dict) -> tuple[str | None, bool]:
    """(繁體中文名, 是不是台灣／繁體的標籤)。zh-tw > zh-hant > zh-hk > zh > zh-hans。"""
    lb = ent.get("labels") or {}
    for k, tw in (("zh-tw", True), ("zh-hant", True), ("zh-hk", True), ("zh", False), ("zh-hans", False), ("zh-cn", False)):
        v = (lb.get(k) or {}).get("value")
        if _ok_zh(v):
            return (v if tw else names.to_trad(v)), tw
    return None, False


def wd_lookup(tag: str, other_names: list[str], hints: set[str] | None = None) -> dict:
    """Wikidata：名字（標籤或別名）完全一樣、而且是虛構角色／VTuber；同名好幾個時看說明裡的作品（fictional character from Pokémon Sword and Shield）。"""
    hints = series_hints(tag) if hints is None else hints
    long_hints = {h for h in hints if len(h) >= 5}
    for q, english in _queries(tag, other_names):
        lang = "en" if english else "ja"
        d = _wd({"action": "wbsearchentities", "search": q, "language": lang, "uselang": lang, "type": "item", "limit": 7})
        ids = [x["id"] for x in d.get("search") or [] if norm((x.get("match") or {}).get("text") or x.get("label") or "") == norm(q)]
        if not ids:
            continue
        e = _wd({"action": "wbgetentities", "ids": "|".join(ids), "props": "labels|descriptions|claims",
                 "languages": "zh-tw|zh-hant|zh-hk|zh|zh-hans|zh-cn|en|ja"}).get("entities") or {}
        cands = []
        for i in ids:
            ent = e.get(i) or {}
            p31 = {((c.get("mainsnak") or {}).get("datavalue") or {}).get("value", {}).get("id") for c in (ent.get("claims") or {}).get("P31", [])}
            desc = " ".join((ent.get("descriptions") or {}).get(k, {}).get("value", "") for k in ("en", "ja"))
            if not (p31 & CHAR_P31 or _CHAR_DESC.search(desc)):
                continue                  # 不是角色（歌手、法國小鎮、小行星…）
            zh, tw = _wd_label(ent)
            lb = ent.get("labels") or {}
            cn = next((v for v in ((lb.get(k) or {}).get("value") for k in ("zh-cn", "zh-hans", "zh")) if _ok_zh(v)), None)
            nd = norm(_simp(desc))
            cands.append({"id": i, "zh": zh, "tw": tw, "cn": cn, "series_ok": any(h in nd for h in long_hints),
                          "vtuber": bool(re.search(r"youtuber|vtuber", desc, re.I))})
        if not cands:
            continue
        if hints:
            ok = [c for c in cands if c["series_ok"]]
            if not ok and len(cands) == 1 and cands[0]["vtuber"] and not english:
                ok = cands                # VTuber 的說明不寫所屬（hololive），名字是全名就用
        else:
            ok = cands if len(cands) == 1 and not english else []
        if len(ok) == 1:
            c = ok[0]
            if not c["zh"]:
                return {"status": "none", "note": "Wikidata 有這個角色，但沒有中文名"}
            # raw：大陸譯名（簡體模式用）；Wikidata 沒有簡體標籤就跟顯示用的一樣
            return {"status": "found", "zh": c["zh"], "raw": c.get("cn") or c["zh"], "ref": f"wd:{c['id']}", "tw": c["tw"]}
    return {"status": "none"}


def lookup_best(tag: str, other_names: list[str]) -> dict:
    """角色：Wikidata 的台灣譯名 > Bangumi > Wikidata 的其他中文名。"""
    hints = series_hints(tag)
    try:
        wd = wd_lookup(tag, other_names, hints)
    except Exception as e:  # noqa  連不上 Wikidata：只用 Bangumi
        log.info("Wikidata 查 %s 失敗：%s", tag, e)
        wd = {"status": "none"}
    if wd["status"] == "found" and wd.get("tw"):
        return wd
    bg = lookup_character(tag, other_names)
    if bg["status"] == "found":
        return bg
    if wd["status"] == "found":
        return wd
    return bg


def _opts(hits: list[dict], subs_of: dict) -> list[dict]:
    """好幾個、或對不上作品：不自動用，列成候選（去掉重複的譯名）。"""
    seen, out = set(), []
    for h in hits:
        zh = names.to_trad(h["cn"])
        if zh in seen:
            continue
        seen.add(zh)
        out.append({"zh": zh, "raw": h["cn"], "ref": f"bgm:character/{h['id']}",
                    "subjects": [x.get("name_cn") or x.get("name") for x in subs_of.get(h["id"], [])][:3]})
    return out[:5]


def lookup_series(tag: str, other_names: list[str]) -> dict:
    qs = ja_names(other_names)
    if not qs:
        qs = [names.pretty_raw(tag)] if "_(" not in tag else []
    for q in qs:
        hits = [h for h in search_subjects(q) if h["cn"]]
        cns = {h["cn"] for h in hits}
        if len(cns) == 1:
            h = hits[0]
            return {"status": "found", "raw": h["cn"], "zh": names.to_trad(h["cn"]), "ref": f"bgm:subject/{h['id']}"}
        if len(cns) > 1:
            return {"status": "ambiguous", "options": [{"zh": names.to_trad(h["cn"]), "raw": h["cn"], "ref": f"bgm:subject/{h['id']}",
                                                        "subjects": [h["name"]]} for h in hits][:5]}
    return {"status": "none"}


# ---------- 造型：本體的名字＋造型 ----------
LABELS = {"swimsuit": "泳裝", "bikini": "比基尼", "new_year": "新年", "bunny": "兔女郎", "casual": "便服", "casual_clothes": "便服",
          "sailor": "水手服", "school_uniform": "制服", "uniform": "制服", "maid": "女僕", "jirai_kei": "地雷系", "dress": "禮服",
          "christmas": "聖誕", "halloween": "萬聖節", "idol": "偶像", "track": "運動服", "sportswear": "運動服", "cheer_squad": "啦啦隊",
          "cheerleader": "啦啦隊", "kimono": "和服", "pajamas": "睡衣", "pyjamas": "睡衣", "band": "樂團", "hot_spring": "溫泉",
          "young": "幼年", "adult": "成年", "wedding": "婚紗", "winter": "冬裝", "summer": "夏裝", "party": "派對", "race_queen": "賽車女郎",
          "office": "上班", "camp": "露營", "riding": "騎乘", "armor": "鎧甲", "alter": "Alter", "append": "Append"}
_NTH = re.compile(r"^(\d+)(?:st|nd|rd|th)_costume$")
_ZH_NUM = "一二三四五六七八九十"


def variant_label(q: str) -> str:
    if q in LABELS:
        return LABELS[q]
    m = _NTH.match(q)
    if m:
        n = int(m.group(1))
        return "初始服裝" if n == 1 else f"第{_ZH_NUM[n - 1] if n <= 10 else n}套服裝"
    return " ".join(w[:1].upper() + w[1:] for w in q.replace("_", " ").split())


def refresh_variants() -> int:
    """所有造型 tag：本體現在顯示的中文名＋造型。只算本機（不連網），本體的名字變了就跟著變。"""
    c = db.conn()
    rows = c.execute("""SELECT t.name, ti.other_names, ti.display_name FROM tags t LEFT JOIN tag_info ti ON ti.tag_id=t.id
                        WHERE t.category='character'""").fetchall()
    info = {r[0]: (json.loads(r[1] or "[]"), r[2]) for r in rows}
    existing = set(info)
    out = []
    for name in info:
        base = names.base_of(name, existing)
        if not base:
            continue
        m = names.VARIANT.match(name) or names.SIMPLE_VARIANT.match(name)
        q = m.group(2) if m else ""
        on, dn = info[base]
        bd = names.display(base, on, dn, "zh")
        if not names.HAN_CHAR.search(bd) or names.KANA.search(bd):
            continue            # 本體也沒有中文名：不用造型名蓋掉 Danbooru 給造型的名字
        out.append((name, "character", f"{bd}（{variant_label(q)}）"))
    with db.write_lock:
        c.executemany("""INSERT INTO name_zh(name, category, status, zh, checked_at) VALUES(?,?,'variant',?,?)
                         ON CONFLICT(name) DO UPDATE SET status='variant', zh=excluded.zh, checked_at=excluded.checked_at
                         WHERE name_zh.status IN ('variant','none','error') OR name_zh.status IS NULL""",
                      [(n, cat, zh, time.time()) for n, cat, zh in out])
    reload()
    return len(out)


# ---------- 要查哪些 ----------
def pending(force: bool = False) -> list[str]:
    """有確認圖的角色／作品，還沒查過（查不到的 60 天後再查）。造型不查（用本體的）。"""
    c = db.conn()
    rows = c.execute("""SELECT t.name, t.category FROM tags t JOIN media_tags x ON x.tag_id=t.id AND x.state=1
                        WHERE t.category IN ('character','copyright') GROUP BY t.id ORDER BY COUNT(*) DESC""").fetchall()   # 圖多的先查
    existing = {r[0] for r in rows if r[1] == "character"}
    cutoff = time.time() - RETRY_DAYS * 86400
    done = {r[0]: (r[1], r[2] or 0) for r in c.execute("SELECT name, status, checked_at FROM name_zh")}
    out = []
    for name, cat in rows:
        if cat == "character" and names.base_of(name, existing):
            continue
        st = done.get(name)
        if force or not st or (st[0] in ("none", "error") and st[1] < cutoff):
            out.append(name)
    return out


def queue(names_: list[str] | None = None, manual: bool = False) -> int:
    from . import jobqueue, online
    s = db.get_settings()
    if not s.get("online_enabled"):
        return 0
    todo = names_ if names_ is not None else pending()
    if not manual:      # 已經排在佇列裡的不要再排（以前每次重啟都多排一組，同一個查兩三次）
        q = {n for r in online.get_jobs().q.rows() if r["kind"] == "zhname" and isinstance(r["arg"], list) for n in r["arg"]}
        todo = [t for t in todo if t not in q]
    if todo:
        online.get_jobs().submit("zhname", todo, jobqueue.NORMAL if manual else jobqueue.BACKGROUND, manual=manual)
    return len(todo)


_pend: set[str] = set()
_plock = threading.Lock()
_pthread: threading.Thread | None = None


def schedule(name: str):
    """新的角色／作品抓完 Danbooru 資料卡時呼叫：記下來，每 15 秒合成一組排進線上佇列。"""
    global _pthread
    s = db.get_settings()
    if not (s.get("online_enabled") and s.get("auto_zhname", True)):
        return
    with _plock:
        _pend.add(name)
        if _pthread is None or not _pthread.is_alive():
            _pthread = threading.Thread(target=_flush, daemon=True, name="zhname-schedule")
            _pthread.start()


def _flush():
    while True:
        time.sleep(15)
        with _plock:
            todo = sorted(_pend)
            _pend.clear()
        if todo:
            try:
                done = {r[0] for r in db.conn().execute(
                    f"SELECT name FROM name_zh WHERE status NOT IN ('error') AND name IN ({','.join('?' * len(todo))})", todo)}
                todo = [t for t in todo if t not in done]
                if todo:
                    queue(todo)
            except Exception as e:  # noqa
                log.warning("排中文名查詢失敗：%s", e)


def queue_on_start():
    """啟動時：設定開著就把還沒查過的排進背景（Bangumi 每個約 1 秒）；規則重新套一次、造型先用本機算一次。"""
    try:        # 維基以前被 403 擋（User-Agent），記成「失敗／查不到」的作品譯名：清掉，之後重查
        from . import wikiname
        wikiname._ensure()
        with db.write_lock:
            db.conn().execute("DELETE FROM series_zh WHERE via='error'")
    except Exception as e:  # noqa
        log.info("清維基舊紀錄失敗：%s", e)
    recheck_local()
    refresh_variants()
    if db.get_settings().get("auto_zhname", True):
        queue()


# ---------- Danbooru 的中文名比較可信的情況：留著 Danbooru 的 ----------
def keep_danbooru(cat: str, on: list[str], bgm_zh: str) -> bool:
    """- Danbooru 的第一個別名就是中文（原神、絕區零、碧藍航線這種中國遊戲：第一個別名＝官方中文名）
         b19 實測：玉藻前 → 新聞主播、耀嘉音 → 阿斯特拉、雷 → 黑色雷霆，都是這種
       - Bangumi 的名字是 Danbooru 中文名的一部分（千夏蕾米爾 vs 千夏、才羽桃井 vs 才羽桃）
       - 作品：Danbooru 有中文名就留著（Bangumi 只用來補沒有中文的）"""
    dz = names.pick(on, "zh")
    if not dz or dz == bgm_zh:
        return False
    if cat == "copyright":      # 作品：Bangumi 是一版一個條目（朱、紫分開），Danbooru 有中文就用 Danbooru 的（寶可夢朱紫 ≠ 寶可夢 朱）
        return True
    if cat != "character":
        return False
    first = next(((n or "").strip() for n in on if (n or "").strip()), "")
    core = _paren_split(first)[0]
    if names.HAN_ONLY.match(core) and names.HAN_CHAR.search(core) and names._chinese_ok(core) and not names.JUNK.search(core):
        return True
    return norm(bgm_zh) in norm(dz)


def recheck_local() -> int:
    """已經查到的，用目前的規則再判斷一次要不要留 Danbooru 的（不連網）。規則改了不用重查 Bangumi。"""
    c = db.conn()
    # b72：簡轉繁的規則改了（栗村 → 慄村、明里 → 明裡、洛托姆 → 洛託姆 這類轉錯）：Bangumi 的原文重轉一次
    fixed = []
    for name, raw, zh in c.execute("SELECT name, raw, zh FROM name_zh WHERE ref LIKE 'bgm:%' AND raw IS NOT NULL").fetchall():
        z = names.to_trad(raw)
        if z != zh:
            fixed.append((z, name))
    # 校對過的譯名表裡有的：已經有人看過了，「Bangumi 自動換的」不用再排在前面等你看
    cu = list(names._curated())
    with db.write_lock:
        if fixed:
            c.executemany("UPDATE name_zh SET zh=? WHERE name=?", fixed)
        for i in range(0, len(cu), 500):
            part = cu[i:i + 500]
            c.execute(f"UPDATE name_zh SET seen=1 WHERE seen=0 AND name IN ({','.join('?' * len(part))})", part)
    rows = c.execute("""SELECT z.name, z.status, z.zh, z.category, ti.other_names FROM name_zh z JOIN tags t ON t.name=z.name
                        LEFT JOIN tag_info ti ON ti.tag_id=t.id WHERE z.status IN ('found','kept') AND z.zh IS NOT NULL""").fetchall()
    ch = []
    for name, st, zh, cat, on in rows:
        want = "kept" if keep_danbooru(cat, json.loads(on or "[]"), zh) else "found"
        if want != st:
            ch.append((want, name))
    if ch:
        with db.write_lock:
            c.executemany("UPDATE name_zh SET status=? WHERE name=?", ch)
    if ch or fixed:
        reload()
    return len(ch) + len(fixed)


# ---------- 背景工作（線上佇列 kind=zhname） ----------
def _current_display(name: str, cat: str, on: list[str], lang: str = "zh") -> str:
    """不算 Bangumi 的話，現在會顯示什麼（「改回」用）。"""
    if cat == "copyright":
        zh = names._series_zh().get(name)
        if zh:
            return zh
    pk = names.pokemon_zh(name, on) if cat == "character" else None
    return pk or names.pick(on, "zh") or names.pretty_raw(name)       # 沒中文：英文（使用者要求，不用日文）


def run(tags: list[str], state: dict, stop) -> None:
    c = db.conn()
    for name in tags:
        if stop():
            break
        r = c.execute("""SELECT t.category, ti.other_names FROM tags t LEFT JOIN tag_info ti ON ti.tag_id=t.id WHERE t.name=?""",
                      (name,)).fetchone()
        if not r:
            state["done"] = (state.get("done") or 0) + 1
            continue
        cat, on = r[0], json.loads(r[1] or "[]")
        try:
            res = lookup_series(name, on) if cat == "copyright" else lookup_best(name, on)
        except Exception as e:  # noqa  連不上：一天後再試
            log.info("Bangumi 查 %s 失敗：%s", name, e)
            res = {"status": "error"}
        prev = _current_display(name, cat, on)
        if res["status"] == "found" and keep_danbooru(cat, on, res["zh"]):
            res["status"] = "kept"
        with db.write_lock:
            c.execute("""INSERT INTO name_zh(name, category, status, zh, raw, ref, options, prev, seen, checked_at)
                         VALUES(?,?,?,?,?,?,?,?,0,?)
                         ON CONFLICT(name) DO UPDATE SET category=excluded.category, status=excluded.status, zh=excluded.zh,
                           raw=excluded.raw, ref=excluded.ref, options=excluded.options,
                           prev=COALESCE(name_zh.prev, excluded.prev),
                           seen=CASE WHEN name_zh.zh IS excluded.zh THEN name_zh.seen ELSE 0 END, checked_at=excluded.checked_at""",
                      (name, cat, res["status"], res.get("zh"), res.get("raw"), res.get("ref"),
                       json.dumps(res.get("options"), ensure_ascii=False) if res.get("options") else None, prev,
                       time.time() - ((RETRY_DAYS - 1) * 86400 if res["status"] == "error" else 0)))
        if res["status"] == "found":
            state["found"] = (state.get("found") or 0) + 1
            log.info("中文名：%s → %s（%s）", name, res["zh"], res.get("ref"))
        state["done"] = (state.get("done") or 0) + 1
    reload()
    try:
        refresh_variants()
    except Exception as e:  # noqa
        log.warning("造型中文名更新失敗：%s", e)


# ---------- 中文名整理用 ----------
def rows_for_review() -> dict[str, dict]:
    out = {}
    for r in db.conn().execute("SELECT * FROM name_zh"):
        out[r["name"]] = {k: r[k] for k in r.keys()}
        out[r["name"]]["options"] = json.loads(r["options"] or "[]")
    return out


def mark_seen(tags: list[str]) -> int:
    with db.write_lock:
        cur = db.conn().executemany("UPDATE name_zh SET seen=1 WHERE name=?", [(t,) for t in tags])
    return cur.rowcount


def debug(tag: str) -> dict:
    """診斷：這個角色查了哪些名字、作品線索、Wikidata／Bangumi 各自的結果（例外也列出來）。"""
    c = db.conn()
    r = c.execute("SELECT ti.other_names FROM tags t LEFT JOIN tag_info ti ON ti.tag_id=t.id WHERE t.name=?", (tag,)).fetchone()
    on = json.loads((r[0] if r else None) or "[]")
    out = {"queries": _queries(tag, on), "hints": sorted(series_hints(tag))[:30]}
    for k, fn in (("wikidata", lambda: wd_lookup(tag, on)), ("bangumi", lambda: lookup_character(tag, on))):
        try:
            out[k] = fn()
        except Exception as e:  # noqa
            out[k] = {"error": f"{type(e).__name__}: {e}"}
    try:
        q = out["queries"][0][0] if out["queries"] else tag
        out["wd_raw"] = _wd({"action": "wbsearchentities", "search": q, "language": "ja", "uselang": "ja", "type": "item", "limit": 3})
    except Exception as e:  # noqa
        out["wd_raw"] = {"error": f"{type(e).__name__}: {e}"}
    return out


def net_test() -> dict:
    """診斷：Wikipedia／Wikidata 用不同 User-Agent 回應什麼（Wikimedia 會擋不合規定的 User-Agent）。"""
    from .httpclient import UA, BROWSER_UA, client
    out = {}
    for site, url in (("wikipedia", "https://ja.wikipedia.org/w/api.php?action=query&titles=A&format=json"),
                      ("wikidata", "https://www.wikidata.org/w/api.php?action=wbsearchentities&search=A&language=ja&format=json")):
        for name, ua in (("app", UA), ("app+", UA + " (+zh names)"), ("browser", BROWSER_UA),
                         ("contact", "PickleJar/1.1 (personal local image organizer; https://www.mediawiki.org/wiki/API:Etiquette)")):
            try:
                r = client().get(url, headers={"User-Agent": ua}, timeout=15)
                out[f"{site}:{name}"] = [r.status_code, r.text[:120]]
            except Exception as e:  # noqa
                out[f"{site}:{name}"] = str(e)[:120]
    return out
