"""Dung lượng dữ liệu (2.1.29): quét thư mục từng dự án theo nhóm file, dọn ĐỆM (tool tự tạo lại khi cần) / RÁC (không
còn dùng: giọng câu cũ, bản lồng cũ, file tạm…) và tự dọn (đệm nghe chỉ giữ cho các dự án mở gần đây, file tạm cũ khi mở
tool). Chỉ xoá file khớp mẫu đã biết – file lạ trong thư mục dự án luôn được giữ; video xuất / video gốc / bản tách giọng /
giọng đang dùng / bản lồng đang dùng không bao giờ bị xoá ở đây."""
from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path

from .config import DATA_DIR, MODELS_DIR, PROJECTS_DIR

CACHE, JUNK, KEEP = "cache", "junk", "keep"
# mã nhóm -> (tên hiện trên giao diện, loại)
GROUPS = {
    "live": ("Đệm nghe trực tiếp", CACHE),
    "wav16": ("Âm thanh 16 kHz cho bộ nghe", CACHE),
    "preview": ("Bản xem trước nhẹ", CACHE),
    "chunks": ("Đệm xuất lại nhanh", CACHE),
    "tts_cache": ("Đệm giọng từng câu", CACHE),
    "misc": ("Đệm khác (sóng âm, ảnh timeline, khúc gửi Gemini…)", CACHE),
    "tmp": ("File tạm", JUNK),
    "tts_old": ("Giọng câu cũ không câu nào dùng", JUNK),
    "dub_old": ("Bản lồng cũ (không dùng, chưa ghim)", JUNK),
    "inpaint_old": ("Bản xoá phụ đề không còn dùng", JUNK),
    "stems": ("Bản tách giọng / nhạc nền", KEEP),
    "tts": ("Giọng từng câu đang dùng", KEEP),
    "dub": ("Bản lồng đang dùng / đã ghim", KEEP),
    "inpaint": ("Bản đã xoá phụ đề cũ", KEEP),
    "output": ("Video xuất", KEEP),
    "source": ("Video gốc (tải về)", KEEP),
    "other": ("Phụ đề, cài đặt dự án, khác", KEEP),
}
# thanh màu trên trang Dung lượng: gộp nhóm cho dễ nhìn
BARS = (("cache", "Đệm"), ("junk", "Rác"), ("stems", "Bản tách"), ("voice", "Giọng lồng"),
        ("inpaint", "Bản xoá phụ đề"), ("output", "Video xuất"), ("source", "Video gốc"), ("other", "Khác"))
PROTECT_OPEN = ("live", "preview")   # dự án đang mở: trình phát đang đọc các file này
TMP_RE = re.compile(r"\.(tmp|part|incomplete)$|\.part\.|\.tmp\.|_tmp\.mp4$|_new\.wav$|_patch\.wav$")
TMP_MIN_AGE = 600        # dọn tay: file tạm mới hơn 10 phút có thể đang được ghi -> để nguyên
SWEEP_AGE = 86400        # mở tool: dọn file tạm cũ hơn 1 ngày
RECENT_FILE = DATA_DIR / "recent_projects.json"
_TTS_RE = re.compile(r"^(tts_[0-9a-f]+(?:_v)?\.(?:wav|mp3))")


def bar_of(group: str) -> str:
    kind = GROUPS[group][1]
    if kind == CACHE:
        return "cache"
    if kind == JUNK:
        return "junk"
    return "voice" if group in ("tts", "dub") else group


def fmt_size(n: float) -> str:
    """1536 -> "2 KB", 3.4e9 -> "3,4 GB" (dấu phẩy thập phân)."""
    n = float(n)
    if n >= 1e9:
        return f"{n / 1e9:.1f} GB".replace(".", ",")
    if n >= 1e6:
        return f"{n / 1e6:.0f} MB"
    if n >= 1e3:
        return f"{n / 1e3:.0f} KB"
    return f"{int(n)} B"


# ------------------------------------------------------------------ đọc dự án

class _Ctx:
    """Những gì cần biết từ project.json để phân loại file: giọng câu đang dùng, bản lồng giữ, bản xoá phụ đề đang dùng."""

    def __init__(self, pdir: Path):
        self.name = pdir.name
        self.updated = 0.0
        self.tts_ref: set[str] = set()
        self.dub_keep: set[str] = set()
        self.dub_known: set[str] = set()
        self.inpaint_cur: str | None = None
        self.inpaint_unknown = True     # không xác định được bản đang dùng -> giữ mọi bản
        self.has_output = False
        self.ok = False
        try:
            data = json.loads((pdir / "project.json").read_text("utf-8"))
        except (OSError, ValueError):
            return
        self.ok = True
        self.name = data.get("name") or pdir.name
        self.updated = float(data.get("updated") or 0)
        self.has_output = bool(data.get("outputs"))
        for s in data.get("segments") or []:
            ex = s.get("extra") or {}
            for k in ("tts_file", "_tts"):
                if ex.get(k):
                    self.tts_ref.add(Path(str(ex[k])).name)
        cur = ((data.get("edit") or {}).get("dub") or {}).get("wav") or ""
        if cur:
            self.dub_keep.add(Path(cur).name)
            self.dub_known.add(Path(cur).name)
        for v in data.get("dub_versions") or []:
            nm = Path(str(v.get("wav") or "")).name
            if not nm:
                continue
            self.dub_known.add(nm)
            if v.get("pinned"):
                self.dub_keep.add(nm)
            for e in v.get("placed") or []:   # bảng đoạn giọng của mọi bản còn giữ: cần để vá bản đó sau này
                if e:
                    self.tts_ref.add(str(e[0]))
        self._inpaint(pdir)

    def _inpaint(self, pdir: Path):
        """Tên file bản xoá phụ đề khớp vùng / cách xoá hiện tại (video.inpaint_cached chỉ giữ 1 bản)."""
        try:
            from .config import load_settings
            from .project import Project
            from .video import inpainted_file, needs_clean
            from . import pipeline

            p = Project(pdir.name)
            hs = pipeline.hardsub_spec(p)
            if not needs_clean(hs) or not p.data.get("source"):
                self.inpaint_cur = None
            else:
                self.inpaint_cur = inpainted_file(p.source, hs, load_settings(), p.work / "render").name
            self.inpaint_unknown = False
        except Exception:  # noqa: BLE001 – không đoán được -> giữ mọi bản
            self.inpaint_unknown = True


def _dub_base(name: str) -> str:
    """dub_x.wav / dub_x.env10.npy / dub_x.env10s_<mã>.npy / dub_x.env.npy -> dub_x.wav"""
    if name.endswith(".wav"):
        return name
    i = name.find(".env")
    return (name[:i] if i > 0 else Path(name).stem) + ".wav"


def classify(rel: tuple[str, ...], ctx: _Ctx) -> str:
    """Nhóm của 1 file theo đường dẫn tương đối trong thư mục dự án."""
    top, name = rel[0], rel[-1]
    if len(rel) == 1:
        return "other"                       # project.json, thumb.jpg, posts.json…
    if top == "source":
        return "source"
    if top == "output":
        return "output"
    if top != "work":
        return "other"
    sub = rel[1:]
    if TMP_RE.search(name):
        return "tmp"
    if len(sub) == 1:                        # thẳng trong work/
        if name in ("audio16k.wav", "asr16k_vocals.wav"):
            return "wav16"
        if name == "preview.mp4":
            return "preview"
        if name.startswith(("frame_", "hs_preview_", "voice_preview_", "dub_preview_")) or \
                name in ("live_tts.wav", "live_draft.json"):
            return "tmp"
        if name.startswith("tl_thumbs_") or name.endswith(".npy"):
            return "misc"
        return "other"                       # ocr_boxes.json, asr_progress_*.json…
    d = sub[0]
    if d == "live":
        return "live"
    if d in ("gemvideo", "layers"):
        return "misc"
    if d == "stems":
        return "tmp" if (len(sub) > 2 and sub[1] == "_sep") or name == "mix44k.wav" else "stems"
    if d == "render":
        if len(sub) > 2 and sub[1].startswith("chunks"):
            return "chunks"
        if len(sub) > 2 and sub[1] in ("warp", "fonts", "layers"):
            return "misc"
        if name.startswith("inpainted_") and name.endswith(".mp4"):
            return "inpaint" if ctx.inpaint_unknown or name == ctx.inpaint_cur else "inpaint_old"
        if len(sub) == 2 and name.endswith((".ass", ".txt", ".png", ".json")):
            return "misc"                    # burn.ass, last_ffmpeg_cmd.txt…
        return "other"                       # logo_*.npz (tính lâu) …
    if d == "dubs" and len(sub) == 2:
        base = _dub_base(name)
        if not ctx.ok:
            return "dub"
        return "dub" if base in ctx.dub_keep else "dub_old"
    if d == "tts" and len(sub) == 2:
        if name.startswith(("ref_", "omniref_")):
            return "tts_cache"
        m = _TTS_RE.match(name)
        if not m:
            return "other"                   # _clipmeta.json …
        base = m.group(1)
        # vế đọc riêng của Edge (.mp3) nhỏ và không biết thuộc câu ghép nào -> luôn giữ (xoá thì lần lồng sau đọc lại);
        # dự án rất cũ không ghi tên file giọng từng câu (trước 2.0.26) -> không biết câu nào dùng file nào -> giữ hết
        used = base.endswith(".mp3") or not ctx.ok or not ctx.tts_ref or base in ctx.tts_ref
        if name.endswith(".npy"):
            return "tts_cache" if used else "tts_old"
        return "tts" if used else "tts_old"
    return "other"


def scan_project(pdir: str | Path) -> dict:
    """{"id", "name", "dir", "total", "groups": {nhóm: byte}, "files": {nhóm: [(đường dẫn, byte, giờ sửa)]}, …}"""
    pdir = Path(pdir)
    ctx = _Ctx(pdir)
    groups: dict[str, int] = {g: 0 for g in GROUPS}
    files: dict[str, list] = {g: [] for g in GROUPS}
    for root, dirs, names in os.walk(pdir):
        rp = Path(root).relative_to(pdir).parts
        for nm in names:
            fp = Path(root) / nm
            try:
                st = fp.lstat()
            except OSError:
                continue
            g = classify(rp + (nm,), ctx)
            groups[g] += st.st_size
            files[g].append((fp, st.st_size, st.st_mtime))
    bars = {k: 0 for k, _ in BARS}
    for g, n in groups.items():
        bars[bar_of(g)] += n
    return {"id": pdir.name, "name": ctx.name, "dir": pdir, "updated": ctx.updated, "has_output": ctx.has_output,
            "total": sum(groups.values()), "groups": groups, "files": files, "bars": bars,
            "cache": sum(n for g, n in groups.items() if GROUPS[g][1] == CACHE),
            "junk": sum(n for g, n in groups.items() if GROUPS[g][1] == JUNK)}


def project_dirs() -> list[Path]:
    if not PROJECTS_DIR.exists():
        return []
    return [d for d in PROJECTS_DIR.iterdir() if d.is_dir() and (d / "project.json").exists()
            and not (d / ".deleted").exists()]


def dir_size(path: str | Path) -> int:
    """Tổng cỡ file trong thư mục (không theo liên kết – kho HuggingFace trên Linux / macOS dùng liên kết tới blobs)."""
    total = 0
    for root, _dirs, names in os.walk(path):
        for nm in names:
            try:
                total += os.lstat(os.path.join(root, nm)).st_size
            except OSError:
                pass
    return total


def model_dirs() -> list[tuple[str, Path]]:
    """Các nơi cất mô hình: data/models + kho HuggingFace / ModelScope nằm ngoài thư mục data."""
    out = [("Thư mục mô hình của tool", MODELS_DIR)]
    hf = os.environ.get("HF_HUB_CACHE") or (os.path.join(os.environ["HF_HOME"], "hub") if os.environ.get("HF_HOME")
                                           else os.path.join(Path.home(), ".cache", "huggingface", "hub"))
    ms = os.path.join(Path.home(), ".cache", "modelscope")
    for label, p in (("Kho HuggingFace", Path(hf)), ("Kho ModelScope cũ", Path(ms))):
        try:
            inside = p.resolve().is_relative_to(MODELS_DIR.resolve())
        except OSError:
            inside = False
        if p.is_dir() and not inside:
            out.append((label, p))
    return out


def scan_global() -> dict:
    """Dung lượng ngoài các dự án: mô hình + phần còn lại của thư mục data."""
    models = [(lb, p, dir_size(p)) for lb, p in model_dirs()]
    other = 0
    if DATA_DIR.exists():
        for d in DATA_DIR.iterdir():
            if d.name in ("projects", "models"):
                continue
            try:
                other += dir_size(d) if d.is_dir() else d.stat().st_size
            except OSError:
                pass
    return {"models": models, "models_total": sum(x[2] for x in models), "other": other}


# ------------------------------------------------------------------ dọn

def plan(scan: dict, kinds=(CACHE,), protect=(), now: float | None = None) -> dict:
    """Những gì sẽ xoá (để hỏi lại trước khi xoá): {"groups": {nhóm: byte}, "bytes", "files": [đường dẫn]}."""
    now = time.time() if now is None else now
    out: dict[str, int] = {}
    todo = []
    for g, (_lb, kind) in GROUPS.items():
        if kind not in kinds or g in protect:
            continue
        for fp, n, mt in scan["files"][g]:
            if g == "tmp" and now - mt < TMP_MIN_AGE:
                continue
            out[g] = out.get(g, 0) + n
            todo.append(fp)
    return {"groups": {g: n for g, n in out.items() if n}, "bytes": sum(out.values()), "files": todo}


def clean(scan: dict, kinds=(CACHE,), protect=(), now: float | None = None) -> dict:
    """Xoá theo plan. File đang bị chương trình khác mở (Windows) -> bỏ qua, đếm vào "failed"."""
    pl = plan(scan, kinds, protect, now)
    freed = failed = 0
    gone: set[str] = set()
    for fp in pl["files"]:
        try:
            n = fp.lstat().st_size
            fp.unlink()
            freed += n
            gone.add(fp.name)
        except FileNotFoundError:
            pass
        except OSError:
            failed += 1
    pdir = Path(scan["dir"])
    _drop_empty(pdir / "work")
    if gone:
        _prune_clipmeta(pdir / "work" / "tts", gone)
    return {"bytes": freed, "planned": pl["bytes"], "failed": failed, "groups": pl["groups"]}


def _drop_empty(root: Path) -> None:
    """Bỏ các thư mục con rỗng sau khi dọn (kho đoạn xuất, khúc Gemini, thư mục tách tạm…)."""
    keep = {"live", "tts", "dubs", "render", "stems"}
    for d in sorted((x for x in root.rglob("*") if x.is_dir()), key=lambda x: len(x.parts), reverse=True):
        if d.parent == root and d.name in keep:
            continue
        try:
            d.rmdir()
        except OSError:
            pass


def _prune_clipmeta(tts_dir: Path, gone: set[str]) -> None:
    """Bỏ thông số của file giọng đã xoá khỏi work/tts/_clipmeta.json."""
    f = tts_dir / "_clipmeta.json"
    try:
        d = json.loads(f.read_text("utf-8"))
    except (OSError, ValueError):
        return
    keep = {k: v for k, v in d.items() if k not in gone and (tts_dir / k).exists()}
    if len(keep) == len(d):
        return
    tmp = f.with_suffix(".tmp")
    try:
        tmp.write_text(json.dumps(keep, separators=(",", ":")), "utf-8")
        tmp.replace(f)
    except OSError:
        pass


# ------------------------------------------------------------------ tự dọn

def note_opened(pid: str) -> list[str]:
    """Ghi dự án vừa mở lên đầu danh sách mở gần đây (data/recent_projects.json)."""
    ids = [pid] + [x for x in recent() if x != pid]
    ids = ids[:50]
    try:
        tmp = RECENT_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(ids), "utf-8")
        tmp.replace(RECENT_FILE)
    except OSError:
        pass
    return ids


def recent() -> list[str]:
    try:
        v = json.loads(RECENT_FILE.read_text("utf-8"))
        return [str(x) for x in v] if isinstance(v, list) else []
    except (OSError, ValueError):
        return []


def trim_live(keep: int = 2, busy=(), open_id: str | None = None) -> int:
    """Đệm nghe trực tiếp (work/live – phim 2 giờ ~4–5 GB) chỉ giữ cho `keep` dự án mở gần nhất (+ dự án đang mở + dự
    án đang chạy việc). Trả số byte đã xoá."""
    keep_ids = set(recent()[:max(1, int(keep))]) | set(busy) | ({open_id} if open_id else set())
    freed = 0
    for d in project_dirs():
        if d.name in keep_ids:
            continue
        live = d / "work" / "live"
        if not live.is_dir():
            continue
        for f in live.iterdir():
            try:
                n = f.lstat().st_size
                f.unlink()
                freed += n
            except OSError:
                pass   # file đang được mở (dự án vừa đóng, trình phát chưa nhả) -> lần sau
        try:
            live.rmdir()
        except OSError:
            pass
    return freed


def sweep(max_age: float = SWEEP_AGE, busy=()) -> int:
    """Khi mở tool: xoá file tạm cũ hơn max_age (lần chạy bị ngắt, ảnh chụp khung, file tải dở…) ở mọi dự án."""
    now = time.time()
    freed = 0
    for d in project_dirs():
        if d.name in busy:
            continue
        sc = scan_project(d)
        for fp, n, mt in sc["files"]["tmp"]:
            if now - mt < max_age:
                continue
            try:
                fp.unlink()
                freed += n
            except OSError:
                pass
        _drop_empty(d / "work")
    return freed


def auto_clean_on_open(pid: str, settings: dict, busy=()) -> int:
    """Gọi (ở luồng nền) khi mở 1 dự án: ghi dự án mở gần đây + xoá đệm nghe của các dự án mở lâu rồi."""
    note_opened(pid)
    if not settings.get("auto_clean", True):
        return 0
    return trim_live(int(settings.get("live_keep_projects", 2) or 2), busy, pid)

