"""Trình phát video (QtMultimedia) có lớp phụ đề xem trước vẽ bằng QPainter."""
from __future__ import annotations

import time

from PySide6.QtCore import QObject, QPointF, QRectF, QSizeF, Qt, QTimer, QUrl, Signal, Slot
from PySide6.QtGui import (QBrush, QColor, QFont, QFontMetricsF, QImage, QPainter, QPainterPath, QPen,
                           QTransform)
from PySide6.QtWidgets import (QGraphicsEffect, QGraphicsItem, QGraphicsRectItem, QGraphicsScene, QGraphicsView,
                               QHBoxLayout, QLabel, QSlider, QVBoxLayout, QWidget)

from . import icons, theme
from .widgets import IconButton


def fmt(t: float, ms: bool = True) -> str:
    t = max(0.0, t or 0.0)
    h, rem = divmod(t, 3600)
    m, s = divmod(rem, 60)
    base = (f"{int(h):02d}:" if h else "") + f"{int(m):02d}:"
    return base + (f"{s:06.3f}" if ms else f"{int(s):02d}")


from ..textfit import REF_PX, qt_font as ass_font   # noqa: E402 – đo chữ như libass, dùng chung với video xuất


class SubtitleItem(QGraphicsItem):
    """Phụ đề xem trước vẽ như libass khi xuất (cỡ chữ, ngắt dòng, nền hộp, vị trí).
    Kéo chữ để đặt vị trí; khi có nền hộp, kéo 4 chấm trên 4 cạnh nền để đổi chiều cao / độ dài."""
    SAMPLE = "Phụ đề mẫu – kéo để đặt vị trí"

    def __init__(self):
        super().__init__()
        self.parts: list[tuple[str, str]] = []   # (chữ, "vi" | "zh" (dòng Trung nhỏ, song ngữ) | "src" (chỉ chữ Trung))
        self.box = QRectF(0, 0, 100, 100)        # khung hình đầu ra (toạ độ cảnh)
        self.style: dict = {}
        self.video_h = 1080
        self.editable = False
        self.show_frame = False   # tab Kiểu chữ: luôn hiện khung nét đứt (+ chữ mẫu khi không có câu nào)
        self.on_changed = None    # thả chuột -> on_changed({khoá kiểu chữ: giá trị mới})
        self._ovr: dict = {}      # thay đổi vừa kéo, giữ tới khi trang dự án gửi lại kiểu chữ mới
        self._drag = None
        self._hover = ""
        self._snap = False
        self._geo = None
        self.kara: list[float] | None = None   # karaoke: mỗi từ của câu đã đổi màu bao nhiêu (0..1)
        self._spr = None          # ảnh chữ đã vẽ sẵn (khoá, (vùng, ảnh màu chữ, ảnh màu karaoke)) – xem _sprite
        self.setAcceptHoverEvents(True)
        self.set_editable(False)

    def set_editable(self, on: bool):
        self.editable = on
        self.setAcceptedMouseButtons(Qt.MouseButton.LeftButton if on else Qt.MouseButton.NoButton)
        self.setAcceptHoverEvents(on)
        if not on:
            self._hover = ""
            self.unsetCursor()
        self.update()

    def clear_override(self):
        if not self._drag and self._ovr:
            self._ovr = {}
            self.prepareGeometryChange()
            self._geo = None
            self.update()

    def boundingRect(self):
        w, h = self.box.width(), self.box.height()   # rộng ra: chữ kéo sát mép vẫn vẽ trọn
        return self.box.adjusted(-w / 2, -h / 2, w / 2, h / 2)

    def set(self, parts: list, box: QRectF, style: dict, video_h: int, kara: list[float] | None = None):
        vh = max(16, video_h or 1080)
        if parts == self.parts and box == self.box and style == self.style and vh == self.video_h:
            # cùng câu, cùng kiểu (trình phát báo vị trí ~25 lần/giây): KHÔNG vẽ lại; karaoke chỉ tô lại vùng chữ
            if kara != self.kara:
                self.kara = kara
                g = self._geo
                self.update(g["outer"].adjusted(-40, -40, 40, 40) if g else self.boundingRect())
            return
        self.prepareGeometryChange()
        self.parts, self.box, self.style, self.video_h = parts, box, style, vh
        self.kara = kara
        self._geo = None
        self.update()

    # ------------------------------------------------------------ hình học (giống libass)
    def _layout(self):
        from ..subtitle import layout_event

        fr = self.box
        if fr.height() <= 2 or fr.width() <= 2:
            return None
        parts = self.parts or ([(self.SAMPLE, "vi")] if self.show_frame and self.editable else [])
        if not parts:
            return None
        st = {**self.style, **self._ovr}
        sc = fr.height() / self.video_h
        W, H = fr.width() / sc, float(self.video_h)
        # cùng cách xếp dòng / cỡ chữ / nền như khi xuất (subtitle.layout_event, đo chữ bằng font thật)
        lay = layout_event(parts, {**st, "font": st.get("font") or theme.FONT_FAMILY}, round(W), round(H))
        if not lay:
            return None
        size = lay["size"]
        bold = bool(st.get("bold", True))
        kara = self.kara if st.get("karaoke") and self.parts else None
        rows = [(text, fam, fs, st.get("secondary_color", "#FFE14D") if kind == "zh" else st.get("color", "#FFFFFF"),
                 kind != "zh" and kara is not None) for text, fam, fs, kind in lay["rows"]]
        block_h = lay["block_h"]
        cx, bottom = lay["cx"], lay["bottom"]
        X, top = fr.left() + cx * sc, fr.top() + (bottom - block_h) * sc
        lines, y, maxw, word_i = [], top, 1.0, 0
        for text, fam, fs, col, is_kara in rows:
            f, k, asc = ass_font(fam, bold)
            fac = fs * sc * k / REF_PX
            fm = QFontMetricsF(f)
            w = fm.horizontalAdvance(text) * fac
            lh = fs * sc
            split = None
            if is_kara:    # karaoke: chữ bên trái điểm này đã đổi màu (giống libass quét \kf từng từ)
                from ..karaoke import tokens

                toks = tokens(text)
                done, split = "", w
                for j, (word, join) in enumerate(toks):
                    pr = kara[word_i + j] if word_i + j < len(kara) else 0.0
                    if pr >= 1.0:
                        done += word + (" " if join == " " else "")
                        continue
                    a = fm.horizontalAdvance(done) * fac
                    split = a + pr * (fm.horizontalAdvance(done + word) * fac - a)
                    break
                word_i += len(toks)
            lines.append((text, f, fac, X - w / 2, y, w, lh, y + lh * asc, col, split))
            maxw = max(maxw, w)
            y += lh
        block = QRectF(X - maxw / 2, top, maxw, block_h * sc)
        boxes = [QRectF(fr.left() + bx * sc, fr.top() + by * sc, bw * sc, bh * sc) for bx, by, bw, bh in lay["boxes"]]
        outer = QRectF(block)
        for b in boxes:
            outer = outer.united(b) if not st.get("box_fixed") else b
        return {"st": st, "sc": sc, "size": size, "lines": lines, "block": block, "boxes": boxes, "outer": outer,
                "radius": lay["radius"] * sc, "anchor": QPointF(X, top + block_h * sc), "sample": not self.parts}

    def _geometry(self):
        if self._geo is None:
            self._geo = self._layout()
        return self._geo

    def _sprite(self, g: dict, dpr: float):
        """Vẽ chữ (bóng + viền + màu chữ) 1 LẦN thành ảnh, các lần sau chỉ dán ảnh. 2.0.39: trước đây mỗi khung hình
        đều dựng lại đường viền + ghép bóng bằng phép hợp hình (QPainterPath.united ~50 ms/câu) -> giao diện nghẽn,
        tiếng lồng giật ở câu dài, nặng nhẹ tuỳ cỡ chữ."""
        st = g["st"]
        box = bool(st.get("box"))
        size_s = g["size"] * g["sc"]
        outline = 0.0 if box else max(0.0, float(st.get("outline", 2.5))) / 48 * size_s
        shadow = 0.0 if box else max(0.0, float(st.get("shadow", 1))) / 48 * size_s
        if shadow <= 0.3:
            shadow = 0.0
        lines = [ln for ln in g["lines"] if ln[0]]
        kara = any(ln[9] is not None for ln in lines)
        kcol = st.get("karaoke_color") or "#FFEF00"
        key = (tuple((ln[0], ln[1].family(), ln[1].bold(), round(ln[2], 6), round(ln[3], 2), round(ln[7], 2), ln[8])
                     for ln in lines), round(outline, 3), round(shadow, 3), st.get("outline_color", "#000000"),
               round(dpr, 3), kcol if kara else None)
        if self._spr is not None and self._spr[0] == key:
            return self._spr[1]
        paths = []
        for text, f, fac, x, _y, _w, _lh, base, col, _split in lines:
            path = QPainterPath()
            path.addText(0, 0, f, text)
            paths.append((QTransform.fromScale(fac, fac).map(path).translated(x, base), col))
        if not paths:
            self._spr = (key, None)
            return None
        rect = QRectF()
        for pth, _c in paths:
            rect = rect.united(pth.boundingRect())
        pad = outline + shadow + 2
        rect = rect.adjusted(-pad, -pad, pad, pad)
        # khớp lưới điểm ảnh: dán ảnh đúng ô, không nội suy (chữ nét như vẽ thẳng lên khung)
        import math

        x0, y0 = math.floor(rect.left() * dpr) / dpr, math.floor(rect.top() * dpr) / dpr
        size = (max(1, math.ceil((rect.right() - x0) * dpr)), max(1, math.ceil((rect.bottom() - y0) * dpr)))
        rect = QRectF(x0, y0, size[0] / dpr, size[1] / dpr)
        fmt = QImage.Format.Format_ARGB32_Premultiplied
        pen = QPen(QColor(st.get("outline_color", "#000000")), outline * 2)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)

        def layer(color: QColor | None, dx: float = 0.0, solid: QColor | None = None) -> QImage:
            img = QImage(size[0], size[1], fmt)
            img.setDevicePixelRatio(dpr)
            img.fill(Qt.GlobalColor.transparent)
            q = QPainter(img)
            q.setRenderHint(QPainter.RenderHint.Antialiasing)
            q.translate(-rect.left() + dx, -rect.top() + dx)
            for pth, col in paths:
                if outline > 0:
                    q.strokePath(pth, QPen(solid, outline * 2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.SquareCap,
                                           Qt.PenJoinStyle.RoundJoin) if solid is not None else pen)
                q.fillPath(pth, solid if solid is not None else QColor(color or col))
            q.end()
            return img

        def compose(color: QColor | None) -> QImage:
            img = layer(color)
            if not shadow:
                return img
            # bóng = (chữ + viền) dời đi, đen đặc trên lớp riêng rồi phủ trong suốt 40 % (như BackColour khi xuất)
            out = QImage(size[0], size[1], fmt)
            out.setDevicePixelRatio(dpr)
            out.fill(Qt.GlobalColor.transparent)
            q = QPainter(out)
            q.setOpacity(0.6)
            q.drawImage(QPointF(0, 0), shadow_img)
            q.setOpacity(1.0)
            q.drawImage(QPointF(0, 0), img)
            q.end()
            return out
        shadow_img = layer(None, shadow, QColor(0, 0, 0)) if shadow else None
        res = (rect, compose(None), compose(QColor(kcol)) if kara else None)
        self._spr = (key, res)
        return res

    def shape(self):
        path = QPainterPath()
        g = self._geometry() if self.editable else None
        if g:
            path.addRect(g["outer"].adjusted(-9, -9, 9, 9))
        return path

    def paint(self, p: QPainter, opt, widget=None):
        g = self._geo = self._layout()
        if not g:
            return
        st = g["st"]
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        if g["sample"]:
            p.setOpacity(0.6)
        box = bool(st.get("box"))
        if box:
            bc = QColor(st.get("box_color", "#000000"))
            bc.setAlphaF(max(0.0, min(1.0, float(st.get("box_opacity", 0.55)))))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(bc)
            r = g.get("radius", 0.0)
            for b in g["boxes"]:
                if r >= 0.5:
                    p.drawRoundedRect(b, min(r, b.width() / 2), min(r, b.height() / 2))
                else:
                    p.drawRect(b)
        dev = p.device()
        spr = self._sprite(g, dev.devicePixelRatioF() if dev is not None else 1.0)
        if spr:
            rect, img, kimg = spr
            p.drawImage(rect, img)
            for text, _f, _fac, x, y, _w, lh, _base, _col, split in g["lines"]:
                if kimg is None or split is None or not text or x + split <= rect.left():
                    continue
                p.save()        # karaoke: phần đã đọc lấy từ ảnh tô màu karaoke (viền, bóng giống hệt)
                p.setClipRect(QRectF(rect.left(), y, x + split - rect.left(), lh))
                p.drawImage(rect, kimg)
                p.restore()
        p.setOpacity(1.0)
        if self.editable and (self.show_frame or self._hover or self._drag):
            pen = QPen(QColor(theme.ACCENT), 1.5, Qt.PenStyle.DashLine)
            pen.setCosmetic(True)
            p.setPen(pen)
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRect(g["outer"].adjusted(-3, -3, 3, 3))
            if box:
                from .editors import paint_handles

                paint_handles(p, g["outer"], theme.ACCENT)
            if self._snap:
                p.drawLine(QPointF(self.box.center().x(), self.box.top()),
                           QPointF(self.box.center().x(), self.box.bottom()))

    # ------------------------------------------------------------ kéo bằng chuột
    def _hit(self, pos) -> str:
        g = self._geometry()
        if not g or not self.editable:
            return ""
        if g["st"].get("box"):
            from .editors import edge_handles

            for k, pt in edge_handles(g["outer"]).items():
                if abs(pos.x() - pt.x()) <= 9 and abs(pos.y() - pt.y()) <= 9:
                    return k
        return "move" if g["outer"].adjusted(-6, -6, 6, 6).contains(pos) else ""

    def hoverMoveEvent(self, e):
        from .editors import REGION_CURSORS

        k = self._hit(e.pos())
        if k != self._hover:
            self._hover = k
            if k:
                self.setCursor(REGION_CURSORS[k])
            else:
                self.unsetCursor()
            self.update()

    def hoverLeaveEvent(self, e):
        self._hover = ""
        self.unsetCursor()
        self.update()

    def mousePressEvent(self, e):
        kind = self._hit(e.pos())
        g = self._geometry()
        if not kind or not g:
            e.ignore()
            return
        from ..subtitle import BOX_PAD

        st, fr = g["st"], self.box
        a = g["anchor"]
        v0 = {"pos_x": (a.x() - fr.left()) / fr.width(), "pos_y": (a.y() - fr.top()) / fr.height(),
              "box_w": float(st.get("box_w") or 0.8), "box_h": float(st.get("box_h") or 0.12),
              "box_pad_x": float(BOX_PAD if st.get("box_pad_x") is None else st["box_pad_x"]),
              "box_pad_y": float(BOX_PAD if st.get("box_pad_y") is None else st["box_pad_y"]),
              "block_h": g["block"].height() / fr.height()}
        self._drag = (kind, e.scenePos(), v0, g["size"] * g["sc"], bool(st.get("box_fixed")))
        e.accept()

    def mouseMoveEvent(self, e):
        if not self._drag:
            return
        kind, p0, v0, size_s, fixed = self._drag
        fr = self.box
        d = e.scenePos() - p0
        self._snap = False
        if kind == "move":
            x = v0["pos_x"] + d.x() / fr.width()
            y = v0["pos_y"] + d.y() / fr.height()
            if abs(x - 0.5) * fr.width() < 8:     # hít vào giữa khung
                x, self._snap = 0.5, True
            x = min(1.0, max(0.0, x))
            y = min(1.0, max(min(1.0, v0["block_h"]), y))
            self._ovr.update(position="custom", pos_x=round(x, 4), pos_y=round(y, 4))
        elif kind in ("L", "R"):
            dx = d.x() if kind == "R" else -d.x()
            if fixed:
                self._ovr["box_w"] = round(min(1.0, max(0.05, v0["box_w"] + 2 * dx / fr.width())), 4)
            else:
                self._ovr["box_pad_x"] = round(min(4.0, max(0.0, v0["box_pad_x"] + dx / size_s)), 3)
        else:
            dy = d.y() if kind == "B" else -d.y()
            if fixed:
                self._ovr["box_h"] = round(min(1.0, max(0.02, v0["box_h"] + 2 * dy / fr.height())), 4)
            else:
                self._ovr["box_pad_y"] = round(min(3.0, max(0.0, v0["box_pad_y"] + dy / size_s)), 3)
        self._geo = None
        self.update()

    def mouseReleaseEvent(self, e):
        if not self._drag:
            return
        self._drag = None
        self._snap = False
        self.update()
        if self._ovr and self.on_changed:
            self.on_changed(dict(self._ovr))


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


LOGO_COLOR = "#2BB3FF"     # vùng logo cố định (khác màu vùng phụ đề)


class HardsubOverlay(QGraphicsItem):
    """Vẽ hiệu ứng xoá / che phụ đề cũ lên video ngay khi chỉnh (gắn vào video nên theo cả crop, lật, phóng to).
    Che dải màu: đúng như khi xuất. Làm mờ / xoá AI / delogo: làm mờ gần đúng từ khung hình đang hiện."""

    def __init__(self, video):
        super().__init__(video)
        self.video = video
        self.hs: dict = {}
        self.show_outline = False
        self.clean = False        # trình phát đang chiếu video đã xoá bằng AI -> không vẽ hiệu ứng gần đúng nữa
        self.on_changed = None    # thả chuột sau khi kéo / vẽ / xoá vùng -> on_changed(danh sách vùng)
        self.target = "regions"   # đang khoanh: "regions" (phụ đề) | "logos" (logo cố định)
        self._drag = None
        self._hover = (-1, "")
        self._frame = None
        self._img = None
        self._sink_connected = False
        self.setAcceptedMouseButtons(Qt.MouseButton.NoButton)

    def set_editing(self, on: bool):
        """Tab Xoá phụ đề: vẽ / kéo / đổi cỡ các vùng ngay trên video."""
        self.show_outline = on
        self.setAcceptedMouseButtons(Qt.MouseButton.LeftButton if on else Qt.MouseButton.NoButton)
        self.setAcceptHoverEvents(on)
        if not on:
            self.unsetCursor()
        self.update()

    def boundingRect(self):
        s = self.video.size()
        return QRectF(0, 0, s.width(), s.height())

    def set_hardsub(self, hs: dict):
        self.prepareGeometryChange()
        hs = dict(hs or {})
        if self._drag:      # đang kéo: giữ vùng đang kéo, không để bản lưu cũ ghi đè
            hs[self.target] = self.hs.get(self.target) or []
        self.hs = hs
        need = self.hs.get("mode") in ("blur", "inpaint", "delogo", "patch", "stretch") and bool(self.hs.get("regions"))
        if need and not self._sink_connected:
            sink = self.video.videoSink()
            sink.videoFrameChanged.connect(self._on_frame)
            self._sink_connected = True
            cur = sink.videoFrame()      # video đang dừng: lấy ngay khung đang hiện để làm mờ, không đợi khung mới
            if cur.isValid():
                self._frame, self._img = cur, None
        elif not need and self._sink_connected:
            try:
                self.video.videoSink().videoFrameChanged.disconnect(self._on_frame)
            except (RuntimeError, TypeError):
                pass
            self._sink_connected = False
            self._img = None
        self.update()

    def _on_frame(self, frame):
        self._frame = frame
        self._img = None
        self.update()

    def _frame_image(self):
        if self._img is None and self._frame is not None and self._frame.isValid():
            self._img = self._frame.toImage()
        img = self._img
        return img if img is not None and not img.isNull() else None

    def _paint_patch(self, p: QPainter, img: QImage, regs: list, rects: list):
        """Vá nền gần đúng như khi xuất: chép dải hình sát vùng chữ của khung đang hiện, viền mờ dần."""
        from PySide6.QtGui import QLinearGradient

        from ..video import patch_feather, patch_source

        iw, ih = img.width(), img.height()
        for reg, r in zip(regs, rects):
            sy = patch_source(reg, self.hs.get("patch_from", "auto"))
            src = QRectF(float(reg["x"]) * iw, sy * ih, float(reg["w"]) * iw, float(reg["h"]) * ih).toRect()
            part = img.copy(src).convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)
            if part.isNull() or part.width() < 2 or part.height() < 2:
                continue
            w, h = part.width(), part.height()
            f = patch_feather(w, h)
            q = QPainter(part)
            q.setCompositionMode(QPainter.CompositionMode.CompositionMode_DestinationIn)
            for horiz in (True, False):
                n = w if horiz else h
                g = QLinearGradient(0, 0, n if horiz else 0, 0 if horiz else n)
                k = min(0.5, f / max(1, n))
                g.setColorAt(0, QColor(0, 0, 0, 0))
                g.setColorAt(k, QColor(0, 0, 0, 255))
                g.setColorAt(1 - k, QColor(0, 0, 0, 255))
                g.setColorAt(1, QColor(0, 0, 0, 0))
                q.fillRect(0, 0, w, h, g)
            q.end()
            p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
            p.drawImage(r, part)

    def _paint_stretch(self, p: QPainter, img: QImage, regs: list, rects: list):
        """Kéo dọc gần đúng như khi xuất: dải 2 dòng sát mép trên / dưới vùng của khung đang hiện kéo giãn phủ kín vùng
        (2 mép: mép dưới mờ dần lên trên mép trên)."""
        from PySide6.QtGui import QLinearGradient

        from ..video import STRIP, stretch_rows

        iw, ih = img.width(), img.height()
        side = self.hs.get("stretch_from") or "both"
        for reg, r in zip(regs, rects):
            x, y = int(float(reg["x"]) * iw), int(float(reg["y"]) * ih)
            w, h = max(1, int(float(reg["w"]) * iw)), max(2, int(float(reg["h"]) * ih))
            top, bot = stretch_rows(ih, y, h, side)
            layers = []
            for row in (top, bot):
                if row is None:
                    continue
                strip = img.copy(x, row, w, STRIP)
                if strip.isNull():
                    continue
                layers.append(strip.scaled(w, h, Qt.AspectRatioMode.IgnoreAspectRatio,
                                           Qt.TransformationMode.SmoothTransformation
                                           ).convertToFormat(QImage.Format.Format_ARGB32_Premultiplied))
            if not layers:
                continue
            part = layers[0]
            if len(layers) == 2:
                low = layers[1]
                q = QPainter(low)
                q.setCompositionMode(QPainter.CompositionMode.CompositionMode_DestinationIn)
                g = QLinearGradient(0, 0, 0, h)
                g.setColorAt(0, QColor(0, 0, 0, 0))
                g.setColorAt(1, QColor(0, 0, 0, 255))
                q.fillRect(0, 0, w, h, g)
                q.end()
                q = QPainter(part)
                q.drawImage(0, 0, low)
                q.end()
            mask = self._shape_mask(w, h)
            if mask is not None:
                self._apply_mask(part, mask)
            p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
            p.drawImage(r, part)

    def _src_size(self, reg: dict, img, r: QRectF) -> tuple[int, int]:
        """Cỡ vùng tính bằng điểm ảnh video gốc (bo góc / viền mờ tính theo đó, như khi xuất)."""
        if img is not None:
            return max(1, round(float(reg["w"]) * img.width())), max(1, round(float(reg["h"]) * img.height()))
        return max(1, round(r.width())), max(1, round(r.height()))

    def _shape_mask(self, w: int, h: int):
        """Mặt nạ bo góc + viền mờ dần (QImage alpha) – None nếu vùng vuông, không mờ viền."""
        from ..video import round_mask, shape_px

        rad, fea = shape_px(self.hs, w, h)
        if rad < 0.5 and fea < 0.5:
            return None
        key = (w, h, round(rad, 1), round(fea, 1))
        if getattr(self, "_mask_cache", (None,))[0] != key:
            import numpy as np

            a = (round_mask(w, h, rad, fea) * 255).astype(np.uint8)
            argb = np.zeros((h, w, 4), np.uint8)
            argb[..., 3] = a             # đen trong suốt dần (premultiplied: màu 0)
            img = QImage(argb.data, w, h, w * 4, QImage.Format.Format_ARGB32_Premultiplied).copy()
            self._mask_cache = (key, img)
        return self._mask_cache[1]

    @staticmethod
    def _apply_mask(img: QImage, mask: QImage):
        q = QPainter(img)
        q.setCompositionMode(QPainter.CompositionMode.CompositionMode_DestinationIn)
        q.drawImage(QRectF(img.rect()), mask)
        q.end()

    def _rect(self, r: dict) -> QRectF:
        br = self.boundingRect()
        return QRectF(br.width() * float(r["x"]), br.height() * float(r["y"]),
                      br.width() * float(r["w"]), br.height() * float(r["h"]))

    @staticmethod
    def _del_rect(rr: QRectF) -> QRectF:
        return QRectF(rr.right() - 16, rr.top() + 2, 14, 14)

    def paint(self, p: QPainter, opt, widget=None):
        regs = self.hs.get("regions") or []
        mode = self.hs.get("mode", "none")
        br = self.boundingRect()
        if not (self.hs.get(self.target) or []) and self.show_outline and br.width() > 40:
            p.setPen(QColor("#FFFFFF"))
            p.setFont(theme.font(10, QFont.Weight.Bold))
            r = QRectF(br.left(), br.top() + 8, br.width(), 28)
            p.fillRect(QRectF(r.center().x() - 190, r.top(), 380, r.height()), QColor(0, 0, 0, 150))
            p.drawText(r, Qt.AlignmentFlag.AlignCenter, "Kéo chuột trên video để khoanh " +
                       ("logo cố định cần gỡ" if self.target == "logos" else "vùng chữ cần xoá"))
        if not regs and not self.show_outline:
            return
        rects = [self._rect(r) for r in regs]
        if self.clean and mode == "inpaint":
            pass   # đang chiếu bản AI đã xoá thật
        elif mode == "box":
            c = QColor(self.hs.get("box_color", "#000000"))
            c.setAlphaF(max(0.0, min(1.0, float(self.hs.get("box_opacity", 1.0)))))
            img = self._frame_image()
            for reg, r in zip(regs, rects):
                sw, sh = self._src_size(reg, img, r)
                mask = self._shape_mask(sw, sh)
                if mask is None:
                    p.fillRect(r, c)
                    continue
                part = QImage(sw, sh, QImage.Format.Format_ARGB32_Premultiplied)
                part.fill(c)
                self._apply_mask(part, mask)
                p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
                p.drawImage(r, part)
        elif mode == "patch":
            img = self._frame_image()
            if img is not None:
                self._paint_patch(p, img, regs, rects)
        elif mode == "stretch":
            img = self._frame_image()
            if img is not None:
                self._paint_stretch(p, img, regs, rects)
        elif mode in ("blur", "inpaint", "delogo"):
            img = self._frame_image()
            if img is not None:
                blur = mode == "blur"
                k = 24 if not blur else max(2, int(self.hs.get("blur_strength", 20)) // 2)
                pixel = blur and self.hs.get("blur_kind") == "pixel"
                dk = max(0.0, min(0.8, float(self.hs.get("darken") or 0))) if blur else 0.0
                for r in rects:
                    src = QRectF(r.x() / br.width() * img.width(), r.y() / br.height() * img.height(),
                                 r.width() / br.width() * img.width(), r.height() / br.height() * img.height()).toRect()
                    part = img.copy(src)
                    if part.isNull():
                        continue
                    small = part.scaled(max(1, part.width() // k), max(1, part.height() // k),
                                        Qt.AspectRatioMode.IgnoreAspectRatio,
                                        Qt.TransformationMode.SmoothTransformation)
                    mask = self._shape_mask(part.width(), part.height()) if blur else None
                    if mask is None and not pixel and dk <= 0.005:
                        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
                        p.drawImage(r, small)
                        continue
                    # khảm ô vuông / phủ tối / bo góc + viền mờ dần: dựng ảnh đúng cỡ vùng rồi dán (như khi xuất)
                    big = small.scaled(part.width(), part.height(), Qt.AspectRatioMode.IgnoreAspectRatio,
                                       Qt.TransformationMode.FastTransformation if pixel else
                                       Qt.TransformationMode.SmoothTransformation
                                       ).convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)
                    if dk > 0.005:
                        q = QPainter(big)
                        q.fillRect(big.rect(), QColor(0, 0, 0, int(255 * dk)))
                        q.end()
                    if mask is not None:
                        self._apply_mask(big, mask)
                    p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, not pixel)
                    p.drawImage(r, big)
        if self.show_outline:
            from .editors import paint_handles

            # danh sách KHÔNG đang khoanh: chỉ viền mờ để biết vị trí
            other = "logos" if self.target == "regions" else "regions"
            pen2 = QPen(QColor(theme.MUTED), 1.2, Qt.PenStyle.DotLine)
            pen2.setCosmetic(True)
            for i, reg in enumerate(self.hs.get(other) or []):
                r = self._rect(reg)
                p.setPen(pen2)
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawRect(r)
                p.setFont(theme.font(8, QFont.Weight.Bold))
                p.drawText(QRectF(r.left() + 4, r.top() + 2, 80, 14), Qt.AlignmentFlag.AlignLeft,
                           f"Logo {i + 1}" if other == "logos" else f"Vùng {i + 1}")
            col = theme.ACCENT if self.target == "regions" else LOGO_COLOR
            pen = QPen(QColor(col), 1.5, Qt.PenStyle.DashLine)
            pen.setCosmetic(True)
            rects = [self._rect(r) for r in (self.hs.get(self.target) or [])]
            name = "Logo" if self.target == "logos" else "Vùng"
            for i, r in enumerate(rects):
                p.setPen(pen)
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawRect(r)
                paint_handles(p, r, col)
                if r.width() > 40 and r.height() > 14:
                    d = self._del_rect(r)
                    p.setPen(Qt.PenStyle.NoPen)
                    p.setBrush(QColor(col))
                    p.drawRoundedRect(d, 3, 3)
                    icons.paint_icon(p, "x", d.adjusted(3, 3, -3, -3), "#FFFFFF", 2.2)
                    p.setPen(QColor("#FFFFFF"))
                    p.setFont(theme.font(8, QFont.Weight.Bold))
                    p.drawText(QRectF(r.left() + 4, r.top() + 2, 60, 14), Qt.AlignmentFlag.AlignLeft, f"{name} {i + 1}")

    # ------------------------------------------------------------ chỉnh vùng bằng chuột
    def _hit(self, pos):
        from .editors import hit_region

        regs = self.hs.get(self.target) or []
        for i in range(len(regs) - 1, -1, -1):
            rr = self._rect(regs[i])
            if rr.width() > 40 and rr.height() > 14 and self._del_rect(rr).contains(pos):
                return i, "del"
            kind = hit_region(rr, pos)
            if kind:
                return i, kind
        return -1, ""

    def _rel(self, pos):
        br = self.boundingRect()
        return (min(1.0, max(0.0, pos.x() / max(1.0, br.width()))),
                min(1.0, max(0.0, pos.y() / max(1.0, br.height()))))

    def hoverMoveEvent(self, e):
        from .editors import REGION_CURSORS

        hit = self._hit(e.pos())
        if hit != self._hover:
            self._hover = hit
            self.setCursor(REGION_CURSORS.get(hit[1], Qt.CursorShape.CrossCursor))

    def mousePressEvent(self, e):
        if not self.show_outline:
            e.ignore()
            return
        regs = [dict(r) for r in (self.hs.get(self.target) or [])]
        i, kind = self._hit(e.pos())
        x, y = self._rel(e.pos())
        if kind == "del":
            del regs[i]
            self.hs = {**self.hs, self.target: regs}
            self.update()
            if self.on_changed:
                self.on_changed(regs)
            e.accept()
            return
        if kind:
            self._drag = (kind, i, dict(regs[i]), (x, y))
        else:
            regs.append({"x": x, "y": y, "w": 0.0, "h": 0.0})
            self._drag = ("new", len(regs) - 1, None, (x, y))
        self.hs = {**self.hs, self.target: regs}
        self.update()
        e.accept()

    def mouseMoveEvent(self, e):
        if not self._drag:
            return
        from .editors import drag_region

        kind, i, r0, (x0, y0) = self._drag
        x, y = self._rel(e.pos())
        regs = self.hs[self.target]
        if kind == "new":
            regs[i] = {"x": min(x, x0), "y": min(y, y0), "w": abs(x - x0), "h": abs(y - y0)}
        else:
            regs[i] = drag_region(r0, kind, x - x0, y - y0)
        self.update()

    def mouseReleaseEvent(self, e):
        if not self._drag:
            return
        self._drag = None
        regs = [{k: round(float(v), 4) for k, v in r.items()} for r in (self.hs.get(self.target) or [])
                if float(r["w"]) > 0.01 and float(r["h"]) > 0.01]
        self.hs = {**self.hs, self.target: regs}
        self.update()
        if self.on_changed:
            self.on_changed(regs)


class BackdropItem(QGraphicsItem):
    """Nền mờ thật khi đổi tỉ lệ khung (như video xuất): khung hình đang chiếu thu nhỏ rồi phóng to phủ kín khung."""

    def __init__(self, video, parent):
        super().__init__(parent)
        self.video = video
        self.rect = QRectF()
        self.on = False
        self._img: QImage | None = None
        self._last = 0.0
        self._connected = False
        self.setZValue(-1)
        self.setAcceptedMouseButtons(Qt.MouseButton.NoButton)

    def boundingRect(self):
        return self.rect

    def set_state(self, on: bool, rect: QRectF):
        self.prepareGeometryChange()
        self.rect = QRectF(rect)
        self.on = on
        sink = self.video.videoSink()
        if on and not self._connected:
            sink.videoFrameChanged.connect(self._on_frame)
            self._connected = True
            self._on_frame(sink.videoFrame(), force=True)
        elif not on and self._connected:
            try:
                sink.videoFrameChanged.disconnect(self._on_frame)
            except (RuntimeError, TypeError):
                pass
            self._connected = False
            self._img = None
        self.update()

    def _on_frame(self, frame, force: bool = False):
        now = time.monotonic()
        if not force and now - self._last < 0.12:     # ~8 lần/giây là đủ cho nền mờ
            return
        if frame is None or not frame.isValid():
            return
        self._last = now
        img = frame.toImage()
        if img.isNull():
            return
        small = img.scaled(40, max(2, int(40 * img.height() / max(1, img.width()))),
                           Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation)
        self._img = small.scaled(small.width() * 2, small.height() * 2, Qt.AspectRatioMode.IgnoreAspectRatio,
                                 Qt.TransformationMode.SmoothTransformation)
        self.update()

    def paint(self, p: QPainter, opt, widget=None):
        if not self.on or self._img is None:
            return
        r, img = self.rect, self._img
        k = max(r.width() / img.width(), r.height() / img.height())
        w, h = img.width() * k, img.height() * k
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        p.drawImage(QRectF(r.center().x() - w / 2, r.center().y() - h / 2, w, h), img)
        p.fillRect(r, QColor(0, 0, 0, 40))


class LayerItem(QGraphicsItem):
    """1 lớp phủ (chữ / logo) trên khung xem trước – ảnh vẽ bằng đúng hàm của video xuất (trami/overlay.py).
    Kéo để dời, kéo 4 góc để phóng to / thu nhỏ; thả chuột -> on_changed(mã lớp, {khoá: giá trị})."""
    HANDLE = 9

    def __init__(self, view: "VideoView", layer: dict):
        super().__init__(view.canvas)
        self.view = view
        self.layer = dict(layer)
        self._img: QImage | None = None
        self._key = None
        self._o = (0.0, 0.0)
        self._state = (0.0, 0.0, 1.0)    # (mép trái, dời y, độ hiện) theo điểm ảnh khung xuất – hiệu ứng
        self._drag = None
        self._hover = ""
        self._movie = None
        self._frame = None
        self.selected = False
        self.editable = True
        self.setAcceptHoverEvents(True)
        self.setZValue(5)

    # ---------------------------------------------------------------- dữ liệu
    def set_layer(self, layer: dict):
        from ..overlay import LOOK_KEYS

        if self._drag:     # đang kéo: không để bản lưu cũ ghi đè chỗ đang kéo
            keep = {k: self.layer.get(k) for k in ("x", "y", "size", "w")}
            layer = {**layer, **keep}
        if any(layer.get(k) != self.layer.get(k) for k in LOOK_KEYS):
            self._img = None       # đổi hình dạng (chữ, font, màu, cỡ…) -> vẽ lại ảnh; chỉ dời chỗ thì không
        self.layer = dict(layer)
        self._ensure_movie()
        self.prepareGeometryChange()
        self.update()

    def _ensure_movie(self):
        from .. import overlay

        path = self.layer.get("path") if self.layer.get("kind") == "logo" else ""
        anim = bool(path) and overlay.is_animated(path)
        if anim and (self._movie is None or self._movie.fileName() != path):
            from PySide6.QtGui import QMovie

            self._movie = QMovie(path)
            self._movie.frameChanged.connect(self._movie_frame)
            self._movie.start()
        elif not anim and self._movie is not None:
            self._movie.stop()
            self._movie = None
            self._frame = None

    def _movie_frame(self, _n):
        if self._movie is not None and self.isVisible():
            self._frame = self._movie.currentImage()
            self._img = None
            self.update()

    def stop(self):
        if self._movie is not None:
            self._movie.stop()
            self._movie = None

    def _scale(self) -> float:
        return self.view.canvas.rect().width() / max(1, self.view.out_w)

    def _image(self):
        from .. import overlay

        TW, TH = self.view.out_w, self.view.out_h
        sc = self._scale()
        key = (TW, TH, round(sc, 4))
        if key != self._key or self._img is None:
            try:
                self._img, ox, oy = overlay.render(self.layer, TW, TH, sc, self._frame)
                self._o = (ox, oy)
            except Exception:  # noqa: BLE001 – ảnh / font lỗi: không làm hỏng trình phát
                self._img = QImage()
                self._o = (0.0, 0.0)
            self._key = key
        return self._img

    def _rect(self) -> QRectF:
        img = self._image()
        fr = self.view.canvas.rect()
        sc = self._scale()
        cx = fr.left() + float(self.layer.get("x", 0.5)) * fr.width()
        cy = fr.top() + float(self.layer.get("y", 0.5)) * fr.height()
        left = cx - self._o[0]
        if not img or img.isNull():
            return QRectF(cx - 20, cy - 10, 40, 20)
        x_out, dy, _a = self._state
        if self.layer.get("anim") in ("scroll",) or str(self.layer.get("anim", "")).startswith("slide"):
            left = fr.left() + x_out * sc
        return QRectF(left, cy - self._o[1] + dy * sc, img.width(), img.height())

    def set_time(self, rel: float | None, length: float):
        """rel = giây kể từ lúc lớp hiện trên video xuất (None = không hiện lúc này)."""
        from .. import overlay

        vis = rel is not None and not self.layer.get("hidden")
        if vis:
            img = self._image()
            fr = self.view.canvas.rect()
            sc = self._scale() or 1
            cx = float(self.layer.get("x", 0.5)) * self.view.out_w
            left = cx - self._o[0] / sc
            st = overlay.anim_state(self.layer, rel, length, self.view.out_w, self.view.out_h,
                                    (img.width() if img else 0) / sc, (img.height() if img else 0) / sc, left)
            if st != self._state:
                self.prepareGeometryChange()
                self._state = st
            self.setOpacity(max(0.0, min(1.0, st[2])))
            _ = fr
        if vis != self.isVisible():
            self.setVisible(vis)

    def boundingRect(self):
        return self._rect().adjusted(-self.HANDLE, -self.HANDLE, self.HANDLE, self.HANDLE)

    def shape(self):
        path = QPainterPath()
        path.addRect(self._rect().adjusted(-4, -4, 4, 4))
        return path

    def paint(self, p: QPainter, opt, widget=None):
        img = self._image()
        r = self._rect()
        if img and not img.isNull():
            p.drawImage(r.topLeft(), img)
        if self.editable and (self.selected or self._hover or self._drag):
            pen = QPen(QColor(theme.ACCENT if self.selected else "#FFFFFF"), 1.4, Qt.PenStyle.DashLine)
            pen.setCosmetic(True)
            p.setPen(pen)
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRect(r)
            if self.selected:
                p.setPen(QPen(QColor(theme.ACCENT), 1.5))
                p.setBrush(QColor("#FFFFFF"))
                for c in (r.topLeft(), r.topRight(), r.bottomLeft(), r.bottomRight()):
                    p.drawRoundedRect(QRectF(c.x() - 4.5, c.y() - 4.5, 9, 9), 2, 2)

    # ---------------------------------------------------------------- chuột
    def _hit(self, pos) -> str:
        r = self._rect()
        if self.selected:
            for k, c in (("TL", r.topLeft()), ("TR", r.topRight()), ("BL", r.bottomLeft()), ("BR", r.bottomRight())):
                if abs(pos.x() - c.x()) <= self.HANDLE and abs(pos.y() - c.y()) <= self.HANDLE:
                    return k
        return "move" if r.adjusted(-4, -4, 4, 4).contains(pos) else ""

    def hoverMoveEvent(self, e):
        from .editors import REGION_CURSORS

        if not self.editable:
            return
        k = self._hit(e.pos())
        if k != self._hover:
            self._hover = k
            if k:
                self.setCursor(REGION_CURSORS[k])
            else:
                self.unsetCursor()
            self.update()

    def hoverLeaveEvent(self, e):
        self._hover = ""
        self.unsetCursor()
        self.update()

    def mousePressEvent(self, e):
        if not self.editable or e.button() != Qt.MouseButton.LeftButton:
            e.ignore()
            return
        k = self._hit(e.pos())
        if not k:
            e.ignore()
            return
        if self.view.on_layer_select:
            self.view.on_layer_select(self.layer.get("id"))
        r = self._rect()
        size_key = "w" if self.layer.get("kind") == "logo" else "size"
        self._drag = (k, e.scenePos(), float(self.layer.get("x", 0.5)), float(self.layer.get("y", 0.5)),
                      float(self.layer.get(size_key) or 0.06), r.center(), size_key)
        e.accept()

    def mouseMoveEvent(self, e):
        if not self._drag:
            return
        k, p0, x0, y0, s0, c0, size_key = self._drag
        fr = self.view.canvas.rect()
        if k == "move":
            d = e.scenePos() - p0
            x, y = x0 + d.x() / fr.width(), y0 + d.y() / fr.height()
            snap = 7 / max(1.0, fr.width())
            if abs(x - 0.5) < snap:     # hít vào giữa khung
                x = 0.5
            if abs(y - 0.5) < 7 / max(1.0, fr.height()):
                y = 0.5
            self.layer["x"], self.layer["y"] = round(min(1.05, max(-0.05, x)), 4), round(min(1.05, max(-0.05, y)), 4)
        else:
            dist0 = max(4.0, (p0 - c0).manhattanLength())
            f = max(0.1, (e.scenePos() - c0).manhattanLength() / dist0)
            lo, hi = (0.02, 1.5) if size_key == "w" else (0.01, 0.4)
            self.layer[size_key] = round(min(hi, max(lo, s0 * f)), 4)
            self._img = None
        self.prepareGeometryChange()
        self.update()

    def mouseReleaseEvent(self, e):
        if not self._drag:
            return
        k, *_r, size_key = self._drag
        self._drag = None
        ch = {"x": self.layer["x"], "y": self.layer["y"]} if k == "move" else {size_key: self.layer[size_key]}
        if self.view.on_layer_changed:
            self.view.on_layer_changed(self.layer.get("id"), ch)
        self.update()


class SafeZoneItem(QGraphicsItem):
    """Vùng bị nút bấm / tên kênh / chú thích của TikTok, Reels, Shorts che – tô đỏ mờ để tránh đặt chữ vào đó."""

    def __init__(self, parent):
        super().__init__(parent)
        self.rect = QRectF()
        self.setZValue(50)
        self.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        self.setVisible(False)

    def boundingRect(self):
        return self.rect

    def set_rect(self, r: QRectF):
        self.prepareGeometryChange()
        self.rect = QRectF(r)

    def zones(self) -> list[QRectF]:
        from ..video import SAFE_ZONE

        r = self.rect
        top = QRectF(r.left(), r.top(), r.width(), r.height() * SAFE_ZONE["top"])
        bot = QRectF(r.left(), r.bottom() - r.height() * SAFE_ZONE["bottom"], r.width(), r.height() * SAFE_ZONE["bottom"])
        right = QRectF(r.right() - r.width() * SAFE_ZONE["right"], top.bottom(), r.width() * SAFE_ZONE["right"],
                       bot.top() - top.bottom())
        return [top, bot, right]

    def paint(self, p: QPainter, opt, widget=None):
        z = self.zones()
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(239, 68, 68, 60))
        for r in z:
            p.drawRect(r)
        pen = QPen(QColor(248, 113, 113, 220), 1.2, Qt.PenStyle.DashLine)
        pen.setCosmetic(True)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        safe = QRectF(self.rect.left(), z[0].bottom(), self.rect.width() - z[2].width(), z[1].top() - z[0].bottom())
        p.drawRect(safe)
        p.setPen(QColor(255, 230, 230, 230))
        p.setFont(theme.font(7.5))
        p.drawText(z[0].adjusted(6, 0, -6, 0), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                   "Vùng bị nút / tên kênh che (TikTok · Reels · Shorts)")
        p.drawText(z[1].adjusted(6, 0, -6, 0), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                   "Chú thích + nhạc của TikTok / Reels che vùng này")


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
        self.backdrop = BackdropItem(self.video, self.canvas)
        self.safe = SafeZoneItem(self.canvas)
        self.layers: dict[str, LayerItem] = {}   # lớp phủ chữ / logo theo mã lớp
        self.layer_sel = ""
        self.layers_editable = True
        self.on_layer_select = None    # bấm 1 lớp trên video -> on_layer_select(mã)
        self.on_layer_changed = None   # kéo / phóng lớp xong -> on_layer_changed(mã, {khoá: giá trị})
        self.on_drop = None            # thả logo từ thư viện vào video -> on_drop(dữ liệu, x 0..1, y 0..1)
        self.setAcceptDrops(True)
        self.hs = HardsubOverlay(self.video)
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
        self.out_w = 1920
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
        self.hs.set_hardsub(self.edit.get("hardsub") or {})
        self._fit()

    def _fit(self):
        from ..video import aspect_wh, fit_geometry, target_size

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
        ar = aspect_wh(aspect)
        aw, ah = ar if ar else (rw, rh)
        # khung đầu ra vừa khít ô xem
        k = min(w / aw, h / ah)
        fw, fh = aw * k, ah * k
        frame = QRectF((w - fw) / 2, (h - fh) / 2, fw, fh)
        mode = e.get("aspect_mode", "blur")
        self.canvas.setRect(frame)
        bg = (e.get("aspect_color") or "#000000") if (ar and mode == "color") else \
            "#000" if (mode in ("pad", "crop") or not ar) else "#20232b"
        self.canvas.setBrush(QBrush(QColor(bg)))
        if ar:   # cùng cách đặt phim vào khung như khi xuất (cách lấp, phóng, vị trí)
            gx, gy, gw, gh = fit_geometry(rw, rh, fw, fh, e)
            content = QRectF(frame.left() + gx, frame.top() + gy, gw, gh)
            sc = gw / rw
        else:
            sc = min(fw / rw, fh / rh)
            content = QRectF(frame.center().x() - rw * sc / 2, frame.center().y() - rh * sc / 2, rw * sc, rh * sc)
        self.clip.setRect(content)
        covered = content.contains(frame.adjusted(1, 1, -1, -1))      # phim phủ kín khung -> không cần nền
        self.backdrop.set_state(bool(ar) and mode == "blur" and not covered, frame)
        self.safe.set_rect(frame)
        # đặt video: tâm vùng crop -> tâm nội dung, lật, xoay, phóng
        kk = sc * zoom
        self.hs.prepareGeometryChange()
        self.video.setSize(QSizeF(vs.width() * kk, vs.height() * kk))
        self.video.setPos(0, 0)
        ccx, ccy = (cx + cw / 2) * vs.width() * kk, (cy + ch / 2) * vs.height() * kk
        t = QTransform.fromTranslate(-ccx, -ccy)
        t = t * QTransform.fromScale(-1 if e.get("flip_h") else 1, -1 if e.get("flip_v") else 1)
        t = t * QTransform().rotate(rot)
        t = t * QTransform.fromTranslate(content.center().x(), content.center().y())
        self.video.setTransform(t)
        # kích thước khung đầu ra thật (phụ đề, lớp phủ tính theo nó)
        if self.src_w and self.src_h:
            sw, sh = self.src_w * cw, self.src_h * ch
            if rot in (90, 270):
                sw, sh = sh, sw
            self.out_w, self.out_h = target_size(int(sw), int(sh), aspect, e.get("resolution", "original"))
        else:
            self.out_w, self.out_h = int(round(1080 * aw / ah)) // 2 * 2, 1080
        for it in self.layers.values():
            it.prepareGeometryChange()
            it.update()
        if self.on_layout:
            self.on_layout()

    # ------------------------------------------------------------ lớp phủ chữ / logo
    def set_layers(self, layers: list):
        """Đồng bộ các lớp phủ với danh sách (thêm / bỏ / đổi thứ tự chồng)."""
        ids = []
        for z, L in enumerate(layers or []):
            lid = L.get("id")
            if not lid:
                continue
            ids.append(lid)
            it = self.layers.get(lid)
            if it is None:
                it = self.layers[lid] = LayerItem(self, L)
                it.set_layer(L)
            elif it.layer != L:
                it.set_layer(L)
            it.setZValue(5 + z * 0.01)
            it.selected = lid == self.layer_sel
            it.editable = self.layers_editable
            it.setAcceptHoverEvents(self.layers_editable)
            it.setAcceptedMouseButtons(Qt.MouseButton.LeftButton if self.layers_editable else Qt.MouseButton.NoButton)
        for lid in [k for k in self.layers if k not in ids]:
            it = self.layers.pop(lid)
            it.stop()
            it.setParentItem(None)
            self.scene_.removeItem(it)

    def select_layer(self, lid: str):
        self.layer_sel = lid or ""
        for k, it in self.layers.items():
            if it.selected != (k == self.layer_sel):
                it.selected = k == self.layer_sel
                it.update()

    def set_safe_zone(self, on: bool):
        self.safe.setVisible(bool(on))

    def safe_issues(self) -> list[str]:
        """Chữ nằm trong vùng bị nút / chú thích TikTok – Reels che: ["phụ đề", "Chữ · …"]."""
        from .. import overlay

        zones = self.safe.zones()
        out = []
        g = self.sub._geometry() if self.sub.parts or self.sub.show_frame else None
        if g and any(z.intersects(g["outer"]) for z in zones):
            out.append("phụ đề")
        for it in self.layers.values():
            if it.isVisible() and any(z.intersects(it._rect()) for z in zones):
                out.append(overlay.text_name(it.layer))
        return out

    def dragEnterEvent(self, e):
        if e.mimeData().hasFormat("application/x-subvideo-logo") or e.mimeData().hasUrls():
            e.acceptProposedAction()
        else:
            super().dragEnterEvent(e)

    def dragMoveEvent(self, e):
        if e.mimeData().hasFormat("application/x-subvideo-logo") or e.mimeData().hasUrls():
            e.acceptProposedAction()
        else:
            super().dragMoveEvent(e)

    def dropEvent(self, e):
        md = e.mimeData()
        fr = self.canvas.rect()
        pt = self.mapToScene(e.position().toPoint())
        rx = (pt.x() - fr.left()) / max(1.0, fr.width())
        ry = (pt.y() - fr.top()) / max(1.0, fr.height())
        data = None
        if md.hasFormat("application/x-subvideo-logo"):
            data = bytes(md.data("application/x-subvideo-logo")).decode("utf-8")
        elif md.hasUrls():
            data = "file:" + md.urls()[0].toLocalFile()
        if data and self.on_drop and 0 <= rx <= 1 and 0 <= ry <= 1:
            self.on_drop(data, rx, ry)
            e.acceptProposedAction()

    def video_rect(self) -> QRectF:
        return self.canvas.rect()

    def set_tool(self, regions: bool, sub_frame: bool = False):
        """regions: tab Xoá phụ đề – chuột chỉnh vùng che; còn lại chuột kéo phụ đề (sub_frame: tab Kiểu chữ) và lớp
        phủ chữ / logo."""
        self.hs.set_editing(regions)
        self.sub.show_frame = sub_frame and not regions
        self.sub.set_editable(not regions)
        self.layers_editable = not regions
        for it in self.layers.values():
            it.editable = not regions
            it.setAcceptHoverEvents(not regions)
            it.setAcceptedMouseButtons(Qt.MouseButton.NoButton if regions else Qt.MouseButton.LeftButton)
            it.update()


class _Feeder(QObject):
    """Sống ở LUỒNG RIÊNG: tạo QAudioSink và đẩy PCM của bộ trộn vào loa theo nhịp 10 ms. Giao diện bận (cuộn bảng câu,
    vẽ timeline khi chuyển câu, nạp xong tác vụ) không làm loa cạn tiếng. 2.0.38: trước đây đẩy trên luồng giao diện,
    đệm 0,2 s -> chuyển câu là cạn tiếng -> nhảy theo hình -> mất chữ cuối câu, câu sau đọc luôn."""

    def __init__(self, live: "LiveAudio"):
        super().__init__()
        self.live = live
        self.sink = None
        self.io = None
        self.timer = None
        self.pos = 0                      # khung (theo thời gian video gốc) đã đưa vào loa

    def _ensure(self):
        if self.sink is not None:
            return
        from PySide6.QtMultimedia import QAudioFormat, QAudioSink, QMediaDevices

        fmt = QAudioFormat()
        fmt.setSampleRate(self.live.SR)
        fmt.setChannelCount(2)
        fmt.setSampleFormat(QAudioFormat.SampleFormat.Int16)
        self.sink = QAudioSink(QMediaDevices.defaultAudioOutput(), fmt, self)
        self.sink.setBufferSize(int(self.live.SR * 4 * self.live.BUFFER))
        self.sink.setVolume(self.live.volume)
        self.timer = QTimer(self)
        self.timer.setTimerType(Qt.TimerType.PreciseTimer)
        self.timer.setInterval(10)
        self.timer.timeout.connect(self._feed)

    @Slot(float)
    def begin(self, t: float):
        self._ensure()
        self.sink.stop()
        self.pos = int(max(0.0, t) * self.live.SR)
        self.io = self.sink.start()
        self._feed()
        self.timer.start()

    @Slot()
    def end(self):
        if self.timer is not None:
            self.timer.stop()
        if self.sink is not None:
            self.sink.stop()
        self.io = None

    @Slot(float)
    def volume(self, v: float):
        if self.sink is not None:
            self.sink.setVolume(v)

    def _feed(self):
        mix = self.live.mix
        if self.io is None or mix is None or self.sink is None:
            return
        from ..audiomix import to_pcm16

        free = self.sink.bytesFree() // 4
        if free >= 512:
            a, adv = mix.render(self.pos, min(free, int(self.live.SR * 0.1)))
            self.io.write(to_pcm16(a))
            self.pos += adv
        buffered = (self.sink.bufferSize() - self.sink.bytesFree()) / 4
        self.live._audible = (self.pos - buffered * (mix.rate or 1.0)) / self.live.SR


class _Bridge(QObject):
    begin = Signal(float)
    end = Signal()
    volume = Signal(float)


class LiveAudio:
    """Phát bộ trộn trực tiếp (trami/audiomix.LiveMix) ra loa, bám theo vị trí video: đổi âm lượng / tắt lớp / nghe
    riêng nghe thấy ngay (~0,3 s). Việc đẩy tiếng chạy ở luồng riêng (_Feeder); lệch hình quá 0,25 s thì nhảy lại
    đúng chỗ video."""
    BUFFER = 0.3      # giây đệm trong loa (đổi âm lượng có tác dụng sau tối đa chừng này)
    RESYNC = 0.25     # lệch tiếng / hình quá chừng này mới nhảy (nhảy = bỏ qua 1 đoạn tiếng)

    def __init__(self, owner: QWidget):
        from ..audiomix import SR

        self.SR = SR
        self.owner = owner
        self.mix = None
        self.volume = 0.8
        self.failed = False              # máy không có thiết bị phát âm thanh
        self.running = False
        self._audible = 0.0              # giây video đang phát ra loa (luồng phát cập nhật)
        self._thread = None
        self._feeder = None
        self._bridge = None

    def ok(self) -> bool:
        return self.mix is not None and bool(self.mix.tracks) and not self.failed

    def ready(self) -> bool:
        """Có loa -> dựng luồng phát (1 lần). Không có loa -> False (video tự phát tiếng của nó)."""
        if self._thread is not None:
            return True
        if self.failed:
            return False
        from PySide6.QtCore import QCoreApplication, QThread
        from PySide6.QtMultimedia import QMediaDevices

        if QMediaDevices.defaultAudioOutput().isNull():
            self.failed = True
            return False
        self._thread = QThread()
        self._thread.setObjectName("trami-audio")
        self._feeder = _Feeder(self)
        self._feeder.moveToThread(self._thread)
        self._bridge = _Bridge()
        self._bridge.begin.connect(self._feeder.begin)
        self._bridge.end.connect(self._feeder.end)
        self._bridge.volume.connect(self._feeder.volume)
        self._thread.start(QThread.Priority.TimeCriticalPriority)
        app = QCoreApplication.instance()
        if app is not None:
            app.aboutToQuit.connect(self.shutdown)
        return True

    def start(self, t: float):
        if not self.ok() or not self.ready():
            return
        self.running = True
        self._audible = max(0.0, t)
        self._bridge.begin.emit(float(t))

    def stop(self):
        self.running = False
        if self._bridge is not None:
            self._bridge.end.emit()

    def shutdown(self):
        if self._thread is not None:
            self.stop()
            self._thread.quit()
            self._thread.wait(2000)
            self._thread = None

    def set_volume(self, v: float):
        self.volume = v
        if self._bridge is not None:
            self._bridge.volume.emit(float(v))

    def audible(self) -> float:
        """Giây video đang phát ra loa (trừ phần còn nằm trong bộ đệm)."""
        return self._audible

    def sync(self, t: float):
        if self.running and abs(self.audible() - t) > self.RESYNC:
            self.start(t)


class Player(QWidget):
    positionChanged = Signal(float)   # giây
    mediaError = Signal(str)
    subtitleEdited = Signal(dict)     # kéo phụ đề / nền hộp trên video -> các khoá kiểu chữ mới
    regionsEdited = Signal(list)      # kéo / vẽ / xoá vùng che phụ đề cũ trên video

    def __init__(self, parent=None):
        super().__init__(parent)
        from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer

        self.view = VideoView()
        self.mp = QMediaPlayer(self)
        self.audio = QAudioOutput(self)
        self.mp.setAudioOutput(self.audio)
        self.mp.setVideoOutput(self.view.video)
        # âm thanh trộn trực tiếp các lớp (tiếng gốc / nhạc nền / giọng gốc / lồng tiếng / nhạc thêm) theo tab Âm thanh
        self.live = LiveAudio(self)
        self.source = ""
        self.segments: list = []
        self.style: dict = {}
        self.which = "trans"
        self.show_subs = True
        self.video_h = 1080
        self._dur = 0.0
        # karaoke: đoạn giọng lồng từng câu (số câu -> [[giây, giây]]) + đồng hồ nội suy để chữ quét mượt khi phát
        self.kara_spans: dict = {}
        self._kara_cache: dict = {}
        self._clock = (0.0, 0.0)
        self._kara_timer = QTimer(self, interval=33, timeout=self._kara_tick)
        self._anim_timer = QTimer(self, interval=33, timeout=self._anim_tick)   # lớp phủ có hiệu ứng (trượt / chạy)
        self.speed = 1.0

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
        self.vol.valueChanged.connect(self._set_volume)
        self.mp.positionChanged.connect(self._pos)
        self.mp.durationChanged.connect(lambda d: setattr(self, "_dur", d / 1000))
        self.mp.playbackStateChanged.connect(self._state)
        self.mp.errorOccurred.connect(lambda err, msg: self.mediaError.emit(msg or str(err)))
        self.fps = 25.0
        self.keep: list = []        # các đoạn được giữ (sau cắt đầu/cuối, xoá đoạn giữa)
        self.view.on_layout = lambda: self.update_sub()
        self.view.sub.on_changed = self.subtitleEdited.emit
        self.view.hs.on_changed = self.regionsEdited.emit

    # ------------------------------------------------------------ điều khiển
    def _set_volume(self, v: int):
        self.audio.setVolume(v / 100)
        self.live.set_volume(v / 100)

    def set_live_mix(self, mix):
        """Dùng bộ trộn trực tiếp thay cho tiếng của file video (None = tiếng gốc của file). Giữ nguyên vị trí phát."""
        self.live.stop()
        self.live.mix = mix
        if mix is not None:
            mix.rate = self.mp.playbackRate() or 1.0
        on = self.live.ok() and self.live.ready()
        self.audio.setMuted(on)
        if on and self.playing():
            self.live.start(self.position())

    def load(self, path: str, duration: float = 0.0, fps: float = 25.0, height: int = 1080, start: float = 0.0):
        self.mp.stop()
        self.live.stop()
        self._dur = duration
        self.fps = fps or 25
        self.video_h = height or 1080
        self.source = path
        self.mp.setSource(QUrl.fromLocalFile(path) if path else QUrl())
        # hiện khung đầu (hoặc khung đang xem dở khi đổi sang bản video khác)

        def first():
            if path:
                self.mp.pause()
                if start > 0:
                    self.seek(start)
        QTimer.singleShot(300, first)

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
        if self.playing() and self.live.ok():
            self.live.start(t)
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
        if self.playing() and self.live.ok():
            self.live.start(self.position())
        else:
            self.live.stop()
        self._clock = (self.position(), time.monotonic())
        if self.playing() and self.style.get("karaoke"):
            self._kara_timer.start()
        else:
            self._kara_timer.stop()
            self.update_sub(self.position())
        self.update_layers(self.position())

    def set_edit(self, edit: dict, src_w: int = 0, src_h: int = 0, duration: float = 0.0):
        """Áp các chỉnh sửa (khung hình, màu, tốc độ, đoạn cắt) lên trình phát để xem ngay."""
        from ..video import keep_ranges

        edit = edit or {}
        self.view.set_edit(edit, src_w, src_h)
        self.view.set_layers(edit.get("layers") or [])
        rate = min(4.0, max(0.25, float(edit.get("speed") or 1.0)))
        self.speed = rate
        if abs(self.mp.playbackRate() - rate) > 1e-3:
            self.mp.setPlaybackRate(rate)
        if self.live.mix is not None:
            self.live.mix.rate = rate
        dur = duration or self._dur
        try:
            self.keep = keep_ranges(dur, edit.get("trim"), edit.get("cuts")) if dur else []
        except Exception:
            self.keep = []
        if self.keep == [(0.0, dur)] or (len(self.keep) == 1 and self.keep[0][0] <= 0.001
                                         and abs(self.keep[0][1] - dur) < 0.01):
            self.keep = []
        self.update_layers()

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
        self._clock = (t, time.monotonic())
        if self._skip_cut(t):
            return
        if self.playing() and self.live.ok():
            self.live.sync(t)       # bù lệch tiếng / hình
        if self._dur and not self.slider.isSliderDown():
            self.slider.blockSignals(True)
            self.slider.setValue(int(t / self._dur * 1000))
            self.slider.blockSignals(False)
        self.time.setText(f"{fmt(t)} / {fmt(self._dur, False)}")
        self.update_sub(t)
        self.update_layers(t)
        self.positionChanged.emit(t)

    # ------------------------------------------------------------ phụ đề
    def set_subtitles(self, segments: list, style: dict, which: str = "trans", kara_spans: dict | None = None):
        self.segments, self.style, self.which = segments, style, which
        if kara_spans is not None:
            self.kara_spans = kara_spans
        self._kara_cache = {}
        self.view.sub.clear_override()
        if self.playing() and style.get("karaoke"):
            self._kara_timer.start()
        elif not style.get("karaoke"):
            self._kara_timer.stop()
        self.update_sub(self.position())

    # ------------------------------------------------------------ lớp phủ chữ / logo
    def update_layers(self, t: float | None = None):
        """Hiện / ẩn từng lớp phủ theo đầu phát + hiệu ứng (giờ tính theo video XUẤT: bỏ đoạn cắt, tính tốc độ)."""
        from .. import overlay
        from ..video import out_time

        if not self.view.layers:
            return
        t = self.position() if t is None else t
        dur = self._dur or 0.0
        keep = self.keep or [(0.0, dur or 1e9)]
        in_keep = not self.keep or any(a - 0.02 <= t < b for a, b in self.keep)
        any_anim = False
        for it in self.view.layers.values():
            L = it.layer
            a, b = overlay.src_span(L, dur or 1e9)
            if a <= t < b and in_keep:
                A = out_time(a, keep, self.speed)
                it.set_time(out_time(t, keep, self.speed) - A, out_time(b, keep, self.speed) - A)
            else:
                it.set_time(None, 0)
            any_anim = any_anim or (L.get("anim") or "none") != "none" or bool(L.get("fade_in") or L.get("fade_out"))
        if self.playing() and any_anim and not self._anim_timer.isActive():
            self._anim_timer.start()

    def _anim_tick(self):
        if not self.playing():
            self._anim_timer.stop()
            return
        t0, at = self._clock
        self.update_layers(t0 + min(0.5, time.monotonic() - at) * self.mp.playbackRate())

    def _kara_tick(self):
        """Khi phát: vẽ lại phụ đề ~30 lần/giây theo đồng hồ nội suy (vị trí video chỉ báo vài lần/giây)."""
        if not self.playing():
            self._kara_timer.stop()
            return
        t0, at = self._clock
        self.update_sub(t0 + min(0.5, time.monotonic() - at) * self.mp.playbackRate())

    def _karaoke(self, i: int, s, t: float) -> list[float] | None:
        from ..karaoke import progress, word_times

        text = s.text if self.which == "source" else s.trans
        key = (i, text, s.start, s.end)
        times = self._kara_cache.get(key)
        if times is None:
            v = self.kara_spans.get(i) or {}
            if isinstance(v, list):
                v = {"kv": v}
            times = self._kara_cache[key] = word_times(" ".join((text or "").split()) if self.which != "source"
                                                       else text, s.start, s.end, v.get("kv"),
                                                       v.get("kw") if self.which != "source" else None)
        return progress(times, t, self.style.get("karaoke_mode", "sweep"))

    def update_sub(self, t: float | None = None):
        t = self.position() if t is None else t
        parts, kara = [], None
        if self.show_subs:
            for i, s in enumerate(self.segments):
                if s.start <= t < s.end:
                    if self.which == "source":
                        parts = [(s.text, "src")]
                    elif self.which == "both":
                        parts = [(s.trans, "vi"), (s.text, "zh")]
                    else:
                        parts = [(s.trans, "vi")]
                    if self.style.get("karaoke"):
                        kara = self._karaoke(i, s, t)
                    break
        parts = [(x, k) for x, k in parts if (x or "").strip()]
        self.view.sub.set(parts, self.view.video_rect(), self.style, self.view.out_h, kara)
