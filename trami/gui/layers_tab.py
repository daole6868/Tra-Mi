"""Tab "Chữ & Logo": thêm chữ theo mẫu (tiêu đề nền cọ, hộp bo góc, bong bóng thoại, chữ chạy…), logo của bạn (nhiều
logo, PNG / JPG / WebP / SVG / GIF động – bấm hoặc kéo thả vào video), logo mẫu kèm tên kênh, danh sách lớp + thuộc tính
lớp đang chọn. Mỗi lớp có 1 dòng riêng trên timeline (kéo để đổi lúc hiện / ẩn), kéo trên video để đặt chỗ."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QMimeData, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (QAbstractButton, QDoubleSpinBox, QFileDialog, QLineEdit, QListWidget,
                               QListWidgetItem, QMessageBox, QPlainTextEdit, QVBoxLayout, QWidget)

from .. import overlay
from . import theme
from .widgets import (ColorButton, Form, SectionCard, SliderRow, Switch, button, combo, flow, hbox, label,
                      set_combo)

MIME = "application/x-subvideo-logo"
LIST_QSS = (f"QListWidget {{ background: #0F141D; border: 1px solid {theme.BORDER}; border-radius: 9px; padding: 4px; "
            f"color: {theme.TEXT}; outline: none; }} QListWidget::item {{ padding: 5px 7px; border-radius: 7px; }} "
            f"QListWidget::item:selected {{ background: #4A2A24; color: #FFFFFF; }} "
            f"QListWidget::item:hover:!selected {{ background: {theme.CARD2}; }}")
POS_PRESETS = [("", "Chọn nhanh…"), ("tl", "Trên trái"), ("tc", "Trên giữa"), ("tr", "Trên phải"),
               ("ml", "Giữa trái"), ("mc", "Chính giữa"), ("mr", "Giữa phải"), ("bl", "Dưới trái"),
               ("bc", "Dưới giữa"), ("br", "Dưới phải")]
_PX = {"l": 0.18, "c": 0.5, "r": 0.82}
_PY = {"t": 0.1, "m": 0.5, "b": 0.86}


def _fmt(t: float) -> str:
    m, s = divmod(max(0.0, t), 60)
    return f"{int(m):02d}:{s:04.1f}"


class TemplateButton(QAbstractButton):
    """Nút mẫu chữ: ảnh xem trước vẽ đúng như trên video + tên mẫu."""

    def __init__(self, code: str, name: str, channel: str = "", parent=None):
        super().__init__(parent)
        self.code, self.name = code, name
        self.channel = channel
        self._pix: QPixmap | None = None
        self._hov = False
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(f"Thêm '{name}' vào video")

    def set_channel(self, ch: str):
        self.channel = ch
        self._pix = None
        self.update()

    def sizeHint(self):
        return QSize(150, 82)

    def enterEvent(self, e):
        self._hov = True
        self.update()

    def leaveEvent(self, e):
        self._hov = False
        self.update()

    def _preview(self) -> QPixmap:
        if self._pix is None:
            try:
                L = overlay.new_layer("text", template=self.code, channel=self.channel)
                L["rot"] = 0
                img, _ox, _oy = overlay.render(L, 1280, 720, 0.55)
                if img.height() > 50:
                    img = img.scaledToHeight(50, Qt.TransformationMode.SmoothTransformation)
                if img.width() > 138:
                    if L.get("anim") == "scroll":    # chữ chạy dài: chỉ hiện đầu dòng (đúng cỡ chữ)
                        img = img.copy(0, 0, 138, img.height())
                    else:
                        img = img.scaled(138, 50, Qt.AspectRatioMode.KeepAspectRatio,
                                         Qt.TransformationMode.SmoothTransformation)
                self._pix = QPixmap.fromImage(img)
            except Exception:  # noqa: BLE001
                self._pix = QPixmap()
        return self._pix

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        p.setPen(QColor(theme.ACCENT if self._hov else theme.BORDER))
        p.setBrush(QColor("#2B3448"))
        p.drawRoundedRect(r, 9, 9)
        pm = self._preview()
        if not pm.isNull():
            p.drawPixmap(int(r.center().x() - pm.width() / 2), int(r.top() + 6 + (50 - pm.height()) / 2), pm)
        p.setPen(QColor(theme.TEXT if self._hov else theme.MUTED))
        p.setFont(theme.font(8))
        p.drawText(QRectF(r.left() + 4, r.bottom() - 20, r.width() - 8, 18), Qt.AlignmentFlag.AlignCenter,
                   p.fontMetrics().elidedText(self.name, Qt.TextElideMode.ElideRight, int(r.width() - 8)))


class LogoList(QListWidget):
    """Thư viện logo: kéo 1 logo thả vào video bên phải."""

    def __init__(self):
        super().__init__()
        self.setViewMode(QListWidget.ViewMode.IconMode)
        self.setIconSize(QSize(64, 64))
        self.setGridSize(QSize(92, 92))
        self.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.setMovement(QListWidget.Movement.Static)
        self.setDragEnabled(True)
        self.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)
        self.setMinimumHeight(110)
        self.setMaximumHeight(210)
        self.setWordWrap(True)
        self.setStyleSheet(LIST_QSS)

    def mimeTypes(self):
        return [MIME]

    def mimeData(self, items):
        md = QMimeData()
        if items:
            md.setData(MIME, ("lib:" + items[0].data(Qt.ItemDataRole.UserRole)).encode("utf-8"))
        return md


class LayersTab(QWidget):
    changed = Signal()          # thêm / xoá / sửa lớp (trang dự án lưu + vẽ lại trình phát, timeline)
    selected = Signal(str)      # chọn lớp (mã) – đồng bộ trình phát + timeline

    def __init__(self, page):
        super().__init__()
        self.page = page
        self.layers: list[dict] = []
        self.sel = ""
        self._loading = False
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 2, 0)
        v.setSpacing(theme.GAP)
        overlay.register_fonts()

        # --- thêm chữ
        add = SectionCard("Thêm chữ", "text", "Bấm 1 mẫu để thêm vào video, rồi sửa chữ ở mục 'Lớp đang chọn' bên dưới. "
                          "Kéo chữ trên video để đặt chỗ, kéo 4 góc để phóng to / thu nhỏ.")
        self.when = combo([("all", "Hiện suốt video"), ("here", "Hiện từ đầu phát, 5 giây")], "all")
        add.add(hbox(label("Khi thêm:", "muted"), self.when, None))
        self.tpl_btns = []
        tw = flow()
        for code, name, _p in overlay.TEMPLATES:
            b = TemplateButton(code, name)
            b.clicked.connect(lambda _=False, c=code: self.add_text(c))
            tw.layout().addWidget(b)
            self.tpl_btns.append(b)
        add.add(tw)
        add.add(flow(button("Chữ trống", "plus", "Thêm 1 dòng chữ trắng viền đen", flat=True,
                            on_click=lambda: self.add_text(None))))

        # --- logo của bạn
        logo = SectionCard("Logo của bạn", "image", "Tải nhiều logo lên 1 lần (PNG, JPG, WebP, SVG, GIF động). Bấm đúp "
                           "hoặc kéo logo thả vào video. Dùng được nhiều logo cùng lúc.")
        self.logo_list = LogoList()
        self.logo_list.itemDoubleClicked.connect(lambda it: self.add_logo_item(it))
        logo.add(self.logo_list)
        logo.add(flow(button("Tải logo lên…", "upload", on_click=self._upload_logos),
                      button("Thêm vào video", "plus", flat=True, on_click=self._add_selected_logos),
                      button("Xoá khỏi thư viện", "trash", flat=True, on_click=self._remove_logos)))

        # --- logo mẫu (tên kênh)
        ch = SectionCard("Logo mẫu – tên kênh", "sparkle", "Nhập tên kênh rồi bấm 1 mẫu.")
        self.channel = QLineEdit()
        self.channel.setPlaceholderText("Tên kênh của bạn, vd: Phim Hay TV")
        self.channel.setText(str(page.settings.get("channel_name", "") or ""))
        self.channel.textChanged.connect(self._channel_changed)
        ch.add(hbox(label("Tên kênh", "muted"), self.channel))
        cw = flow()
        self.ch_btns = []
        for code, name, _p in overlay.LOGO_TEMPLATES:
            b = TemplateButton(code, name, self.channel.text())
            b.clicked.connect(lambda _=False, c=code: self.add_text(c, channel=True))
            cw.layout().addWidget(b)
            self.ch_btns.append(b)
        ch.add(cw)

        # --- các lớp
        lst = SectionCard("Các lớp trên video", "layers", "Lớp ở trên danh sách nằm trên cùng. Mỗi lớp có 1 dòng trên "
                          "timeline: kéo thanh để dời lúc hiện, kéo 2 mép để đổi lúc bắt đầu / kết thúc.")
        self.list = QListWidget()
        self.list.setStyleSheet(LIST_QSS)
        self.list.setMinimumHeight(120)
        self.list.setMaximumHeight(230)
        self.list.currentRowChanged.connect(self._list_row)
        lst.add(self.list)
        lst.add(flow(button("Lên trên", "chev_left", "Đưa lớp lên trên (che các lớp khác)", flat=True,
                            on_click=lambda: self.move(+1)),
                     button("Xuống dưới", "chev_right", "Đưa lớp xuống dưới", flat=True,
                            on_click=lambda: self.move(-1)),
                     button("Nhân bản", "stack", flat=True, on_click=self.duplicate),
                     button("Ẩn / hiện", "eye", "Tạm ẩn lớp (không có trong video xuất)", flat=True,
                            on_click=self.toggle_hidden),
                     button("Xoá lớp", "trash", flat=True, on_click=self.delete)))
        self.empty = label("Chưa có chữ / logo nào – bấm 1 mẫu ở trên để thêm.", "muted", wrap=True)
        lst.add(self.empty)

        # --- thuộc tính
        self.props = SectionCard("Lớp đang chọn", "settings")
        self._build_props()
        for c in (add, logo, ch, lst, self.props):
            v.addWidget(c)
        v.addStretch(1)
        self.fill_library()
        self._refresh_list()

    # ================================================================== thuộc tính
    def _build_props(self):
        f = Form()
        self.pf = f
        self.p_text = f.row("Chữ", QPlainTextEdit())
        self.p_text.setPlaceholderText("Nhập chữ (Enter = xuống dòng)")
        self.p_text.setFixedHeight(66)
        self.p_text.textChanged.connect(lambda: self._set("text", self.p_text.toPlainText()))
        self.p_font = f.row("Font", combo([], None))
        self._fill_fonts()
        self.p_font.currentIndexChanged.connect(lambda *_: self._set("font", self.p_font.currentData()))
        self.p_addfont = f.row("", flow(button("Nạp font riêng…", "upload", "Chọn file .ttf / .otf trên máy",
                                               flat=True, on_click=self._add_font)))
        self.p_size = f.row("Cỡ chữ (% cạnh ngắn khung)", SliderRow(1, 30, 0.5, 6, 1))
        self.p_size.changed.connect(lambda v: self._set("size", round(v / 100, 4)))
        self.p_bold = f.row("Đậm", Switch(True))
        self.p_bold.toggledOn.connect(lambda on: self._set("bold", on))
        self.p_italic = f.row("Nghiêng", Switch())
        self.p_italic.toggledOn.connect(lambda on: self._set("italic", on))
        self.p_align = f.row("Căn dòng", combo(overlay.ALIGNS, "center"))
        self.p_align.currentIndexChanged.connect(lambda *_: self._set("align", self.p_align.currentData()))
        self.p_color = f.row("Màu chữ", ColorButton("#FFFFFF"))
        self.p_color.changed.connect(lambda c: self._set("color", c))
        self.p_outline = f.row("Viền chữ", hbox(SliderRow(0, 30, 1, 8, 0, suffix=" %"), ColorButton("#000000")))
        self.p_ol, self.p_olc = self.p_outline.itemAt(0).widget(), self.p_outline.itemAt(1).widget()
        self.p_ol.changed.connect(lambda v: self._set("outline", round(v / 100, 3)))
        self.p_olc.changed.connect(lambda c: self._set("outline_color", c))
        self.p_shadow = f.row("Bóng đổ", hbox(SliderRow(0, 20, 1, 0, 0, suffix=" %"), ColorButton("#000000")))
        self.p_sd, self.p_sdc = self.p_shadow.itemAt(0).widget(), self.p_shadow.itemAt(1).widget()
        self.p_sd.changed.connect(lambda v: self._set("shadow", round(v / 100, 3)))
        self.p_sdc.changed.connect(lambda c: self._set("shadow_color", c))
        self.p_bg = f.row("Nền", hbox(combo(overlay.BG_STYLES, "none"), ColorButton("#000000")))
        self.p_bgk, self.p_bgc = self.p_bg.itemAt(0).widget(), self.p_bg.itemAt(1).widget()
        self.p_bgk.currentIndexChanged.connect(lambda *_: (self._set("bg", self.p_bgk.currentData()), self._rows()))
        self.p_bgc.changed.connect(lambda c: self._set("bg_color", c))
        self.p_bgop = f.row("Độ đậm nền", SliderRow(0, 100, 5, 70, 0, suffix=" %"))
        self.p_bgop.changed.connect(lambda v: self._set("bg_opacity", round(v / 100, 3)))
        self.p_pad = f.row("Đệm quanh chữ", SliderRow(0, 150, 5, 35, 0, suffix=" %"))
        self.p_pad.changed.connect(lambda v: self._set("pad", round(v / 100, 3)))
        self.p_radius = f.row("Bo góc nền", SliderRow(0, 50, 1, 30, 0, suffix=" %"))
        self.p_radius.changed.connect(lambda v: self._set("radius", round(v / 100, 3)))
        self.p_lineh = f.row("Giãn dòng", SliderRow(0.8, 2.0, 0.02, 1.12, 2))
        self.p_lineh.changed.connect(lambda v: self._set("line_h", round(v, 3)))
        self.p_spacing = f.row("Giãn chữ", SliderRow(-10, 40, 1, 0, 0, suffix=" %"))
        self.p_spacing.changed.connect(lambda v: self._set("spacing", int(v)))
        # logo
        self.p_file = f.row("Ảnh logo", hbox(label("", "muted"), button("Đổi ảnh…", "image", flat=True,
                                                                         on_click=self._change_logo)))
        self.p_file_lbl = self.p_file.itemAt(0).widget()
        self.p_w = f.row("Cỡ logo (% bề rộng khung)", SliderRow(2, 100, 1, 16, 0, suffix=" %"))
        self.p_w.changed.connect(lambda v: self._set("w", round(v / 100, 4)))
        # chung
        self.p_rot = f.row("Nghiêng / xoay", SliderRow(-180, 180, 1, 0, 0, suffix="°"))
        self.p_rot.changed.connect(lambda v: self._set("rot", float(v)))
        self.p_op = f.row("Độ rõ", SliderRow(5, 100, 5, 100, 0, suffix=" %"))
        self.p_op.changed.connect(lambda v: self._set("opacity", round(v / 100, 3)))
        self.p_pos = f.row("Vị trí", combo(POS_PRESETS, ""))
        self.p_pos.activated.connect(self._pos_preset)
        self.p_x = f.row("Ngang (% từ trái)", SliderRow(-5, 105, 0.5, 50, 1))
        self.p_x.changed.connect(lambda v: self._set("x", round(v / 100, 4)))
        self.p_y = f.row("Dọc (% từ trên)", SliderRow(-5, 105, 0.5, 12, 1))
        self.p_y.changed.connect(lambda v: self._set("y", round(v / 100, 4)))
        self.p_anim = f.row("Hiệu ứng", combo(overlay.ANIMS, "none"))
        self.p_anim.currentIndexChanged.connect(lambda *_: self._set("anim", self.p_anim.currentData()))
        self.p_fi, self.p_fo = QDoubleSpinBox(), QDoubleSpinBox()
        for sp in (self.p_fi, self.p_fo):
            sp.setRange(0, 5)
            sp.setSingleStep(0.1)
            sp.setDecimals(1)
            sp.setSuffix(" s")
            sp.setFixedWidth(84)
        self.p_fade = f.row("Hiện dần / mờ dần", hbox(label("hiện dần", "muted"), self.p_fi, label("mờ dần", "muted"),
                                                      self.p_fo, None),
                            "Giây chữ / logo hiện rõ dần lúc xuất hiện và mờ dần lúc biến mất (trượt vào: thời gian trượt)")
        self.p_fi.valueChanged.connect(lambda v: self._set("fade_in", round(v, 2)))
        self.p_fo.valueChanged.connect(lambda v: self._set("fade_out", round(v, 2)))
        self.p_start = QDoubleSpinBox()
        self.p_end = QDoubleSpinBox()
        for sp in (self.p_start, self.p_end):
            sp.setRange(0, 1e6)
            sp.setDecimals(2)
            sp.setSuffix(" s")
            sp.setFixedWidth(108)
        self.p_end.setSpecialValueText("hết video")
        self.p_start.valueChanged.connect(lambda v: self._set("start", round(v, 3)))
        self.p_end.valueChanged.connect(lambda v: self._set("end", round(v, 3)))
        f.row("Hiện từ", hbox(self.p_start, button("= đầu phát", "clock", flat=True,
                                                    on_click=lambda: self.p_start.setValue(self.page.player.position())),
                              None))
        f.row("Đến (0 = hết video)", hbox(self.p_end, button("= đầu phát", "clock", flat=True,
                                                             on_click=lambda: self.p_end.setValue(
                                                                 self.page.player.position())), None))
        f.row("", flow(button("Hiện suốt video", "fit", flat=True, on_click=self.full_span)))
        self.props.add(f)
        self.props.setVisible(False)
        self._text_rows = [self.p_text, self.p_font, self.p_addfont, self.p_size, self.p_bold, self.p_italic,
                           self.p_align, self.p_color, self.p_outline, self.p_shadow, self.p_bg, self.p_bgop,
                           self.p_pad, self.p_radius, self.p_lineh, self.p_spacing]
        self._logo_rows = [self.p_file, self.p_w]

    def _fill_fonts(self, cur: str | None = None):
        self.p_font.blockSignals(True)
        self.p_font.clear()
        for fam, name in overlay.FONTS:
            self.p_font.addItem(name, fam)
        for fam, fn in overlay.user_fonts():
            if self.p_font.findData(fam) < 0:
                self.p_font.addItem(f"{fam} – font của bạn", fam)
        for fam in ("Arial", "Segoe UI", "Tahoma", "Times New Roman"):
            self.p_font.addItem(fam, fam)
        if cur:
            if self.p_font.findData(cur) < 0:
                self.p_font.addItem(cur, cur)
            set_combo(self.p_font, cur)
        self.p_font.blockSignals(False)

    def _add_font(self):
        path, _ = QFileDialog.getOpenFileName(self, "Chọn font", "", "Font (*.ttf *.otf)")
        if not path:
            return
        try:
            fam = overlay.add_user_font(path)
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(self, "Nạp font", str(e))
            return
        self._fill_fonts(fam)
        self._set("font", fam)
        self.page.win.toast(f"Đã nạp font {fam}", "ok")

    def _rows(self):
        L = self.cur()
        if not L:
            return
        is_logo = L.get("kind") == "logo"
        for w in self._text_rows:
            self.pf.setRowVisible(w, not is_logo)
        for w in self._logo_rows:
            self.pf.setRowVisible(w, is_logo)
        if not is_logo:
            bg = L.get("bg") or "none"
            self.pf.setRowVisible(self.p_bgop, bg != "none")
            self.pf.setRowVisible(self.p_pad, bg != "none")
            self.pf.setRowVisible(self.p_radius, bg in ("round", "bubble"))
            self.p_bgc.setVisible(bg != "none")

    def _load_props(self):
        L = self.cur()
        self.props.setVisible(bool(L))
        if not L:
            return
        self._loading = True
        try:
            if L.get("kind") == "logo":
                self.p_file_lbl.setText(Path(L.get("path", "")).name or "(chưa có ảnh)")
                self.p_w.set(float(L.get("w") or 0.16) * 100)
            else:
                if self.p_text.toPlainText() != L.get("text", ""):
                    self.p_text.setPlainText(L.get("text", ""))
                self._fill_fonts(L.get("font"))
                self.p_size.set(float(L.get("size") or 0.06) * 100)
                self.p_bold.setChecked(bool(L.get("bold")))
                self.p_italic.setChecked(bool(L.get("italic")))
                set_combo(self.p_align, L.get("align", "center"))
                self.p_color.set(L.get("color", "#FFFFFF"))
                self.p_ol.set(float(L.get("outline") or 0) * 100)
                self.p_olc.set(L.get("outline_color", "#000000"))
                self.p_sd.set(float(L.get("shadow") or 0) * 100)
                self.p_sdc.set(L.get("shadow_color", "#000000"))
                set_combo(self.p_bgk, L.get("bg", "none"))
                self.p_bgc.set(L.get("bg_color", "#000000"))
                self.p_bgop.set(float(L.get("bg_opacity", 0.7) if L.get("bg_opacity") is not None else 0.7) * 100)
                self.p_pad.set(float(L.get("pad") or 0) * 100)
                self.p_radius.set(float(L.get("radius") or 0) * 100)
                self.p_lineh.set(float(L.get("line_h") or 1.12))
                self.p_spacing.set(float(L.get("spacing") or 0))
            self.p_rot.set(float(L.get("rot") or 0))
            self.p_op.set(float(L.get("opacity", 1) if L.get("opacity") is not None else 1) * 100)
            self.p_x.set(float(L.get("x", 0.5)) * 100)
            self.p_y.set(float(L.get("y", 0.5)) * 100)
            set_combo(self.p_anim, L.get("anim", "none"))
            self.p_fi.setValue(float(L.get("fade_in") or 0))
            self.p_fo.setValue(float(L.get("fade_out") or 0))
            self.p_start.setValue(float(L.get("start") or 0))
            self.p_end.setValue(float(L.get("end") or 0))
            self.props.head.itemAt(1).layout().itemAt(0).widget().setText("Lớp đang chọn – " +
                                                                           overlay.text_name(L))
        finally:
            self._loading = False
        self._rows()

    def _set(self, key: str, value):
        if self._loading:
            return
        L = self.cur()
        if not L or L.get(key) == value:
            return
        L[key] = value
        if key in ("text", "path", "start", "end", "hidden"):
            self._refresh_list(keep=True)
        self.changed.emit()

    def _pos_preset(self, *_):
        code = self.p_pos.currentData()
        L = self.cur()
        if not code or not L:
            return
        x, y = _PX[code[1]], _PY[code[0]]
        if L.get("kind") == "logo":
            w = float(L.get("w") or 0.16)
            x = {"l": 0.03 + w / 2, "c": 0.5, "r": 0.97 - w / 2}[code[1]]
            y = {"t": 0.08, "m": 0.5, "b": 0.92}[code[0]]
        L["x"], L["y"] = round(x, 4), round(y, 4)
        set_combo(self.p_pos, "")
        self._load_props()
        self.changed.emit()

    # ================================================================== danh sách lớp
    def set_layers(self, layers: list, keep_sel: bool = True):
        self.layers = [dict(L) for L in layers or []]
        if not keep_sel or not any(L.get("id") == self.sel for L in self.layers):
            self.sel = ""
        self._refresh_list()
        self._load_props()

    def cur(self) -> dict | None:
        return next((L for L in self.layers if L.get("id") == self.sel), None)

    def _refresh_list(self, keep: bool = False):
        self.list.blockSignals(True)
        self.list.clear()
        sel_row = -1
        dur = self.page.duration()
        for k, L in enumerate(reversed(self.layers)):
            a, b = overlay.src_span(L, dur)
            when = "suốt video" if a <= 0.01 and not float(L.get("end") or 0) else f"{_fmt(a)} → {_fmt(b)}"
            it = QListWidgetItem(("🚫 " if L.get("hidden") else "") + overlay.text_name(L) + f"   ·   {when}")
            it.setData(Qt.ItemDataRole.UserRole, L.get("id"))
            self.list.addItem(it)
            if L.get("id") == self.sel:
                sel_row = k
        self.list.setCurrentRow(sel_row)
        self.list.blockSignals(False)
        self.empty.setVisible(not self.layers)

    def _list_row(self, row: int):
        it = self.list.item(row)
        if it:
            self.select(it.data(Qt.ItemDataRole.UserRole), emit=True)

    def select(self, lid: str, emit: bool = False):
        self.sel = lid or ""
        for k in range(self.list.count()):
            if self.list.item(k).data(Qt.ItemDataRole.UserRole) == self.sel:
                self.list.blockSignals(True)
                self.list.setCurrentRow(k)
                self.list.blockSignals(False)
        self._load_props()
        if emit:
            self.selected.emit(self.sel)

    def _new_span(self) -> tuple[float, float]:
        if self.when.currentData() == "here":
            t = self.page.player.position()
            return round(t, 3), round(t + 5.0, 3)
        return 0.0, 0.0

    def _append(self, L: dict):
        L["start"], L["end"] = self._new_span() if not (L.get("start") or L.get("end")) else (L["start"], L["end"])
        if L.get("anim") == "scroll" and not L.get("end"):
            L["start"], L["end"] = 0.0, 0.0
        self.layers.append(L)
        self.sel = L["id"]
        self._refresh_list()
        self._load_props()
        self.changed.emit()
        self.selected.emit(self.sel)

    def add_text(self, template: str | None, channel: bool = False):
        L = overlay.new_layer("text", template=template, channel=self.channel.text() if channel else "")
        if not template:
            L.update(text="Chữ của bạn", outline=0.1, name="Chữ")
        self._append(L)
        if not channel:
            self.p_text.setFocus()
            self.p_text.selectAll()

    def add_logo_path(self, path: str, x: float | None = None, y: float | None = None):
        L = overlay.new_layer("logo", path=str(path), name=Path(path).stem[:30])
        if x is not None:
            L["x"], L["y"] = round(x, 4), round(y, 4)
        self._append(L)

    def add_logo_item(self, it):
        lib = next((d for d in overlay.logo_library() if d["id"] == it.data(Qt.ItemDataRole.UserRole)), None)
        if lib:
            self.add_logo_path(str(overlay.logo_path(lib)))

    def drop(self, data: str, x: float, y: float):
        """Thả logo vào video: 'lib:<mã>' (thư viện) hoặc 'file:<đường dẫn>' (kéo file từ máy)."""
        if data.startswith("lib:"):
            lib = next((d for d in overlay.logo_library() if d["id"] == data[4:]), None)
            if lib:
                self.add_logo_path(str(overlay.logo_path(lib)), x, y)
        elif data.startswith("file:"):
            path = data[5:]
            if Path(path).suffix.lower() in overlay.IMAGE_EXTS:
                new = overlay.add_logos([path])
                self.fill_library()
                if new:
                    self.add_logo_path(str(overlay.logo_path(new[0])), x, y)

    def move(self, d: int):
        i = next((k for k, L in enumerate(self.layers) if L.get("id") == self.sel), -1)
        j = i + d
        if i < 0 or not 0 <= j < len(self.layers):
            return
        self.layers[i], self.layers[j] = self.layers[j], self.layers[i]
        self._refresh_list()
        self.changed.emit()

    def duplicate(self):
        L = self.cur()
        if not L:
            return
        N = dict(L)
        N["id"] = overlay._uid()
        N["x"] = min(1.0, float(L.get("x", 0.5)) + 0.03)
        N["y"] = min(1.0, float(L.get("y", 0.5)) + 0.03)
        i = self.layers.index(L)
        self.layers.insert(i + 1, N)
        self.sel = N["id"]
        self._refresh_list()
        self._load_props()
        self.changed.emit()
        self.selected.emit(self.sel)

    def toggle_hidden(self):
        L = self.cur()
        if L:
            L["hidden"] = not L.get("hidden")
            self._refresh_list()
            self.changed.emit()

    def delete(self, lid: str | None = None):
        lid = lid or self.sel
        n = len(self.layers)
        self.layers = [L for L in self.layers if L.get("id") != lid]
        if len(self.layers) != n:
            if self.sel == lid:
                self.sel = ""
            self._refresh_list()
            self._load_props()
            self.changed.emit()
            self.selected.emit(self.sel)

    def full_span(self, lid: str | None = None):
        L = next((x for x in self.layers if x.get("id") == (lid or self.sel)), None)
        if L:
            L["start"], L["end"] = 0.0, 0.0
            self._refresh_list()
            self._load_props()
            self.changed.emit()

    def set_span(self, lid: str, a: float, b: float):
        L = next((x for x in self.layers if x.get("id") == lid), None)
        if not L:
            return
        dur = self.page.duration()
        L["start"] = round(max(0.0, a), 3)
        L["end"] = 0.0 if dur and b >= dur - 0.05 else round(b, 3)
        self._refresh_list()
        if lid == self.sel:
            self._load_props()
        self.changed.emit()

    def update_layer(self, lid: str, ch: dict):
        """Kéo / phóng lớp trên video -> cập nhật ô thuộc tính."""
        L = next((x for x in self.layers if x.get("id") == lid), None)
        if not L:
            return
        L.update(ch)
        if lid == self.sel:
            self._load_props()
        self.changed.emit()

    # ================================================================== thư viện logo
    def fill_library(self):
        self.logo_list.clear()
        for d in overlay.logo_library():
            img = overlay.load_image(overlay.logo_path(d), 128)
            pm = QPixmap.fromImage(img.scaled(64, 64, Qt.AspectRatioMode.KeepAspectRatio,
                                              Qt.TransformationMode.SmoothTransformation)) if not img.isNull() else QPixmap()
            it = QListWidgetItem(QIcon(pm), (d.get("name") or "")[:14] + (" (GIF)" if d.get("anim") else ""))
            it.setData(Qt.ItemDataRole.UserRole, d["id"])
            it.setToolTip(f"{d.get('name')} · {d.get('w')}×{d.get('h')}" + (" · GIF động" if d.get("anim") else "")
                          + "\nBấm đúp hoặc kéo thả vào video")
            self.logo_list.addItem(it)
        if not self.logo_list.count():
            it = QListWidgetItem("Chưa có logo – bấm 'Tải logo lên…'")
            it.setFlags(Qt.ItemFlag.NoItemFlags)
            self.logo_list.addItem(it)

    def _upload_logos(self):
        paths, _ = QFileDialog.getOpenFileNames(self, "Chọn logo (chọn được nhiều file)", "",
                                                "Ảnh (*.png *.jpg *.jpeg *.webp *.svg *.gif *.bmp)")
        if not paths:
            return
        new = overlay.add_logos(paths)
        self.fill_library()
        self.page.win.toast(f"Đã thêm {len(new)} logo vào thư viện" if new else "Không đọc được ảnh nào",
                            "ok" if new else "err")

    def _add_selected_logos(self):
        items = [it for it in self.logo_list.selectedItems() if it.data(Qt.ItemDataRole.UserRole)]
        if not items:
            self.page.win.toast("Chọn 1 hoặc nhiều logo trong thư viện trước", "warn")
        for it in items:
            self.add_logo_item(it)

    def _remove_logos(self):
        items = [it for it in self.logo_list.selectedItems() if it.data(Qt.ItemDataRole.UserRole)]
        if not items:
            return
        if QMessageBox.question(self, "Xoá logo", f"Xoá {len(items)} logo khỏi thư viện?\n(Logo đang dùng trên video "
                                "của các dự án sẽ không còn ảnh.)") != QMessageBox.StandardButton.Yes:
            return
        for it in items:
            overlay.remove_logo(it.data(Qt.ItemDataRole.UserRole))
        self.fill_library()

    def _change_logo(self):
        L = self.cur()
        if not L:
            return
        path, _ = QFileDialog.getOpenFileName(self, "Chọn ảnh logo", "", "Ảnh (*.png *.jpg *.jpeg *.webp *.svg *.gif)")
        if path:
            new = overlay.add_logos([path])
            self.fill_library()
            if new:
                self._set("path", str(overlay.logo_path(new[0])))
                self._load_props()

    def _channel_changed(self, t: str):
        for b in self.ch_btns:
            b.set_channel(t)
        try:
            from ..config import save_settings

            save_settings({"channel_name": t})
            self.page.settings["channel_name"] = t
        except Exception:  # noqa: BLE001
            pass
