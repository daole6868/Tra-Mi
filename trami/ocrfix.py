"""Sửa chữ OCR dễ nhầm theo từ điển (2.1.37) – "他己经走了" -> "他已经走了".

Bộ đọc chữ cho mỗi chữ 1 độ chắc + vài chữ ứng viên (ocr_engine.recognize_ex). Chữ nào bộ đọc chưa chắc (< WEAK) mà
đứng lẻ (không ghép thành từ nào trong từ điển jieba) thì thử thay bằng chữ ứng viên của bộ đọc hoặc chữ giống hình trong
bảng CONFUSABLE; chỉ đổi khi chữ mới GHÉP THÀNH TỪ có trong từ điển và câu "hợp lý" hơn hẳn (xác suất theo từ điển tăng hơn
phần bộ đọc nghiêng về chữ cũ + MARGIN). Chữ thuộc tên riêng (bảng tên / hồ sơ phim) không đụng. Tối đa MAX_FIX chữ / câu."""
from __future__ import annotations

import math
import re

WEAK = 0.97          # chỉ xét chữ bộ đọc chắc dưới mức này (chữ chắc chắn mà sai – hiếm – không đụng: an toàn hơn)
FLOOR = 3e-3         # chữ trong bảng giống hình mà bộ đọc không nêu: coi xác suất bằng mức này
MARGIN = 1.0         # câu mới phải hợp lý hơn (log xác suất theo từ điển) ít nhất chừng này sau khi trừ phần bộ đọc
MAX_FIX = 2
FIXED_CONF = 0.9     # chữ đã sửa theo từ điển coi là khá chắc
_HAN = re.compile(r"[㐀-鿿]")

# chữ Hán giản thể hay bị bộ đọc chữ nhầm với nhau (giống hình)
CONFUSABLE = [
    "己已巳", "未末", "人入八", "日曰", "土士", "戊戌戍戎", "千干于午", "天夭夫", "王玉主", "大太犬丈", "免兔", "拨拔",
    "侯候", "汩汨", "苦若", "准淮", "刀力刁", "贝见", "白自", "田由甲申", "今令", "折拆析", "治冶", "杨扬场", "历厉",
    "壁璧", "辨辩辫", "侍待", "鸟乌", "风凤", "季李", "买卖", "亨享", "祟崇", "茶荼", "撤撒", "即既", "喝渴", "狠狼很",
    "往住", "栗粟", "幕墓慕暮募", "怀坏", "籍藉", "设没", "话活", "肓盲", "冒冐", "折拆", "失矢", "免勉", "斤斥", "本木",
    "休体", "办为", "处外", "开升", "乐东", "可司", "问间", "吗妈", "那哪", "做敬", "未朱", "已巴", "了子", "厂广", "兑兄",
]
_MAP: dict[str, set[str]] = {}
for _g in CONFUSABLE:
    for _c in _g:
        _MAP.setdefault(_c, set()).update(x for x in _g if x != _c)

# 2.1.46: chữ Hán bị đọc thành chữ số / chữ Latin (font phụ đề đậm: "对了父亲" -> "对7父亲"). Chỉ xét chữ số / chữ Latin
# ĐỨNG LẺ giữa chữ Hán mà bộ đọc đọc KHÔNG chắc (< WEAK – số thật đọc chắc ~1,0, không có chữ ứng viên) và có chữ Hán ứng
# viên (bộ đọc tự nêu, hoặc bảng LATIN_HAN); chữ số đứng trước lượng từ / đơn vị ("7天", "7个") hay sau "第 / 几" là số thật.
LATIN_HAN = {"7": "了"}
_LATIN = re.compile(r"[0-9A-Za-z０-９Ａ-Ｚａ-ｚ]")
_NUMLIKE = re.compile(r"[0-9A-Za-z０-９Ａ-Ｚａ-ｚ〇零一二三四五六七八九十百千万亿两.,:%．，：％/·]")
MEASURE = set("个只条张本次遍回天年月日号点分秒岁块元角毛钱斤米里公克吨层楼集章节页名位人倍成折届期周时代辆台部件双对"
              "颗粒杯碗瓶首场趟顿把间座片支根份家户声步口度级")
NUM_PREV = set("第几约共每近")
ALT_STRONG = 0.05

_dt = None


def _dict():
    global _dt
    if _dt is None:
        try:
            import jieba

            jieba.setLogLevel(60)
            jieba.dt.check_initialized()
            _dt = jieba.dt
        except Exception:  # noqa: BLE001 – chưa cài jieba: không sửa
            _dt = False
    return _dt or None


def _best(s: str):
    """(log xác suất cách tách từ tốt nhất theo từ điển, danh sách [(đầu, cuối)] các từ)."""
    dt = _dict()
    dag = dt.get_DAG(s)
    route: dict = {}
    dt.calc(s, dag, route)
    words, i = [], 0
    while i < len(s):
        j = route[i][1] + 1
        words.append((i, j))
        i = j
    return route[0][0], words


def _in_word(words, i: int) -> bool:
    return any(a <= i < b and b - a >= 2 for a, b in words)


def available() -> bool:
    return _dict() is not None


def fix_line(text: str, cc: list | None, alts: list | None, names: list | None = None) -> tuple[str, list]:
    """Sửa chữ dễ nhầm trong 1 câu. cc: độ chắc từng chữ (khớp số chữ), alts: [[chữ ứng viên, xác suất]…] từng chữ.
    Trả (câu mới, [(vị trí, chữ cũ, chữ mới)])."""
    if not text or not cc or len(cc) != len(text) or not available():
        return text, []
    alts = alts if alts and len(alts) == len(text) else [[] for _ in text]
    guard = [False] * len(text)
    for nm in names or []:
        if len(nm) >= 2:
            k = text.find(nm)
            while k >= 0:
                for j in range(k, k + len(nm)):
                    guard[j] = True
                k = text.find(nm, k + 1)
    changes: list = []
    cur = text
    for i, ch in enumerate(text):                   # chữ số / chữ Latin đứng lẻ giữa chữ Hán (了 -> 7)
        if len(changes) >= MAX_FIX:
            break
        a = _latin_fix(cur, i, float(cc[i]), alts[i])
        if a and not guard[i]:
            changes.append((i, cur[i], a))
            cur = cur[:i] + a + cur[i + 1:]
    cand = sorted((float(cc[i]), i) for i, ch in enumerate(text)
                  if _HAN.match(ch) and not guard[i] and float(cc[i]) < WEAK and (alts[i] or ch in _MAP))
    for conf, i in cand:
        if len(changes) >= MAX_FIX:
            break
        lp0, w0 = _best(cur)
        if _in_word(w0, i):
            continue                        # chữ cũ đã ghép thành từ -> giữ
        opts = {a: float(p) for a, p in alts[i] if _HAN.match(str(a or ""))}
        for a in _MAP.get(cur[i], ()):
            opts.setdefault(a, FLOOR)
        best = None
        for a, pa in opts.items():
            var = cur[:i] + a + cur[i + 1:]
            lp1, w1 = _best(var)
            if not _in_word(w1, i):
                continue                    # chữ mới phải ghép thành từ có trong từ điển
            gain = lp1 - lp0 - (math.log(max(conf, 1e-3)) - math.log(max(pa, FLOOR)))
            if gain >= MARGIN and (best is None or gain > best[0]):
                best = (gain, a)
        if best:
            changes.append((i, cur[i], best[1]))
            cur = cur[:i] + best[1] + cur[i + 1:]
    return cur, changes


def _latin_fix(text: str, i: int, conf: float, alts) -> str | None:
    """Chữ Hán thay cho chữ số / chữ Latin text[i] bị đọc nhầm, None = giữ nguyên (xem LATIN_HAN)."""
    ch = text[i]
    if not _LATIN.match(ch) or conf >= WEAK:
        return None
    prev = text[i - 1] if i > 0 else ""
    nxt = text[i + 1] if i + 1 < len(text) else ""
    if (prev and _NUMLIKE.match(prev)) or (nxt and _NUMLIKE.match(nxt)):
        return None                                 # thuộc 1 dãy số / chữ Latin (12, 3.5, AI, 7点半…) – giữ
    if not (_HAN.match(prev or " ") or _HAN.match(nxt or " ")):
        return None
    if ch.isdigit() and (nxt in MEASURE or prev in NUM_PREV):
        return None                                 # "7天", "7个人", "第7" – số thật
    # chữ Latin thật hay có trong phụ đề ("B计划", "A级", "X光") -> chữ Latin chỉ theo bảng; chữ số ngoài bảng cần bộ đọc
    # nêu chữ Hán ứng viên đủ mạnh
    opts = {str(a): float(p) for a, p in (alts or []) if _HAN.match(str(a or "")) and float(p) >= ALT_STRONG} \
        if ch.isdigit() else {}
    if ch in LATIN_HAN:
        opts.setdefault(LATIN_HAN[ch], FLOOR)
    if not opts:
        return None
    if _dict() is None:
        return max(opts, key=opts.get)
    best = max(opts, key=lambda a: _best(text[:i] + a + text[i + 1:])[0] + math.log(max(opts[a], FLOOR)))
    return best


def fix_rows(segs: list, names: list | None = None, rows=None) -> list[int]:
    """Áp fix_line cho các câu OCR có độ chắc từng chữ (extra ocr_cc / ocr_alt) – rows None = mọi câu. Câu đổi:
    extra["ocr_fix"] = [[vị trí, cũ, mới]…] (cộng dồn khi sửa lại lần nữa), extra["ocr_before"] = chữ OCR đầu tiên, độ
    chắc chữ đã sửa nâng lên FIXED_CONF, ocr_conf tính lại; số chữ Hán đổi (7 -> 了) thì bỏ mốc từng chữ ct cũ.
    Trả số thứ tự các câu đã sửa."""
    out = []
    for k in (range(len(segs)) if rows is None else rows):
        if not (0 <= k < len(segs)):
            continue
        s = segs[k]
        cc = s.extra.get("ocr_cc")
        if not cc or len(cc) != len(s.text):
            continue
        new, ch = fix_line(s.text, cc, s.extra.get("ocr_alt"), names)
        if not ch:
            continue
        s.extra.setdefault("ocr_before", s.text)
        s.extra["ocr_fix"] = [list(c) for c in (s.extra.get("ocr_fix") or [])] + [list(c) for c in ch]
        cc = list(cc)
        for i, _o, _w in ch:
            cc[i] = max(float(cc[i]), FIXED_CONF)
        s.extra["ocr_cc"] = [round(float(v), 3) for v in cc]
        s.extra["ocr_conf"] = line_conf(cc)
        if len(_HAN.findall(new)) != len(_HAN.findall(s.text)):
            s.extra.pop("ct", None)
        s.text = new
        out.append(k)
    return out


def fix_segments(segs: list, names: list | None = None) -> int:
    """fix_rows cho mọi câu (lúc quét OCR). Trả số câu đã sửa."""
    return len(fix_rows(segs, names))


def line_conf(cc: list) -> float:
    """Độ chắc cả câu từ độ chắc từng chữ: (trung bình + chữ kém nhất) / 2 – 1 chữ rất kém kéo cả câu xuống (để tô / xoá
    câu không chắc bắt được câu sai đúng 1 chữ), câu toàn chữ chắc giữ ~1."""
    if not cc:
        return 0.0
    v = [float(x) for x in cc]
    return round((sum(v) / len(v) + min(v)) / 2, 3)


def weak_chars(s, thr: float = 0.85) -> list[tuple[int, str, float, list]]:
    """Các chữ OCR đọc kém chắc của 1 câu: [(vị trí, chữ, độ chắc, [chữ ứng viên])]."""
    cc = s.extra.get("ocr_cc")
    if not cc or len(cc) != len(s.text):
        return []
    al = s.extra.get("ocr_alt") or []
    return [(i, s.text[i], float(c), [a for a, _ in (al[i] if i < len(al) else [])])
            for i, c in enumerate(cc) if float(c) < thr]
