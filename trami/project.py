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
        out = []
        for d in sorted(PROJECTS_DIR.iterdir(), reverse=True):
            f = d / "project.json"
            if f.exists():
                try:
                    j = json.loads(f.read_text("utf-8"))
                    out.append({"id": j["id"], "name": j.get("name"), "created": j.get("created"),
                                "updated": j.get("updated"), "info": j.get("info", {}),
                                "n_segments": len(j.get("segments", [])),
                                "n_translated": sum(1 for s in j.get("segments", []) if s.get("trans")),
                                "outputs": j.get("outputs", [])[-3:], "status": j.get("status", {})})
                except Exception:
                    continue
        return out

    def delete(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    # ------------------------------------------------------------ lưu / đọc
    def save(self):
        with _lock(self.id):
            self.data["updated"] = time.time()
            tmp = self.file.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.data, ensure_ascii=False, indent=1), "utf-8")
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

        return _deep_merge(DEFAULT_EDIT, self.data.get("edit") or {})

    def public(self) -> dict:
        d = dict(self.data)
        d.pop("cache", None)
        d["edit"] = self.edit
        return d

    def add_output(self, path: Path, kind: str):
        outs = self.data.setdefault("outputs", [])
        outs.append({"path": str(path), "name": path.name, "kind": kind, "time": time.time()})
        self.save()

    def set_status(self, step: str, value):
        self.data.setdefault("status", {})[step] = value
        self.save()
