"""Tách âm thanh PHIM thành 3 phần: LỜI THOẠI / NHẠC / TIẾNG ĐỘNG bằng Bandit v2 (2.1.15).

Mô hình: Watcharasupat, Wu, Orife – "Remastering Divide and Remaster: A Cinematic Audio Source Separation Dataset with
Multilingual Support" (2024). Mã suy luận dưới đây viết lại gọn từ github.com/kwatcharasupat/bandit-v2 (Apache-2.0):
chỉ phần chạy mô hình, không cần torchaudio / librosa / pytorch-lightning; tên các lớp con giữ đúng như bản gốc để nạp
được trọng số chính hãng. Trọng số: Zenodo 12701995 (CC BY-SA 4.0 – dùng được cả cho video kiếm tiền, ghi công tác giả),
bản đa ngôn ngữ `checkpoint-multi.ckpt` (học trên âm thanh phim hơn 30 thứ tiếng, có tiếng Trung).

Vì sao: các mô hình tách khác của tool học trên BÀI HÁT (giọng hát / nhạc đệm) – tiếng hét, tiếng động, tiếng thở có khi
bị coi là giọng; Bandit học trên âm thanh phim nên lời thoại tách riêng khỏi cả nhạc lẫn tiếng động. Điểm tách lời thoại
trên bộ thử DnR v3 (SDR): 12,29. Tool dùng: lời thoại -> vocals.wav (nghe, bám mép câu, lồng tiếng), nhạc + tiếng động ->
instrumental.wav (nhạc nền khi lồng tiếng).

Chạy: mô hình nhận 1 kênh, 48 kHz -> mỗi kênh trái / phải tách riêng; cửa sổ 8 s trượt `HOP_SEC` 2 s, ghép chồng bằng
cửa sổ Hann (mỗi mẫu được nghe 4 lần ở 4 vị trí khác nhau trong cửa sổ).
"""
from __future__ import annotations

import hashlib
import pickle
import time
from pathlib import Path
from typing import Callable

import numpy as np

from .config import MODELS_DIR

Prog = Callable[[float, str], None]

NAME = "bandit_v2_multi"                     # mã mô hình trong Cài đặt `sep_model`
SR = 48000
STEMS = ("speech", "music", "sfx")
CHUNK_SEC = 8.0                              # cửa sổ mô hình học (8 s)
HOP_SEC = 2.0                                # bản gốc trượt 1 s (8 lần mỗi mẫu – rất chậm); 2 s = 4 lần, gần như bằng
BLOCK_SEC = 300.0                            # phim dài: tách từng đoạn 5 phút (RAM ~0,5 GB dù phim dài bao nhiêu)
WEIGHTS = MODELS_DIR / "separator" / f"{NAME}.pt"    # trọng số đã lọc (chỉ các tensor của mô hình, ~150 MB)
RAW_NAME = "checkpoint-multi.ckpt"           # file gốc trên Zenodo (kèm trạng thái huấn luyện, 447 MB)
RAW_SIZE = 446680129
RAW_MD5 = "fea2868787551b0cff36cfcf7c3622a3"
ZENODO_RECORD = "12701995"
URLS = (f"https://zenodo.org/records/{ZENODO_RECORD}/files/{RAW_NAME}?download=1",
        # bản chép y hệt (kiểm tra MD5 trước khi dùng) khi Zenodo chậm / bị chặn
        "https://github.com/jontechlabs/ai-dubbing-studio/releases/download/bandit-v2-multilingual-12701995/"
        + RAW_NAME)
CONFIG = dict(in_channels=1, stems=list(STEMS), n_bands=64, n_sqm_modules=8, emb_dim=128, rnn_dim=256,
              rnn_type="GRU", mlp_dim=512, n_fft=2048, hop_length=512, fs=SR)


def available() -> bool:
    from .config import installed

    return installed("torch")


def ready() -> bool:
    return WEIGHTS.is_file() and WEIGHTS.stat().st_size > 50_000_000


# ------------------------------------------------------------------ dải tần "theo âm nhạc" (64 dải đều theo nửa cung)

def _hz_to_midi(f):
    return 12.0 * (np.log2(np.asarray(f, dtype=np.float64)) - np.log2(440.0)) + 69.0


def _midi_to_hz(m):
    return 440.0 * 2.0 ** ((np.asarray(m, dtype=np.float64) - 69.0) / 12.0)


def musical_filterbank(n_bands: int, fs: int, n_freqs: int) -> np.ndarray:
    """Như `musical_filterbank` của bản gốc: n_bands dải đều theo cao độ (midi) từ 1 vạch tần tới Nyquist, mỗi dải
    rộng thêm 1 khoảng ở 2 bên (chồng nhau)."""
    nfft = 2 * (n_freqs - 1)
    df = fs / nfft
    f_max, f_min = fs / 2, fs / nfft
    mult = 2.0 ** (np.log2(f_max / f_min) / n_bands)
    hz = _midi_to_hz(np.linspace(max(0.0, float(_hz_to_midi(f_min))), float(_hz_to_midi(f_max)), n_bands))
    low = np.floor(hz / mult / df).astype(int)
    high = np.ceil(hz * mult / df).astype(int)
    fb = np.zeros((n_bands, n_freqs))
    for i in range(n_bands):
        fb[i, low[i]:high[i] + 1] = 1.0
    fb[0, :low[0]] = 1.0
    fb[-1, high[-1] + 1:] = 1.0
    return fb


def band_specs(n_bands: int, fs: int, n_fft: int) -> tuple[list[tuple[int, int]], list[np.ndarray]]:
    """[(vạch đầu, vạch cuối + 1)] mỗi dải + trọng số từng vạch (vạch thuộc nhiều dải chia đều)."""
    fb = musical_filterbank(n_bands, fs, n_fft // 2 + 1)
    norm = fb / fb.sum(axis=0, keepdims=True)
    specs, weights = [], []
    for i in range(n_bands):
        act = np.nonzero(fb[i])[0]
        if not len(act):
            continue
        a, b = int(act[0]), int(act[-1]) + 1
        specs.append((a, b))
        weights.append(norm[i, a:b])
    return specs, weights


# ------------------------------------------------------------------ mô hình (tên lớp con như bản gốc)

def build_model():
    """Bandit v2 (GRU, 64 dải, 8 khối) – cấu hình bản đa ngôn ngữ của tác giả."""
    import torch
    from torch import nn

    c = CONFIG
    specs, fweights = band_specs(c["n_bands"], c["fs"], c["n_fft"])
    n_freq = c["n_fft"] // 2 + 1

    class Spec(nn.Module):
        """= torchaudio Spectrogram(normalized=True, power=None, center=True, pad_mode="reflect")."""

        def __init__(self):
            super().__init__()
            self.register_buffer("window", torch.hann_window(c["n_fft"]))

        def forward(self, x):
            shape = x.shape
            X = torch.stft(x.reshape(-1, shape[-1]), c["n_fft"], c["hop_length"], c["n_fft"], self.window,
                           center=True, pad_mode="reflect", normalized=False, onesided=True, return_complex=True)
            X = X / self.window.pow(2).sum().sqrt()
            return X.reshape(shape[:-1] + X.shape[-2:])

    class ISpec(nn.Module):
        def __init__(self):
            super().__init__()
            self.register_buffer("window", torch.hann_window(c["n_fft"]))

        def forward(self, X, length: int):
            shape = X.shape
            X = X.reshape((-1,) + shape[-2:]) * self.window.pow(2).sum().sqrt()
            y = torch.istft(X, c["n_fft"], c["hop_length"], c["n_fft"], self.window, center=True, normalized=False,
                            onesided=True, length=length)
            return y.reshape(shape[:-2] + (length,))

    class NormFC(nn.Module):
        def __init__(self, bw: int):
            super().__init__()
            self.combined = nn.Sequential(nn.LayerNorm(c["in_channels"] * bw * 2),
                                          nn.Linear(bw * 2 * c["in_channels"], c["emb_dim"]))

        def forward(self, xb):
            return self.combined(xb)

    class BandSplitModule(nn.Module):
        def __init__(self):
            super().__init__()
            self.norm_fc_modules = nn.ModuleList([NormFC(b - a) for a, b in specs])

        def forward(self, x):   # x: (lô, kênh, tần số, thời gian) phức
            batch, _ch, _f, n_time = x.shape
            z = torch.zeros((batch, len(specs), n_time, c["emb_dim"]), device=x.device)
            x = torch.permute(x, (0, 3, 1, 2)).contiguous()
            for i, nfm in enumerate(self.norm_fc_modules):
                a, b = specs[i]
                xb = torch.view_as_real(x[:, :, :, a:b]).reshape(batch, n_time, -1)
                z[:, i, :, :] = nfm(xb)
            return z

    class ResidualRNN(nn.Module):
        def __init__(self):
            super().__init__()
            self.norm = nn.LayerNorm(c["emb_dim"])
            self.rnn = nn.GRU(input_size=c["emb_dim"], hidden_size=c["rnn_dim"], num_layers=1, batch_first=True,
                              bidirectional=True)
            self.fc = nn.Linear(c["rnn_dim"] * 2, c["emb_dim"])

        def forward(self, z):   # (lô, nhóm, dãy, emb): RNN chạy dọc chiều "dãy"
            z0 = z
            b, n_unc, n_acr, e = z.shape
            y = self.rnn(self.norm(z).reshape(b * n_unc, n_acr, e))[0]
            return self.fc(y.reshape(b, n_unc, n_acr, -1)) + z0

    class Transpose(nn.Module):
        def forward(self, z):
            return z.transpose(1, 2)

    class SeqBandModellingModule(nn.Module):
        def __init__(self):
            super().__init__()
            layers: list = []
            for _ in range(2 * c["n_sqm_modules"]):
                layers += [ResidualRNN(), Transpose()]
            self.seqband = nn.Sequential(*layers)

        def forward(self, z):
            return self.seqband(z)

    class NormMLP(nn.Module):
        def __init__(self, bw: int):
            super().__init__()
            self.bw = bw
            self.norm = nn.LayerNorm(c["emb_dim"])
            self.hidden = nn.Sequential(nn.Linear(c["emb_dim"], c["mlp_dim"]), nn.Tanh())
            self.output = nn.Sequential(nn.Linear(c["mlp_dim"], bw * c["in_channels"] * 2 * 2), nn.GLU(dim=-1))
            self.combined = nn.Sequential(self.norm, self.hidden, self.output)   # như bản gốc (cùng tham số)

        def forward(self, qb):
            mb = self.combined(qb).float()      # nửa độ chính xác không có số phức -> về float32
            batch, n_time, _ = mb.shape
            mb = torch.view_as_complex(mb.reshape(batch, n_time, c["in_channels"], self.bw, 2).contiguous())
            return torch.permute(mb, (0, 2, 3, 1))   # (lô, kênh, độ rộng dải, thời gian)

    class OverlappingMaskEstimationModule(nn.Module):
        def __init__(self):
            super().__init__()
            self.norm_mlp = nn.ModuleList([NormMLP(b - a) for a, b in specs])
            for i, fw in enumerate(fweights):
                self.register_buffer(f"freq_weights/{i}", torch.as_tensor(fw, dtype=torch.float32))

        def forward(self, q):
            batch, _nb, n_time, _e = q.shape
            masks = torch.zeros((batch, c["in_channels"], n_freq, n_time), device=q.device, dtype=torch.complex64)
            for i, (a, b) in enumerate(specs):
                m = self.norm_mlp[i](q[:, i, :, :]) * self.get_buffer(f"freq_weights/{i}").float()[:, None]
                masks[:, :, a:b, :] += m
            return masks

    class Bandit(nn.Module):
        def __init__(self):
            super().__init__()
            self.stems = list(c["stems"])
            self.stft = Spec()
            self.istft = ISpec()
            self.band_split = BandSplitModule()
            self.tf_model = SeqBandModellingModule()
            self.mask_estim = nn.ModuleDict({s: OverlappingMaskEstimationModule() for s in self.stems})

        def forward(self, x, amp_dtype=None):
            """x: (lô, 1, mẫu) 48 kHz -> (lô, số phần, 1, mẫu)."""
            n = x.shape[-1]
            X = self.stft(x.float())
            dev = "cuda" if X.is_cuda else "cpu"
            with torch.autocast(dev, dtype=amp_dtype or torch.float32, enabled=amp_dtype is not None):
                q = self.tf_model(self.band_split(X))
                masks = [self.mask_estim[s](q) for s in self.stems]
            return torch.stack([self.istft(X * m.to(X.dtype), n) for m in masks], dim=1)

    return Bandit()


# ------------------------------------------------------------------ nạp trọng số an toàn

class _Dummy:
    """Thay cho mọi lớp lạ trong file checkpoint (trạng thái huấn luyện, cấu hình…) – không chạy mã nào."""

    def __init__(self, *a, **kw):
        pass

    def __setstate__(self, state):
        pass

    def __call__(self, *a, **kw):
        return _Dummy()


_SAFE = {("collections", "OrderedDict"), ("torch._utils", "_rebuild_tensor_v2"), ("torch._utils", "_rebuild_tensor"),
         ("torch._utils", "_rebuild_parameter"), ("torch", "Size"), ("torch", "device"),
         ("builtins", "set"), ("builtins", "frozenset"), ("builtins", "slice"), ("builtins", "complex")}


class _SafeUnpickler(pickle.Unpickler):
    def find_class(self, module, name):
        if (module, name) in _SAFE or (module == "torch" and name.endswith("Tensor")) or \
                (module == "torch" and name in ("float32", "float64", "float16", "bfloat16", "int64", "int32", "bool")):
            return super().find_class(module, name)
        return _Dummy


class _SafePickle:
    """Mô-đun "pickle" chỉ dựng được tensor / dict – cho torch.load khi file có thêm trạng thái huấn luyện mà
    weights_only=True từ chối (checkpoint PyTorch Lightning)."""
    Unpickler = _SafeUnpickler
    load = staticmethod(lambda f, **kw: _SafeUnpickler(f, **kw).load())


def load_checkpoint(path: str | Path) -> dict:
    """{tên tham số: tensor} của mô hình từ file gốc Zenodo (checkpoint Lightning, khoá "model.…") hoặc file đã lọc."""
    import torch

    try:
        ck = torch.load(str(path), map_location="cpu", weights_only=True)
    except Exception:  # noqa: BLE001 – có đối tượng ngoài tensor -> nạp bằng bộ đọc chỉ dựng tensor
        ck = torch.load(str(path), map_location="cpu", weights_only=False, pickle_module=_SafePickle)
    sd = ck.get("state_dict", ck) if isinstance(ck, dict) else {}
    if any(str(k).startswith("model.") for k in sd):
        sd = {k[6:]: v for k, v in sd.items() if str(k).startswith("model.")}
    return {str(k).replace("_orig_mod.", ""): v for k, v in sd.items() if hasattr(v, "shape")}


def check_state(model, sd: dict) -> None:
    import torch

    want = model.state_dict()
    sd = dict(sd)
    for k in ("stft.window", "istft.window"):   # cửa sổ Hann của bước biến đổi phổ – không có thì dùng bản tự tạo
        sd.setdefault(k, want[k])
    miss = [k for k in want if k not in sd]
    bad = [k for k in want if k in sd and tuple(sd[k].shape) != tuple(want[k].shape)]
    if miss or bad:
        raise RuntimeError("File mô hình Bandit v2 không khớp cấu hình (thiếu " + ", ".join(miss[:3]) +
                           ("…" if len(miss) > 3 else "") + (" · sai cỡ " + ", ".join(bad[:3]) if bad else "") + ")")
    model.load_state_dict({k: sd[k].to(want[k].dtype) if torch.is_floating_point(want[k]) else sd[k] for k in want})


def convert(raw: Path, out: Path = WEIGHTS) -> Path:
    """File gốc 447 MB (kèm trạng thái huấn luyện) -> chỉ trọng số mô hình (~150 MB), nạp được bằng weights_only=True."""
    import torch

    model = build_model()
    sd = load_checkpoint(raw)
    check_state(model, sd)
    tmp = out.with_name(out.name + ".tmp")
    # không chép (clone): các khoá dùng chung tham số (NormMLP.norm = combined.0…) giữ chung vùng nhớ -> lưu 1 lần
    torch.save(model.state_dict(), str(tmp))
    tmp.replace(out)
    return out


_model = None


def load_model(dev: str):
    import torch

    global _model
    if _model is None:
        m = build_model()
        sd = torch.load(str(WEIGHTS), map_location="cpu", weights_only=True)
        check_state(m, sd)
        _model = m.eval()
    return _model.to(dev)


def release() -> None:
    global _model
    _model = None
    from .gpu import free_cache

    free_cache()


# ------------------------------------------------------------------ tải về

def _md5(path: Path) -> str:
    h = hashlib.md5()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def _zenodo_md5() -> str:
    """MD5 Zenodo công bố cho file gốc (hỏi thẳng Zenodo; lỗi mạng -> dùng giá trị ghi sẵn)."""
    try:
        from .net import session

        r = session().get(f"https://zenodo.org/api/records/{ZENODO_RECORD}", timeout=(10, 30))
        for f in (r.json().get("files") or []):
            if f.get("key") == RAW_NAME and str(f.get("checksum", "")).startswith("md5:"):
                return f["checksum"][4:]
    except Exception:  # noqa: BLE001
        pass
    return RAW_MD5


def download(progress: Prog | None = None, cancel=None) -> Path:
    """Tải file gốc từ Zenodo (lỗi -> bản chép y hệt), tải tiếp được khi đứt mạng, kiểm tra MD5, lọc thành trọng số."""
    from .ffmpeg import Cancelled
    from .net import session

    if ready():
        return WEIGHTS
    WEIGHTS.parent.mkdir(parents=True, exist_ok=True)
    raw = WEIGHTS.parent / RAW_NAME
    part = raw.with_name(raw.name + ".part")
    want_md5 = _zenodo_md5()
    errors: list[str] = []
    done = raw.is_file() and raw.stat().st_size == RAW_SIZE
    for url in URLS:
        if done:
            break
        for attempt in range(4):
            have = part.stat().st_size if part.exists() else 0
            if have >= RAW_SIZE:
                break
            try:
                hdr = {"Range": f"bytes={have}-"} if have else {}
                with session().get(url, headers=hdr, stream=True, timeout=(15, 60)) as r:
                    r.raise_for_status()
                    if have and r.status_code != 206:
                        have = 0                  # máy chủ không cho tải tiếp -> tải lại từ đầu
                    total = have + int(r.headers.get("Content-Length") or 0) or RAW_SIZE
                    t0, b0, last = time.time(), have, 0.0
                    with open(part, "ab" if have else "wb") as f:
                        for buf in r.iter_content(1 << 20):
                            if cancel and cancel():
                                raise Cancelled()
                            f.write(buf)
                            have += len(buf)
                            now = time.time()
                            if progress and now - last > 0.4:
                                last = now
                                sp = (have - b0) / max(0.1, now - t0) / 1e6
                                progress(0.95 * have / total, f"Đang tải Bandit v2 {int(100 * have / total)}% · "
                                         f"{have / 1e6:.0f} MB / {total / 1e6:.0f} MB · {sp:.1f} MB/s")
            except Cancelled:
                raise
            except Exception as e:  # noqa: BLE001 – đứt mạng giữa chừng: tải tiếp từ chỗ đã có (cùng file ở mọi nguồn)
                errors.append(f"{url.split('/')[2]}: {type(e).__name__}: {str(e)[:120]}")
                time.sleep(2 * (attempt + 1))
        if part.exists() and part.stat().st_size == RAW_SIZE:
            part.replace(raw)
            done = True
    if not raw.is_file():
        raise RuntimeError("Không tải được Bandit v2 – " + (" · ".join(errors[-3:]) or "không rõ lỗi"))
    if progress:
        progress(0.96, "Đang kiểm tra file Bandit v2…")
    got = _md5(raw)
    if got not in (want_md5, RAW_MD5):
        raw.unlink(missing_ok=True)
        raise RuntimeError(f"File Bandit v2 tải về bị hỏng (MD5 {got}) – bấm Tải về lần nữa")
    if progress:
        progress(0.98, "Đang lọc trọng số Bandit v2…")
    convert(raw)
    raw.unlink(missing_ok=True)
    return WEIGHTS


# ------------------------------------------------------------------ tách

def plan_windows(n: int, chunk: int, hop: int) -> tuple[int, int, list[int]]:
    """(đệm đầu, đệm cuối, vị trí các cửa sổ trên tín hiệu đã đệm): đệm đầu = cửa sổ − bước nên mẫu thật đầu tiên cũng
    nằm trong đủ chunk/hop cửa sổ; đệm cuối cho cửa sổ cuối vừa khít."""
    pad = chunk - hop
    total = n + 2 * pad
    k = max(1, int(np.ceil((total - chunk) / hop)) + 1)
    end_pad = pad + (k - 1) * hop + chunk - total
    return pad, end_pad, [i * hop for i in range(k)]


def separate_array(model, audio: np.ndarray, dev: str, amp_dtype=None, batch: int = 4,
                   on_step: Callable[[int, int], None] | None = None, cancel=None) -> np.ndarray:
    """audio (kênh, mẫu) float32 48 kHz -> (số phần, kênh, mẫu): cửa sổ 8 s trượt `HOP_SEC`, ghép chồng Hann."""
    import torch

    from .ffmpeg import Cancelled

    ch, n = audio.shape
    C, H = int(CHUNK_SEC * SR), int(HOP_SEC * SR)
    pad, end_pad, pos = plan_windows(n, C, H)
    x = torch.from_numpy(np.ascontiguousarray(audio, dtype=np.float32))[None]
    mode = "reflect" if n > max(pad, end_pad) else "constant"
    x = torch.nn.functional.pad(x, (pad, end_pad), mode=mode)[0]
    L = x.shape[-1]
    win = torch.hann_window(C)
    out = torch.zeros((len(STEMS), ch, L))
    wsum = torch.zeros(L)
    for p in pos:
        wsum[p:p + C] += win
    step = max(1, int(batch))
    with torch.inference_mode():
        k = 0
        while k < len(pos):
            if cancel and cancel():
                raise Cancelled()
            part = pos[k:k + step]
            frames = torch.stack([x[:, p:p + C] for p in part]).reshape(-1, 1, C).to(dev)
            try:
                est = model(frames, amp_dtype).float().cpu()          # (cửa sổ × kênh, phần, 1, C)
            except RuntimeError as e:
                if "out of memory" in str(e).lower() and step > 1:
                    if dev != "cpu":
                        torch.cuda.empty_cache()
                    step = max(1, step // 2)                          # thiếu bộ nhớ card -> lô nhỏ hơn
                    continue
                raise
            est = est.reshape(len(part), ch, len(STEMS), C)
            for i, p in enumerate(part):
                out[:, :, p:p + C] += est[i].permute(1, 0, 2) * win
            k += len(part)
            if on_step:
                on_step(k, len(pos))
    return (out[:, :, pad:pad + n] / wsum[pad:pad + n].clamp_min(1e-6)).numpy()


def device_plan() -> tuple[str, object, int]:
    """(thiết bị, kiểu số rút gọn khi chạy card, số cửa sổ mỗi lô)."""
    import torch

    if not torch.cuda.is_available():
        return "cpu", None, 1
    gb = torch.cuda.get_device_properties(0).total_memory / 1024 ** 3
    amp = torch.bfloat16 if torch.cuda.is_bf16_supported() else torch.float16
    return "cuda", amp, (2 if gb < 7 else 4 if gb < 11 else 8)


def separate_file(mix: Path, tmp: Path, progress: Prog | None = None, cancel=None) -> tuple[Path, Path]:
    """mix: wav 48 kHz 16 bit -> (lời thoại, nhạc + tiếng động) wav 48 kHz trong tmp. Phim dài tách từng đoạn
    `BLOCK_SEC` chồng 6 s, nối mượt (dùng chung cách nối với các mô hình khác: separate._Joiner)."""
    import wave

    from .separate import _Joiner, plan_chunks

    if not ready():   # chưa tải ở tab Tài liệu -> tải ngay (tiến độ 0,02–0,12 của bước tách)
        download((lambda x, m: progress(0.02 + 0.1 * x, m)) if progress else None, cancel)
    dev, amp, batch = device_plan()
    if progress:
        progress(0.12, "Đang nạp Bandit v2 (lời thoại / nhạc / tiếng động)…")
    model = load_model(dev)
    jv, ji = _Joiner(tmp / "speech48.wav", SR), _Joiner(tmp / "bg48.wav", SR)
    try:
        with wave.open(str(mix), "rb") as src:
            ch = src.getnchannels()
            chunks = plan_chunks(src.getnframes(), SR, BLOCK_SEC)
            for k, (a, b) in enumerate(chunks):
                src.setpos(a)
                y = np.frombuffer(src.readframes(b - a), np.int16).reshape(-1, ch).T.astype(np.float32) / 32768.0
                t0 = time.time()

                def step(i, n, k=k, t0=t0):
                    if progress:
                        per = (time.time() - t0) / max(1, i)            # giây mỗi cửa sổ
                        left = per * ((n - i) + (len(chunks) - k - 1) * n)
                        progress(0.15 + 0.8 * (k + i / n) / len(chunks), f"Bandit v2 đang tách lời thoại / nhạc / "
                                 f"tiếng động – phần {k + 1}/{len(chunks)}, {int(100 * i / n)}% · còn ~{int(left // 60) + 1} "
                                 "phút")
                try:
                    est = separate_array(model, y, dev, amp, batch, step, cancel)
                except RuntimeError as e:
                    if amp is None or "out of memory" in str(e).lower():
                        raise
                    amp = None     # card không chạy được nửa độ chính xác cho GRU -> float32
                    est = separate_array(model, y, dev, None, batch, step, cancel)
                keep = (b - chunks[k + 1][0]) if k + 1 < len(chunks) else 0
                jv.add(est[0].T.copy(), keep)
                ji.add((est[1] + est[2]).T.copy(), keep)
                del est, y
    finally:
        jv.close()
        ji.close()
        release()
    return tmp / "speech48.wav", tmp / "bg48.wav"
