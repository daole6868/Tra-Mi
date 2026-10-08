@echo off
chcp 65001 >nul
title TraMi - Cai dat
cd /d "%~dp0"
echo ============================================================
echo    TraMi - Cong cu dich video Trung -^> Viet  ^|  CAI DAT
echo ============================================================
echo.

REM ---- 1. Tim Python (uu tien 3.11 / 3.12 / 3.10)
set "PY="
for %%V in (3.11 3.12 3.10) do (
  if not defined PY (
    py -%%V -c "import sys" >nul 2>&1 && set "PY=py -%%V"
  )
)
if not defined PY (
  python -c "import sys; assert (3,10) <= sys.version_info[:2] <= (3,12)" >nul 2>&1 && set "PY=python"
)
if not defined PY (
  echo [!] Chua co Python 3.10 - 3.12. Dang thu cai Python 3.11 bang winget...
  winget install -e --id Python.Python.3.11 --accept-package-agreements --accept-source-agreements
  py -3.11 -c "import sys" >nul 2>&1 && set "PY=py -3.11"
)
if not defined PY (
  echo.
  echo [LOI] Khong tim thay Python. Hay tai Python 3.11 tai:
  echo       https://www.python.org/downloads/release/python-3119/
  echo       Khi cai nho TICH vao o "Add python.exe to PATH", sau do chay lai file nay.
  pause
  exit /b 1
)
echo [OK] Dung Python: %PY%

REM ---- 2. Tao moi truong ao
if not exist ".venv\Scripts\python.exe" (
  echo [..] Dang tao moi truong ao .venv
  %PY% -m venv .venv
)
if not exist ".venv\Scripts\python.exe" (
  echo [LOI] Khong tao duoc moi truong ao .venv
  pause
  exit /b 1
)
set "VPY=.venv\Scripts\python.exe"
"%VPY%" -m pip install --upgrade pip wheel setuptools
echo [..] Dang cai thu vien loi (lan dau co the mat 5-10 phut)...
"%VPY%" -m pip install -r requirements.txt
if errorlevel 1 (
  echo [LOI] Cai thu vien that bai, kiem tra mang roi chay lai file nay.
  pause
  exit /b 1
)

REM ---- 3. Tai FFmpeg ban day du (co ffprobe, libass, NVENC)
if not exist "bin\ffmpeg.exe" (
  echo [..] Dang tai FFmpeg...
  if not exist bin mkdir bin
  powershell -NoProfile -ExecutionPolicy Bypass -Command "$ErrorActionPreference='Stop'; [Net.ServicePointManager]::SecurityProtocol='Tls12'; $z=Join-Path $env:TEMP 'trami_ffmpeg.zip'; Invoke-WebRequest 'https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip' -OutFile $z; $d=Join-Path $env:TEMP 'trami_ffmpeg'; if (Test-Path $d) {Remove-Item $d -Recurse -Force}; Expand-Archive $z $d; Get-ChildItem $d -Recurse -Include ffmpeg.exe,ffprobe.exe | Copy-Item -Destination 'bin' -Force; Remove-Item $z"
  if exist "bin\ffmpeg.exe" (
    echo [OK] Da tai FFmpeg
  ) else (
    echo [!] Khong tai duoc FFmpeg - se dung ban FFmpeg du phong tu pip.
  )
)

REM ---- 4. Tao loi tat tren Desktop (mo thang giao dien, khong hien cua so den)
powershell -NoProfile -Command "$s=(New-Object -ComObject WScript.Shell).CreateShortcut([Environment]::GetFolderPath('Desktop')+'\TraMi.lnk'); $s.TargetPath='%~dp0.venv\Scripts\pythonw.exe'; $s.Arguments='\"%~dp0TraMi.py\"'; $s.WorkingDirectory='%~dp0'; $s.IconLocation='%~dp0assets\TraMi.ico'; $s.Save()" >nul 2>&1

echo.
echo ============================================================
echo   CAI DAT PHAN LOI XONG!
echo.
echo   Nen cai them AI NANG CAO (tach giong/nhac nen, SenseVoice,
echo   phan biet nguoi noi, long tieng nhai giong, xoa phu de LaMa).
echo   Can them khoang 5-8 GB dung luong.
echo ============================================================
choice /C YN /M "Cai AI nang cao ngay bay gio"
if errorlevel 2 goto :done
call CaiDat_AI_NangCao.bat nopause

:done
echo.
echo Xong! Mo tool bang icon TraMi tren Desktop hoac file ChayTool_Windows.bat
pause
