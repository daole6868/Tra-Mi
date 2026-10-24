"""Thẻ "Rút khoảng lặng & cắt bài hát" (tab Chỉnh sửa): chọn mức tìm, xem lại từng đoạn (tích / bỏ tích, ▶ xem chỗ
nối), phát thử bản đã cắt, áp dụng vào các đoạn cắt của video. Đoạn chờ cắt hiện sọc đỏ trên dòng Video của timeline
(bấm để bật / tắt, kéo 2 mép để chỉnh)."""
from __future__ import annotations

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QDoubleSpinBox, QHeaderView, QTableWidget,
                               QTableWidgetItem, QWidget)

from .. import silence as SL
from .common import run_async
from .widgets import (Form, GradientButton, SectionCard, SliderRow, Switch, button, combo, flow, hbox, label,
                      set_combo)


def _fmt(t: float) -> str:
    m, s = divmod(max(0.0, t), 60)
    h, m = divmod(int(m), 60)
    return (f"{h}:{m:02d}:{s:04.1f}" if h else f"{m:02d}:{s:04.1f}")


def _dur(t: float) -> str:
    m, s = divmod(max(0.0, t), 60)
    h, m = divmod(int(m), 60)
    return f"{h}:{m:02d}:{int(s):02d}" if h else f"{m:02d}:{int(s):02d}"


def _spin(lo: float, hi: float, step: float, val: float, suffix: str = " s", dec: int = 1) -> QDoubleSpinBox:
    sp = QDoubleSpinBox()
    sp.setRange(lo, hi)
    sp.setSingleStep(step)
    sp.setDecimals(dec)
    sp.setValue(val)
    sp.setSuffix(suffix)
    sp.setFixedWidth(92)
    return sp


class SilenceCard(SectionCard):
    itemsChanged = Signal(list)     # danh sách đoạn chờ cắt (timeline vẽ sọc đỏ, phát thử)
    previewToggled = Signal(bool)   # bật / tắt "Phát thử bản đã cắt"
    playJunction = Signal(dict)     # ▶ xem chỗ nối của 1 đoạn
    applyRequested = Signal(list)   # Áp dụng cắt: các đoạn đã tích

    def __init__(self, page):
        super().__init__("Rút khoảng lặng & cắt bài hát", "silence",
                         "Tìm chỗ không có lời / tiếng nhỏ dài hơn mức đặt rồi rút ngắn lại; hoặc tìm đúng đoạn bài hát "
                         "để cắt bỏ hết. Xem lại từng đoạn, bỏ tích đoạn muốn giữ, rồi bấm Áp dụng cắt.")
        self.page = page
        self.items: list[dict] = []
        self.songs_on = False         # đã bấm "Tìm / cắt đoạn bài hát"
        self.gaps_on = False          # đã bấm "Tìm khoảng lặng"
        self._off: set[str] = set()   # đoạn bạn đã bỏ tích (giữ khi đổi mức rồi tìm lại)
        self._db = None
        self._loading = False
        o = SL.options(page.settings)
        f = Form()
        self.form = f
        self.s_preset = f.row("Mức", combo(SL.PRESET_NAMES, SL.preset_of(o)))
        self.s_by = f.row("Tìm theo", combo(SL.BY, o.get("by", "speech")))
        self.s_db = f.row("Ngưỡng tiếng nhỏ", SliderRow(-60, -10, 1, float(o.get("db", -30)), 0, suffix=" dB"),
                          "Tiếng gốc nhỏ hơn mức này coi là lặng (−30 dB: lặng hẳn, −20 dB: cả tiếng động nhỏ)")
        self.s_min = _spin(0.5, 120, 0.5, float(o.get("min", 4)))
        self.s_max = _spin(0, 3600, 5, float(o.get("max", 60) or 0))
        self.s_max.setSpecialValueText("không giới hạn")
        f.row("Đoạn dài từ", hbox(self.s_min, label("đến", "muted"), self.s_max, None),
              "Chỉ tìm đoạn dài trong khoảng này. Đoạn dài hơn mức tối đa (vd. cảnh không lời có chủ ý) để nguyên")
        self.s_keep = _spin(0, 30, 0.5, float(o.get("keep", 1.5)))
        f.row("Rút còn", hbox(self.s_keep, label("(0 = xoá hẳn)", "muted"), None),
              "Đoạn lặng 10 s, rút còn 1,5 s -> cắt bỏ 8,5 s")
        self.s_where = f.row("Phần giữ lại ở", combo(SL.WHERE, o.get("where", "split")))
        self.s_songs = f.row("Không cắt đoạn 🎵", Switch(bool(o.get("skip_songs", True))),
                             "Bỏ qua khoảng lặng nằm trong đoạn bài hát (giữa các câu hát)")
        self.s_head = _spin(0, 600, 1, float(o.get("skip_head") or 0))
        self.s_tail = _spin(0, 600, 1, float(o.get("skip_tail") or 0))
        f.row("Không cắt", hbox(self.s_head, label("đầu video,", "muted"), self.s_tail, label("cuối video", "muted"),
                                None), "Giữ nguyên phần mở đầu / kết thúc (vd. intro, outro)")
        self.s_pad = _spin(0, 120, 1, float(o.get("song_pad", 15)))
        f.row("Bài hát: nới ra tối đa", hbox(self.s_pad, label("mỗi đầu (nhạc dạo / nhạc kết)", "muted"), None))
        self.add(f)
        self.add(flow(button("Tìm khoảng lặng", "search", on_click=lambda: self.scan(songs=False)),
                      button("Tìm / cắt đoạn bài hát", "music",
                             "Tìm các cụm câu 🎵 (lời bài hát) và cắt bỏ cả bài – nới ra tới sát lời thoại", on_click=
                             lambda: self.scan(songs=True)),
                      button("Xoá danh sách", "x", flat=True, on_click=self.clear)))
        self.status = label("", "muted", wrap=True)
        self.add(self.status)
        self.table = QTableWidget(0, 8)
        self.table.setHorizontalHeaderLabels(["", "#", "Loại", "Từ – Đến", "Dài", "Cắt bỏ", "Còn lại", ""])
        self.table.verticalHeader().hide()
        self.table.verticalHeader().setDefaultSectionSize(34)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        hh = self.table.horizontalHeader()
        for c, w in ((0, 34), (1, 40), (2, 128), (4, 60), (5, 64), (6, 64), (7, 84)):
            self.table.setColumnWidth(c, w)
        hh.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.table.setMinimumHeight(170)
        self.table.cellClicked.connect(self._cell)
        self.add(self.table)
        self.longer = _spin(0, 600, 1, 6)
        self.add(flow(button("Tích hết", "check", flat=True, on_click=lambda: self._tick_all(True)),
                      button("Bỏ hết", "x", flat=True, on_click=lambda: self._tick_all(False)),
                      hbox(button("Chỉ tích đoạn dài hơn", "fit", flat=True, on_click=self._tick_longer), self.longer)))
        self.summary = label("", "", wrap=True)
        self.add(self.summary)
        self.preview = Switch()
        self.preview.toggledOn.connect(self.previewToggled.emit)
        self.go = GradientButton("Áp dụng cắt", "scissors")
        self.go.clicked.connect(lambda: self.applyRequested.emit([it for it in self.items if it.get("on")]))
        self.add(flow(hbox(self.preview, label("Phát thử bản đã cắt (trình phát bỏ qua các đoạn đã tích)", "muted")),
                      self.go))
        self._rescan = QTimer(self, singleShot=True, interval=250, timeout=lambda: self.scan(quiet=True))
        self.s_preset.currentIndexChanged.connect(self._preset)
        self.s_by.currentIndexChanged.connect(lambda *_: (self._rows(), self._changed()))
        for w in (self.s_min, self.s_max, self.s_keep, self.s_head, self.s_tail, self.s_pad):
            w.valueChanged.connect(self._changed)
        self.s_db.changed.connect(self._changed)
        self.s_where.currentIndexChanged.connect(self._changed)
        self.s_songs.toggledOn.connect(self._changed)
        self._rows()
        self._fill()

    # ------------------------------------------------------------------ mức
    def opts(self) -> dict:
        return {"by": self.s_by.currentData(), "db": self.s_db.value(), "min": self.s_min.value(),
                "max": self.s_max.value(), "keep": self.s_keep.value(), "where": self.s_where.currentData(),
                "skip_songs": self.s_songs.isChecked(), "skip_head": self.s_head.value(),
                "skip_tail": self.s_tail.value(), "song_pad": self.s_pad.value()}

    def _rows(self):
        self.form.setRowVisible(self.s_db, self.s_by.currentData() != "speech")

    def _preset(self, *_):
        k = self.s_preset.currentData()
        if k in SL.PRESETS:
            self._loading = True
            self.s_min.setValue(SL.PRESETS[k]["min"])
            self.s_keep.setValue(SL.PRESETS[k]["keep"])
            self._loading = False
            self._changed()

    def _changed(self, *_):
        if self._loading:
            return
        self._loading = True
        set_combo(self.s_preset, SL.preset_of(self.opts()))
        self._loading = False
        try:
            from ..config import save_settings

            save_settings({"silence": self.opts()})
            self.page.settings["silence"] = self.opts()
        except Exception:  # noqa: BLE001
            pass
        if self.items:          # đổi mức: tìm lại ngay, giữ các đoạn bạn đã bỏ tích
            self._rescan.start()

    # ------------------------------------------------------------------ tìm
    def scan(self, songs: bool = False, quiet: bool = False):
        """songs: bấm "Tìm / cắt đoạn bài hát" (thêm các đoạn bài hát); không: "Tìm khoảng lặng". quiet: tìm lại theo
        mức mới (giữ các loại đã tìm + các đoạn bạn đã bỏ tích)."""
        p = self.page.p
        if not p:
            return
        if not quiet:
            if songs:
                self.songs_on = True
            else:
                self.gaps_on = True
        dur = self.page.duration()
        segs = self.page.model.segs
        o = self.opts()
        if songs and not quiet and not any(s.extra.get("song") for s in segs):
            from .. import pipeline

            if pipeline.stems(p):
                self.status.setText("Chưa có câu 🎵 nào – đang tự tìm câu hát trên giọng đã tách…")
                self.page._detect_songs(then=lambda n: self.scan(True) if n else self.status.setText(
                    "Không tìm thấy đoạn hát nào trên giọng đã tách."))
            else:
                self.status.setText("Chưa có câu nào đánh dấu 🎵. Cách đánh dấu: tab Lồng tiếng → 'Tự tìm câu hát' (cần "
                                    "tách giọng), tab Phụ đề → 'AI lọc lời bài hát', hoặc tích câu rồi 'Đặt 🎵'.")
            return
        gaps_on = getattr(self, "gaps_on", False)
        if gaps_on and o["by"] != "speech" and self._db is None:
            if SL.has_loudness(p.source, p.work):
                self._db = SL.loudness(p.source, p.work)
            else:
                self.status.setText("Đang đo âm lượng tiếng gốc (chỉ đo 1 lần, video dài mất ~ vài chục giây)…")
                src, work = p.source, p.work

                def done(db):
                    self._db = db
                    self.scan(songs, quiet=True)
                    self.status.setText(f"Tìm thấy {sum(1 for it in self.items if it['kind'] != 'song')} khoảng lặng")

                run_async(lambda: SL.loudness(src, work, duration=dur), done,
                          lambda m: self.status.setText("Không đo được âm lượng: " + m))
                return
        try:
            gaps = SL.find_gaps(segs, dur, o, self._db) if gaps_on else []
        except ValueError as e:
            self.status.setText(str(e))
            return
        song_items = SL.find_songs(segs, dur, o) if self.songs_on else []
        self.items = SL.merge_items(gaps, song_items)
        for it in self.items:
            it["on"] = it["id"] not in self._off
        if not quiet:
            n_s = sum(1 for it in self.items if it["kind"] == "song")
            self.status.setText((f"Tìm thấy {n_s} đoạn bài hát" if songs else
                                 f"Tìm thấy {len(self.items) - n_s} khoảng lặng") +
                                ("" if self.items else " – thử hạ mức 'Đoạn dài từ' hoặc chọn mức Mạnh"))
        self._fill()

    def clear(self):
        self.items, self._off, self.songs_on, self.gaps_on = [], set(), False, False
        self.status.setText("")
        self.preview.setChecked(False)
        self.previewToggled.emit(False)
        self._fill()

    # ------------------------------------------------------------------ bảng
    def _fill(self):
        t = self.table
        t.setRowCount(0)
        t.setRowCount(len(self.items))
        for r, it in enumerate(self.items):
            cb = QCheckBox()
            cb.setChecked(bool(it.get("on")))
            cb.toggled.connect(lambda on, r=r: self.set_on(r, on))
            box = QWidget()
            lay = hbox(cb, None)
            lay.setContentsMargins(8, 0, 0, 0)
            box.setLayout(lay)
            t.setCellWidget(r, 0, box)
            a, b = it["a"], it["b"]
            ca, cb_ = it["cut"]
            kind = SL.KINDS.get(it["kind"], it["kind"]) + (f" ({it['lines']} câu)" if it.get("lines") else "")
            vals = [str(r + 1), kind, f"{_fmt(a)} – {_fmt(b)}", f"{b - a:.1f} s", f"{cb_ - ca:.1f} s",
                    f"{(b - a) - (cb_ - ca):.1f} s"]
            for c, v in enumerate(vals, start=1):
                x = QTableWidgetItem(v)
                if c in (1, 4, 5, 6):
                    x.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                t.setItem(r, c, x)
            t.setCellWidget(r, 7, button("Xem", "play", "Phát từ 2 giây trước chỗ cắt tới 2 giây sau (bỏ qua đoạn cắt)",
                                         flat=True, on_click=lambda _=False, r=r: self.playJunction.emit(self.items[r])))
        self._summary()
        self.itemsChanged.emit(self.items)

    def _cell(self, r: int, c: int):
        if 0 <= r < len(self.items) and c != 0:
            self.page.player.seek(max(0.0, self.items[r]["cut"][0] - 1.0))

    def set_on(self, r: int, on: bool):
        if not 0 <= r < len(self.items):
            return
        it = self.items[r]
        it["on"] = on
        (self._off.discard if on else self._off.add)(it["id"])
        w = self.table.cellWidget(r, 0)
        cb = w.findChild(QCheckBox) if w else None
        if cb and cb.isChecked() != on:
            cb.blockSignals(True)
            cb.setChecked(on)
            cb.blockSignals(False)
        self._summary()
        self.itemsChanged.emit(self.items)

    def set_cut(self, r: int, ca: float, cb: float):
        """Kéo mép đoạn chờ cắt trên timeline."""
        if 0 <= r < len(self.items):
            it = self.items[r]
            it["cut"] = [round(max(0.0, ca), 3), round(max(ca + 0.1, cb), 3)]
            it["a"], it["b"] = min(it["a"], it["cut"][0]), max(it["b"], it["cut"][1])
            self._fill()

    def _tick_all(self, on: bool):
        for r in range(len(self.items)):
            self.items[r]["on"] = on
            (self._off.discard if on else self._off.add)(self.items[r]["id"])
        self._fill()

    def _tick_longer(self):
        lim = self.longer.value()
        for it in self.items:
            it["on"] = (it["b"] - it["a"]) > lim
            (self._off.discard if it["on"] else self._off.add)(it["id"])
        self._fill()

    def _summary(self):
        p = self.page.p
        if not p or not self.items:
            self.summary.setText("")
            self.go.setEnabled(False)
            return
        e = p.edit
        n, tot, before, after = SL.summary(self.items, self.page.duration(), e.get("trim"), e.get("cuts"))
        self.summary.setText(f"Đã chọn {n}/{tot} đoạn · video {_dur(before)} → {_dur(after)} "
                             f"(bớt {_dur(before - after)})")
        self.go.setEnabled(n > 0)

    def on_cuts(self) -> list[list[float]]:
        return [list(it["cut"]) for it in self.items if it.get("on")]
