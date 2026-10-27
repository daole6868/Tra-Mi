"""Hộp chọn model AI (nút ☰ cạnh ô Model ở Cài đặt → Công cụ dịch): đủ mọi model key dùng được, mới nhất trước, có ô tìm.
Trước 2.1.14 là menu chỉ hiện 60 model đầu theo ABC (model mới như gpt-5.2-pro, o3, o4-mini bị cắt mất)."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QCheckBox, QDialog, QLineEdit, QListWidget, QListWidgetItem, QVBoxLayout

from . import theme
from .widgets import button, hbox, label


class ModelPicker(QDialog):
    def __init__(self, parent, title: str, models: list[str], hidden: list[str], current: str = ""):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(520, 620)
        self.models, self.hidden, self.chosen = list(models), list(hidden), ""
        v = QVBoxLayout(self)
        v.setContentsMargins(18, 16, 18, 14)
        v.setSpacing(8)
        v.addWidget(label(title, "h3"))
        note = f"{len(models)} model dùng để dịch, mới nhất ở trên"
        if hidden:
            note += (f" · ẩn {len(hidden)} model không dịch được (giọng nói, chép lời, ảnh, embedding, kiểm duyệt, "
                     "chỉ chạy qua Responses API…)")
        v.addWidget(label(note + ". Bấm đúp để chọn.", "muted", wrap=True))
        self.search = QLineEdit()
        self.search.setPlaceholderText("Tìm model… (VD: gpt-5, flash, claude, deepseek)")
        self.search.textChanged.connect(self._fill)
        v.addWidget(self.search)
        self.list = QListWidget()
        self.list.setStyleSheet(
            f"QListWidget {{ background: #0F141D; border: 1px solid {theme.BORDER}; border-radius: 9px; padding: 4px; "
            f"color: {theme.TEXT}; outline: none; }} QListWidget::item {{ padding: 5px 8px; border-radius: 6px; }} "
            f"QListWidget::item:selected {{ background: {theme.CARD3}; color: {theme.TEXT}; }} "
            f"QListWidget::item:hover:!selected {{ background: {theme.CARD2}; }}")
        self.list.itemDoubleClicked.connect(lambda _it: self._use())
        v.addWidget(self.list, 1)
        self.show_all = QCheckBox("Hiện cả model không dùng để dịch")
        self.show_all.setVisible(bool(hidden))
        self.show_all.toggled.connect(self._fill)
        self.count = label("", "muted")
        v.addLayout(hbox(self.show_all, None, self.count))
        v.addLayout(hbox(None, button("Chọn model này", "check", on_click=self._use),
                         button("Đóng", "x", on_click=self.reject)))
        self._fill()
        self._select(current)
        self.search.setFocus()

    def _fill(self, *_):
        q = self.search.text().strip().lower()
        self.list.clear()
        names = [(n, False) for n in self.models]
        if self.show_all.isChecked():
            names += [(n, True) for n in self.hidden]
        shown = 0
        for n, off in names:
            if q and q not in n.lower():
                continue
            it = QListWidgetItem(n + ("   (không dùng để dịch)" if off else ""))
            it.setData(Qt.ItemDataRole.UserRole, n)
            if off:
                it.setForeground(Qt.GlobalColor.gray)
            self.list.addItem(it)
            shown += 1
        self.count.setText(f"{shown} model")
        if self.list.count():
            self.list.setCurrentRow(0)

    def _select(self, name: str):
        for i in range(self.list.count()):
            if self.list.item(i).data(Qt.ItemDataRole.UserRole) == name:
                self.list.setCurrentRow(i)
                self.list.scrollToItem(self.list.item(i))
                return

    def _use(self):
        it = self.list.currentItem()
        if it:
            self.chosen = it.data(Qt.ItemDataRole.UserRole)
            self.accept()
