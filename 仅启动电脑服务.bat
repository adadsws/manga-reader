@echo off
setlocal
chcp 65001 >nul
title 漫画朗读 - 仅启动电脑服务
set "reader_follow=-Follow"
if /i "%~1"=="once" set "reader_follow="
if /i "%~1"=="stop" goto stop
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0tools\start.ps1" %reader_follow%
goto done
:stop
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0tools\stop.ps1"
:done
set "reader_exit=%errorlevel%"
if not "%READER_NO_PAUSE%"=="1" pause
exit /b %reader_exit%
