@echo off
chcp 65001 >nul
cd /d "%~dp0"
title Shift Conflict Checker
echo ============================================================
echo   Employee Monthly Shift Conflict Checker
echo ============================================================
echo.
where python >nul 2>nul
if errorlevel 1 (
  echo [!] Python not found in PATH. Install Python 3.10+ first.
  pause
  exit /b 1
)
python app.py
if errorlevel 1 (
  echo.
  echo [!] App exited with an error. See messages above.
  pause
)
