# Copyright 2026 Xiaohongshu. (Author: Kaituo Xu)
# Licensed under the Apache License, Version 2.0 (see trami/firered/LICENSE).
#
# Mô hình FireRedASR2-AED (Conformer + Transformer + CTC), chép từ github.com/FireRedTeam/FireRedASR2S
# (fireredasr2s/fireredasr2/models/fireredasr_aed.py + module/{conformer_encoder,transformer_decoder,ctc}.py).
# Sửa đổi của Subvideo: gộp 4 file làm 1; bỏ torchaudio (căn CTC bằng `ctc_forced_align` numpy – torchaudio bản mới
# đã bỏ forced_align), thêm `FireRedAsrAed.align` (căn lời có sẵn bằng nhánh CTC); không dùng LSTM LM ngoài.
from typing import Dict, List, Optional

import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F
from torch import Tensor

from .ctc import ctc_forced_align

class ConformerEncoder(nn.Module):
    def __init__(self, idim, n_layers, n_head, d_model,
                 residual_dropout=0.1, dropout_rate=0.1, kernel_size=33,
                 pe_maxlen=5000):
        super().__init__()
        self.odim = d_model

        self.input_preprocessor = Conv2dSubsampling(idim, d_model)
        self.positional_encoding = RelPositionalEncoding(d_model)
        self.dropout = nn.Dropout(residual_dropout)

        self.layer_stack = nn.ModuleList()
        for l in range(n_layers):
            block = RelPosEmbConformerBlock(d_model, n_head,
                        residual_dropout,
                        dropout_rate, kernel_size)
            self.layer_stack.append(block)

    def forward(self, padded_input, input_lengths, pad=True):
        if pad:
            padded_input = F.pad(padded_input,
                (0, 0, 0, self.input_preprocessor.context - 1), 'constant', 0.0)
        src_mask = self.padding_position_is_0(padded_input, input_lengths)

        embed_output, input_lengths, src_mask = self.input_preprocessor(padded_input, src_mask)
        enc_output = self.dropout(embed_output)

        pos_emb = self.dropout(self.positional_encoding(embed_output))

        enc_outputs = []
        for enc_layer in self.layer_stack:
            enc_output = enc_layer(enc_output, pos_emb, slf_attn_mask=src_mask,
                                   pad_mask=src_mask)
            enc_outputs.append(enc_output)

        return enc_output, input_lengths, src_mask

    def padding_position_is_0(self, padded_input, input_lengths):
        N, T = padded_input.size()[:2]
        mask = torch.ones((N, T)).to(padded_input.device)
        for i in range(N):
            mask[i, input_lengths[i]:] = 0
        mask = mask.unsqueeze(dim=1)
        return mask.to(torch.uint8)


class RelPosEmbConformerBlock(nn.Module):
    def __init__(self, d_model, n_head,
                 residual_dropout=0.1,
                 dropout_rate=0.1, kernel_size=33):
        super().__init__()
        self.ffn1 = ConformerFeedForward(d_model, dropout_rate)
        self.mhsa = RelPosMultiHeadAttention(n_head, d_model,
                                             residual_dropout)
        self.conv = ConformerConvolution(d_model, kernel_size,
                                         dropout_rate)
        self.ffn2 = ConformerFeedForward(d_model, dropout_rate)
        self.layer_norm = nn.LayerNorm(d_model)

    def forward(self, x, pos_emb, slf_attn_mask=None, pad_mask=None):
        out = 0.5 * x + 0.5 * self.ffn1(x)
        out = self.mhsa(out, out, out, pos_emb, mask=slf_attn_mask)[0]
        out = self.conv(out, pad_mask)
        out = 0.5 * out + 0.5 * self.ffn2(out)
        out = self.layer_norm(out)
        return out


class Swish(nn.Module):
    def forward(self, x):
        return x * torch.sigmoid(x)


class Conv2dSubsampling(nn.Module):
    def __init__(self, idim, d_model, out_channels=32):
        super().__init__()
        self.conv = nn.Sequential(
            nn.Conv2d(1, out_channels, 3, 2),
            nn.ReLU(),
            nn.Conv2d(out_channels, out_channels, 3, 2),
            nn.ReLU(),
        )
        subsample_idim = ((idim - 1) // 2 - 1) // 2
        self.out = nn.Linear(out_channels * subsample_idim, d_model)

        self.subsampling = 4
        left_context = right_context = 3  # both exclude currect frame
        self.context = left_context + 1 + right_context  # 7

    def forward(self, x, x_mask):
        x = x.unsqueeze(1)
        x = self.conv(x)
        N, C, T, D = x.size()
        x = self.out(x.transpose(1, 2).contiguous().view(N, T, C * D))
        mask = x_mask[:, :, :-2:2][:, :, :-2:2]
        input_lengths = mask[:, -1, :].sum(dim=-1)
        return x, input_lengths, mask


class RelPositionalEncoding(torch.nn.Module):
    def __init__(self, d_model, max_len=5000):
        super().__init__()
        pe_positive = torch.zeros(max_len, d_model, requires_grad=False)
        pe_negative = torch.zeros(max_len, d_model, requires_grad=False)
        position = torch.arange(0, max_len).unsqueeze(1).float()
        div_term = torch.exp(torch.arange(0, d_model, 2).float() *
                             -(torch.log(torch.tensor(10000.0)).item()/d_model))
        pe_positive[:, 0::2] = torch.sin(position * div_term)
        pe_positive[:, 1::2] = torch.cos(position * div_term)
        pe_negative[:, 0::2] = torch.sin(-1 * position * div_term)
        pe_negative[:, 1::2] = torch.cos(-1 * position * div_term)

        pe_positive = torch.flip(pe_positive, [0]).unsqueeze(0)
        pe_negative = pe_negative[1:].unsqueeze(0)
        pe = torch.cat([pe_positive, pe_negative], dim=1)
        self.register_buffer('pe', pe)

    def forward(self, x):
        # Tmax = 2 * max_len - 1
        Tmax, T = self.pe.size(1), x.size(1)
        pos_emb = self.pe[:, Tmax // 2 - T + 1 : Tmax // 2 + T].clone().detach()
        return pos_emb


class ConformerFeedForward(nn.Module):
    def __init__(self, d_model, dropout_rate=0.1):
        super().__init__()
        pre_layer_norm = nn.LayerNorm(d_model)
        linear_expand = nn.Linear(d_model, d_model*4)
        nonlinear = Swish()
        dropout_pre = nn.Dropout(dropout_rate)
        linear_project = nn.Linear(d_model*4, d_model)
        dropout_post = nn.Dropout(dropout_rate)
        self.net = nn.Sequential(pre_layer_norm,
                                 linear_expand,
                                 nonlinear,
                                 dropout_pre,
                                 linear_project,
                                 dropout_post)

    def forward(self, x):
        residual = x
        output = self.net(x)
        output = output + residual
        return output


class ConformerConvolution(nn.Module):
    def __init__(self, d_model, kernel_size=33, dropout_rate=0.1):
        super().__init__()
        assert kernel_size % 2 == 1
        self.pre_layer_norm = nn.LayerNorm(d_model)
        self.pointwise_conv1 = nn.Conv1d(d_model, d_model*4, kernel_size=1, bias=False)
        self.glu = F.glu
        self.padding = (kernel_size - 1) // 2
        self.depthwise_conv = nn.Conv1d(d_model*2, d_model*2,
                                        kernel_size, stride=1,
                                        padding=self.padding,
                                        groups=d_model*2, bias=False)
        self.batch_norm = nn.LayerNorm(d_model*2)
        self.swish = Swish()
        self.pointwise_conv2 = nn.Conv1d(d_model*2, d_model, kernel_size=1, bias=False)
        self.dropout = nn.Dropout(dropout_rate)

    def forward(self, x, mask=None):
        residual = x
        out = self.pre_layer_norm(x)
        out = out.transpose(1, 2)
        if mask is not None:
            out.masked_fill_(mask.ne(1), 0.0)
        out = self.pointwise_conv1(out)
        out = F.glu(out, dim=1)
        out = self.depthwise_conv(out)

        out = out.transpose(1, 2)
        out = self.swish(self.batch_norm(out))
        out = out.transpose(1, 2)

        out = self.dropout(self.pointwise_conv2(out))
        if mask is not None:
            out.masked_fill_(mask.ne(1), 0.0)
        out = out.transpose(1, 2)
        return out + residual


class EncoderMultiHeadAttention(nn.Module):
    def __init__(self, n_head, d_model,
                 residual_dropout=0.1):
        super().__init__()
        assert d_model % n_head == 0
        self.n_head = n_head
        self.d_k = d_model // n_head
        self.d_v = self.d_k

        self.w_qs = nn.Linear(d_model, n_head * self.d_k, bias=False)
        self.w_ks = nn.Linear(d_model, n_head * self.d_k, bias=False)
        self.w_vs = nn.Linear(d_model, n_head * self.d_v, bias=False)

        self.layer_norm_q = nn.LayerNorm(d_model)
        self.layer_norm_k = nn.LayerNorm(d_model)
        self.layer_norm_v = nn.LayerNorm(d_model)

        self.attention = ScaledDotProductAttention(temperature=self.d_k ** 0.5)
        self.fc = nn.Linear(n_head * self.d_v, d_model, bias=False)
        self.dropout = nn.Dropout(residual_dropout)

    def forward(self, q, k, v, mask=None):
        sz_b, len_q = q.size(0), q.size(1)

        residual = q
        q, k, v = self.forward_qkv(q, k, v)

        output, attn = self.attention(q, k, v, mask=mask)

        output = self.forward_output(output, residual, sz_b, len_q)
        return output, attn

    def forward_qkv(self, q, k, v):
        d_k, d_v, n_head = self.d_k, self.d_v, self.n_head
        sz_b, len_q, len_k, len_v = q.size(0), q.size(1), k.size(1), v.size(1)

        q = self.layer_norm_q(q)
        k = self.layer_norm_k(k)
        v = self.layer_norm_v(v)

        q = self.w_qs(q).view(sz_b, len_q, n_head, d_k)
        k = self.w_ks(k).view(sz_b, len_k, n_head, d_k)
        v = self.w_vs(v).view(sz_b, len_v, n_head, d_v)
        q = q.transpose(1, 2)
        k = k.transpose(1, 2)
        v = v.transpose(1, 2)
        return q, k, v

    def forward_output(self, output, residual, sz_b, len_q):
        output = output.transpose(1, 2).contiguous().view(sz_b, len_q, -1)
        fc_out = self.fc(output)
        output = self.dropout(fc_out)
        output = output + residual
        return output


class ScaledDotProductAttention(nn.Module):
    def __init__(self, temperature):
        super().__init__()
        self.temperature = temperature
        self.dropout = nn.Dropout(0.0)
        self.INF = float('inf')

    def forward(self, q, k, v, mask=None):
        attn = torch.matmul(q, k.transpose(2, 3)) / self.temperature
        output, attn = self.forward_attention(attn, v, mask)
        return output, attn

    def forward_attention(self, attn, v, mask=None):
        if mask is not None:
            mask = mask.unsqueeze(1)
            mask = mask.eq(0)
            attn = attn.masked_fill(mask, -self.INF)
            attn = torch.softmax(attn, dim=-1).masked_fill(mask, 0.0)
        else:
            attn = torch.softmax(attn, dim=-1)

        d_attn = self.dropout(attn)
        output = torch.matmul(d_attn, v)

        return output, attn


class RelPosMultiHeadAttention(EncoderMultiHeadAttention):
    def __init__(self, n_head, d_model,
                 residual_dropout=0.1):
        super().__init__(n_head, d_model,
                         residual_dropout)
        d_k = d_model // n_head
        self.scale = 1.0 / (d_k ** 0.5)
        self.linear_pos = nn.Linear(d_model, n_head * d_k, bias=False)
        self.pos_bias_u = nn.Parameter(torch.FloatTensor(n_head, d_k))
        self.pos_bias_v = nn.Parameter(torch.FloatTensor(n_head, d_k))
        torch.nn.init.xavier_uniform_(self.pos_bias_u)
        torch.nn.init.xavier_uniform_(self.pos_bias_v)

    def _rel_shift(self, x):
        N, H, T1, T2 = x.size()
        zero_pad = torch.zeros((N, H, T1, 1), device=x.device, dtype=x.dtype)
        x_padded = torch.cat([zero_pad, x], dim=-1)

        x_padded = x_padded.view(N, H, T2 + 1, T1)
        x = x_padded[:, :, 1:].view_as(x)
        x = x[:, :, :, : x.size(-1) // 2 + 1]
        return x

    def forward(self, q, k, v, pos_emb, mask=None):
        sz_b, len_q = q.size(0), q.size(1)

        residual = q
        q, k, v = self.forward_qkv(q, k, v)

        q = q.transpose(1, 2)
        n_batch_pos = pos_emb.size(0)
        p = self.linear_pos(pos_emb).view(n_batch_pos, -1, self.n_head, self.d_k)
        p = p.transpose(1, 2)

        q_with_bias_u = (q + self.pos_bias_u).transpose(1, 2)
        q_with_bias_v = (q + self.pos_bias_v).transpose(1, 2)

        matrix_ac = torch.matmul(q_with_bias_u, k.transpose(-2, -1))

        matrix_bd = torch.matmul(q_with_bias_v, p.transpose(-2, -1))
        matrix_bd = self._rel_shift(matrix_bd)

        attn_scores = matrix_ac + matrix_bd
        attn_scores.mul_(self.scale)

        output, attn = self.attention.forward_attention(attn_scores, v, mask=mask)

        output = self.forward_output(output, residual, sz_b, len_q)
        return output, attn


class TransformerDecoder(nn.Module):
    def __init__(
            self, sos_id, eos_id, pad_id, odim,
            n_layers, n_head, d_model,
            residual_dropout=0.1, pe_maxlen=5000):
        super().__init__()
        self.INF = 1e10
        # parameters
        self.pad_id = pad_id
        self.sos_id = sos_id
        self.eos_id = eos_id
        self.n_layers = n_layers

        # Components
        self.tgt_word_emb = nn.Embedding(odim, d_model, padding_idx=self.pad_id)
        self.positional_encoding = PositionalEncoding(d_model, max_len=pe_maxlen)
        self.dropout = nn.Dropout(residual_dropout)

        self.layer_stack = nn.ModuleList()
        for l in range(n_layers):
            block = DecoderLayer(d_model, n_head, residual_dropout)
            self.layer_stack.append(block)

        self.tgt_word_prj = nn.Linear(d_model, odim, bias=False)
        self.layer_norm_out = nn.LayerNorm(d_model)

        self.tgt_word_prj.weight = self.tgt_word_emb.weight
        self.scale = (d_model ** 0.5)

    def batch_beam_search(self, encoder_outputs, src_masks,
                   beam_size=1, nbest=1, decode_max_len=0,
                   softmax_smoothing=1.0, length_penalty=0.0, eos_penalty=1.0,
                   elm=None, elm_weight=0.0):
        B = beam_size
        N, Ti, H = encoder_outputs.size()
        device = encoder_outputs.device
        maxlen = decode_max_len if decode_max_len > 0 else Ti
        assert eos_penalty > 0.0

        # Init
        encoder_outputs = encoder_outputs.unsqueeze(1).repeat(1, B, 1, 1).view(N*B, Ti, H)
        src_mask = src_masks.unsqueeze(1).repeat(1, B, 1, 1).view(N*B, -1, Ti)
        ys = torch.ones(N*B, 1).fill_(self.sos_id).long().to(device)
        t_ys = ys.clone()
        confidences = torch.zeros(N*B, 1).float().to(device)
        caches: List[Optional[Tensor]] = []
        for _ in range(self.n_layers):
            caches.append(None)
        scores = torch.tensor([0.0] + [-self.INF]*(B-1)).float().to(device)
        scores = scores.repeat(N).view(N*B, 1)
        is_finished = torch.zeros_like(scores)
        if elm is not None:
            elm_cache = elm.init_hidden(encoder_outputs, N*B)

        # Autoregressive Prediction
        for t in range(maxlen):
            tgt_mask = self.ignored_target_position_is_0(ys, self.pad_id)

            dec_output = self.dropout(
                self.tgt_word_emb(ys) * self.scale +
                self.positional_encoding(ys))
#            if t > 0:
#                dec_output = dec_output[:, -1:, :]
            i = 0
            for dec_layer in self.layer_stack:
                dec_output = dec_layer.forward(
                    dec_output, encoder_outputs,
                    tgt_mask, src_mask,
                    cache=caches[i])
                caches[i] = dec_output
                i += 1

            dec_output = self.layer_norm_out(dec_output)

            t_logit = self.tgt_word_prj(dec_output[:, -1])
            t_scores = F.log_softmax(t_logit / softmax_smoothing, dim=-1)
            t_origin_scores  = t_scores

            if elm is not None and elm_weight > 0.0:
                elm_logit, elm_cache = elm.forward_model(t_ys, hidden=elm_cache)
                #elm_logit, _ = elm.forward_model(ys)
                t_lm_scores = torch.log_softmax(elm_logit[:, -1], dim=-1) * (1 - is_finished.float()) # mask, (N*B, V)
                t_lm_scores[:, elm.eos_id] *= 3
                t_scores = t_scores + elm_weight * t_lm_scores

            if eos_penalty != 1.0:
                t_scores[:, self.eos_id] *= eos_penalty

            t_topB_scores, t_topB_ys = torch.topk(t_scores, k=B, dim=1)
            t_topB_scores = self.set_finished_beam_score_to_zero(t_topB_scores, is_finished)
            t_topB_ys = self.set_finished_beam_y_to_eos(t_topB_ys, is_finished)

            # Accumulated
            scores = scores + t_topB_scores

            # Pruning
            scores = scores.view(N, B*B)
            scores, topB_score_ids = torch.topk(scores, k=B, dim=1)
            scores = scores.view(-1, 1)

            topB_row_number_in_each_B_rows_of_ys = torch.div(topB_score_ids, B).view(N*B)
            stride = B * torch.arange(N).view(N, 1).repeat(1, B).view(N*B).to(device)
            topB_row_number_in_ys = topB_row_number_in_each_B_rows_of_ys.long() + stride.long()

            # Update ys
            ys = ys[topB_row_number_in_ys]
            t_ys = torch.gather(t_topB_ys.view(N, B*B), dim=1, index=topB_score_ids).view(N*B, 1)
            ys = torch.cat((ys, t_ys), dim=1)

            # Update confidences
            confidences = confidences[topB_row_number_in_ys]
            t_confidences = torch.gather(t_topB_scores.view(N, B*B), dim=1, index=topB_score_ids).view(N*B, 1)
            t_confidences = torch.exp(t_confidences)
            assert torch.all(t_confidences <= 1.0)
            assert torch.all(t_confidences >= 0.0)
            confidences = torch.cat((confidences, t_confidences), dim=1)

            # Update caches
            new_caches: List[Optional[Tensor]] = []
            for cache in caches:
                if cache is not None:
                    new_caches.append(cache[topB_row_number_in_ys])
            caches = new_caches
            if elm and elm_weight > 0.0:
                elm_cache = (elm_cache[0][:, topB_row_number_in_ys], elm_cache[1][:, topB_row_number_in_ys])

            # Update finished state
            is_finished = t_ys.eq(self.eos_id)
            if is_finished.sum().item() == N*B:
                break

        # Length penalty (follow GNMT)
        scores = scores.view(N, B)
        ys = ys.view(N, B, -1)
        ys_lengths = self.get_ys_lengths(ys)
        if length_penalty > 0.0:
            penalty = torch.pow((5+ys_lengths.float())/(5.0+1), length_penalty)
            scores /= penalty
        nbest_scores, nbest_ids = torch.topk(scores, k=int(nbest), dim=1)
        nbest_scores = -1.0 * nbest_scores
        index = nbest_ids + B * torch.arange(N).view(N, 1).to(device).long()
        nbest_ys = ys.view(N*B, -1)[index.view(-1)]
        nbest_ys = nbest_ys.view(N, nbest_ids.size(1), -1)
        nbest_ys_lengths = ys_lengths.view(N*B)[index.view(-1)].view(N, -1)
        nbest_confidences = confidences.view(N*B, -1)[index.view(-1)].view(N, nbest_ids.size(1), -1)

        # result
        nbest_hyps: List[List[Dict[str, Tensor]]] = []
        for n in range(N):
            n_nbest_hyps: List[Dict[str, Tensor]] = []
            for i, score in enumerate(nbest_scores[n]):
                confidence = nbest_confidences[n, i, 1:nbest_ys_lengths[n, i]]
                confidence = confidence.mean()
                new_hyp = {
                    "yseq": nbest_ys[n, i, 1:nbest_ys_lengths[n, i]],
                    "confidence": confidence
                }
                n_nbest_hyps.append(new_hyp)
            nbest_hyps.append(n_nbest_hyps)
        return nbest_hyps

    def ignored_target_position_is_0(self, padded_targets, ignore_id):
        mask = torch.ne(padded_targets, ignore_id)
        mask = mask.unsqueeze(dim=1)
        T = padded_targets.size(-1)
        upper_tri_0_mask = self.upper_triangular_is_0(T).unsqueeze(0).to(mask.dtype)
        upper_tri_0_mask = upper_tri_0_mask.to(mask.dtype).to(mask.device)
        return mask.to(torch.uint8) & upper_tri_0_mask.to(torch.uint8)

    def upper_triangular_is_0(self, size):
        ones = torch.ones(size, size)
        tri_left_ones = torch.tril(ones)
        return tri_left_ones.to(torch.uint8)

    def set_finished_beam_score_to_zero(self, scores, is_finished):
        NB, B = scores.size()
        is_finished = is_finished.float()
        mask_score = torch.tensor([0.0] + [-self.INF]*(B-1)).float().to(scores.device)
        mask_score = mask_score.view(1, B).repeat(NB, 1)
        return scores * (1 - is_finished) + mask_score * is_finished

    def set_finished_beam_y_to_eos(self, ys, is_finished):
        is_finished = is_finished.long()
        return ys * (1 - is_finished) + self.eos_id * is_finished

    def get_ys_lengths(self, ys):
        N, B, Tmax = ys.size()
        ys_lengths = torch.sum(torch.ne(ys, self.eos_id), dim=-1)
        return ys_lengths.int()



class DecoderLayer(nn.Module):
    def __init__(self, d_model, n_head, dropout):
        super().__init__()
        self.self_attn_norm = nn.LayerNorm(d_model)
        self.self_attn = DecoderMultiHeadAttention(d_model, n_head, dropout)

        self.cross_attn_norm = nn.LayerNorm(d_model)
        self.cross_attn = DecoderMultiHeadAttention(d_model, n_head, dropout)

        self.mlp_norm = nn.LayerNorm(d_model)
        self.mlp = PositionwiseFeedForward(d_model, d_model*4, dropout)

    def forward(self, dec_input, enc_output, self_attn_mask, cross_attn_mask,
                cache=None):
        x = dec_input
        residual = x
        x = self.self_attn_norm(x)
        if cache is not None:
            xq = x[:, -1:, :]
            residual = residual[:, -1:, :]
            self_attn_mask = self_attn_mask[:, -1:, :]
        else:
            xq = x
        x = self.self_attn(xq, x, x, mask=self_attn_mask)
        x = residual + x

        residual = x
        x = self.cross_attn_norm(x)
        x = self.cross_attn(x, enc_output, enc_output, mask=cross_attn_mask)
        x = residual + x

        residual = x
        x = self.mlp_norm(x)
        x = residual + self.mlp(x)

        if cache is not None:
            x = torch.cat([cache, x], dim=1)

        return x


class DecoderMultiHeadAttention(nn.Module):
    def __init__(self, d_model, n_head, dropout=0.1):
        super().__init__()
        self.d_model = d_model
        self.n_head = n_head
        self.d_k = d_model // n_head

        self.w_qs = nn.Linear(d_model, n_head * self.d_k)
        self.w_ks = nn.Linear(d_model, n_head * self.d_k, bias=False)
        self.w_vs = nn.Linear(d_model, n_head * self.d_k)

        self.attention = DecoderScaledDotProductAttention(
            temperature=self.d_k ** 0.5)
        self.fc = nn.Linear(n_head * self.d_k, d_model)
        self.dropout = nn.Dropout(dropout)

    def forward(self, q, k, v, mask=None):
        bs = q.size(0)

        q = self.w_qs(q).view(bs, -1, self.n_head, self.d_k)
        k = self.w_ks(k).view(bs, -1, self.n_head, self.d_k)
        v = self.w_vs(v).view(bs, -1, self.n_head, self.d_k)
        q = q.transpose(1, 2)
        k = k.transpose(1, 2)
        v = v.transpose(1, 2)

        if mask is not None:
            mask = mask.unsqueeze(1)

        output = self.attention(q, k, v, mask=mask)

        output = output.transpose(1, 2).contiguous().view(bs, -1, self.d_model)
        output = self.fc(output)
        output = self.dropout(output)

        return output


class DecoderScaledDotProductAttention(nn.Module):
    def __init__(self, temperature):
        super().__init__()
        self.temperature = temperature
        self.INF = float("inf")

    def forward(self, q, k, v, mask=None):
        attn = torch.matmul(q, k.transpose(2, 3)) / self.temperature
        if mask is not None:
            mask = mask.eq(0)
            attn = attn.masked_fill(mask, -self.INF)
            attn = torch.softmax(attn, dim=-1).masked_fill(mask, 0.0)
        else:
            attn = torch.softmax(attn, dim=-1)
        output = torch.matmul(attn, v)
        return output


class PositionwiseFeedForward(nn.Module):
    def __init__(self, d_model, d_ff, dropout=0.1):
        super().__init__()
        self.w_1 = nn.Linear(d_model, d_ff)
        self.act = nn.GELU()
        self.w_2 = nn.Linear(d_ff, d_model)
        self.dropout = nn.Dropout(dropout)

    def forward(self, x):
        output = self.w_2(self.act(self.w_1(x)))
        output = self.dropout(output)
        return output


class PositionalEncoding(nn.Module):
    def __init__(self, d_model, max_len=5000):
        super().__init__()
        assert d_model % 2 == 0
        pe = torch.zeros(max_len, d_model, requires_grad=False)
        position = torch.arange(0, max_len).unsqueeze(1).float()
        div_term = torch.exp(torch.arange(0, d_model, 2).float() *
                             -(torch.log(torch.tensor(10000.0)).item()/d_model))
        pe[:, 0::2] = torch.sin(position * div_term)
        pe[:, 1::2] = torch.cos(position * div_term)
        pe = pe.unsqueeze(0)
        self.register_buffer('pe', pe)

    def forward(self, x):
        length = x.size(1)
        return self.pe[:, :length].clone().detach()


class CTC(torch.nn.Module):
    def __init__(self, odim, encoder_output_size):
        super().__init__()
        self.ctc_lo = torch.nn.Linear(encoder_output_size, odim)

    def forward(self, encoder_output_pad):
        """encoder_output_pad: (N, T, H)"""
        return F.log_softmax(self.ctc_lo(encoder_output_pad), dim=2)

    @classmethod
    def ctc_alignment_to_timestamp(cls, ys_with_blank, subsampling, blank_id=0):
        start_times: List[float] = []
        end_times: List[float] = []
        frame_shift = 10 # ms, hard code
        T = len(ys_with_blank)
        t = 0
        ctc_durs = []
        while t < T:
            token = ys_with_blank[t]
            t += 1
            if token != blank_id:
                start_t = t
                timestamp = frame_shift * subsampling * t / 1000.0 # s
                start_times.append(timestamp)
                if len(start_times) == len(end_times) + 2:
                    end_times.append(start_times[-1])
                # skip repeat token
                while t < T and token == ys_with_blank[t]:
                    t += 1
                assert t-start_t >= 0
                ctc_durs.append((t-start_t+1) * frame_shift * subsampling / 1000.0)
        end_times.append((frame_shift * subsampling * T + 25)/ 1000.0)
        if len(start_times) == 0:
            start_times.append(0.0)

        # Refine end_times
        assert len(ctc_durs) == len(end_times) and len(start_times) == len(end_times)
        avg_dur = sum(e-s for s, e in zip(start_times, end_times)) / len(end_times)
        new_end_times = []
        for s, e, ctc_dur in zip(start_times, end_times, ctc_durs):
            if e - s > 2 * avg_dur:
                e = s + max(1.5*avg_dur, ctc_dur)
            new_end_times.append(round(e, 3))
        end_times = new_end_times
        return start_times, end_times


class FireRedAsrAed(torch.nn.Module):
    @classmethod
    def from_args(cls, args):
        return cls(args)

    def __init__(self, args):
        super().__init__()
        self.sos_id = args.sos_id
        self.eos_id = args.eos_id

        self.encoder = ConformerEncoder(
            args.idim, args.n_layers_enc, args.n_head, args.d_model,
            args.residual_dropout, args.dropout_rate,
            args.kernel_size, args.pe_maxlen)

        self.decoder = TransformerDecoder(
            args.sos_id, args.eos_id, args.pad_id, args.odim,
            args.n_layers_dec, args.n_head, args.d_model,
            args.residual_dropout, args.pe_maxlen)

        self.ctc = CTC(args.odim, args.d_model)

    def transcribe(self, padded_input, input_lengths,
                   beam_size=1, nbest=1, decode_max_len=0,
                   softmax_smoothing=1.0, length_penalty=0.0, eos_penalty=1.0,
                   return_timestamp=False, elm=None, elm_weight=0.0):
        enc_outputs, enc_lengths, enc_mask = self.encoder(padded_input, input_lengths)
        nbest_hyps = self.decoder.batch_beam_search(
            enc_outputs, enc_mask,
            beam_size, nbest, decode_max_len,
            softmax_smoothing, length_penalty, eos_penalty,
            elm, elm_weight)
        if return_timestamp:
            nbest_hyps = self.get_token_timestamp(enc_outputs, enc_lengths, nbest_hyps)
        return nbest_hyps

    def get_token_timestamp(self, enc_outputs, enc_lengths, nbest_hyps):
        ctc_logits = self.ctc(enc_outputs).float().cpu().numpy()
        for n in range(enc_outputs.size(0)):
            hyp = nbest_hyps[n][0]
            y = [int(v) for v in hyp["yseq"].cpu().tolist() if int(v) != 0]   # 0 is blank
            hyp["timestamp"] = self.ctc_timestamp(ctc_logits[n, :int(enc_lengths[n])], y)
        return nbest_hyps

    def ctc_timestamp(self, logp, y):
        """(đầu, cuối) từng mã chữ theo căn CTC trên đầu ra bộ mã hoá; None nếu không căn được."""
        if not y or logp.shape[0] == 0:
            return None
        alignment = ctc_forced_align(logp, y, blank=0)
        if not alignment:
            return None
        return self.ctc.ctc_alignment_to_timestamp(alignment, self.encoder.input_preprocessor.subsampling, blank_id=0)

    @torch.no_grad()
    def align(self, padded_input, input_lengths, batch_ids):
        """Căn lời có sẵn (mã chữ từng câu) với âm thanh bằng nhánh CTC -> [(starts, ends) | None]."""
        enc_outputs, enc_lengths, _ = self.encoder(padded_input, input_lengths)
        ctc_logits = self.ctc(enc_outputs).float().cpu().numpy()
        return [self.ctc_timestamp(ctc_logits[n, :int(enc_lengths[n])], [int(v) for v in ids if int(v) != 0])
                for n, ids in enumerate(batch_ids)]
