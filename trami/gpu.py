"""Trả bộ nhớ card đồ hoạ (GPU) giữa các việc.

Mô hình AI chạy ở 2 nơi:
  * ngay trong tiến trình giao diện: tách giọng (audio-separator), chia đoạn giọng nói (FunASR VAD), căn từng chữ fa-zh,
    SenseVoice / Paraformer, Whisper, phân biệt người nói (ECAPA), xoá phụ đề (LaMa). PyTorch GIỮ bộ nhớ đã dùng (bộ
    nhớ đệm của CUDA) cho tới khi được bảo trả lại -> tách giọng xong vẫn chiếm vài GB;
  * ở tiến trình riêng: Qwen3-ASR, VieNeu, OmniVoice, Hy-MT2 – sống tới khi tắt -> giữ nguyên mô hình trên card.
Card hết chỗ thì Windows lấy RAM thay (chậm đi nhiều lần). Vì vậy: trước mỗi việc GPU tắt các tiến trình việc đó không
dùng, sau mỗi việc GPU bỏ mô hình đã nạp trong tiến trình giao diện + trả bộ nhớ đệm; tiến trình đọc giọng để không quá
IDLE_STOP giây thì tự tắt.
"""
from __future__ import annotations

import gc
import sys
import threading
import time

IDLE_STOP = 600.0            # tiến trình đọc giọng (VieNeu / OmniVoice) không dùng 10 phút -> tắt, trả card
TTS_KINDS = {"dub", "redub"}  # việc dùng tiến trình đọc giọng


def _torch_cuda():
    t = sys.modules.get("torch")   # chỉ khi đã nạp torch – không tự nạp (mất vài giây, tạo CUDA)
    try:
        if t is not None and t.cuda.is_available() and t.cuda.is_initialized():
            return t
    except Exception:  # noqa: BLE001
        pass
    return None


def free_cache() -> None:
    """Trả bộ nhớ đệm CUDA của tiến trình giao diện (phần mô hình đã bỏ, tensor tạm)."""
    gc.collect()
    t = _torch_cuda()
    if t is not None:
        try:
            t.cuda.synchronize()
            t.cuda.empty_cache()
            t.cuda.ipc_collect()
        except Exception:  # noqa: BLE001
            pass


def drop_local_models() -> None:
    """Bỏ các mô hình đã nạp trong tiến trình giao diện (lần sau dùng nạp lại từ ổ đĩa, vài giây)."""
    for mod, names in (("trami.lama", ("_model",)), ("trami.diarize", ("_enc",)), ("trami.align", ("_fa_model",))):
        m = sys.modules.get(mod)
        for n in names:
            if m is not None and getattr(m, n, None) is not None:
                setattr(m, n, None)
    for mod, name in (("trami.asr_funasr", "_cache"), ("trami.asr", "_model_cache")):
        m = sys.modules.get(mod)
        c = getattr(m, name, None) if m is not None else None
        if isinstance(c, dict):
            c.clear()
    free_cache()


def _workers(kinds: set[str] | None = None):
    """(loại, tiến trình) đang có: tts (VieNeu / OmniVoice), asr (Qwen3), mt (Hy-MT2)."""
    out = []
    tts = sys.modules.get("trami.tts")
    if tts is not None:
        for w in (getattr(tts, "_vn_model", None), getattr(tts, "_omni_model", None)):
            if w is not None:
                out.append(("tts", w))
    q = sys.modules.get("trami.asr_qwen")
    if q is not None and getattr(q, "_worker", None) is not None:
        out.append(("asr", q._worker))
    tr = sys.modules.get("trami.translate")
    if tr is not None and getattr(tr, "_hymt", None) is not None:
        out.append(("mt", tr._hymt))
    return [(k, w) for k, w in out if kinds is None or k in kinds]


def _stop_if_idle(w) -> bool:
    """Tắt tiến trình nếu nó không đang đọc / nghe dở (đang dùng thì để nguyên)."""
    if not getattr(w, "proc", None) or w.proc.poll() is not None:
        return False
    lock = getattr(w, "lock", None)
    if lock is not None and not lock.acquire(blocking=False):
        return False
    try:
        w.stop()
        return True
    finally:
        if lock is not None:
            lock.release()


def stop_workers(kinds: set[str] | None = None) -> int:
    return sum(_stop_if_idle(w) for _, w in _workers(kinds))


def before_job(kind: str) -> None:
    """Trước 1 việc GPU: tắt tiến trình AI mà việc này không dùng -> việc này có đủ chỗ trên card."""
    if kind in TTS_KINDS:
        stop_workers({"asr", "mt"})
    else:
        stop_workers({"tts", "mt"} if kind != "translate" else {"tts", "asr"})
    drop_local_models()   # LaMa của "Xem thử 1 khung", ECAPA, SenseVoice… còn nằm trên card từ lần trước


def after_job(kind: str) -> None:
    """Sau 1 việc GPU: bỏ mô hình nạp trong tiến trình giao diện + trả bộ nhớ đệm (tiến trình đọc giọng giữ lại để
    'Tạo lại giọng' vài câu không phải nạp lại – quá IDLE_STOP giây không dùng thì tự tắt)."""
    drop_local_models()


def reap_idle(max_idle: float = IDLE_STOP) -> int:
    n = 0
    now = time.time()
    for _, w in _workers({"tts"}):
        if getattr(w, "proc", None) and w.proc.poll() is None and now - getattr(w, "last_used", now) > max_idle:
            n += _stop_if_idle(w)
    return n


_reaper: threading.Thread | None = None


def start_reaper(busy=lambda: False, every: float = 30.0) -> None:
    """Luồng nền: tắt tiến trình đọc giọng để không quá lâu (không làm khi đang có việc GPU chạy)."""
    global _reaper
    if _reaper is not None:
        return

    def loop():
        while True:
            time.sleep(every)
            try:
                if not busy():
                    reap_idle()
            except Exception:  # noqa: BLE001
                pass

    _reaper = threading.Thread(target=loop, daemon=True, name="trami-gpu-reaper")
    _reaper.start()
