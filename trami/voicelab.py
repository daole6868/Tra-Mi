"""Tạo giọng (2.4.22, đợt 1): "giọng của tôi" = 1 CÔNG THỨC giọng, dùng cho mọi video / mọi người nói.

Công thức = nguồn giọng (bộ đọc + giọng nền + thông số riêng bộ đọc) + phần CHỈNH SAU KHI ĐỌC (cao độ, âm sắc, tốc độ,
ấm, rõ chữ, sáng, nén, vang phòng, màu đặc biệt – trami/voicefx.py, chỉ numpy). Mã giọng "my:<id>".

Khi đọc: bộ đọc đọc bằng giọng nền như bình thường (file "thô" nằm chung kho đệm work/tts, cùng cách đặt tên với giọng
nền -> dùng lại được), rồi áp phần chỉnh -> file cuối `tts_<mã thô>_m<mã chỉnh>.wav`. Sửa phần chỉnh = chỉ áp lại lên file
thô đã có (nhanh, không đọc lại); đổi giọng nền / bộ đọc / thông số bộ đọc = đọc lại.

Lưu ở data/voices/my/index.json (danh sách công thức), đệm nghe thử ở data/voices/my/cache/ (tự dọn, giữ ≤ CACHE_KEEP).
Cao độ chặn ±PITCH_MAX nửa cung, âm sắc ±TIMBRE_MAX % (quá thì nghe giọng máy).
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
import re
import shutil
import time
import uuid
from pathlib import Path

from .config import DATA_DIR

PREFIX = "my:"
DIR = DATA_DIR / "voices" / "my"
INDEX = DIR / "index.json"
CACHE = DIR / "cache"
CACHE_KEEP = 80
EXT = ".subvoice.json"          # file xuất / nhập 1 giọng (chỉ công thức – không có file âm thanh)

PITCH_MAX = 4.0
TIMBRE_MAX = 15.0
TEMPO_LO, TEMPO_HI = 0.85, 1.20
DB_MAX = 6.0

ENGINES = [("edge", "Edge"), ("vieneu", "VieNeu"), ("omni", "OmniVoice"), ("vox", "VoxCPM2"), ("korva", "KorvaTTS")]
ENGINE_LABEL = dict(ENGINES)
ENGINE_NOTE = {
    "edge": "2 giọng tiếng Việt (Hoài My, Nam Minh). Cao độ / tốc độ chỉnh ngay trong bộ đọc – tự nhiên nhất.",
    "vieneu": "25 giọng dựng sẵn + 6 giọng thu âm + giọng mẫu của bạn. Ô nghe thử nhận [cười] [thở dài].",
    "omni": "33 giọng chọn sẵn (có 10 vai phim HK) + giọng mẫu của bạn. Đổi biến thể = cùng kiểu, người đọc khác.",
    "vox": "18 giọng (6 thuyết minh) + giọng mẫu của bạn. Có cảm xúc thật cho câu thử.",
    "korva": "10 giọng, chạy CPU, chỉ tiếng Việt.",
}
ENGINE_TIME = {"edge": "~1 giây", "vieneu": "~1–2 giây", "omni": "~2–4 giây", "vox": "~5–20 giây", "korva": "~1 giây"}
ENGINE_DEFAULT = {"edge": {"rate": 0, "pitch": 0}, "vieneu": {"style": "clear"}, "omni": {"variant": 0},
                  "vox": {"quality": "normal", "cfg": 2.0}, "korva": {"quality": "normal", "speed": 1.05}}
VN_STYLES = [("clear", "Rõ ràng"), ("natural", "Tự nhiên"), ("lively", "Biểu cảm")]
QUALITIES = {"vox": [("fast", "Nhanh"), ("normal", "Chuẩn"), ("best", "Kỹ")],
             "korva": [("fast", "Nhanh"), ("normal", "Vừa"), ("best", "Tốt nhất")]}

ROOMS = [("none", "Không vang"), ("small", "Phòng nhỏ"), ("hall", "Hội trường"), ("cave", "Hang động")]
ROOM_FX = {"small": [0.5, 0.1], "hall": [1.4, 0.2], "cave": [2.6, 0.3]}
COLORS = [("none", "Không"), ("hoarse", "Khàn"), ("whisper", "Thì thầm"), ("phone", "Qua điện thoại"),
          ("speaker", "Loa phường"), ("god", "Vọng (thần linh)"), ("robot", "Robot")]
COLOR_FX = {
    "hoarse": {"drive": 0.35, "whisper": 0.18},
    "whisper": {"whisper": 1.0, "low": 2, "band": [180, 9000]},
    "phone": {"band": [320, 3400], "drive": 0.2, "comp": 0.5, "noise": -42},
    "speaker": {"band": [400, 3800], "presence": 5, "drive": 0.3, "comp": 0.6, "echo": [0.32, 0.3, 3],
                "reverb": [1.2, 0.12]},
    "god": {"chorus": 0.35, "echo": [0.28, 0.25, 2], "reverb": [2.4, 0.22]},
    "robot": {"robot": 120, "chorus": 0.25, "presence": 4, "band": [120, 7500]},
}

# phần chỉnh sau khi đọc (giá trị trên giao diện): cao độ nửa cung, âm sắc %, tốc độ ×, ấm / rõ chữ / sáng dB, nén %
ADJ_DEFAULT = {"pitch": 0.0, "timbre": 0.0, "tempo": 1.0, "warm": 0.0, "clear": 0.0, "bright": 0.0, "comp": 0,
               "room": "none", "color": "none"}
PRESETS = [   # mẫu nhanh – bấm là áp vào phần chỉnh, chỉnh tiếp được
    ("Thuyết minh nhanh", {"pitch": 0.5, "tempo": 1.08, "clear": 3, "comp": 40}),
    ("Review phim", {"pitch": -1.5, "timbre": -6, "tempo": 1.05, "warm": 3, "clear": 2, "comp": 50}),
    ("Kể chuyện ma", {"pitch": -2, "timbre": -8, "tempo": 0.9, "warm": 2, "bright": -2, "room": "hall"}),
    ("Ông lão", {"pitch": -2.5, "timbre": -6, "tempo": 0.9, "warm": 2, "color": "hoarse"}),
    ("Cô bé", {"pitch": 3.5, "timbre": 12, "tempo": 1.04, "bright": 2}),
    ("MC sôi động", {"pitch": 1, "tempo": 1.1, "clear": 3, "comp": 70}),
    ("Phản diện", {"pitch": -3, "timbre": -10, "tempo": 0.95, "warm": 3, "color": "hoarse", "room": "small"}),
    ("Thì thầm ASMR", {"tempo": 0.92, "warm": 2, "color": "whisper"}),
]
SAMPLES = [   # câu mẫu để nghe thử
    ("Thoại thường", "Hôm nay trời đẹp quá, mình ra ngoài đi dạo một chút nhé."),
    ("Câu hỏi", "Sao giờ này cậu mới về? Có chuyện gì xảy ra à?"),
    ("Quát to", "Đứng lại! Ngươi tưởng chạy được khỏi đây sao?"),
    ("Thì thầm", "Nhỏ giọng thôi, có người đang nghe lén đấy."),
    ("Số & tên riêng", "Ngày 15 tháng 8 năm 2024, Lâm Phong mang theo 300 lượng bạc rời khỏi Thiên Kiếm Tông."),
    ("Đoạn 30 giây", "Ba năm trước, hắn chỉ là một tên đệ tử tạp dịch, ngày ngày gánh nước bổ củi, bị người khác coi "
                     "thường. Không ai ngờ rằng, chỉ sau một đêm, hắn nhặt được một chiếc nhẫn cổ dưới đáy vực. Từ đó, "
                     "con đường tu tiên của hắn rẽ sang một hướng hoàn toàn khác. Người từng khinh thường hắn, nay phải "
                     "cúi đầu. Kẻ từng hãm hại hắn, nay phải run sợ. Và cả Thiên Kiếm Tông, cũng phải nhìn hắn bằng "
                     "con mắt khác."),
]
DEFAULT_TEXT = ("Ba năm trước, hắn chỉ là một tên đệ tử tạp dịch. Không ai ngờ rằng, chỉ sau một đêm, cả Thiên Kiếm "
                "Tông đều phải cúi đầu trước hắn!")
RATE_TEXTS = [   # 3 câu chuẩn để đo tốc độ đọc khi lưu
    "Hôm nay trời đẹp quá, mình ra ngoài đi dạo một chút nhé.",
    "Ba năm trước, hắn chỉ là một tên đệ tử tạp dịch trong tông môn.",
    "Nếu ngươi còn dám bước thêm một bước nữa, đừng trách ta không khách khí.",
]
MAX_TEXT = 900    # ~1 phút đọc


# ------------------------------------------------------------------ lưu / đọc công thức

def _load() -> list[dict]:
    try:
        data = json.loads(INDEX.read_text("utf-8"))
        return [v for v in data if isinstance(v, dict) and v.get("id") and v.get("base")]
    except Exception:  # noqa: BLE001
        return []


def _save(items: list[dict]) -> None:
    DIR.mkdir(parents=True, exist_ok=True)
    tmp = INDEX.with_suffix(".tmp")
    tmp.write_text(json.dumps(items, ensure_ascii=False, indent=1), "utf-8")
    tmp.replace(INDEX)


def list_all() -> list[dict]:
    return _load()


def bare(vid: str) -> str:
    return vid[len(PREFIX):] if vid.startswith(PREFIX) else vid


def get(vid: str) -> dict | None:
    v = bare(vid or "")
    return next((r for r in _load() if r["id"] == v), None)


def engine_of(base: str) -> str:
    """Bộ đọc của 1 mã giọng nền."""
    b = base or ""
    if b.startswith("edge:"):
        return "edge"
    if b.startswith(("vieneu:", "clone")):
        return "vieneu"
    if b.startswith(("omni:", "omnid:", "omniclone")):
        return "omni"
    if b.startswith(("vox:", "voxd:", "voxclone")):
        return "vox"
    if b.startswith("korva:"):
        return "korva"
    return "edge"


def new_record(base: str = "edge:vi-VN-HoaiMyNeural", name: str = "", gender: str = "") -> dict:
    eng = engine_of(base)
    return {"id": "", "name": name or "Giọng mới", "gender": gender, "use": "", "base": base,
            "eng": dict(ENGINE_DEFAULT.get(eng, {})), "adj": dict(ADJ_DEFAULT), "rate": None}


def _unique_name(name: str, skip: str = "") -> str:
    names = {r["name"] for r in _load() if r["id"] != skip}
    if name not in names:
        return name
    k = 2
    while f"{name} ({k})" in names:
        k += 1
    return f"{name} ({k})"


def clean(rec: dict) -> dict:
    """Chuẩn hoá 1 công thức (giá trị trong khoảng cho phép, thiếu khoá -> mặc định)."""
    r = copy.deepcopy(rec or {})
    r["name"] = (str(r.get("name") or "").strip() or "Giọng mới")[:80]
    r["gender"] = r.get("gender") if r.get("gender") in ("male", "female") else ""
    r["use"] = str(r.get("use") or "").strip()[:160]
    base = str(r.get("base") or "edge:vi-VN-HoaiMyNeural")
    if base.startswith(PREFIX):
        base = "edge:vi-VN-HoaiMyNeural"
    r["base"] = base
    eng = engine_of(base)
    e = dict(ENGINE_DEFAULT.get(eng, {}))
    e.update({k: v for k, v in (r.get("eng") or {}).items() if k in e})
    if eng == "edge":
        e["rate"] = int(max(-30, min(30, round(float(e.get("rate") or 0)))))
        e["pitch"] = int(max(-30, min(30, round(float(e.get("pitch") or 0)))))
    elif eng == "vieneu":
        e["style"] = e["style"] if e.get("style") in dict(VN_STYLES) else "clear"
    elif eng == "omni":
        e["variant"] = int(max(0, min(99, int(e.get("variant") or 0))))
    elif eng == "vox":
        e["quality"] = e["quality"] if e.get("quality") in dict(QUALITIES["vox"]) else "normal"
        e["cfg"] = round(max(1.0, min(3.0, float(e.get("cfg") or 2.0))), 2)
    elif eng == "korva":
        e["quality"] = e["quality"] if e.get("quality") in dict(QUALITIES["korva"]) else "normal"
        e["speed"] = round(max(0.8, min(1.3, float(e.get("speed") or 1.05))), 2)
    r["eng"] = e
    a = dict(ADJ_DEFAULT)
    a.update({k: v for k, v in (r.get("adj") or {}).items() if k in a})
    try:
        a["pitch"] = round(max(-PITCH_MAX, min(PITCH_MAX, float(a["pitch"]))) * 2) / 2
        a["timbre"] = float(round(max(-TIMBRE_MAX, min(TIMBRE_MAX, float(a["timbre"])))))
        a["tempo"] = round(max(TEMPO_LO, min(TEMPO_HI, float(a["tempo"]))), 2)
        for k in ("warm", "clear", "bright"):
            a[k] = float(round(max(-DB_MAX, min(DB_MAX, float(a[k])))))
        a["comp"] = int(max(0, min(100, round(float(a["comp"])))))
    except (TypeError, ValueError):
        a = dict(ADJ_DEFAULT)
    a["room"] = a["room"] if a.get("room") in dict(ROOMS) else "none"
    a["color"] = a["color"] if a.get("color") in dict(COLORS) else "none"
    r["adj"] = a
    try:
        r["rate"] = round(float(r["rate"]), 1) if r.get("rate") else None
    except (TypeError, ValueError):
        r["rate"] = None
    return r


def save(rec: dict, new: bool = False) -> dict:
    """Lưu công thức (new / chưa có id -> giọng mới). Trả lại bản đã lưu."""
    r = clean(rec)
    items = _load()
    if new or not rec.get("id") or not any(x["id"] == rec.get("id") for x in items):
        r["id"] = uuid.uuid4().hex[:8]
        r["created"] = time.time()
        r["name"] = _unique_name(r["name"])
        items.append(r)
    else:
        r["id"] = rec["id"]
        r["name"] = _unique_name(r["name"], r["id"])
        old = next(x for x in items if x["id"] == r["id"])
        r["created"] = old.get("created", time.time())
        items[items.index(old)] = r
    r["updated"] = time.time()
    _save(items)
    return r


def delete(vid: str) -> bool:
    items = _load()
    left = [r for r in items if r["id"] != bare(vid)]
    if len(left) == len(items):
        return False
    _save(left)
    return True


def duplicate(vid: str) -> dict | None:
    r = get(vid)
    if not r:
        return None
    c = copy.deepcopy(r)
    c["name"] = r["name"] + " – bản sao"
    return save(c, new=True)


def export_file(vid: str, path: str | Path) -> Path:
    r = get(vid)
    if not r:
        raise ValueError("Giọng này không còn")
    data = {k: r.get(k) for k in ("name", "gender", "use", "base", "eng", "adj", "rate")}
    data["subvideo_voice"] = 1
    p = Path(path)
    p.write_text(json.dumps(data, ensure_ascii=False, indent=1), "utf-8")
    return p


def import_file(path: str | Path) -> tuple[dict, str]:
    """Nhập 1 file giọng (.subvoice.json). Trả (giọng đã lưu, ghi chú – vd giọng nền là giọng mẫu không có trên máy)."""
    try:
        data = json.loads(Path(path).read_text("utf-8"))
    except Exception as e:  # noqa: BLE001
        raise ValueError(f"Không đọc được file giọng: {e}") from None
    if not isinstance(data, dict) or not data.get("base"):
        raise ValueError("File này không phải file giọng của Subvideo (.subvoice.json)")
    r = save({k: data.get(k) for k in ("name", "gender", "use", "base", "eng", "adj", "rate")}, new=True)
    note = ""
    if r["base"].startswith(("vieneu:lib:", "omni:", "vox:")) and not _base_known(r["base"]):
        note = "giọng nền là giọng mẫu ghi âm của máy khác – chọn lại giọng nền ở mục 1"
    return r, note


def _base_known(base: str) -> bool:
    from . import voices as vlib

    for pre in ("vieneu:lib:", "omni:", "vox:"):
        if base.startswith(pre):
            return bool(vlib.get(base[len(pre):]))
    return True


# ------------------------------------------------------------------ công thức -> hiệu ứng / thông số bộ đọc

def fx_of(rec: dict) -> dict:
    """Phần chỉnh trên giao diện -> chuỗi hiệu ứng voicefx.apply. Không chỉnh gì -> {}."""
    a = clean(rec)["adj"]
    fx: dict = {}
    if abs(a["tempo"] - 1.0) > 1e-3:
        fx["tempo"] = a["tempo"]
    if a["pitch"]:
        fx["pitch"] = a["pitch"]
    if a["timbre"] or a["pitch"]:
        # âm sắc ĐỘC LẬP với cao độ: chỉ kéo cao độ thì giữ nguyên âm sắc (đỡ giọng chuột chít / giọng máy)
        fx["formant"] = round(12 * math.log2(1 + a["timbre"] / 100.0), 3)
    for k_ui, k_fx in (("warm", "low"), ("clear", "presence"), ("bright", "high")):
        if a[k_ui]:
            fx[k_fx] = a[k_ui]
    if a["comp"]:
        fx["comp"] = round(a["comp"] / 100.0, 2)
    if a["room"] in ROOM_FX:
        fx["reverb"] = list(ROOM_FX[a["room"]])
    for k, v in (COLOR_FX.get(a["color"]) or {}).items():
        if k in ("low", "high", "presence"):
            fx[k] = round(fx.get(k, 0) + v, 2)
        elif k == "comp":
            fx[k] = max(fx.get(k, 0), v)
        else:
            fx[k] = copy.deepcopy(v)       # màu đặc biệt có vang riêng -> thay vang phòng
    return fx


def fx_key(rec_or_fx: dict) -> str:
    fx = rec_or_fx if "adj" not in rec_or_fx else fx_of(rec_or_fx)
    if not fx:
        return ""
    return hashlib.md5(json.dumps(fx, sort_keys=True).encode()).hexdigest()[:10]


def overlay(settings: dict | None, rec: dict) -> dict:
    """Cài đặt riêng của giọng: kiểu đọc VieNeu, chất lượng / bám mẫu VoxCPM2, chất lượng / tốc độ KorvaTTS."""
    st = dict(settings or {})
    r = clean(rec)
    e, eng = r["eng"], engine_of(r["base"])
    if eng == "vieneu":
        st["vieneu_style"] = e["style"]
    elif eng == "vox":
        st["voxcpm_quality"] = e["quality"]
        st["voxcpm_cfg"] = e["cfg"]
    elif eng == "korva":
        st["korva_quality"] = e["quality"]
        st["korva_speed"] = e["speed"]
    return st


def _num(s: str, unit: str) -> float:
    try:
        return float(str(s).strip().rstrip(unit).rstrip("%") or 0)
    except ValueError:
        return 0.0


def edge_args(rec: dict, rate: str = "+0%", pitch: str = "+0Hz") -> tuple[str, str]:
    """Tốc độ / cao độ bộ đọc Edge của giọng, cộng với "Tốc độ đọc" chung của dự án."""
    e = clean(rec)["eng"]
    if engine_of(rec.get("base", "")) != "edge":
        return rate, pitch
    tot = (1 + _num(rate, "%") / 100.0) * (1 + e.get("rate", 0) / 100.0) - 1
    hz = _num(pitch, "Hz") + e.get("pitch", 0)
    return f"{round(tot * 100):+d}%", f"{round(hz):+d}Hz"


def variant(rec: dict) -> int:
    r = clean(rec)
    return r["eng"].get("variant", 0) if engine_of(r["base"]) == "omni" else 0


def source_key(rec: dict) -> str:
    """Mã nguồn giọng (giọng nền + thông số bộ đọc) – đổi thì phải đọc lại."""
    r = clean(rec)
    return hashlib.md5(json.dumps([r["base"], r["eng"]], sort_keys=True).encode()).hexdigest()[:10]


def voices() -> list[dict]:
    """Cho mọi ô chọn giọng: nhóm ⭐ Giọng của tôi (đứng đầu)."""
    out = []
    for r in _load():
        eng = ENGINE_LABEL.get(engine_of(r["base"]), "")
        tip = describe(r)
        if r.get("use"):
            tip = f"Hợp với: {r['use']}. " + tip
        out.append({"id": PREFIX + r["id"], "name": f"⭐ {r['name']} – giọng của tôi ({eng})",
                    "gender": r.get("gender", ""), "tip": tip})
    return out


def describe(rec: dict) -> str:
    """Dòng tóm tắt: "VieNeu · nữ · 4,8 tiếng/giây · cao độ +1"."""
    r = clean(rec)
    a = r["adj"]
    parts = [ENGINE_LABEL.get(engine_of(r["base"]), "")]
    if r["gender"]:
        parts.append("nam" if r["gender"] == "male" else "nữ")
    if r.get("rate"):
        parts.append(f"{r['rate']:.1f}".replace(".", ",") + " tiếng/giây")
    if a["pitch"]:
        parts.append(f"cao độ {a['pitch']:+g}".replace(".", ","))
    if a["timbre"]:
        parts.append(f"âm sắc {a['timbre']:+g} %")
    if abs(a["tempo"] - 1) > 1e-3:
        parts.append(f"×{a['tempo']:.2f}".replace(".", ","))
    if a["room"] != "none":
        parts.append(dict(ROOMS)[a["room"]].lower())
    if a["color"] != "none":
        parts.append(dict(COLORS)[a["color"]].lower())
    return " · ".join(p for p in parts if p)


def adj_text(rec: dict) -> str:
    """Tóm tắt ngắn cho các bản vừa nghe: "cao độ +1 · âm sắc +4 % · ×1,05 · rõ chữ"."""
    a = clean(rec)["adj"]
    out = []
    if a["pitch"]:
        out.append(f"cao độ {a['pitch']:+g}".replace(".", ","))
    if a["timbre"]:
        out.append(f"âm sắc {a['timbre']:+g} %")
    if abs(a["tempo"] - 1) > 1e-3:
        out.append(f"×{a['tempo']:.2f}".replace(".", ","))
    for k, nm in (("warm", "ấm"), ("clear", "rõ chữ"), ("bright", "sáng")):
        if a[k]:
            out.append(nm)
    if a["comp"]:
        out.append("nén")
    if a["room"] != "none":
        out.append(dict(ROOMS)[a["room"]].lower())
    if a["color"] != "none":
        out.append(dict(COLORS)[a["color"]].lower())
    return " · ".join(out) or "gốc"


# ------------------------------------------------------------------ áp phần chỉnh lên file đã đọc

def apply_file(raw: str | Path, fx: dict, out: str | Path) -> Path:
    """Đọc file thô (wav / mp3 bất kỳ) -> 24 kHz mono -> hiệu ứng -> ghi out (.wav)."""
    from . import tts, voicefx

    p = Path(raw)
    a = tts._read_wav24(p) if p.suffix.lower() == ".wav" else None
    if a is None:
        if p.suffix.lower() == ".wav":
            try:
                a, sr = tts.read_wav(p)
                if sr != tts.SR:
                    a = voicefx.resample_len(a, int(round(len(a) * tts.SR / sr)))
            except Exception:  # noqa: BLE001
                a = tts._decode(p)
        else:
            a = tts._decode(p)
    if not len(a):
        raise RuntimeError("File giọng đã đọc không có tiếng")
    y = voicefx.apply(a, tts.SR, fx) if fx else a
    out = Path(out)
    tmp = out.with_name(out.stem + ".part.wav")
    tts.write_wav(tmp, y, tts.SR)
    tmp.replace(out)
    return out


def final_path(raw: str | Path, rec: dict) -> Path:
    """File cuối (đã chỉnh) cạnh file thô."""
    p = Path(raw)
    return p.with_name(f"{p.stem}_m{fx_key(rec)}.wav")


# ------------------------------------------------------------------ nghe thử trong trang Tạo giọng

def _clean_cache() -> None:
    try:
        fs = sorted((f for f in CACHE.iterdir() if f.is_file()), key=lambda f: f.stat().st_mtime)
    except OSError:
        return
    for f in fs[:max(0, len(fs) - CACHE_KEEP)]:
        f.unlink(missing_ok=True)


def raw_key(rec: dict, text: str, lang: str, settings: dict | None, emo: str = "") -> str:
    st = settings or {}
    eng = engine_of(clean(rec)["base"])
    model = {"vieneu": st.get("vieneu_mode", "v3turbo") + "|" + st.get("vieneu_device", "auto"),
             "omni": st.get("omnivoice_model", ""), "vox": st.get("voxcpm_model", "")}.get(eng, "")
    raw = f"{source_key(rec)}|{text}|{lang}|{model}|{emo if eng == 'vox' else ''}"
    return hashlib.md5(raw.encode()).hexdigest()[:14]


def cached_raw(rec: dict, text: str, lang: str = "vi", settings: dict | None = None, emo: str = "") -> Path | None:
    k = raw_key(rec, text, lang, settings, emo)
    for ext in (".wav", ".mp3"):
        p = CACHE / f"raw_{k}{ext}"
        if p.exists() and p.stat().st_size > 0:
            return p
    return None


def read_raw(rec: dict, text: str, settings: dict | None = None, lang: str = "vi", emo: str = "") -> Path:
    """Bộ đọc đọc câu thử bằng NGUỒN giọng (chưa chỉnh). Đã đọc (cùng nguồn + lời) thì dùng lại."""
    from . import tts

    have = cached_raw(rec, text, lang, settings, emo)
    if have:
        have.touch()
        return have
    r = clean(rec)
    CACHE.mkdir(parents=True, exist_ok=True)
    k = raw_key(r, text, lang, settings, emo)
    rate, pitch = edge_args(r)
    got = Path(tts.preview_voice(r["base"], text, CACHE / f"tmp_{k}", overlay(settings, r), lang=lang, rate=rate,
                                 pitch=pitch, variant=variant(r), emo=emo))
    dst = CACHE / f"raw_{k}{got.suffix.lower() or '.wav'}"
    shutil.move(str(got), str(dst))
    _clean_cache()
    return dst


def render(rec: dict, raw: str | Path) -> Path:
    """Áp phần chỉnh lên file thô của câu thử -> file nghe (đã có thì dùng lại)."""
    fx = fx_of(rec)
    out = CACHE / f"out_{Path(raw).stem[4:]}_{fx_key(fx) or 'g'}.wav"
    if out.exists() and out.stat().st_size > 0:
        out.touch()
        return out
    return apply_file(raw, fx, out)


def preview(rec: dict, text: str, settings: dict | None = None, lang: str = "vi", emo: str = "") -> Path:
    return render(rec, read_raw(rec, text, settings, lang, emo))


def stats(path: str | Path, text: str) -> dict:
    """Độ dài (giây), số tiếng, tiếng / giây (phần có tiếng, bỏ lặng 2 đầu) + đỉnh sóng âm cho hình."""
    from . import tts

    a = tts._read_wav24(Path(path))
    if a is None:
        a = tts._decode(path)
    body = tts._trim_silence(a)
    syl = tts.syllables(text)
    dur = len(a) / tts.SR
    speak = max(0.05, len(body) / tts.SR)
    return {"dur": dur, "syl": syl, "rate": syl / speak if syl else 0.0, "peaks": peaks(a, 120)}


def peaks(a, n: int = 120) -> list[float]:
    import numpy as np

    if not len(a):
        return [0.0] * n
    k = max(1, len(a) // n)
    m = np.abs(np.asarray(a[:k * n], dtype=np.float32)).reshape(-1, k).max(axis=1)
    top = float(m.max()) or 1.0
    return [float(x) / top for x in m]


def measure_rate(rec: dict, settings: dict | None = None, lang: str = "vi", progress=None) -> float:
    """Tốc độ đọc của giọng (tiếng / giây) – đọc 3 câu chuẩn rồi đo phần có tiếng."""
    syl = 0
    sec = 0.0
    for k, t in enumerate(RATE_TEXTS):
        if progress:
            progress(k / len(RATE_TEXTS), f"Đo tốc độ đọc – câu {k + 1}/{len(RATE_TEXTS)}")
        st = stats(preview(rec, t, settings, lang), t)
        syl += st["syl"]
        sec += st["syl"] / st["rate"] if st["rate"] else 0.0
    return round(syl / sec, 1) if sec else 0.0


# ------------------------------------------------------------------ danh sách giọng nền theo bộ đọc

def base_voices(engine: str, settings: dict | None = None) -> list[dict]:
    """Giọng nền chọn được cho 1 bộ đọc (không có nhái giọng nhân vật gốc – cần video của 1 dự án)."""
    from . import tts

    if engine == "edge":
        return tts.edge_voices("vi")
    out = []
    for v in tts.list_voices(settings, "vi"):
        vid = v["id"]
        if vid.startswith(PREFIX) or vid in (tts.CLONE, tts.OMNI_CLONE, tts.VOX_CLONE) or engine_of(vid) != engine:
            continue
        out.append(v)
    return out


def engine_ready(engine: str) -> str:
    """"" = dùng được; không thì lý do (chưa cài bộ đọc…)."""
    from . import tts

    if engine == "vieneu" and not tts.vieneu_available():
        return "Chưa cài VieNeu-TTS (xem hướng dẫn cài bộ AI nâng cao)"
    if engine == "omni" and not tts.omnivoice_available():
        return "Chưa cài OmniVoice"
    if engine == "vox" and not tts.voxcpm_available():
        return "Chưa cài VoxCPM2 – tab Tài liệu"
    if engine == "korva":
        from . import korva

        if not korva.available():
            return "Chưa cài KorvaTTS"
        if not korva.ready():
            return "Chưa tải mô hình KorvaTTS – tab Tài liệu"
    return ""


def gender_of(vid: str) -> str:
    r = get(vid)
    return (r or {}).get("gender", "")


def rate_of(vid: str) -> float | None:
    r = get(vid)
    return (r or {}).get("rate") or None


def label(vid: str) -> str:
    r = get(vid)
    if not r:
        return "Giọng của tôi (đã xoá)"
    return f"{r['name']} (giọng của tôi · {ENGINE_LABEL.get(engine_of(r['base']), '')})"


def safe_name(name: str) -> str:
    return re.sub(r'[\\/:*?"<>|]+', "_", name).strip() or "giong"
