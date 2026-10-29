"""LaMa inpainting (Large Mask Inpainting) – AI vẽ lại nền phía sau chữ, đẹp hơn nhiều so với OpenCV.

Dùng bản TorchScript big-lama (Apache-2.0), tải 1 lần (~200MB) vào data/models/lama.
Chỉ xử lý vùng nhỏ quanh chữ nên nhanh, chạy GPU NVIDIA rất nhanh, CPU vẫn dùng được.
"""
from __future__ import annotations

import threading
import urllib.request
from pathlib import Path

import numpy as np

from .config import MODELS_DIR

LAMA_URL = "https://github.com/enesmsahin/simple-lama-inpainting/releases/download/v0.1.0/big-lama.pt"
LAMA_FILE = MODELS_DIR / "lama" / "big-lama.pt"
_model = None
_device = None
_lock = threading.Lock()


def available() -> bool:
    from .config import installed

    return installed("torch")


def ensure_model(progress=None, cancel=None) -> Path:
    if LAMA_FILE.exists() and LAMA_FILE.stat().st_size > 100_000_000:
        return LAMA_FILE
    import time

    LAMA_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = LAMA_FILE.with_suffix(".part")
    st = {"t": time.time(), "b": 0, "speed": 0.0, "last": 0.0}

    def hook(n, bs, total):
        if cancel and cancel():
            from .ffmpeg import Cancelled

            raise Cancelled()
        now, done = time.time(), n * bs
        if now - st["t"] >= 1.0:
            cur = (done - st["b"]) / (now - st["t"])
            st["speed"] = cur if st["speed"] <= 0 else st["speed"] * 0.6 + cur * 0.4
            st["t"], st["b"] = now, done
        if progress and total > 0 and now - st["last"] > 0.4:
            st["last"] = now
            sp = f" · {st['speed'] / 1e6:.1f} MB/s" if st["speed"] > 0 else ""
            progress(min(1.0, done / total),
                     f"Đang tải mô hình LaMa {int(min(1.0, done / total) * 100)}% · "
                     f"{done / 1e6:.0f} MB / {total / 1e6:.0f} MB{sp}")

    urllib.request.urlretrieve(LAMA_URL, tmp, hook)
    tmp.replace(LAMA_FILE)
    return LAMA_FILE


def load(progress=None):
    global _model, _device
    with _lock:
        if _model is None:
            import torch

            path = ensure_model(progress)
            _device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
            _model = torch.jit.load(str(path), map_location=_device)
            _model.eval()
        return _model, _device


def inpaint(img_bgr: np.ndarray, mask: np.ndarray) -> np.ndarray:
    """img_bgr: HxWx3 uint8, mask: HxW (>0 = vùng cần xoá). Trả về ảnh BGR cùng kích thước."""
    import torch

    model, dev = load()
    h, w = img_bgr.shape[:2]
    ph, pw = (8 - h % 8) % 8, (8 - w % 8) % 8
    rgb = img_bgr[:, :, ::-1]
    if ph or pw:
        rgb = np.pad(rgb, ((0, ph), (0, pw), (0, 0)), mode="symmetric")
        mask = np.pad(mask, ((0, ph), (0, pw)), mode="symmetric")
    t_img = torch.from_numpy(np.ascontiguousarray(rgb.transpose(2, 0, 1))).float().div(255).unsqueeze(0).to(dev)
    t_mask = torch.from_numpy((mask > 0).astype(np.float32)).unsqueeze(0).unsqueeze(0).to(dev)
    t_img = t_img * (1 - t_mask)  # che hẳn vùng chữ để AI không "nhìn thấy" chữ cũ
    with torch.inference_mode():
        out = model(t_img, t_mask)[0]
    res = (out.permute(1, 2, 0).clamp(0, 1).mul(255).byte().cpu().numpy())[:h, :w, ::-1].astype(np.float32)
    # Hiệu chỉnh màu: LaMa giữ chi tiết/kết cấu tốt nhưng hay lệch tông nhẹ (thành "bóng chữ" trên nền phẳng).
    # Lấy phần tần số thấp (màu nền) từ Telea - vốn khớp màu mép - cộng vào chi tiết của LaMa.
    import cv2

    m2 = (mask[:h, :w] > 0).astype(np.uint8)
    telea = cv2.inpaint(img_bgr, m2 * 255, 5, cv2.INPAINT_TELEA).astype(np.float32)
    diff = cv2.GaussianBlur(telea - res, (0, 0), 6)
    res = np.clip(res + diff, 0, 255)
    m = m2.astype(bool)[..., None]
    return np.where(m, res, img_bgr).astype(np.uint8)


def inpaint_roi(frame: np.ndarray, x0: int, y0: int, x1: int, y1: int, mask: np.ndarray, ctx: int = 32) -> None:
    """Xoá trong vùng (x0,y0,x1,y1) của frame (tại chỗ). Lấy thêm viền ngữ cảnh ctx px để AI vẽ nền khớp."""
    H, W = frame.shape[:2]
    X0, Y0 = max(0, x0 - ctx), max(0, y0 - ctx)
    X1, Y1 = min(W, x1 + ctx), min(H, y1 + ctx)
    big = np.zeros((Y1 - Y0, X1 - X0), np.uint8)
    big[y0 - Y0:y1 - Y0, x0 - X0:x1 - X0] = mask
    crop = frame[Y0:Y1, X0:X1]
    frame[Y0:Y1, X0:X1] = inpaint(crop, big)
