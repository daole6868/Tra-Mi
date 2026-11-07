"""Trang Dung lượng (2.1.29): dự án nào nặng, phần nào là đệm / rác dọn được (hỏi lại kèm số GB trước khi xoá) + tự dọn."""
from __future__ import annotations

import time

from PySide6.QtCore import QRectF, Qt, QUrl
from PySide6.QtGui import QColor, QDesktopServices, QFont, QPainter
from PySide6.QtWidgets import QFrame, QMessageBox, QSpinBox, QVBoxLayout, QWidget

from .. import storage as S
from ..config import load_settings, save_settings
from ..jobs import manager
from . import theme
from .common import run_async
from .widgets import SectionCard, Switch, button, flow, hbox, label

BAR_COLORS = {"cache": theme.INFO, "junk": theme.WARN, "stems": "#9B6BFF", "voice": theme.OK, "inpaint": "#3FC1C9",
              "output": theme.ACCENT, "source": "#C47B4A", "other": "#5A6478"}
BAR_HELP = {"cache": "tool tự tạo lại khi cần", "junk": "không còn dùng", "stems": "tách lại mất 10–30 phút",
            "voice": "giọng từng câu + bản lồng", "inpaint": "chạy xoá lại rất lâu", "output": "sản phẩm của bạn",
            "source": "video tải từ link", "other": "phụ đề, cài đặt dự án…"}


def _ago(t: float) -> str:
    if not t:
        return ""
    d = int((time.time() - t) // 86400)
    return "sửa hôm nay" if d <= 0 else "sửa hôm qua" if d == 1 else f"sửa {d} ngày trước"


class SizeBar(QWidget):
    """Thanh màu chia theo nhóm file của 1 dự án (rê chuột xem từng nhóm)."""

    def __init__(self, sc: dict):
        super().__init__()
        self.bars = sc["bars"]
        self.total = max(1, sc["total"])
        self.setFixedHeight(12)
        tip = [f"{dict(S.BARS)[k]}: {S.fmt_size(n)}" for k, _ in S.BARS if (n := self.bars.get(k, 0)) > 0]
        groups = [f"   · {S.GROUPS[g][0]}: {S.fmt_size(n)}" for g, n in sorted(sc["groups"].items(), key=lambda x: -x[1])
                  if n >= 1e6]
        self.setToolTip("\n".join(tip + ([""] + groups if groups else [])))

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(0, 1, 0, -1)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(theme.BORDER))
        p.drawRoundedRect(r, 5, 5)
        p.setClipRect(r)
        x = r.left()
        for k, _lb in S.BARS:
            n = self.bars.get(k, 0)
            if n <= 0:
                continue
            w = r.width() * n / self.total
            p.setBrush(QColor(BAR_COLORS[k]))
            p.drawRect(QRectF(x, r.top(), max(1.0, w), r.height()))
            x += w


class ProjectRow(QFrame):
    def __init__(self, page: "StoragePage", sc: dict, busy: bool, is_open: bool):
        super().__init__()
        self.setObjectName("StorageRow")
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 10, 0, 10)
        v.setSpacing(6)
        name = label(sc["name"])
        name.setFont(theme.font(10.5, QFont.Weight.DemiBold))
        bits = [S.fmt_size(sc["total"]), _ago(sc["updated"])]
        if sc["has_output"]:
            bits.append("đã xuất video")
        if is_open:
            bits.append("đang mở (giữ đệm nghe + bản xem trước)")
        if busy:
            bits.append("⏳ đang chạy việc – chưa dọn được")
        meta = label(" · ".join(b for b in bits if b), "muted", wrap=True)
        pr = page.protect(sc["id"])
        cache = S.plan(sc, (S.CACHE,), pr)["bytes"]
        junk = S.plan(sc, (S.JUNK,), pr)["bytes"]
        b1 = button(f"Dọn đệm · {S.fmt_size(cache)}", "refresh", "Xoá đệm (tool tự tạo lại khi cần – lần mở / xuất sau "
                    "chậm hơn 1 chút)", flat=True, on_click=lambda: page.clean([sc], S.CACHE))
        b2 = button(f"Dọn rác · {S.fmt_size(junk)}", "trash", "Xoá file không còn dùng: giọng câu cũ, bản lồng cũ chưa "
                    "ghim, bản xoá phụ đề không còn khớp, file tạm", flat=True, on_click=lambda: page.clean([sc], S.JUNK))
        b3 = button("", "folder", "Mở thư mục dự án", flat=True,
                    on_click=lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(sc["dir"]))))
        b1.setEnabled(cache > 0 and not busy)
        b2.setEnabled(junk > 0 and not busy)
        v.addWidget(name)
        v.addWidget(meta)
        v.addWidget(SizeBar(sc))
        v.addWidget(flow(b1, b2, b3))


class StoragePage(QWidget):
    def __init__(self, win):
        super().__init__()
        from .project_page import scroll

        self.win = win
        self.scans: list[dict] = []
        self.glob: dict = {}
        self._busy_scan = False
        inner = QWidget()
        v = QVBoxLayout(inner)
        v.setContentsMargins(28, 24, 28, 24)
        v.setSpacing(16)
        v.addWidget(label("Dung lượng", "h1"))
        v.addWidget(label("Dọn đệm: chỉ xoá file tool tự tạo lại được (đệm nghe, âm thanh 16 kHz, đệm xuất…). Dọn rác: "
                          "xoá file không còn dùng. Video xuất, video gốc, bản tách giọng, giọng và bản lồng đang dùng "
                          "luôn được giữ.", "muted", wrap=True))

        c = SectionCard("Tổng quan", "disk")
        self.sum_lb = label("Đang quét…", wrap=True)
        self.sum_lb.setTextFormat(Qt.TextFormat.RichText)
        c.add(self.sum_lb)
        leg = label(" &nbsp; ".join(f"<span style='color:{BAR_COLORS[k]}'>●</span> {lb}" for k, lb in S.BARS))
        leg.setTextFormat(Qt.TextFormat.RichText)
        leg.setToolTip("\n".join(f"{lb}: {BAR_HELP[k]}" for k, lb in S.BARS))
        c.add(leg)
        self.b_all_cache = button("Dọn đệm tất cả dự án", "refresh", on_click=lambda: self.clean(self.scans, S.CACHE))
        self.b_all_junk = button("Dọn rác tất cả dự án", "trash", on_click=lambda: self.clean(self.scans, S.JUNK))
        c.add(flow(self.b_all_cache, self.b_all_junk, button("Quét lại", "search", flat=True, on_click=self.refresh)))
        v.addWidget(c)

        a = SectionCard("Tự dọn", "clock", "Chạy ngầm, chỉ xoá đệm / file tạm / bản lồng cũ – không bao giờ xoá video "
                        "hay giọng đang dùng")
        st = load_settings()
        self.sw_auto = Switch(bool(st.get("auto_clean", True)))
        self.sp_live = QSpinBox()
        self.sp_live.setRange(1, 20)
        self.sp_live.setValue(int(st.get("live_keep_projects", 2) or 2))
        self.sp_dub = QSpinBox()
        self.sp_dub.setRange(0, 50)
        self.sp_dub.setSpecialValueText("không giới hạn")
        self.sp_dub.setValue(int(st.get("dub_keep_versions", 3) or 0))
        for sp in (self.sp_live, self.sp_dub):
            sp.setFixedWidth(150)
        a.add(hbox(self.sw_auto, label("Bật tự dọn"), stretch_last=True))
        a.add(hbox(label("Giữ đệm nghe cho"), self.sp_live, label("dự án mở gần nhất (phim 2 giờ ~2–4 GB mỗi dự án)",
                                                                   "muted"), stretch_last=True))
        a.add(hbox(label("Mỗi dự án giữ tối đa"), self.sp_dub, label("bản lồng (bản đang dùng + bản đã ghim ở tab Lồng "
                                                                     "tiếng mục 4 luôn giữ)", "muted"), stretch_last=True))
        a.add(label("Mỗi lần mở tool: xoá file tạm cũ hơn 1 ngày (lần chạy bị ngắt, ảnh chụp khung, file tải dở).",
                    "muted", wrap=True))
        self.sw_auto.toggled.connect(self._save_auto)
        self.sp_live.valueChanged.connect(self._save_auto)
        self.sp_dub.valueChanged.connect(self._save_auto)
        v.addWidget(a)

        self.list_card = SectionCard("Dự án", "folder", "Nặng nhất ở trên. Rê chuột lên thanh màu để xem từng phần.")
        self.rows = QVBoxLayout()
        self.rows.setSpacing(0)
        self.list_card.add(self.rows)
        self.empty = label("Đang quét…", "muted")
        self.list_card.add(self.empty)
        v.addWidget(self.list_card)
        v.addStretch(1)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(scroll(inner))

    # ------------------------------------------------------------ dữ liệu
    def busy_ids(self) -> set[str]:
        return {j["project_id"] for j in manager.list() if j["status"] in ("running", "queued") and j["project_id"]}

    def open_id(self) -> str | None:
        pp = getattr(self.win, "project_page", None)
        return pp.p.id if pp is not None and pp.p is not None else None

    def protect(self, pid: str) -> tuple:
        return S.PROTECT_OPEN if pid == self.open_id() else ()

    def refresh(self):
        if self._busy_scan:
            return
        self._busy_scan = True
        self.sum_lb.setText("Đang quét…")

        def work():
            return [S.scan_project(d) for d in S.project_dirs()], S.scan_global()

        def done(res):
            self._busy_scan = False
            self.scans, self.glob = res
            self._show()

        def err(m):
            self._busy_scan = False
            self.sum_lb.setText("Không quét được: " + m)
        run_async(work, done, err)

    def _show(self):
        while self.rows.count():
            w = self.rows.takeAt(0).widget()
            if w:
                w.hide()
                w.setParent(None)
                w.deleteLater()
        busy, oid = self.busy_ids(), self.open_id()
        scans = sorted(self.scans, key=lambda s: -s["total"])
        for sc in scans:
            self.rows.addWidget(ProjectRow(self, sc, sc["id"] in busy, sc["id"] == oid))
        self.empty.setVisible(not scans)
        self.empty.setText("Chưa có dự án nào.")
        proj = sum(s["total"] for s in scans)
        free_c = sum(S.plan(s, (S.CACHE,), self.protect(s["id"]))["bytes"] for s in scans if s["id"] not in busy)
        free_j = sum(S.plan(s, (S.JUNK,), self.protect(s["id"]))["bytes"] for s in scans if s["id"] not in busy)
        g = self.glob or {}
        models = " · ".join(f"{lb} {S.fmt_size(n)}" for lb, _p, n in g.get("models", []) if n > 0)
        self.sum_lb.setText(
            f"<b>Dự án: {S.fmt_size(proj)}</b> ({len(scans)} dự án) &nbsp;·&nbsp; "
            f"<b>Mô hình: {S.fmt_size(g.get('models_total', 0))}</b>"
            + (f" <span style='color:{theme.MUTED}'>({models})</span>" if models else "")
            + f" &nbsp;·&nbsp; Khác: {S.fmt_size(g.get('other', 0))}<br>"
            f"Dọn được ngay: <span style='color:{BAR_COLORS['cache']}'>Đệm {S.fmt_size(free_c)}</span> &nbsp;·&nbsp; "
            f"<span style='color:{BAR_COLORS['junk']}'>Rác {S.fmt_size(free_j)}</span>")
        self.b_all_cache.setEnabled(free_c > 0)
        self.b_all_junk.setEnabled(free_j > 0)

    # ------------------------------------------------------------ dọn
    def clean(self, scans: list[dict], kind: str):
        busy = self.busy_ids()
        todo = [(sc, S.plan(sc, (kind,), self.protect(sc["id"]))) for sc in scans if sc["id"] not in busy]
        todo = [(sc, pl) for sc, pl in todo if pl["bytes"] > 0]
        total = sum(pl["bytes"] for _sc, pl in todo)
        if not total:
            return self.win.toast("Không có gì để dọn", "info")
        sums: dict[str, int] = {}
        for _sc, pl in todo:
            for gname, n in pl["groups"].items():
                sums[gname] = sums.get(gname, 0) + n
        lines = "\n".join(f"  · {S.GROUPS[gname][0]}: {S.fmt_size(n)}" for gname, n in
                          sorted(sums.items(), key=lambda x: -x[1]))
        who = f"dự án \"{todo[0][0]['name']}\"" if len(todo) == 1 else f"{len(todo)} dự án"
        what = "đệm" if kind == S.CACHE else "rác"
        note = ("\n\nTool tự tạo lại đệm khi cần (lần mở / xuất sau chậm hơn 1 chút)." if kind == S.CACHE else
                "\n\nCác file này không còn dùng. Muốn giữ 1 bản lồng cũ thì ghim nó ở tab Lồng tiếng mục 4 trước.")
        skipped = len([s for s in scans if s["id"] in busy])
        if skipped:
            note += f"\n{skipped} dự án đang chạy việc – bỏ qua."
        if QMessageBox.question(self, f"Dọn {what}?", f"Xoá {S.fmt_size(total)} {what} của {who}:\n{lines}{note}") != \
                QMessageBox.StandardButton.Yes:
            return
        oid = self.open_id()

        def work():
            freed = failed = 0
            for sc, _pl in todo:
                r = S.clean(sc, (kind,), self.protect(sc["id"]))
                freed += r["bytes"]
                failed += r["failed"]
            return freed, failed

        def done(res):
            freed, failed = res
            self.win.toast(f"Đã dọn {S.fmt_size(freed)}" + (f" · {failed} file đang được mở – để lần sau" if failed
                                                             else ""), "ok")
            if oid and any(sc["id"] == oid for sc, _pl in todo) and kind == S.JUNK:
                self.win.project_page._fill_dub_versions()   # bản lồng cũ vừa xoá biến khỏi mục 4
            self.refresh()
        self.win.toast(f"Đang dọn {S.fmt_size(total)}…")
        run_async(work, done, lambda m: self.win.toast("Dọn lỗi: " + m, "err"))

    def _save_auto(self, *_):
        vals = {"auto_clean": self.sw_auto.isChecked(), "live_keep_projects": self.sp_live.value(),
                "dub_keep_versions": self.sp_dub.value()}
        save_settings(vals)
        pp = getattr(self.win, "project_page", None)
        if pp is not None:
            pp.settings.update(vals)
