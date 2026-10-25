"""Trả bộ nhớ card đồ hoạ (GPU) giữa các việc.

Mô hình AI chạy ở 2 nơi:
  * ngay trong tiến trình giao diện: tách giọng (audio-separator), chia đoạn giọng nói (FunASR VAD), căn từng chữ fa-zh,
    SenseVoice / Paraformer, Whisper, phân biệt người nói (ECAPA), xoá phụ đề (LaMa). PyTorch GIỮ bộ nhớ đã dùng (bộ
    nhớ đệm của CUDA) cho tới khi được bảo trả lại -> tách giọng xong vẫn chiếm vài GB;
  * ở tiến trình riêng: Qwen3-ASR, VieNeu, OmniVoice, Hy-MT2 – sống tới khi tắt -> giữ nguyên mô hình trên card.
Card hết chỗ thì Windows lấy RAM thay (chậm đi nhiều lần). Vì vậy: trước mỗi việc GPU tắt các tiến trình việc đó không
dùng, sau mỗi việc GPU bỏ mô hình đã nạp trong tiến trình giao diện + trả bộ nhớ đệm; tiến trình đọc giọng để không quá
IDLE_STOP giây thì tự tắt.
"""
from __future__ import annotations

import gc
import sys
import threading
import time

IDLE_STOP = 600.0            # tiến trình đọc giọng (VieNeu / OmniVoice) không dùng 10 phút -> tắt, trả card
TTS_KINDS = {"dub", "redub"}  # việc dùng tiến trình đọc giọng


def _torch_cuda():
    t = sys.modules.get("torch")   # chỉ khi đã nạp torch – không tự nạp (mất vài giây, tạo CUDA)
    try:
        if t is not None and t.cuda.is_available() and t.cuda.is_initialized():
            return t
    except Exception:  # noqa: BLE001
        pass
    return None


def free_cache() -> None:
    """Trả bộ nhớ đệm CUDA của tiến trình giao diện (phần mô hình đã bỏ, tensor tạm)."""
    gc.collect()
    t = _torch_cuda()
    if t is not None:
        try:
            t.cuda.synchronize()
            t.cuda.empty_cache()
            t.cuda.ipc_collect()
        except Exception:  # noqa: BLE001
            pass


def drop_local_models() -> None:
    """Bỏ các mô hình đã nạp trong tiến trình giao diện (lần sau dùng nạp lại từ ổ đĩa, vài giây)."""
    for mod, names in (("trami.lama", ("_model",)), ("trami.diarize", ("_enc",)), ("trami.align", ("_fa_model",))):
        m = sys.modules.get(mod)
        for n in names:
            if m is not None and getattr(m, n, None) is not None:
                setattr(m, n, None)
    for mod, name in (("trami.asr_funasr", "_cache"), ("trami.asr", "_model_cache")):
        m = sys.modules.get(mod)
        c = getattr(m, name, None) if m is not None else None
        if isinstance(c, dict):
            c.clear()
    free_cache()


def _workers(kinds: set[str] | None = None):
    """(loại, tiến trình) đang có: tts (VieNeu / OmniVoice), asr (Qwen3), mt (Hy-MT2)."""
    out = []
    tts = sys.modules.get("trami.tts")
    if tts is not None:
        for w in (getattr(tts, "_vn_model", None), getattr(tts, "_omni_model", None)):
            if w is not None:
                out.append(("tts", w))
    q = sys.modules.get("trami.asr_qwen")
    if q is not None and getattr(q, "_worker", None) is not None:
        out.append(("asr", q._worker))
    tr = sys.modules.get("trami.translate")
    if tr is not None and getattr(tr, "_hymt", None) is not None:
        out.append(("mt", tr._hymt))
    return [(k, w) for k, w in out if kinds is None or k in kinds]


def _stop_if_idle(w) -> bool:
    """Tắt tiến trình nếu nó không đang đọc / nghe dở (đang dùng thì để nguyên)."""
    if not getattr(w, "proc", None) or w.proc.poll() is not None:
        return False
    lock = getattr(w, "lock", None)
    if lock is not None and not lock.acquire(blocking=False):
        return False
    try:
        w.stop()
        return True
    finally:
        if lock is not None:
            lock.release()


def stop_workers(kinds: set[str] | None = None) -> int:
    return sum(_stop_if_idle(w) for _, w in _workers(kinds))


def before_job(kind: str) -> None:
    """Trước 1 việc GPU: tắt tiến trình AI mà việc này không dùng -> việc này có đủ chỗ trên card."""
    if kind in TTS_KINDS:
        stop_workers({"asr", "mt"})
    else:
        stop_workers({"tts", "mt"} if kind != "translate" else {"tts", "asr"})
    drop_local_models()   # LaMa của "Xem thử 1 khung", ECAPA, SenseVoice… còn nằm trên card từ lần trước


def after_job(kind: str) -> None:
    """Sau 1 việc GPU: bỏ mô hình nạp trong tiến trình giao diện + trả bộ nhớ đệm (tiến trình đọc giọng giữ lại để
    'Tạo lại giọng' vài câu không phải nạp lại – quá IDLE_STOP giây không dùng thì tự tắt)."""
    drop_local_models()


# ------------------------------------------------------------------ chương trình KHÁC đang chiếm card
# Tool trả card giữa các việc của nó, nhưng trình duyệt (tăng tốc phần cứng), game, CapCut, 1 bản Subvideo khác… vẫn
# có thể chiếm card. Nạp mô hình vào card gần đầy -> Windows lấy RAM thay ("bộ nhớ GPU dùng chung") -> chạy chậm cỡ
# 5 %, GPU vẫn 98–100 %, máy đơ hàng chục phút. Trước mỗi việc GPU: đo chỗ trống THẬT của cả card (nvidia-smi), thiếu
# thì báo tên chương trình đang chiếm + chờ người dùng đóng bớt (tối đa VRAM_WAIT giây) rồi mới chạy.
VRAM_WAIT = 180.0
VRAM_HEADROOM = 300          # MB chừa thêm cho tensor tạm


def _smi(args: list[str]) -> list[str] | None:
    import shutil
    import subprocess

    exe = shutil.which("nvidia-smi")
    if not exe:
        return None
    try:
        r = subprocess.run([exe] + args, capture_output=True, text=True, timeout=6,
                           creationflags=0x08000000 if sys.platform == "win32" else 0)
    except (OSError, subprocess.SubprocessError):
        return None
    if r.returncode != 0:
        return None
    return [ln.strip() for ln in r.stdout.splitlines() if ln.strip()]


def gpu_name() -> str:
    """Tên card NVIDIA thứ nhất ('' = không có / không đo được) – hỏi nvidia-smi, không nạp PyTorch."""
    out = _smi(["--query-gpu=name", "--format=csv,noheader"])
    return out[0] if out else ""


def torch_build() -> str:
    """Bản PyTorch đã cài, đọc từ thông tin gói (vd '2.11.0+cu128' / '2.11.0+cpu') – KHÔNG import torch (import torch +
    khởi động CUDA trong tiến trình giao diện giữ ~0,5–1 GB RAM tới khi tắt tool). '' = chưa cài."""
    try:
        from importlib.metadata import PackageNotFoundError, version
    except ImportError:   # pragma: no cover
        return ""
    try:
        return version("torch")
    except PackageNotFoundError:
        if "torch" in sys.modules:   # bản đóng gói exe không còn thông tin gói – torch đã nạp thì hỏi thẳng
            return str(getattr(sys.modules["torch"], "__version__", ""))
        return ""
    except Exception:  # noqa: BLE001
        return ""


def vram_free() -> tuple[int, int] | None:
    """(MB còn trống, MB tổng) của card NVIDIA thứ nhất – tính cả chương trình khác. None = không đo được."""
    out = _smi(["--query-gpu=memory.free,memory.total", "--format=csv,noheader,nounits"])
    try:
        free, total = (int(float(x)) for x in out[0].split(",")[:2])
        return free, total
    except (TypeError, ValueError, IndexError):
        return None


def gpu_apps(skip: set[int] | None = None) -> list[tuple[str, int]]:
    """Chương trình đang dùng card (tên, MB – 0 nếu Windows không cho biết), trừ tiến trình của tool."""
    out = _smi(["--query-compute-apps=pid,process_name,used_memory", "--format=csv,noheader,nounits"]) or []
    skip = set(skip or ()) | {__import__("os").getpid()}
    for _, w in _workers():
        if getattr(w, "proc", None) and w.proc.poll() is None:
            skip.add(w.proc.pid)
    apps: dict[str, int] = {}
    for ln in out:
        parts = [x.strip() for x in ln.split(",")]
        if len(parts) < 3:
            continue
        try:
            pid = int(parts[0])
        except ValueError:
            continue
        if pid in skip:
            continue
        name = parts[1].replace("\\", "/").rsplit("/", 1)[-1]
        try:
            mb = int(float(parts[2]))
        except ValueError:
            mb = 0
        apps[name] = apps.get(name, 0) + mb
    return sorted(apps.items(), key=lambda x: -x[1])


def vram_need(kind: str, settings: dict | None = None) -> int:
    """Bộ nhớ card (MB) việc này cần – ước lượng theo mô hình đang chọn ở Cài đặt (0 = không dùng card)."""
    st = settings or {}
    if kind in ("recognize", "auto"):
        eng = st.get("asr_engine", "qwen3")
        if eng == "qwen3":
            return 2600 if "0.6" in str(st.get("qwen_asr_model", "")) else 4600
        return 3000 if eng == "whisper" else 1800
    if kind in ("dub", "redub"):
        eng = st.get("tts_engine", "edge")
        return {"vieneu": 2600, "omnivoice": 4600}.get(eng, 0)
    if kind == "translate":
        return 4600 if st.get("translate_engine") == "hymt" else 0
    return {"separate": 3000, "inpaint": 1600, "render": 1600, "realign": 2200, "diarize": 900}.get(kind, 0)


def wait_for_vram(kind: str, report=None, cancel=None, settings: dict | None = None,
                  wait_max: float = VRAM_WAIT, poll: float = 3.0) -> str:
    """Chờ tới khi card đủ chỗ cho việc `kind` (đã tắt tiến trình AI không dùng ở before_job). Trả ghi chú khi phải
    chạy dù card vẫn thiếu chỗ (chờ quá wait_max) – "" nếu đủ chỗ / không đo được / việc không dùng card."""
    if settings is None:
        try:
            from .config import load_settings

            settings = load_settings()
        except Exception:  # noqa: BLE001
            settings = {}
    if not settings.get("vram_check", True):
        return ""
    need = vram_need(kind, settings)
    if need <= 0:
        return ""
    t0 = time.time()
    while True:
        info = vram_free()
        if info is None:
            return ""
        free, total = info
        if free >= need + VRAM_HEADROOM:
            return ""
        apps = gpu_apps()
        who = ", ".join(f"{n} {mb / 1024:.1f} GB" if mb else n for n, mb in apps[:4])
        msg = (f"⚠ Card đồ hoạ chỉ còn trống {free / 1024:.1f} / {total / 1024:.1f} GB, việc này cần ~{need / 1024:.1f} GB"
               + (f" – đang chiếm card: {who}" if who else " – chương trình khác đang dùng card (trình duyệt, game, "
                  "CapCut…)") + ". Đóng bớt, tool tự chạy tiếp")
        left = wait_max - (time.time() - t0)
        if left <= 0:
            return (f"chạy khi card chỉ còn trống {free / 1024:.1f} GB (cần ~{need / 1024:.1f} GB) – có thể chậm; đóng "
                    "bớt chương trình dùng card rồi chạy lại nếu quá chậm")
        if report:
            report(f"{msg} (còn chờ {left:.0f} s)")
        end = time.time() + poll
        while time.time() < end:
            if cancel and cancel():
                from .ffmpeg import Cancelled

                raise Cancelled()
            time.sleep(0.2)


def reap_idle(max_idle: float = IDLE_STOP) -> int:
    n = 0
    now = time.time()
    for _, w in _workers({"tts"}):
        if getattr(w, "proc", None) and w.proc.poll() is None and now - getattr(w, "last_used", now) > max_idle:
            n += _stop_if_idle(w)
    return n


_reaper: threading.Thread | None = None


def start_reaper(busy=lambda: False, every: float = 30.0) -> None:
    """Luồng nền: tắt tiến trình đọc giọng để không quá lâu (không làm khi đang có việc GPU chạy)."""
    global _reaper
    if _reaper is not None:
        return

    def loop():
        while True:
            time.sleep(every)
            try:
                if not busy():
                    reap_idle()
            except Exception:  # noqa: BLE001
                pass

    _reaper = threading.Thread(target=loop, daemon=True, name="trami-gpu-reaper")
    _reaper.start()
