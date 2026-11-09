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
from .ocr_engine import SCAN_SIDE
from .subtitle import Segment

_engine = None
_engine_lock = threading.Lock()
_CJK = re.compile(r"[㐀-鿿]")
_ALNUM = re.compile(r"[0-9A-Za-z０-９Ａ-Ｚａ-ｚ]")
NONHAN_MIN = 0.8    # 2.1.32: câu chỉ có số / chữ Latin ("4", "43", "2 1", "OK") giữ khi đọc chắc ≥ 0,8 và nằm đúng hàng
                    # phụ đề (trước: bỏ mọi chữ không có chữ Hán -> câu đếm số mất hẳn)


def get_engine(settings: dict | None = None):
    """Bộ đọc chữ dùng chung (ocr_engine.Engine: RapidOCR 3 PP-OCRv6/v5 – card nếu có; không có thì RapidOCR 1.x)."""
    from . import ocr_engine

    return ocr_engine.get(settings)


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


def _text_signature(img: np.ndarray) -> np.ndarray:
    """Bản đồ nét chữ thu nhỏ để biết chữ có đổi không – KHÔNG phụ thuộc màu chữ (trước chỉ nhận chữ trắng / vàng có
    viền: chữ hồng, đỏ, xanh lá… không bao giờ được đọc; cảnh sáng làm câu mới hiện ra chỉ đổi 14 % -> bị coi là cũ).
    Mật độ biên (Canny) theo ô 4×4 điểm trên ảnh thu về rộng 320 điểm."""
    import cv2

    h, w = img.shape[:2]
    sc = 320 / w if w > 320 else 1.0
    small = cv2.resize(img, (max(8, int(w * sc)), max(8, int(h * sc))), interpolation=cv2.INTER_AREA) if sc != 1 else img
    e = cv2.Canny(small if small.ndim == 2 else cv2.cvtColor(small, cv2.COLOR_BGR2GRAY), 60, 180) > 0
    gh, gw = e.shape[0] // 4 * 4, e.shape[1] // 4 * 4
    return e[:gh, :gw].reshape(gh // 4, 4, gw // 4, 4).mean(axis=(1, 3)).astype(np.float32)


def _changed(a: np.ndarray | None, b: np.ndarray | None) -> bool:
    """Khung chữ có đổi không: đủ vài ô có nét đổi nhiều (1 chữ Hán ~ 9 ô) – nền chuyển động cũng tính là đổi
    (đọc lại thừa còn hơn bỏ sót câu)."""
    if a is None or b is None or a.shape != b.shape:
        return True
    d = np.abs(a - b) > 0.25
    return int(d.sum()) >= max(3, int(0.004 * d.size))


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
    n = min(len(a), len(b))
    if n < 4:      # 2.1.32: câu 1–3 chữ phải giống hệt ("4" -> "5", "但" -> "哼" là 2 câu, trước bị gộp vì lệch 1 chữ)
        return False
    edits = sum(max(i2 - i1, j2 - j1) for op, i1, i2, j1, j2 in
                difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes() if op != "equal")
    return edits <= max(1, n // 6)


def _bbox(pts) -> tuple[float, float, float, float]:
    return float(pts[:, 0].min()), float(pts[:, 1].min()), float(pts[:, 0].max()), float(pts[:, 1].max())


class _It(tuple):
    """Mảnh chữ đọc được (khung, chữ, độ chắc) – vẫn là tuple 3 phần như trước – kèm (2.1.37) ch = chi tiết từng chữ
    [(chữ, độ chắc, tâm chữ theo tỉ lệ bề rộng khung, [(chữ ứng viên, xác suất)])] | None."""
    ch = None


def _mk(pts, text: str, score: float, ch=None) -> _It:
    it = _It((pts, text, score))
    it.ch = ch
    return it


def _rect_pts(x0: float, y0: float, x1: float, y1: float) -> np.ndarray:
    return np.array([[x0, y0], [x1, y0], [x1, y1], [x0, y1]], np.float32)


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


def _mark_like(text: str, mark: str) -> bool:
    """Chữ đọc ở chỗ watermark có phải (bản đọc lệch của) watermark không: 1 chữ ("冬", "水"), ≥ nửa số chữ là chữ của
    watermark, hoặc giống ≥ 34 %. Câu phụ đề khác hẳn hiện đúng chỗ đó thì không (2.1.41)."""
    import difflib

    t = "".join(str(text or "").split())
    if len(t) <= 1:
        return True
    if sum(1 for c in t if c in mark) * 2 >= len(t):
        return True
    return difflib.SequenceMatcher(None, t, mark, autojunk=False).ratio() >= 0.34


def _is_mark(item, marks, pad: float = 15) -> bool:
    """Khung chữ nằm trong vùng watermark (kể cả khi OCR đọc lệch thành chữ khác: "冬", "水"…)."""
    x0, y0, x1, y1 = _bbox(item[0])
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    return any((mx0 - pad <= cx <= mx1 + pad and my0 - pad <= cy <= my1 + pad and _mark_like(item[1], m))
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


# ------------------------------------------------------------------ 2.1.31: quét nhanh, giờ đúng từng khung
SCAN_FPS_MAX = 30.0      # giải mã tối đa 30 khung/giây (video 60 khung/giây: giờ câu vẫn đúng tới 1/30 s)
MAX_W = 1280             # dải quét rộng hơn -> thu nhỏ về 1280 điểm (chữ phụ đề vẫn cao ≥ 25 điểm)
STACK = 8                # số khung của 1 câu dùng dựng ảnh chữ sạch
MOVE_DIFF = 36           # điểm ảnh đổi hơn mức này giữa các khung cùng 1 câu = nền đang chuyển động
SPECK = 8                # cụm điểm "đứng yên" nhỏ hơn = nhiễu tình cờ, không phải nét chữ
MOVE_SHARE = 0.08        # ≥ 8 % điểm ảnh chuyển động mới làm sạch (cảnh đứng yên: dùng khung giữa câu)
WEAK = 0.75              # lần đọc ảnh sạch kém chắc hơn mức này -> đọc thêm khung gốc rồi chọn bản tốt hơn
MIN_SEG = 0.2            # câu hiện ngắn hơn (khung chuyển cảnh / chữ đang mờ dần) bỏ
QUEUE_MAX = 32           # số câu chờ đọc tối đa (giải mã chờ khi đầy)


KEEP = 0.6               # ≥ 60 % điểm ảnh nét chữ còn y nguyên = vẫn câu đó (nền đổi xung quanh không tính)
TOL = 40                 # điểm ảnh nét chữ lệch ≤ 40 mức sáng = còn y nguyên (nhiễu nén, chữ mờ nhẹ)
MASK_MAX = 6000          # theo dõi tối đa 6000 điểm ảnh nét chữ mỗi câu (đủ chắc, tính nhanh)


def _glyph_pixels(img: np.ndarray, boxes: list, skip: list | None = None) -> np.ndarray:
    """Chỉ số (phẳng) các điểm ảnh NÉT CHỮ trong các khung chữ: phần sáng nhất (thân chữ) + tối nhất (viền) của mỗi
    khung – chữ phụ đề luôn tương phản mạnh. skip: khung đã có (khi chữ mới hiện thêm, chỉ lấy phần mới)."""
    import cv2

    H, W = img.shape
    m = np.zeros((H, W), bool)
    for b in boxes:
        x0, y0, x1, y1 = (int(v) for v in _bbox(b))
        x0, y0, x1, y1 = max(0, x0), max(0, y0), min(W, x1 + 1), min(H, y1 + 1)
        roi = img[y0:y1, x0:x1]
        if roi.size < 16:
            continue
        p2, lo, hi, p98 = np.percentile(roi, (2, 12, 80, 98))
        if hi - lo < 30 and p98 - p2 < 30:
            continue
        # 2.1.39: chữ chiếm ít hơn 20 % khung (nền đã làm rõ chữ thành đen) -> mức 80 % rơi vào hạt nhiễu nền -> lấy cả
        # mức giữa thân chữ / viền làm ngưỡng (chỉ thân chữ thật là "sáng", chỉ viền / nền tối thật là "tối")
        mid = (p2 + p98) / 2
        hi, lo = max(hi, mid), min(lo, mid)
        # nét chữ = điểm sáng nằm sát điểm tối (thân chữ cạnh viền) và ngược lại; mảng nền sáng / tối liền một khối
        # (trời, tường, dải màu đứng yên) không tính – nếu không, đổi câu mà nền đứng yên vẫn bị coi là còn câu cũ
        br, dk = (roi >= hi).astype(np.uint8), (roi <= lo).astype(np.uint8)
        k = np.ones((5, 5), np.uint8)
        m[y0:y1, x0:x1] |= ((br > 0) & (cv2.dilate(dk, k) > 0)) | ((dk > 0) & (cv2.dilate(br, k) > 0))
    for b in skip or []:
        x0, y0, x1, y1 = (int(v) for v in _bbox(b))
        pad = int(0.3 * max(4, y1 - y0))
        m[max(0, y0 - pad):y1 + pad + 1, max(0, x0 - pad):x1 + pad + 1] = False
    idx = np.flatnonzero(m)
    if len(idx) > MASK_MAX:
        idx = idx[np.linspace(0, len(idx) - 1, MASK_MAX).astype(np.int64)]
    return idx


PERSIST_GROUP = 12    # mỗi nhóm nét (sáng / tối) cần ít nhất chừng này điểm mới xét riêng


def _persist(img: np.ndarray, idx: np.ndarray, ref: np.ndarray) -> float:
    """Phần nét chữ còn nguyên. 2.1.39: nét SÁNG (thân chữ) và nét TỐI (viền) xét riêng, lấy phần kém hơn – nền cùng màu
    viền (làm rõ chữ làm nền đen như viền, phụ đề trên dải đen) thì viền cũ "còn nguyên" dù chữ đã đổi -> trước đây câu
    mới bị coi là câu cũ (tách theo đoạn có làm rõ chữ ra 0 câu, quét cả phim lệch giờ / sót câu)."""
    if not len(idx):
        return 0.0
    ok = np.abs(img.ravel()[idx].astype(np.int16) - ref) <= TOL
    hi = ref >= (int(ref.min()) + int(ref.max())) / 2
    nh = int(hi.sum())
    if nh >= PERSIST_GROUP and len(ref) - nh >= PERSIST_GROUP:
        return float(min(ok[hi].mean(), ok[~hi].mean()))
    return float(ok.mean())


@dataclass
class _Span:
    """1 câu phụ đề trên dải quét: khung đầu / cuối (số khung từ đầu video), khung chữ (tìm chữ 1 lần), các điểm ảnh
    nét chữ đang theo dõi + vài khung mẫu để dựng ảnh chữ sạch."""
    f0: int
    f1: int
    boxes: list
    idx: np.ndarray
    ref: np.ndarray
    frames: list
    n0: int = 0
    checks: int = 0
    text: str | None = None      # chữ đọc thử của câu (chỉ khi cần xem có chữ mới hiện thêm thật không – 2.4.5)

    def add(self, img: np.ndarray) -> None:
        if len(self.frames) >= 2 * STACK:
            self.frames = self.frames[::2]
        self.frames.append(img)

    def refine(self, img: np.ndarray) -> None:
        """3 mốc đầu: bỏ các điểm ảnh "nét chữ" thực ra là nền (đã đổi) -> theo dõi đúng nét chữ."""
        self.checks += 1
        if self.checks > 3 or not len(self.idx):
            return
        ok = np.abs(img.ravel()[self.idx].astype(np.int16) - self.ref) <= TOL
        if ok.sum() >= 0.5 * self.n0:
            self.idx, self.ref = self.idx[ok], self.ref[ok]


class _Tracker:
    """Theo dõi 1 khúc video: chỉ chạy bước TÌM CHỮ khi nét ở dải quét đổi (nền chuyển động không làm tách câu – câu
    chỉ đổi khi chính các điểm ảnh nét chữ đổi). Giờ hiện / tắt chữ tìm bằng chia đôi các khung giữa 2 mốc (D)."""

    def __init__(self, detect, emit, offset: int, read=None):
        self.detect, self.emit, self.off, self.read = detect, emit, offset, read
        self.cur: _Span | None = None
        self.vsig = None            # dấu nét của khung đã kiểm tra bằng bộ tìm chữ gần nhất
        self.n_det = 0

    def _det(self, img: np.ndarray, sig) -> list:
        self.n_det += 1
        self.vsig = sig
        return self.detect(img)

    def _start(self, pend: list, i: int, img: np.ndarray, boxes: list, idx: np.ndarray) -> int:
        """Khung đầu tiên (trong pend + khung i) đã có các nét chữ idx – chữ hiện từ đó."""
        ref = img.ravel()[idx].astype(np.int16)
        lo, hi = -1, len(pend)
        while hi - lo > 1:
            m = (lo + hi) // 2
            if _persist(pend[m][1], idx, ref) >= KEEP:
                hi = m
            else:
                lo = m
        return pend[hi][0] if hi < len(pend) else i

    def _open(self, f0: int, i: int, img: np.ndarray, boxes: list, idx: np.ndarray | None = None) -> None:
        if idx is None:
            idx = _glyph_pixels(img, boxes)
        self.cur = _Span(f0, i, boxes, idx, img.ravel()[idx].astype(np.int16), [img], n0=len(idx))

    def _close(self, f1: int) -> None:
        sp = self.cur
        if sp is not None and f1 >= sp.f0:
            sp.f1 = f1
            self.emit(_Span(sp.f0 + self.off, sp.f1 + self.off, sp.boxes, sp.idx, sp.ref, sp.frames))
        self.cur = None

    def _really_new(self, sp: _Span, img: np.ndarray, boxes: list) -> bool:
        """2.4.5: khung chữ tìm lại có thêm "nét" ngoài khung cũ – chữ mới hiện thêm thật, hay vật sáng viền tối của cảnh
        (giáp, vòng sáng) chạy vào dính với khung chữ? Đọc thử: chỉ là chữ mới khi đọc ra chữ Hán (chữ / số khi câu cũ
        không có chữ Hán) mà câu cũ không có. Trước đây không đọc -> vật trong cảnh chạm chữ là câu bị cắt đôi, mảnh sau
        đọc thiếu chữ / ra rác (câu mất / câu 0,4 s)."""
        if self.read is None:
            return True
        try:
            if sp.text is None:
                sp.text = self.read(sp.frames[0], sp.boxes) if sp.frames else ""
            now = self.read(img, boxes)
        except Exception:  # noqa: BLE001 – đọc thử lỗi: như trước (coi là chữ mới)
            return True
        old = sp.text or ""
        han = bool(_CJK.search(old))
        core = (lambda t: "".join(_CJK.findall(t))) if han else \
            (lambda t: "".join(_CJK.findall(t) + _ALNUM.findall(t)).lower())
        a, b = core(old), core(now)
        if not b:
            return False                 # khung mới không đọc ra chữ: chỉ là vật trong cảnh
        if not a or (a in b and len(b) > len(a)):
            return True                  # chữ hiện thêm sau câu cũ / dòng mới cạnh dòng cũ
        # chữ không có trong câu cũ: ≥ 2 chữ mới là chữ mới thật (khung dính vật trong cảnh hay đọc sai 1 chữ: 你 -> 饮)
        same = sum(m.size for m in difflib.SequenceMatcher(None, a, b, autojunk=False).get_matching_blocks())
        return len(b) - same >= 2

    def _same_text(self, sp: _Span, img: np.ndarray, sig, i: int) -> bool:
        """2.4.5: nét đang theo dõi đổi nhưng có thể chỉ do NỀN – khung chữ lúc mở câu dính vật sáng viền tối của cảnh
        (giáp, vòng sáng…) nên "nét chữ" có cả điểm của vật đó; vật chuyển động -> nét "đổi" -> câu bị cắt đôi (câu
        0,4 s, mảnh câu đọc thiếu chữ). Tìm chữ lại trên khung này: nét chữ của khung này có y nguyên ở khung gần nhất
        của câu (cùng chỗ, cùng độ sáng) -> vẫn câu đó: theo dõi tiếp bằng nét mới."""
        if not sp.frames:
            return False
        boxes = self._det(img, sig)
        if not boxes or not sp.boxes:
            return False
        # khung chữ chính bây giờ phải nằm ĐÚNG chỗ khung chữ chính của câu cũ (không phải chỉ còn watermark / chữ cảnh
        # ở chỗ khác); khung cũ dính vật trong cảnh thì to hơn khung mới -> so phần chung với khung NHỎ hơn
        def area(b):
            x0, y0, x1, y1 = _bbox(b)
            return max(0.0, x1 - x0) * max(0.0, y1 - y0)
        bo, bn = max(sp.boxes, key=area), max(boxes, key=area)
        (a0, b0, a1, b1), (c0, d0, c1, d1) = _bbox(bo), _bbox(bn)
        inter = max(0.0, min(a1, c1) - max(a0, c0)) * max(0.0, min(b1, d1) - max(b0, d0))
        if inter < 0.6 * max(1.0, min(area(bo), area(bn))):
            return False
        idx = _glyph_pixels(img, boxes)
        if len(idx) < 40:
            return False
        ref = img.ravel()[idx].astype(np.int16)
        if _persist(sp.frames[-1], idx, ref) < KEEP:
            return False
        sp.idx, sp.ref, sp.n0, sp.checks = idx, ref, len(idx), 0
        sp.f1 = i
        sp.add(img)
        self.n_same = getattr(self, "n_same", 0) + 1
        return True

    def check(self, i: int, img: np.ndarray, pend: list) -> None:
        sig = _text_signature(img)
        while True:
            sp = self.cur
            if sp is None:                                    # đang không có chữ
                if self.vsig is not None and not _changed(sig, self.vsig):
                    return
                boxes = self._det(img, sig)
                if not boxes:
                    return
                idx = _glyph_pixels(img, boxes)
                f0 = self._start(pend, i, img, boxes, idx) if len(idx) else i
                self._open(f0, i, img, boxes, idx)
                return
            if len(sp.idx) and _persist(img, sp.idx, sp.ref) >= KEEP:
                if self.vsig is None or _changed(sig, self.vsig):   # nét đổi: có thêm chữ mới hiện ra không?
                    boxes = self._det(img, sig)
                    new = _glyph_pixels(img, boxes, skip=sp.boxes)
                    lh = float(np.median([_bbox(b)[3] - _bbox(b)[1] for b in sp.boxes])) if sp.boxes else 20.0
                    if len(new) >= max(40.0, 0.25 * lh * lh) and self._really_new(sp, img, boxes):
                        # thêm ít nhất ~1 chữ (khung lệch vài điểm thì không)
                        f0 = self._start(pend, i, img, boxes, new)
                        self._close(f0 - 1)                       # (khung i có chữ mới: không vào câu cũ)
                        self._open(f0, i, img, boxes)
                        return
                sp.f1 = i
                sp.add(img)
                sp.refine(img)
                return
            if self._same_text(sp, img, sig, i):
                return
            # nét chữ đổi -> câu kết thúc ở khung cuối còn nét cũ (chia đôi), xét lại từ khung đầu tiên đổi
            lo, hi = -1, len(pend)
            while hi - lo > 1:
                m = (lo + hi) // 2
                if len(sp.idx) and _persist(pend[m][1], sp.idx, sp.ref) >= KEEP:
                    lo = m
                else:
                    hi = m
            self._close(pend[lo][0] if lo >= 0 else sp.f1)
            self.vsig = None
            pend = pend[hi:] if hi < len(pend) else []


EQ_KEYS = ("brightness", "contrast", "gamma")   # bộ đọc chữ đọc ẢNH XÁM -> bão hoà màu không có tác dụng (bỏ)
EQ_NEUTRAL = {"brightness": 0.0, "contrast": 1.0, "gamma": 1.0, "sharpen": False}
# mẫu làm rõ chữ (chọn là áp ngay): (mã, tên, giá trị | None = tắt)
EQ_PRESETS = [
    ("off", "Ảnh gốc – không chỉnh", None),
    ("white_dark", "Chữ trắng viền đen – nền tối", {"brightness": -0.30, "contrast": 1.80, "gamma": 0.50,
                                                    "sharpen": False}),
    ("white_bright", "Chữ trắng viền đen – nền sáng / đổi liên tục", {"brightness": -0.10, "contrast": 1.40,
                                                                      "gamma": 0.80, "sharpen": False}),
    ("dark_text", "Chữ đen / màu tối – nền sáng", {"brightness": 0.15, "contrast": 1.60, "gamma": 1.60,
                                                   "sharpen": False}),
    ("blurry", "Chữ mờ / nhoè – tăng nét", {"brightness": 0.0, "contrast": 1.30, "gamma": 0.90, "sharpen": True}),
]
EQ_PRESET = EQ_PRESETS[1][2]


def eq_values(settings: dict | None) -> dict:
    """Giá trị làm rõ chữ trước khi OCR (Cài đặt ocr_eq_<khoá> + ocr_eq_sharpen, chưa đặt -> mẫu chữ trắng nền tối)."""
    st = settings or {}
    out = {k: float(st.get(f"ocr_eq_{k}", EQ_PRESET[k])) for k in EQ_KEYS}
    out["sharpen"] = bool(st.get("ocr_eq_sharpen", EQ_PRESET["sharpen"]))
    return out


def eq_preset_of(vals: dict, on: bool = True) -> str:
    """Mã mẫu khớp giá trị đang đặt (không khớp mẫu nào -> "custom")."""
    if not on:
        return "off"
    for k, _n, v in EQ_PRESETS:
        if v and all(abs(float(vals.get(x, 0)) - v[x]) < 1e-3 for x in EQ_KEYS) and \
                bool(vals.get("sharpen")) == v["sharpen"]:
            return k
    return "custom"


def eq_filter(settings: dict | None) -> str:
    """Lọc ảnh làm rõ chữ trước khi quét OCR (2.1.36, Cài đặt ocr_eq – chỉ ảnh đưa vào bộ đọc chữ, video xuất không đổi):
    eq độ sáng / tương phản / gamma như tab Chỉnh sửa → Màu sắc (+ làm nét). Mẫu chữ trắng -0,30 / 1,80 / 0,50: nền tối
    hẳn, chữ trắng viền đen nổi rõ. Tắt / giá trị trung tính -> ""."""
    st = settings or {}
    if not st.get("ocr_eq"):
        return ""
    v = eq_values(st)
    parts = []
    if any(abs(v[k] - EQ_NEUTRAL[k]) > 1e-6 for k in EQ_KEYS):
        parts.append(f"eq=brightness={v['brightness']:.3f}:contrast={v['contrast']:.3f}:gamma={v['gamma']:.3f}")
    if v["sharpen"]:
        parts.append("unsharp=5:5:0.8:3:3:0.0")
    return ",".join(parts)


def preview_scan(src: str | Path, t: float, region: dict | None, settings: dict | None) -> tuple[bytes, bytes]:
    """Ảnh vùng quét tại giây t: (ảnh gốc, ảnh bộ đọc chữ thấy – đã lọc + xám) dạng PNG – nút "Xem thử" tab Tự động."""
    r = region or {"x": 0.0, "y": 0.66, "w": 1.0, "h": 0.34}
    crop = (f"crop=trunc(iw*{float(r['w']):.4f}/2)*2:trunc(ih*{float(r['h']):.4f}/2)*2:"
            f"trunc(iw*{float(r['x']):.4f}):trunc(ih*{float(r['y']):.4f})")
    out = []
    for vf in (crop, ",".join(x for x in (crop, eq_filter({**(settings or {}), "ocr_eq": True}), "format=gray") if x)):
        cmd = [ffmpeg_bin(), "-hide_banner", "-loglevel", "error", "-ss", f"{max(0.0, t):.3f}", "-i", str(src),
               "-frames:v", "1", "-an", "-vf", vf, "-f", "image2pipe", "-vcodec", "png", "pipe:1"]
        out.append(subprocess.run(cmd, capture_output=True, creationflags=_NO_WINDOW).stdout)
    return out[0], out[1]


def _scan(src: str, rect, size, fps: float, f0: int, count: int | None, step: int, hw: list, detect, emit,
          stop: threading.Event, seen: list, pre: str = "", tm: dict | None = None, colorf=None,
          read=None) -> tuple[int, int]:
    """Giải mã 1 khúc video (từ khung f0, count khung) chỉ lấy dải phụ đề ảnh xám -> các câu (_Span, số khung tính từ
    đầu video). Trả (số khung đã đọc, số lần tìm chữ). pre: lọc ảnh làm rõ chữ (eq_filter) trước khi chuyển xám.
    colorf (2.1.44, ocrcolor.ColorFilter): giải mã ẢNH MÀU, mỗi khung chỉ giữ nét đúng màu chữ / viền đã chọn (còn lại
    xám đều) rồi mới đưa cho bộ theo dõi nét + bộ đọc. tm["decode"] cộng thời gian chờ giải mã, tm["color"] thời gian lọc."""
    import time as _time

    x, y, w, h = rect
    ow, oh = size
    vf = f"fps={fps:.6f},crop={w}:{h}:{x}:{y}" + (f",scale={ow}:{oh}:flags=area" if (ow, oh) != (w, h) else "") + \
         (f",{pre}" if pre and colorf is None else "") + (",format=bgr24" if colorf is not None else ",format=gray")
    cmd = [ffmpeg_bin(), "-hide_banner", "-loglevel", "error", *hw]
    if f0:
        cmd += ["-ss", f"{f0 / fps:.4f}"]
    cmd += ["-i", src, "-an", "-sn", "-dn", "-vf", vf]
    if count:
        cmd += ["-frames:v", str(count)]
    cmd += ["-f", "rawvideo", "-pix_fmt", "bgr24" if colorf is not None else "gray", "pipe:1"]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, creationflags=_NO_WINDOW)
    tr = _Tracker(detect, emit, f0, read)
    nbytes = ow * oh * (3 if colorf is not None else 1)
    pend, i = [], -1
    wait = 0.0
    ctime = [0.0]

    def timed(raw):
        t1 = _time.perf_counter()
        try:
            return colorf(raw)
        finally:
            ctime[0] += _time.perf_counter() - t1
    try:
        while not stop.is_set():
            t0 = _time.perf_counter()
            buf = proc.stdout.read(nbytes)  # type: ignore[union-attr]
            wait += _time.perf_counter() - t0
            if len(buf) < nbytes:
                break
            i += 1
            if colorf is not None:
                raw = np.frombuffer(buf, np.uint8).reshape(oh, ow, 3)
                if i % step:              # khung chờ: chỉ lọc màu khi bộ theo dõi cần tới (chia đôi tìm giờ hiện / tắt)
                    pend.append(_LazyFrame(i, raw, timed))
                    img = None
                else:
                    img = timed(raw)
            else:
                img = np.frombuffer(buf, np.uint8).reshape(oh, ow)
            if img is None:
                pass
            elif i % step:
                pend.append((i, img))
            else:
                tr.check(i, img, pend)
                pend = []
            if i % 32 == 0:
                seen[0] += 32
        if pend and not stop.is_set():       # khung cuối khúc chưa phải mốc kiểm tra -> xét để câu cuối đúng giờ
            li, limg = pend.pop()
            tr.check(li, limg, pend)
        if tr.cur is not None and not stop.is_set():
            tr._close(tr.cur.f1)
    finally:
        proc.kill()
        proc.wait()
        if tm is not None:
            with _TM_LOCK:
                tm["decode"] = tm.get("decode", 0.0) + wait
                if colorf is not None:
                    tm["color"] = tm.get("color", 0.0) + ctime[0]
    return i + 1, tr.n_det


_TM_LOCK = threading.Lock()


class _LazyFrame:
    """Khung chờ (giữa 2 mốc kiểm tra) khi lọc màu: chỉ lọc khi bộ theo dõi đọc tới (phần lớn khung không bao giờ cần)
    – dùng như (số khung, ảnh)."""
    __slots__ = ("i", "raw", "f", "img")

    def __init__(self, i: int, raw: np.ndarray, f):
        self.i, self.raw, self.f, self.img = i, raw, f, None

    def __getitem__(self, k: int):
        if k == 0:
            return self.i
        if k != 1:
            raise IndexError(k)
        if self.img is None:
            self.img, self.raw = self.f(self.raw), None
        return self.img

    def __iter__(self):
        yield self[0]
        yield self[1]

    def __len__(self) -> int:
        return 2


def _clean_image(frames: list, clean: bool) -> tuple[np.ndarray, np.ndarray, np.ndarray | None]:
    """F: chữ phụ đề đứng yên, nền chuyển động. Trả (ảnh sạch, khung gốc giữa câu, ảnh "nền đen" | None):
      * ảnh sạch: điểm đứng yên qua các khung của câu (chữ + viền) giữ nguyên khung giữa (sắc nét), điểm chuyển động
        thay bằng trung bình các khung (nền nhoè thành mảng mờ, nhiễu giảm) -> bộ đọc ít bị nền rối làm sai;
      * ảnh nền đen: điểm chuyển động tô đen -> chữ cùng màu với nền đang chuyển động cũng lộ ra; chỉ dùng để TÌM chữ
        (tô đen làm vỡ nét chữ có điểm bị nén làm dao động, đọc trên ảnh này kém hơn).
    Cảnh đứng yên / ít khung: ảnh sạch = khung gốc, không có ảnh nền đen. Bỏ khung đầu / cuối câu (có thể đang chuyển
    cảnh / chữ mờ dần)."""
    import cv2

    n = len(frames)
    raw = frames[n // 2]
    if not clean or n < 3:
        return raw, raw, None
    use = frames[1:-1] if n >= 5 else frames
    idx = np.linspace(0, len(use) - 1, min(STACK, len(use))).round().astype(int)
    st = np.stack([use[k] for k in idx])
    moving = (st.max(axis=0) - st.min(axis=0)) > MOVE_DIFF
    if moving.mean() < MOVE_SHARE:
        return raw, raw, None
    # điểm "đứng yên" lẻ tẻ do nhiễu tình cờ không đổi -> bỏ (cụm < SPECK điểm; nét chữ mảnh nhưng liền nét vẫn còn –
    # mở ảnh bằng ô 2×2 / 3×3 làm mất cả nét chữ cỡ nhỏ); rồi nới 1 điểm giữ mép chữ khử răng cưa
    cnt, lab, stats, _ = cv2.connectedComponentsWithStats((~moving).astype(np.uint8), connectivity=8)
    big = np.zeros(cnt, np.uint8)
    big[1:] = stats[1:, cv2.CC_STAT_AREA] >= SPECK
    keep = cv2.dilate(big[lab], np.ones((3, 3), np.uint8)) > 0
    clean_img = np.where(keep, raw, st.mean(axis=0)).round().astype(np.uint8)
    return clean_img, raw, np.where(keep, raw, 0).astype(np.uint8)


def _cover(boxes: list) -> float:
    """Tổng diện tích các khung chữ."""
    return float(sum((_bbox(b)[2] - _bbox(b)[0]) * (_bbox(b)[3] - _bbox(b)[1]) for b in boxes))


BIG_BELOW = 0.93         # câu đọc kém chắc hơn mức này (theo chữ kém nhất) -> đọc lại bằng bộ đọc lớn cuối lần quét
BIG_MAX = 4000           # giữ ảnh chữ tối đa chừng này câu để đọc lại


class _Reader:
    """Đọc chữ từng câu (_Span) – chỉ bước ĐỌC DÒNG trên các khung chữ bộ theo dõi đã tìm (C: không tìm chữ lại),
    trên ảnh chữ sạch (F); kém chắc thì đọc thêm khung gốc rồi chọn bản tốt hơn. 2.1.37: đọc kèm độ chắc từng chữ + chữ
    ứng viên; câu kém chắc giữ ảnh chữ để bộ đọc lớn đọc lại cuối lần quét (keep_weak)."""

    def __init__(self, eng, clean: bool, min_score: float, low_score: float, keep_weak: bool = False):
        self.eng, self.clean, self.min_score, self.low_score = eng, clean, min_score, low_score
        self.keep_weak = keep_weak
        self.lock = threading.Lock()
        self.n = {"ocr": 0, "cleaned": 0, "spans": 0}
        self.weak: list = []         # [(chỉ số câu, [ảnh dòng chữ], khung)]
        self.t = 0.0
        self.hs: list[float] = []    # chiều cao khung các dòng đọc rất chắc (2.4.5: nhận ra khung dính vật trong cảnh)

    def _keep(self, items: list) -> list:
        out = []
        for it in items:
            pts, t, s = it
            if s < self.low_score or not (_CJK.search(t) or (s >= NONHAN_MIN and _ALNUM.search(t))):
                continue
            st = t.strip()
            ch = getattr(it, "ch", None)
            if ch is not None and len(ch) == len(t) and st != t:
                lead = len(t) - len(t.lstrip())
                ch = ch[lead:lead + len(st)]
            out.append(_mk(pts, st, s, ch if ch is not None and len(ch) == len(st) else None))
        return _reading_order(out)

    def _read(self, img: np.ndarray, boxes: list) -> list:
        from .ocr_engine import _crop

        self.n["ocr"] += 1
        with self.lock:
            recs = self.eng.recognize_ex([_crop(img, b) for b in boxes])
        return self._keep([_mk(np.asarray(b, np.float32), t, s, d) for b, (t, s, d) in zip(boxes, recs)])

    def read(self, sp: _Span, k: int = -1) -> list:
        import time as _time

        t0 = _time.perf_counter()
        try:
            return self._read_span(sp, k)
        finally:
            self.t += _time.perf_counter() - t0

    def _read_span(self, sp: _Span, k: int) -> list:
        self.n["spans"] += 1
        img, raw, dark = _clean_image(sp.frames, self.clean)
        boxes = sp.boxes
        reads = [(self._read(img, boxes), img, boxes)]
        if dark is not None:
            self.n["cleaned"] += 1
            # nền đen: chữ cùng màu với nền chuyển động (khó thấy trên khung gốc) lộ ra -> tìm chữ lại; thấy khung rộng
            # hơn thì đọc THÊM bằng khung đó rồi chọn bản tốt hơn. 2.4.5: trước đây khung mới THAY khung chữ – nền có vật
            # sáng viền tối chuyển động (giáp, vòng sáng) làm bộ tìm chữ ra 1 khung cả dải -> đọc ra rác, mất cả câu
            cb = self.eng.detect(dark, SCAN_SIDE)
            if cb and _cover(cb) > 1.15 * _cover(boxes):
                reads.append((self._read(img, cb), img, cb))
                self.n["redetect"] = self.n.get("redetect", 0) + 1

        def q(items):          # nhiều chữ + chắc chắn (bình phương: 1 bản dài mà kém chắc không thắng bản ngắn mà chắc);
            # 2.4.5: tính theo chữ Hán (chữ / số Latin nửa) – khung sai đọc hình nền ra chuỗi ký hiệu "(())(…)()" / mẩu
            # "C" "0" rời không thắng bản đọc đúng câu
            return sum(max(0.25, len(_CJK.findall(t)) + 0.5 * len(_ALNUM.findall(t))) * s * s for _, t, s in items)
        reads.sort(key=lambda r: q(r[0]), reverse=True)
        # 2.4.5: khung chữ (tìm lúc câu mới hiện) dính vật sáng viền tối của cảnh -> cao hẳn dòng chữ thường gặp, đọc ra
        # mẩu câu / chữ rác; hoặc đọc kém -> tìm chữ lại trên ẢNH SẠCH (vật chuyển động đã nhoè) rồi đọc thêm
        med = float(np.median(self.hs)) if len(self.hs) >= 5 else 0.0
        tall = med > 0 and any(_bbox(b)[3] - _bbox(b)[1] > 1.35 * med for b in boxes)
        first = reads[0][0]
        if img is not raw and (tall or not first or min(s for _, _, s in first) < WEAK):
            cb = self.eng.detect(img, SCAN_SIDE)
            if cb and abs(_cover(cb) - _cover(boxes)) > 0.15 * max(1.0, _cover(boxes)):
                reads.append((self._read(img, cb), img, cb))
                self.n["reclean"] = self.n.get("reclean", 0) + 1
                reads.sort(key=lambda r: q(r[0]), reverse=True)
        boxes = reads[0][2]
        weak = not reads[0][0] or min(s for _, _, s in reads[0][0]) < WEAK
        if img is not raw and (weak or boxes is not sp.boxes):
            reads.append((self._read(raw, boxes), raw, boxes))      # đọc thêm khung gốc rồi chọn bản tốt hơn
        elif weak and len(sp.frames) > 2:
            reads.append((self._read(sp.frames[0], boxes), sp.frames[0], boxes))
        reads.sort(key=lambda r: q(r[0]), reverse=True)
        best, bimg, boxes = reads[0]
        others = [t for r, _, _ in reads[1:] for _, t, _ in r]
        # chữ kém chắc (< min_score) chỉ giữ khi lần đọc kia ra giống (chữ thật), không thì bỏ (rác thoáng qua)
        out = [it for it in best if it[2] >= self.min_score or any(_similar(it[1], o) >= 0.8 for o in others)]
        for it in out:
            if it[2] >= 0.9 and _CJK.search(it[1]):
                self.hs.append(_bbox(it[0])[3] - _bbox(it[0])[1])
        if len(self.hs) > 400:
            self.hs = self.hs[-200:]
        if self.keep_weak and k >= 0 and len(self.weak) < BIG_MAX and boxes:
            low = min((min(c[1] for c in it.ch) if it.ch else it[2]) for it in out) if out else 0.0
            if low < BIG_BELOW:
                from .ocr_engine import _crop

                self.weak.append((k, [_crop(bimg, b).copy() for b in boxes], list(boxes)))
        return out


def big_model(settings: dict | None, eng=None) -> str:
    """B3 (2.1.37): bộ đọc lớn đã tải dùng để đọc lại câu kém chắc ("" = không có / đang dùng sẵn bộ lớn / tắt)."""
    from .ocr_engine import model_ready

    st = settings or {}
    if not st.get("ocr_big", True):
        return ""
    if eng is not None and (getattr(eng, "kind", "") != "v3" or getattr(eng, "key", "") != "v6_small"):
        return ""
    for k in ("v5_server", "v6_medium"):
        try:
            if model_ready(k):
                return k
        except Exception:  # noqa: BLE001
            pass
    return ""


def _chunks(total: float) -> int:
    import os

    if total < 120:
        return 1
    return 3 if total >= 1200 and (os.cpu_count() or 4) >= 8 else 2


def extract_hardsubs(src: str | Path, region=None, fps: float = 4.0, min_score: float = 0.6,
                     progress: Callable[[float, str], None] | None = None,
                     cancel: Callable[[], bool] | None = None,
                     boxes_out: str | Path | None = None, stats: dict | None = None,
                     low_score: float = 0.45, settings: dict | None = None,
                     windows: list | None = None) -> list[Segment]:
    """Đọc phụ đề in sẵn trong vùng `region` (2.1.31):
      A giải mã bằng card (NVDEC / D3D11) chỉ lấy dải phụ đề ảnh xám; phim dài chia 2–3 khúc giải mã song song; giải mã
        và đọc chữ chạy ở luồng riêng;
      B chỉ đọc khi nét chữ đổi (bỏ "mỗi giây đọc lại 1 lần"), dải trống không đọc, câu y hệt câu vừa đọc dùng lại;
      C bộ đọc RapidOCR 3 (card nếu có), hàng chữ vẫn ở chỗ cũ thì đọc thẳng dòng không qua bước tìm chữ;
      D kiểm tra nét chữ `fps` lần mỗi giây rồi chia đôi tìm đúng khung chữ hiện / tắt -> giờ câu đúng tới 1 khung;
      E mô hình PP-OCRv6 / PP-OCRv5 (Cài đặt ocr_model);
      F ảnh chữ sạch dựng từ nhiều khung của câu (nền chuyển động bị loại).
    region có line_y / line_h (dòng phụ đề chính, từ detect_subtitle_region) thì chỉ giữ khối dòng chữ quanh dòng đó;
    region "exact" (khung người dùng kéo) -> quét đúng khung đó, giữ mọi chữ trong khung.
    windows [(giây đầu, giây cuối)] (2.1.35 – tách phụ đề theo đoạn): chỉ quét các đoạn này (mỗi đoạn 1 khúc giải mã,
    tối đa 2–3 khúc chạy cùng lúc), giờ câu vẫn tính từ đầu video.
    Cài đặt ocr_big: câu kém chắc đọc lại bằng bộ đọc lớn đã tải (B3, 2.1.37). Câu có extra ocr_cc (độ chắc từng chữ) /
    ocr_alt (chữ ứng viên). stats["t"] = thời gian từng công đoạn (A1). 2.1.42: đã BỎ HẲN phần lọc logo chữ (2.1.37) –
    nhận nhầm câu thoại thành logo làm mất câu."""
    import bisect
    import queue
    import time as _time

    from . import ffmpeg as ff

    src = str(src)
    st = settings or {}
    info = probe(src)
    if not info.has_video:
        raise RuntimeError("File không có hình ảnh.")
    reg_d = dict(region) if isinstance(region, dict) else {}
    region = Region.from_any(region)
    total = info.duration or 1
    if progress:
        progress(0.0, "Đang nạp mô hình OCR…")
    eng = get_engine(st)
    W, H = info.width, info.height
    rect = region.to_px(W, H)
    x, y, w, h = rect
    scale = min(1.0, MAX_W / max(1, w))
    size = (max(2, int(w * scale) // 2 * 2), max(2, int(h * scale) // 2 * 2))
    F = min(SCAN_FPS_MAX, info.fps if info.fps and info.fps > 1 else 25.0)
    n_total = int(total * F) + 1
    step = max(1, int(round(F / max(1.0, float(fps or 4)))))
    hw = ff.hwdec_args(src, info.vcodec) if st.get("ocr_hwdec", True) else []
    covered = total
    if windows:
        wins = sorted((max(0.0, float(a)), min(total, float(b))) for a, b in windows if float(b) - float(a) > 0.05)
        if not wins:
            raise ValueError("Không có đoạn nào để quét.")
        bounds = [(int(a * F), max(1, int(round((b - a) * F)) + 1)) for a, b in wins]
        n_total = sum(c for _, c in bounds)
        covered = sum(b - a for a, b in wins)
        nch = min(len(bounds), max(2, _chunks(total)))     # số luồng giải mã cùng lúc
    else:
        nch = _chunks(total)
        bounds = [(k * n_total // nch, (k + 1) * n_total // nch - k * n_total // nch if k < nch - 1 else None)
                  for k in range(nch)]
    todo = list(bounds)
    todo_lock = threading.Lock()
    from . import ocrcolor

    colorf = ocrcolor.get(st)              # 2.1.44: chỉ lấy chữ đúng màu chữ / viền (đo trên màu GỐC – bỏ làm rõ chữ)
    pre = "" if colorf is not None else eq_filter(st)
    tm: dict[str, float] = {}               # A1: thời gian từng công đoạn (giây, cộng các luồng)

    # hàng đợi có giới hạn: đọc chữ chậm hơn giải mã (máy chỉ CPU) thì luồng giải mã chờ – mỗi câu giữ ≤ 16 khung mẫu,
    # không giới hạn thì phim dài có thể dồn hàng nghìn câu (vài GB RAM)
    q: queue.Queue = queue.Queue(maxsize=QUEUE_MAX)
    stop = threading.Event()

    def put(sp: _Span) -> None:
        while not stop.is_set():
            try:
                q.put(sp, timeout=0.2)
                return
            except queue.Full:
                continue
    seen = [0]
    errs: list = []
    used_hw = [bool(hw)]

    det_lock = threading.Lock()
    n_det = [0]

    def detect(img: np.ndarray) -> list:
        with det_lock:
            t0 = _time.perf_counter()
            r = eng.detect(img, SCAN_SIDE)
            tm["detect"] = tm.get("detect", 0.0) + _time.perf_counter() - t0
            return r

    def read_text(img: np.ndarray, boxes: list) -> str:
        """Đọc thử (bộ theo dõi xem có chữ mới hiện thêm thật không – 2.4.5): chữ các dòng đọc chắc ≥ 0,7."""
        from .ocr_engine import _crop

        t0 = _time.perf_counter()
        recs = eng.recognize([_crop(img, b) for b in boxes]) if boxes else []
        tm["verify"] = tm.get("verify", 0.0) + _time.perf_counter() - t0
        return "".join(t for t, sc in recs if sc >= 0.7)

    def run():
        try:
            while not stop.is_set():
                with todo_lock:
                    if not todo:
                        return
                    f0, count = todo.pop(0)
                got, nd = _scan(src, rect, size, F, f0, count, step, hw if used_hw[0] else [], detect, put, stop, seen,
                                pre, tm, colorf, read_text)
                if got == 0 and used_hw[0] and not stop.is_set():   # card không giải mã được file này -> giải mã CPU
                    used_hw[0] = False
                    got, nd = _scan(src, rect, size, F, f0, count, step, [], detect, put, stop, seen, pre, tm, colorf,
                                    read_text)
                n_det[0] += nd
        except Exception as e:  # noqa: BLE001
            errs.append(e)
            stop.set()
    threads = [threading.Thread(target=run, daemon=True, name=f"trami-ocr-scan{k}") for k in range(nch)]
    t_start = _time.time()
    for th in threads:
        th.start()
    big_key = big_model(st, eng)
    reader = _Reader(eng, bool(st.get("ocr_clean", True)), min_score, low_score, keep_weak=bool(big_key))
    spans: list[tuple[int, int, list]] = []
    last = 0.0
    try:
        while True:
            if cancel and cancel():
                raise Cancelled()
            try:
                sp = q.get(timeout=0.2)
            except queue.Empty:
                if not any(th.is_alive() for th in threads) and q.empty():
                    break
                sp = None
            if sp is not None:
                spans.append((sp.f0, sp.f1, reader.read(sp, len(spans))))
            now = _time.time()
            if progress and now - last > 0.5:
                last = now
                done = min(0.999, seen[0] / max(1, n_total))
                progress(done, f"OCR phụ đề {int(done * covered)}/{int(covered)} giây · {reader.n['spans']} câu · "
                               f"{eng.label}" + (" · giải mã bằng card" if used_hw[0] else ""))
    finally:
        stop.set()                      # xong / Huỷ / lỗi: dừng mọi luồng giải mã còn chạy (đã xong thì vô hại)
        for th in threads:
            th.join(timeout=5)
    if errs:
        raise errs[0]
    tm["read"] = reader.t
    # B3: câu kém chắc -> bộ đọc lớn đọc lại đúng ảnh chữ đã giữ (quét cả phim bằng bộ nhỏ cho nhanh)
    big_st = {"key": big_key, "lines": 0, "changed": 0}
    if big_key and reader.weak:
        from .ocr_engine import Engine

        t0 = _time.time()
        if progress:
            progress(0.995, f"Đọc lại {len(reader.weak)} câu kém chắc bằng bộ đọc lớn…")
        try:
            big = Engine(big_key, str(st.get("ocr_device") or "auto"))
            for k, crops, bxs in reader.weak:
                if cancel and cancel():
                    raise Cancelled()
                recs = big.recognize_ex(crops)
                items = [it for it in reader._keep([_mk(np.asarray(b, np.float32), t, sc, d)
                                                     for b, (t, sc, d) in zip(bxs, recs)]) if it[2] >= min_score]
                f0_, f1_, old = spans[k]
                big_st["lines"] += 1
                if not items:
                    continue
                mo = float(np.mean([it[2] for it in old])) if old else 0.0
                if float(np.mean([it[2] for it in items])) >= mo - 0.1:
                    if "".join(i[1] for i in items) != "".join(o[1] for o in old):
                        big_st["changed"] += 1
                    spans[k] = (f0_, f1_, items)
            del big
        except Cancelled:
            raise
        except Exception as e:  # noqa: BLE001 – bộ đọc lớn lỗi: giữ kết quả bộ nhỏ
            big_st["error"] = f"{type(e).__name__}: {e}"[:200]
        tm["big"] = _time.time() - t0
    spans.sort(key=lambda s: s[0])
    times = [((a / F), (b + 1) / F) for a, b, _ in spans]
    reads = [items for _, _, items in spans]
    mids = [(a + b) / 2 for a, b in times]

    # chữ trong cảnh đang chuyển động (vật có chữ bay ngang qua) lọt vào vùng quét: cùng chữ mà mỗi lần đọc ở 1 chỗ
    # khác -> bỏ; phụ đề đứng yên suốt lúc hiện
    moved = []
    for i, items in enumerate(reads):
        lo, hi = bisect.bisect_left(mids, mids[i] - 1.0), bisect.bisect_right(mids, mids[i] + 1.0)
        keep = []
        for it in items:
            b = _bbox(it[0])
            hb = b[3] - b[1]
            tol = 0.2 * hb
            # 2.4.5: chỉ so với lần đọc có khung cao gần bằng – khung dính vật trong cảnh (cao hẳn) đọc ra mẩu câu không
            # phải bằng chứng chữ đang di chuyển (trước đây làm mất cả câu đọc đúng bên cạnh)
            same = [ob for j in range(lo, hi) if j != i for o in reads[j] if _similar(o[1], it[1]) >= 0.5
                    for ob in [_bbox(o[0])] if 0.7 * hb <= ob[3] - ob[1] <= 1.4 * hb]
            if same and not any(abs(ob[1] - b[1]) <= tol and abs(ob[3] - b[3]) <= tol for ob in same):
                continue
            keep.append(it)
        moved.append(keep)
    reads = moved

    anchor_y = line_h = None
    if reg_d.get("line_y") is not None and reg_d.get("line_h") and not reg_d.get("exact"):
        anchor_y = (float(reg_d["line_y"]) * H - y) * scale
        line_h = float(reg_d["line_h"]) * H * scale
    # watermark tên kênh (chữ Y HỆT đứng yên 1 chỗ ≥ 30 % video, hiện cùng lúc câu khác – có từ 2.0.23, không phải
    # chức năng logo chữ đã bỏ ở 2.1.42) + chữ nhỏ hơn hẳn chữ phụ đề (hoạ tiết áo đọc thành "米")
    marks = _static_marks_spans(times, reads, covered)
    # chiều cao chữ phụ đề = trung vị theo THỜI GIAN hiện (1 đoạn dài nặng hơn 1 đoạn ngắn – như khi đếm từng khung)
    hw_ = [(_line_h(it), b - a) for (a, b), items in zip(times, reads) for it in items
           if not _is_mark(it, marks) and _CJK.search(it[1])]
    h_sub = 0.0
    if len(hw_) >= 3 and sum(d for _, d in hw_) >= 2.0:
        hw_.sort()
        acc, half = 0.0, sum(d for _, d in hw_) / 2
        for hh, d in hw_:
            acc += d
            if acc >= half:
                h_sub = float(hh)
                break
    band = _han_band(reads, marks, h_sub, size[0])
    # 2.1.44: phụ đề căn giữa – khối chữ cùng hàng mà cách xa cụm chữ ở giữa khung hình (biển hiệu ở góc) không gộp vào
    # câu. Chỉ khi giữa khung hình nằm trong vùng quét (khung kéo lệch hẳn 1 bên -> không xét)
    cx = (W / 2 - x) * scale
    center_on = bool(st.get("ocr_center", True)) and 0 < cx < size[0]
    center_cut = [0]
    kept = []
    for items in reads:
        keep = [it for it in items if not _is_mark(it, marks)
                and (not h_sub or _line_h(it) >= 0.7 * h_sub)]
        han = [it for it in keep if _CJK.search(it[1])]
        keep = [it for it in keep if _CJK.search(it[1]) or _stray_ok(it, han, h_sub, band)]
        keep = _anchor_block(keep, anchor_y, line_h or h_sub)
        if center_on and len(keep) > 1:
            n0 = len(keep)
            keep = _center_keep(keep, cx)
            center_cut[0] += n0 - len(keep)
        kept.append(keep)

    from .ocrfix import line_conf

    boxes: list[dict] = []
    raw: list[tuple] = []
    for (a, b), items in zip(times, kept):
        text = "".join(it[1] for it in items)
        chars = None
        if items and all(getattr(it, "ch", None) is not None and len(it.ch) == len(it[1]) for it in items):
            chars = [c for it in items for c in it.ch]
        if items:
            bbs = [_bbox(it[0]) for it in items]
            wid = max(bb[2] for bb in bbs) - min(bb[0] for bb in bbs)
            lh = float(np.median([bb[3] - bb[1] for bb in bbs]))
        else:
            wid = lh = 0.0
        raw.append((a, b, text, float(np.mean([it[2] for it in items])) if items else 0.0, wid, lh, chars))
        if items:
            bxs = []
            for pts, _, _ in items:
                px = pts / scale
                bxs.append([int(px[:, 0].min() + x), int(px[:, 1].min() + y),
                            int(px[:, 0].max() + x), int(px[:, 1].max() + y)])
            boxes.append({"a": round(a, 3), "b": round(b, 3), "boxes": bxs})

    # gom các đoạn liền nhau cùng nội dung thành 1 câu (đọc lệch vài chữ vẫn là 1 câu; chọn bản đọc nhiều thời gian nhất)
    gap = max(2.5 * step / F, 0.15)
    segs: list[Segment] = []
    shapes: list[tuple[float, float]] = []      # (bề rộng khối chữ, cao dòng) của từng câu – để không gộp nhầm
    cur: list = []
    for r in raw + [(total + 1, total + 1, "", 0.0, 0.0, 0.0, None)]:
        a, b, text, score, wid, lh = r[:6]
        if cur:
            ref = max(cur, key=lambda c: (c[1] - c[0]) * c[3])
            if text and _same_line(text, ref[2]) and a - cur[-1][1] <= gap and \
                    not _other_shape(text, ref[2], wid, ref[4], max(lh, ref[5])):
                cur.append(r)
                continue
            if cur[-1][1] - cur[0][0] >= MIN_SEG:
                votes: dict[str, float] = {}
                for c in cur:
                    votes[c[2]] = votes.get(c[2], 0.0) + (c[1] - c[0]) * max(c[3], 0.05)
                best = max(votes, key=votes.get)
                bdr = [c for c in cur if c[2] == best]
                # độ chắc câu (2.1.36) = độ chắc trung bình (theo thời lượng) của các lần đọc ra đúng chữ đã chọn;
                # 2.1.37: có độ chắc từng chữ -> (trung bình + chữ kém nhất) / 2 (1 chữ đọc rất kém kéo cả câu xuống)
                conf = sum((c[1] - c[0]) * c[3] for c in bdr) / max(1e-6, sum(c[1] - c[0] for c in bdr))
                extra: dict = {}
                chs = [(c[1] - c[0], c[6]) for c in bdr if c[6] and len(c[6]) == len(best)]
                if chs:
                    tw = max(1e-6, sum(d for d, _ in chs))
                    cc = [sum(d * ch[j][1] for d, ch in chs) / tw for j in range(len(best))]
                    alts = []
                    for j in range(len(best)):
                        al: dict[str, float] = {}
                        for _d, ch in chs:
                            for ac, ap in ch[j][3]:
                                al[ac] = max(al.get(ac, 0.0), float(ap))
                        alts.append([[k, round(v, 3)] for k, v in sorted(al.items(), key=lambda kv: -kv[1])[:3]]
                                    if cc[j] < 0.97 else [])
                    conf = line_conf(cc)
                    extra["ocr_cc"] = [round(float(v), 3) for v in cc]
                    if any(alts):
                        extra["ocr_alt"] = alts
                extra["ocr_conf"] = round(float(conf), 3)
                segs.append(Segment(round(cur[0][0], 3), round(min(total, cur[-1][1]), 3), best, extra=extra))
                shapes.append((ref[4], ref[5]))
            cur = []
        if text:
            cur = [r]
    merged: list[Segment] = []
    mshape: list[tuple[float, float]] = []
    for s, shp in zip(segs, shapes):   # gộp câu trùng lặp sát nhau (do nháy chữ)
        if merged and _same_line(merged[-1].text, s.text) and s.start - merged[-1].end < 0.6 and \
                not _other_shape(s.text, merged[-1].text, shp[0], mshape[-1][0], max(shp[1], mshape[-1][1])):
            m, da, db = merged[-1], merged[-1].end - merged[-1].start, s.end - s.start
            m.extra["ocr_conf"] = round((m.extra.get("ocr_conf", 1.0) * da + s.extra.get("ocr_conf", 1.0) * db) /
                                        max(1e-6, da + db), 3)
            if m.text == s.text and m.extra.get("ocr_cc") and s.extra.get("ocr_cc"):
                m.extra["ocr_cc"] = [round((x1 * da + x2 * db) / max(1e-6, da + db), 3)
                                     for x1, x2 in zip(m.extra["ocr_cc"], s.extra["ocr_cc"])]
            m.end = s.end
        else:
            merged.append(s)
            mshape.append(shp)
    try:
        from .asr import to_simplified

        for s in merged:
            t2 = to_simplified(s.text)
            if len(t2) != len(s.text):
                s.extra.pop("ocr_cc", None)
                s.extra.pop("ocr_alt", None)
            s.text = t2
    except Exception:
        pass
    if boxes_out:
        Path(boxes_out).write_text(json.dumps({"fps": F, "width": W, "height": H, "spans": boxes}), "utf-8")
    if stats is not None:
        stats.update({"frames": seen[0], "ocr_runs": reader.n["ocr"], "lines": len(merged), "fps": F,
                      "spans": reader.n["spans"], "det": n_det[0], "cleaned": reader.n["cleaned"], "engine": eng.label,
                      "engine_warn": eng.warn, "hw": used_hw[0], "chunks": nch, "secs": round(_time.time() - t_start, 1),
                      "eq": bool(pre), "t": {k: round(v, 1) for k, v in tm.items()},
                      "color": ocrcolor.describe(colorf.ents) if colorf is not None else "",
                      "center_cut": center_cut[0],
                      "big": big_st, "text_box": _text_box(boxes, W, H)})
    if progress:
        progress(1.0, f"OCR xong {len(merged)} câu")
    return merged


CENTER_GAP = 1.5         # khoảng trống giữa 2 khối chữ > 1,5 cỡ chữ = khối riêng (biển hiệu / chữ cảnh)
BOX_PAD = 0.75           # khung bộ tìm chữ có lề: cỡ chữ ≈ 0,75 × cao khung


def _line_h(it) -> float:
    """Chiều cao dòng chữ (theo cỡ khung bộ tìm chữ). 2.4.5: khung dính vật sáng viền tối của cảnh cao hẳn chữ -> có
    vị trí từng chữ thì lấy khoảng cách giữa 2 chữ Hán liền nhau (≈ cỡ chữ) / BOX_PAD, lấy số nhỏ hơn – trước đây vài
    câu khung dính cảnh làm "cỡ chữ phụ đề" to lên, câu đọc đúng (khung vừa chữ) bị coi là chữ nhỏ trong cảnh -> mất."""
    x0, y0, x1, y1 = _bbox(it[0])
    h = y1 - y0
    ch = getattr(it, "ch", None)
    if not ch or len(ch) != len(it[1]) or len(ch) < 3:
        return h
    w = max(1.0, x1 - x0)
    cs = [x0 + float(c[2]) * w for c in ch if _CJK.match(c[0])]
    if len(cs) < 3:
        return h
    pitch = float(np.median(np.diff(cs)))
    return min(h, pitch / BOX_PAD) if pitch > 0 else h


def _ink_parts(it) -> list[tuple[float, float, object]]:
    """Phần có chữ thật của 1 mảnh chữ: [(x trái, x phải, mảnh)]. Có vị trí từng chữ (ch) -> đo theo tâm chữ đầu / cuối
    (khung bộ tìm chữ có lề rộng – 2 khối cách xa vẫn có khung sát nhau) và TÁCH mảnh ở khe lớn giữa 2 chữ (bộ tìm chữ gộp
    biển hiệu với câu phụ đề vào 1 khung)."""
    x0, y0, x1, y1 = _bbox(it[0])
    w, h = max(1.0, x1 - x0), max(1.0, (y1 - y0) * BOX_PAD)
    ch = getattr(it, "ch", None)
    if not ch or len(ch) != len(it[1]):
        return [(x0, x1, it)]
    cs = [x0 + float(c[2]) * w for c in ch]
    cuts = [k for k in range(1, len(cs)) if cs[k] - cs[k - 1] - h > CENTER_GAP * h]
    out = []
    for lo, hi in zip([0] + cuts, cuts + [len(cs)]):
        a, b = max(x0, cs[lo] - 0.55 * h), min(x1, cs[hi - 1] + 0.55 * h)
        if len(cuts) == 0:
            out.append((a, b, it))
        else:
            part = _mk(_rect_pts(a, y0, b, y1), it[1][lo:hi], float(np.mean([c[1] for c in ch[lo:hi]])), ch=[
                (c[0], c[1], (x0 + float(c[2]) * w - a) / max(1.0, b - a), c[3]) for c in ch[lo:hi]])
            out.append((a, b, part))
    return out


def _center_keep(items: list, cx: float) -> list:
    """2.1.44: mỗi hàng chữ chia thành các cụm (khoảng trống giữa 2 khối > CENTER_GAP × cỡ chữ, đo theo vị trí từng
    chữ); có ĐÚNG 1 cụm phủ giữa khung hình (± nửa cỡ chữ) thì chỉ giữ cụm đó – cụm xa ở 2 bên là chữ trong cảnh. Không
    cụm nào / nhiều cụm phủ giữa (vd 2 người nói "-A   -B" mà giữa là khe) -> giữ nguyên cả hàng."""
    rows: list[dict] = []
    for it in sorted(items, key=lambda it: _bbox(it[0])[1]):
        b = _bbox(it[0])
        cy, h = (b[1] + b[3]) / 2, b[3] - b[1]
        for r in rows:
            if abs(r["cy"] - cy) <= 0.5 * max(h, r["h"]):
                r["its"].append(it)
                break
        else:
            rows.append({"cy": cy, "h": h, "its": [it]})
    repl: dict[int, list] = {}            # mảnh -> các phần giữ lại (mảnh bị tách / bỏ)
    for r in rows:
        parts = sorted((p for it in r["its"] for p in ((pa, pb, pi, it) for pa, pb, pi in _ink_parts(it))),
                       key=lambda q: q[0])
        if len(parts) < 2:
            continue
        h = float(np.median([_bbox(it[0])[3] - _bbox(it[0])[1] for it in r["its"]])) * BOX_PAD
        clusters = [[parts[0]]]
        for q in parts[1:]:
            if q[0] - max(o[1] for o in clusters[-1]) > CENTER_GAP * h:
                clusters.append([q])
            else:
                clusters[-1].append(q)
        if len(clusters) < 2:
            continue
        central = [c for c in clusters if min(o[0] for o in c) - 0.5 * h <= cx <= max(o[1] for o in c) + 0.5 * h]
        if len(central) != 1:
            continue
        keep_parts = {id(q[2]) for q in central[0]}
        for it in r["its"]:
            mine = [q for q in parts if q[3] is it]
            kept = [q[2] for q in mine if id(q[2]) in keep_parts]
            if len(kept) != len(mine):
                repl[id(it)] = kept
    if not repl:
        return items
    out = []
    for it in items:
        out.extend(repl.get(id(it), [it]))
    return out


def _other_shape(a: str, b: str, wa: float, wb: float, lh: float) -> bool:
    """2 lần đọc khác chữ mà khối chữ rộng hẳn nhau (≥ 0,6 cao dòng ~ hơn kém 1 chữ) = 2 câu khác nhau dù chữ gần giống
    ("是不喜欢吗" -> "是我不喜欢吗" liền nhau). Cùng 1 câu đọc lệch chữ thì khung chữ gần như y hệt (2.1.32)."""
    return a != b and lh > 0 and abs(wa - wb) >= 0.6 * lh


def _han_band(reads: list, marks, h_sub: float, scan_w: float):
    """Hàng của dòng phụ đề chữ Hán: (đỉnh, đáy) chứa 80 % tâm dòng, tâm ngang trung vị + sai số (None khi phụ đề không
    căn giữa) – để nhận câu chỉ có số / chữ Latin nằm đúng chỗ phụ đề (2.1.32)."""
    ys, xs = [], []
    for items in reads:
        han = [_bbox(it[0]) for it in items if _CJK.search(it[1]) and not _is_mark(it, marks)]
        if not han:
            continue
        ys += [(bb[1] + bb[3]) / 2 for bb in han]
        xs.append((min(bb[0] for bb in han) + max(bb[2] for bb in han)) / 2)
    if len(ys) < 3 or not h_sub:
        return None
    y0, y1 = (float(v) for v in np.percentile(ys, [10, 90]))
    q1, mx, q3 = (float(v) for v in np.percentile(xs, [25, 50, 75]))
    centred = q3 - q1 <= 0.1 * scan_w
    return y0, y1, (mx if centred else None), max(2.0 * h_sub, 0.08 * scan_w)


def _stray_ok(it, han: list, h_sub: float, band) -> bool:
    """Mảnh chữ không có chữ Hán (số / chữ Latin) có phải phụ đề không: cùng hàng với chữ Hán của câu ("我买了" + "iPhone"
    bị tìm thành 2 khung), hoặc đứng 1 mình đúng hàng + cỡ chữ + tâm ngang của phụ đề ("4", "43")."""
    bb = _bbox(it[0])
    h = bb[3] - bb[1]
    if han:
        for o in han:
            ob = _bbox(o[0])
            oh = ob[3] - ob[1]
            if min(bb[3], ob[3]) - max(bb[1], ob[1]) >= 0.5 * min(h, oh) and \
                    max(ob[0] - bb[2], bb[0] - ob[2], 0.0) <= 3.0 * oh:
                return True
        return False
    if band is None or not h_sub or len(it[1]) > 16 or not 0.7 * h_sub <= h <= 1.5 * h_sub:
        return False
    y0, y1, cx, tol = band
    if not y0 - 0.5 * h_sub <= (bb[1] + bb[3]) / 2 <= y1 + 0.5 * h_sub:
        return False
    return cx is None or abs((bb[0] + bb[2]) / 2 - cx) <= tol


MARK_COOC = 0.2     # watermark: ≥ 20 % thời gian hiện cùng lúc với chữ Hán khác


def _static_marks_spans(times: list, reads: list, total: float, min_share: float = 0.3,
                        min_sec: float = 10.0) -> list[tuple[str, float, float, float, float]]:
    """Như static_marks nhưng theo thời lượng từng đoạn: chữ ở cùng 1 chỗ tổng cộng ≥ 30 % video và ≥ 10 s (video ngắn:
    ≥ 80 % video) và có mặt ở ≥ 3 đoạn khác nhau (1 câu phụ đề hiện lâu không bị coi là logo) = logo / watermark."""
    pos: dict[str, list] = {}
    dur: dict[str, float] = {}
    for (a, b), items in zip(times, reads):
        for pts, txt, _ in items:
            pos.setdefault(txt, []).append(_bbox(pts))
            dur[txt] = dur.get(txt, 0.0) + (b - a)
    need = max(min_share * total, min(min_sec, 0.8 * total))
    cand = {}
    for txt, bs in pos.items():
        if dur[txt] < need or len(bs) < 3:
            continue
        arr = np.array(bs)
        if arr[:, :2].std(axis=0).max() < 12:
            cand[txt] = tuple(float(v) for v in np.median(arr, axis=0))
    # 2.1.41: watermark đè lên cảnh nên hay hiện CÙNG LÚC với câu phụ đề (chữ không phải watermark khác); chữ luôn hiện 1
    # mình đúng chỗ phụ đề là câu thoại lặp lại nhiều lần – không phải watermark
    co: dict[str, float] = {}
    for (a, b), items in zip(times, reads):
        if not any(o[1] not in cand and _CJK.search(o[1]) for o in items):
            continue
        for _pts, txt, _ in items:
            if txt in cand:
                co[txt] = co.get(txt, 0.0) + (b - a)
    return [(txt, *bx) for txt, bx in cand.items() if co.get(txt, 0.0) >= MARK_COOC * dur[txt]]


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


def _sample(src: str, start: float, dur: float, fps: float, W: int, H: int, max_side: int = 960, pre: str = ""):
    """Các khung hình trong [start, start+dur] (fps khung/giây), thu nhỏ cạnh dài ≤ max_side – 1 lần gọi ffmpeg.
    pre: lọc ảnh làm rõ chữ (eq_filter)."""
    sc = min(1.0, max_side / max(W, H))
    w2, h2 = int(W * sc) // 2 * 2, int(H * sc) // 2 * 2
    cmd = [ffmpeg_bin(), "-hide_banner", "-loglevel", "error", "-ss", f"{max(0.0, start):.3f}", "-t", f"{dur:.3f}",
           "-i", src, "-an", "-vf", f"fps={fps},scale={w2}:{h2}" + (f",{pre}" if pre else ""), "-f", "rawvideo",
           "-pix_fmt", "bgr24", "pipe:1"]
    buf = subprocess.run(cmd, capture_output=True, creationflags=_NO_WINDOW).stdout
    n = len(buf) // (w2 * h2 * 3)
    return [(start + i / fps, np.frombuffer(buf[i * w2 * h2 * 3:(i + 1) * w2 * h2 * 3], np.uint8).reshape(h2, w2, 3),
             sc) for i in range(n)]


def detect_subtitle_region(src: str | Path, progress: Callable[[float, str], None] | None = None,
                           settings: dict | None = None) -> dict:
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
    eng = get_engine(settings)
    dur = info.duration or 10
    pre = eq_filter(settings)
    frames = _sample(src, 0, min(5.0, dur), 4, W, H, pre=pre)
    if dur > 10:
        frames += _sample(src, dur - 5, 5, 2, W, H, pre=pre)
    if dur > 15:
        for k in range(8):
            frames += _sample(src, 5 + (dur - 10) * (k + 0.5) / 8, 0.3, 4, W, H, pre=pre)[:1]
    reads: list[tuple[float, list]] = []

    def ocr_all(fr):
        for n, (t, img, sc) in enumerate(fr):
            if progress:
                progress(n / max(1, len(fr)), f"Đang dò vùng phụ đề ({n + 1}/{len(fr)} khung)")
            items = []
            for box, text, score in eng.ocr(img):
                if float(score) < 0.6 or not _CJK.search(text):
                    continue
                pts = np.asarray(box, dtype=np.float32) / sc
                items.append(((float(pts[:, 0].min() / W), float(pts[:, 1].min() / H),
                               float(pts[:, 0].max() / W), float(pts[:, 1].max() / H)), text.strip()))
            reads.append((t, items))

    ocr_all(frames)
    head = [it for t, its in reads if t <= 5 for it in its]
    if not head and dur > 10:   # 5 giây đầu chưa có chữ (đoạn mở đầu) -> quét tiếp tới giây 30
        more = _sample(src, 5, min(25.0, dur - 10), 1, W, H, pre=pre)
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
