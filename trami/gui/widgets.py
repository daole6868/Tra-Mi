"""Các widget tự vẽ bằng QPainter: thẻ bo góc, nút gradient, công tắc, thanh tab, thanh tiến độ, vùng kéo thả…"""
from __future__ import annotations

from typing import Callable

from PySide6.QtCore import (QEasingCurve, QPoint, QPropertyAnimation, QRect, QRectF, QSize, Qt, QTimer, Signal,
                            Property)
from PySide6.QtGui import QColor, QFont, QLinearGradient, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (QAbstractButton, QComboBox, QDoubleSpinBox, QFormLayout, QFrame, QGraphicsDropShadowEffect,
                               QHBoxLayout, QLabel, QLayout, QPushButton, QSizePolicy, QSlider, QVBoxLayout, QWidget)

from . import icons, theme


def label(text: str = "", kind: str = "", wrap: bool = False) -> QLabel:
    lb = QLabel(text)
    if kind:
        lb.setProperty(kind, True)
    lb.setWordWrap(wrap)
    return lb


def shadow(w: QWidget, blur: int = 28, alpha: int = 110, dy: int = 6):
    eff = QGraphicsDropShadowEffect(w)
    eff.setBlurRadius(blur)
    eff.setOffset(0, dy)
    eff.setColor(QColor(0, 0, 0, alpha))
    w.setGraphicsEffect(eff)


# ====================================================================== Thẻ (card)

class Card(QFrame):
    """Khung bo góc có viền mảnh, nền gradient nhẹ."""

    def __init__(self, parent=None, padding: int = 16, radius: int = theme.RADIUS, accent: bool = False):
        super().__init__(parent)
        self.radius = radius
        self.accent = accent
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, False)
        self.lay = QVBoxLayout(self)
        self.lay.setContentsMargins(padding, padding, padding, padding)
        self.lay.setSpacing(10)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        g = QLinearGradient(0, 0, 0, r.height())
        g.setColorAt(0, QColor(theme.CARD))
        g.setColorAt(1, QColor("#151A25"))
        p.setBrush(g)
        p.setPen(QPen(QColor(theme.ACCENT if self.accent else theme.BORDER), 1))
        p.drawRoundedRect(r, self.radius, self.radius)

    def add(self, w):
        if isinstance(w, QWidget):
            self.lay.addWidget(w)
        else:
            self.lay.addLayout(w)
        return w


class SectionCard(Card):
    """Thẻ có tiêu đề + icon."""

    def __init__(self, title: str, icon_name: str = "", subtitle: str = "", parent=None):
        super().__init__(parent)
        head = QHBoxLayout()
        head.setSpacing(10)
        if icon_name:
            ic = IconBadge(icon_name)
            head.addWidget(ic)
        tbox = QVBoxLayout()
        tbox.setSpacing(0)
        tbox.addWidget(label(title, "h2"))
        if subtitle:
            tbox.addWidget(label(subtitle, "muted", wrap=True))
        head.addLayout(tbox, 1)
        self.head = head
        self.lay.addLayout(head)


class IconBadge(QWidget):
    """Ô vuông bo góc nền mờ màu nhấn, chứa icon."""

    def __init__(self, name: str, size: int = 34, color: str = theme.ACCENT2, parent=None):
        super().__init__(parent)
        self.name, self.color = name, color
        self.setFixedSize(size, size)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect())
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(theme.c(self.color, 38))
        p.drawRoundedRect(r, 9, 9)
        m = r.width() * 0.22
        icons.paint_icon(p, self.name, r.adjusted(m, m, -m, -m), self.color, 2.0)


# ====================================================================== Nút

class GradientButton(QPushButton):
    """Nút chính: nền gradient đỏ-cam, bo tròn, có icon, hiệu ứng hover."""

    def __init__(self, text: str, icon_name: str = "", big: bool = False, parent=None):
        super().__init__(text, parent)
        self.icon_name = icon_name
        self.big = big
        self._hover = 0.0
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMinimumHeight(48 if big else 38)
        f = theme.font(11.5 if big else 10, QFont.Weight.Bold)
        self.setFont(f)
        self._anim = QPropertyAnimation(self, b"hover", self)
        self._anim.setDuration(160)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)

    def sizeHint(self):
        fm = self.fontMetrics()
        w = fm.horizontalAdvance(self.text()) + (60 if self.icon_name else 36)
        return QSize(max(120, w), 48 if self.big else 38)

    def _get_hover(self):
        return self._hover

    def _set_hover(self, v):
        self._hover = v
        self.update()

    hover = Property(float, _get_hover, _set_hover)

    def enterEvent(self, e):
        self._anim.stop()
        self._anim.setEndValue(1.0)
        self._anim.start()

    def leaveEvent(self, e):
        self._anim.stop()
        self._anim.setEndValue(0.0)
        self._anim.start()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        rad = r.height() / 2 if not self.big else 14
        if self.isEnabled():
            g = theme.accent_gradient(0, 0, r.width(), r.height())
            p.setBrush(g)
        else:
            p.setBrush(QColor(theme.CARD3))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawRoundedRect(r, rad, rad)
        if self._hover > 0 or self.isDown():
            p.setBrush(QColor(255, 255, 255, int(28 * self._hover + (25 if self.isDown() else 0))))
            p.drawRoundedRect(r, rad, rad)
        # nội dung
        fm = self.fontMetrics()
        tw = fm.horizontalAdvance(self.text())
        isz = 20 if self.big else 17
        total = tw + (isz + 8 if self.icon_name else 0)
        x = (r.width() - total) / 2 + r.x()
        col = "#FFFFFF" if self.isEnabled() else theme.MUTED
        if self.icon_name:
            icons.paint_icon(p, self.icon_name, QRectF(x, (self.height() - isz) / 2, isz, isz), col, 2.1)
            x += isz + 8
        p.setPen(QColor(col))
        p.setFont(self.font())
        p.drawText(QRectF(x, 0, tw + 4, self.height()), Qt.AlignmentFlag.AlignVCenter, self.text())


def button(text: str, icon_name: str = "", tip: str = "", danger: bool = False, flat: bool = False,
           on_click: Callable | None = None) -> QPushButton:
    b = QPushButton(text)
    if icon_name:
        b.setIcon(icons.icon(icon_name, theme.ERR if danger else theme.TEXT, 18))
        b.setIconSize(QSize(17, 17))
    if tip:
        b.setToolTip(tip)
    if danger:
        b.setProperty("danger", True)
    if flat:
        b.setProperty("flat", True)
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    if on_click:
        b.clicked.connect(on_click)
    return b


class IconButton(QAbstractButton):
    """Nút tròn chỉ có icon (dùng cho trình phát video, hàng công cụ)."""

    def __init__(self, icon_name: str, tip: str = "", size: int = 34, accent: bool = False, parent=None):
        super().__init__(parent)
        self.icon_name = icon_name
        self.accent = accent
        self.setToolTip(tip)
        self.setFixedSize(size, size)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._hov = False

    def set_icon(self, name: str):
        self.icon_name = name
        self.update()

    def enterEvent(self, e):
        self._hov = True
        self.update()

    def leaveEvent(self, e):
        self._hov = False
        self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        if self.accent:
            p.setBrush(theme.accent_gradient(0, 0, r.width(), r.height()))
            p.setPen(Qt.PenStyle.NoPen)
        else:
            p.setBrush(QColor(theme.CARD3 if self._hov else theme.CARD2))
            p.setPen(QPen(QColor(theme.BORDER), 1))
        p.drawEllipse(r)
        m = r.width() * 0.27
        icons.paint_icon(p, self.icon_name, r.adjusted(m, m, -m, -m),
                         "#FFFFFF" if self.accent else (theme.TEXT if self.isEnabled() else theme.MUTED), 2.0)


class NavButton(QAbstractButton):
    """Mục điều hướng bên trái: icon + chữ, nền viên thuốc khi được chọn, thanh nhấn bên trái."""

    def __init__(self, text: str, icon_name: str, parent=None):
        super().__init__(parent)
        self.setText(text)
        self.icon_name = icon_name
        self.setCheckable(True)
        self.setAutoExclusive(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(42)
        self._hov = False
        self.compact = False
        self.badge = 0          # số nhỏ góc phải (vd. số việc đang chạy)
        self.setFont(theme.font(10.2, QFont.Weight.DemiBold))

    def set_badge(self, n: int):
        if n != self.badge:
            self.badge = n
            self.update()

    def _paint_badge(self, p: QPainter, r: QRectF):
        if not self.badge:
            return
        t = str(self.badge) if self.badge < 100 else "99+"
        f = theme.font(7.6, QFont.Weight.Bold)
        w = max(17.0, 8 + len(t) * 7.0)
        if self.compact:
            br = QRectF(r.center().x() + 3, r.top() + 1, w, 16)
        else:
            br = QRectF(r.right() - w - 8, r.center().y() - 8, w, 16)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(theme.accent_gradient(br.left(), 0, br.right(), 0))
        p.drawRoundedRect(br, 8, 8)
        p.setPen(QColor("#FFFFFF"))
        p.setFont(f)
        p.drawText(br, Qt.AlignmentFlag.AlignCenter, t)

    def enterEvent(self, e):
        self._hov = True
        self.update()

    def leaveEvent(self, e):
        self._hov = False
        self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(2, 2, -2, -2)
        on = self.isChecked()
        if on or self._hov:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(theme.CARD3 if on else theme.CARD2))
            p.drawRoundedRect(r, 10, 10)
        if on:
            bar = QRectF(r.left() + 1, r.top() + 10, 3.5, r.height() - 20)
            p.setBrush(theme.accent_gradient(0, bar.top(), 0, bar.bottom()))
            p.drawRoundedRect(bar, 2, 2)
        col = theme.TEXT if on else ("#C9D0DC" if self._hov else theme.MUTED)
        if self.compact:   # thanh bên thu gọn: chỉ icon ở giữa
            icons.paint_icon(p, self.icon_name, QRectF(r.center().x() - 10, r.center().y() - 10, 20, 20),
                             theme.ACCENT2 if on else col, 1.9)
            self._paint_badge(p, r)
            return
        icons.paint_icon(p, self.icon_name, QRectF(r.left() + 14, r.center().y() - 10, 20, 20),
                         theme.ACCENT2 if on else col, 1.9)
        p.setPen(QColor(col))
        p.setFont(self.font())
        p.drawText(r.adjusted(46, 0, -6, 0), Qt.AlignmentFlag.AlignVCenter, self.text())
        self._paint_badge(p, r)


# ====================================================================== Công tắc

class Switch(QAbstractButton):
    """Công tắc bật/tắt có hiệu ứng trượt."""
    toggledOn = Signal(bool)

    def __init__(self, checked: bool = False, parent=None):
        super().__init__(parent)
        self.setCheckable(True)
        self.setChecked(checked)
        self.setFixedSize(44, 24)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._pos = 1.0 if checked else 0.0
        self._anim = QPropertyAnimation(self, b"knob", self)  # KHÔNG đặt tên "pos" (trùng vị trí widget)
        self._anim.setDuration(150)
        self.toggled.connect(self._on_toggle)

    def _get_pos(self):
        return self._pos

    def _set_pos(self, v):
        self._pos = v
        self.update()

    knob = Property(float, _get_pos, _set_pos)

    def _on_toggle(self, on: bool):
        self._anim.stop()
        self._anim.setEndValue(1.0 if on else 0.0)
        self._anim.start()
        self.toggledOn.emit(on)

    def setChecked(self, on: bool):  # type: ignore[override]
        super().setChecked(on)
        self._pos = 1.0 if on else 0.0
        self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        off_c, on_c = QColor(theme.CARD3), QColor(theme.ACCENT)
        t = self._pos
        col = QColor(int(off_c.red() + (on_c.red() - off_c.red()) * t),
                     int(off_c.green() + (on_c.green() - off_c.green()) * t),
                     int(off_c.blue() + (on_c.blue() - off_c.blue()) * t))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(col)
        p.drawRoundedRect(r, r.height() / 2, r.height() / 2)
        d = r.height() - 6
        x = r.left() + 3 + (r.width() - d - 6) * t
        p.setBrush(QColor("#FFFFFF"))
        p.drawEllipse(QRectF(x, r.top() + 3, d, d))


# ====================================================================== Thanh tab dạng viên thuốc

class PillTabs(QWidget):
    changed = Signal(int)

    def __init__(self, items: list[tuple[str, str]], parent=None):
        super().__init__(parent)
        self.items = items
        self.cur = 0
        self._hov = -1
        self.setMouseTracking(True)
        self.setFixedHeight(46)
        self.setFont(theme.font(9.8, QFont.Weight.DemiBold))
        self._rects: list[QRectF] = []

    def set_current(self, i: int, emit: bool = True):
        if 0 <= i < len(self.items):
            self.cur = i
            self.update()
            if emit:
                self.changed.emit(i)

    def _layout(self):
        fm = self.fontMetrics()
        x = 4.0
        self._rects = []
        for text, _ in self.items:
            w = fm.horizontalAdvance(text) + 46
            self._rects.append(QRectF(x, 5, w, self.height() - 10))
            x += w + 4
        return x

    def sizeHint(self):
        return QSize(int(self._layout()) + 4, 46)

    def minimumSizeHint(self):
        return QSize(200, 46)

    def mouseMoveEvent(self, e):
        self._layout()
        h = next((i for i, r in enumerate(self._rects) if r.contains(e.position())), -1)
        if h != self._hov:
            self._hov = h
            self.update()

    def leaveEvent(self, e):
        self._hov = -1
        self.update()

    def mousePressEvent(self, e):
        self._layout()
        for i, r in enumerate(self._rects):
            if r.contains(e.position()):
                self.set_current(i)

    def paintEvent(self, e):
        self._layout()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        bg = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        p.setPen(QPen(QColor(theme.BORDER), 1))
        p.setBrush(QColor(theme.CARD))
        p.drawRoundedRect(bg, 14, 14)
        p.setFont(self.font())
        # thu nhỏ nếu không đủ chỗ
        total = self._rects[-1].right() + 4 if self._rects else 0
        sc = min(1.0, (self.width() - 4) / total) if total else 1.0
        p.scale(sc, 1.0)
        for i, ((text, ic), r) in enumerate(zip(self.items, self._rects)):
            if i == self.cur:
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(theme.accent_gradient(r.left(), 0, r.right(), 0))
                p.drawRoundedRect(r, 11, 11)
            elif i == self._hov:
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QColor(theme.CARD2))
                p.drawRoundedRect(r, 11, 11)
            col = "#FFFFFF" if i == self.cur else (theme.TEXT if i == self._hov else theme.MUTED)
            icons.paint_icon(p, ic, QRectF(r.left() + 12, r.center().y() - 8.5, 17, 17), col, 2.0)
            p.setPen(QColor(col))
            p.drawText(r.adjusted(34, 0, -8, 0), Qt.AlignmentFlag.AlignVCenter, text)


class IconTabs(QWidget):
    """Thanh tab kiểu trình dựng phim (Concat/CapCut): icon trên, chữ dưới, chia đều chiều ngang;
    hẹp quá thì tự xuống 2 hàng. Cùng API với PillTabs (changed, set_current, cur)."""
    changed = Signal(int)
    MIN_CELL = 74

    def __init__(self, items: list[tuple[str, str]], parent=None):
        super().__init__(parent)
        self.items = items
        self.cur = 0
        self._hov = -1
        self.setMouseTracking(True)
        self.setFont(theme.font(8.6, QFont.Weight.DemiBold))
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._rows = 1

    def set_current(self, i: int, emit: bool = True):
        if 0 <= i < len(self.items):
            self.cur = i
            self.update()
            if emit:
                self.changed.emit(i)

    def _per_row(self, w: int) -> int:
        n = len(self.items)
        return n if w - 8 >= n * self.MIN_CELL else (n + 1) // 2

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, w: int) -> int:
        rows = -(-len(self.items) // max(1, self._per_row(w)))
        return 8 + rows * 54

    def sizeHint(self):
        return QSize(len(self.items) * self.MIN_CELL + 8, self.heightForWidth(len(self.items) * self.MIN_CELL + 8))

    def minimumSizeHint(self):
        return QSize(4 * self.MIN_CELL, 62)

    def resizeEvent(self, e):
        h = self.heightForWidth(self.width())
        if self.height() != h:
            self.setFixedHeight(h)
        super().resizeEvent(e)

    def _rects(self) -> list[QRectF]:
        per = max(1, self._per_row(self.width()))
        cw = (self.width() - 8) / per
        return [QRectF(4 + (i % per) * cw + 2, 4 + (i // per) * 54 + 2, cw - 4, 50) for i in range(len(self.items))]

    def mouseMoveEvent(self, e):
        h = next((i for i, r in enumerate(self._rects()) if r.contains(e.position())), -1)
        if h != self._hov:
            self._hov = h
            self.update()

    def leaveEvent(self, e):
        self._hov = -1
        self.update()

    def mousePressEvent(self, e):
        for i, r in enumerate(self._rects()):
            if r.contains(e.position()):
                self.set_current(i)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setFont(self.font())
        fm = p.fontMetrics()
        for i, ((text, ic), r) in enumerate(zip(self.items, self._rects())):
            on, hov = i == self.cur, i == self._hov
            if on or hov:
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QColor(theme.CARD3 if on else theme.CARD2))
                p.drawRoundedRect(r, 9, 9)
            if on:   # vạch nhấn dưới tab đang chọn
                p.setBrush(theme.accent_gradient(r.left(), 0, r.right(), 0))
                p.drawRoundedRect(QRectF(r.center().x() - 12, r.bottom() - 3, 24, 3), 1.5, 1.5)
            col = theme.ACCENT2 if on else (theme.TEXT if hov else theme.MUTED)
            icons.paint_icon(p, ic, QRectF(r.center().x() - 9, r.top() + 7, 18, 18), col, 1.9)
            p.setPen(QColor(theme.TEXT if on else (theme.TEXT if hov else theme.MUTED)))
            p.drawText(QRectF(r.left() + 2, r.top() + 28, r.width() - 4, 18), Qt.AlignmentFlag.AlignCenter,
                       fm.elidedText(text, Qt.TextElideMode.ElideRight, int(r.width() - 4)))


# ====================================================================== Thanh tiến độ

class ProgressBar(QWidget):
    def __init__(self, parent=None, height: int = 8):
        super().__init__(parent)
        self.value = 0.0
        self.busy = False
        self._phase = 0.0
        self.setFixedHeight(height)
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)

    def set(self, v: float, busy: bool = False):
        self.value = max(0.0, min(1.0, v))
        self.busy = busy
        if busy and not self._timer.isActive():
            self._timer.start(40)
        elif not busy:
            self._timer.stop()
        self.update()

    def _tick(self):
        self._phase = (self._phase + 0.02) % 1.0
        self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect())
        rad = r.height() / 2
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(theme.CARD3))
        p.drawRoundedRect(r, rad, rad)
        w = r.width() * self.value
        if w > 1:
            p.setBrush(theme.accent_gradient(0, 0, r.width(), 0))
            p.drawRoundedRect(QRectF(0, 0, max(w, r.height()), r.height()), rad, rad)
            if self.busy:  # vệt sáng chạy
                x = (w + 80) * self._phase - 80
                g = QLinearGradient(x, 0, x + 80, 0)
                g.setColorAt(0, QColor(255, 255, 255, 0))
                g.setColorAt(0.5, QColor(255, 255, 255, 70))
                g.setColorAt(1, QColor(255, 255, 255, 0))
                p.setBrush(g)
                path = QPainterPath()
                path.addRoundedRect(QRectF(0, 0, w, r.height()), rad, rad)
                p.setClipPath(path)
                p.drawRect(QRectF(x, 0, 80, r.height()))


# ====================================================================== Vùng kéo thả

class DropZone(QWidget):
    dropped = Signal(list)
    clicked = Signal()

    def __init__(self, text: str = "Kéo thả video vào đây", sub: str = "hoặc bấm để chọn file", parent=None):
        super().__init__(parent)
        self.text, self.sub = text, sub
        self.setAcceptDrops(True)
        self._over = False
        self.setMinimumHeight(150)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()
            self._over = True
            self.update()

    def dragLeaveEvent(self, e):
        self._over = False
        self.update()

    def dropEvent(self, e):
        self._over = False
        self.update()
        paths = [u.toLocalFile() for u in e.mimeData().urls() if u.isLocalFile()]
        if paths:
            self.dropped.emit(paths)

    def mouseReleaseEvent(self, e):
        self.clicked.emit()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(2, 2, -2, -2)
        p.setBrush(theme.c(theme.ACCENT, 26 if self._over else 10))
        pen = QPen(QColor(theme.ACCENT2 if self._over else "#3A4458"), 1.6, Qt.PenStyle.DashLine)
        pen.setDashPattern([5, 4])
        p.setPen(pen)
        p.drawRoundedRect(r, 14, 14)
        icons.paint_icon(p, "upload", QRectF(r.center().x() - 18, r.center().y() - 42, 36, 36),
                         theme.ACCENT2, 1.8)
        p.setPen(QColor(theme.TEXT))
        p.setFont(theme.font(11, QFont.Weight.Bold))
        p.drawText(QRectF(r.left(), r.center().y() + 2, r.width(), 24), Qt.AlignmentFlag.AlignCenter, self.text)
        p.setPen(QColor(theme.MUTED))
        p.setFont(theme.font(9))
        p.drawText(QRectF(r.left(), r.center().y() + 26, r.width(), 20), Qt.AlignmentFlag.AlignCenter, self.sub)


# ====================================================================== Form tiện dụng

class Form(QFormLayout):
    def __init__(self):
        super().__init__()
        self.setLabelAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.setFormAlignment(Qt.AlignmentFlag.AlignTop)
        self.setHorizontalSpacing(14)
        self.setVerticalSpacing(10)
        self.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)

    def row(self, text: str, w, tip: str = ""):
        lb = label(text, "muted")
        if tip:
            lb.setToolTip(tip)
            if isinstance(w, QWidget):
                w.setToolTip(tip)
        self.addRow(lb, w)
        return w


def combo(items: list[tuple[str, str]], current: str | None = None) -> QComboBox:
    cb = QComboBox()
    for val, text in items:
        cb.addItem(text, val)
    if current is not None:
        set_combo(cb, current)
    cb.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
    cb.setMinimumContentsLength(8)
    return cb


def set_combo(cb: QComboBox, value):
    i = cb.findData(value)
    if i < 0 and value is not None:
        i = cb.findData(str(value))
    if i >= 0:
        cb.setCurrentIndex(i)


class SliderRow(QWidget):
    """Thanh trượt số thực + ô hiển thị giá trị."""
    changed = Signal(float)

    def __init__(self, lo: float, hi: float, step: float, value: float, decimals: int = 2, suffix: str = ""):
        super().__init__()
        self.lo, self.step = lo, step
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.sl = QSlider(Qt.Orientation.Horizontal)
        self.sl.setRange(0, int(round((hi - lo) / step)))
        self.sp = QDoubleSpinBox()
        self.sp.setRange(lo, hi)
        self.sp.setSingleStep(step)
        self.sp.setDecimals(decimals)
        self.sp.setSuffix(suffix)
        self.sp.setFixedWidth(84)
        lay.addWidget(self.sl, 1)
        lay.addWidget(self.sp)
        self.set(value)
        self.sl.valueChanged.connect(lambda v: self._from_slider(v))
        self.sp.valueChanged.connect(lambda v: self._from_spin(v))

    def _from_slider(self, v):
        val = self.lo + v * self.step
        self.sp.blockSignals(True)
        self.sp.setValue(val)
        self.sp.blockSignals(False)
        self.changed.emit(val)

    def _from_spin(self, v):
        self.sl.blockSignals(True)
        self.sl.setValue(int(round((v - self.lo) / self.step)))
        self.sl.blockSignals(False)
        self.changed.emit(v)

    def set(self, v):
        self.sp.blockSignals(True)
        self.sl.blockSignals(True)
        self.sp.setValue(float(v))
        self.sl.setValue(int(round((float(v) - self.lo) / self.step)))
        self.sp.blockSignals(False)
        self.sl.blockSignals(False)

    def value(self) -> float:
        return float(self.sp.value())


class ColorButton(QPushButton):
    changed = Signal(str)

    def __init__(self, color: str = "#FFFFFF"):
        super().__init__()
        self.color = color
        self.setFixedSize(56, 32)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.clicked.connect(self._pick)

    def set(self, color: str):
        self.color = color or "#FFFFFF"
        self.update()

    def _pick(self):
        from PySide6.QtWidgets import QColorDialog

        col = QColorDialog.getColor(QColor(self.color), self, "Chọn màu")
        if col.isValid():
            self.set(col.name().upper())
            self.changed.emit(self.color)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        p.setPen(QPen(QColor(theme.BORDER), 1))
        p.setBrush(QColor(theme.CARD2))
        p.drawRoundedRect(r, 9, 9)
        p.setPen(QPen(QColor(255, 255, 255, 60), 1))
        p.setBrush(QColor(self.color))
        p.drawRoundedRect(r.adjusted(6, 6, -6, -6), 5, 5)


def hbox(*ws, spacing: int = 8, stretch_last: bool = False) -> QHBoxLayout:
    lay = QHBoxLayout()
    lay.setSpacing(spacing)
    lay.setContentsMargins(0, 0, 0, 0)
    for w in ws:
        if w is None:
            lay.addStretch(1)
        elif isinstance(w, QWidget):
            lay.addWidget(w)
        else:
            lay.addLayout(w)
    if stretch_last:
        lay.addStretch(1)
    return lay


class FlowLayout(QLayout):
    """Hàng nút tự xuống dòng khi khung hẹp – không đẩy khung rộng ra rồi bị cắt mất viền / cắt chữ."""

    def __init__(self, parent=None, spacing: int = 8):
        super().__init__(parent)
        self._items: list = []
        self.setContentsMargins(0, 0, 0, 0)
        self.setSpacing(spacing)

    def addItem(self, item):
        self._items.append(item)

    def count(self) -> int:
        return len(self._items)

    def itemAt(self, i: int):
        return self._items[i] if 0 <= i < len(self._items) else None

    def takeAt(self, i: int):
        return self._items.pop(i) if 0 <= i < len(self._items) else None

    def hasHeightForWidth(self) -> bool:
        return True

    def heightForWidth(self, w: int) -> int:
        return self._place(QRect(0, 0, w, 0), dry=True)

    def setGeometry(self, rect: QRect):
        super().setGeometry(rect)
        self._place(rect, dry=False)

    def sizeHint(self) -> QSize:
        vis = [it for it in self._items if not it.isEmpty()]
        if not vis:
            return QSize(0, 0)
        return QSize(sum(it.sizeHint().width() for it in vis) + self.spacing() * (len(vis) - 1),
                     max(it.sizeHint().height() for it in vis))

    def minimumSize(self) -> QSize:
        s = QSize(0, 0)
        for it in self._items:
            if not it.isEmpty():
                s = s.expandedTo(it.minimumSize())
        return s

    def _place(self, rect: QRect, dry: bool) -> int:
        x, y, line, sp = rect.x(), rect.y(), 0, self.spacing()
        row: list = []

        def flush():                    # các ô trong 1 hàng căn giữa theo chiều dọc
            if not dry:
                for item, px, h in row:
                    item.setGeometry(QRect(QPoint(px, y + (line - h.height()) // 2), h))
            row.clear()
        for it in self._items:
            if it.isEmpty():
                continue
            hint = it.sizeHint()
            if x > rect.x() and x + hint.width() > rect.right() + 1:
                flush()
                x, y, line = rect.x(), y + line + sp, 0
            row.append((it, x, hint))
            x += hint.width() + sp
            line = max(line, hint.height())
        flush()
        return y + line - rect.y()


def flow(*ws, spacing: int = 8) -> QWidget:
    """Như hbox nhưng tự xuống dòng khi hẹp (None bị bỏ qua; layout con được gói thành 1 khối không tách)."""
    box = QWidget()
    lay = FlowLayout(box, spacing)
    for w in ws:
        if w is not None:
            lay.addWidget(w if isinstance(w, QWidget) else wrap(w))
    return box


def wrap(layout) -> QWidget:
    w = QWidget()
    w.setLayout(layout)
    return w


class Toast(QWidget):
    """Thông báo nổi góc trên, tự ẩn."""

    def __init__(self, parent: QWidget, text: str, kind: str = "info", ms: int = 3500):
        super().__init__(parent)
        self.text, self.kind = text, kind
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setFont(theme.font(10, QFont.Weight.DemiBold))
        fm = self.fontMetrics()
        maxw = min(560, parent.width() - 80)
        br = fm.boundingRect(0, 0, maxw - 64, 400, Qt.TextFlag.TextWordWrap, text)
        self.resize(br.width() + 66, max(46, br.height() + 24))
        self.move((parent.width() - self.width()) // 2, 18)
        shadow(self, 30, 140, 8)
        self.show()
        self.raise_()
        QTimer.singleShot(ms, self.deleteLater)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        col = {"ok": theme.OK, "err": theme.ERR, "warn": theme.WARN}.get(self.kind, theme.INFO)
        p.setBrush(QColor(theme.CARD2))
        p.setPen(QPen(QColor(col), 1.2))
        p.drawRoundedRect(r, 12, 12)
        name = {"ok": "check", "err": "x", "warn": "info"}.get(self.kind, "info")
        icons.paint_icon(p, name, QRectF(16, r.center().y() - 10, 20, 20), col, 2.2)
        p.setPen(QColor(theme.TEXT))
        p.setFont(self.font())
        p.drawText(r.adjusted(46, 8, -14, -8), Qt.AlignmentFlag.AlignVCenter | Qt.TextFlag.TextWordWrap, self.text)


def expanding(w: QWidget) -> QWidget:
    w.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
    return w
