@echo off
setlocal
chcp 65001 >nul
title ASR Local Hot Debug
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\dev\start_electron_debug.ps1" -DataProfile real
if errorlevel 1 pause
endlocal
