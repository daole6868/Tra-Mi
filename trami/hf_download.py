"""Tải mô hình HuggingFace (tab Tài liệu) ở TIẾN TRÌNH RIÊNG: tiến độ thật, Huỷ được, tự đổi cách tải khi bị treo.

Vì sao (2.0.49 – "Đang tải Hy-MT2 0/18" đứng im, cửa sổ đen chỉ có "Downloading bytes: 0.00B"):
  * huggingface_hub ≥ 1.0 tải file lớn qua Xet (gói hf_xet, mã Rust, máy chủ *.xethub.hf.co). Mạng / nhà mạng / VPN /
    tường lửa chặn hoặc làm nghẽn máy chủ Xet thì tải đứng ở 0 byte mãi, KHÔNG báo lỗi. Cách khắc phục được khuyên:
    HF_HUB_DISABLE_XET=1 (tải HTTP thường, có nối tiếp phần đã tải).
  * Thư viện chỉ vẽ thanh byte khi có cửa sổ đen (disable=None + kiểm tra TTY); thanh "Fetching 18 files" chỉ tăng khi
    xong trọn 1 file -> file trọng số 3,6 GB tải xong mới lên 1/18 -> trông như không tải gì.
  * Đứng im trong mã Rust thì không có lần cập nhật tqdm nào để nút Huỷ chen vào.
Cách làm: tiến trình con (`TraMi.py --hf-download <json>`) gọi snapshot_download với lớp tqdm riêng (luôn bật, không vẽ)
gom số byte đã nhận / đã ghi / số file, gửi về mỗi 0,5 s (`@@TRAMI@@{...}`). Tiến trình chính hiện %, MB, MB/s, số
file; bấm Huỷ -> giết tiến trình con. Không có byte nào mới sau STALL_XET giây khi tải kiểu Xet -> giết, xoá file dở,
tải lại kiểu thường và ghi nhớ (`config.NO_XET_FILE`, mọi lần tải sau trên máy này đều tải thường). Tải thường cũng
đứng im -> thử lại 1 lần (nối tiếp phần đã tải) rồi báo lỗi mạng rõ ràng.
"""
from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Callable

TAG = "@@TRAMI@@"
STALL_XET = 45.0      # giây không nhận thêm byte nào khi tải kiểu Xet -> coi là bị treo, chuyển sang tải thường
STALL_HTTP = 90.0     # tải thường: đứng im lâu hơn mới coi là mạng hỏng
BEAT = 0.5

Prog = Callable[[float, str], None]


def _fmt(n: float) -> str:
    return f"{n / 1e9:.2f} GB" if n >= 1e9 else f"{n / 1e6:.0f} MB" if n >= 1e6 else f"{n / 1e3:.0f} KB"


def repo_dir(repo: str) -> Path:
    try:
        from huggingface_hub.constants import HF_HUB_CACHE

        base = Path(HF_HUB_CACHE)
    except Exception:  # noqa: BLE001
        base = Path(os.environ.get("HF_HOME", Path.home() / ".cache" / "huggingface")) / "hub"
    return base / f"models--{repo.replace('/', '--')}"


def drop_incomplete(repo: str) -> None:
    """Xoá file tải dở (blobs/*.incomplete) – file Xet ghi dở không nối tiếp được bằng tải thường."""
    for f in (repo_dir(repo) / "blobs").glob("*.incomplete"):
        try:
            f.unlink()
        except OSError:
            pass


def xet_enabled() -> bool:
    from .config import NO_XET_FILE

    if os.environ.get("HF_HUB_DISABLE_XET", "").strip().lower() in ("1", "true", "yes", "on") or NO_XET_FILE.is_file():
        return False
    from .config import installed

    return installed("hf_xet")


def remember_no_xet(reason: str) -> None:
    from .config import NO_XET_FILE

    try:
        NO_XET_FILE.write_text(f"{time.strftime('%Y-%m-%d %H:%M')} {reason}\n", encoding="utf-8")
    except OSError:
        pass
    os.environ["HF_HUB_DISABLE_XET"] = "1"   # tiến trình Qwen3 / Hy-MT2 / OmniVoice mở sau cũng tải thường


# ------------------------------------------------------------------ đo tiến độ (dùng chung tiến trình con + kiểm thử)

class Stats:
    """Gom mọi thanh tqdm mà snapshot_download tạo: số file xong, byte đã ghi / tổng, byte nhận qua mạng."""

    def __init__(self):
        self.bars: list = []
        self.lock = threading.Lock()

    def snapshot(self) -> dict:
        files = [0, 0]
        written = total = net = 0
        has_net = False
        with self.lock:
            bars = list(self.bars)
        for b in bars:
            n, t = float(getattr(b, "n", 0) or 0), float(getattr(b, "total", 0) or 0)
            unit = str(getattr(b, "unit", "") or "").lower()
            desc = str(getattr(b, "desc", "") or "").lower()
            if unit not in ("b", "ib", "byte", "bytes"):
                files[0] += int(n)
                files[1] += int(t)
            elif "downloading bytes" in desc or "download complete" in desc:
                net += n          # byte nhận qua mạng (Xet nén / bỏ trùng nên khác kích thước file)
                has_net = True
            else:
                written += n
                total += t
        return {"files": files, "bytes": int(written), "total": int(total), "net": int(net if has_net else written)}


def tracker(stats: Stats):
    """Lớp tqdm truyền vào snapshot_download: luôn đếm (không tự tắt khi không có cửa sổ đen), không vẽ gì.
    Kế thừa tqdm gốc (không phải lớp tqdm của huggingface_hub) để thư viện không ép `disable`."""
    import io

    from tqdm.std import tqdm as base

    class Track(base):
        def __init__(self, *a, **kw):
            kw.pop("name", None)
            kw["disable"] = False
            kw["file"] = io.StringIO()
            kw["mininterval"] = 3600   # không cần vẽ lại
            super().__init__(*a, **kw)
            with stats.lock:
                stats.bars.append(self)

        def display(self, *a, **k):
            return None

        def __delattr__(self, attr):
            try:
                super().__delattr__(attr)
            except AttributeError:
                if attr != "_lock":
                    raise

    return Track


# ------------------------------------------------------------------ tiến trình con

def _emit(obj: dict) -> None:
    sys.stdout.write(TAG + json.dumps(obj, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def child_main(arg: str) -> None:
    """`TraMi.py --hf-download <json>`: tải 1 kho HuggingFace, gửi tiến độ mỗi 0,5 s."""
    req = json.loads(arg)
    stats = Stats()
    stop = threading.Event()

    def beat():
        while not stop.wait(BEAT):
            _emit(stats.snapshot())

    threading.Thread(target=beat, daemon=True).start()
    try:
        from huggingface_hub import snapshot_download

        kw = {"allow_patterns": req.get("patterns") or None}
        try:
            path = snapshot_download(req["repo"], tqdm_class=tracker(stats), **kw)
        except TypeError:   # bản huggingface_hub rất cũ không nhận tqdm_class
            path = snapshot_download(req["repo"], **kw)
        stop.set()
        _emit({**stats.snapshot(), "done": str(path)})
    except BaseException as e:  # noqa: BLE001
        stop.set()
        from .config import err_text

        _emit({"error": err_text(e)})
        sys.exit(1)


# ------------------------------------------------------------------ tiến trình chính

def _cmd(arg: str) -> list[str]:
    from .config import APP_DIR, FROZEN

    if FROZEN:
        return [sys.executable, "--hf-download", arg]
    return [sys.executable, str(APP_DIR / "TraMi.py"), "--hf-download", arg]


def _run(repo: str, patterns, token: str | None, xet: bool, progress: Prog | None, cancel, label: str,
         lo: float, hi: float) -> tuple[str, str]:
    """1 lần tải. Trả về ("ok", đường dẫn) / ("stall", thông báo) / ("error", thông báo)."""
    from .config import APP_DIR, DATA_DIR, no_console
    from .ffmpeg import _NO_WINDOW, Cancelled

    env = dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8", HF_HUB_DISABLE_SYMLINKS_WARNING="1")
    if not xet:
        env["HF_HUB_DISABLE_XET"] = "1"
    if token:
        env["HF_TOKEN"] = token
    err = None   # log của thư viện: cửa sổ đen; chạy không cửa sổ (pythonw) thì ghi file
    if no_console():
        (DATA_DIR / "logs").mkdir(parents=True, exist_ok=True)
        err = open(DATA_DIR / "logs" / "tai_mo_hinh.txt", "a", encoding="utf-8")
    arg = json.dumps({"repo": repo, "patterns": patterns}, ensure_ascii=False)
    proc = subprocess.Popen(_cmd(arg), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=err, text=True,
                            encoding="utf-8", errors="replace", bufsize=1, cwd=str(APP_DIR), env=env,
                            creationflags=_NO_WINDOW)
    q: queue.Queue = queue.Queue()

    def reader():
        assert proc.stdout
        for line in proc.stdout:
            if line.startswith(TAG):
                try:
                    q.put(json.loads(line[len(TAG):]))
                except ValueError:
                    pass
        q.put(None)

    threading.Thread(target=reader, daemon=True).start()
    limit = STALL_XET if xet else STALL_HTTP
    t0 = last_change = time.time()
    key = None
    speed, sp_t, sp_n = 0.0, t0, 0
    shown = 0.0
    result: tuple[str, str] | None = None
    try:
        while result is None:
            if cancel and cancel():
                raise Cancelled()
            try:
                obj = q.get(timeout=0.3)
            except queue.Empty:
                obj = {}
            now = time.time()
            if obj is None:   # tiến trình con đã thoát mà không báo xong / lỗi
                proc.wait(10)
                result = ("error", f"tiến trình tải bị tắt đột ngột (mã {proc.returncode})")
                break
            if "done" in obj:
                result = ("ok", obj["done"])
            elif "error" in obj:
                result = ("error", obj["error"])
            if result:
                break
            if obj:
                files, written, total, net = obj["files"], obj["bytes"], obj["total"], obj["net"]
                k = (files[0], written, net)
                if k != key:
                    key, last_change = k, now
                if now - sp_t >= 1.0:   # tốc độ trung bình trượt theo từng giây
                    cur = max(0.0, (net - sp_n) / (now - sp_t))
                    speed = cur if speed <= 0 else speed * 0.6 + cur * 0.4
                    sp_t, sp_n = now, net
                if progress and now - shown >= 0.4:
                    shown = now
                    idle = now - last_change
                    sp = f" · {speed / 1e6:.1f} MB/s" if speed > 0 and idle < 3 else ""
                    nf = f" · {files[0]}/{files[1]} file" if files[1] else ""
                    wait = f" · chờ dữ liệu {int(idle)} s" if idle >= 5 else ""
                    frac = 0.0
                    if total >= 2e6:   # đã biết file trọng số lớn -> % theo byte
                        frac = min(1.0, written / total)
                        msg = f"{label}: {int(frac * 100)}% · {_fmt(written)} / {_fmt(total)}{sp}{nf}{wait}"
                    elif net >= 1e6:
                        msg = f"{label}: đã nhận {_fmt(net)}{sp}{nf}{wait}"
                    elif files[1]:
                        msg = f"{label}: đang tải{nf}{wait}"
                    else:
                        msg = f"{label}: đang kết nối HuggingFace… {int(now - t0)} s"
                    progress(lo + (hi - lo) * frac, msg)
            if now - last_change > limit:
                result = ("stall", f"không nhận thêm dữ liệu nào trong {int(limit)} giây")
    finally:
        if proc.poll() is None:
            proc.kill()
            try:
                proc.wait(10)
            except subprocess.TimeoutExpired:
                pass
        if err:
            err.close()
    return result


NET_HELP = ("Kiểm tra mạng (mở thử huggingface.co trên trình duyệt), tắt VPN / proxy / phần mềm chặn quảng cáo hoặc "
            "đổi mạng (vd. phát Wi-Fi từ điện thoại) rồi bấm Tải về lần nữa – phần đã tải được giữ lại.")


def download(repo: str, patterns=None, token: str | None = None, progress: Prog | None = None, cancel=None,
             label: str = "Đang tải mô hình", lo: float = 0.0, hi: float = 1.0) -> Path:
    """Tải kho HuggingFace `repo` vào kho trên máy (đúng chỗ from_pretrained / snapshot_download nạp)."""
    xet = xet_enabled()
    http_tries = 0
    while True:
        kind, info = _run(repo, patterns, token, xet, progress, cancel, label, lo, hi)
        if kind == "ok":
            return Path(info)
        fatal = any(x in info for x in ("401", "403", "404", "Gated", "gated", "restricted", "RepositoryNotFound"))
        if kind == "error" and fatal:   # cần token / đồng ý điều khoản / sai tên kho: tải lại cũng vậy
            raise RuntimeError(info)
        if xet:
            # Xet đứng im / lỗi -> tải thường (file Xet ghi dở không nối tiếp được -> xoá); chỉ ghi nhớ khi đúng là
            # lỗi của Xet (mất mạng hẳn thì tải thường cũng lỗi, không cần tắt Xet mãi)
            if kind == "stall" or any(x in info.lower() for x in ("xet", "cas", "reconstruct")):
                remember_no_xet(f"{repo}: {info}")
            drop_incomplete(repo)
            xet = False
            if progress:
                progress(lo, f"{label}: kiểu tải Xet {info} – chuyển sang tải thường…")
            continue
        http_tries += 1
        if http_tries >= 2:
            if kind == "stall":
                raise RuntimeError(f"Tải {repo} bị đứng: {info}. {NET_HELP}")
            raise RuntimeError(f"Tải {repo} lỗi: {info}. {NET_HELP}")
        if progress:
            progress(lo, f"{label}: {info} – thử lại, tải tiếp phần còn thiếu…")
