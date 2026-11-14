"""Từ điển đọc (2.4.16): cách đọc riêng cho bộ đọc giọng – phụ đề / SRT / karaoke vẫn giữ chữ gốc.

Học ý từ CIT Voice Studio (Apache-2.0, "từ điển phát âm"): bộ chuẩn hoá chữ của VieNeu (sea-g2p) coi chữ viết tắt IN HOA
/ từ lạ là tiếng Anh ("Mm" -> "mi li mét", "Hm" / "Hehe" đọc kiểu Anh, "Aaa" -> "ây ây ây", "Ừmmm" -> "ừm mờ mờ"); viết
sẵn cách đọc bằng chữ Việt ("hừm", "ừm", "ha ha") thì bộ đọc đọc đúng. Thay ở tầng CHỮ trước khi đưa cho bộ đọc
(mọi bộ đọc: Edge, VieNeu, OmniVoice, VoxCPM2, KorvaTTS).

Mục từ: {"w": từ cần thay, "r": cách đọc, "c": phân biệt hoa / thường (mặc định True)}.
- Chỉ khớp NGUYÊN TỪ ("AI" không đụng "OpenAI"), từ dài thử trước từ ngắn.
- Từ chỉ gồm nguyên âm + h / m ("Hmm", "Ừmm", "Aaa") khớp cả bản kéo dài: "Hmmmm" = "Hmm", "Ừmmmm" = "Ừmm".
Luật có sẵn (chỉ câu tiếng Việt, khi bật từ điển): tiếng cười viết liền "Hahaha" / "Hehe" / "Hihi" / "Kaka" -> "ha ha ha"…
"""
from __future__ import annotations

import csv
import io
import re
import unicodedata

MAX_ENTRIES = 3000
MAX_WORD = 64
MAX_READING = 200

# Bộ mặc định = các chữ VieNeu đọc sai đã gặp (2.4.0) – người dùng sửa / xoá / thêm ở "Từ điển đọc…"
DEFAULT_ENTRIES: list[dict] = [
    {"w": "Hm", "r": "hừm", "c": False},
    {"w": "Hmm", "r": "hừm", "c": False},
    {"w": "Mm", "r": "ừm", "c": False},
    {"w": "Ừmm", "r": "ừm", "c": False},
    {"w": "Aaa", "r": "a", "c": False},
]

_LAUGH = re.compile(r"^([hk][aeiouyáàảãạéèẻẽẹíìỉĩịóòỏõọúùủũụêôơ])\1+$", re.IGNORECASE)
_WORD = re.compile(r"(?<!\w)[^\W\d_]+(?!\w)")


def _base(ch: str) -> str:
    d = unicodedata.normalize("NFD", ch.lower())
    return "".join(c for c in d if unicodedata.category(c) != "Mn")


def _stretchy(word: str) -> bool:
    """Từ chỉ có nguyên âm + h / m (tiếng cảm thán kéo dài được)."""
    return bool(word) and all(_base(c) in "aeiouyhm" for c in word if not c.isspace())


def _collapse(word: str) -> str:
    """Chữ thường, gộp các chữ giống nhau liền nhau: "Hmmmm" -> "hm", "Ừmmm" -> "ừm"."""
    w = unicodedata.normalize("NFC", word.lower())
    return re.sub(r"(.)\1+", r"\1", w)


def _has_run(word: str) -> bool:
    return bool(re.search(r"(.)\1", unicodedata.normalize("NFC", word.lower())))


def validate(raw) -> list[dict]:
    """Chuẩn hoá danh sách mục từ: bỏ dòng trống / trùng / quá dài (không báo lỗi – dòng hỏng bị bỏ)."""
    out, seen = [], set()
    for it in raw or []:
        if not isinstance(it, dict):
            continue
        w = " ".join(str(it.get("w", it.get("word", "")) or "").split())
        r = " ".join(str(it.get("r", it.get("reading", "")) or "").split())
        c = it.get("c", it.get("match_case", True))
        c = c if isinstance(c, bool) else str(c).strip().lower() not in ("0", "false", "no", "khong", "không", "")
        if not w or not r or len(w) > MAX_WORD or len(r) > MAX_READING or "[" in w or "]" in w:
            continue
        key = w if c else w.lower()
        if (c, key) in seen:
            continue
        seen.add((c, key))
        out.append({"w": w, "r": r, "c": c})
        if len(out) >= MAX_ENTRIES:
            break
    return out


def entries(settings: dict | None) -> list[dict]:
    """Mục từ đang dùng (Cài đặt "tts_dict"; chưa sửa lần nào = bộ mặc định)."""
    raw = (settings or {}).get("tts_dict")
    return validate(DEFAULT_ENTRIES if raw is None else raw)


def enabled(settings: dict | None) -> bool:
    return bool((settings or {}).get("tts_dict_on", True))


class Dict:
    """Bộ thay đã biên dịch: 1 regex cho mọi mục khớp nguyên văn + bảng khớp bản kéo dài."""

    def __init__(self, items: list[dict], rules: bool = True):
        self.items = validate(items)
        self.rules = rules
        self._exact = {e["w"]: e["r"] for e in self.items if e["c"]}
        self._fold = {e["w"].lower(): e["r"] for e in self.items if not e["c"]}
        self._stretch: dict[str, str] = {}
        for e in self.items:
            if _stretchy(e["w"]) and " " not in e["w"]:
                self._stretch.setdefault(_collapse(e["w"]), e["r"])
        if self.items:
            order = sorted(self.items, key=lambda e: len(e["w"]), reverse=True)
            alts = [re.escape(e["w"]) if e["c"] else f"(?i:{re.escape(e['w'])})" for e in order]
            self._re = re.compile(r"(?<!\w)(?:" + "|".join(alts) + r")(?!\w)")
        else:
            self._re = None

    def _hit(self, m: re.Match) -> str:
        s = m.group(0)
        return self._exact.get(s) or self._fold.get(s.lower(), s)

    def _token(self, m: re.Match, vi: bool) -> str:
        w = m.group(0)
        if self._stretch and _stretchy(w) and _has_run(w):
            r = self._stretch.get(_collapse(w))
            if r:
                return r
        if vi and self.rules:
            lm = _LAUGH.match(w)
            if lm:
                syl = lm.group(1).lower()
                return " ".join([syl] * (len(w) // len(syl)))
        return w

    def apply(self, text: str, lang: str = "vi") -> str:
        if not text:
            return text
        t = unicodedata.normalize("NFC", text)
        if self._re is not None:
            t = self._re.sub(self._hit, t)
        vi = lang == "vi"
        if self._stretch or (vi and self.rules):
            t = _WORD.sub(lambda m: self._token(m, vi), t)
        return t


_CACHE: dict = {}


def for_settings(settings: dict | None) -> Dict | None:
    """Bộ thay theo Cài đặt (None khi tắt "Từ điển đọc"). Nhớ theo nội dung để không biên dịch lại mỗi câu."""
    if not enabled(settings):
        return None
    items = entries(settings)
    key = repr(items)
    d = _CACHE.get(key)
    if d is None:
        if len(_CACHE) > 8:
            _CACHE.clear()
        d = _CACHE[key] = Dict(items)
    return d


def apply(text: str, settings: dict | None, lang: str = "vi") -> str:
    d = for_settings(settings)
    return d.apply(text, lang) if d else text


def changed_rows(segs, settings: dict | None, lang: str = "vi") -> list[int]:
    """Câu có bản dịch bị từ điển đổi cách đọc (để tích sẵn / báo số câu)."""
    from . import langs

    d = for_settings(settings)
    if not d:
        return []
    out = []
    for i, s in enumerate(segs):
        t = s.trans or ""
        if t and d.apply(t, langs.detect(t, lang)) != unicodedata.normalize("NFC", t):
            out.append(i)
    return out


def to_csv(items: list[dict]) -> str:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["word", "reading", "match_case"])
    for e in validate(items):
        w.writerow([e["w"], e["r"], "true" if e["c"] else "false"])
    return buf.getvalue()


def from_csv(text: str) -> list[dict]:
    """Đọc CSV (cột từ, cách đọc, phân biệt hoa thường – có / không dòng tiêu đề; cũng nhận "từ=cách đọc" mỗi dòng)."""
    text = (text or "").lstrip("﻿")
    rows = []
    if "," not in text and ";" not in text and "\t" not in text and "=" in text:
        for ln in text.splitlines():
            if "=" in ln:
                w, r = ln.split("=", 1)
                rows.append({"w": w, "r": r, "c": True})
        return validate(rows)
    try:
        dialect = csv.Sniffer().sniff(text[:2000], delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel
    for k, row in enumerate(csv.reader(io.StringIO(text), dialect)):
        if len(row) < 2:
            continue
        if k == 0 and row[0].strip().lower() in ("word", "từ", "tu", "w"):
            continue
        rows.append({"w": row[0], "r": row[1], "c": row[2] if len(row) > 2 and row[2].strip() else True})
    return validate(rows)
