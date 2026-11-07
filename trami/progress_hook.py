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
# nhiều tác vụ chạy cùng lúc (JobManager nhiều luồng) -> mỗi luồng 1 ngữ cảnh; thanh tqdm chạy ở luồng phụ của thư
# viện (tải song song) -> dùng ngữ cảnh mới nhất
_by_thread: dict[int, dict] = {}
_order: list[dict] = []


def _ctx_now() -> dict | None:
    c = _by_thread.get(threading.get_ident())
    if c is not None:
        return c
    return _order[-1] if _order else None


def _fmt_bytes(n: float) -> str:
    return f"{n / 1e9:.2f} GB" if n >= 1e9 else f"{n / 1e6:.0f} MB"


# Tải mô hình đang chạy (mọi thanh tải theo byte): tổng byte đã nhận + lúc nhận gần nhất -> canh tải đứng im
_dl = {"n": {}, "tot": {}, "t": 0.0}


def download_state() -> dict:
    """{bars: số thanh tải, bytes: đã nhận, total: tổng, t: lúc byte tăng lần cuối} (hfoffline canh tải đứng im)."""
    with _lock:
        return {"bars": len(_dl["n"]), "bytes": sum(_dl["n"].values()), "total": sum(_dl["tot"].values()),
                "t": _dl["t"]}


def _attr(bar, name: str, default=None):
    """Thanh tqdm bị tắt (disable – vd thư viện HuggingFace khi không có cửa sổ đen) không giữ unit / desc / n ->
    lấy bản tool đã nhớ lúc tạo thanh (`_sv_<tên>`)."""
    v = getattr(bar, name, None)
    return v if v not in (None, "") else getattr(bar, "_sv_" + name, default)


def _is_bytes(bar, total: float) -> bool:
    unit = str(_attr(bar, "unit", "") or "")
    return unit.lower() in ("b", "ib", "byte", "bytes") or (bool(_attr(bar, "unit_scale", False)) and total > 5e6)


def _report(bar) -> None:
    ctx = _ctx_now()
    if not ctx:
        return
    if ctx["cancel"] and ctx["cancel"]():
        from .ffmpeg import Cancelled

        raise Cancelled()
    total = getattr(bar, "total", None) or 0
    n = getattr(bar, "n", 0) or 0
    now = time.time()
    is_bytes = total > 0 and _is_bytes(bar, total)
    if is_bytes and total >= 2e6:
        with _lock:
            k = id(bar)
            if not _dl["n"]:
                _dl["t"] = now          # thanh tải đầu tiên: bắt đầu tính giờ đứng im
            if n > _dl["n"].get(k, -1) or total != _dl["tot"].get(k):
                if n > _dl["n"].get(k, 0):
                    _dl["t"] = now
                _dl["n"][k], _dl["tot"][k] = n, total
    if total <= 0 or (now - ctx["last"] < 0.4 and n < total):
        return
    ctx["last"] = now
    frac = max(0.0, min(1.0, n / total))
    desc = str(_attr(bar, "desc", "") or "").strip().rstrip(":")
    if is_bytes:
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
    orig_init, orig_update, orig_refresh = cls.__init__, cls.update, cls.refresh

    def init(self, *a, **k):
        orig_init(self, *a, **k)
        if getattr(self, "disable", False):
            # 2.3.3: thanh bị tắt (huggingface_hub đặt disable=None, không có cửa sổ đen -> tắt) không lưu unit / desc,
            # không tăng n -> tool từng báo "Đang nạp VieNeu-TTS 0/415319040" mãi dù đang tải
            try:
                self._sv_unit = k.get("unit", a[11] if len(a) > 11 else "it")
                self._sv_unit_scale = k.get("unit_scale", a[12] if len(a) > 12 else False)
                self._sv_desc = k.get("desc", a[1] if len(a) > 1 else "")
            except Exception:  # noqa: BLE001
                pass

    def update(self, n=1):
        r = orig_update(self, n)
        if getattr(self, "disable", False):
            try:
                self.n = (getattr(self, "n", 0) or 0) + (n or 0)
            except Exception:  # noqa: BLE001
                pass
        _report(self)
        return r

    def refresh(self, *a, **k):
        r = orig_refresh(self, *a, **k)
        _report(self)
        return r

    cls.__init__, cls.update, cls.refresh = init, update, refresh
    cls._trami_patched = True


@contextmanager
def tqdm_progress(progress: Callable[[float, str], None] | None, lo: float = 0.0, hi: float = 1.0,
                  label: str = "Đang xử lý", cancel: Callable[[], bool] | None = None, download: str = ""):
    """Trong khối này, thanh tqdm của thư viện -> progress(lo..hi, 'label n/total')."""
    if progress is None and cancel is None:
        yield
        return
    _install()
    ctx = {"progress": progress or (lambda *_: None), "lo": lo, "hi": hi, "label": label, "cancel": cancel,
           "last": 0.0, "download": download or "Đang tải mô hình", "speed": {}}
    tid = threading.get_ident()
    with _lock:
        prev = _by_thread.get(tid)
        _by_thread[tid] = ctx
        _order.append(ctx)
    try:
        yield
    finally:
        with _lock:
            if prev is None:
                _by_thread.pop(tid, None)
            else:
                _by_thread[tid] = prev
            _order[:] = [c for c in _order if c is not ctx]
            if not _order:
                _dl["n"].clear()
                _dl["tot"].clear()
