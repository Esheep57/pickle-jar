"""貼出處網址（b94）：圖庫裡已經有的圖，貼 X／Pixiv 的貼文網址 → 比對畫面 → 記出處、上創作者／作品／角色。

以前（使用者 10-19 問「庫內的圖想用 X 或 Pixiv 原網址上標籤怎麼處理」）：
- 從網站匯入貼同一個網址：只有檔案指紋一模一樣才接到現有的圖；手機從 X 存的、轉存過的檔案不一樣 → 另外下載一張（變重複）
- 大圖「換成高清版」貼網址：只換檔案，不記出處、不上標籤
- 只有 ascii2d 那條流程會「比對後接到現有的圖」，但要先經過 ascii2d 搜尋

做法照 ascii2d（同一套比對、同一套候選規則，它的紀錄：ascii2d 確認的創作者之後被否決 3／1,082、作品 4／590）：
1. `check(ids, url)`：讀貼文一次（X：FxTwitter＋Danbooru 有沒有收；Pixiv：作品資料＋每一頁＋Danbooru），
   每張選的圖跟貼文裡每張圖比（`danbooru.compare_images`，兩邊先裁單色邊），取最像的那頁。
   ≥0.95 同一張、≥0.9 很像（沒有 WD 模型時 0.8）、以下不太像；抓不到對方的圖＝沒辦法比對。
   候選＝`ascii2d._tags_for`（Danbooru 有收就用 Danbooru 的；沒有就用作者帳號、標籤對應）；標籤推的角色預設不勾；
   `ascii2d._char_evidence`：跟你已確認的角色衝突時那些角色不勾。都還沒寫入，結果先放在記憶體（_PENDING，30 分鐘）。
2. `apply(token, picks)`：你勾的才確認（source `url`），一筆撤銷；記出處（media_source method `url`）、原貼文連結、
   NSFW（貼文標了敏感、Pixiv R-18）、排「找更高清的版本」。
只收 X、Pixiv 的貼文網址。一次最多 MAX_IDS 張、Pixiv 多頁最多比 MAX_PAGES 頁。
"""
from __future__ import annotations

import json
import logging
import re
import threading
import time
import uuid

from . import db

log = logging.getLogger("srcurl")
MAX_IDS = 30
MAX_PAGES = 40
_PENDING: dict[str, dict] = {}
_lock = threading.Lock()


class SrcUrlError(ValueError):
    pass


def _parse(url: str) -> dict:
    from .importer import parse
    p = parse(url)
    if not p or p["site"] not in ("x", "pixiv"):
        raise SrcUrlError("只收 X（twitter.com／x.com）或 Pixiv 的貼文網址")
    return p


# ---------- 讀貼文（一次），回傳 meta 和每一張圖 ----------
def _read_x(p: dict) -> tuple[dict, list]:
    from .ascii2d import _fetch_image
    from .danbooru import _post_summary, dan_get
    from .importer import FX_API
    from .httpclient import _req
    meta = {"kind": "x", "tweet_id": p["id"], "handle": p.get("user"), "url": p["url"], "rank": 0}
    try:
        posts = dan_get("/posts.json", {"tags": f"source:*/status/{p['id']}*", "limit": 10})
        if posts:
            post = _post_summary(posts[0])
            if len(posts) > 1:
                for key in ("characters", "copyrights", "artists"):
                    post[key] = sorted({t for q in posts for t in _post_summary(q)[key]})
            meta["danbooru"] = post
    except Exception as e:  # noqa
        meta["danbooru_error"] = str(e)[:200]
    resp = _req("fxtwitter", "GET", f"{FX_API}/status/{p['id']}")
    if resp.status_code >= 400:
        raise SrcUrlError(f"讀不到這則貼文（HTTP {resp.status_code}，可能被刪除或設為私人）")
    t = (resp.json() or {}).get("tweet") or {}
    a = t.get("author") or {}
    from . import sitetags
    meta.update(handle=a.get("screen_name") or p.get("user"), author=a.get("name"), nsfw=bool(t.get("possibly_sensitive")),
                hashtags=sitetags.hashtags(t.get("text") or ""))
    meta["url"] = f"https://x.com/{meta['handle']}/status/{p['id']}"
    photos = [m.get("url") for m in ((t.get("media") or {}).get("all") or []) if m.get("type") == "photo" and m.get("url")]
    if not photos:
        raise SrcUrlError("這則貼文沒有圖片（影片的貼文沒辦法比對畫面）")
    imgs = []
    for i, u in enumerate(photos, 1):
        img = _fetch_image(u.split("?")[0] + "?name=small" if "pbs.twimg.com" in u else u, "fxtwitter")
        imgs.append({"page": i, "img": img, "danbooru": None})
    return meta, imgs


def _read_pixiv(p: dict) -> tuple[dict, list]:
    from .ascii2d import _fetch_image
    from .danbooru import _post_summary, dan_get
    from .httpclient import BROWSER_UA, _req
    from .pixiv import PIXIV, fetch_pixiv, resolve_pixiv_artist
    pid = int(p["id"])
    meta = {"kind": "pixiv", "pixiv_id": str(pid), "url": p["url"], "rank": 0}
    try:
        pix = fetch_pixiv(pid)
        pix["artist_tag"] = resolve_pixiv_artist(pix.get("user_id"), pix.get("user"))
        meta["pixiv"] = pix
        meta["author"] = pix.get("user")
    except Exception as e:  # noqa
        raise SrcUrlError(f"讀不到這篇 Pixiv 作品（{str(e)[:120]}）")
    dan_by_page: dict[int, dict] = {}
    try:      # Danbooru 有收哪幾頁（多頁作品每頁的角色可能不一樣：照頁對）
        for q in dan_get("/posts.json", {"tags": f"pixiv:{pid}", "limit": 40}) or []:
            sm = _post_summary(q)
            m = re.search(r"_p(\d+)", sm.get("source") or "")
            dan_by_page.setdefault(int(m.group(1)) if m else 0, sm)
    except Exception as e:  # noqa
        meta["danbooru_error"] = str(e)[:200]
    s = db.get_settings()
    hd = {"User-Agent": BROWSER_UA, "Referer": "https://www.pixiv.net/"}
    ck = {"PHPSESSID": s["pixiv_phpsessid"]} if s.get("pixiv_phpsessid") else None
    resp = _req("pixiv", "GET", f"{PIXIV}/ajax/illust/{pid}/pages", headers=hd, cookies=ck)
    pages = ((resp.json() or {}).get("body") or []) if resp.status_code < 400 else []
    want = set(p.get("pages") or [])
    imgs = []
    for i, pg in enumerate(pages[:MAX_PAGES]):
        if want and i not in want:
            continue
        u = (pg.get("urls") or {}).get("small") or (pg.get("urls") or {}).get("regular") or (pg.get("urls") or {}).get("original")
        img = _fetch_image(u, "pixiv", hd) if u else None
        imgs.append({"page": i, "img": img, "danbooru": dan_by_page.get(i)})
    if not imgs and dan_by_page:       # Pixiv 的圖抓不到（R-18 沒登入）：拿 Danbooru 的圖比
        for i, sm in sorted(dan_by_page.items())[:MAX_PAGES]:
            try:
                q = dan_get(f"/posts/{sm['post_id']}.json")
                u = q.get("large_file_url") or q.get("preview_file_url")
                imgs.append({"page": i, "img": _fetch_image(u, "danbooru") if u else None, "danbooru": sm, "by": "Danbooru 的圖"})
            except Exception as e:  # noqa
                log.info("Danbooru 的圖抓不到：%s", e)
    if not imgs:
        imgs = [{"page": 0, "img": None, "danbooru": dan_by_page.get(0)}]
    return meta, imgs


# ---------- 比對 ----------
def check(ids: list[int], url: str) -> dict:
    from .ascii2d import _char_evidence, _local_image, _same_threshold, _tags_for
    from .danbooru import compare_images
    from . import names
    ids = list(dict.fromkeys(int(i) for i in ids))[:MAX_IDS]
    if not ids:
        raise SrcUrlError("先選圖")
    p = _parse(url)
    meta, imgs = _read_x(p) if p["site"] == "x" else _read_pixiv(p)
    thr = _same_threshold()
    lang = db.get_settings()["display_lang"]
    out = []
    for mid in ids:
        row = db.conn().execute("SELECT kind, status FROM media WHERE id=?", (mid,)).fetchone()
        if not row or row["kind"] != "image" or row["status"] in ("trashed", "missing"):
            out.append({"id": mid, "error": "不是圖片、或檔案不在了"})
            continue
        try:
            local = _local_image(mid)
        except Exception as e:  # noqa
            out.append({"id": mid, "error": f"讀不到這張圖：{e}"[:200]})
            continue
        best = None
        for it in imgs:
            if it["img"] is None:
                continue
            sc = compare_images(local, it["img"])
            if best is None or sc > best[0]:
                best = (sc, it)
        it = best[1] if best else imgs[0]
        sc = best[0] if best else None
        b = {**meta, "score": sc, "page": it["page"], "verify_by": it.get("by") or ("Pixiv 的圖" if meta["kind"] == "pixiv" else "X 的圖")}
        dan = it.get("danbooru") if meta["kind"] == "pixiv" else meta.get("danbooru")
        if dan:
            b["danbooru"] = dan
        else:
            b.pop("danbooru", None)
        b["match"] = None if sc is None else "same" if sc >= 0.95 else "likely" if sc >= thr else "unlikely"
        tags = _tags_for(mid, b, via="貼的網址") if b["match"] != "unlikely" else []
        d = {"tags": tags}
        ev = _char_evidence(mid, d, b)
        if b["match"] is None:           # 沒辦法比對：你特地貼的網址，候選照列，但預設都不勾，讓你自己看
            for t in tags:
                t["checked"] = False
        for t in tags:
            t["display"] = names.display(t["name"], [], None, lang)
        out.append({"id": mid, "match": b["match"], "score": None if sc is None else round(sc, 3), "page": b["page"],
                    "verify_by": b["verify_by"], "tags": tags, "conflict": ev.get("conflict"), "_b": b})
    token = uuid.uuid4().hex[:12]
    with _lock:
        now = time.time()
        for k in [k for k, v in _PENDING.items() if v["at"] < now - 1800]:
            _PENDING.pop(k, None)
        _PENDING[token] = {"at": now, "url": meta["url"], "items": {o["id"]: o for o in out if "_b" in o}}
    pub = [{k: v for k, v in o.items() if k != "_b"} for o in out]
    return {"token": token, "site": meta["kind"], "url": meta["url"], "author": meta.get("author") or meta.get("handle"),
            "pages": len(imgs), "compared": sum(1 for i in imgs if i["img"] is not None), "threshold": thr, "items": pub}


# ---------- 寫入（你按確認） ----------
def apply(token: str, picks: dict) -> dict:
    """picks：{media id: [要確認的標籤名稱]}；列在 picks 裡的圖都記出處（就算一個標籤都沒勾）。"""
    from .api.common import _apply, _schedule_rematch
    from . import importer, upgrade
    with _lock:
        pend = _PENDING.get(token)
    if not pend:
        raise SrcUrlError("比對結果過期了（超過 30 分鐘），再貼一次網址")
    picks = {int(k): [n for n in (v or []) if n] for k, v in (picks or {}).items()}
    done, rows = [], []
    now = time.time()
    with db.write_lock:
        c = db.conn()
        for mid, accept in picks.items():
            it = pend["items"].get(mid)
            if not it:
                continue
            b, known = it["_b"], {t["name"]: t for t in it["tags"]}
            for name in accept:
                t = known.get(name)
                if not t:
                    continue
                tid0 = db.tag_id(name, create=False)
                had = tid0 and c.execute("SELECT 1 FROM media_tags WHERE media_id=? AND tag_id=? AND state=1", (mid, tid0)).fetchone()
                rw, _, _ = _apply("confirm", name, [mid], t["category"])
                rows += rw
                if not had:     # 本來就確認過的保留原本的來源
                    c.execute("UPDATE media_tags SET source='url', reason=? WHERE media_id=? AND tag_id=?",
                              (t["why"][:200], mid, db.tag_id(name, create=False)))
            post_url = (b.get("danbooru") or {}).get("post_url") or b["url"]
            c.execute("""INSERT OR REPLACE INTO media_source(media_id, status, method, similarity, post_url, source_url,
                pixiv_id, data, fetched_at) VALUES(?, 'found', 'url', ?, ?, ?, ?, ?, ?)""",
                      (mid, round((b.get("score") or 0) * 100, 1), post_url, b["url"], b.get("pixiv_id"),
                       json.dumps({"url": {k: v for k, v in b.items() if k not in ("danbooru", "pixiv")}, "danbooru": b.get("danbooru"),
                                   "pixiv": b.get("pixiv"), "suggested": [{"name": n, "category": known[n]["category"]} for n in accept if n in known]},
                                  ensure_ascii=False, default=str), now))
            if b.get("nsfw") or (b.get("pixiv") or {}).get("x_restrict"):
                c.execute("UPDATE media SET nsfw_manual=1 WHERE id=? AND nsfw_manual IS NULL", (mid,))
            c.execute("UPDATE media SET edited_at=? WHERE id=?", (now, mid))
            done.append(mid)
        if rows:
            db.push_action(f"貼出處網址（{len(done)} 張、{len(rows)} 個標籤）", {"tag_rows": rows}, touched=done)
        elif done:
            db.log_op(f"貼出處網址：只記出處（{len(done)} 張）", done, keys="srcurl")
        c.commit()
    importer._ensure()
    for mid in done:
        it = pend["items"][mid]
        importer._remember_url(mid, {"site": it["_b"]["kind"], "url": it["_b"]["url"]})    # 大圖「原貼文」連結
        upgrade.schedule(mid)        # 對方比較大的話，大圖會出現「↑ 高清」
    if rows:
        _schedule_rematch()
    return {"applied": len(done), "tags": len(rows)}
