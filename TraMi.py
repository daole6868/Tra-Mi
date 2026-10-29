"""Subvideo – dịch & lồng tiếng video.   Chạy:  python TraMi.py   (bản đóng gói: Subvideo.exe)"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

if __name__ == "__main__":
    import multiprocessing

    multiprocessing.freeze_support()   # bản .exe: thư viện nào mở tiến trình con bằng multiprocessing vẫn chạy đúng
    if len(sys.argv) > 1 and sys.argv[1] == "--vieneu-worker":   # tiến trình riêng đọc giọng VieNeu
        from trami.vieneu_worker import main as vn_main

        vn_main(sys.argv[2] if len(sys.argv) > 2 else "v3turbo")
    elif len(sys.argv) > 1 and sys.argv[1] == "--omni-worker":   # tiến trình riêng nhái giọng OmniVoice
        from trami.omni_worker import main as omni_main

        omni_main(sys.argv[2] if len(sys.argv) > 2 else "k2-fsa/OmniVoice")
    elif len(sys.argv) > 1 and sys.argv[1] == "--qwen-asr-worker":   # tiến trình riêng nghe tiếng Trung Qwen3-ASR
        from trami.qwen_asr_worker import main as qa_main

        qa_main(sys.argv[2] if len(sys.argv) > 2 else "Qwen/Qwen3-ASR-1.7B-hf")
    elif len(sys.argv) > 1 and sys.argv[1] == "--firered-worker":   # tiến trình riêng nghe tiếng Trung FireRedASR2
        from trami.firered_worker import main as fr_main

        fr_main(sys.argv[2] if len(sys.argv) > 2 else "FireRedTeam/FireRedASR2-AED")
    elif len(sys.argv) > 1 and sys.argv[1] == "--hymt-worker":   # tiến trình riêng dịch trên máy Hy-MT2
        from trami.hymt_worker import main as mt_main

        mt_main(sys.argv[2] if len(sys.argv) > 2 else "tencent/Hy-MT2-1.8B")
    elif len(sys.argv) > 1 and sys.argv[1] == "--hf-download":   # tiến trình riêng tải mô hình HuggingFace
        from trami.hf_download import child_main

        child_main(sys.argv[2])
    elif len(sys.argv) > 1 and sys.argv[1] == "--pack-models":   # gom mô hình đã tải cho bản mang đi (DongGoi_MoHinh.bat)
        from trami.packmodels import main as pack_main

        sys.exit(pack_main(sys.argv[2:]))
    elif len(sys.argv) > 1:          # có tham số -> chế độ dòng lệnh (VD: python TraMi.py dich video.mp4)
        from trami.config import fix_std_streams

        fix_std_streams()
        from trami.__main__ import main

        main()
    else:
        from trami.config import fix_std_streams

        fix_std_streams()   # mở bằng pythonw (shortcut, không cửa sổ đen) -> log thư viện ra data/logs/console.txt
        from trami.gui.main import run

        sys.exit(run())
