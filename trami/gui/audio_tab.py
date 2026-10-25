"""Tab "Âm thanh": MỌI chỉnh âm thanh của video ở 1 chỗ – tiếng gốc, nhạc nền + hiệu ứng, giọng gốc, giọng lồng tiếng,
nhạc thêm, âm lượng tổng, đổi cao độ. Kéo thanh / tắt lớp / nghe riêng -> nghe ngay trên video (bộ trộn trực tiếp
trami/audiomix.LiveMix); video xuất ra trộn đúng như vậy (video.build_and_render dùng chung quy tắc)."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QFrame, QHBoxLayout, QPushButton, QVBoxLayout, QWidget

from . import theme
from .widgets import (Form, IconBadge, IconButton, SectionCard, SliderRow, Switch, button, combo, flow, hbox, label,
                      set_combo)

SOURCES = [("auto", "Tự động – đã tách giọng + có lồng tiếng thì dùng bản tách"),
           ("orig", "Tiếng gốc nguyên bản của video"),
           ("stems", "Bản đã tách: nhạc nền và giọng gốc chỉnh riêng")]
PITCHES = [("1.0", "Giữ nguyên"), ("0.94", "Trầm hơn"), ("1.06", "Cao hơn")]


class TrackRow(QFrame):
    """1 lớp âm thanh: biểu tượng · tên + mô tả · loa (tắt / bật) · Nghe riêng · thanh âm lượng 0–200 %."""
    changed = Signal()
    solo = Signal(str, bool)

    def __init__(self, key: str, icon: str, title: str, sub: str, value: float = 1.0, allow_solo: bool = True):
        super().__init__()
        self.key = key
        self.muted = False
        self.setObjectName("trackRow")
        self.setStyleSheet(f"#trackRow {{ background: {theme.CARD2}; border: 1px solid {theme.BORDER}; "
                           "border-radius: 10px; }")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 8, 10, 8)
        lay.setSpacing(6)
        top = QHBoxLayout()
        top.setSpacing(10)
        top.addWidget(IconBadge(icon, 32))
        txt = QVBoxLayout()
        txt.setSpacing(0)
        self.title = label(title)
        self.title.setFont(theme.font(10, QFont.Weight.DemiBold))
        self.sub = label(sub, "muted", wrap=True)
        txt.addWidget(self.title)
        txt.addWidget(self.sub)
        top.addLayout(txt, 1)
        self.solo_btn = QPushButton("Nghe riêng")
        self.solo_btn.setCheckable(True)
        self.solo_btn.setProperty("flat", True)
        self.solo_btn.setToolTip("Chỉ nghe lớp này trên trình phát (để dò âm lượng) – không ảnh hưởng video xuất")
        self.solo_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.solo_btn.toggled.connect(self._solo)
        self.solo_btn.setVisible(allow_solo)
        top.addWidget(self.solo_btn)
        self.mute_btn = IconButton("volume", "Tắt / bật lớp này (cả khi xuất video)", 32)
        self.mute_btn.clicked.connect(self._toggle_mute)
        top.addWidget(self.mute_btn)
        lay.addLayout(top)
        self.slider = SliderRow(0, 200, 5, value * 100, 0, " %")
        self.slider.changed.connect(lambda *_: self.changed.emit())
        lay.addWidget(self.slider)
        self.extra = QVBoxLayout()
        self.extra.setSpacing(4)
        lay.addLayout(self.extra)

    def _toggle_mute(self):
        self.set_muted(not self.muted)
        self.changed.emit()

    def set_muted(self, on: bool):
        self.muted = on
        self.mute_btn.set_icon("mute" if on else "volume")
        self.slider.setEnabled(not on)
        self.title.setProperty("muted", on)
        self.title.style().unpolish(self.title)
        self.title.style().polish(self.title)

    def _solo(self, on: bool):
        self.solo_btn.setText("● Đang nghe riêng" if on else "Nghe riêng")
        self.solo.emit(self.key, on)

    def value(self) -> float:
        return round(self.slider.value() / 100, 3)

    def set_value(self, v: float):
        self.slider.set(float(v) * 100)


class AudioTab(QWidget):
    """Nội dung tab Âm thanh. page: ProjectPage (gọi lại khi đổi để lưu + cập nhật bộ trộn ngay)."""
    changed = Signal()          # đổi âm lượng / tắt lớp / nguồn: lưu + nghe ngay
    sources_changed = Signal()  # đổi file / cao độ: phải giải mã lại lớp đó (vài giây)
    solo = Signal(object)       # tên lớp nghe riêng hoặc None

    def __init__(self, page):
        super().__init__()
        self.page = page
        self.loading = False
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 2, 0)
        v.setSpacing(theme.GAP)

        # ---- 1. nguồn tiếng gốc
        src = SectionCard("1. Nguồn tiếng gốc", "film",
                          "Tiếng gốc của video gồm lời thoại tiếng Trung + nhạc. Sau khi tách (tab Lồng tiếng mục 1) "
                          "có thể chỉnh riêng nhạc nền và giọng nói gốc.")
        self.src = combo(SOURCES, "auto")
        self.src.currentIndexChanged.connect(self._emit)
        src.add(self.src)
        self.src_status = label("", "muted", wrap=True)
        self.sep_btn = button("Tách giọng / nhạc nền", "layers", "Tách giọng nói gốc khỏi nhạc nền + hiệu ứng",
                              on_click=lambda: page.run_separate())
        self.retime_btn = button("Căn giờ theo hình", "clock",
                                 "Phụ đề / giọng lồng càng về sau càng lệch khẩu hình (video dài, livestream, video ghép) "
                                 "-> đo giờ âm thanh của video và căn bản tách giọng, mốc câu, bản lồng cho khớp hình. "
                                 "Không phải nghe, dịch hay đọc lại. Đã khớp thì không đổi gì.",
                                 on_click=lambda: page.run_retime())
        self.sync_status = label("", "muted", wrap=True)
        src.add(self.src_status)
        src.add(flow(self.sep_btn, self.retime_btn))
        src.add(self.sync_status)
        v.addWidget(src)

        # ---- 2. các lớp
        lay = SectionCard("2. Các lớp âm thanh", "volume",
                          "Kéo thanh để chỉnh to / nhỏ (100 % = như bản gốc), bấm hình loa để tắt lớp, \"Nghe riêng\" "
                          "để chỉ nghe 1 lớp. Bấm Phát trên video bên phải – nghe thay đổi NGAY; video xuất ra trộn "
                          "đúng như vậy.")
        self.rows: dict[str, TrackRow] = {}
        self.rows["orig"] = TrackRow("orig", "film", "Tiếng gốc của video", "Lời thoại tiếng Trung + nhạc, nguyên bản "
                                     "(dùng khi chưa tách hoặc chọn nguồn \"nguyên bản\")")
        self.rows["bg"] = TrackRow("bg", "layers", "Nhạc nền + hiệu ứng (đã tách)",
                                   "Phần còn lại sau khi bỏ giọng nói: nhạc, tiếng động, hiệu ứng")
        self.rows["voice"] = TrackRow("voice", "users", "Giọng nói gốc tiếng Trung (đã tách)",
                                      "Thường để 0 % khi lồng tiếng; để nhỏ (10–20 %) nếu muốn nghe thoáng lời gốc", 0.0)
        self.songs = Switch(True)
        self.songs.setToolTip("Các câu được đánh dấu 🎵 (đang hát) giữ nguyên giọng gốc 100 %")
        self.rows["voice"].extra.addLayout(hbox(label("Đoạn hát 🎵 giữ nguyên giọng gốc", "muted"), self.songs, None))
        self.rows["dub"] = TrackRow("dub", "mic", "Giọng lồng tiếng Việt", "Bản lồng tiếng đang dùng (tab Lồng tiếng)")
        self.dub_on = Switch(False)
        self.dub_status = label("", "muted", wrap=True)
        self.rows["dub"].extra.addLayout(hbox(label("Dùng bản lồng tiếng", "muted"), self.dub_on, self.dub_status,
                                              None))
        self.rows["bgm"] = TrackRow("bgm", "music", "Nhạc thêm", "File nhạc của bạn, tự lặp lại hết video", 0.2)
        self.bgm_name = label("Chưa chọn", "muted")
        self.duck = Switch(True)
        self.duck.setToolTip("Có lời lồng tiếng thì nhạc thêm nhỏ xuống ~16 dB cho nghe rõ lời, hết lời thì to lại")
        self.rows["bgm"].extra.addWidget(flow(button("Chọn nhạc…", "music", on_click=lambda: page._pick_asset("bgm")),
                                              button("", "x", "Bỏ nhạc thêm", flat=True,
                                                     on_click=lambda: page._clear_asset("bgm")),
                                              self.bgm_name))
        self.rows["bgm"].extra.addLayout(hbox(label("Tự giảm nhạc khi có lời lồng tiếng", "muted"), self.duck, None))
        self.rows["master"] = TrackRow("master", "volume", "Âm lượng tổng", "Cả video (sau khi trộn mọi lớp)",
                                       allow_solo=False)
        self.rows["master"].mute_btn.hide()
        for r in self.rows.values():
            lay.add(r)
            r.changed.connect(self._emit)
            r.solo.connect(self._solo)
        for w in (self.songs, self.dub_on, self.duck):
            w.toggledOn.connect(self._emit)
        v.addWidget(lay)

        # ---- 3. hiệu ứng giọng gốc
        fx = SectionCard("3. Hiệu ứng", "sparkle")
        f = Form()
        self.pitch = f.row("Đổi cao độ giọng gốc", combo(PITCHES, "1.0"),
                           "Áp dụng cho tiếng gốc (hoặc giọng gốc đã tách). Đổi xong cần vài giây chuẩn bị để nghe thử")
        self.pitch.currentIndexChanged.connect(self._pitch_changed)
        fx.add(f)
        v.addWidget(fx)

        # ---- 4. tải
        out = SectionCard("4. Tải âm thanh", "download")
        out.add(flow(button("Tải MP3 bản trộn", "download", "Lưu toàn bộ âm thanh đã trộn (đúng như đang nghe) thành MP3",
                            on_click=lambda: page._save_mix_mp3()),
                     button("Tải MP3 giọng gốc", "download", flat=True, on_click=lambda: page._save_stem_mp3("vocals")),
                     button("Tải MP3 nhạc nền", "download", flat=True,
                            on_click=lambda: page._save_stem_mp3("instrumental"))))
        v.addWidget(out)
        v.addStretch(1)

    # ------------------------------------------------------------ dữ liệu
    def _emit(self, *_):
        if not self.loading:
            self.changed.emit()

    def _pitch_changed(self, *_):
        if not self.loading:
            self.changed.emit()
            self.sources_changed.emit()

    def _solo(self, key: str, on: bool):
        if on:
            for k, r in self.rows.items():
                if k != key and r.solo_btn.isChecked():
                    r.solo_btn.blockSignals(True)
                    r.solo_btn.setChecked(False)
                    r.solo_btn.setText("Nghe riêng")
                    r.solo_btn.blockSignals(False)
        self.solo.emit(key if on else None)

    def collect(self) -> dict:
        r = self.rows
        return {"src": self.src.currentData(), "orig": r["orig"].value(), "bg": r["bg"].value(),
                "voice": r["voice"].value(), "songs": self.songs.isChecked(), "dub": r["dub"].value(),
                "bgm": r["bgm"].value(), "duck": self.duck.isChecked(), "pitch": float(self.pitch.currentData()),
                "master": r["master"].value(), "mute": [k for k, row in r.items() if row.muted and k != "master"]}

    def fill(self, a: dict, dub_on: bool, bgm_path: str):
        self.loading = True
        try:
            set_combo(self.src, a.get("src", "auto"))
            for k in ("orig", "bg", "voice", "dub", "bgm", "master"):
                self.rows[k].set_value(float(a.get(k, 1.0)))
                self.rows[k].set_muted(k in (a.get("mute") or []))
            self.songs.setChecked(bool(a.get("songs", True)))
            self.duck.setChecked(bool(a.get("duck", True)))
            p = float(a.get("pitch") or 1.0)
            set_combo(self.pitch, min((x for x, _ in PITCHES), key=lambda x: abs(float(x) - p)))
            self.dub_on.setChecked(dub_on)
            self.bgm_name.setText(Path(bgm_path).name if bgm_path else "Chưa chọn")
        finally:
            self.loading = False

    def show_state(self, state: dict, srcs: dict):
        """Hiện / ẩn các lớp đúng với nguồn đang dùng + trạng thái (đã tách chưa, có bản lồng chưa)."""
        stems_on = state.get("stems_on")
        has_stems = "bg" in srcs
        self.rows["orig"].setVisible(not stems_on)
        self.rows["bg"].setVisible(bool(stems_on))
        self.rows["voice"].setVisible(bool(stems_on))
        if has_stems:
            self.src_status.setText("✓ Đã tách giọng / nhạc nền · đang dùng: " +
                                    ("bản đã tách" if stems_on else "tiếng gốc nguyên bản"))
        else:
            self.src_status.setText("Chưa tách giọng / nhạc nền – đang dùng tiếng gốc nguyên bản")
        self.sep_btn.setVisible(not has_stems)
        has_dub = "dub" in srcs
        self.dub_on.setEnabled(has_dub)
        self.rows["dub"].slider.setEnabled(has_dub and not self.rows["dub"].muted)
        self.dub_status.setText("" if has_dub else "Chưa có bản lồng tiếng – tạo ở tab Lồng tiếng mục 3")
        self.rows["bgm"].slider.setEnabled("bgm" in srcs and not self.rows["bgm"].muted)
