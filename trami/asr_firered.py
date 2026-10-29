"""Nghe tiếng Trung bằng FireRedASR2-AED (Xiaohongshu, 2/2026, Apache-2.0) – bộ nghe thứ 2 cạnh Qwen3-ASR.

Vì sao: bảng đo của nhóm FireRed (README FireRedASR2S): lỗi chữ tiếng Phổ thông trung bình 3,05 % (Qwen3-ASR-1.7B 3,76 %),
giọng địa phương 11,67 % (11,85 %), lời hát opencpop 1,17 % (2,57 %); có điểm tin cậy + mốc từng chữ (nhánh CTC). Kiến
trúc khác hẳn Qwen3 (mã hoá–giải mã thuần âm thanh, không có mô hình ngôn ngữ lớn) -> chỗ 2 bộ nghe khác nhau thường là
chỗ khó nghe thật -> "Nghe 2 bộ rồi so" (pipeline.compare_listen).

Chạy trong tiến trình riêng (trami/firered_worker.py, `TraMi.py --firered-worker`), giao thức như Qwen3-ASR nên
asr_qwen.transcribe_qwen / relisten_cuts / align_qwen dùng chung (tham số engine="firered"). Mô hình tải ở tab Tài liệu
(HuggingFace FireRedTeam/FireRedASR2-AED + FireRedPunc, lỗi thì ModelScope của tác giả).
"""
from __future__ import annotations

from .asr_qwen import QwenAsrWorker

LANGS = ("zh", "yue", "auto", "en")   # FireRedASR2: tiếng Phổ thông + 20 giọng địa phương (cả Quảng Đông) + tiếng Anh


def available() -> bool:
    """Đã cài PyTorch (không import). transformers chỉ cần cho bộ đặt dấu câu."""
    from .config import installed

    return installed("torch")


def ready() -> bool:
    from .models import firered_ready

    return available() and firered_ready()


def why_unavailable() -> str:
    if not available():
        return "chưa cài PyTorch"
    return "chưa tải mô hình – tab Tài liệu → FireRedASR2 → Tải về"


class FireRedWorker(QwenAsrWorker):
    title = "FireRedASR2"
    flag = "--firered-worker"
    log_name = "firered_asr.txt"


_worker: FireRedWorker | None = None


def worker(settings: dict | None = None) -> FireRedWorker:
    global _worker
    from .models import FIRERED_REPO

    if _worker is None:
        _worker = FireRedWorker(FIRERED_REPO, (settings or {}).get("hf_token", ""))
        import atexit

        atexit.register(release)
    return _worker


def release() -> None:
    """Tắt tiến trình FireRedASR2 – trả bộ nhớ GPU cho bước sau."""
    global _worker
    if _worker is not None:
        _worker.stop()
        _worker = None
