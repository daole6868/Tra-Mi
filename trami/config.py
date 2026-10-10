"""Cấu hình, đường dẫn và lưu thiết lập người dùng."""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path

import sys

FROZEN = bool(getattr(sys, "frozen", False))          # đang chạy từ file .exe đóng gói
PKG_DIR = Path(__file__).resolve().parent
if FROZEN:
    BUNDLE_DIR = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))   # tài nguyên đi kèm exe
    APP_DIR = Path(sys.executable).resolve().parent                           # thư mục chứa TraMi.exe
    _default_data = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "TraMi"
else:
    BUNDLE_DIR = APP_DIR = PKG_DIR.parent
    _default_data = APP_DIR / "data"
ROOT_DIR = APP_DIR
FONTS_DIR = BUNDLE_DIR / "fonts"

DATA_DIR = Path(os.environ.get("TRAMI_DATA", _default_data))
PROJECTS_DIR = DATA_DIR / "projects"
MODELS_DIR = DATA_DIR / "models"
CACHE_DIR = DATA_DIR / "cache"
# Mô hình ModelScope (SenseVoice, VAD, Paraformer, căn thời gian…) luôn cất ở MỘT chỗ: data/models/funasr
# (trước 2.0.22: lúc dịch cất ở đây nhưng tab Tài liệu lại tải vào ~/.cache/modelscope -> tải lại 2 lần)
os.environ.setdefault("MODELSCOPE_CACHE", str(MODELS_DIR / "funasr"))
SETTINGS_FILE = DATA_DIR / "settings.json"

for _d in (DATA_DIR, PROJECTS_DIR, MODELS_DIR, CACHE_DIR):
    _d.mkdir(parents=True, exist_ok=True)

DEFAULT_SETTINGS: dict = {
    # Tách giọng / nhạc nền
    "sep_model": "model_bs_roformer_ep_317_sdr_12.9755.ckpt",   # BS-RoFormer (đẹp nhất, nên dùng GPU)
    "sep_before_asr": True,           # nhận dạng trên giọng đã tách -> chính xác hơn khi có nhạc
    # Phân biệt người nói
    "diarize": True,
    "diarize_speakers": 0,            # 0 = tự đoán số người nói
    "diarize_threshold": 0.62,        # độ giống nhau để coi là cùng 1 người (0..1)
    # Nhận dạng giọng nói
    "asr_engine": "sensevoice",       # sensevoice / paraformer / whisper
    "asr_model": "large-v3",          # (Whisper) tiny/base/small/medium/large-v3/large-v3-turbo
    "asr_device": "auto",             # auto/cuda/cpu
    "asr_compute_type": "auto",       # auto/float16/int8_float16/int8
    "asr_beam_size": 5,
    "asr_vad": True,
    "asr_max_chars": 22,              # số chữ Hán tối đa mỗi dòng phụ đề
    # Dịch
    "translate_engine": "google",     # google / openai / anthropic / gemini / deepseek / ollama / offline(none)
    "translate_style": "auto",        # auto/co_trang/hien_dai/review/phim_hoat_hinh/tai_lieu
    "translate_batch": 40,
    "openai_api_key": "",
    "openai_base_url": "https://api.openai.com/v1",
    "openai_model": "gpt-4o-mini",
    "anthropic_api_key": "",
    "anthropic_model": "claude-opus-5-5",
    "gemini_api_key": "",
    "gemini_model": "gemini-2.5-flash",
    "deepseek_api_key": "",
    "deepseek_model": "deepseek-chat",
    "ollama_base_url": "http://localhost:11434/v1",
    "ollama_model": "qwen2.5:7b",
    "glossary": "",                   # mỗi dòng: 中文=Tiếng Việt
    # Lồng tiếng
    "tts_engine": "edge",             # edge (Microsoft, cần mạng) / vieneu (AI offline, nhái giọng)
    "tts_voice": "vi-VN-HoaiMyNeural",
    "tts_rate": "+0%",
    "tts_max_speedup": 1.3,           # tăng tốc giọng lồng tối đa (cao hơn nghe vội, mất tự nhiên)
    "dub_fit": True,                  # AI dịch vừa thời lượng câu (giới hạn số tiếng)
    "dub_auto_shorten": True,         # câu đọc vẫn quá dài -> AI rút gọn rồi đọc lại
    "align_words": True,              # căn thời gian từng chữ sau khi nhận dạng
    "split_speaker_changes": True,    # tách câu có 2 người nói liền nhau
    "vieneu_mode": "v3turbo",         # v3turbo (đẹp) / v3nano (máy yếu)
    "omnivoice_model": "k2-fsa/OmniVoice",   # hoặc g-group-ai-lab/g-omnivoice (tối ưu tiếng Việt, cần HF token)
    "hf_token": "",                           # token HuggingFace cho mô hình cần đồng ý điều khoản
    "dub_skip_songs": True,           # không lồng tiếng các câu đang hát -> giữ nguyên bài hát gốc
    # Xoá phụ đề
    "inpaint_backend": "lama",        # lama (AI, đẹp) / opencv (nhanh)
    # Phụ đề mặc định khi ghép vào video
    "sub_style": {
        "font": "Be Vietnam Pro",
        "size": 0,                    # 0 = tự động theo chiều cao video
        "bold": True,
        "color": "#FFFFFF",
        "outline_color": "#000000",
        "outline": 2.5,
        "shadow": 1,
        "box": False,                 # nền hộp mờ sau chữ
        "box_color": "#000000",
        "box_opacity": 0.55,
        "position": "auto",           # auto (đúng chỗ phụ đề cũ) / bottom / top / middle
        "margin_v": 0,                # 0 = tự động
        "uppercase": False,
    },
    # Xuất video
    "video_codec": "libx264",        # libx264 / h264_nvenc / libx265
    "video_crf": 20,
    "video_preset": "medium",
    "audio_bitrate": "192k",
}

_lock = threading.Lock()


def _deep_merge(base: dict, extra: dict) -> dict:
    out = dict(base)
    for k, v in extra.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def log_error(where: str = "") -> Path:
    """Ghi traceback của lỗi đang xử lý vào data/logs/loi.txt để gửi đi khi cần hỗ trợ."""
    import time
    import traceback

    f = DATA_DIR / "logs" / "loi.txt"
    try:
        f.parent.mkdir(parents=True, exist_ok=True)
        if f.exists() and f.stat().st_size > 500_000:
            f.write_text(f.read_text("utf-8", errors="replace")[-200_000:], "utf-8")
        with open(f, "a", encoding="utf-8") as fh:
            fh.write(f"\n===== {time.strftime('%Y-%m-%d %H:%M:%S')} {where}\n{traceback.format_exc()}")
    except OSError:
        pass
    return f


def err_text(e: BaseException) -> str:
    """Tên lỗi + nội dung (nhiều lỗi có nội dung rỗng, chỉ tên lỗi mới cho biết chuyện gì)."""
    msg = str(e).strip()
    return f"{type(e).__name__}: {msg}" if msg else type(e).__name__


def installed(*pkgs: str) -> bool:
    """Gói đã được cài chưa – KHÔNG import (import torch/funasr có thể mất cả phút, làm treo giao diện)."""
    import importlib.util

    try:
        return all(importlib.util.find_spec(p) is not None for p in pkgs)
    except (ImportError, ValueError):
        return False


SETTINGS_VERSION = 2


def _diff(cur: dict, base: dict) -> dict:
    """Chỉ giữ các giá trị khác mặc định (để sau này đổi mặc định vẫn có tác dụng)."""
    out = {}
    for k, v in cur.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            d = _diff(v, base[k])
            if d:
                out[k] = d
        elif k not in base or base[k] != v:
            out[k] = v
    return out


def _read_raw() -> dict:
    if not SETTINGS_FILE.exists():
        return {}
    try:
        data = json.loads(SETTINGS_FILE.read_text("utf-8"))
    except Exception:
        return {}
    if int(data.get("_version", 1)) < SETTINGS_VERSION:
        # bản 1.x lưu cứng toàn bộ mặc định -> bỏ các giá trị mặc định cũ để nhận mặc định mới
        st = data.get("sub_style") or {}
        if st.get("position") == "bottom":
            st.pop("position", None)
        data = _diff(data, DEFAULT_SETTINGS)
        data["_version"] = SETTINGS_VERSION
    return data


def load_settings() -> dict:
    with _lock:
        data = _read_raw()
        data.pop("_version", None)
        return _deep_merge(DEFAULT_SETTINGS, data)


def save_settings(new: dict) -> dict:
    with _lock:
        raw = _read_raw()
        raw.pop("_version", None)
        merged = _deep_merge(_deep_merge(DEFAULT_SETTINGS, raw), new or {})
        store = _diff(merged, DEFAULT_SETTINGS)
        store["_version"] = SETTINGS_VERSION
        tmp = SETTINGS_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(store, ensure_ascii=False, indent=2), "utf-8")
        tmp.replace(SETTINGS_FILE)
    return merged


def public_settings(s: dict | None = None) -> dict:
    """Ẩn bớt API key khi gửi ra giao diện."""
    s = dict(s or load_settings())
    for k in list(s):
        if (k.endswith("_api_key") or k == "hf_token") and s[k]:
            s[k] = "••••" + s[k][-4:]
    return s
