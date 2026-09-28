# 先设置离线缓存与项目导入路径，再导入模型库。
# ruff: noqa: E402
import os
import sys
import json
import time
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
os.environ["HF_HOME"] = str(ROOT / "~temp/huggingface")
os.environ["HF_HUB_OFFLINE"] = "1"
sys.path.insert(0, str(ROOT))
import torch
from transformers.utils import logging

logging.set_verbosity_error()
from torchvision.ops import box_convert, nms
from transformers import AutoConfig, AutoModel
from PIL import Image
import numpy as np

run = ROOT / "~temp/magiv2"
run.mkdir(parents=True, exist_ok=True)
for src in (ROOT / "reference/layout/magiv2").glob("*"):
    if src.suffix in (".py", ".json"):
        shutil.copy2(src, run / src.name)
w = run / "pytorch_model.bin"
if not w.exists():
    os.link(ROOT / "models/layout/magiv2/pytorch_model.bin", w)
cfg = AutoConfig.from_pretrained(run, trust_remote_code=True, local_files_only=True)
cfg.disable_ocr = True
cfg.detection_model_config.use_pretrained_backbone = False
t = time.perf_counter()
model = (
    AutoModel.from_pretrained(
        run, config=cfg, trust_remote_code=True, local_files_only=True
    )
    .cuda()
    .eval()
)
print("LOADED", round(time.perf_counter() - t, 2), flush=True)
for name in ("0012", "0009", "0041"):
    img = np.array(Image.open(ROOT / ("secrets/manga/" + name + ".jpg")).convert("RGB"))
    t = time.perf_counter()
    with torch.inference_mode():
        result = model.predict_detections_and_associations([img])[0]
        inputs = model.move_to_device(
            model.processor.preprocess_inputs_for_detection([img])
        )
        scores, boxes = model._get_predicted_bboxes_and_classes(
            model._get_detection_transformer_output(**inputs)
        )
        values, labels = scores[0].max(-1)
        values = values.sigmoid()
        boxes = box_convert(boxes[0], "cxcywh", "xyxy") * torch.tensor(
            [img.shape[1], img.shape[0]] * 2, device=boxes.device
        )
        selected = (labels == 2) & (values > 0.1)
        rawboxes = boxes[selected]
        rawscores = values[selected]
        keep = nms(rawboxes, rawscores, 0.5)
        result["raw_panels"] = [
            {"box": b, "score": v}
            for b, v in zip(rawboxes[keep].tolist(), rawscores[keep].tolist())
        ]
    output = ROOT / "~outputs-intermediate/layout"
    output.mkdir(parents=True, exist_ok=True)
    (output / (name + ".json")).write_text(
        json.dumps(result, indent=2), encoding="utf-8"
    )
    print(
        name,
        "panels",
        result["raw_panels"],
        "seconds",
        round(time.perf_counter() - t, 2),
        flush=True,
    )
