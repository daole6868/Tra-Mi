"""Trích xuất phụ đề cứng (chữ in sẵn trên video) bằng OCR.

Rất hữu ích với video Douyin/phim Trung có sẵn phụ đề tiếng Trung: thường chính xác hơn nhận dạng giọng nói.
Đồng thời lưu vị trí khung chữ để xoá phụ đề chính xác (inpaint).
"""
from __future__ import annotations

import difflib
import json
import re
import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import numpy as np

from .ffmpeg import Cancelled, ffmpeg_bin, probe, _NO_WINDOW
from .subtitle import Segment

_engine = None
_engine_lock = threading.Lock()
_CJK = re.compile(r"[㐀-鿿]")


def get_engine():
    global _engine
    with _engine_lock:
        if _engine is None:
            from rapidocr_onnxruntime import RapidOCR

            _engine = RapidOCR()
        return _engine


@dataclass
class Region:
    """Vùng chữ, toạ độ tương đối 0..1 theo khung hình."""
    x: float = 0.0
    y: float = 0.72
    w: float = 1.0
    h: float = 0.26

    @classmethod
    def from_any(cls, d) -> "Region":
        if isinstance(d, Region):
            return d
        d = d or {}
        r = cls(float(d.get("x", 0)), float(d.get("y", 0.72)), float(d.get("w", 1)), float(d.get("h", 0.26)))
        r.x, r.y = min(max(r.x, 0), 0.99), min(max(r.y, 0), 0.99)
        r.w, r.h = min(max(r.w, 0.01), 1 - r.x), min(max(r.h, 0.01), 1 - r.y)
        return r

    def to_px(self, W: int, H: int) -> tuple[int, int, int, int]:
        x, y = int(self.x * W) // 2 * 2, int(self.y * H) // 2 * 2
        w, h = max(2, int(self.w * W) // 2 * 2), max(2, int(self.h * H) // 2 * 2)
        w, h = min(w, W - x), min(h, H - y)
        return x, y, w, h


def _frames(src: str, region: Region, fps: float, W: int, H: int, max_w: int = 1280):
    """Sinh (thời gian, ảnh BGR vùng chữ, hệ số scale) qua pipe ffmpeg."""
    x, y, w, h = region.to_px(W, H)
    scale = 1.0
    if w > max_w:
        scale = max_w / w
    ow, oh = int(w * scale) // 2 * 2, int(h * scale) // 2 * 2
    vf = f"fps={fps},crop={w}:{h}:{x}:{y}"
    if scale != 1.0:
        vf += f",scale={ow}:{oh}"
    cmd = [ffmpeg_bin(), "-hide_banner", "-loglevel", "error", "-i", src, "-an", "-vf", vf,
           "-f", "rawvideo", "-pix_fmt", "bgr24", "pipe:1"]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, creationflags=_NO_WINDOW)
    size = ow * oh * 3
    i = 0
    try:
        while True:
            buf = proc.stdout.read(size)  # type: ignore[union-attr]
            if len(buf) < size:
                break
            yield i / fps, np.frombuffer(buf, np.uint8).reshape(oh, ow, 3), scale, (x, y)
            i += 1
    finally:
        proc.kill()
        proc.wait()


def _text_signature(img: np.ndarray) -> np.ndarray:
    """Mặt nạ nét chữ (sáng/vàng có viền) thu nhỏ - để so sánh nhanh, ít bị ảnh hưởng bởi nền chuyển động."""
    import cv2

    from .inpaint import text_mask

    h, w = img.shape[:2]
    sc = 320 / w if w > 320 else 1.0
    small = cv2.resize(img, (int(w * sc), max(8, int(h * sc))), interpolation=cv2.INTER_AREA) if sc != 1 else img
    return text_mask(small, 0) > 0


def _ocr(img: np.ndarray, min_score: float):
    res, _ = get_engine()(img)
    items = []
    for box, text, score in res or []:
        if float(score) < min_score or not _CJK.search(text):
            continue
        pts = np.array(box, dtype=np.float32)
        items.append((pts, text.strip(), float(score)))
    # sắp xếp theo dòng (y) rồi x
    items.sort(key=lambda it: (round(float(it[0][:, 1].mean()) / 20), float(it[0][:, 0].min())))
    return items


def _similar(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    return difflib.SequenceMatcher(None, a, b).ratio()


def _bbox(pts) -> tuple[float, float, float, float]:
    return float(pts[:, 0].min()), float(pts[:, 1].min()), float(pts[:, 0].max()), float(pts[:, 1].max())


def static_marks(frames: list[tuple[float, list]], fps: float, min_share: float = 0.3,
                 min_sec: float = 10.0) -> list[tuple[str, float, float, float, float]]:
    """Chữ đứng yên 1 chỗ gần suốt video (logo / watermark tên kênh như "冬冰 bilibili") -> không phải phụ đề.
    Trả về [(chữ, x0, y0, x1, y1)] – khung chữ (trung vị)."""
    pos: dict[str, list[tuple[float, float, float, float]]] = {}
    for _, items in frames:
        for pts, txt, _ in items:
            pos.setdefault(txt, []).append(_bbox(pts))
    need = max(min_share * len(frames), min_sec * fps)
    out = []
    for txt, bs in pos.items():
        if len(bs) < need:
            continue
        b = np.array(bs)
        if b[:, :2].std(axis=0).max() < 12:   # không xê dịch -> chữ in cố định
            out.append((txt, *(float(v) for v in np.median(b, axis=0))))
    return out


def _is_mark(item, marks, pad: float = 15) -> bool:
    """Khung chữ nằm trong vùng watermark (kể cả khi OCR đọc lệch thành chữ khác: "冬", "水"…)."""
    x0, y0, x1, y1 = _bbox(item[0])
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    return any((mx0 - pad <= cx <= mx1 + pad and my0 - pad <= cy <= my1 + pad)
               # đọc dính cả logo bên cạnh ("冬冰bilibili" -> "冬冰b#"): cùng chỗ bắt đầu, có chữ watermark
               or (abs(x0 - mx0) < 2 * pad and abs(y0 - my0) < 2 * pad and m in item[1])
               for m, mx0, my0, mx1, my1 in marks)


def subtitle_height(frames: list[tuple[float, list]], marks) -> float:
    """Chiều cao chữ phụ đề chính (trung vị theo thời gian) – chữ nhỏ hơn hẳn là hoạ tiết / chữ trong cảnh."""
    hs = [_bbox(it[0])[3] - _bbox(it[0])[1] for _, items in frames for it in items if not _is_mark(it, marks)]
    return float(np.median(hs)) if len(hs) >= 8 else 0.0


def extract_hardsubs(src: str | Path, region=None, fps: float = 4.0, min_score: float = 0.6,
                     progress: Callable[[float, str], None] | None = None,
                     cancel: Callable[[], bool] | None = None,
                     boxes_out: str | Path | None = None) -> list[Segment]:
    src = str(src)
    info = probe(src)
    if not info.has_video:
        raise RuntimeError("File không có hình ảnh.")
    region = Region.from_any(region)
    total = info.duration or 1
    if progress:
        progress(0.0, "Đang nạp mô hình OCR…")
    get_engine()

    frames: list[tuple[float, list]] = []      # (t, các khung chữ OCR của khung hình)
    boxes: list[dict] = []
    last_sig = None
    last_items: list = []
    step = 1.0 / fps
    n_ocr = 0
    for t, img, scale, (ox, oy) in _frames(src, region, fps, info.width, info.height):
        if cancel and cancel():
            raise Cancelled()
        sig = _text_signature(img)
        if last_sig is not None and sig.shape == last_sig.shape:
            union = np.logical_or(sig, last_sig).sum()
            diff = np.logical_xor(sig, last_sig).sum() / max(1, union)
            same = union < 12 or diff < 0.25
        else:
            same = False
        if not same:
            last_items = _ocr(img, min_score) if sig.sum() > 10 else []
            n_ocr += 1
            last_sig = sig
        frames.append((t, last_items))
        if last_items:
            bxs = []
            for pts, _, _ in last_items:
                px = pts / scale
                x0, y0 = px[:, 0].min() + ox, px[:, 1].min() + oy
                x1, y1 = px[:, 0].max() + ox, px[:, 1].max() + oy
                bxs.append([int(x0), int(y0), int(x1), int(y1)])
            boxes.append({"t": round(t, 3), "boxes": bxs})
        if progress and int(t * fps) % 8 == 0:
            progress(min(0.999, t / total), f"OCR phụ đề {int(t)}/{int(total)} giây")

    # bỏ logo / watermark tên kênh (chữ đứng yên gần suốt video) khỏi phụ đề,
    # và chữ nhỏ hơn hẳn chữ phụ đề (hoạ tiết trên áo đọc nhầm thành "米"…)
    marks = static_marks(frames, fps)
    h_sub = subtitle_height(frames, marks)
    raw: list[tuple[float, str, float]] = []  # (t, text, score)
    for t, items in frames:
        keep = [it for it in items if not _is_mark(it, marks)
                and (not h_sub or _bbox(it[0])[3] - _bbox(it[0])[1] >= 0.7 * h_sub)]
        raw.append((t, "".join(it[1] for it in keep), float(np.mean([it[2] for it in keep])) if keep else 0.0))

    # Gom các khung liên tiếp có cùng nội dung thành 1 câu
    segs: list[Segment] = []
    cur_start, cur_texts, cur_last = None, [], 0.0
    for t, text, score in raw + [(total + step, "", 0.0)]:
        if cur_start is not None:
            ref = max(cur_texts, key=lambda x: x[1])[0]
            if text and _similar(text, ref) >= 0.7 and t - cur_last <= step * 2.5:
                cur_texts.append((text, score))
                cur_last = t
                continue
            # đóng câu hiện tại
            if cur_last + step - cur_start >= 0.3:
                counts: dict[str, float] = {}
                for tx, sc in cur_texts:
                    counts[tx] = counts.get(tx, 0) + sc
                best = max(counts, key=lambda k: counts[k])
                segs.append(Segment(round(cur_start, 3), round(min(total, cur_last + step), 3), best))
            cur_start, cur_texts = None, []
        if text:
            cur_start, cur_texts, cur_last = t, [(text, score)], t
    # gộp câu trùng lặp sát nhau (do nháy chữ)
    merged: list[Segment] = []
    for s in segs:
        if merged and _similar(merged[-1].text, s.text) >= 0.85 and s.start - merged[-1].end < 0.6:
            merged[-1].end = s.end
        else:
            merged.append(s)
    try:
        from .asr import to_simplified

        for s in merged:
            s.text = to_simplified(s.text)
    except Exception:
        pass
    if boxes_out:
        Path(boxes_out).write_text(json.dumps({"fps": fps, "width": info.width, "height": info.height,
                                               "frames": boxes}), "utf-8")
    if progress:
        progress(1.0, f"OCR xong {len(merged)} câu")
    return merged


def detect_subtitle_region(src: str | Path, samples: int = 12) -> dict:
    """Tự dò vùng phụ đề: OCR vài khung hình, lấy vùng chứa chữ Hán xuất hiện nhiều ở nửa dưới."""
    import cv2

    info = probe(src)
    W, H = info.width, info.height
    if not W or not H:
        return Region().__dict__
    eng = get_engine()
    ys: list[tuple[float, float]] = []
    xs: list[tuple[float, float]] = []
    dur = info.duration or 10
    for k in range(samples):
        t = dur * (k + 0.5) / samples
        cmd = [ffmpeg_bin(), "-hide_banner", "-loglevel", "error", "-ss", f"{t:.2f}", "-i", str(src),
               "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "bgr24", "pipe:1"]
        buf = subprocess.run(cmd, capture_output=True, creationflags=_NO_WINDOW).stdout
        if len(buf) < W * H * 3:
            continue
        img = np.frombuffer(buf[:W * H * 3], np.uint8).reshape(H, W, 3)
        sc = 960 / max(W, H) if max(W, H) > 960 else 1.0
        small = cv2.resize(img, (int(W * sc), int(H * sc))) if sc != 1 else img
        res, _ = eng(small)
        for box, text, score in res or []:
            if score < 0.6 or not _CJK.search(text):
                continue
            pts = np.array(box) / sc
            y0, y1 = pts[:, 1].min() / H, pts[:, 1].max() / H
            if (y0 + y1) / 2 < 0.45:  # bỏ chữ ở nửa trên (logo, tiêu đề)
                continue
            ys.append((y0, y1))
            xs.append((pts[:, 0].min() / W, pts[:, 0].max() / W))
    if not ys:
        return {"x": 0.0, "y": 0.72, "w": 1.0, "h": 0.26, "found": False}
    # lấy cụm theo trung vị để loại nhiễu
    c = float(np.median([(a + b) / 2 for a, b in ys]))
    near = [(a, b) for a, b in ys if abs((a + b) / 2 - c) < 0.08]
    y0 = max(0.0, min(a for a, _ in near) - 0.02)
    y1 = min(1.0, max(b for _, b in near) + 0.02)
    # phụ đề dài ngắn khác nhau -> mở rộng ngang đối xứng quanh tâm, tối thiểu 80% chiều rộng
    x0 = max(0.0, min(min(a for a, _ in xs) - 0.03, 0.1))
    x1 = min(1.0, max(max(b for _, b in xs) + 0.03, 0.9))
    return {"x": round(float(x0), 4), "y": round(float(y0), 4), "w": round(float(x1 - x0), 4),
            "h": round(float(y1 - y0), 4), "found": True}
