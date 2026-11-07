@echo off
chcp 65001 >nul
title Subvideo - Tao bieu tuong
cd /d "%~dp0"
REM Tao bieu tuong "Subvideo" tren Desktop + Start menu: bam dup la mo giao dien (khong cua so den).
REM Da dong goi exe (dist\Subvideo\Subvideo.exe) thi bieu tuong mo file exe, chua thi mo bang .venv\Scripts\pythonw.exe
set "ICO=%~dp0assets\Subvideo.ico"
if exist "%~dp0dist\Subvideo\Subvideo.exe" (
  set "TGT=%~dp0dist\Subvideo\Subvideo.exe"
  set "ARGS="
  set "WD=%~dp0dist\Subvideo"
) else (
  set "TGT=%~dp0.venv\Scripts\pythonw.exe"
  set "ARGS=\"%~dp0TraMi.py\""
  set "WD=%~dp0"
)
powershell -NoProfile -Command "$w=New-Object -ComObject WScript.Shell; foreach($d in @([Environment]::GetFolderPath('Desktop'), [Environment]::GetFolderPath('Programs'))){ $s=$w.CreateShortcut($d+'\Subvideo.lnk'); $s.TargetPath='%TGT%'; $s.Arguments='%ARGS%'; $s.WorkingDirectory='%WD%'; $s.IconLocation='%ICO%'; $s.Description='Subvideo - Dich va long tieng video'; $s.Save() }; $old=[Environment]::GetFolderPath('Desktop')+'\TraMi.lnk'; if(Test-Path $old){Remove-Item $old}" >nul 2>&1
echo [OK] Da tao bieu tuong Subvideo tren Desktop va Start menu.
echo      Muon ghim thanh taskbar: mo Subvideo, bam chuot phai vao bieu tuong duoi taskbar - Ghim vao thanh tac vu.
if /I not "%~1"=="nopause" pause
