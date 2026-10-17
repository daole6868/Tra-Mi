"""Lồng tiếng Việt nhiều giọng.

Giọng cho từng người nói (S1, S2…) được ghi dạng chuỗi:
  - "edge:vi-VN-HoaiMyNeural"  : Microsoft Edge TTS (miễn phí, cần Internet)
  - "vieneu:Ngọc Huyền"        : 25 giọng Việt dựng sẵn của VieNeu-TTS (chạy offline, GPU nhanh)
  - "clone"                    : NHÁI GIỌNG nhân vật gốc bằng VieNeu-TTS (lấy mẫu 5–12 giây từ chính video)
  - "omni:<id>"                : OmniVoice nhái giọng từ FILE GHI ÂM MẪU trong thư viện giọng mẫu (trami/voices.py)
  - "omniclone"                : OmniVoice nhái giọng nhân vật gốc (mẫu 3–10 giây cắt từ video + lời gốc)

Mỗi câu được đọc rồi tự co giãn tốc độ cho vừa khung thời gian của phụ đề.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import subprocess
import sys
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
OMNI_CLONE = "omniclone"
SR = 24000
_vn_model = None
_vn_lock = threading.Lock()


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
    if omnivoice_available():
        from . import voices as vlib

        omni = [{"id": f"omni:{v['id']}", "name": f"🎙 {v['name']} – giọng mẫu (OmniVoice)", "gender": ""}
                for v in vlib.list_samples()]
        omni.append({"id": OMNI_CLONE, "name": "🎭 Nhái giọng nhân vật gốc (OmniVoice)", "gender": ""})
        voices = omni + voices
    return voices


def auto_assign(speakers: list[str], settings: dict | None = None, current: dict | None = None) -> dict:
    """Gán sẵn giọng khác nhau cho từng người nói (xen kẽ nam/nữ)."""
    current = dict(current or {})
    engine = (settings or {}).get("tts_engine", "edge")
    if engine == "vieneu" and vieneu_available():
        pool = [v["id"] for v in vieneu_presets((settings or {}).get("vieneu_mode", "v3turbo"))]
    elif engine == "omnivoice" and omnivoice_available():
        from . import voices as vlib

        pool = [f"omni:{v['id']}" for v in vlib.list_samples()] or [OMNI_CLONE]
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


def _atempo(a: np.ndarray, speed: float) -> np.ndarray:
    """Tăng tốc đoạn âm thanh (giữ cao độ) bằng FFmpeg atempo."""
    if abs(speed - 1) < 0.01 or len(a) == 0:
        return a
    raw = (np.clip(a, -1, 1) * 32767).astype(np.int16).tobytes()
    af = []
    s = speed
    while s > 2.0:
        af.append("atempo=2.0")
        s /= 2.0
    af.append(f"atempo={s:.4f}")
    cmd = [ffmpeg_bin(), "-hide_banner", "-loglevel", "error", "-f", "s16le", "-ac", "1", "-ar", str(SR),
           "-i", "pipe:0", "-af", ",".join(af), "-f", "s16le", "-ac", "1", "-ar", str(SR), "pipe:1"]
    out = subprocess.run(cmd, input=raw, capture_output=True, creationflags=_NO_WINDOW).stdout
    return np.frombuffer(out, dtype=np.int16).astype(np.float32) / 32768.0


def _decode_cached(path: str | Path) -> np.ndarray:
    """Giải mã câu đã đọc (24 kHz) có lưu đệm .npy bên cạnh -> lồng lại vài câu không phải giải mã lại cả video."""
    p = Path(path)
    c = p.with_name(p.name + ".npy")
    try:
        if c.exists() and c.stat().st_mtime >= p.stat().st_mtime:
            return np.load(c).astype(np.float32) / 32768.0
    except Exception:  # noqa: BLE001 – file đệm hỏng -> giải mã lại
        pass
    a = _decode(p)
    try:
        np.save(c, (np.clip(a, -1, 1) * 32767).astype(np.int16))
    except OSError:
        pass
    return a


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


def build_reference_text(ref_audio: str | Path, segs: list[Segment], speaker: str, out: Path,
                         max_sec: float = 10.0) -> tuple[Path, str] | None:
    """Mẫu giọng 3–10 giây ghép từ các CÂU NGUYÊN VẸN của 1 người nói + lời gốc tương ứng (cho OmniVoice)."""
    cand = [s for s in segs if (s.speaker or "S1") == speaker and not s.extra.get("song")
            and 1.0 <= s.duration <= 8.0 and s.text.strip()]
    if not cand:
        return None
    cand.sort(key=lambda s: -s.duration)
    audio = _decode(ref_audio, sr=SR)
    if len(audio) == 0:
        return None
    parts, texts, total = [], [], 0.0
    gap = np.zeros(int(0.2 * SR), np.float32)
    for s in cand:
        if total + s.duration > max_sec and total >= 3.0:
            continue
        clip = audio[int(s.start * SR):int(s.end * SR)]
        if len(clip) < SR * 0.8 or np.abs(clip).max() < 0.02:
            continue
        parts += [clip, gap]
        texts.append(s.text.strip())
        total += len(clip) / SR
        if total >= max_sec - 1:
            break
    if total < 2.0:
        return None
    ref = np.concatenate(parts)[: int(max_sec * SR)]
    peak = float(np.abs(ref).max())
    if peak > 0:
        ref = ref * (0.9 / peak)
    out.parent.mkdir(parents=True, exist_ok=True)
    return write_wav(out, ref), " ".join(texts)


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


class VieneuWorker:
    """Điều khiển tiến trình riêng chạy VieNeu (trami/vieneu_worker.py). Mỗi lúc chỉ 1 câu được đọc."""

    title = "VieNeu"

    def __init__(self, mode: str):
        self.mode = mode
        self.sr = 48000
        self.proc: subprocess.Popen | None = None
        self.lock = threading.Lock()
        self.fused = "1"      # CUDA graph của VieNeu (nhanh); tắt nếu GPU báo lỗi chụp graph

    flag = "--vieneu-worker"
    log_name = "vieneu.txt"

    def _extra_env(self) -> dict:
        return {}

    def _cmd(self) -> list[str]:
        from .config import APP_DIR, FROZEN

        if FROZEN:
            return [sys.executable, self.flag, self.mode]
        return [sys.executable, str(APP_DIR / "TraMi.py"), self.flag, self.mode]

    def _start(self, progress=None, cancel=None):
        from .config import APP_DIR, DATA_DIR

        env = dict(os.environ, VIENEU_FUSED_FRAME=self.fused, PYTHONUTF8="1", PYTHONIOENCODING="utf-8",
                   **self._extra_env())
        err = None  # log của VieNeu hiện trong cửa sổ đen; chạy không cửa sổ (pythonw/exe) thì ghi ra file
        if sys.stderr is None or not getattr(sys.stderr, "fileno", None):
            (DATA_DIR / "logs").mkdir(parents=True, exist_ok=True)
            err = open(DATA_DIR / "logs" / self.log_name, "a", encoding="utf-8")
        self.proc = subprocess.Popen(self._cmd(), stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=err,
                                     text=True, encoding="utf-8", errors="replace", bufsize=1, cwd=str(APP_DIR),
                                     env=env, creationflags=_NO_WINDOW)
        r = self._read(progress, cancel)
        if "error" in r:
            self.stop()
            raise RuntimeError(r["error"])
        self.sr = int(r.get("sr", 48000))

    def _read(self, progress=None, cancel=None) -> dict:
        assert self.proc and self.proc.stdout
        for line in self.proc.stdout:
            if not line.startswith("@@TRAMI@@"):
                continue
            obj = json.loads(line[len("@@TRAMI@@"):])
            if "progress" in obj:   # tiến độ tải / nạp mô hình từ tiến trình VieNeu
                if cancel and cancel():
                    self.stop()
                    raise Cancelled()
                if progress:
                    progress(float(obj["progress"]), obj.get("msg", ""))
                continue
            return obj
        code = self.proc.poll() if self.proc else None
        self.proc = None
        raise RuntimeError(f"Tiến trình {self.title} bị tắt đột ngột (mã {code}). Xem log trong cửa sổ đen để biết chi tiết.")

    def ensure_started(self, progress=None, cancel=None):
        """Mở tiến trình VieNeu và nạp mô hình (tự tải nếu chưa có), báo tiến độ tải."""
        with self.lock:
            if not self.proc or self.proc.poll() is not None:
                self._start(progress, cancel)

    def synth(self, text: str, out: str | Path, voice: str | None = None, ref_audio: str | None = None,
              ref_text: str | None = None, duration: float | None = None) -> Path:
        req = json.dumps({"text": text, "out": str(out), "voice": voice, "ref_audio": ref_audio,
                          "ref_text": ref_text or "", "duration": duration}, ensure_ascii=False)
        with self.lock:
            for attempt in range(2):
                try:
                    if not self.proc or self.proc.poll() is not None:
                        self._start()
                    assert self.proc and self.proc.stdin
                    self.proc.stdin.write(req + "\n")
                    self.proc.stdin.flush()
                    r = self._read()
                except (OSError, RuntimeError) as e:
                    self.stop()
                    if attempt == 0:
                        continue  # khởi động lại tiến trình 1 lần
                    raise RuntimeError(str(e)) from None
                if "error" in r:
                    if attempt == 0 and "captur" in r["error"].lower():
                        self.fused = "0"   # GPU không chụp được CUDA graph -> chạy lại không dùng graph
                        self.stop()
                        continue
                    raise RuntimeError(r["error"])
                return Path(out)
        raise RuntimeError(f"{self.title} không đọc được câu này")

    def stop(self):
        p, self.proc = self.proc, None
        if p and p.poll() is None:
            try:
                p.stdin.close()  # type: ignore[union-attr]
                p.wait(5)
            except Exception:
                p.kill()


def _vieneu(mode: str) -> VieneuWorker:
    global _vn_model
    with _vn_lock:
        if _vn_model is None or _vn_model.mode != mode:
            if _vn_model is not None:
                _vn_model.stop()
            _vn_model = VieneuWorker(mode)
            import atexit

            atexit.register(_vn_model.stop)
        return _vn_model


class OmniWorker(VieneuWorker):
    """Tiến trình riêng chạy OmniVoice (trami/omni_worker.py); mode = tên mô hình trên HuggingFace."""
    title = "OmniVoice"
    flag = "--omni-worker"
    log_name = "omnivoice.txt"

    def __init__(self, repo: str, token: str = ""):
        super().__init__(repo)
        self.token = token

    def _extra_env(self) -> dict:
        return {"HF_TOKEN": self.token} if self.token else {}


_omni_model: OmniWorker | None = None


def omnivoice_available() -> bool:
    from .config import installed

    return installed("omnivoice", "torch")


def _omni(settings: dict | None = None) -> OmniWorker:
    global _omni_model
    st = settings or {}
    repo = st.get("omnivoice_model") or "k2-fsa/OmniVoice"
    with _vn_lock:
        if _omni_model is None or _omni_model.mode != repo:
            if _omni_model is not None:
                _omni_model.stop()
            _omni_model = OmniWorker(repo, st.get("hf_token", ""))
            import atexit

            atexit.register(_omni_model.stop)
        return _omni_model


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
    if not voice.startswith(("edge:", "vieneu:", "omni:")) and voice not in (CLONE, OMNI_CLONE):
        voice = "edge:" + voice  # tương thích bản cũ
    mode = settings.get("vieneu_mode", "v3turbo")

    edge_items: list[tuple[int, str, Path, str]] = []
    vn_items: list[tuple[int, str, Path, str]] = []
    omni_items: list[tuple[int, str, Path, str]] = []
    omni_meta: dict[int, tuple[str, str]] = {}   # câu đọc bằng OmniVoice -> (mã giọng, lời) để đọc lại vừa thời lượng
    samples = {}
    if omnivoice_available():
        from . import voices as vlib

        samples = {v["id"]: v for v in vlib.list_samples()}
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
        spec = s.extra.get("voice") or voices.get(s.speaker or "S1") or voice   # giọng riêng của câu (nếu có)
        if spec in (CLONE,) or spec.startswith("vieneu:"):
            if not vieneu_available():
                spec = "edge:vi-VN-HoaiMyNeural"
        if spec == OMNI_CLONE or spec.startswith("omni:"):
            if not omnivoice_available() or (spec.startswith("omni:") and spec[5:] not in samples):
                spec = "edge:vi-VN-HoaiMyNeural"   # chưa cài OmniVoice / giọng mẫu đã bị xoá
        if spec == CLONE:
            spec = f"clone:{s.speaker or 'S1'}"
        if spec == OMNI_CLONE:
            spec = f"omniclone:{s.speaker or 'S1'}"
        extra = ""
        if spec.startswith("omni:"):   # sửa lời / file mẫu thì đọc lại
            sm = samples[spec[5:]]
            extra = f"{sm.get('text', '')}|{sm.get('file')}|{settings.get('omnivoice_model', '')}"
        take = int(s.extra.get("take", 0) or 0)   # "Tạo lại giọng": lần đọc mới của cùng câu
        h = hashlib.md5((f"{t}|{spec}|{rate}|{pitch}|{mode}|{extra}" + (f"|take{take}" if take else "")).encode()
                        ).hexdigest()[:14]
        if spec.startswith(("omni:", "omniclone:")):
            p = work / f"tts_{h}.wav"
            omni_meta[i] = (spec, t)
            if not (p.exists() and p.stat().st_size > 0):
                omni_items.append((i, t, p, spec))
        elif spec.startswith("edge:"):
            p = work / f"tts_{h}.mp3"
            if not (p.exists() and p.stat().st_size > 0):
                edge_items.append((i, t, p, spec[5:]))
        else:
            p = work / f"tts_{h}.wav"
            if not (p.exists() and p.stat().st_size > 0):
                vn_items.append((i, t, p, spec))
        s.extra["_tts"] = str(p)
    total_items = len(edge_items) + len(vn_items) + len(omni_items)
    if progress:
        progress(0.0, f"Đang tạo giọng đọc 0/{total_items}")
    errors: list[str] = []

    # --- VieNeu (offline): dựng sẵn hoặc nhái giọng
    if vn_items:
        if progress:
            progress(0.01, "Đang nạp VieNeu-TTS…")
        tts = _vieneu(mode)
        tts.ensure_started((lambda x, m: progress(0.01 * x, m)) if progress else None, cancel)
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
                    tmp = p.with_suffix(".raw.wav")
                    # không đủ mẫu giọng -> dùng giọng mặc định
                    tts.synth(text, tmp, ref_audio=str(refs[spk]) if refs[spk] else None)
                else:
                    tmp = p.with_suffix(".raw.wav")
                    tts.synth(text, tmp, voice=spec[7:] or None)
                sr = tts.sr
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

    orefs: dict[str, tuple] = {}

    def omni_ref(spec: str) -> tuple:
        """(file mẫu, lời trong file mẫu) cho mã giọng OmniVoice."""
        if spec not in orefs:
            if spec.startswith("omni:"):
                sm = samples[spec[5:]]
                orefs[spec] = (str(vlib.path_of(sm)), sm.get("text", ""))
            else:
                spk = spec[10:]
                r = build_reference_text(ref_audio, segs, spk, work / f"omniref_{spk}.wav") if ref_audio else None
                orefs[spec] = (str(r[0]), r[1]) if r else (None, "")
        return orefs[spec]

    # --- OmniVoice (offline): nhái giọng từ file ghi âm mẫu / từ nhân vật gốc
    if omni_items:
        if progress:
            progress(0.01, "Đang nạp OmniVoice…")
        om = _omni(settings)
        om.ensure_started((lambda x, m: progress(0.01 * x, m)) if progress else None, cancel)
        speed = _speed_tag(rate)
        base = len(vn_items)
        for n, (i, text, p, spec) in enumerate(omni_items):
            if cancel and cancel():
                raise Cancelled()
            try:
                ref, rtext = omni_ref(spec)
                tmp = p.with_suffix(".raw.wav")
                om.synth(text, tmp, ref_audio=ref, ref_text=rtext)
                sr = om.sr
                if abs(speed - 1) > 0.01:
                    write_wav(p, _decode(tmp, speed, sr), sr)
                    tmp.unlink(missing_ok=True)
                else:
                    tmp.replace(p)
            except Exception as e:
                errors.append(f"Câu {i + 1}: {e}")
            if progress:
                progress((base + n + 1) / max(1, total_items) * 0.8, f"OmniVoice đang đọc {n + 1}/{len(omni_items)}")

    # --- Edge (online)
    if edge_items:
        errors += asyncio.run(_edge_all(edge_items, rate, pitch, progress, cancel, total_items,
                                        len(vn_items) + len(omni_items)))
    if cancel and cancel():
        raise Cancelled()
    ok = sum(1 for s in segs if s.extra.get("_tts") and Path(s.extra["_tts"]).exists())
    if ok == 0:
        if total_items:
            raise RuntimeError("Không tạo được giọng đọc. " + "; ".join(errors[:3]))
        raise RuntimeError(_why_nothing(len(segs), n_song, n_empty, n_nodub))

    # --- xếp giọng theo thời gian (khớp lúc nhân vật nói, không bao giờ để 2 câu đọc chồng lên nhau)
    total = max(total_duration, max((s.end for s in segs), default=0) + 1)
    track = np.zeros(int(total * SR) + SR, dtype=np.float32)
    order = sorted(range(len(segs)), key=lambda k: segs[k].start)
    placed = 0
    prev_end = 0.0
    GAP, BORROW, DELAY, FADE = 0.04, 0.15, 0.25, 0.3
    layout: list[list] = []
    too_long: list[tuple[int, float, float]] = []   # (câu, thời lượng cần, thời lượng có) -> để AI rút gọn
    om = None
    for n, k in enumerate(order):
        s = segs[k]
        p = s.extra.pop("_tts", None)
        s.extra.pop("fit", None)
        if not p or not Path(p).exists():
            continue
        clip = _trim_silence(_decode_cached(p))
        if len(clip) == 0:
            errors.append(f"Câu {k + 1}: giọng đọc tạo ra toàn khoảng lặng")
            continue
        nxt = min(total, next((segs[order[m]].start for m in range(n + 1, len(order))), total))
        start = max(s.start, prev_end + GAP)
        slot = max(0.3, nxt - GAP - start)
        need = len(clip) / SR
        # mượn khoảng lặng phía trước (tối đa 0,15 s) nếu câu dài hơn chỗ trống
        if need > slot and start <= s.start + 1e-6:
            borrow = min(BORROW, need - slot, max(0.0, s.start - (prev_end + GAP)))
            start -= borrow
            slot += borrow
        speed = 1.0
        if need > slot * 1.03:
            if k in omni_meta and om is None:
                om = _omni(settings)
            if k in omni_meta:
                # OmniVoice đọc lại vừa đúng thời lượng (tự nhiên hơn tua nhanh file)
                spec, txt = omni_meta[k]
                target = max(slot, need / 1.5)
                fp = Path(p).with_suffix(f".d{int(target * 100)}.wav")
                try:
                    if not fp.exists():
                        ref, rtext = omni_ref(spec)
                        om.synth(txt, fp, ref_audio=ref, ref_text=rtext, duration=round(target, 2))
                    c2 = _trim_silence(_decode(fp))
                    if len(c2):
                        speed = need / max(0.1, len(c2) / SR)
                        clip, need = c2, len(c2) / SR
                except Exception as e:  # noqa: BLE001
                    errors.append(f"Câu {k + 1}: đọc lại vừa thời lượng lỗi ({e})")
            if need > slot * 1.03:
                sp2 = min(max_speedup, need / slot)
                # OmniVoice: tua tiếp bản vừa đọc lại; bộ đọc khác: tua thẳng từ file gốc (giữ chất lượng)
                clip = _trim_silence(_atempo(clip, sp2)) if k in omni_meta else _trim_silence(_decode(p, sp2))
                speed = speed * sp2
                need = len(clip) / SR
        fit = "ok" if speed <= 1.06 else "fast"
        end = start + need
        hard_end = nxt + DELAY - GAP          # câu sau được lùi tối đa 0,25 s
        if end > hard_end:
            # vẫn quá dài: nhỏ dần phần cuối (≤0,3 s) rồi dừng đúng lúc câu sau vào, đánh dấu ⚠ để sửa
            too_long.append((k, need * speed, hard_end - start))
            keep = max(int(0.3 * SR), int((hard_end - start) * SR))
            clip = clip[:keep].copy()
            f = min(int(FADE * SR), len(clip) // 3)
            if f > 0:
                clip[-f:] *= np.linspace(1.0, 0.0, f, dtype=np.float32)
            fit = "long"
        st = int(start * SR)
        seg_len = min(len(clip), len(track) - st)
        if seg_len > 0:
            track[st:st + seg_len] += clip[:seg_len]
            placed += 1
            prev_end = start + seg_len / SR
            layout.append([round(start, 3), round(prev_end, 3), k, fit])
        s.extra["fit"] = fit
        s.extra["fit_speed"] = round(speed, 2)
        if progress and n % 20 == 0:
            progress(0.8 + 0.2 * n / max(1, len(order)), "Đang xếp giọng đọc khớp thời gian nhân vật nói")
    stats["layout"] = layout
    stats["too_long"] = too_long
    stats["fit"] = {"ok": sum(1 for x in layout if x[3] == "ok"), "fast": sum(1 for x in layout if x[3] == "fast"),
                    "long": sum(1 for x in layout if x[3] == "long")}
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
    if spec.startswith("omni:") or spec == OMNI_CLONE:
        om = _omni(settings)
        if spec.startswith("omni:"):
            from . import voices as vlib

            sm = vlib.get(spec[5:])
            if not sm:
                raise ValueError("Giọng mẫu này đã bị xoá")
            return om.synth(text, out.with_suffix(".wav"), ref_audio=str(vlib.path_of(sm)), ref_text=sm.get("text", ""))
        ref, rtext = (ref_audio if isinstance(ref_audio, tuple) else (ref_audio, ""))
        return om.synth(text, out.with_suffix(".wav"), ref_audio=str(ref) if ref else None, ref_text=rtext)
    tts = _vieneu(settings.get("vieneu_mode", "v3turbo"))
    if spec == CLONE or spec.startswith("clone"):
        return tts.synth(text, out.with_suffix(".wav"), ref_audio=str(ref_audio) if ref_audio else None)
    return tts.synth(text, out.with_suffix(".wav"), voice=spec[7:] or None)
