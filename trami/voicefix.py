"""Sửa lỗi giọng đọc AI (VieNeu / OmniVoice) trước khi ghép vào bản lồng tiếng.

- `spoken_vi`: đọc số thành chữ ("90.000" -> "chín mươi nghìn"). Chỉ đổi lời gửi cho bộ đọc, phụ đề giữ nguyên.
  Bộ đọc AI gặp chữ số hay đọc sai hoặc phát ra tiếng "bíp".
- `voice_problem`: phát hiện file giọng hỏng (im lặng, tiếng ù một chiều, tiếng bíp / còi một tần số) để đọc lại.
- `speech_level` + `level_gains`: câu nào nhỏ hơn hẳn các câu khác cùng giọng thì tăng lên cho đều.
- `lone_interjection`: câu CHỈ gồm từ cảm thán trong danh sách ("A!", "Ừm.", "Ái!", "Hả?") -> xoá trước khi lồng tiếng Việt;
  `strip_interjections`: bỏ vế chỉ gồm từ cảm thán trong câu dài ("Ừm, đúng vậy." -> "Đúng vậy."). Danh sách từ do người
  dùng điền (Cài đặt `interj_words`, 2.0.96) – chỉ lọc đúng các từ đó.
"""
from __future__ import annotations

import re

import numpy as np

_DIG = ("không", "một", "hai", "ba", "bốn", "năm", "sáu", "bảy", "tám", "chín")
_UNITS = ("", " nghìn", " triệu")


def _three(n: int, pad: bool) -> str:
    """Đọc số 0..999. pad: nhóm đứng sau nhóm lớn hơn (1.005 -> "không trăm linh năm")."""
    tr, rest = divmod(n, 100)
    ch, dv = divmod(rest, 10)
    out = []
    if tr or pad:
        out += [_DIG[tr], "trăm"]
    if ch == 0:
        if dv and (tr or pad):
            out.append("linh")
    elif ch == 1:
        out.append("mười")
    else:
        out += [_DIG[ch], "mươi"]
    if dv:
        if dv == 1 and ch >= 2:
            out.append("mốt")
        elif dv == 4 and ch >= 2:
            out.append("tư")
        elif dv == 5 and ch >= 1:
            out.append("lăm")
        else:
            out.append(_DIG[dv])
    return " ".join(out)


def _int(n: int, pad: bool = False) -> str:
    if n >= 10 ** 9:                       # tỷ: đọc phần tỷ rồi phần còn lại ("hai nghìn năm trăm tỷ")
        q, r = divmod(n, 10 ** 9)
        return _int(q) + " tỷ" + (" " + _int(r, pad=True) if r else "")
    groups = []
    while n:
        n, g = divmod(n, 1000)
        groups.append(g)
    words = []
    for i in range(len(groups) - 1, -1, -1):
        if groups[i]:
            words.append(_three(groups[i], pad=pad or bool(words)) + _UNITS[i])
    return " ".join(words)


def read_int(s: str) -> str:
    """Đọc số nguyên viết liền ("1250000" -> "một triệu hai trăm năm mươi nghìn"); số có số 0 đầu đọc từng chữ số."""
    s = s.lstrip("+")
    if len(s) > 1 and s.startswith("0"):
        return " ".join(_DIG[int(c)] for c in s)
    n = int(s)
    return _int(n) if n else "không"


def _read_decimal(intp: str, frac: str) -> str:
    frac_words = " ".join(_DIG[int(c)] for c in frac) if frac.startswith("0") or len(frac) > 3 else read_int(frac)
    return f"{read_int(intp)} phẩy {frac_words}"


# số đứng một mình (không dính chữ cái, không phải giờ 10:30, ngày 12/10, phiên bản 1.2.3)
_NUM = re.compile(r"(?<![\w.,:/])(\d{1,3}(?:\.\d{3})+|\d+)(?:,(\d+))?(?![\w:/]|[.,]\d)")
_PCT = re.compile(r"(\d)\s*%")
_DATE = re.compile(r"(?<![\w/])(\d{1,2})/(\d{1,2})(?:/(\d{4}))?(?![\w/])")
_TIME = re.compile(r"(?<![\w:])(\d{1,2}):(\d{2})(?![\w:])")
_ORD = re.compile(r"\b(thứ|hạng|lần thứ)\s+(\d{1,2})\b", re.IGNORECASE)


def spoken_vi(text: str) -> str:
    """Đổi số trong câu tiếng Việt thành chữ để bộ đọc AI đọc đúng (chỉ những số đọc được 1 cách duy nhất)."""
    if not text or not any(c.isdigit() for c in text):
        return text
    t = _ORD.sub(lambda m: f"{m.group(1)} " + {"1": "nhất", "4": "tư"}.get(m.group(2), read_int(m.group(2))), text)
    t = _PCT.sub(r"\1 phần trăm", t)

    def date(m: re.Match) -> str:
        d, mo = int(m.group(1)), int(m.group(2))
        before = m.string[:m.start()].rstrip().lower()
        if not (1 <= d <= 31 and 1 <= mo <= 12) or not (m.group(3) or before.endswith(("ngày", "hôm"))):
            return m.group(0)       # "3/4" không có năm, không có chữ "ngày" đứng trước: có thể là phân số / tỉ số
        return f"{read_int(str(d))} tháng {read_int(str(mo))}" + (f" năm {read_int(m.group(3))}" if m.group(3) else "")

    def clock(m: re.Match) -> str:
        h, mi = int(m.group(1)), int(m.group(2))
        if h > 24 or mi > 59:
            return m.group(0)
        return f"{read_int(str(h))} giờ" + (f" {read_int(str(mi))}" if mi else "")
    t = _DATE.sub(date, t)
    t = _TIME.sub(clock, t)
    t = re.sub(r"\$\s*(\d[\d.,]*)", r"\1 đô la", t)
    t = re.sub(r"(\d)\s*\$", r"\1 đô la", t)

    def num(m: re.Match) -> str:
        whole = m.group(1).replace(".", "")
        if len(whole) > 15:
            return m.group(0)
        return _read_decimal(whole, m.group(2)) if m.group(2) else read_int(whole)
    return _NUM.sub(num, t)


# ------------------------------------------------------------------ kiểm tra file giọng

def _frames(a: np.ndarray, sr: int, ms: float = 40.0) -> np.ndarray:
    n = max(16, int(sr * ms / 1000))
    a = np.asarray(a, dtype=np.float32).reshape(-1)
    return a[: len(a) // n * n].reshape(-1, n)


def speech_level(a: np.ndarray, sr: int) -> float:
    """Độ to của phần có tiếng nói (RMS các khung 40 ms đủ to, bỏ khoảng lặng và lệch một chiều)."""
    f = _frames(a, sr)
    if not len(f):
        return 0.0
    f = f - f.mean(axis=1, keepdims=True)
    rms = np.sqrt((f ** 2).mean(axis=1))
    on = rms[rms > max(0.002, float(rms.max()) * 0.1)]
    return float(np.sqrt((on ** 2).mean())) if len(on) else 0.0


def voice_problem(a: np.ndarray, sr: int) -> str | None:
    """None nếu giống tiếng nói; không thì trả lý do. Chỉ bắt lỗi rõ ràng của bộ đọc AI (không phải chấm điểm phát âm):
    im lặng, lệch một chiều (DC), tiếng bíp / còi một tần số kéo dài hoặc lặp lại."""
    f = _frames(a, sr)
    if not len(f):
        return "không có tiếng"
    rms = np.sqrt((f ** 2).mean(axis=1))
    top = float(rms.max())
    loud = rms > max(0.003, top * 0.1)
    if int(loud.sum()) < 3 or top < 0.003:
        return "toàn khoảng lặng"
    lf = f[loud]
    dc = (lf.mean(axis=1) ** 2) / np.maximum((lf ** 2).mean(axis=1), 1e-12)
    if float((dc > 0.7).mean()) > 0.8:
        return "tiếng ù (lệch một chiều)"
    win = np.hanning(f.shape[1]).astype(np.float32)
    spec = np.abs(np.fft.rfft((f - f.mean(axis=1, keepdims=True)) * win, axis=1)) ** 2
    tot = np.maximum(spec.sum(axis=1), 1e-12)
    pk = spec.argmax(axis=1)
    hz = pk * sr / f.shape[1]
    # tỉ lệ năng lượng dồn vào ±2 vạch quanh đỉnh: tiếng nói (nhiều hoạ âm, phụ âm) thấp; tiếng bíp gần 1
    idx = np.clip(pk[:, None] + np.arange(-2, 3)[None, :], 0, spec.shape[1] - 1)
    conc = np.take_along_axis(spec, idx, axis=1).sum(axis=1) / tot
    tone = loud & (conc > 0.8) & (hz >= 500)
    if float(tone[loud].mean()) > 0.5:
        return "tiếng bíp thay cho lời"
    # còi / bíp kéo dài >= 0,32 s: đỉnh đứng yên trong dải 1–4 kHz, gần như mọi năng lượng ở đó
    run = 0
    for i in range(len(f)):
        steady = i > 0 and abs(hz[i] - hz[i - 1]) <= 40
        if tone[i] and 1000 <= hz[i] <= 4000 and (steady or run == 0):
            run += 1
            if run >= 8:
                return "tiếng còi kéo dài"
        else:
            run = 1 if (tone[i] and 1000 <= hz[i] <= 4000) else 0
    # >= 2 tiếng bíp ngắn cùng tần số trong 1 giây đầu
    first = np.flatnonzero(tone[: int(1.0 / 0.04)])
    if len(first) >= 3:
        bursts = np.split(first, np.flatnonzero(np.diff(first) > 1) + 1)
        if len(bursts) >= 2 and float(np.ptp(hz[first])) <= 60:
            return "tiếng bíp ở đầu câu"
    return None


def level_gains(levels: dict, groups: dict, max_boost_db: float = 9.0, tol_db: float = 1.0) -> dict:
    """Hệ số khuếch đại cho câu quá nhỏ. levels: {câu: độ to}; groups: {câu: mã giọng}.
    Mỗi giọng lấy trung vị các câu của nó (cần >= 3 câu); giọng nhỏ hơn hẳn các giọng khác (> 3 dB) được kéo lên
    gần mức chung. Chỉ tăng, không giảm (câu to đã có bộ chặn đỉnh)."""
    by: dict = {}
    for k, v in levels.items():
        if 0.003 <= v <= 0.5:
            by.setdefault(groups.get(k, ""), []).append(v)
    allv = [v for vs in by.values() for v in vs]
    if len(allv) < 3:
        return {}
    overall = float(np.median(allv))
    target = {}
    for g, vs in by.items():
        t = float(np.median(vs)) if len(vs) >= 3 else overall
        target[g] = max(t, overall * 10 ** (-3 / 20))
    out = {}
    lim = 10 ** (max_boost_db / 20)
    for k, v in levels.items():
        t = target.get(groups.get(k, ""), overall)
        if 0.003 <= v < t * 10 ** (-tol_db / 20):
            out[k] = min(t / v, lim)
    return out


# tiếng cảm thán / tiếng đệm (không mang nghĩa) – câu chỉ gồm 1–2 tiếng này thì bỏ khi lồng tiếng Việt.
# KHÔNG có từ có nghĩa dễ trùng ("ai" = ai đó, "ơi" = dạ, "vâng", "dạ", "ừ thì"…) để không xoá nhầm câu trả lời.
_WORD = re.compile(r"[^\W\d_]+", re.UNICODE)


# danh sách mặc định (người dùng sửa ở tab Lồng tiếng mục 3): mỗi mục 1 từ / cụm, cách nhau dấu phẩy
INTERJ_DEFAULT = ("a, á, à, ạ, ơ, ờ, ớ, ồ, ô, ừ, ừm, ưm, ứ, hừ, hừm, hử, hả, hứ, hm, hmm, ha ha, hì hì, hi hi, hehe, "
                  "ui, ui da, úi, ối, ôi, ái, ái da, chà, chậc, chặc, tặc, xì, ặc, phù, oa, uầy, ah, oh, uh, um")


def interj_list(settings: dict | None = None) -> list[tuple[str, ...]]:
    """Danh sách từ / cụm cảm thán cần lọc (Cài đặt `interj_words`, chưa đặt -> INTERJ_DEFAULT), dạng bộ từ thường."""
    import unicodedata

    raw = (settings or {}).get("interj_words")
    raw = INTERJ_DEFAULT if raw is None else str(raw)
    out = set()
    for it in re.split(r"[,;\n]+", raw):
        w = tuple(_WORD.findall(unicodedata.normalize("NFC", it).lower()))
        if w:
            out.add(w)
    return sorted(out, key=lambda x: (-len(x), x))


def _covered(words: list[str], phrases: list[tuple[str, ...]]) -> bool:
    """Dãy từ ghép được hoàn toàn từ các từ / cụm trong danh sách."""
    ok = [True] + [False] * len(words)
    for i in range(1, len(words) + 1):
        for ph in phrases:
            n = len(ph)
            if n <= i and ok[i - n] and tuple(words[i - n:i]) == ph:
                ok[i] = True
                break
    return bool(words) and ok[-1]


def lone_interjection(text: str, phrases: list[tuple[str, ...]] | None = None) -> bool:
    """Câu CHỈ gồm từ cảm thán trong danh sách: "A!", "Ừm…", "Ái!", "Hả?", "Ha ha". "Ừm, đúng là như vậy." hay "A Lan"
    -> False (có lời thật). phrases None = danh sách mặc định."""
    import unicodedata

    t = unicodedata.normalize("NFC", text or "").lower()
    if any(c.isdigit() for c in t):
        return False
    words = _WORD.findall(t)
    return 1 <= len(words) <= 8 and _covered(words, phrases if phrases is not None else interj_list())


_PIECE = re.compile(r"[^,.!?…;:]+[,.!?…;:]*|[,.!?…;:]+")
_END = "!?.…"


def strip_interjections(text: str, phrases: list[tuple[str, ...]] | None = None) -> str:
    """Bỏ các vế (ngăn bởi dấu câu) CHỈ gồm từ cảm thán trong danh sách khỏi câu có lời thật: "Ừm, đúng là như vậy."
    -> "Đúng là như vậy.", "Đi thôi, ha ha!" -> "Đi thôi!". Câu toàn cảm thán / không có vế nào như vậy -> giữ nguyên."""
    import unicodedata

    phrases = phrases if phrases is not None else interj_list()
    t = unicodedata.normalize("NFC", text or "")
    pieces = _PIECE.findall(t)
    keep, removed_end = [], ""
    for pc in pieces:
        body = pc.rstrip(",.!?…;: ")
        words = _WORD.findall(body.lower())
        if words and not any(c.isdigit() for c in body) and _covered(words, phrases):
            if keep and not keep[-1].rstrip()[-1:] in _END:
                removed_end = pc.strip()[len(body.strip()):].strip()
            continue
        keep.append(pc)
        removed_end = ""
    if len(keep) == len(pieces) or not any(_WORD.search(x) for x in keep):
        return text
    if removed_end and any(c in _END for c in removed_end):   # bỏ vế cuối: câu kết bằng dấu của vế đó
        last = keep[-1].rstrip()
        keep[-1] = last.rstrip(",;:") + "".join(c for c in removed_end if c in _END)
    out = re.sub(r"\s{2,}", " ", "".join(keep)).strip()
    first = next((c for c in t if c.isalpha()), "")
    i = next((k for k, c in enumerate(out) if c.isalpha()), None)
    if i is not None and (first.isupper() or not first) and out[i].islower():
        out = out[:i] + out[i].upper() + out[i + 1:]
    return out