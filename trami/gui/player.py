"""Trình phát video (QtMultimedia) có lớp phụ đề xem trước vẽ bằng QPainter."""
from __future__ import annotations

from PySide6.QtCore import QRectF, QSizeF, Qt, QUrl, Signal, QTimer
from PySide6.QtGui import QBrush, QColor, QFont, QImage, QPainter, QPainterPath, QPen, QTransform
from PySide6.QtWidgets import (QGraphicsEffect, QGraphicsItem, QGraphicsRectItem, QGraphicsScene, QGraphicsView,
                               QHBoxLayout, QLabel, QSlider, QVBoxLayout, QWidget)

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


class ColorEffect(QGraphicsEffect):
    """Xem trước độ sáng / tương phản / bão hoà / gamma giống bộ lọc eq của FFmpeg (gần đúng)."""

    def __init__(self):
        super().__init__()
        self.b, self.c, self.s, self.g = 0.0, 1.0, 1.0, 1.0
        self._lut = None

    def set_params(self, b: float, c: float, s: float, g: float):
        self.b, self.c, self.s, self.g = b, c, s, g
        import numpy as np

        x = np.arange(256, dtype=np.float32) / 255.0
        y = np.clip((x - 0.5) * c + 0.5 + b, 0, 1) ** (1.0 / max(0.1, g))
        self._lut = (np.clip(y, 0, 1) * 255 + 0.5).astype(np.uint8)
        self.update()

    def draw(self, painter: QPainter):
        import numpy as np

        pix = self.sourcePixmap(Qt.CoordinateSystem.DeviceCoordinates)
        if isinstance(pix, tuple):  # một số bản PySide trả (pixmap, offset)
            pix, off = pix
        else:
            off = painter.worldTransform().mapRect(
                self.sourceBoundingRect(Qt.CoordinateSystem.LogicalCoordinates)).topLeft().toPoint()
        if pix.isNull() or self._lut is None:
            return self.drawSource(painter)
        img = pix.toImage().convertToFormat(QImage.Format.Format_ARGB32)
        w, h = img.width(), img.height()
        a = np.frombuffer(img.constBits(), np.uint8, img.sizeInBytes()).reshape(h, img.bytesPerLine() // 4, 4)[:, :w]
        rgb = self._lut[a[:, :, :3]]
        if abs(self.s - 1) > 1e-3:
            f = rgb.astype(np.float32)
            luma = f[:, :, 0:1] * 0.114 + f[:, :, 1:2] * 0.587 + f[:, :, 2:3] * 0.299   # BGRA
            rgb = np.clip(luma + (f - luma) * self.s, 0, 255).astype(np.uint8)
        out = np.dstack([rgb, a[:, :, 3:4]]).copy()
        res = QImage(out.data, w, h, w * 4, QImage.Format.Format_ARGB32)
        painter.save()
        painter.setWorldTransform(QTransform())
        painter.drawImage(off, res)
        painter.restore()


class VideoView(QGraphicsView):
    """Khung xem: mô phỏng khung hình đầu ra (tỉ lệ khung, crop, lật, xoay, phóng to, màu) ngay khi chỉnh."""

    def __init__(self, parent=None):
        super().__init__(parent)
        from PySide6.QtMultimediaWidgets import QGraphicsVideoItem

        self.scene_ = QGraphicsScene(self)
        self.setScene(self.scene_)
        # canvas = khung hình đầu ra; clip = vùng nội dung video bên trong (cả hai cắt phần thừa)
        self.canvas = QGraphicsRectItem()
        self.canvas.setPen(QPen(Qt.PenStyle.NoPen))
        self.canvas.setFlag(QGraphicsItem.GraphicsItemFlag.ItemClipsChildrenToShape, True)
        self.scene_.addItem(self.canvas)
        self.clip = QGraphicsRectItem(self.canvas)
        self.clip.setPen(QPen(Qt.PenStyle.NoPen))
        self.clip.setFlag(QGraphicsItem.GraphicsItemFlag.ItemClipsChildrenToShape, True)
        self.video = QGraphicsVideoItem(self.clip)
        self.color = ColorEffect()
        self.color.setEnabled(False)
        self.video.setGraphicsEffect(self.color)
        self.sub = SubtitleItem()
        self.sub.setZValue(10)
        self.scene_.addItem(self.sub)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setStyleSheet("background: #000; border: none; border-radius: 12px;")
        self.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.video_size = QSizeF(16, 9)
        self.edit: dict = {}
        self.src_w = self.src_h = 0
        self.out_h = 1080
        self.on_layout = None   # gọi lại khi khung thay đổi (để vẽ lại phụ đề)
        self.video.nativeSizeChanged.connect(self._native)
        self.setMinimumHeight(220)

    def _native(self, s: QSizeF):
        if s.width() > 0:
            self.video_size = s
        self._fit()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._fit()

    def set_edit(self, edit: dict, src_w: int, src_h: int):
        self.edit = edit or {}
        self.src_w, self.src_h = src_w or 0, src_h or 0
        col = self.edit.get("color") or {}
        b, c = float(col.get("brightness", 0)), float(col.get("contrast", 1))
        s_, g = float(col.get("saturation", 1)), float(col.get("gamma", 1))
        on = abs(b) > 1e-3 or abs(c - 1) > 1e-3 or abs(s_ - 1) > 1e-3 or abs(g - 1) > 1e-3
        if on:
            self.color.set_params(b, c, s_, g)
        self.color.setEnabled(on)
        self._fit()

    def _fit(self):
        from ..video import ASPECTS, target_size

        w, h = self.viewport().width(), self.viewport().height()
        self.scene_.setSceneRect(0, 0, w, h)
        vs = self.video_size
        if vs.width() <= 0 or w <= 0 or h <= 0:
            return
        e = self.edit
        cr = e.get("crop") or {}
        cx, cy = float(cr.get("x", 0)), float(cr.get("y", 0))
        cw, ch = float(cr.get("w", 1)) or 1, float(cr.get("h", 1)) or 1
        rot = int(e.get("rotate") or 0) % 360
        zoom = max(1.0, float(e.get("zoom") or 1.0))
        # nội dung sau crop + xoay (đơn vị: điểm ảnh của bản đang phát)
        rw, rh = vs.width() * cw, vs.height() * ch
        if rot in (90, 270):
            rw, rh = rh, rw
        aspect = e.get("aspect", "original")
        if aspect in ASPECTS:
            aw, ah = ASPECTS[aspect]
        else:
            aw, ah = rw, rh
        # khung đầu ra vừa khít ô xem
        k = min(w / aw, h / ah)
        fw, fh = aw * k, ah * k
        frame = QRectF((w - fw) / 2, (h - fh) / 2, fw, fh)
        mode = e.get("aspect_mode", "blur")
        self.canvas.setRect(frame)
        self.canvas.setBrush(QBrush(QColor("#000" if mode == "pad" or aspect not in ASPECTS else "#20232b")))
        sc = (max if (aspect in ASPECTS and mode == "crop") else min)(fw / rw, fh / rh)
        content = QRectF(frame.center().x() - rw * sc / 2, frame.center().y() - rh * sc / 2, rw * sc, rh * sc)
        self.clip.setRect(content)
        # đặt video: tâm vùng crop -> tâm nội dung, lật, xoay, phóng
        kk = sc * zoom
        self.video.setSize(QSizeF(vs.width() * kk, vs.height() * kk))
        self.video.setPos(0, 0)
        ccx, ccy = (cx + cw / 2) * vs.width() * kk, (cy + ch / 2) * vs.height() * kk
        t = QTransform.fromTranslate(-ccx, -ccy)
        t = t * QTransform.fromScale(-1 if e.get("flip_h") else 1, -1 if e.get("flip_v") else 1)
        t = t * QTransform().rotate(rot)
        t = t * QTransform.fromTranslate(content.center().x(), content.center().y())
        self.video.setTransform(t)
        # chiều cao khung đầu ra thật (phụ đề tính theo nó)
        if self.src_w and self.src_h:
            sw, sh = self.src_w * cw, self.src_h * ch
            if rot in (90, 270):
                sw, sh = sh, sw
            self.out_h = target_size(int(sw), int(sh), aspect, e.get("resolution", "original"))[1]
        else:
            self.out_h = 1080
        if self.on_layout:
            self.on_layout()

    def video_rect(self) -> QRectF:
        return self.canvas.rect()


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
        # âm thanh thay thế (bản lồng tiếng đã trộn) phát song song, đồng bộ theo video
        self.mp2 = QMediaPlayer(self)
        self.audio2 = QAudioOutput(self)
        self.mp2.setAudioOutput(self.audio2)
        self.alt_audio = ""
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
        self.audio2.setVolume(0.8)

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
        self.vol.valueChanged.connect(self._set_volume)
        self.mp.positionChanged.connect(self._pos)
        self.mp.durationChanged.connect(lambda d: setattr(self, "_dur", d / 1000))
        self.mp.playbackStateChanged.connect(self._state)
        self.mp.errorOccurred.connect(lambda err, msg: self.mediaError.emit(msg or str(err)))
        self.fps = 25.0
        self.keep: list = []        # các đoạn được giữ (sau cắt đầu/cuối, xoá đoạn giữa)
        self.view.on_layout = lambda: self.update_sub()

    # ------------------------------------------------------------ điều khiển
    def _set_volume(self, v: int):
        self.audio.setVolume(v / 100)
        self.audio2.setVolume(v / 100)

    def set_alt_audio(self, path: str | None):
        """Phát file âm thanh khác thay cho tiếng của video (vd. bản lồng tiếng). None = tiếng gốc."""
        path = str(path or "")
        if path == self.alt_audio:
            return
        self.alt_audio = path
        self.mp2.stop()
        self.mp2.setSource(QUrl.fromLocalFile(path) if path else QUrl())
        self.audio.setMuted(bool(path))
        if path:
            self.mp2.setPosition(self.mp.position())
            if self.playing():
                self.mp2.play()

    def load(self, path: str, duration: float = 0.0, fps: float = 25.0, height: int = 1080):
        self.mp.stop()
        self.set_alt_audio(None)
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
            t = self.position()
            if self.keep and not any(a - 0.02 <= t < b - 0.06 for a, b in self.keep):
                nxt = next((a for a, b in self.keep if a > t), self.keep[0][0])   # đứng ở đoạn đã cắt / hết
                self.seek(nxt + 0.001)
            self.mp.play()

    def seek(self, t: float):
        self.mp.setPosition(int(max(0.0, t) * 1000))
        if self.alt_audio:
            self.mp2.setPosition(int(max(0.0, t) * 1000))
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
        if self.alt_audio:
            if self.playing():
                self.mp2.setPosition(self.mp.position())
                self.mp2.play()
            else:
                self.mp2.pause()

    def set_edit(self, edit: dict, src_w: int = 0, src_h: int = 0, duration: float = 0.0):
        """Áp các chỉnh sửa (khung hình, màu, tốc độ, đoạn cắt) lên trình phát để xem ngay."""
        from ..video import keep_ranges

        edit = edit or {}
        self.view.set_edit(edit, src_w, src_h)
        rate = min(4.0, max(0.25, float(edit.get("speed") or 1.0)))
        if abs(self.mp.playbackRate() - rate) > 1e-3:
            self.mp.setPlaybackRate(rate)
            self.mp2.setPlaybackRate(rate)
        dur = duration or self._dur
        try:
            self.keep = keep_ranges(dur, edit.get("trim"), edit.get("cuts")) if dur else []
        except Exception:
            self.keep = []
        if self.keep == [(0.0, dur)] or (len(self.keep) == 1 and self.keep[0][0] <= 0.001
                                         and abs(self.keep[0][1] - dur) < 0.01):
            self.keep = []

    def _skip_cut(self, t: float) -> bool:
        """Đang phát vào đoạn đã cắt -> nhảy qua. Trả về True nếu đã tua."""
        if not self.keep or not self.playing():
            return False
        for a, b in self.keep:
            if a - 0.02 <= t < b:
                return False
        nxt = next((a for a, b in self.keep if a > t), None)
        if nxt is None:
            self.mp.pause()
            self.seek(self.keep[-1][1] - 0.05)
        else:
            self.seek(nxt + 0.001)
        return True

    def _pos(self, ms: int):
        t = ms / 1000
        if self._skip_cut(t):
            return
        if self.alt_audio and self.playing() and abs(self.mp2.position() - ms) > 250:
            self.mp2.setPosition(ms)  # bù lệch tiếng / hình
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
        self.view.sub.set(text, self.view.video_rect(), self.style, self.view.out_h)
