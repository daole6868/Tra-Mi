"""Các bước xử lý (dùng chung cho giao diện web và dòng lệnh)."""
from __future__ import annotations

import time
from pathlib import Path
from typing import Callable

from . import ffmpeg as ff, net
from .config import err_text, load_settings, log_error
from .project import Project, safe_name
from .subtitle import Segment, normalize, save_subtitles

Prog = Callable[[float, str], None]
NO_TRANSLATE = "none"   # tuỳ chọn "Không dịch": chỉ nghe + tách câu, xuất SRT gốc để dịch ở phần mềm khác


def _sub(progress: Prog | None, a: float, b: float) -> Prog:
    def f(x: float, m: str = ""):
        if progress:
            progress(a + (b - a) * max(0.0, min(1.0, x)), m)
    return f


# ------------------------------------------------------------------ nguồn video

def ytdlp_version() -> str:
    """Phiên bản yt-dlp đang cài (không import – đọc thông tin gói)."""
    import importlib
    import importlib.metadata as md

    importlib.invalidate_caches()
    try:
        return md.version("yt-dlp")
    except md.PackageNotFoundError:
        return ""


def update_ytdlp(progress: Prog | None = None, cancel=None) -> dict:
    """Cập nhật yt-dlp (Bilibili / Douyin hay đổi cách chặn – bản cũ báo lỗi 412 / Unable to extract)."""
    import subprocess
    import sys

    from .config import FROZEN

    if FROZEN:
        raise RuntimeError("Bản đóng gói .exe không tự cập nhật trình tải được – hãy tải bản TraMi mới.")
    old = ytdlp_version()
    if progress:
        progress(0.1, f"Đang cập nhật yt-dlp {old}…")
    r = subprocess.run([sys.executable, "-m", "pip", "install", "-U", "yt-dlp"], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", creationflags=ff._NO_WINDOW, timeout=900)
    if r.returncode:
        raise RuntimeError("pip báo lỗi khi cập nhật yt-dlp:\n" + (r.stderr or r.stdout or "")[-800:])
    new = ytdlp_version()
    if new == old:
        return {"message": f"yt-dlp {new} đã là bản mới nhất"}
    again = " – mở lại tool để dùng bản mới" if "yt_dlp" in sys.modules else ""
    return {"message": f"Đã cập nhật yt-dlp {old} → {new}{again}"}


def _dl_error(e: BaseException, url: str, used_cookies: bool) -> str:
    """Lỗi yt-dlp -> lời giải thích + cách sửa."""
    from . import sitelogin

    msg = str(e)
    low = msg.lower()
    upd = "bấm 'Cập nhật trình tải' ở khung Tải video từ link (Trang chủ) rồi mở lại tool"
    if "412" in msg or "precondition failed" in low:
        return f"Bilibili chặn bản trình tải đang cài (HTTP 412) – {upd}.\n({msg[:200]})"
    if "fresh cookies" in low:
        return ("Douyin cần cookie trình duyệt: ở Trang chủ, mục 'Trang khác lấy đăng nhập từ' chọn Firefox (mở "
                f"douyin.com trên Firefox 1 lần trước) hoặc chọn file cookies.txt.\n({msg[:200]})")
    if "unsupported url" in low or "is unusual and will be skipped" in low:
        # bộ tải chung nhận về trang web chứ không phải video: link không phải trang phát video / trang chưa hỗ trợ
        return ("Tool không tìm thấy video ở link này (nhận về trang web, không phải video).\n"
                f"Link đã thử: {url}\n"
                "Mở video trên trình duyệt MÁY TÍNH, chép link ở thanh địa chỉ khi video đang phát (Bilibili dạng "
                "https://www.bilibili.com/video/BV…, Douyin dạng https://www.douyin.com/video/…) rồi dán lại. "
                f"Link trang cá nhân / danh sách / ảnh không tải được.\n({msg[:160]})")
    if any(x in low for x in ("not available in your country", "geo restrict", "地区")):
        return f"Video bị giới hạn khu vực, không tải được từ Việt Nam.\n({msg[:200]})"
    if any(x in low for x in ("premium", "大会员", "vip", "paid", "purchase")):
        return f"Video chỉ dành cho tài khoản trả phí (VIP / 大会员).\n({msg[:200]})"
    if any(x in low for x in ("login", "sign in", "log in", "cookies")) and not used_cookies:
        hint = ("bấm 'Đăng nhập Bilibili (quét mã QR)'" if sitelogin.is_bilibili(url)
                else "chọn trình duyệt ở 'Trang khác lấy đăng nhập từ'")
        return f"Video cần đăng nhập – {hint} ở Trang chủ rồi tải lại.\n({msg[:200]})"
    return f"{msg[:400]}\nNếu trước đây tải được: {upd}."


def download_video(url: str, dest_dir: Path, progress: Prog | None = None, cancel=None,
                   notes: list | None = None, settings: dict | None = None) -> Path:
    """Tải video từ Douyin, Bilibili, YouTube, TikTok, Weibo, Xigua... bằng yt-dlp.

    `url` có thể là cả dòng chia sẻ của app ("【tên】 https://b23.tv/xx") – tự lấy link. Bilibili đã đăng nhập bằng mã QR
    (sitelogin) -> tải 720p/1080p; trang khác dùng cookie trình duyệt / file cookies.txt ở Cài đặt. Chọn ≤ 1080p, ưu
    tiên H.264 (trình phát + FFmpeg mở nhanh, không phải tạo bản xem trước như HEVC/AV1). Tiến độ gộp hình + tiếng
    (Bilibili / YouTube tải 2 file rồi ghép). `notes` nhận lời nhắc (vd. chỉ 480p vì chưa đăng nhập)."""
    import yt_dlp

    from . import sitelogin

    raw = url
    url = sitelogin.extract_url(url)
    if not url:
        raise ValueError(f"Không tìm thấy link trong: {raw[:120]}")
    if progress:
        progress(0.0, "Đang mở link…")
    # m.bilibili.com / b23.tv / v.douyin.com… -> dạng yt-dlp nhận ra (không thì rơi vào bộ tải chung -> lỗi 'html')
    url = sitelogin.normalize_url(url)
    settings = settings if settings is not None else load_settings()
    bili = sitelogin.is_bilibili(url)
    cookie_opts: dict = {}
    if bili and sitelogin.bili_cookie_file():
        cookie_opts = {"cookiefile": str(sitelogin.BILI_FILE)}
    elif (settings.get("dl_cookie_file") or "") and Path(settings["dl_cookie_file"]).is_file():
        cookie_opts = {"cookiefile": settings["dl_cookie_file"]}
    elif settings.get("dl_browser"):
        cookie_opts = {"cookiesfrombrowser": (settings["dl_browser"],)}

    dest_dir.mkdir(parents=True, exist_ok=True)
    parts: dict[str, list] = {}       # file đang tải -> [đã tải, tổng]
    state = {"t": 0.0}

    def hook(d):
        if cancel and cancel():
            raise ff.Cancelled()
        if not progress:
            return
        st = d.get("status")
        name = d.get("filename") or d.get("tmpfilename") or "?"
        tot = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
        if st == "finished":
            parts[name] = [tot or parts.get(name, [0, 0])[1], tot or parts.get(name, [0, 0])[1]]
            return
        if st != "downloading":
            return
        parts[name] = [d.get("downloaded_bytes") or 0, tot]
        now = time.time()
        if now - state["t"] < 0.4:
            return
        state["t"] = now
        info = d.get("info_dict") or {}
        req = info.get("requested_formats") or [info]
        sizes = [f.get("filesize") or f.get("filesize_approx") or 0 for f in req]
        done = sum(v[0] for v in parts.values())
        expect = max(sum(sizes), sum(v[1] for v in parts.values())) if all(sizes) else 0
        if expect:
            frac = min(1.0, done / expect)
        else:   # chưa biết cỡ cả 2 file: hình ~85 %, tiếng ~15 %
            k = len(parts) - 1
            w = [0.85, 0.15] if len(req) == 2 else [1.0 / max(1, len(req))] * max(1, len(req))
            frac = sum(w[:k]) + w[min(k, len(w) - 1)] * (parts[name][0] / tot if tot else 0)
            expect = sum(v[1] for v in parts.values())
        kind = "" if len(req) < 2 else (" (hình)" if (info.get("vcodec") or "none") != "none" else " (tiếng)")
        sp = d.get("speed") or 0
        size = f" · {done / 1e6:.0f}/{expect / 1e6:.0f} MB" if expect else f" · {done / 1e6:.0f} MB"
        progress(min(0.95, frac * 0.95), f"Đang tải video{kind} {int(frac * 100)}%{size}"
                 + (f" · {sp / 1e6:.1f} MB/s" if sp else ""))

    def pp_hook(d):
        if progress and d.get("status") == "started" and "Merger" in str(d.get("postprocessor", "")):
            progress(0.96, "Đang ghép hình và tiếng…")

    opts = {
        "outtmpl": str(dest_dir / "%(title).80s [%(id)s].%(ext)s"),
        "format": "bv*+ba/b",
        # ≤ 1080p (4K làm mọi bước chậm mà đăng Facebook / YouTube không cần), cùng cỡ thì H.264 + AAC trước
        "format_sort": ["res:1080", "vcodec:h264", "acodec:aac"],
        "merge_output_format": "mp4",
        "noplaylist": True,
        "progress_hooks": [hook],
        "postprocessor_hooks": [pp_hook],
        "quiet": True,
        "noprogress": True,     # tiến độ đi qua hook (không in ra cửa sổ đen)
        "no_warnings": True,
        "ffmpeg_location": ff.ffmpeg_bin(),
        "restrictfilenames": False,
        "windowsfilenames": True,
        "retries": 10,
        "fragment_retries": 10,
        "socket_timeout": 20,   # mạng lag / rớt: 20 s không có dữ liệu thì thử lại (mặc định chờ rất lâu)
        "http_headers": {"User-Agent": sitelogin.UA},
    }

    def run_once(extra: dict):
        with yt_dlp.YoutubeDL({**opts, **extra}) as ydl:
            info = ydl.extract_info(url, download=True)
            return info, Path(ydl.prepare_filename(info))

    def run(extra: dict):
        # chạy ở luồng phụ -> bấm Huỷ dừng ngay cả khi mạng đang đứng; mất mạng -> chờ có mạng rồi tải tiếp (nối file .part)
        while True:
            try:
                return net.call(run_once, extra, cancel=cancel)
            except yt_dlp.utils.DownloadError as e:
                if not (net.is_net_error(e) and net.wait_online("tải video", cancel=cancel)):
                    raise

    try:
        try:
            info, path = run(cookie_opts)
        except yt_dlp.utils.DownloadError as e:
            low = str(e).lower()
            # Chrome / Edge trên Windows: cookie mã hoá "App-Bound" hoặc file đang bị trình duyệt khoá -> tải không cookie
            if cookie_opts.get("cookiesfrombrowser") and any(
                    x in low for x in ("cookie", "dpapi", "decrypt", "could not copy", "permission denied")):
                if notes is not None:
                    notes.append(f"Không đọc được cookie của {settings['dl_browser']} (Chrome / Edge trên Windows hay "
                                 "khoá cookie) – đã tải không đăng nhập. Nên dùng Firefox hoặc file cookies.txt.")
                cookie_opts = {}
                info, path = run({})
            else:
                raise
    except ff.Cancelled:
        raise
    except yt_dlp.utils.DownloadError as e:
        if isinstance(getattr(e, "exc_info", (None, None))[1], ff.Cancelled):
            raise ff.Cancelled() from None
        raise RuntimeError(_dl_error(e, url, bool(cookie_opts))) from None
    if path.suffix.lower() != ".mp4" and path.with_suffix(".mp4").exists():
        path = path.with_suffix(".mp4")   # đã ghép hình + tiếng thành .mp4
    if not path.exists():
        cands = sorted(dest_dir.glob("*.mp4"), key=lambda p: p.stat().st_mtime, reverse=True)
        if cands:
            path = cands[0]
    if not path.exists():
        raise RuntimeError("Tải video thất bại.")
    h = int(info.get("height") or 0)
    if bili and h and h <= 480 and not cookie_opts and notes is not None:
        notes.append(f"Video chỉ {h}p vì chưa đăng nhập Bilibili – ở Trang chủ bấm 'Đăng nhập Bilibili (quét mã QR)' "
                     "rồi tải lại để có 720p / 1080p.")
    if progress:
        progress(1.0, "Đã tải xong video" + (f" ({h}p)" if h else ""))
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


VIDEO_EXTS = (".mp4", ".mkv", ".mov", ".flv", ".webm", ".avi", ".m4v", ".ts", ".wmv")


def source_missing(p: Project) -> bool:
    """Dự án có video gốc nhưng file không còn ở chỗ cũ (bị chuyển / đổi tên / xoá, hoặc mở dự án trên máy khác)."""
    return bool(p.data.get("source")) and not p.source.exists()


def _same_length(path: Path, want: float) -> bool:
    if not want:
        return True
    try:
        return abs(ff.probe(path).duration - want) <= max(1.5, want * 0.01)
    except Exception:  # noqa: BLE001
        return False


def find_moved_source(p: Project) -> Path | None:
    """Tìm lại video gốc đã bị chuyển chỗ: file CÙNG TÊN, cùng thời lượng, ở thư mục cũ, thư mục tool, Downloads, Desktop,
    Videos, Documents (và các thư mục con 1 cấp của chúng)."""
    from .config import APP_DIR

    name = p.source.name
    if not name:
        return None
    home = Path.home()
    roots = [p.source.parent, APP_DIR, APP_DIR.parent, p.dir] + \
        [home / d for d in ("Downloads", "Desktop", "Videos", "Documents", "OneDrive/Desktop", "OneDrive/Documents")]
    want = float(p.data.get("info", {}).get("duration", 0) or 0)
    seen: set[str] = set()
    for root in roots:
        try:
            if not root.is_dir():
                continue
            dirs = [root] + [d for d in sorted(root.iterdir())[:300] if d.is_dir()]
        except OSError:
            continue
        for d in dirs:
            cand = d / name
            key = str(cand).lower()
            if key in seen:
                continue
            seen.add(key)
            try:
                if cand.is_file() and _same_length(cand, want):
                    return cand
            except OSError:
                continue
    return None


def relink_source(p: Project, path: str | Path) -> Path:
    """Nối dự án với video gốc ở chỗ mới – giữ nguyên phụ đề, bản dịch, lồng tiếng. Video khác hẳn độ dài -> báo lỗi
    (phụ đề / lồng tiếng sẽ lệch)."""
    path = Path(path).expanduser().resolve()
    info = ff.probe(path)
    if not info.has_video and not info.has_audio:
        raise ValueError("File không phải video/âm thanh hợp lệ.")
    want = float(p.data.get("info", {}).get("duration", 0) or 0)
    if want and abs(info.duration - want) > max(1.5, want * 0.01):
        def mmss(x: float) -> str:
            return f"{int(x // 60)}:{int(x % 60):02d}"
        raise ValueError(f"Video này dài {mmss(info.duration)}, video gốc của dự án dài {mmss(want)} – không phải cùng "
                         "video (phụ đề, lồng tiếng sẽ lệch). Chọn đúng file video đã dùng khi tạo dự án.")
    p.data["source"] = str(path)
    p.data["info"] = info.to_dict()
    p.save()
    return path


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


def timeline_assets(p: Project) -> dict:
    """Dải ảnh thu nhỏ + đường sóng âm cho timeline (tạo 1 lần, lưu trong work/). Video dài vẫn ≤ 160 ảnh."""
    import math
    import subprocess

    import numpy as np

    info = p.data.get("info", {})
    dur = float(info.get("duration") or 0)
    if not dur or not p.data.get("source"):
        return {}
    out: dict = {}
    if info.get("has_video", True):
        step = max(1.0, dur / 160)
        n = max(1, math.ceil(dur / step))
        strip = p.work / f"tl_thumbs_{n}.jpg"
        if not (strip.exists() and strip.stat().st_size > 0):
            tmp = strip.with_name(strip.stem + ".tmp.jpg")
            ff.run(["-i", str(p.source), "-an", "-vf", f"fps=1/{step:.4f},scale=-2:54,tile={n}x1",
                    "-frames:v", "1", "-q:v", "6", str(tmp)], dur, None, None, "Đang tạo ảnh thu nhỏ")
            tmp.replace(strip)
        out.update({"thumbs": str(strip), "step": step, "count": n})
    src = stems(p).get("vocals") or str(p.source)
    wave = p.work / "tl_wave.npy"
    if not wave.exists() or wave.stat().st_mtime < Path(src).stat().st_mtime:
        raw = subprocess.run([ff.ffmpeg_bin(), "-hide_banner", "-loglevel", "error", "-i", str(src), "-vn", "-ac", "1",
                              "-ar", "4000", "-f", "s16le", "pipe:1"], capture_output=True,
                             creationflags=ff._NO_WINDOW).stdout
        a = np.abs(np.frombuffer(raw, np.int16).astype(np.float32) / 32768.0)
        hop = 200   # 0,05 s
        env = a[: len(a) // hop * hop].reshape(-1, hop).max(axis=1) if len(a) >= hop else np.zeros(0, np.float32)
        if len(env):
            env = np.minimum(1.0, env / max(1e-4, float(np.percentile(env, 99))))
        np.save(wave, env.astype(np.float32))
    out.update({"wave": str(wave), "hop": 0.05})
    return out


def import_stems(p: Project, vocals: str | Path, instrumental: str | Path | None = None) -> tuple[Path, Path]:
    """Nạp giọng nói / nhạc nền đã tách bằng phần mềm khác (CapCut, UVR…). Chỉ có file giọng -> nhạc nền = tiếng gốc
    trừ giọng (file giọng phải khớp thời gian với video)."""
    d = p.work / "stems"
    d.mkdir(parents=True, exist_ok=True)
    dur = float(p.data.get("info", {}).get("duration", 0)) or ff.probe(p.source).duration
    fit = ["-af", f"apad=whole_dur={dur:.3f}", "-t", f"{dur:.3f}", "-ac", "2", "-ar", "44100", "-c:a", "pcm_s16le"]
    v, i = d / "vocals.wav", d / "instrumental.wav"
    tmp_v = d / "_import_vocals.wav"
    ff.run(["-i", str(vocals), "-vn"] + fit + [str(tmp_v)], dur, None, None, "Đang nạp file giọng")
    if instrumental:
        ff.run(["-i", str(instrumental), "-vn"] + fit + [str(i)], dur, None, None, "Đang nạp file nhạc nền")
    else:
        ff.run(["-i", str(p.source), "-i", str(tmp_v), "-filter_complex",
                "[0:a]aresample=44100,aformat=channel_layouts=stereo[m];[m][1:a]amerge=inputs=2,"
                "pan=stereo|c0=c0-c2|c1=c1-c3[o]", "-map", "[o]", "-t", f"{dur:.3f}", "-c:a", "pcm_s16le", str(i)],
               dur, None, None, "Đang tính nhạc nền = tiếng gốc − giọng")
    tmp_v.replace(v)
    (d / "model.txt").write_text("external", "utf-8")
    p.data["stems"] = {"vocals": str(v), "instrumental": str(i)}
    p.save()
    return v, i


def stems(p: Project) -> dict:
    st = p.data.get("stems") or {}
    if st.get("vocals") and Path(st["vocals"]).exists() and Path(st.get("instrumental", "")).exists():
        return st
    return {}


def sep_model_name(p: Project) -> str:
    """Tên mô hình đã dùng để tách giọng của dự án (theo work/stems/model.txt – đúng cái thật sự đã chạy)."""
    from .separate import SEP_MODELS

    try:
        used = (p.work / "stems" / "model.txt").read_text("utf-8").strip()
    except OSError:
        return "?"
    names = dict(SEP_MODELS)
    if used == "external":
        return "file bạn nạp vào"
    return names.get(used, used).split(" – ")[0]


def speaker_note(p: Project, settings: dict) -> str:
    """Phân biệt người nói đã chạy theo lựa chọn nào (ghi vào thông báo sau bước 1)."""
    if not settings.get("diarize", True):
        return "phân biệt người nói: tắt"
    n_set = int(settings.get("diarize_speakers", 0) or 0)
    found = len({s.speaker for s in p.segments if s.speaker})
    if n_set == 1:
        return "1 người nói (gán hết S1, không tách câu theo giọng)"
    return (f"người nói: {found}" + (f" (đặt {n_set})" if n_set else " (tự đoán)")
            + (f", tách thêm {p.data['speaker_splits']} câu đổi giọng" if p.data.get("speaker_splits") else ""))


def diarize(p: Project, settings: dict | None = None, progress: Prog | None = None, cancel=None) -> int:
    from .diarize import assign_speakers

    settings = settings or load_settings()
    segs = p.segments
    if not segs:
        raise ValueError("Chưa có phụ đề để phân biệt người nói.")
    if int(settings.get("diarize_speakers", 0) or 0) == 1:
        # đặt "1 người nói": gán hết S1, KHÔNG tách câu ở chỗ "đổi giọng" (vô nghĩa khi chỉ có 1 người – trước đây
        # vẫn đo ECAPA và có thể cắt đôi câu ở chỗ giọng đổi sắc thái)
        for s in segs:
            s.speaker = "S1"
        p.data["speaker_splits"] = 0
        p.segments = segs
        p.save()
        return 1
    audio = stems(p).get("vocals") or str(p.source)
    old_texts = {s.text for s in segs}
    for s in segs:
        s.speaker = ""
    assign_speakers.splits = 0
    n = assign_speakers(audio, segs, float(settings.get("diarize_threshold", 0.62)),
                        int(settings.get("diarize_speakers", 0)), progress, cancel,
                        split_changes=bool(settings.get("split_speaker_changes", True)))
    p.data["speaker_splits"] = int(getattr(assign_speakers, "splits", 0))
    from .align import drop_char_times

    drop_char_times(segs)
    lost = forget_stale_voice(old_texts, segs, moved=False)
    p.segments = segs
    p.save()
    if lost and has_dub(p):   # câu bị tách: bỏ giọng cũ khỏi bản lồng ngay (không đọc lại câu nào)
        relayout(p, settings)
    try:   # đoán giọng nam / nữ của từng người nói để "Tự gán giọng" chọn giọng lồng hợp
        speaker_genders(p, audio)
    except Exception:  # noqa: BLE001
        log_error("Đoán giọng nam/nữ")
    return n


DUB_MARKS = ("dub_at", "fit", "fit_speed", "fit_shift", "fit_over", "fit_need", "fit_have")


def forget_stale_voice(old_texts: set, segs: list, moved: bool) -> int:
    """Sau khi căn lại thời gian / tách câu: câu bị tách (lời gốc khác mọi câu cũ) đang mang giọng lồng của CẢ câu cũ
    -> bỏ (đọc lại khi Tạo bản lồng tiếng; trước đây 2 nửa cùng giữ 1 giọng -> xếp lại thì đặt 2 lần). moved: mốc thời
    gian đã đổi -> độ vừa / đoạn giọng cũ không còn đúng (xếp lại tính lại). Trả về số câu bị bỏ giọng."""
    n = 0
    for s in segs:
        if moved:
            for k in DUB_MARKS:
                s.extra.pop(k, None)
        if s.text not in old_texts and s.extra.get("tts_file"):
            for k in DUB_MARKS + ("tts_file", "take"):
                s.extra.pop(k, None)
            n += 1
    return n


def has_dub(p: Project) -> bool:
    """Đã có bản lồng tiếng + giọng từng câu (xếp lại được mà không đọc lại)."""
    wav = (p.edit.get("dub") or {}).get("wav")
    return bool(wav and Path(wav).exists() and any(s.extra.get("tts_file") for s in p.segments))


def speaker_genders(p: Project, audio: str | None = None) -> dict:
    """{người nói: {"g": male/female/"", "conf", "f0", "by"}} – lưu p.data["speaker_gender"]. Chạy CPU, vài giây."""
    from .asr_funasr import load_wav16k
    from .gender import speaker_gender

    wav = load_wav16k(audio or stems(p).get("vocals") or str(p.source))
    spans: dict[str, list] = {}
    for s in p.segments:
        if not s.extra.get("song"):
            spans.setdefault(s.speaker or "S1", []).append((s.start, s.end))
    out = {spk: speaker_gender(wav, sp) for spk, sp in spans.items()}
    p.reload()
    p.data["speaker_gender"] = out
    p.save()
    return out


def use_qwen_align(engine: str, settings: dict) -> bool:
    """Căn mốc từng chữ bằng Qwen3-ForcedAligner (lệch ~0,03 s): khi đang nghe bằng Qwen3-ASR hoặc đã tải bộ căn."""
    from . import asr_qwen, models

    if not settings.get("align_words", True) or not asr_qwen.available():
        return False
    return engine == "qwen3" or models.is_ready("qwen_align", settings)


def align_segments(segs, wav: Path, language: str, settings: dict, progress: Prog | None = None, cancel=None,
                   engine: str = ""):
    """Tầng 1 khớp thoại: mốc từng chữ (đo sẵn bởi bộ nhận dạng / Qwen3-ForcedAligner / fa-zh), không đo được thì
    dùng độ to giọng; rồi tách câu trọn nghĩa theo dấu câu cuối câu + chỗ nghỉ thật (align.plan_cuts)."""
    from . import align
    from .asr_funasr import available as fa_available, load_wav16k
    from .progress_hook import tqdm_progress

    max_chars = int(settings.get("asr_max_chars", 28))
    try:
        audio = load_wav16k(wav)
        zh = language in ("zh", "auto", "yue")
        if zh and use_qwen_align(engine, settings):
            from . import asr_qwen

            try:
                asr_qwen.align_qwen(segs, wav, language, settings, _sub(progress, 0.0, 0.7), cancel)
            except ff.Cancelled:
                raise
            except Exception as e:  # noqa: BLE001 – dùng fa-zh / độ to thay thế
                log_error("Căn thời gian Qwen3")
                if progress:
                    progress(0.7, f"Bộ căn thời gian Qwen3 lỗi ({str(e)[:60]}) – dùng cách dự phòng")
        use_fa = zh and fa_available() and bool(settings.get("align_fa", True))
        with tqdm_progress(progress, 0.0, 1.0, "Đang căn thời gian từng chữ", cancel,
                           download="Đang tải mô hình căn thời gian (fa-zh)"):
            return align.refine(segs, audio, use_fa=use_fa, progress=progress, cancel=cancel, max_chars=max_chars,
                                params=align.split_params(settings))
    except ff.Cancelled:
        raise
    except Exception as e:  # noqa: BLE001 – không để bước phụ làm hỏng cả quy trình
        log_error("Căn thời gian")
        if progress:
            progress(1.0, f"Bỏ qua căn thời gian ({str(e)[:80]})")
        return split_lines(segs, max_chars)


def split_lines(segs, max_chars: int = 28):
    """Chia dòng dài theo dấu câu / số chữ (cách cũ, khi không căn được thời gian từng chữ)."""
    from .asr_funasr import split_long
    from .subtitle import Segment

    out = []
    for s in segs:
        parts = split_long(s.start, s.end, s.text, max_chars) if s.text else []
        if len(parts) <= 1:
            out.append(s)
            continue
        out += [Segment(round(a, 3), round(b, 3), t, "", s.speaker, dict(s.extra)) for a, b, t in parts]
    return out


def rescue_segments(segs, wav: Path, engine: str, language: str, settings: dict, keep_speakers: bool = False,
                    progress: Prog | None = None, cancel=None) -> tuple[list, int]:
    """Tìm đoạn có tiếng nói (trên giọng đã tách) mà chưa có câu nào, nghe lại từng đoạn. Trả về (câu, số câu thêm)."""
    from . import align
    from .asr_funasr import load_wav16k

    try:
        if engine == "whisper":
            from .asr import whisper_decoder

            decode = whisper_decoder(settings, language)
        elif engine == "qwen3":
            from .asr_qwen import qwen_decoder

            decode = qwen_decoder(settings, language, Path(wav).parent)
        else:
            from . import models
            from .asr_funasr import sensevoice_decoder

            if engine == "paraformer" and not models.is_ready("sensevoice"):
                return segs, 0
            decode = sensevoice_decoder(settings, language)
        new = align.rescue_missed(segs, load_wav16k(wav), decode, int(settings.get("asr_max_chars", 28)),
                                  progress, cancel)
    except ff.Cancelled:
        raise
    except Exception as e:  # noqa: BLE001 – bước phụ, lỗi thì giữ kết quả nhận dạng
        log_error("Nghe lại đoạn bị sót")
        if progress:
            progress(1.0, f"Bỏ qua nghe lại đoạn bị sót ({str(e)[:80]})")
        return segs, 0
    if keep_speakers and segs:
        for s in new:   # Paraformer đã phân người nói: câu nghe lại lấy người nói của câu gần nhất
            s.speaker = min(segs, key=lambda x: abs((x.start + x.end) / 2 - (s.start + s.end) / 2)).speaker
    if progress:
        progress(1.0, f"Nghe lại: thêm {len(new)} câu bị sót" if new else "Không có câu bị sót")
    kept = [s for s in segs if not s.extra.pop("absorbed", None)]
    return sorted(kept + new, key=lambda s: s.start), len(new)


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
    from .align import drop_char_times

    drop_char_times(segs)   # đo lại từ đầu theo lời hiện tại (người dùng có thể đã sửa chữ)
    old_texts = {s.text for s in segs}
    try:
        segs = align_segments(segs, wav, p.data.get("language", "zh"), settings, _sub(progress, 0.1, 0.9), cancel,
                              engine=p.data.get("asr_engine_used", ""))
    finally:
        from .asr_qwen import release

        release()

    drop_char_times(segs)
    forget_stale_voice(old_texts, segs, moved=True)
    p.segments = segs
    p.save()
    if has_dub(p):   # đã lồng tiếng: xếp lại giọng theo mốc mới ngay (không đọc lại) -> độ vừa, màu tính lại
        relayout(p, settings, _sub(progress, 0.9, 1.0), cancel)
    return len(segs)


def recognize(p: Project, method: str = "asr", language: str = "zh", region: dict | None = None,
              ocr_fps: float = 4.0, settings: dict | None = None, progress: Prog | None = None, cancel=None,
              denoise: bool = False, engine: str | None = None,
              on_draft: Callable[[list[Segment]], None] | None = None) -> int:
    """Lấy phụ đề gốc.  method: asr (giọng nói) | ocr (chữ trên video).
    engine (asr): qwen3 | sensevoice | paraformer | whisper.
    on_draft: chế độ vừa tách vừa dịch – nhận CÂU NHÁP ngay khi có (Qwen3: sau mỗi nhóm khúc vừa nghe; bộ khác: nghe
    xong cả phim, trước các bước căn thời gian / phân biệt người nói)."""

    def draft(new: list[Segment]):
        if on_draft and new:
            try:
                from .align import draft_lines

                on_draft(draft_lines(new, settings, p.data.get("info", {}).get("duration", 0)))
            except ff.Cancelled:
                raise
            except Exception:  # noqa: BLE001 – câu nháp lỗi không được làm hỏng việc nghe
                log_error("Câu nháp vừa tách vừa dịch")
    settings = settings or load_settings()
    src = p.source
    engine = engine or settings.get("asr_engine", "qwen3")
    want_spk = bool(settings.get("diarize", True))
    if method == "ocr":
        from .ocr import detect_subtitle_region, expand_region, extract_hardsubs

        if region:   # vùng khoanh ở tab Xoá phụ đề (vừa khít 1 dòng) -> mở rộng: hết ngang, thêm dòng trên / dưới
            scan = expand_region(region)
        else:
            if progress:
                progress(0.0, "Đang dò vùng phụ đề (kỹ ở 5 giây đầu và 5 giây cuối)…")
            scan = detect_subtitle_region(src, _sub(progress, 0.0, 0.05))
        boxes = p.work / "ocr_boxes.json"
        stats: dict = {}
        segs = extract_hardsubs(src, scan, fps=float(ocr_fps or 4), progress=_sub(progress, 0.05, 0.85),
                                cancel=cancel, boxes_out=boxes, stats=stats)
        if on_draft and segs:
            on_draft([Segment(x.start, x.end, x.text) for x in segs])
        p.data["ocr_region"] = scan
        p.data["ocr_boxes"] = str(boxes)
        p.data["ocr_stats"] = {"fps": float(ocr_fps or 4), "frames": stats.get("frames", 0),
                               "ocr_runs": stats.get("ocr_runs", 0), "first_t": scan.get("first_t")}
        p.data["ocr_warning"] = "" if segs else (
            "Không đọc được câu phụ đề nào – video có thể không có chữ Trung in sẵn, hoặc chữ nằm ngoài vùng quét: "
            "vào tab Xoá phụ đề bấm Tự dò / khoanh lại vùng chữ, hoặc dùng Nhận dạng giọng nói.")
        # gợi ý luôn vùng xoá phụ đề: vừa khít chữ phụ đề đã đọc (không phải cả vùng quét rộng)
        ed = p.edit
        box = stats.get("text_box") or scan.get("erase")
        if not ed["hardsub"].get("regions") and box:
            ed["hardsub"]["regions"] = [box]
            p.data["edit"] = ed
        spk_from_asr = False
    else:
        from . import asr_funasr, separate as sepmod

        info_dur = p.data.get("info", {}).get("duration", 0)
        # 1) tách giọng trước để nhận dạng chính xác hơn khi có nhạc nền (bản tách dùng luôn cho tab Âm thanh / Lồng
        # tiếng). Lỗi / chưa cài -> vẫn nghe trên tiếng gốc nhưng BÁO RÕ (trước đây bỏ qua lặng lẽ -> tab Âm thanh
        # báo "chưa tách" mà người dùng không biết vì sao)
        audio_in: Path = Path(src)
        p.data.pop("sep_warning", None)
        if settings.get("sep_before_asr", True) and not sepmod.available():
            p.data["sep_warning"] = ("Chưa tách được giọng khỏi nhạc nền: chưa cài bộ tách giọng (audio-separator, cài AI "
                                     "nâng cao) – đã nghe trên tiếng gốc")
        elif settings.get("sep_before_asr", True):
            try:
                v, _ = separate_audio(p, settings, _sub(progress, 0.0, 0.3), cancel)
                audio_in = v
            except Exception as e:  # không chặn cả quy trình nếu tách giọng lỗi
                if isinstance(e, ff.Cancelled):
                    raise
                log_error("Tách giọng trước khi nghe")
                p.data["sep_warning"] = (f"Tách giọng khỏi nhạc nền bị lỗi ({err_text(e)[:160]}) – đã nghe trên tiếng "
                                         "gốc. Thử lại ở tab Âm thanh → 'Tách giọng / nhạc nền'")
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
        segs = None
        streamed = False
        p.data.pop("asr_warning", None)
        if engine == "qwen3":
            from . import asr_qwen

            if not asr_qwen.available():
                p.data["asr_warning"] = f"Chưa dùng được Qwen3-ASR ({asr_qwen.why_unavailable()}) – đã dùng SenseVoice."
                engine = "sensevoice"
            else:
                try:
                    segs = asr_qwen.transcribe_qwen(wav, settings, language, pr, cancel,
                                                    on_chunk=draft if on_draft else None)
                    streamed = True
                except ff.Cancelled:
                    asr_qwen.release()
                    raise
                except Exception as e:  # noqa: BLE001 – lỗi tải / thiếu bộ nhớ GPU… -> SenseVoice, không dừng quy trình
                    log_error("Qwen3-ASR")
                    asr_qwen.release()
                    p.data["asr_warning"] = f"Qwen3-ASR lỗi ({str(e)[:160]}) – đã dùng SenseVoice thay thế."
                    if progress:
                        progress(0.33, "Qwen3-ASR lỗi → chuyển sang SenseVoice")
                    engine = "sensevoice"
        if engine in ("sensevoice", "paraformer") and not asr_funasr.available():
            if progress:
                progress(0.33, "Chưa cài FunASR -> dùng Whisper")
            engine = "whisper"
        if engine == "sensevoice" and language not in ("zh", "yue", "auto", "en", "ja", "ko"):
            engine = "whisper"
        spk_from_asr = False
        if engine in ("sensevoice", "paraformer"):
            try:
                if engine == "sensevoice":
                    # có căn thời gian thì để bước căn tách câu (theo mốc từng chữ), không chia theo số chữ trước
                    segs = asr_funasr.transcribe_sensevoice(wav, settings, language, pr, cancel,
                                                            split=not (settings.get("align_words", True)
                                                                       and language in ("zh", "yue", "auto")))
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
        if not streamed:
            draft(segs or [])
        # 2a) nghe lại các đoạn CÓ TIẾNG mà bộ chia đoạn (VAD) bỏ sót -> không còn câu bị bỏ không dịch
        p.data["asr_rescued"] = 0
        if settings.get("asr_rescue", True) and segs is not None and audio_in != Path(src):
            segs, p.data["asr_rescued"] = rescue_segments(segs, wav, engine, language, settings, spk_from_asr,
                                                          _sub(progress, asr_end - 0.03, asr_end), cancel)
        # 2b) căn thời gian từng chữ + tách câu trọn nghĩa (cuối câu / chỗ nghỉ thật, phẩy nói liền không tách)
        try:
            if settings.get("align_words", True) and segs:
                segs = align_segments(segs, wav, language, settings, _sub(progress, asr_end, asr_end + 0.07),
                                      cancel, engine=engine)
        finally:
            from .asr_qwen import release

            release()   # trả bộ nhớ GPU trước bước phân biệt người nói
        # 2c) nối dòng bị cắt ngang câu (cắt giữa từ, mẩu cụt, câu chưa hết) rồi tách lại đúng chỗ – không cần AI
        p.data["asr_joined"] = 0
        if segs and language in ("zh", "yue", "auto"):
            from .align import join_broken, split_params

            segs, p.data["asr_joined"] = join_broken(segs, split_params(settings),
                                                     int(settings.get("asr_max_chars", 28)), info_dur)
        if engine in ("sensevoice", "qwen3"):
            from .asr_funasr import mark_songs

            mark_songs(segs)
        # 2d) đoạn HÁT (nhạc phim, nhân vật hát): cao độ giữ nốt ngân trên giọng đã tách -> 🎵, không lồng tiếng
        p.data["songs_found"] = 0
        if segs and audio_in != Path(src) and settings.get("song_detect", True):
            try:
                from .songs import mark_sung

                p.data["songs_found"] = mark_sung(segs, wav)
            except Exception:  # noqa: BLE001 – bước phụ
                log_error("Đoán đoạn hát")
    segs = normalize(segs)
    one_spk = want_spk and int(settings.get("diarize_speakers", 0) or 0) == 1
    if one_spk:   # đặt "1 người nói": gán hết S1, không đo giọng / không tách câu theo chỗ đổi giọng
        for s in segs:
            s.speaker = "S1"
        p.data["speaker_splits"] = 0
    want_dz = want_spk and not spk_from_asr and not one_spk and bool(segs)
    if not want_dz:
        from .align import drop_char_times

        drop_char_times(segs)
    p.segments = segs
    p.data["sub_source"] = method
    if method == "ocr":
        p.data.pop("asr_warning", None)
        p.data.pop("asr_joined", None)
        p.data.pop("songs_found", None)
    p.save()
    # 3) phân biệt người nói
    if want_dz:
        from . import diarize as dz

        if dz.available():
            try:
                diarize(p, settings, _sub(progress, asr_end + 0.07 if method != "ocr" else 0.85, 1.0), cancel)
            except ff.Cancelled:
                raise
            except Exception as e:
                if progress:
                    progress(1.0, f"Bỏ qua phân biệt người nói ({str(e)[:80]})")
        _drop_char_times(p)
    return len(p.segments)


def _drop_char_times(p: Project) -> None:
    """Mốc từng chữ (extra["ct"]) chỉ dùng trong lúc nhận dạng – không lưu vào dự án (sửa chữ sẽ lệch)."""
    from .align import drop_char_times

    segs = p.segments
    if any("ct" in s.extra or "chunked" in s.extra for s in segs):
        drop_char_times(segs)
        p.segments = segs
        p.save()


# ------------------------------------------------------------------ dịch

def target_lang(p: Project | None, settings: dict | None = None) -> str:
    """Ngôn ngữ đích của dự án ("Dịch sang"): mỗi dự án nhớ riêng, chưa chọn -> theo Cài đặt (mặc định tiếng Việt)."""
    from . import langs

    t = p.data.get("target_lang") if p is not None else None
    if t not in langs.INFO:
        t = (settings if settings is not None else load_settings()).get("target_lang")
    return langs.norm(t)


def trans_lang(p: Project) -> str:
    """Tiếng của bản dịch ĐANG CÓ trong dự án (dự án cũ chưa ghi -> tiếng Việt)."""
    from . import langs

    return langs.norm(p.data.get("trans_lang") or "vi")


def guess_trans_lang(segs, default: str = "vi") -> str | None:
    """Tiếng của các lời dịch (đa số câu) – nhận theo chữ viết; chưa có lời dịch -> None."""
    from . import langs

    count: dict[str, int] = {}
    for s in segs:
        if (s.trans or "").strip():
            lg = langs.detect(s.trans, default)
            count[lg] = count.get(lg, 0) + 1
    return max(count, key=count.get) if count else None


def note_trans_lang(p: Project) -> str | None:
    """Sau khi nạp file phụ đề đã dịch: ghi tiếng của bản dịch; khác tiếng đích đang chọn thì đổi "Dịch sang" theo
    (không thì bấm Dịch sẽ dịch đè mất bản vừa nạp). Trả về tiếng mới nếu đã đổi."""
    p.reload()
    old = target_lang(p)
    lg = guess_trans_lang(p.segments, old)
    if not lg:
        return None
    p.data["trans_lang"] = lg
    p.data["target_lang"] = lg
    p.save()
    return lg if lg != old else None


def glossary_key(target: str) -> str:
    """Bảng thuật ngữ của dự án theo tiếng đích (tên Hán Việt chỉ dùng cho tiếng Việt)."""
    return "glossary" if target == "vi" else f"glossary_{target}"


def source_lang(p: Project) -> str:
    """Tiếng gốc của video cho công cụ dịch (theo tiếng đã chọn khi nghe; mặc định tiếng Trung)."""
    lang = str(p.data.get("language") or "zh")
    return lang if lang in ("zh", "yue", "en", "ja", "ko") else "zh"


def _skill(settings: dict) -> str:
    """Kỹ năng dịch đang chọn (gắn vào lời nhắc AI khi dịch / rút gọn / sửa lỗi nghe / kiểm tra)."""
    from . import skills

    return skills.skill_text(settings)


def ai_engine(settings: dict, engine: str | None = None) -> str:
    """Công cụ dịch AI đang chọn; Google / Hy-MT2 / Không dịch -> lỗi rõ (kỹ năng AI cần AI hội thoại)."""
    from .translate import LOCAL_ENGINES

    eng = engine or settings.get("translate_engine", "google")
    if eng in LOCAL_ENGINES or eng == NO_TRANSLATE:
        raise ValueError("Kỹ năng dịch AI cần công cụ dịch AI (DeepSeek / Claude / GPT / Gemini / Ollama / API tương "
                         "thích OpenAI). Google và Hy-MT2 không làm được – đổi Công cụ dịch ở tab Tự động.")
    return eng


def film_profile(p: Project, settings: dict | None = None, engine: str | None = None, force: bool = False) -> str:
    """Hồ sơ phim (thể loại, nhân vật, xưng hô, tên riêng / meme) – AI lập 1 lần, lưu trong dự án, sửa được; mọi lần dịch
    / kiểm tra sau dùng lại. Thuật ngữ tìm được thêm vào bảng tên riêng của dự án (không đè chỗ bạn đã sửa)."""
    from . import langs, skills
    from .translate import LLMClient, glossary_text, parse_glossary

    settings = settings or load_settings()
    if p.data.get("film_profile", "").strip() and not force:
        return p.data["film_profile"]
    eng = ai_engine(settings, engine)
    texts = [x.text for x in p.segments if x.text.strip()]
    if not texts:
        raise ValueError("Chưa có phụ đề gốc để AI đọc.")
    target = target_lang(p, settings)
    text, terms = skills.make_profile(LLMClient(settings, eng), texts, langs.INFO[target]["name"].lower(),
                                      skills.skill_text(settings))
    gkey = glossary_key(target)
    g = parse_glossary(p.data.get(gkey, ""))
    for k, v in terms.items():
        g.setdefault(k, v)
    p.data[gkey] = glossary_text(g)
    p.data["film_profile"] = text
    p.save()
    return text


def ai_cleanup(p: Project, settings: dict | None = None, engine: str | None = None, progress: Prog | None = None,
               cancel=None) -> dict:
    """Kỹ năng 1: AI sửa chữ nghe nhầm + nối câu bị cắt sai chỗ (giữ bản cũ để Hoàn tác). Trả về thống kê của
    `skills.apply_cleanup` (changed / unsure = số thứ tự câu sau khi sửa)."""
    from . import skills
    from .translate import LLMClient

    settings = settings or load_settings()
    eng = ai_engine(settings, engine)
    segs = p.segments
    if not segs:
        raise ValueError("Chưa có phụ đề gốc để sửa.")
    res = skills.clean_lines(LLMClient(settings, eng), segs, p.data.get("film_profile", ""),
                             skills.skill_text(settings), progress, cancel)
    new, stats = skills.apply_cleanup(segs, res)
    if stats["changed"]:
        p.data["cleanup_backup"] = [x.to_dict() for x in segs]
        lost = forget_stale_voice({x.text for x in segs}, new, moved=len(new) != len(segs))
        p.segments = new
        p.save()
        if lost and has_dub(p):   # câu đổi lời / gộp: bỏ giọng cũ khỏi bản lồng (không đọc lại câu nào)
            relayout(p, settings)
    p.data["asr_cleaned"] = True
    p.save()
    return stats


def join_lines(p: Project, settings: dict | None = None) -> list[int]:
    """Nút "Nối câu bị cắt" (tab Phụ đề): nối các dòng bị cắt ngang câu rồi tách lại đúng chỗ, không cần AI, không
    nghe lại. Giữ bản cũ để Hoàn tác; câu đổi lời mất bản dịch + giọng lồng (bản lồng tự xếp lại). Trả về số thứ tự các
    câu mới (để tích sẵn)."""
    from . import align

    settings = settings or load_settings()
    segs = p.segments
    new, made = align.join_broken(segs, align.split_params(settings), int(settings.get("asr_max_chars", 28)),
                                  float(p.data.get("info", {}).get("duration", 0) or 0))
    if not made:
        return []
    old_ids = {id(s) for s in segs}
    p.data["join_backup"] = [s.to_dict() for s in segs]
    lost = forget_stale_voice({s.text for s in segs}, new, moved=False)
    p.segments = new
    p.save()
    if lost and has_dub(p):   # câu nối: bỏ giọng cũ khỏi bản lồng (không đọc lại câu nào)
        relayout(p, settings)
    return [k for k, s in enumerate(new) if id(s) not in old_ids]


def undo_join(p: Project, settings: dict | None = None) -> int:
    """Hoàn tác lần "Nối câu bị cắt" gần nhất. Trả về số câu sau khi hoàn tác (0 = không có gì để hoàn tác)."""
    from .subtitle import Segment

    back = p.data.pop("join_backup", None)
    if not back:
        return 0
    p.segments = [Segment.from_dict(d) for d in back]
    p.save()
    if has_dub(p):
        relayout(p, settings or load_settings())
    return len(back)


def undo_cleanup(p: Project, settings: dict | None = None) -> int:
    """Hoàn tác lần AI sửa lỗi nghe & nối câu gần nhất. Trả về số câu sau khi hoàn tác (0 = không có gì để hoàn tác)."""
    from .subtitle import Segment

    back = p.data.pop("cleanup_backup", None)
    if not back:
        return 0
    p.segments = [Segment.from_dict(d) for d in back]
    p.data["asr_cleaned"] = False
    p.save()
    if has_dub(p):
        relayout(p, settings or load_settings())
    return len(back)


def ai_review(p: Project, settings: dict | None = None, engine: str | None = None, progress: Prog | None = None,
              cancel=None, rows: list[int] | None = None) -> list[int]:
    """Kỹ năng 3: AI kiểm tra bản dịch (sai nghĩa, xưng hô / tên không thống nhất, quá dài, còn chữ Hán…) và sửa. Câu bạn
    sửa tay không đụng tới. Bản cũ giữ ở extra["trans_before_review"], lý do ở extra["review_note"]. Trả về câu đã sửa."""
    from . import langs, skills
    from .translate import LLMClient, budget_for

    settings = settings or load_settings()
    eng = ai_engine(settings, engine)
    segs = p.segments
    target = target_lang(p, settings)
    L = langs.INFO[target]
    items = [(i, x.text, x.trans, budget_for(x.duration, 1.1, target)) for i, x in enumerate(segs)
             if x.trans.strip() and x.text.strip() and not x.extra.get("trans_user") and (rows is None or i in rows)]
    if not items:
        return []
    src = {"zh": "tiếng Trung", "yue": "tiếng Quảng Đông"}.get(source_lang(p), "tiếng gốc")
    fixes = skills.review_lines(LLMClient(settings, eng), items, p.data.get("film_profile", ""),
                                skills.skill_text(settings), src, L["name"].lower(), L["unit_vi"], target,
                                progress, cancel)
    for i, (new, why) in fixes.items():
        segs[i].extra["trans_before_review"] = segs[i].trans
        segs[i].extra["review_note"] = why
        segs[i].trans = new
    if fixes:
        p.segments = segs
        p.save()
    return sorted(fixes)


def translate(p: Project, engine: str | None = None, style: str | None = None, only_empty: bool = False,
              settings: dict | None = None, progress: Prog | None = None, cancel=None,
              rows: list[int] | None = None) -> int:
    """Dịch phụ đề. rows: chỉ các câu này (dịch LẠI: bỏ bản dịch nhớ sẵn của chúng để ra bản mới)."""
    from .translate import BUDGET_RE, Translator, cache_line, glossary_text, parse_glossary, untranslated

    settings = settings or load_settings()
    target = target_lang(p, settings)
    segs = p.segments
    if not segs:
        raise ValueError("Chưa có phụ đề gốc. Hãy nhận dạng giọng nói/OCR hoặc nhập file phụ đề trước.")
    if (engine or settings.get("translate_engine")) == NO_TRANSLATE:
        raise ValueError("Đang chọn 'Không dịch'. Chọn công cụ dịch ở tab Tự động, hoặc nạp file SRT đã dịch "
                         "ở tab Lồng tiếng / Phụ đề.")
    cache = p.data.setdefault("cache", {})
    if rows is not None and not only_empty:   # dịch lại câu chọn: không lấy lại bản dịch cũ đã nhớ
        texts = {segs[i].text for i in rows if 0 <= i < len(segs)}
        for key in list(cache):
            if BUDGET_RE.sub("", cache_line(key)).strip() in texts:
                del cache[key]
    gkey = glossary_key(target)
    old_lang = trans_lang(p)
    if old_lang != target and rows is None:
        # đổi tiếng đích: bản dịch cũ (tiếng khác) không dùng được -> "chỉ dịch dòng trống" cũng dịch lại mọi câu
        only_empty = False
    from . import skills
    from .translate import LOCAL_ENGINES

    eng = engine or settings.get("translate_engine", "google")
    is_ai = eng not in LOCAL_ENGINES
    whole = rows is None
    # Kỹ năng dịch AI: 1) sửa lỗi nghe & nối câu (chỉ khi dịch cả phim lần đầu – chưa câu nào có bản dịch), 2) hồ sơ
    # phim (lập 1 lần, dùng lại), 3) kiểm tra bản dịch sau khi dịch cả phim. Kỹ năng riêng gắn vào mọi lời nhắc.
    do_clean = (is_ai and whole and settings.get("ai_clean_before_translate", True) and not p.data.get("asr_cleaned")
                and not any(x.trans.strip() for x in segs))
    do_review = is_ai and whole and bool(settings.get("ai_review_after_translate", False))
    a0 = 0.0
    p.data.pop("skill_warning", None)
    if whole:
        p.data.pop("skill_note", None)
    if do_clean:
        try:
            st = ai_cleanup(p, settings, eng, _sub(progress, 0.0, 0.18), cancel)
            segs = p.segments
            if st["changed"]:
                p.data["skill_note"] = (f"AI sửa lỗi nghe {st['fixed']} câu, nối {st['merged']} câu bị cắt"
                                        + (f" ({len(st['unsure'])} chỗ chưa chắc – xem cột gốc)" if st["unsure"] else ""))
        except (ff.Cancelled, net.NetworkDown):
            raise
        except Exception as e:  # noqa: BLE001 – không chặn việc dịch
            log_error("AI sửa lỗi nghe & nối câu")
            p.data["skill_warning"] = f"Bỏ qua bước AI sửa lỗi nghe ({err_text(e)[:120]})"
        a0 = 0.18
    profile = p.data.get("film_profile", "") or p.data.get("summary", "")
    if is_ai and not profile.strip():
        if progress:
            progress(a0, "AI đang đọc kịch bản, lập hồ sơ phim (nhân vật, xưng hô, tên riêng)…")
        try:
            profile = film_profile(p, settings, eng)
        except (ff.Cancelled, net.NetworkDown):
            raise
        except Exception:  # noqa: BLE001 – không lập được thì dịch như cũ (AI tự đọc kịch bản)
            log_error("Lập hồ sơ phim")
            profile = ""
        a0 += 0.05
    tr = Translator(settings, engine, style, parse_glossary(p.data.get(gkey, "")), cache, target=target,
                    source=source_lang(p), skill=skills.skill_text(settings) if is_ai else "")

    last_save = [time.time()]
    tr_prog = _sub(progress, a0, 0.85 if do_review else 1.0)

    def prog(x, m):
        tr_prog(x, m)
        if time.time() - last_save[0] > 20:  # lưu định kỳ để không mất công khi video dài
            p.save()
            last_save[0] = time.time()

    tr.translate_segments(segs, prog, cancel, only_empty=only_empty, summary=profile, auto_terms=not profile,
                          rows=set(rows) if rows is not None else None)
    # AI nhận ra lời bài hát (bản dịch có ♪) -> đánh dấu 🎵: không lồng tiếng, giữ giọng hát gốc
    n_song = mark_song_lines(segs, rows)
    if n_song:
        p.data["skill_note"] = ((p.data.get("skill_note", "") + " · ") if p.data.get("skill_note") else "") + \
            f"{n_song} câu là lời bài hát (♪) – đã đánh dấu 🎵, không lồng tiếng"
    # ghi lại thuật ngữ AI tìm được để người dùng xem/sửa
    user_g = parse_glossary(settings.get("glossary", "")) if target == "vi" else {}
    proj_g = parse_glossary(p.data.get(gkey, ""))
    for k, v in tr.glossary.items():
        if k not in user_g:
            proj_g.setdefault(k, v)
    p.data[gkey] = glossary_text(proj_g)
    p.data["target_lang"] = target
    if rows is None or old_lang == target:
        p.data["trans_lang"] = target

    left = sum(1 for s in segs if s.text.strip() and untranslated(s.trans, target))
    warn = []
    if tr.fallback_lines:
        warn.append(f"{tr.fallback_lines} câu AI trả lời lỗi định dạng nên đã dịch bằng Google – nên soát lại các câu này")
    if left:
        warn.append(f"{left} câu chưa dịch được (mạng/API lỗi) – bấm 'Dịch dòng trống' ở tab Phụ đề để dịch nốt")
    p.data["translate_warning"] = ". ".join(warn)
    p.segments = segs
    p.save()
    if do_review:
        try:
            fixed = ai_review(p, settings, eng, _sub(progress, 0.85, 1.0), cancel)
            if fixed:
                p.data["skill_note"] = ((p.data.get("skill_note", "") + " · ") if p.data.get("skill_note") else "") + \
                    f"AI kiểm tra và sửa {len(fixed)} câu dịch"
                p.save()
        except (ff.Cancelled, net.NetworkDown):
            raise
        except Exception as e:  # noqa: BLE001
            log_error("AI kiểm tra bản dịch")
            p.data["skill_warning"] = f"Bỏ qua bước AI kiểm tra bản dịch ({err_text(e)[:120]})"
            p.save()
    return sum(1 for s in p.segments if s.trans)


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


def dub_default_voice(voice: str | None, engine: str, settings: dict) -> str:
    """Giọng mặc định của bộ đọc (người nói chưa chọn giọng riêng dùng giọng này)."""
    if voice:
        return voice
    if engine == "omnivoice":
        from . import voices as vlib
        from .tts import OMNI_CLONE

        smp = vlib.list_samples()
        return f"omni:{smp[0]['id']}" if smp else OMNI_CLONE
    return f"edge:{settings.get('tts_voice')}" if engine == "edge" else "vieneu:"


def dub(p: Project, voice: str | None = None, rate: str | None = None, settings: dict | None = None,
        progress: Prog | None = None, cancel=None, engine: str | None = None, inplace: bool = False,
        only: list[int] | None = None) -> Path:
    """Lồng tiếng Việt. Mỗi người nói có thể có giọng riêng (p.data['voices'][speaker]), mỗi câu cũng có thể có giọng
    riêng (seg.extra['voice']): 'edge:<voice id>' | 'vieneu:<preset>' | 'clone' | 'omni:<id>' | 'omniclone'
    | 'omnid:<mã giọng chọn sẵn>'.
    Câu đã đọc được lưu đệm theo (lời, giọng, tốc độ, lần đọc) -> chỉ câu mới / đã sửa mới phải đọc lại.
    inplace: ghi đè bản lồng tiếng đang dùng (sửa vài câu) thay vì thêm bản mới; only: chỉ AI rút gọn các câu này."""
    from .tts import synthesize_dub

    settings = settings or load_settings()
    segs = p.segments
    if not any(s.trans for s in segs):
        raise ValueError("Chưa có bản dịch để lồng tiếng.")
    engine = engine or settings.get("tts_engine", "edge")
    default_voice = dub_default_voice(voice, engine, settings)
    voices = dict(p.data.get("voices") or {})
    auto_voiced = 0
    if not inplace and only is None and not voice:
        # nhiều người nói mà chưa ai được chọn giọng -> trước đây cả phim đọc 1 giọng mặc định; giờ tự gán mỗi người 1
        # giọng (nam / nữ theo chất giọng) và lưu lại để mục 2 hiện đúng giọng đã dùng
        spks = [x["speaker"] for x in speaker_list(p)]
        if len(spks) > 1 and not any(voices.get(k) for k in spks):
            from . import tts

            genders = p.data.get("speaker_gender") or {}
            if not all(k in genders for k in spks) and p.data.get("source"):
                try:
                    genders = speaker_genders(p)
                except Exception:  # noqa: BLE001 – không đoán được thì xen kẽ nam / nữ
                    log_error("Đoán giọng nam / nữ trước khi lồng tiếng")
            got = tts.auto_assign(spks, dict(settings, tts_engine=engine), {}, genders, trans_lang(p))
            if len({v for v in got.values() if v}) > 1:
                voices = got
                p.data["voices"] = got
                auto_voiced = len(spks)
    dropped = 0
    if not inplace and only is None and trans_lang(p) == "vi" and settings.get("dub_drop_interj", True):
        dropped = drop_interjections(p, segs)   # câu chỉ có "A!", "Ừm.", "Ái!"… -> xoá khỏi phụ đề trước khi đọc
    st = stems(p)
    # mẫu giọng để nhái: lấy từ giọng đã tách (sạch) hoặc âm thanh gốc
    ref_audio = st.get("vocals") or str(p.source)
    (p.work / "dubs").mkdir(parents=True, exist_ok=True)
    out = p.work / "dubs" / f"dub_{time.strftime('%Y%m%d_%H%M%S')}.wav"
    cur = (p.edit.get("dub") or {}).get("wav")
    target = Path(cur) if inplace and cur and Path(cur).exists() else out
    tmp_out = target.with_name(target.stem + "_new.wav")
    stats: dict = {}

    def run(pr):
        stats.clear()
        synthesize_dub(segs, tmp_out, p.data.get("info", {}).get("duration", 0), p.work / "tts",
                       default_voice, rate or settings.get("tts_rate", "+0%"),
                       max_speedup=float(settings.get("tts_max_speedup", 1.3)), progress=pr, cancel=cancel,
                       voices=voices, ref_audio=ref_audio, settings=settings,
                       skip_songs=bool(settings.get("dub_skip_songs", True)), stats=stats,
                       only=set(only) if only is not None else None, lang=trans_lang(p),
                       genders=p.data.get("speaker_gender") or {})

    run(_sub(progress, 0.0, 0.85))
    # tầng 3b: câu vẫn quá dài -> AI rút gọn bản dịch rồi đọc lại (chỉ những câu đó, câu khác dùng lại bản đã đọc)
    # câu người dùng tự sửa không bị AI đổi; lồng lại vài câu thì chỉ rút gọn các câu đó
    long_ = [x for x in (stats.get("too_long") or []) if not segs[x[0]].extra.get("trans_user")
             and (only is None or x[0] in only)]
    shortened = 0
    if long_ and settings.get("dub_auto_shorten", True) and \
            settings.get("translate_engine", "google") not in ("google", "hymt", NO_TRANSLATE):
        from .translate import Translator, budget_for, syllables

        if progress:
            progress(0.86, f"AI đang rút gọn {len(long_)} câu đọc quá dài so với thời gian nhân vật nói…")
        try:
            lg = trans_lang(p)
            tr = Translator(settings, target=lg, source=source_lang(p), skill=_skill(settings))
            items = []
            for k, need, have in long_:
                cur = segs[k].trans
                n = min(syllables(cur, lg) - 1,
                        max(2, int(syllables(cur, lg) * max(0.4, have / max(need, 0.1)) * 0.95)))
                items.append((segs[k].text, cur, max(2, min(n, budget_for(have, 1.25, lg)))))
            new = tr.shorten(items)
            for (k, _, _), t in zip(long_, new):
                if t != segs[k].trans:
                    segs[k].extra.setdefault("trans_long", segs[k].trans)   # giữ bản dài để người dùng xem lại
                    segs[k].trans = t
                    shortened += 1
        except ff.Cancelled:
            raise
        except Exception as e:  # noqa: BLE001 – lỗi AI (cả mất mạng) không làm hỏng bản lồng
            if progress:
                progress(0.86, f"Không rút gọn được ({str(e)[:80]})")
        if shortened:
            run(_sub(progress, 0.87, 1.0))
    stats["shortened"] = shortened
    stats["interj"] = dropped
    stats["auto_voiced"] = auto_voiced
    try:
        tmp_out.replace(target)
        out = target
    except OSError:   # Windows: file đang được chương trình khác mở -> lưu thành bản mới
        tmp_out.replace(out)
    env = stats.pop("wave", None)
    if env is not None:
        import numpy as np

        np.save(dub_env_path(out), env)   # sóng âm từng đoạn giọng lồng cho timeline
    p.segments = segs            # lưu độ vừa (fit) + bản dịch đã rút gọn
    layout = stats.pop("layout", [])
    p.data["dub_layout"] = layout
    p.data["dub_stats"] = stats
    vers = dub_versions(p)
    entry = {"wav": str(out), "time": time.strftime("%H:%M %d/%m"), "voices": _voice_summary(voices, default_voice),
             "stats": stats, "layout": layout}
    old_i = next((i for i, v in enumerate(vers) if v["wav"] == str(out)), None)
    if old_i is None:
        entry["created"] = entry["time"]
        vers.insert(0, entry)
    else:   # sửa thẳng vào bản đang dùng ("Tạo lại giọng các câu đã tích") -> đếm số lần sửa
        prev = vers[old_i]
        entry["created"] = prev.get("created") or prev.get("time", "")
        entry["edits"] = int(prev.get("edits", 0)) + 1
        vers[old_i] = entry
    p.data["dub_versions"] = vers
    ed = p.edit
    was_on = bool(ed["dub"].get("enabled"))
    ed["dub"]["enabled"] = True
    ed["dub"]["wav"] = str(out)
    if ed.get("audio") and not was_on and not st and float(ed["audio"].get("orig", 1.0)) > 0.5:
        ed["audio"]["orig"] = 0.15     # mới bật lồng tiếng, chưa tách giọng: tiếng gốc nhỏ lại cho nghe rõ lời Việt
    if st:  # có nhạc nền đã tách -> dùng làm nền cho bản lồng tiếng (sạch giọng gốc)
        ed["dub"]["bg_wav"] = st["instrumental"]
        ed["dub"]["vocals_wav"] = st["vocals"]
        ed["dub"]["song_spans"] = [[s.start, s.end] for s in segs if s.extra.get("song")] \
            if settings.get("dub_skip_songs", True) else []
    p.data["edit"] = ed
    p.save()
    return out


def drop_interjections(p: Project, segs: list | None = None) -> int:
    """Xoá khỏi phụ đề các câu CHỈ có 1–2 tiếng cảm thán (A!, Ừm., Ái!, Hả?…) trước khi lồng tiếng Việt – câu
    "Ừm, đúng là như vậy." giữ nguyên. Câu bị xoá cất ở p.data["interj_removed"] (khôi phục được); câu đã khôi phục
    (extra["keep_interj"]) không bị xoá lại. Sửa `segs` tại chỗ (hoặc p.segments) và trả về số câu đã xoá."""
    from .voicefix import lone_interjection

    own = segs is None
    segs = p.segments if own else segs
    keep, gone = [], []
    for s in segs:
        if s.trans.strip() and not s.extra.get("keep_interj") and lone_interjection(s.trans):
            gone.append(s)
        else:
            keep.append(s)
    if gone:
        p.data.setdefault("interj_removed", []).extend(s.to_dict() for s in gone)
        segs[:] = keep
        if own:
            p.segments = segs
    return len(gone)


def restore_interjections(p: Project) -> int:
    """Đưa lại các câu cảm thán đã bị xoá vào đúng chỗ theo thời gian (đánh dấu để lần lồng sau không xoá nữa)."""
    from .subtitle import Segment

    back = [Segment.from_dict(d) for d in p.data.pop("interj_removed", []) or []]
    if not back:
        return 0
    for s in back:
        s.extra["keep_interj"] = True
    segs = sorted(p.segments + back, key=lambda s: (s.start, s.end))
    p.segments = segs
    p.save()
    return len(back)


def detect_songs(p: Project, settings: dict | None = None, progress: Prog | None = None, cancel=None) -> int:
    """Tìm các cụm câu đang HÁT trên giọng đã tách (dự án đã nghe từ trước) -> đánh dấu 🎵. Cần đã tách giọng."""
    st = stems(p)
    if not st:
        raise ValueError("Chưa tách giọng / nhạc nền – bấm 'Tách ngay' ở mục 1 tab Lồng tiếng trước (đoán đoạn hát đo "
                         "trên giọng đã tách khỏi nhạc)")
    wav = p.work / "asr16k_vocals.wav"
    if not wav.exists() or wav.stat().st_size < 1000 or wav.stat().st_mtime < Path(st["vocals"]).stat().st_mtime:
        ff.run(["-i", st["vocals"], "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(wav)],
               p.data.get("info", {}).get("duration", 0), progress, cancel, "Đang chuẩn bị giọng đã tách")
    from .songs import mark_sung

    segs = p.segments
    if progress:
        progress(0.6, "Đang nghe cao độ từng câu để tìm đoạn hát…")
    n = mark_sung(segs, wav)
    if n:
        p.segments = segs
        p.save()
    return n


def undubbed_rows(segs: list, skip_songs: bool = True) -> list[int]:
    """Câu đã có lời dịch mà chưa có giọng trong bản lồng đang dùng (bị sót / đọc lỗi / câu mới thêm) – không tính câu
    bài hát 🎵 (khi giữ nguyên bài hát) và câu 'Bỏ lồng'."""
    from . import langs
    from .tts import clean_tts_text

    out = []
    for i, s in enumerate(segs):
        t = (s.trans or "").strip()
        if not t or not clean_tts_text(t, langs.detect(t, "vi")):
            continue
        if s.extra.get("nodub") or (skip_songs and s.extra.get("song")):
            continue
        if not s.extra.get("dub_at"):
            out.append(i)
    return out


def relayout(p: Project, settings: dict | None = None, progress: Prog | None = None, cancel=None) -> Path:
    """Xếp lại các đoạn giọng lồng đã có theo vị trí mới (người dùng kéo đoạn giọng trên timeline) – không đọc lại câu
    nào, chỉ ghép lại bản lồng đang dùng (vài giây)."""
    if not any(s.extra.get("tts_file") for s in p.segments):
        raise ValueError("Chưa có giọng lồng – bấm Tạo bản lồng tiếng trước.")
    return dub(p, None, None, settings, progress, cancel, inplace=True, only=[])


def dub_env_path(wav: str | Path) -> Path:
    return Path(wav).with_suffix(".env.npy")


def dub_wave(p: Project) -> tuple[str, float] | None:
    """(file sóng âm, bước giây) của bản lồng đang dùng – bản cũ chưa có thì tạo (chạy ở luồng nền)."""
    wav = (p.edit.get("dub") or {}).get("wav")
    if not wav or not Path(wav).exists():
        return None
    env = dub_env_path(wav)
    if not env.exists() or env.stat().st_mtime < Path(wav).stat().st_mtime:
        import wave as _wave

        import numpy as np

        from .tts import wave_envelope

        with _wave.open(str(wav)) as w:
            sr = w.getframerate()
            a = np.frombuffer(w.readframes(w.getnframes()), np.int16).astype(np.float32) / 32768.0
            if w.getnchannels() > 1:
                a = a.reshape(-1, w.getnchannels()).mean(axis=1)
        np.save(env, wave_envelope(a, sr))
    return str(env), 0.05



def dub_clips(p: Project, segs: list | None = None) -> list[list]:
    """[[bắt đầu, kết thúc, câu, độ vừa]] các đoạn giọng lồng đang dùng – lấy từ chính từng câu (extra["dub_at"]) nên
    vẫn đúng khi thêm / xoá câu sau khi lồng; dự án cũ (chưa có dub_at) dùng p.data["dub_layout"]."""
    segs = p.segments if segs is None else segs
    out = [[float(s.extra["dub_at"][0]), float(s.extra["dub_at"][1]), k, s.extra.get("fit") or "ok"]
           for k, s in enumerate(segs) if s.extra.get("dub_at")]
    return out or list(p.data.get("dub_layout") or [])


def dub_audible(p: Project, edit: dict | None = None) -> bool:
    """Bản lồng tiếng đang được nghe (bật, không tắt lớp, âm lượng > 0)."""
    from .audiomix import audio_settings

    ed = edit or p.edit
    d = ed.get("dub") or {}
    a = audio_settings(ed)
    return bool(d.get("enabled") and d.get("wav")) and "dub" not in a["mute"] and float(a.get("dub", 1.0)) > 0


def subs_follow_dub(p: Project, segs: list, edit: dict | None = None, settings: dict | None = None,
                    which: str = "trans") -> list:
    """Phụ đề Việt chạy theo giọng lồng (cài đặt dub_subs_follow, mặc định bật) khi đang nghe bản lồng tiếng."""
    from .subtitle import follow_dub

    settings = settings if settings is not None else load_settings()
    if which == "source" or not settings.get("dub_subs_follow", True) or not dub_audible(p, edit):
        return segs
    return follow_dub(segs)


def karaoke_spans(p: Project, edit: dict | None = None) -> dict[int, list]:
    """{số câu: [[giây bắt đầu, giây kết thúc] các đoạn có tiếng]} của giọng lồng từng câu – karaoke chạy theo giọng
    đọc. Không dùng bản lồng tiếng (tắt / tắt lớp / âm lượng 0) -> {} (karaoke theo thời gian hiện câu)."""
    from .audiomix import audio_settings
    from .karaoke import voiced

    ed = edit or p.edit
    d = ed.get("dub") or {}
    a = audio_settings(ed)
    if not d.get("enabled") or "dub" in a["mute"] or float(a.get("dub", 1.0)) <= 0:
        return {}
    w = dub_wave(p)
    layout = p.data.get("dub_layout") or []
    if not w or not layout:
        return {}
    import numpy as np

    try:
        env = np.load(w[0]).astype(np.float32)
    except (OSError, ValueError):
        return {}
    segs = p.segments
    out: dict[int, list] = {}
    for item in layout:
        a0, a1, k = float(item[0]), float(item[1]), int(item[2])
        if not 0 <= k < len(segs):
            continue
        at = segs[k].extra.get("dub_at")
        if at and abs(float(at[0]) - a0) < 0.05:
            pass            # đúng đoạn giọng của câu này (kể cả khi bị lùi / đọc sớm nhiều)
        elif a1 <= segs[k].start - 1.0 or a0 >= segs[k].end + 1.0:
            continue        # câu đã sửa / thêm bớt sau khi lồng tiếng
        sp = voiced(env, w[1], a0, a1)
        if sp:
            out[k] = sp
    return out

def redub(p: Project, rows: list[int], rate: str | None = None, settings: dict | None = None,
          progress: Prog | None = None, cancel=None, engine: str | None = None, new_take: bool = True) -> Path:
    """Đọc lại riêng các câu đã chọn (lần đọc mới, theo bản dịch + giọng hiện tại của câu) và cập nhật NGAY bản lồng
    tiếng đang dùng – các câu khác dùng lại giọng đã đọc, trình phát nghe được ngay khi xong."""
    segs = p.segments
    rows = sorted({k for k in rows if 0 <= k < len(segs)})
    if not rows:
        raise ValueError("Chưa tích chọn câu nào để tạo lại giọng.")
    if new_take:
        for k in rows:
            segs[k].extra["take"] = int(segs[k].extra.get("take", 0) or 0) + 1
    p.segments = segs
    p.save()
    return dub(p, None, rate, settings, progress, cancel, engine, inplace=True, only=rows)


SHORTEN_FROM = 1.15    # câu ⚡ phải đọc nhanh hơn ×1,15 mới cần rút gọn (nhanh hơn nữa nghe vội)
SHORTEN_SPEED = 1.1    # bản rút gọn nhắm đọc vừa chỗ ở tốc độ ≤ ×1,1


def shorten_target(s, slot: float, lang: str = "vi") -> int | None:
    """Số tiếng (âm tiết) tối đa nên rút gọn còn; None = KHÔNG cần rút gọn.
    Đã lồng tiếng (theo đúng màu cột Độ vừa): ✓ xanh (kể cả câu đọc sớm mà vừa) và ⚡ đọc nhanh ≤ ×1,15 -> giữ nguyên;
    ⚠ đỏ / ⚡ nhanh hơn ×1,15 -> rút gọn, TRỪ KHI lời của chính câu vừa chỗ của nó (giọng ở ×1 `fit_need` so với chỗ
    trống từ lúc nhân vật nói tới câu sau `fit_have`) – tức câu chỉ bị câu trước đẩy trễ. Tốc độ người dùng tự đặt cho
    câu tính là chấp nhận được. Chưa lồng: ước theo số tiếng so với chỗ trống tới câu sau."""
    from .translate import budget_for, syllables

    syl = syllables(s.trans, lang)
    ex = s.extra
    fit = ex.get("fit")
    if fit:
        sp = float(ex.get("fit_speed", 1) or 1)
        if fit == "ok" or (fit == "fast" and sp <= SHORTEN_FROM):
            return None
        need, have = float(ex.get("fit_need") or 0), float(ex.get("fit_have") or 0)
        if need > 0 and have > 0:
            if need / have <= max(SHORTEN_FROM, float(ex.get("speed") or 0)):
                return None      # lời vừa chỗ của nó – chỉ bị câu trước đẩy trễ
            ratio = have * SHORTEN_SPEED / need
        elif fit == "fast":
            ratio = SHORTEN_SPEED / sp
        else:
            ratio = budget_for(slot, SHORTEN_SPEED, lang) / max(1, syl)
    else:
        if syl <= budget_for(slot, SHORTEN_SPEED, lang):
            return None
        ratio = budget_for(slot, SHORTEN_SPEED, lang) / max(1, syl)
    return max(2, min(syl - 1, int(syl * min(1.0, ratio) * 0.95)))


def needs_shorten(s, lang: str = "vi") -> bool:
    """Câu đã lồng tiếng có cần AI rút gọn không (nút "Tích câu ⚠ quá dài")."""
    return bool(s.extra.get("fit")) and bool((s.trans or "").strip()) and \
        shorten_target(s, s.duration, lang) is not None


def shorten_lines(p: Project, rows: list[int], settings: dict | None = None) -> dict:
    """AI viết lại NGẮN HƠN bản dịch của các câu QUÁ DÀI trong các câu đã chọn (câu đã vừa giữ nguyên), giữ bản cũ ở
    trans_long. Trả về {"changed": câu đã rút gọn, "fit": câu đã vừa (bỏ qua), "same": câu AI không rút ngắn được}.
    Cần công cụ dịch AI (không dùng được Google / Hy-MT2)."""
    from .translate import Translator

    settings = settings or load_settings()
    if settings.get("translate_engine", "google") in ("google", "hymt", NO_TRANSLATE):
        raise ValueError("Rút gọn câu cần công cụ dịch AI hội thoại (DeepSeek, Claude, GPT…) – Google và Hy-MT2 chỉ "
                         "dịch được, không viết lại câu. Chọn ở Cài đặt → Công cụ dịch.")
    segs = p.segments
    lg = trans_lang(p)
    total = float(p.data.get("info", {}).get("duration", 0) or 0)
    starts = sorted(x.start for x in segs)
    items, fits = [], []
    for k in sorted(set(rows)):
        if not (0 <= k < len(segs)) or not segs[k].trans.strip():
            continue
        s = segs[k]
        nxt = next((t for t in starts if t > s.start + 0.01), max(total, s.end))
        n = shorten_target(s, max(s.duration, nxt - s.start), lg)
        if n is None:
            fits.append(k)
        else:
            items.append((k, (s.text, s.trans, n)))
    res = {"changed": [], "fit": fits, "same": []}
    if not items:
        return res
    new = Translator(settings, target=lg, source=source_lang(p), skill=_skill(settings)).shorten([x for _, x in items])
    for (k, _), t in zip(items, new):
        if t and t != segs[k].trans:
            segs[k].extra.setdefault("trans_long", segs[k].trans)
            segs[k].trans = t
            segs[k].extra.pop("fit", None)
            res["changed"].append(k)
        else:
            res["same"].append(k)
    p.segments = segs
    p.save()
    return res


def unshorten_lines(p: Project, rows: list[int]) -> list[int]:
    """Bỏ bản AI rút gọn: các câu đã chọn quay về lời dịch trước khi rút gọn. Trả về các câu đã đổi lại."""
    segs = p.segments
    back = []
    for k in sorted(set(rows)):
        if 0 <= k < len(segs) and segs[k].extra.get("trans_long"):
            segs[k].trans = segs[k].extra.pop("trans_long")
            segs[k].extra.pop("fit", None)
            segs[k].extra["trans_user"] = True   # người dùng chọn giữ lời dài: "AI tự rút gọn" khi lồng không đụng vào
            back.append(k)
    if back:
        p.segments = segs
        p.save()
    return back


def apply_translation(segs: list, cues: list, stats: dict | None = None) -> list[int]:
    """Nạp bản dịch từ file SRT (đã dịch ở phần mềm khác) vào các câu hiện có – giữ câu gốc, mốc, người nói.
    Cùng số câu và mốc gần khớp -> ghép theo thứ tự. Khác số câu -> ghép theo thời gian:
      * file dịch GỘP câu (1 câu dịch phủ 2–3 câu gốc liền nhau) -> gộp các câu gốc đó thành 1 câu mang bản dịch đó
        (trước đây câu gốc thứ 2 bị bỏ trống);
      * file dịch TÁCH câu (2 câu dịch nằm trong 1 câu gốc) -> nối các bản dịch vào câu gốc đó;
      * câu dịch không trùng câu gốc nào -> thêm câu mới chỉ có lời dịch.
    Sửa `segs` tại chỗ. stats (nếu có) nhận {"merged": số câu gốc bị gộp, "joined": số câu gốc nhận ≥ 2 câu dịch,
    "added": số câu thêm}. Trả về chỉ số (sau khi gộp) các câu có lời dịch THAY ĐỔI (để tích sẵn, chỉ đọc lại câu đó)."""
    import re

    from .subtitle import Segment

    han = re.compile(r"[㐀-鿿]")
    kana = re.compile(r"[぀-ヿ]")
    st = stats if stats is not None else {}
    st.update(merged=0, joined=0, added=0)

    def vi_text(c) -> str:   # file song ngữ: bỏ dòng tiếng Trung, giữ dòng bản dịch (dòng tiếng Nhật có kana: giữ)
        lines = [x.strip() for x in (c.text or "").splitlines() if x.strip()]
        keep = [x for x in lines if not han.search(x) or kana.search(x)] or lines
        return " ".join(" ".join(keep).split())

    def ov(c, x) -> float:
        return min(c.end, x.end) - max(c.start, x.start)

    def put(x, t: str) -> bool:
        if t and t != x.trans:
            x.trans = t
            x.extra["trans_user"] = True     # bản dịch người dùng đưa vào: AI không tự rút gọn
            x.extra.pop("fit", None)
            return True
        return False

    cues = sorted([c for c in cues if vi_text(c)], key=lambda c: c.start)
    if not segs or not cues:
        return []
    if len(cues) == len(segs) and all(abs(c.start - x.start) < 1.0 for c, x in zip(cues, segs)):
        rows = [k for k, (x, c) in enumerate(zip(segs, cues)) if put(x, vi_text(c))]
        st["songs"] = mark_song_lines(segs, rows)
        return rows
    # câu gốc -> câu dịch phủ nó nhiều nhất (phủ ≥ 30 % câu hoặc ≥ 0,5 s mới tính)
    owner: list[int | None] = []
    for x in segs:
        best = max(((ov(c, x), j) for j, c in enumerate(cues)), default=(0.0, None))
        dur = max(0.05, x.end - x.start)
        owner.append(best[1] if best[0] > 0 and (best[0] >= 0.3 * dur or best[0] >= 0.5) else None)
    # các câu gốc liền nhau cùng 1 câu dịch -> 1 nhóm (sẽ gộp)
    groups: list[list[int]] = []
    for k, o in enumerate(owner):
        if groups and o is not None and owner[groups[-1][-1]] == o:
            groups[-1].append(k)
        else:
            groups.append([k])
    group_of = {k: g for g, ks in enumerate(groups) for k in ks}
    # mỗi câu dịch vào đúng 1 nhóm: nhóm câu gốc nhận nó làm chủ; không thì câu gốc trùng giờ nhiều nhất
    got: dict[int, list] = {}
    added = []
    for j, c in enumerate(cues):
        own = [k for k, o in enumerate(owner) if o == j]
        if own:
            got.setdefault(group_of[own[0]], []).append(c)
            continue
        best = max(((ov(c, x), k) for k, x in enumerate(segs)), default=(0.0, -1))
        if best[0] > 0:
            got.setdefault(group_of[best[1]], []).append(c)
        else:
            added.append(c)
    new: list = []
    changed_ids: set[int] = set()
    for g, ks in enumerate(groups):
        cs = got.get(g, [])
        t = " ".join(vi_text(c) for c in cs).strip()
        if len(ks) > 1:   # file dịch gộp các câu này làm 1 -> gộp câu gốc
            parts = [segs[k] for k in ks]
            text = ""
            for x in parts:
                y = x.text.strip()
                text += (" " if text and y and text[-1].isascii() and text[-1].isalnum() and y[0].isascii()
                         and y[0].isalnum() else "") + y
            m = Segment(parts[0].start, max(x.end for x in parts), text, "", parts[0].speaker,
                        {k: v for k, v in parts[0].extra.items()
                         if k not in ("tts_file", "take", "dub_at", "dub_shift", "fit", "fit_need", "fit_have",
                                      "fit_shift", "fit_over")})
            put(m, t)
            st["merged"] += len(ks) - 1
            new.append(m)
            changed_ids.add(id(m))
            continue
        x = segs[ks[0]]
        if len(cs) > 1:
            st["joined"] += 1
        if put(x, t):
            changed_ids.add(id(x))
        new.append(x)
    for c in added:
        x = Segment(c.start, c.end, "", vi_text(c))
        x.extra["trans_user"] = True
        new.append(x)
        changed_ids.add(id(x))
    st["added"] = len(added)
    new.sort(key=lambda x: x.start)
    segs[:] = new
    rows = [k for k, x in enumerate(segs) if id(x) in changed_ids]
    st["songs"] = mark_song_lines(segs, rows)
    return rows


SONG_MARKS = ("♪", "♫", "♬", "🎵", "🎶")


def mark_song_lines(segs: list, rows=None) -> int:
    """Câu dịch có dấu ♪ (người dịch / AI đánh dấu lời bài hát) -> đánh dấu bài hát 🎵: không lồng tiếng, giữ giọng hát
    gốc. rows: chỉ xét các câu này. Trả về số câu mới được đánh dấu."""
    n = 0
    for k in (range(len(segs)) if rows is None else rows):
        x = segs[k]
        if not x.extra.get("song") and not x.extra.get("not_song") and any(m in (x.trans or "") for m in SONG_MARKS):
            x.extra["song"] = True
            n += 1
    return n


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
    segs = p.segments            # đoạn giọng từng câu của bản được chọn (phụ đề chạy theo giọng lồng)
    at = {int(r[2]): [float(r[0]), float(r[1])] for r in p.data["dub_layout"]}
    for k, s in enumerate(segs):
        if k in at:
            s.extra["dub_at"] = at[k]
        else:
            s.extra.pop("dub_at", None)
    p.segments = segs


def delete_dub_version(p: Project, wav: str) -> None:
    vers = [v for v in dub_versions(p) if v["wav"] != wav]
    try:
        Path(wav).unlink(missing_ok=True)
        dub_env_path(wav).unlink(missing_ok=True)
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


def song_spans(p: Project, settings: dict | None = None) -> list:
    """Đoạn hát (giây) – giọng gốc giữ nguyên ở các đoạn này khi bật "giữ nguyên bài hát"."""
    d = (p.edit.get("dub") or {})
    if d.get("song_spans") is not None and d.get("wav"):
        return d["song_spans"]
    st = settings or load_settings()
    return [[s.start, s.end] for s in p.segments if s.extra.get("song")] if st.get("dub_skip_songs", True) else []


def audio_sources(p: Project, edit: dict | None = None) -> dict:
    """Các lớp âm thanh hiện có của dự án: {lớp: (file, bộ lọc ffmpeg khi giải mã)} – xem trami/audiomix.py."""
    from .audiomix import audio_settings, pitch_filter

    ed = edit or p.edit
    a = audio_settings(ed)
    pf = pitch_filter(float(a.get("pitch") or 1.0))
    out: dict[str, tuple[str, str]] = {}
    if p.data.get("source") and p.data.get("info", {}).get("has_audio", True) and p.source.exists():
        out["orig"] = (str(p.source), pf)
    st = stems(p)
    d = ed.get("dub") or {}
    bg = d.get("bg_wav") if d.get("bg_wav") and Path(d["bg_wav"]).exists() else st.get("instrumental")
    voc = d.get("vocals_wav") if d.get("vocals_wav") and Path(d["vocals_wav"]).exists() else st.get("vocals")
    if bg:
        out["bg"] = (str(bg), "")
    if voc:
        out["voice"] = (str(voc), pf)
    if d.get("wav") and Path(d["wav"]).exists():
        out["dub"] = (str(d["wav"]), "")
    bgm = (ed.get("bgm") or {}).get("path")
    if bgm and Path(bgm).exists():
        out["bgm"] = (str(bgm), "")
    return out


def audio_state(p: Project, edit: dict | None = None, srcs: dict | None = None) -> dict:
    """Cách trộn hiện tại (âm lượng thật từng lớp, đoạn hát, đoạn giữ lại, mờ dần, giảm nhạc) – giống hệt khi xuất."""
    from .audiomix import audio_settings, gains, use_stems
    from .video import keep_ranges

    ed = edit or p.edit
    a = audio_settings(ed)
    srcs = audio_sources(p, ed) if srcs is None else srcs
    d = ed.get("dub") or {}
    dub_on = bool(d.get("enabled")) and "dub" in srcs
    stems_on = use_stems(a, "bg" in srcs, dub_on)
    dur = float(p.data.get("info", {}).get("duration") or 0)
    try:
        keep = keep_ranges(dur, ed.get("trim"), ed.get("cuts")) if dur else []
    except Exception:  # noqa: BLE001
        keep = []
    fade = ed.get("fade") or {}
    return {"gains": gains(a, stems_on, dub_on, "bgm" in srcs), "stems_on": stems_on, "dub_on": dub_on,
            "songs": song_spans(p) if stems_on else [], "keep": keep, "duck": bool(a.get("duck", True)),
            "fade": (float(fade.get("in") or 0), float(fade.get("out") or 0)), "settings": a,
            "mute": [list(x) for x in ed.get("mute_spans") or []]}


def live_mix(p: Project, edit: dict | None = None, progress: Prog | None = None):
    """Bộ trộn đã nạp đủ các lớp (giải mã 1 lần vào work/live, lần sau dùng lại) + trạng thái trộn hiện tại."""
    from .audiomix import LiveMix, decode_track, load_raw

    ed = edit or p.edit
    srcs = audio_sources(p, ed)
    mix = LiveMix()
    tracks = {}
    for k, (f, pre) in srcs.items():
        if progress:
            progress(len(tracks) / max(1, len(srcs)), "Đang chuẩn bị âm thanh để nghe thử…")
        raw = decode_track(f, p.work / "live", k, pre)
        if raw:
            tracks[k] = load_raw(raw)
    mix.set_tracks(tracks)
    apply_audio_state(mix, audio_state(p, ed, srcs))
    return mix


def apply_audio_state(mix, state: dict) -> None:
    mix.g = dict(state["gains"])
    mix.songs = state["songs"]
    mix.keep = state["keep"]
    mix.fade = state["fade"]
    mix.duck = state["duck"]
    mix.mute = state.get("mute") or []


def mix_audio_file(p: Project, out: Path, progress: Prog | None = None) -> Path:
    """Xuất âm thanh đã trộn (đúng như đang nghe trong tool) ra file – nút "Tải MP3 bản trộn"."""
    from .audiomix import SR, to_pcm16

    mix = live_mix(p, progress=progress)
    dur = mix.duration()
    if dur <= 0:
        raise ValueError("Chưa có âm thanh nào để trộn.")
    tmp = out.with_suffix(".tmp.wav")
    import wave

    with wave.open(str(tmp), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(SR)
        pos, step = 0, SR * 10
        total = int(dur * SR)
        while pos < total:
            a, adv = mix.render(pos, min(step, total - pos))
            w.writeframes(to_pcm16(a))
            pos += adv
            if progress:
                progress(pos / total, "Đang trộn âm thanh")
    ff.run(["-i", str(tmp), "-c:a", "libmp3lame", "-b:a", "192k", str(out)], dur, None, None, "Đang lưu MP3")
    tmp.unlink(missing_ok=True)
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
                     + (f", ⚠ {fit['long']} quá dài – nên sửa ngắn lại / AI rút gọn" if fit.get("long") else ""))
    sh = st.get("shift") or {}
    if sh.get("early"):
        extra.append(f"◀ {sh['early']} câu đọc sớm")
    if sh.get("late"):
        extra.append(f"▶ {sh['late']} câu bị lùi (nhiều nhất {sh.get('max_late', 0):.1f} s"
                     + (" – trễ nhiều so với hình, nên dùng 'Tự động' hoặc rút gọn câu" if sh.get("max_late", 0) > 3 else "")
                     + ")")
    if sh.get("over"):
        extra.append(f"⧉ {sh['over']} câu đọc đè câu sau")
    if sh.get("cut"):
        extra.append(f"✂ {sh['cut']} câu bị cắt bớt cuối")
    if st.get("shortened"):
        extra.append(f"AI đã rút gọn {st['shortened']} câu")
    if st.get("songs"):
        extra.append(f"{st['songs']} câu bài hát giữ nguyên")
    if st.get("empty"):
        extra.append(f"{st['empty']} câu chưa dịch")
    if st.get("nodub"):
        extra.append(f"{st['nodub']} câu 'Bỏ lồng'")
    if st.get("interj"):
        extra.append(f"đã xoá {st['interj']} câu chỉ có tiếng cảm thán (A, Ừm…)")
    if st.get("errors"):
        extra.append(f"{st['errors']} câu lỗi")
    if st.get("lang_edge"):
        extra.append(f"{st['lang_edge']} câu không phải tiếng Việt đọc bằng Edge (VieNeu chỉ đọc tiếng Việt)")
    if st.get("auto_voiced"):
        extra.append(f"chưa chọn giọng nên đã tự gán giọng khác nhau cho {st['auto_voiced']} người nói")
    for spec, n in (st.get("fallback") or {}).items():
        extra.append(f"⚠ {n} câu chọn giọng {voice_label(spec)} nhưng bộ đọc này chưa cài / giọng mẫu đã xoá – "
                     "đã đọc bằng Hoài My (Edge)")
    miss = int(st.get("total", 0)) - int(st.get("placed", 0)) - sum(
        int(st.get(k, 0) or 0) for k in ("songs", "empty", "nodub"))
    if miss > 0:
        extra.append(f"{miss} câu chưa có giọng – bấm 'Tích câu chưa lồng' rồi 'Tạo lại giọng các câu đã tích'")
    used = st.get("voices_used") or {}
    if used:   # giọng THẬT đã đọc từng người nói
        parts = []
        for spk in sorted(used, key=lambda k: (-sum(used[k].values()), k)):
            vs = sorted(used[spk].items(), key=lambda x: -x[1])
            parts.append(f"{spk}: " + ", ".join(voice_label(v) + (f" ×{n}" if len(vs) > 1 else "") for v, n in vs))
        extra.append("giọng – " + "; ".join(parts[:6]) + (" …" if len(parts) > 6 else ""))
    return msg + (f" ({', '.join(extra)})" if extra else "")


def voice_label(spec: str) -> str:
    """Tên dễ đọc của mã giọng ("edge:vi-VN-NamMinhNeural" -> "Nam Minh (Edge)")."""
    from . import tts

    if spec in (tts.CLONE,) or spec.startswith("clone:"):
        return "Nhái giọng gốc (VieNeu)"
    if spec == tts.OMNI_CLONE or spec.startswith("omniclone:"):
        return "Nhái giọng gốc (OmniVoice)"
    if spec.startswith(tts.VN_SAMPLE):
        return spec[len(tts.VN_SAMPLE):].split(" (")[0] + " (VieNeu)"
    if spec.startswith("vieneu:"):
        return (spec[7:] or "mặc định") + " (VieNeu)"
    if spec.startswith("edge:"):
        import re as _re

        n = _re.sub(r"^[a-z]{2}-[A-Z]{2}-|Neural$|Multilingual", "", spec[5:])
        return _re.sub(r"(?<=[a-z])(?=[A-Z])", " ", n) + " (Edge)"
    if spec.startswith("omnid:"):
        from . import omni_design

        d = omni_design.get(spec) or {}
        return (d.get("name") or spec[6:]) + " (OmniVoice)"
    if spec.startswith("omni:"):
        from . import voices as vlib

        sm = vlib.get(spec[5:]) or {}
        return (sm.get("name") or spec[5:]) + " (OmniVoice)"
    return spec


# ------------------------------------------------------------------ xuất

def export_subs(p: Project, fmt: str = "srt", which: str = "trans", settings: dict | None = None) -> Path:
    settings = settings or load_settings()
    info = p.data.get("info", {})
    name = safe_name(Path(p.data.get("source", "video")).stem or p.data.get("name", "video"))
    tl = trans_lang(p)
    suffix = {"trans": tl, "source": "zh", "both": f"zh-{tl}"}.get(which, tl)
    out = p.output_dir / f"{name}.{suffix}.{fmt}"
    save_subtitles(p.segments, out, which, {**(settings.get("sub_style") or {}), "lang": tl}, info.get("width") or 1920,
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
    """Vị trí 'auto': đặt câu Việt vào đúng chỗ phụ đề Trung cũ (canh theo mép dưới của vùng chữ cũ).
    'custom' (kéo phụ đề trên video): giữ chỗ người dùng đặt, cỡ chữ tự động vẫn theo phụ đề cũ như 'auto'.
    Ghi thêm "lang" = tiếng của bản dịch -> chọn font có chữ Thái / Nhật / Hàn khi xuất và xem trước."""
    st = dict(style or {})
    st["lang"] = trans_lang(p)
    pos = st.get("position", "auto")
    if pos == "custom" and (st.get("pos_x") is None or st.get("pos_y") is None):
        pos = "auto"
    if pos not in ("auto", "custom"):
        return st
    if pos == "auto":
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
    if pos == "auto" and 0.25 < center < 0.97:
        st["margin_v_rel"] = round(min(0.7, max(0.01, 1.0 - bottom)), 4)
    return st


def hardsub_spec(p: Project, hs: dict | None = None) -> dict:
    """Cách xoá phụ đề cũ đúng như lúc xuất (thêm file khung chữ OCR khi xoá bằng AI 'chỉ lúc có chữ')."""
    hs = dict((p.edit.get("hardsub") or {}) if hs is None else hs)
    if hs.get("mode") == "inpaint" and p.data.get("ocr_boxes") and hs.get("use_ocr_boxes", True):
        hs["boxes_file"] = p.data["ocr_boxes"]
    return hs


def inpainted_video(p: Project, hs: dict | None = None, settings: dict | None = None) -> Path | None:
    """Video đã xoá phụ đề (AI / nền khung khác) + gỡ logo với đúng vùng / tuỳ chọn hiện tại (None = chưa chạy / đã đổi)."""
    from .video import inpainted_file, needs_clean

    hs = hardsub_spec(p, hs)
    if not needs_clean(hs) or not p.data.get("source"):
        return None
    f = inpainted_file(p.source, hs, settings or load_settings(), p.work / "render")
    return f if f.exists() else None


def run_inpaint(p: Project, settings: dict | None = None, progress: Prog | None = None, cancel=None) -> Path:
    """Nút 'Chạy xoá': xoá phụ đề cũ / gỡ logo cả video ngay (không đợi xuất). Xuất video sau đó dùng lại bản này."""
    from .video import inpaint_cached, needs_clean

    hs = hardsub_spec(p)
    if not needs_clean(hs):
        if hs.get("mode") == "inpaint":
            raise ValueError("Chưa khoanh vùng chữ cần xoá.")
        raise ValueError("Chọn cách xoá 'Xoá sạch (AI)' hoặc khoanh logo cố định trước.")
    return inpaint_cached(p.source, hs, settings or load_settings(), p.work / "render", progress, cancel)


def render(p: Project, settings: dict | None = None, progress: Prog | None = None, cancel=None,
           out_name: str | None = None) -> Path:
    from .video import build_and_render

    settings = settings or load_settings()
    edit = p.edit
    edit["hardsub"] = hardsub_spec(p)
    srcs = audio_sources(p, edit)
    d = edit.setdefault("dub", {})
    if "bg" in srcs and not (d.get("bg_wav") and Path(d["bg_wav"]).exists()):
        # tab Âm thanh chọn "bản đã tách" khi chưa lồng tiếng: video xuất cũng dùng nhạc nền + giọng gốc đã tách
        d["bg_wav"] = srcs["bg"][0]
        d["vocals_wav"] = srcs.get("voice", ("",))[0]
        d["song_spans"] = song_spans(p, settings)
    from .config import _deep_merge

    base_style = _deep_merge(settings.get("sub_style") or {}, (edit.get("subtitles") or {}).get("style") or {})
    edit.setdefault("subtitles", {})["style"] = effective_style(p, base_style)
    name = out_name or (safe_name(Path(p.data.get("source", "video")).stem) + "_vietsub")
    out = p.output_dir / f"{safe_name(name)}.mp4"
    if out.exists():
        out = p.output_dir / f"{safe_name(name)}_{time.strftime('%H%M%S')}.mp4"
    segs = p.segments
    if edit["subtitles"]["style"].get("karaoke"):
        for k, sp in karaoke_spans(p, edit).items():
            segs[k].extra["kv"] = sp
    # câu có giọng lồng đọc sớm / bị lùi / đọc dài: phụ đề hiện theo đúng lúc giọng đọc
    segs = subs_follow_dub(p, segs, edit, settings, (edit.get("subtitles") or {}).get("which", "trans"))
    build_and_render(p.source, out, edit, settings, p.work / "render", segs, progress, cancel)
    p.add_output(out, "video")
    return out


# ------------------------------------------------------------------ tự động toàn bộ

def auto(p: Project, opts: dict, progress: Prog | None = None, cancel=None) -> dict:
    """Chạy trọn gói: (tải) -> nhận dạng -> dịch -> (lồng tiếng) -> xuất video + phụ đề."""
    settings = load_settings()
    if opts.get("engine"):
        settings["translate_engine"] = opts["engine"]
    no_tr = opts.get("engine") == NO_TRANSLATE or not opts.get("translate", True)
    if opts.get("engine") == NO_TRANSLATE:
        settings["translate_engine"] = "google"   # không dùng tới, tránh lỗi ở bước khác
    steps = []
    if opts.get("url") and not p.data.get("source"):
        steps.append("download")
    if not p.segments or opts.get("force_recognize"):
        steps.append("recognize")
    if not no_tr:
        steps.append("translate")
    if opts.get("dub") and not no_tr:
        steps.append("dub")
    steps.append("export")
    weights = {"download": 1, "recognize": 4, "translate": 2, "dub": 2, "export": 3}
    total_w = sum(weights[s] for s in steps)
    result: dict = {}
    if opts.get("target"):   # "Dịch sang" (tiếng Việt / Anh / Thái / Nhật / Hàn)
        from . import langs

        p.data["target_lang"] = langs.norm(opts["target"])
    live_box: list = [None]   # chế độ vừa tách vừa dịch (stream.LivePipeline)
    try:
        _auto_steps(p, opts, steps, weights, total_w, settings, no_tr, result, progress, cancel, live_box)
    finally:
        live = live_box[0]
        if live:
            live.stop()
            live.cleanup()
    if not no_tr:
        result["message"] = "Hoàn tất! " + (f"Video: {Path(result['video']).name}" if result.get("video") else "")
    if opts.get("parallel") and p.data.get("live_reused"):
        result["message"] += f"  · vừa tách vừa dịch: dùng lại {p.data['live_reused']} câu dịch sẵn lúc đang nghe"
    if opts.get("dub") and not no_tr:
        result["message"] += "  · " + dub_summary(p)
    if p.data.get("skill_note") and not no_tr:
        result["message"] = result.get("message", "") + "  · " + p.data["skill_note"]
    for w in ("dl_warning", "sep_warning", "asr_warning", "ocr_warning", "translate_warning", "skill_warning"):
        if p.data.get(w):
            result["message"] = result.get("message", "") + "  ⚠ " + p.data[w]
    return result


def _start_live(p: Project, opts: dict, settings: dict, cancel):
    """Bật chế độ vừa tách vừa dịch: luồng dịch (+ luồng đọc trước bằng Edge khi biết chắc giọng của mọi câu)."""
    from . import langs, stream

    eng = settings.get("translate_engine", "google")
    if eng == "hymt":   # dịch trên máy dùng card đồ hoạ – không chạy cùng lúc với bộ nghe (hết bộ nhớ GPU)
        return None
    target = langs.norm(p.data.get("target_lang") or settings.get("target_lang", "vi"))
    dub_cfg = None
    tts_engine = opts.get("tts_engine") or settings.get("tts_engine", "edge")
    voice = opts.get("voice") or ""
    if opts.get("dub") and tts_engine == "edge" and voice != "@auto":
        voices = {k: v for k, v in (p.data.get("voices") or {}).items() if v}
        one_spk = not settings.get("diarize", True) or int(settings.get("diarize_speakers", 0) or 0) == 1
        if voice or one_spk or len(set(voices.values())) <= 1:   # mọi câu cùng 1 giọng -> đọc trước được
            v = voice or next(iter(voices.values()), "") or dub_default_voice(None, "edge", settings)
            if v.startswith("edge:"):
                dub_cfg = {"voice": v, "rate": opts.get("rate") or settings.get("tts_rate", "+0%"),
                           "settings": settings, "lang": target,
                           "drop_interj": target == "vi" and settings.get("dub_drop_interj", True)}
    return stream.LivePipeline(p, settings, eng, opts.get("style"), target, cancel, dub_cfg)


def _auto_steps(p: Project, opts: dict, steps: list, weights: dict, total_w: float, settings: dict, no_tr: bool,
                result: dict, progress: Prog | None, cancel, live_box: list) -> None:
    acc = 0.0
    for st in steps:
        a = acc / total_w
        acc += weights[st]
        pr = _sub(progress, a, acc / total_w)
        if st == "download":
            notes: list[str] = []
            path = download_video(opts["url"], p.dir / "source", pr, cancel, notes=notes, settings=settings)
            attach_source(p, path)
            p.data["name"] = path.stem[:80]
            p.data["dl_warning"] = " ".join(notes)
            p.save()
        elif st == "recognize":
            if opts.get("asr_engine"):
                settings["asr_engine"] = opts["asr_engine"]
            if "diarize" in opts:
                settings["diarize"] = bool(opts["diarize"])
            if "separate" in opts:
                settings["sep_before_asr"] = bool(opts["separate"])
            live = _start_live(p, opts, settings, cancel) if opts.get("parallel") and "translate" in steps else None
            live_box[0] = live
            rpr = pr
            if live:
                def rpr(x, m, pr=pr, live=live):
                    pr(x, m + live.status())
            recognize(p, opts.get("method", "asr"), opts.get("language", "zh"), opts.get("region"),
                      float(opts.get("ocr_fps") or settings.get("ocr_fps") or 4), settings, rpr, cancel,
                      bool(opts.get("denoise")), on_draft=live.feed if live else None)
            p.data.pop("live_reused", None)
            if live:   # gắn bản dịch làm sẵn lúc đang nghe vào câu cuối cùng (trùng chữ gốc)
                pr(1.0, "Đang chờ lần gửi dịch đang chạy xong…" + live.status())
                done = live.finish()
                segs = p.segments
                n = 0
                for x in segs:
                    if not x.trans.strip() and done.get(x.text):
                        x.trans = done[x.text]
                        n += 1
                p.data.setdefault("cache", {}).update(live.tr.cache)
                if n:
                    p.segments = segs
                    p.data["trans_lang"] = p.data["target_lang"] = live.tr.target
                p.data["live_reused"] = n
                p.save()
        elif st == "translate":
            # vừa tách vừa dịch: chỉ dịch các câu chưa có bản dịch (câu bị đổi sau khi nghe lại / căn / tách người nói)
            translate(p, opts.get("engine"), opts.get("style"), bool(opts.get("only_empty")) or bool(opts.get("parallel")),
                      settings, pr, cancel)
        elif st == "dub":
            _auto_voices(p, opts, settings)
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
                    # vùng xoá = khung vừa khít chữ phụ đề cũ (vùng quét OCR rộng hơn nhiều, không dùng để xoá)
                    reg = (p.data.get("ocr_region") or {}).get("erase")
                    if not reg:
                        from .ocr import detect_subtitle_region

                        r = detect_subtitle_region(p.source)
                        reg = r.get("erase") or r
                    ed["hardsub"]["regions"] = [{k: reg[k] for k in ("x", "y", "w", "h")}]
            if "burn" in opts:
                ed["subtitles"]["burn"] = bool(opts["burn"])
            if opts.get("which"):
                ed["subtitles"]["which"] = opts["which"]
            if opts.get("dub") and not no_tr:
                ed["dub"]["enabled"] = True
            p.data["edit"] = ed
            p.save()
            if no_tr:   # "Không dịch": chỉ xuất phụ đề gốc đã tách câu, không ghép video
                srt = export_subs(p, "srt", "source", settings)
                result["srt"] = str(srt)
                result["message"] = (f"Đã nghe và tách {len(p.segments)} câu – không dịch. "
                                     f"Phụ đề gốc: {Path(srt).name}")
                continue
            srt = export_subs(p, "srt", "trans", settings)
            result["srt"] = str(srt)
            if opts.get("render", True) and p.data.get("info", {}).get("has_video"):
                result["video"] = str(render(p, settings, pr, cancel))


def _auto_voices(p: Project, opts: dict, settings: dict) -> None:
    """Giọng chọn ở tab Tự động: 1 giọng cho mọi người nói, hoặc "@auto" = tự gán giọng nam / nữ theo từng người nói.
    Để trống = giữ giọng đã chọn ở tab Lồng tiếng mục 2."""
    v = opts.get("voice") or ""
    if not v:
        return
    spks = [x["speaker"] for x in speaker_list(p)]
    if v == "@auto":
        from . import tts

        genders = p.data.get("speaker_gender") or {}
        if not all(k in genders for k in spks):
            try:
                genders = speaker_genders(p)
            except Exception:  # noqa: BLE001 – không đoán được thì xen kẽ nam / nữ
                log_error("Đoán giọng nam / nữ (tab Tự động)")
        st = dict(settings, tts_engine=opts.get("tts_engine") or settings.get("tts_engine", "edge"))
        p.data["voices"] = tts.auto_assign(spks, st, {}, genders, target_lang(p, settings))
        opts["voice"] = None
    else:
        p.data["voices"] = {k: v for k in spks}
    segs = p.segments   # giọng riêng từng câu đè lên giọng người nói -> bỏ, để giọng vừa chọn áp cho mọi câu
    if any(s.extra.get("voice") for s in segs):
        for s in segs:
            s.extra.pop("voice", None)
        p.segments = segs
    p.save()
