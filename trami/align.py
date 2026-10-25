"""Căn thời gian phụ đề sát với lời thoại thật (tầng 1 + 2 của khớp lồng tiếng).

1. Mốc từng chữ: mô hình căn thời gian FunASR (fa-zh) cho biết mỗi chữ Hán nói ở giây nào. Không có / lỗi thì
   dự phòng bằng độ to của giọng (ưu tiên giọng đã tách khỏi nhạc nền) và chia chữ theo thời gian có tiếng.
2. Thu mốc đầu/cuối câu về đúng lúc có tiếng, cắt câu ở chỗ nghỉ thật (≥ 0,35 s).
3. `split_speaker_changes`: câu dài có 2 giọng khác nhau (ECAPA, cửa sổ trượt) -> tách thành 2 câu.
"""
from __future__ import annotations

import re
from typing import Callable

import numpy as np

from .subtitle import Segment

SR = 16000
CJK = re.compile(r"[㐀-鿿]")
GAP_SPLIT = 0.35     # (dự phòng theo độ to) cụm tiếng cách nhau hơn mức này là 2 vế
PAUSE_HARD = 0.8     # có mốc từng chữ thật: nghỉ dài hơn mức này giữa 2 chữ cũng là hết câu
PAUSE_COMMA = 0.45   # dấu phẩy + nghỉ ≥ mức này = hết câu / đổi lượt nói (phẩy nói liền thì không tách)
MAX_DUR = 8.0        # câu nói dài hơn mức này (giây) thì chia ở dấu phẩy / chỗ nghỉ gần giữa
MIN_PIECE = 3        # chia câu dài: mỗi phần ít nhất 3 chữ
SHORT_PIECE = 2      # mẩu ≤ 2 chữ ("哇，", "嘿嘿，") gộp vào câu sát bên nếu nói liền
STRONG = "。！？!?…"
WEAK = "，,；;：:"
CLOSERS = "”」』）)\"' "
PAD = 0.05           # chừa thêm trước/sau mỗi câu
Prog = Callable[[float, str], None]

# Kiểu tách câu (Cài đặt `split_style`)
SPLIT_STYLES = {
    # câu trọn ý – hợp lồng tiếng: tin dấu câu của bộ nghe hơn khoảng lặng, nối dòng chưa hết câu
    "dub": {"pause_comma": 0.7, "pause_hard": 1.6, "chunk_gap": 1.5, "max_dur": 10.0, "join": True},
    # câu ngắn – hợp phụ đề đọc nhanh (cách tách trước 2.0.57)
    "short": {"pause_comma": PAUSE_COMMA, "pause_hard": PAUSE_HARD, "chunk_gap": 1.0, "max_dur": MAX_DUR, "join": False},
}
JOIN_GAP = 2.0          # nối dòng chưa hết câu (không có dấu câu cuối) với dòng sau cách ≤ 2 s
JOIN_GAP_BROKEN = 3.0   # cắt giữa 1 từ / mẩu cụt: nối dù cách tới 3 s
JOIN_CHARS = 36         # câu sau khi nối ≤ 36 chữ Hán và ≤ 10 s (rồi tách lại đúng chỗ)
JOIN_DUR = 10.0
LEAD_IN = 4             # dòng mở đầu ngắn kết thúc bằng dấu phẩy ("那是当然，", "不，") nối với câu sau nếu cách ≤ 1 s
TAIL_PULL = 0.8         # chữ cuối câu (≤ 2 chữ) cách phần trước hơn mức này: mốc thời gian sai -> kéo về
# từ không đứng cuối câu được (câu bị cắt ngang) / trợ từ không đứng đầu câu được
HANG_TAGS = ("p", "c", "m", "q")       # jieba: giới từ, liên từ, số từ, lượng từ (一只, 十份, 在, 因为…)
ENDABLE = {"一下", "一会", "一会儿", "一点儿", "一样", "一起", "一遍", "一次", "一半", "第一"}
HANG_WORDS = {"很", "最", "太", "更", "都", "也", "就", "还", "才", "真", "不", "没", "别", "这个", "那个", "这些",
              "那些", "这种", "那种", "一个", "一只", "一些", "一点", "因为", "所以", "但是", "可是", "如果", "虽然"}
HEAD_WORDS = set("的了着过得地吗呢吧嘛啦们么")
# dấu "。" giả: bộ nghe đặt dấu kết câu lúc người nói ngừng lấy hơi / ở chỗ cắt khúc ("我刚回来就。" | "收到…")
FALSE_STOP_GAP = 1.2    # câu kết thúc bằng phó từ / giới từ / liên từ, câu sau mở bằng trợ từ: nối nếu cách ≤ 1,2 s
OPENER_GAP = 2.0        # vế mở bằng 既然 / 就连 / 如果… mà chưa có vế sau (người nói hay ngừng lâu hơn): ≤ 2 s
SOFT_PAUSE = 0.3        # bỏ "。" giả: người nói ngừng ≥ 0,3 s -> thay bằng "，", nói liền -> bỏ hẳn
TAIL_WORDS = {"就", "才", "都", "也", "又", "却", "再", "更", "很", "最", "太", "把", "被", "向", "往", "从", "与",
              "因为", "所以", "但是", "可是", "而且", "如果", "虽然", "既然", "就是", "还是"}
OPENERS = ("既然", "就连", "如果", "要是", "虽然", "尽管", "即使", "哪怕", "只要", "除非", "不管", "无论", "假如")
CONSEQUENT = set("就都也那则还便才却但")      # vế sau của 既然…就 / 就连…都 / 虽然…但…: có rồi thì câu đã trọn
MODAL_HANG = {"会", "能", "要", "想", "得", "可以", "应该", "敢", "肯", "必须", "需要", "打算", "准备"}
TAIL_SHIFT_MAX = 8      # dòng chưa hết câu mà nối thì quá dài: chuyển mẩu sau dấu phẩy cuối (≤ 8 chữ) sang câu sau


def split_params(settings: dict | None) -> dict:
    """Thông số tách câu theo Cài đặt "Kiểu tách câu" (mặc định: câu trọn ý – hợp lồng tiếng)."""
    return dict(SPLIT_STYLES.get((settings or {}).get("split_style", "dub"), SPLIT_STYLES["dub"]))


# ------------------------------------------------------------------ đo độ to giọng

def voiced_mask(audio: np.ndarray, hop: float = 0.02) -> tuple[np.ndarray, float]:
    """Mảng True/False theo khung 20 ms: có tiếng nói hay không (ngưỡng tự thích nghi)."""
    n = int(SR * hop)
    if len(audio) < n:
        return np.zeros(0, bool), hop
    frames = audio[: len(audio) // n * n].reshape(-1, n)
    rms = np.sqrt((frames.astype(np.float32) ** 2).mean(axis=1) + 1e-12)
    loud = np.percentile(rms, 95) if len(rms) else 0
    thr = max(0.008, loud * 0.12)
    m = rms > thr
    # lấp lỗ ngắn < 0,12 s (hơi thở giữa chữ), bỏ tiếng lẻ < 0,06 s
    m = _close(m, int(0.12 / hop))
    m = _open(m, int(0.06 / hop))
    return m, hop


def _runs(m: np.ndarray, val: bool):
    i, n = 0, len(m)
    while i < n:
        if m[i] == val:
            j = i
            while j < n and m[j] == val:
                j += 1
            yield i, j
            i = j
        else:
            i += 1


def _close(m: np.ndarray, k: int) -> np.ndarray:
    m = m.copy()
    for a, b in list(_runs(m, False)):
        if a > 0 and b < len(m) and b - a <= k:
            m[a:b] = True
    return m


def _open(m: np.ndarray, k: int) -> np.ndarray:
    m = m.copy()
    for a, b in list(_runs(m, True)):
        if b - a < k:
            m[a:b] = False
    return m


# ------------------------------------------------------------------ mốc từng chữ

_fa_model = None
_fa_error: str | None = None   # nạp mô hình lỗi 1 lần -> không thử lại (tránh tải đi tải lại)


def _fa():
    global _fa_model, _fa_error
    if _fa_error:
        raise RuntimeError(_fa_error)
    if _fa_model is None:
        try:
            from .asr_funasr import _device, _model

            _fa_model = _model("fa-zh", model="fa-zh", device=_device("auto"))
        except Exception as e:  # noqa: BLE001
            _fa_error = f"{type(e).__name__}: {e}"
            raise
    return _fa_model


def fa_usable() -> bool:
    """Nạp mô hình căn thời gian (chỉ thử 1 lần). False nếu không dùng được -> dùng cách dự phòng."""
    try:
        _fa()
        return True
    except Exception:  # noqa: BLE001
        return False


def char_times_fa(clip: np.ndarray, chars: str) -> list[tuple[float, float]] | None:
    """Mốc (giây, tính từ đầu clip) của từng chữ Hán bằng mô hình căn thời gian; None nếu câu này không căn được."""
    if _fa_model is None:
        return None
    try:
        res = _fa_model.generate(input=(clip, " ".join(chars)), data_type=("sound", "text"))
        ts = (res[0] or {}).get("timestamp") if res else None
        if not ts or len(ts) != len(chars):
            return None
        return [(a / 1000.0, b / 1000.0) for a, b in ts]
    except Exception:  # noqa: BLE001
        return None


def char_times_runs(mask: np.ndarray, hop: float, text: str, pos: list[int]) -> list[tuple[float, float]] | None:
    """Dự phòng khi không có mô hình căn chữ: các cụm có tiếng cách nhau ≥ GAP_SPLIT được coi là các vế câu; chia chữ
    cho từng vế theo độ dài (ưu tiên ranh giới ở dấu câu), trong mỗi vế chia đều."""
    n = len(pos)
    runs = list(_runs(mask, True))
    if not runs or n == 0:
        return None
    gap_k = int(GAP_SPLIT / hop)
    groups = [list(runs[0])]
    for a, b in runs[1:]:
        if a - groups[-1][1] >= gap_k:
            groups.append([a, b])
        else:
            groups[-1][1] = b
    if len(groups) == 1 or n < 2 * len(groups):
        return char_times_energy(mask, hop, n)
    # chỗ được phép cắt: sau chữ có dấu câu theo sau (ưu tiên), số chữ mỗi vế tỉ lệ thời lượng
    punct = {k for k in range(n - 1) if any(not CJK.match(ch) and not ch.isspace()
                                             for ch in text[pos[k] + 1:pos[k + 1]])}
    lens = np.array([b - a for a, b in groups], float)
    acc = np.cumsum(lens)[:-1] / lens.sum() * n
    cuts = []
    for x in acc:
        c = int(round(x)) - 1
        near = [k for k in punct if abs(k - c) <= max(1, int(n * 0.15))]
        if near:
            c = min(near, key=lambda k: abs(k - c))
        c = max(cuts[-1] + 1 if cuts else 0, min(n - 2, c))
        cuts.append(c)
    bounds = [0] + [c + 1 for c in cuts] + [n]
    out: list[tuple[float, float]] = []
    for g, (a, b) in enumerate(groups):
        k = bounds[g + 1] - bounds[g]
        if k <= 0:
            continue
        per = (b - a) / k
        for q in range(k):
            out.append(((a + q * per) * hop, (a + (q + 1) * per) * hop))
    return out if len(out) == n else char_times_energy(mask, hop, n)


def char_times_energy(mask: np.ndarray, hop: float, n: int) -> list[tuple[float, float]] | None:
    """Dự phòng: chia đều n chữ theo các khung có tiếng."""
    idx = np.where(mask)[0]
    if len(idx) == 0 or n == 0:
        return None
    per = len(idx) / n
    out = []
    for k in range(n):
        a = idx[min(len(idx) - 1, int(k * per))]
        b = idx[min(len(idx) - 1, max(int((k + 1) * per) - 1, int(k * per)))]
        out.append((a * hop, (b + 1) * hop))
    return out


# ------------------------------------------------------------------ căn + tách câu

def _char_positions(text: str) -> list[int]:
    return [i for i, ch in enumerate(text) if CJK.match(ch)]


def _split_text(text: str, pos: list[int], cuts: list[int]) -> list[str]:
    """Cắt text sau chữ Hán thứ cuts[k]. Có dấu câu ở chỗ cắt: cắt SAU dấu câu cuối – chữ Latin / số đứng trước dấu câu
    thuộc câu trước ("我可是最强AI。" | "你先别…"); không có dấu câu: số / chữ Latin đi theo phần sau ("6块钱")."""
    parts, last = [], 0
    for c in cuts:
        a = pos[c] + 1
        between = text[a:pos[c + 1]] if c + 1 < len(pos) else text[a:]
        marks = [i for i, ch in enumerate(between) if ch in STRONG or ch in WEAK]
        if marks:
            end = a + marks[-1] + 1
            while end < len(text) and text[end] in CLOSERS:
                end += 1
        else:
            end = a
            while end < len(text) and not CJK.match(text[end]) and not text[end].isalnum():
                end += 1
        parts.append(text[last:end].strip())
        last = end
    parts.append(text[last:].strip())
    return parts


def _boundary(text: str, pos: list[int], k: int) -> str:
    """Giữa chữ Hán thứ k và k+1 có gì: 'strong' (。！？) / 'weak' (，；：) / 'word' (số, chữ Latin) / ''."""
    between = text[pos[k] + 1:pos[k + 1]]
    if any(c in STRONG for c in between):
        return "strong"
    if any(c in WEAK for c in between):
        return "weak"
    return "word" if any(c.isalnum() for c in between) else ""


def _word_cut(text: str, pos: list[int], cand: list[int]) -> int:
    """Chỗ cắt (chỉ số chữ Hán, cắt SAU chữ đó) trong `cand` gần giữa nhất mà không cắt đôi 1 từ."""
    mid = (cand[0] + cand[-1]) / 2
    ends = _word_ends(text)
    ok = [k for k in cand if ends is None or pos[k] in ends or pos[k + 1] - pos[k] > 1] or list(cand)
    return min(ok, key=lambda k: abs(k - mid))


def _word_info(text: str) -> tuple[set[int], set[int], set[int]] | None:
    """Tách từ có loại từ (jieba): (vị trí ký tự cuối mỗi từ, cuối các từ không đứng cuối câu được – 一只 / 在 / 因为…,
    đầu các trợ từ không đứng đầu câu được – 的 / 了 / 吗…). Chưa cài jieba -> None."""
    try:
        import jieba
        import jieba.posseg as pseg

        jieba.setLogLevel(60)
        words = [(w.word, w.flag) for w in pseg.cut(text)]
    except Exception:  # noqa: BLE001
        return None
    if "".join(w for w, _ in words) != text:
        return None
    ends, hang, head, i = set(), set(), set(), 0
    for w, f in words:
        j = i + len(w)
        ends.add(j - 1)
        if w in HANG_WORDS or (f[:1] in HANG_TAGS and w not in ENDABLE and CJK.match(w[-1])):
            hang.add(j - 1)
        if w in HEAD_WORDS:
            head.add(i)
        i = j
    return ends, hang, head


def _cut_ok(text: str, pos: list[int], k: int, info) -> bool:
    """Cắt sau chữ Hán thứ k ở chỗ KHÔNG có dấu câu (chỉ dựa vào khoảng lặng) có được không: không cắt giữa 1 từ
    (真|的, 日|子, 好|吃), không để từ "treo" cuối câu (一只, 在, 因为…), không để trợ từ đầu câu sau (的, 了, 吗…)."""
    i, j = pos[k], pos[k + 1]
    if info is not None:
        ends, hang, head = info
        return i in ends and i not in hang and j not in head
    left = text[:i + 1]
    return not any(left.endswith(w) for w in HANG_WORDS) and text[j] not in HEAD_WORDS


def _word_ends(text: str) -> set[int] | None:
    """Vị trí ký tự cuối của mỗi từ (jieba) – để không bao giờ cắt đôi 1 từ; None nếu chưa cài jieba."""
    try:
        import jieba

        jieba.setLogLevel(60)
        ends, i = set(), 0
        for w in jieba.cut(text):
            i += len(w)
            ends.add(i - 1)
        return ends
    except Exception:  # noqa: BLE001
        return None


def plan_cuts(text: str, pos: list[int], times: list[tuple[float, float]], max_chars: int = 28,
              exact: bool = False, max_dur: float = MAX_DUR, pause_comma: float = PAUSE_COMMA,
              pause_hard: float = PAUSE_HARD) -> list[int]:
    """Chỗ tách câu (cắt SAU chữ Hán thứ k) – mỗi dòng là 1 CÂU TRỌN NGHĨA để dịch và lồng tiếng:
      * luôn tách ở cuối câu (。！？) và ở chỗ nghỉ thật ≥ PAUSE_HARD giây (chỉ khi có mốc từng chữ thật);
      * dấu phẩy (，；：) chỉ tách khi sau nó có nghỉ ≥ PAUSE_COMMA (hết câu / đổi lượt nói); phẩy nói liền là
        chỗ chia ưu tiên khi câu dài quá `max_chars` chữ / `max_dur` giây;
      * mẩu ≤ 2 chữ nói liền với câu bên cạnh được gộp lại; không bao giờ cắt đôi 1 từ hay dính số sang câu trước;
      * chỗ nghỉ không có dấu câu: chỉ cắt ở ranh giới từ, không để từ "treo" cuối câu / trợ từ đầu câu (`_cut_ok`).
    pause_comma / pause_hard theo kiểu tách câu (`SPLIT_STYLES`)."""
    n = len(pos)
    if n < 2:
        return []
    kind = [_boundary(text, pos, k) for k in range(n - 1)]
    gap = [max(0.0, round(times[k + 1][0] - times[k][1], 3)) for k in range(n - 1)]
    info = _word_info(text)
    ok = [kind[k] != "" or _cut_ok(text, pos, k, info) for k in range(n - 1)]
    cuts = {k for k in range(n - 1) if kind[k] == "strong" or (kind[k] == "weak" and gap[k] >= pause_comma)}
    if exact:
        cuts |= {k for k in range(n - 1) if kind[k] != "word" and gap[k] >= max(pause_hard, pause_comma) and ok[k]}
    cuts = sorted(cuts)
    while True:   # gộp mẩu ngắn vào câu kề, ưu tiên phía nghỉ ngắn hơn
        bounds = [-1] + cuts + [n - 1]
        best = None
        for j in range(len(bounds) - 1):
            size = bounds[j + 1] - bounds[j]
            if size > SHORT_PIECE:
                continue
            for c, other in ((bounds[j + 1], bounds[j + 2] - bounds[j + 1] if j + 2 < len(bounds) else 0),
                             (bounds[j], bounds[j] - bounds[j - 1] if j > 0 else 0)):
                if not (other and c in cuts):
                    continue
                # không có dấu câu ngăn cách: mẩu ≤ 2 chữ là đầu / đuôi câu bị cắt (mốc thời gian lệch) -> gộp dù nghỉ lâu
                if kind[c] == "":
                    near = gap[c] <= JOIN_GAP_BROKEN
                elif kind[c] == "weak":
                    near = gap[c] < 0.6
                else:
                    near = size <= 1 and gap[c] < 0.3
                if size + other <= max_chars and near:
                    if best is None or gap[c] < best[0]:
                        best = (gap[c], c)
        if best is None:
            break
        cuts.remove(best[1])
    # câu quá dài (số chữ / thời lượng): chia ở dấu phẩy hoặc chỗ nghỉ gần giữa nhất, không thì ở ranh giới từ
    ends = info[0] if info is not None else _word_ends(text)

    def too_long(x: int, y: int) -> bool:
        if y - x > max_chars:
            return True
        return y - x >= 8 and times[y][1] - times[x + 1][0] > max_dur

    def best_cut(x: int, y: int) -> int | None:
        size = y - x
        by_len = size > max_chars
        cand = []
        for k in range(x + 1, y):
            left, right = k - x, y - k
            if min(left, right) < MIN_PIECE or kind[k] == "word" or not ok[k]:
                continue
            score = {"weak": 3.0, "strong": 3.0}.get(kind[k], 0.0)
            if exact:
                score += min(gap[k], 1.0) * 3
            if score <= 0:
                if not by_len:
                    continue     # câu chỉ dài về thời gian: chỉ chia ở dấu câu / chỗ nghỉ thật
                if ends is not None and not (pos[k] in ends or pos[k + 1] - pos[k] > 1):
                    continue     # giữa 1 từ
            cand.append((score - 2.5 * abs(left - right) / size, k))
        if not cand and by_len:
            lo, hi = x + 1 + size // 4, y - size // 4
            cand = [(0.0, _word_cut(text, pos, [k for k in range(lo, hi) if kind[k] != "word"] or list(range(lo, hi))))]
        return max(cand)[1] if cand else None

    out: list[int] = []
    bounds = [-1] + cuts + [n - 1]
    for a, b in zip(bounds, bounds[1:]):
        pieces = [(a, b)]
        while pieces:
            x, y = pieces.pop()
            c = best_cut(x, y) if too_long(x, y) and y - x >= 4 else None
            if c is None:
                out.append(y)
                continue
            pieces += [(c, y), (x, c)]
    return sorted(set(out) - {n - 1})


def refine(segs: list[Segment], audio: np.ndarray, use_fa: bool = True, progress: Prog | None = None,
           cancel=None, duration: float = 0.0, max_chars: int = 28, params: dict | None = None) -> list[Segment]:
    """Trả về danh sách câu mới: mốc sát lời thật, mỗi dòng là 1 câu / vế câu trọn vẹn (`plan_cuts`).
    Câu không phải tiếng Trung giữ nguyên mốc (chỉ thu theo độ to). Có mốc từng chữ thật thì lưu vào extra["ct"]
    (để tách khi đổi người nói đúng chỗ nghỉ; xoá trước khi lưu dự án)."""
    from .ffmpeg import Cancelled

    total = duration or len(audio) / SR
    out: list[Segment] = []
    # nạp mô hình căn thời gian đúng 1 lần cho cả video (lỗi -> cả video dùng cách dự phòng)
    def has_ct(s: Segment) -> bool:   # bộ nhận dạng đã đo sẵn mốc từng chữ (Qwen3-ASR, SenseVoice có mốc)
        ct = s.extra.get("ct")
        return bool(ct) and len(ct) == len(_char_positions(s.text))

    fa_ok = use_fa and any(CJK.search(s.text) and not has_ct(s) for s in segs) and fa_usable()
    for k, s in enumerate(segs):
        if cancel and cancel():
            raise Cancelled()
        if has_ct(s):
            out += split_timed(s, [(float(a), float(b)) for a, b in s.extra["ct"]], True, max_chars, total, params)
            continue
        a0 = max(0.0, s.start - 0.3)
        a1 = min(total, s.end + 0.3)
        clip = audio[int(a0 * SR):int(a1 * SR)]
        pos = _char_positions(s.text)
        mask, hop = voiced_mask(clip)
        times, exact = None, False
        if fa_ok and pos and len(clip) > SR * 0.3:
            times = char_times_fa(clip, "".join(s.text[i] for i in pos))
            exact = times is not None
        if times is None and pos:
            # chỉ tính phần có tiếng nằm trong mốc gốc (không lấn sang câu bên cạnh)
            m2 = mask.copy()
            lo, hi = int((s.start - a0) / hop) - 3, int((s.end - a0) / hop) + 3
            m2[:max(0, lo)] = False
            m2[max(0, hi):] = False
            times = char_times_runs(m2, hop, s.text, pos)
        if not times:
            # không có chữ Hán / không thấy tiếng: chỉ thu mốc theo độ to
            vi = np.where(mask)[0]
            if len(vi):
                st = max(s.start - 0.15, a0 + vi[0] * hop - PAD)
                en = min(s.end + 0.15, a0 + (vi[-1] + 1) * hop + PAD)
                if en - st >= 0.3:
                    s.start, s.end = round(st, 3), round(en, 3)
            if len(s.text) > max_chars:   # đoạn dài chưa tách (không đo được mốc) -> chia theo dấu câu / số chữ
                from .asr_funasr import split_long

                out += [Segment(round(x, 3), round(y, 3), t, "", s.speaker, dict(s.extra))
                        for x, y, t in split_long(s.start, s.end, s.text, max_chars)]
                continue
            out.append(s)
            continue
        out += split_timed(s, [(a0 + a, a0 + b) for a, b in times], exact, max_chars, total, params)
        if progress and k % 10 == 0:
            progress(k / max(1, len(segs)), f"Đang căn thời gian và tách câu {k}/{len(segs)}")
    return fix_overlaps(out)


def _latin_run(t: str, head: bool) -> int:
    """Số chữ Latin / số ở đầu (head) / cuối câu, trước chữ Hán đầu / sau chữ Hán cuối (không có mốc riêng)."""
    found = list(CJK.finditer(t))
    if not found:
        return 0
    part = t[:found[0].start()] if head else t[found[-1].end():]
    return sum(1 for ch in part if ch.isascii() and ch.isalnum())


def _latin_dur(n: int) -> float:
    return 0.1 + 0.12 * min(n, 8)   # "AI" ~0,34 s, "GPT" ~0,46 s, "2026" ~0,58 s


def _pull_tail(times: list[tuple[float, float]], a: int, b: int, limit: float = TAIL_PULL) -> None:
    """Câu ≥ 3 chữ mà 1–2 chữ cuối cách phần trước > limit giây: bộ căn thời gian hay gán chữ cuối vào tiếng động /
    hơi thở phía sau ("…什么日" … "子。") -> kéo về sát chữ trước."""
    if b - a < 3:
        return
    for m in (1, 2):
        k = b - m
        if k - 1 < a:
            break
        if times[k][0] - times[k - 1][1] > limit:
            d = times[k][0] - times[k - 1][1] - 0.05
            for i in range(k, b):
                times[i] = (times[i][0] - d, times[i][1] - d)
            break


def split_timed(s: Segment, times: list[tuple[float, float]], exact: bool, max_chars: int = 28,
                total: float = 0.0, params: dict | None = None) -> list[Segment]:
    """1 đoạn lời + mốc (giây, tính từ đầu video) của từng chữ Hán -> các câu trọn nghĩa (`plan_cuts`), mốc mỗi câu
    sát chữ đầu / chữ cuối. exact: mốc đo thật (lưu extra["ct"] để tách khi đổi người nói đúng chỗ nghỉ).
    params: thông số kiểu tách câu (`split_params`)."""
    q = params or {}
    pos = _char_positions(s.text)
    if not pos or len(times) != len(pos):
        return [s]
    cuts = plan_cuts(s.text, pos, times, max_chars, exact, q.get("max_dur", MAX_DUR),
                     q.get("pause_comma", PAUSE_COMMA), q.get("pause_hard", PAUSE_HARD))
    texts = _split_text(s.text, pos, cuts) if cuts else [s.text]
    bounds = [0] + [c + 1 for c in cuts] + [len(times)]
    times = list(times)
    out = []
    for j, t in enumerate(texts):
        a, b = bounds[j], bounds[j + 1]
        _pull_tail(times, a, b)
        st, en = times[a][0], times[b - 1][1]
        # chữ Latin / số ở đầu / cuối câu chưa có mốc: nới vào khoảng trống bên cạnh ("最强AI。" nói xong mới hết câu)
        n_head, n_tail = _latin_run(t, True), _latin_run(t, False)
        if n_head:
            lo = times[a - 1][1] + 0.02 if a > 0 else min(s.start + PAD, st)
            st = max(lo, st - _latin_dur(n_head))
        if n_tail:
            hi = times[b][0] - 0.02 if b < len(times) else max(s.end - PAD, en)
            en = max(en, min(hi, en + _latin_dur(n_tail)))
        end = en + PAD
        ns = Segment(round(max(0.0, st - PAD), 3), round(min(total, end) if total else end, 3), t,
                     s.trans if not cuts else "", s.speaker, dict(s.extra))
        if ns.end - ns.start < 0.2:
            ns.end = round(ns.start + 0.2, 3)
        if exact:
            ns.extra["ct"] = [[round(x, 3), round(y, 3)] for x, y in times[a:b]]
        out.append(ns)
    return out


def fix_overlaps(out: list[Segment]) -> list[Segment]:
    """Không để câu chồng lên nhau sau khi nới mốc."""
    out.sort(key=lambda x: x.start)
    for a, b in zip(out, out[1:]):
        if a.end > b.start:
            mid = round((a.end + b.start) / 2, 3) if a.end - b.start < 0.2 else b.start
            a.end = max(a.start + 0.1, min(a.end, mid))
            b.start = max(b.start, a.end)
    return out


def times_from_words(text: str, words: list, offset: float = 0.0) -> list[tuple[float, float]] | None:
    """Mốc từng chữ Hán của `text` từ kết quả có mốc thời gian của bộ nhận dạng / bộ căn thời gian:
    words = [(chữ hoặc từ, đầu, cuối)] (giây, tính từ đầu đoạn); từ nhiều chữ chia đều thời gian cho từng chữ.
    Dấu câu, chữ Latin, số được bỏ qua. None nếu số chữ Hán không khớp -> người gọi dùng cách dự phòng."""
    cjk: list[tuple[float, float]] = []
    for w, a, b in words:
        w, a, b = str(w), float(a), float(b)
        n = len(w)
        for i, ch in enumerate(w):
            if CJK.match(ch):
                cjk.append((a + (b - a) * i / n, a + (b - a) * (i + 1) / n))
    if not cjk or len(cjk) != len(_char_positions(text)):
        return None
    out, last = [], 0.0
    for a, b in cjk:
        a = max(a, last)
        b = max(b, a + 0.02)
        out.append((round(offset + a, 3), round(offset + b, 3)))
        last = a
    return out if plausible_times(out) else None


def plausible_times(times: list[tuple[float, float]]) -> bool:
    """Mốc từng chữ có hợp lý không: người nói nhanh nhất ~9 chữ/giây; bộ căn lỗi (lời không khớp tiếng, sửa chữ)
    thường dồn nhiều chữ vào cùng 1 chỗ -> bỏ, dùng cách dự phòng thay vì co câu còn 0,2 giây."""
    n = len(times)
    if n <= 2:
        return True
    dur = times[-1][1] - times[0][0]
    if n / max(dur, 0.01) > 12:
        return False
    same = sum(1 for x, y in zip(times, times[1:]) if y[0] - x[0] < 0.01)
    return same <= n // 3


def speech_spans(audio: np.ndarray, min_len: float = 0.3) -> list[tuple[float, float]]:
    """Các đoạn có tiếng nói theo độ to (khi không có bộ chia đoạn VAD của FunASR)."""
    m, hop = voiced_mask(audio)
    if not len(m):
        return []
    m = _close(m, int(0.5 / hop))
    return [(round(i * hop, 3), round(j * hop, 3)) for i, j in _runs(m, True) if (j - i) * hop >= min_len]


def _quietest(audio: np.ndarray, lo: float, hi: float) -> float:
    """Thời điểm lặng nhất (cửa sổ 0,2 s) trong [lo, hi] – chỗ cắt ít hại nhất khi phải cắt 1 đoạn nói liền."""
    n = int(SR * 0.02)
    a = audio[int(lo * SR):int(hi * SR)]
    if len(a) < n * 12:
        return (lo + hi) / 2
    fr = a[: len(a) // n * n].reshape(-1, n)
    rms = np.sqrt((fr.astype(np.float32) ** 2).mean(axis=1) + 1e-12)
    sm = np.convolve(rms, np.ones(10) / 10, mode="same")
    return lo + (int(np.argmin(sm[5:-5])) + 5) * 0.02


def chunk_spans(spans: list[tuple[float, float]], audio: np.ndarray | None = None, max_gap: float = 1.0,
                max_len: float = 25.0, min_len: float = 8.0) -> list[tuple[float, float]]:
    """Gộp các đoạn có tiếng nói sát nhau (nghỉ ≤ max_gap giây) thành khúc ≤ max_len giây để bộ nhận dạng nghe trọn
    câu (đặt dấu câu đúng hơn, không cắt đôi câu). Khúc quá dài: cắt ở chỗ nghỉ dài nhất giữa 2 đoạn; đoạn nói liền
    quá dài (VAD cắt cứng) thì cắt ở chỗ lặng nhất, không cắt giữa chữ."""
    groups: list[list[tuple[float, float]]] = []
    for a, b in sorted(spans):
        if b <= a:
            continue
        if groups and a - groups[-1][-1][1] <= max_gap:
            groups[-1].append((a, b))
        else:
            groups.append([(a, b)])
    out: list[tuple[float, float]] = []
    while groups:
        g = groups.pop(0)
        a, b = g[0][0], g[-1][1]
        if b - a <= max_len:
            out.append((round(a, 3), round(b, 3)))
            continue
        lo, hi = a + min_len, a + max_len
        gaps = [(g[i + 1][0] - g[i][1], i) for i in range(len(g) - 1) if lo <= g[i][1] <= hi]
        if gaps:
            i = max(gaps)[1]
            groups[:0] = [g[:i + 1], g[i + 1:]]
            continue
        t = _quietest(audio, lo, hi) if audio is not None else hi
        left = [(x, min(y, t)) for x, y in g if x < t]
        right = [(max(x, t), y) for x, y in g if y > t]
        groups[:0] = [left, right] if left and right else [[(a, t)], [(t, b)]]
    return out


# ------------------------------------------------------------------ nối câu bị cắt ngang (không cần AI)

def _cjk_count(t: str) -> int:
    return len(CJK.findall(t or ""))


def _last_mark(t: str) -> str:
    """Ký tự cuối (bỏ ngoặc / khoảng trắng đóng)."""
    t = (t or "").rstrip(CLOSERS)
    return t[-1:] if t else ""


def _join_text(parts: list[str]) -> str:
    out = ""
    for x in parts:
        x = x.strip()
        if out and x and out[-1].isascii() and out[-1].isalnum() and x[0].isascii() and x[0].isalnum():
            out += " "
        out += x
    return out


def broken_junction(a: str, b: str, punctuated: bool = True) -> bool:
    """Chỗ nối dòng a | dòng b là 1 câu bị cắt ngang (bằng chứng chắc chắn, không cần AI): cắt giữa 1 từ (真|的, 日|子),
    từ "treo" cuối a (一只, 在, 因为…), trợ từ đầu b (的, 了, 吗…), chữ Latin / số đầu b dính dấu câu ("最强" | "AI。");
    phụ đề có dấu câu (bộ nghe đặt): thêm mẩu cụt ≤ 2 chữ ("子。", "吃。")."""
    if not a.strip() or not b.strip():
        return False
    m = _last_mark(a)
    if m in STRONG or m in WEAK:
        return False
    if re.match(r"\s*[A-Za-z0-9]+\s*[。！？!?…，,；;：:]", b):
        return True
    if punctuated and (_cjk_count(a) <= 2 or (_cjk_count(b) <= 2 and _last_mark(b) in STRONG)):
        return True
    ta, tb = a.rstrip(), b.lstrip()
    if not (CJK.match(ta[-1]) and CJK.match(tb[0])):
        return False
    if punctuated:      # bộ nghe không đặt dấu câu + dòng dừng ở động từ năng nguyện ("勤俭持家，会" | "过日子。")
        toks = _tokens(ta[-6:])
        if (toks[-1] if toks else ta[-1]) in MODAL_HANG:
            return True
    tail, head = ta[-8:], tb[:8]
    joined = tail + head
    pos = _char_positions(joined)
    k = _cjk_count(tail) - 1
    if k < 0 or k + 1 >= len(pos):
        return False
    return not _cut_ok(joined, pos, k, _word_info(joined))


def _tokens(text: str) -> list[str] | None:
    """Các từ (jieba), bỏ khoảng trắng; None nếu chưa cài jieba."""
    try:
        import jieba

        jieba.setLogLevel(60)
        return [w for w in jieba.cut(text) if w.strip()]
    except Exception:  # noqa: BLE001
        return None


def false_stop(a: str, b: str) -> str:
    """Dấu "。" cuối dòng a là giả (bộ nghe tưởng hết câu lúc người nói ngừng lấy hơi / ở chỗ cắt khúc) – chỉ nhận khi có
    bằng chứng ngữ pháp chắc chắn: a dừng ở phó từ / giới từ / liên từ ("我刚回来就。"), vế cuối mở bằng 既然 / 就连 /
    如果… mà chưa có vế sau ("就连今日的风。"), b mở bằng trợ từ (的 / 了 / 吗…). Trả lý do ("" = dấu chấm thật):
    "opener" | "tail" | "head"."""
    t = (a or "").rstrip(CLOSERS)
    if not t.endswith("。") or not CJK.search(b or ""):
        return ""
    s = t[:-1].rstrip()
    if _cjk_count(s) < 2 or not CJK.match(s[-1]):
        return ""
    clause = re.split(f"[{STRONG}{WEAK}]", s)[-1].strip()
    for op in OPENERS:
        if clause.startswith(op) and len(clause) > len(op) and not set(clause[len(op):]) & CONSEQUENT:
            return "opener"
    toks = _tokens(s)
    if (toks[-1] if toks else s[-1]) in TAIL_WORDS:
        return "tail"
    tb = b.lstrip()
    hb = _tokens(tb)
    head = hb[0] if hb else (tb[:1] if tb[:1] in "的了吗呢吧嘛啦么" else "")
    return "head" if head in HEAD_WORDS else ""


def soft_join(grp: list[Segment]) -> str:
    """Chữ của các dòng nối lại; dấu "。" giả ở chỗ nối (chỉ chỗ đó) -> "，" nếu người nói có ngừng, nói liền thì bỏ."""
    parts = []
    for k, s in enumerate(grp):
        t = s.text or ""
        if k + 1 < len(grp):
            core = t.rstrip(CLOSERS)
            if core.endswith("。"):
                mark = "，" if grp[k + 1].start - s.end >= SOFT_PAUSE else ""
                t = core[:-1] + mark + t[len(core):]
        parts.append(t)
    return _join_text(parts)


def shift_tails(segs: list[Segment], q: dict, punctuated: bool) -> int:
    """Dòng chưa hết câu (không dấu câu cuối) mà nối với dòng sau thì quá dài (> 36 chữ / 10 s): chuyển mẩu sau dấu phẩy
    cuối sang đầu dòng sau ("…为大师兄挨的雷，为三师兄" | "让雷劈焦的辫子…" -> "…挨的雷，" | "为三师兄让雷劈焦的辫子…").
    Sửa `segs` tại chỗ (đối tượng mới cho 2 dòng đổi chữ). Trả số chỗ đã chuyển."""
    n = 0
    for k in range(len(segs) - 1):
        a, b = segs[k], segs[k + 1]
        if not punctuated or not (CJK.search(a.text or "") and CJK.search(b.text or "")):
            continue
        if a.extra.get("song") or b.extra.get("song") or (a.speaker and b.speaker and a.speaker != b.speaker):
            continue
        if split_mates(a, b):
            continue
        if not a.text.strip() or _last_mark(a.text) in STRONG + WEAK or a.text.rstrip() != a.text.rstrip(CLOSERS):
            continue
        gap = b.start - a.end
        if not ((gap <= JOIN_GAP_BROKEN and broken_junction(a.text, b.text, punctuated))
                or (q.get("join") and gap <= JOIN_GAP)):
            continue
        if _cjk_count(a.text) + _cjk_count(b.text) <= JOIN_CHARS and b.end - a.start <= JOIN_DUR:
            continue                       # nối được bình thường (join_broken lo)
        cut = max(a.text.rfind(c) for c in STRONG + WEAK)
        if cut < 0:
            continue
        head_t, tail_t = a.text[:cut + 1], a.text[cut + 1:].strip()
        nh, nt = _cjk_count(head_t), _cjk_count(tail_t)
        if not 1 <= nt <= TAIL_SHIFT_MAX or nh < 2 or nt + _cjk_count(b.text) > JOIN_CHARS:
            continue
        ct = a.extra.get("ct") or []
        exact = len(ct) == nh + nt
        if exact:
            t_split, a_end = float(ct[nh][0]), float(ct[nh - 1][1]) + PAD
        else:
            t_split = a_end = a.start + (a.end - a.start) * nh / (nh + nt)
        if b.end - t_split > JOIN_DUR:
            continue
        new_a = Segment(a.start, round(a_end, 3), head_t, "", a.speaker,
                        {kk: v for kk, v in a.extra.items() if kk != "ct"})
        new_b = Segment(round(max(a_end, t_split - PAD), 3), b.end, _join_text([tail_t, b.text]), "", b.speaker,
                        {kk: v for kk, v in b.extra.items() if kk != "ct"})
        bct = b.extra.get("ct") or []
        if exact and len(bct) == _cjk_count(b.text):
            new_a.extra["ct"], new_b.extra["ct"] = ct[:nh], ct[nh:] + bct
        segs[k], segs[k + 1] = new_a, new_b
        n += 1
    return n


def heard_junction(t1: str, t2: str, heard: str, near: int = 6) -> dict | None:
    """Chỗ cắt khúc nghe lại có ngữ cảnh 2 bên: tìm chỗ nối (đuôi t1 | đầu t2) trong lời nghe lại `heard`. Trả {"end":
    có dấu kết câu ở chỗ nối, "mark": "，" nếu có dấu phẩy, "t1"/"t2": lời đã sửa chữ nghe sai sát chỗ nối (cùng số chữ,
    ≤ 2 chữ trong 2 chữ mỗi bên), "fixed": số chữ sửa}; None nếu không tìm thấy chỗ nối (giữ nguyên)."""
    from difflib import SequenceMatcher

    def chars(t):
        return [(ch, i) for i, ch in enumerate(t or "") if CJK.match(ch) or ch.isalnum()]
    c1, c2 = chars(t1)[-near:], chars(t2)[:near]
    if not c1 or not c2:
        return None
    hw: list[str] = []
    after: list[str] = []
    for ch in heard or "":
        if CJK.match(ch) or ch.isalnum():
            hw.append(ch)
            after.append("")
        elif hw:
            after[-1] += ch
    pat = [c for c, _ in c1] + [c for c, _ in c2]
    m: dict[int, int] = {}
    same = 0
    for tag, i1, i2, j1, j2 in SequenceMatcher(None, pat, hw, autojunk=False).get_opcodes():
        if tag == "equal" or (tag == "replace" and i2 - i1 == j2 - j1):
            for d in range(i2 - i1):
                m[i1 + d] = j1 + d
            same += (i2 - i1) if tag == "equal" else 0
    k = len(c1) - 1
    if same < 0.6 * len(pat) or k not in m or k + 1 not in m or m[k + 1] != m[k] + 1:
        return None
    p = after[m[k]]
    out = {"end": any(ch in STRONG for ch in p), "mark": "，" if any(ch in WEAK for ch in p) else "",
           "t1": t1, "t2": t2, "fixed": 0}
    fixes = [(i, hw[m[i]]) for i in range(len(pat)) if i in m and pat[i] != hw[m[i]] and k - 1 <= i <= k + 2
             and CJK.match(pat[i]) and CJK.match(hw[m[i]])]
    if 0 < len(fixes) <= 2:
        l1, l2 = list(t1), list(t2)
        for i, ch in fixes:
            if i < len(c1):
                l1[c1[i][1]] = ch
            else:
                l2[c2[i - len(c1)][1]] = ch
        out.update(t1="".join(l1), t2="".join(l2), fixed=len(fixes))
    return out


def _should_join(grp: list[Segment], nxt: Segment, q: dict, punctuated: bool) -> bool:
    last = grp[-1]
    if not (CJK.search(last.text or "") and CJK.search(nxt.text or "")):
        return False
    if split_mates(last, nxt):
        return False
    if last.extra.get("song") or nxt.extra.get("song"):
        return False
    if last.speaker and nxt.speaker and last.speaker != nxt.speaker:
        return False
    text = _join_text([s.text for s in grp])
    if sum(_cjk_count(s.text) for s in grp) + _cjk_count(nxt.text) > JOIN_CHARS or nxt.end - grp[0].start > JOIN_DUR:
        return False
    gap = nxt.start - last.end
    if _last_mark(text) in STRONG:   # dấu kết câu: chỉ nối khi là dấu "。" giả (bằng chứng ngữ pháp chắc chắn)
        why = false_stop(text, nxt.text) if punctuated else ""
        return bool(why) and gap <= (OPENER_GAP if why == "opener" else FALSE_STOP_GAP)
    if gap <= JOIN_GAP_BROKEN and broken_junction(text, nxt.text, punctuated):
        return True
    if not (q.get("join") and punctuated):
        return False
    if _last_mark(text) not in WEAK:          # bộ nghe không đặt dấu câu cuối dòng = câu chưa hết
        return gap <= JOIN_GAP
    lead = re.split(f"[{STRONG}]", text.rstrip(CLOSERS + WEAK))[-1]
    return _cjk_count(lead) <= LEAD_IN and gap <= 1.0     # "那是当然，" / "不，" + câu sau


def _even_times(s: Segment) -> list[tuple[float, float]]:
    """Mốc ước lượng: chữ Hán của câu rải đều trong [đầu, cuối] (đã trừ phần chừa PAD)."""
    n = _cjk_count(s.text)
    a, b = s.start + PAD, max(s.start + PAD + 0.05 * n, s.end - PAD)
    d = (b - a) / max(1, n)
    return [(a + i * d, a + (i + 1) * d) for i in range(n)]


def _whole_lines_trans(grp: list[Segment], text: str) -> str:
    """Bản dịch cho câu mới nếu nó đúng bằng các dòng cũ i..j liền nhau ghép lại (và các dòng đó đều đã dịch)."""
    t = text.strip()
    for i in range(len(grp)):
        for j in range(i + 1, len(grp) + 1):
            if _join_text([x.text for x in grp[i:j]]).strip() == t:
                tr = [x.trans.strip() for x in grp[i:j]]
                return " ".join(tr) if all(tr) else ""
    return ""


def join_broken(segs: list[Segment], params: dict | None = None, max_chars: int = 28,
                total: float = 0.0) -> tuple[list[Segment], int]:
    """Nối các dòng bị cắt ngang câu rồi tách lại đúng chỗ (dấu kết câu / câu quá dài) – không cần AI.
      * luôn nối khi có bằng chứng chắc chắn (`broken_junction`), cách ≤ 3 s;
      * kiểu "câu trọn ý" (params["join"]): thêm dòng chưa có dấu câu cuối (cách ≤ 2 s) và dòng mở đầu ngắn kết thúc
        bằng dấu phẩy (≤ 4 chữ, cách ≤ 1 s);
      * dấu "。" giả (`false_stop`): dòng dừng ở 就 / 都 / 因为…, vế 既然 / 就连… chưa có vế sau, dòng sau mở bằng trợ từ
        -> nối, "。" ở chỗ nối thành "，" (có ngừng) hoặc bỏ (nói liền); dòng không dấu câu dừng ở 会 / 能 / 要… -> nối;
      * nối thì quá dài: chuyển mẩu sau dấu phẩy cuối sang dòng sau (`shift_tails`);
      * cùng người nói, không phải đoạn hát, câu nối ≤ 36 chữ / 10 s. Phụ đề không có dấu câu (OCR) chỉ nối khi cắt giữa
        từ / từ treo / trợ từ đầu câu.
    Câu giữ nguyên chữ là đúng câu cũ (giữ bản dịch, giọng lồng). Trả về (danh sách mới, số câu mới tạo ra)."""
    q = dict(params or SPLIT_STYLES["dub"])
    segs = sorted(segs, key=lambda s: s.start)
    cjk = [s for s in segs if CJK.search(s.text or "")]
    punctuated = sum(1 for s in cjk if _last_mark(s.text) in STRONG + WEAK) >= 0.3 * max(1, len(cjk))
    resplit = dict(q, pause_comma=99.0, pause_hard=99.0)   # tách lại chỉ ở dấu kết câu / câu quá dài
    shifted = shift_tails(segs, q, punctuated)
    out: list[Segment] = []
    made, i = shifted, 0
    while i < len(segs):
        grp = [segs[i]]
        while i + len(grp) < len(segs) and _should_join(grp, segs[i + len(grp)], q, punctuated):
            grp.append(segs[i + len(grp)])
        i += len(grp)
        if len(grp) == 1:
            out.append(grp[0])
            continue
        exact = all(len(s.extra.get("ct") or []) == _cjk_count(s.text) for s in grp)
        times = []
        for s in grp:
            times += [(float(a), float(b)) for a, b in s.extra["ct"]] if exact else _even_times(s)
        trans = " ".join(s.trans.strip() for s in grp if s.trans.strip())
        joined = Segment(grp[0].start, max(s.end for s in grp), soft_join(grp), trans,
                         grp[0].speaker, {k: v for k, v in grp[0].extra.items() if k != "ct"})
        old = {s.text.strip(): s for s in grp}
        for piece in split_timed(joined, times, exact, max_chars, total, resplit):
            keep = old.get(piece.text.strip())
            if keep is None:
                made += 1
                if not piece.trans:   # câu mới = đúng mấy dòng cũ liền nhau ghép lại -> giữ bản dịch của chúng
                    piece.trans = _whole_lines_trans(grp, piece.text)
            out.append(keep if keep is not None else piece)
    return fix_overlaps(out) if made else out, made


def drop_char_times(segs: list[Segment], keep: bool = True) -> None:
    """Xoá dữ liệu tạm của bước nhận dạng (cờ khúc) trước khi lưu dự án. Mốc từng chữ (extra["ct"]) còn khớp lời được
    GIỮ LẠI từ 2.0.96 (gộp / tách / tách câu dài / bám mép câu dùng mốc thật thay vì chia theo tỉ lệ); keep=False: xoá
    hết để đo lại (Căn khớp thoại)."""
    from .snap import ct_ok

    for s in segs:
        s.extra.pop("chunked", None)
        if not keep or ("ct" in s.extra and not ct_ok(s)):
            s.extra.pop("ct", None)


# ------------------------------------------------------------------ nghe lại câu bị sót

def missed_speech(covered: list[tuple[float, float]], audio: np.ndarray, min_len: float = 0.35,
                  pad: float = 0.2) -> list[tuple[float, float, float, float]]:
    """Đoạn CÓ TIẾNG NÓI (độ to của giọng đã tách khỏi nhạc nền) mà chưa câu nào phủ: bộ chia đoạn (VAD) bỏ sót.
    Trả về (đầu, cuối của phần có tiếng, đầu, cuối cửa sổ nghe lại – nới `pad` giây nhưng không lấn câu đã có)."""
    m, hop = voiced_mask(audio)
    if not len(m):
        return []
    m = _close(m, int(0.3 / hop))
    cov = np.zeros(len(m), bool)
    for a, b in covered:
        cov[max(0, int(a / hop)):max(0, int(np.ceil(b / hop)))] = True
    free = _close(m & ~cov, int(0.3 / hop)) & ~cov
    out = []
    for i, j in _runs(free, True):
        if (j - i) * hop < min_len:
            continue
        lo, hi = i, j
        while lo > 0 and not cov[lo - 1] and (i - lo) * hop < pad:
            lo -= 1
        while hi < len(m) and not cov[hi] and (hi - j) * hop < pad:
            hi += 1
        out.append((round(i * hop, 3), round(j * hop, 3), round(lo * hop, 3), round(hi * hop, 3)))
    return out


def repeats_neighbor(text: str, segs: list[Segment], t0: float, t1: float, near: float = 1.0) -> bool:
    """Câu nghe lại trùng với câu sát bên đã có (mốc câu cũ ước lượng lệch) -> bỏ, tránh 1 câu xuất hiện 2 lần."""
    from difflib import SequenceMatcher

    core = re.sub(r"[\W_]", "", text)
    if not core:
        return True
    for s in segs:
        if s.end < t0 - near or s.start > t1 + near:
            continue
        other = re.sub(r"[\W_]", "", s.text)
        if other and (core in other or SequenceMatcher(None, core, other).ratio() >= 0.75):
            return True
    return False


def rescue_missed(segs: list[Segment], audio: np.ndarray, decode: Callable, max_chars: int = 22,
                  progress: Prog | None = None, cancel=None) -> list[Segment]:
    """Nghe lại các đoạn có tiếng mà chưa có câu: decode(clip) -> (lời, nhãn). Trả về các câu mới (chưa sắp xếp);
    câu cũ được gộp vào câu mới có extra["absorbed"] = True – người gọi bỏ chúng đi."""
    from .asr import is_hallucination
    from .asr_funasr import split_long
    from .ffmpeg import Cancelled

    wins = missed_speech([(s.start, s.end) for s in segs], audio)
    new: list[Segment] = []
    for k, (a, b, lo, hi) in enumerate(wins):
        if cancel and cancel():
            raise Cancelled()
        if progress:
            progress(k / max(1, len(wins)), f"Nghe lại đoạn bị sót {k + 1}/{len(wins)}")
        # câu ngắn nằm SÁT cửa sổ nghe lại thường là nửa kia của cùng 1 câu ("不要再" | "说") -> nghe gộp cả hai
        touch = [s for s in segs if not s.extra.get("absorbed") and s.duration <= 2.5
                 and len(CJK.findall(s.text)) <= 8 and (abs(s.start - hi) <= 0.15 or abs(lo - s.end) <= 0.15)]
        if touch:
            lo2, hi2 = min([lo] + [s.start for s in touch]), max([hi] + [s.end for s in touch])
            text, tags = decode(audio[int(lo2 * SR):int(hi2 * SR)])
            core = re.sub(r"[\W_]", "", text or "")
            if len(core) >= max(len(re.sub(r"[\W_]", "", s.text)) for s in touch) + 2 and not is_hallucination(text):
                for s in touch:
                    s.extra["absorbed"] = True   # câu cũ bị thay bằng câu gộp (pipeline bỏ đi)
                a, b = min([a] + [s.start for s in touch]), max([b] + [s.end for s in touch])
                for x, y, t in split_long(a, b, text.strip(), max_chars):
                    ns = Segment(round(x, 3), round(y, 3), t, "", touch[0].speaker)
                    ns.extra.update({"tags": [g for g in tags if g not in ("withitn", "woitn")], "rescued": True})
                    new.append(ns)
                continue
        text, tags = decode(audio[int(lo * SR):int(hi * SR)])
        text = (text or "").strip()
        core = re.sub(r"[\W_]", "", text)
        n = len(CJK.findall(text)) if CJK.search(text) else len(core.split()) + (len(core) >= 4)
        if n < 2 or is_hallucination(text):
            continue   # mẩu 1 chữ ("啊", "嗯"…): tiếng thở / đuôi câu bên cạnh, không phải câu thoại
        if any(t in tags for t in ("Breath", "Cough", "Sneeze")) or \
                repeats_neighbor(text, [s for s in segs if not s.extra.get("absorbed")], a, b):
            continue
        for x, y, t in split_long(a, b, text, max_chars):
            s = Segment(round(x, 3), round(y, 3), t)
            s.extra["tags"] = [g for g in tags if g not in ("withitn", "woitn")]
            s.extra["rescued"] = True
            if "Sing" in tags:
                s.extra["song"] = True
            new.append(s)
    return new


# ------------------------------------------------------------------ tách khi đổi người nói

def speaker_change_points(emb_fn, clip: np.ndarray, win: float = 1.2, hop: float = 0.4,
                          thr: float = 0.3) -> list[float]:
    """Các thời điểm (giây trong clip) giọng đổi hẳn: độ giống giữa 2 cửa sổ liền kề tụt dưới ngưỡng."""
    n, h = int(win * SR), int(hop * SR)
    if len(clip) < 2 * n:
        return []
    starts = list(range(0, len(clip) - n + 1, h))
    embs = np.stack([emb_fn(clip[s:s + n]) for s in starts])
    embs /= np.linalg.norm(embs, axis=1, keepdims=True) + 1e-9
    pts = []
    step = int(round(win / hop))
    for i in range(len(starts) - step):
        j = i + step                  # 2 cửa sổ liền kề, không chồng nhau
        sim = float(embs[i] @ embs[j])
        if sim < thr:
            t = (starts[j]) / SR
            if not pts or t - pts[-1] > win:
                pts.append(t)
    return pts


def split_speaker_changes(segs: list[Segment], audio: np.ndarray, emb_fn, min_len: float = 2.4,
                          progress: Prog | None = None, cancel=None) -> list[Segment]:
    """Câu dài có chỗ đổi giọng -> tách đôi, CHỈ ở dấu câu hoặc chỗ nghỉ thật (mốc từng chữ extra["ct"]) gần chỗ
    đổi giọng (±0,8 s); không có chỗ như vậy thì giữ nguyên câu (không bao giờ cắt giữa 1 từ)."""
    from .ffmpeg import Cancelled

    out: list[Segment] = []
    for k, s in enumerate(segs):
        if cancel and cancel():
            raise Cancelled()
        pos = _char_positions(s.text)
        if s.duration < min_len or len(pos) < 4:
            out.append(s)
            continue
        clip = audio[int(s.start * SR):int(s.end * SR)]
        pts = speaker_change_points(emb_fn, clip)
        if not pts:
            out.append(s)
            continue
        ct = s.extra.get("ct")
        if ct and len(ct) == len(pos):
            times = [(a - s.start, b - s.start) for a, b in ct]
        else:
            ct = None
            mask, hop = voiced_mask(clip)
            times = char_times_energy(mask, hop, len(pos)) or []
        if len(times) != len(pos):
            out.append(s)
            continue
        allowed = [c for c in range(1, len(pos) - 2)
                   if _boundary(s.text, pos, c) in ("strong", "weak")
                   or (ct and not _boundary(s.text, pos, c) and times[c + 1][0] - times[c][1] >= 0.15)]
        cuts = []
        for t in pts:
            near = [c for c in allowed if abs((times[c][1] + times[c + 1][0]) / 2 - t) <= 0.8 and c not in cuts]
            if near:
                cuts.append(min(near, key=lambda c: abs((times[c][1] + times[c + 1][0]) / 2 - t)))
        if not cuts:
            out.append(s)
            continue
        cuts.sort()
        texts = _split_text(s.text, pos, cuts)
        edges = [s.start] + [round(s.start + (times[c][1] + times[c + 1][0]) / 2, 3) for c in cuts] + [s.end]
        bounds = [0] + [c + 1 for c in cuts] + [len(pos)]
        for j, t in enumerate(texts):
            ex = dict(s.extra)
            if ct:
                ex["ct"] = ct[bounds[j]:bounds[j + 1]]
            out.append(Segment(edges[j], edges[j + 1], t, "", None, ex))
        if progress and k % 10 == 0:
            progress(k / max(1, len(segs)), f"Đang tìm chỗ đổi người nói {k}/{len(segs)}")
    return out


def draft_lines(segs: list[Segment], settings: dict | None = None, total: float = 0.0) -> list[Segment]:
    """Câu NHÁP từ các khúc vừa nghe xong (chế độ vừa tách vừa dịch): tách câu theo mốc từng chữ có sẵn (như `refine`,
    không nạp thêm mô hình) + nối câu bị cắt ngang. Trả về bản sao – không đụng vào danh sách gốc. Câu cuối cùng (sau
    nghe lại đoạn sót, căn thời gian, phân biệt người nói) phần lớn trùng chữ với câu nháp -> dùng lại bản dịch."""
    params = split_params(settings)
    max_chars = int((settings or {}).get("asr_max_chars", 28))
    out: list[Segment] = []
    for s in segs:
        s = Segment(s.start, s.end, s.text, "", s.speaker, dict(s.extra))
        ct = s.extra.get("ct")
        if ct and len(ct) == len(_char_positions(s.text)):
            out += split_timed(s, [(float(a), float(b)) for a, b in ct], True, max_chars, total, params)
        elif len(s.text) > max_chars:
            from .asr_funasr import split_long

            out += [Segment(round(x, 3), round(y, 3), t, "", s.speaker, {})
                    for x, y, t in split_long(s.start, s.end, s.text, max_chars)]
        else:
            out.append(s)
    out = fix_overlaps(out)
    try:
        out, _ = join_broken(out, params, max_chars, total)
    except Exception:  # noqa: BLE001 – câu nháp: không nối được thì thôi
        pass
    for s in out:
        s.extra = {}
    return [s for s in out if s.text.strip()]


# ------------------------------------------------------------------ tách câu DỊCH dài ở dấu phẩy (sau khi dịch)
# Câu trọn ý (kiểu tách "dub") dịch liền mạch nhưng có câu rất dài ("为…的账，为…的雷，" -> 19 tiếng) -> phụ đề 2 dòng
# chữ dày đặc, giọng lồng đọc 1 hơi dài. Sau khi dịch: câu dài hơn mức đặt, có cùng số dấu ngắt vế (，；：) ở câu gốc
# và câu dịch -> tách đúng ở dấu ngắt chia đôi đều nhất (câu dịch đã đủ ý nên mỗi vế giữ đúng bản dịch của nó); mốc
# thời gian đặt ở chỗ ngắt hơi thật (chỗ lặng nhất của giọng quanh vị trí ước theo số chữ), vế sau có "pause_before"
# -> giọng lồng nghỉ 1 nhịp giữa 2 vế.
CLAUSE_ZH = "，,；;：:"
END_ZH = "。！？!?"
CLAUSE_TR = {"ja": "、，,；;：:", "zh": "，,；;：:"}
CLAUSE_TR_DEFAULT = ",;:，；："
END_TR = "。！？!?."
_TAIL_JUNK = " \t\"'”’)）」』】…。！？!?.,，;；:："
_AFTER_MARK = "”’\"')）」』】"
SPLIT_KEEP = ("voice", "speed", "overflow", "nodub", "trans_user", "song", "not_song")
PAIR_TOL = 0.12          # số dấu câu 2 bên khác nhau: vế dịch ứng với vế gốc khi vị trí (tỉ lệ độ dài) lệch ≤ 12 %
END_PAUSE = 1.6          # tách ở chỗ hết câu (? ! .) nghỉ dài hơn tách ở dấu phẩy


def _bounds(text: str, ends: str, clauses: str) -> list[tuple[int, str]]:
    """Chỗ ngắt BÊN TRONG câu: [(chỉ số ngay sau dấu + ngoặc đóng, "end" | "clause")]. "end" = ? ! . 。, "clause" =
    phẩy / chấm phẩy / hai chấm / ba chấm. Bỏ dấu ở cuối câu, dấu trong số ("1,5", "3.5", "10:30")."""
    out = []
    i, n = 0, len(text)
    marks = ends + clauses + "…"
    while i < n:
        if text[i] not in marks:
            i += 1
            continue
        j = i
        while j < n and text[j] in marks:
            j += 1
        run = text[i:j]
        if len(run) == 1 and run in ",.:" and 0 < i and j < n and text[i - 1].isdigit() and text[j].isdigit():
            i = j
            continue
        if run == "." and j < n and not text[j].isspace() and text[j] not in _AFTER_MARK:
            i = j                          # "TP.HCM", "v.v" – không phải hết câu
            continue
        while j < n and text[j] in _AFTER_MARK:
            j += 1
        kind = "clause" if ("…" in run or ".." in run or not any(c in ends for c in run)) else "end"
        if text[j:].strip(_TAIL_JUNK):
            out.append((j, kind))
        i = j
    return out


def _vis(text: str) -> int:
    """Số chữ có nghĩa (Hán, chữ cái, số) – ước vị trí thời gian theo câu gốc."""
    return sum(1 for c in text if c.isalnum())


def _pair_cuts(zh: str, tr: str, lang: str) -> list[tuple[int, int, str]]:
    """Cặp chỗ tách (vị trí trong câu gốc, vị trí trong câu dịch, loại). Cùng số chỗ ngắt -> ghép theo thứ tự (vế i ứng
    vế i, lệch tỉ lệ ≤ 30 %); khác số -> mỗi chỗ ngắt của câu dịch lấy chỗ ngắt câu gốc gần nhất theo tỉ lệ độ dài
    (lệch ≤ PAIR_TOL, khác loại dấu thì chặt hơn)."""
    from . import langs

    bz = _bounds(zh, END_ZH, CLAUSE_ZH)
    bt = _bounds(tr, END_TR, CLAUSE_TR.get(lang, CLAUSE_TR_DEFAULT))
    nz, nt = max(1, _vis(zh)), max(1, langs.units(tr, lang))
    sz = [(_vis(zh[:p]) / nz, p, k) for p, k in bz]
    st = [(langs.units(tr[:p], lang) / nt, p, k) for p, k in bt]
    if not sz or not st:
        return []
    out = []
    if len(sz) == len(st):
        for (fz, pz, kz), (ft, pt, kt) in zip(sz, st):
            if abs(fz - ft) <= 0.3:
                out.append((pz, pt, "end" if "end" in (kz, kt) else "clause"))
        return out
    for ft, pt, kt in st:
        fz, pz, kz = min(sz, key=lambda x: abs(x[0] - ft) + (0.04 if x[2] != kt else 0))
        if abs(fz - ft) <= (PAIR_TOL if kz == kt else PAIR_TOL * 0.6):
            out.append((pz, pt, "end" if "end" in (kz, kt) else "clause"))
    return out


def _quiet_cut(env: np.ndarray | None, hop: float, a: float, b: float, est: float) -> tuple[float, float]:
    """Chỗ ngắt hơi quanh est trong câu [a, b]: (hết vế trước, bắt đầu vế sau). Không đo được -> (est, est)."""
    if env is None or not len(env) or b - a < 0.8:
        return est, est
    dur = b - a
    lo, hi = max(a + 0.2 * dur, est - 0.3 * dur), min(b - 0.2 * dur, est + 0.3 * dur)
    ia, ib = int(lo / hop), int(hi / hop) + 1
    sa, sb = int(a / hop), int(b / hop) + 1
    if ib - ia < 3 or sb > len(env) + 2:
        return est, est
    seg = np.asarray(env[sa:min(sb, len(env))], np.float32)
    win = np.asarray(env[ia:min(ib, len(env))], np.float32)
    if not len(seg) or len(win) < 3:
        return est, est
    k = max(1, int(round(0.1 / hop)))      # làm mượt 0,1 s
    sm = np.convolve(win, np.ones(k) / k, mode="same") if len(win) >= k else win
    # ưu tiên chỗ lặng gần vị trí ước (phạt 6 dB mỗi 1/3 câu lệch)
    pos = np.arange(len(sm)) * hop + ia * hop
    score = sm + 6.0 * np.abs(pos - est) / max(0.1, dur / 3)
    j = int(np.argmin(score))
    floor = float(np.median(seg))
    if sm[j] > floor - 8.0:                 # không có chỗ lặng rõ -> cắt tại chỗ ước, 2 vế liền nhau
        t = float(pos[j]) if abs(float(pos[j]) - est) < 0.15 * dur else est
        return t, t
    thr = float(sm[j]) + 4.0
    l = r = j
    while l > 0 and sm[l - 1] <= thr:
        l -= 1
    while r + 1 < len(sm) and sm[r + 1] <= thr:
        r += 1
    t0, t1 = float(pos[l]), float(pos[r]) + hop
    if t1 - t0 > 0.6 * dur:
        mid = (t0 + t1) / 2
        return mid, mid
    return round(t0 + 0.02, 3), round(t1 - 0.02, 3) if t1 - t0 > 0.08 else round(t0 + 0.02, 3)


def split_clauses(seg: Segment, lang: str = "vi", max_units: float = 14, min_units: float = 4,
                  env: np.ndarray | None = None, hop: float = 0.05, pause: float = 0.25) -> list[Segment]:
    """Tách 1 câu đã dịch quá dài (> max_units tiếng – quy đổi theo tốc độ nói của tiếng đích) thành các vế ở chỗ hết
    câu (? ! .) hoặc dấu phẩy BÊN TRONG câu, chỗ đó phải có ở cả câu gốc lẫn câu dịch (`_pair_cuts`). Ưu tiên chỗ hết
    câu, rồi chia đều số tiếng; mỗi vế ≥ min_units tiếng. Các vế cùng mã `split` (lệnh nối câu không nối lại), vế sau
    nghỉ `pause` giây (hết câu × END_PAUSE). Không tách được -> [seg] (đúng đối tượng cũ)."""
    from . import langs

    tr, zh = (seg.trans or "").strip(), (seg.text or "").strip()
    if not tr or not zh or seg.extra.get("song") or seg.end - seg.start < 1.2:
        return [seg]
    lang = langs.norm(lang)
    k = langs.INFO[lang]["rate"] / langs.INFO["vi"]["rate"]
    lim, mn = max_units * k, max(1.0, min_units * k)
    if langs.units(tr, lang) <= lim:
        return [seg]
    best = None
    for pz, pt, kind in _pair_cuts(zh, tr, lang):
        u1, u2 = langs.units(tr[:pt], lang), langs.units(tr[pt:], lang)
        if u1 < mn or u2 < mn or _vis(zh[:pz]) < 2 or _vis(zh[pz:]) < 2:
            continue
        sc = (kind != "end", abs(u1 - u2))
        if best is None or sc < best[0]:
            best = (sc, pz, pt, kind)
    if best is None:
        return [seg]
    _, pz, pt, kind = best
    n1, n2 = _vis(zh[:pz]), _vis(zh[pz:])
    est = seg.start + (seg.end - seg.start) * n1 / max(1, n1 + n2)
    from .subtitle import ct_of

    ct = ct_of(seg)
    kh = len(CJK.findall(zh[:pz]))
    use_ct = bool(ct) and 0 < kh < len(ct)
    if use_ct:   # mốc từng chữ thật (2.0.96): chỗ tách nằm giữa chữ cuối vế trước và chữ đầu vế sau
        ce, cs = float(ct[kh - 1][1]), float(ct[kh][0])
        est = (ce + cs) / 2 if cs >= ce else ce
    e1, s2 = _quiet_cut(env, hop, seg.start, seg.end, est)
    if use_ct:
        e1 = min(max(e1, ce), max(ce, cs))
        s2 = min(max(s2, e1), max(ce, cs))
    e1 = min(max(e1, seg.start + 0.3), seg.end - 0.3)
    s2 = min(max(s2, e1), seg.end - 0.3)
    sid = seg.extra.get("split") or f"{seg.start:.3f}"
    ex = {k2: v for k2, v in seg.extra.items() if k2 in SPLIT_KEEP}
    ex["split"] = sid
    ea = dict(ex)
    if seg.extra.get("pause_before"):     # vế đầu giữ khoảng nghỉ với vế đứng trước nó (tách nhiều lần)
        ea["pause_before"] = seg.extra["pause_before"]
    gap = round(float(pause) * (END_PAUSE if kind == "end" else 1.0), 3)
    eb = dict(ex, pause_before=gap) if gap > 0 else dict(ex)
    if use_ct:
        ea["ct"], eb["ct"] = ct[:kh], ct[kh:]
    sn = seg.extra.get("snap")
    if sn:
        ea["snap"], eb["snap"] = [bool(sn[0]), False], [False, bool(sn[1])]
    a = Segment(round(seg.start, 3), round(e1, 3), zh[:pz].strip(), tr[:pt].strip(), seg.speaker, ea)
    b = Segment(round(s2, 3), round(seg.end, 3), zh[pz:].strip(), tr[pt:].strip(), seg.speaker, eb)
    return split_clauses(a, lang, max_units, min_units, env, hop, pause) + \
        split_clauses(b, lang, max_units, min_units, env, hop, pause)


def split_mates(a: Segment, b: Segment) -> bool:
    """2 dòng liền nhau là các vế của 1 câu dịch dài đã tách có chủ ý -> lệnh nối câu (luật / AI) không nối lại."""
    sid = a.extra.get("split")
    return bool(sid) and sid == b.extra.get("split")


def split_long_lines(segs: list[Segment], lang: str = "vi", max_units: float = 14, min_units: float = 4,
                     env: np.ndarray | None = None, hop: float = 0.05, pause: float = 0.25,
                     rows=None) -> tuple[list[Segment], list[int]]:
    """Tách mọi câu dịch quá dài (hoặc chỉ các câu rows). Trả (danh sách mới, số thứ tự các câu mới)."""
    out: list[Segment] = []
    made: list[int] = []
    pick = set(rows) if rows is not None else None
    for i, s in enumerate(segs):
        parts = split_clauses(s, lang, max_units, min_units, env, hop, pause) if pick is None or i in pick else [s]
        if len(parts) > 1:
            made += list(range(len(out), len(out) + len(parts)))
        out += parts
    return out, made
