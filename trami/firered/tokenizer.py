# Copyright 2026 Xiaohongshu. (Author: Kaituo Xu, Junjie Chen)
# Licensed under the Apache License, Version 2.0 (see trami/firered/LICENSE).
#
# Bảng chữ của FireRedASR2-AED / FireRedPunc, theo fireredasr2s/fireredasr2/{data/token_dict.py,
# tokenizer/aed_tokenizer.py}. Sửa đổi của Subvideo: sentencepiece không bắt buộc (chỉ cần khi căn lời có chữ Latin);
# thêm `WordPiece` (tách chữ cho FireRedPunc – thay BertTokenizer của transformers: chữ Hán mỗi chữ 1 mảnh, chữ Latin
# / số tách theo vocab).
from __future__ import annotations

import re

CJK_RE = re.compile(r"([㐀-䶿一-鿿])")


class TokenDict:
    def __init__(self, dict_path: str, unk: str = ""):
        self.id2word, self.word2id = self.read_dict(dict_path)
        self.unk = unk
        assert unk == "" or unk in self.word2id
        self.unkid = self.word2id[unk] if unk else -1

    def get(self, key, default):
        if isinstance(default, str):
            default = self.word2id[default]
        return self.word2id.get(key, default)

    def __getitem__(self, key):
        if isinstance(key, str):
            return self.word2id.get(key, self.word2id[self.unk]) if self.unk else self.word2id[key]
        return self.id2word[key]

    def __len__(self):
        return len(self.id2word)

    def __contains__(self, query):
        return query in (self.word2id if isinstance(query, str) else range(len(self.id2word)))

    @staticmethod
    def read_dict(dict_path: str):
        id2word, word2id = [], {}
        with open(dict_path, encoding="utf8") as f:
            for i, line in enumerate(f):
                tokens = line.strip().split()
                if len(tokens) >= 2:
                    word, index = tokens[0], int(tokens[1])
                elif len(tokens) == 1:
                    word, index = tokens[0], i
                else:  # empty line or space
                    word, index = " ", i
                assert len(id2word) == index
                if word == "<space>":
                    word = " "
                word2id[word] = index
                id2word.append(word)
        return id2word, word2id


class AedTokenizer:
    """One Chinese char is a token; English words are SPM pieces ("▁" = space)."""
    SPM_SPACE = "▁"

    def __init__(self, dict_path: str, spm_model: str = ""):
        self.dict = TokenDict(dict_path, unk="<unk>")
        self.sp = None
        if spm_model:
            try:
                import sentencepiece as spm

                self.sp = spm.SentencePieceProcessor()
                self.sp.Load(spm_model)
            except Exception:  # noqa: BLE001 – không có sentencepiece: căn lời bỏ qua chữ Latin
                self.sp = None

    def tokenize(self, text: str) -> list[int]:
        """Lời -> mã chữ để căn CTC. Dấu câu bỏ; chữ Latin cần sentencepiece (không có thì bỏ)."""
        text = re.sub(r"[，。？！、；：,.?!;:…「」『』“”\"'（）()《》·\-—~～]", " ", text.upper())
        ids = []
        for part in [p for p in CJK_RE.split(text.strip()) if p.strip()]:
            if CJK_RE.fullmatch(part):
                ids.append(self.dict.get(part, self.dict.unkid))
            elif self.sp is not None:
                ids += [self.dict.get(p, self.dict.unkid) for p in self.sp.EncodeAsPieces(part.strip())]
        return ids

    def detokenize(self, ids, join_symbol: str = "", replace_spm_space: bool = True) -> str:
        s = join_symbol.join(self.dict[int(i)] for i in ids)
        return s.replace(self.SPM_SPACE, " ").strip() if replace_spm_space else s

    def piece(self, i: int) -> str:
        return self.dict[int(i)]

    def merge_spm_timestamp(self, timestamp):
        merged, i = [], 0
        while i < len(timestamp):
            token, start, end = timestamp[i]
            if token.startswith(self.SPM_SPACE):
                token = token.replace(self.SPM_SPACE, "")
                j = i + 1
                while j < len(timestamp) and re.match("^[a-zA-Z']+$", timestamp[j][0]):
                    token += timestamp[j][0]
                    end = timestamp[j][2]
                    j += 1
                i = j
            else:
                i += 1
            merged.append((token, start, end))
        return merged


class WordPiece:
    """Tách chữ kiểu BERT tiếng Trung (chinese-bert-wwm-ext): chữ Hán / dấu câu mỗi ký tự 1 mảnh, từ Latin / số
    tách theo vocab (mảnh tiếp theo có "##"), chữ không có trong vocab -> [UNK] (giữ chữ gốc để ghép lại lời)."""

    def __init__(self, vocab: TokenDict):
        self.vocab = vocab

    def _word(self, w: str) -> list[str]:
        out, i = [], 0
        while i < len(w):
            j = len(w)
            while j > i:
                sub = w[i:j] if i == 0 else "##" + w[i:j]
                if sub in self.vocab.word2id:
                    out.append(sub)
                    break
                j -= 1
            if j == i:
                return ["[UNK]"]
            i = j
        return out

    def tokenize(self, text: str) -> list[tuple[str, str]]:
        """[(mảnh để tra vocab, chữ gốc tương ứng)]."""
        out: list[tuple[str, str]] = []
        for m in re.finditer(r"[A-Za-z0-9']+|\S", text):
            w = m.group(0)
            if re.fullmatch(r"[A-Za-z0-9']+", w):
                pieces = self._word(w.lower())
                if pieces == ["[UNK]"]:
                    out.append(("[UNK]", w))
                else:
                    pos = 0
                    for p in pieces:
                        n = len(p.replace("##", "", 1))
                        out.append((p, w[pos:pos + n]))
                        pos += n
            else:
                out.append((w if w in self.vocab.word2id else "[UNK]", w))
        return out
