"""Tiến trình riêng chạy VieNeu-TTS.

Chạy VieNeu trong cùng tiến trình với giao diện dễ đụng độ GPU (trình phát video giải mã bằng GPU, CUDA graph của
VieNeu…) và nếu thư viện GPU sập thì cả cửa sổ TraMi tắt theo. Ở tiến trình riêng: VieNeu có CUDA context riêng,
sập thì TraMi chỉ báo lỗi và khởi động lại.

Giao thức (mỗi dòng 1 JSON, UTF-8):
  vào : {"text": "...", "out": "đường/dẫn.wav", "voice": "Tên giọng" | null, "ref_audio": "mẫu.wav" | null,
         "params": {"temperature": 0.6, ...} (tuỳ chọn, kiểu đọc), "seed": số | null (đọc lại khi câu trước hỏng)}
  ra  : @@TRAMI@@{"ok": true, "sr": 48000}   hoặc   @@TRAMI@@{"error": "..."}
Dòng đầu tiên sau khi nạp mô hình: @@TRAMI@@{"ready": true}
"""
from __future__ import annotations

import json
import sys

MARK = "@@TRAMI@@"
_INFER_KEYS = {"temperature", "top_k", "top_p", "repetition_penalty"}


def _seed(n: int) -> None:
    """Đổi hạt ngẫu nhiên (đọc lại câu bị hỏng sẽ ra kết quả khác)."""
    import random

    random.seed(n)
    try:
        import numpy as np

        np.random.seed(n % (2 ** 32))
    except Exception:  # noqa: BLE001
        pass
    try:
        import torch

        torch.manual_seed(n)
    except Exception:  # noqa: BLE001
        pass


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

        from .progress_hook import tqdm_progress
        from .tts import write_wav

        def prog(x: float, msg: str):
            _reply(proto, {"progress": round(x, 4), "msg": msg})

        with tqdm_progress(prog, 0.0, 1.0, "Đang nạp VieNeu-TTS", download="Đang tải VieNeu-TTS"):
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
            if req.get("seed") is not None:
                _seed(int(req["seed"]))
            prm = {k: v for k, v in (req.get("params") or {}).items() if k in _INFER_KEYS}
            try:
                wav = tts.infer(req["text"], **kw, **prm)
            except TypeError:       # bản VieNeu cũ không nhận thông số lấy mẫu
                wav = tts.infer(req["text"], **kw)
            write_wav(req["out"], wav, sr)
            _reply(proto, {"ok": True, "sr": sr})
        except Exception as e:  # noqa: BLE001
            _reply(proto, {"error": f"{type(e).__name__}: {e}"})


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "v3turbo")
