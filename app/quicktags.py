"""快速標籤：梗圖、真人、截圖這種你天天在用的分類，比一般描述標籤（1girl、long_hair）常用得多。

- 是普通的「自訂」標籤（搜得到、有標籤頁），只是到處都有一鍵按鈕：數字鍵 1–9、選取列、大圖
- 在 quick_tags_skip 裡的（預設全部）：加上時順便「略過」未識別——梗圖、真人照片不用再認角色。
  只設略過旗標，不刪 AI 的角色候選（cosplay 照片可能還是想標角色）
- 在 no_char_tags 裡的（預設梗圖、截圖）：不再認角色——還沒決定的身分候選拿掉、之後任何來源都不再列候選、
  你的圖庫不推薦、不自動上傳以圖搜圖（使用者要求：標了梗圖、截圖的不要再檢查角色、不要再要我確認）
- 每個快速標籤可以有一個「AI 覺得可能是」的搜尋：標籤頁上提示「還有 N 張可能是，看一看」。
  標過 30 張以上的用你的標記學出來的（qtlearn.py，`guess:名字`，b93）；其他用分堆規則（HINTS）
"""
from __future__ import annotations

from . import db, query

HINTS = {"梗圖": "pile:meme|pile:texty", "表情包": "pile:texty", "迷因": "pile:meme|pile:texty",
         "真人": "is:real", "照片": "is:real", "真人照片": "is:real", "cosplay": "type:cosplay", "Cosplay": "type:cosplay",
         "公仔": "type:figure", "截圖": "is:screenshot", "漫畫": "pile:comic"}


def no_char_names() -> list[str]:
    return [n.strip().replace(" ", "_") for n in db.get_settings().get("no_char_tags", []) if n and n.strip()]


NO_CHAR_SQL = """SELECT x.media_id FROM media_tags x JOIN tags t ON t.id=x.tag_id WHERE x.state=1 AND t.name IN ({ph})"""


def no_char_ids_sql() -> tuple[str, list]:
    """「有梗圖、截圖這類標籤的圖」的子查詢（給 NOT IN 用）。設定空的時候回傳不會符合任何圖的條件。"""
    ns = no_char_names()
    if not ns:
        return "SELECT -1", []
    return NO_CHAR_SQL.format(ph=",".join("?" * len(ns))), ns


def has_no_char(c, mid: int) -> bool:
    ns = no_char_names()
    if not ns:
        return False
    return bool(c.execute(f"SELECT 1 FROM ({NO_CHAR_SQL.format(ph=','.join('?' * len(ns)))}) WHERE media_id=? LIMIT 1", [*ns, mid]).fetchone())


def purge_candidates(c, ids: list[int] | None = None) -> list:
    """梗圖、截圖：還沒決定的角色／創作者／作品候選全部拿掉（不是否決，不影響其他圖）。回傳撤銷用的 tag_rows。
    ids=None：整個圖庫有這些標籤的圖（啟動時清一次）。必須在 write_lock 內呼叫。"""
    sub, p = no_char_ids_sql()
    if ids is not None:
        ids = [int(i) for i in ids]
        if not ids:
            return []
        rows = []
        for i in range(0, len(ids), 500):
            ch = ids[i:i + 500]
            rows += _purge(c, f"mt.media_id IN ({','.join('?' * len(ch))}) AND mt.media_id IN ({sub})", [*ch, *p])
        return rows
    return _purge(c, f"mt.media_id IN ({sub})", p)


def _purge(c, cond: str, params: list) -> list:
    rows = [[r[0], r[1], [r[2], r[3], r[4], r[5], r[6]]] for r in c.execute(
        f"""SELECT mt.media_id, mt.tag_id, mt.score, mt.source, mt.state, mt.tier, mt.reason FROM media_tags mt
            JOIN tags t ON t.id=mt.tag_id WHERE mt.state=0 AND t.category IN ('character','artist','copyright') AND {cond}""", params)]
    c.executemany("DELETE FROM media_tags WHERE media_id=? AND tag_id=? AND state=0", [(r[0], r[1]) for r in rows])
    return rows


def names() -> list[str]:
    return [n.strip().replace(" ", "_") for n in db.get_settings().get("quick_tags", []) if n and n.strip()][:9]


def _tid(name: str, create: bool = True):
    return db.tag_id(name, "custom", create=create) if create else db.tag_id(name, create=False)


def toggle(ids: list[int], name: str, on: bool | None = None) -> dict:
    """加上／拿掉一個快速標籤（on=None：這幾張都有就拿掉，否則全部加上）。一次撤銷還原全部。"""
    ids = list(dict.fromkeys(int(i) for i in ids))
    if not ids or not name.strip():
        return {"ok": False, "n": 0}
    name = name.strip().replace(" ", "_")
    skip_names = {n.replace(" ", "_") for n in db.get_settings().get("quick_tags_skip", [])}
    with db.write_lock:
        c = db.conn()
        tid = _tid(name)
        ph = ",".join("?" * len(ids))
        have = {r[0] for r in c.execute(f"SELECT media_id FROM media_tags WHERE tag_id=? AND state=1 AND media_id IN ({ph})", [tid, *ids])}
        if on is None:
            on = len(have) < len(ids)
        rows = db.snapshot_tag_rows(ids, tid)
        prev = [[r[0], r[1] or 0] for r in c.execute(f"SELECT id, unrec_skip FROM media WHERE id IN ({ph})", ids)]
        if on:
            c.executemany("""INSERT INTO media_tags(media_id, tag_id, score, source, state) VALUES(?,?,NULL,'manual',1)
                ON CONFLICT(media_id, tag_id) DO UPDATE SET state=1, source='manual'""", [(i, tid) for i in ids])
            try:      # 你又加回去了：不再當成「不是」（qtlearn，b93）
                c.executemany("DELETE FROM qt_no WHERE media_id=? AND tag_id=?", [(i, tid) for i in ids])
            except Exception:  # noqa  還沒遷移
                pass
            if name in skip_names:
                # 2＝因為這個標籤才略過（你自己按過 L 的保持 1），拿掉標籤時只放回 2 的
                c.executemany("UPDATE media SET unrec_skip=CASE WHEN COALESCE(unrec_skip,0)=1 THEN 1 ELSE 2 END WHERE id=?", [(i,) for i in ids])
        else:
            c.executemany("DELETE FROM media_tags WHERE media_id=? AND tag_id=?", [(i, tid) for i in ids])
            try:      # 你拿掉的：AI 之後不再建議這幾張（qtlearn，b93）
                from . import qtlearn
                qtlearn.removed([i for i in ids if i in have], tid)
            except Exception:  # noqa
                pass
            if name in skip_names:
                # 拿掉梗圖：如果沒有別的「會略過」的快速標籤了，回到未識別（不然標錯的圖就再也找不到）
                others = [x for x in skip_names if x != name]
                keep = set()
                if others:
                    oph = ",".join("?" * len(others))
                    keep = {r[0] for r in c.execute(f"""SELECT x.media_id FROM media_tags x JOIN tags t ON t.id=x.tag_id
                        WHERE x.state=1 AND t.name IN ({oph}) AND x.media_id IN ({ph})""", [*others, *ids])}
                c.executemany("UPDATE media SET unrec_skip=0 WHERE id=? AND unrec_skip=2", [(i,) for i in ids if i not in keep])
        purged = []
        if on and name in no_char_names():     # 梗圖、截圖：不再認角色 → 還沒決定的候選拿掉
            purged = purge_candidates(c, ids)
        db.push_action(f"{'加上' if on else '拿掉'}「{name}」（{len(ids)} 張）", {"tag_rows": rows + purged, "skip": prev})
        c.commit()
    from . import qtlearn
    qtlearn.schedule()        # 2 分鐘後（連續標很多張合併成一次）重新學「AI 覺得也是」
    return {"ok": True, "on": on, "n": len(ids), "name": name, "no_char": on and name in no_char_names(), "cleared": len(purged)}


def summary(nsfw: str = "hide", hints: bool = True) -> list[dict]:
    """側欄、選取列用：每個快速標籤幾張、AI 覺得可能還有幾張沒標。"""
    c = db.conn()
    nf = f" AND {query.NSFW}=0" if nsfw == "hide" else ""
    skip = set(db.get_settings().get("quick_tags_skip", []))
    out = []
    for i, n in enumerate(names()):
        tid = _tid(n)      # 還沒用過也先建立：側欄點進去才有標籤頁（看得到「AI 覺得可能是」的提示）
        cnt = c.execute(f"""SELECT COUNT(*) FROM media_tags x JOIN media m ON m.id=x.media_id
            WHERE x.tag_id=? AND x.state=1 AND m.status='done' AND COALESCE(m.excluded,0)=0{nf}""", (tid or -1,)).fetchone()[0]
        from . import qtlearn
        hint = f"guess:{n}" if tid and qtlearn.has_model(tid) else HINTS.get(n)      # 有從你的標記學的就用它（b93）
        hint_n = None
        if hint and hints:
            w, p = query.compile_query(hint)
            hint_n = c.execute(f"""SELECT COUNT(*) FROM media m WHERE m.status='done' AND COALESCE(m.excluded,0)=0{nf}
                AND {' AND '.join(w)} AND m.id NOT IN (SELECT media_id FROM media_tags WHERE tag_id=? AND state>=0)""",
                               [*p, tid or -1]).fetchone()[0]
        out.append({"name": n, "key": i + 1, "n": cnt, "skip": n in skip, "hint": hint, "hint_n": hint_n,
                    "no_artist": n in set(db.get_settings().get("no_artist_tags", []))})
    return out


def sync_no_artist() -> None:
    """設定的「作者不可考」標籤 → tags.no_artist（還沒有的先建成自訂標籤）。啟動時、改設定後呼叫。"""
    want = [n.strip().replace(" ", "_") for n in db.get_settings().get("no_artist_tags", []) if n and n.strip()]
    with db.write_lock:
        c = db.conn()
        ids = [_tid(n) for n in want]
        c.execute("UPDATE tags SET no_artist=0 WHERE no_artist=1")
        c.executemany("UPDATE tags SET no_artist=1 WHERE id=?", [(i,) for i in ids if i])
        c.commit()
