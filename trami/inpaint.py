"""Xoá phụ đề cứng / gỡ logo cố định trên cả video – không để lại vệt mờ / hộp đen.

Mỗi khung hình (chỉ xử lý các dải ngang chứa vùng, phần còn lại giữ nguyên):
  1. Logo cố định (trami/logo.py): tính ngược lớp logo trong suốt -> nền thật hiện lại; phần logo đặc -> bước 3.
  2. Mặt nạ phụ đề: nét chữ MỌI MÀU + viền + bóng (so với nền xung quanh, trami/vfill.subtitle_mask) + chữ sáng có
     viền tối (cách cũ); đã OCR thì chỉ trong khung chữ OCR lúc có chữ (hoặc xoá cả khung chữ – "Xoá cả khung chữ").
  3. Lấp chỗ bị che bằng NỀN THẬT lấy từ khung trước / sau (bù chuyển động, trami/vfill.BandFiller); chỗ chưa từng lộ
     ra mới để AI LaMa (hoặc OpenCV) vẽ, cảnh đứng yên dùng lại phần đã vẽ (không nhấp nháy).
  4. FFmpeg encode, giữ nguyên âm thanh.
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


def _fill_mask(img: np.ndarray, m: np.ndarray, radius: int, use_lama: bool):
    """Vẽ lại (AI LaMa / OpenCV) phần mặt nạ m (uint8, cùng cỡ img) – chỉ trong khung bao quanh mặt nạ."""
    ys, xs = np.nonzero(m)
    if not len(ys):
        return
    y0, y1, x0, x1 = int(ys.min()), int(ys.max()) + 1, int(xs.min()), int(xs.max()) + 1
    _fill(img, x0, y0, x1, y1, np.ascontiguousarray(m[y0:y1, x0:x1]), radius, use_lama)


def _rects(regions: list[dict], W: int, H: int) -> list[tuple[int, int, int, int]]:
    out = []
    for r in regions or []:
        x, y = int(float(r["x"]) * W), int(float(r["y"]) * H)
        w, h = int(float(r["w"]) * W), int(float(r["h"]) * H)
        x0, y0, x1, y1 = max(0, x), max(0, y), min(W, x + w), min(H, y + h)
        if x1 - x0 >= 2 and y1 - y0 >= 2:
            out.append((x0, y0, x1, y1))
    return out


def _bands(rects: list[tuple[int, int, int, int]], H: int) -> list[tuple[int, int]]:
    """Các dải ngang cần xử lý: vùng chữ / logo + viền trên dưới (để đo chuyển động và so khớp nền), gộp dải chồng nhau."""
    iv = []
    for x0, y0, x1, y1 in rects:
        c = max(24, int(0.6 * (y1 - y0)))
        iv.append([max(0, y0 - c), min(H, y1 + c)])
    iv.sort()
    out: list[list[int]] = []
    for a, b in iv:
        if out and a <= out[-1][1]:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return [(a, b) for a, b in out]


class _Job:
    """Xử lý dải hình của các vùng: gỡ logo cố định (tính ngược lớp logo) + xoá phụ đề (nền thật từ khung trước / sau,
    còn sót mới AI vẽ)."""

    def __init__(self, W: int, H: int, fps: float, regions: list[dict], logos: list, boxes, strength: str,
                 use_lama: bool, temporal: bool, cover_box: bool, budget_mb: int = 600):
        from .vfill import BandFiller

        self.W, self.H, self.fps = W, H, fps
        self.sub_rects = _rects(regions, W, H)
        self.logos = logos            # [LogoModel]
        self.boxes = boxes
        self.dil = _dilate_for(H, strength)
        self.radius = {"light": 3, "normal": 5, "strong": 7}.get(strength, 5)
        self.cover_box = cover_box
        self.use_lama = use_lama
        rects = self.sub_rects + [(m.x0, m.y0, m.x1, m.y1) for m in logos]
        self.bands = _bands(rects, H)
        per = sum((b - a) * W * 8 for a, b in self.bands) or 1
        look = min(int(fps * 4), max(4, budget_mb * 1024 * 1024 // per)) if temporal else 0
        self.fillers = []
        for a, b in self.bands:
            ins = [(y0, y1) for _, y0, _, y1 in rects if y0 < b and y1 > a]
            ctx = []         # hàng ngoài vùng chữ / logo trong dải -> đo chuyển động cả khung
            cur = a
            for y0, y1 in sorted(ins):
                if y0 > cur:
                    ctx.append((cur - a, y0 - a))
                cur = max(cur, y1)
            if b > cur:
                ctx.append((cur - a, b - a))
            f = BandFiller(None, look, ctx)
            f.fallback = self._make_fallback(f)
            self.fillers.append(f)
        self.lama_only = None   # Xem thử: chỉ dùng AI ở đúng khung xem, khung khác OpenCV cho nhanh

    def _make_fallback(self, f):
        def fb(img, m):
            lama = self.use_lama and (self.lama_only is None or f.cur == self.lama_only)
            _fill_mask(img, m, self.radius, lama)
        return fb

    @property
    def height(self) -> int:
        return sum(b - a for a, b in self.bands)

    def crop_filter(self) -> str:
        """Bộ lọc FFmpeg cắt các dải rồi xếp chồng thành 1 ảnh (1 lần giải mã cho mọi dải)."""
        W = self.W
        # đổi RGB TRƯỚC khi cắt: cắt ảnh YUV 4:2:0 ở toạ độ / chiều cao lẻ bị FFmpeg làm tròn -> lệch hàng
        if len(self.bands) == 1:
            a, b = self.bands[0]
            return f"format=bgr24,crop={W}:{b - a}:0:{a}"
        n = len(self.bands)
        parts = "".join(f"[s{k}]crop={W}:{b - a}:0:{a}[c{k}];" for k, (a, b) in enumerate(self.bands))
        return f"format=bgr24,split={n}" + "".join(f"[s{k}]" for k in range(n)) + ";" + parts + \
            "".join(f"[c{k}]" for k in range(n)) + f"vstack=inputs={n}"

    def push(self, idx: int, stacked: np.ndarray, t: float):
        ocr = self.boxes.at(t) if self.boxes else None
        y = 0
        for (a, b), f in zip(self.bands, self.fillers):
            band = stacked[y:y + (b - a)].copy()
            y += b - a
            mask = np.zeros(band.shape[:2], np.uint8)
            soft = np.zeros(band.shape[:2], bool)
            for m in self.logos:
                if m.y0 >= a and m.y1 <= b:
                    op = m.apply(band, a)
                    import cv2

                    # cả logo: lấy nền thật từ khung khác nếu máy quay lia làm nền lộ ra, không thì giữ bản tính ngược
                    soft[m.y0 - a:m.y1 - a, m.x0:m.x1] |= cv2.dilate(m.mask.astype(np.uint8), np.ones((3, 3),
                                                                                                 np.uint8)) > 0
                    if op.any():
                        mask |= cv2.dilate(op, np.ones((3, 3), np.uint8), iterations=2)
            for x0, y0, x1, y1 in self.sub_rects:
                if not (y0 >= a and y1 <= b):
                    continue
                roi = band[y0 - a:y1 - a, x0:x1]
                loc = None
                if ocr is not None:
                    loc = [(bx0 - x0, by0 - y0, bx1 - x0, by1 - y0) for bx0, by0, bx1, by1 in ocr
                           if bx1 > x0 and bx0 < x1 and by1 > y0 and by0 < y1] or None
                    # OCR không thấy chữ ở khung này -> vẫn tự dò nét chữ (bắt câu OCR đọc sót)
                from .vfill import subtitle_mask

                mm = subtitle_mask(roi, self.dil, loc, self.cover_box, text_mask)
                if loc is None and mm.sum() < 255 * 30:
                    continue
                mask[y0 - a:y1 - a, x0:x1] |= mm
            f.push(idx, band, mask, soft)

    def pop(self, eof: bool = False) -> list[tuple[int, list[np.ndarray]]]:
        outs = [f.pop(eof) for f in self.fillers]
        return [(outs[0][k][0], [o[k][1] for o in outs]) for k in range(len(outs[0]))] if outs else []

    def paste(self, frame: np.ndarray, bands: list[np.ndarray]):
        for (a, b), img in zip(self.bands, bands):
            frame[a:b] = img

    def stats(self) -> dict:
        s: dict = {}
        for f in self.fillers:
            for k, v in f.stats.items():
                s[k] = s.get(k, 0) + v
        return s


def _logo_models(src: str | Path, logos: list[dict] | None, W: int, H: int, dur: float, work: Path | None,
                 progress=None) -> list:
    from . import logo as lg

    out = []
    for k, r in enumerate(logos or []):
        if progress:
            progress(0.0, f"Đang đo logo {k + 1}/{len(logos)} (lấy ~200 khung rải khắp video)…")
        out.append(lg.get_model(src, r, W, H, dur, Path(work) if work else Path(src).parent))
    return out


def inpaint_video(src: str | Path, out: str | Path, regions: list[dict], settings: dict,
                  boxes_file: str | Path | None = None, strength: str = "normal",
                  progress: Callable[[float, str], None] | None = None,
                  cancel: Callable[[], bool] | None = None, logos: list[dict] | None = None,
                  temporal: bool = True, cover_box: bool = False, work: str | Path | None = None) -> Path:
    """Xoá phụ đề cũ (regions) + gỡ logo cố định (logos) trên cả video, 1 lần giải mã. Chỉ các dải hình chứa vùng
    được xử lý, phần còn lại của khung giữ nguyên từng điểm ảnh."""
    info = ff.probe(src)
    W, H, fps = info.width, info.height, info.fps or 25
    total_frames = max(1, int(info.duration * fps))
    models = _logo_models(src, logos, W, H, info.duration, Path(work) if work else None, progress)
    use_lama = bool(regions) and _use_lama(settings, progress)
    if progress:
        progress(0.0, "Đang xoá phụ đề / logo bằng " + ("nền khung trước–sau + " if temporal else "") +
                 ("AI LaMa" if use_lama else "OpenCV"))
    job = _Job(W, H, fps, regions, models, BoxIndex(boxes_file), strength, use_lama, temporal, cover_box)
    bh = job.height
    decA = subprocess.Popen([ff.ffmpeg_bin(), "-hide_banner", "-loglevel", "error", "-i", str(src), "-an",
                             "-vf", job.crop_filter(), "-f", "rawvideo", "-pix_fmt", "bgr24", "-vsync", "cfr",
                             "-r", f"{fps:.5f}", "pipe:1"], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                            creationflags=ff._NO_WINDOW)
    decB = subprocess.Popen([ff.ffmpeg_bin(), "-hide_banner", "-loglevel", "error", "-i", str(src), "-an",
                             "-f", "rawvideo", "-pix_fmt", "bgr24", "-vsync", "cfr", "-r", f"{fps:.5f}", "pipe:1"],
                            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, creationflags=ff._NO_WINDOW)
    enc_args = [ff.ffmpeg_bin(), "-hide_banner", "-loglevel", "error", "-y",
                "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{W}x{H}", "-r", f"{fps:.5f}", "-i", "pipe:0",
                "-i", str(src), "-map", "0:v", "-map", "1:a?", "-c:a", "copy"]
    enc_args += ff.video_encode_args(settings, crf=min(18, int(settings.get("video_crf", 20))))
    enc_args += ["-shortest", str(out)]
    enc = subprocess.Popen(enc_args, stdin=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=ff._NO_WINDOW)
    err: list[bytes] = []
    threading.Thread(target=lambda: err.append(enc.stderr.read()), daemon=True).start()  # type: ignore

    band_size, size = W * bh * 3, W * H * 3
    i = written = 0

    def emit(ready):
        nonlocal written
        for idx, bands in ready:
            buf = decB.stdout.read(size)  # type: ignore[union-attr]
            while len(buf) == size and written < idx:      # khung không có dải (hiếm): ghi nguyên
                enc.stdin.write(buf)  # type: ignore[union-attr]
                written += 1
                buf = decB.stdout.read(size)  # type: ignore[union-attr]
            if len(buf) < size:
                return
            frame = np.frombuffer(buf, np.uint8).reshape(H, W, 3).copy()
            job.paste(frame, bands)
            enc.stdin.write(frame.tobytes())  # type: ignore[union-attr]
            written += 1
            if progress and written % 25 == 0:
                progress(min(0.999, written / total_frames), f"Đang xoá phụ đề / logo khung {written}/{total_frames}")
    try:
        while True:
            buf = decA.stdout.read(band_size)  # type: ignore[union-attr]
            if len(buf) < band_size:
                break
            if cancel and cancel():
                raise ff.Cancelled()
            job.push(i, np.frombuffer(buf, np.uint8).reshape(bh, W, 3), i / fps)
            i += 1
            emit(job.pop())
        emit(job.pop(eof=True))
    finally:
        decA.kill()
        decB.kill()
        try:
            enc.stdin.close()  # type: ignore[union-attr]
        except Exception:
            pass
        enc.wait()
    if enc.returncode != 0:
        raise ff.FFmpegError("Lỗi encode khi xoá phụ đề: " + b"".join(err).decode("utf-8", "replace")[-500:])
    st = job.stats()
    if progress:
        msg = "Đã xoá xong" + (" phụ đề cũ" if job.sub_rects else "") + (" + gỡ logo" if models else "")
        if st.get("temporal") or st.get("drawn"):
            msg += (f" – lấy nền thật từ khung khác: {st.get('temporal', 0)} lượt, "
                    f"AI / OpenCV vẽ thêm: {st.get('drawn', 0)} lượt")
        progress(1.0, msg)
    return Path(out)


def _decode(src, t0: float, n: int, W: int, H: int, vf: str | None = None, h_out: int | None = None) -> np.ndarray:
    cmd = [ff.ffmpeg_bin(), "-hide_banner", "-loglevel", "error", "-ss", f"{max(0.0, t0):.3f}", "-i", str(src),
           "-an", "-frames:v", str(n)]
    if vf:
        cmd += ["-vf", vf]
    cmd += ["-f", "rawvideo", "-pix_fmt", "bgr24", "pipe:1"]
    buf = subprocess.run(cmd, capture_output=True, creationflags=ff._NO_WINDOW).stdout
    hh = h_out or H
    k = len(buf) // (W * hh * 3)
    return np.frombuffer(buf[:k * W * hh * 3], np.uint8).reshape(k, hh, W, 3)


def preview_frame(src: str | Path, t: float, regions: list[dict], out_jpg: str | Path, mode: str = "inpaint",
                  strength: str = "normal", settings: dict | None = None, patch_from: str = "auto",
                  hs: dict | None = None, work: str | Path | None = None, boxes_file: str | Path | None = None) -> Path:
    """Xem trước hiệu quả xoá phụ đề / gỡ logo trên 1 khung hình – đúng cách làm khi chạy cả video (xoá phụ đề: xử lý
    cả các khung trước / sau để lấy nền thật)."""
    import cv2

    hs = dict(hs or {})
    info = ff.probe(src)
    W, H, fps = info.width, info.height, info.fps or 25
    logos = hs.get("logos") or []
    if mode == "inpaint" or logos:
        temporal = hs.get("temporal", True) is not False
        models = _logo_models(src, logos, W, H, info.duration, Path(work) if work else None)
        subs = regions if mode == "inpaint" else []
        job = _Job(W, H, fps, subs, models, BoxIndex(boxes_file) if hs.get("use_ocr_boxes", True) else BoxIndex(None),
                   strength, bool(subs) and _use_lama(settings), temporal, bool(hs.get("cover_box")), budget_mb=200)
        back = int(fps * 1.0) if temporal else 0
        look = job.fillers[0].look if job.fillers else 0
        t0 = max(0.0, t - back / fps)
        first = int(round(t0 * fps))
        target = int(round(t * fps))
        stack = _decode(src, t0, target - first + look + 1, W, H, job.crop_filter(), job.height)
        frame = _decode(src, t, 1, W, H)
        frame = frame[0].copy() if len(frame) else np.zeros((H, W, 3), np.uint8)
        job.lama_only = target
        res = None
        for k, img in enumerate(stack):
            job.push(first + k, img, (first + k) / fps)
            for idx, bands in job.pop():
                if idx == target:
                    res = bands
        for idx, bands in job.pop(eof=True):
            if idx == target:
                res = bands
        if res is not None:
            job.paste(frame, res)
        if mode == "inpaint":
            cv2.imwrite(str(out_jpg), frame)
            return Path(out_jpg)
    else:
        f = _decode(src, t, 1, W, H)
        frame = f[0].copy() if len(f) else np.zeros((H, W, 3), np.uint8)
    dil = _dilate_for(H, strength)
    from .video import round_mask, shape_px   # cùng cách tính bo góc / viền mờ như khi xuất

    for r in regions:
        x0, y0 = int(float(r["x"]) * W), int(float(r["y"]) * H)
        x1, y1 = min(W, x0 + int(float(r["w"]) * W)), min(H, y0 + int(float(r["h"]) * H))
        roi = frame[y0:y1, x0:x1]
        if roi.size == 0:
            continue
        h, w = y1 - y0, x1 - x0
        rad, fea = shape_px(hs, w, h)
        a = round_mask(w, h, rad, fea)[..., None] if (rad >= 0.5 or fea >= 0.5) else None
        if mode in ("blur", "box"):
            if mode == "blur":
                st = int(hs.get("blur_strength", 20))
                kind = hs.get("blur_kind") or "box"
                if kind == "pixel":
                    n = max(2, st // 2)
                    small = cv2.resize(roi, (max(1, w // n), max(1, h // n)), interpolation=cv2.INTER_AREA)
                    eff = cv2.resize(small, (w, h), interpolation=cv2.INTER_NEAREST).astype(np.float32)
                else:
                    sig = max(1.0, st * (0.6 if kind == "gauss" else 0.5))
                    eff = cv2.GaussianBlur(roi, (0, 0), sig).astype(np.float32)
                eff *= 1 - max(0.0, min(0.8, float(hs.get("darken") or 0)))
                op = 1.0
            else:
                c = hs.get("box_color", "#000000").lstrip("#")
                eff = np.zeros_like(roi, np.float32)
                eff[...] = (int(c[4:6], 16), int(c[2:4], 16), int(c[0:2], 16))
                op = float(hs.get("box_opacity", 1.0))
            al = (a if a is not None else 1.0) * op
            frame[y0:y1, x0:x1] = np.clip(eff * al + roi.astype(np.float32) * (1 - al), 0, 255).astype(np.uint8)
        elif mode == "stretch":
            from .video import stretch_fill

            eff = stretch_fill(frame, x0, y0, x1, y1, hs.get("stretch_from") or "both",
                               float(hs.get("stretch_smooth", 3) or 0))
            al = a if a is not None else 1.0
            frame[y0:y1, x0:x1] = np.clip(eff * al + roi.astype(np.float32) * (1 - al), 0, 255).astype(np.uint8)
        elif mode == "patch":
            from .video import patch_feather, patch_source

            sy = min(H - h, max(0, int(patch_source(r, patch_from) * H)))
            src_px = frame[sy:sy + h, x0:x1].astype(np.float32)
            f = patch_feather(w, h)
            yy, xx = np.mgrid[0:h, 0:w]
            a2 = np.clip(np.minimum(np.minimum(xx, w - 1 - xx), np.minimum(yy, h - 1 - yy)) / f, 0, 1)[..., None]
            frame[y0:y1, x0:x1] = (src_px * a2 + roi.astype(np.float32) * (1 - a2)).astype(np.uint8)
        elif mode == "mask":
            m = text_mask(roi, dil)
            over = roi.copy()
            over[m > 0] = (0, 0, 255)
            frame[y0:y1, x0:x1] = over
    cv2.imwrite(str(out_jpg), frame)
    return Path(out_jpg)
