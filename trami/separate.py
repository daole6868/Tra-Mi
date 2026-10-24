"""Tách âm thanh thành GIỌNG NÓI (vocals) và NHẠC NỀN + HIỆU ỨNG (instrumental).

Dùng thư viện audio-separator (github.com/nomadkaraoke/python-audio-separator, mô hình cộng đồng UVR):
  - Mel-RoFormer Kim: điểm tách giọng cao nhất trong bảng đo của thư viện (SDR giọng 12,6), nên chạy GPU NVIDIA.
  - Kết hợp BS + Mel-RoFormer: chạy cả 2 mô hình rồi lấy trung bình -> sạch nhất, chậm gấp đôi.
  - BS-RoFormer: rất tốt (SDR 11,8).  MDX-Net Voc FT (ONNX): nhẹ, dùng được trên CPU.
Mô hình tự tải từ GitHub về thư mục data/models/separator ở lần đầu (hoặc tải trước ở tab Tài liệu).
"""
from __future__ import annotations

import shutil
from pathlib import Path
from typing import Callable

from . import ffmpeg as ff
from .config import MODELS_DIR

BS_ROFORMER = "model_bs_roformer_ep_317_sdr_12.9755.ckpt"
KIM_ROFORMER = "vocals_mel_band_roformer.ckpt"
ENSEMBLE = f"ens:{BS_ROFORMER}+{KIM_ROFORMER}"
SEP_MODELS = [
    (KIM_ROFORMER, "Mel-RoFormer Kim – tách giọng sạch nhất (khuyên dùng với GPU)"),
    (ENSEMBLE, "Kết hợp BS + Mel-RoFormer – chính xác nhất, chậm gấp đôi"),
    (BS_ROFORMER, "BS-RoFormer – rất tốt"),
    ("model_mel_band_roformer_ep_3005_sdr_11.4360.ckpt", "Mel-RoFormer 3005 – nhẹ hơn"),
    ("UVR-MDX-NET-Voc_FT.onnx", "MDX-Net Voc FT – nhanh, hợp máy chỉ có CPU"),
    ("htdemucs_ft.yaml", "HT-Demucs FT – ổn định, tách nhiều loại nhạc"),
]
SEP_DIR = MODELS_DIR / "separator"


UPGRADE_CMD = ".venv\\Scripts\\python -m pip install -U \"audio-separator>=0.44\" -c torch-constraints.txt"


def model_list(model: str) -> list[str]:
    """"ens:a.ckpt+b.ckpt" -> [a, b] (kết hợp nhiều mô hình); tên thường -> [tên]."""
    return model[4:].split("+") if model.startswith("ens:") else [model]


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
        progress(0.08, f"Đang nạp mô hình tách giọng {model}…")

    from audio_separator.separator import Separator

    SEP_DIR.mkdir(parents=True, exist_ok=True)
    tmp = out_dir / "_sep"
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir()
    from .progress_hook import tqdm_progress

    models = model_list(model)
    try:
        sep = Separator(output_dir=str(tmp), model_file_dir=str(SEP_DIR), output_format="WAV", log_level=40,
                        **({"ensemble_algorithm": "avg_wave"} if len(models) > 1 else {}))
    except TypeError:
        raise RuntimeError("Bản audio-separator đang cài chưa hỗ trợ kết hợp 2 mô hình. Cập nhật bằng lệnh:\n"
                           + UPGRADE_CMD) from None
    with tqdm_progress(progress, 0.08, 0.15, "Đang nạp mô hình tách giọng", cancel,
                       download="Đang tải mô hình tách giọng"):
        try:
            sep.load_model(model_filename=models if len(models) > 1 else models[0])
        except ValueError as e:
            if "not found" not in str(e) or model == BS_ROFORMER:
                raise
            # bản audio-separator cũ chưa có mô hình mới -> dùng BS-RoFormer để không hỏng cả quy trình
            if progress:
                progress(0.1, "audio-separator cũ chưa có mô hình này -> dùng BS-RoFormer (nên cập nhật thư viện)")
            model = BS_ROFORMER
            sep.load_model(model_filename=model)
    if progress:
        progress(0.15, "Đang tách giọng nói khỏi nhạc nền (GPU nhanh hơn CPU rất nhiều)…")
    names = {"Vocals": "vocals", "Instrumental": "instrumental"}
    if not model.endswith(".yaml"):   # Mel-RoFormer xuất "vocals" + "other" (= nhạc nền); Demucs xuất 4 phần
        names["Other"] = "instrumental"
    try:
        with tqdm_progress(progress, 0.15, 0.97, "Đang tách giọng nói khỏi nhạc nền – đoạn", cancel,
                           download="Đang tải mô hình tách giọng"):
            outs = sep.separate(str(mix), names)
    finally:
        # mô hình tách giọng chạy NGAY trong tiến trình giao diện: bỏ đi + trả bộ nhớ đệm CUDA, nếu không vài GB cứ
        # nằm trên card khi Qwen3-ASR (tiến trình riêng) nạp -> tràn sang RAM, nghe chậm hẳn
        for attr in ("model_instance", "model_instances"):
            try:
                setattr(sep, attr, None)
            except Exception:  # noqa: BLE001
                pass
        del sep
        from .gpu import free_cache

        free_cache()
    paths = [Path(o) if Path(o).is_absolute() else tmp / o for o in outs]
    # nhận file theo ĐÚNG tên phần (tên file mặc định có cả tên mô hình, VD "..._(Other)_vocals_mel_band...")
    found_v = next((p for p in paths if p.stem.lower() == "vocals"), None) or \
        next((p for p in paths if "(vocals)" in p.name.lower()), None)
    found_i = next((p for p in paths if p.stem.lower() == "instrumental"), None) or \
        next((p for p in paths if "(instrumental)" in p.name.lower() or "(no vocals)" in p.name.lower()), None)
    if not found_v:
        raise RuntimeError(f"Tách giọng thất bại, không tìm thấy file vocals: {outs}")
    if not found_i:  # một số mô hình (Demucs) xuất nhiều stem -> trộn phần còn lại thành nhạc nền
        others = [p for p in paths if p != found_v]
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
