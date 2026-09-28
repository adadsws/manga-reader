# GPT-SoVITS

完整服务调用；MIT；https://github.com/RVC-Boss/GPT-SoVITS

固定 SHA: d523079fc05d9a8028d6085bffe4a2757c32abb6

源码通过 git archive 固定提交导出，保持干净。运行视图在 ~temp/gpt-sovits，模型只读复用 tavern，配置来自 config/tts.yaml。

重建源码：对上述完整提交执行 git archive，将结果解压到 reference/tts/GPT-SoVITS；不要使用浮动分支。服务启动器调用 tools/prepare_tts.py 创建可写运行副本。
