"""搜尋語法 → SQL。先把 tag 名稱解析成 tag id（精確 / 萬用字元 / 別名 / 中文對照），再組 EXISTS，比逐列 JOIN 名稱快很多。

語法（空白 = AND）：
  hatsune_miku  初音        tag 名稱、資料卡別名、一般 tag 的中文對照
  -tag                       排除
  a|b   或   a OR b          任一個
  miku*                      萬用字元
  rating:safe / nsfw / g s q e
  type:chibi（媒材）  type:anime / type:manga / type:novel / type:movie（官方：動畫、漫畫截圖、小說插圖、電影截圖）  is:official  is:video / is:image / is:noartist / is:unsourced / is:sourced / is:multi
  date:2024  date:2024-05  date:>=2024-01-01  date:2023..2024-06   （拍攝或檔案時間）
  added:7d  added:2024-05   （加入圖庫的時間）
  w:>=1920  h:<800  ratio:>1.5  ratio:portrait|landscape|square
  chars:>=2（已確認角色數）  path:資料夾  series:作品  src:danbooru|pixiv|none
  is:noanime（非動漫：照片＋截圖＋梗圖＋貼圖）  is:anime  is:real  is:screenshot  is:meme
"""
from __future__ import annotations

import datetime
import json
import re
import time
from functools import lru_cache
from pathlib import Path

from . import db

NSFW = db.nsfw_expr("m")
IDENTITY = ("character", "artist", "copyright")
# 身分標籤（角色/創作者/作品）只算「已確認」；一般描述 tag 自動的也算
OK = "x.state >= (CASE WHEN t.category IN ('character','artist','copyright') THEN 1 ELSE 0 END)"


def _has(cat: str, extra: str = "") -> str:
    # IN (子查詢) 只算一次，比每列都跑一次 EXISTS 快很多
    return f"""m.id IN (SELECT x.media_id FROM media_tags x JOIN tags t ON t.id=x.tag_id
               WHERE t.category='{cat}' AND x.state=1 {extra})"""


_ORIGINAL = "AND t.name='original'"
# 「缺創作者」：沒有創作者標籤，而且——
# 官方作品（動畫截圖、漫畫截圖、小說插圖）都當作有作者（使用者要求）；按過「作者不可考」的、
# 標了「作者不可考」類標籤（梗圖、表情包、截圖；設定可改）的也不算
_NO_ARTIST = f"""NOT {_has('artist')} AND COALESCE(m.official,'')='' AND COALESCE(m.artist_done,0)=0
      AND m.id NOT IN (SELECT x.media_id FROM media_tags x WHERE x.state=1 AND x.tag_id IN (SELECT id FROM tags WHERE no_artist=1))"""
MISSING_RAW = {
    # 「完全未識別」的「沒有創作者」用同一個規則：標了動畫／漫畫截圖＝有作者，不該還在這裡（使用者回報）
    "all": f"NOT {_has('character')} AND ({_NO_ARTIST}) AND NOT {_has('copyright')}",
    "character": f"NOT {_has('character')} AND NOT {_has('copyright', _ORIGINAL)}",
    "artist": _NO_ARTIST,
    # 多人圖還缺角色：人數（描述標籤推估）≥ 2、標的角色比人數少、還沒按「角色標完了」（見 people.py）
    # 寫成不相關子查詢（整個圖庫只算一次）：每張圖各算一次角色數要 2.4 秒，這樣 4 ms
    # Danbooru／Pixiv 已經標好角色的圖不算：那邊標的就是全部有名字的角色，WD 多數到的人基本上是路人（使用者要求）
    # 角色數：同一個角色的本體和造型（asuna_(blue_archive) ＋ asuna_(bunny)_(blue_archive)）只算一個（char_base，見 people.py）
    "people": """m.id IN (SELECT m2.id FROM media m2 JOIN (SELECT x.media_id, COUNT(DISTINCT COALESCE(cb.base_id, x.tag_id)) n FROM media_tags x
      LEFT JOIN char_base cb ON cb.tag_id=x.tag_id
      WHERE x.state=1 AND x.tag_id IN (SELECT id FROM tags WHERE category='character') GROUP BY x.media_id) k ON k.media_id=m2.id
      WHERE m2.people >= 2 AND COALESCE(m2.people_done,0) = 0 AND k.n < m2.people)
      AND m.id NOT IN (SELECT media_id FROM media_source WHERE status='found' AND method IN ('md5','pixel_hash','iqdb','pixiv','pixiv_id')
                       UNION SELECT x.media_id FROM media_tags x WHERE x.state=1 AND x.source IN ('danbooru','pixiv')
                         AND x.tag_id IN (SELECT id FROM tags WHERE category='character'))""",
}
SITE_TAGGED_METHODS = ("md5", "pixel_hash", "iqdb", "pixiv", "pixiv_id")
OFFICIAL = {"anime": "anime", "動畫": "anime", "動畫截圖": "anime", "manga": "manga", "漫畫截圖": "manga",
            "novel": "novel", "小說": "novel", "小說插圖": "novel",
            "movie": "movie", "film": "movie", "電影": "movie", "電影截圖": "movie"}
SKIP = "COALESCE(m.unrec_skip,0)"
# 候選（state=0）有 20 萬筆以上，幾乎都是一般描述 tag；先限定「身分 tag 的 id」，SQLite 才會走 (tag_id, state) 索引，
# 只看幾百筆身分候選，不用掃過全部候選再篩類別（實測 72ms → 5ms）
IDENT_IDS = "(SELECT id FROM tags WHERE category IN ('character','artist','copyright'))"
# 未識別的待辦：按過「略過」的不算
# 作品集裡的圖也不算待辦（隔絕：整本的標籤一次處理，不要每頁都出現在缺角色／缺創作者；使用者要求 2026-10-05）
MISSING = {k: f"{v} AND {SKIP}=0 AND m.id NOT IN (SELECT media_id FROM album_items)" for k, v in MISSING_RAW.items()}
_PENDING_ID = f"""m.id IN (SELECT x.media_id FROM media_tags x WHERE x.state=0 AND x.tag_id IN {IDENT_IDS})"""
# 未識別頁的分流
BUCKETS = {
    "cand": _PENDING_ID,
    "nocand": f"NOT {_PENDING_ID}",
    "unsearched": "m.kind='image' AND m.id NOT IN (SELECT media_id FROM media_source)",
    "video": "m.kind='video'",
}
# 缺創作者的分流：原創作品（標了 original、沒有作者，以前混在五千多張裡找不到）、AI 生成
BUCKETS.update({
    "orig": "m.id IN (SELECT x.media_id FROM media_tags x JOIN tags t ON t.id=x.tag_id WHERE t.name='original' AND x.state=1)",
    "aigen": "COALESCE(m.ai_gen,'')<>''",
})
# 分堆（piles.py）：p_meme、p_texty…
BUCKETS.update({f"p_{k}": f"m.pile='{k}'" for k in ("meme", "texty", "photo", "nohuman", "comic", "video", "illust")})

SORTS = {
    "new": "m.added_at DESC, m.id DESC",
    "old": "m.added_at ASC, m.id ASC",
    "taken": "COALESCE(m.taken_at, m.mtime) DESC, m.id DESC",
    "suspicious": "(mt.state=1) ASC, mt.score ASC, m.id",
    "confident": "mt.score DESC, m.id",
    "tier": "COALESCE(mt.tier, 9) ASC, mt.score DESC, m.id",
    "path": "m.path, m.id",
    "size": "(COALESCE(m.width,0) * COALESCE(m.height,0)) DESC, m.id",
    "random": "RANDOM()",
    # 你最近動過的（確認、否決、加標籤、略過、ascii2d…）在前、沒動過的在後；倒序就是整個倒過來
    # （以前倒序時「沒動過的」還是固定放最後，結果動過的那幾百張正序倒序都擠在最前面——使用者回報）
    "edited": "(m.edited_at IS NULL) ASC, m.edited_at DESC, m.id DESC",
}


def _split_order(expr: str) -> list[str]:
    out, depth, cur = [], 0, ""
    for ch in expr:
        depth += (ch == "(") - (ch == ")")
        if ch == "," and depth == 0:
            out.append(cur.strip()); cur = ""
        else:
            cur += ch
    if cur.strip():
        out.append(cur.strip())
    return out


def order_by(expr: str, reverse: bool = False, sort: str = "") -> str:
    """排序的 ORDER BY；reverse＝倒過來（每一段 ASC↔DESC）。每個排序最後都有 m.id 決勝負，所以倒序剛好是正序整個反過來。"""
    if reverse and expr != "RANDOM()":
        parts = []
        for p in _split_order(expr):
            u = p.upper()
            parts.append(p[:-5] + " ASC" if u.endswith(" DESC") else p[:-4] + " DESC" if u.endswith(" ASC") else p + " DESC")
        expr = ", ".join(parts)
    return expr

RATING = {"g": ["general"], "general": ["general"], "s": ["sensitive"], "sensitive": ["sensitive"],
          "q": ["questionable"], "questionable": ["questionable"], "e": ["explicit"], "explicit": ["explicit"]}

# ---------- 一般 tag 中文對照 ----------
DICT_PATH = Path(__file__).resolve().parent / "data" / "general_zh.json"


@lru_cache(maxsize=1)
def zh_dict() -> dict[str, str]:
    try:
        return json.loads(DICT_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


@lru_cache(maxsize=1)
def zh_reverse() -> dict[str, list[str]]:
    from . import zhui
    rev: dict[str, list[str]] = {}
    for k, v in zh_dict().items():
        for part in re.split(r"[／/、]", v):
            part = part.strip()
            for w in dict.fromkeys((part, zhui.conv(part))):    # 簡體模式畫面上看到的寫法也收（「影片」→「视频」）
                rev.setdefault(w, []).append(k)
    return rev


def zh_of(name: str) -> str | None:
    return zh_dict().get(name)


# ---------- 解析 ----------
def tokenize(q: str) -> list[list[str]]:
    """回傳 AND 的清單，每一項是 OR 的選項。"""
    raw = (q or "").split()
    groups: list[list[str]] = []
    i = 0
    while i < len(raw):
        t = raw[i]
        if t.upper() == "OR" and groups and i + 1 < len(raw):
            groups[-1] += [x for x in raw[i + 1].split("|") if x]
            i += 2
            continue
        parts = [x for x in t.split("|") if x] if "|" in t and not t.startswith(("path:",)) else [t]
        if parts:
            groups.append(parts)
        i += 1
    return groups


def _like(s: str) -> str:
    """LIKE 用的字面文字：% 和 _ 不當萬用字元（以前搜「%」會比對到每一張、還要 14 秒）。配合 ESCAPE '\\'。"""
    return s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def resolve_tags(tok: str) -> tuple[list[int], list[int]]:
    """tag 文字 → (身分類 tag id, 其他 tag id)。"""
    c = db.conn()
    ids: set[int] = set()
    name = tok.replace(" ", "_")
    if "*" in name:
        pat = _like(name).replace("*", "%")
        ids |= {r[0] for r in c.execute("SELECT id FROM tags WHERE name LIKE ? ESCAPE '\\' LIMIT 500", (pat,))}
    else:
        tid = db.tag_id(name, create=False)
        if tid:
            ids.add(tid)
        else:
            # 任何看得到的名字：顯示名稱、簡繁體、去掉括號的本名、空白代替底線（nameindex.py）
            # 剛好就是某個標籤的原名（frieren）→ 只要那個標籤，不要連作品別名裡的「Frieren」一起算
            from . import nameindex
            ids |= nameindex.exact(tok)
    # 資料卡別名（中文/日文名、合併過的舊名稱）：非英文可以只打一部分（「初音」→ 初音ミク）
    if not tok.isascii() and len(tok) >= 2 and "*" not in tok:
        ids |= {r[0] for r in c.execute("SELECT tag_id FROM tag_info WHERE other_names LIKE ? ESCAPE '\\'", (f"%{_like(tok)}%",))}
    elif tok.isascii() and not ids:
        ids |= {r[0] for r in c.execute("SELECT tag_id FROM tag_info WHERE other_names LIKE ? ESCAPE '\\'", (f'%"{_like(tok)}"%',))}
    # 一般 tag 的中文對照
    if not tok.isascii():
        rev = zh_reverse()
        for zh, en in rev.items():
            if tok == zh or (len(tok) >= 2 and tok in zh):
                for n in en:
                    tid = db.tag_id(n, create=False)
                    if tid:
                        ids.add(tid)
    if not ids:
        return [], []
    ph = ",".join("?" * len(ids))
    ident, other = [], []
    for r in c.execute(f"SELECT id, category FROM tags WHERE id IN ({ph})", list(ids)):
        (ident if r[1] in IDENTITY else other).append(r[0])
    return ident, other


def _num(expr: str) -> tuple[str, float] | None:
    m = re.fullmatch(r"(>=|<=|>|<|=)?([\d.]+)", expr)
    if not m:
        return None
    return m.group(1) or "=", float(m.group(2))


def _date_range(v: str) -> tuple[float | None, float | None] | None:
    """2024 / 2024-05 / 2024-05-01 / >=2024-01 / 2023..2024-06 / 7d → (起, 迄) timestamp。"""
    def start_end(s: str):
        for fmt, step in (("%Y-%m-%d", "d"), ("%Y-%m", "m"), ("%Y", "y")):
            try:
                d = datetime.datetime.strptime(s, fmt)
            except ValueError:
                continue
            if step == "d":
                e = d + datetime.timedelta(days=1)
            elif step == "m":
                e = d.replace(year=d.year + (d.month == 12), month=d.month % 12 + 1)
            else:
                e = d.replace(year=d.year + 1)
            return d.timestamp(), e.timestamp()
        return None
    m = re.fullmatch(r"(\d+)([dwmy])", v)
    if m:
        n, u = int(m.group(1)), m.group(2)
        return time.time() - n * {"d": 1, "w": 7, "m": 30, "y": 365}[u] * 86400, None
    if ".." in v:
        a, b = v.split("..", 1)
        ra, rb = (start_end(a) if a else None), (start_end(b) if b else None)
        return (ra[0] if ra else None), (rb[1] if rb else None)
    m = re.fullmatch(r"(>=|<=|>|<)(.+)", v)
    if m:
        r = start_end(m.group(2))
        if not r:
            return None
        return {">=": (r[0], None), ">": (r[1], None), "<": (None, r[0]), "<=": (None, r[1])}[m.group(1)]
    return start_end(v)


def _gen(names: tuple[str, ...], thr: float) -> str:
    """一般描述 tag（WD Tagger）有任何一個分數 ≥ thr。手動加的（分數空白）算 1。"""
    ns = ",".join(f"'{n}'" for n in names)
    return f"""EXISTS (SELECT 1 FROM media_tags x JOIN tags t ON t.id=x.tag_id WHERE x.media_id=m.id
               AND t.name IN ({ns}) AND x.state>=0 AND COALESCE(x.score, 1)>={thr})"""


# ---------- 非動漫圖判斷（在使用者的 iCloud 圖上抽樣校正過）----------
# 有確認的角色、或 AI 很確定是某角色 → 當作動漫圖（動畫截圖也算）
_HAS_CHAR = """EXISTS (SELECT 1 FROM media_tags x JOIN tags t ON t.id=x.tag_id WHERE x.media_id=m.id
               AND t.category='character' AND (x.state=1 OR (x.state=0 AND COALESCE(x.score, 0)>=0.6)))"""
_REAL = ("(m.media_type IN ('photo','cosplay','figure') OR "
         + _gen(("realistic", "photo_(medium)", "photorealistic"), 0.35) + ")")
_SHOT = f"({_gen(('screenshot', 'fake_screenshot', 'user_interface'), 0.5)} AND NOT {_HAS_CHAR})"
_MEME = f"({_gen(('meme', 'text_focus'), 0.5)} AND NOT {_HAS_CHAR})"
_NOHUMAN = (f"({_gen(('no_humans',), 0.5)} AND NOT {_gen(('1girl', '1boy', '2girls', '2boys', 'multiple_girls', 'multiple_boys'), 0.35)}"
            f" AND NOT {_HAS_CHAR})")
_NOANIME = f"(m.kind='image' AND m.status='done' AND ({_REAL} OR {_SHOT} OR {_MEME} OR {_NOHUMAN}))"
NOT_ANIME = {
    "real": _REAL, "photo": _REAL, "照片": _REAL, "真人": _REAL,
    "screenshot": _SHOT, "截圖": _SHOT,
    "meme": _MEME, "梗圖": _MEME,
    "noanime": _NOANIME, "nonanime": _NOANIME, "非動漫": _NOANIME,
    "anime": f"(m.kind='image' AND m.status='done' AND NOT {_NOANIME})", "動漫": f"(m.kind='image' AND m.status='done' AND NOT {_NOANIME})",
}


MTYPE_ALIAS = {"插畫": "illustration", "q版": "chibi", "漫畫": "comic", "公仔": "figure", "娃娃": "figure", "真人照片": "photo",
               "截圖": "other", "文字": "other", "未定": "unsure", "text": "other"}

_CP_CACHE: dict = {}


def _copyrights() -> list[str]:
    """所有角色資料卡上的作品（30 秒快取：作品頁切換角色時不用每次重查）"""
    if time.time() - _CP_CACHE.get("at", 0) > 30:
        _CP_CACHE["v"] = [r[0] for r in db.conn().execute("""SELECT copyright FROM tag_info WHERE copyright IS NOT NULL
                                                               UNION SELECT name FROM tags WHERE category='copyright'""")]
        _CP_CACHE["at"] = time.time()
    return _CP_CACHE["v"]


def _term(tok: str) -> tuple[str, list] | None:
    """單一條件 → (SQL, 參數)；None = 找不到任何東西（整個條件為假）。"""
    low = tok.lower()
    key, _, val = low.partition(":")
    if _ and key in ("rating", "r"):
        if val in ("nsfw", "unsafe"):
            return f"{NSFW}=1", []
        if val in ("safe", "sfw"):
            return f"{NSFW}=0", []
        rs = [x for v in val.split(",") for x in RATING.get(v, [])]
        return (f"m.rating IN ({','.join('?' * len(rs))})", rs) if rs else ("0", [])
    if _ and key in ("type", "媒材"):
        if val in OFFICIAL:
            return "m.official=?", [OFFICIAL[val]]
        return mtype_cond(MTYPE_ALIAS.get(val, val))
    if _ and key in ("date", "taken", "added"):
        r = _date_range(val)
        if not r:
            return "0", []
        col = "m.added_at" if key == "added" else "COALESCE(m.taken_at, m.mtime)"
        w, p = [], []
        if r[0] is not None:
            w.append(f"{col}>=?")
            p.append(r[0])
        if r[1] is not None:
            w.append(f"{col}<?")
            p.append(r[1])
        return (" AND ".join(w) or "1"), p
    if _ and key in ("w", "width", "h", "height"):
        n = _num(val)
        col = "m.width" if key.startswith("w") else "m.height"
        return (f"{col}{n[0]}?", [n[1]]) if n else ("0", [])
    if _ and key == "ratio":
        named = {"portrait": ("<", 0.9), "landscape": (">", 1.1), "square": None, "直": ("<", 0.9), "橫": (">", 1.1)}
        expr = "(CAST(m.width AS REAL) / NULLIF(m.height, 0))"
        if val in ("square", "方"):
            return f"{expr} BETWEEN 0.9 AND 1.1", []
        n = named.get(val) or _num(val)
        return (f"{expr}{n[0]}?", [n[1]]) if n else ("0", [])
    if _ and key in ("chars", "characters"):
        n = _num(val)
        if not n:
            return "0", []
        # 以前每張圖各數一次（11 秒）；改成整個圖庫只數一次。條件包含 0 個（chars:0、chars:<2）時用 NOT IN
        op, v = n
        ok0 = {"=": 0 == v, ">": 0 > v, "<": 0 < v, ">=": 0 >= v, "<=": 0 <= v}[op]
        inner = f"""SELECT x.media_id FROM media_tags x LEFT JOIN char_base cb ON cb.tag_id=x.tag_id
                    WHERE x.state=1 AND x.tag_id IN (SELECT id FROM tags WHERE category='character')
                    GROUP BY x.media_id HAVING COUNT(DISTINCT COALESCE(cb.base_id, x.tag_id))"""
        if ok0:
            neg = {"=": "!=", ">": "<=", "<": ">=", ">=": "<", "<=": ">"}[op]
            return f"m.id NOT IN ({inner}{neg}?)", [v]
        return f"m.id IN ({inner}{op}?)", [v]
    if _ and key == "path":
        return "m.path LIKE ? ESCAPE '\\'", [f"%{_like(tok[5:])}%"]
    if _ and key in ("series", "作品"):
        v = tok.split(":", 1)[1]
        # 母系列也包含子作品（Fate 系列含 FGO）；IN (子查詢) 只算一次，比每張圖跑一次 EXISTS 快很多
        return _series_sql([v])
    if _ and key == "ai":           # ai:any、ai:comfyui（AI 生成的圖，aigen.py）
        return ("COALESCE(m.ai_gen,'')<>''", []) if val in ("any", "yes", "1", "") else ("LOWER(m.ai_gen) LIKE ?", [f"%{val}%"])
    if _ and key == "album":        # album:12（某一本）、is:album／is:noalbum 見下面
        try:
            return "m.id IN (SELECT media_id FROM album_items WHERE album_id=?)", [int(val)]
        except ValueError:
            return "m.id IN (SELECT ai.media_id FROM album_items ai JOIN albums a ON a.id=ai.album_id WHERE a.title LIKE ? ESCAPE '\\')", [f"%{_like(tok.split(':', 1)[1])}%"]
    if _ and key in ("prompt", "model", "lora"):   # AI 生成資料：prompt:hatsune、model:animagine、lora:xxx
        col = {"prompt": "prompt", "model": "model", "lora": "loras"}[key]
        v = tok.split(":", 1)[1]
        # 提示詞裡的名字可能寫 hatsune_miku 也可能寫 hatsune miku：兩種都找（搜尋框的空白是「而且」，所以用底線打）
        return (f"m.id IN (SELECT media_id FROM ai_meta WHERE {col} LIKE ? ESCAPE '\\' OR {col} LIKE ? ESCAPE '\\')",
                [f"%{_like(v)}%", f"%{_like(v.replace('_', ' '))}%"])
    if _ and key == "guess":        # guess:表情包：AI 從你的標記學、覺得也是、你還沒標的（qtlearn.py，b93）
        from . import qtlearn
        return qtlearn.guess_sql(tok.split(":", 1)[1])
    if _ and key == "pile":         # 未識別的分堆（piles.py）：pile:meme、pile:texty…
        return "m.pile=?", [val]
    if _ and key == "tag":          # 只比對這一個標籤的原名（不查別名、中文）：-tag:梗圖 ＝還沒標梗圖
        tid = db.tag_id(tok.split(":", 1)[1].replace(" ", "_"), create=False)
        return (f"m.id IN (SELECT media_id FROM media_tags WHERE tag_id={int(tid)} AND state=1)", []) if tid else ("0", [])
    if _ and key == "src":
        if val == "none":
            return "NOT EXISTS (SELECT 1 FROM media_source s WHERE s.media_id=m.id AND s.status='found')", []
        return ("""EXISTS (SELECT 1 FROM media_source s WHERE s.media_id=m.id AND s.status='found'
                   AND (s.method=? OR s.data LIKE ?))""", [val, f'%"{val}"%'])
    if _ and key == "is":
        if val in ("artist", "noartist"):
            s = _has("artist")
            return (s if val == "artist" else f"NOT {s}"), []
        if val in ("official", "官方"):
            return "COALESCE(m.official,'')<>''", []
        if val in ("sourced", "unsourced"):
            s = "EXISTS (SELECT 1 FROM media_source s WHERE s.media_id=m.id)"
            return (s if val == "sourced" else f"NOT {s}"), []
        if val == "icloud":
            return "(m.path LIKE '%iCloud Photos%' OR m.path LIKE '%iCloudPhotos%' OR m.path LIKE '%iCloud Drive%' OR m.path LIKE '%iCloudDrive%')", []
        if val in ("video", "image"):
            return "m.kind=?", [val]
        if val in NOT_ANIME:
            return NOT_ANIME[val], []
        if val in ("album", "noalbum"):
            s2 = "m.id IN (SELECT media_id FROM album_items)"
            return (s2 if val == "album" else f"NOT {s2}"), []
        if val in ("nochar", "nocharacter"):     # 沒有確認的角色（作品頁「沒標角色」）
            return f"NOT {_has('character')}", []
        if val in ("char", "character"):
            return _has("character"), []
        if val == "missartist":                 # 跟「缺創作者」同一個條件（不算官方截圖、不用找作者的）
            return MISSING_RAW["artist"], []
        if val in ("aigen", "ai", "generated"):
            return "COALESCE(m.ai_gen,'')<>''", []
        if val in ("artistdone", "noartistneeded", "unknownartist", "作者不可考"):
            return """(COALESCE(m.artist_done,0)=1 OR m.id IN (SELECT x.media_id FROM media_tags x WHERE x.state=1
                      AND x.tag_id IN (SELECT id FROM tags WHERE no_artist=1)))""", []
        if val in ("multi", "group"):
            return _term("chars:>=2")
        if val in ("upgrade", "hires", "高清"):        # 有更高清的版本可以換（upgrade.py）
            return "m.id IN (SELECT media_id FROM upgrade WHERE status='offer') AND m.id NOT IN (SELECT media_id FROM album_items)", []
        if val in ("upgraded", "換過高清"):
            return "m.id IN (SELECT media_id FROM upgrade WHERE status='done')", []
        if val in ("candidate", "unchecked"):
            return _PENDING_ID, []
        return "0", []
    ident, other = resolve_tags(tok)
    if not ident and not other:
        return None
    w, pre = [], []
    # 作品：跟作品頁同一個算法——有作品標籤的圖，加上已確認角色屬於這部作品（含子作品）的圖
    # （以前搜「蔚藍檔案」198 張、作品頁卻是 693 張）
    cps = [r[0] for r in db.conn().execute(
        f"SELECT name FROM tags WHERE category='copyright' AND id IN ({','.join(map(str, ident))})")] if ident else []
    if cps:
        pre.append(_series_sql(cps))
    if ident:
        w.append(f"(x.tag_id IN ({','.join(map(str, ident))}) AND x.state=1)")
    if other:
        w.append(f"(x.tag_id IN ({','.join(map(str, other))}) AND x.state>=0)")
    ex = f"EXISTS (SELECT 1 FROM media_tags x WHERE x.media_id=m.id AND ({' OR '.join(w)}))"
    if pre:
        return "(" + " OR ".join([pre[0][0], ex]) + ")", pre[0][1]
    return ex, []


def _series_sql(keys: list[str]) -> tuple[str, list]:
    from . import family, names
    subs = list(dict.fromkeys(keys + [k for k in _copyrights() if k not in keys and any(names.series_parent(k, {v}) == v for v in keys)]))
    groups = family.groups_cached()
    # b110 獨立顯示的子作品（終末地）：子作品頁只放有子作品證據的圖（見 family.py）
    own = [g for g in groups if g["child"] in subs]
    rest = [k for k in subs if k not in {g["child"] for g in own}]
    parts, p = [], []
    if rest:
        ph = ",".join("?" * len(rest))
        parts.append(f"""m.id IN (SELECT x.media_id FROM media_tags x JOIN tags t ON t.id=x.tag_id LEFT JOIN tag_info ti ON ti.tag_id=t.id
               WHERE x.state=1 AND (ti.copyright IN ({ph}) OR t.name IN ({ph})))""")
        p += [*rest, *rest]
    for g in own:
        q, qp = family.child_evidence_sql(g)
        parts.append(q)
        p += qp
    q = "(" + " OR ".join(parts) + ")" if parts else "0"
    # 母系列頁：子作品的圖（有子作品證據）除非也有母系列自己的角色（同框）
    for g in groups:
        if g["parent"] in subs and g["child"] not in subs:
            ce, cp = family.child_evidence_sql(g)
            po, pp = family.parent_only_char_sql(g, subs)
            q = f"({q} AND NOT ({ce} AND NOT {po}))"
            p += [*cp, *pp]
    return q, p


_PLAIN = re.compile(r"^[^:\-*|()][^:*|]*$")


def merge_phrases(groups: list[list[str]]) -> list[list[str]]:
    """「blue archive」「hatsune miku」「long hair」：連在一起的幾個字剛好是一個名字，就當一個條件
    （以前拆成 blue AND archive → 0 張）。從最長的試起，最多 5 個字。"""
    out, i = [], 0
    while i < len(groups):
        g = groups[i]
        if len(g) == 1 and _PLAIN.match(g[0]):
            for j in range(min(len(groups), i + 5), i + 1, -1):
                seg = groups[i:j]
                if all(len(x) == 1 and _PLAIN.match(x[0]) for x in seg):
                    phrase = " ".join(x[0] for x in seg)
                    a, b = resolve_tags(phrase)
                    if a or b:
                        out.append([phrase])
                        i = j
                        break
            else:
                out.append(g)
                i += 1
            continue
        out.append(g)
        i += 1
    return out


def explain(q: str) -> list[dict]:
    """搜尋沒有結果時用：每個條件各自找到幾張、找不到的字給建議（「你是不是要找…」）。"""
    from . import nameindex
    out = []
    c = db.conn()
    for group in merge_phrases(tokenize(q)):
        for t in group:
            body = t[1:] if t.startswith("-") and len(t) > 1 else t
            try:
                r = _term(body)
            except Exception:  # noqa
                r = ("0", [])
            n = None
            if r is not None:
                try:
                    n = c.execute(f"SELECT COUNT(*) FROM media m WHERE m.status='done' AND COALESCE(m.excluded,0)=0 AND ({r[0]})", r[1]).fetchone()[0]
                except Exception:  # noqa
                    n = 0
            sug = []
            if not n and ":" not in body:
                for tid, name, cat, disp, _ in nameindex.search(body, limit=6)[:6]:
                    sug.append({"name": name, "category": cat, "display": disp})
                if len(sug) < 6 and body.isascii() and len(body) >= 3:
                    for r2 in c.execute("SELECT name, category FROM tags WHERE category='general' AND name LIKE ? ESCAPE '\\' LIMIT 4",
                                        (f"%{_like(body.replace(' ', '_'))}%",)):
                        sug.append({"name": r2[0], "category": r2[1], "display": zh_of(r2[0]) or r2[0]})
            out.append({"term": t, "found": r is not None, "n": n, "suggest": sug})
    return out


def compile_query(q: str) -> tuple[list[str], list]:
    w, p = [], []
    for group in merge_phrases(tokenize(q)):
        ors, op = [], []
        neg_all = all(t.startswith("-") for t in group)
        for t in group:
            neg = t.startswith("-") and len(t) > 1
            body = t[1:] if neg else t
            try:
                r = _term(body)
            except (ValueError, OverflowError, TypeError):   # w:1.2.3、date:9999 這種打錯的條件 → 當作找不到，不要整頁出錯
                r = ("0", [])
            if r is None:
                sql, params = "0", []
            else:
                sql, params = r
            if neg and not neg_all:
                sql = f"NOT ({sql})"
            ors.append(f"({sql})")
            op += params
        expr = " OR ".join(ors)
        if neg_all:
            # -a|-b：兩個都不要
            expr = f"NOT ({expr})"
        w.append(f"({expr})")
        p += op
    return w, p


def mtype_cond(mtype: str) -> tuple[str, list]:
    """媒材篩選（b77）：官方種類看 official；一般媒材只列「你標的」或 AI 有把握的（信心 ≥ CONF），
    沒把握的集中在 unsure（未定）。"""
    if mtype in ("anime", "manga", "novel", "movie"):
        return "m.official=?", [mtype]
    from .mediaclf import CONF
    if mtype == "unsure":
        return (f"(m.kind='image' AND COALESCE(m.media_src,'')<>'manual' AND m.media_conf IS NOT NULL AND m.media_conf<{CONF})", [])
    return (f"(m.media_type=? AND (m.media_src='manual' OR m.media_conf IS NULL OR m.media_conf>={CONF}))", [mtype])


def where(view: str, nsfw: str, q: str, tag_id: int | None, miss: str = "all", src: str | None = None,
          mtype: str | None = None, bucket: str | None = None):
    w, p = [], []
    w.append("m.status='error'" if view == "errors" else "m.status='done'")
    w.append("m.excluded=1" if view == "excluded" else "COALESCE(m.excluded,0)=0")
    if mtype:
        sq, sp = mtype_cond(mtype)
        w.append(sq); p += sp
    if nsfw == "hide":
        w.append(f"{NSFW}=0")
    elif nsfw == "only" or view == "nsfw":
        w.append(f"{NSFW}=1")
    if view == "noartist":
        view, miss = "unrecognized", "artist"
    if view == "unrecognized":
        if bucket == "skipped":
            w.append(f"{MISSING_RAW.get(miss, MISSING_RAW['all'])} AND {SKIP}>0")
        else:
            w.append(MISSING.get(miss, MISSING["all"]))
            if bucket in BUCKETS:
                w.append(BUCKETS[bucket])
    elif view == "videos":
        w.append("m.kind='video'")
    elif view == "official":         # 圖庫「官方」分頁：動畫截圖、漫畫截圖、小說插圖
        w.append("COALESCE(m.official,'')<>''")
    qw, qp = compile_query(q)
    w += qw
    p += qp
    join = ""
    if tag_id is not None:
        cond = {"unchecked": "mt.state=0", "candidates": "mt.state=0", "confirmed": "mt.state=1",
                "model": "mt.state=0 AND mt.source='model'",
                "online": "mt.state=0 AND mt.source IN ('online','danbooru','pixiv')",
                "manual": "mt.state=1 AND mt.source='manual'", "rejected": "mt.state=-1"}.get(src or "", "mt.state>=0")
        join = f"JOIN media_tags mt ON mt.media_id=m.id AND mt.tag_id=? AND {cond}"
        p.insert(0, tag_id)
    return join, " AND ".join(w), p
