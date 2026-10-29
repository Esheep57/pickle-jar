"""b110.6：Bangumi 的組織頁（少女與戰車的隊伍、學校）不能當成角色的中文名。"""
import sqlite3

from app import db, zhname


def test_migration_drops_group_pages():
    c = sqlite3.connect(":memory:")
    c.execute("CREATE TABLE name_zh(name TEXT PRIMARY KEY, category TEXT, status TEXT, zh TEXT, raw TEXT, ref TEXT)")
    rows = [("erwin_(girls_und_panzer)", "河馬隊", "bgm:character/166515"),
            ("caesar_(girls_und_panzer)", "河馬隊", "bgm:character/166515"),
            ("mitachurl_(genshin_impact)", "丘丘人", "bgm:character/1"),
            ("lawachurl_(genshin_impact)", "丘丘人", "bgm:character/1"),
            ("nishizumi_miho", "西住美穗", "bgm:character/2")]
    c.executemany("INSERT INTO name_zh(name, category, status, zh, ref) VALUES(?, 'character', 'found', ?, ?)", rows)
    db._m21_bgm_groups(c)
    left = {r[0] for r in c.execute("SELECT name FROM name_zh")}
    assert left == {"mitachurl_(genshin_impact)", "lawachurl_(genshin_impact)", "nishizumi_miho"}


def test_search_skips_organizations(monkeypatch):
    data = {"data": [{"id": 166515, "name": "カバさんチーム", "type": 4, "infobox": [{"key": "简体中文名", "value": "河马队"}]},
                     {"id": 9, "name": "カバさんチーム", "type": 1, "infobox": [{"key": "简体中文名", "value": "河马"}]}]}
    monkeypatch.setattr(zhname, "_bgm", lambda *a, **k: data)
    got = zhname.search_characters("カバさんチーム")
    assert [h["id"] for h in got] == [9]


def test_transform_and_duplicate_names():
    from app import dupnames, names
    assert names.display("cure_peace", ["キュアピース", "黄瀬やよい"], None, "zh", cn=False).startswith("Cure Peace")
    old = dict(dupnames._S)
    try:
        dupnames._S.update(dup={False: {"紫苑"}, True: {"紫苑"}}, cp={"shion_(tensura)": "tensura"})
        assert dupnames.suffix("shion_(tensura)", "紫苑", False).startswith("紫苑（")
        assert dupnames.suffix("other", "紫苑", False) == "紫苑"        # 不知道作品：不加
        assert dupnames.suffix("shion_(tensura)", "梅", False) == "梅"
    finally:
        dupnames._S.update(old)
