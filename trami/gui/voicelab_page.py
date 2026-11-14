"""Trang Tạo giọng (2.4.22, đợt 1 – theo bản thiết kế người dùng đã duyệt): 3 cột

  Giọng của bạn (thư viện "my:<id>")  |  Công thức giọng (1 Nguồn giọng – bộ đọc + giọng nền + thông số bộ đọc;
  2 Chỉnh sau khi đọc – cao độ, âm sắc, tốc độ, ấm, rõ chữ, sáng, nén, vang phòng, màu đặc biệt; Mẫu nhanh)  |  Nghe thử
  (ô tự gõ, câu mẫu, cảm xúc VoxCPM2, sóng âm + tiếng / giây, các bản vừa nghe, Lưu thành giọng mới / Lưu đè).

Đổi phần "Chỉnh sau khi đọc" khi câu thử đã đọc -> áp lại ngay lên bản đã đọc rồi phát (không đọc lại). Đổi nguồn giọng
-> bấm Nghe thử để bộ đọc đọc lại. Lõi ở trami/voicelab.py.
"""
from __future__ import annotations

import copy
from pathlib import Path

from PySide6.QtCore import QRectF, Qt, QTimer, QUrl
from PySide6.QtGui import QColor, QFont, QPainter
from PySide6.QtWidgets import (QButtonGroup, QCheckBox, QComboBox, QDialog, QFileDialog, QFrame, QHBoxLayout,
                               QLineEdit, QListWidget, QListWidgetItem, QMessageBox, QPlainTextEdit, QPushButton,
                               QSpinBox, QVBoxLayout, QWidget)

from .. import voicelab as VL
from ..config import load_settings, save_settings
from ..jobs import manager
from . import theme
from .common import run_async
from .widgets import (Card, GradientButton, Group, IconButton, PillTabs, SliderRow, button, combo, flow, label,
                      set_combo)

ENGINE_ICONS = {"edge": "globe", "vieneu": "mic", "omni": "users", "vox": "sparkle", "korva": "cpu"}
EMOS = [("", "Bình thường"), ("vui, hào hứng", "Vui, phấn khích"), ("giận dữ, quát to", "Giận dữ, quát to"),
        ("buồn, khóc", "Buồn, nghẹn ngào"), ("sợ hãi", "Sợ hãi"), ("lạnh lùng", "Lạnh lùng")]
HISTORY_MAX = 6


class Wave(QWidget):
    """Sóng âm câu thử (đỉnh từng ô) + vạch đang phát."""

    def __init__(self):
        super().__init__()
        self.peaks: list[float] = []
        self.pos = 0.0
        self.setFixedHeight(64)

    def set(self, peaks: list[float]):
        self.peaks = list(peaks or [])
        self.pos = 0.0
        self.update()

    def set_pos(self, f: float):
        self.pos = max(0.0, min(1.0, f))
        self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(theme.CARD2))
        p.drawRoundedRect(r, 10, 10)
        if not self.peaks:
            p.setPen(QColor(theme.MUTED))
            p.drawText(r, Qt.AlignmentFlag.AlignCenter, "Bấm Nghe thử để nghe giọng")
            return
        n = len(self.peaks)
        w = (r.width() - 16) / n
        mid = r.center().y()
        for k, v in enumerate(self.peaks):
            h = max(2.0, v * (r.height() - 16))
            x = r.left() + 8 + k * w
            on = (k + 0.5) / n <= self.pos
            p.setBrush(QColor(theme.ACCENT2 if on else "#4A5468"))
            p.drawRoundedRect(QRectF(x + w * 0.15, mid - h / 2, max(1.0, w * 0.7), h), 1.5, 1.5)


class SaveDialog(QDialog):
    """Lưu thành giọng mới: tên, nam / nữ, hợp với, tốc độ đọc (đo bằng 3 câu chuẩn), dùng làm mặc định."""

    def __init__(self, parent, rec: dict, settings: dict):
        super().__init__(parent)
        self.setWindowTitle("Lưu thành giọng mới")
        self.setMinimumWidth(480)
        self.rec = copy.deepcopy(rec)
        self.settings = settings
        v = QVBoxLayout(self)
        v.setSpacing(12)
        v.addWidget(label("Lưu thành giọng mới", "h2"))
        v.addWidget(label("Tên giọng", "muted"))
        self.name = QLineEdit(rec.get("name") or "")
        self.name.setPlaceholderText("VD: Thuyết minh nhanh – Ngọc sáng")
        v.addWidget(self.name)
        v.addWidget(label("Nam / nữ – để \"Tự gán giọng nam / nữ\" chọn đúng người nói", "muted", wrap=True))
        self.gender = QButtonGroup(self)
        self.setStyleSheet(f"QPushButton:checked {{ border: 1px solid {theme.ACCENT2}; background: #2E2320; "
                           f"color: #FFB07A; }}")
        row = QHBoxLayout()
        for k, (gid, txt) in enumerate((("female", "Nữ"), ("male", "Nam"), ("", "Không rõ"))):
            b = QPushButton(txt)
            b.setCheckable(True)
            b.setProperty("flat", True)
            b.setChecked(gid == (rec.get("gender") or ""))
            self.gender.addButton(b, k)
            b._gid = gid
            row.addWidget(b)
        row.addStretch(1)
        v.addLayout(row)
        v.addWidget(label("Hợp với (hiện khi rê chuột vào giọng)", "muted"))
        self.use = QLineEdit(rec.get("use") or "")
        self.use.setPlaceholderText("VD: Thuyết minh, review phim nhịp nhanh")
        v.addWidget(self.use)
        self.rate_lb = label("Tốc độ đọc: đang đo (đọc 3 câu chuẩn)…", wrap=True)
        self.rate_lb.setToolTip("Đo khi lưu (đọc 3 câu chuẩn) – biết giọng đọc nhanh hay chậm khi chọn giọng cho phim")
        v.addWidget(self.rate_lb)
        self.default = QCheckBox("Dùng làm giọng mặc định cho video mới")
        v.addWidget(self.default)
        btns = QHBoxLayout()
        btns.addStretch(1)
        cancel = button("Huỷ", on_click=self.reject)
        self.ok = GradientButton("Lưu", "save")
        self.ok.clicked.connect(self._ok)
        btns.addWidget(cancel)
        btns.addWidget(self.ok)
        v.addLayout(btns)
        self._alive = True
        self.finished.connect(lambda *_a: setattr(self, "_alive", False))
        if rec.get("rate"):
            self._rate_done(rec["rate"])
        else:
            run_async(lambda: VL.measure_rate(self.rec, self.settings), self._rate_done, self._rate_err)

    def _rate_done(self, r):
        if not self._alive:
            return
        self.rec["rate"] = r or None
        self.rate_lb.setText(f"✓ Tốc độ đọc: {r:.1f} tiếng/giây".replace(".", ",") if r else "Tốc độ đọc: chưa đo được")

    def _rate_err(self, m):
        if self._alive:
            self.rate_lb.setText("Tốc độ đọc: chưa đo được (" + m[:120] + ") – vẫn lưu được")

    def _ok(self):
        if not self.name.text().strip():
            self.name.setFocus()
            return
        b = self.gender.checkedButton()
        self.rec["name"] = self.name.text().strip()
        self.rec["gender"] = getattr(b, "_gid", "") if b else ""
        self.rec["use"] = self.use.text().strip()
        self.accept()


class VoiceLabPage(QWidget):
    def __init__(self, win):
        super().__init__()
        self.win = win
        self.cur_id = ""                       # giọng đang sửa ("" = giọng mới, chưa lưu)
        self.rec = VL.new_record()
        self.history: list[dict] = []
        self._loading = False
        self._busy = False
        self._last: dict | None = None         # lần nghe gần nhất: {path, text, src, emo}
        self._player = None
        self._fx_timer = QTimer(self, singleShot=True, interval=350, timeout=self._fx_changed_now)
        self._pos_timer = QTimer(self, interval=60, timeout=self._tick)

        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 20)
        root.setSpacing(14)
        head = QHBoxLayout()
        tb = QVBoxLayout()
        tb.setSpacing(2)
        tb.addWidget(label("Tạo giọng", "h1"))
        tb.addWidget(label("Chọn bộ đọc + giọng nền, chỉnh cao độ / âm sắc / tốc độ / màu giọng, gõ câu bất kỳ để nghe "
                           "thử. Ưng thì lưu thành giọng của bạn – dùng cho mọi video, mọi người nói.", "muted", wrap=True))
        head.addLayout(tb, 1)
        self.b_import = button("Nhập giọng…", "upload", flat=True, on_click=self._import)
        self.b_export = button("Xuất giọng…", "download", flat=True, on_click=self._export)
        head.addWidget(self.b_import, 0, Qt.AlignmentFlag.AlignTop)
        head.addWidget(self.b_export, 0, Qt.AlignmentFlag.AlignTop)
        root.addLayout(head)
        body = QHBoxLayout()
        body.setSpacing(theme.GAP + 4)
        body.addWidget(self._lib_col())
        body.addWidget(self._recipe_col(), 1)
        body.addWidget(self._test_col())
        root.addLayout(body, 1)
        self._load_rec(self.rec)
        self.refresh()

    # ------------------------------------------------------------------ cột 1: thư viện
    def _lib_col(self) -> QWidget:
        c = Card(padding=14)
        c.setFixedWidth(300)
        hl = QHBoxLayout()
        hl.addWidget(label("Giọng của bạn", "h3"))
        hl.addStretch(1)
        self.lib_count = label("", "muted")
        hl.addWidget(self.lib_count)
        c.add(hl)
        self.lib_find = QLineEdit()
        self.lib_find.setPlaceholderText("Tìm giọng")
        self.lib_find.setClearButtonEnabled(True)
        self.lib_find.textChanged.connect(lambda *_a: self.refresh())
        c.add(self.lib_find)
        self.lib = QListWidget()
        self.lib.setSpacing(2)
        self.lib.setWordWrap(True)
        self.lib.setTextElideMode(Qt.TextElideMode.ElideNone)
        self.lib.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.lib.setStyleSheet(
            f"QListWidget {{ background: {theme.SIDEBAR}; border: 1px solid {theme.BORDER}; border-radius: 12px; "
            f"padding: 4px; outline: 0; }}"
            f"QListWidget::item {{ color: {theme.TEXT}; padding: 8px 8px; border-radius: 9px; }}"
            f"QListWidget::item:hover {{ background: {theme.CARD2}; }}"
            f"QListWidget::item:selected {{ background: #2E2320; color: #FFB07A; border: 1px solid {theme.ACCENT2}; }}"
            f"QListWidget::item:disabled {{ color: {theme.MUTED}; }}")
        self.lib.currentItemChanged.connect(lambda cur, _prev: self._lib_pick(cur))
        c.lay.addWidget(self.lib, 1)
        self.b_new = button("Giọng mới", "plus", on_click=self._new)
        self.b_dup = button("Nhân bản", "stack", flat=True, on_click=self._dup)
        self.b_del = button("Xoá", "trash", danger=True, flat=True, on_click=self._delete)
        c.add(flow(self.b_new, self.b_dup, self.b_del))
        c.add(label("Giọng đã lưu hiện ở mọi ô chọn giọng (Giọng cho tất cả, từng người nói, từng câu) trong nhóm "
                    "⭐ Giọng của tôi.", "muted", wrap=True))
        return c

    # ------------------------------------------------------------------ cột 2: công thức
    def _recipe_col(self) -> QWidget:
        from .project_page import scroll

        inner = QWidget()
        v = QVBoxLayout(inner)
        v.setContentsMargins(0, 0, 6, 0)
        v.setSpacing(12)
        hl = QHBoxLayout()
        hl.addWidget(label("Công thức giọng", "h3"))
        hl.addStretch(1)
        self.editing = label("", "muted")
        hl.addWidget(self.editing)
        v.addLayout(hl)

        g1 = Group("1 · Nguồn giọng", "mic", "Đổi mục này = bộ đọc đọc lại câu thử")
        self.g1 = g1
        self.eng_tabs = PillTabs([(lb, ENGINE_ICONS[k]) for k, lb in VL.ENGINES])
        self.eng_tabs.changed.connect(lambda i: self._engine_changed(VL.ENGINES[i][0]))
        g1.add(self.eng_tabs)
        self.base = QComboBox()
        self.base.setMinimumWidth(220)
        self.base.activated.connect(lambda *_a: self._src_changed())
        g1.row("Giọng nền", self.base)
        self.base_note = label("", "muted", wrap=True)
        g1.span(self.base_note)
        self.eng_warn = label("", wrap=True)
        self.eng_warn.setStyleSheet(f"color: {theme.WARN};")
        g1.span(self.eng_warn)
        # thông số riêng từng bộ đọc
        self.e_rate = SliderRow(-30, 30, 1, 0, 0, " %")
        self.e_pitch = SliderRow(-30, 30, 1, 0, 0, " Hz")
        self.e_style = combo(VL.VN_STYLES, "clear")
        self.e_var = QSpinBox()
        self.e_var.setRange(0, 99)
        self.e_var.setSpecialValueText("gốc")
        self.e_var.setMaximumWidth(110)
        self.e_vq = combo(VL.QUALITIES["vox"], "normal")
        self.e_cfg = SliderRow(1.0, 3.0, 0.1, 2.0, 1)
        self.e_kq = combo(VL.QUALITIES["korva"], "normal")
        self.e_kspeed = SliderRow(0.8, 1.3, 0.01, 1.05, 2, "×")
        self.eng_rows = {"edge": [self.e_rate, self.e_pitch], "vieneu": [self.e_style], "omni": [self.e_var],
                         "vox": [self.e_vq, self.e_cfg], "korva": [self.e_kq, self.e_kspeed]}
        g1.row("Tốc độ bộ đọc", self.e_rate, "Edge đọc nhanh / chậm hơn ngay khi đọc (tự nhiên hơn kéo tốc độ sau khi đọc)")
        g1.row("Cao độ bộ đọc", self.e_pitch, "Edge đọc cao / thấp hơn ngay khi đọc")
        g1.row("Kiểu đọc", self.e_style, "Rõ ràng: đều, ít lỗi · Tự nhiên: mặc định VieNeu · Biểu cảm: lên xuống giọng "
                                         "nhiều hơn (đôi khi đọc lỗi – nghe thử trước)")
        g1.row("Biến thể", self.e_var, "Chỉ giọng OmniVoice chọn sẵn: cùng kiểu giọng, người đọc khác (tạo câu mẫu "
                                       "mới 1 lần)")
        g1.row("Chất lượng", self.e_vq, "Nhanh 6 bước · Chuẩn 10 · Kỹ 16 (chậm hơn, rõ hơn)")
        g1.row("Bám giọng mẫu", self.e_cfg, "VoxCPM2 bám giọng / mô tả chặt hơn (cao) hay thả hơn (thấp)")
        g1.row("Chất lượng", self.e_kq, "KorvaTTS: số bước Nhanh 12 / Vừa 20 / Tốt nhất 32")
        g1.row("Tốc độ bộ đọc", self.e_kspeed, "KorvaTTS đọc nhanh / chậm hơn ngay khi đọc")
        for w in (self.e_rate, self.e_pitch, self.e_cfg, self.e_kspeed):
            w.changed.connect(lambda *_a: self._src_changed())
        for w in (self.e_style, self.e_vq, self.e_kq):
            w.activated.connect(lambda *_a: self._src_changed())
        self.e_var.valueChanged.connect(lambda *_a: self._src_changed())
        v.addWidget(g1)

        g2 = Group("2 · Chỉnh sau khi đọc", "wand", "Kéo là nghe ngay – không đọc lại")
        self.a_pitch = SliderRow(-VL.PITCH_MAX, VL.PITCH_MAX, 0.5, 0, 1, " nửa cung")
        self.a_timbre = SliderRow(-VL.TIMBRE_MAX, VL.TIMBRE_MAX, 1, 0, 0, " %")
        self.a_tempo = SliderRow(VL.TEMPO_LO, VL.TEMPO_HI, 0.01, 1.0, 2, "×")
        self.a_warm = SliderRow(-VL.DB_MAX, VL.DB_MAX, 1, 0, 0, " dB")
        self.a_clear = SliderRow(-VL.DB_MAX, VL.DB_MAX, 1, 0, 0, " dB")
        self.a_bright = SliderRow(-VL.DB_MAX, VL.DB_MAX, 1, 0, 0, " dB")
        self.a_comp = SliderRow(0, 100, 5, 0, 0, " %")
        self.a_room = combo(VL.ROOMS, "none")
        self.a_color = combo(VL.COLORS, "none")
        g2.row("Cao độ", self.a_pitch, "Thấp ← → Cao (giữ nguyên âm sắc – kéo xa nghe giọng máy)")
        g2.row("Âm sắc", self.a_timbre, "Dày, to con ← → Mỏng, trẻ")
        g2.row("Tốc độ", self.a_tempo, "Đọc chậm / nhanh hơn, giữ cao độ")
        g2.row("Ấm (trầm)", self.a_warm, "Tăng / giảm dải trầm")
        g2.row("Rõ chữ", self.a_clear, "Tăng dải 2–5 kHz: nghe rõ chữ hơn khi có nhạc nền")
        g2.row("Sáng", self.a_bright, "Tăng / giảm dải cao")
        g2.row("Đều tiếng (nén)", self.a_comp, "Chỗ to – chỗ nhỏ đều nhau hơn (giọng thuyết minh / quảng cáo)")
        g2.row("Vang phòng", self.a_room)
        g2.row("Màu đặc biệt", self.a_color)
        for w in (self.a_pitch, self.a_timbre, self.a_tempo, self.a_warm, self.a_clear, self.a_bright, self.a_comp):
            w.changed.connect(lambda *_a: self._fx_changed())
        for w in (self.a_room, self.a_color):
            w.activated.connect(lambda *_a: self._fx_changed())
        self.b_reset = button("Về gốc", "undo", flat=True, on_click=self._reset_adj)
        g2.actions(self.b_reset)
        g2.add(label(f"Cao độ quá ±{VL.PITCH_MAX:g} nửa cung / âm sắc quá ±{VL.TIMBRE_MAX:g} % nghe giọng máy – tool "
                     "chặn ở mức đó. Muốn đổi nam ↔ nữ: kéo cả cao độ lẫn âm sắc.", "muted", wrap=True))
        v.addWidget(g2)

        g3 = Group("Mẫu nhanh", "sparkle", "Bấm là áp vào mục 2, chỉnh tiếp được")
        self.preset_btns = []
        for nm, adj in VL.PRESETS:
            b = button(nm, flat=True, on_click=lambda a=adj: self._apply_preset(a))
            self.preset_btns.append(b)
        g3.actions(*self.preset_btns)
        v.addWidget(g3)
        v.addStretch(1)
        sa = scroll(inner)
        c = Card(padding=14)
        c.lay.addWidget(sa)
        return c

    # ------------------------------------------------------------------ cột 3: nghe thử
    def _test_col(self) -> QWidget:
        c = Card(padding=14)
        c.setFixedWidth(400)
        c.add(label("Nghe thử", "h3"))
        c.add(label("Gõ đoạn bạn muốn nghe (tối đa ~1 phút)", "muted"))
        self.text = QPlainTextEdit(VL.DEFAULT_TEXT)
        self.text.setMinimumHeight(110)
        self.text.setMaximumHeight(160)
        self.text.textChanged.connect(self._text_changed)
        c.add(self.text)
        c.add(label("Câu mẫu", "muted"))
        self.sample_btns = [button(nm.replace("&", "&&"), flat=True, on_click=lambda t=t: self.text.setPlainText(t))
                            for nm, t in VL.SAMPLES]
        self.b_proj = button("Câu trong dự án đang mở", "film", flat=True, on_click=self._proj_line)
        c.add(flow(*self.sample_btns, self.b_proj))
        self.emo_box = QWidget()
        eh = QHBoxLayout(self.emo_box)
        eh.setContentsMargins(0, 0, 0, 0)
        eh.addWidget(label("Cảm xúc (VoxCPM2)", "muted"))
        self.emo = combo([(k, n) for k, n in EMOS], "")
        self.emo.setToolTip("VoxCPM2 đọc câu thử với cảm xúc này (chỉ để nghe thử – khi lồng tiếng, cảm xúc ghi ở từng câu)")
        eh.addWidget(self.emo, 1)
        c.add(self.emo_box)
        prow = QHBoxLayout()
        self.b_play = GradientButton("Nghe thử", "play")
        self.b_play.clicked.connect(lambda *_a: self._listen())
        self.b_stop = IconButton("pause", "Dừng", 38)
        self.b_stop.clicked.connect(lambda *_a: self._stop())
        prow.addWidget(self.b_play, 1)
        prow.addWidget(self.b_stop)
        c.add(prow)
        self.wave = Wave()
        c.add(self.wave)
        self.stat = label("", "muted", wrap=True)
        c.add(self.stat)
        hl = QHBoxLayout()
        hl.addWidget(label("Các bản vừa nghe", "h4"))
        hl.addWidget(label("bấm ▶ để so qua lại", "muted"))
        hl.addStretch(1)
        c.add(hl)
        self.hist_box = QVBoxLayout()
        self.hist_box.setSpacing(4)
        hw = QWidget()
        hw.setLayout(self.hist_box)
        c.add(hw)
        c.lay.addStretch(1)
        self.b_save_new = GradientButton("Lưu thành giọng mới…", "save")
        self.b_save_new.clicked.connect(lambda *_a: self._save_new())
        self.b_save = button("Lưu đè giọng đang sửa", "save", on_click=self._save_over)
        c.add(self.b_save_new)
        c.add(self.b_save)
        return c

    # ------------------------------------------------------------------ dữ liệu <-> ô
    def _engine(self) -> str:
        return VL.ENGINES[self.eng_tabs.cur][0]

    def _fill_base(self, eng: str, cur: str = ""):
        self.base.clear()
        st = load_settings()
        try:
            vs = VL.base_voices(eng, st)
        except Exception:  # noqa: BLE001 – bộ đọc lỗi khi liệt kê giọng: vẫn mở được trang
            vs = []
        from .project_page import _add_voices

        self._base_gender = {v["id"]: v.get("gender", "") for v in vs}
        _add_voices(self.base, vs)
        if cur and self.base.findData(cur) < 0 and VL.engine_of(cur) == eng:
            self.base.addItem(f"{cur} (không còn trên máy)", cur)
        set_combo(self.base, cur) if cur else None
        self.base_note.setText(VL.ENGINE_NOTE.get(eng, ""))
        why = VL.engine_ready(eng)
        self.eng_warn.setText(("⚠ " + why) if why else ("" if vs else "⚠ Chưa có giọng nào cho bộ đọc này"))
        self.eng_warn.setVisible(bool(self.eng_warn.text()))
        self.g1.hint_lb.setText(f"Đổi mục này = bộ đọc đọc lại câu thử ({VL.ENGINE_TIME.get(eng, '')})")
        for k, rows in self.eng_rows.items():
            for w in rows:
                f = self.g1._form_of(w)
                if f is not None:
                    f.setRowVisible(w, k == eng)
        self.emo_box.setVisible(eng == "vox")

    def _engine_changed(self, eng: str):
        if self._loading:
            return
        self._fill_base(eng)
        self._src_changed()

    def _load_rec(self, rec: dict):
        r = VL.clean(rec)
        self._loading = True
        try:
            self.rec = r
            eng = VL.engine_of(r["base"])
            self.eng_tabs.set_current([k for k, _ in VL.ENGINES].index(eng), emit=False)
            self._fill_base(eng, r["base"])
            e = r["eng"]
            self.e_rate.set(e.get("rate", 0))
            self.e_pitch.set(e.get("pitch", 0))
            set_combo(self.e_style, e.get("style", "clear"))
            self.e_var.setValue(int(e.get("variant", 0)))
            set_combo(self.e_vq, e.get("quality", "normal") if eng == "vox" else "normal")
            self.e_cfg.set(e.get("cfg", 2.0))
            set_combo(self.e_kq, e.get("quality", "normal") if eng == "korva" else "normal")
            self.e_kspeed.set(e.get("speed", 1.05))
            self._set_adj(r["adj"])
        finally:
            self._loading = False
        self._update_head()

    def _set_adj(self, a: dict):
        a = {**VL.ADJ_DEFAULT, **(a or {})}
        self.a_pitch.set(a["pitch"])
        self.a_timbre.set(a["timbre"])
        self.a_tempo.set(a["tempo"])
        self.a_warm.set(a["warm"])
        self.a_clear.set(a["clear"])
        self.a_bright.set(a["bright"])
        self.a_comp.set(a["comp"])
        set_combo(self.a_room, a["room"])
        set_combo(self.a_color, a["color"])

    def current(self) -> dict:
        """Công thức theo các ô đang hiện."""
        eng = self._engine()
        r = copy.deepcopy(self.rec)
        r["base"] = self.base.currentData() or r.get("base") or "edge:vi-VN-HoaiMyNeural"
        if VL.engine_of(r["base"]) != eng:     # bộ đọc chưa có giọng nào -> giữ bộ đọc cũ
            eng = VL.engine_of(r["base"])
        e = {"edge": {"rate": self.e_rate.value(), "pitch": self.e_pitch.value()},
             "vieneu": {"style": self.e_style.currentData()},
             "omni": {"variant": self.e_var.value()},
             "vox": {"quality": self.e_vq.currentData(), "cfg": self.e_cfg.value()},
             "korva": {"quality": self.e_kq.currentData(), "speed": self.e_kspeed.value()}}[eng]
        r["eng"] = e
        r["adj"] = {"pitch": self.a_pitch.value(), "timbre": self.a_timbre.value(), "tempo": self.a_tempo.value(),
                    "warm": self.a_warm.value(), "clear": self.a_clear.value(), "bright": self.a_bright.value(),
                    "comp": self.a_comp.value(), "room": self.a_room.currentData(), "color": self.a_color.currentData()}
        return VL.clean(r)

    def _update_head(self):
        if self.cur_id:
            r = VL.get(self.cur_id)
            self.editing.setText("Đang sửa: " + (r or {}).get("name", "?"))
        else:
            self.editing.setText("Giọng mới – chưa lưu")
        self.b_save.setEnabled(bool(self.cur_id))
        self.b_dup.setEnabled(bool(self.cur_id))
        self.b_del.setEnabled(bool(self.cur_id))
        self.b_export.setEnabled(bool(self.cur_id))

    # ------------------------------------------------------------------ thư viện
    def refresh(self, select: str | None = None):
        q = self.lib_find.text().strip().lower()
        items = VL.list_all()
        self.lib_count.setText(f"{len(items)} giọng")
        want = select if select is not None else self.cur_id
        self.lib.blockSignals(True)
        self.lib.clear()
        for r in items:
            if q and q not in r["name"].lower() and q not in (r.get("use") or "").lower():
                continue
            it = QListWidgetItem(f"{r['name']}\n{VL.describe(r)}")
            it.setData(Qt.ItemDataRole.UserRole, r["id"])
            it.setToolTip(("Hợp với: " + r["use"]) if r.get("use") else VL.describe(r))
            self.lib.addItem(it)
            if r["id"] == want:
                self.lib.setCurrentItem(it)
        if self.lib.count() == 0:
            it = QListWidgetItem("Chưa có giọng nào – chỉnh ở giữa, nghe thử rồi\n\"Lưu thành giọng mới…\"")
            it.setFlags(Qt.ItemFlag.NoItemFlags)
            self.lib.addItem(it)
        self.lib.blockSignals(False)

    def _lib_pick(self, item):
        vid = item.data(Qt.ItemDataRole.UserRole) if item is not None else None
        if not vid or vid == self.cur_id:
            return
        r = VL.get(vid)
        if not r:
            return
        self.cur_id = vid
        self._load_rec(r)

    def open_voice(self, spec: str = ""):
        """Mở từ tab Lồng tiếng: giọng của tôi -> sửa giọng đó; giọng khác -> giọng mới có giọng nền là giọng đó."""
        if spec.startswith(VL.PREFIX) and VL.get(spec):
            self.cur_id = VL.bare(spec)
            self._load_rec(VL.get(spec))
            self.refresh(self.cur_id)
            return
        self.cur_id = ""
        self._load_rec(VL.new_record(spec) if spec and not spec.startswith(VL.PREFIX) else VL.new_record())
        self.refresh("")

    def _new(self):
        self.cur_id = ""
        base = self.base.currentData() or "edge:vi-VN-HoaiMyNeural"
        self._load_rec(VL.new_record(base))
        self.refresh("")

    def _dup(self):
        if not self.cur_id:
            return
        r = VL.duplicate(self.cur_id)
        if r:
            self.cur_id = r["id"]
            self._load_rec(r)
            self.refresh(r["id"])
            self._changed_voices()

    def _delete(self):
        r = VL.get(self.cur_id) if self.cur_id else None
        if not r:
            return
        if QMessageBox.question(self, "Xoá giọng", f"Xoá giọng \"{r['name']}\"?\nCâu đang dùng giọng này sẽ đọc bằng "
                                "Hoài My (Edge) ở lần lồng tiếng sau – tool báo trong thông báo lồng tiếng.") != \
                QMessageBox.StandardButton.Yes:
            return
        VL.delete(self.cur_id)
        st = load_settings()
        if st.get("dub_voice_all") == VL.PREFIX + self.cur_id:
            st["dub_voice_all"] = ""
            save_settings(st)
        self.cur_id = ""
        self._load_rec(VL.new_record(r["base"]))
        self.refresh("")
        self._changed_voices()
        self.win.toast(f"Đã xoá giọng {r['name']}", "ok")

    def _import(self):
        path, _ = QFileDialog.getOpenFileName(self, "Nhập giọng", "", f"Giọng Subvideo (*{VL.EXT} *.json)")
        if not path:
            return
        try:
            r, note = VL.import_file(path)
        except ValueError as e:
            return self.win.toast(str(e), "err")
        self.cur_id = r["id"]
        self._load_rec(r)
        self.refresh(r["id"])
        self._changed_voices()
        self.win.toast(f"Đã nhập giọng {r['name']}" + (f" – ⚠ {note}" if note else ""), "warn" if note else "ok")

    def _export(self):
        r = VL.get(self.cur_id) if self.cur_id else None
        if not r:
            return self.win.toast("Chọn 1 giọng đã lưu để xuất", "warn")
        path, _ = QFileDialog.getSaveFileName(self, "Xuất giọng", VL.safe_name(r["name"]) + VL.EXT,
                                              f"Giọng Subvideo (*{VL.EXT})")
        if not path:
            return
        if not path.endswith(VL.EXT):
            path += VL.EXT
        VL.export_file(self.cur_id, path)
        self.win.toast("Đã xuất giọng (chỉ công thức – giọng nền là giọng mẫu ghi âm thì máy kia phải có giọng mẫu đó)",
                       "ok")

    def _changed_voices(self):
        """Báo trang dự án nạp lại danh sách giọng (giọng của tôi đứng đầu mọi ô chọn giọng)."""
        cb = getattr(self.win, "voices_changed", None)
        if cb:
            cb()

    # ------------------------------------------------------------------ chỉnh
    def _src_changed(self):
        if self._loading:
            return
        self._last = None      # nguồn đổi -> phải đọc lại (bấm Nghe thử)
        self.stat.setText("Nguồn giọng đã đổi – bấm Nghe thử để bộ đọc đọc lại")

    def _text_changed(self):
        if len(self.text.toPlainText()) > VL.MAX_TEXT:
            self.text.blockSignals(True)
            self.text.setPlainText(self.text.toPlainText()[:VL.MAX_TEXT])
            self.text.blockSignals(False)

    def _fx_changed(self):
        if self._loading:
            return
        self._fx_timer.start()

    def _fx_changed_now(self):
        """Đã có bản đọc của đúng nguồn + lời: áp lại phần chỉnh rồi phát luôn (không đọc lại)."""
        last = self._last
        if not last or self._busy:
            return
        rec = self.current()
        if VL.source_key(rec) != last["src"] or self.text.toPlainText().strip() != last["text"]:
            return
        self._run(rec, last["text"], last["emo"], replace=True)

    def _apply_preset(self, adj: dict):
        self._loading = True
        try:
            self._set_adj({**VL.ADJ_DEFAULT, **adj})
        finally:
            self._loading = False
        self._fx_changed()

    def _reset_adj(self):
        self._apply_preset({})

    # ------------------------------------------------------------------ nghe thử
    def _proj_line(self):
        pp = getattr(self.win, "project_page", None)
        segs = getattr(getattr(pp, "model", None), "segs", None) if pp is not None and getattr(pp, "p", None) else None
        if not segs:
            return self.win.toast("Chưa mở dự án nào có bản dịch", "warn")
        row = None
        try:
            row = pp._selected_row()
        except Exception:  # noqa: BLE001
            row = None
        s = segs[row] if row is not None and 0 <= row < len(segs) and segs[row].trans else \
            next((x for x in segs if x.trans), None)
        if not s:
            return self.win.toast("Dự án đang mở chưa có câu nào đã dịch", "warn")
        self.text.setPlainText(s.trans)

    def _listen(self):
        if self._busy:
            return
        rec = self.current()
        text = self.text.toPlainText().strip() or VL.DEFAULT_TEXT
        eng = VL.engine_of(rec["base"])
        why = VL.engine_ready(eng)
        if why:
            return self.win.toast(why, "warn")
        if eng != "edge" and not VL.cached_raw(rec, text, "vi", load_settings(), self._emo()):
            busy = manager.gpu_busy({"dub", "redub"}) if eng != "korva" else ""
            if busy:
                return self.win.toast(f"Đang chạy '{busy}' trên card đồ hoạ – nghe thử giọng AI sau khi việc đó xong "
                                      "(giọng Edge nghe thử được ngay)", "warn")
        self._run(rec, text, self._emo(), replace=False)

    def _emo(self) -> str:
        return (self.emo.currentData() or "") if self._engine() == "vox" else ""

    def _run(self, rec: dict, text: str, emo: str, replace: bool):
        self._busy = True
        self.b_play.setEnabled(False)
        st = load_settings()
        fresh = VL.cached_raw(rec, text, "vi", st, emo) is None
        self.stat.setText(f"Đang đọc… ({VL.ENGINE_TIME.get(VL.engine_of(rec['base']), '')})" if fresh
                          else "Đang áp phần chỉnh…")

        def job():
            path = VL.preview(rec, text, st, "vi", emo)
            return path, VL.stats(path, text)

        run_async(job, lambda res: self._done(rec, text, emo, res, replace), self._fail)

    def _fail(self, m: str):
        self._busy = False
        self.b_play.setEnabled(True)
        self.stat.setText("Không tạo được giọng: " + m[:300])
        self.win.toast("Không tạo được giọng: " + m[:200], "err")

    def _done(self, rec: dict, text: str, emo: str, res, replace: bool):
        self._busy = False
        self.b_play.setEnabled(True)
        path, st = res
        self._last = {"path": str(path), "text": text, "src": VL.source_key(rec), "emo": emo}
        self._show_stats(st)
        base_name = self.base.currentText().split(" – ")[0].split(" (")[0]
        entry = {"rec": copy.deepcopy(rec), "path": str(path), "stats": st,
                 "label": f"{base_name} · {VL.adj_text(rec)}"}
        if replace and self.history and self.history[0]["rec"].get("_src") == VL.source_key(rec):
            entry["n"] = self.history[0]["n"]
            self.history[0] = entry
        else:
            entry["n"] = (self.history[0]["n"] + 1) if self.history else 1
            self.history.insert(0, entry)
            del self.history[HISTORY_MAX:]
        entry["rec"]["_src"] = VL.source_key(rec)
        self._fill_history()
        self._play(path)

    def _show_stats(self, st: dict):
        self.wave.set(st.get("peaks") or [])
        sec = f"{st['dur']:.1f}".replace(".", ",")
        rate = f"{st['rate']:.1f}".replace(".", ",")
        self.stat.setText(f"{sec} giây · {st['syl']} tiếng · {rate} tiếng/giây")

    def _fill_history(self):
        while self.hist_box.count():
            w = self.hist_box.takeAt(0).widget()
            if w:
                w.hide()
                w.setParent(None)
                w.deleteLater()
        for k, h in enumerate(self.history):
            row = QFrame()
            hl = QHBoxLayout(row)
            hl.setContentsMargins(6, 2, 2, 2)
            tb = QVBoxLayout()
            tb.setSpacing(0)
            t = label(f"Bản {h['n']}" + (" · đang chỉnh" if k == 0 else ""))
            t.setFont(theme.font(9.4, QFont.Weight.DemiBold))
            tb.addWidget(t)
            sub = label(h["label"], "muted")
            sub.setToolTip(h["label"])
            tb.addWidget(sub)
            hl.addLayout(tb, 1)
            pb = IconButton("play", "Nghe bản này", 30)
            pb.clicked.connect(lambda *_a, h=h: self._play_hist(h))
            hl.addWidget(pb)
            if k:
                rb = button("Lấy lại", flat=True, on_click=lambda h=h: self._restore(h))
                hl.addWidget(rb)
            self.hist_box.addWidget(row)

    def _play_hist(self, h: dict):
        if Path(h["path"]).exists():
            self._show_stats(h["stats"])
            self._play(h["path"])

    def _restore(self, h: dict):
        r = copy.deepcopy(h["rec"])
        r.pop("_src", None)
        r["id"], r["name"] = self.rec.get("id", ""), self.rec.get("name", "")
        self._load_rec(r)
        self._last = None
        self.win.toast("Đã lấy lại công thức của bản này – bấm Nghe thử để nghe với câu đang gõ", "ok")

    # ------------------------------------------------------------------ phát
    def _ensure_player(self):
        if self._player is None:
            try:
                from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer

                self._player = QMediaPlayer(self)
                self._audio = QAudioOutput(self)
                self._player.setAudioOutput(self._audio)
            except Exception:  # noqa: BLE001 – máy không có âm thanh: vẫn tạo file được
                self._player = False
        return self._player or None

    def _play(self, path):
        mp = self._ensure_player()
        if mp is None:
            return
        mp.stop()
        mp.setSource(QUrl.fromLocalFile(str(path)))
        mp.play()
        self._pos_timer.start()

    def _stop(self):
        if self._player:
            self._player.stop()
        self._pos_timer.stop()
        self.wave.set_pos(0)

    def _tick(self):
        mp = self._player
        if not mp:
            return self._pos_timer.stop()
        dur = mp.duration()
        if dur > 0:
            self.wave.set_pos(mp.position() / dur)
        from PySide6.QtMultimedia import QMediaPlayer

        if mp.playbackState() != QMediaPlayer.PlaybackState.PlayingState and mp.position() >= dur > 0:
            self._pos_timer.stop()

    # ------------------------------------------------------------------ lưu
    def _save_new(self):
        rec = self.current()
        rec["id"] = ""
        if not rec.get("gender"):     # nam / nữ theo giọng nền (Hoài My nữ, Nam Minh nam…) – sửa được trong hộp Lưu
            rec["gender"] = getattr(self, "_base_gender", {}).get(rec["base"], "")
        if not rec.get("name") or rec["name"] == "Giọng mới":
            rec["name"] = (self.base.currentText().split(" – ")[0].split(" (")[0].strip("⭐🎙🔥🎬📣 ") or "Giọng mới")
            if VL.adj_text(rec) != "gốc":
                rec["name"] += " – chỉnh"
        st = load_settings()
        dlg = SaveDialog(self, rec, st)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        r = VL.save(dlg.rec, new=True)
        if dlg.default.isChecked():
            st = load_settings()
            st["dub_voice_all"] = VL.PREFIX + r["id"]
            save_settings(st)
        self.cur_id = r["id"]
        self._load_rec(r)
        self.refresh(r["id"])
        self._changed_voices()
        self.win.toast(f"Đã lưu giọng {r['name']} – chọn ở tab Lồng tiếng → Giọng cho tất cả (nhóm ⭐ Giọng của tôi)",
                       "ok")

    def _save_over(self):
        old = VL.get(self.cur_id) if self.cur_id else None
        if not old:
            return self._save_new()
        rec = self.current()
        for k in ("id", "name", "gender", "use", "created"):
            rec[k] = old.get(k)
        same_src = VL.source_key(rec) == VL.source_key(old) and abs(rec["adj"]["tempo"] - old["adj"]["tempo"]) < 1e-3
        rec["rate"] = old.get("rate") if same_src else None   # tốc độ đọc đổi -> đo lại ở lần lưu sau / nút đo
        r = VL.save(rec)
        self._load_rec(r)
        self.refresh(r["id"])
        self._changed_voices()
        if not same_src:
            run_async(lambda: VL.measure_rate(r, load_settings()), lambda v: self._rate_saved(r["id"], v),
                      lambda m: None)
        self.win.toast(f"Đã lưu đè giọng {r['name']}. Phim đã lồng bằng giọng này: bấm Tạo lại giọng – chỉ đổi phần "
                       "chỉnh thì tool áp lại, không đọc lại", "ok")

    def _rate_saved(self, vid: str, v: float):
        r = VL.get(vid)
        if r and v:
            r["rate"] = v
            VL.save(r)
            self.refresh(vid if self.cur_id == vid else None)
