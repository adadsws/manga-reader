"""固定 Magiv2 的薄适配：关闭英文 OCR，保留嵌套分镜，不使用对白筛选。"""

import importlib
import os
import shutil
from pathlib import Path
import numpy as np
import torch
from torchvision.ops import box_convert, nms
from transformers import AutoConfig, AutoModel
from transformers.utils import logging
from .core import select_panel_candidates

ROOT = Path(__file__).resolve().parents[1]


class MangaLayout:
    def __init__(self, device="cuda"):
        requested = str(device or "cuda").strip().lower()
        if requested not in ("cuda", "cpu", "auto"):
            raise ValueError("ocr_device 只能是 cuda、cpu 或 auto")
        if requested == "cuda" and not torch.cuda.is_available():
            raise RuntimeError("配置要求 Magi 使用 CUDA，但 PyTorch CUDA 当前不可用")
        self.device = (
            "cpu" if requested == "cpu"
            else "cuda" if torch.cuda.is_available()
            else "cpu"
        )
        run = ROOT / "~temp/magiv2"
        run.mkdir(parents=True, exist_ok=True)
        for source in (ROOT / "reference/layout/magiv2").iterdir():
            if source.suffix in (".py", ".json"):
                shutil.copy2(source, run / source.name)
        weights = run / "pytorch_model.bin"
        if not weights.exists():
            os.link(ROOT / "models/layout/magiv2/pytorch_model.bin", weights)
        config = AutoConfig.from_pretrained(
            run, trust_remote_code=True, local_files_only=True
        )
        config.disable_ocr = True
        config.detection_model_config.use_pretrained_backbone = False
        # 原权重包含英文 OCR；只允许这部分成为未使用权重，不能吞掉检测模型的不兼容。
        previous = logging.get_verbosity()
        try:
            logging.set_verbosity_error()
            model, info = AutoModel.from_pretrained(
                run,
                config=config,
                trust_remote_code=True,
                local_files_only=True,
                output_loading_info=True,
            )
        finally:
            logging.set_verbosity(previous)
        if (
            info["missing_keys"]
            or info.get("mismatched_keys")
            or any(not k.startswith("ocr_model.") for k in info["unexpected_keys"])
        ):
            raise RuntimeError("Magi 权重与固定代码不匹配")
        self.model = model.to(self.device).eval()
        self.utils = importlib.import_module(
            self.model.__module__.rsplit(".", 1)[0] + ".utils"
        )

    @torch.inference_mode()
    def panels(self, bgr):
        # 阅读器可能在页外留纯白/纯黑边；去除无内容边距，防止分镜模型把边距当大格。
        gray = bgr.mean(axis=2)
        background = float(np.median(np.concatenate([gray[0], gray[-1]])))
        foreground = (
            np.abs(gray - background) > 20
            if background < 20 or background > 235
            else np.ones(gray.shape, dtype=bool)
        )
        ys, xs = np.where(foreground)
        x0 = y0 = 0
        if len(xs):
            x0 = max(0, int(xs.min()) - 4)
            y0 = max(0, int(ys.min()) - 4)
            bgr = bgr[
                y0 : min(bgr.shape[0], int(ys.max()) + 5),
                x0 : min(bgr.shape[1], int(xs.max()) + 5),
            ]
        rgb = np.ascontiguousarray(bgr[:, :, ::-1])
        inputs = self.model.move_to_device(
            self.model.processor.preprocess_inputs_for_detection([rgb])
        )
        output = self.model._get_detection_transformer_output(**inputs)
        scores, boxes = self.model._get_predicted_bboxes_and_classes(output)
        values, labels = scores[0].max(-1)
        values = values.sigmoid()
        height, width = rgb.shape[:2]
        boxes = box_convert(boxes[0], "cxcywh", "xyxy") * boxes.new_tensor(
            [width, height, width, height]
        )
        # 复用固定 Magiv2 的文本检测区域，与画格共用一次前向推理。
        indices = self.model.processor._get_indices_of_texts_to_keep(
            values[None], labels[None], boxes[None], 0.3
        )[0]
        tokens = self.model._get_predicted_obj_tokens(output)
        dialogue = self.model._get_text_classification([tokens[0][indices]], apply_sigmoid=True)[0]
        self.text_regions = [
            {
                "box": [b[0] + x0, b[1] + y0, b[2] + x0, b[3] + y0],
                "dialogue_score": float(d),
            }
            for b, d in zip(boxes[indices].cpu().tolist(), dialogue.cpu().tolist())
        ]
        # 上游按面积覆盖率排除嵌套格；标准 IoU NMS 保留小格和大格。
        selected = (labels == 2) & (values > 0.2)
        boxes, values = boxes[selected], values[selected]
        keep = nms(boxes, values, 0.5)
        boxes = select_panel_candidates(
            boxes[keep].cpu().tolist(), values[keep].cpu().tolist()
        )
        order = self.utils.sort_panels(boxes)
        return [
            [boxes[i][0] + x0, boxes[i][1] + y0, boxes[i][2] + x0, boxes[i][3] + y0]
            for i in order
        ]

    def order(self, groups, panels):
        return [
            groups[i]
            for i in self.utils.sort_text_boxes_in_reading_order(
                [g["box"] for g in groups], panels
            )
        ]
