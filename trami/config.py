"""Cấu hình, đường dẫn và lưu thiết lập người dùng."""
from __future__ import annotations

import io
import json
import os
import threading
from pathlib import Path

import sys

FROZEN = bool(getattr(sys, "frozen", False))          # đang chạy từ file .exe đóng gói
PKG_DIR = Path(__file__).resolve().parent


def _frozen_data_dir(app_dir: Path) -> Path:
    """Thư mục dữ liệu của bản .exe (dự án, cài đặt, mô hình đã tải):
    1. datadir.txt cạnh exe (DongGoi_EXE.bat ghi) -> DÙNG CHUNG thư mục data của bản chạy bằng Python trên máy này;
    2. thư mục data cạnh exe (bản mang đi / USB);
    3. %LOCALAPPDATA%\\Subvideo (máy khác) – còn dữ liệu bản exe cũ %LOCALAPPDATA%\\TraMi thì dùng tiếp chỗ đó."""
    ptr = app_dir / "datadir.txt"
    try:
        want = Path(ptr.read_text("utf-8-sig").strip()) if ptr.is_file() else None
    except OSError:
        want = None
    if want and want.is_dir():
        return want
    if (app_dir / "data").is_dir():
        return app_dir / "data"
    local = Path(os.environ.get("LOCALAPPDATA", Path.home()))
    if (local / "TraMi").is_dir() and not (local / "Subvideo").exists():
        return local / "TraMi"
    return local / "Subvideo"


if FROZEN:
    BUNDLE_DIR = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))   # tài nguyên đi kèm exe
    APP_DIR = Path(sys.executable).resolve().parent                           # thư mục chứa Subvideo.exe
    _default_data = _frozen_data_dir(APP_DIR)
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
# Windows không bật Developer Mode thì kho HuggingFace chép file thay cho symlink – chỉ là cảnh báo, không phải lỗi
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
# Máy này từng bị treo khi tải kiểu Xet (hf_download.py) -> mọi chỗ tải HuggingFace dùng tải thường
NO_XET_FILE = DATA_DIR / "hf_no_xet.txt"
if NO_XET_FILE.is_file():
    os.environ.setdefault("HF_HUB_DISABLE_XET", "1")
# HuggingFace không tải được trên máy này nhưng ModelScope được -> mô hình có bản ModelScope chính hãng tải từ đó trước
PREFER_MS_FILE = DATA_DIR / "hf_prefer_modelscope.txt"
SETTINGS_FILE = DATA_DIR / "settings.json"

for _d in (DATA_DIR, PROJECTS_DIR, MODELS_DIR, CACHE_DIR):
    _d.mkdir(parents=True, exist_ok=True)

DEFAULT_SETTINGS: dict = {
    # Tách giọng / nhạc nền
    "sep_model": "vocals_mel_band_roformer.ckpt",   # Mel-RoFormer Kim (tách giọng sạch nhất, nên dùng GPU)
    "sep_before_asr": True,           # nhận dạng trên giọng đã tách -> chính xác hơn khi có nhạc
    # Phân biệt người nói
    "diarize": True,
    "diarize_speakers": 0,            # 0 = tự đoán số người nói
    "diarize_threshold": 0.62,        # độ giống nhau để coi là cùng 1 người (0..1)
    # Nhận dạng giọng nói
    "asr_engine": "qwen3",            # qwen3 / sensevoice / paraformer / whisper (qwen3 chưa cài -> sensevoice)
    "qwen_asr_model": "Qwen/Qwen3-ASR-1.7B-hf",   # hoặc Qwen/Qwen3-ASR-0.6B-hf (GPU yếu)
    "asr_model": "large-v3",          # (Whisper) tiny/base/small/medium/large-v3/large-v3-turbo
    "asr_device": "auto",             # auto/cuda/cpu
    "asr_compute_type": "auto",       # auto/float16/int8_float16/int8
    "asr_beam_size": 5,
    "asr_vad": True,
    "ocr_fps": "4",                   # OCR phụ đề cứng: số lần quét mỗi giây (2 nhanh / 4 chuẩn / 8 kỹ)
    "split_style": "dub",            # kiểu tách câu: dub = câu trọn ý (hợp lồng tiếng) / short = câu ngắn
    "asr_max_chars": 28,              # câu dài hơn số chữ Hán này mới chia đôi (ở dấu phẩy / chỗ nghỉ gần giữa)
    # Dịch
    "translate_engine": "google",     # google / openai / anthropic / gemini / deepseek / ollama / offline(none)
    "target_lang": "vi",              # dịch sang: vi / en / th / ja / ko (mỗi dự án nhớ riêng, đây là mặc định)
    "translate_style": "auto",        # auto/co_trang/hien_dai/review/phim_hoat_hinh/tai_lieu
    "translate_batch": 40,
    "openai_api_key": "",
    "openai_base_url": "https://api.openai.com/v1",
    "openai_model": "gpt-4o-mini",
    "anthropic_api_key": "",
    "anthropic_model": "claude-opus-5-5",
    "gemini_api_key": "",
    # nhiều key mỗi công cụ (key 1 hết lượt / hết hạn -> tự dùng key 2…), số câu mỗi lần gửi, số lần gửi / phút mỗi key
    "deepseek_api_keys": [], "openai_api_keys": [], "anthropic_api_keys": [], "gemini_api_keys": [],
    "gemini_rpm": 8,
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
    "dub_overflow": "smart",          # câu đọc dài hơn chỗ trống: smart / push / overlap / early / cut (tts.OVERFLOW_MODES)
    "dub_lead_max": 1.0,              # "Tự động": câu dài được bắt đầu sớm tối đa (giây)
    "dub_max_lag": 2.0,               # "Tự động": câu sau bị lùi tối đa (giây), quá thì đọc đè
    "dub_subs_follow": True,          # phụ đề Việt hiện theo đúng lúc giọng lồng đọc (khi giọng sớm / trễ / dài)
    "dub_fit": True,                  # AI dịch vừa thời lượng câu (giới hạn số tiếng)
    "dub_auto_shorten": True,         # câu đọc vẫn quá dài -> AI rút gọn rồi đọc lại
    "align_words": True,              # căn thời gian từng chữ sau khi nhận dạng
    "asr_rescue": True,               # nghe lại đoạn có tiếng mà bộ chia đoạn bỏ sót (cần tách giọng trước)
    "split_speaker_changes": True,    # tách câu có 2 người nói liền nhau
    "vieneu_mode": "v3turbo",         # v3turbo (đẹp) / v3nano (máy yếu)
    "vieneu_style": "clear",          # clear: đọc rõ, ổn định / natural: mặc định VieNeu (nhiều ngữ điệu hơn)
    "dub_level_match": True,          # câu lồng tiếng nhỏ hơn hẳn các câu khác -> tăng lên cho đều
    "omnivoice_model": "k2-fsa/OmniVoice",   # hoặc g-group-ai-lab/g-omnivoice (tối ưu tiếng Việt, cần HF token)
    "hf_token": "",                           # token HuggingFace cho mô hình cần đồng ý điều khoản
    # Kỹ năng dịch AI (skills.py) – dùng với mọi công cụ dịch AI
    "translate_skill": "chuan",               # kỹ năng đang dùng ("" = không; "chuan" = Dịch chuẩn – giữ đúng số câu)
    "translate_skills": [],                   # kỹ năng của bạn: [{"id", "name", "text"}]
    "ai_clean_before_translate": False,       # AI sửa lỗi nghe & nối câu bị cắt trước khi dịch cả phim lần đầu
    "ai_review_after_translate": False,       # AI kiểm tra lại bản dịch sau khi dịch cả phim (tốn thêm ~1 lần gọi AI)
    "ai_song_filter": True,                   # trước khi dịch cả phim lần đầu: AI lọc lời bài hát (🎵) + chữ không phải thoại
    "ai_join_engine": "auto",                 # AI nối câu theo nghĩa sau khi tách câu: auto (công cụ dịch nếu là AI) / off / tên AI
    "asr_relisten": True,                     # nghe lại chỗ cắt khúc giữa lúc đang nói (bỏ dấu "。" giả bộ nghe tự đặt)
    # đăng video (publish.py): Zernio / API riêng YouTube (dự án Google Cloud) / API riêng Facebook (app Meta)
    "zernio_api_key": "", "zernio_profile_id": "",
    "yt_client_id": "", "yt_client_secret": "", "fb_app_id": "", "fb_app_secret": "",
    "browser_exe": "",                        # "Mở Chrome & điền sẵn": đường dẫn chrome.exe / msedge.exe (trống = tự tìm)
    "publish_privacy": "public", "publish_category": "24", "publish_notify": True, "publish_synthetic": False,
    # Tải video từ link: Bilibili đăng nhập bằng mã QR (sitelogin); trang khác lấy cookie từ trình duyệt / file
    "dl_browser": "",                         # "" / firefox / edge / chrome / brave / opera / vivaldi
    "dl_cookie_file": "",                     # file cookies.txt (Netscape) người dùng xuất từ trình duyệt
    "dub_skip_songs": True,           # không lồng tiếng các câu đang hát -> giữ nguyên bài hát gốc
    "dub_drop_interj": True,          # lồng tiếng Việt: xoá câu chỉ có 1–2 tiếng cảm thán (A!, Ừm., Ái!) trước khi đọc
    "dub_voice_all": "",              # 1 giọng cho MỌI người nói (tab Tự động / Lồng tiếng mục 2), "@auto" = tự gán nam / nữ
    "auto_parallel": False,           # tab Tự động: vừa tách vừa dịch (nghe tới đâu dịch tới đó, Edge đọc trước giọng)
    "proxy_mode": "auto",             # bản xem trước nhẹ ≤ 720p cho trình phát: auto (video nặng) / always / off
    "vram_check": True,               # trước việc GPU: card bị chương trình khác chiếm -> báo + chờ (tối đa 3 phút)
    "jobs_parallel": False,           # chạy nhiều việc cùng lúc (tắt = lần lượt từng việc, như trước 2.0.64: ít RAM / GPU)
    "song_detect": True,              # khi nghe: đoán cụm câu đang hát (nốt ngân trên giọng đã tách) -> 🎵
    "pitch_check": True,              # khi nghe: đánh dấu câu nghi là nhạc / tiếng động bị chép thành lời (cao độ giọng)
    "pitch_tol": 4.0,                 #   lệch cao độ cho phép ngoài khoảng giọng thường của người nói (nửa cung)
    "pitch_trim": True,               #   thu đầu / cuối câu thừa sang nhạc / tiếng động theo đoạn có giọng thật
    "translate_fill": True,           # dịch AI: câu ngắn hơn hẳn chỗ trống -> thêm từ đệm / phụ từ cho vừa thời gian
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
        "box_radius": 0.0,            # bo góc nền (0..0,5 chiều cao 1 dòng nền)
        "line_width": 0.9,            # dòng phụ đề dài tối đa 90 % bề rộng khung (đo chữ thật)
        "max_lines": 2,
        "line_fit": "wrap",           # wrap: xuống dòng · shrink: thu nhỏ chữ cho vừa 1 dòng trước
        "min_scale": 0.7,
        "karaoke": False,             # chữ đổi màu lần lượt theo lời đọc
        "karaoke_color": "#FFEF00",
        "karaoke_mode": "sweep",      # sweep: quét mượt từng chữ / word: đổi màu từng từ
        "position": "auto",           # auto (đúng chỗ phụ đề cũ) / bottom / top / middle
        "margin_v": 0,                # 0 = tự động
        "uppercase": False,
    },
    # Xuất video
    "video_codec": "libx264",        # libx264 / h264_nvenc / libx265
    "video_crf": 20,
    "video_preset": "medium",
    "audio_bitrate": "192k",
    "export_chunks": True,           # phim ≥ 10 phút xuất theo từng đoạn 60 s, nhớ đoạn đã xuất -> sửa ít câu xuất lại nhanh
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


class _ConsoleLog(io.TextIOBase):
    """Thay sys.stdout / sys.stderr khi tool chạy KHÔNG có cửa sổ đen (pythonw: shortcut ngoài màn hình,
    ChayTool_Windows.bat, exe) – lúc đó Python để cả hai là None. Thư viện AI (tách giọng, SenseVoice, tải mô hình…)
    vẽ thanh tiến độ tqdm ra đó -> "AttributeError: 'NoneType' object has no attribute 'write'". Ghi vào
    data/logs/console.txt (mỗi lần mở tool ghi mới, tối đa 5 MB)."""

    encoding = "utf-8"
    errors = "replace"
    LIMIT = 5_000_000

    def __init__(self, path: Path):
        super().__init__()
        self._lock = threading.Lock()
        self._n = 0
        self._f = None
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            self._f = open(path, "w", encoding="utf-8", errors="replace")
        except OSError:
            pass

    def writable(self) -> bool:
        return True

    def isatty(self) -> bool:
        return False

    def write(self, s) -> int:
        s = s if isinstance(s, str) else str(s)
        with self._lock:
            if self._f and self._n < self.LIMIT:
                try:
                    self._f.write(s)
                    self._n += len(s)
                except (OSError, ValueError):
                    pass
        return len(s)

    def flush(self) -> None:
        with self._lock:
            try:
                if self._f:
                    self._f.flush()
            except (OSError, ValueError):
                pass


def fix_std_streams(path: Path | None = None) -> None:
    """Gọi đầu tiên khi mở tool: sys.stdout / sys.stderr là None (không cửa sổ đen) -> ghi ra data/logs/console.txt."""
    if sys.stdout is not None and sys.stderr is not None:
        return
    log = _ConsoleLog(path or DATA_DIR / "logs" / "console.txt")
    if sys.stdout is None:
        sys.stdout = log
    if sys.stderr is None:
        sys.stderr = log


def no_console() -> bool:
    """Tool đang chạy không có cửa sổ đen (sys.__stderr__ giữ giá trị gốc kể cả sau fix_std_streams)."""
    return sys.__stderr__ is None or sys.stderr is None or not getattr(sys.stderr, "fileno", None)


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
        if (k.endswith("_api_key") or k.endswith("_secret") or k == "hf_token") and s[k]:
            s[k] = "••••" + s[k][-4:]
    return s
