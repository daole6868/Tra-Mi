"""Tách âm thanh thành GIỌNG NÓI (vocals) và NHẠC NỀN + HIỆU ỨNG (instrumental).

Dùng thư viện audio-separator (mô hình của cộng đồng UVR):
  - BS-RoFormer: chất lượng cao nhất, nên chạy GPU NVIDIA.
  - MDX-Net Voc FT (ONNX): nhẹ, dùng được trên CPU.
Mô hình tự tải về thư mục data/models/separator ở lần đầu.
"""
from __future__ import annotations

import shutil
from pathlib import Path
from typing import Callable

from . import ffmpeg as ff
from .config import MODELS_DIR

SEP_MODELS = [
    ("model_bs_roformer_ep_317_sdr_12.9755.ckpt", "BS-RoFormer – tách sạch nhất (khuyên dùng với GPU)"),
    ("model_mel_band_roformer_ep_3005_sdr_11.4360.ckpt", "Mel-RoFormer – rất tốt, nhẹ hơn một chút"),
    ("UVR-MDX-NET-Voc_FT.onnx", "MDX-Net Voc FT – nhanh, hợp máy chỉ có CPU"),
    ("htdemucs_ft.yaml", "HT-Demucs FT – ổn định, tách nhiều loại nhạc"),
]
SEP_DIR = MODELS_DIR / "separator"


def available() -> bool:
    from .config import installed

    return installed("audio_separator", "torch")


def separate(src: str | Path, out_dir: str | Path, model: str | None = None,
             progress: Callable[[float, str], None] | None = None, cancel=None) -> tuple[Path, Path]:
    """Trả về (vocals.wav, instrumental.wav) có cùng độ dài với video gốc."""
    if not available():
        raise RuntimeError("Chưa cài bộ tách giọng. Chạy CaiDat_Windows.bat và chọn cài AI nâng cao "
                           "(hoặc: pip install audio-separator).")
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    vocals, inst = out_dir / "vocals.wav", out_dir / "instrumental.wav"
    model = model or SEP_MODELS[0][0]
    stamp = out_dir / "model.txt"
    if vocals.exists() and inst.exists() and stamp.exists() and stamp.read_text("utf-8") == model:
        return vocals, inst  # đã tách trước đó

    info = ff.probe(src)
    if not info.has_audio:
        raise RuntimeError("Video không có âm thanh để tách.")
    if progress:
        progress(0.02, "Đang chuẩn bị âm thanh để tách giọng…")
    mix = out_dir / "mix44k.wav"
    ff.run(["-i", str(src), "-vn", "-ac", "2", "-ar", "44100", "-c:a", "pcm_s16le", str(mix)],
           info.duration, None, cancel)
    if cancel and cancel():
        raise ff.Cancelled()
    if progress:
        progress(0.08, f"Đang tải mô hình tách giọng {model} (lần đầu có thể mất vài phút)…")

    from audio_separator.separator import Separator

    SEP_DIR.mkdir(parents=True, exist_ok=True)
    tmp = out_dir / "_sep"
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir()
    from .progress_hook import tqdm_progress

    sep = Separator(output_dir=str(tmp), model_file_dir=str(SEP_DIR), output_format="WAV", log_level=40)
    with tqdm_progress(progress, 0.08, 0.15, "Đang tải mô hình tách giọng", cancel,
                       download="Đang tải mô hình tách giọng"):
        sep.load_model(model_filename=model)
    if progress:
        progress(0.15, "Đang tách giọng nói khỏi nhạc nền (GPU nhanh hơn CPU rất nhiều)…")
    with tqdm_progress(progress, 0.15, 0.97, "Đang tách giọng nói khỏi nhạc nền – đoạn", cancel):
        outs = sep.separate(str(mix), {"Vocals": "vocals", "Instrumental": "instrumental"})
    found_v = found_i = None
    for o in outs:
        p = Path(o) if Path(o).is_absolute() else tmp / o
        name = p.name.lower()
        if "vocal" in name and found_v is None:
            found_v = p
        elif ("instrumental" in name or "no_vocal" in name or "other" in name) and found_i is None:
            found_i = p
    if not found_v:
        raise RuntimeError(f"Tách giọng thất bại, không tìm thấy file vocals: {outs}")
    if not found_i:  # một số mô hình (Demucs) xuất nhiều stem -> trộn phần còn lại thành nhạc nền
        others = [Path(o) if Path(o).is_absolute() else tmp / o for o in outs]
        others = [p for p in others if p != found_v]
        if not others:
            raise RuntimeError("Mô hình không xuất được phần nhạc nền.")
        found_i = tmp / "instrumental_mix.wav"
        args: list[str] = []
        for p in others:
            args += ["-i", str(p)]
        args += ["-filter_complex", f"amix=inputs={len(others)}:normalize=0", str(found_i)]
        ff.run(args)
    shutil.move(str(found_v), vocals)
    shutil.move(str(found_i), inst)
    shutil.rmtree(tmp, ignore_errors=True)
    mix.unlink(missing_ok=True)
    stamp.write_text(model, "utf-8")
    if progress:
        progress(1.0, "Đã tách xong giọng nói và nhạc nền")
    return vocals, inst
