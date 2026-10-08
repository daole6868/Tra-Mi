"""Nhận dạng tiếng Trung bằng FunASR (Alibaba):

  - SenseVoice-Small : rất nhanh, giỏi Phổ thông + Quảng Đông, kèm nhãn sự kiện (nhạc nền, đang hát, cười…)
  - Paraformer-zh    : có dấu câu, mốc thời gian từng câu và PHÂN BIỆT NGƯỜI NÓI (CAM++) sẵn trong 1 bước

Mô hình tải tự động từ ModelScope (máy chủ ở Trung Quốc) vào data/models/funasr.
"""
from __future__ import annotations

import os
import re
import threading
from pathlib import Path
from typing import Callable

import numpy as np

from .config import MODELS_DIR
from .ffmpeg import Cancelled
from .subtitle import Segment

_cache: dict = {}
_lock = threading.Lock()
SR = 16000
_TAG = re.compile(r"<\|([^|]*)\|>")
_SPLIT = re.compile(r"(?<=[。！？!?；;…])")


def available() -> bool:
    from .config import installed

    return installed("funasr", "torch")


def _device(pref: str = "auto") -> str:
    if pref == "cpu":
        return "cpu"
    try:
        import torch

        return "cuda:0" if torch.cuda.is_available() else "cpu"
    except Exception:
        return "cpu"


def _model(key: str, **kw):
    from funasr import AutoModel

    os.environ.setdefault("MODELSCOPE_CACHE", str(MODELS_DIR / "funasr"))
    with _lock:
        if key not in _cache:
            _cache[key] = AutoModel(disable_update=True, log_level="ERROR", **kw)
        return _cache[key]


def load_wav16k(path: str | Path) -> np.ndarray:
    import subprocess

    from . import ffmpeg as ff

    raw = subprocess.run([ff.ffmpeg_bin(), "-hide_banner", "-loglevel", "error", "-i", str(path), "-ac", "1",
                          "-ar", str(SR), "-f", "s16le", "pipe:1"], capture_output=True,
                         creationflags=ff._NO_WINDOW).stdout
    return np.frombuffer(raw, np.int16).astype(np.float32) / 32768.0


def parse_sensevoice(text: str) -> tuple[str, list[str]]:
    """'<|zh|><|NEUTRAL|><|BGM|><|withitn|>你好。' -> ('你好。', ['zh','NEUTRAL','BGM','withitn'])"""
    tags = _TAG.findall(text)
    return _TAG.sub("", text).strip(), tags


def split_long(start: float, end: float, text: str, max_chars: int) -> list[tuple[float, float, str]]:
    """Chia đoạn dài theo dấu câu, chia thời gian theo tỉ lệ số chữ."""
    parts = [p for p in _SPLIT.split(text) if p.strip()]
    out: list[str] = []
    for p in parts:
        while len(p) > max_chars:
            cut = max((p.rfind(c, 0, max_chars) for c in "，,、"), default=-1)
            cut = cut + 1 if cut > max_chars // 3 else max_chars
            out.append(p[:cut])
            p = p[cut:]
        if p.strip():
            out.append(p)
    total = sum(len(p) for p in out) or 1
    res, t = [], start
    for p in out:
        d = (end - start) * len(p) / total
        res.append((t, t + d, p.strip(" ，,")))
        t += d
    return res


def vad_segments(audio: np.ndarray, device: str) -> list[tuple[float, float]]:
    vad = _model("fsmn-vad", model="fsmn-vad", device=device, max_single_segment_time=20000)
    res = vad.generate(input=audio, fs=SR)
    spans = (res[0].get("value") if res else None) or []
    return [(a / 1000.0, b / 1000.0) for a, b in spans if b > a]


def transcribe_sensevoice(audio_path: str | Path, settings: dict, language: str = "zh",
                          progress: Callable[[float, str], None] | None = None, cancel=None) -> list[Segment]:
    from .asr import is_hallucination, to_simplified

    dev = _device(settings.get("asr_device", "auto"))
    if progress:
        progress(0.0, "Đang nạp SenseVoice + chia đoạn giọng nói…")
    from .progress_hook import tqdm_progress

    audio = load_wav16k(audio_path)
    with tqdm_progress(progress, 0.0, 0.05, "Đang chia đoạn giọng nói", cancel, download="Đang tải SenseVoice + VAD"):
        spans = vad_segments(audio, dev)
        model = _model("sensevoice", model="iic/SenseVoiceSmall", device=dev)
    lang = {"zh": "zh", "yue": "yue", "en": "en", "ja": "ja", "ko": "ko"}.get(language, "auto")
    max_chars = int(settings.get("asr_max_chars", 22))
    segs: list[Segment] = []
    batch = 16
    for b in range(0, len(spans), batch):
        if cancel and cancel():
            raise Cancelled()
        chunk = spans[b:b + batch]
        wavs = [audio[int(s * SR):int(e * SR)] for s, e in chunk]
        res = model.generate(input=wavs, language=lang, use_itn=True, batch_size=len(wavs))
        for (s, e), r in zip(chunk, res):
            text, tags = parse_sensevoice(r.get("text", ""))
            text = to_simplified(text) if lang in ("zh", "auto", "yue") else text
            if not text or is_hallucination(text):
                continue
            for a, z, t in split_long(s, e, text, max_chars):
                seg = Segment(round(a, 3), round(z, 3), t)
                seg.extra["tags"] = [x for x in tags if x not in ("withitn", "woitn")]
                segs.append(seg)
        if progress:
            progress(0.05 + 0.949 * min(1.0, (b + len(chunk)) / max(1, len(spans))),
                     f"SenseVoice đang nhận dạng {b + len(chunk)}/{len(spans)} đoạn")
    mark_songs(segs)
    return segs


def transcribe_paraformer(audio_path: str | Path, settings: dict, progress=None, cancel=None,
                          with_speakers: bool = True) -> list[Segment]:
    """Paraformer-zh + dấu câu + mốc thời gian câu + phân người nói CAM++ (chỉ tiếng Trung)."""
    from .asr import to_simplified

    dev = _device(settings.get("asr_device", "auto"))
    if progress:
        progress(0.02, "Đang nạp Paraformer + CAM++…")
    kw = dict(model="paraformer-zh", vad_model="fsmn-vad", punc_model="ct-punc", device=dev)
    if with_speakers:
        kw["spk_model"] = "cam++"
    from .progress_hook import tqdm_progress

    with tqdm_progress(progress, 0.02, 0.1, "Đang nạp Paraformer", cancel, download="Đang tải Paraformer + CAM++"):
        model = _model("paraformer" + ("_spk" if with_speakers else ""), **kw)
    if cancel and cancel():
        raise Cancelled()
    if progress:
        progress(0.1, "Paraformer đang nhận dạng (video dài sẽ mất vài phút)…")
    with tqdm_progress(progress, 0.1, 0.99, "Paraformer đang nhận dạng", cancel):
        res = model.generate(input=str(audio_path), batch_size_s=300, sentence_timestamp=True)
    max_chars = int(settings.get("asr_max_chars", 22))
    segs: list[Segment] = []
    for r in res or []:
        for si in r.get("sentence_info") or []:
            text = to_simplified((si.get("text") or "").strip())
            if not text:
                continue
            spk = si.get("spk")
            for a, z, t in split_long(si["start"] / 1000.0, si["end"] / 1000.0, text, max_chars):
                seg = Segment(round(a, 3), round(z, 3), t, speaker=f"S{int(spk) + 1}" if spk is not None else "")
                segs.append(seg)
    if progress:
        progress(1.0, f"Nhận dạng xong {len(segs)} câu")
    return segs


def mark_songs(segs: list[Segment]) -> None:
    """Đánh dấu câu đang HÁT: nhãn Sing của SenseVoice, hoặc nhạc nền + nhịp chữ rất chậm (hát kéo dài)."""
    guess = []
    for s in segs:
        tags = s.extra.get("tags") or []
        n = max(1, len(re.sub(r"[^㐀-鿿]", "", s.text)))
        slow = s.duration >= 3.0 and n / max(0.1, s.duration) < 1.6
        if "Sing" in tags:
            s.extra["song"] = True
        elif "BGM" in tags and slow:
            guess.append(s)
    # video có nhạc nền suốt (review phim, TikTok…) -> đoán theo nhạc nền dễ nhầm lời thoại thành bài hát
    if len(guess) <= max(2, 0.25 * len(segs)):
        for s in guess:
            s.extra["song"] = True