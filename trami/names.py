"""Tên riêng cho bộ nghe (2.1.0).

Bộ nghe hay sai TÊN (nhân vật, môn phái, chiêu thức…) vì tên không có trong từ điển: "林北" -> "临北" / "林贝". Ba cách:
  1. Gợi ý trước khi nghe (`context_text`): tên trong bảng thuật ngữ (Cài đặt + dự án) + hồ sơ phim + tiêu đề / mô tả
     video (lúc tải từ link) -> lời nhắc của Qwen3-ASR (mô hình nhận ngữ cảnh dạng chữ bất kỳ).
  2. Nghe lại câu nghi sai tên (`candidates`): chữ đọc giống hệt 1 tên (cùng âm pinyin) mà viết khác -> nghe lại câu đó
     kèm gợi ý tên (pipeline.name_pass).
  3. Sửa theo âm đọc (`fix`): nghe lại vẫn chưa ra tên -> thay đúng chỗ đọc giống tên. Tên 2 chữ cần trùng cả thanh điệu
     và chỗ bị thay không phải từ thông dụng (từ điển jieba) – tránh đổi nhầm từ thường thành tên.
"""
from __future__ import annotations

import re

CJK = re.compile(r"[㐀-鿿]")
RUN = re.compile(r"[㐀-鿿]{2,8}")
COMMON_FREQ = 300     # chỗ bị thay là từ có tần suất jieba ≥ mức này -> từ thường, không đổi thành tên 2 chữ
MAX_NAMES = 80


def _glossary_keys(text: str) -> list[str]:
    out = []
    for line in (text or "").splitlines():
        k = line.split("=", 1)[0].strip()
        if RUN.fullmatch(k) and k not in out:
            out.append(k)
    return out


def _profile_names(profile: str) -> list[str]:
    """Tên trong hồ sơ phim (mục Nhân vật / Tên riêng): mọi cụm 2–6 chữ Hán đứng đầu mỗi dòng gạch đầu dòng."""
    out, on = [], False
    for line in (profile or "").splitlines():
        if line.rstrip().endswith(":"):
            on = line.startswith(("Nhân vật", "Tên riêng", "Máy có thể nghe nhầm"))
            continue
        if on and line.lstrip().startswith("-"):
            body = line.lstrip()[1:].strip()
            if "=" in body:              # "- 林北 = Lâm Bắc"
                body = body.split("=", 1)[0]
            for m in RUN.finditer(body[:24]):
                if len(m.group(0)) <= 6 and m.group(0) not in out:
                    out.append(m.group(0))
                break                    # chỉ cụm đầu tiên (tên), không lấy chữ Hán trong lời mô tả
    return out


def names_of(p, settings: dict) -> list[str]:
    """Tên riêng tiếng Trung đã biết của dự án (dài trước – tên dài khớp trước tên ngắn nằm trong nó)."""
    from .pipeline import glossary_key, target_lang

    keys: list[str] = []
    for txt in (settings.get("glossary", ""), p.data.get("glossary", ""),
                p.data.get(glossary_key(target_lang(p, settings)), "")):
        keys += [k for k in _glossary_keys(txt) if k not in keys]
    keys += [k for k in _profile_names(p.data.get("film_profile", "")) if k not in keys]
    return sorted(keys, key=len, reverse=True)[:MAX_NAMES]


def _han(text: str, n: int) -> str:
    return "".join(CJK.findall(text or ""))[:n]


def context_text(p, settings: dict, names: list[str] | None = None) -> str:
    """Lời nhắc cho Qwen3-ASR: tên riêng + tiêu đề / mô tả / thẻ của video (chỉ phần có chữ Hán)."""
    names = names_of(p, settings) if names is None else names
    meta = p.data.get("video_meta") or {}
    parts = []
    if names:
        parts.append("热词：" + "、".join(names[:MAX_NAMES]))
    title = str(meta.get("title") or "").strip()
    if CJK.search(title):
        parts.append("视频标题：" + title[:100])
    desc = re.sub(r"\s+", " ", str(meta.get("description") or "")).strip()
    if len(CJK.findall(desc)) >= 4:
        parts.append("简介：" + desc[:150])
    tags = [t for t in (meta.get("tags") or []) if CJK.search(str(t))][:10]
    if tags:
        parts.append("标签：" + "、".join(tags))
    return "\n".join(parts)


def _pinyin(chars: str) -> tuple[list[str], list[str]] | None:
    try:
        from pypinyin import Style, lazy_pinyin
    except ImportError:
        return None
    return lazy_pinyin(chars, style=Style.TONE3, neutral_tone_with_five=True), lazy_pinyin(chars, style=Style.NORMAL)


def _common(word: str) -> bool:
    try:
        import jieba

        jieba.initialize()
        return int(jieba.dt.FREQ.get(word, 0) or 0) >= COMMON_FREQ
    except Exception:  # noqa: BLE001
        return False


def candidates(text: str, names: list[str], loose: bool = False) -> list[tuple[int, int, str]]:
    """Chỗ trong câu đọc giống 1 tên mà viết khác: [(vị trí đầu, vị trí cuối (chỉ số trong text), tên)].
    loose: chỉ cần cùng âm (bỏ qua thanh điệu) – để chọn câu NGHE LẠI (bộ nghe tự quyết), không dùng để thay thẳng.
    Không có pypinyin -> []."""
    if not names or not CJK.search(text or ""):
        return []
    pos = [i for i, ch in enumerate(text) if CJK.match(ch)]
    han = "".join(text[i] for i in pos)
    py = _pinyin(han)
    if py is None:
        return []
    toned, plain = py
    if len(toned) != len(han):
        return []
    out: list[tuple[int, int, str]] = []
    used = [False] * len(han)
    for name in names:                       # dài trước
        npy = _pinyin(name)
        if npy is None or len(npy[0]) != len(name):
            continue
        n = len(name)
        for k in range(len(han) - n + 1):     # chỗ đã đúng tên: không sửa, không để tên khác thay vào trong
            if han[k:k + n] == name:
                for j in range(k, k + n):
                    used[j] = True
        for k in range(0, len(han) - n + 1):
            if any(used[k:k + n]):
                continue
            if pos[k + n - 1] - pos[k] != n - 1:   # chữ phải liền nhau (không vắt qua dấu câu)
                continue
            same_tone = toned[k:k + n] == npy[0]
            if not (same_tone or ((n >= 3 or loose) and plain[k:k + n] == npy[1])):
                continue
            if n == 2 and ((not same_tone and not loose) or _common(han[k:k + n])):
                continue
            out.append((pos[k], pos[k + n - 1] + 1, name))
            for j in range(k, k + n):
                used[j] = True
    return sorted(out)


def apply(text: str, cands: list[tuple[int, int, str]]) -> str:
    for a, b, name in sorted(cands, reverse=True):
        text = text[:a] + name + text[b:]
    return text


def fix(segs, names: list[str], rows: list[int] | None = None) -> list[int]:
    """Sửa chỗ đọc giống tên (cùng số chữ -> mốc từng chữ vẫn đúng). Trả số thứ tự các câu đã sửa."""
    changed = []
    for i, s in enumerate(segs):
        if rows is not None and i not in rows:
            continue
        c = candidates(s.text, names)
        if not c:
            continue
        old = s.text
        s.text = apply(old, c)
        s.extra.setdefault("src_before", old)
        s.extra["name_fix"] = [[old[a:b], n] for a, b, n in c]
        changed.append(i)
    return changed
