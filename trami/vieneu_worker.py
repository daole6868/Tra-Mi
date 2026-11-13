"""Tiến trình riêng chạy VieNeu-TTS.

Chạy VieNeu trong cùng tiến trình với giao diện dễ đụng độ GPU (trình phát video giải mã bằng GPU, CUDA graph của
VieNeu…) và nếu thư viện GPU sập thì cả cửa sổ TraMi tắt theo. Ở tiến trình riêng: VieNeu có CUDA context riêng,
sập thì TraMi chỉ báo lỗi và khởi động lại.

Giao thức (mỗi dòng 1 JSON, UTF-8):
  vào : {"text": "...", "out": "đường/dẫn.wav", "voice": "Tên giọng" | null, "ref_audio": "mẫu.wav" | null,
         "params": {"temperature": 0.6, ...} (tuỳ chọn, kiểu đọc), "seed": số | null (đọc lại khi câu trước hỏng)}
  ra  : @@TRAMI@@{"ok": true, "sr": 48000}   hoặc   @@TRAMI@@{"error": "..."}
  vào {"cmd": "trim"} (2.4.8, sau mỗi việc lồng tiếng): bỏ đồ thị CUDA + bộ nhớ đệm, giữ mô hình -> {"ok": true, …}
Dòng đầu tiên sau khi nạp mô hình: @@TRAMI@@{"ready": true}
"""
from __future__ import annotations

import json
import os
import sys

MARK = "@@TRAMI@@"
_INFER_KEYS = {"temperature", "top_k", "top_p", "repetition_penalty"}
LOW_FREE = 0.15        # card còn trống < 15 % -> bỏ các đồ thị CUDA VieNeu đã chụp


def _small_card() -> bool:
    """Card < 11 GB (RTX 4060 laptop 8 GB…): mỗi câu đọc từng đoạn một (batch 1) -> VieNeu chỉ chụp đồ thị CUDA cỡ nhỏ.
    VieNeu gộp các đoạn của 1 câu vào cùng lô (1, 2, 4, 8, 16) và giữ MÃI 1 đồ thị + bộ nhớ KV riêng cho mỗi (cỡ lô,
    độ dài, kiểu đọc) đã gặp -> đọc vài trăm câu là card đầy, Windows lấy RAM thay -> chậm như treo, GPU vẫn 98 %."""
    torch = sys.modules.get("torch")
    try:
        return bool(torch and torch.cuda.is_available()
                    and torch.cuda.get_device_properties(0).total_memory < 11 * 1024 ** 3)
    except Exception:  # noqa: BLE001
        return False


GRAPH_BUDGET_SMALL = 1.5 * 1024 ** 3   # 2.4.8: card < 11 GB – đồ thị CUDA + bộ nhớ đệm giữ thêm tối đa 1,5 GB ngoài
GRAPH_BUDGET = 4 * 1024 ** 3           # mô hình (trước chỉ dọn khi CẢ card còn < 15 % -> 8 GB bị chiếm tới ~6,8 GB)


def reserved() -> int:
    """Bộ nhớ card tiến trình này đang giữ (mô hình + đồ thị CUDA + bộ nhớ đệm của PyTorch); 0 = không đo được."""
    torch = sys.modules.get("torch")
    try:
        return int(torch.cuda.memory_reserved()) if torch is not None and torch.cuda.is_available() else 0
    except Exception:  # noqa: BLE001
        return 0


def _drop_graphs(tts) -> int:
    eng = getattr(tts, "_batch_engine", None)
    n = 0
    for name in ("_fused", "_graphs"):
        d = getattr(eng, name, None)
        if isinstance(d, dict):
            n += len(d)
            d.clear()
    return n


def mem_guard(base: int = 0, drop=None, force: bool = False, label: str = "") -> dict:
    """Sau mỗi câu / lệnh "trim": CẢ card còn trống < LOW_FREE, hoặc tiến trình này giữ thêm > GRAPH_BUDGET ngoài mô
    hình (base = bộ nhớ lúc vừa nạp xong), hoặc force -> drop() (bỏ đồ thị CUDA / Whisper…) + trả bộ nhớ đệm."""
    torch = sys.modules.get("torch")
    try:
        if torch is None or not torch.cuda.is_available():
            return {}
        free, total = torch.cuda.mem_get_info()
    except Exception:  # noqa: BLE001
        return {}
    out = {"vram_free": round(free / max(total, 1), 3)}
    extra = reserved() - base if base else 0
    budget = GRAPH_BUDGET_SMALL if total < 11 * 1024 ** 3 else GRAPH_BUDGET
    if not force and free / max(total, 1) >= LOW_FREE and extra <= budget:
        return out
    n = drop() if drop else 0
    import gc

    gc.collect()
    try:
        torch.cuda.synchronize()
        torch.cuda.empty_cache()
        free, total = torch.cuda.mem_get_info()
        out["vram_free"] = round(free / max(total, 1), 3)
    except Exception:  # noqa: BLE001
        pass
    out["freed_graphs"] = n
    why = "trả bộ nhớ sau khi lồng tiếng" if force else (
        "card gần đầy" if free / max(total, 1) < LOW_FREE else f"giữ thêm {extra / 1024 ** 3:.1f} GB ngoài mô hình")
    print(f"[Subvideo] {label or 'VieNeu'}: {why} – bỏ {n} mục đã chụp / nạp tạm, card còn trống {out['vram_free']:.0%}",
          file=sys.stderr, flush=True)
    return out


def _vram_guard(tts, base: int = 0, force: bool = False) -> dict:
    """VieNeu: bỏ các đồ thị CUDA đã chụp (VieNeu chụp lại khi cần, ~0,5 s) + trả bộ nhớ đệm khi card gần đầy / giữ quá
    nhiều / được yêu cầu (lệnh "trim" sau mỗi việc lồng tiếng – mô hình vẫn nạp sẵn cho lần tạo lại giọng sau)."""
    return mem_guard(base, lambda: _drop_graphs(tts), force, "VieNeu")


def _seed(n: int) -> None:
    """Đổi hạt ngẫu nhiên (đọc lại câu bị hỏng sẽ ra kết quả khác)."""
    import random

    random.seed(n)
    try:
        import numpy as np

        np.random.seed(n % (2 ** 32))
    except Exception:  # noqa: BLE001
        pass
    try:
        import torch

        torch.manual_seed(n)
    except Exception:  # noqa: BLE001
        pass


def short_caps(caps=None) -> int:
    """Hạ trần số khung của VieNeu v3 Turbo cho đoạn ≤ 4 tiếng xuống sát độ dài đọc thật (`tts.VN_SHORT_CAP`).
    Thư viện cho 13 khung + 5 mỗi tiếng thêm (gấp ≥ 1,5 lần đọc thật) – câu ngắn trượt chỗ dừng là đọc lặp lời tới đúng
    trần đó ("được thôi được thôi"). Trần thấp -> không đủ chỗ đọc lặp; lớp chặn "bịa thêm" của thư viện cũng dùng trần
    này nên tự đọc lại khi chạm trần. Đoạn có tiếng cười / thở dài (emotion cue) và đoạn dài giữ trần cũ.
    Trả về số module đã thay hàm (0 = bản VieNeu không có hàm này)."""
    try:
        import vieneu_utils.core_utils as cu
    except Exception:  # noqa: BLE001
        return 0
    if caps is None:
        from .tts import VN_SHORT_CAP as caps
    orig = getattr(cu, "max_expected_frames", None)
    if orig is None or not callable(getattr(cu, "syllable_count", None)):
        return 0
    orig = getattr(orig, "_trami_orig", orig)

    def capped(phonemes):
        cap = orig(phonemes)
        try:
            if "<|emotion_" in (phonemes or ""):
                return cap
            syl = int(cu.syllable_count(phonemes))
        except Exception:  # noqa: BLE001
            return cap
        return min(cap, caps[syl]) if 1 <= syl < len(caps) else cap
    capped._trami_orig = orig  # type: ignore[attr-defined]
    n = 0
    for name, mod in list(sys.modules.items()):   # cả các module đã "from … import max_expected_frames"
        if (name == "vieneu_utils.core_utils" or name.startswith("vieneu")) and mod is not None:
            cur = getattr(mod, "max_expected_frames", None)
            if cur is not None and getattr(cur, "_trami_orig", cur) is orig:
                setattr(mod, "max_expected_frames", capped)
                n += 1
    return n


def _reply(out, obj: dict) -> None:
    out.write(MARK + json.dumps(obj, ensure_ascii=False) + "\n")
    out.flush()


DEVICE_KW = {"cpu_int8": {"backend": "onnx", "precision": "int8"},   # 2.4.16: VieNeu v3 Turbo chạy ONNX trên CPU
             "cpu": {"backend": "onnx", "precision": "fp32"}}           # (không chiếm card; int8 cần CPU có VNNI)


def make_kwargs(mode: str, device: str = "") -> dict:
    """Tham số tạo VieNeu theo nơi chạy. v3 Nano vốn chỉ chạy CPU -> không thêm gì."""
    return dict(DEVICE_KW.get(device or "", {})) if mode == "v3turbo" else {}


def main(mode: str = "v3turbo", device: str = "") -> None:
    # stdout dành cho giao thức: chuyển mọi print/log của thư viện sang stderr (hiện trong cửa sổ đen)
    proto = sys.stdout
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w", encoding="utf-8")
    sys.stdout = sys.stderr
    try:
        proto.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
        sys.stdin.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:
        pass
    from .torchenv import guard_torchvision, load_error

    guard_torchvision()   # torchvision lệch PyTorch -> transformers không nạp được PreTrainedModel (2.3.1)
    try:
        from vieneu import Vieneu

        from .progress_hook import tqdm_progress
        from .tts import write_wav

        def prog(x: float, msg: str):
            _reply(proto, {"progress": round(x, 4), "msg": msg})

        from .hfoffline import load_local_first

        def stalled(st, idle):   # tải từ HuggingFace đứng im: báo lỗi rõ rồi tắt tiến trình (luồng tải kẹt không huỷ được)
            _reply(proto, {"error": f"Không tải được VieNeu-TTS từ HuggingFace: {idle / 60:.0f} phút không nhận thêm dữ "
                                    f"liệu (đã tải {st['bytes'] / 1e6:.0f}/{st['total'] / 1e6:.0f} MB) – mạng đang chặn "
                                    "máy chủ tải file lớn của HuggingFace. Thử mạng khác / VPN rồi bấm lại."})
            os._exit(3)

        # 2.3.3: nạp bản ĐÃ CÓ trên máy trước (không hỏi HuggingFace) – kho VieNeu có bản mới thì thư viện tự tải lại
        # ~400 MB mỗi lần mở, mạng chặn -> đứng "0/415319040" mãi
        kw = make_kwargs(mode, device)

        def make():
            if not kw:
                return Vieneu(mode=mode)
            try:
                return Vieneu(mode=mode, **kw)
            except TypeError:     # bản VieNeu cũ chưa có đường ONNX / CPU
                raise RuntimeError("Bản VieNeu đang cài chưa chạy được bằng CPU (cần vieneu ≥ 3.8) – cập nhật VieNeu "
                                   "hoặc chọn 'Card đồ hoạ' ở Cài đặt → VieNeu chạy trên") from None

        what = "VieNeu-TTS" + (" (CPU)" if kw else "")
        with tqdm_progress(prog, 0.0, 1.0, f"Đang nạp {what}", download=f"Đang tải {what}"):
            tts = load_local_first(make, ("vieneu", "moss-audio", "neucodec"), "VieNeu-TTS",
                                   report=prog, on_stall=stalled)
        sr = int(getattr(tts, "sample_rate", 48000))
    except Exception as e:  # noqa: BLE001
        _reply(proto, {"error": load_error("VieNeu", e)})
        return
    if mode != "v3nano" and short_caps():
        print("[Subvideo] VieNeu: câu ≤ 4 tiếng dùng trần số khung thấp (chống đọc lặp lời)", file=sys.stderr, flush=True)
    if kw:
        print(f"[Subvideo] VieNeu chạy trên CPU ({kw.get('precision')}) – không dùng card đồ hoạ", file=sys.stderr,
              flush=True)
    _reply(proto, {"ready": True, "sr": sr, "device": "cpu" if kw or mode == "v3nano" else "auto"})
    one_chunk = {"batch_size": 1} if _small_card() and not kw else {}
    base = reserved()     # bộ nhớ card của riêng mô hình (lúc vừa nạp)
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
            if req.get("cmd") == "trim":    # việc lồng tiếng xong: trả phần đồ thị / bộ nhớ đệm, giữ mô hình
                _reply(proto, dict({"ok": True}, **_vram_guard(tts, base, force=True)))
                continue
            kw = {}
            if req.get("ref_audio"):
                kw["ref_audio"] = req["ref_audio"]
            elif req.get("voice"):
                kw["voice"] = req["voice"]
            if req.get("seed") is not None:
                _seed(int(req["seed"]))
            prm = {k: v for k, v in (req.get("params") or {}).items() if k in _INFER_KEYS}
            tries = [dict(prm, **one_chunk), prm, {}] if one_chunk else [prm, {}]
            for k, extra in enumerate(tries):
                try:
                    wav = tts.infer(req["text"], **kw, **extra)
                    break
                except TypeError:   # bản VieNeu cũ không nhận batch_size / thông số lấy mẫu
                    if k == len(tries) - 1:
                        raise
                    one_chunk = {}
            write_wav(req["out"], wav, sr)
            _reply(proto, dict({"ok": True, "sr": sr}, **_vram_guard(tts, base)))
        except Exception as e:  # noqa: BLE001
            _reply(proto, {"error": f"{type(e).__name__}: {e}"})


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "v3turbo", sys.argv[2] if len(sys.argv) > 2 else "")
