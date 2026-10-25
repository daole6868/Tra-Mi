"""Gom các mô hình AI đã tải trên máy này vào thư mục `data` của bản exe mang sang máy khác (DongGoi_MoHinh.bat):
data/models/... (tách giọng, SenseVoice / FunASR, Whisper, ECAPA, LaMa…) + kho HuggingFace của tool -> data/models/hf
(Qwen3, Hy-MT2, VieNeu, OmniVoice, giọng nam / nữ…). KHÔNG chép cài đặt (API key), tài khoản, cookie, hồ sơ Chrome, giọng
ghi âm, dự án. Máy nhận: exe thấy thư mục data cạnh mình -> dùng luôn, kho HuggingFace = data/models/hf (config.py)."""
from __future__ import annotations

import os
import re
import shutil
import sys
from pathlib import Path

from .config import MODELS_DIR

SKIP_NAMES = {".locks", "__pycache__"}
SKIP_SUFFIX = (".incomplete", ".tmp", ".lock", ".part")
# gói có ghi tên kho mô hình phụ trong mã nguồn (VieNeu: bộ mã hoá âm thanh, OmniVoice: Whisper nghe file mẫu…)
SOURCE_PKGS = ("trami", "vieneu", "vieneu_utils", "omnivoice", "speechbrain")


def hf_cache() -> Path:
    try:
        from huggingface_hub.constants import HF_HUB_CACHE

        return Path(HF_HUB_CACHE)
    except Exception:  # noqa: BLE001
        return Path(os.environ.get("HF_HOME", Path.home() / ".cache" / "huggingface")) / "hub"


def _pkg_text() -> str:
    """Mã nguồn của tool + các gói AI (để biết kho HuggingFace nào thật sự được dùng)."""
    import importlib.util

    chunks = []
    for name in SOURCE_PKGS:
        try:
            spec = importlib.util.find_spec(name)
        except (ImportError, ValueError):
            spec = None
        if not spec:
            continue
        roots = list(spec.submodule_search_locations or []) or ([str(Path(spec.origin).parent)] if spec.origin else [])
        for root in roots:
            for f in Path(root).rglob("*.py"):
                try:
                    chunks.append(f.read_text("utf-8", errors="ignore"))
                except OSError:
                    pass
    return "\n".join(chunks)


def used_hf_repos(cache: Path | None = None) -> list[Path]:
    """Các kho trong kho HuggingFace trên máy mà tool / gói AI có nhắc tới tên (bỏ kho của phần mềm khác)."""
    cache = cache or hf_cache()
    if not cache.is_dir():
        return []
    text = _pkg_text()
    out = []
    for d in sorted(cache.glob("models--*")):
        repo = d.name[len("models--"):].replace("--", "/", 1)
        if re.search(re.escape(repo), text, re.I) and (d / "snapshots").is_dir():
            out.append(d)
    return out


def _size(p: Path) -> int:
    if p.is_file():
        return p.stat().st_size
    return sum(f.stat().st_size for f in p.rglob("*") if f.is_file())


def _copy_tree(src: Path, dst: Path, report) -> int:
    """Chép src -> dst (đi theo symlink = chép file thật; bỏ file tải dở / khoá). Trả số byte đã chép."""
    n = 0
    for f in src.rglob("*"):
        rel = f.relative_to(src)
        if any(part in SKIP_NAMES for part in rel.parts) or f.name.endswith(SKIP_SUFFIX) or not f.is_file():
            continue
        t = dst / rel
        if t.exists() and t.stat().st_size == f.stat().st_size:
            continue
        t.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(f, t)
        n += t.stat().st_size
        report(n)
    return n


def pack(dest_data: str | Path, progress=None) -> dict:
    """Chép mô hình vào `dest_data`/models (+ models/hf). Trả {"local": [(tên, MB)], "hf": [(kho, MB)], "bytes"}."""
    dest = Path(dest_data) / "models"
    dest.mkdir(parents=True, exist_ok=True)
    done = [0]

    def report(n):
        if progress:
            progress(f"đã chép {(done[0] + n) / 1e9:.2f} GB")

    res = {"local": [], "hf": [], "bytes": 0}
    if MODELS_DIR.is_dir():
        for d in sorted(MODELS_DIR.iterdir()):
            if d.name in SKIP_NAMES or d.name == "hf" or d.name.endswith(SKIP_SUFFIX):
                continue
            if progress:
                progress(f"Đang chép {d.name}…")
            if d.is_dir():
                n = _copy_tree(d, dest / d.name, report)
            else:
                shutil.copyfile(d, dest / d.name)
                n = d.stat().st_size
            done[0] += n
            res["local"].append((d.name, round(_size(d) / 1e6)))
    # mô hình ModelScope (SenseVoice, VAD, Paraformer, FA-zh…) tải trước 2.0.22 nằm ở %USERPROFILE%\.cache\modelscope –
    # chép vào data/models/funasr/models/<tổ chức>/<tên> (chỗ models.ms_location tìm trên máy nhận)
    from . import models as mdl

    root = MODELS_DIR.resolve()
    for mid in dict.fromkeys(mdl.MS_IDS.values()):
        d = mdl.ms_location(mid, probe=False)
        if not d or Path(d).resolve().is_relative_to(root):
            continue   # đã nằm trong data/models (vừa chép ở trên) hoặc chưa tải
        if progress:
            progress(f"Đang chép {mid}…")
        org, name = mid.split("/", 1)
        # kiểu lưu mới (<tổ chức>--<tên>/snapshots/<bản>) -> chép ruột bản đó về models/<tổ chức>/<tên> (kiểu cũ, ModelScope
        # và tool đều nhận)
        sub = name.replace(".", "___") if Path(d).parent.name == "snapshots" else Path(d).name
        n = _copy_tree(Path(d), dest / "funasr" / "models" / org / sub, report)
        done[0] += n
        res["local"].append((mid, round(_size(Path(d)) / 1e6)))
    for d in used_hf_repos():
        repo = d.name[len("models--"):].replace("--", "/", 1)
        if progress:
            progress(f"Đang chép {repo}…")
        tgt = dest / "hf" / d.name
        n = 0
        for sub in ("refs", "snapshots"):   # snapshots đi theo symlink tới blobs = file thật; không chép blobs (đỡ gấp đôi)
            if (d / sub).is_dir():
                n += _copy_tree(d / sub, tgt / sub, report)
        done[0] += n
        res["hf"].append((repo, round(_size(tgt) / 1e6)))
    res["bytes"] = _size(dest)
    return res


def main(argv: list[str]) -> int:
    if not argv:
        print("Cách dùng: TraMi.py --pack-models <thư mục data của bản mang đi>")
        return 2
    dest = Path(argv[0])
    print(f"Chép mô hình vào {dest / 'models'} …")
    res = pack(dest, lambda m: print("  " + m, end="\r", flush=True))
    print(" " * 60)
    for name, mb in res["local"]:
        print(f"  [x] {name:40s} {mb:>8} MB")
    for repo, mb in res["hf"]:
        print(f"  [x] {repo:40s} {mb:>8} MB")
    if not res["local"] and not res["hf"]:
        print("  (chưa có mô hình nào – tải ở tab Tài liệu trước)")
    print(f"Tổng: {res['bytes'] / 1e9:.2f} GB")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
