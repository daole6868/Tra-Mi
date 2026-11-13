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

IDLE_STOP = 300.0            # tiến trình đọc giọng (VieNeu / OmniVoice) không dùng 5 phút -> tắt, trả card (2.4.8: 10 -> 5)
IDLE_STOP_AI = 120.0         # bộ nghe (Qwen3 / FireRed), dịch trên máy (Hy-MT2), OCR trên card: không dùng 2 phút -> tắt
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
    for mod in ("trami.ocr_engine", "trami.bandit"):   # bộ đọc OCR (onnxruntime trên card giữ vùng nhớ riêng), Bandit
        m = sys.modules.get(mod)
        if m is not None and hasattr(m, "release"):
            try:
                m.release()
            except Exception:  # noqa: BLE001
                pass
    free_cache()


def _workers(kinds: set[str] | None = None):
    """(loại, tiến trình) đang có: tts (VieNeu / OmniVoice / VoxCPM2), asr (Qwen3), mt (Hy-MT2)."""
    out = []
    tts = sys.modules.get("trami.tts")
    if tts is not None:
        for w in (getattr(tts, "_vn_model", None), getattr(tts, "_omni_model", None),
                  getattr(tts, "_vox_model", None)):
            if w is not None:
                out.append(("tts", w))
    for name in ("trami.asr_qwen", "trami.asr_firered"):
        q = sys.modules.get(name)
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
    """Sau 1 việc GPU: bỏ mô hình nạp trong tiến trình giao diện + trả bộ nhớ đệm + tắt bộ nghe / Hy-MT2 (2.4.8: các nút
    nghe lại / dọn câu / soát OCR từng để tiến trình Qwen3 ~4,6 GB sống tới khi tắt tool). Tiến trình đọc giọng giữ lại
    để 'Tạo lại giọng' vài câu không phải nạp lại (2.4.8: nhưng trả phần đồ thị CUDA / Whisper đã phình ra) – quá IDLE_STOP
    giây không dùng thì tự tắt."""
    stop_workers({"asr", "mt"})
    if kind in TTS_KINDS or kind == "auto":   # đọc giọng xong: tiến trình đọc giọng trả đồ thị CUDA / Whisper / bộ nhớ đệm, giữ mô hình
        trim_workers()
    drop_local_models()


def trim_workers() -> int:
    n = 0
    for _, w in _workers({"tts"}):
        if hasattr(w, "trim"):
            try:
                n += bool(w.trim())
            except Exception:  # noqa: BLE001
                pass
    return n


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
        if eng in ("qwen3", "firered"):   # nghe 2 bộ: lần lượt từng bộ (bộ trước tắt rồi bộ sau mới nạp)
            q = 2600 if "0.6" in str(st.get("qwen_asr_model", "")) else 4600
            return q if eng == "qwen3" or st.get("asr_dual") else 3000
        return 3000 if eng == "whisper" else 1800
    if kind in ("dub", "redub"):
        eng = st.get("tts_engine", "edge")
        if eng == "vieneu" and (str(st.get("vieneu_device", "auto")).startswith("cpu") or
                                st.get("vieneu_mode") == "v3nano"):
            return 0      # 2.4.16: VieNeu chạy CPU – không cần chờ card
        return {"vieneu": 2600, "omnivoice": 4600, "voxcpm": 5500}.get(eng, 0)
    if kind == "translate":
        return 4600 if st.get("translate_engine") == "hymt" else 0
    return {"separate": 3000, "inpaint": 1600, "render": 1600, "realign": 2200, "diarize": 900,
            "dual": 4600}.get(kind, 0)


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


def reap_idle(max_idle: float = IDLE_STOP, max_idle_ai: float = IDLE_STOP_AI) -> int:
    """Tắt tiến trình AI để không lâu (đọc giọng max_idle, bộ nghe / Hy-MT2 max_idle_ai) + bỏ bộ đọc OCR trên card để
    không max_idle_ai. Chỉ gọi khi không có việc GPU nào đang chạy."""
    n = 0
    now = time.time()
    for k, w in _workers():
        lim = max_idle if k == "tts" else max_idle_ai
        if getattr(w, "proc", None) and w.proc.poll() is None and now - getattr(w, "last_used", now) > lim:
            n += _stop_if_idle(w)
    oe = sys.modules.get("trami.ocr_engine")
    if oe is not None and hasattr(oe, "release_idle"):
        try:
            n += int(oe.release_idle(max_idle_ai))
        except Exception:  # noqa: BLE001
            pass
    kv = sys.modules.get("trami.korva")       # 2.4.16 KorvaTTS (CPU, trong cửa sổ chính) – để không lâu thì bỏ, trả RAM
    if kv is not None and hasattr(kv, "release_idle"):
        try:
            n += int(kv.release_idle(max_idle))
        except Exception:  # noqa: BLE001
            pass
    # mô hình nạp trong cửa sổ chính ngoài việc GPU (Xem thử 1 khung xoá chữ = LaMa, nghe thử…) -> để không quá
    # max_idle_ai thì bỏ (trước đây giữ tới khi có việc GPU kế tiếp / tắt tool)
    if _local_loaded():
        if not _LOCAL_SEEN[0]:
            _LOCAL_SEEN[0] = now
        elif now - _LOCAL_SEEN[0] > max_idle_ai:
            drop_local_models()
            _LOCAL_SEEN[0] = 0.0
            n += 1
    else:
        _LOCAL_SEEN[0] = 0.0
    return n


_LOCAL_SEEN = [0.0]   # lúc reaper thấy có mô hình nạp trong cửa sổ chính (0 = không có)


def _local_loaded() -> bool:
    """Có mô hình AI nào đang nạp trong tiến trình giao diện không (LaMa, ECAPA, fa-zh, FunASR, Whisper, Bandit)."""
    for mod, n in (("trami.lama", "_model"), ("trami.diarize", "_enc"), ("trami.align", "_fa_model")):
        m = sys.modules.get(mod)
        if m is not None and getattr(m, n, None) is not None:
            return True
    for mod, n in (("trami.asr_funasr", "_cache"), ("trami.asr", "_model_cache")):
        m = sys.modules.get(mod)
        if m is not None and getattr(m, n, None):
            return True
    b = sys.modules.get("trami.bandit")
    return bool(b is not None and getattr(b, "_model", None) is not None)


# ------------------------------------------------------------------ ai đang chiếm card (2.4.8)
WORKER_FLAGS = {"--vieneu-worker": "VieNeu (đọc giọng)", "--omni-worker": "OmniVoice (đọc giọng)",
                "--voxcpm-worker": "VoxCPM2 (đọc giọng)",
                "--qwen-asr-worker": "Qwen3-ASR (bộ nghe)", "--firered-worker": "FireRedASR2 (bộ nghe)",
                "--hymt-worker": "Hy-MT2 (dịch trên máy)", "--hf-download": "tải mô hình"}
WORKER_CLASS = {"VieneuWorker": "VieNeu (đọc giọng)", "OmniWorker": "OmniVoice (đọc giọng)",
                "VoxWorker": "VoxCPM2 (đọc giọng)",
                "QwenAsrWorker": "Qwen3-ASR (bộ nghe)", "FireRedWorker": "FireRedASR2 (bộ nghe)",
                "HymtWorker": "Hy-MT2 (dịch trên máy)"}
KNOWN_APPS = {"chrome": "trình duyệt Chrome", "msedge": "trình duyệt Edge", "firefox": "trình duyệt Firefox",
              "brave": "trình duyệt Brave", "opera": "trình duyệt Opera", "browser": "trình duyệt Cốc Cốc",
              "coccoc": "trình duyệt Cốc Cốc", "dwm": "Windows – vẽ màn hình", "explorer": "Windows Explorer",
              "capcut": "CapCut", "jianyingpro": "CapCut (Jianying)", "obs64": "OBS", "zalo": "Zalo",
              "discord": "Discord", "steam": "Steam", "steamwebhelper": "Steam", "premiere": "Premiere",
              "adobe premiere pro": "Premiere", "afterfx": "After Effects", "photoshop": "Photoshop",
              "nvcontainer": "NVIDIA (phần mềm card)", "nvidia overlay": "NVIDIA overlay",
              "searchhost": "Windows – tìm kiếm", "widgets": "Windows – tiện ích", "textinputhost": "Windows – bàn phím",
              "msedgewebview2": "Windows WebView (Teams / Widgets…)"}
_PS = r"""
$ErrorActionPreference='SilentlyContinue'
[Console]::OutputEncoding=[Text.Encoding]::UTF8
$procs=@{}
foreach($p in Get-CimInstance Win32_Process){ $procs[[int]$p.ProcessId]=$p }
foreach($m in Get-CimInstance Win32_PerfFormattedData_GPUPerformanceCounters_GPUProcessMemory){
  if($m.Name -match '^pid_(\d+)_luid_(0x[0-9A-Fa-f]+_0x[0-9A-Fa-f]+)'){
    $id=[int]$Matches[1]; $p=$procs[$id]
    $par=0; $alive=0; $n=''; $cl=''
    if($p){ $par=[int]$p.ParentProcessId; $n=$p.Name; if($p.CommandLine){ $cl=($p.CommandLine -replace "[`t`r`n]",' ') } }
    if($procs.ContainsKey($par)){ $alive=1 }
    "G`t$id`t$($Matches[2])`t$($m.DedicatedUsage)`t$n`t$par`t$alive`t$cl"
  }
}
"""


def parse_ps(text: str) -> list[dict]:
    """Kết quả _PS -> mỗi tiến trình 1 dòng {pid, name, mb, parent, parent_alive, cmd} trên card CHÍNH (card có bộ nhớ
    riêng bị dùng nhiều nhất – máy laptop có thêm card Intel tích hợp). Thuần, test được."""
    by_luid: dict[str, dict[int, dict]] = {}
    for ln in (text or "").splitlines():
        parts = ln.rstrip("\r").split("\t")
        if len(parts) < 8 or parts[0] != "G":
            continue
        try:
            pid, used = int(parts[1]), int(float(parts[3] or 0))
            parent = int(parts[5] or 0)
        except ValueError:
            continue
        name = parts[4].strip()
        if name.lower().endswith(".exe"):
            name = name[:-4]
        row = by_luid.setdefault(parts[2], {}).setdefault(
            pid, {"pid": pid, "name": name, "mb": 0, "parent": parent, "parent_alive": parts[6] == "1",
                  "cmd": "\t".join(parts[7:]).strip()})
        row["mb"] += used // (1024 * 1024)
    if not by_luid:
        return []
    main = max(by_luid.values(), key=lambda d: sum(r["mb"] for r in d.values()))
    return sorted((r for r in main.values() if r["mb"] > 0), key=lambda r: -r["mb"])


def _win_processes() -> list[dict] | None:
    import subprocess

    if sys.platform != "win32":
        return None
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", _PS],
                           capture_output=True, timeout=25, creationflags=0x08000000)
    except (OSError, subprocess.SubprocessError):
        return None
    return parse_ps(r.stdout.decode("utf-8", "replace"))


def _smi_processes() -> list[dict]:
    """Máy không phải Windows: nvidia-smi cho biết tiến trình + MB (dòng lệnh đọc /proc)."""
    out = _smi(["--query-compute-apps=pid,process_name,used_memory", "--format=csv,noheader,nounits"]) or []
    rows = []
    for ln in out:
        parts = [x.strip() for x in ln.split(",")]
        try:
            pid, mb = int(parts[0]), int(float(parts[2]))
        except (ValueError, IndexError):
            continue
        cmd = ""
        try:
            cmd = open(f"/proc/{pid}/cmdline", "rb").read().replace(b"\0", b" ").decode("utf-8", "replace")
        except OSError:
            pass
        rows.append({"pid": pid, "name": parts[1].replace("\\", "/").rsplit("/", 1)[-1], "mb": mb, "parent": 0,
                     "parent_alive": True, "cmd": cmd})
    return sorted(rows, key=lambda r: -r["mb"])


def _ours() -> dict[int, tuple[str, object]]:
    """{pid: (tên, tiến trình phụ | None)} các tiến trình của CHÍNH cửa sổ Subvideo này."""
    import os

    ours: dict[int, tuple[str, object]] = {os.getpid(): ("cửa sổ chính", None)}
    for _, w in _workers():
        if getattr(w, "proc", None) and w.proc.poll() is None:
            ours[w.proc.pid] = (WORKER_CLASS.get(type(w).__name__, type(w).__name__), w)
    return ours


def _is_subvideo(cmd: str, name: str) -> bool:
    c = cmd.lower()
    return "trami.py" in c or "subvideo" in name.lower() or "subvideo.exe" in c or "-m trami" in c


def describe(rows: list[dict], ours: dict | None = None, now: float | None = None) -> list[dict]:
    """Gắn tên dễ hiểu + loại cho từng tiến trình: ours (của cửa sổ này) / orphan (tiến trình phụ Subvideo còn sót khi
    cửa sổ đã tắt) / other_sv (cửa sổ Subvideo khác) / app."""
    ours = _ours() if ours is None else ours
    now = time.time() if now is None else now
    out = []
    for r in rows:
        r = dict(r)
        flag = next((lab for f, lab in WORKER_FLAGS.items() if f in r.get("cmd", "")), "")
        if r["pid"] in ours:
            lab, w = ours[r["pid"]]
            r["kind"] = "ours"
            if w is None:
                r["who"] = ("Subvideo – cửa sổ chính (mô hình nạp tạm khi tách giọng / xoá phụ đề / OCR; ~0,3–0,5 GB "
                            "vùng làm việc của card chỉ trả khi tắt tool)")
            else:
                idle = max(0.0, now - getattr(w, "last_used", now))
                r["who"] = f"Subvideo – {lab}" + (f", không dùng {idle / 60:.0f} phút" if idle >= 60 else "")
        elif flag and _is_subvideo(r.get("cmd", ""), r.get("name", "")):
            if not r.get("parent_alive", True):
                r["kind"], r["who"] = "orphan", f"Subvideo – {flag} còn sót (cửa sổ Subvideo mở nó đã tắt)"
            else:
                r["kind"], r["who"] = "other_sv", f"Cửa sổ Subvideo khác – {flag}"
        elif _is_subvideo(r.get("cmd", ""), r.get("name", "")):
            r["kind"], r["who"] = "other_sv", "Cửa sổ Subvideo khác đang mở"
        else:
            nm = (r.get("name") or "?").lower()
            r["kind"] = "app"
            r["who"] = KNOWN_APPS.get(nm, r.get("name") or f"tiến trình {r['pid']}")
        out.append(r)
    return out


def usage_report() -> dict:
    """Card đang bị dùng bao nhiêu + tiến trình nào đang chiếm (cả chương trình khác). {total, used, rows, ok, why}."""
    info = vram_free()
    rows = _win_processes()
    why = ""
    if rows is None:
        rows = _smi_processes()
        if not rows and info is not None:
            why = "Máy này không cho biết từng chương trình dùng bao nhiêu – xem Trình quản lý tác vụ → Chi tiết"
    rows = describe(rows)
    total = info[1] if info else None
    used = (info[1] - info[0]) if info else (sum(r["mb"] for r in rows) or None)
    if info is None and not rows:
        why = "Không đo được card (không có card NVIDIA / chưa cài driver có nvidia-smi)"
    return {"total": total, "used": used, "rows": rows, "ok": bool(rows) or info is not None, "why": why}


def report_text(rep: dict) -> str:
    lines = []
    if rep.get("total"):
        lines.append(f"Card đang dùng {rep['used'] / 1024:.1f} / {rep['total'] / 1024:.1f} GB")
    for r in rep.get("rows", [])[:12]:
        lines.append(f"• {r['who']}: {r['mb'] / 1024:.1f} GB")
    if rep.get("why"):
        lines.append(rep["why"])
    return "\n".join(lines)


def kill_orphans(rows: list[dict]) -> int:
    """Tắt tiến trình phụ Subvideo còn sót (cửa sổ mở nó đã tắt – không ai dùng nữa mà vẫn giữ mô hình trên card)."""
    import os
    import signal
    import subprocess

    n = 0
    for r in rows:
        if r.get("kind") != "orphan":
            continue
        try:
            if sys.platform == "win32":
                subprocess.run(["taskkill", "/PID", str(r["pid"]), "/F"], capture_output=True, timeout=10,
                               creationflags=0x08000000)
            else:
                os.kill(r["pid"], signal.SIGTERM)
            n += 1
        except (OSError, subprocess.SubprocessError):
            pass
    return n


def release_all(rows: list[dict] | None = None) -> list[str]:
    """Nút "Trả bộ nhớ card": tắt mọi tiến trình AI của tool đang để không (đang đọc / nghe dở thì để nguyên), bỏ mô
    hình nạp trong cửa sổ chính, bỏ bộ đọc OCR, tắt tiến trình phụ còn sót. Trả danh sách đã tắt."""
    done = []
    for _, w in _workers():
        if getattr(w, "proc", None) and w.proc.poll() is None and _stop_if_idle(w):
            done.append(WORKER_CLASS.get(type(w).__name__, type(w).__name__))
    drop_local_models()
    k = kill_orphans(rows or [])
    if k:
        done.append(f"{k} tiến trình phụ còn sót")
    return done


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
