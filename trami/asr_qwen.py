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
    """Tắt tiến trình Qwen3-ASR (và FireRedASR2 nếu đang mở) – trả bộ nhớ GPU cho các bước sau (phân biệt người nói,
    lồng tiếng)."""
    import sys

    global _worker
    if _worker is not None:
        _worker.stop()
        _worker = None
    fr = sys.modules.get("trami.asr_firered")
    if fr is not None:
        fr.release()


def asr_worker(engine: str, settings: dict | None = None) -> QwenAsrWorker:
    """Tiến trình nghe theo bộ nghe: "firered" -> FireRedASR2, còn lại Qwen3-ASR (cùng giao thức)."""
    if engine == "firered":
        from . import asr_firered

        return asr_firered.worker(settings)
    return worker(settings)


def engine_name(engine: str) -> str:
    return "FireRedASR2" if engine == "firered" else "Qwen3-ASR"


def hotwords(settings: dict) -> str:
    """Tên riêng / thuật ngữ tiếng Trung trong bảng thuật ngữ (Cài đặt) -> gợi ý cho Qwen3-ASR nghe đúng.
    pipeline.recognize đặt sẵn `_asr_context` (names.context_text: tên của dự án + hồ sơ phim + tiêu đề video)."""
    if settings.get("_asr_context") is not None:
        return str(settings["_asr_context"])
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
    if item.get("lang"):
        seg.extra["lang"] = item["lang"]    # khúc nói tiếng khác tiếng chính của phim (2.1.3)
    ct = times_from_words(text, item.get("words") or [], s)
    if ct:
        seg.extra["ct"] = [[a, b] for a, b in ct]
        seg.start, seg.end = round(max(s, ct[0][0] - 0.05), 3), round(min(e, ct[-1][1] + 0.05), 3)
    return seg


CHUNK_SEC = 28.0   # khúc nghe mặc định (Cài đặt `asr_chunk_sec`): đủ dài để nghe trọn câu + câu bên cạnh
CHUNK_RANGE = (10.0, 60.0)
FIRERED_MAX = 55.0  # FireRedASR2-AED nghe tốt tới ~60 s / khúc (firered_worker.MAX_CLIP 58 s – dài hơn bị cắt)


def chunk_len(settings: dict, engine: str = "qwen3") -> float:
    """Độ dài tối đa 1 khúc nghe (giây) theo Cài đặt `asr_chunk_sec` (2.1.15). Dài hơn: nhiều ngữ cảnh hơn, ít chỗ cắt
    khúc; nhưng khúc dài dễ bỏ sót câu / lặp lời khi có nhạc và tốn bộ nhớ card hơn."""
    try:
        v = float(settings.get("asr_chunk_sec", CHUNK_SEC) or CHUNK_SEC)
    except (TypeError, ValueError):
        v = CHUNK_SEC
    v = min(CHUNK_RANGE[1], max(CHUNK_RANGE[0], v))
    return min(v, FIRERED_MAX) if engine == "firered" else v


STREAM_GROUP = 6   # chế độ vừa tách vừa dịch: nghe từng nhóm 6 khúc (≤ ~3 phút) rồi gửi câu sang bộ dịch ngay
SAVE_GROUP = 40    # không vừa tách vừa dịch: nghe từng nhóm 40 khúc (≤ ~18 phút), xong nhóm nào lưu tiến độ nhóm đó
PROGRESS_FILE = "asr_progress.json"   # work/asr_progress_<mã>.json: kết quả nghe + căn từng khúc của lần nghe dở


class Resume:
    """Tiến độ "1. Nghe & tách câu" (2.1.3): mỗi nhóm khúc nghe xong / căn xong được ghi ngay vào
    work/asr_progress_<mã>.json. Lần chạy sau cùng âm thanh + bộ nghe + mô hình + tiếng + gợi ý tên riêng -> dùng lại,
    chỉ nghe tiếp khúc còn thiếu. pipeline.recognize xoá file khi nghe xong trọn vẹn."""

    def __init__(self, wav: str | Path, engine: str, settings: dict, language: str, prompt: str):
        import hashlib

        w = Path(wav)
        model = "firered" if engine == "firered" else settings.get("qwen_asr_model") or MODELS[0][0]
        self.key = json.dumps([engine, model, language, prompt, w.name, file_sig(w)], ensure_ascii=False)
        # mỗi bộ nghe 1 file (bộ nghe thứ 2 của "Nghe 2 bộ rồi so" không ghi đè tiến độ của bộ chính)
        tag = hashlib.md5(self.key.encode()).hexdigest()[:10]
        self.path = w.parent / PROGRESS_FILE.replace(".json", f"_{tag}.json")
        self.heard: dict = {}
        self.words: dict = {}
        self.reused = 0
        try:
            d = json.loads(self.path.read_text(encoding="utf-8"))
            if d.get("key") == self.key:
                self.heard, self.words = dict(d.get("heard") or {}), dict(d.get("words") or {})
        except (OSError, ValueError):
            pass

    @staticmethod
    def span(s: float, e: float) -> str:
        return f"{float(s):.3f}-{float(e):.3f}"

    def word_key(self, s: float, e: float, text: str) -> str:
        return self.span(s, e) + "|" + text

    def save(self) -> None:
        import os

        tmp = self.path.with_name(self.path.name + ".tmp")
        try:
            tmp.write_text(json.dumps({"key": self.key, "heard": self.heard, "words": self.words},
                                      ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, self.path)
        except OSError:
            pass   # không ghi được (ổ đầy…) -> chỉ mất khả năng chạy tiếp, không làm hỏng việc nghe


def file_sig(path: Path) -> str:
    """Dấu của file âm thanh theo NỘI DUNG (cỡ + 3 mẫu 256 KB đầu / giữa / cuối) – bản 16 kHz bị tạo lại y hệt (lần
    nghe trước dừng giữa chừng nên dự án chưa ghi mốc giờ) vẫn nhận ra là cùng âm thanh."""
    import hashlib

    try:
        n = path.stat().st_size
        h = hashlib.md5(str(n).encode())
        with open(path, "rb") as f:
            for at in (0, max(0, n // 2 - 131072), max(0, n - 262144)):
                f.seek(at)
                h.update(f.read(262144))
        return h.hexdigest()
    except OSError:
        return ""


def clear_progress(work: str | Path) -> None:
    """Xoá tiến độ nghe dở (nghe xong trọn vẹn)."""
    for f in Path(work).glob(PROGRESS_FILE.replace(".json", "*")):
        try:
            f.unlink()
        except OSError:
            pass


FORCED_GAP = 0.3   # 2 khúc nghe liền nhau (nghỉ < 0,3 s): khúc bị cắt giữa lúc đang nói vì dài quá độ dài khúc
RELISTEN_PAD = 6.0  # nghe lại 6 s mỗi bên chỗ cắt khúc để bộ nghe thấy trọn câu
MERGE_MAX = 56.0    # khúc sau khi nối ≤ 56 s (= 2 khúc mặc định; bộ căn thời gian chạy 1 khúc / lô trên card 8 GB)


def relisten_cuts(raw: list[tuple[float, float, str]], wav: str | Path, settings: dict, language: str,
                  progress: Prog | None = None, cancel=None, stats: dict | None = None,
                  engine: str = "qwen3") -> list[tuple[float, float, str]]:
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
    r = asr_worker(engine, settings).call({"cmd": "asr", "wav": _wav_path(wav), "language": language,
                                           "prompt": hotwords(settings), "align": False,
                                           "spans": [[a, b] for _, a, b in todo]}, progress, cancel)
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
    merge_max = max(MERGE_MAX, 2 * chunk_len(settings, engine))
    for k, (s, e, t) in enumerate(raw):
        if out and (k - 1) in merge and e - out[-1][0] <= merge_max:
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
                    stats: dict | None = None, engine: str = "qwen3",
                    spans: list[tuple[float, float]] | None = None,
                    conf_out: list | None = None) -> list[Segment]:
    """Nghe cả video: chia đoạn có tiếng -> gộp thành khúc ≤ `chunk_len` (mặc định 28 s, nghe trọn câu) -> Qwen3-ASR ->
    nghe lại chỗ cắt khúc
    giữa lúc đang nói (`relisten_cuts`) -> mốc từng chữ. Mỗi khúc trả về 1 Segment có extra["ct"]; bước căn thời gian
    (align.refine) tách câu theo dấu câu + chỗ nghỉ.
    on_chunk: nghe theo từng nhóm khúc, nghe xong nhóm nào gọi on_chunk(các khúc đó) ngay (vừa tách vừa dịch).
    stats: nhận {"cuts", "merged", "fixed"} của bước nghe lại chỗ cắt khúc.
    engine: "qwen3" | "firered" (FireRedASR2 trả luôn mốc từng chữ khi nghe – chỉ khúc bị nối mới phải căn lại).
    spans: nghe đúng các khúc này (bộ nghe thứ 2 nghe lại đúng khúc của bộ thứ nhất) thay vì tự chia đoạn.
    conf_out: nhận [(giờ chữ, xác suất)] từng chữ Hán đã nghe (2.1.15) – để tính độ chắc từng câu sau khi tách câu
    (`recheck.assign_conf`) và nghe lại câu không chắc."""
    from .align import chunk_spans, split_params
    from .asr_funasr import load_wav16k

    if progress:
        progress(0.0, "Đang chia đoạn giọng nói…")
    if spans is not None:
        chunks = [(float(a), float(b)) for a, b in spans]
    else:
        audio = load_wav16k(wav)
        chunks = chunk_spans(_spans(audio, settings), audio, max_gap=split_params(settings)["chunk_gap"],
                             max_len=chunk_len(settings, engine))
        del audio
    if not chunks:
        return []
    simplify = language in ("zh", "yue", "auto")
    want_align = bool(settings.get("align_words", True))
    w = asr_worker(engine, settings)
    prompt = hotwords(settings)
    req = {"cmd": "asr", "wav": _wav_path(wav), "language": language, "prompt": prompt, "align": want_align,
           "conf": True}
    ck = Resume(wav, engine, settings, language, prompt)
    heard_words: dict[tuple, list] = {}   # FireRedASR2 trả mốc từng chữ ngay khi nghe
    # NGHE hết (chưa căn) -> nghe lại chỗ cắt khúc -> CĂN 1 lần cho cả phim (bộ căn chỉ nạp sau khi nghe xong).
    # Vừa tách vừa dịch: nghe theo từng nhóm khúc -> gửi câu nháp ngay. (2.0.64 nghe + căn xen kẽ từng nhóm -> từ nhóm 2
    # bộ căn 0.6B nằm sẵn trên card trong lúc bộ nghe 1.7B chạy -> đỉnh bộ nhớ cao hơn ~1,2 GB, card nhỏ tràn sang RAM)
    size = STREAM_GROUP if on_chunk else SAVE_GROUP
    groups = [chunks[i:i + size] for i in range(0, len(chunks), size)]
    a_end = 0.8 if want_align else 0.95
    raw: list[tuple[float, float, str]] = []
    have = sum(1 for c in chunks if ck.span(*c) in ck.heard)
    if have and progress:
        progress(0.0, f"Chạy tiếp lần nghe trước: đã nghe {have}/{len(chunks)} khúc, nghe tiếp phần còn lại…")
    for g, part in enumerate(groups):
        lo, hi = a_end * g / len(groups), a_end * (g + 1) / len(groups)
        pr = None
        if progress:
            def pr(x, m, lo=lo, hi=hi, g=g):
                progress(lo + (hi - lo) * x, m + (f" (phần {g + 1}/{len(groups)})" if len(groups) > 1 else ""))
        todo = [c for c in part if ck.span(*c) not in ck.heard]
        if todo:
            r = w.call(dict(req, spans=[list(c) for c in todo], align=False), pr, cancel)
            for c, it in zip(todo, r.get("items") or []):
                ck.heard[ck.span(*c)] = {"text": (it.get("text") or "").strip(), "words": it.get("words") or [],
                                         "conf": it.get("conf"), "cp": _strip_cp(it)}
            ck.save()
        ck.reused += len(part) - len(todo)
        new = []
        for s, e in part:
            it = ck.heard.get(ck.span(s, e)) or {}
            text = (it.get("text") or "").strip()
            raw.append((s, e, text))
            if it.get("words"):
                heard_words[(s, e, text)] = it["words"]
            if x := _segment(s, e, {"text": text}, simplify):
                new.append(x)
        if new and on_chunk:
            on_chunk(new)
    # khúc nói tiếng khác (câu tiếng Anh trong phim Trung…): nghe lại không ép tiếng (2.1.3)
    langs_of: dict[tuple, str] = {}
    try:
        fpr = (lambda x, m: progress(a_end, m)) if progress else None
        alt = {}
        if spans is not None:
            pass   # bộ nghe thứ 2 nghe lại đúng khúc của bộ chính ("Nghe 2 bộ rồi so") – không nghe lại tiếng khác
        elif any("alt" in (ck.heard.get(ck.span(s, e)) or {}) for s, e, _t in raw):   # chạy tiếp: đã nghe lại rồi
            for k, (s, e, _t) in enumerate(raw):
                a = (ck.heard.get(ck.span(s, e)) or {}).get("alt")
                if a:
                    alt[k] = (a["text"], a["lang"])
        else:
            alt = foreign_pass(raw, [(ck.heard.get(ck.span(s, e)) or {}).get("conf") for s, e, _t in raw], wav,
                               settings, language, fpr, cancel, engine)
            for k, (t, code) in alt.items():
                ck.heard.setdefault(ck.span(raw[k][0], raw[k][1]), {})["alt"] = {"text": t, "lang": code}
            if alt:
                ck.save()
        for k, (t, code) in alt.items():
            s, e, _old = raw[k]
            raw[k] = (s, e, t)
            langs_of[(s, e)] = code
    except Exception as ex:  # noqa: BLE001 – bước phụ: lỗi thì giữ bản nghe ép tiếng chính
        from .ffmpeg import Cancelled

        if isinstance(ex, Cancelled):
            raise
        from .config import log_error

        log_error("Nghe lại khúc tiếng khác")
    if stats is not None:
        stats["foreign"] = len(langs_of)
    if raw and settings.get("asr_guard", True):   # khúc chép lời gợi ý / lặp vòng -> nghe lại không gợi ý (2.1.8)
        gpr = (lambda x, m: progress(a_end, m)) if progress else None
        gst = guard_pass(raw, wav, settings, language, prompt, engine, ck, gpr, cancel)
        if stats is not None:
            stats.update(gst)
    if settings.get("asr_relisten", True) and len(raw) > 1:
        rpr = (lambda x, m: progress(a_end + 0.03 * x, m)) if progress else None
        keep = {k: v for k, v in langs_of.items()}
        raw = relisten_cuts(raw, wav, settings, language, rpr, cancel, stats, engine=engine)
        langs_of = {(s, e): keep[(s, e)] for s, e, _t in raw if (s, e) in keep}
    a_end = min(0.97, a_end + 0.03)
    words: list = [heard_words.get(x, []) or ck.words.get(ck.word_key(*x), []) for x in raw]
    if want_align and raw:
        todo = [k for k, (_s, _e, t) in enumerate(raw) if t and not words[k]]
        parts = [todo[i:i + SAVE_GROUP * 2] for i in range(0, len(todo), SAVE_GROUP * 2)]
        err = ""
        for g, part in enumerate(parts):
            lo, hi = a_end + (1 - a_end) * g / len(parts), a_end + (1 - a_end) * (g + 1) / len(parts)
            apr = (lambda x, m, lo=lo, hi=hi: progress(lo + (hi - lo) * x, m)) if progress else None
            r = w.call({"cmd": "align", "wav": _wav_path(wav), "language": language,
                        "items": [[raw[k][0], raw[k][1], raw[k][2]] for k in part]}, apr, cancel)
            for k, it in zip(part, r.get("items") or []):
                words[k] = it.get("words") or []
                if words[k]:
                    ck.words[ck.word_key(*raw[k])] = words[k]
            ck.save()
            if r.get("align_error"):
                err = r["align_error"]
                break   # bộ căn lỗi (thiếu bộ nhớ…) – các khúc còn lại dùng cách dự phòng
        if err and progress:
            progress(1.0, f"Không căn được thời gian bằng {engine_name(engine)} (" + err[:80] + ") – dùng cách dự phòng")
    if stats is not None:
        stats["resumed"] = ck.reused
    if conf_out is not None:
        conf_out.extend(_conf_points(raw, words, ck))
    return [x for (s, e, t), w in zip(raw, words)
            if (x := _segment(s, e, {"text": t, "words": w, "lang": langs_of.get((s, e))}, simplify))]


def _strip_cp(it: dict) -> list | None:
    """Xác suất từng ký tự (bộ nghe trả "cp") – chỉ giữ khi đúng số ký tự của lời đã nghe."""
    cp, text = it.get("cp"), (it.get("text") or "")
    if not cp or len(cp) != len(text):
        return None
    lead = len(text) - len(text.lstrip())
    return cp[lead:lead + len(text.strip())]


def _conf_points(raw: list[tuple[float, float, str]], words: list, ck: Resume) -> list[tuple[float, float]]:
    """[(giờ giữa chữ, xác suất)] mọi chữ Hán đã nghe: xác suất từng chữ khi bộ nghe trả về (Qwen3), không thì độ chắc
    cả khúc cho mọi chữ (FireRedASR2, khúc bị nối / sửa sau khi nghe); giờ theo mốc từng chữ, chưa căn thì rải đều."""
    from .align import _char_positions, times_from_words

    heard = []
    for key, it in ck.heard.items():
        try:
            a, b = (float(x) for x in key.split("-"))
        except ValueError:
            continue
        if it.get("conf") is not None:
            heard.append((a, b, float(it["conf"])))
    out: list[tuple[float, float]] = []
    for (s, e, t), w in zip(raw, words):
        pos = _char_positions(t or "")
        if not pos:
            continue
        it = ck.heard.get(ck.span(s, e)) or {}
        cp = it.get("cp") if (it.get("text") or "").strip() == t else None
        if cp and len(cp) == len(t):
            probs = [float(cp[i]) for i in pos]
        else:
            near = [c for a, b, c in heard if a < e and b > s]
            if not near:
                continue
            probs = [sum(near) / len(near)] * len(pos)
        ct = times_from_words(t, w or [], s)
        mids = [(x + y) / 2 for x, y in ct] if ct else [s + (e - s) * (k + 0.5) / len(pos) for k in range(len(pos))]
        out.extend(zip(mids, probs))
    return out


REP_PENALTY = 1.2   # nghe lại khúc nghi lặp vòng: phạt chữ đã viết (bộ nghe không lặp lại cụm vừa viết được nữa)


def guard_pass(raw: list[tuple[float, float, str]], wav: str | Path, settings: dict, language: str, prompt: str,
               engine: str = "qwen3", ck: Resume | None = None, progress: Prog | None = None,
               cancel=None) -> dict:
    """Chặn 2 lỗi của bộ nghe là mô hình ngôn ngữ (2.1.8), sửa `raw` tại chỗ:
    - khúc CHÉP lời gợi ý (`align.prompt_echo` – khúc chỉ có nhạc / không ai nói) -> nghe lại KHÔNG gợi ý; vẫn chép /
      không ra chữ -> bỏ khúc;
    - khúc có 1 cụm ≥ 5 chữ lặp liền nhau (`align.repeat_block`) -> nghe lại không gợi ý + phạt lặp (`REP_PENALTY`):
      cụm vẫn ra 2 lần = nói 2 lần thật -> giữ; ra 1 lần -> bỏ lần lặp (`align.decide_repeat`, giữ nguyên chữ còn lại).
    Kết quả nhớ trong tiến độ nghe (`ck`) – chạy tiếp không nghe lại. Trả {"echo", "repeat_fixed", "repeat_kept"}."""
    from .align import decide_repeat, prompt_echo, repeat_block

    st = {"echo": 0, "repeat_fixed": 0, "repeat_kept": 0}
    todo = []
    for k, (s, e, t) in enumerate(raw):
        if not t:
            continue
        g = (ck.heard.get(ck.span(s, e)) or {}).get("guard") if ck is not None else None
        if g:     # chạy tiếp lần nghe trước: đã quyết rồi
            raw[k] = (s, e, g.get("text", t))
            st[g.get("why", "repeat_kept")] = st.get(g.get("why", "repeat_kept"), 0) + 1
            continue
        if prompt_echo(t, prompt):
            todo.append((k, "echo", None))
        elif blk := repeat_block(t):
            todo.append((k, "repeat", blk))
    if not todo:
        return st
    if progress:
        progress(0.0, f"Nghe lại {len(todo)} khúc nghi chép lời gợi ý / lặp lời…")
    try:
        r = asr_worker(engine, settings).call({"cmd": "asr", "wav": _wav_path(wav), "language": language, "prompt": "",
                                               "align": False, "rep_penalty": REP_PENALTY,
                                               "spans": [[raw[k][0], raw[k][1]] for k, _w, _b in todo]},
                                              progress, cancel)
        items = r.get("items") or []
    except Exception as ex:  # noqa: BLE001 – nghe lại lỗi: khúc chép gợi ý vẫn bỏ, khúc lặp giữ nguyên
        from .ffmpeg import Cancelled

        if isinstance(ex, Cancelled):
            raise
        items = []
    for n, (k, what, blk) in enumerate(todo):
        s, e, old = raw[k]
        heard = (items[n].get("text") or "").strip() if n < len(items) else None
        if what == "echo":
            txt = "" if not heard or prompt_echo(heard, prompt) else heard
            why = "echo"
        elif heard is None:
            txt, why = old, "repeat_kept"
        else:
            txt, fixed = decide_repeat(old, heard, blk)
            why = "repeat_fixed" if fixed else "repeat_kept"
        raw[k] = (s, e, txt)
        st[why] += 1
        if ck is not None and heard is not None:
            ck.heard.setdefault(ck.span(s, e), {})["guard"] = {"text": txt, "why": why}
    if ck is not None:
        ck.save()
    return st


RESCUE_CONF = 0.5   # nghe lại đoạn sót: xác suất trung bình các chữ < 0,5 -> bộ nghe "đoán bừa" (nhạc / tiếng động)
LANG_CONF = 0.6     # khúc nghe (ép tiếng chính) độ tin cậy < 0,6 -> nghe lại để bộ nghe tự nhận tiếng
LANG_GAIN = 0.1     # ... nhận bản tiếng khác khi tin cậy hơn ít nhất 0,1
LANG_CODES = {"chinese": "zh", "cantonese": "yue", "english": "en", "japanese": "ja", "korean": "ko",
              "vietnamese": "vi", "thai": "th"}
LATIN_LINE = re.compile(r"[A-Za-z]{2,}(?:[\s'’-]+[A-Za-z]{2,})+")


def lang_code(name: str | None) -> str:
    n = (name or "").strip().lower()
    return LANG_CODES.get(n, n[:12])


def foreign_pass(raw: list[tuple[float, float, str]], confs: list, wav: str | Path, settings: dict, language: str,
                 progress: Prog | None = None, cancel=None, engine: str = "qwen3") -> dict:
    """Câu / khúc nói tiếng khác tiếng chính của phim (câu tiếng Anh trong phim Trung…), 2.1.3. Bộ nghe bị ép nghe
    tiếng chính thì nghe khúc tiếng khác kém tự tin -> khúc có độ tin cậy < LANG_CONF được nghe lại KHÔNG ép tiếng (bộ
    nghe tự nhận tiếng); ra tiếng khác mà tin cậy hơn -> dùng bản đó. Khúc chép ra toàn chữ Latin (≥ 2 từ, không chữ
    Hán) cũng là tiếng Anh. Trả {khúc k: (lời, mã tiếng)}."""
    from .align import CJK

    out: dict[int, tuple[str, str]] = {}
    if language in ("auto", "", None):
        return out
    for k, (_s, _e, t) in enumerate(raw):
        if language != "en" and t and not CJK.search(t) and LATIN_LINE.search(t):
            out[k] = (t, "en")
    if engine != "qwen3" or not settings.get("asr_lang_check", True):
        return out   # FireRedASR2 tự nghe lẫn Trung – Anh, không có chế độ tự nhận tiếng
    low = [k for k, (_s, _e, t) in enumerate(raw) if k not in out and t and confs[k] is not None
           and float(confs[k]) < LANG_CONF]
    if not low:
        return out
    if progress:
        progress(0.0, f"Nghe lại {len(low)} khúc nghe chưa chắc – xem có phải tiếng khác không…")
    r = asr_worker(engine, settings).call({"cmd": "asr", "wav": _wav_path(wav), "language": "auto",
                                           "prompt": hotwords(settings), "align": False, "conf": True,
                                           "spans": [[raw[k][0], raw[k][1]] for k in low]}, progress, cancel)
    for k, it in zip(low, r.get("items") or []):
        code = lang_code(it.get("lang"))
        text = (it.get("text") or "").strip()
        if text and code and code != language and not (language in ("zh", "yue") and code in ("zh", "yue")) \
                and float(it.get("conf") or 0) >= float(confs[k]) + LANG_GAIN:
            out[k] = (text, code)
    return out


def conf_tags(conf, threshold: float) -> list[str]:
    """Nhãn độ tin cậy cho hàm nghe 1 đoạn: ["conf=0.82"] (+ "lowconf" khi dưới ngưỡng – câu bị bỏ)."""
    if conf is None:
        return []
    return [f"conf={float(conf):.3f}"] + (["lowconf"] if float(conf) < threshold else [])


def qwen_decoder(settings: dict, language: str, work: Path,
                 engine: str = "qwen3") -> Callable[[np.ndarray], tuple[str, list[str]]]:
    """Hàm nghe 1 đoạn ngắn bằng Qwen3-ASR / FireRedASR2 – dùng để nghe lại các đoạn bị bộ chia đoạn bỏ sót.
    Nhãn trả về có độ tin cậy của bộ nghe (`conf_tags`): dưới ngưỡng `asr_rescue_conf` -> "lowconf" (2.1.3)."""
    from .tts import write_wav

    w = asr_worker(engine, settings)
    tmp = Path(work) / f"{engine}_clip.wav"
    thr = float(settings.get("asr_rescue_conf", RESCUE_CONF) or 0)

    def decode(clip: np.ndarray) -> tuple[str, list[str]]:
        if len(clip) < SR * 0.2:
            return "", []
        write_wav(tmp, clip.astype(np.float32), SR)
        r = w.call({"cmd": "asr", "wav": _wav_path(tmp), "spans": [[0.0, len(clip) / SR]], "language": language,
                    "prompt": hotwords(settings), "align": False, "conf": True})
        items = r.get("items") or [{}]
        from .asr import to_simplified

        text = (items[0].get("text") or "").strip()
        return (to_simplified(text) if language in ("zh", "yue", "auto") else text), \
            conf_tags(items[0].get("conf"), thr)
    return decode


def align_qwen(segs: list[Segment], wav: str | Path, language: str, settings: dict, progress: Prog | None = None,
               cancel=None, engine: str = "qwen3", pad: float = 0.3) -> int:
    """Đo mốc từng chữ (extra["ct"]) cho các câu chưa có – câu từ bộ nhận dạng khác, câu nghe lại, câu đã sửa lời.
    Trả về số câu đo được."""
    from .align import CJK, _char_positions, times_from_words

    todo = [k for k, s in enumerate(segs) if CJK.search(s.text or "")
            and len(s.extra.get("ct") or []) != len(_char_positions(s.text))]
    if not todo:
        return 0
    items = []
    for k in todo:   # pad: câu OCR (giờ hiện chữ, có thể lệch lúc nói ~0,5 s) căn trong khoảng rộng hơn
        s = segs[k]
        a = max(0.0, s.start - pad)
        items.append([a, s.end + pad, s.text])
    r = asr_worker(engine, settings).call({"cmd": "align", "wav": _wav_path(wav), "items": items,
                                           "language": language}, progress, cancel)
    if r.get("align_error"):
        raise RuntimeError(r["align_error"])
    n = 0
    for k, (a, _b, _t), it in zip(todo, items, r.get("items") or []):
        ct = times_from_words(segs[k].text, it.get("words") or [], a)
        if ct:
            segs[k].extra["ct"] = [[x, y] for x, y in ct]
            n += 1
    return n
