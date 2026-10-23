"""背景工作面板：正在做什麼、排隊中、排程（什麼時候會自動做）、最近完成。

兩條佇列：本機（掃描、標記…，pipeline.Jobs）和線上（搜源、抓資料…，online.OnlineJobs）。
另外幾個不在佇列裡的背景工作：局部截圖比對、新角色辨識、定期重搜、定期掃描、每日備份。
"""
from __future__ import annotations

import time

from . import db

LABEL = {
    # 本機
    "scan": "掃描資料夾", "retag": "重新標記", "rematch": "套用否決", "sweep": "套用否決", "fingerprint": "補算檔案指紋",
    "archive": "移出 iCloud", "upgrade": "換成高清版", "clip": "媒材 CLIP 特徵", "pixai": "PixAI",
    # 線上
    "taginfo": "抓取角色資料", "source": "以圖搜源", "artistscan": "從畫師反推",
    "r34scan": "Rule34 原檔比對", "seriesparent": "查作品的母系列", "sigscan": "讀圖上的簽名", "pixelhash": "像素指紋比對",
    "seriesname": "查作品譯名", "upgradecheck": "找更高清的版本", "zhname": "找中文名（Bangumi）",
}


def _size(arg):
    if isinstance(arg, (list, tuple, set)):
        return len(arg)
    return None


def _queued(q) -> list[tuple]:
    with q.mutex:
        return list(q.queue)


def overview() -> dict:
    from . import charknn, online, pipeline
    pj, oj = pipeline.get_jobs(), online.get_jobs()
    now = time.time()
    running = []
    ps = pj.state
    if ps.get("running") or ps.get("paused"):
        running.append({"queue": "local", "kind": ps.get("kind"), "label": LABEL.get(ps.get("kind"), ps.get("phase")),
                        "phase": ps.get("phase"), "done": ps.get("done"), "total": ps.get("total"), "eta": ps.get("eta"),
                        "paused": ps.get("paused"), "started": ps.get("started"), "can_pause": True})
    os_ = oj.state
    if os_.get("running"):
        running.append({"queue": "online", "kind": os_.get("kind"), "label": LABEL.get(os_.get("kind"), os_.get("phase")),
                        "phase": os_.get("phase"), "done": os_.get("done"), "total": os_.get("total"), "eta": os_.get("eta"),
                        "found": os_.get("found"), "skipped": os_.get("skipped"), "started": os_.get("started")})
    cs = pipeline.crop_state
    if cs.get("running"):
        running.append({"queue": "bg", "kind": "crop", "label": "局部截圖比對", "done": cs.get("checked"), "total": cs.get("total")})
    if charknn._state.get("running"):
        running.append({"queue": "bg", "kind": "knn", "label": "新角色辨識（重算推薦）"})
    from . import mediaclf, tasks
    if getattr(mediaclf, "_state", {}).get("running"):
        running.append({"queue": "bg", "kind": "mediaclf", "label": "媒材重算"})
    ts = tasks.snapshot()      # 登記過的背景工作（b93：匯入、轉 GIF、匯出、蘊含同步…）
    for t in ts["running"]:
        running.append({"queue": "task", "id": t["id"], "kind": t["kind"], "label": t["label"], "done": t.get("done"),
                        "total": t.get("total"), "phase": t.get("note") or ("排隊中（同時最多 2 個重的工作）" if t["state"] == "waiting" else ""),
                        "started": t["started"], "can_cancel": t["can_cancel"]})

    queued = []
    for i, (kind, arg) in enumerate(_queued(pj.q)):
        queued.append({"queue": "local", "index": i, "kind": kind, "label": LABEL.get(kind, kind), "n": _size(arg)})
    cur = (oj._grp or {}).get("grp")
    PRIO = {0: "你要的，最先做", 1: "", 2: "背景"}
    for i, g in enumerate(x for x in oj.q.groups() if x["grp"] != cur):   # 線上：一組一項，依要做的順序
        queued.append({"queue": "online", "index": i, "kind": g["kind"], "label": LABEL.get(g["kind"], g["kind"]), "n": g["n"],
                       "note": PRIO.get(g["prio"], "")})

    s = db.get_settings()
    sched = []
    h = float(s.get("scan_interval_h") or 0)
    if h > 0 and s.get("roots"):
        last = ps.get("last_scan_timer") or now
        sched.append({"label": "完整掃描資料夾", "every": f"每 {h:g} 小時", "next": last + h * 3600,
                      "note": "監看中，新檔案會馬上處理；這是保險用的完整掃描" if pj.observer else ""})
    b = db.list_backups()
    last_b = max((x["mtime"] for x in b), default=None)
    sched.append({"label": "自動備份資料庫", "every": "每天", "next": (last_b + 86400) if last_b else now, "last": last_b})
    if s.get("online_enabled"):
        rs = online.research_status()
        la = online._last_auto
        base = la.get("at") or la.get("started") or now
        sched.append({"label": "找不到出處的圖重搜", "every": f"每 {rs['days']:g} 天（每 {online.PERIODIC_H:g} 小時檢查）",
                      "next": max(base + online.PERIODIC_H * 3600, rs["next_at"] or 0) if not rs["due"] else base + online.PERIODIC_H * 3600,
                      "note": f"找不到出處 {rs['notfound']:,} 張，現在到期 {rs['due']:,} 張", "last": la.get("at")})
    kl = charknn._state.get("last")
    sched.append({"label": "新角色辨識（你的圖庫推薦）", "every": "確認／否決後約 20 秒、新圖處理完",
                  "next": None, "last": kl.get("at") if kl else None,
                  "note": f"上次：新增 {kl['added']}、收掉 {kl['removed']}、目前推薦 {kl['suggested']}" if kl and "added" in kl else ""})

    hist = []
    for qn, j in (("local", pj), ("online", oj)):
        for h_ in list(j.history):
            hist.append({**h_, "queue": qn, "label": LABEL.get(h_["kind"], h_["kind"])})
    for t in ts["recent"]:
        hist.append({"queue": "task", "kind": t["kind"], "label": t["label"], "started": t["started"], "ended": t["ended"],
                     "error": t["error"], "stopped": t["state"] == "cancelled"})
    hist.sort(key=lambda x: -(x.get("ended") or 0))
    interrupted = [{"label": t["label"], "started": t["started"], "ended": t["ended"]} for t in ts["interrupted"]]
    return {"running": running, "queued": queued, "scheduled": sched, "history": hist[:20], "now": now, "interrupted": interrupted}


def cancel(queue: str, index: int) -> bool:
    """把排隊中的一項拿掉（還沒開始的）。"""
    from . import online, pipeline
    if queue == "task":          # 登記過的背景工作：index＝工作編號
        from . import tasks
        return tasks.cancel(index)
    if queue == "online":
        j = online.get_jobs()
        cur = (j._grp or {}).get("grp")
        gs = [x for x in j.q.groups() if x["grp"] != cur]
        if not (0 <= index < len(gs)):
            return False
        for r in j.q.cancel_group(gs[index]["grp"]):
            j._forget(r["kind"], r["arg"] if isinstance(r["arg"], list) else [])
        return True
    j = pipeline.get_jobs()
    with j.q.mutex:
        items = list(j.q.queue)
        if not (0 <= index < len(items)):
            return False
        kind, arg = items[index]
        del j.q.queue[index]
        j.q.unfinished_tasks = max(0, j.q.unfinished_tasks - 1)
    return True
