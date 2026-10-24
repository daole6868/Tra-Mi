"""Tab "Tài liệu": danh sách mô hình AI, trạng thái đã tải / chưa tải, nút tải về có % và tốc độ thật."""
from __future__ import annotations

from PySide6.QtCore import QTimer, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QHBoxLayout, QVBoxLayout, QWidget

from .. import models
from ..config import MODELS_DIR, load_settings
from ..jobs import manager
from .common import run_async
from .widgets import Card, GradientButton, IconBadge, ProgressBar, SectionCard, button, hbox, label


class ModelRow(Card):
    def __init__(self, info: models.ModelInfo, on_download):
        super().__init__(padding=12)
        self.info = info
        self.job_id = None
        top = QHBoxLayout()
        top.setSpacing(12)
        top.addWidget(IconBadge(info.icon, 34))
        txt = QVBoxLayout()
        txt.setSpacing(2)
        self.title = label(f"{info.name}  ·  {info.size}")
        self.title.setStyleSheet("font-weight: 600;")
        txt.addWidget(self.title)
        txt.addWidget(label(info.use, "muted", wrap=True))
        top.addLayout(txt, 1)
        self.status = label("Đang kiểm tra…", "muted")
        top.addWidget(self.status)
        self.btn = button("Tải về", "download", on_click=lambda: on_download(info.key))
        top.addWidget(self.btn)
        self.add(top)
        self.bar = ProgressBar(height=6)
        self.detail = label("", "muted")
        self.add(self.bar)
        self.add(self.detail)
        self.bar.hide()
        self.detail.hide()

    def set_status(self, st: str):
        if self.job_id:
            return
        if st == "ready":
            self.status.setText("✓ Đã có trên máy")
            self.status.setProperty("muted", False)
            self.status.setStyleSheet("color: #4ADE80;")
            self.btn.setText("Đã tải")
            self.btn.setEnabled(False)
        elif st.startswith("nopkg:"):
            self.status.setText("Chưa cài gói " + st[6:])
            self.status.setStyleSheet("color: #F59E0B;")
            self.btn.setText("Tải về")
            self.btn.setEnabled(False)
            if "transformers>=" in st:
                from ..asr_qwen import UPGRADE_CMD

                self.status.setText("Cần cập nhật gói transformers (≥ 5.13)")
                self.btn.setToolTip("Mở cửa sổ đen trong thư mục TraMi và chạy:\n" + UPGRADE_CMD)
                self.status.setToolTip(self.btn.toolTip())
            else:
                self.btn.setToolTip("Chạy CaiDat_AI_NangCao.bat để cài gói AI trước")
        else:
            self.status.setText("Chưa tải")
            self.status.setStyleSheet("color: #F87171;")
            self.btn.setText("Tải về")
            self.btn.setEnabled(True)

    def show_job(self, j):
        running = j.status in ("queued", "running")
        self.bar.setVisible(True)
        self.detail.setVisible(True)
        self.bar.set(j.progress, busy=running and j.progress <= 0.001)
        if j.status == "queued":
            self.detail.setText("Đang chờ tác vụ khác chạy xong…")
        elif running:
            self.detail.setText(j.message)
        elif j.status == "done":
            self.detail.setText("✓ " + (j.message or "Đã tải xong"))
        elif j.status == "cancelled":
            self.detail.setText("Đã huỷ – bấm Tải về để tải tiếp")
        else:
            self.detail.setText("Lỗi: " + (j.error or j.message))
        self.btn.setEnabled(False if running else self.btn.isEnabled())
        if running:
            self.status.setText("Đang tải…")
            self.status.setStyleSheet("color: #60A5FA;")


class ModelsTab(QWidget):
    def __init__(self, win):
        super().__init__()
        self.win = win
        self.rows: dict[str, ModelRow] = {}
        card = SectionCard("Mô hình AI của TraMi", "download",
                           "Tải mỗi mô hình một lần, lưu luôn trên máy. Khi dịch video, tool lấy mô hình đã tải ở đây "
                           "(không tải lại). Mô hình chưa tải sẽ được tự tải khi cần dùng lần đầu.")
        all_btn = GradientButton("Tải tất cả mô hình còn thiếu", "download")
        all_btn.clicked.connect(self.download_missing)
        card.add(hbox(all_btn, button("Kiểm tra lại", "refresh", on_click=self.refresh),
                      None, button("Mở thư mục mô hình", "folder",
                                   on_click=lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(MODELS_DIR))))))
        for m in models.MODELS:
            row = ModelRow(m, self.download)
            self.rows[m.key] = row
            card.add(row)
        card.add(label("Dịch bằng Google / AI và giọng đọc Microsoft Edge chạy qua mạng nên không cần tải mô hình.",
                       "muted", wrap=True))
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 2, 0)
        v.addWidget(card)
        v.addStretch(1)
        self.timer = QTimer(self, interval=400, timeout=self._poll)
        self._checked = False

    def showEvent(self, e):
        super().showEvent(e)
        if not self._checked:
            self._checked = True
            self.refresh()
        self.timer.start()

    def hideEvent(self, e):
        super().hideEvent(e)
        self.timer.stop()

    def refresh(self):
        for r in self.rows.values():
            if not r.job_id:
                r.status.setText("Đang kiểm tra…")
        st = load_settings()
        run_async(lambda: models.status_all(st), self._apply_status,
                  lambda m: self.win.toast("Không kiểm tra được mô hình: " + m, "err"))

    def _apply_status(self, res: dict):
        self.status = res
        for k, st in res.items():
            if k in self.rows:
                self.rows[k].set_status(st)

    def download(self, key: str):
        row = self.rows[key]
        if row.job_id:
            j = manager.get(row.job_id)
            if j and j.status in ("queued", "running"):
                return
        st = load_settings()
        # VieNeu / ECAPA nạp thử mô hình lên card đồ hoạ khi tải -> tính là việc GPU (chờ việc GPU khác xong)
        job = manager.submit("model", f"Tải mô hình: {models.BY_KEY[key].name}",
                             lambda j: (models.download(key, j.update, j.cancelled, st) or
                                        {"message": f"Đã tải xong {models.BY_KEY[key].name}"}),
                             gpu=key in ("vieneu", "ecapa"))
        row.job_id = job.id
        row.show_job(job)
        self.win.jobs_changed()
        self.timer.start()

    def download_missing(self):
        st = getattr(self, "status", {}) or {}
        todo = [k for k, s in st.items() if s == "missing"]
        if not todo:
            return self.win.toast("Không còn mô hình nào cần tải (hoặc chưa cài gói AI)", "ok")
        for k in todo:
            self.download(k)

    def _poll(self):
        busy = False
        for key, row in self.rows.items():
            if not row.job_id:
                continue
            j = manager.get(row.job_id)
            if not j:
                row.job_id = None
                continue
            row.show_job(j)
            if j.status in ("queued", "running"):
                busy = True
            else:
                row.job_id = None
                self.refresh()
        if not busy and not self.isVisible():
            self.timer.stop()
