"""Kiểm tra bộ PyTorch (torch / torchvision / torchaudio) đang cài + báo lỗi nạp mô hình AI cho dễ hiểu.

2.3.1: "Không nạp được VieNeu: ModuleNotFoundError: Could not import module 'PreTrainedModel'…". transformers bản mới khi
nạp PreTrainedModel tự import torchvision (transformers.image_utils, chỉ khi có Pillow + torchvision). torchvision cài
LỆCH bản PyTorch (1 gói khác nâng / thay torch mà không đổi torchvision) -> torchvision báo "operator torchvision::nms
does not exist" -> transformers giấu lỗi đó sau thông báo trên -> VieNeu / OmniVoice / Qwen3-ASR / Hy-MT2 đều không nạp
được. Tool không cần torchvision (chỉ là mô hình âm thanh / chữ) -> torchvision hỏng thì giấu đi trong tiến trình đó
(transformers coi như chưa cài) + ghi lệnh sửa."""
from __future__ import annotations

import re
import sys

TV_BROKEN = ""   # lỗi của torchvision trong tiến trình này ("" = không hỏng / chưa kiểm)
_CHECKED = False


def versions() -> dict:
    """Bản torch / torchvision / torchaudio theo thông tin gói (không import – không tốn RAM / thời gian)."""
    from importlib import metadata

    out = {}
    for n in ("torch", "torchvision", "torchaudio"):
        try:
            out[n] = metadata.version(n)
        except Exception:  # noqa: BLE001
            pass
    return out


def _mm(v: str | None) -> tuple[int, int] | None:
    m = re.match(r"(\d+)\.(\d+)", v or "")
    return (int(m[1]), int(m[2])) if m else None


def _tag(v: str | None) -> str:
    return (v or "").split("+", 1)[1] if "+" in (v or "") else ""


def _pin(dist: str) -> str | None:
    """Bản torch mà gói này được dựng cho (thông tin gói: "Requires-Dist: torch (==2.11.0)")."""
    from importlib import metadata

    try:
        reqs = metadata.requires(dist) or []
    except Exception:  # noqa: BLE001
        return None
    for r in reqs:
        m = re.match(r"\s*torch\s*\(?\s*==\s*([\d.]+)", r)
        if m and "extra" not in r:
            return m[1]
    return None


def mismatch(vers: dict | None = None, pins: dict | None = None) -> list[str]:
    """Gói nào lệch bản PyTorch: theo bản torch ghi trong thông tin gói torchvision / torchaudio (torch==2.11.0); không
    có thì theo quy luật torch 2.N – torchvision 0.(N+15)."""
    vers = versions() if vers is None else vers
    tver = (vers.get("torch") or "").split("+", 1)[0]
    t = _mm(tver)
    if not t or t[0] != 2:
        return []
    pins = {n: _pin(n) for n in ("torchvision", "torchaudio") if vers.get(n)} if pins is None else pins
    bad = []
    for n in ("torchvision", "torchaudio"):
        if not vers.get(n):
            continue
        pin = pins.get(n)
        if pin:
            ok = _mm(pin) == t and (pin.count(".") < 2 or tver.count(".") < 2 or pin == tver)
            need = pin
        elif n == "torchvision":
            ok, need = _mm(vers[n]) == (0, t[1] + 15), ""
        else:
            continue
        if not ok:
            bad.append(f"{n} {vers[n]} " + (f"dựng cho PyTorch {need}" if need else "không khớp") +
                       f", máy đang có PyTorch {vers['torch']}")
    tt = _tag(vers.get("torch"))
    others = {_tag(vers.get(n)) for n in ("torchvision", "torchaudio") if vers.get(n)}
    if not tt and any(o.startswith("cu") for o in others):
        bad.append(f"PyTorch {vers['torch']} là bản CPU (bị gói khác thay), torchvision / torchaudio vẫn bản card")
    return bad


def _constraints() -> dict:
    """Bộ PyTorch lúc cài AI nâng cao (CaiDat_AI_NangCao.bat ghi torch-constraints.txt)."""
    try:
        from .config import APP_DIR

        out = {}
        for ln in (APP_DIR / "torch-constraints.txt").read_text("utf-8", errors="ignore").splitlines():
            m = re.match(r"\s*(torch|torchvision|torchaudio)==(\S+)", ln)
            if m:
                out[m[1]] = m[2]
        return out
    except Exception:  # noqa: BLE001
        return {}


def fix_cmd(vers: dict | None = None) -> str:
    """Lệnh cài lại cho khớp: còn file torch-constraints.txt (bộ đã cài lúc đầu) mà torch đã bị đổi -> cài lại đúng bộ
    đó; không thì cài torchvision / torchaudio đúng bản của torch đang có (cùng kho cu128 / cu126… của torch)."""
    from .config import FROZEN

    if FROZEN:
        return "bản exe: đóng gói lại trên máy (DongGoi_EXE.bat) sau khi sửa thư viện của bản Python"
    vers = versions() if vers is None else vers
    py = f'"{sys.executable}" -m pip install --force-reinstall --no-deps'
    cons = _constraints()
    if cons.get("torch") and cons["torch"] != vers.get("torch"):
        tag = _tag(cons["torch"])
        idx = f" --index-url https://download.pytorch.org/whl/{tag}" if tag else ""
        pk = " ".join(f'"{n}=={v}"' for n, v in cons.items())
        return f"{py} {pk}{idx}"
    t = _mm(vers.get("torch"))
    tag = _tag(vers.get("torch"))
    if not t or (not tag and any(_tag(vers.get(n)).startswith("cu") for n in ("torchvision", "torchaudio"))):
        return "chạy lại CaiDat_AI_NangCao.bat (cài lại PyTorch bản card)"
    idx = f" --index-url https://download.pytorch.org/whl/{tag}" if tag else ""
    pk = [f'"torchvision==0.{t[1] + 15}.*"']
    if vers.get("torchaudio"):
        pk.append(f'"torchaudio=={t[0]}.{t[1]}.*"')
    return f"{py} {' '.join(pk)}{idx}"


def guard_torchvision() -> str:
    """Gọi trước khi import transformers / thư viện AI (tiến trình đọc giọng, nghe, dịch trên máy, tách giọng…).
    torchvision import lỗi -> giấu (sys.modules = None: transformers / thư viện khác coi như chưa cài) -> các mô hình âm
    thanh / chữ vẫn nạp được. Trả lỗi của torchvision ("" = không sao)."""
    global TV_BROKEN, _CHECKED
    if _CHECKED:
        return TV_BROKEN
    _CHECKED = True
    import importlib.util

    try:
        if "torchvision" in sys.modules or importlib.util.find_spec("torchvision") is None:
            return ""
    except (ImportError, ValueError):
        return ""
    try:
        import torchvision  # noqa: F401
        return ""
    except BaseException as e:  # noqa: BLE001 – RuntimeError "operator torchvision::nms…", OSError DLL…
        if isinstance(e, (KeyboardInterrupt, SystemExit)):
            raise
        from .config import err_text

        TV_BROKEN = err_text(e)
    for k in [k for k in sys.modules if k == "torchvision" or k.startswith("torchvision.")]:
        sys.modules.pop(k, None)
    sys.modules["torchvision"] = None   # type: ignore[assignment]
    why = "; ".join(mismatch())
    print(f"[Subvideo] torchvision lỗi ({TV_BROKEN[:200]}){' – ' + why if why else ''} -> bỏ qua torchvision (tool "
          f"không cần). Sửa hẳn: {fix_cmd()}", file=sys.stderr, flush=True)
    return TV_BROKEN


def root_error(e: BaseException) -> BaseException:
    """Lỗi gốc của chuỗi lỗi (transformers giấu lỗi thật sau "Could not import module …")."""
    seen = {id(e)}
    while True:
        n = e.__cause__ or (None if e.__suppress_context__ else e.__context__)
        if n is None or id(n) in seen:
            return e
        seen.add(id(n))
        e = n


_PKG = {"cv2": "opencv-python", "PIL": "Pillow", "yaml": "pyyaml", "sklearn": "scikit-learn", "google": "protobuf",
        "soundfile": "soundfile", "sentencepiece": "sentencepiece"}


def hint(e: BaseException) -> str:
    """Cách sửa cho các lỗi nạp thư viện hay gặp."""
    root = root_error(e)
    txt = f"{type(root).__name__}: {root}"
    low = txt.lower()
    bad = mismatch()
    if "torchvision" in low or "torchaudio" in low:
        why = "; ".join(bad) or "torchvision / torchaudio không khớp bản PyTorch"
        return f"{why} – sửa: tắt tool, chạy {fix_cmd()} rồi mở lại tool"
    m = re.search(r"No module named '([\w.]+)'", txt)
    if m:
        mod = m[1]
        top = mod.split(".")[0]
        if top == "transformers":
            from importlib import metadata

            try:
                pin = "==" + metadata.version("transformers")
            except Exception:  # noqa: BLE001
                pin = ""
            return (f"thư viện transformers bị cài dở / thiếu file ({mod}) – sửa: tắt tool, chạy "
                    f'"{sys.executable}" -m pip install --force-reinstall --no-deps "transformers{pin}"')
        if top == "torch":
            return f"thư viện PyTorch bị cài dở ({mod}) – chạy lại CaiDat_AI_NangCao.bat"
        return f'thiếu gói {top} – sửa: tắt tool, chạy "{sys.executable}" -m pip install {_PKG.get(top, top)}'
    if any(k in low for k in ("c10.dll", "winerror 1114", "winerror 126", "dll load failed", "torch_cpu.dll")):
        return "thư viện PyTorch hỏng (DLL) – chạy lại CaiDat_AI_NangCao.bat"
    if bad:
        return f"{'; '.join(bad)} – nên sửa: tắt tool, chạy {fix_cmd()} rồi mở lại tool"
    return ""


def load_error(what: str, e: BaseException) -> str:
    """Thông báo "Không nạp được …" có cả lỗi gốc + cách sửa; traceback đầy đủ ghi ra log của tiến trình (stderr)."""
    import traceback

    from .config import err_text

    try:
        traceback.print_exception(type(e), e, e.__traceback__, file=sys.stderr)
    except Exception:  # noqa: BLE001
        pass
    head = err_text(e)
    m = re.search(r"Could not import module '([^']+)'", head)
    if m:   # câu của transformers dài mà không nói gì – lỗi thật ở "lỗi gốc"
        head = f"transformers không nạp được {m[1]}"
    msg = f"Không nạp được {what}: {head}"
    root = root_error(e)
    if root is not e:
        msg += f" | lỗi gốc: {err_text(root)[:400]}"
    h = hint(e)
    return msg + (f" | {h}" if h else "")
