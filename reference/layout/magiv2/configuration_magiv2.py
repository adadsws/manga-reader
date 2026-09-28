from transformers import PretrainedConfig, VisionEncoderDecoderConfig
from typing import List


class Magiv2Config(PretrainedConfig):
    model_type = "magiv2"

    def __init__(
        self,
        disable_ocr: bool = False,
        disable_crop_embeddings: bool = False,
        disable_detections: bool = False,
        detection_model_config: dict = None,
        ocr_model_config: dict = None,
        crop_embedding_model_config: dict = None,
        detection_image_preprocessing_config: dict = None,
        ocr_pretrained_processor_path: str = None,
        crop_embedding_image_preprocessing_config: dict = None,
        **kwargs,
    ):
        self.disable_ocr = disable_ocr
        self.disable_crop_embeddings = disable_crop_embeddings
        self.disable_detections = disable_detections
        self.kwargs = kwargs
        self.detection_model_config = None
        self.ocr_model_config = None
        self.crop_embedding_model_config = None
        if detection_model_config is not None:
            self.detection_model_config = PretrainedConfig.from_dict(detection_model_config)
        if ocr_model_config is not None:
            self.ocr_model_config = VisionEncoderDecoderConfig.from_dict(ocr_model_config)
        if crop_embedding_model_config is not None:
            self.crop_embedding_model_config = PretrainedConfig.from_dict(crop_embedding_model_config)
        
        self.detection_image_preprocessing_config = detection_image_preprocessing_config
        self.ocr_pretrained_processor_path = ocr_pretrained_processor_path
        self.crop_embedding_image_preprocessing_config = crop_embedding_image_preprocessing_config
        super().__init__(**kwargs)
