"""顯示名稱：從 Danbooru 別名挑出中文 / 日文名，並把簡體轉成繁體（台灣用字）。
另外處理「造型／皮膚」：mika_(swimsuit)_(blue_archive) 是 mika_(blue_archive) 的一個造型。"""
from __future__ import annotations

import re
from functools import lru_cache

KANA = re.compile(r"[぀-ヿㇰ-ㇿｦ-ﾟ]")
HANGUL = re.compile(r"[가-힯ᄀ-ᇿ]")
HAN_ONLY = re.compile(r"^[㐀-鿿豈-﫿·・･\s\-－—（）()：:]+$")
LATIN = re.compile(r"[A-Za-z]")
HAN_CHAR = re.compile(r"[㐀-鿿豈-﫿]")


@lru_cache(maxsize=1)
def _cc():
    try:
        from opencc import OpenCC
        return OpenCC("s2twp")
    except Exception:  # 沒裝 opencc 就不轉換
        return None


# 人名、作品名裡本來就是繁體也通用的字，OpenCC 會轉錯（b72 實測：栗村愛理 → 慄村、明里 → 明裡、
# 洛托姆 → 洛託姆、范／于／余 是姓）。轉換前後長度一樣時，這些字照原樣留著。
NAME_KEEP = set("栗托台夸松郁岩")
NAME_FIX = {"冢": "塚"}
SURNAME_KEEP = set("范于余")          # 只在開頭（姓）留著：關于 → 關於
_LI_PHRASE = set("心這这那哪家夜手城屋水眼夢梦")


def _keep(i: int, a: str, s: str) -> bool:
    if a in NAME_KEEP:
        return True
    if a in SURNAME_KEEP:
        return i == 0
    if a == "里":                       # 明里、繪里、里蘇特 留著；心里、這里、里面 → 裡
        return not ((i > 0 and s[i - 1] in _LI_PHRASE) or s[i + 1:i + 2] in ("面", "頭", "头"))
    return False


def to_trad(s: str) -> str:
    cc = _cc()
    out = cc.convert(s) if cc and s else s
    if out and len(out) == len(s):
        out = "".join(a if _keep(i, a, s) else NAME_FIX.get(b, b) for i, (a, b) in enumerate(zip(s, out)))
    elif "夸" in s:
        out = out.replace("誇", "夸")
    return out


# ---------- 簡體模式（設定 zh_mode＝cn）----------
# 介面文字由前端換成大陸用語（static/js/zhcn.js）；這裡管角色／作品名稱：有大陸譯名的用大陸譯名
#（Bangumi 原文、維基百科「大陆简体」標題、寶可夢官方簡中譯名），沒有的才把台灣譯名轉成簡體字。
def mode() -> str:
    """tw 繁體（台灣譯名）／ cn 简体（大陸譯名）。"""
    from . import db
    s = db._settings_cache if db._settings_cache is not None else db.get_settings()
    return "cn" if s.get("zh_mode") == "cn" else "tw"


@lru_cache(maxsize=50000)
def to_simp(s: str) -> str:
    """繁體 → 簡體字（只換字、不換用語：名字不要被「影片→视频」這種規則改到）。"""
    st = _st()
    return st[1].convert(s) if st and s else s


def pretty_raw(name: str) -> str:
    """akiyama_mizuki → Akiyama Mizuki；mika_(blue_archive) → Mika"""
    base = re.sub(r"_\([^()]*\)$", "", name) or name
    return " ".join(w[:1].upper() + w[1:] for w in base.replace("_", " ").split())


# 日本新字體（中文不用的寫法）：名字裡有這些字，多半是日文漢字名，不當中文名
JP_ONLY = set("亜悪圧囲壱隠栄営駅円塩応殴桜穏仮価絵拡覚楽巻陥勧寛歓観関顔帰気戯犠拠挙暁駆勲恵掲渓経蛍軽鶏撃倹剣険圏検権顕験厳広鉱黒"
              "済砕斎剤雑桟賛児実釈収渋獣縦粛処奨渉焼証乗剰壌嬢浄畳譲醸寝図粋酔髄瀬斉摂説専戦銭繊総荘捜挿巣層騒増蔵臓続対帯滞滝択沢"
              "単団弾遅昼鋳庁徴聴鎮逓鉄転伝稲闘徳読届弐悩脳覇拝廃売発髪抜晩浜払仏塀変辺舗歩穂豊毎満黙訳薬誉揺様謡頼覧竜両緑隣涙塁"
              "隷霊齢暦歴労録呪込畑峠匂働枠々")   # 「咲」不算：中文音譯名常用（千咲、妃咲、梓川咲太）
# 「奥」也不算：簡體中文同一個字（奥提斯 = Outis），以前被當成日文，Limbus 的角色全部顯示日文
# 看起來像標籤、不像名字的字眼（Pixiv 標籤常混在 Danbooru 別名裡）
# 以前「夢」「百合」「腐」整個字擋掉，連喜多川海夢、博麗靈夢、百合園聖婭都一起擋了 → 只擋標籤用法（夢女、百合向…）
JUNK = re.compile(r"同人|画师|畫師|绘师|繪師|周榜|榜|排行|合集|本子|R-?18|^腐|腐向|夢女|梦女|夢向|梦向|夢小説|イラ|FA$|FA\b|^百合$|百合向|百合CP|cp|CP|コラボ|版本|表情|差分|系列作")
# 學校、隊伍名：角色的別名常混著（少女與戰車「繼續高校」「河馬隊」「隊長組」），角色名不用（b110.8；作品名照用：○○戰隊）
GROUPISH = re.compile(r"(高校|高中|[學学][園园]|[學学]院|隊|队|組|组)$")


@lru_cache(maxsize=1)
def _st():
    try:
        from opencc import OpenCC
        return OpenCC("s2t"), OpenCC("t2s")
    except Exception:
        return None


def _chinese_ok(n: str) -> bool:
    """純漢字名是不是「中文」：沒有日文新字體，也不是簡繁混用（簡繁混用通常是日文）。"""
    if any(ch in JP_ONLY for ch in n):
        return False
    st = _st()
    if st:
        s2t, t2s = st
        if s2t.convert(n) != n and t2s.convert(n) != n:   # 同時有簡體字和繁體字 → 多半是日文
            return False
    return True


def pick(other_names: list[str], lang: str, variant: bool = False, char: bool = False) -> str | None:
    """char：角色名（學校、隊伍名的別名不用，GROUPISH）。"""
    return _pick(tuple(other_names or ()), lang, variant, char)


@lru_cache(maxsize=50000)
def _pick(other_names: tuple, lang: str, variant: bool, char: bool = False) -> str | None:
    """結果只跟輸入有關 → 快取（簡繁轉換很慢，書架一次要算上千個名字）。"""
    names = [n.strip() for n in other_names if n and n.strip()]
    if char:
        names = [n for n in names if not GROUPISH.search(n)]
    if lang == "zh":
        han = [n for n in names if HAN_ONLY.match(n) and HAN_CHAR.search(n) and _chinese_ok(n) and not JUNK.search(n)
               and len(n) <= 16]
        if not han:     # 沒有中文別名：日文漢字名換成中文字形再用（暁 → 曉、鶴 → 鶴；中文圈本來就直接用漢字名）（b109.5）
            han = [j for j in (jp_to_trad(n) for n in names if HAN_ONLY.match(n) and HAN_CHAR.search(n) and not JUNK.search(n)
                               and len(n) <= 16 and any(ch in JP_ONLY for ch in n)) if j and _chinese_ok(j)]
        if not variant:  # 「甘雨(原神)」這種帶括號的往後排，先用「甘雨」
            han = [n for n in han if not re.search(r"[（(]", n)] + [n for n in han if re.search(r"[（(]", n)]
        return to_trad(han[0]) if han else None
    if lang == "ja":
        for n in names:
            if (KANA.search(n) or (HAN_ONLY.match(n) and HAN_CHAR.search(n))) and not HANGUL.search(n) \
                    and not JUNK.search(n) and not re.fullmatch(r"[・･·\s\-－—]+", n):
                return n
    return None


@lru_cache(maxsize=1)
def _franchise() -> dict:
    """作品官方中文名（app/data/franchise_zh.json，b109.6）：FGO 台服／國服、寶可夢人物（52poke）。tag → {tw, cn}。"""
    from pathlib import Path
    return _read_json(Path(__file__).parent / "data" / "franchise_zh.json").get("names", {})


def franchise_zh(name: str, cn: bool = False) -> str | None:
    hit = _franchise().get(name or "")
    if not hit:
        return None
    return (hit.get("cn") or to_simp(hit.get("tw") or "")) if cn else (hit.get("tw") or to_trad(hit.get("cn") or ""))


@lru_cache(maxsize=1)
def _jp_map() -> dict:
    """日本新字體 → 繁體字形（OpenCC 的 JPVariants 反過來：暁 → 曉、桜 → 櫻、竜 → 龍）。"""
    try:
        import opencc
        from pathlib import Path
        out = {}
        for line in (Path(opencc.__file__).parent / "dictionary" / "JPVariants.txt").read_text("utf-8").splitlines():
            if "\t" in line:
                t, j = line.split("\t", 1)
                for ch in j.split():
                    if len(ch) == 1 and len(t) == 1:
                        out.setdefault(ch, t)
        return out
    except Exception:  # noqa  沒裝 opencc：不換
        return {}


def jp_to_trad(n: str) -> str | None:
    """日文漢字名 → 中文字形；換完還有日文才有的字（字典沒收）就回傳 None。"""
    m = _jp_map()
    out = "".join(m.get(ch, ch) for ch in n)
    return None if any(ch in JP_ONLY for ch in out) else out


@lru_cache(maxsize=1)
def _pokemon() -> dict:
    """寶可夢官方台灣譯名（PokeAPI 的資料，內建）：tag → [台灣譯名, 日文, 大陸譯名]。"""
    import json
    from pathlib import Path
    try:
        return json.loads((Path(__file__).parent / "data" / "pokemon_zh.json").read_text("utf-8"))["names"]
    except Exception:
        return {}


def pokemon_zh(name: str, other_names: list[str] | None, cn: bool = False) -> str | None:
    """寶可夢的物種名（gardevoir → 沙奈朵）。只有確定是寶可夢才用：tag 是 xxx_(pokemon)，
    或 Danbooru 別名裡有牠的日文名（避免 golem 這種一般詞被當成隆隆岩）。"""
    base = name[:-len("_(pokemon)")] if name.endswith("_(pokemon)") else name
    hit = _pokemon().get(base)
    if not hit:
        return None
    if name.endswith("_(pokemon)") or (hit[1] and hit[1] in (other_names or [])):
        return (hit[2] if len(hit) > 2 else to_simp(hit[0])) if cn else hit[0]
    return None


def candidates(other_names: list[str] | None) -> list[str]:
    """可以當中文名的別名（繁體、去重，照 Danbooru 的順序）：讓你自己挑。"""
    out = []
    for n in other_names or []:
        n = (n or "").strip()
        if HAN_ONLY.match(n) and HAN_CHAR.search(n) and not JUNK.search(n) and len(n) <= 16 and _chinese_ok(n):
            t = to_trad(n)
            if t not in out:
                out.append(t)
    return out


def _auto(name: str) -> str | None:
    """Bangumi 找到的中文名、造型的「本體＋造型」（zhname.py）。"""
    try:
        from . import zhname
        return zhname.auto_map().get(name or "")
    except Exception:  # noqa
        return None


def _read_json(p) -> dict:
    import json
    from pathlib import Path
    try:
        return json.loads(Path(p).read_text("utf-8"))
    except Exception:  # noqa  沒有檔案、壞掉
        return {}


def _pkg_path():
    import os
    from pathlib import Path
    return os.environ.get("LIB_ZH_CURATED") or str(Path(__file__).parent / "data" / "zh_curated.json")


def local_path():
    """你（或 Claude）之後校對的結果：存在資料夾 data/ 裡（不會被程式更新蓋掉）。"""
    from . import db
    return db.DATA_DIR / "zh_curated_local.json"


@lru_cache(maxsize=1)
def _tables() -> tuple[dict, set]:
    """(tag → 名字, 校對過的 tag)。兩份表：
    - app/data/zh_curated.json：2026-10 把有確認圖的角色、作品全部看過一遍（改掉錯的、補上沒中文的、對的鎖住；
      不確定的放 reviewed_none，照舊顯示自動挑的名字）
    - data/zh_curated_local.json：之後的校對（中文名整理 ›「貼上校對結果」），蓋過上面那份
    值是 null／空字串＝看過了、沒有可靠的中文名（不再列進「還沒校對」）。"""
    names_, reviewed = {}, set()
    for d in (_read_json(_pkg_path()), _read_json(local_path())):
        for sec in ("character", "copyright", "names"):
            for k, v in (d.get(sec) or {}).items():
                v = (v.get("zh") if isinstance(v, dict) else v) or ""
                reviewed.add(k)
                if v.strip():
                    names_[k] = v.strip()
                else:
                    names_.pop(k, None)
        reviewed |= set(d.get("reviewed_none") or [])
    return names_, reviewed


def _curated() -> dict:
    return _tables()[0]


_curated.cache_clear = _tables.cache_clear      # 舊的呼叫方式（測試）


def reviewed(name: str) -> bool:
    return (name or "") in _tables()[1]


def curated(name: str) -> str | None:
    return _curated().get(name or "")


def save_local(items: dict[str, str | None]) -> int:
    """把校對結果寫進 data/zh_curated_local.json（tag → 名字；None／空＝看過、沒有可靠的中文名）。"""
    import json
    d = _read_json(local_path())
    tbl = d.setdefault("names", {})
    for k, v in items.items():
        tbl[k] = (v or "").strip() or None
    d["names"] = dict(sorted(tbl.items()))
    p = local_path()
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(d, ensure_ascii=False, indent=0), "utf-8")
    tmp.replace(p)
    _tables.cache_clear()
    try:
        from . import nameindex
        nameindex.invalidate()
    except Exception:  # noqa
        pass
    return len(items)


# 變身後的名字（cure_peace、sailor_mercury）：中文資料常只有本名 → 顯示「Cure Peace（黃瀨彌生）」，本名、變身名都看得到（b110.7）
TRANSFORM = re.compile(r"^(cure_[a-z]+(_[a-z]+)?|dark_precure|(super_|eternal_)?sailor_(moon|chibi_moon|mercury|mars|jupiter|venus|"
                       r"saturn|uranus|neptune|pluto|star_[a-z]+))$")


def display(name: str, other_names: list[str] | None, manual: str | None, lang: str, use_curated: bool = True,
            cn: bool | None = None, series: bool = False) -> str:
    """顯示名稱（_display_core）＋兩個補充：變身名附本名；不同作品同名（紫苑、梅、背包）時後面加作品名（dupnames）。"""
    d = _display_core(name, other_names, manual, lang, use_curated, cn, series)
    if lang != "zh" or manual or series:
        return d
    if TRANSFORM.match(name or "") and HAN_CHAR.search(d) and not re.search(r"[A-Za-z]", d):
        d = f"{pretty_raw(name)}（{d}）"
    try:
        from . import dupnames
        d = dupnames.suffix(name, d, mode() == "cn" if cn is None else cn)
    except Exception:  # noqa  還沒算好
        pass
    return d


def _display_core(name: str, other_names: list[str] | None, manual: str | None, lang: str, use_curated: bool = True,
                  cn: bool | None = None, series: bool = False) -> str:
    """你取的名字 > 校對過的譯名表 > 寶可夢官方譯名 > Bangumi／Wikidata（或造型＝本體＋造型）> Danbooru 中文別名 > 英文。
    中文模式沒有中文名時用英文、不用日文（使用者要求：日文看不懂，英文至少念得出來）。
    簡體模式（zh_mode＝cn）：同樣的順序，換成大陸譯名（_display_cn）。"""
    if lang == "zh" and (mode() == "cn" if cn is None else cn):
        return _display_cn(name, other_names, manual, use_curated, series)
    if manual:
        return manual
    if lang == "zh":
        cu = curated(name) if use_curated else None
        if cu:
            return cu
        fz = franchise_zh(name)          # 官方譯名（FGO 台服、寶可夢人物）
        if fz:
            return fz
        pk = pokemon_zh(name or "", other_names)
        if pk:
            return pk
        au = _auto(name)
        if au:
            return au
    if lang in ("zh", "ja"):
        variant = bool(VARIANT.match(name or ""))
        p = pick(other_names or [], lang, variant, char=not series)
        if p:
            return p
    return pretty_raw(name) if lang != "raw" else name


def _bgm_cn(name: str) -> str | None:
    try:
        from . import zhname
        return zhname.auto_map_cn().get(name or "")
    except Exception:  # noqa
        return None


def _display_cn(name: str, other_names: list[str] | None, manual: str | None, use_curated: bool = True,
                series: bool = False) -> str:
    """簡體模式的角色名：你取的名字（轉簡體字）> 校對過的譯名（Bangumi 原文跟它一樣就用原文，不然轉簡體字）
    > 寶可夢官方簡中譯名 > Bangumi 原文（大陸譯名）> Danbooru 中文別名 > 英文。"""
    if manual:
        return to_simp(manual)
    bgm = _bgm_cn(name)
    cu = curated(name) if use_curated else None
    if cu:
        return bgm if bgm and to_trad(bgm) == cu else to_simp(cu)
    fz = franchise_zh(name, cn=True)     # 官方譯名（FGO 國服、寶可夢人物）
    if fz:
        return fz
    pk = pokemon_zh(name or "", other_names, cn=True)
    if pk:
        return pk
    if bgm:
        return bgm
    p = pick(other_names or [], "zh", bool(VARIANT.match(name or "")), char=not series)
    return to_simp(p) if p else pretty_raw(name)


# ---------- 作品 ----------
_WIKI: dict = {"at": 0.0, "map": {}}


def _wiki_auto(key: str) -> str | None:
    """維基百科查到的作品台灣譯名（series_zh 表，wikiname.py）：整張表放記憶體，30 秒重讀一次。"""
    import time as _t
    if _t.time() - _WIKI["at"] > 30:
        try:
            from . import db
            _WIKI["map"] = {r[0]: r[1] for r in db.conn().execute("SELECT name, zh FROM series_zh WHERE zh IS NOT NULL")}
        except Exception:  # noqa  資料表還沒建
            _WIKI["map"] = {}
        _WIKI["at"] = _t.time()
    return _WIKI["map"].get(key)
@lru_cache(maxsize=1)
def _series_zh() -> dict:
    import json
    from pathlib import Path
    try:
        return json.loads((Path(__file__).parent / "data" / "series_zh.json").read_text("utf-8"))
    except Exception:
        return {}


def series_display(key: str, other_names: list[str] | None, manual: str | None, lang: str, auto: str | None = None,
                   use_curated: bool = True, cn: bool | None = None) -> str:
    """作品名稱：手動 > 校對過的譯名表 > 內建中文對照表（台灣譯名）> 維基百科查到的台灣譯名 > Bangumi > Danbooru 別名 > 羅馬拼音。
    auto（維基）沒傳的呼叫端（以前有好幾個）：從快取拿，不然同一部作品在不同畫面名字不一樣。"""
    if lang == "zh" and (mode() == "cn" if cn is None else cn):
        return _series_cn(key, other_names, manual, use_curated)
    if manual:
        return manual
    if lang == "zh":
        cu = curated(key) if use_curated else None
        if cu:
            return cu
        if auto is None:
            auto = _wiki_auto(key)
        zh = _series_zh().get(key) or auto
        if zh:
            return zh
    return display(key, other_names, None, lang, use_curated, cn=False, series=True)


@lru_cache(maxsize=1)
def _series_cn_table() -> dict:
    """內建表、校對表裡的作品 → [台灣譯名, 維基百科的「大陆简体」標題]（app/data/series_cn.json）。"""
    from pathlib import Path
    return _read_json(Path(__file__).parent / "data" / "series_cn.json").get("names", {})


_WIKI_CN: dict = {"at": 0.0, "map": {}}


def _wiki_cn(key: str) -> str | None:
    """維基百科查到的作品大陸譯名（series_zh.cn，wikiname.py）。"""
    import time as _t
    if _t.time() - _WIKI_CN["at"] > 30:
        try:
            from . import db
            _WIKI_CN["map"] = {r[0]: r[1] for r in db.conn().execute("SELECT name, cn FROM series_zh WHERE cn IS NOT NULL")}
        except Exception:  # noqa  資料表還沒建、還沒有 cn 欄
            _WIKI_CN["map"] = {}
        _WIKI_CN["at"] = _t.time()
    return _WIKI_CN["map"].get(key)


def _series_cn(key: str, other_names: list[str] | None, manual: str | None, use_curated: bool = True) -> str:
    """簡體模式的作品名：你取的名字（轉簡體字）> 維基百科大陸標題（內建表、查到的）> 台灣譯名轉簡體字 > Bangumi 原文 > 角色規則。"""
    if manual:
        return to_simp(manual)
    wiki_tw = _wiki_auto(key)
    tw = (curated(key) if use_curated else None) or _series_zh().get(key) or wiki_tw
    built = _series_cn_table().get(key)          # [當時的台灣譯名, 大陸譯名]：台灣譯名改過了（你重新校對）就不用
    if built and built[0] == tw:
        return built[1]
    if tw and tw == wiki_tw and _wiki_cn(key):
        return _wiki_cn(key)
    if tw:
        return to_simp(tw)
    return _display_cn(key, other_names, None, use_curated, series=True)


def _base(k: str) -> str:
    return re.sub(r"_\(series\)$", "", k)


# 可以當「母系列」的作品：Danbooru 的 xxx_(series) 標籤，加上這幾個沒有 _(series) 後綴的大系列
FRANCHISES = {"bang_dream!", "love_live!", "idolmaster", "precure", "persona", "pokemon", "gundam", "digimon",
              "touhou", "final_fantasy", "dragon_quest", "yu-gi-oh!", "honkai", "kantai_collection", "vocaloid"}
# 這幾個系列的子作品名稱是「xxx_precure」這種放在後面的
SUFFIX_FRANCHISES = {"precure", "idolmaster", "gundam"}


@lru_cache(maxsize=64)
def _parent_candidates(keys: frozenset) -> tuple:
    """keys 裡可以當母系列的：(名稱, 去掉 _(series) 的前綴)。同一組 keys 只算一次。"""
    out = []
    for p in keys:
        if p.endswith("_(series)") or p in FRANCHISES:
            b = _base(p)
            if len(b) >= 4:
                out.append((p, b))
    return tuple(out)


def independent() -> frozenset:
    """設定成「獨立顯示」的子作品（作品頁開關；預設：明日方舟：終末地）。"""
    from . import db
    s = db._settings_cache if db._settings_cache is not None else db.get_settings()
    return frozenset(s.get("series_independent") or ())


def series_parent(key: str, keys: set[str], ignore_independent: bool = False) -> str | None:
    """（b110）設定成獨立顯示的子作品（終末地）不併入母系列：自己一個作品頁、自己的角色、series: 也只搜自己。
    ignore_independent：問「本來屬於哪個系列」（作品頁的「相關」連結用）。"""
    if not ignore_independent and key in independent():
        return None
    return _series_parent(key, keys)


def _series_parent(key: str, keys: set[str]) -> str | None:
    """同一個系列的子作品 → 母系列（只併到你圖庫裡本來就有的母系列）。保守：
    母系列必須是 xxx_(series) 或上面列出的大系列，子作品要以母系列名開頭（fate/grand_order → fate_(series)、
    bang_dream!_it's_mygo!!!!! → bang_dream!、honkai:_star_rail → honkai_(series)）；
    只有光之美少女、偶像大師、鋼彈允許名字放在後面（hirogaru_sky!_precure、gakuen_idolmaster）。"""
    # 先看 Danbooru 的 tag implication（limbus_company → project_moon）：取圖庫裡有的、最上層的母系列
    try:
        from . import seriesmap
        up = [a for a in seriesmap.ancestors(key) if a in keys]
        if up:
            return up[-1]
    except Exception:  # noqa  資料表還沒建好等：退回名字規則
        pass
    best = None
    for p, b in _parent_candidates(keys if isinstance(keys, frozenset) else frozenset(keys)):
        if p == key:
            continue
        ok = key.startswith((b + "_", b + "/", b + ":"))
        if not ok and b in SUFFIX_FRANCHISES:
            ok = key.endswith(("_" + b, "_" + b + "!"))
        if ok and (best is None or len(p) < len(best)):   # 取最上層
            best = p
    return best


VARIANT = re.compile(r"^(.+)_\(([^()]+)\)_\(([^()]+)\)$")
SIMPLE_VARIANT = re.compile(r"^(.+)_\(([^()]+)\)$")


def base_of(name: str, existing: set[str]) -> str | None:
    """若 name 是某個已存在角色的造型，回傳本體名稱。"""
    m = VARIANT.match(name)
    if m:
        base = f"{m.group(1)}_({m.group(3)})"
        if base in existing:
            return base
    m = SIMPLE_VARIANT.match(name)
    if m and m.group(1) in existing:  # hatsune_miku_(append) → hatsune_miku
        return m.group(1)
    return None
