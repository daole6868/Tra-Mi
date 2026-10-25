"""Tự động từ link: 1 bộ "thiết lập tự động" dùng chung (tab Tự động, Trang chủ, Xử lý hàng loạt) + xếp hàng các link
(mỗi link 1 dự án: việc tải video chạy trước – không chờ việc dài – rồi tới việc dịch tự động; link đã tải thì mở lại
dự án cũ)."""
from __future__ import annotations

import re
from urllib.parse import parse_qs, urlsplit

from .config import PROJECTS_DIR, load_settings

# các lựa chọn riêng của tab Tự động (phần còn lại – công cụ dịch, tách giọng, kỹ năng AI, tách câu dài… – vốn là Cài đặt
# chung); lưu ở Cài đặt "auto_preset"
PRESET_DEFAULT = {"method": "asr", "language": "zh", "hardsub_mode": "none", "which": "trans", "dub": False,
                  "denoise": False}


def preset_values(settings: dict | None = None) -> dict:
    st = settings if settings is not None else load_settings()
    return {**PRESET_DEFAULT, **(st.get("auto_preset") or {})}


def preset(settings: dict | None = None, render: bool = False) -> dict:
    """opts cho pipeline.auto theo thiết lập tự động dùng chung. render False: dừng trước bước xuất video."""
    st = settings if settings is not None else load_settings()
    pv = preset_values(st)
    which = pv.get("which") or "trans"
    return {"method": pv["method"], "asr_engine": st.get("asr_engine"),
            "ocr_fps": float(st.get("ocr_fps") or 4), "language": pv["language"],
            "separate": bool(st.get("sep_before_asr", True)), "diarize": bool(st.get("diarize", True)),
            "engine": st.get("translate_engine"), "target": st.get("target_lang", "vi"),
            "style": st.get("translate_style"), "hardsub_mode": pv["hardsub_mode"],
            "burn": which != "none", "which": which if which != "none" else "trans",
            "dub": bool(pv["dub"]), "tts_engine": st.get("tts_engine", "edge"),
            "voice": st.get("dub_voice_all", "") or "", "parallel": bool(st.get("auto_parallel", False)),
            "denoise": bool(pv["denoise"]), "render": bool(render)}


def summary(settings: dict | None = None) -> str:
    """1 dòng tóm tắt thiết lập tự động (Trang chủ / Hàng loạt)."""
    from . import langs
    from .gui import common as C
    from .pipeline import voice_label

    st = settings if settings is not None else load_settings()
    o = preset(st)

    def name(choices, val, n=None):
        t = dict(choices).get(val, str(val or ""))
        t = t.split(" – ")[0].split(" (")[0]
        return t[:n] if n else t

    parts = []
    if o["method"] == "ocr":
        parts.append("OCR phụ đề cứng")
    else:
        parts.append(name(C.ASR_ENGINES, o["asr_engine"]))
    if o["engine"] == "none":
        parts.append("không dịch")
    else:
        parts.append(f"{name(C.ENGINES, o['engine'])} → {langs.INFO[langs.norm(o['target'])]['name']}")
        if o["style"] and o["style"] != "auto":
            parts.append(name(C.STYLES, o["style"]))
        if st.get("split_long_trans", True):
            parts.append("tách câu dài")
    if o["hardsub_mode"] != "none":
        parts.append("phụ đề cũ: " + name(C.HARDSUB, o["hardsub_mode"]).lower())
    if o["dub"]:
        v = o["voice"]
        vn = "tự gán nam / nữ" if v == "@auto" else (voice_label(v) if v else "")
        parts.append("lồng tiếng " + name(C.TTS_ENGINES, o["tts_engine"]).split(" ")[0] + (f" ({vn})" if vn else ""))
    else:
        parts.append("không lồng tiếng")
    return " · ".join(p for p in parts if p)


# ------------------------------------------------------------------ link trùng
_BV = re.compile(r"(BV[0-9A-Za-z]{10})")
_AV = re.compile(r"/video/av(\d+)", re.I)
_YT = re.compile(r"(?:youtu\.be/|[?&]v=|/shorts/)([0-9A-Za-z_-]{11})")
_NUM = re.compile(r"/(?:video|share/video|note)/(\d{8,})")


def link_key(url: str) -> str:
    """Mã nhận ra cùng 1 video dù link khác dạng (www / m., có / không tham số chia sẻ). Không gọi mạng."""
    u = (url or "").strip()
    if not u:
        return ""
    m = _BV.search(u)
    if m:
        page = (parse_qs(urlsplit(u).query).get("p") or ["1"])[0]
        return f"bili:{m.group(1)}:{page}"
    m = _AV.search(u)
    if m:
        return f"bili:av{m.group(1)}"
    m = _YT.search(u)
    if m:
        return f"yt:{m.group(1)}"
    q = parse_qs(urlsplit(u).query)
    for k in ("modal_id", "vid", "aweme_id"):
        if q.get(k):
            return f"id:{q[k][0]}"
    m = _NUM.search(u)
    if m:
        return f"id:{m.group(1)}"
    s = urlsplit(u)
    return f"{s.netloc.lower().removeprefix('www.').removeprefix('m.')}{s.path.rstrip('/')}"


def find_by_url(url: str) -> str:
    """Dự án đã tải link này (cùng video) – '' nếu chưa có."""
    import json

    key = link_key(url)
    if not key or not PROJECTS_DIR.exists():
        return ""
    for d in sorted(PROJECTS_DIR.iterdir(), reverse=True):
        f = d / "project.json"
        if not f.is_file():
            continue
        try:
            j = json.loads(f.read_text("utf-8"))
        except Exception:  # noqa: BLE001
            continue
        if j.get("link_key") == key:
            return j.get("id", "")
    return ""


# ------------------------------------------------------------------ xếp hàng
def _download(pid: str, url: str):
    def fn(j):
        from . import pipeline
        from .project import Project

        pr = Project(pid)
        notes: list[str] = []
        path = pipeline.download_video(url, pr.dir / "source", j.update, j.cancelled, notes=notes)
        pipeline.attach_source(pr, path)
        pr.data["name"] = path.stem[:80]
        pr.data["dl_warning"] = " ".join(notes)
        pr.save()
        return {"message": "Đã tải xong video" + ("  ⚠ " + " ".join(notes) if notes else "")}
    return fn


def _auto(pid: str, opts: dict):
    def fn(j):
        from . import pipeline
        from .project import Project

        if not (PROJECTS_DIR / pid / "project.json").is_file():
            raise RuntimeError("Bỏ qua: tải video không được (xem lỗi ở việc tải phía trên)")
        p = Project(pid)
        if not p.data.get("source"):
            raise RuntimeError("Bỏ qua: tải video không được (xem lỗi ở việc tải phía trên)")
        return pipeline.auto(p, dict(opts), j.update, j.cancelled)
    return fn


def queue_links(urls: list[str], auto: bool = True, settings: dict | None = None, render: bool = False,
                aspect: str = "original") -> list[dict]:
    """Mỗi link: dự án mới + việc "Tải video" (chạy trước, không chờ việc dài) + (auto) việc "Dịch tự động" theo
    thiết lập tự động (chờ tải xong). Link đã tải trước đó -> không tải lại (auto: dự án cũ chưa có phụ đề thì dịch tiếp).
    Trả [{url, pid, new, queued}] theo thứ tự."""
    from .jobs import manager
    from .project import Project

    st = settings if settings is not None else load_settings()
    opts = preset(st, render)
    out = []
    seen: set[str] = set()
    for url in urls:
        key = link_key(url)
        if not key or key in seen:
            continue
        seen.add(key)
        old = find_by_url(url)
        if old:
            queued = False
            p = Project(old)
            if auto and p.data.get("source") and not p.segments and not _busy(old):
                manager.submit("auto", f"Dịch tự động: {p.data.get('name', '')[:50]}", _auto(old, opts), old)
                queued = True
            out.append({"url": url, "pid": old, "new": False, "queued": queued})
            continue
        p = Project.create("Video từ link")
        p.data["url"], p.data["link_key"] = url, key
        if aspect and aspect != "original":
            e = p.edit
            e["aspect"] = aspect
            p.data["edit"] = e
        p.save()
        manager.submit("download", f"Tải video: {url[:50]}", _download(p.id, url), p.id)
        if auto:
            manager.submit("auto", f"Dịch tự động: {url[-40:]}", _auto(p.id, opts), p.id)
        out.append({"url": url, "pid": p.id, "new": True, "queued": True})
    return out


def _busy(pid: str) -> bool:
    from .jobs import manager

    return any(j["project_id"] == pid and j["status"] in ("queued", "running") for j in manager.list())
