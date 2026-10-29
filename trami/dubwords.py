"""Mốc từng từ THẬT của giọng lồng (2.1.24) – karaoke đổi màu đúng lúc từng từ được đọc thay vì ước lượng.

  - Edge TTS: dịch vụ trả sẵn mốc từng từ (WordBoundary) khi đọc -> ghi kèm file giọng, không tốn gì thêm;
  - VieNeu / OmniVoice / giọng khác: nghe lại đoạn giọng (đã biết lời) bằng Whisper `word_timestamps` (chỉ khi đã có mô
    hình Whisper trên máy, hoặc bấm "Đo mốc từng từ" ở tab Kiểu chữ – tải Whisper turbo ~1,6 GB lần đầu).

File `<file giọng>.words.json` = {"rel": "raw" | "trim", "src": "edge" | "whisper", "sig": [mtime, cỡ], "w": [[giây
bắt đầu, giây kết thúc, chữ]]} – mốc theo file giọng gốc ("raw") hoặc theo đoạn đã bỏ lặng 2 đầu ("trim"). Đổi sang giờ
bản lồng theo đúng cách đoạn giọng được đặt vào (bảng `placed`: vị trí, tốc độ, khoảng nghỉ chèn thêm, cắt đuôi) rồi
ghép vào từng từ của phụ đề theo chữ (karaoke.align_words) – đổi lời sau khi lồng thì từ không khớp tự đoán như cũ.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np

SUFFIX = ".words.json"
KARA_MODEL = "turbo"          # Whisper dùng để nghe lại giọng lồng (nhanh, nghe tiếng Việt tốt)


def path(clip: str | Path) -> Path:
    p = Path(clip)
    return p.with_name(p.name + SUFFIX)


def _sig(clip: str | Path) -> list[int]:
    st = Path(clip).stat()
    return [int(st.st_mtime), int(st.st_size)]


def save(clip: str | Path, words: list, rel: str = "raw", src: str = "edge") -> None:
    w = [[round(float(a), 3), round(float(b), 3), str(t)] for a, b, t in words if str(t).strip()]
    if not w:
        return
    try:
        path(clip).write_text(json.dumps({"rel": rel, "src": src, "sig": _sig(clip), "w": w}, ensure_ascii=False),
                              encoding="utf-8")
    except OSError:
        pass


def load(clip: str | Path) -> dict | None:
    """Mốc từ đã lưu của file giọng (file giọng đổi sau đó -> bỏ)."""
    f = path(clip)
    try:
        d = json.loads(f.read_text(encoding="utf-8"))
        if d.get("sig") and list(d["sig"]) != _sig(clip):
            return None
        return d if d.get("w") else None
    except (OSError, ValueError, TypeError):
        return None


def edge_jsonl(meta: str | Path) -> list[list]:
    """File metadata edge-tts (mỗi dòng 1 JSON {"type": "WordBoundary", "offset", "duration", "text"}, đơn vị 100 ns)."""
    out = []
    try:
        lines = Path(meta).read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    for ln in lines:
        try:
            m = json.loads(ln)
        except ValueError:
            continue
        if m.get("type") != "WordBoundary" or not str(m.get("text") or "").strip():
            continue
        a = float(m.get("offset") or 0) / 1e7
        out.append([round(a, 4), round(a + float(m.get("duration") or 0) / 1e7, 4), str(m["text"])])
    return out


def join(parts: list[str | Path], out: str | Path, runs: list[list[int]], sr: int) -> None:
    """Đọc từng vế rồi ghép (tts.join_parts): mốc từ của file ghép = mốc từng vế dời theo chỗ vế đó nằm trong file ghép
    (mỗi vế đã bỏ lặng 2 đầu). Thiếu mốc của 1 vế -> không ghi (karaoke tự đoán / nghe lại)."""
    from .tts import _decode_cached, _trim_silence, _trim_start

    words, pos = [], 0
    for j, f in enumerate(parts):
        d = load(f)
        a = _decode_cached(f)
        n = len(_trim_silence(a))
        if not d:
            return
        off = _trim_start(a) if d.get("rel") == "raw" else 0
        if j and j - 1 < len(runs):
            pos = int(runs[j - 1][1])
        for t0, t1, w in d["w"]:
            a0 = min(n, max(0, t0 * sr - off))
            a1 = min(n, max(a0, t1 * sr - off))
            words.append([(pos + a0) / sr, (pos + a1) / sr, w])
        pos += n
    save(out, words, "raw", "edge")


def trim_offset(clip: str | Path) -> float:
    from .tts import SR, _decode_cached, _trim_start

    return _trim_start(_decode_cached(clip)) / SR


def to_dub(entry: list, ts, vspeed: float = 1.0) -> np.ndarray:
    """Giây trên đoạn giọng ĐÃ BỎ LẶNG 2 đầu (tốc độ ×1) -> giây video gốc trong bản lồng, theo đúng mục `entry` của
    bảng đoạn giọng đã đặt [tên, mẫu bắt đầu, tốc độ, âm lượng, mẫu giữ khi cắt, độ dài(, khoảng nghỉ)]."""
    from .tts import SR

    st, speed, keep_n = int(entry[1]), float(entry[2]) or 1.0, int(entry[4] or 0)
    gaps = entry[6] if len(entry) > 6 and entry[6] else []
    q = np.asarray(ts, dtype=np.float64) * SR / speed           # mẫu trên đoạn giọng đã đổi tốc độ
    out = q.copy()
    last, base = 0.0, 0.0
    for g in sorted(tuple(int(v) for v in x) for x in gaps):    # như tts.insert_pauses: cắt, (đọc nhanh vế trước), lặng
        cut = max(float(g[0]), last)
        f = g[2] / 1000.0 if len(g) > 2 else 1.0
        ln = cut - last
        eff = ln / f if f > 1.001 and ln > SR // 10 else ln
        m = (q >= last) & (q < cut)
        out[m] = base + (q[m] - last) * (eff / ln if ln > 0 else 1.0)
        base += eff + max(0, g[1])
        last = cut
    m = q >= last
    out[m] = base + (q[m] - last)
    if keep_n:
        out = np.minimum(out, keep_n)
    return (st + out) / SR * float(vspeed or 1.0)


def line_words(clip: str | Path, entry: list | None, vspeed: float = 1.0) -> list[list] | None:
    """[[giây bắt đầu, giây kết thúc, chữ]] (giờ video gốc) các từ giọng lồng 1 câu đọc, hoặc None nếu chưa đo."""
    if not clip or entry is None or not Path(clip).exists():
        return None
    d = load(clip)
    if not d:
        return None
    w = d["w"]
    off = trim_offset(clip) if d.get("rel") == "raw" else 0.0
    a = np.array([max(0.0, float(x[0]) - off) for x in w])
    b = np.array([max(0.0, float(x[1]) - off) for x in w])
    da, db = to_dub(entry, a, vspeed), to_dub(entry, b, vspeed)
    return [[round(float(x), 3), round(float(max(x, y)), 3), str(t[2])] for x, y, t in zip(da, db, w)]


# ------------------------------------------------------------------ nghe lại bằng Whisper

def model_name(settings: dict | None = None) -> str | None:
    """Mô hình Whisper đã có trên máy để nghe lại giọng lồng (turbo trước, rồi mô hình nhận dạng ở Cài đặt)."""
    from .models import _whisper_cached

    for name in (KARA_MODEL, (settings or {}).get("asr_model") or "", "large-v3", "medium", "small"):
        try:
            if name and _whisper_cached(name):
                return name
        except Exception:  # noqa: BLE001
            continue
    return None


def listen(items: list[tuple[str, str, str]], settings: dict | None = None, progress=None, cancel=None,
           download: bool = False) -> dict:
    """Nghe lại các đoạn giọng [(file giọng, lời đã đọc, tiếng)] bằng Whisper (biết trước lời -> gợi ý đúng lời đó), ghi
    mốc từng từ. Chưa có mô hình: download=True -> tải Whisper turbo, không thì bỏ qua. Trả {"done", "skipped", "model"}."""
    from .ffmpeg import Cancelled
    from .tts import SR, _decode_cached, _trim_silence

    settings = settings or {}
    st = {"done": 0, "skipped": 0, "model": None}
    if not items:
        return st
    name = model_name(settings) or (KARA_MODEL if download else None)
    if not name:
        st["skipped"] = len(items)
        return st
    from .asr import get_model

    if progress:
        progress(0.0, "Đang nạp Whisper để đo mốc từng từ của giọng lồng" +
                 (" (lần đầu tải ~1,6 GB)" if not model_name(settings) else ""))
    if model_name(settings):
        model = get_model(name, settings.get("asr_device", "auto"), settings.get("asr_compute_type", "auto"))
    else:      # lần đầu: tải Whisper turbo vào data/models (hiện % / MB như tab Tài liệu)
        from .progress_hook import tqdm_progress

        lab = "Đang tải Whisper turbo (~1,6 GB, chỉ lần đầu)"
        with tqdm_progress(progress, 0.0, 0.1, lab, cancel, download=lab):
            model = get_model(name, settings.get("asr_device", "auto"), settings.get("asr_compute_type", "auto"))
    st["model"] = name
    for n, (clip, text, lang) in enumerate(items):
        if cancel and cancel():
            raise Cancelled()
        try:
            a = _trim_silence(_decode_cached(clip))
            if len(a) < SR // 5:
                st["skipped"] += 1
                continue
            m = int(len(a) * 16000 / SR)
            a16 = np.interp(np.linspace(0, len(a) - 1, m), np.arange(len(a)), a).astype(np.float32)
            segs, _ = model.transcribe(a16, language=(lang or "vi")[:2], task="transcribe", beam_size=1,
                                       word_timestamps=True, initial_prompt=text or None, vad_filter=False,
                                       condition_on_previous_text=False)
            words = [[w.start, w.end, w.word.strip()] for s in segs for w in (s.words or []) if w.word.strip()]
        except Cancelled:
            raise
        except Exception:  # noqa: BLE001 – 1 câu lỗi không chặn các câu khác (karaoke câu đó tự đoán)
            st["skipped"] += 1
            continue
        if words:
            save(clip, words, "trim", "whisper")
            st["done"] += 1
        else:
            st["skipped"] += 1
        if progress and n % 10 == 0:
            progress(n / len(items), f"Đang đo mốc từng từ của giọng lồng {n}/{len(items)}")
    return st
