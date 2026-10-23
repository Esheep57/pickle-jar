"""未識別的「分堆」：把一大堆沒有身分的圖依描述標籤自動分成幾堆，讓你一堆一堆決定（整堆略過＋標「梗圖」、排除…）。

開發時的圖庫實測（未識別 3,932 張；每堆隨機抽 40 張目視檢查）：
- 梗圖／截圖 498：聊天紀錄、假截圖、meme、大字的圖；抽查約九成是梗圖或截圖（少數真人照片，一樣不用標角色）
- 有字的插畫 522：有人物、但圖上有中文字／英文字／emoji——約七成是表情包、梗圖，也有翻譯過的同人圖 → 單獨一堆，先看再決定
- 真人照片／cosplay／公仔 126、沒有人物 39、漫畫 365、影片 348
- 其餘插畫 ~2,030：真正要認角色的（仍有一兩成梗圖混在裡面）
分堆只是篩選，不會自動做任何事；每張圖處理完（標了角色、略過、排除）就離開未識別。
"""
from __future__ import annotations

from . import db

PILES = [("meme", "梗圖／截圖"), ("texty", "有字的插畫"), ("photo", "照片／cosplay"), ("nohuman", "沒有人物"),
         ("comic", "漫畫"), ("video", "影片"), ("illust", "插畫")]
TIPS = {
    "meme": "聊天紀錄、假截圖、meme、大字的圖（抽查約九成是梗圖或截圖）",
    "texty": "有人物、圖上有中文字／英文字／emoji：多半是表情包、梗圖，也有翻譯過的同人圖，先看一遍",
    "photo": "真人照片、cosplay、公仔",
    "nohuman": "食物、動物、風景、物品",
    "comic": "漫畫頁、四格",
    "video": "影片",
    "illust": "有人物的插畫：真正要認角色的",
}
_TAGS = ["chat_log", "fake_screenshot", "meme", "text_focus", "reaction_image", "chinese_text", "english_text", "korean_text",
         "emoji", "realistic", "photo_(medium)", "no_humans", "comic", "4koma", "monochrome", "speech_bubble",
         "1girl", "1boy", "multiple_girls", "multiple_boys", "solo"]


def classify(kind: str, media_type: str | None, d: dict) -> str:
    on = lambda k, t=0.5: (d.get(k) or 0) >= t
    if kind == "video":
        return "video"
    if media_type in ("photo", "cosplay", "figure") or on("realistic", 0.6) or on("photo_(medium)"):
        return "photo"
    if media_type == "other" and not on("comic"):     # 截圖／文字（b77）
        return "meme"
    if on("chat_log") or on("fake_screenshot") or on("meme") or on("text_focus", 0.6) or on("reaction_image"):
        return "meme"
    people = on("1girl") or on("1boy") or on("multiple_girls") or on("multiple_boys") or on("solo")
    if (on("chinese_text", 0.7) or on("english_text", 0.7)) and not people and not on("comic"):
        return "meme"
    if on("no_humans") and not people:
        return "nohuman"
    if media_type == "comic" or on("comic") or on("4koma") or (on("monochrome") and on("speech_bubble")):
        return "comic"
    if on("chinese_text") or on("english_text", 0.6) or on("korean_text") or on("emoji"):
        return "texty"
    return "illust"


def refresh(mids: list[int] | None = None) -> int:
    c = db.conn()
    if mids is not None and not mids:
        return 0
    where = f" AND m.id IN ({','.join('?' * len(mids))})" if mids else ""
    rows = c.execute(f"SELECT m.id, m.kind, m.media_type, m.pile FROM media m WHERE m.status='done'{where}", mids or []).fetchall()
    ids = [r[0] for r in rows]
    tags: dict[int, dict] = {}
    ph = ",".join("?" * len(_TAGS))
    for i in range(0, len(ids), 800):
        ch = ids[i:i + 800]
        for mid, name, sc, src in c.execute(f"""SELECT mt.media_id, t.name, mt.score, mt.source FROM media_tags mt JOIN tags t ON t.id=mt.tag_id
                WHERE mt.state>=0 AND t.name IN ({ph}) AND mt.media_id IN ({','.join('?' * len(ch))})""", [*_TAGS, *ch]):
            tags.setdefault(mid, {})[name] = 1.0 if (sc is None or src != "model") else sc
    ch = [(p, r[0]) for r in rows if (p := classify(r[1], r[2], tags.get(r[0], {}))) != r[3]]
    with db.write_lock:
        c.executemany("UPDATE media SET pile=? WHERE id=?", ch)
        c.commit()
    return len(ch)
