"""Thẻ nghỉ trong bản dịch (2.4.15): người dùng gõ "Ta… [nghỉ 0,8s] ta không cố ý." -> giọng lồng đọc "Ta…", im 0,8 s
rồi đọc tiếp; phụ đề / SRT / karaoke vẫn là "Ta… ta không cố ý." (không hiện thẻ).

Lưu: seg.trans = bản dịch SẠCH (mọi chỗ khác của tool dùng như cũ) + extra["tag_pause"] = {"t": bản dịch sạch lúc đặt
thẻ, "p": [[vị trí ký tự trong bản sạch, giây nghỉ]]}. Bản dịch đổi (AI dịch lại / rút gọn / sửa chữ không có thẻ) ->
"t" khác bản dịch -> thẻ cũ không còn tác dụng (of() trả []).

Cú pháp (không phân biệt hoa thường, có / không dấu): [nghỉ], [nghỉ 0,5s], [nghỉ 0.5], [nghỉ 1 giây], [nghỉ 500ms],
[ngừng 1s], [dừng 1s], [pause 500ms]. Không ghi số = 0,5 s; 0,05–5 s."""
from __future__ import annotations

import re

DEFAULT = 0.5
MIN_SEC, MAX_SEC = 0.05, 5.0
WORDS = r"nghỉ|nghi|ngừng|ngung|dừng|dung|pause"
TAG_RE = re.compile(r"\s*\[\s*(?:" + WORDS + r")(?:\s*[:=]?\s*(\d+(?:[.,]\d+)?)\s*(ms|s|giây|giay|gi)?)?\s*\]\s*",
                    re.IGNORECASE)
PUNCT = ",.;:!?…，。；：！？、"
KEY = "tag_pause"


def _secs(num: str | None, unit: str | None) -> float:
    if not num:
        return DEFAULT
    v = float(num.replace(",", "."))
    if (unit or "").lower() == "ms":
        v /= 1000.0
    return round(min(MAX_SEC, max(MIN_SEC, v)), 2)


def has_tag(text: str) -> bool:
    return bool(TAG_RE.search(text or ""))


def parse(text: str) -> tuple[str, list[list]]:
    """(bản dịch sạch, [[vị trí ký tự trong bản sạch, giây]]). Thẻ ở đầu / cuối câu bỏ qua (không có gì để ngắt);
    2 thẻ liền nhau cộng dồn."""
    text = text or ""
    out, pauses, last = [], [], 0
    for m in TAG_RE.finditer(text):
        out.append(text[last:m.start()])
        clean_so_far = "".join(out)
        pos = len(clean_so_far.rstrip())
        sec = _secs(m.group(1), m.group(2))
        # chừa 1 khoảng trắng giữa 2 phần (thẻ đã nuốt khoảng trắng 2 bên)
        if clean_so_far and not clean_so_far.endswith(" ") and m.end() < len(text):
            out.append(" ")
        if pauses and pauses[-1][0] == pos:
            pauses[-1][1] = round(min(MAX_SEC, pauses[-1][1] + sec), 2)
        else:
            pauses.append([pos, sec])
        last = m.end()
    out.append(text[last:])
    raw = "".join(out)
    clean = " ".join(raw.split())
    # vị trí theo bản đã gộp khoảng trắng: đếm lại theo số ký tự không phải khoảng trắng đứng trước
    def remap(pos: int) -> int:
        k = len("".join(raw[:pos].split()))
        if k <= 0:
            return 0
        n = 0
        for i, ch in enumerate(clean):
            if not ch.isspace():
                n += 1
                if n == k:
                    return i + 1
        return len(clean)
    res = []
    for pos, sec in pauses:
        p = remap(pos)
        if 0 < p < len(clean):
            if res and res[-1][0] == p:
                res[-1][1] = round(min(MAX_SEC, res[-1][1] + sec), 2)
            else:
                res.append([p, sec])
    return clean, res


def fmt(sec: float) -> str:
    return f"[nghỉ {sec:g}s]".replace(".", ",")


def render(clean: str, pauses: list) -> str:
    """Bản dịch kèm thẻ (để hiện / sửa trong bảng câu)."""
    if not pauses:
        return clean
    out, last = [], 0
    for pos, sec in sorted((int(p), float(s)) for p, s in pauses):
        out += [clean[last:pos].strip(), fmt(sec)]
        last = pos
    out.append(clean[last:].strip())
    return " ".join(x for x in out if x)


def of(seg) -> list[list]:
    """Thẻ nghỉ còn hiệu lực của câu ([] khi bản dịch đã đổi sau lúc đặt thẻ)."""
    d = seg.extra.get(KEY)
    if not isinstance(d, dict) or d.get("t") != (seg.trans or ""):
        return []
    try:
        return [[int(p), float(s)] for p, s in d.get("p") or [] if 0 < int(p) < len(seg.trans or "")]
    except (TypeError, ValueError):
        return []


def shown(seg) -> str:
    """Bản dịch hiện ở bảng câu / khung Câu đang chọn (có thẻ nếu còn hiệu lực)."""
    return render(seg.trans or "", of(seg))


def set_text(seg, text: str) -> bool:
    """Ghi bản dịch người dùng gõ (có thể kèm thẻ): seg.trans = bản sạch, thẻ vào extra. Trả True nếu có thẻ."""
    clean, pauses = parse(text)
    seg.trans = clean
    if pauses:
        seg.extra[KEY] = {"t": clean, "p": pauses}
        return True
    seg.extra.pop(KEY, None)
    return False


def tts_text(text: str, pauses: list) -> str:
    """Lời đưa cho bộ đọc: thêm dấu phẩy ở chỗ đặt thẻ (nếu chưa có dấu câu) để bộ đọc tự ngắt đúng chỗ đó."""
    if not pauses:
        return text
    out, last = [], 0
    for pos, _sec in sorted((int(p), float(s)) for p, s in pauses):
        head = text[last:pos].strip()
        if head and head[-1] not in PUNCT:
            head += ","
        out.append(head)
        last = pos
    out.append(text[last:].strip())
    return " ".join(x for x in out if x)


def fractions(text: str, pauses: list, lang: str = "vi") -> list[tuple[float, float]]:
    """[(tỉ lệ vị trí theo số tiếng / chữ trong câu, giây nghỉ)] – để tìm chỗ cắt trên đoạn giọng."""
    from . import langs

    n = max(1, langs.units(text, lang))
    return [(langs.units(text[:int(p)], lang) / n, float(s)) for p, s in sorted(pauses)]


def total(pauses: list) -> float:
    return round(sum(float(s) for _p, s in pauses), 3)
