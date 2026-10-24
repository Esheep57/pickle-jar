# 開發文件

> 給想改這個程式的人：模組在哪、資料表、處理流程、安全、測試、踩過的坑。使用說明見 [USER_GUIDE.md](USER_GUIDE.md)，介面設計見 [DESIGN.md](../DESIGN.md)。
> 註：括號裡的 b42、b93 這類是內部版本號，對應當時加上的功能。

## 2. 模組

**後端 `app/`**

| 檔案 | 用途 |
|---|---|
| `server.py` | 進入點、guard 中介層（CSRF、只收 localhost、匯入按鈕例外、**例外記錄 errlog**）、啟動背景整理 `_prep`（媒材、備份、畫師重查、資料卡補查、people／piles／charknn、no_artist 同步、aigen、WAL checkpoint） |
| `db.py` | 連線（每執行緒一條）、資料表與欄位遷移、設定預設 `DEFAULT_SETTINGS`、撤銷（`push_action`／`_apply_undo`）、備份、`_WriteLock`（可重入、**最外層離開自動 commit**）、`media_seq`（id 不重用） |
| `pipeline.py` | 掃描→解碼→縮圖→dHash／指紋→WD Tagger→寫入；監看；**`same_image()` 唯一的同圖判斷**；`find_duplicates()`、`crop_scan()` 局部截圖；`process_pending` 結尾呼叫 people／piles／aigen／charknn |
| `tagger.py` | ONNX 推論、embedding |
| `candidates.py` | 身分候選新增與順位（tier 1 原檔、2 搜源、3 Pixiv／生成提示詞、4 AI）、`TIER_FILTER`（待確認分頁）、`media_type()` 媒材推測（新圖先用規則，整批處理完由 `mediaclf` 蓋過） |
| `clipemb.py` | CLIP ViT-B/32 圖像特徵（b78）：`Xenova/clip-vit-base-patch32` `onnx/vision_model.onnx`（fp32，350 MB），GPU 跟 WD 同一套 provider（b79；`_clip_gpu`：trying → 算完一塊 ok；啟動時還是 trying＝上次崩潰 → crashed 改 CPU；Python 例外 → failed 改 CPU；GPU 固定批次 16），輸入是縮圖（中心裁 224）；表 `media_clip`（fp16 512 維、fp 不同就重算）；本機工作 `clip`（一塊 400 張，做完一塊 `mediaclf.run(only)`，全部做完 `schedule`）；設定 `media_clip`；測試用 `LIB_MEDIA_CLIP=0` 不下載 |
| `mediaclf.py` | 媒材分類（b77）：WD embedding 上的 softmax regression（numpy，Adam 200 步，L2 1e-2，類別平衡），訓練資料 `media_label`（src `user` 你標的、`seed` 起始標註，`media_seed.json` 用 fp 對）；`run(only)` 訓練＋重算（手動的不動）、`schedule()` 標完 3 秒重算、`set_label()`／`undo_rows()`、`evaluate()` 5 折；`CONF=0.6` 以下篩選不列、歸「未定」（`query.mtype_cond`） |
| `matcher.py` | 否決索引、停止自動標註、否決傳播 |
| `httpclient.py` | 所有連外請求：`client()`、`_req(site, …)` 每個網站各自限速（`MIN_INTERVAL`）＋429／5xx／斷線重試、`UA`／`BROWSER_UA` |
| `danbooru.py` | Danbooru API `dan_get`、資料卡 `fetch_tag_info`／`save_tag_info`、原檔 MD5、Danbooru 內建相似圖（`_dan_iqdb*` 狀態）＋公開 IQDB `iqdb_search`、`verify_similar`、`search_mode`、`test_danbooru` |
| `pixiv.py` | Pixiv 作品資訊、作者 → Danbooru 畫師、Pixiv 標籤對應角色 `map_pixiv_tag` |
| `jobqueue.py` | 存在資料庫的優先序佇列 `PQueue`（表 `job_queue`）：大工作切小塊（搜源 10 張）、同一次送出的是一「組」；優先序 0 手動（≤50 張）＞ 1 資料卡／工具 ＞ 2 背景自動搜源 |
| `online.py` | 搜源主流程 `lookup_source`、結果套用 `save_source`／`auto_apply`、`should_skip`（預判＋真人照片不上傳）、線上工作 `OnlineJobs`：用 `jobqueue.PQueue`，一次做一小塊、每塊做完重新挑優先序最高的；線上功能關著時**暫停不丟**；停止＝整組取消；重啟後自動接著做（`queue_missing_taginfo`、`research-maybe`、`periodic_research`） |
| `likely.py` | 搜源前預判分數（≥60 很可能、30–59 可能、<30 不太可能） |
| `hashscan.py`／`artistscan.py`／`sigscan.py`／`r34.py`／`e621.py`／`rsearch.py` | 像素指紋、從畫師反推、讀簽名、Rule34、e621（b96 起只剩手動原檔／以圖搜圖、網址匯入）、TinEye／Fluffle |
| `importer.py`／`sitetags.py`／`siteartists.py`／`artistlinks.py` | 從網站匯入、網站標籤對應、未登記畫師、創作者連結補齊 |
| `togif.py` | 影片轉 GIF：`plan()` 檢查參數（≤120 秒、≤3600 格、格數×寬×高 ≤ `MAX_PIXELS` 4 億、fps 只能 `FPS_CHOICES`、不放大）、`iter_frames()`（`read_frames()` 是它的 list 版）OpenCV 從開始時間讀、每 1/fps 取時間最接近（±半格）的那格、`_run()` 讀出來的格先寫 `_Spool`（`data/gif_tmp/<id>.frames.npy` memmap，量化完就關掉刪掉；Windows 要先 `_mmap.close()`），`build_palette()` 整段共用 256 色（抽 16～48 格 MEDIANCUT）、`encode()` Pillow 寫 GIF（`frame_durations` 累加後四捨五入到 1/100 秒）；背景工作 `JOBS`（記憶體，一次一個 `_lock`）→ `data/gif_tmp/<id>.gif`（一天後清，maintenance 也清）；`save()` 複製到影片旁邊（iCloud／不能寫 → 匯入資料夾的 `GIF`）、直接建 media 列、帶上影片 state=1 的 character／artist／copyright／custom（source `gif`）和 nsfw_manual、`log_op`、`submit("scan", [p])`；`share()` 用好認的檔名放剪貼簿（`share.set_clipboard`）；`after`＝`share`／`save`：做好後 `_after()` 自動做（狀態 `after` → `done`，失敗放 `after_error`，GIF 照樣算做好）。前端 `quickGif()`（Shift＋點底片、Ctrl+Shift+C）整部 + `after:"share"`，超過 2 分鐘或估計格數×寬×高 > `BIG`（2,000 萬）改開視窗 |
| `gdl.py` | 其他網站交給 gallery-dl（b84）：`match(url)` 用 `gallery_dl.extractor.find`，`DENY`（directlink／generic／recursive…，以及付費內容平台和轉貼站）不接；不是單篇（`SINGLE` 以外的 subcategory）＝整批，`--post-range 1-gdl_max_posts`（0＝全部）、`--download-archive`；子程式 `python -m gallery_dl --write-metadata --no-part -D data/gdl_tmp/<工作>`（`LIB_GDL_CMD` 換成假的，tests/fake_gdl.py），stdout 一行＝一個檔案（「# 」開頭＝下載紀錄裡有、跳過）；每 20 個 `_place_ready()`→`_place()`：MD5 已在圖庫 → `_remember_url`、同一篇多檔 → 小資料夾、`import_meta.site='gdl:<網站>'`、`submit("scan")`；`after_processed` Bluesky 等帳號 → `sigscan.resolve` → 創作者候選（source `gdl`，不建新畫師）；`install()` 用 tools/uv 裝 requirements.txt 裡的版本；進度在 `JOBS`（`/api/fetch-url/galleries` 一起回，kind=gdl） |
| `implies.py` | 標籤蘊含（b84）：表 `tag_implies(tag_id, implies_id, src danbooru／danbooru_copy／manual／off)`；b93 `danbooru_copy`＝角色 ⇒ 作品，`sync_copyrights(tids)` 照 `tag_info.copyrights`（只收圖庫裡有的作品標籤、資料卡改了跟著改、off／manual 不動；啟動時全部、`save_tag_info` 存角色時那一個、存作品時全部）；`unapply` 否決角色時刪掉只因它才在的作品**候選**（state 0；圖上別的角色——確認或候選——也屬於的留著）；`danbooru_says_no` 只看角色 ⇒ 角色的規則、`implies_checked`；`closure()`（記憶體快取，`invalidate()`）；`apply(c, mids, tid)`／`unapply()` 回撤銷用 tag_rows（`common._apply` 的 confirm／add／replace／reject、`candidates.add` 自動確認時呼叫；source `implied`）；`sync_danbooru()` 一次 40 個 `search[antecedent_name_comma]`，只收兩邊都是本機角色的、`INSERT OR IGNORE`（不蓋 manual／off）；`schedule()`（save_tag_info 新角色、啟動 90 秒後）；`backfill(dry)` 舊圖一筆撤銷；`people.refresh_bases` 併入角色 ⇒ 角色規則、扣掉 off；b92 `danbooru_says_no()`（問過、tag_info.post_count>0、沒有 danbooru／manual 規則）的名字猜法也扣掉，`sync_danbooru` 只要有問就重算 char_base，`schedule()` 新本體連名字像它造型的（`people.guessed_pairs()`）一起重問 |
| `srcurl.py` | 貼出處網址（b94）：`check(ids, url)`＝`importer.parse` 只收 x／pixiv → `_read_x`（Danbooru `source:*/status/ID*`、FxTwitter 作者／hashtag／敏感、每張 `?name=small`）／`_read_pixiv`（`fetch_pixiv`＋`resolve_pixiv_artist`、Danbooru `pixiv:ID` 依 source 的 `_pN` 對頁、`/ajax/illust/ID/pages` 每頁 small，最多 40 頁；抓不到時用 Danbooru 的圖）→ 每張選的圖 `compare_images` 取最像的頁 → match（同 ascii2d：≥0.95 same、≥`_same_threshold` likely）→ `ascii2d._tags_for(mid, b, via="貼的網址")`＋`_char_evidence`；結果放 `_PENDING[token]`（30 分鐘）。`apply(token, picks)`：勾的 `_apply("confirm")`（source `url`）一筆 push_action、media_source method `url`、NSFW、`importer._remember_url`、`upgrade.schedule`。API `POST /api/media/source-url/check`、`/apply`（`api/library.py`）。前端 `static/js/srcurl.js` `srcUrlModal(ids, after)`：大圖出處卡 `#v-srcurl`、選取列 `srcurl` |
| `tasks.py` | 背景工作登記表（b93）：`spawn(kind, label, target, *args, probe=, cancel=, heavy=, name=)` 取代 `threading.Thread(...).start()`；`probe_of(dict)` 讀各模組原本的進度；heavy 同時最多 `HEAVY_MAX`=2（Semaphore，其他 waiting）；表 `bg_tasks`（`on_startup` 把 running／waiting 改 interrupted、只留 60 筆）；`snapshot()`／`cancel(id)`／`version()`（事件流用）。目前登記：gdl、gallery、gif、dataset、import-apply、implies-sync、artist-links、artist-links-all、aimeta 補跑、qtlearn。**新的背景執行緒用 `tasks.spawn`**；charknn／mediaclf 的重算有自己的鎖，沒有登記（面板另外列） |
| `perf.py` | 回應速度（b93）：`server.guard` 量每個請求（`perf.begin()`＝contextvar dict，FastAPI 的同步路由在執行緒池也指向同一個 → 寫入鎖等待時間帶得回來）；`db._WriteLock` 最外層取得／放開時呼叫 `lock_waited`／`lock_held`；慢請求 ≥300 ms 進 `data/slow.log`（1 MB 換一份）；`GET /api/diag/perf`；系統狀態「回應速度」 |
| `qtlearn.py` | 快速標籤從你的標記學（b93）：每個快速標籤（≥ `MIN_POS`=50 張）一個二元 logistic regression（WD embedding 標準化、Adam 300 步、正例加權）；`qt_pred`（p ≥0.5、還沒標的）、`qt_no`（你拿掉的，之後不建議；再加回去就刪）；`guess:名字`＝p ≥ `THRESHOLD` 0.9；`quicktags.summary` 有模型就用它當 hint。`schedule()` 快速標籤改了 2 分鐘後（`tasks.spawn` heavy），啟動 3 分鐘後。真實資料 6 個標籤共 5 秒。**坑**：Adam 裡 `np.sqrt(python float)` 回 float64，b 變成 float64 → 整條 `X @ w + b` 變 float64、混型 matmul 慢 30 倍（200 秒 → 5 秒） |
| `dataset.py` | 匯出訓練集（b84）：`caption(mid, opts)`（確認的角色／作品／創作者＋一般標籤，模型的要 ≥ general_min；角色的 `tag_info.copyright` 補作品）、`check_dest()`（不能在 roots／import_dir／DATA_DIR 裡）、背景 `JOBS`；JPG/PNG/WebP/GIF 原檔複製，其他轉 PNG；檔名 `<media id>.<ext>`＋`.txt` |
| `drivebackup.py` | 備份到外接硬碟：`sources()`（roots＋程式＋data，label 存在 manifest）、`plan()`、`start()`／`_run()`（`.pjpart` 再 `os.replace`；刪掉／改過的先 `_hold` 到 `_刪除保留/<日期>`，30 天後 `_purge_hold`；整個資料夾讀不到或少一半以上不 hold；Windows 雲端預留位置不複製；資料庫用 SQLite backup API＋`quick_check`；抽查 MD5）、`drives()`（`LIB_DRIVES` 給測試）、`overdue()` |
| `upgrade.py` | 換成高清版：`refs()` 這張圖已知的貼文（media_source 的 Danbooru／Pixiv／推文／e621／Rule34、import_meta）→ `files()` 各站原圖尺寸與網址 → `check()` 比你的大（長邊 ≥1.1×、長寬比差 ≤4%）就記 `offer`；`replace()` 下載到 `data/upgrade_tmp`、`dupverify.same` 逐像素確認、放回原資料夾（檔名主幹不變、`os.utime` 沿用舊日期）、更新 media（path/fp/size/寬高、status=pending 重新標記）、舊檔 send2trash；`schedule()`（save_source、ascii2d 確認時呼叫）每 10 秒合成一組線上佇列 `upgradecheck`；整批換是 pipeline Jobs `upgrade` |
| `pixai.py`／`pixai_worker.py` | PixAI Tagger：排程與寫入在主程式，推論在子程式（stdin/stdout 一行一個 JSON；死掉＝那張記失敗，連續兩次自動關掉 `pixai_enabled`）；結果存 `pixai_pred`，`apply_all()` 依門檻重算候選；`evaluate()` 準確度 |
| `steps.py` | 自動標記與搜源的每一步（`STEPS`）：開關、找到時怎麼辦（`act_<key>`，在 `candidates.add` 套用）、門檻；`/api/steps` 統計、`/api/rerun` 補跑 |
| `sweep.py` | 畫師作品比對（Pixiv 作品縮圖、X 媒體頁由腳本送網址） |
| `galleries.py` | E-Hentai、nhentai 整本匯入 → 自動建本子 |
| `maintenance.py` | 每 6 小時（啟動時也跑）：孤兒紀錄、轉檔快取上限、`.part`、PRAGMA optimize、WAL checkpoint、`op_log` 清 2 年前 |
| `zhname.py` | Bangumi 中文名：`ja_names()` 只拿確定是日文的別名（有假名、或純漢字但 `_chinese_ok` 為假）→ `search_characters`／`search_subjects` 名字完全一樣（`norm`：NFKC、去標點）→ 同名多個／短名字要 `character_subjects` 對上 `series_hints()`（tag 括號、tag_info.copyright(s)、同圖的作品）→ `name_zh` 表（found／none／ambiguous／kept／variant／error）；`auto_map()` 記憶體快取給 `names.display`，寫入後 `reload()`（順便 `nameindex.invalidate`）；`refresh_variants()` 造型＝本體顯示名＋`variant_label`；線上佇列 kind `zhname`（CHUNK 20）；`schedule()` 在 `save_tag_info` 後呼叫 |
| `official.py` | 官方種類 `media.official`：anime／manga／novel／movie；手動或照 Danbooru 一般標籤 |
| `ascii2d.py` | ascii2d 半自動找出處：佇列（表 `ascii2d`，status queued→searching→ready→done/none/skipped）、`parse()` 結果頁連結、`resolve()` 查 Danbooru（推文／Pixiv ID）＋FxTwitter／Pixiv＋下載對方的圖用 `danbooru.compare_images` 比對、`decide()` 你確認才寫入（source `ascii2d`、media_source method `ascii2d`） |
| `charknn.py` | 新角色辨識（kNN＋中心）、多人圖猜另一個人、`evaluate()`；`_predict_many` 一次算一塊矩陣；`run(only=新圖)` 新圖處理完只算新圖，`run()` 整庫（確認／否決後 20 秒、啟動時） |
| `people.py` | 多人圖人數估計 `media.people`、「標完了」`people_done` |
| `piles.py` | 未識別分堆 `media.pile`（meme／texty／photo／nohuman／comic／video／illust） |
| `quicktags.py` | 快速標籤（數字鍵）、`sync_no_artist()`（作者不可考標籤 → `tags.no_artist`） |
| `albums.py` | 作品集（同人本、cos 圖包）：整本標籤、頁序、快照撤銷、資料夾建議、E-Hentai 名稱解析 |
| `aigen.py` | AI 生成圖：工具判斷 `media.ai_gen`、生成資料 `ai_meta`、提示詞 → 角色候選 |
| `nameindex.py` | 名字索引：原名／顯示名稱／別名／譯名，簡繁、大小寫、底線空白都對得到 |
| `query.py` | 搜尋語法、`MISSING_RAW`（未識別各分頁條件）、`BUCKETS`、`where()`、`explain()` |
| `names.py`／`seriesmap.py`／`seriesguess.py`／`wikiname.py` | 顯示名稱（繁體）、母系列、未知作品推測、維基譯名 |
| `dupverify.py` | 逐像素同圖驗證、局部截圖 |
| `jobsview.py`／`errlog.py` | 背景工作面板、伺服器錯誤紀錄 |
| `evaltagger.py`、`archive.py`、`ocr.py`、`media.py` | WD 驗證、iCloud 移出、OCR、圖片工具 |

**API `app/api/`**：`library.py`（圖庫列表 `list_media`（含 `album=`）、單張、側欄、略過、排除、分堆整批、作者不可考、快速標籤、依作品）、`tags.py`（角色／標籤清單、搜尋、建議、單一標籤頁、改名、合併 `merge_tags`、刪除、加標籤；**最一般的 `POST /api/tag/{name}` 放檔案最後**，`{name:path}` 會吃掉斜線）、`review.py`（待確認佇列、是／不是、高信心一次確認、`/api/undo`）、`shelf.py`（書架 `shelf()`、作品頁、作品封面）、`saved.py`（智慧相簿、搜尋說明）、`ascii2d.py`（`/api/ascii2d/*` 圖庫用；`/api/ascii2d/ext/*` 油猴腳本用，一律要 `X-Import-Key`，`server.py` 的跨站檢查對這些放行）、`albums.py`、`net.py`、`system.py`（設定、狀態、首頁、診斷、重啟）、`imports.py`、`lan.py`、`common.py`（`_apply` 標籤動作）、`steps.py`（每一步的設定、統計、補跑）、`sweep.py`、`upgrade.py`、`work.py`（整篇 Pixiv 貼文逐頁標角色）；操作紀錄 `/api/oplog*` 在 `system.py`。

**前端 `static/js/`**（**ES modules**，2026-10-01 起）：`index.html` 只載入 `main.js`（`type="module"`），每個檔案用 `import`／`export` 拿別的檔案的東西，名字錯了瀏覽器載入時就報錯。**新增函式給別的檔案用要 `export`，用的那邊要 `import`**；不能重新指派別的檔案的變數（要改就在原檔案提供函式，例如 `markDirty()`）；HTML 字串裡不能再寫 `onclick="函式()"`（沒有全域函式）。除錯：主控台的 `lib.S`、`lib.render()`、`lib.openViewer(id)`…（`main.js` 的 `window.lib`）。檔案：`core.js`（共用、`SRC` 來源徽章、`OFFICIAL`、未接住錯誤提示）→ `shell.js`（路由白名單 `VIEWS`、側欄、快捷鍵、快速標籤 `quickTag`）→ `grid.js`（圖庫主畫面 `render`、`gridParams`、資料卡、搜源）、`layout.js`（原比例排列＋虛擬捲動）、`filters.js`（`FILTER_KEYS`／`activeFilters`、查詢列 token＋分面）、`selection.js`（選取、拖曳、Shift 範圍）、`actions.js`（命令膠囊 `updateActionBar`／`actionList`、移出 iCloud）、`unrec.js`（未識別分流／分堆、圖庫分頁）→ `picker.js`（加標籤）→ `review.js`、`batch.js`（整批看）→ `shelf.js`（書架、作品頁）→ `albums.js`（本子）→ `viewer.js`（大圖）→ `work.js`（整篇貼文逐頁標角色）→ `palette.js`（Ctrl+K）→ `pages.js`（收件匣、系統、工具、診斷、操作紀錄）→ `settings.js`、`steps.js`（自動標記與搜源、補跑）→ `sweep.js`（畫師作品比對、E-Hentai）→ `main.js`。`core.js` 的 `api()` 每個請求帶 `X-Lib-View`／`X-Lib-Via`（操作紀錄用）。

---

### 2.0 樣式表 `static/style.css`（2026-10-07 整理成一份，b71）
- **設計規則在 `DESIGN.md`（專案根目錄，b76）**：顏色、字型、元件、層次、該做／不該做、已知偏差。改介面前先讀；改了 token 要同一個 commit 改 DESIGN.md 的 YAML 區塊和 §2 表格，再跑 `npx @google/design.md lint DESIGN.md`（雲端有 Node 就能跑，不用裝進 repo）。
- **一個選擇器只寫一次，依功能分段**：token → 動畫 → 基本元素 → 版面骨架（index.html、shell.js）→ 共用元件（core.js、好幾頁共用）→ 查詢列 → 圖庫格子 → 命令膠囊 → 未識別 → 加標籤 → 待確認 → 書架 → 本子 → 大圖 → 指令面板 → 收件匣／系統 → 設定 → 工具視窗。分段依「建立這個 class 的檔案」。**新樣式加在對應的段落、改既有規則就改那一條**，不要在檔尾加一段蓋掉前面的（以前疊了 v1.3～v4.2 十層）。
- 標「（續）」的小段：權重一樣、會套到同一個元素的規則必須排在別段後面才蓋得過，不能搬回原段（搬了畫面會變）。
- 深色模式只改 token（`@media (prefers-color-scheme: dark) :root`）；`body.tint` 是設定 › 顯示的綠色底。
- `!important` 只用在：蓋過 JS 設的行內樣式、`.hidden`／`body.remote` 這類一定要贏的工具規則、或要贏過權重比較高的規則（能改選擇器就改選擇器）。
- 防呆 `tests/test_css.py`：每條規則在某一段裡；同一個選擇器寫兩次會失敗（例外列在 `SPLIT_OK`）；樣式表裡的 class／id 一定要有程式在用；**同一個 class 被兩個功能檔建立會失敗**（b69.1 `.cv`、b70.1 `.mosaic`／`.sugrow`／`.dcard` 都是這樣撞名的；確定是共用元件才加進 `SHARED`）；`!important` 不能變多（`IMPORTANT_MAX`）。
- 下面 2.1～2.4 的「v3／v4／v4.1 段」是整理前的歷史；元件與 token 的說明仍然有效，只是規則現在都在各自的段落裡。
- 整理的做法（以後大改樣式可以照做）：同一個選擇器後面完整蓋掉的宣告拿掉 → 同一個選擇器合併 → 依段落重排，但「權重一樣、設同一個屬性、可能套到同一個元素」的兩條保持原本先後（用真實圖庫的畫面擷取＋原始碼裡的 class 組合判斷）→ 用真實資料庫的唯讀副本開伺服器，98 個畫面狀態 × 淺色／深色，比對每個元素（含 ::before/::after）的所有計算後樣式，整理前後零差異。工具在 Claude 的工作區，沒有放進 repo。


## 3. 資料表

`media`（path、fp、dhash、embedding、media_type、nsfw、status、excluded、unrec_skip、official／official_src、people／people_done、pile、ai_gen、artist_done…）、`tags`（name、category、pinned、auto_block、cover、no_artist、last_viewed）、`media_tags`（state 1 確認／0 候選／-1 否決；source；tier；score；reason）、`tag_info`（other_names、links、display_name、manual…）、`tag_alias`、`media_source`、`actions`（撤銷，只留最後 100 筆）、`op_log`（操作紀錄，見下）、`saved_search`、`albums`／`album_items`／`album_tags`、`ai_meta`、`media_seq`、`knn_refs`、`upgrade`（換成高清：status offer／none／done／failed／dismissed、cands、old_path／old_fp）、`dup_check`、`media_person`，以及搜源用的快取表（`series_parent`、`series_zh`、`pixhash`、`ocr_text`、`sig_handle2`、`r34_*`、`e621_checked`、`anilist_map`、`import_meta`、`jp_tag_map`、`pending_artist`、`audit_log`）。

`media_tags.source` 常見值：`model`（WD）、`manual`、`danbooru`、`pixiv`、`rule34`、`knn`（你的圖庫）、`album`（作品集整本標籤）、`aimeta`（生成提示詞）、`tracemoe`、`signature`、`match`、`x`。

---

**操作紀錄 `op_log`**（v14）：每個改資料的操作一列，2 年後才清（`maintenance` 每 6 小時）。欄位：ts、label（跟撤銷一樣的說明）、kind（`db.op_kind()` 去掉張數，分組用）、keys（撤銷資料的種類，例如 `tag_rows+skip`；`undo`＝撤銷本身）、n、mids（最多 50）、tids（最多 20）、view（前端每個請求帶 `X-Lib-View`＝目前的 hash，網址編碼；背景＝NULL；腳本＝「瀏覽器腳本」）、via（`X-Lib-Via`：最近 5 秒的輸入是 key／mouse／touch；腳本＝script）、action_id、undone_at。寫入點：`db.push_action()`（所有可撤銷的操作）會自動記；不能撤銷的另外呼叫 `db.log_op()`（移到回收筒、ascii2d 找到／沒找到／跳過、換成高清）。**新增會改資料、但不經過 push_action 的 API，記得呼叫 `db.log_op()`**。請求的頁面／輸入方式由 `server.guard` 放進 contextvar `db.OP_CTX`。API：`GET /api/oplog?days=&limit=&kind=`、`/api/oplog/summary?days=`、`/api/oplog.csv`；介面：系統 › 工具 ›「操作紀錄」（`pages.oplogModal`）。分析使用習慣時直接讀這張表（§8.4）。

**遷移**：`db.init()` 先建表，再依 `PRAGMA user_version` 跑 `db.MIGRATIONS` 裡還沒跑過的（v1 以前散落的欄位補齊、v2 `media.trashed_at`、v3 `job_queue`）。**新欄位／新表＝在 `MIGRATIONS` 最後加一個函式**，不要改前面的。`media.status` 多了 `trashed`（丟到回收筒，紀錄保留 30 天，檔案回來就接回）。

---

## 4. 處理流程

1. **掃描／監看** → `pending` → WD Tagger：一般標籤 `source='model', state=0`、角色只進候選（tier 4）。
2. 處理完 → people／piles／aigen（新圖）→ charknn → `online.auto_source()`（背景搜源）→ 匯入後處理。
3. **搜源順序**：Danbooru 原檔 MD5 → 檔名 MD5 → 像素指紋 → X 圖片 key → Pixiv ID → 推文 ID → 沿用真正複本 → IQDB（≥90 採用；70–90 下載比對）。找不到：e621 → Rule34 → 讀簽名 → trace.moe（像動畫畫面的圖）。自動搜源前 `should_skip` 預判。
4. **身分標籤**只有你按「是」才算數（確定的方法除外：MD5、Pixiv ID…）。

**未識別的分頁條件**（`query.MISSING_RAW`，全部另外排除 `unrec_skip`）
- 完全未識別：沒有已確認角色、`_NO_ARTIST`、沒有作品。
- 缺角色：沒有角色、也不是標了 original。
- 缺創作者 `_NO_ARTIST`：沒有創作者，而且不是官方（anime／manga／novel／movie）、沒按「作者不可考」、沒有 `no_artist` 標籤。
- 多人圖缺角色：人數 ≥2、角色比人數少、沒按「標完了」、角色不是 Danbooru／Pixiv 標的。

---


## 6. 安全

- 只收 localhost、Host 檢查；區網只開 `/m`（要 PIN）。
- CSRF：非 GET 跨站一律拒絕；例外只有 `POST /api/fetch-url/ext` 帶正確 `X-Import-Key`。
- 匯入只接受五個網站的貼文網址（防 SSRF）；單檔上限 300 MB。
- API key 由你自己輸入；匯出不含。
- 以圖搜圖只上傳縮小的圖；真人照片不自動上傳。

---


## 9. 測試

- `python -m pytest tests -q -x`（約 240 項、8–10 分鐘；畫面測試要 Playwright＋Chromium：`pip install -r requirements-dev.txt`、`playwright install chromium`）。
- **畫面測試是必跑的**：沒裝 Playwright 或 Chromium 會**失敗**（以前自動跳過，新環境會靜靜少 9 項）。真的不能裝時設 `LIB_ALLOW_NO_UI=1` 才跳過。
- 測試結束時 `conftest.py` 用 `os._exit(結果)` 結束：背景執行緒（ONNX、OpenCV）在直譯器關閉時會讓 C++ abort（exit 134），測試全過卻回報失敗。
- 背景重算的東西在測試裡要用同步版本（例如 `charknn.run(block=True)`），不要假設沒有別的執行緒同時在算。
- 卡住時 `py-spy dump --pid <python 的 pid>`（`pip install py-spy --break-system-packages`）。
- `tests/mock_online.py`：假 Danbooru／IQDB／Pixiv／e621／Rule34／維基／FxTwitter／trace.moe。`conftest.py` 的 `client` 是整個 session 共用的圖庫：**測試之間會互相影響**，要自己準備資料、用 id 排序挑圖、把會影響判斷的欄位（unrec_skip、excluded、media_source…）先重設。
- 防呆測試（新增功能時一定要顧到）：
  - `test_every_filter_is_listed`：畫面查詢用到的狀態都要登記在 `FILTER_KEYS`／`activeFilters()`（頁面本身的狀態如 `albumId` 列在 `not_filters`）。
  - `test_every_page_opens_without_errors`：23 個頁面實際打開，任何 JS 錯誤都算失敗；新頁面要加進 `PAGES`。
  - `test_action_bar_matches_page`：每一頁選一張，選取列必須是該頁的按鈕。
  - `test_zz_all_writes_hold_the_lock` 和 fuzz 前的 `test_zz_writes_hold_the_lock_after_all_tests`：所有寫入都要在 `db.write_lock` 裡（**測試裡自己寫資料庫也一樣**，否則留下沒 commit 的交易，後面的請求全部卡死）。
  - `test_no_500_on_junk`：所有寫入 API × 12 種亂輸入不能 500。
  - `test_noise_budget`：每頁看得到的控制有上限。
  - `tests/test_css.py`：樣式表每條規則在某一段、同一個選擇器只寫一次、class／id 都有人用、同一個 class 不能被兩個功能檔建立（除非在 `SHARED`）、`!important` 不能變多（§2.0）。

---


## 12. 經驗教訓

- **樣式表只往檔尾加一段蓋前面＝每一版都要比上一版權重更高或加 !important**，最後誰贏要看十層；新 class 名稱也容易撞到舊段落沒人記得的規則。整理成一份後：改哪個元件就改那條規則；新名字先 grep（`tests/test_css.py` 會擋同一個 class 被兩個功能檔建立）。

- **JS**：`node --check` 抓得到重複宣告，抓不到 TDZ（const 在宣告前被用）和未定義變數 → 一定要有「實際操作」的畫面測試（選圖、開大圖）。選取列這種會殘留舊內容的元件，出錯時要清空而不是留著。
- **新 SQL 條件先在大一點的資料庫量時間**：相關子查詢曾經 5–12 秒（改成不相關子查詢 4–10 ms）。
- 同一件事只能有一個實作：同圖判斷 `same_image`、缺創作者 `_NO_ARTIST`、作品範圍 `_series_sql`、名字 `nameindex`。數字不一致（書架 vs 作品頁）幾乎都是兩個實作。
- 所有寫入都在 `db.write_lock` 裡；背景網路查詢不能在鎖裡做。
- 函式裡 `from ..query import X` 會讓整個函式的 X 變成區域變數（首頁 500 過一次）。
- 路由 `{name:path}` 時具體路徑要排前面；新端點名稱先確認沒撞到。
- 空畫面要說明是不是篩選造成；有篩選時不能說「都完成了」。
- 評估要避免倖存者偏差：排除自己推薦又被確認的資料再算準確率／召回。
- **背景重算「忙碌就略過」一定要記下來、做完補算**（新角色辨識以前只設旗標沒人讀，確認的角色要等下一個事件才反映）。
- 「算一次就沿用」的全域參數要注意是在什麼時候算的（`matcher.mu` 曾在標記到一半時被凍結）；抽樣要固定，不要 `ORDER BY RANDOM()`。
- 同一次計算裡的正規化參數只能取一次（新角色辨識以前確認圖和新圖各取一次平均，中間 matcher 重建就不一致 → 測試隨機失敗）。
- 分組不能用並查集串連「兩兩相似」：A≈B、B≈C 不代表 A≈C；會被一鍵刪除的分組要每張都直接比對過。
- 「前 N 名」的候選要先排除已處理過的再取，否則處理完一次就永遠卡在同一批。
- 刪除一律軟刪除（保留紀錄），你的手動標記是最貴的資料。
- 批次處理要「一張壞掉只影響一張」（SAVEPOINT）；整批撤回＋工作中止會讓同一張壞圖每次都擋住後面的。
- **尺寸一律讀檔頭**（`media.image_size`），不要用解碼後的 `img.size`：`load_image` 會 draft 縮小大 JPEG。
- 背景 `log.warning` 會進「程式出錯」（`errlog.install()`）；不要再寫 `except: pass`，至少 `with errlog.bg("步驟名")`。

---

- 改版時保留舊元素的 id（`#miss-tabs`、`#find-src`、`#sort`、`#mtype`…）只搬位置，綁定和測試就不用重寫；搬進彈出面板的元素要等面板畫好才綁（`S.bindFacets`）。
- 畫面測試用 `inner_text` 判斷選取列，只看得到「看得見的」字：常用 3 個動作的預設要包含測試期待的那個（例如未識別頁的「略過」）。

