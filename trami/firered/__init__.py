"""FireRedASR2-AED (Xiaohongshu FireRedTeam, 2/2026, Apache-2.0) – bộ nghe tiếng Trung thứ 2 của Subvideo.

Mã suy luận chép từ github.com/FireRedTeam/FireRedASR2S (bản 4e7d9aa, 2/6/2026), giấy phép Apache-2.0 – xem LICENSE
cùng thư mục; mỗi file ghi rõ chỗ đã sửa. Không cài gói `fireredasr2s` (gói ghim torch==2.10 / transformers==5.1, đè
mất PyTorch CUDA + transformers ≥ 5.13 mà Qwen3-ASR / OmniVoice cần). Chỉ cần torch (+ transformers cho bộ đặt dấu câu);
kaldi_native_fbank / sentencepiece có thì dùng, không có vẫn chạy.

  Recognizer(model_dir).transcribe(clips) -> [{"text", "confidence", "words": [[chữ, đầu, cuối], ...]}]
  Recognizer.align(clips, texts)           -> [[[chữ, đầu, cuối], ...]]   (căn lời có sẵn bằng nhánh CTC)
  punc.Punctuator(punc_dir)(texts)         -> lời có dấu câu
"""
from __future__ import annotations

import os
import re

import numpy as np

SR = 16000
SPECIAL = re.compile(r"<[a-z_]+>")   # <blank> <sil> <unk> <sos> <eos>…


def load_package(path: str):
    import torch

    try:   # đọc kiểu ánh xạ file: file 4 GB không phải chép hết vào RAM
        return torch.load(path, map_location="cpu", weights_only=False, mmap=True)
    except Exception:  # noqa: BLE001 – file kiểu cũ không ánh xạ được
        return torch.load(path, map_location="cpu", weights_only=False)


class Recognizer:
    def __init__(self, model_dir: str, device: str = "cpu", half: bool = False, beam_size: int = 3):
        import torch

        from .feat import FeatExtractor
        from .model import FireRedAsrAed
        from .tokenizer import AedTokenizer

        pkg = load_package(os.path.join(model_dir, "model.pth.tar"))
        model = FireRedAsrAed.from_args(pkg["args"])
        model.load_state_dict(pkg["model_state_dict"], strict=False)
        del pkg
        model.eval()
        self.dtype = torch.float32
        if device != "cpu" and half:
            self.dtype = torch.bfloat16 if torch.cuda.is_bf16_supported() else torch.float16
            model = model.to(self.dtype)
        self.model = model.to(device)
        self.device = device
        self.feat = FeatExtractor(os.path.join(model_dir, "cmvn.ark"))
        spm = os.path.join(model_dir, "train_bpe1000.model")
        self.tok = AedTokenizer(os.path.join(model_dir, "dict.txt"), spm if os.path.isfile(spm) else "")
        self.beam_size = beam_size

    def _inputs(self, clips):
        feats, lengths, durs = self.feat(clips)
        return feats.to(self.device, self.dtype), lengths.to(self.device), durs

    def transcribe(self, clips: list[np.ndarray]) -> list[dict]:
        import torch

        feats, lengths, durs = self._inputs(clips)
        with torch.no_grad():
            hyps = self.model.transcribe(feats, lengths, self.beam_size, 1, 0, 1.25, 0.6, 1.0, True)
        out = []
        for hyp, dur in zip(hyps, durs):
            hyp = hyp[0]   # 1-best
            ids = [int(i) for i in hyp["yseq"].cpu().tolist() if int(i) != 0]
            text = SPECIAL.sub("", self.tok.detokenize(ids)).lower()
            out.append({"text": text, "confidence": round(float(hyp["confidence"].float().cpu().item()), 3),
                        "words": self._timestamps(hyp.get("timestamp"), ids, dur)})
        return out

    def align(self, clips: list[np.ndarray], texts: list[str]) -> list[list]:
        ids = [self.tok.tokenize(t) for t in texts]
        feats, lengths, durs = self._inputs(clips)
        res = self.model.align(feats, lengths, ids)
        return [self._timestamps(r, i, d) if i else [] for r, i, d in zip(res, ids, durs)]

    def _timestamps(self, ts, ids, dur):
        """Như FireRedAsr2._get_and_fix_timestamp: dời 60 ms, không vượt độ dài đoạn, gộp mảnh tiếng Anh thành từ."""
        r3 = lambda x: round(float(x), 3)   # noqa: E731
        if not ids:
            return []
        stamp = []
        if ts is None:
            avg = dur / len(ids)
            for i, k in enumerate(ids):
                stamp.append([self.tok.piece(k).lower(), r3(i * avg), r3(min((i + 1) * avg, dur))])
        else:
            starts, ends = ts
            last = dur
            for k, a, b in zip(ids, starts, ends):
                a = min(max(0.0, a - 0.06), last)
                b = min(max(0.0, b - 0.06), dur)
                last = b
                stamp.append([self.tok.piece(k).lower(), r3(a), r3(b)])
        return [list(x) for x in self.tok.merge_spm_timestamp(stamp) if not SPECIAL.fullmatch(x[0])]
