"""Giọng OmniVoice CHỌN SẴN – không cần file ghi âm mẫu.

OmniVoice tự tạo giọng theo một mô tả ngắn (tính năng "voice design", `generate(instruct=...)`). Mô tả chỉ được dùng
đúng các từ OmniVoice hiểu (sai 1 từ là báo lỗi):
  giới tính : male, female
  tuổi      : child, teenager, young adult, middle-aged, elderly
  cao độ    : very low pitch, low pitch, moderate pitch, high pitch, very high pitch
  kiểu      : whisper

Giọng tạo theo mô tả mỗi lần đọc lại ra 1 người khác -> mỗi giọng đọc 1 CÂU MẪU cố định (hạt ngẫu nhiên cố định) đúng 1
lần, lưu data/voices/design/, rồi mọi câu lồng tiếng NHÁI LẠI câu mẫu đó -> cả video giữ đúng 1 giọng.
"""
from __future__ import annotations

import hashlib
import re
from pathlib import Path

from .config import DATA_DIR

PREFIX = "omnid:"
DESIGN_DIR = DATA_DIR / "voices" / "design"
# câu mẫu: đủ dài (~6 giây) để nhái giọng ổn định, đủ thanh điệu tiếng Việt, không có số
ANCHOR_TEXT = ("Xin chào các bạn, hôm nay mình sẽ kể cho các bạn nghe một câu chuyện thật thú vị. "
               "Hãy ngồi xuống và lắng nghe nhé.")
# lồng tiếng Anh / Thái / Nhật / Hàn: câu mẫu cùng tiếng -> giọng nhái đọc đúng giọng điệu tiếng đó
ANCHOR_TEXTS = {
    "vi": ANCHOR_TEXT,
    "en": "Hello everyone, today I am going to tell you a really interesting story. Sit back, relax and listen closely.",
    "th": "สวัสดีทุกคน วันนี้ฉันจะเล่าเรื่องที่น่าสนใจมากให้ฟัง นั่งลงสบาย ๆ แล้วตั้งใจฟังนะ",
    "ja": "皆さん、こんにちは。今日はとても面白いお話をしたいと思います。ゆっくり座って聞いてくださいね。",
    "ko": "여러분 안녕하세요. 오늘은 정말 재미있는 이야기를 들려드릴게요. 편하게 앉아서 잘 들어 주세요.",
}


def anchor_text(lang: str = "vi") -> str:
    return ANCHOR_TEXTS.get(lang, ANCHOR_TEXT)

# (mã, tên hiện trên giao diện, giọng nam/nữ cho "Tự gán giọng", mô tả gửi OmniVoice, hạt ngẫu nhiên, tiêu biểu)
PRESETS: list[tuple[str, str, str, str, int, bool]] = [
    ("nu_tre", "Nữ trẻ – tự nhiên", "female", "female, young adult, moderate pitch", 1101, True),
    ("nu_trong", "Nữ trẻ – trong, cao", "female", "female, young adult, high pitch", 1102, True),
    ("nu_am", "Nữ trẻ – ấm, trầm", "female", "female, young adult, low pitch", 1103, False),
    ("thieu_nu", "Thiếu nữ", "female", "female, teenager, high pitch", 1104, True),
    ("nu_trung_nien", "Nữ trung niên", "female", "female, middle-aged, moderate pitch", 1105, True),
    ("nu_trung_nien_tram", "Nữ trung niên – trầm, nghiêm", "female", "female, middle-aged, low pitch", 1106, False),
    ("ba_lao", "Bà lão", "female", "female, elderly, moderate pitch", 1107, False),
    ("ba_lao_cao", "Bà lão – giọng cao", "female", "female, elderly, high pitch", 1108, False),
    ("be_gai", "Bé gái", "female", "female, child, high pitch", 1109, False),
    ("nu_thi_tham", "Nữ thì thầm", "female", "female, young adult, moderate pitch, whisper", 1110, False),
    ("nam_tre", "Nam trẻ – tự nhiên", "male", "male, young adult, moderate pitch", 1201, True),
    ("nam_tram", "Nam trẻ – trầm ấm", "male", "male, young adult, low pitch", 1202, True),
    ("nam_sang", "Nam trẻ – sáng, cao", "male", "male, young adult, high pitch", 1203, False),
    ("thieu_nien", "Thiếu niên", "male", "male, teenager, moderate pitch", 1204, True),
    ("nam_trung_nien", "Nam trung niên", "male", "male, middle-aged, moderate pitch", 1205, True),
    ("nam_rat_tram", "Nam trung niên – rất trầm (tướng quân, phản diện)", "male",
     "male, middle-aged, very low pitch", 1206, False),
    ("ong_lao", "Ông lão – trầm", "male", "male, elderly, low pitch", 1207, False),
    ("ong_lao_cao", "Ông lão – giọng cao, lanh lảnh", "male", "male, elderly, high pitch", 1208, False),
    ("nam_ke_chuyen", "Nam kể chuyện – rất trầm", "male", "male, elderly, very low pitch", 1209, False),
    ("be_trai", "Bé trai", "male", "male, child, moderate pitch", 1210, False),
    ("nam_thi_tham", "Nam thì thầm", "male", "male, young adult, low pitch, whisper", 1211, False),
    ("hoat_hinh", "Hoạt hình – rất cao", "", "child, very high pitch", 1301, False),
    ("bi_an", "Thì thầm bí ẩn", "", "elderly, very low pitch, whisper", 1302, False),
]
_BY_ID = {p[0]: p for p in PRESETS}


def get(pid: str) -> dict | None:
    p = _BY_ID.get(pid[len(PREFIX):] if pid.startswith(PREFIX) else pid)
    if not p:
        return None
    return {"id": p[0], "name": p[1], "gender": p[2], "instruct": p[3], "seed": p[4], "featured": p[5]}


def voices() -> list[dict]:
    """Cho danh sách giọng: tiêu biểu (⭐) đứng đầu."""
    items = sorted(PRESETS, key=lambda p: not p[5])
    return [{"id": PREFIX + p[0], "name": f"{'⭐ ' if p[5] else ''}{p[1]} – chọn sẵn (OmniVoice)", "gender": p[2]}
            for p in items]


def key(pid: str, repo: str, lang: str = "vi") -> str:
    """Mã của câu mẫu: đổi mô tả / hạt / mô hình / câu mẫu -> tạo câu mẫu mới (bản lồng tiếng cũng đọc lại)."""
    p = get(pid)
    raw = f"{p['instruct']}|{p['seed']}|{repo}|{anchor_text(lang)}" if p else pid
    return hashlib.md5(raw.encode()).hexdigest()[:10]


def anchor_path(pid: str, repo: str, lang: str = "vi") -> Path:
    p = get(pid)
    tag = "" if lang == "vi" else f"{lang}_"
    return DESIGN_DIR / f"{p['id'] if p else 'x'}_{tag}{key(pid, repo, lang)}.wav"


def _stale(f: Path, pid: str, lang: str) -> bool:
    """File câu mẫu cũ của CÙNG giọng + cùng tiếng ("nu_trung_nien_…" không được xoá nhầm "nu_trung_nien_tram_…")."""
    m = re.fullmatch(re.escape(pid) + r"_(?:(en|th|ja|ko)_)?[0-9a-f]{10}", f.stem)
    return bool(m) and (m.group(1) or "vi") == lang


def ensure_anchor(worker, pid: str, repo: str, lang: str = "vi") -> tuple[str, str]:
    """(file câu mẫu, lời câu mẫu) của 1 giọng chọn sẵn; chưa có thì cho OmniVoice tạo (≈ vài giây, chỉ 1 lần).
    Câu mẫu hỏng (im lặng, tiếng bíp) -> tạo lại với hạt khác, tối đa 3 lần. lang: tiếng của câu mẫu."""
    from .tts import read_wav
    from .voicefix import voice_problem

    p = get(pid)
    if not p:
        raise ValueError("Giọng chọn sẵn này không còn trong danh sách")
    out = anchor_path(pid, repo, lang)
    text = anchor_text(lang)
    if out.exists() and out.stat().st_size > 0:
        return str(out), text
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".tmp.wav")
    why = ""
    for k in range(3):
        worker.synth(text, tmp, instruct=p["instruct"], seed=p["seed"] + 7919 * k, language=lang)
        try:
            a, sr = read_wav(tmp)
            why = voice_problem(a, sr) or ""
        except Exception:  # noqa: BLE001 – không đọc được file -> coi như dùng được
            why = ""
        if not why:
            tmp.replace(out)
            for old in out.parent.glob(f"{p['id']}_*.wav"):   # câu mẫu của mô hình / mô tả cũ
                if old != out and _stale(old, p["id"], lang):
                    old.unlink(missing_ok=True)
            return str(out), text
    tmp.unlink(missing_ok=True)
    raise RuntimeError(f"OmniVoice không tạo được giọng '{p['name']}' ({why}) – chọn giọng khác")
