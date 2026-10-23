"""啟動：python run.py [--port 7860] [--no-browser]"""
import argparse
import logging
import os
import threading
import webbrowser

import uvicorn

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=7860)
    ap.add_argument("--no-browser", action="store_true")
    ap.add_argument("--wait-port", action="store_true", help="等舊的程式把連接埠放掉再啟動（重新啟動用）")
    a = ap.parse_args()
    os.environ["LIB_PORT"] = str(a.port)
    if a.wait_port:
        import socket
        import time
        for _ in range(60):
            with socket.socket() as sk:
                if sk.connect_ex(("127.0.0.1", a.port)) != 0:
                    break
            time.sleep(0.5)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s: %(message)s", datefmt="%H:%M:%S")
    # httpx 會把完整網址（含 Danbooru API key）記在 INFO 等級 → 關掉
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    from app import db
    if db.apply_pending_restore():
        print("  已還原備份。")
    db.init()
    try:
        import time as _t
        t0 = _t.time()
        if db.vacuum_if_requested():           # 遷移刪了很多資料（b96）：整理一次檔案，備份也跟著變小
            print(f"  資料庫整理完成（{_t.time() - t0:.0f} 秒）。")
    except Exception as e:  # noqa  整理失敗不影響啟動
        print(f"  資料庫整理失敗：{e}")
    s = db.get_settings()
    # 只有開啟「手機上傳」且設了 PIN 才綁區網；其他情況只給本機用
    host = "0.0.0.0" if s.get("lan_enabled") and s.get("lan_pin") else "127.0.0.1"
    os.environ["LIB_HOST"] = host
    url = f"http://127.0.0.1:{a.port}/"
    if not a.no_browser:
        threading.Timer(1.5, lambda: webbrowser.open(url)).start()
    print(f"\n  圖庫已啟動：{url}\n  關閉此視窗即可停止。\n")
    if host != "127.0.0.1":
        from app.api.lan import lan_ip
        print(f"  手機／平板：http://{lan_ip()}:{a.port}/m （PIN 在設定裡）\n")
    # 事件流（/api/events）是一直開著的連線：關程式時最多等 3 秒，不要一直等瀏覽器分頁斷線（b93）
    uvicorn.run("app.server:app", host=host, port=a.port, log_level="warning", timeout_graceful_shutdown=3)
