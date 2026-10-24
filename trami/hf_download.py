"""Tải mô hình HuggingFace (tab Tài liệu) ở TIẾN TRÌNH RIÊNG: tiến độ thật, Huỷ được, tự đổi đường tải khi bị treo.

Vì sao (2.0.49 – "Đang tải Hy-MT2 0/18" đứng im, cửa sổ đen chỉ có "Downloading bytes: 0.00B"):
  * huggingface_hub ≥ 1.0 tải file lớn qua Xet (gói hf_xet, mã Rust, máy chủ *.xethub.hf.co). Mạng / nhà mạng / VPN /
    tường lửa chặn hoặc làm nghẽn máy chủ Xet thì tải đứng ở 0 byte mãi, KHÔNG báo lỗi, không huỷ được.
  * Thư viện chỉ vẽ thanh byte khi có cửa sổ đen (disable=None + kiểm tra TTY); thanh "Fetching 18 files" chỉ tăng khi
    xong trọn 1 file -> file trọng số 3,6 GB tải xong mới lên 1/18 -> trông như không tải gì.
2.0.50 – tải thường (HF_HUB_DISABLE_XET) cũng đứng: file lớn của HuggingFace giờ nằm trên Xet, tải thường cũng bị chuyển
  hướng sang cas-bridge.xethub.hf.co -> mạng chặn máy chủ này thì cả 2 cách đều đứng. Thêm nguồn thứ 2: ModelScope (Tencent,
  Qwen đăng chính thức cùng mô hình ở đó; ModelScope vốn tải được trên máy người dùng – SenseVoice), tải thẳng vào kho
  HuggingFace trên máy (snapshot riêng + refs/main) -> các tiến trình nạp mô hình theo tên kho HF không phải đổi gì.

Đường tải (`download`): HuggingFace kiểu Xet (đứng im STALL_XET giây -> bỏ, ghi nhớ không dùng Xet nữa) -> HuggingFace
tải thường (đứng im STALL_HTTP giây; đã nhận được dữ liệu thì thử lại 1 lần, nối tiếp) -> ModelScope. HuggingFace hỏng mà
ModelScope được -> ghi nhớ (`config.PREFER_MS_FILE`), lần sau thử ModelScope trước. Tiến trình con
(`TraMi.py --hf-download <json>`) gom tiến độ từ mọi thanh tqdm (lớp tqdm riêng cho HuggingFace, móc tqdm cho ModelScope)
+ dung lượng thư mục đang ghi, gửi về mỗi 0,5 s (`@@TRAMI@@{...}`); bấm Huỷ -> giết tiến trình con.
"""
from __future__ import annotations

import hashlib
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
STALL_XET = 45.0      # giây không nhận thêm byte nào khi tải kiểu Xet -> coi là bị treo
STALL_HTTP = 60.0     # HuggingFace tải thường
STALL_MS = 90.0       # ModelScope (máy chủ ở Trung Quốc, bắt đầu chậm hơn)
BEAT = 0.5

# kho ModelScope chính thức của cùng mô hình (cùng file). Chỉ ghi kho do chính hãng đăng – không đoán tên kho của người lạ.
MS_MIRRORS = {
    "tencent/Hy-MT2-1.8B": ["Tencent-Hunyuan/Hy-MT2-1.8B"],
    "tencent/Hy-MT2-7B": ["Tencent-Hunyuan/Hy-MT2-7B"],
}
MS_SAME_ORGS = ("Qwen",)   # tổ chức đăng cùng tên kho trên cả 2 nơi

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


def ms_commit(ms_id: str) -> str:
    """Tên snapshot (40 chữ số hex như mã commit) cho bản tải từ ModelScope."""
    return hashlib.sha1(f"modelscope:{ms_id}".encode()).hexdigest()


def ms_candidates(repo: str) -> list[str]:
    out = list(MS_MIRRORS.get(repo, []))
    if repo.split("/")[0] in MS_SAME_ORGS and repo not in out:
        out.append(repo)
    return out


def dir_bytes(path: Path) -> int:
    """Tổng dung lượng file thật trong thư mục (bỏ symlink, bỏ .locks) – đo được cả khi thư viện không báo tiến độ."""
    total = 0
    stack = [str(path)]
    while stack:
        try:
            with os.scandir(stack.pop()) as it:
                for e in it:
                    try:
                        if e.is_symlink():
                            continue
                        if e.is_dir():
                            if e.name != ".locks":
                                stack.append(e.path)
                        else:
                            total += e.stat().st_size
                    except OSError:
                        pass
        except OSError:
            pass
    return total


def drop_incomplete(repo: str) -> None:
    """Xoá file tải dở (blobs/*.incomplete) – file Xet ghi dở không nối tiếp được bằng tải thường."""
    for f in (repo_dir(repo) / "blobs").glob("*.incomplete"):
        try:
            f.unlink()
        except OSError:
            pass


def xet_enabled() -> bool:
    from .config import NO_XET_FILE, installed

    if os.environ.get("HF_HUB_DISABLE_XET", "").strip().lower() in ("1", "true", "yes", "on") or NO_XET_FILE.is_file():
        return False
    return installed("hf_xet")


def _note(path: Path, reason: str) -> None:
    try:
        path.write_text(f"{time.strftime('%Y-%m-%d %H:%M')} {reason}\n", encoding="utf-8")
    except OSError:
        pass


def remember_no_xet(reason: str) -> None:
    from .config import NO_XET_FILE

    _note(NO_XET_FILE, reason)
    os.environ["HF_HUB_DISABLE_XET"] = "1"   # tiến trình Qwen3 / Hy-MT2 / OmniVoice mở sau cũng tải thường


# ------------------------------------------------------------------ đo tiến độ (dùng chung tiến trình con + kiểm thử)

class Stats:
    """Gom mọi thanh tqdm của thư viện tải: số file xong, byte đã ghi / tổng, byte nhận qua mạng."""

    def __init__(self):
        self.bars: dict[int, object] = {}
        self.lock = threading.Lock()

    def add(self, bar) -> None:
        with self.lock:
            self.bars[id(bar)] = bar

    def snapshot(self) -> dict:
        files = [0, 0]
        written = total = net = 0
        has_net = False
        with self.lock:
            bars = list(self.bars.values())
        for b in bars:
            n, t = float(getattr(b, "n", 0) or 0), float(getattr(b, "total", 0) or 0)
            unit = str(getattr(b, "unit", "") or "").lower()
            desc = str(getattr(b, "desc", "") or "").lower()
            if unit not in ("b", "ib", "byte", "bytes"):
                if t < 10000:   # thanh đếm file ("Fetching 18 files")
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
            stats.add(self)

        def display(self, *a, **k):
            return None

        def __delattr__(self, attr):
            try:
                super().__delattr__(attr)
            except AttributeError:
                if attr != "_lock":
                    raise

    return Track


def hook_all_tqdm(stats: Stats) -> None:
    """Ghi nhận mọi thanh tqdm tạo ra trong tiến trình (ModelScope không nhận lớp tqdm riêng)."""
    from tqdm.std import tqdm as base

    orig = base.__init__

    def init(self, *a, **kw):
        orig(self, *a, **kw)
        stats.add(self)

    base.__init__ = init


# ------------------------------------------------------------------ tiến trình con

def _emit(obj: dict) -> None:
    sys.stdout.write(TAG + json.dumps(obj, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def _ms_download(repo: str, ms_id: str, patterns) -> Path:
    """Tải kho ModelScope `ms_id` thẳng vào kho HuggingFace trên máy của `repo` (snapshot riêng + refs/main)."""
    from modelscope.hub.snapshot_download import snapshot_download

    commit = ms_commit(ms_id)
    snap = repo_dir(repo) / "snapshots" / commit
    snap.mkdir(parents=True, exist_ok=True)
    snapshot_download(ms_id, local_dir=str(snap), allow_file_pattern=patterns or None)
    refs = repo_dir(repo) / "refs"
    refs.mkdir(parents=True, exist_ok=True)
    (refs / "main").write_text(commit, encoding="utf-8")   # from_pretrained(repo, local_files_only) đọc snapshot này
    return snap


def child_main(arg: str) -> None:
    """`TraMi.py --hf-download <json>`: tải 1 kho, gửi tiến độ mỗi 0,5 s."""
    req = json.loads(arg)
    stats = Stats()
    stop = threading.Event()
    where = repo_dir(req["repo"])

    def beat():
        while not stop.wait(BEAT):
            _emit({**stats.snapshot(), "disk": dir_bytes(where)})

    threading.Thread(target=beat, daemon=True).start()
    try:
        patterns = req.get("patterns") or None
        if req.get("ms_id"):
            hook_all_tqdm(stats)
            path = _ms_download(req["repo"], req["ms_id"], patterns)
        else:
            from huggingface_hub import snapshot_download

            try:
                path = snapshot_download(req["repo"], tqdm_class=tracker(stats), allow_patterns=patterns)
            except TypeError:   # bản huggingface_hub rất cũ không nhận tqdm_class
                path = snapshot_download(req["repo"], allow_patterns=patterns)
        stop.set()
        _emit({**stats.snapshot(), "disk": dir_bytes(where), "done": str(path)})
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


def _run(req: dict, env_extra: dict, limit: float, progress: Prog | None, cancel, label: str,
         lo: float, hi: float) -> tuple[str, str, int]:
    """1 lần tải. Trả về (kết quả, thông tin, số byte nhận được): ("ok", đường dẫn) / ("stall", …) / ("error", …)."""
    from .config import APP_DIR, DATA_DIR, no_console
    from .ffmpeg import _NO_WINDOW, Cancelled

    env = dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8", HF_HUB_DISABLE_SYMLINKS_WARNING="1", **env_extra)
    err = None   # log của thư viện: cửa sổ đen; chạy không cửa sổ (pythonw) thì ghi file
    if no_console():
        (DATA_DIR / "logs").mkdir(parents=True, exist_ok=True)
        err = open(DATA_DIR / "logs" / "tai_mo_hinh.txt", "a", encoding="utf-8")
    proc = subprocess.Popen(_cmd(json.dumps(req, ensure_ascii=False)), stdin=subprocess.DEVNULL,
                            stdout=subprocess.PIPE, stderr=err, text=True, encoding="utf-8", errors="replace",
                            bufsize=1, cwd=str(APP_DIR), env=env, creationflags=_NO_WINDOW)
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
    t0 = last_change = time.time()
    key = None
    disk0 = None
    got = 0                      # byte nhận được trong lần này (mạng hoặc ghi ra đĩa)
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
                disk = int(obj.get("disk", 0))
                if disk0 is None:
                    disk0 = disk
                grew = max(0, disk - disk0)
                got = max(got, net, grew)
                k = (files[0], written, net, disk)
                if k != key:
                    key, last_change = k, now
                if now - sp_t >= 1.0:   # tốc độ trung bình trượt theo từng giây
                    cur = max(0.0, (got - sp_n) / (now - sp_t))
                    speed = cur if speed <= 0 else speed * 0.6 + cur * 0.4
                    sp_t, sp_n = now, got
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
                    elif got >= 1e6:
                        msg = f"{label}: đã nhận {_fmt(got)}{sp}{nf}{wait}"
                    elif files[1]:
                        msg = f"{label}: đang tải{nf}{wait}"
                    else:
                        msg = f"{label}: đang kết nối… {int(now - t0)} s"
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
    return result[0], result[1], got


NET_HELP = ("Kiểm tra mạng (mở thử huggingface.co và modelscope.cn trên trình duyệt), tắt VPN / proxy / phần mềm chặn "
            "quảng cáo hoặc đổi mạng (vd. phát Wi-Fi từ điện thoại) rồi bấm Tải về lần nữa – phần đã tải được giữ lại.")


def _fatal(info: str) -> bool:
    """Lỗi tải lại cũng vậy: cần token / đồng ý điều khoản."""
    return any(x in info for x in ("GatedRepo", "gated", "restricted", "401 ", "401:", " 401", "Unauthorized"))


def _not_found(info: str) -> bool:
    return any(x in info for x in ("RepositoryNotFound", "404", "NotExist", "not exist", "not found", "Not Found"))


def download(repo: str, patterns=None, token: str | None = None, progress: Prog | None = None, cancel=None,
             label: str = "Đang tải mô hình", lo: float = 0.0, hi: float = 1.0) -> Path:
    """Tải kho HuggingFace `repo` vào kho trên máy (đúng chỗ from_pretrained / snapshot_download nạp).
    HuggingFace không tải được -> tải cùng mô hình từ ModelScope (nếu hãng có đăng ở đó)."""
    from .config import PREFER_MS_FILE, installed

    def say(msg: str) -> None:
        if progress:
            progress(lo, f"{label}: {msg}")

    ms_ids = ms_candidates(repo) if installed("modelscope") else []
    order = ["ms", "hf"] if ms_ids and PREFER_MS_FILE.is_file() else ["hf", "ms"]
    report: list[str] = []
    hf_failed = False
    for src in order:
        if src == "hf":
            env = {"HF_TOKEN": token} if token else {}
            xet = xet_enabled()
            http_tries = 0
            while True:
                mode = "Xet" if xet else "tải thường"
                kind, info, got = _run({"repo": repo, "patterns": patterns}, env if xet else {**env, "HF_HUB_DISABLE_XET": "1"},
                                       STALL_XET if xet else STALL_HTTP, progress, cancel, label, lo, hi)
                if kind == "ok":
                    return Path(info)
                if _fatal(info):
                    raise RuntimeError(info)
                report.append(f"HuggingFace ({mode}): {info} (nhận được {_fmt(got)})")
                if xet:
                    # Xet đứng im / lỗi -> tải thường (file Xet ghi dở không nối tiếp được -> xoá); chỉ ghi nhớ khi
                    # đúng là lỗi của Xet (mất mạng hẳn thì tải thường cũng lỗi, không cần tắt Xet mãi)
                    if kind == "stall" or any(x in info.lower() for x in ("xet", "cas", "reconstruct")):
                        remember_no_xet(f"{repo}: {info}")
                    drop_incomplete(repo)
                    xet = False
                    say(f"kiểu tải Xet {info} – chuyển sang tải thường…")
                    continue
                http_tries += 1
                # đã nhận được dữ liệu rồi mới đứng -> mạng chập chờn, thử lại 1 lần (nối tiếp phần đã tải)
                if http_tries < 2 and got >= 1e6 and not _not_found(info):
                    say(f"{info} – thử lại, tải tiếp phần còn thiếu…")
                    continue
                break
            hf_failed = True
            if ms_ids and "ms" not in order[:order.index(src)]:
                say("HuggingFace không tải được – chuyển sang tải từ ModelScope…")
        else:
            for ms_id in ms_ids:
                tries = 0
                while True:
                    kind, info, got = _run({"repo": repo, "patterns": patterns, "ms_id": ms_id}, {}, STALL_MS,
                                           progress, cancel, label + " (ModelScope)", lo, hi)
                    if kind == "ok":
                        if hf_failed:
                            _note(PREFER_MS_FILE, f"{repo}: HuggingFace không tải được, ModelScope được")
                        return Path(info)
                    report.append(f"ModelScope {ms_id}: {info} (nhận được {_fmt(got)})")
                    tries += 1
                    if tries < 2 and got >= 1e6 and not _not_found(info):
                        say(f"ModelScope {info} – thử lại, tải tiếp phần còn thiếu…")
                        continue
                    break
            if "hf" in order[order.index(src) + 1:]:
                say("ModelScope không tải được – thử HuggingFace…")
    detail = "\n".join("• " + r for r in report)
    raise RuntimeError(f"Không tải được {repo}:\n{detail}\n{NET_HELP}")
