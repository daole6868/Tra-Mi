"""Màu sắc, font chữ và stylesheet (QSS) cho giao diện TraMi."""
from __future__ import annotations

from PySide6.QtGui import QColor, QFont, QFontDatabase, QLinearGradient

from ..config import FONTS_DIR

BG = "#0E1117"
SIDEBAR = "#121722"
CARD = "#171D29"
CARD2 = "#1E2533"
CARD3 = "#252D3D"
BORDER = "#283042"
TEXT = "#E9EDF4"
MUTED = "#8B95A8"
ACCENT = "#FF5A4E"
ACCENT2 = "#FF8A3D"
OK = "#3CCB7F"
WARN = "#F5B841"
ERR = "#F0525A"
INFO = "#5B8CFF"
RADIUS = 12

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
QToolTip {{ background: {CARD3}; color: {TEXT}; border: 1px solid {BORDER}; padding: 6px 8px; border-radius: 6px; }}

QLineEdit, QPlainTextEdit, QTextEdit, QSpinBox, QDoubleSpinBox, QComboBox, QTimeEdit {{
    background: #0F141D; border: 1px solid {BORDER}; border-radius: 9px; padding: 7px 10px;
    selection-background-color: {ACCENT}; min-height: 20px;
}}
QLineEdit:focus, QPlainTextEdit:focus, QTextEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus {{
    border: 1px solid {ACCENT2};
}}
QLineEdit:disabled, QComboBox:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled {{ color: {MUTED}; }}
QComboBox::drop-down {{ border: none; width: 26px; }}
QComboBox::down-arrow {{ image: url("{{CHEV}}"); width: 14px; height: 14px; margin-right: 10px; }}
QComboBox QAbstractItemView {{ background: {CARD2}; border: 1px solid {BORDER}; border-radius: 8px;
    selection-background-color: {CARD3}; outline: 0; padding: 4px; }}
QSpinBox::up-button, QSpinBox::down-button, QDoubleSpinBox::up-button, QDoubleSpinBox::down-button {{ width: 0; border: none; }}

QPushButton {{ background: {CARD2}; border: 1px solid {BORDER}; border-radius: 9px; padding: 7px 14px; }}
QPushButton:hover {{ background: {CARD3}; border-color: #3A4458; }}
QPushButton:pressed {{ background: #2C3547; }}
QPushButton:disabled {{ color: {MUTED}; }}
QPushButton[danger="true"] {{ color: {ERR}; }}
QPushButton[flat="true"] {{ background: transparent; border: none; padding: 4px 8px; }}
QPushButton[flat="true"]:hover {{ background: {CARD2}; }}

QCheckBox {{ spacing: 8px; background: transparent; }}
QCheckBox::indicator {{ width: 18px; height: 18px; border-radius: 5px; border: 1px solid {BORDER}; background: #0F141D; }}
QCheckBox::indicator:checked {{ background: {ACCENT}; border-color: {ACCENT}; image: url("{{CHECK}}"); }}

QSlider::groove:horizontal {{ height: 5px; background: {CARD3}; border-radius: 2px; }}
QSlider::sub-page:horizontal {{ background: {ACCENT}; border-radius: 2px; }}
QSlider::handle:horizontal {{ background: #FFFFFF; width: 14px; height: 14px; margin: -5px 0; border-radius: 7px; }}

QScrollArea {{ background: transparent; border: none; }}
QScrollArea > QWidget > QWidget {{ background: transparent; }}
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: {CARD3}; border-radius: 4px; min-height: 30px; }}
QScrollBar::handle:vertical:hover {{ background: #3A4458; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
QScrollBar::handle:horizontal {{ background: {CARD3}; border-radius: 4px; min-width: 30px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

QTableView {{ background: #10151E; border: 1px solid {BORDER}; border-radius: 10px; gridline-color: transparent;
    selection-background-color: #2A2230; selection-color: {TEXT}; alternate-background-color: #131924; }}
QTableView::item {{ padding: 4px 6px; border-bottom: 1px solid #1B2230; }}
QTableView::indicator {{ width: 17px; height: 17px; border-radius: 5px; border: 1px solid #3A4458; background: #0F141D; }}
QTableView::indicator:checked {{ background: {ACCENT}; border-color: {ACCENT}; image: url("{{CHECK}}"); }}
QHeaderView::section {{ background: {CARD2}; color: {MUTED}; border: none; padding: 7px 6px; font-weight: 600; }}
QTableCornerButton::section {{ background: {CARD2}; border: none; }}

QSplitter::handle {{ background: transparent; width: 8px; }}
QMenu {{ background: {CARD2}; border: 1px solid {BORDER}; border-radius: 8px; padding: 5px; }}
QMenu::item {{ padding: 6px 18px; border-radius: 6px; }}
QMenu::item:selected {{ background: {CARD3}; }}
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
            .replace("{CHECK}", _icon_file("check", "#FFFFFF")))
