"""Nghe lại câu KHÔNG CHẮC với khúc rộng hơn (2.1.15) – và "Nghe kỹ": mọi câu được nghe lần 2.

Vì sao: bộ nghe chia phim thành khúc ≤ 28 s; câu nằm sát mép khúc thiếu ngữ cảnh 1 bên, câu lẫn nhạc / tiếng động thì
bộ nghe đoán. Bộ nghe tự chấm độ chắc từng chữ (xác suất chữ nó chọn) -> câu có độ chắc trung bình dưới mức đặt (Cài đặt
`asr_recheck_conf`, mặc định 0,7) được nghe lại trong 1 khúc MỚI: câu nằm giữa, mỗi bên thêm `asr_recheck_pad` giây (câu
trước / sau làm ngữ cảnh). Có bản tách giọng thì nghe thêm trên TIẾNG GỐC (bản tách giọng đôi khi méo / mất phụ âm).
"Nghe kỹ" (`asr_overlap`): mọi câu đều được nghe lần 2 như vậy (chậm gấp đôi).

Bản nghe lại chỉ được nhận khi (tránh "thêm chữ không liên quan"):
  - các câu ngữ cảnh trong khúc nghe lại ra giống bản cũ ≥ 60 % (khúc nghe lại ổn, ghép đúng câu);
  - chắc hơn bản cũ ≥ 0,05 và ≥ 0,6;
  - giống bản cũ ≥ 50 % và không dài thêm quá 2 chữ – trừ khi chắc hơn hẳn (≥ 0,15).
Không nhận mà nghe ra khác -> câu tô xanh, chú thích có bản nghe lại; tích câu rồi bấm "Dùng bản bộ nghe 2" để dùng.

Nút "Bộ nghe nghe lại câu đã tích" (2.1.16, `manual`): câu người dùng tích được nghe 3 lần – khúc rộng trên giọng đã
tách, khúc rộng trên tiếng gốc, riêng câu (±`TIGHT_PAD`) – rồi `decide_manual` chọn bản nhiều lần nghe ra giống nhau /
chắc nhất và ĐIỀN LUÔN khi đủ chắc (luật nới hơn lúc tự nghe vì người dùng đã chỉ câu sai).

Hàm thuần (`windows`, `map_rows`, `decide`, `decide_manual`) + `recheck_lines` gọi tiến trình nghe (asr_qwen.asr_worker).
"""
from __future__ import annotations

from difflib import SequenceMatcher
from pathlib import Path
from typing import Callable

from .dual import CORE, core

Prog = Callable[[float, str], None]

RECHECK_CONF = 0.7    # câu có độ chắc dưới mức này -> nghe lại
RECHECK_PAD = 5.0     # giây nghe kèm mỗi bên
GAIN = 0.05           # bản nghe lại phải chắc hơn ít nhất bấy nhiêu
MIN_ACCEPT = 0.6      # ... và tự nó chắc ≥ 0,6
BIG_GAIN = 0.15       # chắc hơn hẳn: cho phép khác nhiều / dài thêm
SIM_MIN = 0.5         # giống bản cũ ≥ 50 %
GROW_MAX = 2          # dài thêm tối đa 2 chữ
CTX_SAME = 0.6        # câu ngữ cảnh trong khúc nghe lại giống bản cũ ≥ 60 %
HALLU_CONF = 0.5      # bản cũ < 0,5 mà nghe lại không ra chữ nào -> nghi nhạc / tiếng động bị chép thành lời
GROUP = 40            # số khúc mỗi lần gửi bộ nghe
MANUAL_MIN = 0.5      # nghe lại câu đã tích: bản mới tự nó chắc ≥ 0,5 (và không kém bản cũ)
MANUAL_SIM = 0.3      # ... giống bản cũ ≥ 30 % – trừ khi chắc hơn hẳn / 2 lần nghe ra cùng chữ
TIGHT_PAD = 0.4       # lần nghe riêng câu: thêm 0,4 s mỗi bên


# ------------------------------------------------------------------ độ chắc từng câu

def assign_conf(segs, pts: list[tuple[float, float]]) -> int:
    """extra["asr_conf"] = xác suất trung bình các chữ (theo giờ chữ) nằm trong câu. Câu không có điểm nào (câu nghe
    lại đoạn sót đã có sẵn độ chắc…) giữ nguyên. Trả số câu đã gán."""
    import bisect

    pts = sorted(pts)
    ts = [t for t, _ in pts]
    n = 0
    for s in segs:
        i, j = bisect.bisect_left(ts, s.start), bisect.bisect_right(ts, s.end)
        if j > i:
            s.extra["asr_conf"] = round(sum(p for _, p in pts[i:j]) / (j - i), 3)
            n += 1
    return n


def conf_of(seg) -> float | None:
    c = seg.extra.get("asr_conf")
    return None if c is None else float(c)


# ------------------------------------------------------------------ khúc nghe lại

def windows(segs, rows: list[int], pad: float, max_len: float, total: float = 0.0
            ) -> list[tuple[float, float, list[int], list[int]]]:
    """Gom các câu cần nghe lại liền nhau thành khúc: [(đầu, cuối, câu cần nghe lại, mọi câu trong khúc)]. Mỗi câu
    cần nghe lại cách mép khúc ≥ `pad` giây (nằm giữa khúc); mép khúc dời vào khoảng lặng giữa 2 câu (không cắt đôi
    câu nào – câu ở mép được lấy trọn hoặc bỏ hẳn)."""
    rows = sorted(r for r in set(rows) if 0 <= r < len(segs))
    room = max(1.0, max_len - 2 * pad)
    groups: list[list[int]] = []
    for r in rows:   # câu cần nghe lại gần nhau (cách ≤ 2 × pad) chung 1 khúc; xa nhau thì khúc riêng (đỡ nghe thừa)
        if groups and segs[r].end - segs[groups[-1][0]].start <= room and \
                segs[r].start - segs[groups[-1][-1]].end <= 2 * pad:
            groups[-1].append(r)
        else:
            groups.append([r])
    out = []
    for g in groups:
        a = max(0.0, segs[g[0]].start - pad)
        b = segs[g[-1]].end + pad
        if total:
            b = min(b, total)
        for s in segs:   # mép rơi giữa 1 câu: lấy trọn câu đó nếu khúc không quá dài, không thì bỏ câu đó
            if s.start < a < s.end:
                a = max(0.0, s.start - 0.05) if b - s.start + 0.05 <= max_len else s.end + 0.02
            if s.start < b < s.end:
                b = s.end + 0.05 if s.end + 0.05 - a <= max_len else s.start - 0.02
        if total:
            b = min(b, total)
        inside = [i for i, s in enumerate(segs) if a <= (s.start + s.end) / 2 <= b and core(s.text)]
        if b - a >= 1.0 and any(r in inside for r in g):
            out.append((round(a, 3), round(b, 3), [r for r in g if r in inside], inside))
    return out


def map_rows(texts: list[str], heard: str, probs: list[float] | None, conf: float | None
             ) -> list[tuple[str, list[float]]]:
    """Chia lời nghe lại của cả khúc cho từng câu trong khúc (khớp từng chữ với lời cũ, như "Nghe 2 bộ rồi so"): chữ
    thay thế chia theo tỉ lệ, chữ chèn giữa 2 câu theo câu trước. Trả [(chữ mới của câu, xác suất từng chữ)]."""
    alt, ap = "", []
    for k, ch in enumerate(heard or ""):
        if CORE.match(ch):
            alt += ch.lower()
            ap.append(float(probs[k]) if probs and k < len(probs) else float(conf or 0.0))
    p, owner = "", []
    for r, t in enumerate(texts):
        c = core(t)
        p += c
        owner += [r] * len(c)
    got: list[list[int]] = [[] for _ in texts]
    if not p:
        return [("", []) for _ in texts]
    for op, i1, i2, j1, j2 in SequenceMatcher(None, p, alt, autojunk=False).get_opcodes():
        if op == "equal":
            for k in range(i2 - i1):
                got[owner[i1 + k]].append(j1 + k)
            continue
        span = owner[i1:i2]
        for k in range(j1, j2):
            r = span[min(len(span) - 1, (k - j1) * len(span) // max(1, j2 - j1))] if span else \
                (owner[i1 - 1] if i1 > 0 else owner[0])
            got[r].append(k)
    return [("".join(alt[k] for k in g), [ap[k] for k in g]) for g in got]


def ctx_ok(old_texts: list[str], new_cores: dict[int, str], ctx: list[int]) -> bool:
    """Các câu ngữ cảnh (không phải câu đang nghe lại) nghe lại ra giống bản cũ ≥ CTX_SAME (tính theo số chữ khớp)."""
    tot = same = 0
    for i in ctx:
        a, b = core(old_texts[i]), new_cores.get(i, "")
        tot += len(a)
        same += sum(n for _x, _y, n in SequenceMatcher(None, a, b, autojunk=False).get_matching_blocks())
    return tot == 0 or same >= CTX_SAME * tot


def decide(old: str, old_conf: float | None, cands: list[tuple[str, str, float, bool]]
           ) -> tuple[tuple[str, str, float] | None, tuple[str, str, float] | None]:
    """cands: [(nguồn, chữ mới, độ chắc chữ mới, khúc ổn?)]. Trả (bản được nhận | None, bản khác chưa nhận để tô xanh
    | None). Bản nhận phải chắc hơn, giống bản cũ và không dài thêm nhiều (trừ khi chắc hơn hẳn). Có lần nghe lại ra
    đúng như cũ -> bản cũ được xác nhận, không tô xanh."""
    oc = core(old)
    base = float(old_conf) if old_conf is not None else 0.5
    best = None
    others: list[tuple[str, str, float]] = []
    empty = None
    confirmed = False
    for src, new, conf, ok in cands:
        if not ok:
            continue
        if not new:
            empty = empty or (src, "", conf)
            continue
        if new == oc:
            confirmed = True
            continue
        sim = SequenceMatcher(None, oc, new, autojunk=False).ratio()
        big = conf >= base + BIG_GAIN
        if conf >= base + GAIN and conf >= MIN_ACCEPT and (sim >= SIM_MIN or big) and \
                (len(new) - len(oc) <= GROW_MAX or big):
            if best is None or conf > best[2]:
                best = (src, new, conf)
        else:
            others.append((src, new, conf))
    if best or confirmed:
        return best, None
    if others:
        return None, max(others, key=lambda x: x[2])
    if empty and base < HALLU_CONF:
        return None, empty
    return None, None


def decide_manual(old: str, old_conf: float | None, cands: list[tuple[str, str, float, bool]]
                  ) -> tuple[str | None, tuple[str, str, float] | None]:
    """Nghe lại câu người dùng đã tích. cands như `decide`. Trả (kết quả, (nguồn, chữ, độ chắc)):
      "fix"   – điền bản mới: nhiều lần nghe ra cùng chữ nhất (bằng nhau thì chắc nhất), tự nó chắc ≥ MANUAL_MIN, không
                kém bản cũ; 2 lần nghe ra cùng chữ thì nới GAIN; các lần nghe mỗi lần 1 kiểu thì phải chắc hơn GAIN và
                giống bản cũ ≥ MANUAL_SIM; khác hẳn bản cũ thì phải chắc hơn hẳn (BIG_GAIN);
      "same"  – nghe lại vẫn ra như cũ (bản cũ được xác nhận);
      "alt"   – nghe ra khác mà chưa đủ chắc -> tô xanh, "Dùng bản bộ nghe 2" nếu thấy đúng;
      "empty" – mọi lần nghe không ra chữ nào (nhạc / tiếng động bị chép thành lời?);
      None    – không lần nghe nào dùng được."""
    oc = core(old)
    good = [(src, new, conf) for src, new, conf, ok in cands if ok]
    if not good:
        return None, None
    heard = [c for c in good if c[1]]
    if not heard:
        return "empty", good[0]
    votes: dict[str, list[tuple[str, float]]] = {}
    for src, new, conf in heard:
        votes.setdefault(new, []).append((src, conf))

    def score(t: str) -> tuple[int, float]:
        return len(votes[t]), max(c for _s, c in votes[t])
    top = max(votes, key=score)
    n, conf = score(top)
    src = max(votes[top], key=lambda x: x[1])[0]
    if top == oc:
        return "same", (src, top, conf)
    if oc in votes and score(oc)[0] >= n:      # bản cũ cũng được nghe lại ra ngần ấy lần -> chưa đủ để đổi
        return ("same", (src, oc, score(oc)[1])) if score(oc)[1] >= conf else ("alt", (src, top, conf))
    base = float(old_conf) if old_conf is not None else None
    need = MIN_ACCEPT if base is None else max(MANUAL_MIN, base)
    sim = SequenceMatcher(None, oc, top, autojunk=False).ratio()
    if n >= 2:                   # ≥ 2 lần nghe (khúc khác nhau / âm thanh khác nhau) ra cùng chữ
        ok = conf >= need - GAIN
    elif len(votes) > 1:         # các lần nghe ra mỗi lần 1 kiểu -> chỉ điền khi chắc hơn rõ và gần bản cũ
        ok = conf >= need + GAIN and sim >= MANUAL_SIM
    else:                        # chỉ 1 lần nghe ra chữ (lần khác không ra chữ / khúc không ổn)
        ok = conf >= need and (sim >= MANUAL_SIM or conf >= (base if base is not None else MIN_ACCEPT) + BIG_GAIN)
    return ("fix" if ok else "alt"), (src, top, conf)


# ------------------------------------------------------------------ chạy

def recheck_lines(segs, wav: str | Path, orig_wav: str | Path | None, engine: str, language: str, settings: dict,
                  total: float = 0.0, progress: Prog | None = None, cancel=None, rows: list[int] | None = None) -> dict:
    """Nghe lại câu không chắc (và mọi câu khi "Nghe kỹ") – sửa `segs` tại chỗ. Trả thống kê.
    rows: nghe lại ĐÚNG các câu này (nút "Bộ nghe nghe lại câu đã tích") – thêm lần nghe riêng câu, `decide_manual`;
    thống kê có "rows_fixed" / "rows_same" / "rows_alt" / "rows_empty" (số thứ tự câu)."""
    from . import asr_qwen, dual
    from .asr import to_simplified

    thr = float(settings.get("asr_recheck_conf", RECHECK_CONF) or RECHECK_CONF)
    pad = float(settings.get("asr_recheck_pad", RECHECK_PAD) or RECHECK_PAD)
    every = bool(settings.get("asr_overlap", False))
    manual = rows is not None
    st = {"overlap": every, "pad": pad, "thr": thr, "checked": 0, "weak": 0, "fixed": 0, "fixed_orig": 0,
          "unsure": 0, "windows": 0}
    if manual:
        st.update(manual=True, overlap=False, same=0, empty=0, rows_fixed=[], rows_same=[], rows_alt=[], rows_empty=[])
        targets = sorted(i for i in set(rows) if 0 <= i < len(segs) and core(segs[i].text))
        weak = list(targets)
    else:
        usable = [i for i, s in enumerate(segs) if core(s.text) and not s.extra.get("song")]
        weak = [i for i in usable if conf_of(segs[i]) is not None and conf_of(segs[i]) < thr]
        st["weak"] = len(weak)
        targets = usable if every else (weak if settings.get("asr_recheck", True) else [])
    if not targets:
        return st
    max_len = asr_qwen.chunk_len(settings, engine)
    wins = windows(segs, targets, pad, max_len, total)
    st["windows"] = len(wins)
    st["checked"] = len(targets) if manual else sum(len(w[2]) for w in wins)
    if not wins and not manual:
        return st
    simplify = language in ("zh", "yue", "auto")
    texts = [s.text for s in segs]
    w = asr_qwen.asr_worker(engine, settings)
    req = {"cmd": "asr", "language": language, "prompt": asr_qwen.hotwords(settings), "align": False, "conf": True}

    def hear(path, spans, lo, hi, what):
        if progress:
            progress(lo, f"Nghe lại {sum(len(x[2]) for x in spans)} câu {what}…")
        items: list = []
        for g in range(0, len(spans), GROUP):   # từng nhóm khúc: RAM thấp (Nghe kỹ trên phim dài), tiến độ đều
            part = spans[g:g + GROUP]
            a0, a1 = lo + (hi - lo) * g / len(spans), lo + (hi - lo) * (g + len(part)) / len(spans)
            pr = (lambda x, m, a0=a0, a1=a1: progress(a0 + (a1 - a0) * x, m)) if progress else None
            r = w.call(dict(req, wav=str(Path(path).resolve()), spans=[[a, b] for a, b, _t, _i in part]), pr, cancel)
            got = list(r.get("items") or [])
            items += got + [{}] * (len(part) - len(got))
        out = []
        for it in items:
            t = (it.get("text") or "").strip()
            cp = asr_qwen._strip_cp(it)
            if simplify:
                t2 = to_simplified(t)
                if len(t2) == len(t):
                    t = t2
            out.append((t, cp, it.get("conf")))
        return out

    # mỗi câu cần nghe lại: các bản nghe được [(nguồn, chữ, độ chắc, khúc ổn?)]
    cands: dict[int, list[tuple[str, str, float, bool]]] = {}

    def collect(spans, heard, src):
        for (a, b, tg, inside), (t, cp, conf) in zip(spans, heard):
            parts = map_rows([texts[i] for i in inside], t, cp, conf)
            new = {i: parts[k] for k, i in enumerate(inside)}
            ok = ctx_ok(texts, {i: new[i][0] for i in inside}, [i for i in inside if i not in tg])
            for i in tg:
                nc, probs = new.get(i, ("", []))
                c = sum(probs) / len(probs) if probs else float(conf or 0.0)
                cands.setdefault(i, []).append((src, nc, round(c, 3), ok))

    with_orig = orig_wav is not None and (manual or settings.get("asr_recheck_orig", True))
    if manual:   # câu người dùng chỉ: nghe khúc rộng (giọng đã tách + tiếng gốc) + nghe riêng từng câu
        cuts = (0.0, 0.35, 0.65) if with_orig else (0.0, 0.5, 0.5)
        if wins:
            collect(wins, hear(wav, wins, cuts[0], cuts[1], "với khúc rộng hơn"), "v")
            if with_orig:
                collect(wins, hear(orig_wav, wins, cuts[1], cuts[2], "trên tiếng gốc (chưa tách giọng)"), "o")
        tight = [(round(max(0.0, segs[i].start - TIGHT_PAD), 3), round(segs[i].end + TIGHT_PAD, 3), [i], [i])
                 for i in targets]
        collect(tight, hear(wav, tight, cuts[2], 0.92, "riêng từng câu"), "s")
    else:
        collect(wins, hear(wav, wins, 0.0, 0.6 if with_orig else 0.95, "với khúc rộng hơn"), "v")
    if with_orig and not manual:
        weak_set = set(weak)
        ow = [(a, b, [i for i in tg if i in weak_set], inside) for a, b, tg, inside in wins]
        ow = [x for x in ow if x[2]]
        if ow:
            collect(ow, hear(orig_wav, ow, 0.6, 0.95, "trên tiếng gốc (chưa tách giọng)"), "o")
    realign = []
    eng_of = {"o": "recheck_o", "s": "recheck_s"}
    for i, cs in sorted(cands.items()):
        s = segs[i]
        if str(s.extra.get("asr_alt_eng", "")).startswith("recheck"):
            for k in ("asr_alt", "asr_alt_eng", "asr_unsure", "asr_pick"):
                s.extra.pop(k, None)
        if manual:
            kind, got = decide_manual(s.text, conf_of(s), cs)
            best = got if kind == "fix" else None
            other = got if kind in ("alt", "empty") else None
            if kind == "same":
                s.extra["asr_conf"] = round(max(conf_of(s) or 0.0, got[2]), 3)
                s.extra["asr_same"] = True
                st["same"] += 1
                st["rows_same"].append(i)
            elif kind == "empty":
                st["empty"] += 1
                st["rows_empty"].append(i)
            elif kind == "alt":
                st["rows_alt"].append(i)
            elif kind == "fix":
                st["rows_fixed"].append(i)
        else:
            best, other = decide(s.text, conf_of(s), cs)
            oc = core(s.text)
            same = [c for _src, new, c, ok in cs if ok and new == oc]
            if not best and not other and same:   # nghe lại ra y hệt -> câu đã được xác nhận (2.1.19)
                s.extra["asr_conf"] = round(max(conf_of(s) or 0.0, max(same)), 3)
                s.extra["asr_same"] = True
                st["confirmed"] = st.get("confirmed", 0) + 1
        if best:
            s.extra.pop("asr_same", None)
            src, new, conf = best
            old = s.text
            s.text = dual.merge_alt(old, new)
            s.extra.setdefault("src_before", old)
            s.extra["asr_recheck"] = src
            s.extra["asr_conf"] = conf
            s.extra.pop("ct", None)      # chữ đổi -> đo lại mốc từng chữ
            realign.append(s)
            st["fixed_orig" if src == "o" else "fixed"] += 1
        elif other:
            src, new, _conf = other
            s.extra.pop("asr_same", None)
            s.extra["asr_alt"] = new
            s.extra["asr_alt_eng"] = eng_of.get(src, "recheck")
            s.extra["asr_unsure"] = True
            st["unsure"] += 1
    if realign:   # câu đổi chữ -> đo lại mốc từng chữ (bước bám mép / tách câu sau đó dùng)
        try:
            asr_qwen.align_qwen(realign, wav, language, settings, cancel=cancel, engine=engine)
        except Exception as e:  # noqa: BLE001 – không đo được thì dùng mốc câu cũ
            from .ffmpeg import Cancelled

            if isinstance(e, Cancelled):
                raise
    return st


def note(st: dict | None) -> str:
    """Câu báo kết quả cho thông báo "1. Nghe & tách câu"."""
    if not st:
        return ""
    if st.get("error"):
        return f"⚠ bỏ qua bước nghe lại câu không chắc ({st['error']})"
    if st.get("manual"):
        if not st.get("checked"):
            return "Các câu đã tích không có chữ để nghe lại"
        fixed = int(st.get("fixed", 0)) + int(st.get("fixed_orig", 0))
        parts = [f"điền lại {fixed} câu" if fixed else "không câu nào phải đổi chữ"]
        if st.get("same"):
            parts.append(f"{st['same']} câu nghe lại vẫn ra như cũ")
        alt = int(st.get("unsure", 0)) - int(st.get("empty", 0))
        if alt > 0:
            parts.append(f"{alt} câu nghe ra khác mà chưa chắc – chữ gốc màu xanh, rê chuột xem bản nghe lại, đúng thì "
                         "bấm 'Dùng bản bộ nghe 2'")
        if st.get("empty"):
            parts.append(f"{st['empty']} câu bộ nghe không nghe ra lời nào (nhạc / tiếng động bị chép thành chữ?) – "
                         "xem rồi Xoá")
        return f"Bộ nghe nghe lại {st['checked']} câu đã tích: " + ", ".join(parts)
    if not st.get("checked"):
        return ""
    what = "nghe kỹ lần 2" if st.get("overlap") else "nghe lại câu không chắc"
    out = f"{what} {st['checked']} câu (±{float(st.get('pad', RECHECK_PAD)):g} s)"
    fixed = int(st.get("fixed", 0)) + int(st.get("fixed_orig", 0))
    out += f": sửa {fixed} câu" if fixed else ": không câu nào cần sửa"
    if st.get("fixed_orig"):
        out += f" ({st['fixed_orig']} câu theo tiếng gốc)"
    if st.get("unsure"):
        out += f", {st['unsure']} câu nghe lại ra khác mà chưa chắc – chữ gốc màu xanh (nghe lại để chọn)"
    return out
