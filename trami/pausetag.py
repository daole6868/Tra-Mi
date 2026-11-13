"""Thẻ nghỉ trong bản dịch (2.4.15): người dùng gõ "Ta… [nghỉ 0,8s] ta không cố ý." -> giọng lồng đọc "Ta…", im 0,8 s
rồi đọc tiếp; phụ đề / SRT / karaoke vẫn là "Ta… ta không cố ý." (không hiện thẻ).

Lưu: seg.trans = bản dịch SẠCH (mọi chỗ khác của tool dùng như cũ) + extra["tag_pause"] = {"t": bản dịch sạch lúc đặt
thẻ, "p": [[vị trí ký tự trong bản sạch, giây nghỉ]]}. Bản dịch đổi (AI dịch lại / rút gọn / sửa chữ không có thẻ) ->
"t" khác bản dịch -> thẻ cũ không còn tác dụng (of() trả []).

Cú pháp (không phân biệt hoa thường, có / không dấu): [nghỉ], [nghỉ 0,5s], [nghỉ 0.5], [nghỉ 1 giây], [nghỉ 500ms],
[ngừng 1s], [dừng 1s], [pause 500ms]. Không ghi số = 0,5 s; 0,05–5 s.

Thẻ âm (2.4.16, chỉ bộ đọc VieNeu v3 Turbo – bộ đọc khác bỏ qua): [cười], [thở dài], [hắng giọng] (cả [chuckle], [sigh],
[clear throat], không dấu) -> extra["tag_pause"]["c"] = [[vị trí ký tự, "cười"]]; khi đọc bằng VieNeu chèn lại đúng chỗ
("<|emotion_k|>" của mô hình). Phụ đề không hiện."""
from __future__ import annotations

import re

DEFAULT = 0.5
MIN_SEC, MAX_SEC = 0.05, 5.0
WORDS = r"nghỉ|nghi|ngừng|ngung|dừng|dung|pause"
TAG_RE = re.compile(r"\s*\[\s*(?:" + WORDS + r")(?:\s*[:=]?\s*(\d+(?:[.,]\d+)?)\s*(ms|s|giây|giay|gi)?)?\s*\]\s*",
                    re.IGNORECASE)
PUNCT = ",.;:!?…，。；：！？、"
KEY = "tag_pause"
# thẻ âm VieNeu: chữ người dùng gõ -> tên chuẩn (VieNeu nhận đúng 3 tiếng này – vieneu_utils.phonemize_text)
CUES = {"cười": "cười", "cuoi": "cười", "chuckle": "cười", "laugh": "cười",
        "thở dài": "thở dài", "tho dai": "thở dài", "sigh": "thở dài",
        "hắng giọng": "hắng giọng", "hang giong": "hắng giọng", "hắc giọng": "hắng giọng", "hac giong": "hắng giọng",
        "clear throat": "hắng giọng"}
CUE_RE = re.compile(r"\s*\[\s*(" + "|".join(sorted((re.escape(k) for k in CUES), key=len, reverse=True)) +
                    r")\s*\]\s*", re.IGNORECASE)
ANY_RE = re.compile(TAG_RE.pattern + "|" + CUE_RE.pattern, re.IGNORECASE)


def _secs(num: str | None, unit: str | None) -> float:
    if not num:
        return DEFAULT
    v = float(num.replace(",", "."))
    if (unit or "").lower() == "ms":
        v /= 1000.0
    return round(min(MAX_SEC, max(MIN_SEC, v)), 2)


def has_tag(text: str) -> bool:
    return bool(ANY_RE.search(text or ""))


def _cue_name(raw: str) -> str:
    return CUES.get(" ".join(raw.lower().split()), "")


def parse(text: str) -> tuple[str, list[list]]:
    """(bản dịch sạch, [[vị trí ký tự trong bản sạch, giây]]). Thẻ ở đầu / cuối câu bỏ qua (không có gì để ngắt);
    2 thẻ liền nhau cộng dồn. Thẻ âm bị bỏ khỏi bản sạch (lấy bằng parse_all)."""
    clean, pauses, _cues = parse_all(text)
    return clean, pauses


def parse_all(text: str) -> tuple[str, list[list], list[list]]:
    """(bản dịch sạch, thẻ nghỉ [[vị trí, giây]], thẻ âm [[vị trí, "cười"]]). Thẻ âm ở đầu / cuối câu vẫn giữ."""
    text = text or ""
    out, pauses, cues, last = [], [], [], 0
    for m in ANY_RE.finditer(text):
        out.append(text[last:m.start()])
        clean_so_far = "".join(out)
        pos = len(clean_so_far.rstrip())
        # chừa 1 khoảng trắng giữa 2 phần (thẻ đã nuốt khoảng trắng 2 bên)
        if clean_so_far and not clean_so_far.endswith(" ") and m.end() < len(text):
            out.append(" ")
        cue = _cue_name(m.group(3) or "") if m.group(3) else ""
        if cue:
            cues.append([pos, cue])
        else:
            sec = _secs(m.group(1), m.group(2))
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
    cres = [[remap(pos), c] for pos, c in cues]
    return clean, res, cres


def fmt(sec: float) -> str:
    return f"[nghỉ {sec:g}s]".replace(".", ",")


def render(clean: str, pauses: list, cues: list | None = None) -> str:
    """Bản dịch kèm thẻ (để hiện / sửa trong bảng câu)."""
    if not pauses and not cues:
        return clean
    marks = sorted([(int(p), 0, fmt(float(s))) for p, s in pauses or []] +
                   [(int(p), 1, f"[{c}]") for p, c in cues or []])
    out, last = [], 0
    for pos, _k, tag in marks:
        out += [clean[last:pos].strip(), tag]
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


def cues_of(seg) -> list[list]:
    """Thẻ âm còn hiệu lực của câu ([[vị trí, "cười"]]; [] khi bản dịch đã đổi sau lúc đặt thẻ)."""
    d = seg.extra.get(KEY)
    if not isinstance(d, dict) or d.get("t") != (seg.trans or ""):
        return []
    try:
        n = len(seg.trans or "")
        return [[int(p), str(c)] for p, c in d.get("c") or [] if 0 <= int(p) <= n and c in CUES.values()]
    except (TypeError, ValueError):
        return []


def shown(seg) -> str:
    """Bản dịch hiện ở bảng câu / khung Câu đang chọn (có thẻ nếu còn hiệu lực)."""
    return render(seg.trans or "", of(seg), cues_of(seg))


def set_text(seg, text: str) -> bool:
    """Ghi bản dịch người dùng gõ (có thể kèm thẻ): seg.trans = bản sạch, thẻ vào extra. Trả True nếu có thẻ."""
    clean, pauses, cues = parse_all(text)
    seg.trans = clean
    if pauses or cues:
        d = {"t": clean, "p": pauses}
        if cues:
            d["c"] = cues
        seg.extra[KEY] = d
        return True
    seg.extra.pop(KEY, None)
    return False


def tts_text(text: str, pauses: list, fix=None, cues: list | None = None) -> str:
    """Lời đưa cho bộ đọc: thêm dấu phẩy ở chỗ đặt thẻ nghỉ (nếu chưa có dấu câu) để bộ đọc tự ngắt đúng chỗ đó.
    fix(đoạn) – sửa từng đoạn chữ giữa các thẻ (từ điển đọc, bỏ ký tự lạ…); cues – chèn lại thẻ âm "[cười]" (VieNeu)."""
    if not pauses and not cues:
        return fix(text) if fix else text
    marks = sorted([(int(p), 0, float(s)) for p, s in pauses or []] + [(int(p), 1, c) for p, c in cues or []])
    out, last = [], 0
    for pos, kind, val in marks:
        head = text[last:pos].strip()
        if fix and head:
            head = fix(head)
        if kind == 0 and head and head[-1] not in PUNCT:
            head += ","
        out.append(head)
        if kind == 1:
            out.append(f"[{val}]")
        last = pos
    tail = text[last:].strip()
    out.append(fix(tail) if fix and tail else tail)
    return " ".join(x for x in out if x)


def fractions(text: str, pauses: list, lang: str = "vi") -> list[tuple[float, float]]:
    """[(tỉ lệ vị trí theo số tiếng / chữ trong câu, giây nghỉ)] – để tìm chỗ cắt trên đoạn giọng."""
    from . import langs

    n = max(1, langs.units(text, lang))
    return [(langs.units(text[:int(p)], lang) / n, float(s)) for p, s in sorted(pauses)]


def total(pauses: list) -> float:
    return round(sum(float(s) for _p, s in pauses), 3)
