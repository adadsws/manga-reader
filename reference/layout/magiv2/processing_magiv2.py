from transformers import ConditionalDetrImageProcessor, TrOCRProcessor, ViTImageProcessor
import torch
from typing import List
from shapely.geometry import box
from .utils import x1y1x2y2_to_xywh
import numpy as np

class Magiv2Processor():
    def __init__(self, config):
        self.config = config
        self.detection_image_preprocessor = None
        self.ocr_preprocessor = None
        self.crop_embedding_image_preprocessor = None
        if not config.disable_detections:
            assert config.detection_image_preprocessing_config is not None
            self.detection_image_preprocessor =  ConditionalDetrImageProcessor.from_dict(config.detection_image_preprocessing_config)
        if not config.disable_ocr:
            assert config.ocr_pretrained_processor_path is not None
            self.ocr_preprocessor = TrOCRProcessor.from_pretrained(config.ocr_pretrained_processor_path)
        if not config.disable_crop_embeddings:
            assert config.crop_embedding_image_preprocessing_config is not None
            self.crop_embedding_image_preprocessor = ViTImageProcessor.from_dict(config.crop_embedding_image_preprocessing_config)
    
    def preprocess_inputs_for_detection(self, images, annotations=None):
        images = list(images)
        assert isinstance(images[0], np.ndarray)
        annotations = self._convert_annotations_to_coco_format(annotations)
        inputs = self.detection_image_preprocessor(images, annotations=annotations, return_tensors="pt")
        return inputs

    def preprocess_inputs_for_ocr(self, images):
        images = list(images)
        assert isinstance(images[0], np.ndarray)
        return self.ocr_preprocessor(images, return_tensors="pt").pixel_values
    
    def preprocess_inputs_for_crop_embeddings(self, images):
        images = list(images)
        assert isinstance(images[0], np.ndarray)
        return self.crop_embedding_image_preprocessor(images, return_tensors="pt").pixel_values
    
    def postprocess_ocr_tokens(self, generated_ids, skip_special_tokens=True):
        return self.ocr_preprocessor.batch_decode(generated_ids, skip_special_tokens=skip_special_tokens)
    
    def crop_image(self, image, bboxes):
        crops_for_image = []
        for bbox in bboxes:
            x1, y1, x2, y2 = bbox

            # fix the bounding box in case it is out of bounds or too small
            x1, y1, x2, y2 = int(x1), int(y1), int(x2), int(y2)
            x1, y1, x2, y2 = min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2) # just incase
            x1, y1 = max(0, x1), max(0, y1)
            x1, y1 = min(image.shape[1], x1), min(image.shape[0], y1)
            x2, y2 = max(0, x2), max(0, y2)
            x2, y2 = min(image.shape[1], x2), min(image.shape[0], y2)
            if x2 - x1 < 10:
                if image.shape[1] - x1 > 10:
                    x2 = x1 + 10
                else:
                    x1 = x2 - 10
            if y2 - y1 < 10:
                if image.shape[0] - y1 > 10:
                    y2 = y1 + 10
                else:
                    y1 = y2 - 10

            crop = image[y1:y2, x1:x2]
            crops_for_image.append(crop)
        return crops_for_image

    def _get_indices_of_characters_to_keep(self, batch_scores, batch_labels, batch_bboxes, character_detection_threshold):
        indices_of_characters_to_keep = []
        for scores, labels, _ in zip(batch_scores, batch_labels, batch_bboxes):
            indices = torch.where((labels == 0) & (scores > character_detection_threshold))[0]
            indices_of_characters_to_keep.append(indices)
        return indices_of_characters_to_keep
    
    def _get_indices_of_panels_to_keep(self, batch_scores, batch_labels, batch_bboxes, panel_detection_threshold):
        indices_of_panels_to_keep = []
        for scores, labels, bboxes in zip(batch_scores, batch_labels, batch_bboxes):
            indices = torch.where(labels == 2)[0]
            bboxes = bboxes[indices]
            scores = scores[indices]
            labels = labels[indices]
            if len(indices) == 0:
                indices_of_panels_to_keep.append([])
                continue
            scores, labels, indices, bboxes  = zip(*sorted(zip(scores, labels, indices, bboxes), reverse=True))
            panels_to_keep = []
            union_of_panels_so_far = box(0, 0, 0, 0)
            for ps, pb, pl, pi in zip(scores, bboxes, labels, indices):
                panel_polygon = box(pb[0], pb[1], pb[2], pb[3])
                if ps < panel_detection_threshold:
                    continue
                if union_of_panels_so_far.intersection(panel_polygon).area / panel_polygon.area > 0.5:
                    continue
                panels_to_keep.append((ps, pl, pb, pi))
                union_of_panels_so_far = union_of_panels_so_far.union(panel_polygon)
            indices_of_panels_to_keep.append([p[3].item() for p in panels_to_keep])
        return indices_of_panels_to_keep
    
    def _get_indices_of_texts_to_keep(self, batch_scores, batch_labels, batch_bboxes, text_detection_threshold):
        indices_of_texts_to_keep = []
        for scores, labels, bboxes in zip(batch_scores, batch_labels, batch_bboxes):
            indices = torch.where((labels == 1) & (scores > text_detection_threshold))[0]
            bboxes = bboxes[indices]
            scores = scores[indices]
            labels = labels[indices]
            if len(indices) == 0:
                indices_of_texts_to_keep.append([])
                continue
            scores, labels, indices, bboxes  = zip(*sorted(zip(scores, labels, indices, bboxes), reverse=True))
            texts_to_keep = []
            texts_to_keep_as_shapely_objects = []
            for ts, tb, tl, ti in zip(scores, bboxes, labels, indices):
                text_polygon = box(tb[0], tb[1], tb[2], tb[3])
                should_append = True
                for t in texts_to_keep_as_shapely_objects:
                    if t.intersection(text_polygon).area / t.union(text_polygon).area > 0.5:
                        should_append = False
                        break
                if should_append:
                    texts_to_keep.append((ts, tl, tb, ti))
                    texts_to_keep_as_shapely_objects.append(text_polygon)
            indices_of_texts_to_keep.append([t[3].item() for t in texts_to_keep])
        return indices_of_texts_to_keep
    
    def _get_indices_of_tails_to_keep(self, batch_scores, batch_labels, batch_bboxes, text_detection_threshold):
        indices_of_texts_to_keep = []
        for scores, labels, bboxes in zip(batch_scores, batch_labels, batch_bboxes):
            indices = torch.where((labels == 3) & (scores > text_detection_threshold))[0]
            bboxes = bboxes[indices]
            scores = scores[indices]
            labels = labels[indices]
            if len(indices) == 0:
                indices_of_texts_to_keep.append([])
                continue
            scores, labels, indices, bboxes  = zip(*sorted(zip(scores, labels, indices, bboxes), reverse=True))
            texts_to_keep = []
            texts_to_keep_as_shapely_objects = []
            for ts, tb, tl, ti in zip(scores, bboxes, labels, indices):
                text_polygon = box(tb[0], tb[1], tb[2], tb[3])
                should_append = True
                for t in texts_to_keep_as_shapely_objects:
                    if t.intersection(text_polygon).area / t.union(text_polygon).area > 0.5:
                        should_append = False
                        break
                if should_append:
                    texts_to_keep.append((ts, tl, tb, ti))
                    texts_to_keep_as_shapely_objects.append(text_polygon)
            indices_of_texts_to_keep.append([t[3].item() for t in texts_to_keep])
        return indices_of_texts_to_keep
        
    def _convert_annotations_to_coco_format(self, annotations):
        if annotations is None:
            return None
        self._verify_annotations_are_in_correct_format(annotations)
        coco_annotations = []
        for annotation in annotations:
            coco_annotation = {
                "image_id": annotation["image_id"],
                "annotations": [],
            }
            for bbox, label in zip(annotation["bboxes_as_x1y1x2y2"], annotation["labels"]):
                coco_annotation["annotations"].append({
                    "bbox": x1y1x2y2_to_xywh(bbox),
                    "category_id": label,
                    "area": (bbox[2] - bbox[0]) * (bbox[3] - bbox[1]),
                })
            coco_annotations.append(coco_annotation)
        return coco_annotations
    
    def _verify_annotations_are_in_correct_format(self, annotations):
        error_msg = """
        Annotations must be in the following format:
        [
            {
                "image_id": 0,
                "bboxes_as_x1y1x2y2": [[0, 0, 10, 10], [10, 10, 20, 20], [20, 20, 30, 30]],
                "labels": [0, 1, 2],
            },
            ...
        ]
        Labels: 0 for characters, 1 for text, 2 for panels.
        """
        if annotations is None:
            return
        if not isinstance(annotations, List) and not isinstance(annotations, tuple):
            raise ValueError(
                f"{error_msg} Expected a List/Tuple, found {type(annotations)}."
            )
        if len(annotations) == 0:
            return
        if not isinstance(annotations[0], dict):
            raise ValueError(
                f"{error_msg} Expected a List[Dicct], found {type(annotations[0])}."
            )
        if "image_id" not in annotations[0]:
            raise ValueError(
                f"{error_msg} Dict must contain 'image_id'."
            )
        if "bboxes_as_x1y1x2y2" not in annotations[0]:
            raise ValueError(
                f"{error_msg} Dict must contain 'bboxes_as_x1y1x2y2'."
            )
        if "labels" not in annotations[0]:
            raise ValueError(
                f"{error_msg} Dict must contain 'labels'."
            )
