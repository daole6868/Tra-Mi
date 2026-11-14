@echo off
chcp 65001 >nul
title Subvideo - Dong goi ban gui nguoi khac (kem mo hinh AI)
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Hay chay CaiDat_Windows.bat truoc.
  pause
  exit /b 1
)
if not exist "dist\Subvideo\Subvideo.exe" (
  echo Chua co dist\Subvideo\Subvideo.exe - hay chay DongGoi_EXE.bat truoc.
  pause
  exit /b 1
)
set "OUT=dist\Subvideo_GuiBan"
echo ============================================================
echo   TAO BAN GUI NGUOI KHAC: phan mem + mo hinh AI da tai
echo   Thu muc tao ra: %OUT%
echo   CHI chep mo hinh. KHONG chep: cai dat (API key), tai khoan
echo   YouTube / Facebook, cookie Bilibili, ho so Chrome, giong ghi am,
echo   du an cua ban.
echo   Can trong o dia: ~ dung luong phan mem + mo hinh (10-30 GB).
echo ============================================================
echo.
echo [..] Chep phan mem (bo datadir.txt - may khac dung thu muc data canh exe)...
robocopy "dist\Subvideo" "%OUT%" /E /NFL /NDL /NJH /NJS /NP /XF datadir.txt >nul
if %errorlevel% GEQ 8 (
  echo [LOI] Chep phan mem that bai.
  pause
  exit /b 1
)
echo [..] Chep mo hinh AI da tai tren may nay...
".venv\Scripts\python.exe" TraMi.py --pack-models "%OUT%\data"
if errorlevel 1 (
  echo [LOI] Chep mo hinh that bai - xem thong bao o tren.
  pause
  exit /b 1
)
echo.
echo ============================================================
echo   XONG! Gui nguyen thu muc: %OUT%
echo   (nen bang 7-Zip / WinRAR - file lon nen chia nhieu phan)
echo   May nhan: giai nen, mo Subvideo.exe, mo hinh co san o tab
echo   Tai lieu (khong phai tai lai). Can driver NVIDIA moi.
echo ============================================================
pause
