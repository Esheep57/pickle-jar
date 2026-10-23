"""AI 生成的圖：讀 PNG／WebP 裡生成工具留下的資料（ComfyUI 的 workflow、Stable Diffusion WebUI 的 parameters、
NovelAI 的 Software 欄位…）。這種圖沒有「畫師」可以找，不該一直卡在「缺創作者」。

使用者圖庫：選了「原創」但沒有創作者的 65 張裡，大部分是 Downloads 裡的 ComfyUI_xxxxx.png。
只讀檔頭（PIL 開檔不解碼），整個圖庫一次約幾秒；結果存在 media.ai_gen（'' ＝查過、不是）。
"""
from __future__ import annotations

import json
import logging
import os
import re

from . import db

log = logging.getLogger(__name__)
NAME_RE = re.compile(r"^(ComfyUI|NovelAI|NAI|SD|sd)[ _-]?\d{3,}|^\d{5}-\d{6,}", re.I)   # ComfyUI_00830_.png、00012-123456789.png（WebUI）


def _read_info(path: str) -> dict:
    ext = os.path.splitext(path)[1].lower()
    if ext not in (".png", ".webp", ".jpg", ".jpeg"):
        return {}
    try:
        from PIL import Image
        with Image.open(path) as im:
            info = dict(im.info or {})
            if ext in (".jpg", ".jpeg", ".webp"):
                try:
                    uc = im.getexif().get_ifd(0x8769).get(0x9286)   # EXIF UserComment（WebUI 存 JPEG 時放這裡）
                    if isinstance(uc, bytes):
                        if uc[:8] == b"UNICODE\x00":
                            body = uc[8:]
                            uc = body.decode("utf-16-le" if body[:1] != b"\x00" else "utf-16-be", "ignore")
                        else:
                            uc = uc[8:].decode("utf-8", "ignore") if uc[:5] in (b"ASCII", b"\x00\x00\x00\x00\x00") else uc.decode("utf-8", "ignore")
                    if uc:
                        info["parameters"] = str(uc)
                except Exception:  # noqa
                    pass
            return info
    except Exception:  # noqa
        return {}


def _tool(keys: dict, base: str) -> str:
    if "workflow" in keys or '"class_type"' in str(keys.get("prompt", ""))[:20000]:
        return "ComfyUI"
    sw = str(keys.get("software", "")) + str(keys.get("source", ""))
    if "novelai" in sw.lower():
        return "NovelAI"
    if "invokeai_metadata" in keys or "sd-metadata" in keys or "dream" in keys:
        return "InvokeAI"
    par = str(keys.get("parameters", ""))
    if "Steps:" in par and ("Sampler" in par or "CFG" in par):
        return "Stable Diffusion"
    if NAME_RE.match(base) and base.lower().startswith("comfyui"):
        return "ComfyUI"
    return ""


def detect(path: str) -> str:
    keys = {str(k).lower(): v for k, v in _read_info(path).items()}
    return _tool(keys, os.path.basename(path))


LORA_RE = re.compile(r"<lora:([^:>]+)(?::[^>]*)?>", re.I)


def _a1111(par: str) -> dict:
    """Stable Diffusion WebUI：正向提示詞 \n Negative prompt: … \n Steps: 20, Sampler: …, Model: xxx"""
    lines = par.strip().split("\n")
    pos, neg, rest = [], [], ""
    mode = "pos"
    for ln in lines:
        if ln.startswith("Negative prompt:"):
            mode = "neg"
            neg.append(ln[len("Negative prompt:"):].strip())
        elif re.match(r"^Steps: \d+", ln):
            rest = ln
            mode = "rest"
        elif mode == "pos":
            pos.append(ln)
        elif mode == "neg":
            neg.append(ln)
    params = dict(re.findall(r"([A-Za-z][\w ]*?): ((?:\"[^\"]*\")|[^,]+)", rest))
    prompt = "\n".join(pos)
    return {"prompt": prompt, "negative": "\n".join(neg), "model": params.get("Model", "").strip(),
            "seed": params.get("Seed", "").strip(), "loras": sorted(set(LORA_RE.findall(prompt))),
            "params": {k.strip(): v.strip() for k, v in params.items() if k.strip() in ("Steps", "Sampler", "CFG scale", "Size", "Model hash", "Schedule type")}}


def _comfy(prompt_json: str) -> dict:
    """ComfyUI 的 prompt（節點圖）：從 KSampler 的 positive／negative 找回 CLIPTextEncode 的文字；模型、LoRA、種子。"""
    try:
        g = json.loads(prompt_json)
    except Exception:  # noqa
        return {}
    if not isinstance(g, dict):
        return {}

    def text_of(ref, depth=0):
        if depth > 8 or not isinstance(ref, list) or not ref:
            return ""
        n = g.get(str(ref[0])) or {}
        inp = n.get("inputs", {}) or {}
        for k in ("text", "text_g", "text_l", "positive", "string", "value"):
            v = inp.get(k)
            if isinstance(v, str) and v.strip():
                return v
            if isinstance(v, list):
                t = text_of(v, depth + 1)
                if t:
                    return t
        for k in ("conditioning", "conditioning_1", "conditioning_to", "clip"):
            if isinstance(inp.get(k), list) and k != "clip":
                t = text_of(inp[k], depth + 1)
                if t:
                    return t
        return ""
    pos = neg = model = seed = ""
    loras, params = [], {}
    for nid, n in g.items():
        if not isinstance(n, dict):
            continue
        ct, inp = str(n.get("class_type", "")), n.get("inputs", {}) or {}
        if "KSampler" in ct or "SamplerCustom" in ct:
            pos = pos or text_of(inp.get("positive"))
            neg = neg or text_of(inp.get("negative"))
            for k in ("seed", "noise_seed"):
                if isinstance(inp.get(k), (int, str)) and not seed:
                    seed = str(inp[k])
            for k in ("steps", "cfg", "sampler_name", "scheduler"):
                if isinstance(inp.get(k), (int, float, str)):
                    params[k] = inp[k]
        if "CheckpointLoader" in ct or ct in ("UNETLoader", "UnetLoaderGGUF"):
            model = model or str(inp.get("ckpt_name") or inp.get("unet_name") or "")
        if "Lora" in ct:
            for k, v in inp.items():
                if k.startswith("lora_name") and isinstance(v, str) and v.lower() != "none":
                    loras.append(v)
    if not pos:   # 找不到接線：拿最長的文字節點當提示詞
        texts = [n.get("inputs", {}).get("text") for n in g.values() if isinstance(n, dict) and isinstance(n.get("inputs", {}).get("text"), str)]
        pos = max(texts, key=len) if texts else ""
    return {"prompt": pos, "negative": neg, "model": model, "seed": seed, "loras": sorted(set(loras)), "params": params}


def read_meta(path: str) -> tuple[str, dict]:
    """（工具, 生成資料）；不是 AI 生成的回傳 ("", {})。"""
    info = _read_info(path)
    keys = {str(k).lower(): v for k, v in info.items()}
    tool = _tool(keys, os.path.basename(path))
    if not tool:
        return "", {}
    meta: dict = {}
    if tool == "ComfyUI" and keys.get("prompt"):
        meta = _comfy(str(keys["prompt"]))
    elif tool == "NovelAI":
        try:
            cm = json.loads(str(keys.get("comment", "{}")))
        except Exception:  # noqa
            cm = {}
        meta = {"prompt": str(keys.get("description") or cm.get("prompt") or ""), "negative": str(cm.get("uc", "")),
                "model": str(keys.get("source", "")), "seed": str(cm.get("seed", "")), "loras": [],
                "params": {k: cm[k] for k in ("steps", "scale", "sampler") if k in cm}}
    elif keys.get("parameters"):
        meta = _a1111(str(keys["parameters"]))
    return tool, {k: v for k, v in meta.items() if v not in ("", [], {}, None)}


# ---------- 提示詞 → 角色／作品候選 ----------
_W = re.compile(r"^\(*\s*(.*?)\s*(?::[\d.]+)?\s*\)*$")


def prompt_tokens(prompt: str) -> list[str]:
    """「(hatsune miku:1.2), blue_archive, <lora:x:0.8>, masterpiece」→ ["hatsune_miku", "blue_archive", "masterpiece"]"""
    p = LORA_RE.sub(",", prompt or "")
    p = p.replace("\\(", "\x01").replace("\\)", "\x02")      # 跳脫的括號是名字的一部分：hatsune miku \(append\)
    out = []
    for part in re.split(r"[,，、;\n|]|\bBREAK\b|\bAND\b", p):   # 中文逗號、頓號也算分隔（NovelAI 常見）
        part = re.sub(r"[\[\]{}]", "", part.strip())
        m = _W.match(part)
        t = (m.group(1) if m else part).replace("\x01", "(").replace("\x02", ")").strip()
        while t.startswith("(") and t.count("(") > t.count(")"):
            t = t[1:].strip()
        while t.endswith(")") and t.count(")") > t.count("("):
            t = t[:-1].strip()
        t = re.sub(r"^(artist:|by )", "", t, flags=re.I)
        if 2 <= len(t) <= 80:
            out.append(re.sub(r"\s+", "_", t.lower()))
    return list(dict.fromkeys(out))


def suggest_from_prompt(mid: int, prompt: str) -> int:
    """提示詞裡有你圖庫認得的角色／作品 → 照設定 act_aimeta（預設直接確認，b97.1；以前放待確認）。
    跟 Danbooru 角色清單衝突、跟你否決過的圖太像的照樣不加（candidates.add 的檢查）。"""
    from . import candidates
    c = db.conn()
    names = prompt_tokens(prompt)
    if not names:
        return 0
    # 「Wakaba Mutsumi(Band Dream It's MYGO)」這種自己加的作品括號：也試試去掉括號的名字
    names += [b for b in (re.sub(r"_*\(.*$", "", t) for t in names) if b and "_" in b and b not in names]
    ph = ",".join("?" * len(names))
    hits = [(r[0], r[1]) for r in c.execute(f"SELECT name, category FROM tags WHERE name IN ({ph}) AND category IN ('character','copyright')", names)]
    got = {h[0] for h in hits}
    from . import nameindex
    for t in names:          # 別名、其他寫法（只看有兩個字以上的，避免 solo、maid 這種一般字對到角色）
        if "_" not in t or t in got:
            continue
        for tid in nameindex.exact(t):
            r = c.execute("SELECT name, category FROM tags WHERE id=? AND category IN ('character','copyright')", (tid,)).fetchone()
            if r and r[0] not in got:
                hits.append((r[0], r[1]))
                got.add(r[0])
    n = 0
    with db.write_lock:
        for name, cat in hits:
            if c.execute("SELECT 1 FROM media_tags x JOIN tags t ON t.id=x.tag_id WHERE x.media_id=? AND t.name=? AND x.state<>0", (mid, name)).fetchone():
                continue      # 已經確認或否決過
            n += bool(candidates.add(mid, name, cat, candidates.T_PIXIV, "aimeta", score=None, reason="生成提示詞裡有這個名字"))
        c.commit()
    return n


def refresh(mids: list[int] | None = None, limit: int = 20000) -> int:
    """還沒檢查過的圖（或指定的圖）讀檔頭：是不是 AI 生成、生成資料（提示詞、模型、LoRA）、提示詞裡的角色 → 待確認。"""
    c = db.conn()
    if mids is not None:
        if not mids:
            return 0
        rows = c.execute(f"SELECT id, path FROM media WHERE kind='image' AND id IN ({','.join('?' * len(mids))})", mids).fetchall()
    else:   # 還沒檢查過的、以前只判斷了工具但還沒讀生成資料的
        rows = c.execute("""SELECT id, path FROM media WHERE kind='image' AND status='done' AND (ai_gen IS NULL
            OR (ai_gen<>'' AND id NOT IN (SELECT media_id FROM ai_meta))) LIMIT ?""", (limit,)).fetchall()
    found = 0
    for k in range(0, len(rows), 200):
        batch = []
        for mid, path in rows[k:k + 200]:
            tool, meta = read_meta(path) if os.path.isfile(path) else ("", {})
            batch.append((mid, tool, meta))
        with db.write_lock:
            c.executemany("UPDATE media SET ai_gen=? WHERE id=?", [(t, m) for m, t, _ in batch])
            c.executemany("""INSERT OR REPLACE INTO ai_meta(media_id, tool, model, loras, prompt, negative, seed, params)
                VALUES(?,?,?,?,?,?,?,?)""", [(m, t, meta.get("model"), json.dumps(meta.get("loras", []), ensure_ascii=False),
                                               meta.get("prompt"), meta.get("negative"), meta.get("seed"),
                                               json.dumps(meta.get("params", {}), ensure_ascii=False)) for m, t, meta in batch if t])
            c.commit()
        for m, t, meta in batch:
            if t:
                found += 1
                if meta.get("prompt"):
                    try:
                        suggest_from_prompt(m, meta["prompt"])
                    except Exception as e:  # noqa
                        log.info("提示詞候選失敗 %s: %s", m, e)
    return found


def meta_of(mid: int) -> dict | None:
    r = db.conn().execute("SELECT * FROM ai_meta WHERE media_id=?", (mid,)).fetchone()
    if not r:
        return None
    d = dict(r)
    d["loras"] = json.loads(d["loras"] or "[]")
    d["params"] = json.loads(d["params"] or "{}")
    return d


def resuggest_all() -> int:
    """所有已讀出的提示詞重新對一次角色（新角色建好、比對規則改進後補上）；已確認或否決過的不動。"""
    n = 0
    for mid, prompt in db.conn().execute("SELECT media_id, prompt FROM ai_meta WHERE prompt IS NOT NULL").fetchall():
        try:
            n += suggest_from_prompt(mid, prompt)
        except Exception as e:  # noqa
            log.info("提示詞候選失敗 %s: %s", mid, e)
    return n
