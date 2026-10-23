---
version: alpha
name: pickle-jar
description: "Pickle Jar 是個人本機圖庫整理工具。安靜的專業工具感，像 Lightroom、Figma：圖是主角，介面退到後面。淺灰外框包一塊圓角 14 的主畫面；標題用思源宋體，其餘全用思源黑體；醃黃瓜綠只代表「主要動作」和「候選」。平常用深色模式，鍵盤操作為主。"

colors:
  # 淺色（預設 token）
  frame: "#f4f4f2"
  bg: "#ffffff"
  panel: "#ffffff"
  side: "#f0f0ee"
  line: "#e4e4e0"
  hover: "#ebebe8"
  active: "#e2e2de"
  text: "#1b1c1d"
  mute: "#6b6a66"
  primary: "#3f7a3b"        # CSS: --accent
  on-accent: "#ffffff"      # CSS: --on-accent（綠底上的字）
  on-accent-dark: "#0f1a0e"
  accent-bg: "#e6f1e4"
  warn: "#9a6512"
  danger: "#b8453a"
  cell-bg: "#ececea"
  on-image: "#ffffff"
  image-scrim: "#000000"
  # 深色（@media prefers-color-scheme: dark 換掉同名 token）
  frame-dark: "#0f1011"
  bg-dark: "#161718"
  panel-dark: "#161718"
  side-dark: "#1e1f21"
  line-dark: "#2a2b2e"
  hover-dark: "#25262a"
  active-dark: "#2e2f33"
  text-dark: "#ecebe7"
  mute-dark: "#9a9994"
  primary-dark: "#8fd08a"   # CSS: --accent（深色）
  warn-dark: "#e8b36a"
  danger-dark: "#ef8a7c"
  cell-bg-dark: "#1e1f21"

typography:
  fs1:
    fontFamily: Noto Sans TC
    fontSize: 12px
    fontWeight: 400
    lineHeight: 1.55
  fs2:
    fontFamily: Noto Sans TC
    fontSize: 14px
    fontWeight: 400
    lineHeight: 1.55
  fs2-medium:
    fontFamily: Noto Sans TC
    fontSize: 14px
    fontWeight: 500
    lineHeight: 1.55
  fs3:
    fontFamily: Noto Sans TC
    fontSize: 17px
    fontWeight: 400
    lineHeight: 1.55
  title:
    fontFamily: Noto Serif TC
    fontSize: 28px
    fontWeight: 700
    lineHeight: 1.3
  dialog-title:
    fontFamily: Noto Serif TC
    fontSize: 17px
    fontWeight: 700
    lineHeight: 1.4
  display-question:
    fontFamily: Noto Serif TC
    fontSize: 40px
    fontWeight: 700
    lineHeight: 1.2
  display-inbox:
    fontFamily: Noto Serif TC
    fontSize: 34px
    fontWeight: 700
    lineHeight: 1.2
  mono-sm:
    fontFamily: Cascadia Mono
    fontSize: 12px
    fontWeight: 400
    lineHeight: 1.4
  mono-hud:
    fontFamily: Cascadia Mono
    fontSize: 28px
    fontWeight: 700
    lineHeight: 1.1

rounded:
  in: 6px
  ctl: 8px
  pop: 10px
  panel: 14px

spacing:
  s2: 2px
  s4: 4px
  s6: 6px
  s8: 8px
  s10: 10px
  s12: 12px
  s14: 14px
  s16: 16px
  page: 34px

components:
  button:
    backgroundColor: "{colors.panel}"
    textColor: "{colors.text}"
    typography: "{typography.fs2-medium}"
    rounded: "{rounded.ctl}"
    height: 32px
  button-hover:
    backgroundColor: "{colors.hover}"
    textColor: "{colors.text}"
  button-pressed:
    backgroundColor: "{colors.active}"
    textColor: "{colors.text}"
  button-primary:
    backgroundColor: "{colors.primary}"
    textColor: "{colors.on-accent}"
    typography: "{typography.fs2-medium}"
    rounded: "{rounded.ctl}"
    height: 32px
  button-primary-dark:
    backgroundColor: "{colors.primary-dark}"
    textColor: "{colors.on-accent-dark}"
    typography: "{typography.fs2-medium}"
    rounded: "{rounded.ctl}"
    height: 32px
  button-sm:
    backgroundColor: "{colors.panel}"
    textColor: "{colors.text}"
    typography: "{typography.fs1}"
    rounded: "{rounded.ctl}"
    height: 28px
  button-big:
    backgroundColor: "{colors.panel}"
    textColor: "{colors.text}"
    typography: "{typography.fs2-medium}"
    rounded: "{rounded.ctl}"
    height: 40px
  ghost:
    backgroundColor: "{colors.panel}"
    textColor: "{colors.text}"
    typography: "{typography.fs2-medium}"
    rounded: "{rounded.ctl}"
    height: 32px
  danger:
    backgroundColor: "{colors.panel}"
    textColor: "{colors.danger}"
    typography: "{typography.fs2-medium}"
  danger-dark:
    backgroundColor: "{colors.panel-dark}"
    textColor: "{colors.danger-dark}"
    typography: "{typography.fs2-medium}"
  link:
    backgroundColor: "{colors.panel}"
    textColor: "{colors.mute}"
    typography: "{typography.fs2}"
  link-dark:
    backgroundColor: "{colors.panel-dark}"
    textColor: "{colors.mute-dark}"
    typography: "{typography.fs2}"
  seg:
    backgroundColor: "{colors.side}"
    textColor: "{colors.mute}"
    typography: "{typography.fs1}"
    rounded: "{rounded.ctl}"
    height: 28px
  seg-selected:
    backgroundColor: "{colors.panel}"
    textColor: "{colors.text}"
    typography: "{typography.fs1}"
    rounded: "{rounded.in}"
  chip:
    backgroundColor: "{colors.side}"
    textColor: "{colors.text}"
    typography: "{typography.fs2}"
    rounded: "{rounded.ctl}"
  candidate:
    backgroundColor: "{colors.accent-bg}"
    textColor: "{colors.text}"
    typography: "{typography.fs2}"
  status-warn:
    backgroundColor: "{colors.panel}"
    textColor: "{colors.warn}"
    typography: "{typography.fs1}"
  status-warn-dark:
    backgroundColor: "{colors.panel-dark}"
    textColor: "{colors.warn-dark}"
    typography: "{typography.fs1}"
  kbd:
    backgroundColor: "{colors.side}"
    textColor: "{colors.mute}"
    typography: "{typography.mono-sm}"
    rounded: "{rounded.in}"
  cell:
    backgroundColor: "{colors.cell-bg}"
    rounded: "{rounded.panel}"
  cell-badge:
    backgroundColor: "{colors.image-scrim}"
    textColor: "{colors.on-image}"
    typography: "{typography.fs1}"
    rounded: "{rounded.in}"
  modal:
    backgroundColor: "{colors.panel}"
    textColor: "{colors.text}"
    typography: "{typography.fs2}"
    rounded: "{rounded.pop}"
    width: 560px
  popmenu:
    backgroundColor: "{colors.panel}"
    textColor: "{colors.text}"
    typography: "{typography.fs2}"
    rounded: "{rounded.pop}"
  actionbar:
    backgroundColor: "{colors.panel}"
    textColor: "{colors.text}"
    typography: "{typography.fs2-medium}"
    rounded: "{rounded.pop}"
  toast:
    backgroundColor: "{colors.panel}"
    textColor: "{colors.text}"
    typography: "{typography.fs2}"
    rounded: "{rounded.pop}"
    height: 40px
  hud:
    backgroundColor: "{colors.panel-dark}"
    textColor: "{colors.primary-dark}"
    typography: "{typography.mono-hud}"
    rounded: "{rounded.pop}"
  page-title:
    backgroundColor: "{colors.panel}"
    textColor: "{colors.text}"
    typography: "{typography.title}"
  page-title-dark:
    backgroundColor: "{colors.panel-dark}"
    textColor: "{colors.text-dark}"
    typography: "{typography.title}"
  surface-dark:
    backgroundColor: "{colors.bg-dark}"
    textColor: "{colors.text-dark}"
  surface-dark-side:
    backgroundColor: "{colors.side-dark}"
    textColor: "{colors.mute-dark}"
  frame-dark:
    backgroundColor: "{colors.frame-dark}"
    textColor: "{colors.mute-dark}"
  hover-dark:
    backgroundColor: "{colors.hover-dark}"
    textColor: "{colors.text-dark}"
  active-dark:
    backgroundColor: "{colors.active-dark}"
    textColor: "{colors.text-dark}"
  line-dark:
    backgroundColor: "{colors.line-dark}"
    textColor: "{colors.text-dark}"
  cell-dark:
    backgroundColor: "{colors.cell-bg-dark}"
  frame:
    backgroundColor: "{colors.frame}"
    textColor: "{colors.mute}"
  bg:
    backgroundColor: "{colors.bg}"
    textColor: "{colors.text}"
  line:
    backgroundColor: "{colors.line}"
    textColor: "{colors.text}"
---

# Pickle Jar — DESIGN.md

> 這份文件是寫給「要改介面的 AI」看的設計系統，格式照 Google Stitch DESIGN.md（開頭 YAML token 區塊＋九段說明）。
> 數值全部取自 `static/style.css` 的 token（2026-10-14，b76）。這份文件說明「長什麼樣、為什麼」；樣式表怎麼分段、怎麼加規則，看 `docs/DEVELOPMENT.md` 的「樣式表」。
> **兩邊衝突時以 `style.css` 的 token 為準**，再回來改這份。
> 開頭的 YAML 區塊是給工具讀的：改完跑 `npx @google/design.md lint DESIGN.md`，會檢查壞掉的引用、文字對比度、沒被用到的 token。CSS 的 `--xxx` 在這裡寫成 `{colors.xxx}`，唯一的例外是 `--accent` 寫成 `{colors.primary}`（工具規定一定要有 primary）；深色值用 `-dark` 結尾。

---

## 1. 視覺主題與氛圍

**安靜的專業工具。** 像 Lightroom、Figma：圖是主角，介面退到後面，只在需要時出現。不是畫廊，也不是沉浸式瀏覽。

- 使用者每天在這裡整理上萬張圖，大部分時間在按鍵盤。介面要**可預期、密度適中、不搶眼**。
- 標題用宋體（襯線），帶一點書卷氣；其他全部用黑體（無襯線）。
- 品牌意象是「醃黃瓜罐」：綠色只代表「主要動作」和「候選」，其他地方不用綠。
- 功能不刪、按鍵不改：新功能優先收進 Ctrl K、≡ 選單或詳細抽屜，不要在畫面上多加一顆按鈕。

**同類參考**（awesome-design-md 收錄的範本，只參考做法，不抄品牌）
- **Linear／Raycast**：深色表面階梯、細線邊框、強調色極少、產品畫面當主角。我們的深色 token 本來就是這一路（`--frame #0f1011` 跟 Linear 的 surface-1 是同一個值）。
- **Claude**：襯線標題配人文黑體的「編輯排版」語氣。我們的宋體標題跟它是同一個想法。
- Notion 的公開設計是全站無襯線、紫色按鈕、粉彩卡片，**不是**這裡的參考。

---

## 2. 色彩與角色

**一律用 token，不寫死顏色。** 深色模式只改 token（`@media (prefers-color-scheme: dark) :root`）；`body.tint` 是「設定 › 顯示」裡的綠色底，也只改 token。

| Token | 淺色 | 深色 | 用途 |
|---|---|---|---|
| `--frame` | `#f4f4f2` | `#0f1011` | 視窗最外層底色（側欄後面） |
| `--bg` | `#ffffff` | `#161718` | 頁面底色 |
| `--panel` | `#ffffff` | `#161718` | 主畫面 `#main`、彈出面板、視窗、toast |
| `--side` | `#f0f0ee` | `#1e1f21` | 次要表面：分段控制底、chip、輸入框底 |
| `--line` | `#e4e4e0` | `#2a2b2e` | 細線、邊框、kbd |
| `--hover` | `#ebebe8` | `#25262a` | 滑過 |
| `--active` | `#e2e2de` | `#2e2f33` | 按下、側欄選中的標籤 |
| `--text` | `#1b1c1d` | `#ecebe7` | 主要文字 |
| `--mute` | `#6b6a66` | `#9a9994` | 次要文字、數字、連結（`.lnk`） |
| `--accent` | `#3f7a3b` | `#8fd08a` | **主要動作、候選、選中** |
| `--on-accent` | `#ffffff` | `#0f1a0e` | **綠底上的字**（主按鈕、數字徽章、勾勾）。一律用這個，不寫 `#fff`：深色的綠很淺，白字只有 1.9:1 |
| `--accent-bg` | `#e6f1e4` | accent 14% | 候選／高亮的淡底 |
| `--warn` | `#9a6512` | `#e8b36a` | 進行中、需要注意（不是錯誤） |
| `--danger` | `#b8453a` | `#ef8a7c` | **只給刪除、解散、錯誤** |
| `--cell-bg` | `#ececea` | `#1e1f21` | 縮圖還沒載入時的格子底 |
| `--leaf` / `--rind` / `--seed` | 綠／深綠／淺 | | 品牌用：logo、側欄選中的左邊線、頁面標題字色 |

**深色表面階梯**（作者平常用深色）
`--frame #0f1011` → `--bg/--panel #161718` → `--side #1e1f21` → `--hover #25262a` → `--active #2e2f33`，線是 `--line #2a2b2e`。
深色模式的層次主要靠這條階梯分出來，陰影只是輔助（深底上的陰影看不太出來）。新元件要先決定站在哪一階，不要跳階，也不要自己調一個新的灰。

**顏色的規則**
- 綠（accent）只有兩個意思：「按這個往下走」和「這是候選」。資訊性的數字、連結、狀態燈都不用綠。
- 紅（danger）只給會刪東西或出錯的地方。
- 標籤類別（`.cat`）有自己的固定色：角色紫、作品（copyright）橘、創作者（artist）青綠、自訂藍，淺深各一組。這是**唯一允許寫死色碼**的元件群；新增類別照同樣做法，淺色和深色都要寫。
- 疊在圖上的東西（格子角標、選取圈）用半透明黑底白字 `rgba(0,0,0,.55–.62)`，不用 token，因為底下是圖，不是介面。

---

## 3. 字體規則

字型全部**離線內建**（`static/fonts/`，@fontsource 的 woff2 分片，OFL 授權），不載 Google Fonts。

| Token | 字族 | 用在 |
|---|---|---|
| `--font-sans` | Noto Sans TC → 微軟正黑體 → PingFang TC | 所有內文、按鈕、輸入框 |
| `--font-serif` | Noto Serif TC → 思源宋體 → 新細明體 | **只用在標題**：`#head h1`、`#content h2/h3`、視窗 h3、待確認的問句、收件匣那句 |
| `--mono` | Cascadia Mono → JetBrains Mono → Consolas | 數字鍵 HUD 的大數字、kbd 鍵帽、分頁上的張數 |

**字級只有四個**（`tests/test_ui.py` 會擋 token 以外的字級）：

| Token | 大小 | 字重 | 用在 |
|---|---|---|---|
| `--fs1` | 12px | 400／500 | 次要資訊、鍵帽、角標、sec-title、分段標籤 |
| `--fs2` | 14px | 400／500 | 內文、按鈕、輸入框（預設） |
| `--fs3` | 17px | 400 | HUD 訊息、小標、視窗標題 |
| `--fs4` | 28px | 700 | 頁面標題（宋體） |

例外只有兩個展示字級：待確認專注模式的角色問句 40px、收件匣那句 34px，都用宋體。

- 字重只用 **400／500／700**。500 給按鈕和分段標籤，700 給標題和選中狀態。
- 行高 1.55；標題 1.2–1.3。數字一律 `tabular-nums`（等寬數字，張數才對得齊）。
- 中文不加字距壓縮（`palt` 關掉），也不加負字距。英文範本常見的 display 負字距不適用中文。
- **mono 只給數字和鍵帽。** 角色名、檔名、標題都不用 mono，即使內容是英文 tag（例如 `akiyama_mizuki`）。

---

## 4. 元件樣式

### 尺寸 token
| Token | 值 | |
|---|---|---|
| `--h-sm` / `--h-md` / `--h-lg` | 28 / 32 / 40px | 控制項高度只有這三種（待確認的大鍵 48 例外） |
| `--r-in` / `--r-ctl` / `--r-pop` | 6 / 8 / 10px | 內層小元件／控制項／浮層 |
| `--panel-r` | 14px | 主畫面 `#main`、格子 |
| `--t1` / `--t2` | 80 / 140ms | 狀態變化／進場 |
| `--ease-std` | `cubic-bezier(.2,0,0,1)` | 預設緩動 |
| `--ring` | 2px 底色＋2px accent | 鍵盤焦點 |
| `--pop-shadow` | 兩層淡陰影 | 所有浮層 |

### 按鈕
- **`.btn`**（`{components.button}`）：高 `--h-md`、圓角 `--r-ctl`、1px `--line` 邊框、`--panel` 底、fs2、字重 500。滑過換 `--hover`，按下換 `--active` 並縮到 .96。
- **`.btn.primary`**（`{components.button-primary}`）：accent 底、`--on-accent` 字（淺色白字、深色深綠字）。**一個畫面最多一個。**
- **`.btn.sm`**：高 `--h-sm`、fs1。**`.btn.big`**：高 `--h-lg`，給待確認的「是／不是」。
- **`.ghost`**：沒有邊框和底色，滑過才出現 `--hover`。工具列、選單裡的動作都用這個。
- **`.btn.danger` / `.lnk.danger`**：紅字。危險動作只放在「⋯」選單裡，不常駐。
- 按鈕裡的快捷鍵用 `<kbd>`，透明度 .55–.7。

### 連結
- **`.lnk`**：`--mute` 灰字＋淡底線（offset 3px），滑過變 `--text`。**不用白字、不用綠字**（v4 改過：白字加底線會比主按鈕還搶眼）。

### 分段控制與分頁
- **`.seg`**：`--side` 底、2px 內距；選項高 `--h-sm`、fs1、字重 500、`--mute` 字。選中的換成 `--panel` 底＋細陰影＋字重 700。
- **`.tabs` / `.pagetabs`**：同一套樣子，但高 `--h-md`、fs2。分頁上的張數用 `<i>`、mono、`--mute`。

### Chip 與標籤
- **`.chip`**：`--side` 底、1px `--line`、圓角 `--r-ctl`；內容是名字＋分數（`--mute` fs1）＋小按鈕。★ ✕ 平常寬度 0，滑過才出現。
- **`.cat`**：fs1、圓角 8、類別色（見 §2）。

### 鍵帽 `kbd`
- `--side` 底、1px `--line`、圓角 `--r-in`、mono fs1、`--mute` 字。只拿來標快捷鍵，不當裝飾。
- 觸控裝置（`hover: none`）上全部藏起來。
- 顯示的是 **Windows 鍵名**：`Ctrl K`、`Del`、`⌫`、`Space`、`Esc`。不要用 `⌘`。

### 格子 `.cell`
- `--cell-bg` 底、圓角 14、`object-fit: cover`。
- **平常沒有任何角標**。選取圈、影片 ▶、NSFW、張數、角色名都是滑過或按住 Alt（透視）才出現。
- **進入選取狀態（已選 ≥1 張）時**，其他格子的選取圈只在滑過時出現；不要讓每一格同時冒出圓圈（v4 噪音清單第 7 條）。
- 選中：`::after` 內陰影畫 2px accent 框，左上角 accent 圓圈 ✓。不浮起、不放大、不彈跳。
- 待確認卡的信心：底部 2px accent 細線（`.rconf`），長度代表信心。

### 浮層
- **`.modal`**：`--panel`、圓角 `--r-pop`、`--pop-shadow`＋1px 內框線；寬 560（`.wide` 760）；背景遮罩 `rgba(20,30,20,.35)` 加 6px 模糊。標題 h3 用宋體。**一律用右上 ✕＋Esc 關閉。**
- **`.popmenu`**：同樣的表面，從觸發點下方 6px 打開。
- **`#actionbar`（命令膠囊）**：選了圖才出現，固定在底部置中。最多 3 個常用動作＋「⋯」；主動作（如果有）用 `.btn.primary`，其他用 ghost。
- **`.toast`**：底部置中、高 `--h-lg`；會改資料的動作一律附「撤銷」（accent 字）。
- **`#hud`**：數字鍵快速標籤專用，在畫面正中；左邊是 mono fs4 accent 的大數字，右邊是訊息。

### 狀態指示
- 頁首或頂欄的狀態（NSFW 隱藏中、掃描中、未檢查 N 張）用 `--mute` 或 `--warn` 字，最多加一個小點，**不用綠底膠囊**。綠色留給動作和候選。
- 系統健康狀態集中在健康燈，收件匣不放警示。

### 圖示
- 線條圖示，`core.svg(name, size)`：24 viewBox、stroke 1.7、圓角端點、`currentColor`。
- **不用 emoji 當圖示**（UX 巡檢 D1 還在清）。

---

## 5. 版面原則

```
┌────────┬──────────────────────────────────────┐
│ 側欄    │ #main（--panel，圓角 14，四周 12px 留白） │
│ 236px  │  #topbar   標題列（padding 26/34/10）    │
│ 透明底  │  #head     h1 宋體 ＋ 一行 meta ＋ 分頁    │
│ 三區：  │  #content  內容（左右 34px，底部 110px）   │
│ 整理／  │                                        │
│ 瀏覽／  │        [ 底部查詢列 ／ 命令膠囊 ]         │
│ 收藏    │                                        │
└────────┴──────────────────────────────────────┘
```

- **間距**：常用 2／4／6／8／10／12／14／16px；元件內 6–8，區塊之間 12–16，頁面左右 34。
- **一頁一條指令線**：查詢列放在底部；選了圖時讓位給命令膠囊。有查詢列的頁面就不在右上放搜尋。
- **頁首格式固定**：標題＋一行 meta，說明收進 ⓘ；頁面動作最多 2 個常用的＋「⋯」。
- **噪音預算**（`tests/test_ui.py::test_noise_budget`）：`#main` 裡看得到的控制（不含格子、卡片、側欄），上限是 home 9、all 5、unrecognized 7、review 12、characters 12。超過就收進 ≡、Ctrl K 或詳細抽屜；要拉高上限前先想清楚。
- 圖要佔畫面的大部分。待確認專注模式改版前圖只佔 18%，這是反例。

### 圖片怎麼擺
- **格子**：`object-fit: cover` 裁成方塊，是為了排得整齊。格子大小由滑桿決定，不跟著圖的比例變。
- **大圖**：`object-fit: contain`，**永遠不裁切、不放大超過原始像素**。圖上不常駐任何東西；身分卡、標籤放在旁邊的詳細抽屜，不要浮在圖上（v4 噪音清單第 2 條）。
- 漫畫、長條圖、截圖在格子裡被裁到看不出內容是正常的，不要為了它們改格子的比例。

---

## 6. 深度與層次

只有三層，用表面顏色和陰影來分，不用粗邊框：

| 層 | 表面 | 陰影 |
|---|---|---|
| 0 外框 | `--frame` | 無 |
| 1 主畫面 | `--panel`，圓角 14 | `0 1px 3px rgba(0,0,0,.04), 0 10px 40px rgba(0,0,0,.06)` |
| 2 浮層（視窗、選單、toast、命令膠囊、HUD） | `--panel`，圓角 `--r-pop` | `--pop-shadow` ＋ `inset 0 0 0 1px var(--line)` |

- 選中的分段或分頁用 `0 1px 2px rgba(0,0,0,.18)` 浮起一點點，不換顏色。
- 深色模式的陰影比較重（`--shadow`、`--pop-shadow` 各有深色版），但層次主要還是靠 §2 的表面階梯和 1px 內框線。
- 焦點環一律用 `--ring`，不要用瀏覽器預設的藍框。

---

## 7. 該做／不該做

**該做**
- 用 token：`--fs*`、`--h-*`、`--r-*`、顏色變數。
- 新樣式加在 `style.css` 對應功能的段落（看是哪個 JS 檔建立這個 class）；改既有元件就改那一條規則。
- 取新 class 名之前，先在 `static/js` 和 `style.css` 裡 grep。
- 會改資料的動作要有 toast＋撤銷；確認框用 `core.ask()`（危險按鈕紅色、Esc 取消），不用原生 `confirm()`。
- 刪除之前先確認，刪掉的檔案移到資源回收筒。
- 同一個動作在每個地方叫同一個名字、用同一個鍵（大圖、格子、待確認共用同一張鍵表）。
- 想強調的時候，先放大字級或換到宋體，最後才考慮加粗或上色。

**不該做**
- 不寫死顏色、字級、圓角（`.cat` 類別色例外）。
- 不在檔尾加一段規則蓋掉前面的（以前疊了十層 v1.3～v4.2）。
- 不加 `!important`，除非是要蓋 JS 行內樣式或 `.hidden` 這類工具 class（`IMPORTANT_MAX` 會擋數量變多）。
- 不讓格子常駐角標；不加 hover 浮起、放大、彈跳動畫。
- 不用綠色表示「資訊」或「狀態」；不用 emoji 當圖示；控制項不用 999px 膠囊形（圓角一律 `--r-ctl`，只有點和頭像是圓的，用 `50%`）。
- 不用 mono 字型顯示名字或標題。
- 不把快速標籤放到第三個地方（已經在數字鍵、大圖詳細、底部面板）。
- 不加深層的設定或巢狀選單，使用者覺得每天用很煩。
- 看大圖時，標記完不要自動跳下一張。

**快捷鍵慣例（不能改）**
- Delete＝刪除（移到回收筒，會先確認）；Backspace＝否決候選；1–9＝快速標籤；Enter＝確認候選；K＝官方；M＝媒材；Esc＝關閉／取消選取；Space＝開關大圖；Ctrl K＝全部功能。

---

## 8. 響應式行為

主要環境是 Windows 桌機瀏覽器、深色模式；手機和平板是區網的「只看」模式（`body.remote`）。

| 斷點 | 變化 |
|---|---|
| 高 ≤ 820px | 精簡側欄字變 fs1、間距縮小 |
| 寬 ≤ 800px | 側欄變抽屜（`#menu-btn` 打開），主畫面去掉外框圓角 |
| 寬 ≤ 600px | 頁面左右 12px；底部查詢列全寬 |
| `hover: none`（觸控） | 藏鍵帽提示、透視（Alt）、頁尾快捷鍵 |
| `body.remote` | 藏所有整理入口、選取、編輯、撤銷 |
| `prefers-reduced-motion` | 關掉所有動畫與轉場 |

---

## 9. 給 AI 的提示指南

**快速色彩參照（深色）**
底 `#161718`・外框 `#0f1011`・次要表面 `#1e1f21`・線 `#2a2b2e`・字 `#ecebe7`・次要字 `#9a9994`・綠 `#8fd08a`・警告 `#e8b36a`・紅 `#ef8a7c`

**改介面前**
1. 讀這份文件和 `style.css` 開頭的 token。
2. 估一下會不會超過噪音預算。
3. 看看有沒有現成元件可以用（`.btn`、`.ghost`、`.seg`、`.chip`、`.popmenu`、`core.ask`、toast）。
4. 新元件先決定站在哪一層（§6）、哪一階表面（§2），再決定其他的。

**改的方式**
- 一次只改一個元件，用 token 名字講（「改 `{components.seg-selected}`」），不要用形容詞描述。
- 新變體（`-hover`、`-pressed`、`-disabled`）在 YAML 裡另開一條，不要埋在說明文字裡。
- 改完 `style.css` 的 token，同一個提交裡把 YAML 區塊和 §2 表格一起改掉，再跑 `npx @google/design.md lint DESIGN.md`。

**可直接用的提示**
- 「照 DESIGN.md 加一個 ⋯ 選單動作：ghost 樣式，危險的用 danger 紅字，不要新增常駐按鈕。」
- 「這個新面板用浮層第 2 層：`--panel`、`--r-pop`、`--pop-shadow`＋內框線，右上 ✕，Esc 關閉。」
- 「新頁面：宋體 h1＋一行 `--mute` meta，分頁用 `.pagetabs`，查詢放底部查詢列。」
- 「檢查這次改動：有沒有寫死顏色、token 以外的字級、新的 !important、常駐角標、第二個主按鈕、綠色用在狀態上。」

---

## 已知偏差（還沒改完的地方）

這些是現況跟上面規則不一致的地方。改到相關元件時順手修，不要照著抄：

- 還有 66 處 `999px`：大多是點、進度條、頭像（本來就該是圓的），但 `.qtok`、`#facets .tabs button`、`.ubk button`、`.pk-cats button`、`.cchip`、`.srcpill`、`.skins a`、`#quick.qchips a`、幾個搜尋框和下拉選單還是膠囊形的控制項（巡檢 D4）。
- 沒有直接證據的綠字（巡檢 D5）沒有重新掃過。

**b76 修掉的**（2026-10-14）：淺色主按鈕對比（白字，5.1:1）；深色模式 14 處綠底白字（改 `--on-accent`）；`#head h1`、`#pagetitle h1` 直接寫宋體 700，拿掉兩條 `!important`；`.taglist a`、`.chip`、`.card .nm`、`.rawname`、`.clist small`、`.shero-raw` 殘留的 `ui-monospace`；`#hud button` 寫死的深色；格子角標圓角改 token；`.chip`、`.pillbtn`、`.pills span` 改 `--r-ctl`；空狀態的 emoji。

> 參考截圖 `HS53DcqbkAEdy09.jpg` 不是現在的 Pickle Jar（簡體、⌘K、側欄結構都不同；NSFW 在現在的程式裡是側欄底部的一行），不要拿它對照。
