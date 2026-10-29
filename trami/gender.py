"""Đoán mỗi người nói (S1, S2…) là GIỌNG NAM hay GIỌNG NỮ để "Tự gán giọng" chọn giọng lồng hợp.

Đây là đoán chất giọng (trầm / cao) để chọn giọng đọc, không phải xác định giới tính của người thật.
Hai nguồn, dùng cả hai nếu có:
  - Mô hình ECAPA phân loại giọng (MIT, ~16 MB, chạy CPU bằng onnxruntime) – tải ở tab Tài liệu.
  - Cao độ giọng (F0) đo trực tiếp từ âm thanh: nam thường 85–165 Hz, nữ 165–300 Hz. Không cần tải gì.
Giọng trẻ con / hoạt hình / la hét có thể đoán sai: luôn có thể đổi giọng tay ở bảng người nói.
"""
from __future__ import annotations

import numpy as np

REPO = "Alice-Sabrina-Ivy/voice-gender-classifier-onnx-q8"   # bản ONNX 8-bit của JaesungHuh/voice-gender-classifier
REVISION = "5debf995242f9851881d4e4848fe6eed2bb5ade7"
FILE = "onnx/model_quantized.onnx"
SR = 16000
_session = None


def model_path(download: bool = False) -> str | None:
    """Đường dẫn file mô hình trong kho HuggingFace trên máy (None nếu chưa tải). download=True: tải nếu chưa có."""
    try:
        from huggingface_hub import hf_hub_download, try_to_load_from_cache

        p = try_to_load_from_cache(REPO, FILE, revision=REVISION)
        if isinstance(p, str):
            return p
        if download:
            return hf_hub_download(REPO, FILE, revision=REVISION)
    except Exception:  # noqa: BLE001
        if download:
            raise
    return None


def _model():
    global _session
    if _session is None:
        path = model_path()
        if not path:
            return None
        try:
            import onnxruntime as ort

            opt = ort.SessionOptions()
            opt.intra_op_num_threads = 2
            opt.log_severity_level = 3
            _session = ort.InferenceSession(path, sess_options=opt, providers=["CPUExecutionProvider"])
        except Exception:  # noqa: BLE001 – thiếu onnxruntime / file hỏng: dùng cao độ giọng
            return None
    return _session


def male_prob(a: np.ndarray) -> float | None:
    """Xác suất giọng nam của 1 đoạn (mono 16 kHz, -1..1) theo mô hình; None nếu chưa có mô hình."""
    m = _model()
    if m is None or len(a) < SR * 0.5:
        return None
    logits = np.asarray(m.run(None, {"input_values": np.asarray(a, np.float32)[None, :]})[0], np.float64).reshape(-1)
    if logits.shape != (2,) or not np.isfinite(logits).all():
        return None
    e = np.exp(logits - logits.max())
    return float(e[0] / e.sum())          # nhãn 0 = male, 1 = female


def pitch_track(a: np.ndarray, sr: int = SR) -> np.ndarray:
    """Cao độ (Hz) các khung 40 ms có tiếng nói rõ, đo bằng hàm sai lệch chuẩn hoá (kiểu YIN). Khung không tuần
    hoàn (phụ âm, nhiễu, lặng) bị bỏ."""
    a = np.asarray(a, np.float32)
    n, hop = int(sr * 0.04), int(sr * 0.02)
    lo, hi = int(sr / 400), int(sr / 70)          # 70–400 Hz
    if len(a) < n + hi:
        return np.zeros(0)
    top = float(np.abs(a).max()) or 1.0
    out = []
    lags = np.arange(1, hi + 1)
    for st in range(0, len(a) - n - hi, hop):
        x = a[st:st + n + hi].astype(np.float64)
        if np.sqrt(np.mean(x[:n] ** 2)) < 0.02 * top:
            continue
        # d(t) = Σ(x_j − x_{j+t})² = E(x[:n]) + E(x[t:t+n]) − 2·Σ x_j·x_{j+t}
        corr = np.correlate(x, x[:n], mode="valid")[1:hi + 1]
        sq = np.concatenate([[0.0], np.cumsum(x * x)])
        d = sq[n] + (sq[lags + n] - sq[lags]) - 2 * corr
        cm = d * lags / np.maximum(np.cumsum(d), 1e-12)   # chuẩn hoá tích luỹ
        cand = np.flatnonzero(cm[lo - 1:] < 0.2)
        if not len(cand):
            continue
        t = cand[0] + lo - 1
        while t + 1 < len(cm) and cm[t + 1] < cm[t]:
            t += 1
        out.append(sr / (t + 1))
    return np.asarray(out)


def speaker_gender(audio: np.ndarray, spans: list[tuple[float, float]]) -> dict:
    """{"g": "male"|"female"|"", "conf": 0..1, "f0": Hz, "by": "model+pitch"|"pitch"} cho 1 người nói
    từ các câu của người đó (audio mono 16 kHz)."""
    spans = sorted(spans, key=lambda x: x[0] - x[1])[:12]        # tối đa 12 câu dài nhất
    probs, weights, f0s = [], [], []
    for a, b in spans:
        clip = audio[int(a * SR):int(min(b, a + 8.0) * SR)]
        if len(clip) < SR * 0.6:
            continue
        f0s.append(pitch_track(clip))
        p = male_prob(clip)
        if p is not None:
            probs.append(p)
            weights.append(len(clip))
    f0 = np.concatenate(f0s) if f0s else np.zeros(0)
    f0 = f0[(f0 >= 70) & (f0 <= 400)]
    med = float(np.median(f0)) if len(f0) >= 8 else 0.0
    # cao độ: dưới 150 Hz rõ là trầm (nam), trên 190 Hz rõ là cao (nữ), giữa là khó đoán
    pitch_p = None if not med else float(np.clip((190.0 - med) / 40.0, 0.0, 1.0))
    if probs:
        mp = float(np.average(probs, weights=weights))
        p = mp if pitch_p is None else 0.75 * mp + 0.25 * pitch_p
        by = "model+pitch"
    elif pitch_p is not None:
        p, by = pitch_p, "pitch"
    else:
        return {"g": "", "conf": 0.0, "f0": round(med, 1), "by": ""}
    g = "male" if p >= 0.5 else "female"
    conf = abs(p - 0.5) * 2
    return {"g": g if conf >= 0.2 else "", "conf": round(conf, 2), "f0": round(med, 1), "by": by}
