"""作品集：一本同人本、一套 cos 圖包、一本畫集——你自己建的一組「有順序」的圖。

為什麼要有：同人本的每一頁、cos 圖包的每一張，AI 和搜源幾乎都認不出來（漫畫頁、真人照片），
一張一張標作者、角色太累，所以一直卡在「缺創作者／缺角色」。
作品集把它們當一個整體：
- **整本的標籤**（作者／社團、角色、作品、自訂）套到每一頁，之後加進來的頁也自動套上；拿掉整本的標籤，
  只拿掉「因為作品集才加上的」那些（你自己在單頁上標的不動，你否決過的也不會被蓋掉）
- 頁序：智慧排序（分本、集數、檔名、手機照片編號，見 plan()），也可以換排序方式或拖曳自己調
- 資料夾建議：有很多圖、檔名連號的資料夾（不是「2026-07」這種按月份的）列出來，一鍵建立；
  E-Hentai 式的資料夾名「(C104) [社團 (作者)] 標題 (作品) [中國翻訳]」會拆出作者和作品當建議
- 每個動作都能撤銷（整本的快照：資訊、頁、標籤）
"""
from __future__ import annotations

import json
import ntpath
import os
import re
import time

from . import db

KINDS = {"doujin": "同人本", "cos": "cos 圖包", "artbook": "畫集", "comic": "漫畫", "other": "其他"}
SRC = "album"          # media_tags.source：因為作品集才加上的


def _natkey(p: str):
    b = ntpath.basename(p).lower()
    return [int(t) if t.isdigit() else t for t in re.split(r"(\d+)", b)]


# ---------- 頁序 ----------
# 一本作品集的圖可能來自：
# - 一個「書」資料夾（E-Hentai 匯入、下載的同人本）：照檔名自然排序（1、2…10）
# - 好幾個書資料夾（同一系列好幾本合成一本）：先分本、本照集數排（1、2、3、完），每本裡再照檔名
# - 手機照片（圖庫\2025-02 這種按月份的資料夾）：資料夾不代表什麼，照 IMG_xxxx 編號；
#   iPhone 編號到 9999 會回到 0001 → 找編號裡最大的空缺當起點（IMG_9169…9407 再接 IMG_0257…）
_GALLERY_ID = re.compile(r"\s*\[(?:eh|nh)(\d+)\]\s*$", re.I)
_IMG_NO = re.compile(r"^([A-Za-z_]*?)[_-]?(\d{4})(?:\s*\(\d+\))?\.\w+$")
_FINAL = re.compile(r"(完結?|最終|最终|大結局|大结局|final|end\b|fin\b|後編|后编|後篇|后篇|下[卷篇編编]?$)", re.I)
_EXTRA = re.compile(r"(番外|特典|extra|omake|おまけ|附錄|附录)", re.I)
_CN = {"一": 1, "二": 2, "兩": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}
_FIRST = re.compile(r"(前編|前编|前篇|上[卷篇編编]?$)")


def _dir(p: str) -> str:
    return ntpath.dirname(p) if "\\" in p else os.path.dirname(p)


def _base(p: str) -> str:
    return ntpath.basename(p) if "\\" in p else os.path.basename(p)


def _bookish(folder: str, roots: set) -> bool:
    """這個資料夾是「一本書」嗎？按月份的、下載、匯入這種雜物資料夾不是。"""
    b = _base(folder)
    return bool(b) and not MONTH_DIR.match(b) and b.lower() not in GENERIC and \
        os.path.normcase(os.path.normpath(folder)) not in roots


def book_label(folder: str) -> str:
    return _GALLERY_ID.sub("", _base(folder)).strip() or _base(folder)


def _cn_num(t: str):
    if t in _CN:
        return _CN[t]
    if len(t) == 2 and t[0] == "十" and t[1] in _CN:
        return 10 + _CN[t[1]]
    if len(t) == 2 and t[1] == "十" and t[0] in _CN:
        return _CN[t[0]] * 10
    if len(t) == 3 and t[1] == "十":
        return _CN.get(t[0], 0) * 10 + _CN.get(t[2], 0)
    return None


def _volume(rest: str):
    """書名去掉共同的開頭以後，剩下的部分是第幾集。回傳 (排序值, 說明)。"""
    r = rest.strip(" -_.:：—~～")
    m = re.search(r"\d+", r)
    if m:
        return float(m.group()), m.group()
    m = re.search(r"第([一二兩两三四五六七八九十]{1,3})", r) or re.match(r"([一二兩两三四五六七八九十]{1,3})(?:[卷集部話话章篇巻]|\s|$)", r)
    if m and _cn_num(m.group(1)):
        return float(_cn_num(m.group(1))), m.group(1)
    if not r:
        return 1.0, ""
    if _EXTRA.search(r):
        return 1e6 + 1, "番外"
    if _FINAL.search(r):
        return 1e6, "完"
    if _FIRST.search(r):
        return 1.0, "上"
    return 1e5, ""          # 看不出是第幾集：排在有編號的後面、完結篇前面，同樣看不出的照上傳順序


def _photo_order(items: list) -> list:
    """沒有「書」資料夾的圖（手機照片）：IMG_xxxx 編號（處理 9999→0001），其他的插在時間最接近的那張後面。"""
    numbered, other = [], []
    for it in items:
        m = _IMG_NO.match(_base(it["path"]))
        (numbered if m else other).append((int(m.group(2)), m.group(1).lower(), it) if m else it)
    if len(numbered) < max(2, len(items) // 2):      # 大多不是手機編號：照檔名
        return sorted(items, key=lambda x: (_natkey(x["path"]), x["id"]))
    numbered.sort(key=lambda x: (x[0], x[1], x[2]["id"]))
    nums = [n for n, _, _ in numbered]
    # 繞一圈的最大空缺之後是起點
    gaps = [((nums[(i + 1) % len(nums)] - nums[i]) % 10000, i) for i in range(len(nums))]
    gap, at = max(gaps)
    start = (at + 1) % len(nums) if gap > 0 else 0
    seq = [x[2] for x in numbered[start:] + numbered[:start]]
    for it in sorted(other, key=lambda x: (x.get("mtime") or 0, _natkey(x["path"]))):
        t = it.get("mtime") or 0
        best = min(range(len(seq)), key=lambda i: (abs((seq[i].get("mtime") or 0) - t), -i))
        seq.insert(best + 1, it)
    return seq


def plan(rows: list[dict], roots: set | None = None) -> dict:
    """智慧頁序。rows：[{id, path, mtime}]。回傳 {order: [id…], books: [{folder, label, vol, n, first}]}。"""
    if roots is None:
        roots = {os.path.normcase(os.path.normpath(r)) for r in db.get_settings().get("roots", [])}
    groups: dict[str, list] = {}
    loose = []
    for r in rows:
        f = _dir(r["path"])
        (groups.setdefault(f, []) if _bookish(f, roots) else loose).append(r)
    labels = {f: book_label(f) for f in groups}
    # 共同開頭（字元）：《兽X王X兽》、《兽X王X兽》2 庭院野花 … → 剩下「」「2 庭院野花」
    pre = os.path.commonprefix(list(labels.values())) if len(labels) > 1 else ""
    def key(f):
        vol, _ = _volume(labels[f][len(pre):])
        g = _GALLERY_ID.search(_base(f))
        return (vol, int(g.group(1)) if g else 0, _natkey(labels[f]))
    books = []
    order = []
    for f in sorted(groups, key=key):
        pages = sorted(groups[f], key=lambda x: (_natkey(x["path"]), x["id"]))
        books.append({"folder": f, "label": labels[f], "vol": _volume(labels[f][len(pre):])[1] if pre else "",
                      "n": len(pages), "first": pages[0]["id"]})
        order += [p["id"] for p in pages]
    if loose:
        lp = _photo_order(loose)
        if groups:
            books.append({"folder": None, "label": "其他（不在書資料夾裡的圖）", "vol": "", "n": len(lp), "first": lp[0]["id"]})
        order += [p["id"] for p in lp]
    return {"order": order, "books": books if len(books) > 1 else []}


SORTS = {"smart": "智慧排序", "name": "依檔名", "taken": "依拍攝時間", "added": "依加入時間", "reverse": "整本倒過來"}


def _rows(c, aid: int) -> list[dict]:
    return [dict(r) for r in c.execute("""SELECT m.id, m.path, m.mtime, m.taken_at, m.added_at, ai.pos FROM album_items ai
            JOIN media m ON m.id=ai.media_id WHERE ai.album_id=? ORDER BY ai.pos, m.id""", (aid,))]


def sorted_ids(c, aid: int, by: str) -> list[int]:
    rows = _rows(c, aid)
    if by == "smart":
        return plan(rows)["order"]
    if by == "name":
        return [r["id"] for r in sorted(rows, key=lambda r: (_natkey(r["path"]), r["id"]))]
    if by == "taken":
        return [r["id"] for r in sorted(rows, key=lambda r: (r["taken_at"] or r["mtime"] or 0, _natkey(r["path"]), r["id"]))]
    if by == "added":
        return [r["id"] for r in sorted(rows, key=lambda r: (r["added_at"] or 0, r["id"]))]
    if by == "reverse":
        return [r["id"] for r in reversed(rows)]
    raise ValueError(by)


def sort_album(aid: int, by: str) -> int:
    """依 by 重新排頁序（可撤銷）。回傳換了位置的頁數。"""
    c = db.conn()
    if not c.execute("SELECT 1 FROM albums WHERE id=?", (aid,)).fetchone():
        raise KeyError(aid)
    before = [r["id"] for r in _rows(c, aid)]
    ids = sorted_ids(c, aid, by)
    moved = sum(1 for a, b in zip(before, ids) if a != b)
    if moved:
        reorder(aid, ids, label=SORTS.get(by, by))
    return moved


def books(aid: int) -> dict:
    """這本裡有哪幾本（書資料夾），以及現在的頁序是不是一本一本連著（才畫分隔）。"""
    c = db.conn()
    rows = _rows(c, aid)
    p = plan(rows)
    if not p["books"]:
        return {"books": [], "sec": {}, "contiguous": True}
    roots = {os.path.normcase(os.path.normpath(r)) for r in db.get_settings().get("roots", [])}
    idx = {b["folder"]: i for i, b in enumerate(p["books"])}
    loose_i = idx.get(None)
    sec = {}
    for r in rows:
        f = _dir(r["path"])
        sec[r["id"]] = idx.get(f, loose_i) if _bookish(f, roots) else loose_i
    seq = [sec[r["id"]] for r in rows]
    runs = 1 + sum(1 for a, b in zip(seq, seq[1:]) if a != b) if seq else 0
    _post_info(c, p["books"], rows)
    return {"books": p["books"], "sec": sec, "contiguous": runs == len(p["books"]), "smart": [r["id"] for r in rows] == p["order"]}


def _post_info(c, books_: list[dict], rows: list[dict]):
    """從網站匯入的貼文（Pixiv 多頁…）：每一段補上貼文的標題、日期、內文、網址（分段標題、「內文」按鈕用）。"""
    first = {b["first"]: b for b in books_ if b.get("folder")}
    path = {r["id"]: r["path"] for r in rows}
    for mid, b in first.items():
        r = c.execute("SELECT post_url, data FROM import_meta WHERE path=?", (os.path.normpath(path.get(mid) or ""),)).fetchone()
        if not r:
            continue
        try:
            d = json.loads(r[1] or "{}")
        except ValueError:
            d = {}
        if d.get("title") or d.get("text"):
            b["post"] = {"title": d.get("title"), "published": d.get("published"), "text": d.get("text") or "",
                         "url": r[0], "fee": d.get("fee"), "tags": d.get("tags") or []}


# ---------- 快照（撤銷用） ----------
def snapshot(c, aid: int) -> dict:
    a = c.execute("SELECT * FROM albums WHERE id=?", (aid,)).fetchone()
    return {"id": aid, "row": dict(a) if a else None,
            "items": [[r[0], r[1]] for r in c.execute("SELECT media_id, pos FROM album_items WHERE album_id=?", (aid,))],
            "tags": [r[0] for r in c.execute("SELECT tag_id FROM album_tags WHERE album_id=?", (aid,))]}


def restore(c, snap: dict):
    aid = snap["id"]
    c.execute("DELETE FROM album_items WHERE album_id=?", (aid,))
    c.execute("DELETE FROM album_tags WHERE album_id=?", (aid,))
    c.execute("DELETE FROM albums WHERE id=?", (aid,))
    if snap["row"] is None:
        return
    row = snap["row"]
    cols = list(row)
    c.execute(f"INSERT INTO albums({','.join(cols)}) VALUES({','.join('?' * len(cols))})", [row[k] for k in cols])
    alive = {r[0] for r in c.execute(f"SELECT id FROM media WHERE id IN ({','.join(str(int(m)) for m, _ in snap['items']) or 'NULL'})")}
    c.executemany("INSERT OR IGNORE INTO album_items(album_id, media_id, pos) VALUES(?,?,?)",
                  [(aid, m, p) for m, p in snap["items"] if m in alive])
    tl = {r[0] for r in c.execute(f"SELECT id FROM tags WHERE id IN ({','.join(str(int(t)) for t in snap['tags']) or 'NULL'})")}
    c.executemany("INSERT OR IGNORE INTO album_tags(album_id, tag_id) VALUES(?,?)", [(aid, t) for t in snap["tags"] if t in tl])


# ---------- 套用整本的標籤 ----------


def _apply_tags(c, mids: list[int], tids: list[int]) -> list:
    """把標籤加到這幾頁（已確認的、你否決過的都不動）。回傳撤銷用的 tag_rows。"""
    rows = []
    for tid in tids:
        if not mids:
            break
        rows += db.snapshot_tag_rows(mids, tid)
        c.executemany("""INSERT INTO media_tags(media_id, tag_id, score, source, state) VALUES(?,?,NULL,'album',1)
            ON CONFLICT(media_id, tag_id) DO UPDATE SET state=1, source='album' WHERE media_tags.state=0""", [(m, tid) for m in mids])
    return rows


def _unapply_tags(c, aid: int, mids: list[int], tids: list[int]) -> list:
    """拿掉「因為這本作品集才加上的」標籤；同一頁如果還在別的作品集、而那本也有這個標籤，就留著。"""
    rows = []
    for tid in tids:
        if not mids:
            break
        keep = {r[0] for r in c.execute(f"""SELECT ai.media_id FROM album_items ai JOIN album_tags at ON at.album_id=ai.album_id
            WHERE at.tag_id=? AND ai.album_id<>? AND ai.media_id IN ({','.join('?' * len(mids))})""", [tid, aid, *mids])}
        ms = [m for m in mids if m not in keep]
        if not ms:
            continue
        rows += db.snapshot_tag_rows(ms, tid)
        c.executemany("DELETE FROM media_tags WHERE media_id=? AND tag_id=? AND source='album' AND state=1", [(m, tid) for m in ms])
    return rows


def _tid(name: str, category: str | None):
    name = name.strip().replace(" ", "_")
    if not name:
        return None
    return db.tag_id(name, category or "custom")


# ---------- 隔絕（使用者要求 2026-10-05） ----------
# 作品集的圖：不自動推角色／作者／作品（candidates.add 擋）、不自動搜源／ascii2d／E-Hentai／畫師比對／PixAI、
# 不在未識別的待辦裡（query.MISSING）。一般描述標籤、NSFW 分級、縮圖照常（WD 標記本身不停：搜尋、同圖判斷要用）。
# 整本的標籤（source album）和你手動加的照常。
AUTO_KEEP = ("album", "manual")


def clear_auto_candidates(c, mids: list[int]) -> list:
    """拿掉這些圖還沒確認的自動身分候選（需在 write_lock 內）。回傳撤銷用的 tag_rows。已確認、否決的不動。"""
    rows = []
    for i in range(0, len(mids), 500):
        ch = [int(x) for x in mids[i:i + 500]]
        ph = ",".join("?" * len(ch))
        q = f"""FROM media_tags WHERE media_id IN ({ph}) AND state=0 AND source NOT IN ('album','manual')
                AND tag_id IN (SELECT id FROM tags WHERE category IN ('character','artist','copyright'))"""
        rows += [[r[0], r[1], [r[2], r[3], r[4], r[5], r[6]]] for r in c.execute(
            f"SELECT media_id, tag_id, score, source, state, tier, reason {q}", ch)]
        c.execute(f"DELETE {q}", ch)
    return rows


# ---------- 動作（全部可撤銷） ----------
def create(title: str, ids: list[int], kind: str = "other", tags: list[dict] | None = None, folder: str | None = None,
           source_url: str | None = None, note: str | None = None, keep_order: bool = False) -> int:
    """keep_order：照 ids 的順序；預設智慧排序。"""
    c = db.conn()
    ids = list(dict.fromkeys(int(i) for i in ids))
    paths = {r[0]: r[1] for r in c.execute(f"SELECT id, path FROM media WHERE id IN ({','.join('?' * len(ids)) or 'NULL'})", ids)}
    ids = [i for i in ids if i in paths]
    # 頁序一律用智慧排序（以前沒有資料夾時照你選的順序，手機照片就會亂跳：IMG_8391、8388、8387…）
    mt = {r[0]: r[1] for r in c.execute(f"SELECT id, mtime FROM media WHERE id IN ({','.join('?' * len(ids)) or 'NULL'})", ids)}
    order = ids if keep_order else plan([{"id": i, "path": paths[i], "mtime": mt.get(i)} for i in ids])["order"] if ids else []
    with db.write_lock:
        cur = c.execute("INSERT INTO albums(title, kind, cover, note, source_url, folder, created_at) VALUES(?,?,?,?,?,?,?)",
                        (title.strip() or "未命名作品集", kind if kind in KINDS else "other", order[0] if order else None,
                         note, source_url, folder, time.time()))
        aid = cur.lastrowid
        c.executemany("INSERT INTO album_items(album_id, media_id, pos) VALUES(?,?,?)", [(aid, m, i) for i, m in enumerate(order)])
        tids = [t for t in (_tid(x.get("name", ""), x.get("category")) for x in (tags or [])) if t]
        c.executemany("INSERT OR IGNORE INTO album_tags(album_id, tag_id) VALUES(?,?)", [(aid, t) for t in tids])
        rows = _apply_tags(c, order, tids)
        rows += clear_auto_candidates(c, order)          # 隔絕：這些圖上還沒確認的自動候選拿掉
        db.push_action(f"建立本子「{title}」（{len(order)} 張）", {"tag_rows": rows, "album": {"id": aid, "row": None, "items": [], "tags": []}})
        c.commit()
    return aid


def add_items(aid: int, ids: list[int]) -> int:
    c = db.conn()
    with db.write_lock:
        snap = snapshot(c, aid)
        if snap["row"] is None:
            raise KeyError(aid)
        have = {m for m, _ in snap["items"]}
        new = [i for i in dict.fromkeys(int(x) for x in ids) if i not in have]
        new = [r[0] for r in c.execute(f"SELECT id FROM media WHERE id IN ({','.join('?' * len(new)) or 'NULL'})", new)]
        if not new:
            return 0          # 都已經在裡面：不留一筆「加入 0 張」的撤銷紀錄（不然 Ctrl Z 撤到的是這個空動作）
        was_smart = bool(snap["items"]) and [r["id"] for r in _rows(c, aid)] == plan(_rows(c, aid))["order"]
        start = max([p for _, p in snap["items"]] or [-1]) + 1
        c.executemany("INSERT INTO album_items(album_id, media_id, pos) VALUES(?,?,?)", [(aid, m, start + i) for i, m in enumerate(new)])
        if new and (was_smart or not snap["items"]):
            # 原本就是智慧排序（例如再匯入同系列的下一本）→ 新的頁放到該在的位置，不是一律接在最後
            c.executemany("UPDATE album_items SET pos=? WHERE album_id=? AND media_id=?",
                          [(i, aid, m) for i, m in enumerate(plan(_rows(c, aid))["order"])])
        rows = _apply_tags(c, new, snap["tags"])
        rows += clear_auto_candidates(c, new)
        if not snap["row"]["cover"] and new:
            c.execute("UPDATE albums SET cover=? WHERE id=?", (new[0], aid))
        db.push_action(f"加入本子「{snap['row']['title']}」（{len(new)} 張）", {"tag_rows": rows, "album": snap})
        c.commit()
    return len(new)


def sync_items(aid: int, ordered: list[int], label: str) -> int:
    """從網站自動整理進來的本子：補上還沒在裡面的頁。
    頁序：原本就是照 ordered 的順序（你沒手動改過）→ 整本照 ordered 重排；改過 → 新的接在最後。
    整本的標籤套到新頁、自動候選拿掉（隔絕）。不記撤銷（跟匯入本身一樣），記操作紀錄。回傳新加幾頁。"""
    c = db.conn()
    with db.write_lock:
        snap = snapshot(c, aid)
        if snap["row"] is None:
            raise KeyError(aid)
        cur = [m for m, _ in sorted(snap["items"], key=lambda x: (x[1], x[0]))]
        have = set(cur)
        ok = {r[0] for r in c.execute(f"SELECT id FROM media WHERE id IN ({','.join('?' * len(ordered)) or 'NULL'})", ordered)}
        ordered = [m for m in dict.fromkeys(ordered) if m in ok]
        new = [m for m in ordered if m not in have]
        if not new:
            return 0
        pos_in = {m: i for i, m in enumerate(ordered)}
        untouched = [m for m in cur if m in pos_in] == sorted([m for m in cur if m in pos_in], key=pos_in.get) and \
            all(m in pos_in for m in cur)
        full = ordered if untouched else cur + new
        c.executemany("INSERT OR IGNORE INTO album_items(album_id, media_id, pos) VALUES(?,?,?)", [(aid, m, 0) for m in new])
        c.executemany("UPDATE album_items SET pos=? WHERE album_id=? AND media_id=?", [(i, aid, m) for i, m in enumerate(full)])
        _apply_tags(c, new, snap["tags"])
        clear_auto_candidates(c, new)
        if not snap["row"]["cover"]:
            c.execute("UPDATE albums SET cover=? WHERE id=?", (full[0], aid))
        c.commit()
    db.log_op(f"{label}：本子「{snap['row']['title']}」加入 {len(new)} 張", new[:50], keys="album_sync")
    return len(new)


def remove_items(aid: int, ids: list[int]) -> int:
    c = db.conn()
    with db.write_lock:
        snap = snapshot(c, aid)
        if snap["row"] is None:
            raise KeyError(aid)
        have = {m for m, _ in snap["items"]}
        ids = [int(i) for i in ids if int(i) in have]
        c.executemany("DELETE FROM album_items WHERE album_id=? AND media_id=?", [(aid, m) for m in ids])
        rows = _unapply_tags(c, aid, ids, snap["tags"])
        if snap["row"]["cover"] in ids:
            first = c.execute("SELECT media_id FROM album_items WHERE album_id=? ORDER BY pos LIMIT 1", (aid,)).fetchone()
            c.execute("UPDATE albums SET cover=? WHERE id=?", (first[0] if first else None, aid))
        db.push_action(f"從本子「{snap['row']['title']}」移出 {len(ids)} 張", {"tag_rows": rows, "album": snap})
        c.commit()
    return len(ids)


def set_tag(aid: int, name: str, category: str | None, on: bool) -> dict:
    c = db.conn()
    with db.write_lock:
        snap = snapshot(c, aid)
        if snap["row"] is None:
            raise KeyError(aid)
        tid = _tid(name, category) if on else db.tag_id(name.strip().replace(" ", "_"), create=False)
        if not tid:
            return {"ok": False}
        mids = [m for m, _ in sorted(snap["items"], key=lambda x: x[1])]
        if on:
            c.execute("INSERT OR IGNORE INTO album_tags(album_id, tag_id) VALUES(?,?)", (aid, tid))
            rows = _apply_tags(c, mids, [tid])
        else:
            c.execute("DELETE FROM album_tags WHERE album_id=? AND tag_id=?", (aid, tid))
            rows = _unapply_tags(c, aid, mids, [tid])
        db.push_action(f"本子「{snap['row']['title']}」{'加上' if on else '拿掉'} {name}（{len(mids)} 張）", {"tag_rows": rows, "album": snap})
        c.commit()
    return {"ok": True, "n": len(mids)}


def update(aid: int, **kw) -> None:
    c = db.conn()
    allowed = {k: v for k, v in kw.items() if k in ("title", "kind", "cover", "note", "source_url") and v is not None}
    if "kind" in allowed and allowed["kind"] not in KINDS:
        allowed.pop("kind")
    if not allowed:
        return
    with db.write_lock:
        snap = snapshot(c, aid)
        if snap["row"] is None:
            raise KeyError(aid)
        c.execute(f"UPDATE albums SET {', '.join(f'{k}=?' for k in allowed)} WHERE id=?", [*allowed.values(), aid])
        db.push_action(f"修改本子「{snap['row']['title']}」", {"album": snap})
        c.commit()


def reorder(aid: int, ids: list[int], label: str | None = None) -> None:
    """照 ids 的順序排頁。不在這本的忽略；沒列到的頁照原本順序接在後面（只傳一部分也不會跟別頁撞號）。"""
    c = db.conn()
    with db.write_lock:
        snap = snapshot(c, aid)
        if snap["row"] is None:
            raise KeyError(aid)
        have = [m for m, _ in sorted(snap["items"], key=lambda x: (x[1], x[0]))]
        hs = set(have)
        want = [m for m in dict.fromkeys(int(x) for x in ids) if m in hs]
        ws = set(want)
        full = want + [m for m in have if m not in ws]
        c.executemany("UPDATE album_items SET pos=? WHERE album_id=? AND media_id=?", [(i, aid, m) for i, m in enumerate(full)])
        db.push_action(f"本子「{snap['row']['title']}」頁序{('：' + label) if label else ''}", {"album": snap})
        c.commit()


def sort_by_name(aid: int) -> None:
    sort_album(aid, "name")


def delete(aid: int, untag: bool = True) -> None:
    """解散作品集（圖本身不動）。untag：一併拿掉因為這本才加上的標籤。"""
    c = db.conn()
    with db.write_lock:
        snap = snapshot(c, aid)
        if snap["row"] is None:
            raise KeyError(aid)
        mids = [m for m, _ in snap["items"]]
        rows = _unapply_tags(c, aid, mids, snap["tags"]) if untag else []
        c.execute("DELETE FROM albums WHERE id=?", (aid,))
        db.push_action(f"解散本子「{snap['row']['title']}」", {"tag_rows": rows, "album": snap})
        c.commit()


# ---------- 讀取 ----------
def _tags_of(c, aid: int, lang: str) -> list[dict]:
    from . import names
    out = []
    for r in c.execute("""SELECT t.name, t.category, ti.other_names, ti.display_name FROM album_tags at JOIN tags t ON t.id=at.tag_id
            LEFT JOIN tag_info ti ON ti.tag_id=t.id WHERE at.album_id=? ORDER BY CASE t.category WHEN 'artist' THEN 0
            WHEN 'character' THEN 1 WHEN 'copyright' THEN 2 ELSE 3 END, t.name""", (aid,)):
        on = json.loads(r[2] or "[]")
        disp = (names.series_display(r[0], on, r[3], lang) if r[1] == "copyright" else
                names.display(r[0], on, r[3], lang) if r[1] in ("character", "artist") else r[0])
        out.append({"name": r[0], "category": r[1], "display": disp})
    return out


def listing(nsfw: str = "hide") -> list[dict]:
    from .query import NSFW
    c = db.conn()
    lang = db.get_settings()["display_lang"]
    nf = f" AND {NSFW}=0" if nsfw == "hide" else ""
    out = []
    for a in c.execute("SELECT * FROM albums ORDER BY created_at DESC").fetchall():
        n, nn = c.execute(f"""SELECT COUNT(*), COALESCE(SUM({NSFW}),0) FROM album_items ai JOIN media m ON m.id=ai.media_id
            WHERE ai.album_id=? AND m.status='done'""", (a["id"],)).fetchone()
        cover = a["cover"]
        # 封面要是這本裡、還在的圖（b99.4：圖被清掉、丟回收筒、移出這本，或 NSFW 隱藏時）→ 不然用第一頁
        if not (cover and c.execute(f"""SELECT 1 FROM album_items ai JOIN media m ON m.id=ai.media_id
                WHERE ai.album_id=? AND ai.media_id=? AND m.status='done'{nf}""", (a["id"], cover)).fetchone()):
            cover = (c.execute(f"""SELECT ai.media_id FROM album_items ai JOIN media m ON m.id=ai.media_id WHERE ai.album_id=?
                AND m.status='done'{nf} ORDER BY ai.pos LIMIT 1""", (a["id"],)).fetchone() or [None])[0]
        out.append({**dict(a), "cover": cover, "n": n, "nsfw_n": nn, "kind_label": KINDS.get(a["kind"] or "other", "其他"),
                    "tags": _tags_of(c, a["id"], lang)})
    return out


def detail(aid: int) -> dict | None:
    c = db.conn()
    a = c.execute("SELECT * FROM albums WHERE id=?", (aid,)).fetchone()
    if not a:
        return None
    lang = db.get_settings()["display_lang"]
    n = c.execute("SELECT COUNT(*) FROM album_items WHERE album_id=?", (aid,)).fetchone()[0]
    b = books(aid)
    return {**dict(a), "n": n, "kind_label": KINDS.get(a["kind"] or "other", "其他"), "tags": _tags_of(c, aid, lang), "kinds": KINDS,
            "books": [{k: v for k, v in x.items() if k != "folder"} for x in b["books"]], "books_contiguous": b["contiguous"],
            "is_smart": b.get("smart", [r["id"] for r in _rows(c, aid)] == plan(_rows(c, aid))["order"]), "sorts": SORTS}


def pages(aid: int) -> list[dict]:
    """手動調頁序用：全部的頁（含 NSFW 標記、在第幾本）。"""
    from .query import NSFW
    c = db.conn()
    if not c.execute("SELECT 1 FROM albums WHERE id=?", (aid,)).fetchone():
        raise KeyError(aid)
    sec = books(aid)["sec"]
    return [{"id": r[0], "name": _base(r[1]), "nsfw": r[2], "kind": r[3], "sec": sec.get(r[0])} for r in c.execute(
        f"""SELECT m.id, m.path, {NSFW}, m.kind FROM album_items ai JOIN media m ON m.id=ai.media_id
            WHERE ai.album_id=? ORDER BY ai.pos, m.id""", (aid,))]


def preview(aid: int, by: str) -> dict:
    """排序前先看：會分成哪幾本、前幾頁是什麼、會動到幾頁。"""
    c = db.conn()
    if not c.execute("SELECT 1 FROM albums WHERE id=?", (aid,)).fetchone():
        raise KeyError(aid)
    rows = _rows(c, aid)
    name = {r["id"]: _base(r["path"]) for r in rows}
    ids = sorted_ids(c, aid, by)
    out = {"by": by, "moved": sum(1 for a, b in zip([r["id"] for r in rows], ids) if a != b), "first": [name[i] for i in ids[:6]]}
    if by == "smart":
        out["books"] = [{k: v for k, v in x.items() if k != "folder"} for x in plan(rows)["books"]]
    return out


def albums_of(mid: int) -> list[dict]:
    return [{"id": r[0], "title": r[1], "page": r[2]} for r in db.conn().execute(
        """SELECT a.id, a.title, (SELECT COUNT(*) FROM album_items x WHERE x.album_id=a.id AND x.pos <= ai.pos)
           FROM album_items ai JOIN albums a ON a.id=ai.album_id WHERE ai.media_id=?""", (mid,))]


# ---------- 資料夾建議、資料夾名稱解析 ----------
MONTH_DIR = re.compile(r"^(\d{4}[-_. ]?\d{1,2}|\d{4}|\d{6,8})$")
GENERIC = {"downloads", "下載", "pictures", "圖片", "desktop", "桌面", "images", "img", "photos",
           "danbooru", "pixiv", "x", "twitter", "e621", "rule34", "匯入", "import", "screenshots", "螢幕擷取畫面", "civitai"}
EH = re.compile(r"^(?:\d+[-_ ])?(?:\((?P<event>[^)]+)\)\s*)?\[(?P<circle>[^\]\(]+?)\s*(?:\((?P<artist>[^)]+)\))?\]\s*"
                r"(?P<title>.+?)\s*(?:\((?P<parody>[^)]+)\))?\s*(?:\[[^\]]*\]\s*)*$")


def parse_name(name: str) -> dict:
    """E-Hentai／nhentai 式的資料夾名：(C104) [社團 (作者)] 標題 (作品) [中國翻訳] → 各部分。不符合就只有標題。"""
    m = EH.match(name.strip())
    if not m:
        return {"title": name.strip()}
    d = {k: (v or "").strip() for k, v in m.groupdict().items()}
    return {k: v for k, v in d.items() if v}


def suggest_tags(name: str) -> list[dict]:
    """從資料夾名找得到的作者／作品（只建議你圖庫裡已經有、或名字很明確的；由你勾選）。"""
    from . import nameindex
    p = parse_name(name)
    out = []
    for field, cat in (("artist", "artist"), ("circle", "artist"), ("parody", "copyright")):
        v = p.get(field)
        if not v:
            continue
        hit = None
        for tid in nameindex.exact(v):
            r = db.conn().execute("SELECT name, category FROM tags WHERE id=?", (tid,)).fetchone()
            if r and r[1] == cat:
                hit = r[0]
                break
        # 圖庫裡已經有的預設勾選；沒有的列出來（會建立新標籤），要你自己勾
        disp = v
        if hit:
            from . import names
            r = db.conn().execute("SELECT ti.other_names, ti.display_name FROM tags t LEFT JOIN tag_info ti ON ti.tag_id=t.id WHERE t.name=?", (hit,)).fetchone()
            on, dn = (json.loads(r[0] or "[]"), r[1]) if r else ([], None)
            disp = names.series_display(hit, on, dn, db.get_settings()["display_lang"]) if cat == "copyright" else names.display(hit, on, dn, db.get_settings()["display_lang"])
        out.append({"name": hit or v.replace(" ", "_").lower(), "display": disp, "category": cat, "known": bool(hit), "from": field,
                    "raw": v, "checked": bool(hit)})
    return out


def folder_suggestions(min_n: int = 6, limit: int = 30) -> list[dict]:
    """可能是一整本／一整套的資料夾：圖夠多、不是按月份分的、不是下載／匯入這種雜物資料夾、還沒建成作品集。"""
    c = db.conn()
    roots = {os.path.normcase(os.path.normpath(r)) for r in db.get_settings().get("roots", [])}
    in_album = {r[0] for r in c.execute("SELECT media_id FROM album_items")}
    made = {r[0] for r in c.execute("SELECT folder FROM albums WHERE folder IS NOT NULL")}
    groups: dict[str, list] = {}
    for mid, path in c.execute("SELECT id, path FROM media WHERE status='done' AND kind='image' AND COALESCE(excluded,0)=0"):
        groups.setdefault(ntpath.dirname(path) if "\\" in path else os.path.dirname(path), []).append((mid, path))
    out = []
    for d, items in groups.items():
        base = ntpath.basename(d) if "\\" in d else os.path.basename(d)
        if len(items) < min_n or d in made or os.path.normcase(os.path.normpath(d)) in roots:
            continue
        if MONTH_DIR.match(base) or base.lower() in GENERIC or len(items) > 2000:
            continue
        free = [x for x in items if x[0] not in in_album]
        if len(free) < min_n:
            continue
        free.sort(key=lambda x: _natkey(x[1]))
        p = parse_name(base)
        out.append({"folder": d, "name": base, "title": p.get("title", base), "n": len(free), "cover": free[0][0],
                    "ids": [x[0] for x in free], "parsed": p, "tags": suggest_tags(base)})
    out.sort(key=lambda x: -x["n"])
    return out[:limit]
