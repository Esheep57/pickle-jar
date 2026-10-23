"""假的 gallery-dl（tests/test_library22_gdl_implies_dataset.py 用）：參數跟真的一樣，依網址產生合成圖片和 metadata JSON。

- …/post/multi：同一篇兩張（要收進小資料夾）
- …/post/dup：一張（內容由環境變數 FAKE_GDL_DUP 指定的檔案複製：圖庫裡已經有的）
- …/profile/<人>（沒有 /post/）：整個帳號，五篇各一張；照 --range 只給前幾張
- …/post/fail：什麼都不下載，stderr 印 403、結束碼 1
- --post-range 1-N：只給前 N 篇；--download-archive：記下載過的，下次印「# 路徑」不再下載
- 其他：一張
"""
import json
import os
import random
import sys
from pathlib import Path

from PIL import Image, ImageDraw


def img(path, seed):
    rnd = random.Random(seed)
    im = Image.new("RGB", (300, 420), tuple(rnd.randrange(256) for _ in range(3)))
    d = ImageDraw.Draw(im)
    for _ in range(10):
        x, y, r = rnd.randrange(300), rnd.randrange(420), rnd.randrange(10, 80)
        d.ellipse((x - r, y - r, x + r, y + r), fill=tuple(rnd.randrange(256) for _ in range(3)))
    im.save(path)


def main(argv):
    dest, prange, url, cfg, archive = None, None, argv[-1], {}, None
    for i, a in enumerate(argv):
        if a == "-D":
            dest = Path(argv[i + 1])
        if a == "--post-range":
            prange = int(argv[i + 1].split("-")[1])
        if a == "-c":
            cfg = json.loads(Path(argv[i + 1]).read_text("utf-8"))
        if a == "--download-archive":
            archive = Path(argv[i + 1])
    assert "--write-metadata" in argv and dest
    assert "--range" not in argv, "要按篇數（--post-range）"
    if url.endswith("/post/fail"):
        print("[bluesky][warning] Skipping post fail (HttpError: '403 Forbidden' for 'https://x/post.info')", file=sys.stderr)
        return 1
    done = set(archive.read_text().split()) if archive and archive.exists() else set()
    handle = "fakeartist.bsky.social"
    seed = sum(map(ord, url))
    if "/post/" in url or "/posts/" in url:
        pid = url.rstrip("/").rsplit("/", 1)[-1]
        posts = [(pid, 2 if pid == "multi" else 1)]
    else:
        posts = [(f"p{k}", 2 if k == 0 else 1) for k in range(5)]     # 第一篇兩張
    if prange:
        posts = posts[:prange]
    n = 0
    for pid, cnt in posts:
        for k in range(cnt):
            key = f"{url.split('/')[2]}:{pid}:{k}"
            f = dest / f"bluesky_{pid}_{k + 1}.png"
            if key in done:
                print("# " + str(f), flush=True)
                continue
            if pid == "dup" and os.environ.get("FAKE_GDL_DUP"):
                f = dest / f"bluesky_{pid}_{k + 1}{Path(os.environ['FAKE_GDL_DUP']).suffix}"
                f.write_bytes(Path(os.environ["FAKE_GDL_DUP"]).read_bytes())
            else:
                img(f, seed + sum(map(ord, pid)) * 7 + k)
            meta = {"category": "bluesky", "subcategory": "post", "post_id": pid, "num": k + 1, "count": cnt,
                    "author": {"handle": handle, "displayName": "Fake Artist"}, "text": "hi", "tags": ["oc", "sketch"],
                    "post_url": f"https://bsky.app/profile/{handle}/post/{pid}", "sensitive": pid == "p1"}
            Path(str(f) + ".json").write_text(json.dumps(meta), "utf-8")
            print(str(f), flush=True)
            if archive:
                with open(archive, "a") as a:
                    a.write(key + "\n")
            n += 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
