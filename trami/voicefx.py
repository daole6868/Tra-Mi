"""Hiệu ứng giọng đọc – chỉ cần numpy (máy không có scipy / rubberband vẫn chạy).

Đổi CAO ĐỘ và ÂM SẮC (formant – độ "to người" của giọng) tách riêng, luôn giữ nguyên thời lượng câu:
  1. kéo dài / co câu bằng WSOLA (ghép từng khung 30 ms ở chỗ sóng khớp nhất – không méo như phase vocoder) rồi lấy
     mẫu lại về độ dài cũ -> cao độ và âm sắc cùng đổi `pitch` nửa cung (chỉ thế thì người lớn nghe như sóc chuột);
  2. âm sắc cần khác cao độ (bé gái: cao độ +6, âm sắc +3,5) -> dời đường bao phổ (đo bằng cepstrum) từng khung 43 ms:
     chỉ đổi biên độ, giữ pha = 1 bộ lọc đổi dần theo thời gian, không sinh tiếng lạ.
Hiệu ứng khác: EQ / lọc dải (nhân phổ cả câu, không lệch pha), nén, méo, rung giọng, run biên độ, điệp âm, tiếng vọng,
vang phòng (tích chập với tiếng vang tổng hợp), robot (pha 0), thì thầm (đường bao phổ + pha ngẫu nhiên), tiếng rè.
Cuối cùng đưa về cùng độ to phần có tiếng nói như giọng gốc, đỉnh ≤ -1 dBFS.
"""
from __future__ import annotations

from functools import lru_cache

import numpy as np

PEAK = 0.89          # -1 dBFS
TAIL_MAX = 0.4       # tiếng vọng / vang kéo dài sau câu tối đa 0,4 s (dài hơn thì câu chiếm chỗ câu sau)


def _hann(n: int) -> np.ndarray:
    return (0.5 - 0.5 * np.cos(2 * np.pi * np.arange(n) / n)).astype(np.float32)   # tuần hoàn: chồng 50 % cộng = 1


def _f32(x) -> np.ndarray:
    return np.ascontiguousarray(x, dtype=np.float32)


# ------------------------------------------------------------------ tốc độ, lấy mẫu lại, cao độ

def resample_len(x: np.ndarray, n: int) -> np.ndarray:
    """Lấy mẫu lại thành đúng n mẫu (nội suy giới hạn băng tần qua FFT, mép dải mềm để không reo)."""
    m = len(x)
    if n == m or m == 0:
        return _f32(x).copy()
    pad = 1024
    xp = np.concatenate([np.zeros(pad, np.float32), _f32(x), np.zeros(pad, np.float32)])
    big = len(xp)
    out_n = int(round(big * n / m))
    spec = np.fft.rfft(xp)
    keep = min(len(spec), out_n // 2 + 1)
    y = np.zeros(out_n // 2 + 1, np.complex128)
    y[:keep] = spec[:keep]
    if out_n < big:          # thu nhỏ: cắt tần cao -> làm mềm 6 % cuối dải giữ lại
        t = max(1, keep // 16)
        y[keep - t:keep] *= np.linspace(1.0, 0.0, t)
    out = np.fft.irfft(y, out_n) * (out_n / big)
    s = int(round(pad * out_n / big))
    return _f32(out[s:s + n])


def stretch(x: np.ndarray, rate: float, sr: int, win: float = 0.03, tol: float = 0.008) -> np.ndarray:
    """Đổi tốc độ, giữ cao độ (WSOLA). rate > 1 = nhanh hơn (câu ngắn lại)."""
    x = _f32(x)
    if abs(rate - 1.0) < 1e-3 or len(x) < 8:
        return x.copy()
    n = max(16, int(sr * win) // 2 * 2)
    hs = n // 2
    tn = max(1, int(sr * tol))
    step = 2 if sr >= 16000 else 1            # dò chỗ khớp trên tín hiệu thưa 1/2 -> nhanh gấp 4, đủ chính xác
    src = np.concatenate([np.zeros(hs, np.float32), x])   # bù nửa khung đầu (cửa sổ đầu tiên chỉ có nửa lên)
    out_n = int(round(len(src) / rate))
    frames = out_n // hs + 2
    off = tn + n
    xp = np.concatenate([np.zeros(off, np.float32), src,
                         np.zeros(off + int(frames * hs * rate) + 2 * n, np.float32)])
    w = _hann(n)
    y = np.zeros(frames * hs + n, np.float32)
    prev = 0
    for k in range(frames):
        ideal = int(round(k * hs * rate))
        if k == 0:
            pos = 0
        else:
            nat = prev + hs                    # đoạn nối tự nhiên của khung trước
            tgt = xp[off + nat:off + nat + n:step]
            reg = xp[off + ideal - tn:off + ideal + tn + n:step]
            c = np.correlate(reg, tgt, "valid")
            pos = ideal - tn + int(np.argmax(c)) * step
        y[k * hs:k * hs + n] += xp[off + pos:off + pos + n] * w
        prev = pos
    s = int(round(hs / rate))
    return y[s:s + int(round(len(x) / rate))]


def _stft(x: np.ndarray, n: int, hop: int):
    w = _hann(n)
    xp = np.concatenate([np.zeros(n, np.float32), _f32(x), np.zeros(2 * n, np.float32)])
    nf = (len(xp) - n) // hop + 1
    idx = np.arange(n)[None, :] + hop * np.arange(nf)[:, None]
    return np.fft.rfft(xp[idx] * w, axis=1)


def _overlap_add(frames: np.ndarray, hop: int, w: np.ndarray) -> np.ndarray:
    nf, n = frames.shape
    out = np.zeros(hop * (nf - 1) + n, np.float64)
    norm = np.zeros_like(out)
    if n % hop == 0:                            # cộng chồng không vòng lặp từng khung
        for j in range(n // hop):
            out[j * hop:j * hop + nf * hop] += frames[:, j * hop:(j + 1) * hop].reshape(-1)
            norm[j * hop:j * hop + nf * hop] += np.tile((w * w)[j * hop:(j + 1) * hop], nf)
    else:
        for i in range(nf):
            out[i * hop:i * hop + n] += frames[i]
            norm[i * hop:i * hop + n] += w * w
    return out / np.maximum(norm, 1e-3)


def _istft(spec: np.ndarray, n: int, hop: int, length: int) -> np.ndarray:
    w = _hann(n)
    fr = np.fft.irfft(spec, n, axis=1) * w
    return _f32(_overlap_add(fr, hop, w)[n:n + length])


def _envelope(mag: np.ndarray, lifter: int) -> np.ndarray:
    """Đường bao phổ (log tự nhiên) từng khung: làm mượt cepstrum, bỏ các hài của cao độ."""
    n = (mag.shape[1] - 1) * 2
    cep = np.fft.irfft(np.log(np.maximum(mag, 1e-7)), n, axis=1)
    cep[:, lifter:n - lifter + 1] = 0.0
    return np.fft.rfft(cep, axis=1).real


def warp_formants(x: np.ndarray, sr: int, factor: float, n: int | None = None) -> np.ndarray:
    """Dời âm sắc (formant) theo hệ số `factor` (> 1 = giọng "nhỏ người" hơn), giữ cao độ và thời lượng."""
    if abs(factor - 1.0) < 1e-3 or len(x) < 64:
        return _f32(x).copy()
    n = n or (1024 if sr <= 32000 else 2048)
    hop = n // 4
    spec = _stft(x, n, hop)
    env = _envelope(np.abs(spec), max(8, int(sr * 0.0018)))
    bins = np.arange(spec.shape[1], dtype=np.float64) / factor
    i0 = np.clip(np.floor(bins).astype(int), 0, spec.shape[1] - 1)
    i1 = np.clip(i0 + 1, 0, spec.shape[1] - 1)
    fr = np.clip(bins - i0, 0.0, 1.0)
    target = env[:, i0] * (1 - fr) + env[:, i1] * fr
    gain = np.exp(np.clip(target - env, -3.0, 3.0))   # ≤ ±26 dB
    return _istft(spec * gain, n, hop, len(x))


def shift(x: np.ndarray, sr: int, pitch: float = 0.0, formant: float | None = None) -> np.ndarray:
    """Đổi cao độ `pitch` và âm sắc `formant` (nửa cung, None = theo cao độ), giữ nguyên thời lượng."""
    formant = pitch if formant is None else formant
    y = _f32(x)
    if abs(pitch) >= 0.01:
        r = 2.0 ** (pitch / 12.0)
        y = resample_len(stretch(y, 1.0 / r, sr), len(x))
    if abs(formant - pitch) >= 0.05:
        y = warp_formants(y, sr, 2.0 ** ((formant - pitch) / 12.0))
    return y


# ------------------------------------------------------------------ tạo tiếng kiểu khác

def robot(x: np.ndarray, sr: int, f0: float = 110.0) -> np.ndarray:
    """Giọng robot: mỗi khung giữ phổ, bỏ pha (pha 0) rồi ghép đều đặn -> 1 cao độ cố định `f0` Hz. Khung 16 ms: lời
    rõ nhất (đo bằng máy nghe tiếng Việt: khung 43 ms nghe sai 27 % số từ, 16 ms còn 10 % – giọng gốc 7 %)."""
    n = max(128, int(sr * 0.016) // 2 * 2)
    hop = max(16, int(round(sr / max(40.0, f0))))
    w = _hann(n)
    xp = np.concatenate([np.zeros(n, np.float32), _f32(x), np.zeros(2 * n, np.float32)])
    nf = (len(xp) - n) // hop + 1
    idx = np.arange(n)[None, :] + hop * np.arange(nf)[:, None]
    mag = np.abs(np.fft.rfft(xp[idx] * w, axis=1))
    fr = np.fft.fftshift(np.fft.irfft(mag, n, axis=1), axes=1) * w
    return _f32(_overlap_add(fr, hop, w)[n:n + len(x)])


def whisper(x: np.ndarray, sr: int, amount: float = 1.0) -> np.ndarray:
    """Thì thầm: giữ đường bao phổ (lời vẫn rõ), thay dây thanh bằng hơi (tiếng ồn) – pha ngẫu nhiên."""
    if amount <= 0:
        return _f32(x).copy()
    n = 512 if sr <= 32000 else 1024
    hop = n // 4
    spec = _stft(x, n, hop)
    env = np.exp(_envelope(np.abs(spec), max(6, int(sr * 0.0012))))
    rng = np.random.default_rng(11)
    ph = np.exp(2j * np.pi * rng.random(spec.shape))
    y = _istft(env * ph, n, hop, len(x))
    y *= _rms(x) / max(_rms(y), 1e-9)
    return _f32((1 - amount) * _f32(x) + amount * y)


def vibrato(x: np.ndarray, sr: int, rate: float = 5.5, cents: float = 30.0) -> np.ndarray:
    """Rung cao độ (giọng già, run run): trễ thay đổi theo hình sin."""
    if cents <= 0:
        return _f32(x).copy()
    dev = 2.0 ** (cents / 1200.0) - 1.0
    depth = dev * sr / (2 * np.pi * rate)
    t = np.arange(len(x), dtype=np.float64)
    pos = t - depth * (1 + np.sin(2 * np.pi * rate * t / sr))
    return _f32(np.interp(pos, t, x, left=0.0))


def tremolo(x: np.ndarray, sr: int, rate: float = 6.0, depth: float = 0.1) -> np.ndarray:
    t = np.arange(len(x), dtype=np.float32) / sr
    return _f32(x * (1 - depth * 0.5 * (1 + np.sin(2 * np.pi * rate * t))))


def chorus(x: np.ndarray, sr: int, mix: float = 0.4, base_ms: float = 18.0, depth_ms: float = 3.0,
           rate: float = 0.8, voices: int = 2) -> np.ndarray:
    """Điệp âm: cộng thêm vài bản trễ dao động nhẹ -> giọng dày, "vang vọng thần linh"."""
    t = np.arange(len(x), dtype=np.float64)
    out = _f32(x).astype(np.float64)
    for v in range(voices):
        d = (base_ms + v * 7.0 + depth_ms * np.sin(2 * np.pi * rate * t / sr + v * np.pi / voices)) * sr / 1000.0
        out += mix * np.interp(t - d, t, x, left=0.0)
    return _f32(out / (1 + mix * voices * 0.5))


# ------------------------------------------------------------------ lọc, nén, méo, vang

def _rms(x: np.ndarray) -> float:
    return float(np.sqrt(np.mean(np.square(x, dtype=np.float64)))) if len(x) else 0.0


def eq(x: np.ndarray, sr: int, low: float = 0.0, high: float = 0.0, presence: float = 0.0,
       band: tuple | None = None, low_f: float = 150.0, high_f: float = 5000.0, pres_f: float = 3000.0,
       slope: float = 24.0) -> np.ndarray:
    """EQ không lệch pha: low / high = nâng (dB) dưới low_f / trên high_f, presence = vùng quanh pres_f (~1 quãng 8),
    band = (thấp, cao) lọc dải như loa / điện thoại (dốc `slope` dB mỗi quãng 8)."""
    if not (low or high or presence or band):
        return _f32(x).copy()
    n = len(x)
    size = 1 << int(np.ceil(np.log2(n + 4096)))
    f = np.maximum(np.fft.rfftfreq(size, 1.0 / sr), 1.0)
    g = np.zeros_like(f)
    if low:
        g += low / (1 + np.exp(4 * np.log2(f / low_f)))
    if high:
        g += high / (1 + np.exp(-4 * np.log2(f / high_f)))
    if presence:
        g += presence * np.exp(-0.5 * (np.log2(f / pres_f) / 0.5) ** 2)
    if band:
        lo, hi = band
        g -= slope * np.maximum(0.0, np.log2(lo / f)) + slope * np.maximum(0.0, np.log2(f / hi))
    spec = np.fft.rfft(_f32(x), size) * (10.0 ** (np.maximum(g, -90.0) / 20.0))
    return _f32(np.fft.irfft(spec, size)[:n])


def compress(x: np.ndarray, sr: int, amount: float = 0.5) -> np.ndarray:
    """Nén: câu đều tiếng, chữ nhỏ không bị chìm (kiểu MC / review). amount 0..1."""
    if amount <= 0 or len(x) < sr // 50:
        return _f32(x).copy()
    hop = max(1, int(0.01 * sr))
    nf = len(x) // hop
    fr = _f32(x[:nf * hop]).reshape(nf, hop)
    lv = 20 * np.log10(np.sqrt((fr.astype(np.float64) ** 2).mean(axis=1)) + 1e-9)
    on = lv[lv > lv.max() - 40]
    if not len(on):
        return _f32(x).copy()
    thr = float(np.percentile(on, 40))
    ratio = 1 + 5 * amount
    want = np.where(lv > thr, (lv - thr) * (1 - 1 / ratio), 0.0)
    att, rel = np.exp(-1 / 0.5), np.exp(-1 / 8.0)        # theo khung 10 ms: ~5 ms / ~80 ms
    gr = np.empty(nf)
    g = 0.0
    for i in range(nf):
        c = att if want[i] > g else rel
        g = c * g + (1 - c) * want[i]
        gr[i] = g
    t = (np.arange(nf) + 0.5) * hop
    gain = 10 ** (-np.interp(np.arange(len(x)), t, gr) / 20)
    return _f32(x * gain)


def drive(x: np.ndarray, amount: float = 0.3) -> np.ndarray:
    """Méo mềm (loa, máy bộ đàm, giọng quái vật)."""
    if amount <= 0:
        return _f32(x).copy()
    pk = float(np.abs(x).max()) if len(x) else 0.0
    if pk <= 0:
        return _f32(x).copy()
    k = 1 + 10 * amount
    return _f32(np.tanh(k * x / pk) / np.tanh(k) * pk)


def _conv(x: np.ndarray, ir: np.ndarray) -> np.ndarray:
    size = 1 << int(np.ceil(np.log2(len(x) + len(ir))))
    return np.fft.irfft(np.fft.rfft(x, size) * np.fft.rfft(ir, size), size)[:len(x) + len(ir) - 1]


def _with_tail(dry: np.ndarray, wet: np.ndarray, sr: int, tail: float) -> np.ndarray:
    """Giọng gốc + phần hiệu ứng, kéo dài tối đa `tail` giây sau câu (nhỏ dần về 0)."""
    t = int(min(tail, TAIL_MAX) * sr)
    out = np.zeros(len(dry) + t, np.float64)
    out[:len(dry)] += dry
    m = min(len(out), len(wet))
    out[:m] += wet[:m]
    if t > 0:
        out[len(dry):] *= np.linspace(1.0, 0.0, t) ** 2
    return _f32(out)


def echo(x: np.ndarray, sr: int, delay: float = 0.3, decay: float = 0.4, count: int = 3) -> np.ndarray:
    d = max(1, int(delay * sr))
    wet = np.zeros(len(x) + d * count, np.float64)
    for k in range(1, count + 1):
        wet[k * d:k * d + len(x)] += (decay ** k) * x
    return _with_tail(x, wet, sr, delay * count)


def reverb(x: np.ndarray, sr: int, t60: float = 1.2, wet: float = 0.25, predelay: float = 0.02,
           damp: float = 0.5) -> np.ndarray:
    """Vang phòng / hang / hội trường: tích chập với tiếng vang tổng hợp (nhiễu tắt dần, tần cao tắt nhanh hơn)."""
    if wet <= 0:
        return _f32(x).copy()
    ir = _room(sr, round(float(t60), 3), round(float(predelay), 3), round(float(damp), 3))
    return _with_tail(_f32(x) * (1 - wet * 0.3), wet * _conv(_f32(x), ir), sr, t60 * 0.5)


@lru_cache(maxsize=16)
def _room(sr: int, t60: float, predelay: float, damp: float) -> np.ndarray:
    """Tiếng vang tổng hợp (tạo 1 lần cho mỗi kiểu phòng)."""
    L = int(sr * min(max(t60, 0.1), 3.0))
    rng = np.random.default_rng(7)
    t = np.arange(L) / sr
    ir = rng.standard_normal(L) * np.exp(-6.91 * t / t60)
    ir[:int(predelay * sr)] = 0.0
    if damp > 0:                    # tần cao tắt nhanh hơn (tường hút âm)
        size = 1 << int(np.ceil(np.log2(L)))
        f = np.fft.rfftfreq(size, 1.0 / sr)
        fc = 8000.0 * (1 - damp) + 1500.0
        ir = np.fft.irfft(np.fft.rfft(ir, size) / (1 + (f / fc) ** 2), size)[:L]
    ir /= max(float(np.sqrt((ir ** 2).sum())), 1e-9)
    ir.setflags(write=False)
    return ir


def noise(x: np.ndarray, sr: int, db: float = -40.0, crackle: float = 0.0) -> np.ndarray:
    """Tiếng rè nền (điện thoại, radio): `db` so với độ to giọng nói; crackle = tiếng lách tách / giây."""
    from .voicefix import speech_level

    lv = speech_level(x, sr) or _rms(x)
    rng = np.random.default_rng(5)
    y = _f32(x) + _f32(rng.standard_normal(len(x)) * lv * 10 ** (db / 20))
    if crackle > 0:
        k = rng.random(len(x)) < crackle / sr
        y[k] += _f32(rng.uniform(-1, 1, int(k.sum())) * lv * 0.6)
    return y


def match_level(y: np.ndarray, ref: float, sr: int) -> np.ndarray:
    """Cùng độ to phần có tiếng nói với giọng gốc, đỉnh không quá -1 dBFS."""
    from .voicefix import speech_level

    y = _f32(y)
    lv = speech_level(y, sr)
    if lv > 0 and ref > 0:
        y = y * (ref / lv)
    pk = float(np.abs(y).max()) if len(y) else 0.0
    if pk > PEAK:
        y = y * (PEAK / pk)
    return _f32(y)


# ------------------------------------------------------------------ chuỗi hiệu ứng

FX_KEYS = ("tempo", "pitch", "formant", "robot", "whisper", "vibrato", "tremolo", "low", "high", "presence", "band",
           "drive", "comp", "chorus", "echo", "reverb", "noise", "crackle")


def _args(v, default: tuple) -> list:
    """[a, b] thiếu phần sau -> lấy mặc định; True -> mặc định."""
    vals = list(v) if isinstance(v, (list, tuple)) else []
    return vals[:len(default)] + list(default[len(vals):])


def apply(x: np.ndarray, sr: int, fx: dict | None) -> np.ndarray:
    """Áp chuỗi hiệu ứng `fx` lên 1 câu (mảng float mono). Khoá trong fx (đều tuỳ chọn):
    tempo (×tốc độ), pitch / formant (nửa cung), robot (Hz), whisper (0..1), vibrato (cent), tremolo (0..1),
    low / high / presence (dB), band (thấp, cao Hz), drive (0..1), comp (0..1), chorus (0..1),
    echo (trễ s, giảm, số lần), reverb (t60 s, mức 0..1), noise (dB so với giọng), crackle (lần / s)."""
    x = _f32(x)
    if not fx or not len(x):
        return x.copy()
    from .voicefix import speech_level

    ref = speech_level(x, sr)
    y = x
    if fx.get("tempo") and abs(fx["tempo"] - 1) > 1e-3:
        y = stretch(y, float(fx["tempo"]), sr)
    body = len(y)
    if fx.get("pitch") or fx.get("formant"):
        y = shift(y, sr, float(fx.get("pitch", 0.0)), fx.get("formant"))
    if fx.get("robot"):
        y = robot(y, sr, float(fx["robot"]))
    if fx.get("whisper"):
        y = whisper(y, sr, float(fx["whisper"]))
    if fx.get("vibrato"):
        y = vibrato(y, sr, cents=float(fx["vibrato"]))
    if fx.get("tremolo"):
        y = tremolo(y, sr, depth=float(fx["tremolo"]))
    if fx.get("low") or fx.get("high") or fx.get("presence") or fx.get("band"):
        y = eq(y, sr, float(fx.get("low", 0)), float(fx.get("high", 0)), float(fx.get("presence", 0)),
               tuple(fx["band"]) if fx.get("band") else None)
    if fx.get("drive"):
        y = drive(y, float(fx["drive"]))
    if fx.get("comp"):
        y = compress(y, sr, float(fx["comp"]))
    if fx.get("chorus"):
        y = chorus(y, sr, float(fx["chorus"]))
    if fx.get("echo"):
        d, dec, cnt = _args(fx["echo"], (0.3, 0.4, 3))
        y = echo(y, sr, float(d), float(dec), int(cnt))
    if fx.get("reverb"):
        t60, wet = _args(fx["reverb"], (1.2, 0.25))
        y = reverb(y, sr, float(t60), float(wet))
    if fx.get("noise") or fx.get("crackle"):
        y = noise(y, sr, float(fx.get("noise", -60.0)), float(fx.get("crackle", 0.0)))
    limit = body + int(TAIL_MAX * sr)
    if len(y) > limit:              # tiếng vọng + vang cộng dồn: cả đuôi vẫn ≤ 0,4 s, nhỏ dần
        y = y[:limit].copy()
        y[body:] *= np.linspace(1.0, 0.0, limit - body, dtype=np.float32) ** 2
    return match_level(y, ref, sr)
