"""Tiến trình riêng chạy Hy-MT2 (Tencent, mô hình CHUYÊN DỊCH 1,8 tỉ tham số, giấy phép Apache-2.0): dịch phụ đề
Trung -> Việt ngay trên máy, không cần API key, không mất phí. Cùng giao thức với vieneu_worker.

  vào : {"items": [{"text": "câu gốc", "context": ["câu trước", ...], "terms": [["中文", "Việt"], ...]}, ...],
         "target": "越南语"}
  ra  : @@TRAMI@@{"ok": true, "out": ["bản dịch", ...]} | {"error": "..."} | {"progress": 0.5, "msg": "..."}

Câu lệnh theo đúng mẫu nhà phát triển hướng dẫn cho dòng Hunyuan-MT: dịch thường / có ngữ cảnh / có thuật ngữ bắt buộc.
"""
from __future__ import annotations

import os
import re
import sys

from .qwen_asr_worker import _load
from .vieneu_worker import _reply

REPO = "tencent/Hy-MT2-1.8B"


def build_prompt(text: str, context: list[str] | None = None, terms: list | None = None,
                 target: str = "越南语") -> str:
    head = ""
    if terms:   # thuật ngữ bắt buộc (bảng tên riêng Hán Việt của TraMi)
        head = "参考下面的翻译：\n" + "\n".join(f"{a} 翻译成 {b}" for a, b in terms) + "\n\n"
    if context:
        return (head + "\n".join(context) + f"\n参考上面的信息，把下面的文本翻译成{target}，注意不需要翻译上文，"
                "也不要额外解释：\n" + text)
    if terms:
        return head + f"将以下文本翻译为{target}，注意只需要输出翻译后的结果，不要额外解释：\n{text}"
    return f"把下面的文本翻译成{target}，不要额外解释。\n\n{text}"


def clean_output(t: str) -> str:
    t = re.sub(r"<think>.*?</think>", "", t or "", flags=re.S)
    t = t.replace("<think>", "").replace("</think>", "")
    return " ".join(t.strip().split())


def main(repo: str = REPO) -> None:
    proto = sys.stdout
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w", encoding="utf-8")
    sys.stdout = sys.stderr
    try:
        proto.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
        sys.stdin.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:
        pass

    def prog(x: float, msg: str):
        _reply(proto, {"progress": round(x, 4), "msg": msg})

    try:
        import torch
        from transformers import AutoModelForCausalLM, AutoTokenizer

        from .progress_hook import tqdm_progress

        if torch.cuda.is_available():
            dev = "cuda:0"
            dtype = torch.bfloat16 if torch.cuda.is_bf16_supported() else torch.float16
        else:
            dev, dtype = "cpu", torch.float32
        with tqdm_progress(prog, 0.0, 1.0, "Đang nạp Hy-MT2", download="Đang tải Hy-MT2"):
            tok = _load(AutoTokenizer, repo)
            model = _load(AutoModelForCausalLM, repo, dtype=dtype, device_map=dev)
        model.eval()
        tok.padding_side = "left"
        if tok.pad_token is None:
            tok.pad_token = tok.eos_token
    except Exception as e:  # noqa: BLE001
        _reply(proto, {"error": f"Không nạp được Hy-MT2: {type(e).__name__}: {e}"})
        return
    _reply(proto, {"ready": True, "sr": 0})
    batch = 8 if dev != "cpu" else 2
    import json

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
            items = req.get("items") or []
            target = req.get("target") or "越南语"
            out: list[str] = []
            for b in range(0, len(items), batch):
                part = items[b:b + batch]
                prompts = [tok.apply_chat_template([{"role": "user", "content": build_prompt(
                    it["text"], it.get("context"), it.get("terms"), target)}], tokenize=False,
                    add_generation_prompt=True) for it in part]
                enc = tok(prompts, return_tensors="pt", padding=True, add_special_tokens=False).to(model.device)
                longest = max(len(it["text"]) for it in part)
                with torch.inference_mode():
                    gen = model.generate(**enc, max_new_tokens=min(512, 4 * longest + 48), do_sample=False,
                                         repetition_penalty=1.05, pad_token_id=tok.pad_token_id)
                new = gen[:, enc["input_ids"].shape[1]:]
                out += [clean_output(x) for x in tok.batch_decode(new, skip_special_tokens=True)]
                _reply(proto, {"progress": round(min(1.0, (b + len(part)) / max(1, len(items))), 4),
                               "msg": f"Hy-MT2 đang dịch {b + len(part)}/{len(items)}"})
            _reply(proto, {"ok": True, "out": out})
        except Exception as e:  # noqa: BLE001
            _reply(proto, {"error": f"{type(e).__name__}: {e}"})


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else REPO)
