"""Tự động từ link: 1 bộ "thiết lập tự động" dùng chung (tab Tự động, Trang chủ, Xử lý hàng loạt) + xếp hàng các link
(mỗi link 1 dự án: việc tải video chạy trước – không chờ việc dài – rồi tới việc dịch tự động; link đã tải thì mở lại
dự án cũ)."""
from __future__ import annotations

import re
from pathlib import Path
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
        ai_on = st.get("ocr_listen_unsure") and (st.get("ocr_ai_engine", "auto") or "auto") != "off"
        extra = [x for x, on in (("giờ kết hợp giọng nói", st.get("ocr_timing") == "mix"),
                                 ("lọc màu chữ", st.get("ocr_color")),
                                 ("làm rõ chữ", st.get("ocr_eq") and not st.get("ocr_color")),
                                 ("AI đọc lại + nghe câu không đạt" if ai_on else "nghe lại câu không đạt",
                                  st.get("ocr_listen_unsure")))
                 if on]
        parts.append("OCR phụ đề cứng" + (f" ({', '.join(extra)})" if extra else ""))
    elif o["method"] == "hybrid":
        parts.append("OCR + nghe bằng " + name(C.ASR_ENGINES, o["asr_engine"]))
    else:
        parts.append(name(C.ASR_ENGINES, o["asr_engine"]))
    if o["method"] != "ocr" and st.get("asr_dual"):
        parts.append("nghe 2 bộ rồi so")
    if o["engine"] == "none":
        parts.append("không dịch")
    else:
        parts.append(f"{name(C.ENGINES, o['engine'])} → {langs.INFO[langs.norm(o['target'])]['name']}")
        if o["style"] and o["style"] != "auto":
            parts.append(name(C.STYLES, o["style"]))
        from .translate import LOCAL_ENGINES

        if o["engine"] not in LOCAL_ENGINES:   # các bước AI chỉ chạy với công cụ dịch AI (không với Google / Hy-MT2)
            from .pipeline import pre_translate_ai

            fix_on, join_on = pre_translate_ai(st)
            ai = (["sửa lỗi nghe"] if fix_on else []) + (["nối câu"] if join_on else [])
            if st.get("ai_song_filter", True) and not fix_on:     # sửa lỗi nghe đã gồm lọc lời bài hát
                ai.append("lọc lời hát")
            if st.get("ai_review_after_translate", False):
                ai.append("kiểm tra bản dịch")
            if st.get("dub_fit", False):     # 2.4.10: mặc định tắt = giữ bản dịch sát nghĩa
                ai.append("rút gọn bản dịch cho vừa thời gian nói")
            if ai:
                parts.append("AI " + ", ".join(ai))
        if langs.norm(o["target"]) == "vi":    # 2.4.3/2.4.4: tên pinyin -> Hán Việt / từ lóng (mặc định tắt)
            from .hanviet import kinds_from

            kd = kinds_from(st)
            vi = []
            if "person" in kd and "place" in kd:
                vi.append("tên riêng → Hán Việt")
            elif "person" in kd:
                vi.append("tên người → Hán Việt")
            elif "place" in kd:
                vi.append("địa danh, môn phái → Hán Việt")
            if "term" in kd:
                vi.append("từ thường → Hán Việt")
            if st.get("trans_slang"):
                vi.append("hiểu từ lóng")
            if vi:
                parts.append(", ".join(vi))
        if st.get("split_long_trans", True):
            parts.append("tách câu dài")
    if o["hardsub_mode"] != "none":
        parts.append("phụ đề cũ: " + name(C.HARDSUB, o["hardsub_mode"]).lower())
    if o["dub"]:
        v = o["voice"]
        vn = "tự gán nam / nữ" if v == "@auto" else (voice_label(v) if v else "")
        parts.append("lồng tiếng " + name(C.TTS_ENGINES, o["tts_engine"]).split(" ")[0] + (f" ({vn})" if vn else ""))
        from .translate import LOCAL_ENGINES as _LOCAL

        if st.get("dub_auto_shorten", False) and o["engine"] not in _LOCAL and o["engine"] != "none":
            parts.append("AI tự rút gọn câu quá dài khi lồng")
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
    """Dự án đã tải link này (cùng video) – '' nếu chưa có. Nhiều dự án cùng link -> ưu tiên dự án đã có video."""
    import json

    from .project import TOMB

    key = link_key(url)
    if not key or not PROJECTS_DIR.exists():
        return ""
    found = []
    for d in sorted(PROJECTS_DIR.iterdir(), reverse=True):
        f = d / "project.json"
        if not f.is_file() or (d / TOMB).exists():
            continue
        try:
            j = json.loads(f.read_text("utf-8"))
        except Exception:  # noqa: BLE001
            continue
        if j.get("link_key") == key and j.get("id"):
            found.append((not _has_video(j.get("source")), j["id"]))
    return min(found, key=lambda x: x[0])[1] if found else ""


def _has_video(src) -> bool:
    return bool(src) and Path(src).is_file()


# ------------------------------------------------------------------ xếp hàng
def _download(pid: str, url: str):
    def fn(j):
        from . import pipeline
        from .project import Project

        pr = Project(pid)
        notes: list[str] = []
        meta: dict = {}
        try:
            path = pipeline.download_video(url, pr.dir / "source", j.update, j.cancelled, notes=notes, meta=meta)
        except Exception as e:  # noqa: BLE001
            # tải dở (máy chủ từ chối giữa chừng, Huỷ, mất mạng quá lâu): phần đã tải giữ trong source/ -> dự án KHÔNG
            # bị xoá, Tải tiếp làm nốt
            if isinstance(e, pipeline.PartialDownload) or has_partial(pid):
                pr = Project(pid)
                pr.data["dl_partial"] = True
                pr.save()
            raise
        pr = Project(pid)
        pr.data.pop("dl_partial", None)
        pipeline.attach_source(pr, path)
        pr.data["video_meta"] = meta   # tiêu đề / mô tả: gợi ý tên riêng cho bộ nghe
        pr.data["name"] = video_name(path)
        pr.data["dl_warning"] = " ".join(notes)
        pr.save()
        return {"message": "Đã tải xong video" + ("  ⚠ " + " ".join(notes) if notes else "")}
    return fn


def video_name(path: Path) -> str:
    """Tên dự án từ file tải về "<tiêu đề ≤ 80> [<mã>].mp4": bỏ mã; video nhiều phần (Bilibili ?p=N, mã "…_p2") mà
    tiêu đề dài bị cắt mất "p02" -> thêm "(P2)" để 3 phần không trùng tên."""
    stem = Path(path).stem
    m = re.match(r"(.*\S)\s*\[([^\[\]]+)\]$", stem)
    base, vid = (m.group(1), m.group(2)) if m else (stem, "")
    part = re.search(r"_p(\d+)$", vid)
    if part:
        n = int(part.group(1))
        if not re.search(rf"\bp0*{n}\b", base, re.I):
            base = base[:72].rstrip() + f" (P{n})"
    return base[:80] or stem[:80]


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
    Dự án cũ chưa có video (lần trước tải lỗi / tắt tool giữa lúc tải / file video đã mất) -> tải lại vào chính dự án
    đó; đang tải / chờ tải -> không xếp thêm.
    Trả [{url, pid, new, queued, state, name}] theo thứ tự; state: new / retry (tải lại) / busy (đang tải, đang làm) /
    again (đã có video, dịch tiếp) / have (đã có, không làm gì)."""
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
            p = Project(old)
            name = p.data.get("name", "")
            r = {"url": url, "pid": old, "new": False, "queued": False, "name": name}
            if _busy(old):
                out.append({**r, "state": "busy"})
                continue
            if not _has_video(p.data.get("source")) and not p.segments:
                # lần trước tải không xong (lỗi mạng, tắt tool khi đang tải / đang chờ lượt tải) -> trước đây báo "đã
                # tải trước đó" mãi mà không bao giờ tải lại
                p.data["url"], p.data["dl_auto"] = url, bool(auto)
                p.save()
                manager.submit("download", f"Tải video: {url[:50]}", _download(old, url), old)
                if auto:
                    manager.submit("auto", f"Dịch tự động: {url[-40:]}", _auto(old, opts), old)
                out.append({**r, "queued": True, "state": "retry"})
                continue
            if auto and _has_video(p.data.get("source")) and not p.segments:
                manager.submit("auto", f"Dịch tự động: {name[:50]}", _auto(old, opts), old)
                out.append({**r, "queued": True, "state": "again"})
                continue
            out.append({**r, "state": "have"})
            continue
        p = Project.create("Video từ link")
        p.data["url"], p.data["link_key"], p.data["dl_auto"] = url, key, bool(auto)
        if aspect and aspect != "original":
            e = p.edit
            e["aspect"] = aspect
            p.data["edit"] = e
        p.save()
        manager.submit("download", f"Tải video: {url[:50]}", _download(p.id, url), p.id)
        if auto:
            manager.submit("auto", f"Dịch tự động: {url[-40:]}", _auto(p.id, opts), p.id)
        out.append({"url": url, "pid": p.id, "new": True, "queued": True, "state": "new", "name": ""})
    return out


def queue_message(res: list[dict], auto: bool = True, render: bool = False) -> str:
    """Thông báo sau khi xếp hàng link (Trang chủ / Hàng loạt): nói rõ link nào tải mới, tải lại, đang tải, đã có."""
    def names(rs):
        nm = [f"“{r['name'][:30]}”" for r in rs if r.get("name")][:2]
        return f" ({', '.join(nm)}{'…' if len(rs) > len(nm) and nm else ''})" if nm else ""

    by = {k: [r for r in res if r.get("state") == k] for k in ("new", "retry", "busy", "again", "have")}
    what = "tải + dịch tự động" if auto else "tải"
    parts = []
    if by["new"]:
        parts.append(f"Đã xếp hàng {what} {len(by['new'])} video")
    if by["retry"]:
        parts.append(f"tải tiếp {len(by['retry'])} video lần trước chưa tải xong (phần đã tải được giữ lại)")
    if by["busy"]:
        parts.append(f"{len(by['busy'])} link đang tải / đang làm – xem 'Việc đang làm'")
    if by["again"]:
        parts.append(f"{len(by['again'])} link đã tải trước đó, chưa có phụ đề -> dịch tiếp{names(by['again'])}")
    if by["have"]:
        parts.append(f"{len(by['have'])} link đã có dự án{names(by['have'])} – mở ở Trang chủ, không tải lại")
    if auto and not render and (by["new"] or by["retry"] or by["again"]):
        parts.append("dừng trước bước xuất – kiểm tra rồi bấm 'Xuất video'")
    return " · ".join(parts)


def resume_download(pid: str) -> list[dict]:
    """Nút 'Tải tiếp' (việc tải video bị lỗi): xếp lại việc tải vào CHÍNH dự án đó – fastdl tải tiếp đúng khúc còn thiếu
    (file .svpart + bảng khúc trong source/) – kèm dịch tự động nếu lần đầu có. [] nếu dự án đã có video / không có link."""
    from .project import Project

    if not (PROJECTS_DIR / pid / "project.json").is_file():
        return []
    p = Project(pid)
    url = p.data.get("url") or ""
    if not url or _has_video(p.data.get("source")):
        return []
    if not p.data.get("link_key"):
        p.data["link_key"] = link_key(url)
        p.save()
    return queue_links([url], auto=bool(p.data.get("dl_auto", True)))


def has_partial(pid: str) -> bool:
    """Dự án có phần video tải dở (giữ lại để Tải tiếp – không xoá dự án trống khi việc tải lỗi)."""
    from .fastdl import PART

    d = PROJECTS_DIR / pid / "source"
    try:
        return any(f.is_file() and f.stat().st_size >= 1_000_000 and (f.name.endswith((PART, ".part")) or ".f" in f.name)
                   for f in d.iterdir()) if d.is_dir() else False
    except OSError:
        return False


def _busy(pid: str) -> bool:
    from .jobs import manager

    return any(j["project_id"] == pid and j["status"] in ("queued", "running") for j in manager.list())
