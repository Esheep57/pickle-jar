"""PixAI 推論的獨立子程式：模型在 GPU 上出問題（DirectML／CUDA 直接讓程式崩潰）時，只有這個子程式死掉，Pickle Jar 照常開著。

用法（pixai.run 啟動）：python -m app.pixai_worker
- stdin 每行一個 JSON：{"mid": 123, "path": "C:\\...jpg"}；空行或 EOF 結束
- stdout 第一行 {"ready": true, "provider": "...", "model": "..."}，之後每張一行 {"mid": 123, "pred": {...}} 或 {"mid": 123, "error": "..."}
- 原生崩潰的 Python 堆疊寫到 data/pixai_crash.log（faulthandler）
"""
from __future__ import annotations

import faulthandler
import json
import os
import sys
from pathlib import Path


def main():
    from . import db
    crash = open(Path(db.DATA_DIR) / "pixai_crash.log", "a", encoding="utf-8") if hasattr(db, "DATA_DIR") else sys.stderr
    faulthandler.enable(file=crash, all_threads=True)
    from . import media, pixai
    out = sys.stdout
    try:
        px = pixai.PixAI()
    except Exception as e:  # noqa
        out.write(json.dumps({"ready": False, "error": f"{type(e).__name__}: {e}"}, ensure_ascii=False) + "\n")
        out.flush()
        return
    out.write(json.dumps({"ready": True, "provider": px.provider, "model": px.model_file}) + "\n")
    out.flush()
    for line in sys.stdin:
        line = line.strip()
        if not line:
            break
        job = json.loads(line)
        mid = job["mid"]
        if os.environ.get("LIB_PIXAI_CRASH_ON") == str(mid):     # 測試用：模擬 GPU 崩潰
            os._exit(3)
        try:
            x = pixai.PixAI.preprocess(media.load_image(Path(job["path"])))
            p = px.run(x[None])[0]
            res = {"mid": mid, "pred": px.decode(p)}
        except Exception as e:  # noqa
            res = {"mid": mid, "error": f"{type(e).__name__}: {e}"[:300]}
        out.write(json.dumps(res, ensure_ascii=False) + "\n")
        out.flush()


if __name__ == "__main__":
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    main()
