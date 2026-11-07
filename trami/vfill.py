"""Xoá chữ / logo trên video bằng NỀN THẬT lấy từ các khung trước / sau (không phải AI đoán).

Ý tưởng giống các bộ xoá video theo thời gian (ProPainter, STTN…) nhưng tự viết, nhẹ, không cần mô hình:
  * Phụ đề đổi câu liên tục, máy quay hay lia -> chỗ bị chữ che ở khung này thường LỘ RA ở khung khác.
  * Đo chuyển động của cả khung hình giữa 2 khung liền nhau (tương quan pha trên dải hình quanh vùng chữ), cộng dồn ->
    biết điểm nền ở khung t nằm đâu ở khung s.
  * Lấp chỗ chữ của khung t bằng điểm ảnh của khung gần nhất mà chỗ đó KHÔNG bị chữ che. Kiểm tra viền quanh chữ
    khớp (sai số nhỏ) mới dùng – cắt cảnh, vật chuyển động riêng -> bỏ khung đó. Bù chênh sáng theo viền.
  * Khung trước đã lấp xong được dùng tiếp như nguồn sạch -> nền "chảy" qua cả câu phụ đề dài khi máy quay đứng yên.
  * Chỗ chưa từng lộ ra: cảnh đứng yên -> dùng lại đúng phần đã lấp ở khung trước (không nhấp nháy); còn lại mới
    dùng LaMa / OpenCV vẽ (`fallback`).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

import numpy as np

# khung nguồn thử lần lượt (gần trước): dày ở gần, thưa dần ở xa – không bỏ lỡ quãng ngắt ngắn giữa 2 câu phụ đề
PAST = tuple(range(1, 9)) + tuple(range(10, 25, 2))
FUTURE = tuple(range(1, 9)) + tuple(range(10, 33, 2)) + tuple(range(36, 65, 4)) + tuple(range(72, 129, 8))
ERR_MAX = 9.0          # sai số trung bình cho phép trên viền quanh chữ (0..255) để nhận 1 khung làm nguồn
OFF_MAX = 45.0         # chênh sáng tối đa bù được


# ------------------------------------------------------------------ mặt nạ chữ

def stroke_mask(roi: np.ndarray, line_h: int | None = None) -> np.ndarray:
    """Điểm ảnh khác hẳn nền xung quanh (nét chữ MỌI MÀU + viền + bóng): so mỗi điểm với trung vị vùng lân cận
    (lớn hơn nét chữ) trên cả 3 kênh màu Lab, ngưỡng Otsu. 255 = chữ."""
    import cv2

    h, w = roi.shape[:2]
    if h < 4 or w < 4:
        return np.zeros((h, w), np.uint8)
    lh = line_h or h
    k = max(5, int(lh * 0.6)) | 1
    k = min(k, 255 if roi.dtype == np.uint8 else 5)
    lab = cv2.cvtColor(roi, cv2.COLOR_BGR2LAB)
    bg = cv2.medianBlur(lab, k)
    diff = np.abs(lab.astype(np.int16) - bg.astype(np.int16))
    d = np.maximum(diff[..., 0], np.maximum(diff[..., 1], diff[..., 2]) * 2).clip(0, 255).astype(np.uint8)
    t, _ = cv2.threshold(d, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    m = (d > max(28.0, t)).astype(np.uint8) * 255
    m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3)))
    n, lab_, st, _ = cv2.connectedComponentsWithStats(m, 8)
    keep = np.zeros_like(m)
    for i in range(1, n):
        if st[i, cv2.CC_STAT_AREA] >= 4 and st[i, cv2.CC_STAT_HEIGHT] <= lh * 1.6:
            keep[lab_ == i] = 255
    return keep


def subtitle_mask(roi: np.ndarray, dil: int, boxes: list | None = None, cover_box: bool = False,
                  legacy: Callable | None = None) -> np.ndarray:
    """Mặt nạ phụ đề trong 1 vùng: boxes = khung chữ OCR (toạ độ trong vùng) lúc này ([] = không có chữ, None = không
    có dữ liệu OCR). cover_box: xoá cả khung chữ OCR (chắc chắn sạch). legacy: cách cũ (chữ sáng có viền tối)."""
    import cv2

    h, w = roi.shape[:2]
    if boxes is not None and not boxes:
        return np.zeros((h, w), np.uint8)
    pad = max(3, dil)
    limit = None
    line_h = None
    if boxes:
        limit = np.zeros((h, w), np.uint8)
        hs = []
        for bx0, by0, bx1, by1 in boxes:
            a0, b0 = max(0, int(bx0) - pad), max(0, int(by0) - pad)
            a1, b1 = min(w, int(bx1) + pad), min(h, int(by1) + pad)
            if a1 > a0 and b1 > b0:
                limit[b0:b1, a0:a1] = 255
                hs.append(by1 - by0)
        if not limit.any():
            return limit
        line_h = int(np.median(hs)) if hs else None
        if cover_box:
            return limit
    m = stroke_mask(roi, line_h)
    if legacy is not None:
        old = legacy(roi, 0)
        if limit is None:
            # không có khung OCR: chỉ giữ nét quanh chữ sáng đã tìm (tránh bắt nhầm cây cối, đồ vật), hoặc cả dòng chữ
            # (dải ngang có nhiều nét nhất) khi chữ có màu
            if old.any():
                near = cv2.dilate(old, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (dil * 4 + 1, dil * 4 + 1)))
                m = cv2.bitwise_or(old, cv2.bitwise_and(m, near))
            else:
                m = _text_band(m)
        else:
            m = cv2.bitwise_or(m, old)
    if limit is not None:
        m = cv2.bitwise_and(m, limit)
        if m.sum() < 255 * 20:
            return limit                      # không bắt được nét chữ -> xoá cả khung chữ
    if dil > 0 and m.any():
        m = cv2.dilate(m, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (dil * 2 + 1, dil * 2 + 1)))
        if limit is not None:
            m = cv2.bitwise_and(m, cv2.dilate(limit, np.ones((3, 3), np.uint8), iterations=dil))
    return m


def _text_band(m: np.ndarray) -> np.ndarray:
    """Chỉ giữ các nét nằm trong dải ngang dày đặc nhất (dòng chữ) – bỏ nét lẻ của cảnh."""
    h = m.shape[0]
    rows = (m > 0).sum(1).astype(np.float32)
    if rows.max() < 8:
        return np.zeros_like(m)
    k = max(3, h // 6)
    sm = np.convolve(rows, np.ones(k) / k, mode="same")
    c = int(sm.argmax())
    band = sm > sm[c] * 0.25
    y0 = c
    while y0 > 0 and band[y0 - 1]:
        y0 -= 1
    y1 = c
    while y1 < h - 1 and band[y1 + 1]:
        y1 += 1
    out = np.zeros_like(m)
    out[y0:y1 + 1] = m[y0:y1 + 1]
    return out


# ------------------------------------------------------------------ đo chuyển động

_WIN: dict = {}


def motion(prev: np.ndarray, cur: np.ndarray) -> tuple[float, float, bool]:
    """Dịch chuyển (dx, dy) của cả dải hình từ khung trước sang khung này + có tin được không."""
    import cv2

    if prev.shape != cur.shape or prev.size == 0:
        return 0.0, 0.0, False
    if float(np.abs(prev - cur).mean()) < 2.5:
        return 0.0, 0.0, True                  # gần như y hệt: đứng yên
    win = _WIN.get(cur.shape)
    if win is None:
        win = _WIN[cur.shape] = cv2.createHanningWindow((cur.shape[1], cur.shape[0]), cv2.CV_32F)
    (dx, dy), resp = cv2.phaseCorrelate(prev, cur, win)
    ok = resp > 0.12 and abs(dx) < cur.shape[1] / 3 and abs(dy) < cur.shape[0] / 2
    return float(dx), float(dy), bool(ok)


def _sample(a: np.ndarray, y0: int, y1: int, x0: int, x1: int, dx: int, dy: int, fill) -> np.ndarray:
    """a[y+dy, x+dx] cho y ∈ [y0, y1), x ∈ [x0, x1) – ngoài ảnh thì = fill."""
    H, W = a.shape[:2]
    out = np.empty((y1 - y0, x1 - x0) + a.shape[2:], a.dtype)
    out[...] = fill
    sy0, sy1 = max(y0 + dy, 0), min(y1 + dy, H)
    sx0, sx1 = max(x0 + dx, 0), min(x1 + dx, W)
    if sy1 > sy0 and sx1 > sx0:
        out[sy0 - (y0 + dy):sy1 - (y0 + dy), sx0 - (x0 + dx):sx1 - (x0 + dx)] = a[sy0:sy1, sx0:sx1]
    return out


def _sample_f(a: np.ndarray, y0: int, y1: int, x0: int, x1: int, dx: float, dy: float, fill) -> np.ndarray:
    """Như `_sample` nhưng lệch lẻ điểm ảnh (nội suy song tuyến; mặt nạ: điểm xấu nếu chạm điểm xấu nào)."""
    rx, ry = round(dx), round(dy)
    if abs(dx - rx) < 0.15 and abs(dy - ry) < 0.15:
        return _sample(a, y0, y1, x0, x1, rx, ry, fill)
    import cv2

    mx, my = np.meshgrid(np.arange(x0, x1, dtype=np.float32) + dx, np.arange(y0, y1, dtype=np.float32) + dy)
    if a.dtype == bool:
        out = cv2.remap(a.astype(np.uint8) * 255, mx, my, cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT,
                        borderValue=255)
        return out > 0
    return cv2.remap(a, mx, my, cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT, borderValue=fill)


# ------------------------------------------------------------------ lấp theo thời gian

@dataclass
class _Fr:
    img: np.ndarray                 # dải hình gốc (BGR uint8)
    mask: np.ndarray                # True = chỗ cần xoá
    ctx: np.ndarray                 # ảnh xám để đo chuyển động
    soft: np.ndarray | None = None  # chỗ đã có giá trị tạm dùng được (logo đã tính ngược): ưu tiên nền thật khung
    #                                 khác, không có thì giữ nguyên (không cần AI vẽ)
    pos: np.ndarray = field(default_factory=lambda: np.zeros(2))
    seg: int = 0                    # đoạn liền mạch (cắt cảnh / không đo được chuyển động -> đoạn mới)
    out: np.ndarray | None = None   # dải hình đã xoá
    trusted: np.ndarray | None = None   # điểm là nền thật (không bị chữ, hoặc lấp từ khung khác)


class BandFiller:
    """Nhận lần lượt các dải hình (cùng vị trí trên khung) + mặt nạ chữ, trả ra dải hình đã xoá theo đúng thứ tự,
    trễ `lookahead` khung (để dùng được khung sau)."""

    def __init__(self, fallback: Callable[[np.ndarray, np.ndarray], None], lookahead: int = 64,
                 ctx_rows: list[tuple[int, int]] | None = None):
        self.fallback = fallback        # fallback(ảnh, mặt nạ uint8) – vẽ tại chỗ phần chưa lấp được
        self.look = max(0, int(lookahead))
        self.ctx_rows = ctx_rows
        self.frames: dict[int, _Fr] = {}
        self.last = -1
        self.next_out = 0
        self.seg = 0
        self.cur = -1                   # khung đang xử lý (fallback biết để chọn AI / OpenCV)
        self._rel: dict = {}            # độ dời đo thẳng giữa 2 khung
        self.stats = {"frames": 0, "masked": 0, "temporal": 0, "static": 0, "drawn": 0}

    def _ctx(self, img: np.ndarray) -> np.ndarray:
        g = img.mean(axis=2, dtype=np.float32) if img.ndim == 3 else img.astype(np.float32)
        if self.ctx_rows:
            parts = [g[a:b] for a, b in self.ctx_rows if b > a]
            if parts and sum(p.shape[0] for p in parts) >= 8:
                return np.ascontiguousarray(np.vstack(parts))
        return g

    def push(self, idx: int, img: np.ndarray, mask: np.ndarray, soft: np.ndarray | None = None):
        sf = soft.astype(bool) & ~mask.astype(bool) if soft is not None and soft.any() else None
        fr = _Fr(img=img, mask=mask.astype(bool), ctx=self._ctx(img), soft=sf)
        prev = self.frames.get(idx - 1)
        if prev is not None:
            dx, dy, ok = motion(prev.ctx, fr.ctx)
            if not ok:
                self.seg += 1
            fr.pos = prev.pos + (np.array([dx, dy]) if ok else 0)
        fr.seg = self.seg
        self.frames[idx] = fr
        self.last = idx

    def pop(self, eof: bool = False) -> list[tuple[int, np.ndarray]]:
        """Các khung đã đủ khung sau để xử lý (eof: xử lý hết)."""
        out = []
        while self.next_out <= self.last and (eof or self.next_out + self.look <= self.last):
            t = self.next_out
            if t in self.frames:
                out.append((t, self._process(t)))
            self.next_out += 1
            for k in [k for k in self.frames if k < self.next_out - max(PAST) - 1]:
                del self.frames[k]
            if len(self._rel) > 4000:
                self._rel = {k: v for k, v in self._rel.items() if min(k) >= self.next_out - max(PAST) - 1}
        return out

    def _offset(self, s: int, t: int, src: _Fr, fr: _Fr) -> tuple[float, float]:
        """Điểm p ở khung t nằm ở p + (dx, dy) ở khung s. Đo thẳng giữa 2 khung (chính xác hơn cộng dồn từng bước);
        không đo được / lệch xa ước lượng cộng dồn thì dùng vị trí cộng dồn."""
        est = src.pos - fr.pos
        if abs(s - t) <= 8:            # khung gần: cộng dồn từng bước đã đủ chính xác
            return float(est[0]), float(est[1])
        key = (s, t)
        if key not in self._rel:
            dx, dy, ok = motion(src.ctx, fr.ctx)      # khung t = khung s dời (dx, dy)
            if ok and abs(-dx - est[0]) < 6 and abs(-dy - est[1]) < 6:
                v = (-dx, -dy)
            else:
                v = (float(est[0]), float(est[1]))
            self._rel[key], self._rel[(t, s)] = v, (-v[0], -v[1])
        return self._rel[key]

    def _process(self, t: int) -> np.ndarray:
        import cv2

        self.cur = t
        fr = self.frames[t]
        self.stats["frames"] += 1
        allm = fr.mask if fr.soft is None else (fr.mask | fr.soft)
        if not allm.any():
            fr.out, fr.trusted = fr.img, np.ones(fr.mask.shape, bool)
            return fr.out
        if fr.mask.any():
            self.stats["masked"] += 1
        img = fr.img
        H, W = fr.mask.shape
        m8 = allm.astype(np.uint8)
        ring_all = cv2.dilate(m8, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (17, 17))) > 0
        near = cv2.dilate(m8, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (7, 7))) > 0
        ys, xs = np.nonzero(ring_all)
        y0, y1, x0, x1 = int(ys.min()), int(ys.max()) + 1, int(xs.min()), int(xs.max()) + 1
        need = allm[y0:y1, x0:x1].copy()
        soft = fr.soft[y0:y1, x0:x1] if fr.soft is not None else np.zeros_like(need)
        # vòng so khớp: cách mép mặt nạ 3–8 điểm ảnh (sát chữ còn quầng mờ do nén video -> làm lệch phép bù sáng)
        ring = ring_all[y0:y1, x0:x1] & ~near[y0:y1, x0:x1]
        base = img[y0:y1, x0:x1].astype(np.float32)
        res = base.copy()
        got = np.zeros_like(need)
        n_ring = int(ring.sum())
        # lượt 1: điểm nền THẬT (khung khác, chỗ đó không bị chữ che) – mỗi điểm lấy độc lập, không cộng dồn sai số;
        # lượt 2: khung trước đã lấp xong (nền "chảy" qua cả câu dài khi đứng yên) – không bù sáng để không trôi màu
        cands = [(k, -k, False) for k in PAST] + [(k, k, False) for k in FUTURE if k <= self.look] + \
            [(k + 1000, -k, True) for k in PAST]
        for _, off, chained in sorted(cands):
            s = t + off
            src = self.frames.get(s)
            if src is None or src.seg != fr.seg or (chained and src.out is None):
                continue
            a = src.out if chained else src.img
            bad_a = ~src.trusted if chained else (src.mask if src.soft is None else src.mask | src.soft)
            est = np.rint(src.pos - fr.pos).astype(int)
            # thử nhanh theo vị trí ước lượng: khung này không lộ chỗ nào cần lấp -> bỏ, khỏi đo chuyển động chính xác
            if not (need & ~_sample(bad_a, y0, y1, x0, x1, int(est[0]), int(est[1]), True)).any():
                continue
            d = self._offset(s, t, src, fr)
            sm = _sample_f(a, y0, y1, x0, x1, d[0], d[1], 0).astype(np.float32)
            bad = _sample_f(bad_a, y0, y1, x0, x1, d[0], d[1], True)
            rk = ring & ~bad
            if rk.sum() < max(12, 0.3 * n_ring):
                continue
            if rk.sum() > 3000:              # đủ điểm để so: lấy thưa cho nhanh
                idx = np.flatnonzero(rk)[::max(1, int(rk.sum()) // 3000)]
                rk = np.zeros_like(rk)
                rk.flat[idx] = True
            diff = base[rk] - sm[rk]
            offc = np.median(diff, axis=0)
            if np.abs(offc).max() > OFF_MAX or float(np.abs(diff - offc).mean()) > ERR_MAX:
                continue
            if chained or np.abs(offc).max() < 4.0:
                offc = np.zeros(3, np.float32)   # chênh nhỏ = nhiễu nén: không bù (bù mãi qua chuỗi khung sẽ trôi màu)
            take = need & ~bad
            if not take.any():
                continue
            res[take] = sm[take] + offc
            need &= ~take
            got |= take
            if not need.any():
                break
        n_t = int(got.sum())
        if n_t:
            self.stats["temporal"] += 1
        need &= ~soft            # phần logo không lấy được nền thật: giữ bản đã tính ngược
        if need.any():
            prev = self.frames.get(t - 1)
            # cảnh đứng yên: dùng lại phần khung trước đã lấp (kể cả do AI vẽ) -> không nhấp nháy giữa các khung
            if prev is not None and prev.out is not None and prev.seg == fr.seg and \
                    np.allclose(prev.pos, fr.pos, atol=0.5) and prev.mask[y0:y1, x0:x1][need].all():
                pv = prev.out[y0:y1, x0:x1].astype(np.float32)
                rk = ring & ~prev.mask[y0:y1, x0:x1]
                if rk.sum() >= 12 and float(np.abs(base[rk] - pv[rk]).mean()) < ERR_MAX * 0.6:
                    res[need] = pv[need]
                    need[:] = False
                    self.stats["static"] += 1
        out = img.copy()
        sub = np.clip(res, 0, 255).astype(np.uint8)
        if need.any():
            self.stats["drawn"] += 1
            tmp = out.copy()
            tmp[y0:y1, x0:x1] = sub
            full = np.zeros((H, W), np.uint8)
            full[y0:y1, x0:x1][need] = 255
            self.fallback(tmp, full)
            sub = tmp[y0:y1, x0:x1]
        # mép mặt nạ hoà mềm 2 điểm ảnh (mặt nạ đã nới rộng nên mép nằm trên nền, không lộ vệt)
        m = allm[y0:y1, x0:x1].astype(np.float32)
        a = np.maximum(cv2.GaussianBlur(m, (5, 5), 0), cv2.erode(m, np.ones((3, 3), np.uint8)))[..., None]
        out[y0:y1, x0:x1] = np.clip(sub * a + base * (1 - a), 0, 255).astype(np.uint8)
        trusted = ~allm
        trusted[y0:y1, x0:x1] |= got
        fr.out, fr.trusted = out, trusted
        return out
