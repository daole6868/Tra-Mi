"""Bảng phụ đề (model + delegate) cho QTableView."""
from __future__ import annotations

from PySide6.QtCore import QAbstractTableModel, QModelIndex, QRectF, QSortFilterProxyModel, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter
from PySide6.QtWidgets import QStyledItemDelegate

from ..subtitle import Segment, parse_time
from . import theme
from .player import fmt

COLS = ["#", "Bắt đầu", "Kết thúc", "Người", "Tiếng Trung", "Tiếng Việt", "🎵", "Bỏ lồng"]
C_N, C_START, C_END, C_SPK, C_ZH, C_VI, C_SONG, C_NODUB = range(8)
SPK_COLORS = ["#FF8A3D", "#5B8CFF", "#3CCB7F", "#C07BFF", "#F5B841", "#FF5FA2", "#3BD1D1", "#B5C44B"]


def spk_color(spk: str) -> str:
    try:
        return SPK_COLORS[(int(spk.lstrip("S")) - 1) % len(SPK_COLORS)]
    except Exception:
        return theme.MUTED


class SubsModel(QAbstractTableModel):
    edited = Signal()

    def __init__(self, segs: list[Segment] | None = None):
        super().__init__()
        self.segs: list[Segment] = segs or []
        self.active = -1

    def set_segments(self, segs: list[Segment]):
        self.beginResetModel()
        self.segs = segs
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.segs)

    def columnCount(self, parent=QModelIndex()):
        return len(COLS)

    def headerData(self, section, orient, role=Qt.ItemDataRole.DisplayRole):
        if orient == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return COLS[section]
        return None

    def flags(self, idx):
        f = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
        if idx.column() in (C_SONG, C_NODUB):
            return f | Qt.ItemFlag.ItemIsUserCheckable
        if idx.column() != C_N:
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
            return None
        if role == Qt.ItemDataRole.CheckStateRole:
            if c == C_SONG:
                return Qt.CheckState.Checked if s.extra.get("song") else Qt.CheckState.Unchecked
            if c == C_NODUB:
                return Qt.CheckState.Checked if s.extra.get("nodub") else Qt.CheckState.Unchecked
        if role == Qt.ItemDataRole.ForegroundRole:
            if c in (C_N, C_START, C_END):
                return QColor(theme.MUTED)
            if c == C_SPK:
                return QColor(spk_color(s.speaker))
            if c == C_ZH:
                return QColor("#C3CAD6")
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
                return f"{s.duration:.1f}s · {cps:.0f} ký tự/giây" + (" – hơi nhanh, khó đọc kịp" if cps > 20 else "")
            if c == C_SONG:
                return "Câu đang hát – khi lồng tiếng sẽ giữ nguyên giọng hát gốc"
            if c == C_NODUB:
                return "Không lồng tiếng câu này"
        if role == Qt.ItemDataRole.TextAlignmentRole and c in (C_N, C_START, C_END, C_SPK):
            return int(Qt.AlignmentFlag.AlignCenter)
        return None

    def setData(self, idx, value, role=Qt.ItemDataRole.EditRole):
        if not idx.isValid():
            return False
        s = self.segs[idx.row()]
        c = idx.column()
        if role == Qt.ItemDataRole.CheckStateRole:
            on = Qt.CheckState(value) == Qt.CheckState.Checked
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
            elif c == C_VI:
                s.trans = " ".join(v.split())
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
