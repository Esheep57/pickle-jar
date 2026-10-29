"""角色書架與作品頁。"""
from __future__ import annotations

import json
import time

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .. import db, family, names, query
from ..query import NSFW

router = APIRouter()
ALB = " AND m.id NOT IN (SELECT media_id FROM album_items)"     # 作品集裡的頁不算（noalbum）

# ---------- 作品頁 ----------
@router.get("/api/series/{key:path}")
def series_detail(key: str, nsfw: str = "hide", noalbum: bool = False):
    """作品頁：作品資訊＋這部作品裡的角色（子作品已併入，例如 Fate 系列含 FGO）。圖庫本身用 series: 搜尋。"""
    d = shelf(nsfw, noalbum=noalbum)
    g = next((x for x in d["series"] if x["key"] == key), None)
    if g is None:   # 子作品已經併進母系列（Limbus Company → Project Moon）→ 告訴畫面去母系列的頁面
        par = next((x["key"] for x in d["series"] if x["key"] and names.series_parent(key, {x["key"]}) == x["key"]), None)
        if par:
            return {"key": key, "parent": par}
    chars = [ch for ch in d["chars"] if (ch["series"] or "") == key]
    # 再旅角色：另一邊的作品頁也列，張數只算這邊的（b110）
    have = {ch["name"] for ch in chars}
    for ch in d["chars"]:
        sp = ch.get("split") or {}
        if ch["name"] not in have and sp.get(key):
            chars.append({**ch, "n": sp[key], "series": key})
        elif ch["name"] in have and key in sp:
            chars[[x["name"] for x in chars].index(ch["name"])] = {**ch, "n": sp[key]}
    chars.sort(key=lambda x: -x["n"])
    c = db.conn()
    info = c.execute("""SELECT ti.url, ti.summary, ti.post_count FROM tags t JOIN tag_info ti ON ti.tag_id=t.id
                        WHERE t.name=?""", (key,)).fetchone()
    subs = sorted({ch.get("series_raw") for ch in chars if ch.get("series_raw") and ch.get("series_raw") != key})
    pr = c.execute("SELECT pinned FROM tags WHERE name=?", (key,)).fetchone()
    lang = db.get_settings()["display_lang"]
    from .. import wikiname
    # 沒有角色的作品（原創、只標了作品的圖）不在書架的分組裡：名稱、張數、封面直接從作品標籤拿
    # （以前這裡沒讀你取的名字 → 作品頁「改名」存了卻看不到）
    ti = c.execute("""SELECT t.id, t.cover, ti.other_names, ti.display_name FROM tags t LEFT JOIN tag_info ti ON ti.tag_id=t.id
                      WHERE t.name=?""", (key,)).fetchone()
    disp = g["display"] if g else names.series_display(key, json.loads(ti["other_names"] or "[]") if ti and ti["other_names"] else [],
                                                        ti["display_name"] if ti else None, lang, wikiname.cached([key]).get(key))
    if g is None and ti is not None:
        nf = (f" AND {NSFW}=0" if nsfw == "hide" else "") + (ALB if noalbum else "")
        tn, rc = c.execute(f"""SELECT COUNT(*), COALESCE(SUM(m.added_at >= ?), 0) FROM media_tags mt
            JOIN media m ON m.id=mt.media_id WHERE mt.tag_id=? AND mt.state=1 AND m.status='done'{nf}""",
                           (time.time() - d["days"] * 86400, ti["id"])).fetchone()
        cov = [r[0] for r in c.execute(f"""SELECT m.id FROM media_tags mt JOIN media m ON m.id=mt.media_id
            WHERE mt.tag_id=? AND mt.state=1 AND m.status='done'{nf} ORDER BY COALESCE(m.mtime, m.added_at) DESC LIMIT 3""", (ti["id"],))]
        if ti["cover"]:
            cov = [ti["cover"]] + [x for x in cov if x != ti["cover"]][:2]
        g = {"n": tn, "recent": rc, "covers": cov, "custom_cover": ti["cover"]}
    pending = sorted((p for p in d["pending"] if p["series"] == key), key=lambda p: -p["cand"])
    # 這部作品裡還沒標角色／作者的圖（標了作品卻沒有角色、作者的圖以前很難找）
    miss = {}
    if key:
        sq, sp = query._series_sql([key])
        nf2 = (f" AND {NSFW}=0" if nsfw == "hide" else "") + (ALB if noalbum else "")
        for k, cond in (("nochar", "is:nochar"), ("noartist", "is:missartist")):
            w2, p2 = query.compile_query(cond)
            miss[k] = c.execute(f"""SELECT COUNT(*) FROM media m WHERE m.status='done' AND COALESCE(m.excluded,0)=0{nf2}
                AND {sq} AND {w2[0]}""", [*sp, *p2]).fetchone()[0]
    return {"key": key, "display": disp, "chars": chars, "pending": pending, "subs": subs, "days": d["days"], "miss": miss,
            "n": g["n"] if g else 0, "recent": g["recent"] if g else 0, "covers": g["covers"] if g else [],
            "custom_cover": g.get("custom_cover") if g else None,
            "url": info["url"] if info else None, "summary": info["summary"] if info else None,
            "post_count": info["post_count"] if info else None, "pinned": bool(pr and pr[0]),
            "sub_display": {k: names.series_display(k, [], None, lang) for k in subs},
            **_family(key, d, lang)}


def _family(key: str, d: dict, lang: str) -> dict:
    """（b110）獨立顯示的子作品跟母系列互相連得到：母系列頁列出「相關」的獨立子作品；子作品頁有「屬於 X」。"""
    keys = {x["key"] for x in d["series"] if x["key"]} | {key}
    ind = names.independent()
    parent = names.series_parent(key, keys - {key}, ignore_independent=True) if key in ind else None
    related = [k for k in ind if k != key and k in keys and names.series_parent(k, {key}, ignore_independent=True) == key]
    disp = lambda k: next((x["display"] for x in d["series"] if x["key"] == k), names.series_display(k, [], None, lang))
    can = names.series_parent(key, keys - {key}, ignore_independent=True) if key not in ind else parent
    return {"independent": key in ind, "family_parent": parent and {"key": parent, "display": disp(parent)},
            "can_split": bool(can), "split_parent": can and {"key": can, "display": disp(can)},
            "related": [{"key": k, "display": disp(k)} for k in sorted(related)]}


class IndepReq(BaseModel):
    on: bool


@router.post("/api/series/{key:path}/independent")
def series_independent(key: str, body: IndepReq):
    """作品頁的「獨立顯示」開關：存在設定 series_independent。"""
    cur = list(db.get_settings().get("series_independent") or [])
    if body.on and key not in cur:
        cur.append(key)
    if not body.on:
        cur = [k for k in cur if k != key]
    db.update_settings({"series_independent": cur})
    db.user_wrote()                    # 書架、作品頁的快取跟著換
    if body.on:
        from .. import family
        family.queue_shares()          # 哪些角色是原生、哪些是再旅（Danbooru 貼文的比例）
    return {"ok": True, "independent": body.on}



class CoverReq(BaseModel):
    media_id: int | None = None     # None = 恢復自動



@router.post("/api/series/{key:path}/cover")
def series_cover(key: str, body: CoverReq):
    """自選作品封面（書架和作品頁的第一張）。作品 tag 還不存在時（只有角色帶出作品）也可以設。"""
    if not key:
        raise HTTPException(400, "「未知作品」不能設封面")
    c = db.conn()
    if body.media_id and not c.execute("SELECT 1 FROM media WHERE id=?", (body.media_id,)).fetchone():
        raise HTTPException(404, "找不到這張圖")
    with db.write_lock:
        tid = db.tag_id(key, "copyright", create=bool(body.media_id))
        if tid:
            c.execute("UPDATE tags SET cover=? WHERE id=?", (body.media_id, tid))
        c.commit()
    return {"ok": True}



# ---------- 角色書架 ----------
@router.get("/api/shelf")
def shelf(nsfw: str = "hide", days: int = 7, noalbum: bool = False):
    """（b96）資料沒變就沿用上次算的：見 db.read_cache。"""
    return db.read_cache("shelf", (nsfw, days, noalbum,), lambda: _shelf(nsfw, days, noalbum), extra=None)


def _shelf(nsfw: str = "hide", days: int = 7, noalbum: bool = False):
    """角色書架：角色（造型併入本體）、作品分組、近期新增。nsfw: hide / blur / show
    noalbum：張數、封面不算作品集裡的頁（跟角色頁的圖一致；前端照「也顯示作品集裡的頁」帶）
    近期新增（b99.2）：看「加進圖庫的時間」（added_at），近 7 天。以前看檔案修改日期：Google 相簿匯出的舊照片
    日期都是匯出那天，很多舊照片會被當成新的。"""
    c = db.conn()
    s = db.get_settings()
    lang = s["display_lang"]
    nf = (f" AND {NSFW}=0" if nsfw == "hide" else "") + (ALB if noalbum else "")
    sfw_first = f"{NSFW.replace('m.', 'm.')} ASC, " if nsfw != "show" else ""   # 模糊模式：封面優先挑安全的圖
    since = time.time() - days * 86400
    rows = c.execute(f"""SELECT t.id, t.name, t.cover, t.pinned, ti.copyright, ti.copyrights, ti.other_names, ti.display_name, ti.match_suggest,
          SUM(mt.state=1) n, SUM(mt.state=1 AND m.added_at >= ?) recent, SUM(mt.state=0) cand,
          MAX(CASE WHEN mt.state=1 THEN COALESCE(m.mtime, m.added_at) END) last
        FROM tags t CROSS JOIN media_tags mt ON mt.tag_id=t.id AND mt.state>=0
        CROSS JOIN media m ON m.id=mt.media_id AND m.status='done' AND COALESCE(m.excluded,0)=0{nf}
        LEFT JOIN tag_info ti ON ti.tag_id=t.id
        WHERE t.category='character' GROUP BY t.id""", (since,)).fetchall()
    # CROSS JOIN = 固定從「角色 tag（約一千個）」出發走 (tag_id, state) 索引；讓 SQLite 自己選會從全部圖出發、慢 7 倍
    # 每個角色的封面：自選 > 最近收的已確認圖
    # 每個角色留前 6 張備選：作品封面要三張「不同的圖」，多人合照常常同時是好幾個角色的封面
    alts: dict[int, list[int]] = {}
    for r in c.execute(f"""SELECT tag_id, media_id FROM (
          SELECT mt.tag_id, mt.media_id, ROW_NUMBER() OVER (PARTITION BY mt.tag_id ORDER BY {sfw_first}COALESCE(m.mtime, m.added_at) DESC) rn
          FROM media_tags mt JOIN tags t ON t.id=mt.tag_id JOIN media m ON m.id=mt.media_id
          WHERE t.category='character' AND mt.state=1 AND m.status='done' AND COALESCE(m.excluded,0)=0{nf}) WHERE rn<=6 ORDER BY tag_id, rn"""):
        alts.setdefault(r[0], []).append(r[1])
    covers = {k: v[0] for k, v in alts.items()}
    visible = None
    if nsfw == "hide":      # 自選封面如果是 NSFW，隱藏模式下不用它
        visible = lambda mid: bool(mid) and c.execute(f"SELECT 1 FROM media m WHERE m.id=? AND {NSFW}=0 AND COALESCE(m.excluded,0)=0", (mid,)).fetchone() is not None
    else:
        visible = lambda mid: bool(mid) and c.execute("SELECT 1 FROM media WHERE id=? AND COALESCE(excluded,0)=0", (mid,)).fetchone() is not None
    existing = {r["name"] for r in rows}
    from .. import seriesguess
    guess = seriesguess.guess_all()          # 還不知道作品的角色：先用名字尾巴／同圖的作品推測（標「推測」）
    chars: dict[str, dict] = {}
    for r in rows:
        chars[r["name"]] = {
            "name": r["name"], "n": r["n"] or 0, "recent": r["recent"] or 0, "cand": r["cand"] or 0, "last": r["last"],
            "series": family.char_home(r["name"], r["copyright"], r["copyrights"]) or guess.get(r["name"]), "series_guess": not r["copyright"] and r["name"] in guess, "aliases": json.loads(r["other_names"] or "[]"), "pinned": r["pinned"],
            "display": names.display(r["name"], json.loads(r["other_names"] or "[]"), r["display_name"], lang),
            "cover": r["cover"] if r["cover"] and visible(r["cover"]) else covers.get(r["id"]), "skins": [], "_id": r["id"],
            "_alts": ([r["cover"]] if r["cover"] and visible(r["cover"]) else []) + alts.get(r["id"], []),
            "suggest": json.loads(r["match_suggest"]) if r["match_suggest"] else None,
        }
    # 造型併入本體——除了屬於「獨立顯示」子作品的版本（終末地的再旅版本 xxx_(endfield)_(arknights)：自己一個角色，歸子作品；b110）
    nat = {n for g in family.groups_cached() for n in g["natives"]}
    for name in list(chars):
        b = names.base_of(name, existing)
        if b and b in chars and name in chars and not (name in nat and b not in nat):
            v = chars.pop(name)
            bb = chars[b]
            bb["skins"].append({"name": name, "n": v["n"]})
            bb["n"] += v["n"]
            bb["recent"] += v["recent"]
            bb["cand"] += v["cand"]
            bb["aliases"] += v["aliases"]
            bb.setdefault("_ids", [bb["_id"]]).append(v["_id"])
            bb["_alts"] += v["_alts"]
    # 有造型的角色：同一張圖可能同時標了本體和造型 → 重新算不重複的張數
    for ch in chars.values():
        tids = ch.pop("_ids", None)
        ch["_tids"] = tids or [ch.pop("_id", None)]
        ch.pop("_id", None)
        if tids:
            ph = ",".join("?" * len(tids))
            q = f"""SELECT COUNT(DISTINCT mt.media_id), COUNT(DISTINCT CASE WHEN m.added_at >= ? THEN mt.media_id END)
                    FROM media_tags mt JOIN media m ON m.id=mt.media_id AND m.status='done' AND COALESCE(m.excluded,0)=0{nf}
                    WHERE mt.state=1 AND mt.tag_id IN ({ph})"""
            ch["n"], ch["recent"] = c.execute(q, (since, *tids)).fetchone()
    # 作品：同一個系列的子作品併進母系列（FGO → Fate 系列、Limbus Company → Project Moon…），避免重複
    keys = {ch["series"] for ch in chars.values() if ch["series"] and ch["n"] > 0}
    # 圖上直接標的作品也算（例如 project_moon 只出現在作品 tag 上，不是任何角色的主要作品）
    keys |= {r[0] for r in c.execute("""SELECT DISTINCT t.name FROM tags t JOIN media_tags mt ON mt.tag_id=t.id AND mt.state=1
                                        WHERE t.category='copyright'""")}
    from .. import online as _online, seriesmap
    if s["online_enabled"]:
        miss = seriesmap.need_lookup(sorted(keys))
        if miss:
            _online.get_jobs().queue_series_parent(miss)
    fk, pmap = frozenset(keys), {}      # 每部作品只算一次母系列（角色上千個，作品才幾百部）
    for ch in chars.values():
        if ch["series"]:
            ch["series_raw"] = ch["series"]
            if ch["series"] not in pmap:
                pmap[ch["series"]] = names.series_parent(ch["series"], fk) or ch["series"]
            ch["series"] = pmap[ch["series"]]
    # b110 再旅角色（同一個角色標籤兩款遊戲都用）：兩個作品頁都列、各算各的；書架放在圖比較多的那邊
    groups = family.groups_cached()
    gsets: dict[str, set] = {}
    for g in groups:
        duals = [chars[n] for n in g["duals"] if n in chars and chars[n]["n"] > 0]
        if not duals:
            continue
        ce, cp = family.child_evidence_sql(g)
        cset = gsets.setdefault(g["child"], {r[0] for r in c.execute(
            f"SELECT m.id FROM media m WHERE m.status='done' AND COALESCE(m.excluded,0)=0{nf} AND {ce}", cp)})
        for ch in duals:
            ph = ",".join("?" * len(ch["_tids"]))
            mine = {r[0] for r in c.execute(f"""SELECT DISTINCT mt.media_id FROM media_tags mt JOIN media m ON m.id=mt.media_id
                AND m.status='done' AND COALESCE(m.excluded,0)=0{nf} WHERE mt.state=1 AND mt.tag_id IN ({ph})""", ch["_tids"])}
            nc = len(mine & cset)
            ch["split"] = {g["child"]: nc, g["parent"]: len(mine) - nc}
            ch["series"] = g["child"] if nc > len(mine) - nc else g["parent"]
    # 作品：名稱與中文顯示
    series_names = {ch["series"] for ch in chars.values() if ch["series"]}
    sinfo = {}
    if series_names:
        ph = ",".join("?" * len(series_names))
        for r in c.execute(f"""SELECT t.name, ti.other_names, ti.display_name FROM tags t LEFT JOIN tag_info ti ON ti.tag_id=t.id
                               WHERE t.name IN ({ph})""", list(series_names)):
            sinfo[r["name"]] = {"aliases": json.loads(r["other_names"] or "[]"), "display_name": r["display_name"]}
    from .. import online, wikiname
    auto = wikiname.cached(list(series_names))
    if lang == "zh" and s["online_enabled"] and s.get("online_auto_charinfo", True):
        miss = wikiname.need_lookup(list(series_names))
        if miss:
            online.get_jobs().queue_series_zh(miss)
    series: dict[str, dict] = {}
    for ch in chars.values():
        if ch["n"] <= 0:
            continue
        k = ch["series"] or ""
        si = sinfo.get(k, {})
        ch["series_display"] = names.series_display(k, si.get("aliases"), si.get("display_name"), lang, auto.get(k)) if k else "未知作品"
        g = series.setdefault(k, {"key": k, "display": ch["series_display"], "aliases": si.get("aliases", []),
                                  "chars": 0, "n": 0, "recent": 0, "covers": []})
        g["chars"] += 1
        g["n"] += ch["n"]
        g["recent"] += ch["recent"]
    # 作品封面：自選的放第一張，其餘從張數最多的角色輪流挑，三張一定是不同的圖
    raw_of: dict[str, set] = {}
    for ch in chars.values():
        if ch.get("series_raw"):
            raw_of.setdefault(ch["series"], set()).add(ch["series_raw"])
    # 作品的張數＝不重複的圖（跟作品頁的圖庫、搜尋一樣）：角色屬於這部作品的圖＋直接標了這部作品的圖。
    # 以前把每個角色的張數加起來，多人圖被算好幾次：蔚藍檔案卡片寫 693 張，點進去只有 467 張
    tid2key: dict[int, str] = {}
    for ch in chars.values():
        if ch["n"] > 0:
            for t in ch["_tids"]:
                tid2key[t] = ch["series"] or ""
    cpkeys = {raw: k for k in series if k for raw in (raw_of.get(k, set()) | {k})}
    if cpkeys:
        ph = ",".join("?" * len(cpkeys))
        for r in c.execute(f"SELECT id, name FROM tags WHERE category='copyright' AND name IN ({ph})", list(cpkeys)):
            tid2key.setdefault(r[0], cpkeys[r[1]])
    if tid2key:
        got: dict[str, set] = {}
        rec: dict[str, set] = {}
        for tid, mid, ts in c.execute(f"""SELECT mt.tag_id, mt.media_id, m.added_at FROM media_tags mt
                JOIN media m ON m.id=mt.media_id AND m.status='done' AND COALESCE(m.excluded,0)=0{nf}
                WHERE mt.state=1 AND mt.tag_id IN (SELECT id FROM tags WHERE category IN ('character','copyright'))"""):
            k = tid2key.get(tid)
            if k is None:
                continue
            got.setdefault(k, set()).add(mid)
            if (ts or 0) >= since:
                rec.setdefault(k, set()).add(mid)
        # b110 獨立顯示的子作品（終末地）跟它的母系列：張數照作品頁的圖庫算（family.py 的規則）
        for g in groups:
            for k in (g["child"], g["parent"]):
                if k in series:
                    sq, sp = query._series_sql([k])
                    got[k] = {r[0] for r in c.execute(f"""SELECT m.id FROM media m WHERE m.status='done'
                        AND COALESCE(m.excluded,0)=0{nf} AND {sq}""", sp)}
                    rec[k] = {r[0] for r in c.execute(f"""SELECT m.id FROM media m WHERE m.status='done'
                        AND COALESCE(m.excluded,0)=0{nf} AND m.added_at >= ? AND {sq}""", [since, *sp])}
        for k, g in series.items():
            if k in got:
                g["n"], g["recent"] = len(got[k]), len(rec.get(k, ()))
    scover = {r[0]: r[1] for r in c.execute("SELECT name, cover FROM tags WHERE category='copyright' AND cover IS NOT NULL")}
    pinned_s = {r[0] for r in c.execute("SELECT name FROM tags WHERE category='copyright' AND pinned=1")}
    for g in series.values():
        k = g["key"]
        own = scover.get(k) or next((scover[x] for x in sorted(raw_of.get(k, ())) if x in scover), None)
        g["custom_cover"] = own if own and visible(own) else None
        g["pinned"] = k in pinned_s
        members = sorted((ch for ch in chars.values() if (ch["series"] or "") == k and ch["n"] > 0), key=lambda x: -x["n"])
        g["top_chars"] = [{"name": ch["name"], "display": ch["display"], "cover": ch["cover"]} for ch in members[:6]]
        picked = [g["custom_cover"]] if g["custom_cover"] else []
        for ch in members[:8]:          # 每個角色先各出一張：跟前面撞圖就換他的下一張
            if len(picked) >= 3:
                break
            m = next((x for x in ch["_alts"] if x not in picked), None)
            if m:
                picked.append(m)
        for ch in members[:8]:          # 角色太少（一兩位）：同一個角色再多拿幾張
            for x in ch["_alts"]:
                if len(picked) < 3 and x not in picked:
                    picked.append(x)
        g["covers"] = picked[:3]
    for ch in chars.values():
        ch.pop("_alts", None)
        ch.pop("_tids", None)
    confirmed = [ch for ch in chars.values() if ch["n"] > 0]
    pending = [{"name": ch["name"], "display": ch["display"], "cand": ch["cand"], "cover": ch["cover"], "series": ch["series"]}
               for ch in chars.values() if ch["n"] <= 0 and ch["cand"] > 0 and ch["series"]]
    return {
        "pending": pending,
        "chars": sorted(confirmed, key=lambda x: -x["n"]),
        "series": sorted(series.values(), key=lambda g: -g["n"]),
        "only_candidates": sum(1 for ch in chars.values() if ch["n"] <= 0 and ch["cand"] > 0),
        # 還不知道作品的角色、其中還沒查過 Danbooru 資料卡的（背景會補查）
        "unknown": sum(1 for ch in confirmed if not ch["series"]),
        "unfetched": c.execute("""SELECT COUNT(*) FROM tags t LEFT JOIN tag_info ti ON ti.tag_id=t.id
            WHERE t.category='character' AND ti.tag_id IS NULL
              AND EXISTS (SELECT 1 FROM media_tags x WHERE x.tag_id=t.id AND x.state=1)""").fetchone()[0],
        "guessed": sum(1 for ch in confirmed if ch.get("series_guess")),
        "suggest": [{"name": ch["name"], "display": ch["display"], "n": ch["n"], "to": ch["suggest"]}
                    for ch in chars.values() if ch["suggest"]],
        "days": days,
    }
