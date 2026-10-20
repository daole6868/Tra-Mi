"""Mô hình phụ đề và đọc/ghi SRT, VTT, ASS, TXT."""
from __future__ import annotations

import re
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


def wrap_line(text: str, max_chars: int) -> str:
    """Ngắt dòng tiếng Việt cân đối (tối đa 2 dòng) nếu quá dài."""
    text = " ".join(text.split())
    if max_chars <= 0 or len(text) <= max_chars or "\n" in text:
        return text
    words = text.split(" ")
    if len(words) < 2:
        return text
    best, best_diff = None, 1e9
    for i in range(1, len(words)):
        a, b = " ".join(words[:i]), " ".join(words[i:])
        diff = abs(len(a) - len(b))
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


def _ass_color_alpha(color: str, opacity: float) -> tuple[str, str]:
    full = _hex_to_ass(color, 1 - float(opacity))          # &HAABBGGRR
    return f"&H{full[4:]}&", f"&H{full[2:4]}&"


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
    margin_h = int(width * 0.05)
    box = bool(st.get("box"))
    fixed = box and bool(st.get("box_fixed"))
    primary = _hex_to_ass(st.get("color", "#FFFFFF"))
    secondary = _hex_to_ass(st.get("secondary_color", "#FFE14D"))
    bord = ""
    if box and not fixed:
        # nền hộp ôm từng dòng: libass vẽ hộp = chữ + \xbord (trái/phải) + \ybord (trên/dưới)
        border_style = 3
        outline_c = _hex_to_ass(st.get("box_color", "#000000"), 1 - float(st.get("box_opacity", 0.55)))
        back = outline_c
        pad_x, pad_y = box_pads(st, size)
        outline_v = pad_y
        bord = f"\\xbord{pad_x:.1f}\\ybord{pad_y:.1f}"
    elif fixed:
        # dải nền cố định vẽ riêng (lớp 0), chữ không viền ở lớp 1
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
    max_chars = int(width / (size * 0.55)) if size else 42
    src_size = int(size * SOURCE_SCALE)

    head = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {width}
PlayResY: {height}
WrapStyle: 0
ScaledBorderAndShadow: yes
YCbCr Matrix: TV.709

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,{font},{size},{primary},{secondary},{outline_c},{back},{bold},0,0,0,100,100,0,0,{border_style},{outline_v:.1f},{0 if box else shadow:.1f},{align},{margin_h},{margin_h},{margin_v},1
Style: Source,{cjk_font},{src_size},{secondary},{secondary},{outline_c},{back},{bold},0,0,0,100,100,0,0,{border_style},{outline_v:.1f},{0 if box else shadow:.1f},{align},{margin_h},{margin_h},{margin_v},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    lines = [head]
    tag = (f"\\an2\\pos({float(st['pos_x']) * width:.0f},{float(st['pos_y']) * height:.0f})" if custom else "") + bord
    pre = "{" + tag + "}" if tag else ""
    layer = 1 if fixed else 0

    def emit(s: Segment, txt: str, n_main: int, n_src: int):
        t0, t1 = fmt_ass_time(s.start), fmt_ass_time(s.end)
        if fixed:
            block_h = n_main * size + n_src * src_size
            cx, bottom = block_anchor(st, width, height, block_h)
            x, y, bw, bh = fixed_box(st, width, height, cx, bottom - block_h / 2)
            c, a = _ass_color_alpha(st.get("box_color", "#000000"), float(st.get("box_opacity", 0.55)))
            lines.append(f"Dialogue: 0,{t0},{t1},Default,,0,0,0,,{{\\an7\\pos({x:.0f},{y:.0f})\\bord0\\shad0"
                         f"\\1c{c}\\1a{a}\\p1}}m 0 0 l {bw:.0f} 0 {bw:.0f} {bh:.0f} 0 {bh:.0f}{{\\p0}}\n")
        lines.append(f"Dialogue: {layer},{t0},{t1},Default,,0,0,0,,{pre}{txt}\n")

    for s in segs:
        body = _pick(s, "source" if which == "source" else "trans").strip()
        if which == "both":
            vi = wrap_line(s.trans.strip(), max_chars)
            zh = s.text.strip()
            if st.get("uppercase"):
                vi = vi.upper()
            if not vi and not zh:
                continue
            txt = _ass_escape(vi)
            if zh:
                txt += "\\N{\\rSource" + bord + "}" + _ass_escape(zh)
            emit(s, txt, vi.count("\n") + 1 if vi else 0, zh.count("\n") + 1 if zh else 0)
            continue
        if not body:
            continue
        if which != "source":
            body = wrap_line(body, max_chars)
        if st.get("uppercase"):
            body = body.upper()
        emit(s, _ass_escape(body), body.count("\n") + 1, 0)
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
                out.append(Segment(a - ks + offset_base, b - ks + offset_base, s.text, s.trans, s.speaker, dict(s.extra)))
        offset_base += ke - ks
    return out


def scale_time(segs: list[Segment], speed: float) -> list[Segment]:
    if speed and speed != 1:
        for s in segs:
            s.start /= speed
            s.end /= speed
    return segs
