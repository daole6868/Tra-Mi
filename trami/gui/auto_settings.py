"""Hộp "Thiết lập tự động": 1 bộ thiết lập dùng chung cho tab Tự động, Trang chủ (Tải về & dịch tự động) và Xử lý hàng
loạt – sửa được cả khi chưa mở dự án nào. Lưu ở Cài đặt (các khoá chung + `auto_preset`, xem trami/autoflow.py)."""
from __future__ import annotations

from PySide6.QtWidgets import QDialog, QScrollArea, QVBoxLayout, QWidget

from .. import autoflow, pipeline, skills, tts
from ..config import load_settings, save_settings
from . import common as C
from .widgets import Form, Switch, button, combo, hbox, label, set_combo


class AutoSettingsDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Thiết lập tự động")
        self.resize(760, 720)
        s = load_settings()
        pv = autoflow.preset_values(s)
        v = QVBoxLayout(self)
        v.setContentsMargins(20, 18, 20, 16)
        v.setSpacing(10)
        v.addWidget(label("Thiết lập tự động", "h3"))
        v.addWidget(label("Dùng chung cho tab Tự động của mọi dự án, nút 'Tải về & dịch tự động' ở Trang chủ và Xử lý hàng "
                          "loạt. Làm tới lồng tiếng rồi dừng trước bước xuất video.", "muted", wrap=True))
        f = Form()
        self.method = f.row("Nguồn phụ đề", combo(C.METHODS, pv["method"]))
        self.asr = f.row("Bộ nhận dạng", combo(C.ASR_ENGINES, s.get("asr_engine")))
        self.ocrfps = f.row("Tốc độ quét phụ đề (OCR)", combo(C.OCR_SPEEDS, str(s.get("ocr_fps", "4"))))
        self.lang = f.row("Ngôn ngữ gốc", combo(C.LANGS, pv["language"]))
        self.sep = f.row("Tách giọng khỏi nhạc trước", Switch(bool(s.get("sep_before_asr", True))))
        self.spk = f.row("Phân biệt người nói", Switch(bool(s.get("diarize", True))))
        self.split = f.row("Kiểu tách câu", combo(C.SPLIT_STYLES, s.get("split_style", "dub")), C.SPLIT_HELP)
        self.joinai = f.row("AI nối câu theo nghĩa", combo(pipeline.join_engine_choices(s), s.get("ai_join_engine", "auto")))
        self.engine = f.row("Công cụ dịch", combo(C.ENGINES, s.get("translate_engine")))
        self.target = f.row("Dịch sang", combo(C.TARGETS, s.get("target_lang", "vi")))
        self.style = f.row("Thể loại / văn phong", combo(C.STYLES, s.get("translate_style")))
        self.skill = f.row("Kỹ năng dịch AI", combo([("", "Không dùng")] + [
            (x["id"], ("🔒 " if x.get("builtin") else "✎ ") + x["name"]) for x in skills.all_skills(s)],
            s.get("translate_skill", "")))
        self.clean = f.row("AI sửa lỗi nghe & nối câu trước khi dịch", Switch(bool(s.get("ai_clean_before_translate", True))))
        self.songai = f.row("AI lọc lời bài hát trước khi dịch", Switch(bool(s.get("ai_song_filter", True))))
        self.review = f.row("AI kiểm tra lại bản dịch", Switch(bool(s.get("ai_review_after_translate", False))))
        self.fill = f.row("Câu ngắn thêm từ đệm cho vừa thời gian", Switch(bool(s.get("translate_fill", True))))
        self.split_long = f.row("Tách câu dịch dài", Switch(bool(s.get("split_long_trans", True))),
                                "Dịch xong hết -> tách câu dịch dài ở dấu ? ! . / dấu phẩy -> tách xong hết mới lồng tiếng")
        self.hardsub = f.row("Phụ đề Trung cũ", combo(C.HARDSUB, pv["hardsub_mode"]))
        self.which = f.row("Ghép phụ đề vào video", combo(C.WHICH, pv["which"]))
        self.dub = f.row("Lồng tiếng (đọc bản dịch)", Switch(bool(pv["dub"])))
        self.tts = f.row("Bộ đọc lồng tiếng", combo(C.TTS_ENGINES, s.get("tts_engine", "edge")))
        self.voice = f.row("Giọng lồng", combo([], None),
                           "Chưa nghe phim nên chưa biết có những người nói nào: chọn 1 giọng cho tất cả, hoặc tự gán "
                           "giọng nam / nữ theo từng người nói")
        self.voice.setMinimumWidth(280)
        self.parallel = f.row("Vừa tách vừa dịch", Switch(bool(s.get("auto_parallel", False))))
        self.denoise = f.row("Lọc nhiễu âm thanh", Switch(bool(pv["denoise"])))
        self.target.currentIndexChanged.connect(self._fill_voices)
        self.target.currentIndexChanged.connect(lambda: C.relabel(self.which, C.which_choices(self.target.currentData())))
        C.relabel(self.which, C.which_choices(self.target.currentData()))
        self._fill_voices(s.get("dub_voice_all", "") or "")
        inner = QWidget()
        inner.setLayout(f)
        sa = QScrollArea()
        sa.setWidgetResizable(True)
        sa.setWidget(inner)
        sa.setFrameShape(QScrollArea.Shape.NoFrame)
        v.addWidget(sa, 1)
        v.addLayout(hbox(None, button("Huỷ", "x", flat=True, on_click=self.reject),
                         button("Lưu thiết lập", "save", on_click=self._save)))

    def _fill_voices(self, cur=None):
        cur = self.voice.currentData() if not isinstance(cur, str) else cur
        st = load_settings()
        self.voice.blockSignals(True)
        self.voice.clear()
        self.voice.addItem("(Mặc định của bộ đọc / theo giọng từng người nói)", "")
        self.voice.addItem("🎲 Tự gán giọng nam / nữ theo từng người nói", "@auto")
        for x in tts.list_voices(st, self.target.currentData() or "vi"):
            self.voice.addItem(x["name"], x["id"])
        set_combo(self.voice, cur or "")
        self.voice.blockSignals(False)

    def values(self) -> dict:
        return {"asr_engine": self.asr.currentData(), "ocr_fps": self.ocrfps.currentData(),
                "sep_before_asr": self.sep.isChecked(), "diarize": self.spk.isChecked(),
                "split_style": self.split.currentData(), "ai_join_engine": self.joinai.currentData(),
                "translate_engine": self.engine.currentData(), "target_lang": self.target.currentData(),
                "translate_style": self.style.currentData(), "translate_skill": self.skill.currentData() or "",
                "ai_clean_before_translate": self.clean.isChecked(), "ai_song_filter": self.songai.isChecked(),
                "ai_review_after_translate": self.review.isChecked(), "translate_fill": self.fill.isChecked(),
                "split_long_trans": self.split_long.isChecked(), "tts_engine": self.tts.currentData(),
                "dub_voice_all": self.voice.currentData() or "", "auto_parallel": self.parallel.isChecked(),
                "auto_preset": {"method": self.method.currentData(), "language": self.lang.currentData(),
                                "hardsub_mode": self.hardsub.currentData(), "which": self.which.currentData(),
                                "dub": self.dub.isChecked(), "denoise": self.denoise.isChecked()}}

    def _save(self):
        save_settings(self.values())
        self.accept()
