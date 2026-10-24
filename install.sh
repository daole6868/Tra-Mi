#!/usr/bin/env bash
# Cài đặt TraMi trên Linux / macOS.   ./install.sh        (thêm --ai để cài luôn AI nâng cao)
set -e
cd "$(dirname "$0")"
PY=""
for c in python3.11 python3.12 python3.10 python3; do
  if command -v "$c" >/dev/null 2>&1; then PY="$c"; break; fi
done
[ -z "$PY" ] && { echo "Chưa có Python 3.10-3.12. Hãy cài Python rồi chạy lại."; exit 1; }
echo "Dùng $($PY --version)"
[ -x .venv/bin/python ] || "$PY" -m venv .venv
.venv/bin/python -m pip install --upgrade pip wheel setuptools
.venv/bin/python -m pip install -r requirements.txt
if [ "$1" = "--ai" ]; then
  if command -v nvidia-smi >/dev/null 2>&1; then
    .venv/bin/python -m pip install torch torchaudio            # bản PyPI Linux đã kèm CUDA
  else
    .venv/bin/python -m pip install torch torchaudio --index-url https://download.pytorch.org/whl/cpu
  fi
  .venv/bin/python -m pip install -r requirements-ai.txt
fi
if ! command -v ffmpeg >/dev/null 2>&1; then
  echo "Gợi ý: cài FFmpeg đầy đủ:  Ubuntu: sudo apt install ffmpeg   |   macOS: brew install ffmpeg"
fi
chmod +x run.sh
echo "Cài đặt xong! Chạy: ./run.sh"
