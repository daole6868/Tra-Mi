"""Tiện ích gọi FFmpeg: tìm binary, đọc thông tin video, chạy lệnh có tiến trình."""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Callable

ProgressCB = Callable[[float, str], None]  # (0..1, thông điệp)

_FFMPEG: str | None = None
_FFPROBE: str | None = None

_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0  # ẩn cửa sổ console trên Windows


class Cancelled(Exception):
    """Người dùng đã huỷ tác vụ."""


class FFmpegError(RuntimeError):
    pass


def ffmpeg_bin() -> str:
    global _FFMPEG
    if _FFMPEG:
        return _FFMPEG
    cand = os.environ.get("TRAMI_FFMPEG")
    from .config import APP_DIR

    local = APP_DIR / "bin" / ("ffmpeg.exe" if os.name == "nt" else "ffmpeg")
    for c in (cand, str(local) if local.exists() else None, shutil.which("ffmpeg")):
        if c:
            _FFMPEG = c
            return c
    try:
        import imageio_ffmpeg

        _FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()
        return _FFMPEG
    except Exception as e:  # pragma: no cover
        raise FFmpegError(
            "Không tìm thấy FFmpeg. Hãy cài FFmpeg hoặc chạy lại file cài đặt (pip install imageio-ffmpeg)."
        ) from e


def ffprobe_bin() -> str | None:
    global _FFPROBE
    if _FFPROBE is not None:
        return _FFPROBE or None
    ff = Path(ffmpeg_bin())
    sib = ff.with_name(ff.name.replace("ffmpeg", "ffprobe"))
    if sib.exists() and sib != ff:
        _FFPROBE = str(sib)
    else:
        _FFPROBE = shutil.which("ffprobe") or ""
    return _FFPROBE or None


_NVENC_OK: dict = {}


def encoder_works(name: str) -> bool:
    """Thử encode 1 khung hình để chắc chắn GPU encoder hoạt động (có driver/GPU)."""
    if name in _NVENC_OK:
        return _NVENC_OK[name]
    ok = False
    if has_encoder(name):
        try:
            r = subprocess.run([ffmpeg_bin(), "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
                                "color=black:s=256x256:d=0.1", "-frames:v", "1", "-c:v", name, "-f", "null", "-"],
                               capture_output=True, creationflags=_NO_WINDOW, timeout=30)
            ok = r.returncode == 0
        except Exception:
            ok = False
    _NVENC_OK[name] = ok
    return ok


def has_encoder(name: str) -> bool:
    try:
        out = subprocess.run([ffmpeg_bin(), "-hide_banner", "-encoders"], capture_output=True, text=True,
                             encoding="utf-8", errors="replace", creationflags=_NO_WINDOW, timeout=20).stdout or ""
        return f" {name} " in out
    except Exception:
        return False


@dataclass
class MediaInfo:
    path: str
    duration: float = 0.0
    width: int = 0
    height: int = 0
    fps: float = 25.0
    has_audio: bool = False
    has_video: bool = False
    vcodec: str = ""
    acodec: str = ""
    rotation: int = 0

    def to_dict(self) -> dict:
        return asdict(self)


def _parse_rate(r: str) -> float:
    try:
        if "/" in r:
            a, b = r.split("/")
            return float(a) / float(b) if float(b) else 0.0
        return float(r)
    except Exception:
        return 0.0


def probe(path: str | Path) -> MediaInfo:
    path = str(path)
    info = MediaInfo(path=path)
    fp = ffprobe_bin()
    if fp:
        r = subprocess.run([fp, "-v", "error", "-print_format", "json", "-show_format", "-show_streams", path],
                           capture_output=True, text=True, encoding="utf-8", errors="replace",
                           creationflags=_NO_WINDOW)
        if r.returncode == 0:
            d = json.loads(r.stdout or "{}")
            info.duration = float(d.get("format", {}).get("duration") or 0)
            for s in d.get("streams", []):
                if s.get("codec_type") == "video" and not info.has_video:
                    if (s.get("disposition") or {}).get("attached_pic"):
                        continue
                    info.has_video = True
                    info.width, info.height = int(s.get("width") or 0), int(s.get("height") or 0)
                    info.fps = _parse_rate(s.get("avg_frame_rate") or "") or _parse_rate(s.get("r_frame_rate") or "") or 25.0
                    info.vcodec = s.get("codec_name", "")
                    rot = (s.get("tags") or {}).get("rotate")
                    for sd in s.get("side_data_list") or []:
                        if "rotation" in sd:
                            rot = sd["rotation"]
                    try:
                        info.rotation = int(float(rot or 0)) % 360
                    except Exception:
                        pass
                    if not info.duration and s.get("duration"):
                        info.duration = float(s["duration"])
                elif s.get("codec_type") == "audio" and not info.has_audio:
                    info.has_audio = True
                    info.acodec = s.get("codec_name", "")
            if info.rotation in (90, 270):
                info.width, info.height = info.height, info.width
            return info
    # Dự phòng: đọc stderr của ffmpeg -i
    r = subprocess.run([ffmpeg_bin(), "-hide_banner", "-i", path], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", creationflags=_NO_WINDOW)
    txt = r.stderr
    m = re.search(r"Duration: (\d+):(\d+):(\d+(?:\.\d+)?)", txt)
    if m:
        info.duration = int(m[1]) * 3600 + int(m[2]) * 60 + float(m[3])
    m = re.search(r"Stream #.*Video: (\w+).*?, (\d{2,5})x(\d{2,5})", txt)
    if m:
        info.has_video, info.vcodec = True, m[1]
        info.width, info.height = int(m[2]), int(m[3])
    m = re.search(r"([\d.]+) fps", txt)
    if m:
        info.fps = float(m[1])
    m = re.search(r"Stream #.*Audio: (\w+)", txt)
    if m:
        info.has_audio, info.acodec = True, m[1]
    return info


def run(args: list[str], duration: float = 0.0, progress: ProgressCB | None = None,
        cancel: Callable[[], bool] | None = None, msg: str = "Đang xử lý video", cwd: str | Path | None = None) -> None:
    """Chạy ffmpeg với -progress để báo tiến độ. Ném FFmpegError nếu lỗi."""
    for i, a in enumerate(args[:-1]):   # file vào không còn (bị chuyển chỗ / đổi tên / xoá): báo dễ hiểu
        src = args[i + 1]
        if a == "-i" and os.path.isabs(src) and "://" not in src and not os.path.exists(src):
            raise FFmpegError(f"Không tìm thấy file: {src}\nFile đã bị chuyển chỗ, đổi tên hoặc xoá. Nếu đây là video gốc "
                              "của dự án: bấm 'Tìm lại video gốc…' ở thanh trên cùng của dự án.")
    cmd = [ffmpeg_bin(), "-hide_banner", "-y", "-nostdin", "-progress", "pipe:1", "-stats_period", "0.5"] + args
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                            encoding="utf-8", errors="replace", creationflags=_NO_WINDOW,
                            cwd=str(cwd) if cwd else None)
    err_tail: list[str] = []

    import threading

    def _drain():
        for line in proc.stderr:  # type: ignore[union-attr]
            err_tail.append(line)
            if len(err_tail) > 60:
                del err_tail[:20]

    t = threading.Thread(target=_drain, daemon=True)
    t.start()
    try:
        for line in proc.stdout:  # type: ignore[union-attr]
            if cancel and cancel():
                proc.kill()
                raise Cancelled()
            if progress and duration > 0 and line.startswith("out_time_us="):
                try:
                    us = int(line.split("=", 1)[1])
                    progress(max(0.0, min(1.0, us / 1e6 / duration)), msg)
                except ValueError:
                    pass
        proc.wait()
    finally:
        if proc.poll() is None:
            proc.kill()
    t.join(timeout=2)
    if proc.returncode != 0:
        raise FFmpegError("FFmpeg lỗi:\n" + "".join(err_tail[-25:]))
    if progress:
        progress(1.0, msg)


# ------------------------------------------------------------------ âm thanh theo đúng giờ của video
# FFmpeg mặc định ghi các mẫu âm thanh NỐI LIỀN, bỏ qua giờ (timestamp) của luồng âm thanh. Video dài có giờ âm thanh
# lệch dần (livestream / ghép nhiều phần / đồng hồ thu âm lệch: số mẫu nhiều hoặc ít hơn giờ ghi trong file) thì mọi thứ
# tính từ bản giải mã (tách giọng, nghe, giọng lồng, tiếng gốc khi phát trong tool) khớp nhau nhưng TRÔI DẦN so với hình
# – trình phát xếp hình theo giờ. aresample async: đệm lặng / bỏ / co giãn nhẹ mẫu cho khớp giờ; first_pts=0: âm thanh
# bắt đầu muộn hơn hình thì đệm lặng ở đầu. TIMELINE = phiên bản cách giải mã (dự án cũ hơn -> pipeline.retime).
TIMELINE = 2
SYNC_MAX = 0.15      # lệch ≤ 0,15 s coi như khớp (không cần căn lại giờ dự án cũ)


def audio_sync(src: str | Path, sr: int | None = None) -> str:
    """Bộ lọc giải mã âm thanh theo giờ của file (ra `sr` Hz, mặc định 44,1 kHz). File .wav (tool tự ghi, mẫu liền =
    giờ) -> ''. Qua tần số trung gian 44101 Hz: tần số vào = ra thì bộ lấy mẫu lại không co giãn mềm được (đo: vẫn trễ
    0,1–0,4 s), chỉ bỏ / đệm từng 0,1 s; khác tần số -> co giãn mềm, khớp giờ ±0,02 s."""
    if Path(src).suffix.lower() == ".wav":
        return ""
    return f"aresample=44101:async=1000:first_pts=0,aresample={sr or 44100}"


def af_args(*filters: str) -> list[str]:
    """["-af", "a,b"] từ các bộ lọc khác rỗng (không có thì [])."""
    fs = [x for x in filters if x]
    return ["-af", ",".join(fs)] if fs else []


def audio_timeline(src: str | Path):
    """Giờ của âm thanh so với số mẫu: (c, p) – c = giây tính theo số mẫu đã giải mã (cách cũ, mẫu nối liền) ở đầu mỗi
    khung âm thanh, p = giờ thật của khung đó trên dòng thời gian của video (0 = đầu file). None = không đọc được.
    Phim 3 giờ ~4 s (ffprobe đọc giờ + số mẫu từng khung; không có ffprobe thì FFmpeg ashowinfo, chậm hơn)."""
    import numpy as np

    fp = ffprobe_bin()
    pts: list[float] = []
    ns: list[int] = []
    sr = 0
    if fp:
        meta = subprocess.run([fp, "-v", "error", "-select_streams", "a:0", "-show_entries",
                               "stream=sample_rate:format=start_time", "-of", "json", str(src)],
                              capture_output=True, text=True, encoding="utf-8", errors="replace",
                              creationflags=_NO_WINDOW)
        try:
            d = json.loads(meta.stdout or "{}")
            sr = int((d.get("streams") or [{}])[0].get("sample_rate") or 0)
            t0 = float((d.get("format") or {}).get("start_time") or 0)
        except (ValueError, IndexError):
            sr, t0 = 0, 0.0
        r = subprocess.run([fp, "-v", "error", "-select_streams", "a:0", "-show_entries", "frame=pts_time,nb_samples",
                            "-of", "csv=p=0", str(src)], capture_output=True, text=True, encoding="utf-8",
                           errors="replace", creationflags=_NO_WINDOW)
        for line in r.stdout.splitlines():
            parts = line.split(",")
            try:
                pts.append(float(parts[0]) - t0)
                ns.append(int(parts[1]))
            except (ValueError, IndexError):
                continue
    if not pts or not sr:
        pts, ns = [], []
        r = subprocess.run([ffmpeg_bin(), "-hide_banner", "-nostdin", "-i", str(src), "-vn", "-map", "0:a:0",
                            "-af", "ashowinfo", "-f", "null", "-"], capture_output=True, text=True, encoding="utf-8",
                           errors="replace", creationflags=_NO_WINDOW)
        for m in re.finditer(r"pts_time:\s*(-?[\d.]+).*?rate:\s*(\d+).*?nb_samples:\s*(\d+)", r.stderr):
            pts.append(float(m[1]))
            sr = int(m[2])
            ns.append(int(m[3]))
    if len(pts) < 2 or not sr:
        return None
    n = np.asarray(ns, np.float64)
    c = np.concatenate([[0.0], np.cumsum(n)[:-1]]) / sr
    return c, np.asarray(pts, np.float64), n / sr


def timeline_drift(src: str | Path) -> dict | None:
    """Âm thanh lệch giờ bao nhiêu so với hình nếu giải mã nối liền (cách trước 2.0.92): {"max": lệch lớn nhất (s),
    "end": lệch ở cuối (âm = mốc tính từ âm thanh bị TRỄ so với hình), "start": âm thanh bắt đầu muộn (s)}."""
    import numpy as np

    tl = audio_timeline(src)
    if tl is None:
        return None
    c, p, _ = tl
    d = p - c
    return {"max": round(float(np.max(np.abs(d))), 3), "end": round(float(np.median(d[-50:])), 3),
            "start": round(float(np.median(d[:50])), 3)}


def extract_audio(src: str | Path, dst: str | Path, sr: int = 16000, mono: bool = True,
                  progress: ProgressCB | None = None, cancel=None) -> Path:
    info = probe(src)
    if not info.has_audio:
        raise FFmpegError("Video không có âm thanh.")
    args = ["-i", str(src), "-vn"] + af_args(audio_sync(src, sr)) + ["-ar", str(sr)]
    if mono:
        args += ["-ac", "1"]
    args += ["-c:a", "pcm_s16le", str(dst)]
    run(args, info.duration, progress, cancel, "Đang tách âm thanh")
    return Path(dst)


def snapshot(src: str | Path, t: float, dst: str | Path, width: int = 0) -> Path:
    args = [ffmpeg_bin(), "-hide_banner", "-y", "-ss", f"{max(0.0, t):.3f}", "-i", str(src), "-frames:v", "1"]
    if width:
        args += ["-vf", f"scale={width}:-2"]
    args += ["-q:v", "3", "-update", "1", str(dst)]
    Path(dst).parent.mkdir(parents=True, exist_ok=True)
    r = subprocess.run(args, capture_output=True, text=True, encoding="utf-8", errors="replace",
                       creationflags=_NO_WINDOW)
    if r.returncode != 0 or not Path(dst).exists():
        raise FFmpegError("Không chụp được khung hình: " + r.stderr[-400:])
    return Path(dst)


def video_encode_args(settings: dict, crf: int | None = None) -> list[str]:
    codec = settings.get("video_codec", "libx264")
    crf = settings.get("video_crf", 20) if crf is None else crf
    preset = settings.get("video_preset", "medium")
    if codec in ("h264_nvenc", "hevc_nvenc"):
        if not encoder_works(codec):
            codec = "libx264"
        else:
            return ["-c:v", codec, "-preset", "p5", "-cq", str(crf), "-b:v", "0", "-pix_fmt", "yuv420p"]
    if codec == "libx265":
        return ["-c:v", "libx265", "-crf", str(crf), "-preset", preset, "-pix_fmt", "yuv420p", "-tag:v", "hvc1"]
    return ["-c:v", "libx264", "-crf", str(crf), "-preset", preset, "-pix_fmt", "yuv420p"]


def audio_encode_args(settings: dict) -> list[str]:
    return ["-c:a", "aac", "-b:a", settings.get("audio_bitrate", "192k")]
