@echo off
setlocal
chcp 65001 >nul
title 漫画朗读 - 仅启动安卓模拟器
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0tools\start_emulator.ps1"
set "reader_exit=%errorlevel%"
if "%reader_exit%"=="0" (
    echo.
    echo [完成] ReaderAosp35、reader.apk 和安卓状态窗口均已就绪。
    exit /b 0
)
echo.
echo [失败] 安卓启动未完成，退出码 %reader_exit%。
echo 请查看上方具体阶段；模拟器诊断日志位于 ~temp\logs\emulator-*.log。
if not "%READER_NO_PAUSE%"=="1" (
    echo 按任意键关闭此失败窗口…
    pause >nul
)
exit /b %reader_exit%
