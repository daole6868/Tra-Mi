"""Lọc câu "nghe nhầm": nhạc nền, tiếng hát, tiếng động lọt vào bản giọng đã tách bị bộ nghe chép thành lời thoại.

Cách làm (trên giọng đã tách khỏi nhạc, 16 kHz): đo cao độ (F0, khung 20 ms – `songs.f0_frames`) + độ to từng câu.
1. Hồ sơ giọng từng người nói = các câu CHẮC là lời thoại (nhiều tiếng hữu thanh, đủ to, không ngân như hát, tốc độ nói
   bình thường): cao độ trung vị + khoảng p10–p90 (nửa cung). Người nói ít câu -> dùng hồ sơ chung cả phim.
2. Câu nghi nghe nhầm: gần như không có tiếng hữu thanh (tiếng động / nhạc không cao độ), nhỏ hơn hẳn lời thoại thật
   (nhạc lọt vào bản tách), cao độ nằm ngoài khoảng của người nói ± dung sai (nhạc cụ, giọng hát cao vút), hoặc cao độ
   đứng yên như nốt nhạc. Chỉ ĐÁNH DẤU (`extra["noise"]` = lý do) để người dùng nghe lại rồi xoá – không tự xoá.
3. Căn đầu / cuối câu theo đoạn có giọng thật trong khoảng cao độ: câu kéo dài sang nhạc / tiếng động > 0,3 s mới
   thu lại (giữ ít nhất 0,5 s, không bớt quá 40 % câu, chừa 0,08 s trước / 0,15 s sau cho phụ âm đầu / cuối).
"""
from __future__ import annotations

import re

import numpy as np

from .songs import HOP, SR, f0_frames, note_share

TOL = 4.0            # dung sai mặc định: ± 4 nửa cung ngoài khoảng p10–p90 của người nói
MIN_VOICED = 0.15    # < 15 % khung có tiếng là hữu thanh -> gần như không có giọng nói
QUIET_DB = 20.0      # nhỏ hơn mức lời thoại thật > 20 dB -> nhạc / tiếng động lọt vào bản tách giọng
NOTE_SUS = 0.6       # ≥ 60 % thời gian hữu thanh là nốt ngân đứng yên -> giống nhạc cụ / tiếng hát
MIN_PROFILE = 8      # người nói cần ≥ 8 câu chắc chắn mới có hồ sơ riêng
EDGE = 0.3           # đầu / cuối câu thừa > 0,3 s không có giọng mới thu lại
_HAN = re.compile(r"[㐀-鿿]")


def _semi(f: np.ndarray) -> np.ndarray:
    return 12 * np.log2(f / 100.0)


def _hz(st: float) -> float:
    return float(100.0 * 2 ** (st / 12))


def line_features(audio: np.ndarray, s, sr: int = SR) -> dict:
    """Đặc điểm 1 câu trên bản giọng đã tách: tỉ lệ hữu thanh, cao độ (nửa cung so với 100 Hz) từng khung, độ to (dB),
    tỉ lệ nốt ngân."""
    scale = 1 / 32768.0 if audio.dtype == np.int16 else 1.0
    a = np.asarray(audio[max(0, int(s.start * sr)):int(s.end * sr)], np.float32) * scale
    f0, rms = f0_frames(a, sr, active_only=True)
    out = {"voiced": 0.0, "st": np.zeros(0), "times": np.zeros(0), "db": -120.0, "note": 0.0, "n": len(rms)}
    if not len(rms) or not rms.any():
        return out
    act = rms > 0.1 * np.percentile(rms, 95)
    v = act & ~np.isnan(f0) & (f0 >= 70) & (f0 <= 900)
    out["voiced"] = float(v.sum() / max(1, act.sum()))
    out["db"] = float(20 * np.log10(max(1e-6, float(np.percentile(rms[act], 90)))))
    out["st"] = _semi(f0[v])
    out["times"] = s.start + 0.02 + np.flatnonzero(v) * HOP      # giữa khung 40 ms
    out["note"] = note_share(a, sr) if s.end - s.start >= 0.6 else 0.0
    return out


def _speech_like(s, ft: dict, ref_db: float | None) -> bool:
    cps = len(_HAN.findall(s.text or "")) / max(0.3, s.end - s.start)
    return (ft["voiced"] >= 0.35 and ft["note"] < 0.35 and s.end - s.start >= 0.6 and len(ft["st"]) >= 10
            and (ref_db is None or ft["db"] >= ref_db - 10) and (cps == 0 or 1.5 <= cps <= 9.0))


def profiles(segs: list, feats: list[dict]) -> tuple[dict, tuple | None, float | None]:
    """Hồ sơ cao độ: ({người nói: (p10, p50, p90)}, hồ sơ chung, mức to lời thoại dB)."""
    cand = [k for k, s in enumerate(segs) if feats[k]["voiced"] >= 0.35 and not s.extra.get("song")]
    ref_db = float(np.median([feats[k]["db"] for k in cand])) if len(cand) >= 3 else None
    good = [k for k in cand if _speech_like(segs[k], feats[k], ref_db)]
    meds = {k: float(np.median(feats[k]["st"])) for k in good}

    def prof(ks):
        v = np.array([meds[k] for k in ks])
        return float(np.percentile(v, 10)), float(np.median(v)), float(np.percentile(v, 90))

    by: dict[str, list[int]] = {}
    for k in good:
        by.setdefault(segs[k].speaker or "S1", []).append(k)
    per = {spk: prof(ks) for spk, ks in by.items() if len(ks) >= MIN_PROFILE}
    overall = prof(good) if len(good) >= 5 else None
    return per, overall, ref_db


def check_lines(segs: list, audio: np.ndarray, tol: float = TOL, trim: bool = True, sr: int = SR,
                progress=None) -> dict:
    """Đánh dấu câu nghi nghe nhầm + thu đầu / cuối câu thừa. Sửa `segs` tại chỗ. Trả {"suspect": [chỉ số],
    "trimmed": số câu đã căn, "profiles": {người nói: "a–b Hz"}}."""
    feats = []
    for k, s in enumerate(segs):
        feats.append(line_features(audio, s, sr))
        if progress and k % 50 == 0:
            progress(0.1 + 0.7 * k / max(1, len(segs)), f"Đang đo cao độ giọng {k}/{len(segs)} câu")
    per, overall, ref_db = profiles(segs, feats)
    suspect, trimmed = [], 0
    for k, s in enumerate(segs):
        ft = feats[k]
        s.extra.pop("noise", None)
        if s.extra.get("song") or s.extra.get("voice_ok") or s.end - s.start < 0.25:
            continue
        prof = per.get(s.speaker or "S1") or overall
        lo = hi = None
        if prof:
            lo, hi = prof[0] - tol, prof[2] + tol
        why = ""
        if ft["n"] and ft["voiced"] < MIN_VOICED:
            why = f"gần như không có giọng nói ({ft['voiced'] * 100:.0f} % hữu thanh) – nhạc / tiếng động"
        elif ref_db is not None and ft["db"] < ref_db - QUIET_DB:
            why = f"nhỏ hơn lời thoại {ref_db - ft['db']:.0f} dB – nhạc / tiếng động lọt vào bản tách giọng"
        elif prof and len(ft["st"]) >= 5:
            med = float(np.median(ft["st"]))
            if not lo <= med <= hi:
                why = (f"cao độ {_hz(med):.0f} Hz khác hẳn giọng {s.speaker or 'S1'} "
                       f"({_hz(prof[0]):.0f}–{_hz(prof[2]):.0f} Hz ± {tol:g} nửa cung) – nhạc cụ / tiếng hát")
        if not why and ft["note"] >= NOTE_SUS and len(ft["st"]) >= 5:
            why = f"cao độ đứng yên như nốt nhạc ({ft['note'] * 100:.0f} %) – tiếng hát / nhạc cụ"
        if why:
            s.extra["noise"] = why
            suspect.append(k)
            continue
        if trim and prof and len(ft["st"]) >= 5:
            # từng khung cao độ dao động rộng hơn trung vị cả câu -> nới thêm 6 nửa cung khi tìm lúc có giọng
            ok = ft["times"][(ft["st"] >= lo - 6) & (ft["st"] <= hi + 6)]
            if len(ok):
                a = float(ok[0]) - 0.08
                b = float(ok[-1]) + 0.15
                dur = s.end - s.start
                na = s.start if a - s.start < EDGE else min(a, s.start + 0.2 * dur)
                nb = s.end if s.end - b < EDGE else max(b, s.end - 0.2 * dur)
                if nb - na >= max(0.5, len(_HAN.findall(s.text or "")) / 9.0) and (na > s.start or nb < s.end):
                    s.start, s.end = round(na, 3), round(nb, 3)
                    trimmed += 1
    names = {spk: f"{_hz(p[0]):.0f}–{_hz(p[2]):.0f} Hz" for spk, p in per.items()}
    if overall:
        names.setdefault("chung", f"{_hz(overall[0]):.0f}–{_hz(overall[2]):.0f} Hz")
    return {"suspect": suspect, "trimmed": trimmed, "profiles": names}
