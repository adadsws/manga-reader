# tavern 中文朗读参考

只读来源为本地 tavern 项目的固定提交 `e6bce3df5704ff2c3dbba51a791a738857a9a177`；公开仓库不包含该本地运行目录。

参考 apps/gpt-sovits/tts/interactive_tts.py、compat/sillytavern_bridge.py 和 compat/api_v2_compat.py。复用其中文目标、日语参考、cut5、batch_size=1、非流式 WAV 配置模式；没有复制其对白过滤逻辑。固定 GPT-SoVITS 版本见同目录 GPT-SoVITS.md。

运行库和基础权重只读复用，路径见 config/runtime.json；reader 创建自己的 TTS 源码运行视图和独立的 9882 服务，不改变 tavern 的权重状态或配置。首次准备运行 tools/prepare_tts.py。
