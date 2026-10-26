"""Trang làm việc với 1 dự án: trình phát + 7 tab chức năng."""
from __future__ import annotations

import time
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, QUrl
from PySide6.QtGui import QDesktopServices, QFont, QKeySequence, QPixmap, QShortcut
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QDoubleSpinBox, QFileDialog, QFrame,
                               QHeaderView, QInputDialog, QLineEdit, QMessageBox, QPlainTextEdit,
                               QScrollArea, QSpinBox, QSplitter, QStackedWidget, QTableView, QTableWidget,
                               QTableWidgetItem, QVBoxLayout, QWidget, QGridLayout, QSizePolicy)

from .. import langs, pipeline
from ..config import _deep_merge, load_settings
from ..jobs import manager
from ..karaoke import DEFAULT_COLOR as KARA_COLOR, MODES as KARA_MODES
from ..project import Project
from ..subtitle import Segment, load_subtitles, normalize, save_subtitles, shift_ct

from . import common as C
from . import icons, theme
from .common import run_async
from .editors import RegionEditor, Timeline
from .player import Player, fmt
from .subs_model import (C_END, C_FIT, C_NODUB, C_SEL, C_SONG, C_SPK, C_START, C_VI, C_VOICE, C_ZH, SpeakerDelegate,
                         WrapDelegate,
                         SubsFilter, SubsModel, VoiceDelegate)
from .widgets import (Card, ColorButton, Form, GradientButton, IconButton, IconTabs, PillTabs, ProgressBar,
                      SectionCard, SliderRow, Switch, button, combo, flow, hbox, label, set_combo, wrap)

UNDO_TIP = ("Hoàn tác (Ctrl+Z): xoá / gộp / tách / kéo câu, sửa chữ, dịch, tạo giọng, tách / nối câu, căn mép, lọc cảm "
            "thán, cắt video (đầu / cuối, đoạn giữa, Rút khoảng lặng), tách / xoá / chèn đoạn video, tắt tiếng gốc, chữ & logo")


def safe_stem(p: Project) -> str:
    return (p.source.stem if p.data.get("source") else p.data.get("name", "video"))[:60]


def _add_voices(cb: QComboBox, voices: list[dict]) -> None:
    """Thêm giọng vào ô chọn; giọng có "tip" (giọng trend: hợp với loại video nào) hiện khi rê chuột."""
    for v in voices:
        cb.addItem(v["name"], v["id"])
        if v.get("tip"):
            cb.setItemData(cb.count() - 1, v["tip"], Qt.ItemDataRole.ToolTipRole)


def scroll(w: QWidget) -> QScrollArea:
    sa = QScrollArea()
    sa.setWidgetResizable(True)
    sa.setWidget(w)
    sa.setFrameShape(QScrollArea.Shape.NoFrame)
    sa.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    return sa



def _rowlbl(text: str) -> QWidget:
    """Nhãn đầu hàng (bề rộng cố định -> các hàng nút thẳng cột)."""
    lb = label(text, "muted")
    lb.setFixedWidth(84)
    return lb


def _vsep() -> QWidget:
    """Vạch dọc mảnh chia các nhóm nút trong 1 hàng."""
    f = QFrame()
    f.setFrameShape(QFrame.Shape.VLine)
    f.setFixedSize(1, 26)
    f.setStyleSheet(f"background: {theme.BORDER}; border: none;")
    return f

class ProjectPage(QWidget):
    def __init__(self, win):
        super().__init__()
        self.win = win
        self.p: Project | None = None
        self.loading = False
        self.settings = load_settings()
        self.cut_a: float | None = None
        self._mirrors: list[tuple[QComboBox, QComboBox]] = []   # ô chọn ở tab Phụ đề <-> ô gốc ở tab Tự động
        self._ratio_view = ""          # đang 👁 xem thử 1 tỉ lệ xuất thêm (tab Xuất)
        self._ratio_over: dict = {}    # chỉnh riêng từng tỉ lệ xuất thêm (edit["ratio_over"])
        self._junction_cut = None      # ▶ xem chỗ nối 1 đoạn chờ cắt
        self._after_job: dict = {}     # việc nền xong -> gọi tiếp (mã việc -> hàm)
        # danh sách câu trên đĩa lúc giao diện nạp / lưu lần cuối: việc nền (tách câu, nối câu, dịch…) ghi câu mới trong lúc
        # bảng còn hiện danh sách cũ -> không ghi đè (xem _save_segs_now)
        self._disk_sig: list | None = None
        self._loaded_at = 0.0
        self._chained: set[str] = set()   # việc đã gọi việc tiếp theo (không gửi 2 lần)
        self._timeline_need: dict[str, dict] = {}   # dự án đo thấy âm thanh lệch giờ hình (phiên này) -> độ lệch

        self._save_edit_timer = QTimer(self, singleShot=True, interval=500, timeout=self._save_edit_now)
        self._live_timer = QTimer(self, singleShot=True, interval=40, timeout=self._apply_live)
        self._save_segs_timer = QTimer(self, singleShot=True, interval=800, timeout=self._save_segs_now)
        # đổi cách xếp giọng ở tab Lồng tiếng -> xếp lại bản lồng (đợi người dùng chỉnh xong)
        self._relayout_timer = QTimer(self, singleShot=True, interval=900,
                                      timeout=lambda: self._relayout("Đã xếp lại giọng lồng theo cách mới"))
        # xoá / kéo / gộp nhiều câu liền nhau -> gộp thành 1 lần xếp lại (đợi 1,2 s sau thao tác cuối)
        self._relayout_whats: list[str] = []
        self._relayout_go = QTimer(self, singleShot=True, interval=1200, timeout=self._relayout_now)

        # ---------------- thanh trên cùng: tên dự án · trạng thái lưu · Xuất (như trình dựng phim)
        self.name = QLineEdit()
        self.name.setFont(theme.font(14, QFont.Weight.ExtraBold))
        self.name.setStyleSheet("background: transparent; border: 1px solid transparent; padding: 2px 6px;")
        self.name.editingFinished.connect(self._rename)
        self.saved = label("", "muted")
        export_btn = GradientButton("Xuất video", "export")
        export_btn.clicked.connect(lambda: self.tabs.set_current(self.TAB_EXPORT))
        b_dir = IconButton("folder", "Mở thư mục dự án", 34)
        b_dir.clicked.connect(self._open_dir)
        b_del = IconButton("trash", "Xoá dự án", 34)
        b_del.clicked.connect(self._delete)
        self.insp_btn = IconButton("panel_right", "Ẩn / hiện khung \"Câu đang chọn\" bên phải", 34)
        self.insp_btn.clicked.connect(lambda: self._show_inspector(not self.insp_box.isVisible()))
        # video gốc bị chuyển chỗ / đổi tên / xoá: nút chọn lại file (giữ nguyên phụ đề, bản dịch, lồng tiếng)
        self.relink_btn = button("Tìm lại video gốc…", "folder", "Video gốc của dự án không còn ở chỗ cũ – chọn lại "
                                 "file video (phụ đề, bản dịch, lồng tiếng giữ nguyên)", danger=True,
                                 on_click=self._relink_source)
        self.relink_btn.hide()
        # dự án cũ đo thấy âm thanh lệch giờ hình: nút căn lại ngay trên thanh đầu (cùng nút ở tab Âm thanh mục 1)
        self.retime_btn = button("Căn giờ theo hình", "clock", "Âm thanh của video lệch giờ so với hình – phụ đề / giọng "
                                 "lồng càng về sau càng lệch khẩu hình. Bấm để căn bản tách giọng, mốc câu, bản lồng cho "
                                 "khớp hình (không phải nghe, dịch hay đọc lại).", on_click=lambda: self.run_retime())
        self.retime_btn.hide()
        self.undo_btn = IconButton("undo", UNDO_TIP, 34)
        self.undo_btn.clicked.connect(self.undo)
        self.redo_btn = IconButton("redo", "Làm lại (Ctrl+Y)", 34)
        self.redo_btn.clicked.connect(self.redo)
        self._undo: list[str] = []      # ảnh chụp danh sách câu TRƯỚC mỗi thao tác (json)
        self._redo: list[str] = []
        self._undo_cur: str | None = None   # ảnh chụp trạng thái hiện tại
        self._undo_t = 0.0
        self._undo_busy = False
        head = hbox(self.name, self.saved, None, self.relink_btn, self.retime_btn, self.undo_btn, self.redo_btn, self.insp_btn, b_dir,
                    b_del, export_btn)
        head.setSpacing(8)

        # ---------------- giữa: khung xem trước (trình phát) + tiến độ tác vụ
        self.player = Player()
        self.player.positionChanged.connect(self._on_pos)
        self.player.mediaError.connect(self._media_error)
        self.player.subtitleEdited.connect(self._sub_edited)
        self.player.regionsEdited.connect(self._regions_edited)
        self.ov_which = combo([("trans", "Xem: Tiếng Việt"), ("both", "Xem: Song ngữ"), ("source", "Xem: Tiếng Trung")])
        self.ov_which.setToolTip("Phụ đề hiện trên khung xem trước: bản dịch / song ngữ / tiếng Trung gốc")
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
                              ("Chỉnh sửa", "scissors"), ("Kiểu chữ", "palette"), ("Chữ+Logo", "text"),
                              ("Lồng tiếng", "mic"), ("Âm thanh", "volume"), ("Tài liệu", "download"),
                              ("Xuất", "export")])
        self.stack = QStackedWidget()
        for build in (self._tab_auto, self._tab_subs, self._tab_hardsub, self._tab_edit, self._tab_style,
                      self._tab_layers, self._tab_dub, self._tab_audio, self._tab_models, self._tab_export):
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
        self.tracks.subsMoved.connect(self._subs_moved)
        self.tracks.dubReset.connect(lambda r: self._dub_moved(r, None))
        self.tracks.layerSelected.connect(lambda lid: self._layer_selected(lid, from_tl=True))
        self.tracks.layerTimed.connect(lambda lid, a, b: self.layers_tab.set_span(lid, a, b))
        self.tracks.layerAction.connect(self._layer_action)
        self.tracks.pendingToggled.connect(lambda i: self.sil.set_on(i, not self.sil.items[i].get("on"))
                                           if 0 <= i < len(self.sil.items) else None)
        self.tracks.pendingChanged.connect(lambda i, a, b: self.sil.set_cut(i, a, b))
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
        # ô chữ đang gõ (Tiếng Việt, tìm kiếm…) tự giữ Ctrl+Z của nó; ngoài ô chữ = hoàn tác thao tác với câu
        for keys, fn in (("Ctrl+Z", self.undo), ("Ctrl+Y", self.redo), ("Ctrl+Shift+Z", self.redo)):
            QShortcut(QKeySequence(keys), self, activated=fn,
                      context=Qt.ShortcutContext.WidgetWithChildrenShortcut)
        self._update_undo_btns()

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
            self._show_profile(H >= 1300)   # mở đầu ẩn 2 ô hồ sơ phim / tên riêng để bảng câu đủ cao (nút trên thẻ)

    def _show_profile(self, on: bool | None):
        """Hiện / ẩn 2 ô Hồ sơ phim + Bảng tên riêng ở tab Phụ đề (None = đảo trạng thái)."""
        on = (not self.sk_more.isVisible()) if on is None else on
        self.sk_more.setVisible(on)
        self.sk_toggle.setText("Ẩn hồ sơ phim / tên riêng" if on else "Hồ sơ phim / tên riêng")
        sizes = self.subs_split.sizes()
        if len(sizes) == 2 and sum(sizes) > 0:
            card = self.subs_split.widget(1).sizeHint().height()
            self.subs_split.setSizes([max(60, sum(sizes) - card), card])

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
        self.a_spk = f.row("Phân biệt người nói", Switch(bool(s.get("diarize", True))),
                           "Cùng 1 công tắc với Cài đặt → Tự phân biệt người nói. Số người nói / độ nhạy ở Cài đặt")
        self.a_split = f.row("Kiểu tách câu", combo(C.SPLIT_STYLES, s.get("split_style", "dub")), C.SPLIT_HELP)
        self.a_dual = f.row("Nghe 2 bộ rồi so", Switch(bool(s.get("asr_dual", False))),
                            C.DUAL_HELP + ". Cùng 1 công tắc với Cài đặt → Nhận dạng giọng nói")
        self.a_dual.toggled.connect(self._audio_opts_changed)
        self.a_sep.toggled.connect(self._audio_opts_changed)
        self.a_spk.toggled.connect(self._audio_opts_changed)
        self.a_split.currentIndexChanged.connect(self._audio_opts_changed)
        self.a_joinai = f.row("AI nối câu theo nghĩa", combo(pipeline.join_engine_choices(s),
                                                             s.get("ai_join_engine", "auto")),
                              "Sau khi tách câu: AI đọc cả kịch bản, gộp các dòng thực ra là 1 câu mà luật không đoán "
                              "được (\"这笔账。\" | \"你自己算。\"). Chọn AI trong danh sách đã nhập key ở Cài đặt → Công cụ "
                              "dịch. Tắt = chỉ dùng luật nối câu (không tốn lượt AI)")
        self.a_joinai.currentIndexChanged.connect(self._audio_opts_changed)
        self.a_engine = f.row("Công cụ dịch", combo(C.ENGINES + [(pipeline.NO_TRANSLATE, "Không dịch – chỉ nghe & tách câu, "
                                                                 "xuất SRT gốc (dịch ở phần mềm khác)")],
                                                    s.get("translate_engine")))
        self.a_target = f.row("Dịch sang", combo(C.TARGETS, s.get("target_lang", "vi")),
                              "Tiếng đích của dự án này: bản dịch, phụ đề ghép vào video và giọng lồng tiếng đều theo "
                              "tiếng này. Edge / OmniVoice đọc được cả 5 tiếng; VieNeu chỉ đọc tiếng Việt")
        self.a_target.currentIndexChanged.connect(self._target_changed)
        self.a_style = f.row("Thể loại / văn phong", combo(C.STYLES, s.get("translate_style")))
        self.a_skill = combo(self._skill_items(s), s.get("translate_skill", ""))
        self.a_skill.currentIndexChanged.connect(self._skill_changed)
        sk_row = hbox(self.a_skill, button("Sửa / thêm…", "wand", "Xem, thêm, sửa kỹ năng dịch của bạn",
                                           on_click=self._edit_skills))
        sk_row.setStretch(0, 1)
        f.row("Kỹ năng dịch AI", sk_row,
              "Luật riêng gắn vào lời nhắc của MỌI công cụ dịch AI (DeepSeek / Claude / GPT / Gemini / Ollama / API "
              "tương thích OpenAI). Không dùng với Google / Hy-MT2")
        self.a_clean = f.row("AI sửa lỗi nghe & nối câu trước khi dịch", Switch(bool(s.get("ai_clean_before_translate", True))),
                             "Trước khi dịch cả phim lần đầu: AI sửa chữ máy nghe nhầm (đồng âm) và nối câu bị cắt sai "
                             "chỗ. Có nút Hoàn tác ở tab Phụ đề")
        self.a_songai = f.row("AI lọc lời bài hát trước khi dịch", Switch(bool(s.get("ai_song_filter", True))),
                              "Trước khi dịch cả phim lần đầu (khi không bật sửa lỗi nghe ở trên – bước đó đã gồm việc "
                              "này): AI đọc cả kịch bản, câu là lời bài hát (văn vần, điệp khúc, không ăn nhập lời thoại) "
                              "-> đánh 🎵 không lồng tiếng; câu máy tự bịa / chữ vô nghĩa lúc chỉ có nhạc -> bỏ. Dựa vào "
                              "nội dung chữ, không dựa vào âm lượng. Có nút Hoàn tác sửa ở tab Phụ đề")
        self.a_review = f.row("AI kiểm tra lại bản dịch", Switch(bool(s.get("ai_review_after_translate", False))),
                              "Sau khi dịch cả phim: AI soát sai nghĩa, xưng hô / tên không thống nhất, câu quá dài, còn "
                              "chữ Hán rồi sửa (tốn thêm ~1 lần gọi AI)")
        self.a_fill = f.row("Câu ngắn thêm từ đệm cho vừa thời gian", Switch(bool(s.get("translate_fill", True))),
                            "Dịch bằng AI: câu dịch ngắn hơn hẳn thời gian nhân vật nói thì nói đầy đủ hơn – thêm từ "
                            "đệm / phụ từ (à, nhé, đấy, cơ, mà, thế…), xưng hô – không thêm ý, không dài quá → giọng "
                            "lồng không hụt so với miệng nhân vật. Tắt = chỉ giới hạn độ dài tối đa")
        # = công tắc "Tự tách câu dịch dài" ở tab Phụ đề (1 cài đặt chung split_long_trans, 2 công tắc luôn giống nhau)
        self.a_split_long = f.row("Tách câu dịch dài", Switch(bool(s.get("split_long_trans", True))),
                                  "Dịch xong cả phim -> câu dịch dài hơn mức đặt ở tab Phụ đề (mặc định 14 tiếng) được "
                                  "tách ở dấu ? ! . / dấu phẩy thành nhiều dòng, giọng lồng nghỉ 1 nhịp giữa 2 vế. Tách "
                                  "xong hết mới lồng tiếng. Cùng 1 công tắc với 'Tự tách câu dịch dài' ở tab Phụ đề")
        self.a_clean.toggled.connect(self._ai_switch_changed)
        self.a_review.toggled.connect(self._ai_switch_changed)
        self.a_songai.toggled.connect(self._ai_switch_changed)
        self.a_fill.toggled.connect(self._ai_switch_changed)
        self.a_hardsub = f.row("Phụ đề Trung cũ", combo(C.HARDSUB, "none"))
        self.a_which = f.row("Ghép phụ đề vào video", combo(C.WHICH, "trans"))
        self.a_dub = f.row("Lồng tiếng (đọc bản dịch)", Switch(False))
        self.a_tts = f.row("Bộ đọc lồng tiếng", combo(C.TTS_ENGINES, s.get("tts_engine", "edge")))
        # cùng 1 bộ đọc với tab Lồng tiếng (trước đây bước "3. Lồng tiếng" dùng ô ở tab Lồng tiếng, ô này bị bỏ qua)
        self.a_tts.currentIndexChanged.connect(
            lambda: None if self.loading or self.d_engine.currentData() == self.a_tts.currentData()
            else set_combo(self.d_engine, self.a_tts.currentData()))
        # giọng lồng: cùng danh sách giọng với tab Lồng tiếng mục 2
        self.a_voice = combo([("", "(Theo giọng từng người nói – mục 2)")], "")
        self.a_voice.setMinimumWidth(240)
        self.a_voice.currentIndexChanged.connect(self._voice_all_changed)
        a_play = IconButton("play", "Nghe thử giọng này", 32)
        a_play.clicked.connect(self._preview_auto_voice)
        v_row = hbox(self.a_voice, a_play)
        v_row.setStretch(0, 1)
        f.row("Giọng lồng", v_row,
              "1 giọng cho MỌI người nói – áp cho mọi nút lồng tiếng (cả '3. Lồng tiếng', 'Tạo bản lồng tiếng') và nhớ làm "
              "mặc định cho video sau. 'Theo giọng từng người nói' = chọn giọng từng người ở tab Lồng tiếng mục 2")
        self.a_parallel = f.row("Vừa tách vừa dịch", Switch(bool(s.get("auto_parallel", False))),
                                "Nghe tới đâu dịch tới đó: đủ 'Số câu mỗi lần gửi' của công cụ dịch (Cài đặt → Công cụ "
                                "dịch) là gửi đi luôn, câu dịch hiện ngay ở tab Phụ đề; Edge đọc trước giọng các câu đã "
                                "dịch. Nghe xong, câu bị đổi (nghe lại đoạn sót / tách người nói) được dịch / đọc lại. "
                                "Dịch trên máy (Hy-MT2) và VieNeu / OmniVoice dùng card đồ hoạ nên chạy sau khi nghe xong")
        self.a_parallel.toggled.connect(lambda on: None if self.loading else self._save_setting("auto_parallel", on))
        self.a_denoise = f.row("Lọc nhiễu âm thanh", Switch(False))
        self.a_force = f.row("Nhận dạng lại từ đầu", Switch(False))
        self.a_method.currentIndexChanged.connect(
            lambda: (self.a_asr.setEnabled(self.a_method.currentData() in ("asr", "hybrid")),
                     self.a_ocrfps.setEnabled(self.a_method.currentData() in ("ocr", "hybrid"))))
        # 1 công cụ dịch cho cả tool: đổi ở đây = đổi ở Cài đặt (nút Dịch, AI rút gọn, dịch lại đều dùng chung)
        self.a_engine.currentIndexChanged.connect(self._engine_changed)
        self.a_style.currentIndexChanged.connect(self._engine_changed)
        # thiết lập tự động dùng chung (Trang chủ "Tải về & dịch tự động", Xử lý hàng loạt): các ô riêng của tab này
        for w in (self.a_method, self.a_asr, self.a_ocrfps, self.a_lang, self.a_hardsub, self.a_which, self.a_tts):
            w.currentIndexChanged.connect(self._preset_changed)
        for w in (self.a_dub, self.a_denoise):
            w.toggled.connect(self._preset_changed)
        card.add(f)
        card.add(hbox(label("Cùng 1 bộ thiết lập với 'Tải về & dịch tự động' (Trang chủ) và Xử lý hàng loạt", "muted"),
                      None))
        go = GradientButton("Bắt đầu dịch tự động", "bolt", big=True)
        go.setToolTip("Tự làm: nghe & tách câu → dịch → lồng tiếng (nếu bật) rồi DỪNG. Kiểm tra xong bấm 'Xuất video' "
                      "(nút trên cùng hoặc tab Xuất) để ghép video")
        go.clicked.connect(self.run_auto)
        card.add(go)
        card.add(label("Dừng trước bước xuất – kiểm tra xong bấm 'Xuất video' để ghép video.", "muted"))
        steps = SectionCard("Làm từng bước", "list",
                            "Đi lần lượt 4 bước, soát kết quả từng bước trước khi sang bước sau (bước nào xong có dấu ✓).")
        grid = QGridLayout()
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(4)
        step_defs = [
            ("1. Nghe && tách câu", "mic", self.run_recognize,
             "Nghe lời thoại gốc: ghi mốc lúc nhân vật BẮT ĐẦU nói tới lúc DỨT câu, tách đúng từng câu (và từng người nói)."),
            ("2. Dịch", "globe", self._step_translate,
             "Dịch từng câu sang ngôn ngữ ở ô 'Dịch sang', độ dài vừa với thời gian nhân vật nói. Đã dịch một phần thì "
             "chỉ dịch các câu còn trống (giữ câu bạn đã sửa)."),
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
        # sửa chữ trong bảng / khung Câu đang chọn: các lần sửa liền nhau = 1 bước hoàn tác
        self.model.edited.connect(lambda *_: (self._undo_mark(join=True), self._segs_edited()))
        self.proxy = SubsFilter()
        self.proxy.setSourceModel(self.model)
        tv = QTableView()
        tv.setModel(self.proxy)
        tv.setAlternatingRowColors(True)
        tv.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        tv.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)   # Ctrl / Shift chọn nhiều dòng
        tv.setWordWrap(True)
        tv.setTextElideMode(Qt.TextElideMode.ElideNone)
        tv.setShowGrid(False)   # lưới trong suốt vẫn chừa khe 1 px -> dòng đang chọn (tô cam) bị chia vạch
        tv.verticalHeader().hide()
        tv.verticalHeader().setDefaultSectionSize(40)
        hh = tv.horizontalHeader()
        for c, w in ((C_SEL, 52), (0, 40), (C_START, 92), (C_END, 92), (C_SPK, 58)):
            hh.setSectionResizeMode(c, QHeaderView.ResizeMode.Fixed)
            tv.setColumnWidth(c, w)
        hh.setSectionResizeMode(C_ZH, QHeaderView.ResizeMode.Stretch)
        hh.setSectionResizeMode(C_VI, QHeaderView.ResizeMode.Stretch)
        hh.moveSection(hh.visualIndex(C_SEL), 0)          # Chọn | # | giờ | Người | Tiếng Trung | Tiếng Việt
        hh.sectionClicked.connect(lambda c: self._toggle_pick_all(self.proxy) if c == C_SEL else None)
        tv.setItemDelegateForColumn(C_SPK, SpeakerDelegate(tv))
        self._wrap_delegate = WrapDelegate(tv)
        for c in (C_ZH, C_VI):
            tv.setItemDelegateForColumn(c, self._wrap_delegate)
        tv.setEditTriggers(QAbstractItemView.EditTrigger.DoubleClicked | QAbstractItemView.EditTrigger.EditKeyPressed |
                           QAbstractItemView.EditTrigger.AnyKeyPressed)
        for c in (C_SONG, C_NODUB, C_FIT, C_VOICE):   # cột của lồng tiếng: ở bảng câu tab Lồng tiếng
            tv.setColumnHidden(c, True)
        tv.clicked.connect(self._row_clicked)
        self.table = tv
        tv.verticalScrollBar().valueChanged.connect(self._fit_visible)   # đo chiều cao dòng khi cuộn tới
        self._refit_timer = QTimer(self, singleShot=True, interval=120)
        self._refit_timer.timeout.connect(self._fit_rows)
        # lọc / thêm / xoá câu / cột đổi bề rộng (mở dự án, kéo khung) -> đo lại các dòng đang hiện
        for sig in (self.proxy.modelReset, self.proxy.layoutChanged, self.proxy.rowsInserted, self.proxy.rowsRemoved,
                    tv.horizontalHeader().sectionResized):
            sig.connect(lambda *_: self._refit_timer.start())
        self._edit_triggers = tv.editTriggers()
        self._live_draft = None   # mtime file câu nháp đang hiện (vừa tách vừa dịch)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Tìm trong phụ đề… (gõ S1 để lọc theo người nói)")
        self.search.textChanged.connect(self.proxy.set_query)
        self.follow = Switch(True)
        self.seg_count = label("", "muted")
        # cách dịch: cùng giá trị với tab Tự động / Cài đặt (đổi ở đây = đổi ở đó)
        self.s_engine, self.s_target = self._mirror(self.a_engine), self._mirror(self.a_target)
        self.s_style, self.s_skill = self._mirror(self.a_style), self._mirror(self.a_skill)
        # ---- khung "Dịch" (luôn hiện): cách dịch + 3 nút dịch
        tr_card = SectionCard("Dịch", "globe")
        tr_card.lay.setContentsMargins(14, 12, 14, 12)
        b_tr = GradientButton("Dịch câu chưa dịch", "globe")
        b_tr.setToolTip("Dịch mọi câu chưa có bản dịch (dịch lần đầu: dịch cả phim)")
        b_tr.clicked.connect(lambda: self.run_translate(True))
        tr_card.head.addWidget(flow(
            b_tr, button("Dịch lại tất cả", "refresh", on_click=lambda: self.run_translate(False)),
            button("Dịch câu đã tích", "sparkle", "Dịch (lại) các câu đã tích ở cột Chọn – đã có bản lồng thì "
                   "đọc lại giọng đúng các câu đó", on_click=self._translate_picked)))
        g = QGridLayout()   # 2 hàng × 2 ô chọn, giãn đều theo bề rộng khung (không cắt chữ trong ô)
        g.setHorizontalSpacing(10)
        g.setVerticalSpacing(8)
        for i, (t, w) in enumerate((("Dịch bằng", self.s_engine), ("Dịch sang", self.s_target),
                                    ("Văn phong", self.s_style),
                                    ("Kỹ năng AI", hbox(self.s_skill, button(
                                        "", "wand", "Xem, thêm, sửa kỹ năng dịch AI của bạn", flat=True,
                                        on_click=self._edit_skills))))):
            g.addWidget(_rowlbl(t), i // 2, (i % 2) * 2)
            (g.addWidget if isinstance(w, QWidget) else g.addLayout)(w, i // 2, (i % 2) * 2 + 1)
        for c in (1, 3):
            g.setColumnStretch(c, 1)
        for cb in (self.s_engine, self.s_target, self.s_style, self.s_skill):
            cb.setMinimumWidth(150)
        self.s_skill.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        tr_card.add(g)
        self.s_tr_card = tr_card
        # ---- các trang của khung "Công cụ" (mỗi lúc hiện 1 trang)
        self.s_snap_ct = Switch(False)
        self.s_snap_ct.setToolTip("Bật: đo lại mốc từng chữ bằng Qwen3 trước khi bám mép (dùng card, vài phút với phim dài) – "
                                  "cho dự án nghe trước bản 2.0.96 (chưa lưu mốc từng chữ, cần để giọng lồng nghỉ đúng chỗ "
                                  "ngắt hơi). Tắt: chỉ nhìn độ to giọng – nhanh")
        pg_edit = [
            "Bấm đúp vào ô để sửa chữ · Ctrl / Shift chọn nhiều dòng · tích cột Chọn để làm nhiều câu cùng lúc",
            flow(button("Thêm", "plus", "Thêm câu tại vị trí đang phát", on_click=lambda: self._seg_op("insert")),
                 button("Tách", "split", "Tách câu tại vị trí đang phát", on_click=lambda: self._seg_op("split")),
                 button("Gộp", "merge", "Gộp các câu đã tích (liền nhau), không tích thì gộp câu đang chọn với câu sau",
                        on_click=lambda: self._seg_op("merge")),
                 button("Xoá", "trash", "Xoá câu đang chọn (hoặc các câu đã tích)", danger=True,
                        on_click=lambda: self._seg_op("delete")),
                 _vsep(),
                 button("Bỏ tích", "x", "Bỏ tích mọi câu", flat=True,
                        on_click=lambda: self.model.set_picked(range(len(self.model.segs)), False)),
                 button("Dời giờ", "clock", "Dời giờ mọi câu (hoặc các câu đã tích) sớm / muộn đi vài giây",
                        on_click=self._shift),
                 button("Tìm && thay", "search", on_click=self._replace),
                 button("Căn khớp thoại", "wand", "Căn mốc từng câu sát lời thoại thật, tách câu ở chỗ nghỉ "
                        "(dùng cho phụ đề đã nhận dạng từ bản cũ)", on_click=self.run_realign),
                 _vsep(),
                 button("Lưu", "save", on_click=lambda: (self._save_segs_now(), self.win.toast("Đã lưu phụ đề", "ok")))),
            flow(_rowlbl("Mép câu"),
                 button("Căn mép theo giọng", "silence", "Dời đầu / cuối câu về đúng lúc nhân vật bắt đầu / thôi nói, nhìn "
                        "độ to giọng đã tách mỗi 0,01 s (như sóng âm dòng Tiếng gốc). Câu đã tích hoặc cả phim. Không "
                        "đổi chữ, không tách câu; đã lồng tiếng thì xếp lại giọng theo mốc mới (không đọc lại). "
                        "'1. Nghe & tách câu' tự làm bước này – nút dùng cho dự án cũ / sau khi sửa tay",
                        on_click=self.run_snap),
                 hbox(self.s_snap_ct, label("đo lại mốc từng chữ", "muted")))]
        # câu dịch dài: tách ở chỗ hết câu (? ! .) / dấu phẩy thành nhiều dòng phụ đề + đoạn giọng, giữa 2 vế nghỉ 1 nhịp
        self.s_split = Switch(bool(self.settings.get("split_long_trans", True)))
        self.s_split.setToolTip("Sau khi dịch cả phim: câu dịch dài hơn mức đặt có dấu ? ! . hoặc dấu phẩy ở giữa câu (cả câu gốc lẫn "
                                "câu dịch) -> tự tách thành các vế (mỗi vế 1 dòng phụ đề, giọng lồng nghỉ 1 nhịp giữa 2 "
                                "vế). Ưu tiên tách ở chỗ hết câu (? ! .)")
        self.s_split_n = QSpinBox()
        self.s_split_n.setRange(6, 60)
        self.s_split_n.setSuffix(" tiếng")
        self.s_split_n.setValue(int(self.settings.get("split_long_units", 14) or 14))
        self.s_split_n.setToolTip("Câu dịch dài hơn số tiếng này mới tách (tiếng Anh / Thái / Nhật / Hàn tự quy đổi "
                                  "theo tốc độ nói)")
        self.s_split_p = QDoubleSpinBox()
        self.s_split_p.setRange(0.0, 1.5)
        self.s_split_p.setSingleStep(0.05)
        self.s_split_p.setDecimals(2)
        self.s_split_p.setSuffix(" s")
        self.s_split_p.setValue(float(self.settings.get("split_pause", 0.25)))
        self.s_split_p.setToolTip("Giọng lồng nghỉ tối thiểu bấy nhiêu giây giữa 2 vế của câu đã tách (tách ở ? ! . thì nghỉ "
                                "gấp 1,6 lần)")
        for w in (self.s_split, self.s_split_n, self.s_split_p):
            (w.toggledOn if w is self.s_split else w.valueChanged).connect(self._split_opts_changed)
        self.a_split_long.toggled.connect(
            lambda on: None if self.loading or self.s_split.isChecked() == on else self.s_split.setChecked(on))
        pg_join = [
            "Nối dòng bị cắt ngang câu (máy nghe cắt sai chỗ) · tách câu dịch quá dài thành nhiều dòng, giữa 2 vế "
            "giọng lồng nghỉ 1 nhịp",
            flow(_rowlbl("Nối câu"),
                 button("Nối câu bị cắt", "merge", "Tự nối các dòng bị cắt ngang câu (cắt giữa 1 từ như 真|的, mẩu "
                        "cụt như 子。, câu chưa hết) rồi tách lại đúng chỗ – không cần AI, không nghe lại. Theo Kiểu "
                        "tách câu ở tab Tự động. Câu đổi được tích sẵn để dịch lại", on_click=self.run_join),
                 button("AI nối câu", "sparkle", "AI đọc cả kịch bản, gộp các dòng thực ra là 1 câu mà luật không "
                        "đoán được (\"这笔账。\" | \"你自己算。\") – chỉ gộp, không đổi chữ. AI chọn ở tab Tự động / "
                        "Cài đặt → 'AI nối câu theo nghĩa'. Câu gộp được tích sẵn", on_click=self.run_ai_join),
                 button("Hoàn tác nối", "undo", flat=True, on_click=self.run_undo_join)),
            flow(_rowlbl("Tách câu dài"),
                 hbox(self.s_split, label("tự tách câu dịch dài hơn", "muted"), self.s_split_n),
                 hbox(label("nghỉ giữa 2 vế", "muted"), self.s_split_p),
                 button("Tách câu dài", "split", "Tách ngay các câu dịch dài hơn mức đặt ở dấu ? ! . hoặc dấu phẩy giữa "
                        "câu (các câu đã tích, chưa tích thì cả phim). Chỗ tách phải có dấu câu ở cả câu gốc lẫn câu "
                        "dịch, đúng vị trí tương ứng. Mốc đặt đúng chỗ ngắt hơi. Đã lồng tiếng thì đọc lại giọng các vế "
                        "mới", on_click=self.run_split_long),
                 button("Hoàn tác tách", "undo", flat=True, on_click=self.run_undo_split))]
        # câu nghe nhầm: nhạc / tiếng động bị chép thành lời -> lọc theo cao độ giọng thật của từng người nói
        self.s_ptol = QDoubleSpinBox()
        self.s_ptol.setRange(1.0, 12.0)
        self.s_ptol.setSingleStep(0.5)
        self.s_ptol.setDecimals(1)
        self.s_ptol.setPrefix("± ")
        self.s_ptol.setSuffix(" nửa cung")
        self.s_ptol.setValue(float(self.settings.get("pitch_tol", 4.0)))
        self.s_ptol.setToolTip("Lệch cao độ cho phép ngoài khoảng giọng thường của người nói. Nhỏ = lọc chặt (dễ đánh "
                               "dấu nhầm câu kêu / hét), lớn = lọc lỏng (chỉ bắt nhạc cụ / giọng hát cao hẳn)")
        self.s_ptol.valueChanged.connect(self._ptol_changed)
        pg_song = [
            "Nhạc / lời bài hát / tiếng động bị nghe thành lời thoại: đánh 🎵 (không lồng tiếng) hoặc đánh dấu để "
            "nghe lại rồi xoá",
            flow(button("AI lọc lời bài hát", "wand", "Nhờ công cụ dịch AI đọc cả kịch bản: câu là lời bài hát (văn vần, "
                        "điệp khúc, không ăn nhập lời thoại) -> đánh 🎵 (không lồng tiếng); câu máy tự bịa / chữ vô nghĩa "
                        "lúc chỉ có nhạc -> bỏ; đoạn lời hát dính vào câu thoại -> xoá phần đó. Dựa vào nội dung, không "
                        "dựa vào âm lượng. Không ưng: Hoàn tác sửa", on_click=self.run_ai_filter),
                 button("Lọc câu nghe nhầm", "music", "Đo cao độ giọng thật của từng người nói (trên giọng đã tách) – câu "
                        "nằm ngoài khoảng cao độ đó, gần như không có giọng nói, hoặc nhỏ hẳn so với lời thoại = nhạc / "
                        "tiếng động bị nghe thành lời → đánh dấu + tích sẵn để nghe lại rồi xoá. Đồng thời thu đầu / "
                        "cuối câu kéo sang nhạc cho khớp lúc nói", on_click=self.run_voice_check),
                 hbox(label("lệch cao độ cho phép", "muted"), self.s_ptol)),
            flow(button("Tích câu nghi nghe nhầm", "check", flat=True, on_click=lambda: self._pick_flag("noise")),
                 button("Không phải nhạc", "x", "Bỏ đánh dấu các câu đã tích (câu thoại thật) – lần lọc sau không đánh "
                        "dấu lại", flat=True, on_click=self._voice_ok)),
            flow(_rowlbl("Câu bịa / lặp"),
                 button("Dọn câu bịa / câu lặp", "wand", "Bỏ câu bộ nghe chép lại tiêu đề / mô tả / thẻ video (đoạn nhạc, "
                        "không ai nói – lỗi bản 2.1.0–2.1.7); bỏ chỗ nói vấp (我不管闲，我不管闲事 -> 我不管闲事 – "
                        "không thì dịch / lồng tiếng thành 2 lần); câu có 1 cụm lặp liền nhau và 2 câu liền nhau gần giống "
                        "nhau -> Qwen3-ASR nghe lại đúng đoạn đó: chỉ ra 1 lần thì bỏ lần lặp / gộp 2 câu, vẫn ra 2 lần "
                        "(nói 2 lần thật) thì giữ. Không phải nghe lại cả phim. Không ưng: Hoàn tác sửa",
                        on_click=self.run_clean_asr),
                 button("Tích câu nghi lặp", "check", "Câu chữ gốc màu hồng: còn 1 cụm lặp liền nhau / 2 câu liền nhau "
                        "gần giống nhau – nghe lại rồi sửa hoặc Xoá", flat=True,
                        on_click=lambda: self._pick_flag("asr_repeat"))),
            flow(_rowlbl("2 bộ nghe"),
                 button("Nghe 2 bộ rồi so", "mic", C.DUAL_HELP + ". Chạy cho câu đã tích (chưa tích = cả phim)",
                        on_click=self.run_dual),
                 button("Tích câu 2 bộ nghe khác nhau", "check", "Câu chữ gốc màu xanh: 2 bộ nghe ra khác nhau mà chưa "
                        "chọn được – rê chuột vào chữ gốc xem bản của bộ 2", flat=True,
                        on_click=lambda: self._pick_flag("asr_alt")),
                 button("Dùng bản bộ nghe 2", "refresh", "Câu đã tích: thay chữ gốc bằng chữ bộ nghe thứ 2 nghe được (giữ "
                        "dấu câu). Ctrl+Z hoàn tác", flat=True, on_click=self._use_alt)),
            flow(_rowlbl("Tên riêng"),
                 button("Sửa tên theo âm đọc", "wand", "Chỗ đọc giống hệt 1 tên trong Bảng tên riêng / hồ sơ phim mà viết "
                        "khác (临北 → 林北) -> sửa thành đúng tên. Ctrl+Z hoàn tác", flat=True, on_click=self._fix_names))]
        # giờ HIỆN phụ đề theo giọng lồng (2.0.96) – không đổi giờ nói của câu
        from ..subtitle import FOLLOW_MODES

        st0 = self.settings
        self.s_follow = Switch(bool(st0.get("dub_subs_follow", True)))
        self.s_follow.setToolTip("Bật: phụ đề đã dịch hiện theo đúng lúc giọng lồng đọc (xem trước, video xuất, SRT đã "
                                 "dịch). Cùng công tắc với 'Phụ đề chạy theo giọng lồng' ở tab Lồng tiếng")
        self.s_follow_mode = QComboBox()
        for k, t in FOLLOW_MODES:
            self.s_follow_mode.addItem(t, k)
        set_combo(self.s_follow_mode, st0.get("sub_follow_mode", "always"))
        self.s_follow_mode.setToolTip("Luôn theo giọng lồng: mọi câu có giọng hiện từ lúc giọng bắt đầu tới lúc đọc xong "
                                      "(theo các mức bên dưới). Chỉ khi lệch: như bản cũ – chỉ câu giọng đọc sớm / bị "
                                      "lùi / đọc dài hơn phụ đề quá 0,3 s")

        def _fspin(lo, hi, val, tip):
            w = QDoubleSpinBox()
            w.setRange(lo, hi)
            w.setSingleStep(0.05)
            w.setDecimals(2)
            w.setSuffix(" s")
            w.setValue(float(val))
            w.setToolTip(tip)
            w.setMaximumWidth(96)
            return w
        self.s_lead = _fspin(0.0, 1.0, st0.get("sub_lead", 0.1), "Chữ hiện trước lúc giọng lồng bắt đầu bấy nhiêu giây "
                             "(mắt đọc kịp trước khi nghe)")
        self.s_hold = _fspin(0.0, 2.0, st0.get("sub_hold", 0.3), "Chữ còn trên màn hình bấy nhiêu giây sau khi giọng đọc xong")
        self.s_min = _fspin(0.3, 4.0, st0.get("sub_min_dur", 1.0), "Câu ngắn (A! / Hả?) vẫn hiện ít nhất bấy nhiêu giây "
                            "(không đè câu sau)")
        self.s_join = _fspin(0.0, 1.0, st0.get("sub_join_gap", 0.3), "Khoảng trống giữa 2 câu ngắn hơn mức này -> câu trước "
                             "giữ tới khi câu sau hiện (chữ không nháy tắt – bật)")
        self.s_follow.toggledOn.connect(self._follow_opts_changed)
        self.s_follow_mode.currentIndexChanged.connect(self._follow_opts_changed)
        for w in (self.s_lead, self.s_hold, self.s_min, self.s_join):
            w.valueChanged.connect(self._follow_opts_changed)
        pg_show = [
            "Chỉ đổi lúc HIỆN chữ trên video, khung xem trước và SRT đã dịch – không đổi giờ nói của câu, chỗ đặt giọng "
            "lồng, karaoke. Câu chưa có giọng lồng hiện theo giờ nói. Trên timeline: vạch trắng dưới khối phụ đề = giờ hiện",
            flow(_rowlbl("Theo giọng"), self.s_follow, label("Phụ đề hiện theo giọng lồng", "muted"), self.s_follow_mode),
            flow(_rowlbl("Thời gian"), hbox(label("Hiện trước", "muted"), self.s_lead),
                 hbox(label("Giữ thêm", "muted"), self.s_hold), hbox(label("Tối thiểu", "muted"), self.s_min),
                 hbox(label("Nối khoảng hở <", "muted"), self.s_join),
                 button("Mặc định", "refresh", "Về 0,1 / 0,3 / 1,0 / 0,3 s", flat=True, on_click=self._follow_defaults))]
        self.profile_edit = QPlainTextEdit()
        self.profile_edit.setPlaceholderText(
            "AI tự lập khi dịch lần đầu: thể loại, nhân vật, xưng hô, tên riêng / meme. Sửa được – AI dùng cho mọi lần "
            "dịch / kiểm tra sau. VD:\n- 小克 -> Claude – AI bạn của Thần D\n- các AI: tớ – cậu")
        self.profile_edit.setMinimumHeight(64)
        self.profile_edit.textChanged.connect(lambda: self._save_edit_timer.start())
        self.glossary = QPlainTextEdit()
        self.glossary.setPlaceholderText("Mỗi dòng: 中文=Tiếng Việt   (VD: 李逍遥=Lý Tiêu Dao). AI tự điền khi dịch.")
        self.glossary.setMinimumHeight(64)
        self.glossary.textChanged.connect(lambda: self._save_edit_timer.start())
        # 1 thẻ chung: kỹ năng dịch AI + hồ sơ phim + bảng tên riêng (2 ô cạnh nhau) – kéo vách trên để đổi chiều cao
        sk = SectionCard("Kỹ năng dịch AI", "sparkle")
        self.sk_toggle = button("Ẩn hồ sơ phim / tên riêng", "chev_down", "Hiện / ẩn 2 ô Hồ sơ phim và Bảng tên riêng "
                                "(ẩn để bảng câu cao hơn)", flat=True, on_click=lambda: self._show_profile(None))
        sk.head.addWidget(self.sk_toggle)
        sk.add(flow(button("AI sửa lỗi nghe && nối câu", "wand", "Sửa chữ máy nghe nhầm (đồng âm) + nối câu bị cắt sai "
                           "chỗ. Giữ bản cũ để Hoàn tác; câu AI sửa được tích sẵn, rê chuột vào câu gốc để xem trước / "
                           "sau", on_click=self.run_ai_clean),
                    button("Hoàn tác sửa", "refresh", flat=True, on_click=self.run_undo_clean),
                    button("AI kiểm tra bản dịch", "check", "Soát và sửa câu dịch sai nghĩa / xưng hô, tên không thống "
                           "nhất / quá dài / còn chữ Hán. Câu bạn sửa tay không bị đụng tới", on_click=self.run_ai_review),
                    button("AI lập lại hồ sơ phim", "users", flat=True, on_click=self.run_profile)))
        col1, col2 = QVBoxLayout(), QVBoxLayout()
        col1.setSpacing(4)
        col2.setSpacing(4)
        col1.addWidget(label("Hồ sơ phim (AI đọc khi dịch)", "muted"))
        col1.addWidget(self.profile_edit, 1)
        col2.addWidget(label("Bảng tên riêng / thuật ngữ của dự án", "muted"))
        col2.addWidget(self.glossary, 1)
        self.sk_more = QWidget()
        more = hbox(col1, col2, spacing=theme.GAP)
        self.sk_more.setLayout(more)
        sk.add(self.sk_more)
        tv.setMinimumHeight(170)   # màn thấp: cả tab cuộn dọc thay vì ép bảng câu còn 1 dòng
        split = QSplitter(Qt.Orientation.Vertical)
        split.setHandleWidth(theme.GAP)
        split.addWidget(tv)
        split.addWidget(sk)
        split.setStretchFactor(0, 1)
        split.setStretchFactor(1, 0)
        split.setCollapsible(0, False)
        split.setSizes([10000, 230])
        self.subs_split = split
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(8)
        # xuất / nạp phụ đề để dịch ở phần mềm khác (trước ở tab Lồng tiếng)
        self.s_srt_trans = button("Xuất SRT đã dịch", "upload", on_click=lambda: self._export_sub("srt", "trans"))
        pg_srt = [
            "Dịch / sửa phụ đề ở phần mềm khác rồi nạp lại – giữ nguyên lời gốc, thời gian, người nói",
            flow(_rowlbl("Nhập"),
                 button("Nhập SRT…", "download", "Nhập file phụ đề SRT / ASS / VTT (thay phụ đề hiện có)",
                        on_click=self._import_subs),
                 button("Nạp SRT đã dịch…", "download", "Nạp bản dịch làm ở phần mềm khác vào các câu hiện có "
                        "(giữ nguyên lời gốc, thời gian, người nói). Câu có dấu ♪ tự đánh dấu là bài hát 🎵",
                        on_click=self._import_translation)),
            flow(_rowlbl("Xuất"),
                 button("Xuất SRT gốc (chưa dịch)", "upload", "Lấy lời gốc đã tách câu để dịch ở phần mềm khác",
                        on_click=lambda: self._export_sub("srt", "source")),
                 self.s_srt_trans,
                 button("Xuất SRT song ngữ", "upload", on_click=lambda: self._export_sub("srt", "both")))]
        tools = Card(padding=12)
        self.s_tools = PillTabs([("Sửa câu", "text"), ("Nối / tách câu", "merge"), ("Lời hát & nghe nhầm", "music"),
                                 ("Nhập / xuất SRT", "upload"), ("Giờ hiện", "clock")])
        self.s_tools_btn = button("Thu gọn", "chev_down", "Ẩn / hiện các nút công cụ (ẩn để bảng câu cao hơn)", flat=True,
                                  on_click=lambda: self._subs_tools(open_=not self.s_tools_body.isVisible()))
        tools.add(hbox(self.s_tools, None, self.s_tools_btn))
        self.s_tools_body = QWidget()
        body = QVBoxLayout(self.s_tools_body)
        body.setContentsMargins(2, 0, 2, 0)
        body.setSpacing(8)
        self.s_tools_pages = []
        for desc, *rows in (pg_edit, pg_join, pg_song, pg_srt, pg_show):
            pg = QWidget()
            pl = QVBoxLayout(pg)
            pl.setContentsMargins(0, 0, 0, 0)
            pl.setSpacing(8)
            pl.addWidget(label(desc, "muted", wrap=True))
            for r in rows:
                pl.addWidget(r)
            body.addWidget(pg)
            self.s_tools_pages.append(pg)
        tools.add(self.s_tools_body)
        self.s_tools_card = tools
        self.s_tools.changed.connect(lambda i: self._subs_tools(page=i, open_=True))
        opened = self.settings.get("subs_tools_open")
        if opened is None:   # chưa chọn lần nào: màn thấp (laptop 768) mở đầu thu gọn để bảng câu đủ chỗ
            from PySide6.QtGui import QGuiApplication

            scr = QGuiApplication.primaryScreen()
            opened = not scr or scr.availableGeometry().height() >= 900
        self._subs_tools(page=int(self.settings.get("subs_tools_tab", 0) or 0), open_=bool(opened), save=False)
        v.setSpacing(theme.GAP)
        v.addWidget(tr_card)
        v.addWidget(tools)
        v.addLayout(hbox(self.search, self.seg_count, label("Tự cuộn", "muted"), self.follow))
        v.addWidget(split, 1)
        return scroll(w)

    def _subs_tools(self, page: int | None = None, open_: bool | None = None, save: bool = True):
        """Khung Công cụ ở tab Phụ đề: mỗi lúc hiện 1 trang (Sửa câu / Nối – tách câu / Lời hát / Nhập – xuất SRT);
        thu gọn = chỉ còn hàng chọn trang (bảng câu cao hơn). Nhớ trang + trạng thái ở Cài đặt."""
        from ..config import save_settings

        if page is not None:
            page = max(0, min(page, len(self.s_tools_pages) - 1))
            for i, pg in enumerate(self.s_tools_pages):
                pg.setVisible(i == page)   # trang ẩn không chiếm chỗ (khung cao đúng theo trang đang mở)
            self.s_tools.set_current(page, emit=False)
        if open_ is not None:
            self.s_tools_body.setVisible(open_)
            self.s_tools_btn.setText("Thu gọn" if open_ else "Mở công cụ")
        if save:
            d = {}
            if page is not None:
                d["subs_tools_tab"] = page
            if open_ is not None:
                d["subs_tools_open"] = bool(open_)
            if d:
                self.settings = save_settings(d)

    # ================================================================== TAB: Xoá phụ đề cũ
    TAB_HARDSUB, TAB_STYLE, TAB_LAYERS, TAB_DUB, TAB_AUDIO, TAB_MODELS, TAB_EXPORT = 2, 4, 5, 6, 7, 8, 9
    HS_MODES = [("none", "Không xoá", "x"), ("inpaint", "Xoá sạch (AI)", "sparkle"), ("stretch", "Kéo dọc", "layers"),
                ("patch", "Vá nền", "image"), ("blur", "Làm mờ", "eye"), ("box", "Che dải màu", "stack"),
                ("delogo", "Delogo", "eraser")]
    HS_HELP = {
        "none": "<b>Không xoá</b> – giữ nguyên chữ Trung cũ trên video. Dùng khi video gốc không có phụ đề cứng.",
        "inpaint": "<b>Xoá sạch</b> – chỗ bị chữ che được lấp bằng <b>nền thật</b> lấy từ các khung trước / sau (lúc "
                   "chữ đổi câu, máy quay lia – đo chuyển động để ghép khớp); phần chưa từng lộ ra mới để AI (LaMa) "
                   "vẽ, cảnh đứng yên giữ nguyên phần đã vẽ nên không nhấp nháy. Đẹp nhất, chậm hơn các cách khác.<br>"
                   "Cách dùng: ① khoanh vùng chữ (hoặc <i>Tự dò vùng phụ đề</i>) ② chọn tuỳ chọn bên dưới "
                   "③ bấm <i>Xem thử 1 khung</i> ④ bấm <b>Chạy xoá (cả video)</b> ở mục 3 (hoặc cứ bấm <b>Xuất "
                   "video</b> – tool tự xoá trước khi ghép). Đã lấy phụ đề bằng OCR thì mặt nạ chữ chính xác nhất.",
        "stretch": "<b>Kéo dọc</b> – lấy dòng điểm ảnh ngay sát mép trên / dưới vùng chữ kéo dài phủ kín cả vùng (2 mép: "
                   "nối dần từ màu mép trên xuống màu mép dưới) → <b>không còn điểm ảnh nào của chữ cũ</b>, theo từng "
                   "khung nên cảnh chuyển động vẫn khớp màu. Thành vệt dọc như kéo giãn – hợp phụ đề trên nền tối / "
                   "nền đơn giản, nhanh như Làm mờ. Khoanh vùng sát chữ (chừa 2–4 px) để mép không dính nét chữ. "
                   "Không cần chạy riêng: áp dụng khi bấm <b>Xuất video</b>; video bên phải hiện gần đúng ngay.",
        "patch": "<b>Vá nền</b> – chép dải hình ngay sát vùng chữ (mặc định phía trên) phủ lên chữ cũ, viền mờ dần "
                 "cho hoà vào cảnh. Nhanh như Làm mờ nhưng nhìn tự nhiên hơn khi nền đơn giản (trời, tường, mặt nước, "
                 "nền tối); cảnh nhiều chi tiết có thể thấy vệt lặp. Không cần chạy riêng: áp dụng khi bấm "
                 "<b>Xuất video</b>; video bên phải hiện gần đúng ngay.",
        "blur": "<b>Làm mờ</b> – nhoè vùng chữ thành vệt mờ. Nhanh, không cần chạy riêng: áp dụng khi bấm "
                "<b>Xuất video</b>. Kéo <i>Độ mờ</i> cho tới khi không đọc được chữ cũ (video bên phải hiện gần đúng).",
        "box": "<b>Che dải màu</b> – phủ một dải màu đặc lên vùng chữ, phụ đề Việt đặt đè lên. Nhanh, gọn, hợp video "
               "nền tối. Chọn <i>Màu</i> + <i>Độ đục</i>; video bên phải hiện đúng như khi xuất.",
        "delogo": "<b>Delogo</b> – bộ lọc của FFmpeg lấp vùng bằng màu viền xung quanh. Rất nhanh, hợp logo / chữ nhỏ; "
                  "chữ to sẽ thành vệt nhoè. Áp dụng khi bấm <b>Xuất video</b>.",
    }

    def _tab_hardsub(self):
        sv = lambda *_: (self._save_edit_timer.start(), self._live_timer.start())  # noqa: E731
        # --- bước 1: khoanh vùng
        area = SectionCard("1. Khoanh vùng chữ cần xoá", "eraser",
                           "Chọn đang khoanh 'Phụ đề cũ' hay 'Logo cố định' rồi kéo chuột ngay trên VIDEO bên phải "
                           "(hoặc trên ảnh dưới đây) – khoanh nhiều vùng được. Kéo 4 chấm trắng trên 4 cạnh để đổi "
                           "cao / thấp / dài / ngắn, kéo giữa vùng để di chuyển, bấm × để xoá vùng.")
        self._hs_lists = {"regions": [], "logos": []}
        self._hs_target = "regions"
        self.hs_target = PillTabs([("Phụ đề cũ", "eraser"), ("Logo cố định", "image")])
        self.hs_target.changed.connect(self._hs_target_changed)
        area.add(self.hs_target)
        self.hs_target_help = label("Khoanh vùng phụ đề tiếng Trung cũ cần xoá (chọn cách xoá ở mục 2).", "muted",
                                    wrap=True)
        area.add(self.hs_target_help)
        self.hs_editor = RegionEditor(multi=True)
        self.hs_editor.changed.connect(lambda regs: (self._hs_lists.__setitem__(self._hs_target, [dict(r) for r in regs]),
                                                     sv()))
        area.add(self.hs_editor)
        auto_btn = GradientButton("Tự dò vùng phụ đề", "wand")
        auto_btn.clicked.connect(self._detect_region)
        area.add(hbox(auto_btn,
                      button("Chụp khung đang phát", "image", "Lấy khung hình ở vị trí đang phát để khoanh vùng",
                             on_click=self._grab_frame),
                      None,
                      button("Xoá hết vùng", "trash", danger=True,
                             on_click=lambda: (self.hs_editor.set_regions([]),
                                               self._hs_lists.__setitem__(self._hs_target, []), sv()))))
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
        self.hs_temporal = f.row("Lấy nền thật từ khung trước / sau", Switch(True),
                                 "Chỗ bị chữ che được lấp bằng đúng hình nền thật ở các khung khác (lúc chữ đổi câu, máy "
                                 "quay lia). Chỉ phần chưa từng lộ ra mới để AI vẽ → nét, không nhoè, không nhấp nháy")
        self.hs_useocr = f.row("Dùng khung chữ OCR", Switch(True),
                               "Nếu đã lấy phụ đề bằng quét chữ (OCR): chỉ xoá đúng khung chữ lúc có chữ (chính xác, "
                               "không xoá nhầm cảnh). Khung OCR đọc sót thì tool tự dò nét chữ")
        self.hs_coverbox = f.row("Xoá cả khung chữ", Switch(False),
                                 "Xoá cả ô chữ nhật quanh chữ (theo OCR) thay vì chỉ nét chữ – sạch hẳn viền / bóng chữ, "
                                 "hợp khi đã bật 'Lấy nền thật từ khung trước / sau'")
        self.hs_patch = f.row("Lấy nền từ", combo([("auto", "Tự chọn (phía trên, thiếu chỗ thì phía dưới)"),
                                                   ("above", "Phía trên vùng chữ"), ("below", "Phía dưới vùng chữ")]),
                              "Chọn phía có nền giống chỗ chữ nhất (cùng màu, ít chi tiết)")
        from ..video import BLUR_KINDS, STRETCH_FROM

        self.hs_stretch = f.row("Kéo từ", combo(list(STRETCH_FROM), "both"),
                                "Mép nào giống nền chỗ chữ nhất thì kéo từ mép đó; 2 mép = nối dần, ít lộ nhất")
        self.hs_ssmooth = f.row("Độ mịn", SliderRow(0, 20, 1, 3, 0, " px"),
                                "Làm mịn ngang cho đỡ vệt sọc dọc (0 = giữ nguyên vệt)")

        self.hs_bkind = f.row("Kiểu mờ", combo(list(BLUR_KINDS), "box"))
        self.hs_blur = f.row("Độ mờ", SliderRow(4, 60, 1, 20, 0))
        self.hs_darken = f.row("Phủ tối", SliderRow(0, 60, 1, 0, 0, " %"),
                               "Làm tối vùng mờ để phụ đề Việt đặt lên trên dễ đọc hơn")
        self.hs_color = f.row("Màu dải che", ColorButton("#000000"))
        self.hs_opacity = f.row("Độ đục dải che", SliderRow(0.2, 1, 0.05, 1))
        self.hs_round = f.row("Bo góc", SliderRow(0, 50, 1, 0, 0, " %"),
                              "0 = góc vuông · 50 = tròn hẳn 2 đầu (kiểu viên thuốc)")
        self.hs_feather = f.row("Viền mờ dần", SliderRow(0, 50, 1, 0, 0, " %"),
                                "Mép vùng hoà dần vào hình xung quanh, không lộ đường cắt")
        self.hs_mode.currentIndexChanged.connect(self._hs_mode_changed)
        for w in (self.hs_backend, self.hs_strength, self.hs_patch, self.hs_bkind, self.hs_stretch):
            w.currentIndexChanged.connect(sv)
        for w in (self.hs_blur, self.hs_opacity, self.hs_darken, self.hs_round, self.hs_feather, self.hs_ssmooth):
            w.changed.connect(sv)
        self.hs_color.changed.connect(sv)
        for w in (self.hs_useocr, self.hs_temporal, self.hs_coverbox):
            w.toggledOn.connect(sv)
        how.add(f)
        # --- bước 3: chạy / kiểm tra
        chk = SectionCard("3. Kiểm tra & chạy", "eye",
                          "‘Xem thử 1 khung’ tạo ảnh kết quả THẬT ở khung đang phát (hiện ở ảnh mục 1), "
                          "không đổi video. Video chỉ được xoá thật khi chạy bước dưới hoặc khi Xuất video.")
        chk.add(flow(button("Xem thử 1 khung", "eye", "Tạo ảnh kết quả chính xác ở khung hình đang phát",
                            on_click=self._preview_hardsub),
                     button("Về ảnh gốc", "refresh", on_click=self._clear_preview)))
        self.hs_run = GradientButton("Chạy xoá (cả video)", "sparkle")
        self.hs_run.setToolTip("Xoá phụ đề cũ (cách 'Xoá sạch') + gỡ logo cố định trên toàn bộ video ngay bây giờ. Xong "
                               "thì trình phát bên phải chiếu bản đã xoá; Xuất video dùng lại bản này, không chạy lại.")
        self.hs_run.clicked.connect(self.run_inpaint)
        self.hs_viewclean = Switch(True)
        self.hs_viewclean.setToolTip("Bật: trình phát chiếu bản đã xoá bằng AI (khi đã chạy xong với đúng vùng hiện tại)")
        self.hs_viewclean.toggledOn.connect(lambda *_: self._sync_clean())
        self.hs_runrow = wrap(hbox(self.hs_run, label("Xem bản đã xoá", "muted"), self.hs_viewclean, None))
        chk.add(self.hs_runrow)
        self.hs_status = label("", "muted", wrap=True)
        chk.add(self.hs_status)
        chk.add(hbox(button("Đi tới Xuất video", "export", "Mở tab Xuất để ghép phụ đề Việt + lồng tiếng và xuất MP4",
                            on_click=lambda: self.tabs.set_current(self.TAB_EXPORT)), None))
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

    LOGO_HELP = ("<b>Gỡ logo cố định</b> – logo / watermark nằm YÊN 1 chỗ suốt video (tên kênh, Douyin…). Khoanh sát "
                 "quanh logo (có thể vài vùng). Tool đo logo trên ~200 khung rải khắp video rồi <b>tính ngược lớp logo "
                 "trong suốt</b> → nền thật phía sau hiện lại (không nhoè như làm mờ). Phần logo đặc hoàn toàn: lấy nền "
                 "từ khung khác khi máy quay lia, không được mới để AI vẽ. Áp dụng cùng mọi cách xoá phụ đề ở mục 2; "
                 "bấm <i>Xem thử 1 khung</i> hoặc <b>Chạy xoá</b> ở mục 3.")

    @property
    def _hs_regions(self) -> list:
        return self._hs_lists["regions"]

    def _hs_target_changed(self, i: int):
        """Đang khoanh vùng phụ đề cũ hay logo cố định (2 danh sách riêng, cùng vẽ trên ảnh + video)."""
        from .player import LOGO_COLOR

        self._hs_lists[self._hs_target] = [dict(r) for r in self.hs_editor.regions]
        self._hs_target = "logos" if i == 1 else "regions"
        other = "regions" if self._hs_target == "logos" else "logos"
        ed = self.hs_editor
        ed.set_regions(self._hs_lists[self._hs_target])
        ed.extra = [dict(r) for r in self._hs_lists[other]]
        ed.color = LOGO_COLOR if self._hs_target == "logos" else theme.ACCENT2
        ed.label, ed.extra_label = ("Logo", "Vùng") if self._hs_target == "logos" else ("Vùng", "Logo")
        ed.update()
        self.player.view.hs.target = self._hs_target
        self.hs_target_help.setText(self.LOGO_HELP if self._hs_target == "logos" else
                                    "Khoanh vùng phụ đề tiếng Trung cũ cần xoá (chọn cách xoá ở mục 2).")
        self.hs_runrow.setVisible(self.hs_mode.currentData() == "inpaint" or bool(self._hs_lists["logos"]))
        self._live_timer.start()

    def _hs_pill_changed(self, i: int):
        set_combo(self.hs_mode, self.HS_MODES[i][0])
        if self.HS_MODES[i][0] != "none" and not self._hs_regions and not self.loading:
            self.win.toast("Khoanh vùng chữ trên ảnh (hoặc bấm 'Tự dò vùng phụ đề') để xoá", "warn")

    def _hs_mode_changed(self, *_):
        mode = self.hs_mode.currentData() or "none"
        i = next((k for k, m in enumerate(self.HS_MODES) if m[0] == mode), 0)
        self.hs_pills.set_current(i, emit=False)
        self.hs_help.setText(self.HS_HELP.get(mode, ""))
        inp = mode == "inpaint"
        vis = {self.hs_backend: inp, self.hs_strength: inp, self.hs_useocr: inp, self.hs_temporal: inp,
               self.hs_coverbox: inp, self.hs_blur: mode == "blur", self.hs_bkind: mode == "blur",
               self.hs_darken: mode == "blur", self.hs_patch: mode == "patch",
               self.hs_color: mode == "box", self.hs_opacity: mode == "box",
               self.hs_stretch: mode == "stretch", self.hs_ssmooth: mode == "stretch",
               self.hs_round: mode in ("blur", "box", "stretch"), self.hs_feather: mode in ("blur", "box", "stretch")}
        for wdg, on in vis.items():
            self.hs_form.setRowVisible(wdg, on)
        self.hs_runrow.setVisible(inp or bool(self._hs_lists.get("logos")))
        self._update_hs_status()
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
            sp.valueChanged.connect(lambda *_: None if self.loading else self._undo_mark(join=True))
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
        self.cuts_rows = QWidget()          # mỗi đoạn cắt 1 hàng: ▶ Xem chỗ nối · × Bỏ đoạn này
        self.cuts_rows_l = QVBoxLayout(self.cuts_rows)
        self.cuts_rows_l.setContentsMargins(0, 0, 0, 0)
        self.cuts_rows_l.setSpacing(4)
        cut.add(self.cuts_rows)
        # --- rút khoảng lặng / cắt bài hát
        from .silence_ui import SilenceCard

        self.sil = SilenceCard(self)
        self.sil.itemsChanged.connect(self._silence_items)
        self.sil.previewToggled.connect(lambda on: self._silence_items(self.sil.items))
        self.sil.playJunction.connect(self._play_junction)
        self.sil.applyRequested.connect(self._apply_silence)
        # --- khung hình
        from .aspect_ui import AspectPicker

        frame = SectionCard("Khung hình", "aspect", "Tỉ lệ dọc cho TikTok / Reels / Shorts, ngang cho YouTube – xem ngay "
                            "trên video bên phải. Tab Xuất dùng chung lựa chọn này.")
        self.e_aspect = AspectPicker()
        self.e_aspect.changed.connect(self._aspect_changed)
        frame.add(self.e_aspect)
        self.e_ratio_note = label("", "warn", wrap=True)
        self.e_ratio_bar = flow(self.e_ratio_note, button("Về tỉ lệ chính", "x", flat=True,
                                                          on_click=lambda: self._ratio_preview("")))
        self.e_ratio_bar.hide()
        frame.add(self.e_ratio_bar)
        f2 = Form()
        self.frame_form = f2
        self.e_amode = f2.row("Phần trống", combo(C.ASPECT_MODES, "blur"),
                              "Khi phim không cùng tỉ lệ với khung: nền mờ (lấy chính hình phim làm mờ), viền đen, nền "
                              "1 màu, hoặc phóng phim cho đầy khung (cắt bớt 2 bên / trên dưới)")
        self.e_acolor = f2.row("Màu nền", ColorButton("#000000"))
        self.e_apos = f2.row("Vị trí phim", combo(C.FILM_POS, "c"),
                             "Đặt phim lệch lên / xuống để chừa chỗ cho tiêu đề, chữ ghi chú")
        self.e_aposx = f2.row("Ngang (% từ trái)", SliderRow(0, 100, 1, 50, 0))
        self.e_aposy = f2.row("Dọc (% từ trên)", SliderRow(0, 100, 1, 50, 0))
        self.e_ascale = f2.row("Cỡ phim trong khung", SliderRow(40, 300, 5, 100, 0, suffix=" %"),
                               "100 % = vừa khít khung; lớn hơn = phóng to (phần tràn bị cắt), nhỏ hơn = thu nhỏ")
        self.e_safe = f2.row("Hiện vùng bị TikTok / Reels che", Switch(),
                             "Tô đỏ vùng nút bấm, tên kênh, chú thích của TikTok / Reels / Shorts che mất (trên 12 %, "
                             "dưới 18 %, phải 14 %) – chỉ để xem, không có trong video xuất")
        self.e_safe_warn = label("", "warn", wrap=True)
        self.e_safe_fix = button("Đưa phụ đề + chữ vào vùng an toàn", "fit", flat=True, on_click=self._safe_fix)
        f2.row("", flow(self.e_safe_warn, self.e_safe_fix))
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
        self.e_amode.currentIndexChanged.connect(lambda *_: self._amode_changed(self.e_amode.currentData()))
        self.e_apos.currentIndexChanged.connect(self._film_pos_preset)
        for w in (self.e_aposx, self.e_aposy):
            w.changed.connect(self._film_pos_slider)
        self.e_ascale.changed.connect(lambda *_: self._frame_live())
        self.e_acolor.changed.connect(lambda *_: self._frame_live())
        self.e_safe.toggledOn.connect(lambda *_: (sv(), self._update_safe()))
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
        # --- logo & chữ: đã chuyển sang tab "Chữ & Logo" (nhiều lớp, mỗi lớp 1 dòng trên timeline)
        brand = SectionCard("Logo & chữ", "text", "Thêm tiêu đề, ghi chú, chữ chạy, logo (nhiều logo, GIF động) ở tab "
                            "Chữ+Logo – mỗi chữ / logo 1 dòng riêng trên timeline, kéo để đổi lúc hiện.")
        brand.add(flow(button("Mở tab Chữ+Logo", "text", on_click=lambda: self.tabs.set_current(self.TAB_LAYERS))))  # tab "Chữ+Logo"
        fx = SectionCard("Hiệu ứng", "sparkle")
        f6 = Form()
        self.e_fin = f6.row("Mờ dần đầu (giây)", SliderRow(0, 5, 0.1, 0, 1))
        self.e_fout = f6.row("Mờ dần cuối (giây)", SliderRow(0, 5, 0.1, 0, 1))
        fx.add(f6)

        for w in (self.e_res, self.e_rot):
            w.currentIndexChanged.connect(sv)
        for w in (self.e_zoom, self.e_speed, self.e_bright, self.e_contrast, self.e_sat, self.e_gamma,
                  self.e_fin, self.e_fout):
            w.changed.connect(sv)
        for w in (self.e_fliph, self.e_flipv, self.e_sharpen, self.e_denoise):
            w.toggledOn.connect(sv)

        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(0, 0, 2, 0)
        v.setSpacing(theme.GAP)
        for c in (cut, self.sil, frame, color, brand, fx):
            v.addWidget(c)
        v.addStretch(1)
        return scroll(w)

    # ================================================================== TAB: Âm thanh
    def _tab_audio(self):
        from .audio_tab import AudioTab

        self.audio_tab = AudioTab(self)
        self.audio_tab.changed.connect(self._audio_changed)
        self.audio_tab.sources_changed.connect(lambda: self._update_player_audio(force=True))
        self.audio_tab.solo.connect(self._audio_solo)
        return scroll(self.audio_tab)

    def _audio_changed(self):
        """Đổi âm lượng / tắt lớp / nguồn ở tab Âm thanh: nghe NGAY trên trình phát + lưu."""
        if self.loading or not self.p:
            return
        self._save_edit_timer.start()
        self._apply_audio_now()
        if self.st_kara.isChecked():
            self._live_timer.start()     # karaoke theo giọng lồng: bật / tắt lồng tiếng thì đổi nhịp chữ

    def _apply_audio_now(self):
        mix = self.player.live.mix
        if mix is None or not self.p:
            return
        try:
            ed = self.collect_edit()
        except Exception:  # noqa: BLE001 – giao diện chưa dựng xong
            return
        st = pipeline.audio_state(self.p, ed, getattr(self, "_audio_srcs", None))
        pipeline.apply_audio_state(mix, st)
        self.audio_tab.show_state(st, getattr(self, "_audio_srcs", {}) or {})

    def _audio_solo(self, key):
        if self.player.live.mix is not None:
            self.player.live.mix.solo = key

    # ================================================================== TAB: Kiểu chữ
    def _tab_style(self):
        sv = lambda *_: (self._save_edit_timer.start(), self._live_timer.start())  # noqa: E731
        card = SectionCard("Kiểu phụ đề ghép vào video", "palette",
                           "Xem trước trực tiếp trên khung video bên phải – đúng cỡ chữ, chỗ đặt như video xuất ra. "
                           "Kéo phụ đề trên video để đặt chỗ khác; khi bật nền hộp, kéo 4 chấm trên 4 cạnh nền "
                           "để đổi chiều cao / độ dài.")
        f = Form()
        self.st_burn = f.row("Ghép phụ đề vào video", Switch(True))
        self.st_which = f.row("Hiển thị", combo(C.WHICH[:3], "trans"))
        from ..overlay import FONTS as _LAYER_FONTS

        self.st_font = f.row("Font chữ", combo([("Be Vietnam Pro", "Be Vietnam Pro – gọn, rõ (mặc định)"),
                                                 ("Bangers", "Bangers – kiểu truyện tranh, chữ hoa nghiêng")] +
                                                [(fam, name) for fam, name in _LAYER_FONTS
                                                 if fam not in ("Be Vietnam Pro", "Bangers")] +
                                                [("Arial", "Arial"), ("Roboto", "Roboto"), ("Tahoma", "Tahoma"),
                                                 ("Segoe UI", "Segoe UI"), ("Times New Roman", "Times New Roman")],
                                                "Be Vietnam Pro"))
        self.st_size = f.row("Cỡ chữ (0 = tự động)", QSpinBox())
        self.st_size.setRange(0, 200)
        self.st_pos = f.row("Vị trí", combo([("auto", "Tự động – đúng chỗ phụ đề cũ"), ("bottom", "Dưới"),
                                              ("middle", "Giữa"), ("top", "Trên"),
                                              ("custom", "Tự chọn – kéo trên video")], "auto"))
        self.st_margin = f.row("Cách mép (px, 0 = tự động)", QSpinBox())
        self.st_margin.setRange(0, 1000)
        self.st_posx = f.row("Ngang (% từ trái)", SliderRow(0, 100, 0.5, 50, 1),
                             "Tâm phụ đề tính từ mép trái khung hình (50 = giữa)")
        self.st_posy = f.row("Dọc (% từ trên)", SliderRow(0, 100, 0.5, 90, 1),
                             "Chân dòng chữ cuối tính từ mép trên khung hình")
        self.st_posauto = f.row("", wrap(hbox(button("Về vị trí tự động", "refresh",
                                                     "Đặt lại phụ đề đúng chỗ phụ đề Trung cũ", flat=True,
                                                     on_click=lambda: set_combo(self.st_pos, "auto")), None)))
        self.st_color = f.row("Màu chữ", ColorButton("#FFFFFF"))
        self.st_kara = f.row("Karaoke – chữ đổi màu theo lời", Switch(False),
                             "Từng từ đổi màu đúng lúc được đọc (dùng được với mọi font). Đang dùng bản lồng tiếng thì "
                             "chạy theo giọng Việt, không thì theo thời gian hiện câu")
        self.st_kcolor = f.row("Màu karaoke", ColorButton(KARA_COLOR))
        self.st_kmode = f.row("Kiểu karaoke", combo(list(KARA_MODES), "sweep"),
                              "Quét mượt: màu chạy dần qua từng chữ · Từng từ: cả từ đổi màu khi tới lượt")
        self.st_ocolor = f.row("Màu viền", ColorButton("#000000"))
        self.st_outline = f.row("Độ dày viền", SliderRow(0, 8, 0.5, 2.5, 1))
        self.st_shadow = f.row("Bóng đổ", SliderRow(0, 5, 0.5, 1, 1))
        self.st_bold = f.row("Chữ đậm", Switch(True))
        self.st_upper = f.row("VIẾT HOA", Switch(False))
        self.st_lwidth = f.row("Độ dài tối đa của dòng", SliderRow(40, 100, 1, 90, 0, " %"),
                               "Dòng chữ dài tối đa bao nhiêu % bề rộng khung hình. Chữ được ĐO bằng font thật: chỉ xuống "
                               "dòng (hoặc thu nhỏ) khi câu thật sự dài hơn chừng này")
        self.st_maxlines = f.row("Số dòng tối đa", combo([("1", "1 dòng"), ("2", "2 dòng"), ("3", "3 dòng")], "2"))
        self.st_linefit = f.row("Khi câu dài hơn 1 dòng", combo(
            [("wrap", "Xuống dòng (chia các dòng dài đều nhau)"),
             ("shrink", "Thu nhỏ chữ câu đó cho vừa 1 dòng, nhỏ quá mới xuống dòng")], "wrap"),
            "Câu vẫn dài quá số dòng tối đa thì chữ của riêng câu đó tự nhỏ lại cho vừa")
        self.st_minscale = f.row("Thu nhỏ tối đa còn", SliderRow(50, 95, 1, 70, 0, " %"),
                                 "Câu dài được thu nhỏ chữ tới mức này (so với Cỡ chữ); dài hơn nữa thì xuống dòng")
        self.st_box = f.row("Nền hộp sau chữ", Switch(False))
        self.st_bfixed = f.row("Kiểu nền", combo([("text", "Ôm theo chữ – từng dòng"),
                                                  ("block", "Ôm theo chữ – 1 khối chung cho các dòng"),
                                                  ("fixed", "Dải cố định (mọi câu cùng cỡ)")], "text"),
                               "Dải cố định hợp để che luôn phụ đề cũ: đặt nền đè lên chỗ chữ Trung")
        self.st_bradius = f.row("Bo góc nền", SliderRow(0, 50, 1, 0, 0, " %"),
                                "0 = góc vuông · 50 = tròn hẳn 2 đầu (kiểu viên thuốc). Tính theo chiều cao 1 dòng nền")
        self.st_bpadx = f.row("Độ dài nền (đệm trái/phải)", SliderRow(0, 4, 0.05, 0.18, 2),
                              "Phần nền thừa ra mỗi bên chữ, tính theo cỡ chữ (1 = bằng 1 dòng chữ)")
        self.st_bpady = f.row("Chiều cao nền (đệm trên/dưới)", SliderRow(0, 3, 0.05, 0.18, 2),
                              "Phần nền thừa ra phía trên và dưới dòng chữ, tính theo cỡ chữ (1 = bằng 1 dòng chữ)")
        self.st_bw = f.row("Độ dài nền (% chiều ngang)", SliderRow(5, 100, 0.5, 80, 1, " %"))
        self.st_bh = f.row("Chiều cao nền (% chiều cao)", SliderRow(2, 100, 0.5, 12, 1, " %"))
        self.st_bcolor = f.row("Màu nền hộp", ColorButton("#000000"))
        self.st_bop = f.row("Độ đục nền hộp", SliderRow(0.1, 1, 0.05, 0.55))
        self.st_form = f
        for w in (self.st_which, self.st_font, self.st_pos, self.st_bfixed, self.st_kmode, self.st_maxlines,
                  self.st_linefit):
            w.currentIndexChanged.connect(sv)
        for w in (self.st_size, self.st_margin):
            w.valueChanged.connect(sv)
        for w in (self.st_color, self.st_ocolor, self.st_bcolor, self.st_kcolor):
            w.changed.connect(sv)
        for w in (self.st_outline, self.st_shadow, self.st_bop, self.st_posx, self.st_posy, self.st_bpadx,
                  self.st_bpady, self.st_bw, self.st_bh, self.st_bradius, self.st_lwidth, self.st_minscale):
            w.changed.connect(sv)
        self.st_pos.currentIndexChanged.connect(self._style_rows)
        self.st_bfixed.currentIndexChanged.connect(self._style_rows)
        self.st_linefit.currentIndexChanged.connect(self._style_rows)
        self.st_box.toggledOn.connect(lambda *_: self._style_rows())
        self.st_kara.toggledOn.connect(lambda *_: self._style_rows())
        for w in (self.st_burn, self.st_bold, self.st_upper, self.st_box, self.st_kara):
            w.toggledOn.connect(sv)
        card.add(f)
        self._style_rows()
        presets = SectionCard("Mẫu nhanh", "sparkle")
        presets.add(flow(button("Karaoke vàng", "sparkle", "Font Bangers, chữ trắng viền đen dày, từng từ chuyển vàng "
                                "theo lời đọc", on_click=lambda: self._style_preset("karaoke")),
                         button("Trắng viền đen", on_click=lambda: self._style_preset("classic")),
                         button("Vàng nổi bật", on_click=lambda: self._style_preset("yellow")),
                         button("Nền hộp đen", on_click=lambda: self._style_preset("box")),
                         button("TikTok to rõ", on_click=lambda: self._style_preset("tiktok"))))
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(0, 0, 2, 0)
        v.setSpacing(theme.GAP)
        v.addWidget(card)
        v.addWidget(presets)
        v.addStretch(1)
        return scroll(w)

    def _style_rows(self, *_):
        """Chỉ hiện ô chỉnh của đúng lựa chọn: vị trí tự chọn / cách mép, kiểu nền ôm chữ / dải cố định."""
        custom = self.st_pos.currentData() == "custom"
        box, fixed = self.st_box.isChecked(), self.st_bfixed.currentData() == "fixed"
        f = self.st_form
        kara = self.st_kara.isChecked()
        for w, on in ((self.st_margin, not custom and self.st_pos.currentData() != "middle"),
                      (self.st_kcolor, kara), (self.st_kmode, kara),
                      (self.st_posx, custom), (self.st_posy, custom), (self.st_posauto, custom),
                      (self.st_bfixed, box), (self.st_bpadx, box and not fixed), (self.st_bpady, box and not fixed),
                      (self.st_bw, box and fixed), (self.st_bh, box and fixed), (self.st_bcolor, box),
                      (self.st_bop, box), (self.st_bradius, box),
                      (self.st_minscale, self.st_linefit.currentData() == "shrink")):
            f.setRowVisible(w, on)

    def _sub_edited(self, d: dict):
        """Kéo phụ đề / 4 chấm của nền hộp trên video -> ghi vào tab Kiểu chữ (lưu lại, xuất đúng như vậy)."""
        if not self.p:
            return
        if self._ratio_view and "pos_x" in d:    # đang xem thử tỉ lệ xuất thêm: vị trí phụ đề riêng cho tỉ lệ đó
            self._ratio_over.setdefault(self._ratio_view, {}).setdefault("sub", {}).update(
                {"position": "custom", "pos_x": float(d["pos_x"]), "pos_y": float(d["pos_y"])})
            d = {k: v for k, v in d.items() if k not in ("pos_x", "pos_y", "position")}
        if "pos_x" in d:
            self.st_posx.set(float(d["pos_x"]) * 100)
            self.st_posy.set(float(d["pos_y"]) * 100)
            set_combo(self.st_pos, "custom")
        for key, w, k in (("box_pad_x", self.st_bpadx, 1), ("box_pad_y", self.st_bpady, 1),
                          ("box_w", self.st_bw, 100), ("box_h", self.st_bh, 100)):
            if key in d:
                w.set(float(d[key]) * k)
        self._style_rows()
        self._save_edit_timer.start()
        self._live_timer.start()

    def _regions_edited(self, regs: list):
        """Vẽ / kéo / đổi cỡ / xoá vùng che ngay trên video -> đồng bộ ảnh khoanh vùng + lưu."""
        if not self.p:
            return
        self.hs_editor.set_regions(regs)
        self._hs_lists[self._hs_target] = [dict(r) for r in regs]
        if self._hs_target == "logos":
            self.hs_runrow.setVisible(True)
        elif regs and self.hs_mode.currentData() == "none":
            set_combo(self.hs_mode, "inpaint")
            self.win.toast("Đã chọn cách xoá 'Xoá sạch (AI)' – đổi cách khác ở mục 2 nếu muốn", "ok")
        self._save_edit_timer.start()
        self._live_timer.start()

    # ================================================================== TAB: Lồng tiếng
    def _tab_dub(self):
        sep = SectionCard("1. Tách giọng nói / nhạc nền", "layers",
                          "Giữ nguyên nhạc nền + hiệu ứng gốc, chỉ thay lời thoại bằng tiếng Việt. Bài hát trong phim được giữ nguyên.")
        from ..separate import SEP_MODELS

        self.d_sepmodel = combo([(m, t) for m, t in SEP_MODELS], self.settings.get("sep_model"))
        self.d_sepmodel.setMinimumContentsLength(10)
        self.d_sepmodel.currentIndexChanged.connect(self._audio_opts_changed)
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
                          "cập nhật ngay, trình phát bên phải nghe được luôn.")
        self.d_engine = combo(C.TTS_ENGINES, self.settings.get("tts_engine", "edge"))
        self.d_engine.setMinimumContentsLength(14)
        self.d_engine.currentIndexChanged.connect(self._dub_engine_changed)
        row = hbox(label("Bộ đọc mặc định", "muted"), self.d_engine)
        row.setStretch(1, 1)
        spk.add(row)
        self.d_voice_all = self._mirror(self.a_voice)
        self.d_voice_all.setMinimumContentsLength(22)
        self.d_voice_all.setToolTip("1 giọng cho MỌI người nói – dùng cho mọi nút lồng tiếng và nhớ làm mặc định cho video "
                                    "sau. Để '(Theo giọng từng người nói)' thì chọn giọng từng người ở bảng dưới")
        va_play = IconButton("play", "Nghe thử giọng này", 32)
        va_play.clicked.connect(self._preview_auto_voice)
        va_row = hbox(label("Giọng cho tất cả", "muted"), self.d_voice_all, va_play)
        va_row.setStretch(1, 1)
        spk.add(va_row)
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
        self.spk_table.setColumnWidth(0, 100)
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
        dt.setShowGrid(False)
        dt.verticalHeader().hide()
        dt.verticalHeader().setDefaultSectionSize(38)
        dh = dt.horizontalHeader()
        dt.setColumnHidden(C_ZH, True)
        for c in (C_SONG, C_NODUB):   # 🎵 / Bỏ lồng: hiện ở cột Độ vừa, đổi bằng nút cho câu đã tích
            dt.setColumnHidden(c, True)
        for c, w in ((C_SEL, 52), (0, 40), (C_START, 88), (C_END, 88), (C_SPK, 58), (C_VOICE, 170), (C_FIT, 84)):
            dh.setSectionResizeMode(c, QHeaderView.ResizeMode.Fixed if c != C_VOICE else QHeaderView.ResizeMode.Interactive)
            dt.setColumnWidth(c, w)
        dh.setSectionResizeMode(C_VI, QHeaderView.ResizeMode.Stretch)
        dh.moveSection(dh.visualIndex(C_SEL), 0)                         # Chọn | # | giờ | Người | Giọng | Tiếng Việt…
        dh.moveSection(dh.visualIndex(C_VOICE), dh.visualIndex(C_SPK) + 1)
        dh.sectionClicked.connect(lambda c: self._toggle_pick_all(self.dub_proxy) if c == C_SEL else None)
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
        self.model.picked.connect(self._update_count)
        spk.add(label("Các câu lồng tiếng", "h3"))
        spk.add(hbox(self.dub_search,
                     button("Tích các câu đang lọc", "check", "Tích tất cả câu đang hiện trong bảng",
                            flat=True, on_click=self._pick_visible),
                     button("Tích câu ⚠ quá dài", "info", "Tích các câu ⚠ quá dài và ⚡ phải đọc nhanh hơn ×1,15 so với "
                            "thời gian nhân vật nói (đúng các câu 'AI rút gọn' sẽ làm)", flat=True, on_click=self._pick_long),
                     button("Bỏ tích", "x", flat=True, on_click=lambda: self.model.set_picked(range(len(self.model.segs)), False))))
        spk.add(flow(button("Tích câu chưa lồng", "check", "Tích các câu đã có lời dịch mà chưa có giọng trong bản lồng "
                            "đang dùng (bị sót, đọc lỗi, câu mới thêm) – rồi bấm 'Tạo lại giọng các câu đã tích'",
                            flat=True, on_click=self._pick_undubbed),
                     button("Tích câu 🎵", "music", "Tích các câu đang đánh dấu bài hát (không lồng tiếng)", flat=True,
                            on_click=lambda: self._pick_flag("song")),
                     button("Tự tìm câu hát", "music", "Tìm các cụm câu đang HÁT (nhạc phim, nhân vật hát) bằng cao độ "
                            "trên giọng đã tách -> đánh dấu 🎵, không lồng tiếng. Cần đã tách giọng (mục 1)", flat=True,
                            on_click=self._detect_songs),
                     button("Đặt / bỏ 🎵 câu đã tích", "music", "Câu đã tích là bài hát -> giữ nguyên giọng hát gốc, "
                            "không lồng; bấm lần nữa để bỏ dấu", flat=True, on_click=lambda: self._toggle_flag("song")),
                     button("Bỏ lồng / lồng lại câu đã tích", "x", "Không đọc các câu đã tích (vd. tiếng hét, tiếng động); "
                            "bấm lần nữa để lồng lại", flat=True, on_click=lambda: self._toggle_flag("nodub")),
                     button("Lọc câu cảm thán", "wand", "Theo ô 'Từ cảm thán cần lọc' (mục 3): xoá ngay câu CHỈ gồm các "
                            "từ đó (\"A!\", \"Ừm.\", \"Hả?\") – cả phụ đề lẫn giọng lồng – và (nếu bật) bỏ vế cảm thán "
                            "đứng riêng trong câu dài rồi đọc lại giọng câu đó. Khôi phục được ở mục 3", flat=True,
                            on_click=self._drop_interj_now),
                     button("Xoá câu đã tích", "trash", "Xoá các câu đã tích: cả phụ đề lẫn giọng lồng của chúng "
                            "(bản lồng tiếng được xếp lại ngay, không đọc lại câu nào)", danger=True,
                            on_click=self._delete_picked)))
        spk.add(dt)
        self.d_line_voice = QComboBox()
        self.d_line_voice.setMinimumContentsLength(16)
        # chọn là áp luôn cho câu đã tích (trước phải bấm thêm "Đổi giọng" – chọn xong bấm thẳng Tạo lại giọng thì câu
        # vẫn đọc giọng cũ)
        self.d_line_voice.activated.connect(lambda _i: self._set_line_voice() if self.model.selected else None)
        self.d_speed = QDoubleSpinBox()
        self.d_speed.setRange(0.5, 2.0)
        self.d_speed.setSingleStep(0.05)
        self.d_speed.setDecimals(2)
        self.d_speed.setValue(1.2)
        self.d_speed.setPrefix("×")
        from ..tts import OVERFLOW_HELP, OVERFLOW_MODES

        self.d_line_mode = combo([("", "Theo cài đặt chung")] + list(OVERFLOW_MODES), "")
        self.d_line_mode.setMinimumContentsLength(12)
        self.d_speed.setToolTip("Tốc độ đọc tối thiểu cho các câu đã tích: câu đang tự đọc nhanh hơn mức này (để vừa thời "
                                "gian nhân vật nói) giữ nguyên, câu chậm hơn được nâng lên. Dưới ×1 = đọc chậm lại nếu còn chỗ")
        spk.add(flow(self.d_picked, hbox(label("Giọng cho câu đã tích", "muted"), self.d_line_voice),
                     button("Đổi giọng", "users", "Đặt giọng riêng cho các câu đã tích (chưa đọc lại)",
                            on_click=self._set_line_voice),
                     hbox(label("Tốc độ đọc", "muted"), self.d_speed),
                     button("Đặt tốc độ", "bolt", "Áp dụng tốc độ cho các câu đã tích – xếp lại bản lồng ngay (không đọc lại)",
                            on_click=lambda: self._apply_speed(sorted(self.model.selected), self.d_speed.value())),
                     button("Về tốc độ gốc", "refresh", "Bỏ tốc độ đặt tay của các câu đã tích – đọc như lúc tạo bản lồng",
                            flat=True, on_click=lambda: self._apply_speed(sorted(self.model.selected), None)),
                     hbox(label("Khi câu dài", "muted"), self.d_line_mode),
                     button("Đặt cách xếp", "layers", "Cách xử lý riêng cho các câu đã tích khi đọc dài hơn chỗ trống "
                            "– xếp lại bản lồng ngay (không đọc lại)",
                            on_click=lambda: self._apply_overflow(sorted(self.model.selected),
                                                                  self.d_line_mode.currentData() or None)),
                     button("AI rút gọn", "sparkle", "AI viết lại ngắn hơn các câu QUÁ DÀI trong các câu đã tích (câu ✓ "
                            "đã vừa giữ nguyên) rồi tự tạo lại giọng đúng các câu đó. Dùng công cụ dịch AI ở Cài đặt "
                            "(DeepSeek, Claude, GPT, Gemini, Ollama – không dùng được Google / Hy-MT2)",
                            on_click=self._shorten_picked),
                     button("Bỏ rút gọn", "refresh", "Các câu đã tích quay về lời dịch trước khi AI rút gọn và tạo lại "
                            "giọng", flat=True, on_click=lambda: self._unshorten_rows(sorted(self.model.selected))),
                     button("Nghe câu", "play", "Phát câu đang chọn trên trình phát bên phải", on_click=self._play_line)))
        regen = GradientButton("Tạo lại giọng các câu đã tích", "refresh")
        regen.setToolTip("Đọc lại riêng các câu đã tích theo lời Việt + giọng hiện tại, cập nhật ngay vào bản lồng "
                         "tiếng đang dùng (không đọc lại cả video)")
        regen.clicked.connect(self._redub_picked)
        spk.add(regen)
        self.d_engine_lbl = label("", "muted")
        spk.add(flow(label("Dịch", "muted"),
                     button("Dịch câu chưa dịch", "globe", "Dịch các câu chưa có lời tiếng Việt – đã có bản lồng thì "
                            "đọc luôn giọng các câu này", on_click=lambda: self._translate_rows(None, True)),
                     button("Dịch câu đã tích", "refresh", "Dịch (lại) các câu đã tích (ra bản dịch mới) rồi tạo lại "
                            "giọng đúng các câu đó",
                            on_click=lambda: self._translate_rows(sorted(self.model.selected), False)),
                     self.d_engine_lbl))

        mix = SectionCard("3. Tạo bản lồng tiếng", "mic",
                          "Mỗi câu phụ đề được đọc thành 1 đoạn giọng riêng và ghép đúng lúc nhân vật nói – xem từng đoạn "
                          "ở dòng \"Lồng tiếng\" trên timeline phía dưới: bấm đúp để nghe, kéo sang trái / phải để dời.")
        f = Form()
        self.d_rate = f.row("Tốc độ đọc", combo([("-10%", "Chậm"), ("+0%", "Bình thường"), ("+10%", "Nhanh"), ("+20%", "Rất nhanh")], "+0%"))
        self.d_skipsong = f.row("Giữ nguyên bài hát (không lồng câu 🎵)", Switch(bool(self.settings.get("dub_skip_songs", True))))
        self.d_skipsong.toggledOn.connect(self._skip_song_changed)
        self.d_interj = f.row("Xoá câu chỉ có từ cảm thán",
                              Switch(bool(self.settings.get("dub_drop_interj", True))))
        self.d_interj.setToolTip("Lồng tiếng Việt: trước khi đọc, câu CHỈ gồm các từ trong ô 'Từ cảm thán cần lọc' (\"A!\", "
                                 "\"Ừm.\", \"Hả?\") bị xoá khỏi phụ đề. Câu \"Ừm, đúng là như vậy.\" giữ nguyên.")
        self.d_interj.toggledOn.connect(lambda on: None if self.loading else self._save_setting("dub_drop_interj", on))
        self.d_interj_in = f.row("Bỏ từ cảm thán đứng riêng trong câu dài",
                                 Switch(bool(self.settings.get("interj_inline", True))))
        self.d_interj_in.setToolTip("\"Ừm, đúng là như vậy.\" -> \"Đúng là như vậy.\", \"Đi thôi, ha ha!\" -> \"Đi thôi!\": chỉ "
                                    "bỏ vế (ngăn bởi dấu câu) gồm toàn từ trong danh sách, cả phụ đề lẫn giọng lồng. "
                                    "'Khôi phục câu cảm thán đã xoá' trả lại lời cũ")
        self.d_interj_in.toggledOn.connect(lambda on: None if self.loading else self._save_setting("interj_inline", on))
        from ..voicefix import INTERJ_DEFAULT

        iw = self.settings.get("interj_words")
        self.d_interj_words = QLineEdit(INTERJ_DEFAULT if iw is None else str(iw))
        self.d_interj_words.setPlaceholderText("Mỗi từ / cụm cách nhau dấu phẩy, VD: a, ừm, hả, ha ha, ui da")
        self.d_interj_words.setToolTip("Chỉ lọc ĐÚNG các từ / cụm này (không phân biệt hoa thường) – từ cảm thán khác giữ "
                                       "nguyên. Cụm nhiều chữ (\"ui da\", \"ha ha\") phải khớp cả cụm. Để trống = không lọc")
        self.d_interj_words.editingFinished.connect(self._interj_words_changed)
        f.row("Từ cảm thán cần lọc", hbox(self.d_interj_words, button(
            "Mặc định", "refresh", "Về danh sách có sẵn", flat=True, on_click=lambda: (
                self.d_interj_words.setText(INTERJ_DEFAULT), self._interj_words_changed()))))
        mix.add(f)
        self.d_voice_note = label("", "muted", wrap=True)
        mix.add(self.d_voice_note)
        mix.add(flow(button("Đổi giọng ở mục 2", "users", "Giọng đọc lấy từ bảng 'Giọng đọc cho từng người nói' (mục 2) "
                            "– câu có giọng riêng (cột Giọng của bảng câu) đọc bằng giọng đó", flat=True,
                            on_click=self._goto_voices),
                     button("Khôi phục câu cảm thán đã xoá", "refresh", "Đưa lại các câu 'A!', 'Ừm.'… đã bị xoá và lời "
                            "cũ của câu dài đã bỏ vế cảm thán (lần lồng sau không lọc các câu này nữa)", flat=True,
                            on_click=self._restore_interj)))
        mix.add(label("Khi câu đọc dài hơn chỗ trống", "h3"))
        g = Form()
        st = self.settings
        self.d_overflow = g.row("Cách xếp", combo(OVERFLOW_MODES, st.get("dub_overflow", "smart")))
        self.d_overflow.setMinimumContentsLength(18)
        self.d_overflow_help = label("", "muted", wrap=True)
        self._overflow_help = OVERFLOW_HELP

        def dspin(lo, hi, step, val, prefix="", suffix="", tip=""):
            w = QDoubleSpinBox()
            w.setRange(lo, hi)
            w.setSingleStep(step)
            w.setDecimals(2)
            w.setValue(float(val))
            w.setPrefix(prefix)
            w.setSuffix(suffix)
            w.setToolTip(tip)
            w.setMaximumWidth(120)
            return w
        self.d_maxspeed = g.row("Tăng tốc tối đa", dspin(
            1.0, 2.0, 0.05, st.get("tts_max_speedup", 1.3), "×", "", "Đọc nhanh hơn tối đa bao nhiêu để vừa chỗ (×1 = "
            "không bao giờ tăng tốc). Cao hơn ×1,3 nghe vội, mất tự nhiên"))
        self.d_lead = g.row("Đọc sớm tối đa (Tự động)", dspin(
            0.0, 3.0, 0.1, st.get("dub_lead_max", 1.0), "", " s", "Câu dài được bắt đầu sớm hơn lúc nhân vật nói tối "
            "đa bao nhiêu giây (chỉ dùng khoảng lặng phía trước, không đè câu trước)"))
        self.d_maxlag = g.row("Câu sau lùi tối đa (Tự động)", dspin(
            0.3, 10.0, 0.1, st.get("dub_max_lag", 2.0), "", " s", "Nhiều câu dài liền nhau: giọng được trễ so với hình "
            "tối đa bao nhiêu giây; quá mức này câu sau vào đúng mức đó và đọc đè lên đuôi câu trước"))
        self.d_follow = g.row("Phụ đề chạy theo giọng lồng", Switch(bool(st.get("dub_subs_follow", True))))
        self.d_follow.setToolTip("Phụ đề đã dịch hiện theo đúng lúc giọng lồng đọc (xem trước, video xuất, SRT đã dịch). "
                                 "Chỉnh hiện trước / giữ thêm / tối thiểu ở tab Phụ đề → Công cụ → Giờ hiện")
        self.d_pauses = g.row("Nghỉ đúng chỗ nhân vật ngắt hơi", Switch(bool(st.get("dub_pauses", True))))
        self.d_pauses.setToolTip("Câu (ngắn hay dài) mà nhân vật ngừng giữa chừng ở chỗ có dấu câu: giọng lồng cũng ngừng ở "
                                 "dấu câu tương ứng, phần sau vào đúng lúc nhân vật nói tiếp. Chỉ dùng chỗ trống trước câu "
                                 "sau (không đẩy câu nào), cần mốc từng chữ (câu nghe từ bản 2.0.96 hoặc bấm 'Căn mép theo "
                                 "giọng' + đo lại mốc từng chữ ở tab Phụ đề). Phụ đề vẫn là 1 dòng")
        self.d_pause_min = g.row("… khi nhân vật ngừng từ", dspin(
            0.15, 2.0, 0.05, st.get("dub_pause_min", 0.3), "", " s", "Chỉ tái hiện chỗ nhân vật ngừng ít nhất bấy nhiêu "
            "giây (ngắn hơn thì để bộ đọc tự ngắt như thường)"))
        self.d_by_clause = g.row("… đọc từng vế", Switch(bool(st.get("dub_by_clause", True))))
        self.d_by_clause.setToolTip("Câu có chỗ nhân vật ngắt hơi: mỗi vế (chia ở dấu phẩy tương ứng) đọc riêng rồi ghép "
                                    "lại – chỗ nghỉ luôn đúng chỗ, vế sau vào đúng lúc nhân vật nói tiếp, vế nào dài hơn "
                                    "vế gốc thì chỉ đọc nhanh riêng vế đó. Có tác dụng khi bấm 'Tạo bản lồng tiếng' / "
                                    "'Tạo lại giọng các câu đã tích' (đọc lại các câu đó). Tắt: đọc cả câu 1 lần, nghỉ ở "
                                    "chỗ bộ đọc tự ngắt như bản 2.0.96")
        self.d_autoshort = g.row("AI tự rút gọn câu quá dài", Switch(bool(st.get("dub_auto_shorten", True))))
        self.d_autoshort.setToolTip("Khi bấm Tạo bản lồng tiếng: câu vẫn quá dài sau khi tăng tốc được AI viết lại ngắn "
                                    "hơn rồi đọc lại (cần công cụ dịch AI; câu bạn tự sửa lời không bị đổi)")
        mix.add(g)
        mix.add(self.d_overflow_help)
        self.d_overflow.currentIndexChanged.connect(self._dub_layout_changed)
        for w in (self.d_maxspeed, self.d_lead, self.d_maxlag, self.d_pause_min):
            w.valueChanged.connect(self._dub_layout_changed)
        self.d_pauses.toggledOn.connect(self._dub_layout_changed)
        self.d_by_clause.toggledOn.connect(self._dub_layout_changed)
        self.d_follow.toggledOn.connect(self._dub_follow_changed)
        self.d_autoshort.toggledOn.connect(self._dub_autoshort_changed)
        self._update_overflow_help()
        gen = GradientButton("Tạo bản lồng tiếng", "mic", big=True)
        gen.clicked.connect(self.run_dub)
        self.d_status = label("", "muted", wrap=True)   # dòng thống kê dài tự xuống dòng, không đẩy thẻ rộng ra
        mix.add(gen)
        mix.add(self.d_status)
        mix.add(flow(button("Nghe bản lồng", "volume", flat=True, on_click=self._open_dub),
                     button("Chỉnh âm lượng giọng lồng / nhạc nền…", "volume",
                            "Mọi chỉnh âm thanh (bật / tắt bản lồng, nhạc nền, giọng gốc, nhạc thêm) ở tab Âm thanh",
                            flat=True, on_click=lambda: self.tabs.set_current(self.TAB_AUDIO))))

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
        # cùng 1 giá trị với "Độ phân giải" ở tab Chỉnh sửa (đổi bên nào cũng đổi bên kia)
        self.e_res.setToolTip("Độ phân giải video xuất ra – chung cho tab Chỉnh sửa và tab Xuất")
        self.x_res = f.row("Độ phân giải", self._mirror(self.e_res))
        card.add(f)
        from .aspect_ui import AspectPicker, ExtraRatios

        card.add(label("Tỉ lệ khung (chung với tab Chỉnh sửa)", "muted"))
        self.x_aspect = AspectPicker()
        self.x_aspect.changed.connect(self._aspect_changed)
        card.add(self.x_aspect)
        f_x = Form()
        self.x_amode = f_x.row("Phần trống", combo(C.ASPECT_MODES, "blur"))
        self.x_amode.currentIndexChanged.connect(lambda *_: self._amode_changed(self.x_amode.currentData()))
        card.add(f_x)
        card.add(label("Xuất thêm tỉ lệ (mỗi tỉ lệ 1 file, cùng lúc với bản chính) – 👁 xem thử trên video bên phải; "
                       "khi đang xem thử, kéo chữ / logo / phụ đề hoặc đổi Phần trống / Vị trí phim = chỉnh riêng cho tỉ "
                       "lệ đó", "muted", wrap=True))
        self.x_extra = ExtraRatios()
        self.x_extra.changed.connect(lambda v: (self._save_edit_timer.start(), self._update_summary()))
        self.x_extra.preview.connect(self._ratio_preview)
        card.add(self.x_extra)
        self.x_ratio_note = label("", "warn", wrap=True)
        self.x_ratio_back = button("Về tỉ lệ chính", "x", flat=True, on_click=lambda: self._ratio_preview(""))
        self.x_ratio_bar = flow(self.x_ratio_note, self.x_ratio_back)
        self.x_ratio_bar.hide()
        card.add(self.x_ratio_bar)
        self.x_summary = label("", "muted", wrap=True)
        card.add(self.x_summary)
        go = GradientButton("Xuất video", "film", big=True)
        go.clicked.connect(self.run_render)
        card.add(go)
        from .publish_ui import PublishCard

        self.pub = PublishCard(self)
        subs = SectionCard("Xuất phụ đề", "subtitles")
        self.x_srt_trans = button("SRT tiếng Việt", on_click=lambda: self._export_sub("srt", "trans"))
        subs.add(hbox(self.x_srt_trans,
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
        for c in (card, self.pub, subs, tools, outs):
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
        if not same:
            self._undo_reset()
            self._check_timeline()

    def _check_timeline(self):
        """Dự án làm trước 2.0.92: đo xem âm thanh của video có lệch giờ so với hình không (phụ đề / giọng lồng / tiếng
        gốc khớp nhau nhưng càng về sau càng lệch khẩu hình). Lệch -> hiện nút "Căn giờ theo hình" (thanh đầu + tab Âm
        thanh) + nhắc 1 lần, KHÔNG hỏi; không lệch -> ghi dấu."""
        from ..ffmpeg import TIMELINE

        p = self.p
        if not p or p.data.get("timeline") == TIMELINE or not p.data.get("source"):
            return
        pid = p.id

        def done(r):
            if not r or not self.p or self.p.id != pid:
                return
            if r.get("mark"):
                self.p.data["timeline"] = TIMELINE
                self.p.save()
            if r.get("need"):
                first = pid not in self._timeline_need
                self._timeline_need[pid] = r["drift"]
                if first:
                    d = r["drift"]
                    self.win.toast(f"Âm thanh của video lệch giờ so với hình (cuối phim lệch {abs(d['end']):.1f} s) – "
                                   "phụ đề / giọng lồng càng về sau càng lệch khẩu hình. Bấm \"Căn giờ theo hình\" trên "
                                   "thanh trên cùng (hoặc tab Âm thanh) để căn lại.", "warn")
            self._update_sync_status()
        run_async(lambda: pipeline.timeline_check(Project(pid)), done, lambda m: None)

    def _update_sync_status(self):
        """Nút "Căn giờ theo hình" trên thanh đầu (chỉ khi đo thấy lệch) + dòng trạng thái ở tab Âm thanh mục 1."""
        from ..ffmpeg import TIMELINE

        p = self.p
        if not p:
            return
        need = self._timeline_need.get(p.id) if p.data.get("timeline") != TIMELINE else None
        self.retime_btn.setVisible(bool(need))
        d = p.data.get("audio_drift") or {}
        if need:
            t = (f"⚠ Âm thanh lệch giờ so với hình: cuối phim lệch {abs(need['end']):.1f} s (lớn nhất {need['max']:.1f} s)"
                 " – bấm \"Căn giờ theo hình\".")
        elif p.data.get("timeline") == TIMELINE:
            t = "✓ Âm thanh theo đúng giờ của hình" + (f" (đã căn lại, video lệch {abs(d['end']):.1f} s)"
                                                      if d.get("end") is not None else "") + "."
        elif p.data.get("source"):
            t = ("Dự án làm trước bản 2.0.92: nếu phụ đề / giọng lồng càng về sau càng lệch khẩu hình, bấm \"Căn giờ "
                 "theo hình\".")
        else:
            t = ""
        self.audio_tab.sync_status.setText(t)

    def run_retime(self):
        """Nút "Căn giờ theo hình": đo giờ âm thanh của video, lệch thì căn bản tách giọng / mốc câu / bản lồng."""
        if not self.p or not self.p.data.get("source"):
            return self.win.toast("Dự án chưa có video", "warn")
        if pipeline.source_missing(self.p):
            return self.win.toast("Không tìm thấy video gốc – bấm \"Tìm lại video gốc…\" trước.", "err")
        if any(j["kind"] == "retime" and j["status"] in ("queued", "running") for j in manager.list(self.p.id)):
            return self.win.toast("Đang căn giờ theo hình – chờ chút", "info")

        def fn(j, p):
            r = pipeline.retime(p, load_settings(), j.update, j.cancelled)
            d = r["drift"]
            if r["state"] == "ok":
                msg = f"Âm thanh đã khớp hình (lệch lớn nhất {d['max']:.2f} s) – không cần căn"
            elif r["state"] == "already":
                msg = ("Dự án đã theo đúng giờ của hình" + (f" (video lệch {abs(d['end']):.1f} s, đã được căn)"
                                                            if d["max"] > 0.15 else "") +
                       ". Nếu vẫn thấy lệch khẩu hình: tắt \"Xem bản đã xoá\" ở tab Xoá phụ đề (bản xoá làm trước "
                       "2.0.92 của video có tốc độ khung thay đổi có thể lệch – bấm Chạy xoá lại)")
            else:
                msg = f"Đã căn lại giờ cho khớp hình (cuối phim lệch {abs(d['end']):.1f} s)"
                if r["moved"]:
                    msg += f": dời {r['moved']} câu" + (", xếp lại bản lồng" if r["dub"] else "")
            return {"message": msg, "retime": r["state"]}
        self.submit("retime", "Căn giờ theo hình", fn)

    def reload(self):
        if self.p:
            self.p.reload()
            self.populate(reload_media=False)
            self._undo_mark(join=False)    # việc vừa chạy (dịch, nối câu, tạo giọng…) = 1 bước hoàn tác được

    def populate(self, reload_media: bool = True):
        p = self.p
        from .. import overlay

        raw = p.data.get("edit") or {}
        if overlay.migrate(raw):     # dự án cũ: chữ chạy / logo -> lớp phủ (tab Chữ & Logo)
            p.data["edit"] = raw
            p.save()
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
            self._disk_sig, self._loaded_at = pipeline.seg_sigs(p.segments), time.time()
            self._fit_rows()
            self._update_count()
            self._apply_target(pipeline.target_lang(p, self.settings))
            self._fill_edit()
            if self.profile_edit.toPlainText() != (p.data.get("film_profile") or ""):
                self.profile_edit.setPlainText(p.data.get("film_profile") or "")
            self._fill_speakers()
            self._fill_dub_layout()
            self.sync_engine()
            self._fill_outputs()
            if reload_media:
                self.pub.load()
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
        if not self.p or not self.p.data.get("source") or pipeline.source_missing(self.p):
            return
        pid = self.p.id

        def done(a):
            if not a or not self.p or self.p.id != pid:
                return
            if a.get("thumbs"):
                self.tracks.set_thumbs(QPixmap(a["thumbs"]), a["step"], a["count"])
            if a.get("wave"):
                import numpy as np

                self.tracks.set_wave(np.load(a["wave"]).astype(np.float32), a["hop"])
        run_async(lambda: pipeline.timeline_assets(Project(pid)), done, lambda m: None)

    def _media_source(self, hs: dict | None = None) -> str:
        """Video cho trình phát: bản đã xoá phụ đề bằng AI (nếu đã chạy với đúng vùng hiện tại) / bản xem trước / gốc."""
        p = self.p
        if not p or not p.data.get("source"):
            return ""
        if self.hs_viewclean.isChecked():
            try:
                f = pipeline.inpainted_video(p, hs)
            except Exception:  # noqa: BLE001
                f = None
            if f:
                return str(f)
        return str(p.work / "preview.mp4" if (p.work / "preview.mp4").exists() else p.source)

    def _sync_clean(self):
        """Đổi vùng / tuỳ chọn xoá AI -> bản đã xoá không còn đúng -> chiếu lại video gốc (và ngược lại)."""
        if not self.p or self.loading:
            return
        want = self._media_source(self._hs_widgets())
        self.player.view.hs.clean = "inpainted_" in Path(want).name
        self._update_hs_status()
        if want and want != self.player.source:
            i = self.p.data.get("info", {})
            self.player.load(want, i.get("duration", 0), i.get("fps", 25), i.get("height", 1080),
                             start=self.player.position())
            self._dub_key = None
            self._update_player_audio()

    def _update_hs_status(self):
        if not self.p or not hasattr(self, "hs_status"):
            return
        mode = self.hs_mode.currentData() or "none"
        logos = len(self._hs_lists["logos"])
        need_run = mode == "inpaint" and self._hs_regions or logos
        if not need_run:
            if mode == "none":
                txt = "Không xoá phụ đề cũ."
            elif not self._hs_regions:
                txt = "⚠ Chưa có vùng nào – kéo chuột trên video bên phải hoặc bấm 'Tự dò vùng phụ đề'."
            else:
                txt = "Cách này không cần chạy riêng: tự áp dụng khi bấm Xuất video."
        elif self.player.view.hs.clean:
            txt = ("✓ Đã xoá xong cả video – trình phát đang chiếu bản đã xoá. Xuất video dùng luôn bản này "
                   "(không chạy lại). Đổi vùng / tuỳ chọn thì phải chạy lại.")
        else:
            try:
                done = pipeline.inpainted_video(self.p, self._hs_widgets()) is not None
            except Exception:  # noqa: BLE001
                done = False
            txt = ("✓ Đã xoá xong cả video (đang tắt 'Xem bản đã xoá')." if done else
                   ("Chưa chạy. " + (f"Có {logos} logo cố định cần gỡ. " if logos else "")) +
                   "Bấm 'Chạy xoá (cả video)' để xoá ngay và xem kết quả trên video bên phải – "
                   "hoặc cứ bấm Xuất video, tool tự xoá trước khi ghép (lâu hơn).")
        self.hs_status.setText(txt)

    def run_inpaint(self):
        if not self.p or not self.p.data.get("source"):
            return self.win.toast("Dự án chưa có video", "err")
        logos = bool(self._hs_lists["logos"])
        if self.hs_mode.currentData() != "inpaint" and not logos:
            return self.win.toast("Chọn cách xoá 'Xoá sạch (AI)' ở mục 2 (hoặc khoanh logo cố định) trước", "warn")
        if not self._hs_regions and not logos:
            return self.win.toast("Hãy khoanh vùng chữ / logo cần xoá trước (kéo chuột trên video)", "warn")
        if pipeline.inpainted_video(self.p, self._hs_widgets()):
            self.hs_viewclean.setChecked(True)
            self._sync_clean()
            return self.win.toast("Đã xoá xong với đúng vùng này rồi – trình phát đang chiếu bản đã xoá", "ok")

        msgs: list[str] = []

        def fn(j, p):
            def pr(x, m=""):
                msgs.append(m)
                j.update(x, m)
            out = pipeline.run_inpaint(p, None, pr, j.cancelled)
            tail = next((m for m in reversed(msgs) if m.startswith("Đã xoá")), "")
            return {"message": "Đã xoá cả video – trình phát chiếu bản đã xoá, xuất video dùng luôn bản này" +
                    (f" ({tail.split('–', 1)[1].strip()})" if "–" in tail else ""), "path": str(out)}
        self.submit("inpaint", "Xoá phụ đề / gỡ logo", fn)

    def _load_media(self):
        p = self.p
        if pipeline.source_missing(p):
            old = p.source
            found = pipeline.find_moved_source(p)
            try:
                if found:
                    pipeline.relink_source(p, found)
            except Exception:  # noqa: BLE001
                found = None
            if found:
                self.win.toast(f"Video gốc đã bị chuyển chỗ – đã tìm thấy và nối lại: {found}", "ok")
            else:
                self.win.toast(f"Không tìm thấy video gốc: {old}\nFile đã bị chuyển chỗ, đổi tên hoặc xoá – bấm 'Tìm lại "
                               "video gốc…' (nút đỏ trên cùng) để chọn lại file", "err")
        self.relink_btn.setVisible(pipeline.source_missing(p))
        self._update_sync_status()
        i = p.data.get("info", {})
        src = self._media_source()
        self.player.view.hs.clean = "inpainted_" in Path(src).name
        self.player.load(src, i.get("duration", 0), i.get("fps", 25), i.get("height", 1080))
        self.hs_editor.set_image(QPixmap())
        self.hs_editor.overlay = None
        if p.data.get("source"):
            QTimer.singleShot(400, self._grab_frame)
        self._dub_key = None
        self._update_player_audio()
        self._maybe_proxy()

    def _maybe_proxy(self):
        """Video nặng (4K, 60 khung/s, HEVC…): tự tạo bản xem trước nhẹ ≤ 720p cho trình phát (chạy nền, xuất video vẫn
        dùng video gốc). Xong -> trình phát tự đổi sang bản nhẹ."""
        p = self.p
        if not p or (p.work / "preview.mp4").exists() or pipeline.source_missing(p) or \
                not pipeline.needs_proxy(p, self.settings):
            return
        if any(j["kind"] == "preview" and j["status"] in ("queued", "running") for j in manager.list(p.id)):
            return
        i = p.data.get("info", {})
        self.win.toast(f"Video nặng ({i.get('width')}×{i.get('height')}, {float(i.get('fps') or 0):.0f} khung/s, "
                       f"{i.get('vcodec', '')}) – đang tạo bản xem trước nhẹ 720p để phát mượt (xuất video vẫn dùng "
                       "video gốc)", "info")
        self.submit("preview", "Tạo bản xem trước nhẹ", lambda j, p: (pipeline.make_preview(p, j.update, j.cancelled),
                                                                     {"message": "Đã tạo bản xem trước nhẹ – trình "
                                                                                 "phát dùng bản này"})[1], quiet=True)

    def _update_player_audio(self, force: bool = False):
        """Trình phát nghe đúng như video xuất: bộ trộn trực tiếp các lớp âm thanh (tab Âm thanh). File các lớp đổi
        (vừa tách giọng, tạo / sửa bản lồng, đổi nhạc thêm, đổi cao độ) -> nạp lại các lớp (vài giây, chạy nền);
        chỉ đổi âm lượng thì áp ngay, không nạp lại."""
        if not self.p:
            return
        try:
            ed = self.p.edit if self.loading else self.collect_edit()   # đang nạp dự án: ô chưa điền xong
        except Exception:  # noqa: BLE001
            ed = self.p.edit
        srcs = pipeline.audio_sources(self.p, ed)
        key = tuple(sorted((k, f, pre, Path(f).stat().st_mtime if Path(f).exists() else 0)
                           for k, (f, pre) in srcs.items()))
        dub_key = (srcs.get("dub"), key)
        if dub_key != getattr(self, "_dub_key", None):
            self._dub_key = dub_key
            self._load_dub_wave()   # sóng âm từng đoạn giọng lồng trên timeline
        if not force and key == getattr(self, "_audio_key", None) and self.player.live.mix is not None:
            self._audio_srcs = srcs
            return self._apply_audio_now()
        self._audio_key = key
        self._audio_srcs = srcs
        pid = self.p.id
        self.audio_tab.show_state(pipeline.audio_state(self.p, ed, srcs), srcs)

        def done(mix):
            if not self.p or self.p.id != pid or self._audio_key != key:
                return
            mix.solo = self.player.live.mix.solo if self.player.live.mix is not None else None
            self.player.set_live_mix(mix)
            self._apply_audio_now()
            row = getattr(self, "_play_after_mix", None)
            if row is not None:
                self._play_after_mix = None
                self._play_line(row)      # vừa tạo lại giọng -> phát luôn câu đó bằng bản mới
        run_async(lambda: pipeline.live_mix(Project(pid), ed), done,
                  lambda m: self.win.toast("Không chuẩn bị được âm thanh để nghe thử: " + m, "err"))

    def _fill_edit(self):
        e = self.p.edit
        st = _deep_merge(self.settings.get("sub_style") or {}, e["subtitles"].get("style") or {})
        hs = e["hardsub"]
        set_combo(self.hs_mode, hs.get("mode", "none"))
        set_combo(self.hs_backend, hs.get("backend") or self.settings.get("inpaint_backend", "lama"))
        set_combo(self.hs_strength, hs.get("strength", "normal"))
        self.hs_blur.set(hs.get("blur_strength", 20))
        set_combo(self.hs_patch, hs.get("patch_from", "auto"))
        set_combo(self.hs_stretch, hs.get("stretch_from") or "both")
        self.hs_ssmooth.set(float(hs.get("stretch_smooth", 3) or 0))
        self.hs_color.set(hs.get("box_color", "#000000"))
        self.hs_opacity.set(hs.get("box_opacity", 1.0))
        self.hs_useocr.setChecked(hs.get("use_ocr_boxes", True) is not False)
        self.hs_temporal.setChecked(hs.get("temporal", True) is not False)
        self.hs_coverbox.setChecked(bool(hs.get("cover_box")))
        set_combo(self.hs_bkind, hs.get("blur_kind") or "box")
        self.hs_darken.set(float(hs.get("darken") or 0) * 100)
        self.hs_round.set(float(hs.get("round") or 0) * 100)
        self.hs_feather.set(float(hs.get("feather") or 0) * 100)
        self._hs_lists = {"regions": [dict(r) for r in hs.get("regions") or []],
                          "logos": [dict(r) for r in hs.get("logos") or []]}
        self.hs_editor.set_regions(self._hs_lists[self._hs_target])
        self.hs_editor.extra = [dict(r) for r in self._hs_lists["logos" if self._hs_target == "regions" else "regions"]]
        self.e_start.setValue(float(e["trim"].get("start") or 0))
        self.e_end.setValue(float(e["trim"].get("end") or 0))
        self._ratio_over = __import__("json").loads(__import__("json").dumps(e.get("ratio_over") or {}))
        if getattr(self, "_ratio_view", "") and self._ratio_view not in (e.get("extra_ratios") or []):
            self._ratio_view = ""
        self._set_frame_widgets(e)
        self.layers_tab.set_layers(e.get("layers") or [])
        self.x_extra.set_values(e.get("extra_ratios") or [], e.get("aspect", "original"))
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
        from ..audiomix import audio_settings

        self.audio_tab.fill(audio_settings(e), bool(e["dub"].get("enabled")), e["bgm"].get("path", ""))
        self.e_fin.set(e["fade"].get("in", 0))
        self.e_fout.set(e["fade"].get("out", 0))
        self.crop_info.setText(f"Crop {e['crop']['w'] * 100:.0f}% × {e['crop']['h'] * 100:.0f}%" if e.get("crop") else "Không crop")
        self._update_cuts_label()
        # kiểu chữ
        self.st_burn.setChecked(bool(e["subtitles"].get("burn", True)))
        set_combo(self.st_which, e["subtitles"].get("which", "trans"))
        self._set_style_widgets(st)
        # lồng tiếng
        d = e["dub"]
        set_combo(self.d_engine, self.p.data.get("tts_engine") or self.settings.get("tts_engine", "edge"))
        set_combo(self.a_tts, self.d_engine.currentData())
        stems = pipeline.stems(self.p)
        self.d_sepstatus.setText("✓ Đã tách giọng / nhạc nền" if stems else "Chưa tách")
        has_dub = d.get("wav") and Path(d["wav"]).exists()
        self.d_status.setText(("✓ " + pipeline.dub_summary(self.p)) if has_dub else "Chưa tạo bản lồng tiếng")
        self._fill_dub_versions()
        set_combo(self.x_crf, str(e.get("crf") or self.settings.get("video_crf", 20)))

    def collect_edit(self) -> dict:
        e = self.p.edit
        aud = self._audio_now()
        return {
            "trim": {"start": self.e_start.value(), "end": self.e_end.value()},
            "cuts": e.get("cuts", []), "crop": e.get("crop"),
            "vsplits": e.get("vsplits", []), "inserts": e.get("inserts", []), "mute_spans": e.get("mute_spans", []),
            "hardsub": self._hs_widgets(),
            **self._frame_edit(e),
            "resolution": self.e_res.currentData(), "rotate": int(self.e_rot.currentData()),
            "flip_h": self.e_fliph.isChecked(), "flip_v": self.e_flipv.isChecked(),
            "zoom": self.e_zoom.value(), "speed": self.e_speed.value(),
            "color": {"brightness": self.e_bright.value(), "contrast": self.e_contrast.value(),
                      "saturation": self.e_sat.value(), "gamma": self.e_gamma.value(),
                      "sharpen": self.e_sharpen.isChecked(), "denoise": self.e_denoise.isChecked()},
            "audio": aud,
            "bgm": {"path": e["bgm"].get("path", ""), "volume": aud["bgm"], "duck": aud["duck"]},
            "watermark": e.get("watermark") or {}, "text": e.get("text") or {},
            "layers": [dict(L) for L in self._layers],
            "fade": {"in": self.e_fin.value(), "out": self.e_fout.value()},
            "subtitles": {"burn": self.st_burn.isChecked(), "which": self.st_which.currentData(),
                          "style": self.current_style()},
            "dub": {**e["dub"], "enabled": self.audio_tab.dub_on.isChecked()},
            "crf": int(self.x_crf.currentData()),
        }

    def _audio_now(self) -> dict:
        return self.audio_tab.collect()

    def _hs_widgets(self) -> dict:
        self._hs_lists[self._hs_target] = [dict(r) for r in self.hs_editor.regions]
        return {"mode": self.hs_mode.currentData(), "regions": self._hs_lists["regions"],
                "logos": self._hs_lists["logos"], "temporal": self.hs_temporal.isChecked(),
                "cover_box": self.hs_coverbox.isChecked(), "blur_kind": self.hs_bkind.currentData(),
                "darken": round(self.hs_darken.value() / 100, 3), "round": round(self.hs_round.value() / 100, 3),
                "feather": round(self.hs_feather.value() / 100, 3),
                "backend": self.hs_backend.currentData(), "strength": self.hs_strength.currentData(),
                "blur_strength": int(self.hs_blur.value()), "box_color": self.hs_color.color,
                "box_opacity": self.hs_opacity.value(), "use_ocr_boxes": self.hs_useocr.isChecked(),
                "patch_from": self.hs_patch.currentData(), "stretch_from": self.hs_stretch.currentData(),
                "stretch_smooth": int(self.hs_ssmooth.value())}

    def _set_style_widgets(self, st: dict):
        set_combo(self.st_font, st.get("font", "Be Vietnam Pro"))
        self.st_size.setValue(int(st.get("size") or 0))
        set_combo(self.st_pos, st.get("position", "auto"))
        self.st_margin.setValue(int(st.get("margin_v") or 0))
        self.st_color.set(st.get("color", "#FFFFFF"))
        self.st_kara.setChecked(bool(st.get("karaoke", False)))
        self.st_kcolor.set(st.get("karaoke_color") or KARA_COLOR)
        set_combo(self.st_kmode, st.get("karaoke_mode", "sweep"))
        self.st_ocolor.set(st.get("outline_color", "#000000"))
        self.st_outline.set(st.get("outline", 2.5))
        self.st_shadow.set(st.get("shadow", 1))
        self.st_bold.setChecked(bool(st.get("bold", True)))
        self.st_upper.setChecked(bool(st.get("uppercase", False)))
        self.st_box.setChecked(bool(st.get("box", False)))
        self.st_bcolor.set(st.get("box_color", "#000000"))
        self.st_bop.set(st.get("box_opacity", 0.55))
        self.st_posx.set(float(st.get("pos_x", 0.5)) * 100)
        self.st_posy.set(float(st.get("pos_y", 0.9)) * 100)
        set_combo(self.st_bfixed, "fixed" if st.get("box_fixed") else "block" if st.get("box_block") else "text")
        self.st_bradius.set(float(st.get("box_radius") or 0) * 100)
        self.st_lwidth.set(float(st.get("line_width") or 0.9) * 100)
        set_combo(self.st_maxlines, str(int(st.get("max_lines") or 2)))
        set_combo(self.st_linefit, st.get("line_fit") or "wrap")
        self.st_minscale.set(float(st.get("min_scale") or 0.7) * 100)
        self.st_bpadx.set(float(st.get("box_pad_x", 0.18)))
        self.st_bpady.set(float(st.get("box_pad_y", 0.18)))
        self.st_bw.set(float(st.get("box_w", 0.8)) * 100)
        self.st_bh.set(float(st.get("box_h", 0.12)) * 100)
        self._style_rows()

    def current_style(self) -> dict:
        return {"font": self.st_font.currentData(), "size": self.st_size.value(), "position": self.st_pos.currentData(),
                "karaoke": self.st_kara.isChecked(), "karaoke_color": self.st_kcolor.color,
                "karaoke_mode": self.st_kmode.currentData(),
                "margin_v": self.st_margin.value(), "color": self.st_color.color, "outline_color": self.st_ocolor.color,
                "outline": self.st_outline.value(), "shadow": self.st_shadow.value(), "bold": self.st_bold.isChecked(),
                "uppercase": self.st_upper.isChecked(), "box": self.st_box.isChecked(),
                "box_color": self.st_bcolor.color, "box_opacity": self.st_bop.value(),
                "pos_x": round(self.st_posx.value() / 100, 4), "pos_y": round(self.st_posy.value() / 100, 4),
                "box_fixed": self.st_bfixed.currentData() == "fixed", "box_block": self.st_bfixed.currentData() == "block",
                "box_radius": round(self.st_bradius.value() / 100, 3),
                "line_width": round(self.st_lwidth.value() / 100, 3), "max_lines": int(self.st_maxlines.currentData()),
                "line_fit": self.st_linefit.currentData(), "min_scale": round(self.st_minscale.value() / 100, 3),
                "box_pad_x": round(self.st_bpadx.value(), 3),
                "box_pad_y": round(self.st_bpady.value(), 3), "box_w": round(self.st_bw.value() / 100, 4),
                "box_h": round(self.st_bh.value() / 100, 4)}

    # ================================================================== lưu
    def _save_edit_now(self):
        if self.loading or not self.p:
            return
        self.p.reload()
        ed = self.collect_edit()
        cur = self.p.edit
        cur.update(ed)
        self.p.data["edit"] = cur
        self.p.data[pipeline.glossary_key(pipeline.target_lang(self.p, self.settings))] = self.glossary.toPlainText()
        self.p.data["film_profile"] = self.profile_edit.toPlainText()
        self.p.data["tts_engine"] = self.d_engine.currentData()
        self.p.save()
        self._update_summary()
        self._update_timeline()
        self._update_player_audio()

    # ------------------------------------------------------------------ hoàn tác / làm lại
    UNDO_MAX = 60
    UNDO_JOIN = 0.8     # các thay đổi liền nhau trong 0,8 s (gõ chữ, kéo nhiều câu) = 1 bước hoàn tác

    def _snap(self) -> str:
        import json

        # không dùng to_dict (asdict chép sâu từng câu – 2.600 câu ~55 ms); json tự chép phần extra.
        # Kèm đoạn cắt + lớp phủ -> Ctrl+Z hoàn tác được cả "Áp dụng cắt" và sửa chữ / logo
        ed = (self.p.data.get("edit") or {}) if self.p else {}
        cuts = ed.get("cuts") or []
        # + cắt đầu / cuối (ô đang hiện), điểm tách, video chèn, đoạn tắt tiếng gốc (2.0.98 – cắt / sửa ở tab Chỉnh sửa
        # và dòng Video / Tiếng gốc của timeline cũng hoàn tác được bằng nút ↶)
        ex = {"trim": {"start": round(self.e_start.value(), 3), "end": round(self.e_end.value(), 3)},
              "vsplits": ed.get("vsplits") or [], "inserts": ed.get("inserts") or [],
              "mute_spans": ed.get("mute_spans") or []}
        return json.dumps({"s": [{"start": s.start, "end": s.end, "text": s.text, "trans": s.trans,
                                  "speaker": s.speaker, "extra": s.extra} for s in self.model.segs],
                           "c": cuts, "l": self._layers, "e": ex})

    def _undo_reset(self):
        """Mở dự án: bắt đầu lịch sử hoàn tác mới."""
        self._undo.clear()
        self._redo.clear()
        self._undo_cur = self._snap() if self.p else None
        self._undo_t = 0.0
        self._update_undo_btns()

    def _undo_mark(self, join: bool = False):
        """Gọi SAU mỗi thay đổi danh sách câu: cất trạng thái trước đó vào lịch sử hoàn tác."""
        if self._undo_busy or not self.p or self._live_draft:
            return
        snap = self._snap()
        if snap == self._undo_cur:
            return
        now = time.time()
        if self._undo_cur is not None and not (join and now - self._undo_t < self.UNDO_JOIN and self._undo):
            self._undo.append(self._undo_cur)
            del self._undo[:-self.UNDO_MAX]
        self._undo_t = now if join else 0.0
        self._undo_cur = snap
        self._redo.clear()
        self._update_undo_btns()

    def _update_undo_btns(self):
        self.undo_btn.setEnabled(bool(self._undo))
        self.redo_btn.setEnabled(bool(self._redo))
        self.undo_btn.setToolTip(f"{UNDO_TIP}\nCòn {len(self._undo)} bước" if self._undo else
                                 f"{UNDO_TIP}\nChưa có thao tác nào để hoàn tác (lịch sử tính từ lúc mở dự án)")
        self.redo_btn.setToolTip(f"Làm lại (Ctrl+Y) – còn {len(self._redo)} bước" if self._redo else
                                 "Làm lại (Ctrl+Y) – chưa có bước nào vừa hoàn tác")

    def undo(self):
        self._undo_step(self._undo, self._redo, "Đã hoàn tác")

    def redo(self):
        self._undo_step(self._redo, self._undo, "Đã làm lại")

    def _undo_step(self, src: list, dst: list, what: str):
        import json

        if not self.p:
            return
        if any(j["status"] in ("queued", "running") and j["kind"] != "relayout" for j in manager.list(self.p.id)):
            return self.win.toast("Đợi việc đang chạy của dự án này xong rồi hãy hoàn tác", "warn")
        self.inspector._flush()
        self._undo_mark(join=False)      # thay đổi chưa kịp ghi lịch sử (vừa gõ xong)
        if not src:
            return self.win.toast("Không còn bước nào để " + ("hoàn tác" if src is self._undo else "làm lại"), "info")
        cur = self._undo_cur or self._snap()
        snap = src.pop()
        dst.append(cur)
        old = {(round(s.start, 3), round(s.end, 3), s.text, s.trans, json.dumps(s.extra, sort_keys=True))
               for s in self.model.segs}
        snap_d = json.loads(snap)
        if isinstance(snap_d, list):      # ảnh chụp kiểu cũ (chỉ danh sách câu)
            snap_d = {"s": snap_d, "c": None, "l": None}
        segs = [Segment.from_dict(d) for d in snap_d["s"]]
        e = self.p.edit
        ex = snap_d.get("e") or {}
        cur_ex = {"trim": {"start": round(self.e_start.value(), 3), "end": round(self.e_end.value(), 3)},
                  "vsplits": e.get("vsplits") or [], "inserts": e.get("inserts") or [],
                  "mute_spans": e.get("mute_spans") or []}
        edit_changed = (snap_d.get("c") is not None and snap_d["c"] != (e.get("cuts") or [])) or \
            (snap_d.get("l") is not None and snap_d["l"] != self._layers) or \
            any(k in ex and ex[k] != cur_ex[k] for k in cur_ex)
        self._undo_busy = True
        try:
            self.model.set_segments(segs)
            self._undo_cur = snap
            self._fit_rows()
            self._segs_edited()
            self._save_segs_now()
        finally:
            self._undo_busy = False
        if edit_changed:     # đoạn cắt / lớp phủ của bước đó
            self.p.reload()
            e = self.p.edit
            if snap_d.get("c") is not None:
                e["cuts"] = snap_d["c"]
            if snap_d.get("l") is not None:
                e["layers"] = snap_d["l"]
            for k in ("vsplits", "inserts", "mute_spans"):
                if k in ex:
                    e[k] = ex[k]
            if "trim" in ex:
                e["trim"] = dict(e.get("trim") or {}, **ex["trim"])
            self.p.data["edit"] = e
            self.p.save()
            self._undo_busy = True
            was, self.loading = self.loading, True
            try:
                self.layers_tab.set_layers(e.get("layers") or [])
                if "trim" in ex:
                    self.e_start.setValue(float(ex["trim"].get("start") or 0))
                    self.e_end.setValue(float(ex["trim"].get("end") or 0))
            finally:
                self.loading = was
                self._undo_busy = False
            self._update_cuts_label()
            self._update_summary()
            self._apply_live()
            self._apply_audio_now()       # đoạn tắt tiếng gốc nghe ngay
        self._sel_row = -1
        self._update_timeline()
        self._update_undo_btns()
        changed = sum(1 for s in segs if (round(s.start, 3), round(s.end, 3), s.text, s.trans,
                                          json.dumps(s.extra, sort_keys=True)) not in old)
        parts = [f"{changed} câu đổi"] if changed or not edit_changed else []
        if edit_changed:
            parts.append("đoạn cắt / video / lớp phủ")
        self.win.toast(f"{what}: {', '.join(parts)} · còn {len(self._undo)} bước hoàn tác, "
                       f"{len(self._redo)} bước làm lại", "ok")
        if any(x.extra.get("tts_file") for x in segs) and (self.p.edit.get("dub") or {}).get("wav"):
            self._relayout(what)       # giọng lồng theo đúng các câu sau khi hoàn tác (chỉ vá đoạn đổi)

    def _segs_edited(self):
        self._undo_mark(join=False)       # xoá / gộp / tách / kéo câu…: mỗi thao tác 1 bước hoàn tác
        self._update_steps()
        self._save_segs_timer.start()
        self._update_count()
        self._refresh_overlay()
        self.saved.setText("● đang sửa…")
        if self.p:   # thêm / xoá câu: đoạn giọng lồng trên timeline vẫn đúng câu của nó
            self.tracks.dub = pipeline.dub_clips(self.p, self.model.segs)
        self.tracks.canvas.update()

    def _save_segs_now(self):
        if not self.p or self._live_draft:   # đang hiện câu nháp của "vừa tách vừa dịch": không ghi đè dự án
            return
        self._undo_mark()
        self.p.reload()
        if self._disk_sig is not None and pipeline.seg_sigs(self.p.segments) != self._disk_sig and self._job_touched():
            # việc nền vừa ghi danh sách câu mới (tách câu dài, nối câu, dịch…) trong lúc bảng còn hiện danh sách cũ:
            # trước đây ghi đè lại bằng danh sách cũ -> mất hết câu vừa tách, giọng lồng lệch câu
            self.reload()
            self.win.toast("Phụ đề vừa được việc đang chạy cập nhật – đã nạp bản mới, thao tác vừa rồi chưa được lưu "
                           "(làm lại nếu cần)", "warn")
            return
        self.p.segments = self.model.segs
        self.p.save()
        self._disk_sig = pipeline.seg_sigs(self.model.segs)
        self.saved.setText("✓ đã lưu " + time.strftime("%H:%M"))

    def _job_touched(self) -> bool:
        """Có việc nền của dự án này đang chạy / đang chờ, hoặc vừa xong mà giao diện chưa nạp lại."""
        for j in manager.list():
            if j["project_id"] == self.p.id and (j["status"] in ("queued", "running")
                                                 or (j.get("finished") or 0) >= self._loaded_at - 1):
                return True
        return False

    def flush(self):
        if self._save_segs_timer.isActive():
            self._save_segs_timer.stop()
            self._save_segs_now()
        if self._save_edit_timer.isActive():
            self._save_edit_timer.stop()
            self._save_edit_now()

    # ================================================================== tác vụ nền
    def submit(self, kind: str, title: str, fn, quiet: bool = False):
        if not self.p:
            return
        self.flush()
        pid = self.p.id
        job = manager.submit(kind, f"{title}: {self.p.data.get('name')}", lambda j: fn(j, Project(pid)), pid)
        if not quiet:
            self.win.toast(f"Đã thêm vào hàng đợi: {title}", "ok")
        self.win.jobs_changed()
        return job

    def update_job(self, jobs: list[dict]):
        """Gọi định kỳ từ cửa sổ chính để hiển thị tiến độ tác vụ của dự án này."""
        if not self.p:
            return
        mine = [j for j in jobs if j["project_id"] == self.p.id and j["status"] in ("queued", "running")]
        self._show_live_draft(next((j for j in mine if j["kind"] == "auto" and j["status"] == "running"), None))
        if not mine:
            self.job_box.hide()
            self._cur_job = None
            return
        j = mine[-1] if all(x["status"] == "queued" for x in mine) else next(x for x in mine if x["status"] == "running")
        self._cur_job = j["id"]
        self.job_box.show()
        eta = f" · còn ~{j['eta'] // 60} phút" if j["eta"] > 90 else (f" · còn ~{j['eta']}s" if j["eta"] > 0 else "")
        more = f"  (+{len(mine) - 1} việc)" if len(mine) > 1 else ""
        self.job_title.setText(("Đang chờ: " if j["status"] == "queued" else "") + j["title"] + more)
        self.job_msg.setText(f"{j['message']}  ·  {j['progress'] * 100:.1f}%{eta}")
        self.job_bar.set(j["progress"], busy=j["status"] == "running")

    def _show_live_draft(self, job: dict | None):
        """Vừa tách vừa dịch: câu đã nghe / đã dịch hiện ngay ở tab Phụ đề (chỉ xem, sửa khi chạy xong)."""
        from .. import stream

        f = stream.draft_path(self.p.work)
        try:
            mt = f.stat().st_mtime if job else 0.0
        except OSError:
            mt = 0.0
        if not job or mt < job["started"]:
            if self._live_draft:   # chạy xong / huỷ -> về phụ đề thật của dự án
                self._live_draft = None
                self.table.setEditTriggers(self._edit_triggers)
                self.reload()
            return
        if self._live_draft == mt:
            return
        # phim dài: dựng lại bảng + timeline vài trăm ms -> làm mới tối đa 1 lần / 3 s và chỉ khi đang xem trang dự án
        if self._live_draft and (time.time() - getattr(self, "_live_draft_t", 0) < 3.0 or not self.isVisible()):
            return
        self._live_draft_t = time.time()
        segs = stream.read_draft(self.p.work)
        if segs is None:
            return
        if not self._live_draft:
            self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._live_draft = mt
        at_end = self.table.verticalScrollBar().value() >= self.table.verticalScrollBar().maximum() - 4
        self.model.set_segments(segs)
        self._update_count()
        if at_end:   # đang xem cuối bảng -> cuộn theo câu mới
            self.table.scrollToBottom()

    def job_finished(self, j: dict):
        if self.p and j["project_id"] == self.p.id:
            if self._live_draft:
                self._live_draft = None
                self.table.setEditTriggers(self._edit_triggers)
            redub = j["kind"] in ("redub", "relayout")
            self._play_after_mix = getattr(self, "_replay_row", None) if redub and j.get("status") == "done" else None
            self._replay_row = None if redub else getattr(self, "_replay_row", None)
            self.reload()
            self._update_player_audio()
            if j["kind"] == "retime" and j.get("status") == "done":
                self._timeline_need.pop(self.p.id, None)
                if (j.get("result") or {}).get("retime") == "done":
                    self._undo_reset()          # ảnh chụp hoàn tác còn mốc giờ cũ
                    self._load_timeline_assets()
                self._update_sync_status()
            pick = (j.get("result") or {}).get("pick") if isinstance(j.get("result"), dict) else None
            if pick:   # tích sẵn các câu vừa làm (AI rút gọn…) để thấy ngay / nghe / sửa tiếp
                self.model.set_picked(range(len(self.model.segs)), False)
                self.model.set_picked([r for r in pick if 0 <= r < len(self.model.segs)])
            again = (j.get("result") or {}).get("redub") if isinstance(j.get("result"), dict) else None
            if again and j.get("status") == "done" and j["id"] not in self._chained:
                self._chained.add(j["id"])   # tách câu dài xong -> đọc lại giọng các vế mới (việc thứ 2), đúng 1 lần
                self._redub_rows(again, play=False)
            if j["kind"] == "render" and j.get("status") == "done":
                self.pub._use_latest()          # mục Đăng video dùng ngay bản vừa xuất
            elif j["kind"] == "publish":
                self.pub.fill_history()
            if j["kind"] in ("preview", "download"):
                self._load_media()
            elif j["kind"] == "inpaint":
                self._sync_clean()
            cb = self._after_job.pop(j["id"], None)
            if cb and j.get("status") == "done":
                cb(j.get("result") if isinstance(j.get("result"), dict) else {})

    def _auto_opts(self) -> dict:
        which = self.a_which.currentData()
        return {"method": self.a_method.currentData(), "asr_engine": self.a_asr.currentData(),
                "ocr_fps": float(self.a_ocrfps.currentData() or 4),
                "language": self.a_lang.currentData(), "separate": self.a_sep.isChecked(),
                "diarize": self.a_spk.isChecked(), "engine": self.a_engine.currentData(),
                "target": self.a_target.currentData(),
                "style": self.a_style.currentData(), "hardsub_mode": self.a_hardsub.currentData(),
                "burn": which != "none", "which": which if which != "none" else "trans",
                "dub": self.a_dub.isChecked(), "tts_engine": self.a_tts.currentData(),
                "voice": self.a_voice.currentData() or "", "parallel": self.a_parallel.isChecked(),
                "denoise": self.a_denoise.isChecked(), "force_recognize": self.a_force.isChecked()}

    def run_auto(self):
        if not self.p or not self.p.data.get("source"):
            return self.win.toast("Dự án chưa có video", "err")
        opts = self._auto_opts()
        if opts["method"] in ("ocr", "hybrid") and self._hs_regions:   # vùng chữ đã khoanh (như "1. Nghe & tách câu")
            opts["region"] = self._hs_regions[0]
        if self.p.segments and not opts["force_recognize"]:
            done = (any(x.trans.strip() for x in self.p.segments)
                    and pipeline.trans_lang(self.p) == (opts.get("target") or pipeline.target_lang(self.p, self.settings)))
            self.win.toast("Dự án đã có phụ đề → dùng lại (bật 'Nhận dạng lại từ đầu' nếu muốn làm lại)" +
                           (" · đã có bản dịch → giữ nguyên, chỉ dịch câu còn trống (dịch lại cả phim: tab Phụ đề → "
                            "Dịch lại tất cả)" if done else ""), "warn")
        opts["render"] = False   # dừng trước bước xuất: kiểm tra xong tự bấm "Xuất video"
        self.submit("auto", "Dịch tự động", lambda j, p: pipeline.auto(p, opts, j.update, j.cancelled))

    def run_recognize(self):
        if self.p.segments and QMessageBox.question(self, "Nhận dạng lại?", "Thay thế toàn bộ phụ đề hiện có?") != \
                QMessageBox.StandardButton.Yes:
            return
        o = self._auto_opts()
        st = dict(load_settings())
        st.update({"asr_engine": o["asr_engine"], "diarize": o["diarize"], "sep_before_asr": o["separate"],
                   "asr_dual": self.a_dual.isChecked()})
        region = self._hs_regions[0] if (o["method"] in ("ocr", "hybrid") and self._hs_regions) else None

        def fn(j, p):
            n = pipeline.recognize(p, o["method"], o["language"], region, o["ocr_fps"], st, j.update, j.cancelled,
                                   o["denoise"])
            r = p.data.get("asr_rescued", 0)
            msg = f"Đã lấy {n} câu phụ đề" + (f" (nghe lại được thêm {r} câu bị sót)" if r else "")
            rl = p.data.get("asr_relisten") or {}
            if o["method"] != "ocr" and rl.get("cuts"):
                msg += f" · nghe lại {rl['cuts']} chỗ cắt khúc giữa lúc nói" + \
                    (f", nối {rl['merged']} câu bị chặt đôi" if rl.get("merged") else "") + \
                    (f", sửa {rl['fixed']} chữ" if rl.get("fixed") else "")
            if o["method"] != "ocr" and p.data.get("asr_joined"):
                msg += f" · nối lại {p.data['asr_joined']} câu bị cắt ngang"
            if o["method"] != "ocr" and p.data.get("ai_joined"):
                msg += f" · AI nối thêm {p.data['ai_joined']} câu theo nghĩa"
            if o["method"] != "ocr" and p.data.get("ai_join_note"):
                msg += " · ⚠ " + p.data["ai_join_note"]
            if o["method"] != "ocr" and p.data.get("songs_found"):
                msg += f" · {p.data['songs_found']} câu là lời bài hát – đã đánh dấu 🎵 (không lồng tiếng)"
            if o["method"] != "ocr" and p.data.get("noise_found"):
                msg += (f" · ⚠ {p.data['noise_found']} câu nghi là nhạc / tiếng động bị nghe thành lời – tab Phụ đề "
                        "bấm 'Tích câu nghi nghe nhầm', nghe lại rồi Xoá")
            if pipeline.listen_note(p):
                msg += " · " + pipeline.listen_note(p)
            if p.data.get("snap_stats"):
                msg += " · " + pipeline.snap_note(p.data["snap_stats"])
            if o["method"] == "asr":
                msg += " · kiểu tách: " + dict(C.SPLIT_STYLES).get(st.get("split_style", "dub"), "").split(" –")[0]
            used = {"qwen3": "Qwen3-ASR", "firered": "FireRedASR2", "sensevoice": "SenseVoice",
                    "paraformer": "Paraformer", "whisper": "Whisper"}.get(p.data.get("asr_engine_used", ""), "")
            if o["method"] == "asr" and used:
                msg += f" · nghe bằng {used}"
            if o["method"] != "ocr" and p.data.get("asr_warning"):
                msg += "  ⚠ " + p.data["asr_warning"]
            if o["method"] != "ocr" and p.data.get("sep_warning"):
                msg += "  ⚠ " + p.data["sep_warning"]
            elif o["method"] != "ocr" and o["separate"] and pipeline.stems(p):
                msg += f" · đã tách giọng / nhạc nền bằng {pipeline.sep_model_name(p)} (dùng được ở tab Âm thanh)"
            if o["method"] != "ocr":
                msg += " · " + pipeline.speaker_note(p, st)
            if o["method"] in ("ocr", "hybrid"):
                ost = p.data.get("ocr_stats") or {}
                if ost.get("frames"):
                    msg += f" · quét {ost['frames']} khung ({ost.get('fps', 4):g} lần/giây), đọc chữ {ost['ocr_runs']} lần"
                if p.data.get("ocr_warning"):
                    msg = "⚠ " + p.data["ocr_warning"]
            return {"message": msg}
        self.submit("recognize", "Lấy phụ đề", fn)

    def sync_engine(self):
        """Ô công cụ dịch / văn phong (tab Tự động) theo Cài đặt – gọi khi mở dự án và khi lưu Cài đặt."""
        st = self.settings
        was, self.loading = self.loading, True
        try:
            if self.a_engine.currentData() != pipeline.NO_TRANSLATE:
                set_combo(self.a_engine, st.get("translate_engine", "google"))
            set_combo(self.a_style, st.get("translate_style", "auto"))
            if self.p is not None:
                set_combo(self.a_target, pipeline.target_lang(self.p, st))
            # tách giọng / phân biệt người nói / mô hình tách: 1 giá trị chung với Cài đặt (trước 2.0.53 các ô này chỉ
            # đọc Cài đặt lúc mở tool -> đổi Cài đặt giữa chừng thì "1. Nghe & tách câu" vẫn chạy theo giá trị cũ)
            self.a_sep.setChecked(bool(st.get("sep_before_asr", True)))
            self.a_spk.setChecked(bool(st.get("diarize", True)))
            set_combo(self.d_sepmodel, st.get("sep_model"))
            set_combo(self.a_split, st.get("split_style", "dub"))
            self.a_joinai.clear()     # danh sách AI theo key hiện có ở Cài đặt
            for val, text in pipeline.join_engine_choices(st):
                self.a_joinai.addItem(text, val)
            set_combo(self.a_joinai, st.get("ai_join_engine", "auto"))
            self._fill_skills(st)
            self.a_clean.setChecked(bool(st.get("ai_clean_before_translate", True)))
            self.a_review.setChecked(bool(st.get("ai_review_after_translate", False)))
            self.a_songai.setChecked(bool(st.get("ai_song_filter", True)))
            self.a_fill.setChecked(bool(st.get("translate_fill", True)))
            on = bool(st.get("split_long_trans", True))
            self.a_split_long.setChecked(on)
            self.s_split.setChecked(on)
            self.s_split_n.setValue(int(st.get("split_long_units", 14) or 14))
            self.s_split_p.setValue(float(st.get("split_pause", 0.25)))
            self.d_interj.setChecked(bool(st.get("dub_drop_interj", True)))
            self.d_interj_in.setChecked(bool(st.get("interj_inline", True)))
            from ..voicefix import INTERJ_DEFAULT

            iw = st.get("interj_words")
            self.d_interj_words.setText(INTERJ_DEFAULT if iw is None else str(iw))
            self.a_parallel.setChecked(bool(st.get("auto_parallel", False)))
            self._sync_follow(st)
            from .. import autoflow

            pv = autoflow.preset_values(st)
            set_combo(self.a_method, pv["method"])
            set_combo(self.a_lang, pv["language"])
            set_combo(self.a_hardsub, pv["hardsub_mode"])
            set_combo(self.a_which, pv["which"])
            self.a_dub.setChecked(bool(pv["dub"]))
            self.a_denoise.setChecked(bool(pv["denoise"]))
            set_combo(self.a_asr, st.get("asr_engine"))
            set_combo(self.a_ocrfps, str(st.get("ocr_fps", "4")))
            self.a_asr.setEnabled(pv["method"] in ("asr", "hybrid"))
            self.a_ocrfps.setEnabled(pv["method"] in ("ocr", "hybrid"))
            self.a_dual.setChecked(bool(st.get("asr_dual", False)))
        finally:
            self.loading = was
        self._update_engine_label()

    # ------------------------------------------------------------------ kỹ năng dịch AI
    @staticmethod
    def _skill_items(st: dict) -> list[tuple[str, str]]:
        from .. import skills

        return [("", "Không dùng")] + [(x["id"], ("🔒 " if x.get("builtin") else "✎ ") + x["name"])
                                       for x in skills.all_skills(st)]

    def _fill_skills(self, st: dict):
        was, self.loading = self.loading, True
        try:
            self.a_skill.clear()
            for val, text in self._skill_items(st):
                self.a_skill.addItem(text, val)
            set_combo(self.a_skill, st.get("translate_skill", ""))
        finally:
            self.loading = was
        self._sync_mirrors()

    def _skill_changed(self, *_):
        if self.loading:
            return
        from ..config import save_settings

        self.settings = save_settings({"translate_skill": self.a_skill.currentData() or ""})

    def _preset_changed(self, *_):
        """Ô của tab Tự động đổi -> lưu vào thiết lập tự động dùng chung (video tải từ link / hàng loạt làm y như vậy)."""
        if getattr(self, "loading", True):
            return
        from ..config import save_settings

        d = {"auto_preset": {"method": self.a_method.currentData(), "language": self.a_lang.currentData(),
                             "hardsub_mode": self.a_hardsub.currentData(), "which": self.a_which.currentData(),
                             "dub": self.a_dub.isChecked(), "denoise": self.a_denoise.isChecked()},
             "asr_engine": self.a_asr.currentData(), "ocr_fps": self.a_ocrfps.currentData() or "4",
             "tts_engine": self.a_tts.currentData()}
        self.settings = save_settings(d)

    def _ai_switch_changed(self, *_):
        if getattr(self, "loading", True):
            return
        from ..config import save_settings

        self.settings = save_settings({"ai_clean_before_translate": self.a_clean.isChecked(),
                                       "ai_review_after_translate": self.a_review.isChecked(),
                                       "ai_song_filter": self.a_songai.isChecked(),
                                       "translate_fill": self.a_fill.isChecked()})

    def _edit_skills(self):
        from .skills_dialog import SkillsDialog

        dlg = SkillsDialog(self, self.a_skill.currentData() or "")
        dlg.exec()
        dlg.deleteLater()
        self.settings = load_settings()
        self._fill_skills(self.settings)

    def _ai_engine_or_warn(self) -> str | None:
        from ..translate import LOCAL_ENGINES

        eng = self.a_engine.currentData()
        if eng in LOCAL_ENGINES or eng == pipeline.NO_TRANSLATE:
            self.win.toast("Kỹ năng dịch AI cần công cụ dịch AI (DeepSeek / Claude / GPT / Gemini / Ollama…) – đổi Công "
                           "cụ dịch ở tab Tự động", "warn")
            return None
        if not self.model.segs:
            self.win.toast("Chưa có phụ đề – làm bước 1 (Nghe && tách câu) trước", "warn")
            return None
        self.flush()
        return eng

    def run_ai_clean(self):
        eng = self._ai_engine_or_warn()
        if not eng:
            return
        if any(x.trans.strip() for x in self.model.segs) and QMessageBox.question(
                self, "AI sửa lỗi nghe & nối câu", "Đã có bản dịch. AI sửa câu gốc / nối câu thì các câu đó cần dịch lại "
                "(câu được tích sẵn). Tiếp tục?") != QMessageBox.StandardButton.Yes:
            return

        def fn(j, p):
            st = pipeline.ai_cleanup(p, None, eng, j.update, j.cancelled)
            msg = pipeline.cleanup_note(st)
            if not msg:
                return {"message": "AI không thấy chỗ nào cần sửa"}
            if any(p.segments[k].trans.strip() for k in st["changed"]):
                msg += " · câu đổi được tích sẵn – bấm 'Dịch câu đã tích'"
            return {"message": msg + " · bấm 'Hoàn tác sửa' nếu không ưng", "pick": st["changed"]}
        self.submit("clean", "AI sửa lỗi nghe & nối câu", fn)

    def run_ai_filter(self):
        """AI lọc lời bài hát / chữ không phải lời thoại theo nội dung (không sửa chữ, không nối câu)."""
        eng = self._ai_engine_or_warn()
        if not eng:
            return

        def fn(j, p):
            st = pipeline.ai_cleanup(p, None, eng, j.update, j.cancelled, mode="filter")
            msg = pipeline.cleanup_note(st)
            if not msg:
                return {"message": "AI không thấy lời bài hát / chữ nào không phải lời thoại"}
            if st["songs"]:
                msg += " · câu 🎵 được tích sẵn – muốn bỏ hẳn phụ đề lời hát thì bấm Xoá"
            return {"message": msg + " · bấm 'Hoàn tác sửa' nếu không ưng", "pick": st["songs"] or st["changed"]}
        self.submit("clean", "AI lọc lời bài hát", fn)

    def run_ai_join(self):
        """AI nối câu theo nghĩa (mục 4). Dự án đã dịch: câu gộp được dịch lại ngay bằng công cụ đang chọn."""
        if not self.model.segs:
            return self.win.toast("Chưa có phụ đề", "warn")
        st = dict(load_settings())
        st["ai_join_engine"] = self.a_joinai.currentData() or "auto"
        eng, why = pipeline.join_engine(st)
        if not eng:
            return self.win.toast(f"Chưa chọn được AI để nối câu: {why}", "warn")
        self.flush()
        t_eng, sty = self.a_engine.currentData(), self.a_style.currentData()
        translated = any(x.trans.strip() for x in self.model.segs)

        def fn(j, p):
            rows = pipeline.ai_join(p, st, eng, lambda x, m: j.update(0.8 * x, m), j.cancelled)
            if not rows:
                return {"message": f"{why}: không thấy dòng nào cần nối"}
            msg = f"{why} nối {len(rows)} câu bị chặt đôi (được tích sẵn) · bấm 'Hoàn tác nối' nếu không ưng"
            empty = [k for k in rows if p.segments[k].text.strip() and not p.segments[k].trans.strip()]
            if translated and empty and t_eng != pipeline.NO_TRANSLATE:
                pipeline.translate(p, t_eng, sty, settings=load_settings(), rows=empty,
                                   progress=lambda x, m: j.update(0.8 + 0.2 * x, m), cancel=j.cancelled)
                msg += f" · đã dịch lại {len(empty)} câu gộp"
            return {"message": msg, "pick": rows}
        self.submit("join", "AI nối câu", fn)

    def run_join(self):
        """Nối câu bị cắt ngang. Dự án đã dịch: câu mới chưa có bản dịch được DỊCH LUÔN (trước đây để trống, phải tự bấm
        dịch) và đã lồng tiếng thì đọc lại giọng đúng các câu đó."""
        if not self.model.segs:
            return self.win.toast("Chưa có phụ đề", "warn")
        self.flush()
        eng, sty = self.a_engine.currentData(), self.a_style.currentData()
        tts_eng, rate = self.d_engine.currentData(), self.d_rate.currentData()
        st = dict(load_settings())
        st["dub_skip_songs"] = self.d_skipsong.isChecked()
        translated = any(x.trans.strip() for x in self.model.segs)

        def fn(j, p):
            j.update(0.05, "Đang tìm câu bị cắt ngang…")
            rows = pipeline.join_lines(p)
            if not rows:
                return {"message": "Không thấy câu nào bị cắt ngang"}
            msg = f"Đã nối và tách lại thành {len(rows)} câu trọn ý (được tích sẵn)"
            empty = [k for k in rows if p.segments[k].text.strip() and not p.segments[k].trans.strip()]
            if translated and empty:
                if eng == pipeline.NO_TRANSLATE:
                    msg += f" – {len(empty)} câu chưa có bản dịch: chọn công cụ dịch rồi bấm 'Dịch câu đã tích'"
                else:
                    pipeline.translate(p, eng, sty, False, None, lambda x, m="": j.update(0.1 + 0.5 * x, m),
                                       j.cancelled, rows=empty)
                    left = sum(1 for k in empty if not p.segments[k].trans.strip())
                    msg += f" · đã dịch {len(empty) - left} câu mới" + (f" (⚠ {left} câu chưa dịch được)" if left else "")
            if pipeline.has_dub(p) and any(p.segments[k].trans.strip() for k in rows):
                pipeline.redub(p, rows, rate, st, lambda x, m="": j.update(0.6 + 0.4 * x, m), j.cancelled, tts_eng)
                msg += " và tạo lại giọng các câu đó"
            return {"message": msg + " · bấm 'Hoàn tác nối' nếu không ưng", "pick": rows}
        self.submit("redub" if pipeline.has_dub(self.p) else "join", "Nối câu bị cắt", fn)

    def _split_opts_changed(self, *_):
        from ..config import save_settings

        d = {"split_long_trans": self.s_split.isChecked(), "split_long_units": int(self.s_split_n.value()),
             "split_pause": round(self.s_split_p.value(), 2)}
        save_settings(d)
        self.settings.update(d)
        if self.a_split_long.isChecked() != self.s_split.isChecked():   # công tắc gương ở tab Tự động
            was, self.loading = self.loading, True
            try:
                self.a_split_long.setChecked(self.s_split.isChecked())
            finally:
                self.loading = was

    def run_split_long(self):
        """Tách câu dịch dài ở dấu ? ! . / dấu phẩy (câu đã tích, chưa tích thì cả phim). 2 bước: tách (nhanh) -> bảng
        hiện ngay các câu mới; đã lồng tiếng thì việc thứ 2 đọc lại giọng các vế (bảng đã là danh sách mới nên thao tác
        trong lúc đọc không ghi đè mất câu đã tách)."""
        if not self.model.segs or not any(x.trans.strip() for x in self.model.segs):
            return self.win.toast("Chưa có bản dịch – dịch trước rồi mới tách câu dài", "warn")
        self.flush()
        picked = sorted(self.model.selected)
        st = dict(load_settings())

        def fn(j, p):
            j.update(0.05, "Đang tìm câu dịch dài có dấu câu ở giữa…")
            dubbed = pipeline.has_dub(p) or any(x.extra.get("tts_file") for x in p.segments)
            rows = pipeline.split_long(p, st, picked or None, lambda x, m="": j.update(0.05 + 0.9 * x, m), j.cancelled)
            if not rows:
                return {"message": f"Không có câu dịch nào dài hơn {st.get('split_long_units', 14)} tiếng mà tách được "
                                   "(cần dấu ? ! . hoặc dấu phẩy ở giữa câu, có ở cả câu gốc lẫn câu dịch)"}
            n0 = len(rows) - (len(p.segments) - len(p.data.get("split_backup") or p.segments))
            msg = f"Đã tách {n0} câu dài thành {len(rows)} câu ngắn (được tích sẵn)"
            if dubbed:
                msg += " · đang đọc lại giọng các vế mới (việc tiếp theo)"
            return {"message": msg + " · bấm 'Hoàn tác tách' nếu không ưng", "pick": rows,
                    "redub": rows if dubbed else []}
        self.submit("join", "Tách câu dài", fn)

    def run_undo_split(self):
        if not self.p or not self.p.data.get("split_backup"):
            return self.win.toast("Chưa có lần tách câu dài nào để hoàn tác", "info")
        self.flush()
        n = pipeline.undo_split(self.p, self.settings)
        self.reload()
        self.win.toast(f"Đã về bản trước khi tách câu ({n} câu)", "ok")

    def run_undo_join(self):
        if not self.p or not self.p.data.get("join_backup"):
            return self.win.toast("Chưa có lần nối câu nào để hoàn tác", "info")
        self.flush()
        n = pipeline.undo_join(self.p, self.settings)
        self.reload()
        self.win.toast(f"Đã về bản trước khi nối câu ({n} câu)", "ok")

    def run_undo_clean(self):
        if not self.p or not self.p.data.get("cleanup_backup"):
            return self.win.toast("Chưa có lần AI sửa lỗi nghe nào để hoàn tác", "info")
        self.flush()
        n = pipeline.undo_cleanup(self.p, self.settings)
        self.reload()
        self.win.toast(f"Đã về bản trước khi AI sửa ({n} câu)", "ok")

    def run_ai_review(self):
        eng = self._ai_engine_or_warn()
        if not eng:
            return
        if not any(x.trans.strip() for x in self.model.segs):
            return self.win.toast("Chưa có bản dịch để kiểm tra – bấm Dịch trước", "warn")
        tts_eng, rate = self.d_engine.currentData(), self.d_rate.currentData()
        st = dict(load_settings())
        st["dub_skip_songs"] = self.d_skipsong.isChecked()

        def fn(j, p):
            rows = pipeline.ai_review(p, None, eng, lambda x, m="": j.update(0.6 * x, m), j.cancelled)
            if not rows:
                return {"message": "AI kiểm tra xong – không câu nào cần sửa"}
            msg = f"AI kiểm tra và sửa {len(rows)} câu (rê chuột vào câu để xem lý do; câu được tích sẵn)"
            if pipeline.has_dub(p):
                pipeline.redub(p, rows, rate, st, lambda x, m="": j.update(0.6 + 0.4 * x, m), j.cancelled, tts_eng)
                msg += " và đã tạo lại giọng các câu đó"
            return {"message": msg, "pick": rows}
        self.submit("redub" if pipeline.has_dub(self.p) else "review", "AI kiểm tra bản dịch", fn)

    def run_profile(self):
        eng = self._ai_engine_or_warn()
        if not eng:
            return

        def fn(j, p):
            j.update(0.1, "AI đang đọc kịch bản, lập hồ sơ phim…")
            pipeline.film_profile(p, None, eng, force=True)
            return {"message": "Đã lập hồ sơ phim – xem / sửa ở tab Phụ đề (khung Kỹ năng dịch AI)"}
        self.submit("profile", "AI lập hồ sơ phim", fn)

    def _audio_opts_changed(self, *_):
        """Đổi công tắc tách giọng / phân biệt người nói (tab Tự động) hay mô hình tách (tab Lồng tiếng) -> lưu vào Cài
        đặt, để mọi nơi (Cài đặt, Nghe & tách câu, Tách ngay, chạy trọn gói) dùng cùng 1 giá trị."""
        if getattr(self, "loading", True) or not hasattr(self, "d_sepmodel"):
            return
        from ..config import save_settings

        self.settings = save_settings({"sep_before_asr": self.a_sep.isChecked(), "diarize": self.a_spk.isChecked(),
                                       "sep_model": self.d_sepmodel.currentData() or self.settings.get("sep_model"),
                                       "split_style": self.a_split.currentData() or "dub",
                                       "ai_join_engine": self.a_joinai.currentData() or "auto",
                                       "asr_dual": self.a_dual.isChecked()})

    def _engine_changed(self, *_):
        self._update_engine_label()
        if self.loading:
            return
        eng = self.a_engine.currentData()
        from ..config import save_settings

        save_settings({"translate_style": self.a_style.currentData(),
                       **({"translate_engine": eng} if eng != pipeline.NO_TRANSLATE else {})})
        self.settings = load_settings()

    def _target_changed(self, *_):
        """Đổi tiếng đích của dự án: lưu vào dự án (+ làm mặc định cho dự án mới), đổi nhãn / giọng theo tiếng mới."""
        if self.loading or not self.p:
            return
        from ..config import save_settings

        lang = self.a_target.currentData()
        old = pipeline.target_lang(self.p, self.settings)
        if lang == old:
            return
        self.p.reload()
        self.p.data[pipeline.glossary_key(old)] = self.glossary.toPlainText()
        self.p.data["target_lang"] = lang
        self.p.save()
        save_settings({"target_lang": lang})
        self.settings = load_settings()
        self._apply_target(lang)
        self.loading = True
        try:
            self._fill_speakers()
        finally:
            self.loading = False
        self._refresh_overlay()
        name = langs.name(lang)
        have = sum(1 for x in self.model.segs if x.trans.strip())
        if have and pipeline.trans_lang(self.p) != lang:
            self.win.toast(f"Đã chọn dịch sang {name}. Bản dịch đang có là {langs.name(pipeline.trans_lang(self.p))} – "
                           f"bấm '2. Dịch' để dịch lại toàn bộ sang {name}", "warn")
        else:
            self.win.toast(f"Dự án này dịch sang {name}" +
                           (" – VieNeu chỉ đọc tiếng Việt, lồng tiếng sẽ dùng Edge (hoặc chọn OmniVoice)"
                            if lang != "vi" and self.d_engine.currentData() == "vieneu" else ""), "ok")

    def _note_trans_lang(self) -> str:
        """Nạp phụ đề đã dịch tiếng khác tiếng đích đang chọn -> đổi "Dịch sang" theo, trả về câu báo."""
        self.flush()
        lg = pipeline.note_trans_lang(self.p)
        if not lg:
            return ""
        self._apply_target(lg)
        self.loading = True
        try:
            self._fill_speakers()
        finally:
            self.loading = False
        self._refresh_overlay()
        return f" · nhận ra bản dịch {langs.name(lg).lower()} – đã đổi 'Dịch sang' của dự án thành {langs.name(lg)}"

    def _apply_target(self, lang: str):
        """Nhãn theo tiếng đích: cột / ô bản dịch, lựa chọn phụ đề ghép vào video, nút SRT, bảng thuật ngữ."""
        name = langs.name(lang)
        short = langs.INFO[langs.norm(lang)]["short"]
        was, self.loading = self.loading, True
        try:
            set_combo(self.a_target, lang)
            self.model.set_trans_label(name)
            self.inspector.set_lang(name)
            ch = C.which_choices(lang)
            C.relabel(self.a_which, ch)
            C.relabel(self.st_which, ch)
            C.relabel(self.ov_which, [("trans", f"Xem: {name}"), ("both", "Xem: Song ngữ")])
            self.x_srt_trans.setText(f"SRT tiếng {short}")
            self.glossary.setPlaceholderText(
                "Mỗi dòng: 中文=Tiếng Việt   (VD: 李逍遥=Lý Tiêu Dao). AI tự điền khi dịch." if lang == "vi" else
                f"Mỗi dòng: 中文={name}   (VD: 李逍遥=Li Xiaoyao). Bảng riêng cho {name.lower()}, AI tự điền khi dịch.")
            if self.p is not None:
                self.glossary.setPlainText(self.p.data.get(pipeline.glossary_key(lang), ""))
        finally:
            self.loading = was

    def _update_engine_label(self):
        if getattr(self, "d_engine_lbl", None) is not None:
            self.d_engine_lbl.setText("bằng: " + self.a_engine.currentText().split(" – ")[0] +
                                      " (đổi ở Cài đặt hoặc tab Tự động)")

    def _translate_rows(self, rows: list[int] | None, only_empty: bool):
        """Dịch ngay ở tab Lồng tiếng: câu chưa dịch (rows None) / dịch lại câu đã tích; đã có bản lồng thì đọc luôn
        giọng ĐÚNG các câu vừa đổi lời; các câu đó được tích sẵn."""
        eng, sty = self.a_engine.currentData(), self.a_style.currentData()
        if eng == pipeline.NO_TRANSLATE:
            return self.win.toast("Đang chọn 'Không dịch' ở tab Tự động – chọn công cụ dịch trước", "warn")
        if rows is not None and not rows:
            return self.win.toast("Tích chọn câu cần dịch lại ở cột Chọn trước", "warn")
        segs = self.model.segs
        if rows is None and not any(x.text.strip() and not x.trans.strip() for x in segs):
            return self.win.toast("Câu nào cũng đã có bản dịch", "info")
        self.inspector._flush()
        before = [x.trans for x in segs]
        tts_eng, rate = self.d_engine.currentData(), self.d_rate.currentData()
        st = dict(load_settings())
        st["dub_skip_songs"] = self.d_skipsong.isChecked()
        self._replay_row = None

        def fn(j, p):
            pipeline.translate(p, eng, sty, only_empty, None, lambda x, m="": j.update(0.6 * x, m), j.cancelled,
                               rows=rows)
            new = p.segments
            changed = [k for k, x in enumerate(new) if k < len(before) and x.trans.strip() and x.trans != before[k]]
            msg = f"Đã dịch {len(changed)} câu"
            w = p.data.get("translate_warning")
            if changed and pipeline.has_dub(p):
                pipeline.redub(p, changed, rate, st, lambda x, m="": j.update(0.6 + 0.4 * x, m), j.cancelled, tts_eng)
                msg += " và đã tạo giọng các câu đó – trình phát đang dùng bản mới"
            elif changed:
                msg += " – bấm 'Tạo bản lồng tiếng' để đọc"
            return {"message": msg + (f" – ⚠ {w}" if w else ""), "pick": changed}
        self.submit("redub" if pipeline.has_dub(self.p) else "translate",
                    "Dịch câu chưa dịch" if rows is None else f"Dịch lại {len(rows)} câu", fn)

    def run_translate(self, only_empty: bool):
        eng, sty = self.a_engine.currentData(), self.a_style.currentData()
        if eng == pipeline.NO_TRANSLATE:
            return self.win.toast("Đang chọn 'Không dịch' ở tab Tự động. Chọn công cụ dịch, hoặc nạp SRT đã dịch "
                                  "(tab Phụ đề → Nạp SRT đã dịch)", "warn")

        def fn(j, p):
            n = pipeline.translate(p, eng, sty, only_empty, None, j.update, j.cancelled)
            w = " · ".join(x for x in (p.data.get("translate_warning"), p.data.get("skill_warning")) if x)
            note = p.data.get("skill_note") if not only_empty or n else ""
            return {"message": f"Đã dịch {n} câu" + (f" · {note}" if note else "") + (f" – ⚠ {w}" if w else "")}
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
            if pipeline.has_dub(p):
                extra += " · đã xếp lại giọng lồng theo mốc mới (không đọc lại)"
                if n > before:
                    extra += ", câu mới tách cần 'Tạo bản lồng tiếng' để có giọng"
            return {"message": f"Đã căn lại thời gian {n} câu{extra}"}
        self.submit("realign", "Căn lại thời gian", fn)

    def run_snap(self):
        """Căn mép câu theo giọng (2.0.96): đầu / cuối câu đã tích (không tích -> cả phim) về đúng lúc nhân vật bắt
        đầu / thôi nói. Nút ↶ / Ctrl+Z hoàn tác."""
        if not self.model.segs:
            return self.win.toast("Chưa có phụ đề", "warn")
        self.flush()
        rows = sorted(self.model.selected) or None
        remeasure = self.s_snap_ct.isChecked()

        def fn(j, p):
            st = pipeline.snap_edges(p, None, rows, remeasure, True, j.update, j.cancelled)
            msg = pipeline.snap_note(st)
            msg = msg[0].upper() + msg[1:]
            if st.get("measured"):
                msg += f" · đo lại mốc từng chữ {st['measured']} câu"
            if pipeline.has_dub(p) and st.get("moved"):
                msg += " · đã xếp lại giọng lồng theo mốc mới (không đọc lại)"
            return {"message": msg + (f" ({len(rows)} câu đã tích)" if rows else "")}
        self.submit("realign" if remeasure else "snap", "Căn mép câu theo giọng", fn)

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

    def _step_translate(self):
        """Bước 2: chưa dịch câu nào -> dịch hết; dịch dở -> chỉ dịch câu còn trống (giữ câu đã dịch / đã sửa tay);
        đã dịch hết -> hỏi trước khi dịch lại tất cả (trước đây luôn dịch lại tất cả, mất chỗ đã sửa)."""
        segs = self.model.segs
        if not segs:
            return self.win.toast("Làm bước 1 (Nghe && tách câu) trước đã", "warn")
        done = sum(1 for x in segs if x.trans.strip())
        todo = sum(1 for x in segs if x.text.strip() and not x.trans.strip())
        if not done:
            return self.run_translate(False)
        if todo:
            return self.run_translate(True)
        if QMessageBox.question(self, "Dịch lại tất cả?", "Mọi câu đã có bản dịch. Dịch lại TẤT CẢ? (các câu bạn đã sửa "
                                "tay sẽ bị thay)") == QMessageBox.StandardButton.Yes:
            self.run_translate(False)

    def _step_dub(self):
        if not any(sg.trans.strip() for sg in self.model.segs):
            return self.win.toast("Làm bước 2 (Dịch) trước đã", "warn")
        self.run_dub()

    def _step_export(self):
        self.tabs.set_current(self.TAB_EXPORT)
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
            if self._junction_cut:
                self._junction_cut = None
                self._live_timer.start()
        segs = self.model.segs
        idx = next((i for i, s in enumerate(segs) if s.start <= t < s.end), -1)
        if idx != self.model.active:
            self.model.set_active(idx)
            if idx >= 0 and self.follow.isChecked() and self.player.playing():
                table, proxy = {1: (self.table, self.proxy), self.TAB_DUB: (self.dub_table, self.dub_proxy)}.get(
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
        style = self.current_style()
        ed_v = None
        if self._ratio_view:      # xem thử tỉ lệ xuất thêm: phụ đề đặt riêng cho tỉ lệ đó (nếu đã kéo)
            style.update((self._ratio_over.get(self._ratio_view) or {}).get("sub") or {})
            ed_v = dict(self.p.edit, aspect=self._ratio_view)
        st = pipeline.effective_style(self.p, style, ed_v)
        if st.get("margin_v_rel") is not None:
            st["margin_v"] = st["margin_v_rel"] * self.player.view.out_h
        segs = self.model.segs
        try:   # câu có giọng lồng đọc sớm / bị lùi / đọc dài: phụ đề hiện theo đúng lúc giọng đọc (như video xuất)
            segs = pipeline.subs_follow_dub(self.p, segs, self.collect_edit(), self.settings, which)
        except Exception:  # noqa: BLE001 – giao diện chưa dựng xong
            pass
        self.player.set_subtitles(segs, st, which, self._karaoke_spans() if st.get("karaoke") else {})

    def _karaoke_spans(self) -> dict:
        """Đoạn giọng lồng từng câu cho karaoke xem trước (tính ở luồng nền, nhớ theo bản lồng + bật/tắt lồng tiếng)."""
        try:
            ed = self.collect_edit()
        except Exception:  # noqa: BLE001
            return {}
        d = ed.get("dub") or {}
        wav = d.get("wav") or ""
        a = ed.get("audio") or {}
        on = bool(d.get("enabled")) and "dub" not in (a.get("mute") or []) and float(a.get("dub", 1.0)) > 0
        try:
            mt = Path(wav).stat().st_mtime if on and wav else 0
        except OSError:
            mt = 0
        key = (self.p.id, wav if on else "", mt, len(self.p.data.get("dub_layout") or []))
        cache = getattr(self, "_kara", None)
        if cache and cache[0] == key:
            return cache[1]
        if not on or not mt:
            self._kara = (key, {})
            return {}
        if getattr(self, "_kara_busy", None) != key:
            self._kara_busy = key
            pid = self.p.id

            def done(spans):
                self._kara_busy = None
                if self.p and self.p.id == pid:
                    self._kara = (key, spans or {})
                    self._refresh_overlay()
            run_async(lambda: pipeline.karaoke_spans(Project(pid), ed), done, lambda m: setattr(self, "_kara_busy", None))
        return cache[1] if cache else {}

    def _media_error(self, msg: str):
        if not self.p or (self.p.work / "preview.mp4").exists() or pipeline.source_missing(self.p):
            return      # mất video gốc: không phải lỗi định dạng, tạo bản xem trước cũng không được
        self.win.toast("Không phát được định dạng này → đang tạo bản xem trước (không ảnh hưởng chất lượng xuất)", "warn")
        self.submit("preview", "Tạo bản xem trước", lambda j, p: (pipeline.make_preview(p, j.update, j.cancelled),
                                                                 {"message": "Đã tạo bản xem trước"})[1])

    def _relink_source(self):
        """Chọn lại file video gốc (bị chuyển chỗ / đổi tên) – phụ đề, bản dịch, lồng tiếng giữ nguyên."""
        if not self.p:
            return
        old = self.p.source
        start = str(old.parent) if old.parent.exists() else str(Path.home() / "Downloads")
        exts = " ".join("*" + e for e in pipeline.VIDEO_EXTS)
        path, _ = QFileDialog.getOpenFileName(self, f"Chọn lại video gốc ({old.name})", start,
                                              f"Video ({exts});;Mọi file (*)")
        if not path:
            return
        try:
            pipeline.relink_source(self.p, path)
        except Exception as e:  # noqa: BLE001
            return self.win.toast(str(e), "err")
        self.populate(reload_media=True)
        self.win.toast(f"Đã nối lại video gốc: {Path(path).name} – phụ đề, bản dịch, lồng tiếng giữ nguyên", "ok")

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
                    # chọn từ timeline / khung câu / bảng kia: đưa câu ra GIỮA bảng (tô cam) cho dễ thấy
                    table.scrollTo(pi, QAbstractItemView.ScrollHint.PositionAtCenter)
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
        elif name.startswith("overflow:"):
            self._apply_overflow([row], name.split(":", 1)[1] or None)

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
        where = "đúng lúc nhân vật nói" if not shift else f"dời {shift:+.2f} giây so với lúc nhân vật nói"
        self._relayout(f"Đã đặt giọng câu {row + 1} {where}", row)

    def _subs_moved(self, changes: list):
        """Kéo khối phụ đề trên timeline: đổi đầu / cuối câu; giọng lồng của câu đi theo lúc bắt đầu mới (vẫn giữ phần
        đã kéo dời riêng đoạn giọng) -> xếp lại bản lồng, chỉ vá quanh các câu đó."""
        segs = self.model.segs
        moved, shifted = [], {}
        for r, a, b in changes:
            if not 0 <= r < len(segs) or b - a < 0.05:
                continue
            s = segs[r]
            if abs(s.start - a) < 0.005 and abs(s.end - b) < 0.005:
                continue
            if abs(a - s.start) >= 0.005:
                shifted[r] = a - s.start
            if abs((a - s.start) - (b - s.end)) < 0.005:   # kéo cả khối: mốc từng chữ đi theo; kéo mép: giữ nguyên
                shift_ct(s, a - s.start)
            s.extra.pop("snap", None)   # người dùng tự đặt mép -> không còn là mép đã bám theo giọng
            s.start, s.end = round(a, 3), round(b, 3)
            moved.append(r)
        if not moved:
            return
        self.model.dataChanged.emit(self.model.index(min(moved), 0),
                                    self.model.index(max(moved), self.model.columnCount() - 1))
        self._segs_edited()
        # hiện ngay giọng lồng ở chỗ mới trên timeline trong lúc xếp lại ở nền
        self.tracks.dub = [[a + shifted[int(k)], b + shifted[int(k)], k, f] if int(k) in shifted else [a, b, k, f]
                           for a, b, k, f in self.tracks.dub]
        self.tracks.canvas.update()
        if self._sel_row in moved:
            self.inspector.set_row(self._sel_row)
        self._save_segs_now()
        r0 = moved[0]
        what = (f"Đã dời câu {r0 + 1} tới {fmt(segs[r0].start)} → {fmt(segs[r0].end)}" if len(moved) == 1
                else f"Đã dời {len(moved)} câu phụ đề")
        if shifted and any(segs[r].extra.get("tts_file") for r in shifted) and \
                (self.p.edit.get("dub") or {}).get("wav"):
            self._relayout(what + " – giọng lồng đi theo", r0)
        else:
            self.win.toast(what, "ok")

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
        """Công cụ trên thanh timeline – làm với thứ đang chọn trên timeline (nhiều câu / đoạn / khoảng trống)."""
        if not self.p:
            return
        tl = self.tracks
        rows = tl.selected_rows()
        if name == "sel_all":
            return tl.select_all(tl.sel_track or "subs")
        # ---- phụ đề
        if name == "sub_add":
            return self._sub_add(tl.sel_gap if tl.sel_track == "subs" else None)
        if name in ("insert", "split", "merge", "delete", "sub_split", "sub_merge", "sub_delete"):
            return self._sub_op(name.replace("sub_", ""), rows)
        if name == "sub_translate":
            if not rows:
                return self.win.toast("Chọn câu trên dòng Phụ đề trước", "warn")
            for r in rows:
                self._retranslate_one(r)
            return
        # ---- lồng tiếng
        if name in ("play", "redub", "dub_redo"):
            if not rows:
                return self.win.toast("Bấm chọn câu trên timeline trước", "warn")
            return self._play_line(rows[0]) if name == "play" else self._redub_rows(rows)
        if name in ("dub_speed", "dub_speed_reset"):
            return self._apply_speed(rows, tl.speed.value() if name == "dub_speed" else None)
        if name.startswith("dub_mode:"):
            return self._apply_overflow(rows, name.split(":", 1)[1] or None)
        if name in ("dub_toggle", "dub_reset_pos"):
            return self._dub_rows_flag(name, rows)
        # ---- video / tiếng gốc
        if name in ("v_split", "v_delete", "v_restore", "v_unsplit", "v_insert", "v_insert_del", "a_mute", "a_unmute"):
            return self._video_op(name)

    # ------------------------------------------------------------------ timeline: phụ đề
    def _sub_add(self, gap: tuple[float, float] | None):
        """Thêm câu: vào khoảng trống đang chọn (đặt ở chỗ vừa bấm, tối đa 2,5 s, không lấn câu bên cạnh) hoặc tại
        đầu phát; chọn câu mới + đưa con trỏ vào ô Tiếng Việt để gõ ngay."""
        segs = self.model.segs
        if gap:
            lo, hi = gap
            t = float(getattr(self.tracks, "gap_click", lo))
            a = max(lo + 0.02, min(t, hi - 0.6))
            b = min(hi - 0.02, a + 2.5)
        else:
            a = self.player.position()
            nxt = min((x.start for x in segs if x.start > a + 0.05), default=a + 2.0)
            b = max(a + 0.4, min(a + 2.0, nxt - 0.02))
        pos = next((k for k, x in enumerate(segs) if x.start > a), len(segs))
        segs.insert(pos, Segment(round(a, 3), round(b, 3), "", ""))
        self.model.set_segments(segs)
        self._fit_rows()
        self._segs_edited()
        self.tracks.select("subs", rows={pos})
        self._select_line(pos, seek=True)
        self._show_inspector(True)
        self.inspector.vi.setFocus()
        self.win.toast(f"Đã thêm câu #{pos + 1} ({fmt(a)} → {fmt(b)}) – gõ lời ở khung Câu đang chọn", "ok")

    def _sub_op(self, op: str, rows: list[int]):
        from .. import subtitle as st

        segs = self.model.segs
        if op == "insert":
            return self._sub_add(None)
        t = self.player.position()
        if op == "split":
            row = next((r for r in rows if segs[r].start < t < segs[r].end), None)
            if row is None:
                row = next((i for i, x in enumerate(segs) if x.start < t < x.end), rows[0] if rows else -1)
            if row < 0:
                return self.win.toast("Đưa đầu phát vào giữa câu cần tách", "warn")
            st.split(segs, row, t)
            focus = row
        elif not rows:
            return self.win.toast("Chọn câu trên dòng Phụ đề trước", "warn")
        elif op == "merge":
            lo, hi = rows[0], rows[-1]
            if lo == hi and lo >= len(segs) - 1:
                return self.win.toast("Câu cuối cùng – không còn câu sau để gộp", "warn")
            picked = any(self.model.is_picked(r) for r in range(lo, max(hi, lo + 1) + 1))
            voiced = any(segs[r].extra.get("tts_file") for r in range(lo, max(hi, lo + 1) + 1))
            if lo == hi:
                st.merge(segs, lo)
            else:
                for _ in range(hi - lo):
                    st.merge(segs, lo)
            if picked:   # gộp câu đã tích -> câu gộp vẫn được tích
                self.model.set_picked([lo])
            focus = lo
        elif op == "delete":
            for r in sorted(rows, reverse=True):
                del segs[r]
            focus = min(rows[0], len(segs) - 1)
        else:
            return
        self.model.set_segments(segs)
        self._fit_rows()
        self._segs_edited()
        self._sel_row = -1
        self.tracks.select("subs", rows={focus} if 0 <= focus < len(segs) else set())
        if 0 <= focus < len(segs):
            self._select_line(focus)
        dubbed = any(x.extra.get("tts_file") for x in segs) and (self.p.edit.get("dub") or {}).get("wav")
        if op == "delete":
            self.win.toast(f"Đã xoá {len(rows)} câu", "ok")
            if dubbed:
                self._save_segs_now()   # giọng lồng của câu đã xoá bỏ khỏi bản lồng ngay (không đọc lại câu nào)
                self._relayout("", deleted=len(rows))
        elif op == "merge" and dubbed and voiced and (segs[focus].trans or "").strip():
            # câu gộp = lời của các câu cũ nối lại -> đọc lại giọng đúng câu này (chỉ vá đoạn đó trong bản lồng); không
            # đọc lại thì bản lồng chỉ còn giọng của câu đầu
            self._save_segs_now()
            self.win.toast(f"Đã gộp – đang đọc lại giọng câu {focus + 1} (câu gộp)", "ok")
            self._redub_rows([focus])

    # ------------------------------------------------------------------ timeline: lồng tiếng
    def _dub_rows_flag(self, name: str, rows: list[int]):
        """Bỏ lồng / lồng lại, về đúng chỗ cho nhiều câu -> xếp lại bản lồng ngay (không đọc lại)."""
        segs = self.model.segs
        rows = [r for r in rows if 0 <= r < len(segs)]
        if not rows:
            return self.win.toast("Chọn đoạn giọng trên dòng Lồng tiếng trước", "warn")
        if name == "dub_toggle":
            off = not all(segs[r].extra.get("nodub") for r in rows)
            for r in rows:
                if off:
                    segs[r].extra["nodub"] = True
                else:
                    segs[r].extra.pop("nodub", None)
            what = f"Đã bỏ lồng {len(rows)} câu" if off else f"Đã lồng lại {len(rows)} câu"
        else:
            for r in rows:
                segs[r].extra.pop("dub_shift", None)
            what = f"Đã đặt {len(rows)} đoạn giọng về đúng lúc nhân vật nói"
        self.model.dataChanged.emit(self.model.index(rows[0], 0), self.model.index(rows[-1], self.model.columnCount() - 1))
        self._save_segs_now()
        if not any(x.extra.get("tts_file") for x in segs):
            return self.win.toast(what + " – áp dụng khi tạo bản lồng tiếng", "ok")
        self._relayout(what, rows[0] if name != "dub_toggle" else None)

    # ------------------------------------------------------------------ timeline: video / tiếng gốc
    def _video_op(self, name: str):
        from ..audiomix import merge_spans, subtract_spans

        tl = self.tracks
        self.p.reload()
        e = self.p.edit
        dur = float(self.p.data.get("info", {}).get("duration") or 0)
        pcs = tl.pieces()
        sel = [pcs[i] for i in sorted(tl.sel_pieces) if 0 <= i < len(pcs)]
        t = self.player.position()
        msg = ""
        if name == "v_split":
            sp = [float(x) for x in e.get("vsplits", [])]
            if not 0.05 < t < dur - 0.05 or any(abs(x - t) < 0.05 for x in sp):
                return self.win.toast("Đưa đầu phát tới chỗ cần tách (không trùng điểm tách cũ)", "warn")
            e["vsplits"] = sorted(sp + [round(t, 3)])
            msg = f"Đã tách tại {fmt(t)} – bấm chọn từng đoạn trên dòng Video / Tiếng gốc"
        elif name in ("v_delete", "v_restore", "a_mute", "a_unmute"):
            if not sel:
                return self.win.toast("Chọn đoạn trên dòng Video / Tiếng gốc trước (Tách tại đầu phát để chia đoạn)",
                                      "warn")
            if name == "v_delete" and sum(b - a for a, b in sel) >= dur - 0.1:
                return self.win.toast("Không thể xoá hết video", "warn")
            key = "cuts" if name.startswith("v_") else "mute_spans"
            cur = [list(map(float, x)) for x in e.get(key, [])]
            e[key] = merge_spans(cur + [list(x) for x in sel]) if name in ("v_delete", "a_mute") else \
                subtract_spans(cur, sel)
            msg = {"v_delete": f"Đã xoá {len(sel)} đoạn – trình phát bỏ qua ngay, video xuất không có đoạn này",
                   "v_restore": f"Đã khôi phục {len(sel)} đoạn",
                   "a_mute": f"Đã tắt tiếng gốc {len(sel)} đoạn – nghe ngay trên trình phát",
                   "a_unmute": f"Đã bật lại tiếng gốc {len(sel)} đoạn"}[name]
        elif name == "v_unsplit":
            if len(sel) < 2:
                return self.win.toast("Chọn từ 2 đoạn liền nhau trở lên để gộp", "warn")
            lo, hi = sel[0][0], sel[-1][1]
            e["vsplits"] = [x for x in e.get("vsplits", []) if not lo + 0.01 < float(x) < hi - 0.01]
            msg = f"Đã gộp {len(sel)} đoạn"
        elif name == "v_insert":
            path, _ = QFileDialog.getOpenFileName(self, "Chọn video để chèn vào", "",
                                                  "Video (*.mp4 *.mov *.mkv *.avi *.webm *.flv *.m4v)")
            if not path:
                return
            from .. import ffmpeg as ff

            try:
                info = ff.probe(path)
            except Exception as ex:  # noqa: BLE001
                return self.win.toast(f"Không đọc được video: {ex}", "err")
            if not info.has_video:
                return self.win.toast("File này không có hình", "warn")
            at = round(min(max(0.0, t), dur), 3)
            e["inserts"] = sorted(e.get("inserts", []) + [{"at": at, "path": path, "dur": round(info.duration, 3),
                                                           "name": Path(path).name}], key=lambda x: x["at"])
            msg = (f"Đã chèn {Path(path).name} ({info.duration:.1f} giây) tại {fmt(at)} – có trong video xuất "
                   f"(dấu + tím trên dòng Video)")
        elif name == "v_insert_del":
            if tl.sel_insert is None:
                return
            ins = list(e.get("inserts", []))
            if 0 <= tl.sel_insert < len(ins):
                gone = ins.pop(tl.sel_insert)
                e["inserts"] = ins
                msg = f"Đã bỏ video chèn {gone.get('name', '')}"
            tl.sel_insert = None
        self.p.data["edit"] = e
        self.p.save()
        self._undo_mark()                 # xoá / khôi phục / tách / chèn / tắt tiếng đoạn: nút ↶ hoàn tác được
        self._update_timeline()
        self._update_cuts_label()
        self._update_summary()
        self._live_timer.start()          # trình phát bỏ qua đoạn xoá ngay
        self._apply_audio_now()           # tắt / bật tiếng gốc nghe ngay
        if msg:
            self.win.toast(msg + " · bấm ↶ trên cùng (hoặc Ctrl+Z) để hoàn tác", "ok")

    def _fit_rows(self):
        """Chiều cao dòng theo chữ (xuống dòng): chỉ đo các dòng đang hiện + 1 đoạn quanh đó, dòng khác đo khi cuộn tới.
        (Trước: resizeRowsToContents đo MỌI dòng mỗi lần mở dự án / xong 1 việc – phim 1500 câu ~0,9 s đứng giao diện.)"""
        self._fitted: set[int] = set()
        self._fit_visible()

    def _fit_visible(self, *_):
        tv = self.table
        n = tv.model().rowCount() if tv.model() is not None else 0
        if not n:
            return
        fitted = getattr(self, "_fitted", None)
        if fitted is None:
            fitted = self._fitted = set()
        top = tv.rowAt(0)
        bottom = tv.rowAt(max(0, tv.viewport().height() - 1))
        top = 0 if top < 0 else top
        bottom = min(n - 1, top + 60) if bottom < 0 else bottom
        for r in range(max(0, top - 15), min(n, bottom + 30)):
            if r not in fitted:
                fitted.add(r)
                tv.resizeRowToContents(r)

    def _update_count(self):
        segs = self.model.segs
        n = len(segs)
        spk = len({s.speaker for s in segs if s.speaker})
        k = len(self.model.selected)
        self.seg_count.setText(f"{n} câu · đã dịch {sum(1 for s in segs if s.trans)}" + (f" · {spk} người nói" if spk else "")
                               + (f" · đã tích {k}" if k else ""))

    def _multi_rows(self) -> list[int]:
        """Các câu cho Gộp / Xoá ở tab Phụ đề: câu đã tích (≥ 2), không thì các dòng đang bôi chọn (Ctrl / Shift)."""
        picked = sorted(self.model.selected)
        if len(picked) >= 2:
            return picked
        sel = sorted({self.proxy.mapToSource(i).row() for i in self.table.selectionModel().selectedRows()})
        return sel if len(sel) >= 2 else []

    def _seg_op(self, op: str):
        from .. import subtitle as st

        rows = self._multi_rows() if op in ("merge", "delete") else []
        if op == "merge" and rows:
            if rows[-1] - rows[0] + 1 != len(rows):
                return self.win.toast("Chỉ gộp được các câu liền nhau – bỏ tích câu nằm xa", "warn")
            return self._sub_op("merge", rows)
        if op == "delete" and rows:
            if QMessageBox.question(self, "Xoá câu", f"Xoá {len(rows)} câu đã chọn?") != QMessageBox.StandardButton.Yes:
                return
            return self._sub_op("delete", rows)
        i = self._selected_row()
        segs = self.model.segs
        if op in ("merge", "delete") and i >= 0:
            # 1 câu: cùng đường với timeline -> bản lồng được xếp lại / câu gộp được đọc lại giọng
            return self._sub_op(op, [i])
        if op == "insert":
            t = self.player.position()
            pos = next((k for k, s in enumerate(segs) if s.start > t), len(segs))
            segs.insert(pos, Segment(t, t + 2.0, "", ""))
            i = pos
        elif i < 0:
            return self.win.toast("Hãy chọn 1 câu trong bảng trước", "warn")
        elif op == "merge":
            picked = self.model.is_picked(i) or self.model.is_picked(i + 1)
            st.merge(segs, i)
            if picked:
                self.model.set_picked([i])
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

    def _translate_picked(self):
        """Tab Phụ đề: dịch (lại) các câu đã tích; chưa tích câu nào thì dịch câu đang chọn."""
        rows = sorted(self.model.selected)
        if not rows:
            i = self._selected_row()
            if i < 0:
                return self.win.toast("Tích ô ở cột Chọn (đầu bảng) các câu cần dịch – bấm chữ 'Chọn' để tích hết",
                                      "warn")
            rows = [i]
        self._translate_rows(rows, False)

    def _toggle_pick_all(self, proxy):
        """Bấm tiêu đề cột Chọn: tích hết các câu đang hiện (đang lọc), đã tích hết rồi thì bỏ tích."""
        rows = [proxy.mapToSource(proxy.index(r, 0)).row() for r in range(proxy.rowCount())]
        on = not rows or not all(self.model.is_picked(r) for r in rows)
        self.model.set_picked(rows, on)

    def _mirror(self, src: QComboBox) -> QComboBox:
        """Ô chọn ở tab Phụ đề dùng chung giá trị với ô `src` ở tab Tự động: đổi bên nào cũng đổi bên kia (và lưu Cài đặt
        như ô gốc)."""
        m = QComboBox()
        m.setMinimumContentsLength(10)
        m.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        m.setToolTip(src.toolTip())
        self._mirrors.append((src, m))
        src.currentIndexChanged.connect(self._sync_mirrors)
        m.currentIndexChanged.connect(lambda _=0, a=src, b=m: set_combo(a, b.currentData()))
        self._sync_mirrors()
        return m

    def _sync_mirrors(self, *_):
        for src, m in self._mirrors:
            items = [(src.itemData(i), src.itemText(i)) for i in range(src.count())]
            m.blockSignals(True)
            try:
                if [(m.itemData(i), m.itemText(i)) for i in range(m.count())] != items:
                    m.clear()
                    for d, t in items:
                        m.addItem(t, d)
                m.setCurrentIndex(src.currentIndex())
            finally:
                m.blockSignals(False)

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

        tgt, src = pipeline.target_lang(self.p, self.settings), pipeline.source_lang(self.p)
        run_async(lambda: translate_text(seg.text, load_settings(), eng, tgt, src), done,
                  lambda m: self.win.toast(m, "err"))

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
        self.win.toast(f"Đã nhập {len(segs)} câu" + (self._note_trans_lang() if k else ""), "ok")

    # ================================================================== xoá phụ đề
    def _grab_frame(self):
        if not self.p or not self.p.data.get("source") or pipeline.source_missing(self.p):
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
            self._hs_lists["regions"] = [{k: box[k] for k in ("x", "y", "w", "h")}]
            if self._hs_target != "regions":
                self.hs_target.set_current(0)
            else:
                self.hs_editor.set_regions(self._hs_lists["regions"])
            if self.hs_mode.currentData() == "none":
                set_combo(self.hs_mode, "inpaint")
            self._save_edit_timer.start()
        run_async(lambda: detect_subtitle_region(src), done, lambda m: self.win.toast(m, "err"))

    def _preview_hardsub(self):
        hs = self._hs_widgets()
        regs = hs["regions"]
        if not regs and not hs["logos"]:
            return self.win.toast("Hãy khoanh vùng phụ đề / logo trước", "warn")
        from ..inpaint import preview_frame

        mode = self.hs_mode.currentData()
        if mode == "none":
            mode = "none" if hs["logos"] else "inpaint"
        elif mode == "delogo":
            mode = "blur"
        boxes = pipeline.hardsub_spec(self.p, {**hs, "mode": "inpaint"}).get("boxes_file")
        work = self.p.work / "render"
        st = dict(load_settings())
        st["inpaint_backend"] = self.hs_backend.currentData()
        busy = manager.gpu_busy()
        if busy and st["inpaint_backend"] == "lama":
            # đang có việc GPU (nghe, lồng tiếng…): xem thử bằng OpenCV, không nạp LaMa lên card chạy chồng
            st["inpaint_backend"] = "opencv"
            self.win.toast(f"Đang chạy '{busy}' trên card đồ hoạ – ảnh xem thử vẽ bằng OpenCV (gần đúng), "
                           "xuất video vẫn dùng LaMa", "warn")
        t = self.player.position()
        out = self.p.work / f"hs_preview_{int(time.time())}.jpg"
        src = self.p.source
        strength = self.hs_strength.currentData()

        def done(path):
            pm = QPixmap(path)
            self.hs_editor.set_image(pm)
            self.hs_editor.overlay = None
            self.hs_editor.update()
        pfrom = self.hs_patch.currentData()
        self.win.toast("Đang tạo ảnh xem trước…" + (" (lần đầu đo logo mất vài giây)" if hs["logos"] else ""))
        run_async(lambda: str(preview_frame(src, t, regs, out, mode, strength, st, pfrom, hs, work, boxes)), done,
                  lambda m: self.win.toast(m, "err"))

    def _clear_preview(self):
        self._grab_frame()

    # ================================================================== chỉnh sửa
    def _mark_cut(self, which: str):
        from ..audiomix import merge_spans

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
        self.flush()
        self.p.reload()
        e = self.p.edit
        # đoạn mới chồng / sát đoạn cũ -> gộp thành 1 (trước đây cộng dồn, danh sách có đoạn trùng nhau)
        e["cuts"] = merge_spans([list(map(float, x)) for x in e.get("cuts", [])] + [[round(a, 3), round(b, 3)]])
        self.p.data["edit"] = e
        self.p.save()
        self._undo_mark()
        self.cut_a = None
        self.timeline.mark = None
        self.cut_label.setText(f"Đã thêm đoạn cắt {fmt(a)} → {fmt(b)} – bấm ↶ trên cùng (hoặc Ctrl+Z) để hoàn tác")
        self._cuts_changed()

    def _clear_cuts(self):
        n = len(self.p.edit.get("cuts") or []) if self.p else 0
        if not n:
            return self.win.toast("Chưa có đoạn cắt nào", "info")
        self._set_cuts([], f"Đã bỏ {n} đoạn cắt – bấm ↶ trên cùng (hoặc Ctrl+Z) để lấy lại")

    def _drop_cut(self, k: int):
        """Bỏ 1 đoạn cắt (nút × ở danh sách đoạn cắt)."""
        cuts = list(self.p.edit.get("cuts") or []) if self.p else []
        if 0 <= k < len(cuts):
            a, b = cuts.pop(k)
            self._set_cuts(cuts, f"Đã bỏ đoạn cắt {fmt(a)} → {fmt(b)} – bấm ↶ trên cùng (hoặc Ctrl+Z) để lấy lại")

    def _set_cuts(self, cuts: list, msg: str):
        self.flush()
        self.p.reload()
        e = self.p.edit
        e["cuts"] = cuts
        self.p.data["edit"] = e
        self.p.save()
        self._undo_mark()
        self._cuts_changed()
        self.win.toast(msg, "ok")

    def _cuts_changed(self):
        """Đoạn cắt vừa đổi: danh sách, 2 thanh thời gian, tóm tắt và trình phát (bỏ qua / phát lại đoạn đó NGAY –
        trước đây "Bỏ hết đoạn cắt" xong trình phát vẫn nhảy qua chỗ cũ tới khi đổi ô khác)."""
        self._update_cuts_label()
        self._update_timeline()
        self._update_summary()
        self._apply_live()

    def _play_cut(self, k: int):
        cuts = (self.p.edit.get("cuts") or []) if self.p else []
        if 0 <= k < len(cuts):
            self._play_junction({"cut": [float(cuts[k][0]), float(cuts[k][1])]})

    # ================================================================== TAB: Chữ & Logo
    def _tab_layers(self):
        from .layers_tab import LayersTab

        self.layers_tab = LayersTab(self)
        self.layers_tab.changed.connect(self._layers_changed)
        self.layers_tab.selected.connect(lambda lid: self._layer_selected(lid))
        v = self.player.view
        v.on_layer_select = lambda lid: self._layer_selected(lid, from_video=True)
        v.on_layer_changed = self._layer_moved
        v.on_drop = lambda data, x, y: self.layers_tab.drop(data, x, y)
        return scroll(self.layers_tab)

    @property
    def _layers(self) -> list:
        return self.layers_tab.layers if hasattr(self, "layers_tab") else []

    def duration(self) -> float:
        return float((self.p.data.get("info") or {}).get("duration") or 0) if self.p else 0.0

    def _layers_changed(self):
        if self.loading or not self.p:
            return
        self._save_edit_timer.start()
        self._live_timer.start()
        self.tracks.set_layers(self._layers)
        self._undo_mark(join=True)

    def _layer_selected(self, lid: str, from_tl: bool = False, from_video: bool = False):
        self.player.view.select_layer(lid)
        if not from_tl:
            self.tracks.select_layer(lid)
        if self.layers_tab.sel != lid:
            self.layers_tab.select(lid)
        if lid and (from_tl or from_video) and self.stack.currentIndex() != self.TAB_LAYERS:
            self.tabs.set_current(self.TAB_LAYERS)

    def _layer_moved(self, lid: str, ch: dict):
        """Kéo / phóng 1 lớp trên video. Đang xem thử tỉ lệ xuất thêm -> chỉ chỉnh riêng cho tỉ lệ đó."""
        if self._ratio_view:
            self._ratio_over.setdefault(self._ratio_view, {}).setdefault("layers", {}).setdefault(lid, {}).update(ch)
            self._save_edit_timer.start()
            self._live_timer.start()
            return
        self.layers_tab.update_layer(lid, ch)

    def _layer_action(self, lid: str, act: str):
        from .. import overlay

        t = self.layers_tab
        L = next((x for x in t.layers if x.get("id") == lid), None)
        if not L:
            return
        t.select(lid)
        dur, pos = self.duration(), self.player.position()
        a, b = overlay.src_span(L, dur)
        if act == "full":
            t.full_span(lid)
        elif act == "from":
            t.set_span(lid, pos, b if b > pos + 0.2 else min(dur or pos + 5, pos + 5))
        elif act == "to":
            t.set_span(lid, a if a < pos - 0.2 else max(0.0, pos - 5), pos)
        elif act == "dup":
            t.duplicate()
        elif act in ("up", "down"):
            t.move(+1 if act == "up" else -1)
        elif act == "hide":
            t.toggle_hidden()
        elif act == "delete":
            t.delete(lid)

    # ================================================================== khung hình: tỉ lệ, phần trống, vị trí phim
    FILM_POS = {"c": (0.5, 0.5), "up": (0.5, 0.15), "down": (0.5, 0.85), "left": (0.15, 0.5), "right": (0.85, 0.5)}

    def _frame_widgets(self) -> dict:
        x, y = self.e_aposx.value() / 100, self.e_aposy.value() / 100
        return {"aspect_mode": self.e_amode.currentData(), "aspect_color": self.e_acolor.color,
                "aspect_pos": [round(x, 3), round(y, 3)], "aspect_scale": round(self.e_ascale.value() / 100, 3)}

    def _frame_edit(self, e: dict) -> dict:
        """Các khoá khung hình của bản chỉnh sửa. Đang xem thử 1 tỉ lệ xuất thêm: ô Phần trống / Vị trí / Cỡ phim là
        của tỉ lệ đó (ghi vào ratio_over), bản chính giữ nguyên như đã lưu."""
        fr = self._frame_widgets()
        ro = self._ratio_over
        if self._ratio_view:
            ro.setdefault(self._ratio_view, {}).update(fr)
            main = {k: e.get(k) for k in fr}
        else:
            main = fr
        return {"aspect": self.e_aspect.value, **main, "extra_ratios": list(self.x_extra.values),
                "ratio_over": ro, "safe_zone": self.e_safe.isChecked()}

    def _set_frame_widgets(self, e: dict):
        from ..video import aspect_pos, edit_for_ratio

        src = edit_for_ratio({**e, "ratio_over": self._ratio_over}, self._ratio_view) if self._ratio_view else e
        self.e_aspect.set_value(e.get("aspect", "original"))
        self.x_aspect.set_value(e.get("aspect", "original"))
        for cb in (self.e_amode, self.x_amode, self.e_apos):
            cb.blockSignals(True)
        set_combo(self.e_amode, src.get("aspect_mode", "blur"))
        set_combo(self.x_amode, src.get("aspect_mode", "blur"))
        x, y = aspect_pos(src)
        set_combo(self.e_apos, next((k for k, v in self.FILM_POS.items() if abs(v[0] - x) < 1e-3 and
                                     abs(v[1] - y) < 1e-3), "custom"))
        for cb in (self.e_amode, self.x_amode, self.e_apos):
            cb.blockSignals(False)
        self.e_acolor.set(src.get("aspect_color") or "#000000")
        self.e_aposx.set(x * 100)
        self.e_aposy.set(y * 100)
        self.e_ascale.set(float(src.get("aspect_scale") or 1.0) * 100)
        self.e_safe.blockSignals(True)
        self.e_safe.setChecked(bool(e.get("safe_zone")))
        self.e_safe.blockSignals(False)
        self._frame_rows()

    def _frame_rows(self):
        on = self.e_aspect.value != "original" or bool(self._ratio_view)
        mode = self.e_amode.currentData()
        f = self.frame_form
        for w in (self.e_amode, self.e_apos, self.e_ascale, self.e_safe):
            f.setRowVisible(w, on)
        f.setRowVisible(self.e_acolor, on and mode == "color")
        custom = self.e_apos.currentData() == "custom"
        f.setRowVisible(self.e_aposx, on and custom)
        f.setRowVisible(self.e_aposy, on and custom)
        self.x_amode.setEnabled(on)

    def _frame_live(self):
        self._frame_rows()
        if not self.loading:
            self._save_edit_timer.start()
            self._live_timer.start()

    def _aspect_changed(self, r: str):
        """Chọn tỉ lệ ở tab Chỉnh sửa hoặc tab Xuất: cả 2 bộ chọn cùng đổi (1 giá trị chung)."""
        self.e_aspect.set_value(r)
        self.x_aspect.set_value(r)
        self.x_extra.set_values(self.x_extra.values, r)
        if self._ratio_view:
            self._ratio_preview("")
        self._frame_live()
        self._update_safe()

    def _amode_changed(self, m: str):
        for cb in (self.e_amode, self.x_amode):
            if cb.currentData() != m:
                cb.blockSignals(True)
                set_combo(cb, m)
                cb.blockSignals(False)
        self._frame_live()

    def _film_pos_preset(self, *_):
        code = self.e_apos.currentData()
        if code in self.FILM_POS:
            x, y = self.FILM_POS[code]
            self.e_aposx.set(x * 100)
            self.e_aposy.set(y * 100)
        self._frame_live()

    def _film_pos_slider(self, *_):
        if self.e_apos.currentData() != "custom":
            self.e_apos.blockSignals(True)
            set_combo(self.e_apos, "custom")
            self.e_apos.blockSignals(False)
        self._frame_live()

    def _ratio_preview(self, r: str):
        """👁 Xem thử 1 tỉ lệ xuất thêm trên trình phát ("" = về tỉ lệ chính)."""
        if not self.p:
            return
        self.flush()
        self._ratio_view = r or ""
        self.x_extra.set_viewing(self._ratio_view)
        self.loading = True
        try:
            self._set_frame_widgets(self.p.edit)
        finally:
            self.loading = False
        if r:
            extra = r in self.x_extra.values
            msg = (f"👁 Đang xem thử khung {r}: kéo chữ / logo / phụ đề, đổi Phần trống / Vị trí phim = chỉnh riêng cho "
                   f"{r}" + ("" if extra else " (chưa tích xuất thêm tỉ lệ này)"))
            self.x_ratio_note.setText(msg)
            self.e_ratio_note.setText(msg)
        self.x_ratio_bar.setVisible(bool(r))
        self.e_ratio_bar.setVisible(bool(r))
        self._apply_live()

    def _update_safe(self):
        on = self.e_safe.isChecked()
        v = self.player.view
        v.set_safe_zone(on)
        if not on:
            self.e_safe_warn.setText("")
            self.e_safe_fix.setVisible(False)
            return
        issues = v.safe_issues()
        self.e_safe_warn.setText(("⚠ Nằm trong vùng bị che: " + ", ".join(issues[:4]) +
                                  (f" và {len(issues) - 4} lớp khác" if len(issues) > 4 else ""))
                                 if issues else "✓ Phụ đề và chữ đều nằm trong vùng an toàn")
        self.e_safe_fix.setVisible(bool(issues))

    def _safe_fix(self):
        """Đưa phụ đề + các lớp chữ / logo nằm trong vùng bị che vào vùng an toàn."""
        from ..video import SAFE_ZONE

        top, bot, right = SAFE_ZONE["top"], SAFE_ZONE["bottom"], SAFE_ZONE["right"]
        v = self.player.view
        if "phụ đề" in v.safe_issues():
            d = {"position": "custom", "pos_x": 0.5, "pos_y": round(1 - bot - 0.015, 4)}
            if self._ratio_view:
                self._ratio_over.setdefault(self._ratio_view, {}).setdefault("sub", {}).update(
                    {**d, "line_width": min(float(self.current_style().get("line_width") or 0.9), 1 - 2 * right)})
            else:
                set_combo(self.st_pos, "custom")
                self.st_posx.set(50)
                self.st_posy.set(d["pos_y"] * 100)
                self.st_lwidth.set(min(self.st_lwidth.value(), (1 - 2 * right) * 100))
                self._style_rows()
        fr = v.canvas.rect()
        zones = v.safe.zones()
        for lid, it in list(v.layers.items()):
            r = it._rect()
            if not it.isVisible() or not any(z.intersects(r) for z in zones):
                continue
            w, h = r.width() / fr.width(), r.height() / fr.height()
            x = min(max(w / 2, (r.center().x() - fr.left()) / fr.width()), 1 - right - w / 2)
            y = min(max(top + h / 2, (r.center().y() - fr.top()) / fr.height()), 1 - bot - h / 2)
            # tâm lớp (x, y) lệch so với tâm ảnh (bóng đổ, nét cọ) -> dời đúng bằng độ lệch của tâm ảnh
            L = it.layer
            dx = x - (r.center().x() - fr.left()) / fr.width()
            dy = y - (r.center().y() - fr.top()) / fr.height()
            self._layer_moved(lid, {"x": round(float(L.get("x", 0.5)) + dx, 4),
                                    "y": round(float(L.get("y", 0.5)) + dy, 4)})
        self._save_edit_timer.start()
        self._live_timer.start()
        self.win.toast("Đã đưa phụ đề / chữ vào vùng an toàn", "ok")

    # ================================================================== rút khoảng lặng / cắt bài hát
    def _silence_items(self, items: list):
        self.tracks.set_pending(items)
        self._live_timer.start()     # "Phát thử bản đã cắt": trình phát bỏ qua các đoạn đã tích

    def _play_junction(self, it: dict):
        """▶ Xem chỗ nối: phát từ 2 s trước đoạn cắt, nhảy qua đoạn cắt, dừng 2 s sau."""
        a, b = it["cut"]
        self._junction_cut = [a, b]
        self._apply_live()
        self.player.seek(max(0.0, a - 2.0))
        self._stop_at = b + 2.0
        if not self.player.playing():
            self.player.toggle()

    def _apply_silence(self, items: list):
        from .. import silence

        if not self.p or not items:
            return
        self.flush()
        self.p.reload()
        e = self.p.edit
        before = sum(b - a for a, b in __import__("trami.video", fromlist=["keep_ranges"]).keep_ranges(
            self.duration(), e.get("trim"), e.get("cuts")))
        e["cuts"] = silence.new_cuts(e.get("cuts"), items)
        self.p.data["edit"] = e
        self.p.save()
        self._undo_mark()
        after = sum(b - a for a, b in __import__("trami.video", fromlist=["keep_ranges"]).keep_ranges(
            self.duration(), e.get("trim"), e.get("cuts")))
        self.sil.clear()
        self._update_cuts_label()
        self._update_timeline()
        self._update_summary()
        self._apply_live()
        self.win.toast(f"Đã cắt {len(items)} đoạn, bớt {fmt(before - after, False)} – bấm ↶ trên cùng (hoặc Ctrl+Z) để "
                       "hoàn tác; lấy lại 1 đoạn: bấm Bỏ ở danh sách đoạn cắt (thẻ Cắt video)", "ok")

    CUT_ROWS = 30    # hiện tối đa 30 đoạn cắt có nút (Rút khoảng lặng có thể tạo hàng trăm đoạn)

    def _update_cuts_label(self):
        cuts = self.p.edit.get("cuts", []) if self.p else []
        lay = self.cuts_rows_l
        while lay.count():
            w = lay.takeAt(0).widget()
            if w is not None:
                w.hide()
                w.setParent(None)
                w.deleteLater()
        if not cuts:
            self.cuts_list.setText("Chưa có đoạn cắt nào")
            return
        total = sum(float(b) - float(a) for a, b in cuts)
        self.cuts_list.setText(f"{len(cuts)} đoạn cắt, bỏ {fmt(total, False)}" +
                               (f" – hiện {self.CUT_ROWS} đoạn đầu" if len(cuts) > self.CUT_ROWS else ""))
        for k, (a, b) in enumerate(cuts[:self.CUT_ROWS]):
            lay.addWidget(wrap(hbox(label(f"✂ Xoá {fmt(a)} → {fmt(b)}  ({float(b) - float(a):.1f} s)", "muted"),
                               button("Xem chỗ nối", "play", "Phát từ 2 giây trước đoạn cắt, nhảy qua đoạn cắt, dừng 2 giây sau",
                                      flat=True, on_click=lambda _=False, k=k: self._play_cut(k)),
                               button("Bỏ", "x", "Bỏ đoạn cắt này (giữ lại phần video đó)", flat=True,
                                      on_click=lambda _=False, k=k: self._drop_cut(k)), None)))

    def _update_timeline(self):
        if not self.p:
            return
        e = self.p.edit
        d = self.p.data.get("info", {}).get("duration", 0)
        clips = pipeline.dub_clips(self.p, self.model.segs)   # theo từng câu: đúng cả khi vừa thêm / xoá câu
        self.timeline.dub = clips if (e.get("dub") or {}).get("enabled") else []
        keep = (e["trim"].get("start") or 0, e["trim"].get("end") or 0)
        self.timeline.set_data(d, keep, e.get("cuts", []), self.model.segs)
        self.tracks.mark = self.timeline.mark
        self.tracks.show_est = bool(self.p.data.get("snap_stats"))
        shown = {}
        try:   # giờ hiện phụ đề khi khác giờ nói (theo giọng lồng) -> vạch trắng dưới khối phụ đề
            fs = pipeline.subs_follow_dub(self.p, self.model.segs, e, self.settings, "trans")
            if fs is not self.model.segs:
                shown = {i: (b.start, b.end) for i, (a, b) in enumerate(zip(self.model.segs, fs))
                         if abs(a.start - b.start) > 0.03 or abs(a.end - b.end) > 0.03}
        except Exception:  # noqa: BLE001 – chỉ để vẽ
            pass
        self.tracks.shown = shown
        self.tracks.set_data(d, self.model.segs, clips, keep, e.get("cuts", []),
                             e.get("vsplits", []), e.get("inserts", []), e.get("mute_spans", []))
        self.tracks.set_layers(self._layers)

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
        self.audio_tab.bgm_name.setText(Path(path).name)
        self._update_summary()
        if kind == "bgm":
            self._update_player_audio(force=True)

    def _clear_asset(self, kind: str):
        self.p.reload()
        e = self.p.edit
        e[kind]["path"] = ""
        self.p.data["edit"] = e
        self.p.save()
        self.audio_tab.bgm_name.setText("Chưa chọn")
        if kind == "bgm":
            self._update_player_audio(force=True)

    def _style_preset(self, name: str):
        presets = {
            # giống phụ đề HaizFlow: Bangers, viền đen dày ~9 % cỡ chữ, từng từ trắng -> vàng theo lời đọc
            "karaoke": dict(font="Bangers", color="#FFFFFF", karaoke=True, karaoke_color=KARA_COLOR,
                            karaoke_mode="sweep", outline_color="#000000", outline=4.5, shadow=1.5, box=False,
                            bold=False, uppercase=False),
            "classic": dict(color="#FFFFFF", outline_color="#000000", outline=2.5, box=False, bold=True,
                            karaoke=False),
            "yellow": dict(color="#FFE14D", outline_color="#000000", outline=3, box=False, bold=True, karaoke=False),
            "box": dict(color="#FFFFFF", box=True, box_color="#000000", box_opacity=0.6, bold=True, karaoke=False),
            "tiktok": dict(color="#FFFFFF", outline_color="#000000", outline=4, box=False, bold=True, size=0,
                           position="middle", uppercase=False, karaoke=False),
        }[name]
        st = self.current_style()
        st.update(presets)
        if name != "karaoke" and st.get("font") == "Bangers":
            st["font"] = "Be Vietnam Pro"
        self.loading = True
        self._set_style_widgets(st)
        self.loading = False
        self._save_edit_now()
        self._refresh_overlay()

    # ================================================================== lồng tiếng
    def _fill_speakers(self):
        from .. import tts

        p = self.p
        spks = pipeline.speaker_list(p)
        voices = tts.list_voices(self.settings, pipeline.target_lang(p, self.settings))
        self._voices = voices
        self.model.voice_names = {v["id"]: v["name"] for v in voices}
        cur = self.d_line_voice.currentData()
        self.d_line_voice.clear()
        self.d_line_voice.addItem("(Theo người nói)", "")
        _add_voices(self.d_line_voice, voices)
        set_combo(self.d_line_voice, cur or "")
        self._fill_auto_voices(voices)
        self.inspector.refresh_voices()
        self.spk_table.setRowCount(len(spks))
        self.spk_table.setFixedHeight(36 + 44 * max(1, min(len(spks), 6)))
        gend = p.data.get("speaker_gender") or {}
        for r, s in enumerate(spks):
            gi = gend.get(s["speaker"]) or {}
            tag = {"male": " · nam", "female": " · nữ"}.get(gi.get("g", ""), "")
            it = QTableWidgetItem(s["speaker"] + tag)
            if gi:
                it.setToolTip(f"Chất giọng: {('nam' if gi.get('g') == 'male' else 'nữ' if gi.get('g') == 'female' else 'chưa rõ')}"
                              + (f" · cao độ ~{gi['f0']:.0f} Hz" if gi.get("f0") else "")
                              + (" · theo mô hình + cao độ" if gi.get("by") == "model+pitch" else " · theo cao độ giọng"
                                 if gi.get("by") == "pitch" else ""))
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
            _add_voices(cb, voices)
            set_combo(cb, s["voice"])
            cb.currentIndexChanged.connect(lambda _i, spk=s["speaker"], c=cb: self._set_voice(spk, c.currentData()))
            self.spk_table.setCellWidget(r, 2, cb)
            b = IconButton("play", "Nghe thử giọng này", 32)
            b.clicked.connect(lambda _=False, c=cb, spk=s["speaker"]: self._preview_voice(c.currentData(), spk))
            self.spk_table.setCellWidget(r, 3, wrap(hbox(b)))
        self._update_voice_note()

    def _fill_auto_voices(self, voices: list[dict]):
        """Ô "Giọng lồng" ở tab Tự động = danh sách giọng của mục 2 (+ tự gán nam / nữ)."""
        cur = pipeline.voice_all(self.p, self.settings) if self.p else (self.a_voice.currentData() or "")
        was, self.loading = self.loading, True
        self.a_voice.clear()
        self.a_voice.addItem("(Theo giọng từng người nói – mục 2)", "")
        self.a_voice.addItem("🎲 Tự gán giọng nam / nữ theo từng người nói", "@auto")
        _add_voices(self.a_voice, voices)
        set_combo(self.a_voice, cur)
        self.loading = was

    def _voice_all_changed(self, *_):
        """Chọn 1 giọng cho tất cả người nói (tab Tự động / mục 2): áp ngay cho dự án (mọi người nói, bỏ giọng riêng
        từng câu) và nhớ làm mặc định cho video sau."""
        if self.loading or not self.p:
            return
        v = self.a_voice.currentData() or ""
        self._save_setting("dub_voice_all", v)
        self.p.reload()
        self.p.data["voice_all"] = v
        self.p.save()
        if v == "@auto":
            return self._auto_voices()
        if not v:
            return self._update_voice_note()
        self.p.data["voices"] = {x["speaker"]: v for x in pipeline.speaker_list(self.p)}
        self.p.save()
        own = [r for r, x in enumerate(self.model.segs) if x.extra.get("voice")]
        if self.model.set_voices(own, ""):   # giọng riêng từng câu đè lên giọng chung -> bỏ
            self._save_segs_now()
        self.loading = True
        try:
            self._fill_speakers()
        finally:
            self.loading = False
        self.win.toast(f"Mọi người nói dùng giọng {self.a_voice.currentText()}"
                       + (f" (bỏ giọng riêng của {len(own)} câu)" if own else "")
                       + " – bấm Tạo bản lồng tiếng để đọc lại", "ok")

    def _preview_auto_voice(self):
        vid = self.a_voice.currentData() or ""
        if vid == "@auto":
            return self.win.toast("Tự gán: mỗi nhân vật 1 giọng nam / nữ – nghe thử từng giọng ở tab Lồng tiếng mục 2",
                                  "warn")
        spk = next((x["speaker"] for x in pipeline.speaker_list(self.p)), "S1") if self.p else "S1"
        self._preview_voice(vid or (self.p.data.get("voices") or {}).get(spk, ""), spk)

    def _set_voice(self, spk: str, vid: str):
        if self.loading:
            return
        self.p.reload()
        self.p.data.setdefault("voices", {})[spk] = vid
        if self.p.data.get("voice_all") or pipeline.voice_all(self.p, self.settings):
            self.p.data["voice_all"] = ""   # chọn giọng từng người -> dự án này thôi dùng 1 giọng chung
            was, self.loading = self.loading, True
            set_combo(self.a_voice, "")
            self.loading = was
        self.p.save()
        # câu của người này đang có giọng riêng (Đổi giọng câu đã tích / khung Câu đang chọn) -> giọng riêng đè lên giọng
        # vừa chọn, đọc lại bao nhiêu lần cũng ra giọng cũ -> hỏi có dùng giọng mới cho cả các câu đó không
        own = [r for r, x in enumerate(self.model.segs) if (x.speaker or "S1") == spk and x.extra.get("voice")]
        if own and QMessageBox.question(
                self, "Câu có giọng riêng",
                f"{len(own)} câu của {spk} đang có giọng riêng (đặt ở cột Giọng / 'Đổi giọng' câu đã tích) nên KHÔNG đổi "
                f"theo giọng vừa chọn.\n\nDùng giọng mới cho cả {len(own)} câu này?") == QMessageBox.StandardButton.Yes:
            self.model.set_voices(own, "")
            self._save_segs_now()
        self._update_voice_note()

    def _update_voice_note(self):
        """Mục 3 ghi rõ giọng sẽ dùng: bộ đọc + giọng từng người nói (chọn ở mục 2)."""
        parts = []
        for r in range(self.spk_table.rowCount()):
            it, cb = self.spk_table.item(r, 0), self.spk_table.cellWidget(r, 2)
            if it and isinstance(cb, QComboBox):
                parts.append(f"{it.text().split(' ·')[0]}: {cb.currentText()}")
        own = sum(1 for x in self.model.segs if x.extra.get("voice"))
        n_spk = self.spk_table.rowCount()
        defaults = sum(1 for r in range(n_spk) if isinstance(self.spk_table.cellWidget(r, 2), QComboBox)
                       and not self.spk_table.cellWidget(r, 2).currentData())
        hint = ""
        va = pipeline.voice_all(self.p, self.settings) if self.p else ""
        if va and va != "@auto":
            hint = f" · mọi người nói dùng 1 giọng: {self.a_voice.currentText()}"
        elif n_spk == 1 and len(self.model.segs) > 1:
            hint = (" · ⚠ chỉ có 1 người nói (S1) nên cả phim 1 giọng – muốn mỗi nhân vật 1 giọng: Cài đặt → Số người nói "
                    "đặt 0 (tự đoán, không để 1), rồi bấm 'Phân biệt người nói' ở mục 2; hoặc tích câu rồi đổi giọng riêng")
        elif n_spk > 1 and defaults == n_spk:
            hint = " · chưa chọn giọng: khi lồng tiếng tool tự gán mỗi người nói 1 giọng khác nhau"
        self.d_voice_note.setText(f"Giọng đọc – bộ đọc: {self.d_engine.currentText().split(' (')[0]}" +
                                  (" · " + ", ".join(parts[:6]) + (" …" if len(parts) > 6 else "") if parts else "") +
                                  (f" · {own} câu có giọng riêng (đè lên giọng người nói)" if own else "") + hint)

    def _goto_voices(self):
        from PySide6.QtWidgets import QScrollArea

        w = self.spk_table.parentWidget()
        while w is not None and not isinstance(w, QScrollArea):
            w = w.parentWidget()
        if w is not None:
            w.ensureWidgetVisible(self.spk_table, 0, 60)
        self.spk_table.setFocus()

    def _restore_interj(self):
        if not self.p or not (self.p.data.get("interj_removed")
                              or any(x.extra.get("trans_before_interj") for x in self.model.segs)):
            return self.win.toast("Không có câu cảm thán nào bị xoá / bỏ bớt", "info")
        self.flush()
        trimmed = {(round(x.start, 3), x.text) for x in self.p.segments if x.extra.get("trans_before_interj")}
        n = pipeline.restore_interjections(self.p)
        self.reload()
        back = [i for i, x in enumerate(self.model.segs) if (round(x.start, 3), x.text) in trimmed]
        if back and pipeline.has_dub(self.p):   # câu dài được trả lại vế cảm thán: đọc lại giọng đúng các câu đó
            self._redub_rows(back, play=False)
        self.win.toast(f"Đã khôi phục {n} câu – lần lồng tiếng sau các câu này được giữ (câu bị xoá: bấm 'Tích câu chưa "
                       "lồng' rồi 'Tạo lại giọng các câu đã tích' để đọc ngay)", "ok")

    def _auto_voices(self):
        """Gán giọng khác nhau cho từng người nói, đúng nam / nữ theo chất giọng (đoán trên CPU vài giây nếu chưa có)."""
        from .. import tts

        pid = self.p.id
        spks = [s["speaker"] for s in pipeline.speaker_list(self.p)]
        known = self.p.data.get("speaker_gender") or {}

        def apply(genders: dict):
            if not self.p or self.p.id != pid:
                return
            self.p.reload()
            st = dict(self.settings)
            st["tts_engine"] = self.d_engine.currentData()
            self.p.data["voices"] = tts.auto_assign(spks, st, {}, genders, pipeline.target_lang(self.p, st))
            self.p.save()
            self.loading = True
            self._fill_speakers()
            self.loading = False
            n = sum(1 for k in spks if (genders.get(k) or {}).get("g"))
            self.win.toast(f"Đã gán giọng cho {len(spks)} người nói" +
                           (f" ({n} người đoán được giọng nam / nữ)" if n else ""), "ok")

        if all(k in known for k in spks) or not self.p.data.get("source"):
            return apply(known)
        self.win.toast("Đang nghe giọng từng người nói để chọn giọng nam / nữ…")
        run_async(lambda: pipeline.speaker_genders(Project(pid)), apply,
                  lambda m: (self.win.toast("Không đoán được giọng nam / nữ: " + m, "warn"), apply(known)))

    def _dub_engine_changed(self):
        self._update_voice_note()
        if self.a_tts.currentData() != self.d_engine.currentData():
            was, self.loading = self.loading, True
            set_combo(self.a_tts, self.d_engine.currentData())
            self.loading = was
        if not self.loading:
            self._save_edit_timer.start()

    # ------------------------------------------------------------------ cách xếp giọng khi câu dài
    def _update_overflow_help(self):
        m = self.d_overflow.currentData() or "smart"
        self.d_overflow_help.setText(self._overflow_help.get(m, ""))
        for w in (self.d_lead, self.d_maxlag):
            w.setEnabled(m == "smart")

    def _fill_dub_layout(self):
        """Ô cách xếp giọng theo cài đặt hiện tại (có thể đã đổi ở trang Cài đặt)."""
        st = self.settings
        set_combo(self.d_overflow, st.get("dub_overflow", "smart"))
        self.d_maxspeed.setValue(float(st.get("tts_max_speedup", 1.3)))
        self.d_lead.setValue(float(st.get("dub_lead_max", 1.0)))
        self.d_maxlag.setValue(float(st.get("dub_max_lag", 2.0)))
        self.d_follow.setChecked(bool(st.get("dub_subs_follow", True)))
        self.d_pauses.setChecked(bool(st.get("dub_pauses", True)))
        self.d_pause_min.setValue(float(st.get("dub_pause_min", 0.3)))
        self.d_by_clause.setChecked(bool(st.get("dub_by_clause", True)))
        self.d_autoshort.setChecked(bool(st.get("dub_auto_shorten", True)))
        self._update_overflow_help()

    def _dub_layout_changed(self, *_):
        """Đổi cách xếp / tăng tốc tối đa / đọc sớm / trễ tối đa -> lưu + xếp lại bản lồng đang dùng (không đọc lại)."""
        self._update_overflow_help()
        if self.loading:
            return
        from ..config import save_settings

        save_settings({"dub_overflow": self.d_overflow.currentData() or "smart",
                       "tts_max_speedup": round(self.d_maxspeed.value(), 2),
                       "dub_lead_max": round(self.d_lead.value(), 2), "dub_max_lag": round(self.d_maxlag.value(), 2),
                       "dub_pauses": self.d_pauses.isChecked(), "dub_pause_min": round(self.d_pause_min.value(), 2),
                       "dub_by_clause": self.d_by_clause.isChecked()})
        self.settings = load_settings()
        if self.p and any(x.extra.get("tts_file") for x in self.model.segs) and (self.p.edit.get("dub") or {}).get("wav"):
            self._relayout_timer.start()

    def _dub_follow_changed(self, on: bool):
        if self.loading:
            return
        if self.s_follow.isChecked() != on:   # công tắc gương ở tab Phụ đề -> lưu qua _follow_opts_changed
            self.s_follow.setChecked(on)
            return
        self._follow_opts_changed()

    def _follow_opts_changed(self, *_):
        """Giờ hiện phụ đề (tab Phụ đề → Công cụ → Giờ hiện): lưu Cài đặt, vẽ lại phụ đề xem trước + vạch giờ hiện."""
        self._follow_enable()
        if self.loading:
            return
        if self.d_follow.isChecked() != self.s_follow.isChecked():
            self.loading = True
            try:
                self.d_follow.setChecked(self.s_follow.isChecked())
            finally:
                self.loading = False
        from ..config import save_settings

        save_settings({"dub_subs_follow": self.s_follow.isChecked(),
                       "sub_follow_mode": self.s_follow_mode.currentData() or "always",
                       "sub_lead": round(self.s_lead.value(), 2), "sub_hold": round(self.s_hold.value(), 2),
                       "sub_min_dur": round(self.s_min.value(), 2), "sub_join_gap": round(self.s_join.value(), 2)})
        self.settings = load_settings()
        if self.p:
            self._refresh_overlay()
            self._update_timeline()

    def _follow_enable(self):
        always = self.s_follow_mode.currentData() == "always"
        for w in (self.s_lead, self.s_hold, self.s_min, self.s_join):
            w.setEnabled(always and self.s_follow.isChecked())
        self.s_follow_mode.setEnabled(self.s_follow.isChecked())

    def _follow_defaults(self):
        self.loading = True
        try:
            set_combo(self.s_follow_mode, "always")
            for w, v in ((self.s_lead, 0.1), (self.s_hold, 0.3), (self.s_min, 1.0), (self.s_join, 0.3)):
                w.setValue(v)
        finally:
            self.loading = False
        self._follow_opts_changed()

    def _sync_follow(self, st: dict):
        """Ô giờ hiện phụ đề theo Cài đặt (mở dự án / lưu Cài đặt)."""
        was, self.loading = self.loading, True
        try:
            self.s_follow.setChecked(bool(st.get("dub_subs_follow", True)))
            self.d_follow.setChecked(bool(st.get("dub_subs_follow", True)))
            set_combo(self.s_follow_mode, st.get("sub_follow_mode", "always"))
            self.s_lead.setValue(float(st.get("sub_lead", 0.1)))
            self.s_hold.setValue(float(st.get("sub_hold", 0.3)))
            self.s_min.setValue(float(st.get("sub_min_dur", 1.0)))
            self.s_join.setValue(float(st.get("sub_join_gap", 0.3)))
        finally:
            self.loading = was
        self._follow_enable()

    def _dub_autoshort_changed(self, on: bool):
        if not self.loading:
            from ..config import save_settings

            save_settings({"dub_auto_shorten": on})

    def _relayout(self, what: str, replay_row: int | None = None, deleted: int = 0):
        """Xếp lại bản lồng tiếng đang dùng theo cài đặt / cách xếp / câu mới (không đọc lại câu nào; chỉ ghi lại đoạn
        thời gian có thay đổi – ~1 giây). Nhiều thao tác liền nhau (xoá từng câu, kéo…) gộp thành 1 lần."""
        if not self.p:
            return
        self._replay_row = replay_row
        self._relayout_deleted = getattr(self, "_relayout_deleted", 0) + deleted
        if what and what not in self._relayout_whats:
            self._relayout_whats.append(what)
        self._relayout_go.start()

    def _relayout_now(self):
        n_del, self._relayout_deleted = getattr(self, "_relayout_deleted", 0), 0
        whats, self._relayout_whats = self._relayout_whats, []
        if n_del:
            whats.insert(0, f"Đã xoá {n_del} câu và giọng lồng của chúng")
        if not self.p or not whats:
            return
        what = "; ".join(whats[-3:]) + (f" (+{len(whats) - 3} thao tác)" if len(whats) > 3 else "")
        if any(j["kind"] == "relayout" and j["status"] == "queued" for j in manager.list(self.p.id)):
            return self._save_segs_now()   # lần xếp lại đang chờ sẽ đọc dự án mới nhất khi chạy

        def fn(j, p):
            pipeline.relayout(p, None, j.update, j.cancelled)
            sts = p.data.get("dub_stats") or {}
            return {"message": what + " – " + pipeline.dub_summary(p) if sts else what}
        self.submit("relayout", "Xếp lại giọng lồng", fn, quiet=True)

    def _apply_overflow(self, rows: list[int], mode: str | None):
        """Cách xếp riêng cho các câu (None = theo cài đặt chung) -> xếp lại bản lồng ngay."""
        from .subs_model import OVERFLOW_SHORT

        segs = self.model.segs
        rows = [r for r in rows if 0 <= r < len(segs)]
        if not rows:
            return self.win.toast("Tích chọn câu trước (cột Chọn, hoặc chọn khối trên dòng Lồng tiếng của timeline)", "warn")
        changed = 0
        for r in rows:
            ex = segs[r].extra
            old = ex.get("overflow")
            if mode:
                ex["overflow"] = mode
            else:
                ex.pop("overflow", None)
            changed += old != ex.get("overflow")
        self.model.dataChanged.emit(self.model.index(rows[0], 0), self.model.index(rows[-1], self.model.columnCount() - 1))
        self._save_segs_now()
        what = f"'{OVERFLOW_SHORT.get(mode, mode)}'" if mode else "cách xếp chung"
        if not changed:
            return self.win.toast(f"{len(rows)} câu đã dùng {what}", "info")
        if not any(x.extra.get("tts_file") for x in segs):
            return self.win.toast(f"Đã đặt {what} cho {len(rows)} câu – áp dụng khi bấm Tạo bản lồng tiếng", "ok")
        self._relayout(f"Đã đặt {what} cho {len(rows)} câu", rows[0])

    def _save_setting(self, key: str, value):
        from ..config import save_settings

        save_settings({key: value})
        self.settings[key] = value

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
        if not vid.startswith("edge:"):
            # giọng AI (VieNeu / OmniVoice) nạp mô hình lên card đồ hoạ: đang có việc GPU khác (nghe, tách giọng, xoá phụ
            # đề…) thì đợi xong – chạy chồng làm cả 2 tràn bộ nhớ card, chậm hẳn. Đang lồng tiếng thì dùng chung tiến trình
            busy = manager.gpu_busy({"dub", "redub"})
            if busy:
                return self.win.toast(f"Đang chạy '{busy}' trên card đồ hoạ – nghe thử giọng AI sau khi việc đó xong "
                                      "(giọng Edge nghe thử được ngay)", "warn")
        self.win.toast("Đang tạo giọng nghe thử…")
        lang = pipeline.target_lang(self.p, self.settings)
        run_async(lambda: tts.preview_voice(vid, text, out, self.settings, ref, lang),
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
        """Tích các câu cần rút gọn: ⚠ quá dài + ⚡ phải đọc nhanh hơn ×1,15 (đúng các câu "AI rút gọn" sẽ làm)."""
        lg = pipeline.trans_lang(self.p)
        rows = [i for i, s in enumerate(self.model.segs) if pipeline.needs_shorten(s, lg)]
        if not rows:
            return self.win.toast("Không có câu nào quá dài / phải đọc quá nhanh (hoặc chưa tạo bản lồng tiếng)", "ok")
        self.model.set_picked(range(len(self.model.segs)), False)
        self.model.set_picked(rows)
        self.win.toast(f"Đã tích {len(rows)} câu quá dài / đọc quá nhanh – bấm 'AI rút gọn' để rút gọn và tạo lại giọng",
                       "ok")

    def _pick_undubbed(self):
        """Tích các câu đã dịch mà chưa có giọng trong bản lồng đang dùng (sót / đọc lỗi / câu mới)."""
        if not pipeline.has_dub(self.p):
            return self.win.toast("Chưa có bản lồng tiếng – bấm 'Tạo bản lồng tiếng' ở mục 3 trước", "warn")
        rows = pipeline.undubbed_rows(self.model.segs, self.d_skipsong.isChecked())
        if not rows:
            return self.win.toast("Mọi câu đã dịch đều đã có giọng lồng", "ok")
        self.model.set_picked(range(len(self.model.segs)), False)
        self.model.set_picked(rows)
        self.win.toast(f"Đã tích {len(rows)} câu chưa có giọng – bấm 'Tạo lại giọng các câu đã tích' để đọc bù", "ok")

    def _detect_songs(self, then=None):
        skip = self.d_skipsong.isChecked()

        def fn(j, p):
            n = pipeline.detect_songs(p, None, j.update, j.cancelled)
            if n and pipeline.has_dub(p) and skip:
                pipeline.relayout(p, None, j.update, j.cancelled)   # bỏ giọng lồng của các câu hát khỏi bản đang dùng
            rows = [i for i, x in enumerate(p.segments) if x.extra.get("song_by") == "pitch"]
            return {"message": (f"Tìm được {n} câu đang hát – đã đánh dấu 🎵 và tích sẵn (bấm 'Đặt / bỏ 🎵' nếu câu nào "
                                "là lời thoại)") if n else "Không thấy thêm đoạn hát nào", "pick": rows if n else []}
        job = self.submit("redub" if pipeline.has_dub(self.p) else "join", "Tìm câu hát", fn)
        if then and job is not None:
            self._after_job[job.id] = lambda res: then(len(res.get("pick") or []))

    def _delete_picked(self):
        rows = [r for r in sorted(self.model.selected) if 0 <= r < len(self.model.segs)]
        if not rows:
            return self.win.toast("Tích câu cần xoá ở cột Chọn trước", "warn")
        if QMessageBox.question(self, "Xoá câu", f"Xoá {len(rows)} câu đã tích – cả phụ đề lẫn giọng lồng của các câu "
                                "này?") != QMessageBox.StandardButton.Yes:
            return
        self._sub_op("delete", rows)
        self._save_segs_now()

    def _interj_words_changed(self):
        if self.loading:
            return
        txt = self.d_interj_words.text().strip()
        self._save_setting("interj_words", txt)
        from ..voicefix import interj_list

        n = len(interj_list({"interj_words": txt}))
        self.win.toast(f"Danh sách từ cảm thán: {n} từ / cụm" if n else "Danh sách trống – không lọc câu cảm thán", "ok")

    def _drop_interj_now(self):
        """Nút "Lọc câu cảm thán" (theo danh sách từ người dùng điền): xoá ngay câu CHỈ gồm từ cảm thán (phụ đề + giọng
        lồng) và bỏ vế cảm thán đứng riêng trong câu dài (đọc lại giọng câu đó nếu đã lồng); cất để khôi phục."""
        from ..voicefix import interj_list, lone_interjection

        segs = self.model.segs
        phrases = interj_list(self.settings)
        if not phrases:
            return self.win.toast("Danh sách 'Từ cảm thán cần lọc' (mục 3) đang trống", "warn")
        rows = [i for i, x in enumerate(segs) if x.trans.strip() and not x.extra.get("keep_interj")
                and lone_interjection(x.trans, phrases)]
        self.flush()
        trimmed = []
        if self.settings.get("interj_inline", True):
            gone = set(rows)
            trimmed = [segs[i] for i in pipeline.trim_interjections(segs, self.settings) if i not in gone]
        if not rows and not trimmed:
            return self.win.toast("Không có câu nào chứa từ cảm thán trong danh sách đứng riêng", "ok")
        self.p.reload()
        if rows:
            self.p.data.setdefault("interj_removed", []).extend(segs[r].to_dict() for r in rows)
            self.p.save()
            self._sub_op("delete", rows)
        else:
            self.model.layoutChanged.emit()
            self._segs_edited()
        self._save_segs_now()
        msg = []
        if rows:
            msg.append(f"xoá {len(rows)} câu chỉ có tiếng cảm thán")
        if trimmed:
            msg.append(f"bỏ vế cảm thán trong {len(trimmed)} câu dài")
            back = [i for i, x in enumerate(self.model.segs) if any(x is t for t in trimmed)]
            if pipeline.has_dub(self.p) and back:
                self._redub_rows(back, play=False)
                msg[-1] += " (đang đọc lại giọng các câu đó)"
        self.win.toast("Đã " + ", ".join(msg) + " – 'Khôi phục câu cảm thán đã xoá' (mục 3) để lấy lại", "ok")

    def _pick_flag(self, key: str):
        if key == "asr_alt":   # chưa chọn được bản đúng trước; không có thì mọi câu 2 bộ nghe khác nhau
            rows = [i for i, s in enumerate(self.model.segs) if s.extra.get("asr_unsure")] or \
                [i for i, s in enumerate(self.model.segs) if s.extra.get("asr_alt") is not None]
        else:
            rows = [i for i, s in enumerate(self.model.segs) if s.extra.get(key)]
        what = {"noise": "nghi nghe nhầm (nhạc / tiếng động)", "asr_alt": "2 bộ nghe ra khác nhau",
                "asr_repeat": "nghi bộ nghe lặp lời"}.get(key, "bài hát 🎵")
        if not rows:
            return self.win.toast(f"Không có câu nào đánh dấu {what}", "ok")
        self.model.set_picked(range(len(self.model.segs)), False)
        self.model.set_picked(rows)
        self._select_line(rows[0], seek=True)
        self.win.toast(f"Đã tích {len(rows)} câu {what}" + (" – rê chuột vào chữ gốc để xem lý do, nghe lại rồi bấm "
                       "Xoá (hoặc 'Không phải nhạc' cho câu thoại thật)" if key == "noise" else ""), "ok")

    def run_clean_asr(self):
        """Dọn câu bộ nghe chép lời gợi ý / lặp lời (2.1.8) – cho phim đã nghe xong, không phải nghe lại cả phim."""
        if not self.model.segs:
            return self.win.toast("Chưa có phụ đề", "warn")
        translated = any(x.trans.strip() for x in self.model.segs)

        def fn(j, p):
            st = pipeline.clean_asr(p, None, j.update, j.cancelled)
            pick = st["changed"] + [i for i, x in enumerate(p.segments) if x.extra.get("asr_repeat")]
            return {"message": pipeline.clean_asr_note(st, translated), "pick": sorted(set(pick))}
        self.submit("dual", "Dọn câu bịa / câu lặp", fn)

    def run_dual(self):
        """Nghe 2 bộ rồi so cho phụ đề đã có (câu đã tích / cả phim)."""
        if not self.model.segs:
            return self.win.toast("Chưa có phụ đề", "warn")
        p = self.p
        primary = "ocr" if p.data.get("sub_source") in ("ocr", "hybrid") else p.data.get("asr_engine_used") or \
            self.settings.get("asr_engine", "qwen3")
        eng, why = pipeline.dual_engine(primary, p.data.get("language", "zh") or "zh", self.settings)
        if not eng:
            return self.win.toast("Chưa nghe 2 bộ được: " + why, "warn")
        rows = sorted(self.model.selected) or None

        def fn(j, p):
            st = pipeline.dual_check(p, None, rows, j.update, j.cancelled)
            pick = [i for i, x in enumerate(p.segments) if x.extra.get("asr_unsure")] + st.get("changed", [])
            msg = pipeline.dual_note(st) or "Đã nghe 2 bộ"
            if st.get("changed") and any(p.segments[i].trans.strip() for i in st["changed"]):
                msg += " · câu đổi chữ gốc đã tích sẵn – bấm 'Dịch câu đã tích' để dịch lại"
            return {"message": msg, "pick": sorted(set(pick))}
        self.submit("dual", "Nghe 2 bộ rồi so", fn)

    def _use_alt(self):
        rows = [r for r in sorted(self.model.selected) if 0 <= r < len(self.model.segs)]
        if not rows:
            return self.win.toast("Tích câu (chữ gốc màu xanh) trước – 'Tích câu 2 bộ nghe khác nhau'", "warn")
        self.flush()
        self._undo_mark()
        done = pipeline.use_alt(self.p, rows)
        self.reload()
        if not done:
            return self.win.toast("Các câu đã tích không có bản khác của bộ nghe 2", "warn")
        self.model.set_picked(range(len(self.model.segs)), False)
        self.model.set_picked(done)
        tr = any(self.model.segs[i].trans.strip() for i in done if i < len(self.model.segs))
        self.win.toast(f"Đã dùng bản bộ nghe 2 cho {len(done)} câu" + (" – bấm 'Dịch câu đã tích' để dịch lại" if tr
                                                                     else ""), "ok")

    def _fix_names(self):
        if not self.model.segs:
            return self.win.toast("Chưa có phụ đề", "warn")
        self.flush()
        self._undo_mark()
        try:
            rows = pipeline.fix_names_now(self.p, self.settings)
        except ValueError as e:
            return self.win.toast(str(e), "warn")
        self.reload()
        if not rows:
            return self.win.toast("Không thấy chỗ nào đọc giống tên riêng mà viết khác", "ok")
        self.model.set_picked(range(len(self.model.segs)), False)
        self.model.set_picked(rows)
        self._select_line(rows[0], seek=False)
        tr = any(self.model.segs[i].trans.strip() for i in rows if i < len(self.model.segs))
        self.win.toast(f"Đã sửa tên riêng ở {len(rows)} câu (rê chuột vào chữ gốc xem chỗ sửa)" +
                       (" – bấm 'Dịch câu đã tích' để dịch lại" if tr else ""), "ok")

    def _ptol_changed(self, v: float):
        if not self.loading:
            from ..config import save_settings

            save_settings({"pitch_tol": round(float(v), 1)})
            self.settings = load_settings()

    def run_voice_check(self):
        """Lọc câu nghe nhầm theo cao độ giọng (cần đã tách giọng / nhạc nền)."""
        if not self.model.segs:
            return self.win.toast("Chưa có phụ đề", "warn")
        if not pipeline.stems(self.p):
            return self.win.toast("Cần tách giọng / nhạc nền trước: tab Lồng tiếng → mục 1 → Tách ngay", "warn")
        tol = self.s_ptol.value()

        def fn(j, p):
            res = pipeline.check_voices(p, None, j.update, j.cancelled, tol=tol)
            n = len(res["suspect"])
            prof = ", ".join(f"{k}: {v}" for k, v in res["profiles"].items())
            msg = (f"⚠ {n} câu nghi là nhạc / tiếng động bị nghe thành lời – đã tích sẵn, nghe lại rồi bấm Xoá"
                   if n else "Không thấy câu nào nghi nghe nhầm")
            if res["trimmed"]:
                msg += f" · căn lại đầu / cuối {res['trimmed']} câu theo lúc có giọng thật"
            if prof:
                msg += f" · cao độ giọng thường – {prof}"
            return {"message": msg, "pick": res["suspect"]}
        self.submit("join", "Lọc câu nghe nhầm", fn)

    def _voice_ok(self):
        rows = [r for r in sorted(self.model.selected) if 0 <= r < len(self.model.segs)]
        if not rows:
            return self.win.toast("Tích câu thoại thật bị đánh dấu nhầm trước", "warn")
        for r in rows:
            ex = self.model.segs[r].extra
            ex.pop("noise", None)
            ex["voice_ok"] = True
            if ex.pop("ai_song", None) is not None:      # AI đoán nhầm là lời bài hát -> lời thoại, lồng tiếng lại
                ex.pop("song", None)
                ex["not_song"] = True
        self.model.dataChanged.emit(self.model.index(rows[0], 0), self.model.index(rows[-1], self.model.columnCount() - 1))
        self._segs_edited()
        self.win.toast(f"Đã bỏ đánh dấu {len(rows)} câu (lần lọc sau không đánh dấu lại)", "ok")

    def _toggle_flag(self, key: str):
        """Đặt / bỏ dấu bài hát 🎵 hoặc Bỏ lồng cho các câu đã tích (thay 2 cột cũ của bảng)."""
        rows = [r for r in sorted(self.model.selected) if 0 <= r < len(self.model.segs)]
        if not rows:
            return self.win.toast("Tích câu ở cột Chọn trước", "warn")
        if key == "nodub":
            return self._dub_rows_flag("dub_toggle", rows)
        segs = self.model.segs
        on = not all(segs[r].extra.get("song") for r in rows)
        for r in rows:
            if on:
                segs[r].extra["song"] = True
                segs[r].extra.pop("not_song", None)
            else:
                segs[r].extra.pop("song", None)
                segs[r].extra["not_song"] = True   # người dùng nói không phải bài hát: không tự đánh dấu lại
        self.model.dataChanged.emit(self.model.index(rows[0], 0), self.model.index(rows[-1], self.model.columnCount() - 1))
        self._save_segs_now()
        what = f"Đã đánh dấu {len(rows)} câu là bài hát 🎵" if on else f"Đã bỏ dấu 🎵 {len(rows)} câu"
        if not pipeline.has_dub(self.p):
            return self.win.toast(what + " – áp dụng khi tạo bản lồng tiếng", "ok")
        need = 0 if on else sum(1 for r in rows if not segs[r].extra.get("tts_file"))
        self._relayout(what + (f" – {need} câu chưa có giọng: bấm 'Tạo lại giọng các câu đã tích'" if need else ""))

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
        if self.model.set_voices(rows, vid):   # 1 lần cho mọi câu (từng câu 1: 2.600 câu đơ vài phút)
            self._save_segs_now()
        name = self.d_line_voice.currentText() if vid else "giọng của người nói"
        self.win.toast(f"{len(rows)} câu đã tích đọc bằng {name} – bấm 'Tạo lại giọng các câu đã tích' để đọc lại", "ok")

    def _apply_speed(self, rows: list[int], speed: float | None):
        """Tốc độ đọc tối thiểu cho các câu (None = về tốc độ gốc) -> xếp lại bản lồng đang dùng (không đọc lại câu nào),
        trình phát nghe ngay."""
        segs = self.model.segs
        rows = [r for r in rows if 0 <= r < len(segs)]
        if not rows:
            return self.win.toast("Tích chọn câu trước (cột Chọn, hoặc chọn khối trên dòng Lồng tiếng của timeline)", "warn")
        changed = 0
        for r in rows:
            ex = segs[r].extra
            old = ex.get("speed")
            if speed is None:
                ex.pop("speed", None)
            else:
                ex["speed"] = round(float(speed), 2)
            changed += old != ex.get("speed")
        self.model.dataChanged.emit(self.model.index(rows[0], 0), self.model.index(rows[-1], self.model.columnCount() - 1))
        self._save_segs_now()
        what = f"tốc độ ×{speed:.2f}" if speed is not None else "tốc độ gốc"
        if not changed:
            return self.win.toast(f"{len(rows)} câu đã ở {what}", "info")
        if not any(s.extra.get("tts_file") for s in segs):
            return self.win.toast(f"Đã đặt {what} cho {len(rows)} câu – áp dụng khi bấm Tạo bản lồng tiếng", "ok")
        self._relayout(f"Đã đặt {what} cho {len(rows)} câu", rows[0])

    def _play_line(self, row: int | None = None):
        rows = [row] if row is not None else self._dub_current_rows()
        if not rows or not (0 <= rows[0] < len(self.model.segs)):
            return self.win.toast("Chọn 1 câu trong bảng trước", "warn")
        s = self.model.segs[rows[0]]
        # dừng khi GIỌNG LỒNG của câu đọc xong (giọng Việt thường dài hơn phụ đề, có thể lấn sang ~0,2 s đầu câu sau);
        # trước đây dừng ở cuối phụ đề + 0,35 s -> mất chữ cuối
        end, start = s.end, s.start
        for a, b, k, *_ in pipeline.dub_clips(self.p, self.model.segs):
            if int(k) == rows[0] and (self.p.edit.get("dub") or {}).get("enabled"):
                end, start = max(end, float(b)), min(start, float(a))
        self._stop_at = end + 0.25
        self.player.seek(max(0.0, start - 0.1))
        if not self.player.playing():
            self.player.toggle()

    def _redub_picked(self):
        rows = sorted(self.model.selected)
        if not rows:
            return self.win.toast("Tích chọn câu cần tạo lại giọng ở cột Chọn trước", "warn")
        vid = self.d_line_voice.currentData() or ""
        # ô "Giọng cho câu đã tích" đang chọn 1 giọng mà câu tích chưa mang giọng đó -> áp trước khi đọc lại
        if vid and self.model.set_voices(rows, vid):
            self._save_segs_now()
        self._redub_rows(rows)

    def _redub_rows(self, rows: list[int], play: bool = True):
        self.inspector._flush()
        rows = [r for r in rows if 0 <= r < len(self.model.segs)]
        if not rows:
            return self.win.toast("Chưa chọn câu nào", "warn")
        empty = [r + 1 for r in rows if not self.model.segs[r].trans.strip()]
        if empty:
            return self.win.toast(f"Câu {', '.join(map(str, empty[:5]))} chưa có lời tiếng Việt", "warn")
        eng = self.d_engine.currentData()
        rate = self.d_rate.currentData()
        st = dict(load_settings())
        st["dub_skip_songs"] = self.d_skipsong.isChecked()
        self._replay_row = rows[0] if play else None

        def fn(j, p):
            pipeline.redub(p, rows, rate, st, j.update, j.cancelled, eng)
            sts = p.data.get("dub_stats") or {}
            msg = f"Đã tạo lại giọng {sts.get('redone', len(rows))}/{len(rows)} câu – trình phát đang dùng bản mới"
            segs = p.segments
            used = sorted({segs[r].extra.get("voice_used") for r in rows if r < len(segs) and segs[r].extra.get("voice_used")})
            if used:
                msg += " · giọng: " + ", ".join(pipeline.voice_label(v) for v in used)
            for spec, n in (sts.get("fallback") or {}).items():
                msg += (f" · ⚠ {n} câu chọn {pipeline.voice_label(spec)} nhưng bộ đọc này chưa cài / giọng mẫu đã xoá – "
                        "đã đọc bằng Hoài My (Edge)")
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
        """AI rút gọn các câu QUÁ DÀI trong các câu đã chọn (câu đã vừa giữ nguyên) rồi tự tạo lại giọng đúng các câu
        vừa rút gọn (đã có bản lồng) – trình phát nghe được ngay; các câu đã rút gọn được tích sẵn."""
        self.inspector._flush()
        eng = self.d_engine.currentData()
        rate = self.d_rate.currentData()
        st = dict(load_settings())
        st["dub_skip_songs"] = self.d_skipsong.isChecked()
        self._replay_row = None

        def fn(j, p):
            j.update(0.05, f"AI đang rút gọn các câu quá dài trong {len(rows)} câu đã chọn…")
            r = pipeline.shorten_lines(p, rows, st)
            ch = r["changed"]
            if not ch:
                msg = "Không có câu nào cần rút gọn"
                if r["fit"]:
                    msg += f" – {len(r['fit'])} câu đã vừa thời gian nhân vật nói (✓ xanh / đọc nhanh ≤ ×1,15)"
                if r["same"]:
                    msg += f" – AI không viết ngắn hơn được {len(r['same'])} câu (sửa tay ở ô Tiếng Việt)"
                return {"message": msg}
            msg = f"AI đã rút gọn {len(ch)} câu"
            if r["fit"]:
                msg += f" (bỏ qua {len(r['fit'])} câu đã vừa)"
            if r["same"]:
                msg += f", {len(r['same'])} câu AI không rút ngắn được"
            if pipeline.has_dub(p):
                pipeline.redub(p, ch, rate, st, lambda x, m="": j.update(0.3 + 0.7 * x, m), j.cancelled, eng)
                msg += " và đã tạo lại giọng đúng các câu đó – trình phát đang dùng bản mới"
            else:
                msg += " – bấm 'Tạo bản lồng tiếng' để đọc"
            return {"message": msg, "pick": ch}
        self.submit("redub" if pipeline.has_dub(self.p) else "shorten", f"AI rút gọn {len(rows)} câu", fn)

    def _unshorten_rows(self, rows: list[int]):
        """Bỏ bản AI rút gọn (câu đã chọn quay về lời dịch cũ) rồi tạo lại giọng các câu đó."""
        rows = [r for r in rows if 0 <= r < len(self.model.segs) and self.model.segs[r].extra.get("trans_long")]
        if not rows:
            return self.win.toast("Các câu đã tích chưa được AI rút gọn (không có lời cũ để quay về)", "info")
        self.inspector._flush()
        eng = self.d_engine.currentData()
        rate = self.d_rate.currentData()
        st = dict(load_settings())
        st["dub_skip_songs"] = self.d_skipsong.isChecked()
        self._replay_row = rows[0]

        def fn(j, p):
            back = pipeline.unshorten_lines(p, rows)
            msg = f"Đã đưa {len(back)} câu về lời trước khi rút gọn"
            if back and pipeline.has_dub(p):
                pipeline.redub(p, back, rate, st, j.update, j.cancelled, eng)
                msg += " và tạo lại giọng"
            return {"message": msg, "pick": back}
        self.submit("redub", f"Bỏ rút gọn {len(rows)} câu", fn)

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
            return self.win.toast(f"Đã nạp {len(cues)} câu đã dịch" + self._note_trans_lang(), "ok")
        n_before = len(segs)
        stats: dict = {}
        changed = pipeline.apply_translation(segs, cues, stats)
        self.model.set_segments(segs)
        self._fit_rows()
        self._segs_edited()
        parts = []   # file dịch khác số câu: báo đã xử lý thế nào (không còn câu trống)
        if stats.get("merged"):
            parts.append(f"gộp {stats['merged']} câu gốc vào câu trước (file dịch gộp câu)")
        if stats.get("joined"):
            parts.append(f"{stats['joined']} câu gốc nhận 2 câu dịch, đã nối lại (file dịch tách câu)")
        if stats.get("added"):
            parts.append(f"thêm {stats['added']} câu chỉ có trong file dịch")
        diff = (f"File dịch {len(cues)} câu, dự án {n_before} câu: " + "; ".join(parts) + f" → còn {len(segs)} câu. "
                if parts else "")
        note = self._note_trans_lang()
        if stats.get("songs"):
            note += (f" · {stats['songs']} câu có dấu ♪ đã đánh dấu là bài hát 🎵 (không lồng tiếng – bấm 'Tích câu 🎵' ở "
                     "tab Lồng tiếng để xem)")
        if not changed:
            return self.win.toast(diff + "File này không làm đổi câu nào (lời Việt giống hệt bản đang có)" + note, "warn")
        self.model.set_picked(range(len(segs)), False)
        self.model.set_picked(changed)       # tích sẵn các câu đổi lời -> bấm "Tạo lại giọng…" là đọc lại đúng các câu này
        self.win.toast(diff + f"Đã nạp bản dịch: {len(changed)}/{len(segs)} câu đổi lời và đã được tích sẵn – bấm "
                       "'Tạo lại giọng các câu đã tích' để đọc lại và cập nhật vào video" + note, "ok")

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

    def _save_mix_mp3(self):
        """Tải MP3 toàn bộ âm thanh đã trộn đúng như đang nghe (tab Âm thanh)."""
        if not self.p:
            return
        out, _ = QFileDialog.getSaveFileName(self, "Lưu MP3", str(Path.home() / f"{safe_stem(self.p)}_amthanh.mp3"),
                                             "MP3 (*.mp3)")
        if not out:
            return
        if not out.lower().endswith(".mp3"):
            out += ".mp3"
        self.flush()

        def fn(j, p):
            pipeline.mix_audio_file(p, Path(out), j.update)
            return {"message": f"Đã lưu {Path(out).name}", "path": out}
        self.submit("tool", "Tải MP3 bản trộn", fn)

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
        """Áp ngay chỉnh sửa (khung hình, màu, tốc độ, đoạn cắt) + kiểu phụ đề lên trình phát bên phải."""
        if not self.p or self.loading:
            return
        try:
            ed = self.collect_edit()
        except Exception:  # noqa: BLE001 – giao diện chưa dựng xong
            return
        if self._ratio_view:      # 👁 xem thử 1 tỉ lệ xuất thêm (với phần chỉnh riêng của tỉ lệ đó)
            from ..video import edit_for_ratio

            ed = edit_for_ratio(ed, self._ratio_view)
        extra = []
        if getattr(self, "sil", None) is not None and self.sil.preview.isChecked():
            extra += self.sil.on_cuts()           # phát thử bản đã cắt
        if self._junction_cut:
            extra.append(self._junction_cut)      # ▶ xem chỗ nối
        if extra:
            from ..audiomix import merge_spans

            ed = dict(ed, cuts=merge_spans(list(ed.get("cuts") or []) + extra))
        i = self.p.data.get("info", {})
        self.player.set_edit(ed, i.get("width", 0), i.get("height", 0), i.get("duration", 0))
        self.player.view.select_layer(self.layers_tab.sel)
        self._refresh_overlay()
        self._sync_clean()
        QTimer.singleShot(0, self._update_safe)

    def _update_summary(self):
        if not self.p:
            return
        self._live_timer.start()
        self._update_steps()
        e = self.p.edit
        segs = self.model.segs
        items = [f"Phụ đề: {len(segs)} câu, đã dịch {sum(1 for s in segs if s.trans)}" +
                 (f" → ghép vào video ({dict(C.which_choices(pipeline.target_lang(self.p)))[e['subtitles']['which']]})"
                  if e["subtitles"].get("burn") else " (không ghép)")]
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
        if e.get("extra_ratios"):
            items.append("Xuất thêm tỉ lệ: " + ", ".join(r for r in e["extra_ratios"] if r != e["aspect"]))
        lay = [L for L in e.get("layers") or [] if not L.get("hidden")]
        if lay:
            nt = sum(1 for L in lay if L.get("kind") != "logo")
            items.append(f"Lớp phủ: {nt} chữ, {len(lay) - nt} logo")
        if e["flip_h"] or e["flip_v"] or e.get("rotate"):
            items.append("Lật / xoay")
        if abs(e["speed"] - 1) > 1e-3:
            items.append(f"Tốc độ ×{e['speed']:.2f}")
        if e["dub"].get("enabled") and e["dub"].get("wav"):
            items.append(f"Lồng tiếng {langs.INFO[pipeline.trans_lang(self.p)]['short']}" +
                         (" + nhạc nền gốc đã tách" if e["dub"].get("bg_wav") else ""))
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
        st = _deep_merge(self.settings.get("sub_style") or {}, self.current_style())
        st["lang"] = pipeline.trans_lang(self.p)
        segs = self.model.segs
        if which != "source":   # có giọng lồng: phụ đề đã dịch theo giờ HIỆN như video xuất (tab Phụ đề → Giờ hiện)
            segs = pipeline.subs_follow_dub(self.p, segs, self.collect_edit(), self.settings, which)
        save_subtitles(segs, path, which, st, i.get("width") or 1920, i.get("height") or 1080)
        note = " (giờ hiện theo giọng lồng)" if segs is not self.model.segs else ""
        self.win.toast(f"Đã lưu {Path(path).name}{note}", "ok")

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
        # tab Xoá phụ đề: chuột vẽ / kéo vùng che trên video; tab khác: chuột kéo phụ đề (tab Kiểu chữ hiện khung)
        self.player.view.set_tool(regions=i == 2, sub_frame=i == 4)
        self.player.update_sub()
        if i == 2 and not self.hs_editor.pix:
            self._grab_frame()
        if i == self.TAB_EXPORT:
            self._update_summary()
            self.pub.load()

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
        pid = self.p.id
        busy = [j for j in manager.list(pid) if j["status"] in ("queued", "running")]
        if QMessageBox.question(self, "Xoá dự án", "Xoá dự án này?\n(File video gốc trên máy KHÔNG bị xoá; chỉ xoá dữ liệu dự án "
                                "và các file đã xuất trong thư mục dự án.)"
                                + (f"\n\n{len(busy)} việc của dự án đang chạy / chờ sẽ bị huỷ." if busy else "")
                                ) != QMessageBox.StandardButton.Yes:
            return
        for j in busy:   # việc nền còn chạy từng ghi lại project.json -> dự án xoá rồi vẫn hiện tên
            manager.cancel(j["id"])
        for t in (self._save_segs_timer, self._save_edit_timer, self._relayout_timer, self._live_timer):
            t.stop()     # không lưu nốt những gì đang chờ lưu
        self.player.mp.stop()
        self.player.mp.setSource(QUrl())
        self.player.set_live_mix(None)   # nhả file nghe trực tiếp (Windows không xoá được file đang mở)
        import gc

        gc.collect()
        p, self.p = self.p, None
        gone = p.delete()
        self.win.refresh_projects()
        self.win.go_home()
        if not gone:
            self.win.toast("Đã xoá dự án – vài file đang bị giữ sẽ được dọn nốt khi mở lại tool", "ok")
