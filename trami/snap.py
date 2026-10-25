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
