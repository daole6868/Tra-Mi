"""Tách phụ đề theo đoạn (2.1.35) – hàm thuần: đoạn người dùng đánh dấu / khoảng chưa có phụ đề -> danh sách đoạn,
lọc câu tách được nằm trong đoạn, gộp vào phụ đề đang có đúng giờ (giữ câu cũ: chỉ thêm câu còn thiếu | thay câu cũ trong
đoạn). Việc nghe / OCR chạy ở `pipeline.recognize_spans` (y hệt "1. Nghe & tách câu", chỉ khác là bộ nghe / OCR chỉ thấy
các đoạn này)."""
from __future__ import annotations

PAD = 1.5          # giây nghe / quét thêm mỗi bên đoạn (câu nằm vắt qua mép đoạn vẫn nghe / đọc trọn)
MIN_SPAN = 0.4     # đoạn ngắn hơn bỏ qua
MIN_LINE = 0.2     # câu mới bị xén còn ngắn hơn thì bỏ
SAME = 0.5         # câu mới trùng ≥ 50 % thời lượng với câu cũ = cùng 1 lời (giữ câu cũ)
SIMILAR = 0.5      # chữ giống ≥ 50 % (hoặc là 1 mẩu của nhau) = cùng 1 lời (2.4.5)
MODES = [("keep", "Giữ câu cũ – chỉ thêm câu còn thiếu"), ("replace", "Thay câu cũ trong đoạn bằng câu mới")]


def clean(spans, total: float = 0.0) -> list[tuple[float, float]]:
    """[(đầu, cuối)] -> kẹp trong [0, total], bỏ đoạn quá ngắn, xếp theo giờ, gộp đoạn chồng / chạm nhau."""
    out: list[list[float]] = []
    for a, b in sorted((float(min(x, y)), float(max(x, y))) for x, y in (spans or [])):
        a = max(0.0, a)
        if total > 0:
            b = min(float(total), b)
        if b - a < MIN_SPAN:
            continue
        if out and a <= out[-1][1] + 1e-6:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return [(round(a, 3), round(b, 3)) for a, b in out]


def padded(spans, total: float = 0.0, pad: float = PAD) -> list[tuple[float, float]]:
    """Đoạn nới thêm `pad` mỗi bên (để nghe / quét) rồi gộp lại."""
    return clean([(a - pad, b + pad) for a, b in spans], total)


def gaps(segs, total: float, lo: float = 10.0, hi: float = 60.0, skip_songs: bool = False) -> list[tuple[float, float]]:
    """Các khoảng KHÔNG có câu phụ đề nào (đầu video tới câu đầu, giữa 2 câu, câu cuối tới hết video) dài từ `lo` tới
    `hi` giây (hi ≤ 0: không giới hạn). skip_songs: câu 🎵 coi như không có (khoảng có bài hát cũng được tìm)."""
    lines = sorted((float(s.start), float(s.end)) for s in segs
                   if not (skip_songs and getattr(s, "extra", {}).get("song")))
    out, t = [], 0.0
    for a, b in lines + [(float(total), float(total))]:
        if a - t >= max(lo, MIN_SPAN) and (hi <= 0 or a - t <= hi):
            out.append((round(t, 3), round(a, 3)))
        t = max(t, b)
    return out


def inside(seg, spans) -> bool:
    """Câu thuộc đoạn: điểm giữa câu nằm trong 1 đoạn."""
    m = (float(seg.start) + float(seg.end)) / 2
    return any(a <= m <= b for a, b in spans)


def _overlap(a0: float, a1: float, b0: float, b1: float) -> float:
    return max(0.0, min(a1, b1) - max(a0, b0))


def same_words(a: str, b: str) -> bool:
    """2 câu cùng 1 lời (2.4.5): chữ (bỏ dấu câu) giống ≥ `SIMILAR` hoặc câu này là 1 mẩu của câu kia."""
    from difflib import SequenceMatcher

    from .dual import core

    x, y = core(a), core(b)
    if not x or not y:
        return False
    return x in y or y in x or SequenceMatcher(None, x, y, autojunk=False).ratio() >= SIMILAR


def _trim(s, o) -> None:
    """Xén câu mới cho khỏi đè câu cũ (câu cũ đã có giờ chuẩn, không đụng): câu cũ ở đầu -> lùi đầu câu mới, ở cuối ->
    rút cuối; câu cũ nằm giữa -> giữ phần dài hơn."""
    if o.start <= s.start or (o.end < s.end and s.end - o.end > o.start - s.start):
        s.start = round(o.end + 0.02, 3)
    else:
        s.end = round(o.start - 0.02, 3)


def merge(existing: list, new: list, spans, mode: str = "keep") -> dict:
    """Gộp câu mới vào phụ đề đang có. Chỉ lấy câu mới nằm trong đoạn (`inside`).
      keep: câu mới nằm ≥ `SAME` trong 1 câu cũ (đã có) -> bỏ; câu cũ NGẮN nằm ≥ `SAME` trong câu mới:
        cùng lời (`same_words`) = câu cũ bị cắt ngắn / thiếu chữ (2.4.5 – trước đây coi là "đã có" nên câu đủ quét lại
        được bị bỏ): y hệt chữ -> sửa giờ câu cũ theo câu mới (giữ bản dịch, giọng), khác chữ -> thay câu cũ bằng câu mới;
        khác lời -> xén câu mới như chạm nhẹ (còn ≥ MIN_LINE);
      replace: bỏ câu cũ nằm trong đoạn, thêm mọi câu mới (câu cũ ngoài đoạn vẫn xét như keep).
    Câu cũ giữ nguyên đối tượng (bản dịch, giọng lồng, mốc chữ). Trả {"segs": danh sách mới theo giờ, "added": [câu mới
    – đối tượng], "retimed": [câu cũ được sửa giờ], "removed": số câu cũ bị thay, "replaced": số câu cũ bị cắt ngắn được
    thay, "skipped": số câu mới bỏ vì đã có}."""
    from .dual import core

    new = sorted((s for s in new if inside(s, spans)), key=lambda s: s.start)
    removed = 0
    if mode == "replace":
        keep = [s for s in existing if not inside(s, spans)]
        removed = len(existing) - len(keep)
    else:
        keep = list(existing)
    added, retimed, skipped, replaced = [], [], 0, 0
    fresh: set[int] = set()            # câu vừa thêm ở lượt này (2 câu mới chồng nhau -> như cũ: bỏ câu sau)
    for s in new:
        dup, grow = False, []
        for o in keep:
            ov = _overlap(s.start, s.end, o.start, o.end)
            if not ov:
                continue
            if ov >= SAME * max(1e-3, s.end - s.start):
                dup = True
                break
            if ov >= SAME * max(1e-3, o.end - o.start):
                if id(o) in fresh:
                    dup = True
                    break
                if same_words(o.text, s.text):
                    grow.append(o)          # câu cũ ngắn cùng lời: mẩu / bản cắt ngắn của câu này
        if dup:
            skipped += 1
            continue
        for o in keep:
            if any(o is g for g in grow) or not _overlap(s.start, s.end, o.start, o.end):
                continue
            _trim(s, o)
        if s.end - s.start < MIN_LINE:
            skipped += 1
            continue
        ct = s.extra.get("ct")
        if ct and (ct[0][0] < s.start - 0.05 or ct[-1][1] > s.end + 0.05):
            s.extra.pop("ct", None)       # xén mép -> mốc từng chữ không còn khớp
        if len(grow) == 1 and core(grow[0].text) == core(s.text):
            o = grow[0]                     # cùng chữ: giữ câu cũ (bản dịch, giọng lồng), chỉ sửa giờ
            o.extra["span_retimed"] = [round(o.start, 3), round(o.end, 3)]
            o.start, o.end = s.start, s.end
            for k in ("ct", "snap", "edge_fix", "ocr_time"):
                o.extra.pop(k, None)
            for k in ("ct", "ocr_time"):
                if s.extra.get(k):
                    o.extra[k] = s.extra[k]
            retimed.append(o)
            continue
        if grow:
            keep = [o for o in keep if not any(o is g for g in grow)]
            removed += len(grow)
            replaced += len(grow)
        added.append(s)
        fresh.add(id(s))
        keep.append(s)
    keep.sort(key=lambda s: (s.start, s.end))
    return {"segs": keep, "added": added, "retimed": retimed, "removed": removed, "replaced": replaced,
            "skipped": skipped}


def nearest_speaker(new: list, existing: list) -> int:
    """Câu mới chưa có người nói -> người nói của câu cũ gần nhất về thời gian. Trả số câu đã gán."""
    known = [(float(o.start + o.end) / 2, o.speaker) for o in existing if o.speaker]
    n = 0
    if not known:
        return 0
    for s in new:
        if s.speaker:
            continue
        m = (s.start + s.end) / 2
        s.speaker = min(known, key=lambda k: abs(k[0] - m))[1]
        n += 1
    return n


def note(st: dict | None) -> str:
    """Kết quả tách theo đoạn (thông báo xong việc)."""
    if not st:
        return ""
    t = f"tách {st.get('spans', 0)} đoạn ({st.get('secs', 0):.0f} s): thêm {st.get('added', 0)} câu"
    rep = int(st.get("replaced") or 0)
    if st.get("retimed"):
        t += f", sửa giờ {st['retimed']} câu cũ bị cắt ngắn"
    if rep:
        t += f", thay {rep} câu cũ bị cắt ngắn / thiếu chữ"
    if int(st.get("removed") or 0) - rep > 0:
        t += f", thay {int(st['removed']) - rep} câu cũ"
    if st.get("skipped"):
        t += (f", bỏ {st['skipped']} câu đã có sẵn (câu cũ đã phủ giờ đó – muốn lấy câu mới thì chọn "
              "'Thay câu cũ trong đoạn')")
    return t
