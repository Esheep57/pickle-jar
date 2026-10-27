"""線上資料的流程：以圖搜源（原檔 MD5 → 像素指紋 → X／Pixiv ID → IQDB）、結果怎麼套用、自動搜源的佇列與排程。

連線與限速在 httpclient.py；Danbooru（資料卡、MD5、相似圖）在 danbooru.py；Pixiv 在 pixiv.py。
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading
import time

from . import db
from .httpclient import NotFound, TransientError
from .danbooru import CAT_BY_KEY, _post_summary, dan_get, danbooru_by_md5, fetch_tag_info, iqdb_search, md5_file, save_tag_info, verify_similar
from .pixiv import TWEET_ID, _pixiv_character_matches, fetch_pixiv, pixiv_id_from_name, pixiv_id_from_url, resolve_pixiv_artist


log = logging.getLogger("online")


# ---------- 檔名線索 ----------
HEX32 = re.compile(r"^([0-9a-fA-F]{32})(?:\s*\(\d+\)|_\d)?$")      # booru 下載器用原檔 MD5 當檔名

X_MEDIA = re.compile(r"^(?:x_\d{5,25}_)?([A-Za-z0-9_-]{15})(?:\s*\(\d+\))?$")          # X（Twitter）圖片的 media key，例如 G3EnWvxa4AAcQYN

LOCAL_AI = re.compile(r"^(?:ComfyUI_|\d{5}-\d{6,}|\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}-)", re.I)


def is_local_ai_output(path: str) -> bool:
    """本機 AI 生成的圖（ComfyUI / WebUI 輸出）：Danbooru 上不會有，不用上傳 IQDB。"""
    name = os.path.splitext(os.path.basename(path))[0]
    if LOCAL_AI.match(name):
        return True
    if path.lower().endswith(".png"):
        try:
            from PIL import Image as _I
            with _I.open(path) as im:
                info = im.info or {}
            return any(k in info for k in ("parameters", "prompt", "workflow"))
        except Exception:  # noqa
            return False
    return False


def lookup_source(mid: int, quick: str | None = None) -> dict:
    """單張搜源。回傳要存入 media_source 的 dict。
    quick：預判不太可能在 Danbooru 上（自動搜源時）→ 只做 MD5 / 檔名 / 重複圖這些快速比對，不上傳 IQDB。"""
    s = db.get_settings()
    row = db.conn().execute("SELECT path, kind FROM media WHERE id=?", (mid,)).fetchone()
    if not row or not os.path.isfile(row["path"]):
        raise RuntimeError("找不到檔案")
    path, name = row["path"], os.path.basename(row["path"])
    res = {"status": "notfound", "method": None, "similarity": None, "post_url": None, "source_url": None,
           "pixiv_id": pixiv_id_from_name(name), "data": {}}
    res["data"]["pixiv_from_name"] = bool(res["pixiv_id"])
    if not res["pixiv_id"]:      # 從 Pixiv 匯入的：貼文網址就是作品 ID（不靠檔名猜）
        try:
            from . import importer
            im = importer.post_url_of(row["path"])
            if im and im.get("site") == "pixiv":
                res["pixiv_id"] = pixiv_id_from_url(im.get("post_url"))
                res["data"]["pixiv_from_name"] = bool(res["pixiv_id"])
        except Exception as e:  # noqa
            log.info("讀匯入紀錄失敗 %s：%s", mid, e)
    stem = os.path.splitext(name)[0]
    # 1) MD5 完全相符（原檔直接從 Danbooru 下載的）
    r = db.conn().execute("SELECT fp, media_type FROM media WHERE path=?", (path,)).fetchone()
    fp = r[0] if r and r[0] and not r[0].startswith("v:") else md5_file(path)
    mtype = r[1] if r else None
    post = danbooru_by_md5(fp)
    if post:
        res.update(status="found", method="md5", similarity=100.0)
    # 1b) 檔名就是 MD5（Grabber 等下載器的命名）→ 就算檔案被轉檔/壓縮過也對得上原檔
    m = HEX32.match(stem)
    if not post and m and m.group(1).lower() != fp:
        post = danbooru_by_md5(m.group(1).lower())
        if post:
            res.update(status="found", method="md5_name", similarity=100.0)
    # 1b') 像素完全一樣（去掉 EXIF、PNG 重新壓縮的同一張圖）→ 用像素指紋查（見 hashscan.py）
    if not post and db.get_settings().get("auto_pixelhash", True):
        from . import hashscan
        ph = hashscan.cached_hash(fp, path)
        if ph:
            posts = dan_get("/posts.json", {"tags": f"pixelhash:{ph}", "limit": 1})
            if posts:
                post = _post_summary(posts[0])
                res.update(status="found", method="pixel_hash", similarity=100.0)
    # 1c) 檔名是 X 圖片的 media key → 找 Danbooru 上來源是這張圖的貼文
    m = X_MEDIA.match(stem)
    key = m.group(1) if m else ""
    # media key 的樣子：大小寫混合＋數字（排除 Screenshot_2024… 這類普通檔名）
    if not post and key and sum(ch.isupper() for ch in key) >= 2 and sum(ch.islower() for ch in key) >= 2 \
            and re.search(r"\d", key) and not re.match(r"(?i)(screenshot|img|image|photo|download|video)", key):
        res["data"]["x_media"] = key
        posts = dan_get("/posts.json", {"tags": f"source:*{key}*", "limit": 1})
        # 只有來源真的是這張 X 圖片（pbs.twimg.com/media/KEY）才算完全對應
        if posts and f"/media/{key}" in (posts[0].get("source") or ""):
            post = _post_summary(posts[0])
            res.update(status="found", method="x_media", similarity=100.0)
    # 2) 檔名是 Pixiv 作品 ID → 直接找 Danbooru 上的同一個 Pixiv 作品（完全對應，不用猜）
    if not post and res["pixiv_id"]:
        posts = dan_get("/posts.json", {"tags": f"pixiv:{res['pixiv_id']}", "limit": 1})
        if posts:
            post = _post_summary(posts[0])
            post["pixiv_id"] = post.get("pixiv_id") or res["pixiv_id"]
            res.update(status="found", method="pixiv_id", similarity=100.0)
    # 3) 檔名帶有 X（Twitter）貼文 ID → 找 Danbooru 上來源是這則推文的貼文
    tweet = TWEET_ID.search(os.path.splitext(name)[0])
    if not post and tweet:
        res["data"]["tweet_id"] = tweet.group(1)
        posts = dan_get("/posts.json", {"tags": f"source:*/status/{tweet.group(1)}*", "limit": 10})
        if posts:
            post = _post_summary(posts[0])
            if len(posts) > 1:  # 同一則推文有多張圖在 Danbooru：角色取聯集，當相似候選
                for key in ("characters", "copyrights", "artists"):
                    post[key] = sorted({t for p in posts for t in _post_summary(p)[key]})
            res.update(status="found", method="tweet" if len(posts) == 1 else "tweet_multi", similarity=100.0)
    # 3b) 重複的圖已經搜到 → 沿用（不是同一個檔案，所以當候選，不直接確認）
    if not post:
        dup = duplicate_source(mid)
        if dup:
            d0 = json.loads(dup["data"] or "{}")
            if d0.get("danbooru"):
                post = d0["danbooru"]
                res.update(status="found", method="duplicate", similarity=dup["similarity"] or 95.0)
                res["data"]["dup_of"] = dup["media_id"]
    # 4) IQDB 相似圖搜尋（檔名已經有 Pixiv ID / 推文 ID 時不需要）
    skip = quick
    if skip:
        pass
    elif mtype in ("photo", "cosplay", "figure"):
        skip = "真人／公仔照片"
    elif is_local_ai_output(path):
        skip = "本機 AI 生成的圖"
    if skip:
        res["data"]["skipped"] = skip
    if not post and not skip and not res["pixiv_id"] and not tweet and row["kind"] == "image" and s["online_iqdb"]:
        t0 = time.time()
        hits = iqdb_search(path)
        res["data"]["iqdb_ms"] = round((time.time() - t0) * 1000)
        res["data"]["iqdb"] = hits[:5]
        thr = float(s["online_min_similarity"])
        vmin = float(s.get("online_verify_min", 70))
        if hits and hits[0]["similarity"] >= thr:
            p = dan_get(f"/posts/{hits[0]['post_id']}.json")
            post = _post_summary(p)
            res.update(status="found", method="iqdb", similarity=hits[0]["similarity"])
        elif hits and hits[0]["similarity"] >= vmin:
            # 分數在灰色地帶（截圖、裁切、壓縮常見）：下載 Danbooru 縮圖實際比對
            for h in hits[:2]:
                if h["similarity"] < vmin:
                    break
                try:
                    v = verify_similar(path, h["post_id"])
                except Exception as e:  # noqa
                    log.info("IQDB 驗證失敗：%s", e)
                    v = None
                res["data"].setdefault("verify", []).append({"post_id": h["post_id"], "score": v})
                if v is not None and v >= (0.9 if tagger_loaded() else 0.8):
                    post = _post_summary(dan_get(f"/posts/{h['post_id']}.json"))
                    res.update(status="found", method="iqdb", similarity=h["similarity"])
                    res["data"]["verified"] = round(v, 3)
                    break
    if post:
        res["data"]["danbooru"] = post
        res["post_url"] = post["post_url"]
        res["source_url"] = post["source"] or None
        res["pixiv_id"] = res["pixiv_id"] or post.get("pixiv_id") or pixiv_id_from_url(post["source"])
    # 3) Pixiv 作品資訊（檔名或來源帶有 Pixiv ID）
    if res["pixiv_id"]:
        try:
            res["data"]["pixiv"] = fetch_pixiv(int(res["pixiv_id"]))
            res["data"]["pixiv"]["artist_tag"] = resolve_pixiv_artist(res["data"]["pixiv"].get("user_id"),
                                                                     res["data"]["pixiv"].get("user"))
            if res["status"] != "found":
                res.update(status="found", method="pixiv")
            res["source_url"] = res["source_url"] or res["data"]["pixiv"]["url"]
        except NotFound as e:
            res["data"]["pixiv_error"] = str(e)
        except Exception as e:  # noqa  Pixiv 暫時掛了：Danbooru 已經找到的結果照樣保留
            if res["status"] != "found":
                raise
            res["data"]["pixiv_error"] = str(e)[:200]
    return res


def suggested_tags(res: dict) -> list[dict]:
    """搜源結果 → 可套用的 tag 清單 [{name, category}]。"""
    out, seen = [], set()
    dan = res["data"].get("danbooru")
    if dan:
        for key, cat in CAT_BY_KEY.items():
            for t in dan.get(key, []):
                if t not in seen:
                    seen.add(t)
                    out.append({"name": t, "category": cat})
    for t in (res["data"].get("e621") or {}).get("mapped", []):     # e621：只有 Danbooru 上也有的標籤
        if t["name"] not in seen:
            seen.add(t["name"])
            out.append({**t, "via": "e621"})
    for t in (res["data"].get("rule34") or {}).get("mapped", []):   # Rule34：只有 Danbooru 上也有的標籤
        if t["name"] not in seen:
            seen.add(t["name"])
            out.append({**t, "via": "r34"})
    pix = res["data"].get("pixiv")
    if pix and pix.get("artist_tag") and not (dan and dan.get("artists")):
        if pix["artist_tag"] not in seen:
            seen.add(pix["artist_tag"])
            out.append({"name": pix["artist_tag"], "category": "artist", "via": "pixiv-author"})
    if pix:
        local = _pixiv_character_matches(pix["tags"])
        for t in local:
            if t not in seen:
                seen.add(t)
                out.append({"name": t, "category": "character", "via": "pixiv"})
        # 本機還沒有的角色／作品：用 Danbooru wiki 的其他名稱對應（暁山瑞希 → akiyama_mizuki），一樣只是候選
        if not (dan and dan.get("characters")):
            from . import sitetags
            try:
                m = sitetags.jp_to_danbooru([x.get("tag") for x in pix["tags"] if x.get("tag")])
            except Exception as e:  # noqa
                log.info("Pixiv 標籤對應失敗：%s", e)
                m = {}
            for txt, found in m.items():
                for f in found:
                    if f["name"] not in seen:
                        seen.add(f["name"])
                        out.append({"name": f["name"], "category": f["category"], "via": "pixiv", "jp": txt})
    return out


def save_source(mid: int, res: dict | None, error: str | None = None):
    if res is not None:
        res["data"]["suggested"] = suggested_tags(res)      # 可能要連網（日文標籤對應），不能在寫入鎖裡做
    with db.write_lock:
        c = db.conn()
        if res is None:
            c.execute("""INSERT OR REPLACE INTO media_source(media_id, status, fetched_at, error)
                         VALUES(?, 'error', ?, ?)""", (mid, time.time(), error))
        else:
            c.execute("""INSERT OR REPLACE INTO media_source(media_id, status, method, similarity, post_url, source_url,
                pixiv_id, data, fetched_at, error) VALUES(?,?,?,?,?,?,?,?,?,NULL)""",
                      (mid, res["status"], res["method"], res["similarity"], res["post_url"], res["source_url"],
                       res["pixiv_id"], json.dumps(res["data"], ensure_ascii=False), time.time()))
        c.commit()
    if res is not None and res.get("status") == "found":
        try:
            from . import upgrade
            upgrade.schedule(mid)           # 有沒有更高清的版本（背景查，不連網、不等）
        except Exception as e:  # noqa
            log.info("排高清版檢查失敗：%s", e)
    dan = (res or {}).get("data", {}).get("danbooru") if res and res.get("status") == "found" else None
    if dan and dan.get("general"):          # Danbooru 標了 anime_screenshot、novel_illustration… → 官方種類
        try:
            from . import official
            official.from_danbooru(mid, dan["general"])
        except Exception as e:  # noqa
            log.info("官方種類判斷失敗：%s", e)


def is_exact(res: dict, via: str | None = None) -> bool:
    """搜源結果是否「就是這張圖」：Danbooru 原檔 MD5 相符，或檔名本身就是 Pixiv 作品 ID。"""
    d = res["data"]
    if res["method"] in ("md5", "md5_name", "pixel_hash", "x_media", "pixiv_id", "tweet"):
        return True
    if d.get("pixiv_from_name"):
        if via and via.startswith("pixiv"):
            return True  # Pixiv 作者 / Pixiv 標籤對應的角色
        dan = d.get("danbooru")  # IQDB 找到的 Danbooru 貼文正好就是這個 Pixiv 作品
        return bool(dan and dan.get("pixiv_id") and str(dan["pixiv_id"]) == str(res.get("pixiv_id")))
    return False


def auto_apply(mid: int, res: dict) -> list[str]:
    """搜源最優先：
    - 就是這張圖（MD5 相符 / 檔名是 Pixiv ID）→ 角色、創作者、作品直接確認
    - Danbooru 相似圖（IQDB）→ tier 2；Pixiv（標籤對應、作者）→ tier 3；都等你確認
    然後清掉其他來源推薦、但不在搜源角色清單裡的角色候選，並把確認的身分推薦給同一組圖。"""
    if res["status"] != "found":
        return []
    from . import candidates
    from .matcher import _emb
    row = db.conn().execute("SELECT embedding FROM media WHERE id=?", (mid,)).fetchone()
    emb = _emb(row[0]) if row and row[0] else None
    applied, confirmed = [], []
    dan = res["data"].get("danbooru")
    with db.write_lock:
        c = db.conn()
        for t in res["data"].get("suggested", []):
            via = t.get("via")
            if via == "pixiv" and dan and dan.get("characters"):
                continue  # 有 Danbooru（人工整理）的角色清單時，以它為準；Pixiv 標籤只在沒有 Danbooru 資料時使用
            exact = is_exact(res, via)
            src = "pixiv" if str(via or "").startswith("pixiv") else "danbooru"
            what = {"md5": "Danbooru 原檔完全相符", "pixiv_id": "Danbooru 上的同一個 Pixiv 作品",
                    "tweet": "Danbooru 上的同一則推文", "tweet_multi": "Danbooru 上的同一則推文（多張圖）", "iqdb": f"IQDB 相似度 {round(res['similarity'] or 0)}%",
                    "pixiv": "Pixiv 作品"}.get(res["method"], "搜源")
            if src == "pixiv":
                what = "Pixiv 作品（檔名 ID）" if res["data"].get("pixiv_from_name") else "Pixiv 作品（經 Danbooru 來源）"
                what += "・作者" if via == "pixiv-author" else "・標籤對應" if via == "pixiv" else ""
            tier = candidates.T_DANBOORU_EXACT if exact else candidates.T_PIXIV if src == "pixiv" else candidates.T_DANBOORU
            ok = candidates.add(mid, t["name"], t["category"], tier, src,
                                (res["similarity"] or 100) / 100, what, emb=None if exact else emb, confirm=exact)
            if ok:
                applied.append(t["name"])
                if exact:
                    confirmed.append((t["name"], t["category"]))
        candidates.enforce_source_priority(mid)
        gen = None
        try:
            from . import sitetags
            gen = sitetags.danbooru_general_for(res)
            if gen:                 # Danbooru 人工整理的一般標籤
                sitetags.apply_general(mid, gen[0], "danbooru", gen[1])
        except Exception as e:  # noqa
            log.info("套用一般標籤失敗 %s：%s", mid, e)
        pix = res["data"].get("pixiv")
        if pix and pix.get("x_restrict"):  # Pixiv 標為 R-18 → NSFW（不覆蓋手動設定）
            c.execute("UPDATE media SET nsfw_manual=1 WHERE id=? AND nsfw_manual IS NULL", (mid,))
        c.commit()
    return applied


def tagger_loaded() -> bool:
    from . import tagger
    return tagger.loaded() is not None


def unsearched_ids(limit: int = 20000) -> list[int]:
    """還沒搜過來源的圖＋很久以前找不到的（Danbooru 一直在新增）＋之前出錯的。
    依本地預判排序：最可能在 Danbooru 上的先搜（見 likely.py）。"""
    from . import likely
    s = db.get_settings()
    old = time.time() - float(s.get("online_retry_days", 30)) * 86400
    rows = db.conn().execute("""SELECT m.id, m.mtime, s.media_id IS NULL AS fresh,
          EXISTS (SELECT 1 FROM media_tags x JOIN tags t ON t.id=x.tag_id WHERE x.media_id=m.id AND x.state=1
                  AND t.category IN ('character','artist')) AS known
        FROM media m LEFT JOIN media_source s ON s.media_id=m.id
        WHERE m.status='done' AND m.kind='image' AND COALESCE(m.excluded,0)=0
          AND m.id NOT IN (SELECT media_id FROM album_items)
          AND (s.media_id IS NULL OR (s.status='error' AND s.fetched_at < ?)
               OR (s.status='notfound' AND s.fetched_at < ? AND s.data NOT LIKE '%"skipped"%'))""",
                             (time.time() - 86400, old)).fetchall()
    sc = likely.assess([r["id"] for r in rows])
    rows = sorted(rows, key=lambda r: (-r["fresh"], -(sc.get(r["id"], (0,))[0]), r["known"], -(r["mtime"] or 0)))
    return [r["id"] for r in rows[:limit]]


def skipped_maybe_ids() -> list[int]:
    """自動搜源時因為預判只有「可能」而略過以圖搜源（沒上傳 IQDB）的圖。"""
    from . import likely
    rows = [r[0] for r in db.conn().execute("""SELECT s.media_id FROM media_source s JOIN media m ON m.id=s.media_id
        WHERE s.status='notfound' AND s.data LIKE '%"skipped"%' AND m.status='done' AND m.kind='image'
        AND COALESCE(m.excluded,0)=0 AND m.id NOT IN (SELECT media_id FROM album_items)""")]
    sc = likely.assess(rows) if rows else {}
    return [i for i in rows if likely.MAYBE <= sc.get(i, (0,))[0]]


def research_status() -> dict:
    """找不到出處的圖多久重搜一次、下一批什麼時候到期。"""
    s = db.get_settings()
    days = float(s.get("online_retry_days", 30))
    now = time.time()
    r = db.conn().execute("""SELECT COUNT(*), MIN(fetched_at), SUM(fetched_at < ?) FROM media_source
        WHERE status='notfound' AND data NOT LIKE '%"skipped"%'""", (now - days * 86400,)).fetchone()
    nxt = (r[1] + days * 86400) if r[1] else None
    return {"days": days, "notfound": r[0] or 0, "due": r[2] or 0, "next_at": nxt,
            "last_run": _last_auto.get("at")}


_last_auto: dict = {}


PERIODIC_H = 6.0


def periodic_research(interval_h: float = PERIODIC_H):
    """程式一直開著時也要重搜：每幾個小時檢查一次（以前只有新增圖片、重新開程式時才會觸發）。"""
    _last_auto.setdefault("started", time.time())

    def loop():
        while True:
            time.sleep(interval_h * 3600)
            try:
                _last_auto["at"] = time.time()
                get_jobs().auto_source()
                get_jobs().queue_missing_taginfo()
            except Exception as e:  # noqa
                log.warning("定期重搜失敗：%s", e)
    threading.Thread(target=loop, daemon=True, name="periodic-research").start()


def should_skip(mid: int) -> str | None:
    """自動搜源時，預判不太可能在 Danbooru 上的圖只做快速的 MD5 比對（回傳略過理由）。"""
    from . import likely
    lv = db.get_settings().get("online_auto_level", "maybe")
    # 真人照片（你自己的照片）不自動上傳去以圖搜圖：Danbooru 上幾乎沒有，也不該送出去。手動按 S 還是會搜
    mt = db.conn().execute("SELECT media_type FROM media WHERE id=?", (mid,)).fetchone()
    if mt and mt[0] == "photo":
        return "真人照片不自動上傳"
    from . import quicktags
    if quicktags.has_no_char(db.conn(), mid):
        return "標了梗圖／截圖（不認角色）"
    if lv == "all":
        return None
    sc, why = likely.assess([mid]).get(mid, (50, []))
    if sc < likely.MAYBE or (lv == "likely" and sc < likely.LIKELY):
        return "預判" + likely.LABEL[likely.level(sc)][:5] + ("：" + "、".join(why[:3]) if why else "")
    return None


# 可以沿用給複本的結果：只有「確定是這張圖」的方法。推測來的（從畫師反推、簽名、沿用別張）不能再傳下去
REUSABLE_METHODS = ("md5", "md5_name", "pixel_hash", "x_media", "pixiv_id", "tweet", "iqdb")


def duplicate_source(mid: int) -> dict | None:
    """真正的複本（同一張圖的不同尺寸／格式）已經有確定的搜源結果 → 沿用當候選，不用再問 Danbooru。
    每一張都直接跟這張比（不透過「A 像 B、B 像 C」的連鎖），而且要通過 pipeline.same_image。"""
    from . import pipeline
    c = db.conn()
    me = c.execute("SELECT id, dhash, width, height, embedding FROM media WHERE id=?", (mid,)).fetchone()
    if not me:
        return None
    for g in pipeline.find_duplicates(3):
        if mid in g:
            others = [x for x in g if x != mid]
            if not others:
                return None
            ph = ",".join("?" * len(others))
            rows = c.execute(f"""SELECT s.*, m.dhash, m.width, m.height, m.embedding FROM media_source s JOIN media m ON m.id=s.media_id
                WHERE s.status='found' AND s.media_id IN ({ph}) AND s.method IN ({",".join("?" * len(REUSABLE_METHODS))})
                ORDER BY s.similarity DESC""", [*others, *REUSABLE_METHODS]).fetchall()
            for r in rows:
                if pipeline.same_image(me, r):
                    return {k: r[k] for k in r.keys() if k not in ("dhash", "width", "height", "embedding")}
            return None
    return None


def repair_duplicate_reuse(dry_run: bool = True) -> dict:
    """修正以前「沿用重複圖」沿用錯的結果：用現在的標準（same_image＋只沿用確定的方法）重新檢查每一筆。
    不合格的：拿掉沿用來的搜源結果和它帶來、你還沒確認的候選；重新標記（把被收掉的 AI 候選找回來）並重新搜源。
    你已經確認過的標籤不動，只列出來給你檢查。"""
    from . import pipeline
    c = db.conn()
    rows = c.execute("""SELECT s.media_id, s.data, m.dhash, m.width, m.height, m.embedding, m.path FROM media_source s
                        JOIN media m ON m.id=s.media_id WHERE s.method='duplicate'""").fetchall()
    bad, kept, gone = [], 0, 0
    for r in rows:
        d = json.loads(r["data"] or "{}")
        src = c.execute("""SELECT s.method, m.id, m.dhash, m.width, m.height, m.embedding FROM media m LEFT JOIN media_source s ON s.media_id=m.id
                           WHERE m.id=?""", (d.get("dup_of"),)).fetchone()
        if src is None:         # 被借用的那張已經刪掉（例如在重複頁刪了其他複本）：沒辦法重新比對，保持原樣
            gone += 1
            continue
        if src["method"] in REUSABLE_METHODS and pipeline.same_image(r, src):
            kept += 1
            continue
        post = d.get("danbooru") or {}
        names = set(post.get("characters", [])) | set(post.get("copyrights", [])) | set(post.get("artists", []))
        ph = ",".join("?" * len(names)) or "''"
        tags = c.execute(f"""SELECT t.name, mt.state FROM media_tags mt JOIN tags t ON t.id=mt.tag_id
                             WHERE mt.media_id=? AND mt.source='danbooru' AND mt.state>=0 AND t.name IN ({ph})""", (r["media_id"], *names)).fetchall()
        met = None
        if src["dhash"] is not None and r["dhash"] is not None:
            ra, rb = pipeline._aspect(r["width"], r["height"]), pipeline._aspect(src["width"], src["height"])
            met = {"dhash": bin((r["dhash"] ^ src["dhash"]) & 0xFFFFFFFFFFFFFFFF).count("1"),
                   "aspect": round(abs(ra / rb - 1), 3) if ra and rb else None,
                   "emb": (lambda x: round(x, 3) if x is not None else None)(pipeline.emb_cos(r["embedding"], src["embedding"])),
                   "size": [r["width"], r["height"], src["width"], src["height"]]}
        bad.append({"id": r["media_id"], "name": os.path.basename(r["path"]), "dup_of": d.get("dup_of"), "metrics": met,
                    "why": "借用的那張不是確定結果" if src["method"] not in REUSABLE_METHODS else "不是同一張圖",
                    "pending": [t["name"] for t in tags if t["state"] == 0], "confirmed": [t["name"] for t in tags if t["state"] == 1]})
    if not dry_run and bad:
        with db.write_lock:
            for b in bad:
                if b["pending"]:
                    ph = ",".join("?" * len(b["pending"]))
                    c.execute(f"""DELETE FROM media_tags WHERE media_id=? AND source='danbooru' AND state=0
                                  AND tag_id IN (SELECT id FROM tags WHERE name IN ({ph}))""", (b["id"], *b["pending"]))
                c.execute("DELETE FROM media_source WHERE media_id=?", (b["id"],))
            c.commit()
        ids = [b["id"] for b in bad]
        pipeline.get_jobs().submit("retag", ids)            # 找回被收掉的 AI 候選
        get_jobs().queue_sources(ids, manual=True)          # 用新標準重新搜源
    return {"checked": len(rows), "kept": kept, "unverifiable": gone, "bad": bad, "dry_run": dry_run}


# ---------- 背景工作 ----------
class OnlineJobs:
    def __init__(self):
        from . import jobqueue
        self.q = jobqueue.PQueue("online")      # 存在資料庫：重啟後接著做；有優先序（見 jobqueue.py）
        self.state = {"running": False, "phase": "", "done": 0, "total": 0, "found": 0, "error": None, "eta": None}
        self._cnt = threading.Lock()
        self.stop_flag = threading.Event()
        self._queued_tags: set[str] = set()
        self._queued_src: set[int] = set()
        self._manual: set[int] = set()   # 使用者手動按的搜源：不做預判，一定搜
        self._t0 = time.time()
        self._grp: dict | None = None     # 正在做的那一組（進度、找到幾張是整組累計）
        import collections
        self.history: collections.deque = collections.deque(maxlen=20)
        for r in self.q.rows():           # 上次沒做完的：排隊標記接回來，避免重複排
            self._mark(r["kind"], r["arg"], r["manual"])
        threading.Thread(target=self._loop, daemon=True, name="online").start()

    def queued_count(self) -> int:
        """排隊中的組數（不含正在做的那組）。"""
        cur = (self._grp or {}).get("grp")
        return sum(1 for g in self.q.groups() if g["grp"] != cur)

    def _mark(self, kind, arg, manual=False):
        if not isinstance(arg, list):
            return
        if kind == "taginfo":
            self._queued_tags.update(arg)
        elif kind == "seriesname":
            self._queued_tags.update("zh:" + k for k in arg)
        elif kind == "seriesparent":
            self._queued_tags.update("sp:" + k for k in arg)
        elif kind == "source":
            self._queued_src.update(arg)
            if manual:
                self._manual.update(arg)

    def submit(self, kind: str, arg, prio: int | None = None, manual: bool = False):
        from . import jobqueue
        if prio is None:
            prio = jobqueue.BACKGROUND if kind == "source" and not manual else jobqueue.NORMAL
        return self.q.put(kind, arg, prio, manual)

    def queue_sources(self, ids: list[int], manual: bool = False):
        """搜源。manual（你按的）：不做預判、一定搜，而且插隊到背景補搜前面（少量的最優先）。"""
        from . import jobqueue
        ids = list(dict.fromkeys(int(i) for i in ids))
        prio = (jobqueue.MANUAL if len(ids) <= 50 else jobqueue.NORMAL) if manual else jobqueue.BACKGROUND
        moved: set[int] = set()
        if manual:
            moved = jobqueue.remove_ids(self.q, "source", set(ids), prio)   # 本來排在背景補搜裡的，拉到前面
        new = [i for i in ids if i not in self._queued_src or i in moved]
        if manual:
            self._manual.update(new)
        self._queued_src.update(new)
        if new:
            self.submit("source", new, prio, manual)
        return len(new)

    def auto_source(self):
        """背景自動搜源（設定開啟時）。"""
        _last_auto["at"] = time.time()
        s = db.get_settings()
        if s["online_enabled"] and s.get("online_auto_source", True):
            return self.queue_sources(unsearched_ids())
        return 0

    def queue_series_zh(self, keys: list[str]):
        """作品的台灣譯名：到維基百科查（見 wikiname.py）。"""
        new = [k for k in keys if ("zh:" + k) not in self._queued_tags]
        self._queued_tags.update("zh:" + k for k in new)
        if new:
            self.submit("seriesname", new)
        return len(new)

    def queue_series_cn(self) -> int:
        """簡體模式：以前只查了台灣譯名的作品，重查一次（同一個條目的大陸標題）。"""
        if not db.get_settings()["online_enabled"]:
            return 0
        from . import wikiname
        keys = [k for k in wikiname.need_cn() if ("zh:" + k) not in self._queued_tags]
        self._queued_tags.update("zh:" + k for k in keys)
        if keys:
            self.submit("seriesname", keys)
        return len(keys)

    def queue_series_parent(self, keys: list[str]):
        """作品的母系列：到 Danbooru 查 tag implication（見 seriesmap.py）。"""
        new = [k for k in keys if ("sp:" + k) not in self._queued_tags]
        self._queued_tags.update("sp:" + k for k in new)
        if new:
            self.submit("seriesparent", new)
        return len(new)

    def queue_missing_taginfo(self) -> int:
        """補查：有圖（已確認或候選）但從來沒查過 Danbooru 資料卡的角色／作品；以前查過、但作品是空的角色重查一次
        （Danbooru 上只有 original 的原創角色以前沒記下來）。線上佇列在重啟時會清空，所以啟動時和定期都要補。"""
        s = db.get_settings()
        if not (s["online_enabled"] and s.get("online_auto_charinfo", True)):
            return 0
        c = db.conn()
        names = [r[0] for r in c.execute("""SELECT t.name FROM tags t LEFT JOIN tag_info ti ON ti.tag_id=t.id
            WHERE t.category IN ('character','copyright') AND ti.tag_id IS NULL
              AND EXISTS (SELECT 1 FROM media_tags x WHERE x.tag_id=t.id AND x.state>=0)
            ORDER BY (SELECT COUNT(*) FROM media_tags x WHERE x.tag_id=t.id AND x.state=1) DESC""")]
        if not s.get("taginfo_refetch_original"):
            names += [r[0] for r in c.execute("""SELECT t.name FROM tags t JOIN tag_info ti ON ti.tag_id=t.id
                WHERE t.category='character' AND ti.status='ok' AND COALESCE(ti.copyright,'')='' AND COALESCE(ti.manual,0)=0
                  AND EXISTS (SELECT 1 FROM media_tags x WHERE x.tag_id=t.id AND x.state=1)""")]
            db.update_settings({"taginfo_refetch_original": True})
        # 手動改過作品、但從來沒拿到別名的角色：補查一次別名（見 save_tag_info）
        if not s.get("taginfo_refetch_manual"):
            names += [r[0] for r in c.execute("""SELECT t.name FROM tags t JOIN tag_info ti ON ti.tag_id=t.id
                WHERE t.category IN ('character','copyright') AND COALESCE(ti.manual,0)=1
                  AND COALESCE(ti.other_names,'[]') IN ('[]','')""")]
            db.update_settings({"taginfo_refetch_manual": True})
        # 查失敗的（網路錯誤）隔一天再試
        names += [r[0] for r in c.execute("""SELECT t.name FROM tags t JOIN tag_info ti ON ti.tag_id=t.id
            WHERE t.category IN ('character','copyright') AND ti.status='error' AND COALESCE(ti.fetched_at,0) < ?""", (time.time() - 86400,))]
        names = list(dict.fromkeys(names))
        self.queue_tag_info(names)
        return len(names)

    def queue_tag_info(self, names: list[str]):
        new = [n for n in names if n not in self._queued_tags]
        self._queued_tags.update(new)
        if new:
            self.submit("taginfo", new)

    def _forget(self, kind, arg):
        """工作結束（做完、被停止、或線上功能關著跳過）：把排隊標記清掉，之後才能再排進來。"""
        with self._cnt:
            if kind == "taginfo":
                self._queued_tags.difference_update(arg)
            elif kind == "seriesname":
                self._queued_tags.difference_update("zh:" + k for k in arg)
            elif kind == "seriesparent":
                self._queued_tags.difference_update("sp:" + k for k in arg)
            elif kind == "source":
                self._queued_src.difference_update(arg)
                self._manual.difference_update(arg)

    PHASE = {"taginfo": "抓取角色資料", "source": "以圖搜源", "artistscan": "從畫師反推",
             "r34scan": "Rule34 原檔比對", "seriesparent": "查作品的母系列", "sigscan": "讀圖上的簽名",
             "pixelhash": "像素指紋比對", "seriesname": "查作品譯名", "upgradecheck": "找更高清的版本", "pixivsweep": "Pixiv 畫師作品比對", "xsweep": "X 畫師媒體比對", "zhname": "找中文名（Bangumi）", "rerun": "補跑"}

    def _begin_group(self, item: dict):
        rows = self.q.group_rows(item["grp"])
        total = sum(len(r["arg"]) for r in rows if isinstance(r["arg"], list))
        self._grp = {"grp": item["grp"], "kind": item["kind"], "t0": time.time()}
        self._t0 = time.time()
        self.state.update(running=True, error=None, done=0, found=0, skipped=0, total=total, kind=item["kind"],
                          started=self._t0, phase=self.PHASE.get(item["kind"], item["kind"]))

    def _end_group(self, stopped: bool):
        g = self._grp or {}
        self.history.appendleft({"kind": g.get("kind"), "phase": self.state.get("phase"), "n": self.state.get("total"),
                                 "done": self.state.get("done"), "total": self.state.get("total"), "found": self.state.get("found"),
                                 "started": g.get("t0"), "ended": time.time(), "error": self.state.get("error"), "stopped": stopped})
        self._grp = None
        self.state.update(running=False, phase="", eta=None, kind=None)

    def _loop(self):
        """一次做一小塊；每塊做完都重新挑「優先序最高、最早排」的下一塊。線上功能關著時暫停（不丟掉）。"""
        while True:
            try:
                item = self.q.peek()
                enabled = db.get_settings()["online_enabled"]
            except Exception as e:  # noqa
                log.warning("讀線上佇列失敗：%s", e)
                time.sleep(5)
                continue
            if item is None or not enabled:
                if self._grp and item is None:
                    self._end_group(False)
                self.q.wait(5)
                continue
            if item["grp"] != (self._grp or {}).get("grp"):
                self._begin_group(item)
            kind, arg = item["kind"], item["arg"]
            if item["manual"] and kind == "source":
                self._manual.update(arg)
            self.stop_flag.clear()
            try:
                self._run(kind, arg)
            except Exception as e:  # noqa
                log.exception("線上工作失敗")
                self.state["error"] = f"{type(e).__name__}: {e}"
            finally:
                try:      # 這裡出錯（資料庫忙、磁碟滿）不能讓整個線上工作執行緒結束——以前會靜靜停掉，到重開程式前都不搜源
                    self.q.done(item["id"])
                    self._forget(kind, arg if isinstance(arg, list) else [])
                    stopped = self.stop_flag.is_set()
                    if stopped:       # 你按了停止：整組（還沒做的小塊）一起取消
                        for r in self.q.cancel_group(item["grp"]):
                            self._forget(r["kind"], r["arg"] if isinstance(r["arg"], list) else [])
                    if stopped or not self.q.group_rows(item["grp"]):
                        self._end_group(stopped)
                except Exception as e:  # noqa
                    log.exception("線上工作收尾失敗")
                    from . import errlog
                    errlog.record_bg("線上工作收尾", e)
                    time.sleep(5)

    def _run(self, kind, arg):
        base = self.state.get("done") or 0
        prog = lambda n: self.state.update(done=base + n)
        if kind == "taginfo":
            self._taginfo(list(arg))
        elif kind == "source":
            self._source(arg)
        elif kind == "artistscan":
            from . import artistscan
            artistscan.run(self.state, self.stop_flag.is_set, **arg)
        elif kind == "seriesparent":
            from . import seriesmap
            seriesmap.run(arg, stop=self.stop_flag.is_set, progress=prog)
        elif kind == "seriesname":
            from . import wikiname
            wikiname.run(arg, stop=self.stop_flag.is_set, progress=prog)
        elif kind == "zhname":
            from . import zhname
            zhname.run(arg, self.state, self.stop_flag.is_set)
        elif kind == "pixivsweep":
            from . import sweep
            for a in arg:
                if self.stop_flag.is_set():
                    break
                sweep.run_pixiv(self.state, self.stop_flag.is_set, keys=[a["key"]], rescan=a.get("rescan", False))
                prog(1)
        elif kind == "xsweep":
            from . import sweep
            sweep.run_x(self.state, self.stop_flag.is_set, arg["handle"], arg["items"], arg.get("final", False))
        elif kind == "upgradecheck":
            from . import upgrade
            upgrade.run_checks(arg, self.state, self.stop_flag.is_set)
        elif kind == "rerun":
            self._rerun(arg)

    def _rerun(self, items):
        """補跑（設定 → 自動標記與搜源 → 補跑）：一項＝[步驟, 圖]。一張出錯不影響其他張。"""
        from . import r34, sigscan
        fn = {"r34": r34.check_one, "sig": sigscan.one}
        for step, mid in items:
            if self.stop_flag.is_set():
                break
            try:
                r = fn[step](int(mid)) if step in fn else None
                if (isinstance(r, dict) and r.get("found")) or (step == "sig" and r):
                    with self._cnt:
                        self.state["found"] += 1
            except Exception as e:  # noqa
                if type(e).__name__ == "QuotaError":
                    self.state["error"] = str(e)
                log.info("補跑 %s %s 失敗：%s", step, mid, e)
            with self._cnt:
                self.state["done"] += 1

    def _taginfo(self, names):
        try:
            self._taginfo_inner(names)
        finally:          # 途中加進來的作品名稱也要清掉排隊標記（按停止時以前會一直卡著，到重開前都排不進來）
            with self._cnt:
                self._queued_tags.difference_update(names)

    def _taginfo_inner(self, names):
        for n in names:
            if self.stop_flag.is_set():
                break
            tid = db.tag_id(n, create=False)
            if tid:
                try:
                    info = fetch_tag_info(n)
                    if info.get("alias_to") and db.tag_id(info["alias_to"], create=False) == tid:
                        # 你自己把 Danbooru 的新名字合併進這個標籤了（方向跟 Danbooru 相反）：照你的，不再反向合併
                        log.info("Danbooru 改名 %s → %s，但你已把後者合併進前者，維持你的", n, info["alias_to"])
                    elif info.get("alias_to"):
                        # Danbooru 已經改名 → 本地也合併到新名字（舊名字記成別名，之後 AI 再給舊名字會自動導向新的）
                        from .api.tags import merge_tags
                        try:
                            merge_tags(n, info["alias_to"], queue_info=False)
                            log.info("Danbooru 改名：%s → %s，已合併", n, info["alias_to"])
                            tid = db.tag_id(info["alias_to"], create=False) or tid
                        except Exception as e:  # noqa
                            log.warning("自動合併 %s → %s 失敗：%s", n, info["alias_to"], e)
                    save_tag_info(tid, info)
                    self.state["found"] += info["status"] == "ok"
                    # 角色的作品也抓資料（作品的中文名，例如 blue_archive → 蔚藍檔案）
                    for cp in info.get("copyrights") or []:
                        ctid = db.tag_id(cp, "copyright")
                        if not db.conn().execute("SELECT 1 FROM tag_info WHERE tag_id=?", (ctid,)).fetchone() \
                                and cp not in self._queued_tags:
                            self._queued_tags.add(cp)
                            names.append(cp)
                            self.state["total"] += 1
                except Exception as e:  # noqa
                    (log.info if isinstance(e, TransientError) else log.warning)("角色資料 %s 失敗：%s", n, e)
                    save_tag_info(tid, None, str(e)[:300])
                    if "連線失敗" in str(e):
                        self.state["error"] = str(e)
            self._queued_tags.discard(n)
            self.state["done"] += 1

    def source_now(self, mid: int):
        """馬上搜這一張（不排隊）：從網站匯入的圖用，你是特地存的，應該馬上看到結果（在匯入自己的背景執行緒裡呼叫）。"""
        new_chars: set = set()
        self._manual.add(mid)
        self._queued_src.add(mid)
        self._source_one(mid, True, new_chars, count=False)
        if new_chars and db.get_settings().get("online_auto_charinfo", True):
            self.queue_tag_info(sorted(new_chars))

    def _source_one(self, mid, auto, new_chars, count: bool = True):
        if self.stop_flag.is_set():
            return
        r = db.conn().execute("SELECT path, status FROM media WHERE id=?", (mid,)).fetchone()
        if not r or r["status"] in ("trashed", "missing") or not os.path.isfile(r["path"]):
            # 排隊搜源之後你刪掉了（或檔案搬走、還沒接回）：靜靜跳過，不算錯誤、也不記成「搜過失敗」
            # （以前每張都寫進錯誤紀錄：使用者刪了 87 張重複圖，跳出 51 筆「找不到檔案」）。檔案回來時會重新排。
            log.info("搜源 %s 略過：檔案不在了", mid)
            self._queued_src.discard(mid)
            self._manual.discard(mid)
            return
        try:
            quick = None if mid in self._manual else should_skip(mid)
            res = lookup_source(mid, quick=quick)
            save_source(mid, res)
            if quick:
                with self._cnt:
                    self.state["skipped"] = self.state.get("skipped", 0) + 1
            if res["status"] == "found":
                with self._cnt:
                    self.state["found"] += 1
                if auto:
                    auto_apply(mid, res)
                new_chars.update(t["name"] for t in res["data"]["suggested"] if t["category"] in ("character", "artist"))
            else:
                self._extras(mid, manual=mid in self._manual)
        except Exception as e:  # noqa
            # 網站暫時掛掉（503、連不上）不是程式錯：記 info，之後定期重搜會再試（狀態 error）
            (log.info if isinstance(e, TransientError) else log.warning)("搜源 %s 失敗：%s", mid, e)
            save_source(mid, None, f"{type(e).__name__}: {e}"[:300])
            if "逾時" in str(e):
                self.state["error"] = "IQDB 回應很慢（伺服器端），會慢慢搜"
            elif "連線失敗" in str(e) or "429" in str(e):
                self.state["error"] = str(e)[:200]
                time.sleep(30)  # 網路斷了或被限速：休息一下再繼續
        finally:
            self._queued_src.discard(mid)
            self._manual.discard(mid)
            if count:     # 匯入時直接搜的那張不算進正在跑的那組的進度
                with self._cnt:
                    self.state["done"] += 1
                    d = self.state["done"]
                el = time.time() - self._t0
                self.state["eta"] = round(el / d * (self.state["total"] - d)) if d else None

    def _extras(self, mid, manual: bool = False):
        """Danbooru 找不到時，順便做便宜的：Rule34 原檔比對（有填 key 才做）、讀圖上的簽名（只讀 AI 判斷有簽名的圖）。
        這樣新圖不用再手動按各種「找出處」按鈕。
        （b96 拿掉：e621 原檔比對——查 15,108 張找到 14 張；trace.moe——找到 8 張，你手動標官方 174 張）"""
        from . import r34, sigscan
        s = db.get_settings()
        try:
            if s.get("auto_r34", True) and r34.configured():
                r = r34.check_one(mid)
                if r.get("found"):
                    with self._cnt:
                        self.state["found"] += 1
        except Exception as e:  # noqa
            log.info("Rule34 比對 %s 失敗：%s", mid, e)
        try:
            if s.get("auto_signature", True):
                sigscan.one(mid)
        except Exception as e:  # noqa
            log.info("讀簽名 %s 失敗：%s", mid, e)

    def _source(self, ids):
        """IQDB 回應很慢（常要 10–30 秒），所以同時送 3 張；每個網站的請求間隔仍然照限速。"""
        from concurrent.futures import ThreadPoolExecutor
        auto = db.get_settings()["online_auto_apply"]
        new_chars = set()
        found0 = self.state["found"]
        with ThreadPoolExecutor(max_workers=2, thread_name_prefix="source") as ex:
            list(ex.map(lambda m: self._source_one(m, auto, new_chars), ids))
        if new_chars and db.get_settings()["online_auto_charinfo"]:
            missing = [n for n in new_chars if not db.conn().execute(
                "SELECT 1 FROM tag_info ti JOIN tags t ON t.id=ti.tag_id WHERE t.name=?", (n,)).fetchone()]
            self.queue_tag_info(missing)
        if auto and self.state["found"] > found0:     # 這一小塊有新確認的樣本 → 重新比對（以前整組只要找到過一張，之後每塊都排）
            from . import pipeline
            pipeline.get_jobs().submit("sweep")


online_jobs: OnlineJobs | None = None


def get_jobs() -> OnlineJobs:
    global online_jobs
    if online_jobs is None:
        online_jobs = OnlineJobs()
    return online_jobs
