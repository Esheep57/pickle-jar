"""標籤蘊含（b84）：「A 也代表 B」——確認 A 的圖自動帶上 B。

例：asuna_(bunny)_(blue_archive) ⇒ asuna_(blue_archive)（造型 ⇒ 本體）、自訂的「表情包 ⇒ 梗圖」。
以前「本體和造型算同一個人」寫死在程式裡（people.variant_base 看名字猜）；現在是一張資料表，可以看、可以改：

- 規則（表 tag_implies，src）：
  - danbooru：Danbooru 上的 tag implication（status=active），只收「角色 ⇒ 角色」。作品 ⇒ 母系列（fate/grand_order ⇒ fate_(series)）
    不收：母系列已經由 seriesmap 處理，不用在每張圖上多一個作品標籤。
  - manual：你在標籤頁 ⋯ ›「也代表…」加的，任何類別都可以。
  - off：你說「不是」的（Danbooru 的、或程式從名字猜的造型）——之後不套用、Danbooru 更新也不會加回來。
  - danbooru_copy（b93）：角色 ⇒ 作品，照 Danbooru 資料卡的「所屬作品」（tag_info.copyrights：這個角色最近 40 張貼文裡
    一起出現 ≥25% 的作品，最多 4 個；只標 original 的原創角色 ⇒ original）。只收圖庫裡已經有的作品標籤。
    依據（開發時的資料 10-19）：角色確認後你在同一張圖上確認的作品，61／113 次是緊接著角色按的；資料卡裡的作品在你圖上
    處理過的 7,500 次全部是確認、0 次否決。資料卡更新時跟著改（`sync_copyrights`）；你關掉的（off）、自己訂的不動。
- 套用：圖上確認（或加上）A → B 一起確認（source implied、reason 寫是誰帶上的）；B 你否決過的不動。
  一起記進同一筆撤銷。否決 A 時，只因為 A 才帶上的 B 一起拿掉；角色被否決時，只因為它才出現的作品**候選**也一起收掉（b93）。
- 已經確認過的舊圖不會自己改：系統 › 工具 ›「標籤蘊含」看會多幾張、你按了才套用（一筆撤銷）。
- 算人數（people.char_base）：程式從名字猜的造型 ＋ 角色 ⇒ 角色的規則，扣掉 off。
  b92：照 Danbooru——Danbooru 問過、上面有這個角色、卻沒有蘊含的，名字猜的不算（danbooru_says_no）。
"""
from __future__ import annotations

import logging
import threading
import time

from . import db

log = logging.getLogger("implies")
SCHEMA = """CREATE TABLE IF NOT EXISTS tag_implies(tag_id INTEGER NOT NULL, implies_id INTEGER NOT NULL, src TEXT NOT NULL,
              created_at REAL, PRIMARY KEY(tag_id, implies_id));
CREATE INDEX IF NOT EXISTS ix_tag_implies_to ON tag_implies(implies_id);
CREATE TABLE IF NOT EXISTS implies_checked(tag_id INTEGER PRIMARY KEY, checked_at REAL)"""
ACTIVE = ("danbooru", "manual", "danbooru_copy")
RECHECK_DAYS = 60
_cache: dict | None = None
_cache_lock = threading.Lock()


def ensure_schema(c):
    c.executescript(SCHEMA)


def invalidate():
    global _cache
    with _cache_lock:
        _cache = None


def _rules() -> dict[int, set[int]]:
    global _cache
    with _cache_lock:
        if _cache is None:
            d: dict[int, set[int]] = {}
            try:
                for a, b in db.conn().execute(f"SELECT tag_id, implies_id FROM tag_implies WHERE src IN {ACTIVE}"):
                    if a != b:
                        d.setdefault(a, set()).add(b)
            except Exception:  # noqa  舊資料庫還沒遷移
                d = {}
            _cache = d
        return _cache


def closure(tid: int) -> list[int]:
    """A 一路蘊含到的全部（A ⇒ B ⇒ C：[B, C]），有循環也不會卡住。"""
    r = _rules()
    out, todo, seen = [], list(r.get(tid, ())), {tid}
    while todo:
        x = todo.pop(0)
        if x in seen:
            continue
        seen.add(x)
        out.append(x)
        todo.extend(r.get(x, ()))
    return out


def off_pairs() -> set[tuple[int, int]]:
    try:
        return set(map(tuple, db.conn().execute("SELECT tag_id, implies_id FROM tag_implies WHERE src='off'")))
    except Exception:  # noqa
        return set()


def danbooru_says_no() -> set[int]:
    """Danbooru 問過、Danbooru 上有這個角色（有圖），而且沒有角色 ⇒ 角色的規則（你自己訂的也沒有）：
    照 Danbooru，名字看起來像造型也不算同一個人（b92）。"""
    try:
        return {r[0] for r in db.conn().execute(f"""SELECT k.tag_id FROM implies_checked k
            JOIN tag_info ti ON ti.tag_id=k.tag_id AND ti.post_count > 0
            WHERE NOT EXISTS(SELECT 1 FROM tag_implies i JOIN tags b ON b.id=i.implies_id AND b.category='character'
                             WHERE i.tag_id=k.tag_id AND i.src IN {ACTIVE})""")}
    except Exception:  # noqa  舊資料庫
        return set()


def char_pairs() -> list[tuple[int, int]]:
    """角色 ⇒ 角色的規則（給 people.refresh_bases）。"""
    try:
        return [tuple(r) for r in db.conn().execute(f"""SELECT i.tag_id, i.implies_id FROM tag_implies i
            JOIN tags a ON a.id=i.tag_id AND a.category='character' JOIN tags b ON b.id=i.implies_id AND b.category='character'
            WHERE i.src IN {ACTIVE}""")]
    except Exception:  # noqa
        return []


# ---------- 套用到圖上（在 write_lock 內呼叫） ----------
def apply(c, mids: list[int], tid: int) -> list:
    """圖上確認了 tid → 它蘊含的標籤一起確認。回傳撤銷用的 tag_rows（呼叫端併進同一筆撤銷）。"""
    imp = closure(tid)
    if not imp or not mids:
        return []
    name = c.execute("SELECT name FROM tags WHERE id=?", (tid,)).fetchone()
    why = f"由 {name[0] if name else tid} 蘊含"
    rows = []
    for b in imp:
        ph = ",".join("?" * len(mids))
        cur = {r[0]: r[1] for r in c.execute(f"SELECT media_id, state FROM media_tags WHERE tag_id=? AND media_id IN ({ph})", [b, *mids])}
        todo = [m for m in mids if cur.get(m) is None or cur.get(m) == 0]       # 已確認的不動、你否決過的不動
        if not todo:
            continue
        rows += db.snapshot_tag_rows(todo, b)
        c.executemany("""INSERT INTO media_tags(media_id, tag_id, score, source, state, tier, reason) VALUES(?,?,NULL,'implied',1,NULL,?)
            ON CONFLICT(media_id, tag_id) DO UPDATE SET state=1, source='implied', reason=excluded.reason""",
                      [(m, b, why) for m in todo])
    return rows


def unapply(c, mids: list[int], tid: int) -> list:
    """否決 tid：只因為 tid 才帶上的（source implied、圖上沒有別的確認標籤也蘊含它）一起拿掉。
    tid 是角色時（b93）：它的作品如果在圖上只是候選、而圖上別的角色（確認的或候選）都不屬於那部作品 → 候選一起收掉
    （刪掉候選，不是否決：這張圖也許真的是那部作品、只是不是這個角色）。"""
    imp = closure(tid)
    if not imp or not mids:
        return []
    copy = _copy_ids(c, imp)
    rows = []
    for m in mids:
        others = [r[0] for r in c.execute("SELECT tag_id FROM media_tags WHERE media_id=? AND state=1 AND tag_id!=?", (m, tid))]
        still = {x for o in others for x in closure(o)}
        drop = [b for b in imp if b not in still and c.execute(
            "SELECT 1 FROM media_tags WHERE media_id=? AND tag_id=? AND state=1 AND source='implied'", (m, b)).fetchone()]
        if copy:
            cands = [r[0] for r in c.execute("""SELECT x.tag_id FROM media_tags x JOIN tags t ON t.id=x.tag_id AND t.category='character'
                WHERE x.media_id=? AND x.state>=0 AND x.tag_id!=?""", (m, tid))]
            keep = still | {x for o in cands for x in closure(o)}
            drop += [b for b in copy if b not in keep and b not in drop and c.execute(
                "SELECT 1 FROM media_tags WHERE media_id=? AND tag_id=? AND state=0", (m, b)).fetchone()]
        for b in drop:
            rows += db.snapshot_tag_rows([m], b)
            c.execute("DELETE FROM media_tags WHERE media_id=? AND tag_id=?", (m, b))
    return rows


def _copy_ids(c, ids: list[int]) -> list[int]:
    if not ids:
        return []
    return [r[0] for r in c.execute(f"SELECT id FROM tags WHERE category='copyright' AND id IN ({','.join('?' * len(ids))})", ids)]


# ---------- 角色 ⇒ 作品（照 Danbooru 資料卡，b93） ----------
def sync_copyrights(tids: list[int] | None = None) -> dict:
    """從 tag_info.copyrights 重算角色 ⇒ 作品的規則（src danbooru_copy）。不連網、在鎖內寫。
    tids＝只算這幾個角色（存資料卡時）；None＝全部。你關掉的（off）、自己訂的（manual）不動。"""
    import json
    c = db.conn()
    series = {r[0]: r[1] for r in c.execute("SELECT name, id FROM tags WHERE category='copyright'")}
    where = f" AND t.id IN ({','.join('?' * len(tids))})" if tids else ""
    want: dict[int, set[int]] = {}
    for tid, cs in c.execute(f"""SELECT t.id, ti.copyrights FROM tags t JOIN tag_info ti ON ti.tag_id=t.id
            WHERE t.category='character' AND ti.status='ok'{where}""", tids or []):
        try:
            names = json.loads(cs or "[]")
        except ValueError:
            names = []
        want[tid] = {series[n] for n in names if n in series}
    scope = list(want) if tids is None else list(tids)
    added = removed = 0
    now = time.time()
    with db.write_lock:
        have: dict[int, set[int]] = {}
        for i in range(0, len(scope), 500):
            part = scope[i:i + 500]
            for a, b in c.execute(f"SELECT tag_id, implies_id FROM tag_implies WHERE src='danbooru_copy' AND tag_id IN ({','.join('?' * len(part))})", part):
                have.setdefault(a, set()).add(b)
        if tids is None:     # 資料卡不見了／不是角色了的舊規則
            for a, b in c.execute("SELECT tag_id, implies_id FROM tag_implies WHERE src='danbooru_copy'").fetchall():
                if a not in want:
                    c.execute("DELETE FROM tag_implies WHERE tag_id=? AND implies_id=? AND src='danbooru_copy'", (a, b))
                    removed += 1
        for a in scope:
            w, h = want.get(a, set()), have.get(a, set())
            for b in h - w:
                c.execute("DELETE FROM tag_implies WHERE tag_id=? AND implies_id=? AND src='danbooru_copy'", (a, b))
                removed += 1
            for b in w - h:
                added += c.execute("INSERT OR IGNORE INTO tag_implies(tag_id, implies_id, src, created_at) VALUES(?,?,'danbooru_copy',?)",
                                   (a, b, now)).rowcount
        c.commit()
    if added or removed:
        invalidate()
    return {"added": added, "removed": removed}


# ---------- 規則 ----------
def rules_for(name: str) -> dict:
    """標籤頁用：這個標籤也代表哪些（含來源）、哪些標籤也代表它、你關掉的。"""
    c = db.conn()
    tid = db.tag_id(name, create=False)
    if not tid:
        return {"implies": [], "implied_by": [], "off": [], "guessed": None}
    q = lambda sql: [dict(r) for r in c.execute(sql, (tid,))]
    out = {
        "implies": q(f"""SELECT t.name, t.category, i.src FROM tag_implies i JOIN tags t ON t.id=i.implies_id
                        WHERE i.tag_id=? AND i.src IN {ACTIVE} ORDER BY t.name"""),
        "implied_by": q(f"""SELECT t.name, t.category, i.src FROM tag_implies i JOIN tags t ON t.id=i.tag_id
                           WHERE i.implies_id=? AND i.src IN {ACTIVE} ORDER BY t.name LIMIT 200"""),
        "off": q("""SELECT t.name, t.category FROM tag_implies i JOIN tags t ON t.id=i.implies_id WHERE i.tag_id=? AND i.src='off'"""),
    }
    g = c.execute("""SELECT t.name FROM char_base cb JOIN tags t ON t.id=cb.base_id WHERE cb.tag_id=?""", (tid,)).fetchone()
    out["guessed"] = g[0] if g and g[0] not in {x["name"] for x in out["implies"]} else None
    return out


def set_rules(name: str, implies: list[str]) -> dict:
    """標籤頁「也代表…」存檔：清單裡的＝manual（Danbooru 的照舊）；原本有、被你拿掉的＝off。"""
    c = db.conn()
    tid = db.tag_id(name, create=False)
    if not tid:
        raise ValueError("沒有這個標籤")
    want: dict[int, str] = {}
    for n in implies:
        n = (n or "").strip().replace(" ", "_")
        if not n or n == name:
            continue
        b = db.tag_id(n, create=False)
        if not b:
            raise ValueError(f"沒有「{n}」這個標籤（先在圖上加過一次，或用 Danbooru 的正式名稱）")
        want[b] = n
    if any(tid in closure(b) or b == tid for b in want):
        raise ValueError("這樣會繞一圈（A 代表 B、B 又代表 A）")
    now = time.time()
    with db.write_lock:
        cur = {r[0]: r[1] for r in c.execute("SELECT implies_id, src FROM tag_implies WHERE tag_id=?", (tid,))}
        for b, src in cur.items():
            if src in ACTIVE and b not in want:
                c.execute("UPDATE tag_implies SET src='off', created_at=? WHERE tag_id=? AND implies_id=?", (now, tid, b))
        for b in want:
            if cur.get(b) in ACTIVE:
                continue
            c.execute("""INSERT INTO tag_implies(tag_id, implies_id, src, created_at) VALUES(?,?,'manual',?)
                         ON CONFLICT(tag_id, implies_id) DO UPDATE SET src='manual', created_at=excluded.created_at""", (tid, b, now))
        c.commit()
    invalidate()
    from . import people
    people.refresh_bases()
    return rules_for(name)


def set_off(name: str, other: str, off: bool = True):
    """「A 不代表 B」：Danbooru 的、程式從名字猜的造型都可以關掉；off=False 收回（刪掉 off 那一列）。"""
    c = db.conn()
    a, b = db.tag_id(name, create=False), db.tag_id(other, create=False)
    if not (a and b):
        raise ValueError("沒有這個標籤")
    with db.write_lock:
        if off:
            c.execute("""INSERT INTO tag_implies(tag_id, implies_id, src, created_at) VALUES(?,?,'off',?)
                         ON CONFLICT(tag_id, implies_id) DO UPDATE SET src='off', created_at=excluded.created_at""", (a, b, time.time()))
        else:
            c.execute("DELETE FROM tag_implies WHERE tag_id=? AND implies_id=? AND src='off'", (a, b))
        c.commit()
    invalidate()
    from . import people
    people.refresh_bases()


# ---------- Danbooru ----------
def sync_danbooru(tids: list[int] | None = None, force: bool = False, stop=None) -> dict:
    """問 Danbooru 這些角色有沒有蘊含（一次 40 個名字）。沒給 tids＝有確認圖、還沒問過（或 60 天前問的）的角色。"""
    from .danbooru import dan_get
    c = db.conn()
    if tids is None:
        old = time.time() - RECHECK_DAYS * 86400
        tids = [r[0] for r in c.execute(f"""SELECT DISTINCT t.id FROM tags t JOIN media_tags mt ON mt.tag_id=t.id AND mt.state=1
            LEFT JOIN implies_checked k ON k.tag_id=t.id
            WHERE t.category='character' {'' if force else 'AND (k.checked_at IS NULL OR k.checked_at < ?)'}""", () if force else (old,))]
    names = {r[0]: r[1] for r in c.execute(f"SELECT id, name FROM tags WHERE category='character' AND id IN ({','.join('?' * len(tids))})", tids)} if tids else {}
    chars = {r[1]: r[0] for r in c.execute("SELECT id, name FROM tags WHERE category='character'")}
    asked = added = 0
    ids = list(names)
    for i in range(0, len(ids), 40):
        if stop and stop():
            break
        chunk = ids[i:i + 40]
        try:
            res = dan_get("/tag_implications.json", {"search[antecedent_name_comma]": ",".join(names[x] for x in chunk),
                                                     "search[status]": "active", "only": "antecedent_name,consequent_name", "limit": 1000})
        except Exception as e:  # noqa
            log.warning("Danbooru 蘊含查詢失敗：%s", e)
            break
        now = time.time()
        with db.write_lock:
            for it in res or []:
                a, b = chars.get(it.get("antecedent_name")), chars.get(it.get("consequent_name"))
                if a and b and a != b:      # 只收角色 ⇒ 角色；manual、off 不蓋掉
                    cur = c.execute("INSERT OR IGNORE INTO tag_implies(tag_id, implies_id, src, created_at) VALUES(?,?,'danbooru',?)", (a, b, now))
                    added += cur.rowcount
            c.executemany("INSERT OR REPLACE INTO implies_checked(tag_id, checked_at) VALUES(?,?)", [(x, now) for x in chunk])
            c.commit()
        asked += len(chunk)
    if added:
        invalidate()
    if asked:       # 問過、沒有規則的也會改變「算不算同一個人」（danbooru_says_no）
        from . import people
        people.refresh_bases()
    return {"asked": asked, "added": added}


_sched: threading.Timer | None = None
_pending: set[int] = set()


def schedule(tids: list[int] | None = None, delay: float = 15.0):
    """新角色存了資料卡之後：等一下一起問（線上功能關著就不問）。"""
    global _sched
    if not db.get_settings().get("online_enabled", True):
        return
    if tids:
        _pending.update(tids)
    if _sched:
        _sched.cancel()

    def run():
        todo = sorted(_pending)
        _pending.clear()
        if todo:    # 新的本體：名字像它造型的也重問（之前問的時候本體還不在圖庫，Danbooru 的規則收不進來）
            from . import people
            want = set(todo)
            todo = sorted(want | {a for a, b in people.guessed_pairs() if b in want})
        try:
            sync_danbooru(todo or None)
        except Exception as e:  # noqa
            log.warning("Danbooru 蘊含更新失敗：%s", e)
    _sched = threading.Timer(delay, run)
    _sched.daemon = True
    _sched.start()


# ---------- 已確認的舊圖 ----------
def backfill(dry: bool = True) -> dict:
    """已經確認了 A、還沒有 B 的圖：dry＝只數；否則套用（一筆撤銷）。你否決過 B 的不動。"""
    c = db.conn()
    r = _rules()
    todo: dict[int, list[int]] = {}         # 標籤 A → 要套用的圖
    images: set[int] = set()
    rows_n = rejected = 0
    for a in r:
        imp = closure(a)
        mids = [x[0] for x in c.execute("SELECT media_id FROM media_tags WHERE tag_id=? AND state=1", (a,))]
        if not mids:
            continue
        hit = []
        for m in mids:
            st = {x[0]: x[1] for x in c.execute(f"SELECT tag_id, state FROM media_tags WHERE media_id=? AND tag_id IN ({','.join('?' * len(imp))})", [m, *imp])}
            need = [b for b in imp if st.get(b) in (None, 0)]
            rejected += sum(1 for b in imp if st.get(b) == -1)
            if need:
                hit.append(m)
                rows_n += len(need)
        if hit:
            todo[a] = hit
            images.update(hit)
    out = {"images": len(images), "rows": rows_n, "rejected": rejected, "rules": sum(len(v) for v in r.values())}
    if dry or not todo:
        return out
    with db.write_lock:
        rows = []
        for a, mids in todo.items():
            rows += apply(c, mids, a)
        if rows:
            db.push_action(f"套用標籤蘊含：{len(images)} 張", {"tag_rows": rows}, touched=[])
        c.commit()
    from . import people
    people.refresh(sorted(images))
    return {**out, "applied": True}


def summary() -> dict:
    c = db.conn()
    by = {r[0]: r[1] for r in c.execute("SELECT src, COUNT(*) FROM tag_implies GROUP BY src")}
    checked = c.execute("SELECT COUNT(*), MAX(checked_at) FROM implies_checked").fetchone()
    manual = [dict(x) for x in c.execute("""SELECT a.name AS name, a.category AS category, b.name AS implies, i.src FROM tag_implies i
        JOIN tags a ON a.id=i.tag_id JOIN tags b ON b.id=i.implies_id WHERE i.src IN ('manual','off') ORDER BY i.created_at DESC LIMIT 200""")]
    return {"danbooru": by.get("danbooru", 0), "danbooru_copy": by.get("danbooru_copy", 0), "manual": by.get("manual", 0), "off": by.get("off", 0),
            "checked": checked[0], "checked_at": checked[1], "list": manual, **backfill(dry=True)}
