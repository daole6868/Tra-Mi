"""Tiến trình riêng chạy FireRedASR2-AED (nghe tiếng Trung + giọng địa phương, mốc từng chữ bằng nhánh CTC, điểm tin
cậy) + FireRedPunc (đặt dấu câu). Cùng giao thức với qwen_asr_worker – asr_qwen dùng chung cách chia khúc / nghe lại
chỗ cắt khúc / căn thời gian cho cả 2 bộ nghe.

  vào : {"cmd": "asr", "wav": "x.wav", "spans": [[đầu, cuối], ...], "language": "zh", "align": true}
        {"cmd": "align", "wav": "x.wav", "items": [[đầu, cuối, "lời"], ...]}
  ra  : @@TRAMI@@{"ok": true, "items": [{"text": "...", "words": [[chữ, đầu, cuối], ...], "conf": 0.93}]}
        | {"error": "..."} | {"progress": 0.5, "msg": "..."}
"""
from __future__ import annotations

import json
import os
import sys

from .qwen_asr_worker import _load_wav, plan_batches, vram_plan
from .vieneu_worker import _reply

SR = 16000
MAX_CLIP = 58.0   # bản AED nghe tốt tới ~60 s / khúc (dài hơn dễ "bịa" chữ)


def model_dirs(repo: str) -> tuple[str, str]:
    """(thư mục FireRedASR2-AED, thư mục FireRedPunc – '' nếu chưa tải)."""
    from .models import FIRERED_PUNC, _hf_snapshot

    aed = os.environ.get("TRAMI_FIRERED_DIR") or str(_hf_snapshot(repo) or "")
    punc = os.environ.get("TRAMI_FIRERED_PUNC")
    if punc is None:
        punc = str(_hf_snapshot(FIRERED_PUNC) or "")
    return aed, punc


def main(repo: str = "FireRedTeam/FireRedASR2-AED") -> None:
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

        from .firered import Recognizer

        aed_dir, punc_dir = model_dirs(repo)
        if not aed_dir or not os.path.isfile(os.path.join(aed_dir, "model.pth.tar")):
            raise RuntimeError("chưa tải mô hình FireRedASR2 – vào tab Tài liệu bấm Tải về ở mục FireRedASR2")
        dev = "cuda:0" if torch.cuda.is_available() else "cpu"
        total = torch.cuda.get_device_properties(0).total_memory if dev != "cpu" else 0
        # card < 12 GB: trọng số nửa độ chính xác (bf16 ~2,2 GB thay vì 4,4 GB) – card 8 GB còn chỗ cho lô nghe
        half = dev != "cpu" and total < 12 * 1024 ** 3
        prog(0.0, "Đang nạp FireRedASR2")
        rec = Recognizer(aed_dir, dev, half=half)
        punc = None
        if punc_dir and os.path.isfile(os.path.join(punc_dir, "model.pth.tar")):
            from .firered.punc import Punctuator

            prog(0.5, "Đang nạp bộ đặt dấu câu FireRedPunc")
            punc = Punctuator(punc_dir, dev)
    except Exception as e:  # noqa: BLE001
        _reply(proto, {"error": f"Không nạp được FireRedASR2: {type(e).__name__}: {e}"})
        return
    budget = 56.0 if dev == "cpu" else vram_plan(total)[0]
    _reply(proto, {"ready": True, "sr": SR, "punc": punc is not None})

    def run_batched(fn, idx: list[int], lens: list[float]) -> None:
        for part in plan_batches(lens, idx, budget):
            try:
                fn(part)
            except torch.cuda.OutOfMemoryError:
                torch.cuda.empty_cache()
                if len(part) == 1:
                    raise
                for k in part:
                    fn([k])

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
            audio = _load_wav(req["wav"])
            if req.get("cmd") == "align":
                spans = [(float(a), float(b)) for a, b, _t in req["items"]]
                given = [str(t) for _a, _b, t in req["items"]]
            else:
                spans = [(float(a), float(b)) for a, b in req["spans"]]
                given = None
            clips = [audio[int(a * SR):max(int(a * SR) + SR // 10, int(min(b, a + MAX_CLIP) * SR))] for a, b in spans]
            lens = [len(c) / SR for c in clips]
            items = [{"text": "", "words": [], "conf": 0.0} for _ in clips]
            done = [0]

            def fn(part: list[int]):
                if given is None:
                    res = rec.transcribe([clips[k] for k in part])
                    if punc is not None:
                        for r, t in zip(res, punc([r["text"] for r in res])):
                            r["text"] = t
                    for k, r in zip(part, res):
                        items[k] = {"text": r["text"], "words": r["words"], "conf": r["confidence"]}
                    what = "FireRedASR2 đang nghe"
                else:
                    for k, w in zip(part, rec.align([clips[k] for k in part], [given[k] for k in part])):
                        items[k] = {"text": given[k], "words": w, "conf": 0.0}
                    what = "FireRedASR2 đang căn thời gian từng chữ"
                done[0] += len(part)
                prog(done[0] / max(1, len(clips)), f"{what} {done[0]}/{len(clips)} khúc")

            idx = list(range(len(clips))) if given is None else [k for k, t in enumerate(given) if t.strip()]
            run_batched(fn, idx, lens)
            _reply(proto, {"ok": True, "align_error": "", "items": items})
        except Exception as e:  # noqa: BLE001
            _reply(proto, {"error": f"{type(e).__name__}: {e}"})
