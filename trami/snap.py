"""Bám mép câu theo giọng đã tách (2.0.96).

Mốc câu từ bộ căn thời gian (Qwen3-ForcedAligner / fa-zh / ước lượng theo độ to) lệch ±0,1–0,2 s so với lúc nhân vật
thật sự bắt đầu / thôi nói (đo trên ảnh timeline người dùng gửi: sớm 0,11–0,15 s, trễ 0,13 s). Bước này nhìn thẳng độ
to giọng đã tách (mỗi 10 ms): quanh đầu câu tìm chỗ "lặng -> có tiếng" gần nhất, quanh cuối câu chỗ "có tiếng -> lặng"
gần nhất, ngưỡng lặng tính theo độ to của chính câu đó (tiếng thở / nhạc rò không tính là nói). Không lấn câu bên cạnh,
không vượt mốc chữ đầu / chữ cuối đã đo; câu nói liền không có chỗ ngắt giữ nguyên.

extra["snap"] = [đầu bám được, cuối bám được]; câu không có mốc từng chữ thật (extra["ct"]) mà không bám được cả 2 đầu
= mốc ước lượng (`estimated`, tô khác trên timeline).
"""
from __future__ import annotations

import numpy as np

from .align import CJK, _runs

HOP = 0.01          # độ to mỗi 10 ms
WIN = 0.35          # tìm chỗ bắt đầu / thôi nói trong ±0,35 s quanh mốc cũ
WIN_EST = 0.8       # câu chưa có mốc từng chữ thật (mốc ước lượng / phụ đề OCR): tìm rộng hơn
DROP_DB = 22.0      # thấp hơn độ to của chính câu 22 dB = lặng
FLOOR_DB = 6.0      # ... và phải to hơn nền ồn của cả phim ít nhất 6 dB mới là tiếng
MIN_CONTRAST = 10.0  # câu to hơn nền ồn chưa tới 10 dB (nhạc rò, giọng quá nhỏ): không đoán được -> giữ nguyên
MIN_SIL = 0.08      # lặng ≥ 80 ms mới là chỗ ngắt (khe giữa 2 âm tiết ngắn hơn)
MIN_VOICE = 0.05    # tiếng ≥ 50 ms (không phải tiếng tách / tiếng click)
GAP = 0.02          # cách câu bên cạnh tối thiểu
MOVED = 0.02        # dời > 20 ms mới tính là đã sửa
CT_SLACK = 0.3      # bộ căn có thể lệch: cho phép đầu câu muộn hơn chữ đầu đã đo / cuối câu sớm hơn chữ cuối tới 0,3 s


def ct_ok(s) -> bool:
    """Câu có mốc từng chữ thật còn khớp lời (đủ số chữ Hán, nằm quanh câu – không cũ sau khi kéo / sửa)."""
    ct = s.extra.get("ct")
    if not ct or len(ct) != len(CJK.findall(s.text or "")):
        return False
    try:
        return float(ct[0][0]) >= s.start - 1.0 and float(ct[-1][1]) <= s.end + 1.0
    except (TypeError, ValueError, IndexError):
        return False


def estimated(s) -> bool:
    """Mốc câu chỉ là ước lượng: không có mốc từng chữ thật và chưa bám được cả 2 đầu theo giọng."""
    sn = s.extra.get("snap") or [False, False]
    return not ct_ok(s) and not (sn[0] and sn[1])


def _mask(env: np.ndarray, thr: float, hop: float) -> np.ndarray:
    """Khung có tiếng: to hơn ngưỡng, lấp khe ≤ 30 ms giữa tiếng, bỏ tiếng < MIN_VOICE."""
    m = env > thr
    fill = max(1, int(round(0.03 / hop)))
    for i, j in list(_runs(m, False)):
        if 0 < i and j < len(m) and j - i <= fill:
            m[i:j] = True
    nv = max(1, int(round(MIN_VOICE / hop)))
    for i, j in list(_runs(m, True)):
        if j - i < nv:
            m[i:j] = False
    return m


def _floor(env: np.ndarray) -> float:
    e = env[env > -95]
    return float(np.percentile(e, 5)) if len(e) else -90.0


def _voiced_at(env: np.ndarray, hop: float, t: float, thr: float) -> bool:
    """Lúc t đang có tiếng (theo cùng mặt nạ với _edges)."""
    k = int(t / hop)
    a, b = max(0, k - 30), min(len(env), k + 30)
    if not a <= k < b:
        return False
    return bool(_mask(env[a:b].copy(), thr, hop)[k - a])


def _edges(env: np.ndarray, hop: float, lo: float, hi: float, thr: float):
    """Chỗ bắt đầu nói (lặng ≥ MIN_SIL rồi có tiếng) và chỗ thôi nói (có tiếng rồi lặng ≥ MIN_SIL) trong [lo, hi].
    Lặng tính cả phần ngay ngoài cửa sổ (đo trên env) để chỗ sát mép cửa sổ vẫn nhận ra."""
    ns = max(1, int(round(MIN_SIL / hop)))
    a = max(0, int(lo / hop) - ns)
    b = min(len(env), int(np.ceil(hi / hop)) + ns)
    if b - a < 3:
        return [], []
    m = _mask(env[a:b].copy(), thr, hop)
    on, off = [], []
    for i, j in _runs(m, True):
        if i >= ns and not m[i - ns:i].any():
            t = (a + i) * hop
            if lo - 1e-6 <= t <= hi + 1e-6:
                on.append(t)
        if j <= len(m) - ns and not m[j:j + ns].any():
            t = (a + j) * hop
            if lo - 1e-6 <= t <= hi + 1e-6:
                off.append(t)
    return on, off


def _thr(env: np.ndarray, hop: float, a: float, b: float, floor: float) -> float | None:
    """Ngưỡng "có tiếng" cho đoạn [a, b] theo độ to của chính đoạn đó (None: quá nhỏ so với nền, không đoán được)."""
    seg = env[max(0, int(a / hop)):max(int(a / hop) + 1, int(np.ceil(b / hop)))]
    if len(seg) < 3:
        return None
    level = float(np.percentile(seg, 80))
    if level - floor < MIN_CONTRAST:
        return None
    return max(level - DROP_DB, floor + FLOOR_DB)


def _clusters(env: np.ndarray, hop: float, a: float, b: float, thr: float, gap: float = 0.15) -> list[list[float]]:
    """Các cụm có tiếng trong [a, b] (giây), cụm cách nhau < gap giây gộp lại."""
    fa = max(0, int(a / hop))
    fb = min(len(env), max(fa + 1, int(np.ceil(b / hop))))
    if fb - fa < 2:
        return []
    out: list[list[float]] = []
    for i, j in _runs(_mask(env[fa:fb].copy(), thr, hop), True):
        x, y = (fa + i) * hop, (fa + j) * hop
        if out and x - out[-1][1] < gap:
            out[-1][1] = y
        else:
            out.append([x, y])
    return out


def voiced_span(env: np.ndarray, hop: float, a: float, b: float, floor: float | None = None):
    """(lúc bắt đầu có tiếng, lúc hết tiếng) trong [a, b] theo giọng đã tách; None nếu không đoán được."""
    env = np.asarray(env, np.float32)
    thr = _thr(env, hop, a, b, _floor(env) if floor is None else floor)
    cl = _clusters(env, hop, a, b, thr) if thr is not None else []
    return (cl[0][0], cl[-1][1]) if cl else None


def fix_stretched(segs: list, env: np.ndarray, hop: float = HOP, rows=None) -> int:
    """Chữ bị bộ căn thời gian kéo dài bất thường (`align.stretched_chars`): co lại theo độ to giọng đã tách – chữ cuối
    / chữ giữa giữ phần có tiếng ở đầu (đoạn lặng phía sau là chỗ nhân vật ngừng), chữ đầu giữ phần có tiếng ở cuối.
    Đầu / cuối câu theo chữ đầu / chữ cuối đã sửa. Trả số câu đã sửa (2.1.3)."""
    from .align import PAD, _latin_run, stretched_chars

    env = np.asarray(env, np.float32)
    n = len(segs)
    pick = range(n) if rows is None else sorted({int(r) for r in rows if 0 <= int(r) < n})
    if not len(env):
        return 0
    floor = _floor(env)
    fixed = 0
    for i in pick:
        s = segs[i]
        if not ct_ok(s):
            continue
        ct = [[float(a), float(b)] for a, b in s.extra["ct"]]
        bad = stretched_chars(ct)
        if not bad:
            continue
        thr = _thr(env, hop, ct[0][0], ct[-1][1], floor)
        if thr is None:
            continue
        good = [b - a for k, (a, b) in enumerate(ct) if k not in bad]
        med = float(np.median(good)) if good else 0.25
        last = len(ct) - 1
        changed = False
        for k in bad:
            a, b = ct[k]
            cl = _clusters(env, hop, a, b, thr)
            head = bool(cl) and cl[0][0] - a < 0.12      # có tiếng ngay đầu chữ
            tail = bool(cl) and b - cl[-1][1] < 0.12     # có tiếng tới cuối chữ
            if not cl:
                na, nb = (b - med, b) if k == 0 and last > 0 else (a, a + med)
            elif k == 0 and last > 0:          # chữ đầu: bộ căn cho bắt đầu từ tiếng động / lặng trước câu
                na, nb = cl[-1][0], (b if tail else cl[-1][1])
            elif k == last:                    # chữ cuối: kéo qua đoạn lặng sau câu
                na, nb = (a if head else cl[0][0]), cl[0][1]
            elif head:                         # chữ giữa: nhân vật ngừng SAU chữ này
                na, nb = a, cl[0][1]
            elif tail:                         # ... ngừng TRƯỚC chữ này
                na, nb = cl[-1][0], b
            else:
                na, nb = cl[0][0], cl[0][1]
            na = max(na, ct[k - 1][1] if k > 0 else na)
            nb = min(nb, ct[k + 1][0] if k < last else nb)
            if nb - na >= 0.04 and (abs(na - a) > 0.05 or abs(nb - b) > 0.05):
                ct[k] = [round(na, 3), round(nb, 3)]
                changed = True
        if not changed:
            continue
        s.extra["ct"] = ct
        lo = segs[i - 1].end + GAP if i > 0 else 0.0
        if 0 in bad and not _latin_run(s.text, True):
            s.start = round(max(lo, ct[0][0] - PAD), 3)
        if last in bad and not _latin_run(s.text, False):
            s.end = round(max(s.start + 0.2, ct[-1][1] + PAD), 3)
        fixed += 1
    return fixed


OPEN_GAP = 0.3      # chỗ lặng ≥ 0,3 s bên trong câu = nhân vật ngừng (như dub_pause_min)
OPEN_NEAR = 0.25    # chỗ ranh giới 2 chữ (theo bộ căn) cách đoạn lặng ≤ 0,25 s mới coi là chỗ ngừng của 2 chữ đó
MIN_CHAR = 0.04     # mỗi chữ còn ≥ 40 ms sau khi sửa


def _voiced_len(env: np.ndarray, hop: float, a: float, b: float, thr: float) -> float:
    fa, fb = max(0, int(a / hop)), min(len(env), int(np.ceil(b / hop)))
    return float(np.sum(env[fa:fb] > thr)) * hop if fb > fa else 0.0


def _ct_valid(ct: list) -> bool:
    return all(b - a >= MIN_CHAR - 1e-6 for a, b in ct) and \
        all(ct[k][1] <= ct[k + 1][0] + 1e-6 for k in range(len(ct) - 1))


def open_pauses(segs: list, env: np.ndarray, hop: float = HOP, min_gap: float = OPEN_GAP, rows=None) -> list[int]:
    """Chỗ nhân vật NGỪNG giữa câu mà mốc từng chữ không có khe (2.1.20): bộ căn thời gian hay kéo chữ qua đoạn lặng
    khi nói ngắt quãng – đếm chậm "一……二……三", có người khác chen ngang – nên khe giữa 2 chữ = 0 -> không thêm dấu
    phẩy, giọng lồng đọc liền 1 mạch. Đo độ to giọng đã tách: đoạn lặng ≥ min_gap nằm TRONG câu -> đặt lại mốc 2 chữ ở
    2 bên cho có khe đúng đoạn lặng (ranh giới 2 chữ gần đoạn lặng nhất; đoạn lặng nằm giữa 1 chữ -> chữ đó là phần có
    tiếng nhiều hơn, phần còn lại thuộc chữ bên cạnh). Chữ đầu / cuối kéo qua đoạn lặng ở mép câu -> co lại, mép câu
    theo. Không đổi chữ. Trả các câu đã sửa."""
    env = np.asarray(env, np.float32)
    n = len(segs)
    pick = range(n) if rows is None else sorted({int(r) for r in rows if 0 <= int(r) < n})
    if not len(env):
        return []
    floor = _floor(env)
    changed: list[int] = []
    for i in pick:
        s = segs[i]
        if not ct_ok(s) or len(s.extra["ct"]) < 2:
            continue
        ct = [[float(a), float(b)] for a, b in s.extra["ct"]]
        a0, b0 = ct[0][0], ct[-1][1]
        thr = _thr(env, hop, a0, b0, floor)
        if thr is None:
            continue
        cl = _clusters(env, hop, a0, b0, thr)
        sil = [(cl[j][1], cl[j + 1][0]) for j in range(len(cl) - 1) if cl[j + 1][0] - cl[j][1] >= min_gap]
        new = [list(c) for c in ct]
        used: set[int] = set()
        for x0, x1 in sil:
            if any(new[k + 1][0] - new[k][1] >= 0.6 * (x1 - x0) and new[k][1] < x1 and new[k + 1][0] > x0
                   for k in range(len(new) - 1)):
                continue   # mốc chữ đã có khe ở đúng đoạn lặng
            dist = []
            for k in range(len(new) - 1):
                lo, hi = new[k][1], new[k + 1][0]
                d = 0.0 if lo <= x1 and hi >= x0 else min(abs(lo - x1), abs(hi - x0), abs(lo - x0), abs(hi - x1))
                dist.append((d, k))
            d, k = min(dist)
            if d > OPEN_NEAR:   # đoạn lặng nằm giữa 1 chữ: chữ đó ở phía có tiếng nhiều hơn
                m = (x0 + x1) / 2
                inside = [j for j, (a, b) in enumerate(new) if a < m < b]
                if not inside:
                    continue
                j = inside[0]
                before = _voiced_len(env, hop, new[j][0], x0, thr)
                after = _voiced_len(env, hop, x1, new[j][1], thr)
                k = j if before >= after else j - 1
                if not 0 <= k < len(new) - 1:
                    continue
            if k in used:
                continue
            trial = [list(c) for c in new]
            trial[k][1], trial[k + 1][0] = x0, x1   # chữ trước thôi đúng lúc lặng, chữ sau vào đúng lúc có tiếng lại
            if not _ct_valid(trial):
                continue
            new = trial
            used.add(k)
        # chữ đầu / chữ cuối bị kéo qua đoạn lặng ở mép câu (câu vừa tách ở chỗ ngừng): co về phần có tiếng, mép câu
        # theo (snap sau đó bám đúng chỗ bắt đầu / thôi nói)
        edge = [False, False]
        if cl and cl[-1][1] < new[-1][1] - min_gap and cl[-1][1] - new[-1][0] >= MIN_CHAR:
            new[-1][1] = cl[-1][1]
            edge[1] = True
        if cl and cl[0][0] > new[0][0] + min_gap and new[0][1] - cl[0][0] >= MIN_CHAR:
            new[0][0] = cl[0][0]
            edge[0] = True
        if (used or any(edge)) and _ct_valid(new):
            s.extra["ct"] = [[round(a, 3), round(b, 3)] for a, b in new]
            if edge[0]:
                s.start = round(max(s.start, new[0][0] - 0.05), 3)
            if edge[1]:
                s.end = round(max(s.start + 0.2, min(s.end, new[-1][1] + 0.05)), 3)
            changed.append(i)
    return changed


def snap(segs: list, env: np.ndarray, hop: float = HOP, rows=None, total: float = 0.0) -> dict:
    """Dời đầu / cuối các câu (tại chỗ) về đúng lúc nhân vật bắt đầu / thôi nói. rows: chỉ các câu này (số thứ tự
    trong segs đã sắp theo giờ). Trả {"moved": số câu dời, "start": số đầu câu dời, "end": số cuối câu dời,
    "shift": dời trung bình (giây), "est": số câu mốc ước lượng còn lại, "checked": số câu đã xét}."""
    env = np.asarray(env, np.float32)
    n = len(segs)
    pick = set(range(n)) if rows is None else {int(r) for r in rows if 0 <= int(r) < n}
    if not len(env) or not pick:
        return {"moved": 0, "start": 0, "end": 0, "shift": 0.0, "est": sum(estimated(s) for s in segs),
                "checked": 0}
    floor = _floor(env)
    end_t = total or len(env) * hop
    orig = [(s.start, s.end) for s in segs]
    flags: dict[int, list[bool]] = {i: [False, False] for i in pick}
    for _ in range(2):   # lượt 2: câu bên cạnh đã về đúng chỗ -> câu bị chặn ở lượt 1 tới được mép thật
        for i in sorted(pick):
            s = segs[i]
            fa, fb = int(s.start / hop), max(int(s.start / hop) + 1, int(s.end / hop))
            seg_env = env[fa:fb]
            if len(seg_env) < 5:
                seg_env = env[max(0, fa - 5):fb + 5]
            if not len(seg_env):
                continue
            level = float(np.percentile(seg_env, 80))
            if level - floor < MIN_CONTRAST:
                continue
            thr = max(level - DROP_DB, floor + FLOOR_DB)
            exact = ct_ok(s)
            win = WIN if exact else WIN_EST
            prev_end = segs[i - 1].end if i > 0 else 0.0
            next_start = segs[i + 1].start if i + 1 < n else end_t
            ct = s.extra.get("ct") if exact else None
            # đầu câu: không trước câu trước, không sau chữ đầu tiên đã đo / quá sát cuối câu
            lo = max(0.0, prev_end + GAP if i > 0 else 0.0, s.start - win)
            hi = min(s.start + win, s.end - 0.15, float(ct[0][1]) + CT_SLACK if ct else s.end)
            on, _o = _edges(env, hop, lo, hi, thr) if hi > lo else ([], [])
            # mốc đang nằm trong tiếng nói -> lùi về chỗ bắt đầu của đoạn tiếng đó; nằm trong lặng -> tới chỗ bắt đầu
            # nói kế tiếp (không nhảy vào chỗ ngắt hơi giữa câu dù gần hơn)
            on = [x for x in on if x <= s.start + 1e-6] if _voiced_at(env, hop, s.start, thr) else \
                [x for x in on if x >= s.start - 1e-6]
            if on:
                t = min(on, key=lambda x: abs(x - s.start))
                s.start = round(t, 3)
                flags[i][0] = True
            # cuối câu: không sau câu sau, không trước chữ cuối đã đo
            lo = max(s.start + 0.15, s.end - win, float(ct[-1][0]) - CT_SLACK if ct else s.start + 0.15)
            hi = min(next_start - GAP if i + 1 < n else end_t, s.end + win)
            _n, off = _edges(env, hop, lo, hi, thr) if hi > lo else ([], [])
            off = [x for x in off if x >= s.end - 1e-6] if _voiced_at(env, hop, max(0.0, s.end - hop), thr) else \
                [x for x in off if x <= s.end + 1e-6]
            if off:
                t = min(off, key=lambda x: abs(x - s.end))
                s.end = round(t, 3)
                flags[i][1] = True
    moved = st = en = 0
    shift = 0.0
    for i in pick:
        s = segs[i]
        old = s.extra.get("snap") or [False, False]
        s.extra["snap"] = [bool(flags[i][0] or old[0]), bool(flags[i][1] or old[1])]
        ds, de = abs(s.start - orig[i][0]), abs(s.end - orig[i][1])
        st += ds > MOVED
        en += de > MOVED
        if ds > MOVED or de > MOVED:
            moved += 1
            shift += max(ds, de)
    return {"moved": moved, "start": st, "end": en, "shift": round(shift / moved, 3) if moved else 0.0,
            "est": sum(estimated(s) for s in segs), "checked": len(pick)}
