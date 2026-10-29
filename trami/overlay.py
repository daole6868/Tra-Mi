"""Lớp phủ trên video: CHỮ (tiêu đề nền cọ, hộp bo góc, bong bóng thoại…) và LOGO (PNG / JPG / WebP / SVG / GIF động).

Mỗi lớp là 1 dict trong edit["layers"] (thứ tự trong danh sách = thứ tự chồng, cuối danh sách nằm trên cùng):
  chung: id, kind ("text" | "logo"), name, start / end (giây VIDEO GỐC – cắt / đổi tốc độ thì lớp tự dời theo; end 0 =
         tới hết video), x / y (tâm lớp, 0..1 theo khung xuất), rot (độ), opacity, anim, fade_in / fade_out (giây), hidden
  chữ:  text (nhiều dòng), font, bold, italic, size (cỡ chữ × cạnh ngắn khung), color, outline (× cỡ chữ),
        outline_color, shadow (× cỡ chữ), shadow_color, bg (kiểu nền), bg_color, bg_opacity, pad (× cỡ chữ),
        radius (bo góc, × chiều cao nền), align, line_h, spacing (giãn chữ, %)
  logo: path, w (bề rộng × bề rộng khung)

Ảnh của lớp vẽ bằng Qt (QPainter) – CÙNG 1 hàm cho khung xem trước (trình phát) và video xuất (ảnh PNG đè bằng FFmpeg
overlay) -> chữ / nền / viền / bóng giống hệt nhau ở 2 nơi. GIF động khi xuất do FFmpeg giải mã từng khung.
"""
from __future__ import annotations

import hashlib
import json
import math
import random
import shutil
import threading
import uuid
from pathlib import Path

from .config import DATA_DIR, FONTS_DIR

USER_FONTS_DIR = DATA_DIR / "fonts"
LOGOS_DIR = DATA_DIR / "logos"

# font có đủ chữ tiếng Việt (đi kèm tool, giấy phép OFL – fonts/Fonts-more-OFL.txt)
FONTS = [("Be Vietnam Pro", "Be Vietnam Pro – gọn, rõ"), ("Anton", "Anton – đậm, cao (tiêu đề)"),
         ("Paytone One", "Paytone One – tròn, đậm"), ("Barlow Condensed ExtraBold", "Barlow Condensed – hẹp, đậm"),
         ("Chakra Petch", "Chakra Petch – góc cạnh, game"), ("Bungee", "Bungee – khối, biển hiệu"),
         ("Baloo 2", "Baloo 2 – tròn, dễ thương"), ("Bangers", "Bangers – truyện tranh"),
         ("Lobster", "Lobster – nét liền cổ điển"), ("Pacifico", "Pacifico – viết tay mềm"),
         ("Dancing Script", "Dancing Script – viết tay"), ("Pattaya", "Pattaya – mềm, vui"),
         ("Patrick Hand", "Patrick Hand – chữ viết tay")]
BG_STYLES = [("none", "Không nền"), ("brush", "Nét cọ"), ("round", "Hộp bo góc"), ("box", "Hộp vuông"),
             ("bubble", "Bong bóng thoại"), ("underline", "Gạch chân màu"), ("circle", "Hình tròn")]
ANIMS = [("none", "Không"), ("fade", "Hiện dần / mờ dần"), ("slide_l", "Trượt từ phải vào"),
         ("slide_r", "Trượt từ trái vào"), ("slide_u", "Trượt từ dưới lên"), ("slide_d", "Trượt từ trên xuống"),
         ("scroll", "Chữ chạy ngang (lặp lại)")]
ALIGNS = [("center", "Giữa"), ("left", "Trái"), ("right", "Phải")]
IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".webp", ".svg", ".gif", ".bmp")
SLIDE_DUR = 0.5            # giây trượt vào khi không đặt "hiện dần"
SCROLL_SPEED = 1 / 8       # chữ chạy: bề rộng khung / 8 mỗi giây (như chữ chạy cũ)

COMMON = {"name": "", "start": 0.0, "end": 0.0, "x": 0.5, "y": 0.12, "rot": 0.0, "opacity": 1.0, "anim": "none",
          "fade_in": 0.0, "fade_out": 0.0, "hidden": False}
TEXT_DEFAULT = {**COMMON, "kind": "text", "text": "Nhập chữ…", "font": "Be Vietnam Pro", "bold": True,
                "italic": False, "size": 0.06, "color": "#FFFFFF", "outline": 0.08, "outline_color": "#000000",
                "shadow": 0.0, "shadow_color": "#000000", "bg": "none", "bg_color": "#000000", "bg_opacity": 0.7,
                "pad": 0.35, "radius": 0.3, "align": "center", "line_h": 1.12, "spacing": 0}
LOGO_DEFAULT = {**COMMON, "kind": "logo", "path": "", "w": 0.16, "x": 0.88, "y": 0.1}

# mẫu chữ: (mã, tên, thuộc tính)
TEMPLATES = [
    ("brush_yellow", "Tiêu đề cọ vàng", dict(text="TIÊU ĐỀ VIDEO", font="Paytone One", bold=False, size=0.075,
                                              color="#FFFFFF", outline=0.13, outline_color="#000000", shadow=0.06,
                                              bg="brush", bg_color="#FFD21F", bg_opacity=1.0, pad=0.42, y=0.14)),
    ("black_round", "Nền đen bo góc", dict(text="Ghi chú cho video", font="Be Vietnam Pro", size=0.045,
                                            color="#FFFFFF", outline=0.0, bg="round", bg_color="#000000",
                                            bg_opacity=0.65, pad=0.45, radius=0.35, y=0.1)),
    ("red_white", "Viền trắng chữ đỏ", dict(text="CẢNH BÁO", font="Anton", bold=False, size=0.08, color="#E8202A",
                                             outline=0.14, outline_color="#FFFFFF", shadow=0.05, y=0.15)),
    ("bubble", "Bong bóng thoại", dict(text="Xem đến cuối nhé!", font="Baloo 2", size=0.05, color="#111111",
                                       outline=0.0, bg="bubble", bg_color="#FFFFFF", bg_opacity=1.0, pad=0.5,
                                       radius=0.45, x=0.3, y=0.22)),
    ("underline", "Gạch chân màu", dict(text="Điểm nhấn", font="Be Vietnam Pro", size=0.06, color="#FFFFFF",
                                        outline=0.0, shadow=0.05, bg="underline", bg_color="#FF6A2B", bg_opacity=1.0,
                                        y=0.14)),
    ("outline", "Không nền – viền đậm", dict(text="CHỮ VIỀN ĐẬM", font="Be Vietnam Pro", size=0.065,
                                             color="#FFFFFF", outline=0.16, outline_color="#000000", shadow=0.0,
                                             y=0.14)),
    ("yellow_title", "Chữ vàng viền đen", dict(text="TẬP 1", font="Bangers", bold=False, size=0.09,
                                               color="#FFE14D", outline=0.12, outline_color="#000000", shadow=0.07,
                                               y=0.16)),
    ("marquee", "Chữ chạy ngang", dict(text="Cảm ơn các bạn đã xem – nhớ đăng ký kênh nhé!", font="Be Vietnam Pro",
                                       size=0.04, color="#FFFFFF", outline=0.0, bg="box", bg_color="#000000",
                                       bg_opacity=0.55, pad=0.35, anim="scroll", y=0.94)),
]
# logo mẫu = hình + tên kênh ({kênh} thay bằng tên kênh, {tắt} = chữ cái đầu)
LOGO_TEMPLATES = [
    ("ch_red", "Thẻ đỏ tên kênh", dict(text="▶ {kênh}", font="Be Vietnam Pro", size=0.04, color="#FFFFFF",
                                         outline=0.0, bg="round", bg_color="#E53935", bg_opacity=1.0, pad=0.45,
                                         radius=0.3, x=0.86, y=0.07)),
    ("ch_at", "@kênh nền mờ", dict(text="@{kênh}", font="Be Vietnam Pro", size=0.035, color="#FFFFFF", outline=0.0,
                                   bg="round", bg_color="#000000", bg_opacity=0.5, pad=0.4, radius=0.5, x=0.86,
                                   y=0.07, opacity=0.9)),
    ("ch_circle", "Huy hiệu tròn", dict(text="{tắt}", font="Paytone One", bold=False, size=0.05, color="#FFFFFF",
                                         outline=0.0, bg="circle", bg_color="#FF6A2B", bg_opacity=1.0, pad=0.45,
                                         x=0.92, y=0.08)),
    ("ch_brush", "Nét cọ tên kênh", dict(text="{kênh}", font="Paytone One", bold=False, size=0.04, color="#111111",
                                          outline=0.0, bg="brush", bg_color="#FFD21F", bg_opacity=1.0, pad=0.4,
                                          x=0.86, y=0.07)),
    ("ch_sub", "Mời đăng ký kênh", dict(text="Đăng ký {kênh} nhé!", font="Baloo 2", size=0.04, color="#E53935",
                                         outline=0.0, bg="bubble", bg_color="#FFFFFF", bg_opacity=1.0, pad=0.5,
                                         radius=0.45, x=0.78, y=0.86)),
]


def _uid() -> str:
    return uuid.uuid4().hex[:8]


def new_layer(kind: str = "text", template: str | None = None, channel: str = "", **kw) -> dict:
    """Lớp mới (chữ / logo) – template: mã trong TEMPLATES / LOGO_TEMPLATES; channel: tên kênh cho logo mẫu."""
    base = dict(LOGO_DEFAULT if kind == "logo" else TEXT_DEFAULT)
    if template:
        for code, name, props in TEMPLATES + LOGO_TEMPLATES:
            if code == template:
                base.update(props)
                base["name"] = name
                if "{" in base.get("text", ""):
                    ch = (channel or "Tên kênh").strip()
                    initials = "".join(w[0] for w in ch.replace("@", "").split()[:2]).upper() or "K"
                    base["text"] = base["text"].replace("{kênh}", ch).replace("{tắt}", initials)
                break
    base.update(kw)
    base["id"] = _uid()
    if not base.get("name"):
        base["name"] = Path(base.get("path", "")).stem if kind == "logo" else "Chữ"
    return base


def text_name(layer: dict) -> str:
    """Tên hiển thị của lớp (dòng timeline / danh sách)."""
    if layer.get("kind") == "logo":
        return "Logo · " + (layer.get("name") or Path(layer.get("path", "")).stem or "ảnh")
    t = " ".join((layer.get("text") or "").split())
    return "Chữ · " + (t[:28] + "…" if len(t) > 29 else t or "(trống)")


def migrate(edit: dict) -> bool:
    """Dự án cũ: chữ chạy / chữ tĩnh (edit["text"]) và logo (edit["watermark"]) -> lớp phủ. Trả về True nếu có đổi."""
    layers = edit.setdefault("layers", [])
    changed = False
    wm = edit.get("watermark") or {}
    if wm.get("path"):
        pos = wm.get("position", "top-right")
        sc = float(wm.get("scale", 0.15) or 0.15)
        m = 0.03
        x = {"top-left": m + sc / 2, "bottom-left": m + sc / 2, "top-right": 1 - m - sc / 2,
             "bottom-right": 1 - m - sc / 2}.get(pos, 0.5)
        y = 0.08 if pos.startswith("top") else 0.92 if pos.startswith("bottom") else 0.5
        layers.insert(0, new_layer("logo", path=wm["path"], w=sc, x=x, y=y,
                                   opacity=float(wm.get("opacity", 0.85) or 0.85)))
        edit["watermark"] = {**wm, "path": ""}
        changed = True
    tx = edit.get("text") or {}
    if (tx.get("content") or "").strip():
        pos = tx.get("position", "top-left")
        L = new_layer("text", text=" ".join(tx["content"].split()), color=tx.get("color", "#FFFFFF"), size=0.04,
                      outline=0.06, shadow=0.04, bold=True)
        if pos == "scroll":
            L.update(anim="scroll", y=0.06)
        else:
            # tâm lớp = mép chữ cũ ± nửa bề rộng chữ (ước theo số ký tự, khung 16:9)
            half = min(0.45, len(L["text"]) * 0.5 * float(L["size"]) * 9 / 16 / 2 + 0.01)
            L["x"] = round(0.03 + half, 4) if pos in ("top-left", "bottom-left") else \
                round(0.97 - half, 4) if pos in ("top-right", "bottom-right") else 0.5
            L["y"] = 0.06 if pos.startswith("top") else 0.94 if pos.startswith("bottom") else 0.5
        layers.append(L)
        edit["text"] = {**tx, "content": ""}
        changed = True
    return changed


# ------------------------------------------------------------------ thời gian
def src_span(layer: dict, duration: float) -> tuple[float, float]:
    """(đầu, cuối) theo giây video gốc; cuối 0 = tới hết video."""
    a = max(0.0, float(layer.get("start") or 0))
    b = float(layer.get("end") or 0) or duration
    return a, (min(b, duration) if duration else b)


def out_span(layer: dict, duration: float, keep: list, speed: float = 1.0) -> tuple[float, float] | None:
    """(đầu, cuối) trên video XUẤT RA (sau cắt / đổi tốc độ) – None nếu lớp nằm hết trong đoạn đã cắt."""
    from .video import out_time

    a, b = src_span(layer, duration)
    A, B = out_time(a, keep, speed), out_time(b, keep, speed)
    return (A, B) if B - A >= 0.05 else None


def slide_offset(anim: str, p: float, TW: int, TH: int, w: float, h: float) -> tuple[float, float]:
    """Độ dời khi trượt vào (p = tiến độ 0..1, chậm dần về cuối)."""
    k = (1 - min(1.0, max(0.0, p))) ** 2
    if anim == "slide_l":
        return k * (TW * 0.5 + w), 0.0
    if anim == "slide_r":
        return -k * (TW * 0.5 + w), 0.0
    if anim == "slide_u":
        return 0.0, k * (TH * 0.35 + h)
    if anim == "slide_d":
        return 0.0, -k * (TH * 0.35 + h)
    return 0.0, 0.0


def anim_state(layer: dict, t_rel: float, length: float, TW: int, TH: int, w: float, h: float,
               left: float) -> tuple[float, float, float]:
    """Trạng thái lúc t_rel giây kể từ khi lớp hiện (đã tính tốc độ): (mép trái x, dời y, độ hiện 0..1) theo điểm ảnh
    khung xuất – dùng cho khung xem trước, công thức giống hệt biểu thức FFmpeg khi xuất (`ffmpeg_xy`, `fades`)."""
    anim = layer.get("anim") or "none"
    fi, fo = fades(layer)
    alpha = 1.0
    if fi > 0:
        alpha = min(alpha, max(0.0, t_rel / fi))
    if fo > 0:
        alpha = min(alpha, max(0.0, (length - t_rel) / fo))
    x, dy = left, 0.0
    if anim == "scroll":
        x = TW - (t_rel * TW * SCROLL_SPEED) % (TW + w)
    elif anim.startswith("slide"):
        dx, dy = slide_offset(anim, t_rel / (float(layer.get("fade_in") or 0) or SLIDE_DUR), TW, TH, w, h)
        x += dx
    return x, dy, alpha


def ffmpeg_xy(layer: dict, A: float, X: float, Y: float, TW: int, TH: int, w: int, h: int) -> tuple[str, str]:
    """Biểu thức x / y cho overlay (t = giây video xuất, A = lúc lớp hiện) – cùng công thức với anim_state."""
    anim = layer.get("anim") or "none"
    if anim == "scroll":
        v = TW * SCROLL_SPEED
        return f"{TW}-mod((t-{A:.3f})*{v:.3f}\\,{TW + w})", f"{Y:.1f}"
    if anim.startswith("slide"):
        d = float(layer.get("fade_in") or 0) or SLIDE_DUR
        k = f"pow(1-min(1\\,max(0\\,(t-{A:.3f})/{d:.3f}))\\,2)"
        if anim == "slide_l":
            return f"{X:.1f}+{TW * 0.5 + w:.1f}*{k}", f"{Y:.1f}"
        if anim == "slide_r":
            return f"{X:.1f}-{TW * 0.5 + w:.1f}*{k}", f"{Y:.1f}"
        if anim == "slide_u":
            return f"{X:.1f}", f"{Y:.1f}+{TH * 0.35 + h:.1f}*{k}"
        return f"{X:.1f}", f"{Y:.1f}-{TH * 0.35 + h:.1f}*{k}"
    return f"{X:.1f}", f"{Y:.1f}"


def fades(layer: dict) -> tuple[float, float]:
    fi, fo = float(layer.get("fade_in") or 0), float(layer.get("fade_out") or 0)
    if (layer.get("anim") or "none") == "fade":
        fi, fo = fi or 0.5, fo or 0.5
    return fi, fo


# ------------------------------------------------------------------ Qt: font + vẽ
_qt_lock = threading.Lock()
_fonts_done: set[str] = set()
_app = None


def ensure_qt():
    """Vẽ chữ cần QGuiApplication (dòng lệnh / xuất không mở giao diện -> tạo bản ẩn 'offscreen')."""
    global _app
    from PySide6.QtGui import QGuiApplication

    if QGuiApplication.instance() is None:
        import os
        import sys

        from PySide6.QtWidgets import QApplication   # (không dùng QGuiApplication: giao diện mở sau vẫn dùng được)

        if not os.environ.get("QT_QPA_PLATFORM") and (sys.platform != "win32" and not os.environ.get("DISPLAY")):
            os.environ["QT_QPA_PLATFORM"] = "offscreen"
        _app = QApplication(sys.argv[:1])
    register_fonts()


def register_fonts() -> list[str]:
    """Nạp font đi kèm + font riêng của bạn (data/fonts) vào Qt (1 lần mỗi file)."""
    from PySide6.QtGui import QFontDatabase

    fams = []
    dirs = [FONTS_DIR, USER_FONTS_DIR]
    with _qt_lock:
        for d in dirs:
            if not d.exists():
                continue
            for f in sorted(list(d.glob("*.ttf")) + list(d.glob("*.otf")) + list(d.glob("*.TTF")) + list(d.glob("*.OTF"))):
                key = str(f)
                if key in _fonts_done:
                    continue
                _fonts_done.add(key)
                fid = QFontDatabase.addApplicationFont(key)
                if fid >= 0:
                    fams += QFontDatabase.applicationFontFamilies(fid)
    return fams


def user_fonts() -> list[tuple[str, str]]:
    """Font riêng đã nạp: [(họ font, tên file)]."""
    out = []
    try:
        from PySide6.QtGui import QFontDatabase

        if not USER_FONTS_DIR.exists():
            return out
        for f in sorted(USER_FONTS_DIR.iterdir()):
            if f.suffix.lower() not in (".ttf", ".otf"):
                continue
            fid = QFontDatabase.addApplicationFont(str(f))
            fams = QFontDatabase.applicationFontFamilies(fid) if fid >= 0 else []
            if fams:
                out.append((fams[0], f.name))
    except Exception:  # noqa: BLE001
        pass
    return out


def add_user_font(path: str | Path) -> str:
    """Nạp font riêng (.ttf / .otf): chép vào data/fonts, trả về tên họ font."""
    from PySide6.QtGui import QFontDatabase

    src = Path(path)
    if src.suffix.lower() not in (".ttf", ".otf"):
        raise ValueError("Chỉ nhận file font .ttf hoặc .otf")
    USER_FONTS_DIR.mkdir(parents=True, exist_ok=True)
    dst = USER_FONTS_DIR / src.name
    if src.resolve() != dst.resolve():
        shutil.copy2(src, dst)
    fid = QFontDatabase.addApplicationFont(str(dst))
    fams = QFontDatabase.applicationFontFamilies(fid) if fid >= 0 else []
    if not fams:
        dst.unlink(missing_ok=True)
        raise ValueError(f"File {src.name} không phải font hợp lệ")
    _fonts_done.add(str(dst))
    return fams[0]


def _qfont(layer: dict, px: float):
    from PySide6.QtGui import QFont

    f = QFont(layer.get("font") or "Be Vietnam Pro")
    f.setPixelSize(max(1, int(round(px))))
    f.setBold(bool(layer.get("bold")))
    f.setItalic(bool(layer.get("italic")))
    f.setHintingPreference(QFont.HintingPreference.PreferNoHinting)
    sp = float(layer.get("spacing") or 0)
    if sp:
        f.setLetterSpacing(QFont.SpacingType.PercentageSpacing, 100 + sp)
    return f


def _color(hexstr: str, alpha: float = 1.0):
    from PySide6.QtGui import QColor

    c = QColor(hexstr or "#000000")
    if not c.isValid():
        c = QColor("#000000")
    c.setAlphaF(max(0.0, min(1.0, alpha)))
    return c


def _brush_path(rect, seed: int):
    """Nét cọ quét ngang: mép trên / dưới gợn nhẹ, 2 đầu tua lông cọ so le (vẽ theo hạt cố định -> cùng chữ = cùng hình)."""
    from PySide6.QtCore import QPointF
    from PySide6.QtGui import QPainterPath

    rng = random.Random(seed)
    L, T, R, B = rect.left(), rect.top(), rect.right(), rect.bottom()
    h, w = B - T, R - L
    tilt = rng.uniform(-0.04, 0.04) * h
    path = QPainterPath()
    n = max(6, int(w / max(4.0, h * 0.45)))
    top = [QPointF(L + w * i / n, T + tilt * (i / n - 0.5) + rng.uniform(-0.05, 0.05) * h
                   + math.sin(i / n * math.pi) * -0.04 * h) for i in range(n + 1)]
    bot = [QPointF(L + w * i / n, B + tilt * (i / n - 0.5) + rng.uniform(-0.05, 0.05) * h
                   + math.sin(i / n * math.pi) * 0.03 * h) for i in range(n + 1)]
    path.moveTo(top[0])
    for q in top[1:]:
        path.lineTo(q)
    # đầu phải: tua lông cọ (dài ngắn xen kẽ)
    k = 9
    for j in range(1, k):
        y = top[-1].y() + (bot[-1].y() - top[-1].y()) * j / k
        out = rng.uniform(0.05, 0.38) * h if j % 2 else -rng.uniform(0.0, 0.12) * h
        path.lineTo(QPointF(R + out, y))
    path.lineTo(bot[-1])
    for q in reversed(bot[:-1]):
        path.lineTo(q)
    # đầu trái: tua ngắn hơn
    for j in range(k - 1, 0, -1):
        y = top[0].y() + (bot[0].y() - top[0].y()) * j / k
        out = rng.uniform(0.04, 0.22) * h if j % 2 else -rng.uniform(0.0, 0.08) * h
        path.lineTo(QPointF(L - out, y))
    path.closeSubpath()
    return path


def _bubble_path(rect, radius: float):
    from PySide6.QtCore import QPointF
    from PySide6.QtGui import QPainterPath, QPolygonF

    path = QPainterPath()
    path.addRoundedRect(rect, radius, radius)
    w, h = rect.width(), rect.height()
    tail = QPainterPath()
    tail.addPolygon(QPolygonF([QPointF(rect.left() + w * 0.16, rect.bottom() - 2),
                               QPointF(rect.left() + w * 0.16 + max(h * 0.45, w * 0.1), rect.bottom() - 2),
                               QPointF(rect.left() + w * 0.06, rect.bottom() + h * 0.42)]))
    return path.united(tail)


def _text_geometry(layer: dict, px: float):
    """Đường viền chữ (tâm khối chữ ở gốc toạ độ) + bề rộng / cao khối + ô từng dòng."""
    from PySide6.QtCore import QPointF, QRectF, Qt
    from PySide6.QtGui import QFontMetricsF, QPainterPath

    f = _qfont(layer, px)
    fm = QFontMetricsF(f)
    lines = [ln.rstrip() for ln in str(layer.get("text") or "").split("\n")] or [""]
    lh = fm.height() * float(layer.get("line_h") or 1.12)
    widths = [fm.horizontalAdvance(ln) for ln in lines]
    tw = max(widths + [px * 0.3])
    th = lh * (len(lines) - 1) + fm.height()
    align = layer.get("align") or "center"
    path = QPainterPath()
    path.setFillRule(Qt.FillRule.WindingFill)   # font biến thiên / nét chồng nhau: không thủng lỗ ở chỗ chồng
    rows = []
    for i, (ln, w) in enumerate(zip(lines, widths)):
        x = -tw / 2 + {"left": 0.0, "right": tw - w}.get(align, (tw - w) / 2)
        top = -th / 2 + i * lh
        if ln:
            path.addText(QPointF(x, top + fm.ascent()), f, ln)
        rows.append(QRectF(x, top, w, fm.height()))
    return path, tw, th, rows, fm


def render_text(layer: dict, TW: int, TH: int, scale: float = 1.0):
    """Ảnh của lớp chữ (đã xoay, đã nhân độ đục) + toạ độ tâm khối chữ trong ảnh: (QImage, ox, oy)."""
    from PySide6.QtCore import QPointF, QRectF, Qt
    from PySide6.QtGui import QBrush, QImage, QPainter, QPainterPath, QPen, QTransform

    px = max(2.0, float(layer.get("size") or 0.06) * min(TW, TH) * scale)
    path, tw, th, rows, fm = _text_geometry(layer, px)
    ol = max(0.0, float(layer.get("outline") or 0)) * px
    sd = max(0.0, float(layer.get("shadow") or 0)) * px
    bg = layer.get("bg") or "none"
    pad = max(0.0, float(layer.get("pad") or 0)) * px
    block = QRectF(-tw / 2 - pad, -th / 2 - pad * (0.62 if bg in ("brush", "underline") else 0.8),
                   tw + 2 * pad, th + pad * (1.24 if bg in ("brush", "underline") else 1.6))
    bgpath = QPainterPath()
    if bg == "box":
        bgpath.addRect(block)
    elif bg == "round":
        r = min(block.height() / 2, float(layer.get("radius") or 0.3) * block.height())
        bgpath.addRoundedRect(block, r, r)
    elif bg == "bubble":
        r = min(block.height() / 2, float(layer.get("radius") or 0.45) * block.height())
        bgpath = _bubble_path(block, r)
    elif bg == "brush":   # mỗi dòng 1 nét cọ (dòng sát nhau thì 2 nét chồng nhẹ lên nhau)
        seed = int(hashlib.md5((layer.get("id", "") + str(layer.get("text"))).encode()).hexdigest()[:8], 16)
        for i, rr in enumerate(rows):
            if rr.width() <= 0:
                continue
            r = QRectF(rr.left() - pad, rr.top() - pad * 0.32, rr.width() + 2 * pad, rr.height() + pad * 0.64)
            bgpath = bgpath.united(_brush_path(r, seed + i * 7919))
    elif bg == "circle":
        d = max(tw, th) + 2 * pad
        bgpath.addEllipse(QPointF(0, 0), d / 2, d / 2)
    elif bg == "underline":
        for rr in rows:
            if rr.width() > 0:
                bgpath.addRoundedRect(QRectF(rr.left() - pad * 0.35, rr.top() + rr.height() * 0.52,
                                             rr.width() + pad * 0.7, rr.height() * 0.42), px * 0.08, px * 0.08)
    ink = path.boundingRect().adjusted(-ol - 1, -ol - 1, ol + 1 + sd, ol + 1 + sd)
    box = ink.united(bgpath.boundingRect()) if not bgpath.isEmpty() else ink
    box = box.united(QRectF(-tw / 2, -th / 2, tw, th))
    rot = float(layer.get("rot") or 0)
    rr_ = QTransform().rotate(rot).mapRect(box)
    W, H = int(math.ceil(rr_.width())) + 4, int(math.ceil(rr_.height())) + 4
    img = QImage(max(1, W), max(1, H), QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(Qt.GlobalColor.transparent)
    ox, oy = -rr_.left() + 2, -rr_.top() + 2
    p = QPainter(img)
    p.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.TextAntialiasing)
    p.translate(ox, oy)
    p.rotate(rot)
    if not bgpath.isEmpty():
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(_color(layer.get("bg_color"), float(layer.get("bg_opacity", 1) if layer.get("bg_opacity")
                                                                  is not None else 1))))
        p.drawPath(bgpath)
        if bg == "brush":   # vệt lông cọ: vài sọc mảnh đậm / nhạt dọc theo nét + khe hở ở 2 đầu (chỉ trong nét cọ)
            rng = random.Random(len(str(layer.get("text"))) * 7919 + 13)
            base = _color(layer.get("bg_color"))
            p.save()
            p.setClipPath(bgpath)
            for rr in rows:
                if rr.width() <= 0:
                    continue
                L, R = rr.left() - pad, rr.right() + pad
                T, Hh = rr.top() - pad * 0.32, rr.height() + pad * 0.64
                for j in range(5):
                    y = T + Hh * rng.uniform(0.1, 0.9)
                    x0, x1 = L + (R - L) * rng.uniform(0.0, 0.3), R - (R - L) * rng.uniform(0.0, 0.3)
                    c = base.darker(115) if j % 2 else base.lighter(110)
                    c.setAlphaF(0.5)
                    p.setPen(QPen(c, max(1.0, Hh * rng.uniform(0.012, 0.035)), Qt.PenStyle.SolidLine,
                                  Qt.PenCapStyle.RoundCap))
                    p.drawLine(QPointF(x0, y), QPointF(x1, y + rng.uniform(-0.03, 0.03) * Hh))
                p.setCompositionMode(QPainter.CompositionMode.CompositionMode_DestinationOut)
                for j in range(4):
                    y = T + Hh * rng.uniform(0.12, 0.88)
                    if j % 2:
                        x0, x1 = R - (R - L) * rng.uniform(0.03, 0.12), R + px
                    else:
                        x0, x1 = L - px, L + (R - L) * rng.uniform(0.02, 0.07)
                    p.setPen(QPen(_color("#000000", 1.0), max(1.0, Hh * 0.025)))
                    p.drawLine(QPointF(x0, y), QPointF(x1, y))
                p.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
            p.restore()
        if bg == "bubble" and ol <= 0:   # bong bóng: viền mảnh màu viền
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QPen(_color(layer.get("outline_color") or "#000000", 0.9), max(1.0, px * 0.05)))
            p.drawPath(bgpath)
    if sd > 0:   # bóng đổ: chữ + viền dời xuống phải
        sp = path.translated(sd, sd)
        sc = _color(layer.get("shadow_color") or "#000000", 0.6)
        if ol > 0:
            p.strokePath(sp, QPen(sc, 2 * ol, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        p.fillPath(sp, sc)
    if ol > 0:
        p.strokePath(path, QPen(_color(layer.get("outline_color") or "#000000"), 2 * ol, Qt.PenStyle.SolidLine,
                                Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
    p.fillPath(path, _color(layer.get("color") or "#FFFFFF"))
    p.end()
    _apply_opacity(img, float(layer.get("opacity", 1.0) if layer.get("opacity") is not None else 1.0))
    return img, ox, oy


def _apply_opacity(img, op: float):
    from PySide6.QtGui import QColor, QPainter

    if op >= 0.999:
        return
    p = QPainter(img)
    p.setCompositionMode(QPainter.CompositionMode.CompositionMode_DestinationIn)
    p.fillRect(img.rect(), QColor(0, 0, 0, int(max(0.0, op) * 255)))
    p.end()


def is_animated(path: str | Path) -> bool:
    """Ảnh động (GIF nhiều khung) – khi xuất FFmpeg giải mã từng khung, xem trước chạy bằng QMovie."""
    p = Path(path or "")
    if p.suffix.lower() != ".gif" or not p.exists():
        return False
    try:
        from PySide6.QtGui import QImageReader

        r = QImageReader(str(p))
        return r.supportsAnimation() and r.imageCount() != 1
    except Exception:  # noqa: BLE001
        return False


def load_image(path: str | Path, width: int = 0):
    """Đọc ảnh logo (SVG vẽ đúng cỡ cần, ảnh thường giữ nguyên) -> QImage ARGB (rỗng nếu lỗi)."""
    from PySide6.QtCore import QSize, Qt
    from PySide6.QtGui import QImage, QImageReader, QPainter

    p = Path(path or "")
    if not p.exists():
        return QImage()
    if p.suffix.lower() == ".svg":
        from PySide6.QtSvg import QSvgRenderer

        r = QSvgRenderer(str(p))
        if not r.isValid():
            return QImage()
        ds = r.defaultSize()
        w = width or max(64, ds.width())
        h = max(1, int(round(w * (ds.height() / max(1, ds.width())))))
        img = QImage(w, h, QImage.Format.Format_ARGB32_Premultiplied)
        img.fill(Qt.GlobalColor.transparent)
        pa = QPainter(img)
        r.render(pa)
        pa.end()
        return img
    rd = QImageReader(str(p))
    rd.setAutoTransform(True)
    if width:
        sz = rd.size()
        if sz.isValid() and sz.width() > width * 2:   # ảnh rất lớn: đọc nhỏ lại cho nhanh
            rd.setScaledSize(QSize(width * 2, max(1, int(sz.height() * width * 2 / sz.width()))))
    img = rd.read()
    return img.convertToFormat(QImage.Format.Format_ARGB32_Premultiplied) if not img.isNull() else img


def logo_size(layer: dict, TW: int, TH: int, src_w: int, src_h: int, scale: float = 1.0) -> tuple[int, int]:
    w = max(2, int(round(float(layer.get("w") or 0.16) * TW * scale)))
    return w, max(2, int(round(w * src_h / max(1, src_w))))


def render_logo(layer: dict, TW: int, TH: int, scale: float = 1.0, frame=None):
    """Ảnh của lớp logo (đúng cỡ, đã xoay, đã nhân độ đục) + tâm trong ảnh. frame: khung GIF đang chạy (xem trước)."""
    from PySide6.QtCore import QRectF, Qt
    from PySide6.QtGui import QImage, QPainter, QTransform

    w_target = max(2, int(round(float(layer.get("w") or 0.16) * TW * scale)))
    src = frame if frame is not None and not frame.isNull() else load_image(layer.get("path"), w_target)
    if src is None or src.isNull():
        img = QImage(max(2, w_target), max(2, w_target // 2), QImage.Format.Format_ARGB32_Premultiplied)
        img.fill(Qt.GlobalColor.transparent)
        return img, img.width() / 2, img.height() / 2
    w, h = logo_size(layer, TW, TH, src.width(), src.height(), scale)
    rot = float(layer.get("rot") or 0)
    box = QRectF(-w / 2, -h / 2, w, h)
    rr = QTransform().rotate(rot).mapRect(box)
    W, H = int(math.ceil(rr.width())) + 2, int(math.ceil(rr.height())) + 2
    img = QImage(W, H, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(Qt.GlobalColor.transparent)
    ox, oy = -rr.left() + 1, -rr.top() + 1
    p = QPainter(img)
    p.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.SmoothPixmapTransform)
    p.translate(ox, oy)
    p.rotate(rot)
    p.drawImage(box, src)
    p.end()
    _apply_opacity(img, float(layer.get("opacity", 1.0) if layer.get("opacity") is not None else 1.0))
    return img, ox, oy


def render(layer: dict, TW: int, TH: int, scale: float = 1.0, frame=None):
    if layer.get("kind") == "logo":
        return render_logo(layer, TW, TH, scale, frame)
    return render_text(layer, TW, TH, scale)


LOOK_KEYS = ("kind", "text", "font", "bold", "italic", "size", "color", "outline", "outline_color", "shadow",
             "shadow_color", "bg", "bg_color", "bg_opacity", "pad", "radius", "align", "line_h", "spacing", "rot",
             "opacity", "path", "w", "id")


def look_key(layer: dict, TW: int, TH: int, scale: float = 1.0) -> str:
    """Mã hình dạng của lớp (không gồm vị trí / thời gian) – đổi vị trí không phải vẽ lại."""
    d = {k: layer.get(k) for k in LOOK_KEYS}
    if layer.get("kind") == "logo":
        try:
            st = Path(layer.get("path", "")).stat()
            d["_f"] = [st.st_size, int(st.st_mtime)]
        except OSError:
            d["_f"] = None
    d["_s"] = [TW, TH, round(scale, 4)]
    return hashlib.sha1(json.dumps(d, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:16]


def render_png(layer: dict, TW: int, TH: int, out_dir: str | Path) -> tuple[Path, float, float, int, int]:
    """Ảnh PNG của lớp đúng cỡ khung xuất (nhớ theo hình dạng): (file, tâm x, tâm y trong ảnh, rộng, cao)."""
    ensure_qt()
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    key = look_key(layer, TW, TH)
    f = out_dir / f"layer_{key}.png"
    meta = out_dir / f"layer_{key}.json"
    if f.exists() and meta.exists():
        try:
            m = json.loads(meta.read_text("utf-8"))
            return f, m["ox"], m["oy"], m["w"], m["h"]
        except Exception:  # noqa: BLE001
            pass
    img, ox, oy = render(layer, TW, TH)
    img.save(str(f), "PNG")
    meta.write_text(json.dumps({"ox": ox, "oy": oy, "w": img.width(), "h": img.height()}), "utf-8")
    return f, ox, oy, img.width(), img.height()


def build_overlays(layers: list, TW: int, TH: int, fps: float, duration: float, keep: list, speed: float,
                   work: Path, in_label: str, first_input: int, window: tuple[float, float] | None = None
                   ) -> tuple[list[str], list[str], str, int]:
    """Đồ thị FFmpeg đè các lớp phủ lên video (giây video xuất). window = (đầu, cuối) khi xuất từng đoạn: chỉ thêm lớp
    có mặt trong đoạn đó, lớp bắt đầu từ đầu đoạn (không phải giải mã từ đầu phim).
    Trả (tham số đầu vào thêm, các đoạn filter, nhãn đầu ra, số đầu vào kế tiếp)."""
    inputs: list[str] = []
    fc: list[str] = []
    v, n_in = in_label, first_input
    ldir = work / "layers"
    for k, L in enumerate(layers or []):
        if L.get("hidden"):
            continue
        if L.get("kind") == "logo" and not Path(L.get("path") or "").exists():
            continue
        if L.get("kind") != "logo" and not str(L.get("text") or "").strip():
            continue
        span = out_span(L, duration, keep, speed)
        if not span:
            continue
        A, B = span
        A0, B0 = A, B
        if window:
            A0, B0 = max(A, window[0]), min(B, window[1])
            if B0 - A0 < 1 / max(1.0, fps):
                continue
        fi, fo = fades(L)
        fade = []
        if fi > 0:
            fade.append(f"fade=t=in:st={A:.3f}:d={fi:.3f}:alpha=1")
        if fo > 0:
            fade.append(f"fade=t=out:st={max(A, B - fo):.3f}:d={fo:.3f}:alpha=1")
        dur = B0 - A0 + 0.1
        cx, cy = float(L.get("x", 0.5)) * TW, float(L.get("y", 0.5)) * TH
        if L.get("kind") == "logo" and is_animated(L["path"]):
            img = load_image(L["path"])
            w, h = logo_size(L, TW, TH, img.width() or 1, img.height() or 1)
            inputs += ["-ignore_loop", "0", "-t", f"{dur:.3f}", "-i", str(Path(L["path"]).resolve())]
            rot = float(L.get("rot") or 0)
            chain = [f"fps={fps:.5f}", f"scale={w}:{h}", "format=rgba"]
            op = float(L.get("opacity", 1.0) if L.get("opacity") is not None else 1.0)
            if op < 0.999:
                chain.append(f"colorchannelmixer=aa={op:.3f}")
            if abs(rot) > 0.01:
                chain.append(f"rotate={math.radians(rot):.5f}:c=none:ow=rotw({math.radians(rot):.5f}):"
                             f"oh=roth({math.radians(rot):.5f})")
                rw = abs(w * math.cos(math.radians(rot))) + abs(h * math.sin(math.radians(rot)))
                rh = abs(w * math.sin(math.radians(rot))) + abs(h * math.cos(math.radians(rot)))
            else:
                rw, rh = w, h
            X, Y = cx - rw / 2, cy - rh / 2
            iw, ih = int(rw), int(rh)
        else:
            f, ox, oy, iw, ih = render_png(L, TW, TH, ldir)
            inputs += ["-loop", "1", "-framerate", f"{fps:.5f}", "-t", f"{dur:.3f}", "-i", str(f.resolve())]
            chain = ["format=rgba"]
            X, Y = cx - ox, cy - oy
        chain.append(f"setpts=PTS-STARTPTS+{A0:.4f}/TB")
        chain += fade
        lab = f"ly{k}"
        fc.append(f"[{n_in}:v]{','.join(chain)}[{lab}]")
        xe, ye = ffmpeg_xy(L, A, X, Y, TW, TH, iw, ih)
        fc.append(f"[{v}][{lab}]overlay=x='{xe}':y='{ye}':eof_action=pass:"
                  f"enable='between(t,{A0:.3f},{B0:.3f})'[lo{k}]")
        v = f"lo{k}"
        n_in += 1
    return inputs, fc, v, n_in


# ------------------------------------------------------------------ thư viện logo của bạn (data/logos)
def _lib_file() -> Path:
    return LOGOS_DIR / "index.json"


def logo_library() -> list[dict]:
    """Logo đã tải lên: [{id, name, file, w, h, anim}] (file còn trên đĩa)."""
    try:
        items = json.loads(_lib_file().read_text("utf-8"))
    except Exception:  # noqa: BLE001
        return []
    return [it for it in items if (LOGOS_DIR / it.get("file", "")).exists()]


def logo_path(item: dict) -> Path:
    return LOGOS_DIR / item["file"]


def add_logos(paths: list) -> list[dict]:
    """Tải nhiều logo vào thư viện (chép vào data/logos). Trả về các mục mới."""
    LOGOS_DIR.mkdir(parents=True, exist_ok=True)
    items = logo_library()
    new = []
    for src in paths:
        src = Path(src)
        if src.suffix.lower() not in IMAGE_EXTS or not src.exists():
            continue
        img = load_image(src)
        if img.isNull():
            continue
        lid = _uid()
        dst = LOGOS_DIR / f"{lid}{src.suffix.lower()}"
        shutil.copy2(src, dst)
        it = {"id": lid, "name": src.stem[:40], "file": dst.name, "w": img.width(), "h": img.height(),
              "anim": is_animated(dst)}
        items.append(it)
        new.append(it)
    _lib_file().write_text(json.dumps(items, ensure_ascii=False, indent=1), "utf-8")
    return new


def remove_logo(lid: str):
    items = logo_library()
    keep = []
    for it in items:
        if it["id"] == lid:
            try:
                (LOGOS_DIR / it["file"]).unlink()
            except OSError:
                pass
        else:
            keep.append(it)
    LOGOS_DIR.mkdir(parents=True, exist_ok=True)
    _lib_file().write_text(json.dumps(keep, ensure_ascii=False, indent=1), "utf-8")

