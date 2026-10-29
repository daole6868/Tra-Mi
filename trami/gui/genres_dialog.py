"""Hộp "Form thể loại phim": xem / sửa form dịch theo từng thể loại (xưng hô, thuật ngữ, giọng văn – trami/genres.py).
Bản sửa lưu ở Cài đặt `genre_forms` [{"id", "text"}]; "Về mặc định" xoá bản sửa."""
from __future__ import annotations

from PySide6.QtWidgets import QDialog, QListWidget, QListWidgetItem, QMessageBox, QPlainTextEdit, QVBoxLayout

from .. import genres
from ..config import load_settings, save_settings
from . import theme
from .widgets import button, hbox, label

HINT = ("Form của thể loại được gắn vào lời nhắc của AI mỗi lần dịch (và khi AI kiểm tra bản dịch): xưng hô, thuật ngữ "
        "(chữ gốc -> cách dịch), giọng văn. Mỗi ý 1 dòng. Luật chung luôn áp dụng: đúng số dòng, đọc 2 câu trước / sau, "
        "không thêm bớt ý. Sửa xong bấm Lưu; 'Về mặc định' lấy lại nội dung gốc.")


class GenresDialog(QDialog):
    def __init__(self, parent=None, current: str = "auto"):
        super().__init__(parent)
        self.setWindowTitle("Form thể loại phim")
        self.resize(860, 580)
        self.chosen = current or "auto"
        v = QVBoxLayout(self)
        v.setContentsMargins(20, 18, 20, 16)
        v.setSpacing(10)
        v.addWidget(label("Form thể loại phim", "h3"))
        v.addWidget(label(HINT, "muted", wrap=True))
        self.list = QListWidget()
        self.list.setFixedWidth(300)
        self.list.setWordWrap(True)
        self.list.setStyleSheet(
            f"QListWidget {{ background: #0F141D; border: 1px solid {theme.BORDER}; border-radius: 9px; padding: 4px; "
            f"color: {theme.TEXT}; outline: none; }} QListWidget::item {{ padding: 7px 8px; border-radius: 7px; }} "
            f"QListWidget::item:selected {{ background: {theme.CARD3}; color: {theme.TEXT}; }} "
            f"QListWidget::item:hover:!selected {{ background: {theme.CARD2}; }}")
        self.list.currentRowChanged.connect(self._show)
        self.text = QPlainTextEdit()
        self.note = label("", "muted", wrap=True)
        right = QVBoxLayout()
        right.addWidget(self.text, 1)
        right.addWidget(self.note)
        v.addLayout(hbox(self.list, right), 1)
        self.b_reset = button("Về mặc định", "refresh", on_click=self._reset)
        self.b_save = button("Lưu", "save", on_click=self._save)
        self.b_use = button("Dùng thể loại này", "check", on_click=self._use)
        v.addLayout(hbox(self.b_reset, None, self.b_save, self.b_use, button("Đóng", "x", on_click=self.reject)))
        self._fill(self.chosen)

    def _own(self) -> dict:
        return genres.edited(load_settings())

    def _fill(self, select: str):
        own = self._own()
        self.list.blockSignals(True)
        self.list.clear()
        for gid, name in genres.choices():
            it = QListWidgetItem(("✎ " if gid in own else "") + name)
            it.setData(256, gid)
            self.list.addItem(it)
        self.list.blockSignals(False)
        ids = [gid for gid, _ in genres.choices()]
        self.list.setCurrentRow(ids.index(select) if select in ids else 0)
        self._show()

    def _cur(self) -> str:
        it = self.list.currentItem()
        return it.data(256) if it else "auto"

    def _show(self, _row: int = 0):
        gid = self._cur()
        edited = gid in self._own()
        self.text.setPlainText(genres.form(gid, "vi", load_settings()))
        self.note.setText("✎ Đã sửa – khác bản mặc định." if edited else "Bản mặc định của tool.")
        self.b_reset.setEnabled(edited)

    def _save(self):
        gid, txt = self._cur(), self.text.toPlainText().strip()
        own = self._own()
        if not txt or txt == genres.default_text(gid).strip():
            own.pop(gid, None)          # giống bản mặc định -> không lưu (lần sau tool cập nhật form vẫn nhận)
        else:
            own[gid] = txt
        save_settings({"genre_forms": genres.to_setting(own)})
        self._fill(gid)

    def _reset(self):
        gid = self._cur()
        if QMessageBox.question(self, "Về mặc định", f"Đưa form “{genres.name(gid)}” về nội dung mặc định?") != \
                QMessageBox.StandardButton.Yes:
            return
        own = self._own()
        own.pop(gid, None)
        save_settings({"genre_forms": genres.to_setting(own)})
        self._fill(gid)

    def _use(self):
        if self.text.toPlainText().strip() != genres.form(self._cur(), "vi", load_settings()).strip():
            self._save()
        self.chosen = self._cur()
        save_settings({"translate_style": self.chosen})
        self.accept()
