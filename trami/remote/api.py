"""Thao tác điều khiển từ xa (2.3.0) – hàm thường, không biết gì về HTTP (server.py gọi; test gọi thẳng).

Quy tắc: ĐỌC dự án thì đọc thẳng file; GHI dự án (sửa câu, lưu vùng phụ đề, đổi cách xuất…) luôn là 1 việc trong hàng
việc (kind "remote" – nhẹ, không chờ việc dài của dự án khác) -> không ghi chồng lúc việc khác của dự án đang chạy, và
giao diện trên máy tính tự nạp lại khi việc xong (MainWindow.poll -> project_page.job_finished)."""
from __future__ import annotations

import base64
import re
import time
from pathlib import Path

from .. import autoflow, pipeline
from ..config import PROJECTS_DIR, load_settings, save_settings
from ..jobs import manager
from ..project import Project

PID_RE = re.compile(r"^[A-Za-z0-9_.-]{1,80}$")
DONE = ("done", "error", "cancelled")

# Cài đặt điện thoại được đổi (không bao giờ có key / mật khẩu / đường dẫn)
SETTING_KEYS = {
    "translate_engine": str, "translate_style": str, "translate_skill": str, "target_lang": str,
    "ai_review_after_translate": bool, "split_long_trans": bool, "translate_video": bool,
    "tts_engine": str, "tts_rate": str, "dub_overflow": str, "tts_max_speedup": float, "dub_subs_follow": bool,
    "dub_auto_shorten": bool, "dub_voice_all": str,
    "ocr_color": bool, "ocr_eq": bool,
}
OVERFLOW = [("smart", "Tự động"), ("push", "Nối tiếp"), ("overlap", "Đọc đè"), ("early", "Đọc sớm"), ("cut", "Cắt bớt")]
RATES = [("-10%", "Chậm"), ("+0%", "Bình thường"), ("+10%", "Nhanh"), ("+20%", "Rất nhanh")]
SPEEDUPS = [1.15, 1.3, 1.45, 1.6]
CRFS = [("20", "Cao"), ("23", "Trung bình – nhẹ để đăng"), ("16", "Rất cao (file lớn)")]
EXPORT_ASPECTS = [("original", "Gốc"), ("9:16", "9:16"), ("16:9", "16:9"), ("1:1", "1:1"), ("4:5", "4:5")]


class ApiError(Exception):
    def __init__(self, msg: str, code: int = 400):
        super().__init__(msg)
        self.code = code


# ================================================================== chung
def project(pid: str) -> Project:
    if not PID_RE.match(pid or "") or not (PROJECTS_DIR / pid / "project.json").is_file():
        raise ApiError("Không thấy dự án", 404)
    return Project(pid)


def job_brief(j: dict) -> dict:
    return {k: j.get(k) for k in ("id", "kind", "title", "project_id", "status", "progress", "eta", "finished",
                                  "started", "created")} | {
        "message": (j.get("message") or "")[:400], "error": (j.get("error") or "")[:400],
        "result_msg": ((j.get("result") or {}).get("message") or "")[:600] if isinstance(j.get("result"), dict) else ""}


def badge(pr: dict, jobs: list[dict]) -> tuple[str, str]:
    """(chữ, loại) – loại: run / wait / err / done / check / heard / none (như thẻ dự án Trang chủ)."""
    mine = [j for j in jobs if j["project_id"] == pr["id"]]
    run = next((j for j in mine if j["status"] == "running"), None)
    if run:
        return ("Đang tải… " if run["kind"] == "download" else "Đang làm… ") + f"{run['progress'] * 100:.0f} %", "run"
    if any(j["status"] == "queued" for j in mine):
        return "Đang chờ", "wait"
    done = sorted((j for j in mine if j.get("finished")), key=lambda j: j["finished"])
    if done and done[-1]["status"] == "error":
        return "⚠ Lỗi", "err"
    if pr.get("outputs") and any((o or {}).get("kind") == "video" for o in pr["outputs"]):
        return "✓ Đã xuất", "done"
    if pr.get("n_translated"):
        return "Chờ kiểm tra & xuất", "check"
    if pr.get("n_segments"):
        return "Đã nghe, chưa dịch", "heard"
    if not pr.get("info"):
        return "Chưa có video", "none"
    return "Đã tải · chọn vùng rồi chạy", "new"


def state() -> dict:
    jobs = manager.list()
    projects = []
    for pr in Project.list_all()[:60]:
        text, kind = badge(pr, jobs)
        info = pr.get("info") or {}
        projects.append({"id": pr["id"], "name": pr.get("name") or pr["id"], "updated": pr.get("updated"),
                         "duration": info.get("duration") or 0, "n": pr.get("n_segments", 0),
                         "nt": pr.get("n_translated", 0), "badge": text, "bk": kind,
                         "thumb": (PROJECTS_DIR / pr["id"] / "thumb.jpg").is_file()})
    st = load_settings()
    try:
        summ = autoflow.summary(st)
    except Exception:  # noqa: BLE001
        summ = ""
    return {"jobs": [job_brief(j) for j in jobs[:40]], "projects": projects, "summary": summ}


def events(since: float) -> list[dict]:
    """Việc đã xong / lỗi sau mốc `since` (app Android hỏi định kỳ để báo lên điện thoại)."""
    return [job_brief(j) for j in manager.list()
            if j["status"] in ("done", "error") and (j.get("finished") or 0) > since]


def submit(pid: str, kind: str, title: str, fn) -> dict:
    p = project(pid)
    name = p.data.get("name") or pid
    job = manager.submit(kind, f"{title}: {name}", lambda j: fn(j, Project(pid)), pid)
    return job.to_dict()


def wait(job: dict, timeout: float = 8.0) -> dict:
    """Chờ việc nhẹ xong để trả kết quả ngay (việc phải xếp hàng sau việc dài -> trả trạng thái đang chờ)."""
    end = time.time() + timeout
    j = manager.get(job["id"])
    while j and j.status not in DONE and time.time() < end:
        if j.status == "queued" and time.time() > end - timeout + 1.5:
            break
        time.sleep(0.05)
    return job_brief(j.to_dict()) if j else job


def edit(pid: str, title: str, fn, kind: str = "remote", timeout: float = 8.0) -> dict:
    """Sửa dự án qua hàng việc. fn(p) -> dict | None (message, …)."""
    def run(j, p):
        res = fn(p) or {}
        return res if isinstance(res, dict) else {}
    return wait(submit(pid, kind, title, run), timeout)


# ================================================================== tuỳ chọn
def options(pid: str | None = None) -> dict:
    from .. import langs, skills, tts
    from ..gui import common as C

    st = load_settings()
    lang = st.get("target_lang", "vi")
    if pid:
        try:
            lang = pipeline.target_lang(project(pid), st)
        except ApiError:
            pass
    try:
        voices = [{"id": v["id"], "name": v["name"], "g": v.get("gender") or ""} for v in tts.list_voices(st, lang)]
    except Exception:  # noqa: BLE001
        voices = []
    try:
        skl = [{"id": s["id"], "name": s["name"]} for s in skills.all_skills(st)]
    except Exception:  # noqa: BLE001
        skl = []
    vals = {k: st.get(k) for k in SETTING_KEYS}
    vals["target_lang"] = lang
    return {
        "engines": [{"id": k, "name": n.split(" (")[0].split(" – ")[0]} for k, n in C.ENGINES]
        + [{"id": pipeline.NO_TRANSLATE, "name": "Không dịch"}],
        "targets": [{"id": k, "name": n.split(" (")[0]} for k, n in C.TARGETS],
        "styles": [{"id": k, "name": n} for k, n in C.STYLES],
        "skills": [{"id": "", "name": "Không dùng"}] + skl,
        "tts": [{"id": k, "name": n.split(" (")[0]} for k, n in C.TTS_ENGINES],
        "voices": voices, "overflow": [{"id": k, "name": n} for k, n in OVERFLOW],
        "rates": [{"id": k, "name": n} for k, n in RATES], "speedups": SPEEDUPS,
        "hardsub": [{"id": k, "name": n} for k, n in C.HARDSUB],
        "which": [{"id": k, "name": n} for k, n in C.which_choices(lang)],
        "aspects": [{"id": k, "name": n} for k, n in EXPORT_ASPECTS],
        "resolutions": [{"id": k, "name": n} for k, n in C.RESOLUTIONS], "crfs": [{"id": k, "name": n} for k, n in CRFS],
        "values": vals, "lang_name": langs.INFO[langs.norm(lang)]["name"],
    }


def set_settings(new: dict) -> dict:
    upd = {}
    for k, v in (new or {}).items():
        typ = SETTING_KEYS.get(k)
        if typ is None:
            continue
        try:
            upd[k] = typ(v) if typ is not bool else bool(v)
        except (TypeError, ValueError):
            raise ApiError(f"Giá trị không hợp lệ: {k}") from None
    if upd:
        save_settings(upd)
    return {"saved": sorted(upd)}


def queue_links(text: str, auto: bool) -> dict:
    from ..sitelogin import extract_urls

    urls = extract_urls(text or "")
    if not urls:
        raise ApiError("Không thấy link trong ô – dán link video hoặc cả dòng chia sẻ của app")
    res = autoflow.queue_links(urls, auto=auto)
    return {"message": autoflow.queue_message(res, auto), "projects": [r.get("pid") for r in res]}


# ================================================================== dự án
def detail(pid: str) -> dict:
    p = project(pid)
    st = load_settings()
    segs = p.segments
    n = len(segs)
    nt = sum(1 for s in segs if s.trans.strip())
    ds = p.data.get("dub_stats") or {}
    vids = [o for o in p.data.get("outputs") or [] if (o or {}).get("kind") == "video"]
    pv = autoflow.preset_values(st)
    box = pipeline.ocr_box(p)
    info = p.data.get("info") or {}
    mine = [job_brief(j) for j in manager.list(pid)[:8]]
    return {
        "id": pid, "name": p.data.get("name") or pid, "info": {k: info.get(k) for k in ("width", "height", "duration", "fps")},
        "has_video": bool(p.data.get("source")) and not pipeline.source_missing(p),
        "method": p.data.get("sub_source") or pv.get("method") or "asr", "preset_method": pv.get("method") or "asr",
        "box": box, "n": n, "nt": nt, "dubbed": pipeline.has_dub(p), "dub_placed": ds.get("placed", 0),
        "videos": len(vids), "target": pipeline.target_lang(p, st), "jobs": mine,
        "speakers": len({s.speaker for s in segs if s.speaker}), "thumb": (p.dir / "thumb.jpg").is_file(),
    }


def run_step(pid: str, step: str) -> dict:
    """Bước 1–4 + chạy trọn gói – đúng như nút trên máy (dùng thiết lập ở Cài đặt / tab Tự động)."""
    st = dict(load_settings())
    p = project(pid)
    if step in ("recognize", "auto") and not p.data.get("source"):
        raise ApiError("Dự án chưa có video")
    o = autoflow.preset(st)
    if step == "recognize":
        st.update({"asr_engine": o["asr_engine"], "diarize": o["diarize"], "sep_before_asr": o["separate"]})

        def fn(j, p):
            from ..gui.project_page import recognize_message

            n = pipeline.recognize(p, o["method"], o["language"], None, o["ocr_fps"], st, j.update, j.cancelled,
                                   o["denoise"])
            return {"message": recognize_message(p, n, o["method"], o["separate"], st)}
        return submit(pid, "recognize", "Lấy phụ đề", fn)
    if step == "translate":
        return translate(pid, "auto")
    if step == "dub":
        if not any(s.trans.strip() for s in p.segments):
            raise ApiError("Làm bước 2 (Dịch) trước đã")

        def fn(j, p):
            pipeline.dub(p, None, st.get("tts_rate", "+0%"), st, j.update, j.cancelled, st.get("tts_engine", "edge"))
            return {"message": pipeline.dub_summary(p)}
        return submit(pid, "dub", "Lồng tiếng", fn)
    if step == "render":
        def fn(j, p):
            out = pipeline.render(p, None, j.update, j.cancelled, None)
            return {"message": f"Đã xuất: {out.name}", "path": str(out)}
        return submit(pid, "render", "Xuất video", fn)
    if step == "auto":
        o["render"] = False

        def fn(j, p):
            return pipeline.auto(p, o, j.update, j.cancelled)
        return submit(pid, "auto", "Dịch tự động", fn)
    raise ApiError("Bước không hợp lệ")


def set_target(pid: str, lang: str) -> dict:
    from .. import langs

    lang = langs.norm(lang)

    def fn(p):
        p.data["target_lang"] = lang
        p.save()
        return {"message": f"Dịch sang {langs.INFO[lang]['name']}"}
    save_settings({"target_lang": lang})
    return edit(pid, "Đổi tiếng đích", fn)


# ================================================================== vùng phụ đề
def _src(p: Project) -> Path:
    if not p.data.get("source") or pipeline.source_missing(p):
        raise ApiError("Không thấy video gốc trên máy tính", 404)
    return p.source


def frame(pid: str, t: float, width: int = 960) -> Path:
    from .. import ffmpeg as ff
    from ..config import DATA_DIR

    p = project(pid)
    src = _src(p)
    dur = float((p.data.get("info") or {}).get("duration") or 0)
    t = max(0.0, min(float(t), max(0.0, dur - 0.05) if dur else float(t)))
    width = max(160, min(1920, int(width)))
    out = DATA_DIR / "remote" / "cache" / f"{pid}_{int(round(t * 10))}_{width}.jpg"
    if not out.exists():
        _trim_cache(out.parent)
        ff.snapshot(src, t, out, width=width)
    return out


def _trim_cache(d: Path, keep: int = 200):
    try:
        fs = sorted(d.glob("*.jpg"), key=lambda f: f.stat().st_mtime)
        for f in fs[:-keep]:
            f.unlink(missing_ok=True)
    except OSError:
        pass


def _box(b: dict | None) -> dict:
    if not isinstance(b, dict):
        raise ApiError("Thiếu khung vùng quét")
    try:
        x, y, w, h = (max(0.0, min(1.0, float(b[k]))) for k in ("x", "y", "w", "h"))
    except (KeyError, TypeError, ValueError):
        raise ApiError("Khung vùng quét không hợp lệ") from None
    w, h = min(w, 1 - x), min(h, 1 - y)
    if w < 0.02 or h < 0.02:
        raise ApiError("Khung quá nhỏ")
    return {"x": round(x, 4), "y": round(y, 4), "w": round(w, 4), "h": round(h, 4)}


def detect_region(pid: str) -> dict:
    from .. import ocr
    from ..gui.project_page import ocr_box_from_detect

    p = project(pid)
    r = ocr.detect_subtitle_region(_src(p), None, load_settings())
    if not r.get("found"):
        return {"found": False, "box": None, "t": 0, "message": "Không tự dò được phụ đề – kéo khung bằng tay"}
    return {"found": True, "box": ocr_box_from_detect(r), "t": float(r.get("first_t") or 0),
            "message": "Đã dò thấy dòng phụ đề – kiểm tra rồi bấm Dùng khung này"}


def _reader_image(src: Path, t: float, box: dict, st: dict):
    """(ảnh màu vùng quét BGR, ảnh bộ đọc chữ thấy BGR) – đúng như lúc quét (làm rõ chữ / lọc màu)."""
    import cv2
    import numpy as np

    from .. import ocr, ocrcolor

    if st.get("ocr_color") and (st.get("ocr_colors") or []):
        img = ocrcolor.grab(src, t, box, max_w=1280)
        if img is None:
            raise ApiError("Không chụp được khung hình")
        _, seen = ocrcolor.preview(img, st)
        g = cv2.imdecode(np.frombuffer(seen, np.uint8), cv2.IMREAD_COLOR)
        return img, (g if g is not None else img)
    orig, gray = ocr.preview_scan(src, t, box, st if st.get("ocr_eq") else {**st, "ocr_eq": False})
    a = cv2.imdecode(np.frombuffer(orig, np.uint8), cv2.IMREAD_COLOR) if orig else None
    if a is None:
        raise ApiError("Không chụp được khung hình")
    if not st.get("ocr_eq"):
        return a, a
    g = cv2.imdecode(np.frombuffer(gray, np.uint8), cv2.IMREAD_COLOR) if gray else None
    return a, (g if g is not None else a)


def read_region(pid: str, t: float, box: dict) -> dict:
    """Đọc thử chữ trong khung tại giây t: ảnh bộ đọc thấy + các dòng đọc được + cảnh báo (chữ sát mép = bị cắt)."""
    import cv2

    from .. import ocr_engine

    p = project(pid)
    st = load_settings()
    box = _box(box)
    _, seen = _reader_image(_src(p), float(t), box, st)
    h, w = seen.shape[:2]
    rows = []
    for b, txt, conf in ocr_engine.get(st).ocr(seen):
        txt = (txt or "").strip()
        if not txt or conf < 0.5:
            continue
        xs, ys = [float(q[0]) for q in b], [float(q[1]) for q in b]
        x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
        lh = max(1.0, y1 - y0)
        edge = x0 <= 2 or x1 >= w - 3 or y0 <= max(2.0, 0.08 * lh) or y1 >= h - max(3.0, 0.08 * lh)
        rows.append({"text": txt, "conf": round(float(conf), 3), "edge": bool(edge),
                     "cy": round((y0 + y1) / 2 / h, 3), "cx": round((x0 + x1) / 2 / w, 3)})
    rows.sort(key=lambda r: (round(r["cy"] * 10), r["cx"]))
    ok, buf = cv2.imencode(".jpg", seen, [cv2.IMWRITE_JPEG_QUALITY, 82])
    img = "data:image/jpeg;base64," + base64.b64encode(buf.tobytes()).decode() if ok else ""
    if not rows:
        kind, msg = "err", "✗ Không thấy chữ trong khung – kéo khung tới chỗ có chữ phụ đề"
    elif any(r["edge"] for r in rows):
        kind, msg = "warn", "⚠ Có chữ sát mép khung (có thể bị cắt) – nới khung ra"
    elif len({round(r["cy"] * 10) for r in rows}) > 2 or len(rows) > 3:
        kind, msg = "warn", "⚠ Khung có nhiều cụm chữ – có thể dính chữ trong cảnh, thu khung lại"
    else:
        cmin = min(r["conf"] for r in rows)
        kind, msg = "ok", f"✓ Đọc được: {' / '.join(r['text'] for r in rows)} · chắc {cmin:.2f}"
    return {"kind": kind, "message": msg, "lines": rows, "image": img}


def seek_text(pid: str, t: float, box: dict, direction: int = 1) -> dict:
    """Câu trước / câu sau có chữ: dò từng 1,5 s (tối đa 90 s) tới khung có chữ trong khung quét (khác câu đang xem)."""
    from .. import ocr_engine, ocrcolor

    p = project(pid)
    src = _src(p)
    st = load_settings()
    box = _box(box)
    eng = ocr_engine.get(st)
    dur = float((p.data.get("info") or {}).get("duration") or 0) or 1e9
    step = 1.5 if direction >= 0 else -1.5
    cur = None
    img = ocrcolor.grab(src, float(t), box, max_w=960)
    if img is not None:
        cur = _sig(eng, img)
    tt = float(t)
    for _ in range(60):
        tt += step
        if tt < 0 or tt > dur:
            break
        img = ocrcolor.grab(src, tt, box, max_w=960)
        if img is None:
            continue
        sig = _sig(eng, img)
        if sig and sig != cur:
            return {"found": True, "t": round(tt, 2)}
        if not sig:
            cur = None
    return {"found": False, "t": float(t), "message": "Không thấy câu có chữ nào nữa theo hướng này"}


def _sig(eng, img) -> str:
    try:
        boxes = eng.detect(img)
    except Exception:  # noqa: BLE001
        return ""
    if not boxes:
        return ""
    h = img.shape[0]
    big = [b for b in boxes if (max(float(q[1]) for q in b) - min(float(q[1]) for q in b)) > 0.12 * h]
    if not big:
        return ""
    return ",".join(sorted(f"{round(min(float(q[0]) for q in b) / 24)}" for b in big))


def pick_color(pid: str, t: float, x: float, y: float) -> dict:
    from .. import ocrcolor

    p = project(pid)
    img = ocrcolor.grab(_src(p), float(t), None, max_w=1280)
    if img is None:
        raise ApiError("Không chụp được khung hình")
    h, w = img.shape[:2]
    return {"color": ocrcolor.pick(img, int(max(0.0, min(1.0, float(x))) * (w - 1)),
                                   int(max(0.0, min(1.0, float(y))) * (h - 1)))}


def use_color(fill: str) -> dict:
    if not re.match(r"^#[0-9A-Fa-f]{6}$", fill or ""):
        raise ApiError("Màu không hợp lệ")
    save_settings({"ocr_color": True, "ocr_colors": [{"fill": fill.upper(), "outline": ""}]})
    return {"message": f"Chỉ lấy chữ màu {fill.upper()} khi quét"}


def save_region(pid: str, box: dict | None) -> dict:
    b = _box(box) if box else None

    def fn(p):
        if b:
            p.data["ocr_box"] = b
        else:
            p.data.pop("ocr_box", None)
        p.save()
        return {"message": "Đã lưu vùng phụ đề" if b else "Đã bỏ khung – tool sẽ tự dò khi chạy"}
    return edit(pid, "Vùng phụ đề", fn)


# ================================================================== bảng câu + dịch
def lines(pid: str, offset: int = 0, limit: int = 60, flt: str = "all", q: str = "") -> dict:
    p = project(pid)
    st = load_settings()
    segs = p.segments
    unsure = set(pipeline.unsure_rows(segs, float(st.get("asr_pick_conf", 0.7)),
                                      float(st.get("ocr_pick_conf", 0.85))))
    q = (q or "").strip().lower()
    idx = []
    for i, s in enumerate(segs):
        if flt == "empty" and (s.trans.strip() or not s.text.strip()):
            continue
        if flt == "unsure" and i not in unsure:
            continue
        if flt in ("long", "fast", "ok") and pipeline.speed_cat(s) != flt:
            continue
        if q and q not in (s.text + " " + s.trans).lower():
            continue
        idx.append(i)
    offset = max(0, int(offset))
    rows = []
    for i in idx[offset:offset + max(1, min(200, int(limit)))]:
        s = segs[i]
        ex = s.extra
        rows.append({"i": i, "start": round(s.start, 2), "end": round(s.end, 2), "spk": s.speaker or "",
                     "text": s.text, "trans": s.trans, "unsure": i in unsure, "song": bool(ex.get("song")),
                     "fit": pipeline.speed_cat(s) or "", "speed": ex.get("fit_speed") or 0,
                     "voice": ex.get("voice_used") or ex.get("voice") or ""})
    return {"total": len(segs), "count": len(idx), "offset": offset, "rows": rows,
            "empty": sum(1 for s in segs if s.text.strip() and not s.trans.strip())}


def _check_rows(p: Project, rows: list, texts: list | None) -> list[int]:
    segs = p.segments
    out = []
    for k, r in enumerate(rows or []):
        r = int(r)
        if not 0 <= r < len(segs):
            raise ApiError("Câu không còn – tải lại bảng câu", 409)
        if texts and k < len(texts) and texts[k] is not None and segs[r].text != texts[k]:
            raise ApiError("Bảng câu đã đổi trên máy tính – tải lại rồi làm lại", 409)
        out.append(r)
    return sorted(set(out))


def edit_line(pid: str, i: int, text: str | None, trans: str) -> dict:
    trans = (trans or "").strip()

    def fn(p):
        segs = p.segments
        r = _check_rows(p, [i], [text])[0]
        segs[r].trans = trans
        segs[r].extra["trans_user"] = True
        p.segments = segs
        p.save()
        return {"message": f"Đã lưu câu #{r + 1}"}
    return edit(pid, "Sửa câu", fn)


def delete_lines(pid: str, rows: list, texts: list | None) -> dict:
    p0 = project(pid)
    dubbed = pipeline.has_dub(p0)

    def fn(p):
        segs = p.segments
        rs = set(_check_rows(p, rows, texts))
        p.segments = [s for k, s in enumerate(segs) if k not in rs]
        p.save()
        msg = f"Đã xoá {len(rs)} câu"
        if dubbed and pipeline.has_dub(p):
            pipeline.relayout(p, load_settings())
            msg += " · đã xếp lại bản lồng"
        return {"message": msg}
    return edit(pid, "Xoá câu", fn, kind="relayout" if dubbed else "remote", timeout=20)


def translate(pid: str, mode: str, rows: list | None = None, texts: list | None = None) -> dict:
    """mode: auto (như bước 2 trên máy: chưa dịch -> dịch hết, dịch dở -> câu còn trống, xong hết -> dịch lại hết),
    empty, all, rows (dịch lại câu đã tích; đã có bản lồng -> đọc lại giọng đúng các câu đó)."""
    st = dict(load_settings())
    eng, sty = st.get("translate_engine"), st.get("translate_style")
    if eng == pipeline.NO_TRANSLATE:
        raise ApiError("Đang chọn 'Không dịch' – chọn công cụ dịch trước")
    p = project(pid)
    segs = p.segments
    if not segs:
        raise ApiError("Làm bước 1 (Nghe & tách câu) trước đã")
    if mode == "auto":
        done = sum(1 for x in segs if x.trans.strip())
        todo = sum(1 for x in segs if x.text.strip() and not x.trans.strip())
        mode = "all" if not done or not todo else "empty"
    if mode in ("empty", "all"):
        only = mode == "empty"
        if only and not any(x.text.strip() and not x.trans.strip() for x in segs):
            raise ApiError("Câu nào cũng đã có bản dịch")

        def fn(j, p):
            n = pipeline.translate(p, eng, sty, only, None, j.update, j.cancelled)
            w = " · ".join(x for x in (p.data.get("translate_warning"), p.data.get("skill_warning")) if x)
            note = p.data.get("skill_note") if not only or n else ""
            return {"message": f"Đã dịch {n} câu" + (f" · {note}" if note else "") + (f" – ⚠ {w}" if w else "")}
        return submit(pid, "translate", "Dịch phụ đề", fn)
    if mode != "rows":
        raise ApiError("Cách dịch không hợp lệ")
    rs = _check_rows(p, rows or [], texts)
    if not rs:
        raise ApiError("Tích chọn câu cần dịch trước")
    before = [x.trans for x in segs]

    def fn(j, p):
        pipeline.translate(p, eng, sty, False, None, lambda x, m="": j.update(0.6 * x, m), j.cancelled, rows=rs)
        new = p.segments
        changed = [k for k, x in enumerate(new) if k < len(before) and x.trans.strip() and x.trans != before[k]]
        msg = f"Đã dịch {len(changed)} câu"
        w = p.data.get("translate_warning")
        if changed and pipeline.has_dub(p):
            pipeline.redub(p, changed, st.get("tts_rate"), st, lambda x, m="": j.update(0.6 + 0.4 * x, m),
                           j.cancelled, st.get("tts_engine"))
            msg += " và đã tạo giọng các câu đó"
        return {"message": msg + (f" – ⚠ {w}" if w else ""), "pick": changed}
    return submit(pid, "redub" if pipeline.has_dub(p) else "translate", f"Dịch lại {len(rs)} câu", fn)


# ================================================================== lồng tiếng
SPEED_LABELS = {"ok": "Vừa", "fast": "Dài", "long": "Rất dài"}


def dub_info(pid: str) -> dict:
    p = project(pid)
    st = load_settings()
    segs = p.segments
    gen = p.data.get("speaker_gender") or {}
    voices = p.data.get("voices") or {}
    groups = pipeline.speed_groups(segs)
    d = p.edit.get("dub") or {}
    wav = d.get("wav") or ""
    return {
        "engine": st.get("tts_engine", "edge"), "voice_all": pipeline.voice_all(p, st),
        "speakers": [{"id": x["speaker"], "n": x["lines"], "gender": ((gen.get(x["speaker"]) or {}).get("g") or ""),
                      "voice": voices.get(x["speaker"]) or ""} for x in pipeline.speaker_list(p)] if segs else [],
        "cats": [{"id": c, "label": SPEED_LABELS.get(c, c), "n": len(groups.get(c) or [])} for c in ("ok", "fast", "long")],
        "dubbed": pipeline.has_dub(p), "summary": pipeline.dub_summary(p) if pipeline.has_dub(p) else "",
        "has_audio": bool(wav) and Path(wav).is_file(),
        "values": {k: st.get(k) for k in ("tts_rate", "dub_overflow", "tts_max_speedup", "dub_subs_follow",
                                          "dub_auto_shorten")},
    }


def set_voices(pid: str, voice_all: str | None, speakers: dict | None) -> dict:
    """Giọng cho tất cả ("" = theo từng người, "@auto" = tự gán nam / nữ) hoặc giọng từng người nói."""
    st = load_settings()

    def fn(p):
        if voice_all is not None:
            pipeline._auto_voices(p, {"voice": voice_all, "tts_engine": st.get("tts_engine", "edge")}, st)
        if speakers:
            v = dict(p.data.get("voices") or {})
            v.update({str(k): str(x) for k, x in speakers.items()})
            p.data["voices"] = v
            p.data["voice_all"] = ""
        p.save()
        return {"message": "Đã lưu giọng đọc – bấm Tạo bản lồng tiếng để đọc lại"}
    if voice_all is not None:
        save_settings({"dub_voice_all": voice_all})
    return edit(pid, "Đổi giọng", fn, timeout=15)


def preview_voice(pid: str, vid: str, spk: str = "") -> Path:
    """Nghe thử 1 giọng bằng câu dịch của người nói đó (như nút ▶ ở tab Lồng tiếng mục 2)."""
    from .. import tts
    from ..config import DATA_DIR

    p = project(pid)
    st = load_settings()
    eng = st.get("tts_engine", "edge")
    if vid == "@auto":
        vid = ""
    if not vid and eng == "omnivoice":
        from .. import voices as vlib

        smp = vlib.list_samples()
        vid = f"omni:{smp[0]['id']}" if smp else tts.OMNI_CLONE
    vid = vid or (f"edge:{st.get('tts_voice')}" if eng == "edge" else "vieneu:")
    segs = p.segments
    text = next((s.trans for s in segs if (s.speaker or "S1") == spk and s.trans), "") or \
        next((s.trans for s in segs if s.trans), "")
    if not vid.startswith("edge:"):
        busy = manager.gpu_busy({"dub", "redub"})
        if busy:
            raise ApiError(f"Đang chạy '{busy}' trên card đồ hoạ – nghe thử giọng AI sau khi việc đó xong "
                           "(giọng Edge nghe thử được ngay)", 409)
    ref = None
    if vid in (tts.CLONE, tts.OMNI_CLONE):
        stems = pipeline.stems(p)
        build = tts.build_reference if vid == tts.CLONE else tts.build_reference_text
        name = f"ref_{spk or 'S1'}.wav" if vid == tts.CLONE else f"omniref_{spk or 'S1'}.wav"
        ref = build(stems.get("vocals") or str(p.source), segs, spk or "S1", p.work / "tts" / name)
        if not ref:
            raise ApiError("Người nói này chưa có câu nào đủ rõ để lấy mẫu giọng")
    out = DATA_DIR / "remote" / "cache" / f"voice_{int(time.time() * 1000)}"
    out.parent.mkdir(parents=True, exist_ok=True)
    for old in sorted(out.parent.glob("voice_*"))[:-6]:
        old.unlink(missing_ok=True)
    return Path(tts.preview_voice(vid, text, out, st, ref, pipeline.target_lang(p, st)))


def cat_action(pid: str, cat: str, action: str, value: float | None = None) -> dict:
    """Loại câu Vừa / Dài / Rất dài -> AI rút gọn / đổi tốc độ / tạo lại giọng (tab Lồng tiếng mục 2 trên máy)."""
    st = dict(load_settings())
    p = project(pid)
    rows = pipeline.speed_groups(p.segments).get(cat) or []
    if not rows:
        raise ApiError("Không có câu nào thuộc loại này")
    texts = [p.segments[r].text for r in rows]
    if action == "redub":
        def fn(j, p):
            rs = _check_rows(p, rows, texts)
            pipeline.redub(p, rs, st.get("tts_rate"), st, j.update, j.cancelled, st.get("tts_engine"))
            return {"message": f"Đã tạo lại giọng {len(rs)} câu", "pick": rs}
        return submit(pid, "redub", f"Tạo lại giọng {len(rows)} câu", fn)
    if action == "shorten":
        def fn(j, p):
            rs = _check_rows(p, rows, texts)
            r = pipeline.shorten_fit(p, rs, st, st.get("tts_rate"), st.get("tts_engine"), j.update, j.cancelled)
            return {"message": pipeline.shorten_note(r, p.segments, pipeline.trans_lang(p)), "pick": r["pick"]}
        return submit(pid, "redub", f"AI rút gọn {len(rows)} câu", fn)
    if action == "speed":
        v = None if value is None else float(value)

        def fn(j, p):
            segs = p.segments
            rs = _check_rows(p, rows, texts)
            ch = pipeline.apply_speed(segs, rs, "exact", v)
            p.segments = segs
            p.save()
            if ch and pipeline.has_dub(p):
                pipeline.relayout(p, st, j.update, j.cancelled)
            return {"message": (f"Đặt tốc độ ×{v:.2f} cho {len(ch)} câu" if v else f"Về tốc độ gốc {len(ch)} câu")
                    + " · đã xếp lại bản lồng (không đọc lại)", "pick": ch}
        return submit(pid, "relayout", "Đổi tốc độ giọng", fn)
    raise ApiError("Thao tác không hợp lệ")


def dub_audio(pid: str) -> Path:
    p = project(pid)
    wav = (p.edit.get("dub") or {}).get("wav") or ""
    if not wav or not Path(wav).is_file():
        raise ApiError("Chưa có bản lồng tiếng", 404)
    return Path(wav)


def line_audio(pid: str, i: int) -> Path:
    p = project(pid)
    segs = p.segments
    if not 0 <= int(i) < len(segs):
        raise ApiError("Không có câu này", 404)
    f = segs[int(i)].extra.get("tts_file") or ""
    if not f or not Path(f).is_file():
        raise ApiError("Câu này chưa có giọng lồng", 404)
    return Path(f)


# ================================================================== xuất
def export_info(pid: str) -> dict:
    p = project(pid)
    st = load_settings()
    e = p.edit
    sub = e.get("subtitles") or {}
    outs = []
    for k, o in enumerate(p.data.get("outputs") or []):
        f = Path((o or {}).get("path") or "")
        if (o or {}).get("kind") not in ("video", "subtitle") or not f.is_file():
            continue
        outs.append({"idx": k, "name": f.name, "kind": o.get("kind"), "size": f.stat().st_size,
                     "time": o.get("time") or 0, "ratio": o.get("ratio") or ""})
    outs.sort(key=lambda o: -o["time"])
    sample = next(({"t": round((s.start + s.end) / 2, 2), "trans": s.trans, "text": s.text}
                   for s in p.segments if s.trans.strip()), None)
    which = sub.get("which") or "trans"
    return {
        "which": "none" if not sub.get("burn", True) else which,
        "hardsub": (e.get("hardsub") or {}).get("mode") or "none",
        "dub": bool((e.get("dub") or {}).get("enabled")),
        "aspect": e.get("aspect") or "original", "extra916": "9:16" in (e.get("extra_ratios") or []),
        "resolution": e.get("resolution") or "original", "crf": str(e.get("crf") or st.get("video_crf", 20)),
        "outputs": outs[:12], "sample": sample, "has_dub": pipeline.has_dub(p),
    }


def export_settings(pid: str, v: dict) -> dict:
    def fn(p):
        e = p.data.setdefault("edit", {})
        sub = dict(e.get("subtitles") or {})
        if "which" in v:
            w = str(v["which"])
            sub["burn"] = w != "none"
            if w != "none":
                sub["which"] = w
            e["subtitles"] = sub
        if "hardsub" in v:
            hs = dict(e.get("hardsub") or {})
            hs["mode"] = str(v["hardsub"])
            if hs["mode"] != "none" and not hs.get("regions"):
                r = pipeline.ocr_box(p) or p.data.get("ocr_region")
                if r:   # chưa khoanh vùng xoá ở tab Xoá phụ đề -> dùng vùng quét phụ đề
                    hs["regions"] = [{k: float(r[k]) for k in ("x", "y", "w", "h")}]
            e["hardsub"] = hs
        if "dub" in v:
            e["dub"] = {**(e.get("dub") or {}), "enabled": bool(v["dub"])}
        if "aspect" in v:
            e["aspect"] = str(v["aspect"])
        if "extra916" in v:
            ex = [r for r in (e.get("extra_ratios") or []) if r != "9:16"]
            if v["extra916"] and e.get("aspect") != "9:16":
                ex.append("9:16")
            e["extra_ratios"] = ex
        if "resolution" in v:
            e["resolution"] = str(v["resolution"])
        if "crf" in v:
            e["crf"] = int(v["crf"])
        p.save()
        return {"message": "Đã lưu cách xuất"}
    return edit(pid, "Cách xuất", fn)


def output_file(pid: str, idx: int) -> Path:
    p = project(pid)
    outs = p.data.get("outputs") or []
    if not 0 <= int(idx) < len(outs):
        raise ApiError("Không có file này", 404)
    f = Path((outs[int(idx)] or {}).get("path") or "")
    if not f.is_file():
        raise ApiError("File đã bị xoá / chuyển chỗ", 404)
    return f


def make_srt(pid: str) -> Path:
    p = project(pid)
    if not any(s.trans.strip() for s in p.segments):
        raise ApiError("Chưa có bản dịch")
    return pipeline.export_subs(p, "srt", "trans")


def video_file(pid: str) -> Path:
    """Video để xem trên điện thoại: bản xem trước nhẹ (nếu có) hơn video gốc."""
    p = project(pid)
    prev = p.work / "preview.mp4"
    return prev if prev.is_file() else _src(p)
