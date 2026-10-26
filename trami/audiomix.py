"""Âm thanh của video: 1 chỗ quy định cách trộn, dùng chung cho XUẤT VIDEO (video.py) và BỘ TRỘN TRỰC TIẾP trong
trình phát (nghe ngay khi kéo thanh âm lượng) -> nghe trong tool thế nào thì video xuất ra đúng như thế.

Các lớp âm thanh (edit["audio"]):
  orig  : tiếng gốc nguyên bản của video (lời Trung + nhạc) – dùng khi chưa tách / chọn nguồn "nguyên bản"
  bg    : nhạc nền + hiệu ứng đã tách (không còn giọng Trung)
  voice : giọng nói gốc đã tách (tiếng Trung); "songs" = đoạn hát luôn giữ to như bản gốc
  dub   : giọng lồng tiếng Việt (bật / tắt = edit["dub"]["enabled"])
  bgm   : nhạc thêm (file ở edit["bgm"]["path"]); "duck" = tự nhỏ xuống khi có lời lồng tiếng
  master: âm lượng tổng;  pitch: đổi cao độ giọng gốc;  mute: các lớp đang tắt
"""
from __future__ import annotations

import hashlib
import os
import subprocess
import threading
import time
from pathlib import Path

import numpy as np

SR = 44100
TRACKS = ("orig", "bg", "voice", "dub", "bgm")
DEFAULT = {"src": "auto", "orig": 1.0, "bg": 1.0, "voice": 0.0, "songs": True, "dub": 1.0, "bgm": 0.2,
           "duck": True, "pitch": 1.0, "master": 1.0, "mute": []}
DUCK_DB = -16.0                 # nhạc thêm nhỏ đi khi có lời lồng tiếng (giống sidechaincompress khi xuất)
DUCK_THR, DUCK_RELEASE = 0.02, 0.4


def audio_settings(edit: dict) -> dict:
    """Cài đặt âm thanh của dự án; dự án cũ (trước 2.0.35, chưa có edit["audio"]) được suy ra từ các khoá cũ
    (âm lượng gốc, tắt tiếng gốc, các thanh trộn ở tab Lồng tiếng, nhạc nền) để nghe y như trước."""
    a = edit.get("audio") or {}
    if not a:
        d = edit.get("dub") or {}
        bgm = edit.get("bgm") or {}
        vol = float(edit.get("volume", 1.0) if edit.get("volume") is not None else 1.0)
        on = bool(d.get("enabled"))
        a = {"src": "auto", "orig": vol * (float(d.get("original_volume", 0.15)) if on else 1.0),
             "bg": vol * float(d.get("bg_volume", 1.0)), "voice": vol * float(d.get("vocals_volume", 0.0)),
             "dub": float(d.get("dub_volume", 1.0)), "bgm": float(bgm.get("volume", 0.2)),
             "duck": bgm.get("duck", True) is not False, "pitch": float(edit.get("pitch_shift") or 1.0) or 1.0,
             "mute": ["orig", "bg", "voice"] if edit.get("mute_original") else []}
    out = {**DEFAULT, **a}
    out["mute"] = list(out.get("mute") or [])
    return out


def use_stems(a: dict, has_stems: bool, dub_on: bool) -> bool:
    """Nguồn tiếng gốc: bản đã tách (nhạc nền + giọng riêng) hay tiếng nguyên bản. "auto": có bản tách VÀ đang
    lồng tiếng thì dùng bản tách (bỏ được giọng Trung), không thì nguyên bản."""
    if not has_stems:
        return False
    return a.get("src") == "stems" or (a.get("src", "auto") == "auto" and dub_on)


def gains(a: dict, stems_on: bool, dub_on: bool, has_bgm: bool) -> dict:
    """Âm lượng thật của từng lớp (đã nhân âm lượng tổng, lớp tắt = 0). "voice_song": giọng gốc ở đoạn hát."""
    m = float(a.get("master", 1.0))
    mute = set(a.get("mute") or [])

    def v(k: str) -> float:
        return 0.0 if k in mute else max(0.0, float(a.get(k, DEFAULT[k]))) * m
    g = {k: 0.0 for k in TRACKS}
    if stems_on:
        g["bg"], g["voice"] = v("bg"), v("voice")
        g["voice_song"] = max(g["voice"], m) if a.get("songs", True) and "voice" not in mute else g["voice"]
    else:
        g["orig"] = v("orig")
    g["dub"] = v("dub") if dub_on else 0.0
    g["bgm"] = v("bgm") if has_bgm else 0.0
    return g


def pitch_filter(pitch: float, rate: int = SR) -> str:
    """Đổi cao độ giữ nguyên tốc độ (dùng cho cả xuất video lẫn bản nghe thử)."""
    if not pitch or abs(pitch - 1.0) < 1e-3:
        return ""
    return f"asetrate={rate}*{pitch:.4f},aresample={rate},atempo={1 / pitch:.4f}"


# ------------------------------------------------------------------ bộ trộn trực tiếp (trình phát trong tool)

def _track_key(src: Path, size: int, mtime: float, pre: str) -> str:
    return hashlib.md5(f"{src.resolve()}|{size}|{int(mtime)}|{pre}".encode()).hexdigest()[:12]


def _cached_track(d: Path, name: str, key: str) -> Path | None:
    """Bản giải mã có sẵn cho đúng file + bộ lọc: <lớp>_<mã>.raw, hoặc bản cũ đã được vá cho khớp (<lớp>_<mã>.ref chỉ
    tới file .raw đó – xem patch_track)."""
    out = d / f"{name}_{key}.raw"
    if out.exists() and out.stat().st_size > 0:
        return out
    ref = d / f"{name}_{key}.ref"
    try:
        t = d / ref.read_text(encoding="utf-8").strip()
        if t.suffix == ".raw" and t.exists() and t.stat().st_size > 0:
            return t
    except OSError:
        pass
    return None


def _decode_cmd(src: str | Path, pre: str, dst: str) -> list[str]:
    from . import ffmpeg as ff

    return [ff.ffmpeg_bin(), "-hide_banner", "-loglevel", "error", "-y", "-i", str(src), "-vn", "-ac", "2",
            "-ar", str(SR)] + (["-af", pre] if pre else []) + ["-f", "s16le", dst]


_DECODE_LOCKS: dict[str, threading.Lock] = {}
_LOCKS_GUARD = threading.Lock()
STALE_TMP = 3600      # file .tmp của lần giải mã bị ngắt (tắt tool giữa chừng) cũ hơn 1 giờ -> xoá


def _decode_lock(path: Path) -> threading.Lock:
    with _LOCKS_GUARD:
        return _DECODE_LOCKS.setdefault(str(path), threading.Lock())


def decode_track(src: str | Path, cache_dir: str | Path, name: str, pre: str = "") -> Path | None:
    """Giải mã 1 file âm thanh / video thành PCM 16-bit stereo 44,1 kHz (đọc lại bằng memmap, không chiếm RAM).
    Có sẵn bản giải mã đúng file + đúng bộ lọc thì dùng lại. 2 lần nạp cùng lúc (mở dự án + vừa xong 1 việc) cùng 1
    bản -> lần sau chờ lần trước giải mã xong rồi dùng luôn (trước 2.0.93 cả 2 cùng ghi 1 file .tmp -> WinError 32)."""
    from . import ffmpeg as ff

    src = Path(src)
    if not src.exists():
        return None
    pre = ",".join(x for x in (ff.audio_sync(src, SR), pre) if x)   # video gốc: theo giờ của hình (2.0.92)
    st = src.stat()
    key = _track_key(src, st.st_size, st.st_mtime, pre)
    d = Path(cache_dir)
    d.mkdir(parents=True, exist_ok=True)
    out = d / f"{name}_{key}.raw"
    with _decode_lock(out):
        hit = _cached_track(d, name, key)
        if hit:
            return hit
        tmp = d / f"{name}_{key}.{os.getpid()}_{threading.get_ident()}.tmp"
        r = subprocess.run(_decode_cmd(src, pre, str(tmp)), capture_output=True, creationflags=ff._NO_WINDOW)
        if r.returncode != 0 or not tmp.exists() or tmp.stat().st_size == 0:
            tmp.unlink(missing_ok=True)
            return None
        try:
            ff.replace_file(tmp, out)
        except OSError:
            tmp.unlink(missing_ok=True)
            if not (out.exists() and out.stat().st_size > 0):
                raise
    now = time.time()
    for old in list(d.glob(f"{name}_*.raw")) + list(d.glob(f"{name}_*.ref")) + list(d.glob(f"{name}_*.tmp")):
        try:            # bản cũ của lớp này (file / bộ lọc đã đổi); đang được phát (memmap) thì để lần sau
            if old != out and not (old.suffix == ".tmp" and now - old.stat().st_mtime < STALE_TMP):
                old.unlink()    # .tmp mới = lần giải mã khác đang chạy -> để nguyên
        except OSError:
            pass
    return out


def patch_track(src: str | Path, cache_dir: str | Path, name: str, pre: str, old_stat, regions) -> bool:
    """File nguồn (wav PCM 16-bit) vừa được ghi lại vài đoạn (regions: [(mẫu đầu, mẫu cuối)] theo tần số của file) ->
    vá đúng các đoạn đó trong bản giải mã đã có (giải mã lại từng đoạn có đệm 0,1 s 2 bên – khớp từng mẫu với giải mã cả
    file) rồi ghi <lớp>_<mã mới>.ref chỉ tới nó. Trình phát đang phát bản này (memmap) nghe thay đổi ngay; lần nạp sau
    không phải giải mã lại cả phim. False = chưa có bản giải mã cũ (nạp lần sau giải mã cả file như thường)."""
    import wave
    from math import gcd

    from . import ffmpeg as ff
    from .tts import wav_info

    src, d = Path(src), Path(cache_dir)
    raw = _cached_track(d, name, _track_key(src, old_stat.st_size, old_stat.st_mtime, pre))
    if raw is None:
        return False
    off, frames, sr, ch, sw = wav_info(src)
    if sw != 2:
        return False
    k = gcd(sr, SR)
    u, v = sr // k, SR // k          # u mẫu nguồn <-> v khung 44,1 kHz (24 kHz: 80 <-> 147)
    m = u * max(1, int(0.1 * sr) // u)
    nraw = raw.stat().st_size // 4
    tmp = d / f"{name}_patch.wav"
    with open(src, "rb") as fs, open(raw, "r+b") as fr:
        for r0, r1 in regions:
            # bộ lấy mẫu lại trộn vài chục mẫu 2 bên -> ghi rộng hơn đoạn đổi 0,05 s, giải mã rộng thêm 0,1 s nữa
            a0, a1 = max(0, (r0 - m // 2) // u * u), min(frames, -(-(r1 + m // 2) // u) * u)
            i0, i1 = max(0, a0 - m), min(frames, a1 + m)
            if a1 <= a0:
                continue
            fs.seek(off + i0 * ch * 2)
            with wave.open(str(tmp), "wb") as w:
                w.setnchannels(ch)
                w.setsampwidth(2)
                w.setframerate(sr)
                w.writeframes(fs.read((i1 - i0) * ch * 2))
            r = subprocess.run(_decode_cmd(tmp, pre, "pipe:1"), capture_output=True, creationflags=ff._NO_WINDOW)
            if r.returncode != 0:
                raise RuntimeError(r.stderr.decode("utf-8", "replace")[-300:])
            pcm = np.frombuffer(r.stdout, dtype=np.int16).reshape(-1, 2)
            o0, dst = (a0 - i0) * v // u, a0 * v // u
            n = min((a1 - a0) * v // u, len(pcm) - o0, nraw - dst)
            if n > 0:
                fr.seek(dst * 4)
                fr.write(pcm[o0:o0 + n].tobytes())
    tmp.unlink(missing_ok=True)
    st = src.stat()
    key = _track_key(src, st.st_size, st.st_mtime, pre)
    if not raw.name.endswith(f"_{key}.raw"):
        (d / f"{name}_{key}.ref").write_text(raw.name, encoding="utf-8")
    return True


def load_raw(path: str | Path) -> np.ndarray:
    return np.memmap(str(path), dtype=np.int16, mode="r").reshape(-1, 2)


ENV_BLOCK = 60.0   # giây mỗi khối khi đo độ to bản lồng (không nạp cả bản vào RAM)


def dub_envelope(dub: np.ndarray | None, hop_s: float = 0.05) -> np.ndarray | None:
    """Độ to giọng lồng (đỉnh 0..1) mỗi `hop_s` giây, đọc từng khối ENV_BLOCK giây của PCM int16 [khung, 2]."""
    if dub is None or not len(dub):
        return None
    hop = int(SR * hop_s)
    n = len(dub) // hop
    out = np.zeros(n, np.float32)
    blk = hop * max(1, int(ENV_BLOCK / hop_s))
    for a in range(0, n * hop, blk):
        b = min(n * hop, a + blk)
        x = np.abs(np.asarray(dub[a:b, 0], dtype=np.int32))    # int32: |−32768| không tràn
        out[a // hop:b // hop] = x.reshape(-1, hop).max(axis=1) / 32768.0
    return out


class LiveMix:
    """Trộn các lớp âm thanh theo thời gian video, từng khối nhỏ (~20–50 ms) ngay lúc phát: đổi âm lượng / tắt lớp /
    nghe riêng có tác dụng ngay ở khối kế tiếp. Không phụ thuộc Qt (kiểm thử được)."""

    def __init__(self):
        self.tracks: dict[str, np.ndarray] = {}   # tên lớp -> PCM int16 [khung, 2] theo thời gian video gốc
        self.g: dict[str, float] = {}             # âm lượng từng lớp (gains())
        self.songs: list = []                     # đoạn hát (giây) – giọng gốc giữ to
        self.keep: list = []                      # đoạn giữ lại sau khi cắt (giây) – nhạc thêm / mờ dần theo đó
        self.fade = (0.0, 0.0)
        self.duck = False
        self.mute: list = []                      # đoạn tắt tiếng gốc (orig / bg / voice), giây video gốc
        self.solo: str | None = None              # "nghe riêng" 1 lớp (chỉ khi xem trước, không lưu)
        self.rate = 1.0                           # tốc độ phát
        self._dub_env = None                      # độ to giọng lồng mỗi 0,05 s (để giảm nhạc thêm)
        self._duck_cs = None                      # (âm lượng lồng tiếng, tổng tích luỹ các khối có lời)

    # ---- cấu hình
    def set_tracks(self, tracks: dict[str, np.ndarray]):
        self.tracks = dict(tracks)
        # độ to giọng lồng chỉ dùng để giảm nhạc thêm khi có lời -> chỉ tính khi có lớp nhạc thêm (2.1.10: trước tính
        # mọi lần chuẩn bị âm thanh = đọc cả bản lồng 1,4 GB / phim 2 giờ + cấp thêm 1,4 GB RAM, ~10 s)
        self._dub_env = dub_envelope(self.tracks.get("dub")) if "bgm" in self.tracks else None
        self._duck_cs = None

    def duration(self) -> float:
        return max((len(t) for t in self.tracks.values()), default=0) / SR

    # ---- thời gian sau khi cắt (nhạc thêm + mờ dần tính theo video XUẤT RA)
    def _out_time(self, t: np.ndarray) -> np.ndarray:
        if not self.keep:
            return t
        out = np.zeros_like(t)
        acc = 0.0
        for a, b in self.keep:
            inside = (t >= a) & (t < b)
            out[inside] = acc + (t[inside] - a)
            out[t >= b] = acc + (b - a)
            acc += b - a
        return out

    def _total_out(self) -> float:
        return sum(b - a for a, b in self.keep) if self.keep else self.duration()

    def render(self, start: int, n: int) -> tuple[np.ndarray, int]:
        """n khung stereo float32 bắt đầu từ khung `start` của video gốc. Trả về (âm thanh, số khung video đã đi
        qua) – khác n khi phát nhanh / chậm."""
        span = max(1, int(round(n * self.rate)))
        acc = np.zeros((span, 2), np.float32)
        t = (start + np.arange(span)) / SR
        for name, arr in self.tracks.items():
            if self.solo and name != self.solo:
                continue
            gain = self.g.get(name, 0.0)
            if name == "voice":
                gs = self.g.get("voice_song", gain)
                if gain <= 0 and gs <= 0:
                    continue
            elif gain <= 0:
                continue
            if name == "bgm":
                if not len(arr):
                    continue
                ot = self._out_time(t)
                idx = (np.round(ot * SR).astype(np.int64)) % len(arr)
                part = arr[idx].astype(np.float32)
                if self.duck and self._dub_env is not None and self.g.get("dub", 0) > 0 and self.solo != "bgm":
                    part *= self._duck_curve(t)[:, None]
            else:
                a, b = start, min(len(arr), start + span)
                if a >= b:
                    continue
                part = np.zeros((span, 2), np.float32)
                part[: b - a] = arr[a:b]
            gv = None
            if name == "voice" and self.songs:
                gv = np.full(span, gain, np.float32)
                for s0, s1 in self.songs:
                    gv[(t >= s0) & (t < s1)] = gs
            if name in ORIGINAL and self.mute:
                m = mute_mask(t, self.mute)
                if m is not None:
                    gv = (gv if gv is not None else np.full(span, gain, np.float32)) * m
            if gv is not None:
                acc += part * (gv[:, None] / 32768.0)
            else:
                acc += part * (gain / 32768.0)
        fi, fo = self.fade
        if fi > 0 or fo > 0:
            ot = self._out_time(t)
            env = np.ones(span, np.float32)
            if fi > 0:
                env *= np.clip(ot / fi, 0, 1)
            if fo > 0:
                env *= np.clip((self._total_out() - ot) / fo, 0, 1)
            acc *= env[:, None]
        if span != n:                               # phát nhanh / chậm: co giãn khối cho vừa n khung
            src = np.linspace(0, span - 1, n)
            acc = np.stack([np.interp(src, np.arange(span), acc[:, c]) for c in (0, 1)], axis=1).astype(np.float32)
        peak = float(np.abs(acc).max()) if len(acc) else 0.0
        if peak > 0.95:                             # chặn đỉnh như alimiter khi xuất
            acc *= 0.95 / peak
        return acc, span

    def _duck_curve(self, t: np.ndarray) -> np.ndarray:
        """Hệ số nhạc thêm: có lời lồng tiếng trong 0,4 s vừa qua -> giảm DUCK_DB. Tổng tích luỹ tính 1 lần theo âm
        lượng lồng tiếng (trước: tính lại trên cả phim ở MỖI khối âm thanh ~20 ms của luồng phát)."""
        hop = 0.05
        gd = self.g.get("dub", 1.0)
        if self._duck_cs is None or self._duck_cs[0] != gd:
            active = (self._dub_env * gd) > DUCK_THR
            self._duck_cs = (gd, np.concatenate([[0], np.cumsum(active)]))
        cs = self._duck_cs[1]
        n = len(cs) - 1
        k = (t / hop).astype(np.int64)
        look = int(DUCK_RELEASE / hop)
        hi = np.clip(k + 1, 0, n)
        lo = np.clip(k - look, 0, n)
        on = (cs[hi] - cs[lo]) > 0
        return np.where(on, 10 ** (DUCK_DB / 20), 1.0).astype(np.float32)


ORIGINAL = ("orig", "bg", "voice")      # các lớp "tiếng gốc" (tắt theo đoạn trên timeline)
MUTE_RAMP = 0.015


def mute_mask(t: np.ndarray, spans: list) -> np.ndarray | None:
    """Hệ số 0..1 cho các thời điểm t (giây): 0 trong đoạn tắt tiếng, vào / ra dần 15 ms cho khỏi tiếng "bụp"."""
    m = None
    for a, b in spans:
        a, b = float(a), float(b)
        if b <= t[0] - MUTE_RAMP or a >= t[-1] + MUTE_RAMP:
            continue
        d = np.minimum(t - a, b - t)
        w = np.clip(1.0 - (d + MUTE_RAMP) / MUTE_RAMP, 0.0, 1.0).astype(np.float32)   # 1 ngoài đoạn, 0 bên trong
        w[d < -MUTE_RAMP] = 1.0
        m = w if m is None else np.minimum(m, w)
    return m


def merge_spans(spans: list) -> list[list[float]]:
    """Gộp các đoạn [a, b] chồng / sát nhau."""
    out: list[list[float]] = []
    for a, b in sorted((float(a), float(b)) for a, b in spans if float(b) > float(a)):
        if out and a <= out[-1][1] + 0.01:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([round(a, 3), round(b, 3)])
    return out


def subtract_spans(spans: list, cut: list) -> list[list[float]]:
    """Các đoạn trong spans trừ đi các đoạn trong cut."""
    out = [[float(a), float(b)] for a, b in spans]
    for c0, c1 in cut:
        nxt = []
        for a, b in out:
            if c1 <= a or c0 >= b:
                nxt.append([a, b])
                continue
            if a < c0 - 0.01:
                nxt.append([a, c0])
            if c1 < b - 0.01:
                nxt.append([c1, b])
        out = nxt
    return [[round(a, 3), round(b, 3)] for a, b in out]


def mute_expr(spans: list, offset: float = 0.0) -> str:
    """Biểu thức FFmpeg (theo t của luồng) cho các đoạn tắt tiếng gốc; "" nếu không có."""
    parts = [f"between(t,{float(a) - offset:.3f},{float(b) - offset:.3f})" for a, b in spans
             if float(b) - offset > 0]
    return "+".join(parts)


def to_pcm16(a: np.ndarray) -> bytes:
    return (np.clip(a, -1.0, 1.0) * 32767).astype("<i2").tobytes()
