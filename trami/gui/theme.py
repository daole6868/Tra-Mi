"""Màu sắc, font chữ và stylesheet (QSS) cho giao diện Subvideo."""
from __future__ import annotations

from PySide6.QtGui import QColor, QFont, QFontDatabase, QLinearGradient

from ..config import FONTS_DIR

BG = "#0B0E14"
SIDEBAR = "#0F131B"
CARD = "#141925"
CARD2 = "#1A2030"
CARD3 = "#232B3D"
BORDER = "#252C3A"
BORDER2 = "#343D50"     # viền ô nhập / nút (đậm hơn viền thẻ 1 bậc), rê chuột sáng thêm
TEXT = "#E8ECF3"
TEXT2 = "#C3CAD6"       # chữ phụ (nhãn công tắc, chữ gốc)
MUTED = "#8E98AC"       # chữ mờ: nhãn ô, mô tả (5,9:1 trên nền thẻ)
ACCENT = "#FF5A4E"
ACCENT2 = "#FF8A3D"
ON_ACCENT = "#1A0B08"   # chữ / icon trên nền màu nhấn (2.2.0: chữ tối dễ đọc hơn chữ trắng – 6,2:1)
OK = "#3CCB7F"
WARN = "#F5B841"
ERR = "#F0525A"
INFO = "#6BA3FF"
RADIUS = 14
GAP = 10          # khe giữa các khung (ngang = dọc) trên trang dự án
PANEL_PAD = 10    # lề trong của mọi khung lớn (công cụ, xem trước, câu đang chọn, timeline)

FONT_FAMILY = "Be Vietnam Pro"


def c(hexstr: str, alpha: int = 255) -> QColor:
    col = QColor(hexstr)
    col.setAlpha(alpha)
    return col


def accent_gradient(x0: float, y0: float, x1: float, y1: float) -> QLinearGradient:
    g = QLinearGradient(x0, y0, x1, y1)
    g.setColorAt(0, QColor(ACCENT))
    g.setColorAt(1, QColor(ACCENT2))
    return g


def load_fonts() -> str:
    global FONT_FAMILY
    fams = set()
    for f in sorted(FONTS_DIR.glob("*.ttf")):
        fid = QFontDatabase.addApplicationFont(str(f))
        if fid >= 0:
            fams.update(QFontDatabase.applicationFontFamilies(fid))
    if fams:
        FONT_FAMILY = "Be Vietnam Pro" if "Be Vietnam Pro" in fams else sorted(fams)[0]
    return FONT_FAMILY


def font(size: float = 10, weight: QFont.Weight = QFont.Weight.Normal) -> QFont:
    f = QFont(FONT_FAMILY)
    f.setPointSizeF(size)
    f.setWeight(weight)
    f.setHintingPreference(QFont.HintingPreference.PreferNoHinting)
    return f


QSS = f"""
* {{ font-family: "{{FONT}}"; color: {TEXT}; }}
QMainWindow, QWidget#Root {{ background: {BG}; }}
QWidget {{ font-size: 10pt; }}
QLabel {{ background: transparent; }}
QLabel[muted="true"] {{ color: {MUTED}; }}
QLabel[h1="true"] {{ font-size: 18pt; font-weight: 800; }}
QLabel[h2="true"] {{ font-size: 12.5pt; font-weight: 700; }}
QLabel[h3="true"] {{ font-size: 10.5pt; font-weight: 700; color: {TEXT}; }}
QLabel[h4="true"] {{ font-size: 10pt; font-weight: 700; color: {TEXT}; }}
QToolTip {{ background: {CARD3}; color: {TEXT}; border: 1px solid {BORDER}; padding: 6px 8px; border-radius: 6px; }}

QLineEdit, QPlainTextEdit, QTextEdit, QSpinBox, QDoubleSpinBox, QComboBox, QTimeEdit, QDateTimeEdit {{
    background: {CARD2}; border: 1px solid {BORDER2}; border-radius: 8px; padding: 6px 10px;
    selection-background-color: {ACCENT}; selection-color: {ON_ACCENT};
}}
QLineEdit:hover, QPlainTextEdit:hover, QTextEdit:hover, QSpinBox:hover, QDoubleSpinBox:hover, QComboBox:hover {{
    border-color: #48526A; }}
QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox, QTimeEdit, QDateTimeEdit {{ min-height: 20px; }}
QLineEdit:focus, QPlainTextEdit:focus, QTextEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus {{
    border: 1px solid {ACCENT2}; background: #1C2232;
}}
QLineEdit:disabled, QComboBox:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled, QDateTimeEdit:disabled {{
    color: {MUTED}; }}
QDateTimeEdit::drop-down {{ border: none; width: 26px; }}
QDateTimeEdit::down-arrow {{ image: url("{{CHEV}}"); width: 14px; height: 14px; margin-right: 10px; }}
QComboBox::drop-down {{ border: none; width: 26px; }}
QComboBox::down-arrow {{ image: url("{{CHEV}}"); width: 14px; height: 14px; margin-right: 10px; }}
QComboBox QAbstractItemView {{ background: {CARD2}; border: 1px solid {BORDER}; border-radius: 8px;
    selection-background-color: {CARD3}; outline: 0; padding: 4px; }}
QSpinBox::up-button, QSpinBox::down-button, QDoubleSpinBox::up-button, QDoubleSpinBox::down-button {{ width: 0; border: none; }}

QPushButton {{ background: #181E2C; border: 1px solid {BORDER2}; border-radius: 9px; padding: 6px 12px;
    font-weight: 500; }}
QPushButton:hover {{ background: {CARD3}; border-color: #4A5470; }}
QPushButton:pressed {{ background: #2C3547; border-color: {ACCENT2}; }}
QPushButton:disabled {{ color: {MUTED}; border-color: {BORDER}; }}
QPushButton[danger="true"] {{ color: #FF9EA3; border-color: #5A2B33; }}
QPushButton[danger="true"]:hover {{ background: #2A1A20; border-color: {ERR}; }}
QPushButton[flat="true"] {{ background: transparent; border: 1px solid {BORDER2}; border-radius: 8px; padding: 5px 10px; }}
QPushButton[flat="true"]:hover {{ background: {CARD2}; border-color: #4A5470; }}
QPushButton[flat="true"]:pressed {{ background: #2C3547; }}

QCheckBox {{ spacing: 8px; background: transparent; }}
QCheckBox::indicator {{ width: 18px; height: 18px; border-radius: 5px; border: 1.5px solid #4A5468; background: {CARD2}; }}
QCheckBox::indicator:hover {{ border-color: #7A8499; }}
QCheckBox::indicator:checked {{ background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 {ACCENT}, stop:1 {ACCENT2});
    border-color: {ACCENT}; image: url("{{CHECK}}"); }}

QSlider::groove:horizontal {{ height: 5px; background: {CARD3}; border-radius: 2px; }}
QSlider::sub-page:horizontal {{ background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 {ACCENT}, stop:1 {ACCENT2});
    border-radius: 2px; }}
QSlider::handle:horizontal {{ background: #FFFFFF; width: 14px; height: 14px; margin: -5px 0; border-radius: 7px;
    border: 3px solid #3A2420; }}
QSlider::handle:horizontal:hover {{ border-color: #6A3A2E; }}

QScrollArea {{ background: transparent; border: none; }}
QScrollArea > QWidget > QWidget {{ background: transparent; }}
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: #232A39; border-radius: 4px; min-height: 30px; }}
QScrollBar::handle:vertical:hover {{ background: #3A4458; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
QScrollBar::handle:horizontal {{ background: {CARD3}; border-radius: 4px; min-width: 30px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

QTableView {{ background: {SIDEBAR}; border: 1px solid {BORDER}; border-radius: 12px; gridline-color: transparent;
    selection-background-color: #4A2A24; selection-color: {TEXT}; alternate-background-color: #121722; }}
QTableView::item {{ padding: 4px 6px; border-bottom: 1px solid #1B2230; }}
QTableView::item:hover {{ background: #1A2030; }}
QTableView::item:selected {{ background: #4A2A24; color: #FFFFFF; border-top: 1px solid {ACCENT2};
    border-bottom: 1px solid {ACCENT2}; }}
QTableView::indicator {{ width: 17px; height: 17px; border-radius: 5px; border: 1.5px solid #4A5468; background: {CARD2}; }}
QTableView::indicator:checked {{ background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 {ACCENT}, stop:1 {ACCENT2});
    border-color: {ACCENT}; image: url("{{CHECK}}"); }}
QHeaderView::section {{ background: {CARD}; color: {MUTED}; border: none; border-bottom: 1px solid {BORDER};
    padding: 7px 6px; font-weight: 600; font-size: 8.6pt; }}
QTableCornerButton::section {{ background: {CARD}; border: none; }}

QSplitter::handle {{ background: transparent; }}
QMenu {{ background: {CARD2}; border: 1px solid {BORDER}; border-radius: 8px; padding: 5px; }}
QMenu::item {{ padding: 6px 18px; border-radius: 6px; }}
QMenu::item:selected {{ background: {CARD3}; color: #FFFFFF; }}
QMessageBox {{ background: {CARD}; }}
QDialog {{ background: {BG}; }}
"""


def _icon_file(name: str, color: str) -> str:
    """Vẽ icon bằng QPainter ra file PNG để dùng trong stylesheet."""
    from ..config import CACHE_DIR
    from . import icons

    out = CACHE_DIR / f"ui_{name}_{color.strip('#')}.png"
    if not out.exists():
        icons.pixmap(name, color, 16, 2.0).save(str(out))
    return out.as_posix()


def stylesheet() -> str:
    return (QSS.replace("{FONT}", FONT_FAMILY).replace("{CHEV}", _icon_file("chev_down", MUTED))
            .replace("{CHECK}", _icon_file("check", ON_ACCENT)))
