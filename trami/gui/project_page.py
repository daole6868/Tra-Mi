"""Trang làm việc với 1 dự án: trình phát + 7 tab chức năng."""
from __future__ import annotations

import time
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, QUrl
from PySide6.QtGui import QDesktopServices, QFont, QKeySequence, QPixmap, QShortcut
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QDoubleSpinBox, QFileDialog,
                               QHeaderView, QInputDialog, QLineEdit, QMessageBox, QPlainTextEdit,
                               QScrollArea, QSpinBox, QSplitter, QStackedWidget, QTableView, QTableWidget,
                               QTableWidgetItem, QVBoxLayout, QWidget, QGridLayout)

from .. import pipeline
from ..config import _deep_merge, load_settings
from ..jobs import manager
from ..project import Project
from ..subtitle import Segment, load_subtitles, normalize, save_subtitles
from . import common as C
from . import icons, theme
from .common import run_async
from .editors import RegionEditor, Timeline
from .player import Player, fmt
from .subs_model import (C_END, C_FIT, C_NODUB, C_SEL, C_SONG, C_SPK, C_START, C_VI, C_VOICE, C_ZH, SpeakerDelegate,
                         SubsFilter, SubsModel, VoiceDelegate)
from .widgets import (Card, ColorButton, Form, GradientButton, IconButton, IconTabs, PillTabs, ProgressBar,
                      SectionCard, SliderRow, Switch, button, combo, flow, hbox, label, set_combo, wrap)


def safe_stem(p: Project) -> str:
    return (p.source.stem if p.data.get("source") else p.data.get("name", "video"))[:60]


def scroll(w: QWidget) -> QScrollArea:
    sa = QScrollArea()
    sa.setWidgetResizable(True)
    sa.setWidget(w)
    sa.setFrameShape(QScrollArea.Shape.NoFrame)
    sa.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    return sa


class ProjectPage(QWidget):
    def __init__(self, win):
        super().__init__()
        self.win = win
        self.p: Project | None = None
        self.loading = False
        self.settings = load_settings()
        self.cut_a: float | None = None

        self._save_edit_timer = QTimer(self, singleShot=True, interval=500, timeout=self._save_edit_now)
        self._live_timer = QTimer(self, singleShot=True, interval=40, timeout=self._apply_live)
        self._save_segs_timer = QTimer(self, singleShot=True, interval=800, timeout=self._save_segs_now)

        # ---------------- thanh trên cùng: tên dự án · trạng thái lưu · Xuất (như trình dựng phim)
        self.name = QLineEdit()
        self.name.setFont(theme.font(14, QFont.Weight.ExtraBold))
        self.name.setStyleSheet("background: transparent; border: 1px solid transparent; padding: 2px 6px;")
        self.name.editingFinished.connect(self._rename)
        self.saved = label("", "muted")
        export_btn = GradientButton("Xuất video", "export")
        export_btn.clicked.connect(lambda: self.tabs.set_current(7))
        b_dir = IconButton("folder", "Mở thư mục dự án", 34)
        b_dir.clicked.connect(self._open_dir)
        b_del = IconButton("trash", "Xoá dự án", 34)
        b_del.clicked.connect(self._delete)
        self.insp_btn = IconButton("panel_right", "Ẩn / hiện khung \"Câu đang chọn\" bên phải", 34)
        self.insp_btn.clicked.connect(lambda: self._show_inspector(not self.insp_box.isVisible()))
        head = hbox(self.name, self.saved, None, self.insp_btn, b_dir, b_del, export_btn)
        head.setSpacing(8)

        # ---------------- giữa: khung xem trước (trình phát) + tiến độ tác vụ
        self.player = Player()
        self.player.positionChanged.connect(self._on_pos)
        self.player.mediaError.connect(self._media_error)
        self.ov_which = combo([("trans", "Xem: Tiếng Việt"), ("both", "Xem: Song ngữ"), ("source", "Xem: Tiếng Trung")])
        self.ov_which.currentIndexChanged.connect(self._refresh_overlay)
        self.ov_show = Switch(True)
        self.ov_show.toggledOn.connect(self._refresh_overlay)
        self.info = label("", "muted")
        center = Card(padding=theme.PANEL_PAD)
        center.add(hbox(label("Xem trước", "h3"), None, self.info))
        center.add(self.player)
        center.add(hbox(label("Hiện phụ đề", "muted"), self.ov_show, self.ov_which, None))
        self.job_box = Card(padding=theme.PANEL_PAD)
        self.job_title = label("", "h3")
        self.job_msg = label("", "muted", wrap=True)
        self.job_bar = ProgressBar()
        self.job_cancel = button("Huỷ", "x", flat=True)
        self.job_box.add(hbox(self.job_title, None, self.job_cancel))
        self.job_box.add(self.job_bar)
        self.job_box.add(self.job_msg)
        self.job_box.hide()
        self._cur_job = None
        self.job_cancel.clicked.connect(lambda: manager.cancel(self._cur_job) if self._cur_job else None)
        ccol = QVBoxLayout()
        ccol.setContentsMargins(0, 0, 0, 0)   # không thụt vào: mép khung Xem trước thẳng hàng với các khung bên cạnh
        ccol.setSpacing(theme.GAP)
        ccol.addWidget(center, 1)
        ccol.addWidget(self.job_box)

        # ---------------- trái: thanh công cụ dạng icon + nội dung từng bước
        self.tabs = IconTabs([("Tự động", "bolt"), ("Phụ đề", "subtitles"), ("Xoá phụ đề", "eraser"),
                              ("Chỉnh sửa", "scissors"), ("Kiểu chữ", "palette"), ("Lồng tiếng", "mic"),
                              ("Tài liệu", "download"), ("Xuất", "export")])
        self.stack = QStackedWidget()
        for build in (self._tab_auto, self._tab_subs, self._tab_hardsub, self._tab_edit, self._tab_style,
                      self._tab_dub, self._tab_models, self._tab_export):
            self.stack.addWidget(build())
        self.tabs.changed.connect(self._tab_changed)
        tools = Card(padding=theme.PANEL_PAD)
        tools.add(self.tabs)
        tools.add(self.stack)
        tools.lay.setStretch(1, 1)
        tools.setMinimumWidth(700)   # kéo vách ngăn không làm nội dung các tab bị cắt (tab rộng nhất ~670 px)

        # ---------------- phải: thuộc tính câu đang chọn
        from .inspector import LineInspector

        self._sel_row = -1
        self.inspector = LineInspector(self.model, lambda: [(v["id"], v["name"]) for v in self._voices])
        self.inspector.navigate.connect(lambda r: self._select_line(r, seek=True))
        self.inspector.action.connect(self._line_action)
        self.inspector.setMinimumWidth(260)

        # khe giữa các khung = vách ngăn kéo được, cùng 1 cỡ theo cả 2 chiều (theme.GAP)
        top = QSplitter(Qt.Orientation.Horizontal)
        top.setHandleWidth(theme.GAP)
        top.addWidget(tools)
        top.addWidget(wrap(ccol))
        self.insp_box = scroll(self.inspector)
        self.insp_box.setMinimumWidth(270)
        top.addWidget(self.insp_box)
        top.setStretchFactor(0, 9)
        top.setStretchFactor(1, 8)
        top.setStretchFactor(2, 4)
        # ẩn khung bên phải bằng nút trên thanh đầu (ẩn hẳn -> vách ngăn cũng ẩn, mép phải các khung thẳng hàng)
        top.setChildrenCollapsible(False)
        self.top_split = top

        # ---------------- dưới: timeline nhiều track trải ngang
        from .timeline import TrackTimeline

        self.tracks = TrackTimeline()
        self.tracks.seekRequested.connect(self.player.seek)
        self.tracks.lineClicked.connect(lambda r: self._select_line(r, seek=True))
        self.tracks.lineActivated.connect(lambda r: (self._select_line(r), self._play_line(r)))
        self.tracks.actionRequested.connect(self._timeline_action)
        self.tracks.dubMoved.connect(self._dub_moved)
        self.tracks.dubReset.connect(lambda r: self._dub_moved(r, None))
        bottom = Card(padding=theme.PANEL_PAD)
        bottom.add(self.tracks)
        self.tl_card = bottom

        vsplit = QSplitter(Qt.Orientation.Vertical)
        vsplit.setHandleWidth(theme.GAP)
        vsplit.addWidget(top)
        vsplit.addWidget(bottom)
        vsplit.setStretchFactor(0, 7)
        vsplit.setStretchFactor(1, 3)
        vsplit.setChildrenCollapsible(False)
        self.v_split = vsplit
        self._sized = False

        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 10, 16, 14)
        lay.setSpacing(theme.GAP)
        lay.addLayout(head)
        lay.addWidget(vsplit, 1)

        QShortcut(QKeySequence("Ctrl+S"), self, activated=lambda: (self._save_segs_now(), self._save_edit_now(),
                                                                   self.win.toast("Đã lưu", "ok")))

    def showEvent(self, e):
        super().showEvent(e)
        if not self._sized and self.width() > 400:
            # chia chỗ lần đầu theo cỡ màn hình: timeline ~28% chiều cao; màn hẹp thì thu khung "Câu đang chọn"
            self._sized = True
            W, H = self.width(), self.height()
            need = self.tl_card.minimumSizeHint().height()   # đủ chỗ cho cả 4 dòng timeline, không bị cắt
            bottom = max(need, int(min(320, H * 0.32)))
            self.v_split.setSizes([max(300, H - bottom), bottom])
            self._show_inspector(W >= 1600)

    def _show_inspector(self, on: bool):
        """Hiện / ẩn khung "Câu đang chọn" và chia lại chiều ngang (màn hẹp mở đầu ẩn)."""
        self.insp_box.setVisible(on)
        W = max(600, self.top_split.width() or self.width())
        if on:
            self.top_split.setSizes([int(W * 0.44), int(W * 0.38), max(290, int(W * 0.18))])
        else:
            self.top_split.setSizes([int(W * 0.55), int(W * 0.45), 0])

    # ================================================================== TAB: Tự động
    def _tab_auto(self):
        s = self.settings
        card = SectionCard("Dịch tự động trọn gói", "bolt", "Chọn tuỳ chọn rồi bấm Bắt đầu. Có thể làm từng bước bên dưới.")
        f = Form()
        self.a_method = f.row("Nguồn phụ đề", combo(C.METHODS, "asr"))
        self.a_asr = f.row("Bộ nhận dạng", combo(C.ASR_ENGINES, s.get("asr_engine")))
        self.a_ocrfps = f.row("Tốc độ quét phụ đề", combo(C.OCR_SPEEDS, str(s.get("ocr_fps", "4"))),
                              "Khi lấy phụ đề bằng OCR: quét vùng chữ bao nhiêu lần mỗi giây. Câu hiện rất nhanh "
                              "(dưới 0,5 giây) thì chọn Kỹ; video dài, câu hiện lâu thì chọn Nhanh")
        self.a_ocrfps.setEnabled(False)
        self.a_lang = f.row("Ngôn ngữ gốc", combo(C.LANGS, "zh"))
        self.a_sep = f.row("Tách giọng khỏi nhạc trước", Switch(bool(s.get("sep_before_asr", True))),
                           "Nhận dạng chính xác hơn với video có nhạc nền; cũng dùng để lồng tiếng giữ nhạc gốc")
        self.a_spk = f.row("Phân biệt người nói", Switch(bool(s.get("diarize", True))))
        self.a_engine = f.row("Công cụ dịch", combo(C.ENGINES + [(pipeline.NO_TRANSLATE, "Không dịch – chỉ nghe & tách câu, "
                                                                 "xuất SRT gốc (dịch ở phần mềm khác)")],
                                                    s.get("translate_engine")))
        self.a_style = f.row("Thể loại / văn phong", combo(C.STYLES, s.get("translate_style")))
        self.a_hardsub = f.row("Phụ đề Trung cũ", combo(C.HARDSUB, "none"))
        self.a_which = f.row("Ghép phụ đề vào video", combo(C.WHICH, "trans"))
        self.a_dub = f.row("Lồng tiếng Việt", Switch(False))
        self.a_tts = f.row("Bộ đọc lồng tiếng", combo(C.TTS_ENGINES, s.get("tts_engine", "edge")))
        self.a_denoise = f.row("Lọc nhiễu âm thanh", Switch(False))
        self.a_force = f.row("Nhận dạng lại từ đầu", Switch(False))
        self.a_method.currentIndexChanged.connect(
            lambda: (self.a_asr.setEnabled(self.a_method.currentData() == "asr"),
                     self.a_ocrfps.setEnabled(self.a_method.currentData() == "ocr")))
        card.add(f)
        go = GradientButton("Bắt đầu dịch tự động", "bolt", big=True)
        go.clicked.connect(self.run_auto)
        card.add(go)
        steps = SectionCard("Làm từng bước", "list",
                            "Đi lần lượt 4 bước, soát kết quả từng bước trước khi sang bước sau (bước nào xong có dấu ✓).")
        grid = QGridLayout()
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(4)
        step_defs = [
            ("1. Nghe && tách câu", "mic", self.run_recognize,
             "Nghe lời thoại gốc: ghi mốc lúc nhân vật BẮT ĐẦU nói tới lúc DỨT câu, tách đúng từng câu (và từng người nói)."),
            ("2. Dịch", "globe", lambda: self.run_translate(False),
             "Dịch từng câu sang tiếng Việt, độ dài vừa với thời gian nhân vật nói."),
            ("3. Lồng tiếng", "volume", self._step_dub,
             "Đọc từng câu tiếng Việt và đặt đúng vào thời điểm nhân vật nói (không chồng câu)."),
            ("4. Ghép vào video && xuất", "export", self._step_export,
             "Ghép giọng lồng + nhạc nền + phụ đề vào video, xuất file MP4."),
        ]
        self.step_status = []
        for i, (title, ic, fn, tip) in enumerate(step_defs):
            b = button(title, ic, tip, on_click=fn)
            st = label("", "muted", wrap=True)
            st.setToolTip(tip)
            grid.addWidget(b, 0, i)
            grid.addWidget(st, 1, i)
            grid.setColumnStretch(i, 1)
            self.step_status.append(st)
        steps.add(grid)
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(0, 0, 2, 0)
        v.setSpacing(theme.GAP)
        v.addWidget(card)
        v.addWidget(steps)
        v.addStretch(1)
        return scroll(w)

    # ================================================================== TAB: Phụ đề
    def _tab_subs(self):
        self.model = SubsModel()
        self.model.edited.connect(self._segs_edited)
        self.proxy = SubsFilter()
        self.proxy.setSourceModel(self.model)
        tv = QTableView()
        tv.setModel(self.proxy)
        tv.setAlternatingRowColors(True)
        tv.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        tv.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        tv.setWordWrap(True)
        tv.setTextElideMode(Qt.TextElideMode.ElideNone)
        tv.verticalHeader().hide()
        tv.verticalHeader().setDefaultSectionSize(40)
        hh = tv.horizontalHeader()
        for c, w in ((0, 40), (C_START, 92), (C_END, 92), (C_SPK, 58), (C_SONG, 34), (C_NODUB, 64), (C_FIT, 74)):
            hh.setSectionResizeMode(c, QHeaderView.ResizeMode.Fixed)
            tv.setColumnWidth(c, w)
        hh.setSectionResizeMode(C_ZH, QHeaderView.ResizeMode.Stretch)
        hh.setSectionResizeMode(C_VI, QHeaderView.ResizeMode.Stretch)
        tv.setItemDelegateForColumn(C_SPK, SpeakerDelegate(tv))
        tv.setEditTriggers(QAbstractItemView.EditTrigger.DoubleClicked | QAbstractItemView.EditTrigger.EditKeyPressed |
                           QAbstractItemView.EditTrigger.AnyKeyPressed)
        tv.setColumnHidden(C_SEL, True)      # 2 cột này dùng ở bảng câu của tab Lồng tiếng
        tv.setColumnHidden(C_VOICE, True)
        tv.clicked.connect(self._row_clicked)
        self.table = tv
        self.search = QLineEdit()
        self.search.setPlaceholderText("Tìm trong phụ đề… (gõ S1 để lọc theo người nói)")
        self.search.textChanged.connect(self.proxy.set_query)
        self.follow = Switch(True)
        self.seg_count = label("", "muted")
        bar1 = flow(button("Dịch dòng trống", "globe", on_click=lambda: self.run_translate(True)),
                    button("Dịch lại tất cả", "refresh", on_click=lambda: self.run_translate(False)),
                    button("Dịch câu chọn", "sparkle", on_click=self._retranslate_one),
                    button("Nhập SRT", "download", "Nhập file phụ đề SRT/ASS/VTT", on_click=self._import_subs),
                    button("Lưu", "save", on_click=lambda: (self._save_segs_now(), self.win.toast("Đã lưu phụ đề", "ok"))))
        bar2 = flow(button("Thêm", "plus", "Thêm câu tại vị trí đang phát", on_click=lambda: self._seg_op("insert")),
                    button("Gộp", "merge", "Gộp câu đang chọn với câu sau", on_click=lambda: self._seg_op("merge")),
                    button("Tách", "split", "Tách câu tại vị trí đang phát", on_click=lambda: self._seg_op("split")),
                    button("Xoá", "trash", "Xoá câu đang chọn", danger=True, on_click=lambda: self._seg_op("delete")),
                    button("Dời giờ", "clock", on_click=self._shift),
                    button("Căn khớp thoại", "wand", "Căn mốc từng câu sát lời thoại thật, tách câu ở chỗ nghỉ "
                           "(dùng cho phụ đề đã nhận dạng từ bản cũ)", on_click=self.run_realign),
                    button("Tìm && thay", "search", on_click=self._replace))
        self.glossary = QPlainTextEdit()
        self.glossary.setPlaceholderText("Mỗi dòng: 中文=Tiếng Việt   (VD: 李逍遥=Lý Tiêu Dao). AI tự điền khi dịch.")
        self.glossary.setFixedHeight(90)
        self.glossary.textChanged.connect(lambda: self._save_edit_timer.start())
        gl = SectionCard("Bảng tên riêng / thuật ngữ của dự án", "list")
        gl.add(self.glossary)
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(8)
        v.addWidget(bar1)
        v.addWidget(bar2)
        v.addLayout(hbox(self.search, self.seg_count, label("Tự cuộn", "muted"), self.follow))
        v.addWidget(tv, 1)
        v.addWidget(gl)
        return w

    # ================================================================== TAB: Xoá phụ đề cũ
    HS_MODES = [("none", "Không xoá", "x"), ("inpaint", "Xoá sạch (AI)", "sparkle"), ("blur", "Làm mờ", "eye"),
                ("box", "Che dải màu", "stack"), ("delogo", "Delogo", "eraser")]
    HS_HELP = {
        "none": "Giữ nguyên chữ Trung cũ trên video.",
        "inpaint": "AI vẽ lại nền phía sau chữ – sạch nhất. Video bên trái chỉ hiện làm mờ gần đúng; "
                   "bấm 'Xem trước kết quả' để thấy chính xác.",
        "blur": "Làm mờ vùng chữ. Video bên trái hiện gần đúng ngay khi chỉnh.",
        "box": "Phủ một dải màu lên vùng chữ (đặt phụ đề Việt đè lên). Video bên trái hiện đúng như khi xuất.",
        "delogo": "Xoá nhanh bằng bộ lọc delogo của FFmpeg – hợp với logo nhỏ, chữ ít.",
    }

    def _tab_hardsub(self):
        sv = lambda *_: (self._save_edit_timer.start(), self._live_timer.start())  # noqa: E731
        # --- bước 1: khoanh vùng
        area = SectionCard("1. Khoanh vùng chữ cần xoá", "eraser",
                           "Kéo chuột trên ảnh để khoanh vùng phụ đề Trung (khoanh nhiều vùng được, cả logo). "
                           "Vùng khoanh hiện ngay trên video bên trái.")
        self.hs_editor = RegionEditor(multi=True)
        self.hs_editor.changed.connect(sv)
        area.add(self.hs_editor)
        auto_btn = GradientButton("Tự dò vùng phụ đề", "wand")
        auto_btn.clicked.connect(self._detect_region)
        area.add(hbox(auto_btn,
                      button("Chụp khung đang phát", "image", "Lấy khung hình ở vị trí đang phát để khoanh vùng",
                             on_click=self._grab_frame),
                      None,
                      button("Xoá hết vùng", "trash", danger=True,
                             on_click=lambda: (self.hs_editor.set_regions([]), sv()))))
        # --- bước 2: cách xoá
        how = SectionCard("2. Cách xoá", "sparkle")
        self.hs_pills = PillTabs([(t, ic) for _, t, ic in self.HS_MODES])
        self.hs_pills.changed.connect(self._hs_pill_changed)
        how.add(self.hs_pills)
        self.hs_help = label("", "muted", wrap=True)
        how.add(self.hs_help)
        f = Form()
        self.hs_form = f
        self.hs_mode = combo([(k, t) for k, t, _ in self.HS_MODES])   # giữ trạng thái, ẩn (chọn bằng các nút trên)
        self.hs_mode.hide()
        self.hs_backend = f.row("Công nghệ xoá", combo([("lama", "AI LaMa (đẹp, nên có GPU)"), ("opencv", "OpenCV (nhanh)")],
                                                        self.settings.get("inpaint_backend", "lama")))
        self.hs_strength = f.row("Độ mạnh", combo([("light", "Nhẹ"), ("normal", "Vừa"), ("strong", "Mạnh (chữ viền dày)")], "normal"))
        self.hs_useocr = f.row("Chỉ xoá lúc có chữ (OCR)", Switch(True),
                               "Nếu đã OCR, chỉ xoá đúng lúc có chữ → giữ hình gốc ở các đoạn không có phụ đề")
        self.hs_blur = f.row("Độ mờ", SliderRow(4, 60, 1, 20, 0))
        self.hs_color = f.row("Màu dải che", ColorButton("#000000"))
        self.hs_opacity = f.row("Độ đục dải che", SliderRow(0.2, 1, 0.05, 1))
        self.hs_mode.currentIndexChanged.connect(self._hs_mode_changed)
        for w in (self.hs_backend, self.hs_strength):
            w.currentIndexChanged.connect(sv)
        for w in (self.hs_blur, self.hs_opacity):
            w.changed.connect(sv)
        self.hs_color.changed.connect(sv)
        self.hs_useocr.toggledOn.connect(sv)
        how.add(f)
        # --- bước 3: kiểm tra
        chk = SectionCard("3. Kiểm tra kết quả", "eye",
                          "Tạo ảnh kết quả chính xác ở khung hình đang phát (giống hệt khi xuất video).")
        chk.add(hbox(button("Xem trước kết quả", "eye", on_click=self._preview_hardsub),
                     button("Về ảnh gốc", "refresh", on_click=self._clear_preview), None))
        w = QWidget()
        self.hs_mode.setParent(w)
        self.hs_mode.hide()
        v = QVBoxLayout(w)
        v.setContentsMargins(0, 0, 2, 0)
        v.setSpacing(theme.GAP)
        v.addWidget(area)
        v.addWidget(how)
        v.addWidget(chk)
        v.addStretch(1)
        self._hs_mode_changed()
        return scroll(w)

    def _hs_pill_changed(self, i: int):
        set_combo(self.hs_mode, self.HS_MODES[i][0])
        if self.HS_MODES[i][0] != "none" and not self.hs_editor.regions and not self.loading:
            self.win.toast("Khoanh vùng chữ trên ảnh (hoặc bấm 'Tự dò vùng phụ đề') để xoá", "warn")

    def _hs_mode_changed(self, *_):
        mode = self.hs_mode.currentData() or "none"
        i = next((k for k, m in enumerate(self.HS_MODES) if m[0] == mode), 0)
        self.hs_pills.set_current(i, emit=False)
        self.hs_help.setText(self.HS_HELP.get(mode, ""))
        vis = {self.hs_backend: mode == "inpaint", self.hs_strength: mode == "inpaint",
               self.hs_useocr: mode == "inpaint", self.hs_blur: mode == "blur",
               self.hs_color: mode == "box", self.hs_opacity: mode == "box"}
        for wdg, on in vis.items():
            self.hs_form.setRowVisible(wdg, on)
        if not self.loading:
            self._save_edit_timer.start()
            self._live_timer.start()

    # ================================================================== TAB: Chỉnh sửa
    def _tab_edit(self):
        sv = lambda *_: (self._save_edit_timer.start(), self._live_timer.start())  # noqa: E731
        # --- cắt
        cut = SectionCard("Cắt video", "scissors", "Phụ đề và lồng tiếng tự khớp lại sau khi cắt.")
        self.e_start = QDoubleSpinBox()
        self.e_end = QDoubleSpinBox()
        for sp in (self.e_start, self.e_end):
            sp.setRange(0, 1e6)
            sp.setDecimals(2)
            sp.setSuffix(" giây")
            sp.valueChanged.connect(sv)
        f = Form()
        f.row("Bắt đầu từ", hbox(self.e_start, button("Lấy vị trí đang phát", "clock",
                                                     on_click=lambda: self.e_start.setValue(self.player.position()))))
        f.row("Kết thúc (0 = hết)", hbox(self.e_end, button("Lấy vị trí đang phát", "clock",
                                                           on_click=lambda: self.e_end.setValue(self.player.position()))))
        cut.add(f)
        self.timeline = Timeline()
        self.timeline.seekRequested.connect(self.player.seek)
        cut.add(self.timeline)
        self.cut_label = label("Xoá đoạn giữa: đánh dấu điểm đầu rồi điểm cuối", "muted")
        cut.add(hbox(button("[ Đánh dấu đầu", on_click=lambda: self._mark_cut("a")),
                     button("Đánh dấu cuối ]", on_click=lambda: self._mark_cut("b")),
                     button("Bỏ hết đoạn cắt", "trash", on_click=self._clear_cuts), None))
        cut.add(self.cut_label)
        self.cuts_list = label("", "muted", wrap=True)
        cut.add(self.cuts_list)
        # --- khung hình
        frame = SectionCard("Khung hình", "crop")
        f2 = Form()
        self.e_aspect = f2.row("Tỉ lệ khung", combo(C.ASPECTS, "original"))
        self.e_amode = f2.row("Cách lấp đầy", combo(C.ASPECT_MODES, "blur"))
        self.e_res = f2.row("Độ phân giải", combo(C.RESOLUTIONS, "original"))
        self.e_rot = f2.row("Xoay", combo([("0", "0°"), ("90", "90°"), ("180", "180°"), ("270", "270°")], "0"))
        self.e_fliph = f2.row("Lật ngang (gương)", Switch())
        self.e_flipv = f2.row("Lật dọc", Switch())
        self.e_zoom = f2.row("Phóng to", SliderRow(1, 1.5, 0.01, 1))
        self.e_speed = f2.row("Tốc độ", SliderRow(0.5, 2, 0.05, 1, suffix="×"))
        self.crop_info = label("Không crop", "muted")
        f2.row("Crop khung", hbox(button("Chọn vùng crop", "crop", on_click=self._crop_dialog),
                                  button("Bỏ crop", "x", on_click=self._clear_crop), self.crop_info, None))
        frame.add(f2)
        # --- màu
        color = SectionCard("Màu sắc & hình ảnh", "palette")
        f3 = Form()
        self.e_bright = f3.row("Độ sáng", SliderRow(-0.3, 0.3, 0.01, 0))
        self.e_contrast = f3.row("Tương phản", SliderRow(0.5, 1.8, 0.01, 1))
        self.e_sat = f3.row("Bão hoà màu", SliderRow(0, 2, 0.01, 1))
        self.e_gamma = f3.row("Gamma", SliderRow(0.5, 2, 0.01, 1))
        self.e_sharpen = f3.row("Làm nét", Switch())
        self.e_denoise = f3.row("Khử nhiễu hình", Switch())
        color.add(f3)
        # --- âm thanh
        audio = SectionCard("Âm thanh", "volume")
        f4 = Form()
        self.e_volume = f4.row("Âm lượng gốc", SliderRow(0, 2, 0.05, 1))
        self.e_mute = f4.row("Tắt tiếng gốc", Switch())
        self.e_pitch = f4.row("Đổi cao độ giọng gốc", combo([("0", "Không"), ("0.94", "Trầm hơn"), ("1.06", "Cao hơn")], "0"))
        self.bgm_name = label("Chưa chọn", "muted")
        f4.row("Nhạc nền thêm", hbox(button("Chọn nhạc…", "music", on_click=lambda: self._pick_asset("bgm")),
                                     button("", "x", "Bỏ nhạc nền", flat=True, on_click=lambda: self._clear_asset("bgm")),
                                     self.bgm_name, None))
        self.e_bgmvol = f4.row("Âm lượng nhạc nền", SliderRow(0, 1, 0.05, 0.2))
        audio.add(f4)
        # --- logo & chữ
        brand = SectionCard("Logo & chữ", "image")
        f5 = Form()
        self.wm_name = label("Chưa chọn", "muted")
        f5.row("Logo (PNG)", hbox(button("Chọn ảnh…", "image", on_click=lambda: self._pick_asset("watermark")),
                                  button("", "x", "Bỏ logo", flat=True, on_click=lambda: self._clear_asset("watermark")),
                                  self.wm_name, None))
        self.e_wmpos = f5.row("Vị trí logo", combo(C.POSITIONS, "top-right"))
        self.e_wmscale = f5.row("Cỡ logo", SliderRow(0.05, 0.5, 0.01, 0.15))
        self.e_wmop = f5.row("Độ rõ logo", SliderRow(0.1, 1, 0.05, 0.85))
        self.e_text = f5.row("Chữ trên video", QLineEdit())
        self.e_text.setPlaceholderText("VD: Vietsub by …")
        self.e_textpos = f5.row("Vị trí chữ", combo(C.POSITIONS[:5] + [("scroll", "Chữ chạy ngang")], "top-left"))
        self.e_textcolor = f5.row("Màu chữ", ColorButton("#FFFFFF"))
        brand.add(f5)
        fx = SectionCard("Hiệu ứng", "sparkle")
        f6 = Form()
        self.e_fin = f6.row("Mờ dần đầu (giây)", SliderRow(0, 5, 0.1, 0, 1))
        self.e_fout = f6.row("Mờ dần cuối (giây)", SliderRow(0, 5, 0.1, 0, 1))
        fx.add(f6)

        for w in (self.e_aspect, self.e_amode, self.e_res, self.e_rot, self.e_pitch, self.e_wmpos, self.e_textpos):
            w.currentIndexChanged.connect(sv)
        for w in (self.e_zoom, self.e_speed, self.e_bright, self.e_contrast, self.e_sat, self.e_gamma, self.e_volume,
                  self.e_bgmvol, self.e_wmscale, self.e_wmop, self.e_fin, self.e_fout):
            w.changed.connect(sv)
        for w in (self.e_fliph, self.e_flipv, self.e_sharpen, self.e_denoise, self.e_mute):
            w.toggledOn.connect(sv)
        self.e_text.textChanged.connect(sv)
        self.e_textcolor.changed.connect(sv)

        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(0, 0, 2, 0)
        v.setSpacing(theme.GAP)
        for c in (cut, frame, color, audio, brand, fx):
            v.addWidget(c)
        v.addStretch(1)
        return scroll(w)

    # ================================================================== TAB: Kiểu chữ
    def _tab_style(self):
        sv = lambda *_: (self._save_edit_timer.start(), self._live_timer.start())  # noqa: E731
        card = SectionCard("Kiểu phụ đề ghép vào video", "palette", "Xem trước trực tiếp trên khung video bên trái.")
        f = Form()
        self.st_burn = f.row("Ghép phụ đề vào video", Switch(True))
        self.st_which = f.row("Hiển thị", combo(C.WHICH[:3], "trans"))
        self.st_font = f.row("Font chữ", combo([(x, x) for x in ("Be Vietnam Pro", "Arial", "Roboto", "Tahoma",
                                                                  "Segoe UI", "Times New Roman")], "Be Vietnam Pro"))
        self.st_size = f.row("Cỡ chữ (0 = tự động)", QSpinBox())
        self.st_size.setRange(0, 200)
        self.st_pos = f.row("Vị trí", combo([("auto", "Tự động – đúng chỗ phụ đề cũ"), ("bottom", "Dưới"),
                                              ("middle", "Giữa"), ("top", "Trên")], "auto"))
        self.st_margin = f.row("Cách mép (px, 0 = tự động)", QSpinBox())
        self.st_margin.setRange(0, 1000)
        self.st_color = f.row("Màu chữ", ColorButton("#FFFFFF"))
        self.st_ocolor = f.row("Màu viền", ColorButton("#000000"))
        self.st_outline = f.row("Độ dày viền", SliderRow(0, 8, 0.5, 2.5, 1))
        self.st_shadow = f.row("Bóng đổ", SliderRow(0, 5, 0.5, 1, 1))
        self.st_bold = f.row("Chữ đậm", Switch(True))
        self.st_upper = f.row("VIẾT HOA", Switch(False))
        self.st_box = f.row("Nền hộp sau chữ", Switch(False))
        self.st_bcolor = f.row("Màu nền hộp", ColorButton("#000000"))
        self.st_bop = f.row("Độ đục nền hộp", SliderRow(0.1, 1, 0.05, 0.55))
        for w in (self.st_which, self.st_font, self.st_pos):
            w.currentIndexChanged.connect(sv)
        for w in (self.st_size, self.st_margin):
            w.valueChanged.connect(sv)
        for w in (self.st_color, self.st_ocolor, self.st_bcolor):
            w.changed.connect(sv)
        for w in (self.st_outline, self.st_shadow, self.st_bop):
            w.changed.connect(sv)
        for w in (self.st_burn, self.st_bold, self.st_upper, self.st_box):
            w.toggledOn.connect(sv)
        card.add(f)
        presets = SectionCard("Mẫu nhanh", "sparkle")
        presets.add(hbox(button("Trắng viền đen", on_click=lambda: self._style_preset("classic")),
                         button("Vàng nổi bật", on_click=lambda: self._style_preset("yellow")),
                         button("Nền hộp đen", on_click=lambda: self._style_preset("box")),
                         button("TikTok to rõ", on_click=lambda: self._style_preset("tiktok")), None))
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(0, 0, 2, 0)
        v.setSpacing(theme.GAP)
        v.addWidget(card)
        v.addWidget(presets)
        v.addStretch(1)
        return scroll(w)

    # ================================================================== TAB: Lồng tiếng
    def _tab_dub(self):
        sep = SectionCard("1. Tách giọng nói / nhạc nền", "layers",
                          "Giữ nguyên nhạc nền + hiệu ứng gốc, chỉ thay lời thoại bằng tiếng Việt. Bài hát trong phim được giữ nguyên.")
        from ..separate import SEP_MODELS

        self.d_sepmodel = combo([(m, t) for m, t in SEP_MODELS], self.settings.get("sep_model"))
        self.d_sepmodel.setMinimumContentsLength(10)
        self.d_sepstatus = label("Chưa tách", "muted")
        sep.add(hbox(self.d_sepmodel, button("Tách ngay", "layers", on_click=self.run_separate)))
        sep.lay.itemAt(sep.lay.count() - 1).layout().setStretch(0, 1)
        sep.add(flow(self.d_sepstatus,
                     button("Nghe giọng", "volume", flat=True, on_click=lambda: self._open_stem("vocals")),
                     button("Tải MP3 giọng", "download", "Lưu giọng nói đã tách thành file MP3", flat=True,
                            on_click=lambda: self._save_stem_mp3("vocals")),
                     button("Nghe nhạc nền", "music", flat=True, on_click=lambda: self._open_stem("instrumental")),
                     button("Tải MP3 nhạc nền", "download", "Lưu nhạc nền + hiệu ứng đã tách thành file MP3", flat=True,
                            on_click=lambda: self._save_stem_mp3("instrumental"))))
        sep.add(hbox(label("Đã tách ở phần mềm khác (CapCut, UVR…)?", "muted"),
                     button("Nạp file giọng / nhạc nền…", "upload", "Dùng file giọng nói (và nhạc nền nếu có) tách sẵn "
                            "bằng phần mềm khác thay cho bản tách của tool", flat=True, on_click=self._import_stems), None))

        spk = SectionCard("2. Giọng đọc cho từng người nói", "users",
                          "Mỗi nhân vật (S1, S2…) một giọng. Bảng câu bên dưới: bấm đúp ô Tiếng Việt để sửa lời đọc, "
                          "tích cột Chọn để đổi giọng / tạo lại giọng riêng những câu đó – bản lồng tiếng đang dùng được "
                          "cập nhật ngay, trình phát bên trái nghe được luôn.")
        self.d_engine = combo(C.TTS_ENGINES, self.settings.get("tts_engine", "edge"))
        self.d_engine.setMinimumContentsLength(14)
        self.d_engine.currentIndexChanged.connect(self._dub_engine_changed)
        row = hbox(label("Bộ đọc mặc định", "muted"), self.d_engine)
        row.setStretch(1, 1)
        spk.add(row)
        spk.add(hbox(button("Phân biệt người nói", "users", on_click=self.run_diarize),
                     button("Tự gán giọng khác nhau", "wand", on_click=self._auto_voices), None))
        self.spk_table = QTableWidget(0, 4)
        self.spk_table.setHorizontalHeaderLabels(["Người nói", "Số câu", "Giọng lồng", ""])
        self.spk_table.verticalHeader().hide()
        self.spk_table.verticalHeader().setDefaultSectionSize(44)
        hh = self.spk_table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        hh.setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)
        hh.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        hh.setSectionResizeMode(3, QHeaderView.ResizeMode.Fixed)
        self.spk_table.setColumnWidth(0, 90)
        self.spk_table.setColumnWidth(1, 70)
        self.spk_table.setColumnWidth(3, 54)
        self.spk_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        spk.add(self.spk_table)

        # --- bảng từng câu: thời gian nhân vật nói, sửa lời Việt, giọng riêng từng câu
        self.dub_proxy = SubsFilter()
        self.dub_proxy.setSourceModel(self.model)
        dt = QTableView()
        dt.setModel(self.dub_proxy)
        dt.setAlternatingRowColors(True)
        dt.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        dt.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        dt.setWordWrap(True)
        dt.setTextElideMode(Qt.TextElideMode.ElideNone)
        dt.verticalHeader().hide()
        dt.verticalHeader().setDefaultSectionSize(38)
        dh = dt.horizontalHeader()
        for c in (C_ZH, C_SONG):
            dt.setColumnHidden(c, True)
        for c, w in ((C_SEL, 52), (0, 40), (C_START, 88), (C_END, 88), (C_SPK, 58), (C_VOICE, 170), (C_FIT, 70),
                     (C_NODUB, 64)):
            dh.setSectionResizeMode(c, QHeaderView.ResizeMode.Fixed if c != C_VOICE else QHeaderView.ResizeMode.Interactive)
            dt.setColumnWidth(c, w)
        dh.setSectionResizeMode(C_VI, QHeaderView.ResizeMode.Stretch)
        dh.moveSection(dh.visualIndex(C_SEL), 0)                         # Chọn | # | giờ | Người | Giọng | Tiếng Việt…
        dh.moveSection(dh.visualIndex(C_VOICE), dh.visualIndex(C_SPK) + 1)
        dt.setItemDelegateForColumn(C_SPK, SpeakerDelegate(dt))
        dt.setItemDelegateForColumn(C_VOICE, VoiceDelegate(dt, lambda: [(v["id"], v["name"]) for v in self._voices]))
        dt.setEditTriggers(QAbstractItemView.EditTrigger.DoubleClicked | QAbstractItemView.EditTrigger.EditKeyPressed)
        dt.setMinimumHeight(480)
        dt.clicked.connect(self._dub_row_clicked)
        self.dub_table = dt
        self._voices: list[dict] = []
        self.dub_search = QLineEdit()
        self.dub_search.setPlaceholderText("Lọc câu… (gõ S2 để xem câu của người nói S2)")
        self.dub_search.textChanged.connect(self.dub_proxy.set_query)
        self.d_picked = label("Chưa tích câu nào", "muted")
        self.model.picked.connect(self._update_picked)
        spk.add(label("Các câu lồng tiếng", "h3"))
        spk.add(hbox(self.dub_search,
                     button("Tích các câu đang lọc", "check", "Tích tất cả câu đang hiện trong bảng",
                            flat=True, on_click=self._pick_visible),
                     button("Tích câu ⚠ quá dài", "info", "Tích các câu đọc quá dài so với thời gian nhân vật nói",
                            flat=True, on_click=self._pick_long),
                     button("Bỏ tích", "x", flat=True, on_click=lambda: self.model.set_picked(range(len(self.model.segs)), False))))
        spk.add(dt)
        self.d_line_voice = QComboBox()
        self.d_line_voice.setMinimumContentsLength(16)
        spk.add(flow(self.d_picked, hbox(label("Giọng cho câu đã tích", "muted"), self.d_line_voice),
                     button("Đổi giọng", "users", "Đặt giọng riêng cho các câu đã tích (chưa đọc lại)",
                            on_click=self._set_line_voice),
                     button("AI rút gọn", "sparkle", "AI viết lại ngắn hơn các câu đã tích cho vừa thời gian nhân vật "
                            "nói (cần công cụ dịch AI)", on_click=self._shorten_picked),
                     button("Nghe câu", "play", "Phát câu đang chọn trên trình phát bên trái", on_click=self._play_line)))
        regen = GradientButton("Tạo lại giọng các câu đã tích", "refresh")
        regen.setToolTip("Đọc lại riêng các câu đã tích theo lời Việt + giọng hiện tại, cập nhật ngay vào bản lồng "
                         "tiếng đang dùng (không đọc lại cả video)")
        regen.clicked.connect(self._redub_picked)
        spk.add(regen)
        spk.add(flow(label("Phụ đề", "muted"),
                     button("Xuất SRT gốc (chưa dịch)", "upload", "Lấy lời gốc đã tách câu để dịch ở phần mềm khác",
                            on_click=lambda: self._export_sub("srt", "source")),
                     button("Xuất SRT đã dịch", "upload", on_click=lambda: self._export_sub("srt", "trans")),
                     button("Xuất SRT song ngữ", "upload", on_click=lambda: self._export_sub("srt", "both")),
                     button("Nạp SRT đã dịch…", "download", "Nạp bản dịch làm ở phần mềm khác vào các câu hiện có "
                            "(giữ nguyên lời gốc, thời gian, người nói)", on_click=self._import_translation)))

        mix = SectionCard("3. Tạo bản lồng tiếng & trộn âm thanh", "mic",
                          "Mỗi câu phụ đề được đọc thành 1 đoạn giọng riêng và ghép đúng lúc nhân vật nói – xem từng đoạn "
                          "ở dòng \"Lồng tiếng\" trên timeline phía dưới: bấm đúp để nghe, kéo sang trái / phải để dời.")
        f = Form()
        self.d_rate = f.row("Tốc độ đọc", combo([("-10%", "Chậm"), ("+0%", "Bình thường"), ("+10%", "Nhanh"), ("+20%", "Rất nhanh")], "+0%"))
        self.d_skipsong = f.row("Giữ nguyên bài hát (không lồng câu 🎵)", Switch(bool(self.settings.get("dub_skip_songs", True))))
        self.d_bg = f.row("Âm lượng nhạc nền (đã tách)", SliderRow(0, 1.5, 0.05, 1))
        self.d_voc = f.row("Giọng Trung gốc còn lại", SliderRow(0, 1, 0.05, 0))
        self.d_orig = f.row("Tiếng gốc (khi chưa tách)", SliderRow(0, 1, 0.05, 0.15))
        self.d_vol = f.row("Âm lượng giọng lồng", SliderRow(0.2, 2, 0.05, 1))
        self.d_enabled = f.row("Dùng bản lồng tiếng (xem trước + khi xuất)", Switch(False))
        for w in (self.d_bg, self.d_voc, self.d_orig, self.d_vol):
            w.changed.connect(lambda *_: self._save_edit_timer.start())
        self.d_enabled.toggledOn.connect(lambda *_: self._save_edit_timer.start())
        self.d_skipsong.toggledOn.connect(self._skip_song_changed)
        mix.add(f)
        gen = GradientButton("Tạo bản lồng tiếng", "mic", big=True)
        gen.clicked.connect(self.run_dub)
        self.d_status = label("", "muted", wrap=True)   # dòng thống kê dài tự xuống dòng, không đẩy thẻ rộng ra
        mix.add(gen)
        mix.add(self.d_status)
        mix.add(flow(button("Nghe bản lồng", "volume", flat=True, on_click=self._open_dub),
                     button("Tải MP3 bản lồng", "download", "Lưu bản lồng tiếng đã trộn nhạc nền thành MP3", flat=True,
                            on_click=self._save_dub_mp3)))

        hist = SectionCard("4. Các bản lồng tiếng đã tạo", "list",
                           "Mỗi lần bấm 'Tạo bản lồng tiếng' = 1 bản MỚI. 'Tạo lại giọng các câu đã tích' sửa THẲNG vào "
                           "bản đang dùng (dấu ✓), không tạo bản mới. Bản có dấu ✓ được dùng cho trình phát và khi xuất video.")
        self.d_versions_box = QWidget()
        self.d_versions = QVBoxLayout(self.d_versions_box)
        self.d_versions.setContentsMargins(0, 0, 0, 0)
        self.d_versions.setSpacing(6)
        hist.add(self.d_versions_box)
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(0, 0, 2, 0)
        v.setSpacing(theme.GAP)
        from .voice_samples import VoiceSamplesCard

        self.voice_samples = VoiceSamplesCard(self)
        v.addWidget(sep)
        v.addWidget(spk)
        v.addWidget(self.voice_samples)
        v.addWidget(mix)
        v.addWidget(hist)
        v.addStretch(1)
        return scroll(w)

    # ================================================================== TAB: Tài liệu (mô hình AI)
    def _tab_models(self):
        from .models_tab import ModelsTab

        self.models_tab = ModelsTab(self.win)
        return scroll(self.models_tab)

    # ================================================================== TAB: Xuất
    def _tab_export(self):
        card = SectionCard("Xuất video", "film", "Một lần encode duy nhất: giữ chất lượng tốt nhất.")
        f = Form()
        self.x_name = f.row("Tên file", QLineEdit())
        self.x_name.setPlaceholderText("(tự động: tên video + _vietsub)")
        self.x_crf = f.row("Chất lượng", combo([("16", "Rất cao (file lớn)"), ("20", "Cao (khuyên dùng)"),
                                                ("23", "Trung bình"), ("28", "Nhẹ")], "20"))
        card.add(f)
        self.x_summary = label("", "muted", wrap=True)
        card.add(self.x_summary)
        go = GradientButton("Xuất video", "film", big=True)
        go.clicked.connect(self.run_render)
        card.add(go)
        subs = SectionCard("Xuất phụ đề", "subtitles")
        subs.add(hbox(button("SRT tiếng Việt", on_click=lambda: self._export_sub("srt", "trans")),
                      button("SRT tiếng Trung", on_click=lambda: self._export_sub("srt", "source")),
                      button("SRT song ngữ", on_click=lambda: self._export_sub("srt", "both")), None))
        subs.add(hbox(button("ASS có kiểu chữ", on_click=lambda: self._export_sub("ass", None)),
                      button("VTT", on_click=lambda: self._export_sub("vtt", "trans")),
                      button("TXT", on_click=lambda: self._export_sub("txt", "trans")), None))
        tools = SectionCard("Công cụ khác", "settings")
        tools.add(hbox(button("Chia thành nhiều phần…", "split", on_click=lambda: self._tool("split_parts")),
                       button("Tách MP3", "music", on_click=lambda: self._tool("extract_audio")), None))
        outs = SectionCard("Kết quả", "check")
        self.outs = QTableWidget(0, 3)
        self.outs.setHorizontalHeaderLabels(["File", "", ""])
        self.outs.verticalHeader().hide()
        self.outs.verticalHeader().setDefaultSectionSize(42)
        self.outs.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.outs.setColumnWidth(1, 80)
        self.outs.setColumnWidth(2, 120)
        self.outs.setMinimumHeight(160)
        self.outs.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        outs.add(self.outs)
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(0, 0, 2, 0)
        v.setSpacing(theme.GAP)
        for c in (card, subs, tools, outs):
            v.addWidget(c)
        v.addStretch(1)
        return scroll(w)

    # ================================================================== nạp dự án
    def open(self, pid: str):
        same = self.p is not None and self.p.id == pid
        self.p = Project(pid)
        if not self.p.exists:
            self.win.toast("Không tìm thấy dự án", "err")
            return
        self.settings = load_settings()
        self.populate(reload_media=not same)

    def reload(self):
        if self.p:
            self.p.reload()
            self.populate(reload_media=False)

    def populate(self, reload_media: bool = True):
        p = self.p
        self.loading = True
        try:
            self.name.setText(p.data.get("name", ""))
            i = p.data.get("info", {})
            self.info.setText(f"{i.get('width', 0)}×{i.get('height', 0)} · {fmt(i.get('duration', 0), False)} · "
                              f"{i.get('fps', 0):.2f} fps" if i.get("duration") else "Đang chờ video…")
            if reload_media:
                self._load_media()
                self._sel_row = -1
                self.tracks.set_thumbs(None, 0, 0)
                self.tracks.set_wave(None, 0.05)
            self.model.set_segments(p.segments)
            self._fit_rows()
            self._update_count()
            self.glossary.setPlainText(p.data.get("glossary", ""))
            self._fill_edit()
            self._fill_speakers()
            self._fill_outputs()
            self._update_summary()
            self._update_timeline()
            self._refresh_overlay()
            self.inspector.set_row(self._sel_row)
            self.tracks.set_selected(self._sel_row)
        finally:
            self.loading = False
        self._apply_live()
        self._load_timeline_assets()

    def _load_timeline_assets(self):
        """Ảnh thu nhỏ + sóng âm cho timeline (tạo 1 lần ở nền, có sẵn thì nạp ngay)."""
        if not self.p or not self.p.data.get("source"):
            return
        pid = self.p.id

        def done(a):
            if not a or not self.p or self.p.id != pid:
                return
            if a.get("thumbs"):
                self.tracks.set_thumbs(QPixmap(a["thumbs"]), a["step"], a["count"])
            if a.get("wave"):
                import numpy as np

                self.tracks.set_wave(np.load(a["wave"]), a["hop"])
        run_async(lambda: pipeline.timeline_assets(Project(pid)), done, lambda m: None)

    def _load_media(self):
        p = self.p
        src = p.work / "preview.mp4" if (p.work / "preview.mp4").exists() else p.source
        i = p.data.get("info", {})
        self.player.load(str(src) if p.data.get("source") else "", i.get("duration", 0), i.get("fps", 25),
                         i.get("height", 1080))
        self.hs_editor.set_image(QPixmap())
        self.hs_editor.overlay = None
        if p.data.get("source"):
            QTimer.singleShot(400, self._grab_frame)
        self._dub_key = None
        self._update_player_audio()

    def _update_player_audio(self):
        """Trình phát trong tool phát luôn bản lồng tiếng (đã trộn nhạc nền) nếu đang bật lồng tiếng."""
        if not self.p:
            return
        d = self.p.edit.get("dub") or {}
        on = bool(d.get("enabled") and d.get("wav") and Path(d["wav"]).exists())
        key = (on, d.get("wav"), Path(d["wav"]).stat().st_mtime if on else 0, d.get("bg_volume"), d.get("vocals_volume"),
               d.get("original_volume"), d.get("dub_volume"), self.p.edit.get("volume"), self.p.edit.get("mute_original"))
        if key == getattr(self, "_dub_key", None):
            return
        self._dub_key = key
        self._load_dub_wave()   # sóng âm từng đoạn giọng lồng trên timeline
        if not on:
            self.player.set_alt_audio(None)
            return
        pid = self.p.id

        def done(path):
            if self.p and self.p.id == pid:
                self.player.set_alt_audio(str(path) if path else None)
                row = getattr(self, "_play_after_mix", None)
                if row is not None and path:
                    self._play_after_mix = None
                    self._play_line(row)      # vừa tạo lại giọng -> phát luôn câu đó bằng bản mới
        run_async(lambda: pipeline.dub_preview_audio(Project(pid)), done,
                  lambda m: self.win.toast("Không trộn được bản nghe thử lồng tiếng: " + m, "err"))

    def _fill_edit(self):
        e = self.p.edit
        st = _deep_merge(self.settings.get("sub_style") or {}, e["subtitles"].get("style") or {})
        hs = e["hardsub"]
        set_combo(self.hs_mode, hs.get("mode", "none"))
        set_combo(self.hs_backend, hs.get("backend") or self.settings.get("inpaint_backend", "lama"))
        set_combo(self.hs_strength, hs.get("strength", "normal"))
        self.hs_blur.set(hs.get("blur_strength", 20))
        self.hs_color.set(hs.get("box_color", "#000000"))
        self.hs_opacity.set(hs.get("box_opacity", 1.0))
        self.hs_useocr.setChecked(hs.get("use_ocr_boxes", True) is not False)
        self.hs_editor.set_regions(hs.get("regions") or [])
        self.e_start.setValue(float(e["trim"].get("start") or 0))
        self.e_end.setValue(float(e["trim"].get("end") or 0))
        set_combo(self.e_aspect, e["aspect"])
        set_combo(self.e_amode, e["aspect_mode"])
        set_combo(self.e_res, e["resolution"])
        set_combo(self.e_rot, str(int(e.get("rotate") or 0)))
        self.e_fliph.setChecked(bool(e["flip_h"]))
        self.e_flipv.setChecked(bool(e["flip_v"]))
        self.e_zoom.set(e["zoom"])
        self.e_speed.set(e["speed"])
        col = e["color"]
        self.e_bright.set(col.get("brightness", 0))
        self.e_contrast.set(col.get("contrast", 1))
        self.e_sat.set(col.get("saturation", 1))
        self.e_gamma.set(col.get("gamma", 1))
        self.e_sharpen.setChecked(bool(col.get("sharpen")))
        self.e_denoise.setChecked(bool(col.get("denoise")))
        self.e_volume.set(e["volume"])
        self.e_mute.setChecked(bool(e["mute_original"]))
        set_combo(self.e_pitch, str(e.get("pitch_shift") or 0) if e.get("pitch_shift") else "0")
        self.bgm_name.setText(Path(e["bgm"]["path"]).name if e["bgm"].get("path") else "Chưa chọn")
        self.e_bgmvol.set(e["bgm"].get("volume", 0.2))
        self.wm_name.setText(Path(e["watermark"]["path"]).name if e["watermark"].get("path") else "Chưa chọn")
        set_combo(self.e_wmpos, e["watermark"].get("position", "top-right"))
        self.e_wmscale.set(e["watermark"].get("scale", 0.15))
        self.e_wmop.set(e["watermark"].get("opacity", 0.85))
        self.e_text.setText(e["text"].get("content", ""))
        set_combo(self.e_textpos, e["text"].get("position", "top-left"))
        self.e_textcolor.set(e["text"].get("color", "#FFFFFF"))
        self.e_fin.set(e["fade"].get("in", 0))
        self.e_fout.set(e["fade"].get("out", 0))
        self.crop_info.setText(f"Crop {e['crop']['w'] * 100:.0f}% × {e['crop']['h'] * 100:.0f}%" if e.get("crop") else "Không crop")
        self._update_cuts_label()
        # kiểu chữ
        self.st_burn.setChecked(bool(e["subtitles"].get("burn", True)))
        set_combo(self.st_which, e["subtitles"].get("which", "trans"))
        set_combo(self.st_font, st.get("font", "Be Vietnam Pro"))
        self.st_size.setValue(int(st.get("size") or 0))
        set_combo(self.st_pos, st.get("position", "auto"))
        self.st_margin.setValue(int(st.get("margin_v") or 0))
        self.st_color.set(st.get("color", "#FFFFFF"))
        self.st_ocolor.set(st.get("outline_color", "#000000"))
        self.st_outline.set(st.get("outline", 2.5))
        self.st_shadow.set(st.get("shadow", 1))
        self.st_bold.setChecked(bool(st.get("bold", True)))
        self.st_upper.setChecked(bool(st.get("uppercase", False)))
        self.st_box.setChecked(bool(st.get("box", False)))
        self.st_bcolor.set(st.get("box_color", "#000000"))
        self.st_bop.set(st.get("box_opacity", 0.55))
        # lồng tiếng
        d = e["dub"]
        self.d_bg.set(d.get("bg_volume", 1.0))
        self.d_voc.set(d.get("vocals_volume", 0.0))
        self.d_orig.set(d.get("original_volume", 0.15))
        self.d_vol.set(d.get("dub_volume", 1.0))
        self.d_enabled.setChecked(bool(d.get("enabled")))
        set_combo(self.d_engine, self.p.data.get("tts_engine") or self.settings.get("tts_engine", "edge"))
        stems = pipeline.stems(self.p)
        self.d_sepstatus.setText("✓ Đã tách giọng / nhạc nền" if stems else "Chưa tách")
        has_dub = d.get("wav") and Path(d["wav"]).exists()
        self.d_status.setText(("✓ " + pipeline.dub_summary(self.p)) if has_dub else "Chưa tạo bản lồng tiếng")
        self._fill_dub_versions()
        set_combo(self.x_crf, str(e.get("crf") or self.settings.get("video_crf", 20)))

    def collect_edit(self) -> dict:
        e = self.p.edit
        return {
            "trim": {"start": self.e_start.value(), "end": self.e_end.value()},
            "cuts": e.get("cuts", []), "crop": e.get("crop"),
            "hardsub": {"mode": self.hs_mode.currentData(), "regions": self.hs_editor.regions,
                        "backend": self.hs_backend.currentData(), "strength": self.hs_strength.currentData(),
                        "blur_strength": int(self.hs_blur.value()), "box_color": self.hs_color.color,
                        "box_opacity": self.hs_opacity.value(), "use_ocr_boxes": self.hs_useocr.isChecked()},
            "aspect": self.e_aspect.currentData(), "aspect_mode": self.e_amode.currentData(),
            "resolution": self.e_res.currentData(), "rotate": int(self.e_rot.currentData()),
            "flip_h": self.e_fliph.isChecked(), "flip_v": self.e_flipv.isChecked(),
            "zoom": self.e_zoom.value(), "speed": self.e_speed.value(),
            "color": {"brightness": self.e_bright.value(), "contrast": self.e_contrast.value(),
                      "saturation": self.e_sat.value(), "gamma": self.e_gamma.value(),
                      "sharpen": self.e_sharpen.isChecked(), "denoise": self.e_denoise.isChecked()},
            "volume": self.e_volume.value(), "mute_original": self.e_mute.isChecked(),
            "pitch_shift": float(self.e_pitch.currentData()),
            "bgm": {"path": e["bgm"].get("path", ""), "volume": self.e_bgmvol.value()},
            "watermark": {"path": e["watermark"].get("path", ""), "position": self.e_wmpos.currentData(),
                          "scale": self.e_wmscale.value(), "opacity": self.e_wmop.value()},
            "text": {"content": self.e_text.text(), "position": self.e_textpos.currentData(),
                     "color": self.e_textcolor.color},
            "fade": {"in": self.e_fin.value(), "out": self.e_fout.value()},
            "subtitles": {"burn": self.st_burn.isChecked(), "which": self.st_which.currentData(),
                          "style": self.current_style()},
            "dub": {**e["dub"], "bg_volume": self.d_bg.value(), "vocals_volume": self.d_voc.value(),
                    "original_volume": self.d_orig.value(), "dub_volume": self.d_vol.value(),
                    "enabled": self.d_enabled.isChecked()},
            "crf": int(self.x_crf.currentData()),
        }

    def current_style(self) -> dict:
        return {"font": self.st_font.currentData(), "size": self.st_size.value(), "position": self.st_pos.currentData(),
                "margin_v": self.st_margin.value(), "color": self.st_color.color, "outline_color": self.st_ocolor.color,
                "outline": self.st_outline.value(), "shadow": self.st_shadow.value(), "bold": self.st_bold.isChecked(),
                "uppercase": self.st_upper.isChecked(), "box": self.st_box.isChecked(),
                "box_color": self.st_bcolor.color, "box_opacity": self.st_bop.value()}

    # ================================================================== lưu
    def _save_edit_now(self):
        if self.loading or not self.p:
            return
        self.p.reload()
        ed = self.collect_edit()
        cur = self.p.edit
        cur.update(ed)
        self.p.data["edit"] = cur
        self.p.data["glossary"] = self.glossary.toPlainText()
        self.p.data["tts_engine"] = self.d_engine.currentData()
        self.p.save()
        self._update_summary()
        self._update_timeline()
        self._update_player_audio()

    def _segs_edited(self):
        self._update_steps()
        self._save_segs_timer.start()
        self._update_count()
        self._refresh_overlay()
        self.saved.setText("● đang sửa…")
        self.tracks.canvas.update()

    def _save_segs_now(self):
        if not self.p:
            return
        self.p.reload()
        self.p.segments = self.model.segs
        self.p.save()
        self.saved.setText("✓ đã lưu " + time.strftime("%H:%M"))

    def flush(self):
        if self._save_segs_timer.isActive():
            self._save_segs_timer.stop()
            self._save_segs_now()
        if self._save_edit_timer.isActive():
            self._save_edit_timer.stop()
            self._save_edit_now()

    # ================================================================== tác vụ nền
    def submit(self, kind: str, title: str, fn):
        if not self.p:
            return
        self.flush()
        pid = self.p.id
        job = manager.submit(kind, f"{title}: {self.p.data.get('name')}", lambda j: fn(j, Project(pid)), pid)
        self.win.toast(f"Đã thêm vào hàng đợi: {title}", "ok")
        self.win.jobs_changed()
        return job

    def update_job(self, jobs: list[dict]):
        """Gọi định kỳ từ cửa sổ chính để hiển thị tiến độ tác vụ của dự án này."""
        if not self.p:
            return
        mine = [j for j in jobs if j["project_id"] == self.p.id and j["status"] in ("queued", "running")]
        if not mine:
            self.job_box.hide()
            self._cur_job = None
            return
        j = mine[-1] if all(x["status"] == "queued" for x in mine) else next(x for x in mine if x["status"] == "running")
        self._cur_job = j["id"]
        self.job_box.show()
        eta = f" · còn ~{j['eta'] // 60} phút" if j["eta"] > 90 else (f" · còn ~{j['eta']}s" if j["eta"] > 0 else "")
        self.job_title.setText(("Đang chờ: " if j["status"] == "queued" else "") + j["title"])
        self.job_msg.setText(f"{j['message']}  ·  {j['progress'] * 100:.1f}%{eta}")
        self.job_bar.set(j["progress"], busy=j["status"] == "running")

    def job_finished(self, j: dict):
        if self.p and j["project_id"] == self.p.id:
            self._play_after_mix = getattr(self, "_replay_row", None) if j["kind"] == "redub" and \
                j.get("status") == "done" else None
            self._replay_row = None if j["kind"] == "redub" else getattr(self, "_replay_row", None)
            self.reload()
            self._update_player_audio()
            if j["kind"] in ("preview", "download"):
                self._load_media()

    def _auto_opts(self) -> dict:
        which = self.a_which.currentData()
        return {"method": self.a_method.currentData(), "asr_engine": self.a_asr.currentData(),
                "ocr_fps": float(self.a_ocrfps.currentData() or 4),
                "language": self.a_lang.currentData(), "separate": self.a_sep.isChecked(),
                "diarize": self.a_spk.isChecked(), "engine": self.a_engine.currentData(),
                "style": self.a_style.currentData(), "hardsub_mode": self.a_hardsub.currentData(),
                "burn": which != "none", "which": which if which != "none" else "trans",
                "dub": self.a_dub.isChecked(), "tts_engine": self.a_tts.currentData(),
                "denoise": self.a_denoise.isChecked(), "force_recognize": self.a_force.isChecked()}

    def run_auto(self):
        if not self.p or not self.p.data.get("source"):
            return self.win.toast("Dự án chưa có video", "err")
        opts = self._auto_opts()
        if self.p.segments and not opts["force_recognize"]:
            self.win.toast("Dự án đã có phụ đề → dùng lại (bật 'Nhận dạng lại từ đầu' nếu muốn làm lại)", "warn")
        self.submit("auto", "Dịch tự động", lambda j, p: pipeline.auto(p, opts, j.update, j.cancelled))

    def run_recognize(self):
        if self.p.segments and QMessageBox.question(self, "Nhận dạng lại?", "Thay thế toàn bộ phụ đề hiện có?") != \
                QMessageBox.StandardButton.Yes:
            return
        o = self._auto_opts()
        st = dict(load_settings())
        st.update({"asr_engine": o["asr_engine"], "diarize": o["diarize"], "sep_before_asr": o["separate"]})
        region = self.hs_editor.regions[0] if (o["method"] == "ocr" and self.hs_editor.regions) else None

        def fn(j, p):
            n = pipeline.recognize(p, o["method"], o["language"], region, o["ocr_fps"], st, j.update, j.cancelled,
                                   o["denoise"])
            r = p.data.get("asr_rescued", 0)
            msg = f"Đã lấy {n} câu phụ đề" + (f" (nghe lại được thêm {r} câu bị sót)" if r else "")
            used = {"qwen3": "Qwen3-ASR", "sensevoice": "SenseVoice", "paraformer": "Paraformer",
                    "whisper": "Whisper"}.get(p.data.get("asr_engine_used", ""), "")
            if o["method"] != "ocr" and used:
                msg += f" · nghe bằng {used}"
            if o["method"] != "ocr" and p.data.get("asr_warning"):
                msg += "  ⚠ " + p.data["asr_warning"]
            if o["method"] == "ocr":
                ost = p.data.get("ocr_stats") or {}
                if ost.get("frames"):
                    msg += f" · quét {ost['frames']} khung ({ost.get('fps', 4):g} lần/giây), đọc chữ {ost['ocr_runs']} lần"
                if p.data.get("ocr_warning"):
                    msg = "⚠ " + p.data["ocr_warning"]
            return {"message": msg}
        self.submit("recognize", "Lấy phụ đề", fn)

    def run_translate(self, only_empty: bool):
        eng, sty = self.a_engine.currentData(), self.a_style.currentData()
        if eng == pipeline.NO_TRANSLATE:
            return self.win.toast("Đang chọn 'Không dịch' ở tab Tự động. Chọn công cụ dịch, hoặc nạp SRT đã dịch "
                                  "(tab Lồng tiếng → Nạp SRT đã dịch)", "warn")

        def fn(j, p):
            n = pipeline.translate(p, eng, sty, only_empty, None, j.update, j.cancelled)
            w = p.data.get("translate_warning")
            return {"message": f"Đã dịch {n} câu" + (f" – ⚠ {w}" if w else "")}
        self.submit("translate", "Dịch phụ đề", fn)

    def run_separate(self):
        model = self.d_sepmodel.currentData()

        def fn(j, p):
            pipeline.separate_audio(p, None, j.update, j.cancelled, model)
            return {"message": "Đã tách giọng nói / nhạc nền"}
        self.submit("separate", "Tách giọng/nhạc nền", fn)

    def run_realign(self):
        def fn(j, p):
            before = len(p.segments)
            n = pipeline.realign(p, None, j.update, j.cancelled)
            extra = f" – tách thêm {n - before} câu, bấm 'Dịch dòng trống' để dịch các câu mới tách" if n > before else ""
            return {"message": f"Đã căn lại thời gian {n} câu{extra}"}
        self.submit("realign", "Căn lại thời gian", fn)

    def run_diarize(self):
        def fn(j, p):
            n = pipeline.diarize(p, None, j.update, j.cancelled)
            sp = p.data.get("speaker_splits", 0)
            extra = f" – tách {sp} câu có 2 người nói liền nhau (bấm 'Dịch dòng trống' nếu đã dịch)" if sp else ""
            return {"message": f"Tìm thấy {n} người nói{extra}"}
        self.submit("diarize", "Phân biệt người nói", fn)

    def run_dub(self):
        eng = self.d_engine.currentData()
        rate = self.d_rate.currentData()
        st = dict(load_settings())
        st["dub_skip_songs"] = self.d_skipsong.isChecked()

        def fn(j, p):
            pipeline.dub(p, None, rate, st, j.update, j.cancelled, eng)
            return {"message": pipeline.dub_summary(p)}
        self.submit("dub", "Lồng tiếng", fn)

    def _step_dub(self):
        if not any(sg.trans.strip() for sg in self.model.segs):
            return self.win.toast("Làm bước 2 (Dịch) trước đã", "warn")
        self.run_dub()

    def _step_export(self):
        self.tabs.set_current(7)
        self.run_render()

    def _update_steps(self):
        if not getattr(self, "step_status", None) or not self.p:
            return
        segs = self.model.segs
        n = len(segs)
        done_tr = sum(1 for sg in segs if sg.trans.strip())
        st = self.p.data.get("dub_stats") or {}
        d = self.p.edit.get("dub") or {}
        vids = [o for o in (self.p.data.get("outputs") or []) if (o.get("kind") if isinstance(o, dict) else "") == "video"]
        spk = len({sg.speaker for sg in segs if sg.speaker})
        texts = [
            (f"✓ {n} câu" + (f" · {spk} người nói" if spk > 1 else "")) if n else "Chưa có",
            (f"✓ {done_tr}/{n} câu" if done_tr == n else f"{done_tr}/{n} câu") if n else "—",
            (f"✓ {st.get('placed', 0)}/{st.get('total', 0)} câu" + (f" · ⚠ {st['fit']['long']} quá dài"
                                                                     if (st.get('fit') or {}).get('long') else ""))
            if d.get("wav") and st else "Chưa lồng",
            f"✓ {len(vids)} video" if vids else "Chưa xuất",
        ]
        for lb, t in zip(self.step_status, texts):
            lb.setText(t)

    def run_render(self):
        name = self.x_name.text().strip() or None

        def fn(j, p):
            out = pipeline.render(p, None, j.update, j.cancelled, name)
            return {"message": f"Đã xuất: {out.name}", "path": str(out)}
        self.submit("render", "Xuất video", fn)

    def _tool(self, op: str):
        from .. import video

        settings = load_settings()
        if op == "split_parts":
            m, ok = QInputDialog.getDouble(self, "Chia video", "Mỗi phần dài bao nhiêu phút?", 10, 0.5, 600, 1)
            if not ok:
                return

            def fn(j, p):
                outs = video.split_parts(p.source, p.output_dir / f"{p.source.stem}_parts", m, settings, j.update, j.cancelled)
                for o in outs:
                    p.add_output(o, "video")
                return {"message": f"Đã chia thành {len(outs)} phần"}
            self.submit("tool", "Chia video", fn)
        elif op == "extract_audio":
            def fn(j, p):
                out = video.extract_audio_file(p.source, p.output_dir / f"{p.source.stem}.mp3", j.update, j.cancelled)
                p.add_output(out, "audio")
                return {"message": "Đã tách MP3"}
            self.submit("tool", "Tách MP3", fn)

    # ================================================================== trình phát & phụ đề
    def _on_pos(self, t: float):
        if getattr(self, "_stop_at", None) is not None and t >= self._stop_at:
            self._stop_at = None
            if self.player.playing():
                self.player.toggle()      # "Nghe câu": phát hết câu thì dừng
        segs = self.model.segs
        idx = next((i for i, s in enumerate(segs) if s.start <= t < s.end), -1)
        if idx != self.model.active:
            self.model.set_active(idx)
            if idx >= 0 and self.follow.isChecked() and self.player.playing():
                table, proxy = {1: (self.table, self.proxy), 5: (self.dub_table, self.dub_proxy)}.get(
                    self.stack.currentIndex(), (None, None))
                pi = proxy.mapFromSource(self.model.index(idx, 0)) if proxy else None
                if pi is not None and pi.isValid():
                    table.scrollTo(pi, QAbstractItemView.ScrollHint.PositionAtCenter)
            self.tracks.set_active(idx)
        self.timeline.set_pos(t)
        self.tracks.set_pos(t, follow=self.player.playing())

    def _refresh_overlay(self):
        if not self.p:
            return
        self.player.show_subs = self.ov_show.isChecked()
        which = self.ov_which.currentData()
        st = pipeline.effective_style(self.p, self.current_style())
        if st.get("margin_v_rel") is not None:
            st["margin_v"] = st["margin_v_rel"] * self.player.view.out_h
        self.player.set_subtitles(self.model.segs, st, which)

    def _media_error(self, msg: str):
        if not self.p or (self.p.work / "preview.mp4").exists():
            return
        self.win.toast("Không phát được định dạng này → đang tạo bản xem trước (không ảnh hưởng chất lượng xuất)", "warn")
        self.submit("preview", "Tạo bản xem trước", lambda j, p: (pipeline.make_preview(p, j.update, j.cancelled),
                                                                 {"message": "Đã tạo bản xem trước"})[1])

    def _row_clicked(self, pidx):
        row = self.proxy.mapToSource(pidx).row()
        if 0 <= row < len(self.model.segs):
            self._select_line(row, seek=pidx.column() in (0, C_START, C_END, C_SPK), source="subs")

    def _selected_row(self) -> int:
        if 0 <= self._sel_row < len(self.model.segs):
            return self._sel_row
        idx = self.table.currentIndex()
        return self.proxy.mapToSource(idx).row() if idx.isValid() else -1

    # ------------------------------------------------------------------ câu đang chọn (đồng bộ mọi khung)
    def _select_line(self, row: int, seek: bool = False, source: str = ""):
        segs = self.model.segs
        if not (0 <= row < len(segs)):
            return
        self._sel_row = row
        self.inspector.set_row(row)
        self.tracks.set_selected(row)
        for name, table, proxy in (("subs", self.table, self.proxy), ("dub", self.dub_table, self.dub_proxy)):
            if name != source:
                pi = proxy.mapFromSource(self.model.index(row, 0))
                if pi.isValid():
                    table.selectRow(pi.row())
                    table.scrollTo(pi, QAbstractItemView.ScrollHint.EnsureVisible)
        if seek:
            self.player.seek(segs[row].start + 0.01)

    def _line_action(self, name: str, row: int):
        if name == "play":
            self._play_line(row)
        elif name == "retranslate":
            self._retranslate_one(row)
        elif name == "shorten":
            self._shorten_rows([row])
        elif name == "redub":
            self._redub_rows([row])

    def _dub_moved(self, row: int, delta: float | None):
        """Kéo đoạn giọng lồng của 1 câu trên timeline (delta giây) / đặt lại (None) -> xếp lại bản lồng (không đọc lại)."""
        if not self.p or not (0 <= row < len(self.model.segs)):
            return
        s = self.model.segs[row]
        old = float(s.extra.get("dub_shift", 0) or 0)
        shift = 0.0 if delta is None else round(old + delta, 3)
        if abs(shift) < 0.02:
            s.extra.pop("dub_shift", None)
        else:
            s.extra["dub_shift"] = shift
        if abs(shift - old) < 0.005:
            return
        # hiện ngay vị trí mới trên timeline trong lúc xếp lại ở nền
        self.tracks.dub = [[a + shift - old, b + shift - old, k, f] if int(k) == row else [a, b, k, f]
                           for a, b, k, f in self.tracks.dub]
        self.tracks.canvas.update()
        self._save_segs_now()
        self._replay_row = row

        def fn(j, p):
            pipeline.relayout(p, None, j.update, j.cancelled)
            where = "đúng lúc nhân vật nói" if not shift else f"dời {shift:+.2f} giây so với lúc nhân vật nói"
            return {"message": f"Đã đặt giọng câu {row + 1} {where} – trình phát đang dùng bản mới"}
        self.submit("redub", f"Xếp lại giọng câu {row + 1}", fn)

    def _load_dub_wave(self):
        if not self.p:
            return
        pid = self.p.id

        def done(r):
            if self.p and self.p.id == pid:
                if r:
                    import numpy as np

                    self.tracks.set_dub_wave(np.load(r[0]), r[1])
                else:
                    self.tracks.set_dub_wave(None)
        run_async(lambda: pipeline.dub_wave(Project(pid)), done, lambda m: None)

    def _timeline_action(self, name: str):
        row = self._selected_row()
        if name in ("insert", "split", "merge", "delete"):
            return self._seg_op(name)
        if row < 0:
            return self.win.toast("Bấm chọn 1 câu trên timeline trước", "warn")
        self._line_action(name, row)

    def _fit_rows(self):
        if len(self.model.segs) <= 3000:  # bảng quá dài thì bỏ qua cho nhanh
            self.table.resizeRowsToContents()

    def _update_count(self):
        segs = self.model.segs
        n = len(segs)
        spk = len({s.speaker for s in segs if s.speaker})
        self.seg_count.setText(f"{n} câu · đã dịch {sum(1 for s in segs if s.trans)}" + (f" · {spk} người nói" if spk else ""))

    def _seg_op(self, op: str):
        from .. import subtitle as st

        i = self._selected_row()
        segs = self.model.segs
        if op == "insert":
            t = self.player.position()
            pos = next((k for k, s in enumerate(segs) if s.start > t), len(segs))
            segs.insert(pos, Segment(t, t + 2.0, "", ""))
            i = pos
        elif i < 0:
            return self.win.toast("Hãy chọn 1 câu trong bảng trước", "warn")
        elif op == "merge":
            st.merge(segs, i)
        elif op == "split":
            st.split(segs, i, self.player.position())
        elif op == "delete":
            del segs[i]
        self.model.set_segments(segs)
        self._fit_rows()
        self._segs_edited()
        self._sel_row = -1
        if 0 <= i < len(segs):
            self._select_line(min(i, len(segs) - 1))

    def _retranslate_one(self, row: int | None = None):
        i = self._selected_row() if row is None else row
        if i < 0:
            return self.win.toast("Hãy chọn 1 câu", "warn")
        seg = self.model.segs[i]
        eng = self.a_engine.currentData()
        if eng == pipeline.NO_TRANSLATE:
            return self.win.toast("Đang chọn 'Không dịch' ở tab Tự động – chọn công cụ dịch trước", "warn")

        def done(txt):
            seg.trans = txt
            seg.extra.pop("trans_user", None)
            seg.extra.pop("fit", None)
            self.model.dataChanged.emit(self.model.index(i, 0), self.model.index(i, 7))
            self._segs_edited()
        from ..translate import translate_text

        run_async(lambda: translate_text(seg.text, load_settings(), eng), done, lambda m: self.win.toast(m, "err"))

    def _shift(self):
        v, ok = QInputDialog.getDouble(self, "Dời thời gian", "Dời bao nhiêu giây? (âm = sớm hơn)\n"
                                       "Áp dụng từ câu đang chọn trở đi (nếu có chọn).", 0.3, -3600, 3600, 3)
        if not ok:
            return
        from .. import subtitle as st

        i = self._selected_row()
        st.shift(self.model.segs, v, self.model.segs[i].start if i >= 0 else 0)
        self.model.set_segments(self.model.segs)
        self._segs_edited()

    def _replace(self):
        a, ok = QInputDialog.getText(self, "Tìm & thay", "Tìm (trong bản dịch tiếng Việt):")
        if not ok or not a:
            return
        b, ok = QInputDialog.getText(self, "Tìm & thay", f"Thay “{a}” bằng:")
        if not ok:
            return
        n = 0
        for s in self.model.segs:
            if a in s.trans:
                s.trans = s.trans.replace(a, b)
                n += 1
        self.model.set_segments(self.model.segs)
        self._segs_edited()
        self.win.toast(f"Đã thay ở {n} câu", "ok")

    def _import_subs(self):
        path, _ = QFileDialog.getOpenFileName(self, "Chọn file phụ đề", "", "Phụ đề (*.srt *.ass *.ssa *.vtt)")
        if not path:
            return
        items = ["Là phụ đề tiếng Trung (gốc)", "Là phụ đề tiếng Việt", "Ghép làm bản dịch cho phụ đề hiện có"]
        choice, ok = QInputDialog.getItem(self, "Nhập phụ đề", "File này:", items, 0, False)
        if not ok:
            return
        segs = load_subtitles(path)
        if not segs:
            return self.win.toast("File trống hoặc không đọc được", "err")
        k = items.index(choice)
        if k == 1:
            for s in segs:
                s.trans, s.text = s.text, ""
        elif k == 2 and self.model.segs:
            cur = self.model.segs
            pipeline.apply_translation(cur, segs)
            segs = cur
        self.model.set_segments(normalize(segs))
        self._segs_edited()
        self.win.toast(f"Đã nhập {len(segs)} câu", "ok")

    # ================================================================== xoá phụ đề
    def _grab_frame(self):
        if not self.p or not self.p.data.get("source"):
            return
        from .. import ffmpeg as ff

        t = self.player.position() or min(5.0, self.p.data.get("info", {}).get("duration", 10) / 3)
        out = self.p.work / f"frame_{int(t * 1000)}.jpg"
        src = self.p.source

        def job():
            if not out.exists():
                ff.snapshot(src, t, out, 1280)
            return str(out)
        run_async(job, lambda path: (self.hs_editor.set_image(path), setattr(self.hs_editor, "overlay", None),
                                     self.hs_editor.update()),
                  lambda m: self.win.toast("Không chụp được khung hình: " + m, "err"))

    def _detect_region(self):
        from ..ocr import detect_subtitle_region

        self.win.toast("Đang dò vùng phụ đề (quét kỹ 5 giây đầu + 5 giây cuối)…")
        src = self.p.source

        def done(r):
            if not r.get("found"):
                self.win.toast("Không thấy phụ đề tiếng Trung rõ ràng – đã đặt vùng mặc định phía dưới", "warn")
            else:
                self.win.toast("Đã tìm thấy vùng phụ đề", "ok")
            box = r.get("erase") or r   # vùng xoá vừa khít chữ phụ đề (vùng quét OCR thì rộng hơn, tự tính)
            self.hs_editor.set_regions([{k: box[k] for k in ("x", "y", "w", "h")}])
            if self.hs_mode.currentData() == "none":
                set_combo(self.hs_mode, "inpaint")
            self._save_edit_timer.start()
        run_async(lambda: detect_subtitle_region(src), done, lambda m: self.win.toast(m, "err"))

    def _preview_hardsub(self):
        regs = self.hs_editor.regions
        if not regs:
            return self.win.toast("Hãy khoanh vùng phụ đề trước", "warn")
        from ..inpaint import preview_frame

        mode = self.hs_mode.currentData()
        mode = "inpaint" if mode in ("none", "inpaint") else ("blur" if mode == "delogo" else mode)
        st = dict(load_settings())
        st["inpaint_backend"] = self.hs_backend.currentData()
        t = self.player.position()
        out = self.p.work / f"hs_preview_{int(time.time())}.jpg"
        src = self.p.source
        strength = self.hs_strength.currentData()
        self.win.toast("Đang tạo ảnh xem trước…")

        def done(path):
            pm = QPixmap(path)
            self.hs_editor.set_image(pm)
            self.hs_editor.overlay = None
            self.hs_editor.update()
        run_async(lambda: str(preview_frame(src, t, regs, out, mode, strength, st)), done,
                  lambda m: self.win.toast(m, "err"))

    def _clear_preview(self):
        self._grab_frame()

    # ================================================================== chỉnh sửa
    def _mark_cut(self, which: str):
        t = self.player.position()
        if which == "a":
            self.cut_a = t
            self.timeline.mark = t
            self.timeline.update()
            self.cut_label.setText(f"Điểm đầu: {fmt(t)} → tua tới điểm cuối rồi bấm 'Đánh dấu cuối'")
            return
        if self.cut_a is None:
            return self.win.toast("Đánh dấu điểm đầu trước", "warn")
        a, b = sorted((self.cut_a, t))
        if b - a < 0.05:
            return self.win.toast("Đoạn cắt quá ngắn", "warn")
        self.p.reload()
        e = self.p.edit
        e["cuts"] = e.get("cuts", []) + [[round(a, 3), round(b, 3)]]
        self.p.data["edit"] = e
        self.p.save()
        self.cut_a = None
        self.timeline.mark = None
        self.cut_label.setText("Đã thêm đoạn cắt")
        self._update_cuts_label()
        self._update_timeline()
        self._update_summary()

    def _clear_cuts(self):
        self.p.reload()
        e = self.p.edit
        e["cuts"] = []
        self.p.data["edit"] = e
        self.p.save()
        self._update_cuts_label()
        self._update_timeline()

    def _update_cuts_label(self):
        cuts = self.p.edit.get("cuts", []) if self.p else []
        self.cuts_list.setText("\n".join(f"✂ Xoá {fmt(a)} → {fmt(b)}" for a, b in cuts) or "Chưa có đoạn cắt nào")

    def _update_timeline(self):
        if not self.p:
            return
        e = self.p.edit
        d = self.p.data.get("info", {}).get("duration", 0)
        self.timeline.dub = (self.p.data.get("dub_layout") or []) if (e.get("dub") or {}).get("enabled") else []
        keep = (e["trim"].get("start") or 0, e["trim"].get("end") or 0)
        self.timeline.set_data(d, keep, e.get("cuts", []), self.model.segs)
        self.tracks.mark = self.timeline.mark
        self.tracks.set_data(d, self.model.segs, self.p.data.get("dub_layout") or [], keep, e.get("cuts", []))

    def _crop_dialog(self):
        from PySide6.QtWidgets import QDialog, QDialogButtonBox

        dlg = QDialog(self)
        dlg.setWindowTitle("Chọn vùng giữ lại (crop)")
        dlg.resize(900, 600)
        ed = RegionEditor(multi=False)
        if self.hs_editor.pix:
            ed.set_image(self.hs_editor.pix)
        if self.p.edit.get("crop"):
            ed.set_regions([self.p.edit["crop"]])
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(dlg.accept)
        bb.rejected.connect(dlg.reject)
        v = QVBoxLayout(dlg)
        v.addWidget(label("Kéo chuột để chọn vùng hình muốn GIỮ LẠI.", "muted"))
        v.addWidget(ed, 1)
        v.addWidget(bb)
        if dlg.exec() and ed.regions:
            self.p.reload()
            e = self.p.edit
            e["crop"] = ed.regions[0]
            self.p.data["edit"] = e
            self.p.save()
            self.crop_info.setText(f"Crop {ed.regions[0]['w'] * 100:.0f}% × {ed.regions[0]['h'] * 100:.0f}%")
            self._update_summary()

    def _clear_crop(self):
        self.p.reload()
        e = self.p.edit
        e["crop"] = None
        self.p.data["edit"] = e
        self.p.save()
        self.crop_info.setText("Không crop")
        self._apply_live()

    def _pick_asset(self, kind: str):
        flt = "Âm thanh (*.mp3 *.wav *.m4a *.aac *.flac *.ogg)" if kind == "bgm" else "Ảnh (*.png *.jpg *.jpeg *.webp)"
        path, _ = QFileDialog.getOpenFileName(self, "Chọn file", "", flt)
        if not path:
            return
        self.p.reload()
        e = self.p.edit
        e[kind]["path"] = path
        self.p.data["edit"] = e
        self.p.save()
        (self.bgm_name if kind == "bgm" else self.wm_name).setText(Path(path).name)
        self._update_summary()

    def _clear_asset(self, kind: str):
        self.p.reload()
        e = self.p.edit
        e[kind]["path"] = ""
        self.p.data["edit"] = e
        self.p.save()
        (self.bgm_name if kind == "bgm" else self.wm_name).setText("Chưa chọn")

    def _style_preset(self, name: str):
        presets = {
            "classic": dict(color="#FFFFFF", outline_color="#000000", outline=2.5, box=False, bold=True),
            "yellow": dict(color="#FFE14D", outline_color="#000000", outline=3, box=False, bold=True),
            "box": dict(color="#FFFFFF", box=True, box_color="#000000", box_opacity=0.6, bold=True),
            "tiktok": dict(color="#FFFFFF", outline_color="#000000", outline=4, box=False, bold=True, size=0,
                           position="middle", uppercase=False),
        }[name]
        st = self.current_style()
        st.update(presets)
        self.loading = True
        self.st_color.set(st["color"])
        self.st_ocolor.set(st.get("outline_color", "#000000"))
        self.st_outline.set(st.get("outline", 2.5))
        self.st_box.setChecked(bool(st.get("box")))
        self.st_bcolor.set(st.get("box_color", "#000000"))
        self.st_bop.set(st.get("box_opacity", 0.55))
        self.st_bold.setChecked(bool(st.get("bold", True)))
        set_combo(self.st_pos, st.get("position", "bottom"))
        self.loading = False
        self._save_edit_now()
        self._refresh_overlay()

    # ================================================================== lồng tiếng
    def _fill_speakers(self):
        from .. import tts

        p = self.p
        spks = pipeline.speaker_list(p)
        voices = tts.list_voices(self.settings)
        self._voices = voices
        self.model.voice_names = {v["id"]: v["name"] for v in voices}
        cur = self.d_line_voice.currentData()
        self.d_line_voice.clear()
        self.d_line_voice.addItem("(Theo người nói)", "")
        for v in voices:
            self.d_line_voice.addItem(v["name"], v["id"])
        set_combo(self.d_line_voice, cur or "")
        self.inspector.refresh_voices()
        self.spk_table.setRowCount(len(spks))
        self.spk_table.setFixedHeight(36 + 44 * max(1, min(len(spks), 6)))
        for r, s in enumerate(spks):
            it = QTableWidgetItem(s["speaker"])
            it.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            from .subs_model import spk_color
            from PySide6.QtGui import QColor

            it.setForeground(QColor(spk_color(s["speaker"])))
            it.setFont(theme.font(10, QFont.Weight.Bold))
            self.spk_table.setItem(r, 0, it)
            n = QTableWidgetItem(str(s["lines"]))
            n.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.spk_table.setItem(r, 1, n)
            cb = QComboBox()
            cb.addItem("(Mặc định của bộ đọc)", "")
            for v in voices:
                cb.addItem(v["name"], v["id"])
            set_combo(cb, s["voice"])
            cb.currentIndexChanged.connect(lambda _i, spk=s["speaker"], c=cb: self._set_voice(spk, c.currentData()))
            self.spk_table.setCellWidget(r, 2, cb)
            b = IconButton("play", "Nghe thử giọng này", 32)
            b.clicked.connect(lambda _=False, c=cb, spk=s["speaker"]: self._preview_voice(c.currentData(), spk))
            self.spk_table.setCellWidget(r, 3, wrap(hbox(b)))

    def _set_voice(self, spk: str, vid: str):
        if self.loading:
            return
        self.p.reload()
        self.p.data.setdefault("voices", {})[spk] = vid
        self.p.save()

    def _auto_voices(self):
        from .. import tts

        self.p.reload()
        st = dict(self.settings)
        st["tts_engine"] = self.d_engine.currentData()
        spks = [s["speaker"] for s in pipeline.speaker_list(self.p)]
        self.p.data["voices"] = tts.auto_assign(spks, st, {})
        self.p.save()
        self.loading = True
        self._fill_speakers()
        self.loading = False
        self.win.toast("Đã gán giọng khác nhau cho từng người nói", "ok")

    def _dub_engine_changed(self):
        if not self.loading:
            self._save_edit_timer.start()

    def _skip_song_changed(self, on: bool):
        if self.loading:
            return
        from ..config import save_settings

        save_settings({"dub_skip_songs": on})

    def _preview_voice(self, vid: str, spk: str):
        from .. import tts

        if not vid and self.d_engine.currentData() == "omnivoice":
            from .. import voices as vlib

            smp = vlib.list_samples()
            vid = f"omni:{smp[0]['id']}" if smp else tts.OMNI_CLONE
        vid = vid or (f"edge:{self.settings.get('tts_voice')}" if self.d_engine.currentData() == "edge" else "vieneu:")
        text = next((s.trans for s in self.model.segs if (s.speaker or "S1") == spk and s.trans), "")
        out = self.p.work / f"voice_preview_{int(time.time())}"
        ref = None
        if vid == tts.CLONE:
            st = pipeline.stems(self.p)
            ref = tts.build_reference(st.get("vocals") or str(self.p.source), self.model.segs, spk,
                                      self.p.work / "tts" / f"ref_{spk}.wav")
        elif vid == tts.OMNI_CLONE:
            st = pipeline.stems(self.p)
            ref = tts.build_reference_text(st.get("vocals") or str(self.p.source), self.model.segs, spk,
                                           self.p.work / "tts" / f"omniref_{spk}.wav")
            if not ref:
                return self.win.toast("Người nói này chưa có câu nào đủ rõ để lấy mẫu giọng", "warn")
        self.win.toast("Đang tạo giọng nghe thử…")
        run_async(lambda: tts.preview_voice(vid, text, out, self.settings, ref),
                  lambda path: QDesktopServices.openUrl(QUrl.fromLocalFile(str(path))),
                  lambda m: self.win.toast("Không tạo được giọng: " + m, "err"))

    # ------------------------------------------------------------------ bảng câu lồng tiếng
    def _update_picked(self):
        n = len(self.model.selected)
        self.d_picked.setText(f"Đã tích {n} câu" if n else "Chưa tích câu nào")

    def _dub_rows_visible(self) -> list[int]:
        return [self.dub_proxy.mapToSource(self.dub_proxy.index(r, 0)).row() for r in range(self.dub_proxy.rowCount())]

    def _pick_visible(self):
        self.model.set_picked(self._dub_rows_visible())

    def _pick_long(self):
        rows = [i for i, s in enumerate(self.model.segs) if s.extra.get("fit") == "long"]
        if not rows:
            return self.win.toast("Không có câu nào bị ⚠ quá dài (hoặc chưa tạo bản lồng tiếng)", "ok")
        self.model.set_picked(rows)

    def _dub_row_clicked(self, pidx):
        row = self.dub_proxy.mapToSource(pidx).row()
        if 0 <= row < len(self.model.segs):
            self._select_line(row, seek=pidx.column() in (0, C_START, C_END, C_SPK), source="dub")

    def _dub_current_rows(self) -> list[int]:
        rows = sorted({self.dub_proxy.mapToSource(i).row() for i in self.dub_table.selectionModel().selectedRows()})
        return rows or sorted(self.model.selected)

    def _set_line_voice(self):
        rows = sorted(self.model.selected)
        if not rows:
            return self.win.toast("Tích chọn câu cần đổi giọng ở cột Chọn trước", "warn")
        vid = self.d_line_voice.currentData() or ""
        for r in rows:
            self.model.setData(self.model.index(r, C_VOICE), vid)
        self.win.toast(f"Đã đổi giọng {len(rows)} câu – bấm 'Tạo lại giọng các câu đã tích' để đọc lại", "ok")

    def _play_line(self, row: int | None = None):
        rows = [row] if row is not None else self._dub_current_rows()
        if not rows or not (0 <= rows[0] < len(self.model.segs)):
            return self.win.toast("Chọn 1 câu trong bảng trước", "warn")
        s = self.model.segs[rows[0]]
        self._stop_at = s.end + 0.35
        self.player.seek(max(0.0, s.start - 0.1))
        if not self.player.playing():
            self.player.toggle()

    def _redub_picked(self):
        rows = sorted(self.model.selected)
        if not rows:
            return self.win.toast("Tích chọn câu cần tạo lại giọng ở cột Chọn trước", "warn")
        self._redub_rows(rows)

    def _redub_rows(self, rows: list[int]):
        self.inspector._flush()
        empty = [r + 1 for r in rows if not self.model.segs[r].trans.strip()]
        if empty:
            return self.win.toast(f"Câu {', '.join(map(str, empty[:5]))} chưa có lời tiếng Việt", "warn")
        eng = self.d_engine.currentData()
        rate = self.d_rate.currentData()
        st = dict(load_settings())
        st["dub_skip_songs"] = self.d_skipsong.isChecked()
        self._replay_row = rows[0]

        def fn(j, p):
            pipeline.redub(p, rows, rate, st, j.update, j.cancelled, eng)
            sts = p.data.get("dub_stats") or {}
            msg = f"Đã tạo lại giọng {sts.get('redone', len(rows))}/{len(rows)} câu – trình phát đang dùng bản mới"
            if sts.get("kept_old"):
                msg += f". ⚠ {sts['kept_old']} câu đọc lỗi nên giữ giọng cũ: " + "; ".join(sts.get("error_samples") or [])
            return {"message": msg}
        self.submit("redub", f"Tạo lại giọng {len(rows)} câu", fn)

    def _shorten_picked(self):
        rows = sorted(self.model.selected)
        if not rows:
            return self.win.toast("Tích chọn câu cần rút gọn ở cột Chọn trước (có thể bấm 'Tích câu ⚠ quá dài')", "warn")
        self._shorten_rows(rows)

    def _shorten_rows(self, rows: list[int]):
        self.inspector._flush()

        def fn(j, p):
            j.update(0.1, f"AI đang rút gọn {len(rows)} câu…")
            n = pipeline.shorten_lines(p, rows)
            return {"message": f"AI đã rút gọn {n}/{len(rows)} câu – bấm 'Tạo lại giọng các câu đã tích' để đọc lại"}
        self.submit("shorten", f"Rút gọn {len(rows)} câu", fn)

    def _import_translation(self):
        path, _ = QFileDialog.getOpenFileName(self, "Chọn file phụ đề đã dịch", "", "Phụ đề (*.srt *.ass *.ssa *.vtt)")
        if not path:
            return
        cues = load_subtitles(path)
        if not cues:
            return self.win.toast("File trống hoặc không đọc được", "err")
        segs = self.model.segs
        if not segs:   # dự án chưa có câu nào -> dùng luôn file này làm phụ đề tiếng Việt
            for c in cues:
                c.trans, c.text = c.text, ""
                c.extra["trans_user"] = True
            self.model.set_segments(normalize(cues))
            self._segs_edited()
            return self.win.toast(f"Đã nạp {len(cues)} câu tiếng Việt", "ok")
        changed = pipeline.apply_translation(segs, cues)
        self.model.set_segments(segs)
        self._segs_edited()
        if not changed:
            return self.win.toast("File này không làm đổi câu nào (lời Việt giống hệt bản đang có)", "warn")
        self.model.set_picked(range(len(segs)), False)
        self.model.set_picked(changed)       # tích sẵn các câu đổi lời -> bấm "Tạo lại giọng…" là đọc lại đúng các câu này
        self.win.toast(f"Đã nạp bản dịch: {len(changed)}/{len(segs)} câu đổi lời và đã được tích sẵn – bấm "
                       "'Tạo lại giọng các câu đã tích' để đọc lại và cập nhật vào video", "ok")

    def _save_mp3(self, src: str | Path | None, default_name: str):
        if not src or not Path(src).exists():
            return self.win.toast("Chưa có file âm thanh này", "warn")
        out, _ = QFileDialog.getSaveFileName(self, "Lưu MP3", str(Path.home() / default_name), "MP3 (*.mp3)")
        if not out:
            return
        if not out.lower().endswith(".mp3"):
            out += ".mp3"
        from .. import video

        self.win.toast("Đang lưu MP3…")
        run_async(lambda: video.extract_audio_file(src, out), lambda o: self.win.toast(f"Đã lưu {Path(o).name}", "ok"),
                  lambda m: self.win.toast("Không lưu được MP3: " + m, "err"))

    def _import_stems(self):
        flt = "Âm thanh (*.wav *.mp3 *.m4a *.aac *.flac *.ogg *.opus *.mp4)"
        voc, _ = QFileDialog.getOpenFileName(self, "Chọn file GIỌNG NÓI đã tách", "", flt)
        if not voc:
            return
        inst, _ = QFileDialog.getOpenFileName(self, "Chọn file NHẠC NỀN đã tách (bấm Huỷ nếu không có – tool tự tính "
                                                    "= tiếng gốc trừ giọng)", str(Path(voc).parent), flt)

        def fn(j, p):
            pipeline.import_stems(p, voc, inst or None)
            return {"message": "Đã nạp giọng / nhạc nền từ phần mềm khác"}
        self.submit("separate", "Nạp giọng / nhạc nền", fn)

    def _save_stem_mp3(self, which: str):
        st = pipeline.stems(self.p)
        if not st:
            return self.win.toast("Chưa tách giọng / nhạc nền – bấm 'Tách ngay' trước", "warn")
        stem = safe_stem(self.p)
        self._save_mp3(st[which], f"{stem}_{'giong' if which == 'vocals' else 'nhacnen'}.mp3")

    def _save_dub_mp3(self):
        if not (self.p.edit.get("dub") or {}).get("wav"):
            return self.win.toast("Chưa có bản lồng tiếng", "warn")
        pid, stem = self.p.id, safe_stem(self.p)
        self.flush()

        def done(path):
            if path:
                self._save_mp3(path, f"{stem}_longtieng.mp3")
            else:
                self.win.toast("Bật 'Dùng bản lồng tiếng' ở mục 3 trước", "warn")
        run_async(lambda: pipeline.dub_preview_audio(Project(pid)), done,
                  lambda m: self.win.toast("Không trộn được bản lồng tiếng: " + m, "err"))

    def _open_stem(self, which: str):
        st = pipeline.stems(self.p)
        if not st:
            return self.win.toast("Chưa tách giọng / nhạc nền", "warn")
        QDesktopServices.openUrl(QUrl.fromLocalFile(st[which]))

    def _fill_dub_versions(self):
        lay = self.d_versions
        while lay.count():
            it = lay.takeAt(0)
            w = it.widget()
            if w:
                w.hide()
                w.deleteLater()
            elif it.layout():
                sub = it.layout()
                while sub.count():
                    sw = sub.takeAt(0).widget()
                    if sw:
                        sw.hide()
                        sw.deleteLater()
        vers = pipeline.dub_versions(self.p)
        cur = (self.p.edit.get("dub") or {}).get("wav")
        on = bool((self.p.edit.get("dub") or {}).get("enabled"))
        if not vers:
            lay.addWidget(label("Chưa có bản nào.", "muted"))
            return
        n = len(vers)
        for k, v in enumerate(vers):
            st = v.get("stats") or {}
            cnt = f" · {st.get('placed', 0)}/{st.get('total', 0)} câu" if st.get("total") else ""
            active = v["wav"] == cur
            mark = ("✓ " if on else "◦ ") if active else "    "
            when = f"tạo {v.get('created') or v.get('time', '')}"
            if v.get("edits"):
                when += f" · đã sửa {v['edits']} lần, lần cuối {v.get('time', '')}"
            text = f"{mark}Bản {n - k} · {when}{cnt}" + (f" · {v['voices']}" if v.get("voices") else "")
            lb = label(text, "" if active else "muted", wrap=True)   # dòng dài tự xuống dòng, không đẩy khung rộng ra
            miss = st.get("total", 0) - st.get("placed", 0)
            lb.setToolTip(v["wav"] + (f"\n{miss} câu chưa có giọng (chưa dịch / bỏ lồng / bài hát / đọc lỗi)" if miss > 0 else ""))
            wav = v["wav"]
            row = QWidget()
            row.setLayout(hbox(lb,
                               button("Nghe", "volume", flat=True, on_click=lambda _=False, w=wav: QDesktopServices.openUrl(
                                   QUrl.fromLocalFile(w))),
                               button("Dùng bản này" if not (active and on) else "Đang dùng", "check", flat=True,
                                      on_click=lambda _=False, w=wav: self._use_dub(w)),
                               button("", "trash", "Xoá bản này", danger=True, flat=True,
                                      on_click=lambda _=False, w=wav: self._delete_dub(w))))
            row.layout().setStretch(0, 1)
            lay.addWidget(row)

    def _use_dub(self, wav: str):
        self.flush()
        self.p.reload()
        pipeline.use_dub_version(self.p, wav)
        self.p.save()
        self.populate(reload_media=False)
        self._update_player_audio()
        self.win.toast("Đã chọn bản lồng tiếng này cho video", "ok")

    def _delete_dub(self, wav: str):
        if QMessageBox.question(self, "Xoá bản lồng tiếng?", "Xoá hẳn bản lồng tiếng này khỏi máy?") != \
                QMessageBox.StandardButton.Yes:
            return
        self.flush()
        self.p.reload()
        pipeline.delete_dub_version(self.p, wav)
        self.p.save()
        self.populate(reload_media=False)
        self._update_player_audio()

    def _open_dub(self):
        w = self.p.edit["dub"].get("wav")
        if w and Path(w).exists():
            QDesktopServices.openUrl(QUrl.fromLocalFile(w))
        else:
            self.win.toast("Chưa có bản lồng tiếng", "warn")

    # ================================================================== xuất
    def _apply_live(self):
        """Áp ngay chỉnh sửa (khung hình, màu, tốc độ, đoạn cắt) + kiểu phụ đề lên trình phát bên trái."""
        if not self.p or self.loading:
            return
        try:
            ed = self.collect_edit()
        except Exception:  # noqa: BLE001 – giao diện chưa dựng xong
            return
        i = self.p.data.get("info", {})
        self.player.set_edit(ed, i.get("width", 0), i.get("height", 0), i.get("duration", 0))
        self._refresh_overlay()

    def _update_summary(self):
        if not self.p:
            return
        self._live_timer.start()
        self._update_steps()
        e = self.p.edit
        segs = self.model.segs
        items = [f"Phụ đề: {len(segs)} câu, đã dịch {sum(1 for s in segs if s.trans)}" +
                 (f" → ghép vào video ({dict(C.WHICH)[e['subtitles']['which']]})" if e["subtitles"].get("burn") else " (không ghép)")]
        hsn = dict(C.HARDSUB).get(e["hardsub"].get("mode"), "")
        if e["hardsub"].get("mode") != "none":
            items.append(f"Phụ đề cũ: {hsn} – {len(e['hardsub'].get('regions') or [])} vùng")
        if e["trim"].get("start") or e["trim"].get("end") or e.get("cuts"):
            items.append(f"Cắt: {fmt(e['trim'].get('start') or 0, False)} → "
                         f"{fmt(e['trim']['end'], False) if e['trim'].get('end') else 'hết'}, xoá {len(e.get('cuts', []))} đoạn")
        if e.get("crop"):
            items.append("Crop khung hình")
        if e["aspect"] != "original" or e["resolution"] != "original":
            items.append(f"Khung hình: {e['aspect']} · {e['resolution']}")
        if e["flip_h"] or e["flip_v"] or e.get("rotate"):
            items.append("Lật / xoay")
        if abs(e["speed"] - 1) > 1e-3:
            items.append(f"Tốc độ ×{e['speed']:.2f}")
        if e["dub"].get("enabled") and e["dub"].get("wav"):
            items.append("Lồng tiếng Việt" + (" + nhạc nền gốc đã tách" if e["dub"].get("bg_wav") else ""))
        if e["bgm"].get("path"):
            items.append("Nhạc nền thêm")
        if e["watermark"].get("path"):
            items.append("Logo")
        if e["text"].get("content"):
            items.append(f"Chữ: “{e['text']['content']}”")
        self.x_summary.setText("Sẽ xuất với:\n• " + "\n• ".join(items))

    def _export_sub(self, fmt_: str, which: str | None):
        which = which or self.st_which.currentData()
        stem = self.p.source.stem if self.p.data.get("source") else "phude"
        suffix = {"trans": "vi", "source": "zh", "both": "zh-vi"}.get(which, "vi")
        path, _ = QFileDialog.getSaveFileName(self, "Lưu phụ đề", str(Path.home() / f"{stem}.{suffix}.{fmt_}"),
                                              f"*.{fmt_}")
        if not path:
            return
        i = self.p.data.get("info", {})
        save_subtitles(self.model.segs, path, which, _deep_merge(self.settings.get("sub_style") or {}, self.current_style()),
                       i.get("width") or 1920, i.get("height") or 1080)
        self.win.toast(f"Đã lưu {Path(path).name}", "ok")

    def _fill_outputs(self):
        outs = list(reversed(self.p.data.get("outputs", [])))
        self.outs.setRowCount(len(outs))
        for r, o in enumerate(outs):
            ic = {"video": "film", "audio": "music"}.get(o.get("kind"), "subtitles")
            it = QTableWidgetItem(icons.icon(ic, theme.ACCENT2), o["name"])
            it.setToolTip(o["path"])
            self.outs.setItem(r, 0, it)
            b1 = button("Mở", "play", flat=True, on_click=lambda _=False, pth=o["path"]: QDesktopServices.openUrl(QUrl.fromLocalFile(pth)))
            b2 = button("Thư mục", "folder", flat=True,
                        on_click=lambda _=False, pth=o["path"]: QDesktopServices.openUrl(QUrl.fromLocalFile(str(Path(pth).parent))))
            self.outs.setCellWidget(r, 1, b1)
            self.outs.setCellWidget(r, 2, b2)

    # ================================================================== khác
    def _tab_changed(self, i: int):
        self.stack.setCurrentIndex(i)
        self.player.view.hs.show_outline = i == 2   # tab Xoá phụ đề: hiện khung vùng đã khoanh trên video
        self.player.view.hs.update()
        if i == 2 and not self.hs_editor.pix:
            self._grab_frame()
        if i == 7:
            self._update_summary()

    def _rename(self):
        if self.p and self.name.text().strip():
            self.p.reload()
            self.p.data["name"] = self.name.text().strip()
            self.p.save()
            self.win.refresh_projects()

    def _open_dir(self):
        if self.p:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.p.output_dir)))

    def _delete(self):
        if not self.p:
            return
        if QMessageBox.question(self, "Xoá dự án", "Xoá dự án này?\n(File video gốc trên máy KHÔNG bị xoá; chỉ xoá dữ liệu dự án "
                                "và các file đã xuất trong thư mục dự án.)") != QMessageBox.StandardButton.Yes:
            return
        self.player.mp.stop()
        self.player.mp.setSource(QUrl())
        self.p.delete()
        self.p = None
        self.win.refresh_projects()
        self.win.go_home()
