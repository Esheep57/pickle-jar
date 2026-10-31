"""測試用的假 Danbooru / IQDB / Pixiv。MD5 由環境變數 MOCK_MD5 指定（那張圖會被當成 Danbooru 原檔）。"""
import io
import os

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response

MD5 = os.environ.get("MOCK_MD5", "")
app = FastAPI()
CHARS = {"akiyama_mizuki": "project_sekai", "hatsune_miku": "vocaloid", "izmir": "arknights", "gojo_satoru": "jujutsu_kaisen"}
WIKI = {"gojo_satoru": {"title": "gojo_satoru", "body": "x", "other_names": ["五条悟"], "is_deleted": False}, "akiyama_mizuki": {"title": "akiyama_mizuki", "body": "A [[Project Sekai]] character. A student of [[Kamiyama High School uniform (project sekai)|Kamiyama High School]] and the animator of [[25-Ji Nightcord de. (project sekai)|]].\r\n\r\nh4. See also\r\n* [[x]]", "other_names": ["暁山瑞希", "みずえな", "晓山瑞希"], "is_deleted": False},
        "project_sekai": {"title": "project_sekai", "body": "A rhythm game.", "other_names": ["プロジェクトセカイ", "世界计划", "프로젝트 세카이"], "is_deleted": False},
        "vocaloid": {"title": "vocaloid", "body": "Voice synth.", "other_names": ["ボーカロイド", "虚拟歌姬"], "is_deleted": False},
        "hatsune_miku": {"title": "hatsune_miku", "body": "The first [[Vocaloid]] of the Character Vocal Series.", "other_names": ["初音ミク", "初音未來"], "is_deleted": False}}


def post(i, chars="akiyama_mizuki", cp="project_sekai"):
    return {"id": i, "md5": MD5, "source": "https://www.pixiv.net/artworks/11223344", "pixiv_id": 11223344, "rating": "s",
            "tag_string_character": chars, "tag_string_copyright": cp, "tag_string_artist": "some_artist",
            "tag_string_general": "1girl pink_hair", "image_width": 1000, "image_height": 1400}


# Danbooru 角色表（b109）：作品、角色照張數排、一頁 1000 筆（測試資料很少：第 2 頁開始是空的）
CAT_CP = [("blue_archive", 500000), ("kantai_collection", 300000), ("vocaloid", 200000), ("cat_small_work", 12), ("cat_tiny_work", 3)]
CAT_CH = [("cat_hero_(blue_archive)", 20000), ("cat_ship_(kancolle)", 9000), ("cat_popular", 5000), ("cat_obscure", 40), ("cat_rare", 5)]
CAT_WIKI = {"cat_hero_(blue_archive)": ["聖園ミカ", "Mika"], "cat_popular": ["人気キャラ", "人氣角色"], "cat_obscure": []}


@app.get("/tags.json")
def tags(request: Request):
    q = request.query_params
    if q.get("search[category]") and q.get("search[order]") == "count":
        if q.get("page", "1") != "1":
            return []
        rows = CAT_CP if q["search[category]"] == "3" else CAT_CH
        return [{"name": n, "post_count": c} for n, c in rows]
    if request.query_params.get("search[name_comma]"):
        return dan_tags(request)
    n = request.query_params.get("search[name]")
    if n == "some_artist":
        return [{"name": n, "category": 1, "post_count": 321}]
    return [{"name": n, "category": 4, "post_count": 5000}] if n in CHARS else []


@app.get("/wiki_pages.json")
def wiki_search(request: Request):
    if request.query_params.get("search[tag][category]") == "4":       # 角色表：角色的維基別名
        if request.query_params.get("page", "1") != "1":
            return [{"title": "someone_else", "other_names": []}]       # 整頁都不是要的角色 → 停
        return [{"title": k, "other_names": v} for k, v in CAT_WIKI.items()] + [{"title": "not_in_catalog", "other_names": ["x"]}]
    exact = request.query_params.get("search[other_names_include_any]")
    if exact is not None:        # 日文標籤 → Danbooru：其他名稱完全相同
        return [{"title": k, "other_names": v["other_names"], "is_deleted": False} for k, v in WIKI.items() if exact in v["other_names"]]
    q = request.query_params.get("search[other_names_match]", "")
    return [{"title": k, "other_names": v["other_names"]} for k, v in WIKI.items() if any(q in n for n in v["other_names"])]


@app.get("/wiki_pages/{name}.json")
def wiki(name: str):
    return WIKI[name] if name in WIKI else JSONResponse({"success": False}, 404)


LATER = {"on": False}


@app.post("/_later")
def later_on():
    LATER["on"] = True
    return {"ok": True}


@app.get("/artists.json")
def artists(request: Request):
    n = request.query_params.get("search[name]")
    u = request.query_params.get("search[url_matches]")
    if n == "some_artist":
        return [{"id": 9, "name": "some_artist", "other_names": ["某畫師", "someart"], "is_deleted": False,
                 "urls": [{"url": "https://www.pixiv.net/users/777", "is_active": True}, {"url": "https://twitter.com/someart", "is_active": True}, {"url": "https://old.example.com", "is_active": False}]}]
    if u and u.lower() == "https://twitter.com/someart":   # 簽名辨識：X 帳號反查
        return [{"id": 9, "name": "some_artist", "other_names": [], "is_deleted": False,
                 "urls": [{"url": "https://twitter.com/SomeArt", "is_active": True}]}]
    if u and u.lower() == "https://twitter.com/fuzzy":      # Danbooru 模糊比對到別人 → 不能採用
        return [{"id": 11, "name": "other_artist", "other_names": [], "is_deleted": False,
                 "urls": [{"url": "https://twitter.com/fuzzy_other", "is_active": True}]}]
    if u and u.lower() in ("https://twitter.com/later_art", "https://x.com/later_art") and LATER["on"]:   # 之後才在 Danbooru 登記
        return [{"id": 12, "name": "later_official", "other_names": [], "is_deleted": False,
                 "urls": [{"url": "https://twitter.com/later_art", "is_active": True}]}]
    if u == "https://www.pixiv.net/users/2":
        return [{"id": 10, "name": "mapped_artist", "other_names": ["マップ"], "is_deleted": False, "urls": [{"url": "https://www.pixiv.net/users/2"}]}]
    return []


@app.get("/posts.json")
def posts(request: Request):
    t = request.query_params.get("tags", "")
    if t == "cat_popular":           # 角色表：熱門角色抽樣貼文查作品
        return [{"tag_string_copyright": "vocaloid"}] * 5 + [{"tag_string_copyright": "vocaloid project_diva"}]
    if t.startswith("md5:"):
        return [post(555)] if t[4:] == MD5 else []
    if t.startswith("id:"):          # 補抓一般標籤：一次很多篇
        return [{"id": int(x), "tag_string_general": "1girl pink_hair"} for x in t[3:].split(",") if x]
    if t.startswith("pixelhash:"):   # 像素指紋：測試指定的那個指紋 → 一個貼文
        out = []
        for h in t[10:].split(","):
            if h in PIXHASH:
                p = post(PIXHASH[h], "hatsune_miku", "vocaloid"); p["media_asset"] = {"pixel_hash": h}
                out.append(p)
        return out
    if t == "source:*/status/1790000000000000001*":
        return [post(700, "ceres_fauna", "hololive")]
    if t == "pixiv:98765432":
        p = post(600, "akiyama_mizuki", "project_sekai"); p["pixiv_id"] = 98765432; p["tag_string_artist"] = "mizuki_painter"
        return [p]
    if t == "scan_artist":   # 從畫師反推：第一張是本機某張圖的縮圖（同一張），第二張是別的圖
        q = request.query_params
        if q.get("page", "1") != "1":
            return []
        return [{"id": 901, "md5": "x" * 32, "image_width": 400, "image_height": 560, "preview_file_url": BASE + "/preview/0.jpg",
                 "tag_string_artist": "scan_artist", "tag_string_character": "hatsune_miku", "tag_string_copyright": "vocaloid", "source": ""},
                {"id": 902, "md5": "y" * 32, "image_width": 400, "image_height": 560, "preview_file_url": BASE + "/preview/1.jpg",
                 "tag_string_artist": "scan_artist", "tag_string_character": "", "tag_string_copyright": "", "source": ""}]
    if t in CHARS:
        return [{"tag_string_copyright": CHARS[t] + (" original" if i % 5 == 0 else "")} for i in range(40)]
    return []


BASE = os.environ.get("LIB_DANBOORU", "")
PREVIEWS: dict = {}
PIXHASH: dict = {}


@app.get("/preview/{k}.jpg")
def preview(k: int):
    from fastapi.responses import Response
    return Response(PREVIEWS.get(k, b""), media_type="image/jpeg")


def _png(seed: int, fmt: str = "PNG") -> bytes:
    import io
    import random
    from PIL import Image, ImageDraw
    rnd = random.Random(seed)
    img = Image.new("RGB", (500, 700), tuple(rnd.randrange(256) for _ in range(3)))
    d = ImageDraw.Draw(img)
    for _ in range(12):
        x, y, r = rnd.randrange(500), rnd.randrange(700), rnd.randrange(20, 90)
        d.ellipse((x - r, y - r, x + r, y + r), fill=tuple(rnd.randrange(256) for _ in range(3)))
    b = io.BytesIO()
    img.save(b, fmt)
    return b.getvalue()


# 假 trace.moe：上傳的圖寬度 ≥ 500（縮成 600px 以內，16:9 的測試圖）→ 某部動畫的畫面
TRACE_CALLS = []


@app.post("/tracemoe/search")
async def trace_search(request: Request):
    body = await request.body()                    # multipart 裡的 JPEG（不依賴 python-multipart）
    data = body[body.find(b"\xff\xd8"):body.rfind(b"\xff\xd9") + 2]
    TRACE_CALLS.append(len(data))
    TRACE_LAST["img"] = data
    from PIL import Image
    w, h = Image.open(io.BytesIO(data)).size
    if w / h > 1.6:
        return {"frameCount": 1, "error": "", "result": [{"anilist": {"id": 130003, "title": {"native": "ぼっち・ざ・ろっく！", "romaji": "Bocchi the Rock!", "english": "BOCCHI THE ROCK!"}, "synonyms": []},
                "filename": "x.mp4", "episode": 3, "from": 754.2, "to": 756.0, "similarity": 0.953, "image": BASE + "/tracemoe/preview.jpg"}]}
    if w / h > 1.2:     # 相似度高、但畫面其實不一樣（同人圖撞到相似的動畫畫面）
        return {"frameCount": 1, "error": "", "result": [{"anilist": {"id": 777, "title": {"native": "別的動畫"}}, "episode": 2, "from": 5,
                "similarity": 0.97, "image": BASE + "/tracemoe/other.jpg"}]}
    return {"frameCount": 1, "error": "", "result": [{"anilist": {"id": 1, "title": {"native": "x"}}, "episode": 1, "from": 1, "similarity": 0.62}]}


TRACE_LAST: dict = {}


@app.get("/tracemoe/preview.jpg")
def trace_preview():
    return Response(TRACE_LAST.get("img") or _png(1, "JPEG"), media_type="image/jpeg")


@app.get("/tracemoe/other.jpg")
def trace_other():
    return Response(_png(77, "JPEG"), media_type="image/jpeg")


@app.get("/tracemoe/calls")
def trace_calls():
    return {"n": len(TRACE_CALLS)}


# 從網站匯入：假 FxTwitter、Pixiv 原圖、Danbooru 原檔
@app.get("/fx/status/{tid}")
def fx_status(tid: str):
    if tid == "1790000000000000002":     # Danbooru 還沒登記的作者
        return {"code": 200, "tweet": {"text": "", "author": {"screen_name": "Later_Art", "name": "新人繪師"},
                "media": {"all": [{"type": "photo", "url": BASE + "/media/LaTeRaRt12345Xy.jpg"}]}}}
    if tid == "1790000000000000001":
        return {"code": 200, "tweet": {"text": "新圖 #初音ミク #らくがき", "possibly_sensitive": True, "author": {"screen_name": "someart", "name": "Some Art"},
                "media": {"all": [{"type": "photo", "url": BASE + "/media/AbCdEfGh12345Xy.jpg"}, {"type": "photo", "url": BASE + "/media/ZyXwVu98765AbCd.jpg"}]}}}
    return JSONResponse({"code": 404, "message": "NOT_FOUND"}, 404)


@app.get("/media/{key}.jpg")
def media_file(key: str):
    return Response(_png(sum(map(ord, key)), "JPEG"), media_type="image/jpeg")


@app.get("/ajax/illust/{pid}/pages")
def pixiv_pages(pid: int):
    if pid == 55556666:
        return {"error": False, "body": [{"urls": {"original": BASE + f"/img/{pid}_p0.png"}}]}
    if pid == 55558888:     # 四頁的漫畫（b110.16 存成本子）
        return {"error": False, "body": [{"urls": {"original": BASE + f"/img/{pid}_p{i}.png"}} for i in range(4)]}
    if pid == 55557777:     # 三頁的作品（新式檔名有雜湊）：測只存其中幾頁
        return {"error": False, "body": [{"urls": {"original": BASE + f"/img/{pid}-{'ab' * 16}_p{i}.png"}} for i in range(3)]}
    return {"error": True, "body": []}


@app.get("/img/{name}")
def pixiv_img(name: str, request: Request):
    if request.headers.get("referer") != "https://www.pixiv.net/":
        return Response(b"", status_code=403)          # 跟真的 Pixiv 一樣：沒帶 Referer 就拒絕
    return Response(_png(7 if name.startswith("55556666") else sum(map(ord, name))), media_type="image/png")


@app.get("/dl/{name}")
def dl_file(name: str):
    return Response(open(os.environ["MOCK_ORIG_PATH"], "rb").read(), media_type="image/jpeg")


@app.get("/cdn/{name}")
def cdn_file(name: str, request: Request):
    """Danbooru 的圖片伺服器：自稱瀏覽器（Mozilla…）但不是 → Cloudflare 擋下（403）。"""
    if "Mozilla" in request.headers.get("user-agent", ""):
        return Response("blocked", 403)
    return Response(_png(31), media_type="image/png")


UP: dict = {}     # 換成高清版的測試：post id → {"file": 路徑, "w", "h", "ext"}（測試直接改這個 dict）


@app.get("/up/{pid}")
def up_file(pid: int):
    u = UP.get(pid)
    if not u:
        return Response(b"", status_code=404)
    return Response(open(u["file"], "rb").read(), media_type="image/" + ("png" if u["ext"] == "png" else "jpeg"))


@app.get("/posts/{pid}.json")
def one(pid: int):  # noqa
    d = post(pid, "hatsune_miku", "vocaloid")
    if pid in UP:
        u = UP[pid]
        d.update(file_url=BASE + f"/up/{pid}", file_ext=u["ext"], image_width=u["w"], image_height=u["h"], md5="u" * 32)
    if pid == 4343:         # 從網站匯入：新的原檔，放在會擋假瀏覽器的 CDN
        d.update(file_url=BASE + "/cdn/new.png", file_ext="png", md5="c" * 32)
    if pid == 4242:         # 從網站匯入：原檔（跟圖庫裡的 danbooru_original.jpg 一模一樣 → 應該判斷成「已經有了」）
        d.update(file_url=BASE + "/dl/orig.jpg", file_ext="jpg")
    if pid in (901, 902):   # 從畫師反推：大圖確認用
        d["large_file_url"] = BASE + f"/preview/{pid - 901}.jpg"
    return d


@app.get("/profile.json")
def profile(request: Request):
    if request.query_params.get("api_key") == "good":
        return {"name": request.query_params.get("login")}
    return JSONResponse({"success": False, "message": "SessionLoader::AuthenticationFailure"}, 401)


@app.post("/iqdb_queries.json")
async def dan_iqdb(request: Request):
    if request.query_params.get("api_key") != "good":
        return JSONResponse({"success": False}, 401)
    return [{"post_id": 777, "score": 95.0}]


@app.post("/iqdb")
async def iqdb(request: Request):
    form = await request.form()
    assert form["file"].filename
    return HTMLResponse("""<html><body><div id='pages'><div><table><tr><th>Your image</th></tr><tr><td>x</td></tr></table></div>
<div><table><tr><th>Best match</th></tr><tr><td class='image'><a href="//danbooru.donmai.us/posts/777"><img src='/x.jpg'></a></td></tr>
<tr><td><img class="service-icon">Danbooru</td></tr><tr><td>1000×1400 [Safe]</td></tr><tr><td>93% similarity</td></tr></table></div>
<div><table><tr><th>Additional match</th></tr><tr><td class='image'><a href="https://danbooru.donmai.us/posts/778"><img></a></td></tr><tr><td>41% similarity</td></tr></table></div></div></body></html>""")


@app.get("/ajax/illust/{pid}")
def pixiv(pid: int):
    if pid == 98765432:
        return {"error": False, "message": "", "body": {"illustId": str(pid), "illustTitle": "瑞希ちゃん", "userId": "1", "userName": "絵師A", "xRestrict": 1, "aiType": 1,
                "createDate": "2024-05-01T00:00:00+09:00", "tags": {"tags": [{"tag": "暁山瑞希", "translation": {"en": "Akiyama Mizuki"}}, {"tag": "プロセカ"}]}}}
    if pid == 55556666:
        return {"error": False, "body": {"illustTitle": "t2", "userId": "2", "userName": "マップさん", "xRestrict": 0, "tags": {"tags": [{"tag": "オリジナル"}]}}}
    if pid == 55558888:
        return {"error": False, "body": {"illustTitle": "漫画テスト", "userId": "2", "userName": "マップさん", "xRestrict": 0, "illustType": 1,
                                         "createDate": "2026-10-01T00:00:00+09:00", "tags": {"tags": [{"tag": "オリジナル"}]}}}
    if pid == 55557777:
        return {"error": False, "body": {"illustTitle": "t3", "userId": "2", "userName": "マップさん", "xRestrict": 0, "tags": {"tags": [{"tag": "オリジナル"}]}}}
    if pid == 11223344:
        return {"error": False, "body": {"illustTitle": "miku", "userName": "B", "xRestrict": 0, "tags": {"tags": [{"tag": "初音ミク"}]}}}
    return {"error": True, "message": "該当作品は削除されたか、存在しない作品IDです。", "body": []}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=7900, log_level="warning")


# 假維基百科：只認得「プロジェクトセカイ」（完全同名）和一個會被導向到別處的條目
@app.get("/wiki/w/api.php")
def wikipedia(request: Request):
    q = request.query_params
    if q.get("action") == "parse":
        if q.get("variant") == "zh-cn":      # 同一個條目的大陆简体標題（簡體模式）
            return {"parse": {"title": q["page"], "displaytitle": '<span lang="zh-Hans-CN"><span class="mw-page-title-main">世界计划 彩色舞台 feat. 初音未来</span></span>'}}
        return {"parse": {"title": q["page"], "displaytitle": '<span lang="zh-Hant-TW"><span class="mw-page-title-main">世界計畫 彩色舞台 feat. 初音未來</span></span>'}}
    t = q.get("titles", "")
    if t == "プロジェクトセカイ":
        return {"query": {"pages": {"1": {"pageid": 1, "title": t, "langlinks": [{"lang": "zh", "*": "世界计划"}], "pageprops": {}}}}}
    if t == "ボーカロイド":
        return {"query": {"redirects": [{"from": t, "to": "VOCALOID (ソフトウェア)"}],
                          "pages": {"2": {"pageid": 2, "title": "VOCALOID (ソフトウェア)", "langlinks": [{"lang": "zh", "*": "VOCALOID"}]}}}}
    return {"query": {"pages": {"-1": {"title": t, "missing": ""}}}}


E621_MD5: dict = {}  # 測試指定：MD5 → e621 貼文 id
E621_IQDB: list = []  # 測試指定：以圖搜圖的回傳


def e621_post(pid):
    return {"id": pid, "file": {"md5": next((k for k, v in E621_MD5.items() if v == pid), "0" * 32)}, "rating": "e",
            "sources": ["https://www.furaffinity.net/view/1/"],
            "tags": {"artist": ["some_artist", "conditional_dnp"], "character": ["oc_fox_(character)"], "species": ["lucario", "canine"],
                     "copyright": ["pokemon", "nintendo"], "general": ["anthro"], "meta": []}}


@app.get("/e621/posts.json")
def e621_posts(request: Request):
    t = request.query_params.get("tags", "")
    if request.headers.get("user-agent", "").find("PickleJar") < 0:
        return JSONResponse({"success": False}, 403)
    if t.startswith("md5:"):
        return {"posts": [e621_post(E621_MD5[h]) for h in t[4:].split(",") if h in E621_MD5]}
    return {"posts": []}


@app.get("/e621/posts/{pid}.json")
def e621_one(pid: int):
    return {"post": e621_post(pid)}


@app.post("/e621/iqdb_queries.json")
async def e621_iqdb(request: Request):
    form = await request.form()
    assert form.get("file") is not None
    return E621_IQDB


R34_MD5: dict = {}   # 測試指定：MD5 → Rule34 貼文


@app.get("/r34/index.php")
def r34(request: Request):
    q = request.query_params
    if q.get("api_key") != "r34key" or q.get("user_id") != "42":
        return Response('"Missing authentication. Go to api.rule34.xxx for more information"', media_type="application/json")
    if q.get("s") == "tag":
        types = {"some_artist": 1, "hatsune_miku": 4, "vocaloid": 3, "miku_(r34_style)": 4, "weird_artist": 1, "old_miku_name": 4}
        n = q.get("name", "")   # 真的 Rule34：一次只能查一個（names 參數會被忽略）
        rows = f'<tag type="{types.get(n, 0)}" count="5" name="{n}" id="1"/>' if n else ""
        return Response(f'<?xml version="1.0" encoding="UTF-8"?><tags type="array">{rows}</tags>', media_type="text/xml")
    t = q.get("tags", "")
    if t.startswith("md5:") and t[4:] in R34_MD5:
        return [{"id": R34_MD5[t[4:]], "tags": "1girl some_artist hatsune_miku vocaloid miku_(r34_style) weird_artist patreon old_miku_name chilling",
                 "rating": "explicit", "source": "https://x.com/someart/status/1"}]
    return Response("", media_type="application/json")


def dan_tags(request: Request):   # Danbooru 標籤是否存在（Rule34 標籤對應用）
    known = {"some_artist": 1, "hatsune_miku": 4, "vocaloid": 3, "patreon": 3, "lucario": 4, "pokemon": 3, "nintendo": 3, "canine": 0,
             "1girl": 0, "pink_hair": 0, "akiyama_mizuki": 4, "project_sekai": 3, "bocchi_the_rock!": 3}
    names_ = request.query_params.get("search[name_comma]", "").split(",")
    return [{"name": n, "category": known[n], "post_count": 100} for n in names_ if n in known]


# ---------- 假 E-Hentai／nhentai ----------
EH_PAGES = {1: "aaaaaaaaa1", 2: "aaaaaaaaa2", 3: "aaaaaaaaa3"}


@app.get("/eh/g/{gid}/{tok}/")
def eh_gallery(gid: int, tok: str, p: int = 0):
    if gid == 404404:
        return HTMLResponse("Key missing, or incorrect key provided.")
    tags = '<a href="https://e-hentai.org/tag/parody:vocaloid">x</a><a href="https://e-hentai.org/tag/character:hatsune+miku">x</a>' \
           '<a href="https://e-hentai.org/tag/artist:some+artist">x</a><a href="https://e-hentai.org/tag/group:circle+nameless">x</a>' \
           '<a href="https://e-hentai.org/tag/female:twintails">x</a>'
    links = "".join(f'<a href="https://e-hentai.org/s/{EH_PAGES[n]}/{gid}-{n}"><img></a>' for n in ([1, 2] if p == 0 else [3]))
    return HTMLResponse(f'<h1 id="gn">[Circle] Test Book (Vocaloid)</h1><h1 id="gj">テスト本</h1>'
                        f'<td class="gdt1">Length:</td><td class="gdt2">3 pages</td>{tags}{links}')


@app.get("/eh/s/{tok}/{gp}")
def eh_page(tok: str, gp: str):
    n = gp.split("-")[1]
    return HTMLResponse(f'<div id="i3"><img id="img" src="{BASE}/ehimg/keystamp=1;xres=1280/page{n}.webp" /></div>')


@app.get("/ehimg/{rest:path}")
def eh_img(rest: str):
    return Response(_png(sum(map(ord, rest)) + 7), media_type="image/png")


@app.get("/nh/api/v2/galleries/{gid}")
def nh_api(gid: int):
    if gid == 403403:
        return HTMLResponse("<html>Just a moment...</html>", status_code=403)
    return {"id": gid, "media_id": "999", "title": {"english": "NH Book", "japanese": "エヌ本", "pretty": "NH Book"},
            "tags": [{"type": "parody", "name": "vocaloid"}, {"type": "character", "name": "hatsune miku"}, {"type": "tag", "name": "full color"},
                     {"type": "artist", "name": "some artist"}],
            "pages": [{"path": f"galleries/999/{i}.jpg", "width": 10, "height": 10} for i in (1, 2)]}


@app.get("/nhimg/galleries/{mid}/{name}")
def nh_img(mid: str, name: str):
    return Response(_png(sum(map(ord, name)) + 13, "JPEG"), media_type="image/jpeg")


@app.get("/tag_implications.json")
def tag_implications(request: Request):   # 作品的母系列
    rel = {"limbus_company": "project_moon", "library_of_ruina": "project_moon",
           "impvar_(bunny)_(tst)": "impbase_(tst)"}       # 角色造型 ⇒ 本體（標籤蘊含，b84）
    names_ = request.query_params.get("search[antecedent_name_comma]", "").split(",")
    return [{"antecedent_name": n, "consequent_name": rel[n]} for n in names_ if n in rel]


FB_DL = "http://127.0.0.1:7931/fbdl"
@app.get("/tag_aliases.json")
def tag_aliases(request: Request):
    if request.query_params.get("search[consequent_tag][category]") == "3":     # 角色表：指到作品的別名
        return [] if request.query_params.get("page", "1") != "1" else [{"antecedent_name": "kancolle", "consequent_name": "kantai_collection"}]
    a = request.query_params.get("search[antecedent_name]")
    many = request.query_params.get("search[antecedent_name_comma]")
    if many:   # Rule34 用的舊名字
        rel = {"old_miku_name": "hatsune_miku", "gojou_satoru": "gojo_satoru", "chilling": "some_artist"}
        return [{"antecedent_name": n, "consequent_name": rel[n]} for n in many.split(",") if n in rel]
    return [{"antecedent_name": a, "consequent_name": "gojo_satoru", "status": "active"}] if a == "gojou_satoru" else []


# ---------- 假 Bangumi（zhname.py）：/bgm/v0/... ----------
BGM_CHARS = {
    "聖園ミカ": [{"id": 60, "name": "聖園ミカ", "infobox": [{"key": "简体中文名", "value": "圣园未花"}]}],
    "常闇トワ": [{"id": 1, "name": "常闇トワ", "infobox": [{"key": "简体中文名", "value": "常暗永远"}]}],
    "ビオラ": [{"id": 2, "name": "ビオラ", "infobox": [{"key": "简体中文名", "value": "薇奥拉"}]},
              {"id": 3, "name": "ビオラ", "infobox": [{"key": "简体中文名", "value": "维奥拉"}]}],
    "リン": [{"id": 4, "name": "リン", "infobox": [{"key": "简体中文名", "value": "铃"}]}],
    "キリト": [{"id": 6, "name": "キリト", "infobox": []}],
    "桐ヶ谷和人": [{"id": 7, "name": "桐ヶ谷和人", "infobox": [{"key": "简体中文名", "value": "桐人"}]}],
    "ドクター": [{"id": 5, "name": "ドクター", "infobox": [{"key": "简体中文名", "value": "医生"}]}],
}
BGM_SUBJ = {2: [{"id": 90, "name": "BanG Dream! Ave Mujica", "name_cn": "BanG Dream! Ave Mujica", "type": 2}],
            3: [{"id": 91, "name": "別のゲーム", "name_cn": "别的游戏", "type": 4}],
            4: [{"id": 92, "name": "無関係", "name_cn": "无关", "type": 2}],
            7: [{"id": 94, "name": "ソードアート・オンライン", "name_cn": "刀剑神域", "type": 2}],
            5: [{"id": 93, "name": "別の医者アニメ", "name_cn": "别的医生动画", "type": 2}]}


@app.post("/bgm/v0/search/characters")
async def bgm_chars(request: Request):
    k = (await request.json()).get("keyword", "")
    return {"data": BGM_CHARS.get(k, []), "total": len(BGM_CHARS.get(k, []))}


@app.get("/bgm/v0/characters/{cid}/subjects")
def bgm_char_subjects(cid: int):
    return BGM_SUBJ.get(cid, [])


@app.post("/bgm/v0/search/subjects")
async def bgm_subjects(request: Request):
    k = (await request.json()).get("keyword", "")
    if k == "ブルーアーカイブ":
        return {"data": [{"id": 50, "name": "ブルーアーカイブ", "name_cn": "蔚蓝档案", "type": 4}]}
    return {"data": []}


# ---------- 假 Wikidata（zhname.wd_lookup）：/wd/w/api.php ----------
WD = {"Q1": {"label": "アーニャ・フォージャー", "zh-tw": "安妮亞·佛傑", "desc": "fictional character from Spy × Family", "p31": "Q15632617"},
      "Q2": {"label": "マリィ", "zh": "玛俐", "desc": "fictional character from One Piece", "p31": "Q15632617"},
      "Q3": {"label": "マリィ", "zh": "瑪俐", "desc": "fictional character from Pokémon Sword and Shield", "p31": "Q15632617"},
      "Q4": {"label": "Mercy", "zh": "梅尔西", "desc": "commune in Yonne, France", "p31": "Q484170"}}


@app.get("/wd/w/api.php")
def wikidata(request: Request):
    qp = request.query_params
    if qp.get("action") == "wbsearchentities":
        q = qp.get("search", "")
        return {"search": [{"id": k, "label": v["label"], "match": {"type": "label", "text": v["label"]}} for k, v in WD.items() if v["label"] == q]}
    ents = {}
    for i in qp.get("ids", "").split("|"):
        v = WD.get(i)
        if v:
            ents[i] = {"labels": {k: {"value": v[k]} for k in ("zh-tw", "zh") if k in v},
                       "descriptions": {"en": {"value": v["desc"]}},
                       "claims": {"P31": [{"mainsnak": {"datavalue": {"value": {"id": v["p31"]}}}}]}}
    return {"entities": ents}


# ---------- 從畫師作品全集反查（sweep）：測試直接改 SWEEP（縮圖名 → 本機檔案路徑） ----------
SWEEP: dict = {}


@app.post("/_sweep")
async def sweep_set(request: Request):
    SWEEP.clear()
    SWEEP.update(await request.json())
    return {"ok": True}


@app.get("/ajax/user/{uid}/profile/all")
def pixiv_profile_all(uid: int):
    if uid != 4242:
        return {"error": True, "message": "ユーザーが見つかりません", "body": []}
    return {"error": False, "body": {"illusts": {"801": None, "802": None}, "manga": {"803": None}}}


@app.get("/ajax/user/{uid}/profile/illusts")
def pixiv_profile_illusts(uid: int, request: Request):
    ids = request.query_params.getlist("ids[]")
    pages = {"801": 1, "802": 2, "803": 1}
    return {"error": False, "body": {"works": {i: {"id": i, "url": f"https://i.pximg.net/c/250x250_80_a2/img-master/img/2026/01/0{int(i) - 800}/00/00/00/{i}_p0_square1200.jpg",
                                                   "width": 400, "height": 560, "pageCount": pages.get(i, 1)} for i in ids}}}


def _sweep_file(key: str):
    p = SWEEP.get(key)
    if p:
        return Response(open(p, "rb").read(), media_type="image/jpeg")
    # 對不到的作品：雜訊圖（以前用跟圖庫同一種「色塊＋圓」的圖，有時候被當成圖庫裡別張，比對數就不對）
    import io
    import random
    import zlib
    from PIL import Image
    rnd = random.Random(zlib.crc32(f"sweep/{key}".encode()))
    im = Image.frombytes("RGB", (40, 56), bytes(rnd.randrange(256) for _ in range(40 * 56 * 3))).resize((400, 560), Image.NEAREST)
    b = io.BytesIO()
    im.save(b, "JPEG", quality=90)
    return Response(b.getvalue(), media_type="image/jpeg")


@app.get("/pximg/c/{size}/img-master/img/{y}/{m}/{d}/{hh}/{mm}/{ss}/{name}")
def pximg(size: str, name: str, request: Request, y: str = "", m: str = "", d: str = "", hh: str = "", mm: str = "", ss: str = ""):
    if request.headers.get("referer") != "https://www.pixiv.net/":
        return Response(b"", status_code=403)
    return _sweep_file(name.replace("_master1200.jpg", ""))


@app.get("/ximg/media/{key}")
def ximg(key: str):
    return _sweep_file(key)


# ---------- E-Hentai 以圖搜圖 ----------
EHS = {"hits": [], "wait": 0}     # 測試設定：送來的檔案大小 > 0 就回這些本子；wait＝前幾次回「太快了」


@app.post("/_ehs")
async def ehs_set(request: Request):
    d = await request.json()
    EHS["hits"] = d["hits"]
    EHS["wait"] = d.get("wait", 0)
    return {"ok": True}


@app.post("/ehupld")
async def eh_upload(request: Request):
    form = await request.form()
    assert form["sfile"].filename and form.get("fs_similar") == "on"
    data = await form["sfile"].read()
    if EHS["wait"] > 0:
        EHS["wait"] -= 1
        return HTMLResponse("Please wait a bit longer between each file search.")
    return Response(b"", status_code=302, headers={"Location": f"https://e-hentai.org/?f_shash={'a' * 40}&fs_from={form['sfile'].filename}&n={len(data)}"})


@app.get("/eh/")
def eh_index(request: Request):
    if not request.query_params.get("f_shash"):
        return HTMLResponse("<html></html>")
    rows = "".join(f'<tr><td><a href="https://e-hentai.org/g/{g}/{"0123456789"}/">x</a></td></tr>' for g, _ in EHS["hits"])
    return HTMLResponse(f"<html><body><table class='itg'>{rows or ''}</table>{'' if rows else '<p>No hits found</p>'}</body></html>")


@app.post("/ehapi")
async def eh_api(request: Request):
    body = await request.json()
    tags = dict(EHS["hits"])
    return {"gmetadata": [{"gid": g, "token": t, "title": f"[Circle (eh_painter)] Book {g}", "title_jpn": f"本 {g}", "category": "Doujinshi",
                           "filecount": "20", "tags": tags.get(g, [])} for g, t in body["gidlist"]]}
