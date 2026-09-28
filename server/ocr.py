# 直接复用 manga-image-translator 固定 SHA 的分镜与排序，不加载翻译器。
import importlib.util
import sys
import types
import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import cv2
import numpy as np
from rapidocr import RapidOCR
from opencc import OpenCC
from .core import group_lines, split_sentences, map_text_to_panels, filter_speech_candidates, compact_reading_order, intersection_area, box_area
from .layout import MangaLayout

ROOT = Path(__file__).resolve().parents[1]
UPSTREAM = ROOT / "reference/ocr/manga-image-translator/manga_translator/utils"
parent = types.ModuleType("reader_upstream")
parent.__path__ = []
sys.modules[parent.__name__] = parent
pkg = types.ModuleType("reader_upstream.utils")
pkg.__path__ = [str(UPSTREAM)]
pkg.get_logger = logging.getLogger
sys.modules[pkg.__name__] = pkg
stub = types.ModuleType("reader_upstream.utils.textblock")
stub.TextBlock = object
sys.modules[stub.__name__] = stub
spec = importlib.util.spec_from_file_location(
    "reader_upstream.utils.sort", UPSTREAM / "sort.py"
)
sort = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = sort
spec.loader.exec_module(sort)


class Region:
    def __init__(self, data):
        self.data = data
        self.xyxy = data["box"]
        x, y, r, b = self.xyxy
        self.center = ((x + r) / 2, (y + b) / 2)


class MangaOCR:
    def __init__(self, device="cuda"):
        # 设备配置是明确约束：初始化失败时向上报告，不在故障后换设备重跑。
        requested = str(device or "cuda").strip().lower()
        if requested not in ("cuda", "cpu", "auto"):
            raise ValueError("ocr_device 只能是 cuda、cpu 或 auto")
        self.requested_device = requested
        use_cuda = requested != "cpu"
        self.engine = RapidOCR(
            params={"EngineConfig.onnxruntime.use_cuda": use_cuda}
        )
        self.ocr_providers = self._providers(self.engine)
        cuda_ready = bool(self.ocr_providers) and all(
            providers and providers[0] == "CUDAExecutionProvider"
            for providers in self.ocr_providers.values()
        )
        self.ocr_backend = "cuda" if cuda_ready else "cpu"
        if requested == "cuda" and not cuda_ready:
            raise RuntimeError(
                "配置要求 OCR 使用 CUDA，但 CUDAExecutionProvider "
                "未成为 det/cls/rec 全部子模型的首选 Provider"
            )
        # 小字扩边复识是多次微型推理；CPU 避免 CUDA 逐次启动与拷贝开销。
        self.refine_engine = (
            RapidOCR(params={"EngineConfig.onnxruntime.use_cuda": False})
            if self.ocr_backend == "cuda"
            else self.engine
        )
        self.refine_providers = self._providers(self.refine_engine)
        self.cc = OpenCC("t2s")
        self.layout = MangaLayout(requested)

    @staticmethod
    def _providers(engine):
        providers = {}
        for name, attribute in (
            ("det", "text_det"),
            ("cls", "text_cls"),
            ("rec", "text_rec"),
        ):
            component = getattr(engine, attribute, None)
            session = getattr(getattr(component, "session", None), "session", None)
            if session is not None and hasattr(session, "get_providers"):
                providers[name] = list(session.get_providers())
        return providers

    def refine_short_rows(self, frame, rows, text_regions):
        # 对模型文字区内高置信短行扩边放大复识，补回易漏掉的细小笔画。
        result = []
        for row in rows:
            base = re.sub(r"[^\u3400-\u9fff]", "", row["text"])
            box = row["box"]
            coverage = max((intersection_area(box, r["box"]) / max(1, box_area(box)) for r in text_regions), default=0)
            if 1 <= len(base) <= 3 and row["score"] >= .95 and coverage >= .6:
                pad = max(4, round(min(box[2]-box[0], box[3]-box[1]) * .2))
                x0,y0 = max(0,int(box[0])-pad),max(0,int(box[1])-pad)
                x1,y1 = min(frame.shape[1],int(box[2])+pad+1),min(frame.shape[0],int(box[3])+pad+1)
                crop = cv2.resize(frame[y0:y1,x0:x1],None,fx=3,fy=3,interpolation=cv2.INTER_CUBIC)
                refine_engine = getattr(self, "refine_engine", self.engine)
                refined = refine_engine(crop, use_det=True, use_cls=True, use_rec=True)
                candidates = []
                if refined.boxes is not None:
                    for b,t,score in zip(refined.boxes,refined.txts,refined.scores):
                        clean = re.sub(r"[^\u3400-\u9fff]", "", t)
                        it = iter(clean)
                        contains = all(c in it for c in base)
                        mapped = [float(b[:,0].min()/3+x0),float(b[:,1].min()/3+y0),float(b[:,0].max()/3+x0),float(b[:,1].max()/3+y0)]
                        overlap = intersection_area(box,mapped)/max(1,box_area(box))
                        if score >= .9 and len(base)<len(clean)<=len(base)+4 and contains and overlap>=.6:
                            candidates.append((score,t,mapped))
                if candidates:
                    score,text,mapped = max(candidates,key=lambda r:r[0])
                    row = dict(row, text=text, box=mapped, score=float(score), refinement=dict(original_text=row["text"], original_box=box, method="expanded_crop_3x"))
            result.append(row)
        return result

    def warmup(self):
        # 只用内存生成的中性测试图触发 OCR 与版面模型首次推理，不读取用户图片。
        frame = np.full((1920, 1080, 3), 245, dtype=np.uint8)
        cv2.rectangle(frame, (80, 100), (1000, 1820), (30, 30, 30), 4)
        cv2.rectangle(frame, (120, 160), (960, 860), (80, 80, 80), 3)
        cv2.putText(frame, "123", (360, 520), cv2.FONT_HERSHEY_SIMPLEX, 4, (0, 0, 0), 10)
        ok, encoded = cv2.imencode(".jpg", frame)
        if not ok:
            raise ValueError("无法生成 OCR 预热图")
        self.read(encoded.tobytes())

    def read(self, body, parallel=False):
        frame = cv2.imdecode(np.frombuffer(body, np.uint8), cv2.IMREAD_COLOR)
        if frame is None:
            raise ValueError("无法解码截图")
        if frame.shape[0] * frame.shape[1] > 16000000:
            raise ValueError("截图像素过大")
        started = time.monotonic()
        def timed(call):
            step = time.monotonic()
            return call(), time.monotonic() - step
        if parallel:
            # RapidOCR 与 Magi 只共享只读画面；各自模型独立，可安全重叠推理。
            with ThreadPoolExecutor(max_workers=2, thread_name_prefix="reader-vision") as pool:
                ocr_future = pool.submit(timed, lambda: self.engine(frame))
                layout_future = pool.submit(timed, lambda: self.layout.panels(frame))
                result, ocr_seconds = ocr_future.result()
                panels, layout_seconds = layout_future.result()
        else:
            result, ocr_seconds = timed(lambda: self.engine(frame))
            if result.boxes is None:
                self.last_timings = {"ocr_seconds": round(ocr_seconds, 3),
                                     "layout_seconds": 0.0,
                                     "post_seconds": 0.0,
                                     "total_seconds": round(time.monotonic() - started, 3),
                                     "parallel": False,
                                     "ocr_backend": getattr(self, "ocr_backend", "unknown")}
                return []
            panels, layout_seconds = timed(lambda: self.layout.panels(frame))
        if result.boxes is None:
            self.last_timings = {"ocr_seconds": round(ocr_seconds, 3),
                                 "layout_seconds": round(layout_seconds, 3),
                                 "post_seconds": 0.0,
                                 "total_seconds": round(time.monotonic() - started, 3),
                                 "parallel": parallel,
                                 "ocr_backend": getattr(self, "ocr_backend", "unknown")}
            return []
        rows = []
        for box, text, score in zip(result.boxes, result.txts, result.scores):
            rows.append(
                {
                    "box": [
                        float(box[:, 0].min()),
                        float(box[:, 1].min()),
                        float(box[:, 0].max()),
                        float(box[:, 1].max()),
                    ],
                    "text": text,
                    "score": float(score),
                }
            )
        post_started = time.monotonic()
        rows = self.refine_short_rows(frame, rows, getattr(self.layout, "text_regions", []))
        rows, excluded = filter_speech_candidates(
            rows, getattr(self.layout, "text_regions", []) if panels else []
        )
        # 板块模式先恢复连续文字列；检测区域碎片不能打断一个短语。
        groups = group_lines(rows, panels=panels)
        if panels:
            mapping = map_text_to_panels([g["box"] for g in groups], panels)
            grouped = {}
            for g, panel in zip(groups, mapping):
                g["panel_id"] = panel
                g["panel_box"] = panels[panel]
                grouped.setdefault(panel, []).append(Region(g))
            groups = []
            for panel in sorted(grouped):
                regions = grouped[panel]
                order = compact_reading_order([r.data for r in regions])
                ordered = [regions[i] for i in order] if order is not None else sort._simple_sort(regions, True)
                groups.extend(r.data for r in ordered)
        else:
            regions = sort.sort_regions(
                [Region(g) for g in groups], right_to_left=True, img=frame
            )
            groups = [r.data for r in regions]
        # 每个排序后的完整文字区域就是一个朗读单元；不按标点再拆分。
        sentences = [
            {
                "original": g["original"],
                "text": self.cc.convert(g["original"]),
                "box": g["box"],
                "columns": g["columns"],
                "confidence": g["score"],
                "speaker_id": "default",
                "segmentation": "model_text_region_with_geometry_fallback",
                "panel_id": g.get("panel_id"),
                "panel_box": g.get("panel_box"),
            }
            for g in groups
        ]
        if sentences:
            sentences[0]["excluded_texts"] = excluded
        self.last_timings = {
            "ocr_seconds": round(ocr_seconds, 3),
            "layout_seconds": round(layout_seconds, 3),
            "post_seconds": round(time.monotonic() - post_started, 3),
            "total_seconds": round(time.monotonic() - started, 3),
            "parallel": parallel,
            "ocr_backend": getattr(self, "ocr_backend", "unknown"),
        }
        return sentences
