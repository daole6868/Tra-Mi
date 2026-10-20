"""Trình phát video (QtMultimedia) có lớp phụ đề xem trước vẽ bằng QPainter."""
from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, QSizeF, Qt, QUrl, Signal, QTimer
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


_ASS_METRICS: dict = {}
REF_PX = 200        # vẽ chữ ở cỡ chuẩn rồi phóng theo tỉ lệ -> cỡ chữ lẻ vẫn chính xác


def ass_font(family: str, bold: bool) -> tuple[QFont, float, float]:
    """Font Qt + hệ số để vẽ đúng cỡ như libass khi xuất. libass coi Fontsize = winAscent + winDescent
    (bảng OS/2) nên chữ nhỏ hơn cỡ điểm ảnh cùng số (Be Vietnam Pro: ~0,65 lần).
    Trả về (font cỡ REF_PX, em / Fontsize, phần trên đường chân chữ / chiều cao dòng)."""
    key = (family, bold)
    f = QFont(family)
    f.setBold(bold)
    f.setPixelSize(REF_PX)
    if key not in _ASS_METRICS:
        k, asc = 0.8, 0.8
        try:
            import struct

            from PySide6.QtGui import QRawFont

            raw = QRawFont.fromFont(f)
            os2 = bytes(raw.fontTable("OS/2"))
            wa, wd = struct.unpack(">hh", os2[74:78]) if len(os2) >= 78 else (0, 0)
            if wa + wd > 0:
                k, asc = raw.unitsPerEm() / (wa + wd), wa / (wa + wd)
            else:
                fm = QFontMetricsF(f)
                k, asc = REF_PX / (fm.ascent() + fm.descent()), fm.ascent() / (fm.ascent() + fm.descent())
        except Exception:  # noqa: BLE001
            pass
        _ASS_METRICS[key] = (k, asc)
    k, asc = _ASS_METRICS[key]
    return f, k, asc


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
        if not self._drag:
            self._ovr = {}

    def boundingRect(self):
        w, h = self.box.width(), self.box.height()   # rộng ra: chữ kéo sát mép vẫn vẽ trọn
        return self.box.adjusted(-w / 2, -h / 2, w / 2, h / 2)

    def set(self, parts: list, box: QRectF, style: dict, video_h: int):
        self.prepareGeometryChange()
        self.parts, self.box, self.style, self.video_h = parts, box, style, max(16, video_h or 1080)
        self._geo = None
        self.update()

    # ------------------------------------------------------------ hình học (giống libass)
    def _layout(self):
        from ..subtitle import (SOURCE_SCALE, block_anchor, box_pads, cjk_font_name, fixed_box, sub_size,
                                wrap_line)

        fr = self.box
        if fr.height() <= 2 or fr.width() <= 2:
            return None
        parts = self.parts or ([(self.SAMPLE, "vi")] if self.show_frame and self.editable else [])
        if not parts:
            return None
        st = {**self.style, **self._ovr}
        sc = fr.height() / self.video_h
        W, H = fr.width() / sc, float(self.video_h)
        size = sub_size(st, int(W), int(H))
        max_chars = int(W / (size * 0.55)) if size else 42
        bold = bool(st.get("bold", True))
        rows = []
        for text, kind in parts:
            if kind == "vi":
                t = wrap_line(text.strip(), max_chars)
                rows += [(ln.upper() if st.get("uppercase") else ln, st.get("font") or theme.FONT_FAMILY, size,
                          st.get("color", "#FFFFFF")) for ln in t.split("\n")]
            else:   # song ngữ: dòng Trung nhỏ, màu vàng (kiểu "Source" khi xuất)
                fs, col = (int(size * SOURCE_SCALE), st.get("secondary_color", "#FFE14D")) if kind == "zh" else \
                    (size, st.get("color", "#FFFFFF"))
                rows += [(ln, cjk_font_name(st), fs, col) for ln in text.strip().split("\n")]
        block_h = sum(r[2] for r in rows)
        cx, bottom = block_anchor(st, W, H, block_h)
        X, top = fr.left() + cx * sc, fr.top() + (bottom - block_h) * sc
        lines, y, maxw = [], top, 1.0
        for text, fam, fs, col in rows:
            f, k, asc = ass_font(fam, bold)
            fac = fs * sc * k / REF_PX
            w = QFontMetricsF(f).horizontalAdvance(text) * fac
            lh = fs * sc
            lines.append((text, f, fac, X - w / 2, y, w, lh, y + lh * asc, col))
            maxw = max(maxw, w)
            y += lh
        block = QRectF(X - maxw / 2, top, maxw, block_h * sc)
        boxes = []
        if st.get("box"):
            if st.get("box_fixed"):
                bx, by, bw, bh = fixed_box(st, W, H, cx, bottom - block_h / 2)
                boxes = [QRectF(fr.left() + bx * sc, fr.top() + by * sc, bw * sc, bh * sc)]
            else:
                px, py = box_pads(st, size)
                boxes = [QRectF(x - px * sc, ly - py * sc, w + 2 * px * sc, lh + 2 * py * sc)
                         for text, _f, _k, x, ly, w, lh, *_ in lines if text.strip()]
        outer = QRectF(block)
        for b in boxes:
            outer = outer.united(b) if not st.get("box_fixed") else b
        return {"st": st, "sc": sc, "size": size, "lines": lines, "block": block, "boxes": boxes, "outer": outer,
                "anchor": QPointF(X, top + block_h * sc), "sample": not self.parts}

    def _geometry(self):
        if self._geo is None:
            self._geo = self._layout()
        return self._geo

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
            for b in g["boxes"]:
                p.drawRect(b)
        size_s = g["size"] * g["sc"]
        outline = float(st.get("outline", 2.5)) / 48 * size_s
        shadow = float(st.get("shadow", 1)) / 48 * size_s
        shadow_c = QColor(0, 0, 0, 153)          # BackColour khi xuất: đen, trong suốt 40 %
        for text, f, fac, x, _y, _w, _lh, base, col in g["lines"]:
            if not text:
                continue
            path = QPainterPath()
            path.addText(0, 0, f, text)
            path = QTransform.fromScale(fac, fac).map(path).translated(x, base)
            if not box and shadow > 0.3:
                sh = path.translated(shadow, shadow)
                if outline > 0:     # bóng = (chữ + viền) dời đi, tô 1 lần để không đậm chồng
                    from PySide6.QtGui import QPainterPathStroker

                    stroker = QPainterPathStroker()
                    stroker.setWidth(outline * 2)
                    stroker.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
                    sh = stroker.createStroke(sh).united(sh)
                p.fillPath(sh, shadow_c)
            if not box and outline > 0:
                pen = QPen(QColor(st.get("outline_color", "#000000")), outline * 2)
                pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
                p.strokePath(path, pen)
            p.fillPath(path, QColor(col))
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
            hs["regions"] = self.hs.get("regions") or []
        self.hs = hs
        need = self.hs.get("mode") in ("blur", "inpaint", "delogo", "patch") and bool(self.hs.get("regions"))
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
        if not regs:
            if self.show_outline and br.width() > 40:
                p.setPen(QColor("#FFFFFF"))
                p.setFont(theme.font(10, QFont.Weight.Bold))
                r = QRectF(br.left(), br.top() + 8, br.width(), 28)
                p.fillRect(QRectF(r.center().x() - 190, r.top(), 380, r.height()), QColor(0, 0, 0, 150))
                p.drawText(r, Qt.AlignmentFlag.AlignCenter, "Kéo chuột trên video để khoanh vùng chữ cần xoá")
            return
        rects = [self._rect(r) for r in regs]
        if self.clean and mode == "inpaint":
            pass   # đang chiếu bản AI đã xoá thật
        elif mode == "box":
            c = QColor(self.hs.get("box_color", "#000000"))
            c.setAlphaF(max(0.0, min(1.0, float(self.hs.get("box_opacity", 1.0)))))
            for r in rects:
                p.fillRect(r, c)
        elif mode == "patch":
            img = self._frame_image()
            if img is not None:
                self._paint_patch(p, img, regs, rects)
        elif mode in ("blur", "inpaint", "delogo"):
            img = self._frame_image()
            if img is not None:
                k = 24 if mode != "blur" else max(2, int(self.hs.get("blur_strength", 20)) // 2)
                for r in rects:
                    src = QRectF(r.x() / br.width() * img.width(), r.y() / br.height() * img.height(),
                                 r.width() / br.width() * img.width(), r.height() / br.height() * img.height()).toRect()
                    part = img.copy(src)
                    if part.isNull():
                        continue
                    small = part.scaled(max(1, part.width() // k), max(1, part.height() // k),
                                        Qt.AspectRatioMode.IgnoreAspectRatio,
                                        Qt.TransformationMode.SmoothTransformation)
                    p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
                    p.drawImage(r, small)
        if self.show_outline:
            from .editors import paint_handles

            pen = QPen(QColor(theme.ACCENT), 1.5, Qt.PenStyle.DashLine)
            pen.setCosmetic(True)
            for i, r in enumerate(rects):
                p.setPen(pen)
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawRect(r)
                paint_handles(p, r, theme.ACCENT)
                if r.width() > 40 and r.height() > 14:
                    d = self._del_rect(r)
                    p.setPen(Qt.PenStyle.NoPen)
                    p.setBrush(QColor(theme.ACCENT))
                    p.drawRoundedRect(d, 3, 3)
                    icons.paint_icon(p, "x", d.adjusted(3, 3, -3, -3), "#FFFFFF", 2.2)
                    p.setPen(QColor("#FFFFFF"))
                    p.setFont(theme.font(8, QFont.Weight.Bold))
                    p.drawText(QRectF(r.left() + 4, r.top() + 2, 60, 14), Qt.AlignmentFlag.AlignLeft, f"Vùng {i + 1}")

    # ------------------------------------------------------------ chỉnh vùng bằng chuột
    def _hit(self, pos):
        from .editors import hit_region

        regs = self.hs.get("regions") or []
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
        regs = [dict(r) for r in (self.hs.get("regions") or [])]
        i, kind = self._hit(e.pos())
        x, y = self._rel(e.pos())
        if kind == "del":
            del regs[i]
            self.hs = {**self.hs, "regions": regs}
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
        self.hs = {**self.hs, "regions": regs}
        self.update()
        e.accept()

    def mouseMoveEvent(self, e):
        if not self._drag:
            return
        from .editors import drag_region

        kind, i, r0, (x0, y0) = self._drag
        x, y = self._rel(e.pos())
        regs = self.hs["regions"]
        if kind == "new":
            regs[i] = {"x": min(x, x0), "y": min(y, y0), "w": abs(x - x0), "h": abs(y - y0)}
        else:
            regs[i] = drag_region(r0, kind, x - x0, y - y0)
        self.update()

    def mouseReleaseEvent(self, e):
        if not self._drag:
            return
        self._drag = None
        regs = [{k: round(float(v), 4) for k, v in r.items()} for r in (self.hs.get("regions") or [])
                if float(r["w"]) > 0.01 and float(r["h"]) > 0.01]
        self.hs = {**self.hs, "regions": regs}
        self.update()
        if self.on_changed:
            self.on_changed(regs)


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
        self.hs.prepareGeometryChange()
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

    def set_tool(self, regions: bool, sub_frame: bool = False):
        """regions: tab Xoá phụ đề – chuột chỉnh vùng che; còn lại chuột kéo phụ đề (sub_frame: tab Kiểu chữ)."""
        self.hs.set_editing(regions)
        self.sub.show_frame = sub_frame and not regions
        self.sub.set_editable(not regions)


class LiveAudio:
    """Phát bộ trộn trực tiếp (trami/audiomix.LiveMix) ra loa, bám theo vị trí video: đổi âm lượng / tắt lớp / nghe
    riêng nghe thấy ngay (~0,2 s). Đẩy từng khối nhỏ vào QAudioSink theo nhịp đồng hồ, lệch quá 0,15 s thì nhảy lại
    đúng chỗ video."""
    BUFFER = 0.2      # giây đệm trong loa (đổi âm lượng có tác dụng sau tối đa chừng này)

    def __init__(self, owner: QWidget):
        from ..audiomix import SR

        self.SR = SR
        self.owner = owner
        self.mix = None
        self.sink = None
        self.io = None
        self.pos = 0                     # khung (theo thời gian video gốc) đã đưa vào loa
        self.volume = 0.8
        self.failed = False              # máy không có thiết bị phát âm thanh
        self.timer = QTimer(owner, interval=15, timeout=self._feed)

    def ok(self) -> bool:
        return self.mix is not None and bool(self.mix.tracks) and not self.failed

    def _make_sink(self) -> bool:
        from PySide6.QtMultimedia import QAudioFormat, QAudioSink, QMediaDevices

        dev = QMediaDevices.defaultAudioOutput()
        if dev.isNull():
            self.failed = True
            return False
        fmt = QAudioFormat()
        fmt.setSampleRate(self.SR)
        fmt.setChannelCount(2)
        fmt.setSampleFormat(QAudioFormat.SampleFormat.Int16)
        self.sink = QAudioSink(dev, fmt, self.owner)
        self.sink.setBufferSize(int(self.SR * 4 * self.BUFFER))
        self.sink.setVolume(self.volume)
        return True

    def start(self, t: float):
        if not self.ok() or (self.sink is None and not self._make_sink()):
            return
        self.sink.stop()
        self.pos = int(max(0.0, t) * self.SR)
        self.io = self.sink.start()
        self._feed()
        self.timer.start()

    def stop(self):
        self.timer.stop()
        if self.sink is not None:
            self.sink.stop()
        self.io = None

    def set_volume(self, v: float):
        self.volume = v
        if self.sink is not None:
            self.sink.setVolume(v)

    def _feed(self):
        if self.io is None or self.mix is None or self.sink is None:
            return
        free = self.sink.bytesFree() // 4
        if free < 512:
            return
        from ..audiomix import to_pcm16

        a, adv = self.mix.render(self.pos, min(free, int(self.SR * 0.1)))
        self.io.write(to_pcm16(a))
        self.pos += adv

    def audible(self) -> float:
        """Giây video đang phát ra loa (trừ phần còn nằm trong bộ đệm)."""
        if self.sink is None:
            return self.pos / self.SR
        buffered = (self.sink.bufferSize() - self.sink.bytesFree()) / 4
        return (self.pos - buffered * (self.mix.rate if self.mix else 1.0)) / self.SR

    def sync(self, t: float):
        if self.io is not None and abs(self.audible() - t) > 0.15:
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
        on = self.live.ok()
        if on and self.live.sink is None and not self.live._make_sink():
            on = False
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

    def set_edit(self, edit: dict, src_w: int = 0, src_h: int = 0, duration: float = 0.0):
        """Áp các chỉnh sửa (khung hình, màu, tốc độ, đoạn cắt) lên trình phát để xem ngay."""
        from ..video import keep_ranges

        edit = edit or {}
        self.view.set_edit(edit, src_w, src_h)
        rate = min(4.0, max(0.25, float(edit.get("speed") or 1.0)))
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
        if self.playing() and self.live.ok():
            self.live.sync(t)       # bù lệch tiếng / hình
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
        self.view.sub.clear_override()
        self.update_sub(self.position())

    def update_sub(self, t: float | None = None):
        t = self.position() if t is None else t
        parts = []
        if self.show_subs:
            for s in self.segments:
                if s.start <= t < s.end:
                    if self.which == "source":
                        parts = [(s.text, "src")]
                    elif self.which == "both":
                        parts = [(s.trans, "vi"), (s.text, "zh")]
                    else:
                        parts = [(s.trans, "vi")]
                    break
        parts = [(x, k) for x, k in parts if (x or "").strip()]
        self.view.sub.set(parts, self.view.video_rect(), self.style, self.view.out_h)
