"""產生 static/js/zhcn-dict.js（一般的 script，在 <head> 先載入）：簡體模式把介面文字換成大陸用語（台灣繁體 → 大陸簡體）。

字典來自 OpenCC（Apache License 2.0，https://github.com/BYVoid/OpenCC），跟 OpenCC 的 tw2sp 同樣兩步：
  1. 台灣用語 → 大陸用語（TWPhrasesRev、TWVariantsRev…）＋ 這裡的 OVERRIDE（OpenCC 沒有、或換得不自然的）
  2. 繁體字 → 簡體字（TSPhrases、TSCharacters）
重新產生：python scripts/build_zhcn.py（要裝 opencc-python-reimplemented）。
"""
import json
import os
from pathlib import Path

import opencc

D = Path(opencc.__file__).parent / "dictionary"
OUT = Path(__file__).resolve().parent.parent / "static" / "js" / "zhcn-dict.js"

# 台灣用語 → 大陸用語（直接寫簡體結果）。OpenCC 沒有的、或換得不像大陸用法的
OVERRIDE = {
    "預設": "默认", "預設值": "默认值", "進階": "高级", "複製": "复制", "原始碼": "源代码", "介面": "界面", "使用介面": "使用界面",
    "帳號": "账号", "帳戶": "账户", "快速鍵": "快捷键", "捷徑": "快捷方式", "回饋": "反馈", "品質": "质量", "離線": "离线",
    "檔案總管": "资源管理器", "核取方塊": "复选框", "擴充功能": "扩展程序", "拖曳": "拖动", "資料卡": "资料卡", "相片": "照片",
    "外接硬碟": "移动硬盘", "影像": "图像", "按右鍵": "右键", "存檔": "保存", "相簿": "相册", "啟用": "启用", "登出": "退出登录",
    "排程": "定时", "全螢幕": "全屏", "螢幕截圖": "截图", "設定檔": "配置文件", "圖檔": "图片文件", "壓縮檔": "压缩包",
    "個人資料": "个人资料", "資源回收筒": "回收站", "收合": "收起", "頁籤": "标签页", "影格": "帧", "權杖": "令牌",
    "選取": "选择", "取消選取": "取消选择", "已選取": "已选择", "解壓縮": "解压", "數位": "数码", "演算法": "算法",
    "雲端": "云端", "連結": "链接", "網路": "网络", "重新整理": "刷新", "伺服器": "服务器", "記憶體": "内存", "顯示卡": "显卡",
    "專案": "项目", "資料夾": "文件夹", "子資料夾": "子文件夹", "檔名": "文件名", "副檔名": "扩展名", "檔案": "文件",
    "資料庫": "数据库", "支援": "支持", "搜尋": "搜索", "滑鼠": "鼠标", "影片": "视频", "軟體": "软件", "硬體": "硬件",
    "程式": "程序", "程式碼": "代码", "批次": "批量", "佇列": "队列", "執行緒": "线程", "訊息": "消息", "位址": "地址",
    "物件": "对象", "列印": "打印", "預覽": "预览", "最佳化": "优化", "連線": "连接", "儲存": "保存", "匯入": "导入",
    "匯出": "导出", "載入": "加载", "點選": "点击", "游標": "光标", "剪貼簿": "剪贴板", "貼上": "粘贴", "硬碟": "硬盘",
    "磁碟": "磁盘", "隨身碟": "U盘", "螢幕": "屏幕", "解析度": "分辨率", "縮圖": "缩略图", "圖示": "图标", "字型": "字体",
    "視窗": "窗口", "登入": "登录", "金鑰": "密钥", "資訊": "信息", "設定": "设置", "回報": "反馈", "高畫質": "高清",
    "線上": "在线", "外掛": "插件", "瀏覽器": "浏览器", "套件": "软件包", "下拉選單": "下拉菜单", "選單": "菜单",
    "工作列": "任务栏", "檢視": "查看", "標註": "标注", "圖庫": "图库", "本機": "本地", "這台電腦": "这台电脑",
    "預估": "预计", "頻寬": "带宽", "網頁": "网页", "通訊": "通信", "傳送": "发送", "訊號": "信号", "快取": "缓存",
    "封鎖": "屏蔽", "解除封鎖": "取消屏蔽", "推播": "推送", "收件匣": "收件箱",
    "截圖": "截图", "文字": "文字", "查詢": "查询", "機率": "概率", "正規化": "规范化", "向量": "向量", "模擬": "模拟",
}


def load(name):
    out = {}
    for line in (D / name).read_text("utf-8").splitlines():
        if "\t" in line:
            k, v = line.split("\t", 1)
            out[k] = v.split(" ")[0]
    return out


def _longest(s, d, mx):
    out, i = [], 0
    while i < len(s):
        for L in range(min(mx, len(s) - i), 0, -1):
            w = s[i:i + L]
            if w in d:
                out.append(d[w])
                i += L
                break
        else:
            out.append(s[i])
            i += 1
    return "".join(out)


def convert(s, step1, step2):
    """跟 static/js/zhcn.js 一樣的轉法（測試和這裡用）。"""
    m1 = max(map(len, step1))
    m2 = max(map(len, step2))
    return _longest(_longest(s, step1, m1), step2, m2)


def _corpus():
    """程式裡所有中文字串（介面、伺服器訊息）：檢查轉換結果用。"""
    import re
    root = Path(__file__).resolve().parent.parent
    files = [*root.glob("static/js/*.js"), *root.glob("static/*.html"), *root.glob("app/**/*.py")]
    han = re.compile(r"[\u3400-\u9fff][\u3400-\u9fff\w\s，、。：；！？「」（）]*")
    out = set()
    for f in files:
        if f.name.startswith("zhcn"):
            continue
        out |= set(han.findall(f.read_text("utf-8")))
    return sorted(out)


def main():
    step1 = {}
    for f in ("TWVariantsRev.txt", "TWVariantsRevPhrases.txt", "TWPhrasesRev.txt"):
        step1.update(load(f))
    step1.update(OVERRIDE)
    step2 = {**load("TSCharacters.txt"), **load("TSPhrases.txt")}
    # 轉過的字有時會再轉一次（程式拿畫面上的字組新的字串）：台灣用語的詞如果剛好也是簡體字、
    # 又出現在轉換結果裡（「文件」→「文档」會把「文件夹」改成「文档夹」），就從步驟 1 拿掉
    t2s = opencc.OpenCC("t2s")
    corpus = _corpus()
    for _ in range(5):
        outs = "\n".join(convert(x, step1, step2) for x in corpus)
        bad = [k for k in step1 if k not in OVERRIDE and t2s.convert(k) == k and k in outs]
        if not bad:
            break
        for k in bad:
            del step1[k]
    assert all(convert(convert(x, step1, step2), step1, step2) == convert(x, step1, step2) for x in corpus), "轉兩次結果不一樣"
    # 步驟 1 的結果是繁體用語（OpenCC）或已經是簡體（OVERRIDE）；步驟 2 只換字，簡體字不在字典裡、不會被改到
    js = ("// 自動產生（scripts/build_zhcn.py），不要手改。字典來自 OpenCC（Apache License 2.0）＋ 本程式的大陸用語對照。\n"
          "window.ZHCN_DICT = {TW: " + json.dumps(dict(sorted(step1.items())), ensure_ascii=False, separators=(",", ":")) +
          ",\nTS: " + json.dumps(dict(sorted(step2.items())), ensure_ascii=False, separators=(",", ":")) + "};\n")
    OUT.write_text(js, "utf-8")
    print(OUT, len(step1), len(step2), os.path.getsize(OUT))


if __name__ == "__main__":
    main()
