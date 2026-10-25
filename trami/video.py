"""Bộ dựng video: cắt/ghép, xoá & che phụ đề cũ, đè phụ đề mới, lồng tiếng, crop, lật, tốc độ,
đổi tỉ lệ khung hình, logo, chữ, nhạc nền... tất cả trong MỘT lần encode FFmpeg để giữ chất lượng.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Callable

from . import ffmpeg as ff
from .config import FONTS_DIR
from .subtitle import Segment, remap_after_cut, scale_time, save_subtitles

ProgressCB = Callable[[float, str], None]

# tỉ lệ khung có sẵn: dọc (rộng < cao) và ngang – "1:1" có ở cả 2 nhóm
PORTRAIT = ["1:1", "3:4", "9:16", "2:3", "3:5", "4:5", "5:7"]
LANDSCAPE = ["1:1", "4:3", "16:9", "3:2", "5:3", "5:4", "7:5"]
ASPECTS = {a: tuple(int(x) for x in a.split(":")) for a in PORTRAIT + LANDSCAPE}
FILL_MODES = ("blur", "pad", "color", "crop")   # nền mờ / viền đen / nền màu / cắt cho đầy khung
# vùng bị nút bấm / chữ của TikTok, Reels, Shorts che (theo khung dọc): trên 12 %, dưới 18 %, phải 14 %
SAFE_ZONE = {"top": 0.12, "bottom": 0.18, "right": 0.14}


def aspect_wh(aspect: str | None) -> tuple[float, float] | None:
    """"9:16" -> (9, 16); "original" / sai -> None. Nhận cả tỉ lệ tự nhập "a:b"."""
    if not aspect or aspect == "original":
        return None
    if aspect in ASPECTS:
        return ASPECTS[aspect]
    try:
        a, b = (float(x) for x in str(aspect).replace("x", ":").split(":"))
        return (a, b) if a > 0 and b > 0 else None
    except ValueError:
        return None


def aspect_pos(spec: dict) -> tuple[float, float]:
    """Vị trí phim trong khung (0 = sát trái / trên, 0,5 = giữa, 1 = sát phải / dưới)."""
    pos = spec.get("aspect_pos") or [0.5, 0.5]
    try:
        return min(1.0, max(0.0, float(pos[0]))), min(1.0, max(0.0, float(pos[1])))
    except (TypeError, ValueError, IndexError):
        return 0.5, 0.5


def fit_geometry(cw: float, ch: float, TW: float, TH: float, spec: dict) -> tuple[float, float, float, float]:
    """Phim (cw × ch) đặt vào khung TW × TH theo cách lấp + phóng + vị trí: (trái, trên, rộng, cao) – có thể tràn
    ra ngoài khung (phần tràn bị cắt). Dùng chung cho xuất video và khung xem trước."""
    mode = spec.get("aspect_mode", "blur")
    fz = min(4.0, max(0.2, float(spec.get("aspect_scale") or 1.0)))
    base = (max if mode == "crop" else min)(TW / max(1e-6, cw), TH / max(1e-6, ch))
    fw, fh = cw * base * fz, ch * base * fz
    px, py = aspect_pos(spec)
    return (TW - fw) * px, (TH - fh) * py, fw, fh


def edit_for_ratio(edit: dict, ratio: str) -> dict:
    """Bản chỉnh sửa cho 1 tỉ lệ xuất thêm: tỉ lệ đó + phần chỉnh riêng của tỉ lệ (nền, vị trí phim, vị trí lớp phủ /
    phụ đề đã kéo khi xem thử tỉ lệ này) – edit["ratio_over"][tỉ lệ]."""
    e = json.loads(json.dumps(edit))
    e["aspect"] = ratio
    ov = (edit.get("ratio_over") or {}).get(ratio) or {}
    for k in ("aspect_mode", "aspect_pos", "aspect_scale", "aspect_color"):
        if k in ov:
            e[k] = ov[k]
    lp = ov.get("layers") or {}
    for L in e.get("layers") or []:
        if L.get("id") in lp:
            L.update(lp[L["id"]])
    if ov.get("sub"):
        e.setdefault("subtitles", {}).setdefault("style", {}).update(ov["sub"])
    return e


def ratio_tag(ratio: str) -> str:
    return str(ratio).replace(":", "x")


def keep_ranges(duration: float, trim: dict | None, cuts: list | None) -> list[tuple[float, float]]:
    """Tính các đoạn được giữ lại sau khi cắt đầu/cuối và xoá các đoạn giữa."""
    s = float((trim or {}).get("start") or 0)
    e = float((trim or {}).get("end") or 0) or duration
    e = min(e, duration) if duration else e
    ranges = [(max(0.0, s), e)]
    for c in sorted(cuts or [], key=lambda x: float(x[0])):
        cs, ce = float(c[0]), float(c[1])
        if ce <= cs:
            continue
        new = []
        for a, b in ranges:
            if ce <= a or cs >= b:
                new.append((a, b))
            else:
                if cs > a:
                    new.append((a, cs))
                if ce < b:
                    new.append((ce, b))
        ranges = new
    return [(round(a, 3), round(b, 3)) for a, b in ranges if b - a > 0.05]


def _even(v: float) -> int:
    return max(2, int(round(v / 2)) * 2)


def _hex(c: str, default: str = "000000") -> str:
    c = (c or default).lstrip("#")
    return "0x" + (c if len(c) == 6 else default)


def _atempo_chain(speed: float) -> list[str]:
    out, s = [], speed
    while s > 2.0:
        out.append("atempo=2.0")
        s /= 2
    while s < 0.5:
        out.append("atempo=0.5")
        s /= 0.5
    if abs(s - 1) > 1e-3:
        out.append(f"atempo={s:.4f}")
    return out


def target_size(W: int, H: int, aspect: str, resolution: str) -> tuple[int, int]:
    """Kích thước khung hình đầu ra."""
    res_map = {"480p": 480, "720p": 720, "1080p": 1080, "1440p": 1440, "2160p": 2160}
    ar = aspect_wh(aspect)
    if ar:
        aw, ah = ar
        short = res_map.get(resolution) or min(W, H)
        if aw >= ah:
            h = short
            w = h * aw / ah
        else:
            w = short
            h = w * ah / aw
        return _even(w), _even(h)
    if resolution in res_map:
        short = res_map[resolution]
        if W >= H:
            return _even(W * short / H), _even(short)
        return _even(short), _even(H * short / W)
    return _even(W), _even(H)


def patch_source(r: dict, prefer: str = "auto") -> float:
    """Vá nền: y (0..1) của dải hình cùng cỡ ngay sát vùng chữ dùng để phủ lên chữ cũ. Mặc định lấy phía trên
    (phụ đề thường ở dưới, phía trên là cảnh phim); không đủ chỗ thì lấy phía dưới."""
    y, h = float(r["y"]), float(r["h"])
    gap = min(0.01, h * 0.15)
    above, below = y - h - gap, y + h + gap
    if prefer == "below" and below + h <= 1:
        return below
    if above >= 0 and prefer != "below":
        return above
    if below + h <= 1:
        return below
    return max(0.0, min(1.0 - h, above))


def patch_feather(w: int, h: int) -> int:
    """Độ rộng viền mờ dần (điểm ảnh) để dải vá hoà vào hình xung quanh."""
    return max(2, min(w, h) // 5)


BLUR_KINDS = (("box", "Mờ thường"), ("gauss", "Mờ mịn (kính mờ)"), ("pixel", "Khảm ô vuông (pixel)"))
STRETCH_FROM = (("both", "Từ 2 mép trên + dưới (nối dần, ít lộ nhất)"), ("above", "Kéo từ mép trên xuống"),
                ("below", "Kéo từ mép dưới lên"))
STRIP = 2    # Kéo dọc: số dòng điểm ảnh ngay sát ngoài vùng chữ được kéo dài phủ kín vùng


def stretch_rows(H: int, y: int, h: int, side: str = "both") -> tuple[int | None, int | None]:
    """Kéo dọc: hàng bắt đầu của dải STRIP dòng phía trên / phía dưới vùng chữ (None = không dùng phía đó). Vùng sát
    mép khung thì tự dùng phía còn lại."""
    top = y - STRIP if y >= STRIP else None
    bot = y + h if y + h + STRIP <= H else None
    if side == "above" and top is not None:
        bot = None
    elif side == "below" and bot is not None:
        top = None
    if top is None and bot is None:
        top = max(0, min(H - STRIP, y))
    return (None if top is None else top // 2 * 2), (None if bot is None else bot // 2 * 2)


def stretch_filter(hs: dict, W: int, H: int, x: int, y: int, w: int, h: int, src: str, tag: str) -> tuple[str, str]:
    """Chuỗi lọc FFmpeg "Kéo dọc": dải điểm ảnh sát mép trên / dưới vùng chữ kéo giãn phủ kín vùng (2 mép: nối dần từ
    màu mép trên xuống màu mép dưới) -> không còn điểm ảnh nào của chữ cũ; làm mịn ngang cho đỡ vệt sọc.
    Trả (đoạn lọc, nhãn đầu ra cỡ w × h)."""
    top, bot = stretch_rows(H, y, h, hs.get("stretch_from") or "both")
    sm = max(0.0, float(hs.get("stretch_smooth", 3) or 0))
    parts, labs = [], []
    for name, row in (("t", top), ("b", bot)):
        if row is not None:
            labs.append(f"st{name}{tag}")
    parts.append(f"[{src}]split={len(labs)}" + "".join(f"[{lb}i]" for lb in labs))
    for lb, row in zip(labs, [r for r in (top, bot) if r is not None]):
        parts.append(f"[{lb}i]crop={w}:{STRIP}:{x}:{row},scale={w}:{h}:flags=bilinear,setsar=1[{lb}]")
    out = labs[0]
    if len(labs) == 2:
        parts.append(f"[{labs[0]}][{labs[1]}]blend=all_expr='A*(H-1-Y)/(H-1)+B*Y/(H-1)'[stj{tag}]")
        out = f"stj{tag}"
    if sm >= 0.5:
        parts.append(f"[{out}]gblur=sigma={sm:.1f}:sigmaV=0.01[sts{tag}]")
        out = f"sts{tag}"
    return ";".join(parts), out


def stretch_fill(img, x0: int, y0: int, x1: int, y1: int, side: str = "both", smooth: float = 3.0):
    """Kéo dọc cho ảnh numpy BGR (Xem thử 1 khung) – cùng cách với `stretch_filter`. Trả ảnh vùng (h × w × 3, float)."""
    import numpy as np

    H = img.shape[0]
    h = y1 - y0
    top, bot = stretch_rows(H, y0, h, side)
    rows = [img[r:r + STRIP, x0:x1].astype(np.float32).mean(0) for r in (top, bot) if r is not None]
    if len(rows) == 2:
        t = (np.arange(h, dtype=np.float32) / max(1, h - 1))[:, None, None]
        out = rows[0][None] * (1 - t) + rows[1][None] * t
    else:
        out = np.repeat(rows[0][None], h, axis=0)
    if smooth >= 0.5:
        import cv2

        out = cv2.GaussianBlur(out, (0, 0), sigmaX=float(smooth), sigmaY=0.01)
    return out


def shape_px(hs: dict, w: int, h: int) -> tuple[float, float]:
    """(bán kính bo góc, độ rộng viền mờ dần) điểm ảnh của 1 vùng che / làm mờ: tính theo cạnh ngắn của vùng
    (0,5 = tròn hẳn 2 đầu / mờ dần tới tận giữa)."""
    m = min(w, h)
    r = max(0.0, min(0.5, float(hs.get("round") or 0))) * m
    f = max(0.0, min(0.5, float(hs.get("feather") or 0))) * m
    return r, f


def _sdf_terms(w: int, h: int, r: float) -> tuple[float, float, float, float]:
    return w / 2, h / 2, max(0.0, w / 2 - r), max(0.0, h / 2 - r)


def mask_expr(w: int, h: int, r: float, f: float, scale: float = 255.0) -> str:
    """Biểu thức geq của FFmpeg: mặt nạ hình chữ nhật bo góc r, viền mờ dần f (điểm ảnh). Cùng công thức với
    `round_mask` (xem trước)."""
    cx, cy, ax, ay = _sdf_terms(w, h, r)
    qx, qy = f"(abs(X+0.5-{cx:.2f})-{ax:.2f})", f"(abs(Y+0.5-{cy:.2f})-{ay:.2f})"
    d = f"(hypot(max({qx},0),max({qy},0))+min(max({qx},{qy}),0)-{r:.2f})"
    return f"{scale:.2f}*clip(-{d}/{max(1.0, f):.2f},0,1)"


def round_mask(w: int, h: int, r: float, f: float):
    """Mặt nạ 0..1 (h × w) giống hệt `mask_expr` – cho khung xem trước / Xem thử 1 khung."""
    import numpy as np

    cx, cy, ax, ay = _sdf_terms(w, h, r)
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    qx, qy = np.abs(xx + 0.5 - cx) - ax, np.abs(yy + 0.5 - cy) - ay
    d = np.hypot(np.maximum(qx, 0), np.maximum(qy, 0)) + np.minimum(np.maximum(qx, qy), 0) - r
    return np.clip(-d / max(1.0, f), 0, 1).astype(np.float32)


def blur_filter(hs: dict, w: int, h: int) -> str:
    """Chuỗi lọc FFmpeg làm mờ 1 vùng w × h theo kiểu mờ + độ mờ + phủ tối."""
    st = int(hs.get("blur_strength", 20))
    kind = hs.get("blur_kind") or "box"
    if kind == "pixel":
        n = max(2, st // 2)
        out = f"scale={max(1, w // n)}:{max(1, h // n)}:flags=area,scale={w}:{h}:flags=neighbor"
    elif kind == "gauss":
        out = f"gblur=sigma={max(1.0, st * 0.6):.1f}:steps=3"
    else:
        lr = max(1, min(st, min(w, h) // 2 - 1))
        cr = max(1, min(st, min(w, h) // 4 - 1))  # kênh màu có độ phân giải 1/2
        out = f"boxblur=luma_radius={lr}:luma_power=3:chroma_radius={cr}:chroma_power=3"
    dk = max(0.0, min(0.8, float(hs.get("darken") or 0)))
    if dk > 0.005:
        out += f",lutyuv=y=val*{1 - dk:.3f}"
    return out


def needs_clean(hs: dict) -> bool:
    """Có phần phải xử lý trước bằng Python (tạo video trung gian): xoá phụ đề bằng AI / nền khung khác, gỡ logo."""
    return bool(hs.get("logos")) or (hs.get("mode") == "inpaint" and bool(hs.get("regions")))


def inpaint_key(src: str | Path, hs: dict, settings: dict) -> str:
    """Mã của 1 lần xoá: đổi video gốc / vùng / logo / công nghệ / độ mạnh / file OCR / tuỳ chọn -> mã khác."""
    import hashlib

    src = Path(src)
    st = src.stat() if src.exists() else None
    sub = hs.get("mode") == "inpaint"
    boxes = hs.get("boxes_file") if sub else None
    bst = Path(boxes).stat() if boxes and Path(boxes).exists() else None

    def rr(lst):
        return [[round(float(r[k]), 4) for k in ("x", "y", "w", "h")] for r in lst or []]
    key = {"src": str(src), "size": st.st_size if st else 0, "mtime": int(st.st_mtime) if st else 0,
           "regions": rr(hs.get("regions")) if sub else [], "logos": rr(hs.get("logos")),
           "backend": hs.get("backend") or settings.get("inpaint_backend", "lama"),
           "strength": hs.get("strength", "normal"),
           "boxes": [str(boxes), bst.st_size, int(bst.st_mtime)] if bst else None,
           "temporal": hs.get("temporal", True) is not False, "cover": bool(hs.get("cover_box")),
           "algo": 2, "codec": settings.get("video_codec"), "crf": min(18, int(settings.get("video_crf", 20)))}
    return hashlib.sha1(json.dumps(key, sort_keys=True).encode()).hexdigest()[:12]


def inpainted_file(src: str | Path, hs: dict, settings: dict, work: str | Path) -> Path:
    return Path(work) / f"inpainted_{inpaint_key(src, hs, settings)}.mp4"


def inpaint_cached(src: str | Path, hs: dict, settings: dict, work: str | Path,
                   progress: ProgressCB | None = None, cancel: Callable[[], bool] | None = None) -> Path:
    """Xoá phụ đề cũ (AI / nền khung khác) + gỡ logo cố định cho cả video, lưu lại để xem trước / xuất nhiều lần không
    phải chạy lại. Chỉ giữ 1 bản (bản cũ của vùng khác bị xoá cho đỡ tốn ổ đĩa)."""
    from .inpaint import inpaint_video

    work = Path(work)
    work.mkdir(parents=True, exist_ok=True)
    out = inpainted_file(src, hs, settings, work)
    if out.exists():
        return out
    tmp = out.with_name(out.stem + "_tmp.mp4")
    st_inp = dict(settings)
    if hs.get("backend"):
        st_inp["inpaint_backend"] = hs["backend"]
    sub = hs.get("mode") == "inpaint"
    inpaint_video(src, tmp, hs.get("regions") if sub else [], st_inp,
                  boxes_file=hs.get("boxes_file") if (sub and hs.get("use_ocr_boxes", True)) else None,
                  progress=progress, cancel=cancel, strength=hs.get("strength", "normal"), logos=hs.get("logos"),
                  temporal=hs.get("temporal", True) is not False, cover_box=bool(hs.get("cover_box")), work=work)
    tmp.replace(out)
    for old in list(work.glob("inpainted*.mp4")):
        if old != out:
            try:
                old.unlink()
            except OSError:
                pass    # trình phát đang mở -> xoá lần sau
    return out


def out_time(t: float, keep: list[tuple[float, float]], speed: float = 1.0) -> float:
    """Giây trên video gốc -> giây trên video xuất ra (sau khi cắt đầu / cuối / giữa và đổi tốc độ)."""
    acc = 0.0
    for a, b in keep:
        if t <= a:
            break
        acc += min(t, b) - a
    return acc / (speed or 1.0)


# ------------------------------------------------------------------ xuất theo từng đoạn + nhớ đoạn đã xuất
# Phim dài: hình chia thành đoạn CHUNK_SEC giây (theo giờ XUẤT, đúng lưới khung), mỗi đoạn mã hoá riêng với "mã" = lệnh
# FFmpeg của đoạn + file nguồn + các dòng phụ đề / chữ nằm trong đoạn; âm thanh trộn 1 lần cho cả phim; nối các đoạn
# bằng concat không nén lại. Xuất lại sau khi sửa vài câu -> chỉ mã hoá lại các đoạn có câu đổi (vài chục giây thay vì
# cả giờ). Không chia khi có chèn video / đổi tốc độ (giờ xuất không theo lưới giờ gốc) hoặc video ngắn.
CHUNK_SEC = 60.0
CHUNK_MIN_DUR = 600.0       # video xuất ≥ 10 phút mới chia đoạn
CHUNK_JOBS = 2              # số đoạn mã hoá cùng lúc


def can_chunk(spec: dict, settings: dict, info, speed: float, final_dur: float) -> bool:
    return bool(settings.get("export_chunks", True) and info.has_video and abs(speed - 1) < 1e-3
                and not spec.get("inserts") and final_dur >= CHUNK_MIN_DUR)


def _grid_ranges(keep: list[tuple[float, float]], fps: float) -> list[tuple[int, int]]:
    """Khung (chỉ số lưới k/fps theo giây gốc, gồm cả 2 đầu) của từng đoạn giữ lại."""
    import math

    out = []
    for a, b in keep:
        lo, hi = math.ceil(a * fps - 1e-6), math.ceil(b * fps - 1e-6) - 1
        if hi >= lo:
            out.append((lo, hi))
    return out


def _chunk_windows(ranges: list[tuple[int, int]], k0: int, k1: int) -> list[tuple[int, int]]:
    """Khung gốc (lo, hi) ứng với khung xuất [k0, k1) – ghép các đoạn giữ lại theo thứ tự."""
    out, pos = [], 0
    for lo, hi in ranges:
        n = hi - lo + 1
        a, b = max(k0, pos), min(k1, pos + n)
        if b > a:
            out.append((lo + a - pos, lo + b - 1 - pos))
        pos += n
    return out


def _ass_slice(path: Path, t0: float, t1: float) -> str:
    """Phần file ASS ảnh hưởng tới khoảng [t0, t1] giờ xuất: đầu file (kiểu chữ) + các dòng Dialogue chạm khoảng đó."""
    if not path.exists():
        return ""

    def sec(x: str) -> float:
        h, m, s = x.strip().split(":")
        return int(h) * 3600 + int(m) * 60 + float(s)

    head, dlg = [], []
    for ln in path.read_text("utf-8", errors="replace").splitlines():
        if ln.startswith("Dialogue:"):
            parts = ln.split(",", 3)
            try:
                a, b = sec(parts[1]), sec(parts[2])
            except (IndexError, ValueError):
                dlg.append(ln)
                continue
            if b >= t0 - 0.05 and a <= t1 + 0.05:
                dlg.append(ln)
        else:
            head.append(ln)
    return "\n".join(head + dlg)


def _file_sig(args: list[str]) -> list:
    """Kích thước + giờ sửa của mọi file đầu vào (-i) – thay file cùng tên thì mã đoạn đổi."""
    sig = []
    for k, a in enumerate(args[:-1]):
        if a == "-i":
            p = Path(args[k + 1])
            try:
                st = p.stat()
                sig.append([str(p), st.st_size, int(st.st_mtime)])
            except OSError:
                sig.append([str(p), 0, 0])
    return sig


def _render_chunked(src: Path, out: Path, spec: dict, settings: dict, work: Path, segments, info, keep,
                    progress: ProgressCB | None, cancel) -> Path:
    import hashlib
    import json
    from concurrent.futures import ThreadPoolExecutor

    fps = float(spec.get("fps") or 0) or (info.fps if 1 <= (info.fps or 0) <= 120 else 25.0)
    ranges = _grid_ranges(keep, fps)
    total = sum(hi - lo + 1 for lo, hi in ranges)
    if total <= 0:
        raise ValueError("Không còn đoạn video nào sau khi cắt.")
    per = max(1, int(round(CHUNK_SEC * fps)))
    cdir = work / ("chunks_" + spec["_tag"] if spec.get("_tag") else "chunks")   # mỗi tỉ lệ xuất 1 kho đoạn riêng
    cdir.mkdir(parents=True, exist_ok=True)
    chunks = []          # (đường dẫn, lệnh, số khung, nhãn)
    for j, k0 in enumerate(range(0, total, per)):
        k1 = min(total, k0 + per)
        win = _chunk_windows(ranges, k0, k1)
        lo_m, hi_m = win[0][0], win[-1][1]
        X = max(0.0, lo_m / fps - 1.0)       # giải mã từ 1 s trước khung đầu (bộ fps cần khung trước để lấp lưới)
        vp = {"X": X, "span": (hi_m + 1) / fps - X + 0.5, "fps": fps, "n": k1 - k0, "o0": k0 / fps,
              "expr": "+".join(f"between(t,{(a - 0.5) / fps:.6f},{(b + 0.5) / fps:.6f})" for a, b in win),
              "prebuilt": j > 0}
        tmp = cdir / "_chunk.mp4"
        args = build_and_render(src, tmp, spec, settings, work, segments, None, cancel, _part={"video": vp, "info": info})
        t0, t1 = k0 / fps, k1 / fps
        key = json.dumps({"a": [a for a in args if a != str(tmp.resolve())], "f": _file_sig(args),
                          "sub": _ass_slice(work / "burn.ass", t0, t1)}, ensure_ascii=False)
        path = cdir / f"c_{hashlib.sha1(key.encode()).hexdigest()[:20]}.mp4"
        args[-1] = str(path.with_suffix(".part.mp4").resolve())
        chunks.append((path, args, k1 - k0, j))
    todo = [c for c in chunks if not (c[0].exists() and c[0].stat().st_size > 0)]
    done_frames = [sum(c[2] for c in chunks) - sum(c[2] for c in todo)]
    lock = __import__("threading").Lock()
    if progress:
        progress(0.0, f"Xuất theo đoạn: {len(chunks)} đoạn, dùng lại {len(chunks) - len(todo)} đoạn đã xuất, "
                      f"mã hoá {len(todo)} đoạn")

    def run(c):
        path, args, n, j = c
        part = Path(args[-1])

        def pr(x, m):
            if progress:
                with lock:
                    frac = (done_frames[0] + x * n) / total
                progress(0.88 * frac, f"Đang xuất đoạn {j + 1}/{len(chunks)} (mã hoá lại {len(todo)} đoạn có thay đổi)")
        ff.run(args, n / fps, pr, cancel, "Đang xuất", cwd=work)
        part.replace(path)
        with lock:
            done_frames[0] += n

    if todo:
        with ThreadPoolExecutor(max_workers=min(CHUNK_JOBS, len(todo))) as ex:
            list(ex.map(run, todo))
    # âm thanh: trộn cả phim 1 lần (như xuất thường), nhớ theo lệnh + file nguồn
    aud = None
    tmp_a = cdir / "_audio.m4a"
    a_args = build_and_render(src, tmp_a, spec, settings, work, segments, None, cancel, _part={"audio": True, "info": info})
    if a_args:
        key = json.dumps({"a": [a for a in a_args if a != str(tmp_a.resolve())], "f": _file_sig(a_args)},
                         ensure_ascii=False)
        aud = cdir / f"a_{hashlib.sha1(key.encode()).hexdigest()[:20]}.m4a"
        if not (aud.exists() and aud.stat().st_size > 0):
            a_args[-1] = str(aud.with_suffix(".part.m4a").resolve())

            def pa(x, m):
                if progress:
                    progress(0.88 + 0.08 * x, "Đang trộn âm thanh")
            ff.run(a_args, total / fps, pa, cancel, "Đang trộn âm thanh", cwd=work)
            Path(a_args[-1]).replace(aud)
    lst = cdir / "list.txt"
    lst.write_text("".join("file '" + c[0].resolve().as_posix().replace("'", "'\\''") + "'\n" for c in chunks),
                   "utf-8")
    cmd = ["-f", "concat", "-safe", "0", "-i", str(lst.resolve())]
    if aud:
        cmd += ["-i", str(aud.resolve()), "-map", "0:v", "-map", "1:a", "-shortest"]
    cmd += ["-c", "copy", "-movflags", "+faststart", str(out.resolve())]
    (work / "last_ffmpeg_cmd.txt").write_text(" ".join(chunks[0][1]) + "\n\n" + " ".join(cmd), "utf-8")
    ff.run(cmd, total / fps, (lambda x, m: progress(0.96 + 0.04 * x, "Đang nối các đoạn")) if progress else None,
           cancel, "Đang nối các đoạn", cwd=work)
    keepn = {c[0].name for c in chunks} | ({aud.name} if aud else set())
    for old in list(cdir.glob("c_*.mp4")) + list(cdir.glob("a_*.m4a")) + list(cdir.glob("*.part.*")):
        if old.name not in keepn:
            try:
                old.unlink()
            except OSError:
                pass
    if progress:
        progress(1.0, f"Đã xuất ({len(chunks) - len(todo)}/{len(chunks)} đoạn dùng lại, mã hoá {len(todo)} đoạn)")
    return out


def build_and_render(src: str | Path, out: str | Path, spec: dict, settings: dict, work_dir: str | Path,
                     segments: list[Segment] | None = None, progress: ProgressCB | None = None,
                     cancel: Callable[[], bool] | None = None, _part: dict | None = None):
    """Xuất video (1 lần mã hoá). Video dài -> chia đoạn + nhớ đoạn đã xuất (`_render_chunked`).
    _part (dùng nội bộ khi chia đoạn): {"video": {...1 đoạn...}} / {"audio": True} -> chỉ TRẢ VỀ lệnh FFmpeg của phần
    đó (không chạy); src lúc đó đã là video đã xoá phụ đề (nếu có)."""
    src, out, work = Path(src), Path(out), Path(work_dir)
    work.mkdir(parents=True, exist_ok=True)
    if (spec.get("watermark") or {}).get("path") or ((spec.get("text") or {}).get("content") or "").strip():
        from .overlay import migrate     # chữ / logo kiểu cũ -> lớp phủ

        spec = json.loads(json.dumps(spec))
        migrate(spec)
    info = (_part or {}).get("info") or ff.probe(src)
    if not info.has_video:
        raise ff.FFmpegError("File đầu vào không có hình ảnh.")
    W, H, dur = info.width, info.height, info.duration

    # Tiền xử lý: xoá phụ đề bằng AI inpaint (tạo video trung gian; đã chạy với đúng vùng/tuỳ chọn -> dùng lại)
    hs = spec.get("hardsub") or {}
    if needs_clean(hs) and _part is None:
        cached = inpainted_file(src, hs, settings, work)
        if cached.exists():
            if progress:
                progress(0.0, "Dùng lại video đã xoá phụ đề / logo")
            src = cached
            base_p, span_p = 0.0, 1.0
        else:
            def p2(x, m):
                if progress:
                    progress(x * 0.6, m)
            src = inpaint_cached(src, hs, settings, work, p2, cancel)
            base_p, span_p = 0.6, 0.4
    else:
        base_p, span_p = 0.0, 1.0

    keep = keep_ranges(dur, spec.get("trim"), spec.get("cuts"))
    if not keep:
        raise ValueError("Không còn đoạn video nào sau khi cắt.")
    speed = float(spec.get("speed") or 1.0)
    speed = min(4.0, max(0.25, speed))
    final_dur = sum(b - a for a, b in keep) / speed
    if _part is None and can_chunk(spec, settings, info, speed, final_dur):
        def pc(x, m):
            if progress:
                progress(base_p + span_p * x, m)
        return _render_chunked(src, out, spec, settings, work, segments, info, keep, pc, cancel)
    do_video = _part is None or "video" in _part
    do_audio = _part is None or "audio" in _part
    vpart = (_part or {}).get("video")

    # ---- 1. cắt / ghép các đoạn giữ lại
    #  - Cắt đầu/cuối: seek ngay ở đầu vào (-ss/-t) -> không phải giải mã phần bị bỏ, rất nhanh với video dài.
    #  - Xoá đoạn giữa: select/aselect một luồng duy nhất -> bộ nhớ không đổi dù có hàng trăm đoạn cắt.
    seek0, seek1 = keep[0][0], keep[-1][1]
    rel_keep = [(round(a - seek0, 3), round(b - seek0, 3)) for a, b in keep]
    in_opts: list[str] = []
    if seek0 > 0.01:
        in_opts += ["-ss", f"{seek0:.3f}"]
    if seek1 < dur - 0.05 or seek0 > 0.01:
        in_opts += ["-t", f"{seek1 - seek0:.3f}"]
    if vpart:   # 1 đoạn của bản xuất chia đoạn: chỉ giải mã đúng khúc nguồn của đoạn đó
        in_opts = ["-ss", f"{vpart['X']:.3f}", "-t", f"{vpart['span']:.3f}"]
    inputs: list[str] = in_opts + ["-i", str(src.resolve())]
    n_in = 1
    fc: list[str] = []
    full = len(keep) == 1 and keep[0][0] <= 0.01 and abs(keep[0][1] - dur) < 0.05
    has_a = info.has_audio
    fps = info.fps if 1 <= (info.fps or 0) <= 120 else 25
    # đoạn tắt TIẾNG GỐC trên timeline (mốc của luồng sau -ss = giây gốc − seek0)
    from .audiomix import mute_expr

    mexpr = mute_expr(spec.get("mute_spans") or [], seek0)
    mute_f = f"volume=volume=0:enable='{mexpr}'," if mexpr else ""
    expr = "+".join(f"between(t,{a:.3f},{b - 0.001:.3f})" for a, b in rel_keep)
    if vpart:
        # mốc thời gian GỐC (cộng lại phần đã seek) -> lưới khung cố định theo giây gốc, giống nhau ở mọi đoạn ->
        # chọn đúng các khung của đoạn (biên giữa 2 khung) -> các đoạn nối lại không thừa / thiếu khung nào
        fc.append(f"[0:v]setpts=PTS+{vpart['X']:.6f}/TB,fps={vpart['fps']:.6f},select='{vpart['expr']}',"
                  "setpts=N/FRAME_RATE/TB[v0]")
    elif do_video and len(keep) == 1:
        fc.append("[0:v]setpts=PTS-STARTPTS[v0]")
    elif do_video:
        fc.append(f"[0:v]fps={fps:.5f},select='{expr}',setpts=N/FRAME_RATE/TB[v0]")
    if do_audio and has_a:
        # tiếng gốc theo đúng giờ của hình (2.0.92) – trước đây mẫu nối liền: video có giờ âm thanh lệch dần thì tiếng
        # gốc (và mọi thứ tính từ nó) trôi so với khẩu hình
        sync = ff.audio_sync(src, 44100) or "aresample=44100"
        if len(keep) == 1:
            fc.append(f"[0:a]{sync},{mute_f}asetpts=PTS-STARTPTS[a0]")
        else:
            fc.append(f"[0:a]{sync},{mute_f}aselect='{expr}',asetpts=N/SR/TB[a0]")
    v = "v0"
    ins: list = []
    main_dur = final_dur
    cuts_at = [0.0, main_dur]
    fade = spec.get("fade") or {}
    fin, fout = float(fade.get("in") or 0), float(fade.get("out") or 0)
    TW = TH = 0

    if do_video:
        # ---- 2. che/xoá phụ đề cũ (toạ độ khung gốc)
        mode = hs.get("mode", "none")
        if mode in ("blur", "box", "delogo", "patch", "stretch") and hs.get("regions"):
            for k, r in enumerate(hs["regions"]):
                x, y = int(float(r["x"]) * W), int(float(r["y"]) * H)
                w, h = _even(float(r["w"]) * W), _even(float(r["h"]) * H)
                w, h = min(w, W - x), min(h, H - y)
                rad, fea = shape_px(hs, w, h)
                shaped = rad >= 0.5 or fea >= 0.5      # bo góc / viền mờ dần: phủ qua mặt nạ alpha
                if mode == "box" and shaped:
                    col = _hex(hs.get("box_color", "#000000"))
                    op = float(hs.get("box_opacity", 1.0))
                    fc.append(f"[{v}]split[hm{k}][hc{k}];[hc{k}]crop={w}:{h}:{x}:{y},drawbox=x=0:y=0:w=iw:h=ih:"
                              f"color={col}@1:t=fill,format=yuva420p,split[ba{k}][bb{k}];"
                              f"[bb{k}]format=gray,geq=lum='{mask_expr(w, h, rad, fea, 255 * op)}'[bm{k}];"
                              f"[ba{k}][bm{k}]alphamerge[bl{k}];[hm{k}][bl{k}]overlay={x}:{y}[hb{k}]")
                elif mode == "box":
                    col = _hex(hs.get("box_color", "#000000"))
                    op = float(hs.get("box_opacity", 1.0))
                    fc.append(f"[{v}]drawbox=x={x}:y={y}:w={w}:h={h}:color={col}@{op:.2f}:t=fill[hb{k}]")
                elif mode == "patch":
                    # vá nền: chép dải hình ngay sát vùng chữ (theo từng khung, nên nền chuyển động vẫn khớp) đè lên,
                    # viền mờ dần cho hoà vào xung quanh
                    sy = min(H - h, max(0, int(patch_source(r, hs.get("patch_from", "auto")) * H)))
                    f = patch_feather(w, h)
                    fc.append(f"[{v}]split[hm{k}][hp{k}];[hp{k}]crop={w}:{h}:{x}:{sy},format=yuva420p,split[pa{k}][pb{k}];"
                              f"[pb{k}]format=gray,geq=lum='255*min(1,min(min(X,W-1-X),min(Y,H-1-Y))/{f})'[pm{k}];"
                              f"[pa{k}][pm{k}]alphamerge[pp{k}];[hm{k}][pp{k}]overlay={x}:{y}[hb{k}]")
                elif mode == "stretch":
                    # kéo dọc: dòng điểm ảnh sát mép vùng kéo dài phủ kín chữ (theo từng khung -> cảnh động vẫn khớp màu)
                    x, y = x // 2 * 2, y // 2 * 2
                    sf, sl = stretch_filter(hs, W, H, x, y, w, h, f"hc{k}", f"{k}")
                    if shaped:
                        fc.append(f"[{v}]split[hm{k}][hc{k}];{sf};[{sl}]format=yuva420p,split[ba{k}][bb{k}];"
                                  f"[bb{k}]format=gray,geq=lum='{mask_expr(w, h, rad, fea)}'[bm{k}];"
                                  f"[ba{k}][bm{k}]alphamerge[bl{k}];[hm{k}][bl{k}]overlay={x}:{y}[hb{k}]")
                    else:
                        fc.append(f"[{v}]split[hm{k}][hc{k}];{sf};[hm{k}][{sl}]overlay={x}:{y}[hb{k}]")
                elif mode == "delogo":
                    x1, y1 = max(1, x), max(1, y)
                    w1, h1 = min(w, W - x1 - 1), min(h, H - y1 - 1)
                    fc.append(f"[{v}]delogo=x={x1}:y={y1}:w={w1}:h={h1}[hb{k}]")
                elif shaped:     # làm mờ bo góc / viền mờ dần hoà vào hình xung quanh
                    fc.append(f"[{v}]split[hm{k}][hc{k}];[hc{k}]crop={w}:{h}:{x}:{y},{blur_filter(hs, w, h)},"
                              f"format=yuva420p,split[ba{k}][bb{k}];"
                              f"[bb{k}]format=gray,geq=lum='{mask_expr(w, h, rad, fea)}'[bm{k}];"
                              f"[ba{k}][bm{k}]alphamerge[bl{k}];[hm{k}][bl{k}]overlay={x}:{y}[hb{k}]")
                else:
                    fc.append(f"[{v}]split[hm{k}][hc{k}];[hc{k}]crop={w}:{h}:{x}:{y},{blur_filter(hs, w, h)}[hbl{k}];"
                              f"[hm{k}][hbl{k}]overlay={x}:{y}[hb{k}]")
                v = f"hb{k}"

        chain: list[str] = []
        cw, ch = W, H
        # ---- 3. crop
        crop = spec.get("crop")
        if crop and (float(crop.get("w", 1)) < 0.999 or float(crop.get("h", 1)) < 0.999):
            cx, cy = int(float(crop["x"]) * W), int(float(crop["y"]) * H)
            cw, ch = _even(float(crop["w"]) * W), _even(float(crop["h"]) * H)
            cw, ch = min(cw, W - cx), min(ch, H - cy)
            chain.append(f"crop={cw}:{ch}:{cx}:{cy}")
        # ---- 4. lật / xoay
        if spec.get("flip_h"):
            chain.append("hflip")
        if spec.get("flip_v"):
            chain.append("vflip")
        rot = int(spec.get("rotate") or 0) % 360
        if rot == 90:
            chain.append("transpose=1")
            cw, ch = ch, cw
        elif rot == 270:
            chain.append("transpose=2")
            cw, ch = ch, cw
        elif rot == 180:
            chain.append("hflip,vflip")
        # ---- 5. phóng to nhẹ (lách bản quyền)
        zoom = float(spec.get("zoom") or 1.0)
        if zoom > 1.001:
            zw, zh = _even(cw / zoom), _even(ch / zoom)
            chain.append(f"crop={zw}:{zh}:(iw-{zw})/2:(ih-{zh})/2,scale={cw}:{ch}")
        # ---- 6. màu sắc
        col = spec.get("color") or {}
        b, c, s_ = float(col.get("brightness", 0)), float(col.get("contrast", 1)), float(col.get("saturation", 1))
        g = float(col.get("gamma", 1))
        if abs(b) > 1e-3 or abs(c - 1) > 1e-3 or abs(s_ - 1) > 1e-3 or abs(g - 1) > 1e-3:
            chain.append(f"eq=brightness={b:.3f}:contrast={c:.3f}:saturation={s_:.3f}:gamma={g:.3f}")
        if col.get("sharpen"):
            chain.append("unsharp=5:5:0.8:3:3:0.0")
        if col.get("denoise"):
            chain.append("hqdn3d=2:1:3:3")
        # ---- 7. tốc độ
        if abs(speed - 1) > 1e-3:
            chain.append(f"setpts=PTS/{speed:.4f}")
        if spec.get("fps") and not vpart:      # chia đoạn: đã đưa về đúng số khung/giây ở bước 1
            chain.append(f"fps={float(spec['fps'])}")
        if chain:
            fc.append(f"[{v}]{','.join(chain)}[vc]")
            v = "vc"

        # ---- 8. tỉ lệ khung hình / độ phân giải (nền mờ / đen / màu / cắt đầy khung + vị trí phim + phóng)
        aspect = spec.get("aspect", "original")
        res = spec.get("resolution", "original")
        TW, TH = target_size(cw, ch, aspect, res)
        if aspect_wh(aspect):
            amode = spec.get("aspect_mode", "blur")
            gx, gy, gw, gh = fit_geometry(cw, ch, TW, TH, spec)
            fw, fh = _even(gw), _even(gh)
            ox, oy = int(round(gx / 2)) * 2, int(round(gy / 2)) * 2
            fg = [f"scale={fw}:{fh}"]
            if ox < 0 or oy < 0 or ox + fw > TW or oy + fh > TH:    # phần phim tràn ra ngoài khung: cắt bỏ
                x0, y0 = max(0, -ox), max(0, -oy)
                vw, vh = min(fw - x0, TW - max(0, ox)), min(fh - y0, TH - max(0, oy))
                vw, vh = max(2, vw // 2 * 2), max(2, vh // 2 * 2)
                fg.append(f"crop={vw}:{vh}:{x0}:{y0}")
                ox, oy, fw, fh = max(0, ox), max(0, oy), vw, vh
            if (fw, fh) == (TW, TH):
                fc.append(f"[{v}]{','.join(fg)},setsar=1[vr]")
            elif amode == "blur":
                fc.append(f"[{v}]split[bg0][fg0];[bg0]scale={TW}:{TH}:force_original_aspect_ratio=increase,"
                          f"crop={TW}:{TH},boxblur=luma_radius=min(h\\,w)/20:luma_power=2,eq=brightness=-0.08[bg1];"
                          f"[fg0]{','.join(fg)}[fg1];[bg1][fg1]overlay={ox}:{oy},setsar=1[vr]")
            else:
                col = "black" if amode in ("pad", "crop") else _hex(spec.get("aspect_color") or "#000000")
                fc.append(f"[{v}]{','.join(fg)},pad={TW}:{TH}:{ox}:{oy}:color={col},setsar=1[vr]")
            v = "vr"
        elif (TW, TH) != (cw, ch):
            fc.append(f"[{v}]scale={TW}:{TH}:flags=lanczos,setsar=1[vr]")
            v = "vr"

        # thư mục font cục bộ để libass/drawtext dùng đường dẫn tương đối (tránh lỗi escape trên Windows)
        fonts_local = work / "fonts"
        fonts_local.mkdir(parents=True, exist_ok=True)
        for f in FONTS_DIR.glob("*.[ot]tf"):          # chép cả font mới thêm vào bản cập nhật (vd. Bangers)
            dst = fonts_local / f.name
            if not dst.exists() or dst.stat().st_size != f.stat().st_size:
                shutil.copy2(f, dst)

        if vpart:   # đoạn của bản chia đoạn: dời mốc khung về giờ XUẤT thật -> chữ / phụ đề / mờ dần đúng chỗ
            fc.append(f"[{v}]format=yuv420p,setpts=PTS+{vpart['o0']:.6f}/TB[voff]")
            v = "voff"
        # ---- 10. lớp phủ: chữ + logo (trami/overlay.py – ảnh vẽ bằng Qt giống hệt khung xem trước; GIF động giải mã
        # từng khung). Mốc theo giờ video XUẤT (đã dời theo đoạn cắt / tốc độ); đoạn của bản chia đoạn chỉ lấy lớp có mặt
        from . import overlay

        win = (vpart["o0"], vpart["o0"] + vpart["n"] / vpart["fps"]) if vpart else None
        l_in, l_fc, v, n_in = overlay.build_overlays(spec.get("layers") or [], TW, TH, vpart["fps"] if vpart else fps,
                                                     dur, keep, speed, work, v, n_in, win)
        inputs += l_in
        fc += l_fc

        # ---- 11. phụ đề mới
        sub = spec.get("subtitles") or {}
        if sub.get("burn") and segments:
            segs = [Segment(s.start, s.end, s.text, s.trans, extra={"kv": s.extra["kv"]} if s.extra.get("kv") else {})
                    for s in segments]
            if not full:
                segs = remap_after_cut(segs, keep)
            segs = scale_time(segs, speed)
            style = dict(settings.get("sub_style") or {})
            style.update(sub.get("style") or {})
            which = sub.get("which", "trans")
            if not (vpart and vpart.get("prebuilt")):   # các đoạn dùng chung 1 file phụ đề (dựng ở đoạn đầu)
                save_subtitles(segs, work / "burn.ass", which=which, style=style, width=TW, height=TH)
            fc.append(f"[{v}]subtitles=burn.ass:fontsdir=fonts[vs]")
            v = "vs"

        # ---- 11b. video chèn thêm (timeline: "Chèn video…") – ghép vào đúng chỗ trên video XUẤT RA (sau khi cắt / đổi
        # tốc độ); đoạn chèn giữ hình + tiếng của chính nó, không có phụ đề / lồng tiếng
        ins = []
        for it in spec.get("inserts") or []:
            ip = Path(it.get("path") or "")
            if not ip.exists():
                raise FileNotFoundError(f"Không tìm thấy video chèn: {ip}")
            ii = ff.probe(ip)
            if not ii.has_video or ii.duration <= 0.05:
                continue
            at = out_time(float(it.get("at") or 0), keep, speed)
            ins.append((min(at, final_dur), ip, ii))
        ins.sort(key=lambda x: x[0])
        main_dur = final_dur
        cuts_at = [0.0] + [x[0] for x in ins] + [main_dur]
        if ins:
            k = len(ins) + 1
            fc.append(f"[{v}]split={k}" + "".join(f"[vm{i}]" for i in range(k)))
            for i in range(k):
                fc.append(f"[vm{i}]trim=start={cuts_at[i]:.3f}:end={cuts_at[i + 1]:.3f},setpts=PTS-STARTPTS,"
                          f"fps={fps:.5f},format=yuv420p[vp{i}]")
            for j, (_at, ip, ii) in enumerate(ins):
                inputs += ["-i", str(ip.resolve())]
                ins[j] = (_at, ip, ii, n_in)
                fc.append(f"[{n_in}:v]scale={TW}:{TH}:force_original_aspect_ratio=decrease,pad={TW}:{TH}:(ow-iw)/2:"
                          f"(oh-ih)/2,setsar=1,fps={fps:.5f},format=yuv420p,setpts=PTS-STARTPTS[vi{j}]")
                n_in += 1
            final_dur = main_dur + sum(ii.duration for _a, _p, ii, _n in ins)

        # ---- 12. hiệu ứng mờ dần
        fade = spec.get("fade") or {}
        fin, fout = float(fade.get("in") or 0), float(fade.get("out") or 0)
        fl = []
        if fin > 0:
            fl.append(f"fade=t=in:st=0:d={fin}")
        if fout > 0:
            fl.append(f"fade=t=out:st={max(0, final_dur - fout):.3f}:d={fout}")
        if fl and not ins:
            fc.append(f"[{v}]{','.join(fl)}[vf]")
            v = "vf"
        if vpart:
            fc.append(f"[{v}]setpts=PTS-STARTPTS,format=yuv420p[vout]")
        elif not ins:
            fc.append(f"[{v}]format=yuv420p[vout]")

    # ================= ÂM THANH (cách trộn dùng chung với bộ trộn trực tiếp trong trình phát: trami/audiomix.py)
    amix_inputs: list[str] = []
    if do_audio:
        from .audiomix import audio_settings, gains as mix_gains, pitch_filter, use_stems

        dub = spec.get("dub") or {}
        use_dub = bool(dub.get("enabled") and dub.get("wav") and Path(dub["wav"]).exists())
        aset = audio_settings(spec)
        bgm = spec.get("bgm") or {}
        has_bgm = bool(bgm.get("path") and Path(bgm["path"]).exists())
        bg_ok = bool(dub.get("bg_wav") and Path(dub["bg_wav"]).exists())
        stems_on = use_stems(aset, bg_ok, use_dub)
        g = mix_gains(aset, stems_on, use_dub, has_bgm)
        pf = pitch_filter(float(aset.get("pitch") or 1.0))
        tempo = (',' + ','.join(_atempo_chain(speed))) if abs(speed - 1) > 1e-3 else ''
        counter = [0]
        span = seek1 - seek0  # độ dài đoạn video đầu vào sau khi seek

        def ext_audio(path: str, pre: str = "") -> str:
            """Thêm 1 file âm thanh ngoài (cùng mốc thời gian với video gốc), cắt giống video. Trả về nhãn."""
            nonlocal n_in
            inputs.extend(in_opts + ["-i", str(Path(path).resolve())])
            idx = n_in
            n_in += 1
            counter[0] += 1
            lab = f"ex{counter[0]}"
            pre_f = (pre + ",") if pre else ""
            if len(keep) == 1:
                fc.append(f"[{idx}:a]aresample=44100,{pre_f}asetpts=PTS-STARTPTS,apad=whole_dur={span:.3f},"
                          f"atrim=0:{span:.3f}[{lab}]")
            else:
                expr = "+".join(f"between(t,{a:.3f},{b - 0.001:.3f})" for a, b in rel_keep)
                fc.append(f"[{idx}:a]aresample=44100,{pre_f}apad=whole_dur={span:.3f},atrim=0:{span:.3f},"
                          f"aselect='{expr}',asetpts=N/SR/TB[{lab}]")
            return lab

        if stems_on:
            # nền = nhạc + hiệu ứng đã tách (không còn giọng Trung)
            if g["bg"] > 0.001:
                lab = ext_audio(dub["bg_wav"], mute_f.rstrip(","))
                fc.append(f"[{lab}]volume={g['bg']:.3f}{tempo}[bgx]")
                amix_inputs.append("[bgx]")
            # giọng gốc đã tách: nhỏ / tắt, riêng đoạn hát giữ to (nếu bật "giữ nguyên đoạn hát")
            voc = dub.get("vocals_wav")
            spans = dub.get("song_spans") or []
            if voc and Path(voc).exists() and (g["voice"] > 0.001 or (spans and g["voice_song"] > 0.001)):
                # mốc thời gian lúc này là của file gốc (trước khi cắt) vì -ss đã dời về 0 tại seek0
                cond = "+".join(f"between(t,{a - seek0:.3f},{b - seek0:.3f})" for a, b in spans) or "0"
                pre = f"volume='if({cond},{g['voice_song']:.3f},{g['voice']:.3f})':eval=frame" + (f",{pf}" if pf else "") \
                    + (f",{mute_f.rstrip(',')}" if mute_f else "")
                lab = ext_audio(voc, pre)
                fc.append(f"[{lab}]anull{tempo}[vcx]")
                amix_inputs.append("[vcx]")
        elif has_a and g["orig"] > 0.001:
            af = [f"volume={g['orig']:.3f}"] + _atempo_chain(speed)
            if pf:
                af.insert(0, pf)
            fc.append(f"[a0]aresample=44100,{','.join(af)}[ao]")
            amix_inputs.append("[ao]")
        duck = has_bgm and use_dub and aset.get("duck", True) and g["dub"] > 0.001 and g["bgm"] > 0.001
        if use_dub and g["dub"] > 0.001:
            lab = ext_audio(dub["wav"])
            split = ",asplit=2[dso][dsk]" if duck else "[dso]"
            fc.append(f"[{lab}]volume={g['dub']:.3f}{tempo}{split}")
            amix_inputs.append("[dso]")
        if has_a and "[ao]" not in amix_inputs:
            fc.append("[a0]anullsink")  # âm thanh gốc không dùng tới
        if has_bgm and g["bgm"] > 0.001:
            inputs += ["-stream_loop", "-1", "-i", str(Path(bgm["path"]).resolve())]
            bi = n_in
            n_in += 1
            fc.append(f"[{bi}:a]aresample=44100,aformat=channel_layouts=stereo,volume={g['bgm']:.3f},"
                      f"atrim=0:{final_dur:.3f}" + ("[bgr]" if duck else "[bgo]"))
            if duck:
                # giọng lồng làm "chìa khoá": có lời -> nhạc giảm ~10–16 dB trong 20 ms, hết lời -> lên lại trong 0,4 s
                fc.append("[dsk]aformat=channel_layouts=stereo[dsk2];"
                          "[bgr][dsk2]sidechaincompress=threshold=0.02:ratio=8:attack=20:release=400[bgo]")
            amix_inputs.append("[bgo]")
        if ins and not amix_inputs:   # video gốc không có tiếng nhưng đoạn chèn có -> nền im lặng cho phần video gốc
            fc.append(f"anullsrc=r=44100:cl=stereo,atrim=0:{main_dur:.3f}[silm]")
            amix_inputs.append("[silm]")
        if amix_inputs:
            if len(amix_inputs) == 1:
                fc.append(f"{amix_inputs[0]}anull[amx]")
            else:
                fc.append("".join(amix_inputs) + f"amix=inputs={len(amix_inputs)}:duration=first:"
                          f"dropout_transition=0:normalize=0[amx]")
            if ins:
                k = len(ins) + 1
                fc.append(f"[amx]aformat=sample_rates=44100:channel_layouts=stereo,apad=whole_dur={main_dur:.3f},"
                          f"atrim=0:{main_dur:.3f},asplit={k}" + "".join(f"[am{i}]" for i in range(k)))
                for i in range(k):
                    fc.append(f"[am{i}]atrim=start={cuts_at[i]:.3f}:end={cuts_at[i + 1]:.3f},asetpts=PTS-STARTPTS[ap{i}]")
                for j, (_at, _ip, ii, idx) in enumerate(ins):
                    if ii.has_audio:
                        fc.append(f"[{idx}:a]aresample=44100,aformat=sample_rates=44100:channel_layouts=stereo,"
                                  f"apad=whole_dur={ii.duration:.3f},atrim=0:{ii.duration:.3f},asetpts=PTS-STARTPTS[ai{j}]")
                    else:
                        fc.append(f"anullsrc=r=44100:cl=stereo,atrim=0:{ii.duration:.3f}[ai{j}]")
                order = []
                for i in range(k):
                    order.append(f"[vp{i}][ap{i}]")
                    if i < len(ins):
                        order.append(f"[vi{i}][ai{i}]")
                fc.append("".join(order) + f"concat=n={2 * k - 1}:v=1:a=1[vcat][amx2]")
                # mờ dần áp cho cả video sau khi ghép
                vf = []
                if fin > 0:
                    vf.append(f"fade=t=in:st=0:d={fin}")
                if fout > 0:
                    vf.append(f"fade=t=out:st={max(0, final_dur - fout):.3f}:d={fout}")
                fc.append("[vcat]" + (",".join(vf) + "," if vf else "") + "format=yuv420p[vout2]")
                amx_lab = "amx2"
            else:
                amx_lab = "amx"
            afl = ["alimiter=limit=0.95"]
            if fin > 0:
                afl.append(f"afade=t=in:st=0:d={fin}")
            if fout > 0:
                afl.append(f"afade=t=out:st={max(0, final_dur - fout):.3f}:d={fout}")
            fc.append(f"[{amx_lab}]{','.join(afl)}[aout]")

    if vpart:      # 1 đoạn hình (không tiếng), đúng số khung
        return inputs + ["-filter_complex", ";".join(fc), "-map", "[vout]", "-an"] + \
            ff.video_encode_args(settings, spec.get("crf")) + ["-frames:v", str(vpart["n"]), str(out.resolve())]
    if _part is not None:   # chỉ âm thanh (cả phim, trộn 1 lần)
        if not amix_inputs:
            return None
        return inputs + ["-filter_complex", ";".join(fc), "-map", "[aout]"] + ff.audio_encode_args(settings) + \
            ["-t", f"{final_dur:.3f}", str(out.resolve())]
    args = inputs + ["-filter_complex", ";".join(fc), "-map", "[vout2]" if ins else "[vout]"]
    if amix_inputs:
        args += ["-map", "[aout]"] + ff.audio_encode_args(settings)
    args += ff.video_encode_args(settings, spec.get("crf"))
    args += ["-t", f"{final_dur:.3f}", "-movflags", "+faststart", str(out.resolve())]
    (work / "last_ffmpeg_cmd.txt").write_text(" ".join(args), "utf-8")

    def p3(x, m):
        if progress:
            progress(base_p + span_p * x, m)

    ff.run(args, final_dur, p3, cancel, "Đang xuất video", cwd=work)
    return out


# ------------------------------------------------------------------ các thao tác nhanh

def split_parts(src: str | Path, out_dir: str | Path, part_minutes: float, settings: dict,
                progress: ProgressCB | None = None, cancel=None) -> list[Path]:
    """Chia video dài thành nhiều phần (mỗi phần N phút) - encode lại để cắt chính xác."""
    info = ff.probe(src)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    step = max(10.0, part_minutes * 60)
    outs = []
    n = int((info.duration + step - 1) // step)
    for i in range(n):
        a, b = i * step, min(info.duration, (i + 1) * step)
        o = out_dir / f"{Path(src).stem}_phan{i + 1:02d}.mp4"
        ff.run(["-ss", f"{a:.3f}", "-i", str(src), "-t", f"{b - a:.3f}"] + ff.video_encode_args(settings)
               + ff.audio_encode_args(settings) + [str(o)], b - a,
               (lambda x, m, i=i: progress((i + x) / n, f"Đang chia phần {i + 1}/{n}")) if progress else None, cancel)
        outs.append(o)
    return outs


def concat_videos(files: list[str | Path], out: str | Path, settings: dict, width: int = 0, height: int = 0,
                  progress: ProgressCB | None = None, cancel=None) -> Path:
    """Ghép nhiều video (tự đưa về cùng độ phân giải)."""
    infos = [ff.probe(f) for f in files]
    W = width or _even(infos[0].width)
    H = height or _even(infos[0].height)
    args: list[str] = []
    fc = []
    for i, f in enumerate(files):
        args += ["-i", str(f)]
        fc.append(f"[{i}:v]scale={W}:{H}:force_original_aspect_ratio=decrease,pad={W}:{H}:(ow-iw)/2:(oh-ih)/2,"
                  f"setsar=1,fps=30,format=yuv420p[v{i}]")
        if infos[i].has_audio:
            fc.append(f"[{i}:a]aresample=44100,aformat=channel_layouts=stereo[a{i}]")
        else:
            fc.append(f"anullsrc=r=44100:cl=stereo,atrim=0:{infos[i].duration:.3f}[a{i}]")
    fc.append("".join(f"[v{i}][a{i}]" for i in range(len(files))) + f"concat=n={len(files)}:v=1:a=1[v][a]")
    args += ["-filter_complex", ";".join(fc), "-map", "[v]", "-map", "[a]"] + ff.video_encode_args(settings) + \
        ff.audio_encode_args(settings) + ["-movflags", "+faststart", str(out)]
    ff.run(args, sum(i.duration for i in infos), progress, cancel, "Đang ghép video")
    return Path(out)


def extract_audio_file(src: str | Path, out: str | Path, progress=None, cancel=None) -> Path:
    info = ff.probe(src)
    ext = Path(out).suffix.lower()
    codec = ["-c:a", "libmp3lame", "-b:a", "192k"] if ext == ".mp3" else ["-c:a", "pcm_s16le"]
    ff.run(["-i", str(src), "-vn"] + ff.af_args(ff.audio_sync(src)) + codec + [str(out)], info.duration, progress,
           cancel, "Đang tách âm thanh")
    return Path(out)
