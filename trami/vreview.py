"""A – Gemini XEM + NGHE video để soát phụ đề gốc (2.1.25). Hàm thuần (không gọi mạng) – pipeline.gemini_review gọi
Gemini (trami/gemvideo.py) rồi dùng các hàm ở đây.

Mỗi khúc video vài phút + các câu phụ đề trong khúc (số dòng | giờ trong khúc | người nói | chữ | độ chắc) -> Gemini báo:
  - "sua": dòng nghe nhầm / sai tên riêng (chữ đúng), dòng không phải lời nói, lời bài hát;
  - "dung": dòng nghe rõ và đúng nguyên văn (= đã xác nhận, bước "Xoá câu không chắc" không xoá);
  - "thieu": có người nói mà chưa có phụ đề (giờ ước lượng);
  - "ten": tên riêng thấy trên màn hình / nghe rõ (-> Bảng tên riêng).
Áp dụng AN TOÀN như "Nghe 2 bộ rồi so": chữ mới gần chữ cũ (giống nhau / đọc giống nhau) -> điền; khác hẳn -> tô xanh để
người dùng chọn ("Dùng bản bộ nghe 2"); không phải lời / lời hát -> chỉ đánh dấu (tím / 🎵), không tự xoá.
"""
from __future__ import annotations

import json
from difflib import SequenceMatcher

from .dual import core, merge_alt
from .subtitle import Segment

WIN_SEC = 300.0       # mỗi khúc video gửi Gemini tối đa 5 phút (độ phân giải thường – đọc được chữ trên màn hình)
WIN_GAP = 4.0         # chỗ cắt khúc ưu tiên ở khoảng lặng ≥ 4 s giữa 2 câu
PAD = 1.5             # khúc video rộng hơn câu đầu / cuối 1,5 s
CTX = 2               # câu đã tích: gửi kèm 2 câu trước / sau làm ngữ cảnh
FIX_SIM = 0.5         # chữ mới giống chữ cũ ≥ 50 % (hoặc đọc giống ≥ 70 %) mới tự điền
SOUND_SIM = 0.7
ADD_MIN, ADD_MAX = 0.3, 15.0   # câu bị sót: dài 0,3–15 s

PROMPT = """Bạn là biên tập viên phụ đề tiếng Trung. Bạn được XEM và NGHE 1 khúc video cùng danh sách phụ đề tiếng
Trung do máy nghe chép ra. Mỗi dòng: số | giờ trong khúc video (giây, tính từ đầu khúc gửi kèm) | người nói | chữ máy
nghe | độ chắc của máy nghe (0–1, thấp = máy không chắc). Đối chiếu với TIẾNG NÓI THẬT và HÌNH (chữ trên màn hình, ai
đang mở miệng, cảnh) rồi chỉ báo những dòng SAI.

Trả về JSON:
{"sua": [{"so": <số dòng>, "loai": "nghe_nham" | "ten_rieng" | "khong_phai_loi" | "hat", "cau": "<chữ đúng – tiếng
Trung giản thể>", "chac": true | false, "ly_do": "<ngắn, tiếng Việt>"}],
 "dung": [<số các dòng bạn nghe rõ và chắc chắn đúng nguyên văn>],
 "thieu": [{"tu": <giây trong khúc>, "den": <giây trong khúc>, "cau": "<lời tiếng Trung>"}],
 "ten": [{"goc": "<tên riêng tiếng Trung>", "viet": "<phiên âm Hán Việt>", "nguon": "man_hinh" | "nghe"}]}

Luật:
1. CHỈ báo dòng nghe / thấy rõ là sai. Nghe không rõ (nhạc to, nói nhỏ, nhiều người nói cùng lúc) -> không báo, hoặc
   "chac": false.
2. "nghe_nham": chép đúng chữ người đó nói (chữ đồng âm 他/她, 的/得, 在/再, con số…). Giữ dấu câu, KHÔNG viết lại câu cho
   hay hơn, KHÔNG dịch, KHÔNG thêm chữ người nói không nói, không bỏ chữ người nói có nói.
3. "ten_rieng": tên người / địa danh / môn phái / chiêu thức sai chữ – ưu tiên cách viết hiện trên màn hình (tên nhân
   vật hiện chữ, biển hiệu, chữ giới thiệu, phụ đề in sẵn trong video).
4. "khong_phai_loi": dòng không phải lời nói của nhân vật (máy chép nhạc / tiếng động / tiếng hét thành chữ, chữ bịa
   lúc không ai nói). "hat": lời bài hát / nhạc phim đang hát. 2 loại này để "cau" trống.
5. "thieu": có người NÓI rõ ràng mà không dòng nào chép – chép lời + giờ. Không chép lời bài hát, không chép chữ trên
   màn hình mà không ai đọc.
6. Không đổi số dòng, không gộp / tách dòng. Dòng ghi "(ngữ cảnh)" chỉ để hiểu, không cần soát.
7. Chỉ trả JSON."""


def windows(segs, rows: list[int] | None = None, total: float = 0.0, max_len: float = WIN_SEC,
            ctx: int = CTX) -> list[tuple[float, float, list[int], set[int]]]:
    """Gom câu thành khúc video ≤ max_len: [(đầu, cuối, [câu gửi kèm], {câu cần soát})]. rows None = cả phim; có rows
    -> mỗi câu tích kèm `ctx` câu trước / sau làm ngữ cảnh."""
    idx = [i for i, s in enumerate(segs) if core(s.text)]
    if not idx:
        return []
    want = set(idx) if rows is None else {r for r in rows if 0 <= r < len(segs) and core(segs[r].text)}
    if not want:
        return []
    send = set(want)
    if rows is not None:
        pos = {i: k for k, i in enumerate(idx)}
        for r in want:
            k = pos[r]
            send.update(idx[max(0, k - ctx):k + ctx + 1])
    order = sorted(send)
    out: list[tuple[float, float, list[int], set[int]]] = []
    cur: list[int] = []
    for i in order:
        s = segs[i]
        if cur:
            a = segs[cur[0]].start
            gap = s.start - segs[cur[-1]].end
            # khúc đã dài: cắt ở khoảng lặng; quá dài / cách xa (câu tích rải rác) -> khúc mới
            if s.end - a > max_len or gap > 60 or (s.end - a > max_len * 0.8 and gap >= WIN_GAP):
                out.append(_win(segs, cur, want, total))
                cur = []
        cur.append(i)
    if cur:
        out.append(_win(segs, cur, want, total))
    return out


def _win(segs, cur: list[int], want: set[int], total: float):
    a = max(0.0, segs[cur[0]].start - PAD)
    b = segs[cur[-1]].end + PAD
    if total:
        b = min(b, total)
    return a, max(b, a + 1.0), list(cur), {i for i in cur if i in want}


def payload(segs, rows: list[int], check: set[int], a: float, profile: str = "") -> tuple[str, dict[int, int]]:
    """Chữ gửi kèm khúc video + {số dòng: số câu}."""
    lines, num = [], {}
    for n, i in enumerate(rows, 1):
        s = segs[i]
        num[n] = i
        conf = s.extra.get("asr_conf")
        tail = f" | {float(conf):.2f}" if conf is not None else ""
        lines.append(f"{n} | {max(0.0, s.start - a):.1f}–{max(0.0, s.end - a):.1f} | {s.speaker or '?'} | "
                     f"{s.text.strip()}{tail}" + ("" if i in check else " (ngữ cảnh)"))
    head = ("HỒ SƠ PHIM (tên nhân vật, thuật ngữ):\n" + profile.strip() + "\n\n") if profile.strip() else ""
    return head + f"PHỤ ĐỀ TRONG KHÚC VIDEO ({len(lines)} dòng):\n" + "\n".join(lines), num


def _sounds_alike(a: str, b: str) -> float:
    """Độ giống theo âm đọc (pinyin không dấu thanh) – 0 nếu không có pypinyin."""
    try:
        from pypinyin import lazy_pinyin
    except ImportError:
        return 0.0
    pa, pb = lazy_pinyin(a), lazy_pinyin(b)
    return SequenceMatcher(None, pa, pb, autojunk=False).ratio() if pa and pb else 0.0


def close_enough(old: str, new: str) -> bool:
    """Chữ Gemini sửa đủ gần chữ máy nghe để tự điền (không phải 1 câu khác hẳn / viết lại)."""
    o, n = core(old), core(new)
    if not o or not n:
        return False
    if abs(len(n) - len(o)) > max(2, int(0.3 * len(o))):
        return False
    return SequenceMatcher(None, o, n, autojunk=False).ratio() >= FIX_SIM or _sounds_alike(o, n) >= SOUND_SIM


LABEL = {"nghe_nham": "nghe nhầm", "ten_rieng": "sai tên riêng", "khong_phai_loi": "không phải lời nói",
         "hat": "lời bài hát"}


def parse(reply: str) -> dict:
    from .skills import _json

    try:
        d = _json(reply)
    except ValueError:
        return {}
    return d if isinstance(d, dict) else {}


def apply(segs, d: dict, num: dict[int, int], check: set[int], a: float, st: dict, simplify=None) -> dict:
    """Áp kết quả 1 khúc. Sửa `segs` tại chỗ (trừ câu bị sót – trả về để chèn sau). simplify: chữ phồn -> giản.
    Trả {"fixed": [câu], "alt": [câu], "flag": [câu], "added": [Segment], "names": [(gốc, Hán Việt)]}."""
    simplify = simplify or (lambda t: t)
    res: dict = {"fixed": [], "alt": [], "flag": [], "added": [], "names": [], "confirmed": []}
    touched = set()
    for it in d.get("sua") or []:
        if not isinstance(it, dict):
            continue
        try:
            i = num.get(int(it.get("so")))
        except (TypeError, ValueError):
            continue
        if i is None or i not in check or i in touched:
            continue
        x = segs[i]
        kind = str(it.get("loai") or "").strip()
        why = str(it.get("ly_do") or "")[:160]
        sure = it.get("chac") is not False and str(it.get("chac")).lower() != "false"
        if kind in ("khong_phai_loi", "hat"):
            if x.extra.get("voice_ok") or x.extra.get("not_song"):
                continue      # người dùng đã bấm "Không phải nhạc" -> không đánh dấu lại
            if not sure:
                continue
            if kind == "hat":
                x.extra["song"] = True
                x.extra["ai_song"] = "Gemini xem video" + (f": {why}" if why else "")
                st["song"] = st.get("song", 0) + 1
            else:
                x.extra["noise"] = "Gemini xem video: không phải lời nói" + (f" – {why}" if why else "")
                st["noise"] = st.get("noise", 0) + 1
            res["flag"].append(i)
            touched.add(i)
            continue
        if kind not in ("nghe_nham", "ten_rieng"):
            continue
        new = simplify(str(it.get("cau") or "")).strip()
        if not core(new) or core(new) == core(x.text):
            continue
        touched.add(i)
        label = LABEL.get(kind, kind)
        if sure and close_enough(x.text, new):
            old = x.text
            x.text = merge_alt(old, core(new))
            x.extra.setdefault("src_before", old)
            x.extra["gem_fix"] = label + (f": {why}" if why else "")
            for k in ("asr_unsure", "asr_alt", "asr_alt_eng", "asr_pick"):
                x.extra.pop(k, None)
            x.extra["asr_same"] = "gemini"
            if kind == "ten_rieng":
                x.extra.setdefault("name_fix", []).append([old, x.text])
            res["fixed"].append(i)
            st["fixed"] = st.get("fixed", 0) + 1
        else:   # khác hẳn / không chắc -> chữ xanh, người dùng nghe lại rồi chọn "Dùng bản bộ nghe 2"
            x.extra["asr_alt"] = core(new)
            x.extra["asr_alt_eng"] = "gemini"
            x.extra["asr_unsure"] = True
            x.extra["gem_note"] = label + (f": {why}" if why else "")
            x.extra.pop("asr_same", None)
            res["alt"].append(i)
            st["alt"] = st.get("alt", 0) + 1
    for n in d.get("dung") or []:
        try:
            i = num.get(int(n))
        except (TypeError, ValueError):
            continue
        if i is not None and i in check and i not in touched and not segs[i].extra.get("asr_same"):
            segs[i].extra["asr_same"] = "gemini"
            res["confirmed"].append(i)
            st["confirmed"] = st.get("confirmed", 0) + 1
    rows = sorted(num.values())
    lo = segs[rows[0]].start - 1.0 if rows else a
    hi = segs[rows[-1]].end + 1.0 if rows else a
    for it in d.get("thieu") or []:
        if not isinstance(it, dict):
            continue
        try:
            t0, t1 = a + float(it.get("tu")), a + float(it.get("den"))
        except (TypeError, ValueError):
            continue
        text = simplify(str(it.get("cau") or "")).strip()
        if not core(text) or not (ADD_MIN <= t1 - t0 <= ADD_MAX) or t0 < lo - 30 or t1 > hi + 30:
            continue
        # trùng câu đang có (Gemini đoán giờ lệch) -> bỏ
        if any(min(t1, s.end) - max(t0, s.start) > 0.5 * (t1 - t0) or
               SequenceMatcher(None, core(s.text), core(text)).ratio() >= 0.6 and abs(s.start - t0) < 5
               for s in segs):
            continue
        res["added"].append(Segment(round(t0, 3), round(t1, 3), text, "", None,
                                    {"from_gemini": True, "gem_note": "Gemini thấy có người nói mà phụ đề chưa có – giờ "
                                                                      "ước lượng, nên nghe lại"}))
        st["added"] = st.get("added", 0) + 1
    for it in d.get("ten") or []:
        if isinstance(it, dict) and core(str(it.get("goc") or "")) and str(it.get("viet") or "").strip():
            res["names"].append((simplify(str(it["goc"]).strip()), str(it["viet"]).strip()))
    return res


def insert_added(segs, added: list[Segment]) -> list[Segment]:
    """Chèn câu bị sót vào đúng chỗ theo giờ (không chồng câu bên: co lại cho vừa khoảng trống)."""
    out = list(segs)
    for s in sorted(added, key=lambda x: x.start):
        prev = max((x for x in out if x.start <= s.start), key=lambda x: x.start, default=None)
        nxt = min((x for x in out if x.start > s.start), key=lambda x: x.start, default=None)
        a = max(s.start, prev.end + 0.02) if prev else s.start
        b = min(s.end, nxt.start - 0.02) if nxt else s.end
        if b - a < ADD_MIN:
            continue
        s.start, s.end = round(a, 3), round(b, 3)
        out.append(s)
        out.sort(key=lambda x: x.start)
    return out


def names_text(names: list[tuple[str, str]], have: str) -> str:
    """Dòng "中文=Hán Việt" mới cho Bảng tên riêng (bỏ tên đã có)."""
    known = {ln.split("=", 1)[0].strip() for ln in (have or "").splitlines() if "=" in ln}
    out, seen = [], set()
    for zh, vi in names:
        if zh and zh not in known and zh not in seen and len(core(zh)) <= 12:
            out.append(f"{zh}={vi}")
            seen.add(zh)
    return "\n".join(out)


def note(st: dict | None) -> str:
    """1 dòng kết quả cho thông báo."""
    if not st:
        return ""
    if st.get("error") and not st.get("windows"):
        return f"⚠ Gemini chưa xem video được ({st['error']})"
    parts = [f"Gemini xem video {st.get('windows', 0)} khúc, soát {st.get('checked', 0)} câu"]
    det = []
    if st.get("fixed"):
        det.append(f"sửa {st['fixed']} câu")
    if st.get("alt"):
        det.append(f"{st['alt']} câu nghe ra khác hẳn (chữ xanh – nghe lại rồi 'Dùng bản bộ nghe 2')")
    if st.get("song"):
        det.append(f"{st['song']} câu lời bài hát (🎵)")
    if st.get("noise"):
        det.append(f"{st['noise']} câu không phải lời nói (chữ tím)")
    if st.get("added"):
        det.append(f"thêm {st['added']} câu bị sót (chữ cam – giờ ước lượng, nên nghe lại)")
    if st.get("names"):
        det.append(f"thêm {st['names']} tên riêng vào Bảng tên riêng")
    if st.get("confirmed"):
        det.append(f"xác nhận đúng {st['confirmed']} câu")
    t = parts[0] + (": " + ", ".join(det) if det else ": không có câu cần sửa")
    if st.get("failed"):
        t += f" · ⚠ {st['failed']} khúc lỗi ({st.get('error', '')[:100]})"
    return t


def dumps(d) -> str:
    return json.dumps(d, ensure_ascii=False)
