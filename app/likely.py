"""搜源前的本地預判：這張圖「有沒有可能在 Danbooru 上」。

完全不連網，只看已經有的資料（檔名、尺寸、媒材、WD Tagger 的描述 tag），
讓背景搜源先搜最有希望的圖，並略過幾乎不可能找到的（梗圖、截圖、照片、本機 AI 生成…）。
被略過的圖仍然會用檔案 MD5 對一次 Danbooru（很快），也可以手動按 S 強制搜尋。
"""
from __future__ import annotations

import os
import re

from . import db

LIKELY, MAYBE = 60, 30          # ≥60 很可能；30–59 可能；<30 不太可能
LABEL = {"likely": "很可能在 Danbooru", "maybe": "可能在 Danbooru", "unlikely": "不太可能在 Danbooru"}

HEX32 = re.compile(r"^[0-9a-fA-F]{32}(?:\s*\(\d+\)|_\d)?$")
X_MEDIA = re.compile(r"^[A-Za-z0-9_-]{15}(?:\s*\(\d+\))?$")
PIXIV = re.compile(r"(?:^|[^\d])\d{5,10}(?:-[0-9a-f]{32})?_p\d+")
TWEET = re.compile(r"(?:^|[^\d])1\d{17,19}(?:[^\d]|$)")
LOCAL_AI = re.compile(r"^(?:ComfyUI_|\d{5}-\d{6,}|\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}-)", re.I)
PHONE_W = {640, 720, 750, 828, 1080, 1125, 1170, 1179, 1242, 1284, 1290, 1320, 1440}

# WD Tagger 的描述 tag → 加減分
POSITIVE = {  # 有畫師簽名、社群帳號 → 多半是公開發表過的圖
    "signature": 15, "artist_name": 15, "twitter_username": 20, "patreon_username": 15, "pixiv_id": 20,
    "watermark": 10, "web_address": 10, "dated": 8, "copyright_name": 10,
}
NEGATIVE = {  # 梗圖、截圖、照片
    "meme": 35, "screenshot": 30, "fake_screenshot": 25, "user_interface": 25, "subtitled": 25, "text_focus": 25,
    "photo_(medium)": 40, "realistic": 25, "photorealistic": 30, "multiple_views": 0,
    "english_text": 8, "chinese_text": 12, "korean_text": 10,
}
SIGNALS = set(POSITIVE) | set(NEGATIVE)


def filename_clue(name: str) -> str | None:
    stem = os.path.splitext(name)[0]
    if HEX32.match(stem):
        return "檔名是 MD5"
    if PIXIV.search(stem):
        return "檔名有 Pixiv 作品 ID"
    if TWEET.search(stem):
        return "檔名有推文 ID"
    if X_MEDIA.match(stem) and re.search(r"[A-Za-z]", stem) and re.search(r"\d", stem):
        return "檔名是 X 圖片編號"
    return None


def score(m: dict, tags: dict[str, float], has_char: bool) -> tuple[int, list[str]]:
    """m: media 列（path, width, height, media_type）；tags: 訊號 tag → 分數；has_char: WD 認得出角色。
    回傳 (0–100 分, 理由)。"""
    name = re.split(r"[\\/]", m["path"])[-1]
    clue = filename_clue(name)
    if clue:
        return 100, [clue]
    if LOCAL_AI.match(os.path.splitext(name)[0]):
        return 0, ["本機 AI 生成的圖"]
    mt = m.get("media_type") or "illustration"
    if mt in ("photo", "cosplay", "figure"):
        return 0, ["真人／公仔照片"]
    s, why = 50, []
    if mt == "comic":
        s -= 5
    if mt == "other":       # 截圖／文字（b77）
        s -= 30
        why.append("截圖／文字")
    if mt == "3d":
        s -= 10
        why.append("3D")
    if has_char:
        s += 25
        why.append("認得出角色（同人圖）")
    for t, sc in tags.items():
        if t in POSITIVE and sc >= 0.35:
            s += POSITIVE[t]
            why.append({"signature": "有畫師簽名", "artist_name": "有畫師名", "twitter_username": "有 X 帳號",
                        "patreon_username": "有 Patreon", "pixiv_id": "有 Pixiv ID", "watermark": "有浮水印",
                        "web_address": "有網址", "dated": "有日期", "copyright_name": "有作品名"}.get(t, t))
    neg = 0
    for t, sc in tags.items():
        if t in NEGATIVE and sc >= 0.35:
            neg += NEGATIVE[t]
            if NEGATIVE[t] >= 20:
                why.append({"meme": "像梗圖", "screenshot": "像截圖", "fake_screenshot": "像截圖",
                            "user_interface": "有 App 介面", "subtitled": "有字幕", "text_focus": "文字很多",
                            "photo_(medium)": "像照片", "realistic": "寫實", "photorealistic": "像照片"}.get(t, t))
    s -= min(neg, 60)
    w, h = m.get("width") or 0, m.get("height") or 0
    if w and h:
        if max(w, h) < 400:
            s -= 20
            why.append("解析度很低")
        if w in PHONE_W and h / w >= 1.95:
            s -= 15
            why.append("手機截圖尺寸")
    return max(0, min(100, s)), why


def level(sc: int) -> str:
    return "likely" if sc >= LIKELY else "maybe" if sc >= MAYBE else "unlikely"


def assess(ids: list[int]) -> dict[int, tuple[int, list[str]]]:
    """批次預判。"""
    if not ids:
        return {}
    c = db.conn()
    out: dict[int, tuple[int, list[str]]] = {}
    for i in range(0, len(ids), 800):
        ch = ids[i:i + 800]
        ph = ",".join("?" * len(ch))
        meta = {r["id"]: dict(r) for r in c.execute(
            f"SELECT id, path, width, height, media_type FROM media WHERE id IN ({ph})", ch)}
        tags: dict[int, dict[str, float]] = {}
        chars: set[int] = set()
        sig = ",".join("?" * len(SIGNALS))
        for r in c.execute(f"""SELECT mt.media_id, t.name, t.category, mt.score, mt.state FROM media_tags mt JOIN tags t ON t.id=mt.tag_id
                               WHERE mt.media_id IN ({ph}) AND mt.state>=0
                                 AND (t.name IN ({sig}) OR t.category='character')""", [*ch, *SIGNALS]):
            if r["category"] == "character":
                if r["state"] == 1 or (r["score"] or 0) >= 0.85:
                    chars.add(r["media_id"])
            else:
                tags.setdefault(r["media_id"], {})[r["name"]] = r["score"] if r["score"] is not None else 1.0
        for mid in ch:
            if mid in meta:
                out[mid] = score(meta[mid], tags.get(mid, {}), mid in chars)
    return out


def summary(ids: list[int]) -> dict[str, int]:
    res = {"likely": 0, "maybe": 0, "unlikely": 0}
    for sc, _ in assess(ids).values():
        res[level(sc)] += 1
    return res
