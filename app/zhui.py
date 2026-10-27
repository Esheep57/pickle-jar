"""介面用語的簡體轉換（伺服器這邊）：跟前端 static/js/zhcn.js 用同一份字典（static/js/zhcn-dict.js，scripts/build_zhcn.py 產生）。
用在搜尋：簡體模式下畫面上看到的是「视频」「单人女性」，打這些字也要找得到。"""
from __future__ import annotations

import json
import logging
import re
from functools import lru_cache
from pathlib import Path

log = logging.getLogger("zhui")
DICT = Path(__file__).resolve().parent.parent / "static" / "js" / "zhcn-dict.js"


@lru_cache(maxsize=1)
def _dicts() -> tuple[dict, int, dict, int]:
    try:
        s = DICT.read_text("utf-8")
        tw = json.loads(re.search(r"TW: (\{.*?\}),\nTS: ", s, re.S).group(1))
        ts = json.loads(re.search(r"\nTS: (\{.*\})\};", s, re.S).group(1))
    except Exception as e:  # noqa  檔案不見：只做簡繁字轉換
        log.warning("讀不到簡體字典 %s：%s", DICT, e)
        tw, ts = {}, {}
    return tw, max(map(len, tw), default=1), ts, max(map(len, ts), default=1)


def _longest(s: str, d: dict, mx: int) -> str:
    out, i, n = [], 0, len(s)
    while i < n:
        for L in range(min(mx, n - i), 0, -1):
            w = s[i:i + L]
            if w in d:
                out.append(d[w])
                i += L
                break
        else:
            out.append(s[i])
            i += 1
    return "".join(out)


@lru_cache(maxsize=20000)
def conv(s: str) -> str:
    """台灣繁體 → 大陸用語＋簡體字（跟畫面上簡體模式看到的一樣）。"""
    if not s or not re.search(r"[㐀-鿿]", s):
        return s
    tw, m1, ts, m2 = _dicts()
    if not ts:
        from . import names
        return names.to_simp(s)
    return _longest(_longest(s, tw, m1), ts, m2)
