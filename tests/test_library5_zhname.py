"""中文名（Bangumi）：名字完全一樣才用、同名的要對得上作品、對不上就讓你挑；換掉的看得到原本是什麼、可以改回；造型用本體的名字。"""
import json

from tests.conftest import ids_by_name


def _tag(name, cat, other_names, mid):
    from app import db
    with db.write_lock:
        c = db.conn()
        tid = db.tag_id(name, cat)
        c.execute("INSERT OR REPLACE INTO tag_info(tag_id, status, other_names, fetched_at) VALUES(?, 'ok', ?, 1)",
                  (tid, json.dumps(other_names, ensure_ascii=False)))
        c.execute("INSERT OR REPLACE INTO media_tags(media_id, tag_id, source, state) VALUES(?,?, 'manual', 1)", (mid, tid))
    return tid


def test_zhname_bangumi(client):
    from app import db, names, zhname
    mid = ids_by_name(client)["img_05.jpg"]
    _tag("tokoyami_towa", "character", ["常闇トワ", "常盤香菜", "常暗永遠"], mid)            # Danbooru 第一個中文別名是錯的
    _tag("tokoyami_towa_(1st_costume)", "character", ["常闇トワ(通常)"], mid)               # 造型
    _tag("viola_(bang_dream!)", "character", ["ビオラ"], mid)                             # 同名兩個：看作品
    _tag("bang_dream!", "copyright", ["バンドリ"], mid)
    _tag("rin_(zzz_test)", "character", ["リン"], mid)                                    # 名字短、作品對不上 → 你挑
    _tag("blue_archive_(test)", "copyright", ["ブルーアーカイブ"], mid)
    _tag("doctor_(arknights_test)", "character", ["ドクター", "博士"], mid)            # 只有一個同名、但作品對不上 → 不換
    assert names.display("tokoyami_towa", ["常闇トワ", "常盤香菜"], None, "zh") == "常盤香菜"
    assert "tokoyami_towa" in zhname.pending()
    assert "tokoyami_towa_(1st_costume)" not in zhname.pending(), "造型不查（用本體的）"
    st = {}
    zhname.run(["tokoyami_towa", "viola_(bang_dream!)", "rin_(zzz_test)", "blue_archive_(test)", "doctor_(arknights_test)"], st, lambda: False)
    assert st["found"] == 3, st
    assert names.display("tokoyami_towa", ["常闇トワ", "常盤香菜"], None, "zh") == "常暗永遠"
    assert names.display("viola_(bang_dream!)", ["ビオラ"], None, "zh") == "薇奧拉"
    assert names.display("tokoyami_towa_(1st_costume)", ["常闇トワ(通常)"], None, "zh") == "常暗永遠（初始服裝）"
    assert names.series_display("blue_archive_(test)", ["ブルーアーカイブ"], None, "zh") == "蔚藍檔案"
    assert names.display("rin_(zzz_test)", ["リン"], None, "zh") == "Rin", "對不上作品：不自動換；沒中文就用英文，不用日文"
    assert names.display("doctor_(arknights_test)", ["ドクター", "博士"], None, "zh") == "博士", "名字夠長但作品對不上：也不換"
    assert names.display("tokoyami_towa", ["常闇トワ"], "我取的", "zh") == "我取的", "你取的名字永遠優先"
    # 搜尋用新名字也找得到
    assert db.tag_id("tokoyami_towa", create=False) in __import__("app.nameindex", fromlist=["x"]).exact("常闇永遠") or \
        db.tag_id("tokoyami_towa", create=False) in __import__("app.nameindex", fromlist=["x"]).exact("常暗永遠")
    # 中文名整理：自動換的（原本是什麼）、你挑
    r = client.get("/api/names/review", params={"kind": "auto"}).json()
    row = next(x for x in r["items"] if x["name"] == "tokoyami_towa")
    assert row["bgm"]["prev"] == "常盤香菜" and row["display"] == "常暗永遠" and not row["bgm"]["seen"]
    p = client.get("/api/names/review", params={"kind": "pick"}).json()
    assert not any(x["name"] == "rin_(zzz_test)" for x in p["items"]), "同名的都不是這部作品：不列（不是候選）"
    s = client.get("/api/names/review", params={"kind": "auto", "cat": "copyright"}).json()
    assert any(x["name"] == "blue_archive_(test)" for x in s["items"]), s["counts"]
    # 改回（＝把原本的名字存成你取的）、看過
    assert client.post("/api/tag/tokoyami_towa", json={"display_name": "常盤香菜"}).status_code == 200
    assert client.post("/api/names/seen", json={"names": ["viola_(bang_dream!)"]}).json()["seen"] == 1
    r2 = client.get("/api/names/review", params={"kind": "done"}).json()
    assert any(x["name"] == "tokoyami_towa" for x in r2["items"])
    client.post("/api/tag/tokoyami_towa", json={"display_name": ""})
    # 查過的不再排；線上關著時不排
    assert "tokoyami_towa" not in zhname.pending()
    assert client.post("/api/names/bangumi", json={}).status_code == 400


def test_jp_only_rule():
    from app import names
    assert names._chinese_ok("奥提斯"), "奥 也是簡體字"
    assert not names._chinese_ok("桜羽")


def test_zhname_only_japanese_queries():
    from app import zhname
    assert zhname.ja_names(["雷电将军", "RaidenShogun", "巴尔泽布", "雷電将軍", "雷神バアル"]) == ["雷電将軍", "雷神バアル"]
    assert zhname.ja_names(["流浪者", "国崩", "散兵", "放浪者(原神)"]) == []
    assert zhname.ja_names(["千夏(ゼンゼロ)", "ちなつレミエル", "千夏"]) == ["ちなつレミエル"]


def test_zhname_keep_danbooru_for_chinese_games():
    from app import zhname
    assert zhname.keep_danbooru("character", ["玉藻前", "タマモ"], "新闻主播")            # 第一個別名就是中文 → 留 Danbooru
    assert zhname.keep_danbooru("character", ["千夏(ゼンゼロ)", "千夏蕾米尔"], "千夏")      # Bangumi 的比較短
    assert not zhname.keep_danbooru("character", ["常闇トワ", "常盤香菜"], "常暗永遠")       # 第一個是日文 → 用 Bangumi
    assert not zhname.keep_danbooru("copyright", ["原神"], "原神")
    assert zhname.keep_danbooru("copyright", ["ポケモンSV", "宝可梦朱紫"], "宝可梦 朱"), "作品：Danbooru 有中文就留著"
    assert not zhname.keep_danbooru("copyright", ["ポケモンSV"], "宝可梦 朱")


def test_zhname_paren_names_last():
    from app import zhname
    assert zhname.ja_names(["キャスター(Fate/EXTRA)", "玉藻の前", "玉藻前"]) == ["玉藻の前"]
    assert zhname.ja_names(["アーニャ(SPY×FAMILY)"]) == ["アーニャ"], "只有帶括號的才用括號前的名字"


def test_zhname_wikidata_and_next_query(client):
    """Wikidata 的台灣譯名優先；Bangumi 第一個名字對不上就查下一個（キリト → 桐ヶ谷和人）；同名的看說明裡的作品。"""
    from app import names, zhname
    from app import db
    ms = [r[0] for r in db.conn().execute("SELECT id FROM media WHERE status='done' ORDER BY id LIMIT 3")]
    mid, mid2, mid3 = ms
    _tag("spy_x_family", "copyright", ["SPY×FAMILY"], mid)
    _tag("anya_(spy_x_family)", "character", ["アーニャ(SPY×FAMILY)", "アーニャ・フォージャー", "アーニャ"], mid)
    _tag("sword_art_online", "copyright", ["ソードアート・オンライン"], mid2)
    _tag("kirito", "character", ["キリト", "キリト(SAO)", "桐ヶ谷和人"], mid2)
    _tag("pokemon_swsh_test", "copyright", ["Pokémon Sword and Shield"], mid3)
    _tag("marnie_(pokemon_test)", "character", ["マリィ"], mid3)
    st = {}
    zhname.run(["anya_(spy_x_family)", "kirito", "marnie_(pokemon_test)"], st, lambda: False)
    assert names.display("anya_(spy_x_family)", [], None, "zh") == "安妮亞·佛傑"
    assert names.display("kirito", [], None, "zh") == "桐人"
    assert names.display("marnie_(pokemon_test)", [], None, "zh") == "瑪俐", "三個同名：說明裡是寶可夢的那個"
    assert zhname.norm("Pokémon SPY×FAMILY") == zhname.norm("pokemon spyxfamily")


def test_to_trad_name_chars():
    """b72：OpenCC 會把人名用字轉錯（栗→慄、明里→明裡、洛托姆→洛託姆、冢、岩→巖）；片語裡的「里」「于」照樣轉。"""
    from app import names
    if names._cc() is None:
        return
    assert names.to_trad("栗村爱理") == "栗村愛理"
    assert names.to_trad("栗滨明里") == "栗濱明里"
    assert names.to_trad("手机洛托姆") == "手機洛托姆"
    assert names.to_trad("猫冢响") == "貓塚響"
    assert names.to_trad("喜多郁代") == "喜多郁代"
    assert names.to_trad("我心里危险的东西") == "我心裡危險的東西"
    assert names.to_trad("关于我转生变成史莱姆这档事") == "關於我轉生變成史萊姆這檔事"
    assert names.to_trad("湊阿夸") == "湊阿夸"


def test_curated_table(tmp_path, monkeypatch):
    """校對過的譯名表：手動 > 校對表 > 其他；作品也一樣。表裡的 JSON 要能讀、沒有重複或空白的名字。"""
    import json
    from pathlib import Path
    from app import names
    real = json.loads((Path(names.__file__).parent / "data" / "zh_curated.json").read_text("utf-8"))
    for sec in ("character", "copyright"):
        assert real[sec] and all(v.strip() == v and v for v in real[sec].values())
    p = tmp_path / "c.json"
    p.write_text(json.dumps({"character": {"curated_test_char": "常闇永遠"}, "copyright": {"x_series": "某作品"}}, ensure_ascii=False), "utf-8")
    monkeypatch.setenv("LIB_ZH_CURATED", str(p))
    names._curated.cache_clear()
    try:
        assert names.display("curated_test_char", ["常闇トワ", "常盤香菜"], None, "zh") == "常闇永遠"
        assert names.display("curated_test_char", ["常闇トワ", "常盤香菜"], "我取的", "zh") == "我取的"
        assert names.display("curated_test_char", ["常闇トワ", "常盤香菜"], None, "zh", use_curated=False) == "常盤香菜"
        assert names.series_display("x_series", [], None, "zh") == "某作品"
        assert names.display("curated_test_char", [], None, "raw") == "curated_test_char"
    finally:
        monkeypatch.delenv("LIB_ZH_CURATED")
        names._curated.cache_clear()


def test_curated_paste_and_unreviewed(client, tmp_path, monkeypatch):
    """長期校對：還沒校對的列出來（附給 Claude 的說明）→ 貼回 tag<Tab>名字 → 存在 data/zh_curated_local.json、馬上生效；- ＝沒有中文名。"""
    import json
    from app import names
    mid = ids_by_name(client)["img_05.jpg"]
    _tag("paste_test_char_(x)", "character", [], mid)
    _tag("paste_test_none", "character", [], mid)
    names._tables.cache_clear()
    r = client.get("/api/names/unreviewed?limit=500").json()
    assert "paste_test_char_(x)" in r["text"] and "台灣官方譯名" in r["text"]
    r = client.post("/api/names/curated", json={"text": "paste_test_char_(x)\t測試角色\n| paste_test_none | - |\nno_such_tag\t不存在\n"}).json()
    assert r["saved"] == 1 and r["none"] == 1 and r["unknown"] == ["no_such_tag"]
    assert names.display("paste_test_char_(x)", [], None, "zh") == "測試角色"
    assert names.reviewed("paste_test_none") and not names.curated("paste_test_none")
    d = json.loads(names.local_path().read_text("utf-8"))
    assert d["names"]["paste_test_char_(x)"] == "測試角色"
    r = client.get("/api/names/unreviewed?limit=500").json()
    assert "paste_test_char_(x)" not in r["text"] and "paste_test_none" not in r["text"]
    assert client.get("/api/names/review?kind=fixed").json()["counts"]["fixed"] >= 1
    names.local_path().unlink()
    names._tables.cache_clear()
