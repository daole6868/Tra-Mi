# Copyright 2026 Xiaohongshu. (Author: Kaituo Xu, Junjie Chen)
# Licensed under the Apache License, Version 2.0 (see trami/firered/LICENSE).
#
# Đặt dấu câu FireRedPunc (BERT tiếng Trung + lớp phân loại dấu sau mỗi chữ), theo fireredasr2s/fireredpunc/
# {punc.py, models/fireredpunc_bert.py}. Sửa đổi của Subvideo: tách chữ bằng `tokenizer.WordPiece` (không cần
# BertTokenizer), ghép dấu vào đúng chữ gốc của câu (giữ nguyên chữ Latin / số / chữ lạ), bỏ phần xử lý theo mốc thời gian
# và chia câu theo độ dài tối đa (Subvideo tự tách câu theo mốc từng chữ).
from __future__ import annotations

import os
import re

import torch
import torch.nn as nn

from .tokenizer import TokenDict, WordPiece


class FireRedPuncBert(nn.Module):
    def __init__(self, bert, classifier_dropout: float, odim: int, cls_id: int):
        super().__init__()
        self.bert = bert
        self.dropout = nn.Dropout(float(classifier_dropout))
        self.classifier = nn.Linear(bert.config.hidden_size, odim)
        self.max_input_len = self.bert.embeddings.position_embeddings.num_embeddings - 1
        self.cls_id = cls_id

    def forward_model(self, padded_inputs, lengths):
        if padded_inputs.size(1) <= self.max_input_len:
            return self._forward(padded_inputs, lengths)
        scores, left = [], lengths
        for chunk in padded_inputs.split(self.max_input_len, dim=1):
            cl = torch.clamp(left, min=0, max=self.max_input_len)
            left = left - cl
            scores.append(self._forward(chunk, cl))
        return torch.cat(scores, dim=1)

    def _forward(self, padded_inputs, lengths):
        n = padded_inputs.size(0)
        cls = padded_inputs.new_ones(n, 1).fill_(self.cls_id)
        padded_inputs = torch.cat((cls, padded_inputs), dim=1)
        lengths = lengths + 1
        t = int(lengths.max())
        mask = (torch.arange(t, device=lengths.device)[None, :] < lengths[:, None]).float()
        out = self.bert(padded_inputs, mask)
        return self.classifier(self.dropout(out[0][:, 1:]))


class Punctuator:
    """Lời chưa có dấu (đầu ra FireRedASR2) -> lời có dấu câu ，。？！."""

    def __init__(self, model_dir: str, device: str = "cpu"):
        import transformers

        package = torch.load(os.path.join(model_dir, "model.pth.tar"), map_location="cpu", weights_only=False)
        args = package["args"]
        bert = transformers.BertModel.from_pretrained(os.path.join(model_dir, "chinese-lert-base"))
        bert.pooler = None
        self.model = FireRedPuncBert(bert, getattr(args, "classifier_dropout", 0.1), int(args.odim), int(args.cls_id))
        self.model.load_state_dict(package["model_state_dict"], strict=False)
        self.model.eval().to(device)
        self.device = device
        self.in_dict = TokenDict(os.path.join(model_dir, "chinese-bert-wwm-ext_vocab.txt"), unk="[UNK]")
        self.out_dict = TokenDict(os.path.join(model_dir, "out_dict"))
        self.wp = WordPiece(self.in_dict)
        self.pad_id = self.in_dict["[PAD]"]

    @torch.no_grad()
    def __call__(self, texts: list[str]) -> list[str]:
        toks = [self.wp.tokenize(t) for t in texts]
        out = list(texts)
        idx = [k for k, t in enumerate(toks) if t]
        if not idx:
            return out
        lens = [len(toks[k]) for k in idx]
        pad = torch.full((len(idx), max(lens)), self.pad_id, dtype=torch.long)
        for r, k in enumerate(idx):
            pad[r, :len(toks[k])] = torch.tensor([self.in_dict.get(p, self.in_dict.unkid) for p, _o in toks[k]])
        lengths = torch.tensor(lens, dtype=torch.int32)
        logits = self.model.forward_model(pad.to(self.device), lengths.to(self.device))
        preds = logits.argmax(dim=-1).cpu().tolist()
        for r, k in enumerate(idx):
            out[k] = self._join(texts[k], toks[k], preds[r][:lens[r]])
        return out

    def _join(self, text: str, toks: list[tuple[str, str]], tags: list[int]) -> str:
        """Gắn dấu sau từng mảnh, giữ khoảng trắng giữa 2 từ Latin như lời gốc."""
        res, pos = "", 0
        for (_p, orig), tag in zip(toks, tags):
            k = text.find(orig, pos)
            if k > pos and text[pos:k].strip() == "" and res and re.match(r"[A-Za-z0-9']", orig):
                res += " "
            pos = k + len(orig) if k >= 0 else pos
            mark = self.out_dict[int(tag)]
            res += orig + ("" if mark.strip() == "" else mark)
        res = re.sub(r"([A-Za-z0-9])，\s*(?=[A-Za-z])", r"\1, ", res)
        res = re.sub(r"([A-Za-z0-9])。\s*(?=[A-Za-z])", r"\1. ", res)
        return res
