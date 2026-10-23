@echo off
chcp 65001 >nul
cd /d "%~dp0"
title Douyin Agent - start

set "NO_PAUSE=0"
if /I "%~1"=="--no-pause" set "NO_PAUSE=1"

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\start.ps1"
set "RC=%ERRORLEVEL%"

if not "%NO_PAUSE%"=="1" pause
exit /b %RC%
