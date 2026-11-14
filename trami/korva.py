"""Bộ đọc KorvaTTS (2.4.16, dogenthq/KorvaTTS – Apache-2.0 cả mã lẫn mô hình, gói `korvatts`).

Mô hình 99M flow matching chạy ONNX Runtime trên CPU (không chiếm card đồ hoạ), 44,1 kHz, 10 giọng Việt dựng sẵn (5 nữ,
5 nam), đọc tự nhiên từ tiếng Anh xen trong câu Việt. Không nhái giọng. Mô hình không tự quyết lúc dừng (độ dài đoán
trước) -> không đọc lặp lời như bộ đọc tự hồi quy.

Chạy NGAY TRONG tiến trình tool (ONNX trên CPU, nhẹ – không cần tiến trình riêng như VieNeu / OmniVoice): 1 bộ đọc dùng
chung, khoá 1 câu mỗi lúc; để không `IDLE_FREE` giây thì bỏ (gpu.reap_idle) – trả ~0,5 GB RAM.
Mô hình: kho HuggingFace "dogenthq/KorvaTTS" (onnx/* + voice_styles/*) tải ở tab Tài liệu vào kho HF của máy; nạp
thẳng thư mục đó (assets_dir) – không hỏi mạng lúc dùng."""
from __future__ import annotations

import threading
import time
from pathlib import Path

REPO = "dogenthq/KorvaTTS"
FILES = ["onnx/*", "voice_styles/*"]
PREFIX = "korva:"
SR = 44100
IDLE_FREE = 300.0
QUALITY = {"fast": 12, "normal": 20, "best": 32}     # số bước flow matching mỗi câu (gói mặc định 32 = tốt nhất)
DEFAULT_SPEED = 1.05                                  # gói tự đặt 1,05 (nghe tự nhiên hơn tốc độ thô)

# (mã giọng của gói, tên hiện, nam / nữ)
VOICES = [
    ("khanh_vy", "Khánh Vy", "female"), ("ngoc_huyen", "Ngọc Huyền", "female"), ("bao_kim", "Bảo Kim", "female"),
    ("phuong_linh", "Phương Linh", "female"), ("quynh_nhu", "Quỳnh Như", "female"),
    ("hoang_nam", "Hoàng Nam", "male"), ("gia_bao", "Gia Bảo", "male"), ("huu_dat", "Hữu Đạt", "male"),
    ("quang_huy", "Quang Huy", "male"), ("thanh_phong", "Thanh Phong", "male"),
]
ONNX_FILES = ("duration_predictor.onnx", "text_encoder.onnx", "vector_estimator.onnx", "vocoder.onnx", "tts.json",
              "unicode_indexer.json")

_lock = threading.Lock()
_tts = None
_dir: Path | None = None
_last = 0.0


def installed() -> bool:
    from .config import installed as inst

    return inst("korvatts", "onnxruntime", "soundfile")


def assets_dir() -> Path | None:
    """Thư mục mô hình đã tải (kho HF trên máy) có đủ file ONNX, None = chưa tải."""
    from .models import _hf_snapshot

    snap = _hf_snapshot(REPO)
    if snap and all((snap / "onnx" / f).is_file() for f in ONNX_FILES) and any((snap / "voice_styles").glob("*.json")):
        return snap
    return None


def ready() -> bool:
    return installed() and assets_dir() is not None


def available() -> bool:
    """Có thể chọn giọng KorvaTTS (đã cài gói; mô hình chưa tải thì báo lúc đọc)."""
    return installed()


def voices() -> list[dict]:
    have = set()
    d = assets_dir()
    if d:
        have = {p.stem for p in (d / "voice_styles").glob("*.json")}
    out = []
    for vid, name, g in VOICES:
        if have and vid not in have:
            continue
        out.append({"id": PREFIX + vid, "name": f"{name} – {'nữ' if g == 'female' else 'nam'} (KorvaTTS, CPU)",
                    "gender": g})
    for vid in sorted(have - {v for v, _n, _g in VOICES}):     # giọng mới của bản mô hình sau
        out.append({"id": PREFIX + vid, "name": f"{vid} (KorvaTTS, CPU)", "gender": ""})
    return out


def gender_of(spec: str) -> str:
    return next((g for v, _n, g in VOICES if PREFIX + v == spec), "")


def opts(settings: dict | None) -> tuple[int, float]:
    st = settings or {}
    steps = QUALITY.get(str(st.get("korva_quality", "normal")), 20)
    try:
        speed = min(1.6, max(0.7, float(st.get("korva_speed", DEFAULT_SPEED) or DEFAULT_SPEED)))
    except (TypeError, ValueError):
        speed = DEFAULT_SPEED
    return steps, speed


def _get():
    global _tts, _dir
    d = assets_dir()
    if d is None:
        raise RuntimeError("Chưa tải mô hình KorvaTTS – mở tab Tài liệu, bấm Tải về ở mục \"Giọng đọc – KorvaTTS\" "
                           "(~0,4 GB)")
    if _tts is None or _dir != d:
        from korvatts import TTS

        _tts = TTS(assets_dir=d, device="cpu", auto_download=False)
        _dir = d
    return _tts


class KorvaWorker:
    """Cùng cách gọi với tiến trình đọc giọng (synth(text, out, …)) để dùng chung tts.synth_checked."""

    title = "KorvaTTS"
    sr = SR

    def __init__(self, settings: dict | None = None):
        self.settings = settings or {}

    def ensure_started(self, progress=None, cancel=None) -> None:
        with _lock:
            if progress:
                progress(0.0, "Đang nạp KorvaTTS (CPU)…")
            _get()

    def synth(self, text: str, out, voice: str = "", seed: int | None = None, steps: int | None = None,
              speed: float | None = None, **_kw) -> Path:
        global _last
        from .tts import write_wav

        st, sp = opts(self.settings)
        name = (voice or "").removeprefix(PREFIX) or VOICES[0][0]
        with _lock:
            eng = _get()
            wav, _dur = eng.synthesize(text, voice=name, lang="vi", total_steps=int(steps or st),
                                       speed=float(speed or sp), seed=seed)
            sr = int(getattr(eng, "sample_rate", SR) or SR)
            _last = time.time()
        write_wav(out, wav, sr)
        return Path(out)


def release_idle(idle: float = IDLE_FREE) -> bool:
    """Bỏ bộ đọc để không quá `idle` giây (gpu.reap_idle). Đang đọc thì để nguyên."""
    global _tts, _dir
    if _tts is None or time.time() - _last < idle:
        return False
    if not _lock.acquire(blocking=False):
        return False
    try:
        _tts, _dir = None, None
    finally:
        _lock.release()
    return True
