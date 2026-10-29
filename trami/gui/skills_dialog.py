"""Hộp "Kỹ năng dịch": xem kỹ năng có sẵn, thêm / sửa / xoá kỹ năng của bạn (luật viết tay gắn vào lời nhắc AI khi dịch,
sửa lỗi nghe, kiểm tra bản dịch – xem trami/skills.py). Lưu ở Cài đặt `translate_skills`."""
from __future__ import annotations

import time

from PySide6.QtWidgets import QDialog, QFileDialog, QLineEdit, QListWidget, QListWidgetItem, QMessageBox, \
    QPlainTextEdit, QVBoxLayout

from .. import skills
from ..config import load_settings, save_settings
from . import theme
from .widgets import button, hbox, label

HINT = ("Viết luật cho AI như dặn một người dịch: thể loại, cách xưng hô, tên riêng / meme dịch thế nào, độ dài câu… "
        "Mỗi ý 1 dòng. Kỹ năng được gắn vào lời nhắc của MỌI công cụ dịch AI (DeepSeek, Claude, GPT, Gemini, Ollama, API "
        "tương thích OpenAI) khi dịch, sửa lỗi nghe và kiểm tra bản dịch.")


class SkillsDialog(QDialog):
    def __init__(self, parent=None, current: str = ""):
        super().__init__(parent)
        self.setWindowTitle("Kỹ năng dịch")
        self.resize(820, 560)
        self.chosen = current
        v = QVBoxLayout(self)
        v.setContentsMargins(20, 18, 20, 16)
        v.setSpacing(10)
        v.addWidget(label("Kỹ năng dịch AI", "h3"))
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
        self.name = QLineEdit()
        self.name.setPlaceholderText("Tên kỹ năng (VD: Phim hài meme mạng)")
        self.text = QPlainTextEdit()
        self.text.setPlaceholderText("- Xưng hô: bạn bè tớ – cậu…\n- 我去 -> Trời đất…\n- Tên công nghệ giữ tên quốc tế…")
        self.ro = label("", "muted", wrap=True)
        right = QVBoxLayout()
        right.addWidget(self.name)
        right.addWidget(self.text, 1)
        right.addWidget(self.ro)
        body = hbox(self.list, right)
        v.addLayout(body, 1)
        self.b_new = button("Thêm kỹ năng mới", "plus", on_click=self._new)
        self.b_copy = button("Chép thành kỹ năng của tôi", "layers", on_click=self._copy)
        self.b_file = button("Nạp từ file .txt / .md…", "download", on_click=self._load_file)
        self.b_save = button("Lưu", "save", on_click=self._save)
        self.b_del = button("Xoá", "trash", danger=True, on_click=self._delete)
        self.b_use = button("Dùng kỹ năng này", "check", on_click=self._use)
        v.addLayout(hbox(self.b_new, self.b_copy, self.b_file, None, self.b_del, self.b_save, self.b_use,
                         button("Đóng", "x", on_click=self.reject)))
        self._fill(current)

    # ------------------------------------------------------------------
    def _items(self) -> list[dict]:
        return skills.all_skills(load_settings())

    def _fill(self, select: str = ""):
        self.list.blockSignals(True)
        self.list.clear()
        for sk in self._items():
            it = QListWidgetItem(("🔒 " if sk.get("builtin") else "✎ ") + sk["name"])
            it.setData(256, sk["id"])
            self.list.addItem(it)
        self.list.blockSignals(False)
        ids = [self.list.item(i).data(256) for i in range(self.list.count())]
        self.list.setCurrentRow(ids.index(select) if select in ids else 0)
        self._show(self.list.currentRow())

    def _cur(self) -> dict | None:
        it = self.list.currentItem()
        if not it:
            return None
        return next((x for x in self._items() if x["id"] == it.data(256)), None)

    def _show(self, _row: int = 0):
        sk = self._cur()
        ro = bool(sk and sk.get("builtin"))
        self.name.setText(sk["name"] if sk else "")
        self.text.setPlainText(sk["text"] if sk else "")
        self.name.setReadOnly(ro)
        self.text.setReadOnly(ro)
        self.ro.setText("Kỹ năng có sẵn – chỉ đọc. Muốn sửa thì bấm 'Chép thành kỹ năng của tôi'." if ro else "")
        self.b_save.setEnabled(bool(sk) and not ro)
        std = bool(sk and sk["id"] == skills.STD_ID)
        self.b_del.setText("Về mặc định" if std else "Xoá")
        self.b_del.setEnabled(bool(sk) and not ro and not (std and sk.get("default")))
        if std:
            self.ro.setText("Kỹ năng dịch chung – sửa trực tiếp rồi bấm Lưu. 'Về mặc định' để lấy lại nội dung gốc.")
        self.b_copy.setEnabled(bool(sk))

    def _own(self) -> list[dict]:
        # kỹ năng Dịch chuẩn chưa sửa (bản mặc định) không ghi vào Cài đặt – lần sau tool cập nhật nội dung vẫn nhận
        return [dict(id=x["id"], name=x["name"], text=x["text"]) for x in self._items()
                if not x.get("builtin") and not x.get("default")]

    def _write(self, own: list[dict], select: str):
        save_settings({"translate_skills": own})
        self._fill(select)

    def _new(self, name: str = "Kỹ năng mới", text: str = ""):
        sid = "u" + str(int(time.time() * 1000))
        own = self._own() + [{"id": sid, "name": name, "text": text or "- "}]
        self._write(own, sid)
        self.text.setFocus()

    def _copy(self):
        sk = self._cur()
        if sk:
            self._new(sk["name"] + " (của tôi)", sk["text"])

    def _load_file(self):
        path, _ = QFileDialog.getOpenFileName(self, "Chọn file kỹ năng", "", "Văn bản (*.txt *.md);;Tất cả (*.*)")
        if not path:
            return
        try:
            with open(path, encoding="utf-8", errors="replace") as f:
                txt = f.read().strip()
        except OSError as e:
            return QMessageBox.warning(self, "Không đọc được", str(e))
        import os

        self._new(os.path.splitext(os.path.basename(path))[0], txt[:20000])

    def _save(self):
        sk = self._cur()
        if not sk or sk.get("builtin"):
            return
        own = self._own()
        new = {"id": sk["id"], "name": self.name.text().strip() or "Kỹ năng của tôi",
               "text": self.text.toPlainText().strip()}
        if sk.get("default") and (new["name"], new["text"]) == (sk["name"], sk["text"].strip()):
            return self._fill(sk["id"])       # Dịch chuẩn chưa sửa gì: giữ bản mặc định
        for i, x in enumerate(own):
            if x["id"] == sk["id"]:
                own[i] = new
                break
        else:
            own.insert(0, new)                # lần đầu sửa kỹ năng Dịch chuẩn -> thành bản của bạn
        self._write(own, sk["id"])

    def _delete(self):
        sk = self._cur()
        if not sk or sk.get("builtin"):
            return
        if sk["id"] == skills.STD_ID:   # kỹ năng dịch chuẩn: "xoá" = đưa về bản mặc định
            if QMessageBox.question(self, "Về bản mặc định", "Đưa kỹ năng “Dịch chuẩn” về nội dung mặc định?") != \
                    QMessageBox.StandardButton.Yes:
                return
            return self._write([x for x in self._own() if x["id"] != sk["id"]], sk["id"])
        if QMessageBox.question(self, "Xoá kỹ năng", f"Xoá kỹ năng “{sk['name']}”?") != QMessageBox.StandardButton.Yes:
            return
        st = load_settings()
        if st.get("translate_skill") == sk["id"]:
            save_settings({"translate_skill": ""})
        self._write([x for x in self._own() if x["id"] != sk["id"]], "")

    def _use(self):
        sk = self._cur()
        if sk and not sk.get("builtin"):
            self._save()
        if sk:
            self.chosen = sk["id"]
            save_settings({"translate_skill": sk["id"]})
            self.accept()
