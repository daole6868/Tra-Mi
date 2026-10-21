"""Ngôn ngữ đích (dịch sang): Việt, Anh, Thái, Nhật, Hàn – mọi thông tin theo từng tiếng ở 1 chỗ: tên, mã Google,
tên cho Hy-MT2, giọng Microsoft Edge, tốc độ nói tự nhiên (để dịch / rút gọn vừa thời gian nhân vật nói), font phụ đề.

Mỗi câu còn được NHẬN DIỆN tiếng theo chữ viết (`detect`) -> giọng đọc tự đổi đúng tiếng của câu (phụ đề nạp từ file
ngoài, câu sửa tay, dự án có câu nhiều thứ tiếng đều đọc đúng).
"""
from __future__ import annotations

import re
import sys
import unicodedata

DEFAULT = "vi"
TARGETS = ("vi", "en", "th", "ja", "ko")

INFO: dict[str, dict] = {
    "vi": {"name": "Tiếng Việt", "short": "Việt", "english": "Vietnamese", "google": "vi", "hymt": "越南语",
           "unit": "word", "rate": 4.6, "unit_vi": "tiếng", "unit_ai": "tiếng (âm tiết)",
           "edge": [("vi-VN-HoaiMyNeural", "Hoài My", "female"), ("vi-VN-NamMinhNeural", "Nam Minh", "male")]},
    "en": {"name": "Tiếng Anh", "short": "Anh", "english": "English", "google": "en", "hymt": "英语",
           "unit": "word", "rate": 2.7, "unit_vi": "từ", "unit_ai": "words",
           "edge": [("en-US-AriaNeural", "Aria", "female"), ("en-US-GuyNeural", "Guy", "male"),
                    ("en-US-JennyNeural", "Jenny", "female"), ("en-US-ChristopherNeural", "Christopher", "male"),
                    ("en-US-EmmaNeural", "Emma", "female"), ("en-US-AndrewNeural", "Andrew", "male")]},
    "th": {"name": "Tiếng Thái", "short": "Thái", "english": "Thai", "google": "th", "hymt": "泰语",
           "unit": "char", "rate": 11.0, "unit_vi": "chữ", "unit_ai": "Thai characters",
           "edge": [("th-TH-PremwadeeNeural", "Premwadee", "female"), ("th-TH-NiwatNeural", "Niwat", "male")]},
    "ja": {"name": "Tiếng Nhật", "short": "Nhật", "english": "Japanese", "google": "ja", "hymt": "日语",
           "unit": "char", "rate": 6.0, "unit_vi": "chữ", "unit_ai": "Japanese characters",
           "edge": [("ja-JP-NanamiNeural", "Nanami", "female"), ("ja-JP-KeitaNeural", "Keita", "male")]},
    "ko": {"name": "Tiếng Hàn", "short": "Hàn", "english": "Korean", "google": "ko", "hymt": "韩语",
           "unit": "char", "rate": 6.0, "unit_vi": "âm tiết", "unit_ai": "Korean syllables",
           "edge": [("ko-KR-SunHiNeural", "Sun-Hi", "female"), ("ko-KR-InJoonNeural", "InJoon", "male")]},
}
CHOICES = [(k, INFO[k]["name"]) for k in TARGETS]

_THAI = re.compile(r"[฀-๿]")
_HANGUL = re.compile(r"[가-힯ᄀ-ᇿ㄰-㆏]")
_KANA = re.compile(r"[぀-ヿㇰ-ㇿｦ-ﾟ]")
_HAN = re.compile(r"[㐀-鿿豈-﫿]")
_VI = re.compile(r"[ăâđêôơưĂÂĐÊÔƠƯàáảãạằắẳẵặầấẩẫậèéẻẽẹềếểễệìíỉĩịòóỏõọồốổỗộờớởỡợùúủũụừứửữựỳýỷỹỵ"
                 r"ÀÁẢÃẠẰẮẲẴẶẦẤẨẪẬÈÉẺẼẸỀẾỂỄỆÌÍỈĨỊÒÓỎÕỌỒỐỔỖỘỜỚỞỠỢÙÚỦŨỤỪỨỬỮỰỲÝỶỸỴ]")
_LATIN = re.compile(r"[A-Za-z]")


def has_han(text: str) -> bool:
    return bool(_HAN.search(text or ""))


def has_latin(text: str) -> bool:
    return bool(_LATIN.search(text or "") or _VI.search(text or ""))


def norm(code: str | None) -> str:
    return code if code in INFO else DEFAULT


def name(code: str | None) -> str:
    return INFO[norm(code)]["name"]


def detect(text: str, default: str = DEFAULT) -> str:
    """Tiếng của 1 câu theo chữ viết: Thái / Hàn / Nhật (có kana) / Việt (có dấu tiếng Việt) / Anh (chữ Latin không
    dấu); câu chỉ có chữ Hán (chưa dịch) hoặc không đoán được -> default."""
    t = text or ""
    if _THAI.search(t):
        return "th"
    if _HANGUL.search(t):
        return "ko"
    if _KANA.search(t):
        return "ja"
    if _HAN.search(t):
        return "ja" if default == "ja" else default
    if _VI.search(t):
        return "vi"
    if _LATIN.search(t):
        # "OK", "Hello", tên riêng trong câu Việt không dấu: dự án dịch sang tiếng Việt thì vẫn đọc giọng Việt
        return "en" if default != "vi" else "vi"
    return default


def units(text: str, code: str) -> int:
    """Độ dài lời để so với thời gian nói: số tiếng / từ (Việt, Anh) hoặc số chữ (Thái, Nhật, Hàn – không có dấu cách
    giữa các từ; dấu thanh / nguyên âm kết hợp của chữ Thái không tính riêng)."""
    t = text or ""
    if INFO[norm(code)]["unit"] == "word":
        return len([w for w in re.split(r"[\s,.;:!?…\-–—\"'()]+", t) if w])
    return sum(1 for c in t if unicodedata.category(c)[0] in "LN" and unicodedata.category(c) != "Mn")


def budget(duration: float, code: str, speedup: float = 1.0) -> int:
    """Độ dài lời tối đa nói vừa trong `duration` giây (cho phép tăng tốc nhẹ `speedup`)."""
    return max(2, int(duration * INFO[norm(code)]["rate"] * speedup))


def edge_voice(code: str, gender: str = "", index: int = 0) -> str:
    """Mã giọng Microsoft Edge của tiếng `code`: theo nam / nữ nếu biết, không thì xen kẽ theo `index`."""
    vs = INFO[norm(code)]["edge"]
    pool = [v for v in vs if v[2] == gender] or vs
    return "edge:" + pool[index % len(pool)][0]


def edge_lang(voice_id: str) -> str | None:
    """Tiếng của 1 giọng Edge ("edge:en-US-AriaNeural" -> "en")."""
    v = voice_id[5:] if voice_id.startswith("edge:") else voice_id
    code = v.split("-", 1)[0].lower()
    return code if code in INFO else None


def edge_gender(voice_id: str) -> str:
    v = voice_id[5:] if voice_id.startswith("edge:") else voice_id
    for info in INFO.values():
        for vid, _, g in info["edge"]:
            if vid == v:
                return g
    return ""


def subtitle_font(code: str, base_font: str) -> str:
    """Font phụ đề cho câu tiếng `code`: Việt / Anh dùng font đã chọn; Thái / Nhật / Hàn dùng font hệ thống có đủ chữ
    (font Latin như Be Vietnam Pro, Bangers không có chữ Thái / Nhật / Hàn)."""
    code = norm(code)
    if code in ("vi", "en"):
        return base_font
    if sys.platform == "win32":
        return {"th": "Leelawadee UI", "ja": "Yu Gothic", "ko": "Malgun Gothic"}[code]
    if sys.platform == "darwin":
        return {"th": "Thonburi", "ja": "Hiragino Sans", "ko": "Apple SD Gothic Neo"}[code]
    return {"th": "Loma", "ja": "Noto Sans CJK JP", "ko": "Noto Sans CJK KR"}[code]


def no_spaces(code: str) -> bool:
    """Tiếng viết liền không có dấu cách giữa các từ (ngắt dòng / karaoke theo từng chữ)."""
    return norm(code) in ("th", "ja")
