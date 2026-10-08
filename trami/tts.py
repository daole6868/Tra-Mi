"""Lồng tiếng Việt nhiều giọng.

Giọng cho từng người nói (S1, S2…) được ghi dạng chuỗi:
  - "edge:vi-VN-HoaiMyNeural"  : Microsoft Edge TTS (miễn phí, cần Internet)
  - "vieneu:Ngọc Huyền"        : 25 giọng Việt dựng sẵn của VieNeu-TTS (chạy offline, GPU nhanh)
  - "clone"                    : NHÁI GIỌNG nhân vật gốc bằng VieNeu-TTS (lấy mẫu 5–12 giây từ chính video)

Mỗi câu được đọc rồi tự co giãn tốc độ cho vừa khung thời gian của phụ đề.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import subprocess
import unicodedata
import threading
import wave
from pathlib import Path
from typing import Callable

import numpy as np

from .ffmpeg import Cancelled, ffmpeg_bin, _NO_WINDOW
from .subtitle import Segment

EDGE_VOICES = [
    {"id": "edge:vi-VN-HoaiMyNeural", "name": "Hoài My – Nữ (Edge)", "gender": "female"},
    {"id": "edge:vi-VN-NamMinhNeural", "name": "Nam Minh – Nam (Edge)", "gender": "male"},
]
CLONE = "clone"
SR = 24000
_vn_model = None
_vn_lock = threading.Lock()
_vn_infer_lock = threading.Lock()   # mỗi lúc chỉ 1 câu được đọc trên GPU


def _vn_infer(tts, text: str, **kw):
    with _vn_infer_lock:
        return tts.infer(text, **kw)


# ------------------------------------------------------------------ danh sách giọng

def vieneu_available() -> bool:
    from .config import installed

    return installed("vieneu")


def vieneu_presets(mode: str = "v3turbo") -> list[dict]:
    """Đọc danh sách giọng dựng sẵn từ file của VieNeu (không cần tải mô hình)."""
    try:
        import importlib.util

        spec = importlib.util.find_spec("vieneu")
        base = Path(list(spec.submodule_search_locations)[0])
        f = base / "assets" / ("voices_v3_nano.json" if mode == "v3nano" else "voices_v3_turbo.json")
        data = json.loads(f.read_text("utf-8"))
        out = []
        for name, v in data.get("presets", {}).items():
            desc = v.get("description", "")
            out.append({"id": f"vieneu:{name}", "name": f"{name} – {desc} (VieNeu)", "gender": v.get("gender", "")})
        return out
    except Exception:
        return []


def list_voices(settings: dict | None = None) -> list[dict]:
    mode = (settings or {}).get("vieneu_mode", "v3turbo")
    voices = list(EDGE_VOICES)
    if vieneu_available():
        voices.insert(0, {"id": CLONE, "name": "🎭 Nhái giọng nhân vật gốc (VieNeu)", "gender": ""})
        voices += vieneu_presets(mode)
    return voices


def auto_assign(speakers: list[str], settings: dict | None = None, current: dict | None = None) -> dict:
    """Gán sẵn giọng khác nhau cho từng người nói (xen kẽ nam/nữ)."""
    current = dict(current or {})
    engine = (settings or {}).get("tts_engine", "edge")
    if engine == "vieneu" and vieneu_available():
        pool = [v["id"] for v in vieneu_presets((settings or {}).get("vieneu_mode", "v3turbo"))]
    else:
        pool = [v["id"] for v in EDGE_VOICES]
    used = set(current.values())
    i = 0
    for spk in speakers:
        if current.get(spk):
            continue
        free = [v for v in pool if v not in used] or pool
        current[spk] = free[i % len(free)]
        used.add(current[spk])
        i += 1
    return current


# ------------------------------------------------------------------ âm thanh

def _decode(path: str | Path, speed: float = 1.0, sr: int = SR) -> np.ndarray:
    af = []
    s = speed
    while s > 2.0:
        af.append("atempo=2.0")
        s /= 2.0
    if abs(s - 1.0) > 0.01:
        af.append(f"atempo={s:.4f}")
    cmd = [ffmpeg_bin(), "-hide_banner", "-loglevel", "error", "-i", str(path)]
    if af:
        cmd += ["-af", ",".join(af)]
    cmd += ["-f", "s16le", "-ac", "1", "-ar", str(sr), "pipe:1"]
    raw = subprocess.run(cmd, capture_output=True, creationflags=_NO_WINDOW).stdout
    return np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0


def _trim_silence(a: np.ndarray, thr: float = 0.01) -> np.ndarray:
    idx = np.where(np.abs(a) > thr)[0]
    if len(idx) == 0:
        return a[:0]
    s, e = max(0, idx[0] - int(0.02 * SR)), min(len(a), idx[-1] + int(0.06 * SR))
    return a[s:e]


def write_wav(path: str | Path, a: np.ndarray, sr: int = SR) -> Path:
    a = np.clip(np.asarray(a, dtype=np.float32), -1.0, 1.0)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes((a * 32767).astype(np.int16).tobytes())
    return Path(path)


# ------------------------------------------------------------------ mẫu giọng để nhái

def build_reference(ref_audio: str | Path, segs: list[Segment], speaker: str, out: Path,
                    min_sec: float = 5.0, max_sec: float = 12.0) -> Path | None:
    """Ghép các câu dài, rõ của 1 người nói thành file mẫu 5–12 giây."""
    cand = [s for s in segs if (s.speaker or "S1") == speaker and not s.extra.get("song") and s.duration >= 1.0]
    if not cand:
        return None
    cand.sort(key=lambda s: -s.duration)
    audio = _decode(ref_audio, sr=SR)
    if len(audio) == 0:
        return None
    parts, total = [], 0.0
    gap = np.zeros(int(0.25 * SR), np.float32)
    for s in cand:
        clip = audio[int(s.start * SR):int(min(s.end, s.start + 6) * SR)]
        if len(clip) < SR * 0.8 or np.abs(clip).max() < 0.02:
            continue
        parts += [clip, gap]
        total += len(clip) / SR
        if total >= max_sec:
            break
    if total < 2.0:
        return None
    ref = np.concatenate(parts)[: int(max_sec * SR)]
    peak = float(np.abs(ref).max())
    if peak > 0:
        ref = ref * (0.9 / peak)
    out.parent.mkdir(parents=True, exist_ok=True)
    return write_wav(out, ref)


# ------------------------------------------------------------------ bộ đọc

async def _edge_all(items: list[tuple[int, str, Path, str]], rate: str, pitch: str, progress, cancel,
                    total: int, done0: int, concurrency: int = 6):
    import edge_tts

    sem = asyncio.Semaphore(concurrency)
    done = done0
    errors: list[str] = []

    async def one(i: int, text: str, out: Path, voice: str):
        nonlocal done
        async with sem:
            if cancel and cancel():
                return
            for attempt in range(4):
                try:
                    comm = edge_tts.Communicate(text, voice, rate=rate, pitch=pitch)
                    await comm.save(str(out))
                    if out.exists() and out.stat().st_size > 0:
                        break
                except Exception as e:  # mạng chập chờn -> thử lại
                    if attempt == 3:
                        errors.append(f"Câu {i + 1}: {e}")
                    await asyncio.sleep(1.5 * (attempt + 1))
            done += 1
            if progress:
                progress(done / max(1, total) * 0.8, f"Đang tạo giọng đọc {done}/{total}")

    await asyncio.gather(*(one(*it) for it in items))
    return errors


def _vieneu(mode: str):
    global _vn_model
    with _vn_lock:
        if _vn_model is None or getattr(_vn_model, "_trami_mode", None) != mode:
            # Tắt "CUDA graph" của VieNeu: khi chụp graph, mọi việc GPU ở luồng khác (trình phát video giải mã
            # bằng GPU, tách giọng…) làm hỏng phiên chụp -> lỗi cudaErrorStreamCaptureInvalidated.
            os.environ.setdefault("VIENEU_FUSED_FRAME", "0")
            from vieneu import Vieneu

            _vn_model = Vieneu(mode=mode)
            _vn_model._trami_mode = mode
        return _vn_model


def _speed_tag(rate: str) -> float:
    try:
        return 1.0 + float(rate.strip().rstrip("%")) / 100.0
    except Exception:
        return 1.0


_TTS_OK = re.compile(r"[^0-9A-Za-zÀ-ỹ\s.,!?;:'\"()%\-–…]")


def clean_tts_text(text: str) -> str:
    """Bỏ ký tự giọng đọc tiếng Việt không đọc được (chữ Hán sót lại, emoji, ký hiệu lạ)."""
    t = unicodedata.normalize("NFC", text or "")
    t = t.replace("“", '"').replace("”", '"').replace("‘", "'").replace("’", "'").replace("—", "-")
    t = _TTS_OK.sub(" ", t)
    t = re.sub(r"\(\s*\)", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def _why_nothing(total: int, songs: int, empty: int, nodub: int) -> str:
    parts = []
    if songs:
        parts.append(f"{songs} câu bị đánh dấu là bài hát 🎵 (tắt công tắc 'Giữ nguyên bài hát' ở tab Lồng tiếng, "
                     "hoặc bỏ tích cột 🎵 ở tab Phụ đề)")
    if empty:
        parts.append(f"{empty} câu chưa có bản dịch tiếng Việt")
    if nodub:
        parts.append(f"{nodub} câu được đánh dấu 'Bỏ lồng'")
    return f"Không có câu nào để lồng tiếng trong {total} câu: " + "; ".join(parts or ["không rõ lý do"]) + "."


def synthesize_dub(segs: list[Segment], out_wav: str | Path, total_duration: float, work_dir: str | Path,
                   voice: str = "edge:vi-VN-HoaiMyNeural", rate: str = "+0%", pitch: str = "+0Hz",
                   max_speedup: float = 1.6, progress: Callable[[float, str], None] | None = None,
                   cancel: Callable[[], bool] | None = None, voices: dict | None = None,
                   ref_audio: str | Path | None = None, settings: dict | None = None,
                   skip_songs: bool = True, stats: dict | None = None) -> Path:
    settings = settings or {}
    stats = stats if stats is not None else {}
    n_song = n_empty = n_nodub = 0
    work = Path(work_dir)
    work.mkdir(parents=True, exist_ok=True)
    voices = voices or {}
    if not voice.startswith(("edge:", "vieneu:")) and voice != CLONE:
        voice = "edge:" + voice  # tương thích bản cũ
    mode = settings.get("vieneu_mode", "v3turbo")

    edge_items: list[tuple[int, str, Path, str]] = []
    vn_items: list[tuple[int, str, Path, str]] = []
    for i, s in enumerate(segs):
        t = clean_tts_text(s.trans or "")
        s.extra.pop("_tts", None)
        if not t:
            n_empty += 1
            continue
        if s.extra.get("nodub"):
            n_nodub += 1
            continue
        if skip_songs and s.extra.get("song"):
            n_song += 1
            continue
        spec = voices.get(s.speaker or "S1") or voice
        if spec in (CLONE,) or spec.startswith("vieneu:"):
            if not vieneu_available():
                spec = "edge:vi-VN-HoaiMyNeural"
        if spec == CLONE:
            spec = f"clone:{s.speaker or 'S1'}"
        h = hashlib.md5(f"{t}|{spec}|{rate}|{pitch}|{mode}".encode()).hexdigest()[:14]
        if spec.startswith("edge:"):
            p = work / f"tts_{h}.mp3"
            if not (p.exists() and p.stat().st_size > 0):
                edge_items.append((i, t, p, spec[5:]))
        else:
            p = work / f"tts_{h}.wav"
            if not (p.exists() and p.stat().st_size > 0):
                vn_items.append((i, t, p, spec))
        s.extra["_tts"] = str(p)
    total_items = len(edge_items) + len(vn_items)
    if progress:
        progress(0.0, f"Đang tạo giọng đọc 0/{total_items}")
    errors: list[str] = []

    # --- VieNeu (offline): dựng sẵn hoặc nhái giọng
    if vn_items:
        if progress:
            progress(0.01, "Đang tải VieNeu-TTS (lần đầu tải mô hình ~1GB)…")
        tts = _vieneu(mode)
        refs: dict[str, Path | None] = {}
        speed = _speed_tag(rate)
        for n, (i, text, p, spec) in enumerate(vn_items):
            if cancel and cancel():
                raise Cancelled()
            try:
                if spec.startswith("clone:"):
                    spk = spec[6:]
                    if spk not in refs:
                        refs[spk] = build_reference(ref_audio, segs, spk, work / f"ref_{spk}.wav") if ref_audio else None
                    if refs[spk] is None:  # không đủ mẫu -> dùng giọng mặc định
                        wav = _vn_infer(tts, text)
                    else:
                        wav = _vn_infer(tts, text, ref_audio=str(refs[spk]))
                else:
                    wav = _vn_infer(tts, text, voice=spec[7:] or None)
                sr = int(getattr(tts, "sample_rate", 48000))
                tmp = p.with_suffix(".raw.wav")
                write_wav(tmp, wav, sr)
                if abs(speed - 1) > 0.01:
                    a = _decode(tmp, speed, sr)
                    write_wav(p, a, sr)
                    tmp.unlink(missing_ok=True)
                else:
                    tmp.replace(p)
            except Exception as e:
                errors.append(f"Câu {i + 1}: {e}")
            if progress:
                progress((n + 1) / max(1, total_items) * 0.8, f"VieNeu đang đọc {n + 1}/{total_items}")

    # --- Edge (online)
    if edge_items:
        errors += asyncio.run(_edge_all(edge_items, rate, pitch, progress, cancel, total_items, len(vn_items)))
    if cancel and cancel():
        raise Cancelled()
    ok = sum(1 for s in segs if s.extra.get("_tts") and Path(s.extra["_tts"]).exists())
    if ok == 0:
        if total_items:
            raise RuntimeError("Không tạo được giọng đọc. " + "; ".join(errors[:3]))
        raise RuntimeError(_why_nothing(len(segs), n_song, n_empty, n_nodub))

    # --- ghép theo thời gian
    total = max(total_duration, max((s.end for s in segs), default=0) + 1)
    track = np.zeros(int(total * SR) + SR, dtype=np.float32)
    order = sorted(range(len(segs)), key=lambda k: segs[k].start)
    placed = 0
    for n, k in enumerate(order):
        s = segs[k]
        p = s.extra.pop("_tts", None)
        if not p or not Path(p).exists():
            continue
        clip = _trim_silence(_decode(p))
        if len(clip) == 0:
            errors.append(f"Câu {k + 1}: giọng đọc tạo ra toàn khoảng lặng")
            continue
        # khung thời gian cho phép: tới đầu câu kế tiếp (tận dụng khoảng lặng)
        nxt = next((segs[order[m]].start for m in range(n + 1, len(order))), total)
        slot = max(0.3, min(nxt, total) - s.start - 0.05)
        need = len(clip) / SR
        if need > slot:
            speed = min(max_speedup, need / slot)
            clip = _trim_silence(_decode(p, speed))
        st = int(s.start * SR)
        seg_len = min(len(clip), len(track) - st)
        if seg_len > 0:
            track[st:st + seg_len] += clip[:seg_len]
            placed += 1
        if progress and n % 20 == 0:
            progress(0.8 + 0.2 * n / max(1, len(order)), "Đang ghép giọng đọc theo thời gian")
    stats.update({"placed": placed, "total": len(segs), "songs": n_song, "empty": n_empty,
                  "nodub": n_nodub, "errors": len(errors), "error_samples": errors[:3]})
    if placed == 0:
        raise RuntimeError("Bản lồng tiếng tạo ra không có tiếng nào. " + "; ".join(errors[:3]))
    peak = float(np.max(np.abs(track))) if len(track) else 0
    if peak > 0.98:
        track *= 0.98 / peak
    write_wav(out_wav, track[: int(total * SR)])
    if progress:
        msg = "Đã tạo xong bản lồng tiếng"
        if errors:
            msg += f" ({len(errors)} câu lỗi, đã bỏ qua)"
        progress(1.0, msg)
    return Path(out_wav)


def preview_voice(spec: str, text: str, out: Path, settings: dict | None = None,
                  ref_audio: str | Path | None = None) -> Path:
    """Tạo file nghe thử 1 giọng."""
    settings = settings or {}
    text = clean_tts_text(text) or "Xin chào, đây là giọng đọc thử nghiệm của TraMi."
    if spec.startswith("edge:"):
        import edge_tts

        async def run():
            await edge_tts.Communicate(text, spec[5:]).save(str(out.with_suffix(".mp3")))
        asyncio.run(run())
        return out.with_suffix(".mp3")
    tts = _vieneu(settings.get("vieneu_mode", "v3turbo"))
    if spec == CLONE or spec.startswith("clone"):
        wav = _vn_infer(tts, text, ref_audio=str(ref_audio)) if ref_audio else _vn_infer(tts, text)
    else:
        wav = _vn_infer(tts, text, voice=spec[7:] or None)
    return write_wav(out.with_suffix(".wav"), wav, int(getattr(tts, "sample_rate", 48000)))
