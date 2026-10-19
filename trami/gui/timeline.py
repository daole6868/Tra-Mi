"""Timeline nhiều track kiểu trình dựng phim (Concat / CapCut), đặt ngang dưới cùng trang dự án.

Track: Phụ đề (khối từng câu, màu theo người nói) · Lồng tiếng (mỗi câu 1 đoạn giọng riêng có sóng âm, màu theo độ
vừa; câu chưa có giọng: khung nét đứt) · Video (dải ảnh thu nhỏ) · Tiếng gốc (đường sóng âm).
Bấm khối = chọn câu, bấm đúp = nghe câu, kéo đoạn giọng lồng sang trái / phải = dời đúng chỗ (xếp lại ngay),
chuột phải đoạn giọng = nghe / đặt lại vị trí / tạo lại giọng; bấm thước / chỗ trống = tua;
Ctrl + lăn chuột = phóng to/thu nhỏ, lăn chuột = cuộn ngang.
"""
from __future__ import annotations

import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import QHBoxLayout, QMenu, QScrollBar, QVBoxLayout, QWidget

from . import icons, theme
from .subs_model import spk_color
from .widgets import IconButton, label

FIT_COL = {"ok": "#4ADE80", "fast": "#F5B841", "long": "#F87171"}
FIT_TIP = {"ok": "vừa khít", "fast": "đọc nhanh hơn cho vừa", "long": "quá dài – nên sửa ngắn lại / AI rút gọn"}
TICKS = [0.5, 1, 2, 5, 10, 15, 30, 60, 120, 300, 600, 1200]


def _tc(t: float, step: float) -> str:
    m, s = divmod(max(0.0, t), 60)
    h, m = divmod(int(m), 60)
    base = f"{h}:{m:02d}:{int(s):02d}" if h else f"{m:02d}:{int(s):02d}"
    return base + (f".{int(round((s % 1) * 10))}" if step < 1 else "")


class _Canvas(QWidget):
    HEAD = 118      # cột tên track bên trái
    RULER = 24
    TRACKS = [("subs", "Phụ đề", "subtitles", 38), ("dub", "Lồng tiếng", "mic", 38),
              ("video", "Video", "film", 44), ("audio", "Tiếng gốc", "volume", 30)]
    MIN_SCALE, MAX_SCALE = 0.7, 1.3   # các dòng co / giãn theo chiều cao khung: không bị cắt, không thừa chỗ trống

    def __init__(self, tl: "TrackTimeline"):
        super().__init__(tl)
        self.tl = tl
        self.setMouseTracking(True)
        # màn thấp: các dòng tự co (tối đa còn 70 %) thay vì bị cắt mất dòng dưới cùng; màn cao: giãn ra
        self.setMinimumHeight(self.RULER + 4 + sum(round(h * self.MIN_SCALE) + 6 for *_, h in self.TRACKS))
        self._drag = False
        self._hover = None
        self._dpress = None    # (câu, x lúc bấm, đầu, cuối) – đang bấm giữ 1 đoạn giọng lồng
        self._ghost = None     # (câu, đầu mới, cuối mới) – đang kéo đoạn giọng lồng

    # ------------------------------------------------------------------ toạ độ
    def x(self, t: float) -> float:
        return self.HEAD + (t - self.tl.t0) * self.tl.pps

    def t(self, x: float) -> float:
        return max(0.0, min(self.tl.duration, self.tl.t0 + (x - self.HEAD) / self.tl.pps))

    def _scale(self) -> float:
        room = self.height() - self.RULER - 4 - 6 * len(self.TRACKS)
        return max(self.MIN_SCALE, min(self.MAX_SCALE, room / sum(h for *_, h in self.TRACKS)))

    def track_rect(self, key: str) -> QRectF:
        y, sc = self.RULER + 4, self._scale()
        for k, _, _, h in self.TRACKS:
            h = round(h * sc)
            if k == key:
                return QRectF(self.HEAD, y, self.width() - self.HEAD, h)
            y += h + 6
        return QRectF()

    def _dub_at(self, pos: QPointF):
        """Đoạn giọng lồng dưới con trỏ: (câu, đầu, cuối, độ vừa) hoặc None."""
        if pos.x() < self.HEAD or not self.track_rect("dub").contains(pos):
            return None
        t = self.t(pos.x())
        tol = 3 / max(self.tl.pps, 1e-6)
        return next(((int(k), a, b, f) for a, b, k, f in self.tl.dub if a - tol <= t <= b + tol), None)

    def _hit(self, pos: QPointF) -> int | None:
        """Câu nằm dưới con trỏ (track Phụ đề hoặc Lồng tiếng – cả khung nét đứt của câu chưa có giọng)."""
        if pos.x() < self.HEAD:
            return None
        t = self.t(pos.x())
        if self.track_rect("subs").contains(pos):
            return next((i for i, s in enumerate(self.tl.segs) if s.start <= t <= s.end), None)
        if self.track_rect("dub").contains(pos):
            d = self._dub_at(pos)
            if d:
                return d[0]
            placed = {int(k) for *_x, k, _f in self.tl.dub}
            return next((i for i, s in enumerate(self.tl.segs) if s.start <= t <= s.end and i not in placed), None)
        return None

    # ------------------------------------------------------------------ chuột
    def mousePressEvent(self, e):
        if not self.tl.duration or e.button() != Qt.MouseButton.LeftButton or e.position().x() < self.HEAD:
            return
        d = self._dub_at(e.position())
        if d:
            self._dpress = (d[0], e.position().x(), d[1], d[2])
        row = self._hit(e.position())
        if row is not None:
            self.tl.lineClicked.emit(row)
            return
        self._drag = True
        self.tl.seekRequested.emit(self.t(e.position().x()))

    def mouseDoubleClickEvent(self, e):
        self._dpress = self._ghost = None
        row = self._hit(e.position())
        if row is not None:
            self.tl.lineActivated.emit(row)

    def mouseMoveEvent(self, e):
        if self._drag and self.tl.duration:
            self.tl.seekRequested.emit(self.t(e.position().x()))
            return
        if self._dpress and e.buttons() & Qt.MouseButton.LeftButton:
            row, x0, a, b = self._dpress
            dx = e.position().x() - x0
            if self._ghost or abs(dx) > 4:   # kéo đoạn giọng lồng sang chỗ mới
                d = max(-a, dx / self.tl.pps)
                self._ghost = (row, a + d, b + d)
                self.setCursor(Qt.CursorShape.SizeHorCursor)
                self.update()
            return
        row = self._hit(e.position())
        if row != self._hover:
            self._hover = row
            s = self.tl.segs[row] if row is not None and row < len(self.tl.segs) else None
            d = self._dub_at(e.position())
            if s and d:
                shift = float(s.extra.get("dub_shift", 0) or 0)
                tip = (f"#{row + 1} · giọng lồng {d[2] - d[1]:.1f} giây · {FIT_TIP.get(d[3], '')}"
                       + (f" · đã dời {shift:+.2f} s" if shift else "")
                       + f"\n{s.trans}\nKéo sang trái / phải để dời · bấm đúp để nghe · chuột phải: thêm lựa chọn")
            elif s and self.track_rect("dub").contains(e.position()):
                why = ("chưa có lời tiếng Việt" if not (s.trans or "").strip() else "đã tắt lồng câu này"
                       if s.extra.get("nodub") else "đang hát – giữ giọng gốc" if s.extra.get("song")
                       else "chưa có giọng lồng – bấm Tạo lại giọng câu này")
                tip = f"#{row + 1} {why}\n{s.trans or s.text}"
            else:
                tip = f"#{row + 1} {s.speaker or ''}\n{s.trans or s.text}" if s else ""
            self.setToolTip(tip)
            self.setCursor(Qt.CursorShape.PointingHandCursor if row is not None else Qt.CursorShape.ArrowCursor)
            self.update()

    def mouseReleaseEvent(self, e):
        self._drag = False
        g, pr = self._ghost, self._dpress
        self._dpress = self._ghost = None
        if g and pr:
            self.setCursor(Qt.CursorShape.ArrowCursor)
            delta = round(g[1] - pr[2], 3)
            if abs(delta) >= 0.02:
                self.tl.dubMoved.emit(g[0], delta)
            self.update()

    def contextMenuEvent(self, e):
        d = self._dub_at(QPointF(e.pos()))
        if not d:
            return
        row = d[0]
        s = self.tl.segs[row] if row < len(self.tl.segs) else None
        self.tl.lineClicked.emit(row)
        m = QMenu(self)
        m.addAction(f"Nghe câu #{row + 1}", lambda: self.tl.lineActivated.emit(row))
        act = m.addAction("Đặt lại đúng lúc nhân vật nói", lambda: self.tl.dubReset.emit(row))
        act.setEnabled(bool(s and s.extra.get("dub_shift")))
        m.addAction("Tạo lại giọng câu này", lambda: self.tl.actionRequested.emit("redub"))
        m.exec(e.globalPos())

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
        # lồng tiếng: mỗi câu 1 đoạn giọng riêng (sóng âm thật), đặt đúng chỗ đã xếp; câu chưa có giọng: nét đứt
        rd = self.track_rect("dub")
        placed = {int(k) for *_x, k, _f in tl.dub}
        if tl.dub:
            p.setFont(theme.font(7.6))
            for i, s in enumerate(tl.segs):
                if i in placed or s.end < t_lo or s.start > t_hi:
                    continue
                r = QRectF(self.x(s.start), rd.top() + 4, max(2.0, (s.end - s.start) * tl.pps - 1), rd.height() - 8)
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.setPen(QPen(QColor(theme.MUTED), 1, Qt.PenStyle.DashLine))
                p.drawRoundedRect(r, 4, 4)
                why = ("chưa dịch" if not (s.trans or "").strip() else "bỏ lồng" if s.extra.get("nodub")
                       else "🎵 đang hát" if s.extra.get("song") else "chưa có giọng")
                if r.width() > 34:
                    p.setPen(QColor(theme.MUTED))
                    p.drawText(r.adjusted(4, 0, -2, 0), Qt.AlignmentFlag.AlignVCenter,
                               p.fontMetrics().elidedText(why, Qt.TextElideMode.ElideRight, int(r.width() - 6)))
        p.setFont(theme.font(7.6, QFont.Weight.DemiBold))
        ghost_row = self._ghost[0] if self._ghost else None
        for a, b, k, fit in tl.dub:
            if b < t_lo or a > t_hi:
                continue
            self._paint_clip(p, rd, a, b, int(k), fit, faded=int(k) == ghost_row)
        if self._ghost:
            row, a, b = self._ghost
            fit = next((f for *_x, k, f in tl.dub if int(k) == row), "ok")
            self._paint_clip(p, rd, a, b, row, fit, ghost=True)
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


    def _paint_clip(self, p: QPainter, rd: QRectF, a: float, b: float, k: int, fit: str, faded: bool = False,
                    ghost: bool = False):
        tl = self.tl
        r = QRectF(self.x(a), rd.top() + 2, max(3.0, (b - a) * tl.pps - 1), rd.height() - 4)
        col = QColor(FIT_COL.get(fit, "#4ADE80"))
        sel = k == tl.selected
        fill = QColor(col)
        fill.setAlpha(40 if faded else (120 if sel or ghost else 70))
        p.setBrush(fill)
        p.setPen(QPen(QColor("#FFFFFF") if ghost else col, 2 if sel or ghost else 1,
                      Qt.PenStyle.DashLine if ghost else Qt.PenStyle.SolidLine))
        p.drawRoundedRect(r, 4, 4)
        env = tl.dub_wave
        if env is not None and len(env) and not ghost and r.width() > 4:
            # sóng âm của chính đoạn giọng này (lấy từ bản lồng đã ghép, đúng vị trí đã xếp)
            mid, half = r.center().y() + 3, r.height() / 2 - 6
            wc = QColor(col)
            wc.setAlpha(110 if faded else 230)
            p.setPen(QPen(wc, 1))
            px, end = int(r.left()) + 2, int(r.right()) - 1
            while px < end:
                t0, t1 = self.t(px), self.t(px + 1)
                ia = int(t0 / tl.dub_hop)
                ib = max(ia + 1, int(t1 / tl.dub_hop))
                v = float(env[ia:ib].max()) if ia < len(env) else 0.0
                if v > 0.02:
                    p.drawLine(QPointF(px, mid - v * half), QPointF(px, mid + v * half))
                px += 1
        if r.width() > 24:
            p.setPen(QColor("#FFFFFF"))
            p.drawText(r.adjusted(5, 1, -3, 0), Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft, f"#{k + 1}")


class TrackTimeline(QWidget):
    seekRequested = Signal(float)
    lineClicked = Signal(int)       # bấm 1 câu -> chọn câu đó
    lineActivated = Signal(int)     # bấm đúp -> nghe câu
    actionRequested = Signal(str)   # nút trên thanh công cụ: insert / split / merge / delete / play / redub
    dubMoved = Signal(int, float)   # kéo đoạn giọng lồng của câu: (câu, số giây dời thêm)
    dubReset = Signal(int)          # đặt đoạn giọng lồng về đúng lúc nhân vật nói

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
        self.dub_wave: np.ndarray | None = None   # sóng âm bản lồng tiếng đang dùng
        self.dub_hop = 0.05
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
        self.hint = label("Bấm câu để chọn · bấm đúp để nghe · kéo đoạn giọng lồng để dời · Ctrl + lăn chuột để phóng to",
                          "muted")
        tb.addWidget(self.hint)
        tb.addStretch(1)
        for ic, tip, fn in (("minus", "Thu nhỏ", lambda: self.zoom(0.7)), ("plus", "Phóng to", lambda: self.zoom(1.4)),
                            ("fit", "Vừa khung (xem cả video)", self.fit)):
            b = IconButton(ic, tip, 30)
            b.clicked.connect(fn)
            tb.addWidget(b)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)
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

    def set_dub_wave(self, env: np.ndarray | None, hop: float = 0.05):
        self.dub_wave, self.dub_hop = env, hop
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
