"""Các bước xử lý (dùng chung cho giao diện web và dòng lệnh)."""
from __future__ import annotations

import time
from pathlib import Path
from typing import Callable

from . import ffmpeg as ff
from .config import load_settings
from .project import Project, safe_name
from .subtitle import normalize, save_subtitles

Prog = Callable[[float, str], None]


def _sub(progress: Prog | None, a: float, b: float) -> Prog:
    def f(x: float, m: str = ""):
        if progress:
            progress(a + (b - a) * max(0.0, min(1.0, x)), m)
    return f


# ------------------------------------------------------------------ nguồn video

def download_video(url: str, dest_dir: Path, progress: Prog | None = None, cancel=None) -> Path:
    """Tải video từ Douyin, Bilibili, YouTube, TikTok, Weibo, Xigua... bằng yt-dlp."""
    import yt_dlp

    dest_dir.mkdir(parents=True, exist_ok=True)
    result: dict = {}

    def hook(d):
        if cancel and cancel():
            raise ff.Cancelled()
        if d.get("status") == "downloading" and progress:
            tot = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            if tot:
                progress(d.get("downloaded_bytes", 0) / tot * 0.95, f"Đang tải video {d.get('_percent_str', '').strip()}")
        if d.get("status") == "finished":
            result["file"] = d.get("filename")

    opts = {
        "outtmpl": str(dest_dir / "%(title).80s [%(id)s].%(ext)s"),
        "format": "bv*[ext=mp4]+ba[ext=m4a]/bv*+ba/b",
        "merge_output_format": "mp4",
        "noplaylist": True,
        "progress_hooks": [hook],
        "quiet": True,
        "no_warnings": True,
        "ffmpeg_location": ff.ffmpeg_bin(),
        "restrictfilenames": False,
        "windowsfilenames": True,
        "http_headers": {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                                       "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"},
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)
        path = Path(ydl.prepare_filename(info))
    if not path.exists():
        cands = sorted(dest_dir.glob("*.mp4"), key=lambda p: p.stat().st_mtime, reverse=True)
        if cands:
            path = cands[0]
    if not path.exists():
        raise RuntimeError("Tải video thất bại.")
    if progress:
        progress(1.0, "Đã tải xong video")
    return path


def attach_source(p: Project, path: Path):
    path = Path(path).expanduser().resolve()
    info = ff.probe(path)
    if not info.has_video and not info.has_audio:
        raise ValueError("File không phải video/âm thanh hợp lệ.")
    p.data["source"] = str(path)
    p.data["info"] = info.to_dict()
    # trình duyệt chỉ phát được MP4/WebM với H.264/VP9/AV1 -> các loại khác cần bản xem trước
    p.data["needs_preview"] = bool(info.has_video and (
        path.suffix.lower() not in (".mp4", ".m4v", ".webm", ".mov") or info.vcodec not in ("h264", "vp8", "vp9", "av1")))
    old_prev = p.work / "preview.mp4"
    if old_prev.exists():
        old_prev.unlink()
    try:
        ff.snapshot(path, min(3.0, info.duration / 3 if info.duration else 0), p.dir / "thumb.jpg", 480)
    except Exception:
        pass
    p.save()


def make_preview(p: Project, progress: Prog | None = None, cancel=None) -> Path:
    """Tạo bản xem trước nhẹ (H.264 ≤720p) để phát được trong trình duyệt. Không ảnh hưởng chất lượng xuất."""
    out = p.work / "preview.mp4"
    tmp = p.work / "preview_tmp.mp4"
    info = p.data.get("info", {})
    ff.run(["-i", str(p.source), "-vf", "scale=-2:'min(720,ih)'", "-c:v", "libx264", "-preset", "veryfast",
            "-crf", "27", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", str(tmp)],
           info.get("duration", 0), progress, cancel, "Đang tạo bản xem trước")
    tmp.replace(out)
    return out


# ------------------------------------------------------------------ nhận dạng

def separate_audio(p: Project, settings: dict | None = None, progress: Prog | None = None, cancel=None,
                   model: str | None = None) -> tuple[Path, Path]:
    """Tách giọng nói / nhạc nền của video gốc, lưu vào work/stems."""
    from .separate import separate

    settings = settings or load_settings()
    v, i = separate(p.source, p.work / "stems", model or settings.get("sep_model"), progress, cancel)
    p.data["stems"] = {"vocals": str(v), "instrumental": str(i)}
    p.save()
    return v, i


def stems(p: Project) -> dict:
    st = p.data.get("stems") or {}
    if st.get("vocals") and Path(st["vocals"]).exists() and Path(st.get("instrumental", "")).exists():
        return st
    return {}


def diarize(p: Project, settings: dict | None = None, progress: Prog | None = None, cancel=None) -> int:
    from .diarize import assign_speakers

    settings = settings or load_settings()
    segs = p.segments
    if not segs:
        raise ValueError("Chưa có phụ đề để phân biệt người nói.")
    audio = stems(p).get("vocals") or str(p.source)
    for s in segs:
        s.speaker = ""
    assign_speakers.splits = 0
    n = assign_speakers(audio, segs, float(settings.get("diarize_threshold", 0.62)),
                        int(settings.get("diarize_speakers", 0)), progress, cancel,
                        split_changes=bool(settings.get("split_speaker_changes", True)))
    p.data["speaker_splits"] = int(getattr(assign_speakers, "splits", 0))
    p.segments = segs
    p.save()
    return n


def align_segments(segs, wav: Path, language: str, settings: dict, progress: Prog | None = None, cancel=None):
    """Tầng 1 khớp thoại: căn mốc từng chữ (fa-zh cho tiếng Trung), không chạy được thì dùng độ to giọng."""
    from . import align
    from .asr_funasr import available as fa_available, load_wav16k
    from .progress_hook import tqdm_progress

    try:
        audio = load_wav16k(wav)
        use_fa = language in ("zh", "auto", "yue") and fa_available() and bool(settings.get("align_fa", True))
        with tqdm_progress(progress, 0.0, 1.0, "Đang căn thời gian từng chữ", cancel,
                           download="Đang tải mô hình căn thời gian (fa-zh)"):
            return align.refine(segs, audio, use_fa=use_fa, progress=progress, cancel=cancel)
    except ff.Cancelled:
        raise
    except Exception as e:  # noqa: BLE001 – không để bước phụ làm hỏng cả quy trình
        if progress:
            progress(1.0, f"Bỏ qua căn thời gian ({str(e)[:80]})")
        return segs


def realign(p: Project, settings: dict | None = None, progress: Prog | None = None, cancel=None) -> int:
    """Căn lại thời gian cho phụ đề đã có (dự án cũ). Trả về số câu sau khi căn. Câu bị tách mất bản dịch."""
    settings = settings or load_settings()
    segs = p.segments
    if not segs:
        raise ValueError("Chưa có phụ đề")
    st = stems(p)
    src = st.get("vocals") or str(p.source)
    wav = p.work / ("asr16k_vocals.wav" if st else "audio16k.wav")
    if not wav.exists() or wav.stat().st_size < 1000:
        ff.run(["-i", str(src), "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(wav)],
               p.data.get("info", {}).get("duration", 0), _sub(progress, 0, 0.1), cancel, "Đang chuẩn bị âm thanh")
    segs = align_segments(segs, wav, p.data.get("language", "zh"), settings, _sub(progress, 0.1, 1.0), cancel)
    p.segments = segs
    p.save()
    return len(segs)


def recognize(p: Project, method: str = "asr", language: str = "zh", region: dict | None = None,
              ocr_fps: float = 4.0, settings: dict | None = None, progress: Prog | None = None, cancel=None,
              denoise: bool = False, engine: str | None = None) -> int:
    """Lấy phụ đề gốc.  method: asr (giọng nói) | ocr (chữ trên video).
    engine (asr): sensevoice | paraformer | whisper."""
    settings = settings or load_settings()
    src = p.source
    engine = engine or settings.get("asr_engine", "sensevoice")
    want_spk = bool(settings.get("diarize", True))
    if method == "ocr":
        from .ocr import detect_subtitle_region, extract_hardsubs

        if not region:
            if progress:
                progress(0.0, "Đang tự dò vùng phụ đề trên video…")
            region = detect_subtitle_region(src)
        boxes = p.work / "ocr_boxes.json"
        segs = extract_hardsubs(src, region, fps=ocr_fps, progress=_sub(progress, 0.02, 0.85), cancel=cancel,
                                boxes_out=boxes)
        p.data["ocr_region"] = region
        p.data["ocr_boxes"] = str(boxes)
        # gợi ý luôn vùng xoá phụ đề
        ed = p.edit
        if not ed["hardsub"].get("regions"):
            pad = 0.01
            ed["hardsub"]["regions"] = [{"x": max(0, region["x"] - pad), "y": max(0, region["y"] - pad),
                                         "w": min(1, region["w"] + 2 * pad), "h": min(1, region["h"] + 2 * pad)}]
            p.data["edit"] = ed
        spk_from_asr = False
    else:
        from . import asr_funasr, separate as sepmod

        info_dur = p.data.get("info", {}).get("duration", 0)
        # 1) tách giọng trước để nhận dạng chính xác hơn khi có nhạc nền
        audio_in: Path = Path(src)
        if settings.get("sep_before_asr", True) and sepmod.available():
            try:
                v, _ = separate_audio(p, settings, _sub(progress, 0.0, 0.3), cancel)
                audio_in = v
            except Exception as e:  # không chặn cả quy trình nếu tách giọng lỗi
                if isinstance(e, ff.Cancelled):
                    raise
                if progress:
                    progress(0.3, f"Bỏ qua tách giọng ({str(e)[:80]})")
        # 2) chuẩn hoá về wav 16k mono
        wav = p.work / ("asr16k_vocals.wav" if audio_in != Path(src) else "audio16k.wav")
        if not wav.exists() or wav.stat().st_size < 1000 or denoise:
            args = ["-i", str(audio_in), "-vn", "-ac", "1", "-ar", "16000"]
            if denoise:
                args += ["-af", "highpass=f=80,lowpass=f=8000,afftdn=nf=-25,dynaudnorm=f=200"]
            args += ["-c:a", "pcm_s16le", str(wav)]
            ff.run(args, info_dur, _sub(progress, 0.3, 0.33), cancel, "Đang chuẩn bị âm thanh")
        asr_end = 0.78 if want_spk else 0.92
        pr = _sub(progress, 0.33, asr_end)
        if engine in ("sensevoice", "paraformer") and not asr_funasr.available():
            if progress:
                progress(0.33, "Chưa cài FunASR -> dùng Whisper")
            engine = "whisper"
        if engine == "sensevoice" and language not in ("zh", "yue", "auto", "en", "ja", "ko"):
            engine = "whisper"
        spk_from_asr = False
        segs = None
        if engine in ("sensevoice", "paraformer"):
            try:
                if engine == "sensevoice":
                    segs = asr_funasr.transcribe_sensevoice(wav, settings, language, pr, cancel)
                else:
                    segs = asr_funasr.transcribe_paraformer(wav, settings, pr, cancel, with_speakers=want_spk)
                    spk_from_asr = want_spk and any(s.speaker for s in segs)
            except (ImportError, OSError) as e:
                # thư viện hỏng/lệch phiên bản (VD torchaudio khác torch) -> dùng Whisper, không dừng cả quy trình
                p.data["asr_warning"] = (f"{engine} không chạy được ({str(e)[:160]}) – đã dùng Whisper thay thế. "
                                         "Cài lại PyTorch theo README mục 1 để dùng lại SenseVoice.")
                if progress:
                    progress(0.33, f"{engine} lỗi thư viện → chuyển sang Whisper")
                engine = "whisper"
        if segs is None:
            from .asr import transcribe

            segs = transcribe(wav, settings, language, pr, cancel, duration=info_dur)
            from .asr_funasr import mark_songs

            mark_songs(segs)
        p.data["asr_engine_used"] = engine
        p.data["language"] = language
        # 2b) căn thời gian từng chữ: mốc câu sát lời thật, cắt câu ở chỗ nghỉ
        if settings.get("align_words", True) and segs:
            segs = align_segments(segs, wav, language, settings, _sub(progress, asr_end, asr_end + 0.07), cancel)
    segs = normalize(segs)
    p.segments = segs
    p.data["sub_source"] = method
    if method != "ocr" and engine != "whisper":
        p.data.pop("asr_warning", None)
    p.save()
    # 3) phân biệt người nói
    if want_spk and not spk_from_asr and segs:
        from . import diarize as dz

        if dz.available():
            try:
                diarize(p, settings, _sub(progress, asr_end + 0.07 if method != "ocr" else 0.85, 1.0), cancel)
            except ff.Cancelled:
                raise
            except Exception as e:
                if progress:
                    progress(1.0, f"Bỏ qua phân biệt người nói ({str(e)[:80]})")
    return len(segs)


# ------------------------------------------------------------------ dịch

def translate(p: Project, engine: str | None = None, style: str | None = None, only_empty: bool = False,
              settings: dict | None = None, progress: Prog | None = None, cancel=None) -> int:
    from .translate import Translator, glossary_text, parse_glossary

    settings = settings or load_settings()
    segs = p.segments
    if not segs:
        raise ValueError("Chưa có phụ đề gốc. Hãy nhận dạng giọng nói/OCR hoặc nhập file phụ đề trước.")
    cache = p.data.setdefault("cache", {})
    tr = Translator(settings, engine, style, parse_glossary(p.data.get("glossary", "")), cache)

    last_save = [time.time()]

    def prog(x, m):
        if progress:
            progress(x, m)
        if time.time() - last_save[0] > 20:  # lưu định kỳ để không mất công khi video dài
            p.save()
            last_save[0] = time.time()

    tr.translate_segments(segs, prog, cancel, only_empty=only_empty, summary=p.data.get("summary", ""))
    # ghi lại thuật ngữ AI tìm được để người dùng xem/sửa
    user_g = parse_glossary(settings.get("glossary", ""))
    proj_g = parse_glossary(p.data.get("glossary", ""))
    for k, v in tr.glossary.items():
        if k not in user_g:
            proj_g.setdefault(k, v)
    p.data["glossary"] = glossary_text(proj_g)
    p.data["translate_warning"] = (f"{tr.fallback_lines} câu AI trả lời lỗi định dạng nên đã dịch bằng Google – "
                                   f"nên soát lại các câu này" if tr.fallback_lines else "")
    p.segments = segs
    p.save()
    return sum(1 for s in segs if s.trans)


# ------------------------------------------------------------------ lồng tiếng

def speaker_list(p: Project) -> list[dict]:
    """Danh sách người nói kèm số câu và giọng lồng đã chọn."""
    counts: dict[str, int] = {}
    for s in p.segments:
        k = s.speaker or "S1"
        counts[k] = counts.get(k, 0) + 1
    voices = p.data.setdefault("voices", {})
    out = []
    for k in sorted(counts, key=lambda x: (-counts[x], x)):
        out.append({"speaker": k, "lines": counts[k], "voice": voices.get(k, "")})
    return out


def dub(p: Project, voice: str | None = None, rate: str | None = None, settings: dict | None = None,
        progress: Prog | None = None, cancel=None, engine: str | None = None) -> Path:
    """Lồng tiếng Việt. Mỗi người nói có thể có giọng riêng (p.data['voices'][speaker]):
       'edge:<voice id>' | 'vieneu:<preset>' | 'clone' (nhái giọng nhân vật gốc bằng VieNeu)."""
    from .tts import synthesize_dub

    settings = settings or load_settings()
    segs = p.segments
    if not any(s.trans for s in segs):
        raise ValueError("Chưa có bản dịch để lồng tiếng.")
    engine = engine or settings.get("tts_engine", "edge")
    if voice:
        default_voice = voice
    elif engine == "omnivoice":
        from . import voices as vlib
        from .tts import OMNI_CLONE

        smp = vlib.list_samples()
        default_voice = f"omni:{smp[0]['id']}" if smp else OMNI_CLONE
    else:
        default_voice = f"edge:{settings.get('tts_voice')}" if engine == "edge" else "vieneu:"
    voices = dict(p.data.get("voices") or {})
    st = stems(p)
    # mẫu giọng để nhái: lấy từ giọng đã tách (sạch) hoặc âm thanh gốc
    ref_audio = st.get("vocals") or str(p.source)
    (p.work / "dubs").mkdir(parents=True, exist_ok=True)
    out = p.work / "dubs" / f"dub_{time.strftime('%Y%m%d_%H%M%S')}.wav"
    stats: dict = {}

    def run(pr):
        stats.clear()
        synthesize_dub(segs, out, p.data.get("info", {}).get("duration", 0), p.work / "tts",
                       default_voice, rate or settings.get("tts_rate", "+0%"),
                       max_speedup=float(settings.get("tts_max_speedup", 1.3)), progress=pr, cancel=cancel,
                       voices=voices, ref_audio=ref_audio, settings=settings,
                       skip_songs=bool(settings.get("dub_skip_songs", True)), stats=stats)

    run(_sub(progress, 0.0, 0.85))
    # tầng 3b: câu vẫn quá dài -> AI rút gọn bản dịch rồi đọc lại (chỉ những câu đó, câu khác dùng lại bản đã đọc)
    long_ = stats.get("too_long") or []
    shortened = 0
    if long_ and settings.get("dub_auto_shorten", True) and settings.get("translate_engine", "google") != "google":
        from .translate import Translator, budget_for, syllables

        if progress:
            progress(0.86, f"AI đang rút gọn {len(long_)} câu đọc quá dài so với thời gian nhân vật nói…")
        try:
            tr = Translator(settings)
            items = []
            for k, need, have in long_:
                cur = segs[k].trans
                n = min(syllables(cur) - 1, max(2, int(syllables(cur) * max(0.4, have / max(need, 0.1)) * 0.95)))
                items.append((segs[k].text, cur, max(2, min(n, budget_for(have, 1.25)))))
            new = tr.shorten(items)
            for (k, _, _), t in zip(long_, new):
                if t != segs[k].trans:
                    segs[k].extra.setdefault("trans_long", segs[k].trans)   # giữ bản dài để người dùng xem lại
                    segs[k].trans = t
                    shortened += 1
        except Exception as e:  # noqa: BLE001 – lỗi AI không làm hỏng bản lồng
            if progress:
                progress(0.86, f"Không rút gọn được ({str(e)[:80]})")
        if shortened:
            run(_sub(progress, 0.87, 1.0))
    stats["shortened"] = shortened
    p.segments = segs            # lưu độ vừa (fit) + bản dịch đã rút gọn
    layout = stats.pop("layout", [])
    p.data["dub_layout"] = layout
    p.data["dub_stats"] = stats
    vers = dub_versions(p)
    vers.insert(0, {"wav": str(out), "time": time.strftime("%H:%M %d/%m"), "voices": _voice_summary(voices, default_voice),
                    "stats": stats, "layout": layout})
    p.data["dub_versions"] = vers
    ed = p.edit
    ed["dub"]["enabled"] = True
    ed["dub"]["wav"] = str(out)
    if st:  # có nhạc nền đã tách -> dùng làm nền cho bản lồng tiếng (sạch giọng gốc)
        ed["dub"]["bg_wav"] = st["instrumental"]
        ed["dub"]["vocals_wav"] = st["vocals"]
        ed["dub"]["song_spans"] = [[s.start, s.end] for s in segs if s.extra.get("song")] \
            if settings.get("dub_skip_songs", True) else []
    p.data["edit"] = ed
    p.save()
    return out


def _voice_summary(voices: dict, default_voice: str) -> str:
    def name(spec: str) -> str:
        if spec == "clone":
            return "Nhái giọng"
        if spec.startswith("vieneu:"):
            return (spec[7:] or "VieNeu") + " (VieNeu)"
        if spec.startswith("edge:"):
            return spec[5:].replace("vi-VN-", "").replace("Neural", "") + " (Edge)"
        return spec
    if not voices:
        return name(default_voice)
    return ", ".join(f"{k}: {name(v)}" for k, v in sorted(voices.items()))


def dub_versions(p: Project) -> list[dict]:
    """Các bản lồng tiếng đã tạo (mới nhất trước), chỉ giữ bản còn file."""
    vers = [v for v in (p.data.get("dub_versions") or []) if v.get("wav") and Path(v["wav"]).exists()]
    cur = (p.edit.get("dub") or {}).get("wav")
    if cur and Path(cur).exists() and not any(v["wav"] == cur for v in vers):   # bản tạo từ phiên bản cũ của tool
        t = time.strftime("%H:%M %d/%m", time.localtime(Path(cur).stat().st_mtime))
        vers.append({"wav": cur, "time": t, "voices": "", "stats": p.data.get("dub_stats") or {}})
    return vers


def use_dub_version(p: Project, wav: str) -> None:
    """Chọn bản lồng tiếng dùng cho xem trước + xuất video."""
    v = next((x for x in dub_versions(p) if x["wav"] == wav), None)
    if not v:
        raise ValueError("Không tìm thấy bản lồng tiếng này")
    ed = p.edit
    ed["dub"]["wav"] = wav
    ed["dub"]["enabled"] = True
    p.data["edit"] = ed
    p.data["dub_stats"] = v.get("stats") or {}
    p.data["dub_layout"] = v.get("layout") or []
    p.data["dub_versions"] = dub_versions(p)


def delete_dub_version(p: Project, wav: str) -> None:
    vers = [v for v in dub_versions(p) if v["wav"] != wav]
    try:
        Path(wav).unlink(missing_ok=True)
    except OSError:
        pass  # file đang được mở ở chương trình khác -> để lại, chỉ bỏ khỏi danh sách
    p.data["dub_versions"] = vers
    ed = p.edit
    if ed["dub"].get("wav") == wav:
        if vers:
            ed["dub"]["wav"] = vers[0]["wav"]
            p.data["dub_stats"] = vers[0].get("stats") or {}
        else:
            ed["dub"]["wav"] = ""
            ed["dub"]["enabled"] = False
            p.data["dub_stats"] = {}
    p.data["edit"] = ed


def dub_preview_audio(p: Project) -> Path | None:
    """Trộn sẵn âm thanh 'đã lồng tiếng' (giọng Việt + nhạc nền) theo mốc thời gian video gốc
    để trình phát trong tool nghe được giống video xuất. Trả về None nếu chưa có bản lồng."""
    import hashlib

    d = (p.edit.get("dub") or {})
    wav = d.get("wav")
    if not (d.get("enabled") and wav and Path(wav).exists()):
        return None
    bg = d.get("bg_wav") if d.get("bg_wav") and Path(d["bg_wav"]).exists() else None
    voc = d.get("vocals_wav") if bg and d.get("vocals_wav") and Path(d["vocals_wav"]).exists() else None
    spans = d.get("song_spans") or []
    vol = float(p.edit.get("volume", 1.0))
    key = repr((Path(wav).stat().st_mtime, bg, voc, spans, vol, d.get("bg_volume"), d.get("vocals_volume"),
                d.get("original_volume"), d.get("dub_volume"), p.edit.get("mute_original")))
    out = p.work / f"dub_preview_{hashlib.md5(key.encode()).hexdigest()[:10]}.m4a"
    if out.exists() and out.stat().st_size > 0:
        return out
    for old in p.work.glob("dub_preview_*.m4a"):
        try:
            old.unlink(missing_ok=True)
        except OSError:
            pass  # đang được trình phát mở (Windows khoá file) -> xoá ở lần sau
    args, fc, mix = [], [], []
    mute = bool(p.edit.get("mute_original"))
    if bg:
        if not mute:
            args += ["-i", str(bg)]
            fc.append(f"[{len(args) // 2 - 1}:a]aresample=44100,volume={vol * float(d.get('bg_volume', 1.0)):.3f}[b]")
            mix.append("[b]")
        base_v = float(d.get("vocals_volume", 0.0))
        if voc and not mute and (spans or base_v > 0.001):
            cond = "+".join(f"between(t,{a:.3f},{b:.3f})" for a, b in spans) or "0"
            args += ["-i", str(voc)]
            fc.append(f"[{len(args) // 2 - 1}:a]aresample=44100,"
                      f"volume='if({cond},{vol:.3f},{base_v * vol:.3f})':eval=frame[v]")
            mix.append("[v]")
    elif p.data.get("info", {}).get("has_audio", True) and not mute:
        ov = vol * float(d.get("original_volume", 0.15))
        if ov > 0.001:
            args += ["-i", str(p.source)]
            fc.append(f"[{len(args) // 2 - 1}:a]aresample=44100,volume={ov:.3f}[o]")
            mix.append("[o]")
    args += ["-i", str(wav)]
    fc.append(f"[{len(args) // 2 - 1}:a]aresample=44100,volume={float(d.get('dub_volume', 1.0)):.3f}[d]")
    mix.append("[d]")
    fc.append("".join(mix) + (f"amix=inputs={len(mix)}:duration=longest:dropout_transition=0:normalize=0,"
                              if len(mix) > 1 else "anull,") + "alimiter=limit=0.95[out]")
    tmp = out.with_suffix(".tmp.m4a")
    ff.run(args + ["-filter_complex", ";".join(fc), "-map", "[out]", "-vn", "-c:a", "aac", "-b:a", "160k",
                   str(tmp)], float(p.data.get("info", {}).get("duration", 0)), None, None, "Đang trộn bản nghe thử")
    tmp.replace(out)
    return out


def dub_summary(p: Project) -> str:
    st = p.data.get("dub_stats") or {}
    if not st:
        return "Đã tạo bản lồng tiếng"
    msg = f"Đã lồng tiếng {st.get('placed', 0)}/{st.get('total', 0)} câu"
    extra = []
    fit = st.get("fit") or {}
    if fit:
        extra.append(f"{fit.get('ok', 0)} vừa khít, {fit.get('fast', 0)} đọc nhanh hơn"
                     + (f", ⚠ {fit['long']} quá dài – sửa ngắn lại ở tab Phụ đề" if fit.get("long") else ""))
    if st.get("shortened"):
        extra.append(f"AI đã rút gọn {st['shortened']} câu")
    if st.get("songs"):
        extra.append(f"{st['songs']} câu bài hát giữ nguyên")
    if st.get("empty"):
        extra.append(f"{st['empty']} câu chưa dịch")
    if st.get("errors"):
        extra.append(f"{st['errors']} câu lỗi")
    return msg + (f" ({', '.join(extra)})" if extra else "")


# ------------------------------------------------------------------ xuất

def export_subs(p: Project, fmt: str = "srt", which: str = "trans", settings: dict | None = None) -> Path:
    settings = settings or load_settings()
    info = p.data.get("info", {})
    name = safe_name(Path(p.data.get("source", "video")).stem or p.data.get("name", "video"))
    suffix = {"trans": "vi", "source": "zh", "both": "zh-vi"}.get(which, "vi")
    out = p.output_dir / f"{name}.{suffix}.{fmt}"
    save_subtitles(p.segments, out, which, settings.get("sub_style"), info.get("width") or 1920,
                   info.get("height") or 1080)
    p.add_output(out, "subtitle")
    return out


def old_sub_region(p: Project) -> dict | None:
    """Vùng phụ đề Trung cũ trên khung gốc (từ OCR, hoặc vùng người dùng khoanh để xoá)."""
    ed = p.edit
    regs = (ed.get("hardsub") or {}).get("regions") or []
    if p.data.get("ocr_region"):
        return p.data["ocr_region"]
    if regs and (ed.get("hardsub") or {}).get("mode", "none") != "none":
        # chọn vùng thấp nhất (thường là phụ đề, vùng khác có thể là logo)
        return max(regs, key=lambda r: float(r["y"]) + float(r["h"]))
    return None


def effective_style(p: Project, style: dict) -> dict:
    """Vị trí 'auto': đặt câu Việt vào đúng chỗ phụ đề Trung cũ (canh theo mép dưới của vùng chữ cũ)."""
    st = dict(style or {})
    if st.get("position", "auto") != "auto":
        return st
    st["position"] = "bottom"
    if st.get("margin_v"):
        return st
    ed = p.edit
    reg = old_sub_region(p)
    if not reg or ed.get("crop") or ed.get("aspect", "original") != "original" or int(ed.get("rotate") or 0) \
            or ed.get("flip_v"):
        return st  # khung hình đã đổi -> không suy ra được vị trí cũ, dùng mặc định sát đáy
    # canh TÂM dòng chữ Việt (1 dòng) trùng tâm vùng chữ Trung cũ
    center = float(reg["y"]) + float(reg["h"]) / 2
    zoom = float(ed.get("zoom") or 1.0)
    center = 0.5 + (center - 0.5) * zoom
    info = p.data.get("info", {})
    from .video import target_size

    _, th = target_size(info.get("width") or 1920, info.get("height") or 1080, "original",
                        ed.get("resolution", "original"))
    size_rel = 0.055
    if st.get("size"):
        size_rel = float(st["size"]) / max(1, th)
    elif p.data.get("ocr_region"):
        # cỡ chữ tự động = cỡ phụ đề Trung cũ (vùng OCR đã cộng thêm ~0.02 lề mỗi phía)
        old_h = (float(reg["h"]) - 0.04) * zoom
        size_rel = min(0.085, max(0.045, old_h * 1.2))
        st["size"] = max(16, round(size_rel * th))
    bottom = center + size_rel * 0.42  # 0.42: bù khoảng trống dưới chân chữ của font
    if 0.25 < center < 0.97:
        st["margin_v_rel"] = round(min(0.7, max(0.01, 1.0 - bottom)), 4)
    return st


def render(p: Project, settings: dict | None = None, progress: Prog | None = None, cancel=None,
           out_name: str | None = None) -> Path:
    from .video import build_and_render

    settings = settings or load_settings()
    edit = p.edit
    hs = edit.get("hardsub") or {}
    if hs.get("mode") == "inpaint" and p.data.get("ocr_boxes") and hs.get("use_ocr_boxes", True):
        hs["boxes_file"] = p.data["ocr_boxes"]
    from .config import _deep_merge

    base_style = _deep_merge(settings.get("sub_style") or {}, (edit.get("subtitles") or {}).get("style") or {})
    edit.setdefault("subtitles", {})["style"] = effective_style(p, base_style)
    name = out_name or (safe_name(Path(p.data.get("source", "video")).stem) + "_vietsub")
    out = p.output_dir / f"{safe_name(name)}.mp4"
    if out.exists():
        out = p.output_dir / f"{safe_name(name)}_{time.strftime('%H%M%S')}.mp4"
    build_and_render(p.source, out, edit, settings, p.work / "render", p.segments, progress, cancel)
    p.add_output(out, "video")
    return out


# ------------------------------------------------------------------ tự động toàn bộ

def auto(p: Project, opts: dict, progress: Prog | None = None, cancel=None) -> dict:
    """Chạy trọn gói: (tải) -> nhận dạng -> dịch -> (lồng tiếng) -> xuất video + phụ đề."""
    settings = load_settings()
    if opts.get("engine"):
        settings["translate_engine"] = opts["engine"]
    steps = []
    if opts.get("url") and not p.data.get("source"):
        steps.append("download")
    if not p.segments or opts.get("force_recognize"):
        steps.append("recognize")
    if opts.get("translate", True):
        steps.append("translate")
    if opts.get("dub"):
        steps.append("dub")
    steps.append("export")
    weights = {"download": 1, "recognize": 4, "translate": 2, "dub": 2, "export": 3}
    total_w = sum(weights[s] for s in steps)
    acc = 0.0
    result: dict = {}
    for st in steps:
        a = acc / total_w
        acc += weights[st]
        pr = _sub(progress, a, acc / total_w)
        if st == "download":
            path = download_video(opts["url"], p.dir / "source", pr, cancel)
            attach_source(p, path)
            p.data["name"] = path.stem[:80]
            p.save()
        elif st == "recognize":
            if opts.get("asr_engine"):
                settings["asr_engine"] = opts["asr_engine"]
            if "diarize" in opts:
                settings["diarize"] = bool(opts["diarize"])
            if "separate" in opts:
                settings["sep_before_asr"] = bool(opts["separate"])
            recognize(p, opts.get("method", "asr"), opts.get("language", "zh"), opts.get("region"),
                      float(opts.get("ocr_fps", 4)), settings, pr, cancel, bool(opts.get("denoise")))
        elif st == "translate":
            translate(p, opts.get("engine"), opts.get("style"), bool(opts.get("only_empty")), settings, pr, cancel)
        elif st == "dub":
            if opts.get("separate") and not stems(p):
                from . import separate as sepmod

                if sepmod.available():
                    separate_audio(p, settings, _sub(pr, 0, 0.4), cancel)
                    pr = _sub(pr, 0.4, 1.0)
            dub(p, opts.get("voice"), opts.get("rate"), settings, pr, cancel, opts.get("tts_engine"))
        elif st == "export":
            ed = p.edit
            if opts.get("hardsub_mode"):
                ed["hardsub"]["mode"] = opts["hardsub_mode"]
                if opts.get("hardsub_mode") != "none" and not ed["hardsub"].get("regions"):
                    reg = p.data.get("ocr_region")
                    if not reg:
                        from .ocr import detect_subtitle_region

                        reg = detect_subtitle_region(p.source)
                    ed["hardsub"]["regions"] = [reg]
            if "burn" in opts:
                ed["subtitles"]["burn"] = bool(opts["burn"])
            if opts.get("which"):
                ed["subtitles"]["which"] = opts["which"]
            if opts.get("dub"):
                ed["dub"]["enabled"] = True
            p.data["edit"] = ed
            p.save()
            srt = export_subs(p, "srt", "trans", settings)
            result["srt"] = str(srt)
            if opts.get("render", True) and p.data.get("info", {}).get("has_video"):
                result["video"] = str(render(p, settings, pr, cancel))
    result["message"] = "Hoàn tất! " + (f"Video: {Path(result['video']).name}" if result.get("video") else "")
    if opts.get("dub"):
        result["message"] += "  · " + dub_summary(p)
    for w in ("asr_warning", "translate_warning"):
        if p.data.get(w):
            result["message"] += "  ⚠ " + p.data[w]
    return result
