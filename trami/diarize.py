"""Phân biệt người nói: gán S1, S2, S3… cho từng câu phụ đề.

Cách làm (dùng được với mọi nguồn phụ đề: Whisper, SenseVoice, OCR, file SRT):
  1. Lấy "dấu vân giọng" (speaker embedding ECAPA của SpeechBrain) cho từng câu, ưu tiên trên giọng đã tách nhạc.
  2. Gom cụm theo độ giống nhau (cosine) -> mỗi cụm là một người.
"""
from __future__ import annotations

from pathlib import Path
from typing import Callable

import numpy as np

from .config import MODELS_DIR
from .ffmpeg import Cancelled
from .subtitle import Segment

_enc = None
SR = 16000


def available() -> bool:
    from .config import installed

    return installed("speechbrain", "torch")


def drop_broken_lazy_modules() -> int:
    """SpeechBrain đăng ký sẵn các module "lười" (VD speechbrain.integrations.k2_fsa) vào sys.modules; chạm vào là báo
    ImportError (thiếu k2). PySide6 khi soạn thông báo lỗi sẽ duyệt sys.modules và SẬP cả chương trình vì chúng.
    -> gỡ những module lười không nạp được khỏi sys.modules."""
    import sys

    removed = 0
    for name, mod in list(sys.modules.items()):
        if type(mod).__name__ not in ("LazyModule", "DeprecatedModuleRedirect"):
            continue
        try:
            getattr(mod, "__dict__")
            getattr(mod, "__name__")
            getattr(mod, "__file__", None)
        except Exception:  # noqa: BLE001
            sys.modules.pop(name, None)
            removed += 1
    return removed


def _encoder():
    global _enc
    if _enc is None:
        import torch

        try:
            from speechbrain.inference.speaker import EncoderClassifier
        except ImportError:  # speechbrain < 1.0
            from speechbrain.pretrained import EncoderClassifier  # type: ignore
        dev = "cuda" if torch.cuda.is_available() else "cpu"
        kw = {}
        try:
            # Mặc định SpeechBrain tạo symlink tới cache – Windows không cho nếu không chạy quyền Admin
            # (WinError 1314) -> chép file thay vì symlink.
            from speechbrain.utils.fetching import LocalStrategy

            kw["local_strategy"] = LocalStrategy.COPY
        except ImportError:  # speechbrain cũ
            pass
        drop_broken_lazy_modules()
        _enc = EncoderClassifier.from_hparams(source="speechbrain/spkrec-ecapa-voxceleb",
                                              savedir=str(MODELS_DIR / "spkrec-ecapa"),
                                              run_opts={"device": dev}, **kw)
    drop_broken_lazy_modules()
    return _enc


def embed_segments(audio: np.ndarray, segs: list[Segment], progress=None, cancel=None) -> tuple[list[int], np.ndarray]:
    """Trả về (chỉ số các câu có embedding, ma trận embedding đã chuẩn hoá)."""
    import torch

    enc = _encoder()
    idx, embs = [], []
    for i, s in enumerate(segs):
        if cancel and cancel():
            raise Cancelled()
        a, b = int(s.start * SR), int(s.end * SR)
        wav = audio[a:b]
        if len(wav) < SR * 0.5:  # câu quá ngắn -> giọng không đủ tin cậy, gán sau theo câu lân cận
            continue
        wav = wav[: SR * 15]
        with torch.no_grad():
            e = enc.encode_batch(torch.from_numpy(wav).float().unsqueeze(0)).squeeze().cpu().numpy()
        n = np.linalg.norm(e)
        if n > 0:
            idx.append(i)
            embs.append(e / n)
        if progress and i % 20 == 0:
            progress(0.1 + 0.8 * i / max(1, len(segs)), f"Đang nhận diện giọng từng câu {i}/{len(segs)}")
    return idx, (np.vstack(embs) if embs else np.zeros((0, 192)))


def cluster(embs: np.ndarray, threshold: float = 0.62, n_speakers: int = 0) -> np.ndarray:
    """Gom cụm agglomerative (average linkage, cosine). Trả về nhãn 0..k-1, người nói nhiều nhất = 0."""
    n = len(embs)
    if n == 0:
        return np.zeros(0, int)
    if n == 1:
        return np.zeros(1, int)
    sim = embs @ embs.T
    clusters: list[list[int]] = [[i] for i in range(n)]
    # ma trận tương đồng trung bình giữa các cụm
    S = sim.copy()
    np.fill_diagonal(S, -np.inf)
    sizes = np.ones(n)
    active = np.ones(n, bool)
    target = max(1, n_speakers) if n_speakers else 1
    while active.sum() > target:
        Sa = np.where(active[:, None] & active[None, :], S, -np.inf)
        i, j = np.unravel_index(np.argmax(Sa), Sa.shape)
        best = Sa[i, j]
        if not n_speakers and best < threshold:
            break
        # gộp j vào i (average linkage)
        S[i, :] = (S[i, :] * sizes[i] + S[j, :] * sizes[j]) / (sizes[i] + sizes[j])
        S[:, i] = S[i, :]
        S[i, i] = -np.inf
        sizes[i] += sizes[j]
        active[j] = False
        S[j, :] = -np.inf
        S[:, j] = -np.inf
        clusters[i].extend(clusters[j])
        clusters[j] = []
    groups = sorted((c for c in clusters if c), key=len, reverse=True)
    labels = np.zeros(n, int)
    for k, g in enumerate(groups):
        labels[g] = k
    # cụm quá nhỏ (1 câu) và gần 1 cụm lớn -> gộp vào để tránh "người nói ảo"
    if not n_speakers and len(groups) > 1:
        cent = np.vstack([embs[g].mean(0) / np.linalg.norm(embs[g].mean(0)) for g in groups])
        for k, g in enumerate(groups):
            if len(g) == 1 and k > 0:
                sims = cent[:k] @ embs[g[0]]
                if sims.max() > threshold - 0.15:
                    labels[g] = int(np.argmax(sims))
        # đánh lại số liên tục
        uniq = {v: i for i, v in enumerate(sorted(set(labels.tolist()), key=lambda v: -np.sum(labels == v)))}
        labels = np.array([uniq[v] for v in labels])
    return labels


def _emb_fn():
    import torch

    enc = _encoder()

    def f(wav: np.ndarray) -> np.ndarray:
        with torch.no_grad():
            return enc.encode_batch(torch.from_numpy(np.ascontiguousarray(wav)).float().unsqueeze(0)
                                    ).squeeze().cpu().numpy()
    return f


def assign_speakers(audio_path: str | Path, segs: list[Segment], threshold: float = 0.62, n_speakers: int = 0,
                    progress: Callable[[float, str], None] | None = None, cancel=None,
                    split_changes: bool = False) -> int:
    """Gán seg.speaker = 'S1', 'S2'… Trả về số người nói tìm được.
    split_changes: trước khi gán, tách các câu dài có 2 người nói liền nhau (sửa thẳng danh sách `segs`)."""
    from .asr_funasr import load_wav16k

    if not segs:
        return 0
    if progress:
        progress(0.02, "Đang nạp mô hình nhận diện giọng (ECAPA)…")
    from .progress_hook import tqdm_progress

    with tqdm_progress(progress, 0.02, 0.1, "Đang nạp mô hình nhận diện giọng", cancel,
                       download="Đang tải mô hình nhận diện giọng (ECAPA)"):
        _encoder()
    audio = load_wav16k(audio_path)
    if split_changes:
        from .align import split_speaker_changes

        before = len(segs)
        segs[:] = split_speaker_changes(segs, audio, _emb_fn(), progress=progress, cancel=cancel)
        assign_speakers.splits = len(segs) - before   # số câu được tách thêm (để báo người dùng)
    idx, embs = embed_segments(audio, segs, progress, cancel)
    if not idx:
        for s in segs:
            s.speaker = "S1"
        return 1
    labels = cluster(embs, threshold, n_speakers)
    for i, l in zip(idx, labels):
        segs[i].speaker = f"S{l + 1}"
    # câu ngắn chưa có nhãn -> lấy theo câu gần nhất về thời gian
    known = [(segs[i].start, segs[i].speaker) for i in idx]
    for s in segs:
        if not s.speaker:
            s.speaker = min(known, key=lambda k: abs(k[0] - s.start))[1]
    if progress:
        progress(1.0, f"Tìm thấy {len(set(labels.tolist()))} người nói")
    return len(set(labels.tolist()))
