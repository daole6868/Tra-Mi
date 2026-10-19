"""TraMi - Dịch video Trung -> Việt.   Chạy:  python TraMi.py"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--vieneu-worker":   # tiến trình riêng đọc giọng VieNeu
        from trami.vieneu_worker import main as vn_main

        vn_main(sys.argv[2] if len(sys.argv) > 2 else "v3turbo")
    elif len(sys.argv) > 1 and sys.argv[1] == "--omni-worker":   # tiến trình riêng nhái giọng OmniVoice
        from trami.omni_worker import main as omni_main

        omni_main(sys.argv[2] if len(sys.argv) > 2 else "k2-fsa/OmniVoice")
    elif len(sys.argv) > 1 and sys.argv[1] == "--qwen-asr-worker":   # tiến trình riêng nghe tiếng Trung Qwen3-ASR
        from trami.qwen_asr_worker import main as qa_main

        qa_main(sys.argv[2] if len(sys.argv) > 2 else "Qwen/Qwen3-ASR-1.7B-hf")
    elif len(sys.argv) > 1:          # có tham số -> chế độ dòng lệnh (VD: python TraMi.py dich video.mp4)
        from trami.__main__ import main

        main()
    else:
        from trami.gui.main import run

        sys.exit(run())
