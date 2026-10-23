"""整篇 Pixiv 作品逐頁標角色（事後修正用）。

Pixiv 的標籤是整篇作品共用的：一篇 14 頁、標了 8 個角色的作品，搜源會把 8 個角色都確認到每一頁上。
很少發生（開發時的圖庫只有一篇這種作品），所以不改自動流程，
只提供一個「發生了以後一次整理完」的表格：每一列一頁、每一欄一個角色，點格子切換是／不是，一次存、一次撤銷。
"""
from __future__ import annotations

import os
import re

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .. import db, names
from ..pixiv import pixiv_id_from_name

router = APIRouter()
_PAGE = re.compile(r"_p(\d+)", re.I)


def _work_of(mid: int) -> int | None:
    c = db.conn()
    r = c.execute("SELECT m.path, s.pixiv_id FROM media m LEFT JOIN media_source s ON s.media_id=m.id WHERE m.id=?", (mid,)).fetchone()
    if not r:
        return None
    return r["pixiv_id"] or pixiv_id_from_name(os.path.basename(r["path"]))


def pages_of(pid: int) -> list[dict]:
    """圖庫裡同一篇 Pixiv 作品的每一頁（搜源記的作品 ID，或檔名 ID_pN）。"""
    c = db.conn()
    ids = {r[0] for r in c.execute("""SELECT m.id FROM media m JOIN media_source s ON s.media_id=m.id
        WHERE s.pixiv_id=? AND m.status='done'""", (pid,))}
    like = [f"%{pid}_p%", f"%{pid}-%_p%"]
    for r in c.execute("SELECT id, path FROM media WHERE status='done' AND (path LIKE ? OR path LIKE ?)", like):
        if pixiv_id_from_name(os.path.basename(r["path"])) == pid:
            ids.add(r["id"])
    out = []
    for r in c.execute(f"SELECT id, path FROM media WHERE id IN ({','.join('?' * len(ids)) or 'NULL'})", list(ids)):
        m = _PAGE.search(os.path.basename(r["path"]))
        out.append({"id": r["id"], "page": int(m.group(1)) if m else None, "name": os.path.basename(r["path"])})
    out.sort(key=lambda x: (x["page"] is None, x["page"] or 0, x["id"]))
    return out


@router.get("/api/pixiv-work/{mid}")
def work(mid: int):
    """這張圖所屬的 Pixiv 作品：每一頁、作品裡出現過的角色、每一格目前的狀態（1 是、-1 不是、0 候選、null 沒有）。"""
    pid = _work_of(mid)
    if not pid:
        raise HTTPException(404, "這張不是 Pixiv 作品（或還不知道是哪一篇）")
    pages = pages_of(pid)
    if not pages:
        raise HTTPException(404, "圖庫裡沒有這篇作品的圖")
    c = db.conn()
    ph = ",".join("?" * len(pages))
    cells, chars = {}, {}
    for r in c.execute(f"""SELECT mt.media_id, t.name, mt.state, mt.source FROM media_tags mt JOIN tags t ON t.id=mt.tag_id
            WHERE t.category='character' AND mt.media_id IN ({ph})""", [p["id"] for p in pages]):
        cells.setdefault(r["media_id"], {})[r["name"]] = r["state"]
        ch = chars.setdefault(r["name"], {"name": r["name"], "pixiv": False, "yes": 0})
        ch["pixiv"] |= r["source"] == "pixiv"
        ch["yes"] += r["state"] == 1
    lang = db.get_settings()["display_lang"]
    cols = sorted(chars.values(), key=lambda x: (-x["yes"], x["name"]))
    for ch in cols:
        ch["display"] = names.display(ch["name"], [], None, lang)
    for p in pages:
        p["cells"] = cells.get(p["id"], {})
    return {"pixiv_id": pid, "url": f"https://www.pixiv.net/artworks/{pid}", "pages": pages, "chars": cols}


class WorkApply(BaseModel):
    changes: list[list]      # [[media_id, 角色名, 1（是）／-1（不是）／0（拿掉）], ...]


@router.post("/api/pixiv-work/apply")
def work_apply(body: WorkApply):
    """一次存表格裡改過的格子（一筆撤銷）。不做否決傳播：同一篇作品的每一頁本來就很像，傳播會誤傷別頁。"""
    from .common import _schedule_rematch
    by_tag: dict[str, list[tuple[int, int]]] = {}
    for ch in body.changes:
        if len(ch) != 3 or ch[2] not in (1, -1, 0):
            raise HTTPException(400, "格式不對")
        by_tag.setdefault(str(ch[1]).strip().replace(" ", "_"), []).append((int(ch[0]), int(ch[2])))
    if not by_tag:
        return {"ok": True, "n": 0}
    rows, touched = [], set()
    with db.write_lock:
        c = db.conn()
        for name, items in by_tag.items():
            tid = db.tag_id(name, "character")
            rows += db.snapshot_tag_rows([m for m, _ in items], tid)
            for mid, st in items:
                touched.add(mid)
                if st == 0:
                    c.execute("DELETE FROM media_tags WHERE media_id=? AND tag_id=?", (mid, tid))
                else:
                    c.execute("""INSERT INTO media_tags(media_id, tag_id, score, source, state) VALUES(?,?,NULL,'manual',?)
                        ON CONFLICT(media_id, tag_id) DO UPDATE SET state=excluded.state""", (mid, tid, st))
        db.push_action(f"整篇貼文逐頁標角色（{len(body.changes)} 格）", {"tag_rows": rows}, touched=touched)
        c.commit()
    from .. import people
    people.refresh_bases()
    _schedule_rematch()
    return {"ok": True, "n": len(body.changes)}
