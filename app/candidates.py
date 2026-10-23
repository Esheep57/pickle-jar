"""身分標籤（角色 / 創作者 / 作品）的候選管理。

原則：
- 身分標籤只有「已確認」才算數；自動來源一律先進候選（state=0），附上優先序 tier 與理由。
- 優先序：1 Danbooru 原檔／同一作品（直接確認）> 2 Danbooru 相似圖（IQDB）> 3 Pixiv > 4 WD Tagger（及 CCIP）> 5 同一組圖。
- Danbooru 最優先：一張圖只要有 Danbooru 的角色清單，其他來源推薦「清單外」的角色一律不列入候選。
- 否決（「不是 X」）和「停止自動標註」對所有自動來源有效。
"""
from __future__ import annotations

import json
import os
import re
from collections import defaultdict

import numpy as np

from . import db

IDENTITY = ("character", "artist", "copyright")
TIER_NAME = {1: "Danbooru（原檔）", 2: "Danbooru", 3: "Pixiv", 4: "AI 模型"}
T_DANBOORU_EXACT, T_DANBOORU, T_PIXIV, T_AI = 1, 2, 3, 4
# 待確認頁的分頁 → SQL 條件
TIER_FILTER = {"source": "mt.tier<=2", "pixiv": "mt.tier=3 AND mt.source!='aimeta'", "prompt": "mt.source='aimeta'", "library": "mt.source='knn'",
               "ai": "mt.tier>=4 AND mt.source!='knn'"}


def source_characters(mid: int) -> set[str] | None:
    """這張圖的搜源結果中「有 Danbooru 角色清單」時回傳該清單（最可信），否則 None。"""
    r = db.conn().execute("SELECT status, method, data FROM media_source WHERE media_id=?", (mid,)).fetchone()
    if not r or r["status"] != "found":
        return None
    d = json.loads(r["data"] or "{}")
    dan = d.get("danbooru")
    if dan and (dan.get("characters") is not None):
        return set(dan.get("characters") or [])
    return None


def add(mid: int, name: str, category: str, tier: int, source: str, score: float | None = None,
        reason: str = "", emb: np.ndarray | None = None, confirm: bool = False) -> bool:
    """新增或升級一筆候選；回傳是否有寫入。必須在 db.write_lock 內呼叫。"""
    from .matcher import matcher
    from . import steps
    # 這一步「找到時怎麼辦」（設定 → 自動標記與搜源）：只記下 → 不加；放待確認 → 原本直接確認的也改成候選；直接確認 → 過了下面的檢查就確認
    key = steps.step_for(source, tier)
    act = steps.action(key)
    if confirm and steps.BY_KEY.get(key or "", {}).get("act") not in ("confirm", "auto"):
        act = None          # 呼叫端特地要確認（這一步平常不會直接確認）：照呼叫端
    if act == "record":
        return False
    if act == "candidate":
        confirm = False
    forced = act == "confirm" and not confirm
    c = db.conn()
    tid = db.tag_id(name, category)
    r = c.execute("SELECT state, tier, source FROM media_tags WHERE media_id=? AND tag_id=?", (mid, tid)).fetchone()
    if r is not None and r["state"] == 1 and r["source"] == "implied" and (confirm or forced):
        # 只是被別的標籤蘊含帶上的（例如角色 ⇒ 作品，b93），現在有來源直接確認：記成真正的來源（處理紀錄、統計才對）
        c.execute("UPDATE media_tags SET source=?, tier=?, score=?, reason=? WHERE media_id=? AND tag_id=?",
                  (source, tier, score, (reason or "") + (steps.AUTO_MARK if forced else ""), mid, tid))
        return True
    if r is not None and r["state"] != 0:
        # 已確認/已否決的不動；例外：原檔完全相符 → 把舊的候選升級為確認（已否決仍尊重）
        return False
    if name in matcher.blocked and not confirm:
        return False
    if not confirm and source not in ("album", "manual") and db.in_album(mid):
        return False       # 作品集裡的圖隔絕：自動來源一律不推（整本的標籤、你手動加的照常；使用者要求 2026-10-05）
    if not confirm and category in IDENTITY:
        from . import quicktags
        if quicktags.has_no_char(c, mid):
            return False   # 有「梗圖」「截圖」這類標籤（設定 no_char_tags）：不再認角色、不列候選給你確認
    if source in ("model", "ccip", "pixai") and not confirm and category in IDENTITY and \
            c.execute("SELECT COALESCE(unrec_skip,0) FROM media WHERE id=?", (mid,)).fetchone()[0]:
        return False       # 你按過「略過」的圖（梗圖、截圖）：AI 不再推薦角色
    if not confirm and tier >= 3:
        # 搜源最優先：有 Danbooru 角色清單時，清單外的角色不列入候選
        if category == "character":
            sc = source_characters(mid)
            if sc is not None and name not in sc:
                return False
        # 跟你否決過的圖太像 → 不推薦
        if emb is not None and matcher.filter_auto(mid, {name: 1.0}, emb) == {}:
            return False
    if r is not None and not confirm and r["tier"] is not None and r["tier"] <= tier:
        # 已有更可信（或同等）的候選；例外：逐人 CCIP 比整張圖的 AI 模型更可靠，同一層時取代它
        if not (r["tier"] == tier and source == "ccip" and r["source"] == "model"):
            return False
    c.execute("""INSERT INTO media_tags(media_id, tag_id, score, source, state, tier, reason) VALUES(?,?,?,?,?,?,?)
        ON CONFLICT(media_id, tag_id) DO UPDATE SET score=excluded.score, source=excluded.source,
          state=excluded.state, tier=excluded.tier, reason=excluded.reason""",
              (mid, tid, score, source, 1 if (confirm or forced) else 0, tier, (reason or "") + (steps.AUTO_MARK if forced else "")))
    if confirm or forced:          # 自動確認（MD5、Pixiv ID…）也套用標籤蘊含（b84）
        from . import implies
        implies.apply(c, [mid], tid)
    return True


def enforce_source_priority(mid: int):
    """搜源找到後：AI 模型的角色候選收掉（設定可關）；有角色清單時，清掉其他來源推薦、但不在清單裡的角色候選。"""
    c = db.conn()
    if db.get_settings().get("ai_hide_when_sourced", True):
        c.execute("""DELETE FROM media_tags WHERE media_id=? AND state=0 AND source IN ('model','match')
                     AND tag_id IN (SELECT id FROM tags WHERE category='character')""", (mid,))
    sc = source_characters(mid)
    if sc is None:
        return 0
    rows = c.execute("""SELECT mt.tag_id, t.name FROM media_tags mt JOIN tags t ON t.id=mt.tag_id
        WHERE mt.media_id=? AND mt.state=0 AND t.category='character' AND COALESCE(mt.tier, 9) >= 3""", (mid,)).fetchall()
    n = 0
    for r in rows:
        if r["name"] not in sc:
            c.execute("DELETE FROM media_tags WHERE media_id=? AND tag_id=?", (mid, r["tag_id"]))
            n += 1
    return n


# ---------- 同一組圖 ----------
PIXIV_SET = re.compile(r"(?:^|[^\d])(\d{5,10})(?:-[0-9a-f]{32})?_p\d+", re.I)
TWEET_SET = re.compile(r"(?:^|[^\d])(1\d{17,19})(?:[^\d]|$)")  # Twitter/X 貼文 ID（snowflake）
SEQ = re.compile(r"^(.*?)(\d{2,})(\D*)$")
# 連號只認「相機」檔名（一定要有前綴）；純數字、下載器的 01、02… 都不算（最容易把不同角色串在一起）
CAMERA_PREFIX = re.compile(r"^(?:IMG|IMG_E|MVIMG|DSC|DSCF|DSCN|_DSC|_MG|PXL_\d{8}|CIMG|DJI|KIMG)[_\- ]?$", re.I)
MAX_SET = 40


def compute_sets():
    """「同一組圖」功能已移除（判定不夠可靠）：清掉舊的分組和同組候選。"""
    c = db.conn()
    if not c.execute("SELECT 1 FROM media WHERE set_key IS NOT NULL LIMIT 1").fetchone() and \
            not c.execute("SELECT 1 FROM media_tags WHERE source='set' AND state=0 LIMIT 1").fetchone():
        return
    with db.write_lock:
        c.execute("UPDATE media SET set_key=NULL WHERE set_key IS NOT NULL")
        c.execute("DELETE FROM media_tags WHERE source='set' AND state=0")


# ---------- 媒材 ----------
MEDIA_TYPES = {
    "illustration": "插畫", "chibi": "Q 版", "comic": "漫畫", "3d": "3D",
    "cosplay": "Cosplay", "photo": "真人照片", "figure": "公仔/娃娃",
}


def media_type(gen: dict[str, float]) -> str:
    """從 WD tagger 的一般 tag 推測媒材。"""
    g = lambda *ks: max((gen.get(k, 0.0) for k in ks), default=0.0)
    if g("figure", "doll", "plush", "stuffed_toy", "figma", "nendoroid") >= 0.5:
        return "figure"
    photo = g("photo_(medium)", "photorealistic", "realistic")
    if photo >= 0.5:
        return "cosplay" if g("cosplay", "cosplay_photo") >= 0.35 else "photo"
    if g("3d", "blender_(medium)", "koikatsu_(medium)", "mikumikudance_(medium)") >= 0.5:
        return "3d"
    if g("comic", "4koma", "manga") >= 0.5:
        return "comic"
    if g("chibi") >= 0.5:
        return "chibi"
    return "illustration"


def backfill_media_types():
    """舊資料：用已存的一般 tag 推測媒材（不用重新標記）。"""
    c = db.conn()
    ids = [r[0] for r in c.execute("SELECT id FROM media WHERE media_type IS NULL AND status='done'")]
    if not ids:
        return
    keys = ("figure", "doll", "plush", "stuffed_toy", "figma", "nendoroid", "photo_(medium)", "photorealistic",
            "realistic", "cosplay", "cosplay_photo", "3d", "blender_(medium)", "koikatsu_(medium)",
            "mikumikudance_(medium)", "comic", "4koma", "manga", "chibi")
    tags = defaultdict(dict)
    ph = ",".join("?" * len(keys))
    for r in c.execute(f"""SELECT mt.media_id, t.name, mt.score FROM media_tags mt JOIN tags t ON t.id=mt.tag_id
                           WHERE t.name IN ({ph}) AND mt.state>=0""", keys):
        tags[r[0]][r[1]] = r[2] if r[2] is not None else 1.0
    with db.write_lock:
        c.executemany("UPDATE media SET media_type=? WHERE id=?", [(media_type(tags.get(i, {})), i) for i in ids])
        c.commit()
