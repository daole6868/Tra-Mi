@echo off
chcp 65001 >nul
title TraMi - Cai dat AI nang cao
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Hay chay CaiDat_Windows.bat truoc.
  pause
  exit /b 1
)
set "VPY=.venv\Scripts\python.exe"
echo ============================================================
echo   Cai AI nang cao: PyTorch + tach giong + SenseVoice +
echo   phan biet nguoi noi + VieNeu-TTS + LaMa
echo ============================================================

REM ---- 0. Microsoft Visual C++ Redistributable (PyTorch bat buoc can, thieu se loi WinError 1114 / c10.dll)
echo [..] Cai / cap nhat Microsoft Visual C++ Redistributable...
winget install -e --id Microsoft.VCRedist.2015+.x64 --accept-package-agreements --accept-source-agreements --silent >nul 2>&1
if errorlevel 1 (
  powershell -NoProfile -Command "$f=Join-Path $env:TEMP 'vc_redist.x64.exe'; Invoke-WebRequest 'https://aka.ms/vs/17/release/vc_redist.x64.exe' -OutFile $f; Start-Process $f -ArgumentList '/install','/quiet','/norestart' -Wait"
)

REM ---- 1. PyTorch: co card NVIDIA -> ban CUDA, khong -> ban CPU
set "TORCH_OK="
"%VPY%" -m pip uninstall -y torch torchvision torchaudio >nul 2>&1
where nvidia-smi >nul 2>&1
if %errorlevel%==0 (
  echo [OK] Tim thay card NVIDIA:
  nvidia-smi --query-gpu=name,memory.total,driver_version --format=csv,noheader
  for %%C in (cu128 cu126 cu130 cu124) do (
    if not defined TORCH_OK (
      echo [..] Thu cai PyTorch %%C ...
      "%VPY%" -m pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/%%C && set "TORCH_OK=%%C"
    )
  )
)
if not defined TORCH_OK (
  echo [..] Cai PyTorch ban CPU ...
  "%VPY%" -m pip install torch torchvision torchaudio
)

REM ---- 2. Cac goi AI
echo [..] Dang cai cac goi AI (co the mat 10-20 phut)...
REM Khoa phien ban PyTorch vua cai de cac goi AI KHONG duoc thay no bang ban CPU tu PyPI
"%VPY%" -m pip freeze | findstr /B /C:"torch==" /C:"torchvision==" /C:"torchaudio==" > torch-constraints.txt
type torch-constraints.txt
"%VPY%" -m pip install -r requirements-ai.txt -c torch-constraints.txt
if errorlevel 1 (
  echo [!] Mot so goi AI cai loi. Tool van chay duoc, cac tinh nang thieu se tu dung phuong an thay the.
)

REM ---- 3. Thu vien CUDA cho Whisper (faster-whisper) neu co GPU
if defined TORCH_OK (
  "%VPY%" -m pip install nvidia-cublas-cu12 "nvidia-cudnn-cu12>=9,<10"
)

echo.
echo ---------------- KIEM TRA ----------------
"%VPY%" -c "import torch;print('PyTorch', torch.__version__, '| GPU:', torch.cuda.get_device_name(0) if torch.cuda.is_available() else 'KHONG (chay CPU)')"
"%VPY%" -c "import trami.separate as a, trami.asr_funasr as b, trami.diarize as c, trami.tts as d, trami.lama as e; print('Tach giong:', a.available(), '| SenseVoice:', b.available(), '| Phan nguoi noi:', c.available(), '| VieNeu-TTS:', d.vieneu_available(), '| LaMa:', e.available())"
echo.
echo Cac mo hinh AI se tu tai ve o lan dung dau tien (tong khoang 3-5 GB).
if not "%1"=="nopause" pause
