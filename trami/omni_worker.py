"""Tiến trình riêng chạy OmniVoice (nhái giọng từ file ghi âm mẫu). Cùng giao thức với vieneu_worker.

  vào : {"text": "...", "out": "x.wav", "ref_audio": "mau.wav", "ref_text": "lời trong file mẫu" | ""}
  ra  : @@TRAMI@@{"ok": true, "sr": 24000} | {"error": "..."} | {"progress": 0.5, "msg": "..."}
"""
from __future__ import annotations

import json
import os
import sys

from .vieneu_worker import _reply, _seed

# OmniVoice định trước độ dài file theo số chữ rồi mới đọc; câu ước lượng < 50 khung (~2 s) bị kéo dài thêm theo hàm mũ
# 1/3 -> "Được thôi" (~0,56 s) thành 1,3 s, mô hình đọc lặp lời cho đủ ("được thôi được thôi"). Giữ phần nới cho câu
# ngắn nhưng nhẹ hơn hẳn: chỉ câu < 20 khung (~0,8 s), căn bậc 2 (≤ ×1,25 với câu ~0,5 s)
SHORT_TOKENS = 20
SHORT_BOOST = 2.0


def fix_short_duration(model) -> bool:
    """Thay mức nới độ dài câu ngắn của bộ ước lượng độ dài OmniVoice (`RuleDurationEstimator.estimate_duration`).
    Bản OmniVoice không có 2 tham số này -> để nguyên. Trả True khi đã thay."""
    import inspect

    est = getattr(model, "duration_estimator", None)
    fn = getattr(est, "estimate_duration", None)
    if fn is None or getattr(est, "_trami_short", False):
        return False
    try:
        params = inspect.signature(fn).parameters
    except (TypeError, ValueError):
        return False
    if "low_threshold" not in params or "boost_strength" not in params:
        return False

    def estimate_duration(target_text, ref_text, ref_duration, low_threshold=SHORT_TOKENS,
                          boost_strength=SHORT_BOOST):
        return fn(target_text, ref_text, ref_duration, low_threshold, boost_strength)

    est.estimate_duration = estimate_duration
    est._trami_short = True
    return True


def main(repo: str = "k2-fsa/OmniVoice") -> None:
    proto = sys.stdout
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w", encoding="utf-8")
    sys.stdout = sys.stderr
    try:
        proto.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
        sys.stdin.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:
        pass

    def prog(x: float, msg: str):
        _reply(proto, {"progress": round(x, 4), "msg": msg})

    try:
        import torch
        from omnivoice import OmniVoice

        from .progress_hook import tqdm_progress
        from .tts import write_wav

        if torch.cuda.is_available():
            kw = {"device_map": "cuda:0", "dtype": torch.float16}
        else:
            kw = {"device_map": "cpu", "dtype": torch.float32}
        with tqdm_progress(prog, 0.0, 1.0, "Đang nạp OmniVoice", download="Đang tải OmniVoice"):
            model = OmniVoice.from_pretrained(repo, **kw)
        sr = int(getattr(model, "sampling_rate", None) or 24000)
        fix_short_duration(model)
    except Exception as e:  # noqa: BLE001
        _reply(proto, {"error": f"Không nạp được OmniVoice: {type(e).__name__}: {e}"})
        return
    _reply(proto, {"ready": True, "sr": sr})

    prompts: dict = {}   # (file mẫu, lời) -> VoiceClonePrompt, mã hoá giọng mẫu 1 lần dùng cho mọi câu
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
            gen = {"text": req["text"], "language": req.get("language") or "vi"}   # vi / en / th / ja / ko
            ref = req.get("ref_audio")
            if ref:
                key = (ref, req.get("ref_text") or "")
                if key not in prompts:
                    if not key[1] and getattr(model, "_asr_pipe", None) is None:
                        # chưa có lời của file mẫu -> OmniVoice tự nghe bằng Whisper (lần đầu tải ~1,6 GB)
                        with tqdm_progress(prog, 0.0, 1.0, "Đang nạp Whisper để nghe file mẫu",
                                           download="Đang tải Whisper (nghe lời file mẫu)"):
                            model.load_asr_model()
                    prompts[key] = model.create_voice_clone_prompt(ref_audio=ref, ref_text=key[1] or None)
                gen["voice_clone_prompt"] = prompts[key]
            elif req.get("instruct"):
                gen["instruct"] = req["instruct"]
            if req.get("duration"):
                gen["duration"] = float(req["duration"])   # ép số giây: dễ ra tiếng bíp, TraMi không còn dùng
            if req.get("seed") is not None:
                _seed(int(req["seed"]))
            try:
                # bỏ đoạn nhỏ dần 0,1 s ở 2 đầu: câu ngắn ("Đi mau!") mất phụ âm cuối
                audio = model.generate(**gen, fade_duration=0.0)
            except TypeError:       # bản OmniVoice cũ không có tham số này
                audio = model.generate(**gen)
            write_wav(req["out"], audio[0], sr)
            _reply(proto, {"ok": True, "sr": sr})
        except Exception as e:  # noqa: BLE001
            _reply(proto, {"error": f"{type(e).__name__}: {e}"})
