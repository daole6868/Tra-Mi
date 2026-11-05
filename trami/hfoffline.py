"""Nạp mô hình HuggingFace bằng bản ĐÃ CÓ trên máy trước, không hỏi mạng (VieNeu, OmniVoice).

2.3.3: "Đang nạp VieNeu-TTS 0/7207223 … 0/415319040" tăng mãi. Thư viện VieNeu / OmniVoice gọi `from_pretrained` /
`hf_hub_download` theo tên kho -> MỖI lần mở hỏi HuggingFace bản mới nhất; kho VieNeu (`pnnbao-ump/VieNeu-TTS-v3-Turbo`,
thư mục "update") vừa có bản mới -> thư viện tải lại ~400 MB dù máy đã có bản cũ, mạng chặn máy chủ tải file lớn của
HuggingFace (như Hy-MT2 ở 2.0.50) -> đứng ở 0 byte mãi. Giờ: thử nạp không mạng (HF_HUB_OFFLINE) bằng bản đã có; chỉ khi
máy thiếu file mới tải, và tải đứng im quá lâu thì dừng + báo rõ. Lần hỏi mạng bị dở có thể đã đổi refs/main sang bản
mới chưa tải xong -> trỏ lại bản cũ còn đủ file trọng số."""
from __future__ import annotations

import os
import sys
import threading
import time
from pathlib import Path

WEIGHTS = {".safetensors", ".bin", ".pt", ".pth", ".onnx", ".ckpt", ".gguf"}
WEIGHT_MIN = 1_000_000      # file trọng số thật (bỏ file nhỏ)
DL_STALL = 180.0            # tải không nhận thêm byte nào quá 3 phút -> dừng, báo mạng chặn
DL_NOTE = 20.0              # sau 20 s không nhận dữ liệu thì dòng tiến độ báo "chưa nhận được dữ liệu …"
_MISSING = ("offline", "local_files_only", "local cache", "cached files", "couldn't connect", "could not connect",
            "cannot find the requested files", "not found in cache", "does not appear to have", "no such file")
_MISSING_TYPES = {"LocalEntryNotFoundError", "OfflineModeIsEnabled", "EntryNotFoundError", "FileNotFoundError"}


def offline(on: bool) -> None:
    """Bật / tắt chế độ không mạng của huggingface_hub (transformers đọc cùng cờ này mỗi lần gọi)."""
    os.environ["HF_HUB_OFFLINE"] = "1" if on else "0"
    try:
        import huggingface_hub.constants as hc

        hc.HF_HUB_OFFLINE = bool(on)
    except Exception:  # noqa: BLE001
        pass


def _weights(snap: Path) -> int:
    n = 0
    try:
        for f in snap.rglob("*"):
            if f.suffix.lower() in WEIGHTS:
                try:
                    if f.stat().st_size >= WEIGHT_MIN:   # liên kết hỏng (bản đang tải dở) -> lỗi -> không tính
                        n += 1
                except OSError:
                    pass
    except OSError:
        pass
    return n


def cache_root() -> Path | None:
    try:
        import huggingface_hub.constants as hc

        return Path(hc.HF_HUB_CACHE)
    except Exception:  # noqa: BLE001
        return None


def repair_refs(patterns, root: Path | None = None) -> list[str]:
    """Kho có tên chứa 1 trong `patterns` mà refs/main trỏ tới bản CHƯA có file trọng số nào (lần tải trước bị dở)
    trong khi bản khác đã đủ -> trỏ refs/main về bản đó (mới nhất). Trả danh sách kho đã sửa."""
    root = root or cache_root()
    out: list[str] = []
    if root is None or not root.is_dir():
        return out
    pats = [p.lower() for p in patterns]
    for d in root.glob("models--*"):
        name = d.name[len("models--"):].replace("--", "/")
        if not any(p in name.lower() for p in pats):
            continue
        ref, snaps = d / "refs" / "main", d / "snapshots"
        if not ref.is_file() or not snaps.is_dir():
            continue
        try:
            cur = ref.read_text("utf-8").strip()
        except OSError:
            continue
        if (snaps / cur).is_dir() and _weights(snaps / cur):
            continue
        good = [s for s in snaps.iterdir() if s.is_dir() and s.name != cur and _weights(s)]
        if not good:
            continue
        best = max(good, key=lambda s: s.stat().st_mtime)
        try:
            ref.write_text(best.name, "utf-8")
        except OSError:
            continue
        out.append(f"{name}: {cur[:8]} -> {best.name[:8]}")
    return out


def missing_local(e: BaseException) -> bool:
    """Lỗi do máy chưa có đủ file (chế độ không mạng) – thử lại có mạng mới có ích."""
    seen = set()
    c: BaseException | None = e
    while c is not None and id(c) not in seen:
        seen.add(id(c))
        if type(c).__name__ in _MISSING_TYPES or any(k in str(c).lower() for k in _MISSING):
            return True
        c = c.__cause__ or c.__context__
    return False


class _Watch:
    """Canh lúc tải: dòng tiến độ báo chưa nhận được dữ liệu; đứng im quá `stall` giây -> on_stall()."""

    def __init__(self, report, on_stall, label: str, stall: float = DL_STALL):
        self.report, self.on_stall, self.label, self.stall = report, on_stall, label, stall
        self._stop = threading.Event()
        self._t = threading.Thread(target=self._run, daemon=True, name="trami-dl-watch")

    def __enter__(self):
        self._t.start()
        return self

    def __exit__(self, *exc):
        self._stop.set()
        return False

    def _run(self):
        from .progress_hook import download_state

        while not self._stop.wait(5.0):
            st = download_state()
            if not st["bars"]:
                continue
            idle = time.time() - st["t"]
            if idle >= self.stall:
                self.on_stall(st, idle)
                return
            if idle >= DL_NOTE and self.report:
                got = st["bytes"] / 1e6
                try:
                    self.report(0.0, f"{self.label}: chưa nhận thêm dữ liệu {idle:.0f} s (đã tải {got:.0f} MB / "
                                     f"{st['total'] / 1e6:.0f} MB) – mạng có thể đang chặn máy chủ tải của HuggingFace")
                except Exception:  # noqa: BLE001
                    pass


def load_local_first(make, patterns, what: str, report=None, on_stall=None):
    """make() nạp mô hình. Thử không mạng bằng bản đã có trên máy; thiếu file -> có mạng (tải), canh tải đứng im.
    Nạp xong trả lại chế độ có mạng (OmniVoice còn tự tải Whisper khi cần nhái giọng không có lời mẫu)."""
    fixed = repair_refs(patterns)
    if fixed:
        print(f"[Subvideo] {what}: lần tải trước bị dở – dùng lại bản đã có: {'; '.join(fixed)}", file=sys.stderr,
              flush=True)
    offline(True)
    try:
        obj = make()
        print(f"[Subvideo] {what}: nạp bản đã có trên máy (không hỏi HuggingFace)", file=sys.stderr, flush=True)
        return obj
    except Exception as e:  # noqa: BLE001
        if not missing_local(e):
            raise
        print(f"[Subvideo] {what}: máy chưa có đủ file ({type(e).__name__}) -> tải từ HuggingFace", file=sys.stderr,
              flush=True)
    finally:
        offline(False)
    with _Watch(report, on_stall or (lambda st, idle: None), f"Đang tải {what}"):
        return make()
