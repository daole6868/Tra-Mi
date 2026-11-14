"""Gỡ LOGO CỐ ĐỊNH (watermark luôn nằm 1 chỗ) bằng cách khôi phục nền thật phía sau, không phải vẽ đè.

Logo / watermark được ghép lên video theo công thức  I = α·L + (1 − α)·J  (J = hình gốc, L = màu logo, α = độ đục,
đều KHÔNG đổi theo thời gian). Có nhiều khung với nền J khác nhau thì ước lượng được α và α·L cho từng điểm ảnh, rồi
tính ngược  J = (I − α·L) / (1 − α)  -> nền thật hiện lại, không nhoè, không nhấp nháy (cách của Dekel và cộng sự,
"On the Effectiveness of Visible Watermarks", CVPR 2017 – tự viết lại theo dạng thống kê cho video):
  * Viền logo = chỗ đạo hàm ảnh giống nhau ở MỌI khung (lấy trung vị đạo hàm qua ~200 khung: nền thay đổi triệt tiêu,
    logo đứng yên còn lại) -> mặt nạ logo (lấp kín lòng chữ / hình).
  * Trong mặt nạ: độ biến thiên theo thời gian bị logo làm nhỏ đi đúng (1 − α) lần so với nền xung quanh; trung bình bị
    cộng thêm α·L. Thống kê nền tại chỗ logo nội suy từ vùng xung quanh (trung bình / độ biến thiên theo thời gian
    của video thay đổi chậm theo không gian) -> (1 − α) và α·L từng điểm.
  * Phần gần như đặc (α > 0,88) hoặc nền gần như không đổi (không đo được) không tính ngược được -> trả về mặt nạ
    để lấp bằng khung trước / sau (máy quay lia) hoặc AI vẽ.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from . import ffmpeg as ff

ALPHA_MAX = 0.88       # đục hơn mức này: không tính ngược (nhiễu khuếch đại quá lớn) -> lấp bằng nền khung khác / AI
MIN_SPREAD = 4.0       # độ sáng nền phải thay đổi ít nhất chừng này (độ lệch chuẩn, 0..255) mới đo được độ đục


@dataclass
class LogoModel:
    x0: int
    y0: int
    x1: int
    y1: int
    wm: np.ndarray       # α·L theo kênh Y, Cr, Cb (h, w, 3) float32
    beta: np.ndarray     # 1 − α theo kênh Y, Cr, Cb (h, w, 3) float32
    mask: np.ndarray     # điểm thuộc logo (h, w) bool
    opaque: np.ndarray   # điểm không tính ngược được (h, w) bool
    cfill: np.ndarray    # điểm có MÀU bị logo phủ gần hết (tính ngược khuếch đại nhiễu) -> màu lấy từ xung quanh
    frames: int = 0

    def apply(self, frame: np.ndarray, dy: int = 0) -> np.ndarray:
        """Gỡ logo tại chỗ trên khung (BGR uint8; dy = khung là dải bắt đầu ở hàng dy của khung gốc). Trả về mặt nạ
        phần đặc (cùng cỡ `frame`) để lấp tiếp bằng nền khung khác / AI."""
        import cv2

        y0, y1 = self.y0 - dy, self.y1 - dy
        roi = frame[y0:y1, self.x0:self.x1]
        sel = self.mask & ~self.opaque
        if sel.any():
            ycc = cv2.cvtColor(roi, cv2.COLOR_BGR2YCrCb).astype(np.float32)
            J = np.clip((ycc - self.wm) / np.maximum(self.beta, 0.12), 0, 255).astype(np.uint8)
            if self.cfill.any():
                cm = self.cfill.astype(np.uint8) * 255
                for c in (1, 2):
                    J[..., c] = cv2.inpaint(np.ascontiguousarray(J[..., c]), cm, 3, cv2.INPAINT_TELEA)
            back = cv2.cvtColor(J, cv2.COLOR_YCrCb2BGR)
            roi[sel] = back[sel]
        full = np.zeros(frame.shape[:2], np.uint8)
        full[y0:y1, self.x0:self.x1][self.opaque] = 255
        return full

    def save(self, path: Path):
        np.savez_compressed(path, box=np.array([self.x0, self.y0, self.x1, self.y1]), wm=self.wm.astype(np.float16),
                            beta=self.beta.astype(np.float16), mask=self.mask, opaque=self.opaque, cfill=self.cfill,
                            frames=np.array([self.frames]))

    @classmethod
    def load(cls, path: Path) -> "LogoModel":
        d = np.load(path)
        x0, y0, x1, y1 = (int(v) for v in d["box"])
        return cls(x0, y0, x1, y1, d["wm"].astype(np.float32), d["beta"].astype(np.float32), d["mask"].astype(bool),
                   d["opaque"].astype(bool), d["cfill"].astype(bool), int(d["frames"][0]))


def _roi(rect: dict, W: int, H: int) -> tuple[tuple[int, int, int, int], tuple[int, int, int, int]]:
    """(vùng khoanh, vùng đo = vùng khoanh + viền nền xung quanh) theo điểm ảnh."""
    x, y = int(float(rect["x"]) * W), int(float(rect["y"]) * H)
    w, h = max(4, int(float(rect["w"]) * W)), max(4, int(float(rect["h"]) * H))
    x1, y1 = min(W, x + w), min(H, y + h)
    m = max(8, int(0.25 * min(w, h)))
    return (x, y, x1, y1), (max(0, x - m), max(0, y - m), min(W, x1 + m), min(H, y1 + m))


def _frames(src: str | Path, box: tuple[int, int, int, int], dur: float, want: int = 200) -> np.ndarray:
    """Các khung (chỉ vùng box) rải khắp video: lấy khung khoá (rất nhanh); ít quá thì giải mã đều theo thời gian."""
    x0, y0, x1, y1 = box
    w, h = x1 - x0, y1 - y0
    crop = f"format=bgr24,crop={w}:{h}:{x0}:{y0}"     # cắt sau khi đổi RGB: kích thước lẻ không bị làm tròn

    def run(pre: list[str], vf: str) -> np.ndarray:
        cmd = [ff.ffmpeg_bin(), "-hide_banner", "-loglevel", "error", *pre, "-i", str(src), "-an", "-vf", vf,
               "-vsync", "0", "-f", "rawvideo", "-pix_fmt", "bgr24", "pipe:1"]
        buf = subprocess.run(cmd, capture_output=True, creationflags=ff._NO_WINDOW).stdout
        n = len(buf) // (w * h * 3)
        return np.frombuffer(buf[:n * w * h * 3], np.uint8).reshape(n, h, w, 3)

    fr = run(["-skip_frame", "nokey"], crop)
    if len(fr) < 60:
        rate = max(0.2, min(30.0, want / max(1.0, dur)))
        fr = run([], f"fps={rate:.4f},{crop}")
    if len(fr) > want:
        fr = fr[np.linspace(0, len(fr) - 1, want).astype(int)]
    return fr


def _regress(ycc: np.ndarray, known_mask: np.ndarray, k: int):
    """Hồi quy theo thời gian từng điểm: khung (YCrCb uint8) = α·L + (1 − α)·nền nội suy từ ngoài `known_mask`.
    Trả về (1 − α theo 3 kênh, α·L theo 3 kênh, độ lệch chuẩn nền theo thời gian 3 kênh)."""
    import cv2

    jh = np.stack([cv2.inpaint(f, known_mask, max(3, k), cv2.INPAINT_TELEA) for f in ycc]).astype(np.float32)
    yf = ycc.astype(np.float32)
    mi, mj = yf.mean(0), jh.mean(0)
    var = ((jh - mj) ** 2).mean(0)
    cov = ((yf - mi) * (jh - mj)).mean(0)
    del jh, yf
    beta = np.clip(cov / np.maximum(var, 1e-3), 0.0, 1.0).astype(np.float32)
    wm = (mi - beta * mj).astype(np.float32)
    sd = np.sqrt(var).astype(np.float32)
    return np.clip(beta, 0.0, 1.0), wm, sd


def estimate(src: str | Path, rect: dict, W: int, H: int, dur: float) -> LogoModel:
    import cv2

    (rx0, ry0, rx1, ry1), (x0, y0, x1, y1) = _roi(rect, W, H)
    F = _frames(src, (x0, y0, x1, y1), dur).astype(np.float32)
    h, w = y1 - y0, x1 - x0
    inner = np.zeros((h, w), bool)
    inner[ry0 - y0:ry1 - y0, rx0 - x0:rx1 - x0] = True
    if len(F) < 8:
        raise ValueError("Video quá ngắn để đo logo")
    gray = F @ np.array([0.114, 0.587, 0.299], np.float32)
    # 1. viền logo: đạo hàm giống nhau ở mọi khung
    gx = np.stack([cv2.Sobel(g, cv2.CV_32F, 1, 0, ksize=3) for g in gray])
    gy = np.stack([cv2.Sobel(g, cv2.CV_32F, 0, 1, ksize=3) for g in gray])
    G = np.hypot(np.median(gx, 0), np.median(gy, 0))
    del gx, gy
    thr = max(10.0, 4.0 * float(np.median(G[inner])))
    edges = ((G > thr) & inner).astype(np.uint8) * 255
    k = max(3, int(min(rx1 - rx0, ry1 - ry0) * 0.06)) | 1
    m0 = cv2.morphologyEx(edges, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k)))
    # lấp kín lòng chữ / hình (điểm không nối được ra mép vùng đo)
    ff_img = np.pad(m0, 1)
    flood = ff_img.copy()
    cv2.floodFill(flood, None, (0, 0), 255)
    holes = (flood[1:-1, 1:-1] == 0)
    m0 = ((m0 > 0) | holes) & inner
    m0 = cv2.dilate(m0.astype(np.uint8), np.ones((3, 3), np.uint8), iterations=1).astype(bool) & inner
    ycc = np.stack([cv2.cvtColor(f.astype(np.uint8), cv2.COLOR_BGR2YCrCb) for f in F])
    del F
    if not m0.any():
        m0 = inner.copy()
    known_mask = cv2.dilate(m0.astype(np.uint8), np.ones((3, 3), np.uint8), iterations=1)
    # 2. nền dưới logo ở từng khung ≈ nội suy từ xung quanh (Ĵ); hồi quy theo thời gian từng điểm
    #    I = α·L + (1−α)·Ĵ (kênh YCrCb: độ sáng đủ phân giải, màu bị video nén còn 1/2 -> mỗi kênh độ đục riêng)
    beta, wm, sp_bg = _regress(ycc, known_mask, k)   # sp_bg: nền thay đổi bao nhiêu (độ lệch chuẩn theo thời gian)
    del ycc
    by = beta[..., 0]
    bc_model = 1.0 - cv2.blur(1.0 - by, (3, 3))   # kênh màu nền ít đổi: độ đục độ sáng nhoè như lấy mẫu 4:2:0
    for c in (1, 2):
        weak = sp_bg[..., c] < MIN_SPREAD * 0.25
        beta[..., c][weak] = bc_model[weak]
    beta[~m0] = 1.0
    wm[~m0] = 0.0
    sp_bg = sp_bg[..., 0]
    unreliable = sp_bg < MIN_SPREAD
    opaque = m0 & ((1 - by > ALPHA_MAX) | unreliable)
    # điểm logo rất mờ (α < 3 %): giữ nguyên ảnh gốc
    mask = m0 & ((1 - beta.min(axis=2)) > 0.03)
    opaque = cv2.dilate(opaque.astype(np.uint8), np.ones((3, 3), np.uint8), iterations=1).astype(bool) & m0
    cfill = mask & ~opaque & (beta[..., 1:].min(axis=2) < 0.25)
    return LogoModel(x0, y0, x1, y1, wm, beta, mask | opaque, opaque, cfill, len(gray))


def model_path(work: Path, src: str | Path, rect: dict) -> Path:
    st = Path(src).stat() if Path(src).exists() else None
    key = json.dumps({"src": str(src), "size": st.st_size if st else 0, "mtime": int(st.st_mtime) if st else 0,
                      "rect": [round(float(rect[k]), 4) for k in ("x", "y", "w", "h")], "v": 12}, sort_keys=True)
    return Path(work) / f"logo_{hashlib.sha1(key.encode()).hexdigest()[:12]}.npz"


def get_model(src: str | Path, rect: dict, W: int, H: int, dur: float, work: Path) -> LogoModel:
    """Mô hình logo của 1 vùng (đo 1 lần, lưu lại cho Xem thử / Chạy xoá / Xuất video)."""
    p = model_path(work, src, rect)
    if p.exists():
        try:
            return LogoModel.load(p)
        except Exception:  # noqa: BLE001
            pass
    m = estimate(src, rect, W, H, dur)
    Path(work).mkdir(parents=True, exist_ok=True)
    m.save(p)
    return m
