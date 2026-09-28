# Magiv2 固定上游

- 用途：漫画分镜检测及阅读顺序。部分复用模型、配置、Python 源码及排序工具。
- 源码/权重：https://huggingface.co/ragavsachdeva/magiv2/tree/fbc890fec52977142e8ee00bfe26e9458b65517c
- 完整 revision：`fbc890fec52977142e8ee00bfe26e9458b65517c`。
- 项目：https://github.com/ragavsachdeva/magi ，调研 SHA `2a45bf09b43adc80778270a366372aaa148e2291`。
- 原样保存六个文件：config.json、configuration_magiv2.py、modelling_magiv2.py、processing_magiv2.py、utils.py、README.md。
- 代码和权重的许可说明保存在上游 README.md：个人、研究及非商业使用；商业用途联系作者。与 GPT-SoVITS 等组件的许可分别记录。
- 权重位置 `models/layout/magiv2/pytorch_model.bin`；完整 SHA256、字节数及下载 URL 见 config/layout-model.lock.json，与 Hugging Face LFS SHA256 已核对。
- 重建：`python tools/prepare_layout.py`；代码可从上述固定 revision 的 resolve URL 恢复并对照 lock 内 code 哈希。
- 上游源码不改。server/layout.py 禁用其英文 OCR、使用权重内 ResNet 参数而不另下载 backbone；替换面积覆盖去重为 torchvision NMS 以保留嵌套分镜，去除纯色截图外边距。
- 不调用章节转录流程，不使用 is_essential_text 删除旁白、拟声或其他文字；第一阶段不使用角色归属。
