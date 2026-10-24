"""Đoán đoạn HÁT trong phim (nhạc phim mở đầu / kết thúc, nhân vật hát) từ giọng đã tách – để không lồng tiếng lời
bài hát. Bộ nhận dạng Qwen3-ASR không gắn nhãn "đang hát" như SenseVoice, nên lời hát bị chép như lời thoại.

Cách đo (trên giọng đã tách khỏi nhạc, 16 kHz): hát có NỐT NGÂN – cao độ giữ gần như đứng yên ≥ 0,3 s (rung giọng nhẹ
vẫn tính), đoạn hữu thanh dài; nói thì cao độ trượt liên tục, mỗi tiếng ngắn. Đo trên bài hát thật: câu hát 57–94 %
thời gian hữu thanh nằm trong nốt ngân, lời thoại (phim hoạt hình tiếng Trung, sách nói tiếng Anh) phần lớn 0 %, vài câu
kêu kéo dài ("不要啊——") 40–76 % nhưng đứng lẻ. Vì vậy chỉ đánh dấu CỤM câu liền nhau (≥ 4 câu, ≥ 12 s) mà phần lớn
câu giống hát – câu kêu lẻ không bị đánh dấu.
"""
from __future__ import annotations

import re
import wave
from pathlib import Path

import numpy as np

SR = 16000
HOP = 0.02          # 20 ms mỗi khung cao độ
NOTE_LEN = 15       # nốt ngân: ≥ 15 khung (0,3 s) liền nhau
NOTE_STD = 0.45     # cao độ trong nốt lệch chuẩn < 0,45 nửa cung (rung giọng ±0,5 vẫn qua)
NOTE_SPAN = 1.6     # và chênh cao – thấp < 1,6 nửa cung
SUNG = 0.35         # câu "giống hát": ≥ 35 % thời gian hữu thanh là nốt ngân
GROUP_MEAN = 0.45   # cụm câu hát: trung bình ≥ 45 %
GROUP_SHARE = 0.7   #   ≥ 70 % số câu giống hát
GROUP_LINES = 4     #   ≥ 4 câu
GROUP_DUR = 12.0    #   kéo dài ≥ 12 s
GROUP_GAP = 8.0     # 2 câu cách nhau ≤ 8 s (đoạn nhạc dạo giữa bài) vẫn chung 1 cụm
EXTEND = 0.2        # cụm đã chắc là bài hát: nối thêm câu sát bên có ≥ 20 % nốt ngân (câu hát nhanh, đoạn rap)
FAST_CPS = 5.5      # câu > 5,5 chữ Hán / giây là nói, không phải hát
_HAN = re.compile(r"[㐀-鿿]")


def f0_frames(a: np.ndarray, sr: int = SR, fmin: float = 70.0, fmax: float = 900.0) -> tuple[np.ndarray, np.ndarray]:
    """Cao độ (Hz, NaN = không hữu thanh) + độ to mỗi khung 20 ms – hàm sai lệch chuẩn hoá kiểu YIN, tính cả lô bằng
    FFT."""
    a = np.asarray(a, np.float64)
    n, hop = int(0.04 * sr), int(HOP * sr)
    hi, lo = int(sr / fmin), max(2, int(sr / fmax))
    L = n + hi
    N = (len(a) - L) // hop if len(a) > L else 0
    if N <= 0:
        return np.zeros(0), np.zeros(0)
    F = a[np.arange(N)[:, None] * hop + np.arange(L)[None, :]]
    rms = np.sqrt((F[:, :n] ** 2).mean(1))
    nfft = 1 << int(np.ceil(np.log2(L + n)))
    corr = np.fft.irfft(np.fft.rfft(F, nfft) * np.conj(np.fft.rfft(F[:, :n], nfft)), nfft)[:, 1:hi + 1]
    sq = np.concatenate([np.zeros((N, 1)), np.cumsum(F * F, 1)], 1)
    lags = np.arange(1, hi + 1)
    d = sq[:, [n]] + (sq[:, lags + n] - sq[:, lags]) - 2 * corr
    cm = d * lags / np.maximum(np.cumsum(d, 1), 1e-12)
    below = cm[:, lo - 1:] < 0.15
    f0 = np.full(N, np.nan)
    for i in np.flatnonzero(below.any(1)):
        t = int(np.argmax(below[i])) + lo - 1
        while t + 1 < hi and cm[i, t + 1] < cm[i, t]:   # xuống tới đáy gần nhất
            t += 1
        f0[i] = sr / (t + 1)
    return f0, rms


def note_share(a: np.ndarray, sr: int = SR) -> float:
    """Tỉ lệ thời gian hữu thanh nằm trong nốt ngân (0..1)."""
    f0, rms = f0_frames(a, sr)
    if len(f0) < NOTE_LEN or not rms.any():
        return 0.0
    act = rms > 0.1 * np.percentile(rms, 95)
    v = ~np.isnan(f0) & act
    if v.sum() < NOTE_LEN:
        return 0.0
    st = 12 * np.log2(np.where(v, f0, 100.0) / 100.0)
    held = np.zeros(len(v), bool)
    i = 0
    while i < len(v):              # đoạn hữu thanh liền, cao độ không nhảy quá 1,5 nửa cung giữa 2 khung
        if not v[i]:
            i += 1
            continue
        j = i
        while j + 1 < len(v) and v[j + 1] and abs(st[j + 1] - st[j]) < 1.5:
            j += 1
        if j - i + 1 >= NOTE_LEN:
            w = np.lib.stride_tricks.sliding_window_view(st[i:j + 1], NOTE_LEN)
            ok = (w.std(1) < NOTE_STD) & (np.ptp(w, 1) < NOTE_SPAN)
            for k in np.flatnonzero(ok):
                held[i + k:i + k + NOTE_LEN] = True
        i = j + 1
    return float(held.sum() / v.sum())


def read_wav16(path: str | Path) -> np.ndarray:
    """Đọc file wav PCM 16 bit mono (bản nghe 16 kHz của tool) không nạp hết vào bộ nhớ (phim dài)."""
    with wave.open(str(path), "rb") as w:
        if w.getsampwidth() != 2 or w.getnchannels() != 1:
            raise ValueError("cần wav 16 bit mono")
        n = w.getnframes()
    size = Path(path).stat().st_size
    return np.memmap(path, np.int16, "r", offset=size - 2 * n, shape=(n,))   # khối data nằm cuối file (FFmpeg)


def guess_song_lines(segs: list, audio: np.ndarray, sr: int = SR) -> list[int]:
    """Chỉ số các câu nằm trong cụm câu đang hát. audio: giọng đã tách (float hoặc int16) cả phim."""
    scale = 1 / 32768.0 if audio.dtype == np.int16 else 1.0
    score: list[float] = []
    for s in segs:
        a = np.asarray(audio[int(s.start * sr):int(s.end * sr)], np.float32) * scale
        sc = note_share(a, sr) if s.end - s.start >= 0.6 else 0.0
        if len(_HAN.findall(s.text or "")) / max(0.3, s.end - s.start) > FAST_CPS:
            sc = min(sc, SUNG - 0.01)      # nói nhanh như lời thoại -> không phải hát
        score.append(sc)
    out: list[int] = []
    order = sorted(range(len(segs)), key=lambda k: segs[k].start)
    groups: list[list[int]] = []
    for k in order:
        if groups and segs[k].start - segs[groups[-1][-1]].end <= GROUP_GAP:
            groups[-1].append(k)
        else:
            groups.append([k])
    dur = [s.end - s.start for s in segs]
    for g in groups:
        # các dãy câu giống hát liền nhau trong cụm
        runs: list[list[int]] = []
        for pos, k in enumerate(g):
            if score[k] >= SUNG:
                if runs and runs[-1][-1] == pos - 1:
                    runs[-1].append(pos)
                else:
                    runs.append([pos])
        if not runs:
            continue
        # nối 2 dãy cách nhau ≤ 2 câu "không giống hát" (đoạn hát nhanh / hát nói giữa bài) – trừ khi 1 bên chỉ là 1 câu
        # ngắn (câu kêu kéo dài đứng lẻ cạnh bài hát không bị kéo vào)
        blocks = [runs[0][:]]
        for r in runs[1:]:
            cur = blocks[-1]
            gap = list(range(cur[-1] + 1, r[0]))

            def solid(rr: list[int]) -> bool:
                return len(rr) >= 2 or dur[g[rr[0]]] >= 3.0

            if len(gap) <= 2 and ((solid(cur) and solid(r)) or all(score[g[x]] >= EXTEND for x in gap)):
                cur += gap + r
            else:
                blocks.append(r[:])
        for blk in blocks:
            ks = [g[x] for x in blk]
            sung = sum(score[k] >= SUNG for k in ks)
            if len(ks) < GROUP_LINES or sung / len(ks) < GROUP_SHARE or \
                    segs[ks[-1]].end - segs[ks[0]].start < GROUP_DUR or float(np.mean([score[k] for k in ks])) < GROUP_MEAN:
                continue
            lo, hi = blk[0], blk[-1]
            while lo > 0 and score[g[lo - 1]] >= EXTEND:        # câu sát bên hát nhanh hơn / nốt ngắn hơn
                lo -= 1
            while hi + 1 < len(g) and score[g[hi + 1]] >= EXTEND:
                hi += 1
            out += g[lo:hi + 1]
    return sorted(set(out))


def mark_sung(segs: list, vocals_wav: str | Path) -> int:
    """Đánh dấu bài hát 🎵 các câu trong cụm đang hát (bỏ qua câu người dùng đã nói không phải bài hát)."""
    audio = read_wav16(vocals_wav)
    n = 0
    for k in guess_song_lines(segs, audio):
        x = segs[k]
        if not x.extra.get("song") and not x.extra.get("not_song"):
            x.extra["song"] = True
            x.extra["song_by"] = "pitch"
            n += 1
    return n
