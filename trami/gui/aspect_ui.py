"""Chọn tỉ lệ khung video: nhóm Dọc / Ngang (mỗi tỉ lệ 1 ô có hình chữ nhật minh hoạ) + Gốc; ô "Xuất thêm tỉ lệ"
(mỗi tỉ lệ 1 nút bật / tắt + 👁 xem thử). Tab Chỉnh sửa và tab Xuất mỗi nơi 1 bộ chọn, nối với nhau (chọn ở đâu cũng
đổi cả hai)."""
from __future__ import annotations

from PySide6.QtCore import QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import QAbstractButton, QHBoxLayout, QVBoxLayout, QWidget

from . import theme
from .widgets import FlowLayout

USES = {"9:16": "TikTok · Reels · Shorts", "3:4": "Instagram dọc", "4:5": "Facebook · Instagram",
        "2:3": "Pinterest", "1:1": "Vuông", "16:9": "YouTube · Facebook", "4:3": "Màn cũ", "3:2": "Ảnh máy ảnh",
        "5:4": "Ngang gần vuông", "7:5": "Ngang", "3:5": "Dọc hẹp", "5:3": "Ngang rộng", "5:7": "Dọc"}


def ratio_label(r: str) -> str:
    return "Gốc (giữ nguyên)" if r in ("", "original") else f"{r} – {USES.get(r, '')}".rstrip(" –")


def is_portrait(r: str) -> bool:
    from ..video import aspect_wh

    wh = aspect_wh(r)
    return bool(wh) and wh[0] < wh[1]


class RatioChip(QAbstractButton):
    """Ô tỉ lệ: hình chữ nhật đúng tỉ lệ + chữ "9:16" (+ dòng nhỏ: hợp với đâu)."""

    def __init__(self, ratio: str, small: bool = False, parent=None):
        super().__init__(parent)
        self.ratio = ratio
        self.small = small
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(ratio_label(ratio))
        self._hov = False

    def sizeHint(self):
        return QSize(60, 48) if self.small else QSize(76, 74)

    def enterEvent(self, e):
        self._hov = True
        self.update()

    def leaveEvent(self, e):
        self._hov = False
        self.update()

    def paintEvent(self, e):
        from ..video import aspect_wh

        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        on = self.isChecked()
        p.setPen(QPen(QColor(theme.ACCENT if on else theme.BORDER), 1.6 if on else 1))
        p.setBrush(QColor("#3A2420" if on else (theme.CARD2 if self._hov else theme.CARD)))
        p.drawRoundedRect(r, 9, 9)
        wh = aspect_wh(self.ratio) or (16, 9)
        box = 22 if self.small else 30
        k = box / max(wh)
        w, h = wh[0] * k, wh[1] * k
        top = r.top() + (6 if self.small else 8)
        icon = QRectF(r.center().x() - w / 2, top + (box - h) / 2, w, h)
        col = QColor(theme.ACCENT if on else theme.MUTED)
        p.setPen(QPen(col, 1.6))
        p.setBrush(QColor(col.red(), col.green(), col.blue(), 50))
        p.drawRoundedRect(icon, 2.5, 2.5)
        p.setPen(QColor("#FFFFFF" if on else theme.TEXT))
        p.setFont(theme.font(8.4 if self.small else 9, QFont.Weight.DemiBold))
        y = top + box + 2
        p.drawText(QRectF(r.left(), y, r.width(), 15), Qt.AlignmentFlag.AlignHCenter, self.ratio)
        if not self.small:
            p.setFont(theme.font(7))
            p.setPen(QColor(theme.MUTED))
            use = USES.get(self.ratio, "")
            fm = p.fontMetrics()
            p.drawText(QRectF(r.left() + 2, y + 15, r.width() - 4, 14), Qt.AlignmentFlag.AlignHCenter,
                       fm.elidedText(use.split(" · ")[0], Qt.TextElideMode.ElideRight, int(r.width() - 6)))


class _Seg(QAbstractButton):
    """Nút chọn nhóm (Gốc / Dọc / Ngang)."""

    def __init__(self, text: str, parent=None):
        super().__init__(parent)
        self.setText(text)
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFont(theme.font(9.4, QFont.Weight.DemiBold))

    def sizeHint(self):
        return QSize(self.fontMetrics().horizontalAdvance(self.text()) + 34, 32)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        if self.isChecked():
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(theme.accent_gradient(r.left(), 0, r.right(), 0))
        else:
            p.setPen(QPen(QColor(theme.BORDER), 1))
            p.setBrush(QColor(theme.CARD))
        p.drawRoundedRect(r, 10, 10)
        p.setPen(QColor("#FFFFFF" if self.isChecked() else theme.MUTED))
        p.drawText(r, Qt.AlignmentFlag.AlignCenter, self.text())


class AspectPicker(QWidget):
    """Gốc | Dọc | Ngang + các ô tỉ lệ của nhóm đang chọn. changed(tỉ lệ) khi bấm (không phát khi set_value)."""
    changed = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        from ..video import LANDSCAPE, PORTRAIT

        self.value = "original"
        self.group = "landscape"
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)
        top = QHBoxLayout()
        top.setSpacing(6)
        self.segs = {}
        for key, text in (("original", "Gốc"), ("portrait", "Dọc"), ("landscape", "Ngang")):
            b = _Seg(text)
            b.clicked.connect(lambda _=False, k=key: self._group_clicked(k))
            top.addWidget(b)
            self.segs[key] = b
        top.addStretch(1)
        lay.addLayout(top)
        self.chips: dict[str, list[RatioChip]] = {"portrait": [], "landscape": []}
        self.boxes = {}
        for g, items in (("portrait", PORTRAIT), ("landscape", LANDSCAPE)):
            w = QWidget()
            fl = FlowLayout(w, spacing=6)
            for r in items:
                c = RatioChip(r)
                c.clicked.connect(lambda _=False, r=r: self._chip_clicked(r))
                fl.addWidget(c)
                self.chips[g].append(c)
            lay.addWidget(w)
            self.boxes[g] = w
        self._refresh()

    def _group_clicked(self, k: str):
        if k == "original":
            self.value = "original"
            self._refresh()
            self.changed.emit(self.value)
            return
        self.group = k
        if self.value != "original" and self.value != "1:1":   # đổi nhóm: giữ tỉ lệ đảo chiều (16:9 <-> 9:16)
            a, b = self.value.split(":")
            flip = f"{b}:{a}"
            if any(c.ratio == flip for c in self.chips[k]):
                self.value = flip
                self._refresh()
                self.changed.emit(self.value)
                return
        self._refresh()

    def _chip_clicked(self, r: str):
        self.value = r
        self._refresh()
        self.changed.emit(r)

    def set_value(self, r: str):
        self.value = r or "original"
        if self.value != "original" and self.value != "1:1":
            self.group = "portrait" if is_portrait(self.value) else "landscape"
        self._refresh()

    def _refresh(self):
        orig = self.value == "original"
        self.segs["original"].setChecked(orig)
        self.segs["portrait"].setChecked(not orig and self.group == "portrait")
        self.segs["landscape"].setChecked(not orig and self.group == "landscape")
        for g, w in self.boxes.items():
            w.setVisible(g == self.group)
            for c in self.chips[g]:
                c.setChecked(not orig and c.ratio == self.value)


class ExtraRatios(QWidget):
    """Xuất thêm tỉ lệ: mỗi tỉ lệ 1 nút bật / tắt + nút 👁 xem thử trên trình phát."""
    changed = Signal(list)       # danh sách tỉ lệ xuất thêm
    preview = Signal(str)        # bấm 👁: xem thử tỉ lệ này ("" = về tỉ lệ chính)
    COMMON = ["9:16", "16:9", "1:1", "4:5", "3:4", "4:3", "2:3", "3:2"]

    def __init__(self, parent=None):
        super().__init__(parent)
        from .widgets import IconButton

        self.values: list[str] = []
        self.viewing = ""
        fl = FlowLayout(self, spacing=8)
        self.chips: dict[str, RatioChip] = {}
        self.eyes: dict[str, IconButton] = {}
        for r in self.COMMON:
            box = QWidget()
            h = QHBoxLayout(box)
            h.setContentsMargins(0, 0, 0, 0)
            h.setSpacing(2)
            c = RatioChip(r, small=True)
            c.clicked.connect(lambda _=False, r=r: self._toggle(r))
            eye = IconButton("eye", f"Xem thử khung {r} trên trình phát (kéo chữ / logo / phụ đề khi đang xem thử "
                                    "= chỉnh riêng cho tỉ lệ này)", 26)
            eye.clicked.connect(lambda _=False, r=r: self._eye(r))
            h.addWidget(c)
            h.addWidget(eye)
            fl.addWidget(box)
            self.chips[r] = c
            self.eyes[r] = eye

    def _toggle(self, r: str):
        if r in self.values:
            self.values.remove(r)
        else:
            self.values.append(r)
        self._refresh()
        self.changed.emit(list(self.values))

    def _eye(self, r: str):
        self.viewing = "" if self.viewing == r else r
        self._refresh()
        self.preview.emit(self.viewing)

    def set_values(self, values: list, main: str = ""):
        self.values = [v for v in values or [] if v in self.chips]
        self.main = main
        self._refresh()

    def set_viewing(self, r: str):
        self.viewing = r or ""
        self._refresh()

    def _refresh(self):
        main = getattr(self, "main", "")
        for r, c in self.chips.items():
            c.setChecked(r in self.values)
            c.setEnabled(r != main)
            c.setToolTip(("Tỉ lệ chính đang chọn" if r == main else
                          ("Bỏ xuất thêm " if r in self.values else "Xuất thêm bản ") + ratio_label(r)))
            self.eyes[r].set_icon("eye_off" if self.viewing == r else "eye")
