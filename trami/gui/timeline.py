"""Timeline nhiều track kiểu trình dựng phim (Concat / CapCut), đặt ngang dưới cùng trang dự án.

Track: Phụ đề (khối từng câu, màu theo người nói) · Lồng tiếng (mỗi câu 1 đoạn giọng riêng có sóng âm, màu theo độ
vừa; câu chưa có giọng: khung nét đứt) · Video (dải ảnh thu nhỏ) · Tiếng gốc (đường sóng âm).
Bấm khối = chọn (Ctrl: chọn thêm / bỏ, Shift: chọn 1 dải), kéo trên chỗ trống = quét chọn nhiều khối, bấm tên dòng =
chọn hết dòng đó, bấm chỗ trống dòng Phụ đề = chọn khoảng trống (rồi "Thêm phụ đề" đúng chỗ đó), bấm đúp = nghe câu;
dòng Video / Tiếng gốc chia thành đoạn theo điểm tách (Tách tại đầu phát) – chọn đoạn để xoá / khôi phục / tắt tiếng gốc;
kéo đoạn giọng lồng sang trái / phải = dời đúng chỗ (xếp lại ngay); bấm / kéo trên thước = tua;
Delete = xoá thứ đang chọn, Ctrl+A = chọn hết dòng, Esc = bỏ chọn; Ctrl + lăn chuột = phóng to/thu nhỏ.
Công cụ trên thanh phía trên đổi theo dòng đang chọn.
"""
from __future__ import annotations

import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import QDoubleSpinBox, QHBoxLayout, QMenu, QPushButton, QScrollBar, QVBoxLayout, QWidget

from . import icons, theme
from .subs_model import fit_tip, spk_color
from .widgets import IconButton, button, label

FIT_COL = {"ok": "#4ADE80", "fast": "#F5B841", "long": "#F87171"}
TICKS = [0.5, 1, 2, 5, 10, 15, 30, 60, 120, 300, 600, 1200]


def _tc(t: float, step: float) -> str:
    m, s = divmod(max(0.0, t), 60)
    h, m = divmod(int(m), 60)
    base = f"{h}:{m:02d}:{int(s):02d}" if h else f"{m:02d}:{int(s):02d}"
    return base + (f".{int(round((s % 1) * 10))}" if step < 1 else "")


class _Canvas(QWidget):
    HEAD = 118      # cột tên track bên trái
    RULER = 24
    TRACKS = [("subs", "Phụ đề", "subtitles", 38), ("dub", "Lồng tiếng", "mic", 38),
              ("video", "Video", "film", 44), ("audio", "Tiếng gốc", "volume", 30)]
    MIN_SCALE, MAX_SCALE = 0.7, 1.3   # các dòng co / giãn theo chiều cao khung: không bị cắt, không thừa chỗ trống

    def __init__(self, tl: "TrackTimeline"):
        super().__init__(tl)
        self.tl = tl
        self.setMouseTracking(True)
        # màn thấp: các dòng tự co (tối đa còn 70 %) thay vì bị cắt mất dòng dưới cùng; màn cao: giãn ra
        self.setMinimumHeight(self.RULER + 4 + sum(round(h * self.MIN_SCALE) + 6 for *_, h in self.TRACKS))
        self._drag = False
        self._hover = None
        self._dpress = None    # (câu, x lúc bấm, đầu, cuối) – đang bấm giữ 1 đoạn giọng lồng
        self._ghost = None     # (câu, đầu mới, cuối mới) – đang kéo đoạn giọng lồng
        self._band = None      # (dòng, giây lúc bấm, x lúc bấm, giây hiện tại) – đang kéo quét chọn trên chỗ trống
        self.setFocusPolicy(Qt.FocusPolicy.ClickFocus)

    # ------------------------------------------------------------------ toạ độ
    def x(self, t: float) -> float:
        return self.HEAD + (t - self.tl.t0) * self.tl.pps

    def t(self, x: float) -> float:
        return max(0.0, min(self.tl.duration, self.tl.t0 + (x - self.HEAD) / self.tl.pps))

    def _scale(self) -> float:
        room = self.height() - self.RULER - 4 - 6 * len(self.TRACKS)
        return max(self.MIN_SCALE, min(self.MAX_SCALE, room / sum(h for *_, h in self.TRACKS)))

    def track_rect(self, key: str) -> QRectF:
        y, sc = self.RULER + 4, self._scale()
        for k, _, _, h in self.TRACKS:
            h = round(h * sc)
            if k == key:
                return QRectF(self.HEAD, y, self.width() - self.HEAD, h)
            y += h + 6
        return QRectF()

    def track_at(self, pos: QPointF) -> str | None:
        """Dòng (track) dưới con trỏ – tính cả cột tên dòng bên trái."""
        for k, *_ in self.TRACKS:
            r = self.track_rect(k)
            if r.top() <= pos.y() <= r.bottom():
                return k
        return None

    def _insert_at(self, pos: QPointF) -> int | None:
        """Video chèn (dấu tím trên dòng Video) dưới con trỏ."""
        if not self.track_rect("video").contains(pos):
            return None
        for i, it in enumerate(self.tl.inserts):
            if abs(self.x(float(it.get("at", 0))) - pos.x()) <= 9:
                return i
        return None

    def _dub_at(self, pos: QPointF):
        """Đoạn giọng lồng dưới con trỏ: (câu, đầu, cuối, độ vừa) hoặc None."""
        if pos.x() < self.HEAD or not self.track_rect("dub").contains(pos):
            return None
        t = self.t(pos.x())
        tol = 3 / max(self.tl.pps, 1e-6)
        return next(((int(k), a, b, f) for a, b, k, f in self.tl.dub if a - tol <= t <= b + tol), None)

    def _hit(self, pos: QPointF) -> int | None:
        """Câu nằm dưới con trỏ (track Phụ đề hoặc Lồng tiếng – cả khung nét đứt của câu chưa có giọng)."""
        if pos.x() < self.HEAD:
            return None
        t = self.t(pos.x())
        if self.track_rect("subs").contains(pos):
            return next((i for i, s in enumerate(self.tl.segs) if s.start <= t <= s.end), None)
        if self.track_rect("dub").contains(pos):
            d = self._dub_at(pos)
            if d:
                return d[0]
            placed = {int(k) for *_x, k, _f in self.tl.dub}
            return next((i for i, s in enumerate(self.tl.segs) if s.start <= t <= s.end and i not in placed), None)
        return None

    # ------------------------------------------------------------------ chuột
    def mousePressEvent(self, e):
        if not self.tl.duration or e.button() != Qt.MouseButton.LeftButton:
            return
        pos = e.position()
        mods = e.modifiers()
        ctrl = bool(mods & Qt.KeyboardModifier.ControlModifier)
        shift = bool(mods & Qt.KeyboardModifier.ShiftModifier)
        if pos.y() < self.RULER:                     # thước thời gian: tua
            self._drag = True
            self.tl.seekRequested.emit(self.t(pos.x()))
            return
        key = self.track_at(pos)
        if pos.x() < self.HEAD:                      # bấm tên dòng: chọn hết dòng đó
            if key:
                self.tl.select_all(key)
            return
        if key is None:
            return
        t = self.t(pos.x())
        if key in ("subs", "dub"):
            d = self._dub_at(pos) if key == "dub" else None
            if d and not ctrl and not shift:
                self._dpress = (d[0], pos.x(), d[1], d[2])
            row = self._hit(pos)
            if row is not None:
                self.tl.click_row(key, row, ctrl, shift)
                return
            self._band = (key, t, pos.x(), t)
            if not ctrl and not shift:
                self.tl.select_gap(key, t)
            self.tl.seekRequested.emit(t)
            return
        # Video / Tiếng gốc: chọn đoạn (theo điểm tách), video chèn
        ins = self._insert_at(pos) if key == "video" else None
        if ins is not None:
            self.tl.select("video", insert=ins)
            return
        self._band = (key, t, pos.x(), t)
        self.tl.click_piece(key, self.tl.piece_at(t), ctrl, shift)
        self.tl.seekRequested.emit(t)

    def mouseDoubleClickEvent(self, e):
        self._dpress = self._ghost = self._band = None
        row = self._hit(e.position())
        if row is not None:
            self.tl.lineActivated.emit(row)

    def mouseMoveEvent(self, e):
        pos = e.position()
        if self._drag and self.tl.duration:
            self.tl.seekRequested.emit(self.t(pos.x()))
            return
        if self._dpress and e.buttons() & Qt.MouseButton.LeftButton:
            row, x0, a, b = self._dpress
            dx = pos.x() - x0
            if self._ghost or abs(dx) > 4:   # kéo đoạn giọng lồng sang chỗ mới
                d = max(-a, dx / self.tl.pps)
                self._ghost = (row, a + d, b + d)
                self.setCursor(Qt.CursorShape.SizeHorCursor)
                self.update()
            return
        if self._band and e.buttons() & Qt.MouseButton.LeftButton:
            key, t0, x0, _t = self._band
            if abs(pos.x() - x0) > 4:        # quét chọn: mọi khối / đoạn nằm trong vùng kéo
                t1 = self.t(pos.x())
                self._band = (key, t0, x0, t1)
                self.tl.select_span(key, min(t0, t1), max(t0, t1),
                                    add=bool(e.modifiers() & Qt.KeyboardModifier.ControlModifier))
            return
        row = self._hit(pos)
        key = self.track_at(pos) if pos.x() >= self.HEAD else None
        hover = (row, key)
        if hover != self._hover:
            self._hover = hover
            s = self.tl.segs[row] if row is not None and row < len(self.tl.segs) else None
            d = self._dub_at(pos)
            if s and d:
                shift = float(s.extra.get("dub_shift", 0) or 0)
                tip = (f"#{row + 1} · giọng lồng {d[2] - d[1]:.1f} giây"
                       + (f" · đã kéo dời {shift:+.2f} s" if shift else "") + "\n" + fit_tip(s)
                       + f"\n{s.trans}\nKéo sang trái / phải để dời · bấm đúp để nghe · Ctrl/Shift chọn nhiều")
            elif s and self.track_rect("dub").contains(pos):
                why = ("chưa có bản dịch" if not (s.trans or "").strip() else "đã tắt lồng câu này"
                       if s.extra.get("nodub") else "đang hát – giữ giọng gốc" if s.extra.get("song")
                       else "chưa có giọng lồng – bấm Tạo lại giọng câu này")
                tip = f"#{row + 1} {why}\n{s.trans or s.text}"
            elif s:
                tip = f"#{row + 1} {s.speaker or ''}\n{s.trans or s.text}"
            elif key in ("video", "audio"):
                ins = self._insert_at(pos)
                if ins is not None:
                    it = self.tl.inserts[ins]
                    tip = (f"Video chèn: {it.get('name', '')} ({float(it.get('dur', 0)):.1f} giây) tại "
                           f"{_tc(float(it.get('at', 0)), 0.5)}\nChỉ có trong video xuất (trình phát không chiếu)")
                else:
                    a, b = self.tl.pieces()[self.tl.piece_at(self.t(pos.x()))]
                    tags = []
                    if any(ca < b and cb > a for ca, cb in self.tl.cuts):
                        tags.append("đã xoá")
                    if key == "audio" and any(ma < b and mb > a for ma, mb in self.tl.mute):
                        tags.append("tắt tiếng gốc")
                    tip = (f"Đoạn {_tc(a, 0.5)} → {_tc(b, 0.5)}" + (f" ({', '.join(tags)})" if tags else "")
                           + "\nBấm để chọn · Ctrl/Shift chọn nhiều · công cụ ở thanh phía trên")
            elif key == "subs":
                tip = "Chỗ trống – bấm để chọn khoảng này rồi bấm 'Thêm phụ đề'"
            else:
                tip = ""
            self.setToolTip(tip)
            self.setCursor(Qt.CursorShape.PointingHandCursor if row is not None else Qt.CursorShape.ArrowCursor)
            self.update()

    def mouseReleaseEvent(self, e):
        self._drag = False
        self._band = None
        g, pr = self._ghost, self._dpress
        self._dpress = self._ghost = None
        if g and pr:
            self.setCursor(Qt.CursorShape.ArrowCursor)
            delta = round(g[1] - pr[2], 3)
            if abs(delta) >= 0.02:
                self.tl.dubMoved.emit(g[0], delta)
        self.update()

    def keyPressEvent(self, e):
        k = e.key()
        if k in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            act = {"subs": "sub_delete", "video": "v_delete", "audio": "a_mute", "dub": "dub_toggle"}.get(
                self.tl.sel_track or "")
            if act:
                self.tl.actionRequested.emit(act)
            return
        if k == Qt.Key.Key_A and e.modifiers() & Qt.KeyboardModifier.ControlModifier:
            self.tl.select_all(self.tl.sel_track or "subs")
            return
        if k == Qt.Key.Key_Escape:
            self.tl.select(None)
            return
        super().keyPressEvent(e)

    def contextMenuEvent(self, e):
        d = self._dub_at(QPointF(e.pos()))
        if not d:
            return
        row = d[0]
        s = self.tl.segs[row] if row < len(self.tl.segs) else None
        self.tl.lineClicked.emit(row)
        m = QMenu(self)
        m.addAction(f"Nghe câu #{row + 1}", lambda: self.tl.lineActivated.emit(row))
        act = m.addAction("Đặt lại đúng lúc nhân vật nói", lambda: self.tl.dubReset.emit(row))
        act.setEnabled(bool(s and s.extra.get("dub_shift")))
        m.addAction("Tạo lại giọng câu này", lambda: self.tl.actionRequested.emit("redub"))
        m.exec(e.globalPos())

    def wheelEvent(self, e):
        if not self.tl.duration:
            return
        d = e.angleDelta().y() or e.angleDelta().x()
        if e.modifiers() & Qt.KeyboardModifier.ControlModifier:
            self.tl.zoom(1.25 if d > 0 else 0.8, self.t(e.position().x()), e.position().x())
        else:
            self.tl.scroll_by(-d / 120 * 80 / self.tl.pps)
        e.accept()

    # ------------------------------------------------------------------ vẽ
    def paintEvent(self, e):
        tl = self.tl
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.fillRect(self.rect(), QColor("#121722"))
        W, H = self.width(), self.height()
        # cột tên track
        p.setFont(theme.font(8.8, QFont.Weight.DemiBold))
        for key, name, ic, _h in self.TRACKS:
            r = self.track_rect(key)
            on = key == tl.sel_track
            p.fillRect(QRectF(8, r.top(), self.HEAD - 14, r.height()), QColor(theme.CARD))
            if on:     # dòng đang chọn: vạch cam bên trái + chữ cam
                p.fillRect(QRectF(8, r.top(), 3, r.height()), QColor(theme.ACCENT))
            col = theme.ACCENT if on else theme.MUTED
            icons.paint_icon(p, ic, QRectF(16, r.center().y() - 8, 16, 16), col, 1.8)
            p.setPen(QColor(col))
            p.drawText(QRectF(38, r.top(), self.HEAD - 44, r.height()), Qt.AlignmentFlag.AlignVCenter, name)
            p.fillRect(QRectF(self.HEAD, r.top(), W - self.HEAD, r.height()), QColor("#1A2130" if on else "#161C28"))
        if not tl.duration:
            p.setPen(QColor(theme.MUTED))
            p.drawText(QRectF(self.HEAD, 0, W - self.HEAD, H), Qt.AlignmentFlag.AlignCenter, "Chưa có video")
            return
        p.setClipRect(QRectF(self.HEAD, 0, W - self.HEAD, H))
        t_lo, t_hi = self.t(self.HEAD), self.t(W)
        # thước thời gian
        step = next((s for s in TICKS if s * tl.pps >= 90), TICKS[-1])
        p.setPen(QColor(theme.MUTED))
        p.setFont(theme.font(7.8))
        k = int(t_lo // step)
        while k * step <= t_hi + step:
            tx = self.x(k * step)
            p.drawLine(QPointF(tx, self.RULER - 7), QPointF(tx, self.RULER))
            p.drawText(QRectF(tx + 4, 2, 90, self.RULER - 6), Qt.AlignmentFlag.AlignVCenter, _tc(k * step, step))
            k += 1
        # video: dải ảnh thu nhỏ
        rv = self.track_rect("video")
        if tl.thumbs is not None and not tl.thumbs.isNull() and tl.thumb_step > 0:
            # ảnh giữ đúng tỉ lệ (như Concat): mỗi ô rộng bằng 1 ảnh, lấy khung hình ở giữa ô
            fw = tl.thumbs.width() / max(1, tl.thumb_count)
            dw = max(8.0, fw * rv.height() / max(1, tl.thumbs.height()))
            end_x = self.x(tl.duration)
            cx = self.HEAD - ((self.HEAD - self.x(0)) % dw)
            while cx < min(W, end_x):
                i = min(tl.thumb_count - 1, max(0, int(self.t(cx + dw / 2) // tl.thumb_step)))
                w = min(dw, end_x - cx)
                p.drawPixmap(QRectF(cx, rv.top(), w, rv.height()), tl.thumbs,
                             QRectF(i * fw, 0, fw * w / dw, tl.thumbs.height()))
                cx += dw
        else:
            p.setPen(QColor(theme.MUTED))
            p.drawText(rv.adjusted(10, 0, 0, 0), Qt.AlignmentFlag.AlignVCenter, "Đang tạo ảnh thu nhỏ…")
        # tiếng gốc: đường sóng âm
        ra = self.track_rect("audio")
        if tl.wave is not None and len(tl.wave):
            mid, half = ra.center().y(), ra.height() / 2 - 2
            p.setPen(QPen(theme.c(theme.INFO, 190), 1))
            px = int(self.HEAD)
            while px < W:
                a, b = self.t(px), self.t(px + 1)
                ia, ib = int(a / tl.wave_hop), max(int(a / tl.wave_hop) + 1, int(b / tl.wave_hop))
                v = float(tl.wave[ia:ib].max()) if ia < len(tl.wave) else 0.0
                if v > 0.004:
                    p.drawLine(QPointF(px, mid - v * half), QPointF(px, mid + v * half))
                px += 1
        # lồng tiếng: mỗi câu 1 đoạn giọng riêng (sóng âm thật), đặt đúng chỗ đã xếp; câu chưa có giọng: nét đứt
        rd = self.track_rect("dub")
        placed = {int(k) for *_x, k, _f in tl.dub}
        if tl.dub:
            p.setFont(theme.font(7.6))
            for i, s in enumerate(tl.segs):
                if i in placed or s.end < t_lo or s.start > t_hi:
                    continue
                r = QRectF(self.x(s.start), rd.top() + 4, max(2.0, (s.end - s.start) * tl.pps - 1), rd.height() - 8)
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.setPen(QPen(QColor(theme.MUTED), 1, Qt.PenStyle.DashLine))
                p.drawRoundedRect(r, 4, 4)
                why = ("chưa dịch" if not (s.trans or "").strip() else "bỏ lồng" if s.extra.get("nodub")
                       else "🎵 đang hát" if s.extra.get("song") else "chưa có giọng")
                if r.width() > 34:
                    p.setPen(QColor(theme.MUTED))
                    p.drawText(r.adjusted(4, 0, -2, 0), Qt.AlignmentFlag.AlignVCenter,
                               p.fontMetrics().elidedText(why, Qt.TextElideMode.ElideRight, int(r.width() - 6)))
        p.setFont(theme.font(7.6, QFont.Weight.DemiBold))
        ghost_row = self._ghost[0] if self._ghost else None
        # 2 đoạn giọng đọc đè lên nhau: vẽ thành 2 làn (trên / dưới) để thấy rõ phần đè
        lanes: dict[int, int] = {}
        prev = None
        for a, b, k, _f in sorted(tl.dub, key=lambda r: (float(r[0]), int(r[2]))):
            if prev and float(a) < prev[1] - 0.05:
                lanes[int(k)] = 2 if lanes.get(prev[0], 1) == 1 else 1
                lanes.setdefault(prev[0], 1)
            prev = (int(k), float(b))
        for a, b, k, fit in tl.dub:
            if b < t_lo or a > t_hi:
                continue
            self._paint_clip(p, rd, a, b, int(k), fit, faded=int(k) == ghost_row, lane=lanes.get(int(k), 0))
            self._paint_shift(p, rd, a, int(k))
        if self._ghost:
            row, a, b = self._ghost
            fit = next((f for *_x, k, f in tl.dub if int(k) == row), "ok")
            self._paint_clip(p, rd, a, b, row, fit, ghost=True)
        # phụ đề: khối từng câu
        rs = self.track_rect("subs")
        p.setFont(theme.font(8.4, QFont.Weight.Medium))
        for i, s in enumerate(tl.segs):
            if s.end < t_lo or s.start > t_hi:
                continue
            r = QRectF(self.x(s.start), rs.top() + 2, max(2.0, (s.end - s.start) * tl.pps - 1), rs.height() - 4)
            base = QColor(spk_color(s.speaker or "S1"))
            fill = QColor(base)
            sel = tl.is_row_selected(i)
            fill.setAlpha(235 if sel else (190 if i == tl.active or (self._hover and self._hover[0] == i) else 120))
            p.setPen(QPen(QColor("#FFFFFF"), 2) if sel else Qt.PenStyle.NoPen)
            p.setBrush(fill)
            p.drawRoundedRect(r, 5, 5)
            if r.width() > 26:
                p.setPen(QColor("#FFFFFF"))
                txt = (s.trans or s.text or "").strip()
                p.drawText(r.adjusted(6, 0, -4, 0), Qt.AlignmentFlag.AlignVCenter,
                           p.fontMetrics().elidedText(txt, Qt.TextElideMode.ElideRight, int(r.width() - 10)))
        if tl.sel_gap and tl.sel_track == "subs":     # khoảng trống đang chọn trên dòng Phụ đề
            a, b = tl.sel_gap
            r = QRectF(self.x(a) + 1, rs.top() + 2, max(4.0, (b - a) * tl.pps - 2), rs.height() - 4)
            p.setBrush(theme.c(theme.ACCENT, 40))
            p.setPen(QPen(QColor(theme.ACCENT), 1.4, Qt.PenStyle.DashLine))
            p.drawRoundedRect(r, 5, 5)
            if r.width() > 60:
                p.setPen(QColor(theme.ACCENT))
                p.setFont(theme.font(8, QFont.Weight.DemiBold))
                p.drawText(r, Qt.AlignmentFlag.AlignCenter,
                           p.fontMetrics().elidedText("+ Thêm phụ đề ở đây", Qt.TextElideMode.ElideRight,
                                                      int(r.width() - 8)))
        # đoạn tắt tiếng gốc (dòng Tiếng gốc)
        for a, b in tl.mute:
            if b < t_lo or a > t_hi:
                continue
            r = QRectF(self.x(a), ra.top(), max(2.0, (b - a) * tl.pps), ra.height())
            p.fillRect(r, QColor(0, 0, 0, 150))
            if r.width() > 90:
                p.setFont(theme.font(7.6, QFont.Weight.DemiBold))
                tw = p.fontMetrics().horizontalAdvance("tắt tiếng gốc")
                x0 = r.center().x() - (tw + 18) / 2
                icons.paint_icon(p, "mute", QRectF(x0, r.center().y() - 7, 14, 14), "#E6E9F0", 1.6)
                p.setPen(QColor("#E6E9F0"))
                p.drawText(QRectF(x0 + 18, r.top(), tw + 4, r.height()), Qt.AlignmentFlag.AlignVCenter, "tắt tiếng gốc")
        # điểm tách video / tiếng gốc + đoạn đang chọn
        for x0 in tl.vsplits:
            if t_lo <= x0 <= t_hi:
                xs = self.x(x0)
                p.setPen(QPen(QColor("#FFFFFF"), 1.6))
                p.drawLine(QPointF(xs, rv.top()), QPointF(xs, ra.bottom()))
        if tl.sel_track in ("video", "audio") and tl.sel_pieces:
            rr = rv if tl.sel_track == "video" else ra
            pcs = tl.pieces()
            p.setBrush(theme.c(theme.ACCENT, 45))
            p.setPen(QPen(QColor(theme.ACCENT), 2))
            for i in tl.sel_pieces:
                if 0 <= i < len(pcs):
                    a, b = pcs[i]
                    p.drawRect(QRectF(self.x(a) + 1, rr.top() + 1, max(2.0, (b - a) * tl.pps - 2), rr.height() - 2))
        # video chèn: dấu tím trên dòng Video (chỉ có trong video xuất)
        for i, it in enumerate(tl.inserts):
            at = float(it.get("at", 0))
            if not (t_lo - 1 <= at <= t_hi + 1):
                continue
            xi = self.x(at)
            sel = tl.sel_track == "video" and tl.sel_insert == i
            p.setPen(QPen(QColor("#FFFFFF") if sel else QColor("#C07BFF"), 2))
            p.setBrush(QColor("#7C3AED"))
            badge = QRectF(xi - 9, rv.top() + 2, 18, rv.height() - 4)
            p.drawRoundedRect(badge, 4, 4)
            p.setPen(QColor("#FFFFFF"))
            p.setFont(theme.font(10, QFont.Weight.Bold))
            p.drawText(badge, Qt.AlignmentFlag.AlignCenter, "+")
        # vùng đang quét chọn
        if self._band and abs(self._band[3] - self._band[1]) * tl.pps > 4:
            key, t0, _x0, t1 = self._band
            rb = self.track_rect(key)
            p.setBrush(theme.c(theme.ACCENT, 35))
            p.setPen(QPen(QColor(theme.ACCENT), 1, Qt.PenStyle.DashLine))
            p.drawRect(QRectF(self.x(min(t0, t1)), rb.top(), abs(t1 - t0) * tl.pps, rb.height()))
        # đoạn bị cắt / ngoài vùng giữ
        top, bot = self.RULER, H
        ks, ke = tl.keep
        ke = ke or tl.duration
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(0, 0, 0, 120))
        if ks > 0:
            p.drawRect(QRectF(self.x(0), top, ks * tl.pps, bot - top))
        if ke < tl.duration:
            p.drawRect(QRectF(self.x(ke), top, (tl.duration - ke) * tl.pps, bot - top))
        p.setBrush(theme.c(theme.ERR, 70))
        for a, b in tl.cuts:
            p.drawRect(QRectF(self.x(a), top, max(2.0, (b - a) * tl.pps), bot - top))
        # điểm đánh dấu cắt + đầu phát
        if tl.mark is not None:
            p.setPen(QPen(QColor(theme.WARN), 1.5, Qt.PenStyle.DashLine))
            p.drawLine(QPointF(self.x(tl.mark), top), QPointF(self.x(tl.mark), bot))
        hx = self.x(tl.pos)
        p.setPen(QPen(QColor(theme.ACCENT), 1.6))
        p.drawLine(QPointF(hx, 4), QPointF(hx, bot))
        tri = QPainterPath()
        tri.moveTo(hx - 6, 2)
        tri.lineTo(hx + 6, 2)
        tri.lineTo(hx, 10)
        tri.closeSubpath()
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(theme.ACCENT))
        p.drawPath(tri)


    def _paint_shift(self, p: QPainter, rd: QRectF, a: float, k: int):
        """Đoạn giọng đọc sớm / bị lùi: vạch đánh dấu lúc nhân vật bắt đầu nói + đường nối tới đầu đoạn giọng."""
        tl = self.tl
        if not 0 <= k < len(tl.segs):
            return
        s = tl.segs[k]
        sh = float(s.extra.get("fit_shift", 0) or 0)
        if abs(sh) < 0.3:
            return
        want = s.start + float(s.extra.get("dub_shift", 0) or 0)
        xw, xa = self.x(want), self.x(a)
        col = QColor(theme.INFO if sh < 0 else theme.WARN)
        y = rd.top() + 2
        p.setPen(QPen(col, 1.4))
        p.drawLine(QPointF(xw, rd.top()), QPointF(xw, rd.bottom()))
        p.setPen(QPen(col, 1.2, Qt.PenStyle.DashLine))
        p.drawLine(QPointF(min(xw, xa), y), QPointF(max(xw, xa), y))

    def _paint_clip(self, p: QPainter, rd: QRectF, a: float, b: float, k: int, fit: str, faded: bool = False,
                    ghost: bool = False, lane: int = 0):
        tl = self.tl
        r = QRectF(self.x(a), rd.top() + 2, max(3.0, (b - a) * tl.pps - 1), rd.height() - 4)
        if lane:   # đoạn đọc đè: nửa trên / nửa dưới của dòng
            h = r.height() / 2
            r = QRectF(r.left(), r.top() + (h if lane == 2 else 0), r.width(), h)
        col = QColor(FIT_COL.get(fit, "#4ADE80"))
        sel = tl.is_row_selected(k)
        fill = QColor(col)
        fill.setAlpha(40 if faded else (120 if sel or ghost else 70))
        p.setBrush(fill)
        p.setPen(QPen(QColor("#FFFFFF") if ghost else col, 2 if sel or ghost else 1,
                      Qt.PenStyle.DashLine if ghost else Qt.PenStyle.SolidLine))
        p.drawRoundedRect(r, 4, 4)
        env = tl.dub_wave
        if env is not None and len(env) and not ghost and r.width() > 4:
            # sóng âm của chính đoạn giọng này (lấy từ bản lồng đã ghép, đúng vị trí đã xếp)
            mid, half = r.center().y() + 3, r.height() / 2 - 6
            wc = QColor(col)
            wc.setAlpha(110 if faded else 230)
            p.setPen(QPen(wc, 1))
            px, end = int(r.left()) + 2, int(r.right()) - 1
            while px < end:
                t0, t1 = self.t(px), self.t(px + 1)
                ia = int(t0 / tl.dub_hop)
                ib = max(ia + 1, int(t1 / tl.dub_hop))
                v = float(env[ia:ib].max()) if ia < len(env) else 0.0
                if v > 0.02:
                    p.drawLine(QPointF(px, mid - v * half), QPointF(px, mid + v * half))
                px += 1
        if r.width() > 24:
            p.setPen(QColor("#FFFFFF"))
            ex = tl.segs[k].extra if 0 <= k < len(tl.segs) else {}
            sp = ex.get("speed") or (ex.get("fit_speed") if float(ex.get("fit_speed", 1) or 1) > 1.06 else None)
            sh = float(ex.get("fit_shift", 0) or 0)
            tag = f"#{k + 1}" + (f" ×{float(sp):.2f}" if sp and r.width() > 70 else "")
            if abs(sh) >= 0.3 and r.width() > 110:
                tag += f" {'◀' if sh < 0 else '▶'}{abs(sh):.1f}s"
            if float(ex.get("fit_over", 0) or 0) >= 0.1 and r.width() > 150:
                tag += f" ⧉{float(ex['fit_over']):.1f}s"
            p.drawText(r.adjusted(5, 1, -3, 0), Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft,
                       p.fontMetrics().elidedText(tag, Qt.TextElideMode.ElideRight, int(r.width() - 8)))


class TrackTimeline(QWidget):
    seekRequested = Signal(float)
    lineClicked = Signal(int)       # bấm 1 câu -> chọn câu đó
    lineActivated = Signal(int)     # bấm đúp -> nghe câu
    actionRequested = Signal(str)   # công cụ trên thanh (xem TOOLS) – trang dự án làm với lựa chọn hiện tại
    dubMoved = Signal(int, float)   # kéo đoạn giọng lồng của câu: (câu, số giây dời thêm)
    dubReset = Signal(int)          # đặt đoạn giọng lồng về đúng lúc nhân vật nói
    selectionChanged = Signal()

    # công cụ hiện trên thanh theo dòng đang chọn: (tên lệnh, icon, chữ, gợi ý)
    TOOLS = {
        None: [("sub_add", "plus", "Thêm phụ đề", "Thêm câu tại đầu phát"),
               ("v_split", "split", "Tách video", "Tách video + tiếng gốc tại đầu phát thành 2 đoạn"),
               ("v_insert", "film", "Chèn video…", "Chèn 1 video khác vào tại đầu phát (khi xuất)")],
        "subs": [("sub_add", "plus", "Thêm phụ đề", "Thêm câu vào khoảng trống đang chọn (hoặc tại đầu phát)"),
                 ("sub_split", "split", "Tách", "Tách câu tại đầu phát"),
                 ("sub_merge", "merge", "Gộp", "Gộp các câu đang chọn (1 câu: gộp với câu sau)"),
                 ("sub_delete", "trash", "Xoá", "Xoá các câu đang chọn (phím Delete)"),
                 ("sub_translate", "globe", "Dịch lại", "Dịch lại các câu đang chọn"),
                 ("play", "play", "Nghe", "Nghe câu đang chọn"),
                 ("sel_all", "check", "Chọn hết", "Chọn mọi câu (Ctrl+A)")],
        "dub": [("play", "play", "Nghe", "Nghe câu đang chọn (bấm đúp vào đoạn giọng cũng được)"),
                ("dub_redo", "refresh", "Tạo lại giọng", "Đọc lại các câu đang chọn"),
                ("speed", None, None, None),
                ("dub_speed", "bolt", "Đặt tốc độ", "Tốc độ tối thiểu cho các câu đang chọn – câu đang đọc nhanh hơn giữ "
                 "nguyên, câu chậm hơn được nâng lên. Xếp lại ngay, không đọc lại"),
                ("dub_speed_reset", "refresh", "Về tốc độ gốc", "Bỏ tốc độ đặt tay của các câu đang chọn"),
                ("dub_mode", "layers", "Cách xếp", "Câu đang chọn đọc dài hơn chỗ trống thì: Tự động / Nối tiếp / "
                 "Đọc đè / Đọc sớm / Cắt bớt (riêng các câu này) – xếp lại ngay, không đọc lại"),
                ("dub_toggle", "mute", "Bỏ lồng / Lồng lại", "Tắt (hoặc bật lại) giọng lồng của các câu đang chọn"),
                ("dub_reset_pos", "refresh", "Về đúng chỗ", "Đặt đoạn giọng về đúng lúc nhân vật nói"),
                ("sel_all", "check", "Chọn hết", "Chọn mọi đoạn giọng (Ctrl+A)")],
        "video": [("v_split", "split", "Tách tại đầu phát", "Tách video + tiếng gốc thành 2 đoạn tại đầu phát"),
                  ("v_delete", "trash", "Xoá đoạn", "Xoá các đoạn đang chọn khỏi video xuất (trình phát bỏ qua ngay)"),
                  ("v_restore", "refresh", "Khôi phục", "Lấy lại các đoạn đã xoá đang chọn"),
                  ("v_unsplit", "merge", "Gộp đoạn", "Bỏ điểm tách giữa các đoạn đang chọn"),
                  ("v_insert", "film", "Chèn video…", "Chèn 1 video khác vào tại đầu phát (khi xuất)"),
                  ("v_insert_del", "x", "Bỏ video chèn", "Bỏ video chèn đang chọn (dấu + tím)"),
                  ("sel_all", "check", "Chọn hết", "Chọn mọi đoạn (Ctrl+A)")],
        "audio": [("v_split", "split", "Tách tại đầu phát", "Tách tiếng gốc + video thành 2 đoạn tại đầu phát"),
                  ("a_mute", "mute", "Tắt tiếng gốc", "Tắt tiếng gốc (nguyên bản / nhạc nền / giọng gốc đã tách) ở các "
                   "đoạn đang chọn – giọng lồng vẫn giữ"),
                  ("a_unmute", "volume", "Bật lại tiếng gốc", "Bật lại tiếng gốc ở các đoạn đang chọn"),
                  ("v_unsplit", "merge", "Gộp đoạn", "Bỏ điểm tách giữa các đoạn đang chọn"),
                  ("sel_all", "check", "Chọn hết", "Chọn mọi đoạn (Ctrl+A)")],
    }
    TRACK_NAMES = {"subs": "Phụ đề", "dub": "Lồng tiếng", "video": "Video", "audio": "Tiếng gốc"}

    def __init__(self, parent=None):
        super().__init__(parent)
        self.duration = 0.0
        self.segs: list = []
        self.dub: list = []
        self.keep: tuple[float, float] = (0.0, 0.0)
        self.cuts: list = []
        self.pos = 0.0
        self.mark: float | None = None
        self.selected = -1
        self.active = -1
        # lựa chọn trên timeline
        self.sel_track: str | None = None
        self.sel_rows: set[int] = set()       # câu (dòng Phụ đề / Lồng tiếng)
        self.sel_pieces: set[int] = set()     # đoạn (dòng Video / Tiếng gốc), theo pieces()
        self.sel_gap: tuple[float, float] | None = None
        self.sel_insert: int | None = None
        self._anchor = None
        self.vsplits: list[float] = []
        self.inserts: list[dict] = []
        self.mute: list = []
        self.thumbs: QPixmap | None = None
        self.thumb_step = 0.0
        self.thumb_count = 0
        self.wave: np.ndarray | None = None
        self.wave_hop = 0.05
        self.dub_wave: np.ndarray | None = None   # sóng âm bản lồng tiếng đang dùng
        self.dub_hop = 0.05
        self.t0 = 0.0          # giây ở mép trái vùng track
        self.pps = 20.0        # điểm ảnh / giây
        self._fitted = False
        self._opened = False   # đã đặt mức phóng ban đầu cho video này
        self.canvas = _Canvas(self)
        self.bar = QScrollBar(Qt.Orientation.Horizontal)
        self.bar.valueChanged.connect(self._bar_moved)
        tb = QHBoxLayout()
        tb.setContentsMargins(6, 0, 6, 0)
        tb.setSpacing(4)
        self.sel_label = label("", "muted")
        tb.addWidget(self.sel_label)
        self.tools = QHBoxLayout()
        self.tools.setSpacing(2)
        tb.addLayout(self.tools)
        self.speed = QDoubleSpinBox()
        self.speed.setRange(0.5, 2.0)
        self.speed.setSingleStep(0.05)
        self.speed.setDecimals(2)
        self.speed.setValue(1.2)
        self.speed.setPrefix("×")
        self.speed.setFixedWidth(78)
        self.speed.setToolTip("Tốc độ đọc tối thiểu cho các câu đang chọn (bấm 'Đặt tốc độ' để áp dụng)")
        self.speed.hide()
        tb.addStretch(1)
        for ic, tip, fn in (("minus", "Thu nhỏ", lambda: self.zoom(0.7)), ("plus", "Phóng to", lambda: self.zoom(1.4)),
                            ("fit", "Vừa khung (xem cả video)", self.fit)):
            b = IconButton(ic, tip, 30)
            b.clicked.connect(fn)
            tb.addWidget(b)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)
        lay.addLayout(tb)
        lay.addWidget(self.canvas, 1)
        lay.addWidget(self.bar)
        self._refresh_tools()

    # ------------------------------------------------------------------ dữ liệu
    def set_data(self, duration: float, segs: list, dub: list, keep=(0.0, 0.0), cuts=None, vsplits=None,
                 inserts=None, mute=None):
        if abs(duration - self.duration) > 0.01:
            self._fitted = self._opened = False
        self.duration, self.segs, self.dub = float(duration or 0), segs, dub or []
        self.keep, self.cuts = tuple(keep or (0, 0)), cuts or []
        self.vsplits = sorted(float(x) for x in (vsplits or []))
        self.inserts, self.mute = list(inserts or []), list(mute or [])
        # bỏ lựa chọn không còn tồn tại (câu bị xoá, đoạn bị gộp…)
        self.sel_rows = {r for r in self.sel_rows if 0 <= r < len(segs)}
        self.sel_pieces = {i for i in self.sel_pieces if 0 <= i < len(self.pieces())}
        if self.sel_insert is not None and self.sel_insert >= len(self.inserts):
            self.sel_insert = None
        self._refresh_tools()
        if not self._fitted and not self._opened and self.duration and self.canvas.width() > _Canvas.HEAD + 50:
            self._opened = True
            self.fit(90)
        self._sync_bar()
        self.canvas.update()

    def set_thumbs(self, pm: QPixmap | None, step: float, count: int):
        self.thumbs, self.thumb_step, self.thumb_count = pm, step, count
        self.canvas.update()

    def set_wave(self, env: np.ndarray | None, hop: float):
        self.wave, self.wave_hop = env, hop
        self.canvas.update()

    def set_dub_wave(self, env: np.ndarray | None, hop: float = 0.05):
        self.dub_wave, self.dub_hop = env, hop
        self.canvas.update()

    def set_pos(self, t: float, follow: bool = False):
        self.pos = t
        vw = self._view_secs()
        if follow and self.duration and not (self.t0 <= t <= self.t0 + vw * 0.92):
            self.scroll_to(t - vw * 0.1)
        self.canvas.update()

    # ------------------------------------------------------------------ lựa chọn
    def pieces(self) -> list[tuple[float, float]]:
        """Các đoạn video / tiếng gốc chia theo điểm tách."""
        d = self.duration
        pts = sorted({0.0, d} | {round(x, 3) for x in self.vsplits if 0.05 < x < d - 0.05})
        return list(zip(pts[:-1], pts[1:])) if d else []

    def piece_at(self, t: float) -> int:
        for i, (a, b) in enumerate(self.pieces()):
            if a <= t < b:
                return i
        return max(0, len(self.pieces()) - 1)

    def is_row_selected(self, row: int) -> bool:
        if self.sel_track in ("subs", "dub") and self.sel_rows:
            return row in self.sel_rows
        return row == self.selected

    def selected_rows(self) -> list[int]:
        rows = self.sel_rows if self.sel_track in ("subs", "dub") and self.sel_rows else (
            {self.selected} if self.selected >= 0 else set())
        return sorted(r for r in rows if 0 <= r < len(self.segs))

    def select(self, track: str | None, rows=None, pieces=None, gap=None, insert=None):
        self.sel_track = track
        self.sel_rows = set(rows or ())
        self.sel_pieces = set(pieces or ())
        self.sel_gap, self.sel_insert = gap, insert
        self._changed()

    def _changed(self):
        self._refresh_tools()
        self.canvas.update()
        self.selectionChanged.emit()

    def click_row(self, track: str, row: int, ctrl: bool, shift: bool):
        if self.sel_track != track:
            self.sel_rows = set()
        self.sel_track, self.sel_gap, self.sel_pieces, self.sel_insert = track, None, set(), None
        if ctrl:
            self.sel_rows ^= {row}
        elif shift and self._anchor is not None:
            a, b = sorted((self._anchor, row))
            placed = {int(k) for *_x, k, _f in self.dub} if track == "dub" else None
            self.sel_rows = {i for i in range(a, b + 1) if placed is None or i in placed}
        else:
            self.sel_rows = {row}
            self._anchor = row
        self.selected = row
        self._changed()
        if not ctrl and not shift:
            self.lineClicked.emit(row)

    def click_piece(self, track: str, i: int, ctrl: bool, shift: bool):
        if self.sel_track != track:
            self.sel_pieces = set()
        self.sel_track, self.sel_gap, self.sel_rows, self.sel_insert = track, None, set(), None
        if ctrl:
            self.sel_pieces ^= {i}
        elif shift and self._anchor is not None:
            a, b = sorted((self._anchor, i))
            self.sel_pieces = set(range(a, b + 1))
        else:
            self.sel_pieces = {i}
            self._anchor = i
        self._changed()

    def select_gap(self, track: str, t: float):
        """Bấm chỗ trống: dòng Phụ đề -> chọn khoảng trống giữa 2 câu (để thêm câu đúng chỗ); dòng khác -> chọn dòng."""
        gap = None
        if track == "subs":
            lo = max((s.end for s in self.segs if s.end <= t), default=0.0)
            hi = min((s.start for s in self.segs if s.start >= t), default=self.duration)
            if hi - lo >= 0.15:
                gap = (lo, hi)
        self.gap_click = t
        self.select(track, gap=gap)

    def select_span(self, track: str, a: float, b: float, add: bool = False):
        """Kéo quét trên chỗ trống: chọn mọi khối / đoạn nằm trong [a, b]."""
        if track in ("subs", "dub"):
            if track == "dub":
                rows = {int(k) for x0, x1, k, _f in self.dub if x0 < b and x1 > a}
            else:
                rows = {i for i, s in enumerate(self.segs) if s.start < b and s.end > a}
            if add and self.sel_track == track:
                rows |= self.sel_rows
            self.select(track, rows=rows)
        else:
            pcs = {i for i, (x0, x1) in enumerate(self.pieces()) if x0 < b and x1 > a}
            if add and self.sel_track == track:
                pcs |= self.sel_pieces
            self.select(track, pieces=pcs)

    def select_all(self, track: str):
        if track == "subs":
            self.select("subs", rows=range(len(self.segs)))
        elif track == "dub":
            self.select("dub", rows={int(k) for *_x, k, _f in self.dub})
        else:
            self.select(track, pieces=range(len(self.pieces())))

    def _refresh_tools(self):
        """Thanh công cụ đổi theo dòng đang chọn: chỉ hiện việc làm được với thứ đang chọn."""
        lay = self.tools
        while lay.count():
            it = lay.takeAt(0)
            w = it.widget()
            if w is self.speed:
                w.hide()
            elif w is not None:
                w.hide()               # ẩn + gỡ ngay (deleteLater chỉ xoá ở vòng sự kiện sau -> chữ chồng nhau)
                w.setParent(None)
                w.deleteLater()
        track = self.sel_track
        n_rows = len(self.sel_rows) if track in ("subs", "dub") else 0
        if track == "subs" and self.sel_gap and not n_rows:
            a, b = self.sel_gap
            txt = f"Phụ đề · khoảng trống {_tc(a, 0.5)} → {_tc(b, 0.5)}"
        elif track in ("subs", "dub"):
            txt = f"{self.TRACK_NAMES[track]} · " + (f"đã chọn {n_rows} câu" if n_rows else "chưa chọn câu nào")
        elif track in ("video", "audio"):
            pcs = self.pieces()
            n = len(self.sel_pieces)
            txt = f"{self.TRACK_NAMES[track]} · {len(pcs)} đoạn" + (f", đã chọn {n}" if n else "")
            if track == "video" and self.sel_insert is not None:
                txt = f"Video chèn: {self.inserts[self.sel_insert].get('name', '')}"
        else:
            txt = "Bấm khối / chỗ trống để chọn · Ctrl, Shift: chọn nhiều · bấm tên dòng: chọn hết ·"
        self.sel_label.setText(txt)
        for name, ic, text, tip in self.TOOLS.get(track, self.TOOLS[None]):
            if name == "speed":
                lay.addWidget(self.speed)
                self.speed.show()
                continue
            if name == "v_insert_del" and self.sel_insert is None:
                continue
            if name == "dub_mode":   # nút có menu chọn cách xếp
                from ..tts import OVERFLOW_MODES

                b = button(text, ic, tip, flat=True)
                menu = QMenu(b)
                menu.addAction("Theo cài đặt chung", lambda: self.actionRequested.emit("dub_mode:"))
                for m, mname in OVERFLOW_MODES:
                    menu.addAction(mname, lambda _=False, m=m: self.actionRequested.emit("dub_mode:" + m))
                b.setMenu(menu)
                b.setProperty("full_text", text)
                lay.addWidget(b)
                continue
            b = button(text, ic, tip, flat=True, on_click=lambda _=False, n=name: self.actionRequested.emit(n))
            b.setProperty("full_text", text)
            lay.addWidget(b)
        self._fit_tools()

    def _fit_tools(self):
        """Màn hẹp (1366 px): các nút cuối thanh công cụ chỉ còn biểu tượng (chữ vẫn ở chú thích khi rê chuột) cho
        thanh không tràn / cắt chữ."""
        btns = [w for w in (self.tools.itemAt(i).widget() for i in range(self.tools.count()))
                if isinstance(w, QPushButton) and w.property("full_text")]
        for b in btns:
            if not b.text():
                b.setText(b.property("full_text"))
        avail = self.width() - 3 * 34 - 40
        if avail <= 0:
            return

        def need() -> int:
            ws = [self.tools.itemAt(i).widget() for i in range(self.tools.count())]
            return self.sel_label.sizeHint().width() + sum(w.sizeHint().width() + 2 for w in ws if w)
        for b in reversed(btns):
            if need() <= avail:
                break
            b.setText("")
            if not (b.toolTip() or "").startswith(b.property("full_text")):
                b.setToolTip(b.property("full_text") + (" – " + b.toolTip() if b.toolTip() else ""))

    def set_selected(self, row: int):
        if self.sel_track in ("subs", "dub") and row in self.sel_rows:
            self.selected = row      # đang chọn nhiều: giữ nguyên lựa chọn
            self.canvas.update()
            return
        if self.sel_track not in ("subs", "dub") or len(self.sel_rows) != 1 or row not in self.sel_rows:
            if 0 <= row < len(self.segs):
                self.sel_track = self.sel_track if self.sel_track in ("subs", "dub") else "subs"
                self.sel_rows, self.sel_gap, self.sel_pieces, self.sel_insert = {row}, None, set(), None
                self._anchor = row
                self._refresh_tools()
        self.selected = row
        if 0 <= row < len(self.segs):
            s = self.segs[row]
            vw = self._view_secs()
            if not (self.t0 <= s.start and s.end <= self.t0 + vw):
                self.scroll_to(s.start - vw * 0.2)
        self.canvas.update()

    def set_active(self, row: int):
        if row != self.active:
            self.active = row
            self.canvas.update()

    # ------------------------------------------------------------------ phóng to / cuộn
    def _view_secs(self) -> float:
        return max(1.0, (self.canvas.width() - _Canvas.HEAD) / self.pps)

    def fit(self, max_secs: float | None = None):
        """Xem cả video; max_secs: lần đầu mở video dài chỉ hiện ~90 giây cho đọc được chữ trong từng câu."""
        if self.duration:
            span = min(self.duration, max_secs) if max_secs else self.duration
            self.pps = max(0.5, (self.canvas.width() - _Canvas.HEAD - 10) / span)
            self.t0 = 0.0
            self._fitted = span >= self.duration
            self._sync_bar()
            self.canvas.update()

    def zoom(self, f: float, at: float | None = None, at_x: float | None = None):
        if not self.duration:
            return
        lo = (self.canvas.width() - _Canvas.HEAD - 10) / self.duration
        at = self.pos if at is None else at
        at_x = self.canvas.x(at) if at_x is None else at_x
        self.pps = min(400.0, max(lo, self.pps * f))
        self.t0 = at - (at_x - _Canvas.HEAD) / self.pps
        self._fitted = False
        self.scroll_to(self.t0)

    def scroll_by(self, secs: float):
        self.scroll_to(self.t0 + secs)

    def scroll_to(self, t0: float):
        self.t0 = max(0.0, min(t0, max(0.0, self.duration - self._view_secs())))
        self._sync_bar()
        self.canvas.update()

    def _sync_bar(self):
        total = int(self.duration * self.pps)
        view = int(self.canvas.width() - _Canvas.HEAD)
        self.bar.blockSignals(True)
        self.bar.setRange(0, max(0, total - view))
        self.bar.setPageStep(max(1, view))
        self.bar.setValue(int(self.t0 * self.pps))
        self.bar.blockSignals(False)
        self.bar.setVisible(total > view)

    def _bar_moved(self, v: int):
        self.t0 = v / self.pps
        self.canvas.update()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._fit_tools()
        if not self.duration:
            return
        if not self._opened and self.canvas.width() > _Canvas.HEAD + 50:
            self._opened = True
            self.fit(90)
        elif self._fitted:
            self.fit()
        else:
            self._sync_bar()
