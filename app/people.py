"""多人圖：這張圖大概有幾個人、角色標完了沒。

人數來自描述標籤（WD Tagger 或 Danbooru 的 1girl／2girls／multiple_boys…）。它數的是「人」，不是「有名字的角色」
（路人、背景人物也算），所以只拿來提醒「可能還有別人」，你按「標完了」就不再提醒。

實測（使用者圖庫 6,355 張）：WD 判斷 2 人以上 3,282 張；已經標了角色的多人圖裡，409 張標的角色比人數少、
其中 406 張完全沒有其他候選——以前確認一個角色後，另一個人就再也不會被提起。
"""
from __future__ import annotations

import re

from . import db

COUNT_TAGS = ["1girl", "2girls", "3girls", "4girls", "5girls", "6+girls", "multiple_girls",
              "1boy", "2boys", "3boys", "4boys", "5boys", "6+boys", "multiple_boys",
              "1other", "2others", "3others", "multiple_others", "solo", "no_humans",
              # 這些表示「另外的人多半是路人」：只畫一個主角、看不到臉的男性、第一人稱、同一人的多個角度
              "solo_focus", "faceless_male", "pov", "out_of_frame", "multiple_views", "character_sheet"]
_RE = re.compile(r"^(\d)\+?(girl|boy|other)s?$")
MIN_SCORE = 0.5          # 人數標籤要夠有把握（Danbooru 來的沒有分數，一律採用）
ANON_SCORE = 0.35

# 開發時的圖庫實測（已確認角色、WD 說 2 人以上的圖，其中真的標了 2 個以上角色的比例）：
#   1girl+1boy 29%、solo_focus 18%、pov 37%、看不到臉的男性 35%；2girls 81%、3girls 90%
# → 只畫一個主角、第一人稱、看不到臉的男性不算「還有人沒標」。這樣提醒從 395 張減到 198 張，準確率 59% → 74%。


def estimate(tags) -> int:
    """描述標籤（名稱→分數，或名稱集合）→ 大概有幾個「有名字的角色」（0＝沒有人或不知道）。"""
    d = tags if isinstance(tags, dict) else {t: 1.0 for t in tags}
    on = lambda k, th=MIN_SCORE: (d.get(k) if d.get(k) is not None else 0) >= th
    if on("no_humans"):
        return 0
    n = {"girl": 0, "boy": 0, "other": 0}
    for t, sc in d.items():
        m = _RE.match(t)
        if m and (sc is None or sc >= MIN_SCORE):
            n[m.group(2)] = max(n[m.group(2)], int(m.group(1)))
    for k in ("girl", "boy", "other"):
        if on(f"multiple_{k}s"):
            n[k] = max(n[k], 2)
    if on("solo_focus") or on("multiple_views") or on("character_sheet"):
        return 1
    if n["boy"] and any(on(k, ANON_SCORE) for k in ("faceless_male", "pov", "out_of_frame")):
        n["boy"] -= 1
    total = n["girl"] + n["boy"] + n["other"]
    if not total and on("solo"):
        total = 1
    return total


def refresh(mids: list[int] | None = None) -> int:
    """重算人數（整個圖庫約 0.2 秒）。回傳更新了幾張。"""
    c = db.conn()
    ph = ",".join("?" * len(COUNT_TAGS))
    where = ""
    if mids is not None:
        if not mids:
            return 0
        where = f" AND mt.media_id IN ({','.join('?' * len(mids))})"
    per: dict[int, dict] = {}
    for mid, name, sc, src in c.execute(f"""SELECT mt.media_id, t.name, mt.score, mt.source FROM media_tags mt JOIN tags t ON t.id=mt.tag_id
            WHERE t.name IN ({ph}) AND mt.state>=0{where}""", [*COUNT_TAGS, *(mids or [])]):
        per.setdefault(mid, {})[name] = 1.0 if (sc is None or src != "model") else sc
    targets = mids if mids is not None else [r[0] for r in c.execute("SELECT id FROM media WHERE status='done'")]
    rows = [(estimate(per.get(m, {})), m) for m in targets]
    with db.write_lock:
        cur = {r[0]: r[1] for r in c.execute("SELECT id, people FROM media")} if mids is None else \
            {r[0]: r[1] for r in c.execute(f"SELECT id, people FROM media WHERE id IN ({','.join('?' * len(mids))})", mids)}
        ch = [(p, m) for p, m in rows if cur.get(m) != p]
        c.executemany("UPDATE media SET people=? WHERE id=?", ch)
        c.commit()
    return len(ch)


# ---------- 同一個角色的本體和造型：算人數時只算一個 ----------
# 使用者回報：常常一張圖同時標了本體和造型（asuna_(blue_archive) ＋ asuna_(bunny)_(blue_archive)），
# 一個人被算成兩個角色，跟 WD 數的人數對不上（開發時的圖庫 451 張；其中 33 張其實還有人沒標，卻因為多算而沒提醒）。
CHAR_BASE_SCHEMA = "CREATE TABLE IF NOT EXISTS char_base(tag_id INTEGER PRIMARY KEY, base_id INTEGER NOT NULL)"


def variant_base(name: str, chars: set[str], series: set[str]) -> str | None:
    """造型 → 本體（比 names.base_of 保守）：括號裡是作品或別的角色時不算造型
    （higashikata_josuke_(jojolion) 是另一個人、gojo_satoru_(okkotsu_yuta) 是別人）。三段式（mika_(swimsuit)_(blue_archive)）一律算。"""
    from . import names
    b = names.base_of(name, chars)
    if not b:
        return None
    m = names.VARIANT.match(name)
    if m and b == f"{m.group(1)}_({m.group(3)})":
        return b
    q = names.SIMPLE_VARIANT.match(name).group(2)
    return None if (q in series or q in chars) else b


def guessed_pairs(c=None) -> list[tuple[int, int]]:
    """程式從名字猜的「造型 → 本體」（還沒扣掉 Danbooru 說不是的）。"""
    c = c or db.conn()
    rows = c.execute("SELECT id, name, category FROM tags WHERE category IN ('character','copyright')").fetchall()
    ids = {r[1]: r[0] for r in rows if r[2] == "character"}
    series = {r[1] for r in rows if r[2] == "copyright"}
    chars = set(ids)
    pairs = []
    for n, i in ids.items():
        b = variant_base(n, chars, series)
        if b:
            while True:      # 造型的造型：一路找到最上面的本體
                bb = variant_base(b, chars, series)
                if not bb or bb == b:
                    break
                b = bb
            pairs.append((i, ids[b]))
    return pairs


def refresh_bases() -> int:
    """重算 char_base（整個圖庫幾毫秒）。新角色 tag、合併、改名之後呼叫。"""
    c = db.conn()
    pairs = guessed_pairs(c)
    # 標籤蘊含（b84）：角色 ⇒ 角色的規則也算同一個人；你說「不是」的（off）拿掉。造型的造型一路找到最上面
    # b92：照 Danbooru——Danbooru 問過、上面有這個角色、卻沒有蘊含的（draco_malfoy_(redteneri) 是女體化的另一個角色），
    # 名字猜的就不算；Danbooru 沒這個角色或還沒問過的才用猜的。
    from . import implies
    off = implies.off_pairs()
    nope = implies.danbooru_says_no()
    up = {a: b for a, b in pairs if (a, b) not in off and a not in nope}
    for a, b in implies.char_pairs():
        if (a, b) not in off and a not in up:
            up[a] = b
    pairs = []
    for a in up:
        b, seen = up[a], {a}
        while b in up and b not in seen:
            seen.add(b)
            b = up[b]
        if b != a:
            pairs.append((a, b))
    with db.write_lock:
        c.execute(CHAR_BASE_SCHEMA)
        old = set(map(tuple, c.execute("SELECT tag_id, base_id FROM char_base")))
        if old != set(pairs):
            c.execute("DELETE FROM char_base")
            c.executemany("INSERT INTO char_base(tag_id, base_id) VALUES(?,?)", pairs)
        c.commit()
    return len(pairs)


# 多人圖、已經標了至少一個角色、但比人數少、你也還沒按「標完了」（跟未識別頁「多人圖缺角色」同一個條件）
from .query import MISSING_RAW as _MR  # noqa: E402
SHORT_SQL = _MR["people"]


def info(mid: int) -> dict:
    c = db.conn()
    r = c.execute("SELECT COALESCE(people,0), COALESCE(people_done,0) FROM media WHERE id=?", (mid,)).fetchone()
    if not r:
        return {"people": 0, "done": False, "chars": 0, "short": False}
    n = c.execute("""SELECT COUNT(DISTINCT COALESCE(cb.base_id, x.tag_id)) FROM media_tags x JOIN tags t ON t.id=x.tag_id
                     LEFT JOIN char_base cb ON cb.tag_id=x.tag_id
                     WHERE x.media_id=? AND x.state=1 AND t.category='character'""", (mid,)).fetchone()[0]
    short = r[0] >= 2 and not r[1] and 1 <= n < r[0]
    if short:   # 跟未識別頁同一個條件（含「Danbooru／Pixiv 標過的不算」）
        short = bool(c.execute(f"SELECT 1 FROM media m WHERE m.id=? AND {SHORT_SQL}", (mid,)).fetchone())
    return {"people": r[0], "done": bool(r[1]), "chars": n, "short": short, "site": r[0] >= 2 and 1 <= n < r[0] and not short and not r[1]}


