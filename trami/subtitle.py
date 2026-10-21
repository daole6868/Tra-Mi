"""Mô hình phụ đề và đọc/ghi SRT, VTT, ASS, TXT."""
from __future__ import annotations

import math
import re
import unicodedata
from dataclasses import dataclass, field, asdict
from pathlib import Path


@dataclass
class Segment:
    start: float
    end: float
    text: str = ""         # câu gốc (tiếng Trung)
    trans: str = ""        # bản dịch (tiếng Việt)
    speaker: str = ""
    extra: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Segment":
        return cls(start=float(d.get("start", 0)), end=float(d.get("end", 0)), text=d.get("text", "") or "",
                   trans=d.get("trans", "") or "", speaker=d.get("speaker", "") or "", extra=d.get("extra") or {})

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)


# ---------------------------------------------------------------- thời gian

def fmt_srt_time(t: float) -> str:
    t = max(0.0, t)
    ms = int(round(t * 1000))
    h, ms = divmod(ms, 3600000)
    m, ms = divmod(ms, 60000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def fmt_vtt_time(t: float) -> str:
    return fmt_srt_time(t).replace(",", ".")


def fmt_ass_time(t: float) -> str:
    t = max(0.0, t)
    cs = int(round(t * 100))
    h, cs = divmod(cs, 360000)
    m, cs = divmod(cs, 6000)
    s, cs = divmod(cs, 100)
    return f"{h:d}:{m:02d}:{s:02d}.{cs:02d}"


def parse_time(s: str) -> float:
    s = s.strip().replace(",", ".")
    parts = s.split(":")
    try:
        if len(parts) == 3:
            return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
        if len(parts) == 2:
            return int(parts[0]) * 60 + float(parts[1])
        return float(parts[0])
    except ValueError:
        return 0.0


# ---------------------------------------------------------------- đọc

_TIME_LINE = re.compile(r"(\d+:\d{1,2}:\d{1,2}[.,]\d+|\d{1,2}:\d{1,2}[.,]\d+)\s*-->\s*(\d+:\d{1,2}:\d{1,2}[.,]\d+|\d{1,2}:\d{1,2}[.,]\d+)")
_TAG = re.compile(r"<[^>]+>|\{\\[^}]*\}")


def _read_text(path: str | Path) -> str:
    raw = Path(path).read_bytes()
    for enc in ("utf-8-sig", "utf-16", "gb18030", "big5", "cp1258", "latin-1"):
        try:
            txt = raw.decode(enc)
            if enc == "utf-16" and not raw.startswith((b"\xff\xfe", b"\xfe\xff")):
                continue
            return txt
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def parse_srt_vtt(text: str) -> list[Segment]:
    segs: list[Segment] = []
    blocks = re.split(r"\n\s*\n", text.replace("\r\n", "\n").replace("\r", "\n"))
    for b in blocks:
        lines = [l for l in b.strip("\n").split("\n")]
        for i, line in enumerate(lines):
            m = _TIME_LINE.search(line)
            if m:
                body = "\n".join(lines[i + 1:]).strip()
                body = _TAG.sub("", body)
                segs.append(Segment(parse_time(m[1]), parse_time(m[2]), body))
                break
    return segs


def parse_ass(text: str) -> list[Segment]:
    segs: list[Segment] = []
    fmt: list[str] | None = None
    in_events = False
    for line in text.splitlines():
        s = line.strip()
        if s.lower() == "[events]":
            in_events = True
            continue
        if s.startswith("[") and s.endswith("]"):
            in_events = False
            continue
        if not in_events:
            continue
        if s.lower().startswith("format:"):
            fmt = [x.strip().lower() for x in s[7:].split(",")]
        elif s.lower().startswith("dialogue:") and fmt:
            vals = s[9:].split(",", len(fmt) - 1)
            d = dict(zip(fmt, [v.strip() for v in vals]))
            body = _TAG.sub("", d.get("text", "")).replace("\\N", "\n").replace("\\n", "\n").replace("\\h", " ")
            segs.append(Segment(parse_time(d.get("start", "0")), parse_time(d.get("end", "0")), body.strip()))
    segs.sort(key=lambda x: x.start)
    return segs


def load_subtitles(path: str | Path) -> list[Segment]:
    txt = _read_text(path)
    ext = Path(path).suffix.lower()
    if ext in (".ass", ".ssa"):
        return parse_ass(txt)
    return parse_srt_vtt(txt)


# ---------------------------------------------------------------- ghi

def _pick(seg: Segment, which: str) -> str:
    if which == "source":
        return seg.text
    if which == "both":
        return (seg.trans + "\n" + seg.text).strip() if seg.trans else seg.text
    return seg.trans or ""


def to_srt(segs: list[Segment], which: str = "trans") -> str:
    out = []
    n = 0
    for s in segs:
        body = _pick(s, which).strip()
        if not body:
            continue
        n += 1
        out.append(f"{n}\n{fmt_srt_time(s.start)} --> {fmt_srt_time(s.end)}\n{body}\n")
    return "\n".join(out)


def to_vtt(segs: list[Segment], which: str = "trans") -> str:
    out = ["WEBVTT\n"]
    for s in segs:
        body = _pick(s, which).strip()
        if body:
            out.append(f"{fmt_vtt_time(s.start)} --> {fmt_vtt_time(s.end)}\n{body}\n")
    return "\n".join(out)


def to_txt(segs: list[Segment], which: str = "trans") -> str:
    return "\n".join(_pick(s, which).strip() for s in segs if _pick(s, which).strip())


def _hex_to_ass(color: str, alpha: float = 0.0) -> str:
    c = (color or "#FFFFFF").lstrip("#")
    if len(c) == 3:
        c = "".join(ch * 2 for ch in c)
    r, g, b = c[0:2], c[2:4], c[4:6]
    a = int(round(max(0.0, min(1.0, alpha)) * 255))
    return f"&H{a:02X}{b}{g}{r}".upper()


def _ass_escape(t: str) -> str:
    return t.replace("\\", "\\\\").replace("{", "(").replace("}", ")").replace("\n", "\\N")


def vis_len(t: str) -> float:
    """Độ rộng hiển thị tính theo chữ Latin: chữ Hán / kana / Hangul (rộng gấp ~1,8), dấu kết hợp (dấu thanh Thái) 0."""
    n = 0.0
    for c in t:
        if unicodedata.category(c) == "Mn":
            continue
        n += 1.8 if unicodedata.east_asian_width(c) in "WF" else 1.0
    return n


# không ngắt dòng ngay TRƯỚC các ký tự này (dấu câu đóng, kana nhỏ, ー) và ngay SAU các ký tự kia (mở ngoặc, nguyên
# âm đứng trước của chữ Thái เ แ โ ใ ไ)
_NO_START = set("、。，．！？）」』】〉》ー・…ぁぃぅぇぉっゃゅょゎァィゥェォッャュョヮヵヶ,.!?;:)]")
_NO_END = set("（「『【〈《([เแโใไ")
_BREAK_AFTER = set("、。，！？,.!?;:")


def _wrap_chars(text: str, max_chars: int) -> str:
    """Ngắt dòng tiếng viết liền không có dấu cách (Nhật, Thái): cắt giữa 2 chữ gần giữa nhất, ưu tiên sau dấu câu."""
    total = vis_len(text)
    best, best_diff = None, 1e9
    acc = 0.0
    for i in range(1, len(text)):
        acc += vis_len(text[i - 1])
        c, prev = text[i], text[i - 1]
        if unicodedata.category(c) == "Mn" or c in _NO_START or prev in _NO_END:
            continue
        diff = abs(acc - (total - acc))
        if prev in _BREAK_AFTER:
            diff -= 4
        if diff < best_diff:
            best, best_diff = i, diff
    if best is None:
        return text
    return text[:best].rstrip() + "\n" + text[best:].lstrip()


def wrap_line(text: str, max_chars: int) -> str:
    """Ngắt dòng cân đối (tối đa 2 dòng) nếu quá dài. Độ dài tính theo độ rộng hiển thị (chữ Nhật / Hàn rộng gấp
    ~1,8 chữ Latin); tiếng viết liền (Nhật, Thái không cách) ngắt giữa 2 chữ."""
    text = " ".join(text.split())
    if max_chars <= 0 or vis_len(text) <= max_chars or "\n" in text:
        return text
    words = text.split(" ")
    if len(words) < 2 or max(vis_len(w) for w in words) > max_chars * 0.7:
        return _wrap_chars(text, max_chars)
    best, best_diff = None, 1e9
    for i in range(1, len(words)):
        a, b = " ".join(words[:i]), " ".join(words[i:])
        diff = abs(vis_len(a) - vis_len(b))
        # ưu tiên ngắt sau dấu câu
        if a.endswith((",", ".", "?", "!", ";", ":")):
            diff -= 4
        if diff < best_diff:
            best, best_diff = (a, b), diff
    return best[0] + "\n" + best[1] if best else text


BOX_PAD = 0.18          # đệm nền hộp mặc định quanh chữ (× cỡ chữ)
SOURCE_SCALE = 0.8      # dòng chữ Trung trong phụ đề song ngữ nhỏ hơn


def sub_size(st: dict, width: int, height: int) -> int:
    """Cỡ chữ ASS (= chiều cao 1 dòng khi libass vẽ: winAscent + winDescent của font)."""
    return int(st.get("size") or 0) or max(16, round(min(width, height) * 0.058 if height > width else height * 0.055))


def margin_v_px(st: dict, width: int, height: int) -> int:
    if st.get("margin_v_rel") is not None and not st.get("margin_v"):
        return max(2, int(float(st["margin_v_rel"]) * height))
    return int(st.get("margin_v") or 0) or int(height * (0.07 if st.get("position") != "middle" else 0))


def is_custom_pos(st: dict) -> bool:
    return st.get("position") == "custom" and st.get("pos_x") is not None and st.get("pos_y") is not None


def block_anchor(st: dict, width: int, height: int, block_h: float) -> tuple[float, float]:
    """Tâm ngang + mép dưới của khối chữ (điểm ảnh khung xuất), đúng chỗ libass đặt."""
    if is_custom_pos(st):
        return float(st["pos_x"]) * width, float(st["pos_y"]) * height
    pos = st.get("position", "bottom")
    mv = margin_v_px(st, width, height)
    if pos == "top":
        return width / 2, mv + block_h
    if pos == "middle":
        return width / 2, height / 2 + block_h / 2
    return width / 2, height - mv


def box_pads(st: dict, size: float) -> tuple[float, float]:
    """Đệm trái/phải và trên/dưới của nền hộp ôm chữ (điểm ảnh)."""
    px, py = st.get("box_pad_x"), st.get("box_pad_y")
    px = BOX_PAD if px is None else float(px)
    py = BOX_PAD if py is None else float(py)
    return max(1.0, px * size), max(1.0, py * size)


def fixed_box(st: dict, width: int, height: int, cx: float, cy: float) -> tuple[float, float, float, float]:
    """Nền dải cố định (mọi câu cùng cỡ): x, y, rộng, cao – tâm trùng tâm khối chữ."""
    bw = max(0.02, min(1.0, float(st.get("box_w") or 0.8))) * width
    bh = max(0.02, min(1.0, float(st.get("box_h") or 0.12))) * height
    return cx - bw / 2, cy - bh / 2, bw, bh


def cjk_font_name(st: dict) -> str:
    import sys as _sys
    return st.get("cjk_font") or ("Microsoft YaHei" if _sys.platform == "win32" else
                                  "PingFang SC" if _sys.platform == "darwin" else "Noto Sans CJK SC")


def line_font(text: str, st: dict) -> str:
    """Font cho 1 dòng phụ đề dịch: font đã chọn (Việt / Anh); câu tiếng Thái / Nhật / Hàn -> font hệ thống có đủ chữ
    (Be Vietnam Pro, Bangers… không có các chữ này); chữ Trung chưa dịch -> font chữ Hán."""
    from . import langs

    base = st.get("font", "Be Vietnam Pro")
    lg = langs.detect(text, st.get("lang") or "vi")
    if lg in ("vi", "en"):
        return cjk_font_name(st) if langs.has_han(text) and not langs.has_latin(text) else base
    return langs.subtitle_font(lg, base)


def _ass_color_alpha(color: str, opacity: float) -> tuple[str, str]:
    full = _hex_to_ass(color, 1 - float(opacity))          # &HAABBGGRR
    return f"&H{full[4:]}&", f"&H{full[2:4]}&"


LINE_WIDTH = 0.9        # dòng phụ đề dài tối đa 90 % bề rộng khung (mặc định)


def line_avail(st: dict, width: float) -> float:
    """Bề rộng tối đa 1 dòng phụ đề (điểm ảnh)."""
    lw = min(1.0, max(0.3, float(st.get("line_width") or LINE_WIDTH)))
    return width * lw


def drawn_box(st: dict) -> bool:
    """Nền tự vẽ bằng hình (bo góc / 1 khối chung / dải cố định) thay cho nền vuông có sẵn của libass."""
    return bool(st.get("box")) and (bool(st.get("box_fixed")) or bool(st.get("box_block"))
                                    or float(st.get("box_radius") or 0) > 0)


def layout_event(parts: list[tuple[str, str]], st: dict, width: int, height: int) -> dict | None:
    """Xếp 1 câu phụ đề đúng như khi xuất – dùng chung cho video xuất (`to_ass`) và khung xem trước.
    parts: [(chữ, "vi" bản dịch | "zh" dòng Trung nhỏ của song ngữ | "src" chỉ chữ Trung)].
    Trả về các dòng [(chữ, font, cỡ, loại)], bề rộng từng dòng, tâm ngang, chân / đỉnh khối chữ, các ô nền
    [(x, y, rộng, cao)] và bán kính bo góc (điểm ảnh khung xuất)."""
    from . import textfit

    size = sub_size(st, width, height)
    src_size = int(size * SOURCE_SCALE)
    bold = bool(st.get("bold", True))
    avail = line_avail(st, width)
    rows: list[tuple[str, str, int, str]] = []
    for text, kind in parts:
        text = (text or "").strip()
        if not text:
            continue
        if kind == "vi":
            if st.get("uppercase"):
                text = text.upper()
            font = line_font(text, st)
            lines, fs = textfit.fit_lines(text, font, bold, size, avail, int(st.get("max_lines") or 2),
                                          st.get("line_fit") or "wrap", float(st.get("min_scale") or 0.7))
            rows += [(ln, font, fs, kind) for ln in lines if ln]
        else:
            fs = src_size if kind == "zh" else size
            rows += [(ln.strip(), cjk_font_name(st), fs, kind) for ln in text.split("\n") if ln.strip()]
    if not rows:
        return None
    widths = [textfit.width(r[0], r[1], bold, r[2]) for r in rows]
    block_h = float(sum(r[2] for r in rows))
    cx, bottom = block_anchor(st, width, height, block_h)
    top = bottom - block_h
    boxes: list[tuple[float, float, float, float]] = []
    radius = 0.0
    rr = max(0.0, min(0.5, float(st.get("box_radius") or 0)))
    if st.get("box"):
        if st.get("box_fixed"):
            x, y, bw, bh = fixed_box(st, width, height, cx, bottom - block_h / 2)
            boxes = [(x, y, bw, bh)]
            radius = rr * min(bh, bw)
        else:
            px, py = box_pads(st, size)
            radius = rr * (size + 2 * py)
            if st.get("box_block"):     # 1 khối chung cho mọi dòng
                mw = max(widths)
                boxes = [(cx - mw / 2 - px, top - py, mw + 2 * px, block_h + 2 * py)]
            else:
                drawn = drawn_box(st)
                y = top
                for k, (r, w) in enumerate(zip(rows, widths)):
                    lh = r[2]
                    if drawn:   # nền từng dòng nối liền nhau, không chồng lên nhau (chồng -> chỗ chồng đậm hơn)
                        y0 = round(y - (py if k == 0 else 0))
                        y1 = round(y + lh + (py if k == len(rows) - 1 else 0))
                    else:
                        y0, y1 = y - py, y + lh + py
                    boxes.append((cx - w / 2 - px, y0, w + 2 * px, y1 - y0))
                    y += lh
    return {"rows": rows, "widths": widths, "size": size, "src_size": src_size, "cx": cx, "bottom": bottom,
            "top": top, "block_h": block_h, "boxes": boxes, "radius": radius}


def rounded_rect_path(x: float, y: float, w: float, h: float, r: float) -> str:
    """Hình chữ nhật bo góc bằng lệnh vẽ ASS (đường cong Bézier bậc 3)."""
    r = max(0.0, min(r, w / 2, h / 2))

    def f(v: float) -> str:
        return f"{v:.1f}".rstrip("0").rstrip(".")
    if r < 0.5:
        return f"m {f(x)} {f(y)} l {f(x + w)} {f(y)} {f(x + w)} {f(y + h)} {f(x)} {f(y + h)}"
    k = r * 0.4477          # điểm điều khiển: 1 − 0,5523 (xấp xỉ cung tròn)
    return (f"m {f(x + r)} {f(y)} l {f(x + w - r)} {f(y)} b {f(x + w - k)} {f(y)} {f(x + w)} {f(y + k)} {f(x + w)} {f(y + r)} "
            f"l {f(x + w)} {f(y + h - r)} b {f(x + w)} {f(y + h - k)} {f(x + w - k)} {f(y + h)} {f(x + w - r)} {f(y + h)} "
            f"l {f(x + r)} {f(y + h)} b {f(x + k)} {f(y + h)} {f(x)} {f(y + h - k)} {f(x)} {f(y + h - r)} "
            f"l {f(x)} {f(y + r)} b {f(x)} {f(y + k)} {f(x + k)} {f(y)} {f(x + r)} {f(y)}")


def to_ass(segs: list[Segment], style: dict, width: int = 1920, height: int = 1080, which: str = "trans") -> str:
    st = dict(style or {})
    width, height = max(16, int(width or 1920)), max(16, int(height or 1080))
    size = sub_size(st, width, height)
    outline = float(st.get("outline", 2.5)) * size / 48
    shadow = float(st.get("shadow", 1)) * size / 48
    pos = st.get("position", "bottom")
    custom = is_custom_pos(st)
    align = {"bottom": 2, "top": 8, "middle": 5}.get(pos, 2)   # "auto" = canh đáy theo vùng phụ đề cũ
    margin_v = margin_v_px(st, width, height)
    margin_h = max(0, round(width * (1 - line_avail(st, 1.0)) / 2))
    box = bool(st.get("box"))
    drawn = drawn_box(st)          # nền tự vẽ (bo góc / 1 khối / dải cố định): chữ không viền ở lớp trên
    primary = _hex_to_ass(st.get("color", "#FFFFFF"))
    secondary = _hex_to_ass(st.get("secondary_color", "#FFE14D"))
    src_col = secondary
    kara = bool(st.get("karaoke"))
    kara_mode = st.get("karaoke_mode", "sweep")
    main_second = secondary
    if kara:
        # karaoke ASS: chữ chưa tới lượt = SecondaryColour, đã đọc = PrimaryColour
        from .karaoke import DEFAULT_COLOR, ass_text as kara_text

        main_second = primary
        primary = _hex_to_ass(st.get("karaoke_color") or DEFAULT_COLOR)
    bord = ""
    if box and not drawn:
        # nền hộp vuông ôm từng dòng: libass vẽ hộp = chữ + \xbord (trái/phải) + \ybord (trên/dưới)
        border_style = 3
        outline_c = _hex_to_ass(st.get("box_color", "#000000"), 1 - float(st.get("box_opacity", 0.55)))
        back = outline_c
        pad_x, pad_y = box_pads(st, size)
        outline_v = pad_y
        bord = f"\\xbord{pad_x:.1f}\\ybord{pad_y:.1f}"
    elif drawn:
        # nền vẽ riêng (lớp 0), chữ không viền ở lớp 1
        border_style = 1
        outline_c = _hex_to_ass(st.get("outline_color", "#000000"))
        back = _hex_to_ass("#000000", 0.4)
        outline_v = 0.0
    else:
        border_style = 1
        outline_c = _hex_to_ass(st.get("outline_color", "#000000"))
        back = _hex_to_ass("#000000", 0.4)
        outline_v = outline
    bold = -1 if st.get("bold", True) else 0
    font = st.get("font", "Be Vietnam Pro")
    cjk_font = cjk_font_name(st)
    if which == "source":
        font = cjk_font
    src_size = int(size * SOURCE_SCALE)

    # karaoke + nền vuông của libass: libass vẽ nền riêng cho TỪNG TỪ (chồng lên nhau thành sọc tối) -> nền vẽ ở lớp 0
    # từ câu không có thẻ karaoke (chữ trong suốt), chữ karaoke vẽ ở lớp 1 bằng kiểu không nền
    kara_box = kara and box and not drawn
    kara_styles = ""
    if kara_box:
        kara_styles = (f"Style: Kara,{font},{size},{primary},{main_second},{outline_c},{back},{bold},0,0,0,100,100,0,0,"
                       f"1,0,0,{align},{margin_h},{margin_h},{margin_v},1\n"
                       f"Style: KaraSrc,{cjk_font},{src_size},{src_col},{src_col},{outline_c},{back},{bold},0,0,0,100,"
                       f"100,0,0,1,0,0,{align},{margin_h},{margin_h},{margin_v},1\n")
    head = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {width}
PlayResY: {height}
WrapStyle: 0
ScaledBorderAndShadow: yes
YCbCr Matrix: TV.709

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,{font},{size},{primary},{main_second},{outline_c},{back},{bold},0,0,0,100,100,0,0,{border_style},{outline_v:.1f},{0 if box else shadow:.1f},{align},{margin_h},{margin_h},{margin_v},1
Style: Source,{cjk_font},{src_size},{src_col},{src_col},{outline_c},{back},{bold},0,0,0,100,100,0,0,{border_style},{outline_v:.1f},{0 if box else shadow:.1f},{align},{margin_h},{margin_h},{margin_v},1
{kara_styles}
[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    lines = [head]
    pos_tag = f"\\an2\\pos({float(st['pos_x']) * width:.0f},{float(st['pos_y']) * height:.0f})" if custom else ""
    tag = pos_tag + bord
    pre = "{" + tag + "}" if tag else ""
    layer = 1 if drawn else 0
    box_c, box_a = _ass_color_alpha(st.get("box_color", "#000000"), float(st.get("box_opacity", 0.55)))

    def head_tags(f: str, fs: int) -> str:
        """Đổi font (câu Thái / Nhật / Hàn) và cỡ chữ (câu thu nhỏ cho vừa dòng) riêng cho câu này."""
        t = ("\\fn" + f if f != font else "") + (f"\\fs{fs}" if fs != size else "")
        return "{" + t + "}" if t else ""

    for s in segs:
        if which == "both":
            parts = [(s.trans, "vi"), (s.text, "zh")]
        elif which == "source":
            parts = [(s.text, "src")]
        else:
            parts = [(_pick(s, "trans"), "vi")]
        lay = layout_event(parts, st, width, height)
        if not lay:
            continue
        t0, t1 = fmt_ass_time(s.start), fmt_ass_time(s.end)
        rows = lay["rows"]
        vi_rows = [r for r in rows if r[3] == "vi"]
        zh_rows = [r for r in rows if r[3] != "vi"]
        vi = "\n".join(r[0] for r in vi_rows)
        zh = "\n".join(r[0] for r in zh_rows)
        if drawn and lay["boxes"]:
            # nền vẽ bằng hình (bo góc): lớp 0, toạ độ tuyệt đối trên khung xuất
            bx0 = math.floor(min(b[0] for b in lay["boxes"]))
            by0 = math.floor(min(b[1] for b in lay["boxes"]))
            path = " ".join(rounded_rect_path(b[0] - bx0, b[1] - by0, b[2], b[3], lay["radius"])
                            for b in lay["boxes"])
            lines.append(f"Dialogue: 0,{t0},{t1},Default,,0,0,0,,{{\\an7\\pos({bx0:.0f},{by0:.0f})\\bord0\\shad0"
                         f"\\1c{box_c}\\1a{box_a}\\p1}}{path}{{\\p0}}\n")
        if which == "source":
            body = zh
            lines.append(f"Dialogue: {layer},{t0},{t1},Default,,0,0,0,,{pre}{_ass_escape(body)}\n")
            continue
        ft = head_tags(vi_rows[0][1], vi_rows[0][2]) if vi_rows else ""
        txt = ft + (kara_text(vi, s.start, s.end, s.extra.get("kv"), kara_mode) if kara else _ass_escape(vi)) \
            if vi else ""
        if kara_box and vi:
            hide = "{\\1a&HFF&\\2a&HFF&}"
            back_txt = hide + ft + _ass_escape(vi) + \
                ("\\N{\\rSource" + bord + "}" + _ass_escape(zh) if zh else "")
            lines.append(f"Dialogue: 0,{t0},{t1},Default,,0,0,0,,{pre}{back_txt}\n")
            front = "{\\rKara" + pos_tag + "}" + txt + ("\\N{\\rKaraSrc\\1a&HFF&}" + _ass_escape(zh) if zh else "")
            lines.append(f"Dialogue: 1,{t0},{t1},Default,,0,0,0,,{front}\n")
            continue
        if zh:
            txt += ("\\N" if txt else "") + "{\\rSource" + bord + "}" + _ass_escape(zh)
        lines.append(f"Dialogue: {layer},{t0},{t1},Default,,0,0,0,,{pre}{txt}\n")
    return "".join(lines)


def save_subtitles(segs: list[Segment], path: str | Path, which: str = "trans", style: dict | None = None,
                   width: int = 1920, height: int = 1080) -> Path:
    path = Path(path)
    ext = path.suffix.lower()
    if ext == ".ass":
        txt = to_ass(segs, style or {}, width, height, which)
    elif ext == ".vtt":
        txt = to_vtt(segs, which)
    elif ext == ".txt":
        txt = to_txt(segs, which)
    else:
        txt = to_srt(segs, which)
    path.write_text(txt, encoding="utf-8-sig" if ext == ".srt" else "utf-8")
    return path


# ---------------------------------------------------------------- xử lý

def normalize(segs: list[Segment], min_dur: float = 0.4, gap: float = 0.02) -> list[Segment]:
    """Sắp xếp, bỏ dòng rỗng, chống chồng lấn thời gian."""
    segs = sorted((s for s in segs if (s.text.strip() or s.trans.strip())), key=lambda s: (s.start, s.end))
    for i, s in enumerate(segs):
        if s.end - s.start < min_dur:
            s.end = s.start + min_dur
        if i + 1 < len(segs) and s.end > segs[i + 1].start - gap:
            s.end = max(s.start + 0.1, segs[i + 1].start - gap)
    return segs


def shift(segs: list[Segment], offset: float, start_from: float = 0.0) -> list[Segment]:
    for s in segs:
        if s.start >= start_from:
            s.start = max(0.0, s.start + offset)
            s.end = max(s.start + 0.05, s.end + offset)
    return segs


def merge(segs: list[Segment], i: int) -> list[Segment]:
    if 0 <= i < len(segs) - 1:
        a, b = segs[i], segs[i + 1]
        joiner_zh = "" if a.text.endswith(("，", "。", "！", "？")) else "，"
        a.text = (a.text + joiner_zh + b.text).strip("，") if a.text and b.text else a.text + b.text
        a.trans = (a.trans.rstrip() + " " + b.trans.lstrip()).strip()
        a.end = b.end
        del segs[i + 1]
    return segs


def split(segs: list[Segment], i: int, at: float | None = None) -> list[Segment]:
    if not (0 <= i < len(segs)):
        return segs
    s = segs[i]
    at = at if at and s.start < at < s.end else (s.start + s.end) / 2
    ratio = (at - s.start) / max(0.001, s.duration)
    zc = max(1, round(len(s.text) * ratio)) if s.text else 0
    words = s.trans.split()
    wc = max(1, round(len(words) * ratio)) if words else 0
    b = Segment(at, s.end, s.text[zc:], " ".join(words[wc:]), s.speaker)
    s.text, s.trans, s.end = s.text[:zc], " ".join(words[:wc]), at
    segs.insert(i + 1, b)
    return segs


def remap_after_cut(segs: list[Segment], keep: list[tuple[float, float]]) -> list[Segment]:
    """Khi cắt video (giữ lại các đoạn keep), dịch thời gian phụ đề cho khớp."""
    out: list[Segment] = []
    offset_base = 0.0
    for ks, ke in keep:
        for s in segs:
            a, b = max(s.start, ks), min(s.end, ke)
            if b - a > 0.15:
                ex = dict(s.extra)
                if ex.get("kv"):   # đoạn giọng lồng của câu (karaoke) dời theo
                    kv = [[max(x, ks) - ks + offset_base, min(y, ke) - ks + offset_base]
                          for x, y in ex["kv"] if min(y, ke) > max(x, ks)]
                    if kv:
                        ex["kv"] = kv
                    else:
                        ex.pop("kv")
                out.append(Segment(a - ks + offset_base, b - ks + offset_base, s.text, s.trans, s.speaker, ex))
        offset_base += ke - ks
    return out


def follow_dub(segs: list[Segment], min_shift: float = 0.3) -> list[Segment]:
    """Phụ đề hiện theo GIỌNG LỒNG (extra["dub_at"] = đoạn giọng đang phát của câu): câu có giọng đọc sớm / bị lùi /
    đọc dài hơn phụ đề quá min_shift giây -> hiện từ lúc giọng bắt đầu tới lúc đọc xong (+0,15 s), không đè câu sau.
    Trả về bản sao (không đổi thời gian gốc của câu)."""
    out = [Segment(s.start, s.end, s.text, s.trans, s.speaker, dict(s.extra)) for s in segs]
    moved = set()
    for i, s in enumerate(out):
        at = s.extra.get("dub_at")
        if not at or len(at) < 2:
            continue
        a, b = float(at[0]), float(at[1])
        if abs(a - s.start) < min_shift and b <= s.end + min_shift:
            continue
        s.start, s.end = a, max(b + 0.15, a + 0.6)
        moved.add(i)
    if moved:
        order = sorted(range(len(out)), key=lambda q: (out[q].start, q))
        for x, y in zip(order, order[1:]):
            if (x in moved or y in moved) and out[x].end > out[y].start - 0.02:
                out[x].end = max(out[x].start + 0.3, out[y].start - 0.02)
    return out


def scale_time(segs: list[Segment], speed: float) -> list[Segment]:
    if speed and speed != 1:
        for s in segs:
            s.start /= speed
            s.end /= speed
            if s.extra.get("kv"):
                s.extra["kv"] = [[x / speed, y / speed] for x, y in s.extra["kv"]]
    return segs
