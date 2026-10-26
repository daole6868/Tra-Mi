"""Tiến trình riêng chạy Qwen3-ASR (nghe tiếng Trung, có dấu câu) + Qwen3-ForcedAligner (mốc từng chữ, lệch ~0,03 s).
Cùng giao thức với vieneu_worker (mỗi dòng 1 JSON, tiền tố @@TRAMI@@). Chạy riêng để: lỗi / thiếu bộ nhớ GPU không làm
sập giao diện, tắt tiến trình là trả hết bộ nhớ GPU cho bước sau (tách giọng, lồng tiếng).

  vào : {"cmd": "asr", "wav": "x.wav", "spans": [[đầu, cuối], ...], "language": "zh", "prompt": "", "align": true,
         "conf": false, "rep_penalty": 1.2 (tuỳ chọn – nghe lại khúc nghi lặp vòng, 2.1.8)}
        {"cmd": "align", "wav": "x.wav", "items": [[đầu, cuối, "lời"], ...], "language": "zh"}
  ra  : @@TRAMI@@{"ok": true, "items": [{"text": "...", "words": [[chữ, đầu, cuối], ...], "conf": 0.93}]}
        (giây, tính từ đầu khúc; "conf" khi hỏi "conf": true = xác suất trung bình của các chữ bộ nghe chọn;
        "lang" = tiếng bộ nghe tự nhận ra khi "language" là "auto" – VD "English")
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


def read_clips(path: str, spans: list[tuple[float, float]], max_len: float | None = None) -> list:
    """Chỉ đọc đúng các khúc cần nghe từ file wav 16 kHz (phim 3 giờ: không nạp cả 345 MB vào bộ nhớ mỗi lệnh –
    nghe theo từng nhóm khúc để lưu tiến độ thì mỗi nhóm 1 lệnh)."""
    import numpy as np

    out = []
    with wave.open(path) as w:
        ch, n = w.getnchannels(), w.getnframes()
        for a, b in spans:
            if max_len:
                b = min(b, a + max_len)
            i = min(n, max(0, int(a * SR)))
            j = min(n, max(i + SR // 10, int(b * SR)))
            w.setpos(i)
            x = np.frombuffer(w.readframes(max(0, j - i)), np.int16).astype(np.float32) / 32768.0
            out.append(x.reshape(-1, ch).mean(axis=1) if ch > 1 else x)
    return out


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


def mean_conf(gen_ids: list[int], logps: list[float], skip: set, start_id: int | None = None) -> float:
    """Độ tin cậy 1 câu nghe: xác suất trung bình của các chữ bộ nghe chọn (bỏ ký hiệu đặc biệt; có `start_id` – thẻ
    mở đầu lời <asr_text> – thì chỉ tính phần lời sau thẻ, không tính phần "language Chinese" luôn chắc chắn)."""
    import math

    st = gen_ids.index(start_id) + 1 if start_id is not None and start_id in gen_ids else 0
    vals = [math.exp(lp) for t, lp in zip(gen_ids[st:], logps[st:]) if t not in skip]
    return round(sum(vals) / len(vals), 3) if vals else 0.0


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
        from transformers import (AutoProcessor, LogitsProcessor, LogitsProcessorList, Qwen3ASRForConditionalGeneration,
                                  Qwen3ASRForTokenClassification)

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

    class TokenConf(LogitsProcessor):
        """Ghi log-xác suất của chữ được chọn ở mỗi bước (giải mã tham lam: chữ có điểm cao nhất) – không đổi kết quả,
        không giữ cả bảng điểm (151 nghìn chữ × số bước) trong bộ nhớ."""

        def __init__(self):
            self.steps: list = []

        def __call__(self, input_ids, scores):
            self.steps.append(torch.log_softmax(scores.float(), dim=-1).max(dim=-1).values)
            return scores

    tok = getattr(proc, "tokenizer", None)
    skip_ids = set(getattr(tok, "all_special_ids", None) or [])
    eos = getattr(getattr(state["model"], "generation_config", None), "eos_token_id", None)
    skip_ids |= set(eos if isinstance(eos, (list, tuple)) else [eos] if eos is not None else [])
    try:
        text_id = tok.convert_tokens_to_ids("<asr_text>") if tok is not None else None
        text_id = None if text_id is None or text_id == getattr(tok, "unk_token_id", -1) else int(text_id)
    except Exception:  # noqa: BLE001
        text_id = None

    def transcribe(clips, lang, prompt, lo: float, hi: float, conf: bool = False,
                   rep: float | None = None) -> tuple[list[str], list]:
        texts = [""] * len(clips)
        confs: list = [None] * len(clips)
        found: list = [None] * len(clips)   # tiếng bộ nghe tự nhận ra (khi không ép tiếng)
        done = [0]
        model = get_model()

        def fn(part: list[int]):
            kw = {"language": [lang] * len(part)} if lang else {}
            if prompt:
                kw["prompt"] = [prompt] * len(part)
            inputs = proc.apply_transcription_request(audio=[clips[k] for k in part], **kw).to(model.device,
                                                                                               model.dtype)
            longest = max(len(clips[k]) for k in part) / SR
            rec = TokenConf() if conf else None
            gkw = {"logits_processor": LogitsProcessorList([rec])} if rec is not None else {}
            if rep:
                gkw["repetition_penalty"] = float(rep)   # phạt chữ đã viết -> bộ nghe không "lặp vòng" được nữa
            with torch.inference_mode():
                out = model.generate(**inputs, max_new_tokens=int(longest * 10) + 48, do_sample=False, **gkw)
            gen = out[:, inputs["input_ids"].shape[1]:]
            for k, r in zip(part, proc.decode(gen, return_format="parsed")):
                texts[k] = (r.get("transcription") or "").strip()
                found[k] = r.get("language") or None
            if rec is not None and rec.steps:
                lp = torch.stack(rec.steps, dim=1).cpu()
                n = min(lp.shape[1], gen.shape[1])
                for row, k in enumerate(part):
                    confs[k] = mean_conf(gen[row, :n].tolist(), lp[row, :n].tolist(), skip_ids, text_id)
            done[0] += len(part)
            prog(lo + (hi - lo) * done[0] / max(1, len(clips)), f"Qwen3-ASR đang nghe {done[0]}/{len(clips)} khúc")

        run_batched(fn, list(range(len(clips))), [len(c) / SR for c in clips])
        state["found"] = found
        return texts, confs

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
            lang = LANG.get(req.get("language") or "", None)
            if req.get("cmd") == "align":
                spans = [(float(a), float(b)) for a, b, _t in req["items"]]
                texts = [str(t) for _a, _b, t in req["items"]]
            else:
                spans = [(float(a), float(b)) for a, b in req["spans"]]
                texts = None
            clips = read_clips(req["wav"], spans)
            want_align = req.get("cmd") == "align" or bool(req.get("align"))
            confs: list = [None] * len(clips)
            found: list = [None] * len(clips)
            if texts is None:
                texts, confs = transcribe(clips, lang, req.get("prompt") or "", 0.0, 0.75 if want_align else 1.0,
                                          conf=bool(req.get("conf")), rep=req.get("rep_penalty"))
                found = state.pop("found", found)
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
                           "items": [dict({"text": t, "words": w}, **({} if c is None else {"conf": c}),
                                          **({"lang": f} if f else {}))
                                     for t, w, c, f in zip(texts, words, confs, found)]})
        except Exception as e:  # noqa: BLE001
            _reply(proto, {"error": f"{type(e).__name__}: {e}"})
