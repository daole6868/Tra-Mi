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
LONG_PAUSE = 0.6     # có mốc từng chữ thật: nghỉ dài hơn mức này giữa 2 chữ (không dấu câu) cũng là hết câu
SHORT_PIECE = 2      # mẩu ≤ 2 chữ ("哇，", "嘿嘿，") gộp vào câu sát bên nếu nói liền
STRONG = "。！？!?…"
WEAK = "，,；;：:"
PAD = 0.05           # chừa thêm trước/sau mỗi câu
Prog = Callable[[float, str], None]


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
    """Cắt text sau chữ Hán thứ cuts[k]; dấu câu đi theo phần trước, số / chữ Latin đi theo phần sau."""
    parts, last = [], 0
    for c in cuts:
        end = pos[c] + 1
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
    ok = list(cand)
    try:
        import jieba

        jieba.setLogLevel(60)
        ends, i = set(), 0
        for w in jieba.cut(text):
            i += len(w)
            ends.add(i - 1)                       # vị trí ký tự cuối của mỗi từ
        ok = [k for k in ok if pos[k] in ends or pos[k + 1] - pos[k] > 1] or ok
    except Exception:  # noqa: BLE001 – không có jieba: cắt gần giữa
        pass
    return min(ok, key=lambda k: abs(k - mid))


def plan_cuts(text: str, pos: list[int], times: list[tuple[float, float]], max_chars: int = 22,
              exact: bool = False) -> list[int]:
    """Chỗ tách câu (cắt SAU chữ Hán thứ k). Chỉ tách ở dấu câu, ở chỗ nghỉ dài thật (khi có mốc từng chữ) và khi
    câu quá dài – không bao giờ cắt giữa 1 từ; mẩu quá ngắn được gộp lại vào câu sát bên."""
    n = len(pos)
    if n < 2:
        return []
    kind = [_boundary(text, pos, k) for k in range(n - 1)]
    gap = [max(0.0, times[k + 1][0] - times[k][1]) for k in range(n - 1)]
    cuts = {k for k in range(n - 1) if kind[k] in ("strong", "weak")}
    if exact:
        cuts |= {k for k in range(n - 1) if not kind[k] and gap[k] >= LONG_PAUSE and k >= 1 and n - k - 1 >= 2}
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
                if other and c in cuts and size + other <= max_chars \
                        and (gap[c] < 0.6 if kind[c] != "strong" else (size <= 1 and gap[c] < 0.3)):
                    if best is None or gap[c] < best[0]:
                        best = (gap[c], c)
        if best is None:
            break
        cuts.remove(best[1])
    # vế quá dài, không dấu câu: cắt ở chỗ nghỉ dài nhất (có mốc thật) hoặc ranh giới từ gần giữa
    out: list[int] = []
    bounds = [-1] + cuts + [n - 1]
    for a, b in zip(bounds, bounds[1:]):
        pieces = [(a, b)]
        while pieces:
            x, y = pieces.pop()
            if y - x <= max_chars or y - x < 4:
                out.append(y)
                continue
            lo, hi = x + 1 + (y - x) // 4, y - (y - x) // 4
            cand = [k for k in range(lo, hi) if kind[k] != "word"] or list(range(lo, hi))
            if exact and max(gap[k] for k in cand) >= 0.12:
                c = max(cand, key=lambda k: gap[k])
            else:
                c = _word_cut(text, pos, cand)
            pieces += [(c, y), (x, c)]
    return sorted(set(out) - {n - 1})


def refine(segs: list[Segment], audio: np.ndarray, use_fa: bool = True, progress: Prog | None = None,
           cancel=None, duration: float = 0.0, max_chars: int = 22) -> list[Segment]:
    """Trả về danh sách câu mới: mốc sát lời thật, mỗi dòng là 1 câu / vế câu trọn vẹn (`plan_cuts`).
    Câu không phải tiếng Trung giữ nguyên mốc (chỉ thu theo độ to). Có mốc từng chữ thật thì lưu vào extra["ct"]
    (để tách khi đổi người nói đúng chỗ nghỉ; xoá trước khi lưu dự án)."""
    from .ffmpeg import Cancelled

    total = duration or len(audio) / SR
    out: list[Segment] = []
    # nạp mô hình căn thời gian đúng 1 lần cho cả video (lỗi -> cả video dùng cách dự phòng)
    fa_ok = use_fa and any(CJK.search(s.text) for s in segs) and fa_usable()
    for k, s in enumerate(segs):
        if cancel and cancel():
            raise Cancelled()
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
        times = [(a0 + a, a0 + b) for a, b in times]
        cuts = plan_cuts(s.text, pos, times, max_chars, exact)
        texts = _split_text(s.text, pos, cuts) if cuts else [s.text]
        bounds = [0] + [c + 1 for c in cuts] + [len(times)]
        for j, t in enumerate(texts):
            ta, tb = times[bounds[j]], times[bounds[j + 1] - 1]
            ns = Segment(round(max(0.0, ta[0] - PAD), 3), round(min(total, tb[1] + PAD), 3), t,
                         s.trans if not cuts else "", s.speaker, dict(s.extra))
            if ns.end - ns.start < 0.2:
                ns.end = round(ns.start + 0.2, 3)
            if exact:
                ns.extra["ct"] = [[round(x, 3), round(y, 3)] for x, y in times[bounds[j]:bounds[j + 1]]]
            out.append(ns)
        if progress and k % 10 == 0:
            progress(k / max(1, len(segs)), f"Đang căn thời gian và tách câu {k}/{len(segs)}")
    # không để câu chồng lên nhau sau khi nới mốc
    out.sort(key=lambda x: x.start)
    for a, b in zip(out, out[1:]):
        if a.end > b.start:
            mid = round((a.end + b.start) / 2, 3) if a.end - b.start < 0.2 else b.start
            a.end = max(a.start + 0.1, min(a.end, mid))
            b.start = max(b.start, a.end)
    return out


def drop_char_times(segs: list[Segment]) -> None:
    for s in segs:
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
