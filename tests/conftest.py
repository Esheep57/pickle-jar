"""測試環境：暫存資料夾、假模型、合成圖片。不需要網路、不需要 GPU。

執行：python -m pytest -q
"""
import os
import random
import shutil
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
TMP = Path(tempfile.mkdtemp(prefix="libtest-"))
os.environ["LIB_DATA_DIR"] = str(TMP / "data")
os.environ["LIB_MODELS_DIR"] = str(TMP / "models")
os.environ["LIB_CHECK_LOCK"] = "1"
os.environ["LIB_ZH_CURATED"] = str(TMP / "zh_curated.json")   # 測試不用真的校對表（測試裡的 tag 是真名，會被表蓋掉）
os.environ["LIB_MEDIA_CLIP"] = "0"          # 媒材 CLIP 模型不下載（tests/test_library21_clip.py 用假特徵）
MOCK_PORT = 7931
os.environ["LIB_DANBOORU"] = f"http://127.0.0.1:{MOCK_PORT}"   # 假的 Danbooru / IQDB / Pixiv（tests/mock_online.py）
os.environ["LIB_IQDB"] = f"http://127.0.0.1:{MOCK_PORT}/iqdb"
os.environ["LIB_PIXIV"] = f"http://127.0.0.1:{MOCK_PORT}"
os.environ["LIB_PXIMG"] = f"http://127.0.0.1:{MOCK_PORT}/pximg"
os.environ["LIB_XIMG"] = f"http://127.0.0.1:{MOCK_PORT}/ximg"
os.environ["LIB_WIKI"] = f"http://127.0.0.1:{MOCK_PORT}/wiki"
os.environ["LIB_R34"] = f"http://127.0.0.1:{MOCK_PORT}/r34"
os.environ["LIB_E621"] = f"http://127.0.0.1:{MOCK_PORT}/e621"
os.environ["LIB_EHENTAI"] = f"http://127.0.0.1:{MOCK_PORT}/eh"
os.environ["LIB_EHENTAI_UPLD"] = f"http://127.0.0.1:{MOCK_PORT}/ehupld"
os.environ["LIB_EHENTAI_API"] = f"http://127.0.0.1:{MOCK_PORT}/ehapi"
os.environ["LIB_NHENTAI"] = f"http://127.0.0.1:{MOCK_PORT}/nh"
os.environ["LIB_NHENTAI_IMG"] = f"http://127.0.0.1:{MOCK_PORT}/nhimg"
os.environ["LIB_FXTWITTER"] = f"http://127.0.0.1:{MOCK_PORT}/fx"
os.environ["LIB_TRACEMOE"] = f"http://127.0.0.1:{MOCK_PORT}/tracemoe"
os.environ["LIB_BANGUMI"] = f"http://127.0.0.1:{MOCK_PORT}/bgm"
os.environ["LIB_WIKIDATA"] = f"http://127.0.0.1:{MOCK_PORT}/wd"

import pytest  # noqa: E402
from PIL import Image, ImageDraw  # noqa: E402

from tests import fakemodel  # noqa: E402

fakemodel.make(os.environ["LIB_MODELS_DIR"])
fakemodel.make_pixai(os.environ["LIB_MODELS_DIR"])
LIB = TMP / "lib"


def make_image(path: Path, seed: int, size=(400, 560), color=None):
    rnd = random.Random(seed)
    img = Image.new("RGB", size, color or tuple(rnd.randrange(256) for _ in range(3)))
    d = ImageDraw.Draw(img)
    for _ in range(14):
        x, y = rnd.randrange(size[0]), rnd.randrange(size[1])
        r = rnd.randrange(10, 90)
        d.ellipse((x - r, y - r, x + r, y + r), fill=tuple(rnd.randrange(256) for _ in range(3)))
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path, quality=92)
    return path


def build_library():
    if LIB.exists():
        shutil.rmtree(LIB)
    # 這張當作「從 Danbooru 下載的原檔」：MD5 會被假 Danbooru 認得
    import hashlib
    p = make_image(LIB / "danbooru_original.jpg", 500)
    os.environ["MOCK_MD5"] = hashlib.md5(p.read_bytes()).hexdigest()
    os.environ["MOCK_ORIG_PATH"] = str(p)
    make_image(LIB / "98765432_p0.jpg", 501)   # 檔名是 Pixiv 作品 ID
    for i in range(12):
        make_image(LIB / f"img_{i:02d}.jpg", i, size=(400, 560) if i % 3 else (640, 360))
    for i in range(3):   # 連號的一組
        make_image(LIB / "set" / f"DSC_{100 + i:04d}.jpg", 100 + i)
    make_image(LIB / "live" / "IMG_0001.jpg", 200)
    (LIB / "live" / "IMG_0001.MOV").write_bytes(b"\x00" * 2048)   # 原況照片的動態部分：要被略過
    Image.open(LIB / "img_00.jpg").resize((320, 180)).save(LIB / "copy_small.png")  # 重複（等比例縮小：真正的複本長寬比不變）


def start_mock():
    import threading
    import uvicorn
    from tests import mock_online
    srv = uvicorn.Server(uvicorn.Config(mock_online.app, host="127.0.0.1", port=MOCK_PORT, log_level="warning"))
    threading.Thread(target=srv.run, daemon=True).start()
    for _ in range(50):
        if srv.started:
            break
        time.sleep(0.1)


@pytest.fixture(scope="session")
def client():
    build_library()
    start_mock()
    from fastapi.testclient import TestClient
    from app.server import app
    with TestClient(app) as c:
        c.post("/api/settings", json={"roots": [str(LIB)], "online_enabled": False, "setup_done": True, "scan_interval_h": 0})
        wait_idle(c)
        yield c


def wait_idle(c, timeout=60):
    t0 = time.time()
    time.sleep(0.3)
    while time.time() - t0 < timeout:
        s = c.get("/api/status").json()
        if not s["running"] and not s["pending"] and not s["queued"]:
            time.sleep(0.2)
            s = c.get("/api/status").json()
            if not s["running"] and not s["queued"]:
                return s
        time.sleep(0.2)
    raise TimeoutError("背景工作沒有結束")


def ids_by_name(c):
    from app import db
    return {os.path.basename(r["path"]): r["id"] for r in db.conn().execute("SELECT id, path FROM media")}


# 背景執行緒（ONNX 推論、OpenCV）在直譯器關閉時還在跑，C++ 會 abort（exit 134，"terminate called without an active exception"）：
# 測試明明全過，結束碼卻是失敗。測試全部結束、結果印完之後直接用 pytest 的結果結束程式。
def pytest_sessionfinish(session, exitstatus):
    session.config._lib_exit = int(exitstatus)


@pytest.hookimpl(trylast=True)
def pytest_unconfigure(config):
    code = getattr(config, "_lib_exit", None)
    if code is not None:
        from app import charknn
        charknn._stopping = True
        sys.stdout.flush()
        sys.stderr.flush()
        os._exit(code)
