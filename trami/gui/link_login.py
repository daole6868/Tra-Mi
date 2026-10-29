"""Hộp đăng nhập Bilibili bằng mã QR (quét bằng app Bilibili trên điện thoại) – xem trami/sitelogin.py."""
from __future__ import annotations

import numpy as np
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import QDialog, QLabel, QVBoxLayout

from .. import sitelogin
from .common import run_async
from .widgets import button, hbox, label

QR_PX = 264


def qr_pixmap(text: str, px: int = QR_PX) -> QPixmap:
    """Mã QR đen trên nền trắng, chừa lề 4 ô (máy quét cần), phóng to giữ ô vuông sắc nét."""
    m = sitelogin.qr_matrix(text)
    m = np.pad(m, 4, constant_values=False)
    img = np.where(m, 0, 255).astype(np.uint8)
    k = max(1, px // img.shape[0])
    img = np.ascontiguousarray(np.kron(img, np.ones((k, k), np.uint8)))
    h, w = img.shape
    return QPixmap.fromImage(QImage(img.data, w, h, w, QImage.Format.Format_Grayscale8).copy())


class BiliLoginDialog(QDialog):
    """Lấy mã -> hỏi Bilibili mỗi 2 giây: chưa quét / đã quét chờ xác nhận / hết hạn / xong (lưu cookie)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Đăng nhập Bilibili")
        self.key = ""
        self._busy = False
        self._closed = False
        self._fails = 0
        v = QVBoxLayout(self)
        v.setContentsMargins(22, 20, 22, 18)
        v.setSpacing(10)
        v.addWidget(label("Quét mã bằng app Bilibili trên điện thoại", "h3"))
        v.addWidget(label("Mở app Bilibili → biểu tượng quét mã (góc trên, cạnh ô tìm kiếm) → quét mã dưới đây → bấm "
                          "xác nhận đăng nhập (确认登录). Không cần nhập mật khẩu vào tool – tool chỉ lưu cookie đăng nhập "
                          "trên máy này để tải video 720p / 1080p.", "muted", wrap=True))
        self.qr = QLabel()
        self.qr.setFixedSize(QR_PX + 16, QR_PX + 16)
        self.qr.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.qr.setStyleSheet("background: white; border-radius: 10px;")
        v.addWidget(self.qr, 0, Qt.AlignmentFlag.AlignHCenter)
        self.status = label("Đang lấy mã…", wrap=True)
        self.status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        v.addWidget(self.status)
        self.again = button("Lấy mã mới", "refresh", on_click=self._new_code)
        self.again.hide()
        v.addLayout(hbox(None, self.again, button("Đóng", "x", on_click=self.reject)))
        self.setMinimumWidth(420)
        self.timer = QTimer(self, interval=2000, timeout=self._poll)
        self._new_code()

    def done(self, r):
        self._closed = True
        self.timer.stop()
        super().done(r)

    def _say(self, text: str, color: str = ""):
        self.status.setText(text)
        self.status.setStyleSheet(f"color: {color};" if color else "")

    def _new_code(self):
        self.again.hide()
        self.key = ""
        self._fails = 0
        self._say("Đang lấy mã…")
        run_async(sitelogin.bili_qr_start, self._got_code, self._code_err)

    def _got_code(self, r: dict):
        if self._closed:
            return
        self.key = r["key"]
        try:
            self.qr.setPixmap(qr_pixmap(r["url"]))
        except Exception as e:  # noqa: BLE001
            self._say(f"Không vẽ được mã QR: {e}", "#F87171")
            return
        self._say("Chờ quét mã… (mã dùng được khoảng 3 phút)")
        self.timer.start()

    def _code_err(self, msg: str):
        if self._closed:
            return
        self._say(f"Không lấy được mã từ Bilibili: {msg[:200]}\nKiểm tra mạng rồi bấm Lấy mã mới.", "#F87171")
        self.again.show()

    def _poll(self):
        if self._busy or not self.key:
            return
        self._busy = True
        key = self.key
        run_async(lambda: sitelogin.bili_qr_poll(key), self._polled, self._poll_err)

    def _polled(self, r: dict):
        self._busy = False
        if self._closed:
            return
        self._fails = 0
        st = r.get("state")
        if st == "wait":
            self._say("Chờ quét mã… (mã dùng được khoảng 3 phút)")
        elif st == "scanned":
            self._say("Đã quét – bấm xác nhận đăng nhập (确认登录) trên điện thoại", "#60A5FA")
        elif st == "expired":
            self.timer.stop()
            self._say("Mã đã hết hạn – bấm Lấy mã mới.", "#F59E0B")
            self.again.show()
        elif st == "ok":
            self.timer.stop()
            self._say("✓ Đã đăng nhập Bilibili", "#4ADE80")
            QTimer.singleShot(700, self.accept)
        else:
            self.timer.stop()
            self._say(f"Bilibili báo lỗi: {r.get('msg', '')}", "#F87171")
            self.again.show()

    def _poll_err(self, msg: str):
        self._busy = False
        if self._closed:
            return
        self._fails += 1
        if self._fails >= 5:
            self.timer.stop()
            self._say(f"Mất kết nối tới Bilibili: {msg[:160]}", "#F87171")
            self.again.show()
        else:
            self._say("Mạng chập chờn, đang thử lại…", "#F59E0B")
