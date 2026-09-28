$ErrorActionPreference='Stop'
$taskRoot=Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $taskRoot
$runtime=Get-Content -Raw -Encoding UTF8 config/runtime.json | ConvertFrom-Json
$taskPython=$runtime.python
$env:PYTHONUTF8='1'
# 旧版叠加层曾安装 CPU-only ONNX Runtime，会遮蔽固定运行时自带的 GPU 版；可逆迁移出 PYTHONPATH。
$taskOverlay=Join-Path $taskRoot '~temp/ocrdeps'
$taskLegacyOrt=Get-ChildItem -LiteralPath $taskOverlay -Filter 'onnxruntime-*.dist-info' -Directory -ErrorAction SilentlyContinue
if ($taskLegacyOrt) {
 $taskRetired=Join-Path $taskRoot ('~temp/retired-ocr-runtime/'+(Get-Date -Format 'yyyyMMdd-HHmmss'))
 New-Item -ItemType Directory -Force -Path $taskRetired | Out-Null
 $taskOrtPackage=Join-Path $taskOverlay 'onnxruntime'
 if (Test-Path -LiteralPath $taskOrtPackage) {Move-Item -LiteralPath $taskOrtPackage -Destination $taskRetired}
 foreach ($taskDistribution in $taskLegacyOrt) {Move-Item -LiteralPath $taskDistribution.FullName -Destination $taskRetired}
}
& $taskPython -B -m pip install --target '~temp/ocrdeps' --no-deps -r config/ocr-requirements.lock.txt
if ($LASTEXITCODE -ne 0) {throw 'OCR 依赖安装失败'}
& $taskPython -B -m pip install --target '~temp/ocrdeps' --no-deps 'torchvision==0.23.0+cu128' --index-url https://download.pytorch.org/whl/cu128
if ($LASTEXITCODE -ne 0) {throw 'torchvision 安装失败'}
& $taskPython -B tools/prepare_layout.py
if ($LASTEXITCODE -ne 0) {throw '布局模型准备失败'}
& $taskPython -B tools/prepare_tts.py
if ($LASTEXITCODE -ne 0) {throw 'TTS 准备失败'}
