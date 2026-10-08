"""Trình phát video (QtMultimedia) có lớp phụ đề xem trước vẽ bằng QPainter."""
from __future__ import annotations

from PySide6.QtCore import QRectF, QSizeF, Qt, QUrl, Signal, QTimer
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (QGraphicsItem, QGraphicsScene, QGraphicsView, QHBoxLayout, QLabel, QSlider,
                               QVBoxLayout, QWidget)

from . import theme
from .widgets import IconButton


def fmt(t: float, ms: bool = True) -> str:
    t = max(0.0, t or 0.0)
    h, rem = divmod(t, 3600)
    m, s = divmod(rem, 60)
    base = (f"{int(h):02d}:" if h else "") + f"{int(m):02d}:"
    return base + (f"{s:06.3f}" if ms else f"{int(s):02d}")


class SubtitleItem(QGraphicsItem):
    """Vẽ phụ đề có viền/nền hộp giống lúc xuất video (gần đúng)."""

    def __init__(self):
        super().__init__()
        self.text = ""
        self.box = QRectF(0, 0, 100, 100)
        self.style: dict = {}
        self.video_h = 1080

    def boundingRect(self):
        return self.box

    def set(self, text: str, box: QRectF, style: dict, video_h: int):
        self.prepareGeometryChange()
        self.text, self.box, self.style, self.video_h = text, box, style, max(16, video_h or 1080)
        self.update()

    def paint(self, p: QPainter, opt, widget=None):
        if not self.text:
            return
        st = self.style
        scale = self.box.height() / self.video_h
        size = float(st.get("size") or 0) or self.video_h * 0.055
        px = max(8.0, size * scale)
        f = QFont(st.get("font") or theme.FONT_FAMILY)
        f.setPixelSize(int(px))
        f.setBold(bool(st.get("bold", True)))
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        text = self.text.upper() if st.get("uppercase") else self.text
        lines = text.split("\n")
        from PySide6.QtGui import QFontMetricsF

        fm = QFontMetricsF(f)
        lh = fm.height() * 1.05
        total_h = lh * len(lines)
        pos = st.get("position", "bottom")
        margin = (float(st.get("margin_v") or 0) or self.video_h * 0.07) * scale
        if pos == "top":
            y0 = self.box.top() + margin
        elif pos == "middle":
            y0 = self.box.center().y() - total_h / 2
        else:
            y0 = self.box.bottom() - margin - total_h
        outline = float(st.get("outline", 2.5)) * px / 48 * 2
        for i, line in enumerate(lines):
            w = fm.horizontalAdvance(line)
            x = self.box.center().x() - w / 2
            y = y0 + i * lh + fm.ascent()
            if st.get("box"):
                bc = QColor(st.get("box_color", "#000000"))
                bc.setAlphaF(float(st.get("box_opacity", 0.55)))
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(bc)
                p.drawRoundedRect(QRectF(x - px * 0.3, y0 + i * lh, w + px * 0.6, lh), 4, 4)
            path = QPainterPath()
            path.addText(x, y, f, line)
            if not st.get("box") and outline > 0:
                pen = QPen(QColor(st.get("outline_color", "#000000")), outline)
                pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
                p.strokePath(path, pen)
            p.fillPath(path, QColor(st.get("color", "#FFFFFF")))


class VideoView(QGraphicsView):
    def __init__(self, parent=None):
        super().__init__(parent)
        from PySide6.QtMultimediaWidgets import QGraphicsVideoItem

        self.scene_ = QGraphicsScene(self)
        self.setScene(self.scene_)
        self.video = QGraphicsVideoItem()
        self.scene_.addItem(self.video)
        self.sub = SubtitleItem()
        self.sub.setZValue(10)
        self.scene_.addItem(self.sub)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setStyleSheet("background: #000; border: none; border-radius: 12px;")
        self.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.video_size = QSizeF(16, 9)
        self.video.nativeSizeChanged.connect(self._native)
        self.setMinimumHeight(220)

    def _native(self, s: QSizeF):
        if s.width() > 0:
            self.video_size = s
        self._fit()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._fit()

    def _fit(self):
        w, h = self.viewport().width(), self.viewport().height()
        self.scene_.setSceneRect(0, 0, w, h)
        vs = self.video_size
        sc = min(w / vs.width(), h / vs.height()) if vs.width() else 1
        vw, vh = vs.width() * sc, vs.height() * sc
        self.video.setSize(QSizeF(vw, vh))
        self.video.setPos((w - vw) / 2, (h - vh) / 2)

    def video_rect(self) -> QRectF:
        return QRectF(self.video.pos(), self.video.size())


class Player(QWidget):
    positionChanged = Signal(float)   # giây
    mediaError = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer

        self.view = VideoView()
        self.mp = QMediaPlayer(self)
        self.audio = QAudioOutput(self)
        self.mp.setAudioOutput(self.audio)
        self.mp.setVideoOutput(self.view.video)
        self.segments: list = []
        self.style: dict = {}
        self.which = "trans"
        self.show_subs = True
        self.video_h = 1080
        self._dur = 0.0

        self.btn_play = IconButton("play", "Phát / dừng (phím Space)", 40, accent=True)
        self.btn_back = IconButton("chev_left", "Lùi 1 khung hình", 32)
        self.btn_fwd = IconButton("chev_right", "Tiến 1 khung hình", 32)
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(0, 1000)
        self.time = QLabel("00:00.000 / 00:00")
        self.time.setFont(theme.font(9.5))
        self.time.setProperty("muted", True)
        self.vol = QSlider(Qt.Orientation.Horizontal)
        self.vol.setRange(0, 100)
        self.vol.setValue(80)
        self.vol.setFixedWidth(80)
        self.audio.setVolume(0.8)

        ctrl = QHBoxLayout()
        ctrl.setSpacing(8)
        ctrl.addWidget(self.btn_back)
        ctrl.addWidget(self.btn_play)
        ctrl.addWidget(self.btn_fwd)
        ctrl.addWidget(self.slider, 1)
        ctrl.addWidget(self.time)
        from .widgets import IconBadge

        ctrl.addWidget(IconBadge("volume", 26, theme.MUTED))
        ctrl.addWidget(self.vol)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)
        lay.addWidget(self.view, 1)
        lay.addLayout(ctrl)

        self.btn_play.clicked.connect(self.toggle)
        self.btn_back.clicked.connect(lambda: self.step(-1))
        self.btn_fwd.clicked.connect(lambda: self.step(1))
        self.slider.sliderMoved.connect(lambda v: self.seek(v / 1000 * self._dur))
        self.slider.sliderPressed.connect(lambda: self.seek(self.slider.value() / 1000 * self._dur))
        self.vol.valueChanged.connect(lambda v: self.audio.setVolume(v / 100))
        self.mp.positionChanged.connect(self._pos)
        self.mp.durationChanged.connect(lambda d: setattr(self, "_dur", d / 1000))
        self.mp.playbackStateChanged.connect(self._state)
        self.mp.errorOccurred.connect(lambda err, msg: self.mediaError.emit(msg or str(err)))
        self.fps = 25.0

    # ------------------------------------------------------------ điều khiển
    def load(self, path: str, duration: float = 0.0, fps: float = 25.0, height: int = 1080):
        self.mp.stop()
        self._dur = duration
        self.fps = fps or 25
        self.video_h = height or 1080
        self.mp.setSource(QUrl.fromLocalFile(path) if path else QUrl())
        # hiện khung đầu
        QTimer.singleShot(300, lambda: self.mp.pause() if path else None)

    def toggle(self):
        from PySide6.QtMultimedia import QMediaPlayer

        if self.mp.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.mp.pause()
        else:
            self.mp.play()

    def seek(self, t: float):
        self.mp.setPosition(int(max(0.0, t) * 1000))
        self._pos(int(max(0.0, t) * 1000))

    def step(self, n: int):
        self.mp.pause()
        self.seek(self.position() + n / self.fps)

    def position(self) -> float:
        return self.mp.position() / 1000.0

    def playing(self) -> bool:
        from PySide6.QtMultimedia import QMediaPlayer

        return self.mp.playbackState() == QMediaPlayer.PlaybackState.PlayingState

    def _state(self, st):
        self.btn_play.set_icon("pause" if self.playing() else "play")

    def _pos(self, ms: int):
        t = ms / 1000
        if self._dur and not self.slider.isSliderDown():
            self.slider.blockSignals(True)
            self.slider.setValue(int(t / self._dur * 1000))
            self.slider.blockSignals(False)
        self.time.setText(f"{fmt(t)} / {fmt(self._dur, False)}")
        self.update_sub(t)
        self.positionChanged.emit(t)

    # ------------------------------------------------------------ phụ đề
    def set_subtitles(self, segments: list, style: dict, which: str = "trans"):
        self.segments, self.style, self.which = segments, style, which
        self.update_sub(self.position())

    def update_sub(self, t: float | None = None):
        t = self.position() if t is None else t
        text = ""
        if self.show_subs:
            for s in self.segments:
                if s.start <= t < s.end:
                    if self.which == "source":
                        text = s.text
                    elif self.which == "both":
                        text = (s.trans + "\n" + s.text).strip()
                    else:
                        text = s.trans
                    break
        self.view.sub.set(text, self.view.video_rect(), self.style, self.video_h)
