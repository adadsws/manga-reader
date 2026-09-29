@echo off
setlocal
chcp 65001 >nul
title 漫画朗读 - 启动电脑服务和安卓模拟器
if /i "%~1"=="stop" goto stop
start "漫画朗读 - 电脑服务" cmd.exe /k call "%~dp0仅启动电脑服务.bat"
call "%~dp0仅启动安卓模拟器.bat" keep-service
exit /b %errorlevel%
:stop
call "%~dp0仅启动电脑服务.bat" stop
exit /b %errorlevel%
