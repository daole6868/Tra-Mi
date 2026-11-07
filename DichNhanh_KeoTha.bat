@echo off
chcp 65001 >nul
title Subvideo - Dich nhanh
cd /d "%~dp0"
REM Keo tha 1 hoac nhieu video vao file nay de dich tu dong
REM (nhan dang giong noi + dich bang cong cu mac dinh trong Cai dat + ghep phu de tieng Viet)
if "%~1"=="" (
  echo Hay keo tha file video vao file .bat nay.
  pause
  exit /b 0
)
if not exist ".venv\Scripts\python.exe" call CaiDat_Windows.bat
".venv\Scripts\python.exe" TraMi.py dich %*
echo.
echo Xong! Duong dan ket qua in o tren.
pause
