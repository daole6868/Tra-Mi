# Copyright 2026 Xiaohongshu. (Author: Kaituo Xu)
# Licensed under the Apache License, Version 2.0 (see trami/firered/LICENSE).
#
# Đặc trưng đầu vào của FireRedASR2 (fbank 80 kênh kiểu Kaldi + CMVN), theo fireredasr2s/fireredasr2/data/asr_feat.py.
# Sửa đổi của Subvideo: nhận thẳng mảng âm thanh 16 kHz (không đọc file); có kaldi_native_fbank thì dùng (như bản
# gốc), không có thì tính fbank Kaldi bằng numpy (`kaldi_fbank`, cùng công thức: bỏ DC, nhấn tần 0,97, cửa sổ Povey,
# FFT 512, mel 20 Hz–8 kHz, log); đọc cmvn.ark bằng `read_kaldi_matrix` (không cần kaldiio).
from __future__ import annotations

import math
import struct

import numpy as np

SR = 16000
FRAME_LEN = 400      # 25 ms
FRAME_SHIFT = 160    # 10 ms
N_FFT = 512
N_MELS = 80


def read_kaldi_matrix(path: str) -> np.ndarray:
    """Ma trận Kaldi (nhị phân "\\0B" FM/DM hoặc dạng chữ "[ … ]"), có / không có khoá đứng trước."""
    data = open(path, "rb").read()
    k = data.find(b"\0B")
    if 0 <= k <= 256:
        p = k + 2
        kind = data[p:p + 3]
        p += 3
        if kind not in (b"FM ", b"DM "):
            raise ValueError(f"cmvn: kiểu ma trận lạ {kind!r}")
        _sz, rows = struct.unpack_from("<bi", data, p)
        p += 5
        _sz, cols = struct.unpack_from("<bi", data, p)
        p += 5
        dt = np.float32 if kind == b"FM " else np.float64
        return np.frombuffer(data, dtype=dt, count=rows * cols, offset=p).reshape(rows, cols).astype(np.float64)
    txt = data.decode("utf-8", "ignore")
    a, b = txt.index("["), txt.rindex("]")
    rows = [r.split() for r in txt[a + 1:b].strip().splitlines() if r.split()]
    return np.array([[float(x) for x in r] for r in rows], dtype=np.float64)


class CMVN:
    def __init__(self, path: str):
        stats = read_kaldi_matrix(path)
        assert stats.shape[0] == 2, "cmvn: cần 2 hàng thống kê"
        dim = stats.shape[-1] - 1
        count = stats[0, dim]
        assert count >= 1
        mean = stats[0, :dim] / count
        var = np.maximum(stats[1, :dim] / count - mean * mean, 1e-20)
        self.dim, self.means, self.istd = dim, mean, 1.0 / np.sqrt(var)

    def __call__(self, x: np.ndarray) -> np.ndarray:
        assert x.shape[-1] == self.dim, "CMVN dim mismatch"
        return (x - self.means) * self.istd


def _mel(f):
    return 1127.0 * np.log(1.0 + np.asarray(f, dtype=np.float64) / 700.0)


def _mel_banks(n_mels: int = N_MELS, n_fft: int = N_FFT, sr: int = SR, low: float = 20.0, high: float = 0.0):
    """Ma trận mel tam giác theo đúng cách Kaldi (MelBanks: tam giác trên thang mel, n_fft/2 vạch tần đầu)."""
    high = high if high > 0 else sr / 2 + high
    nb = n_fft // 2
    fft_w = sr / n_fft
    mlow, mhigh = _mel(low), _mel(high)
    delta = (mhigh - mlow) / (n_mels + 1)
    m = _mel(fft_w * np.arange(nb))
    banks = np.zeros((n_mels, nb + 1))
    for b in range(n_mels):
        left, center, right = mlow + b * delta, mlow + (b + 1) * delta, mlow + (b + 2) * delta
        up = (m - left) / (center - left)
        down = (right - m) / (right - center)
        w = np.where((m > left) & (m < right), np.where(m <= center, up, down), 0.0)
        banks[b, :nb] = w
    return banks


_BANKS: np.ndarray | None = None
_WIN = (0.5 - 0.5 * np.cos(2 * math.pi * np.arange(FRAME_LEN) / (FRAME_LEN - 1))) ** 0.85   # cửa sổ Povey


def kaldi_fbank(wave: np.ndarray) -> np.ndarray:
    """fbank 80 kênh (log năng lượng mel) như Kaldi / kaldi_native_fbank với dither 0, snip_edges. wave: mẫu theo thang
    int16 (−32768..32767, đúng như bản gốc đọc file wav bằng kaldiio)."""
    global _BANKS
    x = np.asarray(wave, dtype=np.float64)
    if len(x) < FRAME_LEN:
        return np.zeros((0, N_MELS))
    n = 1 + (len(x) - FRAME_LEN) // FRAME_SHIFT
    idx = np.arange(FRAME_LEN)[None, :] + FRAME_SHIFT * np.arange(n)[:, None]
    fr = x[idx]
    fr = fr - fr.mean(axis=1, keepdims=True)
    fr[:, 1:] = fr[:, 1:] - 0.97 * fr[:, :-1]
    fr[:, 0] = fr[:, 0] * (1 - 0.97)
    fr = fr * _WIN
    spec = np.fft.rfft(fr, n=N_FFT)
    power = spec.real ** 2 + spec.imag ** 2
    if _BANKS is None:
        _BANKS = _mel_banks()
    mel = power @ _BANKS.T
    return np.log(np.maximum(mel, np.finfo(np.float32).eps))


def fbank(wave: np.ndarray) -> np.ndarray:
    try:
        import kaldi_native_fbank as knf
    except ImportError:
        return kaldi_fbank(wave)
    opts = knf.FbankOptions()
    opts.frame_opts.dither = 0.0
    opts.mel_opts.num_bins = N_MELS
    opts.frame_opts.snip_edges = True
    opts.mel_opts.debug_mel = False
    fb = knf.OnlineFbank(opts)
    fb.accept_waveform(SR, np.asarray(wave, dtype=np.float32).tolist())
    if fb.num_frames_ready == 0:
        return np.zeros((0, N_MELS))
    return np.vstack([fb.get_frame(i) for i in range(fb.num_frames_ready)])


class FeatExtractor:
    """Mảng âm thanh 16 kHz (float −1..1) -> (đặc trưng đệm (N, T, 80) torch, độ dài (N,), giây mỗi đoạn)."""

    def __init__(self, cmvn_path: str):
        self.cmvn = CMVN(cmvn_path) if cmvn_path else None

    def __call__(self, clips: list[np.ndarray]):
        import torch

        feats, durs = [], []
        for c in clips:
            f = fbank(np.asarray(c, dtype=np.float64) * 32768.0)
            if f.shape[0] < 1:
                f = np.zeros((1, N_MELS))
            if self.cmvn is not None:
                f = self.cmvn(f)
            feats.append(torch.from_numpy(f.astype(np.float32)))
            durs.append(len(c) / SR)
        lengths = torch.tensor([f.size(0) for f in feats]).long()
        pad = torch.zeros(len(feats), int(lengths.max()), N_MELS)
        for i, f in enumerate(feats):
            pad[i, :f.size(0)] = f
        return pad, lengths, durs
