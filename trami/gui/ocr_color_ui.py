"""Giao diện lọc màu chữ phụ đề khi OCR (2.1.44): các cặp màu chữ / viền, hút màu trên khung hình, xem thử."""
from __future__ import annotations

import numpy as np
from PySide6.QtCore import QPoint, Qt, Signal
from PySide6.QtGui import QColor, QImage, QPixmap
from PySide6.QtWidgets import QComboBox, QDialog, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from .. import ocrcolor
from .widgets import ColorButton, button, label

OUTLINE_MODES = [("color", "Viền màu"), ("none", "Không xét viền")]


def _qimage(img: np.ndarray) -> QImage:
    rgb = np.ascontiguousarray(img[:, :, ::-1])
    h, w = rgb.shape[:2]
    return QImage(rgb.data, w, h, 3 * w, QImage.Format.Format_RGB888).copy()


class _Row(QWidget):
    changed = Signal()
    pick = Signal(object, str)           # (hàng, "fill" | "outline")
    remove = Signal(object)

    def __init__(self, ent: dict):
        super().__init__()
        h = QHBoxLayout(self)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(6)
        self.fill = ColorButton(ent.get("fill") or "#FFFFFF")
        self.fill.setToolTip("Màu chữ (thân chữ) – bấm để chọn, hoặc bấm 'Hút' rồi bấm vào nét chữ trên khung hình")
        self.fill.changed.connect(lambda *_: self.changed.emit())
        self.mode = QComboBox()
        for k, t in OUTLINE_MODES:
            self.mode.addItem(t, k)
        self.mode.setCurrentIndex(0 if ent.get("outline") else 1)
        self.mode.currentIndexChanged.connect(self._mode)
        self.outline = ColorButton(ent.get("outline") or "#000000")
        self.outline.setToolTip("Màu viền chữ")
        self.outline.changed.connect(lambda *_: self.changed.emit())
        self.bpick_f = button("Hút", "search", "Hút màu chữ: bấm vào nét chữ trên khung hình đang xem", flat=True,
                              on_click=lambda: self.pick.emit(self, "fill"))
        self.bpick_o = button("Hút", "search", "Hút màu viền: bấm vào viền chữ trên khung hình đang xem", flat=True,
                              on_click=lambda: self.pick.emit(self, "outline"))
        self.bdel = button("", "x", "Bỏ cặp màu này", flat=True, on_click=lambda: self.remove.emit(self))
        for w in (label("Chữ", "muted"), self.fill, self.bpick_f, self.mode, self.outline, self.bpick_o, self.bdel):
            h.addWidget(w)
        h.addStretch(1)
        self._mode()

    def _mode(self, *_):
        on = self.mode.currentData() == "color"
        self.outline.setVisible(on)
        self.bpick_o.setVisible(on)
        self.changed.emit()

    def entry(self) -> dict:
        return {"fill": self.fill.color.upper(),
                "outline": self.outline.color.upper() if self.mode.currentData() == "color" else ""}


class ColorRows(QWidget):
    """Danh sách cặp màu chữ / viền (tối đa ocrcolor.MAX_COLORS)."""
    changed = Signal()
    pickRequested = Signal(object, str)

    def __init__(self, ents: list[dict] | None = None):
        super().__init__()
        self.v = QVBoxLayout(self)
        self.v.setContentsMargins(0, 0, 0, 0)
        self.v.setSpacing(4)
        self.rows: list[_Row] = []
        self.set_entries(ents or ocrcolor.DEFAULT_COLORS)

    def set_entries(self, ents: list[dict]):
        for r in self.rows:
            r.setParent(None)
            r.deleteLater()
        self.rows = []
        for e in (ents or ocrcolor.DEFAULT_COLORS)[:ocrcolor.MAX_COLORS]:
            self._add(e, emit=False)
        self._buttons()

    def _add(self, e: dict, emit: bool = True):
        r = _Row(e)
        r.changed.connect(self.changed.emit)
        r.pick.connect(self.pickRequested.emit)
        r.remove.connect(self._remove)
        self.rows.append(r)
        self.v.addWidget(r)
        self._buttons()
        if emit:
            self.changed.emit()

    def add(self):
        if len(self.rows) < ocrcolor.MAX_COLORS:
            self._add({"fill": "#FFD800", "outline": "#000000"})

    def _remove(self, r: _Row):
        if len(self.rows) <= 1:
            return
        self.rows.remove(r)
        r.setParent(None)
        r.deleteLater()
        self._buttons()
        self.changed.emit()

    def _buttons(self):
        for r in self.rows:
            r.bdel.setVisible(len(self.rows) > 1)

    def entries(self) -> list[dict]:
        return [r.entry() for r in self.rows]


class PickDialog(QDialog):
    """Hút màu: bấm vào nét chữ / viền trên ảnh vùng quét (ảnh phóng to cho dễ bấm trúng nét)."""

    def __init__(self, parent, img: np.ndarray, what: str):
        super().__init__(parent)
        self.setWindowTitle("Hút màu " + ("chữ" if what == "fill" else "viền"))
        self.img = img
        self.color = ""
        h, w = img.shape[:2]
        self.k = max(1.0, min(3.0, 1100 / max(1, w)))
        pm = QPixmap.fromImage(_qimage(img)).scaled(int(w * self.k), int(h * self.k),
                                                    Qt.AspectRatioMode.KeepAspectRatio,
                                                    Qt.TransformationMode.FastTransformation)
        v = QVBoxLayout(self)
        v.addWidget(label("Bấm vào giữa nét " + ("CHỮ" if what == "fill" else "VIỀN") + " phụ đề (không bấm vào nền). "
                          "Tool lấy màu trung vị 5×5 điểm quanh chỗ bấm.", "muted", wrap=True))
        self.pic = QLabel()
        self.pic.setPixmap(pm)
        self.pic.setCursor(Qt.CursorShape.CrossCursor)
        self.pic.mousePressEvent = self._click
        v.addWidget(self.pic)
        row = QHBoxLayout()
        self.sw = QLabel()
        self.sw.setFixedSize(40, 24)
        self.txt = label("Chưa chọn", "muted")
        self.ok = button("Dùng màu này", "check", on_click=self.accept)
        self.ok.setEnabled(False)
        row.addWidget(self.sw)
        row.addWidget(self.txt, 1)
        row.addWidget(self.ok)
        row.addWidget(button("Huỷ", "x", flat=True, on_click=self.reject))
        v.addLayout(row)

    def _click(self, e):
        pt: QPoint = e.position().toPoint()
        x, y = int(pt.x() / self.k), int(pt.y() / self.k)
        h, w = self.img.shape[:2]
        if not (0 <= x < w and 0 <= y < h):
            return
        self.color = ocrcolor.pick(self.img, x, y)
        self.sw.setStyleSheet(f"background:{self.color};border:1px solid #888;border-radius:4px")
        self.txt.setText(f"Màu {self.color} – bấm chỗ khác để chọn lại")
        self.ok.setEnabled(True)


def preview_dialog(parent, img: np.ndarray, settings: dict):
    """Xem thử lọc màu: ảnh đánh dấu (xanh = nét được lấy, đỏ = nét sai màu bị bỏ) + ảnh bộ đọc chữ thấy."""
    mk, seen = ocrcolor.preview(img, settings)
    dlg = QDialog(parent)
    dlg.setWindowTitle("Xem thử lọc màu chữ")
    v = QVBoxLayout(dlg)
    wmax = min(1100, max(img.shape[1], 300))
    for t, data in (("Xanh lá = nét chữ đúng màu (được lấy) · xanh đậm = viền · đỏ = nét chữ sai màu (bị bỏ)", mk),
                    ("Ảnh bộ đọc chữ thấy (chỉ còn chữ đúng màu)", seen)):
        pm = QPixmap()
        pm.loadFromData(data)
        v.addWidget(label(t, "muted", wrap=True))
        lb = QLabel()
        lb.setPixmap(pm.scaledToWidth(wmax, Qt.TransformationMode.SmoothTransformation) if pm.width() > wmax else pm)
        v.addWidget(lb)
    v.addWidget(label("Câu phụ đề bị tô đỏ / mất nét -> chọn lại màu (Hút màu) hoặc nới Độ lệch; chữ trong cảnh còn xanh "
                      "-> thêm màu viền hoặc để Độ lệch Chặt", "muted", wrap=True))
    dlg.exec()
    return dlg


def swatch_html(hex_: str) -> str:
    c = QColor(hex_)
    return f'<span style="background:{c.name()};color:{c.name()}">██</span> {hex_}'
