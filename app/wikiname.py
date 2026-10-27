"""作品的台灣譯名：內建對照表沒有的作品，到維基百科查（保守做法）。

流程：Danbooru 作品的日文名 → 日文維基「完全同名」的條目 → 條目的中文版連結 → 中文維基用台灣用詞顯示的標題。
寧可查不到，也不要查錯：
- 只接受完全同名的條目；被重新導向到別的條目（例如「オーバーウォッチ」→「オーバーウォッチ (2022)」）就放棄
- 消歧義頁放棄
- 沒有日文名的作品，才用英文名查英文維基，一樣要完全同名
- 查到的名字太長、看起來不像作品名就放棄
結果存在 series_zh 資料表；手動改名和內建對照表永遠優先。只送出作品名稱，不會送出任何圖片。
"""
from __future__ import annotations

import json
import logging
import os
import re
import time

from . import db, names

log = logging.getLogger("wikiname")
RETRY_DAYS = 60
_TAG = re.compile(r"<[^>]+>")
_PAREN = re.compile(r"\s*[（(][^（）()]*[）)]\s*$")
_NORM = re.compile(r"[\s・･·:：!！?？~〜\-－_'’\"]+")

WIKI = os.environ.get("LIB_WIKI", "https://{lang}.wikipedia.org")   # 測試時指到假伺服器
SCHEMA = """CREATE TABLE IF NOT EXISTS series_zh(name TEXT PRIMARY KEY, zh TEXT, via TEXT, checked_at REAL, cn TEXT)"""
# cn：同一個條目的「大陆简体」標題（簡體模式用；2026-10 加的欄位，舊資料表補上）


_ready = False


def _ensure():
    global _ready
    if not _ready:   # 只在第一次建表，之後讀取不用搶寫入鎖
        with db.write_lock:
            c = db.conn()
            c.execute(SCHEMA)
            if "cn" not in {r[1] for r in c.execute("PRAGMA table_info(series_zh)")}:
                c.execute("ALTER TABLE series_zh ADD COLUMN cn TEXT")
        _ready = True


def cached(keys: list[str]) -> dict[str, str]:
    if not keys:
        return {}
    _ensure()
    c = db.conn()
    out = {}
    for i in range(0, len(keys), 800):
        ch = keys[i:i + 800]
        for r in c.execute(f"SELECT name, zh FROM series_zh WHERE zh IS NOT NULL AND name IN ({','.join('?' * len(ch))})", ch):
            out[r[0]] = r[1]
    return out


def need_lookup(keys: list[str]) -> list[str]:
    """內建表沒有、還沒查過（或很久以前查不到）的作品。"""
    _ensure()
    table = names._series_zh()
    c = db.conn()
    cutoff = time.time() - RETRY_DAYS * 86400
    done = {r[0] for r in c.execute("SELECT name FROM series_zh WHERE zh IS NOT NULL OR checked_at > ?", (cutoff,))}
    return [k for k in keys if k and k not in table and k not in done]


def _same(a: str, b: str) -> bool:
    return _NORM.sub("", a).lower() == _NORM.sub("", b).lower()


def _wiki(lang: str, params: dict) -> dict:
    from .httpclient import WIKI_UA, _req
    r = _req("wiki", "GET", WIKI.format(lang=lang) + "/w/api.php", params={**params, "format": "json"}, headers={"User-Agent": WIKI_UA})
    if r.status_code >= 400:
        raise RuntimeError(f"Wikipedia HTTP {r.status_code}")
    return r.json()


def _ja_title(key: str) -> str | None:
    r = db.conn().execute("""SELECT ti.other_names FROM tags t JOIN tag_info ti ON ti.tag_id=t.id WHERE t.name=?""",
                          (key,)).fetchone()
    for n in json.loads(r[0] or "[]") if r else []:
        n = n.strip().replace("_", " ")
        if names.KANA.search(n) or names.HAN_ONLY.match(n):
            return n          # Danbooru 別名的第一個日文名通常就是官方標題
    return None


def _clean(disp: str) -> str | None:
    import html
    disp = html.unescape(_PAREN.sub("", _TAG.sub("", disp))).strip()
    short = re.split(r"\s*[～〜~]", disp)[0].strip()      # 去掉「～副標題～」
    if len(short) >= 2:
        disp = short
    if not disp or len(disp) > 30:
        return None
    return disp


def _variant_title(zh_title: str, variant: str) -> str | None:
    d = _wiki("zh", {"action": "parse", "page": zh_title, "prop": "displaytitle", "variant": variant, "redirects": 1})
    return _clean(d.get("parse", {}).get("displaytitle") or zh_title)


def _cn_ok(tw: str, cn: str | None) -> str | None:
    """大陸標題：沒有中文字（條目標題是英文、日文原名）就不用；台灣譯名沒有「系列」、大陸標題多了「系列」就拿掉。"""
    if not cn or not re.search(r"[\u3400-\u9fff]", cn):
        return None
    if cn.endswith("系列") and "系列" not in tw and len(cn) > 3:
        cn = cn[:-2].rstrip("：: ")
    return cn


def _zh_from(lang: str, title: str, cn: bool = False):
    """台灣譯名；cn＝True 時回傳 (台灣譯名, 大陸譯名)。"""
    tw = _zh_pair(lang, title, cn)
    return tw if cn else (tw[0] if tw else None)


def _zh_pair(lang: str, title: str, cn: bool):
    d = _wiki(lang, {"action": "query", "titles": title, "prop": "langlinks|pageprops", "lllang": "zh", "redirects": 1})
    q = d.get("query", {})
    for rd in q.get("redirects", []) + q.get("normalized", []):
        if not _same(rd.get("from", ""), rd.get("to", "")):
            return None      # 導到別的條目 → 不確定是不是同一部作品
    for p in q.get("pages", {}).values():
        if "missing" in p or "disambiguation" in (p.get("pageprops") or {}):
            return None
        if not _same(p.get("title", ""), title):
            return None
        ll = p.get("langlinks") or []
        if not ll:
            return None
        zh_title = ll[0].get("*")
        disp = _variant_title(zh_title, "zh-tw")
        if not disp:
            return None
        return disp, (_cn_ok(disp, _variant_title(zh_title, "zh-cn")) if cn else None)
    return None


def lookup(key: str, cn: bool = False):
    """回傳 (台灣譯名, 從哪裡查到的)；cn＝True 時多一個大陸譯名 (台灣, 從哪, 大陸)。"""
    ja = _ja_title(key)
    if ja:
        via, p = f"ja:{ja}", _zh_pair("ja", ja, cn)
    elif "_(" in key:
        via, p = None, None   # cyberpunk_(series) → 「Cyberpunk」會對到類型條目，不查
    else:
        en = names.pretty_raw(key)
        via, p = f"en:{en}", _zh_pair("en", en, cn)
    if not p:
        return (None, None, None) if cn else (None, None)
    return (p[0], via, p[1]) if cn else (p[0], via)


def need_cn() -> list[str]:
    """查到台灣譯名、還沒有大陸譯名的作品（以前查的、或簡體模式打開之前查的）。"""
    _ensure()
    return [r[0] for r in db.conn().execute("SELECT name FROM series_zh WHERE zh IS NOT NULL AND cn IS NULL")]


def run(keys: list[str], stop=None, progress=None):
    _ensure()
    for i, k in enumerate(keys):
        if stop and stop():
            break
        try:
            zh, via, cn = lookup(k, cn=True)
        except Exception as e:  # noqa  連不上：記下來，一天後再試（不要每次打開書架都重查）
            log.info("查維基失敗 %s：%s", k, e)
            with db.write_lock:
                # 已經有台灣譯名的（簡體模式補查大陸譯名時連不上）：留著，不要蓋掉
                db.conn().execute("INSERT OR IGNORE INTO series_zh(name, zh, via, checked_at) VALUES(?,NULL,'error',?)",
                                  (k, time.time() - (RETRY_DAYS - 1) * 86400))
            if progress:
                progress(i + 1)
            continue
        with db.write_lock:
            db.conn().execute("INSERT OR REPLACE INTO series_zh(name, zh, via, checked_at, cn) VALUES(?,?,?,?,?)",
                              (k, zh, via, time.time(), cn))
        if zh:
            log.info("作品譯名：%s → %s（%s）", k, zh, via)
        if progress:
            progress(i + 1)
