"""自動標記與搜源的「每一步」（2026-10，使用者要求：搜源、自動標記很亂、自訂性差）。

每一步都有：開／關、找到時怎麼辦（直接確認／放待確認／只記下）、門檻、準確度參考（你按過的是／不是）。
預設＝目前實測最穩定的做法（跟改版前的行為一樣）。

找到時怎麼辦在 candidates.add 一個地方套用（所有自動標籤都經過那裡）：
- confirm   直接確認（還是會先過否決、停止自動標註、本子、梗圖這些檢查）
- candidate 放待確認（就算來源本來會直接確認）
- record    只記下：搜源結果、AI 分數照存，但不加標籤
- auto      依情況（X：作者直接確認、hashtag 放待確認）
設定存在 act_<key>，沒設就用這裡的預設。
"""
from __future__ import annotations

from . import db

ACTS = {"confirm": "直接確認", "candidate": "放待確認", "record": "只記下", "auto": "依情況"}
AUTO_MARK = "（自動確認）"       # 原本放待確認、因為設定改成直接確認的：理由後面加這個（準確度統計不算成你按的「是」）

GROUPS = [
    ("local", "在你的電腦上認", "不連網：AI 模型、你確認過的圖、圖裡的資料"),
    ("exact", "Danbooru：同一張", "檔案、像素、Pixiv 作品、推文對得上"),
    ("similar", "找相似的圖", "Danbooru 沒有同一張時：以圖搜圖、其他圖站"),
    ("import", "從網站匯入時", "瀏覽器按鈕存進來的圖，網站上原本的標籤"),
    ("manual", "半自動", "你按了才做"),
    ("after", "找到之後", "補資料，不加身分標籤"),
]


def _t(k, label, lo, hi, step, unit="", desc="", scale=None, invert=False, cat=None):
    """門檻。scale：存著的分數 × scale 才跟門檻同單位；invert：分數 ≥ 1 − 門檻 才算過（CCIP 的差異值）。"""
    return {"k": k, "label": label, "min": lo, "max": hi, "step": step, "unit": unit, "desc": desc,
            "scale": scale, "invert": invert, "cat": cat}


STEPS: list[dict] = [
    # ---- 本機 ----
    {"key": "wd", "group": "local", "name": "WD Tagger", "on": None, "act": "candidate", "acts": ["confirm", "candidate", "record"],
     "desc": "每張新圖都會跑：描述標籤、分級、認角色。這裡管的是「認角色」。",
     "thr": [_t("character_threshold", "角色門檻", 0.5, 0.99, 0.01, desc="實測 ≥0.85 時 94% 正確。改了要補跑才會套用到舊圖。", cat="character"),
             _t("general_threshold", "描述標籤門檻", 0.1, 0.9, 0.01, desc="描述標籤（不是身分）直接加；越低越多、也越多錯的。")],
     "src": [("model", None)], "rerun": "wd"},
    {"key": "pixai", "group": "local", "name": "PixAI", "on": "pixai_enabled", "act": "record", "acts": ["confirm", "candidate", "record"],
     "desc": "第二個 AI 模型，認得比較多角色和畫風。第一次開會下載約 950 MB。預設只記下：評估過再放進待確認。",
     "thr": [_t("pixai_char_threshold", "角色門檻", 0.3, 0.99, 0.01, cat="character"),
             _t("pixai_artist_threshold", "畫風（畫師）門檻", 0.3, 0.99, 0.01, cat="artist"),
             _t("pixai_copyright_threshold", "作品門檻", 0.3, 0.99, 0.01, cat="copyright")],
     "extra": [("pixai_artists", "也推薦畫師（畫風像）")],
     "src": [("pixai", None)], "rerun": "pixai"},
    {"key": "knn", "group": "local", "name": "你的圖庫", "on": "knn_enabled", "act": "candidate", "acts": ["candidate", "record"],
     "desc": "跟你確認過的圖很像就推薦，WD 不認得的新角色也有。你按的是／不是會拿來學習，所以會自己更新、不用補跑。",
     "thr": [_t("knn_p_min", "推薦門檻", 0.2, 0.9, 0.05, desc="學到你的標準以後（按過 40 次以上），預測「是」的機率要多高才推薦。")],
     "src": [("knn", None)], "rerun": None},
    {"key": "clip", "group": "local", "name": "媒材（CLIP）", "on": "media_clip", "act": None,
     "desc": "判斷媒材時多用一個看一般圖片的模型：3D、公仔、Cosplay 分得比較準（抽樣 81% → 85%）。第一次會下載約 350 MB，整個圖庫算一次（有顯示卡用 GPU，沒有用 CPU，約半小時）。",
     "thr": [], "src": [], "rerun": None},
    {"key": "aimeta", "group": "local", "name": "AI 生成提示詞", "on": None, "act": "confirm", "acts": ["confirm", "candidate", "record"],
     "desc": "AI 生成的圖，提示詞裡寫了角色、作品名字。預設直接確認。", "thr": [], "src": [("aimeta", None)], "rerun": "aimeta"},
    {"key": "sig", "group": "local", "name": "圖上的簽名", "on": "auto_signature", "act": "candidate", "acts": ["confirm", "candidate", "record"],
     "desc": "本機讀圖上的 @帳號，對到 Danbooru 登記的畫師（不會建立新畫師）。只讀 AI 判斷有簽名的圖。",
     "thr": [], "src": [("signature", None)], "rerun": "sig"},
    # ---- Danbooru 同一張 ----
    {"key": "exact", "group": "exact", "name": "Danbooru 原檔", "on": None, "act": "confirm", "acts": ["confirm", "candidate", "record"],
     "desc": "檔案或檔名的 MD5、Pixiv 作品 ID、推文 ID 對得上 Danbooru：角色、作品、畫師是人工整理的。",
     "thr": [], "src": [("danbooru", 1), ("pixiv", 1)], "rerun": "source"},
    {"key": "pixelhash", "group": "exact", "name": "像素指紋", "on": "auto_pixelhash", "act": None,
     "desc": "像素跟 Danbooru 原檔一樣（重存的 PNG、去掉 EXIF）。只送出指紋。找到時照「Danbooru 原檔」的做法。",
     "thr": [], "src": [], "rerun": "source"},
    # ---- 相似 ----
    {"key": "iqdb", "group": "similar", "name": "以圖搜圖（IQDB）", "on": "online_iqdb", "act": "candidate", "acts": ["confirm", "candidate", "record"],
     "desc": "上傳縮小的圖找 Danbooru 上相似的（找到最多，但截圖、裁切會有錯的）。",
     "thr": [_t("online_min_similarity", "直接採用", 50, 100, 1, "%", "高於這個相似度直接採用。", scale=100),
             _t("online_verify_min", "下載比對", 50, 100, 1, "%", "介於兩個門檻之間：下載 Danbooru 的圖實際比對，夠像才採用。", scale=100)],
     "src": [("danbooru", 2)], "rerun": "source"},
    {"key": "pixiv", "group": "similar", "name": "Pixiv 標籤", "on": None, "act": "candidate", "acts": ["confirm", "candidate", "record"],
     "desc": "Danbooru 沒有角色清單時，用 Pixiv 作品上的標籤和作者。", "thr": [], "src": [("pixiv", 3)], "rerun": "source"},
    {"key": "r34", "group": "similar", "name": "Rule34 原檔", "on": "auto_r34", "act": "candidate", "acts": ["confirm", "candidate", "record"],
     "desc": "Danbooru 沒收的 NSFW 圖，只送指紋。要先在「網站帳號」填 key。", "thr": [], "src": [("rule34", None)], "rerun": "r34"},
    # ---- 匯入 ----
    {"key": "x", "group": "import", "name": "X 貼文", "on": None, "act": "auto", "acts": ["auto", "confirm", "candidate", "record"],
     "desc": "依情況：貼文作者是登記過的畫師就直接確認，hashtag 放待確認。", "thr": [], "src": [("x", None)], "rerun": None},
    # ---- 半自動 ----
    {"key": "ascii2d", "group": "manual", "name": "ascii2d", "on": None, "act": None,
     "desc": "Danbooru 沒有的 X／Pixiv 圖：你在 ascii2d 按 Enter，程式比對、整理作者。", "thr": [], "src": [], "rerun": "ascii2d",
     "choice": ("ascii2d_auto_accept", "自動確認", [["off", "不要，我自己按 Enter"], ["same", "比對到「同一張」時"], ["likely", "「同一張」或「很像」時"], ["full", "全自動（什麼情況都自己決定，不停下來）"]])},
    {"key": "sweep", "group": "manual", "name": "畫師作品比對", "on": None, "act": "confirm", "acts": ["confirm", "candidate", "record"],
     "desc": "已知畫師在 Pixiv／X 的全部作品跟沒出處的圖比，比對到的畫師。", "thr": [], "src": [("pixiv_scan", None), ("x_scan", None)], "rerun": None},
    # ---- 之後 ----
    {"key": "upgrade", "group": "after", "name": "找更高清的版本", "on": "auto_upgrade", "act": None,
     "desc": "出處上的同一張比你的大：只提醒，換不換你決定。", "thr": [], "src": [], "rerun": None},
    {"key": "zhname", "group": "after", "name": "中文名", "on": "auto_zhname", "act": None,
     "desc": "沒有中文名（或是暱稱）的角色／作品，到 Bangumi 找。", "thr": [], "src": [], "rerun": None},
    {"key": "charinfo", "group": "after", "name": "角色資料卡", "on": "online_auto_charinfo", "act": None,
     "desc": "角色的中文名、作品、介紹（新角色、打開角色頁時）。", "thr": [], "src": [], "rerun": None},
]
# 共同規則（不屬於某一步）：設定頁這一類最上面
COMMON = ("online_enabled", "online_auto_source", "online_auto_level", "online_retry_days", "ai_hide_when_sourced", "auto_rematch", "neg_sim")
BY_KEY = {s["key"]: s for s in STEPS}
DEFAULTS = {f"act_{s['key']}": s["act"] for s in STEPS if s.get("act")}
_SRC = {}
for _s in STEPS:
    for _src, _tier in _s["src"]:
        _SRC[(_src, _tier)] = _s["key"]


def step_for(source: str, tier: int | None) -> str | None:
    return _SRC.get((source, tier)) or _SRC.get((source, None))


def action(key: str | None, s: dict | None = None) -> str | None:
    """這一步「找到時怎麼辦」；None＝這一步不管（手動、整本、舊來源）。"""
    st = BY_KEY.get(key or "")
    if not st or not st.get("act"):
        return None
    v = (s or db.get_settings()).get(f"act_{key}") or st["act"]
    return v if v in st["acts"] else st["act"]


def validate_act(k: str, v) -> str:
    st = BY_KEY.get(k[4:])
    if not st or v not in (st.get("acts") or []):
        raise ValueError("只能是 " + "／".join(ACTS[a] for a in (st or {}).get("acts", [])))
    return v


# ---------- 準確度參考 ----------
def _rows(c, st):
    conds, args = [], []
    for src, tier in st["src"]:
        if tier is None:
            conds.append("mt.source=?")
            args.append(src)
        else:
            conds.append("(mt.source=? AND mt.tier=?)")
            args += [src, tier]
    if not conds:
        return []
    return c.execute(f"""SELECT mt.state, mt.score, mt.reason, t.category FROM media_tags mt JOIN tags t ON t.id=mt.tag_id
        WHERE ({' OR '.join(conds)}) AND t.category IN ('character','artist','copyright')""", args).fetchall()


def _pass(score, thr, val):
    if score is None:
        return True
    sc = score * (thr["scale"] or 1)
    return sc >= (1 - val) if thr["invert"] else sc >= val - 1e-9


def stats(st: dict, s: dict) -> dict | None:
    if not st["src"]:
        return None
    c = db.conn()
    rows = _rows(c, st)
    act = action(st["key"], s)
    auto_yes = sum(1 for r in rows if r["state"] == 1 and (act == "confirm" and st["act"] == "confirm" or AUTO_MARK in (r["reason"] or "")))
    yes = sum(1 for r in rows if r["state"] == 1) - auto_yes
    no = sum(1 for r in rows if r["state"] == -1)
    pending = sum(1 for r in rows if r["state"] == 0)
    out = {"yes": yes, "no": no, "pending": pending, "auto": auto_yes,
           "precision": round(yes / (yes + no), 3) if yes + no >= 5 else None}
    if st["act"] == "confirm":       # 直接確認的步驟：你否決的佔全部的多少
        tot = auto_yes + yes + no
        out["precision"] = round(1 - no / tot, 3) if tot >= 5 else None
    # 門檻曲線：只用你按過的（是／不是）
    curves = []
    for thr in st["thr"]:
        dec = [(r["score"], r["state"] == 1) for r in rows if r["state"] in (1, -1) and r["score"] is not None
               and (thr["cat"] is None or r["category"] == thr["cat"]) and AUTO_MARK not in (r["reason"] or "")]
        if len(dec) < 10 or thr["k"] in ("general_threshold", "online_verify_min", "knn_p_min"):
            continue
        cur = s.get(thr["k"])
        pts = []
        lo, hi, step = thr["min"], thr["max"], thr["step"]
        n = 8
        for i in range(n + 1):
            v = round(lo + (hi - lo) * i / n, 4)
            v = round(round(v / step) * step, 4)
            kept = [ok for sc, ok in dec if _pass(sc, thr, v)]
            if kept:
                pts.append({"v": v, "n": len(kept), "p": round(sum(kept) / len(kept), 3)})
        kept = [ok for sc, ok in dec if _pass(sc, thr, cur)]
        curves.append({"k": thr["k"], "cur": {"v": cur, "n": len(kept), "p": round(sum(kept) / len(kept), 3) if kept else None},
                       "pts": pts, "of": len(dec)})
    out["curves"] = curves
    return out


def describe(s: dict | None = None, with_stats: bool = True) -> dict:
    s = s or db.get_settings()
    out = []
    for st in STEPS:
        d = {k: st.get(k) for k in ("key", "group", "name", "desc", "on", "rerun")}
        d["enabled"] = True if not st["on"] else bool(s.get(st["on"], True))
        d["act"] = action(st["key"], s)
        d["default_act"] = st["act"]
        d["acts"] = [[a, ACTS[a]] for a in st.get("acts", [])] if st.get("act") else []
        d["thr"] = [{**t, "v": s.get(t["k"]), "default": db.DEFAULT_SETTINGS.get(t["k"])} for t in st["thr"]]
        d["extra"] = [{"k": k, "label": l, "v": bool(s.get(k, True)), "default": db.DEFAULT_SETTINGS.get(k)} for k, l in st.get("extra", [])]
        if st.get("on"):
            d["on_default"] = db.DEFAULT_SETTINGS.get(st["on"])
        if st.get("choice"):
            k, l, opts = st["choice"]
            d["choice"] = {"k": k, "label": l, "opts": opts, "v": s.get(k), "default": db.DEFAULT_SETTINGS.get(k)}
        if st["key"] == "r34":
            from . import r34
            d["needs"] = None if r34.configured() else "要先在「網站帳號」填 Rule34 key"
        if with_stats:
            try:
                d["stats"] = stats(st, s)
            except Exception:   # noqa  統計失敗不影響設定頁
                d["stats"] = None
        out.append(d)
    common = {k: s.get(k) for k in COMMON}
    return {"groups": [{"key": k, "name": n, "desc": ds} for k, n, ds in GROUPS], "steps": out, "acts": ACTS,
            "common": common, "common_default": {k: db.DEFAULT_SETTINGS[k] for k in COMMON}}


def reset_patch() -> dict:
    """全部恢復預設（＝目前實測最穩定的）。"""
    p = dict(DEFAULTS)
    for st in STEPS:
        if st.get("on"):
            p[st["on"]] = db.DEFAULT_SETTINGS[st["on"]]
        for t in st["thr"]:
            p[t["k"]] = db.DEFAULT_SETTINGS[t["k"]]
        for k, _ in st.get("extra", []):
            p[k] = db.DEFAULT_SETTINGS[k]
        if st.get("choice"):
            p[st["choice"][0]] = db.DEFAULT_SETTINGS[st["choice"][0]]
    for k in COMMON:
        if k != "online_enabled":       # 總開關不跟著恢復
            p[k] = db.DEFAULT_SETTINGS[k]
    return p
