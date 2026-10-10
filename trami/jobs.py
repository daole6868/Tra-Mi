"""Hàng đợi tác vụ nền: chạy lần lượt, báo tiến độ, huỷ được."""
from __future__ import annotations

import threading
import time
import traceback
import uuid
from dataclasses import dataclass, field
from typing import Callable

from .ffmpeg import Cancelled


@dataclass
class Job:
    id: str
    kind: str
    title: str
    project_id: str = ""
    status: str = "queued"      # queued / running / done / error / cancelled
    progress: float = 0.0
    message: str = "Đang chờ…"
    result: dict = field(default_factory=dict)
    error: str = ""
    created: float = field(default_factory=time.time)
    started: float = 0.0
    finished: float = 0.0
    _cancel: threading.Event = field(default_factory=threading.Event, repr=False)
    _fn: Callable | None = field(default=None, repr=False)

    def to_dict(self) -> dict:
        eta = 0.0
        if self.status == "running" and self.progress > 0.02:
            el = time.time() - self.started
            eta = el / self.progress - el
        return {"id": self.id, "kind": self.kind, "title": self.title, "project_id": self.project_id,
                "status": self.status, "progress": round(self.progress, 4), "message": self.message,
                "result": self.result, "error": self.error, "created": self.created, "started": self.started,
                "finished": self.finished, "eta": round(eta)}

    # dùng trong hàm xử lý
    def update(self, p: float, msg: str = ""):
        self.progress = max(0.0, min(1.0, p))
        if msg:
            self.message = msg

    def cancelled(self) -> bool:
        return self._cancel.is_set()


class JobManager:
    def __init__(self, workers: int = 1):
        self.jobs: dict[str, Job] = {}
        self.queue: list[str] = []
        self.cv = threading.Condition()
        for _ in range(workers):
            threading.Thread(target=self._worker, daemon=True).start()

    def submit(self, kind: str, title: str, fn: Callable[[Job], dict | None], project_id: str = "") -> Job:
        job = Job(id=uuid.uuid4().hex[:10], kind=kind, title=title, project_id=project_id, _fn=fn)
        with self.cv:
            self.jobs[job.id] = job
            self.queue.append(job.id)
            self.cv.notify()
        return job

    def cancel(self, jid: str) -> bool:
        job = self.jobs.get(jid)
        if not job:
            return False
        job._cancel.set()
        with self.cv:
            if jid in self.queue:
                self.queue.remove(jid)
                job.status, job.message, job.finished = "cancelled", "Đã huỷ", time.time()
        return True

    def list(self, project_id: str | None = None) -> list[dict]:
        js = sorted(self.jobs.values(), key=lambda j: j.created, reverse=True)
        if project_id:
            js = [j for j in js if j.project_id == project_id]
        return [j.to_dict() for j in js[:100]]

    def get(self, jid: str) -> Job | None:
        return self.jobs.get(jid)

    def _worker(self):
        while True:
            with self.cv:
                while not self.queue:
                    self.cv.wait()
                jid = self.queue.pop(0)
            job = self.jobs[jid]
            if job._cancel.is_set():
                continue
            job.status, job.started, job.message = "running", time.time(), "Đang bắt đầu…"
            try:
                res = job._fn(job) if job._fn else None
                job.result = res or {}
                job.status, job.progress, job.message = "done", 1.0, job.result.get("message", "Hoàn tất")
            except Cancelled:
                job.status, job.message = "cancelled", "Đã huỷ"
            except Exception as e:
                from .config import err_text, log_error

                job.status, job.error = "error", err_text(e)
                job.message = "Lỗi: " + err_text(e)[:300]
                traceback.print_exc()
                log_error(job.title)
            finally:
                job.finished = time.time()
                try:   # thư viện AI vừa nạp có thể để lại module "lười" làm PySide6 sập -> dọn đi
                    from .diarize import drop_broken_lazy_modules

                    drop_broken_lazy_modules()
                except Exception:  # noqa: BLE001
                    pass


manager = JobManager()
