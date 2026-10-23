"""ascii2d 半自動找出處：Danbooru 沒有的 X／Pixiv 原創圖（手機從 X 存的 IMG_xxxx 大多是這種）。

ascii2d 有 Cloudflare 機器人驗證，不能也不該由程式自己去搜。流程是在你自己的瀏覽器裡：
1. 系統頁按「開始」→ 把要找的圖排進 ascii2d 表（status=queued），打開 ascii2d
2. 油猴腳本（1.4 起）在 ascii2d 頁面右下角顯示面板：你按 Enter，它向本機要下一張圖、用 ascii2d 自己的上傳表單送出
   （驗證出現時由你自己點）
3. 結果頁：腳本把每個結果的連結（X 貼文、Pixiv 作品、作者）送回來 → 這裡解析、查 Danbooru、查 FxTwitter／Pixiv、
   下載對方的圖跟你的圖比對，產生候選（跟搜源一樣只進待確認）
4. 你在面板上看比對結果，Enter 確認勾選的、N 沒找到、S 先跳過 → 下一張

一張一張由你按，速度是人的速度；程式只負責填表、讀結果、比對、整理候選。
"""
from __future__ import annotations

import io
import json
import logging
import re
import threading
import time
from pathlib import Path

from . import db, media

log = logging.getLogger("ascii2d")

SCHEMA = """CREATE TABLE IF NOT EXISTS ascii2d(
  media_id INTEGER PRIMARY KEY, seq REAL, status TEXT NOT NULL,   -- queued | searching | ready | done | none | skipped
  page_url TEXT, raw TEXT, result TEXT, searched_at REAL, decided_at REAL);
CREATE INDEX IF NOT EXISTS ix_ascii2d_status ON ascii2d(status, seq)"""

TWEET = re.compile(r"(?:twitter|x|fxtwitter|vxtwitter)\.com/([A-Za-z0-9_]{1,15})/status(?:es)?/(\d{5,25})", re.I)
PIXIV_ART = re.compile(r"pixiv\.net/(?:[a-z]{2}/)?artworks/(\d{3,12})|member_illust\.php\?[^\"' ]*illust_id=(\d{3,12})", re.I)
PIXIV_USER = re.compile(r"pixiv\.net/(?:[a-z]{2}/)?users/(\d+)|member\.php\?id=(\d+)", re.I)
MAX_RESULTS = 3            # 一次最多驗證前幾個結果（ascii2d 的色合検索第一個通常就是）
# 先用 ascii2d 頁面上的縮圖比（b81）：明顯不像就不再去 Danbooru／X／Pixiv 查（每個結果省 3–8 秒，Pixiv 每次要隔 1.5 秒）。
# 2026-10-16 開發時的紀錄 7,802 次比對：確認的結果分數全部 ≥ 0.85（縮圖比的 14 個也是）；< 0.3 的 6,839 次全部不是。
PREFILTER = 0.4
UPLOAD_MAX = 1600          # 送 ascii2d 的圖縮到這個大小

_state = {"active": False, "started": None}
SEEN: dict = {}            # 油猴腳本最近一次連上來（版本、時間）
_busy: set[int] = set()
_lock = threading.Lock()


# ---------------- 要找哪些圖 ----------------
LOW_TIER = 2     # 層 ≥2（漫畫、有字、沒有人、梗圖、照片、影片）：找到率 2–9%，預設不排（b75）


def targets(split: bool = False, low: bool = False, x: bool = True):
    """low：也要找到率低的（漫畫、有字、梗圖…）。預設不要（開發時的紀錄：插畫 16–47%、漫畫／有字 9%、梗圖 2%）。
    還沒有創作者、不是照片、不是作者不可考（梗圖等）、ascii2d 還沒找過的圖。
    順序：沒有角色也沒有作者的插畫先（你最想整理的），再來是有角色但缺作者的；同一類新的先。
    （先把幾個集合查出來再用 Python 篩：一句 SQL 疊好幾個 NOT EXISTS 在開發時的圖庫要 4 秒）"""
    c = db.conn()
    ident = lambda cond: {r[0] for r in c.execute(f"""SELECT DISTINCT mt.media_id FROM media_tags mt JOIN tags t ON t.id=mt.tag_id
                                                        WHERE mt.state=1 AND {cond}""")}
    has_artist, no_artist, has_char = ident("t.category='artist'"), ident("t.no_artist=1"), ident("t.category='character'")
    done = {r[0] for r in c.execute("SELECT media_id FROM ascii2d WHERE status IN ('done','none')")}
    rows = c.execute("""SELECT id FROM media WHERE status='done' AND kind='image' AND COALESCE(unrec_skip,0)=0
        AND id NOT IN (SELECT media_id FROM album_items)
        AND COALESCE(excluded,0)=0 AND official IS NULL AND COALESCE(artist_done,0)=0
        AND COALESCE(media_type,'illustration') NOT IN ('photo','cosplay','figure')
        ORDER BY COALESCE(taken_at, mtime) DESC, id DESC""").fetchall()
    # 你從網站匯入的（X、Pixiv、本子…）出處早就知道了，不用找（b95）。
    # 以前從 X 匯入的還特地排最前面（b33，想找 Pixiv 上更大的版本）：開發時的紀錄 602 張只多找到 4 張 Pixiv 版，其他都是找回同一則推文
    imp = imported_ids()
    ids = [r[0] for r in rows if r[0] not in has_artist and r[0] not in no_artist and r[0] not in done and r[0] not in imp]
    xi: list[int] = []
    # 找到率高的先（b56，開發時的紀錄 2,189 張）：插畫堆 25%（iCloud 的 41%、Google 相簿的 13%；檔名 IMG_xxxx 46%）、
    # 有字 7%、梗圖 2%。以前只看「新的先」，佇列前面整片是 Google 相簿的梗圖，198 張只找到 5 張。
    tier = yield_tiers(ids)
    if not low:
        ids = [i for i in ids if tier.get(i, 3) < LOW_TIER]
    first = xi + sorted((i for i in ids if i not in has_char), key=lambda i: tier.get(i, 3))
    second = sorted((i for i in ids if i in has_char), key=lambda i: tier.get(i, 3))
    if split:
        return first, second
    # 角色已知、缺作者的插畫也比沒角色的梗圖值得先找：依找到率分層，同一層裡沒角色的先
    rest = sorted(first[len(xi):] + second, key=lambda i: (tier.get(i, 3), i in has_char))
    return xi + rest


_IMGNO = re.compile(r"^IMG_\d{4}", re.I)


def yield_tiers(ids) -> dict[int, int]:
    """ascii2d 預估找到率分層：0 插畫且是手機存的（IMG_xxxx）或 iCloud 圖庫、1 其他插畫、2 漫畫／有字／沒有人、3 梗圖、照片、影片；還沒分堆的算 1。"""
    c = db.conn()
    out = {}
    ids = list(ids)
    for k in range(0, len(ids), 900):
        ch = ids[k:k + 900]
        for mid, path, pile in c.execute(f"SELECT id, path, pile FROM media WHERE id IN ({','.join('?' * len(ch))})", ch):
            base = re.split(r"[\\/]", path or "")[-1]
            if pile == "illust":
                out[mid] = 0 if (_IMGNO.match(base) or "\\圖庫\\" in path or "圖庫保存" in path) else 1
            elif pile is None:
                out[mid] = 1          # 還沒分堆（剛加入）：不知道，不要因為這樣就不排（b75）
            elif pile in ("meme", "photo", "video"):
                out[mid] = 3
            else:
                out[mid] = 2
    return out


def imported_ids() -> set[int]:
    """從網站匯入的圖（import_meta 有紀錄的，任何網站）。ascii2d 不找（出處就是匯入的那篇）。"""
    try:
        return {r[0] for r in db.conn().execute("SELECT m.id FROM import_meta i JOIN media m ON m.path=i.path")}
    except Exception:  # noqa  還沒有 import_meta 表
        return set()


def x_imports() -> list[int]:
    """從 X 匯入、搜源沒在 Danbooru 找到的圖（新的先）。"""
    c = db.conn()
    try:
        return [r[0] for r in c.execute("""SELECT m.id FROM import_meta i JOIN media m ON m.path=i.path
            LEFT JOIN media_source s ON s.media_id=m.id
            WHERE i.site='x' AND m.status='done' AND m.kind='image' AND COALESCE(s.status,'')!='found'
              AND m.id NOT IN (SELECT media_id FROM album_items)
            ORDER BY i.created_at DESC""")]
    except Exception:  # noqa  還沒有 import_meta 表
        return []


def select(q: str = "", ids: list[int] | None = None, only_missing: bool = True, limit: int | None = None,
           include_x: bool = False, low: bool = False) -> dict:
    """你選的範圍 → 要排的圖（依「沒角色的先、新的先」）。
    q：搜尋語法（跟圖庫一樣，例如 date:2026-07、path:圖庫\\2026-07、-tag:梗圖）；ids：選取的圖。
    only_missing：只留還沒有創作者、ascii2d 還沒找過的（照片、作者不可考的本來就不算）。limit：最多幾張。"""
    from .query import where
    c = db.conn()
    if ids:
        pool = [int(i) for i in ids]
    else:
        join, w, p = where("all", "show", q or "", None)
        pool = [r[0] for r in c.execute(f"SELECT m.id FROM media m {join} WHERE {w} AND m.kind='image'", p)]
    matched = len(pool)
    low_out = 0
    if only_missing:
        # 你選的圖：照你選的，不管找到率。從 X 匯入的只在勾「先找從 X 匯入的」時加（下面），以前不勾也排在最前面
        order = {m: i for i, m in enumerate(targets(low=low or bool(ids), x=bool(ids)))}
        if not low and not ids:
            allm = set(targets(low=True, x=False))
            low_out = sum(1 for m in pool if m in allm and m not in order)
        pool = sorted((m for m in pool if m in order), key=order.get)
    elif not ids:
        order = {m: i for i, m in enumerate(targets(low=True, x=bool(ids)))}
        pool = sorted(pool, key=lambda m: order.get(m, len(order) + m))
    nx = 0       # include_x（從 X 匯入的先找）b95 拿掉：參數留著給舊的前端，不再有作用
    total = len(pool)
    if limit:
        pool = pool[:max(1, int(limit))]
    return {"ids": pool, "matched": matched, "eligible": total, "count": len(pool), "x_extra": nx, "low_out": low_out,
            **forecast(pool)}


def months(limit: int = 18) -> list[dict]:
    """還沒有創作者的圖，依拍攝（或檔案）月份分：選範圍時一鍵帶入 date:YYYY-MM。"""
    ids = targets()
    if not ids:
        return []
    out: dict[str, int] = {}
    c = db.conn()
    for i in range(0, len(ids), 900):
        ch = ids[i:i + 900]
        for (mon,) in c.execute(f"""SELECT strftime('%Y-%m', COALESCE(taken_at, mtime), 'unixepoch', 'localtime') FROM media
                                   WHERE id IN ({','.join('?' * len(ch))})""", ch):
            out[mon] = out.get(mon, 0) + 1
    return [{"month": m, "n": n} for m, n in sorted(out.items(), reverse=True)[:limit]]


def clear() -> dict:
    """清空佇列：排隊中的拿掉；已經有結果、還沒決定的改成「先跳過」（結果留著，之後再排會直接顯示）。"""
    with db.write_lock:
        c = db.conn()
        c.execute("DELETE FROM ascii2d WHERE status IN ('queued','searching')")
        c.execute("UPDATE ascii2d SET status='skipped' WHERE status='ready'")
    return counts()


def start(ids: list[int] | None = None, replace: bool = True, picked: bool = False) -> dict:
    """排進佇列。replace：先清掉目前的佇列（預設；「加在後面」就不清）。
    ids 沒給＝全部還沒有創作者的圖。你指定的圖就算之前找過（沒找到、跳過）也會重新排。
    picked：你沒勾「只找還沒有創作者的」＝有創作者也要找，之後有了創作者也不會被當成不用找。"""
    explicit = bool(ids)
    ids = targets() if not ids else [int(i) for i in ids]
    if replace:
        clear()
    else:
        prune()
    now = time.time()
    with db.write_lock:
        c = db.conn()
        base = c.execute("SELECT IFNULL(MAX(seq), 0) FROM ascii2d").fetchone()[0] or 0
        cond = "ascii2d.status NOT IN ('ready')" if explicit else "ascii2d.status='skipped'"
        c.executemany(f"""INSERT INTO ascii2d(media_id, seq, status, picked) VALUES(?,?,'queued',?)
            ON CONFLICT(media_id) DO UPDATE SET status='queued', seq=excluded.seq, picked=excluded.picked WHERE {cond}""",
                      [(m, base + 1 + i, int(picked)) for i, m in enumerate(ids)])
    _state.update(active=True, started=now)
    return {"queued": len(ids), **counts()}


def stop():
    _state["active"] = False


def queue_forecast() -> dict:
    return forecast([r[0] for r in db.conn().execute("SELECT media_id FROM ascii2d WHERE status IN ('queued','searching','ready')")])


def counts() -> dict:
    rows = dict(db.conn().execute("SELECT status, COUNT(*) FROM ascii2d GROUP BY status").fetchall())
    return {"remaining": rows.get("queued", 0) + rows.get("searching", 0) + rows.get("ready", 0),
            "found": db.conn().execute("SELECT COUNT(*) FROM ascii2d WHERE status='done' AND result LIKE '%\"accepted\": [\"%'").fetchone()[0],
            "none": rows.get("none", 0), "skipped": rows.get("skipped", 0), "done": rows.get("done", 0),
            "active": _state["active"], "full": db.get_settings().get("ascii2d_auto_accept") == "full"}


_UNWANTED_SQL = """SELECT m.id FROM media m WHERE m.id IN ({ph}) AND (
      m.status!='done' OR COALESCE(m.unrec_skip,0)>0 OR COALESCE(m.artist_done,0)=1 OR COALESCE(m.excluded,0)=1
      OR m.official IS NOT NULL
      OR m.id IN (SELECT media_id FROM album_items)
      OR EXISTS(SELECT 1 FROM media_tags mt JOIN tags t ON t.id=mt.tag_id WHERE mt.media_id=m.id AND mt.state=1 AND t.no_artist=1)
      OR (NOT EXISTS(SELECT 1 FROM ascii2d a WHERE a.media_id=m.id AND a.picked=1)
          AND (EXISTS(SELECT 1 FROM media_tags mt JOIN tags t ON t.id=mt.tag_id
                      WHERE mt.media_id=m.id AND mt.state=1 AND t.category='artist')
               OR m.path IN (SELECT path FROM import_meta))))"""


def _unwanted(ids: list[int]) -> set[int]:
    """排進來以後不用再找的：你標了梗圖／截圖（作者不可考）、按了略過或「不用找作者」、標成官方（b75）、
    已經有創作者了（b75）、從網站匯入的（b95：出處就是那篇）、圖不在了。你自己挑的（picked）例外。"""
    if not ids:
        return set()
    c = db.conn()
    out: set[int] = set()
    try:
        c.execute("SELECT 1 FROM import_meta LIMIT 1")
        sql0 = _UNWANTED_SQL
    except Exception:  # noqa  還沒有 import_meta 表
        sql0 = _UNWANTED_SQL.replace("\n               OR m.path IN (SELECT path FROM import_meta)", "")
    for k in range(0, len(ids), 400):
        ch = ids[k:k + 400]
        out |= {r[0] for r in c.execute(sql0.format(ph=",".join("?" * len(ch))), ch)}
    return out


def _no_longer_wanted(mid: int) -> bool:
    return mid in _unwanted([mid])


def prune() -> int:
    """佇列裡已經不用找的改成「跳過」（第六次檢查：2,023 張排隊裡 1,159 張早就不用找，剩幾張的數字一直是錯的）。"""
    c = db.conn()
    q = [r[0] for r in c.execute("SELECT media_id FROM ascii2d WHERE status='queued'")]
    gone = _unwanted(q) | ({m for m in q} - {r[0] for r in c.execute("SELECT id FROM media")})
    if gone:
        with db.write_lock:
            c.executemany("UPDATE ascii2d SET status='skipped' WHERE media_id=? AND status='queued'", [(m,) for m in gone])
    return len(gone)


# ---------------- 預估：這批大概找到幾張、要多久 ----------------
_fc_cache: dict = {"at": 0, "rates": {}, "sec": 30.0}


def _history() -> tuple[dict, float]:
    """依找到率分層的歷史找到率（你決定過的：找到／找到＋沒找到）、每張要幾秒（中位數）。5 分鐘算一次。"""
    if time.time() - _fc_cache["at"] < 300:
        return _fc_cache["rates"], _fc_cache["sec"]
    c = db.conn()
    rows = c.execute("SELECT media_id, status, result LIKE '%\"accepted\": [\"%', decided_at FROM ascii2d WHERE status IN ('done','none')").fetchall()
    tier = yield_tiers([r[0] for r in rows])
    hit: dict[int, list] = {}
    for mid, st, acc, _ in rows:
        h = hit.setdefault(tier.get(mid, 3), [0, 0])
        h[0] += 1 if (st == "done" and acc) else 0
        h[1] += 1
    prior = {0: 0.40, 1: 0.15, 2: 0.08, 3: 0.02}          # 第一次用（沒有紀錄）時的估計：第五次檢查的真實數字
    rates = {t: (h[0] + 10 * prior[t]) / (h[1] + 10) for t, h in hit.items()}     # 樣本少時往預設值拉
    for t, p in prior.items():
        rates.setdefault(t, p)
    ts = sorted(r[3] for r in rows if r[3])
    gaps = sorted(b - a for a, b in zip(ts, ts[1:]) if 0 < b - a < 300)     # 超過 5 分鐘＝中間休息，不算
    sec = gaps[len(gaps) // 2] if len(gaps) >= 20 else 30.0
    _fc_cache.update(at=time.time(), rates=rates, sec=sec)
    return rates, sec


def forecast(ids) -> dict:
    """這些圖大概會找到幾張、要花多久（照你過去的找到率和速度）。"""
    ids = list(ids)
    if not ids:
        return {"expect": 0, "minutes": 0}
    rates, sec = _history()
    tier = yield_tiers(ids)
    exp = sum(rates[tier.get(m, 3)] for m in ids)
    return {"expect": round(exp), "minutes": max(1, round(len(ids) * sec / 60))}


def next_item() -> dict | None:
    """下一張：還沒決定的（上次停在這裡的）優先，再來是排隊中的。"""
    r = db.conn().execute("""SELECT media_id, status FROM ascii2d WHERE status IN ('searching','ready','queued')
        ORDER BY CASE status WHEN 'ready' THEN 0 WHEN 'searching' THEN 1 ELSE 2 END, seq LIMIT 1""").fetchone()
    if not r:
        return None
    mid = r[0]
    if not db.conn().execute("SELECT 1 FROM media WHERE id=? AND status='done'", (mid,)).fetchone():
        with db.write_lock:            # 圖不在了（刪掉、搬走）：跳過
            db.conn().execute("UPDATE ascii2d SET status='skipped' WHERE media_id=?", (mid,))
        return next_item()
    if r[1] == "queued" and _no_longer_wanted(mid):
        # 排進來以後你標了梗圖／截圖（作者不可考）、按了略過或「不用找作者」：不用再找（b55；開發時的佇列 399 張裡有 19 張）
        with db.write_lock:
            db.conn().execute("UPDATE ascii2d SET status='skipped' WHERE media_id=?", (mid,))
        return next_item()
    if r[1] == "queued":
        with db.write_lock:
            db.conn().execute("UPDATE ascii2d SET status='searching', searched_at=? WHERE media_id=?", (time.time(), mid))
    return {"mid": mid, "status": r[1], **counts()}


def upload_image(mid: int) -> bytes:
    """送給 ascii2d 的圖：轉成 JPEG、裁掉截圖的單色邊、縮到 1600px。"""
    from .danbooru import trim_borders
    row = db.conn().execute("SELECT path FROM media WHERE id=?", (mid,)).fetchone()
    if not row:
        raise FileNotFoundError(mid)
    img = trim_borders(media.load_image(Path(row[0])))
    img.thumbnail((UPLOAD_MAX, UPLOAD_MAX))
    buf = io.BytesIO()
    media.to_rgb(img).save(buf, "JPEG", quality=90)
    return buf.getvalue()


# ---------------- 解析 ascii2d 結果 ----------------
def parse(items: list[dict]) -> list[dict]:
    """腳本送來的結果（每個結果框：縮圖、連結、文字）→ [{rank, kind, tweet_id/pixiv_id, handle, author, url, thumb}]。
    只收 X 貼文和 Pixiv 作品（其他網站列在 other 給你參考）。同一則貼文／作品只留第一次出現的。"""
    out, seen = [], set()
    for rank, it in enumerate(items or []):
        links = [l for l in (it.get("links") or []) if isinstance(l, dict) and isinstance(l.get("href"), str)][:30]
        hrefs = [l["href"] for l in links]
        author, uid = None, None
        for l in links:            # 作者連結：X 的 intent/user、個人頁；Pixiv 的 users/
            h, txt = l["href"], (l.get("text") or "").strip()[:80]
            mu = PIXIV_USER.search(h)
            if mu:                 # Pixiv 使用者 ID：作品被刪、帳號被封時，還能用它找作者
                uid = mu.group(1) or mu.group(2)
            if txt and (re.search(r"(twitter|x)\.com/(intent/user|i/user|[A-Za-z0-9_]{1,15}/?$)", h) or mu):
                author = txt
        thumb = it.get("thumb") if isinstance(it.get("thumb"), str) else None
        tdata = it.get("thumb_data") if isinstance(it.get("thumb_data"), str) else None
        for h in hrefs:
            m = TWEET.search(h)
            if m and ("x", m.group(2)) not in seen:
                seen.add(("x", m.group(2)))
                out.append({"rank": rank, "kind": "x", "handle": m.group(1), "tweet_id": m.group(2), "author": author,
                            "url": f"https://x.com/{m.group(1)}/status/{m.group(2)}", "thumb": thumb, "thumb_data": tdata})
            m = PIXIV_ART.search(h)
            if m:
                pid = m.group(1) or m.group(2)
                if ("pixiv", pid) not in seen:
                    seen.add(("pixiv", pid))
                    out.append({"rank": rank, "kind": "pixiv", "pixiv_id": int(pid), "author": author, "user_id": uid,
                                "url": f"https://www.pixiv.net/artworks/{pid}", "thumb": thumb, "thumb_data": tdata})
    return out


# ---------------- 收到結果：查 Danbooru、查貼文、比對畫面 ----------------
def receive(mid: int, page_url: str, items: list, debug: str | None = None) -> dict:
    """腳本送來 ascii2d 的結果頁。存起來，背景解析＋比對（幾秒），腳本再來問結果。"""
    if not db.conn().execute("SELECT 1 FROM media WHERE id=?", (mid,)).fetchone():
        raise KeyError(mid)
    results = parse(items)
    raw = json.dumps({"items": _slim((items or [])[:12]), "debug": (debug or "")[:30000]}, ensure_ascii=False)
    with db.write_lock:
        db.conn().execute("""INSERT INTO ascii2d(media_id, seq, status, page_url, raw, result, searched_at) VALUES(?,?,?,?,?,?,?)
            ON CONFLICT(media_id) DO UPDATE SET status='searching', page_url=excluded.page_url, raw=excluded.raw,
            result=excluded.result, searched_at=excluded.searched_at""",
                          (mid, time.time(), "searching", (page_url or "")[:500], raw,
                           json.dumps({"results": _slim(results), "resolving": True}, ensure_ascii=False), time.time()))
    threading.Thread(target=_resolve_safe, args=(mid, results), daemon=True, name="ascii2d-resolve").start()
    return {"results": len(results)}


def _slim(rows: list) -> list:
    """存進資料庫前拿掉縮圖資料（只在比對時用）。"""
    return [{k: v for k, v in r.items() if k != "thumb_data"} if isinstance(r, dict) else r for r in rows]


def _resolve_safe(mid: int, results: list[dict]):
    with _lock:
        if mid in _busy:
            return
        _busy.add(mid)
    try:
        resolve(mid, results)
    except Exception as e:  # noqa
        log.warning("ascii2d 結果處理失敗 %s：%s", mid, e)
        with db.write_lock:
            db.conn().execute("UPDATE ascii2d SET status='ready', result=? WHERE media_id=?",
                              (json.dumps({"results": _slim(results), "error": f"{type(e).__name__}: {e}"[:300]}, ensure_ascii=False), mid))
    finally:
        _busy.discard(mid)


def _local_image(mid: int):
    from .danbooru import trim_borders
    row = db.conn().execute("SELECT path FROM media WHERE id=?", (mid,)).fetchone()
    return trim_borders(media.load_image(Path(row[0])))


def _fetch_image(url: str, site: str, headers: dict | None = None):
    from PIL import Image
    from .httpclient import _req
    from .danbooru import trim_borders
    r = _req(site, "GET", url, headers=headers or {})
    if r.status_code >= 400:
        return None
    return trim_borders(media.to_rgb(Image.open(io.BytesIO(r.content))))   # 兩邊都裁掉單色邊，才比得準


def _data_image(data_url: str):
    """腳本送來的縮圖（data:image/...;base64,...，最多約 200 KB）。"""
    import base64
    from PIL import Image
    try:
        b64 = data_url.split(",", 1)[1] if data_url.startswith("data:image/") else None
        if not b64 or len(b64) > 400_000:
            return None
        from .danbooru import trim_borders
        return trim_borders(media.to_rgb(Image.open(io.BytesIO(base64.b64decode(b64)))))
    except Exception:  # noqa
        return None


def _same_threshold() -> float:
    from .online import tagger_loaded
    return 0.9 if tagger_loaded() else 0.8


def _check_x(r: dict, local) -> dict:
    """X 貼文：Danbooru 有沒有收（有的話角色、作品都有）、FxTwitter 讀作者和圖、跟你的圖比對。"""
    from .danbooru import _post_summary, dan_get
    from .importer import FX_API
    from .httpclient import _req
    out = {**r}
    try:
        posts = dan_get("/posts.json", {"tags": f"source:*/status/{r['tweet_id']}*", "limit": 10})
        if posts:
            post = _post_summary(posts[0])
            if len(posts) > 1:
                for key in ("characters", "copyrights", "artists"):
                    post[key] = sorted({t for p in posts for t in _post_summary(p)[key]})
            out["danbooru"] = post
    except Exception as e:  # noqa
        out["danbooru_error"] = str(e)[:200]
    try:
        resp = _req("fxtwitter", "GET", f"{FX_API}/status/{r['tweet_id']}")
        if resp.status_code < 400:
            t = (resp.json() or {}).get("tweet") or {}
            a = t.get("author") or {}
            out["handle"] = a.get("screen_name") or r["handle"]
            out["author"] = a.get("name") or r.get("author")
            from . import sitetags
            out["hashtags"] = sitetags.hashtags(t.get("text") or "")
            out["nsfw"] = bool(t.get("possibly_sensitive"))
            photos = [m.get("url") for m in ((t.get("media") or {}).get("all") or []) if m.get("type") == "photo" and m.get("url")]
            best = None
            for u in photos[:4]:      # 一則貼文最多 4 張：取最像的那張
                img = _fetch_image(u.split("?")[0] + "?name=small" if "pbs.twimg.com" in u else u, "fxtwitter")
                if img is not None:
                    from .danbooru import compare_images
                    s = compare_images(local, img)
                    best = s if best is None or s > best else best
            out["score"] = best
        else:
            out["x_error"] = f"讀不到貼文（HTTP {resp.status_code}，可能被刪除或設為私人）"
    except Exception as e:  # noqa
        out["x_error"] = str(e)[:200]
    return out


def _check_pixiv(r: dict, local) -> dict:
    from .danbooru import _post_summary, dan_get
    from .pixiv import fetch_pixiv, resolve_pixiv_artist
    out = {**r}
    try:
        posts = dan_get("/posts.json", {"tags": f"pixiv:{r['pixiv_id']}", "limit": 1})
        if posts:
            out["danbooru"] = _post_summary(posts[0])
    except Exception as e:  # noqa
        out["danbooru_error"] = str(e)[:200]
    try:
        pix = fetch_pixiv(int(r["pixiv_id"]))
        pix["artist_tag"] = resolve_pixiv_artist(pix.get("user_id"), pix.get("user"))
        out["pixiv"] = pix
        out["author"] = pix.get("user") or r.get("author")
    except Exception as e:  # noqa
        out["pixiv_error"] = str(e)[:200]
        if r.get("user_id"):         # 作品被刪、帳號被封（或 R-18 沒登入）：用 ascii2d 頁面上的作者連結找作者
            try:                     # Danbooru 還留著被封帳號的網址；沒登記就用 ascii2d 上的暱稱建立
                art = resolve_pixiv_artist(r["user_id"], r.get("author"))
                if art:
                    out["pixiv"] = {"user_id": str(r["user_id"]), "user": r.get("author"), "artist_tag": art, "tags": [],
                                    "gone": True}
            except Exception as e2:  # noqa
                log.info("用 ascii2d 的作者連結找作者失敗：%s", e2)
    try:                             # Pixiv 的圖（要帶 Referer）：多頁作品看前 3 頁，取最像的
        from .danbooru import compare_images
        from .httpclient import BROWSER_UA, _req
        from .pixiv import PIXIV
        s = db.get_settings()
        hd = {"User-Agent": BROWSER_UA, "Referer": "https://www.pixiv.net/"}
        ck = {"PHPSESSID": s["pixiv_phpsessid"]} if s.get("pixiv_phpsessid") else None
        resp = _req("pixiv", "GET", f"{PIXIV}/ajax/illust/{r['pixiv_id']}/pages", headers=hd, cookies=ck)
        pages = (resp.json() or {}).get("body") or [] if resp.status_code < 400 else []
        for pg in pages[:3]:
            u = (pg.get("urls") or {}).get("small") or (pg.get("urls") or {}).get("regular") or (pg.get("urls") or {}).get("original")
            img = _fetch_image(u, "pixiv", {"Referer": "https://www.pixiv.net/", "User-Agent": BROWSER_UA}) if u else None
            if img is not None:
                sc = compare_images(local, img)
                if out.get("score") is None or sc > out["score"]:
                    out["score"], out["verify_by"] = sc, "Pixiv 的圖"
    except Exception as e:  # noqa
        out["verify_error"] = str(e)[:200]
    if out.get("score") is None and out.get("danbooru"):     # Pixiv 抓不到（R-18 沒登入等）：有 Danbooru 就拿 Danbooru 的圖比對
        try:
            from .danbooru import compare_images
            p = dan_get(f"/posts/{out['danbooru']['post_id']}.json") if out["danbooru"].get("post_id") else {}
            u = p.get("large_file_url") or p.get("preview_file_url")
            img = _fetch_image(u, "danbooru") if u else None
            if img is not None:
                out["score"], out["verify_by"] = compare_images(local, img), "Danbooru 的圖"
        except Exception as e:  # noqa
            out["verify_error"] = str(e)[:200]
    return out


def _quick_scores(local, results: list[dict]) -> dict:
    """縮圖先比：你的圖和幾張 ascii2d 縮圖一次送進 WD（以前每比一張就重算一次你的圖）。{結果序號: 分數}。
    沒有 WD 模型時不先比（灰階比對太粗）。算法跟 danbooru.compare_images 一樣。"""
    from . import tagger
    idx, imgs = [], []
    for i, r in enumerate(results):
        img = _data_image(r["thumb_data"]) if r.get("thumb_data") else None
        if img is not None:
            idx.append(i); imgs.append(img)
    tg = tagger.ensure() if imgs else None
    if tg is None:
        return {}
    import numpy as np
    from . import danbooru
    if getattr(danbooru.compare_images, "__module__", "") != "app.danbooru":     # 測試換掉了比對方法：照它的
        return {i: danbooru.compare_images(local, im) for i, im in zip(idx, imgs)}
    from .matcher import matcher
    _, E = tg.run(np.stack([tg.preprocess(local)] + [tg.preprocess(im) for im in imgs]))
    E = E - (matcher.mu if matcher.mu is not None and matcher.mu.shape[-1] == E.shape[-1] else 0)
    E /= np.linalg.norm(E, axis=1, keepdims=True) + 1e-8
    return {i: float(E[0] @ E[k + 1]) for k, i in enumerate(idx)}


def resolve(mid: int, results: list[dict]) -> dict:
    local = _local_image(mid)
    checked = []
    found_x = False
    pre = _quick_scores(local, results[:MAX_RESULTS])
    for i, r in enumerate(results[:MAX_RESULTS]):
        if found_x and r["kind"] != "pixiv":
            continue                 # X 已經找到同一張：剩下的只看 Pixiv（Pixiv 優先）
        sc = pre.get(i)
        if sc is not None and sc < PREFILTER:
            c = {**r, "score": sc, "verify_by": "ascii2d 的縮圖", "quick": True}   # 明顯不像：不用再查
            c.pop("thumb_data", None)
            checked.append(c)
            continue
        c = _check_x(r, local) if r["kind"] == "x" else _check_pixiv(r, local)
        if c.get("score") is None and c.get("thumb_data"):   # 對方的圖抓不到（刪文、R-18、要登入）：用 ascii2d 頁面上的縮圖比
            img = _data_image(c["thumb_data"])
            if img is not None:
                from .danbooru import compare_images
                c["score"], c["verify_by"] = compare_images(local, img), "ascii2d 的縮圖"
        c.pop("thumb_data", None)
        checked.append(c)
        if c.get("score") is not None and c["score"] >= 0.95:
            if c["kind"] == "pixiv":
                break                # 已經在 Pixiv 找到同一張：後面的不用再查
            found_x = True
    thr = _same_threshold()
    for c in checked:
        s = c.get("score")
        c["match"] = None if s is None else "same" if s >= 0.95 else "likely" if s >= thr else "unlikely"
    # 挑一個：同一張 > 很像 > 沒辦法比對（照 ascii2d 的排名）> 不太像。
    # 以前是「比對過的裡面最像的」，結果 ascii2d 排第一、只是比不了的正確結果，輸給比對過但根本不像的結果
    # Pixiv 和 X 都對得上（同一張或很像）時用 Pixiv（使用者要求：Pixiv 優先）
    rank = {"same": 2, "likely": 2, None: 1, "unlikely": 0}
    best = max(checked, key=lambda c: (rank[c["match"]], c["kind"] == "pixiv", c.get("score") or 0, -c.get("rank", 0))) if checked else None
    tags = _tags_for(mid, best) if best and best.get("match") != "unlikely" else []
    if best and best["kind"] == "pixiv" and best.get("match") in ("same", "likely") and not any(t["category"] == "artist" for t in tags):
        # Pixiv 的作者 Danbooru 還沒登記 → 用同一張圖的 X 結果補作者（X 帳號可以建立創作者，Pixiv 不行）
        alt = next((c for c in checked if c["kind"] == "x" and c.get("match") in ("same", "likely")), None)
        if alt:
            names = {t["name"] for t in tags}
            tags += [t for t in _tags_for(mid, alt) if t["category"] == "artist" and t["name"] not in names]
    res = {"results": checked, "best": checked.index(best) if best else None, "tags": tags, "threshold": thr}
    # 你看一眼就能決定的：每個結果都比對過、都「不太像」→ 腳本自動記「沒找到」跳下一張
    # （開發時的紀錄：不太像的 80 張全部被判「沒找到」；同一張的 35 張收了 30 張）
    if checked and all(c.get("match") == "unlikely" for c in checked) and not tags:
        res["auto"] = "none"
    with db.write_lock:
        db.conn().execute("UPDATE ascii2d SET status='ready', result=? WHERE media_id=?",
                          (json.dumps(res, ensure_ascii=False), mid))
    extra = [{"site": "pixiv", "id": str(c["pixiv_id"])} for c in checked if c["kind"] == "pixiv" and c.get("match") == "same"]
    if extra:      # Pixiv 上有同一張：馬上比大小（從 X 存的圖常常比 Pixiv 原圖小很多）→ 大圖出現「↑ 高清」
        threading.Thread(target=_upgrade_check, args=(mid, extra), daemon=True, name="ascii2d-upgrade").start()
    return res


def _upgrade_check(mid: int, extra: list[dict]):
    from . import errlog, upgrade
    if not db.get_settings().get("auto_upgrade", True):
        return
    with errlog.bg("ascii2d：Pixiv 有沒有更大的版本"):
        upgrade.check(mid, only=upgrade.refs(mid) + extra)


def _tags_for(mid: int, b: dict, via: str = "ascii2d") -> list[dict]:
    """選中的結果 → 候選清單 [{name, category, why, checked}]。都還沒寫進資料庫（你按確認才寫）。
    via：理由開頭寫哪裡來的（ascii2d、貼的網址，b94）。"""
    from . import sigscan, siteartists
    from .online import _pixiv_character_matches
    out, seen = [], set()
    ok = b.get("match") in ("same", "likely")
    where = {"x": f"X @{b.get('handle')}", "pixiv": f"Pixiv {b.get('pixiv_id')}"}[b["kind"]]
    score = f"（畫面比對 {round(b['score'] * 100)}%）" if b.get("score") is not None else "（沒辦法比對畫面，請自己看）"

    def add(name, cat, why):
        if name and name not in seen:
            seen.add(name)
            out.append({"name": name, "category": cat, "why": why, "checked": ok})
    dan = b.get("danbooru")
    if dan:
        for key, cat in (("artists", "artist"), ("characters", "character"), ("copyrights", "copyright")):
            for t in dan.get(key, []):
                add(t, cat, f"{via} → {where} → Danbooru 有收{score}")
    if not (dan and dan.get("artists")):
        artist = None
        if b["kind"] == "x" and b.get("handle"):
            sigscan._ensure()
            artist = sigscan.resolve("x", b["handle"])
            why = f"{via} → {where} 的作者{score}"
            if not artist:           # Danbooru 還沒登記：用 X 帳號建立創作者（之後登記了會自動合併）
                artist = siteartists.create("x", b["handle"], b.get("author"))
                why += "（Danbooru 還沒登記，先用 X 帳號）"
            if artist and sigscan._is_character_account(mid, artist):
                artist = None
            add(artist, "artist", why)
        elif b["kind"] == "pixiv" and (b.get("pixiv") or {}).get("artist_tag"):
            gone = "（Pixiv 抓不到這篇，用 ascii2d 上的作者連結）" if b["pixiv"].get("gone") else ""
            add(b["pixiv"]["artist_tag"], "artist", f"{via} → {where} 的作者{score}{gone}")
    if not (dan and dan.get("characters")):
        from . import sitetags
        words = b.get("hashtags") or [t.get("tag") for t in (b.get("pixiv") or {}).get("tags", []) if t.get("tag")]
        for n in _pixiv_character_matches([{"tag": w} for w in words]):
            add(n, "character", f"{via} → {where} 的標籤")
        try:
            for w, found in sitetags.jp_to_danbooru(words).items():
                for f in found:
                    add(f["name"], f["category"], f"{via} → {where} 的標籤 #{w}")
        except Exception as e:  # noqa
            log.info("標籤對應失敗：%s", e)
        for t in out:                # 標籤推的角色：預設不勾（寫了不代表畫的是它）
            if t["why"].endswith("的標籤") or "的標籤 #" in t["why"]:
                t["checked"] = False
    return out


def _gone_below_unlikely(rs: list[dict]) -> bool:
    """比不了的結果都排在「不太像」的結果後面，其他全部不太像 → 自動記「沒找到」。
    （作品被刪、帳號被封、推文刪掉，ascii2d 自己的縮圖也壞掉時會這樣。ascii2d 的色合検索最像的排最前面，
    前面的已經不像，後面比不了的幾乎不會是。b45 曾經排除 X——當時唯一的反例其實是縮圖名額被重複結果用光（b46 已修）；
    使用者要求 X 也一樣自動跳過。比不了的排在最前面時照樣停下來。）"""
    unl = [x.get("rank", 0) for x in rs if x.get("match") == "unlikely"]
    if not unl:
        return False
    for x in rs:
        if x.get("match") == "unlikely":
            continue
        if not (x.get("match") is None and min(unl) < x.get("rank", 0)):
            return False
    return True


def result(mid: int) -> dict:
    r = db.conn().execute("SELECT status, result, page_url FROM ascii2d WHERE media_id=?", (mid,)).fetchone()
    if not r:
        return {"status": None}
    from . import names
    d = json.loads(r[1] or "{}")
    s = db.get_settings()
    lang = s["display_lang"]
    for t in d.get("tags", []):
        t["display"] = names.display(t["name"], [], None, lang)
    # 自動確認（設定 ascii2d_auto_accept）：選中的結果比對到同一張（或很像）、而且有標籤 → 腳本全部勾選後自動按 Enter
    mode = s.get("ascii2d_auto_accept", "off")
    full = mode == "full"        # 全自動（b80）：什麼情況都自己決定，不停下來
    best = d["results"][d["best"]] if d.get("best") is not None and d.get("results") else None
    ok = {"same": ("same",), "likely": ("same", "likely"), "full": ("same", "likely")}.get(mode, ())
    # 沒有標籤可加（作品被刪、Danbooru 沒收）也一樣：確認＝記下出處（貼文網址），然後下一張
    ev = _char_evidence(mid, d, best) if r[0] == "ready" else {}
    if ev.get("conflict"):
        # Danbooru 說這張畫的是別人：不自動確認，那些角色也先不勾（多半是同一位畫師的另一張、或差分）
        d["error"] = (f"Danbooru 上這篇的角色是 {'、'.join(ev['conflict'])}，這張已確認的是 {'、'.join(ev['known'])}："
                      "自動確認先停下來，請自己看要加哪些")
        if full and r[0] == "ready" and best and best.get("match") in ok:
            d["auto"] = "accept"     # 全自動：照收創作者、作品；跟你確認的不一樣的角色不勾（_char_evidence 已經標 auto=False）
            d["auto_note"] = "角色跟你確認過的不一樣：只收創作者和作品，那些角色不勾"
    elif r[0] == "ready" and not d.get("auto") and best and (
            best.get("match") in ok or (mode == "same" and best.get("match") == "likely" and ev.get("agree"))):
        d["auto"] = "accept"        # 「很像」＋角色跟你確認過的一樣，也算夠確定
    if r[0] == "ready" and not d.get("auto") and (_gone_below_unlikely(d.get("results") or []) or not d.get("results")):
        d["auto"] = "none"        # 每個都不像／比不了的排在後面；或這頁根本沒有 X、Pixiv 的結果（使用者要求一樣自動跳過）
    if full and r[0] == "ready" and not d.get("auto"):
        # 全自動（使用者 2026-10-16：「什麼情況都全自動」）。剩下會停的：
        # - 排第一的比不了（作品被刪、ascii2d 縮圖也抓不到）、其他都不太像：開發時的紀錄裡幾乎都是沒找到 → 沒找到
        # - 比對得上但不夠像（很像以下）、沒有可以收的 → 沒找到
        # - 比對過程出錯（連不到 X／Pixiv）→ 先跳過（之後可以再排，不算沒找到）
        failed = bool(d.get("error")) and "best" not in d
        d["auto"] = "skip" if failed else "none"
        d["auto_note"] = ("比對時出錯：先跳過，之後可以再排" if failed else
                          "排第一的結果比不了、其他都不像：記「沒找到」" if best and best.get("match") is None else "沒有夠像的結果：記「沒找到」")
    return {"status": r[0], "page_url": r[2], **d, **counts()}


def _char_evidence(mid: int, d: dict, best: dict | None) -> dict:
    """這張圖已經確認的角色 vs 選中結果在 Danbooru 上的角色（b75：2,848 張插畫角色已知、缺創作者）。
    agree：有重疊（本體／造型算同一個）；conflict：兩邊都有角色、完全不重疊。
    也會改 d["tags"]：已確認的標成「你已確認」；衝突的角色不勾；標籤推的角色在角色已經夠了時「自動確認」不勾（auto=False）。"""
    c = db.conn()
    rows = c.execute("""SELECT t.name, COALESCE(cb.base_id, t.id) FROM media_tags mt JOIN tags t ON t.id=mt.tag_id
        LEFT JOIN char_base cb ON cb.tag_id=t.id
        WHERE mt.media_id=? AND mt.state=1 AND t.category='character' AND mt.source!='ascii2d'""", (mid,)).fetchall()
    if not rows:
        return {}
    known = {n for n, _ in rows}
    kbase = {b for _, b in rows}

    def base(name):
        tid = db.tag_id(name, create=False)
        if not tid:
            return None
        r = c.execute("SELECT base_id FROM char_base WHERE tag_id=?", (tid,)).fetchone()
        return r[0] if r else tid
    out = {"known": sorted(known)}
    dan = [] if not best or best.get("match") not in ("same", "likely") else ((best.get("danbooru") or {}).get("characters") or [])
    if dan:
        hit = [n for n in dan if n in known or base(n) in kbase]
        if hit:
            out["agree"] = hit
        else:
            out["conflict"] = dan
    people = (c.execute("SELECT people FROM media WHERE id=?", (mid,)).fetchone() or [None])[0] or 1
    for t in d.get("tags", []):
        if t["category"] != "character":
            continue
        if t["name"] in known or base(t["name"]) in kbase:
            t["why"] += "（跟你已確認的角色一樣）"
        elif out.get("conflict") and t["name"] in dan:
            t["auto"] = False
            if best.get("match") != "same":      # 只是「很像」又角色不同：多半是同一位畫師的另一張，先不勾
                t["checked"] = False             # （同一張就留著勾：開發時的紀錄 2 筆衝突都是同一張，兩個角色都留了）
        elif not t.get("checked") and len(kbase) >= people:
            t["auto"] = False      # 角色已經標齊了：標籤推的其他角色多半是一般詞對錯（第三次檢查：達磨→daruma_(beatmania)）
    return out


# ---------------- 你的決定 ----------------
def decide(mid: int, outcome: str, accept: list[str] | None = None) -> dict:
    """outcome：found（確認 accept 裡的標籤）／none（ascii2d 沒有）／skip（先跳過，之後再來）。
    確認的標籤＝你按了「是」，直接算確認（跟待確認頁一樣可以撤銷）；同時記下出處（貼文網址），這張圖不再算「沒搜過」。"""
    from .api.common import _apply
    r = db.conn().execute("SELECT result FROM ascii2d WHERE media_id=?", (mid,)).fetchone()
    d = json.loads((r[0] if r else None) or "{}")
    accept = [a for a in (accept or []) if a]
    known = {t["name"]: t for t in d.get("tags", [])}
    status = {"found": "done", "none": "none", "skip": "skipped"}.get(outcome)
    if not status:
        raise ValueError("outcome")
    best = d["results"][d["best"]] if d.get("best") is not None and d.get("results") else None
    with db.write_lock:
        c = db.conn()
        rows = []
        if outcome == "found":
            for name in accept:
                t = known.get(name)
                if not t:
                    continue
                tid0 = db.tag_id(name, create=False)
                had = tid0 and c.execute("SELECT 1 FROM media_tags WHERE media_id=? AND tag_id=? AND state=1",
                                         (mid, tid0)).fetchone()
                rw, _, _ = _apply("confirm", name, [mid], t["category"])
                rows += rw
                if not had:    # 本來就確認過的保留原本的來源（以前會改成 ascii2d，統計就分不出「角色已知」的圖找到率）
                    c.execute("UPDATE media_tags SET source=?, reason=? WHERE media_id=? AND tag_id=?",
                              ("ascii2d", t["why"][:200], mid, db.tag_id(name, create=False)))
            if rows:
                db.push_action(f"ascii2d 確認（{len(rows)} 個標籤）", {"tag_rows": rows})
            if best:
                c.execute("""INSERT OR REPLACE INTO media_source(media_id, status, method, similarity, post_url, source_url,
                    pixiv_id, data, fetched_at) VALUES(?, 'found', 'ascii2d', ?, ?, ?, ?, ?, ?)""",
                          (mid, round((best.get("score") or 0) * 100, 1), (best.get("danbooru") or {}).get("post_url") or best["url"],
                           best["url"], best.get("pixiv_id"),
                           json.dumps({"ascii2d": best, "danbooru": best.get("danbooru"), "pixiv": best.get("pixiv"),
                                       # 其他對得上的貼文（例如選了 X、Pixiv 也是同一張）：換高清會一起比
                                       "also": [x["url"] for x in d.get("results", []) if x is not best and x.get("match") in ("same", "likely")],
                                       "suggested": [{"name": n, "category": known[n]["category"]} for n in accept if n in known]},
                                      ensure_ascii=False), time.time()))
                if best.get("nsfw") or (best.get("pixiv") or {}).get("x_restrict"):
                    c.execute("UPDATE media SET nsfw_manual=1 WHERE id=? AND nsfw_manual IS NULL", (mid,))
                from . import upgrade
                upgrade.schedule(mid)       # 有沒有更高清的版本（背景查）
        d["accepted"] = accept if outcome == "found" else []
        now = time.time()
        c.execute("UPDATE ascii2d SET status=?, result=?, decided_at=? WHERE media_id=?",
                  (status, json.dumps(d, ensure_ascii=False), now, mid))
        c.execute("UPDATE media SET edited_at=? WHERE id=?", (now, mid))   # ascii2d 查過（找到、沒找到、跳過）也算你動過（排序「最近操作」）
        if not rows:    # 有確認標籤的已經由 push_action 記進操作紀錄
            db.log_op({"found": "ascii2d 找到（只記出處）", "none": "ascii2d 沒找到", "skip": "ascii2d 跳過"}[outcome], [mid], keys="ascii2d")
        c.commit()
    if best and outcome == "found":
        from . import importer
        importer._ensure()
        importer._remember_url(mid, {"site": best["kind"], "url": best["url"]})   # 大圖「原貼文」連結
    if rows:
        from .api.common import _schedule_rematch
        _schedule_rematch()
    return counts()
