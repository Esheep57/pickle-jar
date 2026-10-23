"""圖庫清單、單張資料、縮圖/原檔、NSFW/排除/刪除、重複。"""
from __future__ import annotations

import json
import os
import time
import subprocess
import sys
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from .. import candidates, db, media, names, pipeline, query
from ..query import IDENT_IDS, MISSING, NSFW, OK, SORTS, where as _where

router = APIRouter()
from .common import Ids


NOALBUM = "m.id NOT IN (SELECT media_id FROM album_items)"


@router.get("/api/media")
def list_media(view: str = "all", tag: str | None = None, q: str = "", sort: str = "new",
               nsfw: str = "hide", offset: int = 0, limit: int = 100, miss: str = "all", src: str | None = None,
               mtype: str | None = None, bucket: str | None = None, album: int | None = None,
               seed: int = 0, until: float | None = None, rev: bool = False, noalbum: bool = False, ids_only: bool = False):
    """seed：隨機排序的種子（同一次瀏覽每一頁用同一個，才不會重複、漏圖）；
    until：只列這個時間以前加入的（同一次瀏覽固定，背景一直加新圖時往下捲才不會跟已經載入的重疊）。"""
    c = db.conn()
    tid = db.tag_id(tag, create=False) if tag else None
    if tag and tid is None:
        return {"total": 0, "items": []}
    if tid is not None and not src:  # 身分標籤預設只看已確認
        cat = c.execute("SELECT category FROM tags WHERE id=?", (tid,)).fetchone()[0]
        if cat in candidates.IDENTITY:
            src = "confirmed"
    limit = max(1, min(int(limit), 1000))
    offset = max(0, int(offset))
    if src == "candidates":
        sort = "tier"
    join, where, params = _where(view, nsfw, q, tid, miss, src, mtype or None, bucket or None)
    if tid is None and sort in ("suspicious", "confident", "tier"):
        sort = "new"
    order = SORTS.get(sort, SORTS["new"])
    if album is not None:    # 作品集：只看這一本，預設依頁序
        join += " JOIN album_items ai ON ai.media_id=m.id AND ai.album_id=?"
        params = ([params[0], album, *params[1:]] if tid is not None else [album, *params])
        if sort in ("page", "new"):
            order = "ai.pos, m.id"
    if sort == "random":     # 以前 RANDOM()：每一頁重新洗牌 → 實測往下捲 1,200 張裡重複 38 張、也有圖永遠輪不到
        mult = ((int(seed) & 0x7FFFFFFF) * 2654435761 % 4294967291) | 1     # 每個種子一個很不一樣的乘數＝不同的排列
        order = f"((m.id * {mult}) % 4294967291), m.id"
    if noalbum and album is None:   # 圖庫、角色頁、作品頁不放作品集裡的頁（到作品集整本看）
        where += " AND " + NOALBUM
    if until:
        where += " AND m.added_at <= ?"
        params = [*params, float(until)]
    from ..query import order_by
    order = order_by(order, rev, sort)        # rev：正序／倒序切換
    if ids_only:      # 補跑選範圍：這個條件下的全部 id（不分頁）
        ids = [r[0] for r in c.execute(f"SELECT m.id FROM media m {join} WHERE {where} ORDER BY {order}", params)]
        return {"total": len(ids), "ids": ids}
    total = c.execute(f"SELECT COUNT(*) FROM media m {join} WHERE {where}", params).fetchone()[0]
    hidden = 0
    if noalbum and album is None and not offset:     # 藏起來的作品集頁有幾張（畫面上說一聲，張數才對得起來）
        hidden = c.execute(f"SELECT COUNT(*) FROM media m {join} WHERE {where.replace(NOALBUM, '1')}", params).fetchone()[0] - total
    # tv：縮圖版本（重新標記＝縮圖重做時會變）→ 前端網址帶 ?v=，瀏覽器可以長期快取、捲動時不用每張都回來問（b93）
    cols = "m.id, m.kind, m.width, m.height, m.duration, m.media_type, m.official, " + NSFW + " AS nsfw, m.rating, CAST(IFNULL(m.tagged_at, 0) AS INTEGER) AS tv"
    if tid is not None:
        cols += ", mt.score, mt.state, mt.source, mt.tier, mt.reason"
    if view == "unrecognized" and miss == "people":
        cols += ", m.people"
    rows = c.execute(f"SELECT {cols} FROM media m {join} WHERE {where} ORDER BY {order} "
                     f"LIMIT ? OFFSET ?", [*params, limit, offset]).fetchall()
    items = [dict(r) for r in rows]
    if album is not None and items and sort in ("page", "new") and not rev:
        # 好幾本合成一本：每頁帶「第幾本」，畫面在換本的地方畫分隔（頁序交錯時不畫）
        from .. import albums as _alb
        b = _alb.books(album)
        if b["books"] and b["contiguous"]:
            for it in items:
                it["sec"] = b["sec"].get(it["id"])
    if view == "unrecognized" and miss == "people" and items:
        # 多人圖缺角色：每張顯示「已標了誰」，一眼看出還缺誰
        ids = [it["id"] for it in items]
        have: dict[int, list[str]] = {}
        for mid, name, on, dn in c.execute(f"""SELECT x.media_id, t.name, ti.other_names, ti.display_name FROM media_tags x
                JOIN tags t ON t.id=x.tag_id LEFT JOIN tag_info ti ON ti.tag_id=t.id
                WHERE x.state=1 AND t.category='character' AND x.media_id IN ({','.join('?' * len(ids))})""", ids):
            have.setdefault(mid, []).append(names.display(name, json.loads(on or "[]"), dn, db.get_settings()["display_lang"]))
        for it in items:
            it["have"] = have.get(it["id"], [])
    return {"total": total, "items": items, "album_hidden": hidden}


@router.get("/api/media/{mid}")
def media_detail(mid: int):
    c = db.conn()
    m = c.execute(f"SELECT m.*, {NSFW} AS nsfw FROM media m WHERE id=?", (mid,)).fetchone()
    if not m:
        raise HTTPException(404)
    d = {k: m[k] for k in m.keys() if k != "embedding"}
    d["rating_scores"] = json.loads(m["rating_scores"] or "{}")
    d["name"] = os.path.basename(m["path"])
    d["tags"] = [dict(r) for r in c.execute("""SELECT t.name, t.category, mt.score, mt.source, mt.state, mt.tier, mt.reason
        FROM media_tags mt JOIN tags t ON t.id=mt.tag_id WHERE mt.media_id=?
        ORDER BY CASE t.category WHEN 'character' THEN 0 WHEN 'artist' THEN 1 WHEN 'copyright' THEN 2 WHEN 'custom' THEN 3 ELSE 4 END,
                 COALESCE(mt.tier, 0), mt.score DESC""", (mid,))]
    lang = db.get_settings()["display_lang"]
    info = {r[0]: (json.loads(r[1] or "[]"), r[2]) for r in c.execute("""SELECT t.name, ti.other_names, ti.display_name FROM media_tags mt
        JOIN tags t ON t.id=mt.tag_id JOIN tag_info ti ON ti.tag_id=t.id WHERE mt.media_id=?""", (mid,))}
    try:      # 造型 → 本體（大圖把「本體＋造型」兩個候選合成一列）
        bases = dict(c.execute("""SELECT t.name, b.name FROM media_tags mt JOIN tags t ON t.id=mt.tag_id
            JOIN char_base cb ON cb.tag_id=t.id JOIN tags b ON b.id=cb.base_id WHERE mt.media_id=?""", (mid,)).fetchall())
    except Exception:  # noqa
        bases = {}
    for t in d["tags"]:
        if t["name"] in bases:
            t["base"] = bases[t["name"]]
        if t["category"] == "general":
            t["zh"] = query.zh_of(t["name"])
        elif t["category"] in ("character", "artist", "copyright"):
            on, dn = info.get(t["name"], ([], None))
            t["display"] = (names.series_display(t["name"], on, dn, lang) if t["category"] == "copyright"
                            else names.display(t["name"], on, dn, lang))
    src = c.execute("SELECT * FROM media_source WHERE media_id=?", (mid,)).fetchone()
    if src:
        d["source"] = {**dict(src), "data": json.loads(src["data"] or "{}")}
    from .. import importer, likely
    try:
        d["imported"] = importer.post_url_of(m["path"])
    except Exception:  # noqa
        d["imported"] = None
    from .. import upgrade
    d["upgrade"] = upgrade.info(mid)        # 有沒有更高清的版本、換過了沒
    from .. import people
    d["crowd"] = people.info(mid)          # 多人圖：人數、已標幾個角色、是否「標完了」
    try:                                   # 同一篇 Pixiv 作品在圖庫裡有幾頁（大圖「整篇作品逐頁標角色」）
        from . import work as _work
        pid = _work._work_of(mid)
        d["pixiv_work"] = {"id": pid, "pages": len(_work.pages_of(pid))} if pid else None
    except Exception:  # noqa
        d["pixiv_work"] = None
    from .. import aigen, albums
    d["albums"] = albums.albums_of(mid)
    d["ai_meta"] = aigen.meta_of(mid) if m["ai_gen"] else None   # AI 生成資料：提示詞、模型、LoRA    # 在哪些作品集、第幾頁
    if m["kind"] == "image":
        sc, why = likely.assess([mid]).get(mid, (None, []))
        if sc is not None:
            d["likely"] = {"score": sc, "level": likely.level(sc), "label": likely.LABEL[likely.level(sc)], "why": why}
    return d


class PeopleDoneReq(BaseModel):
    ids: list[int]
    value: bool = True


@router.post("/api/media/people-done")
def people_done(body: PeopleDoneReq):
    """多人圖「角色標完了」（其他人是路人、不認識）：不再列在「多人圖缺角色」、也不再猜另一個人。"""
    from .. import charknn
    ids = [int(i) for i in body.ids]
    with db.write_lock:
        c = db.conn()
        prev, rows = [], []
        for i in range(0, len(ids), 800):
            ch = ids[i:i + 800]
            ph = ",".join("?" * len(ch))
            prev += [[r[0], r[1] or 0] for r in c.execute(f"SELECT id, people_done FROM media WHERE id IN ({ph})", ch)]
            if body.value:     # 其他人是路人：「你的圖庫」猜的另一個人也拿掉（記下來，撤銷時放回）
                rows += [[r[0], r[1], [r[2], r[3], r[4], r[5], r[6]]] for r in c.execute(
                    f"""SELECT media_id, tag_id, score, source, state, tier, reason FROM media_tags
                        WHERE media_id IN ({ph}) AND source='knn' AND state=0""", ch)]
                c.execute(f"DELETE FROM media_tags WHERE media_id IN ({ph}) AND source='knn' AND state=0", ch)
        c.executemany("UPDATE media SET people_done=? WHERE id=?", [(1 if body.value else 0, i) for i in ids])
        # 以前沒有撤銷紀錄：按完「標完了」再 Ctrl+Z，撤銷的是更早那個不相干的動作
        db.push_action(f"{'標完了' if body.value else '取消「標完了」'}（{len(ids)} 張）", {"people_done": prev, "tag_rows": rows})
        c.commit()
    charknn.schedule()
    return {"ok": True, "n": len(ids)}


@router.post("/api/media/{mid}/open")
def open_default(mid: int):
    """用系統預設程式開啟（影片用你習慣的播放器）。"""
    r = db.conn().execute("SELECT path FROM media WHERE id=?", (mid,)).fetchone()
    if not r or not os.path.isfile(r["path"]):
        raise HTTPException(404)
    p = r["path"]
    if sys.platform == "win32":
        os.startfile(p)  # noqa
    elif sys.platform == "darwin":
        subprocess.Popen(["open", p])
    else:
        subprocess.Popen(["xdg-open", p])
    return {"ok": True}


@router.get("/t/{mid}")        # 前端用這個（換網址＝丟掉以前快取一天的舊縮圖）
@router.get("/thumb/{mid}")
def thumb(mid: int, size: str = "s", v: int | None = None):
    p = media.thumb_path(mid)
    if size == "l":  # 書架大卡片用的高解析縮圖（第一次請求時產生）
        lp = media.thumb_path(mid, large=True)
        if not lp.exists():
            r = db.conn().execute("SELECT path, kind FROM media WHERE id=?", (mid,)).fetchone()
            if r and r["kind"] == "image" and os.path.isfile(r["path"]):
                try:
                    media.save_thumb(media.load_image(Path(r["path"])), mid, large=True)
                except Exception:  # noqa
                    pass
        if lp.exists():
            p = lp
    if not p.exists():
        raise HTTPException(404)
    # 沒帶版本：no-cache＝每次用 ETag 問一下有沒有變。以前快取一天：圖刪掉後編號被新圖沿用，瀏覽器會繼續顯示舊圖的縮圖。
    # 帶 ?v=（圖庫格子，b93）：版本＝標記時間，縮圖重做就換網址 → 可以一直快取。以前捲動時每張縮圖都要回伺服器問一次（304），
    # 背景工作忙的時候這一來一回就是畫面上的空白格。
    cc = "private, max-age=31536000, immutable" if v else "no-cache"
    return FileResponse(p, headers={"Cache-Control": cc})


# 瀏覽器能直接顯示的圖片格式（看檔案內容，不看副檔名：iPhone 常有 .png 其實是 JPEG）
_MAGIC = [(b"\xff\xd8\xff", "image/jpeg"), (b"\x89PNG", "image/png"), (b"GIF8", "image/gif"), (b"BM", "image/bmp")]


def _sniff(path: str) -> str | None:
    try:
        with open(path, "rb") as f:
            h = f.read(32)
    except OSError:
        return None
    for sig, mt in _MAGIC:
        if h.startswith(sig):
            return mt
    if h[:4] == b"RIFF" and h[8:12] == b"WEBP":
        return "image/webp"
    if h[4:8] == b"ftyp":
        brand = h[8:12]
        if brand in (b"avif", b"avis"):
            return "image/avif"
        if brand in (b"heic", b"heix", b"hevc", b"hevx", b"mif1", b"msf1", b"heim", b"heis"):
            return "image/heic"      # 瀏覽器不支援 → 轉 JPEG
    return None


PREVIEW_DIR = db.DATA_DIR / "preview"


@router.get("/file/{mid}")
def file(mid: int, display: int = 0):
    """原檔。display=1（大圖檢視用）：瀏覽器不能顯示的格式（HEIC、TIFF…）自動轉成 JPEG（快取在 data/preview）。"""
    r = db.conn().execute("SELECT path, kind, mtime FROM media WHERE id=?", (mid,)).fetchone()
    if not r or not os.path.isfile(r["path"]):
        raise HTTPException(404, "找不到檔案（可能被搬走或刪除）")
    if r["kind"] != "image":
        return FileResponse(r["path"])
    mt = _sniff(r["path"])
    if mt and mt != "image/heic":
        return FileResponse(r["path"], media_type=mt)
    if not display:
        return FileResponse(r["path"])
    out = PREVIEW_DIR / f"{mid}.jpg"
    if not out.exists() or out.stat().st_mtime < (r["mtime"] or 0):
        try:
            img = media.to_rgb(media.load_image(Path(r["path"])))
        except Exception as e:  # noqa
            raise HTTPException(415, f"無法轉換這個格式：{e}")
        img.thumbnail((4096, 4096))
        PREVIEW_DIR.mkdir(parents=True, exist_ok=True)
        img.save(out, "JPEG", quality=92)
    return FileResponse(out, media_type="image/jpeg", headers={"Cache-Control": "no-cache"})


# ---------- 側欄 / 角色 / tag ----------
@router.get("/api/sidebar")
def sidebar(nsfw: str = "hide", noalbum: bool = False):
    """（b96）資料沒變就沿用上次算的：見 db.read_cache。"""
    return db.read_cache("sidebar", (nsfw, noalbum,), lambda: _sidebar(nsfw, noalbum), extra=None)


def _sidebar(nsfw: str = "hide", noalbum: bool = False):
    """noalbum：圖庫、影片、NSFW、官方的張數不算作品集裡的頁（跟圖庫畫面一致；前端照「也顯示作品集裡的頁」帶）。"""
    c = db.conn()
    nf = f" AND {NSFW}=0" if nsfw == "hide" else ""
    base = f"FROM media m WHERE m.status='done' AND COALESCE(m.excluded,0)=0{nf}"
    alb = "m.id NOT IN (SELECT media_id FROM album_items)" if noalbum else "1"
    na = f" AND {alb}"
    # 同一張表的計數合併成一次掃描（「未識別／缺角色／缺創作者」的三個子查詢各只算一次）
    tot, vids, unrec, mchar, mart, mppl = c.execute(f"""SELECT COALESCE(SUM({alb}),0), COALESCE(SUM(m.kind='video' AND {alb}),0),
        COALESCE(SUM({MISSING['all']}),0), COALESCE(SUM({MISSING['character']}),0), COALESCE(SUM({MISSING['artist']}),0),
        COALESCE(SUM({MISSING['people']}),0)
        {base}""").fetchone()
    off = {k: n for k, n in c.execute(f"SELECT m.official, COUNT(*) {base}{na} AND COALESCE(m.official,'')<>'' GROUP BY m.official")}
    errors, excluded, pending, nsfw_n = c.execute(f"""SELECT COALESCE(SUM(status='error'),0),
        COALESCE(SUM(excluded=1 AND status='done'),0), COALESCE(SUM(status='pending'),0),
        COALESCE(SUM(status='done' AND COALESCE(excluded,0)=0 AND {NSFW}=1 AND {alb}),0) FROM media m""").fetchone()
    counts = {
        "all": tot, "videos": vids, "nsfw": nsfw_n, "official": sum(off.values()),
        "official_anime": off.get("anime", 0), "official_manga": off.get("manga", 0), "official_novel": off.get("novel", 0), "official_movie": off.get("movie", 0), "unrecognized": unrec, "miss_character": mchar, "miss_artist": mart, "miss_people": mppl,
        "characters": c.execute(f"""SELECT COUNT(DISTINCT mt.tag_id) FROM media_tags mt JOIN tags t ON t.id=mt.tag_id
            JOIN media m ON m.id=mt.media_id WHERE t.category='character' AND mt.state=1 AND m.status='done'{nf}""").fetchone()[0],
        # 待確認：跟待確認頁、首頁一樣算「筆數」（一張圖＋一個候選名稱＝一筆），三個地方的數字才對得起來
        "review": c.execute(f"""SELECT COUNT(*) FROM media_tags mt JOIN tags t ON t.id=mt.tag_id
            JOIN media m ON m.id=mt.media_id WHERE mt.state=0 AND mt.tag_id IN {IDENT_IDS} AND t.category IN ('character','artist','copyright')
            AND m.status='done' AND COALESCE(m.excluded,0)=0{nf}""").fetchone()[0],
        "albums": c.execute("SELECT COUNT(*) FROM albums").fetchone()[0],
        "artists": c.execute(f"""SELECT COUNT(DISTINCT mt.tag_id) FROM media_tags mt JOIN tags t ON t.id=mt.tag_id
            JOIN media m ON m.id=mt.media_id WHERE t.category='artist' AND mt.state=1 AND m.status='done'{nf}""").fetchone()[0],
        "noartist": mart, "errors": errors, "excluded": excluded, "pending": pending,
        "duplicates": sum(len(g) for g in pipeline.dup_cache.get(3, [])) if 3 in pipeline.dup_cache else None,
    }

    lang = db.get_settings()["display_lang"]

    def tag_rows(where, order, limit):
        out = []
        for r in c.execute(f"""SELECT t.name, t.category, ti.other_names, ti.display_name,
            (SELECT COUNT(*) FROM media_tags x JOIN media m ON m.id=x.media_id
             WHERE x.tag_id=t.id AND {OK} AND m.status='done'{nf}) AS n
            FROM tags t LEFT JOIN tag_info ti ON ti.tag_id=t.id WHERE {where} ORDER BY {order} LIMIT {limit}"""):
            d = dict(r)
            on, dn = json.loads(d.pop("other_names") or "[]"), d.pop("display_name")
            # 側欄也用顯示名稱（以前是 tangtang_(arknights) 這種原名，跟其他地方不一致）
            d["display"] = (names.series_display(d["name"], on, dn, lang) if d["category"] == "copyright"
                            else names.display(d["name"], on, dn, lang) if d["category"] in ("character", "artist") else d["name"])
            out.append(d)
        return out
    return {"counts": counts, "pinned": tag_rows("t.pinned=1", "t.name", 50),
            "saved": [dict(r) for r in c.execute("SELECT id, name, query, view FROM saved_search WHERE pinned=1 ORDER BY id")],
            # 最近看過：不重複列置頂的；目前 NSFW 模式下沒有圖的也不列（不然會出現一排 0）
            "recent": [r for r in tag_rows("t.last_viewed IS NOT NULL AND COALESCE(t.pinned,0)=0", "t.last_viewed DESC", 20)
                       if r["n"]][:12],
            "quick": _quick(nsfw)}


class NsfwOp(BaseModel):
    ids: list[int]
    value: int | None   # 1 / 0 / None(自動)


@router.post("/api/media/nsfw")
def set_nsfw(op: NsfwOp):
    if op.value not in (0, 1, None) or not op.ids:
        raise HTTPException(400)
    with db.write_lock:
        c = db.conn()
        prev = [[r["id"], r["nsfw_manual"]] for r in c.execute(
            f"SELECT id, nsfw_manual FROM media WHERE id IN ({','.join('?' * len(op.ids))})", op.ids)]
        c.executemany("UPDATE media SET nsfw_manual=? WHERE id=?", [(op.value, i) for i in op.ids])
        lbl = {1: "標為 NSFW", 0: "標為安全", None: "NSFW 改回自動"}[op.value]
        db.push_action(f"{lbl}（{len(op.ids)} 張）", {"nsfw": prev})
        c.commit()
    return {"ok": True}


@router.post("/api/media/tags-summary")
def tags_summary(body: Ids):
    """多選時的 tag 彙總：每個 tag 出現在幾張、其中幾張已確認。"""
    ids = body.ids[:2000]
    c = db.conn()
    agg: dict[str, dict] = {}
    for i in range(0, len(ids), 800):
        ch = ids[i:i + 800]
        for r in c.execute(f"""SELECT t.name, t.category, mt.state FROM media_tags mt JOIN tags t ON t.id=mt.tag_id
                               WHERE mt.media_id IN ({','.join('?' * len(ch))})""", ch):
            a = agg.setdefault(r["name"], {"name": r["name"], "category": r["category"], "n": 0, "confirmed": 0, "rejected": 0})
            if r["state"] >= 0:
                a["n"] += 1
                a["confirmed"] += r["state"] == 1
            else:
                a["rejected"] += 1
    order = {"character": 0, "artist": 1, "copyright": 2, "custom": 3, "general": 4}
    return {"total": len(ids), "tags": sorted(agg.values(), key=lambda a: (order.get(a["category"], 4), -a["n"]))}


class ArchiveReq(BaseModel):
    ids: list[int]
    dest: str | None = None


@router.post("/api/media/archive/preview")
def archive_preview(body: ArchiveReq):
    """刪除、移出 iCloud 前的提醒。dup_all（b98）：「重複的圖」裡整組每一張都在這次要丟的（一張都不留）有幾組。"""
    from .. import archive
    out = archive.preview(body.ids)
    want = set(body.ids)
    groups = pipeline.dup_cache.get(3) or []          # 只看已經算好的（重複頁開過才有），不為了這個重算
    out["dup_all"] = sum(1 for g in groups if len(g) > 1 and set(g) <= want)
    return out


@router.post("/api/media/archive")
def archive_start(body: ArchiveReq):
    """移出 iCloud：複製到電腦 → 比對 → 改紀錄 → 刪 iCloud 那份（背景進行）。"""
    from .. import archive
    dest = (body.dest or archive.default_dir()).strip().strip('"')
    err = archive.check_dest(dest)
    if err:
        raise HTTPException(400, err)
    pipeline.get_jobs().submit("archive", {"ids": body.ids, "dest": dest})
    return {"ok": True, "dest": dest}


@router.post("/api/media/trash")
def trash(body: Ids):
    """移到資源回收筒（可還原）。資料庫紀錄**不刪**，只標成 trashed：從回收筒還原檔案後，下次掃描用路徑或內容指紋接回，
    你確認過的角色、作者、否決都還在（以前會連紀錄一起刪，還原的檔案被當成新圖）。30 天後才真的清掉紀錄。"""
    from send2trash import send2trash
    c = db.conn()
    done, failed, merged = [], [], []
    want = set(body.ids)
    for mid in body.ids:
        r = c.execute("SELECT path FROM media WHERE id=?", (mid,)).fetchone()
        if not r:
            continue
        # 保險（b98）：還有別筆（不在這次要丟的）紀錄指著同一個檔案（只差大小寫）→ 檔案不能丟，只把這筆併過去
        k = pipeline.pkey(r["path"])
        other = [x["id"] for x in c.execute("SELECT id, path FROM media WHERE id<>? AND status NOT IN ('missing','trashed')", (mid,))
                 if pipeline.pkey(x["path"]) == k and x["id"] not in want]
        if other:
            pipeline.merge_records(other[0], [mid])
            merged.append(mid)
            continue
        try:
            if os.path.exists(r["path"]):
                send2trash(r["path"])
            done.append(mid)
        except Exception as e:  # noqa
            failed.append([mid, str(e)])
    now = time.time()
    with db.write_lock:
        c.executemany("UPDATE media SET status='trashed', trashed_at=? WHERE id=?", [(now, i) for i in done])
    if done:
        db.log_op(f"移到回收筒（{len(done)} 張）", done, keys="trash")
    if merged:
        db.log_op(f"同一個檔案的另一筆紀錄：併過去，檔案保留（{len(merged)} 張）", merged, keys="merge-twins")
    pipeline.dup_cache.clear()
    return {"trashed": len(done), "failed": failed, "merged": len(merged)}


@router.get("/api/media/{mid}/trace")
def media_trace(mid: int):
    """處理紀錄：這張圖自動跑過哪些步驟、結果是什麼（大圖檢視用，讓自動流程不再是黑箱）。"""
    import json as _json
    c = db.conn()
    s = db.get_settings()
    m = c.execute("SELECT id, kind, status, fp, tagged_at, added_at, media_type, unrec_skip FROM media WHERE id=?", (mid,)).fetchone()
    if not m:
        raise HTTPException(404)

    def tbl(name):
        return c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None
    fp = m["fp"] if m["fp"] and not str(m["fp"]).startswith("v:") else None
    out = []
    out.append({"step": "WD Tagger 標記", "at": m["tagged_at"],
                "result": {"done": "完成", "pending": "排隊中", "error": "失敗"}.get(m["status"], m["status"]) +
                          (f"（媒材：{m['media_type']}）" if m["media_type"] else ""), "ok": m["status"] == "done"})
    src = c.execute("SELECT status, method, similarity, fetched_at, data, error FROM media_source WHERE media_id=?", (mid,)).fetchone()
    d = _json.loads(src["data"] or "{}") if src and src["data"] else {}
    if not src:
        out.append({"step": "Danbooru 原檔", "result": "還沒查" + ("（自動搜源已關閉）" if not s.get("online_auto_source", True) else "（排隊中或預判後跳過）"), "ok": None})
    else:
        names = {"md5": "Danbooru 原檔 MD5 相符", "md5_name": "檔名就是 Danbooru 原檔 MD5", "x_media": "X 圖片編號對上 Danbooru",
                 "tweet": "推文 ID 對上 Danbooru", "pixiv_id": "Pixiv 作品 ID", "pixel_hash": "像素指紋相符", "iqdb": "IQDB 以圖搜圖",
                 "e621_md5": "e621 原檔相符", "e621_iqdb": "e621 以圖搜圖", "r34_md5": "Rule34 原檔相符", "artist_scan": "從畫師反推",
                 "pixiv": "Pixiv", "duplicate": "沿用重複圖的結果"}
        if src["status"] == "found":
            out.append({"step": "找出處", "at": src["fetched_at"], "ok": True,
                        "result": names.get(src["method"], src["method"] or "") + (f"（{src['similarity']:.0f}%）" if src["similarity"] and src["similarity"] < 100 else "")})
        elif src["status"] == "error":
            out.append({"step": "Danbooru／IQDB", "at": src["fetched_at"], "ok": False, "result": f"失敗：{src['error'] or ''}"[:200]})
        else:
            out.append({"step": "Danbooru 原檔", "at": src["fetched_at"], "ok": False, "result": "查過，沒有"})
            if d.get("skipped"):
                out.append({"step": "以圖搜圖（IQDB）", "at": src["fetched_at"], "ok": None, "result": f"略過：{d['skipped']}"})
            elif d.get("iqdb"):
                best = max(h.get("similarity", 0) for h in d["iqdb"])
                out.append({"step": "以圖搜圖（IQDB）", "at": src["fetched_at"], "ok": False, "result": f"最像的只有 {best:.0f}%，未採用"})
            else:
                out.append({"step": "以圖搜圖（IQDB）", "at": src["fetched_at"], "ok": False,
                            "result": "沒有結果" if s.get("online_iqdb", True) else "沒做（設定關閉了上傳）"})
    if src and src["status"] == "found":
        out.append({"step": "其他比對", "ok": None, "result": "不用了（前面已經找到出處，找到就停）"})
        from .. import upgrade
        u = upgrade.info(mid)
        if u:
            txt = {"offer": f"有：{u['site_name']} {u['width']}×{u['height']}（你的 {u['local_w']}×{u['local_h']}）",
                   "none": "查過，出處的圖沒有比較大", "done": f"已換成 {u['site_name']} {u['width']}×{u['height']}（原本 {u['old_w']}×{u['old_h']}）",
                   "failed": f"換的時候失敗：{u['error'] or ''}", "dismissed": "你選了不用換"}.get(u["status"], u["status"])
            out.append({"step": "找更高清的版本", "at": u["checked_at"], "ok": u["status"] in ("offer", "done"), "result": txt})
        else:
            out.append({"step": "找更高清的版本", "ok": None,
                        "result": "還沒查" + ("" if s.get("auto_upgrade", True) else "（已關閉）")})
        fp = None
    if fp:
        if tbl("pixhash"):
            r = c.execute("SELECT ph FROM pixhash WHERE fp=?", (fp,)).fetchone()
            if src and src["method"] == "pixel_hash":
                pass
            elif r:
                out.append({"step": "像素指紋", "ok": False if r[0] else None, "result": "查過，沒有" if r[0] else "這張圖算不了（有色彩描述檔）"})
            else:
                out.append({"step": "像素指紋", "ok": None, "result": "還沒算" + ("" if s.get("auto_pixelhash", True) else "（已關閉）")})
        for key, name, table, setting in (("r34", "Rule34 原檔", "r34_checked", "auto_r34"),):
            if src and src["method"] and src["method"].startswith(key):
                continue
            r = c.execute(f"SELECT post_id, checked_at FROM {table} WHERE fp=?", (fp,)).fetchone() if tbl(table) else None
            out.append({"step": name, "at": r[1] if r else None, "ok": False if r else None,
                        "result": "查過，沒有" if r else ("還沒查" + ("" if s.get(setting, True) else "（已關閉）"))})
        if tbl("ocr_text"):
            r = c.execute("SELECT texts FROM ocr_text WHERE fp=?", (fp,)).fetchone()
            if r:
                from .. import sigscan
                hs = sigscan.handles([tuple(x) for x in _json.loads(r[0])])
                hit = c.execute("SELECT 1 FROM media_tags WHERE media_id=? AND source='signature'", (mid,)).fetchone()
                out.append({"step": "讀圖上的簽名", "ok": True if hit else False,
                            "result": ("對上畫師" if hit else "讀到 " + "、".join(("@" if st == "x" else st + ":") + h for st, h in hs[:3]) + "，Danbooru 沒登記") if hs
                            else "沒讀到帳號或網址"})
            else:
                out.append({"step": "讀圖上的簽名", "ok": None, "result": "沒讀（只讀 AI 判斷圖上有簽名的圖）"})
    if m["unrec_skip"]:
        out.append({"step": "你按了略過", "ok": None, "result": "不列入未識別待辦；AI 不再推薦角色"})
    return {"steps": out}


@router.post("/api/media/purge-missing")
def purge_missing():
    """清掉「找不到檔案」的紀錄（你已經刪掉的圖，例如從 iCloud 清掉的）。先自動備份資料庫，後悔可以從備份還原。"""
    c = db.conn()
    ids = [r[0] for r in c.execute("SELECT id FROM media WHERE status='missing'")]
    if not ids:
        return {"purged": 0}
    db.backup(force=True)
    pipeline.forget_media(ids)
    return {"purged": len(ids)}


@router.post("/api/media/{mid}/reveal")
def reveal(mid: int):
    r = db.conn().execute("SELECT path FROM media WHERE id=?", (mid,)).fetchone()
    if not r:
        raise HTTPException(404)
    p = r["path"]
    if sys.platform == "win32":
        subprocess.Popen(["explorer", "/select,", p])
    elif sys.platform == "darwin":
        subprocess.Popen(["open", "-R", p])
    else:
        subprocess.Popen(["xdg-open", os.path.dirname(p)])
    return {"ok": True}


@router.post("/api/media/share")
def share_copy(body: Ids):
    """分享：把圖放到 Windows 剪貼簿（檔案＋單張時的圖片），到 QQ／Messenger 按 Ctrl+V。見 app/share.py。"""
    from .. import share
    if not body.ids:
        raise HTTPException(400, "沒有選圖")
    try:
        return share.copy(body.ids)
    except RuntimeError as e:
        raise HTTPException(409, str(e))


@router.get("/api/duplicates")
def duplicates(nsfw: str = "hide", dist: int = 3):
    dist = max(0, min(dist, 8))
    groups = pipeline.find_duplicates(dist)
    crops = pipeline.dup_cache.get(("crops", dist), {})
    c = db.conn()
    info = {}
    ids = [i for g in groups for i in g]
    for i in range(0, len(ids), 800):
        ch = ids[i:i + 800]
        for r in c.execute(f"""SELECT m.id, m.kind, m.width, m.height, m.size, m.duration, m.path, m.mtime, {NSFW} AS nsfw,
                               (SELECT COUNT(*) FROM media_tags x WHERE x.media_id=m.id AND x.state=1) AS confirmed,
                               (SELECT status FROM media_source s WHERE s.media_id=m.id) AS source,
                               lower(substr(m.path, length(rtrim(m.path, replace(m.path, '.', ''))) + 1)) AS ext
                               FROM media m WHERE id IN ({','.join('?' * len(ch))})""", ch):
            info[r["id"]] = {**dict(r), "name": os.path.basename(r["path"]), "folder": os.path.dirname(r["path"])}
    out = []
    for g in groups:
        items = [info[i] for i in g if i in info]
        if nsfw == "hide":
            items = [x for x in items if not x["nsfw"]]
        if len(items) > 1:
            for x in items:
                if x["id"] in crops:
                    x["crop_of"] = crops[x["id"]]       # 這張是那張的局部截圖（建議留完整的那張）
            # 完整的排前面，再來解析度高的
            items.sort(key=lambda x: ("crop_of" in x, -(x["width"] or 0) * (x["height"] or 0)))
            out.append(items)
    cs = pipeline.crop_state
    return {"groups": out, "count": sum(len(g) for g in out),
            "crop_scan": {"running": cs["running"], "checked": cs["checked"], "total": cs["total"]}}


class ExcludeReq(BaseModel):
    ids: list[int]
    value: bool = True


@router.get("/api/nsfw-ids")
def nsfw_ids():
    """所有 NSFW 圖的 id：前端用來把書架、首頁、卡片上的封面也模糊／隱藏。"""
    return [r[0] for r in db.conn().execute(f"SELECT m.id FROM media m WHERE {NSFW}=1")]


class SkipReq(BaseModel):
    ids: list[int]
    value: bool = True


def _skip(c, ids: list[int], value: bool):
    """略過（需在 write_lock 內）。回傳 (撤銷用 skip 紀錄, 撤銷用 tag_rows)。"""
    prev = []
    for i in range(0, len(ids), 800):
        ch = ids[i:i + 800]
        prev += [[r[0], r[1] or 0] for r in c.execute(
            f"SELECT id, unrec_skip FROM media WHERE id IN ({','.join('?' * len(ch))})", ch)]
    c.executemany("UPDATE media SET unrec_skip=? WHERE id=?", [(int(value), i) for i in ids])
    rows = []
    if value:
        # 略過＝「這張不用標角色」（梗圖、日常截圖剛好有角色）：拿掉 AI 的角色候選，之後也不再推薦。
        # 跟「不是」不同：不會讓 AI 學到「這不是 X」，也不會連帶拿掉相似圖上的 X
        for i in range(0, len(ids), 500):
            ch = ids[i:i + 500]
            ph = ",".join("?" * len(ch))
            for r in c.execute(f"""SELECT mt.media_id, mt.tag_id, mt.score, mt.source, mt.state, mt.tier, mt.reason
                    FROM media_tags mt JOIN tags t ON t.id=mt.tag_id WHERE mt.media_id IN ({ph}) AND mt.state=0
                    AND mt.source IN ('model','ccip','pixai') AND t.category IN ('character','artist','copyright')""", ch):
                rows.append([r[0], r[1], [r[2], r[3], r[4], r[5], r[6]]])
            c.execute(f"""DELETE FROM media_tags WHERE media_id IN ({ph}) AND state=0 AND source IN ('model','ccip','pixai')
                          AND tag_id IN {IDENT_IDS}""", ch)
    return prev, rows


@router.post("/api/media/skip")
def skip(body: SkipReq):
    """未識別頁的「略過」：圖留在圖庫，只是不再出現在未識別的待辦裡（隨時可以在「已略過」取消）。"""
    with db.write_lock:
        prev, rows = _skip(db.conn(), body.ids, body.value)
        db.push_action(f"{'略過' if body.value else '取消略過'}（{len(body.ids)} 張）", {"skip": prev, "tag_rows": rows})
    return {"ok": True, "cleared": len(rows)}


def _quick(nsfw):
    from .. import quicktags
    return quicktags.summary(nsfw, hints=False)


def _set_artist_done(c, ids: list[int], value: bool) -> list:
    """「不用找作者」（需在 write_lock 內）。回傳撤銷用的舊值。"""
    prev = []
    for i in range(0, len(ids), 800):
        ch = ids[i:i + 800]
        prev += [[r[0], r[1] or 0] for r in c.execute(f"SELECT id, artist_done FROM media WHERE id IN ({','.join('?' * len(ch))})", ch)]
    c.executemany("UPDATE media SET artist_done=? WHERE id=?", [(1 if value else 0, i) for i in ids])
    return prev


class ArtistDoneReq(BaseModel):
    ids: list[int]
    value: bool = True


@router.post("/api/media/artist-done")
def artist_done(body: ArtistDoneReq):
    """不用找作者：自己畫的、AI 生成的、真的找不到也沒關係 → 不再列在「缺創作者」。可以撤銷，也可以隨時再標作者。"""
    with db.write_lock:
        c = db.conn()
        prev = _set_artist_done(c, body.ids, body.value)
        db.push_action(f"{'不用找作者' if body.value else '取消「不用找作者」'}（{len(body.ids)} 張）", {"artist_done": prev})
        c.commit()
    return {"ok": True, "n": len(body.ids)}


class QuickReq(BaseModel):
    ids: list[int]
    name: str
    on: bool | None = None          # None＝切換（這幾張都有就拿掉）


@router.post("/api/quicktag")
def quicktag(body: QuickReq):
    """快速標籤（梗圖、真人、截圖…）：一鍵加上／拿掉；可以撤銷。"""
    from .. import quicktags
    if not body.name.strip():
        raise HTTPException(400, "沒有標籤名稱")
    return quicktags.toggle(body.ids[:20000], body.name, body.on)


@router.get("/api/quicktags")
def quicktags_summary(nsfw: str = "hide"):
    from .. import quicktags
    return quicktags.summary(nsfw)


class PileReq(BaseModel):
    bucket: str                    # p_meme …（未識別頁的分堆）
    action: str = "skip_tag"       # skip_tag＝略過＋加標籤、skip＝只略過、exclude＝排除
    tag: str = "梗圖"
    miss: str = "all"
    nsfw: str = "show"
    except_ids: list[int] = []     # 你在這一堆裡勾起來、不要一起處理的圖


@router.post("/api/unrec/pile-action")
def pile_action(body: PileReq, dry_run: bool = False):
    """整堆處理（一次撤銷就全部還原）。"""
    from ..query import BUCKETS
    ok = (body.bucket.startswith("p_") and body.action in ("skip_tag", "skip", "exclude")) or \
         (body.bucket in ("orig", "aigen") and body.action in ("artist_done", "source"))
    if body.bucket not in BUCKETS or not ok:
        raise HTTPException(400, "分堆或動作不對")
    c = db.conn()
    _, w, p = _where("unrecognized", body.nsfw, "", None, body.miss, None, None, body.bucket)
    ex = set(body.except_ids)
    ids = [r[0] for r in c.execute(f"SELECT m.id FROM media m WHERE {w}", p) if r[0] not in ex]
    if dry_run or not ids:
        return {"ok": True, "n": len(ids)}
    tag = body.tag.strip().replace(" ", "_") or "梗圖"
    if body.action == "source":      # 全部找作者：用搜源（手動＝不做預判，一定搜）
        from .. import online
        return {"ok": True, "n": len(ids), "queued": online.get_jobs().queue_sources(ids, manual=True)}
    with db.write_lock:
        payload: dict = {}
        if body.action == "artist_done":
            payload["artist_done"] = _set_artist_done(c, ids, True)
            label = "不用找作者"
        elif body.action == "exclude":
            payload["excluded"] = []
            for i in range(0, len(ids), 800):
                ch = ids[i:i + 800]
                payload["excluded"] += [[r[0], r[1] or 0] for r in c.execute(
                    f"SELECT id, excluded FROM media WHERE id IN ({','.join('?' * len(ch))})", ch)]
            c.executemany("UPDATE media SET excluded=1 WHERE id=?", [(i,) for i in ids])
            label = "排除"
        else:
            prev, rows = _skip(c, ids, True)
            if body.action == "skip_tag":
                from .common import _apply
                trows, _, _ = _apply("add", tag, ids, "custom")
                rows += trows
                skip_names = {n.replace(" ", "_") for n in db.get_settings().get("quick_tags_skip", [])}
                if tag in skip_names:   # 跟快速標籤一樣：因為標籤才略過（拿掉標籤會回到未識別）
                    c.executemany("UPDATE media SET unrec_skip=2 WHERE id=?", [(i,) for i in ids])
                from .. import quicktags
                if tag in quicktags.no_char_names():
                    rows += quicktags.purge_candidates(c, ids)
            payload = {"skip": prev, "tag_rows": rows}
            label = f"略過＋標「{tag}」" if body.action == "skip_tag" else "略過"
        from ..piles import PILES
        pname = dict(PILES).get(body.bucket[2:], {"orig": "原創", "aigen": "AI 生成"}.get(body.bucket, body.bucket))
        db.push_action(f"{pname}：{label}（{len(ids)} 張）", payload)
        c.commit()
    return {"ok": True, "n": len(ids)}


@router.get("/api/unrec/by-series")
def unrec_by_series(miss: str = "character", nsfw: str = "hide", limit: int = 12):
    """缺角色／缺創作者的圖，依作品分：「蔚藍檔案 12 張、原神 5 張…」（標了作品卻沒有角色、作者的圖以前很難找）。
    作品＝圖上的作品標籤，或已確認角色所屬的作品；子作品併進母系列（跟書架一樣）。"""
    if miss not in ("character", "artist"):
        raise HTTPException(400)
    c = db.conn()
    _, w, p = _where("unrecognized", nsfw, "", None, miss, None, None, None)
    per: dict[str, set] = {}
    for key, mid in c.execute(f"""SELECT COALESCE(CASE WHEN t.category='copyright' THEN t.name END, ti.copyright), x.media_id
            FROM media_tags x JOIN tags t ON t.id=x.tag_id LEFT JOIN tag_info ti ON ti.tag_id=t.id
            WHERE x.state=1 AND t.category IN ('copyright','character') AND x.media_id IN (SELECT m.id FROM media m WHERE {w})""", p):
        if key:
            per.setdefault(key, set()).add(mid)
    universe = set(query._copyrights()) | set(per)
    merged: dict[str, set] = {}
    for k, v in per.items():
        par = names.series_parent(k, universe) or k
        merged.setdefault(par, set()).update(v)
    top = sorted(merged.items(), key=lambda kv: -len(kv[1]))[:max(1, min(limit, 50))]
    lang = db.get_settings()["display_lang"]
    from .. import wikiname
    auto = wikiname.cached([k for k, _ in top])
    out = []
    for k, v in top:
        r = c.execute("SELECT ti.other_names, ti.display_name FROM tags t LEFT JOIN tag_info ti ON ti.tag_id=t.id WHERE t.name=?", (k,)).fetchone()
        disp = names.series_display(k, json.loads(r[0] or "[]") if r and r[0] else [], r[1] if r else None, lang, auto.get(k))
        out.append({"key": k, "display": disp, "n": len(v)})
    return out


@router.get("/api/unrec/summary")
def unrec_summary(miss: str = "all", nsfw: str = "hide"):
    """（b96）資料沒變就沿用上次算的：見 db.read_cache。"""
    return db.read_cache("unrec_summary", (miss, nsfw,), lambda: _unrec_summary(miss, nsfw), extra=None)


def _unrec_summary(miss: str = "all", nsfw: str = "hide"):
    """未識別頁的分流數字。"""
    from ..query import BUCKETS
    c = db.conn()
    out = {}
    for b in (None, *BUCKETS, "skipped"):
        _, w, p = _where("unrecognized", nsfw, "", None, miss, None, None, b)
        out[b or "all"] = c.execute(f"SELECT COUNT(*) FROM media m WHERE {w}", p).fetchone()[0]
    from ..piles import PILES, TIPS
    out["piles"] = [{"key": f"p_{k}", "label": l, "tip": TIPS.get(k, "")} for k, l in PILES]
    return out


class OfficialReq(BaseModel):
    ids: list[int]
    kind: str | None = None        # anime / manga / novel / movie / None＝不是官方


@router.post("/api/media/official")
def set_official(body: OfficialReq):
    """標成動畫截圖／漫畫截圖／小說插圖（或取消）。可以撤銷。"""
    from .. import official
    try:
        n = official.set_kind(body.ids, body.kind or None, "manual")
    except ValueError:
        raise HTTPException(400, "種類不對")
    return {"ok": True, "n": n}


class MediaTypeReq(BaseModel):
    ids: list[int]
    label: str | None = None      # illustration／chibi／comic／3d／cosplay／photo／figure／other；None＝改回自動


@router.post("/api/media/mediatype")
def set_media_type(body: MediaTypeReq):
    """你標的媒材（b77）：馬上生效，之後 AI 用這些重新學、整庫重算。可以撤銷。"""
    from .. import mediaclf
    try:
        n = mediaclf.set_label(body.ids, body.label or None)
    except ValueError:
        raise HTTPException(400, "媒材不對")
    return {"ok": True, "n": n}


@router.post("/api/media/exclude")
def exclude(body: ExcludeReq):
    """排除：迷因、截圖、不想整理的圖。不會出現在未識別、待確認和圖庫裡。"""
    with db.write_lock:
        c = db.conn()
        prev = [[r[0], r[1] or 0] for r in c.execute(
            f"SELECT id, excluded FROM media WHERE id IN ({','.join('?' * len(body.ids))})", body.ids)]
        c.executemany("UPDATE media SET excluded=? WHERE id=?", [(int(body.value), i) for i in body.ids])
        db.push_action(f"{'排除' if body.value else '取消排除'}（{len(body.ids)} 張）", {"excluded": prev})
        c.commit()
    return {"ok": True}


# ---------- 貼出處網址（b94，app/srcurl.py）：先比對給你看，你勾好再寫入 ----------
class SrcUrlCheck(BaseModel):
    ids: list[int]
    url: str


class SrcUrlApply(BaseModel):
    token: str
    picks: dict[int, list[str]]


@router.post("/api/media/source-url/check")
def source_url_check(body: SrcUrlCheck):
    from .. import srcurl
    try:
        return srcurl.check(body.ids, body.url)
    except srcurl.SrcUrlError as e:
        raise HTTPException(400, str(e))
    except Exception as e:  # noqa  連線失敗等：看得懂的訊息
        from ..httpclient import TransientError
        if isinstance(e, TransientError):
            raise HTTPException(503, f"網站暫時連不上：{e}")
        raise


@router.post("/api/media/source-url/apply")
def source_url_apply(body: SrcUrlApply):
    from .. import srcurl
    try:
        return srcurl.apply(body.token, body.picks)
    except srcurl.SrcUrlError as e:
        raise HTTPException(400, str(e))
