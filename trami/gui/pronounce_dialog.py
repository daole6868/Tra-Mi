"""Hộp "Từ điển đọc" (2.4.16): bảng Từ / Cách đọc / Phân biệt hoa thường, nhập / xuất CSV, thử 1 câu xem bộ đọc nhận
chữ gì. Lưu ở Cài đặt `tts_dict` (trami/pronounce.py) – phụ đề không đổi, chỉ chữ đưa cho bộ đọc giọng."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QDialog, QFileDialog, QHeaderView, QLineEdit,
                               QMessageBox, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget, QHBoxLayout)

from .. import pronounce
from ..config import load_settings, save_settings
from .widgets import button, hbox, label

HINT = ("Chữ bên trái được thay bằng cách đọc bên phải TRƯỚC khi đưa cho bộ đọc giọng (Edge, VieNeu, OmniVoice, "
        "VoxCPM2, KorvaTTS) – phụ đề, SRT, karaoke vẫn hiện chữ gốc. Dùng cho chữ bộ đọc đọc sai: viết tắt (KTX -> ký "
        "túc xá), tên riêng, tiếng cảm thán (Hm -> hừm, Mm -> ừm). Chỉ khớp NGUYÊN TỪ; không tích 'Hoa thường' = khớp mọi "
        "kiểu viết hoa. Từ chỉ có nguyên âm + h / m khớp cả bản kéo dài (Hmmmm = Hmm). Có sẵn: tiếng cười viết liền "
        "(Hahaha, Hehe, Hihi) tự đọc thành \"ha ha ha\". Sửa xong bấm Lưu, rồi tạo lại giọng các câu bị đổi.")


class PronounceDialog(QDialog):
    def __init__(self, parent=None, preview=None):
        """preview(text) – nghe thử câu bằng giọng đang chọn (None = không có nút nghe)."""
        super().__init__(parent)
        self.setWindowTitle("Từ điển đọc")
        self.resize(780, 600)
        self.saved = False
        self._preview = preview
        st = load_settings()
        v = QVBoxLayout(self)
        v.setContentsMargins(20, 18, 20, 16)
        v.setSpacing(10)
        v.addWidget(label("Từ điển đọc", "h3"))
        v.addWidget(label(HINT, "muted", wrap=True))
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["Chữ trong bản dịch", "Bộ đọc đọc thành", "Hoa thường"])
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.Stretch)
        hh.setSectionResizeMode(1, QHeaderView.Stretch)
        hh.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.itemChanged.connect(lambda *_: self._try_later())
        v.addWidget(self.table, 1)
        for e in pronounce.entries(st):
            self._add_row(e["w"], e["r"], e["c"])
        v.addLayout(hbox(
            button("Thêm dòng", "plus", on_click=lambda: self._add_row("", "", True, edit=True)),
            button("Xoá dòng đã chọn", "trash", flat=True, on_click=self._del_rows),
            button("Nhập CSV…", "folder", "File CSV: cột từ, cách đọc, phân biệt hoa thường (true / false) – hoặc mỗi "
                   "dòng 'từ=cách đọc'. Dòng trùng với bảng thì thay", flat=True, on_click=self._import),
            button("Xuất CSV…", "download", flat=True, on_click=self._export),
            button("Về mặc định", "refresh", "Bỏ hết mục đã sửa, lấy lại bộ có sẵn (Hm, Hmm, Mm, Ừmm, Aaa)", flat=True,
                   on_click=self._reset), None))
        self.test = QLineEdit()
        self.test.setPlaceholderText("Thử 1 câu, VD: Hmmm… Hahaha, về KTX thôi!")
        self.test.textChanged.connect(lambda *_: self._try_later())
        self.result = label("", "muted", wrap=True)
        self.result.setTextInteractionFlags(Qt.TextSelectableByMouse)
        row = [self.test]
        if preview:
            row.append(button("Nghe thử", "play", "Đọc câu thử (đã thay theo bảng) bằng giọng đang chọn ở tab Lồng tiếng",
                              flat=True, on_click=self._listen))
        v.addWidget(label("Thử", "muted"))
        v.addLayout(hbox(*row))
        v.addWidget(self.result)
        self.b_save = button("Lưu", "save", on_click=self._save)
        v.addLayout(hbox(None, self.b_save, button("Đóng", "x", on_click=self.reject)))
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(250)
        self._timer.timeout.connect(self._try)

    # ---------------------------------------------------------------- bảng
    def _add_row(self, w: str, r: str, c: bool, edit: bool = False):
        self.table.blockSignals(True)
        n = self.table.rowCount()
        self.table.insertRow(n)
        self.table.setItem(n, 0, QTableWidgetItem(w))
        self.table.setItem(n, 1, QTableWidgetItem(r))
        box = QWidget()
        lay = QHBoxLayout(box)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setAlignment(Qt.AlignCenter)
        cb = QCheckBox()
        cb.setChecked(bool(c))
        cb.setToolTip("Tích = chỉ khớp đúng kiểu viết hoa / thường (\"AI\" không đụng \"ai\")")
        cb.toggled.connect(lambda *_: self._try_later())
        lay.addWidget(cb)
        self.table.setCellWidget(n, 2, box)
        self.table.blockSignals(False)
        if edit:
            self.table.setCurrentCell(n, 0)
            self.table.editItem(self.table.item(n, 0))

    def _case(self, row: int) -> bool:
        w = self.table.cellWidget(row, 2)
        cb = w.findChild(QCheckBox) if w else None
        return bool(cb.isChecked()) if cb else True

    def items(self) -> list[dict]:
        out = []
        for i in range(self.table.rowCount()):
            w = (self.table.item(i, 0).text() if self.table.item(i, 0) else "").strip()
            r = (self.table.item(i, 1).text() if self.table.item(i, 1) else "").strip()
            if w and r:
                out.append({"w": w, "r": r, "c": self._case(i)})
        return pronounce.validate(out)

    def _set_items(self, items: list[dict]):
        self.table.setRowCount(0)
        for e in items:
            self._add_row(e["w"], e["r"], e["c"])
        self._try_later()

    def _del_rows(self):
        rows = sorted({ix.row() for ix in self.table.selectedIndexes()}, reverse=True)
        for r in rows:
            self.table.removeRow(r)
        self._try_later()

    def _import(self):
        fn, _ = QFileDialog.getOpenFileName(self, "Nhập từ điển đọc", "", "CSV / văn bản (*.csv *.txt *.tsv)")
        if not fn:
            return
        try:
            raw = Path(fn).read_bytes()
            try:
                txt = raw.decode("utf-8-sig")
            except UnicodeDecodeError:
                txt = raw.decode("cp1258", errors="replace")
            new = pronounce.from_csv(txt)
        except OSError as e:
            QMessageBox.warning(self, "Từ điển đọc", f"Không đọc được file: {e}")
            return
        if not new:
            QMessageBox.information(self, "Từ điển đọc", "File không có dòng nào dạng 'từ, cách đọc'.")
            return
        cur = self.items()
        keys = {(e["c"], e["w"] if e["c"] else e["w"].lower()) for e in new}
        merged = [e for e in cur if (e["c"], e["w"] if e["c"] else e["w"].lower()) not in keys] + new
        self._set_items(pronounce.validate(merged))
        self.result.setText(f"Đã nhập {len(new)} mục – bấm Lưu để dùng.")

    def _export(self):
        fn, _ = QFileDialog.getSaveFileName(self, "Xuất từ điển đọc", "tu_dien_doc.csv", "CSV (*.csv)")
        if not fn:
            return
        try:
            Path(fn).write_text(pronounce.to_csv(self.items()), encoding="utf-8-sig")
        except OSError as e:
            QMessageBox.warning(self, "Từ điển đọc", f"Không ghi được file: {e}")

    def _reset(self):
        self._set_items(pronounce.validate(pronounce.DEFAULT_ENTRIES))

    # ---------------------------------------------------------------- thử
    def _try_later(self):
        self._timer.start()

    def _converted(self) -> str:
        t = self.test.text().strip()
        return pronounce.Dict(self.items()).apply(t, "vi") if t else ""

    def _try(self):
        t = self.test.text().strip()
        if not t:
            self.result.setText(f"{len(self.items())} mục.")
            return
        out = self._converted()
        self.result.setText(f"Bộ đọc nhận: “{out}”" + ("" if out != t else "  (không đổi gì)"))

    def _listen(self):
        t = self._converted()
        if t and self._preview:
            self._preview(t)

    def _save(self):
        items = self.items()
        default = pronounce.validate(pronounce.DEFAULT_ENTRIES)
        save_settings({"tts_dict": None if items == default else items})
        self.saved = True
        self.accept()
