"""Logo chữ quanh phụ đề (2.1.37) – hàm thuần, không đụng video / bộ đọc chữ.

Logo kiểu "@纵横" nhảy chỗ vài giây 1 lần, in mờ (trong suốt) đè lên hình, hay nằm sát / cùng hàng phụ đề -> OCR đọc dính
vào câu ("…进来@纵横") hoặc ra câu chỉ có logo. Trước 2.1.37 chỉ lọc chữ đứng yên 1 chỗ VÀ đọc ra y hệt nhau (logo đọc mỗi
lần 1 kiểu "@纵横" / "@凯横" thì lọt), chữ chạy chỉ khi đổi độ cao.

Cách nhận ra logo (không dựa vào vị trí):
  * gom các lần đọc theo NỘI DUNG GẦN GIỐNG (chịu đọc sai 1–2 chữ) + cỡ khung chữ: bản đọc nhiều nhất làm gốc, bản đọc lệch
    cùng cỡ chữ gộp vào;
  * cụm chữ LẶP LẠI ở nhiều lúc cách xa nhau (≥ MIN_HITS lần, trải ≥ SPREAD thời lượng) và hiện tổng cộng ≥ SHARE thời lượng;
  * và có dấu hiệu logo: hiện CÙNG LÚC với câu khác (logo đè lên lúc đang có phụ đề – câu thoại lặp lại như "师父" thì đứng
    1 mình), hoặc có ký tự kiểu tên kênh (@ # chữ Latin / số), hoặc nhảy ≥ 3 chỗ, hoặc chữ trong suốt (nét chữ đổi theo
    nền). Câu 1 chữ ("嗯") không bao giờ là logo.
Đơn vị khung chữ: tuỳ người gọi (ocr.py dùng điểm ảnh dải quét; lưu vào dự án theo tỉ lệ chiều cao khung hình)."""
from __future__ import annotations

import difflib
import re
import unicodedata
from dataclasses import dataclass, field

LOGO_SAMPLES = 80     # số khung lấy mẫu dò logo trước khi quét (phim ngắn: 1 khung / 2 giây, tối thiểu 12)
MIN_HITS = 4          # logo phải thấy ở ≥ 4 lần (khung mẫu / đoạn chữ) khác nhau
SHARE = 0.04          # … và hiện tổng cộng ≥ 4 % thời lượng (tối thiểu MIN_SEC giây, video ngắn ≥ 30 %)
MIN_SEC = 12.0
SPREAD = 0.2          # … trải ≥ 20 % thời lượng (tối đa SPREAD_SEC giây) – không phải 1 câu hiện lâu
SPREAD_SEC = 120.0
COOC = 0.3            # ≥ 30 % số lần hiện cùng lúc với chữ khác = logo đè lên phụ đề
VAR_SIM = 0.6         # bản đọc lệch: giống bản gốc ≥ 60 % …
SIZE_SIM = 0.34       # … hoặc giống ≥ 34 % và cùng cỡ khung chữ
SIZE_H, SIZE_W = 0.3, 0.4   # cùng cỡ: cao lệch ≤ 30 %, rộng lệch ≤ 40 %
ALPHA_MIN = 0.4       # nét chữ dao động theo nền ≥ 40 % = chữ in mờ (trong suốt)
_MARK = re.compile(r"[@#＠＃]|[A-Za-z0-9]")
_LIT = re.compile(r"[㐀-鿿A-Za-z0-9@#぀-ヿ가-힯]")


def norm(text: str) -> str:
    """Chuẩn hoá để so: NFKC (＠ -> @, chữ toàn khổ -> thường), chữ thường, bỏ khoảng trắng."""
    return "".join(unicodedata.normalize("NFKC", str(text or "")).lower().split())


def literal(text: str) -> int:
    """Số chữ thật (Hán / Latin / số / @ #) – dấu câu không tính."""
    return len(_LIT.findall(text or ""))


def sim(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    return difflib.SequenceMatcher(None, a, b, autojunk=False).ratio()


def _size_ok(wh, ref, kh: float = SIZE_H, kw: float = SIZE_W) -> bool:
    if not wh or not ref or not ref[1] or not wh[1]:
        return False
    (w, h), (rw, rh) = wh, ref
    return abs(h - rh) <= kh * rh and (not rw or abs(w - rw) <= kw * rw)


@dataclass
class Logo:
    text: str                                   # bản đọc nhiều nhất (đã chuẩn hoá)
    variants: dict = field(default_factory=dict)   # bản đọc -> số lần
    w: float = 0.0                              # cỡ khung chữ (0 = chưa biết: logo người dùng tự gõ)
    h: float = 0.0
    static: tuple | None = None                 # khung (x0, y0, x1, y1) khi logo đứng yên 1 chỗ
    hits: int = 0
    share: float = 0.0                          # phần thời lượng có logo (0..1)
    places: int = 0                             # số chỗ khác nhau logo hiện
    transparent: bool = False
    user: bool = False                          # người dùng tự thêm ("Đây là logo")
    on: bool = True                             # tắt = trả chữ logo lại vào câu
    cut: int = 0                                # số câu đã cắt logo ra (điền sau khi quét)
    dropped: int = 0                            # số đoạn chỉ có logo đã bỏ
    hidden: int = 0                             # số lần bị bỏ ngay lúc quét (không đọc – tắt logo phải quét lại)

    def texts(self) -> list[str]:
        out = [self.text] + sorted((v for v in self.variants if v != self.text),
                                   key=lambda v: -self.variants.get(v, 0))
        return [t for t in out if t]

    def kind(self) -> str:
        if self.user:
            return "bạn thêm"
        if self.static:
            return "đứng yên"
        return "nhảy chỗ" if self.places >= 2 else "1 chỗ"

    def to_dict(self, k: float = 1.0) -> dict:
        """k: hệ số đổi đơn vị khung chữ (ocr.py: điểm ảnh dải quét -> tỉ lệ chiều cao khung hình)."""
        d = {"text": self.text, "variants": dict(list(sorted(self.variants.items(), key=lambda kv: -kv[1]))[:12]),
             "w": round(self.w * k, 5), "h": round(self.h * k, 5), "hits": self.hits, "share": round(self.share, 4),
             "places": self.places, "transparent": self.transparent, "user": self.user, "on": self.on,
             "cut": self.cut, "dropped": self.dropped, "hidden": self.hidden}
        if self.static:
            d["static"] = [round(v * k, 5) for v in self.static]
        return d

    @classmethod
    def from_dict(cls, d: dict, k: float = 1.0) -> "Logo":
        st = d.get("static")
        return cls(norm(d.get("text", "")), {norm(a): int(b) for a, b in (d.get("variants") or {}).items()},
                   float(d.get("w", 0) or 0) * k, float(d.get("h", 0) or 0) * k,
                   tuple(float(v) * k for v in st) if st else None, int(d.get("hits", 0) or 0),
                   float(d.get("share", 0) or 0), int(d.get("places", 0) or 0), bool(d.get("transparent")),
                   bool(d.get("user")), d.get("on", True) is not False, int(d.get("cut", 0) or 0),
                   int(d.get("dropped", 0) or 0), int(d.get("hidden", 0) or 0))


def match(text: str, logos: list, wh=None, center=None, any_state: bool = False) -> Logo | None:
    """Cả mảnh chữ này là logo? (bản đọc đã gặp, hoặc gần giống + cùng cỡ khung chữ, hoặc nằm đúng chỗ logo đứng yên).
    Logo đã tắt không tính (any_state: tính cả logo đã tắt – để gộp bản đọc vào đúng logo khi tìm)."""
    n = norm(text)
    if not n:
        return None
    ln = literal(n)
    for L in logos:
        if not (L.on or any_state) or not L.text:
            continue
        if L.static and center is not None and L.h:
            x0, y0, x1, y1 = L.static
            pad = 0.5 * L.h
            if x0 - pad <= center[0] <= x1 + pad and y0 - pad <= center[1] <= y1 + pad:
                return L
        if n in L.variants or n == L.text:
            return L
        if any(len(v) >= 2 and literal(v) >= 2 and (n.startswith(v) or n.endswith(v)) for v in L.texts()[:8]):
            continue            # logo dính câu ("嗯@纵横") – không phải cả mảnh là logo: để strip() cắt
        lt = literal(L.text)
        if ln > lt + 2 or ln < max(1, lt - 2) or ln < 2:
            continue
        s = max(sim(n, v) for v in L.texts()[:6])
        if L.h and wh is not None:
            if s >= SIZE_SIM and _size_ok(wh, (L.w, L.h)):
                return L
        elif s >= 0.75 and abs(ln - lt) <= 1 and (not _MARK.search(L.text) or _MARK.search(n)):
            return L        # logo người dùng gõ (chưa biết cỡ): chỉ nhận bản đọc rất giống
    return None


def _charmap(text: str) -> tuple[str, list[int]]:
    """Chuỗi chuẩn hoá + vị trí ký tự gốc của từng ký tự chuẩn hoá (bỏ khoảng trắng)."""
    out, own = [], []
    for i, ch in enumerate(text or ""):
        for c in unicodedata.normalize("NFKC", ch).lower():
            if c.isspace():
                continue
            out.append(c)
            own.append(i)
    return "".join(out), own


_JUNK_HEAD = set(" \t，,、;；:：·.。!！?？-—~～|｜/")
_JUNK_TAIL = set(" \t，,、;；:：·-—~～|｜/")


def strip(text: str, logos: list) -> tuple[str, tuple[int, int], list[str]]:
    """Cắt logo dính ở ĐẦU / CUỐI câu ("进来@纵横" -> "进来"): đúng bản đọc đã gặp, hoặc gần giống (≥ 66 %) khi logo có ký tự
    kiểu tên kênh (@ #…) và mẩu chữ có đúng ký tự đó. Không cắt giữa câu. Trả (chữ còn lại, [đầu, cuối) phần giữ lại theo
    vị trí ký tự gốc, [phần đã cắt])."""
    text = text or ""
    a, b = 0, len(text)
    cuts: list[str] = []
    cut_s = cut_e = False
    for _ in range(4):
        part = text[a:b]
        nt, own = _charmap(part)
        if not nt:
            break
        done = False
        for fz in (False, True):             # bản đọc y hệt trước, gần giống sau
            for L in logos:
                if done or not L.on:
                    continue
                for v in L.texts()[:8]:
                    lv = len(v)
                    if done or literal(v) < 2 or lv >= len(nt):
                        continue
                    pos = [i for i, c in enumerate(v) if c in "@#"]
                    if fz and (not pos or lv < 3):
                        continue
                    for side in ("s", "e"):
                        for d in ((0, -1, 1) if fz else (0,)):
                            k = lv + d
                            if done or k <= 0 or k >= len(nt):
                                continue
                            seg = nt[:k] if side == "s" else nt[-k:]
                            # gần giống: @ / # phải ở đúng chỗ như logo ("@黄横" ~ "@纵横"; "嗯@纵" thì không); mẩu
                            # dài hơn mà chứa nguyên logo -> logo nằm giữa câu ("我@纵横们"), không cắt
                            if not (seg == v if not fz else
                                    (sim(seg, v) >= 0.66 and v not in seg and
                                     [i for i, c in enumerate(seg) if c in "@#"] == pos)):
                                continue
                            if side == "s":
                                cut = own[k]
                                cuts.append(part[:cut])
                                a += cut
                                cut_s = True
                            else:
                                cut = own[len(nt) - k]
                                cuts.append(part[cut:])
                                b = a + cut
                                cut_e = True
                            done = True
        if not done:
            break
    if cut_s:
        while a < b and text[a] in _JUNK_HEAD:
            a += 1
    if cut_e:
        while b > a and text[b - 1] in _JUNK_TAIL:
            b -= 1
    return text[a:b], (a, b), cuts


def strip_all(text: str, logos: list) -> str:
    """Bỏ logo khỏi 1 câu (cả câu là logo -> "")."""
    if match(text, logos):
        return ""
    return strip(text, logos)[0]


@dataclass
class Obs:
    """1 lần thấy 1 mảnh chữ: k = mã lần đọc (khung mẫu / đoạn chữ), giờ, khung chữ, chữ đã chuẩn hoá, các chữ KHÁC trong
    cùng lần đọc, độ "trong suốt" (None: không đo được)."""
    k: int
    t0: float
    t1: float
    box: tuple
    text: str
    others: tuple = ()
    alpha: float | None = None


def _places(boxes: list, h: float) -> int:
    cs: list[tuple[float, float]] = []
    for b in boxes:
        c = ((b[0] + b[2]) / 2, (b[1] + b[3]) / 2)
        if not any(abs(c[0] - o[0]) <= 0.7 * h and abs(c[1] - o[1]) <= 0.7 * h for o in cs):
            cs.append(c)
    return len(cs)


def _med(vals: list[float]) -> float:
    v = sorted(vals)
    return v[len(v) // 2] if v else 0.0


def find(obs: list, covered: float, known: list | None = None) -> list[Logo]:
    """Tìm logo từ các lần đọc. known: logo đã biết (người dùng thêm / lần quét trước / dò trước khi quét) – giữ nguyên
    (logo đã tắt không bị tìm lại), cụm chữ giống logo đã biết gộp vào logo đó. Trả logo đã biết + logo mới tìm được."""
    known = [L for L in (known or []) if L.text]
    groups: dict[str, list] = {}
    for o in obs:
        if o.text and literal(o.text) >= 1:
            groups.setdefault(o.text, []).append(o)

    def spans(g):
        d: dict[int, tuple[float, float]] = {}
        for o in g:
            d[o.k] = (o.t0, o.t1)
        return d

    need = max(SHARE * covered, min(MIN_SEC, 0.3 * covered))
    spread_need = min(SPREAD * covered, SPREAD_SEC)
    seeds = sorted((n for n, g in groups.items() if literal(n) >= 2 and len(spans(g)) >= 3),
                   key=lambda n: -sum(b - a for a, b in spans(groups[n]).values()))
    taken: set[str] = set()
    for L in known:                     # bản đọc đã biết thuộc logo đã biết
        for v in L.texts():
            taken.add(v)
    clusters: list[list[str]] = []
    for n in seeds:
        if n in taken:
            continue
        g = groups[n]
        ref = (_med([o.box[2] - o.box[0] for o in g]), _med([o.box[3] - o.box[1] for o in g]))
        # giống logo đã biết -> gộp vào đó
        hit = next((L for L in known if match(n, [L], ref if L.h else None, any_state=True)), None)
        if hit is not None:
            hit.variants[n] = hit.variants.get(n, 0) + len(spans(g))
            taken.add(n)
            continue
        mem = [n]
        taken.add(n)
        cs = set(n)
        for n2, g2 in groups.items():
            if n2 in taken or literal(n2) > literal(n) + 2 or literal(n2) < max(2, literal(n) - 2):
                continue
            if len(cs & set(n2)) < max(1, min(len(n), len(n2)) // 3):
                continue
            s = sim(n2, n)
            if s < SIZE_SIM:
                continue
            wh = (_med([o.box[2] - o.box[0] for o in g2]), _med([o.box[3] - o.box[1] for o in g2]))
            if (s >= VAR_SIM and _size_ok(wh, ref, 0.35, 0.5)) or _size_ok(wh, ref):
                mem.append(n2)
                taken.add(n2)
        clusters.append(mem)

    found: list[Logo] = []
    accepted_texts: set[str] = set(t for L in known for t in L.texts())
    stats = []
    for mem in clusters:
        g = [o for m in mem for o in groups[m]]
        sp = spans(g)
        stats.append((sum(b - a for a, b in sp.values()), mem, g, sp))
    stats.sort(key=lambda x: -x[0])
    for dur, mem, g, sp in stats:
        hits = len(sp)
        mset = set(mem)
        # mảnh chữ dính logo ("@纵横没想到…") cũng là 1 lần thấy logo (và đang có câu khác)
        glued: dict[int, tuple[float, float]] = {}
        for n2, g2 in groups.items():
            if n2 in mset or literal(n2) < literal(mem[0]) + 1:
                continue
            if any(len(m) >= 2 and (n2.startswith(m) or n2.endswith(m)) for m in mem[:4]):
                for o in g2:
                    glued[o.k] = (o.t0, o.t1)
        allk = dict(sp)
        allk.update(glued)
        hits = len(allk)
        dur = sum(b - a for a, b in allk.values())
        times = [a for a, _ in allk.values()]
        if hits < MIN_HITS or dur < need or (max(times) - min(times)) < spread_need:
            continue
        hh = _med([o.box[3] - o.box[1] for o in g])
        ww = _med([o.box[2] - o.box[0] for o in groups[mem[0]]])
        places = _places([o.box for o in g], hh or 1.0)
        skip = mset | accepted_texts
        by_k: dict[int, set] = {}
        for o in g:
            by_k.setdefault(o.k, set()).update(o.others)
        co = 0
        for k in allk:
            if k in glued or any(literal(t) >= 2 and t not in skip for t in by_k.get(k, ())):
                co += 1
        cooc = co / max(1, hits)
        al = [o.alpha for o in g if o.alpha is not None]
        transparent = len(al) >= 3 and _med(al) >= ALPHA_MIN
        mark = bool(_MARK.search(mem[0]))
        if not (cooc >= COOC or mark or places >= 3 or transparent):
            continue
        static = None
        if places == 1 and len(g) >= 3:
            bx = [o.box for o in g]
            static = tuple(_med([b[i] for b in bx]) for i in range(4))
        L = Logo(mem[0], {m: len(spans(groups[m])) for m in mem}, ww, hh, static, hits,
                 min(1.0, dur / max(1e-6, covered)), places, transparent)
        found.append(L)
        accepted_texts |= set(mem)
    return known + found


def merge(a: list, b: list) -> list[Logo]:
    """Gộp 2 danh sách logo (cùng chữ / gần giống -> 1 logo, gộp các bản đọc + lấy số liệu lớn hơn)."""
    out = [L for L in a]
    for L in b:
        o = next((x for x in out if x.text == L.text or L.text in x.variants or x.text in L.variants or
                  match(L.text, [x], (L.w, L.h) if x.h and L.h else None, any_state=True)), None)
        if o is None:
            out.append(L)
            continue
        for v, c in L.variants.items():
            o.variants[v] = max(o.variants.get(v, 0), c)
        o.hits = max(o.hits, L.hits)
        o.share = max(o.share, L.share)
        o.places = max(o.places, L.places)
        o.transparent = o.transparent or L.transparent
        if not o.h and L.h:
            o.w, o.h = L.w, L.h
        if o.static is None and L.static and L.places <= 1 and o.places <= 1:
            o.static = L.static
        elif L.places > 1:
            o.static = None
    return out


def note(logos: list) -> str:
    """'logo @纵横 (nhảy chỗ, mờ) – cắt khỏi 230 câu, bỏ 15 đoạn chỉ có logo'."""
    parts = []
    for L in logos:
        if not L.on:
            continue
        kind = ", ".join(x for x in (L.kind(), "mờ" if L.transparent else "") if x)
        t = f"logo {L.text} ({kind}"
        if len(L.variants) > 1:
            t += f", đọc {len(L.variants)} kiểu"
        t += ")"
        bits = []
        if L.cut:
            bits.append(f"cắt khỏi {L.cut} câu")
        if L.dropped:
            bits.append(f"bỏ {L.dropped} đoạn chỉ có logo")
        if bits:
            t += " – " + ", ".join(bits)
        parts.append(t)
    return "; ".join(parts)
