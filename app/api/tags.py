"""角色／標籤：清單、搜尋、單一標籤頁、改名／合併／刪除、加標籤。"""
from __future__ import annotations

import json
import re

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .. import db, names, online, query
from ..query import NSFW, OK
from ..matcher import matcher
from .common import LABELS, TagOp, _apply, _schedule_rematch

router = APIRouter()

def _series_counts(keys: list[str], nf: str) -> dict[str, int]:
    """作品卡片的張數，跟作品頁、搜尋 series: 一樣：直接標了作品的圖＋角色屬於這部作品（含子作品）的圖，不重複。
    以前只數作品標籤本身：「蔚藍檔案 198 張」，點進去 467 張。一次掃完，不是每部作品各查一次。"""
    c = db.conn()
    per: dict[str, set] = {}
    for name, mid in c.execute(f"""SELECT COALESCE(CASE WHEN t.category='copyright' THEN t.name END, ti.copyright), x.media_id
            FROM media_tags x JOIN tags t ON t.id=x.tag_id LEFT JOIN tag_info ti ON ti.tag_id=t.id
            JOIN media m ON m.id=x.media_id AND m.status='done' AND COALESCE(m.excluded,0)=0{nf}
            WHERE x.state=1 AND t.category IN ('copyright','character')"""):
        if name:
            per.setdefault(name, set()).add(mid)
    allk = set(per) | set(keys)
    out = {}
    for v in keys:
        got = set(per.get(v, ()))
        for k in allk:
            if k != v and k in per and names.series_parent(k, {v}) == v:
                got |= per[k]
        out[v] = len(got)
    return out



class CoversReq(BaseModel):
    names: list[str]
    n: int = 3
    nsfw: str = "hide"
    noalbum: bool = False


@router.post("/api/covers")
def covers(req: CoversReq):
    """卡片的拼貼封面：每個標籤最近的 n 張已確認的圖（一次一批，卡片畫到哪查到哪）。"""
    c = db.conn()
    nf = (f" AND {NSFW}=0" if req.nsfw == "hide" else "") + (" AND m.id NOT IN (SELECT media_id FROM album_items)" if req.noalbum else "")
    n = max(1, min(int(req.n), 6))
    out = {}
    for name in req.names[:300]:
        tid = db.tag_id(name, create=False)
        if not tid:
            continue
        out[name] = [r[0] for r in c.execute(f"""SELECT m.id FROM media_tags x JOIN media m ON m.id=x.media_id
            WHERE x.tag_id=? AND x.state=1 AND m.status='done' AND m.kind='image'{nf}
            ORDER BY COALESCE(m.mtime, m.added_at) DESC LIMIT ?""", (tid, n))]
    return out


@router.get("/api/characters")
def characters(nsfw: str = "hide", q: str = "", sort: str = "count", category: str = "character", noalbum: bool = False):
    c = db.conn()
    nf = (f" AND {NSFW}=0" if nsfw == "hide" else "") + (" AND m.id NOT IN (SELECT media_id FROM album_items)" if noalbum else "")
    lq = query._like(q.strip().replace(" ", "_"))
    hit_sql = ""
    if q.strip():   # 中文名（繁／簡）、顯示名稱也找得到
        from .. import nameindex
        hits = [h[0] for h in nameindex.search(q.strip(), category, 200)]
        if hits:
            hit_sql = f" OR t.id IN ({','.join(map(str, hits[:800]))})"
    order = {"count": "n DESC, unchecked DESC", "name": "t.name", "unchecked": "unchecked DESC, n DESC",
             "series": "ti.copyright IS NULL, ti.copyright, n DESC"}.get(sort, "n DESC")
    rows = c.execute(f"""SELECT t.name, t.pinned, t.cover AS custom_cover, SUM(mt.state=1) AS n, SUM(mt.state=0) AS unchecked,
          MIN(CASE WHEN mt.state=0 THEN mt.tier END) AS best_tier,
          ti.copyright, ti.other_names, ti.display_name, ti.status AS info_status, ti.links,
          (SELECT x.media_id FROM media_tags x JOIN media m2 ON m2.id=x.media_id
           WHERE x.tag_id=t.id AND x.state>=0 AND m2.status='done' {nf.replace('m.', 'm2.')}
           ORDER BY x.state DESC, COALESCE(x.tier, 0), x.score DESC LIMIT 1) AS cover
        FROM tags t CROSS JOIN media_tags mt ON mt.tag_id=t.id AND mt.state>=0
        CROSS JOIN media m ON m.id=mt.media_id AND m.status='done'{nf}
        LEFT JOIN tag_info ti ON ti.tag_id=t.id
        WHERE t.category=? AND (t.name LIKE ? ESCAPE '\\' OR ti.other_names LIKE ? ESCAPE '\\' OR ti.copyright LIKE ? ESCAPE '\\'{hit_sql})
        GROUP BY t.id ORDER BY {order}""", (category, f"%{lq}%", f"%{query._like(q)}%", f"%{lq}%")).fetchall()
    out = []
    lang = db.get_settings()["display_lang"]
    auto = {}
    if category == "copyright":
        from .. import wikiname
        auto = wikiname.cached([r["name"] for r in rows])
    guess = {}
    if category == "character":
        from .. import seriesguess
        guess = seriesguess.guess_all()
    series_n = _series_counts([r["name"] for r in rows], nf) if category == "copyright" else {}
    for r in rows:
        d = dict(r)
        if not d["copyright"] and d["name"] in guess:
            d["copyright"], d["copyright_guess"] = guess[d["name"]], True
        cc = d.pop("custom_cover", None)
        if cc and (nsfw != "hide" or c.execute(f"SELECT 1 FROM media m WHERE m.id=? AND {NSFW}=0", (cc,)).fetchone()):
            d["cover"] = cc          # 自選封面優先（隱藏 NSFW 時，NSFW 的自選封面先不用）
        d["other_names"] = json.loads(d["other_names"] or "[]")
        d["links"] = json.loads(d["links"] or "[]")
        if d["name"] in series_n:
            d["n"] = series_n[d["name"]]
        # 卡片上顯示中文名（跟書架一致），原始 tag 放在滑鼠提示；資料已經在這一列，不用每個再查一次
        dn = d.pop("display_name", None)
        d["display"] = (names.series_display(d["name"], d["other_names"], dn, lang, auto.get(d["name"])) if category == "copyright"
                        else names.display(d["name"], d["other_names"], dn, lang))
        out.append(d)
    if series_n and sort == "count":
        out.sort(key=lambda d: -(d["n"] or 0))
    return out



@router.get("/api/tags")
def tags(q: str = "", category: str | None = None, limit: int = 20):
    c = db.conn()
    limit = max(1, min(limit, 200))
    qs = q.strip()
    lq = query._like(qs.replace(" ", "_"))
    w, p = ["(t.name LIKE ? ESCAPE '\\' OR ti.other_names LIKE ? ESCAPE '\\')"], [f"%{lq}%", f"%{query._like(qs)}%"]
    # 顯示名稱、簡繁體、空白寫法也找得到（nameindex）：「芙莉蓮」→ frieren（別名只有簡體「芙莉莲」）
    from .. import nameindex
    extra = [h[0] for h in nameindex.search(qs, category, limit)] if qs else []
    if extra:
        w = [f"({w[0]} OR t.id IN ({','.join(map(str, extra[:400]))}))"]
    if category:
        w.append("t.category=?")
        p.append(category)
    rank = {tid: i for i, tid in enumerate(extra)}
    rows = c.execute(f"""SELECT t.id, t.name, t.category, t.pinned, ti.copyright, ti.other_names, ti.display_name,
        (SELECT COUNT(*) FROM media_tags x WHERE x.tag_id=t.id AND {OK}) AS n
        FROM tags t LEFT JOIN tag_info ti ON ti.tag_id=t.id WHERE {' AND '.join(w)}
        ORDER BY (t.name=?) DESC, (t.name LIKE ? ESCAPE '\\') DESC, (t.category IN ('character','artist','copyright')) DESC, n DESC LIMIT ?""",
                     [*p, qs.replace(" ", "_"), f"{lq}%", limit * 3]).fetchall()
    out = []
    lang = db.get_settings()["display_lang"]
    for r in rows:
        d = dict(r)
        tid = d.pop("id")
        on = json.loads(d.pop("other_names") or "[]")
        dn = d.pop("display_name", None)
        hit = [n for n in on if qs and qs in n]
        d["alias"] = hit[0] if hit and qs not in d["name"] else None
        d["zh"] = query.zh_of(d["name"]) if d["category"] == "general" else None
        if d["category"] in ("character", "artist", "copyright"):
            d["display"] = names.display(d["name"], on, dn, lang) if d["category"] != "copyright" else \
                names.series_display(d["name"], on, dn, lang)
        if d["category"] == "copyright" and len(out) < limit:
            # 作品的張數跟作品頁一樣（角色屬於這部作品的圖也算）：以前選單寫 198、點進去 467
            sq, sp = query._series_sql([d["name"]])
            d["n"] = c.execute(f"SELECT COUNT(*) FROM media m WHERE m.status='done' AND COALESCE(m.excluded,0)=0 AND {sq}", sp).fetchone()[0]
        # 排序：名字完全相同 > 開頭相同 > 身分標籤 > 張數（原本 SQL 的順序），名字索引「完全相同」的排最前面
        d["_r"] = (0 if rank.get(tid, 9) == 0 or d["name"] == qs.replace(" ", "_") else 1)
        out.append(d)
    out.sort(key=lambda d: d.pop("_r"))
    out = out[:limit]
    # 中文找一般 tag（例如「雙馬尾」→ twintails）
    if qs and not qs.isascii() and len(out) < limit:
        have = {d["name"] for d in out}
        for zh, ens in query.zh_reverse().items():
            if qs in zh:
                for en in ens:
                    if en in have:
                        continue
                    r = c.execute(f"""SELECT t.name, t.category, t.pinned, (SELECT COUNT(*) FROM media_tags x WHERE x.tag_id=t.id
                                      AND x.state>=0) AS n FROM tags t WHERE t.name=?""", (en,)).fetchone()
                    if r and (not category or r["category"] == category):
                        out.append({**dict(r), "copyright": None, "alias": None, "zh": zh})
                        have.add(en)
        out.sort(key=lambda d: -(d["n"] or 0))
    return out[:limit]



class SuggestReq(BaseModel):
    ids: list[int] = []
    category: str | None = None
    context: str | None = None      # 目前所在的標籤頁（例如角色頁：同作品的其他角色優先）



@router.post("/api/tags/suggest")
def tags_suggest(body: SuggestReq):
    """加標籤的建議（還沒打字時）：這幾張圖的候選、同作品的角色、這幾張已經有的。不列 1girl 這種一般描述。"""
    c = db.conn()
    lang = db.get_settings()["display_lang"]
    cat = body.category if body.category in ("character", "artist", "copyright", "custom", "general") else None
    ids = body.ids[:500]
    ph = ",".join("?" * len(ids)) if ids else "NULL"
    catw = "AND t.category=?" if cat else "AND t.category IN ('character','artist','copyright','custom')"
    cp = [cat] if cat else []

    def pack(rows, **extra):
        out = []
        for r in rows:
            d = dict(r)
            on = json.loads(d.pop("other_names", None) or "[]")
            dn = d.pop("display_name", None)
            d["display"] = names.display(d["name"], on, dn, lang)
            out.append({**d, **extra})
        return out
    cand = pack(c.execute(f"""SELECT t.name, t.category, ti.copyright, ti.other_names, ti.display_name,
            COUNT(*) AS on_n, MAX(mt.score) AS score, MIN(mt.tier) AS tier, GROUP_CONCAT(DISTINCT mt.source) AS sources,
            (SELECT COUNT(*) FROM media_tags x WHERE x.tag_id=t.id AND x.state=1) AS n
        FROM media_tags mt JOIN tags t ON t.id=mt.tag_id LEFT JOIN tag_info ti ON ti.tag_id=t.id
        WHERE mt.media_id IN ({ph}) AND mt.state=0 {catw} GROUP BY t.id ORDER BY MIN(COALESCE(mt.tier,9)), on_n DESC, score DESC LIMIT 12""",
                          [*ids, *cp]).fetchall()) if ids else []
    have = pack(c.execute(f"""SELECT t.name, t.category, ti.copyright, ti.other_names, ti.display_name, COUNT(*) AS on_n,
            (SELECT COUNT(*) FROM media_tags x WHERE x.tag_id=t.id AND x.state=1) AS n
        FROM media_tags mt JOIN tags t ON t.id=mt.tag_id LEFT JOIN tag_info ti ON ti.tag_id=t.id
        WHERE mt.media_id IN ({ph}) AND mt.state=1 {catw} GROUP BY t.id ORDER BY on_n DESC LIMIT 20""", [*ids, *cp]).fetchall()) if ids else []
    # 同作品的角色：目前角色頁的作品、或這幾張圖已確認的作品
    series = set()
    if body.context:
        r = c.execute("SELECT t.category, ti.copyright FROM tags t LEFT JOIN tag_info ti ON ti.tag_id=t.id WHERE t.name=?", (body.context,)).fetchone()
        if r and r["category"] == "copyright":
            series.add(body.context)
        elif r and r["copyright"]:
            series.add(r["copyright"])
    series |= {h["name"] for h in have if h["category"] == "copyright"}
    series |= {h["copyright"] for h in have if h["category"] == "character" and h.get("copyright")}
    mates = []
    if series and cat in (None, "character"):
        sp = ",".join("?" * len(series))
        skip = {x["name"] for x in cand + have}
        mates = [m for m in pack(c.execute(f"""SELECT t.name, t.category, ti.copyright, ti.other_names, ti.display_name,
                (SELECT COUNT(*) FROM media_tags x WHERE x.tag_id=t.id AND x.state=1) AS n
            FROM tags t JOIN tag_info ti ON ti.tag_id=t.id WHERE t.category='character' AND ti.copyright IN ({sp})
            ORDER BY n DESC LIMIT 40""", list(series)).fetchall()) if m["name"] not in skip and m["n"]][:16]
    pinned = pack(c.execute(f"""SELECT t.name, t.category, ti.copyright, ti.other_names, ti.display_name,
            (SELECT COUNT(*) FROM media_tags x WHERE x.tag_id=t.id AND x.state=1) AS n
        FROM tags t LEFT JOIN tag_info ti ON ti.tag_id=t.id WHERE t.pinned=1 {catw} ORDER BY n DESC LIMIT 10""", cp).fetchall())
    return {"candidates": cand, "have": have, "series": sorted(series), "mates": mates, "pinned": pinned, "total": len(ids)}



@router.get("/api/tag/{name:path}")
def tag_info(name: str, nsfw: str = "hide", noalbum: bool = False):
    c = db.conn()
    r = c.execute("SELECT * FROM tags WHERE name=?", (name,)).fetchone()
    if not r:
        raise HTTPException(404)
    nf = (f" AND {NSFW}=0" if nsfw == "hide" else "") + (" AND m.id NOT IN (SELECT media_id FROM album_items)" if noalbum else "")
    st = c.execute(f"""SELECT SUM(mt.state>=0) n, SUM(mt.state=0) unchecked, SUM(mt.state=1) confirmed,
        SUM(mt.source='match' AND mt.state=0) matched, SUM(mt.source='model' AND mt.state=0) model,
        SUM(mt.source IN ('online','danbooru','pixiv') AND mt.state=0) online,
        SUM(mt.source='manual' AND mt.state=1) manual, SUM(mt.state=-1) rejected,
        SUM(mt.state=0 AND mt.tier<=2) cand_source, SUM(mt.state=0 AND mt.tier=3) cand_pixiv,
        SUM(mt.state=0 AND mt.tier=4) cand_ai, SUM(mt.state=0 AND mt.tier>=5) cand_set
        FROM media_tags mt JOIN media m ON m.id=mt.media_id
        WHERE mt.tag_id=? AND m.status='done'{nf}""", (r["id"],)).fetchone()
    with db.write_lock:
        c.execute("UPDATE tags SET last_viewed=strftime('%s','now') WHERE id=?", (r["id"],))
        c.commit()
    info = c.execute("SELECT * FROM tag_info WHERE tag_id=?", (r["id"],)).fetchone()
    s = db.get_settings()
    if info is None and r["category"] in ("character", "artist") and s["online_enabled"] and s["online_auto_charinfo"]:
        online.get_jobs().queue_tag_info([name])
    info_d = None
    if info:
        info_d = dict(info)
        info_d["other_names"] = json.loads(info_d["other_names"] or "[]")
        info_d["copyrights"] = json.loads(info_d["copyrights"] or "[]")
        info_d["links"] = json.loads(info_d.get("links") or "[]")
    lang = s["display_lang"]
    if r["category"] == "copyright":
        from .. import wikiname
        disp = names.series_display(name, info_d["other_names"] if info_d else [], info_d["display_name"] if info_d else None,
                                    lang, wikiname.cached([name]).get(name))
    else:
        disp = names.display(name, info_d["other_names"] if info_d else [], info_d["display_name"] if info_d else None, lang)
    existing = {x[0] for x in c.execute("SELECT name FROM tags WHERE category=?", (r["category"],))}
    base = names.base_of(name, existing)
    skins = [dict(x) for x in c.execute("""SELECT t.name, COUNT(*) n FROM tags t JOIN media_tags mt ON mt.tag_id=t.id AND mt.state=1
        WHERE t.category=? AND t.name LIKE ? AND t.name!=? GROUP BY t.id""", (r["category"], _skin_like(name), name))
        if names.base_of(x["name"], existing) == name]
    # 頁首用的封面：自選封面 > 最近收的已確認圖（非顯示模式時優先挑安全的）
    cover = r["cover"]
    if not cover:
        order = f"{NSFW} ASC, " if nsfw != "show" else ""
        x = c.execute(f"""SELECT m.id FROM media_tags mt JOIN media m ON m.id=mt.media_id WHERE mt.tag_id=? AND mt.state=1
                          AND m.status='done'{nf} ORDER BY {order}COALESCE(m.mtime, m.added_at) DESC LIMIT 1""", (r["id"],)).fetchone()
        cover = x[0] if x else None
    # 所屬作品 → 作品頁連結（子作品連到母系列，跟書架的分組一致）
    series_links = []
    if info_d and info_d.get("copyrights"):
        from .. import wikiname
        keys = {x[0] for x in c.execute("""SELECT copyright FROM tag_info WHERE copyright IS NOT NULL
                                           UNION SELECT name FROM tags WHERE category='copyright'""")}
        for cp in info_d["copyrights"]:
            k = names.series_parent(cp, keys) or cp
            if any(l["key"] == k for l in series_links):
                continue
            si = c.execute("SELECT ti.other_names, ti.display_name FROM tags t JOIN tag_info ti ON ti.tag_id=t.id WHERE t.name=?",
                           (k,)).fetchone()
            series_links.append({"key": k, "display": names.series_display(
                k, json.loads(si["other_names"] or "[]") if si else [], si["display_name"] if si else None,
                s["display_lang"], wikiname.cached([k]).get(k))})
    from .. import implies
    return {**dict(r), **dict(st), "display": disp, "base": base, "skins": skins, "cover_id": cover, "implies": implies.rules_for(name),
            "series_links": series_links, "info": info_d, "info_pending": name in online.get_jobs()._queued_tags}



def _skin_like(name: str) -> str:
    """找造型用的 LIKE 條件：mika_(blue_archive) → mika_(%)_(blue_archive)；hatsune_miku → hatsune_miku_(%)"""
    m = names.SIMPLE_VARIANT.match(name)
    return f"{m.group(1)}_(%)_({m.group(2)})" if m else f"{name}_(%)"



class InfoPatch(BaseModel):
    refetch: bool = False
    copyright: str | None = None
    other_names: list[str] | None = None
    summary: str | None = None
    links: list[str] | None = None     # 手動編輯的連結（一行一個）；Danbooru wiki 網址放 url，其他放 links



@router.post("/api/tag/{name:path}/info")
def tag_info_patch(name: str, body: InfoPatch):
    tid = db.tag_id(name, create=False)
    if tid is None:
        raise HTTPException(404)
    c = db.conn()
    if body.refetch:
        with db.write_lock:
            c.execute("UPDATE tag_info SET manual=0 WHERE tag_id=?", (tid,))
            c.commit()
        online.get_jobs().queue_tag_info([name])
        return {"ok": True, "queued": True}
    cp = (body.copyright or "").strip().replace(" ", "_") or None
    url = links = None
    if body.links is not None:
        good = []
        for u in body.links:
            u = u.strip()
            if re.match(r"^https?://\S+$", u, re.I) and u not in good:
                good.append(u)
        wiki = [u for u in good if re.match(r"^https?://(danbooru|safebooru)\.donmai\.us/wiki_pages/", u, re.I)]
        url = wiki[0] if wiki else ""
        links = json.dumps([u for u in good if u != url], ensure_ascii=False)
    with db.write_lock:
        c.execute("""INSERT INTO tag_info(tag_id, status, copyright, copyrights, other_names, summary, manual, fetched_at)
            VALUES(?, 'ok', ?, ?, ?, ?, 1, strftime('%s','now'))
            ON CONFLICT(tag_id) DO UPDATE SET copyright=excluded.copyright, copyrights=excluded.copyrights,
              other_names=excluded.other_names, summary=excluded.summary, manual=1, status='ok'""",
                  (tid, cp, json.dumps([cp] if cp else []), json.dumps(body.other_names or [], ensure_ascii=False),
                   body.summary or ""))
        if links is not None:
            c.execute("UPDATE tag_info SET links=?, url=? WHERE tag_id=?", (links, url or None, tid))
        c.commit()
    return {"ok": True}



@router.post("/api/tags/apply")
def tags_apply(op: TagOp):
    name = op.tag.strip().replace(" ", "_")
    if not name or not op.ids:
        raise HTTPException(400)
    with db.write_lock:
        rows, propagated, set_added = _apply(op.action, name, op.ids, op.category, op.from_tag)
        db.push_action(f"{LABELS.get(op.action, op.action)} {name}（{len(op.ids)} 張）"
                       + (f"＋相似 {propagated} 張" if propagated else ""), {"tag_rows": rows}, touched=op.ids)
        db.conn().commit()
    _schedule_rematch()
    crowd = 0
    if op.action in ("add", "confirm", "replace") and len(op.ids) <= 2000:
        cat = db.conn().execute("SELECT category FROM tags WHERE name=?", (name,)).fetchone()
        if cat and cat[0] == "character":
            from .. import people
            people.refresh_bases()          # 可能是新的造型 tag
            ph = ",".join("?" * len(op.ids))
            crowd = db.conn().execute(f"SELECT COUNT(*) FROM media m WHERE m.id IN ({ph}) AND {people.SHORT_SQL}", op.ids).fetchone()[0]
    return {"ok": True, "propagated": propagated, "set_added": set_added, "crowd_short": crowd}



class DeleteReq(BaseModel):
    block: bool = True      # 以後也不要自動標上（留一個空殼記住「停止自動標註」）



@router.post("/api/tag/{name:path}/delete")
def tag_delete(name: str, body: DeleteReq):
    """刪除標籤：拿掉所有圖上的這個標籤、資料卡、別名。可撤銷。
    block=True：只留下一個 0 張圖的空殼記住「停止自動標註」，不然 AI／線上來源下次又會把它標回來。"""
    c = db.conn()
    src = c.execute("SELECT * FROM tags WHERE name=?", (name,)).fetchone()
    if not src:
        raise HTTPException(404)
    tid = src["id"]
    with db.write_lock:
        rows = [[r["media_id"], tid, [r["score"], r["source"], r["state"], r["tier"], r["reason"]]]
                for r in c.execute("SELECT * FROM media_tags WHERE tag_id=?", (tid,))]
        info = c.execute("SELECT * FROM tag_info WHERE tag_id=?", (tid,)).fetchone()
        undo = {"tag": dict(src), "info": dict(info) if info else None,
                "aliases": [r[0] for r in c.execute("SELECT name FROM tag_alias WHERE tag_id=?", (tid,))],
                "rows": rows, "kept": body.block}
        if body.block:
            c.execute("DELETE FROM media_tags WHERE tag_id=?", (tid,))
            c.execute("DELETE FROM tag_info WHERE tag_id=?", (tid,))
            c.execute("DELETE FROM tag_alias WHERE tag_id=?", (tid,))
            c.execute("UPDATE tags SET auto_block=1, pinned=0, cover=NULL WHERE id=?", (tid,))
            matcher.blocked.add(name)
        else:
            c.execute("DELETE FROM tags WHERE id=?", (tid,))      # 其他資料表 ON DELETE CASCADE / SET NULL
            matcher.blocked.discard(name)
        db.push_action(f"刪除標籤 {name}（{len(rows)} 筆）", {"delete_tag": undo}, touched=[])   # 動的是標籤，不算動了每一張圖
        c.commit()
        db._tag_cache.clear()
    return {"ok": True, "removed": len(rows), "confirmed": sum(1 for r in rows if r[2][2] == 1)}



class MergeReq(BaseModel):
    into: str



@router.post("/api/tag/{name:path}/merge")
def tag_merge(name: str, body: MergeReq):
    """合併標籤：name 的所有紀錄移到 into，之後任何來源再產生 name 都會自動導向 into。"""
    return merge_tags(name, body.into.strip().replace(" ", "_"))


# ---------- 標籤蘊含（app/implies.py，b84）----------
class ImpliesReq(BaseModel):
    implies: list[str] = []


@router.post("/api/tag/{name:path}/implies")
def tag_implies(name: str, body: ImpliesReq):
    """這個標籤也代表哪些（整份清單）：新加的＝你訂的規則，原本有、被你拿掉的＝之後不套用。"""
    from .. import implies
    try:
        r = implies.set_rules(name, [str(x) for x in body.implies][:50])
    except ValueError as e:
        raise HTTPException(400, str(e))
    db.log_op(f"標籤蘊含：{name} ⇒ {'、'.join(x['name'] for x in r['implies']) or '（無）'}", keys="implies")
    return r


class ImpliesOff(BaseModel):
    other: str
    off: bool = True


@router.post("/api/tag/{name:path}/implies-off")
def tag_implies_off(name: str, body: ImpliesOff):
    """「不是同一個人」：Danbooru 的蘊含、程式從名字猜的造型都可以關掉（off=False 收回）。"""
    from .. import implies
    try:
        implies.set_off(name, body.other.strip().replace(" ", "_"), body.off)
    except ValueError as e:
        raise HTTPException(400, str(e))
    db.log_op(f"標籤蘊含：{name} {'不' if body.off else '又'}代表 {body.other}", keys="implies")
    return implies.rules_for(name)



def merge_tags(name: str, into: str, queue_info: bool = True) -> dict:
    """合併標籤的實際工作（手動合併、以及 Danbooru 改名時自動合併都用這個）。"""
    c = db.conn()
    src = c.execute("SELECT * FROM tags WHERE name=?", (name,)).fetchone()
    if not src or into == name:
        raise HTTPException(400)
    if db.tag_id(into, create=False) == src["id"]:
        raise HTTPException(400, f"「{into}」已經是 {name} 的別名，不能合併到自己")
    with db.write_lock:
        dst_new = db.tag_id(into, create=False) is None
        tid = db.tag_id(into, src["category"])
        # 撤銷用：來源 tag 本身、兩邊的紀錄、資料卡、指向來源的 alias
        mids = [r[0] for r in c.execute("SELECT media_id FROM media_tags WHERE tag_id=?", (src["id"],))]
        info_row = c.execute("SELECT * FROM tag_info WHERE tag_id=?", (src["id"],)).fetchone()
        dst_info = c.execute("SELECT * FROM tag_info WHERE tag_id=?", (tid,)).fetchone()
        undo = {"src_id": src["id"], "src_name": name, "src_cat": src["category"], "pinned": src["pinned"],
                "auto_block": src["auto_block"], "cover": src["cover"],
                "aliases": [r[0] for r in c.execute("SELECT name FROM tag_alias WHERE tag_id=?", (src["id"],))],
                "src_rows": db.snapshot_tag_rows(mids, src["id"]), "dst_rows": db.snapshot_tag_rows(mids, tid),
                "info": dict(info_row) if info_row else None, "dst_new": tid if dst_new else None}
        moved = 0
        for r in c.execute("SELECT * FROM media_tags WHERE tag_id=?", (src["id"],)).fetchall():
            t = c.execute("SELECT state FROM media_tags WHERE media_id=? AND tag_id=?", (r["media_id"], tid)).fetchone()
            if t is None:
                c.execute("UPDATE media_tags SET tag_id=? WHERE media_id=? AND tag_id=?", (tid, r["media_id"], src["id"]))
                moved += 1
            elif r["state"] == 1 and t["state"] != 1:
                c.execute("UPDATE media_tags SET state=1 WHERE media_id=? AND tag_id=?", (r["media_id"], tid))
        # 舊名稱記成別名（搜尋也找得到）
        info = c.execute("SELECT other_names, links FROM tag_info WHERE tag_id=?", (tid,)).fetchone()
        al = json.loads(info["other_names"] or "[]") if info else []
        for n in [name] + (json.loads(info_row["other_names"] or "[]") if info_row else []):
            if n not in al:
                al.append(n)
        # 來源的連結也帶過來（同一個作者的兩個身分：兩邊的 X／Pixiv 帳號都要認得，之後從任一個帳號來的圖都對到這裡）
        links = list(dict.fromkeys((json.loads(info["links"] or "[]") if info else []) +
                                   (json.loads(info_row["links"] or "[]") if info_row and info_row["links"] else [])))
        if info:
            c.execute("UPDATE tag_info SET other_names=?, links=? WHERE tag_id=?",
                      (json.dumps(al, ensure_ascii=False), json.dumps(links, ensure_ascii=False), tid))
        else:
            c.execute("INSERT INTO tag_info(tag_id, status, other_names, links) VALUES(?, 'ok', ?, ?)",
                      (tid, json.dumps(al, ensure_ascii=False), json.dumps(links, ensure_ascii=False)))
        c.execute("UPDATE tag_alias SET tag_id=? WHERE tag_id=?", (tid, src["id"]))
        c.execute("DELETE FROM tags WHERE id=?", (src["id"],))
        c.execute("INSERT OR REPLACE INTO tag_alias(name, tag_id) VALUES(?, ?)", (name, tid))
        db.push_action(f"合併 {name} → {into}", {"merge": undo, "dst_info": [tid, dict(dst_info) if dst_info else None]}, touched=[])
        c.commit()
        db._tag_cache.clear()
    from .. import people
    people.refresh_bases()
    s = db.get_settings()
    if queue_info and s["online_enabled"] and s["online_auto_charinfo"]:
        online.get_jobs().queue_tag_info([into])
    return {"ok": True, "moved": moved, "into": into}


# 最一般的 /api/tag/{name} 放最後：{name:path} 會吃掉斜線，/merge、/delete、/info 這些要先比對到
class TagPatch(BaseModel):
    auto_block: bool | None = None
    cover: int | None = None
    display_name: str | None = None
    dismiss_suggest: bool | None = None
    pinned: bool | None = None
    category: str | None = None
    rename: str | None = None



@router.post("/api/tag/{name:path}")
def tag_patch(name: str, body: TagPatch):
    c = db.conn()
    tid = db.tag_id(name, create=False)
    if tid is None:
        raise HTTPException(404)
    with db.write_lock:
        if body.pinned is not None:
            c.execute("UPDATE tags SET pinned=? WHERE id=?", (int(body.pinned), tid))
        if body.auto_block is not None:
            c.execute("UPDATE tags SET auto_block=? WHERE id=?", (int(body.auto_block), tid))
            if body.auto_block:  # 移除現有所有「未檢查」的自動標註（可撤銷）
                ids = [r[0] for r in c.execute(
                    "SELECT media_id FROM media_tags WHERE tag_id=? AND state=0 AND source!='manual'", (tid,))]
                db.push_action(f"停止自動標註 {name}（移除 {len(ids)} 筆）",
                               {"tag_rows": db.snapshot_tag_rows(ids, tid), "auto_block": [tid, 0]}, touched=[])
                if ids:
                    c.execute("DELETE FROM media_tags WHERE tag_id=? AND state=0 AND source!='manual'", (tid,))
                matcher.blocked.add(name)
            else:
                matcher.blocked.discard(name)
        if body.cover is not None:
            c.execute("UPDATE tags SET cover=? WHERE id=?", (body.cover or None, tid))
        if body.display_name is not None:
            dn = body.display_name.strip() or None
            if c.execute("SELECT 1 FROM tag_info WHERE tag_id=?", (tid,)).fetchone():
                c.execute("UPDATE tag_info SET display_name=? WHERE tag_id=?", (dn, tid))
            else:
                c.execute("INSERT INTO tag_info(tag_id, status, display_name) VALUES(?, 'ok', ?)", (tid, dn))
        if body.dismiss_suggest:
            c.execute("UPDATE tag_info SET match_suggest=NULL WHERE tag_id=?", (tid,))
        if body.category:
            if body.category not in ("character", "artist", "copyright", "custom", "general"):
                raise HTTPException(400, "不認得的分類")
            c.execute("UPDATE tags SET category=? WHERE id=?", (body.category, tid))
        if body.rename and body.rename != name:
            new = body.rename.strip().replace(" ", "_")
            if db.tag_id(new, create=False):
                raise HTTPException(400, "已有同名 tag")
            c.execute("UPDATE tags SET name=? WHERE id=?", (new, tid))
            db._tag_cache.clear()
        c.commit()
    return {"ok": True}


# ---------- 中文名整理：沒有中文名、有好幾個譯名、Bangumi 自動換的，讓你自己挑 ----------
@router.get("/api/implies")
def implies_summary():
    """系統 › 工具 ›「標籤蘊含」：規則數、Danbooru 問到哪、已確認的舊圖會多幾張。"""
    from .. import implies
    return implies.summary()


@router.post("/api/implies/sync")
def implies_sync(body: dict | None = None):
    """馬上問 Danbooru（背景）：沒問過的角色；force＝全部重問。"""
    import threading
    from .. import implies
    if not db.get_settings().get("online_enabled", True):
        raise HTTPException(400, "線上功能關著")
    force = bool((body or {}).get("force"))
    from .. import tasks
    ev = threading.Event()
    tasks.spawn("implies-sync", "標籤蘊含：問 Danbooru", implies.sync_danbooru, force=force, stop=ev.is_set, cancel=ev.set)
    return {"ok": True}


class BackfillReq(BaseModel):
    dry: bool = True


@router.post("/api/implies/backfill")
def implies_backfill(body: BackfillReq):
    """已經確認了 A、還沒有 B 的舊圖：dry 只數，否則套用（一筆撤銷）。"""
    from .. import implies
    return implies.backfill(dry=body.dry)


@router.get("/api/names/review")
def names_review(kind: str = "nozh", limit: int = 200, cat: str = "character"):
    """kind：fixed（校對過的譯名表改掉的：原本 → 現在）／nozh（顯示的不是中文）／auto（Bangumi 找到、換掉了原本的名字：原本是什麼、可以改回）／
    pick（Bangumi 有好幾個同名的，不確定是哪個：列出來讓你挑）／multi（Danbooru 有好幾個中文別名）／done（你自己取過名的）。
    cat：character 或 copyright。依你確認過的張數排，多的先。"""
    from .. import zhname
    cat = "copyright" if cat == "copyright" else "character"
    c = db.conn()
    lang = db.get_settings()["display_lang"]
    rows = c.execute("""SELECT t.name, ti.other_names, ti.display_name, ti.copyright,
        (SELECT COUNT(*) FROM media_tags x WHERE x.tag_id=t.id AND x.state=1) AS n,
        COALESCE(t.cover, (SELECT MAX(x.media_id) FROM media_tags x WHERE x.tag_id=t.id AND x.state=1)) AS cover
        FROM tags t LEFT JOIN tag_info ti ON ti.tag_id=t.id
        WHERE t.category=? AND EXISTS (SELECT 1 FROM media_tags x WHERE x.tag_id=t.id AND x.state=1)""", (cat,)).fetchall()
    zr = zhname.rows_for_review()
    counts = {"fixed": 0, "nozh": 0, "auto": 0, "pick": 0, "multi": 0, "done": 0}
    out = []
    for r in rows:
        on = json.loads(r["other_names"] or "[]")
        cands = names.candidates(on)
        pk = names.pokemon_zh(r["name"], on) if cat == "character" else None
        if pk and pk not in cands:
            cands.insert(0, pk)
        auto = names.series_display(r["name"], on, None, "zh") if cat == "copyright" else names.display(r["name"], on, None, "zh")
        z = zr.get(r["name"]) or {}
        bgm = z.get("zh") if z.get("status") == "found" else None
        prev = z.get("prev")
        opts = [o["zh"] for o in z.get("options") or []] if z.get("status") == "ambiguous" else []
        if bgm and bgm not in cands:
            cands.insert(0, bgm)
        for o in opts:
            if o not in cands:
                cands.append(o)
        if prev and prev not in cands and names.HAN_CHAR.search(prev) and not names.KANA.search(prev):
            cands.append(prev)
        nozh = not names.HAN_CHAR.search(auto) or bool(names.KANA.search(auto))
        cu = names.curated(r["name"])
        before = None
        if cu and cu not in cands:
            cands.insert(0, cu)
        if cu:          # 校對過的譯名表：沒有它的話會顯示什麼（「改回」用）
            before = names.series_display(r["name"], on, None, "zh", use_curated=False) if cat == "copyright" \
                else names.display(r["name"], on, None, "zh", use_curated=False)
            if before not in cands and names.HAN_CHAR.search(before) and not names.KANA.search(before):
                cands.append(before)
        k = "done" if r["display_name"] else \
            "fixed" if cu and before != cu else \
            "auto" if bgm and auto == bgm and prev and prev != bgm and not cu else \
            "pick" if opts and nozh else \
            "nozh" if nozh else \
            "multi" if len(cands) >= 2 and not bgm and not cu else None
        if not k:
            continue
        counts[k] += 1
        if k == kind:
            out.append({"name": r["name"], "display": names.series_display(r["name"], on, r["display_name"], lang) if cat == "copyright"
                        else names.display(r["name"], on, r["display_name"], lang), "auto": auto,
                        "manual": r["display_name"], "candidates": cands, "others": [x for x in on if x not in cands][:6],
                        "n": r["n"], "cover": r["cover"], "series": r["copyright"], "before": before,
                        "bgm": {"zh": bgm, "prev": prev, "ref": z.get("ref"), "seen": z.get("seen"), "status": z.get("status"),
                                "options": z.get("options") or []} if z else None})
    if kind == "auto":
        out.sort(key=lambda x: (bool((x["bgm"] or {}).get("seen")), -x["n"]))
    else:
        out.sort(key=lambda x: -x["n"])
    st = {"unchecked": len(zhname.pending()), "auto_on": bool(db.get_settings().get("auto_zhname", True))}
    return {"items": out[:max(1, min(limit, 1000))], "counts": counts, "bangumi": st, "unreviewed": _unreviewed(1)[0]}


class NamesBody(BaseModel):
    names: list[str] = []
    force: bool = False


@router.get("/api/names/debug")
def names_debug(tag: str):
    """診斷用：一個角色到 Wikidata／Bangumi 查中文名的每一步。"""
    from .. import zhname
    return zhname.debug(tag)


@router.get("/api/names/nettest")
def names_nettest():
    from .. import zhname
    return zhname.net_test()


@router.post("/api/names/seen")
def names_seen(body: NamesBody):
    """「自動換的」：你看過、覺得可以（不再排在前面）。"""
    from .. import zhname
    return {"seen": zhname.mark_seen(body.names[:2000])}


@router.post("/api/names/bangumi")
def names_bangumi(body: NamesBody):
    """現在到 Bangumi 找：沒給名字＝全部還沒查過的（force＝連查過的也重查）。"""
    from .. import zhname
    if not db.get_settings()["online_enabled"]:
        raise HTTPException(400, "線上功能關著（設定 › 線上）")
    todo = [n for n in body.names if db.tag_id(n, create=False)][:2000] if body.names else zhname.pending(force=body.force)
    return {"queued": zhname.queue(todo, manual=True)}


# ---------- 長期校對：還沒校對的名字 → 複製給 Claude（或自己）校對 → 貼回來 ----------
_CJK = re.compile(r"[぀-ヿ㐀-鿿]")
ZH_PROMPT = """請幫我校對下面這些動漫／遊戲角色和作品的繁體中文譯名（我在台灣）。
規則：
1. 用台灣官方譯名（台灣代理、官方繁中版、台灣播出的版本）；沒有台灣版才用港版或中國官方譯名，轉成繁體台灣用字
2. 不確定、或這個角色根本沒有中文名（原創角色、只在英文圈的角色）→ 名字寫「-」，不要用猜的
3. 作品名用正式名稱，不要加「(系列)」這種尾巴；角色不要帶作品名
4. 只輸出結果，每行一個：tag<Tab>中文名（tag 原樣照抄，不要改；不要加說明、不要用表格）

每行的格式：tag｜目前顯示的名字｜張數｜Danbooru 上的中日文別名（參考用）
"""


def _unreviewed(limit: int = 150) -> tuple[int, list[str]]:
    c = db.conn()
    rows = c.execute("""SELECT t.name, t.category, ti.other_names, ti.display_name, ti.copyright, COUNT(*) AS n
        FROM tags t JOIN media_tags x ON x.tag_id=t.id AND x.state=1 LEFT JOIN tag_info ti ON ti.tag_id=t.id
        WHERE t.category IN ('character','copyright') GROUP BY t.id""").fetchall()
    existing = {r[0] for r in rows if r[1] == "character"}
    todo = []
    for r in rows:
        name, cat = r["name"], r["category"]
        if r["display_name"] or names.reviewed(name):
            continue
        on = json.loads(r["other_names"] or "[]")
        if cat == "character" and (names.pokemon_zh(name, on) or names.base_of(name, existing)):
            continue            # 寶可夢官方譯名、造型（跟著本體）
        disp = names.series_display(name, on, None, "zh") if cat == "copyright" else names.display(name, on, None, "zh")
        al = " / ".join([a for a in on if _CJK.search(a)][:5])
        todo.append((r["n"], cat, r["copyright"] or "", f"{name}｜{disp}｜{r['n']}｜{al}"))
    todo.sort(key=lambda t: -t[0])
    picked = todo[:max(1, min(limit, 500))]
    out, last = [], None
    for _, cat, series, line in sorted(picked, key=lambda t: (t[1] != "copyright", t[2], -t[0])):
        head = "## 作品" if cat == "copyright" else f"## 角色（{names.series_display(series, [], None, 'zh') if series else '作品不明'}）"
        if head != last:
            out.append(head)
            last = head
        out.append(line)
    return len(todo), out


@router.get("/api/names/unreviewed")
def names_unreviewed(limit: int = 150):
    """還沒校對的角色／作品（有確認圖、沒手動取名、不在校對表裡），圖多的先；附上給 Claude 的說明。"""
    n, lines = _unreviewed(limit)
    return {"count": n, "shown": sum(1 for x in lines if not x.startswith("##")),
            "text": ZH_PROMPT + "\n" + "\n".join(lines) if lines else ""}


class CuratedBody(BaseModel):
    text: str


@router.post("/api/names/curated")
def names_curated(body: CuratedBody):
    """貼上校對結果：每行「tag<Tab>名字」（也接受 tag｜名字、tag = 名字、Markdown 表格）；名字是 - 或空白＝看過、沒有中文名。"""
    c = db.conn()
    items, bad = {}, []
    for line in body.text.splitlines():
        line = line.strip().strip("|").strip()
        if not line or line.startswith(("#", "```", "---", ":--", "tag\t", "tag｜", "tag |", "tag|")):
            continue
        parts = re.split(r"\t|｜|\s\|\s|\|| = ", line, maxsplit=1)
        if len(parts) < 2:
            bad.append(line[:60])
            continue
        tag, zh = parts[0].strip().strip("`"), parts[1].split("｜")[0].split("|")[0].strip().strip("`")
        if not c.execute("SELECT 1 FROM tags WHERE name=? AND category IN ('character','copyright')", (tag,)).fetchone():
            bad.append(tag[:60])
            continue
        items[tag] = None if zh in ("", "-", "－", "—", "無", "none", "None") else zh
    if not items:
        raise HTTPException(400, "沒有看得懂的行（格式：tag<Tab>中文名）")
    names.save_local(items)
    try:
        from .. import zhname
        zhname.reload()
        zhname.refresh_variants()       # 造型跟著本體的新名字
    except Exception:  # noqa
        pass
    return {"saved": sum(1 for v in items.values() if v), "none": sum(1 for v in items.values() if not v), "unknown": bad[:30]}
