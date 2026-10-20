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
import subprocess
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

def decode_track(src: str | Path, cache_dir: str | Path, name: str, pre: str = "") -> Path | None:
    """Giải mã 1 file âm thanh / video thành PCM 16-bit stereo 44,1 kHz (đọc lại bằng memmap, không chiếm RAM).
    Có sẵn bản giải mã đúng file + đúng bộ lọc thì dùng lại."""
    from . import ffmpeg as ff

    src = Path(src)
    if not src.exists():
        return None
    st = src.stat()
    key = hashlib.md5(f"{src.resolve()}|{st.st_size}|{int(st.st_mtime)}|{pre}".encode()).hexdigest()[:12]
    d = Path(cache_dir)
    d.mkdir(parents=True, exist_ok=True)
    out = d / f"{name}_{key}.raw"
    if out.exists() and out.stat().st_size > 0:
        return out
    tmp = out.with_suffix(".tmp")
    cmd = [ff.ffmpeg_bin(), "-hide_banner", "-loglevel", "error", "-y", "-i", str(src), "-vn", "-ac", "2",
           "-ar", str(SR)] + (["-af", pre] if pre else []) + ["-f", "s16le", str(tmp)]
    r = subprocess.run(cmd, capture_output=True, creationflags=ff._NO_WINDOW)
    if r.returncode != 0 or not tmp.exists() or tmp.stat().st_size == 0:
        tmp.unlink(missing_ok=True)
        return None
    tmp.replace(out)
    for old in d.glob(f"{name}_*.raw"):          # bản giải mã cũ của lớp này (file / bộ lọc đã đổi)
        if old != out:
            try:
                old.unlink()
            except OSError:
                pass
    return out


def load_raw(path: str | Path) -> np.ndarray:
    return np.memmap(str(path), dtype=np.int16, mode="r").reshape(-1, 2)


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

    # ---- cấu hình
    def set_tracks(self, tracks: dict[str, np.ndarray]):
        self.tracks = dict(tracks)
        dub = self.tracks.get("dub")
        if dub is not None and len(dub):
            hop = int(SR * 0.05)
            n = len(dub) // hop
            x = np.abs(dub[: n * hop, 0].astype(np.float32)).reshape(n, hop).max(axis=1) / 32768.0
            self._dub_env = x
        else:
            self._dub_env = None

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
        """Hệ số nhạc thêm: có lời lồng tiếng trong 0,4 s vừa qua -> giảm DUCK_DB."""
        env = self._dub_env
        hop = 0.05
        k = (t / hop).astype(np.int64)
        look = int(DUCK_RELEASE / hop)
        active = (env * self.g.get("dub", 1.0)) > DUCK_THR
        cs = np.concatenate([[0], np.cumsum(active)])
        hi = np.clip(k + 1, 0, len(active))
        lo = np.clip(k - look, 0, len(active))
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
