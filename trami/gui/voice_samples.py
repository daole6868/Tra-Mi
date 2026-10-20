"""Khung "Giọng mẫu của bạn (VieNeu + OmniVoice)" trong tab Lồng tiếng: thêm file ghi âm, ghi âm từ micro,
cắt từ video; sửa lời nói trong file mẫu; nghe / xoá."""
from __future__ import annotations

import time
from pathlib import Path

from PySide6.QtCore import QTimer, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QFileDialog, QInputDialog, QLineEdit, QMessageBox, QVBoxLayout, QWidget

from .. import voices as vlib
from .common import run_async
from .widgets import SectionCard, button, hbox, label

AUDIO_FILTER = "Âm thanh / video (*.wav *.mp3 *.m4a *.aac *.flac *.ogg *.opus *.wma *.mp4 *.mkv *.mov *.webm)"
MAX_REC = 12  # giây


class VoiceSamplesCard(SectionCard):
    def __init__(self, page):
        super().__init__("Giọng mẫu của bạn (VieNeu + OmniVoice)", "mic",
                         "Thêm đoạn ghi âm 3–10 giây của giọng muốn dùng (nói rõ, không nhạc nền) – mỗi mẫu thành 1 giọng "
                         "mới cho cả VieNeu (“🎙 … – giọng mẫu của bạn (VieNeu)”) và OmniVoice. Mẫu càng sạch, giọng đọc "
                         "càng rõ. OmniVoice cần điền đúng lời nói trong đoạn; VieNeu không cần. Sau đó chọn giọng cho "
                         "từng người nói ở bảng phía trên.")
        self.page = page
        from ..tts import omnivoice_available, vieneu_available

        if not omnivoice_available() and not vieneu_available():
            self.add(label("⚠ Chưa cài VieNeu / OmniVoice: chạy CaiDat_AI_NangCao.bat (hoặc lệnh trong README) rồi mở "
                           "lại tool. Vẫn thêm giọng mẫu trước được.", "", wrap=True))
        self.rec_btn = button("Ghi âm từ micro", "mic", on_click=self.toggle_record)
        self.add(hbox(button("Thêm file ghi âm…", "upload", on_click=self.add_file),
                      self.rec_btn,
                      button("Lấy 8 giây từ video đang phát", "film", "Cắt từ vị trí đang phát (ưu tiên giọng đã tách "
                             "khỏi nhạc nền)", on_click=self.add_from_video),
                      None))
        self.rec_label = label("", "muted")
        self.rec_label.hide()
        self.add(self.rec_label)
        self.box = QWidget()
        self.list = QVBoxLayout(self.box)
        self.list.setContentsMargins(0, 0, 0, 0)
        self.list.setSpacing(6)
        self.add(self.box)
        self.recorder = None
        self._rec_t0 = 0.0
        self._rec_timer = QTimer(self, interval=250, timeout=self._rec_tick)
        self.refresh()

    # ------------------------------------------------------------ danh sách
    def refresh(self):
        while self.list.count():
            it = self.list.takeAt(0)
            w = it.widget()
            if w:
                w.hide()
                w.deleteLater()
        items = vlib.list_samples()
        if not items:
            self.list.addWidget(label("Chưa có giọng mẫu nào.", "muted"))
        for v in items:
            row = QWidget()
            name = QLineEdit(v["name"])
            name.setFixedWidth(150)
            name.setToolTip("Tên giọng mẫu")
            text = QLineEdit(v.get("text", ""))
            text.setPlaceholderText("Lời nói trong đoạn ghi âm (bỏ trống: tự nghe bằng Whisper – lần đầu tải ~1,6 GB)")
            name.editingFinished.connect(lambda sid=v["id"], e=name: self._rename(sid, e.text()))
            text.editingFinished.connect(lambda sid=v["id"], e=text: vlib.update(sid, text=e.text()))
            f = str(vlib.path_of(v))
            row.setLayout(hbox(name, label(f"{v.get('dur', 0):.0f}s", "muted"), text,
                               button("Nghe", "volume", flat=True,
                                      on_click=lambda _=False, p=f: QDesktopServices.openUrl(QUrl.fromLocalFile(p))),
                               button("", "trash", "Xoá giọng mẫu này", danger=True, flat=True,
                                      on_click=lambda _=False, sid=v["id"], n=v["name"]: self._delete(sid, n))))
            self.list.addWidget(row)

    def _changed(self):
        self.refresh()
        self.page._fill_speakers()   # cập nhật danh sách giọng trong bảng người nói

    def _rename(self, sid: str, name: str):
        if name.strip():
            vlib.update(sid, name=name)
            self.page._fill_speakers()

    def _delete(self, sid: str, name: str):
        if QMessageBox.question(self, "Xoá giọng mẫu?", f"Xoá giọng mẫu \"{name}\"?") != \
                QMessageBox.StandardButton.Yes:
            return
        vlib.delete(sid)
        self._changed()

    def _ask(self, default_name: str, default_text: str = "") -> tuple[str, str] | None:
        name, ok = QInputDialog.getText(self, "Tên giọng mẫu", "Đặt tên cho giọng này:", text=default_name)
        if not ok:
            return None
        text, ok = QInputDialog.getText(self, "Lời nói trong đoạn ghi âm",
                                        "Gõ đúng lời nói trong đoạn ghi âm (để nhái chuẩn nhất).\n"
                                        "Có thể bỏ trống – tool sẽ tự nghe bằng Whisper.", text=default_text)
        return name, (text if ok else "")

    def _add(self, src, name, text, start=0.0, length=None, cleanup: Path | None = None):
        def done(v):
            if cleanup:
                cleanup.unlink(missing_ok=True)
            self.page.win.toast(f"Đã thêm giọng mẫu \"{v['name']}\" ({v['dur']:.0f} giây)", "ok")
            self._changed()
        run_async(lambda: vlib.add_sample(src, name, text, start, length), done,
                  lambda m: self.page.win.toast("Không thêm được giọng mẫu: " + m, "err"))

    # ------------------------------------------------------------ thêm
    def add_file(self):
        f, _ = QFileDialog.getOpenFileName(self, "Chọn file ghi âm giọng mẫu", "", AUDIO_FILTER)
        if not f:
            return
        r = self._ask(Path(f).stem[:30])
        if r:
            self._add(f, r[0], r[1])

    def add_from_video(self):
        from .. import pipeline

        p = self.page.p
        if not p or not p.data.get("source"):
            return self.page.win.toast("Dự án chưa có video", "warn")
        t = self.page.player.position()
        st = pipeline.stems(p)
        src = st.get("vocals") or str(p.source)
        # lời gốc của các câu nằm trong đoạn 8 giây (giọng gốc -> lời gốc, thường là tiếng Trung)
        said = " ".join(s.text.strip() for s in p.segments if s.start >= t - 0.2 and s.end <= t + 8.2 and s.text.strip())
        r = self._ask(f"Giọng {time.strftime('%H%M%S')}", said)
        if r:
            self._add(src, r[0], r[1], start=t, length=8.0)

    # ------------------------------------------------------------ ghi âm micro
    def toggle_record(self):
        if self.recorder is not None:
            return self._stop_record()
        from PySide6.QtMultimedia import (QAudioInput, QMediaCaptureSession, QMediaDevices, QMediaFormat,
                                          QMediaRecorder)

        if not QMediaDevices.audioInputs():
            return self.page.win.toast("Không tìm thấy micro trên máy", "err")
        vlib.VOICES_DIR.mkdir(parents=True, exist_ok=True)
        self._rec_file = vlib.VOICES_DIR / f"_rec_{int(time.time())}.m4a"
        self.session = QMediaCaptureSession(self)
        self.audio_in = QAudioInput(QMediaDevices.defaultAudioInput(), self)
        self.session.setAudioInput(self.audio_in)
        self.recorder = QMediaRecorder(self)
        self.session.setRecorder(self.recorder)
        fmt = QMediaFormat(QMediaFormat.FileFormat.Mpeg4Audio)
        fmt.setAudioCodec(QMediaFormat.AudioCodec.AAC)
        self.recorder.setMediaFormat(fmt)
        self.recorder.setQuality(QMediaRecorder.Quality.HighQuality)
        self.recorder.setOutputLocation(QUrl.fromLocalFile(str(self._rec_file)))
        self.recorder.recorderStateChanged.connect(self._rec_state)
        self.recorder.errorOccurred.connect(lambda e, m: self.page.win.toast("Lỗi ghi âm: " + m, "err"))
        self.recorder.record()
        self._rec_t0 = time.time()
        self._rec_timer.start()
        self.rec_btn.setText("Dừng ghi âm")
        self.rec_label.show()
        self._rec_tick()

    def _rec_tick(self):
        el = time.time() - self._rec_t0
        self.rec_label.setText(f"● Đang ghi âm {el:4.1f}s – nói rõ 5–10 giây rồi bấm \"Dừng ghi âm\" "
                               f"(tự dừng sau {MAX_REC} giây)")
        if el >= MAX_REC:
            self._stop_record()

    def _stop_record(self):
        if self.recorder is not None:
            self.recorder.stop()
        self._rec_timer.stop()

    def _rec_state(self, state):
        from PySide6.QtMultimedia import QMediaRecorder

        if state != QMediaRecorder.RecorderState.StoppedState:
            return
        loc = self.recorder.actualLocation().toLocalFile() or str(self._rec_file)
        self.recorder = None
        self.rec_btn.setText("Ghi âm từ micro")
        self.rec_label.hide()
        if not Path(loc).exists() or Path(loc).stat().st_size < 2000:
            return self.page.win.toast("Không ghi được âm thanh từ micro", "err")
        r = self._ask(f"Giọng thu âm {time.strftime('%H%M')}")
        if r:
            self._add(loc, r[0], r[1], cleanup=Path(loc))
        else:
            Path(loc).unlink(missing_ok=True)
