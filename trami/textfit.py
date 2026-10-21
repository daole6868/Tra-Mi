"""Đo bề rộng chữ phụ đề đúng như libass vẽ khi xuất + xếp câu thành dòng (dùng chung khung xem trước và video xuất).

libass coi Fontsize = winAscent + winDescent của bảng OS/2 (2.0.33), nên chữ thật nhỏ hơn "cỡ chữ" (Be Vietnam Pro: em =
0,655 × cỡ). Trước 2.0.48 việc xuống dòng đoán theo số ký tự (mỗi ký tự 0,55 × cỡ chữ) – rộng hơn chữ thật ~1,6 lần nên
câu bị xuống dòng khi mới dùng ~2/3 bề rộng khung. Giờ đo bằng font thật:
  1. Qt (QFontMetricsF, đang chạy giao diện – cả lúc xuất video trong tool) – đúng như khung xem trước;
  2. không có Qt (dòng lệnh): Pillow + fontTools nếu có, đọc file font;
  3. không có gì: ước lượng theo loại chữ.
"""
from __future__ import annotations

import sys
import unicodedata
from pathlib import Path

REF_PX = 200        # đo ở cỡ chuẩn rồi nhân tỉ lệ -> cỡ chữ lẻ vẫn chính xác
_QT_METRICS: dict = {}
_PIL_FONTS: dict = {}


def qt_ready() -> bool:
    try:
        from PySide6.QtGui import QGuiApplication

        return QGuiApplication.instance() is not None
    except Exception:  # noqa: BLE001
        return False


def qt_font(family: str, bold: bool):
    """(QFont cỡ REF_PX, em / Fontsize, phần trên đường chân chữ / chiều cao dòng) – như libass."""
    from PySide6.QtGui import QFont, QFontMetricsF

    key = (family, bold)
    f = QFont(family)
    f.setBold(bold)
    f.setPixelSize(REF_PX)
    if key not in _QT_METRICS:
        k, asc = 0.8, 0.8
        try:
            import struct

            from PySide6.QtGui import QRawFont

            raw = QRawFont.fromFont(f)
            os2 = bytes(raw.fontTable("OS/2"))
            wa, wd = struct.unpack(">hh", os2[74:78]) if len(os2) >= 78 else (0, 0)
            if wa + wd > 0:
                k, asc = raw.unitsPerEm() / (wa + wd), wa / (wa + wd)
            else:
                fm = QFontMetricsF(f)
                k, asc = REF_PX / (fm.ascent() + fm.descent()), fm.ascent() / (fm.ascent() + fm.descent())
        except Exception:  # noqa: BLE001
            pass
        _QT_METRICS[key] = (k, asc)
    k, asc = _QT_METRICS[key]
    return f, k, asc


def _font_dirs() -> list[Path]:
    from .config import FONTS_DIR

    dirs = [FONTS_DIR]
    if sys.platform == "win32":
        import os

        dirs += [Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts",
                 Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "Windows" / "Fonts"]
    elif sys.platform == "darwin":
        dirs += [Path("/System/Library/Fonts"), Path("/Library/Fonts"), Path.home() / "Library" / "Fonts"]
    else:
        dirs += [Path("/usr/share/fonts"), Path("/usr/local/share/fonts"), Path.home() / ".fonts",
                 Path.home() / ".local" / "share" / "fonts"]
    return [d for d in dirs if d.is_dir()]


def _pil_font(family: str, bold: bool):
    """(ImageFont cỡ REF_PX theo em, em / Fontsize) đọc từ file font; None nếu không có Pillow / fontTools / file."""
    key = (family, bold)
    if key in _PIL_FONTS:
        return _PIL_FONTS[key]
    res = None
    try:
        from fontTools.ttLib import TTFont
        from PIL import ImageFont

        want = family.lower().replace(" ", "")
        best = None
        for d in _font_dirs():
            for f in d.rglob("*.[ot]t[fc]"):
                if not f.name.lower().replace(" ", "").replace("-", "").startswith(want[:6]):
                    continue
                try:
                    tt = TTFont(str(f), fontNumber=0, lazy=True)
                    name = tt["name"]
                    fam = (name.getDebugName(1) or "").lower().replace(" ", "")
                    sub = (name.getDebugName(2) or "").lower()
                    if fam != want and not fam.startswith(want):
                        continue
                    score = (fam == want) * 2 + (("bold" in sub) == bold) * 2 + ("italic" not in sub)
                    if best is None or score > best[0]:
                        os2, upm = tt["OS/2"], tt["head"].unitsPerEm
                        best = (score, f, upm / max(1, os2.usWinAscent + os2.usWinDescent))
                except Exception:  # noqa: BLE001
                    continue
        if best:
            res = (ImageFont.truetype(str(best[1]), REF_PX), best[2])
    except Exception:  # noqa: BLE001
        res = None
    _PIL_FONTS[key] = res
    return res


def _guess(text: str, size: float) -> float:
    em = 0.7 * size
    w = 0.0
    for c in text:
        if unicodedata.category(c) == "Mn":
            continue
        w += em * (1.0 if unicodedata.east_asian_width(c) in "WF" else 0.33 if c == " " else 0.55)
    return w


def width(text: str, family: str, bold: bool, size: float) -> float:
    """Bề rộng (điểm ảnh khung xuất) của 1 dòng chữ cỡ `size` (Fontsize của ASS)."""
    if not text:
        return 0.0
    if qt_ready():
        try:
            from PySide6.QtGui import QFontMetricsF

            f, k, _ = qt_font(family, bold)
            return QFontMetricsF(f).horizontalAdvance(text) * size * k / REF_PX
        except Exception:  # noqa: BLE001
            pass
    pf = _pil_font(family, bold)
    if pf:
        font, k = pf
        try:
            return font.getlength(text) * size * k / REF_PX
        except Exception:  # noqa: BLE001
            pass
    return _guess(text, size)


# ------------------------------------------------------------------ xếp dòng

_NO_START = set("、。，．！？）」』】〉》ー・…ぁぃぅぇぉっゃゅょゎァィゥェォッャュョヮヵヶ,.!?;:)]")
_NO_END = set("（「『【〈《([เแโใไ")
_BREAK_AFTER = set("、。，！？,.!?;:")


def _units(text: str) -> list[str]:
    """Các đơn vị không được cắt giữa: từ (kèm dấu cách phía sau) cho tiếng có dấu cách; từng chữ (kèm dấu kết hợp,
    dấu câu đóng) cho tiếng viết liền (Nhật, Thái, Trung)."""
    words = text.split(" ")
    if len(words) > 1 and max(len(w) for w in words) <= 24:
        return [w + " " for w in words[:-1]] + [words[-1]]
    out: list[str] = []
    for c in text:
        if out and (unicodedata.category(c) == "Mn" or c in _NO_START or out[-1][-1:] in _NO_END):
            out[-1] += c
        else:
            out.append(c)
    return out


_JA_PARTICLE = set("はがをにでともへや")


def split_balanced(text: str, n: int, measure, avail: float = float("inf")) -> list[str]:
    """Chia câu thành n dòng sao cho dòng dài nhất ngắn nhất (không dòng nào quá `avail`); ưu tiên ngắt sau dấu câu
    (tiếng viết liền: ưu tiên mạnh hơn, và sau trợ từ tiếng Nhật), tránh dòng chỉ 1 từ."""
    units = _units(text)
    if n <= 1 or len(units) < 2:
        return [text.strip()]
    n = min(n, len(units))
    ws = [measure(u) for u in units]
    pre = [0.0]
    for w in ws:
        pre.append(pre[-1] + w)
    total = pre[-1]
    spaced = units[0].endswith(" ")
    bonus = (0.08 if spaced else 0.16) * total / n

    def cost(a: int, b: int) -> float:
        w = pre[b] - pre[a]
        c = w + (1e6 if w > avail else 0.0)
        if b < len(units):
            end = units[b - 1].rstrip()[-1:]
            if end in _BREAK_AFTER:
                c -= bonus
            elif not spaced and end in _JA_PARTICLE:
                c -= bonus * 0.3
        if b - a == 1 and len(units) > n + 1:
            c += bonus            # dòng chỉ 1 từ / 1 chữ: tránh nếu được
        return c

    # quy hoạch động: best[k][j] = (giá trị lớn nhất nhỏ nhất, chỗ cắt) khi xếp units[:j] vào k dòng
    L = len(units)
    INF = float("inf")
    best = [[(INF, -1)] * (L + 1) for _ in range(n + 1)]
    best[0][0] = (0.0, -1)
    for k in range(1, n + 1):
        for j in range(k, L - (n - k) + 1):
            for i in range(k - 1, j):
                prev = best[k - 1][i][0]
                if prev == INF:
                    continue
                v = max(prev, cost(i, j))
                if v < best[k][j][0] - 1e-6:
                    best[k][j] = (v, i)
    cuts, j = [], L
    for k in range(n, 0, -1):
        i = best[k][j][1]
        cuts.append((i, j))
        j = i
    return ["".join(units[a:b]).strip() for a, b in reversed(cuts)]


def fit_lines(text: str, family: str, bold: bool, size: int, avail: float, max_lines: int = 2,
              mode: str = "wrap", min_scale: float = 0.7) -> tuple[list[str], int]:
    """Xếp 1 câu vào khung rộng `avail` (điểm ảnh). Trả về (các dòng, cỡ chữ dùng cho câu này).
    mode "wrap": vừa 1 dòng thì giữ, không thì chia 2..max_lines dòng cân nhau; vẫn không vừa -> thu nhỏ chữ.
    mode "shrink": ưu tiên 1 dòng – thu nhỏ chữ (nhỏ nhất min_scale × cỡ) cho vừa, nhỏ hơn nữa mới xuống dòng."""
    text = " ".join((text or "").split())
    if not text or "\n" in text:
        return ([ln.strip() for ln in (text or "").split("\n")] if text else []), size
    max_lines = max(1, int(max_lines or 1))

    def meas(t: str, s: int = size) -> float:
        return width(t, family, bold, s)

    w1 = meas(text)
    if w1 <= avail:
        return [text], size
    if mode == "shrink":
        s = int(size * avail / w1)
        if s >= size * min_scale or max_lines == 1:
            return [text], max(8, s)
    for n in range(2, max_lines + 1):
        lines = split_balanced(text, n, meas, avail)
        if max(meas(ln) for ln in lines) <= avail:
            return lines, size
    lines = split_balanced(text, max_lines, meas) if max_lines > 1 else [text]
    widest = max(meas(ln) for ln in lines)
    return lines, max(8, int(size * avail / max(1.0, widest)))
