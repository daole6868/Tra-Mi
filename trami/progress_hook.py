"""Lấy tiến độ thật từ các thư viện AI (tách giọng, SenseVoice, tải mô hình…) để hiện lên thanh tiến độ của tool.

Các thư viện này báo tiến độ bằng thanh `tqdm` trong cửa sổ đen. Trong khối `with tqdm_progress(...)`, mọi thanh tqdm
được chuyển thành lời gọi `progress(x, thông báo)` của tool; bấm Huỷ thì dừng luôn ở lần cập nhật kế tiếp.
"""
from __future__ import annotations

import threading
import time
from contextlib import contextmanager
from typing import Callable

_lock = threading.Lock()
_active: dict | None = None   # mỗi lúc chỉ 1 tác vụ dài chạy (JobManager 1 luồng)


def _fmt_bytes(n: float) -> str:
    return f"{n / 1e9:.2f} GB" if n >= 1e9 else f"{n / 1e6:.0f} MB"


def _report(bar) -> None:
    ctx = _active
    if not ctx:
        return
    if ctx["cancel"] and ctx["cancel"]():
        from .ffmpeg import Cancelled

        raise Cancelled()
    total = getattr(bar, "total", None) or 0
    n = getattr(bar, "n", 0) or 0
    now = time.time()
    if total <= 0 or (now - ctx["last"] < 0.4 and n < total):
        return
    ctx["last"] = now
    frac = max(0.0, min(1.0, n / total))
    unit = str(getattr(bar, "unit", "") or "")
    desc = str(getattr(bar, "desc", "") or "").strip().rstrip(":")
    if unit.lower() in ("b", "ib", "byte", "bytes") or (getattr(bar, "unit_scale", False) and total > 5e6):
        if total < 2e6:
            return   # file cấu hình nhỏ, không cần báo
        sp = ctx["speed"].setdefault(id(bar), [now, n, 0.0])
        if now - sp[0] >= 1.0:   # tốc độ trung bình trượt theo từng giây
            cur = (n - sp[1]) / (now - sp[0])
            sp[2] = cur if sp[2] <= 0 else sp[2] * 0.6 + cur * 0.4
            sp[0], sp[1] = now, n
        speed = f" · {sp[2] / 1e6:.1f} MB/s" if sp[2] > 0 else ""
        detail = f"{int(frac * 100)}% · {_fmt_bytes(n)} / {_fmt_bytes(total)}{speed}"
        label = ctx["download"] or ctx["label"]
        if desc and len(desc) < 50:
            label += f" ({desc})"
    else:
        detail = f"{int(n)}/{int(total)}"
        label = ctx["label"]
    lo, hi = ctx["lo"], ctx["hi"]
    try:
        ctx["progress"](lo + (hi - lo) * frac, f"{label} {detail}")
    except Exception:  # noqa: BLE001 – báo tiến độ lỗi không được làm hỏng việc chính
        pass


def _install() -> None:
    try:
        import tqdm.std as std
    except ImportError:
        return
    cls = std.tqdm
    if getattr(cls, "_trami_patched", False):
        return
    orig_update, orig_refresh = cls.update, cls.refresh

    def update(self, n=1):
        r = orig_update(self, n)
        _report(self)
        return r

    def refresh(self, *a, **k):
        r = orig_refresh(self, *a, **k)
        _report(self)
        return r

    cls.update, cls.refresh = update, refresh
    cls._trami_patched = True


@contextmanager
def tqdm_progress(progress: Callable[[float, str], None] | None, lo: float = 0.0, hi: float = 1.0,
                  label: str = "Đang xử lý", cancel: Callable[[], bool] | None = None, download: str = ""):
    """Trong khối này, thanh tqdm của thư viện -> progress(lo..hi, 'label n/total')."""
    global _active
    if progress is None and cancel is None:
        yield
        return
    _install()
    ctx = {"progress": progress or (lambda *_: None), "lo": lo, "hi": hi, "label": label, "cancel": cancel,
           "last": 0.0, "download": download or "Đang tải mô hình", "speed": {}}
    with _lock:
        prev, _active = _active, ctx
    try:
        yield
    finally:
        with _lock:
            _active = prev
