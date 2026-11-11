"""Ảnh khung từng câu gửi kèm khi dịch bằng AI xem được ảnh (2.4.11 – Cài đặt `translate_frames`).

Mỗi câu 1 khung hình chụp lúc nhân vật đang nói (`at`: 40 % thời lượng câu – miệng đang mở, chưa chuyển cảnh), thu nhỏ
`FRAME_W` px, JPEG nhẹ -> AI (Gemini / OpenAI / Claude / Ollama mô hình ảnh) thấy ai đang nói, nam / nữ, nói với ai, bối
cảnh mà chọn xưng hô cho dòng không ghi rõ đại từ. Ảnh đệm ở work/aiframes/ theo video + giây (0,1 s) -> dịch lại / dịch
câu đã tích / lần sau dùng lại ảnh đã chụp, không chụp lại.
"""
from __future__ import annotations

import hashlib
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

FRAME_W = 448            # ảnh rộng tối đa 448 px (đủ thấy mặt / miệng, ~100–250 token mỗi ảnh tuỳ AI)
JPEG_Q = 6               # chất lượng JPEG của FFmpeg (2 = đẹp nhất, 31 = xấu nhất)
WORKERS = 4              # số FFmpeg chụp cùng lúc


class FrameSource:
    def __init__(self, src: str | Path, work: str | Path):
        self.src = Path(src)
        self.dir = Path(work)
        self.dir.mkdir(parents=True, exist_ok=True)
        st = self.src.stat()
        self._sig = hashlib.md5(f"{self.src.resolve()}|{st.st_size}|{int(st.st_mtime)}".encode()).hexdigest()[:8]
        self._lock = threading.Lock()
        self.grabbed = 0        # số ảnh chụp mới (không tính ảnh đệm dùng lại)
        self.reused = 0

    @staticmethod
    def at(t0: float, t1: float) -> float:
        """Giây chụp ảnh của câu [t0, t1]."""
        return max(0.0, t0 + 0.4 * max(0.0, t1 - t0))

    def path(self, t: float) -> Path:
        return self.dir / f"f_{self._sig}_{int(round(t * 10))}.jpg"

    def _grab(self, t: float) -> bytes:
        from .ffmpeg import ffmpeg_bin

        cmd = [ffmpeg_bin(), "-hide_banner", "-loglevel", "error", "-ss", f"{max(0.0, t):.3f}", "-i",
               str(self.src.resolve()), "-frames:v", "1", "-an", "-vf", f"scale='min(iw,{FRAME_W})':-2",
               "-q:v", str(JPEG_Q), "-f", "image2pipe", "-vcodec", "mjpeg", "pipe:1"]
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        return subprocess.run(cmd, capture_output=True, creationflags=flags, timeout=60).stdout

    def get(self, t: float) -> bytes | None:
        """Ảnh JPEG lúc t (có sẵn thì đọc file đệm, chưa có thì chụp + lưu). Không chụp được -> None."""
        p = self.path(t)
        try:
            if p.exists() and p.stat().st_size > 0:
                with self._lock:
                    self.reused += 1
                return p.read_bytes()
        except OSError:
            pass
        try:
            data = self._grab(t)
        except (OSError, subprocess.SubprocessError):
            return None
        if not data:
            return None
        try:
            tmp = p.with_name(p.name + f".{threading.get_ident()}.tmp")
            tmp.write_bytes(data)
            tmp.replace(p)
        except OSError:
            pass
        with self._lock:
            self.grabbed += 1
        return data

    def prefetch(self, times: list[float], progress=None, cancel=None) -> None:
        """Chụp trước ảnh các câu sắp dịch (WORKERS FFmpeg cùng lúc) – ảnh đã có thì bỏ qua."""
        from .ffmpeg import Cancelled

        need = [t for t in times if not self.path(t).exists()]
        if not need:
            return
        with ThreadPoolExecutor(max_workers=WORKERS) as ex:
            futs = [ex.submit(self.get, t) for t in need]
            for k, f in enumerate(futs):
                if cancel and cancel():
                    for g in futs:
                        g.cancel()
                    raise Cancelled()
                f.result()
                if progress and k % 10 == 0:
                    progress(k / len(futs), f"Chụp ảnh khung từng câu cho AI {k + 1}/{len(futs)}…")

    @staticmethod
    def drop_stale(work: str | Path, sig_keep: str = "") -> None:
        for p in Path(work).glob("f_*.jpg"):
            if not sig_keep or not p.name.startswith(f"f_{sig_keep}_"):
                p.unlink(missing_ok=True)
