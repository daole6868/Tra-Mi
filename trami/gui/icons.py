"""Bộ icon nét mảnh, vẽ hoàn toàn bằng QPainter (không cần file ảnh).

Mỗi icon là 1 hàm vẽ trong hệ toạ độ 24x24.  Dùng: icon("film", "#fff", 20) -> QIcon
"""
from __future__ import annotations

import math
from functools import lru_cache

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap

from . import theme


def _p() -> QPainterPath:
    return QPainterPath()


def _home(p: QPainter):
    path = _p()
    path.moveTo(3, 11)
    path.lineTo(12, 3.5)
    path.lineTo(21, 11)
    p.drawPath(path)
    p.drawRoundedRect(QRectF(5.5, 10, 13, 10.5), 2, 2)
    p.drawLine(QPointF(10, 20.5), QPointF(10, 15))
    p.drawLine(QPointF(10, 15), QPointF(14, 15))
    p.drawLine(QPointF(14, 15), QPointF(14, 20.5))


def _folder(p: QPainter):
    path = _p()
    path.moveTo(3, 7)
    path.quadTo(3, 5, 5, 5)
    path.lineTo(9.5, 5)
    path.lineTo(11.5, 7.5)
    path.lineTo(19, 7.5)
    path.quadTo(21, 7.5, 21, 9.5)
    path.lineTo(21, 17.5)
    path.quadTo(21, 19.5, 19, 19.5)
    path.lineTo(5, 19.5)
    path.quadTo(3, 19.5, 3, 17.5)
    path.closeSubpath()
    p.drawPath(path)


def _link(p: QPainter):
    p.drawRoundedRect(QRectF(2.5, 9, 10, 6.5), 3.2, 3.2)
    p.drawRoundedRect(QRectF(11.5, 8.5, 10, 6.5), 3.2, 3.2)


def _upload(p: QPainter):
    p.drawLine(QPointF(12, 15), QPointF(12, 4))
    p.drawLine(QPointF(7.5, 8.5), QPointF(12, 4))
    p.drawLine(QPointF(16.5, 8.5), QPointF(12, 4))
    path = _p()
    path.moveTo(4, 14)
    path.lineTo(4, 18.5)
    path.quadTo(4, 20, 5.5, 20)
    path.lineTo(18.5, 20)
    path.quadTo(20, 20, 20, 18.5)
    path.lineTo(20, 14)
    p.drawPath(path)


def _download(p: QPainter):
    p.drawLine(QPointF(12, 4), QPointF(12, 15))
    p.drawLine(QPointF(7.5, 10.5), QPointF(12, 15))
    p.drawLine(QPointF(16.5, 10.5), QPointF(12, 15))
    p.drawLine(QPointF(4.5, 20), QPointF(19.5, 20))


def _film(p: QPainter):
    p.drawRoundedRect(QRectF(3, 4.5, 18, 15), 2.5, 2.5)
    p.drawLine(QPointF(7.5, 4.5), QPointF(7.5, 19.5))
    p.drawLine(QPointF(16.5, 4.5), QPointF(16.5, 19.5))
    for y in (8.5, 12, 15.5):
        p.drawLine(QPointF(3, y), QPointF(7.5, y))
        p.drawLine(QPointF(16.5, y), QPointF(21, y))


def _subtitles(p: QPainter):
    p.drawRoundedRect(QRectF(2.5, 5, 19, 14), 3, 3)
    p.drawLine(QPointF(6, 12.5), QPointF(10, 12.5))
    p.drawLine(QPointF(12.5, 12.5), QPointF(18, 12.5))
    p.drawLine(QPointF(6, 15.5), QPointF(14, 15.5))
    p.drawLine(QPointF(16.5, 15.5), QPointF(18, 15.5))


def _eraser(p: QPainter):
    path = _p()
    path.moveTo(14.5, 4.5)
    path.lineTo(20.5, 10.5)
    path.lineTo(11, 20)
    path.lineTo(6.5, 20)
    path.lineTo(3.5, 17)
    path.closeSubpath()
    p.drawPath(path)
    p.drawLine(QPointF(9, 10), QPointF(15, 16))
    p.drawLine(QPointF(11, 20), QPointF(20.5, 20))


def _scissors(p: QPainter):
    p.drawEllipse(QRectF(3, 14, 6, 6))
    p.drawEllipse(QRectF(3, 4, 6, 6))
    p.drawLine(QPointF(8.2, 8.7), QPointF(20.5, 18))
    p.drawLine(QPointF(8.2, 15.3), QPointF(20.5, 6))


def _palette(p: QPainter):
    path = _p()
    path.moveTo(12, 3.5)
    path.cubicTo(6.5, 3.5, 3, 7.5, 3, 12)
    path.cubicTo(3, 16.8, 6.8, 20.5, 11.5, 20.5)
    path.cubicTo(13, 20.5, 13.3, 19, 12.6, 18)
    path.cubicTo(11.6, 16.6, 12.5, 15, 14.2, 15)
    path.lineTo(16.5, 15)
    path.cubicTo(19, 15, 21, 13, 21, 10.6)
    path.cubicTo(21, 6.6, 17, 3.5, 12, 3.5)
    p.drawPath(path)
    for x, y in ((8, 11), (10, 7.5), (14.5, 7.5)):
        p.drawEllipse(QPointF(x, y), 0.9, 0.9)


def _mic(p: QPainter):
    p.drawRoundedRect(QRectF(9, 3, 6, 11), 3, 3)
    path = _p()
    path.moveTo(5.5, 11)
    path.cubicTo(5.5, 19, 18.5, 19, 18.5, 11)
    p.drawPath(path)
    p.drawLine(QPointF(12, 17.5), QPointF(12, 21))
    p.drawLine(QPointF(8.5, 21), QPointF(15.5, 21))


def _export(p: QPainter):
    path = _p()
    path.moveTo(11, 4.5)
    path.lineTo(6, 4.5)
    path.quadTo(4, 4.5, 4, 6.5)
    path.lineTo(4, 18)
    path.quadTo(4, 20, 6, 20)
    path.lineTo(17.5, 20)
    path.quadTo(19.5, 20, 19.5, 18)
    path.lineTo(19.5, 13)
    p.drawPath(path)
    p.drawLine(QPointF(11, 13), QPointF(20, 4))
    p.drawLine(QPointF(14, 4), QPointF(20, 4))
    p.drawLine(QPointF(20, 4), QPointF(20, 10))


def _settings(p: QPainter):
    p.drawEllipse(QPointF(12, 12), 3, 3)
    for k in range(8):
        a = k * math.pi / 4
        p.drawLine(QPointF(12 + 6 * math.cos(a), 12 + 6 * math.sin(a)),
                   QPointF(12 + 8.6 * math.cos(a), 12 + 8.6 * math.sin(a)))
    p.drawEllipse(QPointF(12, 12), 6, 6)


def _play(p: QPainter):
    path = _p()
    path.moveTo(8, 5)
    path.lineTo(19, 12)
    path.lineTo(8, 19)
    path.closeSubpath()
    p.save()
    p.setBrush(p.pen().color())
    p.drawPath(path)
    p.restore()


def _pause(p: QPainter):
    p.save()
    p.setBrush(p.pen().color())
    p.drawRoundedRect(QRectF(6.5, 5, 3.5, 14), 1, 1)
    p.drawRoundedRect(QRectF(14, 5, 3.5, 14), 1, 1)
    p.restore()


def _plus(p: QPainter):
    p.drawLine(QPointF(12, 5), QPointF(12, 19))
    p.drawLine(QPointF(5, 12), QPointF(19, 12))


def _x(p: QPainter):
    p.drawLine(QPointF(6, 6), QPointF(18, 18))
    p.drawLine(QPointF(18, 6), QPointF(6, 18))


def _check(p: QPainter):
    path = _p()
    path.moveTo(5, 12.5)
    path.lineTo(10, 17.5)
    path.lineTo(19.5, 7)
    p.drawPath(path)


def _trash(p: QPainter):
    p.drawLine(QPointF(4, 6.5), QPointF(20, 6.5))
    path = _p()
    path.moveTo(6, 6.5)
    path.lineTo(7, 19)
    path.quadTo(7.2, 20.5, 8.7, 20.5)
    path.lineTo(15.3, 20.5)
    path.quadTo(16.8, 20.5, 17, 19)
    path.lineTo(18, 6.5)
    p.drawPath(path)
    p.drawLine(QPointF(9.5, 6.5), QPointF(10, 3.5))
    p.drawLine(QPointF(10, 3.5), QPointF(14, 3.5))
    p.drawLine(QPointF(14, 3.5), QPointF(14.5, 6.5))


def _save(p: QPainter):
    p.drawRoundedRect(QRectF(4, 4, 16, 16), 2.5, 2.5)
    p.drawRect(QRectF(8, 4, 8, 5))
    p.drawRoundedRect(QRectF(7.5, 13, 9, 7), 1, 1)


def _globe(p: QPainter):
    p.drawEllipse(QPointF(12, 12), 8.5, 8.5)
    p.drawEllipse(QRectF(8, 3.5, 8, 17))
    p.drawLine(QPointF(3.5, 12), QPointF(20.5, 12))


def _bolt(p: QPainter):
    path = _p()
    path.moveTo(13.5, 2.5)
    path.lineTo(5, 13.5)
    path.lineTo(11.5, 13.5)
    path.lineTo(10.5, 21.5)
    path.lineTo(19, 10.5)
    path.lineTo(12.5, 10.5)
    path.closeSubpath()
    p.drawPath(path)


def _layers(p: QPainter):
    path = _p()
    path.moveTo(12, 3.5)
    path.lineTo(21, 8.5)
    path.lineTo(12, 13.5)
    path.lineTo(3, 8.5)
    path.closeSubpath()
    p.drawPath(path)
    path2 = _p()
    path2.moveTo(3, 12.5)
    path2.lineTo(12, 17.5)
    path2.lineTo(21, 12.5)
    p.drawPath(path2)
    path3 = _p()
    path3.moveTo(3, 16.5)
    path3.lineTo(12, 21.5)
    path3.lineTo(21, 16.5)
    p.drawPath(path3)


def _users(p: QPainter):
    p.drawEllipse(QPointF(9, 8.5), 3.5, 3.5)
    path = _p()
    path.moveTo(2.5, 20)
    path.cubicTo(2.5, 13.5, 15.5, 13.5, 15.5, 20)
    p.drawPath(path)
    path2 = _p()
    path2.moveTo(15.5, 5.2)
    path2.cubicTo(19.5, 5.5, 19.5, 11.5, 15.5, 11.8)
    p.drawPath(path2)
    path3 = _p()
    path3.moveTo(17.5, 14.5)
    path3.cubicTo(20, 15.3, 21.5, 17, 21.5, 20)
    p.drawPath(path3)


def _music(p: QPainter):
    p.drawEllipse(QPointF(7, 17.5), 3, 2.5)
    p.drawEllipse(QPointF(17, 15.5), 3, 2.5)
    p.drawLine(QPointF(10, 17.5), QPointF(10, 5.5))
    p.drawLine(QPointF(20, 15.5), QPointF(20, 3.5))
    p.drawLine(QPointF(10, 5.5), QPointF(20, 3.5))
    p.drawLine(QPointF(10, 9), QPointF(20, 7))


def _wand(p: QPainter):
    p.drawLine(QPointF(4, 20), QPointF(15, 9))
    p.drawLine(QPointF(13.5, 7.5), QPointF(16.5, 10.5))
    for x, y, r in ((18.5, 4, 1.5), (20.5, 9, 1.0), (11, 4.5, 1.0)):
        p.drawLine(QPointF(x - r, y), QPointF(x + r, y))
        p.drawLine(QPointF(x, y - r), QPointF(x, y + r))


def _search(p: QPainter):
    p.drawEllipse(QPointF(10.5, 10.5), 6, 6)
    p.drawLine(QPointF(15, 15), QPointF(20, 20))


def _refresh(p: QPainter):
    p.drawArc(QRectF(4.5, 4.5, 15, 15), 40 * 16, 290 * 16)
    p.drawLine(QPointF(18.2, 4.5), QPointF(18.2, 8.7))
    p.drawLine(QPointF(18.2, 8.7), QPointF(14, 8.7))


def _clock(p: QPainter):
    p.drawEllipse(QPointF(12, 12), 8.5, 8.5)
    p.drawLine(QPointF(12, 7), QPointF(12, 12))
    p.drawLine(QPointF(12, 12), QPointF(15.5, 14))


def _eye(p: QPainter):
    path = _p()
    path.moveTo(2.5, 12)
    path.cubicTo(6, 5.5, 18, 5.5, 21.5, 12)
    path.cubicTo(18, 18.5, 6, 18.5, 2.5, 12)
    p.drawPath(path)
    p.drawEllipse(QPointF(12, 12), 3, 3)


def _image(p: QPainter):
    p.drawRoundedRect(QRectF(3, 4.5, 18, 15), 2.5, 2.5)
    p.drawEllipse(QPointF(8.5, 9.5), 1.7, 1.7)
    path = _p()
    path.moveTo(3.5, 17.5)
    path.lineTo(9.5, 12.5)
    path.lineTo(13.5, 16)
    path.lineTo(16, 13.5)
    path.lineTo(20.5, 17.5)
    p.drawPath(path)


def _text(p: QPainter):
    p.drawLine(QPointF(5, 6), QPointF(19, 6))
    p.drawLine(QPointF(12, 6), QPointF(12, 19.5))
    p.drawLine(QPointF(9.5, 19.5), QPointF(14.5, 19.5))


def _volume(p: QPainter):
    path = _p()
    path.moveTo(4, 9.5)
    path.lineTo(7.5, 9.5)
    path.lineTo(12, 5.5)
    path.lineTo(12, 18.5)
    path.lineTo(7.5, 14.5)
    path.lineTo(4, 14.5)
    path.closeSubpath()
    p.drawPath(path)
    p.drawArc(QRectF(11, 8.5, 6, 7), -60 * 16, 120 * 16)
    p.drawArc(QRectF(11, 5.5, 10, 13), -60 * 16, 120 * 16)


def _crop(p: QPainter):
    path = _p()
    path.moveTo(7, 3)
    path.lineTo(7, 17)
    path.lineTo(21, 17)
    p.drawPath(path)
    path2 = _p()
    path2.moveTo(3, 7)
    path2.lineTo(17, 7)
    path2.lineTo(17, 21)
    p.drawPath(path2)


def _list(p: QPainter):
    for y in (7, 12, 17):
        p.drawLine(QPointF(9, y), QPointF(20, y))
        p.drawEllipse(QPointF(5, y), 0.8, 0.8)


def _split(p: QPainter):
    p.drawLine(QPointF(12, 3.5), QPointF(12, 20.5))
    p.drawRoundedRect(QRectF(3, 7, 6, 10), 1.5, 1.5)
    p.drawRoundedRect(QRectF(15, 7, 6, 10), 1.5, 1.5)


def _merge(p: QPainter):
    p.drawRoundedRect(QRectF(3, 7, 18, 10), 2, 2)
    p.drawLine(QPointF(9, 12), QPointF(15, 12))
    p.drawLine(QPointF(12, 9), QPointF(12, 15))


def _sparkle(p: QPainter):
    path = _p()
    path.moveTo(12, 3)
    path.quadTo(13, 11, 21, 12)
    path.quadTo(13, 13, 12, 21)
    path.quadTo(11, 13, 3, 12)
    path.quadTo(11, 11, 12, 3)
    p.drawPath(path)


def _stack(p: QPainter):
    for i, y in enumerate((4, 9.5, 15)):
        p.drawRoundedRect(QRectF(4 + i, y, 16 - 2 * i, 4.5), 1.5, 1.5)


def _chev_left(p: QPainter):
    path = _p()
    path.moveTo(15, 5)
    path.lineTo(8, 12)
    path.lineTo(15, 19)
    p.drawPath(path)


def _chev_right(p: QPainter):
    path = _p()
    path.moveTo(9, 5)
    path.lineTo(16, 12)
    path.lineTo(9, 19)
    p.drawPath(path)


def _chev_down(p: QPainter):
    path = _p()
    path.moveTo(6, 9)
    path.lineTo(12, 15)
    path.lineTo(18, 9)
    p.drawPath(path)


def _info(p: QPainter):
    p.drawEllipse(QPointF(12, 12), 8.5, 8.5)
    p.drawLine(QPointF(12, 11), QPointF(12, 16.5))
    p.drawEllipse(QPointF(12, 7.8), 0.6, 0.6)


def _cpu(p: QPainter):
    p.drawRoundedRect(QRectF(6, 6, 12, 12), 2, 2)
    p.drawRect(QRectF(9.5, 9.5, 5, 5))
    for v in (9, 12, 15):
        p.drawLine(QPointF(v, 3), QPointF(v, 6))
        p.drawLine(QPointF(v, 18), QPointF(v, 21))
        p.drawLine(QPointF(3, v), QPointF(6, v))
        p.drawLine(QPointF(18, v), QPointF(21, v))


ICONS = {
    "home": _home, "folder": _folder, "link": _link, "upload": _upload, "download": _download, "film": _film,
    "subtitles": _subtitles, "eraser": _eraser, "scissors": _scissors, "palette": _palette, "mic": _mic,
    "export": _export, "settings": _settings, "play": _play, "pause": _pause, "plus": _plus, "x": _x,
    "check": _check, "trash": _trash, "save": _save, "globe": _globe, "bolt": _bolt, "layers": _layers,
    "users": _users, "music": _music, "wand": _wand, "search": _search, "refresh": _refresh, "clock": _clock,
    "eye": _eye, "image": _image, "text": _text, "volume": _volume, "crop": _crop, "list": _list,
    "split": _split, "merge": _merge, "sparkle": _sparkle, "stack": _stack, "chev_left": _chev_left,
    "chev_right": _chev_right, "chev_down": _chev_down, "info": _info, "cpu": _cpu,
}


def paint_icon(p: QPainter, name: str, rect: QRectF, color: QColor | str, width: float = 1.8):
    """Vẽ icon vào vùng rect bằng painter có sẵn."""
    fn = ICONS.get(name)
    if not fn:
        return
    p.save()
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    p.translate(rect.x(), rect.y())
    s = min(rect.width(), rect.height()) / 24.0
    p.scale(s, s)
    pen = QPen(QColor(color), width)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    p.setPen(pen)
    p.setBrush(Qt.BrushStyle.NoBrush)
    fn(p)
    p.restore()


@lru_cache(maxsize=512)
def pixmap(name: str, color: str = theme.TEXT, size: int = 20, dpr: float = 2.0) -> QPixmap:
    pm = QPixmap(int(size * dpr), int(size * dpr))
    pm.setDevicePixelRatio(dpr)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    paint_icon(p, name, QRectF(0, 0, size, size), color)
    p.end()
    return pm


def icon(name: str, color: str = theme.TEXT, size: int = 20) -> QIcon:
    ic = QIcon()
    ic.addPixmap(pixmap(name, color, size))
    return ic


def app_logo(size: int = 256) -> QPixmap:
    """Logo TraMi: ô vuông bo góc gradient đỏ-cam với chữ 译 (dịch)."""
    from PySide6.QtGui import QFont

    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    r = QRectF(size * 0.04, size * 0.04, size * 0.92, size * 0.92)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(theme.accent_gradient(r.left(), r.top(), r.right(), r.bottom()))
    p.drawRoundedRect(r, size * 0.24, size * 0.24)
    p.setPen(QColor("#FFFFFF"))
    f = QFont()
    f.setPixelSize(int(size * 0.56))
    f.setBold(True)
    p.setFont(f)
    p.drawText(r, Qt.AlignmentFlag.AlignCenter, "译")
    p.end()
    return pm
