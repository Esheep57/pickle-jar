"""簡體模式（zh_mode＝cn）：介面用語換成大陸用語、角色／作品用大陸譯名；兩種寫法都搜得到。"""
import time

from app import db, names


def _mode(client, m):
    r = client.post("/api/settings", json={"zh_mode": m})
    assert r.status_code == 200 and r.json()["zh_mode"] == m


def test_setting_and_validation(client):
    assert client.get("/api/settings").json()["zh_mode"] == "tw"          # 預設繁體
    assert client.post("/api/settings", json={"zh_mode": "jp"}).status_code == 400
    _mode(client, "cn")
    assert names.mode() == "cn"
    _mode(client, "tw")
    assert names.mode() == "tw"


def test_character_names(client):
    from app import zhname
    # 寶可夢：官方大陸譯名（不是台灣譯名轉簡體：謎擬Ｑ → 谜拟丘）
    assert names.pokemon_zh("mimikyu_(pokemon)", [], cn=True) == "谜拟丘"
    assert names.pokemon_zh("mimikyu_(pokemon)", []) == "謎擬Ｑ"
    # 校對過的台灣譯名：沒有 Bangumi 原文 → 轉簡體字
    names.save_local({"abarai_renji": "阿散井戀次"})
    assert names.display("abarai_renji", [], None, "zh", cn=True) == "阿散井恋次"
    assert names.display("abarai_renji", [], None, "zh", cn=False) == "阿散井戀次"
    # Bangumi 原文（大陸譯名）跟台灣譯名用字不同：簡體模式用原文
    c = db.conn()
    with db.write_lock:
        c.execute("""INSERT OR REPLACE INTO name_zh(name, category, status, zh, raw, ref, seen, checked_at)
                     VALUES('zhcn_test_(tst)', 'character', 'found', '湊阿庫婭', '凑阿库娅', 'bgm:character/1', 0, ?)""", (time.time(),))
    zhname.reload()
    assert names.display("zhcn_test_(tst)", [], None, "zh", cn=False) == "湊阿庫婭"
    assert names.display("zhcn_test_(tst)", [], None, "zh", cn=True) == "凑阿库娅"
    # 你取的名字：只轉簡體字（不換用語）
    assert names.display("whatever", [], "影片姬", "zh", cn=True) == "影片姬"
    assert names.display("whatever", [], "戀愛", "zh", cn=True) == "恋爱"
    # 日文、原始 tag 模式不受影響
    assert names.display("zhcn_test_(tst)", ["みなとあくあ"], None, "ja", cn=True) == "みなとあくあ"
    with db.write_lock:
        c.execute("DELETE FROM name_zh WHERE name='zhcn_test_(tst)'")
    zhname.reload()


def test_series_names(client, monkeypatch):
    from app import danbooru, wikiname
    # 內建表：[當時的台灣譯名, 大陸譯名]；台灣譯名跟現在一樣才用
    monkeypatch.setattr(names, "_series_zh", lambda: {"zhcn_series": "刀劍神域"})
    monkeypatch.setattr(names, "_series_cn_table", lambda: {"zhcn_series": ["刀劍神域", "刀剑神域"]})
    assert names.series_display("zhcn_series", [], None, "zh", cn=True) == "刀剑神域"
    assert names.series_display("zhcn_series", [], None, "zh", cn=False) == "刀劍神域"
    monkeypatch.setattr(names, "_series_cn_table", lambda: {"zhcn_series": ["別的名字", "别的名字"]})
    assert names.series_display("zhcn_series", [], None, "zh", cn=True) == "刀剑神域", "台灣譯名改過：轉簡體字"
    # 維基百科：查台灣譯名時一起記大陸標題
    tid = db.tag_id("project_sekai", "copyright")
    danbooru.save_tag_info(tid, danbooru.fetch_tag_info("project_sekai"))
    with db.write_lock:
        db.conn().execute("DELETE FROM series_zh WHERE name='project_sekai'")
    wikiname.run(["project_sekai"])
    row = db.conn().execute("SELECT zh, cn FROM series_zh WHERE name='project_sekai'").fetchone()
    assert tuple(row) == ("世界計畫 彩色舞台 feat. 初音未來", "世界计划 彩色舞台 feat. 初音未来")
    names._WIKI["at"] = names._WIKI_CN["at"] = 0
    assert names.series_display("project_sekai", [], None, "zh", cn=True) == "世界计划 彩色舞台 feat. 初音未来"
    assert "project_sekai" not in wikiname.need_cn()


def test_search_both_scripts(client):
    """簡體模式畫面上看到的名字（大陸譯名、簡體字），打這些字也找得到；切回繁體一樣找得到。"""
    from app import nameindex
    tid = db.tag_id("abarai_renji", "character")
    names.save_local({"abarai_renji": "阿散井戀次"})
    for m in ("cn", "tw"):
        _mode(client, m)
        nameindex.invalidate()
        assert tid in nameindex.exact("阿散井恋次") and tid in nameindex.exact("阿散井戀次")
    # 一般 tag 的中文對照：簡體模式看到的「视频」「单人女性」
    from app import query
    rev = query.zh_reverse()
    assert "1girl" in rev.get("单人女性", []) and "1girl" in rev.get("單人女性", [])
    names.save_local({"abarai_renji": None})


def test_ui_terms():
    """介面用語：跟前端同一份字典；轉兩次結果一樣（程式拿畫面上的字組新字串時不會變成「文档夹」）。"""
    from app import zhui
    assert zhui.conv("資料夾裡的影片") == "文件夹里的视频"
    assert zhui.conv("預設設定") == "默认设置"
    assert zhui.conv("請先登入帳號") == "请先登录账号"
    for s in ("資料夾裡的影片", "外接硬碟", "重新整理縮圖", "檔案總管"):
        once = zhui.conv(s)
        assert zhui.conv(once) == once, s
