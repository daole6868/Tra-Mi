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
        if len(wav) < SR * 0.8:  # câu quá ngắn -> giọng không đủ tin cậy, gán sau theo câu lân cận
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


def cluster(embs: np.ndarray, threshold: float = 0.62, n_speakers: int = 0,
            durs: np.ndarray | None = None) -> np.ndarray:
    """Gom cụm agglomerative (average linkage, cosine). Trả về nhãn 0..k-1, người nói nhiều nhất = 0.
    durs: thời lượng từng câu – cụm "người nói" chỉ có ≤ 2 câu / < 3 giây thường là giọng gào, cười… của người đã có."""
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
    # cụm quá nhỏ (≤ 2 câu hoặc < 3 giây) và đủ gần 1 cụm lớn -> gộp vào để tránh "người nói ảo"
    if not n_speakers and len(groups) > 1:
        w = np.ones(n) * 2.0 if durs is None else np.asarray(durs, float)
        cent = np.vstack([embs[g].mean(0) / np.linalg.norm(embs[g].mean(0)) for g in groups])
        big = [k for k, g in enumerate(groups) if len(g) >= 3 and w[g].sum() >= 3.0] or [0]
        for k, g in enumerate(groups):
            if k in big:
                continue
            sims = cent[big] @ cent[k]
            if sims.max() > threshold - (0.15 if len(g) == 1 and durs is None else 0.2):
                labels[g] = big[int(np.argmax(sims))]
        # đánh lại số liên tục
        uniq = {v: i for i, v in enumerate(sorted(set(labels.tolist()), key=lambda v: -np.sum(labels == v)))}
        labels = np.array([uniq[v] for v in labels])
    return labels


INTERJ_GAP = 0.3       # vế chen ngang: nhân vật ngừng ≥ 0,3 s ở chỗ tách (mốc từng chữ)
INTERJ_CHARS = 6       # vế ≤ 6 chữ, dài 0,3–1,5 s ("找死！", "住手！", "你敢？")
INTERJ_DUR = (0.3, 1.5)
INTERJ_REST = 0.8      # phần còn lại của câu ≥ 0,8 s (đủ để biết giọng chính của câu)
INTERJ_OWN = 0.3       # giống giọng người nói của câu < 0,3 ...
INTERJ_OTHER = 0.4     # ... và giống 1 người nói khác ≥ 0,4, hơn ≥ 0,2 -> người khác nói chen ngang
INTERJ_MARGIN = 0.2
SHORT_MIN = 0.3        # câu ngắn 0,3–0,8 s (không đủ để gom cụm): gán theo giọng giống nhất khi chắc chắn


def _unit(e: np.ndarray) -> np.ndarray | None:
    e = np.asarray(e, np.float32).reshape(-1)
    n = float(np.linalg.norm(e))
    return e / n if n > 0 else None


def centroids(segs: list[Segment], idx: list[int], embs: np.ndarray) -> dict[str, np.ndarray]:
    """Giọng trung bình của từng người nói (từ các câu đủ dài đã đo)."""
    out: dict[str, list] = {}
    for i, e in zip(idx, embs):
        if segs[i].speaker:
            out.setdefault(segs[i].speaker, []).append(e)
    cents = {}
    for k, v in out.items():
        u = _unit(np.mean(v, axis=0))
        if u is not None:
            cents[k] = u
    return cents


def _sims(clip: np.ndarray, emb_fn, cents: dict[str, np.ndarray]) -> dict[str, float]:
    e = _unit(emb_fn(clip))
    return {} if e is None else {k: float(c @ e) for k, c in cents.items()}


def _clip(audio: np.ndarray, a: float, b: float) -> np.ndarray:
    return audio[max(0, int((a - 0.05) * SR)):max(0, int((b + 0.05) * SR))]


def short_speakers(segs: list[Segment], audio: np.ndarray, emb_fn, cents: dict[str, np.ndarray],
                   known: set[int]) -> int:
    """Câu ngắn (0,3–0,8 s) chưa đo giọng – trước đây lấy người nói của câu gần nhất (câu đáp "好。" ngay sau lời người
    khác thành sai người): đo giọng, giống hẳn 1 người nói khác (≥ 0,4 và hơn ≥ 0,2) thì gán người đó (2.1.20)."""
    if len(cents) < 2:
        return 0
    n = 0
    for i, s in enumerate(segs):
        if i in known or s.duration < SHORT_MIN:
            continue
        sims = _sims(_clip(audio, s.start, s.end), emb_fn, cents)
        if not sims:
            continue
        best = max(sims, key=sims.get)
        if best != s.speaker and sims[best] >= INTERJ_OTHER and sims[best] - sims.get(s.speaker, -1.0) >= INTERJ_MARGIN:
            s.speaker = best
            n += 1
    return n


def _interjection(s: Segment, audio: np.ndarray, emb_fn, cents: dict[str, np.ndarray]) -> list[Segment] | None:
    from .align import PAD, _char_positions, _split_text
    from .snap import ct_ok

    if s.speaker not in cents or not ct_ok(s) or s.extra.get("song"):
        return None
    ct = [(float(a), float(b)) for a, b in s.extra["ct"]]
    pos = _char_positions(s.text)
    if len(ct) != len(pos) or len(ct) < 3:
        return None
    groups, st = [], 0
    for k in range(len(ct) - 1):
        if ct[k + 1][0] - ct[k][1] >= INTERJ_GAP:
            groups.append((st, k))
            st = k + 1
    groups.append((st, len(ct) - 1))
    if len(groups) < 2:
        return None
    total = sum(ct[b][1] - ct[a][0] for a, b in groups)
    who = []
    for a, b in groups:
        dur = ct[b][1] - ct[a][0]
        spk = s.speaker
        if b - a + 1 <= INTERJ_CHARS and INTERJ_DUR[0] <= dur <= INTERJ_DUR[1] and total - dur >= INTERJ_REST:
            sims = _sims(_clip(audio, ct[a][0], ct[b][1]), emb_fn, cents)
            if sims:
                own = sims.get(s.speaker, -1.0)
                other = max((k for k in sims if k != s.speaker), key=sims.get)
                if own < INTERJ_OWN and sims[other] >= INTERJ_OTHER and sims[other] - own >= INTERJ_MARGIN:
                    spk = other
        who.append(spk)
    if all(w == s.speaker for w in who) or all(w != s.speaker for w in who):
        return None
    pieces: list[list] = []          # [chữ đầu, chữ cuối, người nói]
    for (a, b), w in zip(groups, who):
        if pieces and pieces[-1][2] == w:
            pieces[-1][1] = b
        else:
            pieces.append([a, b, w])
    texts = _split_text(s.text, pos, [b for _a, b, _w in pieces[:-1]])
    out = []
    for j, ((a, b, w), t) in enumerate(zip(pieces, texts)):
        st_t = s.start if j == 0 else round(max(ct[a][0] - PAD, (ct[a - 1][1] + ct[a][0]) / 2), 3)
        en_t = s.end if j == len(pieces) - 1 else round(min(ct[b][1] + PAD, (ct[b][1] + ct[b + 1][0]) / 2), 3)
        ex = {k: v for k, v in s.extra.items() if k not in ("ct", "breaths")}
        ex["ct"] = [list(x) for x in s.extra["ct"][a:b + 1]]
        if w != s.speaker:
            ex["interject"] = s.speaker      # tách từ câu của người nói này (người khác nói chen ngang)
        out.append(Segment(st_t, en_t, t, "", w, ex))
    return out


def split_interjections(segs: list[Segment], audio: np.ndarray, emb_fn, cents: dict[str, np.ndarray]) -> int:
    """Người khác nói CHEN NGANG giữa câu ("一，二，找死！三…" – 找死 là người khác): vế ngắn có chỗ ngừng ở 2 bên (mốc
    từng chữ) mà giọng không giống người nói của câu, giống hẳn 1 người nói khác -> tách thành câu riêng của người đó
    (lồng tiếng đúng giọng, đúng lúc). Phần trước / sau giữ người nói cũ. Sửa thẳng `segs`, trả số câu được tách (2.1.20)."""
    if len(cents) < 2:
        return 0
    out, n = [], 0
    for s in segs:
        pieces = _interjection(s, audio, emb_fn, cents)
        if pieces:
            out += pieces
            n += 1
        else:
            out.append(s)
    segs[:] = out
    return n


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

    assign_speakers.short_fixed = assign_speakers.interjections = 0
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
    labels = cluster(embs, threshold, n_speakers, np.array([segs[i].duration for i in idx]))
    for i, l in zip(idx, labels):
        segs[i].speaker = f"S{l + 1}"
    # câu ngắn chưa có nhãn -> lấy theo câu gần nhất về thời gian
    known = [(segs[i].start, segs[i].speaker) for i in idx]
    for s in segs:
        if not s.speaker:
            s.speaker = min(known, key=lambda k: abs(k[0] - s.start))[1]
    cents = centroids(segs, idx, embs)
    if len(cents) >= 2:   # 2.1.20: câu ngắn theo giọng giống nhất, người khác nói chen ngang giữa câu -> câu riêng
        try:
            f = _emb_fn()
            assign_speakers.short_fixed = short_speakers(segs, audio, f, cents, set(idx))
            if split_changes:
                assign_speakers.interjections = split_interjections(segs, audio, f, cents)
        except Cancelled:
            raise
        except Exception:  # noqa: BLE001 – bước phụ: giữ kết quả gom cụm
            from .config import log_error

            log_error("Câu ngắn / câu chen ngang theo giọng")
    if progress:
        progress(1.0, f"Tìm thấy {len(set(labels.tolist()))} người nói")
    return len(set(labels.tolist()))
