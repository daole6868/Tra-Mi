"""Tiến trình riêng chạy VoxCPM2 (OpenBMB, Apache-2.0 – gói `voxcpm`). Cùng giao thức với vieneu_worker / omni_worker.

  vào : {"text": "...", "out": "x.wav", "ref_audio": "mau.wav" | null, "ref_text": "lời trong file mẫu" | "",
         "reference": "mau.wav" | null, "control": "mô tả giọng / kiểu đọc" | "", "seed": n | null,
         "cfg": 2.0, "steps": 10}
  ra  : @@TRAMI@@{"ok": true, "sr": 48000} | {"error": "..."} | {"progress": 0.5, "msg": "..."}
  vào {"cmd": "trim"}: trả bộ nhớ đệm card, giữ mô hình

3 cách đọc của VoxCPM2:
  - tạo giọng theo mô tả: control = "A young woman, …" (không file mẫu) – chữ đặt trong ngoặc ở đầu lời đọc
  - nhái giọng (chỉ file mẫu, không cần lời): reference = file mẫu – hợp nhái giọng nhân vật gốc nói tiếng khác
  - nhái sát nhất: ref_audio + ref_text (đọc tiếp file mẫu) + reference cùng file – giữ giọng + nhịp + cảm xúc của mẫu
"""
from __future__ import annotations

import json
import math
import os
import re
import sys
import threading
import time

from .vieneu_worker import _reply, _seed, mem_guard, reserved

DEFAULT_REPO = "openbmb/VoxCPM2"

# 2.4.17 – card 8 GB: VoxCPM2 (2B) cần ~6–8 GB card; thiếu chỗ thì Windows lấy RAM thay ("bộ nhớ GPU dùng chung") ->
# đọc chậm hàng chục lần, card bận 100 % mà dòng tiến độ đứng im ở "Đang nạp…" như treo.
KV_LEN = 4096          # bộ nhớ KV dựng sẵn (thư viện 8192 vị trí): câu lồng tiếng chỉ cần vài trăm -> bớt bộ nhớ card
MIN_SEC = 6.0          # giới hạn độ dài 1 câu: 0,5 s mỗi tiếng + 3 s, ít nhất 6 s (thư viện cho tới 4096 bước –
SEC_PER_UNIT = 0.5     # đọc lặp / không dừng là sinh hàng phút âm thanh rồi giải mã cả khối -> card càng đầy)
BEAT_EVERY = 0.5       # báo "bước i/n" về tool ≤ 2 lần / s (tool thấy còn chạy, hiện cho người dùng)
SPILL_AFTER = 20.0     # đo tốc độ sau 20 s đọc 1 câu: 1 giây âm thanh mất > 8 giây (không biết độ dài 1 bước thì
SPILL_RTF = 8.0        # < 0,5 bước / s) VÀ card còn < 4 % trống = đang tràn sang RAM -> dừng, báo rõ (không giữ card
SPILL_SPS = 0.5        # mãi). Card 4060 bình thường ~0,2–0,5 (5 s âm thanh mất 1–3 s)
SPILL_FREE = 0.04

_CJK = re.compile(r"[\u3040-\u30ff\u3400-\u9fff\uac00-\ud7af]")
_THAI = re.compile(r"[\u0e00-\u0e7f]")


class VramSpill(RuntimeError):
    """Card hết bộ nhớ, đọc gần như đứng (Windows lấy RAM thay)."""


def speech_units(text: str) -> float:
    """Số "tiếng" đọc của lời (bỏ mô tả giọng trong ngoặc ở đầu): từ cách nhau bằng khoảng trắng, chữ Hán / kana /
    Hangul mỗi chữ 1, chữ Thái 3 chữ 1 tiếng, chữ số đọc thành nhiều tiếng."""
    t = re.sub(r"^\s*\([^)]*\)", "", text or "")
    cjk = len(_CJK.findall(t))
    thai = len(_THAI.findall(t))
    rest = _CJK.sub(" ", _THAI.sub(" ", t))
    digits = sum(len(d) for d in re.findall(r"\d+", rest))
    words = len(re.findall(r"[^\W\d_]+", rest))
    return words + cjk + thai / 3 + digits * 1.5


def max_len_for(text: str, patch_sec: float | None) -> int | None:
    """Số bước sinh tối đa cho câu này (mỗi bước = 1 mẩu `patch_sec` giây âm thanh); None = để thư viện tự đặt."""
    if not patch_sec or patch_sec <= 0:
        return None
    sec = max(MIN_SEC, speech_units(text) * SEC_PER_UNIT + 3.0)
    return int(min(4096, max(30, math.ceil(sec / patch_sec))))


def patch_seconds(model) -> float | None:
    """Độ dài âm thanh của 1 bước sinh (patch_size × chunk_size mẫu ở tần số mã hoá); không đọc được -> None."""
    tm = getattr(model, "tts_model", None)
    try:
        return float(tm.patch_size) * float(tm.chunk_size) / float(tm._encode_sample_rate)
    except (AttributeError, TypeError, ValueError, ZeroDivisionError):
        return None


def vram_info() -> dict:
    """{"free", "total", "used"} MB (used = tiến trình này giữ); {} khi không chạy card."""
    torch = sys.modules.get("torch")
    try:
        if torch is None or not torch.cuda.is_available():
            return {}
        free, total = torch.cuda.mem_get_info()
    except Exception:  # noqa: BLE001
        return {}
    return {"free": int(free // 2 ** 20), "total": int(total // 2 ** 20), "used": int(reserved() // 2 ** 20)}


def shrink_kv(model, length: int = KV_LEN) -> int:
    """Dựng lại bộ nhớ KV của 2 mô hình ngôn ngữ trong VoxCPM2 với `length` vị trí (thư viện 8192). Trả số MB bớt được
    (ước lượng); mô hình khác cấu trúc -> 0, không đổi gì."""
    tm = getattr(model, "tts_model", None)
    saved = 0
    for name in ("base_lm", "residual_lm"):
        lm = getattr(tm, name, None)
        kv = getattr(lm, "kv_cache", None)
        buf = getattr(kv, "kv_cache", None)
        if buf is None or not hasattr(lm, "setup_cache") or int(getattr(kv, "max_length", 0) or 0) <= length:
            continue
        old = int(buf.numel() * buf.element_size())
        dev, dt, n = buf.device, buf.dtype, int(kv.max_length)
        try:
            lm.kv_cache = None
            del buf, kv
            lm.setup_cache(1, length, dev, dt)
            saved += old - old * length // n
        except Exception:  # noqa: BLE001 – không dựng lại được: trả về như cũ
            try:
                lm.setup_cache(1, n, dev, dt)
            except Exception:  # noqa: BLE001
                pass
    if saved:
        torch = sys.modules.get("torch")
        try:
            torch.cuda.empty_cache()
        except Exception:  # noqa: BLE001
            pass
    return saved // 2 ** 20


_KV_CAP = [KV_LEN]


def cap_kv() -> bool:
    """TRƯỚC khi nạp: bộ nhớ KV mô hình ngôn ngữ của voxcpm dựng sẵn ≤ _KV_CAP vị trí (thư viện dựng 8192 lên card
    trước cả trọng số) -> đỉnh bộ nhớ lúc nạp thấp hơn. Bản voxcpm khác cấu trúc -> False (không đổi gì)."""
    try:
        from voxcpm.modules.minicpm4.model import MiniCPMModel
    except Exception:  # noqa: BLE001
        return False
    orig = getattr(MiniCPMModel, "setup_cache", None)
    if orig is None:
        return False
    if getattr(orig, "_sv_capped", False):
        return True

    def setup_cache(self, batch_size, max_length, device, dtype):
        return orig(self, batch_size, min(int(max_length), _KV_CAP[0]), device, dtype)

    setup_cache._sv_capped = True   # type: ignore[attr-defined]
    MiniCPMModel.setup_cache = setup_cache
    return True


def kv_len(model) -> int:
    """Số vị trí bộ nhớ KV đang dựng của mô hình ngôn ngữ chính (0 = không đọc được)."""
    kv = getattr(getattr(getattr(model, "tts_model", None), "base_lm", None), "kv_cache", None)
    try:
        return int(getattr(kv, "max_length", 0) or 0)
    except (TypeError, ValueError):
        return 0


def restore_kv(model, length: int = 8192) -> None:
    """Câu quá dài cho bộ nhớ KV đã thu nhỏ: dựng lại cỡ gốc của thư viện (bỏ giới hạn)."""
    _KV_CAP[0] = 1 << 30
    for name in ("base_lm", "residual_lm"):
        lm = getattr(getattr(model, "tts_model", None), name, None)
        buf = getattr(getattr(lm, "kv_cache", None), "kv_cache", None)
        if buf is not None and hasattr(lm, "setup_cache"):
            dev, dt = buf.device, buf.dtype
            lm.kv_cache = None
            del buf
            lm.setup_cache(1, length, dev, dt)


class Steps:
    """Thay `tqdm` trong vòng sinh của voxcpm: báo "bước i/n" về tool + phát hiện card tràn (đọc gần như đứng)."""

    def __init__(self, send, free_frac=None, clock=time.time, patch_sec: float | None = None):
        self.send = send
        self.free_frac = free_frac or _free_frac
        self.clock = clock
        self.patch_sec = patch_sec

    def too_slow(self, steps: int, el: float) -> bool:
        if self.patch_sec:
            return el > SPILL_RTF * steps * self.patch_sec
        return steps / el < SPILL_SPS

    def __call__(self, it, *_a, **_k):
        try:
            n = len(it)
        except TypeError:
            n = 0
        t0 = last = self.clock()
        for i, x in enumerate(it):
            yield x
            now = self.clock()
            if now - last < BEAT_EVERY:
                continue
            last = now
            el = now - t0
            self.send({"beat": {"i": i + 1, "n": n, "t": round(el, 1)}})
            if el >= SPILL_AFTER and self.too_slow(i + 1, el):
                fr = self.free_frac()
                if fr is not None and fr < SPILL_FREE:
                    info = vram_info()
                    speed = (f"1 giây giọng mất {el / ((i + 1) * self.patch_sec):.0f} giây" if self.patch_sec
                             else f"{(i + 1) / el:.2f} bước / s")
                    raise VramSpill(
                        f"VoxCPM2 đọc gần như đứng ({speed}) vì card đồ hoạ hết bộ nhớ (còn "
                        f"{info.get('free', 0)} MB / {info.get('total', 0) / 1024:.1f} GB, VoxCPM2 giữ "
                        f"{info.get('used', 0) / 1024:.1f} GB) – Windows đang lấy RAM thay nên gần như không chạy. "
                        "VoxCPM2 cần ~6–7 GB card trống: tắt chương trình đang dùng card (trang Việc đang làm → Xem ai "
                        "đang chiếm card), rồi bấm lại; hoặc chọn bộ đọc nhẹ hơn (VieNeu, OmniVoice, KorvaTTS)")


def _free_frac() -> float | None:
    torch = sys.modules.get("torch")
    try:
        if torch is None or not torch.cuda.is_available():
            return None
        free, total = torch.cuda.mem_get_info()
        return free / max(total, 1)
    except Exception:  # noqa: BLE001
        return None


def hook_steps(steps: Steps) -> int:
    """Gắn `steps` vào chỗ voxcpm gọi tqdm trong vòng sinh (mọi module mô hình đã nạp). Trả số module đã gắn."""
    n = 0
    for name in ("voxcpm.model.voxcpm2", "voxcpm.model.voxcpm"):
        mod = sys.modules.get(name)
        if mod is not None and hasattr(mod, "tqdm"):
            mod.tqdm = steps
            n += 1
    return n


LOAD_BEAT = 5.0         # đang nạp mà thư viện không báo gì 5 s -> báo "đã N s · card đã nhận x GB" (không đứng im như treo)
LOAD_BEAT_MAX = 900.0   # quá 15 phút thì thôi báo: tool tự coi là treo (stall_start) như cũ


def _load_beat(send, last_msg: list, done: threading.Event, every: float = LOAD_BEAT,
               until: float = LOAD_BEAT_MAX) -> None:
    t0 = time.time()
    while not done.wait(every):
        now = time.time()
        if now - t0 > until:
            return
        if now - last_msg[0] < every:
            continue
        used = reserved() / 2 ** 30
        send({"progress": last_msg[1], "msg": f"Đang nạp VoxCPM2 – đã {now - t0:.0f} s"
              + (f" · card đã nhận {used:.1f} GB" if used >= 0.05 else "")})


def _kv_full(e: Exception) -> bool:
    m = str(e).lower()
    return "kv cache is full" in m or ("must match the size" in m and "dimension" in m)


def make_kwargs(req: dict) -> tuple[str, dict]:
    """(lời đọc, tham số generate) từ 1 yêu cầu – thuần, test gọi thẳng."""
    text = str(req.get("text") or "").strip()
    control = str(req.get("control") or "").strip()
    if control:
        text = f"({control}){text}"
    kw: dict = {"cfg_value": float(req.get("cfg") or 2.0), "inference_timesteps": int(req.get("steps") or 10)}
    ref, rtext = req.get("ref_audio"), str(req.get("ref_text") or "").strip()
    if ref and rtext and not control:   # đọc tiếp file mẫu (cả file lẫn lời); có chỉ dẫn thì không (voxcpm không nhận)
        kw["prompt_wav_path"] = ref
        kw["prompt_text"] = rtext
    reference = req.get("reference") or (ref if ref else None)
    if reference:
        kw["reference_wav_path"] = reference
    return text, kw


def main(repo: str = DEFAULT_REPO) -> None:
    proto = sys.stdout
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w", encoding="utf-8")
    sys.stdout = sys.stderr
    try:
        proto.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
        sys.stdin.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:
        pass

    lock = threading.Lock()
    last_msg = [time.time(), 0.0]

    def send(obj: dict):
        with lock:              # luồng nhịp lúc nạp + luồng chính cùng ghi
            _reply(proto, obj)

    def prog(x: float, msg: str):
        last_msg[0], last_msg[1] = time.time(), x
        send({"progress": round(x, 4), "msg": msg})

    from .torchenv import guard_torchvision, load_error

    guard_torchvision()
    try:
        import numpy as np
        from voxcpm import VoxCPM

        from .hfoffline import load_local_first
        from .progress_hook import tqdm_progress
        from .tts import write_wav

        def stalled(st, idle):
            send({"error": f"Không tải được VoxCPM2 từ HuggingFace: {idle / 60:.0f} phút không nhận thêm dữ "
                                    f"liệu (đã tải {st['bytes'] / 1e6:.0f}/{st['total'] / 1e6:.0f} MB) – tải ở tab Tài "
                                    "liệu (tự chuyển ModelScope khi HuggingFace bị chặn) rồi bấm lại."})
            os._exit(3)

        # optimize=False: torch.compile cần triton (Windows không có) + nạp lâu; load_denoiser=False: bộ khử ồn tải
        # thêm từ ModelScope, tool tự lọc file mẫu
        cap_kv()
        loaded = threading.Event()
        threading.Thread(target=_load_beat, args=(send, last_msg, loaded), daemon=True).start()
        try:
            with tqdm_progress(prog, 0.0, 1.0, "Đang nạp VoxCPM2", download="Đang tải VoxCPM2"):
                model = load_local_first(lambda: VoxCPM.from_pretrained(repo, load_denoiser=False, optimize=False),
                                         (repo.split("/")[-1], "voxcpm"), "VoxCPM2", report=prog, on_stall=stalled)
        finally:
            loaded.set()
        sr = int(getattr(getattr(model, "tts_model", None), "sample_rate", None) or 48000)
    except Exception as e:  # noqa: BLE001
        send({"error": load_error("VoxCPM2", e)})
        return
    shrink_kv(model)               # bản voxcpm không chặn được lúc nạp: thu nhỏ sau khi nạp
    kvn = kv_len(model)
    psec = patch_seconds(model)
    hook_steps(Steps(send, patch_sec=psec))
    info = vram_info()
    print(f"[Subvideo] VoxCPM2 đã nạp: card {info or 'không dùng'}, bộ nhớ KV {kvn} vị trí, 1 bước = {psec} s",
          file=sys.stderr, flush=True)
    send({"ready": True, "sr": sr, "vram": info, "kv_len": kvn, "patch_sec": psec})
    base = reserved()
    kv_small = 0 < kvn < 8192

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
            if req.get("cmd") == "trim":
                send(dict({"ok": True}, **mem_guard(base, None, True, "VoxCPM2")))
                continue
            text, kw = make_kwargs(req)
            ml = max_len_for(text, psec)
            if ml:
                kw["max_len"] = ml
            if req.get("seed") is not None:
                _seed(int(req["seed"]))
            try:
                wav = model.generate(text=text, **kw)
            except (ValueError, RuntimeError) as e:
                if not (kv_small and _kv_full(e)):
                    raise
                restore_kv(model)          # lời / file mẫu quá dài cho bộ nhớ KV đã thu nhỏ: dựng lại cỡ gốc, đọc lại
                try:
                    wav = model.generate(text=text, **kw)
                finally:                   # 2.4.20: đọc xong câu đó -> thu nhỏ lại (trước đây giữ cỡ 8192 tới khi tắt)
                    _KV_CAP[0] = KV_LEN
                    shrink_kv(model)
            write_wav(req["out"], np.asarray(wav, dtype=np.float32).reshape(-1), sr)
            send(dict({"ok": True, "sr": sr}, **mem_guard(base, None, False, "VoxCPM2")))
        except VramSpill as e:
            send({"error": str(e), "vram": True})
        except Exception as e:  # noqa: BLE001
            send({"error": f"{type(e).__name__}: {e}"})
