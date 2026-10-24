"""Tiến trình riêng chạy Qwen3-ASR (nghe tiếng Trung, có dấu câu) + Qwen3-ForcedAligner (mốc từng chữ, lệch ~0,03 s).
Cùng giao thức với vieneu_worker (mỗi dòng 1 JSON, tiền tố @@TRAMI@@). Chạy riêng để: lỗi / thiếu bộ nhớ GPU không làm
sập giao diện, tắt tiến trình là trả hết bộ nhớ GPU cho bước sau (tách giọng, lồng tiếng).

  vào : {"cmd": "asr", "wav": "x.wav", "spans": [[đầu, cuối], ...], "language": "zh", "prompt": "", "align": true}
        {"cmd": "align", "wav": "x.wav", "items": [[đầu, cuối, "lời"], ...], "language": "zh"}
  ra  : @@TRAMI@@{"ok": true, "items": [{"text": "...", "words": [[chữ, đầu, cuối], ...]}]}   (giây, tính từ đầu khúc)
        | {"error": "..."} | {"progress": 0.5, "msg": "..."}
"""
from __future__ import annotations

import json
import os
import sys
import wave

from .vieneu_worker import _reply

ALIGNER = "Qwen/Qwen3-ForcedAligner-0.6B-hf"
LANG = {"zh": "Chinese", "yue": "Cantonese", "en": "English", "ja": "Japanese", "ko": "Korean"}
SR = 16000


def _load_wav(path: str):
    import numpy as np

    with wave.open(path) as w:
        a = np.frombuffer(w.readframes(w.getnframes()), np.int16).astype(np.float32) / 32768.0
        if w.getnchannels() > 1:
            a = a.reshape(-1, w.getnchannels()).mean(axis=1)
    return a


def plan_batches(lens: list[float], idx: list[int], budget: float, cap: int = 8) -> list[list[int]]:
    """Chia khúc thành lô: khúc dài đi cùng khúc dài (ít phần đệm), mỗi lô ≤ `budget` giây âm thanh tính cả đệm
    (= khúc dài nhất × số khúc) -> bộ nhớ GPU của 1 lô có giới hạn (card 8 GB: tràn là Windows lấy RAM thay, rất chậm)."""
    out: list[list[int]] = []
    cur: list[int] = []
    longest = 0.0
    for k in sorted(idx, key=lambda i: -lens[i]):
        ln = max(float(lens[k]), 1.0)
        if cur and (len(cur) >= cap or max(longest, ln) * (len(cur) + 1) > budget):
            out.append(cur)
            cur, longest = [], 0.0
        cur.append(k)
        longest = max(longest, ln)
    if cur:
        out.append(cur)
    return out


def vram_plan(total_bytes: int) -> tuple[float, bool]:
    """(giây âm thanh mỗi lô, chỉ giữ 1 mô hình trên card) theo dung lượng card đồ hoạ."""
    gb = total_bytes / 1024 ** 3
    if gb >= 16:
        return 224.0, False
    if gb >= 11:
        return 112.0, False
    return 60.0, True          # card 8 GB (RTX 4060 laptop…): lô nhỏ, bộ nghe 1.7B và bộ căn 0.6B không nằm cùng lúc


def _load(cls, repo: str, **kw):
    """Đã tải ở tab Tài liệu -> nạp thẳng từ máy, KHÔNG hỏi mạng (mạng chập chờn / bị chặn vẫn chạy, không tải lại).
    Chưa có / thiếu file -> tải (lần đầu) từ HuggingFace vào đúng kho đó."""
    try:
        return cls.from_pretrained(repo, local_files_only=True, **kw)
    except Exception:  # noqa: BLE001
        return cls.from_pretrained(repo, **kw)


def main(repo: str = "Qwen/Qwen3-ASR-1.7B-hf") -> None:
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
        from transformers import AutoProcessor, Qwen3ASRForConditionalGeneration, Qwen3ASRForTokenClassification

        from .progress_hook import tqdm_progress

        if torch.cuda.is_available():
            dev = "cuda:0"
            dtype = torch.bfloat16 if torch.cuda.is_bf16_supported() else torch.float16
        else:
            dev, dtype = "cpu", torch.float32
        def load_asr():
            with tqdm_progress(prog, 0.0, 1.0, "Đang nạp Qwen3-ASR", download="Đang tải Qwen3-ASR"):
                m = _load(Qwen3ASRForConditionalGeneration, repo, dtype=dtype, device_map=dev)
            return m.eval()

        proc = _load(AutoProcessor, repo)
        state = {"model": load_asr()}
    except Exception as e:  # noqa: BLE001
        _reply(proto, {"error": f"Không nạp được Qwen3-ASR: {type(e).__name__}: {e}"})
        return
    if dev == "cpu":
        budget, one_model = 56.0, False
    else:
        budget, one_model = vram_plan(torch.cuda.get_device_properties(0).total_memory)
    if os.environ.get("TRAMI_QWEN_ONE_MODEL") == "1":
        one_model = True
    _reply(proto, {"ready": True, "sr": SR})

    aligner: dict = {}

    def drop(name: str):
        """Bỏ 1 mô hình khỏi card (card nhỏ: không để bộ nghe và bộ căn nằm cùng lúc)."""
        import gc

        if name == "model":
            state["model"] = None
        else:
            aligner.pop("model", None)
        gc.collect()
        if dev != "cpu":
            torch.cuda.empty_cache()

    def get_model():
        if state["model"] is None:
            if one_model and aligner.get("model") is not None:
                drop("aligner")
            state["model"] = load_asr()
        return state["model"]

    def get_aligner():
        if one_model and state["model"] is not None:
            drop("model")
        if aligner.get("model") is None:
            with tqdm_progress(prog, 0.0, 1.0, "Đang nạp bộ căn thời gian Qwen3",
                               download="Đang tải bộ căn thời gian Qwen3-ForcedAligner"):
                aligner.setdefault("proc", _load(AutoProcessor, ALIGNER))
                aligner["model"] = _load(Qwen3ASRForTokenClassification, ALIGNER, dtype=dtype, device_map=dev).eval()
        return aligner["proc"], aligner["model"]

    def run_batched(fn, idx: list[int], lens: list[float]) -> None:
        """Chạy theo lô (khúc dài cùng lô, giới hạn giây âm thanh mỗi lô); hết bộ nhớ GPU thì chạy lại từng khúc."""
        for part in plan_batches(lens, idx, budget):
            try:
                fn(part)
            except torch.cuda.OutOfMemoryError:
                torch.cuda.empty_cache()
                if len(part) == 1:
                    raise
                for k in part:
                    fn([k])

    def transcribe(clips, lang, prompt, lo: float, hi: float) -> list[str]:
        texts = [""] * len(clips)
        done = [0]
        model = get_model()

        def fn(part: list[int]):
            kw = {"language": [lang] * len(part)} if lang else {}
            if prompt:
                kw["prompt"] = [prompt] * len(part)
            inputs = proc.apply_transcription_request(audio=[clips[k] for k in part], **kw).to(model.device,
                                                                                               model.dtype)
            longest = max(len(clips[k]) for k in part) / SR
            with torch.inference_mode():
                out = model.generate(**inputs, max_new_tokens=int(longest * 10) + 48, do_sample=False)
            gen = out[:, inputs["input_ids"].shape[1]:]
            for k, r in zip(part, proc.decode(gen, return_format="parsed")):
                texts[k] = (r.get("transcription") or "").strip()
            done[0] += len(part)
            prog(lo + (hi - lo) * done[0] / max(1, len(clips)), f"Qwen3-ASR đang nghe {done[0]}/{len(clips)} khúc")

        run_batched(fn, list(range(len(clips))), [len(c) / SR for c in clips])
        return texts

    def align(clips, texts, lang, lo: float, hi: float) -> list[list]:
        aproc, amodel = get_aligner()
        words: list[list] = [[] for _ in clips]
        idx = [k for k, t in enumerate(texts) if t and aproc.split_words_for_alignment(t, lang)]
        done = [0]

        def fn(part: list[int]):
            inputs, wl = aproc.prepare_forced_aligner_inputs(audio=[clips[k] for k in part],
                                                             transcript=[texts[k] for k in part], language=lang)
            inputs = inputs.to(amodel.device, amodel.dtype)
            with torch.inference_mode():
                out = amodel(**inputs)
            res = aproc.decode_forced_alignment(logits=out.logits, input_ids=inputs["input_ids"], word_lists=wl,
                                                timestamp_token_id=amodel.config.timestamp_token_id)
            for k, items in zip(part, res):
                words[k] = [[it["text"], float(it["start_time"]), float(it["end_time"])] for it in items]
            done[0] += len(part)
            prog(lo + (hi - lo) * done[0] / max(1, len(idx)), f"Đang căn thời gian từng chữ {done[0]}/{len(idx)}")

        run_batched(fn, idx, [len(c) / SR for c in clips])
        return words

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
            audio = _load_wav(req["wav"])
            lang = LANG.get(req.get("language") or "", None)
            if req.get("cmd") == "align":
                spans = [(float(a), float(b)) for a, b, _t in req["items"]]
                texts = [str(t) for _a, _b, t in req["items"]]
            else:
                spans = [(float(a), float(b)) for a, b in req["spans"]]
                texts = None
            clips = [audio[int(a * SR):max(int(a * SR) + SR // 10, int(b * SR))] for a, b in spans]
            want_align = req.get("cmd") == "align" or bool(req.get("align"))
            if texts is None:
                texts = transcribe(clips, lang, req.get("prompt") or "", 0.0, 0.75 if want_align else 1.0)
                if want_align and dev != "cpu":
                    torch.cuda.empty_cache()   # trả bộ nhớ tạm của bước nghe trước khi nạp bộ căn thời gian
            words, err = [[] for _ in clips], ""
            if want_align:
                try:   # căn thời gian lỗi -> vẫn trả lời đã nghe được (TraMi tự dùng cách căn dự phòng)
                    words = align(clips, texts, lang or "Chinese", 0.75 if req.get("cmd") != "align" else 0.0, 1.0)
                except torch.cuda.OutOfMemoryError:
                    torch.cuda.empty_cache()
                    err = "GPU hết bộ nhớ khi căn thời gian"
                except Exception as e:  # noqa: BLE001
                    err = f"{type(e).__name__}: {e}"
            _reply(proto, {"ok": True, "align_error": err,
                           "items": [{"text": t, "words": w} for t, w in zip(texts, words)]})
        except Exception as e:  # noqa: BLE001
            _reply(proto, {"error": f"{type(e).__name__}: {e}"})
