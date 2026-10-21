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

ASPECTS = {"16:9": (16, 9), "9:16": (9, 16), "1:1": (1, 1), "4:3": (4, 3), "3:4": (3, 4), "4:5": (4, 5)}
POSITIONS = {
    "top-left": ("{m}", "{m}"), "top-right": ("W-w-{m}", "{m}"), "bottom-left": ("{m}", "H-h-{m}"),
    "bottom-right": ("W-w-{m}", "H-h-{m}"), "center": ("(W-w)/2", "(H-h)/2"),
    "top": ("(W-w)/2", "{m}"), "bottom": ("(W-w)/2", "H-h-{m}"),
}


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
    if aspect in ASPECTS:
        aw, ah = ASPECTS[aspect]
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


def build_and_render(src: str | Path, out: str | Path, spec: dict, settings: dict, work_dir: str | Path,
                     segments: list[Segment] | None = None, progress: ProgressCB | None = None,
                     cancel: Callable[[], bool] | None = None) -> Path:
    src, out, work = Path(src), Path(out), Path(work_dir)
    work.mkdir(parents=True, exist_ok=True)
    info = ff.probe(src)
    if not info.has_video:
        raise ff.FFmpegError("File đầu vào không có hình ảnh.")
    W, H, dur = info.width, info.height, info.duration

    # Tiền xử lý: xoá phụ đề bằng AI inpaint (tạo video trung gian; đã chạy với đúng vùng/tuỳ chọn -> dùng lại)
    hs = spec.get("hardsub") or {}
    if needs_clean(hs):
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
    if len(keep) == 1:
        fc.append("[0:v]setpts=PTS-STARTPTS[v0]")
        if has_a:
            fc.append(f"[0:a]{mute_f}asetpts=PTS-STARTPTS[a0]")
    else:
        expr = "+".join(f"between(t,{a:.3f},{b - 0.001:.3f})" for a, b in rel_keep)
        fc.append(f"[0:v]fps={fps:.5f},select='{expr}',setpts=N/FRAME_RATE/TB[v0]")
        if has_a:
            fc.append(f"[0:a]aresample=44100,{mute_f}aselect='{expr}',asetpts=N/SR/TB[a0]")
    v = "v0"

    # ---- 2. che/xoá phụ đề cũ (toạ độ khung gốc)
    mode = hs.get("mode", "none")
    if mode in ("blur", "box", "delogo", "patch") and hs.get("regions"):
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
    if spec.get("fps"):
        chain.append(f"fps={float(spec['fps'])}")
    if chain:
        fc.append(f"[{v}]{','.join(chain)}[vc]")
        v = "vc"

    # ---- 8. tỉ lệ khung hình / độ phân giải
    aspect = spec.get("aspect", "original")
    res = spec.get("resolution", "original")
    TW, TH = target_size(cw, ch, aspect, res)
    if (TW, TH) != (cw, ch):
        amode = spec.get("aspect_mode", "blur")
        if aspect in ASPECTS and amode == "crop":
            fc.append(f"[{v}]scale={TW}:{TH}:force_original_aspect_ratio=increase,crop={TW}:{TH},setsar=1[vr]")
        elif aspect in ASPECTS and amode == "pad":
            fc.append(f"[{v}]scale={TW}:{TH}:force_original_aspect_ratio=decrease,"
                      f"pad={TW}:{TH}:(ow-iw)/2:(oh-ih)/2:color=black,setsar=1[vr]")
        elif aspect in ASPECTS:
            fc.append(f"[{v}]split[bg0][fg0];[bg0]scale={TW}:{TH}:force_original_aspect_ratio=increase,"
                      f"crop={TW}:{TH},boxblur=luma_radius=min(h\\,w)/20:luma_power=2,eq=brightness=-0.08[bg1];"
                      f"[fg0]scale={TW}:{TH}:force_original_aspect_ratio=decrease[fg1];"
                      f"[bg1][fg1]overlay=(W-w)/2:(H-h)/2,setsar=1[vr]")
        else:
            fc.append(f"[{v}]scale={TW}:{TH}:flags=lanczos,setsar=1[vr]")
        v = "vr"

    # ---- 9. logo / watermark ảnh
    wm = spec.get("watermark") or {}
    if wm.get("path") and Path(wm["path"]).exists():
        inputs += ["-loop", "1", "-i", str(Path(wm["path"]).resolve())]
        wi = n_in
        n_in += 1
        sc = float(wm.get("scale", 0.15))
        op = float(wm.get("opacity", 0.85))
        m = int(min(TW, TH) * 0.03)
        px, py = POSITIONS.get(wm.get("position", "top-right"), POSITIONS["top-right"])
        fc.append(f"[{wi}:v]scale={_even(TW * sc)}:-1,format=rgba,colorchannelmixer=aa={op:.2f}[wm]")
        fc.append(f"[{v}][wm]overlay={px.format(m=m)}:{py.format(m=m)}:shortest=1[vw]")
        v = "vw"

    # thư mục font cục bộ để libass/drawtext dùng đường dẫn tương đối (tránh lỗi escape trên Windows)
    fonts_local = work / "fonts"
    fonts_local.mkdir(parents=True, exist_ok=True)
    for f in FONTS_DIR.glob("*.[ot]tf"):          # chép cả font mới thêm vào bản cập nhật (vd. Bangers)
        dst = fonts_local / f.name
        if not dst.exists() or dst.stat().st_size != f.stat().st_size:
            shutil.copy2(f, dst)

    # ---- 10. chữ chạy / chữ tĩnh (vẽ bằng ASS -> không cần drawtext, chạy với mọi bản FFmpeg có libass)
    tx = spec.get("text") or {}
    if (tx.get("content") or "").strip():
        overlay_text_ass(work / "overlay_text.ass", tx, TW, TH, final_dur)
        fc.append(f"[{v}]subtitles=overlay_text.ass:fontsdir=fonts[vt]")
        v = "vt"

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
    if not ins:
        fc.append(f"[{v}]format=yuv420p[vout]")

    # ================= ÂM THANH (cách trộn dùng chung với bộ trộn trực tiếp trong trình phát: trami/audiomix.py)
    from .audiomix import audio_settings, gains as mix_gains, pitch_filter, use_stems

    amix_inputs: list[str] = []
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


def overlay_text_ass(path: Path, tx: dict, TW: int, TH: int, duration: float) -> Path:
    from .subtitle import _ass_escape, _hex_to_ass, fmt_ass_time

    size = int(tx.get("size") or max(18, TH * 0.04))
    m = int(min(TW, TH) * 0.03)
    col = _hex_to_ass(tx.get("color", "#FFFFFF"))
    pos = tx.get("position", "top-left")
    an = {"top-left": 7, "top": 8, "top-right": 9, "bottom-left": 1, "bottom": 2, "bottom-right": 3, "center": 5}
    text = _ass_escape(" ".join(tx["content"].split()))
    if pos == "scroll":
        # chạy từ phải sang trái, lặp lại suốt video
        est_w = int(len(tx["content"]) * size * 0.6)
        span = TW + est_w
        speed = max(60, TW / 8)
        one = span / speed
        events = []
        t = 0.0
        while t < max(duration, 0.1):
            events.append(f"Dialogue: 0,{fmt_ass_time(t)},{fmt_ass_time(t + one)},T,,0,0,0,,"
                          f"{{\\an7\\move({TW},{m},{-est_w},{m})}}{text}")
            t += one
    else:
        a = an.get(pos, 7)
        x = m if a in (7, 1) else TW - m if a in (9, 3) else TW // 2
        y = m if a in (7, 8, 9) else TH - m if a in (1, 2, 3) else TH // 2
        events = [f"Dialogue: 0,{fmt_ass_time(0)},{fmt_ass_time(duration + 1)},T,,0,0,0,,"
                  f"{{\\an{a}\\pos({x},{y})}}{text}"]
    head = (f"[Script Info]\nScriptType: v4.00+\nPlayResX: {TW}\nPlayResY: {TH}\nScaledBorderAndShadow: yes\n\n"
            "[V4+ Styles]\nFormat: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, "
            "Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
            "Alignment, MarginL, MarginR, MarginV, Encoding\n"
            f"Style: T,Be Vietnam Pro,{size},{col},{col},&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,"
            f"{max(1, size / 16):.1f},1,7,0,0,0,1\n\n[Events]\n"
            "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n")
    path.write_text(head + "\n".join(events) + "\n", "utf-8")
    return path


# ------------------------------------------------------------------ các thao tác nhanh

def quick_cut(src: str | Path, out: str | Path, start: float, end: float) -> Path:
    """Cắt nhanh không encode lại (có thể lệch vài khung hình ở keyframe)."""
    args = ["-ss", f"{start:.3f}", "-to", f"{end:.3f}", "-i", str(src), "-c", "copy", "-avoid_negative_ts", "1",
            str(out)]
    ff.run(args, end - start)
    return Path(out)


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
    ff.run(["-i", str(src), "-vn"] + codec + [str(out)], info.duration, progress, cancel, "Đang tách âm thanh")
    return Path(out)


def remove_vocals(src_audio: str | Path, out: str | Path, progress=None, cancel=None) -> Path:
    """Giảm giọng nói, giữ nhạc nền (phương pháp khử kênh giữa - hiệu quả với nhạc stereo)."""
    info = ff.probe(src_audio)
    ff.run(["-i", str(src_audio), "-af", "pan=stereo|c0=c0-c1|c1=c1-c0,highpass=f=120,volume=1.4",
            "-c:a", "pcm_s16le", str(out)], info.duration, progress, cancel, "Đang tách nhạc nền")
    return Path(out)
