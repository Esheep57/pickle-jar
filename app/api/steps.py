"""設定 → 自動標記與搜源：每一步的設定、準確度參考，以及「補跑」（對舊圖重跑某幾步）。"""
from __future__ import annotations


from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .. import db, steps

router = APIRouter()

# 補跑的選項（順序＝畫面上的順序）：key → (名稱, 說明, 只做圖片)
RERUN = {
    "wd": ("WD Tagger 重新標記", "描述標籤、分級、角色候選；你確認、否決、手動加的不會動", False),
    "pixai": ("PixAI", "重新跑第二個 AI 模型", True),
    "aimeta": ("AI 生成提示詞", "重新讀圖裡的生成資料", True),
    "sig": ("圖上的簽名", "只讀 AI 判斷有簽名、還沒有創作者的圖", True),
    "source": ("Danbooru 搜源", "原檔、像素指紋、以圖搜圖、Pixiv；找不到時照設定接著做其他網站", True),
    "r34": ("Rule34 原檔", "只送檔案指紋", True),
    "ascii2d": ("排進 ascii2d", "加在 ascii2d 半自動佇列的後面", True),
}


@router.get("/api/steps")
def get_steps(stats: bool = True):
    d = steps.describe(with_stats=stats)
    d["rerun"] = [{"key": k, "name": n, "desc": ds} for k, (n, ds, _) in RERUN.items()]
    return d


@router.post("/api/steps/reset")
def reset_steps():
    """這一類全部恢復預設（目前實測最穩定的）。"""
    from .system import post_settings
    post_settings(steps.reset_patch())
    return get_steps()


class Range(BaseModel):
    ids: list[int] | None = None
    query: dict | None = None      # 跟 /api/media 一樣的參數（view、q、tag、miss、src、mtype、bucket、album、nsfw、noalbum）


class RerunReq(Range):
    steps: list[str] = []
    dry: bool = False
    resource: bool = False         # Danbooru 搜源：已經找到出處的也重搜


def resolve(r: Range) -> list[int]:
    if r.ids is not None:
        return list(dict.fromkeys(int(i) for i in r.ids))
    q = dict(r.query or {})
    allowed = {"view", "tag", "q", "nsfw", "miss", "src", "mtype", "bucket", "album", "noalbum"}
    q = {k: v for k, v in q.items() if k in allowed and v not in (None, "")}
    q.setdefault("nsfw", "show")
    if "album" in q:
        q["album"] = int(q["album"])
    if "noalbum" in q:
        q["noalbum"] = str(q["noalbum"]).lower() in ("1", "true")
    from .library import list_media
    return list_media(**q, ids_only=True)["ids"]


def _filter(c, ids: list[int], sql: str) -> list[int]:
    out = []
    for i in range(0, len(ids), 800):
        ch = ids[i:i + 800]
        out += [r[0] for r in c.execute(f"SELECT m.id FROM media m WHERE m.id IN ({','.join('?' * len(ch))}) AND {sql}", ch)]
    return out


def _targets(c, key: str, ids: list[int], resource: bool) -> list[int]:
    img = "m.kind='image' AND m.status='done'"
    if key == "wd":
        return _filter(c, ids, "m.status IN ('done','error')")
    if key == "source" and not resource:
        return _filter(c, ids, img + " AND m.id NOT IN (SELECT media_id FROM media_source WHERE status='found')")
    if key == "sig":
        return _filter(c, ids, img + """ AND NOT EXISTS (SELECT 1 FROM media_tags x JOIN tags y ON y.id=x.tag_id
            WHERE x.media_id=m.id AND x.state=1 AND y.category='artist')""")
    return _filter(c, ids, img) if RERUN[key][2] else ids


@router.post("/api/rerun")
def rerun(req: RerunReq):
    bad = [k for k in req.steps if k not in RERUN]
    if bad:
        raise HTTPException(400, f"沒有這一步：{', '.join(bad)}")
    ids = resolve(req)
    c = db.conn()
    s = db.get_settings()
    per = {}
    why = {}
    for k in req.steps:
        per[k] = _targets(c, k, ids, req.resource)
        if k in ("source", "r34") and not s.get("online_enabled", True):
            why[k] = "線上功能關著"
        elif k == "pixai" and not s.get("pixai_enabled"):
            why[k] = "PixAI 沒有開"
        elif k == "r34":
            from .. import r34
            if not r34.configured():
                why[k] = "還沒填 Rule34 key"
    counts = {k: (0 if k in why else len(v)) for k, v in per.items()}
    if req.dry:
        return {"n": len(ids), "per": counts, "why": why}
    from .. import online, pipeline
    jobs = pipeline.get_jobs()
    oj = online.get_jobs()
    queued = {}
    for k, v in per.items():
        if k in why or not v:
            continue
        queued[k] = len(v)
        if k == "wd":
            jobs.submit("retag", v)
        elif k == "pixai":
            jobs.submit("pixai", v)
        elif k == "aimeta":
            from .. import aigen

            def _ai(v=v):
                for i in range(0, len(v), 500):
                    aigen.refresh(v[i:i + 500])
            from .. import tasks
            tasks.spawn("aimeta", "補跑：AI 生成資料", _ai, name="rerun-aimeta")
        elif k == "source":
            oj.queue_sources(v, manual=True)
        elif k == "ascii2d":
            from .. import ascii2d
            ascii2d.start(v, replace=False)
    sets = {k: set(per[k]) for k in ("sig", "r34") if k in per and k not in why}
    online_items = [[k, m] for m in ids for k in sets if m in sets[k]]
    if online_items:
        oj.submit("rerun", online_items, manual=True)
    return {"n": len(ids), "per": counts, "why": why, "queued": queued}
