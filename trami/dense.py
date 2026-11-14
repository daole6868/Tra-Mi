"""Giãn video ở ĐOẠN NÓI NHANH (2.3.8) – hàm thuần, không đụng âm thanh / giao diện.

Đoạn nói nhanh = từ `min_lines` câu lồng tiếng liền nhau trở lên mà giọng lồng phải đọc nhanh hơn `thr` (mặc định ×1,1)
mới vừa chỗ (cho xen tối đa 1 câu bình thường khi `mix`). Đo trên giờ GỐC chưa giãn, bộ xếp giọng chạy nháp với tốc độ
tự tính (không tính tốc độ đặt tay) -> bấm Tìm lại sau khi đã giãn vẫn ra đúng các đoạn đó (không bị nhảy).

3 cách giãn (`how`):
- "gaps"  giãn NGHỈ giữa các câu: chỗ nhân vật đã thôi nói (đo theo giọng thật – `timewarp.voice_speech`) tới câu sau
          chạy chậm; lúc nhân vật nói vẫn ×1. Tốc độ đọc không đặt tay – bộ xếp giọng tự tính lại theo chỗ mới.
- "block" giãn CẢ ĐOẠN (kể cả lúc nói) cùng 1 tốc độ; mỗi câu tự tính lại tốc độ đọc theo chỗ mới.
- "both"  cả đoạn chậm nhẹ + thêm nghỉ.
Kết quả = các đoạn [a, b, tốc độ] (giây gốc) -> `timewarp.Warp`, lưu cố định ở edit["dense"] khi bấm Áp dụng."""
from __future__ import annotations

import time

from . import timewarp

HOWS = [("gaps", "Giãn nghỉ giữa các câu"), ("block", "Giãn cả đoạn"), ("both", "Cả hai (cả đoạn + nghỉ)")]
GAP_LEVELS = [("auto", "Vừa đủ (tự tính)"), ("0.2", "Nhẹ – thêm ≤ 0,2 s mỗi chỗ"), ("0.4", "Vừa – thêm ≤ 0,4 s mỗi chỗ"),
              ("0.7", "Mạnh – thêm ≤ 0,7 s mỗi chỗ"), ("custom", "Tuỳ chỉnh")]
BLOCK_LEVELS = [("auto", "Vừa đủ (tự tính)"), ("0.9", "Nhẹ – 0,9×"), ("0.85", "Vừa – 0,85×"), ("0.8", "Mạnh – 0,8×"),
                ("custom", "Tuỳ chỉnh")]
DEFAULTS = {"dense_min": 5, "dense_thr": 1.1, "dense_mix": True, "dense_how": "gaps", "dense_gap": "auto",
            "dense_gap_add": 0.4, "dense_gap_floor": 0.4, "dense_block": "auto", "dense_block_rate": 0.85,
            "dense_block_floor": 0.75, "dense_cap": 6.0}
BREAK_GAP = 1.5       # câu cách câu trước > 1,5 s (lặng thật) -> hết đoạn
AUTO_GAP_MAX = 1.5    # "Vừa đủ": mỗi chỗ nghỉ thêm tối đa 1,5 s
GAP = 0.04            # = tts.GAP: khoảng hở tối thiểu giữa 2 đoạn giọng lồng
EPS = 0.005


def opts(settings: dict | None) -> dict:
    """Tuỳ chọn (Cài đặt dense_*) đã kẹp vào khoảng hợp lệ."""
    st = settings or {}
    o = {}
    for k, v in DEFAULTS.items():
        x = st.get(k, v)
        try:
            o[k] = type(v)(x) if not isinstance(v, bool) else bool(x)
        except (TypeError, ValueError):
            o[k] = v
    o["dense_min"] = max(2, min(30, int(o["dense_min"])))
    o["dense_thr"] = max(1.0, min(2.0, float(o["dense_thr"])))
    o["dense_how"] = o["dense_how"] if o["dense_how"] in dict(HOWS) else "gaps"
    o["dense_gap"] = o["dense_gap"] if o["dense_gap"] in dict(GAP_LEVELS) else "auto"
    o["dense_block"] = o["dense_block"] if o["dense_block"] in dict(BLOCK_LEVELS) else "auto"
    o["dense_gap_add"] = max(0.05, min(3.0, float(o["dense_gap_add"])))
    o["dense_gap_floor"] = max(0.2, min(0.95, float(o["dense_gap_floor"])))
    o["dense_block_rate"] = max(0.5, min(0.99, float(o["dense_block_rate"])))
    o["dense_block_floor"] = max(0.5, min(0.99, float(o["dense_block_floor"])))
    o["dense_cap"] = max(0.5, min(60.0, float(o["dense_cap"])))
    return o


def _gap_add(o: dict) -> float | None:
    """Giây thêm mỗi chỗ nghỉ theo mức đã chọn (None = Vừa đủ)."""
    g = o["dense_gap"]
    if g == "auto":
        return None
    return o["dense_gap_add"] if g == "custom" else float(g)


def _block_rate(o: dict) -> float | None:
    b = o["dense_block"]
    if b == "auto":
        return None
    return o["dense_block_rate"] if b == "custom" else float(b)


def find_runs(segs, items: list[dict], plan: dict, o: dict) -> list[dict]:
    """Các đoạn nói nhanh: {"rows" (câu lồng trong đoạn, theo thời gian), "fast" (câu đọc nhanh), "a", "b"}.
    items / plan = mục + kết quả bộ xếp giọng chạy nháp trên giờ gốc, tốc độ tự tính."""
    thr = o["dense_thr"]
    order = [x["k"] for x in sorted(items, key=lambda x: (float(x["want"]), int(x["k"])))]
    dubbed = set(order)
    others = sorted(float(s.start) for i, s in enumerate(segs)
                    if i not in dubbed and (s.text or s.trans or "").strip())

    by_k = {x["k"]: x for x in items}

    def fast(k):
        """Câu phải đọc nhanh: bộ xếp đọc nhanh hơn ngưỡng (hoặc vẫn tràn) VÀ chính câu đó dài hơn chỗ của nó – câu
        ngắn chỉ bị kéo nhanh theo tốc độ chung của cụm câu dài (bộ xếp 'Tự động') không tính."""
        x = plan.get(k) or {}
        slot = float(x.get("slot") or 0)
        if slot > 0.05 and float(by_k[k]["dur"]) / slot <= thr + EPS:
            return False
        return float(x.get("speed") or 1.0) > thr + EPS or x.get("fit") == "long"

    def broken(k1, k2):
        """2 câu lồng liền nhau không thuộc cùng 1 đoạn nói dồn: lặng dài / có câu không lồng (🎵, bỏ lồng) ở giữa."""
        a, b = segs[k1], segs[k2]
        if float(b.start) - float(a.end) > BREAK_GAP:
            return True
        return any(float(a.start) < t < float(b.start) for t in others)

    runs: list[dict] = []
    cur: list[int] = []

    def close():
        while cur and not fast(cur[-1]):
            cur.pop()
        f = [k for k in cur if fast(k)]
        if len(f) >= o["dense_min"]:
            runs.append({"rows": list(cur), "fast": f, "a": round(float(segs[cur[0]].start), 3),
                         "b": round(float(segs[cur[-1]].end), 3)})
        cur.clear()

    allow = 1 if o["dense_mix"] else 0
    slow_run = 0
    for k in order:
        if cur and broken(cur[-1], k):
            close()
            slow_run = 0
        if fast(k):
            cur.append(k)
            slow_run = 0
        elif cur and slow_run < allow:
            cur.append(k)
            slow_run += 1
        else:
            close()
            slow_run = 0
    close()
    return runs


def _split_at(pieces, a: float, b: float):
    """Chia các khoảng nghỉ thành phần nằm trong [a, b] và phần ngoài."""
    inside, outside = [], []
    for x, y in pieces:
        for lo, hi, dst in ((x, min(y, a), outside), (max(x, a), min(y, b), inside), (max(x, b), y, outside)):
            if hi - lo > 1e-3:
                dst.append((lo, hi))
    return inside, outside


def plan_run(run: dict, segs, items: dict, plan: dict, speech, total: float, nxt: float, o: dict) -> dict:
    """Các đoạn giãn [a, b, tốc độ] của 1 đoạn nói nhanh + số giây video dài thêm."""
    how, thr = o["dense_how"], o["dense_thr"]
    rows = run["rows"]
    wants = [float(items[k]["want"]) for k in rows]
    a = wants[0]
    b = min(max(float(segs[rows[-1]].end), wants[-1]), nxt)
    # --- cả đoạn chạy chậm r_b
    r_b = 1.0
    if how in ("block", "both"):
        fixed = _block_rate(o)
        if fixed is None:      # Vừa đủ: câu khó nhất của đoạn cần đọc ≤ ngưỡng (theo độ dài giọng thật / chỗ của câu –
            # tốc độ bộ xếp đang đọc thấp hơn nhu cầu thật vì đã đọc sớm / lùi câu sau)
            req = []
            for j, k in enumerate(rows):
                if k in run["fast"]:
                    hi = wants[j + 1] if j + 1 < len(rows) else nxt
                    req.append(float(items[k]["dur"]) / max(0.05, hi - wants[j] - GAP))
            r_b = min([thr / v for v in req if v > 0] + [1.0])
        else:
            r_b = fixed
        r_b = min(1.0, max(o["dense_block_floor"], r_b))
        if b - a > 1e-3 and r_b < 1:
            r_b = max(r_b, (b - a) / (b - a + o["dense_cap"]))    # tối đa dense_cap giây mỗi đoạn
        if r_b > 0.995:
            r_b = 1.0
    block_add = (b - a) * (1 / r_b - 1) if r_b < 1 else 0.0
    # --- nghỉ giữa các câu
    gaps = []        # (phần trong [a, b], phần ngoài, giây thêm)
    if how in ("gaps", "both"):
        add = _gap_add(o)
        for j, k in enumerate(rows):
            hi = wants[j + 1] if j + 1 < len(rows) else nxt
            e = float(segs[k].end)
            e = min(e, max((y for x, y in speech if x < e), default=e))    # thôi nói theo giọng thật
            pieces = timewarp.free_between(speech, max(e, wants[j]), hi)
            if not pieces:
                continue
            ins, outs = _split_at(pieces, a, b) if r_b < 1 else ([], pieces)
            gin, gout = sum(y - x for x, y in ins), sum(y - x for x, y in outs)
            vlen = gin / r_b + gout          # khoảng nghỉ này dài bao nhiêu sau khi cả đoạn đã chậm
            if add is None:                  # Vừa đủ: đủ chỗ để câu đọc ≤ ngưỡng
                dur = float(items[k]["dur"])
                slot = (hi - wants[j]) / r_b - GAP
                ext = min(AUTO_GAP_MAX, max(0.0, dur / thr - slot)) if k in run["fast"] else 0.0
            else:
                ext = add
            kmin = o["dense_gap_floor"] / r_b if gin > 0 else o["dense_gap_floor"]
            if kmin < 1:
                ext = min(ext, vlen * (1 / kmin - 1))
            else:
                ext = 0.0
            if ext > 0.02:
                gaps.append((ins, outs, vlen, ext))
    total_ext = sum(g[3] for g in gaps)
    room = max(0.0, o["dense_cap"] - block_add)
    scale = min(1.0, room / total_ext) if total_ext > 0 else 1.0
    spans: list[list[float]] = []
    gap_pieces: list[tuple[float, float, float]] = []
    for ins, outs, vlen, ext in gaps:
        e2 = ext * scale
        if e2 <= 0.02:
            continue
        kf = vlen / (vlen + e2)
        gap_pieces += [(x, y, r_b * kf) for x, y in ins] + [(x, y, kf) for x, y in outs]
    if r_b < 1:     # phần còn lại của đoạn chạy r_b
        prev = a
        for x, y, _r in sorted(p for p in gap_pieces if a <= p[0] < b):
            if x > prev:
                spans.append([prev, x, r_b])
            prev = max(prev, y)
        if prev < b:
            spans.append([prev, b, r_b])
    spans += [[x, y, r] for x, y, r in gap_pieces]
    w = timewarp.Warp(spans)
    return {"spans": w.to_list(), "added": round(w.added(), 3), "rate": round(r_b, 3)}


def find(segs, items: list[dict], plan: dict, speech, total: float, o: dict, layout) -> dict:
    """Tìm + lập giãn cho mọi đoạn nói nhanh. layout(items, total) -> plan (bộ xếp giọng chạy nháp, tốc độ tự tính).
    Trả {"runs": [{a, b, rows, fast, n, spans, added, rate, before (tb, max), after (tb, max), left}], "lines", "added"}."""
    by_k = {x["k"]: x for x in items}
    runs = find_runs(segs, items, plan, o)
    order = [x["k"] for x in sorted(items, key=lambda x: (float(x["want"]), int(x["k"])))]
    pos = {k: i for i, k in enumerate(order)}
    for r in runs:
        last = pos[r["rows"][-1]]
        nxt = float(by_k[order[last + 1]]["want"]) if last + 1 < len(order) else max(total, float(segs[r["rows"][-1]].end))
        r.update(plan_run(r, segs, by_k, plan, speech, total, nxt, o))
        r["n"] = len(r["rows"])
    estimate(runs, items, plan, total, o, layout)
    return {"runs": runs, "lines": sum(r["n"] for r in runs), "added": round(sum(r["added"] for r in runs), 3),
            "opts": o}


def estimate(runs: list[dict], items: list[dict], plan: dict, total: float, o: dict, layout, chosen=None) -> None:
    """Tốc độ đọc trước / sau khi giãn (bộ xếp giọng chạy nháp trên giờ giãn) cho từng đoạn – ghi vào runs."""
    pick = [r for i, r in enumerate(runs) if chosen is None or i in chosen]
    w = timewarp.Warp([sp for r in pick for sp in r["spans"]])
    after = plan
    if w:
        its = [dict(x, want=w.fwd(float(x["want"]))) for x in items]
        after = layout(its, w.fwd(total))
    thr = o["dense_thr"]
    for r in runs:
        sb = [float((plan.get(k) or {}).get("speed") or 1.0) for k in r["rows"]]
        sa = [float((after.get(k) or {}).get("speed") or 1.0) for k in r["rows"]]
        r["before"] = (round(sum(sb) / len(sb), 3), round(max(sb), 3)) if sb else (1.0, 1.0)
        r["after"] = (round(sum(sa) / len(sa), 3), round(max(sa), 3)) if sa else (1.0, 1.0)
        r["left"] = [k for k, v in zip(r["rows"], sa) if v > thr + EPS or (after.get(k) or {}).get("fit") == "long"]


def applied(found: dict, chosen: list[int]) -> dict:
    """edit["dense"] khi bấm Áp dụng: các đoạn giãn cố định + tóm tắt từng đoạn (không lưu số thứ tự câu – câu có
    thể thêm / xoá sau đó)."""
    runs = [r for i, r in enumerate(found.get("runs") or []) if i in set(chosen)]
    w = timewarp.Warp([sp for r in runs for sp in r["spans"]])
    return {"spans": w.to_list(), "t": time.strftime("%Y-%m-%d %H:%M"), "opts": dict(found.get("opts") or {}),
            "runs": [{"a": r["a"], "b": r["b"], "n": r["n"], "fast": len(r["fast"]), "added": r["added"],
                      "rate": r.get("rate", 1.0), "before": list(r["before"]), "after": list(r["after"]),
                      "left": len(r["left"])} for r in runs]}


def fmt_s(x: float) -> str:
    return f"{x:.1f} s".replace(".", ",") if x < 120 else f"{x / 60:.1f} phút".replace(".", ",")


def note(found: dict) -> str:
    runs = found.get("runs") or []
    if not runs:
        o = found.get("opts") or {}
        return (f"Không có đoạn nào từ {o.get('dense_min', 5)} câu liền nhau đọc nhanh hơn "
                f"×{o.get('dense_thr', 1.1):.2f}".replace(".", ","))
    left = sum(len(r["left"]) for r in runs)
    msg = (f"Tìm thấy {len(runs)} đoạn ({found['lines']} câu) · video dài thêm {fmt_s(found['added'])}")
    if left:
        msg += f" · {left} câu vẫn đọc nhanh hơn ×{found['opts']['dense_thr']:.2f}".replace(".", ",")
    return msg


def applied_note(d: dict | None) -> str:
    """Dòng trạng thái của các đoạn đã áp."""
    runs = (d or {}).get("runs") or []
    if not runs or not (d or {}).get("spans"):
        return ""
    w = timewarp.Warp(d["spans"])
    n = sum(int(r.get("n") or 0) for r in runs)
    return f"Đang giãn {len(runs)} đoạn ({n} câu) · video dài thêm {fmt_s(w.added())} · áp lúc {d.get('t', '')}"
