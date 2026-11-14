"""Cảm xúc từng câu cho giọng lồng VoxCPM2 (2.4.15).

extra["emo"] = nhãn tiếng Việt cách nhau bằng dấu phẩy ("giận dữ, quát to") hoặc chữ tự gõ (tiếng Việt / Anh); "" =
bình thường (đã xét, không cần AI hỏi lại); không có khoá = chưa xét. Khi đọc: VoxCPM2 nhận chỉ dẫn "(angry,
shouting)" đặt trước lời ở chế độ GIỌNG THAM CHIẾU (chế độ đọc tiếp file mẫu không nhận chỉ dẫn – bộ CLI của voxcpm cũng
cấm dùng chung) -> câu có cảm xúc giữ âm sắc giọng nhưng nhịp có thể khác các câu thường một chút.
AI ghi cảm xúc: `ai_emotions` gửi lô câu (gốc + dịch + người nói) cho công cụ dịch AI, chỉ nhận nhãn có trong danh sách."""
from __future__ import annotations

import json
import unicodedata

# (nhãn hiện trên giao diện, chỉ dẫn tiếng Anh cho VoxCPM2)
EMOTIONS: list[tuple[str, str]] = [
    ("giận dữ", "angry, furious"),
    ("quát to", "shouting loudly"),
    ("buồn", "sad, sorrowful"),
    ("khóc", "crying, sobbing"),
    ("vui", "happy, cheerful"),
    ("cười", "laughing"),
    ("hào hứng", "excited, energetic"),
    ("sợ hãi", "scared, trembling voice"),
    ("lo lắng", "worried, anxious"),
    ("ngạc nhiên", "surprised"),
    ("thì thầm", "whispering softly"),
    ("dịu dàng", "gentle, tender"),
    ("lạnh lùng", "cold, indifferent"),
    ("khinh bỉ", "contemptuous, sneering"),
    ("mệt mỏi", "tired, exhausted"),
    ("gấp gáp", "hurried, speaking fast"),
    ("nghiêm túc", "serious, firm"),
    ("đau đớn", "in pain, groaning"),
    ("nũng nịu", "cute, coquettish"),
    ("bình tĩnh", "calm, composed"),
]
NEUTRAL = "bình thường"
MAX_LABELS = 2
MAX_CONTROL = 120
BATCH = 60


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFD", (s or "").lower().strip())
    s = "".join(c for c in s if unicodedata.category(c) != "Mn").replace("đ", "d")
    return " ".join(s.split())


_BY_NORM = {_norm(vi): (vi, en) for vi, en in EMOTIONS}


def labels() -> list[str]:
    return [vi for vi, _en in EMOTIONS]


def parts(emo: str | None) -> list[str]:
    return [x.strip() for x in (emo or "").replace(";", ",").split(",") if x.strip()]


def clean(emo: str | None) -> str:
    """Chuẩn hoá chữ người dùng / AI gõ: nhãn có trong danh sách viết đúng dấu, bỏ "bình thường", ≤ 2 nhãn."""
    out = []
    for x in parts(emo):
        k = _norm(x)
        if k in (_norm(NEUTRAL), "neutral", "normal", "khong", "none"):
            continue
        lab = _BY_NORM.get(k, (x, ""))[0]
        if lab not in out:
            out.append(lab)
    return ", ".join(out[:MAX_LABELS + 1])


def known(emo: str | None) -> str:
    """Chỉ giữ nhãn có trong danh sách (kết quả AI) – ≤ 2 nhãn."""
    out = []
    for x in parts(emo):
        hit = _BY_NORM.get(_norm(x))
        if hit and hit[0] not in out:
            out.append(hit[0])
    return ", ".join(out[:MAX_LABELS])


def to_control(emo: str | None) -> str:
    """Chỉ dẫn cho VoxCPM2: nhãn -> cụm tiếng Anh; chữ tự gõ giữ nguyên. "" = đọc bình thường."""
    out = []
    for x in parts(clean(emo)):
        hit = _BY_NORM.get(_norm(x))
        out.append(hit[1] if hit else x)
    s = ", ".join(out)
    for ch in "()[]":
        s = s.replace(ch, " ")
    return " ".join(s.split())[:MAX_CONTROL]


PROMPT = (
    "Bạn là đạo diễn lồng tiếng phim. Với MỖI dòng thoại, chọn cảm xúc / cách nói của nhân vật khi nói dòng đó để bộ "
    "đọc giọng đọc cho đúng.\n"
    "Danh sách nhãn được dùng (chỉ dùng đúng các nhãn này): " + ", ".join(labels()) + ".\n"
    "Luật:\n"
    "1. Mỗi dòng 0–2 nhãn, cách nhau dấu phẩy. Dòng nói bình thường, kể chuyện, hỏi han thường -> \"\" (chuỗi rỗng). "
    "Chỉ ghi nhãn khi cảm xúc RÕ trong lời (dấu !, từ chửi, khóc lóc, van xin, cười, hét, thì thầm…) hoặc ngữ cảnh "
    "dòng trước / sau cho thấy rõ.\n"
    "2. Đọc cả dòng gốc tiếng Trung và bản dịch, xét các dòng trước / sau để hiểu tình huống.\n"
    "3. Không ghi nhãn cho mọi dòng – phần lớn dòng thoại là bình thường.\n"
    "Trả về DUY NHẤT JSON {\"số dòng\": \"nhãn, nhãn\" hoặc \"\"} đủ mọi dòng được gửi.")


def ai_emotions(llm, items: list[dict], progress=None, cancel=None) -> dict[int, str]:
    """items: [{"n": số câu trong phim, "goc": chữ gốc, "dich": bản dịch, "nguoi_noi": "S1 · nữ"}] theo thứ tự phim.
    Trả {số câu: nhãn ("" = bình thường)} – chỉ câu AI trả lời hợp lệ."""
    from .ffmpeg import Cancelled
    from .translate import _extract_json

    out: dict[int, str] = {}
    for b in range(0, len(items), BATCH):
        if cancel and cancel():
            raise Cancelled()
        part = items[b:b + BATCH]
        payload = {str(x["n"] + 1): {k: x[k] for k in ("goc", "dich", "nguoi_noi") if x.get(k)} for x in part}
        if progress:
            progress(b / max(1, len(items)), f"AI ghi cảm xúc {b + 1}–{b + len(part)}/{len(items)} câu")
        txt = llm.chat(PROMPT, json.dumps(payload, ensure_ascii=False), max_tokens=400 + 30 * len(part),
                       temperature=0.2)
        d = _extract_json(txt) or {}
        for x in part:
            v = d.get(str(x["n"] + 1))
            if isinstance(v, (str, list)):
                out[x["n"]] = known(", ".join(v) if isinstance(v, list) else v)
    if progress:
        progress(1.0, f"AI đã ghi cảm xúc {len(out)} câu")
    return out
