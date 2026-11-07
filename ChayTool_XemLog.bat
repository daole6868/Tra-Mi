@echo off
chcp 65001 >nul
title Subvideo - che do xem log
cd /d "%~dp0"
REM Chay tool kem cua so den hien thong bao/loi - dung khi can bao loi
".venv\Scripts\python.exe" TraMi.py
pause
