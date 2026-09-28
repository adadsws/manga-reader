# 先设置离线缓存与项目导入路径，再导入模型库。
# ruff: noqa: E402
import sys
import json
import time
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ["HF_HOME"] = str(ROOT / "~temp/huggingface")
os.environ["HF_HUB_OFFLINE"] = "1"
from server.ocr import MangaOCR

ocr = MangaOCR()
for name in ("0012", "0009", "0041"):
    t = time.perf_counter()
    rows = ocr.read((ROOT / ("secrets/manga/" + name + ".jpg")).read_bytes())
    out = ROOT / "~outputs-intermediate/ocr"
    out.mkdir(parents=True, exist_ok=True)
    (out / (name + ".json")).write_text(
        json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(name, len(rows), round(time.perf_counter() - t, 2), flush=True)
    if name != "0041":
        print(json.dumps([r["text"] for r in rows], ensure_ascii=False), flush=True)
