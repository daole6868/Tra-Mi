# Licensed under the Apache License, Version 2.0 (see trami/firered/LICENSE).
#
# Căn CTC (Viterbi) bằng numpy cho FireRedASR2 – Subvideo tự viết thay torchaudio.functional.forced_align (torchaudio
# bản mới đã bỏ hàm này); không cần torch nên kiểm thử được trên máy không có PyTorch.
from __future__ import annotations

import numpy as np


def ctc_forced_align(logp: np.ndarray, y: list, blank: int = 0) -> list:
    """Căn CTC (Viterbi) – thay torchaudio.functional.forced_align. logp: (T, V) log-xác suất, y: mã chữ (không có
    blank). Trả nhãn từng khung (blank = `blank`), [] nếu không căn được (chữ nhiều hơn số khung)."""
    T, L = int(logp.shape[0]), len(y)
    if T == 0 or L == 0:
        return []
    ext = np.full(2 * L + 1, blank, dtype=np.int64)
    ext[1::2] = np.asarray(y, dtype=np.int64)
    S = len(ext)
    need = L + sum(1 for i in range(1, L) if y[i] == y[i - 1])   # chữ lặp liền nhau cần 1 khung blank chen giữa
    if need > T:
        return []
    skip = np.zeros(S, dtype=bool)                               # được nhảy từ s-2 sang s (bỏ qua blank giữa 2 chữ khác nhau)
    skip[2:] = (ext[2:] != blank) & (ext[2:] != ext[:-2])
    neg = -1e30
    emit = logp[:, ext].astype(np.float64)
    alpha = np.full(S, neg)
    alpha[0] = emit[0, 0]
    alpha[1] = emit[0, 1]
    back = np.zeros((T, S), dtype=np.int8)                       # 0: giữ trạng thái, 1: từ s-1, 2: từ s-2
    for t in range(1, T):
        stay = alpha
        one = np.concatenate(([neg], alpha[:-1]))
        two = np.where(skip, np.concatenate(([neg, neg], alpha[:-2])), neg)
        cand = np.stack([stay, one, two])
        k = np.argmax(cand, axis=0)
        alpha = cand[k, np.arange(S)] + emit[t]
        back[t] = k
    s = S - 1 if alpha[S - 1] >= alpha[S - 2] else S - 2
    path = np.empty(T, dtype=np.int64)
    for t in range(T - 1, -1, -1):
        path[t] = s
        s -= int(back[t, s])
    if path[0] > 1:     # không đi được từ đầu (âm thanh quá ngắn so với lời)
        return []
    return ext[path].tolist()
