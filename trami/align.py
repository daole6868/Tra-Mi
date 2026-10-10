"""Căn thời gian phụ đề sát với lời thoại thật (tầng 1 + 2 của khớp lồng tiếng).

1. Mốc từng chữ: mô hình căn thời gian FunASR (fa-zh) cho biết mỗi chữ Hán nói ở giây nào. Không có / lỗi thì
   dự phòng bằng độ to của giọng (ưu tiên giọng đã tách khỏi nhạc nền) và chia chữ theo thời gian có tiếng.
2. Thu mốc đầu/cuối câu về đúng lúc có tiếng, cắt câu ở chỗ nghỉ thật (≥ 0,35 s).
3. `split_speaker_changes`: câu dài có 2 giọng khác nhau (ECAPA, cửa sổ trượt) -> tách thành 2 câu.
"""
from __future__ import annotations

import re
from typing import Callable

import numpy as np

from .subtitle import Segment

SR = 16000
CJK = re.compile(r"[㐀-鿿]")
GAP_SPLIT = 0.35     # chỗ nghỉ dài hơn mức này thì tách câu
PAD = 0.05           # chừa thêm trước/sau mỗi câu
Prog = Callable[[float, str], None]


# ------------------------------------------------------------------ đo độ to giọng

def voiced_mask(audio: np.ndarray, hop: float = 0.02) -> tuple[np.ndarray, float]:
    """Mảng True/False theo khung 20 ms: có tiếng nói hay không (ngưỡng tự thích nghi)."""
    n = int(SR * hop)
    if len(audio) < n:
        return np.zeros(0, bool), hop
    frames = audio[: len(audio) // n * n].reshape(-1, n)
    rms = np.sqrt((frames.astype(np.float32) ** 2).mean(axis=1) + 1e-12)
    loud = np.percentile(rms, 95) if len(rms) else 0
    thr = max(0.008, loud * 0.12)
    m = rms > thr
    # lấp lỗ ngắn < 0,12 s (hơi thở giữa chữ), bỏ tiếng lẻ < 0,06 s
    m = _close(m, int(0.12 / hop))
    m = _open(m, int(0.06 / hop))
    return m, hop


def _runs(m: np.ndarray, val: bool):
    i, n = 0, len(m)
    while i < n:
        if m[i] == val:
            j = i
            while j < n and m[j] == val:
                j += 1
            yield i, j
            i = j
        else:
            i += 1


def _close(m: np.ndarray, k: int) -> np.ndarray:
    m = m.copy()
    for a, b in list(_runs(m, False)):
        if a > 0 and b < len(m) and b - a <= k:
            m[a:b] = True
    return m


def _open(m: np.ndarray, k: int) -> np.ndarray:
    m = m.copy()
    for a, b in list(_runs(m, True)):
        if b - a < k:
            m[a:b] = False
    return m


# ------------------------------------------------------------------ mốc từng chữ

_fa_model = None


def _fa():
    global _fa_model
    if _fa_model is None:
        from .asr_funasr import _device, _model

        _fa_model = _model("fa-zh", model="fa-zh", device=_device("auto"))
    return _fa_model


def char_times_fa(clip: np.ndarray, chars: str) -> list[tuple[float, float]] | None:
    """Mốc (giây, tính từ đầu clip) của từng chữ Hán bằng mô hình căn thời gian; None nếu không chạy được."""
    try:
        res = _fa().generate(input=(clip, " ".join(chars)), data_type=("sound", "text"))
        ts = (res[0] or {}).get("timestamp") if res else None
        if not ts or len(ts) != len(chars):
            return None
        return [(a / 1000.0, b / 1000.0) for a, b in ts]
    except Exception:  # noqa: BLE001
        return None


def char_times_runs(mask: np.ndarray, hop: float, text: str, pos: list[int]) -> list[tuple[float, float]] | None:
    """Dự phòng khi không có mô hình căn chữ: các cụm có tiếng cách nhau ≥ GAP_SPLIT được coi là các vế câu; chia chữ
    cho từng vế theo độ dài (ưu tiên ranh giới ở dấu câu), trong mỗi vế chia đều."""
    n = len(pos)
    runs = list(_runs(mask, True))
    if not runs or n == 0:
        return None
    gap_k = int(GAP_SPLIT / hop)
    groups = [list(runs[0])]
    for a, b in runs[1:]:
        if a - groups[-1][1] >= gap_k:
            groups.append([a, b])
        else:
            groups[-1][1] = b
    if len(groups) == 1 or n < 2 * len(groups):
        return char_times_energy(mask, hop, n)
    # chỗ được phép cắt: sau chữ có dấu câu theo sau (ưu tiên), số chữ mỗi vế tỉ lệ thời lượng
    punct = {k for k in range(n - 1) if any(not CJK.match(ch) and not ch.isspace()
                                             for ch in text[pos[k] + 1:pos[k + 1]])}
    lens = np.array([b - a for a, b in groups], float)
    acc = np.cumsum(lens)[:-1] / lens.sum() * n
    cuts = []
    for x in acc:
        c = int(round(x)) - 1
        near = [k for k in punct if abs(k - c) <= max(1, int(n * 0.15))]
        if near:
            c = min(near, key=lambda k: abs(k - c))
        c = max(cuts[-1] + 1 if cuts else 0, min(n - 2, c))
        cuts.append(c)
    bounds = [0] + [c + 1 for c in cuts] + [n]
    out: list[tuple[float, float]] = []
    for g, (a, b) in enumerate(groups):
        k = bounds[g + 1] - bounds[g]
        if k <= 0:
            continue
        per = (b - a) / k
        for q in range(k):
            out.append(((a + q * per) * hop, (a + (q + 1) * per) * hop))
    return out if len(out) == n else char_times_energy(mask, hop, n)


def char_times_energy(mask: np.ndarray, hop: float, n: int) -> list[tuple[float, float]] | None:
    """Dự phòng: chia đều n chữ theo các khung có tiếng."""
    idx = np.where(mask)[0]
    if len(idx) == 0 or n == 0:
        return None
    per = len(idx) / n
    out = []
    for k in range(n):
        a = idx[min(len(idx) - 1, int(k * per))]
        b = idx[min(len(idx) - 1, max(int((k + 1) * per) - 1, int(k * per)))]
        out.append((a * hop, (b + 1) * hop))
    return out


# ------------------------------------------------------------------ căn + tách câu

def _char_positions(text: str) -> list[int]:
    return [i for i, ch in enumerate(text) if CJK.match(ch)]


def _split_text(text: str, pos: list[int], cuts: list[int]) -> list[str]:
    """Cắt text sau chữ Hán thứ cuts[k]; dấu câu đi theo phần trước."""
    parts, last = [], 0
    for c in cuts:
        end = pos[c] + 1
        while end < len(text) and not CJK.match(text[end]):
            end += 1
        parts.append(text[last:end].strip())
        last = end
    parts.append(text[last:].strip())
    return parts


def refine(segs: list[Segment], audio: np.ndarray, use_fa: bool = True, progress: Prog | None = None,
           cancel=None, duration: float = 0.0) -> list[Segment]:
    """Trả về danh sách câu mới: mốc sát lời thật, câu có chỗ nghỉ dài được tách. Câu không phải tiếng Trung giữ nguyên
    mốc (chỉ thu theo độ to)."""
    from .ffmpeg import Cancelled

    total = duration or len(audio) / SR
    out: list[Segment] = []
    fa_ok = use_fa
    for k, s in enumerate(segs):
        if cancel and cancel():
            raise Cancelled()
        a0 = max(0.0, s.start - 0.3)
        a1 = min(total, s.end + 0.3)
        clip = audio[int(a0 * SR):int(a1 * SR)]
        pos = _char_positions(s.text)
        mask, hop = voiced_mask(clip)
        times = None
        if fa_ok and pos and len(clip) > SR * 0.3:
            times = char_times_fa(clip, "".join(s.text[i] for i in pos))
            if times is None and k == 0:
                fa_ok = False   # mô hình không chạy -> các câu sau dùng cách dự phòng luôn
        if times is None and pos:
            # chỉ tính phần có tiếng nằm trong mốc gốc (không lấn sang câu bên cạnh)
            m2 = mask.copy()
            lo, hi = int((s.start - a0) / hop) - 3, int((s.end - a0) / hop) + 3
            m2[:max(0, lo)] = False
            m2[max(0, hi):] = False
            times = char_times_runs(m2, hop, s.text, pos)
        if not times:
            # không có chữ Hán / không thấy tiếng: chỉ thu mốc theo độ to
            vi = np.where(mask)[0]
            if len(vi):
                st = max(s.start - 0.15, a0 + vi[0] * hop - PAD)
                en = min(s.end + 0.15, a0 + (vi[-1] + 1) * hop + PAD)
                if en - st >= 0.3:
                    s.start, s.end = round(st, 3), round(en, 3)
            out.append(s)
            continue
        times = [(a0 + a, a0 + b) for a, b in times]
        cuts = [i for i in range(len(times) - 1)
                if times[i + 1][0] - times[i][1] >= GAP_SPLIT
                and i + 1 >= 2 and len(times) - (i + 1) >= 2]
        texts = _split_text(s.text, pos, cuts) if cuts else [s.text]
        bounds = [0] + [c + 1 for c in cuts] + [len(times)]
        for j, t in enumerate(texts):
            ta, tb = times[bounds[j]], times[bounds[j + 1] - 1]
            ns = Segment(round(max(0.0, ta[0] - PAD), 3), round(min(total, tb[1] + PAD), 3), t,
                         s.trans if not cuts else "", s.speaker, dict(s.extra))
            if ns.end - ns.start < 0.2:
                ns.end = round(ns.start + 0.2, 3)
            out.append(ns)
        if progress and k % 10 == 0:
            progress(k / max(1, len(segs)), f"Đang căn thời gian từng chữ {k}/{len(segs)}")
    # không để câu chồng lên nhau sau khi nới mốc
    out.sort(key=lambda x: x.start)
    for a, b in zip(out, out[1:]):
        if a.end > b.start:
            mid = round((a.end + b.start) / 2, 3) if a.end - b.start < 0.2 else b.start
            a.end = max(a.start + 0.1, min(a.end, mid))
            b.start = max(b.start, a.end)
    return out


# ------------------------------------------------------------------ tách khi đổi người nói

def speaker_change_points(emb_fn, clip: np.ndarray, win: float = 1.2, hop: float = 0.4,
                          thr: float = 0.3) -> list[float]:
    """Các thời điểm (giây trong clip) giọng đổi hẳn: độ giống giữa 2 cửa sổ liền kề tụt dưới ngưỡng."""
    n, h = int(win * SR), int(hop * SR)
    if len(clip) < 2 * n:
        return []
    starts = list(range(0, len(clip) - n + 1, h))
    embs = np.stack([emb_fn(clip[s:s + n]) for s in starts])
    embs /= np.linalg.norm(embs, axis=1, keepdims=True) + 1e-9
    pts = []
    step = int(round(win / hop))
    for i in range(len(starts) - step):
        j = i + step                  # 2 cửa sổ liền kề, không chồng nhau
        sim = float(embs[i] @ embs[j])
        if sim < thr:
            t = (starts[j]) / SR
            if not pts or t - pts[-1] > win:
                pts.append(t)
    return pts


def split_speaker_changes(segs: list[Segment], audio: np.ndarray, emb_fn, min_len: float = 2.4,
                          progress: Prog | None = None, cancel=None) -> list[Segment]:
    """Câu dài có chỗ đổi giọng -> tách đôi tại chữ gần chỗ đổi nhất (theo thời gian từng chữ ước lượng)."""
    from .ffmpeg import Cancelled

    out: list[Segment] = []
    for k, s in enumerate(segs):
        if cancel and cancel():
            raise Cancelled()
        if s.duration < min_len:
            out.append(s)
            continue
        clip = audio[int(s.start * SR):int(s.end * SR)]
        pts = speaker_change_points(emb_fn, clip)
        pos = _char_positions(s.text)
        if not pts or len(pos) < 4:
            out.append(s)
            continue
        mask, hop = voiced_mask(clip)
        times = char_times_energy(mask, hop, len(pos)) or []
        cuts = []
        for t in pts:
            c = min(range(len(times) - 1), key=lambda i: abs(times[i][1] - t)) if len(times) > 1 else None
            if c is not None and 1 <= c < len(pos) - 2 and c not in cuts:
                cuts.append(c)
        if not cuts:
            out.append(s)
            continue
        cuts.sort()
        texts = _split_text(s.text, pos, cuts)
        edges = [s.start] + [round(s.start + (times[c][1] + times[c + 1][0]) / 2, 3) for c in cuts] + [s.end]
        for j, t in enumerate(texts):
            out.append(Segment(edges[j], edges[j + 1], t, "", None, dict(s.extra)))
        if progress and k % 10 == 0:
            progress(k / max(1, len(segs)), f"Đang tìm chỗ đổi người nói {k}/{len(segs)}")
    return out
