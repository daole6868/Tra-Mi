"""Nghe 2 bộ rồi so (2.1.0): sau khi bộ nghe chính (Qwen3-ASR / FireRedASR2) nghe + tách câu xong, bộ thứ 2 nghe lại
ĐÚNG các đoạn đó; so chữ từng câu (bỏ dấu câu) – câu 2 bộ nghe khác nhau thường là chỗ khó nghe thật (tên riêng, chữ
đồng âm, nói nhanh, lẫn nhạc). Có AI (Cài đặt `asr_dual_ai`): AI đọc ngữ cảnh chọn bản đúng (A / B / ghép từ chữ của 2
bản); không có AI / AI không chắc -> câu tô xanh + chữ của bộ 2 trong chú thích để bạn nghe lại.

Hàm thuần (không gọi mô hình) – pipeline.dual_listen gọi tiến trình nghe rồi dùng các hàm ở đây.
"""
from __future__ import annotations

import json
import re
from difflib import SequenceMatcher

CORE = re.compile(r"[㐀-鿿A-Za-z0-9]")
CJK = re.compile(r"[㐀-鿿]")


def core(text: str) -> str:
    """Chữ để so: chữ Hán + chữ Latin / số (chữ thường), bỏ dấu câu / khoảng trắng."""
    return "".join(ch.lower() for ch in (text or "") if CORE.match(ch))


def alt_spans(segs, max_len: float = 28.0, gap: float = 1.5, pad: float = 0.25,
              total: float = 0.0) -> list[tuple[float, float, list[int]]]:
    """Gom các câu liền nhau (nghỉ ≤ gap, khúc ≤ max_len giây) thành khúc cho bộ nghe thứ 2: [(đầu, cuối, [số câu])].
    Bỏ câu 🎵 / câu không có chữ."""
    out: list[tuple[float, float, list[int]]] = []
    for i, s in enumerate(segs):
        if not core(s.text) or s.extra.get("song"):
            continue
        a, b = max(0.0, s.start - pad), s.end + pad
        if total:
            b = min(b, total)
        if out and a - out[-1][1] <= gap and b - out[-1][0] <= max_len:
            out[-1] = (out[-1][0], b, out[-1][2] + [i])
        else:
            out.append((a, b, [i]))
    return out


def _times(text: str, words: list, a: float, b: float) -> list[float]:
    """Giờ (giữa chữ) từng chữ của `core(text)`: theo mốc từng chữ của bộ nghe, không có thì rải đều trong khúc."""
    c = core(text)
    if not c:
        return []
    mids: list[float] = []
    for w, x, y in words or []:
        w = core(str(w))
        for k in range(len(w)):
            mids.append(a + float(x) + (float(y) - float(x)) * (k + 0.5) / len(w))
    if len(mids) == len(c):
        return mids
    return [a + (b - a) * (k + 0.5) / len(c) for k in range(len(c))]


def assign(segs, spans, items) -> dict[int, str]:
    """Chữ bộ nghe thứ 2 cho từng câu: so chuỗi chữ của các câu trong khúc với chuỗi chữ bộ 2 nghe được (khớp từng
    chữ, không dựa vào mốc giờ – mép câu lệch vài chục ms không làm sai); chữ thừa nằm giữa 2 câu theo giờ của chữ đó.
    Trả {số câu: chữ bộ 2 (đã bỏ dấu câu)}."""
    out: dict[int, str] = {}
    for (a, b, rows), it in zip(spans, items):
        alt = core(it.get("text") or "")
        tm = _times(it.get("text") or "", it.get("words") or [], a, b)
        p, owner = "", []
        for r in rows:
            c = core(segs[r].text)
            p += c
            owner += [r] * len(c)
        got = {r: [] for r in rows}
        if not p:
            continue
        for op, i1, i2, j1, j2 in SequenceMatcher(None, p, alt, autojunk=False).get_opcodes():
            if op == "equal":
                for k in range(i2 - i1):
                    got[owner[i1 + k]].append(alt[j1 + k])
                continue
            span_rows = owner[i1:i2]
            for k in range(j1, j2):
                if span_rows:   # thay: chia theo tỉ lệ cho các câu có chữ bị thay
                    r = span_rows[min(len(span_rows) - 1, (k - j1) * len(span_rows) // max(1, j2 - j1))]
                else:           # chèn giữa 2 câu: theo giờ của chữ chèn
                    left = owner[i1 - 1] if i1 > 0 else owner[0]
                    right = owner[i1] if i1 < len(owner) else owner[-1]
                    r = left
                    if right != left and k < len(tm):
                        r = right if abs(tm[k] - segs[right].start) < abs(tm[k] - segs[left].end) else left
                got[r].append(alt[k])
        for r in rows:
            out[r] = "".join(got[r])
    return out


def differs(a: str, b: str) -> bool:
    return core(a) != core(b)


def merge_alt(text: str, new_core: str) -> str:
    """Thay chữ của câu bằng `new_core` mà giữ dấu câu của câu gốc đúng chỗ (dấu nằm sau chữ nào thì vẫn sau chữ đó)."""
    old = core(text)
    if not new_core:
        return text
    marks: dict[int, str] = {}   # số chữ đứng trước -> dấu / khoảng trắng theo sau
    n = 0
    lead = ""
    for ch in text:
        if CORE.match(ch):
            n += 1
        elif n == 0:
            lead += ch
        else:
            marks[n] = marks.get(n, "") + ch
    pos_map: dict[int, int] = {0: 0}   # số chữ cũ đứng trước dấu -> số chữ mới tương ứng
    for op, i1, i2, j1, j2 in SequenceMatcher(None, old, new_core, autojunk=False).get_opcodes():
        if op == "insert":          # chữ mới chen vào sau dấu cũ? -> dấu đứng sau phần chèn ("你好呀，世界")
            pos_map[i1] = j2
            continue
        for k in range(i1 + 1, i2 + 1):
            pos_map[k] = j1 + (k - i1) if op == "equal" else (
                j2 if k == i2 else j1 + round((k - i1) * (j2 - j1) / (i2 - i1)))
    new_marks: dict[int, str] = {}
    for k, m in marks.items():
        j = pos_map.get(k, len(new_core))
        new_marks[j] = new_marks.get(j, "") + m
    # chữ Latin trong câu gốc viết hoa thế nào thì giữ (core() đã hạ chữ thường)
    up = {c.lower(): c for c in text if c.isascii() and c.isalpha()}
    out = lead
    for j, ch in enumerate(new_core, 1):
        out += up.get(ch, ch) + new_marks.get(j, "")
    return out


PICK_PROMPT = """Bạn là biên tập viên phụ đề tiếng Trung. Hai bộ nhận dạng giọng nói (A và B) chép cùng 1 câu thoại trong
phim ra khác nhau. Dựa vào câu trước / câu sau, hồ sơ phim (tên nhân vật, thuật ngữ) và nghĩa của câu, chọn bản đúng.

Trả về JSON: {"<số>": {"chon": "A" | "B" | "C", "cau": "<câu đúng>", "chac": true | false, "ly_do": "<ngắn>"}}
- "A" / "B": 1 trong 2 bản đúng nguyên văn ("cau" chép đúng bản đó).
- "C": cả 2 đều sai 1 phần – ghép lại từ chữ của A và B (CHỈ dùng chữ có trong A hoặc B, không thêm chữ mới).
- "chac": false nếu không đủ ngữ cảnh để biết (tool sẽ đánh dấu cho người xem nghe lại).
- Chú ý tên riêng, chữ đồng âm (他/她, 的/得, 在/再…), số. Không dịch, không sửa văn phong, giữ đúng lời nói."""


def pick_items(segs, rows: list[int], alts: dict[int, str], ctx: int = 2) -> dict[str, dict]:
    """Dữ liệu gửi AI: mỗi câu khác nhau kèm 2 câu trước / sau."""
    out: dict[str, dict] = {}
    for n, r in enumerate(rows, 1):
        out[str(n)] = {"truoc": [segs[k].text for k in range(max(0, r - ctx), r)],
                       "A": segs[r].text, "B": alts[r],
                       "sau": [segs[k].text for k in range(r + 1, min(len(segs), r + 1 + ctx))]}
    return out


def parse_pick(reply: str, segs, rows: list[int], alts: dict[int, str]) -> dict[int, tuple[str, str, bool, str]]:
    """{số câu: (chọn, chữ mới (core), chắc?, lý do)} – chỉ nhận "C" khi mọi chữ có trong A hoặc B và giống ≥ 60 %."""
    from .skills import _json

    try:
        d = _json(reply)
    except ValueError:
        return {}
    out: dict[int, tuple[str, str, bool, str]] = {}
    for k, v in (d or {}).items():
        if not str(k).isdigit() or not (1 <= int(k) <= len(rows)) or not isinstance(v, dict):
            continue
        r = rows[int(k) - 1]
        a, b = core(segs[r].text), core(alts[r])
        pick = str(v.get("chon") or "").strip().upper()[:1]
        sure = v.get("chac") is not False and str(v.get("chac")).lower() != "false"
        why = str(v.get("ly_do") or "")[:160]
        new = core(str(v.get("cau") or ""))
        if pick == "A":
            out[r] = ("A", a, sure, why)
        elif pick == "B":
            out[r] = ("B", b, sure, why)
        elif pick == "C" and new:
            pool = set(a) | set(b)
            close = max(SequenceMatcher(None, new, a).ratio(), SequenceMatcher(None, new, b).ratio())
            if all(ch in pool for ch in new) and close >= 0.6:
                out[r] = ("C", new, sure, why)
    return out


def pick_payload(segs, rows, alts, profile: str = "") -> str:
    return ((("HỒ SƠ PHIM:\n" + profile.strip() + "\n\n") if profile.strip() else "")
            + "CÁC CÂU CẦN CHỌN:\n" + json.dumps(pick_items(segs, rows, alts), ensure_ascii=False, indent=0))
