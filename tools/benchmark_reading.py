# ruff: noqa: E402
# 同一张实际 Android 截图比较几何分框与 Magi；仅两页有人工文字基准。
import os
import sys
import json
import re
import time
import hashlib
from pathlib import Path
from difflib import SequenceMatcher

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ["HF_HOME"] = str(ROOT / "~temp/huggingface")
os.environ["HF_HUB_OFFLINE"] = "1"
import editdistance
from server.ocr import MangaOCR


def normalized(text):
    return re.sub(r"[^一-龥0-9A-Za-z]", "", text)


def benchmark(inputs):
    expected = json.loads(
        (ROOT / "tests/fixtures/reading_expected.json").read_text(encoding="utf-8")
    )
    ocr = MangaOCR()
    original = ocr.layout.panels
    results = {}
    for name, path in inputs.items():
        body = Path(path).read_bytes()
        gold = [normalized(t) for t in expected[name]]
        results[name] = {
            "input_sha256": hashlib.sha256(body).hexdigest(),
            "reference_units": len(gold),
            "modes": {},
        }
        for mode in ["kumiko", "magi"]:
            ocr.layout.panels = (lambda frame: []) if mode == "kumiko" else original
            t = time.perf_counter()
            rows = ocr.read(body)
            elapsed = time.perf_counter() - t
            texts = [normalized(r["text"]) for r in rows]
            gt = "".join(gold)
            actual = "".join(texts)
            mapped = []
            for text in texts:
                match = max(
                    range(len(gold)),
                    key=lambda i: SequenceMatcher(None, text, gold[i]).ratio(),
                )
                if SequenceMatcher(None, text, gold[match]).ratio() >= 0.6:
                    mapped.append(match)
            results[name]["modes"][mode] = {
                "sentences": len(rows),
                "seconds": round(elapsed, 3),
                "normalized_edit_distance": editdistance.eval(gt, actual),
                "reference_characters": len(gt),
                "normalized_cer": editdistance.eval(gt, actual) / len(gt),
                "matched_units": len(set(mapped)),
                "order_inversions": sum(
                    a > b for i, a in enumerate(mapped) for b in mapped[i + 1 :]
                ),
            }
    results["scope"] = (
        "0012 和 0009 的简体汉字、数字与拉丁字母；忽略标点。0041 未逐字人工标注，不能推算其 CER。"
    )
    (ROOT / "~outputs-intermediate/evidence/reading-results.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(results, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit("用法：benchmark_reading.py <0012 安卓截图> <0009 安卓截图>")
    benchmark(dict(zip(["0012", "0009"], sys.argv[1:])))
