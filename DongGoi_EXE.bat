@echo off
chcp 65001 >nul
title TraMi - Dong goi file EXE
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Hay chay CaiDat_Windows.bat truoc.
  pause
  exit /b 1
)
set "VPY=.venv\Scripts\python.exe"
echo [..] Cai PyInstaller...
"%VPY%" -m pip install "pyinstaller>=6.6"
echo [..] Dang dong goi (5-20 phut, tuy so goi AI da cai)...
"%VPY%" -m PyInstaller --noconfirm --clean TraMi.spec
if errorlevel 1 (
  echo [LOI] Dong goi that bai - xem thong bao o tren.
  pause
  exit /b 1
)
REM chep FFmpeg vao canh file exe
if exist "bin\ffmpeg.exe" (
  if not exist "dist\TraMi\bin" mkdir "dist\TraMi\bin"
  copy /Y "bin\ffmpeg.exe" "dist\TraMi\bin\" >nul
  copy /Y "bin\ffprobe.exe" "dist\TraMi\bin\" >nul
)
echo.
echo ============================================================
echo   XONG! Thu muc phan mem: dist\TraMi\   (chay TraMi.exe)
echo   Nen thu muc dist\TraMi thanh .zip de gui cho may khac.
echo   Du lieu du an luu o: %%LOCALAPPDATA%%\TraMi
echo ============================================================
pause
