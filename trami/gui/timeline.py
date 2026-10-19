"""Timeline nhiều track kiểu trình dựng phim (Concat / CapCut), đặt ngang dưới cùng trang dự án.

Track: Phụ đề (khối từng câu, màu theo người nói) · Giọng lồng (khối giọng đã xếp, màu theo độ vừa) ·
Video (dải ảnh thu nhỏ) · Tiếng gốc (đường sóng âm). Bấm khối = chọn câu, bấm đúp = nghe câu,
bấm thước / chỗ trống = tua; Ctrl + lăn chuột = phóng to/thu nhỏ, lăn chuột = cuộn ngang.
"""
from __future__ import annotations

import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import QHBoxLayout, QScrollBar, QVBoxLayout, QWidget

from . import icons, theme
from .subs_model import spk_color
from .widgets import IconButton, label

FIT_COL = {"ok": "#4ADE80", "fast": "#F5B841", "long": "#F87171"}
TICKS = [0.5, 1, 2, 5, 10, 15, 30, 60, 120, 300, 600, 1200]


def _tc(t: float, step: float) -> str:
    m, s = divmod(max(0.0, t), 60)
    h, m = divmod(int(m), 60)
    base = f"{h}:{m:02d}:{int(s):02d}" if h else f"{m:02d}:{int(s):02d}"
    return base + (f".{int(round((s % 1) * 10))}" if step < 1 else "")


class _Canvas(QWidget):
    HEAD = 118      # cột tên track bên trái
    RULER = 24
    TRACKS = [("subs", "Phụ đề", "subtitles", 38), ("dub", "Giọng lồng", "mic", 22),
              ("video", "Video", "film", 44), ("audio", "Tiếng gốc", "volume", 30)]

    def __init__(self, tl: "TrackTimeline"):
        super().__init__(tl)
        self.tl = tl
        self.setMouseTracking(True)
        self.setMinimumHeight(self.RULER + sum(h + 6 for *_, h in self.TRACKS) + 6)
        self._drag = False
        self._hover = None

    # ------------------------------------------------------------------ toạ độ
    def x(self, t: float) -> float:
        return self.HEAD + (t - self.tl.t0) * self.tl.pps

    def t(self, x: float) -> float:
        return max(0.0, min(self.tl.duration, self.tl.t0 + (x - self.HEAD) / self.tl.pps))

    def track_rect(self, key: str) -> QRectF:
        y = self.RULER + 4
        for k, _, _, h in self.TRACKS:
            if k == key:
                return QRectF(self.HEAD, y, self.width() - self.HEAD, h)
            y += h + 6
        return QRectF()

    def _hit(self, pos: QPointF) -> int | None:
        """Câu nằm dưới con trỏ (track Phụ đề hoặc Giọng lồng)."""
        if pos.x() < self.HEAD:
            return None
        t = self.t(pos.x())
        if self.track_rect("subs").contains(pos):
            return next((i for i, s in enumerate(self.tl.segs) if s.start <= t <= s.end), None)
        if self.track_rect("dub").contains(pos):
            return next((int(k) for a, b, k, _f in self.tl.dub if a <= t <= b), None)
        return None

    # ------------------------------------------------------------------ chuột
    def mousePressEvent(self, e):
        if not self.tl.duration or e.button() != Qt.MouseButton.LeftButton or e.position().x() < self.HEAD:
            return
        row = self._hit(e.position())
        if row is not None:
            self.tl.lineClicked.emit(row)
            return
        self._drag = True
        self.tl.seekRequested.emit(self.t(e.position().x()))

    def mouseDoubleClickEvent(self, e):
        row = self._hit(e.position())
        if row is not None:
            self.tl.lineActivated.emit(row)

    def mouseMoveEvent(self, e):
        if self._drag and self.tl.duration:
            self.tl.seekRequested.emit(self.t(e.position().x()))
            return
        row = self._hit(e.position())
        if row != self._hover:
            self._hover = row
            s = self.tl.segs[row] if row is not None and row < len(self.tl.segs) else None
            self.setToolTip(f"#{row + 1} {s.speaker or ''}\n{s.trans or s.text}" if s else "")
            self.setCursor(Qt.CursorShape.PointingHandCursor if row is not None else Qt.CursorShape.ArrowCursor)
            self.update()

    def mouseReleaseEvent(self, e):
        self._drag = False

    def wheelEvent(self, e):
        if not self.tl.duration:
            return
        d = e.angleDelta().y() or e.angleDelta().x()
        if e.modifiers() & Qt.KeyboardModifier.ControlModifier:
            self.tl.zoom(1.25 if d > 0 else 0.8, self.t(e.position().x()), e.position().x())
        else:
            self.tl.scroll_by(-d / 120 * 80 / self.tl.pps)
        e.accept()

    # ------------------------------------------------------------------ vẽ
    def paintEvent(self, e):
        tl = self.tl
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.fillRect(self.rect(), QColor("#121722"))
        W, H = self.width(), self.height()
        # cột tên track
        p.setFont(theme.font(8.8, QFont.Weight.DemiBold))
        for key, name, ic, _h in self.TRACKS:
            r = self.track_rect(key)
            p.fillRect(QRectF(8, r.top(), self.HEAD - 14, r.height()), QColor(theme.CARD))
            icons.paint_icon(p, ic, QRectF(16, r.center().y() - 8, 16, 16), theme.MUTED, 1.8)
            p.setPen(QColor(theme.MUTED))
            p.drawText(QRectF(38, r.top(), self.HEAD - 44, r.height()), Qt.AlignmentFlag.AlignVCenter, name)
            p.fillRect(QRectF(self.HEAD, r.top(), W - self.HEAD, r.height()), QColor("#161C28"))
        if not tl.duration:
            p.setPen(QColor(theme.MUTED))
            p.drawText(QRectF(self.HEAD, 0, W - self.HEAD, H), Qt.AlignmentFlag.AlignCenter, "Chưa có video")
            return
        p.setClipRect(QRectF(self.HEAD, 0, W - self.HEAD, H))
        t_lo, t_hi = self.t(self.HEAD), self.t(W)
        # thước thời gian
        step = next((s for s in TICKS if s * tl.pps >= 90), TICKS[-1])
        p.setPen(QColor(theme.MUTED))
        p.setFont(theme.font(7.8))
        k = int(t_lo // step)
        while k * step <= t_hi + step:
            tx = self.x(k * step)
            p.drawLine(QPointF(tx, self.RULER - 7), QPointF(tx, self.RULER))
            p.drawText(QRectF(tx + 4, 2, 90, self.RULER - 6), Qt.AlignmentFlag.AlignVCenter, _tc(k * step, step))
            k += 1
        # video: dải ảnh thu nhỏ
        rv = self.track_rect("video")
        if tl.thumbs is not None and not tl.thumbs.isNull() and tl.thumb_step > 0:
            # ảnh giữ đúng tỉ lệ (như Concat): mỗi ô rộng bằng 1 ảnh, lấy khung hình ở giữa ô
            fw = tl.thumbs.width() / max(1, tl.thumb_count)
            dw = max(8.0, fw * rv.height() / max(1, tl.thumbs.height()))
            end_x = self.x(tl.duration)
            cx = self.HEAD - ((self.HEAD - self.x(0)) % dw)
            while cx < min(W, end_x):
                i = min(tl.thumb_count - 1, max(0, int(self.t(cx + dw / 2) // tl.thumb_step)))
                w = min(dw, end_x - cx)
                p.drawPixmap(QRectF(cx, rv.top(), w, rv.height()), tl.thumbs,
                             QRectF(i * fw, 0, fw * w / dw, tl.thumbs.height()))
                cx += dw
        else:
            p.setPen(QColor(theme.MUTED))
            p.drawText(rv.adjusted(10, 0, 0, 0), Qt.AlignmentFlag.AlignVCenter, "Đang tạo ảnh thu nhỏ…")
        # tiếng gốc: đường sóng âm
        ra = self.track_rect("audio")
        if tl.wave is not None and len(tl.wave):
            mid, half = ra.center().y(), ra.height() / 2 - 2
            p.setPen(QPen(theme.c(theme.INFO, 190), 1))
            px = int(self.HEAD)
            while px < W:
                a, b = self.t(px), self.t(px + 1)
                ia, ib = int(a / tl.wave_hop), max(int(a / tl.wave_hop) + 1, int(b / tl.wave_hop))
                v = float(tl.wave[ia:ib].max()) if ia < len(tl.wave) else 0.0
                if v > 0.004:
                    p.drawLine(QPointF(px, mid - v * half), QPointF(px, mid + v * half))
                px += 1
        # giọng lồng
        rd = self.track_rect("dub")
        for a, b, k, fit in tl.dub:
            if b < t_lo or a > t_hi:
                continue
            r = QRectF(self.x(a), rd.top() + 3, max(2.0, (b - a) * tl.pps), rd.height() - 6)
            col = QColor(FIT_COL.get(fit, "#4ADE80"))
            col.setAlpha(255 if int(k) == tl.selected else 170)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(col)
            p.drawRoundedRect(r, 4, 4)
        # phụ đề: khối từng câu
        rs = self.track_rect("subs")
        p.setFont(theme.font(8.4, QFont.Weight.Medium))
        for i, s in enumerate(tl.segs):
            if s.end < t_lo or s.start > t_hi:
                continue
            r = QRectF(self.x(s.start), rs.top() + 2, max(2.0, (s.end - s.start) * tl.pps - 1), rs.height() - 4)
            base = QColor(spk_color(s.speaker or "S1"))
            fill = QColor(base)
            fill.setAlpha(235 if i == tl.selected else (190 if i in (tl.active, self._hover) else 120))
            p.setPen(QPen(QColor("#FFFFFF"), 2) if i == tl.selected else Qt.PenStyle.NoPen)
            p.setBrush(fill)
            p.drawRoundedRect(r, 5, 5)
            if r.width() > 26:
                p.setPen(QColor("#FFFFFF"))
                txt = (s.trans or s.text or "").strip()
                p.drawText(r.adjusted(6, 0, -4, 0), Qt.AlignmentFlag.AlignVCenter,
                           p.fontMetrics().elidedText(txt, Qt.TextElideMode.ElideRight, int(r.width() - 10)))
        # đoạn bị cắt / ngoài vùng giữ
        top, bot = self.RULER, H
        ks, ke = tl.keep
        ke = ke or tl.duration
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(0, 0, 0, 120))
        if ks > 0:
            p.drawRect(QRectF(self.x(0), top, ks * tl.pps, bot - top))
        if ke < tl.duration:
            p.drawRect(QRectF(self.x(ke), top, (tl.duration - ke) * tl.pps, bot - top))
        p.setBrush(theme.c(theme.ERR, 70))
        for a, b in tl.cuts:
            p.drawRect(QRectF(self.x(a), top, max(2.0, (b - a) * tl.pps), bot - top))
        # điểm đánh dấu cắt + đầu phát
        if tl.mark is not None:
            p.setPen(QPen(QColor(theme.WARN), 1.5, Qt.PenStyle.DashLine))
            p.drawLine(QPointF(self.x(tl.mark), top), QPointF(self.x(tl.mark), bot))
        hx = self.x(tl.pos)
        p.setPen(QPen(QColor(theme.ACCENT), 1.6))
        p.drawLine(QPointF(hx, 4), QPointF(hx, bot))
        tri = QPainterPath()
        tri.moveTo(hx - 6, 2)
        tri.lineTo(hx + 6, 2)
        tri.lineTo(hx, 10)
        tri.closeSubpath()
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(theme.ACCENT))
        p.drawPath(tri)


class TrackTimeline(QWidget):
    seekRequested = Signal(float)
    lineClicked = Signal(int)       # bấm 1 câu -> chọn câu đó
    lineActivated = Signal(int)     # bấm đúp -> nghe câu
    actionRequested = Signal(str)   # nút trên thanh công cụ: insert / split / merge / delete / play / redub

    def __init__(self, parent=None):
        super().__init__(parent)
        self.duration = 0.0
        self.segs: list = []
        self.dub: list = []
        self.keep: tuple[float, float] = (0.0, 0.0)
        self.cuts: list = []
        self.pos = 0.0
        self.mark: float | None = None
        self.selected = -1
        self.active = -1
        self.thumbs: QPixmap | None = None
        self.thumb_step = 0.0
        self.thumb_count = 0
        self.wave: np.ndarray | None = None
        self.wave_hop = 0.05
        self.t0 = 0.0          # giây ở mép trái vùng track
        self.pps = 20.0        # điểm ảnh / giây
        self._fitted = False
        self._opened = False   # đã đặt mức phóng ban đầu cho video này
        self.canvas = _Canvas(self)
        self.bar = QScrollBar(Qt.Orientation.Horizontal)
        self.bar.valueChanged.connect(self._bar_moved)
        tb = QHBoxLayout()
        tb.setContentsMargins(6, 0, 6, 0)
        tb.setSpacing(4)
        for name, ic, tip in (("insert", "plus", "Thêm câu tại đầu phát"), ("split", "split", "Tách câu tại đầu phát"),
                              ("merge", "merge", "Gộp câu đang chọn với câu sau"),
                              ("delete", "trash", "Xoá câu đang chọn"), (None, None, None),
                              ("play", "play", "Nghe câu đang chọn (bấm đúp vào câu cũng được)"),
                              ("redub", "refresh", "Tạo lại giọng câu đang chọn")):
            if name is None:
                tb.addSpacing(10)
                continue
            b = IconButton(ic, tip, 30)
            b.clicked.connect(lambda _=False, n=name: self.actionRequested.emit(n))
            tb.addWidget(b)
        tb.addSpacing(12)
        self.hint = label("Bấm câu để chọn · bấm đúp để nghe · Ctrl + lăn chuột để phóng to", "muted")
        tb.addWidget(self.hint)
        tb.addStretch(1)
        for ic, tip, fn in (("minus", "Thu nhỏ", lambda: self.zoom(0.7)), ("plus", "Phóng to", lambda: self.zoom(1.4)),
                            ("fit", "Vừa khung (xem cả video)", self.fit)):
            b = IconButton(ic, tip, 30)
            b.clicked.connect(fn)
            tb.addWidget(b)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 4, 0, 0)
        lay.setSpacing(2)
        lay.addLayout(tb)
        lay.addWidget(self.canvas, 1)
        lay.addWidget(self.bar)

    # ------------------------------------------------------------------ dữ liệu
    def set_data(self, duration: float, segs: list, dub: list, keep=(0.0, 0.0), cuts=None):
        if abs(duration - self.duration) > 0.01:
            self._fitted = self._opened = False
        self.duration, self.segs, self.dub = float(duration or 0), segs, dub or []
        self.keep, self.cuts = tuple(keep or (0, 0)), cuts or []
        if not self._fitted and not self._opened and self.duration and self.canvas.width() > _Canvas.HEAD + 50:
            self._opened = True
            self.fit(90)
        self._sync_bar()
        self.canvas.update()

    def set_thumbs(self, pm: QPixmap | None, step: float, count: int):
        self.thumbs, self.thumb_step, self.thumb_count = pm, step, count
        self.canvas.update()

    def set_wave(self, env: np.ndarray | None, hop: float):
        self.wave, self.wave_hop = env, hop
        self.canvas.update()

    def set_pos(self, t: float, follow: bool = False):
        self.pos = t
        vw = self._view_secs()
        if follow and self.duration and not (self.t0 <= t <= self.t0 + vw * 0.92):
            self.scroll_to(t - vw * 0.1)
        self.canvas.update()

    def set_selected(self, row: int):
        self.selected = row
        if 0 <= row < len(self.segs):
            s = self.segs[row]
            vw = self._view_secs()
            if not (self.t0 <= s.start and s.end <= self.t0 + vw):
                self.scroll_to(s.start - vw * 0.2)
        self.canvas.update()

    def set_active(self, row: int):
        if row != self.active:
            self.active = row
            self.canvas.update()

    # ------------------------------------------------------------------ phóng to / cuộn
    def _view_secs(self) -> float:
        return max(1.0, (self.canvas.width() - _Canvas.HEAD) / self.pps)

    def fit(self, max_secs: float | None = None):
        """Xem cả video; max_secs: lần đầu mở video dài chỉ hiện ~90 giây cho đọc được chữ trong từng câu."""
        if self.duration:
            span = min(self.duration, max_secs) if max_secs else self.duration
            self.pps = max(0.5, (self.canvas.width() - _Canvas.HEAD - 10) / span)
            self.t0 = 0.0
            self._fitted = span >= self.duration
            self._sync_bar()
            self.canvas.update()

    def zoom(self, f: float, at: float | None = None, at_x: float | None = None):
        if not self.duration:
            return
        lo = (self.canvas.width() - _Canvas.HEAD - 10) / self.duration
        at = self.pos if at is None else at
        at_x = self.canvas.x(at) if at_x is None else at_x
        self.pps = min(400.0, max(lo, self.pps * f))
        self.t0 = at - (at_x - _Canvas.HEAD) / self.pps
        self._fitted = False
        self.scroll_to(self.t0)

    def scroll_by(self, secs: float):
        self.scroll_to(self.t0 + secs)

    def scroll_to(self, t0: float):
        self.t0 = max(0.0, min(t0, max(0.0, self.duration - self._view_secs())))
        self._sync_bar()
        self.canvas.update()

    def _sync_bar(self):
        total = int(self.duration * self.pps)
        view = int(self.canvas.width() - _Canvas.HEAD)
        self.bar.blockSignals(True)
        self.bar.setRange(0, max(0, total - view))
        self.bar.setPageStep(max(1, view))
        self.bar.setValue(int(self.t0 * self.pps))
        self.bar.blockSignals(False)
        self.bar.setVisible(total > view)

    def _bar_moved(self, v: int):
        self.t0 = v / self.pps
        self.canvas.update()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if not self.duration:
            return
        if not self._opened and self.canvas.width() > _Canvas.HEAD + 50:
            self._opened = True
            self.fit(90)
        elif self._fitted:
            self.fit()
        else:
            self._sync_bar()
