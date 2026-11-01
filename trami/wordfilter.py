"""Lọc từ phụ đề (2.1.33): xoá đúng cụm chữ người dùng điền ở mọi câu – logo / watermark bị OCR đọc dính vào câu
("@纵横排队一个个的进来" -> "排队一个个的进来"), câu chỉ có cụm đó thì bỏ cả câu. Giờ các câu không đổi.

So khớp: đúng thứ tự chữ, không phân biệt chữ hoa / thường, chữ toàn khổ / nửa khổ ("＠" = "@") và khoảng trắng giữa
các chữ ("@ 纵横" = "@纵横")."""
from __future__ import annotations

import re
import unicodedata

SEP_RE = re.compile(r"[\n\r,，;；|]+")
_WORD_RE = re.compile(r"\w", re.UNICODE)                  # câu còn chữ / số
_CJK_RE = re.compile(r"[㐀-鿿]")
_LEAD_RE = re.compile(r"^[\s,，、;；:：·•\-–—|/]+")          # dấu ngắt còn sót ở đầu câu sau khi bỏ chữ
_TRAIL_RE = re.compile(r"[\s,，、;；:：·•\-–—|/]+$")         # ... ở cuối câu
_DUP_RE = re.compile(r"([,，、;；:：])(?:\s*[,，、;；:：])+")    # "排队，，进来" -> "排队，进来"
_WS_RE = re.compile(r"\s{2,}")


def parse(text: str | None) -> list[str]:
    """Ô "Lọc từ": các cụm cách nhau bằng dấu phẩy / chấm phẩy / | / xuống dòng -> danh sách (bỏ trùng, giữ thứ tự)."""
    out: list[str] = []
    for w in SEP_RE.split(text or ""):
        w = w.strip()
        if w and w not in out:
            out.append(w)
    return out


def _norm(ch: str) -> str:
    return unicodedata.normalize("NFKC", ch).casefold()


def _norm_map(text: str) -> tuple[str, list[int]]:
    """Chuỗi để so khớp (NFKC, chữ thường, bỏ khoảng trắng) + vị trí ký tự gốc của từng ký tự."""
    out, idx = [], []
    for i, ch in enumerate(text):
        for c in _norm(ch):
            if not c.isspace():
                out.append(c)
                idx.append(i)
    return "".join(out), idx


def _key(word: str) -> str:
    return "".join(c for c in _norm(word) if not c.isspace())


def find(text: str, words: list[str]) -> list[tuple[int, int]]:
    """Các đoạn [đầu, cuối) trong `text` trùng 1 cụm cần lọc (cụm dài trước, không chồng nhau)."""
    if not text or not words:
        return []
    norm, idx = _norm_map(text)
    spans: list[tuple[int, int]] = []
    for w in sorted({_key(w) for w in words if _key(w)}, key=len, reverse=True):
        k = norm.find(w)
        while k >= 0:
            a, b = idx[k], idx[k + len(w) - 1] + 1
            if not any(a < y and x < b for x, y in spans):
                spans.append((a, b))
            k = norm.find(w, k + len(w))
    return sorted(spans)


def _tidy(text: str, head: bool, tail: bool) -> str:
    """Dọn chỗ vừa bỏ chữ: khoảng trắng đôi, 2 dấu ngắt liền nhau; cụm nằm ở đầu / cuối câu thì bỏ luôn dấu ngắt còn
    sót ở đầu / cuối ("@纵横，排队" -> "排队"; dấu câu kết thúc 。！？ không đụng)."""
    text = _DUP_RE.sub(r"\1", _WS_RE.sub(" ", text))
    if head:
        text = _LEAD_RE.sub("", text)
    if tail:
        text = _TRAIL_RE.sub("", text)
    return text.strip()


def strip_words(text: str, words: list[str]) -> tuple[str, int, set[int]]:
    """Bỏ các cụm cần lọc khỏi 1 câu -> (câu mới, số chỗ đã bỏ, vị trí ký tự gốc đã bỏ). Không thấy -> câu giữ nguyên."""
    spans = find(text, words)
    if not spans:
        return text, 0, set()
    removed: set[int] = set()
    parts, last = [], 0
    for a, b in spans:
        parts.append(text[last:a])
        removed.update(range(a, b))
        last = b
    parts.append(text[last:])
    # chỗ ghép 2 phần: chữ Latin / số 2 bên thì giữ 1 khoảng trắng, chữ Hán thì nối liền
    out = parts[0]
    for p in parts[1:]:
        if out and p and out[-1].isalnum() and p[0].isalnum() and not (_CJK_RE.match(out[-1]) or _CJK_RE.match(p[0])):
            out += " "
        out += p
    head = not text[:spans[0][0]].strip()
    tail = not text[spans[-1][1]:].strip() or not _WORD_RE.search(text[spans[-1][1]:])
    return _tidy(out, head, tail), len(spans), removed


def empty(text: str) -> bool:
    """Câu không còn chữ / số nào (chỉ dấu câu, khoảng trắng)."""
    return not _WORD_RE.search(text or "")


def _ct_after(old: str, new: str, removed: set[int], ct) -> list | None:
    """Mốc từng chữ Hán sau khi bỏ chữ: giữ đúng mốc của các chữ còn lại (None nếu không khớp)."""
    if not ct:
        return None
    pos = [i for i, ch in enumerate(old) if _CJK_RE.match(ch)]
    if len(pos) != len(ct):
        return None
    keep = [list(ct[j]) for j, i in enumerate(pos) if i not in removed]
    return keep if keep and len(keep) == len(_CJK_RE.findall(new)) else None


def plan(segs: list, words: list[str]) -> dict:
    """Xem trước (không sửa gì): {"changed": [câu bỏ được chữ], "dropped": [câu chỉ có cụm đó -> xoá cả câu],
    "hits": tổng số chỗ trùng}."""
    changed, dropped, hits = [], [], 0
    for i, s in enumerate(segs):
        new, n, _ = strip_words(s.text or "", words)
        tn = strip_words(s.trans or "", words)[1]
        if not n and not tn:
            continue
        hits += n + tn
        (dropped if n and empty(new) else changed).append(i)
    return {"changed": changed, "dropped": dropped, "hits": hits}


def apply(segs: list, words: list[str]) -> dict:
    """Bỏ cụm cần lọc ở mọi câu (chữ gốc + bản dịch nếu có cụm đó). Câu gốc chỉ còn dấu câu -> bỏ cả câu. Giờ đầu /
    cuối mọi câu giữ nguyên; mốc từng chữ của chữ còn lại giữ nguyên. Sửa câu tại chỗ, trả {"segs": danh sách mới (cùng
    đối tượng câu, bỏ câu bị xoá), "changed": [câu đã sửa – đối tượng], "dropped": [chỉ số câu bị xoá trong segs cũ],
    "hits"}."""
    out, changed, dropped, hits = [], [], [], 0
    for i, s in enumerate(segs):
        old = s.text or ""
        new, n, removed = strip_words(old, words)
        trans, tn, _ = strip_words(s.trans or "", words)
        if not n and not tn:
            out.append(s)
            continue
        hits += n + tn
        if n and empty(new):
            dropped.append(i)
            continue
        if n:
            ct = _ct_after(old, new, removed, s.extra.get("ct"))
            s.text = new
            if ct:
                s.extra["ct"] = ct
            else:
                s.extra.pop("ct", None)
            s.extra["word_filtered"] = old
        if tn:
            s.trans = trans
        out.append(s)
        changed.append(s)
    return {"segs": out, "changed": changed, "dropped": dropped, "hits": hits}


def note(st: dict | None) -> str:
    """Kết quả tự lọc từ khi lấy phụ đề (thông báo bước 1 / Tự động)."""
    if not st or not st.get("hits"):
        return ""
    t = f"lọc từ {', '.join(st.get('words') or [])}: bỏ ở {st.get('lines', 0)} câu"
    if st.get("dropped"):
        t += f", xoá {st['dropped']} câu chỉ có chữ đó"
    return t
