"""Nhận dạng tiếng Trung bằng Qwen3-ASR (Alibaba Qwen, 1/2026) + mốc từng chữ bằng Qwen3-ForcedAligner.

Vì sao: nghe chuẩn hơn SenseVoice / Whisper rõ rệt (WenetSpeech net/meeting: 4,97 / 5,88 % lỗi chữ so với 9,86 / 19,11
của Whisper large-v3), đặt dấu câu theo nghĩa (mô hình ngôn ngữ), nghe được cả lúc có nhạc / đang hát; bộ căn thời gian
lệch trung bình ~33 ms với tiếng Trung (fa-zh của FunASR ~161 ms) -> tách câu đúng chỗ nghỉ thật, giọng lồng khớp miệng.

Chạy trong tiến trình riêng (trami/qwen_asr_worker.py), dùng thư viện transformers ≥ 5.13 (có sẵn Qwen3-ASR, chung bản
với OmniVoice). Mô hình tải từ HuggingFace vào ~/.cache/huggingface/hub ở lần chạy đầu.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Callable

import numpy as np

from .subtitle import Segment
from .tts import VieneuWorker

Prog = Callable[[float, str], None]
MODELS = [("Qwen/Qwen3-ASR-1.7B-hf", "1.7B – chính xác nhất (GPU ≥ 6 GB, khuyên dùng)"),
          ("Qwen/Qwen3-ASR-0.6B-hf", "0.6B – nhẹ, nhanh (GPU yếu / chỉ có CPU)")]
ALIGNER = "Qwen/Qwen3-ForcedAligner-0.6B-hf"
MIN_TRANSFORMERS = (5, 13)
UPGRADE_CMD = '.venv\\Scripts\\python -m pip install -U "transformers>=5.13" accelerate'
SR = 16000


def transformers_version() -> tuple[int, ...] | None:
    try:
        from importlib.metadata import version

        return tuple(int(x) for x in re.findall(r"\d+", version("transformers"))[:3])
    except Exception:  # noqa: BLE001
        return None


def available() -> bool:
    """Đã cài torch + transformers đủ mới (KHÔNG import – import transformers mất vài giây)."""
    from .config import installed

    v = transformers_version()
    return installed("torch", "transformers") and v is not None and v[:2] >= MIN_TRANSFORMERS


def why_unavailable() -> str:
    from .config import installed

    if not installed("torch"):
        return "chưa cài PyTorch"
    v = transformers_version()
    if v is None:
        return "chưa cài transformers – chạy: " + UPGRADE_CMD
    return f"transformers {'.'.join(map(str, v))} quá cũ (cần ≥ 5.13) – chạy: " + UPGRADE_CMD


class QwenAsrWorker(VieneuWorker):
    """Tiến trình riêng chạy Qwen3-ASR; mode = tên mô hình trên HuggingFace."""
    title = "Qwen3-ASR"
    stall_call = 1800.0  # 1 khúc nghe không xong trong 30 phút -> treo
    flag = "--qwen-asr-worker"
    log_name = "qwen_asr.txt"

    def __init__(self, repo: str, token: str = ""):
        super().__init__(repo)
        self.token = token

    def _extra_env(self) -> dict:
        return {"HF_TOKEN": self.token} if self.token else {}

    def call(self, req: dict, progress: Prog | None = None, cancel=None) -> dict:
        with self.lock:
            if not self.proc or self.proc.poll() is not None:
                self._start(progress, cancel)
            assert self.proc and self.proc.stdin
            self.proc.stdin.write(json.dumps(req, ensure_ascii=False) + "\n")
            self.proc.stdin.flush()
            r = self._read(progress, cancel, self.stall_call)
        if "error" in r:
            raise RuntimeError(r["error"])
        return r


_worker: QwenAsrWorker | None = None


def worker(settings: dict | None = None) -> QwenAsrWorker:
    global _worker
    st = settings or {}
    repo = st.get("qwen_asr_model") or MODELS[0][0]
    if _worker is None or _worker.mode != repo:
        release()
        _worker = QwenAsrWorker(repo, st.get("hf_token", ""))
        import atexit

        atexit.register(release)
    return _worker


def release() -> None:
    """Tắt tiến trình Qwen3-ASR – trả bộ nhớ GPU cho các bước sau (phân biệt người nói, lồng tiếng)."""
    global _worker
    if _worker is not None:
        _worker.stop()
        _worker = None


def hotwords(settings: dict) -> str:
    """Tên riêng / thuật ngữ tiếng Trung trong bảng thuật ngữ (Cài đặt) -> gợi ý cho Qwen3-ASR nghe đúng."""
    terms = []
    for line in (settings.get("glossary") or "").splitlines():
        zh = line.split("=", 1)[0].strip()
        if zh and re.search(r"[㐀-鿿]", zh) and zh not in terms:
            terms.append(zh)
    return ("热词：" + "、".join(terms[:80])) if terms else ""


def _wav_path(wav: str | Path) -> str:
    """Tiến trình nghe đọc thẳng file wav 16 kHz mono (đúng định dạng TraMi tạo ở bước chuẩn bị âm thanh)."""
    return str(Path(wav).resolve())


def _spans(audio: np.ndarray, settings: dict) -> list[tuple[float, float]]:
    """Đoạn có tiếng nói: bộ chia đoạn VAD của FunASR nếu có, không thì theo độ to giọng."""
    from . import asr_funasr
    from .align import speech_spans

    if asr_funasr.available():
        try:
            return asr_funasr.vad_segments(audio, asr_funasr._device(settings.get("asr_device", "auto")))
        except Exception:  # noqa: BLE001 – VAD lỗi thì dùng độ to
            pass
    return speech_spans(audio)


def _segment(s: float, e: float, item: dict, simplify: bool) -> Segment | None:
    from .align import times_from_words
    from .asr import is_hallucination, to_simplified

    raw = (item.get("text") or "").strip()
    text = to_simplified(raw) if simplify else raw
    if len(text) != len(raw):
        text = raw    # đổi phồn -> giản làm lệch số chữ: giữ nguyên để mốc từng chữ còn đúng
    if not text or is_hallucination(text):
        return None
    seg = Segment(round(s, 3), round(e, 3), text)
    ct = times_from_words(text, item.get("words") or [], s)
    if ct:
        seg.extra["ct"] = [[a, b] for a, b in ct]
        seg.start, seg.end = round(max(s, ct[0][0] - 0.05), 3), round(min(e, ct[-1][1] + 0.05), 3)
    return seg


STREAM_GROUP = 6   # chế độ vừa tách vừa dịch: nghe từng nhóm 6 khúc (≤ ~3 phút) rồi gửi câu sang bộ dịch ngay
FORCED_GAP = 0.3   # 2 khúc nghe liền nhau (nghỉ < 0,3 s): khúc bị cắt giữa lúc đang nói vì dài quá 28 s
RELISTEN_PAD = 6.0  # nghe lại 6 s mỗi bên chỗ cắt khúc để bộ nghe thấy trọn câu
MERGE_MAX = 56.0    # khúc sau khi nối ≤ 56 s (bộ căn thời gian chạy 1 khúc / lô trên card 8 GB)


def relisten_cuts(raw: list[tuple[float, float, str]], wav: str | Path, settings: dict, language: str,
                  progress: Prog | None = None, cancel=None, stats: dict | None = None) -> list[tuple[float, float, str]]:
    """raw: [(đầu, cuối, lời)] từng khúc theo thứ tự. Bộ nghe hay tự đặt "。" ở cuối mỗi khúc; ở chỗ cắt khúc KHÔNG có
    khoảng nghỉ (cắt giữa lúc đang nói) dấu đó thường là giả ("我刚回来就。" | "收到…"). Nghe lại 12 s quanh mỗi chỗ cắt
    như vậy (6 s mỗi bên): nghe có ngữ cảnh mà không có dấu kết câu ở chỗ nối -> bỏ dấu, nối 2 khúc (bước tách câu sau
    đó tách lại đúng chỗ); sửa luôn chữ nghe sai sát chỗ cắt (`align.heard_junction`)."""
    from .align import CLOSERS, STRONG, heard_junction

    st = stats if stats is not None else {}
    st.update(cuts=0, merged=0, fixed=0)
    todo = []
    for k in range(len(raw) - 1):
        s1, e1, t1 = raw[k]
        s2, e2, t2 = raw[k + 1]
        if not t1 or not t2 or s2 - e1 >= FORCED_GAP or t1.rstrip(CLOSERS)[-1:] not in STRONG:
            continue
        a, b = max(s1, e1 - RELISTEN_PAD), min(e2, s2 + RELISTEN_PAD)
        if b - a >= 2.0:
            todo.append((k, a, b))
    st["cuts"] = len(todo)
    if not todo:
        return raw
    if progress:
        progress(0.0, f"Nghe lại {len(todo)} chỗ cắt khúc giữa lúc đang nói…")
    r = worker(settings).call({"cmd": "asr", "wav": _wav_path(wav), "language": language, "prompt": hotwords(settings),
                               "align": False, "spans": [[a, b] for _, a, b in todo]}, progress, cancel)
    raw = list(raw)
    merge: dict[int, str] = {}
    for (k, _a, _b), it in zip(todo, r.get("items") or []):
        res = heard_junction(raw[k][2], raw[k + 1][2], (it.get("text") or "").strip())
        if res is None:
            continue
        if res["fixed"]:
            raw[k] = (raw[k][0], raw[k][1], res["t1"])
            raw[k + 1] = (raw[k + 1][0], raw[k + 1][1], res["t2"])
            st["fixed"] += res["fixed"]
        if not res["end"]:
            merge[k] = res["mark"]
    out: list[tuple[float, float, str]] = []
    for k, (s, e, t) in enumerate(raw):
        if out and (k - 1) in merge and e - out[-1][0] <= MERGE_MAX:
            ps, _pe, pt = out[-1]
            core = pt.rstrip(CLOSERS)
            tail = pt[len(core):]
            core = core[:-1] if core[-1:] in STRONG else core
            out[-1] = (ps, e, core + merge[k - 1] + tail + t)
            st["merged"] += 1
        else:
            out.append((s, e, t))
    return out


def transcribe_qwen(wav: str | Path, settings: dict, language: str = "zh", progress: Prog | None = None,
                    cancel=None, on_chunk: Callable[[list[Segment]], None] | None = None,
                    stats: dict | None = None) -> list[Segment]:
    """Nghe cả video: chia đoạn có tiếng -> gộp thành khúc ≤ 28 s (nghe trọn câu) -> Qwen3-ASR -> nghe lại chỗ cắt khúc
    giữa lúc đang nói (`relisten_cuts`) -> mốc từng chữ. Mỗi khúc trả về 1 Segment có extra["ct"]; bước căn thời gian
    (align.refine) tách câu theo dấu câu + chỗ nghỉ.
    on_chunk: nghe theo từng nhóm khúc, nghe xong nhóm nào gọi on_chunk(các khúc đó) ngay (vừa tách vừa dịch).
    stats: nhận {"cuts", "merged", "fixed"} của bước nghe lại chỗ cắt khúc."""
    from .align import chunk_spans, split_params
    from .asr_funasr import load_wav16k

    if progress:
        progress(0.0, "Đang chia đoạn giọng nói…")
    audio = load_wav16k(wav)
    chunks = chunk_spans(_spans(audio, settings), audio, max_gap=split_params(settings)["chunk_gap"], max_len=28.0)
    if not chunks:
        return []
    simplify = language in ("zh", "yue", "auto")
    want_align = bool(settings.get("align_words", True))
    req = {"cmd": "asr", "wav": _wav_path(wav), "language": language, "prompt": hotwords(settings),
           "align": want_align}
    # NGHE hết (chưa căn) -> nghe lại chỗ cắt khúc -> CĂN 1 lần cho cả phim (bộ căn chỉ nạp sau khi nghe xong).
    # Vừa tách vừa dịch: nghe theo từng nhóm khúc -> gửi câu nháp ngay. (2.0.64 nghe + căn xen kẽ từng nhóm -> từ nhóm 2
    # bộ căn 0.6B nằm sẵn trên card trong lúc bộ nghe 1.7B chạy -> đỉnh bộ nhớ cao hơn ~1,2 GB, card nhỏ tràn sang RAM)
    groups = [chunks[i:i + STREAM_GROUP] for i in range(0, len(chunks), STREAM_GROUP)] if on_chunk else [chunks]
    a_end = 0.8 if want_align else 0.95
    raw: list[tuple[float, float, str]] = []
    for g, part in enumerate(groups):
        lo, hi = a_end * g / len(groups), a_end * (g + 1) / len(groups)
        pr = None
        if progress:
            def pr(x, m, lo=lo, hi=hi, g=g):
                progress(lo + (hi - lo) * x, m + (f" (phần {g + 1}/{len(groups)})" if len(groups) > 1 else ""))
        r = worker(settings).call(dict(req, spans=[list(c) for c in part], align=False), pr, cancel)
        new = []
        for (s, e), it in zip(part, r.get("items") or []):
            text = (it.get("text") or "").strip()
            raw.append((s, e, text))
            if x := _segment(s, e, {"text": text}, simplify):
                new.append(x)
        if new and on_chunk:
            on_chunk(new)
    if settings.get("asr_relisten", True) and len(raw) > 1:
        rpr = (lambda x, m: progress(a_end + 0.03 * x, m)) if progress else None
        raw = relisten_cuts(raw, wav, settings, language, rpr, cancel, stats)
    a_end = min(0.97, a_end + 0.03)
    words: list = [[] for _ in raw]
    if want_align and raw:
        todo = [k for k, (_s, _e, t) in enumerate(raw) if t]
        apr = (lambda x, m: progress(a_end + (1 - a_end) * x, m)) if progress else None
        r = worker(settings).call({"cmd": "align", "wav": _wav_path(wav), "language": language,
                                   "items": [[raw[k][0], raw[k][1], raw[k][2]] for k in todo]}, apr, cancel)
        for k, it in zip(todo, r.get("items") or []):
            words[k] = it.get("words") or []
        if r.get("align_error") and progress:
            progress(1.0, "Không căn được thời gian bằng Qwen3 (" + r["align_error"][:80] + ") – dùng cách dự phòng")
    return [x for (s, e, t), w in zip(raw, words) if (x := _segment(s, e, {"text": t, "words": w}, simplify))]


def qwen_decoder(settings: dict, language: str, work: Path) -> Callable[[np.ndarray], tuple[str, list[str]]]:
    """Hàm nghe 1 đoạn ngắn bằng Qwen3-ASR – dùng để nghe lại các đoạn bị bộ chia đoạn bỏ sót."""
    from .tts import write_wav

    w = worker(settings)
    tmp = Path(work) / "qwen_clip.wav"

    def decode(clip: np.ndarray) -> tuple[str, list[str]]:
        if len(clip) < SR * 0.2:
            return "", []
        write_wav(tmp, clip.astype(np.float32), SR)
        r = w.call({"cmd": "asr", "wav": _wav_path(tmp), "spans": [[0.0, len(clip) / SR]], "language": language,
                    "prompt": hotwords(settings), "align": False})
        items = r.get("items") or [{}]
        from .asr import to_simplified

        text = (items[0].get("text") or "").strip()
        return (to_simplified(text) if language in ("zh", "yue", "auto") else text), []
    return decode


def align_qwen(segs: list[Segment], wav: str | Path, language: str, settings: dict, progress: Prog | None = None,
               cancel=None) -> int:
    """Đo mốc từng chữ (extra["ct"]) cho các câu chưa có – câu từ bộ nhận dạng khác, câu nghe lại, câu đã sửa lời.
    Trả về số câu đo được."""
    from .align import CJK, _char_positions, times_from_words

    todo = [k for k, s in enumerate(segs) if CJK.search(s.text or "")
            and len(s.extra.get("ct") or []) != len(_char_positions(s.text))]
    if not todo:
        return 0
    items = []
    for k in todo:
        s = segs[k]
        a = max(0.0, s.start - 0.3)
        items.append([a, s.end + 0.3, s.text])
    r = worker(settings).call({"cmd": "align", "wav": _wav_path(wav), "items": items, "language": language},
                              progress, cancel)
    if r.get("align_error"):
        raise RuntimeError(r["align_error"])
    n = 0
    for k, (a, _b, _t), it in zip(todo, items, r.get("items") or []):
        ct = times_from_words(segs[k].text, it.get("words") or [], a)
        if ct:
            segs[k].extra["ct"] = [[x, y] for x, y in ct]
            n += 1
    return n
