"""Bảng phụ đề (model + delegate) cho QTableView."""
from __future__ import annotations

from PySide6.QtCore import QAbstractTableModel, QModelIndex, QRectF, QSortFilterProxyModel, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter
from PySide6.QtWidgets import QStyledItemDelegate

from ..subtitle import Segment, parse_time
from . import theme
from .player import fmt

COLS = ["#", "Bắt đầu", "Kết thúc", "Người", "Tiếng Trung", "Tiếng Việt", "🎵", "Bỏ lồng", "Độ vừa", "Chọn", "Giọng"]
C_N, C_START, C_END, C_SPK, C_ZH, C_VI, C_SONG, C_NODUB, C_FIT, C_SEL, C_VOICE = range(11)
VOICE_DEFAULT = "(Theo người nói)"
FIT_TEXT = {"ok": "✓", "fast": "⚡", "long": "⚠"}
FIT_COLOR = {"ok": "#4ADE80", "fast": "#F5B841", "long": "#F87171"}
SPK_COLORS = ["#FF8A3D", "#5B8CFF", "#3CCB7F", "#C07BFF", "#F5B841", "#FF5FA2", "#3BD1D1", "#B5C44B"]


OVERFLOW_SHORT = {"smart": "Tự động", "push": "Nối tiếp", "overlap": "Đọc đè", "early": "Đọc sớm", "cut": "Cắt bớt"}


def fit_text(s: Segment) -> str:
    """Chữ ngắn ở cột Độ vừa: ✓/⚡/⚠ ×tốc độ, ◀ đọc sớm, ▶ bị lùi, ⧉ đọc đè, ✂ cắt, ✎ đặt tay."""
    ex = s.extra
    if ex.get("nodub"):
        return "bỏ lồng"
    if ex.get("song") and not ex.get("dub_at"):
        return "🎵 bài hát"
    fit = ex.get("fit")
    if not fit:
        return "✎ ×" + f"{float(ex['speed']):.2f}" if ex.get("speed") else ""
    sp = float(ex.get("fit_speed", 1) or 1)
    out = FIT_TEXT.get(fit, "") + (f" ×{sp:.2f}" if sp > 1.06 or ex.get("speed") else "")
    sh = float(ex.get("fit_shift", 0) or 0)
    if sh <= -0.3:
        out += f" ◀{-sh:.1f}s"
    elif sh >= 0.3:
        out += f" ▶{sh:.1f}s"
    if float(ex.get("fit_over", 0) or 0) >= 0.1:
        out += f" ⧉{float(ex['fit_over']):.1f}s"
    if ex.get("overflow") or ex.get("speed"):
        out += " ✎"
    return out


ENGINE_LABEL = {"firered": "FireRedASR2", "qwen3": "Qwen3-ASR"}


def asr_tip(s) -> str:
    """Chú thích chữ gốc: 2 bộ nghe khác nhau / câu bộ nghe thêm (OCR + nghe) / tên đã sửa."""
    lines = []
    if s.extra.get("asr_alt") is not None:
        who = ENGINE_LABEL.get(s.extra.get("asr_alt_eng", ""), "Bộ nghe 2")
        alt = s.extra["asr_alt"] or "(không nghe ra chữ nào – có thể là nhạc / tiếng động)"
        lines.append(f"2 bộ nghe ra khác nhau – {who}: {alt}")
        pick = s.extra.get("asr_pick")
        if pick:
            lines.append({"A": "AI giữ bản đang có", "B": f"AI chọn bản của {who}",
                          "C": "AI ghép từ 2 bản"}.get(pick[:1], "AI chọn") + (pick[1:] if len(pick) > 1 else ""))
        elif s.extra.get("asr_unsure"):
            lines.append("Chưa chọn được – nghe lại rồi sửa chữ, hoặc tích câu rồi bấm 'Dùng bản bộ nghe 2'")
    if s.extra.get("from_asr"):
        lines.append("OCR + nghe: phụ đề in sẵn không có câu này – bộ nghe nghe thêm (nên nghe lại)")
    if s.extra.get("name_fix"):   # [[chữ cũ, tên]] (sửa theo âm đọc) hoặc [tên] (nghe lại ra đúng tên)
        parts = [f"{x[0]} → {x[1]}" if isinstance(x, (list, tuple)) and len(x) == 2 else f"nghe lại ra {x}"
                 for x in s.extra["name_fix"]]
        lines.append("Sửa tên riêng: " + ", ".join(parts))
    if s.extra.get("src_before"):
        lines.append(f"Trước khi sửa: {s.extra['src_before']}")
    return "\n".join(lines)


def fit_tip(s: Segment) -> str:
    """Giải thích đầy đủ độ vừa của câu (chú thích cột Độ vừa, khung Câu đang chọn, timeline)."""
    ex = s.extra
    if ex.get("nodub"):
        return "Câu đánh dấu 'Bỏ lồng' – không đọc câu này (tích câu rồi bấm 'Lồng lại' để bỏ dấu)"
    if ex.get("song") and not ex.get("dub_at"):
        return "Câu bài hát 🎵 – giữ nguyên giọng hát gốc (tích câu rồi bấm 'Bỏ 🎵' nếu là lời thoại)"
    fit = ex.get("fit")
    sp = float(ex.get("fit_speed", 1) or 1)
    lines = [{"ok": "Giọng lồng vừa thời gian nhân vật nói",
              "fast": f"Đọc nhanh hơn ×{sp:.2f} cho vừa",
              "long": "Quá dài so với thời gian nhân vật nói (kể cả khi đã tăng tốc) – nên sửa ngắn lại / AI rút gọn"
              }.get(fit, "Chưa lồng tiếng")]
    if fit == "long" and sp > 1.06:
        lines.append(f"Đã tăng tốc ×{sp:.2f}")
    sh = float(ex.get("fit_shift", 0) or 0)
    if sh <= -0.3:
        lines.append(f"◀ Đọc sớm {-sh:.1f} s trước lúc nhân vật nói (để câu sau vào đúng giờ)")
    elif sh >= 0.3:
        lines.append(f"▶ Bị lùi {sh:.1f} s vì câu trước đọc chưa xong")
    if float(ex.get("fit_over", 0) or 0) >= 0.1:
        lines.append(f"⧉ Đọc đè lên câu sau {float(ex['fit_over']):.1f} s")
    if ex.get("overflow"):
        lines.append(f"✎ Cách xếp riêng câu này: {OVERFLOW_SHORT.get(ex['overflow'], ex['overflow'])}")
    if ex.get("speed"):
        lines.append(f"✎ Tốc độ đặt tay: tối thiểu ×{float(ex['speed']):.2f} (đang đọc ×{sp:.2f}) – 'Về tốc độ gốc' để bỏ")
    if ex.get("trans_long"):
        lines.append(f"AI đã rút gọn từ: {ex['trans_long']}")
    return "\n".join(lines)


def spk_color(spk: str) -> str:
    try:
        return SPK_COLORS[(int(spk.lstrip("S")) - 1) % len(SPK_COLORS)]
    except Exception:
        return theme.MUTED


class SubsModel(QAbstractTableModel):
    edited = Signal()
    picked = Signal()          # đổi các câu được tích chọn (cột "Chọn" ở bảng lồng tiếng)

    def __init__(self, segs: list[Segment] | None = None):
        super().__init__()
        self.segs: list[Segment] = segs or []
        self.active = -1
        # câu đã tích (chỉ trong phiên làm việc, không lưu) – nhớ theo ĐÚNG CÂU (id đối tượng -> câu), không theo số thứ
        # tự: gộp / tách / thêm / xoá câu làm số thứ tự đổi; trước 2.0.54 tích giữ số cũ -> trỏ sai câu / ra ngoài danh sách
        # (IndexError ở "Tạo lại giọng các câu đã tích" sau khi gộp câu)
        self._picked: dict[int, Segment] = {}
        self.voice_names: dict[str, str] = {}  # mã giọng -> tên hiển thị

    @property
    def selected(self) -> set[int]:
        """Số thứ tự (hiện tại) của các câu đã tích."""
        return {i for i, s in enumerate(self.segs) if id(s) in self._picked}

    def is_picked(self, row: int) -> bool:
        return 0 <= row < len(self.segs) and id(self.segs[row]) in self._picked

    def set_segments(self, segs: list[Segment]):
        self.beginResetModel()
        if segs is self.segs:
            # thêm / gộp / tách / xoá câu ngay trong danh sách: tích đi theo câu, câu đã xoá thì bỏ
            alive = {id(x) for x in segs}
            self._picked = {k: v for k, v in self._picked.items() if k in alive}
        elif len(segs) == len(self.segs):     # đọc lại dự án (cùng các câu, đối tượng mới): giữ tích theo số thứ tự
            self._picked = {id(segs[r]): segs[r] for r in self.selected}
        else:                                 # danh sách câu đổi hẳn -> bỏ tích
            self._picked = {}
        self.segs = segs
        self.endResetModel()
        self.picked.emit()

    def set_picked(self, rows, on: bool = True):
        for r in rows:
            if 0 <= r < len(self.segs):
                seg = self.segs[r]
                if on:
                    self._picked[id(seg)] = seg
                else:
                    self._picked.pop(id(seg), None)
        if self.segs:
            self.dataChanged.emit(self.index(0, C_SEL), self.index(len(self.segs) - 1, C_SEL))
        self.picked.emit()

    def set_voices(self, rows, vid: str) -> list[int]:
        """Giọng riêng cho nhiều câu, báo đổi 1 lần. Đặt từng câu bằng setData thì mọi thứ chạy theo mỗi lần sửa (ảnh
        chụp hoàn tác cả danh sách câu, phụ đề xem trước, đoạn giọng trên timeline…) lặp N lần – 2.600 câu đơ vài phút.
        Trả số thứ tự các câu thật sự đổi giọng."""
        vid = (vid or "").strip()
        changed = []
        for r in rows:
            if 0 <= r < len(self.segs):
                ex = self.segs[r].extra
                if (ex.get("voice") or "") != vid:
                    if vid:
                        ex["voice"] = vid
                    else:
                        ex.pop("voice", None)
                    changed.append(r)
        if changed:
            self.dataChanged.emit(self.index(min(changed), 0), self.index(max(changed), len(COLS) - 1))
            self.edited.emit()
        return changed

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.segs)

    def columnCount(self, parent=QModelIndex()):
        return len(COLS)

    def headerData(self, section, orient, role=Qt.ItemDataRole.DisplayRole):
        if orient == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            if section == C_VI:
                return getattr(self, "trans_label", COLS[C_VI])
            return COLS[section]
        if orient == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.ToolTipRole and section == C_SEL:
            return "Bấm để tích / bỏ tích tất cả câu đang hiện trong bảng"
        return None

    def set_trans_label(self, name: str):
        """Tên cột bản dịch theo tiếng đích ("Tiếng Anh", "Tiếng Thái"…)."""
        if getattr(self, "trans_label", None) != name:
            self.trans_label = name
            self.headerDataChanged.emit(Qt.Orientation.Horizontal, C_VI, C_VI)

    def flags(self, idx):
        f = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
        if idx.column() in (C_SONG, C_NODUB, C_SEL):
            return f | Qt.ItemFlag.ItemIsUserCheckable
        if idx.column() not in (C_N, C_FIT):
            f |= Qt.ItemFlag.ItemIsEditable
        return f

    def data(self, idx, role=Qt.ItemDataRole.DisplayRole):
        if not idx.isValid():
            return None
        s = self.segs[idx.row()]
        c = idx.column()
        if role in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.EditRole):
            if c == C_N:
                return idx.row() + 1
            if c == C_START:
                return fmt(s.start)
            if c == C_END:
                return fmt(s.end)
            if c == C_SPK:
                return s.speaker
            if c == C_ZH:
                return s.text
            if c == C_VI:
                return s.trans
            if c == C_VOICE:
                v = s.extra.get("voice", "")
                return v if role == Qt.ItemDataRole.EditRole else (self.voice_names.get(v, v) if v else VOICE_DEFAULT)
            if c == C_FIT and role == Qt.ItemDataRole.DisplayRole:
                return fit_text(s)
            return None
        if role == Qt.ItemDataRole.CheckStateRole:
            if c == C_SONG:
                return Qt.CheckState.Checked if s.extra.get("song") else Qt.CheckState.Unchecked
            if c == C_NODUB:
                return Qt.CheckState.Checked if s.extra.get("nodub") else Qt.CheckState.Unchecked
            if c == C_SEL:
                return Qt.CheckState.Checked if self.is_picked(idx.row()) else Qt.CheckState.Unchecked
        if role == Qt.ItemDataRole.ForegroundRole:
            if c == C_VOICE:
                return QColor("#FF8A3D" if s.extra.get("voice") else theme.MUTED)   # cam = câu có giọng riêng
            if c in (C_N, C_START, C_END):
                return QColor(theme.MUTED)
            if c == C_SPK:
                return QColor(spk_color(s.speaker))
            if c == C_ZH:
                if s.extra.get("noise"):
                    return QColor("#B98CFF")       # tím = nghi nhạc / tiếng động bị nghe thành lời
                if s.extra.get("asr_repeat"):
                    return QColor("#F28FB0")       # hồng = còn 1 cụm lặp liền nhau / 2 câu liền giống nhau
                if s.extra.get("asr_unsure"):
                    return QColor("#6FC8E8")       # xanh = 2 bộ nghe ra khác nhau, chưa chọn được -> nghe lại
                if s.extra.get("from_asr"):
                    return QColor("#E8A87C")       # cam nhạt = OCR + nghe: câu phụ đề không có, bộ nghe thêm vào
                return QColor("#E8C26A" if s.extra.get("src_unsure") else "#C3CAD6")   # vàng = AI sửa mà chưa chắc
            if c == C_FIT:
                return QColor(FIT_COLOR.get(s.extra.get("fit"), theme.MUTED))
        if role == Qt.ItemDataRole.BackgroundRole:
            if idx.row() == self.active:
                return QColor("#2B1E22")
            if c == C_VI and not s.trans.strip():
                return QColor("#2A2015")
        if role == Qt.ItemDataRole.FontRole and c == C_VI:
            f = theme.font(10.2, QFont.Weight.Medium)
            return f
        if role == Qt.ItemDataRole.ToolTipRole:
            if c == C_VI:
                cps = len(s.trans) / max(0.05, s.duration)
                tip = (f"Gốc: {s.text}\n" if s.text else "") + f"{s.duration:.1f}s · {cps:.0f} ký tự/giây" + \
                    (" – hơi nhanh, khó đọc kịp" if cps > 20 else "")
                if s.extra.get("review_note") and s.extra.get("trans_before_review") is not None:
                    tip += f"\nAI kiểm tra đã sửa: {s.extra['review_note']}\nTrước khi sửa: {s.extra['trans_before_review']}"
                return tip
            if c == C_ZH and s.extra.get("noise"):
                return f"⚠ Nghi nghe nhầm: {s.extra['noise']}\nNghe lại câu này – đúng là nhạc / tiếng động thì Xoá"
            if c == C_ZH and s.extra.get("asr_repeat"):
                return f"⚠ Nghi bộ nghe lặp lời: \"{s.extra['asr_repeat']}\" lặp liền nhau (hoặc câu bên cạnh gần giống " \
                       "hệt). Nghe lại – nói 1 lần thì sửa chữ / Xoá câu thừa; hoặc tab Phụ đề → Công cụ → Lời hát & " \
                       "nghe nhầm → 'Dọn câu bịa / câu lặp'"
            if c == C_ZH and s.extra.get("ai_song") and s.extra.get("song"):
                return f"🎵 AI: lời bài hát – {s.extra['ai_song']}\nKhông lồng tiếng câu này. Là lời thoại thật: tích rồi " \
                       "bấm 'Không phải nhạc'"
            if c == C_ZH and (s.extra.get("asr_alt") is not None or s.extra.get("from_asr") or s.extra.get("name_fix")):
                return asr_tip(s)
            if c == C_ZH and (s.extra.get("src_before") or s.extra.get("src_unsure")):
                tip = f"AI sửa lỗi nghe – trước: {s.extra['src_before']}" if s.extra.get("src_before") else ""
                if s.extra.get("src_unsure"):
                    tip += ("\n" if tip else "") + f"Chưa chắc: {s.extra['src_unsure']}"
                return tip
            if c == C_SONG:
                return "Câu đang hát – khi lồng tiếng sẽ giữ nguyên giọng hát gốc"
            if c == C_NODUB:
                return "Không lồng tiếng câu này"
            if c == C_SEL:
                return "Tích để đổi giọng / tạo lại giọng riêng câu này"
            if c == C_VOICE:
                return "Giọng riêng của câu này (bấm đúp để đổi). Để trống = theo giọng của người nói"
            if c == C_FIT:
                return fit_tip(s)
        if role == Qt.ItemDataRole.TextAlignmentRole and c in (C_N, C_START, C_END, C_SPK, C_FIT, C_SEL):
            return int(Qt.AlignmentFlag.AlignCenter)
        return None

    def setData(self, idx, value, role=Qt.ItemDataRole.EditRole):
        if not idx.isValid():
            return False
        s = self.segs[idx.row()]
        c = idx.column()
        if role == Qt.ItemDataRole.CheckStateRole:
            on = Qt.CheckState(value) == Qt.CheckState.Checked
            if c == C_SEL:
                self.set_picked([idx.row()], on)
                return True
            s.extra["song" if c == C_SONG else "nodub"] = on
        elif role == Qt.ItemDataRole.EditRole:
            v = str(value)
            if c in (C_START, C_END):
                t = parse_time(v)
                if c == C_START:
                    s.start = max(0.0, t)
                else:
                    s.end = max(s.start + 0.05, t)
            elif c == C_SPK:
                v = v.strip().upper()
                s.speaker = v if not v or v.startswith("S") else "S" + v
            elif c == C_ZH:
                s.text = " ".join(v.split())
                s.extra.pop("src_unsure", None)   # người dùng đã xem / sửa
                s.extra.pop("asr_unsure", None)
                s.extra.pop("asr_repeat", None)
            elif c == C_VI:
                s.trans = " ".join(v.split())
                s.extra.pop("fit", None)   # bản dịch đổi -> cần tạo lại bản lồng để đo lại
                s.extra["trans_user"] = True   # người dùng tự sửa: AI không tự rút gọn câu này
            elif c == C_VOICE:
                if v.strip():
                    s.extra["voice"] = v.strip()
                else:
                    s.extra.pop("voice", None)
        else:
            return False
        self.dataChanged.emit(self.index(idx.row(), 0), self.index(idx.row(), len(COLS) - 1))
        self.edited.emit()
        return True

    def set_active(self, row: int):
        if row != self.active:
            old = self.active
            self.active = row
            for r in (old, row):
                if 0 <= r < len(self.segs):
                    self.dataChanged.emit(self.index(r, 0), self.index(r, len(COLS) - 1))


class SubsFilter(QSortFilterProxyModel):
    def __init__(self):
        super().__init__()
        self.q = ""

    def set_query(self, q: str):
        self.q = q.lower().strip()
        self.invalidateFilter()

    def filterAcceptsRow(self, row, parent):
        if not self.q:
            return True
        m = self.sourceModel()
        s = m.segs[row]
        return self.q in s.text.lower() or self.q in s.trans.lower() or self.q == s.speaker.lower()


class SpeakerDelegate(QStyledItemDelegate):
    """Vẽ nhãn người nói dạng viên thuốc màu."""

    def paint(self, p: QPainter, opt, idx):
        spk = idx.data()
        super().paint(p, opt, idx) if not spk else None
        if not spk:
            return
        p.save()
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        col = QColor(spk_color(spk))
        r = QRectF(opt.rect).adjusted(6, 7, -6, -7)
        bg = QColor(col)
        bg.setAlpha(40)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(bg)
        p.drawRoundedRect(r, r.height() / 2, r.height() / 2)
        p.setPen(col)
        p.setFont(theme.font(9, QFont.Weight.Bold))
        p.drawText(r, Qt.AlignmentFlag.AlignCenter, spk)
        p.restore()


class WrapDelegate(QStyledItemDelegate):
    """Cột chữ xuống dòng (Tiếng Trung / Tiếng Việt): đo chiều cao theo ĐÚNG bề rộng chữ còn lại sau phần đệm của ô
    (stylesheet `QTableView::item { padding: 4px 6px }`) – cách đo mặc định của Qt bỏ qua phần đệm nên câu dài bị
    thiếu 1 hàng, hiện "…"."""
    PAD_W, PAD_H = 16, 12

    def sizeHint(self, opt, idx):
        from PySide6.QtCore import QRect, QSize
        from PySide6.QtGui import QFontMetrics

        base = super().sizeHint(opt, idx)
        text = idx.data(Qt.ItemDataRole.DisplayRole)
        view = self.parent()
        w = (view.columnWidth(idx.column()) if view is not None else opt.rect.width()) - self.PAD_W
        if not text or w <= 20:
            return base
        o = type(opt)(opt)
        self.initStyleOption(o, idx)
        h = QFontMetrics(o.font).boundingRect(QRect(0, 0, w, 100000), int(Qt.TextFlag.TextWordWrap), str(text)).height()
        return QSize(base.width(), max(base.height(), h + self.PAD_H))


class VoiceDelegate(QStyledItemDelegate):
    """Ô "Giọng" của từng câu: chọn trong danh sách giọng (mục đầu = theo người nói)."""

    def __init__(self, parent, voices_fn):
        super().__init__(parent)
        self.voices_fn = voices_fn   # () -> [(mã giọng, tên)]

    def createEditor(self, parent, opt, idx):
        from PySide6.QtWidgets import QComboBox

        cb = QComboBox(parent)
        cb.addItem(VOICE_DEFAULT, "")
        for vid, name in self.voices_fn():
            cb.addItem(name, vid)
        return cb

    def setEditorData(self, ed, idx):
        i = ed.findData(idx.data(Qt.ItemDataRole.EditRole) or "")
        ed.setCurrentIndex(max(0, i))
        ed.showPopup()

    def setModelData(self, ed, model, idx):
        model.setData(idx, ed.currentData() or "", Qt.ItemDataRole.EditRole)
