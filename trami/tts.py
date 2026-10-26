"""Lồng tiếng Việt nhiều giọng.

Giọng cho từng người nói (S1, S2…) được ghi dạng chuỗi:
  - "edge:vi-VN-HoaiMyNeural"  : Microsoft Edge TTS (miễn phí, cần Internet)
  - "vieneu:Ngọc Huyền"        : 25 giọng Việt dựng sẵn của VieNeu-TTS (chạy offline, GPU nhanh)
  - "vieneu:mau:<tên>"         : VieNeu nhái 6 giọng thu âm sạch có sẵn trong gói VieNeu (assets/samples)
  - "vieneu:lib:<id>"          : VieNeu nhái giọng từ thư viện giọng mẫu của bạn (trami/voices.py)
  - "vieneu:trend:<mã>"        : 20 giọng VieNeu theo trend = giọng dựng sẵn + kiểu đọc + hiệu ứng (trami/trend_voices.py)
  - "clone"                    : NHÁI GIỌNG nhân vật gốc bằng VieNeu-TTS (lấy mẫu 5–12 giây từ chính video)
  - "omni:<id>"                : OmniVoice nhái giọng từ FILE GHI ÂM MẪU trong thư viện giọng mẫu (trami/voices.py)
  - "omniclone"                : OmniVoice nhái giọng nhân vật gốc (mẫu 3–10 giây cắt từ video + lời gốc)
  - "omnid:<mã>"               : OmniVoice giọng CHỌN SẴN tạo theo mô tả (trami/omni_design.py, không cần file mẫu)

Mỗi câu được đọc rồi tự co giãn tốc độ cho vừa khung thời gian của phụ đề.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import queue
import os
import re
import subprocess
import sys
import unicodedata
import threading
import time
import wave
from pathlib import Path
from typing import Callable

import numpy as np

from . import langs
from .ffmpeg import Cancelled, ffmpeg_bin, _NO_WINDOW
from .subtitle import Segment
from .voicefix import level_gains, speech_level, spoken_vi, voice_problem

EDGE_VOICES = [
    {"id": "edge:vi-VN-HoaiMyNeural", "name": "Hoài My – Nữ (Edge)", "gender": "female"},
    {"id": "edge:vi-VN-NamMinhNeural", "name": "Nam Minh – Nam (Edge)", "gender": "male"},
]
CLONE = "clone"
OMNI_CLONE = "omniclone"
VN_SAMPLE = "vieneu:mau:"   # giọng thu âm sạch đi kèm gói VieNeu
VN_LIB = "vieneu:lib:"      # giọng mẫu trong thư viện của người dùng
VN_TREND = "vieneu:trend:"  # giọng theo trend: giọng dựng sẵn + hiệu ứng (trami/trend_voices.py)
# kiểu đọc VieNeu: "clear" bớt ngẫu nhiên khi lấy mẫu -> phát âm rõ, ổn định hơn; "natural" = mặc định của VieNeu
VIENEU_STYLES = {"clear": {"temperature": 0.6, "top_k": 20, "top_p": 0.9}, "natural": {}}
SR = 24000
_vn_model = None
_vn_lock = threading.Lock()


# ------------------------------------------------------------------ danh sách giọng

def vieneu_available() -> bool:
    from .config import installed

    return installed("vieneu")


def _vieneu_dir() -> Path | None:
    try:
        import importlib.util

        spec = importlib.util.find_spec("vieneu")
        return Path(list(spec.submodule_search_locations)[0])
    except Exception:  # noqa: BLE001
        return None


_STYLE_HINT = {"tin_tuc": "đọc tin tức, rất rõ", "doc_truyen": "kể chuyện", "tu_nhien": "tự nhiên"}


def vieneu_presets(mode: str = "v3turbo") -> list[dict]:
    """Đọc danh sách giọng dựng sẵn từ file của VieNeu (không cần tải mô hình). Giọng được VieNeu chọn là tiêu biểu
    (featured) đứng đầu, có ⭐."""
    try:
        base = _vieneu_dir()
        f = base / "assets" / ("voices_v3_nano.json" if mode == "v3nano" else "voices_v3_turbo.json")
        data = json.loads(f.read_text("utf-8"))
        items = list(data.get("presets", {}).items())

        def rank(it):
            try:
                return int(it[1].get("featured"))
            except (TypeError, ValueError):
                return 999
        out = []
        for name, v in sorted(items, key=rank):
            g = {"male": "nam", "female": "nữ"}.get(v.get("gender", ""), "")
            style = _STYLE_HINT.get(v.get("style", "") if isinstance(v.get("style"), str) else "", "")
            if g and v.get("region") and style:
                desc = f"{g} miền {v['region']}, {style}"     # "nữ miền Bắc, tự nhiên"
            else:
                desc = v.get("description", "")
            star = "⭐ " if rank((name, v)) < 999 else ""
            out.append({"id": f"vieneu:{name}", "name": f"{star}{name} – {desc} (VieNeu)", "gender": v.get("gender", "")})
        return out
    except Exception:
        return []


def vieneu_sample_voices() -> list[dict]:
    """6 giọng thu âm sạch đi kèm gói VieNeu (assets/samples/*.wav) – dùng làm mẫu nhái cho v3 Turbo."""
    base = _vieneu_dir()
    d = base / "assets" / "samples" if base else None
    if not d or not d.is_dir():
        return []
    out = []
    for f in sorted(d.glob("*.wav")):
        label_ = f.stem   # "Bình (nam miền Bắc)"
        g = "female" if "(nữ" in label_ else "male" if "(nam" in label_ else ""
        out.append({"id": VN_SAMPLE + label_, "name": f"🎙 {label_} – thu âm sạch (VieNeu)", "gender": g,
                    "file": str(f)})
    return out


def vieneu_ref(spec: str) -> str | None:
    """File mẫu giọng cho "vieneu:mau:…" / "vieneu:lib:…" (None nếu không còn)."""
    if spec.startswith(VN_SAMPLE):
        f = next((v["file"] for v in vieneu_sample_voices() if v["id"] == spec), None)
        return f if f and Path(f).exists() else None
    if spec.startswith(VN_LIB):
        from . import voices as vlib

        sm = vlib.get(spec[len(VN_LIB):])
        return str(vlib.path_of(sm)) if sm and vlib.path_of(sm).exists() else None
    return None


def vieneu_params(settings: dict | None) -> dict:
    return dict(VIENEU_STYLES.get((settings or {}).get("vieneu_style", "clear"), {}))


def edge_voices(lang: str = "vi") -> list[dict]:
    """Giọng Microsoft Edge của 1 tiếng (tiếng Việt giữ đúng tên cũ)."""
    lang = langs.norm(lang)
    if lang == "vi":
        return list(EDGE_VOICES)
    return [{"id": f"edge:{vid}", "name": f"{nm} – {'Nữ' if g == 'female' else 'Nam'} (Edge, {langs.INFO[lang]['short']})",
             "gender": g} for vid, nm, g in langs.INFO[lang]["edge"]]


def edge_for(spec: str, lang: str) -> str:
    """Đổi giọng Edge sang giọng CÙNG nam / nữ của tiếng `lang` (Hoài My -> Aria, Nam Minh -> Guy…). Giọng đã đúng
    tiếng / giọng Edge lạ không biết tiếng giữ nguyên."""
    src = langs.edge_lang(spec)
    if not src or src == lang:
        return spec
    g = langs.edge_gender(spec)
    same = [v for v, _, gg in langs.INFO[src]["edge"] if gg == g or not g]
    idx = same.index(spec[5:]) if spec[5:] in same else 0
    return langs.edge_voice(lang, g, idx)


def _spk_index(speaker: str | None) -> int:
    m = re.search(r"\d+", speaker or "")
    return max(0, int(m.group()) - 1) if m else 0


def list_voices(settings: dict | None = None, lang: str = "vi") -> list[dict]:
    """Giọng cho tiếng `lang`: VieNeu chỉ đọc tiếng Việt (tiếng khác: Edge của tiếng đó + OmniVoice đa ngôn ngữ)."""
    mode = (settings or {}).get("vieneu_mode", "v3turbo")
    lang = langs.norm(lang)
    voices = edge_voices(lang)
    if vieneu_available() and lang == "vi":
        voices.insert(0, {"id": CLONE, "name": "🎭 Nhái giọng nhân vật gốc (VieNeu)", "gender": ""})
        voices += vieneu_presets(mode)
        from . import trend_voices

        voices += [{k: v[k] for k in ("id", "name", "gender", "tip")} for v in trend_voices.voices()]
        if mode != "v3nano":
            voices += [{k: v[k] for k in ("id", "name", "gender")} for v in vieneu_sample_voices()]
        from . import voices as vlib

        voices += [{"id": VN_LIB + v["id"], "name": f"🎙 {v['name']} – giọng mẫu của bạn (VieNeu)", "gender": ""}
                   for v in vlib.list_samples()]
    if omnivoice_available():
        from . import voices as vlib

        from . import omni_design

        omni = [{"id": f"omni:{v['id']}", "name": f"🎙 {v['name']} – giọng mẫu (OmniVoice)", "gender": ""}
                for v in vlib.list_samples()]
        omni.append({"id": OMNI_CLONE, "name": "🎭 Nhái giọng nhân vật gốc (OmniVoice)", "gender": ""})
        omni += omni_design.voices()
        voices = omni + voices
    return voices


def auto_assign(speakers: list[str], settings: dict | None = None, current: dict | None = None,
                genders: dict | None = None, lang: str = "vi") -> dict:
    """Gán sẵn giọng khác nhau cho từng người nói. genders {người nói: {"g": "male"/"female"}} (đoán từ chất giọng,
    trami/gender.py): nhân vật giọng nam nhận giọng đọc nam, giọng nữ nhận giọng nữ; chưa rõ thì xen kẽ nam / nữ.
    Giọng tiêu biểu (⭐) được dùng trước."""
    current = dict(current or {})
    genders = genders or {}
    engine = (settings or {}).get("tts_engine", "edge")
    lang = langs.norm(lang)
    if engine == "vieneu" and vieneu_available() and lang == "vi":
        pool = [(v["id"], v.get("gender", "")) for v in vieneu_presets((settings or {}).get("vieneu_mode", "v3turbo"))]
    elif engine == "omnivoice" and omnivoice_available():
        from . import omni_design
        from . import voices as vlib

        # giọng mẫu của bạn trước, rồi giọng chọn sẵn (có nam / nữ -> nhân vật nào nhận giọng đó)
        pool = [(f"omni:{v['id']}", "") for v in vlib.list_samples()] + \
            [(v["id"], v["gender"]) for v in omni_design.voices()]
    else:   # Edge (hoặc VieNeu khi lồng tiếng khác tiếng Việt)
        pool = [(v["id"], v["gender"]) for v in edge_voices(lang)]
    gmap = dict(pool)
    used = set(current.values())
    count = {"male": 0, "female": 0}
    for v in used:
        if gmap.get(v) in count:
            count[gmap[v]] += 1
    for spk in speakers:
        if current.get(spk):
            continue
        want = (genders.get(spk) or {}).get("g") or ""
        if not want and any(g for _, g in pool):          # chưa rõ: lấy loại đang ít hơn cho đa dạng
            want = "male" if count["male"] <= count["female"] else "female"
        free = [v for v, g in pool if v not in used]
        pick = next((v for v, g in pool if v not in used and g == want), None) \
            or next((v for v, g in pool if g == want), None) or (free[0] if free else pool[len(used) % len(pool)][0])
        current[spk] = pick
        used.add(pick)
        if gmap.get(pick) in count:
            count[gmap[pick]] += 1
    return current


# ------------------------------------------------------------------ âm thanh

def _decode(path: str | Path, speed: float = 1.0, sr: int = SR, sync: bool = False) -> np.ndarray:
    """sync: video gốc -> giải mã theo giờ của hình (cắt mẫu giọng theo mốc câu – 2.0.92)."""
    from .ffmpeg import audio_sync

    af = [audio_sync(path, sr)] if sync and audio_sync(path, sr) else []
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
    """Bỏ khoảng lặng 2 đầu đoạn giọng. Ngưỡng theo độ to của chính đoạn đó (giọng nhỏ không bị cắt mất phụ âm cuối /
    hơi thở cuối chữ như "tiếng", "rồi"), giữ thêm 0,15 s đuôi."""
    if not len(a):
        return a
    pk = float(np.abs(a).max())
    thr = min(thr, max(0.002, 0.03 * pk))
    idx = np.where(np.abs(a) > thr)[0]
    if len(idx) == 0:
        return a[:0]
    s, e = max(0, idx[0] - int(0.03 * SR)), min(len(a), idx[-1] + int(0.15 * SR))
    return a[s:e]


def read_wav(path: str | Path) -> tuple[np.ndarray, int]:
    """Đọc file wav PCM 16-bit (file do bộ đọc ghi ra) -> (mảng float mono, tần số mẫu)."""
    with wave.open(str(path), "rb") as w:
        sr, ch, n = w.getframerate(), w.getnchannels(), w.getnframes()
        a = np.frombuffer(w.readframes(n), dtype=np.int16).astype(np.float32) / 32768.0
    return (a.reshape(-1, ch).mean(axis=1) if ch > 1 else a), sr


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
    audio = _decode(ref_audio, sr=SR, sync=True)
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
    audio = _decode(ref_audio, sr=SR, sync=True)
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

EDGE_UPGRADE = '.venv\\Scripts\\python -m pip install -U "edge-tts>=7.3.1"'
EDGE_403 = ("Microsoft Edge từ chối đọc (lỗi 403): bản edge-tts trên máy đã cũ (Microsoft đổi mã xác thực Sec-MS-GEC v2, "
            "10/2026). Tắt tool rồi chạy lệnh: " + EDGE_UPGRADE)


DUB_BATCH = 1000      # lồng tiếng video dài: đọc mỗi đợt 1000 câu
EDGE_CONNECT = 10     # giây chờ nối tới máy chủ Edge
EDGE_RECEIVE = 20     # giây chờ giữa 2 gói dữ liệu giọng (mạng lag / rớt -> báo lỗi thay vì chờ mãi)


def _edge_comm(text: str, voice: str, rate: str = "+0%", pitch: str = "+0Hz"):
    import edge_tts

    try:
        return edge_tts.Communicate(text, voice, rate=rate, pitch=pitch, connect_timeout=EDGE_CONNECT,
                                    receive_timeout=EDGE_RECEIVE)
    except TypeError:   # edge-tts rất cũ chưa có tham số chờ
        return edge_tts.Communicate(text, voice, rate=rate, pitch=pitch)


async def _edge_save(text: str, voice: str, out: Path, rate: str = "+0%", pitch: str = "+0Hz") -> None:
    """Đọc 1 câu ra file. Ghi vào file tạm rồi mới đổi tên: rớt mạng giữa chừng không để lại file giọng cụt
    (file cụt từng bị coi là câu đã đọc xong -> câu mất đuôi mãi). Cả câu quá 60 s + 0,2 s/chữ -> coi như treo."""
    tmp = out.with_name(out.stem + ".part" + out.suffix)
    tmp.unlink(missing_ok=True)
    try:
        await asyncio.wait_for(_edge_comm(text, voice, rate, pitch).save(str(tmp)), timeout=60 + 0.2 * len(text))
        if not tmp.exists() or tmp.stat().st_size == 0:
            raise RuntimeError("Edge không trả về tiếng nào")
        tmp.replace(out)
    finally:
        tmp.unlink(missing_ok=True)


async def _edge_all(items: list[tuple[int, str, Path, str]], rate: str, pitch: str, progress, cancel,
                    total: int, done0: int, concurrency: int = 6):
    """Đọc các câu bằng Edge (6 câu cùng lúc). Mất mạng giữa chừng: mọi câu tạm dừng, chờ có mạng lại rồi đọc tiếp
    (câu đang đọc dở đọc lại từ đầu, không tính là lỗi); mất quá 10 phút -> NetworkDown (câu đã đọc được giữ lại).
    Bấm Huỷ: dừng ngay cả câu đang đọc."""
    from . import net

    sem = asyncio.Semaphore(concurrency)
    done = done0
    errors: list[str] = []
    blocked = False   # Edge trả 403 -> các câu sau cũng sẽ bị từ chối, không thử nữa
    net_ok = asyncio.Event()
    net_ok.set()
    state: dict = {"down": None, "waiter": None}
    loop = asyncio.get_running_loop()
    _, report = net.current()
    if progress and not report:
        report = lambda m: progress(done / max(1, total) * 0.8, m)   # noqa: E731

    async def wait_net() -> bool:
        """Chỉ 1 câu đi kiểm tra mạng, các câu khác chờ theo. True = đã mất mạng và có lại (đọc lại không tính lỗi)."""
        if state["waiter"] is None:
            net_ok.clear()

            def check():
                try:
                    return net.wait_online("tạo giọng đọc Edge", cancel=cancel, report=report)
                except (net.NetworkDown, Cancelled) as e:
                    state["down"] = e
                    return False

            state["waiter"] = loop.run_in_executor(None, check)
            try:
                back = await state["waiter"]
            finally:
                state["waiter"] = None
                net_ok.set()
            state["back"] = back
            return back
        await net_ok.wait()
        return bool(state.get("back"))

    async def one(i: int, text: str, out: Path, voice: str):
        nonlocal done, blocked
        async with sem:
            fails = 0
            while True:
                await net_ok.wait()
                if state["down"] or (cancel and cancel()):
                    return
                if blocked:
                    errors.append(f"Câu {i + 1}: bỏ qua (Edge từ chối 403)")
                    break
                try:
                    await _edge_save(text, voice, out, rate, pitch)
                    break
                except asyncio.CancelledError:
                    raise
                except Exception as e:  # mạng chập chờn -> thử lại
                    if "403" in str(e) and fails >= 1:
                        blocked = True
                        continue
                    if net.is_net_error(e) and await wait_net():
                        continue          # mất mạng rồi có lại: đọc lại câu này, không tính là lỗi
                    if state["down"]:
                        return
                    fails += 1
                    if fails >= 4:
                        errors.append(f"Câu {i + 1}: {type(e).__name__}: {e}" if not str(e) else f"Câu {i + 1}: {e}")
                        break
                    await asyncio.sleep(1.5 * fails)
            done += 1
            if progress:
                progress(done / max(1, total) * 0.8, f"Đang tạo giọng đọc {done}/{total}")

    tasks = [asyncio.ensure_future(one(*it)) for it in items]

    # bấm Huỷ -> dừng cả các câu đang đọc dở (không phải đợi mạng trả lời)
    pending = set(tasks)
    while pending:
        _, pending = await asyncio.wait(pending, timeout=0.2)
        if pending and cancel and cancel():
            for t in pending:
                t.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
            break
    for t in tasks:   # lấy lỗi (nếu có) để asyncio không cảnh báo "exception was never retrieved"
        if t.done() and not t.cancelled():
            t.exception()
    if cancel and cancel():
        raise Cancelled()
    if isinstance(state["down"], Cancelled):
        raise Cancelled()
    if state["down"]:
        left = sum(1 for it in items if not (it[2].exists() and it[2].stat().st_size > 0))
        raise net.NetworkDown(f"Mất mạng quá lâu khi tạo giọng đọc Edge: còn {left}/{len(items)} câu chưa đọc. "
                              "Các câu đã đọc được giữ lại – có mạng thì bấm tạo lại, tool chỉ đọc tiếp các câu còn thiếu.")
    if blocked:
        errors.insert(0, EDGE_403)
    return errors


class WorkerStuck(RuntimeError):
    """Tiến trình VieNeu / OmniVoice / Qwen3 / Hy-MT2 không trả lời quá lâu."""


class VieneuWorker:
    """Điều khiển tiến trình riêng chạy VieNeu (trami/vieneu_worker.py). Mỗi lúc chỉ 1 câu được đọc."""

    title = "VieNeu"

    def __init__(self, mode: str):
        self.mode = mode
        self.sr = 48000
        self.proc: subprocess.Popen | None = None
        self.lock = threading.Lock()
        self.fused = "1"      # CUDA graph của VieNeu (nhanh); tắt nếu GPU báo lỗi chụp graph
        self._q: queue.Queue = queue.Queue()   # dòng stdout của tiến trình (luồng _pump đọc)
        self.last_used = time.time()           # gpu.reap_idle: để không lâu thì tắt, trả bộ nhớ card
        self._times: list[float] = []          # giây / chữ của các câu gần đây -> biết câu nào chậm bất thường
        self.note = ""                         # vừa phải mở lại bộ đọc vì chậm bất thường (báo lên dòng tiến độ)
        self.vram_free = None                  # phần card đồ hoạ còn trống (tiến trình báo sau mỗi câu)

    flag = "--vieneu-worker"
    log_name = "vieneu.txt"
    stall_start = 900.0   # nạp / tải mô hình mà 15 phút không có tin gì -> coi là treo
    stall_call = 300.0    # 1 câu đọc quá 5 phút -> treo (GPU kẹt…) -> tắt, mở lại tiến trình
    stall_min = 60.0      # đã biết tốc độ thường (vài câu đầu): chờ 25 × thời gian 1 câu, ít nhất 60 s
    SLOW, SLOW_MIN = 8.0, 20.0   # câu đọc lâu gấp 8 lần bình thường (và > 20 s): card đầy -> Windows lấy RAM thay ->
    # mọi câu sau cũng chậm như treo (GPU vẫn 98 %) -> mở lại tiến trình (trả sạch bộ nhớ card) trước câu sau

    def _extra_env(self) -> dict:
        return {}

    def _cmd(self) -> list[str]:
        from .config import APP_DIR, FROZEN

        if FROZEN:
            return [sys.executable, self.flag, self.mode]
        return [sys.executable, str(APP_DIR / "TraMi.py"), self.flag, self.mode]

    def _start(self, progress=None, cancel=None):
        from .config import APP_DIR, DATA_DIR, no_console

        # HF_DEACTIVATE_ASYNC_LOAD: nạp trọng số tuần tự – transformers 5.x nạp song song từng làm sập ngầm
        # trên Windows + CUDA (tắt không báo lỗi)
        env = dict(os.environ, VIENEU_FUSED_FRAME=self.fused, PYTHONUTF8="1", PYTHONIOENCODING="utf-8",
                   HF_DEACTIVATE_ASYNC_LOAD="1", **self._extra_env())
        err = None  # log của VieNeu hiện trong cửa sổ đen; chạy không cửa sổ (pythonw/exe) thì ghi ra file
        if no_console():
            (DATA_DIR / "logs").mkdir(parents=True, exist_ok=True)
            err = open(DATA_DIR / "logs" / self.log_name, "a", encoding="utf-8")
        self.proc = subprocess.Popen(self._cmd(), stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=err,
                                     text=True, encoding="utf-8", errors="replace", bufsize=1, cwd=str(APP_DIR),
                                     env=env, creationflags=_NO_WINDOW)
        # đọc stdout ở luồng riêng -> luồng tác vụ chờ có giới hạn, bấm Huỷ được, tiến trình treo thì biết
        self._q = queue.Queue()
        threading.Thread(target=self._pump, args=(self.proc, self._q), daemon=True, name=f"trami-{self.title}").start()
        r = self._read(progress, cancel, self.stall_start)
        if "error" in r:
            self.stop()
            raise RuntimeError(r["error"])
        self.sr = int(r.get("sr", 48000))

    @staticmethod
    def _pump(proc: subprocess.Popen, q: queue.Queue) -> None:
        try:
            for line in proc.stdout:   # type: ignore[union-attr]
                q.put(line)
        except (OSError, ValueError):
            pass
        q.put(None)

    def _read(self, progress=None, cancel=None, stall: float | None = None, on_wait=None) -> dict:
        """Chờ 1 câu trả lời của tiến trình. Bấm Huỷ: chờ thêm 3 s cho xong câu đang làm (tiến trình còn dùng tiếp),
        quá thì tắt hẳn. Không nhận được gì quá `stall` giây -> tiến trình bị treo -> tắt + WorkerStuck.
        on_wait(giây đã chờ): gọi mỗi 5 s khi câu đọc lâu hơn 10 s (dòng tiến độ không đứng im như treo)."""
        from . import net

        assert self.proc
        cancel = cancel or net.current()[0]
        q = self._q
        last = t0 = beat = time.time()
        grace = None
        while True:
            try:
                line = q.get(timeout=0.2)
            except queue.Empty:
                now = time.time()
                if on_wait and now - t0 > 10 and now - beat >= 5:
                    beat = now
                    on_wait(now - t0)
                if grace is None and cancel and cancel():
                    grace = now + 3.0
                if grace is not None and now > grace:
                    self.kill()
                    raise Cancelled() from None
                if stall and now - last > stall:
                    self.kill()
                    took = f"{stall:.0f} giây" if stall < 120 else f"{round(stall / 60)} phút"
                    raise WorkerStuck(f"Tiến trình {self.title} không phản hồi quá {took} "
                                      "(bị treo) – đã tắt để mở lại") from None
                continue
            if line is None:
                break
            last = time.time()
            if not line.startswith("@@TRAMI@@"):
                continue
            obj = json.loads(line[len("@@TRAMI@@"):])
            if "progress" in obj:   # tiến độ tải / nạp mô hình từ tiến trình VieNeu
                if progress:
                    progress(float(obj["progress"]), obj.get("msg", ""))
                continue
            if grace is not None:   # câu trả lời tới sau khi bấm Huỷ: tiến trình vẫn dùng tiếp được
                raise Cancelled()
            return obj
        if grace is not None:
            self.proc = None
            raise Cancelled()
        code = self.proc.poll() if self.proc else None
        self.proc = None
        from .config import no_console

        where = f"file data\\logs\\{self.log_name}" if no_console() else "cửa sổ đen"
        raise RuntimeError(f"Tiến trình {self.title} bị tắt đột ngột (mã {code}). Xem log trong {where} để biết chi tiết.")

    def ensure_started(self, progress=None, cancel=None):
        """Mở tiến trình VieNeu và nạp mô hình (tự tải nếu chưa có), báo tiến độ tải."""
        with self.lock:
            if not self.proc or self.proc.poll() is not None:
                self._start(progress, cancel)
            self.last_used = time.time()

    def synth(self, text: str, out: str | Path, voice: str | None = None, ref_audio: str | None = None,
              ref_text: str | None = None, duration: float | None = None, params: dict | None = None,
              seed: int | None = None, instruct: str | None = None, language: str | None = None,
              on_wait=None) -> Path:
        req = {"text": text, "out": str(out), "voice": voice, "ref_audio": ref_audio, "ref_text": ref_text or "",
               "duration": duration, "params": params or {}, "seed": seed, "instruct": instruct}
        if language and language != "vi":   # OmniVoice đọc tiếng khác (VieNeu chỉ đọc tiếng Việt)
            req["language"] = language
        req = json.dumps(req, ensure_ascii=False)
        with self.lock:
            for attempt in range(2):
                try:
                    if not self.proc or self.proc.poll() is not None:
                        self._start()
                    assert self.proc and self.proc.stdin
                    t0 = time.time()
                    self.proc.stdin.write(req + "\n")
                    self.proc.stdin.flush()
                    r = self._read(stall=self._stall_for(text), on_wait=on_wait)
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
                self.last_used = time.time()
                self._timed(time.time() - t0, r, len(text))
                return Path(out)
        raise RuntimeError(f"{self.title} không đọc được câu này")

    def _typical(self, chars: int) -> float:
        """Thời gian đọc bình thường của 1 câu `chars` chữ (theo trung vị giây / chữ của 20 câu gần nhất);
        chưa đủ 5 câu -> 0 (chưa biết)."""
        t = sorted(self._times[-20:])
        return t[len(t) // 2] * max(chars, 10) if len(t) >= 5 else 0.0

    def _stall_for(self, text: str) -> float:
        typ = self._typical(len(text))
        return min(self.stall_call, max(self.stall_min, 25 * typ)) if typ else self.stall_call

    def _timed(self, dt: float, reply: dict, chars: int) -> None:
        """Ghi tốc độ câu vừa đọc. Chậm bất thường -> tắt tiến trình, câu sau mở lại với card sạch."""
        self.vram_free = reply.get("vram_free", self.vram_free)
        typ = self._typical(chars)
        if typ and dt > max(self.SLOW_MIN, self.SLOW * typ):
            free = f", card còn trống {self.vram_free:.0%}" if isinstance(self.vram_free, (int, float)) else ""
            self.note = (f"{self.title} đọc 1 câu mất {dt:.0f} s (thường {typ:.1f} s{free}) – card đồ hoạ có thể đã "
                         "đầy, đã mở lại bộ đọc")
            self.stop()
            return    # không tính câu chậm vào tốc độ thường
        self._times = (self._times + [dt / max(chars, 10)])[-40:]

    def stop(self):
        p, self.proc = self.proc, None
        if p and p.poll() is None:
            try:
                p.stdin.close()  # type: ignore[union-attr]
                p.wait(5)
            except Exception:
                p.kill()

    def kill(self):
        """Tắt ngay (tiến trình bị treo / người dùng bấm Huỷ giữa câu dài)."""
        p, self.proc = self.proc, None
        if p and p.poll() is None:
            try:
                p.kill()
                p.wait(5)
            except Exception:  # noqa: BLE001
                pass


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
    stall_call = 600.0    # chạy CPU chậm hơn VieNeu
    stall_min = 120.0
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


OMNI_SHORT_WORDS = 10   # câu OmniVoice ≤ 10 tiếng: trước 2.1.5 bị kéo dài gấp 2–2,5 lần (đọc lặp lời)
# VieNeu v3 Turbo sinh giọng từng khung 0,08 s (3840 mẫu / 48 kHz) và TỰ quyết lúc dừng. Câu ≤ 4 tiếng hay trượt chỗ dừng
# rồi đọc lặp lời tới trần số khung của thư viện (13 khung + 5 mỗi tiếng thêm): "Được thôi" -> "được thôi được thôi"
# 1,44 s. 2.1.7: tiến trình VieNeu hạ trần câu ngắn xuống sát độ dài đọc thật (`VN_SHORT_CAP` – thư viện đo 1 tiếng 6–9
# khung, 2 tiếng 6–10, 3–4 tiếng 12–15) -> không đủ chỗ để đọc lặp 2–3 lần; phần lặp còn sót thì cắt (`voicefix.repeat_cut`).
VN_FRAME = 0.08
VN_CAP_SYL = 4
VN_SHORT_CAP = (0, 11, 14, 19, 22)   # trần số khung theo số tiếng (1–4)
# chạm trần mà không thấy lặp để cắt -> đọc lại: thông số lấy mẫu mặc định của VieNeu, rồi "nóng" hơn
VN_RETRY_PARAMS = ({}, {"temperature": 0.9, "top_k": 40, "top_p": 0.97})


# 2.1.12: câu dài hơn 4 tiếng cũng có thể đọc lặp 1 cụm / kéo dài – nhận ra theo TỐC ĐỘ ĐỌC THƯỜNG của chính giọng đó
# trong lần lồng tiếng này (giây / tiếng, trung vị các câu trước): dài hơn LOOP_RATIO lần + LOOP_SLACK giây -> đọc lại
# 1 lần (thông số lấy mẫu mặc định, hạt khác), giữ bản ngắn hơn.
LOOP_RATIO = 1.5
LOOP_SLACK = 0.5
LOOP_DEFAULT_SPS = 0.24      # chưa đủ mẫu: ~4,2 tiếng / giây (VieNeu đọc thường 4,5–5 tiếng / giây)
LOOP_MIN_SAMPLES = 8


def syllables(text: str) -> int:
    return len(re.findall(r"\w+", text or ""))


class RateWatch:
    """Tốc độ đọc thường (giây / tiếng) của từng giọng trong 1 lần lồng tiếng – nhận ra câu dài bất thường (đọc lặp)."""

    def __init__(self):
        self.d: dict[str, list[float]] = {}

    def expect(self, key: str, syl: int) -> float:
        xs = self.d.get(key) or []
        per = float(np.median(xs)) if len(xs) >= LOOP_MIN_SAMPLES else LOOP_DEFAULT_SPS
        return per * syl

    def too_long(self, key: str, dur: float, syl: int) -> bool:
        return syl > VN_CAP_SYL and dur > LOOP_RATIO * self.expect(key, syl) + LOOP_SLACK

    def add(self, key: str, dur: float, syl: int) -> None:
        if syl >= 3 and dur > 0:
            xs = self.d.setdefault(key, [])
            xs.append(dur / syl)
            del xs[:-200]


def vn_short(text: str) -> bool:
    n = len(re.findall(r"\w+", text))
    return 1 <= n <= VN_CAP_SYL


def vn_cap_hit(a: np.ndarray, sr: int, text: str) -> bool:
    """Giọng VieNeu v3 Turbo của câu ≤ 4 tiếng dài tới sát trần số khung = bộ đọc không dừng (đọc lặp / kéo dài)."""
    if not vn_short(text) or not sr:
        return False
    cap = VN_SHORT_CAP[len(re.findall(r"\w+", text))]
    return len(a) / sr >= (cap - 1.5) * VN_FRAME


def omni_short(text: str) -> bool:
    """Câu ngắn mà OmniVoice trước 2.1.5 kéo dài quá mức (ước lượng < ~2 s) – mã file giọng đổi để đọc lại."""
    return len(re.findall(r"\w+", text)) <= OMNI_SHORT_WORDS


def synth_checked(worker: VieneuWorker, text: str, out: Path, cap_check: bool = False, report: dict | None = None,
                  watch: RateWatch | None = None, wkey: str = "", **kw) -> Path:  # kw: voice, ref_audio, params, on_wait…
    """Đọc 1 câu rồi kiểm tra file giọng: im lặng / tiếng bíp / tiếng ù -> đọc lại 1 lần (hạt ngẫu nhiên khác).
    Vẫn hỏng thì báo lỗi (câu đó không được ghép vào bản lồng tiếng). cap_check (VieNeu v3 Turbo, câu ≤ 4 tiếng): đọc
    lặp lời -> cắt phần lặp (`voicefix.repeat_cut`, không đọc lại); chạm trần số khung mà không thấy chỗ cắt -> đọc lại
    tối đa 2 lần với thông số lấy mẫu khác (`VN_RETRY_PARAMS`). report["babble"] = 1 (đã cắt / đọc lại được) / 2 (vẫn
    lặp – giữ bản cuối). watch (2.1.12, câu > 4 tiếng): dài bất thường so với tốc độ đọc thường của giọng `wkey` -> đọc
    lại 1 lần, giữ bản ngắn hơn; report["long"] = 1 (bản đọc lại hết dài) / 2 (vẫn dài – giữ bản ngắn hơn)."""
    from .voicefix import repeat_cut

    why, babble = None, 0
    tries = 2 + (len(VN_RETRY_PARAMS) if cap_check else 0)
    for attempt in range(tries):
        kk = kw
        if babble and attempt - 1 < len(VN_RETRY_PARAMS):
            kk = dict(kw, params=dict(VN_RETRY_PARAMS[min(babble, len(VN_RETRY_PARAMS)) - 1]))
        worker.synth(text, out, seed=(None if attempt == 0 else int.from_bytes(os.urandom(3), "big")), **kk)
        try:
            a, sr = read_wav(out)
        except (OSError, EOFError, wave.Error):
            return Path(out)       # không đọc được kiểu file này -> bỏ qua bước kiểm tra
        why = voice_problem(a, sr)
        if not why and cap_check and vn_short(text):
            cut = repeat_cut(a, sr, text)
            if cut:                # đọc lặp lời: giữ lần đọc đầu (mờ 15 ms ở chỗ cắt, không nghe tiếng tách)
                a = a[:cut].copy()
                f = min(len(a), int(0.015 * sr))
                a[len(a) - f:] *= np.linspace(1.0, 0.0, f, dtype=a.dtype)
                write_wav(out, a, sr)
                if report is not None:
                    report["babble"] = 1
                return Path(out)
            if vn_cap_hit(a, sr, text):
                babble += 1
                if babble > len(VN_RETRY_PARAMS):   # đọc lại vẫn lặp: giữ bản này, báo câu để sửa lời / đổi giọng
                    if report is not None:
                        report["babble"] = 2
                    return Path(out)
                continue
        if not why:
            if babble and report is not None:
                report["babble"] = 1
            if watch is not None:
                _loop_guard(worker, text, Path(out), a, sr, watch, wkey, report, kw)
            return Path(out)
        if attempt >= 1 + babble:
            break
    Path(out).unlink(missing_ok=True)
    raise RuntimeError(f"giọng đọc hỏng ({why}) cả 2 lần đọc – sửa lời câu này rồi bấm Tạo lại giọng")


def _loop_guard(worker, text: str, out: Path, a: np.ndarray, sr: int, watch: RateWatch, wkey: str,
                report: dict | None, kw: dict) -> None:
    """Câu > 4 tiếng dài bất thường (đọc lặp 1 cụm / kéo dài): đọc lại 1 lần với thông số mặc định + hạt khác, giữ bản
    ngắn hơn (bản đọc lại phải ≥ 60 % độ dài thường – không lấy bản bị cụt)."""
    syl = syllables(text)
    dur = len(a) / sr if sr else 0.0
    if not watch.too_long(wkey, dur, syl):
        watch.add(wkey, dur, syl)
        return
    first = out.with_name(out.stem + ".first" + out.suffix)
    os.replace(out, first)
    try:
        worker.synth(text, out, seed=int.from_bytes(os.urandom(3), "big"),
                     **dict(kw, params=dict(VN_RETRY_PARAMS[0])))
        b, sr2 = read_wav(out)
        d2 = len(b) / sr2 if sr2 else 0.0
        ok2 = not voice_problem(b, sr2) and d2 >= 0.6 * watch.expect(wkey, syl)
    except Exception:  # noqa: BLE001 – đọc lại lỗi: giữ bản đầu
        ok2, d2 = False, dur
    if ok2 and d2 < dur:
        first.unlink(missing_ok=True)
        dur = d2
    else:
        os.replace(first, out)
    still = watch.too_long(wkey, dur, syl)
    if not still:
        watch.add(wkey, dur, syl)
    if report is not None:
        report["long"] = 2 if still else 1


def trend_synth(worker: VieneuWorker, spec: str, text: str, out: Path, params: dict,
                presets: list[dict] | None = None, on_wait=None, cap_check: bool = False,
                report: dict | None = None, watch: RateWatch | None = None, wkey: str = "") -> Path:
    """Giọng VieNeu theo trend: đọc bằng giọng gốc + kiểu đọc của nó (đã qua kiểm tra giọng hỏng) rồi áp hiệu ứng.
    Ghi 24 kHz (bản lồng tiếng trộn ở 24 kHz; hiệu ứng chạy nhanh gấp đôi so với 48 kHz của VieNeu)."""
    from . import trend_voices
    from .voicefx import resample_len

    if not trend_voices.get(spec):
        raise ValueError("Giọng trend này không còn trong bản Subvideo đang dùng")
    synth_checked(worker, text, out, cap_check=cap_check, report=report, watch=watch, wkey=wkey,
                  voice=trend_voices.base_voice(spec, presets) or None,
                  params=trend_voices.params(spec, params), on_wait=on_wait)
    try:
        a, sr = read_wav(out)
    except (OSError, EOFError, wave.Error):
        return Path(out)
    if sr != SR:
        a, sr = resample_len(a, int(round(len(a) * SR / sr))), SR
    return write_wav(out, trend_voices.process(spec, a, sr), sr)


def read_wav_rate(path: str | Path) -> int | None:
    try:
        with wave.open(str(path), "rb") as w:
            return w.getframerate()
    except (OSError, EOFError, wave.Error):
        return None


def _speed_tag(rate: str) -> float:
    try:
        return 1.0 + float(rate.strip().rstrip("%")) / 100.0
    except Exception:
        return 1.0


_TTS_OK = re.compile(r"[^0-9A-Za-zÀ-ỹ\s.,!?;:'\"()%\-–…]")
# tiếng khác: thêm chữ viết của tiếng đó (Thái; Nhật: kana + chữ Hán + dấu câu Nhật; Hàn: Hangul)
_TTS_OK_LANG = {
    "en": _TTS_OK,
    "th": re.compile(r"[^0-9A-Za-z\u0E00-\u0E7F\s.,!?;:'\"()%\-–…]"),
    "ja": re.compile(r"[^0-9A-Za-z\u3040-\u30FF\u31F0-\u31FF\u3400-\u9FFF\uF900-\uFAFF\uFF66-\uFF9F"
                     r"\u3001\u3002\u300C-\u300F\uFF01\uFF1F\u30FC\s.,!?;:'\"()%\-–…]"),
    "ko": re.compile(r"[^0-9A-Za-z\uAC00-\uD7AF\u1100-\u11FF\u3130-\u318F\s.,!?;:'\"()%\-–…]"),
}


def clean_tts_text(text: str, lang: str = "vi") -> str:
    """Bỏ ký tự bộ đọc không đọc được (chữ Hán sót lại, emoji, ký hiệu lạ) – giữ chữ viết của tiếng `lang`."""
    t = unicodedata.normalize("NFC", text or "")
    t = t.replace("“", '"').replace("”", '"').replace("‘", "'").replace("’", "'").replace("—", "-")
    t = _TTS_OK_LANG.get(lang, _TTS_OK).sub(" ", t)
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


# ------------------------------------------------------------------ xếp giọng lồng theo thời gian
# Khi câu đọc dài hơn chỗ trống (tới lúc câu sau bắt đầu) – chọn chung ở tab Lồng tiếng, hoặc riêng từng câu
# (extra["overflow"]).
OVERFLOW_MODES = [
    ("smart", "Tự động (khuyên dùng)"),
    ("push", "Nối tiếp – đọc hết câu rồi mới sang câu sau"),
    ("overlap", "Đọc đè – câu sau vẫn vào đúng giờ"),
    ("early", "Đọc sớm – câu dài bắt đầu sớm hơn"),
    ("cut", "Giữ đúng giờ – tăng tốc rồi cắt bớt (cách cũ)"),
]
OVERFLOW_HELP = {
    "smart": "Tăng tốc nhẹ (tới ×1,15, khó nhận ra) → đọc sớm vào khoảng lặng phía trước câu → tăng tốc thêm (tới mức "
             "tối đa) → vẫn thiếu thì câu sau lùi lại và đuổi kịp ở chỗ nghỉ kế tiếp. Không bao giờ cắt chữ. Nhiều câu "
             "dài liền nhau được tính chung cả cụm (cùng 1 tốc độ, không câu nhanh câu chậm). Câu sau lùi quá 'Trễ tối "
             "đa' thì vào đúng mức đó và đọc đè lên đuôi câu trước.",
    "push": "Tăng tốc (tới mức tối đa) → vẫn thiếu thì đọc hết câu rồi câu sau mới bắt đầu (câu sau lùi lại). Nhiều câu "
            "dài liền nhau thì độ trễ dồn lại, đuổi kịp ở chỗ nghỉ kế tiếp.",
    "overlap": "Tăng tốc (tới mức tối đa) → vẫn thiếu thì câu đang đọc cứ đọc hết, câu sau vẫn vào đúng lúc nhân vật "
               "nói và đọc đè lên. Hợp với cảnh cãi nhau, nói chen.",
    "early": "Câu dài bắt đầu SỚM hơn (vào khoảng lặng phía trước, tới 3 s) để câu sau vào đúng giờ → vẫn thiếu thì tăng "
             "tốc → vẫn thiếu thì câu sau lùi lại.",
    "cut": "Như bản cũ: mượn 0,15 s phía trước, tăng tốc, câu sau lùi tối đa 0,25 s, vẫn thừa thì nhỏ dần rồi cắt "
           "bớt cuối câu (có thể mất chữ).",
}
_MODE_IDS = {m for m, _ in OVERFLOW_MODES}
GAP, BORROW, DELAY, FADE = 0.04, 0.15, 0.25, 0.3
EARLY_MAX = 3.0        # "Đọc sớm": câu dài bắt đầu sớm tối đa 3 s
COMFY_SPEED = 1.15     # "Tự động": tăng tốc tới mức này (khó nhận ra) trước khi phải đọc sớm
SHIFT_OK = 0.3         # lệch ≤ 0,3 s so với lúc nhân vật nói vẫn coi là đúng chỗ
_TOL = 0.05


def plan_layout(items: list[dict], total: float, mode: str = "smart", max_speedup: float = 1.3,
                lead_max: float = 1.0, max_lag: float = 2.0) -> dict[int, dict]:
    """Xếp các đoạn giọng lồng theo thời gian (chỉ tính toán, không đụng âm thanh).

    items: {"k": số câu, "want": lúc nên bắt đầu (giây), "dur": thời lượng giọng ở ×1, "user": tốc độ tối thiểu đặt tay
    (0 = không), "fixed": người dùng đã kéo đoạn giọng (giữ đúng chỗ, không đọc sớm), "mode": cách xếp riêng ("" = chung),
    "pause": nghỉ tối thiểu trước đoạn này (vế sau của câu dài đã tách ở dấu phẩy – không có = GAP)}.
    Trả về {k: {"start", "end", "speed", "keep" (giây giữ lại khi phải cắt, None = đủ), "fit" ok/fast/long,
    "shift" (bắt đầu − lúc nên bắt đầu: âm = đọc sớm, dương = bị lùi), "over" (giây đè lên câu sau), "mode"}}.

    Nhiều câu dài liền nhau (smart / push / early): gom thành CỤM (các câu liên tiếp đều tràn sang câu sau) và chọn
    1 tốc độ chung nhỏ nhất để không câu nào làm câu sau bị lùi (thử bằng chia đôi), đọc sớm chỉ đúng phần cần
    (tính ngược từ câu cuối cụm: mỗi câu bắt đầu muộn nhất có thể mà vẫn chừa chỗ cho câu sau)."""
    its = sorted(items, key=lambda x: (float(x["want"]), int(x["k"])))
    n = len(its)
    if not n:
        return {}
    want = [max(0.0, float(x["want"])) for x in its]
    dur = [max(0.05, float(x["dur"])) for x in its]
    nxt = [want[i + 1] if i + 1 < n else max(float(total), want[i] + 0.3) for i in range(n)]
    modes = [(x.get("mode") if x.get("mode") in _MODE_IDS else mode if mode in _MODE_IDS else "smart") for x in its]
    fixed = [bool(x.get("fixed")) for x in its]
    # khoảng nghỉ tối thiểu SAU câu i (= nghỉ trước câu i + 1): vế sau của câu đã tách ở dấu phẩy nghỉ 1 nhịp
    ga = [max(GAP, float(its[i + 1].get("pause") or 0)) if i + 1 < n else GAP for i in range(n)]
    base = [min(2.0, max(0.5, float(x["user"]))) if x.get("user") else 1.0 for x in its]
    has_user = [bool(x.get("user")) for x in its]
    cap = [max(max_speedup, b) for b in base]
    lead = [0.0 if f else {"smart": max(0.0, lead_max), "early": EARLY_MAX}.get(m, 0.0) for f, m in zip(fixed, modes)]
    seq = [m in ("smart", "push", "early") for m in modes]
    start = [0.0] * n
    end = [0.0] * n
    speed = list(base)
    keep: list[float | None] = [None] * n

    def after(i: int, st: float, en: float) -> float:
        """Lúc câu sau được phép bắt đầu khi câu i đọc từ st tới en (không bao giờ trước câu i)."""
        m, g = modes[i], ga[i]
        if m == "overlap" and g <= GAP:
            return min(en + g, max(nxt[i], st + SHIFT_OK))
        if m == "smart" and i + 1 < n and g <= GAP:
            return min(en + g, max(nxt[i] + max(0.0, max_lag), st + SHIFT_OK))
        return en + g

    def overflows(i: int, busy: float) -> bool:
        return max(want[i], busy) + dur[i] / base[i] + ga[i] > nxt[i] + 1e-3

    def place_group(run: list[int], busy: float, f: float, lead_on: bool):
        """Vị trí các câu trong cụm với tốc độ chung f: (starts, ends, câu sau có bị lùi quá _TOL không)."""
        d = [dur[q] / max(base[q], f) for q in run]
        lim = [0.0] * len(run)
        nx = nxt[run[-1]]
        for t in range(len(run) - 1, -1, -1):        # muộn nhất có thể mà vẫn chừa chỗ cho câu sau
            q = run[t]
            lo = want[q] - (lead[q] if lead_on else 0.0)
            lim[t] = max(lo, min(want[q], nx - ga[q] - d[t]))
            nx = lim[t]
        sts, ens, late = [], [], False
        b = busy
        prev_st = start[run[0] - 1] if run[0] > 0 else -1e9
        for t, q in enumerate(run):
            st = max(b, lim[t])
            if modes[q] == "smart" and st - want[q] > max_lag:
                # trễ quá mức cho phép -> vào đúng mức đó, đè lên đuôi câu trước (nhưng không trước câu trước)
                st = min(st, max(want[q] + max_lag, prev_st + SHIFT_OK))
            prev_st = st
            en = st + d[t]
            late = late or en + ga[q] > nxt[q] + _TOL
            sts.append(st)
            ens.append(en)
            b = after(q, st, en)
        return sts, ens, late

    def min_speed(run, busy, lead_on, hi) -> float | None:
        """Tốc độ chung nhỏ nhất (1..hi) để không câu sau nào bị lùi; None = kể cả hi cũng không đủ."""
        if not place_group(run, busy, 1.0, lead_on)[2]:
            return 1.0
        if hi <= 1.0 or place_group(run, busy, hi, lead_on)[2]:
            return None
        lo_f, hi_f = 1.0, hi
        for _ in range(24):
            mid = (lo_f + hi_f) / 2
            if place_group(run, busy, mid, lead_on)[2]:
                lo_f = mid
            else:
                hi_f = mid
        return hi_f

    busy = 0.0
    i = 0
    while i < n:
        m = modes[i]
        if seq[i] and not fixed[i] and overflows(i, busy):
            run, b = [], busy
            j = i
            while j < n and seq[j] and not fixed[j]:
                st = max(want[j], b)
                en = st + dur[j] / base[j]
                if run and en + ga[j] <= nxt[j] + 1e-3:
                    break
                run.append(j)
                b = en + ga[j]
                j += 1
            policy = modes[i]
            top = max(1.0, max_speedup)
            if policy == "push":
                lead_on, f = False, min_speed(run, busy, False, top) or top
            elif policy == "early":
                lead_on, f = True, min_speed(run, busy, True, top) or top
            else:   # smart: tăng tốc nhẹ -> đọc sớm -> tăng tốc thêm -> lùi câu sau
                comfy = min(top, COMFY_SPEED)
                f = min_speed(run, busy, False, comfy)
                lead_on = f is None
                if f is None:
                    f = comfy if not place_group(run, busy, comfy, True)[2] else (min_speed(run, busy, True, top) or top)
            sts, ens, _ = place_group(run, busy, f, lead_on)
            for t, q in enumerate(run):
                start[q], end[q], speed[q] = sts[t], ens[t], max(base[q], f)
            busy = after(run[-1], sts[-1], ens[-1])
            i = run[-1] + 1
            continue
        st = max(want[i], busy)
        need = dur[i] / base[i]
        slot = max(0.3, nxt[i] - ga[i] - st)
        if m == "cut" and need > slot and not fixed[i]:
            # mượn khoảng lặng phía trước (tối đa 0,15 s)
            borrow = min(BORROW, need - slot, max(0.0, want[i] - busy))
            st -= borrow
            slot += borrow
        sp = base[i]
        if need > slot * 1.03:
            # tua nhanh (giữ cao độ). KHÔNG bắt OmniVoice đọc lại với thời lượng ép (duration=): hay ra tiếng "bíp" (2.0.34)
            sp = max(sp, min(cap[i], dur[i] / slot))
        en = st + dur[i] / sp
        if m == "cut":
            hard = nxt[i] + DELAY - ga[i]          # câu sau được lùi tối đa 0,25 s
            if en > hard and i + 1 < n:
                keep[i] = max(0.3, hard - st)
                en = st + keep[i]
        start[i], end[i], speed[i] = st, en, sp
        busy = after(i, st, en)
        i += 1

    by_start = sorted(range(n), key=lambda q: (start[q], q))
    over = [0.0] * n
    for a, b in zip(by_start, by_start[1:]):
        over[a] = max(0.0, end[a] - start[b])
    out: dict[int, dict] = {}
    for i, x in enumerate(its):
        full_end = start[i] + dur[i] / speed[i]
        long_ = keep[i] is not None or full_end + ga[i] > nxt[i] + SHIFT_OK
        fit = "long" if long_ else ("ok" if speed[i] <= 1.06 or (has_user[i] and speed[i] <= base[i] + 0.005)
                                    else "fast")
        out[int(x["k"])] = {"start": start[i], "end": end[i], "speed": speed[i], "keep": keep[i], "fit": fit,
                            "shift": start[i] - want[i], "over": over[i], "mode": modes[i],
                            "have": max(0.3, nxt[i] - ga[i] - start[i]),
                            "slot": max(0.3, nxt[i] - ga[i] - want[i])}   # chỗ trống của chính câu (không tính bị đẩy)
    return out


def soft_limit(a: np.ndarray, thr: float = 0.9) -> np.ndarray:
    """Chỗ 2 giọng đọc đè cộng lại vượt ngưỡng -> nén mềm phần đỉnh (không vỡ tiếng, không hạ âm lượng cả bản)."""
    over = np.abs(a) > thr
    if over.any():
        x = a[over]
        a[over] = np.sign(x) * (thr + (1 - thr) * np.tanh((np.abs(x) - thr) / (1 - thr)))
    return a


# ------------------------------------------------------------------ ghép bản lồng nhanh (xếp lại / vá từng đoạn)

PATCH_MAX = 0.5   # thay đổi > 50 % thời lượng bản lồng -> dựng lại cả bản (nhanh ngang vá)


class _NoPatch(Exception):
    """Không vá được bản lồng đang có (khác định dạng / đang bị khoá / đổi gần hết) -> dựng lại cả bản."""


def _clip_speed(v: float) -> float:
    """Tốc độ dùng để giải mã đoạn giọng: làm tròn 0,001 (đệm theo tốc độ dùng lại được); gần ×1 thì đúng ×1."""
    v = round(float(v), 3)
    return 1.0 if abs(v - 1.0) <= 0.01 else v


class _ClipMeta:
    """Đệm thông số từng file giọng (work/tts/_clipmeta.json): độ dài sau khi bỏ khoảng lặng, độ to, tốc độ đã lưu
    bản tua nhanh -> xếp lại 2.000 câu không phải giải mã lại 2.000 file chỉ để biết độ dài."""

    def __init__(self, work: str | Path):
        self.path = Path(work) / "_clipmeta.json"
        try:
            self.d = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self.d = {}
        self.dirty = False

    def _get(self, path: str | Path) -> list:
        p = Path(path)
        st = p.stat()
        e = self.d.get(p.name)
        if not e or e[0] != int(st.st_mtime) or e[1] != st.st_size:
            e = [int(st.st_mtime), st.st_size, len(_trim_silence(_decode_cached(p))), None, None]
            self.d[p.name] = e
            self.dirty = True
        return e

    def warm(self, paths) -> None:
        """Tính trước (song song) các file chưa có trong đệm – lần đầu, phim dài hàng nghìn câu."""
        from concurrent.futures import ThreadPoolExecutor

        todo = []
        for path in set(paths):
            try:
                st = Path(path).stat()
            except OSError:
                continue
            e = self.d.get(Path(path).name)
            if not e or e[0] != int(st.st_mtime) or e[1] != st.st_size:
                todo.append(path)
        if len(todo) < 8:
            return
        with ThreadPoolExecutor(max_workers=max(2, min(8, (os.cpu_count() or 4)))) as ex:
            for path, n in zip(todo, ex.map(lambda q: len(_trim_silence(_decode_cached(q))), todo)):
                st = Path(path).stat()
                self.d[Path(path).name] = [int(st.st_mtime), st.st_size, n, None, None]
                self.dirty = True

    def n(self, path) -> int:
        """Số mẫu (24 kHz) của đoạn giọng ở ×1 sau khi bỏ khoảng lặng 2 đầu."""
        return int(self._get(path)[2])

    def level(self, path) -> float:
        e = self._get(path)
        if e[3] is None:
            e[3] = float(speech_level(_decode_cached(path), SR))
            self.dirty = True
        return float(e[3])

    def gaps(self, path) -> list:
        """Chỗ lặng BÊN TRONG đoạn giọng (×1, đã bỏ lặng 2 đầu): [[mẫu bắt đầu, mẫu kết thúc]] – chỗ bộ đọc tự ngắt ở
        dấu câu, để cắt đoạn giọng ra chèn thêm khoảng nghỉ đúng lúc nhân vật ngắt hơi (2.0.96)."""
        e = self._get(path)
        while len(e) < 6:
            e.append(None)
        if e[5] is None:
            a = _decode_cached(path)
            j = Path(path).with_name(Path(path).name + ".joins.json")
            if j.exists():   # đọc từng vế (2.1.3): chỗ nối giữa các vế biết chính xác
                try:
                    off, n = _trim_start(a), len(_trim_silence(a))
                    e[5] = [[max(0, int(x) - off), min(n, int(y) - off)] for x, y in json.loads(j.read_text())
                            if 0 < int(x) - off < n]
                except (OSError, ValueError, TypeError):
                    e[5] = clip_gaps(_trim_silence(a), SR)
            else:
                e[5] = clip_gaps(_trim_silence(a), SR)
            self.dirty = True
        return e[5]

    def speed_file(self, path, speed: float) -> None:
        """Ghi nhớ bản tua nhanh vừa lưu; bản tốc độ cũ của cùng câu bị xoá (mỗi câu giữ 1 bản)."""
        e = self._get(path)
        old = e[4]
        if old is not None and abs(float(old) - speed) > 1e-6:
            Path(path).with_name(f"{Path(path).name}.x{float(old):.3f}.npy").unlink(missing_ok=True)
        e[4] = speed
        self.dirty = True

    def save(self) -> None:
        if not self.dirty:
            return
        tmp = self.path.with_suffix(".tmp")
        try:
            tmp.write_text(json.dumps(self.d, separators=(",", ":")), encoding="utf-8")
            tmp.replace(self.path)
            self.dirty = False
        except OSError:
            pass


def _clip_at(path: str | Path, speed: float, meta: _ClipMeta | None = None) -> np.ndarray:
    """Đoạn giọng đã bỏ khoảng lặng 2 đầu ở tốc độ `speed`. Bản tua nhanh lưu đệm <file>.x<tốc độ>.npy cạnh file giọng
    -> xếp lại lần sau không phải gọi FFmpeg cho từng câu (trước đây mỗi lần xếp lại: 1 lần FFmpeg / câu đọc nhanh)."""
    if speed == 1.0:
        return _trim_silence(_decode_cached(path))
    p = Path(path)
    c = p.with_name(f"{p.name}.x{speed:.3f}.npy")
    try:
        if c.exists() and c.stat().st_mtime >= p.stat().st_mtime:
            return np.load(c).astype(np.float32) / 32768.0
    except Exception:  # noqa: BLE001 – file đệm hỏng -> giải mã lại
        pass
    a = _trim_silence(_decode(p, speed))
    try:
        np.save(c, np.clip(np.round(a * 32768.0), -32768, 32767).astype(np.int16))
        if meta is not None:
            meta.speed_file(p, speed)
    except OSError:
        pass
    return a


def _prefetch_clips(pairs, meta: _ClipMeta, progress=None, cancel=None) -> None:
    """Tua sẵn (song song, mỗi luồng 1 tiến trình FFmpeg) các đoạn giọng cần tốc độ khác ×1 mà chưa có bản đệm – lần
    đầu dựng bản lồng phim dài có hàng nghìn câu đọc nhanh (trước đây tua lần lượt từng câu)."""
    from concurrent.futures import ThreadPoolExecutor

    todo = []
    for path, speed in pairs:
        p = Path(path)
        c = p.with_name(f"{p.name}.x{speed:.3f}.npy")
        try:
            if not (c.exists() and c.stat().st_mtime >= p.stat().st_mtime):
                todo.append((p, speed))
        except OSError:
            continue
    if not todo:
        return

    def one(job):
        p, speed = job
        a = _trim_silence(_decode(p, speed))
        np.save(p.with_name(f"{p.name}.x{speed:.3f}.npy"), np.clip(np.round(a * 32768.0), -32768, 32767).astype(np.int16))
        return p, speed

    with ThreadPoolExecutor(max_workers=max(2, min(8, (os.cpu_count() or 4)))) as ex:
        for n, res in enumerate(ex.map(one, todo)):
            meta.speed_file(*res)
            if cancel and cancel():
                raise Cancelled()
            if progress and n % 50 == 0:
                progress(0.8, f"Đang tua giọng các câu đọc nhanh {n}/{len(todo)} (chỉ lần đầu, lần sau dùng lại)")


def _shaped_clip(path, speed: float, gain: float | None, keep_n: int, g: float | None = None,
                 meta: _ClipMeta | None = None, gaps=None) -> tuple[np.ndarray, float]:
    """Đoạn giọng đúng như đặt vào bản lồng: tốc độ, âm lượng (g: hệ số đã dùng; None = tính từ `gain` cân âm lượng,
    không vượt -1 dBFS), cắt + nhỏ dần cuối khi bị "Giữ đúng giờ" (keep_n mẫu), chèn khoảng nghỉ (gaps = [[mẫu cắt,
    số mẫu lặng thêm]] – chỗ nhân vật ngắt hơi). Trả (đoạn giọng, hệ số âm lượng)."""
    clip = _clip_at(path, speed, meta)
    if gaps:
        clip = insert_pauses(clip, gaps)
    if g is None:
        g = 1.0
        if gain and gain > 1.0 and len(clip):
            pk = float(np.abs(clip).max())
            if pk > 0 and min(gain, 0.89 / pk) > 1.0:
                g = round(min(gain, 0.89 / pk), 4)
    if g != 1.0:
        clip = clip * g
    if keep_n:
        clip = clip[:keep_n].copy()
        f = min(int(FADE * SR), len(clip) // 3)
        if f > 0:
            clip[-f:] *= np.linspace(1.0, 0.0, f, dtype=np.float32)
    return clip, g


PAUSE_MIN = 0.3     # nhân vật ngừng ≥ 0,3 s giữa câu (ở chỗ có dấu câu) -> giọng lồng cũng nghỉ ở đó (dub_pause_min)
PAUSE_SIL = 0.06    # chỗ lặng trong giọng lồng ≥ 60 ms mới cắt được (không cắt giữa chữ)
PAUSE_MAX = 1.5     # thêm tối đa 1,5 s lặng mỗi chỗ nghỉ
JOIN_GAP = 0.15     # đọc từng vế (2.1.3): các vế nối nhau bằng khoảng lặng ngắn như hơi thở ở dấu phẩy
PART_SPEED = 1.25   # vế dài hơn vế gốc (vế sau sẽ vào muộn): chỉ đọc nhanh riêng vế đó, tối đa thêm ×1,25


def clip_gaps(a: np.ndarray, sr: int = SR) -> list:
    """Chỗ lặng bên trong đoạn giọng (bộ đọc tự ngắt ở dấu câu): [[mẫu bắt đầu, mẫu kết thúc]], ≥ PAUSE_SIL."""
    hop = max(1, sr // 100)
    n = len(a) // hop
    if n < 10:
        return []
    fr = a[:n * hop].reshape(n, hop)
    db = 20 * np.log10(np.sqrt(np.mean(fr * fr, axis=1)) + 1e-6)
    thr = max(float(np.percentile(db, 90)) - 30.0, -60.0)
    quiet = db < thr
    out, i, need = [], 0, max(1, int(round(PAUSE_SIL * 100)))
    while i < n:
        if not quiet[i]:
            i += 1
            continue
        j = i
        while j < n and quiet[j]:
            j += 1
        if j - i >= need and i > 0 and j < n:
            out.append([i * hop, j * hop])
        i = j
    return out


def insert_pauses(clip: np.ndarray, gaps) -> np.ndarray:
    """Chèn lặng vào đoạn giọng: gaps = [[mẫu cắt (đã theo tốc độ), số mẫu lặng(, tốc độ riêng vế trước ×1000)]] – cắt
    ở giữa chỗ bộ đọc tự ngắt / chỗ nối 2 vế; vế trước dài hơn vế gốc thì đọc nhanh riêng vế đó (2.1.3)."""
    parts, last = [], 0
    for g in sorted((tuple(int(v) for v in q) for q in gaps)):
        cut, pad = g[0], g[1]
        f = g[2] / 1000.0 if len(g) > 2 else 1.0
        cut = min(max(cut, last), len(clip))
        piece = clip[last:cut]
        if f > 1.001 and len(piece) > SR // 10:
            from .voicefx import stretch

            piece = stretch(piece, f, SR)
        parts += [piece, np.zeros(max(0, pad), np.float32)]
        last = cut
    parts.append(clip[last:])
    return np.concatenate(parts).astype(np.float32, copy=False)


def pause_points(s: Segment, lang: str = "vi", min_gap: float = PAUSE_MIN) -> list[tuple[float, float, int, int]]:
    """Chỗ nhân vật ngắt hơi giữa câu (theo mốc từng chữ thật extra["ct"]) mà câu dịch có dấu câu tương ứng:
    [(giây thôi nói, giây nói tiếp, vị trí ngắt trong câu gốc, vị trí ngắt trong câu dịch)]. Không có mốc từng chữ /
    không dấu câu ghép được -> []."""
    from .align import CJK, _pair_cuts
    from .subtitle import ct_of

    ct = ct_of(s)
    zh, tr = (s.text or "").strip(), (s.trans or "").strip()
    if not ct or len(ct) < 2 or not tr:
        return []
    out = []
    for pz, pt, _kind in _pair_cuts(zh, tr, lang):
        k = len(CJK.findall(zh[:pz]))
        if not 0 < k < len(ct):
            continue
        g0, g1 = float(ct[k - 1][1]), float(ct[k][0])
        if g1 - g0 >= min_gap:
            out.append((g0, g1, pz, pt))
    return out


def orig_pauses(s: Segment, lang: str = "vi", min_gap: float = PAUSE_MIN) -> list[tuple[float, float, float]]:
    """pause_points dạng [(giây thôi nói, giây nói tiếp, tỉ lệ vị trí trong câu dịch)]."""
    tr = (s.trans or "").strip()
    nt = max(1, langs.units(tr, lang))
    return [(g0, g1, langs.units(tr[:pt], lang) / nt) for g0, g1, _pz, pt in pause_points(s, lang, min_gap)]


def breath_points(s: Segment, min_gap: float = PAUSE_MIN) -> list[int]:
    """Chỗ nhân vật ngắt hơi trong câu GỐC (khe ≥ min_gap giữa 2 chữ, có dấu câu ở đó): vị trí ngay sau dấu câu –
    cùng cách tính với vị trí câu gốc của align._pair_cuts (để biết câu dịch đã giữ đủ các vế chưa)."""
    from .align import END_ZH, CLAUSE_ZH, _bounds, _char_positions
    from .subtitle import ct_of

    ct = ct_of(s)
    zh = (s.text or "").strip()
    pos = _char_positions(zh)
    if not ct or len(ct) != len(pos) or len(pos) < 2:
        return []
    out = []
    for pz, _kind in _bounds(zh, END_ZH, CLAUSE_ZH):
        k = sum(1 for q in pos if q < pz)
        if 0 < k < len(ct) and float(ct[k][0]) - float(ct[k - 1][1]) >= min_gap:
            out.append(pz)
    return out


def clause_texts(text: str, cuts: list[int]) -> list[str]:
    """Chia câu dịch thành các vế tại các vị trí ngắt (ngay sau dấu câu)."""
    out, last = [], 0
    for c in sorted(set(int(x) for x in cuts)):
        if last < c < len(text):
            out.append(text[last:c].strip())
            last = c
    out.append(text[last:].strip())
    return [x for x in out if x]


def join_parts(files: list[str | Path], out: str | Path, gap: float = JOIN_GAP) -> list[list[int]]:
    """Ghép giọng từng vế (đã bỏ lặng 2 đầu) thành 1 đoạn giọng, giữa các vế lặng `gap` giây; ghi kèm
    <file>.joins.json = chỗ nối [[mẫu đầu, mẫu cuối]] (để cắt chèn khoảng nghỉ đúng chỗ nối, không đoán theo độ to)."""
    clips = [_trim_silence(_decode_cached(f)) for f in files]
    z = np.zeros(int(gap * SR), np.float32)
    pieces, runs, pos = [], [], 0
    for j, c in enumerate(clips):
        if j:
            pieces.append(z)
            runs.append([pos, pos + len(z)])
            pos += len(z)
        pieces.append(c)
        pos += len(c)
    out = Path(out)
    write_wav(out, np.concatenate(pieces) if pieces else z)
    out.with_name(out.name + ".joins.json").write_text(json.dumps(runs), encoding="utf-8")
    return runs


def _trim_start(a: np.ndarray, thr: float = 0.01) -> int:
    """Mẫu bắt đầu của _trim_silence(a)."""
    if not len(a):
        return 0
    pk = float(np.abs(a).max())
    thr = min(thr, max(0.002, 0.03 * pk))
    idx = np.where(np.abs(a) > thr)[0]
    return max(0, int(idx[0]) - int(0.03 * SR)) if len(idx) else 0


def plan_pauses(s: Segment, x: dict, runs: list, n1: int, speed: float, room: float, lang: str = "vi",
                min_gap: float = PAUSE_MIN, part_speed: float = 1.0, max_speed: float = 1.5) -> list[list[int]]:
    """Khoảng nghỉ chèn vào giọng lồng 1 câu: phần sau chỗ nhân vật ngắt hơi bắt đầu đúng lúc nhân vật nói tiếp (tính
    từ lúc giọng câu bắt đầu như trong phim gốc), chỉ dùng chỗ trống `room` giây trước câu sau. runs: chỗ lặng của
    đoạn giọng ở ×1 (mẫu), n1: độ dài ×1. part_speed > 1 (đọc từng vế): vế trước dài hơn vế gốc -> đọc nhanh riêng
    vế đó (≤ part_speed, tổng ≤ max_speed) để vế sau vẫn vào đúng lúc. Trả [[mẫu cắt (theo tốc độ), số mẫu lặng(,
    tốc độ riêng ×1000)]]."""
    ps = orig_pauses(s, lang, min_gap)
    if not ps or not runs or (room < 0.05 and part_speed <= 1.0):
        return []
    out, added, used, prev = [], 0.0, set(), 0
    for g0, g1, frac in ps:
        exp = frac * n1
        best = None
        for j, (a, b) in enumerate(runs):
            if j in used:
                continue
            d = abs((a + b) / 2 - exp)
            if d > max(0.25 * n1, 0.4 * SR):
                continue
            # 2.1.12: chỗ bộ đọc ngắt ở dấu phẩy thường lặng ≥ 0,1 s, chỗ khép âm giữa 2 tiếng chỉ vài chục ms – lặng
            # dài hơn 0,1 s được ưu tiên như gần hơn 0,4 s (không chèn nghỉ vào giữa 1 cụm từ)
            score = d - 4.0 * (b - a)
            if best is None or score < best[0]:
                best = (score, j, a, b)
        if best is None:
            continue
        _d, j, a, b = best
        used.add(j)
        mid = (a + b) / 2
        after = x["start"] + added + (b / speed) / SR          # lúc giọng lồng nói tiếp nếu không chèn gì
        target = x["start"] + (g1 - s.start)                    # lúc nhân vật nói tiếp
        f = 1.0
        if after - target > 0.05 and part_speed > 1.0:          # vế trước dài hơn vế gốc: đọc nhanh riêng vế đó
            dur = (a - prev) / speed / SR
            if dur > 0.3:
                f = min(part_speed, max(1.0, max_speed / max(speed, 1e-3)), dur / max(0.1, dur - (after - target)))
                if f > 1.02:
                    added -= dur * (1 - 1 / f)
                    after -= dur * (1 - 1 / f)
                else:
                    f = 1.0
        pad = max(0.0, min(PAUSE_MAX, room - added, target - after))
        if pad < 0.05 and f == 1.0:
            prev = b
            continue
        out.append([int(mid / speed), int(pad * SR)] + ([int(round(f * 1000))] if f > 1.0 else []))
        added += pad if pad >= 0.05 else 0.0
        if pad < 0.05:
            out[-1][1] = 0
        prev = b
    return out


def _gk(e) -> tuple:
    """Khoá so sánh 1 mục bảng đoạn giọng đã đặt (cả khoảng nghỉ chèn thêm – mục cũ không có)."""
    return tuple(e[:5]) + (tuple(tuple(int(v) for v in q) for q in (e[6] if len(e) > 6 and e[6] else ())),)


def merge_regions(spans, limit: int, join: int = SR // 2) -> list[tuple[int, int]]:
    """Gộp các đoạn (mẫu) chồng / sát nhau (< 0,5 s) và cắt trong [0, limit)."""
    out: list[list[int]] = []
    for a, b in sorted((max(0, int(a)), min(int(limit), int(b))) for a, b in spans):
        if b <= a:
            continue
        if out and a <= out[-1][1] + join:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return [(a, b) for a, b in out]


def wav_info(path: str | Path) -> tuple[int, int, int, int, int]:
    """(vị trí bắt đầu dữ liệu, số khung, tần số mẫu, số kênh, số byte / mẫu) của file wav PCM."""
    with open(path, "rb") as f:
        head = f.read(12)
        if head[:4] != b"RIFF" or head[8:12] != b"WAVE":
            raise ValueError("không phải file wav")
        sr = ch = sw = 0
        while True:
            h = f.read(8)
            if len(h) < 8:
                raise ValueError("file wav thiếu dữ liệu")
            cid, size = h[:4], int.from_bytes(h[4:], "little")
            if cid == b"fmt ":
                fmt = f.read(size)
                ch, sr = int.from_bytes(fmt[2:4], "little"), int.from_bytes(fmt[4:8], "little")
                sw = int.from_bytes(fmt[14:16], "little") // 8
                continue
            if cid == b"data":
                if not (sr and ch and sw):
                    raise ValueError("file wav thiếu thông tin định dạng")
                return f.tell(), size // (ch * sw), sr, ch, sw
            f.seek(size + (size & 1), 1)


def _patch_wav(path: str | Path, regions: list[tuple[int, int]], tab: list[list], by_name: dict[str, str],
               meta: _ClipMeta, n_out: int) -> None:
    """Ghi lại đúng các đoạn thời gian `regions` (mẫu) của file bản lồng: trộn lại mọi đoạn giọng (theo tab) chạm vào
    đoạn đó + nén mềm như khi dựng cả bản. Kiểm tra định dạng trước khi ghi (không hợp -> _NoPatch, chưa đụng file)."""
    try:
        off, frames, sr, ch, sw = wav_info(path)
    except (OSError, ValueError) as e:
        raise _NoPatch(str(e)) from None
    if (sr, ch, sw) != (SR, 1, 2) or frames != n_out:
        raise _NoPatch("bản lồng đang dùng khác định dạng / độ dài")
    if not regions:
        return
    try:
        f = open(path, "r+b")
    except OSError as e:
        raise _NoPatch(str(e)) from None
    with f:
        for r0, r1 in regions:
            buf = np.zeros(r1 - r0, dtype=np.float32)
            for e in tab:
                name, st, speed, g, keep_n, full = e[:6]
                if st >= r1 or st + full <= r0 or name not in by_name:
                    continue
                clip, _g = _shaped_clip(by_name[name], speed, None, keep_n, g, meta, e[6] if len(e) > 6 else None)
                a, b = max(r0, st), min(r1, st + len(clip))
                if b > a:
                    buf[a - r0:b - r0] += clip[a - st:b - st]
            soft_limit(buf)
            f.seek(off + r0 * 2)
            f.write((np.clip(buf, -1.0, 1.0) * 32767).astype(np.int16).tobytes())


def synthesize_dub(segs: list[Segment], out_wav: str | Path, total_duration: float, work_dir: str | Path,
                   voice: str = "edge:vi-VN-HoaiMyNeural", rate: str = "+0%", pitch: str = "+0Hz",
                   max_speedup: float = 1.6, progress: Callable[[float, str], None] | None = None,
                   cancel: Callable[[], bool] | None = None, voices: dict | None = None,
                   ref_audio: str | Path | None = None, settings: dict | None = None,
                   skip_songs: bool = True, stats: dict | None = None, only: set[int] | None = None,
                   lang: str = "vi", genders: dict | None = None, prepare_only: bool = False,
                   patch: dict | None = None) -> Path:
    """patch: {"wav": bản lồng đang dùng, "placed": bảng đoạn giọng đã đặt trong bản đó} -> chỉ ghi lại những đoạn thời
    gian có thay đổi vào chính file đó (xoá / kéo / gộp / đọc lại vài câu: ~1 giây thay vì dựng lại cả phim);
    stats["patched"] = các đoạn đã ghi (mẫu 24 kHz). Không vá được -> dựng cả bản vào out_wav như thường.
    prepare_only: chỉ đọc giọng từng câu vào kho đệm (work/tts), không xếp / không ghi bản lồng – chế độ vừa tách
    vừa dịch đọc trước các câu đã dịch, bước lồng tiếng sau đó dùng lại file (cùng lời + giọng = cùng file).
    only: chỉ đọc (lại) các câu này; câu khác dùng giọng đã đọc ở bản trước (extra["tts_file"]) dù lời đã đổi.
    Câu đọc lại bị lỗi mà có giọng cũ -> giữ giọng cũ (báo lỗi), không làm hỏng cả bản lồng tiếng.
    lang: tiếng đích của dự án; mỗi câu còn được nhận tiếng theo chữ viết (langs.detect) -> giọng Edge tự đổi đúng tiếng,
    OmniVoice đọc đúng tiếng, VieNeu (chỉ tiếng Việt) gặp câu tiếng khác thì đọc bằng Edge cùng nam / nữ.
    genders: {người nói: {"g": "male"/"female"}} để chọn giọng Edge thay cho VieNeu."""
    settings = settings or {}
    stats = stats if stats is not None else {}
    lang = langs.norm(lang)
    genders = genders or {}
    n_song = n_empty = n_nodub = n_lang = 0
    fallback: dict[str, int] = {}   # giọng đã chọn mà không dùng được (chưa cài bộ đọc / mẫu bị xoá) -> số câu
    by_lang: dict[str, int] = {}
    work = Path(work_dir)
    work.mkdir(parents=True, exist_ok=True)
    voices = voices or {}
    if not voice.startswith(("edge:", "vieneu:", "omni:", "omnid:")) and voice not in (CLONE, OMNI_CLONE):
        voice = "edge:" + voice  # tương thích bản cũ
    mode = settings.get("vieneu_mode", "v3turbo")

    edge_items: list[tuple[int, str, Path, str]] = []
    vn_items: list[tuple[int, str, Path, str]] = []
    omni_items: list[tuple[int, str, Path, str]] = []
    groups: dict[int, str] = {}   # câu -> mã giọng (cân âm lượng giữa các câu cùng giọng)
    vn_params = vieneu_params(settings)
    samples = {}
    from . import omni_design

    if omnivoice_available():
        from . import voices as vlib

        samples = {v["id"]: v for v in vlib.list_samples()}
    from . import trend_voices

    vn_presets = vieneu_presets(mode) if vieneu_available() else []
    vn_gender = {v["id"]: v.get("gender", "") for v in (vn_presets + vieneu_sample_voices() + trend_voices.voices())} \
        if vieneu_available() else {}
    # đọc từng vế (2.1.3): câu có chỗ nhân vật ngắt hơi ghép được với dấu câu của câu dịch -> mỗi vế 1 lần đọc rồi
    # ghép lại (chỗ nối biết chính xác) -> giọng lồng nghỉ đúng chỗ, vế sau vào đúng lúc nhân vật nói tiếp
    by_clause = bool(settings.get("dub_by_clause", True)) and bool(settings.get("dub_pauses", True))
    min_gap = float(settings.get("dub_pause_min", PAUSE_MIN))
    joins: dict[int, tuple[Path, list[Path]]] = {}
    queued: set[str] = set()
    n_clause = 0
    for i, s in enumerate(segs):
        lg = langs.detect(s.trans or "", lang)
        t = clean_tts_text(s.trans or "", lg)
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
        keep = only is not None and i not in only   # câu không chọn: giữ nguyên giọng đang có trong bản lồng
        if keep:
            old = s.extra.get("tts_file")
            if old and Path(old).exists():
                s.extra["_tts"] = old
                continue
        spec = s.extra.get("voice") or voices.get(s.speaker or "S1") or voice   # giọng riêng của câu (nếu có)
        want = spec
        if spec in (CLONE,) or spec.startswith("vieneu:"):
            if not vieneu_available() or (spec.startswith((VN_SAMPLE, VN_LIB)) and not vieneu_ref(spec)) or \
                    (spec.startswith(VN_TREND) and not trend_voices.get(spec)):
                spec = "edge:vi-VN-HoaiMyNeural"   # chưa cài VieNeu / giọng mẫu đã bị xoá
        if spec == OMNI_CLONE or spec.startswith(("omni:", "omnid:")):
            if not omnivoice_available() or (spec.startswith("omni:") and spec[5:] not in samples) or \
                    (spec.startswith("omnid:") and not omni_design.get(spec)):
                spec = "edge:vi-VN-HoaiMyNeural"   # chưa cài OmniVoice / giọng mẫu đã bị xoá
        if spec != want:   # không đọc được bằng giọng đã chọn -> báo rõ (trước đây đổi im lặng sang Hoài My)
            fallback[want] = fallback.get(want, 0) + 1
        s.extra["voice_used"] = want if spec == want else spec
        if lg != "vi" and (spec == CLONE or spec.startswith("vieneu:")):
            # VieNeu chỉ đọc tiếng Việt -> câu tiếng Anh / Thái / Nhật / Hàn đọc bằng Edge cùng nam / nữ
            g = vn_gender.get(spec) or (genders.get(s.speaker or "S1") or {}).get("g", "")
            spec = langs.edge_voice(lg, g, _spk_index(s.speaker))
            n_lang += 1
        if spec.startswith("edge:"):
            spec = edge_for(spec, lg)        # giọng Edge tiếng Việt gặp câu tiếng khác -> giọng tiếng đó
        by_lang[lg] = by_lang.get(lg, 0) + 1
        if spec == CLONE:
            spec = f"clone:{s.speaker or 'S1'}"
        if spec == OMNI_CLONE:
            spec = f"omniclone:{s.speaker or 'S1'}"
        extra = ""
        if not spec.startswith("edge:") and lg == "vi":
            t = spoken_vi(t)   # bộ đọc AI: số đọc thành chữ ("90.000" -> "chín mươi nghìn"); Edge tự đọc số
        if spec.startswith("omni:"):   # sửa lời / file mẫu thì đọc lại
            sm = samples[spec[5:]]
            extra = f"{sm.get('text', '')}|{sm.get('file')}|{settings.get('omnivoice_model', '')}"
        if spec.startswith("omnid:"):  # đổi mô tả / mô hình -> câu mẫu mới -> đọc lại
            extra = omni_design.key(spec, settings.get("omnivoice_model", ""), lg)
        if spec.startswith(("omni:", "omniclone:", "omnid:")):
            extra += "|nofade"          # 2.0.34: bỏ đoạn nhỏ dần 0,1 s cuối câu (mất phụ âm cuối của câu ngắn)
            if lg != "vi":
                extra += "|" + lg
        elif spec.startswith(("vieneu:", "clone:")):
            extra += "|" + settings.get("vieneu_style", "clear")
            if spec.startswith(VN_LIB):     # đổi file mẫu cùng mã -> đọc lại
                ref = vieneu_ref(spec)
                extra += f"|{Path(ref).stat().st_mtime if ref else ''}"
            if spec.startswith(VN_TREND):   # đổi giọng gốc / hiệu ứng của giọng trend -> đọc lại
                extra += "|" + trend_voices.key(spec) + "|" + trend_voices.base_voice(spec, vn_presets)
        groups[i] = spec
        take = int(s.extra.get("take", 0) or 0)   # "Tạo lại giọng": lần đọc mới của cùng câu

        def tts_path(text: str) -> Path:
            ex = extra
            if spec.startswith(("omni:", "omniclone:", "omnid:")) and omni_short(text):
                ex += "|dur2"   # 2.1.5: câu ngắn đọc trước đó bị OmniVoice kéo dài -> đọc lặp lời -> đọc lại
            elif spec.startswith(("vieneu:", "clone:")) and mode != "v3nano" and vn_short(text):
                ex += "|cap3"   # 2.1.7: câu VieNeu ≤ 4 tiếng đọc trước đó có thể lặp lời -> đọc lại (trần thấp + cắt lặp)
            h = hashlib.md5((f"{text}|{spec}|{rate}|{pitch}|{mode}|{ex}" + (f"|take{take}" if take else ""))
                            .encode()).hexdigest()[:14]
            return work / (f"tts_{h}.mp3" if spec.startswith("edge:") else f"tts_{h}.wav")

        def enqueue(text: str, path: Path) -> None:
            if str(path) in queued:
                return
            queued.add(str(path))
            if spec.startswith(("omni:", "omniclone:", "omnid:")):
                omni_items.append((i, text, path, spec, lg))
            elif spec.startswith("edge:"):
                edge_items.append((i, text, path, spec[5:]))
            else:
                vn_items.append((i, text, path, spec))

        parts: list[str] = []
        # 2.1.12: CHỈ Edge đọc từng vế. Bộ đọc AI (VieNeu / OmniVoice) tự quyết lúc dừng -> vế càng ngắn càng hay trượt
        # chỗ dừng rồi đọc lặp lời (2.1.5–2.1.7) -> đọc CẢ CÂU 1 lần như bản 2.0.90; chỗ nghỉ vẫn đúng lúc nhân vật nói
        # tiếp nhờ chèn lặng vào đúng chỗ bộ đọc tự ngắt ở dấu phẩy (plan_pauses, như 2.0.96).
        if by_clause and spec.startswith("edge:") and not keep and "ct" in s.extra:
            cuts = [pt for _a, _b, _pz, pt in pause_points(s, lg, min_gap)]
            if cuts:
                parts = [clean_tts_text(x, lg) for x in clause_texts(s.trans or "", cuts)]
                if len(parts) < 2 or not all(re.search(r"\w", x) for x in parts):
                    parts = []
        if parts:
            pfiles = [tts_path(x) for x in parts]
            for x, pf in zip(parts, pfiles):
                if not (pf.exists() and pf.stat().st_size > 0):
                    enqueue(x, pf)
            hj = hashlib.md5(("|".join(pf.name for pf in pfiles) + f"|join{JOIN_GAP}").encode()).hexdigest()[:14]
            pj = work / f"tts_{hj}_v.wav"
            joins[i] = (pj, pfiles)
            s.extra["_tts"] = str(pj)
            n_clause += 1
            continue
        p = tts_path(t)
        have = p.exists() and p.stat().st_size > 0
        if keep:   # bản lồng cũ (trước 2.0.26) chưa ghi tts_file: dùng file đã đọc nếu còn, không đọc mới
            if have:
                s.extra["_tts"] = str(p)
            continue
        if not have:
            enqueue(t, p)
        s.extra["_tts"] = str(p)
    stats["clauses"] = n_clause
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
        watch = RateWatch() if mode != "v3nano" else None
        speed = _speed_tag(rate)
        for n, (i, text, p, spec) in enumerate(vn_items):
            if cancel and cancel():
                raise Cancelled()
            if n and n % DUB_BATCH == 0:   # mỗi 1000 câu mở lại tiến trình đọc (trả bộ nhớ GPU, tránh chậm dần / treo)
                tts.stop()
                tts.ensure_started(None, cancel)
            base_p = n / max(1, total_items) * 0.8

            def waiting(sec, n=n, base_p=base_p):   # câu đọc lâu: dòng tiến độ đếm giây, không đứng im như treo
                if progress:
                    progress(base_p, f"VieNeu đang đọc {n + 1}/{total_items} – câu này đã {sec:.0f} s "
                                     "(thường chỉ vài giây)")
            rep: dict = {}
            chk = {"cap_check": mode != "v3nano", "report": rep,   # Nano không sinh theo khung -> không có trần
                   "watch": watch, "wkey": spec}
            try:
                tmp = p.with_suffix(".raw.wav")
                if spec.startswith("clone:"):
                    spk = spec[6:]
                    if spk not in refs:
                        refs[spk] = build_reference(ref_audio, segs, spk, work / f"ref_{spk}.wav") if ref_audio else None
                    # không đủ mẫu giọng -> dùng giọng mặc định
                    synth_checked(tts, text, tmp, ref_audio=str(refs[spk]) if refs[spk] else None, params=vn_params,
                                  on_wait=waiting, **chk)
                elif spec.startswith((VN_SAMPLE, VN_LIB)):
                    synth_checked(tts, text, tmp, ref_audio=vieneu_ref(spec), params=vn_params, on_wait=waiting, **chk)
                elif spec.startswith(VN_TREND):
                    trend_synth(tts, spec, text, tmp, vn_params, vn_presets, on_wait=waiting, **chk)
                else:
                    synth_checked(tts, text, tmp, voice=spec[7:] or None, params=vn_params, on_wait=waiting, **chk)
                if rep.get("babble") == 1:
                    stats["babble_fixed"] = stats.get("babble_fixed", 0) + 1
                elif rep.get("babble") == 2:
                    stats.setdefault("babble_left", []).append(i + 1)
                if rep.get("long") == 1:
                    stats["long_fixed"] = stats.get("long_fixed", 0) + 1
                elif rep.get("long") == 2:
                    stats.setdefault("long_left", []).append(i + 1)
                sr = read_wav_rate(tmp) or tts.sr
                if abs(speed - 1) > 0.01:
                    a = _decode(tmp, speed, sr)
                    write_wav(p, a, sr)
                    tmp.unlink(missing_ok=True)
                else:
                    tmp.replace(p)
            except Cancelled:
                raise
            except Exception as e:
                errors.append(f"Câu {i + 1}: {e}")
            if progress:
                msg = f"VieNeu đang đọc {n + 1}/{total_items}"
                if tts.note:    # vừa mở lại bộ đọc vì 1 câu chậm bất thường
                    stats["slow_restarts"] = stats.get("slow_restarts", 0) + 1
                    msg += " · " + tts.note
                    tts.note = ""
                progress((n + 1) / max(1, total_items) * 0.8, msg)

    orefs: dict[tuple, tuple] = {}

    def omni_ref(spec: str, lg: str = "vi") -> tuple:
        """(file mẫu, lời trong file mẫu) cho mã giọng OmniVoice (giọng chọn sẵn: câu mẫu cùng tiếng với câu đọc)."""
        ck = (spec, lg if spec.startswith("omnid:") else "")
        if isinstance(orefs.get(ck), Exception):   # câu mẫu đã lỗi: không thử lại ở mỗi câu
            raise orefs[ck]
        if ck not in orefs:
            if spec.startswith("omni:"):
                sm = samples[spec[5:]]
                orefs[ck] = (str(vlib.path_of(sm)), sm.get("text", ""))
            elif spec.startswith("omnid:"):   # giọng chọn sẵn: nhái câu mẫu tạo 1 lần -> cả video 1 giọng
                try:
                    orefs[ck] = omni_design.ensure_anchor(om, spec,
                                                          settings.get("omnivoice_model") or "k2-fsa/OmniVoice", lg)
                except Exception as e:  # noqa: BLE001
                    orefs[ck] = e
                    raise
            else:
                spk = spec[10:]
                r = build_reference_text(ref_audio, segs, spk, work / f"omniref_{spk}.wav") if ref_audio else None
                orefs[ck] = (str(r[0]), r[1]) if r else (None, "")
        return orefs[ck]

    # --- OmniVoice (offline): nhái giọng từ file ghi âm mẫu / từ nhân vật gốc
    if omni_items:
        if progress:
            progress(0.01, "Đang nạp OmniVoice…")
        om = _omni(settings)
        om.ensure_started((lambda x, m: progress(0.01 * x, m)) if progress else None, cancel)
        speed = _speed_tag(rate)
        base = len(vn_items)
        for n, (i, text, p, spec, lg) in enumerate(omni_items):
            if cancel and cancel():
                raise Cancelled()
            if n and n % DUB_BATCH == 0:
                om.stop()
                om.ensure_started(None, cancel)
            try:
                ref, rtext = omni_ref(spec, lg)
                tmp = p.with_suffix(".raw.wav")
                synth_checked(om, text, tmp, ref_audio=ref, ref_text=rtext, language=lg,
                              on_wait=lambda sec, n=n: progress and progress(
                                  (base + n) / max(1, total_items) * 0.8,
                                  f"OmniVoice đang đọc {n + 1}/{len(omni_items)} – câu này đã {sec:.0f} s"))
                sr = om.sr
                if abs(speed - 1) > 0.01:
                    write_wav(p, _decode(tmp, speed, sr), sr)
                    tmp.unlink(missing_ok=True)
                else:
                    tmp.replace(p)
            except Cancelled:
                raise
            except Exception as e:
                errors.append(f"Câu {i + 1}: {e}")
            if progress:
                msg = f"OmniVoice đang đọc {n + 1}/{len(omni_items)}"
                if om.note:
                    stats["slow_restarts"] = stats.get("slow_restarts", 0) + 1
                    msg += " · " + om.note
                    om.note = ""
                progress((base + n + 1) / max(1, total_items) * 0.8, msg)

    # --- Edge (online)
    if edge_items:
        base = len(vn_items) + len(omni_items)
        nb = (len(edge_items) + DUB_BATCH - 1) // DUB_BATCH
        for b in range(0, len(edge_items), DUB_BATCH):   # video dài: đọc từng đợt 1000 câu (file đã đọc được giữ)
            if cancel and cancel():
                raise Cancelled()
            if nb > 1 and progress:
                progress((base + b) / max(1, total_items) * 0.8,
                         f"Đợt {b // DUB_BATCH + 1}/{nb}: đọc câu {b + 1}–{min(b + DUB_BATCH, len(edge_items))}")
            errors += asyncio.run(_edge_all(edge_items[b:b + DUB_BATCH], rate, pitch, progress, cancel, total_items,
                                            base + b))
    if cancel and cancel():
        raise Cancelled()
    for i, (pj, pfiles) in joins.items():   # đọc từng vế: ghép các vế đã đọc thành đoạn giọng của câu
        try:
            if all(pf.exists() and pf.stat().st_size > 0 for pf in pfiles):
                if not (pj.exists() and pj.stat().st_mtime >= max(pf.stat().st_mtime for pf in pfiles)):
                    join_parts(pfiles, pj)
            else:
                pj.unlink(missing_ok=True)   # có vế đọc lỗi -> câu không có giọng (hoặc giữ giọng cũ bên dưới)
        except Exception as e:  # noqa: BLE001
            errors.append(f"Câu {i + 1}: ghép các vế lỗi ({e})")
            pj.unlink(missing_ok=True)
    redone = kept_old = 0
    for i, s in enumerate(segs):   # câu đọc (lại) bị lỗi nhưng đã có giọng ở bản trước -> dùng giọng cũ
        p = s.extra.get("_tts")
        if p and only is not None and i in only:
            if Path(p).exists():
                redone += 1
            elif s.extra.get("tts_file") and Path(s.extra["tts_file"]).exists():
                s.extra["_tts"] = s.extra["tts_file"]
                kept_old += 1
    ok = sum(1 for s in segs if s.extra.get("_tts") and Path(s.extra["_tts"]).exists())
    used: dict[str, dict[str, int]] = {}   # người nói -> {giọng thật đã đọc: số câu}
    for s in segs:
        if s.extra.get("_tts") and s.extra.get("voice_used"):
            d = used.setdefault(s.speaker or "S1", {})
            d[s.extra["voice_used"]] = d.get(s.extra["voice_used"], 0) + 1
    stats["voices_used"] = used
    stats["fallback"] = fallback
    if prepare_only:
        stats["prepared"] = ok
        stats["errors"] = errors
        return Path(out_wav)
    if ok == 0:
        if total_items:
            raise RuntimeError("Không tạo được giọng đọc. " + "; ".join(errors[:3]))
        raise RuntimeError(_why_nothing(len(segs), n_song, n_empty, n_nodub))

    # --- xếp giọng theo thời gian: mỗi câu là 1 đoạn giọng riêng, đặt đúng lúc nhân vật nói (hoặc chỗ người dùng kéo
    # tới trên timeline: extra["dub_shift"]); câu dài hơn chỗ trống xử lý theo cách xếp đã chọn (plan_layout)
    total = max(total_duration, max((s.end for s in segs), default=0) + 1)

    def want(k: int) -> float:
        return max(0.0, segs[k].start + float(segs[k].extra.get("dub_shift", 0) or 0))

    order = sorted(range(len(segs)), key=want)
    meta = _ClipMeta(work)   # độ dài / độ to từng file giọng lưu đệm: xếp lại không phải giải mã lại mọi câu
    meta.warm(s.extra["_tts"] for s in segs if s.extra.get("_tts") and Path(s.extra["_tts"]).exists())
    files: dict[int, str] = {}
    for k in order:
        s = segs[k]
        p = s.extra.pop("_tts", None)
        for key in ("fit", "fit_shift", "fit_over", "fit_need", "fit_have", "dub_at", "dub_pauses"):
            s.extra.pop(key, None)
        if not p or not Path(p).exists():
            continue
        if meta.n(p) == 0:
            errors.append(f"Câu {k + 1}: giọng đọc tạo ra toàn khoảng lặng")
            continue
        files[k] = p
    old_g = {e[0]: e[3] for e in (patch or {}).get("placed") or []}

    # cân âm lượng: câu nhỏ hơn hẳn các câu cùng giọng (hoặc giọng nhỏ hơn hẳn giọng khác) được tăng lên cho đều.
    # Vá bản lồng đang có: câu cũ giữ đúng âm lượng đang nghe, chỉ câu mới đọc mới tính
    gains: dict[int, float] = {}
    if settings.get("dub_level_match", True) and (patch is None or any(Path(f).name not in old_g
                                                                       for f in files.values())):
        levels = {k: meta.level(p) for k, p in files.items()}
        gains = level_gains(levels, {k: groups.get(k) or "spk:" + (segs[k].speaker or "S1") for k in levels})
    items = []
    for k in order:
        if k not in files:
            continue
        s = segs[k]
        # tốc độ người dùng đặt cho câu (tab Lồng tiếng / timeline) = mức TỐI THIỂU: câu vốn phải đọc nhanh hơn để vừa
        # thì giữ tốc độ đó; < 1 = đọc chậm lại (nếu còn chỗ)
        items.append({"k": k, "want": want(k), "dur": meta.n(files[k]) / SR, "user": float(s.extra.get("speed") or 0),
                      "fixed": bool(s.extra.get("dub_shift")), "mode": s.extra.get("overflow") or "",
                      "pause": float(s.extra.get("pause_before") or 0)})
    durs = {it["k"]: it["dur"] for it in items}
    user_min = {it["k"]: it["user"] for it in items if it["user"]}
    plan = plan_layout(items, total, settings.get("dub_overflow", "smart"), max_speedup,
                       float(settings.get("dub_lead_max", 1.0)), float(settings.get("dub_max_lag", 2.0)))
    end_t = max([total] + [x["end"] + 0.5 for x in plan.values()])
    track_len = int(end_t * SR) + SR
    n_out = int(total * SR)
    # giọng lồng nghỉ đúng chỗ nhân vật ngắt hơi giữa câu (2.0.96): cắt đoạn giọng ở chỗ bộ đọc tự ngắt (dấu câu tương
    # ứng) rồi chèn lặng cho phần sau vào đúng lúc nhân vật nói tiếp – chỉ dùng chỗ trống trước câu sau, không xê dịch
    # câu nào khác; câu ngắn hay dài đều vậy (cần mốc từng chữ thật extra["ct"])
    pauses: dict[int, list] = {}
    if settings.get("dub_pauses", True):
        starts = sorted((x["start"], k) for k, x in plan.items())
        nxt = {k: starts[j + 1][0] for j, (_a, k) in enumerate(starts[:-1])}
        for k, x in plan.items():
            s = segs[k]
            if x["keep"] is not None or "ct" not in s.extra:
                continue
            room = min(nxt.get(k, total), total) - GAP - x["end"]
            spd = _clip_speed(x["speed"])
            # 2.1.12: cả câu đọc 1 lần (bộ đọc AI) cũng được đọc nhanh riêng vế trước khi vế đó dài hơn vế gốc (cắt ở
            # đúng chỗ bộ đọc tự ngắt) – như đọc từng vế mà không phải đọc riêng từng mẩu ngắn
            g = plan_pauses(s, x, meta.gaps(files[k]), meta.n(files[k]), spd, room,
                            langs.detect(s.trans or "", lang), min_gap, PART_SPEED, max_speedup)
            if g:
                pauses[k] = g
    stats["pauses"] = sum(1 for v in pauses.values() for q in v if q[1] > 0)
    stats["part_fast"] = sum(1 for v in pauses.values() for q in v if len(q) > 2)

    def assemble(patch_on: bool):
        """Đặt từng đoạn giọng vào bản lồng. patch_on: chỉ ghi lại những đoạn thời gian có thay đổi vào file bản lồng
        đang có (xoá / kéo / gộp câu… vài giây) thay vì dựng lại cả phim; không vá được thì báo _NoPatch."""
        old_keys = {_gk(e): int(e[5]) for e in patch["placed"]} if patch_on else {}
        if not patch_on:
            _prefetch_clips({(files[k], _clip_speed(x["speed"])) for k, x in plan.items()
                             if _clip_speed(x["speed"]) != 1.0}, meta, progress, cancel)
        track = None if patch_on else np.zeros(track_len, dtype=np.float32)
        tab: list[list] = []
        layout: list[list] = []
        too_long: list[tuple[int, float, float]] = []   # (câu, thời lượng cần, thời lượng có) -> để AI rút gọn
        placed = 0
        for n, k in enumerate(sorted(plan, key=lambda q: plan[q]["start"])):
            s, x, p = segs[k], plan[k], files[k]
            speed = _clip_speed(x["speed"])
            name = Path(p).name
            st = int(x["start"] * SR)
            # "Giữ đúng giờ": vẫn quá dài -> nhỏ dần phần cuối (≤0,3 s) rồi dừng đúng lúc câu sau vào
            keep_n = max(int(0.3 * SR), int(x["keep"] * SR)) if x["keep"] is not None else 0
            g = old_g.get(name) if patch_on else None
            gaps = pauses.get(k) or []
            ent = [name, st, speed, g, keep_n, 0] + ([gaps] if gaps else [])
            key = _gk(ent)
            if g is not None and key in old_keys:     # đoạn này y như bản đang có -> không cần giải mã
                full_len = old_keys[key]
            else:
                clip, g = _shaped_clip(p, speed, gains.get(k), keep_n, g, meta, gaps)
                full_len = len(clip)
                if track is not None:
                    seg = min(len(clip), len(track) - st)
                    if seg > 0:
                        track[st:st + seg] += clip[:seg]
            tab.append([name, st, speed, g, keep_n, full_len] + ([gaps] if gaps else []))
            if x["fit"] == "long" and durs[k] / max(user_min.get(k, 1.0), 1.0) > x["slot"] * 1.15:
                # chỉ câu có LỜI dài hơn chỗ của chính nó (câu chỉ bị câu trước đẩy trễ thì không cần rút gọn)
                too_long.append((k, durs[k], x["slot"]))
            seg_len = min(full_len, track_len - st)
            if seg_len > 0:
                placed += 1
                a0, a1 = round(x["start"], 3), round(x["start"] + seg_len / SR, 3)
                layout.append([a0, a1, k, x["fit"]])
                s.extra["tts_file"] = str(p)   # giọng đang dùng của câu này (để lần sau chỉ đọc lại câu được chọn)
                s.extra["dub_at"] = [a0, a1]   # đoạn giọng đang phát của câu (phụ đề chạy theo giọng lồng)
                if any(q[1] > 0 for q in gaps):
                    s.extra["dub_pauses"] = sum(1 for q in gaps if q[1] > 0)
                if abs(x["shift"]) >= 0.05:
                    s.extra["fit_shift"] = round(x["shift"], 2)
                if x["over"] >= 0.05:
                    s.extra["fit_over"] = round(x["over"], 2)
            s.extra["fit"] = x["fit"]
            s.extra["fit_speed"] = round(x["speed"], 2)
            s.extra["fit_need"] = round(durs[k], 2)     # giọng ở ×1 cần bao lâu / chỗ trống của chính câu -> AI rút gọn
            s.extra["fit_have"] = round(x["slot"], 2)
            if progress and n % 20 == 0:
                progress(0.8 + 0.2 * n / max(1, len(plan)), "Đang xếp giọng đọc khớp thời gian nhân vật nói")
        if placed == 0:
            raise RuntimeError("Bản lồng tiếng tạo ra không có tiếng nào. " + "; ".join(errors[:3]))
        if patch_on:
            changed = {_gk(e) for e in tab}
            spans = [(e[1], e[1] + int(e[5])) for e in patch["placed"] if _gk(e) not in changed] + \
                    [(e[1], e[1] + int(e[5])) for e in tab if _gk(e) not in old_keys]
            regions = merge_regions(spans, n_out)
            if sum(b - a for a, b in regions) > n_out * PATCH_MAX:
                raise _NoPatch("thay đổi gần cả bản lồng")
            _patch_wav(patch["wav"], regions, tab, files_by_name(), meta, n_out)
            stats["patched"] = regions
        else:
            soft_limit(track)      # chỗ 2 giọng đọc đè lên nhau: nén mềm đỉnh, không vỡ tiếng
            write_wav(out_wav, track[:n_out])
            peaks = wave_peaks(track[:n_out], SR)
            stats["env_top"] = env_top(peaks)
            stats["wave"] = norm_peaks(peaks, stats["env_top"])
            stats.pop("patched", None)
        return tab, layout, too_long, placed

    def files_by_name() -> dict[str, str]:
        return {Path(p).name: p for p in files.values()}

    try:
        tab, layout, too_long, placed = assemble(bool(patch and patch.get("placed")))
    except _NoPatch as e:   # không vá được (file bản lồng khác, đổi gần hết…) -> dựng lại cả bản lồng như thường
        stats["patch_skipped"] = str(e)
        tab, layout, too_long, placed = assemble(False)
    meta.save()
    stats["placed_tab"] = tab
    stats["layout"] = layout
    stats["too_long"] = too_long
    stats["level_fixed"] = sum(1 for e in tab if e[3] > 1.0)
    stats["fit"] = {"ok": sum(1 for x in layout if x[3] == "ok"), "fast": sum(1 for x in layout if x[3] == "fast"),
                    "long": sum(1 for x in layout if x[3] == "long")}
    lay_segs = [segs[int(r[2])].extra for r in layout]
    lates = [e.get("fit_shift", 0) for e in lay_segs if e.get("fit_shift", 0) >= SHIFT_OK]
    stats["shift"] = {"mode": settings.get("dub_overflow", "smart"),
                      "early": sum(1 for e in lay_segs if e.get("fit_shift", 0) <= -SHIFT_OK),
                      "late": len(lates), "max_late": round(max(lates, default=0.0), 2),
                      "over": sum(1 for e in lay_segs if e.get("fit_over", 0) >= 0.1),
                      "cut": sum(1 for x in plan.values() if x["keep"] is not None)}
    stats.update({"placed": placed, "total": len(segs), "songs": n_song, "empty": n_empty,
                  "nodub": n_nodub, "errors": len(errors), "error_samples": errors[:3],
                  "redone": redone, "kept_old": kept_old, "lang_edge": n_lang, "langs": by_lang})
    if progress:
        msg = "Đã tạo xong bản lồng tiếng"
        if errors:
            msg += f" ({len(errors)} câu lỗi, đã bỏ qua)"
        progress(1.0, msg)
    return Path(patch["wav"]) if stats.get("patched") is not None else Path(out_wav)


ENV_HOP = 0.01   # sóng âm bản lồng trên timeline: đỉnh mỗi 0,01 s (trước 2.0.97: 0,05 s)


def wave_peaks(a: np.ndarray, sr: int, hop: float = ENV_HOP) -> np.ndarray:
    """Đỉnh mỗi `hop` giây (chưa chuẩn hoá)."""
    n = max(1, int(sr * hop))
    if len(a) < n:
        return np.zeros(0, np.float32)
    return np.abs(a[: len(a) // n * n].reshape(-1, n)).max(axis=1).astype(np.float32)


def env_top(peaks: np.ndarray) -> float:
    return float(np.percentile(peaks, 99.5)) if len(peaks) else 0.0


def norm_peaks(peaks: np.ndarray, top: float) -> np.ndarray:
    return np.clip(peaks / max(top, 1e-4), 0, 1).astype(np.float16)


def wave_envelope(a: np.ndarray, sr: int, hop: float = ENV_HOP) -> np.ndarray:
    """Sóng âm rút gọn (đỉnh mỗi `hop` giây, 0..1) để vẽ trên timeline."""
    peaks = wave_peaks(a, sr, hop)
    if not len(peaks):
        return np.zeros(0, np.float16)
    return norm_peaks(peaks, env_top(peaks))


PREVIEW_TEXT = {"vi": "Xin chào, đây là giọng đọc thử nghiệm của Subvideo.",
                "en": "Hello, this is a test voice from Subvideo.",
                "th": "สวัสดีค่ะ นี่คือเสียงทดสอบจาก Subvideo",
                "ja": "こんにちは、Subvideoのテスト音声です。",
                "ko": "안녕하세요, Subvideo의 테스트 음성입니다."}


def preview_voice(spec: str, text: str, out: Path, settings: dict | None = None,
                  ref_audio: str | Path | None = None, lang: str = "vi") -> Path:
    """Tạo file nghe thử 1 giọng (lời đọc theo tiếng của câu / tiếng đích `lang`)."""
    settings = settings or {}
    lang = langs.norm(lang)
    lg = langs.detect(text, lang) if (text or "").strip() else lang
    text = clean_tts_text(text, lg) or PREVIEW_TEXT[lg]
    if lg != "vi" and (spec == CLONE or spec.startswith(("vieneu:", "clone"))):
        raise ValueError(f"VieNeu chỉ đọc tiếng Việt – {langs.name(lg).lower()} hãy chọn giọng Edge hoặc OmniVoice "
                         "(khi lồng tiếng, câu không phải tiếng Việt tự đọc bằng Edge)")
    if spec.startswith("edge:"):
        spec = edge_for(spec, lg)
    elif lg == "vi":
        text = spoken_vi(text)
    if spec.startswith("edge:"):
        from . import net

        try:
            asyncio.run(_edge_save(text, spec[5:], out.with_suffix(".mp3")))
        except Exception as e:  # noqa: BLE001
            if net.is_net_error(e):
                raise RuntimeError("Không nối được tới Microsoft Edge để nghe thử (mất mạng / mạng chập chờn) – "
                                   "kiểm tra mạng rồi bấm nghe lại") from None
            raise
        return out.with_suffix(".mp3")
    if spec.startswith(("omni:", "omnid:")) or spec == OMNI_CLONE:
        om = _omni(settings)
        if spec.startswith("omnid:"):
            from . import omni_design

            ref, rtext = omni_design.ensure_anchor(om, spec, settings.get("omnivoice_model") or "k2-fsa/OmniVoice", lg)
            return om.synth(text, out.with_suffix(".wav"), ref_audio=ref, ref_text=rtext, language=lg)
        if spec.startswith("omni:"):
            from . import voices as vlib

            sm = vlib.get(spec[5:])
            if not sm:
                raise ValueError("Giọng mẫu này đã bị xoá")
            return om.synth(text, out.with_suffix(".wav"), ref_audio=str(vlib.path_of(sm)), ref_text=sm.get("text", ""),
                            language=lg)
        ref, rtext = (ref_audio if isinstance(ref_audio, tuple) else (ref_audio, ""))
        return om.synth(text, out.with_suffix(".wav"), ref_audio=str(ref) if ref else None, ref_text=rtext,
                        language=lg)
    tts = _vieneu(settings.get("vieneu_mode", "v3turbo"))
    prm = vieneu_params(settings)
    if spec == CLONE or spec.startswith("clone"):
        return synth_checked(tts, text, out.with_suffix(".wav"), ref_audio=str(ref_audio) if ref_audio else None,
                             params=prm)
    if spec.startswith((VN_SAMPLE, VN_LIB)):
        ref = vieneu_ref(spec)
        if not ref:
            raise ValueError("Giọng mẫu này không còn (đã xoá hoặc bản VieNeu không có)")
        return synth_checked(tts, text, out.with_suffix(".wav"), ref_audio=ref, params=prm)
    if spec.startswith(VN_TREND):
        return trend_synth(tts, spec, text, out.with_suffix(".wav"), prm,
                           vieneu_presets(settings.get("vieneu_mode", "v3turbo")))
    return synth_checked(tts, text, out.with_suffix(".wav"), voice=spec[7:] or None, params=prm)
