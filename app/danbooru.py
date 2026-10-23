"""Danbooru：API、角色／作品資料卡（wiki）、原檔 MD5、相似圖搜尋（Danbooru 內建的 iqdb API，或公開的 IQDB 網站）。"""
from __future__ import annotations

import logging
import hashlib
import io
import json
import os
import re
import time
from collections import Counter
from pathlib import Path
from urllib.parse import quote

import httpx

from . import db, media
from .httpclient import BROWSER_UA, MIN_INTERVAL, NotFound, _last, _rate_lock, _req, client

log = logging.getLogger("online.danbooru")

DANBOORU = os.environ.get("LIB_DANBOORU", "https://danbooru.donmai.us")

IQDB = os.environ.get("LIB_IQDB", "https://danbooru.iqdb.org/")

CAT_BY_KEY = {"characters": "character", "copyrights": "copyright", "artists": "artist"}


def _dan_auth() -> dict:
    s = db.get_settings()
    if s["danbooru_login"] and s["danbooru_api_key"]:
        return {"login": s["danbooru_login"], "api_key": s["danbooru_api_key"]}
    return {}


def dan_get(path: str, params: dict | None = None):
    r = _req("danbooru", "GET", DANBOORU + path, params={**(params or {}), **_dan_auth()})
    if r.status_code == 404:
        raise NotFound(path)
    if r.status_code >= 400:
        raise RuntimeError(f"Danbooru HTTP {r.status_code}")
    return r.json()


# ---------- DText → 純文字 ----------
def dtext_summary(body: str, limit: int = 320) -> str:
    t = body or ""
    t = re.sub(r"\[\[([^\]|]+)\|([^\]]*)\]\]", lambda m: m.group(2) or re.sub(r"\s*\(.*\)$", "", m.group(1)), t)
    t = re.sub(r"\[\[([^\]]+)\]\]", r"\1", t)
    t = re.sub(r"\{\{([^}]+)\}\}", r"\1", t)
    t = re.sub(r'"([^"]+)":\[?https?://\S+\]?', r"\1", t)
    t = re.sub(r"\[/?(b|i|u|s|tn|spoilers?|expand[^\]]*|quote|code|nodtext|table|tr|td|th|url[^\]]*)\]", "", t)
    paras = [p.strip() for p in re.split(r"\r?\n\s*\r?\n", t) if p.strip()]
    paras = [p for p in paras if not re.match(r"^(h\d\.|\*|See also|External links)", p)]
    out = paras[0] if paras else ""
    return (out[:limit] + "…") if len(out) > limit else out


# ---------- 角色資料卡 ----------
def dan_alias(name: str) -> str | None:
    """Danbooru 改過名的 tag（例如 itadori_yuuji → itadori_yuji、gojou_satoru → gojo_satoru）→ 新名字。
    WD Tagger 是用舊資料訓練的，常常吐出舊名字，舊名字在 Danbooru 上查不到資料（所以才會顯示羅馬拼音）。"""
    try:
        r = dan_get("/tag_aliases.json", {"search[antecedent_name]": name, "search[status]": "active", "limit": 1})
    except Exception:  # noqa
        return None
    return r[0].get("consequent_name") if r else None


def _artist_links(a: dict) -> list[str]:
    """Danbooru 畫師登記的網址：只收有效的、整理格式、常用的（X、Pixiv、Fanbox…）排前面。"""
    from .artistlinks import _norm, _rank
    urls = [_norm(u["url"]) for u in a.get("urls", []) if u.get("is_active", True) and u.get("url")]
    return sorted(dict.fromkeys(u for u in urls if u), key=_rank)


def fetch_tag_info(name: str, _depth: int = 0) -> dict:
    norm = re.sub(r"\s+", "_", name.strip()).lower()
    if _depth == 0 and norm != name:
        # 手動打的名字大小寫／空格跟 Danbooru 不同（Berull_ogr → berull_ogr）：Danbooru 有就當成同一個，合併過去
        # 只限創作者（兩邊都是畫師）：角色／作品同名不一定是同一個（例如你自創的角色剛好跟別人撞名），不自動合併
        local = db.conn().execute("SELECT category FROM tags WHERE name=?", (name,)).fetchone()
        t = dan_get("/tags.json", {"search[name]": norm, "only": "name,category,post_count"}) \
            if local and local["category"] == "artist" else None
        if t and t[0].get("post_count") and t[0].get("category") == 1:
            info = fetch_tag_info(norm, 1)
            info["alias_to"] = norm
            return info
    info = {"status": "notfound", "copyright": None, "copyrights": [], "other_names": [], "summary": "",
            "post_count": None, "url": f"{DANBOORU}/wiki_pages/{quote(name, safe='/')}", "links": []}
    tags = dan_get("/tags.json", {"search[name]": name, "only": "name,category,post_count"})
    if _depth == 0 and (not tags or not tags[0].get("post_count")):
        new = dan_alias(name)
        if new and new != name:
            info = fetch_tag_info(new, 1)
            info["alias_to"] = new
            return info
    if tags:
        info["post_count"] = tags[0].get("post_count")
    if tags and tags[0].get("category") == 1:  # 創作者：Danbooru artist 資料（別名 + Pixiv/Twitter 等連結）
        arts = dan_get("/artists.json", {"search[name]": name, "limit": 1,
                                         "only": "id,name,is_deleted,other_names,urls"})
        if arts and not arts[0].get("is_deleted"):
            a = arts[0]
            info.update(status="ok", other_names=a.get("other_names") or [],
                        links=_artist_links(a),
                        url=f"{DANBOORU}/artists/{a.get('id')}" if a.get("id") else info["url"])
        info["status"] = "ok"
        return info
    try:
        # 名字要編碼：「ざか90%」直接放進網址是壞掉的 %-編碼，Danbooru 回 400（斜線保留，fate/grand_order 這類照舊）
        w = dan_get(f"/wiki_pages/{quote(name, safe='/')}.json")
        if not w.get("is_deleted"):
            info.update(status="ok", other_names=w.get("other_names") or [], summary=dtext_summary(w.get("body", "")))
    except NotFound:
        pass
    # 所屬作品：取該角色最近的貼文，統計最常一起出現的 copyright tag
    if tags and tags[0].get("category") == 4:
        posts = dan_get("/posts.json", {"tags": name, "limit": 40, "only": "tag_string_copyright"})
        cnt = Counter(c for p in posts for c in (p.get("tag_string_copyright") or "").split())
        orig = cnt.pop("original", 0)
        if not cnt and orig:
            # 畫師的原創角色：Danbooru 上只標 original → 歸到「原創」（以前被當成沒有作品，全擠在「未知作品」）
            info.update(copyrights=["original"], copyright="original", status="ok")
        if cnt:
            top = [c for c, n in cnt.most_common(4) if n >= max(2, 0.25 * len(posts))]
            info["copyrights"] = top or [cnt.most_common(1)[0][0]]
            info["copyright"] = info["copyrights"][0]
            info["status"] = "ok"
    if info["status"] == "notfound" and tags:
        info["status"] = "ok"
    if not tags:
        # Danbooru 上沒有這個 tag（多半是你自己建的，例如「未花」）→ 用別名反查 Danbooru 的正式名稱
        try:
            hits = dan_get("/wiki_pages.json", {"search[other_names_match]": name.replace("_", " "), "limit": 5,
                                                "only": "title,other_names"})
            info["match_suggest"] = [h["title"] for h in hits if h.get("title")][:5]
        except Exception:  # noqa
            pass
    return info


def save_tag_info(tid: int, info: dict | None, error: str | None = None):
    with db.write_lock:
        c = db.conn()
        r = c.execute("SELECT manual, links, other_names FROM tag_info WHERE tag_id=?", (tid,)).fetchone()
        if r and r["manual"]:
            # 使用者手動改過的（作品、顯示名稱）不覆蓋；但別名、連結、說明、張數照樣補上——
            # 以前手動改過作品的角色就再也拿不到中文名（例如 togawa_sakiko 一直顯示 Togawa Sakiko、搜「祥子」找不到）
            if info and info.get("status") == "ok":
                links = list(dict.fromkeys((info.get("links") or []) + json.loads(r["links"] or "[]")))
                nms = list(dict.fromkeys(json.loads(r["other_names"] or "[]") + info["other_names"]))
                c.execute("""UPDATE tag_info SET other_names=?, links=?, summary=COALESCE(NULLIF(summary,''), ?),
                             post_count=COALESCE(?, post_count), url=COALESCE(url, ?), fetched_at=?, error=NULL WHERE tag_id=?""",
                          (json.dumps(nms, ensure_ascii=False), json.dumps(links, ensure_ascii=False), info.get("summary"),
                           info.get("post_count"), info.get("url"), time.time(), tid))
            else:
                c.execute("UPDATE tag_info SET fetched_at=?, error=? WHERE tag_id=?", (time.time(), error, tid))
        elif info is None:
            c.execute("""INSERT INTO tag_info(tag_id, status, fetched_at, error) VALUES(?, 'error', ?, ?)
                ON CONFLICT(tag_id) DO UPDATE SET status='error', fetched_at=excluded.fetched_at, error=excluded.error""",
                      (tid, time.time(), error))
        else:
            # 合併既有的連結/別名（例如從 Pixiv 建立的創作者，Danbooru 上沒有資料時不要洗掉）
            links = list(dict.fromkeys((info.get("links") or []) + (json.loads(r["links"] or "[]") if r else [])))
            names = list(dict.fromkeys(info["other_names"] + (json.loads(r["other_names"] or "[]") if r else [])))
            status = "ok" if (info["status"] == "ok" or links) else info["status"]
            prev = c.execute("SELECT display_name FROM tag_info WHERE tag_id=?", (tid,)).fetchone()
            c.execute("""INSERT OR REPLACE INTO tag_info(tag_id, status, copyright, copyrights, other_names, summary,
                post_count, url, manual, fetched_at, error, links, display_name, match_suggest)
                VALUES(?,?,?,?,?,?,?,?,0,?,NULL,?,?,?)""",
                      (tid, status, info["copyright"], json.dumps(info["copyrights"], ensure_ascii=False),
                       json.dumps(names, ensure_ascii=False), info["summary"], info["post_count"],
                       info["url"], time.time(), json.dumps(links, ensure_ascii=False),
                       prev["display_name"] if prev else None,
                       json.dumps(info.get("match_suggest")) if info.get("match_suggest") else None))
        c.commit()
    if info and info.get("status") == "ok":       # 角色／作品：到 Bangumi 找中文名（背景；找到就用，你在「中文名整理」看得到換了什麼）
        try:
            row = db.conn().execute("SELECT name, category FROM tags WHERE id=?", (tid,)).fetchone()
            if row and row[1] in ("character", "copyright"):
                from . import zhname
                zhname.schedule(row[0])
            from . import implies
            if row and row[1] == "character":       # 新角色：問 Danbooru 有沒有蘊含（造型 ⇒ 本體，b84）
                implies.schedule([tid])
                implies.sync_copyrights([tid])      # 角色 ⇒ 作品照資料卡（b93）
            elif row and row[1] == "copyright":     # 新作品：資料卡裡有它的角色都補上規則
                implies.sync_copyrights()
        except Exception as e:  # noqa
            log.info("排中文名查詢失敗：%s", e)
    # Danbooru 查不到的創作者（多半是你手動新增的）→ 用名字到其他網站找連結（背景）
    if info and info.get("status") == "notfound" and not (r and r["manual"]):
        try:
            from . import artistlinks
            artistlinks.enrich_name_later(tid)
        except Exception as e:  # noqa
            log.info("補連結排程失敗：%s", e)


# ---------- Danbooru 貼文 / IQDB ----------
def _post_summary(p: dict) -> dict:
    sp = lambda k: [t for t in (p.get(k) or "").split() if t]
    return {
        "post_id": p.get("id"), "post_url": f"{DANBOORU}/posts/{p.get('id')}", "source": p.get("source") or "",
        "rating": p.get("rating"), "characters": sp("tag_string_character"), "copyrights": sp("tag_string_copyright"),
        "artists": sp("tag_string_artist"), "pixiv_id": p.get("pixiv_id"),
        **({"general": sp("tag_string_general")} if "tag_string_general" in p else {}),
        "size": [p.get("image_width"), p.get("image_height")],
    }


def md5_file(path: str) -> str:
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def danbooru_by_md5(md5: str) -> dict | None:
    posts = dan_get("/posts.json", {"tags": f"md5:{md5}", "limit": 1})
    return _post_summary(posts[0]) if posts else None


def trim_borders(img):
    """裁掉截圖四周的單色邊（手機截圖的黑/白底、App 介面留白），讓以圖搜圖比較準。"""
    from PIL import ImageChops, Image as _I
    rgb = media.to_rgb(img)
    w, h = rgb.size
    small = rgb.copy()
    small.thumbnail((400, 400))
    sw, sh = small.size
    box = None
    for corner in ((0, 0), (sw - 1, 0), (0, sh - 1), (sw - 1, sh - 1)):
        bg = _I.new("RGB", small.size, small.getpixel(corner))
        diff = ImageChops.difference(small, bg).convert("L").point(lambda v: 255 if v > 18 else 0)
        b = diff.getbbox()
        if b and (box is None or (b[2] - b[0]) * (b[3] - b[1]) < (box[2] - box[0]) * (box[3] - box[1])):
            box = b
    if not box:
        return rgb
    bw, bh = box[2] - box[0], box[3] - box[1]
    if bw * bh < 0.2 * sw * sh or bw * bh > 0.97 * sw * sh:
        return rgb  # 裁太多（幾乎純色）或幾乎沒裁到 → 用原圖
    k = w / sw
    return rgb.crop((int(box[0] * k), int(box[1] * k), int(box[2] * k), int(box[3] * k)))


def _small_jpeg(path: str) -> bytes:
    img = trim_borders(media.load_image(Path(path)))
    img.thumbnail((600, 600))  # IQDB 內部只用很小的圖，傳小一點比較快
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=88)
    return buf.getvalue()


def verify_similar(path: str, post_id: int) -> float | None:
    """IQDB 分數不夠高時：下載 Danbooru 的縮圖，跟本地圖比對（WD 特徵；沒載入模型就用畫面相關係數）。
    回傳 0–1 的相似度，無法比對回傳 None。"""
    p = dan_get(f"/posts/{post_id}.json")
    url = p.get("large_file_url") or p.get("preview_file_url")
    if not url:
        return None
    r = _req("danbooru", "GET", url)
    if r.status_code >= 400:
        return None
    from PIL import Image as _I
    remote = media.to_rgb(_I.open(io.BytesIO(r.content)))
    local = trim_borders(media.load_image(Path(path)))
    return compare_images(local, remote)


def compare_images(local, remote) -> float:
    """兩張圖像不像（0–1）：有模型就用 WD 特徵，沒有就用縮小灰階的相關係數。ascii2d 的結果也用這個驗證。"""
    from . import tagger
    tg = tagger.ensure()   # 剛重開時模型還沒載入 → 先載入，不然 IQDB 中間分數的驗證會退回粗略比對
    if tg is not None:
        import numpy as np
        from .matcher import matcher
        _, E = tg.run(np.stack([tg.preprocess(local), tg.preprocess(remote)]))
        E = E - (matcher.mu if matcher.mu is not None and matcher.mu.shape[-1] == E.shape[-1] else 0)
        E /= np.linalg.norm(E, axis=1, keepdims=True) + 1e-8
        return float(E[0] @ E[1])
    import numpy as np
    a = np.asarray(local.convert("L").resize((48, 48)), dtype=np.float32).ravel()
    b = np.asarray(remote.convert("L").resize((48, 48)), dtype=np.float32).ravel()
    a, b = a - a.mean(), b - b.mean()
    return float(a @ b / (np.linalg.norm(a) * np.linalg.norm(b) + 1e-8))


_IQDB_BLOCK = re.compile(r"<table>(.*?)</table>", re.S | re.I)


def parse_iqdb(html: str) -> list[dict]:
    """解析 IQDB 結果頁：回傳 [{post_id, similarity, kind}]，依相似度排序。"""
    out = []
    for block in _IQDB_BLOCK.findall(html):
        head = re.search(r"<th>([^<]+)</th>", block)
        kind = head.group(1).strip().lower() if head else ""
        if "your image" in kind or "no relevant" in kind:
            continue
        link = re.search(r'href="(?:https?:)?//danbooru\.donmai\.us/posts/(\d+)"', block)
        sim = re.search(r"(\d+(?:\.\d+)?)%\s*similarity", block)
        if link and sim:
            out.append({"post_id": int(link.group(1)), "similarity": float(sim.group(1)), "kind": kind})
    return sorted(out, key=lambda x: -x["similarity"])


_dan_iqdb_ok: bool | None = None   # Danbooru 內建的相似圖 API 能不能用（第一次試過就記住）

_dan_iqdb_auth: tuple = ()

_dan_auth_bad: bool = False        # Danbooru 回 401：帳號或 API key 錯誤


def search_mode() -> dict:
    """給介面顯示：現在以圖搜源用哪條路、帳號狀態。"""
    has = bool(_dan_auth())
    if _dan_auth_bad:
        state = "bad_auth"
    elif not has:
        state = "no_auth"
    elif _dan_iqdb_ok is False:
        state = "fallback"
    else:
        state = "ok"
    return {"has_auth": has, "state": state, "reason": _dan_iqdb_last,
            "retry_in": max(0, round(_dan_iqdb_retry_at - time.time())) if state == "fallback" else None,
            "fast": _dan_iqdb_ok is True or (has and _dan_iqdb_ok is None and not _dan_auth_bad)}


def test_danbooru() -> dict:
    """設定頁的「測試」：驗證帳號＋API key，並試一次 Danbooru 相似圖搜尋。"""
    global _dan_auth_bad, _dan_iqdb_ok
    auth = _dan_auth()
    if not auth:
        return {"ok": False, "msg": "還沒填帳號和 API key"}
    try:
        r = client().get(DANBOORU + "/profile.json", params=auth, timeout=20)
    except httpx.HTTPError as e:
        return {"ok": False, "msg": f"連不到 Danbooru：{e}"}
    if r.status_code == 401 or "json" not in r.headers.get("content-type", ""):
        _dan_auth_bad = True
        return {"ok": False, "msg": "帳號或 API key 錯誤（Danbooru 拒絕登入）"}
    name = (r.json() or {}).get("name") if r.status_code == 200 else None
    if not name or name.lower() == "anonymous":
        _dan_auth_bad = True
        return {"ok": False, "msg": f"沒辦法用這組帳號登入 Danbooru（HTTP {r.status_code}），請檢查帳號名稱和 API key"}
    _dan_auth_bad = False
    from PIL import Image as _I
    buf = io.BytesIO()
    _I.new("RGB", (64, 64), (200, 120, 160)).save(buf, "JPEG")
    global _dan_iqdb_retry_at, _dan_iqdb_fails
    _dan_iqdb_ok, _dan_iqdb_retry_at, _dan_iqdb_fails = None, 0.0, 0
    t0 = time.time()
    hits = _dan_iqdb(buf.getvalue())
    if hits is None:
        return {"ok": True, "name": name, "iqdb": False,
                "msg": f"登入成功（{name}），但 Danbooru 相似圖搜尋不能用，會改用較慢的 IQDB"}
    return {"ok": True, "name": name, "iqdb": True, "ms": round((time.time() - t0) * 1000),
            "msg": f"登入成功（{name}）· 相似圖搜尋可以用（{round(time.time() - t0, 1)} 秒）"}


_dan_iqdb_fails = 0                 # 連續失敗次數

_dan_iqdb_retry_at = 0.0            # 暫時停用後，什麼時候再試

_dan_iqdb_last = ""                 # 最近一次失敗原因（給介面顯示）


def _dan_iqdb(data: bytes) -> list[dict] | None:
    """Danbooru 自己的相似圖搜尋 API（/iqdb_queries.json）。不能用時回傳 None（這張改用 IQDB）。
    偶爾失敗（逾時、限速、某張圖格式怪）不會就此停用：連續失敗 3 次才暫停 10 分鐘，之後自動再試。"""
    global _dan_iqdb_ok, _dan_iqdb_auth, _dan_auth_bad, _dan_iqdb_fails, _dan_iqdb_retry_at, _dan_iqdb_last
    auth = tuple(sorted(_dan_auth().items()))
    if auth != _dan_iqdb_auth:              # 換了帳號 → 重新開始
        _dan_iqdb_auth, _dan_iqdb_fails, _dan_iqdb_retry_at = auth, 0, 0.0
    if _dan_iqdb_ok is False and time.time() < _dan_iqdb_retry_at:
        return None

    def fail(reason: str, hard: bool = False):
        global _dan_iqdb_ok, _dan_iqdb_fails, _dan_iqdb_retry_at, _dan_iqdb_last
        _dan_iqdb_fails += 1
        _dan_iqdb_last = reason
        if hard or _dan_iqdb_fails >= 3:
            _dan_iqdb_ok = False
            _dan_iqdb_retry_at = time.time() + (1800 if hard else 600)
            log.info("Danbooru 相似圖搜尋暫停（%s），%d 分鐘後再試", reason, 30 if hard else 10)
        return None
    with _rate_lock:
        wait = MIN_INTERVAL["danbooru"] - (time.time() - _last.get("danbooru", 0))
        _last["danbooru"] = time.time() + max(wait, 0)
    if wait > 0:
        time.sleep(wait)
    try:
        r = client().post(DANBOORU + "/iqdb_queries.json", params=_dan_auth(), timeout=45,
                          files={"search[file]": ("image.jpg", data, "image/jpeg")})
    except httpx.HTTPError as e:
        return fail(f"連線問題：{type(e).__name__}")
    if r.status_code == 401 and auth:
        _dan_auth_bad = True
        return fail("帳號或 API key 錯誤", hard=True)
    if r.status_code in (403, 404, 405):
        return fail(f"HTTP {r.status_code}", hard=not auth)
    if r.status_code == 429:
        time.sleep(10)
        return fail("太頻繁（429）")
    if r.status_code == 422:
        return None                           # 這張圖 Danbooru 讀不了，單張改用 IQDB，不算壞掉
    if r.status_code >= 400 or "json" not in r.headers.get("content-type", ""):
        return fail(f"HTTP {r.status_code}" + ("（Cloudflare 驗證頁）" if "html" in r.headers.get("content-type", "") else ""))
    _dan_iqdb_ok, _dan_iqdb_fails, _dan_auth_bad, _dan_iqdb_last = True, 0, False, ""
    out = []
    for h in r.json() or []:
        pid = h.get("post_id") or (h.get("post") or {}).get("id")
        if pid:
            out.append({"post_id": int(pid), "similarity": float(h.get("score") or 0), "kind": "danbooru"})
    return sorted(out, key=lambda x: -x["similarity"])


def iqdb_search(path: str) -> list[dict]:
    data = _small_jpeg(path)
    hits = _dan_iqdb(data)
    if hits is not None:
        return hits
    # danbooru.iqdb.org：有時候一張要等一分鐘；逾時就算這次沒找到，不重試（之後會再排）
    t0 = time.time()
    with _rate_lock:
        wait = MIN_INTERVAL["iqdb"] - (time.time() - _last.get("iqdb", 0))
        _last["iqdb"] = time.time() + max(wait, 0)
    if wait > 0:
        time.sleep(wait)
    try:
        r = client().post(IQDB, files={"file": ("image.jpg", data, "image/jpeg")}, timeout=httpx.Timeout(60, connect=15),
                          data={"MAX_FILE_SIZE": "8388608"}, headers={"User-Agent": BROWSER_UA})
    except httpx.TimeoutException:
        from .httpclient import TransientError
        raise TransientError(f"IQDB 逾時（等了 {round(time.time() - t0)} 秒）")
    except httpx.TransportError as e:
        from .httpclient import TransientError
        raise TransientError(f"連線失敗：{e}")
    if r.status_code >= 400:
        raise RuntimeError(f"IQDB HTTP {r.status_code}")
    return parse_iqdb(r.text)
