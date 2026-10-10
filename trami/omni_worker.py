"""Tiến trình riêng chạy OmniVoice (nhái giọng từ file ghi âm mẫu). Cùng giao thức với vieneu_worker.

  vào : {"text": "...", "out": "x.wav", "ref_audio": "mau.wav", "ref_text": "lời trong file mẫu" | ""}
  ra  : @@TRAMI@@{"ok": true, "sr": 24000} | {"error": "..."} | {"progress": 0.5, "msg": "..."}
"""
from __future__ import annotations

import json
import os
import sys

from .vieneu_worker import _reply


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
            gen = {"text": req["text"], "language": "vi"}
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
                gen["duration"] = float(req["duration"])   # đọc vừa đúng số giây (khớp thời gian nhân vật)
            audio = model.generate(**gen)
            write_wav(req["out"], audio[0], sr)
            _reply(proto, {"ok": True, "sr": sr})
        except Exception as e:  # noqa: BLE001
            _reply(proto, {"error": f"{type(e).__name__}: {e}"})
