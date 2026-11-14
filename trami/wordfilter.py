"""Lọc từ phụ đề (2.1.33): xoá đúng cụm chữ người dùng điền ở mọi câu – logo / watermark bị OCR đọc dính vào câu
("@纵横排队一个个的进来" -> "排队一个个的进来"), câu chỉ có cụm đó thì bỏ cả câu. Giờ các câu không đổi.

So khớp: đúng thứ tự chữ, không phân biệt chữ hoa / thường, chữ toàn khổ / nửa khổ ("＠" = "@") và khoảng trắng giữa
các chữ ("@ 纵横" = "@纵横").

Ký tự đại diện (2.1.34, gõ * ? kiểu thường – "＊" "？" toàn khổ là chữ thường): "?" = đúng 1 chữ bất kỳ, "*" = tối đa
`ANY_MAX` 8 chữ liền sau, phải chạm khoảng trắng / dấu câu / hết câu. OCR đọc logo mỗi lần 1 kiểu ("@纵横" / "@凯横" /
"@黄AGC") -> "@*" bắt cả 3 khi logo dính ở CUỐI câu (hoặc đứng riêng / trước dấu phẩy), không đụng "@纵横排队一个个的进来";
"@??" = "@" + 2 chữ (logo dính ở đầu câu). Cụm phải có ít nhất 1 chữ thật (không nhận "*").

Nguyên từ (2.4.20): cụm có chữ cái / số Latin (tiếng Việt, Anh…) ở đầu / cuối chỉ khớp khi đứng riêng thành từ – "á"
bắt "Á!", "Á, đi thôi" nhưng không bắt chữ á trong "cách" / "lá"; chữ Hán vẫn khớp ở bất kỳ đâu."""
from __future__ import annotations

import re
import unicodedata
from functools import lru_cache

SEP_RE = re.compile(r"[\n\r,，;；|]+")
_WORD_RE = re.compile(r"\w", re.UNICODE)                  # câu còn chữ / số
_CJK_RE = re.compile(r"[㐀-鿿]")
_LEAD_RE = re.compile(r"^[\s,，、;；:：·•\-–—|/]+")          # dấu ngắt còn sót ở đầu câu sau khi bỏ chữ
_TRAIL_RE = re.compile(r"[\s,，、;；:：·•\-–—|/]+$")         # ... ở cuối câu
_DUP_RE = re.compile(r"([,，、;；:：])(?:\s*[,，、;；:：])+")    # "排队，，进来" -> "排队，进来"
_WS_RE = re.compile(r"\s{2,}")
_ANY, _ONE = "\ue000", "\ue001"        # ký tự đại diện sau khi tách (khác chữ "?" / "*" thật của cụm toàn khổ)
ANY_MAX = 8      # "*" bắt tối đa 8 chữ (logo ngắn) – "@*" không nuốt cả câu "@纵横排队一个个的进来" (logo dính đầu câu)
# "*" dừng ở khoảng trắng / dấu câu (cả dấu toàn khổ sau NFKC)
_STOP = r"\s,.!?;:、。…\"'“”‘’()（）\[\]{}《》〈〉【】「」『』<>|/\\·•\-–—~"
# chữ cái / số / dấu thanh kết hợp của tiếng viết cách từ (Latin + tiếng Việt, Hy Lạp, Kirin, Hàn) – xem _spaced
_SPACED = ("0-9a-z\u00aa\u00b5\u00ba\u00c0-\u024f\u0250-\u02af\u0300-\u036f\u0370-\u03ff\u0400-\u052f"
           "\u1e00-\u1eff\u1100-\u11ff\u3130-\u318f\uac00-\ud7af")
_SPACED_RE = re.compile(f"[{_SPACED}]")


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


def _key(word: str) -> str:
    return "".join(c for c in _norm(word) if not c.isspace())


def _tokens(word: str) -> list[str]:
    """Cụm người dùng điền -> từng phần: chữ (đã NFKC, chữ thường) hoặc ký tự đại diện "*" / "?" (gõ kiểu thường)."""
    out: list[str] = []
    for ch in word:
        if ch == "*":
            if not (out and out[-1] == _ANY):
                out.append(_ANY)
        elif ch == "?":
            out.append(_ONE)
        else:
            out.extend(c for c in _norm(ch) if not c.isspace())
    return out


def literal_len(word: str) -> int:
    """Số chữ thật (không tính * ?) – cụm không có chữ thật nào bị bỏ qua (tránh "*" xoá cả câu)."""
    return sum(1 for t in _tokens(word) if t not in (_ANY, _ONE))


def _spaced(ch: str) -> bool:
    """Chữ / số của tiếng viết cách từ (Latin, tiếng Việt, Hàn, Nga…) – 2.4.20: cụm bắt đầu / kết thúc bằng chữ này chỉ
    khớp NGUYÊN TỪ. Chữ Hán / Nhật / Thái (viết liền, không có ranh giới từ) vẫn khớp ở bất kỳ đâu."""
    return bool(_SPACED_RE.match(ch))


@lru_cache(maxsize=64)
def _compile(word: str):
    toks = _tokens(word)
    if not any(t not in (_ANY, _ONE) for t in toks):
        return None
    parts = []
    for t in toks:
        if t == _ONE:
            parts.append(r"[^\s]")
        elif t == _ANY:
            parts.append(f"[^{_STOP}]{{0,{ANY_MAX}}}")
        else:
            parts.append(re.escape(t))
    pat = r"\s*".join(parts)
    # 2.4.20: "á" không khớp chữ á trong "cách", "ha ha" không khớp giữa "hahaha" – đầu / cuối cụm là chữ cái / số của
    # tiếng viết cách từ thì chữ liền trước / liền sau trong câu không được là chữ cái / số (dấu câu, khoảng trắng, chữ
    # Hán thì được: "AGC" vẫn khớp trong "@黄AGC")
    if _spaced(toks[0]):
        pat = f"(?<![{_SPACED}])" + pat
    if _spaced(toks[-1]):
        pat += f"(?![{_SPACED}])"
    if toks[-1] == _ANY:       # "*" cuối cụm phải chạm hết câu / dấu câu / khoảng trắng (không cắt giữa chữ câu)
        pat += f"(?=[{_STOP}]|$)"
    return re.compile(pat)


def matchers(words) -> list:
    """Các cụm -> biểu thức so khớp (cụm nhiều chữ thật trước), bỏ cụm rỗng / chỉ có ký tự đại diện."""
    return _matchers(tuple(words or ()))


@lru_cache(maxsize=32)
def _matchers(words: tuple) -> list:
    ws = sorted({w for w in words if w and w.strip()}, key=lambda w: (-literal_len(w), w))
    return [m for m in (_compile(w) for w in ws) if m is not None]


def _norm_text(text: str) -> tuple[str, list[int]]:
    """Chuỗi để so khớp (NFKC, chữ thường, khoảng trắng giữ 1 ký tự) + vị trí ký tự gốc của từng ký tự."""
    out, idx = [], []
    for i, ch in enumerate(text):
        for c in _norm(ch):
            if c.isspace():
                c = " "
            out.append(c)
            idx.append(i)
    return "".join(out), idx


def find(text: str, words: list[str]) -> list[tuple[int, int]]:
    """Các đoạn [đầu, cuối) trong `text` trùng 1 cụm cần lọc (cụm dài trước, không chồng nhau)."""
    if not text or not words:
        return []
    ms = matchers(words)
    if not ms:
        return []
    norm, idx = _norm_text(text)
    spans: list[tuple[int, int]] = []
    for m in ms:
        for r in m.finditer(norm):
            if r.end() <= r.start():
                continue
            a, b = idx[r.start()], idx[r.end() - 1] + 1
            if not any(a < y and x < b for x, y in spans):
                spans.append((a, b))
    return sorted(spans)


def contains(text: str, words: list[str]) -> bool:
    """Câu có ít nhất 1 cụm cần lọc (bảng câu tab Phụ đề lọc theo cái này – như ô tìm kiếm)."""
    if not text or not words:
        return False
    norm = _norm_text(text)[0]
    return any(m.search(norm) for m in matchers(words))


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
