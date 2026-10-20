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
    """Bản đồ nét chữ thu nhỏ để biết chữ có đổi không – KHÔNG phụ thuộc màu chữ (trước chỉ nhận chữ trắng / vàng có
    viền: chữ hồng, đỏ, xanh lá… không bao giờ được đọc; cảnh sáng làm câu mới hiện ra chỉ đổi 14 % -> bị coi là cũ).
    Mật độ biên (Canny) theo ô 4×4 điểm trên ảnh thu về rộng 320 điểm."""
    import cv2

    h, w = img.shape[:2]
    sc = 320 / w if w > 320 else 1.0
    small = cv2.resize(img, (max(8, int(w * sc)), max(8, int(h * sc))), interpolation=cv2.INTER_AREA) if sc != 1 else img
    e = cv2.Canny(cv2.cvtColor(small, cv2.COLOR_BGR2GRAY), 60, 180) > 0
    gh, gw = e.shape[0] // 4 * 4, e.shape[1] // 4 * 4
    return e[:gh, :gw].reshape(gh // 4, 4, gw // 4, 4).mean(axis=(1, 3)).astype(np.float32)


def _changed(a: np.ndarray | None, b: np.ndarray | None) -> bool:
    """Khung chữ có đổi không: đủ vài ô có nét đổi nhiều (1 chữ Hán ~ 9 ô) – nền chuyển động cũng tính là đổi
    (đọc lại thừa còn hơn bỏ sót câu)."""
    if a is None or b is None or a.shape != b.shape:
        return True
    d = np.abs(a - b) > 0.25
    return int(d.sum()) >= max(3, int(0.004 * d.size))


def _ocr(img: np.ndarray, min_score: float):
    res, _ = get_engine()(img)
    items = []
    for box, text, score in res or []:
        if float(score) < min_score or not _CJK.search(text):
            continue
        pts = np.array(box, dtype=np.float32)
        items.append((pts, text.strip(), float(score)))
    return _reading_order(items)


def _reading_order(items: list, line_h: float = 0.0) -> list:
    """Thứ tự đọc: gom các mảnh chữ cùng hàng (tâm dọc lệch < nửa dòng), hàng trên trước, trong hàng trái -> phải.
    (Làm tròn toạ độ dọc như trước có thể đặt mảnh bên phải lên trước khi 2 mảnh rơi vào 2 mức làm tròn.)"""
    if len(items) < 2:
        return items
    if line_h <= 0:
        line_h = float(np.median([_bbox(it[0])[3] - _bbox(it[0])[1] for it in items]))
    rows: list[list] = []
    for it in sorted(items, key=lambda it: float(it[0][:, 1].mean())):
        if rows and float(it[0][:, 1].mean()) - float(rows[-1][-1][0][:, 1].mean()) < 0.5 * line_h:
            rows[-1].append(it)
        else:
            rows.append([it])
    return [it for r in rows for it in sorted(r, key=lambda it: float(it[0][:, 0].min()))]


def _similar(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    return difflib.SequenceMatcher(None, a, b).ratio()


def _same_line(a: str, b: str) -> bool:
    """2 lần đọc là CÙNG 1 câu phụ đề (OCR đọc lệch vài chữ), hay đã sang câu mới? So số chữ khác nhau: tối đa 1 chữ
    mỗi 6 chữ. (Trước dùng tỉ lệ giống ≥ 0,7 -> 2 câu liền nhau na ná "这是黄色的字幕" / "这是粉红色的字幕" bị gộp làm 1.)"""
    if not a or not b:
        return False
    if a == b:
        return True
    edits = sum(max(i2 - i1, j2 - j1) for op, i1, i2, j1, j2 in
                difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes() if op != "equal")
    return edits <= max(1, min(len(a), len(b)) // 6)


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


def _anchor_block(items: list, anchor_y: float | None, line_h: float) -> list:
    """Chỉ giữ khối dòng chữ liền nhau quanh dòng phụ đề chính (phụ đề 2–3 dòng vẫn đủ) – bỏ chữ trong cảnh nằm xa
    (biển hiệu, chữ trên áo…) lọt vào vùng quét rộng."""
    if not items or anchor_y is None or line_h <= 0:
        return items
    rows = sorted(items, key=lambda it: _bbox(it[0])[1])
    blocks = [[rows[0]]]
    for it in rows[1:]:
        if _bbox(it[0])[1] - max(_bbox(x[0])[3] for x in blocks[-1]) <= 0.9 * line_h:
            blocks[-1].append(it)
        else:
            blocks.append([it])

    def dist(b) -> float:
        top, bot = min(_bbox(x[0])[1] for x in b), max(_bbox(x[0])[3] for x in b)
        if top - 0.5 * line_h <= anchor_y <= bot + 0.5 * line_h:
            return 0.0
        return min(abs(anchor_y - top), abs(anchor_y - bot))

    # khối phải có 1 dòng nằm đúng hàng phụ đề chính (phụ đề 1–3 dòng luôn có); chữ trong cảnh ở chỗ khác -> bỏ
    on_row = [b for b in blocks
              if any(abs((_bbox(x[0])[1] + _bbox(x[0])[3]) / 2 - anchor_y) <= 0.6 * line_h for x in b)]
    if not on_row:
        return []
    return _reading_order(min(on_row, key=dist), line_h)


def subtitle_height(frames: list[tuple[float, list]], marks) -> float:
    """Chiều cao chữ phụ đề chính (trung vị theo thời gian) – chữ nhỏ hơn hẳn là hoạ tiết / chữ trong cảnh."""
    hs = [_bbox(it[0])[3] - _bbox(it[0])[1] for _, items in frames for it in items if not _is_mark(it, marks)]
    return float(np.median(hs)) if len(hs) >= 8 else 0.0


def extract_hardsubs(src: str | Path, region=None, fps: float = 4.0, min_score: float = 0.6,
                     progress: Callable[[float, str], None] | None = None,
                     cancel: Callable[[], bool] | None = None,
                     boxes_out: str | Path | None = None, stats: dict | None = None,
                     low_score: float = 0.45) -> list[Segment]:
    """Đọc phụ đề in sẵn: quét vùng chữ `fps` lần mỗi giây; chỉ chạy OCR khi nét chữ đổi (mọi màu chữ) và ít nhất
    mỗi 1 giây (lưới an toàn). Chữ đọc được với độ tin cậy thấp (< min_score) chỉ giữ khi đọc lặp lại giống nhau
    ở khung khác trong ±1 giây. region có line_y / line_h (dòng phụ đề chính, từ detect_subtitle_region) thì chỉ
    giữ khối dòng chữ quanh dòng đó (phụ đề 2–3 dòng), bỏ chữ trong cảnh."""
    src = str(src)
    info = probe(src)
    if not info.has_video:
        raise RuntimeError("File không có hình ảnh.")
    reg_d = dict(region) if isinstance(region, dict) else {}
    region = Region.from_any(region)
    total = info.duration or 1
    if progress:
        progress(0.0, "Đang nạp mô hình OCR…")
    get_engine()

    reads: list[list] = []                     # kết quả của mỗi lần OCR thật
    frames: list[tuple[float, int]] = []       # (t, chỉ số lần OCR dùng cho khung này; -1 = chưa có chữ)
    fresh_t: list[float] = []
    last_sig = None
    last_read = -1
    since = 0
    step = 1.0 / fps
    geo = None
    for t, img, scale, (ox, oy) in _frames(src, region, fps, info.width, info.height):
        if cancel and cancel():
            raise Cancelled()
        geo = (scale, ox, oy)
        sig = _text_signature(img)
        since += 1
        if _changed(sig, last_sig) or since >= max(1, int(round(fps))):   # chữ đổi, hoặc đã 1 giây chưa đọc lại
            reads.append(_ocr(img, low_score))
            fresh_t.append(t)
            last_read = len(reads) - 1
            last_sig = sig
            since = 0
        frames.append((t, last_read))
        if progress and int(t * fps) % 8 == 0:
            progress(min(0.999, t / total), f"OCR phụ đề {int(t)}/{int(total)} giây")

    # chữ độ tin cậy thấp: giữ nếu 1 lần đọc khác trong ±1 giây ra chữ giống (chữ thật), bỏ nếu chỉ thoáng qua (rác)
    import bisect

    for i, items in enumerate(reads):
        if not any(sc < min_score for _, _, sc in items):
            continue
        lo, hi = bisect.bisect_left(fresh_t, fresh_t[i] - 1.0), bisect.bisect_right(fresh_t, fresh_t[i] + 1.0)
        others = [it[1] for j in range(lo, hi) if j != i for it in reads[j]]
        reads[i] = [it for it in items if it[2] >= min_score or any(_similar(it[1], o) >= 0.8 for o in others)]

    # chữ trong cảnh đang chuyển động (vật có chữ bay ngang qua) lọt vào vùng quét rộng: cùng chữ mà mỗi lần đọc ở
    # 1 chỗ khác -> bỏ; phụ đề đứng yên suốt lúc hiện (video mẫu: thẻ "鲸元券" bay sát trên câu "别说了")
    moved = []
    for i, items in enumerate(reads):
        lo, hi = bisect.bisect_left(fresh_t, fresh_t[i] - 1.0), bisect.bisect_right(fresh_t, fresh_t[i] + 1.0)
        keep = []
        for it in items:
            b = _bbox(it[0])
            tol = 0.2 * (b[3] - b[1])
            same = [_bbox(o[0]) for j in range(lo, hi) if j != i for o in reads[j] if _similar(o[1], it[1]) >= 0.5]
            if same and not any(abs(ob[1] - b[1]) <= tol and abs(ob[3] - b[3]) <= tol for ob in same):
                continue
            keep.append(it)
        moved.append(keep)
    reads = moved

    scale, ox, oy = geo or (1.0, 0, 0)
    anchor_y = line_h = None
    if reg_d.get("line_y") is not None and reg_d.get("line_h"):
        anchor_y = (float(reg_d["line_y"]) * info.height - oy) * scale
        line_h = float(reg_d["line_h"]) * info.height * scale
    timed = [(t, reads[k] if k >= 0 else []) for t, k in frames]
    # bỏ logo / watermark tên kênh (chữ đứng yên gần suốt video) khỏi phụ đề,
    # và chữ nhỏ hơn hẳn chữ phụ đề (hoạ tiết trên áo đọc nhầm thành "米"…)
    marks = static_marks(timed, fps)
    h_sub = subtitle_height(timed, marks)
    kept_reads = []
    for items in reads:
        keep = [it for it in items if not _is_mark(it, marks)
                and (not h_sub or _bbox(it[0])[3] - _bbox(it[0])[1] >= 0.7 * h_sub)]
        kept_reads.append(_anchor_block(keep, anchor_y, line_h or h_sub))
    raw: list[tuple[float, str, float]] = []  # (t, text, score)
    boxes: list[dict] = []
    for t, k in frames:
        keep = kept_reads[k] if k >= 0 else []
        raw.append((t, "".join(it[1] for it in keep), float(np.mean([it[2] for it in keep])) if keep else 0.0))
        if keep:
            bxs = []
            for pts, _, _ in keep:
                px = pts / scale
                bxs.append([int(px[:, 0].min() + ox), int(px[:, 1].min() + oy),
                            int(px[:, 0].max() + ox), int(px[:, 1].max() + oy)])
            boxes.append({"t": round(t, 3), "boxes": bxs})

    # Gom các khung liên tiếp có cùng nội dung thành 1 câu
    segs: list[Segment] = []
    cur_start, cur_texts, cur_last = None, [], 0.0
    for t, text, score in raw + [(total + step, "", 0.0)]:
        if cur_start is not None:
            ref = max(cur_texts, key=lambda x: x[1])[0]
            if text and _same_line(text, ref) and t - cur_last <= step * 2.5:
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
        if merged and _same_line(merged[-1].text, s.text) and s.start - merged[-1].end < 0.6:
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
    if stats is not None:
        stats.update({"frames": len(frames), "ocr_runs": len(reads), "lines": len(merged),
                      "text_box": _text_box(boxes, info.width, info.height)})
    if progress:
        progress(1.0, f"OCR xong {len(merged)} câu")
    return merged


def _text_box(boxes: list[dict], W: int, H: int) -> dict | None:
    """Vùng chữ phụ đề thật (gộp khung chữ đã đọc, bỏ 2 % lệch nhất mỗi phía) – gợi ý vùng xoá phụ đề cũ vừa khít."""
    bs = np.array([b for f in boxes for b in f["boxes"]], dtype=np.float32)
    if len(bs) < 3 or not W or not H:
        return None
    x0, y0 = np.percentile(bs[:, 0], 2), np.percentile(bs[:, 1], 2)
    x1, y1 = np.percentile(bs[:, 2], 98), np.percentile(bs[:, 3], 98)
    pad_x, pad_y = 0.02 * W, 0.25 * float(np.median(bs[:, 3] - bs[:, 1]))
    x0, y0 = max(0.0, x0 - pad_x) / W, max(0.0, y0 - pad_y) / H
    x1, y1 = min(W, x1 + pad_x) / W, min(H, y1 + pad_y) / H
    return {"x": round(float(x0), 4), "y": round(float(y0), 4), "w": round(float(x1 - x0), 4),
            "h": round(float(y1 - y0), 4)}


UP_LINES, DOWN_LINES = 2.2, 1.2   # vùng quét: thêm ~2 dòng phía trên + ~1 dòng phía dưới dòng phụ đề đầu tiên


def _sample(src: str, start: float, dur: float, fps: float, W: int, H: int, max_side: int = 960):
    """Các khung hình trong [start, start+dur] (fps khung/giây), thu nhỏ cạnh dài ≤ max_side – 1 lần gọi ffmpeg."""
    sc = min(1.0, max_side / max(W, H))
    w2, h2 = int(W * sc) // 2 * 2, int(H * sc) // 2 * 2
    cmd = [ffmpeg_bin(), "-hide_banner", "-loglevel", "error", "-ss", f"{max(0.0, start):.3f}", "-t", f"{dur:.3f}",
           "-i", src, "-an", "-vf", f"fps={fps},scale={w2}:{h2}", "-f", "rawvideo", "-pix_fmt", "bgr24", "pipe:1"]
    buf = subprocess.run(cmd, capture_output=True, creationflags=_NO_WINDOW).stdout
    n = len(buf) // (w2 * h2 * 3)
    return [(start + i / fps, np.frombuffer(buf[i * w2 * h2 * 3:(i + 1) * w2 * h2 * 3], np.uint8).reshape(h2, w2, 3),
             sc) for i in range(n)]


def detect_subtitle_region(src: str | Path, progress: Callable[[float, str], None] | None = None) -> dict:
    """Tự dò vùng phụ đề, kỹ nhất ở đầu video:
      * 5 giây đầu quét dày (4 khung/giây), 5 giây cuối 2 khung/giây, thêm 8 khung rải giữa video;
        5 giây đầu chưa có chữ thì quét tiếp tới giây 30;
      * chữ đứng yên ở cùng chỗ gần suốt các khung (logo, watermark) không phải phụ đề;
      * dòng phụ đề = hàng chữ (cùng độ cao) có chữ ĐỔI nhiều lần; lấy CÂU ĐẦU TIÊN của hàng đó làm mốc:
        cao 1 dòng = h -> vùng quét thêm ~2,2·h phía trên + ~1,2·h phía dưới (phụ đề 2–3 dòng không bị cắt),
        chiều ngang hết khung hình (câu dài không bị thiếu chữ ở 2 bên).
    Trả về vùng quét {x, y, w, h} (tỉ lệ 0..1) + line_y / line_h (dòng phụ đề đầu tiên – để bỏ chữ trong cảnh)
    + erase (khung vừa khít chữ phụ đề – gợi ý vùng xoá phụ đề cũ) + first_t (giây có câu đầu tiên)."""
    src = str(src)
    info = probe(src)
    W, H = info.width, info.height
    fallback = {"x": 0.0, "y": 0.66, "w": 1.0, "h": 0.34, "found": False}
    if not W or not H:
        return fallback
    eng = get_engine()
    dur = info.duration or 10
    frames = _sample(src, 0, min(5.0, dur), 4, W, H)
    if dur > 10:
        frames += _sample(src, dur - 5, 5, 2, W, H)
    if dur > 15:
        for k in range(8):
            frames += _sample(src, 5 + (dur - 10) * (k + 0.5) / 8, 0.3, 4, W, H)[:1]
    reads: list[tuple[float, list]] = []

    def ocr_all(fr):
        for n, (t, img, sc) in enumerate(fr):
            if progress:
                progress(n / max(1, len(fr)), f"Đang dò vùng phụ đề ({n + 1}/{len(fr)} khung)")
            res, _ = eng(img)
            items = []
            for box, text, score in res or []:
                if float(score) < 0.6 or not _CJK.search(text):
                    continue
                pts = np.array(box, dtype=np.float32) / sc
                items.append(((float(pts[:, 0].min() / W), float(pts[:, 1].min() / H),
                               float(pts[:, 0].max() / W), float(pts[:, 1].max() / H)), text.strip()))
            reads.append((t, items))

    ocr_all(frames)
    head = [it for t, its in reads if t <= 5 for it in its]
    if not head and dur > 10:   # 5 giây đầu chưa có chữ (đoạn mở đầu) -> quét tiếp tới giây 30
        more = _sample(src, 5, min(25.0, dur - 10), 1, W, H)
        ocr_all(more)
        frames += more

    # logo / watermark: nét chữ Y HỆT nhau ở 2 khung cách xa nhau (so ảnh biên, không so chữ đọc ra – OCR đọc lệch
    # "冬冰b#" / "冬冰bilsil" vẫn nhận ra); phụ đề ở cùng chỗ nhưng nét chữ đổi theo câu -> không bị nhầm
    import cv2

    shots = {t: (img, sc) for t, img, sc in frames}

    def gray_at(t: float, b) -> np.ndarray | None:
        img, _ = shots[t]
        h, w = img.shape[:2]
        x0, y0 = int(b[0] * w), int(b[1] * h)
        x1, y1 = max(x0 + 4, int(b[2] * w)), max(y0 + 4, int(b[3] * h))
        g = img[y0:y1, x0:x1]
        return cv2.cvtColor(g, cv2.COLOR_BGR2GRAY) if g.size else None

    def still(t: float, b) -> bool:
        # đo trên video mẫu: watermark IoU nét 0,52 / tương quan 0,56; phụ đề khác câu cùng chỗ ≤ 0,19 / ≤ 0,13
        far = max((u for u in shots if u != t), key=lambda u: abs(u - t), default=None)
        if far is None or abs(far - t) < 4:
            return False
        a, c = gray_at(t, b), gray_at(far, b)
        if a is None or c is None or a.shape != c.shape:
            return False
        ea, ec = cv2.Canny(a, 60, 180) > 0, cv2.Canny(c, 60, 180) > 0
        iou = (ea & ec).sum() / max(1, (ea | ec).sum())
        ncc = float(np.corrcoef(a.ravel().astype(np.float32), c.ravel().astype(np.float32))[0, 1]) \
            if a.std() > 1 and c.std() > 1 else 0.0
        return iou >= 0.35 or ncc >= 0.5

    cands = [(t, b, txt) for t, its in reads for b, txt in its if t in shots and not still(t, b)]
    if not cands:
        return fallback

    # gom theo hàng (tâm dọc gần nhau) -> hàng phụ đề là hàng có nhiều câu KHÁC nhau nhất
    hs = np.array([b[3] - b[1] for _, b, _ in cands])
    tol = 0.6 * float(np.median(hs))
    cands.sort(key=lambda c: (c[1][1] + c[1][3]) / 2)
    rows: list[list] = [[cands[0]]]
    for c in cands[1:]:
        if (c[1][1] + c[1][3]) / 2 - (rows[-1][-1][1][1] + rows[-1][-1][1][3]) / 2 <= tol:
            rows[-1].append(c)
        else:
            rows.append([c])

    def distinct(row) -> int:
        texts: list[str] = []
        for _, _, txt in row:
            if not any(_similar(txt, x) >= 0.8 for x in texts):
                texts.append(txt)
        return len(texts)

    def row_y(row) -> float:
        return float(np.median([(b[1] + b[3]) / 2 for _, b, _ in row]))

    best = max(rows, key=lambda r: (distinct(r), len(r), row_y(r)))
    if distinct(best) < 2 and not (len(best) >= 3 and row_y(best) > 0.45):
        return fallback
    line_h = float(np.median([b[3] - b[1] for _, b, _ in best]))
    first_t, first_b, _ = min(best, key=lambda c: c[0])
    # hàng sát ngay trên / dưới (dòng 2, 3 của cùng phụ đề) tính vào khung chữ
    block = [c for r in rows if abs(row_y(r) - row_y(best)) <= 1.6 * line_h for c in r]
    top = min(first_b[1], min(b[1] for _, b, _ in block))
    bot = max(first_b[3], max(b[3] for _, b, _ in block))
    y0 = max(0.0, top - UP_LINES * line_h)
    y1 = min(1.0, bot + DOWN_LINES * line_h)
    ex0 = max(0.0, min(b[0] for _, b, _ in block) - 0.02)
    ex1 = min(1.0, max(b[2] for _, b, _ in block) + 0.02)
    erase = {"x": round(ex0, 4), "y": round(max(0.0, top - 0.3 * line_h), 4), "w": round(ex1 - ex0, 4),
             "h": round(min(1.0, bot + 0.3 * line_h) - max(0.0, top - 0.3 * line_h), 4)}
    return {"x": 0.0, "y": round(y0, 4), "w": 1.0, "h": round(y1 - y0, 4), "found": True,
            "line_y": round((first_b[1] + first_b[3]) / 2, 4), "line_h": round(line_h, 4),
            "erase": erase, "first_t": round(first_t, 2)}


def expand_region(region: dict) -> dict:
    """Vùng người dùng tự khoanh (thường vừa khít 1 dòng chữ) -> vùng quét: hết chiều ngang, thêm ~2 dòng phía trên,
    ~1 dòng phía dưới; dòng giữa vùng khoanh là dòng phụ đề chính."""
    r = Region.from_any(region)
    line_h = min(r.h, 0.12)
    y0 = max(0.0, r.y - UP_LINES * line_h)
    y1 = min(1.0, r.y + r.h + DOWN_LINES * line_h)
    return {"x": 0.0, "y": round(y0, 4), "w": 1.0, "h": round(y1 - y0, 4), "found": True,
            "line_y": round(r.y + r.h / 2, 4), "line_h": round(line_h, 4)}
