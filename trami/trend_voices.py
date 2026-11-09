"""20 giọng VieNeu theo trend (review phim, cô gái dễ thương, kể chuyện ma, loa phường, robot…) + 10 giọng 🎬 lồng
tiếng phim Hồng Kông ngày xưa (người dẫn truyện, nam / nữ chính, hiệp khách, sư phụ… – đọc chậm rãi, rõ chữ, ấm,
NGẮT NGHỈ rõ ở dấu phẩy / dấu chấm, biểu cảm hơn; đặt tên theo vai, không nhái giọng diễn viên lồng tiếng thật).

Mỗi giọng = 1 giọng dựng sẵn của VieNeu (đọc tiếng Việt chuẩn, chạy offline) + kiểu đọc riêng (độ ngẫu nhiên khi lấy
mẫu) + chuỗi hiệu ứng (trami/voicefx.py: cao độ, âm sắc, tốc độ, EQ, nén, vang, loa…). Mã giọng "vieneu:trend:<mã>".
Hiệu ứng áp sau khi VieNeu đọc xong từng câu (sau bước kiểm tra giọng hỏng) -> cả video 1 giọng như nhau; sửa mô tả 1
giọng ở đây thì `key` đổi -> các câu của giọng đó được đọc lại.

Không có file mẫu kèm theo: giọng gốc nằm sẵn trong gói VieNeu (voices_v3_turbo.json). Bản v3 Nano (bộ giọng khác)
-> lấy giọng đầu tiên cùng nam / nữ.
"""
from __future__ import annotations

import hashlib
import json

PREFIX = "vieneu:trend:"
ICON = "🔥"
ICON_HK = "🎬"

# kiểu đọc: "lively" = biểu cảm hơn (MC, hài), "steady" = đều, rõ (bản tin); không ghi = theo Cài đặt (Rõ ràng / Tự nhiên)
_LIVELY = {"temperature": 0.85, "top_k": 30, "top_p": 0.95}
_STEADY = {"temperature": 0.5, "top_k": 15, "top_p": 0.85}
# lồng tiếng phim HK: rõ chữ nhưng có lên xuống giọng ("_HK"), vai nhiều cảm xúc (nữ chính, tiểu thư, phản diện) thả hơn.
# VieNeu không có tham số cảm xúc -> cảm xúc = độ ngẫu nhiên khi đọc + giọng gốc kể chuyện; ngắt nghỉ = hiệu ứng "pause"
_HK = {"temperature": 0.75, "top_k": 25, "top_p": 0.93}
_HK_EMO = {"temperature": 0.85, "top_k": 30, "top_p": 0.95}
_HK_ROOM = [0.4, 0.05]      # phòng thu lồng tiếng: vang rất nhẹ, không nghe như hang

PRESETS: list[dict] = [
    {"id": "review_phim", "name": "Review phim – nam trầm cuốn", "gender": "male", "base": "Thiện Minh", "top": True,
     "use": "review / tóm tắt phim, kể chuyện kịch tính",
     "fx": {"tempo": 1.05, "pitch": -1.5, "formant": -1.2, "low": 3, "presence": 2, "comp": 0.5}},
    {"id": "review_nu", "name": "Review phim – nữ cuốn hút", "gender": "female", "base": "Mai Anh", "top": True,
     "use": "review phim, tóm tắt truyện",
     "fx": {"tempo": 1.05, "pitch": -0.5, "formant": -0.3, "low": 1, "presence": 2, "comp": 0.5}},
    {"id": "de_thuong", "name": "Cô gái dễ thương", "gender": "female", "base": "Trúc Ly", "top": True,
     "use": "video vui, vlog, nhân vật nữ trẻ, hoạt hình",
     "fx": {"tempo": 1.04, "pitch": 3.0, "formant": 1.5, "high": 2}},
    {"id": "chuyen_ma", "name": "Kể chuyện ma", "gender": "male", "base": "Thiền Tâm Đức", "top": True,
     "use": "truyện ma, bí ẩn, kinh dị",
     "fx": {"tempo": 0.9, "pitch": -2.0, "formant": -1.5, "whisper": 0.15, "low": 2, "high": -2,
            "reverb": [1.8, 0.28]}},
    {"id": "ngon_tinh", "name": "Đọc truyện ngôn tình", "gender": "female", "base": "Quỳnh Anh",
     "use": "truyện tình cảm, tâm sự, phim ngôn tình", "params": _LIVELY,
     "fx": {"tempo": 0.95, "pitch": 1.0, "formant": 0.5, "low": 1.5, "high": -1, "reverb": [0.8, 0.08]}},
    {"id": "mc_soi_dong", "name": "MC sôi động", "gender": "male", "base": "Hải Đăng", "top": True,
     "use": "giới thiệu sản phẩm, quảng cáo, game show", "params": _LIVELY,
     "fx": {"tempo": 1.1, "pitch": 1.0, "formant": 0.3, "presence": 3, "comp": 0.7}},
    {"id": "vlog_nhanh", "name": "Vlog nói nhanh", "gender": "female", "base": "Ngọc Huyền",
     "use": "vlog, video ngắn TikTok / Reels / Shorts",
     "fx": {"tempo": 1.15, "pitch": 1.0, "formant": 0.5, "presence": 2, "comp": 0.4}},
    {"id": "diem_tin", "name": "Điểm tin nhanh", "gender": "female", "base": "Thùy Dung",
     "use": "điểm tin, thông báo, video kiến thức", "params": _STEADY,
     "fx": {"tempo": 1.1, "presence": 2, "comp": 0.5}},
    {"id": "asmr", "name": "Thì thầm ASMR", "gender": "female", "base": "Đoan Trang",
     "use": "ASMR, kể chuyện đêm khuya, cảnh bí mật",
     "fx": {"tempo": 0.92, "whisper": 1.0, "low": 2, "band": [180, 9000]}},
    {"id": "mien_trung", "name": "Miền Trung ngọt ngào", "gender": "female", "base": "Ngọc Trân",
     "use": "nhân vật nữ miền Trung, du lịch, ẩm thực",
     "fx": {"tempo": 0.95, "pitch": 1.0, "formant": 0.5, "low": 1}},
    {"id": "mien_tay", "name": "Miền Tây mộc mạc", "gender": "male", "base": "Đức Trí",
     "use": "nhân vật nam miền Nam, đời thường, hài", "params": _LIVELY,
     "fx": {"tempo": 0.97, "low": 2, "comp": 0.3}},
    {"id": "be_gai", "name": "Bé gái", "gender": "female", "base": "Ngọc Huyền",
     "use": "nhân vật trẻ con, hoạt hình",
     "fx": {"pitch": 6.0, "formant": 3.5, "high": 1}},
    {"id": "ong_lao", "name": "Ông lão", "gender": "male", "base": "Thanh Bình",
     "use": "nhân vật ông già, thầy, trưởng làng",
     "fx": {"tempo": 0.9, "pitch": -1.0, "formant": -1.0, "vibrato": 35, "tremolo": 0.12, "whisper": 0.12,
            "high": -4}},
    {"id": "ba_ke_chuyen", "name": "Bà kể cổ tích", "gender": "female", "base": "Ngọc Linh",
     "use": "truyện cổ tích, nhân vật bà cụ",
     "fx": {"tempo": 0.9, "pitch": -2.0, "formant": -1.0, "vibrato": 30, "tremolo": 0.1, "whisper": 0.1,
            "high": -3}},
    {"id": "than_linh", "name": "Thần linh vọng", "gender": "male", "base": "Minh Đức",
     "use": "tiên ông, thần, giọng từ trên trời, cảnh hài",
     "fx": {"pitch": -3.0, "formant": -2.0, "low": 3, "chorus": 0.35, "echo": [0.28, 0.25, 2],
            "reverb": [2.4, 0.22]}},
    {"id": "quai_vat", "name": "Quái vật / phản diện", "gender": "male", "base": "Thái Sơn",
     "use": "phản diện, quái vật, ác ma",
     "fx": {"tempo": 0.95, "pitch": -5.0, "formant": -4.0, "low": 4, "drive": 0.15, "reverb": [0.9, 0.12]}},
    {"id": "soc_chuot", "name": "Sóc chuột", "gender": "female", "base": "Trúc Ly",
     "use": "video hài, nhân vật nhí nhố",
     "fx": {"tempo": 1.05, "pitch": 8.0}},
    {"id": "robot", "name": "Robot AI", "gender": "male", "base": "Quốc Tuấn",
     "use": "robot, trợ lý ảo, phim khoa học viễn tưởng",
     "fx": {"robot": 120, "chorus": 0.25, "presence": 4, "band": [120, 7500]}},
    {"id": "loa_phuong", "name": "Loa phường", "gender": "male", "base": "Phạm Tuyên",
     "use": "thông báo kiểu loa phát thanh, cảnh hài",
     "fx": {"band": [400, 3800], "presence": 5, "drive": 0.3, "comp": 0.6, "echo": [0.32, 0.3, 3],
            "reverb": [1.2, 0.12]}},
    {"id": "dien_thoai", "name": "Gọi điện thoại", "gender": "female", "base": "Mỹ Duyên",
     "use": "nhân vật nói qua điện thoại, tin nhắn thoại",
     "fx": {"band": [320, 3400], "drive": 0.2, "comp": 0.5, "noise": -42}},
    # --- 🎬 lồng tiếng phim Hồng Kông ngày xưa: chậm rãi, ấm (low +, high − nhẹ), rõ chữ (presence), giọng đều (nén
    # nhẹ), ngắt nghỉ ở dấu câu dài thêm 0,14–0,24 s (pause: thêm, lặng từ, tổng tối đa)
    {"id": "hk_dan_truyen", "name": "Phim HK – Người dẫn truyện, nam trầm ấm", "gender": "male", "base": "Thái Sơn",
     "group": "hk", "region": "giọng Nam", "use": "lời dẫn truyện, thuyết minh phim bộ, kể chuyện chậm rãi",
     "params": _HK,
     "fx": {"tempo": 0.95, "pause": [0.22, 0.14, 0.7], "pitch": -0.5, "formant": -0.4, "low": 2.5, "presence": 1.5,
            "high": -1, "comp": 0.35, "reverb": _HK_ROOM}},
    {"id": "hk_nam_chinh", "name": "Phim HK – Nam chính, ấm áp hào hoa", "gender": "male", "base": "Đức Trí",
     "group": "hk", "region": "giọng Nam", "use": "nam chính phim bộ, tình cảm, gia đình, hiện đại",
     "params": _HK_EMO,
     "fx": {"tempo": 0.97, "pause": [0.18, 0.14, 0.6], "low": 2, "presence": 2, "comp": 0.35, "reverb": _HK_ROOM}},
    {"id": "hk_nu_chinh", "name": "Phim HK – Nữ chính, dịu dàng tình cảm", "gender": "female", "base": "Thục Đoan",
     "group": "hk", "region": "giọng Nam", "use": "nữ chính, cảnh tình cảm, tâm sự, khóc",
     "params": _HK_EMO,
     "fx": {"tempo": 0.96, "pause": [0.2, 0.14, 0.6], "pitch": 0.5, "formant": 0.2, "low": 1.5, "high": -1,
            "comp": 0.3, "reverb": _HK_ROOM}},
    {"id": "hk_hiep_khach", "name": "Phim HK – Hiệp khách, nam rắn rỏi", "gender": "male", "base": "Minh Triết",
     "group": "hk", "region": "giọng Nam", "use": "kiếm hiệp, võ hiệp, cảnh đối đầu, thề thốt",
     "params": _HK,
     "fx": {"tempo": 0.98, "pause": [0.16, 0.14, 0.5], "pitch": -1.0, "formant": -0.6, "low": 2.5, "presence": 2.5,
            "comp": 0.45, "reverb": _HK_ROOM}},
    {"id": "hk_tieu_thu", "name": "Phim HK – Tiểu thư, nữ trẻ trong trẻo", "gender": "female", "base": "Kim Thanh",
     "group": "hk", "region": "giọng Nam", "use": "tiểu thư, cô gái trẻ, nhõng nhẽo, vui vẻ",
     "params": _HK_EMO,
     "fx": {"tempo": 0.98, "pause": [0.16, 0.14, 0.5], "pitch": 1.2, "formant": 0.6, "high": 1, "comp": 0.3,
            "reverb": _HK_ROOM}},
    {"id": "hk_su_phu", "name": "Phim HK – Sư phụ / lão gia, điềm đạm", "gender": "male", "base": "Thanh Bình",
     "group": "hk", "region": "giọng Bắc", "use": "sư phụ, lão gia, trưởng bối dạy bảo, cổ trang",
     "params": _HK,
     "fx": {"tempo": 0.94, "pause": [0.24, 0.14, 0.8], "pitch": -1.5, "formant": -1.0, "low": 2.5, "high": -2,
            "comp": 0.3, "reverb": [0.6, 0.07]}},
    {"id": "hk_phu_nhan", "name": "Phim HK – Phu nhân / người mẹ, đằm thắm", "gender": "female", "base": "Mỹ Duyên",
     "group": "hk", "region": "giọng Nam", "use": "phu nhân, người mẹ, bà chủ, cảnh gia đình",
     "params": _HK,
     "fx": {"tempo": 0.95, "pause": [0.2, 0.14, 0.6], "pitch": -0.8, "formant": -0.5, "low": 2, "high": -1,
            "comp": 0.3, "reverb": _HK_ROOM}},
    {"id": "hk_phan_dien", "name": "Phim HK – Phản diện, nam lạnh lùng", "gender": "male", "base": "Thiền Tâm Đức",
     "group": "hk", "region": "giọng Bắc", "use": "phản diện, đại ca, kẻ âm mưu, cảnh đe doạ",
     "params": _HK_EMO,
     "fx": {"tempo": 0.94, "pause": [0.22, 0.14, 0.7], "pitch": -2.0, "formant": -1.4, "low": 3, "presence": 1,
            "comp": 0.45, "reverb": [0.5, 0.06]}},
    {"id": "hk_tham_tu", "name": "Phim HK – Thám tử / cảnh sát, dứt khoát", "gender": "male", "base": "Xuân Vĩnh",
     "group": "hk", "region": "giọng Bắc", "use": "phim cảnh sát, hình sự, thám tử phá án, công sở",
     "params": _HK,
     "fx": {"tempo": 1.0, "pause": [0.14, 0.14, 0.5], "low": 1.5, "presence": 2.5, "comp": 0.45, "reverb": _HK_ROOM}},
    {"id": "hk_nu_ke", "name": "Phim HK – Nữ kể chuyện, truyền cảm", "gender": "female", "base": "Ngọc Linh",
     "group": "hk", "region": "giọng Bắc", "use": "lời dẫn nữ, hồi tưởng, kể chuyện tình cảm",
     "params": _HK,
     "fx": {"tempo": 0.95, "pause": [0.2, 0.14, 0.7], "low": 1.5, "presence": 1.5, "high": -1, "comp": 0.35,
            "reverb": [0.5, 0.06]}},
]
_BY_ID = {p["id"]: p for p in PRESETS}


def get(spec: str) -> dict | None:
    """Mô tả giọng theo mã "vieneu:trend:<mã>" (hoặc chỉ "<mã>")."""
    return _BY_ID.get(spec[len(PREFIX):] if spec.startswith(PREFIX) else spec)


def voices() -> list[dict]:
    """Cho danh sách chọn giọng: {"id", "name", "gender", "tip"} (tip = hợp với loại video nào)."""
    out = []
    for p in PRESETS:
        if p.get("group") == "hk":
            tip = (f"Hợp với: {p['use']}. Kiểu lồng tiếng phim Hồng Kông ngày xưa: đọc chậm rãi, rõ chữ, ngắt nghỉ ở "
                   f"dấu câu, biểu cảm. Giọng gốc VieNeu: {p['base']} ({p.get('region', '')})")
            out.append({"id": PREFIX + p["id"], "name": f"{ICON_HK} {p['name']} (VieNeu)", "gender": p["gender"],
                        "tip": tip})
        else:
            out.append({"id": PREFIX + p["id"], "name": f"{ICON} {p['name']} (VieNeu)", "gender": p["gender"],
                        "tip": f"Hợp với: {p['use']}. Giọng gốc VieNeu: {p['base']} + hiệu ứng"})
    return out


def base_voice(spec: str, presets: list[dict] | None = None) -> str:
    """Tên giọng dựng sẵn của VieNeu để đọc. presets: danh sách giọng của bản VieNeu đang dùng (tts.vieneu_presets) –
    thiếu giọng gốc (bản v3 Nano) thì lấy giọng đầu tiên cùng nam / nữ."""
    p = get(spec) or {}
    name = p.get("base", "")
    if presets:
        have = {v["id"][7:]: v.get("gender", "") for v in presets}
        if name not in have:
            name = next((n for n, g in have.items() if g == p.get("gender")), next(iter(have), name))
    return name


def params(spec: str, default: dict) -> dict:
    """Thông số lấy mẫu khi đọc: giọng có kiểu đọc riêng (MC, bản tin…) dùng của nó, không thì theo Cài đặt."""
    p = get(spec) or {}
    return dict(p["params"]) if p.get("params") else dict(default)


def key(spec: str) -> str:
    """Mã nội dung của giọng (giọng gốc + kiểu đọc + hiệu ứng) – đổi mô tả thì đọc lại các câu của giọng này."""
    p = get(spec) or {}
    raw = json.dumps({k: p.get(k) for k in ("base", "params", "fx")}, sort_keys=True, ensure_ascii=False)
    return "trend" + hashlib.md5(raw.encode()).hexdigest()[:10]


def process(spec: str, a, sr: int):
    """Áp hiệu ứng của giọng lên 1 câu VieNeu vừa đọc (mảng float mono)."""
    from . import voicefx

    return voicefx.apply(a, sr, (get(spec) or {}).get("fx"))
