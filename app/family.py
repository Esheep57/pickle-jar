"""獨立顯示的子作品跟母系列（b110：明日方舟：終末地 ⇄ 明日方舟）怎麼分圖、分角色。

Danbooru 的蘊含讓終末地的圖都帶著「明日方舟」，終末地角色的資料卡也常常把主要作品記成明日方舟；
另外，方舟的角色會在終末地「再旅」（同一個角色標籤兩款遊戲都用）。所以：

- 角色分三種（看 Danbooru 上這個角色的貼文有幾成標了終末地，`char_cp_share`，背景查）：
    原生（≥ 80%：陳千語、佩麗卡）／再旅（有、但不到 80%）／母系列的（沒有）
  還沒查到比例的，資料卡作品清單有終末地就先當原生（跟以前一樣）。
- 一張圖算終末地：有終末地原生角色，或圖上的終末地標籤不是蘊含帶上的（Danbooru、Pixiv、你自己標的）。
- 一張圖算母系列：有母系列自己的角色；或沒有終末地證據、但有再旅角色／母系列標籤。
  所以同框（方舟乾員＋終末地角色）兩邊都有；再旅角色的終末地服裝圖（有終末地標籤）只在終末地。
- 再旅角色兩個作品頁都會列，各自只算那邊的圖；角色書架放在圖比較多的那邊。
"""
from __future__ import annotations

import json
import logging
import time
from collections import Counter

from . import db, names

log = logging.getLogger("family")
SCHEMA = "CREATE TABLE IF NOT EXISTS char_cp_share(name TEXT PRIMARY KEY, shares TEXT, posts INTEGER, checked_at REAL)"
NATIVE = 0.8
RECHECK_DAYS = 60
_ready = False


def _ensure():
    global _ready
    if not _ready:
        with db.write_lock:
            db.conn().execute(SCHEMA)
        _ready = True


def shares() -> dict[str, dict]:
    _ensure()
    return {r[0]: json.loads(r[1] or "{}") for r in db.conn().execute("SELECT name, shares FROM char_cp_share")}


def groups() -> list[dict]:
    """每個獨立顯示的子作品：{child, parent, natives, duals}（角色 tag 名稱）。"""
    ind = names.independent()
    if not ind:
        return []
    c = db.conn()
    allcp = [r[0] for r in c.execute("SELECT name FROM tags WHERE category='copyright'")]
    sh = shares()
    out = []
    for ck in sorted(ind):
        pk = names.series_parent(ck, set(allcp) - {ck}, ignore_independent=True)
        if not pk:
            continue
        nat, dual, other = [], [], []
        for name, cps in c.execute("""SELECT t.name, ti.copyrights FROM tags t JOIN tag_info ti ON ti.tag_id=t.id
                                      WHERE t.category='character' AND COALESCE(ti.copyrights, '') LIKE ?""", (f'%"{ck}"%',)):
            if ck not in json.loads(cps or "[]"):
                continue
            s = sh.get(name)
            v = s.get(ck) if s is not None else None
            if s is None or (v or 0) >= NATIVE:       # 還沒查到比例：先當原生
                nat.append(name)
            elif v:
                dual.append(name)
            else:       # 自己的貼文沒有子作品（提豐：終末地的貼文都是堤豐帶上的）——資料卡的主要作品可能還記成子作品
                other.append(name)
        # 子作品自己的子標籤（邊緣行者 → 第一季、第二季）沒有另外獨立的：也算子作品的證據（只標了「邊緣行者 2」的圖）
        csubs = [k for k in allcp if k != ck and k not in ind and names.series_parent(k, {ck}) == ck]
        out.append({"child": ck, "parent": pk, "natives": nat, "duals": dual, "others": other, "subs": csubs})
    return out


def char_home(name: str, copyright: str | None, copyrights) -> str | None:
    """角色書架上這個角色放哪：原生 → 子作品；其他照資料卡（再旅的放哪邊由 shelf 看張數決定）。"""
    for g in groups_cached():
        if name in g["natives"]:
            return g["child"]
        if name in g["duals"] or name in g["others"]:
            return g["parent"]          # 再旅的之後看張數決定；其他放回母系列
    return copyright


_GC: dict = {"at": 0.0, "v": [], "sig": None}


def groups_cached() -> list[dict]:
    """書架一次要問上千個角色：5 秒內用同一份（設定改了馬上重算）。"""
    sig = (tuple(sorted(names.independent())), db._user_gen)
    if time.time() - _GC["at"] > 5 or _GC["sig"] != sig:
        try:
            _GC["v"] = groups()
        except Exception as e:  # noqa  資料表還沒建等
            log.info("讀子作品分組失敗：%s", e)
            _GC["v"] = []
        _GC["at"], _GC["sig"] = time.time(), sig
    return _GC["v"]


def child_evidence_sql(g: dict) -> tuple[str, list]:
    """「這張圖算子作品」的 SQL（m.id IN …）。"""
    nat = g["natives"] or ["\0"]
    own = [g["child"], *(g.get("subs") or [])]
    ph, po = ",".join("?" * len(nat)), ",".join("?" * len(own))
    return (f"""m.id IN (SELECT x.media_id FROM media_tags x JOIN tags t ON t.id=x.tag_id
               WHERE x.state=1 AND ((t.category='character' AND t.name IN ({ph}))
                                    OR (t.name IN ({po}) AND COALESCE(x.source, '') != 'implied')))""", [*nat, *own])


def parent_only_char_sql(g: dict, subs: list[str]) -> tuple[str, list]:
    """「這張圖有母系列自己的角色」（不是原生、也不是再旅的）。"""
    skip = (g["natives"] + g["duals"]) or ["\0"]
    # 母系列自己的角色：資料卡主要作品是母系列的，加上「資料卡記成子作品、但自己的貼文不是」的（提豐）
    extra = g.get("others") or []
    ph, ps = ",".join("?" * len(subs)), ",".join("?" * len(skip))
    return (f"""m.id IN (SELECT x.media_id FROM media_tags x JOIN tags t ON t.id=x.tag_id JOIN tag_info ti ON ti.tag_id=t.id
               WHERE x.state=1 AND t.category='character' AND (ti.copyright IN ({ph}) OR t.name IN ({",".join("?" * len(extra)) or "NULL"}))
                 AND t.name NOT IN ({ps})
                 AND COALESCE(x.source, '') != 'implied'
                 AND NOT EXISTS (SELECT 1 FROM tag_implies ip JOIN media_tags y ON y.tag_id=ip.tag_id AND y.media_id=x.media_id AND y.state=1
                                 WHERE ip.implies_id=x.tag_id AND ip.src != 'off'))""", [*subs, *extra, *skip])
    # 被同一張圖上別的角色「帶上」的不算（Danbooru：typhoeus ⇒ typhon，終末地的堤豐每張都有方舟的提豐；造型 ⇒ 本體也一樣）


# ---------- 背景：Danbooru 上這個角色的貼文幾成標了子作品（kind 'cpshare'） ----------
def share_pending() -> list[str]:
    ind = names.independent()
    if not ind:
        return []
    _ensure()
    c = db.conn()
    cutoff = time.time() - RECHECK_DAYS * 86400
    done = {r[0] for r in c.execute("SELECT name FROM char_cp_share WHERE checked_at > ?", (cutoff,))}
    out = []
    for ck in ind:
        for name, cps in c.execute("""SELECT t.name, ti.copyrights FROM tags t JOIN tag_info ti ON ti.tag_id=t.id
                WHERE t.category='character' AND COALESCE(ti.copyrights, '') LIKE ?
                  AND EXISTS (SELECT 1 FROM media_tags x WHERE x.tag_id=t.id AND x.state>=0)""", (f'%"{ck}"%',)):
            if ck in json.loads(cps or "[]") and name not in done:
                out.append(name)
    return list(dict.fromkeys(out))


def check_shares(names_: list[str], stop) -> int:
    from .danbooru import dan_get
    _ensure()
    n = 0
    for name in names_:
        if stop():
            break
        try:
            posts = dan_get("/posts.json", {"tags": name, "limit": 100, "only": "tag_string_copyright,tag_string_character"})
            # 會「帶上」這個角色的其他角色（typhoeus ⇒ typhon）：那些貼文不算這個角色自己的（不然方舟的提豐 89% 是終末地）
            by = {r["antecedent_name"] for r in dan_get("/tag_implications.json", {"search[consequent_name]": name,
                                                                                   "search[status]": "active", "only": "antecedent_name"})}
        except Exception as e:  # noqa
            log.info("查 %s 的作品比例失敗：%s", name, e)
            continue
        own = [p for p in posts if not (by & set((p.get("tag_string_character") or "").split()))]
        cnt = Counter(cp for p in own for cp in set((p.get("tag_string_copyright") or "").split()))
        tot = len(own) or 1
        posts = own
        sh = {k: round(v / tot, 3) for k, v in cnt.items()}
        with db.write_lock:
            db.conn().execute("INSERT OR REPLACE INTO char_cp_share(name, shares, posts, checked_at) VALUES(?,?,?,?)",
                              (name, json.dumps(sh, ensure_ascii=False), len(posts), time.time()))
        n += 1
    db.user_wrote()
    return n


def queue_shares() -> int:
    from . import jobqueue, online
    if not db.get_settings().get("online_enabled"):
        return 0
    j = online.get_jobs()
    q = {n for r in j.q.rows() if r["kind"] == "cpshare" and isinstance(r["arg"], list) for n in r["arg"]}
    todo = [n for n in share_pending() if n not in q]
    if todo:
        j.submit("cpshare", todo, jobqueue.NORMAL)
    return len(todo)
