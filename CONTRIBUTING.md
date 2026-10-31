# 參與開發

謝謝你願意幫忙。Pickle Jar 是個人維護的專案，以下說明怎麼回報問題、怎麼送修改。

## 回報問題、提建議

- 用 [Issues](../../issues) 的範本：「回報問題」或「功能建議」。中文、英文都可以。
- 附上版本（系統 › 系統狀態 最上面）、作業系統、執行方式（DirectML／CUDA／CPU），和 系統 › 錯誤紀錄 的內容。
- **請先拿掉個人資訊**：檔案路徑裡的使用者名稱、帳號、API key。截圖避開不適合公開的圖（按 H 可以把 NSFW 切成隱藏）。
- 安全性問題請不要開公開 issue，見 [SECURITY.md](SECURITY.md)。

## 送修改（Pull Request）

小修正（錯字、明顯的 bug）直接送 PR；比較大的改動（新功能、改介面、改資料表）請先開 issue 討論，避免做了白工。

### 環境

```bash
git clone https://github.com/Esheep57/pickle-jar.git
cd pickle-jar
python -m venv .venv && . .venv/bin/activate      # Windows：.venv\Scripts\activate
pip install -r requirements.txt -r requirements-dev.txt
playwright install chromium                        # 畫面測試要用
python run.py                                      # http://127.0.0.1:7860
```

### 測試

```bash
python -m pytest tests -q -x
```

- 全部約 270 項、8–10 分鐘；送 PR 前請全部跑過。GitHub Actions 也會在每次 push 跑一次。
- 畫面測試（Playwright）是必跑的，沒裝 Chromium 會失敗。
- 測試共用一個圖庫，彼此會互相影響；寫新測試前先看 [開發文件 › 測試](docs/DEVELOPMENT.md#9-測試) 的注意事項和防呆測試。
- 新功能請附測試；連網的部分用 `tests/mock_online.py` 的假網站，不要連真的網站。

### 寫法

- 程式碼註解、文件、介面文字用**繁體中文**，用台灣的說法（簡體模式另外有轉換表，不要直接寫簡體）。
- 介面依照 [設計系統](DESIGN.md)：顏色、字型、元件都有規定，`tests/test_css.py` 會檢查樣式表。
- 寫資料庫一律包在 `db.write_lock` 裡；改資料表要加一個遷移（`app/db.py` 的 `MIGRATIONS`，加在最後面）。
- 會改使用者資料的動作要能撤銷（`db.push_action`）。
- 前端是不經打包的 ES module：新增的 import 要在 `scripts/jscheck.js` 檢查得過（`cd scripts && npm install && node jscheck.js`）。

### Commit 和 PR

- commit 訊息寫**做了什麼**，一行摘要、需要時再補說明，例如：`作品頁：沒有角色的作品張數照 series: 的規則算`。
- 一個 PR 做一件事；有改到使用者看得到的行為，順便更新 [變更紀錄](docs/CHANGELOG.md) 和 [使用手冊](docs/USER_GUIDE.md)。

## 授權

送出的修改會以 [GPL-3.0](LICENSE) 授權釋出。
