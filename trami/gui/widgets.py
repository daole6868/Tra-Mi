"""Các widget tự vẽ bằng QPainter: thẻ bo góc, nút gradient, công tắc, thanh tab, thanh tiến độ, vùng kéo thả…"""
from __future__ import annotations

from typing import Callable

from PySide6.QtCore import (QEasingCurve, QEvent, QPoint, QPropertyAnimation, QRect, QRectF, QSize, Qt, QTimer, Signal,
                            Property)
from PySide6.QtGui import QColor, QFont, QLinearGradient, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (QAbstractButton, QAbstractSpinBox, QComboBox, QDoubleSpinBox, QFormLayout, QFrame,
                               QGraphicsDropShadowEffect, QHBoxLayout, QLabel, QLayout, QPushButton, QSizePolicy, QSlider,
                               QVBoxLayout, QWidget)

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
        g.setColorAt(1, QColor("#121722"))
        if self.accent:                  # thẻ nổi bật (vd "Chạy trọn gói"): nền ấm nhẹ, viền màu nhấn mờ
            g.setColorAt(0, QColor("#21181C"))
            g.setColorAt(1, QColor("#171419"))
        p.setBrush(g)
        p.setPen(QPen(theme.c(theme.ACCENT, 95) if self.accent else QColor(theme.BORDER), 1))
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

    def __init__(self, text: str, icon_name: str = "", big: bool = False, parent=None, num: int | None = None):
        super().__init__(text, parent)
        self.icon_name = icon_name
        self.big = big
        self.num = None         # 2.3.7: nút không còn số thứ tự (chỉ thẻ bước / thanh tab có số) – bỏ qua num
        self._hover = 0.0
        self._glow = QGraphicsDropShadowEffect(self)   # bóng sáng màu nhấn, rê chuột thì sáng lên
        self._glow.setOffset(0, 5)
        self._glow.setColor(theme.c(theme.ACCENT, 110))
        self._glow.setBlurRadius(16)
        self.setGraphicsEffect(self._glow)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMinimumHeight(48 if big else 38)
        f = theme.font(11.5 if big else 10, QFont.Weight.Bold)
        self.setFont(f)
        self._anim = QPropertyAnimation(self, b"hover", self)
        self._anim.setDuration(160)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)

    def sizeHint(self):
        fm = self.fontMetrics()
        w = fm.horizontalAdvance(self.text().replace("&&", "&")) + (60 if self.icon_name else 36) + (26 if self.num else 0)
        return QSize(max(120, w), 48 if self.big else 38)

    def _get_hover(self):
        return self._hover

    def _set_hover(self, v):
        self._hover = v
        if self.isEnabled():
            self._glow.setBlurRadius(16 + 12 * v)
            self._glow.setOffset(0, 5 + 3 * v)
            self._glow.setColor(theme.c(theme.ACCENT, int(110 + 80 * v)))
        self.update()

    hover = Property(float, _get_hover, _set_hover)

    def changeEvent(self, e):
        if e.type() == QEvent.Type.EnabledChange:
            self._glow.setEnabled(self.isEnabled())
        super().changeEvent(e)

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
        text = self.text().replace("&&", "&")
        tw = fm.horizontalAdvance(text)
        isz = 20 if self.big else 17
        nw = 20 if self.num else 0
        total = tw + (isz + 8 if self.icon_name else 0) + (nw + 8 if nw else 0)
        x = (r.width() - total) / 2 + r.x()
        col = theme.ON_ACCENT if self.isEnabled() else theme.MUTED
        if nw:
            paint_num(p, QRectF(x, (self.height() - 20) / 2, 20, 20), self.num, primary=self.isEnabled())
            x += nw + 8
        if self.icon_name:
            icons.paint_icon(p, self.icon_name, QRectF(x, (self.height() - isz) / 2, isz, isz), col, 2.1)
            x += isz + 8
        p.setPen(QColor(col))
        p.setFont(self.font())
        p.drawText(QRectF(x, 0, tw + 4, self.height()), Qt.AlignmentFlag.AlignVCenter, text)


def button(text: str, icon_name: str = "", tip: str = "", danger: bool = False, flat: bool = False,
           on_click: Callable | None = None) -> QPushButton:
    b = QPushButton(text)
    b._icon_name = icon_name            # numbered() ghép số thứ tự + icon này
    b._danger = danger
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
        # 2.3.7: gọi KHÔNG kèm tham số – clicked(bool) truyền False vào tham số đầu của hàm (vd "Thêm kỹ năng mới" lưu tên
        # kỹ năng = False, "Nghe câu" phát câu số 0 thay vì câu đang chọn)
        b.clicked.connect(lambda *_a, _f=on_click: _f())
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
                         theme.ON_ACCENT if self.accent else (theme.TEXT if self.isEnabled() else theme.MUTED), 2.0)


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
        p.setPen(QColor(theme.ON_ACCENT))
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
        if not self.isEnabled():
            p.setOpacity(0.35)                                   # công tắc bị khoá: mờ đi cho dễ thấy
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
    """Hàng chọn trang dạng viên thuốc. 2.2.0: hẹp thì tự xuống 2–3 hàng (trước co chữ theo chiều ngang)."""
    changed = Signal(int)
    ROW = 42

    def __init__(self, items: list[tuple[str, str]], parent=None):
        super().__init__(parent)
        self.items = items
        self.cur = 0
        self._hov = -1
        self.setMouseTracking(True)
        self.setFont(theme.font(9.4, QFont.Weight.DemiBold))
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._rects: list[QRectF] = []
        self._rows = 1
        self.setFixedHeight(self.ROW + 4)

    def set_current(self, i: int, emit: bool = True):
        if 0 <= i < len(self.items):
            self.cur = i
            self.update()
            if emit:
                self.changed.emit(i)

    def _widths(self) -> list[float]:
        fm = self.fontMetrics()
        return [fm.horizontalAdvance(text) + 44 for text, _ in self.items]

    def _layout(self, width: int | None = None):
        width = self.width() if width is None else width
        x, y, rows = 4.0, 4.0, 1
        self._rects = []
        for w in self._widths():
            if x > 4 and x + w > width - 4:
                x, y, rows = 4.0, y + self.ROW - 4, rows + 1
            self._rects.append(QRectF(x, y, w, self.ROW - 8))
            x += w + 4
        self._rows = rows
        return rows

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, w: int) -> int:
        n = self._layout(w)
        self._layout()
        return 8 + n * (self.ROW - 4)

    def sizeHint(self):
        return QSize(int(sum(self._widths()) + 4 * len(self.items) + 8), self.ROW + 4)

    def minimumSizeHint(self):
        return QSize(int(max(self._widths()) + 12), self.ROW + 4)

    def resizeEvent(self, e):
        h = self.heightForWidth(self.width())
        if self.height() != h:
            self.setFixedHeight(h)
        super().resizeEvent(e)

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
        p.setBrush(QColor(theme.SIDEBAR))
        p.drawRoundedRect(bg, 12, 12)
        p.setFont(self.font())
        for i, ((text, ic), r) in enumerate(zip(self.items, self._rects)):
            if i == self.cur:
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(theme.accent_gradient(r.left(), 0, r.right(), 0))
                p.drawRoundedRect(r, 9, 9)
            elif i == self._hov:
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QColor(theme.CARD2))
                p.drawRoundedRect(r, 9, 9)
            col = theme.ON_ACCENT if i == self.cur else (theme.TEXT if i == self._hov else theme.MUTED)
            icons.paint_icon(p, ic, QRectF(r.left() + 11, r.center().y() - 8, 16, 16), col, 2.0)
            p.setPen(QColor(col))
            p.drawText(r.adjusted(32, 0, -8, 0), Qt.AlignmentFlag.AlignVCenter, text)


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
        # 2.2.0: cột trái hẹp -> ô nào không đủ chỗ cạnh nhãn thì xuống dưới nhãn (không bị cắt mép phải)
        self.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)

    def row(self, text: str, w, tip: str = ""):
        lb = label(text, "muted")
        if tip:
            lb.setToolTip(tip)
            if isinstance(w, QWidget):
                w.setToolTip(tip)
        if isinstance(w, QAbstractSpinBox) and w.sizePolicy().horizontalPolicy() != QSizePolicy.Policy.Fixed:
            # 2.3.7: ô số giữ cỡ vừa chữ (không bị kéo dài hết hàng như ô chọn)
            w.setSizePolicy(QSizePolicy.Policy.Fixed, w.sizePolicy().verticalPolicy())
            w.setMinimumWidth(max(w.minimumWidth(), 110))
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
            hint = it.sizeHint().expandedTo(it.minimumSize())   # ô không bao giờ thấp hơn mức tối thiểu của nó
            if x > rect.x() and x + hint.width() > rect.right() + 1:
                flush()
                x, y, line = rect.x(), y + line + sp, 0
            row.append((it, x, hint))
            x += hint.width() + sp
            line = max(line, hint.height())
        flush()
        return y + line - rect.y()


class HfwBox(QWidget):
    """Khung có hàng tự xuống dòng / chữ tự xuống dòng: chiều cao tối thiểu = chiều cao cần ở bề rộng HIỆN TẠI (2.1.35 /
    2.1.39). Khung cha (QFormLayout trong ô cuộn, vách kéo QSplitter) chỉ tính theo chiều cao tối thiểu 1 hàng nên có lúc
    cấp thiếu -> hàng dưới bị cắt nửa / chữ hướng dẫn đè lên ô. Tính lại khi đổi bề rộng hoặc nội dung đổi."""

    def refit(self):
        lay = self.layout()
        if lay is None or self.width() <= 0:
            return
        h = lay.heightForWidth(max(120, self.width())) if lay.hasHeightForWidth() else -1
        h = max(h, lay.minimumSize().height())
        if h > 0 and h != self.minimumHeight():
            self.setMinimumHeight(h)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if e.size().width() != e.oldSize().width():
            self.refit()

    def event(self, e):
        r = super().event(e)
        if e.type() == QEvent.Type.LayoutRequest:   # ô con hiện / ẩn / đổi chữ
            self.refit()
        return r


def flow(*ws, spacing: int = 8) -> QWidget:
    """Như hbox nhưng tự xuống dòng khi hẹp (None bị bỏ qua; layout con được gói thành 1 khối không tách)."""
    box = HfwBox()
    lay = FlowLayout(box, spacing)
    for w in ws:
        if w is not None:
            lay.addWidget(w if isinstance(w, QWidget) else wrap(w))
    return box


def field(*ws, spacing: int = 8, grow: bool = True) -> QWidget:
    """1 ô của Form gồm ô chính + nút / ô phụ bám ngay bên phải (vd [ô chọn giọng][Đổi giọng]) – không bị tách xuống
    dòng riêng như khi để trong flow(). grow=True: ô đầu giãn hết chỗ; False: mọi ô giữ cỡ vừa chữ, phần thừa để trống
    (ô số không bị kéo dài cả hàng)."""
    box = QWidget()
    lay = QHBoxLayout(box)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(spacing)
    for i, w in enumerate(x for x in ws if x is not None):
        st = 1 if (grow and i == 0) else 0
        if isinstance(w, QWidget):
            lay.addWidget(w, st)
        else:
            lay.addLayout(w, st)
    if not grow:
        lay.addStretch(1)
    return box


class Group(QFrame):
    """Khối nhóm trong thẻ bước (2.3.7): tiêu đề nhỏ (+ ô / nhãn bên phải), dòng gợi ý, các ô tuỳ chọn THẲNG HÀNG (nhãn
    trái – ô phải, `row`) và hàng nút thao tác (`actions`) – nền sáng hơn thẻ 1 bậc, bo góc: nhìn là biết ô / nút nào đi
    với nhau. `add` thêm widget / layout tuỳ ý (sau đó `row` mở 1 Form mới để giữ đúng thứ tự)."""

    def __init__(self, title: str = "", icon_name: str = "", hint: str = "", right=None, parent=None):
        super().__init__(parent)
        self.lay = QVBoxLayout(self)
        self.lay.setContentsMargins(12, 10, 12, 12)
        self.lay.setSpacing(8)
        self._form = None
        self._forms: list = []
        self.title_lb = None
        if title or right is not None:
            hl = QHBoxLayout()
            hl.setSpacing(8)
            if icon_name:
                ic = QLabel()
                ic.setFixedSize(18, 18)
                ic.setPixmap(icons.pixmap(icon_name, theme.ACCENT2, 16))
                hl.addWidget(ic)
            if title:
                self.title_lb = label(title, "h4")
                hl.addWidget(self.title_lb)
            hl.addStretch(1)
            if right is not None:
                if isinstance(right, QWidget):
                    hl.addWidget(right)
                else:
                    hl.addLayout(right)
            self.lay.addLayout(hl)
        self.hint_lb = label(hint, "muted", wrap=True) if hint else None
        if self.hint_lb is not None:
            self.lay.addWidget(self.hint_lb)

    def form(self) -> Form:
        if self._form is None:
            self._form = Form()
            self._form.setVerticalSpacing(8)
            self._forms.append(self._form)
            self.lay.addLayout(self._form)
        return self._form

    def _form_of(self, w):
        """Form của khối có chứa ô w (None nếu không có)."""
        for f in self._forms:
            if f.getWidgetPosition(w)[0] >= 0:
                return f
        return None

    def row(self, text: str, w, tip: str = ""):
        """1 dòng tuỳ chọn: nhãn trái, ô phải (layout -> gói thành 1 khối). Trả lại w."""
        ww = w if isinstance(w, QWidget) else wrap(w)
        self.form().row(text, ww, tip)
        return w

    def span(self, w):
        """Dòng chiếm cả bề rộng NGAY TRONG khối tuỳ chọn đang có (vd dòng giải thích dưới 1 ô) – các nhãn vẫn thẳng
        cột với dòng trước / sau."""
        self.form().addRow(w if isinstance(w, QWidget) else wrap(w))
        return w

    def add(self, w):
        self._form = None
        if isinstance(w, QWidget):
            self.lay.addWidget(w)
        else:
            self.lay.addLayout(w)
        return w

    def actions(self, *ws):
        """Hàng nút thao tác (tự xuống dòng khi hẹp)."""
        return self.add(flow(*ws))

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        p.setBrush(QColor(255, 255, 255, 7))
        p.setPen(QPen(QColor(theme.BORDER), 1))
        p.drawRoundedRect(r, 11, 11)


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
        x = (parent.width() - self.width()) // 2
        self.move(x, -self.height())
        shadow(self, 30, 140, 8)
        self.show()
        self.raise_()
        self._slide = QPropertyAnimation(self, b"pos", self)      # 2.2.0: trượt xuống từ mép trên
        self._slide.setDuration(320)
        self._slide.setEasingCurve(QEasingCurve.Type.OutBack)
        self._slide.setStartValue(QPoint(x, -self.height()))
        self._slide.setEndValue(QPoint(x, 18))
        self._slide.start()
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


# ====================================================================== 2.2.0: số thứ tự, thẻ bước, thanh tab dọc

def paint_num(p: QPainter, r: QRectF, n, primary: bool = False):
    """Vẽ ô số thứ tự bo tròn (nút bước 1, 2, 3…): trên nút màu nhấn nền tối mờ, nút thường nền sáng mờ."""
    p.save()
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(26, 11, 8, 52) if primary else QColor(255, 255, 255, 30))
    p.drawRoundedRect(r, r.height() / 2, r.height() / 2)
    p.setPen(QColor(theme.ON_ACCENT if primary else theme.TEXT))
    p.setFont(theme.font(max(6.5, r.height() * 0.4), QFont.Weight.Bold))
    p.drawText(r, Qt.AlignmentFlag.AlignCenter, str(n))
    p.restore()


def num_icon(n, icon_name: str = "", color: str = theme.TEXT):
    """Icon ghép [số thứ tự][icon] cho QPushButton (nút thường không tự vẽ được số)."""
    from PySide6.QtGui import QIcon, QPixmap

    dpr = 2.0
    w = 40 if icon_name else 18
    pm = QPixmap(int(w * dpr), int(18 * dpr))
    pm.setDevicePixelRatio(dpr)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    paint_num(p, QRectF(0, 0, 18, 18), n)
    if icon_name:
        icons.paint_icon(p, icon_name, QRectF(23, 1, 16, 16), color, 2.0)
    p.end()
    return QIcon(pm), QSize(w, 18)


def numbered(b, n):
    """2.3.7: nút KHÔNG còn số thứ tự (người dùng: chỉ đánh số ở thẻ bước / thanh tab bên ngoài) – chỉ bỏ "1. " ở đầu
    chữ nếu có. Giữ tên hàm cho chỗ gọi cũ. Trả lại chính nút đó."""
    import re

    b.setText(re.sub(r"^\s*\d+\s*[.·)]\s*", "", b.text()))
    return b


def number_buttons(root, start: int = 1) -> int:
    """2.3.7: không đánh số nút nữa (giữ hàm cho chỗ gọi cũ)."""
    return start


class ElideLabel(QLabel):
    """Nhãn 1 dòng tự cắt "…" khi hẹp (dòng tóm tắt dưới tiêu đề thẻ bước)."""

    def __init__(self, text: str = "", parent=None):
        super().__init__(text, parent)
        self.setProperty("muted", True)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)

    def minimumSizeHint(self):
        return QSize(20, super().minimumSizeHint().height())

    def paintEvent(self, e):
        p = QPainter(self)
        p.setFont(self.font())
        p.setPen(QColor(theme.MUTED))
        t = self.fontMetrics().elidedText(self.text(), Qt.TextElideMode.ElideRight, max(0, self.width()))
        p.drawText(self.rect(), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, t)


class StepBadge(QWidget):
    """Ô số thứ tự của thẻ bước: chưa làm (số) / đang chạy (vàng) / xong (✓ xanh) / lỗi."""

    def __init__(self, num=None, icon_name: str = "", parent=None):
        super().__init__(parent)
        self.num, self.icon_name, self.state = num, icon_name, ""
        self.setFixedSize(30, 30)

    def set_state(self, state: str):
        if state != self.state:
            self.state = state
            self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        col = {"done": theme.OK, "run": theme.WARN, "err": theme.ERR}.get(self.state)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(theme.c(col, 40) if col else QColor(theme.CARD3))
        p.drawRoundedRect(r, 10, 10)
        if self.state == "done":
            icons.paint_icon(p, "check", r.adjusted(7, 7, -7, -7), theme.OK, 2.4)
        elif self.num is not None:
            p.setPen(QColor(col or theme.TEXT))
            p.setFont(theme.font(10, QFont.Weight.Bold))
            p.drawText(r, Qt.AlignmentFlag.AlignCenter, str(self.num))
        elif self.icon_name:
            icons.paint_icon(p, self.icon_name, r.adjusted(7, 7, -7, -7), col or theme.ACCENT2, 2.0)


class Chip(QWidget):
    """Nhãn trạng thái bo tròn: ok (xanh), warn / run (vàng, chấm nhấp nháy), err (đỏ), info (xanh dương), "" (xám)."""
    COLORS = {"ok": "#63DD9C", "warn": "#F7C766", "run": "#F7C766", "err": "#FF8D93", "info": "#93BCFF"}

    def __init__(self, text: str = "", kind: str = "", parent=None):
        super().__init__(parent)
        self.text, self.kind = text, kind
        self.setFont(theme.font(8.4, QFont.Weight.DemiBold))
        self._phase = 0.0
        self._timer = QTimer(self, interval=60, timeout=self._tick)
        self.set(text, kind)

    def set(self, text: str, kind: str = ""):
        self.text, self.kind = text, kind
        self.setVisible(bool(text))
        (self._timer.start if kind == "run" and text else self._timer.stop)()
        self.updateGeometry()
        self.update()

    def _tick(self):
        self._phase = (self._phase + 0.035) % 1.0
        self.update()

    def sizeHint(self):
        return QSize(self.fontMetrics().horizontalAdvance(self.text) + (30 if self.kind == "run" else 18), 22)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        col = self.COLORS.get(self.kind, theme.TEXT2)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(theme.c(col, 34) if self.kind else QColor(255, 255, 255, 16))
        p.drawRoundedRect(r, r.height() / 2, r.height() / 2)
        x = 9.0
        if self.kind == "run":        # chấm nhấp nháy: việc đang chạy
            import math

            a = 0.5 + 0.5 * math.sin(self._phase * 2 * math.pi)
            p.setBrush(theme.c(col, int(70 * a)))
            p.drawEllipse(QRectF(x - 3, r.center().y() - 6.5, 13, 13))
            p.setBrush(QColor(col))
            p.drawEllipse(QRectF(x, r.center().y() - 3.5, 7, 7))
            x += 12
        p.setPen(QColor(col))
        p.setFont(self.font())
        p.drawText(r.adjusted(x, 0, -2, 0), Qt.AlignmentFlag.AlignVCenter, self.text)


class Chevron(QWidget):
    """Mũi tên mở / gập của thẻ bước – xoay mượt 180°."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(18, 18)
        self._a = 0.0
        self._anim = QPropertyAnimation(self, b"angle", self)
        self._anim.setDuration(260)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)

    def _get(self):
        return self._a

    def _set(self, v):
        self._a = v
        self.update()

    angle = Property(float, _get, _set)

    def set_open(self, on: bool, anim: bool = True):
        self._anim.stop()
        if anim:
            self._anim.setEndValue(180.0 if on else 0.0)
            self._anim.start()
        else:
            self._set(180.0 if on else 0.0)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.translate(9, 9)
        p.rotate(self._a)
        p.translate(-9, -9)
        icons.paint_icon(p, "chev_down", QRectF(1, 1, 16, 16), theme.MUTED, 2.0)


class _CardHead(QWidget):
    """Hàng tiêu đề thẻ bước: bấm chỗ trống / chữ = mở / gập (nút trong hàng vẫn bấm riêng)."""
    clicked = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._press = False
        self.hover = False
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def mousePressEvent(self, e):
        self._press = e.button() == Qt.MouseButton.LeftButton
        e.accept()

    def mouseReleaseEvent(self, e):
        if self._press and self.rect().contains(e.position().toPoint()):
            self.clicked.emit()
        self._press = False

    def enterEvent(self, e):
        self.hover = True
        self.parentWidget().update()

    def leaveEvent(self, e):
        self.hover = False
        self.parentWidget().update()


class StepCard(Card):
    """Thẻ bước có số thứ tự (2.2.0): tiêu đề + dòng tóm tắt + nhãn trạng thái, bấm tiêu đề để mở / gập (mở ra mượt,
    mũi tên xoay). Cùng cách dùng như SectionCard: .head (hàng tiêu đề – thêm nút), .add() / .lay (thân thẻ)."""
    toggled = Signal(bool)

    def __init__(self, title: str, icon_name: str = "", subtitle: str = "", parent=None, num=None,
                 opened: bool = True, collapsible: bool = True, key: str = ""):
        import re

        super().__init__(parent, padding=0)
        m = re.match(r"^\s*(\d+)\s*[.·)]\s*(.*)$", title)
        if m and num is None:
            num, title = int(m.group(1)), m.group(2)
        self.num, self.key, self.group = num, key or title, None
        self.collapsible = collapsible
        self._open = True
        self._sc_subtitle = subtitle
        self._sc_summary = ""
        outer = self.lay
        outer.setSpacing(0)
        self._headw = _CardHead(self)
        self._headw.clicked.connect(lambda: self.collapsible and self.set_open(not self._open, user=True))
        hl = QHBoxLayout(self._headw)
        hl.setContentsMargins(14, 11, 12, 11)
        hl.setSpacing(12)
        self.badge = StepBadge(num, icon_name)
        hl.addWidget(self.badge)
        tb = QVBoxLayout()
        tb.setSpacing(1)
        tb.setContentsMargins(0, 0, 0, 0)
        self.title_lb = QLabel(title)
        self.title_lb.setStyleSheet("font-weight: 700; font-size: 10.8pt;")
        self.sum_lb = ElideLabel("")
        tb.addWidget(self.title_lb)
        tb.addWidget(self.sum_lb)
        hl.addLayout(tb, 1)
        self.head = QHBoxLayout()           # như SectionCard.head: chỗ thêm nút vào hàng tiêu đề
        self.head.setSpacing(8)
        self.head.setContentsMargins(0, 0, 0, 0)
        hl.addLayout(self.head)
        self.chip = Chip()
        hl.addWidget(self.chip)
        self.chev = Chevron()
        hl.addWidget(self.chev)
        outer.addWidget(self._headw)
        self.body = QWidget()
        self.lay = QVBoxLayout(self.body)   # .add() / .lay = thân thẻ (như Card)
        self.lay.setContentsMargins(16, 0, 16, 16)
        self.lay.setSpacing(10)
        if subtitle:
            self._sc_desc = label(subtitle, "muted", wrap=True)
            self.lay.addWidget(self._sc_desc)
        outer.addWidget(self.body)
        self._anim = QPropertyAnimation(self.body, b"maximumHeight", self)
        self._anim.setDuration(240)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._anim.finished.connect(lambda: self.body.setMaximumHeight(16777215) if self._open else None)
        self.set_collapsible(collapsible)
        if not opened:
            self.set_open(False, anim=False)
        self._sync_head()

    # ---- tiêu đề / trạng thái
    def set_title(self, t: str):
        self.title_lb.setText(t)

    def set_num(self, n):
        self.num = self.badge.num = n
        self.badge.update()

    def set_summary(self, t: str):
        self._sc_summary = t or ""
        self._sync_head()

    def set_status(self, text: str = "", kind: str = "", state: str | None = None):
        """Nhãn bên phải tiêu đề + màu ô số (state: done / run / err / "" – mặc định theo kind)."""
        self.chip.set(text, kind)
        self.badge.set_state(state if state is not None else {"ok": "done", "run": "run", "err": "err"}.get(kind, ""))

    def _sync_head(self):
        t = self._sc_summary or ("" if self._open else self._sc_subtitle.split(". ")[0])
        self.sum_lb.setText(t)
        self.sum_lb.setVisible(bool(t))

    # ---- mở / gập
    def set_collapsible(self, on: bool):
        self.collapsible = on
        self.chev.setVisible(on)
        self._headw.setCursor(Qt.CursorShape.PointingHandCursor if on else Qt.CursorShape.ArrowCursor)
        if not on and not self._open:
            self.set_open(True, anim=False)

    def is_open(self) -> bool:
        return self._open

    def set_open(self, on: bool, anim: bool = True, user: bool = False):
        if not self.collapsible:
            on = True
        if on == self._open:
            return
        self._open = on
        self.chev.set_open(on, anim)
        self._anim.stop()
        if on:
            self.body.show()
            if anim and self.isVisible():
                lay = self.body.layout()
                w = max(200, self.body.width() or self.width())
                h = lay.heightForWidth(w) if lay.hasHeightForWidth() else self.body.sizeHint().height()
                self._anim.setStartValue(0)
                self._anim.setEndValue(max(40, h))
                self.body.setMaximumHeight(0)
                self._anim.start()
            else:
                self.body.setMaximumHeight(16777215)
        else:
            self.body.hide()
        self._sync_head()
        self.update()
        self.toggled.emit(on)
        if self.group is not None and user:
            self.group.changed(self, on)

    @staticmethod
    def reveal(w: QWidget):
        """Mở mọi thẻ bước đang gập chứa widget w (trước khi cuộn tới / đặt con trỏ vào nó)."""
        x = w
        while x is not None:
            if isinstance(x, StepCard) and not x.is_open():
                x.set_open(True, anim=False, user=True)
            x = x.parentWidget()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        g = QLinearGradient(0, 0, 0, r.height())
        g.setColorAt(0, QColor(theme.CARD))
        g.setColorAt(1, QColor("#121722"))
        p.setBrush(g)
        op = self._open and self.collapsible
        p.setPen(QPen(theme.c(theme.ACCENT, 92) if op else QColor(theme.BORDER2 if self._headw.hover else theme.BORDER), 1))
        p.drawRoundedRect(r, self.radius, self.radius)
        if self._headw.hover and self.collapsible:   # rê chuột lên tiêu đề: sáng nhẹ
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(255, 255, 255, 8))
            hr = QRectF(self._headw.geometry()).adjusted(1, 1, -1, 0)
            path = QPainterPath()
            path.addRoundedRect(hr, self.radius - 1, self.radius - 1)
            p.drawPath(path)


class CardGroup:
    """Các thẻ bước của 1 tab: đánh số 1, 2, 3… theo thứ tự; exclusive = mỗi lúc chỉ mở 1 thẻ (thẻ "ghim" luôn mở).
    Nhớ thẻ đang mở theo tab (Cài đặt ui_cards: exclusive -> mã thẻ, không -> {mã thẻ: mở?}). Thẻ trong `manual` tự
    nhớ trạng thái bằng cài đặt riêng (vd khung Công cụ tab Phụ đề) – không khôi phục / lưu ở đây."""

    def __init__(self, key: str, cards: list, pinned=(), default: int = 0, saved=None, on_save=None,
                 exclusive: bool = True, opened=(), manual=()):
        self.key, self.cards, self.on_save, self.exclusive = key, list(cards), on_save, exclusive
        self.manual = list(manual)
        for i, c in enumerate(self.cards):
            if c.num is None:
                c.set_num(i + 1)
            c.group = self
            if c in pinned:
                c.set_collapsible(False)
        auto = [c for c in self.cards if c.collapsible and c not in self.manual]
        if exclusive:
            keys = [c.key for c in auto]
            want = saved if (saved in keys or saved == "") else (keys[default] if 0 <= default < len(keys) else None)
            for c in auto:
                c.set_open(c.key == want, anim=False)
        else:
            saved = saved if isinstance(saved, dict) else {}
            for c in auto:
                c.set_open(bool(saved.get(c.key, c.key in opened)), anim=False)

    def changed(self, card, on: bool):
        if card in self.manual:
            return
        if self.exclusive:
            if on:
                for c in self.cards:
                    if c is not card and c.collapsible and c not in self.manual:
                        c.set_open(False, anim=False)
            if self.on_save:
                self.on_save(self.key, card.key if on else "")
        elif self.on_save:
            self.on_save(self.key, {card.key: bool(on)})


class TabRail(QWidget):
    """Thanh tab DỌC của trang dự án (2.2.0): chia nhóm (Quy trình / Hình ảnh / Khác), tab quy trình có số 1–4, ô chọn
    trượt mượt tới tab được bấm. Cùng cách dùng như IconTabs (changed, set_current, cur, items) – thứ tự hiện khác thứ tự
    trang (groups = danh sách số thứ tự trang)."""
    changed = Signal(int)
    W = 92

    def __init__(self, items: list[tuple[str, str]], groups: list[tuple[str, list[int]]], nums: dict | None = None,
                 parent=None):
        super().__init__(parent)
        self.items, self.groups, self.nums = items, groups, nums or {}
        self.cur = 0
        self._hov = -1
        self._sel_y = -1.0
        self.setMouseTracking(True)
        self.setFixedWidth(self.W)
        self.setFont(theme.font(8.4, QFont.Weight.DemiBold))
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding)
        self._anim = QPropertyAnimation(self, b"selY", self)
        self._anim.setDuration(260)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)

    def _get_y(self):
        return self._sel_y

    def _set_y(self, v):
        self._sel_y = v
        self.update()

    selY = Property(float, _get_y, _set_y)

    def _geom(self):
        """-> ({trang: QRectF}, [(tiêu đề nhóm, QRectF)]) theo chiều cao hiện có."""
        n = sum(len(ix) for _, ix in self.groups)
        gh = 22.0
        avail = max(100.0, self.height() - 10 - gh * len(self.groups))
        ih = max(44.0, min(60.0, avail / max(1, n)))
        y = 6.0
        rects, heads = {}, []
        for title, ix in self.groups:
            heads.append((title, QRectF(4, y, self.W - 8, gh)))
            y += gh
            for i in ix:
                rects[i] = QRectF(6, y, self.W - 12, ih - 4)
                y += ih
        return rects, heads

    def sizeHint(self):
        n = sum(len(ix) for _, ix in self.groups)
        return QSize(self.W, int(12 + 22 * len(self.groups) + 58 * n))

    def minimumSizeHint(self):
        n = sum(len(ix) for _, ix in self.groups)
        return QSize(self.W, int(12 + 22 * len(self.groups) + 44 * n))

    def set_current(self, i: int, emit: bool = True):
        if 0 <= i < len(self.items):
            rects, _ = self._geom()
            if i in rects:
                self._anim.stop()
                if self._sel_y < 0 or not self.isVisible():
                    self._sel_y = rects[i].top()
                else:
                    self._anim.setStartValue(self._sel_y)
                    self._anim.setEndValue(rects[i].top())
                    self._anim.start()
            self.cur = i
            self.update()
            if emit:
                self.changed.emit(i)

    def resizeEvent(self, e):
        rects, _ = self._geom()
        if self.cur in rects:
            self._anim.stop()
            self._sel_y = rects[self.cur].top()
        super().resizeEvent(e)

    def _hit(self, pos) -> int:
        rects, _ = self._geom()
        return next((i for i, r in rects.items() if r.contains(pos)), -1)

    def mouseMoveEvent(self, e):
        h = self._hit(e.position())
        if h != self._hov:
            self._hov = h
            self.update()

    def leaveEvent(self, e):
        self._hov = -1
        self.update()

    def mousePressEvent(self, e):
        h = self._hit(e.position())
        if h >= 0:
            self.set_current(h)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        rects, heads = self._geom()
        p.setFont(theme.font(7.0, QFont.Weight.Bold))
        p.setPen(QColor(theme.MUTED))
        for title, r in heads:
            p.drawText(r.adjusted(0, 6, 0, 0), Qt.AlignmentFlag.AlignCenter, title.upper())
        if self.cur in rects:                     # ô chọn (trượt tới tab mới)
            r = QRectF(rects[self.cur])
            if self._sel_y >= 0:
                r.moveTop(self._sel_y)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(theme.c(theme.ACCENT, 34))
            p.drawRoundedRect(r, 12, 12)
        p.setFont(self.font())
        fm = p.fontMetrics()
        for i, r in rects.items():
            on, hov = i == self.cur, i == self._hov
            if hov and not on:
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QColor(255, 255, 255, 12))
                p.drawRoundedRect(r, 12, 12)
            text, ic = self.items[i]
            small = r.height() < 50
            ib = QRectF(r.center().x() - 18, r.top() + (4 if small else 6) - (1 if hov or on else 0), 36, 26)
            if on:
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(theme.c(theme.ACCENT, 44))
                p.drawRoundedRect(ib, 9, 9)
            col = theme.ACCENT2 if on else (theme.TEXT if hov else theme.MUTED)
            icons.paint_icon(p, ic, QRectF(ib.center().x() - 9, ib.center().y() - 9, 18, 18), col, 1.9)
            n = self.nums.get(i)
            if n:
                br = QRectF(ib.right() - 8, ib.top() - 4, 15, 15)
                p.setPen(QPen(QColor(theme.SIDEBAR), 2))
                p.setBrush(theme.accent_gradient(br.left(), br.top(), br.right(), br.bottom()) if on
                           else QColor(theme.CARD3))
                p.drawEllipse(br)
                p.setPen(QColor(theme.ON_ACCENT if on else theme.TEXT2))
                p.setFont(theme.font(6.6, QFont.Weight.Bold))
                p.drawText(br, Qt.AlignmentFlag.AlignCenter, str(n))
                p.setFont(self.font())
            p.setPen(QColor(theme.TEXT if on or hov else theme.MUTED))
            p.drawText(QRectF(r.left() + 1, ib.bottom() + 1, r.width() - 2, r.bottom() - ib.bottom() - 1),
                       Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop,
                       fm.elidedText(text, Qt.TextElideMode.ElideRight, int(r.width() - 2)))


class FlowStepper(QWidget):
    """Thanh tiến độ 4 bước trên thanh đầu trang dự án (2.2.0): mỗi bước 1 ô (số / ✓ xong / đang chạy %), bấm = mở
    bước đó ở tab Tự động."""
    clicked = Signal(int)

    def __init__(self, labels: list[str], parent=None):
        super().__init__(parent)
        self.steps = [{"label": t, "state": "", "meta": ""} for t in labels]
        self._hov = -1
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(42)
        self._f1 = theme.font(9.0, QFont.Weight.Bold)
        self._f2 = theme.font(7.6)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)

    def set_step(self, i: int, state: str, meta: str = ""):
        if 0 <= i < len(self.steps):
            st = self.steps[i]
            if (st["state"], st["meta"]) != (state, meta):
                st["state"], st["meta"] = state, meta
                self.updateGeometry()
                self.update()

    def _rects(self) -> list[QRectF]:
        from PySide6.QtGui import QFontMetrics

        m1, m2 = QFontMetrics(self._f1), QFontMetrics(self._f2)
        x, out = 4.0, []
        for i, st in enumerate(self.steps):
            w = 6 + 24 + 8 + max(m1.horizontalAdvance(st["label"]), m2.horizontalAdvance(st["meta"])) + 12
            out.append(QRectF(x, 4, w, self.height() - 8))
            x += w + 18
        return out

    def sizeHint(self):
        rs = self._rects()
        return QSize(int(rs[-1].right() + 4) if rs else 100, 42)

    def minimumSizeHint(self):
        return QSize(120, 42)

    def mouseMoveEvent(self, e):
        h = next((i for i, r in enumerate(self._rects()) if r.contains(e.position())), -1)
        if h != self._hov:
            self._hov = h
            self.update()

    def leaveEvent(self, e):
        self._hov = -1
        self.update()

    def mousePressEvent(self, e):
        h = next((i for i, r in enumerate(self._rects()) if r.contains(e.position())), -1)
        if h >= 0:
            self.clicked.emit(h)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        rs = self._rects()
        sc = min(1.0, (self.width() - 4) / (rs[-1].right() + 4)) if rs else 1.0
        p.scale(sc, 1.0)
        bg = QRectF(0.5, 0.5, rs[-1].right() + 3.5 if rs else 0, self.height() - 1)
        p.setPen(QPen(QColor(theme.BORDER), 1))
        p.setBrush(QColor(theme.BG))
        p.drawRoundedRect(bg, 12, 12)
        for i, (st, r) in enumerate(zip(self.steps, rs)):
            if i == self._hov:
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QColor(theme.CARD2))
                p.drawRoundedRect(r, 9, 9)
            if i:
                icons.paint_icon(p, "chev_right", QRectF(r.left() - 15, r.center().y() - 6, 12, 12), "#3C455A", 2.0)
            b = QRectF(r.left() + 6, r.center().y() - 12, 24, 24)
            state = st["state"]
            col = {"done": theme.OK, "run": theme.WARN}.get(state)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(theme.c(col, 42) if col else QColor(theme.CARD3))
            p.drawRoundedRect(b, 8, 8)
            if state == "done":
                icons.paint_icon(p, "check", b.adjusted(5, 5, -5, -5), theme.OK, 2.4)
            else:
                p.setPen(QColor(col or theme.TEXT))
                p.setFont(theme.font(8.4, QFont.Weight.Bold))
                p.drawText(b, Qt.AlignmentFlag.AlignCenter, str(i + 1))
            tx = b.right() + 8
            p.setPen(QColor(theme.TEXT))
            p.setFont(self._f1)
            p.drawText(QRectF(tx, r.top() + 2, r.right() - tx, r.height() / 2), Qt.AlignmentFlag.AlignLeft |
                       Qt.AlignmentFlag.AlignBottom, st["label"])
            p.setPen(QColor("#F7C766" if state == "run" else theme.MUTED))
            p.setFont(self._f2)
            p.drawText(QRectF(tx, r.center().y() + 1, r.right() - tx, r.height() / 2 - 2), Qt.AlignmentFlag.AlignLeft |
                       Qt.AlignmentFlag.AlignTop, st["meta"])
