@echo off
chcp 65001 >nul
title Subvideo - Dong goi file EXE
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Hay chay CaiDat_Windows.bat truoc.
  pause
  exit /b 1
)
set "VPY=.venv\Scripts\python.exe"
echo ============================================================
echo   DONG GOI Subvideo.exe - gom DU thu vien da cai trong .venv
echo   (PyTorch GPU, nhan dang, tach giong, doc giong, dich tren may)
echo   KHONG gom mo hinh AI: mo hinh van tai o tab Tai lieu.
echo   Can trong o dia ~10-15 GB, mat 15-40 phut.
echo ============================================================
echo.
echo [..] Cai PyInstaller...
"%VPY%" -m pip install -U "pyinstaller>=6.6" "pyinstaller-hooks-contrib>=2024.0"
echo [..] Dang dong goi...
"%VPY%" -m PyInstaller --noconfirm --clean Subvideo.spec
if errorlevel 1 (
  echo [LOI] Dong goi that bai - xem thong bao o tren.
  pause
  exit /b 1
)
REM chep FFmpeg vao canh file exe
if exist "bin\ffmpeg.exe" (
  if not exist "dist\Subvideo\bin" mkdir "dist\Subvideo\bin"
  copy /Y "bin\ffmpeg.exe" "dist\Subvideo\bin\" >nul
  copy /Y "bin\ffprobe.exe" "dist\Subvideo\bin\" >nul
)
REM ban exe tren may nay dung CHUNG thu muc data (du an, cai dat, mo hinh da tai) voi ban chay bang Python
if exist "%~dp0data" (
  powershell -NoProfile -Command "[IO.File]::WriteAllText('%~dp0dist\Subvideo\datadir.txt', '%~dp0data')" >nul 2>&1
)
call "%~dp0TaoBieuTuong.bat" nopause
echo.
echo ============================================================
echo   XONG! Phan mem: dist\Subvideo\Subvideo.exe
echo   Bieu tuong Subvideo da co tren Desktop va Start menu.
echo   Du lieu (du an, cai dat, mo hinh): %~dp0data
echo.
echo   Mang sang may khac: nen thu muc dist\Subvideo thanh .zip,
echo   hoac chay DongGoi_MoHinh.bat de gui kem mo hinh AI da tai.
echo   May do chi can driver NVIDIA (khong can cai Python);
echo   du lieu luu o %%LOCALAPPDATA%%\Subvideo, mo hinh tai lai o tab Tai lieu.
echo ============================================================
pause
