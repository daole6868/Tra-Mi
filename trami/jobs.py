"""Hàng đợi tác vụ nền: chạy NHIỀU việc cùng lúc, báo tiến độ, huỷ được.

Quy tắc chia luồng (chạy được ngay thì chạy, không phải chờ hết việc trước):
- tối đa MAX_RUNNING việc cùng lúc;
- 2 việc của CÙNG 1 dự án không chạy cùng lúc (cùng ghi 1 file dự án -> mất dữ liệu); khác dự án thì chạy song song;
- 2 việc nặng GPU (nghe, tách giọng, đọc giọng AI, xoá phụ đề AI…) không chạy cùng lúc (tránh hết bộ nhớ card đồ hoạ);
  việc dùng mạng / CPU (tải video, dịch bằng API, xuất video, tải mô hình) chạy song song với việc GPU.
Trong 1 việc "Dịch tự động" chế độ song song, các bước tách / dịch / đọc giọng tự chạy chồng lên nhau (pipeline.auto)."""
from __future__ import annotations

import threading
import time
import traceback
import uuid
from dataclasses import dataclass, field
from typing import Callable

from . import net
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
    gpu: bool = False
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
                "finished": self.finished, "eta": round(eta), "gpu": self.gpu}

    # dùng trong hàm xử lý
    def update(self, p: float, msg: str = ""):
        self.progress = max(0.0, min(1.0, p))
        if msg:
            self.message = msg

    def cancelled(self) -> bool:
        return self._cancel.is_set()


MAX_RUNNING = 4
# việc nặng GPU (mỗi lúc 1 việc); việc khác (mạng / CPU) chạy song song
GPU_KINDS = {"auto", "recognize", "separate", "diarize", "inpaint", "dub", "redub", "realign"}
# việc chỉ ĐỌC dự án (không ghi project.json) -> chạy được song song với việc khác của cùng dự án
READONLY_KINDS = {"preview", "tool"}


def needs_gpu(kind: str, project_id: str = "") -> bool:
    if kind == "translate":   # dịch trên máy (Hy-MT2) mới dùng GPU; dịch bằng API / Google thì không
        try:
            from .config import load_settings

            return load_settings().get("translate_engine") == "hymt"
        except Exception:  # noqa: BLE001
            return False
    if kind == "render" and project_id:
        # xuất video mà chưa có bản đã xoá phụ đề / gỡ logo -> bước xoá (LaMa, nền khung khác) chạy trên card đồ hoạ
        try:
            from . import pipeline
            from .project import Project
            from .video import needs_clean

            p = Project(project_id)
            return bool(p.data.get("source")) and needs_clean(pipeline.hardsub_spec(p)) and \
                pipeline.inpainted_video(p) is None
        except Exception:  # noqa: BLE001
            return True
    return kind in GPU_KINDS


class JobManager:
    def __init__(self, workers: int = MAX_RUNNING):
        self.jobs: dict[str, Job] = {}
        self.queue: list[str] = []
        self.running: set[str] = set()
        self.cv = threading.Condition()
        for i in range(workers):
            threading.Thread(target=self._worker, daemon=True, name=f"trami-job-{i}").start()
        from . import gpu

        gpu.start_reaper(self.gpu_busy)   # tiến trình đọc giọng để không lâu -> tắt, trả bộ nhớ card

    def gpu_busy(self, allow: set[str] | None = None) -> str:
        """Tên việc GPU đang chạy ("" = không có). allow: các loại việc không tính (dùng chung tiến trình)."""
        with self.cv:   # luồng hàng đợi đang thêm / bớt việc -> đọc trong khoá
            for jid in list(self.running):
                j = self.jobs.get(jid)
                if j and j.gpu and (not allow or j.kind not in allow):
                    return j.title
        return ""

    def submit(self, kind: str, title: str, fn: Callable[[Job], dict | None], project_id: str = "",
               gpu: bool | None = None) -> Job:
        job = Job(id=uuid.uuid4().hex[:10], kind=kind, title=title, project_id=project_id, _fn=fn,
                  gpu=needs_gpu(kind, project_id) if gpu is None else bool(gpu))
        with self.cv:
            self.jobs[job.id] = job
            self.queue.append(job.id)
            self.cv.notify_all()
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
        with self.cv:
            js = sorted(self.jobs.values(), key=lambda j: j.created, reverse=True)
        if project_id:
            js = [j for j in js if j.project_id == project_id]
        return [j.to_dict() for j in js[:100]]

    def get(self, jid: str) -> Job | None:
        return self.jobs.get(jid)

    def clear_finished(self) -> int:
        """Xoá các việc đã xong / lỗi / huỷ khỏi danh sách."""
        with self.cv:
            gone = [k for k, j in self.jobs.items() if j.status in ("done", "error", "cancelled")]
            for k in gone:
                del self.jobs[k]
        return len(gone)

    def counts(self) -> tuple[int, int]:
        """(đang chạy, đang chờ)."""
        with self.cv:
            st = [j.status for j in self.jobs.values()]
        return st.count("running"), st.count("queued")

    def _why_wait(self, job: Job) -> str:
        """Vì sao việc này chưa chạy được ('' = chạy được ngay)."""
        run = [self.jobs[r] for r in self.running]
        if len(run) >= MAX_RUNNING:
            return f"chờ – đang chạy {len(run)} việc"
        if job.project_id and job.kind not in READONLY_KINDS and any(
                r.project_id == job.project_id and r.kind not in READONLY_KINDS for r in run):
            return "chờ việc khác của dự án này xong"
        if job.gpu and any(r.gpu for r in run):
            return "chờ việc dùng card đồ hoạ (GPU) khác xong"
        return ""

    def _next(self) -> Job | None:
        for jid in self.queue:
            job = self.jobs[jid]
            why = self._why_wait(job)
            if not why:
                return job
            job.message = "Đang chờ: " + why
        return None

    def _worker(self):
        while True:
            with self.cv:
                job = self._next()
                while job is None:
                    self.cv.wait(1.0)
                    job = self._next()
                self.queue.remove(job.id)
                self.running.add(job.id)
            try:
                if not job._cancel.is_set():
                    self._run(job)
            finally:
                with self.cv:
                    self.running.discard(job.id)
                    self.cv.notify_all()

    def _run(self, job: Job):
        job.status, job.started, job.message = "running", time.time(), "Đang bắt đầu…"
        if job.gpu:   # tắt tiến trình AI mà việc này không dùng -> đủ chỗ trên card, không tràn sang RAM (rất chậm)
            try:
                from . import gpu

                gpu.before_job(job.kind)
            except Exception:  # noqa: BLE001
                pass
        try:
            # mọi lần gọi mạng trong tác vụ biết nút Huỷ + báo "mất mạng – đang chờ" vào dòng trạng thái
            with net.task(job.cancelled, lambda m, j=job: j.update(j.progress, m)):
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
            if job.gpu:   # bỏ mô hình nạp trong tiến trình giao diện + trả bộ nhớ đệm của card
                try:
                    from . import gpu

                    gpu.after_job(job.kind)
                except Exception:  # noqa: BLE001
                    pass
            try:   # thư viện AI vừa nạp có thể để lại module "lười" làm PySide6 sập -> dọn đi
                from .diarize import drop_broken_lazy_modules

                drop_broken_lazy_modules()
            except Exception:  # noqa: BLE001
                pass


manager = JobManager()
