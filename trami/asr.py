"""Nhận dạng giọng nói tiếng Trung bằng faster-whisper (hỗ trợ video dài, GPU/CPU)."""
from __future__ import annotations

import re
import threading
from pathlib import Path
from typing import Callable

from .config import MODELS_DIR
from .ffmpeg import Cancelled
from .subtitle import Segment

_model_cache: dict = {}
_model_lock = threading.Lock()

ZH_PROMPT = "以下是普通话的对白，请使用简体中文并加上标点符号。"

# Các câu "ảo giác" Whisper hay sinh ra với tiếng Trung
_HALLUCINATIONS = [
    "字幕由", "字幕提供", "请不吝点赞", "订阅我的频道", "点赞订阅", "打赏支持", "明镜与点点", "Amara.org",
    "谢谢观看", "感谢观看", "字幕组", "本字幕", "YoYo Television",
]

_BREAK_PUNCT = "。！？!?；;…"
_SOFT_PUNCT = "，,、：:"


def _add_cuda_dll_paths():
    """Windows: nạp DLL cuBLAS/cuDNN cài qua pip (nvidia-cublas-cu12, nvidia-cudnn-cu12) để Whisper chạy GPU."""
    import os
    import sys

    if sys.platform != "win32":
        return
    try:
        import importlib.util

        for pkg in ("nvidia.cublas", "nvidia.cudnn", "nvidia.cuda_runtime"):
            spec = importlib.util.find_spec(pkg)
            if spec and spec.submodule_search_locations:
                for loc in spec.submodule_search_locations:
                    b = os.path.join(loc, "bin")
                    if os.path.isdir(b):
                        os.add_dll_directory(b)
                        os.environ["PATH"] = b + os.pathsep + os.environ.get("PATH", "")
    except Exception:
        pass


_add_cuda_dll_paths()


def detect_device(pref: str = "auto", compute: str = "auto") -> tuple[str, str]:
    device = pref
    if pref == "auto":
        try:
            import ctranslate2

            device = "cuda" if ctranslate2.get_cuda_device_count() > 0 else "cpu"
        except Exception:
            device = "cpu"
    if compute == "auto":
        compute = "float16" if device == "cuda" else "int8"
    return device, compute


def get_model(name: str, device: str = "auto", compute: str = "auto"):
    from faster_whisper import WhisperModel

    device, compute = detect_device(device, compute)
    key = (name, device, compute)
    with _model_lock:
        if key not in _model_cache:
            _model_cache.clear()  # chỉ giữ 1 model trong RAM
            try:
                _model_cache[key] = WhisperModel(name, device=device, compute_type=compute,
                                                 download_root=str(MODELS_DIR))
            except Exception:
                if device == "cuda":  # thiếu CUDA/cuDNN -> quay về CPU
                    key = (name, "cpu", "int8")
                    _model_cache[key] = WhisperModel(name, device="cpu", compute_type="int8",
                                                     download_root=str(MODELS_DIR))
                else:
                    raise
        return _model_cache[key]


_cc = None


def to_simplified(text: str) -> str:
    """Chuyển chữ Phồn thể -> Giản thể (dịch chính xác hơn)."""
    global _cc
    try:
        if _cc is None:
            from opencc import OpenCC

            _cc = OpenCC("t2s")
        return _cc.convert(text)
    except Exception:
        return text


def is_hallucination(text: str) -> bool:
    t = text.strip()
    if not t:
        return True
    if any(h in t for h in _HALLUCINATIONS) and len(t) < 30:
        return True
    # lặp ký tự/cụm từ quá nhiều: "啊啊啊啊啊啊啊啊" hoặc "好的好的好的好的"
    if re.search(r"(.{1,4})\1{5,}", t):
        return True
    return False


def _clean_repeats(t: str) -> str:
    return re.sub(r"(.{1,6}?)\1{3,}", r"\1\1", t)


def resegment(words: list[dict], max_chars: int = 22, max_dur: float = 7.0, max_gap: float = 0.6) -> list[Segment]:
    """Gộp các từ (word timestamps) thành dòng phụ đề ngắn, ngắt theo dấu câu / độ dài / khoảng lặng."""
    segs: list[Segment] = []
    cur: list[dict] = []

    def flush():
        if not cur:
            return
        text = "".join(w["word"] for w in cur).strip()
        text = text.strip(" ，,、")
        if text:
            segs.append(Segment(cur[0]["start"], cur[-1]["end"], text))
        cur.clear()

    for w in words:
        wt = w["word"]
        if cur:
            gap = w["start"] - cur[-1]["end"]
            cur_len = len("".join(x["word"] for x in cur).strip())
            if gap > max_gap or cur_len + len(wt.strip()) > max_chars or w["end"] - cur[0]["start"] > max_dur:
                flush()
        cur.append(w)
        s = wt.strip()
        if s and s[-1] in _BREAK_PUNCT:
            flush()
        elif s and s[-1] in _SOFT_PUNCT:
            cur_len = len("".join(x["word"] for x in cur).strip())
            if cur_len >= max_chars * 0.55:
                flush()
    flush()
    return segs


def whisper_decoder(settings: dict, language: str = "zh") -> Callable:
    """Hàm nghe 1 đoạn ngắn (numpy 16 kHz) bằng Whisper – dùng để nghe lại các đoạn bị bỏ sót."""
    model = get_model(settings.get("asr_model", "large-v3"), settings.get("asr_device", "auto"),
                      settings.get("asr_compute_type", "auto"))
    lang = None if language in ("auto", "", None) else language
    zh = (lang or "zh").startswith("zh")

    def decode(clip) -> tuple[str, list[str]]:
        if len(clip) < 3200:
            return "", []
        segs, _ = model.transcribe(clip, language=lang, task="transcribe",
                                   beam_size=int(settings.get("asr_beam_size", 5)), vad_filter=False,
                                   condition_on_previous_text=False, initial_prompt=ZH_PROMPT if zh else None,
                                   no_speech_threshold=0.6)
        parts = [s.text.strip() for s in segs if getattr(s, "no_speech_prob", 0) < 0.6]
        text = ("" if zh else " ").join(parts)
        return _clean_repeats(to_simplified(text) if zh else text), []
    return decode


def transcribe(audio_path: str | Path, settings: dict, language: str = "zh",
               progress: Callable[[float, str], None] | None = None,
               cancel: Callable[[], bool] | None = None, duration: float = 0.0) -> list[Segment]:
    model_name = settings.get("asr_model", "large-v3")
    if progress:
        progress(0.0, f"Đang nạp Whisper {model_name} (chưa có thì tự tải – xem tab Tài liệu)…")
    from .progress_hook import tqdm_progress

    with tqdm_progress(progress, 0.0, 0.02, "Đang nạp Whisper", cancel, download=f"Đang tải Whisper {model_name}"):
        model = get_model(model_name, settings.get("asr_device", "auto"), settings.get("asr_compute_type", "auto"))
    lang = None if language in ("auto", "", None) else language
    seg_iter, info = model.transcribe(
        str(audio_path),
        language=lang,
        task="transcribe",
        beam_size=int(settings.get("asr_beam_size", 5)),
        best_of=5,
        temperature=[0.0, 0.2, 0.4, 0.6],
        vad_filter=bool(settings.get("asr_vad", True)),
        vad_parameters={"min_silence_duration_ms": 400, "speech_pad_ms": 250},
        word_timestamps=True,
        condition_on_previous_text=False,
        initial_prompt=ZH_PROMPT if (lang or "zh").startswith("zh") else None,
        no_speech_threshold=0.6,
        compression_ratio_threshold=2.2,
        hallucination_silence_threshold=2.0,
    )
    total = duration or getattr(info, "duration", 0) or 1
    words: list[dict] = []
    fallback_segs: list[Segment] = []
    is_zh = (lang or getattr(info, "language", "zh") or "zh").startswith("zh")
    max_chars = int(settings.get("asr_max_chars", 22)) if is_zh else 80
    for seg in seg_iter:
        if cancel and cancel():
            raise Cancelled()
        text = seg.text.strip()
        if is_zh:
            text = to_simplified(text)
        if is_hallucination(text) or getattr(seg, "no_speech_prob", 0) > 0.85:
            continue
        if seg.words:
            for w in seg.words:
                wt = to_simplified(w.word) if is_zh else w.word
                words.append({"start": float(w.start), "end": float(w.end), "word": wt})
        else:
            fallback_segs.append(Segment(float(seg.start), float(seg.end), _clean_repeats(text)))
        if progress:
            progress(min(0.999, seg.end / total), f"Nhận dạng giọng nói {int(seg.end)}/{int(total)} giây")
    if words:
        if not is_zh:
            for w in words:
                if not w["word"].startswith(" "):
                    w["word"] = " " + w["word"]
        segs = resegment(words, max_chars=max_chars)
    else:
        segs = fallback_segs
    for s in segs:
        s.text = _clean_repeats(s.text.strip())
    segs = [s for s in segs if not is_hallucination(s.text)]
    if progress:
        progress(1.0, f"Nhận dạng xong {len(segs)} câu")
    return segs
