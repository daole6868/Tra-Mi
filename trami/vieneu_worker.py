"""Tiến trình riêng chạy VieNeu-TTS.

Chạy VieNeu trong cùng tiến trình với giao diện dễ đụng độ GPU (trình phát video giải mã bằng GPU, CUDA graph của
VieNeu…) và nếu thư viện GPU sập thì cả cửa sổ TraMi tắt theo. Ở tiến trình riêng: VieNeu có CUDA context riêng,
sập thì TraMi chỉ báo lỗi và khởi động lại.

Giao thức (mỗi dòng 1 JSON, UTF-8):
  vào : {"text": "...", "out": "đường/dẫn.wav", "voice": "Tên giọng" | null, "ref_audio": "mẫu.wav" | null}
  ra  : @@TRAMI@@{"ok": true, "sr": 48000}   hoặc   @@TRAMI@@{"error": "..."}
Dòng đầu tiên sau khi nạp mô hình: @@TRAMI@@{"ready": true}
"""
from __future__ import annotations

import json
import sys

MARK = "@@TRAMI@@"


def _reply(out, obj: dict) -> None:
    out.write(MARK + json.dumps(obj, ensure_ascii=False) + "\n")
    out.flush()


def main(mode: str = "v3turbo") -> None:
    # stdout dành cho giao thức: chuyển mọi print/log của thư viện sang stderr (hiện trong cửa sổ đen)
    proto = sys.stdout
    if sys.stderr is None:
        import os

        sys.stderr = open(os.devnull, "w", encoding="utf-8")
    sys.stdout = sys.stderr
    try:
        proto.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
        sys.stdin.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:
        pass
    try:
        from vieneu import Vieneu

        from .tts import write_wav

        tts = Vieneu(mode=mode)
        sr = int(getattr(tts, "sample_rate", 48000))
    except Exception as e:  # noqa: BLE001
        _reply(proto, {"error": f"Không nạp được VieNeu: {type(e).__name__}: {e}"})
        return
    _reply(proto, {"ready": True, "sr": sr})
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
            kw = {}
            if req.get("ref_audio"):
                kw["ref_audio"] = req["ref_audio"]
            elif req.get("voice"):
                kw["voice"] = req["voice"]
            wav = tts.infer(req["text"], **kw)
            write_wav(req["out"], wav, sr)
            _reply(proto, {"ok": True, "sr": sr})
        except Exception as e:  # noqa: BLE001
            _reply(proto, {"error": f"{type(e).__name__}: {e}"})


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "v3turbo")
