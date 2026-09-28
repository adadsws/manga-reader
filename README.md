# 漫画朗读

在 Android 上打开漫画，点一下浮窗，电脑会按分镜顺序识别文字并用所选角色音色朗读；读完还可以自动翻页。OCR、分镜分析、语音合成和语音完整性检查都在本机完成，使用时电脑需保持开机。

## 效果预览

下面是正式应用通过 MediaProjection 上传的真实漫画页面，以及程序实际返回 Android 播放的整页音频。本页识别为 9 个朗读单元，音频 28.26 秒。

<p align="center">
  <img src="docs/readme-assets/manga-page.jpg" alt="漫画页面预览" width="420">
</p>

<audio controls preload="metadata" src="https://raw.githubusercontent.com/adadsws/manga-reader/main/docs/readme-assets/reading-preview.wav">
  当前 README 查看器不支持音频播放器。
</audio>

▶ [在浏览器中直接播放](https://raw.githubusercontent.com/adadsws/manga-reader/main/docs/readme-assets/reading-preview.wav) · [下载整页 WAV](docs/readme-assets/reading-preview.wav)

## 界面预览

| 连接、模型与常用设置 | 动态模型目录 |
|---|---|
| <img src="docs/readme-assets/settings.png" alt="连接、模型与常用设置" width="360"> | <img src="docs/readme-assets/models.png" alt="动态模型目录" width="360"> |
| **高级选项、自动翻页与权限** | **漫画上的朗读浮窗** |
| <img src="docs/readme-assets/advanced.png" alt="高级选项、自动翻页与权限" width="360"> | <img src="docs/readme-assets/overlay.png" alt="漫画上的朗读浮窗" width="360"> |

## 能做什么

- 识别简体、繁体中文并按漫画分镜顺序朗读；繁体会转成简体发音。
- 从电脑上的 GPT-SoVITS 模型目录选择角色和型号，显示可用状态与头像。
- 调整语速、句间停顿和浮窗透明度，暂停后可从原位置继续。
- 自动翻页，支持左右方向、自适应抓帧和末页停止。
- 可选逐段 ASR 完整性检查；遇到疑似漏读会自动重试。
- 精确复用已经处理过的页面，减少重复 OCR 和语音等待。

## 直接使用

当前项目已经配置好运行环境时：

1. 双击 [启动电脑服务和安卓模拟器.bat](启动电脑服务和安卓模拟器.bat)。
2. 等电脑窗口显示“服务已就绪且已预热”，应用显示“电脑已连接”。
3. 选择朗读模型，按需要调整语速、停顿和自动翻页。
4. 点“保存并启动朗读浮窗”，允许浮窗并选择投屏“整个屏幕”。
5. 打开漫画，点浮窗上的 **▶ 重新开始**。

浮窗运行时提供 **全部停止、暂停/继续、关闭**；暂停只停止播放，后台仍会处理本页，全部停止才会取消任务并清空本页。

首次在新电脑部署还要准备 CUDA、本地 GPT-SoVITS 模型和约 2 GB 的 Magi 权重，步骤见下文。

## 新电脑部署

在项目目录依次运行：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File tools/prepare_runtime.ps1
python tools/prepare_layout.py
python tools/prepare_android.py
python tools/prepare_avd.py
python tools/build_android.py
```

项目已在 Windows、NVIDIA CUDA、官方 Android 15 / API 35 x86_64 模拟器上验证。电脑配置位于 `config/runtime.json`、`config/reader.json` 和 `config/tts.yaml`；换电脑后要按实际路径修改。公开仓库不包含测试漫画或模型权重，构建正式 APK 不需要测试漫画。大模型的来源和恢复方法见[蔚蓝档案模型](models/gpt-sovits/蔚蓝档案-日语.md)、[终末地模型](models/gpt-sovits/终末地-中文.md)及[Magi v2 布局模型](models/layout/magiv2.md)。

## 换成真实手机

1. 让手机和电脑连接同一个 Wi-Fi；不要使用访客网络。
2. 双击 [仅启动电脑服务.bat](仅启动电脑服务.bat)，记下窗口显示的完整地址。
3. 把项目根目录唯一的 [reader.apk](reader.apk) 发到手机并安装；以后重新构建会直接更新此文件。
4. 在应用中填写电脑地址和配对口令，显示“电脑已连接”后选择模型并启动浮窗。
5. 需要自动翻页时，再开启“漫画朗读”的无障碍权限。

真手机不能填写 `127.0.0.1` 或 `10.0.2.2`；应使用电脑在同一 Wi-Fi 下的地址。首次出现 Windows 防火墙提示时，只允许专用网络。实体手机尚未完成全流程实测。

## 常见问题

- **手机连不上电脑：**确认两台设备在同一 Wi-Fi，地址包含 `http://` 和 `:8765`，配对口令正确，并关闭访客网络/AP 隔离。
- **读错或漏读：**放大漫画后重新开始；复杂字体、极小字和分镜仍可能识别或排序错误。
- **显示“语音失败”：**点浮窗中的“查看完整错误”，电脑服务窗口也会显示原因。
- **ASR 仍无法确认：**程序已完成有限重试，会继续播放并把该段标为待复核。
- **模拟器启动失败：**查看 `~temp/logs/emulator.err.log`。
- **停止电脑服务：**在项目目录运行 `仅启动电脑服务.bat stop`；直接关闭状态窗口不会停止后台服务。

版本变化见[更新记录](CHANGELOG.md)。
