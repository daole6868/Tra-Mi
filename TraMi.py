"""TraMi - Dịch video Trung -> Việt.   Chạy:  python TraMi.py"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

if __name__ == "__main__":
    if len(sys.argv) > 1:          # có tham số -> chế độ dòng lệnh (VD: python TraMi.py dich video.mp4)
        from trami.__main__ import main

        main()
    else:
        from trami.gui.main import run

        sys.exit(run())
