@echo off
chcp 65001 >nul
title ToolAutoXalat - Production Snapshot
cd /d "%~dp0"
echo Dang tao va khoi dong ban production snapshot...
echo.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\start-production-snapshot.ps1"
set EXIT_CODE=%ERRORLEVEL%
echo.
if not "%EXIT_CODE%"=="0" (
  echo Khoi dong that bai. Ma loi: %EXIT_CODE%
  echo Xem log trong thu muc runtime\logs.
) else (
  echo Hoan tat. Nhan phim bat ky de dong cua so nay.
)
pause >nul
exit /b %EXIT_CODE%
