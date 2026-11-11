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
USER_FONTS_DIR = DATA_DIR / "fonts"     # font riêng người dùng nạp (tab Chữ+Logo / Kiểu chữ)
PROJECTS_DIR = DATA_DIR / "projects"
MODELS_DIR = DATA_DIR / "models"
CACHE_DIR = DATA_DIR / "cache"
# Mô hình ModelScope (SenseVoice, VAD, Paraformer, căn thời gian…) luôn cất ở MỘT chỗ: data/models/funasr
# (trước 2.0.22: lúc dịch cất ở đây nhưng tab Tài liệu lại tải vào ~/.cache/modelscope -> tải lại 2 lần)
os.environ.setdefault("MODELSCOPE_CACHE", str(MODELS_DIR / "funasr"))
# Bản mang đi kèm mô hình (DongGoi_MoHinh.bat / trami/packmodels.py): kho HuggingFace nằm trong data/models/hf cạnh exe
# -> dùng kho đó (không thì như cũ: %USERPROFILE%\.cache\huggingface). Tiến trình phụ (Qwen3, VieNeu…) kế thừa biến này.
if (MODELS_DIR / "hf").is_dir() and not (os.environ.get("HF_HUB_CACHE") or os.environ.get("HF_HOME")):
    os.environ["HF_HUB_CACHE"] = str(MODELS_DIR / "hf")
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
    "asr_engine": "qwen3",            # qwen3 / firered / sensevoice / paraformer / whisper (qwen3 chưa cài -> sensevoice)
    "asr_dual": False,                # nghe 2 bộ rồi so (Qwen3-ASR + FireRedASR2): câu khác nhau -> AI chọn / tô xanh
    "asr_ai": "auto",                 # AI hỗ trợ nghe (chọn bản đúng khi 2 bộ khác nhau, lập hồ sơ tên nhân vật): auto/off/tên AI
    "asr_context": False,             # gợi ý cho bộ nghe: danh sách tên riêng của dự án (tắt = như 2.0.99: bảng thuật ngữ)
    "asr_name_pass": True,            # nghe xong: tìm câu có chữ đọc giống tên nhân vật -> nghe lại với gợi ý tên / sửa theo âm
    "qwen_asr_model": "Qwen/Qwen3-ASR-1.7B-hf",   # hoặc Qwen/Qwen3-ASR-0.6B-hf (GPU yếu)
    "asr_model": "large-v3",          # (Whisper) tiny/base/small/medium/large-v3/large-v3-turbo
    "asr_device": "auto",             # auto/cuda/cpu
    "asr_compute_type": "auto",       # auto/float16/int8_float16/int8
    "asr_beam_size": 5,
    "asr_vad": True,
    "ocr_fps": "4",                   # OCR phụ đề cứng: số lần kiểm tra chữ đổi mỗi giây (giờ câu vẫn đúng tới 1 khung)
    "ocr_model": "v6_small",          # 2.1.31: bộ đọc chữ (ocr_engine.MODELS): v6_small có sẵn / v6_medium / v5_server / v4
    "ocr_device": "auto",             # auto = card nếu onnxruntime có bản card (onnxruntime-gpu / -directml) | cpu
    "ocr_hwdec": True,                # giải mã video bằng card khi quét phụ đề (NVDEC / D3D11; không được tự về CPU)
    "ocr_clean": True,                # đọc trên ảnh chữ sạch dựng từ nhiều khung (nền chuyển động bị loại)
    "sub_filter_words": "",           # 2.1.33: lọc từ (tab Phụ đề → Công cụ → Sửa câu): cụm cần xoá ở mọi câu ("@纵横")
    "sub_filter_auto": False,         # ... tự lọc mỗi lần lấy phụ đề mới (Nghe & tách câu / OCR / Tự động)
    # OCR (2.1.36): lọc ảnh làm rõ chữ trước khi quét (chỉ ảnh đưa vào bộ đọc chữ – video xuất không đổi) + câu đọc
    # không chắc (độ chắc đọc chữ dưới mức: tô màu / tích được; bật xoá -> bỏ khi lấy phụ đề như phần nghe)
    "ocr_eq": False, "ocr_eq_brightness": -0.30, "ocr_eq_contrast": 1.80, "ocr_eq_gamma": 0.50,
    "ocr_eq_sharpen": False,
    "ocr_pick_conf": 0.85,
    # 2.1.42 (thay "Xoá câu OCR không chắc"): câu OCR đọc chắc dưới ocr_pick_conf -> bộ nghe nghe lại đúng khúc giờ câu
    # đó; chắc từ ocr_listen_conf: ra đúng chữ thì giữ, ra câu khác thì điền; không chắc thì xoá
    "ocr_listen_unsure": False, "ocr_listen_conf": 0.7,
    # 2.1.44: câu OCR "không đạt" (đọc chắc dưới mức / không có tiếng nói suốt lúc hiện) -> bộ nghe nghe đúng giờ câu; câu
    # chưa được giọng nói xác nhận -> AI xem ảnh (ocr_ai_engine: auto = công cụ dịch nếu AI xem được ảnh / off / tên AI)
    # đọc lại -> chọn theo 2 trên 3 nguồn (chữ OCR, chữ AI, lời nghe được; khớp theo âm đọc ≥ ocr_ai_match). Giờ câu giữ
    # nguyên theo OCR
    "ocr_ai_engine": "auto", "ocr_ai_match": 0.7, "ocr_check_novoice": True,
    # 2.1.44: chỉ lấy chữ đúng màu chữ / viền (ocr_colors: [{"fill": "#RRGGBB", "outline": "#RRGGBB" | ""}], tối đa 3;
    # outline "" = không xét viền), độ lệch tight / mid / wide; phụ đề căn giữa: bỏ khối chữ cùng hàng nằm xa cụm giữa
    "ocr_color": False, "ocr_colors": [{"fill": "#FFFFFF", "outline": "#000000"}], "ocr_color_tol": "mid",
    "ocr_center": True,
    # 2.1.37: sửa chữ dễ nhầm theo từ điển, đọc lại câu kém chắc bằng bộ đọc lớn đã tải (PP-OCRv5 server / v6 medium)
    # (2.1.42: bỏ hẳn chức năng lọc logo chữ – "ocr_logo" / "ocr_logo_known" cũ trong settings.json không còn tác dụng)
    "ocr_dict_fix": True, "ocr_big": True,
    # tách phụ đề theo đoạn (2.1.35, tab Phụ đề): tìm khoảng chưa có phụ đề dài từ … đến … giây (0 = không giới hạn)
    "span_gap_min": 10.0, "span_gap_max": 60.0, "span_songs": False,
    "span_mode": "keep",              # keep: giữ câu cũ, chỉ thêm câu còn thiếu | replace: thay câu cũ trong đoạn
    "span_dub": True,                 # dự án đã dịch / lồng tiếng: dịch + lồng luôn câu mới
    "span_open": True,                # khung mở
    "ocr_timing": "sub",              # 2.1.32: giờ câu khi lấy phụ đề bằng OCR: sub = đúng giờ hiện / tắt chữ trên hình |
                                      # mix = kết hợp: câu theo phụ đề, mốc từng chữ + chỗ ngừng giữa câu theo giọng nói
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
    "srt_output_time": True,          # 2.3.8: SRT đã dịch / song ngữ theo giờ VIDEO ĐÃ XUẤT (cắt / tốc độ / giãn / chèn)
    "dense_min": 5, "dense_thr": 1.1, "dense_mix": True, "dense_how": "gaps",    # giãn video ở đoạn nói nhanh (2.3.8)
    "dense_gap": "auto", "dense_gap_add": 0.4, "dense_gap_floor": 0.4, "dense_block": "auto",
    "dense_block_rate": 0.85, "dense_block_floor": 0.75, "dense_cap": 6.0,
    "dub_speed_mode": "exact",        # cách đổi tốc độ giọng câu đã tích: exact (đúng tốc độ) / rel (%) / min (tối thiểu)
    "dub_speed_pct": -10,             # % nhanh (+) / chậm (−) so với tốc độ đang đọc khi cách đổi = rel
    "dub_overflow": "smart",          # câu đọc dài hơn chỗ trống: smart / push / overlap / early / cut (tts.OVERFLOW_MODES)
    "dub_lead_max": 1.0,              # "Tự động": câu dài được bắt đầu sớm tối đa (giây)
    "dub_max_lag": 2.0,               # "Tự động": câu sau bị lùi tối đa (giây), quá thì đọc đè
    "asr_gemini_review": False,       # Gemini xem video soát lại phụ đề gốc cuối bước Nghe & tách câu (2.1.25)
    "translate_video": False,         # dịch bằng Gemini có gửi kèm khúc video (xưng hô theo hình, giới tính người nói)
    "karaoke_listen": True,           # karaoke theo mốc thật từng từ của giọng lồng (Edge trả sẵn / Whisper nghe lại)
    "dub_subs_follow": True,          # phụ đề Việt hiện theo đúng lúc giọng lồng đọc (khi giọng sớm / trễ / dài)
    "sub_follow_mode": "always",      # giờ hiện phụ đề: always = luôn theo giọng lồng / shift = chỉ khi lệch > 0,3 s
    "sub_lead": 0.1,                  # hiện trước lúc giọng lồng bắt đầu (giây)
    "sub_hold": 0.3,                  # giữ thêm sau khi giọng đọc xong
    "sub_min_dur": 1.0,               # mỗi câu hiện ít nhất
    "sub_join_gap": 0.3,              # khoảng hở giữa 2 câu ngắn hơn -> câu trước giữ tới câu sau (không nháy)
    "snap_edges": True,               # bám mép câu theo giọng đã tách sau khi nghe (2.0.96)
    "dub_pauses": True,               # giọng lồng nghỉ đúng chỗ nhân vật ngắt hơi giữa câu (cần mốc từng chữ)
    "dub_pause_min": 0.3,             # ... khi nhân vật ngừng ít nhất (giây)
    "dub_by_clause": True,            # ... đọc TỪNG VẾ rồi ghép theo giờ gốc (chỗ nghỉ chắc chắn, vế dài đọc nhanh riêng)
    "breath_marks": True,             # chỗ nhân vật ngắt hơi mà câu gốc chưa có dấu câu -> thêm "，" (sau khi nghe / dịch)
    "trans_clause_match": True,       # AI dịch: câu dịch thiếu vế so với chỗ ngắt hơi -> AI sửa cho đủ vế
    "trans_name_fix": False,          # TÊN RIÊNG (viết hoa) còn để pinyin trong bản dịch -> âm Hán Việt (2.4.2/2.4.4)
    "trans_name_kinds": "all",        # all / people (chỉ tên người) / places (chỉ địa danh, môn phái, đồ vật) – 2.4.4
    "trans_term_fix": False,          # từ thường (viết thường) còn để pinyin "jianghu" -> "giang hồ" (2.4.4)
    "trans_slang": False,             # từ lóng / tiếng mạng (躺平, yyds…) -> gửi nghĩa quen dùng cho công cụ dịch (2.4.2)
    "dub_fit": False,                 # AI rút gọn bản dịch cho vừa thời gian nói (lúc dịch + AI kiểm tra); 2.4.10: mặc định
                                      # tắt = giữ bản dịch sát nghĩa
    "dub_auto_shorten": False,        # câu đọc vẫn quá dài -> AI rút gọn rồi đọc lại; 2.4.10: mặc định tắt (giữ bản dịch)
    # nút "AI rút gọn" (tab Lồng tiếng, 2.3.6)
    "shorten_mode": "speech",         # rút cho vừa: speech = thời gian nhân vật nói / slot = chỗ trống tới câu sau (cũ)
    "shorten_speed": 1.1,             # ... khi giọng đọc nhanh tối đa (×)
    "shorten_rounds": 3,              # rút gọn -> đọc lại giọng -> đo -> còn dài thì rút tiếp, tối đa N lượt
    "shorten_fallback": "both",       # rút nữa mất ý: both = đọc nhanh vừa đủ, vẫn dài thì tích sẵn / speed / mark
    "shorten_max_speed": 1.2,         # ... đọc nhanh vừa đủ tối đa (×)
    "align_words": True,              # căn thời gian từng chữ sau khi nhận dạng
    "asr_rescue": True,               # nghe lại đoạn có tiếng mà bộ chia đoạn bỏ sót (cần tách giọng trước)
    "asr_rescue_conf": 0.5,           # ... bỏ câu nghe lại có độ tin cậy (xác suất trung bình mỗi chữ) dưới mức này
    "split_speaker_changes": True,    # tách câu có 2 người nói liền nhau
    "vieneu_mode": "v3turbo",         # v3turbo (đẹp) / v3nano (máy yếu)
    "vieneu_style": "clear",          # clear: đọc rõ, ổn định / natural: mặc định VieNeu (nhiều ngữ điệu hơn)
    "dub_level_match": True,          # câu lồng tiếng nhỏ hơn hẳn các câu khác -> tăng lên cho đều
    "omnivoice_model": "k2-fsa/OmniVoice",   # hoặc g-group-ai-lab/g-omnivoice (tối ưu tiếng Việt, cần HF token)
    "hf_token": "",                           # token HuggingFace cho mô hình cần đồng ý điều khoản
    # Kỹ năng dịch AI (skills.py) – dùng với mọi công cụ dịch AI
    "translate_skill": "chuan",               # kỹ năng đang dùng ("" = không; "chuan" = Dịch chuẩn – giữ đúng số câu)
    "translate_skills": [],                   # kỹ năng của bạn: [{"id", "name", "text"}]
    "genre_forms": [],                        # form thể loại phim bạn đã sửa: [{"id", "text"}] (trami/genres.py)
    "ai_clean_before_translate": False,       # AI sửa lỗi nghe (chữ nghe nhầm) trước khi dịch cả phim lần đầu
    "ai_join_before_translate": None,         # AI nối câu bị cắt trước khi dịch (None = theo công tắc sửa lỗi nghe – 2.3.9)
    "ai_review_after_translate": False,       # AI kiểm tra lại bản dịch sau khi dịch cả phim (tốn thêm ~1 lần gọi AI)
    "ai_song_filter": True,                   # trước khi dịch cả phim lần đầu: AI lọc lời bài hát (🎵) + chữ không phải thoại
    "ai_join_engine": "auto",                 # AI nối câu theo nghĩa sau khi tách câu: auto (công cụ dịch nếu là AI) / off / tên AI
    "asr_relisten": True,                     # nghe lại chỗ cắt khúc giữa lúc đang nói (bỏ dấu "。" giả bộ nghe tự đặt)
    "asr_lang_check": True,                   # khúc nghe chưa chắc -> nghe lại không ép tiếng (câu tiếng Anh trong phim Trung)
    "asr_chunk_sec": 28,                      # độ dài tối đa 1 khúc nghe (giây, 10–60; FireRedASR2 ≤ 55) – 2.1.15
    "asr_recheck": True,                      # câu bộ nghe không chắc -> nghe lại với khúc rộng hơn (câu ở giữa) – 2.1.15
    "asr_recheck_conf": 0.7,                  # ... câu có độ chắc (xác suất trung bình mỗi chữ) dưới mức này
    "asr_recheck_pad": 5.0,                   # ... nghe kèm bấy nhiêu giây mỗi bên (câu trước / sau làm ngữ cảnh)
    "asr_recheck_orig": True,                 # ... nghe thêm trên tiếng gốc chưa tách giọng (bản tách có thể méo)
    "asr_pick_conf": 0.7,                     # câu "không chắc" = độ chắc dưới mức này (nút Tích câu tab Phụ đề + Xoá câu không chắc)
    "asr_edge_fix": True,                     # câu bị cắt đầu / cuối khi nhân vật còn nói -> nghe lại xác nhận rồi nới (2.1.21)
    "asr_drop_unsure": False,                 # bước cuối Nghe & tách câu: xoá câu bộ nghe không chắc trước khi dịch (2.1.19)
    "asr_overlap": False,                     # "Nghe kỹ": mọi câu nghe lần 2 trong khúc lệch (chậm gấp đôi) – 2.1.15
    "asr_guard": True,                        # khúc bộ nghe chép lời gợi ý / lặp 1 cụm -> nghe lại không gợi ý (2.1.8)
    # đăng video (publish.py): Zernio / API riêng YouTube (dự án Google Cloud) / API riêng Facebook (app Meta)
    "zernio_api_key": "", "zernio_profile_id": "",
    "yt_client_id": "", "yt_client_secret": "", "fb_app_id": "", "fb_app_secret": "",
    "browser_exe": "",                        # "Mở Chrome & điền sẵn": đường dẫn chrome.exe / msedge.exe (trống = tự tìm)
    "publish_privacy": "public", "publish_category": "24", "publish_notify": True, "publish_synthetic": False,
    "channel_name": "",                       # tab Chữ & Logo: tên kênh cho logo mẫu
    # câu dịch dài hơn N tiếng (quy đổi theo tốc độ nói của tiếng đích) có dấu phẩy ở cả câu gốc lẫn câu dịch -> tách
    # thành các vế sau khi dịch cả phim; vế sau nghỉ split_pause giây khi lồng tiếng (align.split_clauses)
    "split_long_trans": True, "split_long_units": 14, "split_pause": 0.25,
    # tách phụ đề HIỂN THỊ (2.1.28, trami/subsplit.py): off / punct / words / punct_words / chars / breath
    "sub_split_mode": "off", "sub_split_words": 7, "sub_split_over": 12, "sub_split_punct_over": 6,
    "sub_split_chars": 42, "sub_split_breath": 0.3, "sub_split_min_words": 2, "sub_split_min_dur": 0.6,
    # Rút khoảng lặng / cắt bài hát (silence.py) – mức tìm đoạn để cắt, nhớ cho video sau
    "silence": {"by": "speech", "db": -30, "min": 4.0, "max": 60.0, "keep": 1.5, "where": "split",
                "skip_songs": True, "skip_head": 0.0, "skip_tail": 0.0, "song_pad": 15.0},
    # Tải video từ link: Bilibili đăng nhập bằng mã QR (sitelogin); trang khác lấy cookie từ trình duyệt / file
    "dl_browser": "",                         # "" / firefox / edge / chrome / brave / opera / vivaldi
    "dl_cookie_file": "",                     # file cookies.txt (Netscape) người dùng xuất từ trình duyệt
    "dl_connections": 8,                      # tải video nhiều luồng (2.1.30, như IDM); 1 = 1 kết nối như yt-dlp
    "dl_quality": "1080",                     # chất lượng tải (2.4.1): 1080 / 720 / 480 / 360 / 1440 / best – cao nhất ≤ mức
    "dub_skip_songs": True,           # không lồng tiếng các câu đang hát -> giữ nguyên bài hát gốc
    "dub_drop_interj": True,          # lồng tiếng Việt: xoá câu chỉ có 1–2 tiếng cảm thán (A!, Ừm., Ái!) trước khi đọc
    "interj_inline": True,            # bỏ vế chỉ gồm từ cảm thán (theo danh sách interj_words) trong câu dài
    "dub_voice_all": "",              # 1 giọng cho MỌI người nói (tab Tự động / Lồng tiếng mục 2), "@auto" = tự gán nam / nữ
    "auto_preset": {},                # thiết lập tự động dùng chung (tab Tự động, Trang chủ, hàng loạt) – autoflow.PRESET_DEFAULT
    "batch_render": False,            # Xử lý hàng loạt: tự xuất video luôn (mặc định dừng trước bước xuất)
    "auto_parallel": False,           # tab Tự động: vừa tách vừa dịch (nghe tới đâu dịch tới đó, Edge đọc trước giọng)
    "proxy_mode": "auto",             # bản xem trước nhẹ ≤ 720p cho trình phát: auto (video nặng) / always / off
    "vram_check": True,               # trước việc GPU: card bị chương trình khác chiếm -> báo + chờ (tối đa 3 phút)
    "jobs_parallel": False,           # chạy nhiều việc cùng lúc (tắt = lần lượt từng việc, như trước 2.0.64: ít RAM / GPU)
    "auto_clean": True,               # tự dọn (2.1.29): đệm nghe chỉ giữ cho dự án mở gần đây, bản lồng cũ, file tạm cũ
    "live_keep_projects": 2,          #   giữ đệm nghe trực tiếp (work/live) cho N dự án mở gần nhất
    "dub_keep_versions": 3,           #   mỗi dự án giữ tối đa N bản lồng (đang dùng + đã ghim luôn giữ; 0 = không giới hạn)
    "song_detect": True,              # khi nghe: đoán cụm câu đang hát (nốt ngân trên giọng đã tách) -> 🎵
    "pitch_check": True,              # khi nghe: đánh dấu câu nghi là nhạc / tiếng động bị chép thành lời (cao độ giọng)
    "pitch_tol": 4.0,                 #   lệch cao độ cho phép ngoài khoảng giọng thường của người nói (nửa cung)
    "pitch_trim": True,               #   thu đầu / cuối câu thừa sang nhạc / tiếng động theo đoạn có giọng thật
    "addr_unsure": "drop",            # không chắc xưng hô: drop bỏ đại từ / neutral cặp trung tính / guess AI đoán (2.4.0)
    "translate_speakers": True,       # gửi người nói + nam / nữ từng câu cho AI dịch (2.4.0)
    "translate_fill": False,          # dịch AI: câu ngắn hơn hẳn chỗ trống -> nói đầy đủ hơn (2.1.13: mặc định tắt – không thêm từ)
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
    # Điều khiển từ xa bằng điện thoại (2.3.0, trami/remote)
    "remote_on": False,              # bật máy chủ điều khiển trong tool
    "remote_port": 8765,             # cổng trong mạng nhà (Wi-Fi)
    "remote_pw_secret": "",          # mật khẩu đăng nhập (chỉ lưu dạng băm "pbkdf2$muối$băm")
    "remote_vps_url": "",            # trạm VPS (https://tên-miền hoặc https://IP:cổng) – tool tự nối ra, không mở cổng modem
    "remote_vps_secret": "",         # mã kết nối VPS (file cài VPS in ra)
    "remote_vps_fp": "",             # vân tay chứng chỉ tự ký của VPS (khi dùng IP, không tên miền)
    "remote_notify": True,           # báo lên điện thoại khi việc xong / lỗi
    "remote_awake": True,            # giữ máy không ngủ khi đang bật điều khiển từ xa
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


def quiet_dll_errors() -> None:
    """Windows: nạp DLL lỗi (thiếu hàm – "Entry Point Not Found", thiếu file) trả lỗi cho Python thay vì bật hộp thoại
    chặn cả tool (2.1.40 – PyTorch cũng làm vậy khi nạp thư viện của nó). Gọi 1 lần khi mở tool, tiến trình phụ thừa hưởng."""
    if sys.platform != "win32":
        return
    try:
        import ctypes

        k = ctypes.WinDLL("kernel32", use_last_error=True)
        SEM_FAILCRITICALERRORS, SEM_NOOPENFILEERRORBOX = 0x0001, 0x8000
        k.SetErrorMode(k.SetErrorMode(0) | SEM_FAILCRITICALERRORS | SEM_NOOPENFILEERRORBOX)
    except Exception:  # noqa: BLE001
        pass


NONE_GUARD = False   # 2.2.0: đã bật lá chắn lỗi đếm tham chiếu None của PySide6 6.12.0


def guard_none_refcount() -> bool:
    """PySide6 6.12.0 + Python < 3.12: MỌI hàm Qt trả về void gọi từ Python (setText, update, vẽ bằng QPainter,
    processEvents…) trả về None mà không tăng đếm tham chiếu -> mỗi lần mất 1 tham chiếu của None -> dùng tool đủ lâu
    None về 0, Python tự sập "Fatal Python error: none_dealloc: deallocating None" (PySide6 6.11.x không bị; Python
    3.12+ None bất tử nên không sao). Đo thử trên 1 QObject; bị lỗi thì cho None số tham chiếu rất lớn (như Python 3.12
    làm) -> không bao giờ về 0. Gọi 1 lần trước khi dựng giao diện."""
    global NONE_GUARD
    if NONE_GUARD or sys.version_info >= (3, 12) or hasattr(sys, "gettotalrefcount"):
        return NONE_GUARD
    try:
        from PySide6.QtCore import QObject
    except ImportError:
        return False
    try:
        o = QObject()
        before = sys.getrefcount(None)
        for _ in range(64):
            o.setObjectName("x")
        lost = before - sys.getrefcount(None)
        if lost < 48:
            return False
        import ctypes

        ctypes.c_ssize_t.from_address(id(None)).value += 1 << 60
        NONE_GUARD = True
        print(f"[Subvideo] PySide6 bản này làm mất tham chiếu của None ({lost}/64 lần gọi) – đã bật lá chắn; "
              'nên cài: python -m pip install "PySide6<6.12"', file=sys.stderr)
    except Exception:  # noqa: BLE001
        return False
    return NONE_GUARD


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
