"""Quản lý dự án: mỗi video là 1 dự án lưu trong data/projects/<id>/project.json."""
from __future__ import annotations

import json
import re
import shutil
import threading
import time
import uuid
from pathlib import Path

from .config import PROJECTS_DIR
from .subtitle import Segment

_locks: dict[str, threading.RLock] = {}
_glock = threading.Lock()
# dự án đã xoá trong phiên này: không việc nền / hẹn giờ lưu nào được ghi lại project.json (trước đây việc nền đang chạy
# ghi lại -> dự án "xoá rồi mà tên vẫn còn")
_DELETED: set[str] = set()
TOMB = ".deleted"
_SUMMARY: dict[str, tuple[tuple[int, int], dict]] = {}   # list_all: tóm tắt dự án theo (giờ sửa, cỡ) project.json   # dấu đã xoá: Windows còn file bị giữ (trình phát, file nghe trực tiếp) -> lần mở tool sau dọn nốt

DEFAULT_EDIT = {
    "trim": {"start": 0, "end": 0},
    "cuts": [],
    "vsplits": [],          # điểm tách video / tiếng gốc trên timeline (giây video gốc) – chia thành từng đoạn
    "inserts": [],          # video chèn thêm: [{"at": giây video gốc, "path", "dur", "name"}] (chỉ khi xuất)
    "mute_spans": [],       # đoạn tắt TIẾNG GỐC (nguyên bản / nhạc nền / giọng gốc đã tách), giây video gốc
    "crop": None,
    "hardsub": {"mode": "none", "regions": [], "box_color": "#000000", "box_opacity": 1.0,
                "blur_strength": 20, "strength": "normal"},
    "flip_h": False, "flip_v": False, "rotate": 0, "zoom": 1.0, "speed": 1.0,
    "aspect": "original", "aspect_mode": "blur", "resolution": "original",
    "aspect_pos": [0.5, 0.5],   # vị trí phim trong khung khi đổi tỉ lệ (0 = sát trái / trên, 0,5 = giữa)
    "aspect_scale": 1.0,        # cỡ phim trong khung (1 = vừa khít; > 1 phóng to, phần tràn bị cắt)
    "aspect_color": "#000000",  # màu nền khi Phần trống = Nền 1 màu
    "extra_ratios": [],         # xuất thêm các tỉ lệ (mỗi tỉ lệ 1 file)
    "ratio_over": {},           # chỉnh riêng từng tỉ lệ xuất thêm: {tỉ lệ: {aspect_mode, aspect_pos, layers{}, sub{}}}
    "safe_zone": False,         # hiện vùng bị TikTok / Reels che trên khung xem trước
    "layers": [],               # lớp phủ chữ / logo (trami/overlay.py)
    "color": {"brightness": 0, "contrast": 1, "saturation": 1, "gamma": 1, "sharpen": False, "denoise": False},
    "volume": 1.0, "mute_original": False, "pitch_shift": 0,
    "dub": {"enabled": False, "wav": "", "original_volume": 0.15, "dub_volume": 1.0},
    "bgm": {"path": "", "volume": 0.2, "duck": True},
    "watermark": {"path": "", "position": "top-right", "scale": 0.15, "opacity": 0.85},
    "text": {"content": "", "position": "top-left", "size": 0, "color": "#FFFFFF"},
    "subtitles": {"burn": True, "which": "trans", "style": {}},
    "fade": {"in": 0, "out": 0},
}


def _lock(pid: str) -> threading.RLock:
    with _glock:
        return _locks.setdefault(pid, threading.RLock())


def safe_name(name: str) -> str:
    name = re.sub(r"[\\/:*?\"<>|\r\n]+", "_", name).strip(" .")
    return name[:120] or "video"


class Project:
    def __init__(self, pid: str):
        self.id = pid
        self.dir = PROJECTS_DIR / pid
        self.file = self.dir / "project.json"
        self.data: dict = {}
        if self.file.exists():
            self.data = json.loads(self.file.read_text("utf-8"))

    # ------------------------------------------------------------ tạo / liệt kê
    @classmethod
    def create(cls, name: str, source: str | None = None) -> "Project":
        pid = time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6]
        p = cls(pid)
        p.dir.mkdir(parents=True, exist_ok=True)
        (p.dir / "work").mkdir(exist_ok=True)
        (p.dir / "output").mkdir(exist_ok=True)
        p.data = {"id": pid, "name": name, "created": time.time(), "updated": time.time(),
                  "source": source or "", "info": {}, "segments": [], "edit": json.loads(json.dumps(DEFAULT_EDIT)),
                  "cache": {}, "glossary": "", "summary": "", "outputs": [], "sub_source": "", "status": {}}
        p.save()
        return p

    @classmethod
    def list_all(cls) -> list[dict]:
        """Tóm tắt mọi dự án cho Trang chủ / thanh bên. Chỉ đọc lại project.json nào vừa đổi (theo giờ sửa + cỡ file) –
        trước đây mỗi lần 1 việc bắt đầu / xong đọc lại cả chục file vài MB trên luồng giao diện."""
        out = []
        for d in sorted(PROJECTS_DIR.iterdir(), reverse=True):
            f = d / "project.json"
            if d.name in _DELETED or (d / TOMB).exists():
                continue
            try:
                st = f.stat()
            except OSError:
                continue
            sig = (st.st_mtime_ns, st.st_size)
            hit = _SUMMARY.get(d.name)
            if hit and hit[0] == sig:
                out.append(dict(hit[1]))
                continue
            try:
                j = json.loads(f.read_text("utf-8"))
                item = {"id": j["id"], "name": j.get("name"), "created": j.get("created"),
                        "updated": j.get("updated"), "info": j.get("info", {}),
                        "n_segments": len(j.get("segments", [])),
                        "n_translated": sum(1 for s in j.get("segments", []) if s.get("trans")),
                        "outputs": j.get("outputs", [])[-3:], "status": j.get("status", {})}
            except Exception:
                continue
            _SUMMARY[d.name] = (sig, item)
            out.append(dict(item))
        return out

    def delete(self) -> bool:
        """Xoá hẳn dự án. True = đã xoá hết thư mục; False = còn vài file đang bị giữ (để dấu .deleted, dự án không
        còn hiện ở danh sách, lần mở tool sau `purge_deleted` dọn nốt)."""
        _DELETED.add(self.id)
        with _lock(self.id):
            for f in (self.file, self.file.with_suffix(".tmp")):   # xoá project.json TRƯỚC: mất khỏi danh sách ngay
                try:
                    f.unlink(missing_ok=True)
                except OSError:
                    pass
        for i in range(4):
            shutil.rmtree(self.dir, ignore_errors=True)
            if not self.dir.exists():
                return True
            time.sleep(0.25 * (i + 1))   # file vừa được nhả (trình phát / luồng phát tiếng) – thử lại
        try:
            (self.dir / TOMB).write_text("1", "utf-8")
        except OSError:
            pass
        return False

    @staticmethod
    def purge_deleted() -> int:
        """Dọn nốt thư mục của các dự án đã xoá mà lần trước còn file bị giữ. Trả số thư mục đã dọn."""
        n = 0
        if not PROJECTS_DIR.exists():
            return 0
        for d in PROJECTS_DIR.iterdir():
            if d.is_dir() and (d / TOMB).exists():
                shutil.rmtree(d, ignore_errors=True)
                n += not d.exists()
        return n

    # ------------------------------------------------------------ lưu / đọc
    def save(self):
        if self.id in _DELETED or (self.dir / TOMB).exists() or not self.dir.is_dir():
            return   # dự án đã xoá: không tạo lại
        with _lock(self.id):
            self.data["updated"] = time.time()
            tmp = self.file.with_suffix(".tmp")
            # JSON gọn (bộ mã hoá C của Python, không thụt lề): phim 2500 câu 175 -> ~45 ms mỗi lần lưu (2.1.10)
            tmp.write_text(json.dumps(self.data, ensure_ascii=False, separators=(",", ":")), "utf-8")
            tmp.replace(self.file)

    def reload(self):
        with _lock(self.id):
            if self.file.exists():
                self.data = json.loads(self.file.read_text("utf-8"))
        return self

    @property
    def exists(self) -> bool:
        return self.file.exists()

    @property
    def source(self) -> Path:
        return Path(self.data.get("source", ""))

    @property
    def work(self) -> Path:
        d = self.dir / "work"
        d.mkdir(exist_ok=True)
        return d

    @property
    def output_dir(self) -> Path:
        d = self.dir / "output"
        d.mkdir(exist_ok=True)
        return d

    @property
    def segments(self) -> list[Segment]:
        return [Segment.from_dict(s) for s in self.data.get("segments", [])]

    @segments.setter
    def segments(self, segs: list[Segment]):
        self.data["segments"] = [s.to_dict() for s in segs]

    @property
    def edit(self) -> dict:
        from .config import _deep_merge

        # bản sao mặc định riêng mỗi lần đọc: sửa danh sách / dict trong bản chỉnh sửa không làm hỏng DEFAULT_EDIT
        return _deep_merge(json.loads(json.dumps(DEFAULT_EDIT)), self.data.get("edit") or {})

    def public(self) -> dict:
        d = dict(self.data)
        d.pop("cache", None)
        d["edit"] = self.edit
        return d

    def add_output(self, path: Path, kind: str, **extra):
        outs = self.data.setdefault("outputs", [])
        outs.append({"path": str(path), "name": path.name, "kind": kind, "time": time.time(), **extra})
        self.save()

    def set_status(self, step: str, value):
        self.data.setdefault("status", {})[step] = value
        self.save()
