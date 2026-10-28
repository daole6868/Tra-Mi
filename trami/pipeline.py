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
        raise RuntimeError("Bản đóng gói .exe không tự cập nhật trình tải được – hãy tải bản Subvideo mới.")
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
                   notes: list | None = None, settings: dict | None = None, meta: dict | None = None) -> Path:
    """Tải video từ Douyin, Bilibili, YouTube, TikTok, Weibo, Xigua... bằng yt-dlp.

    `url` có thể là cả dòng chia sẻ của app ("【tên】 https://b23.tv/xx") – tự lấy link. Bilibili đã đăng nhập bằng mã QR
    (sitelogin) -> tải 720p/1080p; trang khác dùng cookie trình duyệt / file cookies.txt ở Cài đặt. Chọn ≤ 1080p, ưu
    tiên H.264 (trình phát + FFmpeg mở nhanh, không phải tạo bản xem trước như HEVC/AV1). Tiến độ gộp hình + tiếng
    (Bilibili / YouTube tải 2 file rồi ghép). `notes` nhận lời nhắc (vd. chỉ 480p vì chưa đăng nhập).
    `meta` nhận tiêu đề / mô tả / thẻ / kênh của video (gợi ý tên riêng cho bộ nghe – names.context_text)."""
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
    if meta is not None:
        meta.update({"title": str(info.get("title") or "")[:200], "uploader": str(info.get("uploader") or "")[:80],
                     "description": str(info.get("description") or "")[:600],
                     "tags": [str(t)[:30] for t in (info.get("tags") or [])[:20]], "url": url})
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
    p.data["timeline"] = ff.TIMELINE       # dự án mới: mọi thứ giải mã theo giờ của hình (2.0.92)
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


PROXY_MODES = [("auto", "Tự tạo khi video nặng (trên 1080p, trên 30 khung/s, HEVC / AV1)"),
               ("always", "Luôn tạo bản xem trước nhẹ"), ("off", "Không tạo (phát thẳng video gốc)")]


def needs_proxy(p: Project, settings: dict | None = None) -> bool:
    """Video nặng cho trình phát (4K, 50/60 khung/s, HEVC / AV1 giải mã bằng CPU) -> nên phát bản nhẹ ≤ 720p (chỉ để
    xem trong tool; xuất video vẫn dùng video gốc)."""
    st = settings or load_settings()
    mode = st.get("proxy_mode", "auto")
    i = p.data.get("info", {})
    if mode == "off" or not p.data.get("source") or not i.get("width"):
        return False
    if mode == "always":
        return True
    return (max(int(i.get("width") or 0), int(i.get("height") or 0)) > 1920 or float(i.get("fps") or 0) > 31
            or str(i.get("vcodec") or "") in ("hevc", "h265", "av1", "vp9") and int(i.get("height") or 0) > 720)


def make_preview(p: Project, progress: Prog | None = None, cancel=None) -> Path:
    """Tạo bản xem trước nhẹ (H.264 ≤720p) để phát được trong trình duyệt. Không ảnh hưởng chất lượng xuất."""
    out = p.work / "preview.mp4"
    tmp = p.work / "preview_tmp.mp4"
    info = p.data.get("info", {})
    fps = float(info.get("fps") or 0)
    vf = "scale=-2:'min(720,ih)'" + (",fps=30" if fps > 31 else "")    # 50/60 khung/s -> 30: phát nhẹ hơn hẳn
    ff.run(["-i", str(p.source), "-vf", vf, "-c:v", "libx264", "-preset", "veryfast", "-tune", "fastdecode",
            "-crf", "27", "-g", "30", "-pix_fmt", "yuv420p"] + ff.af_args(ff.audio_sync(p.source)) +
           ["-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", str(tmp)], info.get("duration", 0), progress,
           cancel, "Đang tạo bản xem trước nhẹ (chỉ để xem trong tool)")
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


# ------------------------------------------------------------------ căn lại giờ (âm thanh lệch giờ so với hình)
# Trước 2.0.92 âm thanh được giải mã mẫu nối liền, bỏ qua giờ của luồng âm thanh: video có giờ âm thanh lệch dần
# (livestream, ghép nhiều phần, phim dài) -> bản tách giọng, mốc câu (nghe), giọng lồng, tiếng gốc khi phát khớp nhau
# nhưng trôi dần so với khẩu hình. Dự án cũ: đo lệch (ffprobe, vài giây) -> căn lại mà không phải nghe / dịch / đọc lại.

def timeline_check(p: Project) -> dict:
    """Dự án làm trước 2.0.92 có bị lệch giờ không: {"need": cần căn lại, "mark": không lệch -> ghi dấu đã đúng giờ,
    "drift": {"max", "end", "start"}}. Không đổi dự án (giao diện tự ghi dấu)."""
    if p.data.get("timeline") == ff.TIMELINE or not p.data.get("source") or source_missing(p):
        return {"need": False, "mark": False}
    if not p.data.get("segments") and not stems(p):
        return {"need": False, "mark": True}
    d = ff.timeline_drift(p.source)
    if d is None:
        return {"need": False, "mark": False}
    return {"need": d["max"] > ff.SYNC_MAX, "mark": d["max"] <= ff.SYNC_MAX, "drift": d}


def _retime_segs(segs: list[Segment], f) -> None:
    """Dời mốc câu + đoạn giọng lồng / karaoke theo hàm đổi giờ f (mảng giây -> mảng giây)."""
    import numpy as np

    for s in segs:
        a, b = f(np.array([s.start, s.end], np.float64))
        s.start, s.end = round(float(a), 3), round(float(max(b, a + 0.05)), 3)
        for k in ("dub_at", "kv", "ct"):
            v = s.extra.get(k)
            if not v:
                continue
            if k == "dub_at":
                s.extra[k] = [round(float(x), 3) for x in f(np.array(v[:2], np.float64))] + list(v[2:])
            else:
                s.extra[k] = [[round(float(x), 3) for x in f(np.array(pair[:2], np.float64))] for pair in v]


def retime(p: Project, settings: dict | None = None, progress: Prog | None = None, cancel=None) -> dict:
    """Căn giờ âm thanh theo hình (nút "Căn giờ theo hình"; dự án làm trước 2.0.92): kéo giãn bản tách giọng / nhạc nền
    theo giờ thật, dời mốc câu (nếu lấy bằng nghe – OCR / SRT vốn theo hình) + đoạn giọng lồng, xoá các bản giải mã cũ,
    xếp lại bản lồng. Không nghe / dịch / đọc lại câu nào. Bấm lại khi đã căn / video không lệch -> không dời gì.
    Trả {"state": "done" | "ok" (không lệch) | "already" (đã căn trước đó), "drift", "moved": số câu dời, "dub"}."""
    import numpy as np

    from .separate import retime_stems

    settings = settings or load_settings()
    if source_missing(p):
        raise RuntimeError("Không tìm thấy video gốc – bấm 'Tìm lại video gốc…' trước.")
    if progress:
        progress(0.01, "Đang đọc giờ âm thanh của video…")
    tl = ff.audio_timeline(p.source)
    if tl is None:
        raise RuntimeError("Không đọc được giờ âm thanh của video.")
    c, pt, d = tl
    drift = {"max": round(float(np.max(np.abs(pt - c))), 3), "end": round(float(np.median((pt - c)[-50:])), 3)}
    st = stems(p)
    if st and not (Path(st["vocals"]).parent / "timeline.txt").exists():   # bản tách lại sau 2.0.92 đã đúng giờ
        retime_stems(p.source, Path(st["vocals"]).parent, _sub(progress, 0.03, 0.75), tl)
    if cancel and cancel():
        raise ff.Cancelled()
    already = p.data.get("timeline") == ff.TIMELINE     # câu / bản lồng đã theo giờ hình (nghe sau 2.0.92 / đã căn)
    if already or drift["max"] <= ff.SYNC_MAX:
        if not already:
            p.data["timeline"] = ff.TIMELINE
            p.save()
        if progress:
            progress(1.0, "Âm thanh đã khớp giờ hình")
        return {"state": "already" if already else "ok", "drift": drift, "moved": 0, "dub": False}

    def to_real(t):    # giây theo số mẫu (cách cũ) -> giờ thật của hình; ngoài 2 đầu dời đều theo đầu / cuối
        t = np.asarray(t, np.float64)
        r = np.interp(t, c, pt)
        r = np.where(t < c[0], pt[0] + (t - c[0]), r)
        return np.where(t > c[-1], pt[-1] + (t - c[-1]), r)

    moved = 0
    if p.data.get("sub_source") in ("asr", "hybrid"):   # giờ câu đo trên âm thanh (OCR thuần theo hình – không dời)
        segs = p.segments
        _retime_segs(segs, to_real)
        p.segments = segs
        moved = len(segs)
        for key in ("cleanup_backup", "join_backup", "split_backup", "interj_removed"):
            old = p.data.get(key)
            if isinstance(old, list) and old and isinstance(old[0], dict) and "start" in old[0]:
                bk = [Segment.from_dict(x) for x in old]
                _retime_segs(bk, to_real)
                p.data[key] = [x.to_dict() for x in bk]
    for name in ("asr16k_vocals.wav", "audio16k.wav", "tl_wave.npy", TL_WAVE, "loud_50ms.npy", "loud_50ms.json",
                 "voice_50ms.npy", "voice_50ms.json", VOICE_ENV + ".npy", VOICE_ENV + ".json", "loud_10ms.npy",
                 "loud_10ms.json"):
        try:       # đang mở ở chỗ khác (Windows) -> để đó, các bản này tự làm lại theo dấu giờ / ngày sửa
            (p.work / name).unlink(missing_ok=True)
        except OSError:
            pass
    p.data["timeline"] = ff.TIMELINE
    p.data["audio_drift"] = drift
    p.save()
    re_dub = bool(moved) and has_dub(p)
    if re_dub:
        relayout(p, settings, _sub(progress, 0.78, 1.0), cancel)
    if progress:
        progress(1.0, "Đã căn lại giờ cho khớp hình")
    return {"state": "done", "drift": drift, "moved": moved, "dub": re_dub}


def timeline_assets(p: Project) -> dict:
    """Dải ảnh thu nhỏ + đường sóng âm cho timeline (tạo 1 lần, lưu trong work/). Video dài vẫn ≤ 160 ảnh."""
    import math

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
    wave = p.work / TL_WAVE
    if not wave.exists() or wave.stat().st_mtime < Path(src).stat().st_mtime:
        # sóng âm mịn 0,01 s (2.0.96) – cùng 1 lần đo với bám mép câu (voice_10ms), không giải mã thêm
        db, hop, _src = voice_env(p)
        env = np.power(10.0, np.asarray(db, np.float32) / 20.0) if db is not None else np.zeros(0, np.float32)
        if len(env):
            env = np.minimum(1.0, env / max(1e-4, float(np.percentile(env, 99))))
        np.save(wave, env.astype(np.float16))
    out.update({"wave": str(wave), "hop": 0.01})
    return out


TL_WAVE = "tl_wave_10ms.npy"   # sóng âm timeline mỗi 0,01 s (trước 2.0.96: tl_wave.npy mỗi 0,05 s)


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
        if zh and (engine == "firered" or use_qwen_align(engine, settings)):
            from . import asr_qwen

            try:   # FireRedASR2: căn câu chưa có mốc (câu nghe lại…) bằng chính nhánh CTC của nó (không nạp thêm Qwen3)
                asr_qwen.align_qwen(segs, wav, language, settings, _sub(progress, 0.0, 0.7), cancel,
                                    engine="firered" if engine == "firered" else "qwen3")
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


def verify_char_times(p: Project, segs: list, wav: Path, language: str, settings: dict, engine: str = "qwen3",
                      progress: Prog | None = None, cancel=None) -> dict:
    """Kiểm tra mốc từng chữ sau khi căn (2.1.3): câu có chữ bị kéo dài bất thường (`align.stretched_chars` – thường
    kéo qua đoạn lặng / nhạc) hoặc chữ lấn sang câu liền trước (câu nghe lại / câu sửa căn riêng) -> căn lại RIÊNG câu
    đó trong cửa sổ hẹp (chỉ phần có tiếng theo giọng đã tách; câu lấn nhau: căn CHUNG 2 câu rồi chia theo số chữ).
    Chỉ nhận mốc mới khi hợp lý, bớt chữ bị kéo, không lấn câu bên cạnh; không thì giữ mốc cũ (`snap.fix_stretched`
    co tiếp theo độ to giọng ở bước bám mép). Trả {"bad", "fixed"}."""
    from . import asr_qwen
    from .align import PAD, _char_positions, _latin_run, fix_overlaps, stretched_chars, times_from_words
    from .snap import ct_ok, voiced_span

    st = {"bad": 0, "fixed": 0}
    if language not in ("zh", "yue", "auto") or not segs:
        return st
    n = len(segs)
    ctv = [s.extra["ct"] if ct_ok(s) else None for s in segs]
    groups: list[list[int]] = []
    used: set[int] = set()
    for i in range(n):
        if ctv[i] is None or i in used:
            continue
        if i > 0 and ctv[i - 1] and i - 1 not in used and float(ctv[i][0][0]) < float(ctv[i - 1][-1][1]) - 0.05:
            groups.append([i - 1, i])
            used |= {i - 1, i}
        elif stretched_chars(ctv[i]):
            groups.append([i])
            used.add(i)
    st["bad"] = len(groups)
    if not groups:
        return st
    env = None
    if stems(p):
        try:
            env, hop, _src = voice_env(p, None, cancel)
        except ff.Cancelled:
            raise
        except Exception:  # noqa: BLE001 – không có độ to giọng: chỉ căn lại câu lấn nhau
            env = None
    items, todo = [], []
    for g in groups:
        a, b = segs[g[0]].start, segs[g[-1]].end
        lo = segs[g[0] - 1].end if g[0] > 0 else 0.0
        hi = segs[g[-1] + 1].start if g[-1] + 1 < n else b + 0.3
        if len(g) == 1:
            if env is None or not len(env):
                continue
            vs = voiced_span(env, hop, a, b)
            if not vs or vs[1] - vs[0] > (b - a) - 0.3:
                continue   # phần có tiếng không hẹp hơn câu: căn lại cũng ra như cũ
            a, b = vs[0] - 0.25, vs[1] + 0.25
        else:
            a, b = a - 0.2, b + 0.2
        a = max(0.0, lo, a)
        b = min(hi, b) if hi > a else b
        if b - a < 0.3:
            continue
        items.append([round(a, 3), round(b, 3), "".join(segs[k].text for k in g)])
        todo.append(g)
    if not items:
        return st
    r = asr_qwen.asr_worker("firered" if engine == "firered" else "qwen3", settings).call(
        {"cmd": "align", "wav": str(Path(wav).resolve()), "items": items, "language": language}, progress, cancel)
    if r.get("align_error"):
        return st
    for g, (a, b, text), it in zip(todo, items, r.get("items") or []):
        ct = times_from_words(text, it.get("words") or [], a)
        if not ct:
            continue
        parts, k0 = [], 0
        for k in g:
            m = len(_char_positions(segs[k].text))
            parts.append(ct[k0:k0 + m])
            k0 += m
        if any(not x for x in parts) or sum(len(stretched_chars(x)) for x in parts) > \
                sum(len(stretched_chars(ctv[k])) for k in g) - (1 if len(g) == 1 else 0):
            continue
        prev_ct = ctv[g[0] - 1] if g[0] > 0 else None
        next_ct = ctv[g[-1] + 1] if g[-1] + 1 < n else None
        if (prev_ct and parts[0][0][0] < float(prev_ct[-1][1]) - 0.05) or \
                (next_ct and parts[-1][-1][1] > float(next_ct[0][0]) + 0.05):
            continue   # mốc mới lấn câu bên ngoài nhóm
        for k, x in zip(g, parts):
            s = segs[k]
            s.extra["ct"] = [[round(u, 3), round(v, 3)] for u, v in x]
            if not _latin_run(s.text, True):
                s.start = round(max(0.0, x[0][0] - PAD), 3)
            if not _latin_run(s.text, False):
                s.end = round(max(s.start + 0.2, x[-1][1] + PAD), 3)
        st["fixed"] += 1
    fix_overlaps(segs)
    return st


def _verify_ct_safe(p: Project, segs: list, wav: Path, language: str, settings: dict, engine: str,
                    cancel=None) -> dict:
    """verify_char_times như 1 bước phụ: lỗi (thiếu bộ nhớ GPU, bộ căn chưa tải…) thì giữ mốc đang có."""
    try:
        return verify_char_times(p, segs, wav, language, settings, engine, None, cancel)
    except ff.Cancelled:
        raise
    except Exception as e:  # noqa: BLE001
        log_error("Kiểm tra mốc từng chữ")
        return {"error": err_text(e)[:120]}


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
                    progress: Prog | None = None, cancel=None, keep_lines: bool = False,
                    stats: dict | None = None) -> tuple[list, int]:
    """Tìm đoạn có tiếng nói (trên giọng đã tách) mà chưa có câu nào, nghe lại từng đoạn. Trả về (câu, số câu thêm).
    stats["lowconf"]: số đoạn bỏ vì bộ nghe không chắc (nhạc / tiếng động bị đoán thành lời – 2.1.3)."""
    from . import align
    from .asr_funasr import load_wav16k

    try:
        if engine == "whisper":
            from .asr import whisper_decoder

            decode = whisper_decoder(settings, language)
        elif engine in ("qwen3", "firered"):
            from .asr_qwen import qwen_decoder

            decode = qwen_decoder(settings, language, Path(wav).parent, engine=engine)
        else:
            from . import models
            from .asr_funasr import sensevoice_decoder

            if engine == "paraformer" and not models.is_ready("sensevoice"):
                return segs, 0
            decode = sensevoice_decoder(settings, language)
        new = align.rescue_missed(segs, load_wav16k(wav), decode, int(settings.get("asr_max_chars", 28)),
                                  progress, cancel, keep_lines=keep_lines, stats=stats)
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
    wav = _voice_wav16(p, _sub(progress, 0, 0.1), cancel)
    from .align import drop_char_times

    drop_char_times(segs, keep=False)   # đo lại từ đầu theo lời hiện tại (người dùng có thể đã sửa chữ)
    old_texts = {s.text for s in segs}
    try:
        eng = p.data.get("asr_engine_used", "")
        segs = align_segments(segs, wav, p.data.get("language", "zh"), settings, _sub(progress, 0.1, 0.9), cancel,
                              engine=eng)
        if eng in ("qwen3", "firered") or use_qwen_align(eng, settings):
            p.data["ct_check"] = _verify_ct_safe(p, segs, wav, p.data.get("language", "zh") or "zh", settings, eng,
                                                 cancel)
    finally:
        from .asr_qwen import release

        release()

    drop_char_times(segs)
    forget_stale_voice(old_texts, segs, moved=True)
    p.segments = segs
    p.save()
    if settings.get("snap_edges", True):   # mép câu đúng lúc nhân vật bắt đầu / thôi nói (2.0.96)
        try:
            snap_edges(p, settings, relayout_dub=False, progress=_sub(progress, 0.9, 0.93), cancel=cancel)
        except ff.Cancelled:
            raise
        except Exception:  # noqa: BLE001 – bước phụ
            log_error("Bám mép câu theo giọng")
    if has_dub(p):   # đã lồng tiếng: xếp lại giọng theo mốc mới ngay (không đọc lại) -> độ vừa, màu tính lại
        relayout(p, settings, _sub(progress, 0.93, 1.0), cancel)
    return len(p.segments)


# ------------------------------------------------------------------ nghe chính xác hơn (2.1.0)

def _heard(text: str, language: str) -> str:
    from .asr import to_simplified

    t = (text or "").strip()
    if language in ("zh", "yue", "auto"):
        z = to_simplified(t)
        if len(z) == len(t):
            t = z
    return t


def _cjk_count(text: str) -> int:
    from .align import CJK

    return len(CJK.findall(text or ""))


def _learn_profile(p: Project, segs, settings: dict) -> bool:
    """AI đọc lời vừa nghe -> hồ sơ phim (tên nhân vật, thuật ngữ) – lưu vào dự án, bước dịch dùng lại (không gọi lại)."""
    from . import langs, skills
    from .translate import LLMClient, glossary_text, parse_glossary

    eng, _why = join_engine(settings, "asr_ai")
    texts = [x.text for x in segs if x.text.strip()]
    if not eng or not texts or p.data.get("film_profile", "").strip():
        return False
    target = target_lang(p, settings)
    text, terms = skills.make_profile(LLMClient(settings, eng), texts, langs.INFO[target]["name"].lower(),
                                      skills.skill_text(settings))
    gkey = glossary_key(target)
    g = parse_glossary(p.data.get(gkey, ""))
    for k, v in terms.items():
        g.setdefault(k, v)
    p.data[gkey] = glossary_text(g)
    p.data["film_profile"] = text
    return True


def name_pass(p: Project, segs, wav: Path, engine: str, language: str, settings: dict,
              progress: Prog | None = None, cancel=None) -> dict:
    """Tên riêng (2.1.0): chưa có tên nào (bảng thuật ngữ / hồ sơ phim) mà có AI hỗ trợ nghe -> AI lập hồ sơ phim từ
    lời vừa nghe; câu có chữ đọc giống 1 tên (cùng âm) mà viết khác -> Qwen3-ASR nghe lại câu đó kèm gợi ý tên (nhận
    khi lời mới có đúng tên, phần còn lại gần như giữ nguyên); còn lại -> sửa theo âm đọc (names.fix, chặt hơn).
    Trả {names, profile, listened, heard, fixed}."""
    from difflib import SequenceMatcher

    from . import asr_qwen, dual, names

    st = {"names": 0, "profile": False, "listened": 0, "heard": 0, "fixed": 0}
    nm = names.names_of(p, settings)
    if not nm:
        try:
            st["profile"] = _learn_profile(p, segs, settings)
        except ff.Cancelled:
            raise
        except Exception as e:  # noqa: BLE001 – mất mạng / hết lượt: bỏ qua bước này
            log_error("AI lập hồ sơ phim khi nghe")
            st["error"] = f"AI lập hồ sơ phim: {err_text(e)[:120]}"
        nm = names.names_of(p, settings)
    st["names"] = len(nm)
    if not nm:
        return st
    rows = [i for i, x in enumerate(segs) if names.candidates(x.text, nm, loose=True)]
    if rows and engine == "qwen3" and asr_qwen.available():
        r = asr_qwen.asr_worker("qwen3", settings).call(
            {"cmd": "asr", "wav": str(Path(wav).resolve()), "language": language, "align": False,
             "prompt": names.context_text(p, settings, nm),
             "spans": [[max(0.0, segs[i].start - 0.2), segs[i].end + 0.2] for i in rows]}, progress, cancel)
        st["listened"] = len(rows)
        realign = []
        for i, it in zip(rows, r.get("items") or []):
            old = segs[i].text
            hc, oc = dual.core(_heard(it.get("text", ""), language)), dual.core(old)
            got = [n for n in nm if n in hc and n not in oc]
            if got and hc != oc and SequenceMatcher(None, hc, oc).ratio() >= 0.6:
                segs[i].text = dual.merge_alt(old, hc)
                segs[i].extra.setdefault("src_before", old)
                segs[i].extra["name_fix"] = got
                if _cjk_count(segs[i].text) != _cjk_count(old):
                    segs[i].extra.pop("ct", None)
                    realign.append(segs[i])
                st["heard"] += 1
        if realign:
            asr_qwen.align_qwen(realign, wav, language, settings, cancel=cancel)
    st["fixed"] = len(names.fix(segs, nm))
    return st


def dual_engine(primary: str, language: str, settings: dict | None = None) -> tuple[str | None, str]:
    """Bộ nghe thứ 2 cho "Nghe 2 bộ rồi so": bộ còn lại trong Qwen3-ASR / FireRedASR2 (bộ chính là OCR / SenseVoice /
    Whisper -> FireRedASR2 nếu đã tải, không thì Qwen3-ASR). Trả (bộ | None, tên / lý do)."""
    from . import asr_firered, asr_qwen

    order = ["qwen3"] if primary == "firered" else ["firered"] if primary == "qwen3" else ["firered", "qwen3"]
    why = []
    for e in order:
        if e == "firered":
            if language not in asr_firered.LANGS:
                why.append("FireRedASR2 không nghe tiếng này")
            elif not asr_firered.ready():
                why.append(f"FireRedASR2: {asr_firered.why_unavailable()}")
            else:
                return e, "FireRedASR2"
        elif asr_qwen.available():
            return e, "Qwen3-ASR"
        else:
            why.append(f"Qwen3-ASR: {asr_qwen.why_unavailable()}")
    return None, "; ".join(why)


PICK_BATCH = 40


def dual_listen(p: Project, segs, wav: Path, primary: str, language: str, settings: dict,
                progress: Prog | None = None, cancel=None) -> dict:
    """Nghe 2 bộ rồi so (2.1.0): bộ thứ 2 nghe lại đúng các đoạn của câu -> so chữ từng câu (`dual.assign`). Câu khác
    nhau: extra["asr_alt"] = chữ bộ 2; có AI hỗ trợ nghe -> AI chọn A / B / ghép (chắc chắn -> sửa câu, extra
    ["asr_pick"]); không chắc / không có AI -> extra["asr_unsure"] (chữ gốc tô xanh, nghe lại). Trả thống kê."""
    from . import asr_qwen, dual
    from .translate import ENGINE_NAMES, LLMClient

    second, name = dual_engine(primary, language, settings)
    st = {"second": second or "", "checked": 0, "differ": 0, "ai_fixed": 0, "ai_kept": 0, "unsure": 0}
    if not second:
        st["error"] = name
        return st
    total = float(p.data.get("info", {}).get("duration", 0) or 0)
    spans = dual.alt_spans(segs, total=total)
    if not spans:
        return st
    try:
        r = asr_qwen.asr_worker(second, settings).call(
            {"cmd": "asr", "wav": str(Path(wav).resolve()), "language": language, "align": False,
             "prompt": asr_qwen.hotwords(settings), "spans": [[a, b] for a, b, _ in spans]},
            _sub(progress, 0.0, 0.75), cancel)
        items = [dict(it, text=_heard(it.get("text", ""), language)) for it in (r.get("items") or [])]
        alts = dual.assign(segs, spans, items)
        st["checked"] = len(alts)
        rows = [i for i, a in sorted(alts.items()) if dual.differs(segs[i].text, a)]
        for i in rows:
            segs[i].extra["asr_alt"] = alts[i]
            segs[i].extra["asr_alt_eng"] = second
            segs[i].extra.pop("asr_same", None)
        for i, a in alts.items():   # bộ thứ 2 nghe ra giống hệt -> câu đã được xác nhận (không bị coi là không chắc)
            if a and i not in rows:
                segs[i].extra["asr_same"] = "dual"
        st["differ"] = len(rows)
        picked: dict = {}
        eng, why = join_engine(settings, "asr_ai")
        ask = [i for i in rows if alts[i]]   # bộ 2 không nghe ra chữ nào -> chỉ đánh dấu (có thể là nhạc bị chép thành lời)
        if eng and ask:
            llm = LLMClient(settings, eng)
            try:
                for a in range(0, len(ask), PICK_BATCH):
                    part = ask[a:a + PICK_BATCH]
                    if progress:
                        progress(0.75 + 0.2 * a / len(ask), f"AI chọn bản đúng {a + len(part)}/{len(ask)} câu khác nhau")
                    reply = llm.chat(dual.PICK_PROMPT, dual.pick_payload(segs, part, alts, p.data.get("film_profile", "")),
                                     max_tokens=6000, temperature=0.1)
                    picked.update(dual.parse_pick(reply, segs, part, alts))
                st["ai"] = ENGINE_NAMES.get(eng, eng)
            except ff.Cancelled:
                raise
            except Exception as e:  # noqa: BLE001 – mất mạng / hết lượt: câu còn lại để người dùng nghe lại
                log_error("AI chọn bản nghe đúng")
                st["ai_error"] = err_text(e)[:120]
        elif ask:
            st["ai_off"] = why
        realign = []
        for i in rows:
            pk = picked.get(i)
            if not (pk and pk[2]):
                segs[i].extra["asr_unsure"] = True
                st["unsure"] += 1
                continue
            choice, new_core, _sure, reason = pk
            segs[i].extra["asr_pick"] = choice + (f": {reason}" if reason else "")
            segs[i].extra.pop("asr_unsure", None)
            if choice == "A" or new_core == dual.core(segs[i].text):
                st["ai_kept"] += 1
                continue
            old = segs[i].text
            segs[i].text = dual.merge_alt(old, new_core)
            segs[i].extra.setdefault("src_before", old)
            if _cjk_count(segs[i].text) != _cjk_count(old):
                segs[i].extra.pop("ct", None)
                realign.append(segs[i])
            st["ai_fixed"] += 1
        if realign:   # câu đổi số chữ -> đo lại mốc từng chữ bằng chính bộ đang mở
            asr_qwen.align_qwen(realign, wav, language, settings, cancel=cancel, engine=second)
    finally:
        asr_qwen.release()
    return st


def dual_note(st: dict | None) -> str:
    """1 dòng kết quả "Nghe 2 bộ rồi so" cho thông báo."""
    if not st:
        return ""
    if st.get("error") and not st.get("checked"):
        return f"⚠ chưa nghe 2 bộ được ({st['error']})"
    name = {"firered": "FireRedASR2", "qwen3": "Qwen3-ASR"}.get(st.get("second", ""), "bộ 2")
    if not st.get("differ"):
        return f"nghe lại bằng {name}: {st.get('checked', 0)} câu khớp nhau"
    t = f"nghe lại bằng {name}: {st['differ']}/{st.get('checked', 0)} câu khác nhau"
    if st.get("ai_fixed") or st.get("ai_kept"):
        t += f" – AI {st.get('ai', '')} sửa {st.get('ai_fixed', 0)}, giữ {st.get('ai_kept', 0)}".replace("  ", " ")
    if st.get("unsure"):
        t += f", {st['unsure']} câu cần nghe lại (chữ gốc màu xanh – tab Phụ đề → Tích câu 2 bộ nghe khác nhau)"
    if st.get("ai_error"):
        t += f" · ⚠ AI lỗi ({st['ai_error']})"
    elif st.get("ai_off") and st.get("unsure"):
        t += f" · chưa có AI chọn bản đúng ({st['ai_off']})"
    return t


def name_note(st: dict | None) -> str:
    if not st:
        return ""
    if st.get("error") and not st.get("names"):
        return f"⚠ {st['error']}"
    if not st.get("names"):
        return ""
    parts = []
    if st.get("profile"):
        parts.append("AI lập hồ sơ tên nhân vật")
    if st.get("heard"):
        parts.append(f"nghe lại ra đúng tên {st['heard']}/{st.get('listened', 0)} câu")
    if st.get("fixed"):
        parts.append(f"sửa {st['fixed']} chỗ đọc giống tên")
    return ("tên riêng: " + ", ".join(parts)) if parts else ""


def _asr_context(p: Project, settings: dict) -> None:
    """Lời nhắc của bộ nghe (2.1.8 = như 2.0.99): mặc định chỉ tên trong Bảng thuật ngữ ở Cài đặt (asr_qwen.hotwords).
    Bật "Gợi ý tên riêng cho bộ nghe" -> thêm tên của dự án (bảng tên riêng + hồ sơ phim). KHÔNG gửi tiêu đề / mô tả /
    thẻ video: khúc chỉ có nhạc thì Qwen3-ASR chép nguyên văn chúng thành phụ đề (2.1.0–2.1.7: 169 câu / 3058)."""
    settings.pop("_asr_context", None)
    if settings.get("asr_context"):
        from .names import context_text

        if txt := context_text(p, settings):
            settings["_asr_context"] = txt


def hybrid_listen(p: Project, segs, settings: dict, language: str, engine: str, denoise: bool = False,
                  progress: Prog | None = None, cancel=None) -> list:
    """OCR + nghe (2.1.0): chữ lấy từ phụ đề Trung in sẵn (đúng tên riêng, chữ), giờ đo trên giọng nói (căn từng chữ
    của chính câu OCR vào âm thanh – Qwen3-ForcedAligner / nhánh CTC FireRedASR2), đoạn có tiếng nói mà phụ đề không
    có câu nào (OCR đọc sót, câu không có phụ đề) -> bộ nghe nghe thêm (extra["from_asr"]). Bật "Nghe 2 bộ rồi so" thì
    bộ nghe đọc lại từng câu OCR để bắt chữ OCR đọc sai (AI chọn)."""
    from . import asr_firered, asr_qwen

    st = {"lines": len(segs), "timed": 0, "added": 0}
    p.data.pop("asr_dual", None)
    audio_in, wav = _listen_audio(p, settings, denoise, _sub(progress, 0.0, 0.5), cancel)
    _asr_context(p, settings)
    for s in segs:
        s.extra["ocr_time"] = [s.start, s.end]
    al = "qwen3" if asr_qwen.available() and (engine == "qwen3" or use_qwen_align("qwen3", settings)) else \
        "firered" if asr_firered.ready() else ""
    try:
        if al and language in ("zh", "yue", "auto"):
            try:
                asr_qwen.align_qwen(segs, wav, language, settings, _sub(progress, 0.5, 0.75), cancel, engine=al,
                                    pad=0.6)
            except ff.Cancelled:
                raise
            except Exception as e:  # noqa: BLE001 – không căn được: giữ giờ hiện chữ
                log_error("OCR + nghe: căn giờ câu OCR")
                st["error"] = err_text(e)[:120]
        for s in segs:
            ct = s.extra.get("ct")
            if ct:
                s.start, s.end = round(max(0.0, ct[0][0] - 0.05), 3), round(ct[-1][1] + 0.05, 3)
                st["timed"] += 1
        from .align import fix_overlaps

        segs = fix_overlaps(sorted(segs, key=lambda x: x.start))
        if audio_in != Path(p.source):   # câu phụ đề không có mà có tiếng nói -> bộ nghe nghe thêm
            ears = engine if engine in ("qwen3", "firered") else (al or "qwen3")
            before = {id(x) for x in segs}
            rst: dict = {}
            segs, st["added"] = rescue_segments(segs, wav, ears, language, settings, False,
                                                _sub(progress, 0.75, 0.95), cancel, keep_lines=True, stats=rst)
            if rst.get("lowconf"):
                p.data["asr_rescue_lowconf"] = rst["lowconf"]
            for x in segs:
                if id(x) not in before:
                    x.extra["from_asr"] = True
        p.data["hybrid"] = st
        p.data["asr_engine_used"] = engine
        p.data["language"] = language
        if segs and settings.get("asr_dual"):
            try:
                p.data["asr_dual"] = dual_listen(p, segs, wav, "ocr", language, settings, _sub(progress, 0.95, 0.99),
                                                 cancel)
            except ff.Cancelled:
                raise
            except Exception as e:  # noqa: BLE001
                log_error("OCR + nghe: so chữ OCR với bộ nghe")
                p.data["asr_dual"] = {"error": err_text(e)[:160]}
        if audio_in != Path(p.source):
            if settings.get("song_detect", True):
                try:
                    from .songs import mark_sung

                    p.data["songs_found"] = mark_sung(segs, wav)
                except Exception:  # noqa: BLE001
                    log_error("Đoán đoạn hát")
    finally:
        asr_qwen.release()
    return segs


def hybrid_note(st: dict | None) -> str:
    if not st:
        return ""
    t = f"OCR + nghe: {st.get('timed', 0)}/{st.get('lines', 0)} câu phụ đề căn giờ theo giọng nói"
    if st.get("added"):
        t += f", bộ nghe thêm {st['added']} câu phụ đề không có (chữ cam nhạt – nên xem lại)"
    if st.get("error"):
        t += f" · ⚠ căn giờ lỗi ({st['error']})"
    return t


def _listen_audio(p: Project, settings: dict, denoise: bool = False, progress: Prog | None = None,
                  cancel=None) -> tuple[Path, Path]:
    """Âm thanh để nghe: tách giọng khỏi nhạc nền (nếu bật) -> wav 16 kHz mono theo giờ của hình. Trả (file đã nghe
    – bản giọng đã tách hoặc video gốc, wav 16 kHz). progress: 0 -> 0,33 của bước nghe."""
    from . import separate as sepmod

    src = p.source
    info_dur = p.data.get("info", {}).get("duration", 0)
    # 1) tách giọng trước để nhận dạng chính xác hơn khi có nhạc nền (bản tách dùng luôn cho tab Âm thanh / Lồng
    # tiếng). Lỗi / chưa cài -> vẫn nghe trên tiếng gốc nhưng BÁO RÕ (trước đây bỏ qua lặng lẽ -> tab Âm thanh
    # báo "chưa tách" mà người dùng không biết vì sao)
    audio_in: Path = Path(src)
    p.data.pop("sep_warning", None)
    if settings.get("sep_before_asr", True) and not sepmod.available(settings.get("sep_model")):
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
    if not wav.exists() or wav.stat().st_size < 1000 or denoise or p.data.get("timeline") != ff.TIMELINE:
        # theo đúng giờ của hình (2.0.92); bản 16 kHz cũ (giải mã nối liền) thì làm lại
        args = ["-i", str(audio_in), "-vn", "-ac", "1", "-ar", "16000"] + ff.af_args(
            ff.audio_sync(audio_in, 16000),
            "highpass=f=80,lowpass=f=8000,afftdn=nf=-25,dynaudnorm=f=200" if denoise else "")
        args += ["-c:a", "pcm_s16le", str(wav)]
        ff.run(args, info_dur, _sub(progress, 0.3, 0.33), cancel, "Đang chuẩn bị âm thanh")
    return audio_in, wav


def _orig_wav16(p: Project, cancel=None) -> Path:
    """Tiếng gốc (chưa tách giọng) 16 kHz mono theo giờ của hình – để nghe lại câu không chắc trên tiếng gốc."""
    wav = p.work / "audio16k.wav"
    if wav.exists() and wav.stat().st_size >= 1000 and p.data.get("timeline") == ff.TIMELINE:
        return wav
    src = p.source
    ff.run(["-i", str(src), "-vn", "-ac", "1", "-ar", "16000"] + ff.af_args(ff.audio_sync(src, 16000)) +
           ["-c:a", "pcm_s16le", str(wav)], p.data.get("info", {}).get("duration", 0), None, cancel,
           "Đang chuẩn bị tiếng gốc")
    return wav


def recognize(p: Project, method: str = "asr", language: str = "zh", region: dict | None = None,
              ocr_fps: float = 4.0, settings: dict | None = None, progress: Prog | None = None, cancel=None,
              denoise: bool = False, engine: str | None = None,
              on_draft: Callable[[list[Segment]], None] | None = None) -> int:
    """Lấy phụ đề gốc.  method: asr (giọng nói) | ocr (chữ trên video).
    engine (asr): qwen3 | firered | sensevoice | paraformer | whisper.
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
    settings = dict(settings or load_settings())   # bản sao: khoá tạm "_asr_context" không lọt vào Cài đặt
    src = p.source
    engine = engine or settings.get("asr_engine", "qwen3")
    want_spk = bool(settings.get("diarize", True))
    p.data.pop("asr_rescue_lowconf", None)
    p.data.pop("breaths_marked", None)
    p.data.pop("pauses_opened", None)
    p.data.pop("ct_revoiced", None)
    if method in ("ocr", "hybrid"):
        opr = progress if method == "ocr" else _sub(progress, 0.0, 0.4)
        from .ocr import detect_subtitle_region, expand_region, extract_hardsubs

        if region:   # vùng khoanh ở tab Xoá phụ đề (vừa khít 1 dòng) -> mở rộng: hết ngang, thêm dòng trên / dưới
            scan = expand_region(region)
        else:
            if progress:
                progress(0.0, "Đang dò vùng phụ đề (kỹ ở 5 giây đầu và 5 giây cuối)…")
            scan = detect_subtitle_region(src, _sub(opr, 0.0, 0.05))
        boxes = p.work / "ocr_boxes.json"
        stats: dict = {}
        segs = extract_hardsubs(src, scan, fps=float(ocr_fps or 4), progress=_sub(opr, 0.05, 0.85),
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
        asr_end = 0.85
        if method == "hybrid":   # chữ lấy từ phụ đề in sẵn, giờ đo trên giọng nói, câu OCR sót lấy từ bộ nghe
            segs = hybrid_listen(p, segs, settings, language, engine, denoise, _sub(progress, 0.4, 0.92), cancel)
            asr_end = 0.92
    else:
        from . import asr_funasr

        info_dur = p.data.get("info", {}).get("duration", 0)
        audio_in, wav = _listen_audio(p, settings, denoise, progress, cancel)
        _asr_context(p, settings)
        asr_end = 0.78 if want_spk else 0.92
        pr = _sub(progress, 0.33, asr_end)
        segs = None
        streamed = False
        conf_pts: list = []    # (giờ chữ, xác suất) từng chữ đã nghe – độ chắc từng câu, nghe lại câu không chắc
        p.data.pop("asr_warning", None)
        p.data.pop("asr_relisten", None)
        p.data.pop("asr_recheck", None)
        if engine == "firered":
            from . import asr_firered, asr_qwen

            if language not in asr_firered.LANGS:
                p.data["asr_warning"] = "FireRedASR2 chỉ nghe tiếng Trung / Quảng Đông / Anh – đã dùng Qwen3-ASR."
                engine = "qwen3"
            elif not asr_firered.ready():
                p.data["asr_warning"] = f"Chưa dùng được FireRedASR2 ({asr_firered.why_unavailable()}) – đã dùng Qwen3-ASR."
                engine = "qwen3"
            else:
                try:
                    rl: dict = {}
                    segs = asr_qwen.transcribe_qwen(wav, settings, language, pr, cancel,
                                                    on_chunk=draft if on_draft else None, stats=rl, engine="firered",
                                                    conf_out=conf_pts)
                    p.data["asr_relisten"] = rl
                    streamed = True
                except ff.Cancelled:
                    asr_qwen.release()
                    raise
                except Exception as e:  # noqa: BLE001 – lỗi nạp / thiếu bộ nhớ GPU… -> Qwen3-ASR
                    log_error("FireRedASR2")
                    asr_qwen.release()
                    p.data["asr_warning"] = f"FireRedASR2 lỗi ({str(e)[:160]}) – đã dùng Qwen3-ASR thay thế."
                    if progress:
                        progress(0.33, "FireRedASR2 lỗi → chuyển sang Qwen3-ASR")
                    engine = "qwen3"
        if engine == "qwen3" and segs is None:
            from . import asr_qwen

            if not asr_qwen.available():
                p.data["asr_warning"] = f"Chưa dùng được Qwen3-ASR ({asr_qwen.why_unavailable()}) – đã dùng SenseVoice."
                engine = "sensevoice"
            else:
                try:
                    rl: dict = {}
                    segs = asr_qwen.transcribe_qwen(wav, settings, language, pr, cancel,
                                                    on_chunk=draft if on_draft else None, stats=rl, conf_out=conf_pts)
                    p.data["asr_relisten"] = rl
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
            rst: dict = {}
            segs, p.data["asr_rescued"] = rescue_segments(segs, wav, engine, language, settings, spk_from_asr,
                                                          _sub(progress, asr_end - 0.03, asr_end), cancel, stats=rst)
            if rst.get("lowconf"):
                p.data["asr_rescue_lowconf"] = rst["lowconf"]
        # 2a') bộ căn đặt sai giờ cả khúc (nói chậm / ngắt quãng: chữ dồn vào chỗ lặng, phần nói thật không chữ nào
        # phủ) -> so với độ to giọng đã tách, chia lại chữ theo các cụm có tiếng (2.1.20)
        if segs and audio_in != Path(src) and settings.get("snap_edges", True):
            try:
                env, ehop, esrc = voice_env(p, None, cancel)
                if env is not None and esrc == "vocals":
                    from .snap import retime_by_voice

                    p.data["ct_revoiced"] = len(retime_by_voice(segs, env, ehop))
            except ff.Cancelled:
                raise
            except Exception:  # noqa: BLE001 – bước phụ
                log_error("Kiểm tra mốc chữ theo giọng")
        # 2b) căn thời gian từng chữ + tách câu trọn nghĩa (cuối câu / chỗ nghỉ thật, phẩy nói liền không tách)
        p.data.pop("ct_check", None)
        try:
            if settings.get("align_words", True) and segs:
                segs = align_segments(segs, wav, language, settings, _sub(progress, asr_end, asr_end + 0.05),
                                      cancel, engine=engine)
                if engine in ("qwen3", "firered") or use_qwen_align(engine, settings):
                    p.data["ct_check"] = _verify_ct_safe(p, segs, wav, language, settings, engine, cancel)
            # 2b') độ chắc từng câu (xác suất từng chữ bộ nghe chọn) -> nghe lại câu không chắc với khúc rộng hơn, cả
            # trên tiếng gốc; "Nghe kỹ": mọi câu nghe lần 2 (2.1.15)
            if segs and conf_pts:
                from . import recheck

                recheck.assign_conf(segs, conf_pts)
                if engine in ("qwen3", "firered") and (settings.get("asr_recheck", True) or settings.get("asr_overlap")):
                    try:
                        orig = _orig_wav16(p, cancel) if audio_in != Path(src) else None
                        p.data["asr_recheck"] = recheck.recheck_lines(
                            segs, wav, orig, engine, language, settings, info_dur,
                            _sub(progress, asr_end + 0.05, asr_end + 0.07), cancel)
                    except ff.Cancelled:
                        raise
                    except Exception as e:  # noqa: BLE001 – bước phụ: lỗi thì giữ kết quả nghe
                        log_error("Nghe lại câu không chắc")
                        p.data["asr_recheck"] = {"error": err_text(e)[:160]}
        finally:
            from .asr_qwen import release

            release()   # trả bộ nhớ GPU trước bước phân biệt người nói
        # 2c) nối dòng bị cắt ngang câu (cắt giữa từ, mẩu cụt, câu chưa hết) rồi tách lại đúng chỗ – không cần AI
        p.data["asr_joined"] = 0
        if segs and language in ("zh", "yue", "auto"):
            from .align import join_broken, split_params

            segs, p.data["asr_joined"] = join_broken(segs, split_params(settings),
                                                     int(settings.get("asr_max_chars", 28)), info_dur)
        # 2c') AI nối các dòng thực ra là 1 câu mà luật không đoán được ("这笔账。" | "你自己算。") – AI chọn ở Cài đặt
        p.data.pop("ai_joined", None)
        p.data.pop("ai_join_note", None)
        if segs and language in ("zh", "yue", "auto"):
            eng, why = join_engine(settings)
            if eng:
                try:
                    segs, jst = ai_join_lines(segs, settings, eng,
                                              _sub(progress, min(0.95, asr_end + 0.07), min(0.97, asr_end + 0.1)),
                                              cancel)
                    p.data["ai_joined"] = jst["merged"]
                except ff.Cancelled:
                    raise
                except Exception as e:  # noqa: BLE001 – mất mạng / hết lượt / sai key: giữ kết quả nghe, chỉ báo
                    log_error("AI nối câu")
                    p.data["ai_join_note"] = f"bỏ qua AI nối câu ({err_text(e)[:120]})"
            elif settings.get("ai_join_engine", "auto") not in ("auto", "off"):
                p.data["ai_join_note"] = f"chưa chạy AI nối câu – {why}"
        # 2f) tên riêng: câu có chữ đọc giống tên nhân vật -> nghe lại kèm gợi ý tên / sửa theo âm đọc (2.1.0)
        p.data.pop("name_pass", None)
        if segs and language in ("zh", "yue", "auto") and settings.get("asr_name_pass", True):
            try:
                p.data["name_pass"] = name_pass(p, segs, wav, engine, language, settings,
                                                _sub(progress, min(0.95, asr_end + 0.1), min(0.96, asr_end + 0.11)),
                                                cancel)
            except ff.Cancelled:
                raise
            except Exception as e:  # noqa: BLE001 – bước phụ
                log_error("Nghe lại tên riêng")
                p.data["name_pass"] = {"error": err_text(e)[:160]}
        # 2g) nghe 2 bộ rồi so: bộ thứ 2 nghe lại đúng các câu -> câu khác nhau: AI chọn / tô xanh để nghe lại
        p.data.pop("asr_dual", None)
        if segs and settings.get("asr_dual"):
            try:
                p.data["asr_dual"] = dual_listen(p, segs, wav, engine, language, settings,
                                                 _sub(progress, min(0.96, asr_end + 0.11), min(0.97, asr_end + 0.13)),
                                                 cancel)
            except ff.Cancelled:
                raise
            except Exception as e:  # noqa: BLE001 – bước phụ
                log_error("Nghe 2 bộ rồi so")
                p.data["asr_dual"] = {"error": err_text(e)[:160]}
        # 2g') đầu / cuối câu bị cắt khi nhân vật còn đang nói (nói chậm, ngừng nhiều): giọng đã tách còn tiếng ngay
        # sau cuối / trước đầu câu -> bộ nghe nghe lại xác nhận -> nới mép + đo lại mốc chữ (2.1.21)
        p.data.pop("edge_fix", None)
        if segs and audio_in != Path(src) and settings.get("asr_edge_fix", True) and language in ("zh", "yue", "auto"):
            eng_e, _why = relisten_engine(p, settings)
            if eng_e:
                try:
                    env, ehop, esrc = voice_env(p, None, cancel)
                    if env is not None and esrc == "vocals":
                        segs = normalize(segs)
                        p.data["edge_fix"] = fix_line_edges(
                            segs, wav, eng_e, language, settings, env, ehop, None, info_dur,
                            _sub(progress, min(0.97, asr_end + 0.13), min(0.98, asr_end + 0.14)), cancel)
                except ff.Cancelled:
                    raise
                except Exception as e:  # noqa: BLE001 – bước phụ
                    log_error("Sửa đầu / cuối câu bị cắt")
                    p.data["edge_fix"] = {"error": err_text(e)[:160]}
                finally:
                    from .asr_qwen import release

                    release()
        if engine in ("sensevoice", "qwen3", "firered"):
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
        # 2e) câu "nghe nhầm": nhạc / tiếng động bị chép thành lời -> đo cao độ giọng thật từng người, đánh dấu câu lạ
        p.data.pop("noise_found", None)
        if segs and audio_in != Path(src) and settings.get("pitch_check", True):
            try:
                from .songs import read_wav16
                from .voicecheck import check_lines

                res = check_lines(segs, read_wav16(wav), float(settings.get("pitch_tol", 4.0)),
                                  bool(settings.get("pitch_trim", True)))
                p.data["noise_found"] = len(res["suspect"])
                p.data["voice_trimmed"] = res["trimmed"]
            except Exception:  # noqa: BLE001 – bước phụ
                log_error("Lọc câu nghe nhầm theo cao độ")
        # 2h0) chỗ nhân vật ngừng giữa câu mà bộ căn thời gian kéo chữ qua (đếm chậm, người khác chen ngang): đo độ to
        # giọng đã tách -> mốc chữ có khe đúng đoạn lặng (2.1.20) – để bước 2h thêm dấu phẩy, giọng lồng nghỉ đúng chỗ
        if segs and audio_in != Path(src) and settings.get("snap_edges", True):
            try:
                env, ehop, esrc = voice_env(p, None, cancel)
                if env is not None and esrc == "vocals":
                    from .snap import open_pauses

                    p.data["pauses_opened"] = len(open_pauses(segs, env, ehop,
                                                              float(settings.get("dub_pause_min", 0.3))))
            except ff.Cancelled:
                raise
            except Exception:  # noqa: BLE001 – bước phụ
                log_error("Mở chỗ ngừng giữa câu theo giọng")
        # 2h) chỗ nhân vật ngắt hơi giữa câu mà bộ nghe không đặt dấu câu -> "，" (dịch giữ vế, giọng lồng nghỉ đúng chỗ)
        if segs and language in ("zh", "yue", "auto") and settings.get("breath_marks", True):
            from .align import mark_breaths

            p.data["breaths_marked"] = mark_breaths(segs, float(settings.get("dub_pause_min", 0.3)))
    if method not in ("ocr", "hybrid") and language in ("zh", "yue", "auto"):
        from .align import drop_false_starts

        # nói vấp "我不管闲，我不管闲事" -> "我不管闲事" (không thì dịch / lồng tiếng thành 2 lần)
        p.data["asr_stammer"] = sum(drop_false_starts(x) for x in segs)
    if method != "ocr":
        p.data["asr_repeat"] = mark_repeats(segs)   # câu vẫn còn lặp lời / 2 câu liền giống nhau -> tô màu để kiểm
    # 2i) lớp lọc CUỐI CÙNG trước khi dịch (2.1.19): bỏ hẳn câu bộ nghe không chắc – sau mọi bước nghe lại / 2 bộ nghe
    p.data.pop("unsure_dropped", None)
    p.data.pop("unsure_removed", None)
    if segs and method != "ocr" and settings.get("asr_drop_unsure"):
        segs = drop_unsure(p, segs, settings)
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
    p.data["timeline"] = ff.TIMELINE     # mốc câu tính trên âm thanh giải mã theo giờ của hình (2.0.92)
    for k in ("asr_cleaned", "ai_filtered", "ai_reviewed", "cleanup_backup", "snap_stats"):   # câu mới -> làm lại
        p.data.pop(k, None)
    if method != "hybrid":
        p.data.pop("hybrid", None)
    if method in ("ocr", "hybrid"):
        p.data.pop("name_pass", None)
        p.data.pop("asr_joined", None)
        p.data.pop("asr_relisten", None)
        p.data.pop("asr_recheck", None)
        p.data.pop("ai_joined", None)
    if method in ("ocr", "hybrid"):
        p.data.pop("asr_stammer", None)
    if method == "ocr":
        p.data.pop("asr_warning", None)
        p.data.pop("asr_repeat", None)
        p.data.pop("songs_found", None)
        p.data.pop("asr_dual", None)
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
    # 4) bám mép câu theo giọng đã tách: đầu / cuối câu đúng lúc nhân vật bắt đầu / thôi nói (2.0.96)
    if p.segments and settings.get("snap_edges", True) and stems(p):
        try:
            snap_edges(p, settings, relayout_dub=False, cancel=cancel)
        except ff.Cancelled:
            raise
        except Exception:  # noqa: BLE001 – bước phụ
            log_error("Bám mép câu theo giọng")
    from .asr_qwen import clear_progress

    clear_progress(p.work)   # nghe xong trọn vẹn -> bỏ tiến độ nghe dở (lần sau nghe lại từ đầu)
    return len(p.segments)


def _drop_char_times(p: Project) -> None:
    """Bỏ cờ tạm của bước nhận dạng + mốc từng chữ không còn khớp lời (mốc khớp lời được giữ – 2.0.96)."""
    from .align import drop_char_times

    segs = p.segments
    before = [(dict(s.extra).get("ct") is not None, "chunked" in s.extra) for s in segs]
    drop_char_times(segs)
    if before != [(s.extra.get("ct") is not None, "chunked" in s.extra) for s in segs]:
        p.segments = segs
        p.save()


VOICE_ENV = "voice_10ms"   # độ to giọng đã tách mỗi 10 ms (bám mép câu, sóng âm timeline)


def voice_env(p: Project, progress: Prog | None = None, cancel=None) -> tuple:
    """Độ to (dBFS) mỗi 10 ms của giọng đã tách (không có thì tiếng gốc – lẫn nhạc, kém chính xác hơn).
    Trả (mảng, hop, "vocals" | "mix") hoặc (None, hop, "") nếu không đo được."""
    from . import silence
    from .snap import HOP

    st = stems(p)
    src = st.get("vocals") or (str(p.source) if p.data.get("source") and p.source.exists() else "")
    if not src:
        return None, HOP, ""
    db = silence.loudness(src, p.work, progress, cancel, float(p.data.get("info", {}).get("duration", 0) or 0),
                          name=VOICE_ENV if st else "loud_10ms", hop=HOP)
    return db, HOP, "vocals" if st else "mix"


def snap_edges(p: Project, settings: dict | None = None, rows: list[int] | None = None, remeasure: bool = False,
               relayout_dub: bool = True, progress: Prog | None = None, cancel=None, fix_edges: bool = False) -> dict:
    """Bám mép câu theo giọng (nút "Căn mép câu theo giọng", cuối "1. Nghe & tách câu" / Căn khớp thoại): đầu / cuối
    câu về đúng lúc nhân vật bắt đầu / thôi nói (`snap.snap`). remeasure: đo lại mốc từng chữ bằng Qwen3 trước (dự án
    cũ không lưu mốc từng chữ). fix_edges (nút): câu bị cắt đầu / cuối khi nhân vật còn nói -> nghe lại xác nhận rồi
    nới mép (`fix_line_edges`, 2.1.21). Đã lồng tiếng -> xếp lại bản lồng (không đọc lại). Trả thống kê của snap +
    "src"."""
    from . import snap

    settings = settings or load_settings()
    segs = normalize(p.segments)
    if not segs:
        raise ValueError("Chưa có phụ đề")
    measured = 0
    if remeasure:
        from . import asr_firered, asr_qwen

        # đo bằng bộ nghe đã nghe dự án này; FireRedASR2 (nhánh CTC) khi chưa dùng được Qwen3
        meng = "firered" if asr_firered.ready() and (p.data.get("asr_engine_used") == "firered"
                                                       or not asr_qwen.available()) else "qwen3"
        if meng == "qwen3" and not asr_qwen.available():
            raise RuntimeError(f"Chưa đo lại được mốc từng chữ: {asr_qwen.why_unavailable()}")
        wav = _voice_wav16(p, _sub(progress, 0.0, 0.1), cancel)
        pick = set(range(len(segs))) if rows is None else set(rows)
        for i in pick:
            if 0 <= i < len(segs):
                segs[i].extra.pop("ct", None)
        try:
            measured = asr_qwen.align_qwen([segs[i] for i in sorted(pick) if 0 <= i < len(segs)], wav,
                                           p.data.get("language", "zh") or "zh", settings, _sub(progress, 0.1, 0.7),
                                           cancel, engine=meng)
        finally:
            asr_qwen.release()
    env, hop, src = voice_env(p, _sub(progress, 0.7 if remeasure else 0.0, 0.85), cancel)
    if env is None or not len(env):
        raise RuntimeError("Chưa có âm thanh để đo (video gốc không còn ở chỗ cũ?)")
    before = [(s.start, s.end) for s in segs]
    edges: dict = {}
    if fix_edges and src == "vocals" and source_lang(p) in ("zh", "yue", "auto") \
            and settings.get("asr_edge_fix", True):
        eng_e, why = relisten_engine(p, settings)
        if eng_e:
            try:
                wav = _voice_wav16(p, None, cancel)
                edges = fix_line_edges(segs, wav, eng_e, p.data.get("language", "zh") or "zh", settings, env, hop,
                                       rows, float(p.data.get("info", {}).get("duration", 0) or 0),
                                       _sub(progress, 0.0, 0.7), cancel)
            finally:
                from . import asr_qwen

                asr_qwen.release()
        else:
            edges = {"error": why}
    # mốc chữ bộ căn đặt sai giờ (rơi vào chỗ lặng, phần nói thật không chữ nào phủ) -> chia lại theo giọng (2.1.20)
    revoiced = snap.retime_by_voice(segs, env, hop, rows) if src == "vocals" else []
    stretched = snap.fix_stretched(segs, env, hop, rows) if src == "vocals" else 0
    # chỗ nhân vật ngừng giữa câu mà mốc chữ không có khe -> mở khe đúng đoạn lặng + dấu phẩy (2.1.20): giọng lồng nghỉ
    # đúng chỗ khi xếp lại (câu dịch đã có dấu phẩy ở vế tương ứng – không cần dịch lại)
    opened = snap.open_pauses(segs, env, hop, float(settings.get("dub_pause_min", 0.3)), rows) \
        if src == "vocals" else []
    marks = sorted(set(opened) | set(revoiced) | set(edges.get("rows") or []))
    if marks and settings.get("breath_marks", True) and source_lang(p) in ("zh", "yue", "auto"):
        from .align import mark_breaths

        mark_breaths(segs, float(settings.get("dub_pause_min", 0.3)), marks)
    stats = snap.snap(segs, env, hop, rows, float(p.data.get("info", {}).get("duration", 0) or 0))
    stats.update({"src": src, "measured": measured, "stretched": stretched,
                  "opened": len(set(opened) - set(revoiced)), "revoiced": len(revoiced), "edges": edges})
    p.segments = segs
    p.data["snap_stats"] = stats
    p.save()
    if relayout_dub and has_dub(p) and (marks or before != [(s.start, s.end) for s in segs]):
        relayout(p, settings, _sub(progress, 0.85, 1.0), cancel)
    if progress:
        progress(1.0, "Đã bám mép câu theo giọng")
    return stats


EDGE_GROUP = 40   # số khúc mỗi lần gửi bộ nghe khi kiểm tra đầu / cuối câu


def fix_line_edges(segs: list, wav: Path, engine: str, language: str, settings: dict, env, hop: float,
                   rows: list[int] | None = None, total: float = 0.0, progress: Prog | None = None,
                   cancel=None) -> dict:
    """Đầu / cuối câu bị cắt (2.1.21): mốc câu (bộ căn thời gian / SRT) kết thúc khi nhân vật còn đang nói ("04:30,125 –
    04:34,871" mà thật ra nói tới 04:36,164, giữa chừng ngừng nhiều lần). `snap.edge_steps` tìm câu ngay sau cuối
    (trước đầu) còn tiếng nói không thuộc câu nào, mỗi bên vài nấc nới (dừng ở từng chỗ ngừng) -> bộ nghe nghe lại khúc
    câu đang có + từng nấc -> `snap.edge_heard_ok` (nghe ra thêm chữ của chính câu, không thêm câu khác) -> nhận nấc xa
    nhất nghe đúng. Câu kẹp giữa 2 câu (2.1.22): nấc gần dừng trước chữ câu bên cạnh; câu bị cắt CẢ 2 đầu -> 1 bên nới
    xong thì xét lại bên kia với khúc đã nới; 2 câu liền nhau chia sai ranh giới (nói liền không ngừng, câu bên cạnh
    chiếm mất chữ – `snap.pair_suspects`) -> nghe gộp cả cặp (`snap.pair_heard_ok`), đo mốc chữ cả 2 câu 1 lượt, chia lại
    ranh giới theo chữ. Câu nới mép: đo lại mốc từng chữ trong khúc mới (`asr_qwen.align_qwen`; lỗi thì chia chữ theo
    cụm có tiếng). Sửa `segs` tại chỗ; extra["edge_fix"] = [đầu cũ, cuối cũ]. Trả {"checked", "fixed", "head", "tail",
    "pair", "rows"}."""
    import numpy as np

    from . import asr_qwen, snap
    from .align import CJK, GAP_SPLIT, PAD, _char_positions, alloc_chars
    from .asr import to_simplified
    from .subtitle import Segment

    st = {"checked": 0, "fixed": 0, "extended": 0, "head": 0, "tail": 0, "pair": 0, "rows": []}
    env = np.asarray(env, np.float32)
    steps = snap.edge_steps(segs, env, hop, rows, total)
    st["checked"] = len(steps)
    simplify = language in ("zh", "yue", "auto")
    req = {"cmd": "asr", "wav": str(Path(wav).resolve()), "language": language,
           "prompt": asr_qwen.hotwords(settings), "align": False, "conf": True}
    worker = []

    def listen(spans: list, lo: float, hi: float, msg: str) -> dict:
        """Nghe các khúc [(đầu, cuối, khoá)] -> {khoá: lời}."""
        heard: dict = {}
        if not spans:
            return heard
        if not worker:
            worker.append(asr_qwen.asr_worker(engine, settings))
        for g in range(0, len(spans), EDGE_GROUP):
            part = spans[g:g + EDGE_GROUP]
            pr = None
            if progress:
                x0, x1 = lo + (hi - lo) * g / len(spans), lo + (hi - lo) * (g + len(part)) / len(spans)
                pr = (lambda x, m, x0=x0, x1=x1: progress(x0 + (x1 - x0) * x, m))
                progress(x0, msg)
            r = worker[0].call(dict(req, spans=[[round(a, 3), round(b, 3)] for a, b, _k in part]), pr, cancel)
            for (_a, _b, k), it in zip(part, r.get("items") or []):
                t = (it.get("text") or "").strip()
                if simplify:
                    t2 = to_simplified(t)
                    t = t2 if len(t2) == len(t) else t
                heard[k] = t
        return heard

    order = sorted(range(len(segs)), key=lambda k: segs[k].start)
    pos = {k: j for j, k in enumerate(order)}

    def nb(i: int, d: int):
        j = pos[i] + d
        return order[j] if 0 <= j < len(order) else None

    # lượt 1: từng nấc mỗi bên, so với khúc câu đang có
    spans: list = []
    for i, heads, tails in steps:
        s = segs[i]
        spans.append((s.start, s.end, (i, "o")))
        spans += [(s.start, x, (i, "t", x)) for x in tails]
        spans += [(x, s.end, (i, "h", x)) for x in heads]
    heard = listen(spans, 0.0, 0.5, f"Kiểm tra đầu / cuối {len(steps)} câu có thể bị cắt…")
    new_b: dict[int, list[float]] = {}
    redo: list = []
    for i, heads, tails in steps:
        s = segs[i]
        base = heard.get((i, "o"), "")
        t_ok = [x for x in tails if snap.edge_heard_ok(s.text, base, heard.get((i, "t", x), ""))]
        h_ok = [x for x in heads if snap.edge_heard_ok(s.text, base, heard.get((i, "h", x), ""))]
        a = min(h_ok) if h_ok else s.start
        b = max(t_ok) if t_ok else s.end
        new_b[i] = [a, b]
        if t_ok and heads and not h_ok:      # câu bị cắt cả 2 đầu: nghe lại bên kia với khúc đã nới
            redo.append((i, "h", heads, heard.get((i, "t", b), "")))
        if h_ok and tails and not t_ok:
            redo.append((i, "t", tails, heard.get((i, "h", a), "")))
    # lượt 2
    spans = []
    for i, side, xs, _base in redo:
        a, b = new_b[i]
        spans += [((x, b) if side == "h" else (a, x)) + ((i, side + "2", x),) for x in xs]
    heard2 = listen(spans, 0.5, 0.6, "Kiểm tra lại câu bị cắt cả đầu lẫn cuối…")
    for i, side, xs, base in redo:
        ok = [x for x in xs if snap.edge_heard_ok(segs[i].text, base, heard2.get((i, side + "2", x), ""))]
        if ok:
            new_b[i][0 if side == "h" else 1] = min(ok) if side == "h" else max(ok)
    blocked: set = set()      # nới không được vì nấc xa nghe ra chữ câu bên cạnh -> nghe gộp cả cặp
    fixed: dict[int, tuple[float, float]] = {}
    for i, heads, tails in steps:
        s = segs[i]
        a, b = new_b[i]
        k = nb(i, 1)
        if tails and b == s.end and k is not None and segs[k].start - tails[-1] <= snap.PAIR_GAP:
            blocked.add((i, k))
        k = nb(i, -1)
        if heads and a == s.start and k is not None and heads[-1] - segs[k].end <= snap.PAIR_GAP:
            blocked.add((k, i))
        if (a, b) != (s.start, s.end):
            st["head"] += a < s.start
            st["tail"] += b > s.end
            fixed[i] = (s.start, s.end)
            s.start, s.end = round(a, 3), round(b, 3)
            s.extra.pop("ct", None)
            s.extra.pop("ct_voice", None)
            s.extra.pop("snap", None)
    rows_fixed = sorted(fixed)
    st["extended"] = len(rows_fixed)
    eng_a = "firered" if engine == "firered" else "qwen3"
    if rows_fixed:
        if progress:
            progress(0.6, f"Đo lại mốc từng chữ {len(rows_fixed)} câu đã nới mép…")
        try:
            asr_qwen.align_qwen([segs[i] for i in rows_fixed], wav, language, settings, None, cancel,
                                engine=eng_a, pad=0.05)
        except ff.Cancelled:
            raise
        except Exception:  # noqa: BLE001 – bộ căn lỗi: chia chữ theo cụm có tiếng bên dưới
            log_error("Đo lại mốc chữ câu nới mép")
        floor = snap._floor(env)
        for i in rows_fixed:
            s = segs[i]
            pos_c = _char_positions(s.text)
            ct = s.extra.get("ct")
            if not ct or len(ct) != len(pos_c):
                thr = snap._thr(env, hop, s.start, s.end, floor)
                groups: list[list[float]] = []
                for x, y in (snap._clusters(env, hop, s.start, s.end, thr) if thr else []):
                    if groups and x - groups[-1][1] < GAP_SPLIT:
                        groups[-1][1] = y
                    else:
                        groups.append([x, y])
                got = alloc_chars([tuple(g) for g in groups], s.text, pos_c) if pos_c else None
                if got:
                    s.extra["ct"] = [list(x) for x in got]
                    s.extra["ct_voice"] = True
                ct = s.extra.get("ct")
            if ct and CJK.search(s.text or ""):
                s.start = round(max(s.start, float(ct[0][0]) - PAD), 3)
                s.end = round(min(s.end, float(ct[-1][1]) + PAD), 3) if float(ct[-1][1]) + PAD > s.start + 0.2 else s.end
    # cặp câu liền nhau chia sai ranh giới: nghe gộp, đo mốc chữ cả 2 câu 1 lượt, chia lại theo chữ
    want = None if rows is None else sorted({int(r) for r in rows} | set(rows_fixed))
    view = list(segs)     # câu chưa có mốc chữ (SRT / dự án cũ): đo tạm để nhận ra mép bị ép chữ – không lưu
    bare = [i for j, i in enumerate(order) if CJK.search(segs[i].text or "")
            and len(segs[i].extra.get("ct") or []) != len(_char_positions(segs[i].text))
            and (want is None or i in want)
            and ((j > 0 and segs[i].start - segs[order[j - 1]].end <= snap.PAIR_GAP)
                 or (j + 1 < len(order) and segs[order[j + 1]].start - segs[i].end <= snap.PAIR_GAP))]
    if bare:
        if progress:
            progress(0.65, f"Đo tạm mốc chữ {len(bare)} câu chưa có mốc…")
        tmp = [Segment(segs[i].start, segs[i].end, segs[i].text) for i in bare]
        try:
            asr_qwen.align_qwen(tmp, wav, language, settings, None, cancel, engine=eng_a, pad=0.05)
            for i, t in zip(bare, tmp):
                view[i] = t
        except ff.Cancelled:
            raise
        except Exception:  # noqa: BLE001
            log_error("Đo tạm mốc chữ câu chưa có mốc")
    pairs = snap.pair_suspects(view, env, hop, want, blocked)
    spans = [(segs[i].start, segs[k].end, (i, k)) for i, k in pairs]
    heard3 = listen(spans, 0.7, 0.9, f"Kiểm tra ranh giới {len(pairs)} cặp câu liền nhau…")
    ok_pairs = [(i, k) for i, k in pairs if snap.pair_heard_ok(segs[i].text, segs[k].text, heard3.get((i, k), ""))]
    if ok_pairs:
        if progress:
            progress(0.9, f"Đo mốc từng chữ {len(ok_pairs)} cặp câu…")
        tmp = [Segment(segs[i].start, segs[k].end, segs[i].text + segs[k].text) for i, k in ok_pairs]
        try:
            asr_qwen.align_qwen(tmp, wav, language, settings, None, cancel, engine=eng_a, pad=0.05)
        except ff.Cancelled:
            raise
        except Exception:  # noqa: BLE001 – không đo được thì giữ ranh giới cũ
            log_error("Đo mốc chữ cặp câu liền nhau")
            tmp = []
        for (i, k), t in zip(ok_pairs, tmp):
            A, B = segs[i], segs[k]
            ka = len(_char_positions(A.text))
            ct = t.extra.get("ct") or []
            if not ka or len(ct) != ka + len(_char_positions(B.text)) or len(ct) <= ka:
                continue
            a_end = float(ct[ka - 1][1]) + PAD
            b_start = float(ct[ka][0]) - PAD
            if a_end > b_start - snap.GAP:
                mid = (float(ct[ka - 1][1]) + float(ct[ka][0])) / 2
                a_end, b_start = mid - snap.GAP / 2, mid + snap.GAP / 2
            a_end = min(max(a_end, A.start + 0.2), B.end - 0.2)
            b_start = max(min(b_start, B.end - 0.2), a_end + snap.GAP)
            if max(abs(a_end - A.end), abs(b_start - B.start)) < snap.PAIR_MIN:
                continue
            for r, s, part in ((i, A, ct[:ka]), (k, B, ct[ka:])):
                fixed.setdefault(r, (s.start, s.end))
                s.extra["ct"] = [[float(x), float(y)] for x, y in part]
                s.extra.pop("ct_voice", None)
                s.extra.pop("snap", None)
            A.end, B.start = round(a_end, 3), round(b_start, 3)
            st["pair"] += 1
    if not fixed:
        return st
    # nới mép không bao giờ lấn câu bên cạnh
    for i, k in zip(order, order[1:]):
        A, B = segs[i], segs[k]
        if (i in fixed or k in fixed) and A.end > B.start - snap.GAP and fixed.get(i, (0, A.end))[1] <= B.start:
            A.end = round(max(A.start + 0.2, B.start - snap.GAP), 3)
    rows_fixed = sorted(fixed)
    for i in rows_fixed:
        segs[i].extra["edge_fix"] = [round(fixed[i][0], 3), round(fixed[i][1], 3)]
    st["fixed"] = len(rows_fixed)
    st["rows"] = rows_fixed
    return st


def edge_note(st: dict | None) -> str:
    """1 dòng kết quả sửa đầu / cuối câu bị cắt."""
    if not st:
        return ""
    if st.get("error"):
        return f"chưa kiểm tra được đầu / cuối câu bị cắt ({st['error']})"
    if not st.get("fixed"):
        return ""
    out = []
    n = st.get("extended", st["fixed"] if not st.get("pair") else 0)
    if n:
        parts = [f"{st['tail']} cuối câu" if st.get("tail") else "", f"{st['head']} đầu câu" if st.get("head") else ""]
        out.append(f"nới {n} câu bị cắt sớm theo giọng (" + ", ".join(x for x in parts if x) + ")")
    if st.get("pair"):
        out.append(f"chia lại ranh giới {st['pair']} cặp câu liền nhau theo từng chữ")
    return ", ".join(out) + " – bộ nghe nghe lại xác nhận"


def snap_note(stats: dict | None) -> str:
    """1 dòng kết quả bám mép câu cho thông báo."""
    if not stats:
        return ""
    t = (f"bám mép {stats['moved']} câu theo giọng (dời trung bình {stats['shift']:.2f} s)" if stats.get("moved")
         else "mép câu đã khớp giọng")
    if stats.get("stretched"):
        t += f", co {stats['stretched']} câu có chữ bị kéo dài qua chỗ lặng"
    if edge_note(stats.get("edges")):
        t += ", " + edge_note(stats.get("edges"))
    if stats.get("revoiced"):
        t += f", căn lại theo giọng {stats['revoiced']} câu bộ căn thời gian đặt sai giờ"
    if stats.get("opened"):
        t += f", mở chỗ ngừng giữa câu ở {stats['opened']} câu (giọng lồng nghỉ đúng chỗ)"
    if stats.get("src") == "mix":
        t += " – đo trên tiếng gốc (chưa tách giọng, kém chính xác hơn)"
    if stats.get("est"):
        t += f" · {stats['est']} câu mốc ước lượng (viền nét đứt trên timeline – nên nghe lại)"
    return t


def dual_check(p: Project, settings: dict | None = None, rows: list[int] | None = None, progress: Prog | None = None,
               cancel=None) -> dict:
    """Nút "Nghe 2 bộ rồi so" (tab Phụ đề) cho phụ đề đã có: bộ nghe còn lại nghe lại các câu (đã tích / cả phim),
    AI chọn bản đúng. Chỉ đổi chữ gốc (bản dịch / giọng lồng giữ nguyên – giao diện tích sẵn câu đổi để dịch lại);
    Ctrl+Z hoàn tác. Trả thống kê + "changed" (số thứ tự câu đổi chữ)."""
    settings = dict(settings or load_settings())
    segs = p.segments
    if not segs:
        raise ValueError("Chưa có phụ đề")
    primary = "ocr" if p.data.get("sub_source") in ("ocr", "hybrid") else p.data.get("asr_engine_used") or \
        settings.get("asr_engine", "qwen3")
    language = p.data.get("language", "zh") or "zh"
    if dual_engine(primary, language, settings)[0] is None:
        raise RuntimeError("Chưa có bộ nghe thứ 2: " + dual_engine(primary, language, settings)[1])
    wav = _voice_wav16(p, _sub(progress, 0.0, 0.1), cancel)
    _asr_context(p, settings)
    pick = set(range(len(segs))) if rows is None else {r for r in rows if 0 <= r < len(segs)}
    sub = [segs[i] for i in sorted(pick)]
    for x in sub:
        for k in ("asr_alt", "asr_alt_eng", "asr_unsure", "asr_pick"):
            x.extra.pop(k, None)
    before = [x.text for x in segs]
    st = dual_listen(p, sub, wav, primary, language, settings, _sub(progress, 0.1, 1.0), cancel)
    changed = [i for i, (a, x) in enumerate(zip(before, segs)) if a != x.text]
    p.segments = segs
    p.data["asr_dual"] = st
    p.save()
    st["changed"] = changed
    return st


def use_alt(p: Project, rows: list[int]) -> list[int]:
    """"Dùng bản bộ nghe 2" cho các câu đã tích: thay chữ bằng chữ bộ nghe thứ 2 (giữ dấu câu). Trả số câu đã đổi."""
    from . import dual

    segs = p.segments
    out = []
    for i in rows:
        if not (0 <= i < len(segs)):
            continue
        x = segs[i]
        alt = x.extra.get("asr_alt")
        if not alt or not dual.differs(x.text, alt):
            continue
        old = x.text
        x.text = dual.merge_alt(old, alt)
        x.extra.setdefault("src_before", old)
        x.extra.pop("asr_unsure", None)
        x.extra["asr_pick"] = "B: bạn chọn"
        if _cjk_count(x.text) != _cjk_count(old):
            x.extra.pop("ct", None)
        out.append(i)
    if out:
        p.segments = segs
        p.save()
    return out


def relisten_engine(p: Project, settings: dict | None = None) -> tuple[str | None, str]:
    """Bộ nghe cho "Bộ nghe nghe lại câu đã tích": bộ đã nghe dự án này / bộ chọn ở Cài đặt nếu là Qwen3-ASR /
    FireRedASR2, không thì bộ nào dùng được (cần độ chắc từng chữ). Trả (bộ | None, tên / lý do)."""
    from . import asr_firered, asr_qwen

    settings = settings or load_settings()
    language = p.data.get("language", "zh") or "zh"
    order: list[str] = []
    for e in (p.data.get("asr_engine_used"), settings.get("asr_engine"), "qwen3", "firered"):
        if e in ("qwen3", "firered") and e not in order:
            order.append(e)
    why = []
    for e in order:
        if e == "firered":
            if language not in asr_firered.LANGS:
                why.append("FireRedASR2 không nghe tiếng này")
            elif not asr_firered.ready():
                why.append(f"FireRedASR2: {asr_firered.why_unavailable()}")
            else:
                return e, "FireRedASR2"
        elif asr_qwen.available():
            return e, "Qwen3-ASR"
        else:
            why.append(f"Qwen3-ASR: {asr_qwen.why_unavailable()}")
    return None, "; ".join(why)


def relisten_lines(p: Project, settings: dict | None = None, rows: list[int] | None = None,
                   progress: Prog | None = None, cancel=None) -> dict:
    """Nút "Bộ nghe nghe lại câu đã tích" (2.1.16): bộ nghe nghe lại đúng các câu đó – khúc rộng (câu nằm giữa, có câu
    trước / sau) trên giọng đã tách + trên tiếng gốc + riêng câu – rồi ĐIỀN LẠI chữ gốc khi đủ chắc
    (`recheck.decide_manual`); chưa chắc -> chữ xanh + bản nghe lại ("Dùng bản bộ nghe 2"). Chỉ đổi chữ gốc (bản dịch /
    giọng lồng giữ nguyên – giao diện tích sẵn câu đổi để dịch lại), mốc từng chữ đo lại; Ctrl+Z hoàn tác. Trả thống kê
    của `recheck_lines` + "changed" (số thứ tự câu đổi chữ)."""
    from . import recheck
    from .asr_qwen import release

    settings = dict(settings or load_settings())
    segs = p.segments
    if not segs:
        raise ValueError("Chưa có phụ đề")
    rows = sorted({r for r in rows or [] if 0 <= r < len(segs)})
    if not rows:
        raise ValueError("Tích câu cần nghe lại trước")
    engine, why = relisten_engine(p, settings)
    if engine is None:
        raise RuntimeError("Chưa nghe lại được: " + why)
    language = p.data.get("language", "zh") or "zh"
    wav = _voice_wav16(p, _sub(progress, 0.0, 0.04), cancel)
    orig = _orig_wav16(p, cancel) if stems(p) else None
    _asr_context(p, settings)
    before = [x.text for x in segs]
    try:
        st = recheck.recheck_lines(segs, wav, orig, engine, language, settings, p.data.get("info", {}).get("duration", 0),
                                   _sub(progress, 0.05, 1.0), cancel, rows=rows)
    finally:
        release()     # trả card cho việc khác
    st["engine"] = engine
    st["changed"] = [i for i, (a, x) in enumerate(zip(before, segs)) if a != x.text]
    p.segments = segs
    p.save()
    return st


def fix_names_now(p: Project, settings: dict | None = None) -> list[int]:
    """Nút "Sửa tên theo âm đọc": thay chỗ đọc giống tên riêng (bảng tên riêng / hồ sơ phim) mà viết khác."""
    from . import names

    settings = settings or load_settings()
    nm = names.names_of(p, settings)
    if not nm:
        raise ValueError("Chưa có tên riêng nào – điền Bảng tên riêng (vd. 林北=Lâm Bắc) hoặc bấm 'AI lập lại hồ sơ phim'")
    segs = p.segments
    rows = names.fix(segs, nm)
    if rows:
        p.segments = segs
        p.save()
    return rows


def listen_note(p: Project) -> str:
    """Kết quả các bước nghe chính xác hơn (2.1.0) cho thông báo xong "1. Nghe & tách câu" / Tự động."""
    from .recheck import note as recheck_note

    out = [x for x in (hybrid_note(p.data.get("hybrid")) if p.data.get("sub_source") == "hybrid" else "",
                       recheck_note(p.data.get("asr_recheck")), name_note(p.data.get("name_pass")),
                       dual_note(p.data.get("asr_dual"))) if x]
    if edge_note(p.data.get("edge_fix")):
        out.append(edge_note(p.data.get("edge_fix")))
    if p.data.get("ct_revoiced"):
        out.append(f"căn lại theo giọng {p.data['ct_revoiced']} khúc bộ căn thời gian đặt sai giờ (chữ rơi vào chỗ lặng)")
    if p.data.get("pauses_opened"):
        out.append(f"mở chỗ ngừng giữa câu theo giọng ở {p.data['pauses_opened']} câu (mốc chữ bị kéo qua đoạn lặng)")
    if p.data.get("breaths_marked"):
        out.append(f"thêm dấu phẩy ở {p.data['breaths_marked']} chỗ nhân vật ngắt hơi")
    if (p.data.get("asr_relisten") or {}).get("foreign"):
        out.append(f"{p.data['asr_relisten']['foreign']} khúc nói tiếng khác (VD tiếng Anh) – đã nghe lại đúng tiếng")
    if (p.data.get("asr_relisten") or {}).get("resumed"):
        out.append(f"chạy tiếp lần nghe dở: dùng lại {p.data['asr_relisten']['resumed']} khúc đã nghe")
    ck = p.data.get("ct_check") or {}
    if ck.get("fixed"):
        out.append(f"căn lại mốc chữ {ck['fixed']} câu (chữ bị kéo dài / lấn câu bên)")
    if p.data.get("asr_rescue_lowconf"):
        out.append(f"bỏ {p.data['asr_rescue_lowconf']} đoạn nghe lại mà bộ nghe không chắc (nhạc / tiếng động)")
    rl = p.data.get("asr_relisten") or {}
    if rl.get("echo"):
        out.append(f"bỏ {rl['echo']} khúc bộ nghe chép lại lời gợi ý (đoạn nhạc / không ai nói)")
    if rl.get("repeat_fixed"):
        out.append(f"sửa {rl['repeat_fixed']} chỗ bộ nghe lặp lời")
    if p.data.get("asr_stammer"):
        out.append(f"bỏ {p.data['asr_stammer']} chỗ nói vấp (nói dở rồi nói lại, VD 我不管闲，我不管闲事 -> 我不管闲事)")
    if p.data.get("asr_repeat"):
        out.append(f"⚠ {p.data['asr_repeat']} câu còn lặp lời / 2 câu liền giống nhau (chữ gốc màu hồng) – nghe lại, "
                   "hoặc tab Phụ đề → Công cụ → Lời hát & nghe nhầm → 'Dọn câu bịa / câu lặp'")
    if note := unsure_note(p.data.get("unsure_dropped")):
        out.append(note)
    return " · ".join(out)


UNSURE_CONF = 0.7    # mức mặc định "độ chắc dưới" (Cài đặt asr_pick_conf – chung tab Tự động / tab Phụ đề)


def unsure_rows(segs, thr: float = UNSURE_CONF) -> list[int]:
    """Câu bộ nghe KHÔNG CHẮC (dùng chung: nút "Tích câu bộ nghe không chắc" tab Phụ đề và bước "Xoá câu không chắc"):
    độ chắc `asr_conf` dưới mức `thr`. Bỏ qua câu 🎵, câu chưa có độ chắc (OCR, SRT, SenseVoice / Whisper) và câu đã có
    lần nghe khác xác nhận (`asr_same`: nghe lại ra y hệt / 2 bộ nghe ra giống nhau)."""
    out = []
    for i, s in enumerate(segs):
        c = s.extra.get("asr_conf")
        if c is None or s.extra.get("song") or s.extra.get("asr_same"):
            continue
        if float(c) < thr:
            out.append(i)
    return out


def drop_unsure(p: Project, segs, settings: dict) -> list:
    """Bước cuối "1. Nghe & tách câu" / Tự động khi bật "Xoá câu không chắc" (Cài đặt `asr_drop_unsure`, mức
    `asr_pick_conf`): bỏ hẳn các câu `unsure_rows` để không gửi đi dịch / lồng tiếng. Câu bỏ cất vào
    p.data["unsure_removed"] (nút "Khôi phục câu đã xoá" tab Phụ đề). Trả danh sách câu còn lại."""
    thr = float(settings.get("asr_pick_conf", UNSURE_CONF) or UNSURE_CONF)
    rows = set(unsure_rows(segs, thr))
    known = sum(1 for s in segs if s.extra.get("asr_conf") is not None)
    p.data["unsure_dropped"] = {"n": len(rows), "thr": thr, "known": known}
    if not rows:
        return segs
    p.data["unsure_removed"] = [segs[i].to_dict() for i in sorted(rows)]
    return [s for i, s in enumerate(segs) if i not in rows]


def unsure_note(st: dict | None) -> str:
    if not st:
        return ""
    thr = f"{float(st.get('thr', UNSURE_CONF)):.2f}".replace(".", ",")
    if not st.get("known", 1):
        return "chưa lọc được câu không chắc: bộ nghe này không chấm độ chắc (chỉ Qwen3-ASR / FireRedASR2 có)"
    if not st.get("n"):
        return f"lọc câu không chắc: không có câu nào độ chắc dưới {thr}"
    return (f"xoá {st['n']} câu bộ nghe không chắc (độ chắc dưới {thr}) – lấy lại: tab Phụ đề → Công cụ → Lời hát & "
            "nghe nhầm → 'Khôi phục câu đã xoá'")


def restore_unsure(p: Project) -> list[int]:
    """Đưa lại các câu bước "Xoá câu không chắc" đã bỏ vào đúng chỗ theo thời gian. Trả số thứ tự các câu vừa đưa lại."""
    from .subtitle import Segment

    back = [Segment.from_dict(d) for d in p.data.pop("unsure_removed", []) or []]
    p.data.pop("unsure_dropped", None)
    if not back:
        return []
    segs = sorted(p.segments + back, key=lambda s: (s.start, s.end))
    p.segments = segs
    p.save()
    ids = {id(s) for s in back}
    return [i for i, s in enumerate(segs) if id(s) in ids]


def mark_repeats(segs) -> int:
    """Đánh dấu extra["asr_repeat"] câu còn 1 cụm ≥ 5 chữ lặp liền nhau hoặc 2 câu liền nhau gần như giống nhau (bộ
    nghe lặp vòng mà lần nghe lại không gỡ được / nhân vật nói 2 lần thật) – chữ gốc tô màu để kiểm. Trả số câu."""
    from .align import near_duplicates, repeat_block

    for x in segs:
        x.extra.pop("asr_repeat", None)
    for x in segs:
        if b := repeat_block(x.text):
            x.extra["asr_repeat"] = b
    for i, b in near_duplicates(segs):
        segs[i].extra.setdefault("asr_repeat", b)
        segs[i + 1].extra.setdefault("asr_repeat", b)
    return sum(1 for x in segs if x.extra.get("asr_repeat"))


def old_prompt(p: Project, settings: dict) -> str:
    """Lời gợi ý bản 2.1.0–2.1.7 đã gửi bộ nghe (tên + tiêu đề / mô tả / thẻ video) – để nhận ra câu bộ nghe chép lại."""
    import re

    from . import asr_qwen, names

    meta = p.data.get("video_meta") or {}
    parts = [asr_qwen.hotwords({k: v for k, v in settings.items() if k != "_asr_context"}),
             names.context_text(p, settings), str(meta.get("title") or ""),
             re.sub(r"\s+", " ", str(meta.get("description") or "")), "、".join(str(t) for t in meta.get("tags") or [])]
    return "\n".join(x for x in parts if x)


ECHO_CPS = 9.0   # câu trùng tiêu đề / mô tả video mà > 9 chữ Hán / giây = bộ nghe chép lời gợi ý vào khúc nhạc ngắn


def clean_asr(p: Project, settings: dict | None = None, progress: Prog | None = None, cancel=None) -> dict:
    """Dọn phụ đề đã nghe xong (2.1.8) – không phải nghe lại cả phim:
    1. bỏ câu bộ nghe CHÉP lời gợi ý (tiêu đề / mô tả / thẻ video, danh sách tên – đoạn nhạc / không ai nói); bỏ chỗ nói
       vấp ("我不管闲，我不管闲事" -> "我不管闲事", `align.drop_false_starts`, 2.1.9);
    2. câu có 1 cụm ≥ 5 chữ lặp liền nhau + 2 câu liền nhau gần giống nhau: Qwen3-ASR nghe lại đúng đoạn đó (không gợi
       ý, phạt lặp): cụm chỉ còn 1 lần -> bỏ lần lặp / gộp 2 câu thành 1; vẫn 2 lần (nói 2 lần thật) -> giữ, tô màu.
    Câu đổi chữ: căn lại mốc từng chữ, bỏ giọng lồng cũ; đã lồng -> xếp lại bản lồng (không đọc lại). Hoàn tác = "Hoàn
    tác sửa" (cleanup_backup). Trả {"echo", "stammer", "fixed", "merged", "kept", "changed": [chỉ số câu], "no_asr"}."""
    from difflib import SequenceMatcher

    from . import asr_qwen
    from .align import (CJK, ECHO_MARKERS, decide_repeat, drop_false_starts, latin_echo, near_duplicates,
                        prompt_echo, repeat_block)

    settings = dict(settings or load_settings())
    language = p.data.get("language", "zh") or "zh"
    segs = list(p.segments)
    st: dict = {"echo": 0, "stammer": 0, "fixed": 0, "merged": 0, "kept": 0, "changed": [], "no_asr": False}
    if not segs:
        return st
    backup = [x.to_dict() for x in segs]
    old_texts = {x.text for x in segs}
    prompt = old_prompt(p, settings)
    meta = p.data.get("video_meta") or {}
    tags = "、".join(str(t) for t in meta.get("tags") or [])
    meta_text = "\n".join([str(meta.get("title") or ""), str(meta.get("description") or ""), tags])

    def echo(x) -> bool:
        """Câu chép lời gợi ý: có dấu hiệu ("视频标题"…) / là danh sách thẻ video / trùng tiêu đề, mô tả mà nói nhanh
        bất thường (> ECHO_CPS chữ/s – bộ căn thời gian ép vào khúc nhạc ngắn) / chỉ có đuôi chữ Latin – số của tiêu đề
        ("p03 3" – phần thứ mấy của video Bilibili, 2.1.11). Câu thuyết minh đọc đúng nguyên văn mô tả ở tốc độ thường
        (nhân vật nói thật) được giữ."""
        if latin_echo(x.text, meta_text):
            return True
        if not prompt_echo(x.text, prompt):
            return False
        if any(m in x.text for m in ECHO_MARKERS) or (tags and prompt_echo(x.text, tags)):
            return True
        return len(CJK.findall(x.text)) / max(0.05, x.end - x.start) > ECHO_CPS
    keep = [x for x in segs if not echo(x)]
    st["echo"] = len(segs) - len(keep)
    segs = keep
    changed: list = []
    if language in ("zh", "yue", "auto") and p.data.get("sub_source") not in ("ocr", "hybrid"):
        for x in segs:
            if drop_false_starts(x):     # nói vấp: bỏ lần nói dở (mốc từng chữ giữ khớp), không cần nghe lại
                changed.append(x)
                st["stammer"] += 1
    reps = [(i, b) for i, x in enumerate(segs) if (b := repeat_block(x.text))]
    pairs = near_duplicates(segs)
    spans = [[max(0.0, segs[i].start - 0.1), segs[i].end + 0.1] for i, _b in reps] + \
            [[max(0.0, segs[i].start - 0.1), segs[i + 1].end + 0.1] for i, _b in pairs]
    heard: list = []
    wav = None
    if spans and asr_qwen.available():
        wav = _voice_wav16(p, _sub(progress, 0.0, 0.15), cancel)
        try:
            r = asr_qwen.worker(settings).call(
                {"cmd": "asr", "wav": str(Path(wav).resolve()), "language": language, "prompt": "", "align": False,
                 "rep_penalty": asr_qwen.REP_PENALTY, "spans": spans}, _sub(progress, 0.15, 0.75), cancel)
            heard = [_heard(it.get("text", ""), language) for it in r.get("items") or []]
        except ff.Cancelled:
            raise
        except Exception:  # noqa: BLE001 – không nghe lại được: chỉ bỏ câu chép gợi ý, câu lặp tô màu để kiểm
            log_error("Dọn câu lặp: nghe lại")
            heard = []
    elif spans:
        st["no_asr"] = True

    def han(t: str) -> str:
        return "".join(CJK.findall(t or ""))
    for (i, b), h in zip(reps, heard[:len(reps)]):
        x = segs[i]
        txt, fixed = decide_repeat(x.text, h, b)
        if fixed:
            x.extra.setdefault("src_before", x.text)
            x.text = txt
            x.extra.pop("ct", None)
            changed.append(x)
            st["fixed"] += 1
        else:
            st["kept"] += 1
    gone: set = set()
    for (i, b), h in reversed(list(zip(pairs, heard[len(reps):]))):
        a, c = segs[i], segs[i + 1]
        if id(a) in gone or id(c) in gone:
            continue
        hn = han(h)
        n = hn.count(b)
        if n >= 2 or not hn:
            st["kept"] += 1
            continue
        best = max((a, c), key=lambda y: (SequenceMatcher(None, han(y.text), hn, autojunk=False).ratio(),
                                          len(han(y.text))))
        if n == 0 and SequenceMatcher(None, han(best.text), hn, autojunk=False).ratio() < 0.6:
            st["kept"] += 1
            continue
        other = c if best is a else a
        best.extra.setdefault("src_before", a.text + " | " + c.text)
        best.start, best.end = min(a.start, c.start), max(a.end, c.end)
        best.extra.pop("ct", None)
        gone.add(id(other))
        if best not in changed:
            changed.append(best)
        st["merged"] += 1
    segs = [x for x in segs if id(x) not in gone]
    realign = [x for x in changed if not x.extra.get("ct")]
    if realign and wav is not None:
        try:
            asr_qwen.align_qwen(realign, wav, language, settings, _sub(progress, 0.75, 0.95), cancel)
        except ff.Cancelled:
            raise
        except Exception:  # noqa: BLE001 – căn lỗi: câu giữ mốc đầu / cuối như cũ
            log_error("Dọn câu lặp: căn thời gian")
    p.data["asr_repeat"] = mark_repeats(segs)
    if st["echo"] or changed or gone:
        p.data["cleanup_backup"] = backup
        lost = forget_stale_voice(old_texts, segs, moved=bool(gone))
        p.segments = segs
        p.save()
        if (st["echo"] or lost or gone) and has_dub(p):   # câu bị bỏ / đổi: xếp lại bản lồng, không đọc lại
            relayout(p, settings)
    else:
        p.segments = segs
        p.save()
    pos = {id(x): k for k, x in enumerate(segs)}
    st["changed"] = sorted(pos[id(x)] for x in changed if id(x) in pos)
    return st


def clean_asr_note(st: dict, translated: bool = False) -> str:
    parts = []
    if st.get("echo"):
        parts.append(f"bỏ {st['echo']} câu bộ nghe chép lại tiêu đề / mô tả video")
    if st.get("stammer"):
        parts.append(f"bỏ chỗ nói vấp trong {st['stammer']} câu (VD 我不管闲，我不管闲事 -> 我不管闲事)")
    if st.get("fixed"):
        parts.append(f"bỏ chỗ lặp lời trong {st['fixed']} câu")
    if st.get("merged"):
        parts.append(f"gộp {st['merged']} cặp câu lặp thành 1")
    if st.get("kept"):
        parts.append(f"giữ {st['kept']} chỗ (nghe lại vẫn ra 2 lần – nhân vật nói 2 lần thật, chữ gốc màu hồng)")
    if st.get("no_asr"):
        parts.append("chưa nghe lại được câu lặp (cần Qwen3-ASR) – đã tô màu hồng để kiểm tay")
    if not parts:
        return "Không thấy câu bịa / câu lặp nào"
    msg = "Đã " + ", ".join(parts)
    if st.get("changed") and translated:
        msg += " · câu đổi chữ gốc đã tích sẵn – bấm 'Dịch câu đã tích' rồi 'Tạo lại giọng các câu đã tích'"
    return msg + " · không ưng: 'Hoàn tác sửa'"


def _voice_wav16(p: Project, progress: Prog | None = None, cancel=None) -> Path:
    """Bản 16 kHz mono của giọng đã tách (không có thì tiếng gốc) theo giờ của hình – cho bộ căn thời gian."""
    st = stems(p)
    src = st.get("vocals") or str(p.source)
    wav = p.work / ("asr16k_vocals.wav" if st else "audio16k.wav")
    if not wav.exists() or wav.stat().st_size < 1000 or wav.stat().st_mtime < Path(src).stat().st_mtime:
        ff.run(["-i", str(src), "-vn"] + ff.af_args(ff.audio_sync(src, 16000)) + ["-ac", "1", "-ar", "16000", "-c:a",
               "pcm_s16le", str(wav)], p.data.get("info", {}).get("duration", 0), progress, cancel,
               "Đang chuẩn bị âm thanh")
    return wav


# ------------------------------------------------------------------ dịch

def clause_state(s, lang: str, min_gap: float) -> tuple[list[int], int]:
    """(chỗ ngắt hơi của câu gốc, số chỗ câu dịch đã có dấu câu tương ứng)."""
    from .tts import breath_points, pause_points

    bp = breath_points(s, min_gap)
    if not bp:
        return [], 0
    have = {pz for _a, _b, pz, _pt in pause_points(s, lang, min_gap)}
    return bp, sum(1 for b in bp if b in have)


def match_clauses(segs, rows, tr, lang: str, min_gap: float = 0.3, cancel=None) -> int:
    """Câu dịch thiếu vế so với chỗ nhân vật ngắt hơi (câu gốc có dấu câu ở chỗ khe ≥ min_gap): AI viết lại cho đủ vế
    (Translator.match_clauses). Chỉ nhận khi ghép được thêm chỗ ngắt và không dài hơn hẳn; bản cũ lưu
    extra["trans_before_clause"]. Bỏ qua câu người dùng tự sửa, câu 🎵. Trả số câu đã sửa (2.1.3)."""
    from . import langs
    from .align import restarts
    from .tts import clause_texts

    todo = []
    for i in rows:
        if not 0 <= i < len(segs):
            continue
        s = segs[i]
        if not (s.trans or "").strip() or s.extra.get("trans_user") or s.extra.get("song") or restarts(s.text):
            continue      # câu gốc có vế nói lại ("你，你还没…") -> bản dịch gộp vế là đúng, không ép đủ vế
        bp, n = clause_state(s, lang, min_gap)
        if bp and n < len(bp):
            todo.append((i, clause_texts(s.text, bp), n))
    fixed = 0
    for b in range(0, len(todo), 40):
        if cancel and cancel():
            raise ff.Cancelled()
        part = todo[b:b + 40]
        for (i, _cl, n_old), new in zip(part, tr.match_clauses([(cl, segs[i].trans) for i, cl, _n in part])):
            old = segs[i].trans
            if not new or new == old:
                continue
            segs[i].trans = new
            if clause_state(segs[i], lang, min_gap)[1] > n_old and \
                    langs.units(new, lang) <= langs.units(old, lang) * 1.25 + 2:
                segs[i].extra["trans_before_clause"] = old
                fixed += 1
            else:
                segs[i].trans = old
    return fixed


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
               cancel=None, mode: str = "full") -> dict:
    """Kỹ năng 1: AI sửa chữ nghe nhầm + nối câu bị cắt sai chỗ + lọc chữ không phải lời thoại (giữ bản cũ để Hoàn
    tác). mode "filter": chỉ lọc – lời bài hát -> 🎵 (không lồng tiếng), câu máy tự bịa / chữ vô nghĩa -> bỏ, đoạn rác dính
    vào câu thoại -> xoá phần đó. Trả về thống kê của `skills.apply_cleanup` (số thứ tự câu sau khi sửa)."""
    from . import skills
    from .translate import LLMClient

    settings = settings or load_settings()
    eng = ai_engine(settings, engine)
    segs = p.segments
    if not segs:
        raise ValueError("Chưa có phụ đề gốc để sửa.")
    res = skills.clean_lines(LLMClient(settings, eng), segs, p.data.get("film_profile", ""),
                             skills.skill_text(settings), progress, cancel, mode=mode)
    new, stats = skills.apply_cleanup(segs, res)
    if stats["changed"] or stats["songs"] or stats["removed"]:
        p.data["cleanup_backup"] = [x.to_dict() for x in segs]
        lost = forget_stale_voice({x.text for x in segs}, new, moved=len(new) != len(segs))
        p.segments = new
        p.save()
        if (lost or stats["songs"]) and has_dub(p):   # câu đổi lời / gộp / bỏ / thành 🎵: xếp lại bản lồng, không đọc lại
            relayout(p, settings)
    p.data["ai_filtered"] = True
    if mode != "filter":
        p.data["asr_cleaned"] = True
    p.save()
    return stats


def cleanup_note(st: dict) -> str:
    """Thông báo kết quả AI sửa lỗi nghe / lọc lời bài hát."""
    parts = []
    if st["fixed"] or st["merged"]:
        parts.append(f"AI sửa {st['fixed']} câu, nối {st['merged']} câu bị cắt")
    if st["songs"]:
        parts.append(f"{len(st['songs'])} câu là lời bài hát – đánh 🎵 (không lồng tiếng)")
    if st["removed"]:
        ex = "; ".join(f"\"{t[:14]}\"" for _, t, _ in st["removed"][:3])
        parts.append(f"bỏ {len(st['removed'])} câu không phải lời thoại ({ex}{'…' if len(st['removed']) > 3 else ''})")
    if st["unsure"]:
        parts.append(f"{len(st['unsure'])} chỗ chưa chắc (rê chuột vào câu gốc để xem)")
    return " · ".join(parts)


JOIN_ENGINE_CHOICES = [("auto", "Theo công cụ dịch đang chọn"), ("off", "Tắt")]


def join_engine_choices(settings: dict) -> list[tuple[str, str]]:
    """Lựa chọn cho ô "AI nối câu theo nghĩa": theo công cụ dịch / tắt / từng AI trong Cài đặt (ghi rõ AI chưa có key)."""
    from .translate import AI_ENGINES, ENGINE_NAMES, api_keys

    out = list(JOIN_ENGINE_CHOICES)
    for eng in ("gemini", "deepseek", "anthropic", "openai", "ollama"):
        if eng in AI_ENGINES:
            ok = eng == "ollama" or bool(api_keys(settings, eng))
            out.append((eng, ENGINE_NAMES[eng] + ("" if ok else " – chưa có API key")))
    return out


def join_engine(settings: dict, key: str = "ai_join_engine") -> tuple[str | None, str]:
    """AI dùng cho "AI nối câu theo nghĩa" (Cài đặt `ai_join_engine`) / "AI hỗ trợ nghe" (`asr_ai`): "auto" = công cụ
    dịch đang chọn nếu là AI có key, "off" = tắt, hoặc 1 công cụ AI trong Cài đặt. Trả (công cụ | None, lý do / tên)."""
    from .translate import AI_ENGINES, ENGINE_NAMES, api_keys

    v = settings.get(key, "auto") or "auto"
    if v == "off":
        return None, "đã tắt"
    eng = settings.get("translate_engine", "google") if v == "auto" else v
    if eng not in AI_ENGINES:
        box = "AI hỗ trợ nghe" if key == "asr_ai" else "AI nối câu theo nghĩa"
        return None, f"công cụ dịch đang chọn không phải AI – chọn 1 AI ở ô '{box}'"
    if eng != "ollama" and not api_keys(settings, eng):
        return None, f"chưa có API key {ENGINE_NAMES[eng]} trong Cài đặt → Công cụ dịch"
    return eng, ENGINE_NAMES[eng]


def ai_join_lines(segs: list, settings: dict, engine: str, progress: Prog | None = None, cancel=None,
                  profile: str = "") -> tuple[list, dict]:
    """AI đọc cả kịch bản (kèm khoảng nghỉ giữa các dòng, chỗ "nói liền") -> gộp các dòng liền nhau thực ra là 1 câu
    (`skills.clean_lines(mode="join")`); tool kiểm tra (liền nhau, cách ≤ 2,5 s, ≤ 36 chữ / 10 s, chữ giữ nguyên – chỉ
    đổi dấu câu ở chỗ nối) rồi mới áp. Trả (danh sách mới, thống kê `apply_cleanup`)."""
    from . import skills
    from .translate import LLMClient

    res = skills.clean_lines(LLMClient(settings, engine), segs, profile, "", progress, cancel, mode="join")
    return skills.apply_cleanup(segs, res)


def ai_join(p: Project, settings: dict | None = None, engine: str | None = None, progress: Prog | None = None,
            cancel=None) -> list[int]:
    """Nút "AI nối câu" (tab Phụ đề). Giữ bản cũ để "Hoàn tác nối"; câu gộp bỏ giọng cũ (bản lồng tự xếp lại). Trả số
    thứ tự các câu mới gộp (để tích sẵn)."""
    settings = settings or load_settings()
    eng = engine
    if not eng:
        eng, why = join_engine(settings)
        if not eng:
            raise ValueError(f"Chưa chọn được AI để nối câu: {why}.")
    segs = p.segments
    if not segs:
        raise ValueError("Chưa có phụ đề.")
    new, st = ai_join_lines(segs, settings, eng, progress, cancel, p.data.get("film_profile", ""))
    if not st["merged"]:
        return []
    p.data["join_backup"] = [s.to_dict() for s in segs]
    lost = forget_stale_voice({s.text for s in segs}, new, moved=True)
    p.segments = new
    p.save()
    if lost and has_dub(p):   # câu gộp: bỏ giọng cũ khỏi bản lồng (không đọc lại câu nào)
        relayout(p, settings)
    return list(st["changed"])


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


def speech_env(p: Project, progress: Prog | None = None, cancel=None) -> tuple:
    """Độ to giọng nói mỗi 0,05 s (giọng đã tách nếu có, không thì tiếng gốc) – tìm chỗ ngắt hơi. (None, hop) nếu lỗi."""
    from . import silence

    st = stems(p)
    src = st.get("vocals") or (str(p.source) if p.data.get("source") and p.source.exists() else "")
    if not src:
        return None, silence.HOP
    try:
        db = silence.loudness(src, p.work, progress, cancel, float(p.data.get("info", {}).get("duration", 0) or 0),
                              name="voice_50ms" if st else "loud_50ms")
    except ff.Cancelled:
        raise
    except Exception:  # noqa: BLE001 – không đo được: chia theo số chữ
        log_error("Đo chỗ ngắt hơi")
        return None, silence.HOP
    return db, silence.HOP


def split_long(p: Project, settings: dict | None = None, rows: list[int] | None = None, progress: Prog | None = None,
               cancel=None) -> list[int]:
    """Tách câu DỊCH dài ở dấu ? ! . / dấu phẩy (align.split_clauses): mỗi vế 1 dòng phụ đề + 1 đoạn giọng, giữa 2 vế nghỉ 1 nhịp.
    rows: chỉ các câu này. Giọng lồng của câu cũ bị bỏ (đọc lại 2 vế). Trả về số thứ tự các câu mới."""
    from . import align

    settings = settings or load_settings()
    segs = p.segments
    lang = trans_lang(p)
    if not any(s.trans.strip() for s in segs):
        return []
    env, hop = speech_env(p, progress, cancel)
    new, made = align.split_long_lines(segs, lang, float(settings.get("split_long_units", 14) or 14), 4, env, hop,
                                       float(settings.get("split_pause", 0.25) or 0), rows)
    if not made:
        return []
    p.data["split_backup"] = [s.to_dict() for s in segs]
    p.segments = new
    p.save()
    return made


def undo_split(p: Project, settings: dict | None = None) -> int:
    """Hoàn tác lần tách câu dài gần nhất. Trả về số câu sau khi hoàn tác (0 = không có gì)."""
    from .subtitle import Segment

    back = p.data.pop("split_backup", None)
    if not back:
        return 0
    p.segments = [Segment.from_dict(d) for d in back]
    p.save()
    if has_dub(p):
        relayout(p, settings or load_settings())
    return len(back)


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
    from .translate import LLMClient, length_marker

    settings = settings or load_settings()
    eng = ai_engine(settings, engine)
    segs = p.segments
    target = target_lang(p, settings)
    L = langs.INFO[target]
    fill = bool(settings.get("translate_fill", False))
    # độ dài theo thời gian câu thoại: "≤N tiếng" hoặc "M–N tiếng" (bật "Câu ngắn nói đầy đủ hơn": câu ngắn hơn M)
    items = [(i, x.text, x.trans, length_marker(x.duration, target, L["unit_vi"], fill).strip("⟪⟫"))
             for i, x in enumerate(segs)
             if x.trans.strip() and x.text.strip() and not x.extra.get("trans_user") and (rows is None or i in rows)]
    if not items:
        return []
    src = {"zh": "tiếng Trung", "yue": "tiếng Quảng Đông"}.get(source_lang(p), "tiếng gốc")
    from . import genres

    fixes = skills.review_lines(LLMClient(settings, eng), items, p.data.get("film_profile", ""),
                                skills.skill_text(settings), src, L["name"].lower(), L["unit_vi"], target,
                                progress, cancel, ctx=[(x.text, x.trans) for x in segs],
                                genre=genres.form(settings.get("translate_style"), target, settings))
    for i, (new, why) in fixes.items():
        segs[i].extra["trans_before_review"] = segs[i].trans
        segs[i].extra["review_note"] = why
        segs[i].trans = new
    if fixes:
        mark_song_lines(segs, sorted(fixes))      # AI thêm "♪ " cho lời bài hát -> 🎵 (không lồng tiếng)
        p.segments = segs
        p.save()
    return sorted(fixes)


def translate(p: Project, engine: str | None = None, style: str | None = None, only_empty: bool = False,
              settings: dict | None = None, progress: Prog | None = None, cancel=None,
              rows: list[int] | None = None, split: bool = True, fresh: bool = False) -> int:
    """Dịch phụ đề. rows: chỉ các câu này (dịch LẠI: bỏ bản dịch nhớ sẵn của chúng để ra bản mới). split False: không
    tự tách câu dài ở cuối (chạy tự động tách ở bước riêng `auto_split_long`). fresh: bản dịch đang có chỉ là bản dịch
    nháp lúc đang nghe (vừa tách vừa dịch) -> vẫn chạy AI sửa lỗi nghe / lọc lời bài hát như dịch cả phim lần đầu."""
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
    had_trans = any(x.trans.strip() for x in segs)
    first = fresh or not had_trans
    do_clean = (is_ai and whole and settings.get("ai_clean_before_translate", True) and not p.data.get("asr_cleaned")
                and first)
    # không bật sửa lỗi nghe thì vẫn nhờ AI lọc lời bài hát / chữ không phải lời thoại trước khi dịch cả phim lần đầu
    do_filter = (is_ai and whole and not do_clean and settings.get("ai_song_filter", True) and first
                 and not p.data.get("ai_filtered"))
    do_review = is_ai and whole and bool(settings.get("ai_review_after_translate", False))
    a0 = 0.0
    p.data.pop("skill_warning", None)
    if whole:
        p.data.pop("skill_note", None)
    if do_clean or do_filter:
        try:
            st = ai_cleanup(p, settings, eng, _sub(progress, 0.0, 0.18), cancel, mode="full" if do_clean else "filter")
            segs = p.segments
            if had_trans and st["changed"]:
                # câu đã dịch nháp lúc đang nghe mà AI vừa sửa chữ gốc / nối lại -> bản dịch cũ không còn đúng, dịch lại
                for i in st["changed"]:
                    if 0 <= i < len(segs) and not segs[i].extra.get("trans_user"):
                        segs[i].trans = ""
                p.segments = segs
                p.save()
            note = cleanup_note(st) or ("AI soát lời nghe: không có câu cần sửa" if do_clean
                                        else "AI lọc lời bài hát: không thấy lời bài hát / chữ thừa")
            p.data["skill_note"] = note
        except (ff.Cancelled, net.NetworkDown):
            raise
        except Exception as e:  # noqa: BLE001 – không chặn việc dịch
            log_error("AI sửa lỗi nghe & nối câu" if do_clean else "AI lọc lời bài hát")
            p.data["skill_warning"] = (f"Bỏ qua bước AI {'sửa lỗi nghe' if do_clean else 'lọc lời bài hát'} "
                                       f"({err_text(e)[:120]})")
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

    # chỗ nhân vật ngắt hơi mà câu gốc chưa có dấu câu -> "，" trước khi dịch (AI giữ đúng vế -> giọng lồng nghỉ đúng chỗ)
    min_gap = float(settings.get("dub_pause_min", 0.3))
    if settings.get("breath_marks", True) and source_lang(p) in ("zh", "yue", "auto"):
        from .align import mark_breaths

        mark_breaths(segs, min_gap, rows if rows is not None else
                     ([i for i, x in enumerate(segs) if not x.trans.strip()] if only_empty else None))
    before = [x.trans for x in segs]
    tr.translate_segments(segs, prog, cancel, only_empty=only_empty, summary=profile, auto_terms=not profile,
                          rows=set(rows) if rows is not None else None)
    new_rows = [i for i, x in enumerate(segs) if x.trans.strip() and x.trans != before[i]]   # câu vừa có bản dịch mới
    # câu dịch thiếu vế so với chỗ nhân vật ngắt hơi -> AI sửa cho đủ vế (2.1.3)
    if is_ai and new_rows and settings.get("trans_clause_match", True):
        try:
            nfix = match_clauses(segs, new_rows, tr, target, min_gap, cancel)
            if nfix:
                p.data["skill_note"] = ((p.data.get("skill_note", "") + " · ") if p.data.get("skill_note") else "") + \
                    f"giữ nhịp ngắt hơi: sửa {nfix} câu dịch cho đủ vế như câu gốc"
        except (ff.Cancelled, net.NetworkDown):
            raise
        except Exception:  # noqa: BLE001 – bước phụ
            log_error("Sửa số vế câu dịch")
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
    # đã kiểm tra cả phim rồi (chạy lại / dịch nốt dòng trống) -> chỉ kiểm tra các câu vừa dịch, không gửi lại cả phim
    rev_rows = new_rows if p.data.get("ai_reviewed") and not first else None
    if do_review and rev_rows == []:
        do_review = False
    if do_review:
        try:
            fixed = ai_review(p, settings, eng, _sub(progress, 0.85, 1.0), cancel, rows=rev_rows)
            n_rev = len(rev_rows) if rev_rows is not None else sum(1 for x in p.segments if x.trans.strip())
            p.data["ai_reviewed"] = True
            p.data["skill_note"] = ((p.data.get("skill_note", "") + " · ") if p.data.get("skill_note") else "") + \
                (f"AI kiểm tra và sửa {len(fixed)}/{n_rev} câu dịch" if fixed
                 else f"AI kiểm tra {n_rev} câu dịch: không có câu cần sửa")
            p.save()
        except (ff.Cancelled, net.NetworkDown):
            raise
        except Exception as e:  # noqa: BLE001
            log_error("AI kiểm tra bản dịch")
            p.data["skill_warning"] = f"Bỏ qua bước AI kiểm tra bản dịch ({err_text(e)[:120]})"
            p.save()
    if whole and split:
        # câu đã có giọng lồng giữ nguyên (tách thì mất giọng) – muốn tách thì bấm "Tách câu dài" ở tab Phụ đề
        auto_split_long(p, settings, voiced=False, cancel=cancel)
    return sum(1 for s in p.segments if s.trans)


def auto_split_long(p: Project, settings: dict | None = None, voiced: bool = False, progress: Prog | None = None,
                    cancel=None) -> int:
    """Tự tách câu dịch quá dài (> N tiếng, có dấu ? ! . / dấu phẩy ở cả câu gốc lẫn câu dịch) theo Cài đặt
    split_long_trans. voiced False: bỏ qua câu đã có giọng lồng (tách thì mất giọng); True: tách cả câu đó (chạy tự động
    – bước lồng tiếng ngay sau đọc lại các vế). Ghi skill_note, trả số câu dài đã tách."""
    settings = settings or load_settings()
    if not settings.get("split_long_trans", True) or not any(s.trans.strip() for s in p.segments):
        return 0
    before = len(p.segments)
    rows = None if voiced else [k for k, x in enumerate(p.segments) if not x.extra.get("tts_file")]
    made = split_long(p, settings, rows, progress, cancel)
    if not made:
        n_max = int(settings.get("split_long_units", 14) or 14)
        p.data["skill_note"] = ((p.data.get("skill_note", "") + " · ") if p.data.get("skill_note") else "") + \
            f"tách câu dài: không có câu dịch nào dài hơn {n_max} tiếng mà có dấu câu ở giữa"
        p.save()
        return 0
    n = len(made) - (len(p.segments) - before)
    p.data["skill_note"] = ((p.data.get("skill_note", "") + " · ") if p.data.get("skill_note") else "") + \
        f"tách {n} câu dịch dài ở dấu câu thành {len(made)} câu ngắn"
    p.save()
    return n


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


def voice_all(p: Project, settings: dict | None = None) -> str:
    """Giọng chung cho mọi người nói ("" = theo giọng từng người ở mục 2, "@auto" = tự gán nam / nữ)."""
    if "voice_all" in p.data:
        return p.data.get("voice_all") or ""
    if any((p.data.get("voices") or {}).values()):   # dự án cũ đã chọn giọng từng người -> giữ, không lấy mặc định chung
        return ""
    return (settings or load_settings()).get("dub_voice_all", "") or ""


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
    sig0 = [_seg_sig(s) for s in segs]   # câu lúc bắt đầu: người dùng sửa tiếp trong lúc chạy thì không bị đè (xem cuối)
    engine = engine or settings.get("tts_engine", "edge")
    default_voice = dub_default_voice(voice, engine, settings)
    voices = dict(p.data.get("voices") or {})
    # "Giọng cho tất cả người nói" (tab Tự động / mục 2): dự án nhớ riêng, chưa chọn thì theo mặc định ở Cài đặt -> mọi
    # nút lồng tiếng (Bắt đầu dịch tự động, 3. Lồng tiếng, Tạo bản lồng tiếng, Tạo lại giọng) đọc cùng 1 giọng đó
    va = voice_all(p, settings)
    if va and "voice_all" not in p.data:
        p.data["voice_all"] = va   # mặc định chung lúc lồng lần đầu -> dự án nhớ luôn (đổi Cài đặt sau không ảnh hưởng)
    if not voice and va and va != "@auto":
        voices = {k["speaker"]: va for k in speaker_list(p)}
        p.data["voices"] = voices
        default_voice = va
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
    dropped = trimmed = 0
    if not inplace and only is None and trans_lang(p) == "vi":
        if settings.get("dub_drop_interj", True):   # câu chỉ có "A!", "Ừm.", "Ái!"… -> xoá khỏi phụ đề trước khi đọc
            dropped = drop_interjections(p, segs, settings)
        if settings.get("interj_inline", True):     # "Ừm, đúng vậy." -> "Đúng vậy." (chỉ từ trong danh sách)
            trimmed = len(trim_interjections(segs, settings))
    st = stems(p)
    # mẫu giọng để nhái: lấy từ giọng đã tách (sạch) hoặc âm thanh gốc
    ref_audio = st.get("vocals") or str(p.source)
    (p.work / "dubs").mkdir(parents=True, exist_ok=True)
    out = p.work / "dubs" / f"dub_{time.strftime('%Y%m%d_%H%M%S')}.wav"
    cur = (p.edit.get("dub") or {}).get("wav")
    use_cur = bool(inplace and cur and Path(cur).exists())
    target = Path(cur) if use_cur else out
    tmp_out = target.with_name(target.stem + "_new.wav")
    stats: dict = {}
    # sửa bản đang dùng (xếp lại, xoá / kéo / gộp câu, đọc lại vài câu): chỉ ghi lại đúng đoạn thời gian thay đổi vào
    # file đó (~1 giây) thay vì dựng lại cả phim (phim 2 giờ: ghi lại ~500 MB + giải mã lại cho trình phát)
    ver = next((v for v in dub_versions(p) if v.get("wav") == str(target)), None) if use_cur else None
    patch = {"wav": str(target), "placed": ver["placed"]} if ver and ver.get("placed") else None
    old_stat = target.stat() if patch else None
    patched: list = []        # các đoạn đã vá (mẫu 24 kHz), qua mọi lần chạy
    full = [False]            # có lần dựng lại cả bản (ghi vào tmp_out)

    def run(pr):
        nonlocal patch
        stats.clear()
        synthesize_dub(segs, tmp_out, p.data.get("info", {}).get("duration", 0), p.work / "tts",
                       default_voice, rate or settings.get("tts_rate", "+0%"),
                       max_speedup=float(settings.get("tts_max_speedup", 1.3)), progress=pr, cancel=cancel,
                       voices=voices, ref_audio=ref_audio, settings=settings,
                       skip_songs=bool(settings.get("dub_skip_songs", True)), stats=stats,
                       only=set(only) if only is not None else None, lang=trans_lang(p),
                       genders=p.data.get("speaker_gender") or {}, patch=patch)
        if stats.get("patched") is not None:
            patched.extend(stats["patched"])
            patch = {"wav": str(target), "placed": stats["placed_tab"]}
        else:
            full[0] = True
            patch = None      # bản mới nằm ở tmp_out -> lần chạy sau (rút gọn) cũng dựng cả bản

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
    stats["interj_trim"] = trimmed
    stats["auto_voiced"] = auto_voiced
    if full[0]:
        try:
            tmp_out.replace(target)
            out = target
        except OSError:   # Windows: file đang được chương trình khác mở -> lưu thành bản mới
            tmp_out.replace(out)
    else:
        out = target
    env = stats.pop("wave", None)
    top = stats.pop("env_top", None)
    placed_tab = stats.pop("placed_tab", None)
    stats.pop("patched", None)
    stats.pop("patch_skipped", None)
    if env is not None:
        import numpy as np

        np.save(dub_env_path(out), env)   # sóng âm từng đoạn giọng lồng cho timeline
    elif patched:
        top = _patch_dub_env(out, patched, (ver or {}).get("env_top"))
        from .audiomix import patch_track

        try:   # file nghe thử của trình phát: vá đúng các đoạn đó (không giải mã lại cả phim)
            patch_track(out, p.work / "live", "dub", "", old_stat, patched)
        except Exception:  # noqa: BLE001 – không vá được thì trình phát tự giải mã lại như cũ
            log_error("Vá file nghe thử bản lồng")
    p.segments = segs            # lưu độ vừa (fit) + bản dịch đã rút gọn
    layout = stats.pop("layout", [])
    p.data["dub_layout"] = layout
    p.data["dub_stats"] = stats
    vers = dub_versions(p)
    entry = {"wav": str(out), "time": time.strftime("%H:%M %d/%m"), "voices": _voice_summary(voices, default_voice),
             "stats": stats, "layout": layout}
    if placed_tab is not None:
        entry["placed"] = placed_tab   # đoạn giọng đã đặt trong bản này -> lần sửa sau chỉ vá phần thay đổi
    if top is not None:
        entry["env_top"] = top
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
    if inplace:
        _keep_user_edits(p, sig0)
    p.save()
    return out


# kết quả lồng tiếng ghi vào từng câu (xếp lại / đọc lại) – được ghép vào câu tương ứng khi người dùng sửa song song
_DUB_EXTRA = ("tts_file", "dub_at", "fit", "fit_speed", "fit_need", "fit_have", "fit_shift", "fit_over", "voice_used",
              "take", "take_pending", "dub_pauses")


def _seg_sig(s) -> tuple:
    return round(s.start, 3), round(s.end, 3), s.text, s.trans


def seg_sigs(segs) -> list[tuple]:
    """Dấu nhận ra danh sách câu (giờ, chữ gốc, bản dịch) – giao diện so với đĩa để không ghi đè câu việc nền vừa sửa."""
    return [_seg_sig(s) for s in segs]


def _keep_user_edits(p: Project, sig0: list) -> None:
    """Xếp lại / đọc lại vài câu chạy ở nền trong lúc người dùng vẫn sửa (xoá tiếp câu khác, kéo câu, đổi kiểu chữ…)
    và giao diện đã lưu dự án: giữ mọi sửa đổi đó (lấy dự án mới nhất trên đĩa), chỉ ghép phần của việc lồng tiếng vào
    (giọng / vị trí từng câu còn lại, bản lồng, các bản đã lưu). Trước đây việc chạy xong ghi đè cả dự án -> câu vừa xoá
    hiện lại. Câu còn thay đổi sẽ được lần xếp lại kế tiếp (giao diện tự xếp hàng) cập nhật."""
    fresh = Project(p.id)
    if not fresh.exists:
        return
    data = fresh.data
    fsegs = fresh.segments
    if [_seg_sig(s) for s in fsegs] != sig0:
        mine = {}
        for s in p.segments:
            mine.setdefault((round(s.start, 3), round(s.end, 3), s.text), s)
        for s in fsegs:
            j = mine.get((round(s.start, 3), round(s.end, 3), s.text))
            if j is None:
                continue
            for k in _DUB_EXTRA:
                if k in j.extra:
                    s.extra[k] = j.extra[k]
                else:
                    s.extra.pop(k, None)
        data["segments"] = [s.to_dict() for s in fsegs]
    else:
        data["segments"] = p.data.get("segments", [])
    for k in ("dub_layout", "dub_stats", "dub_versions", "voices", "voice_all"):
        if k in p.data:
            data[k] = p.data[k]
    ed = dict(data.get("edit") or {})
    ed["dub"] = dict((p.data.get("edit") or {}).get("dub") or {})
    data["edit"] = ed
    p.data = data


def drop_interjections(p: Project, segs: list | None = None, settings: dict | None = None) -> int:
    """Xoá khỏi phụ đề các câu CHỈ gồm từ cảm thán trong danh sách người dùng điền (A!, Ừm., Ái!, Hả?…) trước khi lồng
    tiếng Việt – câu "Ừm, đúng là như vậy." giữ nguyên. Câu bị xoá cất ở p.data["interj_removed"] (khôi phục được); câu
    đã khôi phục (extra["keep_interj"]) không bị xoá lại. Sửa `segs` tại chỗ (hoặc p.segments) và trả về số câu đã xoá."""
    from .voicefix import interj_list, lone_interjection

    phrases = interj_list(settings if settings is not None else load_settings())
    own = segs is None
    segs = p.segments if own else segs
    keep, gone = [], []
    for s in segs:
        if s.trans.strip() and not s.extra.get("keep_interj") and lone_interjection(s.trans, phrases):
            gone.append(s)
        else:
            keep.append(s)
    if gone:
        p.data.setdefault("interj_removed", []).extend(s.to_dict() for s in gone)
        segs[:] = keep
        if own:
            p.segments = segs
    return len(gone)


def trim_interjections(segs: list, settings: dict | None = None) -> list[int]:
    """Bỏ các vế CHỈ gồm từ cảm thán trong danh sách khỏi câu dịch có lời thật ("Ừm, đúng vậy." -> "Đúng vậy.");
    bản cũ cất ở extra["trans_before_interj"] (Khôi phục câu cảm thán đưa lại). Trả số thứ tự các câu đã sửa lời."""
    from .voicefix import interj_list, strip_interjections

    phrases = interj_list(settings if settings is not None else load_settings())
    out = []
    for i, s in enumerate(segs):
        if not s.trans.strip() or s.extra.get("keep_interj"):
            continue
        new = strip_interjections(s.trans, phrases)
        if new != s.trans and new.strip():
            s.extra.setdefault("trans_before_interj", s.trans)
            s.trans = new
            out.append(i)
    return out


def restore_interjections(p: Project) -> int:
    """Đưa lại các câu cảm thán đã bị xoá vào đúng chỗ theo thời gian + trả lại các vế cảm thán đã bỏ trong câu dài
    (đánh dấu để lần lồng sau không lọc nữa). Trả số câu được khôi phục / trả lại lời."""
    from .subtitle import Segment

    back = [Segment.from_dict(d) for d in p.data.pop("interj_removed", []) or []]
    segs = p.segments
    n = 0
    for s in segs:
        old = s.extra.pop("trans_before_interj", None)
        if old:
            s.trans = old
            s.extra["keep_interj"] = True
            n += 1
    for s in back:
        s.extra["keep_interj"] = True
    if not back and not n:
        return 0
    p.segments = sorted(segs + back, key=lambda s: (s.start, s.end))
    p.save()
    return len(back) + n


def vocals16k(p: Project, progress: Prog | None = None, cancel=None, why: str = "đo cao độ") -> Path:
    """Giọng đã tách bản 16 kHz mono (đoán đoạn hát, lọc câu nghe nhầm). Chưa tách giọng -> báo."""
    st = stems(p)
    if not st:
        raise ValueError("Chưa tách giọng / nhạc nền – bấm 'Tách ngay' ở mục 1 tab Lồng tiếng trước (" + why +
                         " trên giọng đã tách khỏi nhạc)")
    wav = p.work / "asr16k_vocals.wav"
    if not wav.exists() or wav.stat().st_size < 1000 or wav.stat().st_mtime < Path(st["vocals"]).stat().st_mtime:
        ff.run(["-i", st["vocals"], "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(wav)],
               p.data.get("info", {}).get("duration", 0), progress, cancel, "Đang chuẩn bị giọng đã tách")
    return wav


def check_voices(p: Project, settings: dict | None = None, progress: Prog | None = None, cancel=None,
                 tol: float | None = None, trim: bool | None = None) -> dict:
    """Nút "Lọc câu nghe nhầm": đo cao độ giọng thật từng người nói -> đánh dấu câu nghi là nhạc / tiếng động bị chép
    thành lời (`extra["noise"]`) + căn đầu / cuối câu theo đoạn có giọng. Câu đổi giờ mà đã lồng tiếng -> xếp lại."""
    from .songs import read_wav16
    from .voicecheck import check_lines

    settings = settings or load_settings()
    wav = vocals16k(p, progress, cancel, "lọc câu nghe nhầm đo cao độ")
    segs = p.segments
    before = [(s.start, s.end) for s in segs]
    res = check_lines(segs, read_wav16(wav), float(settings.get("pitch_tol", 4.0) if tol is None else tol),
                      bool(settings.get("pitch_trim", True) if trim is None else trim), progress=progress)
    p.segments = segs
    p.save()
    if res["trimmed"] and has_dub(p) and any((s.start, s.end) != b for s, b in zip(segs, before)):
        relayout(p, settings, progress, cancel)
    return res


def detect_songs(p: Project, settings: dict | None = None, progress: Prog | None = None, cancel=None) -> int:
    """Tìm các cụm câu đang HÁT trên giọng đã tách (dự án đã nghe từ trước) -> đánh dấu 🎵. Cần đã tách giọng."""
    wav = vocals16k(p, progress, cancel, "đoán đoạn hát đo")
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
    """Sóng âm 0,01 s của bản lồng (2.0.97; bản cũ "<bản lồng>.env.npy" 0,05 s – tự làm lại khi mở)."""
    return Path(wav).with_suffix(".env10.npy")


def _old_env_path(wav: str | Path) -> Path:
    return Path(wav).with_suffix(".env.npy")


def _dub_env_full(wav: str | Path) -> float:
    """Tính sóng âm cả bản lồng (đọc từng khối từ đĩa, không nạp cả phim vào RAM), lưu dub_env_path. Trả mức chuẩn."""
    import numpy as np

    from .tts import ENV_HOP, SR, env_top, norm_peaks, wav_info, wave_peaks

    off, frames, sr, ch, sw = wav_info(wav)
    envp = dub_env_path(wav)
    if (sr, ch, sw) != (SR, 1, 2):     # bản lồng lạ (không phải tool ghi): giải mã cả file như cũ
        import wave as _wave

        with _wave.open(str(wav)) as w:
            a = np.frombuffer(w.readframes(w.getnframes()), np.int16).astype(np.float32) / 32768.0
            if w.getnchannels() > 1:
                a = a.reshape(-1, w.getnchannels()).mean(axis=1)
        peaks = wave_peaks(a, w.getframerate())
    else:
        hop = int(SR * ENV_HOP)
        a = np.memmap(str(wav), dtype=np.int16, mode="r", offset=off, shape=(frames,))
        step = hop * 20000
        peaks = np.concatenate([wave_peaks(a[i:i + step].astype(np.float32) / 32767.0, SR)
                                for i in range(0, frames, step)] or [np.zeros(0, np.float32)])
        del a
    top = env_top(peaks)
    np.save(envp, norm_peaks(peaks, top))
    try:
        _old_env_path(wav).unlink(missing_ok=True)   # sóng âm 0,05 s của bản trước 2.0.97
    except OSError:
        pass
    return top


def _patch_dub_env(wav: str | Path, regions: list, top: float | None) -> float:
    """Sóng âm timeline của bản lồng vừa vá: chỉ tính lại các đoạn đã vá (thiếu file / mức chuẩn -> tính cả bản).
    Trả mức chuẩn hoá (đỉnh 99,5 %) đang dùng."""
    import numpy as np

    from .tts import ENV_HOP, SR, wav_info, wave_peaks

    off, frames, sr, _ch, _sw = wav_info(wav)
    hop = int(SR * ENV_HOP)
    envp = dub_env_path(wav)
    env = None
    if top is not None and envp.exists():
        try:
            env = np.load(envp).astype(np.float32)
        except (OSError, ValueError):
            env = None
    if env is None or len(env) != frames // hop:
        return _dub_env_full(wav)
    with open(wav, "rb") as f:
        for r0, r1 in regions:
            h0, h1 = r0 // hop, min(len(env), -(-r1 // hop))
            if h1 <= h0:
                continue
            f.seek(off + h0 * hop * 2)
            a = np.frombuffer(f.read((h1 - h0) * hop * 2), dtype=np.int16).astype(np.float32) / 32767.0
            env[h0:h0 + len(a) // hop] = wave_peaks(a, SR) / max(top, 1e-4)
    np.save(envp, np.clip(env, 0, 1).astype(np.float16))
    return top


def dub_wave(p: Project) -> tuple[str, float] | None:
    """(file sóng âm, bước giây) của bản lồng đang dùng – bản cũ chưa có thì tạo (chạy ở luồng nền)."""
    wav = (p.edit.get("dub") or {}).get("wav")
    if not wav or not Path(wav).exists():
        return None
    from .tts import ENV_HOP

    env = dub_env_path(wav)
    if not env.exists() or env.stat().st_mtime < Path(wav).stat().st_mtime:
        _dub_env_full(wav)
    return str(env), ENV_HOP



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
    return follow_dub(segs, **follow_params(settings))


def follow_params(settings: dict) -> dict:
    """Cài đặt giờ hiện phụ đề (tab Phụ đề → Công cụ → Giờ hiện)."""
    mode = settings.get("sub_follow_mode", "always")
    if mode != "always":
        return {"mode": "shift"}
    return {"mode": "always", "lead": float(settings.get("sub_lead", 0.1)), "hold": float(settings.get("sub_hold", 0.3)),
            "min_dur": float(settings.get("sub_min_dur", 1.0)), "join": float(settings.get("sub_join_gap", 0.3))}


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
            ex = segs[k].extra
            if ex.get("take_pending"):
                # lần "Tạo lại giọng" trước bị Huỷ / dừng giữa chừng: giữ đúng lần đọc đó -> các câu đã đọc xong được
                # dùng lại, chỉ đọc tiếp câu còn thiếu (trước đây đánh lần đọc mới -> đọc lại TẤT CẢ từ đầu)
                continue
            ex["take"] = int(ex.get("take", 0) or 0) + 1
            ex["take_pending"] = True
    p.segments = segs
    p.save()
    out = dub(p, None, rate, settings, progress, cancel, engine, inplace=True, only=rows)
    segs = p.segments
    for k in rows:
        if k < len(segs):
            segs[k].extra.pop("take_pending", None)
    p.segments = segs
    p.save()
    return out


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


def fix_punct(p: Project, segs: list, rows=None, settings: dict | None = None) -> list[int]:
    """Sửa dấu câu bản dịch đã có (2.1.11, không gọi AI): bỏ dấu phẩy chen giữa 1 cụm từ / tên riêng trong bảng thuật
    ngữ, thêm dấu câu cuối theo câu gốc (bản trước 2.1.11 tự bỏ dấu chấm cuối câu; AI hay quên "?"). Câu lời hát (♪)
    và câu đã sửa tay vẫn được sửa dấu câu. Trả về chỉ số các câu đổi lời."""
    from .translate import parse_glossary, punct_fix

    settings = settings if settings is not None else load_settings()
    lang = trans_lang(p)
    g = dict(parse_glossary(settings.get("glossary", ""))) if lang == "vi" else {}
    g.update(parse_glossary(p.data.get(glossary_key(lang), "")))
    names = [v for v in g.values() if isinstance(v, str) and len(v.split()) >= 2]
    changed = []
    for k in (range(len(segs)) if rows is None else rows):
        x = segs[k]
        if not (x.trans or "").strip():
            continue
        new = punct_fix(x.trans.strip(), x.text, lang, names)
        if new != x.trans:
            x.trans = new
            changed.append(k)
    return changed


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
        if spec.startswith("vieneu:trend:") or spec.startswith("vieneu:mau:"):
            return voice_label(spec)
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
        _old_env_path(wav).unlink(missing_ok=True)
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
    if st.get("pauses"):
        extra.append(f"nghỉ đúng chỗ nhân vật ngắt hơi {st['pauses']} chỗ")
    if st.get("clauses"):
        extra.append(f"{st['clauses']} câu đọc từng vế theo nhịp ngắt của nhân vật" +
                     (f" ({st['part_fast']} vế dài đọc nhanh riêng)" if st.get("part_fast") else ""))
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
    if st.get("interj_trim"):
        extra.append(f"bỏ tiếng cảm thán đứng riêng trong {st['interj_trim']} câu dài")
    if st.get("errors"):
        extra.append(f"{st['errors']} câu lỗi")
    if st.get("slow_restarts"):
        extra.append(f"bộ đọc chậm bất thường {st['slow_restarts']} lần (card đồ hoạ đầy) – đã tự mở lại")
    if st.get("babble_fixed"):
        extra.append(f"{st['babble_fixed']} câu ngắn VieNeu đọc lặp lời – đã tự cắt phần lặp / đọc lại")
    if st.get("babble_left"):
        rows = sorted(set(st["babble_left"]))
        extra.append(f"⚠ câu {', '.join(map(str, rows[:12]))}{'…' if len(rows) > 12 else ''} vẫn đọc lặp / kéo dài sau 3 lần "
                     "đọc – sửa lời (thêm 1–2 tiếng) hoặc đổi giọng rồi bấm Tạo lại giọng")
    if st.get("long_fixed"):
        extra.append(f"{st['long_fixed']} câu VieNeu đọc dài bất thường (lặp / kéo dài) – đã tự đọc lại")
    if st.get("long_left"):
        rows = sorted(set(st["long_left"]))
        extra.append(f"⚠ câu {', '.join(map(str, rows[:12]))}{'…' if len(rows) > 12 else ''} vẫn đọc dài bất thường sau "
                     "khi đọc lại – nghe thử, lặp lời thì sửa lời hoặc đổi giọng rồi bấm Tạo lại giọng")
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
    if spec.startswith(tts.VN_TREND):
        from . import trend_voices

        return ((trend_voices.get(spec) or {}).get("name") or spec[len(tts.VN_TREND):]) + " (VieNeu)"
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
    # phụ đề đã dịch khi có giọng lồng: theo giờ HIỆN (như video xuất – 2.0.96), phụ đề gốc theo giờ nói
    segs = subs_follow_dub(p, p.segments, None, settings, which) if which != "source" else p.segments
    save_subtitles(segs, out, which, {**(settings.get("sub_style") or {}), "lang": tl}, info.get("width") or 1920,
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


def effective_style(p: Project, style: dict, ed: dict | None = None) -> dict:
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
    ed = ed if ed is not None else p.edit
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
    """Xuất video theo bản chỉnh sửa + các tỉ lệ xuất thêm (edit["extra_ratios"]: mỗi tỉ lệ 1 file "_9x16"…, phần
    chỉnh riêng của tỉ lệ trong edit["ratio_over"]). Trả về file bản chính."""
    from .video import build_and_render, edit_for_ratio, ratio_tag

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
    name = out_name or (safe_name(Path(p.data.get("source", "video")).stem) + "_vietsub")
    stamp = time.strftime('%H%M%S')
    batch = time.strftime('%Y%m%d-%H%M%S')
    segs0 = p.segments
    ratios = [edit.get("aspect", "original")] + [r for r in edit.get("extra_ratios") or []
                                                if r != edit.get("aspect", "original")]
    main_out = None
    for k, ratio in enumerate(ratios):
        e = edit if k == 0 else edit_for_ratio(edit, ratio)
        if k:
            e["_tag"] = ratio_tag(ratio)
        from .config import _deep_merge

        base_style = _deep_merge(settings.get("sub_style") or {}, (e.get("subtitles") or {}).get("style") or {})
        e.setdefault("subtitles", {})["style"] = effective_style(p, base_style, e)
        nm = name if k == 0 else f"{name}_{ratio_tag(ratio)}"
        out = p.output_dir / f"{safe_name(nm)}.mp4"
        if out.exists():
            out = p.output_dir / f"{safe_name(nm)}_{stamp}.mp4"
        segs = [Segment(s.start, s.end, s.text, s.trans, s.speaker, dict(s.extra)) for s in segs0]
        if e["subtitles"]["style"].get("karaoke"):
            for i, sp in karaoke_spans(p, e).items():
                segs[i].extra["kv"] = sp
        # câu có giọng lồng đọc sớm / bị lùi / đọc dài: phụ đề hiện theo đúng lúc giọng đọc
        segs = subs_follow_dub(p, segs, e, settings, (e.get("subtitles") or {}).get("which", "trans"))
        pr = progress
        if progress and len(ratios) > 1:
            pr = (lambda x, m, k=k, ratio=ratio: progress(  # noqa: E731
                (k + x) / len(ratios), f"[{k + 1}/{len(ratios)} · {ratio if ratio != 'original' else 'gốc'}] {m}"))
        build_and_render(p.source, out, e, settings, p.work / "render", segs, pr, cancel)
        size = {}
        try:
            from . import ffmpeg as ff

            vi = ff.probe(out)
            size = {"w": vi.width, "h": vi.height}
        except Exception:  # noqa: BLE001
            pass
        p.add_output(out, "video", ratio=ratio, batch=batch, **size)
        main_out = main_out or out
    return main_out


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
        if settings.get("split_long_trans", True):
            steps.append("split")      # dịch XONG hết mới tách câu dài, tách xong hết mới lồng tiếng
    if opts.get("dub") and not no_tr:
        steps.append("dub")
    steps.append("export")
    # render False (tab Tự động): dừng trước bước ghép video – chỉ lưu cách xuất + SRT, xuất video bấm tay
    weights = {"download": 1, "recognize": 4, "translate": 2, "split": 0.2, "dub": 2,
               "export": 3 if opts.get("render", True) else 0.2}
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
    if not no_tr and not opts.get("render", True):
        result["message"] = ("Xong (chưa xuất video): " + " → ".join(
            {"download": "tải video", "recognize": "nghe & tách câu", "translate": "dịch", "split": "tách câu dài",
             "dub": "lồng tiếng"}[x]
            for x in steps if x != "export") + ". Kiểm tra rồi bấm 'Xuất video' để ghép video")
    elif not no_tr:
        result["message"] = "Hoàn tất! " + (f"Video: {Path(result['video']).name}" if result.get("video") else "")
    if opts.get("parallel") and p.data.get("live_reused"):
        result["message"] += f"  · vừa tách vừa dịch: dùng lại {p.data['live_reused']} câu dịch sẵn lúc đang nghe"
    if "recognize" in steps and result.get("message") and listen_note(p):
        result["message"] += "  · " + listen_note(p)
    if "recognize" in steps and p.data.get("snap_stats") and result.get("message"):
        result["message"] += "  · " + snap_note(p.data["snap_stats"])
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
            meta: dict = {}
            path = download_video(opts["url"], p.dir / "source", pr, cancel, notes=notes, settings=settings, meta=meta)
            p.data["video_meta"] = meta
            attach_source(p, path)
            from .autoflow import video_name

            p.data["name"] = video_name(path)
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
            # vừa tách vừa dịch: chỉ dịch các câu chưa có bản dịch (câu bị đổi sau khi nghe lại / căn / tách người nói).
            # Chạy lại trên dự án đã dịch (cùng tiếng đích, không nhận dạng lại): chỉ dịch câu còn trống – trước đây dịch
            # lại cả phim -> tốn API + mất các câu đã sửa tay
            again = ("recognize" not in steps and any(x.trans.strip() for x in p.segments)
                     and trans_lang(p) == target_lang(p, settings))
            translate(p, opts.get("engine"), opts.get("style"),
                      bool(opts.get("only_empty")) or bool(opts.get("parallel")) or again, settings, pr, cancel,
                      split=False, fresh=bool(opts.get("parallel")) and "recognize" in steps)
        elif st == "split":
            pr(0.0, "Đang tách câu dịch dài…")
            # có bước lồng tiếng ngay sau -> tách cả câu đã có giọng (các vế được đọc ở bước đó)
            auto_split_long(p, settings, voiced="dub" in steps, progress=pr, cancel=cancel)
        elif st == "dub":
            _auto_voices(p, opts, settings)
            if opts.get("separate") and not stems(p):
                from . import separate as sepmod

                if sepmod.available(settings.get("sep_model")):
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
    p.data["voice_all"] = v
    if not v:
        p.save()
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
