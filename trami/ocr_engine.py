"""Bộ đọc chữ (OCR) cho phụ đề cứng – 2.1.31.

RapidOCR 3.x (Apache-2.0) + mô hình PP-OCR của PaddleOCR (Apache-2.0):
  * PP-OCRv6 small có sẵn trong gói rapidocr (không phải tải) – mặc định;
  * PP-OCRv6 medium / PP-OCRv5 server tải ở tab Tài liệu vào data/models/ocr (link + SHA256 lấy từ danh sách mô hình
    của chính gói rapidocr đã cài);
  * chưa cài rapidocr 3 -> RapidOCR 1.x (PP-OCRv4) như trước 2.1.31.
Chạy trên card khi onnxruntime là bản có CUDA (onnxruntime-gpu) hoặc DirectML (onnxruntime-directml), không thì CPU.
Tìm chữ (det) giới hạn cạnh dài DET_SIDE thay vì phóng cạnh NGẮN lên 736 như mặc định (dải phụ đề 1280×160 bị phóng
thành ~5900×736 -> chậm ~10 lần); đọc dòng (rec) gom cả lô, độ rộng ảnh làm tròn theo bậc -> ít kiểu kích thước."""
from __future__ import annotations

import hashlib
import importlib.util
import os
import sys
import threading
import time
from pathlib import Path
from typing import Callable

import numpy as np

from .config import MODELS_DIR, log_error, quiet_dll_errors

OCR_DIR = MODELS_DIR / "ocr"
MODELS = [
    ("v6_small", "PP-OCRv6 – có sẵn, nhanh và chuẩn (khuyên dùng)"),
    ("v6_medium", "PP-OCRv6 medium – kỹ hơn, chậm hơn (tải ở tab Tài liệu)"),
    ("v5_server", "PP-OCRv5 server – bản lớn chuyên chữ Trung (tải ở tab Tài liệu)"),
    ("v4", "PP-OCRv4 – bộ đọc cũ (RapidOCR 1.x)"),
]
# mã -> (bản PP-OCR, cỡ mô hình, mã mô hình tìm chữ, mã mô hình đọc chữ) trong default_models.yaml của rapidocr
SPEC = {
    "v6_small": ("PP-OCRv6", "small", "multi_PP-OCRv6_det_small", "multi_PP-OCRv6_rec_small"),
    "v6_medium": ("PP-OCRv6", "medium", "multi_PP-OCRv6_det_medium", "multi_PP-OCRv6_rec_medium"),
    "v5_server": ("PP-OCRv5", "server", "ch_PP-OCRv5_det_server", "ch_PP-OCRv5_rec_server"),
}
DEVICES = [("auto", "Tự động – card nếu có"), ("cpu", "CPU")]
DET_SIDE = 960          # tìm chữ: cạnh dài tối đa (điểm ảnh) – cả khung hình (dò vùng phụ đề)
SCAN_SIDE = 736         # tìm chữ trên dải phụ đề khi quét (dải 1280 điểm: chữ vẫn cao ≥ 20 điểm; nhanh hơn ~1,6 lần)
REC_BUCKET = 2.0        # đọc dòng: tỉ lệ rộng / cao làm tròn lên bội số này (card không phải dò lại cho mỗi cỡ ảnh)
REC_PAD = 127           # giá trị lấp phần thêm = đúng giá trị bộ đọc tự lấp (0 sau chuẩn hoá)
WIDE = 8.0              # ảnh rộng hơn 8 lần chiều cao -> thêm lề trên / dưới trước khi tìm chữ (như RapidOCR)
GPU_PKG = {"cuda": "onnxruntime-gpu", "dml": "onnxruntime-directml"}


def has_v3() -> bool:
    """Đã cài rapidocr 3.x (không import – import kéo theo onnxruntime)."""
    try:
        if importlib.util.find_spec("rapidocr") is None:
            return False
        from importlib.metadata import version

        return int(version("rapidocr").split(".")[0]) >= 3
    except Exception:  # noqa: BLE001 – exe không còn thông tin gói: có thư mục models là đủ
        return _pkg_dir() is not None


def has_v1() -> bool:
    try:
        return importlib.util.find_spec("rapidocr_onnxruntime") is not None
    except Exception:  # noqa: BLE001
        return False


def _pkg_dir() -> Path | None:
    try:
        spec = importlib.util.find_spec("rapidocr")
        if spec and spec.origin:
            d = Path(spec.origin).parent
            return d if (d / "default_models.yaml").exists() else None
    except Exception:  # noqa: BLE001
        pass
    return None


_REG: dict | None = None


def _registry() -> dict:
    """{mã mô hình: {"model_dir": link, "SHA256": …}} cho onnxruntime – đọc từ default_models.yaml của gói rapidocr."""
    global _REG
    if _REG is None:
        reg: dict = {}
        d = _pkg_dir()
        if d is not None:
            try:
                import yaml

                data = yaml.safe_load((d / "default_models.yaml").read_text("utf-8")) or {}
                for ver in (data.get("onnxruntime") or {}).values():
                    for task in (ver or {}).values():
                        for k, v in (task or {}).items():
                            if isinstance(v, dict) and v.get("model_dir"):
                                reg[k] = v
            except Exception:  # noqa: BLE001
                reg = {}
        _REG = reg
    return _REG


def _files(key: str) -> list[tuple[str, str, Path]]:
    """[(link, sha256, file trên máy)] của 2 mô hình (tìm chữ, đọc chữ). File có sẵn trong gói rapidocr dùng luôn."""
    spec = SPEC.get(key)
    if not spec:
        return []
    reg, pkg = _registry(), _pkg_dir()
    out = []
    for mk in spec[2:]:
        info = reg.get(mk) or {}
        url = str(info.get("model_dir") or "")
        name = Path(url).name if url else mk + ".onnx"
        local = pkg / "models" / name if pkg else None
        out.append((url, str(info.get("SHA256") or ""), local if local and local.exists() else OCR_DIR / name))
    return out


def model_ready(key: str) -> bool:
    if key == "v4":
        return has_v1()
    fs = _files(key)
    return bool(fs) and has_v3() and all(p.exists() and p.stat().st_size > 500_000 for _, _, p in fs)


def bundled(key: str) -> bool:
    pkg = _pkg_dir()
    return bool(pkg) and all(pkg in p.parents for _, _, p in _files(key)) and model_ready(key)


def download(key: str, progress: Callable[[float, str], None] | None = None, cancel=None) -> None:
    """Tải 2 file mô hình (nhiều luồng, tải tiếp được) vào data/models/ocr + kiểm tra SHA256."""
    from . import fastdl

    if not has_v3():
        raise RuntimeError("Chưa cài RapidOCR 3: chạy lệnh  .venv\\Scripts\\python -m pip install -U rapidocr  rồi mở lại tool.")
    todo = [(u, h, p) for u, h, p in _files(key) if not (p.exists() and p.stat().st_size > 500_000)]
    if not todo:
        return
    OCR_DIR.mkdir(parents=True, exist_ok=True)
    for n, (url, sha, dst) in enumerate(todo):
        if not url:
            raise RuntimeError("Gói rapidocr đang cài không có link tải mô hình này – cập nhật: pip install -U rapidocr")
        got = {"b": 0, "t": 0}

        def on_bytes(b, total, n=n):
            got["b"] += b
            got["t"] = total or got["t"]
            if progress and got["t"]:
                f = min(1.0, got["b"] / got["t"])
                progress((n + f) / len(todo), f"Đang tải mô hình OCR {n + 1}/{len(todo)}: {int(f * 100)}% · "
                         f"{got['b'] / 1e6:.0f} / {got['t'] / 1e6:.0f} MB")
        fastdl.fetch([url], {}, None, dst, conns=4, on_bytes=on_bytes, cancel=cancel,
                     report=(lambda m: progress(n / len(todo), m)) if progress else None)
        if sha:
            h = hashlib.sha256()
            with open(dst, "rb") as f:
                for blk in iter(lambda: f.read(1 << 20), b""):
                    h.update(blk)
            if h.hexdigest().lower() != sha.lower():
                dst.unlink(missing_ok=True)
                raise RuntimeError(f"File mô hình {dst.name} tải về bị hỏng (sai mã kiểm tra) – bấm Tải về lần nữa.")


# ------------------------------------------------------------------ card đồ hoạ

def ort_dists() -> list[str]:
    """Các bản onnxruntime đang cài (đọc thông tin gói – KHÔNG import, để nút cài bản card gỡ được bản cũ)."""
    out = []
    try:
        from importlib.metadata import PackageNotFoundError, version
    except ImportError:   # pragma: no cover
        return out
    for d in ("onnxruntime-gpu", "onnxruntime-directml", "onnxruntime"):
        try:
            version(d)
            out.append(d)
        except PackageNotFoundError:
            pass
        except Exception:  # noqa: BLE001
            pass
    return out


def ort_kind() -> str:
    """Bản onnxruntime đang cài chạy được trên: "cuda" | "dml" | "cpu" | "" (chưa cài)."""
    try:
        import onnxruntime as ort

        av = ort.get_available_providers()
    except Exception:  # noqa: BLE001
        return ""
    if "CUDAExecutionProvider" in av:
        return "cuda"
    if "DmlExecutionProvider" in av:
        return "dml"
    return "cpu"


_CUDA_READY = False


# cuDNN 9 trên Windows: các thư viện con gọi hàm của cudnn_graph64_9 -> phải nạp graph TRƯỚC, rồi tới các thư viện con
CUDA_DLLS = ("cudart64_*.dll", "cublasLt64_*.dll", "cublas64_*.dll", "cufft64_*.dll", "curand64_*.dll", "nvrtc64_*.dll")
CUDNN_ORDER = ("cudnn_graph64_9.dll", "cudnn64_9.dll", "cudnn_ops64_9.dll", "cudnn_cnn64_9.dll", "cudnn_adv64_9.dll",
               "cudnn_heuristic64_9.dll", "cudnn_engines_precompiled64_9.dll", "cudnn_engines_runtime_compiled64_9.dll",
               "cudnn_engines_tensor_ir64_9.dll")
_CUDA_NOTE = ""     # lý do không dùng được thư viện CUDA của PyTorch (hiện trong cảnh báo OCR)


def _cuda_major(ver: str) -> int | None:
    """'12.8' -> 12, '2.11.0+cu128' -> 12, '' -> None."""
    try:
        if "+cu" in ver:
            return int(ver.split("+cu")[1][:2])
        return int(str(ver).split(".")[0]) if ver else None
    except (ValueError, IndexError):
        return None


def _loaded(name: str) -> bool:
    """Windows: thư viện cùng tên đã nạp trong tiến trình (bản nào cũng được)."""
    try:
        import ctypes

        return bool(ctypes.WinDLL("kernel32").GetModuleHandleW(name))
    except Exception:  # noqa: BLE001
        return False


def _prepare_cuda() -> None:
    """onnxruntime-gpu cần thư viện CUDA + cuDNN 9: dùng bản đi kèm PyTorch (torch\\lib) nếu PyTorch cùng đời CUDA với
    onnxruntime – nạp sẵn để onnxruntime tìm thấy (không import torch: tiến trình giao diện không phải giữ ~0,5–1 GB).
    2.1.40: trước đây gọi onnxruntime.preload_dlls() (có thể nạp cuDNN của gói nvidia-* / PATH) RỒI nạp cả bộ cuDNN của
    PyTorch theo ABC -> thư viện con của PyTorch dính cudnn_graph64_9 bản khác -> Windows bật 4 hộp "Entry Point Not
    Found". Giờ: chỉ 1 nguồn cuDNN; đã có cuDNN nào trong tiến trình thì không nạp thêm; graph nạp trước; lỗi không bật
    hộp thoại (quiet_dll_errors) mà ghi lại -> OCR tự chạy CPU + cảnh báo."""
    global _CUDA_READY, _CUDA_NOTE
    if _CUDA_READY:
        return
    _CUDA_READY = True
    quiet_dll_errors()
    try:
        import onnxruntime as ort
    except Exception:  # noqa: BLE001
        return
    try:
        from onnxruntime.capi import build_and_package_info as bi

        ort_major = _cuda_major(str(getattr(bi, "cuda_version", "") or ""))
    except Exception:  # noqa: BLE001
        ort_major = None
    lib = None
    if sys.platform == "win32":
        try:
            spec = importlib.util.find_spec("torch")
            lib = Path(spec.origin).parent / "lib" if spec and spec.origin else None
        except Exception:  # noqa: BLE001
            lib = None
        if lib is not None and not lib.is_dir():
            lib = None
    from .gpu import torch_build

    torch_major = _cuda_major(torch_build())
    use_torch = bool(lib) and torch_major is not None and (ort_major is None or torch_major == ort_major) and \
        (lib / "cudnn_graph64_9.dll").exists()
    if use_torch and any(_loaded(n) for n in ("cudnn_graph64_9.dll", "cudnn64_9.dll")) and "torch" not in sys.modules:
        use_torch = False          # đã có cuDNN khác trong tiến trình -> không trộn 2 bản
    if not use_torch:
        if hasattr(ort, "preload_dlls"):
            try:
                ort.preload_dlls()          # PyTorch cùng đời CUDA / gói nvidia-* cài bằng pip / PATH
            except Exception:  # noqa: BLE001
                pass
        if lib and torch_major is not None and ort_major is not None and torch_major != ort_major:
            _CUDA_NOTE = (f"onnxruntime-gpu dùng CUDA {ort_major} nhưng PyTorch dùng CUDA {torch_major} – không dùng chung "
                          "được thư viện của PyTorch")
        return
    if "torch" in sys.modules:   # PyTorch đã nạp đủ bộ CUDA / cuDNN của nó (tách giọng chạy trong tiến trình này)
        return
    import ctypes

    try:
        os.add_dll_directory(str(lib))
    except Exception:  # noqa: BLE001
        pass
    bad = []
    files = [f for pat in CUDA_DLLS for f in sorted(lib.glob(pat))] + [lib / n for n in CUDNN_ORDER if (lib / n).exists()]
    for f in files:
        try:
            ctypes.WinDLL(str(f))
        except OSError as e:
            bad.append(f"{f.name} ({getattr(e, 'winerror', '') or e})")
    if bad:
        _CUDA_NOTE = "không nạp được thư viện card của PyTorch: " + ", ".join(bad[:3])
        try:
            raise RuntimeError(_CUDA_NOTE)
        except RuntimeError:
            log_error("Nạp thư viện CUDA / cuDNN cho OCR")


ORT_CUDA13 = (1, 27)     # onnxruntime-gpu trên PyPI: 1.19–1.26 dựng cho CUDA 12, từ 1.27 cho CUDA 13 (đã kiểm requires_dist)


def ort_gpu_cuda() -> int | None:
    """Đời CUDA của onnxruntime-gpu đang cài (đọc số phiên bản – không import): 11 / 12 / 13; None = chưa cài."""
    try:
        from importlib.metadata import version

        v = version("onnxruntime-gpu").split(".")
        mm = (int(v[0]), int(v[1]))
    except Exception:  # noqa: BLE001
        return None
    return 13 if mm >= ORT_CUDA13 else 12 if mm >= (1, 19) else 11


def gpu_pkg_spec() -> str:
    """Bản onnxruntime-gpu hợp với PyTorch đang cài (2.1.40): PyTorch CUDA 12 (cu12x) -> onnxruntime-gpu < 1.27 (CUDA
    12, dùng chung thư viện CUDA / cuDNN của PyTorch); PyTorch CUDA 13 -> bản mới nhất; không có PyTorch bản card ->
    bản mới nhất kèm thư viện CUDA / cuDNN của NVIDIA cài bằng pip. Trước đây luôn cài bản mới nhất (CUDA 13) -> máy có
    PyTorch cu128 không chạy được trên card + Windows bật hộp "Entry Point Not Found" (cuDNN lệch bản)."""
    from .gpu import torch_build

    tm = _cuda_major(torch_build())
    if tm == 12:
        return f"onnxruntime-gpu>=1.20,<{ORT_CUDA13[0]}.{ORT_CUDA13[1]}"
    if tm and tm >= 13:
        return f"onnxruntime-gpu>={ORT_CUDA13[0]}.{ORT_CUDA13[1]}"
    return GPU_PKG["cuda"] + "[cuda,cudnn]"


def gpu_mismatch() -> str:
    """'' khi ổn; không thì câu giải thích onnxruntime-gpu đang cài khác đời CUDA với PyTorch."""
    from .gpu import torch_build

    oc, tb = ort_gpu_cuda(), torch_build()
    tm = _cuda_major(tb)
    if oc and tm and oc != tm:
        return (f"onnxruntime-gpu đang cài dùng CUDA {oc}, PyTorch {tb} dùng CUDA {tm} – OCR không chạy được trên card "
                f"(và Windows có thể báo lỗi cudnn …64_9.dll). Bấm \"Cài bản chạy card\" để cài bản CUDA {tm}")
    return ""


def gpu_install_cmd() -> str:
    """Lệnh cài onnxruntime chạy trên card (gỡ bản CPU trước – 2 bản cùng cài sẽ đè file của nhau)."""
    py = sys.executable
    return (f'"{py}" -m pip uninstall -y onnxruntime onnxruntime-gpu onnxruntime-directml && '
            f'"{py}" -m pip install "{gpu_pkg_spec()}"')


def install_gpu(pkg: str | None = None, progress=None, cancel=None) -> str:
    """Nút "OCR chạy trên card" (Cài đặt): gỡ onnxruntime bản CPU / bản card khác đời CUDA, cài bản card hợp với PyTorch
    (`gpu_pkg_spec`). Trả lời nhắn."""
    import subprocess

    from .ffmpeg import _NO_WINDOW, Cancelled

    pkg = pkg or gpu_pkg_spec()

    if getattr(sys, "frozen", False):
        raise RuntimeError("Bản Subvideo.exe không cài thêm gói được – cài bằng bản Python (.venv) rồi đóng gói lại.")
    if "onnxruntime" in sys.modules:   # Windows: file .pyd đang được tool dùng thì pip không gỡ được (hỏng cả 2 bản)
        raise RuntimeError("onnxruntime đang được tool dùng (đã chạy OCR / đoán giọng nam nữ) – tắt mở lại tool rồi bấm "
                           "nút này NGAY (trước khi chạy việc khác). Hoặc tắt tool rồi chạy lệnh:\n" + gpu_install_cmd())
    steps = [[sys.executable, "-m", "pip", "uninstall", "-y", "onnxruntime", "onnxruntime-gpu", "onnxruntime-directml"],
             [sys.executable, "-m", "pip", "install", "--disable-pip-version-check", pkg]]
    for n, cmd in enumerate(steps):
        if progress:
            progress(n / len(steps), ("Đang gỡ onnxruntime bản CPU…", f"Đang cài {pkg}…")[n])
        pr = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                              errors="replace", creationflags=_NO_WINDOW)
        out = []
        for line in pr.stdout:   # type: ignore[union-attr]
            out.append(line)
            if cancel and cancel():
                pr.kill()
                raise Cancelled()
        if pr.wait() != 0 and n == 1:
            raise RuntimeError(f"Cài {pkg} lỗi:\n" + "".join(out[-12:]))
    return f"Đã cài {pkg} – mở lại tool để OCR chạy trên card."


# ------------------------------------------------------------------ bộ đọc

def _crop(img: np.ndarray, pts: np.ndarray) -> np.ndarray:
    x0, y0 = int(max(0, np.floor(pts[:, 0].min()))), int(max(0, np.floor(pts[:, 1].min())))
    x1, y1 = int(min(img.shape[1], np.ceil(pts[:, 0].max()))), int(min(img.shape[0], np.ceil(pts[:, 1].max())))
    return img[y0:max(y1, y0 + 2), x0:max(x1, x0 + 2)]


def _bgr(img: np.ndarray) -> np.ndarray:
    if img.ndim == 2:
        return np.repeat(img[:, :, None], 3, axis=2)
    return img


def bucket(crop: np.ndarray) -> np.ndarray:
    """Lấp phải ảnh dòng chữ cho tỉ lệ rộng / cao tròn bậc REC_BUCKET (bằng đúng giá trị bộ đọc tự lấp)."""
    h, w = crop.shape[:2]
    if h < 2:
        return crop
    want = int(np.ceil(w / h / REC_BUCKET) * REC_BUCKET * h)
    if want <= w:
        return crop
    pad = np.full((h, want - w) + crop.shape[2:], REC_PAD, crop.dtype)
    return np.concatenate([crop, pad], axis=1)


ALT_MIN = 0.01          # chữ ứng viên (2.1.37): xác suất tối thiểu để giữ làm phương án thay thế


class _Detail:
    """Bọc bộ giải mã CTC của RapidOCR (2.1.37): khi gọi đọc dòng với return_word_box thì trả thêm chi tiết TỪNG CHỮ –
    độ chắc (xác suất cao nhất trong các khung của chữ đó), vị trí tâm chữ (tỉ lệ theo bề rộng ảnh đã lấp phải) và tối đa
    3 chữ ứng viên khác ở đúng khung đó (bộ đọc phân vân 己 / 已…). Gọi thường thì y như bản gốc."""

    def __init__(self, base, img_h: int):
        self.base, self.img_h = base, img_h

    def __getattr__(self, name):
        return getattr(self.base, name)

    def __call__(self, preds, return_word_box: bool = False, **kw):
        lines, _ = self.base(preds, False, **kw)
        if not return_word_box:
            return lines, []
        ratios = list(kw.get("wh_ratio_list") or [1.0] * len(preds))
        img_w = self.img_h * float(kw.get("max_wh_ratio") or 1.0)
        chars = self.base.character
        out = []
        for b, p in enumerate(np.asarray(preds)):
            T = p.shape[0]
            idx = p.argmax(axis=1)
            res_w = max(1.0, min(img_w, np.ceil(self.img_h * float(ratios[b] if b < len(ratios) else 1.0))))
            det = []
            t = 0
            while t < T:
                k = int(idx[t])
                e = t
                while e + 1 < T and int(idx[e + 1]) == k:
                    e += 1
                if k != 0:
                    tt = t + int(np.argmax(p[t:e + 1, k]))
                    row = p[tt]
                    top = np.argsort(-row)[:4]
                    alts = [(chars[j], round(float(row[j]), 3)) for j in top
                            if j != k and j != 0 and float(row[j]) >= ALT_MIN and j < len(chars)][:3]
                    x = (t + e + 1) / 2.0 / T * img_w / res_w
                    det.append((chars[k] if k < len(chars) else "", round(float(p[t:e + 1, k].max()), 4),
                                round(float(x), 4), alts))
                t = e + 1
            out.append(det)
        return lines, out


class Engine:
    """kind "v3" (RapidOCR 3: tìm chữ / đọc dòng tách riêng, có thể chạy card) | "v1" (RapidOCR 1.x, CPU)."""

    def __init__(self, key: str = "v6_small", device: str = "auto"):
        self.warn = ""
        self.lock = threading.Lock()        # bước tìm chữ (đổi cỡ ảnh theo từng lần gọi -> không gọi chồng)
        self.rlock = threading.Lock()       # bước đọc dòng
        if key != "v4" and has_v3():
            if key not in SPEC or not model_ready(key):
                if key in SPEC:
                    self.warn = (f"Chưa tải mô hình OCR {dict(MODELS).get(key, key).split(' –')[0]} (tab Tài liệu) – "
                                 "đã dùng PP-OCRv6 có sẵn.")
                key = "v6_small"
            self._init_v3(key, device)
        elif has_v1():
            if key != "v4" and not has_v3():
                self.warn = "Chưa cài RapidOCR 3 (pip install -U rapidocr) – đang dùng bộ đọc cũ PP-OCRv4."
            from rapidocr_onnxruntime import RapidOCR

            self._e = RapidOCR()
            self.kind, self.key, self.device = "v1", "v4", "CPU"
        else:
            raise RuntimeError("Chưa cài bộ đọc chữ: pip install -U rapidocr onnxruntime")

    def _init_v3(self, key: str, device: str) -> None:
        from rapidocr import RapidOCR
        from rapidocr.utils.typings import ModelType, OCRVersion

        ver, size, _, _ = SPEC[key]
        (_, _, det), (_, _, rec) = _files(key)
        kind = ort_kind() if device != "cpu" else "cpu"
        bad = gpu_mismatch() if kind == "cuda" else ""
        if bad:      # onnxruntime-gpu khác đời CUDA với PyTorch: chạy CPU luôn (khỏi thử card -> lỗi cuDNN)
            kind = "cpu"
            self.warn = (self.warn + " " if self.warn else "") + bad + " – OCR đang chạy CPU."
        if kind == "cuda":
            _prepare_cuda()
        p = {"Global.log_level": "error", "Global.use_cls": False,
             "Det.limit_type": "max", "Det.limit_side_len": DET_SIDE,
             "Det.ocr_version": OCRVersion(ver), "Det.model_type": ModelType(size), "Det.model_path": str(det),
             "Rec.ocr_version": OCRVersion(ver), "Rec.model_type": ModelType(size), "Rec.model_path": str(rec),
             "Rec.rec_batch_num": 8, "Global.model_root_dir": str(OCR_DIR),
             "EngineConfig.onnxruntime.use_cuda": kind == "cuda",
             "EngineConfig.onnxruntime.cuda_ep_cfg.cudnn_conv_algo_search": "HEURISTIC",
             "EngineConfig.onnxruntime.use_dml": kind == "dml"}
        self._e = RapidOCR(params=p)
        self._det = self._e._load_det_model()
        self._rec = self._e._load_rec_model()
        try:
            self._rec.postprocess_op = _Detail(self._rec.postprocess_op, int(self._rec.rec_image_shape[1]))
        except Exception:  # noqa: BLE001 – bản rapidocr khác cấu trúc: đọc dòng vẫn chạy, chỉ không có chi tiết từng chữ
            pass
        try:
            prov = self._rec.session.session.get_providers()[0]
        except Exception:  # noqa: BLE001
            prov = "CPUExecutionProvider"
        self.device = {"CUDAExecutionProvider": "card (CUDA)", "DmlExecutionProvider": "card (DirectML)"}.get(prov, "CPU")
        if kind in ("cuda", "dml") and self.device == "CPU":
            self.warn = (self.warn + " " if self.warn else "") + \
                "onnxruntime có bản card nhưng không khởi động được trên card (thiếu CUDA 12 / cuDNN 9?) – OCR chạy CPU." + \
                (f" ({_CUDA_NOTE})" if kind == "cuda" and _CUDA_NOTE else "")
        self.kind, self.key = "v3", key

    @property
    def label(self) -> str:
        return f"{dict(MODELS).get(self.key, self.key).split(' –')[0]} · {self.device}"

    # ---------------------------------------------------------------- tìm chữ / đọc dòng
    def detect(self, img: np.ndarray, side: int | None = None) -> list[np.ndarray]:
        """Khung các dòng chữ [(4, 2) float32] theo toạ độ ảnh vào (side: cạnh dài tối đa khi tìm chữ)."""
        if self.kind == "v1":
            return [b for b, _, _ in self.ocr(img)]
        img = _bgr(img)
        h, w = img.shape[:2]
        top = 0
        want = int(max(32, w / WIDE))
        if h < want:                      # dải rất dẹt: thêm lề trên / dưới để bộ tìm chữ không bị ép dẹt
            top = (want - h) // 2
            img = np.concatenate([np.full((top, w, 3), REC_PAD, np.uint8), img,
                                  np.full((want - h - top, w, 3), REC_PAD, np.uint8)], axis=0)
        with self.lock:
            self._det.limit_side_len = int(side or DET_SIDE)
            r = self._det(img)
        boxes = [] if r.boxes is None else [np.asarray(b, np.float32) for b in r.boxes]
        for b in boxes:
            b[:, 1] = np.clip(b[:, 1] - top, 0, h)
        return [b for b in boxes if b[:, 1].max() - b[:, 1].min() >= 4]

    def recognize(self, crops: list[np.ndarray]) -> list[tuple[str, float]]:
        if not crops:
            return []
        if self.kind == "v1":       # bộ đọc cũ không tách bước: đọc trọn từng ảnh dòng
            out = []
            for c in crops:
                got = self.ocr(c)
                out.append(("".join(t for _, t, _ in got), float(np.mean([s for _, _, s in got])) if got else 0.0))
            return out
        from rapidocr.ch_ppocr_rec import TextRecInput

        with self.rlock:
            r = self._rec(TextRecInput(img=[bucket(_bgr(c)) for c in crops]))
        txts, scores = list(r.txts or ()), list(r.scores or ())
        return [(str(t), float(s)) for t, s in zip(txts, scores)] + [("", 0.0)] * (len(crops) - len(txts))

    def recognize_ex(self, crops: list[np.ndarray]) -> list[tuple[str, float, list | None]]:
        """Như recognize + chi tiết từng chữ [(chữ, độ chắc, tâm chữ theo tỉ lệ bề rộng ảnh dòng, [(chữ ứng viên,
        xác suất)])] (None: bộ đọc cũ / không lấy được). Chữ ghép lại = đúng dòng chữ đọc ra."""
        if not crops:
            return []
        if self.kind == "v1" or not isinstance(getattr(self._rec, "postprocess_op", None), _Detail):
            return [(t, s, None) for t, s in self.recognize(crops)]
        from rapidocr.ch_ppocr_rec import TextRecInput

        imgs = [bucket(_bgr(c)) for c in crops]
        with self.rlock:
            r = self._rec(TextRecInput(img=imgs, return_word_box=True))
        txts, scores = list(r.txts or ()), list(r.scores or ())
        words = list(r.word_results or ())
        out = []
        for i, c in enumerate(crops):
            if i >= len(txts):
                out.append(("", 0.0, None))
                continue
            det = words[i] if i < len(words) and isinstance(words[i], list) else None
            if det is not None:
                k = imgs[i].shape[1] / max(1, c.shape[1])        # tỉ lệ theo ảnh đã lấp phải -> theo ảnh dòng gốc
                det = [(ch, cf, round(min(1.0, x * k), 4), al) for ch, cf, x, al in det]
                if "".join(ch for ch, _, _, _ in det) != str(txts[i]):
                    det = None
            out.append((str(txts[i]), float(scores[i]), det))
        return out

    def ocr(self, img: np.ndarray) -> list[tuple[np.ndarray, str, float]]:
        """Tìm chữ + đọc: [(khung (4, 2), chữ, độ tin)] (chưa lọc)."""
        if self.kind == "v1":
            res, _ = self._e(_bgr(img))
            return [(np.array(b, np.float32), str(t), float(s)) for b, t, s in (res or [])]
        boxes = self.detect(img)
        recs = self.recognize([_crop(img, b) for b in boxes])
        return [(b, t, s) for b, (t, s) in zip(boxes, recs)]


_ENGINES: dict = {}
_LOCK = threading.Lock()
_LAST_USE = [0.0]


def get(settings: dict | None = None) -> Engine:
    st = settings or {}
    key = (str(st.get("ocr_model") or "v6_small"), str(st.get("ocr_device") or "auto"))
    with _LOCK:
        _LAST_USE[0] = time.time()
        if key not in _ENGINES:
            _ENGINES.clear()            # chỉ giữ 1 bộ đọc (đổi mô hình ở Cài đặt -> bỏ bộ cũ)
            _ENGINES[key] = Engine(*key)
        return _ENGINES[key]


def release() -> None:
    with _LOCK:
        _ENGINES.clear()


def release_idle(max_idle: float) -> bool:
    """2.4.8: bộ đọc chạy trên card (onnxruntime CUDA giữ vùng nhớ riêng, không tự trả) để không quá max_idle giây ->
    bỏ (lần sau dùng nạp lại ~1 s). Bộ đọc chạy CPU giữ nguyên (không chiếm card)."""
    with _LOCK:
        if not _ENGINES or time.time() - _LAST_USE[0] < max_idle:
            return False
        if not any(str(getattr(e, "device", "")).startswith("card") for e in _ENGINES.values()):
            return False
        _ENGINES.clear()
    return True
