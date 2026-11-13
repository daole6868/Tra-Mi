"""Từ cảm thán (2.4.20) – 2 hộp ở tab Lồng tiếng mục 3, khối "Từ cảm thán":

- `InterjWordsDialog`: bảng danh sách từ / cụm cảm thán cần lọc (thay ô 1 dòng dài trước đây), cột số câu đang có từ
  đó, Thêm (gõ nhiều từ cách nhau dấu phẩy) / Xoá / Về mặc định / Xoá hết, "Lưu & lọc…" = lưu rồi mở hộp xem trước.
- `InterjFilterDialog`: xem trước "Lọc từ" – bảng các câu sẽ bị xoá (chỉ có tiếng cảm thán) / bỏ vế cảm thán, tích câu
  muốn lọc rồi Áp dụng (bấm đúp 1 dòng = nghe câu đó)."""
from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QAbstractItemView, QDialog, QHeaderView, QLineEdit, QTableWidget, QTableWidgetItem,
                               QVBoxLayout)

from .. import pipeline, voicefix
from . import theme
from .widgets import button, hbox, label

WORDS_HINT = ("Chỉ lọc ĐÚNG các từ / cụm trong bảng, đứng riêng thành từ (không phân biệt hoa thường): \"á\" bắt \"Á!\", "
              "\"Á, đi thôi\" nhưng không đụng chữ á trong \"cách\", \"lá\". Cụm nhiều chữ (\"ui da\", \"ha ha\") phải khớp "
              "cả cụm. Câu CHỈ gồm các từ này bị xoá trước khi lồng tiếng; vế cảm thán trong câu dài (\"Ừm, đúng vậy.\") "
              "bị bỏ khi bật \"Bỏ vế cảm thán trong câu dài\". Cột Số câu = số câu dịch đang có từ đó.")


def words_text(items: list[str]) -> str:
    """Danh sách từ -> chuỗi lưu Cài đặt `interj_words` (cách nhau dấu phẩy, bỏ trùng, giữ thứ tự)."""
    out: list[str] = []
    seen = set()
    for w in items:
        w = " ".join(str(w).split()).strip(" ,;")
        k = w.lower()
        if w and k not in seen:
            seen.add(k)
            out.append(w)
    return ", ".join(out)


def split_words(text: str) -> list[str]:
    import re

    return [w for w in (" ".join(x.split()) for x in re.split(r"[,;\n]+", text or "")) if w]


class InterjWordsDialog(QDialog):
    """Bảng từ cảm thán cần lọc. Sau exec(): `saved` (bấm Lưu / Lưu & lọc), `then_filter` (Lưu & lọc), `text()`."""

    def __init__(self, parent=None, text: str = "", segs: list | None = None):
        super().__init__(parent)
        self.setWindowTitle("Từ cảm thán cần lọc")
        self.resize(560, 620)
        self.saved = False
        self.then_filter = False
        self._segs = list(segs or [])
        v = QVBoxLayout(self)
        v.setContentsMargins(20, 18, 20, 16)
        v.setSpacing(10)
        v.addWidget(label("Từ cảm thán cần lọc", "h3"))
        v.addWidget(label(WORDS_HINT, "muted", wrap=True))
        self.add_edit = QLineEdit()
        self.add_edit.setPlaceholderText("Gõ từ cần thêm – nhiều từ cách nhau dấu phẩy, VD: ê, úi chà, hihi")
        self.add_edit.returnPressed.connect(self._add_typed)
        v.addLayout(hbox(self.add_edit, button("Thêm", "plus", "Thêm các từ vừa gõ vào bảng", on_click=self._add_typed)))
        self.table = QTableWidget(0, 2)
        self.table.setHorizontalHeaderLabels(["Từ / cụm cảm thán", "Số câu"])
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.Stretch)
        hh.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSortingEnabled(False)
        self.table.itemChanged.connect(lambda *_: self._count_later())
        v.addWidget(self.table, 1)
        v.addLayout(hbox(
            button("Xoá dòng đã chọn", "trash", flat=True, on_click=self._del_rows),
            button("Về mặc định", "refresh", "Lấy lại danh sách có sẵn (a, á, à, ừm, hả, ha ha, ui da…)", flat=True,
                   on_click=lambda: self._set_words(split_words(voicefix.INTERJ_DEFAULT))),
            button("Xoá hết", "x", "Bảng trống = không lọc câu cảm thán nào", flat=True,
                   on_click=lambda: self._set_words([])),
            button("Sắp xếp", "list", "Xếp theo số câu nhiều nhất trước", flat=True, on_click=self._sort), None))
        self.note = label("", "muted", wrap=True)
        v.addWidget(self.note)
        filt = button("Lưu && lọc…", "wand", "Lưu danh sách rồi quét lại phụ đề: hiện bảng các câu sẽ bị lọc để bạn "
                      "xem / bỏ tích trước khi xoá", flat=True, on_click=lambda: self._save(True))
        filt.setEnabled(bool(self._segs))
        v.addLayout(hbox(None, filt, button("Lưu", "save", on_click=lambda: self._save(False)),
                         button("Đóng", "x", on_click=self.reject)))
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(250)
        self._timer.timeout.connect(self._count)
        self._set_words(split_words(text))

    # ---------------------------------------------------------------- bảng
    def _add_row(self, w: str):
        n = self.table.rowCount()
        self.table.insertRow(n)
        self.table.setItem(n, 0, QTableWidgetItem(w))
        it = QTableWidgetItem("")
        it.setFlags(it.flags() & ~Qt.ItemIsEditable)
        it.setTextAlignment(Qt.AlignCenter)
        self.table.setItem(n, 1, it)

    def _set_words(self, words: list[str]):
        self.table.blockSignals(True)
        self.table.setRowCount(0)
        for w in split_words(words_text(words)):
            self._add_row(w)
        self.table.blockSignals(False)
        self._count()

    def words(self) -> list[str]:
        out = []
        for i in range(self.table.rowCount()):
            it = self.table.item(i, 0)
            out.append(it.text() if it else "")
        return split_words(words_text(out))

    def text(self) -> str:
        return words_text(self.words())

    def _add_typed(self):
        new = split_words(self.add_edit.text())
        if not new:
            return
        have = {w.lower() for w in self.words()}
        add = [w for w in new if w.lower() not in have]
        self.table.blockSignals(True)
        for w in add:
            self._add_row(w)
        self.table.blockSignals(False)
        self.add_edit.clear()
        self._count()
        if add:
            self.table.scrollToBottom()
        self.note.setText((f"Đã thêm {len(add)} từ" if add else "Các từ này đã có trong bảng") + " – bấm Lưu để dùng.")

    def _del_rows(self):
        rows = sorted({ix.row() for ix in self.table.selectedIndexes()}, reverse=True)
        self.table.blockSignals(True)
        for r in rows:
            self.table.removeRow(r)
        self.table.blockSignals(False)
        self._count()

    def _sort(self):
        ws = self.words()
        counts = self._counts(ws)
        self._set_words(sorted(ws, key=lambda w: (-counts.get(w.lower(), 0), w.lower())))

    # ---------------------------------------------------------------- đếm câu
    def _count_later(self):
        self._timer.start()

    def _counts(self, ws: list[str]) -> dict[str, int]:
        ph = {w.lower(): (voicefix.interj_list({"interj_words": w}) or [()])[0] for w in ws}
        got = pipeline.interj_counts(self._segs, [p for p in ph.values() if p])
        return {k: got.get(p, 0) for k, p in ph.items()}

    def _count(self):
        ws = [(self.table.item(i, 0).text() if self.table.item(i, 0) else "") for i in range(self.table.rowCount())]
        counts = self._counts([w for w in ws if w.strip()]) if self._segs else {}
        self.table.blockSignals(True)
        for i, w in enumerate(ws):
            it = self.table.item(i, 1)
            if it is None:
                continue
            n = counts.get(" ".join(w.split()).lower())
            it.setText("" if n is None else str(n))
            it.setForeground(QColor(theme.TEXT if n else theme.MUTED))
        self.table.blockSignals(False)
        total = len(self.words())
        used = sum(1 for n in counts.values() if n)
        self.note.setText(f"{total} từ / cụm" + (f" · {used} từ đang có trong phụ đề" if self._segs else "") +
                          ("" if total else " – bảng trống: không lọc câu cảm thán nào"))

    def _save(self, then_filter: bool):
        self.saved = True
        self.then_filter = then_filter
        self.accept()


KIND_TEXT = {"drop": "Xoá cả câu", "trim": "Bỏ vế cảm thán"}


class InterjFilterDialog(QDialog):
    """Xem trước Lọc từ cảm thán. items = pipeline.interj_scan(...); segs = danh sách câu (giờ, chữ gốc).
    Sau exec(): `chosen()` -> (câu xoá cả câu, câu bỏ vế) đã tích."""

    def __init__(self, parent=None, items: list[dict] | None = None, segs: list | None = None, inline: bool = True,
                 play=None):
        super().__init__(parent)
        self.setWindowTitle("Lọc từ cảm thán")
        self.resize(980, 640)
        self.items = list(items or [])
        self._play = play
        segs = segs or []
        v = QVBoxLayout(self)
        v.setContentsMargins(20, 18, 20, 16)
        v.setSpacing(10)
        v.addWidget(label("Lọc từ cảm thán – xem trước", "h3"))
        nd = sum(1 for x in self.items if x["kind"] == "drop")
        nt = len(self.items) - nd
        v.addWidget(label(f"Tìm thấy {len(self.items)} câu có từ trong danh sách đứng riêng: {nd} câu chỉ có tiếng "
                          f"cảm thán (xoá cả câu – phụ đề lẫn giọng lồng), {nt} câu bỏ vế cảm thán (giữ phần lời "
                          "thật, đọc lại giọng câu đó). Bỏ tích câu muốn giữ rồi bấm Áp dụng. Bấm đúp 1 dòng = nghe "
                          "câu đó. Lọc xong vẫn lấy lại được bằng 'Khôi phục câu cảm thán đã xoá' hoặc Ctrl+Z.",
                          "muted", wrap=True))
        self.table = QTableWidget(len(self.items), 6)
        self.table.setHorizontalHeaderLabels(["", "#", "Giờ", "Bản dịch hiện tại", "Sau khi lọc", "Việc"])
        hh = self.table.horizontalHeader()
        for c, mode in enumerate((QHeaderView.ResizeToContents, QHeaderView.ResizeToContents,
                                  QHeaderView.ResizeToContents, QHeaderView.Stretch, QHeaderView.Stretch,
                                  QHeaderView.ResizeToContents)):
            hh.setSectionResizeMode(c, mode)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setWordWrap(True)
        muted = QColor(theme.MUTED)
        for r, x in enumerate(self.items):
            ck = QTableWidgetItem()
            ck.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled | Qt.ItemIsSelectable)
            on = not x.get("kept") and (x["kind"] == "drop" or inline)
            ck.setCheckState(Qt.Checked if on else Qt.Unchecked)
            self.table.setItem(r, 0, ck)
            row = x["row"]
            s = segs[row] if 0 <= row < len(segs) else None
            self.table.setItem(r, 1, QTableWidgetItem(str(row + 1)))
            self.table.setItem(r, 2, QTableWidgetItem(pipeline.fmt_time(s.start) if s is not None else ""))
            old = QTableWidgetItem(x["old"])
            if s is not None and s.text:
                old.setToolTip(f"Câu gốc: {s.text}")
            self.table.setItem(r, 3, old)
            new = QTableWidgetItem(x["new"] or "(xoá cả câu)")
            if not x["new"]:
                new.setForeground(muted)
            self.table.setItem(r, 4, new)
            kind = KIND_TEXT.get(x["kind"], x["kind"]) + (" · đã khôi phục trước đây" if x.get("kept") else "")
            k = QTableWidgetItem(kind)
            if x.get("kept"):
                k.setToolTip("Câu này bạn đã bấm Khôi phục trước đây – lồng tiếng tự động không lọc lại; tích nếu "
                             "muốn lọc lần này")
            self.table.setItem(r, 5, k)
        self.table.resizeRowsToContents()
        self.table.itemChanged.connect(lambda *_: self._update())
        self.table.cellDoubleClicked.connect(self._listen)
        v.addWidget(self.table, 1)
        v.addLayout(hbox(
            button("Tích hết", "check", flat=True, on_click=lambda: self._tick(lambda x: True)),
            button("Bỏ hết", "x", flat=True, on_click=lambda: self._tick(lambda x: False)),
            button("Chỉ câu xoá cả câu", "trash", "Chỉ tích câu chỉ có tiếng cảm thán", flat=True,
                   on_click=lambda: self._tick(lambda x: x["kind"] == "drop")),
            button("Chỉ câu bỏ vế", "wand", "Chỉ tích câu dài có vế cảm thán", flat=True,
                   on_click=lambda: self._tick(lambda x: x["kind"] == "trim")), None))
        self.b_apply = button("Áp dụng", "check", on_click=self.accept)
        v.addLayout(hbox(None, self.b_apply, button("Huỷ", "x", on_click=self.reject)))
        self._update()

    def _tick(self, pred):
        self.table.blockSignals(True)
        for r, x in enumerate(self.items):
            self.table.item(r, 0).setCheckState(Qt.Checked if pred(x) else Qt.Unchecked)
        self.table.blockSignals(False)
        self._update()

    def _checked(self) -> list[dict]:
        return [x for r, x in enumerate(self.items) if self.table.item(r, 0).checkState() == Qt.Checked]

    def chosen(self) -> tuple[list[int], list[int]]:
        ch = self._checked()
        return ([x["row"] for x in ch if x["kind"] == "drop"], [x["row"] for x in ch if x["kind"] == "trim"])

    def _update(self):
        d, t = self.chosen()
        n = len(d) + len(t)
        self.b_apply.setText(f"Áp dụng {n} câu" if n else "Áp dụng")
        self.b_apply.setEnabled(bool(n))

    def _listen(self, r: int, _c: int = 0):
        if self._play and 0 <= r < len(self.items):
            self._play(self.items[r]["row"])
