@echo off
chcp 65001 >nul
title ToolAutoXalat - Stop Production
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\stop-all.ps1"
echo.
pause
