"""Tiến trình riêng chạy VoxCPM2 (OpenBMB, Apache-2.0 – gói `voxcpm`). Cùng giao thức với vieneu_worker / omni_worker.

  vào : {"text": "...", "out": "x.wav", "ref_audio": "mau.wav" | null, "ref_text": "lời trong file mẫu" | "",
         "reference": "mau.wav" | null, "control": "mô tả giọng / kiểu đọc" | "", "seed": n | null,
         "cfg": 2.0, "steps": 10}
  ra  : @@TRAMI@@{"ok": true, "sr": 48000} | {"error": "..."} | {"progress": 0.5, "msg": "..."}
  vào {"cmd": "trim"}: trả bộ nhớ đệm card, giữ mô hình

3 cách đọc của VoxCPM2:
  - tạo giọng theo mô tả: control = "A young woman, …" (không file mẫu) – chữ đặt trong ngoặc ở đầu lời đọc
  - nhái giọng (chỉ file mẫu, không cần lời): reference = file mẫu – hợp nhái giọng nhân vật gốc nói tiếng khác
  - nhái sát nhất: ref_audio + ref_text (đọc tiếp file mẫu) + reference cùng file – giữ giọng + nhịp + cảm xúc của mẫu
"""
from __future__ import annotations

import json
import os
import sys

from .vieneu_worker import _reply, _seed, mem_guard, reserved

DEFAULT_REPO = "openbmb/VoxCPM2"


def make_kwargs(req: dict) -> tuple[str, dict]:
    """(lời đọc, tham số generate) từ 1 yêu cầu – thuần, test gọi thẳng."""
    text = str(req.get("text") or "").strip()
    control = str(req.get("control") or "").strip()
    if control:
        text = f"({control}){text}"
    kw: dict = {"cfg_value": float(req.get("cfg") or 2.0), "inference_timesteps": int(req.get("steps") or 10)}
    ref, rtext = req.get("ref_audio"), str(req.get("ref_text") or "").strip()
    if ref and rtext and not control:   # đọc tiếp file mẫu (cả file lẫn lời); có chỉ dẫn thì không (voxcpm không nhận)
        kw["prompt_wav_path"] = ref
        kw["prompt_text"] = rtext
    reference = req.get("reference") or (ref if ref else None)
    if reference:
        kw["reference_wav_path"] = reference
    return text, kw


def main(repo: str = DEFAULT_REPO) -> None:
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

    from .torchenv import guard_torchvision, load_error

    guard_torchvision()
    try:
        import numpy as np
        from voxcpm import VoxCPM

        from .hfoffline import load_local_first
        from .progress_hook import tqdm_progress
        from .tts import write_wav

        def stalled(st, idle):
            _reply(proto, {"error": f"Không tải được VoxCPM2 từ HuggingFace: {idle / 60:.0f} phút không nhận thêm dữ "
                                    f"liệu (đã tải {st['bytes'] / 1e6:.0f}/{st['total'] / 1e6:.0f} MB) – tải ở tab Tài "
                                    "liệu (tự chuyển ModelScope khi HuggingFace bị chặn) rồi bấm lại."})
            os._exit(3)

        # optimize=False: torch.compile cần triton (Windows không có) + nạp lâu; load_denoiser=False: bộ khử ồn tải
        # thêm từ ModelScope, tool tự lọc file mẫu
        with tqdm_progress(prog, 0.0, 1.0, "Đang nạp VoxCPM2", download="Đang tải VoxCPM2"):
            model = load_local_first(lambda: VoxCPM.from_pretrained(repo, load_denoiser=False, optimize=False),
                                     (repo.split("/")[-1], "voxcpm"), "VoxCPM2", report=prog, on_stall=stalled)
        sr = int(getattr(getattr(model, "tts_model", None), "sample_rate", None) or 48000)
    except Exception as e:  # noqa: BLE001
        _reply(proto, {"error": load_error("VoxCPM2", e)})
        return
    _reply(proto, {"ready": True, "sr": sr})
    base = reserved()

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
            if req.get("cmd") == "trim":
                _reply(proto, dict({"ok": True}, **mem_guard(base, None, True, "VoxCPM2")))
                continue
            text, kw = make_kwargs(req)
            if req.get("seed") is not None:
                _seed(int(req["seed"]))
            wav = model.generate(text=text, **kw)
            write_wav(req["out"], np.asarray(wav, dtype=np.float32).reshape(-1), sr)
            _reply(proto, dict({"ok": True, "sr": sr}, **mem_guard(base, None, False, "VoxCPM2")))
        except Exception as e:  # noqa: BLE001
            _reply(proto, {"error": f"{type(e).__name__}: {e}"})
