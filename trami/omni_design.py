"""Giọng OmniVoice CHỌN SẴN – không cần file ghi âm mẫu.

OmniVoice tự tạo giọng theo một mô tả ngắn (tính năng "voice design", `generate(instruct=...)`). Mô tả chỉ được dùng
đúng các từ OmniVoice hiểu (sai 1 từ là báo lỗi):
  giới tính : male, female
  tuổi      : child, teenager, young adult, middle-aged, elderly
  cao độ    : very low pitch, low pitch, moderate pitch, high pitch, very high pitch
  kiểu      : whisper

Giọng tạo theo mô tả mỗi lần đọc lại ra 1 người khác -> mỗi giọng đọc 1 CÂU MẪU cố định (hạt ngẫu nhiên cố định) đúng 1
lần, lưu data/voices/design/, rồi mọi câu lồng tiếng NHÁI LẠI câu mẫu đó -> cả video giữ đúng 1 giọng.

10 giọng 🎬 lồng tiếng phim Hồng Kông ngày xưa (`STYLES`): mô tả không có từ chỉ cảm xúc -> cảm xúc + nhịp ngắt lấy từ
CÂU MẪU RIÊNG của từng vai (lời thoại có cảm xúc, nhiều dấu phẩy – OmniVoice nhái cả nhịp đọc của câu mẫu) + hiệu ứng
sau khi đọc (trami/voicefx.py: kéo dài chỗ ngắt nghỉ, ấm, rõ chữ, phòng thu nhẹ). Đặt tên theo vai, không nhái giọng
diễn viên lồng tiếng thật.
"""
from __future__ import annotations

import hashlib
import json
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


def anchor_text(lang: str = "vi", pid: str = "") -> str:
    """Câu mẫu của 1 giọng: giọng 🎬 phim HK có câu mẫu riêng (tiếng Việt), còn lại câu mẫu chung theo tiếng."""
    st = STYLES.get(_bare(pid)) if pid else None
    if st and lang == "vi":
        return st["anchor"]
    return ANCHOR_TEXTS.get(lang, ANCHOR_TEXT)


def _bare(pid: str) -> str:
    return pid[len(PREFIX):] if pid.startswith(PREFIX) else pid

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
    # 🎬 lồng tiếng phim Hồng Kông ngày xưa (câu mẫu + hiệu ứng riêng ở STYLES)
    ("hk_dan_truyen", "Phim HK – Người dẫn truyện, nam trầm ấm", "male", "male, middle-aged, low pitch", 1401, False),
    ("hk_nam_chinh", "Phim HK – Nam chính, ấm áp hào hoa", "male", "male, young adult, moderate pitch", 1402, False),
    ("hk_nu_chinh", "Phim HK – Nữ chính, dịu dàng tình cảm", "female", "female, young adult, moderate pitch", 1403,
     False),
    ("hk_hiep_khach", "Phim HK – Hiệp khách, nam rắn rỏi", "male", "male, young adult, low pitch", 1404, False),
    ("hk_tieu_thu", "Phim HK – Tiểu thư, nữ trẻ trong trẻo", "female", "female, young adult, high pitch", 1405, False),
    ("hk_su_phu", "Phim HK – Sư phụ / lão gia, điềm đạm", "male", "male, elderly, low pitch", 1406, False),
    ("hk_phu_nhan", "Phim HK – Phu nhân / người mẹ, đằm thắm", "female", "female, middle-aged, moderate pitch", 1407,
     False),
    ("hk_phan_dien", "Phim HK – Phản diện, nam lạnh lùng", "male", "male, middle-aged, very low pitch", 1408, False),
    ("hk_tham_tu", "Phim HK – Thám tử / cảnh sát, dứt khoát", "male", "male, middle-aged, moderate pitch", 1409,
     False),
    ("hk_nu_ke", "Phim HK – Nữ kể chuyện, truyền cảm", "female", "female, middle-aged, low pitch", 1410, False),
]
_BY_ID = {p[0]: p for p in PRESETS}

ICON_HK = "🎬"
_ROOM = [0.4, 0.05]        # phòng thu lồng tiếng: vang rất nhẹ
# câu mẫu có cảm xúc + dấu phẩy (ngắt nghỉ) -> OmniVoice nhái theo; fx áp lên từng câu đã đọc (không áp lên câu mẫu)
STYLES: dict[str, dict] = {
    "hk_dan_truyen": {
        "use": "lời dẫn truyện, thuyết minh phim bộ, kể chuyện chậm rãi",
        "anchor": "Năm ấy, trời vừa vào thu. Giữa chốn giang hồ đầy sóng gió, có một người, đã lặng lẽ ra đi, "
                  "mà không để lại một lời nào.",
        "fx": {"tempo": 0.97, "pause": [0.22, 0.14, 0.7], "low": 2.5, "presence": 1.5, "high": -1, "comp": 0.35,
               "reverb": _ROOM}},
    "hk_nam_chinh": {
        "use": "nam chính phim bộ, tình cảm, gia đình, hiện đại",
        "anchor": "Em đừng lo, có anh ở đây rồi. Dù sau này có chuyện gì xảy ra, anh cũng sẽ không bao giờ, "
                  "để em một mình nữa đâu.",
        "fx": {"pause": [0.18, 0.14, 0.6], "low": 2, "presence": 2, "comp": 0.35, "reverb": _ROOM}},
    "hk_nu_chinh": {
        "use": "nữ chính, cảnh tình cảm, tâm sự, khóc",
        "anchor": "Anh à, em biết anh có nỗi khổ riêng. Nhưng mà, em chỉ mong, anh đừng giấu em nữa, "
                  "được không anh?",
        "fx": {"tempo": 0.98, "pause": [0.2, 0.14, 0.6], "low": 1.5, "high": -1, "comp": 0.3, "reverb": _ROOM}},
    "hk_hiep_khach": {
        "use": "kiếm hiệp, võ hiệp, cảnh đối đầu, thề thốt",
        "anchor": "Hôm nay ta đến đây, không phải để gây chuyện. Ta chỉ muốn đòi lại công bằng, "
                  "cho những người đã chết oan!",
        "fx": {"pause": [0.16, 0.14, 0.5], "low": 2.5, "presence": 2.5, "comp": 0.45, "reverb": _ROOM}},
    "hk_tieu_thu": {
        "use": "tiểu thư, cô gái trẻ, nhõng nhẽo, vui vẻ",
        "anchor": "Cha à, con không muốn lấy người đó đâu! Con, con đã có người trong lòng rồi, "
                  "cha đừng ép con nữa mà.",
        "fx": {"pause": [0.16, 0.14, 0.5], "high": 1, "comp": 0.3, "reverb": _ROOM}},
    "hk_su_phu": {
        "use": "sư phụ, lão gia, trưởng bối dạy bảo, cổ trang",
        "anchor": "Con à, võ công cao đến đâu, cũng không bằng một tấm lòng ngay thẳng. Nhớ lấy lời ta, "
                  "đừng bao giờ quên.",
        "fx": {"tempo": 0.95, "pause": [0.24, 0.14, 0.8], "low": 2.5, "high": -2, "comp": 0.3,
               "reverb": [0.6, 0.07]}},
    "hk_phu_nhan": {
        "use": "phu nhân, người mẹ, bà chủ, cảnh gia đình",
        "anchor": "Con trai à, mẹ không trách con đâu. Chỉ là, mẹ già rồi, mẹ chỉ mong cả nhà mình, "
                  "được bình an mà thôi.",
        "fx": {"tempo": 0.97, "pause": [0.2, 0.14, 0.6], "low": 2, "high": -1, "comp": 0.3, "reverb": _ROOM}},
    "hk_phan_dien": {
        "use": "phản diện, đại ca, kẻ âm mưu, cảnh đe doạ",
        "anchor": "Ngươi nghĩ, ngươi có thể thoát khỏi tay ta sao? Đã quá muộn rồi. Từ hôm nay, "
                  "mọi thứ ở đây, đều là của ta.",
        "fx": {"tempo": 0.96, "pause": [0.22, 0.14, 0.7], "low": 3, "presence": 1, "comp": 0.45,
               "reverb": [0.5, 0.06]}},
    "hk_tham_tu": {
        "use": "phim cảnh sát, hình sự, thám tử phá án, công sở",
        "anchor": "Mọi người bình tĩnh, nghe tôi nói. Hung thủ, vẫn còn ở trong căn phòng này. "
                  "Và tôi biết, người đó là ai.",
        "fx": {"pause": [0.14, 0.14, 0.5], "low": 1.5, "presence": 2.5, "comp": 0.45, "reverb": _ROOM}},
    "hk_nu_ke": {
        "use": "lời dẫn nữ, hồi tưởng, kể chuyện tình cảm",
        "anchor": "Có những chuyện, dù đã qua rất lâu rồi, nhưng mỗi lần nhớ lại, lòng người vẫn không khỏi "
                  "bồi hồi, xao xuyến.",
        "fx": {"tempo": 0.97, "pause": [0.2, 0.14, 0.7], "low": 1.5, "presence": 1.5, "high": -1, "comp": 0.35,
               "reverb": [0.5, 0.06]}},
}


def get(pid: str) -> dict | None:
    p = _BY_ID.get(_bare(pid))
    if not p:
        return None
    st = STYLES.get(p[0]) or {}
    return {"id": p[0], "name": p[1], "gender": p[2], "instruct": p[3], "seed": p[4], "featured": p[5],
            "fx": st.get("fx"), "use": st.get("use", ""), "group": "hk" if st else ""}


def voices() -> list[dict]:
    """Cho danh sách giọng: tiêu biểu (⭐) đứng đầu; giọng 🎬 phim HK có "tip" (hợp với vai nào)."""
    items = sorted(PRESETS, key=lambda p: not p[5])
    out = []
    for p in items:
        st = STYLES.get(p[0])
        if st:
            out.append({"id": PREFIX + p[0], "name": f"{ICON_HK} {p[1]} – chọn sẵn (OmniVoice)", "gender": p[2],
                        "tip": f"Hợp với: {st['use']}. Kiểu lồng tiếng phim Hồng Kông ngày xưa: đọc chậm rãi, rõ "
                               "chữ, ngắt nghỉ ở dấu câu, biểu cảm (theo câu mẫu riêng của vai)"})
        else:
            out.append({"id": PREFIX + p[0], "name": f"{'⭐ ' if p[5] else ''}{p[1]} – chọn sẵn (OmniVoice)",
                        "gender": p[2]})
    return out


def key(pid: str, repo: str, lang: str = "vi") -> str:
    """Mã của câu mẫu: đổi mô tả / hạt / mô hình / câu mẫu -> tạo câu mẫu mới (bản lồng tiếng cũng đọc lại)."""
    p = get(pid)
    raw = f"{p['instruct']}|{p['seed']}|{repo}|{anchor_text(lang, pid)}" if p else pid
    return hashlib.md5(raw.encode()).hexdigest()[:10]


def fx_key(pid: str) -> str:
    """Mã hiệu ứng sau khi đọc ("" = không có) – đổi hiệu ứng thì các câu của giọng đó đọc lại (câu mẫu giữ)."""
    fx = (get(pid) or {}).get("fx")
    if not fx:
        return ""
    return "|fx" + hashlib.md5(json.dumps(fx, sort_keys=True).encode()).hexdigest()[:10]


def process(pid: str, a, sr: int):
    """Áp hiệu ứng của giọng (🎬 phim HK: ngắt nghỉ, ấm, rõ chữ) lên 1 câu OmniVoice vừa đọc (mảng float mono)."""
    from . import voicefx

    fx = (get(pid) or {}).get("fx")
    return voicefx.apply(a, sr, fx) if fx else a


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
    text = anchor_text(lang, pid)
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
