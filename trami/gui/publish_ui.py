"""Đăng video lên YouTube / Facebook: thẻ tài khoản ở Cài đặt (Zernio, YouTube API riêng, Facebook API riêng) và mục
"Đăng video" ở tab Xuất (chọn nơi đăng, AI viết tiêu đề / mô tả / hashtag, hẹn giờ, ảnh bìa, lịch sử đăng)."""
from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

from PySide6.QtCore import QDateTime, Qt, QUrl
from PySide6.QtGui import QDesktopServices, QGuiApplication
from PySide6.QtWidgets import (QAbstractItemView, QDateTimeEdit, QFileDialog, QHeaderView, QInputDialog, QLineEdit,
                               QMessageBox, QPlainTextEdit, QTableWidget, QTableWidgetItem, QVBoxLayout)

from .. import browser_post, publish
from ..config import load_settings, save_settings
from . import icons, theme
from .common import run_async
from .widgets import Form, GradientButton, SectionCard, Switch, button, combo, flow, hbox, label, set_combo

ZN_HELP = """<b>Zernio</b> là dịch vụ đăng bài đã được YouTube và Meta duyệt, nên video lên <b>Công khai ngay</b>.<br><br>
1. Đăng ký tài khoản ở <b>zernio.com</b> → mục <b>API Keys</b> → tạo key → dán vào ô <i>API key</i> → <b>Lưu cài đặt</b>.<br>
2. Bấm <b>Kết nối YouTube</b> / <b>Kết nối Facebook</b>: trang Zernio mở trên trình duyệt → đăng nhập tài khoản YouTube /
Facebook → cho phép (Facebook: chọn Trang).<br>
3. Quay lại đây bấm <b>Tải lại danh sách</b> – tài khoản hiện ở mục <i>Nơi đăng</i> của tab Xuất.<br><br>
Giá: 2 tài khoản đầu miễn phí, thêm tài khoản thì trả phí theo bảng giá trên zernio.com."""

YT_HELP = """<b>API riêng của YouTube</b> – miễn phí, dùng dự án Google Cloud của bạn.<br><br>
1. Vào <b>console.cloud.google.com</b> → tạo dự án mới.<br>
2. <i>APIs &amp; Services → Library</i> → tìm <b>YouTube Data API v3</b> → <b>Enable</b>.<br>
3. <i>OAuth consent screen</i> (Google Auth Platform): kiểu <b>External</b>, điền tên app + email; mục <i>Audience → Test
users</i> thêm Gmail của bạn. Muốn giữ đăng nhập lâu hơn 7 ngày: bấm <b>Publish app</b> (In production).<br>
4. <i>Credentials → Create credentials → OAuth client ID</i> → loại <b>Desktop app</b> → tải file JSON.<br>
5. Ở đây bấm <b>Nạp file client_secret.json…</b> → <b>Lưu cài đặt</b> → <b>Đăng nhập Google</b> → chọn kênh → cho phép
(gặp cảnh báo "Google chưa xác minh ứng dụng này" thì bấm <i>Nâng cao → Đi tới …</i>).<br><br>
<b>Lưu ý:</b> dự án chưa qua kiểm duyệt (audit) của Google thì video tải lên bị <b>khoá ở chế độ Riêng tư</b>, và hạn mức
mặc định chỉ vài video mỗi ngày. Muốn đăng công khai ngay → dùng Zernio."""

FB_HELP = """<b>API riêng của Facebook</b> – miễn phí, dùng app Meta của bạn, chỉ đăng lên <b>Trang (Fanpage)</b> bạn
quản lý (Facebook không cho đăng lên trang cá nhân).<br><br>
1. Vào <b>developers.facebook.com</b> → <i>My Apps → Create App</i> → chọn trường hợp <b>Manage everything on your Page</b>
(hoặc loại Business).<br>
2. <i>App settings → Basic</i>: chép <b>App ID</b> và <b>App Secret</b> vào đây → <b>Lưu cài đặt</b>.<br>
3. Để app ở chế độ <b>Development</b>: chỉ bạn (quản trị app) dùng, không cần Meta duyệt.<br>
4. Bấm <b>Đăng nhập Facebook</b> → chọn các Trang → cho phép.<br>
&nbsp;&nbsp;&nbsp;Hoặc vào <b>Graph API Explorer</b> (developers.facebook.com/tools/explorer) → chọn app → thêm quyền
<i>pages_show_list, pages_read_engagement, pages_manage_posts, publish_video</i> → <b>Generate Access Token</b> → chép mã →
bấm <b>Dán mã truy cập…</b><br><br>
<b>Lưu ý:</b> bài đăng từ app đang ở chế độ Development có thể chỉ quản trị viên thấy. Người khác không thấy bài thì chuyển
app sang <b>Live</b> (cần link chính sách bảo mật) hoặc đăng qua Zernio."""


BR_HELP = """<b>Mở Chrome &amp; điền sẵn</b> – miễn phí, đăng được YouTube, Facebook (Trang), TikTok; bài lên <b>Công
khai ngay</b> vì chính bạn bấm Đăng trên trang của họ.<br><br>
1. Cài Playwright 1 lần: mở cửa sổ lệnh ở thư mục tool, chạy<br><code>.venv\\Scripts\\python -m pip install playwright</code><br>
(không cần tải trình duyệt – tool dùng Chrome / Edge có sẵn trên máy).<br>
2. Bấm <b>Đăng nhập YouTube / Facebook / TikTok</b>: Chrome mở với hồ sơ riêng của Subvideo → đăng nhập như bình thường →
<b>đóng cửa sổ Chrome đó</b>. Chỉ làm 1 lần. Nhiều kênh / nhiều tài khoản: bấm <b>Thêm…</b> để có hồ sơ khác.<br>
3. Tab Xuất → Đăng video: chọn nơi đăng <i>Chrome · Hồ sơ 1</i> → <b>Đăng</b>. Tool mở Chrome, gắn video, điền tiêu đề, mô
tả, hashtag, chọn khán giả / chế độ hiển thị rồi viền cam nút Đăng. Bạn kiểm tra, bấm Đăng, xong thì đóng tab.<br><br>
<b>Lưu ý:</b> tool KHÔNG tự bấm Đăng. Trang YouTube / Facebook / TikTok đổi giao diện thì có ô tool chưa điền được – tool
báo "điền tay" ở dòng tiến độ. Hồ sơ Chrome của tool tách riêng với Chrome bạn dùng hằng ngày."""


def _secret() -> QLineEdit:
    e = QLineEdit()
    e.setEchoMode(QLineEdit.EchoMode.Password)
    return e


def _reveal(path: Path):
    """Mở thư mục chứa file và chọn sẵn file (Windows Explorer) để kéo vào trang tải lên."""
    try:
        if sys.platform.startswith("win"):
            subprocess.Popen(["explorer", "/select,", str(path)])
            return
    except OSError:
        pass
    QDesktopServices.openUrl(QUrl.fromLocalFile(str(path.parent)))


# ================================================================== Cài đặt → Đăng video
class AccountCards:
    """3 thẻ ở Cài đặt: Zernio, YouTube (API riêng), Facebook (API riêng). Ô nhập nằm trong SettingsPage.KEYS."""

    def __init__(self, page):
        self.page = page
        z = SectionCard("Zernio", "share", "Đăng Công khai ngay lên YouTube / Facebook, hẹn giờ được. 2 tài khoản đầu "
                                           "miễn phí.")
        f = Form()
        self.zn_key = f.row("API key", _secret(), "zernio.com → API Keys → tạo key")
        z.add(f)
        z.add(flow(button("Kết nối YouTube", "plus", flat=True, on_click=lambda: self._zn_connect("youtube")),
                   button("Kết nối Facebook", "plus", flat=True, on_click=lambda: self._zn_connect("facebook")),
                   button("Tải lại danh sách", "refresh", flat=True, on_click=self._zn_refresh),
                   button("Hướng dẫn", "info", flat=True, on_click=lambda: self._help("Zernio", ZN_HELP))))
        self.zn_out = label("", "muted", wrap=True)
        z.add(self.zn_out)

        y = SectionCard("YouTube", "film", "API riêng: miễn phí, dùng dự án Google Cloud của bạn. Dự án chưa được Google "
                                           "duyệt thì video bị khoá ở chế độ Riêng tư.")
        f = Form()
        self.yt_cid = f.row("Client ID", QLineEdit())
        self.yt_sec = f.row("Client secret", _secret())
        y.add(f)
        y.add(flow(button("Nạp file client_secret.json…", "folder", flat=True, on_click=self._yt_file),
                   button("Đăng nhập Google", "link", flat=True, on_click=self._yt_login),
                   button("Hướng dẫn", "info", flat=True, on_click=lambda: self._help("YouTube", YT_HELP))))
        self.yt_out = label("", "muted", wrap=True)
        y.add(self.yt_out)
        self.yt_box = QVBoxLayout()
        y.add(self.yt_box)

        fb = SectionCard("Facebook", "users", "API riêng: miễn phí, dùng app Meta của bạn. Chỉ đăng lên Trang (Fanpage) "
                                              "bạn quản lý.")
        f = Form()
        self.fb_app = f.row("App ID", QLineEdit())
        self.fb_sec = f.row("App Secret", _secret())
        fb.add(f)
        fb.add(flow(button("Đăng nhập Facebook", "link", flat=True, on_click=self._fb_login),
                    button("Dán mã truy cập…", "text", flat=True, on_click=self._fb_paste),
                    button("Hướng dẫn", "info", flat=True, on_click=lambda: self._help("Facebook", FB_HELP))))
        self.fb_out = label("", "muted", wrap=True)
        fb.add(self.fb_out)
        self.fb_box = QVBoxLayout()
        fb.add(self.fb_box)
        br = SectionCard("Chrome (điền sẵn)", "globe", "Tool mở Chrome, gắn video, điền tiêu đề / mô tả / hashtag rồi dừng "
                                                       "ở nút Đăng – bạn tự bấm. YouTube, Facebook, TikTok · miễn phí.")
        f = Form()
        self.br_prof = combo([])
        self.br_prof.setMinimumContentsLength(14)
        f.row("Hồ sơ Chrome", hbox(self.br_prof,
                                   button("Thêm…", "plus", "Hồ sơ mới cho kênh / tài khoản khác", flat=True,
                                          on_click=self._br_add),
                                   button("Đổi tên…", flat=True, on_click=self._br_rename),
                                   button("Xoá", "trash", "Xoá hồ sơ (mất đăng nhập đã lưu trong hồ sơ)", flat=True,
                                          on_click=self._br_remove), None))
        self.br_exe = QLineEdit()
        self.br_exe.setPlaceholderText("(tự tìm Chrome / Edge)")
        f.row("Trình duyệt", hbox(self.br_exe, button("Chọn…", "folder", flat=True, on_click=self._br_pick)))
        br.add(f)
        br.add(flow(*[button(f"Đăng nhập {publish.PLATFORMS[k]}", "link", flat=True,
                             on_click=lambda _=False, k=k: self._br_login(k)) for k in ("youtube", "facebook", "tiktok")],
                    button("Hướng dẫn", "info", flat=True, on_click=lambda: self._help("Chrome (điền sẵn)", BR_HELP))))
        self.br_out = label("", "muted", wrap=True)
        br.add(self.br_out)
        self.cards = [z, y, fb, br]
        self.refresh()

    # (thuộc tính, khoá cài đặt, kiểu) cho SettingsPage.KEYS
    KEYS = [("zn_key", "zernio_api_key"), ("yt_cid", "yt_client_id"), ("yt_sec", "yt_client_secret"),
            ("fb_app", "fb_app_id"), ("fb_sec", "fb_app_secret"), ("br_exe", "browser_exe")]

    def _save(self) -> dict:
        save_settings(self.page.collect())
        return load_settings()

    def _help(self, title: str, html: str):
        m = QMessageBox(self.page)
        m.setWindowTitle(f"Hướng dẫn – {title}")
        m.setTextFormat(Qt.TextFormat.RichText)
        m.setText(html)
        m.exec()

    def refresh(self):
        """Danh sách kênh / Trang / tài khoản Zernio đã kết nối (mỗi mục có nút Bỏ)."""
        for box, kind, pre in ((self.yt_box, "youtube", "yt:"), (self.fb_box, "facebook", "fb:")):
            while box.count():
                it = box.takeAt(0)
                w = it.widget()
                if w:
                    w.hide()
                    w.setParent(None)
                    w.deleteLater()
            for a in publish.load_accounts(kind):
                name = a.get("name") or a["id"]
                lb = label(("📺 " if kind == "youtube" else "📄 ") + name + (f"  · {a['user']}" if a.get("user") else ""))
                box.addWidget(flow(lb, button("Bỏ", "x", "Bỏ tài khoản này khỏi Subvideo", flat=True,
                                              on_click=lambda _=False, t=pre + a["id"]: self._remove(t))))
        cur = self.br_prof.currentData()
        self.br_prof.clear()
        for b in browser_post.profiles():
            self.br_prof.addItem(b["name"], b["id"])
        set_combo(self.br_prof, cur)
        why = browser_post.problem(load_settings())
        self.br_out.setText("✗ " + why if why else
                            f"✓ Trình duyệt: {browser_post.find_browser(load_settings())} – bấm Đăng nhập 1 lần cho mỗi "
                            "trang, đăng nhập xong đóng cửa sổ Chrome đó.")
        zn = publish.load_accounts("zernio")
        self.zn_out.setText(("Đã kết nối: " + " · ".join(f"{publish.PLATFORMS[a['platform']]}: {a['name']}" for a in zn))
                            if zn else "Chưa có tài khoản – bấm Kết nối rồi Tải lại danh sách.")

    def _remove(self, tid: str):
        publish.remove_account(tid)
        self.refresh()

    # ---------------- Chrome (điền sẵn)
    def _br_add(self):
        name, ok = QInputDialog.getText(self.page, "Hồ sơ Chrome mới", "Tên hồ sơ (vd. Kênh phim 2):")
        if ok:
            b = browser_post.add_profile(name)
            self.refresh()
            set_combo(self.br_prof, b["id"])

    def _br_rename(self):
        pid = self.br_prof.currentData()
        name, ok = QInputDialog.getText(self.page, "Đổi tên hồ sơ", "Tên mới:", text=self.br_prof.currentText())
        if ok and pid:
            browser_post.rename_profile(pid, name)
            self.refresh()

    def _br_remove(self):
        pid = self.br_prof.currentData()
        if not pid or QMessageBox.question(self.page, "Xoá hồ sơ Chrome",
                                           f"Xoá hồ sơ '{self.br_prof.currentText()}'? Đăng nhập đã lưu trong hồ sơ này "
                                           "sẽ mất.") != QMessageBox.StandardButton.Yes:
            return
        browser_post.remove_profile(pid)
        self.refresh()

    def _br_pick(self):
        f, _ = QFileDialog.getOpenFileName(self.page, "Chọn chrome.exe / msedge.exe", "", "Trình duyệt (*.exe);;Tất cả (*)")
        if f:
            self.br_exe.setText(f)
            self._save()
            self.refresh()

    def _br_login(self, platform: str):
        s = self._save()
        pid = self.br_prof.currentData() or "main"
        name = self.br_prof.currentText()
        self.br_out.setText("Đang mở Chrome…")
        run_async(lambda: browser_post.open_login(s, pid, platform),
                  lambda _: self.br_out.setText(f"Đã mở Chrome (hồ sơ {name}) ở trang đăng nhập "
                                                f"{publish.PLATFORMS[platform]} – đăng nhập xong ĐÓNG cửa sổ đó rồi mới "
                                                "bấm Đăng ở tab Xuất."),
                  lambda m: self.br_out.setText("✗ " + m))

    # ---------------- Zernio
    def _zn_connect(self, platform: str):
        s = self._save()
        self.zn_out.setText("Đang mở trang kết nối của Zernio…")

        def ok(url):
            QDesktopServices.openUrl(QUrl(url))
            self.zn_out.setText(f"Đã mở trang kết nối {publish.PLATFORMS[platform]} trên trình duyệt – cho phép xong "
                                "quay lại bấm Tải lại danh sách.")
        run_async(lambda: publish.Zernio(s.get("zernio_api_key", "")).connect_url(s, platform), ok,
                  lambda m: self.zn_out.setText("✗ " + m))

    def _zn_refresh(self):
        s = self._save()
        self.zn_out.setText("Đang lấy danh sách tài khoản…")
        run_async(lambda: publish.Zernio(s.get("zernio_api_key", "")).accounts(), lambda _: self.refresh(),
                  lambda m: self.zn_out.setText("✗ " + m))

    # ---------------- YouTube
    def _yt_file(self):
        f, _ = QFileDialog.getOpenFileName(self.page, "File client_secret.json của Google Cloud", "", "JSON (*.json)")
        if not f:
            return
        try:
            cid, sec = publish.read_client_secret(f)
        except (OSError, ValueError) as e:
            return self.yt_out.setText("✗ " + str(e))
        self.yt_cid.setText(cid)
        self.yt_sec.setText(sec)
        self._save()
        self.yt_out.setText("✓ Đã nạp Client ID – bấm Đăng nhập Google.")

    def _yt_login(self):
        s = self._save()
        self.yt_out.setText("Đang mở trang đăng nhập Google trên trình duyệt… (chờ tối đa 5 phút)")
        run_async(lambda: publish.yt_login(s),
                  lambda a: (self.yt_out.setText(f"✓ Đã kết nối kênh {a['name']}"), self.refresh()),
                  lambda m: self.yt_out.setText("✗ " + m))

    # ---------------- Facebook
    def _fb_login(self):
        s = self._save()
        self.fb_out.setText("Đang mở trang đăng nhập Facebook trên trình duyệt… (chờ tối đa 5 phút)")
        run_async(lambda: publish.fb_login(s), self._fb_done, lambda m: self.fb_out.setText("✗ " + m))

    def _fb_paste(self):
        tok, ok = QInputDialog.getText(self.page, "Mã truy cập Facebook",
                                       "Dán mã truy cập (User access token) lấy ở Graph API Explorer:")
        if not ok or not tok.strip():
            return
        s = self._save()
        self.fb_out.setText("Đang đọc danh sách Trang…")
        run_async(lambda: publish.fb_use_token(s, tok), self._fb_done, lambda m: self.fb_out.setText("✗ " + m))

    def _fb_done(self, pages):
        self.fb_out.setText(f"✓ Đã kết nối {len(pages)} Trang: " + ", ".join(x["name"] for x in pages))
        self.refresh()


# ================================================================== tab Xuất → Đăng video
class PublishCard(SectionCard):
    """Mục "Đăng video": video (bản xuất mới nhất / chọn file), YouTube + Facebook (nơi đăng, kiểu), tiêu đề / mô tả /
    hashtag (AI viết), quyền riêng tư, danh mục, hẹn giờ, ảnh bìa, nhãn nội dung AI, nút Đăng, lịch sử đăng."""

    def __init__(self, page):
        super().__init__("Đăng video", "share", "YouTube, Facebook (Trang), TikTok. Thêm tài khoản ở Cài đặt → Đăng "
                                                "video.")
        self.page = page
        self.file: Path | None = None
        self.thumb = ""
        self._loading = False
        s = load_settings()
        f = Form()
        self.v_name = label("", "muted", wrap=True)
        f.row("Video", flow(self.v_name,
                            button("Bản xuất mới nhất", "refresh", flat=True, on_click=self._use_latest),
                            button("Chọn file…", "folder", flat=True, on_click=self._pick_file)))
        self.plat: dict[str, dict] = {}
        for plat in ("youtube", "facebook", "tiktok"):
            sw = Switch(plat == "youtube")
            tg = combo([])
            tg.setMinimumContentsLength(22)
            kd = combo(publish.KINDS[plat])
            f.row(publish.PLATFORMS[plat], flow(sw, tg, kd))
            self.plat[plat] = {"on": sw, "target": tg, "kind": kd}
            for w in (sw,):
                w.toggled.connect(self._changed)
            tg.currentIndexChanged.connect(self._changed)
            kd.currentIndexChanged.connect(self._changed)
        self.title = f.row("Tiêu đề", QLineEdit())
        self.title.setPlaceholderText("Tiêu đề video (YouTube tối đa 100 ký tự)")
        self.desc = QPlainTextEdit()
        self.desc.setPlaceholderText("Mô tả / nội dung bài đăng")
        self.desc.setMinimumHeight(96)
        f.row("Mô tả", self.desc)
        self.tags = f.row("Hashtag", QLineEdit())
        self.tags.setPlaceholderText("#phimhay #vietsub …")
        self.add(f)
        self.ai_btn = button("AI viết tiêu đề, mô tả, hashtag", "sparkle", flat=True, on_click=self._ai_write)
        self.ai_out = label("", "muted", wrap=True)
        self.add(flow(self.ai_btn, self.ai_out))
        g = Form()
        self.privacy = g.row("Quyền riêng tư (YouTube)", combo(publish.PRIVACY, s.get("publish_privacy", "public")))
        self.category = g.row("Danh mục (YouTube)", combo(publish.YT_CATEGORIES, s.get("publish_category", "24")))
        self.sched = Switch(False)
        self.when = QDateTimeEdit(QDateTime.currentDateTime().addSecs(3600))
        self.when.setDisplayFormat("dd/MM/yyyy HH:mm")
        self.when.setCalendarPopup(True)
        self.when.setEnabled(False)
        self.sched.toggled.connect(self.when.setEnabled)
        g.row("Hẹn giờ đăng", flow(self.sched, self.when))
        self.thumb_lb = label("(mặc định của nền tảng)", "muted")
        g.row("Ảnh bìa", flow(self.thumb_lb, button("Chọn ảnh…", "image", flat=True, on_click=self._pick_thumb),
                              button("Lấy khung đang xem", "film", "Chụp khung hình ở vị trí đang phát của video đăng",
                                     flat=True, on_click=self._grab_thumb),
                              button("Bỏ", "x", flat=True, on_click=lambda: self._set_thumb(""))))
        self.notify = g.row("Báo cho người đăng ký (YouTube)", Switch(bool(s.get("publish_notify", True))))
        self.synthetic = g.row("Có nội dung tạo bằng AI", Switch(bool(s.get("publish_synthetic", False))),
                               "Bật nếu giọng / hình do AI tạo có thể bị nhầm là thật – YouTube gắn nhãn nội dung AI")
        self.add(g)
        self.warn = label("", "muted", wrap=True)
        self.add(self.warn)
        go = GradientButton("Đăng", "share", big=True)
        go.clicked.connect(self._go)
        self.add(go)
        self.hist = QTableWidget(0, 5)
        self.hist.setHorizontalHeaderLabels(["Bài đăng", "Trạng thái", "", "", ""])
        self.hist.verticalHeader().hide()
        self.hist.verticalHeader().setDefaultSectionSize(40)
        self.hist.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for c, w in ((1, 130), (2, 80), (3, 116), (4, 84)):
            self.hist.setColumnWidth(c, w)
        self.hist.setMinimumHeight(130)
        self.hist.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.add(label("Đã đăng", "h3"))
        self.add(self.hist)
        for w in (self.title, self.tags):
            w.textChanged.connect(self._save_draft)
        self.desc.textChanged.connect(self._save_draft)

    # ---------------- nạp / lưu nháp
    def load(self):
        """Gọi khi mở dự án / mở tab Xuất."""
        p = self.page.p
        if not p:
            return
        self._loading = True
        try:
            d = publish.load_draft(p)
            for plat, w in self.plat.items():
                cur = d.get(f"{plat}_target") or (w["target"].currentData() or "")
                w["target"].clear()
                for t in publish.targets(plat):
                    w["target"].addItem(publish.target_label(t), t["id"])
                set_combo(w["target"], cur)
                set_combo(w["kind"], d.get(f"{plat}_kind", "video"))
                w["on"].setChecked(bool(d.get(f"{plat}_on", plat == "youtube")))
            self.title.setText(d.get("title", ""))
            self.desc.setPlainText(d.get("description", ""))
            self.tags.setText(d.get("hashtags", ""))
            f = d.get("file")
            self.file = Path(f) if f and Path(f).exists() else publish.latest_video(p)
            self._set_thumb(d.get("thumbnail", "") if Path(d.get("thumbnail") or "~nothing").exists() else "")
        finally:
            self._loading = False
        self._show_file()
        self.fill_history()

    def _draft(self) -> dict:
        d = {"title": self.title.text(), "description": self.desc.toPlainText(), "hashtags": self.tags.text(),
             "file": str(self.file) if self.file else "", "thumbnail": self.thumb}
        for plat, w in self.plat.items():
            d.update({f"{plat}_on": w["on"].isChecked(), f"{plat}_target": w["target"].currentData() or "",
                      f"{plat}_kind": w["kind"].currentData()})
        return d

    def _save_draft(self, *_):
        if not self._loading and self.page.p:
            publish.save_draft(self.page.p, self._draft())

    def _changed(self, *_):
        self._save_draft()
        self._check()

    # ---------------- video / ảnh bìa
    def _show_file(self):
        self.v_name.setText(self.file.name if self.file else "Chưa có video xuất – bấm Xuất video ở trên trước")
        if self.file:
            self.v_name.setToolTip(str(self.file))
        self._check()

    def _use_latest(self):
        self.file = publish.latest_video(self.page.p) if self.page.p else None
        self._show_file()
        self._save_draft()

    def _pick_file(self):
        start = str(self.page.p.output_dir) if self.page.p else ""
        f, _ = QFileDialog.getOpenFileName(self, "Chọn video để đăng", start, "Video (*.mp4 *.mov *.mkv *.webm)")
        if f:
            self.file = Path(f)
            self._show_file()
            self._save_draft()

    def _set_thumb(self, path: str):
        self.thumb = path
        self.thumb_lb.setText(Path(path).name if path else "(mặc định của nền tảng)")
        self._save_draft()

    def _pick_thumb(self):
        f, _ = QFileDialog.getOpenFileName(self, "Ảnh bìa (JPG / PNG, ≤ 2 MB)", "", "Ảnh (*.jpg *.jpeg *.png)")
        if f:
            self._set_thumb(f)

    def _grab_thumb(self):
        if not self.file or not self.page.p:
            return self.page.win.toast("Chưa có video để chụp", "warn")
        from .. import ffmpeg as ff

        out = self.page.p.dir / "work" / f"thumb_{int(time.time())}.jpg"
        t = self.page.player.position()
        run_async(lambda: ff.snapshot(self.file, t, out, 1280), lambda pth: self._set_thumb(str(pth)),
                  lambda m: self.page.win.toast("Không chụp được: " + m, "err"))

    def _check(self):
        """Cảnh báo theo nền tảng (Reels 3–90 s dọc, Shorts ≤ 3 phút; YouTube API riêng bị khoá Riêng tư)."""
        if self._loading:
            return
        msgs = []
        for plat, w in self.plat.items():
            if not w["on"].isChecked():
                continue
            tid = w["target"].currentData() or ""
            if tid.startswith("yt:"):
                msgs.append("YouTube qua API riêng: dự án Google chưa được duyệt thì video bị khoá Riêng tư.")
            if tid.startswith("br:"):
                why = browser_post.problem(load_settings())
                if why:
                    msgs.append(why)
            if self.file:
                err, warn = publish.check_video(self.file, plat, w["kind"].currentData())
                msgs += [x for x in (err, warn) if x]
        self.warn.setText("⚠ " + "  ".join(msgs) if msgs else "")

    # ---------------- AI viết nội dung
    def _ai_write(self):
        p = self.page.p
        if not p:
            return
        plat = next((k for k, w in self.plat.items() if w["on"].isChecked()), "youtube")
        kind = self.plat[plat]["kind"].currentData()
        s = load_settings()
        self.ai_btn.setEnabled(False)
        self.ai_out.setText("AI đang đọc phụ đề…")

        def ok(d):
            self.ai_btn.setEnabled(True)
            self.title.setText(d["title"])
            self.desc.setPlainText(d["description"])
            self.tags.setText(" ".join("#" + t for t in d["hashtags"]))
            self.ai_out.setText(f"✓ Viết cho {publish.PLATFORMS[plat]} – sửa lại nếu cần")

        def err(m):
            self.ai_btn.setEnabled(True)
            self.ai_out.setText("✗ " + m)
        run_async(lambda: publish.ai_metadata(p, s, plat, kind), ok, err)

    # ---------------- đăng
    def _go(self):
        p = self.page.p
        if not p:
            return
        if not self.file or not self.file.exists():
            return self.page.win.toast("Chưa có video – bấm Xuất video trước hoặc Chọn file…", "warn")
        on = [k for k, w in self.plat.items() if w["on"].isChecked()]
        if not on:
            return self.page.win.toast("Bật YouTube, Facebook hoặc TikTok để đăng", "warn")
        if not self.title.text().strip() and not self.desc.toPlainText().strip():
            return self.page.win.toast("Nhập tiêu đề / mô tả (hoặc bấm AI viết)", "warn")
        s = load_settings()
        save_settings({"publish_privacy": self.privacy.currentData(), "publish_category": self.category.currentData(),
                       "publish_notify": self.notify.isChecked(), "publish_synthetic": self.synthetic.isChecked()})
        when = self.when.dateTime().toPython().isoformat() if self.sched.isChecked() else ""
        if when and not publish.parse_when(when):
            return self.page.win.toast("Giờ hẹn phải sau bây giờ ít nhất 1 phút", "warn")
        base = {"file": str(self.file), "title": self.title.text().strip(), "description": self.desc.toPlainText(),
                "hashtags": self.tags.text(), "privacy": self.privacy.currentData(),
                "category": self.category.currentData(), "publish_at": when, "thumbnail": self.thumb,
                "notify": self.notify.isChecked(), "synthetic": self.synthetic.isChecked(),
                "lang": publish_lang(p, s)}
        self._save_draft()
        for plat in on:
            w = self.plat[plat]
            tid = w["target"].currentData() or f"manual:{plat}"
            target = next((t for t in publish.targets(plat) if t["id"] == tid), None)
            if not target:
                self.page.win.toast(f"{publish.PLATFORMS[plat]}: tài khoản không còn – chọn lại nơi đăng", "warn")
                continue
            item = dict(base, platform=plat, kind=w["kind"].currentData(), target=target)
            err, _ = publish.check_video(self.file, plat, item["kind"])
            if err:
                self.page.win.toast(f"{publish.PLATFORMS[plat]}: {err}", "err")
                continue
            if target["via"] == "manual":
                self._manual(item)
                continue

            def fn(j, pr, item=item):
                rec = publish.publish(pr, load_settings(), item, j.update)
                name = publish.PLATFORMS[item["platform"]]
                msg = f"{publish.STATUS_NAMES.get(rec['status'], rec['status'])} lên {name}"
                if rec.get("url"):
                    msg += f": {rec['url']}"
                if rec.get("warn"):
                    msg += f" · ⚠ {rec['warn']}"
                return {"message": msg}
            self.page.submit("publish", f"Đăng {publish.PLATFORMS[plat]}", fn)

    def _manual(self, item: dict):
        """Tự tải lên: chép nội dung vào bộ nhớ tạm, ghi file .txt cạnh video, mở trang tải lên + thư mục video."""
        text, txt, url = publish.prepare_manual(self.file, item, item["platform"], item["kind"])
        QGuiApplication.clipboard().setText(text)
        QDesktopServices.openUrl(QUrl(url))
        _reveal(self.file)
        publish.save_post(self.page.p, {"id": f"m{int(time.time() * 1000)}", "time": time.time(),
                                        "platform": item["platform"], "kind": item["kind"], "via": "manual",
                                        "account": "Tự tải lên", "title": item["title"], "file": str(self.file),
                                        "url": "", "status": "prepared", "warn": ""})
        self.fill_history()
        self.page.win.toast(f"Đã chép nội dung + mở trang đăng {publish.PLATFORMS[item['platform']]} – kéo file "
                            f"{self.file.name} vào trang đó, dán nội dung (Ctrl+V). Nội dung cũng lưu ở {txt.name}",
                            "ok")

    # ---------------- lịch sử
    def fill_history(self):
        p = self.page.p
        posts = list(reversed(publish.load_posts(p))) if p else []
        sig = (p.id if p else "", json.dumps(posts, sort_keys=True, ensure_ascii=False))
        if sig == getattr(self, "_hist_sig", None):     # không đổi -> không dựng lại các nút trong bảng
            return
        self._hist_sig = sig
        self.hist.setRowCount(0)
        self.hist.setRowCount(len(posts))
        for r, rec in enumerate(posts):
            when = time.strftime("%d/%m %H:%M", time.localtime(rec.get("time", 0)))
            kind = dict(publish.KINDS.get(rec.get("platform"), [])).get(rec.get("kind"), "")
            it = QTableWidgetItem(icons.icon("share", theme.ACCENT2),
                                  f"{when} · {publish.PLATFORMS.get(rec.get('platform'), '')} {kind} · "
                                  f"{rec.get('account', '')} · {rec.get('title', '')}")
            tip = rec.get("url") or rec.get("file", "")
            if rec.get("warn"):
                tip += "\n⚠ " + rec["warn"]
            it.setToolTip(tip)
            self.hist.setItem(r, 0, it)
            st = QTableWidgetItem(publish.STATUS_NAMES.get(rec.get("status"), rec.get("status", "")))
            st.setToolTip(rec.get("warn", ""))
            self.hist.setItem(r, 1, st)
            url = rec.get("url", "")
            if url:
                self.hist.setCellWidget(r, 2, button("Mở", "play", flat=True,
                                                     on_click=lambda _=False, u=url: QDesktopServices.openUrl(QUrl(u))))
            else:
                self.hist.removeCellWidget(r, 2)
            if rec.get("via") == "zernio" and rec.get("status") == "processing":
                self.hist.setCellWidget(r, 3, button("Cập nhật", "refresh", flat=True,
                                                     on_click=lambda _=False, x=rec: self._refresh_rec(x)))
            else:
                self.hist.removeCellWidget(r, 3)
            if url:
                self.hist.setCellWidget(r, 4, button("Chép", "link", "Chép link bài đăng", flat=True,
                                                     on_click=lambda _=False, u=url: QGuiApplication.clipboard().setText(u)))
            else:
                self.hist.removeCellWidget(r, 4)

    def _refresh_rec(self, rec: dict):
        p = self.page.p
        s = load_settings()

        def ok(r):
            publish.save_post(p, r)
            self.fill_history()
        run_async(lambda: publish.refresh_status(s, dict(rec)), ok, lambda m: self.page.win.toast(m, "err"))


def publish_lang(p, settings: dict) -> str:
    """Mã tiếng của video đăng (tiếng đích của dự án) cho YouTube defaultLanguage."""
    from .. import pipeline

    try:
        return pipeline.target_lang(p, settings)
    except Exception:  # noqa: BLE001
        return ""
