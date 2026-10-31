"""SQLite 資料層：媒體、標籤、設定、撤銷紀錄。"""
from __future__ import annotations

import contextvars
import json
import re
import sqlite3
import threading
import time
from pathlib import Path

import os

DATA_DIR = Path(os.environ.get("LIB_DATA_DIR") or Path(__file__).resolve().parent.parent / "data")
DB_PATH = DATA_DIR / "library.db"

DEFAULT_SETTINGS = {
    "roots": [],                      # 要掃描的資料夾
    "exclude": [".thumbnails", "$RECYCLE.BIN", "@eaDir"],
    "model_repo": "SmilingWolf/wd-eva02-large-tagger-v3",
    "general_threshold": 0.35,        # 一般 tag 門檻
    "character_threshold": 0.85,      # 模型角色 tag 門檻
    "nsfw_ratings": ["questionable", "explicit"],  # 哪些 rating 視為 NSFW
    "ai_hide_when_sourced": True,     # 搜源已有結果的圖，不再顯示 AI 模型的角色候選
    "batch_size": 16,
    "workers": 6,
    "video_max_frames": 12,
    "watch": True,                    # 監看資料夾，自動處理新檔
    "neg_sim": 0.9,                   # 否決傳播：和被否決的圖相似度 ≥ 此值，就不再自動標同一個 tag
    "auto_rematch": True,             # 你否決 tag 後，自動清掉其他圖上跟它很像的同一個自動候選
    "scan_interval_h": 6,             # 定期完整掃描（小時，0 = 關閉；監看漏掉的變動靠這個補）
    "skip_live_photo": True,          # 略過 iPhone 原況照片附帶的 .MOV（同資料夾有同名照片時）
    "backup_keep": 7,                 # 每天自動備份資料庫，保留幾份
    "backup_mirror_dir": "",          # 每日備份另存一份到這裡（另一顆硬碟、隨身碟、雲端資料夾）；硬碟壞掉時還有一份
    "drive_backup_dir": "",           # 備份到外接硬碟（drivebackup.py）：上次選的位置
    "drive_backup_last": {},          # 上次備份到外接硬碟的結果（收件匣提醒用）
    "hf_endpoint": "",                # 模型下載鏡像（例如 https://hf-mirror.com），空白 = 官方
    "lan_enabled": False,             # 允許手機在同一個 Wi-Fi 下上傳（需要 PIN）
    "lan_pin": "",
    "upload_dir": "",
    "relay_uncleared": 0,             # 已移出、但可能還在 iCloud「最近刪除」占空間的張數
    "relay_uncleared_bytes": 0,
    "archive_dir": "",                # 「移出 iCloud」的保存資料夾（不能在同步資料夾裡）                 # 手機上傳存放的資料夾（空白 = 第一個圖庫資料夾下的「手機上傳」）
    "knn_enabled": True,
    "taginfo_refetch_original": False,  # 一次性：重查「Danbooru 上只有 original」的原創角色              # 新角色辨識：用你確認過的圖推薦角色（待確認的「你的圖庫」）
    "quick_tags": ["梗圖", "真人", "截圖"],   # 快速標籤：數字鍵 1–9、大圖和選取列一鍵加／拿掉（自訂標籤）
    "quick_tags_skip": ["梗圖", "真人", "截圖"],   # 加上這些快速標籤的圖，同時離開「未識別」（不用再認角色）
    "no_artist_tags": ["梗圖", "表情包", "截圖"],
    "no_char_tags": ["梗圖", "截圖"],   # 有這些標籤的圖不再認角色、不再列候選給你確認（AI、搜源、你的圖庫都一樣）   # 有這些標籤的圖作者不可考：不算「缺創作者」（標籤拿掉就回來）
    "taginfo_refetch_manual": False,    # 一次性：手動改過作品、沒有別名的角色補查別名
    "media_clip": True,
    "_clip_gpu": "",                  # CLIP 用 GPU 的狀態（b79）：trying／ok／crashed（上次崩潰，改用 CPU）／failed               # 媒材分類多用一個 CLIP 模型（b78；第一次下載約 350 MB，只用 CPU）
    "pixai_enabled": False,           # PixAI Tagger v1.0：第二個 AI 模型，補角色／作品／畫風（第一次開會下載約 950 MB）
    "pixai_candidates": False,        # PixAI 的結果放進待確認（評估過再開）
    "pixai_artists": True,            # 也推薦畫師（畫風像）
    "pixai_char_threshold": 0.7,
    "pixai_artist_threshold": 0.8,
    "pixai_copyright_threshold": 0.8,
    "_pixai_crashes": 0,              # PixAI 子程式連續崩潰幾次（2 次自動關掉）
    # ---- 線上資料（Danbooru / IQDB / Pixiv）----
    "online_enabled": True,           # 總開關
    "online_auto_charinfo": True,     # 自動抓角色資料卡（新角色、打開角色頁時）
    "online_auto_apply": True,        # 搜源結果自動套用 tag
    "online_min_similarity": 90,      # IQDB 相似度門檻（%）
    "online_iqdb": True,              # 允許把縮小的圖片上傳到 IQDB 反查
    "online_auto_source": True,       # 新圖和還沒搜過的圖，背景自動搜源（Danbooru 優先）
    "online_auto_level": "maybe",     # 自動搜源先預判：likely 只搜很可能的 / maybe 很可能＋可能（預設；使用者要求放低）/ all 全部
    "online_verify_min": 70,          # IQDB 70–門檻之間：下載 Danbooru 縮圖跟本地圖比對，夠像才採用
    "online_retry_days": 30,          # 找不到的圖，隔幾天再搜一次（Danbooru 一直在新增）
    # 自動流程：新圖 Danbooru 找不到時，接著做哪些（首頁「整理中心」可以一個一個開關）
    "auto_zhname": True,              # 沒有中文名（或是暱稱）的角色／作品：到 Bangumi 找中文名
    "ascii2d_auto_accept": "off",      # ascii2d 比對到同一張（same）／很像也算（likely）：全部勾選、自動確認；full＝全自動，什麼情況都不停（b80）；off＝自己按 Enter
    "auto_upgrade": True,             # 找到出處時順便看有沒有更高清的版本（只提醒，你決定換不換）
    "auto_pixelhash": True,           # 像素指紋（跟 Danbooru 原檔像素一樣）
    "auto_r34": True,                 # Rule34 原檔比對（要填 key）
    "auto_signature": True,           # 讀圖上的簽名（只讀 AI 判斷有簽名的圖）
    # 每一步找到時怎麼辦（見 steps.py）：confirm 直接確認 / candidate 放待確認 / record 只記下 / auto 依情況
    "act_wd": "candidate", "act_pixai": "record", "act_knn": "candidate", "act_aimeta": "confirm",
    "act_sig": "candidate", "act_exact": "confirm", "act_iqdb": "candidate", "act_pixiv": "record",
    "act_r34": "candidate", "act_x": "auto", "act_sweep": "confirm",
    "knn_p_min": 0.40,                # 你的圖庫：學到你的標準後，預測「是」的機率要多高才推薦
    "_steps_v1": False,               # 一次性：舊設定（pixai_candidates、online_auto_apply）換成 act_*
    "danbooru_login": "",             # 選填：Danbooru 帳號 + API key（提高速率限制）
    "danbooru_api_key": "",
    "pixiv_phpsessid": "",
    "e621_login": "",                 # 選填：e621 帳號名稱 + API key（以圖搜圖限速放寬：每分鐘 1 張 → 每 10 秒 6 張）
    "e621_api_key": "",
    "gdl_enabled": True,              # 其他網站交給 gallery-dl（b84）
    "gdl_max_posts": 0,               # 貼的是整個帳號／標籤／收藏時只抓最新幾篇（0＝全部）
    "gdl_user_agent": "",             # gallery-dl 的 User-Agent；空白＝gallery-dl 自己的
    "pixiv_album": "manga",          # Pixiv 多頁作品存成本子：manga＝Pixiv 標成漫畫的／multi＝所有多頁作品／off＝不要
    "import_dir": "",                 # 從網站匯入的圖存到哪（空白＝第一個圖庫資料夾\匯入）；會自動加進圖庫資料夾
    "import_key": "",                 # 瀏覽器「存到圖庫」按鈕的配對金鑰（第一次用時自動產生）
    "r34_user_id": "",                # 選填：Rule34 帳號的 user ID + API key（原檔比對用）
    "r34_api_key": "",
    "display_lang": "zh",             # 角色/作品顯示名稱：zh 中文（繁簡照 zh_mode）/ ja 日文 / raw 原始 tag
    "series_independent": ["arknights:_endfield"],   # 作品頁獨立顯示、不併入母系列的子作品（角色不同批的，例如終末地；b110）
    "charcat_zh": True,               # Danbooru 角色表：照作品熱度查角色的中文名（Bangumi，背景）
    "zh_mode": "tw",                  # 中文：tw 繁體（台灣用語、台灣譯名）/ cn 简体（大陸用語、大陸譯名）
    "general_lang": "zh",             # 一般描述 tag 顯示：zh 中文對照 / raw 原始
    "grid_mode": "justified",         # 格狀：justified 依原比例 / square 正方形
    "setup_done": False,              # 首次設定精靈是否完成
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS media(
  id INTEGER PRIMARY KEY,
  path TEXT UNIQUE NOT NULL,
  kind TEXT NOT NULL,
  size INTEGER, mtime REAL,
  width INTEGER, height INTEGER, duration REAL,
  dhash INTEGER,
  rating TEXT, rating_scores TEXT,
  nsfw_auto INTEGER DEFAULT 0,
  nsfw_manual INTEGER,
  status TEXT DEFAULT 'pending',
  error TEXT,
  added_at REAL, tagged_at REAL,
  embedding BLOB,
  media_type TEXT, set_key TEXT, taken_at REAL,
  excluded INTEGER DEFAULT 0,
  fp TEXT                     -- 內容指紋（圖片 = MD5），搬家/改名後用來接回標記
);
CREATE INDEX IF NOT EXISTS ix_media_status ON media(status);
CREATE INDEX IF NOT EXISTS ix_media_added ON media(added_at);

CREATE TABLE IF NOT EXISTS tags(
  id INTEGER PRIMARY KEY,
  name TEXT UNIQUE NOT NULL,
  category TEXT NOT NULL DEFAULT 'custom',
  pinned INTEGER DEFAULT 0,
  last_viewed REAL,
  auto_block INTEGER DEFAULT 0
);

-- state: 0 = 候選（未確認）, 1 = 已確認, -1 = 已否決（「不是 X」）
-- source: model（WD）, manual, danbooru, pixiv, rule34, e621, knn（你的圖庫）, album（作品集整本）, aimeta（生成提示詞）,
--         tracemoe, signature, x（X 帳號）…；舊的 set（同一組圖）、match（整張圖比對）已停用，啟動時清掉
-- tier（候選優先序，越小越可信，見 candidates.T_*）: 1 原檔完全相符的搜源, 2 相似圖搜源, 3 Pixiv／生成提示詞, 4 AI（WD、你的圖庫）
CREATE TABLE IF NOT EXISTS media_tags(
  media_id INTEGER NOT NULL REFERENCES media(id) ON DELETE CASCADE,
  tag_id INTEGER NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
  score REAL, source TEXT, state INTEGER NOT NULL DEFAULT 0,
  tier INTEGER, reason TEXT,
  PRIMARY KEY(media_id, tag_id)
);
CREATE INDEX IF NOT EXISTS ix_mt_tag ON media_tags(tag_id, state);

CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT);
-- 圖的編號不重複使用：刪掉最後幾張後，新圖不能拿到舊編號（瀏覽器快取、最近看過、撤銷紀錄都用編號）
CREATE TABLE IF NOT EXISTS media_seq(k INTEGER PRIMARY KEY, hi INTEGER NOT NULL);
INSERT OR IGNORE INTO media_seq VALUES(1, 0);
CREATE TRIGGER IF NOT EXISTS media_seq_del AFTER DELETE ON media BEGIN
  UPDATE media_seq SET hi=MAX(hi, OLD.id) WHERE k=1;
END;
-- 新角色辨識（charknn）：每筆推薦跟你確認過的哪幾張很像
CREATE TABLE IF NOT EXISTS knn_refs(media_id INTEGER NOT NULL, tag TEXT NOT NULL, refs TEXT, PRIMARY KEY(media_id, tag));
-- 重複圖逐對驗證的結果（dupverify）：fa/fb 是檔案指紋，檔案換了就重算
CREATE TABLE IF NOT EXISTS dup_check(a INTEGER NOT NULL, b INTEGER NOT NULL, fa TEXT, fb TEXT, kind TEXT NOT NULL,
  score REAL, small INTEGER, box TEXT, PRIMARY KEY(a, b));
-- 合併後的舊名稱 → 目標 tag（之後任何來源再建立舊名稱都會自動導向）
CREATE TABLE IF NOT EXISTS tag_alias(name TEXT PRIMARY KEY, tag_id INTEGER NOT NULL REFERENCES tags(id) ON DELETE CASCADE);
CREATE TABLE IF NOT EXISTS actions(id INTEGER PRIMARY KEY, ts REAL, label TEXT, payload TEXT);

-- 角色資料卡（Danbooru wiki）
CREATE TABLE IF NOT EXISTS tag_info(
  tag_id INTEGER PRIMARY KEY REFERENCES tags(id) ON DELETE CASCADE,
  status TEXT,                -- ok | notfound | error
  copyright TEXT, copyrights TEXT, other_names TEXT, summary TEXT,
  post_count INTEGER, url TEXT, manual INTEGER DEFAULT 0,
  fetched_at REAL, error TEXT, links TEXT
);
-- 作品集：同人本、cos 圖包、畫集…（你自己建的，一組有順序的圖；整本的標籤會套到每一頁，見 albums.py）
CREATE TABLE IF NOT EXISTS albums(id INTEGER PRIMARY KEY, title TEXT NOT NULL, kind TEXT, cover INTEGER, note TEXT,
  source_url TEXT, folder TEXT, created_at REAL);
CREATE TABLE IF NOT EXISTS album_items(album_id INTEGER NOT NULL REFERENCES albums(id) ON DELETE CASCADE,
  media_id INTEGER NOT NULL REFERENCES media(id) ON DELETE CASCADE, pos REAL, PRIMARY KEY(album_id, media_id));
CREATE INDEX IF NOT EXISTS ix_album_media ON album_items(media_id);
CREATE TABLE IF NOT EXISTS album_tags(album_id INTEGER NOT NULL REFERENCES albums(id) ON DELETE CASCADE,
  tag_id INTEGER NOT NULL REFERENCES tags(id) ON DELETE CASCADE, PRIMARY KEY(album_id, tag_id));
-- AI 生成的圖：檔案裡的生成資料（aigen.py）
CREATE TABLE IF NOT EXISTS ai_meta(media_id INTEGER PRIMARY KEY REFERENCES media(id) ON DELETE CASCADE,
  tool TEXT, model TEXT, loras TEXT, prompt TEXT, negative TEXT, seed TEXT, params TEXT);
-- 已存的搜尋（智慧相簿）
CREATE TABLE IF NOT EXISTS saved_search(id INTEGER PRIMARY KEY, name TEXT NOT NULL, query TEXT NOT NULL,
  view TEXT, pinned INTEGER DEFAULT 1, created_at REAL);
-- 以圖搜源結果
CREATE TABLE IF NOT EXISTS media_source(
  media_id INTEGER PRIMARY KEY REFERENCES media(id) ON DELETE CASCADE,
  status TEXT,                -- found | notfound | error
  method TEXT,                -- md5 | iqdb | pixiv
  similarity REAL, post_url TEXT, source_url TEXT, pixiv_id INTEGER,
  data TEXT, fetched_at REAL, error TEXT
);
"""

_local = threading.local()
class _WriteLock:
    """所有寫入都包在這個鎖裡。最外層離開時自動 commit：
    SQLite 同時只允許一個寫入者，只要有人寫了沒 commit 就一直占著，別的執行緒寫入會等到逾時
    （database is locked）。以前 tag_id() 這類小寫入沒有 commit，搜源和你按按鈕同時發生就會撞到。"""

    def __init__(self):
        self._lock = threading.RLock()
        self._depth = threading.local()

    def acquire(self, *a, **k):
        outer = getattr(self._depth, "n", 0) == 0
        t0 = time.perf_counter() if outer else 0.0
        ok = self._lock.acquire(*a, **k)
        if ok:
            self._depth.n = getattr(self._depth, "n", 0) + 1
            if outer:       # 等了多久、從什麼時候開始抱（app/perf.py，b93）
                now = time.perf_counter()
                self._depth.t = now
                from . import perf
                perf.lock_waited((now - t0) * 1000)
        return ok

    def release(self):
        n = getattr(self._depth, "n", 1) - 1
        self._depth.n = n
        try:
            if n == 0:
                c = getattr(_local, "conn", None)
                if c is not None and c.in_transaction:
                    c.commit()
        finally:
            held = (time.perf_counter() - getattr(self._depth, "t", time.perf_counter())) * 1000 if n == 0 else None
            self._lock.release()
            if held is not None:
                from . import perf
                perf.lock_held(held)

    def held(self) -> bool:
        return getattr(self._depth, "n", 0) > 0

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, et, ev, tb):
        if et is not None and getattr(self._depth, "n", 1) == 1:
            # 最外層出錯：把這次沒完成的寫入整批撤回，不要半套寫進資料庫
            c = getattr(_local, "conn", None)
            if c is not None and c.in_transaction:
                try:
                    c.rollback()
                except Exception:  # noqa
                    pass
        self.release()
        return False


write_lock = _WriteLock()
UNLOCKED_WRITES: list = []


# ---- 讀取快取（b96）：側欄、首頁、書架、未識別分流這幾個「掃整個圖庫」的讀取，資料沒變就不重算 ----
# 「有沒有變」看 WAL 檔（每次寫入都會改它的大小／時間）。背景工作一直在寫的時候不想每秒重算，所以 grace 秒內沿用；
# 但「你按了什麼」（任何非 GET 請求，server.guard 呼叫 user_wrote()）一定重算，畫面才會馬上對。最久 ttl 秒一定重算。
_rc: dict = {}
_rc_lock = threading.Lock()
_user_gen = 0
BUSY = lambda: False     # server 啟動時換成「背景工作在不在跑」；沒在跑時 WAL 一變就重算，不等 grace
READ_CACHE = not os.environ.get("LIB_NO_READ_CACHE")


def clear_read_cache():
    with _rc_lock:
        _rc.clear()


_bg_at = 0.0


def bg_wrote():
    """瀏覽器腳本一張一張送進來（匯入、ascii2d）：當成背景工作，之後 10 秒內 grace 照樣沿用。"""
    global _bg_at
    _bg_at = time.time()


def user_wrote():
    global _user_gen
    _user_gen += 1


def _wal_key():
    try:
        st = os.stat(str(DB_PATH) + "-wal")
        return (st.st_size, st.st_mtime_ns)
    except OSError:
        return None


def read_cache(name: str, args: tuple, fn, grace: float = 5.0, ttl: float = 60.0, extra=None):
    """fn() 的結果依 (name, args) 快取。extra：另外要一起比的東西（例如背景工作在不在跑）。"""
    if not READ_CACHE:
        return fn()
    k = (name, args)
    now = time.time()
    wal = _wal_key()
    with _rc_lock:
        e = _rc.get(k)
    if e and e["gen"] == _user_gen and e["extra"] == extra and now - e["at"] < ttl and (e["wal"] == wal or (now - e["at"] < grace and (now - _bg_at < 10 or BUSY()))):
        return e["v"]
    v = fn()
    with _rc_lock:
        if len(_rc) > 64:
            _rc.clear()
        _rc[k] = {"v": v, "at": now, "wal": wal, "gen": _user_gen, "extra": extra}
    return v


def conn() -> sqlite3.Connection:
    c = getattr(_local, "conn", None)
    if c is None:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        c = sqlite3.connect(DB_PATH, timeout=30, check_same_thread=False)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA synchronous=NORMAL")
        c.execute("PRAGMA foreign_keys=ON")
        # 讀取加速（實測書架／側欄／首頁快約 35%）：資料庫用 mmap 讀（作業系統共用快取，不會每個執行緒各佔一份），
        # 每個連線的頁快取 16 MB（預設只有 2 MB，資料庫有 50 MB）；WAL 檔超過 32 MB 就截短
        c.execute("PRAGMA mmap_size=268435456")
        c.execute("PRAGMA cache_size=-16384")
        c.execute("PRAGMA journal_size_limit=33554432")
        if os.environ.get("LIB_CHECK_LOCK"):  # 測試用：抓出沒包在 write_lock 裡的寫入
            def trace(sql, _c=c):
                head = sql.lstrip()[:7].upper()
                if head.startswith(("INSERT", "UPDATE", "DELETE", "REPLACE")) and not write_lock.held():
                    import traceback
                    UNLOCKED_WRITES.append((sql[:80], "".join(traceback.format_stack(limit=6)[:-1])))
            c.set_trace_callback(trace)
        _local.conn = c
    return c


def init():
    """建表＋依版本遷移。資料庫版本記在 PRAGMA user_version：每個遷移只在升級時跑一次，
    以前是每次啟動都檢查一長串欄位、再跑幾個全表 UPDATE／DELETE。"""
    with write_lock:
        c = conn()
        c.executescript(SCHEMA)
        c.execute("UPDATE media_seq SET hi=MAX(hi, (SELECT IFNULL(MAX(id), 0) FROM media)) WHERE k=1")
        ver = c.execute("PRAGMA user_version").fetchone()[0]
        for n, fn in enumerate(MIGRATIONS, start=1):
            if ver < n:
                fn(c)
                c.execute(f"PRAGMA user_version={n}")
                c.commit()
        c.commit()


def _cols(c, table: str) -> set[str]:
    return {r[1] for r in c.execute(f"PRAGMA table_info({table})")}


def _m1_legacy(c):
    """v1：2026-10 以前散落的升級（欄位補齊、舊來源改名、舊設定清掉）。每一步都可以重跑，所以舊資料庫、新資料庫都安全。"""
    cols = {r[1] for r in c.execute("PRAGMA table_info(tag_info)")}
    if "links" not in cols:  # v1.2：創作者連結（Pixiv / Twitter …）
        c.execute("ALTER TABLE tag_info ADD COLUMN links TEXT")
    tcols = {r[1] for r in c.execute("PRAGMA table_info(tags)")}
    if "auto_block" not in tcols:  # v1.3：停止自動標註
        c.execute("ALTER TABLE tags ADD COLUMN auto_block INTEGER DEFAULT 0")
    if "no_artist" not in tcols:   # 有這個標籤的圖＝作者不可考（梗圖、表情包、截圖），不算缺創作者（見 quicktags.sync_no_artist）
        c.execute("ALTER TABLE tags ADD COLUMN no_artist INTEGER DEFAULT 0")
    mcols = {r[1] for r in c.execute("PRAGMA table_info(media)")}
    if "ai_gen" not in mcols:      # AI 生成的圖：ComfyUI／Stable Diffusion／NovelAI（aigen.py；'' = 查過不是）
        c.execute("ALTER TABLE media ADD COLUMN ai_gen TEXT")
    if "artist_done" not in mcols:  # 「不用找作者」：自己畫的、AI 生成、找不到也沒關係 → 不算缺創作者
        c.execute("ALTER TABLE media ADD COLUMN artist_done INTEGER")
    if "pile" not in mcols:        # 未識別的分堆（piles.py）
        c.execute("ALTER TABLE media ADD COLUMN pile TEXT")
    if "people" not in mcols:      # 多人圖：人數（描述標籤推估）、你按過「角色標完了」
        c.execute("ALTER TABLE media ADD COLUMN people INTEGER")
        c.execute("ALTER TABLE media ADD COLUMN people_done INTEGER DEFAULT 0")
    # v1.3：線上來源細分為 danbooru / pixiv
    c.execute("UPDATE media_tags SET source='danbooru' WHERE source='online'")
    # v1.4：候選優先序、媒材、同一組圖
    tc = {r[1] for r in c.execute("PRAGMA table_info(media_tags)")}
    if "tier" not in tc:
        c.execute("ALTER TABLE media_tags ADD COLUMN tier INTEGER")
        c.execute("ALTER TABLE media_tags ADD COLUMN reason TEXT")
        c.execute("""UPDATE media_tags SET tier=CASE source WHEN 'danbooru' THEN 2 WHEN 'pixiv' THEN 2
                     WHEN 'set' THEN 3 WHEN 'model' THEN 4 WHEN 'match' THEN 5 END WHERE state=0""")
    if "media_type" not in mcols:
        c.execute("ALTER TABLE media ADD COLUMN media_type TEXT")
        c.execute("ALTER TABLE media ADD COLUMN set_key TEXT")
        c.execute("ALTER TABLE media ADD COLUMN taken_at REAL")
    c.execute("CREATE INDEX IF NOT EXISTS ix_media_set ON media(set_key)")
    if "official" not in {r[1] for r in c.execute("PRAGMA table_info(media)")}:   # 官方作品：動畫／漫畫截圖、小說插圖
        c.execute("ALTER TABLE media ADD COLUMN official TEXT")
        c.execute("ALTER TABLE media ADD COLUMN official_src TEXT")
    # v1.5：顯示名稱、Danbooru 對應建議、角色封面、排除
    ic = {r[1] for r in c.execute("PRAGMA table_info(tag_info)")}
    if "display_name" not in ic:
        c.execute("ALTER TABLE tag_info ADD COLUMN display_name TEXT")
        c.execute("ALTER TABLE tag_info ADD COLUMN match_suggest TEXT")
    tc2 = {r[1] for r in c.execute("PRAGMA table_info(tags)")}
    if "cover" not in tc2:
        c.execute("ALTER TABLE tags ADD COLUMN cover INTEGER")
    mc2 = {r[1] for r in c.execute("PRAGMA table_info(media)")}
    if "excluded" not in mc2:
        c.execute("ALTER TABLE media ADD COLUMN excluded INTEGER DEFAULT 0")
    c.execute("CREATE INDEX IF NOT EXISTS ix_mt_state ON media_tags(state, tier)")
    # v2.0：內容指紋、移除沒在用的欄位與整張圖 kNN 設定
    mc3 = {r[1] for r in c.execute("PRAGMA table_info(media)")}
    if "fp" not in mc3:
        c.execute("ALTER TABLE media ADD COLUMN fp TEXT")
    if "reviewed" in mc3:
        try:
            c.execute("ALTER TABLE media DROP COLUMN reviewed")
        except sqlite3.OperationalError:
            pass  # 舊版 SQLite 不支援 DROP COLUMN，留著也無害
    c.execute("CREATE INDEX IF NOT EXISTS ix_media_fp ON media(fp)")
    # v2.2：未識別頁「略過」（留在圖庫，但不再出現在未識別的待辦裡）
    if "unrec_skip" not in {r[1] for r in c.execute("PRAGMA table_info(media)")}:
        c.execute("ALTER TABLE media ADD COLUMN unrec_skip INTEGER DEFAULT 0")
    c.execute("CREATE INDEX IF NOT EXISTS ix_media_mtime ON media(mtime)")
    c.execute("DELETE FROM settings WHERE key LIKE 'match\\_%' ESCAPE '\\'")
    c.execute("DELETE FROM media_tags WHERE source='match' AND state=0")
    if c.execute("SELECT value FROM settings WHERE key='roots'").fetchone() and \
            not c.execute("SELECT 1 FROM settings WHERE key='setup_done'").fetchone():
        c.execute("INSERT INTO settings(key, value) VALUES('setup_done', 'true')")  # 舊使用者跳過精靈
    if not c.execute("SELECT 1 FROM settings WHERE key='_v21'").fetchone():
        # v2.1：優先序 Danbooru > Pixiv > WD Tagger > 同一組圖；舊的寬鬆連號分組重新計算
        c.execute("UPDATE media_tags SET tier=5 WHERE state=0 AND source='set'")
        c.execute("UPDATE media_tags SET tier=3 WHERE state=0 AND source='pixiv' AND tier=2")
        c.execute("DELETE FROM settings WHERE key='set_window_min'")
        c.execute("INSERT OR REPLACE INTO settings(key, value) VALUES('_v21', 'true')")
    if not c.execute("SELECT 1 FROM settings WHERE key='_v14'").fetchone():
        # 舊版預設開啟的整張圖比對，升級時關掉（畫風干擾大，改由候選佇列＋搜源為主）
        c.execute("INSERT OR REPLACE INTO settings(key, value) VALUES('_v14', 'true')")


def _m2_trash(c):
    """v2：刪除改成丟資源回收筒＋保留紀錄 30 天（media.trashed_at）。"""
    if "trashed_at" not in _cols(c, "media"):
        c.execute("ALTER TABLE media ADD COLUMN trashed_at REAL")


def _m3_job_queue(c):
    """v3：線上工作佇列存進資料庫（jobqueue.py）。"""
    from .jobqueue import SCHEMA as JQ
    for stmt in JQ.split(";"):
        if stmt.strip():
            c.execute(stmt)


def _m4_ascii2d(c):
    """v4：ascii2d 半自動找出處的佇列與結果（ascii2d.py）。"""
    from .ascii2d import SCHEMA as A
    for stmt in A.split(";"):
        if stmt.strip():
            c.execute(stmt)


def _m5_upgrade(c):
    """v5：換成高清版（upgrade.py）。"""
    from .upgrade import SCHEMA as U
    for stmt in U.split(";"):
        if stmt.strip():
            c.execute(stmt)


def _m6_name_zh(c):
    """v6：Bangumi 中文名（zhname.py）。"""
    from .zhname import SCHEMA as Z
    c.execute(Z)


def _m7_char_base(c):
    """v7：同一個角色的本體和造型（people.char_base），算人數時只算一個。"""
    from .people import CHAR_BASE_SCHEMA
    c.execute(CHAR_BASE_SCHEMA)


def _m8_skip_by_tag(c):
    """v8：unrec_skip 分兩種——1＝你按了「略過」、2＝因為加了會略過的快速標籤（梗圖…）才略過。
    拿掉梗圖時只把 2 的放回未識別（以前一律放回，連你本來就按過 L 的也回來）。
    舊資料分不出來：有會略過標籤的照舊當成 2（跟以前的行為一樣）。"""
    r = c.execute("SELECT value FROM settings WHERE key='quick_tags_skip'").fetchone()
    try:
        names = json.loads(r[0]) if r else DEFAULT_SETTINGS.get("quick_tags_skip", [])
    except Exception:  # noqa
        names = DEFAULT_SETTINGS.get("quick_tags_skip", [])
    names = [n.replace(" ", "_") for n in names]
    if names:
        ph = ",".join("?" * len(names))
        c.execute(f"""UPDATE media SET unrec_skip=2 WHERE unrec_skip=1 AND id IN (SELECT x.media_id FROM media_tags x
            JOIN tags t ON t.id=x.tag_id WHERE x.state=1 AND t.name IN ({ph}))""", names)


def _m9_edited_at(c):
    """v9：media.edited_at＝你最後一次動這張圖的時間（排序「最近操作」）。用還留著的撤銷紀錄（最後 100 筆）補。"""
    if "edited_at" not in _cols(c, "media"):
        c.execute("ALTER TABLE media ADD COLUMN edited_at REAL")
    c.execute("CREATE INDEX IF NOT EXISTS ix_media_edited ON media(edited_at)")
    for r in c.execute("SELECT ts, payload FROM actions ORDER BY id").fetchall():
        try:
            mids = _action_refs(json.loads(r[1]))[0]
        except Exception:  # noqa
            continue
        c.executemany("UPDATE media SET edited_at=? WHERE id=?", [(r[0], m) for m in mids])


def _m10_edited_ascii2d(c):
    """v10：ascii2d 查過的（你按了找到／沒找到／跳過）也算「最近操作」：用 ascii2d.decided_at 補。"""
    if c.execute("SELECT 1 FROM sqlite_master WHERE name='ascii2d'").fetchone():
        c.execute("""UPDATE media SET edited_at=(SELECT a.decided_at FROM ascii2d a WHERE a.media_id=media.id)
            WHERE id IN (SELECT media_id FROM ascii2d WHERE decided_at IS NOT NULL)
              AND COALESCE(edited_at, 0) < (SELECT a.decided_at FROM ascii2d a WHERE a.media_id=media.id)""")


def _m11_pixai(c):
    """v11：PixAI Tagger 的結果（機率 ≥ 0.1 的角色／畫師／作品，JSON）。fp 跟 media.fp 不同＝檔案換過，要重跑。"""
    c.execute("""CREATE TABLE IF NOT EXISTS pixai_pred(media_id INTEGER PRIMARY KEY, fp TEXT, chars TEXT, artists TEXT,
                 copyrights TEXT, error TEXT, at REAL)""")


def _m12_sweep(c):
    """v12：從畫師作品全集反查（app/sweep.py）：每位畫師看過幾件、看過的縮圖、X 的名單。
    （當時也建了 E-Hentai 以圖搜圖的 eh_search，b96 拿掉這個功能、v20 刪表）"""
    from .sweep import SCHEMA
    c.executescript(SCHEMA)


def _m13_album_index(c):
    """v13：作品集的圖要隔絕（不自動推身分、不搜源）：到處都要問「這張在不在作品集裡」→ album_items(media_id) 索引。"""
    c.execute("CREATE INDEX IF NOT EXISTS ix_album_items_media ON album_items(media_id)")


def in_album(mid: int) -> bool:
    return conn().execute("SELECT 1 FROM album_items WHERE media_id=? LIMIT 1", (int(mid),)).fetchone() is not None


def _m14_op_log(c):
    """v14：操作紀錄（op_log）：每個會改資料的操作記一筆，不會被清掉（撤銷紀錄只留最後 100 筆）。用現有的撤銷紀錄補。"""
    c.executescript(OP_LOG_SCHEMA)
    for r in c.execute("SELECT id, ts, label, payload FROM actions ORDER BY id").fetchall():
        try:
            p = json.loads(r[3])
        except Exception:  # noqa
            p = {}
        mids, tids = _action_refs(p)
        _insert_op(c, r[1], r[2], sorted(mids), sorted(t for t in tids if t), "+".join(sorted(p)), None, None, r[0])


def _m15_ascii2d_picked(c):
    """v15：ascii2d 佇列記「你自己挑的」（picked）：已經有創作者的圖，你特地選了才留在佇列裡（b75）。"""
    cols = {r[1] for r in c.execute("PRAGMA table_info(ascii2d)")}
    if "picked" not in cols:
        c.execute("ALTER TABLE ascii2d ADD COLUMN picked INTEGER NOT NULL DEFAULT 0")


def _m16_mediaclf(c):
    """v16（b77）：媒材改用你標的學（app/mediaclf.py）：media.media_src／media_conf、表 media_label。"""
    from .mediaclf import ensure_schema
    ensure_schema(c)


def _m17_clip(c):
    """v17（b78）：CLIP 圖像特徵（app/clipemb.py），媒材分類的第二種特徵。"""
    from .clipemb import ensure_schema
    ensure_schema(c)


def _m18_implies(c):
    """v18（b84）：標籤蘊含（app/implies.py）：表 tag_implies、implies_checked。"""
    from .implies import ensure_schema
    ensure_schema(c)


def _m19_bg_tasks(c):
    """v19（b93）：背景工作登記表 bg_tasks（app/tasks.py）、快速標籤學習 qt_pred／qt_no（app/qtlearn.py）。"""
    from .tasks import ensure_schema
    ensure_schema(c)
    from . import qtlearn       # 快速標籤從你的標記學：qt_pred、qt_no
    qtlearn.ensure_schema(c)


def _m20_converge(c):
    """v20（b96，第九次檢查「收斂」）：
    - Pixiv 標籤推角色（搜源找到 Pixiv 時用標籤猜角色）改成只記下：你按「是」5、「不是」23（準 18%）。
      你自己改過的不動（還是預設的 candidate 才改）
    - ascii2d 除錯 HTML（raw）只留最近 200 列：4,988 列、42 MB，程式不讀它
    - 拿掉的功能留下的表：E-Hentai 以圖搜圖、CCIP 逐人比對、trace.moe（e621 的 e621_checked 留著：e621 網址匯入還會用）
    - 做完以後 run.py 會整理一次資料庫檔案（VACUUM），讓檔案和備份真的變小"""
    r = c.execute("SELECT value FROM settings WHERE key='act_pixiv'").fetchone()
    if r is None or json.loads(r[0]) == "candidate":
        c.execute("INSERT OR REPLACE INTO settings(key, value) VALUES('act_pixiv', ?)", (json.dumps("record"),))
    trim_ascii2d_raw(c)
    for t in ("eh_search", "media_person", "anime_frame", "anilist_map"):
        c.execute(f"DROP TABLE IF EXISTS {t}")
    c.execute("INSERT OR REPLACE INTO settings(key, value) VALUES('_vacuum', 'true')")


def _m21_bgm_groups(c):
    """v21（b110.6）：Bangumi 的「組織」角色頁（少女與戰車的河馬隊、聖葛羅莉安娜女子學院…）被當成隊員的中文名。
    zhname 之後會跳過組織（type 4）；這裡把已經套上的刪掉，讓背景重查。條件：同一個 Bangumi 頁對到 3 個以上的角色、名字像隊伍／學校。"""
    try:
        rows = c.execute("""SELECT ref FROM name_zh WHERE status='found' AND ref LIKE 'bgm:character/%'
                            GROUP BY ref HAVING COUNT(DISTINCT name) >= 2""").fetchall()
    except sqlite3.OperationalError:
        return
    import re as _re
    bad = []
    for (ref,) in rows:
        zh = c.execute("SELECT zh FROM name_zh WHERE ref=? LIMIT 1", (ref,)).fetchone()[0] or ""
        if _re.search(r"(隊|高中|學園|學院|高校)$", zh):
            bad.append(ref)
    for ref in bad:
        c.execute("DELETE FROM name_zh WHERE ref=? AND status='found'", (ref,))


def _m22_group_names(c):
    """v22（b110.8）：角色的中文名是學校、隊伍名的（Bangumi 組織頁沒標 type 4 的）刪掉重查。"""
    try:
        rows = c.execute("SELECT name, zh FROM name_zh WHERE status='found' AND category='character'").fetchall()
    except sqlite3.OperationalError:
        return
    import re as _re
    bad = [r[0] for r in rows if _re.search(r"(高校|高中|學園|學院|隊|組)$", r[1] or "")]
    c.executemany("DELETE FROM name_zh WHERE name=?", [(n,) for n in bad])


def trim_ascii2d_raw(c, keep: int = 200) -> int:
    """ascii2d 結果頁的原始 HTML（ascii2d 改版時看的）只留最近 keep 列。"""
    try:
        return c.execute("""UPDATE ascii2d SET raw=NULL WHERE raw IS NOT NULL AND media_id NOT IN
            (SELECT media_id FROM ascii2d WHERE raw IS NOT NULL ORDER BY COALESCE(searched_at, 0) DESC LIMIT ?)""", (keep,)).rowcount
    except sqlite3.OperationalError:
        return 0


def vacuum_if_requested() -> bool:
    """遷移刪了很多資料時（v20）：整理一次資料庫檔案。只在 run.py 啟動伺服器之前呼叫（沒有別的執行緒在用資料庫）。"""
    c = conn()
    if not c.execute("SELECT 1 FROM settings WHERE key='_vacuum'").fetchone():
        return False
    with write_lock:
        c.execute("DELETE FROM settings WHERE key='_vacuum'")
        c.commit()
    c.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    c.execute("VACUUM")
    global _settings_cache
    _settings_cache = None
    return True


MIGRATIONS = [_m1_legacy, _m2_trash, _m3_job_queue, _m4_ascii2d, _m5_upgrade, _m6_name_zh, _m7_char_base, _m8_skip_by_tag, _m9_edited_at,
              _m10_edited_ascii2d, _m11_pixai, _m12_sweep, _m13_album_index, _m14_op_log,
              _m15_ascii2d_picked, _m16_mediaclf, _m17_clip, _m18_implies, _m19_bg_tasks, _m20_converge, _m21_bgm_groups, _m22_group_names]   # 新的遷移加在最後面，不要改前面的順序


# ---------- settings ----------
_settings_cache: dict | None = None


def get_settings() -> dict:
    global _settings_cache
    if _settings_cache is None:
        s = dict(DEFAULT_SETTINGS)
        for r in conn().execute("SELECT key, value FROM settings"):
            try:
                s[r["key"]] = json.loads(r["value"])
            except Exception:
                pass
        _settings_cache = s
    return dict(_settings_cache)


def update_settings(patch: dict) -> dict:
    global _settings_cache
    with write_lock:
        c = conn()
        for k, v in patch.items():
            if k in DEFAULT_SETTINGS:
                c.execute("INSERT OR REPLACE INTO settings(key, value) VALUES(?, ?)", (k, json.dumps(v)))
        c.commit()
        _settings_cache = None
        return get_settings()   # 在鎖裡重建快取，避免別的執行緒塞回舊的


# ---------- tags ----------
_tag_cache: dict[str, int] = {}


def tag_id(name: str, category: str = "custom", create: bool = True) -> int | None:
    name = name.strip()
    if name in _tag_cache:
        return _tag_cache[name]
    c = conn()
    r = c.execute("SELECT id FROM tags WHERE name=?", (name,)).fetchone() or \
        c.execute("SELECT tag_id AS id FROM tag_alias WHERE name=?", (name,)).fetchone()
    if r:
        _tag_cache[name] = r["id"]
        return r["id"]
    if not create:
        return None
    with write_lock:
        c.execute("INSERT OR IGNORE INTO tags(name, category) VALUES(?, ?)", (name, category))
        r = c.execute("SELECT id FROM tags WHERE name=?", (name,)).fetchone()
    _tag_cache[name] = r["id"]
    return r["id"]


def nsfw_expr(alias: str = "m") -> str:
    return f"COALESCE({alias}.nsfw_manual, {alias}.nsfw_auto)"


def recompute_nsfw_auto():
    ratings = get_settings()["nsfw_ratings"]
    with write_lock:
        c = conn()
        ph = ",".join("?" * len(ratings)) or "''"
        c.execute(f"UPDATE media SET nsfw_auto = CASE WHEN rating IN ({ph}) THEN 1 ELSE 0 END", ratings)
        c.commit()


# ---------- 操作紀錄（op_log） ----------
# 撤銷紀錄（actions）只留最後 100 筆、內容是還原用的大包資料；操作紀錄每個操作一小列、一直留著（2 年後清），給分析使用習慣用。
OP_LOG_SCHEMA = """
CREATE TABLE IF NOT EXISTS op_log(
  id INTEGER PRIMARY KEY,
  ts REAL NOT NULL,
  label TEXT NOT NULL,      -- 跟撤銷紀錄一樣的說明，例如「拿掉「梗圖」（1 張）」
  kind TEXT,                -- 去掉張數的說明，例如「拿掉「梗圖」」（分組統計用）
  keys TEXT,                -- 改了哪幾類資料（撤銷資料的種類，例如 tag_rows+skip）
  n INTEGER,                -- 動到幾張圖
  mids TEXT,                -- 動到的圖（JSON，最多 50 個）
  tids TEXT,                -- 動到的標籤（JSON，最多 20 個）
  view TEXT,                -- 在哪一頁做的（前端送 X-Lib-View；背景工作＝NULL）
  via TEXT,                 -- 怎麼做的：key（鍵盤）、mouse、touch、script（瀏覽器腳本）
  action_id INTEGER,        -- 對應的撤銷紀錄
  undone_at REAL            -- 撤銷的時間
);
CREATE INDEX IF NOT EXISTS ix_op_log_ts ON op_log(ts);
"""
OP_MIDS_MAX, OP_TIDS_MAX = 50, 20
OP_KEEP_DAYS = 730
# 這次請求是從哪一頁、用什麼方式送來的（server.py 的 OpContext 中介層設定；背景執行緒沒有 → None）
OP_CTX: contextvars.ContextVar[dict | None] = contextvars.ContextVar("op_ctx", default=None)


def op_kind(label: str) -> str:
    """「加上「梗圖」（3 張）＋相似 2 張」→「加上「梗圖」」：同一種操作歸在一起統計。"""
    k = re.sub(r"\s*[（(]\d[\d,]*\s*(張|筆|個|位|部|頁|本|個標籤)[^）)]*[）)]", "", label or "")
    return re.sub(r"\s*＋相似 \d+ 張$", "", k).strip()


def _insert_op(c, ts, label, mids, tids, keys, view, via, action_id):
    mids, tids = list(mids), list(tids)
    c.execute("INSERT INTO op_log(ts, label, kind, keys, n, mids, tids, view, via, action_id) VALUES(?,?,?,?,?,?,?,?,?,?)",
              (ts, label, op_kind(label), keys or None, len(mids), json.dumps(mids[:OP_MIDS_MAX]),
               json.dumps(tids[:OP_TIDS_MAX]), view, via, action_id))


def log_op(label: str, mids=(), tids=(), keys: str | None = None, action_id: int | None = None):
    """記一筆操作紀錄（不能撤銷的操作也記：移到回收筒、ascii2d 找到／沒找到、換成高清…）。失敗不影響操作本身。"""
    try:
        ctx = OP_CTX.get() or {}
        with write_lock:
            _insert_op(conn(), time.time(), label, sorted({int(x) for x in mids}), sorted({int(x) for x in tids if x}), keys,
                       ctx.get("view"), ctx.get("via"), action_id)
    except Exception as e:  # noqa
        import logging
        logging.getLogger("db").warning("操作紀錄寫入失敗：%s", e)


def prune_op_log():
    with write_lock:
        conn().execute("DELETE FROM op_log WHERE ts < ?", (time.time() - OP_KEEP_DAYS * 86400,))


# ---------- undo ----------
def push_action(label: str, payload: dict, touched: list[int] | set[int] | None = None):
    """記一筆撤銷紀錄，同時記下「你最近動過這幾張」（media.edited_at，排序「最近操作」用）和操作紀錄（op_log）。
    touched：你實際操作的圖；沒給就用紀錄裡提到的圖（否決傳播這種連帶改到的，呼叫端要自己給 touched）。"""
    with write_lock:
        c = conn()
        now = time.time()
        cur = c.execute("INSERT INTO actions(ts, label, payload) VALUES(?, ?, ?)", (now, label, json.dumps(payload)))
        refs, tids = _action_refs(payload)
        mids = set(int(x) for x in touched) if touched is not None else refs
        if mids:
            c.executemany("UPDATE media SET edited_at=? WHERE id=?", [(now, m) for m in mids])
        c.execute("DELETE FROM actions WHERE id NOT IN (SELECT id FROM actions ORDER BY id DESC LIMIT 100)")
        log_op(label, mids, tids, "+".join(sorted(payload)), cur.lastrowid)


def snapshot_tag_rows(media_ids: list[int], tid: int) -> list:
    """記錄修改前的 media_tags 狀態，供撤銷。"""
    c = conn()
    out = []
    for i in range(0, len(media_ids), 500):
        chunk = media_ids[i:i + 500]
        ph = ",".join("?" * len(chunk))
        found = {r["media_id"]: r for r in c.execute(
            f"SELECT * FROM media_tags WHERE tag_id=? AND media_id IN ({ph})", [tid, *chunk])}
        for mid in chunk:
            r = found.get(mid)
            out.append([mid, tid, None if r is None else [r["score"], r["source"], r["state"], r["tier"], r["reason"]]])
    return out


def restore_tag_rows(rows: list):
    c = conn()
    alive_m = {r[0] for r in c.execute(f"SELECT id FROM media WHERE id IN ({','.join(str(int(x[0])) for x in rows) or 'NULL'})")}
    alive_t = {r[0] for r in c.execute(f"SELECT id FROM tags WHERE id IN ({','.join(str(int(x[1])) for x in rows) or 'NULL'})")}
    for mid, tid, prev in rows:
        if mid not in alive_m or tid not in alive_t:
            continue   # 圖或標籤已經刪掉了
        if prev is None:
            c.execute("DELETE FROM media_tags WHERE media_id=? AND tag_id=?", (mid, tid))
        else:
            prev = list(prev) + [None] * (5 - len(prev))  # 舊版撤銷紀錄沒有 tier/reason
            c.execute("INSERT OR REPLACE INTO media_tags(media_id, tag_id, score, source, state, tier, reason) VALUES(?,?,?,?,?,?,?)",
                      (mid, tid, *prev[:5]))


def _action_refs(p: dict) -> tuple[set[int], set[int]]:
    mids, tids = set(), set()
    for mid, tid, _ in p.get("tag_rows", []):
        mids.add(mid)
        tids.add(tid)
    for k in ("excluded", "nsfw", "skip", "artist_done", "official", "people_done", "upgrade_status"):
        mids.update(x[0] for x in p.get(k, []))
    if "merge" in p:
        tids.add(p["merge"].get("src_id"))
    if "delete_tag" in p:
        mids.update(r[0] for r in p["delete_tag"]["rows"])
    return mids, tids


def purge_actions(mids: set[int] | None = None, tids: set[int] | None = None):
    """刪除圖片／標籤後，把提到它們的撤銷紀錄一起清掉：
    不然撤銷時會寫回不存在的圖（整個撤銷卡死），或 id 被新檔案重用時套到別張圖上。必須在 write_lock 內呼叫。"""
    mids, tids = mids or set(), tids or set()
    c = conn()
    for r in c.execute("SELECT id, payload FROM actions").fetchall():
        try:
            m, t = _action_refs(json.loads(r["payload"]))
        except Exception:  # noqa
            continue
        if m & mids or t & tids:
            c.execute("DELETE FROM actions WHERE id=?", (r["id"],))


def undo_last() -> str | None:
    with write_lock:
        c = conn()
        r = c.execute("SELECT * FROM actions ORDER BY id DESC LIMIT 1").fetchone()
        if not r:
            return None
        p = json.loads(r["payload"])
        try:
            _apply_undo(c, p)
            mids = _action_refs(p)[0]          # 撤銷也算你動過（排序「最近操作」）
            if mids:
                c.executemany("UPDATE media SET edited_at=? WHERE id=?", [(time.time(), m) for m in mids])
        except Exception as e:  # noqa  還原不了（圖或標籤已經不在）：丟掉這筆，不要讓撤銷卡死
            c.rollback()
            c.execute("DELETE FROM actions WHERE id=?", (r["id"],))
            c.commit()
            return f"{r['label']}（已無法還原：{e}）"
        c.execute("DELETE FROM actions WHERE id=?", (r["id"],))
        c.execute("UPDATE op_log SET undone_at=? WHERE action_id=?", (time.time(), r["id"]))
        log_op(f"撤銷：{r['label']}", _action_refs(p)[0], keys="undo")
        c.commit()
        return r["label"]


def _apply_undo(c, p: dict):
    if True:
        if "tag_rows" in p:
            restore_tag_rows(p["tag_rows"])
        if "merge" in p:
            _undo_merge(c, p["merge"])
            tid, info = p.get("dst_info") or [None, None]
            if tid and info:
                c.execute("UPDATE tag_info SET other_names=?, links=? WHERE tag_id=?", (info.get("other_names"), info.get("links"), tid))
            elif tid:
                c.execute("DELETE FROM tag_info WHERE tag_id=? AND copyright IS NULL AND summary IS NULL", (tid,))
        if "delete_tag" in p:
            _undo_delete_tag(c, p["delete_tag"])
        if "auto_block" in p:
            tid, prev = p["auto_block"]
            c.execute("UPDATE tags SET auto_block=? WHERE id=?", (prev, tid))
        if "excluded" in p:
            c.executemany("UPDATE media SET excluded=? WHERE id=?", [(v, mid) for mid, v in p["excluded"]])
        if "nsfw" in p:
            c.executemany("UPDATE media SET nsfw_manual=? WHERE id=?", [(v, mid) for mid, v in p["nsfw"]])
        if "skip" in p:
            c.executemany("UPDATE media SET unrec_skip=? WHERE id=?", [(v, mid) for mid, v in p["skip"]])
        if "album" in p:
            from . import albums
            albums.restore(c, p["album"])
        if "artist_done" in p:
            c.executemany("UPDATE media SET artist_done=? WHERE id=?", [(v, mid) for mid, v in p["artist_done"]])
        if "official" in p:
            c.executemany("UPDATE media SET official=?, official_src=? WHERE id=?", [(k, s, mid) for mid, k, s in p["official"]])
        if "media_label" in p:      # 媒材（b77）
            from . import mediaclf
            mediaclf.undo_rows(c, p["media_label"])
        if "people_done" in p:      # 多人圖「標完了」
            c.executemany("UPDATE media SET people_done=? WHERE id=?", [(v, mid) for mid, v in p["people_done"]])
        if "upgrade_status" in p:   # 「不用換高清」
            c.executemany("UPDATE upgrade SET status=? WHERE media_id=?", [(v, mid) for mid, v in p["upgrade_status"]])


def _undo_delete_tag(c, d: dict):
    """還原「刪除標籤」：重建標籤（同一個編號）、資料卡、別名、每張圖上的紀錄。"""
    t = d["tag"]
    other = c.execute("SELECT name FROM tags WHERE id=?", (t["id"],)).fetchone()
    if other and other[0] != t["name"]:
        raise ValueError("原本的標籤編號已經被別的標籤使用")
    if c.execute("SELECT 1 FROM tags WHERE name=? AND id!=?", (t["name"], t["id"])).fetchone():
        raise ValueError(f"已經有新的「{t['name']}」標籤了")
    cols = list(t.keys())
    c.execute(f"INSERT OR REPLACE INTO tags({','.join(cols)}) VALUES({','.join('?' * len(cols))})", [t[k] for k in cols])
    for a in d.get("aliases", []):
        c.execute("INSERT OR REPLACE INTO tag_alias(name, tag_id) VALUES(?,?)", (a, t["id"]))
    if d.get("info"):
        ic = list(d["info"].keys())
        c.execute(f"INSERT OR REPLACE INTO tag_info({','.join(ic)}) VALUES({','.join('?' * len(ic))})", [d["info"][k] for k in ic])
    restore_tag_rows(d["rows"])
    from .matcher import matcher
    (matcher.blocked.add if t.get("auto_block") else matcher.blocked.discard)(t["name"])
    _tag_cache.clear()


def _undo_merge(c, m: dict):
    """還原「合併標籤」：重建來源 tag、還原兩邊的 media_tags、刪掉 alias。"""
    other = c.execute("SELECT name FROM tags WHERE id=?", (m["src_id"],)).fetchone()
    if other and other[0] != m["src_name"]:
        raise ValueError("原本的標籤編號已經被別的標籤使用")
    c.execute("INSERT OR IGNORE INTO tags(id, name, category, pinned, auto_block, cover) VALUES(?,?,?,?,?,?)",
              (m["src_id"], m["src_name"], m["src_cat"], m.get("pinned", 0), m.get("auto_block", 0), m.get("cover")))
    c.execute("DELETE FROM tag_alias WHERE name=?", (m["src_name"],))
    for a in m.get("aliases", []):
        c.execute("INSERT OR REPLACE INTO tag_alias(name, tag_id) VALUES(?,?)", (a, m["src_id"]))
    restore_tag_rows(m["src_rows"])
    restore_tag_rows(m["dst_rows"])
    if m.get("dst_new"):
        c.execute("DELETE FROM tags WHERE id=? AND NOT EXISTS (SELECT 1 FROM media_tags WHERE tag_id=?)",
                  (m["dst_new"], m["dst_new"]))
    if m.get("info"):
        cols = list(m["info"].keys())
        c.execute(f"INSERT OR REPLACE INTO tag_info({','.join(cols)}) VALUES({','.join('?' * len(cols))})",
                  [m["info"][k] for k in cols])
    _tag_cache.clear()


# ---------- 備份 ----------
BACKUP_DIR = DATA_DIR / "backups"


def backup(force: bool = False) -> Path | None:
    """每天一份資料庫快照（SQLite 線上備份，不用停機），保留最近 backup_keep 份。"""
    import datetime
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    name = f"library-{datetime.datetime.now():%Y%m%d}.db"
    dst = BACKUP_DIR / name
    if dst.exists() and not force:
        return None
    if force and dst.exists():
        name = f"library-{datetime.datetime.now():%Y%m%d-%H%M%S}.db"
        dst = BACKUP_DIR / name
    tmp = dst.with_suffix(".part")
    src = sqlite3.connect(DB_PATH, timeout=30)
    out = sqlite3.connect(tmp)
    try:
        src.backup(out)
    finally:
        out.close()
        src.close()
    tmp.replace(dst)
    try:
        keep = max(1, int(get_settings().get("backup_keep", 7)))
    except (TypeError, ValueError):
        keep = 7
    _mirror(dst, keep)
    # 每日備份和手動／還原前的備份分開算，手動的不會把每日的歷史擠掉
    daily = sorted(p for p in BACKUP_DIR.glob("library-*.db") if re.fullmatch(r"library-\d{8}\.db", p.name))
    extra = sorted(p for p in BACKUP_DIR.glob("library-*.db") if p not in daily)
    for p in daily[:-keep] + extra[:-5]:
        if p == dst:
            continue
        try:
            p.unlink()
        except OSError:
            pass
    return dst


def _mirror(src: Path, keep: int):
    """備份另存一份到 backup_mirror_dir（跟資料庫不同顆硬碟才有意義）。失敗不影響本機備份，但會記在「程式出錯」。"""
    d = (get_settings().get("backup_mirror_dir") or "").strip().strip('"')
    if not d:
        return
    import shutil
    try:
        mdir = Path(d) / "pickle-jar-backups"
        mdir.mkdir(parents=True, exist_ok=True)
        tmp = mdir / (src.name + ".part")
        shutil.copyfile(src, tmp)
        tmp.replace(mdir / src.name)
        olds = sorted(p for p in mdir.glob("library-*.db") if re.fullmatch(r"library-\d{8}\.db", p.name))
        for p in olds[:-keep]:
            p.unlink(missing_ok=True)
    except OSError as e:
        from . import errlog
        errlog.record_bg("備份另存到 " + d, e)


def backup_offsite() -> bool:
    """有沒有一份備份不在資料庫那顆硬碟上（收件匣會提醒）：備份另存的資料夾在別顆硬碟，或 30 天內備份過外接硬碟。"""
    from . import drivebackup
    if drivebackup.recent_ok(30):
        return True
    d = (get_settings().get("backup_mirror_dir") or "").strip().strip('"')
    if not d or not os.path.isdir(d):
        return False
    try:
        return os.stat(d).st_dev != os.stat(DATA_DIR).st_dev
    except OSError:
        return False


def apply_pending_restore() -> bool:
    """啟動時（還沒開資料庫前）套用排定的備份還原。"""
    pending = DATA_DIR / "restore-pending.db"
    if not pending.exists():
        return False
    for ext in ("-wal", "-shm"):
        Path(str(DB_PATH) + ext).unlink(missing_ok=True)
    pending.replace(DB_PATH)
    return True


def list_backups() -> list[dict]:
    if not BACKUP_DIR.exists():
        return []
    return [{"name": p.name, "size": p.stat().st_size, "mtime": p.stat().st_mtime}
            for p in sorted(BACKUP_DIR.glob("library-*.db"), reverse=True)]
