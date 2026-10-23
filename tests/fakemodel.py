"""測試用的假 WD tagger：結構跟真的一樣（輸入 NHWC、Gemm 分類頭、sigmoid），但很小、權重隨機。"""
import csv
import os

import numpy as np


def make(models_dir: str, repo: str = "SmilingWolf/wd-eva02-large-tagger-v3"):
    import onnx
    from onnx import TensorProto, helper, numpy_helper
    rng = np.random.default_rng(0)
    d = os.path.join(models_dir, repo.replace("/", "__"))
    os.makedirs(d, exist_ok=True)
    names = ["general", "sensitive", "questionable", "explicit"] + [f"gtag_{i}" for i in range(60)] + \
        ["twintails", "chibi", "cosplay"] + ["akiyama_mizuki", "hatsune_miku", "gladiia_(arknights)", "ceres_fauna", "izmir"]
    cats = [9] * 4 + [0] * 63 + [4] * 5
    with open(os.path.join(d, "selected_tags.csv"), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["tag_id", "name", "category", "count"])
        for i, (n, c) in enumerate(zip(names, cats)):
            w.writerow([i, n, c, 1000 - i])
    T = len(names)
    Wc = (rng.standard_normal((64, 3, 32, 32)) * 0.002).astype(np.float32)
    Wg = (rng.standard_normal((64, T)) * 0.5).astype(np.float32)
    b = np.zeros(T, np.float32)
    b[:4] = [0.5, 0, -0.5, -1]
    b[64:67] = -8  # 媒材 tag 平常不要觸發
    nodes = [helper.make_node("Transpose", ["input"], ["x0"], perm=[0, 3, 1, 2]),
             helper.make_node("Conv", ["x0", "Wc"], ["x1"], strides=[32, 32]),
             helper.make_node("Relu", ["x1"], ["x2"]),
             helper.make_node("GlobalAveragePool", ["x2"], ["x3"]),
             helper.make_node("Flatten", ["x3"], ["feat"]),
             helper.make_node("Gemm", ["feat", "Wg", "b"], ["logits"]),
             helper.make_node("Sigmoid", ["logits"], ["output"])]
    g = helper.make_graph(nodes, "fake", [helper.make_tensor_value_info("input", TensorProto.FLOAT, ["batch", 448, 448, 3])],
                          [helper.make_tensor_value_info("output", TensorProto.FLOAT, ["batch", T])],
                          [numpy_helper.from_array(Wc, "Wc"), numpy_helper.from_array(Wg, "Wg"), numpy_helper.from_array(b, "b")])
    m = helper.make_model(g, opset_imports=[helper.make_opsetid("", 17)])
    m.ir_version = 9
    onnx.save(m, os.path.join(d, "model.onnx"))
    return d


if __name__ == "__main__":
    import sys
    print(make(sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(__file__), "..", "models")))


def make_pixai(models_dir: str):
    """測試用的假 PixAI：輸入 NCHW 1008、輸出 prediction（已經 sigmoid）。只靠 bias：每張圖都說是 hatsune_miku（0.95）、
    畫風 pix_painter_(tst)（0.9）、作品 vocaloid（0.85）、kagamine_rin 0.5（門檻以下）。"""
    import onnx
    from onnx import TensorProto, helper, numpy_helper
    d = os.path.join(models_dir, "Mexes__pixai-tagger-v1.0-onnx-fp32-fp16-int8")
    os.makedirs(d, exist_ok=True)
    names = ["1girl", "solo", "pix_painter_(tst)", "other_painter", "vocaloid", "hatsune_miku", "kagamine_rin", "general", "explicit"]
    cats = [0, 0, 1, 1, 3, 4, 4, 9, 9]
    with open(os.path.join(d, "selected_tags.csv"), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["tag_id", "name", "category", "count"])
        for i, (n, c) in enumerate(zip(names, cats)):
            w.writerow([i, n, c, 0])
    T = len(names)
    p = np.array([0.9, 0.9, 0.9, 0.05, 0.85, 0.95, 0.5, 0.6, 0.1], np.float32)
    b = np.log(p / (1 - p)).astype(np.float32)
    Wg = np.zeros((3, T), np.float32)
    nodes = [helper.make_node("GlobalAveragePool", ["input"], ["x1"]),
             helper.make_node("Flatten", ["x1"], ["feat"]),
             helper.make_node("Gemm", ["feat", "Wg", "b"], ["logits"]),
             helper.make_node("Sigmoid", ["logits"], ["prediction"])]
    g = helper.make_graph(nodes, "fakepix", [helper.make_tensor_value_info("input", TensorProto.FLOAT, ["batch", 3, 1008, 1008])],
                          [helper.make_tensor_value_info("prediction", TensorProto.FLOAT, ["batch", T])],
                          [numpy_helper.from_array(Wg, "Wg"), numpy_helper.from_array(b, "b")])
    m = helper.make_model(g, opset_imports=[helper.make_opsetid("", 17)])
    m.ir_version = 9
    for fn in ("model_int8.onnx", "model_fp16.onnx"):
        onnx.save(m, os.path.join(d, fn))
    return d
