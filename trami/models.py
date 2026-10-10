"""Danh sách mô hình AI của TraMi: kiểm tra đã tải chưa và tải về (tab "Tài liệu").

Mỗi mô hình được tải bằng đúng cách thư viện tương ứng tự tải, vào đúng thư mục cache của thư viện đó, nên khi dịch
video tool dùng luôn bản đã tải mà không tải lại.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .config import MODELS_DIR, installed

Prog = Callable[[float, str], None]

# tên gọi tắt của FunASR -> mã mô hình trên ModelScope
MS_IDS = {
    "sensevoice": "iic/SenseVoiceSmall",
    "fsmn-vad": "iic/speech_fsmn_vad_zh-cn-16k-common-pytorch",
    "paraformer-zh": "iic/speech_seaco_paraformer_large_asr_nat-zh-cn-16k-common-vocab8404-pytorch",
    "ct-punc": "iic/punc_ct-transformer_cn-en-common-vocab471067-large",
    "cam++": "iic/speech_campplus_sv_zh-cn_16k-common",
    "fa-zh": "iic/speech_timestamp_prediction-v1-16k-offline",
}
VIENEU_REPO = "pnnbao-ump/VieNeu-TTS-v3-Turbo"


@dataclass
class ModelInfo:
    key: str
    name: str
    use: str            # dùng cho bước nào
    size: str
    packages: tuple     # gói Python cần có
    icon: str = "layers"


MODELS = [
    ModelInfo("sep_roformer", "Tách giọng – BS-RoFormer", "Tách giọng nói khỏi nhạc nền (tốt nhất, nên có GPU)",
              "639 MB", ("audio_separator",), "layers"),
    ModelInfo("sep_mdx", "Tách giọng – MDX-Net Voc FT", "Tách giọng nhanh, hợp máy chỉ có CPU", "67 MB",
              ("audio_separator",), "layers"),
    ModelInfo("sensevoice", "Nhận dạng – SenseVoice + VAD", "Nghe tiếng Trung thành chữ (mặc định, nhanh)", "~900 MB",
              ("funasr", "modelscope"), "mic"),
    ModelInfo("paraformer", "Nhận dạng – Paraformer + dấu câu + CAM++",
              "Nhận dạng có dấu câu, tự phân người nói", "~1,9 GB", ("funasr", "modelscope"), "mic"),
    ModelInfo("align", "Căn thời gian từng chữ – FA-zh", "Mốc câu sát lúc nhân vật nói, giúp giọng lồng khớp thoại",
              "~150 MB", ("funasr", "modelscope"), "clock"),
    ModelInfo("whisper", "Nhận dạng – Whisper large-v3", "Nhận dạng đa ngôn ngữ (Anh, Nhật, Hàn…)", "~3 GB",
              ("faster_whisper",), "globe"),
    ModelInfo("ecapa", "Phân biệt người nói – ECAPA", "Tìm ai đang nói để gán giọng lồng riêng", "~80 MB",
              ("speechbrain", "torch"), "users"),
    ModelInfo("lama", "Xoá phụ đề – AI LaMa", "Vẽ lại nền phía sau chữ Trung cũ", "206 MB", ("torch",), "eraser"),
    ModelInfo("vieneu", "Giọng đọc – VieNeu-TTS v3 Turbo", "25 giọng Việt + nhái giọng nhân vật", "~1–2 GB",
              ("vieneu",), "volume"),
    ModelInfo("omnivoice", "Nhái giọng – OmniVoice", "Nhái giọng y hệt từ file ghi âm mẫu 3–10 giây (chọn bản ở Cài đặt)",
              "~2–3 GB", ("omnivoice", "torch"), "mic"),
]
OMNI_TOKENIZER = "eustlb/higgs-audio-v2-tokenizer"
BY_KEY = {m.key: m for m in MODELS}


# ------------------------------------------------------------------ kiểm tra đã tải chưa

def _sep_file(key: str) -> str:
    from .separate import SEP_MODELS

    return SEP_MODELS[0][0] if key == "sep_roformer" else next(f for f, _ in SEP_MODELS if f.startswith("UVR-MDX"))


def _hf_snapshot(repo: str) -> Path | None:
    try:
        from huggingface_hub.constants import HF_HUB_CACHE
        base = Path(HF_HUB_CACHE)
    except Exception:  # noqa: BLE001
        base = Path(os.environ.get("HF_HOME", Path.home() / ".cache" / "huggingface")) / "hub"
    snaps = base / f"models--{repo.replace('/', '--')}" / "snapshots"
    if snaps.is_dir():
        for d in snaps.iterdir():
            if d.is_dir() and any(d.iterdir()):
                return d
    return None


def _hf_cached(repo: str) -> bool:
    try:
        from huggingface_hub.constants import HF_HUB_CACHE
        base = Path(HF_HUB_CACHE)
    except Exception:  # noqa: BLE001
        base = Path(os.environ.get("HF_HOME", Path.home() / ".cache" / "huggingface")) / "hub"
    snaps = base / f"models--{repo.replace('/', '--')}" / "snapshots"
    return snaps.is_dir() and any(any(d.iterdir()) for d in snaps.iterdir() if d.is_dir())


_WEIGHTS = (".pt", ".pth", ".onnx", ".bin", ".safetensors", ".ckpt")


def _has_weights(d: Path) -> bool:
    try:
        return any(f.suffix.lower() in _WEIGHTS and f.stat().st_size > 1_000_000 for f in d.rglob("*") if f.is_file())
    except OSError:
        return False


def ms_location(model_id: str) -> Path | None:
    """Thư mục chứa mô hình ModelScope đã tải (mỗi bản ModelScope lưu một kiểu khác nhau) – None nếu chưa có."""
    org, name = model_id.split("/", 1)
    names = {name, name.replace(".", "___")}
    bases = []
    if os.environ.get("MODELSCOPE_CACHE"):
        bases.append(Path(os.environ["MODELSCOPE_CACHE"]))
    bases += [Path.home() / ".cache" / "modelscope" / "hub", Path.home() / ".cache" / "modelscope"]
    for base in bases:
        for sub in ("models/" + org, org, "hub/models/" + org, "hub/" + org):
            for n in names:
                d = base / sub / n
                if d.is_dir() and _has_weights(d):
                    return d
    try:   # thử hỏi chính ModelScope (bản mới)
        from modelscope.hub.snapshot_download import snapshot_download

        d = snapshot_download(model_id, local_files_only=True)
        if d and Path(d).is_dir() and _has_weights(Path(d)):
            return Path(d)
    except Exception:  # noqa: BLE001
        pass
    for base in bases:   # tìm sâu hơn (chậm hơn) nếu cấu trúc thư mục khác
        if base.is_dir():
            for n in names:
                for d in base.rglob(n):
                    if d.is_dir() and _has_weights(d):
                        return d
    return None


def _ms_cached(model_id: str) -> bool:
    return ms_location(model_id) is not None


def missing_packages(key: str) -> list[str]:
    return [p for p in BY_KEY[key].packages if not installed(p)]


def is_ready(key: str, settings: dict | None = None) -> bool:
    try:
        if key in ("sep_roformer", "sep_mdx"):
            from .separate import SEP_DIR

            f = SEP_DIR / _sep_file(key)
            return f.exists() and f.stat().st_size > 1_000_000 and not (SEP_DIR / (f.name + ".part")).exists()
        if key == "sensevoice":
            return _ms_cached(MS_IDS["sensevoice"]) and _ms_cached(MS_IDS["fsmn-vad"])
        if key == "align":
            return _ms_cached(MS_IDS["fa-zh"])
        if key == "paraformer":
            return all(_ms_cached(MS_IDS[k]) for k in ("paraformer-zh", "fsmn-vad", "ct-punc", "cam++"))
        if key == "whisper":
            name = (settings or {}).get("asr_model", "large-v3")
            from faster_whisper.utils import download_model

            download_model(name, cache_dir=str(MODELS_DIR), local_files_only=True)
            return True
        if key == "ecapa":
            return (MODELS_DIR / "spkrec-ecapa" / "embedding_model.ckpt").exists()
        if key == "lama":
            from .lama import LAMA_FILE

            return LAMA_FILE.exists() and LAMA_FILE.stat().st_size > 100_000_000
        if key == "vieneu":
            return _hf_cached(VIENEU_REPO)
        if key == "omnivoice":
            snap = _hf_snapshot((settings or {}).get("omnivoice_model") or "k2-fsa/OmniVoice")
            return bool(snap) and ((snap / "audio_tokenizer").is_dir() or _hf_cached(OMNI_TOKENIZER))
    except Exception:  # noqa: BLE001 – chưa có / chưa cài gói
        return False
    return False


def status_all(settings: dict | None = None) -> dict[str, str]:
    """{key: 'ready' | 'missing' | 'nopkg:<gói>'} – chạy ở luồng nền (có thể chậm vài giây)."""
    out = {}
    for m in MODELS:
        miss = missing_packages(m.key)
        out[m.key] = ("nopkg:" + ", ".join(miss)) if miss else ("ready" if is_ready(m.key, settings) else "missing")
    return out


# ------------------------------------------------------------------ tải về

def download(key: str, progress: Prog | None = None, cancel=None, settings: dict | None = None) -> None:
    from .progress_hook import tqdm_progress

    miss = missing_packages(key)
    if miss:
        raise RuntimeError(f"Chưa cài gói {', '.join(miss)} – chạy CaiDat_AI_NangCao.bat trước.")
    m = BY_KEY[key]
    label = f"Đang tải {m.name}"
    if progress:
        progress(0.0, f"{label}: đang kết nối…")

    if key in ("sep_roformer", "sep_mdx"):
        from audio_separator.separator import Separator

        from .separate import SEP_DIR

        SEP_DIR.mkdir(parents=True, exist_ok=True)
        before = {p.name for p in SEP_DIR.iterdir()}
        sep = Separator(model_file_dir=str(SEP_DIR), log_level=40)
        try:
            with tqdm_progress(progress, 0.0, 1.0, label, cancel, download=label):
                sep.download_model_files(_sep_file(key))
        except BaseException:
            # file tải dở bị thư viện coi là "đã có" -> xoá để lần sau tải lại
            for p in SEP_DIR.iterdir():
                if p.name not in before:
                    p.unlink(missing_ok=True)
            raise
    elif key in ("sensevoice", "paraformer", "align"):
        from modelscope.hub.snapshot_download import snapshot_download

        ids = {"sensevoice": ["sensevoice", "fsmn-vad"], "align": ["fa-zh"]}.get(
            key, ["paraformer-zh", "fsmn-vad", "ct-punc", "cam++"])
        for n, k in enumerate(ids):
            lo, hi = n / len(ids), (n + 1) / len(ids)
            with tqdm_progress(progress, lo, hi, f"{label} ({n + 1}/{len(ids)})", cancel,
                               download=f"{label} ({n + 1}/{len(ids)})"):
                snapshot_download(MS_IDS[k])
    elif key == "whisper":
        from faster_whisper.utils import download_model

        name = (settings or {}).get("asr_model", "large-v3")
        with tqdm_progress(progress, 0.0, 1.0, label, cancel, download=label):
            download_model(name, cache_dir=str(MODELS_DIR))
    elif key == "ecapa":
        from .diarize import _encoder

        with tqdm_progress(progress, 0.0, 1.0, label, cancel, download=label):
            _encoder()
    elif key == "lama":
        from .lama import ensure_model

        ensure_model(progress, cancel)
    elif key == "omnivoice":
        from huggingface_hub import snapshot_download

        st = settings or {}
        repo = st.get("omnivoice_model") or "k2-fsa/OmniVoice"
        token = st.get("hf_token") or None
        try:
            with tqdm_progress(progress, 0.0, 0.9, label, cancel, download=label):
                d = Path(snapshot_download(repo, token=token))
            if not (d / "audio_tokenizer").is_dir():
                with tqdm_progress(progress, 0.9, 1.0, label, cancel, download=label + " (bộ mã hoá âm thanh)"):
                    snapshot_download(OMNI_TOKENIZER, token=token)
        except Exception as e:  # noqa: BLE001
            msg = str(e)
            if any(x in msg for x in ("401", "403", "gated", "Gated", "restricted")):
                raise RuntimeError("Mô hình này cần đồng ý điều khoản: đăng nhập huggingface.co, mở trang "
                                   f"{repo}, bấm đồng ý, rồi dán token (huggingface.co/settings/tokens) vào "
                                   "Cài đặt → HuggingFace token.") from None
            raise
    elif key == "vieneu":
        from .tts import _vieneu

        _vieneu((settings or {}).get("vieneu_mode", "v3turbo")).ensure_started(progress, cancel)
    if progress:
        progress(1.0, f"Đã tải xong {m.name}")
