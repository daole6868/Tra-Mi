"""Làm chậm CHỈ KHOẢNG NGHỈ (2.1.27) – chỗ nhân vật nói / giọng lồng đọc chạy đúng ×1 (không rè, khẩu hình khớp), chỉ
giãn khoảng lặng giữa các câu.

  - "gaps" (Chỉ khoảng nghỉ – đều): mọi khoảng nghỉ ≥ `gap_min` giây giữa 2 câu chạy chậm `gap_rate` (vd 0,8×).
  - "need" (Chỉ khoảng nghỉ – theo câu dài): chỉ giãn khoảng nghỉ ngay sau câu có giọng lồng dài hơn chỗ trống, giãn
    đúng số giây còn thiếu (≤ `need_max` giây, hình không chậm dưới `need_rate`); câu vừa chỗ giữ nguyên.

Bản đồ giờ `Warp`: giây video GỐC -> giây "giờ giãn" V (= giờ video xuất trước khi cắt đoạn). Mọi thứ theo giờ gốc
(đoạn cắt, đoạn tắt tiếng, chữ / logo, phụ đề, karaoke, video chèn) đổi sang V ở đầu bước xuất; hình dời mốc bằng
`setpts` (cây tìm kiếm nhị phân trên các mốc), tiếng gốc / nhạc nền / giọng gốc đã tách giãn bằng WSOLA giữ cao độ
(`WarpedTrack`: chỉ khoảng nghỉ được giãn, khi đọc tới; phần còn lại đọc thẳng bản giải mã), bản lồng ghi thẳng theo V.
"""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path

import numpy as np

SR = 44100
MODES = [("all", "Cả phim (theo ô Tốc độ)"), ("gaps", "Chỉ khoảng nghỉ – đều"),
         ("need", "Chỉ khoảng nghỉ – theo câu dài")]
DEFAULTS = {"gap_rate": 0.8, "gap_min": 0.3, "need_rate": 0.6, "need_max": 1.5}
PAD = 0.08            # giữ nguyên 0,08 s sát lời thoại (hơi thở, tiếng vang cuối câu)
MIN_SPAN = 0.05       # đoạn giãn ngắn hơn: bỏ
GAP = 0.04            # khoảng hở tối thiểu giữa 2 đoạn giọng lồng (= tts.GAP)
VERSION = 1           # đổi cách giãn âm thanh -> giãn lại


def mode_of(edit: dict | None) -> str:
    m = (edit or {}).get("speed_mode") or "all"
    return m if m in ("all", "gaps", "need") else "all"


def params(edit: dict | None) -> dict:
    e = edit or {}
    out = {}
    for k, v in DEFAULTS.items():
        try:
            out[k] = float(e.get(k, v) if e.get(k) is not None else v)
        except (TypeError, ValueError):
            out[k] = v
    out["gap_rate"] = min(1.0, max(0.25, out["gap_rate"]))
    out["need_rate"] = min(0.95, max(0.2, out["need_rate"]))
    out["gap_min"] = max(0.05, out["gap_min"])
    out["need_max"] = max(0.05, out["need_max"])
    return out


def signature(edit: dict | None) -> str:
    """Cách giãn đang chọn (bản lồng xếp theo cách nào) – "" = không giãn khoảng nghỉ."""
    m = mode_of(edit)
    if m == "all":
        return ""
    p = params(edit)
    keys = ("gap_rate", "gap_min") if m == "gaps" else ("need_rate", "need_max")
    return m + ":" + ",".join(f"{p[k]:.3f}" for k in keys)


def _clean(spans) -> list[tuple[float, float, float]]:
    out: list[tuple[float, float, float]] = []
    for s in sorted((tuple(float(v) for v in x[:3]) for x in spans or [] if len(x) >= 3), key=lambda x: x[0]):
        a, b, r = s
        if out and a < out[-1][1]:
            a = out[-1][1]
        if b - a >= 1e-3 and r > 0 and abs(r - 1) > 1e-4:
            out.append((a, b, r))
    return out


class Warp:
    """Bản đồ giờ tuyến tính từng đoạn. spans = [(a, b, r)]: đoạn [a, b] giây gốc chạy tốc độ r (r < 1: dài thành
    (b − a) / r). Ngoài các đoạn ×1. `uniform(s)` = cả phim tốc độ s (chỉ dùng cho trình phát)."""

    def __init__(self, spans=(), uniform_rate: float | None = None):
        self.uniform = uniform_rate is not None
        if self.uniform:
            r = min(4.0, max(0.25, float(uniform_rate)))
            self.spans = [(0.0, 1e7, r)]
        else:
            self.spans = _clean(spans)
        xs, ys = [0.0], [0.0]
        for a, b, r in self.spans:
            if a > xs[-1]:
                ys.append(ys[-1] + a - xs[-1])
                xs.append(a)
            ys.append(ys[-1] + (b - a) / r)
            xs.append(b)
        self.xs, self.ys = np.asarray(xs), np.asarray(ys)

    @classmethod
    def of(cls, spans) -> "Warp | None":
        w = cls(spans or [])
        return w if w.spans else None

    def __bool__(self) -> bool:
        return bool(self.spans)

    def _map(self, t, xs, ys):
        arr = np.asarray(t, dtype=np.float64)
        out = np.interp(arr, xs, ys)
        hi = arr > xs[-1]
        if np.any(hi):
            out = np.where(hi, ys[-1] + (arr - xs[-1]), out)
        lo = arr < 0
        if np.any(lo):
            out = np.where(lo, arr, out)
        return float(out) if np.ndim(out) == 0 else out

    def fwd(self, t):
        """Giây gốc -> giây giờ giãn."""
        return self._map(t, self.xs, self.ys)

    def inv(self, v):
        """Giây giờ giãn -> giây gốc."""
        return self._map(v, self.ys, self.xs)

    def rate_at(self, t: float) -> float:
        for a, b, r in self.spans:
            if a <= t < b:
                return r
            if a > t:
                break
        return 1.0

    def next_edge(self, t: float) -> float | None:
        for a, b, _r in self.spans:
            if a > t + 1e-4:
                return a
            if b > t + 1e-4:
                return b
        return None

    def added(self) -> float:
        """Giây video dài thêm."""
        return 0.0 if self.uniform else sum((b - a) * (1 / r - 1) for a, b, r in self.spans)

    def to_list(self) -> list[list[float]]:
        return [[round(a, 3), round(b, 3), round(r, 4)] for a, b, r in self.spans]

    def key(self) -> str:
        return hashlib.md5(json.dumps(self.to_list()).encode()).hexdigest()[:12]

    def map_ranges(self, spans) -> list:
        """Danh sách [a, b(, …)] giây gốc -> giờ giãn (giữ nguyên phần tử sau)."""
        out = []
        for x in spans or []:
            x = list(x)
            out.append([round(self.fwd(float(x[0])), 3), round(self.fwd(float(x[1])), 3)] + x[2:])
        return out

    def setpts_expr(self, s0: float, s1: float) -> str:
        """Biểu thức setpts (giây) cho luồng hình đã -ss tới s0 (giây gốc): W(T + s0) − W(s0). Cây tìm kiếm nhị phân
        trên các mốc (mỗi khung chỉ xét ~log2(số đoạn) phép so sánh – hàng nghìn khoảng nghỉ vẫn nhanh)."""
        x0 = self.fwd(s0)
        pts = [(float(x) - s0, float(y) - x0) for x, y in zip(self.xs, self.ys) if s0 - 1 <= x <= s1 + 1]
        if not pts:
            return "T"
        # đoạn i: từ mốc i tới mốc i+1, độ dốc theo bản đồ; trước mốc đầu / sau mốc cuối: ×1 (ngoài mọi khoảng giãn)
        segs = []
        for i, (x, y) in enumerate(pts):
            if i + 1 < len(pts):
                nx, ny = pts[i + 1]
                slope = (ny - y) / (nx - x) if nx > x else 1.0
            else:
                slope = 1.0
            segs.append((x, y, slope))
        head = f"(T{pts[0][1] - pts[0][0]:+.6f})"

        def leaf(x, y, k):
            return f"({y:.6f}+(T{-x:+.6f})*{k:.6f})" if abs(k - 1) > 1e-9 else f"(T{y - x:+.6f})"

        def node(lo, hi):
            if hi - lo == 1:
                return leaf(*segs[lo])
            m = (lo + hi) // 2
            return f"if(lt(T,{segs[m][0]:.6f}),{node(lo, m)},{node(m, hi)})"

        return f"if(lt(T,{segs[0][0]:.6f}),{head},{node(0, len(segs))})"


def speech_spans(segs, pad: float = PAD) -> list[tuple[float, float]]:
    """Các đoạn có lời (mọi câu kể cả 🎵 / bỏ lồng – không giãn tiếng hát / lời thoại), gộp đoạn chồng."""
    sp = sorted((float(s.start), float(s.end)) for s in segs if (s.text or s.trans or "").strip() and s.end > s.start)
    out: list[list[float]] = []
    for a, b in sp:
        if out and a <= out[-1][1] + 1e-3:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return [(a, b) for a, b in out]


def plan_gaps(segs, total: float, rate: float, min_gap: float, pad: float = PAD) -> list[list[float]]:
    """Cách "đều": mọi khoảng nghỉ GIỮA 2 câu dài ≥ min_gap giây chạy tốc độ rate (đầu / cuối video giữ nguyên)."""
    if rate >= 0.999:
        return []
    sp = speech_spans(segs)
    out = []
    for (_a0, e0), (s1, _b1) in zip(sp, sp[1:]):
        if s1 - e0 < max(min_gap, MIN_SPAN):
            continue
        a, b = e0 + pad, s1 - pad
        if b - a >= MIN_SPAN and (not total or b <= total):
            out.append([round(a, 3), round(b, 3), rate])
    return out


def plan_need(items: list[dict], segs, total: float, rate_min: float, max_extra: float,
              pad: float = PAD) -> list[list[float]]:
    """Cách "theo câu dài": items = đoạn giọng lồng {"k", "want" (giây gốc nên bắt đầu), "dur" (giây ở ×1), "pause"}.
    Giọng câu i tràn qua lúc câu lồng tiếp theo bắt đầu -> giãn các khoảng nghỉ (không có lời) nằm giữa cuối lời câu i
    và câu lồng tiếp theo, cùng 1 tốc độ, đủ số giây còn thiếu (≤ max_extra, hình không chậm dưới rate_min)."""
    its = sorted(items, key=lambda x: (float(x["want"]), int(x["k"])))
    sp = speech_spans(segs)
    out = []
    for i, x in enumerate(its[:-1]):
        nx = its[i + 1]
        lim = float(nx["want"]) - max(GAP, float(nx.get("pause") or 0))
        over = float(x["want"]) + float(x["dur"]) - lim
        if over < 0.05:
            continue
        e = float(segs[int(x["k"])].end)
        lo, hi = max(e, float(x["want"])), float(nx["want"])
        free = []
        prev = lo
        for a, b in sp:               # chỗ không có lời giữa lo và hi
            if b <= prev:
                continue
            if a >= hi:
                break
            if a > prev:
                free.append((prev, min(a, hi)))
            prev = max(prev, b)
        if prev < hi:
            free.append((prev, hi))
        free = [(a + pad, b - pad) for a, b in free if b - a - 2 * pad >= MIN_SPAN]
        g = sum(b - a for a, b in free)
        if g < MIN_SPAN:
            continue
        ext = min(over, max_extra, g * (1 / rate_min - 1))
        if ext < 0.03:
            continue
        r = g / (g + ext)
        out += [[round(a, 3), round(b, 3), round(r, 4)] for a, b in free]
    return out


# ------------------------------------------------------------------ giãn âm thanh giữ cao độ (WSOLA, 2 kênh cùng nhịp)
HOP = 1024              # 23 ms ở 44,1 kHz – khung 46 ms: mượt cho nhạc nền / tiếng động
TOL = 441               # dò chỗ khớp ±10 ms
DEC = 4                 # dò trên tín hiệu thưa 1/4


def _hann(n: int) -> np.ndarray:
    return (0.5 - 0.5 * np.cos(2 * np.pi * np.arange(n) / n)).astype(np.float32)


def stretch_span(x: np.ndarray, ia: int, ib: int, L: int) -> np.ndarray:
    """Đoạn x[ia:ib] (int16 / float, [khung, 2]) kéo dài thành đúng L khung, giữ cao độ, nối liền mạch 2 đầu (khung đầu
    trùng x[ia…], khung cuối trùng …x[ib]) -> float32 [L, 2]."""
    n_in = ib - ia
    h = HOP
    N = 2 * h
    K = int(round(L / h))
    if n_in < N or K < 3:          # đoạn quá ngắn: kéo thẳng (vài chục ms nền – không nghe ra)
        seg = np.asarray(x[ia:ib], dtype=np.float32)
        if len(seg) < 2:
            return np.zeros((L, 2), np.float32)
        src = np.linspace(0, len(seg) - 1, L)
        return np.stack([np.interp(src, np.arange(len(seg)), seg[:, c]) for c in (0, 1)], axis=1).astype(np.float32)
    lo = ia - h - TOL
    hi = ib + h + TOL + N
    base = max(0, lo)
    buf = np.asarray(x[base:min(len(x), hi)], dtype=np.float32)
    pre = base - lo
    post = hi - lo - pre - len(buf)
    if pre > 0 or post > 0:
        buf = np.concatenate([np.zeros((max(0, pre), 2), np.float32), buf, np.zeros((max(0, post), 2), np.float32)])
    off = -lo                       # chỉ số trong buf của khung gốc 0
    mono = buf.mean(axis=1)
    w = _hann(N)[:, None]
    rate = (n_in - h) / max(1, (K - 1) * h)    # khung K−1 bắt đầu đúng ib − h
    y = np.zeros(((K + 1) * h + N, 2), np.float32)
    # khung −1: ngữ cảnh ngay trước ia (nửa sau trùng nửa đầu khung 0 -> chỗ nối liền mạch)
    p = ia - h
    y[0:N] += buf[off + p:off + p + N] * w
    prev = ia
    y[h:h + N] += buf[off + ia:off + ia + N] * w
    for k in range(1, K):
        ideal = int(round(ia + k * h * rate))
        if k == K - 1:
            pos = ib - h            # khung cuối: đúng chỗ -> phần sau nối thẳng vào x[ib…]
        else:
            nat = prev + h
            tgt = mono[off + nat:off + nat + N:DEC]
            reg = mono[off + ideal - TOL:off + ideal + TOL + N:DEC]
            c = np.correlate(reg, tgt, "valid")
            pos = ideal - TOL + int(np.argmax(c)) * DEC if len(c) else ideal
        y[(k + 1) * h:(k + 1) * h + N] += buf[off + pos:off + pos + N] * w
        prev = pos
    out = y[h:h + K * h]
    if len(out) != L:               # làm tròn số khung: co giãn rất nhẹ cho đúng độ dài (≤ nửa khung)
        src = np.linspace(0, len(out) - 1, L)
        out = np.stack([np.interp(src, np.arange(len(out)), out[:, c]) for c in (0, 1)], axis=1)
    return out.astype(np.float32)


PIECE_SEC = 3.0          # khoảng nghỉ dài chia mẩu ≤ 3 s (giãn khi cần, mỗi mẩu ~0,1 s – trình phát không bị khựng)
CACHE_PIECES = 64       # số mẩu đã giãn giữ trong RAM


class WarpedTrack:
    """Lớp âm thanh theo giờ giãn, đọc như mảng int16 [khung, 2]: phần không giãn đọc thẳng bản giải mã gốc (memmap),
    khoảng nghỉ được giãn (giữ cao độ) KHI ĐỌC TỚI rồi nhớ lại – đổi mức làm chậm có tác dụng ngay, không phải tính trước
    cả phim, không ghi thêm file nào ra đĩa. Các mẩu nối liền mạch với nhau và với phần không giãn (stretch_span)."""

    def __init__(self, base: np.ndarray, warp: Warp, key: str = ""):
        import threading
        from collections import OrderedDict

        self.base, self.warp, self.key = base, warp, key
        n = len(base)
        self.index: list[tuple[int, int, int, int]] = []    # (V đầu, V cuối, khung gốc đầu, khung gốc cuối)
        for a, b, _r in warp.spans:
            ia, ib = _frames(a), min(n, _frames(b))
            if ib - ia < 2:
                continue
            k = max(1, int(math.ceil((ib - ia) / (PIECE_SEC * SR))))
            cuts = [ia + (ib - ia) * j // k for j in range(k + 1)]
            for x0, x1 in zip(cuts, cuts[1:]):
                self.index.append((_frames(warp.fwd(x0 / SR)), _frames(warp.fwd(x1 / SR)), x0, x1))
        last_v, last_s = (self.index[-1][1], self.index[-1][3]) if self.index else (0, 0)
        self.n = last_v + max(0, n - last_s) if self.index else n
        self.dtype = base.dtype
        self.shape = (self.n, 2)
        self._starts = [e[0] for e in self.index]
        self._cache: OrderedDict = OrderedDict()
        self._lock = threading.Lock()

    def __len__(self) -> int:
        return self.n

    def _src(self, v: int, j: int) -> int:
        """Khung gốc của khung V nằm ngoài đoạn giãn (j = mẩu giãn cuối cùng trước v, −1 = chưa có)."""
        if j < 0:
            return v
        return self.index[j][3] + (v - self.index[j][1])

    def piece(self, j: int) -> np.ndarray:
        with self._lock:
            got = self._cache.get(j)
            if got is not None:
                self._cache.move_to_end(j)
                return got
        v0, v1, ia, ib = self.index[j]
        y = np.clip(stretch_span(self.base, ia, ib, v1 - v0), -32768, 32767).astype(np.int16)
        with self._lock:
            self._cache[j] = y
            while len(self._cache) > CACHE_PIECES:
                self._cache.popitem(last=False)
        return y

    def __getitem__(self, sl):
        import bisect

        if not isinstance(sl, slice):
            raise TypeError("chỉ đọc theo đoạn liền")
        a, b, _ = sl.indices(self.n)
        out = np.zeros((max(0, b - a), 2), np.int16)
        pos = a
        while pos < b:
            j = bisect.bisect_right(self._starts, pos) - 1
            if j >= 0 and pos < self.index[j][1]:          # trong khoảng giãn
                v0, v1 = self.index[j][:2]
                e = min(b, v1)
                pc = self.piece(j)
                out[pos - a:e - a] = pc[pos - v0:e - v0]
                pos = e
                continue
            nxt = self._starts[j + 1] if j + 1 < len(self.index) else self.n
            e = min(b, nxt)
            s = self._src(pos, j)
            seg = self.base[s:s + (e - pos)]
            out[pos - a:pos - a + len(seg)] = seg
            pos = e
        return out


def _frames(t: float) -> int:
    return int(round(t * SR))


def warped_track(raw: str | Path, cache_dir: str | Path | None, name: str, warp: Warp, progress=None,
                 cancel=None) -> WarpedTrack:
    """Lớp `raw` (PCM int16 2 kênh 44,1 kHz – audiomix.decode_track) theo giờ giãn (khoảng nghỉ giãn khi đọc tới)."""
    from .audiomix import load_raw

    raw = Path(raw)
    st = raw.stat()
    key = hashlib.md5(json.dumps([raw.name, st.st_size, int(st.st_mtime), warp.to_list(), VERSION]).encode()) \
        .hexdigest()[:14]
    return WarpedTrack(load_raw(raw), warp, key)


def write_flac(track: WarpedTrack, out_dir: str | Path, name: str, progress=None, cancel=None) -> Path:
    """Ghi lớp đã giãn ra FLAC (xuất video đọc bằng FFmpeg; nhỏ hơn wav ~1/2). Có sẵn đúng bản -> dùng lại."""
    import subprocess

    from . import ffmpeg as ff

    d = Path(out_dir)
    d.mkdir(parents=True, exist_ok=True)
    out = d / f"{name}_{getattr(track, 'key', 'x')}.flac"
    if out.exists() and out.stat().st_size > 0:
        return out
    tmp = d / f"{name}_{getattr(track, 'key', 'x')}.tmp.flac"
    cmd = [ff.ffmpeg_bin(), "-hide_banner", "-y", "-v", "error", "-f", "s16le", "-ar", str(SR), "-ac", "2", "-i", "-",
           "-c:a", "flac", "-compression_level", "2", str(tmp)]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                            creationflags=ff._NO_WINDOW)
    step = SR * 30
    try:
        for p in range(0, track.n, step):
            if cancel and cancel():
                raise ff.Cancelled()
            proc.stdin.write(np.ascontiguousarray(track[p:min(track.n, p + step)]).astype("<i2").tobytes())
            if progress:
                progress(p / max(1, track.n), f"Đang chuẩn bị tiếng '{name}' đã giãn khoảng nghỉ")
        proc.stdin.close()
        err = proc.stderr.read().decode("utf-8", "replace")
        proc.wait()
    except BaseException:
        proc.kill()
        proc.wait()
        tmp.unlink(missing_ok=True)
        raise
    if proc.returncode != 0 or not tmp.exists():
        tmp.unlink(missing_ok=True)
        raise ff.FFmpegError("Không ghi được tiếng đã giãn: " + err[-400:])
    tmp.replace(out)
    for old in d.glob(f"{name}_*.flac"):
        if old != out:
            try:
                old.unlink()
            except OSError:
                pass
    return out


def map_segments(segs, warp: Warp):
    """Bản sao các câu theo giờ giãn (giờ câu + mốc karaoke kv / kw)."""
    from .subtitle import Segment

    out = []
    for s in segs:
        ex = dict(s.extra)
        for k in ("kv",):
            if ex.get(k):
                ex[k] = [[round(warp.fwd(float(a)), 3), round(warp.fwd(float(b)), 3)] for a, b in ex[k]]
        if ex.get("kw"):
            ex["kw"] = [[round(warp.fwd(float(w[0])), 3), round(warp.fwd(float(w[1])), 3)] + list(w[2:])
                        for w in ex["kw"]]
        out.append(Segment(round(warp.fwd(s.start), 3), round(warp.fwd(s.end), 3), s.text, s.trans, s.speaker, ex))
    return out


def map_spec(spec: dict, warp: Warp) -> dict:
    """Bản sao spec xuất video với mọi mốc giờ gốc đổi sang giờ giãn."""
    sp = json.loads(json.dumps(spec))
    tr = sp.get("trim") or {}
    if tr:
        sp["trim"] = {"start": round(warp.fwd(float(tr.get("start") or 0)), 3),
                      "end": round(warp.fwd(float(tr["end"])), 3) if tr.get("end") else 0}
    for k in ("cuts", "mute_spans"):
        if sp.get(k):
            sp[k] = warp.map_ranges(sp[k])
    for L in sp.get("layers") or []:
        if L.get("start") is not None:
            L["start"] = round(warp.fwd(float(L.get("start") or 0)), 3)
        if L.get("end"):
            L["end"] = round(warp.fwd(float(L["end"])), 3)
    for it in sp.get("inserts") or []:
        it["at"] = round(warp.fwd(float(it.get("at") or 0)), 3)
    d = sp.get("dub") or {}
    if d.get("song_spans"):
        d["song_spans"] = warp.map_ranges(d["song_spans"])
    sp["speed"] = 1.0
    return sp


def summary(warp: Warp | None, mode: str) -> str:
    if mode == "all":
        return ""
    if not warp:
        return ("chưa có khoảng nghỉ nào cần giãn" if mode == "need" else "không có khoảng nghỉ nào đủ dài để giãn")
    add = warp.added()
    return f"giãn {len(warp.spans)} khoảng nghỉ, video dài thêm {add:.1f} s" if add < 120 else \
        f"giãn {len(warp.spans)} khoảng nghỉ, video dài thêm {add / 60:.1f} phút"

