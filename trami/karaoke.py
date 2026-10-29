"""Hiệu ứng karaoke cho phụ đề: chữ đổi màu lần lượt từng từ đúng lúc được đọc. Dùng được với MỌI font.

Thời điểm từng từ:
  - đã có mốc từng từ thật của giọng lồng (2.1.24, trami/dubwords.py: Edge trả sẵn, giọng khác nghe lại bằng
    Whisper): mỗi từ đổi màu đúng lúc được đọc (align_words ghép từ giọng đọc vào từ phụ đề theo chữ);
  - đang dùng bản lồng tiếng: theo đúng đoạn giọng Việt của câu đó (chỗ có tiếng / chỗ ngắt hơi đo từ sóng âm
    bản lồng) -> chữ chạy theo giọng đọc, dừng ở dấu phẩy khi giọng ngừng;
  - không lồng tiếng: rải đều trong thời gian hiện câu (theo độ dài từ, có nghỉ ở dấu câu).
Một chỗ tính dùng chung cho video xuất (thẻ ASS \\kf / \\k, libass vẽ) và khung xem trước trong tool.
"""
from __future__ import annotations

import re
import unicodedata

MODES = (("sweep", "Quét mượt từng chữ"), ("word", "Đổi màu từng từ"))
DEFAULT_COLOR = "#FFEF00"
PAUSE = {",": 0.45, ";": 0.45, ":": 0.45, "…": 0.6, ".": 0.7, "!": 0.7, "?": 0.7, "，": 0.45, "。": 0.7,
         "！": 0.7, "？": 0.7, "、": 0.3}
GAP_MIN = 0.18        # khoảng lặng ≥ 0,18 s trong giọng lồng = chỗ ngắt hơi
_CJK = re.compile(r"[\u2E80-\u9FFF\uF900-\uFAFF\u3040-\u30FF\uAC00-\uD7AF\u0E00-\u0E7F]")   # Hán / Nhật / Hàn / Thái (viết liền)
_WORD = re.compile(r"(\S+)(\s*)")


def tokens(text: str) -> list[tuple[str, str]]:
    """Các từ của câu kèm phần nối phía sau: " " (cách), "\\n" (xuống dòng) hoặc "" (chữ Hán: mỗi chữ 1 đơn vị,
    dấu câu dính vào chữ trước)."""
    text = (text or "").strip()
    if not text:
        return []
    if " " not in text and _CJK.search(text):
        out: list[list[str]] = []
        for line_i, line in enumerate(text.split("\n")):
            if line_i and out:
                out[-1][1] = "\n"
            for ch in line:
                if out and out[-1][1] == "" and (unicodedata.category(ch)[0] in "PS" or
                                                 unicodedata.category(ch) == "Mn"):
                    out[-1][0] += ch           # dấu câu / dấu thanh, nguyên âm trên-dưới của chữ Thái
                elif ch.strip():
                    out.append([ch, ""])
        return [(a, b) for a, b in out]
    res = []
    for m in _WORD.finditer(text):
        sp = m.group(2)
        res.append((m.group(1), "\n" if "\n" in sp else (" " if sp else "")))
    return res


def _weight(word: str) -> float:
    """Thời gian đọc tương đối: tiếng Việt mỗi từ ~1 âm tiết, từ dài đọc lâu hơn chút; chữ số đọc thành nhiều tiếng."""
    letters = sum(1 for c in word if c.isalpha())
    digits = sum(1 for c in word if c.isdigit())
    return 1.0 + 0.12 * letters + 0.9 * digits


def _pause(word: str) -> float:
    return PAUSE.get(word.rstrip("\"'”’)]»")[-1:] if word else "", 0.0)


def _fit(spans: list, start: float, end: float) -> list[tuple[float, float]]:
    """Đoạn có tiếng nằm trong thời gian hiện câu; giọng dài hơn câu thì co lại cho vừa (chữ chạy hết trước khi câu tắt)."""
    sp = sorted((float(a), float(b)) for a, b in spans if float(b) > float(a))
    if not sp or end <= start:
        return []
    lo, hi = min(sp[0][0], start), max(sp[-1][1], end)
    if lo < start or hi > end:
        k = (end - start) / max(1e-6, hi - lo)
        sp = [(start + (a - lo) * k, start + (b - lo) * k) for a, b in sp]
    return [(a, b) for a, b in sp if b - a > 1e-3]


def _walk(spans: list[tuple[float, float]], x: float) -> float:
    """Vị trí x (giây tính trên phần CÓ TIẾNG) -> thời điểm thật (bỏ qua chỗ lặng)."""
    for a, b in spans:
        d = b - a
        if x <= d:
            return a + x
        x -= d
    return spans[-1][1]


def _norm(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFC", s.lower()) if c.isalnum())


ALIGN_MIN = 0.6     # ≥ 60 % chữ của câu phụ đề khớp chữ giọng đọc mới dùng mốc thật (lời đã sửa sau khi lồng -> đoán)


def align_words(text: str, words: list | None) -> list[list[float]] | None:
    """Ghép mốc từng từ của giọng đọc [[bắt đầu, kết thúc, chữ]] vào từng từ phụ đề (tokens(text)) theo CHỮ (không
    theo số từ: Edge / Whisper có thể gộp / tách từ khác phụ đề, đọc "3" thành "ba"…). Từ không khớp chữ nào -> chia
    đều khoảng giữa 2 từ khớp bên cạnh. Khớp < ALIGN_MIN -> None (karaoke đoán như cũ)."""
    from difflib import SequenceMatcher

    toks = tokens(text)
    if not toks or not words:
        return None
    s_chars, s_tok = [], []
    for i, (t, _j) in enumerate(toks):
        for c in _norm(t):
            s_chars.append(c)
            s_tok.append(i)
    t_chars, t_time = [], []
    for a, b, w in words:
        cs = _norm(str(w))
        a, b = float(a), max(float(a), float(b))
        for j, c in enumerate(cs):
            t_chars.append(c)
            t_time.append((a + (b - a) * j / len(cs), a + (b - a) * (j + 1) / len(cs)))
    if not s_chars or not t_chars:
        return None
    sm = SequenceMatcher(None, "".join(s_chars), "".join(t_chars), autojunk=False)
    got: list[list[float] | None] = [None] * len(toks)
    hit = 0
    for blk in sm.get_matching_blocks():
        for d in range(blk.size):
            i, (a, b) = s_tok[blk.a + d], t_time[blk.b + d]
            hit += 1
            if got[i] is None:
                got[i] = [a, b]
            else:
                got[i] = [min(got[i][0], a), max(got[i][1], b)]
    if hit < ALIGN_MIN * len(s_chars):
        return None
    # từ không khớp: lấp khoảng giữa 2 từ khớp bên cạnh (theo độ dài từ)
    known = [i for i, g in enumerate(got) if g is not None]
    if not known:
        return None
    first, last = known[0], known[-1]
    for i in range(first):                       # trước từ khớp đầu tiên: chạy nhanh ngay trước nó
        got[i] = [got[first][0], got[first][0]]
    for i in range(last + 1, len(toks)):
        got[i] = [got[last][1], got[last][1]]
    i = first
    while i < last:
        if got[i + 1] is not None:
            i += 1
            continue
        j = i + 1
        while got[j] is None:
            j += 1
        lo, hi = got[i][1], max(got[i][1], got[j][0])
        ws = [_weight(toks[k][0]) for k in range(i + 1, j)]
        acc, tot = 0.0, sum(ws)
        for k, w in zip(range(i + 1, j), ws):
            a = lo + (hi - lo) * acc / tot
            acc += w
            got[k] = [a, lo + (hi - lo) * acc / tot]
        i = j
    out, prev = [], None
    for a, b in got:                             # luôn tăng dần, không chồng
        a = a if prev is None else max(a, prev)
        b = max(a, b)
        out.append([round(a, 3), round(b, 3)])
        prev = b
    return out


def word_times(text: str, start: float, end: float, spans: list | None = None,
               words: list | None = None) -> list[tuple[float, float]]:
    """(bắt đầu, kết thúc) của từng từ trong tokens(text). words: mốc thật từng từ (align_words, cùng số từ) – giọng dài
    hơn thời gian hiện câu thì co lại cho vừa như đoạn giọng."""
    toks = tokens(text)
    n = len(toks)
    if not n or end <= start:
        return [(start, end)] * n
    if words and len(words) == n:
        lo, hi = min(float(words[0][0]), start), max(float(words[-1][1]), end)
        k = (end - start) / max(1e-6, hi - lo) if (lo < start or hi > end) else 1.0
        return [(start + (float(a) - lo) * k, start + (float(b) - lo) * k) if k != 1.0 else (float(a), float(b))
                for a, b in words]
    w = [_weight(t) for t, _ in toks]
    pz = [_pause(t) for t, _ in toks[:-1]] + [0.0]
    sp = _fit(spans or [], start, end)
    if sp:
        groups = [[sp[0][0], sp[0][1]]]
        for a, b in sp[1:]:
            if a - groups[-1][1] >= GAP_MIN:
                groups.append([a, b])
            else:
                groups[-1][1] = b
        phrases, cur = [], []
        for i in range(n):
            cur.append(i)
            if pz[i] > 0 or i == n - 1:
                phrases.append(cur)
                cur = []
        if len(groups) == len(phrases) > 1:
            # giọng ngắt đúng bằng số vế câu -> mỗi vế chạy trong đúng đoạn giọng của nó
            out = [(0.0, 0.0)] * n
            for (g0, g1), ph in zip(groups, phrases):
                tot = sum(w[i] for i in ph)
                acc = 0.0
                for i in ph:
                    a = g0 + (g1 - g0) * acc / tot
                    acc += w[i]
                    out[i] = (a, g0 + (g1 - g0) * acc / tot)
            return out
        voiced = sum(b - a for a, b in sp)
        tot = sum(w)
        out, acc = [], 0.0
        for i in range(n):
            a = _walk(sp, voiced * acc / tot)
            acc += w[i]
            b = _walk(sp, voiced * acc / tot)
            out.append((a, max(a, b)))
        return out
    tot = sum(w) + sum(pz)
    k = (end - start) / tot
    out, t = [], start
    for i in range(n):
        out.append((t, t + w[i] * k))
        t += (w[i] + pz[i]) * k
    return out


def progress(times: list[tuple[float, float]], t: float, mode: str = "sweep") -> list[float]:
    """Mỗi từ đã đổi màu bao nhiêu (0..1) ở thời điểm t – giống libass vẽ \\kf (quét dần) / \\k (đổi ngay khi tới từ)."""
    if mode == "word":
        return [1.0 if t >= a else 0.0 for a, _ in times]
    return [0.0 if t <= a else 1.0 if t >= b else (t - a) / (b - a) for a, b in times]


def _esc(t: str) -> str:
    return t.replace("\\", "\\\\").replace("{", "(").replace("}", ")")


def ass_text(text: str, start: float, end: float, spans: list | None = None, mode: str = "sweep",
             words: list | None = None) -> str:
    """Câu phụ đề (đã ngắt dòng bằng \\n) -> chữ ASS có thẻ karaoke. Chỗ chưa tới lượt có thẻ \\k rỗng để chờ."""
    toks = tokens(text)
    if not toks:
        return ""
    times = word_times(text, start, end, spans, words)
    tag = "\\kf" if mode != "word" else "\\k"
    out, cur = [], 0
    for (word, join), (a, b) in zip(toks, times):
        a_cs, b_cs = round((a - start) * 100), round((b - start) * 100)
        if a_cs > cur:
            out.append(f"{{\\k{a_cs - cur}}}")
            cur = a_cs
        d = max(1, b_cs - cur)
        out.append(f"{{{tag}{d}}}{_esc(word)}")
        cur += d
        if join:
            out.append("\\N" if join == "\n" else " ")
    return "".join(out)


def voiced(env, hop: float, a: float, b: float) -> list[list[float]]:
    """Các đoạn CÓ TIẾNG trong [a, b] của sóng âm rút gọn (đỉnh mỗi `hop` giây, 0..1)."""
    import numpy as np

    env = np.asarray(env, np.float32)
    i0, i1 = max(0, int(a / hop)), min(len(env), int(np.ceil(b / hop)))
    if i1 - i0 < 2:
        return []
    x = env[i0:i1]
    top = float(x.max())
    if top <= 0.02:
        return []
    on = x > max(0.04, 0.12 * top)
    runs, k = [], 0
    while k < len(on):
        if on[k]:
            j = k
            while j < len(on) and on[j]:
                j += 1
            runs.append([k, j])
            k = j
        else:
            k += 1
    merged: list[list[int]] = []
    for r in runs:                                    # nối chỗ lặng rất ngắn (giữa 2 tiếng của 1 câu nói liền)
        if merged and (r[0] - merged[-1][1]) * hop < 0.12:
            merged[-1][1] = r[1]
        else:
            merged.append(r)
    out = [[max(a, (i0 + s) * hop), min(b, (i0 + e) * hop)] for s, e in merged if (e - s) * hop >= 0.08]
    return [[round(s, 3), round(e, 3)] for s, e in out if e > s]
