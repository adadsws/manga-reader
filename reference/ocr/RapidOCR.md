# RapidOCR 和中文转换

- RapidOCR 3.9.2：https://pypi.org/project/rapidocr/3.9.2/ ，https://github.com/RapidAI/RapidOCR ，Apache-2.0。
- 方式：完整包调用。固定安装清单见 config/ocr-requirements.lock.txt；模型为包内 PP-OCRv6 det/rec small 与方向分类 ONNX，SHA256 见 config/ocr-models.lock.json。
- 简繁转换：opencc-python-reimplemented 0.1.7，Apache-2.0，https://pypi.org/project/opencc-python-reimplemented/0.1.7/ 。t2s 只转换字形，保留 original 字段。
- 重建：tools/prepare_runtime.ps1 仅向 reader/~temp/ocrdeps 安装固定版本，不改 tavern。
- NumPy 固定 1.26.4、OpenCV 固定 4.11.0.86；OpenCV 5 的 LSD 输出与本项目固定 Kumiko 不兼容，已实测回退。
- 不重写 OCR 网络；本地代码只负责坐标、行分组、简体输出和上游排序适配。
