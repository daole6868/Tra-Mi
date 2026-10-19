"""Khung "Câu đang chọn" bên phải (kiểu bảng thuộc tính của trình dựng phim): sửa giờ, người nói, lời Trung / Việt,
giọng riêng, bỏ lồng của 1 câu; nút Nghe câu, Dịch lại, AI rút gọn, Tạo lại giọng câu này.
Mọi chỉnh sửa đi qua SubsModel (cùng bảng ở tab Phụ đề / Lồng tiếng) nên tự lưu và đồng bộ."""
from __future__ import annotations

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import QComboBox, QLineEdit, QPlainTextEdit, QVBoxLayout, QWidget

from .player import fmt
from .subs_model import (C_END, C_NODUB, C_SPK, C_START, C_VI, C_VOICE, C_ZH, FIT_TEXT, VOICE_DEFAULT, SubsModel,
                         spk_color)
from .widgets import Card, GradientButton, IconButton, Switch, button, hbox, label


class LineInspector(Card):
    action = Signal(str, int)     # (play | retranslate | shorten | redub, câu)
    navigate = Signal(int)        # chuyển sang câu khác

    def __init__(self, model: SubsModel, voices_fn, parent=None):
        super().__init__(parent, padding=14)
        self.model = model
        self.voices_fn = voices_fn
        self.row = -1
        self._filling = False
        self.title = label("Câu đang chọn", "h3")
        self.prev = IconButton("chev_left", "Câu trước", 28)
        self.next = IconButton("chev_right", "Câu sau", 28)
        self.prev.clicked.connect(lambda: self.navigate.emit(self.row - 1))
        self.next.clicked.connect(lambda: self.navigate.emit(self.row + 1))
        self.add(hbox(self.title, None, self.prev, self.next))
        self.empty = label("Bấm 1 câu trên timeline (dưới) hoặc trong bảng câu để xem và sửa ở đây.", "muted", wrap=True)
        self.add(self.empty)

        self.body = QWidget()
        v = QVBoxLayout(self.body)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(6)
        self.meta = label("", "muted")
        self.start = QLineEdit()
        self.end = QLineEdit()
        self.spk = QLineEdit()
        self.spk.setMaximumWidth(64)
        for w in (self.start, self.end):
            w.setMaximumWidth(120)
        self.start.editingFinished.connect(lambda: self._set(C_START, self.start.text()))
        self.end.editingFinished.connect(lambda: self._set(C_END, self.end.text()))
        self.spk.editingFinished.connect(lambda: self._set(C_SPK, self.spk.text()))
        v.addWidget(self.meta)
        v.addLayout(hbox(label("Từ", "muted"), self.start, label("đến", "muted"), self.end, None))
        v.addLayout(hbox(label("Người nói", "muted"), self.spk, None))
        v.addWidget(label("Tiếng Trung (gốc)", "muted"))
        self.zh = QPlainTextEdit()
        self.zh.setFixedHeight(58)
        v.addWidget(self.zh)
        v.addWidget(label("Tiếng Việt (lời đọc + phụ đề)", "muted"))
        self.vi = QPlainTextEdit()
        self.vi.setMinimumHeight(84)
        v.addWidget(self.vi, 1)
        self._vi_timer = QTimer(self, singleShot=True, interval=700, timeout=lambda: self._set(C_VI, self.vi.toPlainText()))
        self._zh_timer = QTimer(self, singleShot=True, interval=700, timeout=lambda: self._set(C_ZH, self.zh.toPlainText()))
        self.vi.textChanged.connect(lambda: None if self._filling else self._vi_timer.start())
        self.zh.textChanged.connect(lambda: None if self._filling else self._zh_timer.start())
        self.voice = QComboBox()
        self.voice.setMinimumContentsLength(12)
        self.voice.activated.connect(lambda _i: self._set(C_VOICE, self.voice.currentData() or ""))
        v.addLayout(hbox(label("Giọng", "muted"), self.voice))
        self.nodub = Switch(False)
        self.nodub.toggledOn.connect(self._set_nodub)
        self.fit = label("", "muted", wrap=True)
        v.addLayout(hbox(label("Không lồng câu này", "muted"), self.nodub, None))
        v.addWidget(self.fit)
        v.addLayout(hbox(button("Nghe câu", "play", "Phát câu này trên khung xem trước",
                                on_click=lambda: self._act("play")),
                         button("Dịch lại", "globe", "Dịch lại riêng câu này", on_click=lambda: self._act("retranslate")),
                         button("AI rút gọn", "sparkle", "AI viết lại ngắn hơn cho vừa thời gian nói",
                                on_click=lambda: self._act("shorten"))))
        regen = GradientButton("Tạo lại giọng câu này", "refresh")
        regen.clicked.connect(lambda: self._act("redub"))
        v.addWidget(regen)
        self.add(self.body)
        self.lay.addStretch(1)
        self.body.hide()
        model.dataChanged.connect(self._model_changed)
        model.modelReset.connect(lambda: self.set_row(self.row if self.row < len(self.model.segs) else -1))

    # ------------------------------------------------------------------
    def _act(self, name: str):
        self._flush()
        if 0 <= self.row < len(self.model.segs):
            self.action.emit(name, self.row)

    def _flush(self):
        for t in (self._vi_timer, self._zh_timer):
            if t.isActive():
                t.stop()
                t.timeout.emit()

    def _set(self, col: int, value: str):
        if self._filling or not (0 <= self.row < len(self.model.segs)):
            return
        if str(self.model.index(self.row, col).data(Qt.ItemDataRole.EditRole) or "") == value.strip():
            return
        self.model.setData(self.model.index(self.row, col), value)

    def _set_nodub(self, on: bool):
        if not self._filling and 0 <= self.row < len(self.model.segs):
            st = Qt.CheckState.Checked if on else Qt.CheckState.Unchecked
            self.model.setData(self.model.index(self.row, C_NODUB), st.value, Qt.ItemDataRole.CheckStateRole)

    def _model_changed(self, a, b, roles=None):
        if a.row() <= self.row <= b.row() and not (self.vi.hasFocus() or self.zh.hasFocus()):
            self.set_row(self.row)

    def refresh_voices(self):
        cur = self.voice.currentData()
        self.voice.clear()
        self.voice.addItem(VOICE_DEFAULT, "")
        for vid, name in self.voices_fn():
            self.voice.addItem(name, vid)
        i = self.voice.findData(cur or "")
        self.voice.setCurrentIndex(max(0, i))

    def set_row(self, row: int):
        if row != self.row:
            self._flush()
        segs = self.model.segs
        self.row = row if 0 <= row < len(segs) else -1
        self.body.setVisible(self.row >= 0)
        self.empty.setVisible(self.row < 0)
        self.prev.setEnabled(self.row > 0)
        self.next.setEnabled(0 <= self.row < len(segs) - 1)
        if self.row < 0:
            self.title.setText("Câu đang chọn")
            return
        s = segs[self.row]
        self._filling = True
        try:
            self.title.setText(f"Câu #{self.row + 1}")
            self.title.setStyleSheet(f"color: {spk_color(s.speaker)};" if s.speaker else "")
            self.meta.setText(f"{s.duration:.2f} giây · {len(s.trans)} ký tự Việt")
            for w, val in ((self.start, fmt(s.start)), (self.end, fmt(s.end)), (self.spk, s.speaker or "")):
                if not w.hasFocus():
                    w.setText(val)
            if not self.zh.hasFocus():
                self.zh.setPlainText(s.text)
            if not self.vi.hasFocus():
                self.vi.setPlainText(s.trans)
            if self.voice.count() <= 1:
                self.refresh_voices()
            self.voice.setCurrentIndex(max(0, self.voice.findData(s.extra.get("voice", ""))))
            self.nodub.setChecked(bool(s.extra.get("nodub")))
            fit = s.extra.get("fit")
            tip = {"ok": "giọng lồng vừa khít", "fast": f"đọc nhanh ×{s.extra.get('fit_speed', 1):.2f} cho vừa",
                   "long": "quá dài so với thời gian nói – sửa ngắn lại hoặc bấm AI rút gọn"}.get(fit, "chưa lồng tiếng")
            shift = float(s.extra.get("dub_shift", 0) or 0)
            self.fit.setText(f"Độ vừa: {FIT_TEXT.get(fit, '–')} {tip}"
                             + (f" · giọng đã dời {shift:+.2f} s (chuột phải đoạn giọng trên timeline để đặt lại)"
                                if shift else ""))
        finally:
            self._filling = False

