"""Hộp xem trước của nút "Sửa tên pinyin" (2.4.4): liệt kê mọi tên / từ còn để pinyin trong bản dịch (gom theo cũ →
mới, kèm loại, số câu, câu ví dụ) – tích tên muốn đổi, sửa tay ô "Đổi thành" khi âm chưa đúng, chọn lưu vào Bảng tên
riêng của dự án, rồi Áp dụng. Không gọi AI."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QDialog, QHeaderView, QTableWidget, QTableWidgetItem,
                               QVBoxLayout)

from ..hanviet import KIND_LABEL
from . import theme
from .widgets import button, flow, hbox, label

C_ON, C_OLD, C_NEW, C_ZH, C_KIND, C_N, C_EX = range(7)


class PinyinDialog(QDialog):
    def __init__(self, parent, cands: list[dict], scope: str = "cả phim", can_save: bool = True):
        super().__init__(parent)
        self.setWindowTitle("Sửa tên pinyin")
        self.resize(900, 600)
        self.cands = cands
        v = QVBoxLayout(self)
        v.setContentsMargins(18, 16, 18, 14)
        v.setSpacing(8)
        v.addWidget(label("Sửa tên pinyin", "h3"))
        v.addWidget(label(f"Tìm thấy {len(cands)} tên / từ còn để pinyin trong bản dịch ({scope}). Tích tên muốn đổi, "
                          "sửa ô \"Đổi thành\" nếu âm Hán Việt chưa đúng, rồi bấm Áp dụng. Tích sẵn theo lựa chọn "
                          "loại tên ở khung Dịch.", "muted", wrap=True))
        v.addWidget(flow(button("Tích hết", "check", flat=True, on_click=lambda: self._tick(None)),
                         button("Bỏ hết", "x", flat=True, on_click=lambda: self._tick(set())),
                         button("Chỉ tên riêng", "check", flat=True, on_click=lambda: self._tick({"person", "place"})),
                         button("Chỉ tên người", "check", flat=True, on_click=lambda: self._tick({"person"})),
                         button("Chỉ địa danh, môn phái, đồ vật", "check", flat=True,
                                on_click=lambda: self._tick({"place"})),
                         button("Chỉ từ thường", "check", flat=True, on_click=lambda: self._tick({"term"}))))
        t = self.table = QTableWidget(len(cands), 7)
        t.setHorizontalHeaderLabels(["", "Bản dịch đang có", "Đổi thành", "Chữ gốc", "Loại", "Số câu", "Ví dụ"])
        t.verticalHeader().setVisible(False)
        t.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        t.setEditTriggers(QAbstractItemView.EditTrigger.DoubleClicked | QAbstractItemView.EditTrigger.EditKeyPressed
                          | QAbstractItemView.EditTrigger.SelectedClicked)
        t.setStyleSheet(f"QTableWidget {{ background: #0F141D; border: 1px solid {theme.BORDER}; border-radius: 9px; }}")
        ro = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
        for r, c in enumerate(cands):
            on = QTableWidgetItem()
            on.setFlags(ro | Qt.ItemFlag.ItemIsUserCheckable)
            on.setCheckState(Qt.CheckState.Checked if c.get("on", True) else Qt.CheckState.Unchecked)
            t.setItem(r, C_ON, on)
            for col, text, edit in ((C_OLD, c["old"], False), (C_NEW, c["new"], True), (C_ZH, c.get("zh", ""), False),
                                    (C_KIND, KIND_LABEL.get(c.get("kind", ""), ""), False),
                                    (C_N, str(len(c.get("rows") or [])), False), (C_EX, c.get("example", ""), False)):
                it = QTableWidgetItem(text)
                it.setFlags(ro | (Qt.ItemFlag.ItemIsEditable if edit else Qt.ItemFlag.NoItemFlags))
                if col == C_NEW:
                    it.setToolTip("Bấm đúp để sửa tên Hán Việt (VD đổi âm chữ nhiều cách đọc)")
                elif col == C_EX:
                    it.setToolTip(text)
                t.setItem(r, col, it)
        hh = t.horizontalHeader()
        for col in (C_ON, C_ZH, C_KIND, C_N):
            hh.setSectionResizeMode(col, QHeaderView.ResizeMode.ResizeToContents)
        for col in (C_OLD, C_NEW):
            hh.setSectionResizeMode(col, QHeaderView.ResizeMode.Interactive)
            t.setColumnWidth(col, 170)
        hh.setSectionResizeMode(C_EX, QHeaderView.ResizeMode.Stretch)
        t.itemChanged.connect(lambda _it: self._count())
        v.addWidget(t, 1)
        self.save = QCheckBox("Lưu các tên đã tích vào Bảng tên riêng của dự án (lần dịch sau mọi công cụ dịch dùng "
                              "đúng tên này)")
        self.save.setChecked(can_save)
        self.save.setEnabled(can_save)
        if not can_save:
            self.save.setToolTip("Chỉ lưu được khi dự án đang dịch sang tiếng Việt")
        v.addWidget(self.save)
        self.ok = button("Áp dụng", "check", on_click=self._apply)
        v.addLayout(hbox(None, button("Huỷ", "x", flat=True, on_click=self.reject), self.ok))
        self._count()

    def _tick(self, kinds: set | None):
        t = self.table
        t.blockSignals(True)
        for r, c in enumerate(self.cands):
            on = kinds is None or c.get("kind") in kinds
            t.item(r, C_ON).setCheckState(Qt.CheckState.Checked if on else Qt.CheckState.Unchecked)
        t.blockSignals(False)
        self._count()

    def _checked(self) -> list[int]:
        return [r for r in range(self.table.rowCount())
                if self.table.item(r, C_ON).checkState() == Qt.CheckState.Checked]

    def _count(self):
        rows = self._checked()
        n = len({k for r in rows for k in (self.cands[r].get("rows") or [])})
        self.ok.setText(f"Áp dụng {len(rows)} tên ({n} câu)" if rows else "Áp dụng")
        self.ok.setEnabled(bool(rows))

    def _apply(self):
        if self._checked():
            self.accept()

    def picks(self) -> list[dict]:
        """Các tên đã tích: {old, new (đề xuất), use (chữ thay – có thể đã sửa tay), zh, kind}."""
        out = []
        for r in self._checked():
            c = self.cands[r]
            use = (self.table.item(r, C_NEW).text() or "").strip() or c["new"]
            out.append({"old": c["old"], "new": c["new"], "use": use, "zh": c.get("zh", ""), "kind": c.get("kind")})
        return out

    def save_glossary(self) -> bool:
        return self.save.isEnabled() and self.save.isChecked()
