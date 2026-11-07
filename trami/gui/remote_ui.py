"""Cài đặt → Điều khiển từ xa (2.3.0): bật máy chủ trong tool, mã QR ghép điện thoại, địa chỉ mạng nhà / VPS, mật
khẩu, cổng, thiết bị đã ghép. Thẻ tự lưu (không chờ nút "Lưu cài đặt") rồi bật / tắt máy chủ ngay."""
from __future__ import annotations

import sys
import time
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (QApplication, QFileDialog, QGridLayout, QHBoxLayout, QLabel, QLineEdit, QMessageBox,
                               QSpinBox, QVBoxLayout, QWidget)

from ..config import load_settings, save_settings
from .common import run_async
from .widgets import SectionCard, Switch, button, flow, label

HELP = ("<b>Điện thoại điều khiển tool</b> (cần tool đang mở trên máy tính)<br>"
        "1. Cài app <b>Subvideo</b> trên Android (file APK) → bấm <b>Quét mã QR</b> → quét mã bên cạnh. "
        "Không có app: mở trình duyệt điện thoại vào địa chỉ <i>Cùng Wi-Fi nhà</i>, nhập mã 6 số.<br>"
        "2. <b>Ở ngoài</b> (4G, Wi-Fi khác): cần VPS. Bấm <b>Lưu file cài VPS…</b> → chép file lên VPS → chạy "
        "<code>sudo bash cai_vps_subvideo.sh tên-miền-của-bạn</code> (không có tên miền thì bỏ phần tên miền) → "
        "dán dòng <code>subvideo-vps://…</code> nó in ra vào ô <b>Chuỗi kết nối VPS</b> → Lưu → quét lại mã QR.<br>"
        "Điện thoại chỉ làm được các việc trong tool: không xem thư mục máy, không thấy API key, cookie, mật khẩu "
        "tài khoản. Ngắt 1 điện thoại: bấm Ngắt ở danh sách bên dưới.")


def autostart_cmd() -> str:
    """Lệnh mở tool khi bật máy (Windows: HKCU\\…\\Run)."""
    if getattr(sys, "frozen", False):
        return f'"{Path(sys.executable).resolve()}"'
    exe = Path(sys.executable)
    pyw = exe.with_name("pythonw.exe")
    root = Path(__file__).resolve().parents[2]
    return f'"{pyw if pyw.exists() else exe}" "{root / "TraMi.py"}"'


def autostart_get() -> bool:
    if sys.platform != "win32":
        return False
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run") as k:
            winreg.QueryValueEx(k, "Subvideo")
            return True
    except OSError:
        return False


def autostart_set(on: bool) -> None:
    if sys.platform != "win32":
        raise OSError("Chỉ có trên Windows")
    import winreg

    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run", 0,
                        winreg.KEY_SET_VALUE) as k:
        if on:
            winreg.SetValueEx(k, "Subvideo", 0, winreg.REG_SZ, autostart_cmd())
        else:
            try:
                winreg.DeleteValue(k, "Subvideo")
            except FileNotFoundError:
                pass


def _ago(t: float) -> str:
    d = time.time() - (t or 0)
    if d < 90:
        return "vừa xong"
    if d < 3600:
        return f"{int(d // 60)} phút trước"
    if d < 86400:
        return f"{int(d // 3600)} giờ trước"
    return time.strftime("%d/%m/%Y", time.localtime(t))


class RemoteCard(SectionCard):
    def __init__(self, win, parent=None):
        super().__init__("Điều khiển từ xa", "share", "Mở tool bằng điện thoại – cùng Wi-Fi nhà hoặc ở ngoài qua VPS "
                                                       "của bạn (tool tự nối ra VPS, không mở cổng modem)", parent)
        self.win = win
        st = load_settings()
        self.on = Switch(bool(st.get("remote_on")))
        self.on.setToolTip("Bật máy chủ điều khiển trong tool")
        self.on.toggled.connect(self._toggle)
        self.head.addWidget(self.on, 0, Qt.AlignmentFlag.AlignTop)

        body = QHBoxLayout()
        body.setSpacing(16)
        qcol = QVBoxLayout()
        self.qr = QLabel()
        self.qr.setFixedSize(220, 220)
        self.qr.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.qr.setStyleSheet("background:#FFFFFF;border-radius:10px;color:#1A0B08")
        self.code = label("", "muted")
        self.code.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.newqr = button("Mã QR mới", "refresh", "Tạo mã ghép mới (mã cũ hết dùng được)", flat=True,
                            on_click=self._new_code)
        qcol.addWidget(self.qr, 0, Qt.AlignmentFlag.AlignHCenter)
        qcol.addWidget(self.code)
        qcol.addWidget(self.newqr, 0, Qt.AlignmentFlag.AlignHCenter)
        qcol.addStretch(1)
        body.addLayout(qcol)

        info = QVBoxLayout()
        info.setSpacing(8)
        g = QGridLayout()
        g.setHorizontalSpacing(10)
        g.setVerticalSpacing(6)
        self.lan = QLabel("–")
        self.lan.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.vps = QLabel("–")
        self.vps.setWordWrap(True)
        self.vps.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        copy_lan = button("Chép", "", "Chép địa chỉ mạng nhà", flat=True, on_click=lambda: self._copy(self.lan))
        g.addWidget(label("Cùng Wi-Fi nhà", "muted"), 0, 0)
        g.addWidget(self.lan, 0, 1)
        g.addWidget(copy_lan, 0, 2)
        g.addWidget(label("Ở ngoài (VPS)", "muted"), 1, 0)
        g.addWidget(self.vps, 1, 1, 1, 2)
        g.setColumnStretch(1, 1)
        info.addLayout(g)

        self.vps_in = QLineEdit()
        self.vps_in.setPlaceholderText("Dán dòng subvideo-vps://… mà file cài VPS in ra")
        self.vps_in.setEchoMode(QLineEdit.EchoMode.Password)
        info.addWidget(label("Chuỗi kết nối VPS", "muted"))
        info.addWidget(self.vps_in)
        info.addWidget(flow(button("Lưu VPS", "save", on_click=self._save_vps),
                            button("Bỏ VPS", "", flat=True, on_click=self._clear_vps),
                            button("Lưu file cài VPS…", "download", "File cài trạm trung chuyển lên VPS Ubuntu",
                                   flat=True, on_click=self._save_installer),
                            button("Hướng dẫn", "", flat=True, on_click=self._help)))

        self.pw = QLineEdit()
        self.pw.setEchoMode(QLineEdit.EchoMode.Password)
        self.pw_state = label("", "muted")
        self.port = QSpinBox()
        self.port.setRange(1024, 65535)
        self.port.setValue(int(st.get("remote_port") or 8765))
        self.port.setToolTip("Cổng trong mạng nhà (đổi khi cổng 8765 bị chương trình khác dùng)")
        row = QHBoxLayout()
        row.addWidget(label("Mật khẩu", "muted"))
        row.addWidget(self.pw, 1)
        row.addWidget(button("Đặt", "", "Đặt mật khẩu đăng nhập bằng trình duyệt (ít nhất 6 ký tự)",
                             on_click=self._set_pw))
        row.addSpacing(12)
        row.addWidget(label("Cổng", "muted"))
        row.addWidget(self.port)
        row.addWidget(button("Đổi", "", "Đổi cổng rồi khởi động lại máy chủ", flat=True, on_click=self._save_port))
        info.addLayout(row)
        info.addWidget(self.pw_state)

        def sw(text, key, default=True, on_change=None):
            s = Switch(bool(st.get(key, default)))
            s.toggled.connect(on_change or (lambda v, k=key: save_settings({k: bool(v)})))
            box = QHBoxLayout()
            box.addWidget(label(text), 1)
            box.addWidget(s)
            info.addLayout(box)
            return s

        self.notify = sw("Báo lên điện thoại khi việc xong / lỗi", "remote_notify")
        self.awake = sw("Giữ máy không ngủ khi đang bật điều khiển từ xa", "remote_awake",
                        on_change=self._awake_changed)
        self.auto = Switch(autostart_get())
        self.auto.setEnabled(sys.platform == "win32")
        self.auto.toggled.connect(self._autostart)
        box = QHBoxLayout()
        box.addWidget(label("Tự mở tool khi bật máy (Windows)"), 1)
        box.addWidget(self.auto)
        info.addLayout(box)
        body.addLayout(info, 1)
        self.lay.addLayout(body)

        self.state = label("", "muted", wrap=True)
        self.lay.addWidget(self.state)
        self.lay.addWidget(label("Điện thoại đã ghép", "h3"))
        self.devbox = QVBoxLayout()
        self.devbox.setSpacing(6)
        self.lay.addLayout(self.devbox)
        self.lay.addWidget(label("Trang từ xa chỉ làm được các việc trong tool: không xem thư mục máy, không hiện API "
                                 "key, cookie hay mật khẩu tài khoản.", "muted", wrap=True))
        self._dev_sig = None
        self._qr_for = ""
        self.timer = QTimer(self, interval=3000, timeout=self.refresh)
        self.timer.start()
        QTimer.singleShot(200, self.refresh)

    # ------------------------------------------------------------ trạng thái
    def refresh(self):
        if not self.isVisible() and self._qr_for:
            return
        from ..remote import server

        st = load_settings()
        s = server.status(st)
        on = s["on"]
        self.lan.setText(" · ".join(s["lan"]) if s["lan"] else ("(đang bật…)" if on else "(đang tắt)"))
        t = s["tunnel"]
        if not t["url"]:
            self.vps.setText("Chưa có VPS – chỉ dùng được khi điện thoại cùng Wi-Fi với máy tính")
        else:
            mark = {"on": "✓", "connecting": "…", "error": "⚠", "off": "–"}.get(t["state"], "")
            self.vps.setText(f"{t['url']}  {mark} {t['msg'] or ('đang tắt' if not on else '')}")
        self.pw_state.setText("Đã đặt mật khẩu (dùng khi mở bằng trình duyệt)" if s["password"]
                              else "Chưa đặt mật khẩu – ghép bằng mã QR / mã 6 số là đủ")
        if not on:
            self.state.setText("Đang tắt. Bật công tắc góc trên để điện thoại điều khiển được tool.")
        elif s["error"]:
            self.state.setText("⚠ " + s["error"])
        elif s["running"]:
            self.state.setText(f"✓ Đang chạy · cổng {s['port']} · " + (f"https (chứng chỉ tự ký, vân tay {s['fp'][:16]}… – app ghim)"
                               if s["https"] else "http (thiếu gói cryptography – nên cài: pip install cryptography)"))
        for w in (self.qr, self.code, self.newqr):
            w.setEnabled(bool(s["running"]))
        if s["running"]:
            link = server.pair_link(st)
            code, until = server.PAIRING.current()
            if link != self._qr_for:
                from .link_login import qr_pixmap

                self.qr.setPixmap(qr_pixmap(link, 210))
                self._qr_for = link
            left = max(0, int(until - time.time()))
            self.code.setText(f"Mã ghép: {code[:3]} {code[3:]} · còn {left // 60}:{left % 60:02d}" if code else "")
        else:
            self.qr.clear()
            self.qr.setText("Bật điều khiển\ntừ xa để hiện mã")
            self._qr_for = ""
            self.code.setText("")
        sig = [(d["id"], d["online"], int(d["seen"] or 0) // 60) for d in s["devices"]]
        if sig != self._dev_sig:
            self._dev_sig = sig
            self._fill_devices(s["devices"])

    def _fill_devices(self, devs: list[dict]):
        while self.devbox.count():
            it = self.devbox.takeAt(0)
            w = it.widget()
            if w:            # ẩn + bỏ cha trước deleteLater: không thì hàng cũ còn vẽ chồng lên hàng mới
                w.hide()
                w.setParent(None)
                w.deleteLater()
        if not devs:
            self.devbox.addWidget(label("Chưa có điện thoại nào – quét mã QR để ghép.", "muted"))
            return
        for d in sorted(devs, key=lambda x: -(x.get("seen") or 0)):
            row = QWidget()
            h = QHBoxLayout(row)
            h.setContentsMargins(0, 0, 0, 0)
            dot = "🟢" if d["online"] else "⚪"
            via = "qua VPS" if d.get("via") == "vps" else "mạng nhà"
            h.addWidget(label(f"{dot} {d.get('name') or 'Điện thoại'} · {via} · vào {_ago(d.get('seen'))}"), 1)
            h.addWidget(button("Ngắt", "", "Điện thoại này phải ghép lại mới vào được", flat=True,
                               on_click=lambda _=False, i=d["id"]: self._revoke(i)))
            self.devbox.addWidget(row)
        if len(devs) > 1:
            self.devbox.addWidget(button("Ngắt tất cả", "", flat=True, on_click=lambda: self._revoke(None)))

    # ------------------------------------------------------------ thao tác
    def _apply(self, then=None):
        from ..remote import server

        def done(s):
            self._qr_for = ""
            self.refresh()
            if then:
                then(s)
        run_async(lambda: server.apply_settings(), done, lambda m: self.win.toast("Điều khiển từ xa: " + m, "err"))

    def _toggle(self, v: bool):
        save_settings({"remote_on": bool(v)})
        self._apply(lambda s: self.win.toast("Đã bật điều khiển từ xa – quét mã QR bằng app Subvideo" if v and
                                             s.get("running") else ("⚠ " + s["error"]) if v and s.get("error")
                                             else "Đã tắt điều khiển từ xa", "ok" if not s.get("error") else "err"))

    def _new_code(self):
        from ..remote import server

        server.PAIRING.new()
        self._qr_for = ""
        self.refresh()

    def _copy(self, lb: QLabel):
        t = lb.text().split(" · ")[0]
        if t.startswith("http"):
            QApplication.clipboard().setText(t)
            self.win.toast("Đã chép " + t)

    def _set_pw(self):
        from ..remote import server

        try:
            server.set_password(self.pw.text())
        except ValueError as e:
            return self.win.toast(str(e), "warn")
        self.pw.clear()
        self.win.toast("Đã đặt mật khẩu điều khiển từ xa", "ok")
        self.refresh()

    def _save_port(self):
        save_settings({"remote_port": int(self.port.value())})
        if load_settings().get("remote_on"):
            self._apply(lambda s: self.win.toast(f"Máy chủ chạy ở cổng {s['port']}" if s.get("running")
                                                 else "⚠ " + (s.get("error") or "Không mở được cổng"),
                                                 "ok" if s.get("running") else "err"))
        else:
            self.win.toast("Đã lưu cổng")

    def _save_vps(self):
        from ..remote import server

        try:
            v = server.parse_vps(self.vps_in.text())
        except ValueError as e:
            return self.win.toast(str(e), "warn")
        save_settings(v)
        self.vps_in.clear()
        self._apply(lambda s: self.win.toast("Đã lưu VPS – đang nối… Quét lại mã QR trên điện thoại để app biết "
                                             "địa chỉ VPS", "ok"))

    def _clear_vps(self):
        save_settings({"remote_vps_url": "", "remote_vps_secret": "", "remote_vps_fp": ""})
        self._apply(lambda s: self.win.toast("Đã bỏ VPS"))

    def _save_installer(self):
        from ..remote import server

        path, _ = QFileDialog.getSaveFileName(self, "Lưu file cài VPS", str(Path.home() / "cai_vps_subvideo.sh"),
                                              "Shell script (*.sh)")
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8", newline="\n") as f:   # Linux: dòng kết thúc \n
                f.write(server.vps_installer())
        except OSError as e:
            return self.win.toast(f"Không ghi được file: {e}", "err")
        self.win.toast("Đã lưu " + Path(path).name + " – chép lên VPS (WinSCP / scp) rồi chạy: sudo bash "
                       + Path(path).name + " tên-miền-của-bạn", "ok")

    def _help(self):
        QMessageBox.information(self, "Điều khiển từ xa", HELP)

    def _awake_changed(self, v: bool):
        save_settings({"remote_awake": bool(v)})
        if load_settings().get("remote_on"):
            from ..remote import server

            server.SERVER.stop()
            self._apply()

    def _autostart(self, v: bool):
        try:
            autostart_set(bool(v))
            self.win.toast("Tool sẽ tự mở khi bật máy" if v else "Đã tắt tự mở tool khi bật máy", "ok")
        except OSError as e:
            self.win.toast(f"Không đặt được: {e}", "err")

    def _revoke(self, did: str | None):
        from ..remote import server

        if did is None and QMessageBox.question(self, "Ngắt tất cả?", "Ngắt mọi điện thoại đã ghép? Phải quét mã QR "
                                                "lại mới vào được.") != QMessageBox.StandardButton.Yes:
            return
        n = server.DEVICES.revoke(did)
        self._dev_sig = None
        self.refresh()
        self.win.toast(f"Đã ngắt {n} điện thoại")
