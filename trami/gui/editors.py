"""Widget chỉnh sửa vẽ tay: khoanh vùng trên ảnh (xoá phụ đề / crop) và thanh thời gian cắt video."""
from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QSizePolicy, QWidget

from . import icons, theme

# Chỉnh vùng chữ nhật bằng chuột (dùng chung cho ảnh khoanh vùng và lớp vùng che trên video):
# 4 chấm giữa 4 cạnh (L/R/T/B) đổi dài ngắn / cao thấp, góc (TL/TR/BL/BR) đổi cả hai, kéo bên trong để di chuyển.
REGION_CURSORS = {"L": Qt.CursorShape.SizeHorCursor, "R": Qt.CursorShape.SizeHorCursor,
                  "T": Qt.CursorShape.SizeVerCursor, "B": Qt.CursorShape.SizeVerCursor,
                  "TL": Qt.CursorShape.SizeFDiagCursor, "BR": Qt.CursorShape.SizeFDiagCursor,
                  "TR": Qt.CursorShape.SizeBDiagCursor, "BL": Qt.CursorShape.SizeBDiagCursor,
                  "move": Qt.CursorShape.SizeAllCursor, "del": Qt.CursorShape.PointingHandCursor}


def edge_handles(rr: QRectF) -> dict[str, QPointF]:
    c = rr.center()
    return {"L": QPointF(rr.left(), c.y()), "R": QPointF(rr.right(), c.y()),
            "T": QPointF(c.x(), rr.top()), "B": QPointF(c.x(), rr.bottom())}


def hit_region(rr: QRectF, pos: QPointF, tol: float = 7.0) -> str:
    """Chỗ bấm thuộc phần nào của vùng: chấm cạnh / góc / cạnh / bên trong ('' = ngoài vùng)."""
    for k, pt in edge_handles(rr).items():
        if abs(pos.x() - pt.x()) <= tol * 1.4 and abs(pos.y() - pt.y()) <= tol * 1.4:
            return k
    near_l, near_r = abs(pos.x() - rr.left()) <= tol, abs(pos.x() - rr.right()) <= tol
    near_t, near_b = abs(pos.y() - rr.top()) <= tol, abs(pos.y() - rr.bottom()) <= tol
    in_x = rr.left() - tol <= pos.x() <= rr.right() + tol
    in_y = rr.top() - tol <= pos.y() <= rr.bottom() + tol
    if not (in_x and in_y):
        return ""
    v = "T" if near_t else "B" if near_b else ""
    h = "L" if near_l else "R" if near_r else ""
    if v or h:
        return v + h
    return "move" if rr.contains(pos) else ""


def drag_region(r0: dict, kind: str, dx: float, dy: float, mw: float = 0.01, mh: float = 0.01) -> dict:
    """Vùng mới (toạ độ 0..1) sau khi kéo phần `kind` đi (dx, dy)."""
    x0, y0, w0, h0 = float(r0["x"]), float(r0["y"]), float(r0["w"]), float(r0["h"])
    if kind == "move":
        return {"x": min(1 - w0, max(0.0, x0 + dx)), "y": min(1 - h0, max(0.0, y0 + dy)), "w": w0, "h": h0}
    x1, y1 = x0 + w0, y0 + h0
    if "L" in kind:
        x0 = min(x1 - mw, max(0.0, x0 + dx))
    if "R" in kind:
        x1 = max(x0 + mw, min(1.0, x1 + dx))
    if "T" in kind:
        y0 = min(y1 - mh, max(0.0, y0 + dy))
    if "B" in kind:
        y1 = max(y0 + mh, min(1.0, y1 + dy))
    return {"x": x0, "y": y0, "w": x1 - x0, "h": y1 - y0}


def paint_handles(p: QPainter, rr: QRectF, color: str, size: float = 8.0):
    """4 chấm vuông nhỏ giữa 4 cạnh."""
    p.save()
    pen = QPen(QColor(color), 1.5)
    pen.setCosmetic(True)
    p.setPen(pen)
    p.setBrush(QColor("#FFFFFF"))
    for pt in edge_handles(rr).values():
        p.drawRoundedRect(QRectF(pt.x() - size / 2, pt.y() - size / 2, size, size), 2, 2)
    p.restore()


class RegionEditor(QWidget):
    """Hiển thị 1 khung hình, cho phép kéo chuột vẽ/di chuyển/đổi cỡ các vùng chữ nhật (toạ độ 0..1)."""
    changed = Signal(list)

    def __init__(self, multi: bool = True, parent=None):
        super().__init__(parent)
        self.multi = multi
        self.pix: QPixmap | None = None
        self.regions: list[dict] = []
        self.extra: list[dict] = []          # danh sách còn lại (vùng phụ đề / logo) – chỉ vẽ mờ để biết vị trí
        self.color = theme.ACCENT2
        self.label = "Vùng"
        self.extra_label = "Logo"
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
            if QRectF(rr.right() - 18, rr.top(), 18, 18).contains(pos):
                return i, "del"
            kind = hit_region(rr, pos)
            if kind:
                return i, kind
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
        if kind:
            self.drag = (kind, i, (dict(self.regions[i]), p))
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
            self.setCursor(REGION_CURSORS.get(kind, Qt.CursorShape.CrossCursor))
            return
        kind, i, data = self.drag
        p = self._rel(pos)
        r = self.regions[i]
        if kind == "new":
            ox, oy = data
            r.update(x=min(p.x(), ox), y=min(p.y(), oy), w=abs(p.x() - ox), h=abs(p.y() - oy))
        else:
            r0, p0 = data
            r.update(drag_region(r0, kind, p.x() - p0.x(), p.y() - p0.y()))
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
        dim = QPen(QColor(theme.MUTED), 1.2, Qt.PenStyle.DotLine)
        for i, reg in enumerate(self.extra):
            rr = self._rect_of(reg)
            p.setPen(dim)
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(rr, 4, 4)
            p.setFont(theme.font(8.5, QFont.Weight.Bold))
            p.drawText(QRectF(rr.left() + 6, rr.top() + 3, 120, 16), Qt.AlignmentFlag.AlignLeft,
                       f"{self.extra_label} {i + 1}")
        for i, reg in enumerate(self.regions):
            rr = self._rect_of(reg)
            p.setBrush(theme.c(self.color, 45))
            p.setPen(QPen(QColor(self.color), 2))
            p.drawRoundedRect(rr, 4, 4)
            # nút xoá
            close = QRectF(rr.right() - 18, rr.top(), 18, 18)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(theme.ACCENT))
            p.drawRoundedRect(close, 4, 4)
            icons.paint_icon(p, "x", close.adjusted(3, 3, -3, -3), "#FFFFFF", 2.4)
            # 4 chấm giữa 4 cạnh: kéo để đổi cao / thấp / dài / ngắn
            paint_handles(p, rr, self.color)
            p.setPen(QColor("#FFFFFF"))
            p.setFont(theme.font(8.5, QFont.Weight.Bold))
            p.drawText(QRectF(rr.left() + 6, rr.top() + 3, 120, 16), Qt.AlignmentFlag.AlignLeft,
                       f"{self.label} {i + 1}" if self.multi else "Vùng giữ lại")


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
        self.dub: list = []      # [đầu, cuối, câu, độ vừa] của giọng lồng đã xếp -> dải thứ 2
        self.setFixedHeight(62)
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
        for s in self.segs:   # dải dưới: lời thoại gốc
            p.drawRoundedRect(QRectF(x(s.start), r.bottom() - 9, max(1.5, x(s.end) - x(s.start)), 6), 2, 2)
        fit_col = {"ok": "#4ADE80", "fast": "#F5B841", "long": "#F87171"}
        for a, b, _k, fit in self.dub:   # dải trên: giọng lồng (xanh vừa · vàng đọc nhanh · đỏ quá dài)
            p.setBrush(QColor(fit_col.get(fit, "#4ADE80")))
            p.drawRoundedRect(QRectF(x(a), r.bottom() - 18, max(1.5, x(b) - x(a)), 6), 2, 2)
        if self.mark is not None:
            p.setPen(QPen(QColor(theme.WARN), 2, Qt.PenStyle.DashLine))
            p.drawLine(QPointF(x(self.mark), r.top()), QPointF(x(self.mark), r.bottom()))
        p.setPen(QPen(QColor("#FFFFFF"), 2))
        p.drawLine(QPointF(x(self.pos), r.top() - 3), QPointF(x(self.pos), r.bottom() + 3))
