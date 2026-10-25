"""Rút khoảng lặng + cắt đoạn bài hát: tìm các đoạn để cắt bỏ (người dùng xem lại, tích / bỏ tích, xem chỗ nối) rồi
gộp vào edit["cuts"] – phụ đề, lồng tiếng, lớp phủ tự dời theo khi xuất (giờ của chúng là giờ video gốc).

Khoảng lặng = chỗ không có lời thoại (giữa 2 câu phụ đề / giọng lồng), chỗ tiếng gốc nhỏ hơn ngưỡng (dB), hoặc cả hai.
Đoạn dài từ `min` tới `max` giây (max 0 = không giới hạn; dài hơn max thường là cảnh có chủ ý – để nguyên) được rút còn
`keep` giây (0 = xoá hẳn), phần giữ lại đặt ở giữa (chia đều 2 đầu) / sau câu trước / trước câu sau.
Đoạn bài hát = cụm câu 🎵 (nối các câu hát cách nhau ≤ 4 s) nới ra 2 bên tới sát lời thoại gần nhất (tối đa `song_pad`
giây – nhạc dạo / nhạc kết của bài)."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import numpy as np

HOP = 0.05                 # giây mỗi ô đo âm lượng
SR = 8000                  # đo âm lượng trên tiếng gốc 8 kHz 1 kênh (đủ cho độ to, giải mã nhanh)
SONG_JOIN = 4.0            # 2 câu hát cách nhau ≤ 4 s = cùng 1 bài
SPEECH_PAD = 0.3           # chừa 0,3 s sau / trước lời thoại khi cắt bài hát
PRESETS = {"light": {"min": 6.0, "keep": 2.0}, "medium": {"min": 4.0, "keep": 1.5},
           "strong": {"min": 2.0, "keep": 0.5}}
PRESET_NAMES = [("light", "Nhẹ – chỉ đoạn ≥ 6 s, còn 2 s"), ("medium", "Vừa – đoạn ≥ 4 s, còn 1,5 s"),
                ("strong", "Mạnh – đoạn ≥ 2 s, còn 0,5 s"), ("custom", "Tuỳ chỉnh")]
BY = [("speech", "Lời thoại (chỗ không có câu phụ đề / giọng lồng)"), ("volume", "Âm lượng tiếng gốc"),
      ("both", "Cả hai (không lời + tiếng nhỏ)")]
WHERE = [("split", "Chia đều 2 đầu"), ("after", "Ngay sau câu trước"), ("before", "Ngay trước câu sau")]
KINDS = {"gap": "Khoảng lặng", "head": "Đầu video", "tail": "Cuối video", "song": "🎵 Bài hát"}


def options(settings: dict | None, **kw) -> dict:
    from .config import DEFAULT_SETTINGS

    o = dict(DEFAULT_SETTINGS.get("silence") or {})
    o.update((settings or {}).get("silence") or {})
    o.update(kw)
    return o


def preset_of(opts: dict) -> str:
    for k, v in PRESETS.items():
        if abs(float(opts.get("min", 0)) - v["min"]) < 1e-6 and abs(float(opts.get("keep", 0)) - v["keep"]) < 1e-6:
            return k
    return "custom"


# ------------------------------------------------------------------ đo âm lượng tiếng gốc (1 lần, nhớ trong dự án)
def _sig(src: Path) -> dict:
    st = src.stat()
    from .ffmpeg import TIMELINE

    return {"path": str(src), "size": st.st_size, "mtime": int(st.st_mtime), "hop": HOP, "tl": TIMELINE}


def loudness(src: str | Path, work: str | Path, progress=None, cancel=None, duration: float = 0.0,
             name: str = "loud_50ms") -> np.ndarray:
    """Độ to (dBFS) mỗi HOP giây của tiếng gốc (hoặc giọng đã tách: name="voice_50ms") – giải mã 1 lần, lưu
    work/<name>.npy."""
    from . import ffmpeg as ff

    src, work = Path(src), Path(work)
    f, meta = work / f"{name}.npy", work / f"{name}.json"
    sig = _sig(src)
    if f.exists() and meta.exists():
        try:
            if json.loads(meta.read_text("utf-8")) == sig:
                return np.load(f).astype(np.float32)
        except Exception:  # noqa: BLE001
            pass
    n = int(SR * HOP)
    cmd = [ff.ffmpeg_bin(), "-hide_banner", "-nostdin", "-v", "error", "-i", str(src), "-vn"] + \
        ff.af_args(ff.audio_sync(src, SR)) + ["-ac", "1", "-ar", str(SR), "-f", "s16le", "-"]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                            creationflags=getattr(ff, "_NO_WINDOW", 0))
    out: list[np.ndarray] = []
    rest = b""
    done = 0
    try:
        while True:
            if cancel and cancel():
                proc.kill()
                from .ffmpeg import Cancelled

                raise Cancelled()
            buf = proc.stdout.read(n * 2 * 400)
            if not buf:
                break
            buf = rest + buf
            k = len(buf) // (2 * n) * (2 * n)
            rest = buf[k:]
            x = np.frombuffer(buf[:k], np.int16).astype(np.float32).reshape(-1, n) / 32768.0
            out.append(10 * np.log10(np.mean(x * x, axis=1) + 1e-10))
            done += x.shape[0]
            if progress and duration:
                progress(min(0.99, done * HOP / duration), "Đang đo âm lượng tiếng gốc (chỉ đo 1 lần)…" if
                         name == "loud_50ms" else "Đang tìm chỗ ngắt hơi trong giọng nói (chỉ đo 1 lần)…")
    finally:
        proc.stdout.close()
        proc.wait()
    db = np.concatenate(out) if out else np.zeros(0, np.float32)
    work.mkdir(parents=True, exist_ok=True)
    np.save(f, db.astype(np.float16))
    meta.write_text(json.dumps(sig), "utf-8")
    return db.astype(np.float32)


def has_loudness(src: str | Path, work: str | Path, name: str = "loud_50ms") -> bool:
    f, meta = Path(work) / f"{name}.npy", Path(work) / f"{name}.json"
    try:
        return f.exists() and json.loads(meta.read_text("utf-8")) == _sig(Path(src))
    except Exception:  # noqa: BLE001
        return False


# ------------------------------------------------------------------ đoạn
def _merge(spans: list) -> list[list[float]]:
    out: list[list[float]] = []
    for a, b in sorted((float(a), float(b)) for a, b in spans if float(b) > float(a)):
        if out and a <= out[-1][1] + 1e-3:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return out


def _complement(spans: list, duration: float) -> list[list[float]]:
    out, t = [], 0.0
    for a, b in _merge(spans):
        if a > t:
            out.append([t, min(a, duration)])
        t = max(t, b)
    if t < duration:
        out.append([t, duration])
    return [[a, b] for a, b in out if b - a > 1e-3]


def _intersect(x: list, y: list) -> list[list[float]]:
    out, i, j = [], 0, 0
    x, y = _merge(x), _merge(y)
    while i < len(x) and j < len(y):
        a, b = max(x[i][0], y[j][0]), min(x[i][1], y[j][1])
        if b > a:
            out.append([a, b])
        if x[i][1] < y[j][1]:
            i += 1
        else:
            j += 1
    return out


def _subtract(x: list, y: list) -> list[list[float]]:
    out = _merge(x)
    for c0, c1 in _merge(y):
        nxt = []
        for a, b in out:
            if c1 <= a or c0 >= b:
                nxt.append([a, b])
                continue
            if c0 > a:
                nxt.append([a, c0])
            if c1 < b:
                nxt.append([c1, b])
        out = nxt
    return [[a, b] for a, b in out if b - a > 1e-3]


def speech_spans(segs: list, with_songs: bool = True) -> list[list[float]]:
    """Đoạn có lời: thời gian câu phụ đề ∪ đoạn giọng lồng đã đặt (giọng có thể đọc dài hơn câu)."""
    out = []
    for s in segs:
        if not with_songs and s.extra.get("song"):
            continue
        out.append([s.start, s.end])
        at = s.extra.get("dub_at")
        if at and len(at) == 2 and not s.extra.get("nodub"):
            out.append([float(at[0]), float(at[1])])
    return _merge(out)


def quiet_spans(db: np.ndarray, thr_db: float, min_len: float = 0.3) -> list[list[float]]:
    """Đoạn tiếng gốc nhỏ hơn ngưỡng (bỏ đoạn ngắn hơn min_len)."""
    if db is None or not len(db):
        return []
    q = np.concatenate([[False], db < thr_db, [False]])
    d = np.diff(q.astype(np.int8))
    starts, ends = np.flatnonzero(d == 1), np.flatnonzero(d == -1)
    return [[round(a * HOP, 3), round(b * HOP, 3)] for a, b in zip(starts, ends) if (b - a) * HOP >= min_len]


def song_regions(segs: list) -> list[list[float]]:
    """Cụm câu 🎵 (nối câu hát cách nhau ≤ SONG_JOIN giây)."""
    out: list[list[float]] = []
    for s in sorted((x for x in segs if x.extra.get("song")), key=lambda x: x.start):
        if out and s.start - out[-1][1] <= SONG_JOIN:
            out[-1][1] = max(out[-1][1], s.end)
        else:
            out.append([s.start, s.end])
    return out


def _shrink(a: float, b: float, keep: float, where: str, kind: str) -> tuple[float, float]:
    """Phần cắt bỏ của khoảng [a, b] khi giữ lại `keep` giây."""
    k = min(max(0.0, keep), b - a)
    if kind == "head":           # trước câu đầu tiên: giữ phần sát câu
        return a, b - k
    if kind == "tail":           # sau câu cuối: giữ phần sát câu
        return a + k, b
    if where == "after":
        return a + k, b
    if where == "before":
        return a, b - k
    return a + k / 2, b - k / 2


def find_gaps(segs: list, duration: float, opts: dict, db: np.ndarray | None = None) -> list[dict]:
    """Các khoảng lặng nên rút: [{id, kind, a, b, cut: [đầu, cuối], on}] theo giờ video gốc."""
    by = opts.get("by", "speech")
    if by in ("volume", "both") and (db is None or not len(db)):
        raise ValueError("Chưa đo âm lượng tiếng gốc")
    if by == "speech":
        cand = _complement(speech_spans(segs), duration)
    elif by == "volume":
        cand = quiet_spans(db, float(opts.get("db", -30)))
    else:
        cand = _intersect(_complement(speech_spans(segs), duration), quiet_spans(db, float(opts.get("db", -30))))
    if opts.get("skip_songs", True):
        cand = _subtract(cand, song_regions(segs))
    head, tail = float(opts.get("skip_head") or 0), float(opts.get("skip_tail") or 0)
    if head > 0:
        cand = _subtract(cand, [[0.0, head]])
    if tail > 0:
        cand = _subtract(cand, [[max(0.0, duration - tail), duration]])
    lo, hi = float(opts.get("min", 4.0)), float(opts.get("max", 0) or 0)
    keep = float(opts.get("keep", 1.5))
    first = min((s.start for s in segs), default=duration)
    last = max((s.end for s in segs), default=0.0)
    out = []
    for a, b in cand:
        L = b - a
        if L < lo - 1e-6 or (hi > 0 and L > hi + 1e-6) or L <= keep + 0.05:
            continue
        kind = "head" if a <= 0.01 and b <= first + 0.01 else "tail" if b >= duration - 0.01 and a >= last - 0.01 \
            else "gap"
        ca, cb = _shrink(a, b, keep, opts.get("where", "split"), kind)
        if cb - ca < 0.1:
            continue
        out.append({"id": f"g{a:.1f}", "kind": kind, "a": round(a, 3), "b": round(b, 3),
                    "cut": [round(ca, 3), round(cb, 3)], "on": True})
    return out


def find_songs(segs: list, duration: float, opts: dict | None = None) -> list[dict]:
    """Đoạn bài hát để cắt hẳn: cụm câu 🎵 nới ra 2 bên tới sát lời thoại gần nhất (≤ song_pad giây)."""
    pad = float((opts or {}).get("song_pad", 15.0) or 0)
    talk = speech_spans(segs, with_songs=False)
    out = []
    for sa, sb in song_regions(segs):
        prev = max((b for a, b in talk if b <= sa + 0.05), default=None)
        nxt = min((a for a, b in talk if a >= sb - 0.05), default=None)
        ca = max(sa - pad, (prev + SPEECH_PAD) if prev is not None else 0.0, 0.0)
        cb = min(sb + pad, (nxt - SPEECH_PAD) if nxt is not None else duration, duration)
        if prev is None and sa - pad <= 0.5:
            ca = 0.0
        if nxt is None and sb + pad >= duration - 0.5:
            cb = duration
        ca, cb = min(ca, sa), max(cb, sb)
        n = sum(1 for s in segs if s.extra.get("song") and s.start >= sa - 0.01 and s.end <= sb + 0.01)
        out.append({"id": f"s{sa:.1f}", "kind": "song", "a": round(ca, 3), "b": round(cb, 3),
                    "cut": [round(ca, 3), round(cb, 3)], "on": True, "lines": n})
    return out


def merge_items(gaps: list[dict], songs: list[dict]) -> list[dict]:
    """Gộp danh sách khoảng lặng + bài hát (khoảng lặng nằm trong đoạn bài hát bị bỏ), xếp theo thời gian."""
    out = list(songs)
    for g in gaps:
        if not any(s["cut"][0] - 0.01 <= g["cut"][0] and g["cut"][1] <= s["cut"][1] + 0.01 for s in songs):
            out.append(g)
    return sorted(out, key=lambda it: it["cut"][0])


def new_cuts(existing: list, items: list[dict]) -> list[list[float]]:
    from .audiomix import merge_spans

    return merge_spans([list(c) for c in existing or []] + [list(it["cut"]) for it in items if it.get("on")])


def summary(items: list[dict], duration: float, trim: dict | None, cuts: list | None) -> tuple[int, int, float, float]:
    """(số đoạn đã tích, tổng số đoạn, độ dài video trước, sau khi áp dụng)."""
    from .video import keep_ranges

    before = sum(b - a for a, b in keep_ranges(duration, trim, cuts)) if duration else 0.0
    after = sum(b - a for a, b in keep_ranges(duration, trim, new_cuts(cuts, items))) if duration else 0.0
    return sum(1 for it in items if it.get("on")), len(items), before, after
