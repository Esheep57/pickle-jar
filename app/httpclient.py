"""對外連線：每個網站各自限速、失敗重試（429／5xx／斷線）。所有連外的程式都走這裡。"""
from __future__ import annotations

import logging
import threading
import time

import httpx


class TransientError(RuntimeError):
    """網站暫時連不上、429、5xx（重試 3 次後還是）：不是程式錯，之後會自動重試（定期重搜、資料卡隔天重抓）。
    呼叫端記 info 就好，不要進「程式出錯」（b93：10-16 Danbooru 503 的六筆淹掉了真正的錯誤）。"""


logging.getLogger("httpx").setLevel(logging.WARNING)   # 完整網址含 API key，不要記進 log

UA = "PickleJar/1.1 (local image organizer)"

# Wikimedia（維基百科、Wikidata）2025 起擋「沒有聯絡方式」的 User-Agent（一律 403，連瀏覽器 UA 也擋）：
# 要「程式名/版本 (說明; 網址或聯絡方式)」的格式。以前維基查作品譯名其實一直 403，只是記成「查不到」
WIKI_UA = "PickleJar/1.1 (personal local image organizer; https://www.mediawiki.org/wiki/API:Etiquette)"
BROWSER_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0 Safari/537.36"

MIN_INTERVAL = {"danbooru": 0.4, "iqdb": 2.5, "pixiv": 1.5, "wiki": 1.0, "r34": 1.0, "e621": 1.0, "e621_iqdb": 1.7, "fxtwitter": 1.0, "tracemoe": 7.0, "bangumi": 0.6, "wikidata": 0.5, "ehentai": 1.5, "ehupld": 10.0, "nhentai": 0.5}



class NotFound(Exception):
    pass



# ---------- HTTP（限速 + 重試） ----------
_last: dict[str, float] = {}

_rate_lock = threading.Lock()

_client: httpx.Client | None = None



def client() -> httpx.Client:
    global _client
    if _client is None:
        _client = httpx.Client(timeout=25, follow_redirects=True, headers={"User-Agent": UA})
    return _client



def _req(site: str, method: str, url: str, **kw) -> httpx.Response:
    for attempt in range(3):
        with _rate_lock:  # 多執行緒同時搜時，仍保證每個網站的請求間隔
            wait = MIN_INTERVAL[site] - (time.time() - _last.get(site, 0))
            _last[site] = time.time() + max(wait, 0)
        if wait > 0:
            time.sleep(wait)
        try:
            r = client().request(method, url, **kw)
        except httpx.TransportError as e:
            if attempt == 2:
                raise TransientError(f"連線失敗：{e}") from e
            time.sleep(3 * (attempt + 1))
            continue
        if r.status_code == 429 or r.status_code >= 500:
            time.sleep(5 * (attempt + 1))
            continue
        return r
    raise TransientError(f"{site} 暫時無法使用（HTTP {r.status_code}）")
