"""Bộ dựng video: cắt/ghép, xoá & che phụ đề cũ, đè phụ đề mới, lồng tiếng, crop, lật, tốc độ,
đổi tỉ lệ khung hình, logo, chữ, nhạc nền... tất cả trong MỘT lần encode FFmpeg để giữ chất lượng.
"""
from __future__ import annotations

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


def build_and_render(src: str | Path, out: str | Path, spec: dict, settings: dict, work_dir: str | Path,
                     segments: list[Segment] | None = None, progress: ProgressCB | None = None,
                     cancel: Callable[[], bool] | None = None) -> Path:
    src, out, work = Path(src), Path(out), Path(work_dir)
    work.mkdir(parents=True, exist_ok=True)
    info = ff.probe(src)
    if not info.has_video:
        raise ff.FFmpegError("File đầu vào không có hình ảnh.")
    W, H, dur = info.width, info.height, info.duration

    # Tiền xử lý: xoá phụ đề bằng AI inpaint (tạo video trung gian)
    hs = spec.get("hardsub") or {}
    if hs.get("mode") == "inpaint" and hs.get("regions"):
        from .inpaint import inpaint_video

        inp_out = work / "inpainted.mp4"
        def p2(x, m):
            if progress:
                progress(x * 0.6, m)
        st_inp = dict(settings)
        if hs.get("backend"):
            st_inp["inpaint_backend"] = hs["backend"]
        inpaint_video(src, inp_out, hs["regions"], st_inp, boxes_file=hs.get("boxes_file"),
                      progress=p2, cancel=cancel, strength=hs.get("strength", "normal"))
        src = inp_out
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
    if len(keep) == 1:
        fc.append("[0:v]setpts=PTS-STARTPTS[v0]")
        if has_a:
            fc.append("[0:a]asetpts=PTS-STARTPTS[a0]")
    else:
        expr = "+".join(f"between(t,{a:.3f},{b - 0.001:.3f})" for a, b in rel_keep)
        fc.append(f"[0:v]fps={fps:.5f},select='{expr}',setpts=N/FRAME_RATE/TB[v0]")
        if has_a:
            fc.append(f"[0:a]aresample=44100,aselect='{expr}',asetpts=N/SR/TB[a0]")
    v = "v0"

    # ---- 2. che/xoá phụ đề cũ (toạ độ khung gốc)
    mode = hs.get("mode", "none")
    if mode in ("blur", "box", "delogo") and hs.get("regions"):
        for k, r in enumerate(hs["regions"]):
            x, y = int(float(r["x"]) * W), int(float(r["y"]) * H)
            w, h = _even(float(r["w"]) * W), _even(float(r["h"]) * H)
            w, h = min(w, W - x), min(h, H - y)
            if mode == "box":
                col = _hex(hs.get("box_color", "#000000"))
                op = float(hs.get("box_opacity", 1.0))
                fc.append(f"[{v}]drawbox=x={x}:y={y}:w={w}:h={h}:color={col}@{op:.2f}:t=fill[hb{k}]")
            elif mode == "delogo":
                x1, y1 = max(1, x), max(1, y)
                w1, h1 = min(w, W - x1 - 1), min(h, H - y1 - 1)
                fc.append(f"[{v}]delogo=x={x1}:y={y1}:w={w1}:h={h1}[hb{k}]")
            else:
                st = int(hs.get("blur_strength", 20))
                lr = max(1, min(st, min(w, h) // 2 - 1))
                cr = max(1, min(st, min(w, h) // 4 - 1))  # kênh màu có độ phân giải 1/2
                fc.append(f"[{v}]split[hm{k}][hc{k}];[hc{k}]crop={w}:{h}:{x}:{y},"
                          f"boxblur=luma_radius={lr}:luma_power=3:chroma_radius={cr}:chroma_power=3[hbl{k}];"
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
    if not fonts_local.exists():
        fonts_local.mkdir(parents=True, exist_ok=True)
        for f in FONTS_DIR.glob("*.[ot]tf"):
            shutil.copy2(f, fonts_local / f.name)

    # ---- 10. chữ chạy / chữ tĩnh (vẽ bằng ASS -> không cần drawtext, chạy với mọi bản FFmpeg có libass)
    tx = spec.get("text") or {}
    if (tx.get("content") or "").strip():
        overlay_text_ass(work / "overlay_text.ass", tx, TW, TH, final_dur)
        fc.append(f"[{v}]subtitles=overlay_text.ass:fontsdir=fonts[vt]")
        v = "vt"

    # ---- 11. phụ đề mới
    sub = spec.get("subtitles") or {}
    if sub.get("burn") and segments:
        segs = [Segment(s.start, s.end, s.text, s.trans) for s in segments]
        if not full:
            segs = remap_after_cut(segs, keep)
        segs = scale_time(segs, speed)
        style = dict(settings.get("sub_style") or {})
        style.update(sub.get("style") or {})
        which = sub.get("which", "trans")
        save_subtitles(segs, work / "burn.ass", which=which, style=style, width=TW, height=TH)
        fc.append(f"[{v}]subtitles=burn.ass:fontsdir=fonts[vs]")
        v = "vs"

    # ---- 12. hiệu ứng mờ dần
    fade = spec.get("fade") or {}
    fin, fout = float(fade.get("in") or 0), float(fade.get("out") or 0)
    fl = []
    if fin > 0:
        fl.append(f"fade=t=in:st=0:d={fin}")
    if fout > 0:
        fl.append(f"fade=t=out:st={max(0, final_dur - fout):.3f}:d={fout}")
    if fl:
        fc.append(f"[{v}]{','.join(fl)}[vf]")
        v = "vf"
    fc.append(f"[{v}]format=yuv420p[vout]")

    # ================= ÂM THANH
    amix_inputs: list[str] = []
    vol = float(spec.get("volume", 1.0))
    dub = spec.get("dub") or {}
    use_dub = bool(dub.get("enabled") and dub.get("wav") and Path(dub["wav"]).exists())
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

    bg_ok = use_dub and dub.get("bg_wav") and Path(dub["bg_wav"]).exists()
    if bg_ok and not spec.get("mute_original"):
        # nền = nhạc + hiệu ứng đã tách (không còn giọng Trung)
        lab = ext_audio(dub["bg_wav"])
        fc.append(f"[{lab}]volume={vol * float(dub.get('bg_volume', 1.0)):.3f}{tempo}[bgx]")
        amix_inputs.append("[bgx]")
        # giọng gốc: giữ nguyên ở các đoạn đang hát, còn lại để nhỏ/tắt
        voc = dub.get("vocals_wav")
        spans = dub.get("song_spans") or []
        base_v = float(dub.get("vocals_volume", 0.0))
        if voc and Path(voc).exists() and (spans or base_v > 0.001):
            # mốc thời gian lúc này là của file gốc (trước khi cắt) vì -ss đã dời về 0 tại seek0
            cond = "+".join(f"between(t,{a - seek0:.3f},{b - seek0:.3f})" for a, b in spans) or "0"
            pre = f"volume='if({cond},{vol:.3f},{base_v * vol:.3f})':eval=frame"
            lab = ext_audio(voc, pre)
            fc.append(f"[{lab}]anull{tempo}[vcx]")
            amix_inputs.append("[vcx]")
    else:
        orig_vol = vol * (float(dub.get("original_volume", 0.15)) if use_dub else 1.0)
        if spec.get("mute_original"):
            orig_vol = 0.0
        if has_a and orig_vol > 0.001:
            af = [f"volume={orig_vol:.3f}"] + _atempo_chain(speed)
            if spec.get("pitch_shift"):
                ps = float(spec["pitch_shift"])
                af.insert(0, f"asetrate=44100*{ps:.4f},aresample=44100,atempo={1 / ps:.4f}")
            fc.append(f"[a0]aresample=44100,{','.join(af)}[ao]")
            amix_inputs.append("[ao]")
    if use_dub:
        lab = ext_audio(dub["wav"])
        fc.append(f"[{lab}]volume={float(dub.get('dub_volume', 1.0)):.3f}{tempo}[dso]")
        amix_inputs.append("[dso]")
    if has_a and "[ao]" not in amix_inputs:
        fc.append("[a0]anullsink")  # âm thanh gốc không dùng tới
    bgm = spec.get("bgm") or {}
    if bgm.get("path") and Path(bgm["path"]).exists():
        inputs += ["-stream_loop", "-1", "-i", str(Path(bgm["path"]).resolve())]
        bi = n_in
        n_in += 1
        fc.append(f"[{bi}:a]aresample=44100,volume={float(bgm.get('volume', 0.2)):.3f},"
                  f"atrim=0:{final_dur:.3f}[bgo]")
        amix_inputs.append("[bgo]")
    if amix_inputs:
        if len(amix_inputs) == 1:
            fc.append(f"{amix_inputs[0]}anull[amx]")
        else:
            fc.append("".join(amix_inputs) + f"amix=inputs={len(amix_inputs)}:duration=first:"
                      f"dropout_transition=0:normalize=0[amx]")
        afl = ["alimiter=limit=0.95"]
        if fin > 0:
            afl.append(f"afade=t=in:st=0:d={fin}")
        if fout > 0:
            afl.append(f"afade=t=out:st={max(0, final_dur - fout):.3f}:d={fout}")
        fc.append(f"[amx]{','.join(afl)}[aout]")

    args = inputs + ["-filter_complex", ";".join(fc), "-map", "[vout]"]
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
