"""Lọc màu chữ / màu viền phụ đề khi quét OCR (2.1.44).

Bộ đọc chữ vốn đọc trên ảnh XÁM: chữ vàng / trắng sáng gần bằng nhau, chữ trong cảnh lọt vào khung phụ đề chỉ bị bỏ khi
khác chỗ / khác cỡ / đang chạy. Bật "Chỉ lấy chữ đúng màu" (Cài đặt `ocr_color` + `ocr_colors`): dải phụ đề giải mã bằng
ẢNH MÀU, mỗi khung chỉ giữ điểm ĐÚNG MÀU CHỮ nằm sát điểm ĐÚNG MÀU VIỀN (và ngược lại) – học cách VideoSubFinder lọc
"inline / outline colors" (chỉ học cách làm, không chép mã); còn lại tô xám đều -> bộ theo dõi nét (giờ câu) và bộ đọc chỉ
thấy chữ phụ đề. Chữ sai màu dính cạnh câu cũng bị tách ra.

Đo trên video thử (2 lượt): phụ đề vàng viền đen + HUD trắng viền đen ở khoảng giữa câu – lấy nhầm 10 -> 0; phụ đề trắng
viền đen + chữ trắng không viền / chú thích vàng / biển chữ dính câu, nén mạnh – 10 câu nhầm + 1 câu dính -> 3 (chỉ còn chữ
y hệt kiểu phụ đề); chỉ xét màu chữ (không xét viền) còn 7 câu nhầm; nới độ lệch quá rộng bắt đầu đọc sai chữ.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

import numpy as np

# độ lệch màu cho phép (thang Lab 8 bit của OpenCV): sáng L nới 1,5 lần (nén / làm mờ đổi độ sáng nhiều), màu a / b 0,8
# lần (chữ trắng không được lẫn vàng nhạt)
TOLS = {"tight": 20.0, "mid": 28.0, "wide": 36.0}
TOL_CHOICES = [("tight", "Chặt"), ("mid", "Vừa"), ("wide", "Rộng")]
L_K, AB_K = 1.5, 0.8
MAX_COLORS = 3
DEFAULT_COLORS = [{"fill": "#FFFFFF", "outline": "#000000"}]
BG = 128                 # điểm bị lọc bỏ -> xám giữa (chữ sáng + viền tối vẫn tương phản rõ)
SPECK = 4                # cụm điểm đúng màu nhỏ hơn bấy nhiêu = nhiễu
GROW = 3                 # lan mặt nạ thân chữ thêm 3 lần × 2 điểm ảnh từ chỗ sát viền (nét dày)
SOLID = 0.6              # cụm cao ≥ 55 % dải và đặc ≥ 60 % khung bao = mảng màu (áo, biển, trời), không phải nét chữ
_HEX = re.compile(r"^#?[0-9A-Fa-f]{6}$")


def hex_rgb(h: str) -> tuple[int, int, int]:
    h = h.strip().lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def rgb_hex(rgb) -> str:
    r, g, b = (int(max(0, min(255, round(float(v))))) for v in rgb[:3])
    return f"#{r:02X}{g:02X}{b:02X}"


def lab_of(rgb) -> np.ndarray:
    import cv2

    px = np.uint8([[[rgb[2], rgb[1], rgb[0]]]])
    return cv2.cvtColor(px, cv2.COLOR_BGR2LAB)[0, 0].astype(np.float32)


def rgb_of_lab(lab) -> tuple[int, int, int]:
    import cv2

    px = np.uint8([[np.clip(np.round(lab), 0, 255)]])
    b, g, r = cv2.cvtColor(px, cv2.COLOR_LAB2BGR)[0, 0]
    return int(r), int(g), int(b)


def entries(settings: dict | None) -> list[dict]:
    """Các cặp màu đã chọn (Cài đặt `ocr_colors`): [{"fill": "#RRGGBB", "outline": "#RRGGBB" | ""}] – outline "" =
    không xét viền (phụ đề không có viền liền quanh chữ). Tối đa MAX_COLORS cặp."""
    st = settings or {}
    out = []
    for e in st.get("ocr_colors") or []:
        if not isinstance(e, dict) or not _HEX.match(str(e.get("fill") or "")):
            continue
        o = str(e.get("outline") or "")
        out.append({"fill": "#" + str(e["fill"]).lstrip("#").upper(),
                    "outline": ("#" + o.lstrip("#").upper()) if _HEX.match(o) else ""})
    return out[:MAX_COLORS]


def enabled(settings: dict | None) -> bool:
    return bool((settings or {}).get("ocr_color")) and bool(entries(settings))


def describe(ents: list[dict]) -> str:
    """"chữ #FFFFFF viền #000000; chữ #FFD800 (không xét viền)"."""
    return "; ".join(f"chữ {e['fill']} " + (f"viền {e['outline']}" if e["outline"] else "(không xét viền)")
                     for e in ents)


class ColorFilter:
    """Lọc 1 khung (ảnh BGR dải phụ đề) -> ảnh xám chỉ còn nét đúng màu (còn lại xám BG)."""

    def __init__(self, ents: list[dict], tol: str = "mid"):
        t = TOLS.get(tol, TOLS["mid"])
        k = np.array([t * L_K, t * AB_K, t * AB_K], np.float32)
        self.ents = ents
        self.tol = tol if tol in TOLS else "mid"
        self.rng = []
        for e in ents:
            lf = lab_of(hex_rgb(e["fill"]))
            fr = (np.clip(lf - k, 0, 255).astype(np.uint8), np.clip(lf + k, 0, 255).astype(np.uint8))
            orng = None
            if e["outline"]:
                lo = lab_of(hex_rgb(e["outline"]))
                orng = (np.clip(lo - k, 0, 255).astype(np.uint8), np.clip(lo + k, 0, 255).astype(np.uint8))
            self.rng.append((fr, orng))

    def masks(self, bgr: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """(mặt nạ thân chữ, mặt nạ viền) 0 / 255 – thân chữ phải nằm sát viền (≤ 2 điểm ảnh) và ngược lại."""
        import cv2

        lab = cv2.cvtColor(bgr, cv2.COLOR_BGR2LAB)
        k5 = np.ones((5, 5), np.uint8)
        fm = np.zeros(bgr.shape[:2], np.uint8)
        om = np.zeros_like(fm)
        for (flo, fhi), orng in self.rng:
            f = cv2.inRange(lab, flo, fhi)
            if orng is not None:
                o = cv2.inRange(lab, orng[0], orng[1])
                r = cv2.bitwise_and(f, cv2.dilate(o, k5))
                if r.any():
                    # lan tiếp trong vùng đúng màu chữ (chữ to / nét dày: lõi nét cách viền > 2 điểm vẫn là chữ –
                    # nét rộng tới ~16 điểm ảnh); lan có giới hạn để mảng màu lớn chạm viền không bị lấy cả
                    for _ in range(GROW):
                        r = cv2.bitwise_and(f, cv2.dilate(r, k5))
                    fm |= r
                om |= cv2.bitwise_and(o, cv2.dilate(f, k5))
            else:
                fm |= f
        if fm.any():
            fm = _drop_blobs(fm)
            if om.any():
                om = cv2.bitwise_and(om, cv2.dilate(fm, k5))
        return fm, om

    def __call__(self, bgr: np.ndarray) -> np.ndarray:
        import cv2

        fm, om = self.masks(bgr)
        g = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
        keep = cv2.dilate(cv2.bitwise_or(fm, om), np.ones((3, 3), np.uint8)) > 0
        out = np.full(g.shape, BG, np.uint8)
        out[keep] = g[keep]
        return out


def _drop_blobs(m: np.ndarray) -> np.ndarray:
    """Bỏ cụm nhiễu nhỏ + mảng màu đặc to (không phải nét chữ) khỏi mặt nạ thân chữ."""
    import cv2

    n, lab, stats, _ = cv2.connectedComponentsWithStats((m > 0).astype(np.uint8), connectivity=8)
    if n <= 1:
        return m
    H = m.shape[0]
    a = stats[1:, cv2.CC_STAT_AREA]
    w, h = stats[1:, cv2.CC_STAT_WIDTH], stats[1:, cv2.CC_STAT_HEIGHT]
    bad = (a < SPECK) | ((h >= 0.55 * H) & (a >= SOLID * w * h))
    if not bad.any():
        return m
    keep = np.ones(n, np.uint8)
    keep[0] = 0
    keep[1:][bad] = 0
    return np.where(keep[lab] > 0, 255, 0).astype(np.uint8)


def get(settings: dict | None) -> ColorFilter | None:
    st = settings or {}
    if not enabled(st):
        return None
    return ColorFilter(entries(st), str(st.get("ocr_color_tol") or "mid"))


# ------------------------------------------------------------------ ảnh dải phụ đề (xem thử / hút màu / AI đọc lại)

def _crop_vf(region: dict | None) -> str:
    r = region or {"x": 0.0, "y": 0.66, "w": 1.0, "h": 0.34}
    return (f"crop=trunc(iw*{float(r['w']):.4f}/2)*2:trunc(ih*{float(r['h']):.4f}/2)*2:"
            f"trunc(iw*{float(r['x']):.4f}):trunc(ih*{float(r['y']):.4f})")


def grab(src: str | Path, t: float, region: dict | None, max_w: int = 0) -> np.ndarray | None:
    """Khung tại giây t, cắt đúng vùng quét, ảnh BGR (max_w: thu nhỏ bề rộng tối đa)."""
    import cv2

    from .ffmpeg import ffmpeg_bin

    vf = _crop_vf(region) + (f",scale='min(iw,{int(max_w)})':-2" if max_w else "")
    cmd = [ffmpeg_bin(), "-hide_banner", "-loglevel", "error", "-ss", f"{max(0.0, t):.3f}", "-i", str(src),
           "-frames:v", "1", "-an", "-vf", vf, "-f", "image2pipe", "-vcodec", "png", "pipe:1"]
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    out = subprocess.run(cmd, capture_output=True, creationflags=flags).stdout
    if not out:
        return None
    img = cv2.imdecode(np.frombuffer(out, np.uint8), cv2.IMREAD_COLOR)
    return img if img is not None and img.size else None


def pick(img: np.ndarray, x: int, y: int, r: int = 2) -> str:
    """Màu tại điểm bấm (trung vị ô (2r+1)² – bấm lệch mép nét 1 chút vẫn ra đúng màu)."""
    h, w = img.shape[:2]
    x0, x1 = max(0, x - r), min(w, x + r + 1)
    y0, y1 = max(0, y - r), min(h, y + r + 1)
    p = np.median(img[y0:y1, x0:x1].reshape(-1, 3), axis=0)
    return rgb_hex((p[2], p[1], p[0]))


def preview(img: np.ndarray, settings: dict | None) -> tuple[bytes, bytes]:
    """Nút "Xem thử": (ảnh đánh dấu, ảnh bộ đọc chữ thấy) PNG. Ảnh đánh dấu: nền gốc tối đi, nét ĐƯỢC LẤY tô xanh lá
    (viền xanh đậm), nét tương phản mạnh mà SAI MÀU (sẽ bị bỏ) tô đỏ."""
    import cv2

    st = dict(settings or {})
    st["ocr_color"] = True
    cf = get(st)
    g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    if cf is None:
        out = (img * 0.45).astype(np.uint8)
        return cv2.imencode(".png", out)[1].tobytes(), cv2.imencode(".png", g)[1].tobytes()
    fm, om = cf.masks(img)
    seen = cf(img)
    # nét chữ bất kỳ (mọi màu): điểm khác hẳn trung bình lân cận
    strong = (np.abs(g.astype(np.int16) - cv2.blur(g, (9, 9)).astype(np.int16)) > 55).astype(np.uint8)
    strong = cv2.morphologyEx(strong, cv2.MORPH_OPEN, np.ones((2, 2), np.uint8)) > 0    # bỏ hạt nhiễu lẻ
    keep = cv2.dilate(cv2.bitwise_or(fm, om), np.ones((3, 3), np.uint8)) > 0
    out = (img * 0.45).astype(np.uint8)
    out[strong & ~keep] = (60, 60, 235)
    out[om > 0] = (40, 120, 40)
    out[fm > 0] = (80, 230, 90)
    return cv2.imencode(".png", out)[1].tobytes(), cv2.imencode(".png", seen)[1].tobytes()


# ------------------------------------------------------------------ tự lấy màu phụ đề

def _box_colors(crop: np.ndarray) -> tuple[np.ndarray, np.ndarray] | None:
    """(Lab thân chữ, Lab viền) trong 1 khung chữ: thân = điểm sáng nhất nằm sát điểm tối nhất (viền) – như
    VideoSubFinder mặc định "viền tối hơn chữ"."""
    import cv2

    if crop.size < 48:
        return None
    g = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    lab = cv2.cvtColor(crop, cv2.COLOR_BGR2LAB).reshape(-1, 3).astype(np.float32)
    hi, lo = np.percentile(g, 88), np.percentile(g, 12)
    if hi - lo < 40:
        return None
    br, dk = (g >= hi).astype(np.uint8), (g <= lo).astype(np.uint8)
    k = np.ones((5, 5), np.uint8)
    body = ((br > 0) & (cv2.dilate(dk, k) > 0)).reshape(-1)
    edge = ((dk > 0) & (cv2.dilate(br, k) > 0)).reshape(-1)
    if body.sum() < 8 or edge.sum() < 8:
        return None
    return np.median(lab[body], 0), np.median(lab[edge], 0)


def guess(src: str | Path, region: dict | None, duration: float, settings: dict | None = None, n: int = 36,
          progress=None, cancel=None) -> dict:
    """Nút "Tự lấy màu": chụp `n` khung rải khắp video, tìm + đọc chữ trong vùng quét, đo màu thân chữ / viền từng dòng
    chữ Hán đọc chắc, gom nhóm màu gần nhau; nhóm có NHIỀU CÂU KHÁC NHAU nhất là phụ đề (chữ HUD / biển hiệu / watermark
    lặp lại 1–2 câu). Viền trong nhóm lệch nhau nhiều (phụ đề không viền – "viền" đo được là nền) -> không xét viền.
    Trả {"entries": [cặp màu nhóm tốt nhất], "groups": [{fill, outline, lines, samples, examples}], "frames": số khung}."""
    from .ffmpeg import Cancelled
    from .ocr import _CJK, _similar
    from .ocr_engine import SCAN_SIDE, _crop

    from .ocr import get_engine

    eng = get_engine(settings or {})
    dur = max(1.0, float(duration or 0))
    times = [dur * (0.03 + 0.94 * (k + 0.5) / n) for k in range(n)]
    votes = []
    for k, t in enumerate(times):
        if cancel and cancel():
            raise Cancelled()
        if progress:
            progress(k / n, f"Đang tìm màu chữ phụ đề {k + 1}/{n}…")
        img = grab(src, t, region, max_w=1280)
        if img is None:
            continue
        import cv2

        g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        boxes = eng.detect(g, SCAN_SIDE)
        if not boxes:
            continue
        recs = eng.recognize([_crop(g, b) for b in boxes])
        for b, (txt, sc) in zip(boxes, recs):
            if sc < 0.8 or not _CJK.search(txt or ""):
                continue
            pts = np.asarray(b)
            x0, y0 = int(max(0, pts[:, 0].min())), int(max(0, pts[:, 1].min()))
            x1, y1 = int(pts[:, 0].max()) + 1, int(pts[:, 1].max()) + 1
            c = _box_colors(img[y0:y1, x0:x1])
            if c is not None:
                votes.append((c[0], c[1], txt))
    groups: list[dict] = []
    for fl, ol, txt in votes:
        for gr in groups:
            if np.linalg.norm(gr["f"] - fl) < 20:
                gr["fs"].append(fl)
                gr["os"].append(ol)
                gr["t"].append(txt)
                break
        else:
            groups.append({"f": fl, "fs": [fl], "os": [ol], "t": [txt]})
    out = []
    for gr in groups:
        distinct: list[str] = []
        for t in gr["t"]:
            if not any(_similar(t, d) >= 0.8 for d in distinct):
                distinct.append(t)
        f = np.median(np.array(gr["fs"]), 0)
        os_ = np.array(gr["os"])
        o = np.median(os_, 0)
        spread = float(np.median(np.linalg.norm(os_ - o, axis=1))) if len(os_) else 99.0
        out.append({"fill": rgb_hex(rgb_of_lab(f)), "outline": rgb_hex(rgb_of_lab(o)) if spread <= 18 else "",
                    "lines": len(distinct), "samples": len(gr["t"]), "examples": distinct[:3]})
    out.sort(key=lambda g: (-g["lines"], -g["samples"]))
    if progress:
        progress(1.0, "Xong")
    return {"entries": [{"fill": out[0]["fill"], "outline": out[0]["outline"]}] if out else [],
            "groups": out[:4], "frames": len(times)}


def guess_note(res: dict) -> str:
    gs = res.get("groups") or []
    if not gs:
        return ("Không tìm được chữ phụ đề trong vùng quét ở các khung đã chụp – kéo khung đúng chỗ phụ đề (hoặc bấm Tự "
                "dò) rồi thử lại, hoặc dùng Hút màu")
    g = gs[0]
    out = (f"Phụ đề: chữ {g['fill']} " + (f"viền {g['outline']}" if g["outline"] else "(viền không rõ – không xét viền)")
           + f" – {g['lines']} câu khác nhau")
    others = [x for x in gs[1:] if x["lines"]]
    if others:
        out += " · chữ khác trong vùng: " + "; ".join(
            f"{x['fill']}{(' / ' + x['outline']) if x['outline'] else ''} ({x['lines']} câu, vd {x['examples'][0]})"
            for x in others[:2])
    return out
