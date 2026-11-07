@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Chua cai dat. Dang chay CaiDat_Windows.bat ...
  call CaiDat_Windows.bat
)
REM pythonw = mo giao dien khong kem cua so den. Muon xem log loi: dung ChayTool_XemLog.bat
start "" ".venv\Scripts\pythonw.exe" "%~dp0TraMi.py"
