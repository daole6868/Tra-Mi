"""Xoá phụ đề cứng bằng inpainting (tái tạo nền phía sau chữ) - không để lại vệt mờ/hộp đen.

Cách làm cho từng khung hình:
  1. Lấy vùng phụ đề (người dùng chọn hoặc tự dò).
  2. Tạo mặt nạ nét chữ: chữ sáng/vàng có viền tối -> lọc theo độ sáng + biên cạnh.
     Nếu đã OCR trước đó thì chỉ xoá trong khung chữ OCR tìm được (chính xác hơn, tránh xoá nhầm).
  3. cv2.inpaint (Telea) tái tạo nền, rồi ghép lại -> FFmpeg encode, giữ nguyên âm thanh.
"""
from __future__ import annotations

import bisect
import json
import subprocess
import threading
from pathlib import Path
from typing import Callable

import numpy as np

from . import ffmpeg as ff


def text_mask(roi: np.ndarray, dilate: int = 5) -> np.ndarray:
    """Mặt nạ nét chữ phụ đề: lõi chữ sáng (trắng/vàng) + viền tối bao quanh."""
    import cv2

    hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
    gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
    v, s = hsv[..., 2], hsv[..., 1]
    hue = hsv[..., 0]
    white = (v > 185) & (s < 70)
    yellow = (v > 160) & (s > 90) & (hue > 15) & (hue < 40)
    # chỉ giữ điểm gần biên mạnh (chữ có viền) -> loại bỏ trời/tường sáng
    edges = cv2.Canny(gray, 60, 160)
    edges = cv2.dilate(edges, np.ones((5, 5), np.uint8))
    h, w = roi.shape[:2]
    keep = np.zeros(gray.shape, np.uint8)
    for m in (white, yellow):  # xử lý riêng để chữ trắng trên nền vàng không bị dính vùng
        full = m.astype(np.uint8) * 255
        cand = cv2.bitwise_and(full, edges)
        seeds = np.zeros_like(keep)
        n, lab, stats, _ = cv2.connectedComponentsWithStats(cand, 8)
        for i in range(1, n):
            x, y, ww, hh, area = stats[i]
            if area < 3 or hh > h * 0.9 or (ww > w * 0.6 and hh > h * 0.5):
                continue
            seeds[lab == i] = 255
        if not seeds.any():
            continue
        # "mọc lại" toàn bộ nét chữ (kể cả lõi nét dày xa cạnh) từ các hạt
        n2, lab2, stats2, _ = cv2.connectedComponentsWithStats(full, 8)
        hit = np.unique(lab2[seeds > 0])
        for i in hit:
            if i == 0:
                continue
            x, y, ww, hh, area = stats2[i]
            if hh > h * 0.9 or (ww > w * 0.6 and hh > h * 0.5):
                # vùng sáng lớn dính vào chữ -> chỉ lấy phần gần cạnh
                keep |= ((lab2 == i) & (cand > 0)).astype(np.uint8) * 255
            elif area > 0.8 * ww * hh and ww * hh > (h * 0.6) ** 2:
                continue  # mảng sáng đặc lớn, không phải nét chữ
            else:
                keep[lab2 == i] = 255
    if dilate > 0 and keep.any():
        # bao phủ viền đen quanh chữ: lấy các điểm tối nằm sát lõi chữ
        ring = cv2.dilate(keep, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (dilate * 2 + 1, dilate * 2 + 1)))
        dark = ((v < 90) & (ring > 0)).astype(np.uint8) * 255
        keep = cv2.bitwise_or(keep, dark)
        keep = cv2.dilate(keep, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (dilate, dilate)), iterations=1)
    return keep


def _dilate_for(height: int, strength: str) -> int:
    base = {"light": 3, "normal": 5, "strong": 8}.get(strength, 5)
    return max(3, int(round(base * max(0.6, height / 720))))


class BoxIndex:
    """Tra cứu khung chữ OCR theo thời gian."""

    def __init__(self, path: str | Path | None):
        self.times: list[float] = []
        self.boxes: list[list] = []
        self.tol = 0.4
        if path and Path(path).exists():
            d = json.loads(Path(path).read_text("utf-8"))
            self.tol = 1.5 / float(d.get("fps", 4))
            for f in d.get("frames", []):
                self.times.append(float(f["t"]))
                self.boxes.append(f["boxes"])

    def __bool__(self):
        return bool(self.times)

    def at(self, t: float) -> list | None:
        if not self.times:
            return None
        i = bisect.bisect_left(self.times, t)
        best = None
        for j in (i - 1, i):
            if 0 <= j < len(self.times) and abs(self.times[j] - t) <= self.tol:
                if best is None or abs(self.times[j] - t) < abs(self.times[best] - t):
                    best = j
        return self.boxes[best] if best is not None else []


def _fill(frame: np.ndarray, x0: int, y0: int, x1: int, y1: int, m: np.ndarray, radius: int, use_lama: bool):
    import cv2

    if use_lama:
        from . import lama

        lama.inpaint_roi(frame, x0, y0, x1, y1, m)
    else:
        roi = frame[y0:y1, x0:x1]
        frame[y0:y1, x0:x1] = cv2.inpaint(roi, m, radius, cv2.INPAINT_TELEA)


def _use_lama(settings: dict | None, progress=None) -> bool:
    if (settings or {}).get("inpaint_backend", "lama") != "lama":
        return False
    from . import lama

    if not lama.available():
        return False
    try:
        lama.load(progress)
        return True
    except Exception as e:  # không tải được mô hình -> dùng OpenCV
        if progress:
            progress(0.0, f"Không dùng được LaMa ({str(e)[:60]}), chuyển sang OpenCV")
        return False


def inpaint_video(src: str | Path, out: str | Path, regions: list[dict], settings: dict,
                  boxes_file: str | Path | None = None, strength: str = "normal",
                  progress: Callable[[float, str], None] | None = None,
                  cancel: Callable[[], bool] | None = None) -> Path:
    import cv2

    info = ff.probe(src)
    W, H, fps = info.width, info.height, info.fps or 25
    total_frames = max(1, int(info.duration * fps))
    rects = []
    for r in regions:
        x, y = int(float(r["x"]) * W), int(float(r["y"]) * H)
        w, h = int(float(r["w"]) * W), int(float(r["h"]) * H)
        rects.append((max(0, x), max(0, y), min(W, x + w), min(H, y + h)))
    dil = _dilate_for(H, strength)
    radius = {"light": 3, "normal": 5, "strong": 7}.get(strength, 5)
    boxes = BoxIndex(boxes_file)
    use_lama = _use_lama(settings, progress)
    if progress:
        progress(0.0, "Đang xoá phụ đề bằng " + ("AI LaMa" if use_lama else "OpenCV"))

    dec = subprocess.Popen([ff.ffmpeg_bin(), "-hide_banner", "-loglevel", "error", "-i", str(src), "-an",
                            "-f", "rawvideo", "-pix_fmt", "bgr24", "-vsync", "passthrough", "pipe:1"],
                           stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, creationflags=ff._NO_WINDOW)
    enc_args = [ff.ffmpeg_bin(), "-hide_banner", "-loglevel", "error", "-y",
                "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{W}x{H}", "-r", f"{fps:.5f}", "-i", "pipe:0",
                "-i", str(src), "-map", "0:v", "-map", "1:a?", "-c:a", "copy"]
    enc_args += ff.video_encode_args(settings, crf=min(18, int(settings.get("video_crf", 20))))
    enc_args += ["-shortest", str(out)]
    enc = subprocess.Popen(enc_args, stdin=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=ff._NO_WINDOW)
    err: list[bytes] = []
    threading.Thread(target=lambda: err.append(enc.stderr.read()), daemon=True).start()  # type: ignore

    size = W * H * 3
    i = 0
    try:
        while True:
            buf = dec.stdout.read(size)  # type: ignore[union-attr]
            if len(buf) < size:
                break
            if cancel and cancel():
                raise ff.Cancelled()
            frame = np.frombuffer(buf, np.uint8).reshape(H, W, 3).copy()
            t = i / fps
            ocr_boxes = boxes.at(t) if boxes else None
            for (x0, y0, x1, y1) in rects:
                roi = frame[y0:y1, x0:x1]
                if roi.size == 0:
                    continue
                if ocr_boxes is not None:
                    if not ocr_boxes:
                        continue  # OCR cho biết lúc này không có chữ
                    limit = np.zeros(roi.shape[:2], np.uint8)
                    for bx0, by0, bx1, by1 in ocr_boxes:
                        pad = 6
                        a0, b0 = max(0, bx0 - x0 - pad), max(0, by0 - y0 - pad)
                        a1, b1 = min(x1 - x0, bx1 - x0 + pad), min(y1 - y0, by1 - y0 + pad)
                        if a1 > a0 and b1 > b0:
                            limit[b0:b1, a0:a1] = 255
                    if not limit.any():
                        continue
                    m = cv2.bitwise_and(text_mask(roi, dil), limit)
                    if m.sum() < 255 * 20:  # không bắt được nét chữ -> xoá cả khung chữ
                        m = limit
                else:
                    m = text_mask(roi, dil)
                    if m.sum() < 255 * 30:
                        continue
                _fill(frame, x0, y0, x1, y1, m, radius, use_lama)
            enc.stdin.write(frame.tobytes())  # type: ignore[union-attr]
            i += 1
            if progress and i % 25 == 0:
                progress(min(0.999, i / total_frames), f"Đang xoá phụ đề khung {i}/{total_frames}")
    finally:
        dec.kill()
        try:
            enc.stdin.close()  # type: ignore[union-attr]
        except Exception:
            pass
        enc.wait()
    if enc.returncode != 0:
        raise ff.FFmpegError("Lỗi encode khi xoá phụ đề: " + b"".join(err).decode("utf-8", "replace")[-500:])
    if progress:
        progress(1.0, "Đã xoá phụ đề cũ")
    return Path(out)


def preview_frame(src: str | Path, t: float, regions: list[dict], out_jpg: str | Path, mode: str = "inpaint",
                  strength: str = "normal", settings: dict | None = None) -> Path:
    """Xem trước hiệu quả xoá phụ đề trên 1 khung hình."""
    import cv2

    info = ff.probe(src)
    W, H = info.width, info.height
    cmd = [ff.ffmpeg_bin(), "-hide_banner", "-loglevel", "error", "-ss", f"{t:.3f}", "-i", str(src),
           "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "bgr24", "pipe:1"]
    buf = subprocess.run(cmd, capture_output=True, creationflags=ff._NO_WINDOW).stdout
    frame = np.frombuffer(buf[:W * H * 3], np.uint8).reshape(H, W, 3).copy()
    dil = _dilate_for(H, strength)
    for r in regions:
        x0, y0 = int(float(r["x"]) * W), int(float(r["y"]) * H)
        x1, y1 = min(W, x0 + int(float(r["w"]) * W)), min(H, y0 + int(float(r["h"]) * H))
        roi = frame[y0:y1, x0:x1]
        if roi.size == 0:
            continue
        if mode == "inpaint":
            m = text_mask(roi, dil)
            _fill(frame, x0, y0, x1, y1, m, 5, _use_lama(settings))
        elif mode == "blur":
            k = max(3, (min(roi.shape[:2]) // 3) | 1)
            frame[y0:y1, x0:x1] = cv2.GaussianBlur(roi, (k, k), 0)
        elif mode == "box":
            frame[y0:y1, x0:x1] = 0
        elif mode == "mask":
            m = text_mask(roi, dil)
            over = roi.copy()
            over[m > 0] = (0, 0, 255)
            frame[y0:y1, x0:x1] = over
    cv2.imwrite(str(out_jpg), frame)
    return Path(out_jpg)
