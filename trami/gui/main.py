"""Cửa sổ chính TraMi (PySide6)."""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

from PySide6.QtCore import QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPainterPath, QPixmap
from PySide6.QtWidgets import (QApplication, QButtonGroup, QFileDialog, QFrame, QGridLayout, QHBoxLayout,
                               QLabel, QLineEdit, QMainWindow, QMessageBox, QPlainTextEdit,
                               QStackedWidget, QVBoxLayout, QWidget)

from .. import __version__, pipeline
from ..config import load_settings, public_settings, save_settings
from ..jobs import manager
from ..project import Project
from . import common as C
from . import icons, theme
from .common import run_async
from .project_page import ProjectPage, scroll
from .widgets import (DropZone, Form, GradientButton, IconBadge, IconButton, NavButton, ProgressBar, SectionCard,
                      Switch, Toast, button, combo, hbox, label, set_combo, shadow)

VIDEO_FILTER = "Video (*.mp4 *.mkv *.mov *.avi *.flv *.webm *.ts *.m4v *.wmv *.mpg *.3gp);;Tất cả (*.*)"


def _clear_layout(lay):
    """Gỡ & ẩn ngay các widget cũ (deleteLater chỉ xoá khi về vòng lặp sự kiện)."""
    while lay.count():
        it = lay.takeAt(0)
        w = it.widget()
        if w:
            w.hide()
            w.setParent(None)
            w.deleteLater()


# ====================================================================== Thanh bên

class Sidebar(QFrame):
    def __init__(self, win):
        super().__init__()
        self.win = win
        self.setFixedWidth(250)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 18, 14, 14)
        lay.setSpacing(4)
        brand = QHBoxLayout()
        logo = QLabel()
        pm = icons.app_logo(88)
        pm.setDevicePixelRatio(2)
        logo.setPixmap(pm)
        logo.setFixedSize(46, 46)
        tb = QVBoxLayout()
        tb.setSpacing(0)
        t = QLabel("TraMi")
        t.setFont(theme.font(15, QFont.Weight.ExtraBold))
        st = label("Dịch video Trung → Việt", "muted")
        st.setFont(theme.font(8.5))
        tb.addWidget(t)
        tb.addWidget(st)
        brand.addWidget(logo)
        brand.addLayout(tb, 1)
        lay.addLayout(brand)
        lay.addSpacing(16)
        new = GradientButton("Dự án mới", "plus")
        new.clicked.connect(win.go_home)
        lay.addWidget(new)
        self.new_small = IconButton("plus", "Dự án mới", 40)
        self.new_small.clicked.connect(win.go_home)
        self.new_small.hide()
        lay.addWidget(self.new_small, 0, Qt.AlignmentFlag.AlignHCenter)
        self._full = [new, tb.itemAt(0).widget(), tb.itemAt(1).widget()]
        lay.addSpacing(12)
        self.group = QButtonGroup(self)
        self.nav: dict[str, NavButton] = {}
        for key, text, ic in (("home", "Trang chủ", "home"), ("project", "Dự án đang mở", "film"),
                              ("batch", "Xử lý hàng loạt", "stack"), ("concat", "Ghép nhiều video", "merge"),
                              ("settings", "Cài đặt", "settings")):
            b = NavButton(text, ic)
            b.clicked.connect(lambda _=False, k=key: win.show_page(k))
            self.group.addButton(b)
            self.nav[key] = b
            lay.addWidget(b)
        # thu gọn (chỉ icon): danh sách dự án gần đây ẩn đi -> khoảng trống dồn xuống dưới, các icon xếp sát nhau đều
        self._filler = QWidget()
        self._filler.hide()
        lay.addWidget(self._filler, 1)
        lay.addSpacing(14)
        recent_lb = label("DỰ ÁN GẦN ĐÂY", "muted")
        lay.addWidget(recent_lb)
        self.recent = QVBoxLayout()
        self.recent.setSpacing(2)
        rw = QWidget()
        rw.setLayout(self.recent)
        sa = scroll(rw)
        lay.addWidget(sa, 1)
        self.sys = label("", "muted", wrap=True)
        self.sys.setFont(theme.font(8.5))
        lay.addWidget(self.sys)
        self._full += [recent_lb, sa, self.sys]
        self._lay = lay
        self.compact = False

    def set_compact(self, on: bool):
        """Thu gọn còn cột icon (khi đang mở dự án) để trang dự án rộng như trình dựng phim."""
        if on == self.compact:
            return
        self.compact = on
        self.setFixedWidth(68 if on else 250)
        self._lay.setContentsMargins(10 if on else 14, 18, 10 if on else 14, 14)
        for w in self._full:
            w.setVisible(not on)
        self.new_small.setVisible(on)
        self._filler.setVisible(on)
        self._lay.setSpacing(8 if on else 4)
        for b in self.nav.values():
            b.compact = on
            b.setToolTip(b.text() if on else "")
            b.update()
        self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(theme.SIDEBAR))
        p.setPen(QColor(theme.BORDER))
        p.drawLine(self.width() - 1, 0, self.width() - 1, self.height())

    def set_projects(self, items: list[dict]):
        _clear_layout(self.recent)
        for pr in items[:30]:
            self.recent.addWidget(RecentItem(pr, self.win))
        self.recent.addStretch(1)


class RecentItem(QWidget):
    def __init__(self, pr: dict, win):
        super().__init__()
        self.pr, self.win = pr, win
        self.setFixedHeight(52)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._hov = False
        th = Path(Project(pr["id"]).dir / "thumb.jpg")
        self.thumb = QPixmap(str(th)) if th.exists() else None

    def enterEvent(self, e):
        self._hov = True
        self.update()

    def leaveEvent(self, e):
        self._hov = False
        self.update()

    def mouseReleaseEvent(self, e):
        self.win.open_project(self.pr["id"])

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        r = QRectF(self.rect()).adjusted(1, 2, -1, -2)
        cur = self.win.project_page.p is not None and self.win.project_page.p.id == self.pr["id"]
        if self._hov or cur:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(theme.CARD3 if cur else theme.CARD2))
            p.drawRoundedRect(r, 9, 9)
        tr = QRectF(r.left() + 6, r.top() + 6, 60, r.height() - 12)
        path = QPainterPath()
        path.addRoundedRect(tr, 6, 6)
        p.save()
        p.setClipPath(path)
        if self.thumb and not self.thumb.isNull():
            sc = max(tr.width() / self.thumb.width(), tr.height() / self.thumb.height())
            w, h = self.thumb.width() * sc, self.thumb.height() * sc
            p.drawPixmap(QRectF(tr.center().x() - w / 2, tr.center().y() - h / 2, w, h).toRect(), self.thumb)
        else:
            p.fillRect(tr, QColor(theme.CARD3))
            icons.paint_icon(p, "film", tr.adjusted(18, 6, -18, -6), theme.MUTED)
        p.restore()
        p.setPen(QColor(theme.TEXT))
        p.setFont(theme.font(9.3, QFont.Weight.DemiBold))
        fm = p.fontMetrics()
        name = fm.elidedText(self.pr.get("name") or "", Qt.TextElideMode.ElideRight, int(r.width() - 80))
        p.drawText(QRectF(tr.right() + 9, r.top() + 6, r.width() - 80, 20), Qt.AlignmentFlag.AlignLeft, name)
        p.setPen(QColor(theme.MUTED))
        p.setFont(theme.font(8.3))
        d = (self.pr.get("info") or {}).get("duration", 0)
        from .player import fmt

        p.drawText(QRectF(tr.right() + 9, r.top() + 25, r.width() - 80, 18), Qt.AlignmentFlag.AlignLeft,
                   f"{fmt(d, False) if d else '--:--'} · {self.pr.get('n_translated', 0)}/{self.pr.get('n_segments', 0)} câu")


# ====================================================================== Trang chủ

class ProjectCard(QWidget):
    def __init__(self, pr: dict, win):
        super().__init__()
        self.pr, self.win = pr, win
        self.setFixedSize(236, 190)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        th = Project(pr["id"]).dir / "thumb.jpg"
        self.thumb = QPixmap(str(th)) if th.exists() else None
        self._hov = False
        shadow(self, 22, 90, 5)

    def enterEvent(self, e):
        self._hov = True
        self.update()

    def leaveEvent(self, e):
        self._hov = False
        self.update()

    def mouseReleaseEvent(self, e):
        self.win.open_project(self.pr["id"])

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        r = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        p.setPen(QColor(theme.ACCENT2 if self._hov else theme.BORDER))
        p.setBrush(QColor(theme.CARD))
        p.drawRoundedRect(r, 14, 14)
        tr = QRectF(r.left() + 8, r.top() + 8, r.width() - 16, 120)
        path = QPainterPath()
        path.addRoundedRect(tr, 10, 10)
        p.save()
        p.setClipPath(path)
        if self.thumb and not self.thumb.isNull():
            sc = max(tr.width() / self.thumb.width(), tr.height() / self.thumb.height())
            w, h = self.thumb.width() * sc, self.thumb.height() * sc
            p.drawPixmap(QRectF(tr.center().x() - w / 2, tr.center().y() - h / 2, w, h).toRect(), self.thumb)
        else:
            p.fillRect(tr, QColor(theme.CARD3))
            icons.paint_icon(p, "film", QRectF(tr.center().x() - 20, tr.center().y() - 20, 40, 40), theme.MUTED)
        p.restore()
        n, t = self.pr.get("n_segments", 0), self.pr.get("n_translated", 0)
        if n:  # nhãn tiến độ dịch
            badge = QRectF(tr.right() - 74, tr.top() + 8, 66, 22)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(0, 0, 0, 160))
            p.drawRoundedRect(badge, 11, 11)
            p.setPen(QColor(theme.OK if t >= n else theme.WARN))
            p.setFont(theme.font(8.5, QFont.Weight.Bold))
            p.drawText(badge, Qt.AlignmentFlag.AlignCenter, f"{t}/{n} câu")
        p.setPen(QColor(theme.TEXT))
        p.setFont(theme.font(10, QFont.Weight.Bold))
        fm = p.fontMetrics()
        p.drawText(QRectF(r.left() + 12, tr.bottom() + 8, r.width() - 24, 22), Qt.AlignmentFlag.AlignLeft,
                   fm.elidedText(self.pr.get("name") or "", Qt.TextElideMode.ElideRight, int(r.width() - 24)))
        p.setPen(QColor(theme.MUTED))
        p.setFont(theme.font(8.5))
        i = self.pr.get("info") or {}
        from .player import fmt

        p.drawText(QRectF(r.left() + 12, tr.bottom() + 30, r.width() - 24, 20), Qt.AlignmentFlag.AlignLeft,
                   f"{i.get('width', 0)}×{i.get('height', 0)} · {fmt(i.get('duration', 0), False)}" if i else "")


class HomePage(QWidget):
    def __init__(self, win):
        super().__init__()
        self.win = win
        inner = QWidget()
        v = QVBoxLayout(inner)
        v.setContentsMargins(28, 24, 28, 24)
        v.setSpacing(18)
        v.addWidget(label("Tạo dự án dịch video mới", "h1"))
        v.addWidget(label("Hỗ trợ video dài nhiều giờ. Chọn file, kéo thả hoặc dán link Douyin / Bilibili / YouTube / TikTok…", "muted"))
        grid = QHBoxLayout()
        grid.setSpacing(16)
        c1 = SectionCard("Chọn video trên máy", "folder", "Đọc trực tiếp, không sao chép – nhanh nhất cho video dài.")
        dz = DropZone("Kéo thả video vào đây", "hoặc bấm để chọn file (chọn nhiều file = xử lý hàng loạt)")
        dz.clicked.connect(self.pick)
        dz.dropped.connect(self.open_paths)
        c1.add(dz)
        c2 = SectionCard("Tải video từ link", "link", "Douyin, Bilibili, YouTube, TikTok, Weibo, Xigua, Kuaishou…")
        self.url = QLineEdit()
        self.url.setPlaceholderText("https://www.bilibili.com/video/BV…")
        self.url.returnPressed.connect(self.download)
        c2.add(self.url)
        dl = GradientButton("Tải về & tạo dự án", "download")
        dl.clicked.connect(self.download)
        c2.add(dl)
        c2.add(label("Mẹo: vào tab Cài đặt để chọn công cụ dịch AI cho bản dịch chuẩn nhất.", "muted", wrap=True))
        c2.lay.addStretch(1)
        grid.addWidget(c1, 3)
        grid.addWidget(c2, 2)
        v.addLayout(grid)

        flow = SectionCard("Quy trình", "list")
        steps = QHBoxLayout()
        for n, (ic, t, d) in enumerate((("subtitles", "Lấy phụ đề Trung", "Nhận dạng giọng nói hoặc OCR chữ có sẵn"),
                                        ("globe", "Dịch sang Việt", "AI dịch theo ngữ cảnh, tên riêng Hán Việt"),
                                        ("eraser", "Xoá phụ đề cũ", "AI LaMa tái tạo nền sau chữ"),
                                        ("mic", "Lồng tiếng", "Nhiều giọng, nhái giọng nhân vật"),
                                        ("film", "Xuất video", "Đăng Facebook / YouTube / TikTok")), 1):
            box = QVBoxLayout()
            box.addWidget(IconBadge(ic, 40))
            box.addWidget(label(f"{n}. {t}", "h3"))
            box.addWidget(label(d, "muted", wrap=True))
            box.addStretch(1)
            steps.addLayout(box, 1)
        flow.add(steps)
        v.addWidget(flow)
        v.addWidget(label("Dự án gần đây", "h2"))
        self.grid = QGridLayout()
        self.grid.setSpacing(16)
        self.grid.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        v.addLayout(self.grid)
        v.addStretch(1)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(scroll(inner))

    def set_projects(self, items):
        _clear_layout(self.grid)
        cols = max(1, (self.width() - 60) // 252)
        for k, pr in enumerate(items[:24]):
            self.grid.addWidget(ProjectCard(pr, self.win), k // cols, k % cols)
        if not items:
            self.grid.addWidget(label("Chưa có dự án nào – hãy chọn video ở trên.", "muted"), 0, 0)

    def pick(self):
        paths, _ = QFileDialog.getOpenFileNames(self, "Chọn video", "", VIDEO_FILTER)
        if paths:
            self.open_paths(paths)

    def open_paths(self, paths: list[str]):
        if len(paths) > 1:
            self.win.batch_page.items.setPlainText("\n".join(paths))
            self.win.show_page("batch")
            self.win.toast(f"Đã đưa {len(paths)} video vào xử lý hàng loạt", "ok")
            return
        self.win.create_from_path(paths[0])

    def download(self):
        url = self.url.text().strip()
        if not url.startswith(("http://", "https://")):
            return self.win.toast("Link không hợp lệ", "err")
        p = Project.create("Video từ link")
        pid = p.id

        def fn(j):
            pr = Project(pid)
            path = pipeline.download_video(url, pr.dir / "source", j.update, j.cancelled)
            pipeline.attach_source(pr, path)
            pr.data["name"] = path.stem[:80]
            pr.save()
            return {"message": "Đã tải xong video"}
        manager.submit("download", f"Tải video: {url[:50]}", fn, pid)
        self.url.clear()
        self.win.refresh_projects()
        self.win.open_project(pid)
        self.win.toast("Đang tải video – xem tiến độ bên dưới", "ok")


# ====================================================================== Hàng loạt & ghép

class BatchPage(QWidget):
    def __init__(self, win):
        super().__init__()
        self.win = win
        s = load_settings()
        inner = QWidget()
        v = QVBoxLayout(inner)
        v.setContentsMargins(28, 24, 28, 24)
        v.setSpacing(16)
        v.addWidget(label("Xử lý hàng loạt", "h1"))
        v.addWidget(label("Mỗi dòng 1 đường dẫn file hoặc 1 link. Tool tự tạo dự án và dịch lần lượt từng video.", "muted"))
        c = SectionCard("Danh sách video", "stack")
        self.items = QPlainTextEdit()
        self.items.setPlaceholderText("D:\\Phim\\tap1.mp4\nD:\\Phim\\tap2.mp4\nhttps://www.douyin.com/video/…")
        self.items.setMinimumHeight(160)
        c.add(hbox(button("Chọn nhiều file…", "folder", on_click=self.pick), None))
        c.add(self.items)
        o = SectionCard("Tuỳ chọn", "settings")
        f = Form()
        self.method = f.row("Nguồn phụ đề", combo(C.METHODS, "asr"))
        self.asr = f.row("Bộ nhận dạng", combo(C.ASR_ENGINES, s.get("asr_engine")))
        self.engine = f.row("Công cụ dịch", combo(C.ENGINES, s.get("translate_engine")))
        self.target = f.row("Dịch sang", combo(C.TARGETS, s.get("target_lang", "vi")))
        self.style = f.row("Văn phong", combo(C.STYLES, s.get("translate_style")))
        self.hardsub = f.row("Phụ đề cũ", combo(C.HARDSUB, "none"))
        self.which = f.row("Ghép phụ đề", combo(C.WHICH, "trans"))
        self.aspect = f.row("Khung hình", combo(C.ASPECTS, "original"))
        self.dub = f.row("Lồng tiếng", Switch(False))
        self.tts = f.row("Bộ đọc", combo(C.TTS_ENGINES, s.get("tts_engine", "edge")))
        self.target.currentIndexChanged.connect(
            lambda: C.relabel(self.which, C.which_choices(self.target.currentData())))
        C.relabel(self.which, C.which_choices(self.target.currentData()))
        o.add(f)
        go = GradientButton("Bắt đầu xử lý hàng loạt", "bolt", big=True)
        go.clicked.connect(self.run)
        v.addWidget(c)
        v.addWidget(o)
        v.addWidget(go)
        v.addStretch(1)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(scroll(inner))

    def pick(self):
        paths, _ = QFileDialog.getOpenFileNames(self, "Chọn video", "", VIDEO_FILTER)
        if paths:
            cur = self.items.toPlainText().strip()
            self.items.setPlainText((cur + "\n" if cur else "") + "\n".join(paths))

    def run(self):
        items = [x.strip().strip('"') for x in self.items.toPlainText().splitlines() if x.strip()]
        if not items:
            return self.win.toast("Chưa có video nào", "err")
        which = self.which.currentData()
        opts = {"method": self.method.currentData(), "asr_engine": self.asr.currentData(),
                "engine": self.engine.currentData(), "style": self.style.currentData(),
                "target": self.target.currentData(),
                "hardsub_mode": self.hardsub.currentData(), "burn": which != "none",
                "which": which if which != "none" else "trans", "dub": self.dub.isChecked(),
                "tts_engine": self.tts.currentData(), "separate": True}
        aspect = self.aspect.currentData()
        n = 0
        for it in items:
            is_url = it.startswith(("http://", "https://"))
            if not is_url and not Path(it).exists():
                continue
            p = Project.create(Path(it).stem if not is_url else "Video từ link")
            if not is_url:
                try:
                    pipeline.attach_source(p, Path(it))
                except Exception:
                    p.delete()
                    continue
            if aspect != "original":
                e = p.edit
                e["aspect"] = aspect
                p.data["edit"] = e
                p.save()

            def fn(j, pid=p.id, url=it if is_url else ""):
                o = dict(opts)
                if url:
                    o["url"] = url
                return pipeline.auto(Project(pid), o, j.update, j.cancelled)
            manager.submit("auto", f"Hàng loạt: {Path(it).name if not is_url else it[-40:]}", fn, p.id)
            n += 1
        self.win.refresh_projects()
        self.win.toast(f"Đã xếp hàng {n} video", "ok")


class ConcatPage(QWidget):
    def __init__(self, win):
        super().__init__()
        self.win = win
        inner = QWidget()
        v = QVBoxLayout(inner)
        v.setContentsMargins(28, 24, 28, 24)
        v.setSpacing(16)
        v.addWidget(label("Ghép nhiều video", "h1"))
        v.addWidget(label("Các video được đưa về cùng kích thước video đầu tiên, rồi tạo dự án mới để dịch/chỉnh sửa tiếp.", "muted"))
        c = SectionCard("Danh sách theo thứ tự", "merge")
        self.items = QPlainTextEdit()
        self.items.setMinimumHeight(200)
        c.add(hbox(button("Chọn video…", "folder", on_click=self.pick), None))
        c.add(self.items)
        go = GradientButton("Ghép video", "merge", big=True)
        go.clicked.connect(self.run)
        v.addWidget(c)
        v.addWidget(go)
        v.addStretch(1)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(scroll(inner))

    def pick(self):
        paths, _ = QFileDialog.getOpenFileNames(self, "Chọn video", "", VIDEO_FILTER)
        if paths:
            cur = self.items.toPlainText().strip()
            self.items.setPlainText((cur + "\n" if cur else "") + "\n".join(paths))

    def run(self):
        from .. import video

        files = [x.strip().strip('"') for x in self.items.toPlainText().splitlines() if x.strip() and Path(x.strip().strip('"')).exists()]
        if len(files) < 2:
            return self.win.toast("Cần ít nhất 2 video", "err")
        p = Project.create("Video ghép")
        pid = p.id

        def fn(j):
            pr = Project(pid)
            out = pr.dir / "source" / "ghep.mp4"
            out.parent.mkdir(parents=True, exist_ok=True)
            video.concat_videos(files, out, load_settings(), progress=j.update, cancel=j.cancelled)
            pipeline.attach_source(pr, out)
            return {"message": "Đã ghép video"}
        manager.submit("concat", f"Ghép {len(files)} video", fn, pid)
        self.win.refresh_projects()
        self.win.open_project(pid)


# ====================================================================== Cài đặt

class SettingsPage(QWidget):
    KEYS = [  # (widget attr, setting key, kiểu)
        ("asr_engine", "asr_engine", "c"), ("qwen_model", "qwen_asr_model", "c"), ("asr_model", "asr_model", "c"),
        ("ocr_fps", "ocr_fps", "c"),
        ("asr_device", "asr_device", "c"),
        ("asr_max", "asr_max_chars", "i"), ("asr_beam", "asr_beam_size", "i"), ("asr_vad", "asr_vad", "b"),
        ("sep_model", "sep_model", "c"), ("sep_before", "sep_before_asr", "b"), ("diarize", "diarize", "b"),
        ("spk_n", "diarize_speakers", "i"), ("spk_thr", "diarize_threshold", "f"),
        ("engine", "translate_engine", "c"), ("style", "translate_style", "c"), ("batch", "translate_batch", "i"),
        ("target", "target_lang", "c"),
        ("ds_key", "deepseek_api_key", "t"), ("ds_model", "deepseek_model", "t"),
        ("oa_key", "openai_api_key", "t"), ("oa_base", "openai_base_url", "t"), ("oa_model", "openai_model", "t"),
        ("an_key", "anthropic_api_key", "t"), ("an_model", "anthropic_model", "t"),
        ("ge_key", "gemini_api_key", "t"), ("ge_model", "gemini_model", "t"),
        ("ol_base", "ollama_base_url", "t"), ("ol_model", "ollama_model", "t"),
        ("glossary", "glossary", "p"), ("tts_engine", "tts_engine", "c"), ("vn_mode", "vieneu_mode", "c"),
        ("vn_style", "vieneu_style", "c"), ("level_match", "dub_level_match", "b"),
        ("omni_model", "omnivoice_model", "c"), ("hf_token", "hf_token", "t"),
        ("align_words", "align_words", "b"), ("split_spk", "split_speaker_changes", "b"),
        ("dub_fit", "dub_fit", "b"), ("dub_short", "dub_auto_shorten", "b"),
        ("maxspeed", "tts_max_speedup", "f"), ("inpaint", "inpaint_backend", "c"),
        ("codec", "video_codec", "c"), ("crf", "video_crf", "i"), ("preset", "video_preset", "c"),
    ]

    def __init__(self, win):
        super().__init__()
        self.win = win
        from PySide6.QtWidgets import QDoubleSpinBox, QSpinBox

        from ..separate import SEP_MODELS

        def spin(lo, hi):
            s = QSpinBox()
            s.setRange(lo, hi)
            return s

        def dspin(lo, hi, step):
            s = QDoubleSpinBox()
            s.setRange(lo, hi)
            s.setSingleStep(step)
            return s

        def key():
            e = QLineEdit()
            e.setEchoMode(QLineEdit.EchoMode.Password)
            e.setPlaceholderText("Dán API key vào đây")
            return e

        inner = QWidget()
        v = QVBoxLayout(inner)
        v.setContentsMargins(28, 24, 28, 24)
        v.setSpacing(16)
        v.addWidget(label("Cài đặt", "h1"))
        self.status = label("", "muted", wrap=True)
        v.addWidget(self.status)
        grid = QGridLayout()
        grid.setSpacing(16)

        a = SectionCard("Nhận dạng giọng nói", "mic")
        f = Form()
        self.asr_engine = f.row("Bộ nhận dạng mặc định", combo(C.ASR_ENGINES))
        from ..asr_qwen import MODELS as QWEN_MODELS

        self.qwen_model = f.row("Mô hình Qwen3-ASR", combo(QWEN_MODELS),
                                "Bản 1.7B nghe chuẩn nhất; GPU dưới 6 GB hoặc chỉ có CPU thì chọn 0.6B")
        self.asr_model = f.row("Mô hình Whisper", combo([("large-v3", "large-v3 – chính xác nhất"),
                                                         ("large-v3-turbo", "large-v3-turbo – nhanh"),
                                                         ("medium", "medium"), ("small", "small – máy yếu")]))
        self.asr_device = f.row("Thiết bị", combo([("auto", "Tự động (ưu tiên GPU)"), ("cuda", "GPU NVIDIA"), ("cpu", "CPU")]))
        self.ocr_fps = f.row("Tốc độ quét phụ đề cứng (OCR)", combo(C.OCR_SPEEDS),
                             "Lấy phụ đề bằng OCR chữ Trung in sẵn: quét vùng chữ bao nhiêu lần mỗi giây")
        self.asr_max = f.row("Số chữ Hán tối đa / câu", spin(8, 60),
                             "Câu dài hơn mới chia đôi (ở dấu phẩy / chỗ nghỉ gần giữa); dấu phẩy nói liền không tách")
        self.asr_beam = f.row("Beam size (Whisper)", spin(1, 10))
        self.asr_vad = f.row("Lọc đoạn không có tiếng nói", Switch())
        a.add(f)

        b = SectionCard("Tách giọng & người nói", "layers")
        f = Form()
        self.sep_model = f.row("Mô hình tách giọng", combo(SEP_MODELS))
        self.sep_before = f.row("Tách giọng trước khi nhận dạng", Switch())
        self.diarize = f.row("Tự phân biệt người nói", Switch())
        self.spk_n = f.row("Số người nói (0 = tự đoán)", spin(0, 20))
        self.spk_thr = f.row("Độ nhạy gộp giọng (0.5–0.8)", dspin(0.3, 0.95, 0.02))
        b.add(f)

        t = SectionCard("Dịch thuật", "globe", "DeepSeek: giỏi tiếng Trung, rẻ – dịch cả bộ phim chỉ vài nghìn đồng. "
                        "Hy-MT2: dịch ngay trên máy, miễn phí (tải 1 lần ~3,6 GB ở tab Tài liệu của dự án).")
        f = Form()
        self.engine = f.row("Công cụ dịch mặc định", combo(C.ENGINES))
        self.target = f.row("Dịch sang (dự án mới)", combo(C.TARGETS),
                            "Tiếng đích mặc định cho dự án mới. Mỗi dự án đổi riêng ở tab Tự động → 'Dịch sang'")
        self.style = f.row("Văn phong mặc định", combo(C.STYLES))
        self.batch = f.row("Số câu mỗi lần gửi AI", spin(5, 120))
        self.ds_key = f.row("DeepSeek API key", key())
        self.ds_model = f.row("DeepSeek model", QLineEdit())
        self.oa_key = f.row("OpenAI API key", key())
        self.oa_base = f.row("OpenAI base URL", QLineEdit())
        self.oa_model = f.row("OpenAI model", QLineEdit())
        self.an_key = f.row("Claude API key", key())
        self.an_model = f.row("Claude model", QLineEdit())
        self.ge_key = f.row("Gemini API key", key())
        self.ge_model = f.row("Gemini model", QLineEdit())
        self.ol_base = f.row("Ollama URL", QLineEdit())
        self.ol_model = f.row("Ollama model", QLineEdit())
        t.add(f)
        self.test_out = label("", "muted", wrap=True)
        t.add(hbox(button("Thử dịch 1 câu", "sparkle", on_click=self.test_translate), None))
        t.add(self.test_out)

        g = SectionCard("Bảng thuật ngữ chung", "list", "Áp dụng mọi dự án dịch sang tiếng Việt. Mỗi dòng: 中文=Tiếng Việt")
        self.glossary = QPlainTextEdit()
        self.glossary.setMinimumHeight(140)
        g.add(self.glossary)

        o = SectionCard("Lồng tiếng, xoá phụ đề & xuất video", "film")
        f = Form()
        self.tts_engine = f.row("Bộ đọc mặc định", combo(C.TTS_ENGINES))
        self.vn_mode = f.row("VieNeu-TTS", combo([("v3turbo", "v3 Turbo – đẹp (khuyên dùng)"), ("v3nano", "v3 Nano – máy yếu")]))
        self.vn_style = f.row("Kiểu đọc VieNeu", combo([("clear", "Rõ ràng, ổn định (khuyên dùng)"),
                                                        ("natural", "Tự nhiên, nhiều ngữ điệu (mặc định VieNeu)")]),
                              "Rõ ràng: bớt ngẫu nhiên khi đọc -> phát âm rõ, ít đọc nhịu / lạc giọng")
        self.level_match = f.row("Cân âm lượng giọng lồng", Switch(),
                                 "Câu nào nhỏ hơn hẳn các câu khác cùng giọng (hoặc giọng nhỏ hơn hẳn giọng khác) "
                                 "được tăng lên cho đều, tối đa +9 dB")
        self.omni_model = f.row("OmniVoice", combo([("k2-fsa/OmniVoice", "OmniVoice gốc – đa ngôn ngữ"),
                                                    ("g-group-ai-lab/g-omnivoice",
                                                     "G-OmniVoice – tối ưu tiếng Việt (cần HF token)")]))
        self.hf_token = f.row("HuggingFace token", key(),
                              "Chỉ cần cho G-OmniVoice: tạo token ở huggingface.co/settings/tokens và bấm đồng ý "
                              "điều khoản ở trang mô hình g-group-ai-lab/g-omnivoice")
        self.maxspeed = f.row("Tăng tốc giọng tối đa", dspin(1, 2.5, 0.1),
                              "1,3 là vừa tự nhiên; cao hơn nghe vội")
        self.align_words = f.row("Căn thời gian từng chữ", Switch(),
                                 "Sau khi nhận dạng: mốc câu sát lúc nhân vật nói, tách câu ở chỗ nghỉ")
        self.split_spk = f.row("Tách câu 2 người nói liền nhau", Switch(),
                               "Khi phân biệt người nói: câu có 2 giọng khác nhau được tách đôi")
        self.dub_fit = f.row("AI dịch vừa thời lượng", Switch(),
                             "Gửi kèm số tiếng tối đa theo thời gian nói để giọng lồng khớp nhân vật")
        self.dub_short = f.row("Tự rút gọn câu đọc quá dài", Switch(),
                               "Sau khi lồng tiếng: câu vẫn quá dài được AI viết ngắn lại rồi đọc lại")
        self.inpaint = f.row("Xoá phụ đề", combo([("lama", "AI LaMa (đẹp)"), ("opencv", "OpenCV (nhanh)")]))
        self.codec = f.row("Bộ mã hoá video", combo([("libx264", "H.264 CPU (tương thích nhất)"),
                                                     ("h264_nvenc", "H.264 GPU NVIDIA (rất nhanh)"),
                                                     ("libx265", "H.265 (file nhỏ)")]))
        self.crf = f.row("Chất lượng CRF (thấp = đẹp)", spin(12, 35))
        self.preset = f.row("Tốc độ nén", combo([(x, x) for x in ("ultrafast", "veryfast", "fast", "medium", "slow")]))
        o.add(f)

        grid.addWidget(a, 0, 0)
        grid.addWidget(b, 0, 1)
        grid.addWidget(t, 1, 0, 2, 1)
        grid.addWidget(o, 1, 1)
        grid.addWidget(g, 2, 1)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)
        v.addLayout(grid)
        save = GradientButton("Lưu cài đặt", "save", big=True)
        save.clicked.connect(self.save)
        v.addWidget(save)
        v.addStretch(1)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(scroll(inner))

    def load(self):
        s = public_settings()
        for attr, k, kind in self.KEYS:
            w = getattr(self, attr)
            val = s.get(k)
            if kind == "c":
                set_combo(w, val)
            elif kind in ("i", "f"):
                w.setValue(val or 0)
            elif kind == "b":
                w.setChecked(bool(val))
            elif kind == "p":
                w.setPlainText(val or "")
            else:
                w.setText(val or "")
        self.refresh_status()

    def refresh_status(self):
        self.status.setText("Đang kiểm tra các gói AI…")

        def check():
            from .. import asr_funasr, diarize, lama, separate, tts
            from ..asr import detect_device

            torch_info = "chưa cài"
            if lama.available():
                try:
                    import torch

                    torch_info = (f"GPU ✓ ({torch.cuda.get_device_name(0)})" if torch.cuda.is_available()
                                  else f"CPU ⚠ (bản {torch.__version__} không dùng được card NVIDIA)")
                except Exception as e:  # noqa: BLE001
                    torch_info = f"lỗi: {str(e)[:80]}"
            return (detect_device()[0], separate.available(), asr_funasr.available(), diarize.available(),
                    tts.vieneu_available(), lama.available(), torch_info)

        run_async(check, self._show_status, lambda m: self.status.setText("Không kiểm tra được: " + m))

    def _show_status(self, r):
        dev, sep_ok, fa_ok, dz_ok, vn_ok, lama_ok, torch_info = r

        def ok(b):
            return "✓" if b else "✗ (chưa cài)"
        from ..asr_qwen import available as qwen_ok

        self.status.setText(
            f"Phiên bản {__version__} · Thiết bị: {'GPU NVIDIA ✓' if dev == 'cuda' else 'CPU'} · "
            f"Tách giọng {ok(sep_ok)} · Qwen3-ASR {'✓' if qwen_ok() else '✗ (cần transformers ≥ 5.13)'} · "
            f"SenseVoice/Paraformer {ok(fa_ok)} · "
            f"Phân người nói {ok(dz_ok)} · VieNeu-TTS {ok(vn_ok)} · "
            f"LaMa {ok(lama_ok)}   →  Thiếu gói nào hãy chạy CaiDat_AI_NangCao.bat"
            f"\nPyTorch (tách giọng, LaMa, VieNeu, phân người nói): {torch_info}"
            + ("\n⚠ Máy có card NVIDIA nhưng PyTorch đang chạy CPU → rất chậm. Cài lại PyTorch bản GPU theo README mục 1."
               if dev == "cuda" and torch_info.startswith("CPU") else ""))

    def collect(self) -> dict:
        d = {}
        for attr, k, kind in self.KEYS:
            w = getattr(self, attr)
            if kind == "c":
                d[k] = w.currentData()
            elif kind == "i":
                d[k] = int(w.value())
            elif kind == "f":
                d[k] = float(w.value())
            elif kind == "b":
                d[k] = w.isChecked()
            elif kind == "p":
                d[k] = w.toPlainText()
            else:
                d[k] = w.text()
        cur = load_settings()
        for k in list(d):
            if (k.endswith("_api_key") or k == "hf_token") and isinstance(d[k], str) and d[k].startswith("••••"):
                d[k] = cur.get(k, "")
        return d

    def save(self):
        save_settings(self.collect())
        self.win.project_page.settings = load_settings()
        try:   # nút Dịch / AI rút gọn ở trang dự án dùng đúng công cụ dịch vừa chọn
            self.win.project_page.sync_engine()
        except Exception:  # noqa: BLE001
            pass
        self.win.toast("Đã lưu cài đặt", "ok")
        self.load()

    def test_translate(self):
        save_settings(self.collect())
        from ..translate import translate_text

        self.test_out.setText("Đang dịch…")
        eng = self.engine.currentData()
        run_async(lambda: translate_text("师父，这件事你就别管了，我自己能搞定。", load_settings(), eng),
                  lambda r: self.test_out.setText("✓ " + r), lambda m: self.test_out.setText("✗ " + m))


# ====================================================================== Thanh tác vụ dưới cùng

class JobsBar(QFrame):
    def __init__(self, win):
        super().__init__()
        self.win = win
        self.setFixedHeight(56)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(18, 8, 18, 8)
        lay.setSpacing(12)
        self.icon = IconBadge("cpu", 32, theme.MUTED)
        self.title = label("Không có tác vụ nào đang chạy", "")
        self.title.setFont(theme.font(9.5, QFont.Weight.DemiBold))
        self.msg = label("", "muted")
        self.msg.setFont(theme.font(8.8))
        tb = QVBoxLayout()
        tb.setSpacing(0)
        tb.addWidget(self.title)
        tb.addWidget(self.msg)
        self.bar = ProgressBar(height=7)
        self.bar.setFixedWidth(260)
        self.queue = label("", "muted")
        self.cancel = button("Huỷ", "x", flat=True)
        self.cancel.clicked.connect(self._cancel)
        self.cancel.hide()
        lay.addWidget(self.icon)
        lay.addLayout(tb, 1)
        lay.addWidget(self.queue)
        lay.addWidget(self.bar)
        lay.addWidget(self.cancel)
        self.cur = None

    def paintEvent(self, e):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(theme.SIDEBAR))
        p.setPen(QColor(theme.BORDER))
        p.drawLine(0, 0, self.width(), 0)

    def _cancel(self):
        if self.cur:
            manager.cancel(self.cur)

    def update_jobs(self, jobs: list[dict]):
        running = [j for j in jobs if j["status"] == "running"]
        queued = [j for j in jobs if j["status"] == "queued"]
        self.queue.setText(f"{len(queued)} đang chờ" if queued else "")
        if running:
            j = running[0]
            self.cur = j["id"]
            self.icon.color = theme.ACCENT2
            self.icon.name = "cpu"
            self.title.setText(j["title"])
            eta = f" · còn ~{j['eta'] // 60} phút" if j["eta"] > 90 else (f" · còn ~{j['eta']}s" if j["eta"] > 0 else "")
            self.msg.setText(f"{j['message']} · {j['progress'] * 100:.1f}%{eta}")
            self.bar.set(j["progress"], True)
            self.bar.show()
            self.cancel.show()
        else:
            self.cur = None
            self.icon.color = theme.MUTED
            last = next((j for j in jobs if j["status"] in ("done", "error", "cancelled")), None)
            if last and time.time() - last["finished"] < 60:
                self.icon.name = {"done": "check", "error": "x"}.get(last["status"], "info")
                self.icon.color = {"done": theme.OK, "error": theme.ERR}.get(last["status"], theme.MUTED)
                self.title.setText(last["title"])
                self.msg.setText(last["error"][:200] if last["status"] == "error" else last["message"])
            else:
                self.title.setText("Không có tác vụ nào đang chạy")
                self.msg.setText("")
            self.bar.hide()
            self.cancel.hide()
        self.icon.update()


# ====================================================================== Cửa sổ chính

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"TraMi {__version__} – Dịch video Trung → Việt")
        self.setWindowIcon(QIcon(icons.app_logo(256)))
        self.resize(1480, 920)
        self.setMinimumSize(1100, 700)
        root = QWidget()
        root.setObjectName("Root")
        self.setCentralWidget(root)
        self.sidebar = Sidebar(self)
        self.pages = QStackedWidget()
        self.home = HomePage(self)
        self.project_page = ProjectPage(self)
        self.batch_page = BatchPage(self)
        self.concat_page = ConcatPage(self)
        self.settings_page = SettingsPage(self)
        self.page_keys = {"home": self.home, "project": self.project_page, "batch": self.batch_page,
                          "concat": self.concat_page, "settings": self.settings_page}
        for w in self.page_keys.values():
            self.pages.addWidget(w)
        self.jobsbar = JobsBar(self)
        right = QVBoxLayout()
        right.setContentsMargins(0, 0, 0, 0)
        right.setSpacing(0)
        right.addWidget(self.pages, 1)
        right.addWidget(self.jobsbar)
        lay = QHBoxLayout(root)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addWidget(self.sidebar)
        lay.addLayout(right, 1)

        self._seen: dict[str, str] = {}
        self.timer = QTimer(self, interval=600, timeout=self.poll)
        self.timer.start()
        self.refresh_projects()
        self.show_page("home")
        self._sysinfo()

    # ------------------------------------------------------------ điều hướng
    def show_page(self, key: str):
        if key == "project" and not self.project_page.p:
            self.toast("Chưa mở dự án nào", "warn")
            key = "home"
        if self.pages.currentWidget() is self.project_page and key != "project":
            self.project_page.flush()
            self.project_page.player.mp.pause()
        if key == "settings":
            self.settings_page.load()
        if key == "home":
            self.refresh_projects()
        self.pages.setCurrentWidget(self.page_keys[key])
        self.sidebar.nav[key].setChecked(True)
        self.sidebar.set_compact(key == "project")   # trang dự án: thanh bên chỉ còn icon, rộng chỗ cho timeline

    def go_home(self):
        self.show_page("home")

    def open_project(self, pid: str):
        self.project_page.flush()
        self.project_page.open(pid)
        self.show_page("project")
        self.sidebar.update()
        for i in range(self.sidebar.recent.count()):
            w = self.sidebar.recent.itemAt(i).widget()
            if w:
                w.update()

    def create_from_path(self, path: str):
        src = Path(path)
        if not src.exists():
            return self.toast("Không tìm thấy file", "err")
        self.toast("Đang mở video…")

        def make():
            p = Project.create(src.stem)
            try:
                pipeline.attach_source(p, src)
            except Exception:
                p.delete()
                raise
            return p.id

        def done(pid):
            self.refresh_projects()
            self.open_project(pid)
        run_async(make, done, lambda m: self.toast("Không mở được video: " + m, "err"))

    def refresh_projects(self):
        items = Project.list_all()
        self.sidebar.set_projects(items)
        self.home.set_projects(items)

    def toast(self, text: str, kind: str = "info"):
        Toast(self.centralWidget(), text, kind, 6000 if kind == "err" else 3500)

    def jobs_changed(self):
        self.poll()

    def poll(self):
        jobs = manager.list()
        self.jobsbar.update_jobs(jobs)
        self.project_page.update_job(jobs)
        for j in jobs:
            prev = self._seen.get(j["id"])
            if prev and prev != j["status"] and j["status"] in ("done", "error", "cancelled"):
                if j["status"] == "done":
                    self.toast("✓ " + (j["message"] or j["title"]), "ok")
                elif j["status"] == "error":
                    self.toast(f"{j['title']}: {j['error'][:300]}", "err")
                self.project_page.job_finished(j)
                self.refresh_projects()
            self._seen[j["id"]] = j["status"]

    def _sysinfo(self):
        def get():
            from .. import ffmpeg as ff
            from ..asr import detect_device

            dev, _ = detect_device()
            try:
                ff.ffmpeg_bin()
                fok = True
            except Exception:
                fok = False
            return dev, fok, ff.encoder_works("h264_nvenc") if fok else False

        def done(r):
            dev, fok, nv = r
            self.sidebar.sys.setText(f"v{__version__} · {'GPU NVIDIA ✓' if dev == 'cuda' else 'CPU'}"
                                     f"{' · NVENC ✓' if nv else ''}" + ("" if fok else "\n⚠ Thiếu FFmpeg!"))
        run_async(get, done)

    def closeEvent(self, e):
        self.project_page.flush()
        running = [j for j in manager.list() if j["status"] in ("running", "queued")]
        if running and QMessageBox.question(self, "Thoát?", f"Còn {len(running)} tác vụ đang chạy/chờ. Thoát sẽ huỷ chúng. "
                                            "Vẫn thoát?") != QMessageBox.StandardButton.Yes:
            e.ignore()
            return
        for j in running:
            manager.cancel(j["id"])
        e.accept()


def _dark_titlebar(win: QMainWindow):
    if sys.platform != "win32":
        return
    try:
        import ctypes

        hwnd = int(win.winId())
        val = ctypes.c_int(1)
        for attr in (20, 19):  # DWMWA_USE_IMMERSIVE_DARK_MODE
            if ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, attr, ctypes.byref(val), ctypes.sizeof(val)) == 0:
                break
        corner = ctypes.c_int(2)  # bo góc Windows 11
        ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 33, ctypes.byref(corner), ctypes.sizeof(corner))
    except Exception:
        pass


def _crash_log():
    """Ghi lại nếu chương trình bị sập ở tầng thư viện C/GPU (không có Traceback Python)."""
    import faulthandler

    from ..config import DATA_DIR

    try:
        (DATA_DIR / "logs").mkdir(parents=True, exist_ok=True)
        f = open(DATA_DIR / "logs" / "sap.txt", "a", encoding="utf-8")
        f.write(f"\n===== {time.strftime('%Y-%m-%d %H:%M:%S')} mở TraMi\n")
        f.flush()
        faulthandler.enable(file=f, all_threads=True)
        return f
    except OSError:
        return None


def run():
    from ..config import fix_std_streams

    fix_std_streams()   # python -m trami / exe không cửa sổ đen
    _keep_crash_file = _crash_log()  # noqa: F841
    os.environ.setdefault("QT_ENABLE_HIGHDPI_SCALING", "1")
    if sys.platform == "win32":
        try:
            import ctypes

            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("TraMi.VideoTranslator")
        except Exception:
            pass
    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName("TraMi")
    theme.load_fonts()
    app.setFont(theme.font(10))
    app.setStyleSheet(theme.stylesheet())
    app.setWindowIcon(QIcon(icons.app_logo(256)))
    C.install_wheel_guard(app)
    win = MainWindow()
    _dark_titlebar(win)
    win.show()
    return app.exec()
