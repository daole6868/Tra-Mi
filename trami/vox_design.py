"""Giọng VoxCPM2 CHỌN SẴN – tạo theo mô tả bằng lời (tính năng "voice design" của VoxCPM2), không cần file mẫu.

VoxCPM2 tạo giọng mới theo 1 câu mô tả (giới tính, tuổi, chất giọng, cảm xúc, tốc độ… – tiếng Anh), đặt trong ngoặc ở
đầu lời đọc. Giọng tạo theo mô tả mỗi lần đọc ra 1 người hơi khác -> như omni_design: mỗi giọng đọc 1 CÂU MẪU cố định
(hạt ngẫu nhiên cố định) đúng 1 lần, lưu data/voices/vox_design/, rồi mọi câu lồng tiếng NHÁI LẠI câu mẫu đó (đọc tiếp
file mẫu + cùng file làm giọng tham chiếu) -> cả video giữ đúng 1 giọng, cả nhịp đọc + cảm xúc của câu mẫu.

Nhóm 📣 THUYẾT MINH: câu mẫu kiểu kể / tóm tắt phim (có cảm xúc, nhiều chỗ ngắt) -> mọi câu đọc theo nhịp đó.
Nhóm nhân vật: câu mẫu kiểu hội thoại. "Giọng tự mô tả": mô tả do người dùng viết ở Cài đặt (`voxcpm_design`).
Đặt tên theo kiểu giọng / vai, không nhái giọng người thật.
"""
from __future__ import annotations

import hashlib
import re
from pathlib import Path

from .config import DATA_DIR

PREFIX = "voxd:"
CUSTOM = "custom"
DESIGN_DIR = DATA_DIR / "voices" / "vox_design"
ICON_TM = "📣"
# câu mẫu khi tạo giọng: cố định thông số (đổi "Chất lượng" ở Cài đặt không làm đổi người đọc)
ANCHOR_CFG = 2.0
ANCHOR_STEPS = 10

TALK_TEXT = ("Ơ, sao giờ này cậu mới tới? Mọi người đợi cậu lâu lắm rồi đấy, mau vào đi, "
             "đồ ăn sắp nguội hết rồi.")

# (mã, tên, nam/nữ, mô tả gửi VoxCPM2, hạt, tiêu biểu, nhóm "tm" = thuyết minh, câu mẫu tiếng Việt)
PRESETS: list[tuple[str, str, str, str, int, bool, str, str]] = [
    ("tm_nu_nhanh", "Nữ thuyết minh – trẻ, nhanh, có cảm xúc", "female",
     "A young woman narrating a drama recap, bright and clear voice, fast lively pace, expressive and emotional "
     "storytelling, rising tension at key moments", 2101, True, "tm",
     "Các bạn có biết không, cô gái nghèo ấy vừa bước chân vào cửa, cả nhà chồng đã quay lưng. Nhưng không ai ngờ "
     "rằng, chỉ ba ngày sau, cô lại khiến tất cả phải cúi đầu."),
    ("tm_nu_am", "Nữ thuyết minh – ấm, chậm rãi, sâu lắng", "female",
     "A woman in her thirties, warm and gentle voice, calm steady pace, soft emotional storytelling", 2102, True,
     "tm", "Năm ấy, cô vẫn còn là một cô bé, ngày ngày ngồi bên bậc cửa chờ mẹ về. Cô không hề biết rằng, có những "
           "người đã đi rồi, sẽ không bao giờ quay lại nữa."),
    ("tm_nam_tram", "Nam thuyết minh – trầm, cuốn hút", "male",
     "A man narrating a movie recap, deep warm voice, steady confident pace, engaging and dramatic storytelling",
     2103, True, "tm",
     "Trong một đêm mưa lạnh, chàng trai bị đuổi khỏi gia tộc, trên tay không còn gì cả. Thế nhưng, không ai biết "
     "rằng, anh chính là người thừa kế duy nhất của cả gia sản."),
    ("tm_nam_hai", "Nam thuyết minh – trẻ, nhanh, hài hước", "male",
     "A young man narrating a funny video, energetic bright voice, fast pace, playful humorous tone", 2104, False,
     "tm", "Và thế là, anh chàng của chúng ta vừa ra khỏi cửa, đã vấp ngã ngay trước mặt cô gái mình thích. Đúng là "
           "xui hết chỗ nói luôn các bạn ạ!"),
    ("tm_nu_tin", "Nữ đọc tin – rõ ràng, chuẩn mực", "female",
     "A female news anchor, clear standard pronunciation, neutral professional tone, medium pace", 2105, False,
     "tm", "Chào mừng quý vị đến với bản tin hôm nay. Sáng nay, nhiều tuyến phố trung tâm đã thông thoáng trở lại, "
           "sau đợt mưa lớn kéo dài suốt cả tuần qua."),
    ("tm_nam_dem", "Nam kể chuyện đêm khuya – trầm, chậm, bí ẩn", "male",
     "A middle-aged man, low husky voice, slow pace, mysterious late-night storytelling, quiet and suspenseful",
     2106, False, "tm",
     "Đêm hôm đó, căn nhà cuối làng bỗng nhiên sáng đèn. Không ai dám lại gần, bởi vì ai cũng biết, chủ nhân của nó "
     "đã mất từ mười năm trước."),
    ("nu_tre", "Nữ trẻ – ngọt ngào, tự nhiên", "female",
     "A young woman, sweet and gentle voice, natural conversational tone", 2201, True, "", ""),
    ("nu_manh", "Nữ trẻ – mạnh mẽ, dứt khoát", "female",
     "A young woman, confident firm voice, crisp and decisive, slightly cold", 2202, False, "", ""),
    ("thieu_nu", "Thiếu nữ – trong trẻo, nhõng nhẽo", "female",
     "A teenage girl, high cute voice, playful and a little coquettish", 2203, False, "", ""),
    ("nu_trung_nien", "Nữ trung niên – đằm thắm", "female",
     "A middle-aged woman, warm mature voice, kind and calm", 2204, False, "", ""),
    ("ba_lao", "Bà lão – hiền từ", "female", "An elderly woman, kind gentle voice, slow and warm", 2205, False, "",
     ""),
    ("be_gai", "Bé gái – trong trẻo", "female", "A little girl around eight years old, high bright cheerful voice",
     2206, False, "", ""),
    ("nam_tre", "Nam trẻ – tự nhiên", "male", "A young man, natural clear voice, friendly conversational tone", 2301,
     True, "", ""),
    ("nam_am", "Nam trẻ – trầm ấm, tình cảm", "male", "A young man, low warm voice, gentle and affectionate", 2302,
     False, "", ""),
    ("nam_trung_nien", "Nam trung niên – uy nghiêm", "male",
     "A middle-aged man, deep authoritative voice, slow and serious", 2303, True, "", ""),
    ("ong_lao", "Ông lão – khàn, điềm đạm", "male", "An elderly man, slightly hoarse voice, calm and wise, slow pace",
     2304, False, "", ""),
    ("be_trai", "Bé trai – lanh lợi", "male", "A little boy around eight years old, bright playful voice", 2305,
     False, "", ""),
    ("phan_dien", "Phản diện – lạnh lùng, đe doạ", "male",
     "A man with a cold low voice, menacing and arrogant, slow deliberate pace", 2306, False, "", ""),
]
_BY_ID = {p[0]: p for p in PRESETS}
CUSTOM_SEED = 2401


def _bare(pid: str) -> str:
    return pid[len(PREFIX):] if pid.startswith(PREFIX) else pid


def custom_desc(settings: dict | None) -> str:
    return re.sub(r"\s+", " ", str((settings or {}).get("voxcpm_design") or "")).strip()[:300]


def get(pid: str, settings: dict | None = None) -> dict | None:
    b = _bare(pid)
    if b == CUSTOM:
        desc = custom_desc(settings)
        if not desc:
            return None
        g = str((settings or {}).get("voxcpm_design_gender") or "")
        return {"id": CUSTOM, "name": "Giọng tự mô tả", "gender": g if g in ("male", "female") else "",
                "desc": desc, "seed": CUSTOM_SEED + int((settings or {}).get("voxcpm_design_seed") or 0),
                "featured": False, "group": "custom", "anchor": ""}
    p = _BY_ID.get(b)
    if not p:
        return None
    return {"id": p[0], "name": p[1], "gender": p[2], "desc": p[3], "seed": p[4], "featured": p[5], "group": p[6],
            "anchor": p[7]}


def voices(settings: dict | None = None) -> list[dict]:
    """Cho danh sách giọng: 📣 thuyết minh trước, rồi giọng nhân vật (⭐ tiêu biểu trước), rồi giọng tự mô tả."""
    out = []
    order = sorted(PRESETS, key=lambda p: (p[6] != "tm", not p[5]))
    for p in order:
        icon = f"{ICON_TM} " if p[6] == "tm" else ("⭐ " if p[5] else "")
        out.append({"id": PREFIX + p[0], "name": f"{icon}{p[1]} – chọn sẵn (VoxCPM2)", "gender": p[2],
                    "tip": f"Mô tả gửi VoxCPM2: {p[3]}"})
    c = get(CUSTOM, settings)
    if c:
        out.append({"id": PREFIX + CUSTOM, "name": "✏️ Giọng tự mô tả (VoxCPM2)", "gender": c["gender"],
                    "tip": f"Mô tả của bạn (Cài đặt → VoxCPM2): {c['desc']}"})
    return out


def anchor_text(pid: str, lang: str = "vi", settings: dict | None = None) -> str:
    """Câu mẫu: tiếng Việt = câu riêng của giọng (thuyết minh) / câu hội thoại; tiếng khác = câu mẫu chung theo tiếng."""
    if lang != "vi":
        from .omni_design import ANCHOR_TEXTS

        return ANCHOR_TEXTS.get(lang, ANCHOR_TEXTS["en"])
    p = get(pid, settings)
    return (p or {}).get("anchor") or TALK_TEXT


def key(pid: str, repo: str, lang: str = "vi", settings: dict | None = None) -> str:
    """Mã câu mẫu: đổi mô tả / hạt / mô hình / câu mẫu -> tạo câu mẫu mới (bản lồng tiếng cũng đọc lại)."""
    p = get(pid, settings)
    raw = f"{p['desc']}|{p['seed']}|{repo}|{anchor_text(pid, lang, settings)}" if p else pid
    return hashlib.md5(raw.encode()).hexdigest()[:10]


def anchor_path(pid: str, repo: str, lang: str = "vi", settings: dict | None = None) -> Path:
    p = get(pid, settings)
    tag = "" if lang == "vi" else f"{lang}_"
    return DESIGN_DIR / f"{p['id'] if p else 'x'}_{tag}{key(pid, repo, lang, settings)}.wav"


def _stale(f: Path, pid: str, lang: str) -> bool:
    m = re.fullmatch(re.escape(pid) + r"_(?:(en|th|ja|ko)_)?[0-9a-f]{10}", f.stem)
    return bool(m) and (m.group(1) or "vi") == lang


def ensure_anchor(worker, pid: str, repo: str, lang: str = "vi", settings: dict | None = None) -> tuple[str, str]:
    """(file câu mẫu, lời câu mẫu); chưa có thì cho VoxCPM2 tạo theo mô tả (vài giây, chỉ 1 lần). Câu mẫu hỏng (im lặng,
    tiếng bíp) / dài bất thường (đọc lặp) -> tạo lại với hạt khác, tối đa 3 lần."""
    from .tts import read_wav, syllables
    from .voicefix import voice_problem

    p = get(pid, settings)
    if not p:
        raise ValueError("Giọng chọn sẵn này không còn (giọng tự mô tả: điền mô tả ở Cài đặt → VoxCPM2)")
    out = anchor_path(pid, repo, lang, settings)
    text = anchor_text(pid, lang, settings)
    if out.exists() and out.stat().st_size > 0:
        return str(out), text
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".tmp.wav")
    why = ""
    for k in range(3):
        worker.synth(text, tmp, control=p["desc"], seed=p["seed"] + 7919 * k, cfg=ANCHOR_CFG, steps=ANCHOR_STEPS)
        try:
            a, sr = read_wav(tmp)
            why = voice_problem(a, sr) or ""
            if not why and lang == "vi" and sr and len(a) / sr > 0.45 * syllables(text) + 2.0:
                why = "đọc dài bất thường"
        except Exception:  # noqa: BLE001 – không đọc được file -> coi như dùng được
            why = ""
        if not why:
            tmp.replace(out)
            for old in out.parent.glob(f"{p['id']}_*.wav"):
                if old != out and _stale(old, p["id"], lang):
                    old.unlink(missing_ok=True)
            return str(out), text
    tmp.unlink(missing_ok=True)
    raise RuntimeError(f"VoxCPM2 không tạo được giọng '{p['name']}' ({why}) – chọn giọng khác")
