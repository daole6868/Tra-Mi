"""Tách âm thanh thành GIỌNG NÓI (vocals) và NHẠC NỀN + HIỆU ỨNG (instrumental).

Dùng thư viện audio-separator (github.com/nomadkaraoke/python-audio-separator, mô hình cộng đồng UVR):
  - Mel-RoFormer Kim: điểm tách giọng cao nhất trong bảng đo của thư viện (SDR giọng 12,6), nên chạy GPU NVIDIA.
  - Kết hợp BS + Mel-RoFormer: chạy cả 2 mô hình rồi lấy trung bình -> sạch nhất, chậm gấp đôi.
  - BS-RoFormer: rất tốt (SDR 11,8).  MDX-Net Voc FT (ONNX): nhẹ, dùng được trên CPU.
  - HT-Demucs (Meta, MIT): 1 mô hình nhẹ / bản FT 4 mô hình (SDR giọng 9,9 / 10,8) – học trên nhạc, xuất 4 phần (giọng,
    trống, bass, nhạc khác) -> 3 phần nhạc trộn lại thành nhạc nền.
Mô hình tự tải từ GitHub về thư mục data/models/separator ở lần đầu (hoặc tải trước ở tab Tài liệu).
"""
from __future__ import annotations

import shutil
import subprocess
import wave
from pathlib import Path
from typing import Callable

import numpy as np

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
    ("htdemucs.yaml", "HT-Demucs – nhanh, nhẹ (1 mô hình)"),
]
SEP_DIR = MODELS_DIR / "separator"
# file trọng số của từng mô hình Demucs (thư viện tải từ máy chủ của Meta; .yaml chỉ là file cấu hình nhỏ)
DEMUCS_FILES = {
    "htdemucs.yaml": ("955717e8-8726e21a.th",),
    "htdemucs_ft.yaml": ("f7e0c4bc-ba3fe64a.th", "d12395a8-e57c48e6.th", "92cfc3b6-ef3bcb9c.th",
                         "04573f0d-f3cf25b2.th"),
}
# Demucs chạy 1 lượt (thư viện mặc định 2 lượt dời giờ ngẫu nhiên rồi lấy trung bình -> chậm gấp đôi, sạch hơn rất ít)
DEMUCS_PARAMS = {"segment_size": "Default", "shifts": 0, "overlap": 0.25, "segments_enabled": True}
# Thư viện tách giọng giữ CẢ BÀI trong RAM nhiều lần (bản gốc + kết quả + bộ đếm cho 2 phần × 2 kênh, rồi các bản sao
# khi ghi file): phim 1,5 giờ ~12–15 GB, "Kết hợp" còn hơn -> máy 32 GB đầy RAM. Phim dài: tách từng đoạn 10 phút
# (RAM ~2 GB dù phim dài bao nhiêu), 2 đoạn liền chồng 6 s và nối mượt ở 4 s giữa (bỏ 1 s mép – mép đoạn tách kém hơn)
CHUNK_SEC = 600.0
OVERLAP_SEC = 6.0
MARGIN_SEC = 1.0
SR44 = 44100


UPGRADE_CMD = ".venv\\Scripts\\python -m pip install -U \"audio-separator>=0.44\" -c torch-constraints.txt"


def model_list(model: str) -> list[str]:
    """"ens:a.ckpt+b.ckpt" -> [a, b] (kết hợp nhiều mô hình); tên thường -> [tên]."""
    return model[4:].split("+") if model.startswith("ens:") else [model]


def plan_chunks(total: int, sr: int = SR44, chunk: float | None = None,
                over: float | None = None) -> list[tuple[int, int]]:
    """Các đoạn [đầu, cuối) (theo mẫu) để tách từng phần; đoạn cuối ngắn hơn 1/3 đoạn thì gộp vào đoạn trước."""
    cf, of = int((chunk or CHUNK_SEC) * sr), int((over or OVERLAP_SEC) * sr)
    starts = [0]
    while starts[-1] + cf + cf // 3 < total:
        starts.append(starts[-1] + cf)
    return [(st, min(total, starts[i + 1] + of) if i + 1 < len(starts) else total) for i, st in enumerate(starts)]


def _fade(n: int, margin: int) -> np.ndarray:
    """Trọng số đoạn SAU trong vùng chồng: 0 ở mép đầu, tăng đều, 1 ở mép cuối."""
    m = min(margin, n // 4)
    w = np.zeros(n, np.float32)
    w[n - m:] = 1.0
    if n - 2 * m > 0:
        w[m:n - m] = np.linspace(0.0, 1.0, n - 2 * m, dtype=np.float32)
    return w[:, None]


class _Joiner:
    """Ghi dần các đoạn đã tách (stereo 44,1 kHz) ra 1 file wav 16 bit, nối mượt chỗ 2 đoạn chồng nhau."""

    def __init__(self, path: Path, sr: int = SR44, ch: int = 2):
        self.w = wave.open(str(path), "wb")
        self.w.setnchannels(ch)
        self.w.setsampwidth(2)
        self.w.setframerate(sr)
        self.tail: np.ndarray | None = None
        self.margin = int(MARGIN_SEC * sr)

    def _write(self, a: np.ndarray) -> None:
        if len(a):
            self.w.writeframes((np.clip(a, -1.0, 1.0) * 32767).astype(np.int16).tobytes())

    def add(self, a: np.ndarray, keep: int) -> None:
        """a: cả đoạn (n, kênh); keep: số mẫu cuối đoạn trùng với đoạn sau (giữ lại để nối)."""
        if self.tail is not None:
            o = min(len(self.tail), len(a))
            w = _fade(o, self.margin)
            self._write(self.tail[:o] * (1 - w) + a[:o] * w)
            a = a[o:]
        keep = min(keep, len(a))
        self._write(a[:len(a) - keep])
        self.tail = a[len(a) - keep:].copy() if keep else None

    def close(self) -> None:
        if self.tail is not None:
            self._write(self.tail)
        self.w.close()


def _read_stem(path: Path, n: int) -> np.ndarray:
    """File phần đã tách -> (n, 2) float32 (đúng n mẫu; định dạng lạ thì giải mã bằng FFmpeg)."""
    try:
        with wave.open(str(path), "rb") as w:
            if w.getsampwidth() != 2 or w.getframerate() != SR44 or w.getnchannels() not in (1, 2):
                raise wave.Error("định dạng khác")
            a = np.frombuffer(w.readframes(w.getnframes()), np.int16).reshape(-1, w.getnchannels())
    except (wave.Error, EOFError):
        raw = subprocess.run([ff.ffmpeg_bin(), "-hide_banner", "-loglevel", "error", "-i", str(path), "-ac", "2",
                              "-ar", str(SR44), "-f", "s16le", "pipe:1"], capture_output=True,
                             creationflags=ff._NO_WINDOW).stdout
        a = np.frombuffer(raw, np.int16).reshape(-1, 2)
    if a.shape[1] == 1:
        a = np.repeat(a, 2, axis=1)
    out = np.zeros((n, 2), np.float32)
    m = min(n, len(a))
    out[:m] = a[:m].astype(np.float32) / 32768.0
    return out


def timeline_maps(c: np.ndarray, p: np.ndarray, d: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """(xp, fp) để đổi GIỜ THẬT -> giây theo số mẫu (np.interp(t, xp, fp)): đầu mỗi khung âm thanh; chỗ giờ nhảy cóc
    (âm thanh bị hụt) thêm điểm cuối khung -> trong khoảng hụt giây theo mẫu đứng yên = lặng (như FFmpeg async)."""
    gap = p[1:] - (p[:-1] + d[:-1]) > 0.02
    xp = np.concatenate([p, (p[:-1] + d[:-1])[gap]])
    fp = np.concatenate([c, (c[:-1] + d[:-1])[gap]])
    o = np.argsort(xp, kind="stable")
    xp, fp = xp[o], fp[o]
    ok = np.concatenate([[True], np.diff(xp) > 1e-6])
    return xp[ok], np.maximum.accumulate(fp[ok])


def retime_wav(src: Path, dst: Path, c: np.ndarray, p: np.ndarray, d: np.ndarray, progress=None,
               block: float = 30.0) -> None:
    """Bản tách giọng làm trước 2.0.92 (mẫu nối liền) -> đúng giờ thật của video: mỗi mẫu ra ở giờ t lấy mẫu vào ở giây
    theo số mẫu c(t) (nội suy), chỗ âm thanh bị hụt thì lặng. Đọc / ghi từng khối (RAM thấp, phim 3 giờ ~1 phút)."""
    xp, fp = timeline_maps(c, p, d)
    total = float(p[-1] + d[-1])
    n_out = int(round(total * SR44))
    dec = subprocess.Popen([ff.ffmpeg_bin(), "-hide_banner", "-loglevel", "error", "-i", str(src), "-ac", "2",
                            "-ar", str(SR44), "-f", "s16le", "pipe:1"], stdout=subprocess.PIPE,
                           creationflags=ff._NO_WINDOW)
    buf = np.zeros((0, 2), np.float32)
    b0 = 0                    # chỉ số mẫu vào của buf[0]
    eof = False
    bs = int(block * SR44)
    try:
        with wave.open(str(dst), "wb") as w:
            w.setnchannels(2)
            w.setsampwidth(2)
            w.setframerate(SR44)
            for j0 in range(0, n_out, bs):
                j1 = min(n_out, j0 + bs)
                t = np.arange(j0, j1 + 1, dtype=np.float64) / SR44
                pos = np.interp(t, xp, fp) * SR44
                lo, hi = int(pos[0]), int(np.ceil(pos[-1])) + 2
                while not eof and b0 + len(buf) < hi:
                    raw = dec.stdout.read(SR44 * 4 * 10)
                    if not raw:
                        eof = True
                        break
                    buf = np.concatenate([buf, np.frombuffer(raw, np.int16).reshape(-1, 2).astype(np.float32)])
                if lo > b0:
                    cut = min(lo - b0, len(buf))
                    buf, b0 = buf[cut:], b0 + cut
                rel = pos[:-1] - b0
                i0 = np.clip(rel.astype(np.int64), 0, max(0, len(buf) - 2))
                fr = np.clip(rel - i0, 0.0, 1.0)[:, None].astype(np.float32)
                if len(buf) >= 2:
                    y = buf[i0] * (1 - fr) + buf[i0 + 1] * fr
                else:
                    y = np.zeros((len(rel), 2), np.float32)
                stall = np.diff(pos) < 0.5          # giây theo mẫu đứng yên: âm thanh bị hụt / ngoài phạm vi -> lặng
                y[stall] = 0
                w.writeframes(np.clip(y, -32768, 32767).astype(np.int16).tobytes())
                if progress:
                    progress(j1 / n_out, "Đang căn giờ bản tách giọng…")
    finally:
        if dec.poll() is None:
            dec.kill()
        dec.wait()


def retime_stems(src: str | Path, out_dir: str | Path, progress=None, tl=None) -> dict | None:
    """Bản tách giọng / nhạc nền làm trước 2.0.92: video có âm thanh lệch giờ -> kéo giãn cho đúng giờ của hình (không
    tách lại). Ghi dấu timeline.txt. Trả độ lệch đã sửa (None = không lệch / không đọc được giờ)."""
    out_dir = Path(out_dir)
    tl = tl or ff.audio_timeline(src)
    mark = out_dir / "timeline.txt"
    if tl is None:
        mark.write_text(str(ff.TIMELINE), "utf-8")
        return None
    c, p, d = tl
    drift = float(np.max(np.abs(p - c)))
    if drift > ff.SYNC_MAX:
        names = [x for x in ("vocals.wav", "instrumental.wav") if (out_dir / x).exists()]
        for k, name in enumerate(names):
            tmp = out_dir / f"_retime_{name}"
            retime_wav(out_dir / name, tmp, c, p, d,
                       (lambda x, m, k=k: progress((k + x) / len(names), m)) if progress else None)
            ff.replace_file(tmp, out_dir / name, wait=120)   # trình phát đang giải mã bản cũ -> chờ
    mark.write_text(str(ff.TIMELINE), "utf-8")
    return {"max": round(drift, 3), "end": round(float(np.median((p - c)[-50:])), 3)} if drift > ff.SYNC_MAX else None


def _average(srcs: list[Path], out: Path, block: int = SR44 * 30) -> None:
    """Trung bình các bản tách của nhiều mô hình (= "avg_wave" của audio-separator), đọc / ghi từng khối 30 s."""
    ins = [wave.open(str(x), "rb") for x in srcs]
    try:
        with wave.open(str(out), "wb") as w:
            w.setnchannels(2)
            w.setsampwidth(2)
            w.setframerate(SR44)
            n, done = max(i.getnframes() for i in ins), 0
            while done < n:
                m = min(block, n - done)
                acc = np.zeros((m, 2), np.float32)
                for i in ins:
                    a = np.frombuffer(i.readframes(m), np.int16).reshape(-1, 2)
                    acc[:len(a)] += a
                w.writeframes(np.clip(acc / len(ins), -32768, 32767).astype(np.int16).tobytes())
                done += m
    finally:
        for i in ins:
            i.close()


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
        if not (out_dir / "timeline.txt").exists():   # bản tách trước 2.0.92: kéo giãn cho đúng giờ hình nếu lệch
            retime_stems(src, out_dir, progress)
        return vocals, inst  # đã tách trước đó

    info = ff.probe(src)
    if not info.has_audio:
        raise RuntimeError("Video không có âm thanh để tách.")
    if progress:
        progress(0.02, "Đang chuẩn bị âm thanh để tách giọng…")
    mix = out_dir / "mix44k.wav"
    ff.run(["-i", str(src), "-vn"] + ff.af_args(ff.audio_sync(src, SR44)) + ["-ac", "2", "-ar", "44100", "-c:a",
           "pcm_s16le", str(mix)], info.duration, None, cancel)
    if cancel and cancel():
        raise ff.Cancelled()
    with wave.open(str(mix), "rb") as w:
        total = w.getnframes()
    chunks = plan_chunks(total)
    SEP_DIR.mkdir(parents=True, exist_ok=True)
    tmp = out_dir / "_sep"
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir()
    if len(chunks) == 1:
        found_v, found_i, model = _separate_whole(mix, tmp, model, progress, cancel)
    else:
        found_v, found_i, model = _separate_chunked(mix, tmp, model, chunks, progress, cancel)
    shutil.move(str(found_v), vocals)
    shutil.move(str(found_i), inst)
    shutil.rmtree(tmp, ignore_errors=True)
    mix.unlink(missing_ok=True)
    stamp.write_text(model, "utf-8")
    (out_dir / "timeline.txt").write_text(str(ff.TIMELINE), "utf-8")
    if progress:
        progress(1.0, "Đã tách xong giọng nói và nhạc nền")
    return vocals, inst


def _names(model: str) -> dict:
    names = {"Vocals": "vocals", "Instrumental": "instrumental"}
    if not model.endswith(".yaml"):   # Mel-RoFormer xuất "vocals" + "other" (= nhạc nền); Demucs xuất 4 phần
        names["Other"] = "instrumental"
    return names


def _load(sep, model: str, models: list[str], progress, cancel, lo: float, hi: float) -> str:
    """Nạp mô hình (danh sách = kết hợp). Bản audio-separator cũ thiếu mô hình mới -> BS-RoFormer."""
    from .progress_hook import tqdm_progress

    if progress:
        progress(lo, f"Đang nạp mô hình tách giọng {model}…")
    with tqdm_progress(progress, lo, hi, "Đang nạp mô hình tách giọng", cancel,
                       download="Đang tải mô hình tách giọng"):
        try:
            sep.load_model(model_filename=models if len(models) > 1 else models[0])
        except ValueError as e:
            if "not found" not in str(e) or model == BS_ROFORMER:
                raise
            # bản audio-separator cũ chưa có mô hình mới -> dùng BS-RoFormer để không hỏng cả quy trình
            if progress:
                progress(lo, "audio-separator cũ chưa có mô hình này -> dùng BS-RoFormer (nên cập nhật thư viện)")
            model = BS_ROFORMER
            sep.load_model(model_filename=model)
    return model


def _free(sep) -> None:
    """Mô hình tách giọng chạy NGAY trong tiến trình giao diện: bỏ đi + trả bộ nhớ đệm CUDA, nếu không vài GB cứ nằm
    trên card khi Qwen3-ASR (tiến trình riêng) nạp -> tràn sang RAM, nghe chậm hẳn."""
    for attr in ("model_instance", "model_instances"):
        try:
            setattr(sep, attr, None)
        except Exception:  # noqa: BLE001
            pass
    from .gpu import free_cache

    free_cache()


def _find(outs: list, tmp: Path) -> tuple[Path, Path]:
    """(vocals, nhạc nền) trong các file thư viện vừa ghi – nhận theo ĐÚNG tên phần (tên mặc định có cả tên mô hình,
    VD "..._(Other)_vocals_mel_band..."); mô hình nhiều phần (Demucs) -> trộn phần còn lại thành nhạc nền."""
    paths = [Path(o) if Path(o).is_absolute() else tmp / o for o in outs]
    found_v = next((p for p in paths if p.stem.lower() == "vocals"), None) or \
        next((p for p in paths if "(vocals)" in p.name.lower()), None)
    found_i = next((p for p in paths if p.stem.lower() == "instrumental"), None) or \
        next((p for p in paths if "(instrumental)" in p.name.lower() or "(no vocals)" in p.name.lower()), None)
    if not found_v:
        raise RuntimeError(f"Tách giọng thất bại, không tìm thấy file vocals: {outs}")
    if not found_i:
        others = [p for p in paths if p != found_v]
        if not others:
            raise RuntimeError("Mô hình không xuất được phần nhạc nền.")
        found_i = tmp / "instrumental_mix.wav"
        args: list[str] = []
        for p in others:
            args += ["-i", str(p)]
        args += ["-filter_complex", f"amix=inputs={len(others)}:normalize=0", str(found_i)]
        ff.run(args)
        for p in others:
            p.unlink(missing_ok=True)
    return found_v, found_i


def _separate_whole(mix: Path, tmp: Path, model: str, progress, cancel) -> tuple[Path, Path, str]:
    """Phim ngắn (≤ ~13 phút): tách cả bài 1 lần (kết hợp nhiều mô hình do thư viện làm)."""
    from audio_separator.separator import Separator

    from .progress_hook import tqdm_progress

    models = model_list(model)
    try:
        sep = Separator(output_dir=str(tmp), model_file_dir=str(SEP_DIR), output_format="WAV", log_level=40,
                        demucs_params=dict(DEMUCS_PARAMS),
                        **({"ensemble_algorithm": "avg_wave"} if len(models) > 1 else {}))
    except TypeError:
        raise RuntimeError("Bản audio-separator đang cài chưa hỗ trợ kết hợp 2 mô hình. Cập nhật bằng lệnh:\n"
                           + UPGRADE_CMD) from None
    model = _load(sep, model, models, progress, cancel, 0.08, 0.15)
    if progress:
        progress(0.15, "Đang tách giọng nói khỏi nhạc nền (GPU nhanh hơn CPU rất nhiều)…")
    try:
        with tqdm_progress(progress, 0.15, 0.97, "Đang tách giọng nói khỏi nhạc nền – đoạn", cancel,
                           download="Đang tải mô hình tách giọng"):
            outs = sep.separate(str(mix), _names(model))
    finally:
        _free(sep)
        del sep
    v, i = _find(outs, tmp)
    return v, i, model


def _separate_chunked(mix: Path, tmp: Path, model: str, chunks: list[tuple[int, int]], progress,
                      cancel) -> tuple[Path, Path, str]:
    """Phim dài: mỗi mô hình nạp 1 lần, tách lần lượt từng đoạn ~10 phút, ghi nối dần ra file -> RAM thấp; kết hợp
    nhiều mô hình = trung bình các bản tách (như "avg_wave" của thư viện, đọc / ghi từng khối)."""
    from audio_separator.separator import Separator

    from .progress_hook import tqdm_progress

    models = model_list(model)
    n, steps = len(chunks), len(models) * len(chunks)
    piece = tmp / "piece.wav"
    results: list[tuple[Path, Path]] = []
    used: list[str] = []
    for j, m in enumerate(models):
        sep = Separator(output_dir=str(tmp), model_file_dir=str(SEP_DIR), output_format="WAV", log_level=40,
                        demucs_params=dict(DEMUCS_PARAMS))
        lo = 0.08 + 0.89 * j / len(models)
        m = _load(sep, m, [m], progress, cancel, lo, lo + 0.02)
        used.append(m)
        jv, ji = _Joiner(tmp / f"m{j}_vocals.wav"), _Joiner(tmp / f"m{j}_inst.wav")
        try:
            with wave.open(str(mix), "rb") as src:
                for k, (a, b) in enumerate(chunks):
                    if cancel and cancel():
                        raise ff.Cancelled()
                    src.setpos(a)
                    with wave.open(str(piece), "wb") as w:
                        w.setnchannels(src.getnchannels())
                        w.setsampwidth(2)
                        w.setframerate(SR44)
                        w.writeframes(src.readframes(b - a))
                    step = j * n + k
                    p0 = 0.1 + 0.87 * step / steps
                    who = f" (mô hình {j + 1}/{len(models)})" if len(models) > 1 else ""
                    if progress:
                        progress(p0, f"Đang tách giọng phần {k + 1}/{n}{who}…")
                    with tqdm_progress(progress, p0, 0.1 + 0.87 * (step + 1) / steps,
                                       f"Đang tách giọng phần {k + 1}/{n}{who} – đoạn", cancel,
                                       download="Đang tải mô hình tách giọng"):
                        outs = sep.separate(str(piece), _names(m))
                    v, i = _find(outs, tmp)
                    keep = (b - chunks[k + 1][0]) if k + 1 < n else 0   # phần trùng với đoạn sau
                    jv.add(_read_stem(v, b - a), keep)
                    ji.add(_read_stem(i, b - a), keep)
                    for f in (v, i):
                        f.unlink(missing_ok=True)
        finally:
            jv.close()
            ji.close()
            _free(sep)
            del sep
        results.append((tmp / f"m{j}_vocals.wav", tmp / f"m{j}_inst.wav"))
    piece.unlink(missing_ok=True)
    if len(results) == 1:
        return results[0][0], results[0][1], used[0]
    if progress:
        progress(0.98, "Đang kết hợp kết quả các mô hình…")
    _average([r[0] for r in results], tmp / "vocals_avg.wav")
    _average([r[1] for r in results], tmp / "inst_avg.wav")
    return tmp / "vocals_avg.wav", tmp / "inst_avg.wav", (model if used == models else "ens:" + "+".join(used))
