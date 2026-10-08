"""Widget chỉnh sửa vẽ tay: khoanh vùng trên ảnh (xoá phụ đề / crop) và thanh thời gian cắt video."""
from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QSizePolicy, QWidget

from . import icons, theme


class RegionEditor(QWidget):
    """Hiển thị 1 khung hình, cho phép kéo chuột vẽ/di chuyển/đổi cỡ các vùng chữ nhật (toạ độ 0..1)."""
    changed = Signal(list)

    def __init__(self, multi: bool = True, parent=None):
        super().__init__(parent)
        self.multi = multi
        self.pix: QPixmap | None = None
        self.regions: list[dict] = []
        self.overlay: QPixmap | None = None
        self.drag = None
        self.setMouseTracking(True)
        self.setMinimumHeight(220)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.hint = "Bấm 'Lấy khung hình' rồi kéo chuột để khoanh vùng"

    def set_image(self, path_or_pix):
        self.pix = QPixmap(path_or_pix) if isinstance(path_or_pix, str) else path_or_pix
        self.updateGeometry()
        self.update()

    def set_regions(self, regs: list[dict]):
        self.regions = [dict(r) for r in (regs or [])]
        self.update()

    def heightForWidth(self, w: int) -> int:
        if self.pix and not self.pix.isNull():
            return int(w * self.pix.height() / self.pix.width())
        return int(w * 9 / 16)

    def hasHeightForWidth(self) -> bool:
        return True

    def img_rect(self) -> QRectF:
        r = QRectF(self.rect())
        if not self.pix or self.pix.isNull():
            return r.adjusted(1, 1, -1, -1)
        sc = min(r.width() / self.pix.width(), r.height() / self.pix.height())
        w, h = self.pix.width() * sc, self.pix.height() * sc
        return QRectF((r.width() - w) / 2, (r.height() - h) / 2, w, h)

    def _rel(self, pos: QPointF) -> QPointF:
        ir = self.img_rect()
        return QPointF(min(1, max(0, (pos.x() - ir.x()) / ir.width())), min(1, max(0, (pos.y() - ir.y()) / ir.height())))

    def _rect_of(self, r: dict) -> QRectF:
        ir = self.img_rect()
        return QRectF(ir.x() + r["x"] * ir.width(), ir.y() + r["y"] * ir.height(), r["w"] * ir.width(), r["h"] * ir.height())

    def _hit(self, pos: QPointF):
        for i in range(len(self.regions) - 1, -1, -1):
            rr = self._rect_of(self.regions[i])
            close = QRectF(rr.right() - 18, rr.top(), 18, 18)
            if close.contains(pos):
                return i, "del"
            if QRectF(rr.right() - 9, rr.bottom() - 9, 18, 18).contains(pos):
                return i, "resize"
            if rr.contains(pos):
                return i, "move"
        return -1, ""

    def mousePressEvent(self, e):
        if not self.pix:
            return
        pos = e.position()
        i, kind = self._hit(pos)
        p = self._rel(pos)
        if kind == "del":
            del self.regions[i]
            self.update()
            self.changed.emit(self.regions)
            return
        if kind == "resize":
            self.drag = ("resize", i, None)
        elif kind == "move":
            r = self.regions[i]
            self.drag = ("move", i, (p.x() - r["x"], p.y() - r["y"]))
        else:
            if not self.multi:
                self.regions = []
            self.regions.append({"x": p.x(), "y": p.y(), "w": 0.0, "h": 0.0})
            self.drag = ("new", len(self.regions) - 1, (p.x(), p.y()))
        self.update()

    def mouseMoveEvent(self, e):
        pos = e.position()
        if not self.drag:
            _, kind = self._hit(pos)
            self.setCursor({"move": Qt.CursorShape.SizeAllCursor, "resize": Qt.CursorShape.SizeFDiagCursor,
                            "del": Qt.CursorShape.PointingHandCursor}.get(kind, Qt.CursorShape.CrossCursor))
            return
        kind, i, data = self.drag
        p = self._rel(pos)
        r = self.regions[i]
        if kind == "new":
            ox, oy = data
            r.update(x=min(p.x(), ox), y=min(p.y(), oy), w=abs(p.x() - ox), h=abs(p.y() - oy))
        elif kind == "move":
            dx, dy = data
            r["x"] = min(1 - r["w"], max(0, p.x() - dx))
            r["y"] = min(1 - r["h"], max(0, p.y() - dy))
        else:
            r["w"] = max(0.01, min(1 - r["x"], p.x() - r["x"]))
            r["h"] = max(0.01, min(1 - r["y"], p.y() - r["y"]))
        self.update()

    def mouseReleaseEvent(self, e):
        if self.drag:
            self.drag = None
            self.regions = [r for r in self.regions if r["w"] > 0.01 and r["h"] > 0.01]
            for r in self.regions:
                for k in r:
                    r[k] = round(float(r[k]), 4)
            self.update()
            self.changed.emit(self.regions)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        r = QRectF(self.rect())
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor("#05070A"))
        p.drawRoundedRect(r, 12, 12)
        if not self.pix or self.pix.isNull():
            p.setPen(QColor(theme.MUTED))
            p.setFont(theme.font(10))
            icons.paint_icon(p, "image", QRectF(r.center().x() - 18, r.center().y() - 40, 36, 36), theme.MUTED)
            p.drawText(r.adjusted(10, 30, -10, 0), Qt.AlignmentFlag.AlignCenter, self.hint)
            return
        ir = self.img_rect()
        p.drawPixmap(ir.toRect(), self.overlay or self.pix)
        for i, reg in enumerate(self.regions):
            rr = self._rect_of(reg)
            p.setBrush(theme.c(theme.ACCENT2, 45))
            p.setPen(QPen(QColor(theme.ACCENT2), 2))
            p.drawRoundedRect(rr, 4, 4)
            # nút xoá
            close = QRectF(rr.right() - 18, rr.top(), 18, 18)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(theme.ACCENT))
            p.drawRoundedRect(close, 4, 4)
            icons.paint_icon(p, "x", close.adjusted(3, 3, -3, -3), "#FFFFFF", 2.4)
            # tay cầm đổi cỡ
            p.setBrush(QColor("#FFFFFF"))
            p.drawEllipse(QPointF(rr.right(), rr.bottom()), 5, 5)
            p.setPen(QColor("#FFFFFF"))
            p.setFont(theme.font(8.5, QFont.Weight.Bold))
            p.drawText(QRectF(rr.left() + 6, rr.top() + 3, 120, 16), Qt.AlignmentFlag.AlignLeft,
                       f"Vùng {i + 1}" if self.multi else "Vùng giữ lại")


class Timeline(QWidget):
    """Thanh thời gian: đoạn giữ (xanh), đoạn cắt bỏ (đỏ), các câu phụ đề (xanh dương), đầu phát (trắng)."""
    seekRequested = Signal(float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.duration = 0.0
        self.keep: tuple[float, float] = (0, 0)
        self.cuts: list = []
        self.segs: list = []
        self.pos = 0.0
        self.mark: float | None = None
        self.setFixedHeight(54)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def set_data(self, duration, keep, cuts, segs):
        self.duration, self.keep, self.cuts, self.segs = duration, keep, cuts, segs
        self.update()

    def set_pos(self, t):
        self.pos = t
        self.update()

    def mousePressEvent(self, e):
        if self.duration:
            self.seekRequested.emit(max(0.0, min(1.0, e.position().x() / self.width())) * self.duration)

    def mouseMoveEvent(self, e):
        if e.buttons() and self.duration:
            self.seekRequested.emit(max(0.0, min(1.0, e.position().x() / self.width())) * self.duration)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(0, 4, 0, -4)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(theme.CARD2))
        p.drawRoundedRect(r, 8, 8)
        if not self.duration:
            return
        W = r.width()

        def x(t):
            return r.left() + t / self.duration * W

        ks, ke = self.keep
        ke = ke or self.duration
        p.setBrush(theme.c(theme.OK, 70))
        p.drawRoundedRect(QRectF(x(ks), r.top(), x(ke) - x(ks), r.height()), 8, 8)
        p.setBrush(theme.c(theme.ERR, 150))
        for a, b in self.cuts:
            p.drawRect(QRectF(x(a), r.top(), max(2, x(b) - x(a)), r.height()))
        p.setBrush(theme.c(theme.INFO, 200))
        for s in self.segs:
            p.drawRoundedRect(QRectF(x(s.start), r.bottom() - 9, max(1.5, x(s.end) - x(s.start)), 6), 2, 2)
        if self.mark is not None:
            p.setPen(QPen(QColor(theme.WARN), 2, Qt.PenStyle.DashLine))
            p.drawLine(QPointF(x(self.mark), r.top()), QPointF(x(self.mark), r.bottom()))
        p.setPen(QPen(QColor("#FFFFFF"), 2))
        p.drawLine(QPointF(x(self.pos), r.top() - 3), QPointF(x(self.pos), r.bottom() + 3))
