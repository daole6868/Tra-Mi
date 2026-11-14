"""Tách phụ đề HIỂN THỊ (2.1.28): 1 câu (1 bản dịch, 1 đoạn giọng lồng) hiện trên video thành nhiều mảnh nối nhau, mỗi
mảnh hiện đúng lúc giọng đọc tới phần đó. Khác "Tách câu dài" (align.split_clauses – tách hẳn thành câu mới, đọc lại
giọng): ở đây câu, bản dịch, giọng lồng, karaoke giữ nguyên – chỉ đổi chữ HIỆN (video xuất, khung xem trước, SRT đã dịch).

Cách tách (`MODES`):
  - punct: sau dấu phẩy / dấu câu (, ; : … . ? !) – "Thuyền trưởng, lẩu anh nấu…" -> "Thuyền trưởng," | "lẩu anh nấu…";
  - words: theo số từ – câu dài hơn `over` từ hiện mỗi lần ≤ `words` từ (chia đều, ngắt ở chỗ tự nhiên – không xé tên
    riêng / từ ghép, không để mảnh kết thúc bằng "của / và / một…");
  - punct_words: dấu câu trước, mảnh nào còn dài hơn `over` từ thì chia tiếp theo số từ;
  - chars: theo số ký tự – mỗi lần ≤ `chars` ký tự (vừa 1 dòng, hợp video dọc / chữ to);
  - breath: theo chỗ ngắt hơi của giọng lồng (giọng ngừng ≥ `breath` giây) – chưa lồng tiếng thì như punct.
Mảnh ngắn hơn `min_words` từ gộp vào mảnh bên cạnh; mỗi mảnh hiện ≥ `min_dur` giây (không đủ thời gian thì bớt mảnh).
Tiếng viết liền (chữ Hán / Nhật / Thái): mỗi chữ = 1 "từ".
"""
from __future__ import annotations

from .karaoke import tokens, word_times
from .subtitle import Segment

MODES = [("off", "Không tách"), ("punct", "Sau dấu phẩy / dấu câu"), ("words", "Theo số từ"),
         ("punct_words", "Dấu câu + số từ"), ("chars", "Theo số ký tự (1 dòng)"),
         ("breath", "Theo chỗ ngắt hơi giọng lồng")]
DEFAULTS = {"words": 7, "over": 12, "punct_over": 6, "chars": 42, "breath": 0.3, "min_words": 2, "min_dur": 0.6}
KEYS = {"mode": "sub_split_mode", "words": "sub_split_words", "over": "sub_split_over",
        "punct_over": "sub_split_punct_over", "chars": "sub_split_chars", "breath": "sub_split_breath",
        "min_words": "sub_split_min_words", "min_dur": "sub_split_min_dur"}
PUNCT = set(",;:…，；：、.!?。！？")
_CLOSE = "\"'”’)]»）」』"
DROP = ("ct", "kv", "kw", "snap", "ct_voice")       # dữ liệu theo cả câu – không chép sang từng mảnh
# phó từ đứng trước động từ / tính từ: mảnh không kết thúc ở đây ("đã không | chết")
_NO_END = set("không chưa chẳng chả cũng vẫn còn đều hãy đừng chớ phải muốn cần có định sắp vừa mới luôn càng "
              "lại hơi khá quá thật".split())


def params(settings: dict | None) -> dict:
    st = settings or {}
    m = st.get(KEYS["mode"]) or "off"
    out: dict = {"mode": m if m in dict(MODES) else "off"}
    for k, v in DEFAULTS.items():
        try:
            out[k] = type(v)(st.get(KEYS[k], v))
        except (TypeError, ValueError):
            out[k] = v
    out["words"] = max(1, out["words"])
    out["chars"] = max(6, out["chars"])
    out["min_words"] = max(1, out["min_words"])
    out["min_dur"] = max(0.1, out["min_dur"])
    return out


def _ends_punct(word: str) -> bool:
    w = word.rstrip(_CLOSE)
    return bool(w) and (w[-1] in PUNCT or w.endswith("..."))


def _units(toks) -> list[str]:
    return [w + (" " if j else "") for w, j in toks]


def _bscore(toks) -> list[float]:
    """Điểm từng chỗ ngắt TRƯỚC từ b (âm = nên ngắt) – dùng chung luật xuống dòng phụ đề (textfit._break_score)."""
    from .textfit import _break_score

    units = _units(toks)
    if len(units) < 2:
        return [0.0] * len(units)
    spaced = units[0].endswith(" ")
    text = "".join(units)
    letters = [c for c in text if c.isalpha()]
    caps = not (letters and all(c.isupper() for c in letters))
    out = [0.0]
    for b in range(1, len(units)):
        try:
            sc = _break_score(units, b, spaced, caps)
        except Exception:  # noqa: BLE001
            sc = 0.0
        prev = units[b - 1].strip()
        if spaced and prev.lower() in _NO_END:
            sc += 0.15
        out.append(sc)
    return out


def _balanced(sizes: list[int], limit: float, min_n: int, bs: list[float]) -> list[int] | None:
    """Chia dãy (kích thước từng từ) thành ít mảnh nhất, mỗi mảnh ≤ limit (1 từ dài hơn limit thì đứng riêng) và ≥
    min_n từ; trong các cách ít mảnh nhất chọn cách đều nhất + ngắt ở chỗ tự nhiên. Trả chỗ bắt đầu các mảnh sau (≥ 1)."""
    n = len(sizes)
    if n < 2 or sum(sizes) <= limit:
        return []
    pre = [0]
    for x in sizes:
        pre.append(pre[-1] + x)
    for need in (min_n, 1):
        INF = (10 ** 9, 0.0)
        dp: list[tuple[int, float]] = [INF] * (n + 1)
        back = [-1] * (n + 1)
        dp[0] = (0, 0.0)
        for j in range(1, n + 1):
            for i in range(j - 1, -1, -1):
                w = pre[j] - pre[i]
                if w > limit and j - i > 1:
                    break
                if j - i < need and not (i == 0 and j == n):
                    continue
                if dp[i][0] >= INF[0]:
                    continue
                cost = dp[i][1] + (w / limit) ** 2 + (bs[i] if i > 0 else 0.0)
                cand = (dp[i][0] + 1, cost)
                if cand < dp[j]:
                    dp[j] = cand
                    back[j] = i
        if dp[n][0] < INF[0]:
            cuts, j = [], n
            while j > 0:
                i = back[j]
                if i > 0:
                    cuts.append(i)
                j = i
            return sorted(cuts)
    return []


def _merge_small(cuts: list[int], n: int, min_n: int) -> list[int]:
    """Mảnh ít hơn min_n từ -> gộp vào mảnh bên cạnh ngắn hơn (mảnh đầu / cuối gộp vào mảnh kề)."""
    cuts = sorted(set(c for c in cuts if 0 < c < n))
    while cuts:
        b = [0] + cuts + [n]
        lens = [b[k + 1] - b[k] for k in range(len(b) - 1)]
        k = min(range(len(lens)), key=lambda q: lens[q])
        if lens[k] >= min_n:
            break
        if k == 0:
            cuts.pop(0)
        elif k == len(lens) - 1:
            cuts.pop()
        else:   # gộp với mảnh ngắn hơn bên cạnh
            if lens[k - 1] <= lens[k + 1]:
                cuts.pop(k - 1)
            else:
                cuts.pop(k)
    return cuts


def breath_gaps(kv: list | None, min_gap: float) -> list[tuple[float, float]]:
    """Chỗ giọng lồng ngừng ≥ min_gap giây giữa các đoạn có tiếng của câu: [(hết tiếng, có tiếng lại)]."""
    sp = sorted((float(a), float(b)) for a, b in kv or [] if float(b) > float(a))
    return [(a1, b0) for (_a0, a1), (b0, _b1) in zip(sp, sp[1:]) if b0 - a1 >= min_gap - 1e-6]


def plan(toks, prm: dict, times: list | None = None, gaps: list | None = None,
         at: dict | None = None) -> list[int]:
    """Chỗ bắt đầu các mảnh (chỉ số từ, ≥ 1) của 1 câu đã tách từ (karaoke.tokens). times: giờ từng từ (có giọng lồng),
    gaps: chỗ giọng lồng ngừng (breath_gaps) cho cách "breath" (at[chỗ tách] = lúc giọng có tiếng lại)."""
    n = len(toks)
    mode = prm.get("mode", "off")
    if n < 2 or mode == "off":
        return []
    min_n = int(prm.get("min_words", 2))
    words = [w for w, _ in toks]
    _bs: list = []

    def bs_of() -> list[float]:
        if not _bs:
            _bs.extend(_bscore(toks))
        return _bs

    def punct_cuts(lo: int, hi: int) -> list[int]:
        return [i + 1 for i in range(lo, hi - 1) if _ends_punct(words[i])]

    def by_words(lo: int, hi: int) -> list[int]:
        return [lo + c for c in _balanced([1] * (hi - lo), prm["words"], min_n, bs_of()[lo:hi])]

    if mode == "breath" and times and len(times) == n and (gaps or prm.get("_kw")):
        if n <= prm["punct_over"]:
            return []
        if gaps:     # chỗ ngừng của giọng -> ranh giới 2 từ gần lúc giọng ngừng nhất, mảnh sau hiện khi có tiếng lại
            cuts = []
            for a, b in gaps:
                c = min(range(1, n), key=lambda i: abs(float(times[i][0]) - a))
                cuts.append(c)
                if at is not None:
                    at[c] = b
        else:        # mốc thật từng từ: khe giữa 2 từ
            cuts = [i + 1 for i in range(n - 1) if times[i + 1][0] - times[i][1] >= prm["breath"] - 1e-6]
        return _merge_small(cuts, n, min_n)
    if mode in ("punct", "breath"):
        if n <= prm["punct_over"]:
            return []
        return _merge_small(punct_cuts(0, n), n, min_n)
    if mode == "words":
        return by_words(0, n) if n > prm["over"] else []
    if mode == "punct_words":
        cuts = _merge_small(punct_cuts(0, n), n, min_n) if n > prm["punct_over"] else []
        b = [0] + cuts + [n]
        out = []
        for lo, hi in zip(b, b[1:]):
            if lo:
                out.append(lo)
            if hi - lo > prm["over"]:
                out += by_words(lo, hi)
        return sorted(set(out))
    if mode == "chars":
        sizes = [len(w) + (1 if j else 0) for w, j in toks]
        return _balanced(sizes, prm["chars"] + 1, min_n, bs_of()) if sum(sizes) > prm["chars"] + 1 else []
    return []


def _text(toks) -> str:
    return "".join(w + (j if k < len(toks) - 1 else "") for k, (w, j) in enumerate(toks)).replace("\n", " ")


_CACHE: dict = {}
CACHE_MAX = 40000


def _worth(toks, text: str, prm: dict) -> bool:
    """Câu có thể bị tách theo cách / mức đang chọn (bỏ qua nhanh câu ngắn – phim 3000 câu tách lại mỗi lần vẽ)."""
    n, m = len(toks), prm["mode"]
    if m in ("punct", "breath"):
        return n > prm["punct_over"]
    if m == "words":
        return n > prm["over"]
    if m == "punct_words":
        return n > min(prm["punct_over"], prm["over"])
    if m == "chars":
        return len(text) > prm["chars"]
    return False


def _split_line(text: str, start: float, end: float, kv, kw, prm: dict) -> list | None:
    """[(bắt đầu, kết thúc, chữ, kv mảnh, kw mảnh)] của 1 câu, None = không tách."""
    toks = tokens(text)
    if len(toks) < 2 or end - start < 2 * prm["min_dur"] or not _worth(toks, text, prm):
        return None
    kw = kw if kw and len(kw) == len(toks) else None
    voice = bool(kv or kw)
    gaps = breath_gaps(kv, prm["breath"]) if kv and not kw and prm["mode"] == "breath" else None
    need_t = prm["mode"] == "breath" and voice
    times = word_times(text, start, end, kv, kw) if need_t else None
    forced: dict[int, float] = {}
    cuts = plan(toks, prm | {"_kw": bool(kw)}, times, gaps, forced)
    if not cuts:
        return None
    if times is None:
        times = word_times(text, start, end, kv, kw)
    spans = sorted((float(a), float(c)) for a, c in kv or [] if float(c) > float(a))

    def start_of(c: int) -> float:
        """Lúc giọng đọc tới từ c; từ bắt đầu ngay chỗ giọng ngừng -> lúc giọng có tiếng lại."""
        t = float(forced.get(c, times[c][0]))
        if spans and not kw:
            nxt = next((a for a, e in spans if e > t + 1e-3), None)
            if nxt is not None and nxt > t:
                t = nxt
        return max(start, min(end, t))

    # giờ đổi mảnh = lúc giọng đọc tới từ đầu mảnh; mỗi mảnh ≥ min_dur (không đủ -> bỏ bớt chỗ tách)
    md = prm["min_dur"]
    b: list[float] = []
    while cuts:
        b = [start] + [start_of(c) for c in cuts] + [end]
        for k in range(1, len(b) - 1):                    # đẩy tới: mảnh trước đủ dài
            b[k] = max(b[k], b[k - 1] + md)
        for k in range(len(b) - 2, 0, -1):                # đẩy lui: mảnh sau đủ dài
            b[k] = min(b[k], b[k + 1] - md)
        if all(b[k + 1] - b[k] >= md - 1e-6 for k in range(len(b) - 1)):
            break
        lens = [b[k + 1] - b[k] for k in range(len(b) - 1)]
        k = min(range(len(lens)), key=lambda q: lens[q])  # mảnh ngắn nhất gộp vào mảnh bên cạnh
        cuts.pop(k if k < len(cuts) else len(cuts) - 1)
    if not cuts:
        return None
    idx_b = [0] + cuts + [len(toks)]
    out = []
    for j in range(len(idx_b) - 1):
        i0, i1 = idx_b[j], idx_b[j + 1]
        # đoạn giọng của mảnh: từ lúc mảnh hiện (giọng có tiếng lại) tới hết từ cuối của mảnh
        v0, v1 = max(float(times[i0][0]), b[j]), min(float(times[i1 - 1][1]), b[j + 1])
        ckv = None
        if kv:
            sp = [[max(float(a), v0), min(float(c), v1)] for a, c in kv if float(c) > v0 and float(a) < v1]
            ckv = [[round(a, 3), round(c, 3)] for a, c in sp if c - a > 1e-3] or None
        ckw = [list(w) for w in kw[i0:i1]] if kw else None
        out.append((round(b[j], 3), round(b[j + 1], 3), _text(toks[i0:i1]), ckv, ckw))
    return out


def _key(v) -> tuple:
    return tuple(tuple(round(float(y), 3) for y in x[:2]) for x in v) if v else ()


def split_segments(segs: list[Segment], settings: dict | None, which: str = "trans",
                   prm: dict | None = None) -> list[Segment]:
    """Bản sao các câu (giờ HIỆN – sau follow_dub) đã tách thành mảnh hiển thị. Mỗi mảnh: giờ từ lúc giọng đọc tới từ
    đầu của mảnh tới lúc mảnh sau hiện (mảnh đầu từ đầu câu, mảnh cuối tới cuối câu), karaoke (kv / kw) chia theo mảnh,
    extra["_src"] = số thứ tự câu gốc. which "both": tách dòng dịch, dòng gốc hiện cả câu ở mọi mảnh. Câu không tách giữ
    nguyên đối tượng. Kết quả từng câu nhớ lại (vẽ lại khung xem trước không phải tính lại)."""
    prm = prm or params(settings)
    if prm["mode"] == "off":
        return segs
    pk = tuple(sorted(prm.items()))
    out: list[Segment] = []
    for idx, s in enumerate(segs):
        raw = s.text if which == "source" else s.trans
        text = (raw or "").strip() if which == "source" else " ".join((raw or "").split())
        if not text:
            out.append(s)
            continue
        kv = s.extra.get("kv")
        kw = s.extra.get("kw") if which != "source" else None
        key = (pk, text, round(s.start, 3), round(s.end, 3), _key(kv),
               tuple((round(float(a), 3), round(float(b), 3), str(w[2]) if len(w) > 2 else "")
                     for w in kw for a, b in [w[:2]]) if kw else ())
        res = _CACHE.get(key)
        if res is None:
            res = _split_line(text, s.start, s.end, kv, kw, prm) or ()
            if len(_CACHE) > CACHE_MAX:
                _CACHE.clear()
            _CACHE[key] = res
        if not res:
            out.append(s)
            continue
        ex0 = {k: v for k, v in s.extra.items() if k not in DROP}
        for j, (a, b, part, ckv, ckw) in enumerate(res):
            ex = dict(ex0, _src=idx, _part=[j, len(res)])
            if ckv:
                ex["kv"] = [list(x) for x in ckv]
            if ckw:
                ex["kw"] = [list(x) for x in ckw]
            if which == "source":
                out.append(Segment(a, b, part, s.trans, s.speaker, ex))
            else:
                out.append(Segment(a, b, s.text, part, s.speaker, ex))
    return out


def count(segs: list[Segment], settings: dict | None, which: str = "trans") -> tuple[int, int]:
    """(số câu được tách, tổng số mảnh của các câu đó) – dòng xem trước ở tab Phụ đề."""
    parts = split_segments(segs, settings, which)
    by: dict[int, int] = {}
    for x in parts:
        if "_part" in x.extra:
            by[x.extra["_src"]] = by.get(x.extra["_src"], 0) + 1
    return len(by), sum(by.values())
