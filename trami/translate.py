"""Dịch phụ đề Trung -> Việt (hoặc Anh / Thái / Nhật / Hàn – ngôn ngữ đích, xem trami/langs.py).

Hỗ trợ:
  - google    : miễn phí, không cần key (deep-translator)
  - openai    : OpenAI hoặc bất kỳ API tương thích OpenAI (base_url tuỳ chỉnh)
  - anthropic : Claude
  - gemini    : Google Gemini (qua endpoint tương thích OpenAI)
  - deepseek  : DeepSeek (rất giỏi tiếng Trung, giá rẻ)
  - ollama    : chạy mô hình offline trên máy (VD qwen2.5)
  - hymt      : Hy-MT2 (Tencent) – mô hình chuyên dịch chạy trên máy (GPU / CPU), miễn phí, không cần mạng sau khi tải

Với các engine AI: dịch theo lô có ngữ cảnh, tự trích tên riêng -> Hán Việt để thống nhất,
bắt buộc trả về JSON đúng số dòng, tự thử lại và chia nhỏ lô khi lỗi.
"""
from __future__ import annotations

import json
import re
import threading
import time
import unicodedata
from typing import Callable

import requests

from . import genres, langs, net
from .ffmpeg import Cancelled
from .subtitle import Segment
from .tts import VieneuWorker

ProgressCB = Callable[[float, str], None]

# Thể loại phim (form dịch theo thể loại) nằm ở trami/genres.py – mã cũ giữ nguyên (auto / co_trang / hien_dai / review /
# hoat_hinh / tai_lieu). STYLES giữ để mã cũ tra tên / chữ.
STYLES = {g["id"]: g["vi"] for g in genres.GENRES}
STYLES_EN = {g["id"]: g["en"] for g in genres.GENRES}

# 2.1.13: luật chung viết lại theo yêu cầu người dùng – đúng số dòng, đọc 2 dòng trước / sau để dịch đúng nghĩa, KHÔNG tự
# thêm từ / ý (trước đây luật 8 khuyến khích thêm từ đệm "à, nhé, đấy…" cho đủ dài), kiểm tra đúng dòng bằng "dau".
SYSTEM_PROMPT = """Bạn là chuyên gia biên dịch phụ đề phim từ tiếng Trung sang tiếng Việt với hơn 15 năm kinh nghiệm. Bản dịch
dùng làm phụ đề và để LỒNG TIẾNG.
Luật bắt buộc:
1. ĐÚNG SỐ DÒNG: mỗi dòng gốc dịch thành đúng MỘT dòng, cùng số thứ tự. KHÔNG gộp dòng, KHÔNG tách dòng, KHÔNG bỏ dòng,
   KHÔNG để trống dòng nào, KHÔNG chuyển chữ / ý của dòng này sang dòng khác. Một câu bị cắt ngang thành 2 dòng: phần nào
   ở dòng nào thì dịch ở dòng đó (dòng trước kết thúc dở dang bằng dấu phẩy), đọc nối 2 dòng vẫn liền mạch. Dòng chỉ có
   tiếng cảm thán (嗯, 啊, 哦, 哈哈) vẫn dịch (Ừm, À, Ờ, Haha).
2. ĐỌC NGỮ CẢNH: trước khi dịch mỗi dòng, đọc ít nhất 2 dòng TRƯỚC và 2 dòng SAU nó (dùng cả phần NGỮ CẢNH TRƯỚC / SAU)
   để biết ai đang nói với ai, đâu là câu hỏi – câu đáp, "nó / cái này / chuyện đó" chỉ cái gì, nhân vật đang vui / giận /
   sợ. Bản dịch phải khớp nghĩa với các dòng đó: câu đáp trả lời đúng câu hỏi, xưng hô khớp dòng trước – sau, không mâu
   thuẫn mạch chuyện.
3. SÁT NGHĨA – KHÔNG THÊM, KHÔNG BỚT: dịch đủ mọi ý của dòng gốc, không bỏ ý. KHÔNG tự thêm từ, cụm từ, tiếng cảm thán, từ
   đệm, lời giải thích, chú thích, ngoặc hay ý nào mà dòng gốc không có. Chỉ được thêm chủ ngữ / xưng hô / từ nối khi
   tiếng Việt bắt buộc phải có cho đúng ngữ pháp và ý đó đã ngầm có trong câu gốc. Không dịch word-by-word: câu tự nhiên
   như người Việt nói, đúng sắc thái của câu gốc (lịch sự, mỉa mai, giận dữ, đùa…).
4. Thành ngữ, tiếng lóng, từ mạng: dùng cách nói tiếng Việt tương đương, cùng nghĩa, cùng sắc thái.
5. Xưng hô đúng quan hệ, tuổi tác, địa vị, tình huống; nhất quán cả phim (theo THỂ LOẠI PHIM và THÔNG TIN PHIM).
6. Tên người, địa danh, tổ chức, chiêu thức: phiên âm Hán Việt chuẩn (李逍遥 -> Lý Tiêu Dao); địa danh có tên tiếng Việt quen
   dùng thì dùng tên đó (北京 -> Bắc Kinh); thương hiệu / người nước ngoài: tên quốc tế (微信 -> WeChat). Một tên chỉ một cách
   dịch. Tuân thủ tuyệt đối BẢNG THUẬT NGỮ. Thuật ngữ trong THỂ LOẠI PHIM chỉ dùng khi dòng gốc có đúng chữ đó.
7. Dòng gốc do máy nghe có thể sai vài chữ đồng âm: hiểu theo ngữ cảnh (luật 2) và dịch đúng ý.
8. ĐỘ DÀI: ký hiệu ⟪≤N tiếng⟫ cuối dòng là giới hạn để giọng LỒNG TIẾNG vừa thời gian nhân vật nói: bản dịch KHÔNG vượt N
   tiếng (âm tiết). Dài quá thì chọn từ / cách nói ngắn hơn mà VẪN ĐỦ Ý – không bỏ ý để cho ngắn. Câu dịch ngắn thì để
   ngắn, KHÔNG thêm từ cho dài ra.{fill}
   KHÔNG chép ký hiệu ⟪⟫ vào bản dịch.
9. Dòng chắc chắn là LỜI BÀI HÁT (nhạc phim, nhân vật đang hát – câu văn vần, điệp khúc lặp lại, không trả lời / không
   nối tiếp lời thoại trước – sau) thì bắt đầu bản dịch bằng "♪ ". Lời thoại bình thường KHÔNG thêm ♪.
10. Dấu phẩy / dấu câu GIỮA dòng gốc là chỗ nhân vật ngắt hơi: bản dịch đặt dấu phẩy (hoặc ? ! .) ở chỗ tương ứng, giữ
   số vế và thứ tự các vế như câu gốc (vế 1 dịch vế 1, vế 2 dịch vế 2…) để giọng lồng ngắt nghỉ khớp nhân vật. Dấu phẩy
   chỉ đặt ở RANH GIỚI TỰ NHIÊN của tiếng Việt (giữa 2 vế / 2 cụm trọn nghĩa, sau lời gọi "Anh à,", trước liên từ "nhưng,
   mà, thì, vì, nên, để…"); TUYỆT ĐỐI không chen dấu phẩy vào giữa 1 cụm từ: giữa lượng từ và danh từ ("một món, đồ"),
   danh từ và tính từ đi kèm ("món đồ, tốt"), động từ và tân ngữ, giữa các tiếng của 1 tên riêng ("Thiên Kiếm, Tông"),
   thành ngữ / từ ghép. Vế tiếng Việt không khớp đúng chỗ thì đặt dấu phẩy ở ranh giới tự nhiên gần nhất, hoặc bớt 1
   dấu phẩy – câu đọc trôi chảy quan trọng hơn đủ số vế.
11. Chỗ NÓI VẤP / nói dở rồi nói lại (vế trước là vế sau bị bỏ dở: 我不管闲，我不管闲事 = 我不管闲事; 你还没我一，你还没
   我一半 = 你还没我一半) hoặc 1 cụm bị lặp vô nghĩa (以后所有以后所有的货): chỉ dịch 1 lần, không lặp lại trong bản dịch
   (luật 10 không áp cho vế bị lặp). Lặp CÓ CHỦ Ý để nhấn mạnh / gọi / giục (让一让，让一让; 龙鳅王，龙鳅王; 快走，快走)
   thì giữ lặp.
12. GIỮ DẤU CÂU CUỐI: mỗi bản dịch kết thúc bằng dấu câu hợp nghĩa – câu hỏi "?" (kể cả câu gốc hỏi bằng 吗 / 呢 / 吧 /
   难道 mà thiếu dấu ？), cảm thán / ra lệnh "!", câu kể ".", bỏ lửng "…"; dòng gốc dừng ở dấu phẩy (câu nói tiếp ở dòng
   sau) thì kết thúc bằng ",". Dấu câu cuối giúp giọng lồng lên / xuống giọng đúng.
13. Chỉ trả về JSON hợp lệ, không giải thích: {"1": {"dau": "…", "dich": "bản dịch"}, "2": {"dau": "…", "dich": "…"}, …}
   đủ MỌI số dòng được yêu cầu. "dau" = chép y nguyên 3 ký tự đầu của dòng gốc đó (bỏ dấu câu, khoảng trắng) – để kiểm
   tra bản dịch nằm đúng dòng."""

FILL_RULE_VI = """
   Ký hiệu ⟪M–N tiếng⟫: nhân vật nói lâu – bản dịch nên dài khoảng M tới N tiếng; nếu bản dịch sát nghĩa ngắn hơn hẳn M
   thì được nói đầy đủ hơn bằng câu đủ thành phần (chủ ngữ, xưng hô, từ nối, cách nói đầy đủ thay cách nói tắt) – vẫn
   KHÔNG thêm ý, KHÔNG thêm tiếng cảm thán / từ đệm vô nghĩa, không vượt N."""
FILL_RULE_EN = """
   ⟪M–N {unit}⟫: the character speaks for a while – aim for about M to N {unit}; if the faithful translation is much
   shorter than M you may use a fuller sentence (subject, form of address, connectives, full instead of clipped
   phrasing) – still NO new information, NO meaningless fillers or interjections, never above N."""
_ADDRESS = {"en": "", "th": " (use ครับ / ค่ะ and pronouns that fit each character)",
            "ja": " (choose keigo or casual speech to fit each relationship)",
            "ko": " (choose 존댓말 or 반말 to fit each relationship)"}
_NAMES = {"en": "the established English name if one exists, otherwise pinyin (e.g. 李逍遥 -> Li Xiaoyao)",
          "th": "the usual Thai transliteration of the Chinese name",
          "ja": "the Japanese reading in kanji or katakana as commonly used in Japanese releases",
          "ko": "the Korean (Sino-Korean) reading in Hangul (e.g. 李逍遥 -> 이소요)"}
SRC_NAMES = {"zh": ("tiếng Trung", "Chinese"), "yue": ("tiếng Quảng Đông", "Cantonese"), "en": ("tiếng Anh", "English"),
             "ja": ("tiếng Nhật", "Japanese"), "ko": ("tiếng Hàn", "Korean"), "auto": ("tiếng gốc", "the source language")}


def system_prompt(target: str, source: str = "zh", fill: bool = False, genre: str = "") -> str:
    """Lời nhắc hệ thống cho AI theo ngôn ngữ đích. fill: có ký hiệu ⟪M–N⟫ (Cài đặt "Câu ngắn nói đầy đủ hơn…"); genre:
    form thể loại phim (genres.form) gắn cuối."""
    target = langs.norm(target)
    src_vi, src_en = SRC_NAMES.get(source, SRC_NAMES["zh"])
    if target == "vi":
        sp = SYSTEM_PROMPT.replace("từ tiếng Trung sang", f"từ {src_vi} sang", 1).replace("{fill}", FILL_RULE_VI if fill else "")
        return sp + (f"\n\nTHỂ LOẠI PHIM (xưng hô, thuật ngữ, giọng văn):\n{genre.strip()}" if (genre or "").strip() else "")
    L = langs.INFO[target]
    fill_en = FILL_RULE_EN.format(unit=L["unit_ai"]) if fill else ""
    sp = f"""You are an expert film subtitle translator with 15+ years of experience, translating {src_en} dialogue into {L['english']}.
The translation is used for subtitles and for DUBBING.
Mandatory rules:
1. EXACT LINE COUNT: each source line becomes exactly ONE output line with the same number. NEVER merge, split, drop or
   leave a line empty, never move words / meaning to another line. A sentence cut across 2 lines: translate each part on
   its own line (the first may end unfinished with a comma) so the 2 lines still read as one. Interjection-only lines
   (嗯, 啊, 哦, 哈哈) are translated too.
2. READ THE CONTEXT: before translating each line, read at least the 2 lines BEFORE and the 2 lines AFTER it (including the
   PREVIOUS / FOLLOWING CONTEXT) to know who speaks to whom, which line answers which question, what "it / this / that"
   refers to and the mood. The translation must fit those lines: answers match the questions, forms of address match the
   surrounding lines, no contradiction with the story.
3. FAITHFUL – ADD NOTHING, DROP NOTHING: keep every piece of meaning; NEVER add words, phrases, interjections, fillers,
   explanations, notes, brackets or ideas that are not in the source line. Only add a subject / form of address /
   connective when {L['english']} grammar requires it and it is implied by the source. Not word-by-word: natural speech
   with the same tone as the source (polite, sarcastic, angry, joking…).
4. Render idioms, slang and internet expressions with natural {L['english']} equivalents of the same meaning and tone.
5. Keep forms of address consistent with the characters' relationships, age and status for the whole film{_ADDRESS[target]}
   (follow the FILM GENRE and FILM NOTES).
6. Names of people, places, organizations and techniques: use {_NAMES[target]}. One name, one rendering. ALWAYS follow
   the GLOSSARY. Terms listed in the FILM GENRE apply only when the source line contains that exact term.
7. Source lines may contain speech-recognition errors (homophones): use the context (rule 2) to get the intended meaning.
8. LENGTH: a line ending with ⟪≤N {L['unit_ai']}⟫ is a limit so the DUBBED voice fits the character's speaking time: the
   translation must NOT exceed N {L['unit_ai']}. If too long, choose shorter wording that STILL keeps all the meaning –
   never drop meaning to save length. A short translation stays short – never add words to make it longer.{fill_en}
   NEVER copy the ⟪⟫ marker into the translation.
9. A line that is clearly SONG LYRICS (theme song, a character singing – verse, not dialogue) must start with "♪ ".
   Never add ♪ to normal dialogue.
10. A comma / punctuation mark INSIDE a source line marks where the character pauses for breath: put a comma (or ? ! .)
   at the matching place in the translation, keeping the number and order of clauses (clause 1 translates clause 1,
   clause 2 translates clause 2…) so the dubbed voice pauses with the character. Put commas ONLY at natural
   {L['english']} boundaries (between clauses / complete phrases, after a vocative, before a conjunction); NEVER inside a
   phrase – between a determiner / numeral and its noun, a noun and its modifier, a verb and its object, inside a name
   or an idiom. If a clause boundary does not fall naturally, use the nearest natural boundary or drop that comma – a
   fluent line matters more than the exact clause count.
11. A STAMMER / false start (an earlier clause is an unfinished copy of the next one: 我不管闲，我不管闲事 = 我不管闲事;
   你还没我一，你还没我一半 = 你还没我一半) or a meaningless repeated chunk (以后所有以后所有的货): translate it ONCE, do
   not repeat it in the translation (rule 10 does not apply to the repeated clause). Deliberate repetition for emphasis /
   calling / urging (让一让，让一让; 龙鳅王，龙鳅王; 快走，快走) stays repeated.
12. KEEP THE FINAL PUNCTUATION: every translation ends with the fitting mark – "?" for questions (also when the source
   asks with 吗 / 呢 / 吧 / 难道 but lacks ？), "!" for exclamations / orders, a full stop for statements, "…" when it
   trails off; a source line that stops at a comma (the sentence continues on the next line) ends with a comma. The
   final mark lets the dubbed voice rise / fall correctly{" (Thai: no full stop, keep ? and !)" if target == "th" else ""}.
13. Return ONLY valid JSON, no explanation: {{"1": {{"head": "…", "text": "translation"}}, "2": {{"head": "…", "text": "…"}}, …}}
   with EVERY requested line number. "head" = copy exactly the first 3 characters of that source line (without
   punctuation or spaces) – used to check that each translation sits on the right line."""
    return sp + (f"\n\nFILM GENRE (forms of address, terminology, tone):\n{genre.strip()}" if (genre or "").strip() else "")


SYL_PER_SEC = 4.6      # tiếng Việt lồng tiếng tự nhiên ~4–5 tiếng/giây (tiếng khác: langs.INFO[...]["rate"])
BUDGET_RE = re.compile(r"\s*⟪[^⟫]*⟫\s*")
HAN_RE = re.compile(r"[㐀-鿿]")

SHORTEN_PROMPT = """Bạn là biên tập viên lồng tiếng phim. Mỗi mục gồm câu gốc tiếng Trung, bản dịch tiếng Việt hiện tại và
số tiếng (âm tiết) tối đa. Hãy viết lại bản dịch NGẮN HƠN, không vượt số tiếng tối đa, vẫn tự nhiên, giữ đúng ý chính,
giữ nguyên xưng hô và tên riêng. Chỉ trả về JSON {"1": "câu mới", ...} không kèm giải thích."""


SHORTEN_PROMPT_EN = """You are a film dubbing script editor. Each item has the source line, the current {lang} translation and
the maximum length in {unit}. Rewrite the translation SHORTER, within the maximum length, still natural, keeping the core
meaning, names and forms of address. Return ONLY JSON {{"1": "new line", ...}} with no explanation."""


CLAUSE_PROMPT = """Bạn là biên tập viên lồng tiếng phim. Mỗi mục có câu gốc đã chia thành các VẾ theo chỗ nhân vật ngắt hơi
("ve_goc") và bản dịch tiếng Việt hiện tại ("dich"). Viết lại bản dịch cho có ĐÚNG số vế như câu gốc: các vế nối nhau bằng
dấu phẩy (hoặc ? ! nếu vế gốc là câu hỏi / cảm thán), vế thứ i dịch nghĩa vế gốc thứ i, đúng thứ tự – để giọng lồng ngắt
nghỉ đúng lúc nhân vật ngắt hơi. Mỗi vế phải là 1 cụm TRỌN NGHĨA đọc lên tự nhiên: không chen dấu phẩy giữa lượng từ và
danh từ, danh từ và tính từ, động từ và tân ngữ, giữa các tiếng của tên riêng / thành ngữ – không làm được thì giữ nguyên
bản dịch cũ. Giữ nguyên ý, xưng hô, tên riêng, dấu câu cuối, độ dài gần như cũ; chỉ đổi trật tự / cách nói khi cần.
Chỉ trả về JSON {"1": "bản dịch mới", ...} không kèm giải thích."""

CLAUSE_PROMPT_EN = """You are a film dubbing script editor. Each item has the source line split into CLAUSES where the
character pauses for breath ("source_clauses") and the current {lang} translation ("translation"). Rewrite the translation
so it has EXACTLY the same number of clauses: join them with commas (or ? ! when the source clause is a question /
exclamation), clause i translates source clause i, in order – so the dubbed voice pauses with the character. Each clause
must be a COMPLETE, natural phrase: never put a comma between a determiner / numeral and its noun, a noun and its modifier,
a verb and its object, or inside a name / idiom – if that is impossible, return the current translation unchanged. Keep the
meaning, forms of address, names, final punctuation and about the same length; only reorder / rephrase when needed.
Return ONLY JSON {{"1": "new line", ...}} with no explanation."""


# Dấu câu bản dịch (2.1.11) ------------------------------------------------------------------------------------
_CLOSERS = "\"'”’」』)）]»"
_ENDS = set(".?!…。？！,，、;；:：~～-–—♪")
# chữ luôn đi liền với chữ sau (lượng từ / số lượng / "của"): dấu phẩy ngay sau chúng là chen giữa cụm từ
COMMA_GLUE = set("một những các mấy mỗi mọi chiếc món quyển cuốn bức tấm ngôi căn sợi của".split())
_NUMERAL = set("một hai ba bốn năm sáu bảy tám chín mười mươi trăm nghìn ngàn vạn triệu tỷ linh lẻ rưỡi".split())
_END_MAP = {"vi": (".", "?", "!", ","), "en": (".", "?", "!", ","), "ko": (".", "?", "!", ","),
            "ja": ("。", "？", "！", "、"), "th": ("", "?", "!", "")}


def end_punct(trans: str, src: str, lang: str = "vi") -> str:
    """Bản dịch thiếu dấu câu cuối -> thêm theo câu gốc: ？ -> "?", ！ -> "!", … -> "…", câu gốc dừng ở dấu phẩy (vế
    nói tiếp ở dòng sau) -> ",", còn lại "." (câu gốc tận cùng 吗 / 么 -> "?"). Tiếng Thái không dùng dấu chấm câu."""
    t = (trans or "").rstrip()
    core = t.rstrip(_CLOSERS + " ")
    if not core or core[-1] in _ENDS or core.lstrip().startswith("♪") or unicodedata.category(core[-1])[0] not in "LNM":
        return trans                     # đã có dấu câu / lời hát / tận cùng bằng biểu tượng cảm xúc, ký hiệu
    s = re.sub(r"[\s" + re.escape(_CLOSERS) + r"♪]+$", "", src or "")
    last = s[-1:]
    dot, q, ex, comma = _END_MAP.get(langs.norm(lang), _END_MAP["vi"])
    if last in "？?" or re.search(r"[吗么][。.]?$", s):
        mark = q
    elif last in "！!":
        mark = ex
    elif last == "…" or s.endswith("..."):
        mark = "…"
    elif last in "，,、；;：:":
        mark = comma
    else:
        mark = dot
    return core + mark + t[len(core):] if mark else trans


def fix_commas(trans: str, names: list[str] | tuple = ()) -> str:
    """Bỏ dấu phẩy chen GIỮA 1 cụm từ (AI cố đặt đủ số vế như câu gốc): sau lượng từ / "của" mà chữ sau viết thường
    ("một món, đồ tốt" -> "một món đồ tốt"; giữ "một, hai, ba"), và giữa các tiếng của 1 tên riêng trong bảng thuật ngữ
    ("Thiên Kiếm, Tông")."""
    t = trans or ""
    if "," not in t:
        return t

    def glue(m: re.Match) -> str:
        w, nxt = m.group(1), m.group(2)
        if w.lower() in COMMA_GLUE and nxt[:1].islower() and nxt.lower() not in _NUMERAL:
            return f"{w} {nxt}"
        return m.group(0)
    t = re.sub(r"(\w+),\s+(\w+)", glue, t)
    for name in names or ():
        parts = name.split()
        if len(parts) >= 2:
            pat = r",?\s+".join(re.escape(x) for x in parts)
            t = re.sub(pat, lambda _m, n=name: n, t)
    return t


def punct_fix(trans: str, src: str = "", lang: str = "vi", names: list[str] | tuple = ()) -> str:
    """Dọn dấu câu bản dịch: dấu phẩy chen sai chỗ (tiếng Việt) + dấu câu cuối theo câu gốc."""
    if not trans:
        return trans
    if langs.norm(lang) == "vi":
        trans = fix_commas(trans, names)
    return end_punct(trans, src, lang) if src else trans


def syllables(text: str, lang: str = "vi") -> int:
    """Độ dài lời: số tiếng / từ (Việt, Anh), số chữ (Thái, Nhật, Hàn)."""
    return langs.units(text, lang)


def budget_for(duration: float, speedup: float = 1.0, lang: str = "vi") -> int:
    """Độ dài lời tối đa nói vừa trong `duration` giây (cho phép tăng tốc nhẹ `speedup`) – theo tiếng."""
    return langs.budget(duration, lang, speedup)


FILL_MIN = 0.75     # dịch vừa thời lượng: câu nên dài ≥ 75 % số tiếng nói vừa thời gian nhân vật nói
FILL_FROM = 4       # chỉ nhắc độ dài tối thiểu khi chỗ trống đủ cho ≥ 4 tiếng (câu ngắn "Được.", "Hả?" giữ nguyên)


def length_marker(duration: float, lang: str, unit: str, fill: bool = True) -> str:
    """Ký hiệu độ dài gắn sau câu gửi AI: ⟪≤N⟫ (tối đa, cho phép nhanh 10 %) hoặc ⟪M–N⟫ khi bật "Câu ngắn nói đầy đủ
    hơn" – câu dịch ngắn hơn hẳn thời gian nói thì AI dùng câu đủ thành phần (không thêm ý, không thêm từ đệm)."""
    n = budget_for(duration, 1.1, lang)
    m = int(budget_for(duration, 1.0, lang) * FILL_MIN)
    if fill and m >= FILL_FROM and m < n:
        return f"⟪{m}–{n} {unit}⟫"
    return f"⟪≤{n} {unit}⟫"


KANA_RE = re.compile(r"[\u3040-\u30FF]")


def untranslated(text: str, lang: str = "vi") -> bool:
    """Bản dịch rỗng hoặc vẫn còn chữ Hán (AI chép lại câu gốc / bỏ sót) -> coi như chưa dịch. Dịch sang tiếng Nhật thì
    chữ Hán là bình thường: chỉ coi là chưa dịch khi không có chữ kana nào mà có ≥ 3 chữ Hán (chép nguyên câu Trung)."""
    t = (text or "").strip()
    if not t:
        return True
    if langs.norm(lang) == "ja":
        return not KANA_RE.search(t) and len(HAN_RE.findall(t)) >= 3
    return len(HAN_RE.findall(t)) >= 2


def cache_line(key: str) -> str:
    """Câu gốc trong 1 khoá bộ nhớ bản dịch (khoá tiếng Việt cũ "máy|kiểu|câu", tiếng khác "máy|kiểu|tiếng|câu")."""
    parts = key.split("|", 3)
    if len(parts) == 4 and parts[2] in langs.INFO:
        return parts[3]
    return key.split("|", 2)[2] if len(parts) >= 3 else key


def with_skill(system: str, skill: str) -> str:
    from .skills import with_skill as _ws

    return _ws(system, skill)


def cache_key(engine: str, style: str, target: str, line: str) -> str:
    """Khoá bộ nhớ bản dịch (tiếng Việt giữ dạng cũ để dùng lại bản dịch đã nhớ của dự án cũ)."""
    target = langs.norm(target)
    return f"{engine}|{style}|{line}" if target == "vi" else f"{engine}|{style}|{target}|{line}"


def parse_glossary(text: str) -> dict[str, str]:
    g: dict[str, str] = {}
    for line in (text or "").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        for sep in ("=", "\t", "=>", "->", ":"):
            if sep in line:
                a, b = line.split(sep, 1)
                if a.strip() and b.strip():
                    g[a.strip()] = b.strip().lstrip(">").strip()
                break
    return g


def glossary_text(g: dict[str, str]) -> str:
    return "\n".join(f"{k} = {v}" for k, v in g.items())


def _extract_json(txt: str) -> dict:
    txt = txt.strip()
    txt = re.sub(r"^```(?:json)?\s*|\s*```$", "", txt, flags=re.S)
    try:
        return json.loads(txt)
    except Exception:
        pass
    m = re.search(r"\{.*\}", txt, re.S)
    if m:
        try:
            return json.loads(m.group(0))
        except Exception:
            pass
    # dự phòng (AI trả thiếu ngoặc / bị cắt giữa chừng): lấy các dòng đã trả trọn – "1": "..." hoặc "1": {"dau", "dich"}
    out = {}
    for m in re.finditer(r'"(\d+)"\s*:\s*"((?:[^"\\]|\\.)*)"', txt):
        out[m.group(1)] = json.loads('"' + m.group(2) + '"')
    for m in re.finditer(r'"(\d+)"\s*:\s*(\{[^{}]*\})', txt):
        try:
            out[m.group(1)] = json.loads(m.group(2))
        except Exception:
            pass
    if out:
        return out
    raise ValueError("Phản hồi không phải JSON hợp lệ")


# ======================================================================= LLM clients

class ApiError(RuntimeError):
    """Không gọi được API AI (mất mạng, sai URL, hết tiền, quá tải…) – phải báo cho người dùng, không tự đổi engine."""

AI_ENGINES = ("deepseek", "openai", "anthropic", "gemini", "ollama")
ENGINE_NAMES = {"deepseek": "DeepSeek", "openai": "OpenAI", "anthropic": "Claude", "gemini": "Gemini", "ollama": "Ollama"}
# lần gửi / phút mặc định: gói miễn phí Gemini giới hạn số lần gọi mỗi phút (xem trang Rate limits của Google)
DEFAULT_RPM = {"gemini": 8}


def api_keys(settings: dict, engine: str) -> list[str]:
    """Các API key của 1 công cụ (Cài đặt `<engine>_api_keys`, cộng key cũ `<engine>_api_key`), bỏ trùng / rỗng."""
    out: list[str] = []
    for k in [settings.get(f"{engine}_api_key", "")] + list(settings.get(f"{engine}_api_keys") or []):
        k = (k or "").strip()
        if k and not k.startswith("••••") and k not in out:
            out.append(k)
    return out


BATCH_MAX = 1000    # "Số câu mỗi lần gửi" tối đa (2.1.23 – trước 200)
PARALLEL_MAX = 16   # "Gửi tất cả cùng lúc": tối đa 16 lần gửi chạy song song
TOK_LINE = 60       # token AI trả lời ước cho mỗi dòng dịch (JSON {"dau", "dich"}; tiếng Việt có dấu tốn token)
TOK_BASE = 1500     # ... + phần chung của câu trả lời
THINK_TOK = 12000   # model suy luận (gpt-5, o-series): phần suy nghĩ tính vào giới hạn trả lời -> chừa thêm


def engine_batch(settings: dict, engine: str) -> int:
    """Số câu mỗi lần gửi AI của công cụ này (khung riêng ở Cài đặt; chưa đặt -> 'Số câu mỗi lần gửi AI' cũ)."""
    return min(BATCH_MAX, max(1, int(settings.get(f"{engine}_batch") or settings.get("translate_batch") or 40)))


def engine_parallel(settings: dict, engine: str) -> bool:
    """"Gửi tất cả cùng lúc" (2.1.23): mọi lần gửi của 1 lượt dịch / kiểm tra đi song song, không chờ lần trước trả lời
    xong – gói trả phí không giới hạn dịch cả phim nhanh gấp nhiều lần."""
    return bool(settings.get(f"{engine}_parallel")) and engine not in ("google", "hymt", "none")


def run_parallel(fn: Callable, items: list, workers: int = PARALLEL_MAX):
    """Gọi fn(item) cho mọi item, tối đa `workers` lần cùng lúc; mỗi luồng giữ nút Huỷ + dòng trạng thái của tác vụ
    đang chạy (`net.task`). Trả dần (item, kết quả) theo thứ tự xong trước. 1 lần gửi lỗi không làm mất kết quả các lần
    khác: chờ xong hết rồi mới ném lỗi đầu tiên."""
    from concurrent.futures import ThreadPoolExecutor, as_completed

    cancel, report = net.current()

    def run(it):
        with net.task(cancel, report):
            return fn(it)
    err = None
    with ThreadPoolExecutor(max_workers=max(1, min(workers, len(items))), thread_name_prefix="trami-ai") as ex:
        futs = {ex.submit(run, it): it for it in items}
        for f in as_completed(futs):
            try:
                res = f.result()
            except BaseException as e:  # noqa: BLE001 – Huỷ / lỗi API: các lần khác vẫn chạy xong, giữ kết quả
                err = err or e
                continue
            yield futs[f], res
    if err is not None:
        raise err


_OUT_CAP: dict[tuple[str, str], int] = {}   # (công cụ, model) -> giới hạn token trả lời mà API báo (học trong phiên)
_PACE_LOCK = threading.Lock()


def _token_cap(text: str, asked: int) -> int | None:
    """API từ chối vì xin trả lời dài quá giới hạn của model -> giới hạn đó ("supports at most 16384 completion tokens",
    "valid range of max_tokens is [1, 8192]", "max_tokens: 40000 > 32000, which is the maximum…")."""
    t = (text or "").lower()
    if not any(x in t for x in ("max_tokens", "max_completion_tokens", "max_output_tokens", "maxoutputtokens",
                                "output tokens", "completion tokens")):
        return None
    nums = [int(x) for x in re.findall(r"\d{3,7}", re.sub(r"(?<=\d),(?=\d{3})", "", t))]
    nums = [x for x in nums if 256 <= x < asked]
    return max(nums) if nums else None


def engine_rpm(settings: dict, engine: str) -> int:
    """Số lần gửi tối đa mỗi phút cho MỖI key (0 = không giới hạn)."""
    v = settings.get(f"{engine}_rpm")
    return max(0, int(DEFAULT_RPM.get(engine, 0) if v is None else v))


# nhớ trong phiên: key đang dùng tốt / key hỏng / các lần gửi gần đây của mỗi key (giới hạn mỗi phút)
_KEY_POS: dict[str, int] = {}
_KEY_DEAD: dict[str, tuple[float, str]] = {}   # key -> (hết hạn chặn lúc, lý do)
_KEY_CALLS: dict[str, list[float]] = {}
DAY_BLOCK = 3600.0        # key hết lượt trong ngày: 1 giờ sau mới thử lại
WAIT_LIMIT_429 = 20       # bị giới hạn mỗi phút mà chỉ có 1 key: chờ rồi gửi lại tối đa 20 lần


def _key_tag(engine: str, i: int, n: int) -> str:
    return f"key {ENGINE_NAMES.get(engine, engine)} số {i + 1}" + (f"/{n}" if n > 1 else "")


class LLMClient:
    def __init__(self, settings: dict, engine: str):
        self.engine = engine
        self.s = settings
        if engine == "anthropic":
            self.model = settings.get("anthropic_model") or "claude-opus-5-5"
        elif engine == "gemini":
            self.model = settings.get("gemini_model") or "gemini-2.5-flash"
            self.base = "https://generativelanguage.googleapis.com/v1beta/openai"
        elif engine == "deepseek":
            self.model = settings.get("deepseek_model") or "deepseek-chat"
            self.base = "https://api.deepseek.com/v1"
        elif engine == "ollama":
            self.model = settings.get("ollama_model") or "qwen2.5:7b"
            self.base = (settings.get("ollama_base_url") or "http://localhost:11434/v1").rstrip("/")
        else:
            self.model = settings.get("openai_model") or "gpt-4o-mini"
            self.base = (settings.get("openai_base_url") or "https://api.openai.com/v1").rstrip("/")
        self.keys = ["ollama"] if engine == "ollama" else api_keys(settings, engine)
        if not self.keys:
            raise ValueError(f"Chưa nhập API key cho {ENGINE_NAMES.get(engine, engine)}. Vào trang Cài đặt → khung "
                             f"{ENGINE_NAMES.get(engine, engine)} để nhập.")
        self.ki = _KEY_POS.get(engine, 0) % len(self.keys)
        self.rpm = 0 if engine == "ollama" else engine_rpm(settings, engine)
        self._json_mode = True
        # model OpenAI đời mới (gpt-5…, o1/o3/o4…) chỉ nhận max_completion_tokens; model khác / API khác: max_tokens
        self._max_key = ("max_completion_tokens" if engine == "openai" and "api.openai.com" in self.base
                         and re.match(r"(gpt-5|o\d)", self.model or "") else "max_tokens")
        # Các model Claude đời mới (và một số model OpenAI) không nhận tham số temperature -> không gửi
        self._send_temp = engine != "anthropic"
        self._tl = threading.local()    # .cut: lần trả lời gần nhất của luồng này bị cắt vì hết giới hạn độ dài

    def out_cap(self) -> int | None:
        """Giới hạn token trả lời của model (API đã báo trong phiên này), chưa biết -> None."""
        return _OUT_CAP.get((self.engine, self.model))

    @property
    def thinking(self) -> bool:
        """Model suy luận: phần suy nghĩ tính vào giới hạn trả lời (gpt-5 / o-series, Gemini 2.5+, DeepSeek reasoner)."""
        m = (self.model or "").lower()
        return (self._max_key == "max_completion_tokens" or (self.engine == "gemini" and bool(
            re.search(r"gemini-(2\.5|[3-9])", m))) or (self.engine == "deepseek" and "reasoner" in m))

    @property
    def api_key(self) -> str:
        return self.keys[self.ki]

    # ------------------------------------------------------------------ nhiều key + giới hạn mỗi phút
    def _alive(self, i: int) -> bool:
        d = _KEY_DEAD.get(self.keys[i])
        return not d or time.time() > d[0]

    def _room(self, i: int) -> float:
        """Còn phải chờ bao nhiêu giây thì key i được gửi tiếp (0 = gửi ngay)."""
        if not self.rpm:
            return 0.0
        now = time.time()
        calls = [t for t in _KEY_CALLS.get(self.keys[i], []) if now - t < 60]
        _KEY_CALLS[self.keys[i]] = calls
        return 0.0 if len(calls) < self.rpm else 60.5 - (now - calls[0])

    def _switch(self, why: str) -> bool:
        """Đổi sang key khác còn dùng được (ưu tiên key gửi được ngay). False = không còn key nào."""
        alive = [i for i in range(len(self.keys)) if i != self.ki and self._alive(i)]
        if not alive:
            return False
        nxt = min(alive, key=lambda i: (self._room(i), (i - self.ki) % len(self.keys)))
        _, report = net.current()
        if report:
            report(f"{why} – chuyển sang {_key_tag(self.engine, nxt, len(self.keys))}")
        self.ki = nxt
        _KEY_POS[self.engine] = nxt
        return True

    def _pace(self):
        """Gói miễn phí: không gửi quá `rpm` lần / phút / key – key này hết lượt phút này thì dùng key khác, không có thì chờ.
        Nhiều lần gửi cùng lúc ("Gửi tất cả cùng lúc"): lần lượt xét qua khoá để không vượt giới hạn."""
        with _PACE_LOCK:
            self._pace_one()

    def _pace_one(self):
        if not self._alive(self.ki):
            self._switch(f"{_key_tag(self.engine, self.ki, len(self.keys))} đang bị chặn")
        wait = self._room(self.ki)
        if wait > 0:
            others = [i for i in range(len(self.keys)) if i != self.ki and self._alive(i) and self._room(i) == 0]
            if others:
                self.ki = others[0]
                _KEY_POS[self.engine] = self.ki
                wait = 0.0
        if wait > 0:
            _, report = net.current()
            if report:
                report(f"Gói miễn phí {ENGINE_NAMES.get(self.engine, self.engine)}: chờ {wait:.0f} s "
                       f"(tối đa {self.rpm} lần gửi / phút mỗi key – thêm key ở Cài đặt để dịch nhanh hơn)")
            net.sleep(wait)
        _KEY_CALLS.setdefault(self.api_key, []).append(time.time())

    @staticmethod
    def _retry_after(r) -> float:
        m = re.search(r'retry[_ -]?delay"?\s*[:=]\s*"?(\d+(?:\.\d+)?)', r.text or "", re.I) or \
            re.search(r"retry in (\d+(?:\.\d+)?)\s*s", r.text or "", re.I)
        if m:
            return min(120.0, float(m.group(1)) + 1)
        try:
            return min(120.0, float(r.headers.get("retry-after", "") or 0)) or 20.0
        except (TypeError, ValueError):
            return 20.0

    def _key_failed(self, r) -> str | None:
        """Lỗi do KEY (sai / hết hạn / hết lượt trong ngày / hết tiền) -> lý do; lỗi khác -> None."""
        t = (r.text or "").lower()
        if r.status_code in (401, 403) or (r.status_code == 400 and any(
                x in t for x in ("api key not valid", "api_key_invalid", "invalid api key", "api key expired",
                                 "expired", "invalid x-api-key"))):
            return "sai hoặc hết hạn"
        if r.status_code == 402 or "insufficient_quota" in t or "insufficient balance" in t or "credit balance" in t:
            return "hết tiền / hết hạn mức"
        if r.status_code == 429 and any(x in t for x in ("perday", "per day", "per_day", "daily", "requests per day")):
            return "hết lượt miễn phí hôm nay"
        return None

    def _http(self, url: str, headers: dict, body: dict, read: float = 300):
        # chờ AI viết xong có thể lâu (không có dữ liệu) -> chờ đọc tới 5 phút (lô dài: lâu hơn); rớt mạng thì keepalive
        # báo trong ~40 s
        return net.session().post(url, headers=headers, json=body, timeout=(15, read))

    def chat(self, system: str, user: str, max_tokens: int = 8000, temperature: float = 0.3) -> str:
        """Gửi 1 lần. max_tokens: độ dài trả lời cần (lô dịch dài xin nhiều hơn – 2.1.23); model có giới hạn thấp hơn (API
        báo 400) -> tự hạ theo đúng giới hạn đó. Trả lời bị cắt vì hết giới hạn -> `self._tl.cut` = True (theo luồng)."""
        last_err = None
        fails = 0
        waits = 0
        name = ENGINE_NAMES.get(self.engine, self.engine)
        self._tl.cut = False
        while fails < 4:
            try:
                self._pace()
                cap = self.out_cap()
                if self.engine == "anthropic":
                    ask = max(max_tokens, 16000)     # chừa chỗ cho phần suy nghĩ
                elif self.thinking:   # model suy luận: phần suy nghĩ tính vào giới hạn -> chừa chỗ
                    ask = max(16000, max_tokens + THINK_TOK)
                else:
                    ask = max_tokens
                ask = min(ask, cap) if cap else ask
                read = max(300.0, ask / 20 + 120)    # AI viết ~20+ token / giây: lô dài chờ lâu hơn 5 phút
                if self.engine == "anthropic":
                    body = {"model": self.model, "max_tokens": ask,
                            "system": system, "messages": [{"role": "user", "content": user}]}
                    if self._send_temp:
                        body["temperature"] = temperature
                    r = net.call(self._http, "https://api.anthropic.com/v1/messages",
                                 {"x-api-key": self.api_key, "anthropic-version": "2023-06-01",
                                  "content-type": "application/json"}, body, read)
                    if r.status_code == 200:
                        d = r.json()
                        self._tl.cut = d.get("stop_reason") == "max_tokens"
                        return "".join(b.get("text", "") for b in d.get("content", []) if b.get("type") == "text")
                else:
                    body = {"model": self.model,
                            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
                    body[self._max_key] = ask
                    if self._send_temp:
                        body["temperature"] = temperature
                    if self._json_mode:
                        body["response_format"] = {"type": "json_object"}
                    r = net.call(self._http, f"{self.base}/chat/completions",
                                 {"Authorization": f"Bearer {self.api_key}"}, body, read)
                    if r.status_code == 400 and self._json_mode and "response_format" in r.text:
                        self._json_mode = False
                        continue
                    if r.status_code == 400 and self._max_key == "max_tokens" and "max_completion_tokens" in r.text:
                        self._max_key = "max_completion_tokens"   # gpt-5 / o-series không nhận max_tokens
                        continue
                    if r.status_code == 200:
                        ch = r.json()["choices"][0]
                        self._tl.cut = ch.get("finish_reason") in ("length", "max_tokens")
                        return ch["message"]["content"] or ""
                if r.status_code == 400 and (_token_cap(r.text, ask) or 0) > 0:
                    _OUT_CAP[(self.engine, self.model)] = _token_cap(r.text, ask)   # model trả lời ngắn hơn -> hạ, gửi lại
                    continue
                if r.status_code == 400 and self._send_temp and "temperature" in r.text:
                    self._send_temp = False  # model không hỗ trợ temperature -> gửi lại không có nó
                    continue
                why = self._key_failed(r)
                if why:   # key hỏng / hết lượt -> đánh dấu, đổi key khác dịch tiếp
                    tag = _key_tag(self.engine, self.ki, len(self.keys))
                    _KEY_DEAD[self.api_key] = (time.time() + (DAY_BLOCK if "hôm nay" in why or "tiền" in why
                                                              else 10 ** 9), why)
                    if self._switch(f"{tag} {why}"):
                        continue
                    if r.status_code in (401, 403) or "hết hạn" in why:
                        raise PermissionError(f"API key {name} không hợp lệ hoặc hết hạn ({r.status_code}) – "
                                              f"đã thử {len(self.keys)} key. Thêm / sửa key ở trang Cài đặt: {r.text[:200]}")
                    raise ApiError(f"Tất cả {len(self.keys)} key {name} đều {why}. Thêm key khác ở trang Cài đặt (khung "
                                   f"{name}) hoặc dịch tiếp sau – câu đã dịch được giữ lại, bấm 'Dịch câu chưa dịch' "
                                   "để dịch nốt.")
                if r.status_code == 429:   # giới hạn mỗi phút -> key khác, không có thì chờ rồi gửi lại
                    if self._switch(f"{_key_tag(self.engine, self.ki, len(self.keys))} bị giới hạn số lần / phút"):
                        continue
                    if waits < WAIT_LIMIT_429:
                        waits += 1
                        wait = self._retry_after(r)
                        _, report = net.current()
                        if report:
                            report(f"{name} báo gửi quá nhanh (gói miễn phí) – chờ {wait:.0f} s rồi gửi tiếp")
                        net.sleep(wait)
                        continue
                if r.status_code == 404:
                    raise ValueError(f"Không tìm thấy model '{self.model}' ({r.text[:300]})")
                last_err = RuntimeError(f"HTTP {r.status_code}: {r.text[:300]}")
            except (PermissionError, ValueError, ApiError):
                raise
            except requests.RequestException as e:
                last_err = e
                # mất mạng giữa chừng: chờ có mạng lại rồi gửi lại lô này (không tính là 1 lần hỏng, không bỏ lô)
                if net.is_net_error(e) and net.wait_online(f"dịch bằng AI ({self.engine})"):
                    continue
            fails += 1
            if fails < 4:
                net.sleep(2 * 2 ** (fails - 1))
        hint = ("Mạng chập chờn / máy chủ không trả lời – thử lại sau ít phút" if last_err is not None
                and net.is_net_error(last_err) else "Kiểm tra Internet / API key / số dư tài khoản ở trang Cài đặt")
        raise ApiError(f"Không gọi được API dịch {self.engine} ({self.model}): {str(last_err)[:250]}. {hint}.")


# model KHÔNG dùng để dịch được (giọng nói, chép lời, ảnh / video, embedding, kiểm duyệt, đời cũ chỉ completions, chỉ có
# ở Responses API…) – ẩn khỏi danh sách chọn model (2.1.14; nút "Hiện cả model không dùng để dịch" vẫn xem được)
_NOT_CHAT = re.compile(r"embed|tts|transcri|whisper|dall-e|image|imagen|veo-|sora|moderation|realtime|audio|speech|"
                       r"rerank|diarize|search|computer-use|aqa|lyria|robotics|native-audio|-live|guard|^babbage|"
                       r"^davinci|^text-|^omni-|-instruct", re.I)
# chỉ API chính hãng OpenAI: model chỉ gọi được qua Responses API (tool gọi /chat/completions)
_OPENAI_RESPONSES_ONLY = re.compile(r"codex|(^|-)pro($|-)|deep-research", re.I)
MODEL_PAGE = 1000


def chat_model(name: str, engine: str = "openai", base: str = "") -> bool:
    """Model này có dùng để dịch được không (theo tên)."""
    if _NOT_CHAT.search(name or ""):
        return False
    if engine == "openai" and "api.openai.com" in (base or "https://api.openai.com/v1"):
        return not _OPENAI_RESPONSES_ONLY.search(name)
    return True


def list_models(settings: dict, engine: str, key: str = "", hidden: list | None = None) -> list[str]:
    """Các model key này dùng được để dịch (nút 'Chọn model' ở Cài đặt), MỚI NHẤT TRƯỚC. Lấy hết mọi trang (Claude trả 20
    model / trang, Gemini 50). hidden: danh sách nhận các model bị ẩn (giọng nói, ảnh, embedding…)."""
    key = key or (api_keys(settings, engine) or [""])[0]
    items: list[tuple[str, float]] = []      # (tên, thứ tự: càng lớn càng mới)
    base = ""
    if engine == "gemini":
        token = ""
        for _ in range(20):
            r = net.session().get("https://generativelanguage.googleapis.com/v1beta/models",
                                  params={"pageSize": MODEL_PAGE, **({"pageToken": token} if token else {})},
                                  headers={"x-goog-api-key": key}, timeout=(10, 30))   # key ở header, không ở link
            r.raise_for_status()
            d = r.json()
            items += [(m["name"].split("/", 1)[-1], 0.0) for m in d.get("models", [])
                      if "generateContent" in (m.get("supportedGenerationMethods") or [])]
            token = d.get("nextPageToken") or ""
            if not token:
                break
        items.sort(key=lambda x: x[0], reverse=True)     # gemini-3… trước gemini-2.5…
    elif engine == "anthropic":
        after = ""
        for _ in range(20):
            r = net.session().get("https://api.anthropic.com/v1/models", timeout=(10, 30),
                                  params={"limit": MODEL_PAGE, **({"after_id": after} if after else {})},
                                  headers={"x-api-key": key, "anthropic-version": "2023-06-01"})
            r.raise_for_status()
            d = r.json()
            items += [(m["id"], 0.0) for m in d.get("data", [])]      # API trả mới nhất trước
            after = d.get("last_id") or ""
            if not d.get("has_more") or not after:
                break
    else:
        c = LLMClient(dict(settings, **{f"{engine}_api_keys": [key] if key else []}), engine)
        base = c.base
        r = net.session().get(f"{c.base}/models", headers={"Authorization": f"Bearer {c.api_key}"}, timeout=(10, 30))
        r.raise_for_status()
        data = r.json().get("data", [])
        items = [(m["id"], float(m.get("created") or 0)) for m in data if m.get("id")]
        items.sort(key=lambda x: (x[1], x[0]), reverse=True)     # model mới ra trước
    seen, out = set(), []
    for name, _ in items:
        if name in seen:
            continue
        seen.add(name)
        if chat_model(name, engine, base):
            out.append(name)
        elif hidden is not None:
            hidden.append(name)
    return out


def check_key(settings: dict, engine: str, key: str) -> str:
    """Thử 1 key (nút 'Kiểm tra' ở Cài đặt): trả lời ngắn gọn ✓ / ✗ + lý do."""
    st = dict(settings, **{f"{engine}_api_key": key, f"{engine}_api_keys": [key], f"{engine}_rpm": 0})
    _KEY_DEAD.pop(key, None)
    try:
        c = LLMClient(st, engine)
        c.chat("Reply with JSON only.", 'Return {"ok": 1}', max_tokens=50, temperature=0)
        return f"✓ dùng được ({c.model})"
    except Exception as e:  # noqa: BLE001
        return "✗ " + str(e)[:220]


# ======================================================================= Hy-MT2

LOCAL_ENGINES = ("google", "hymt")    # không phải AI hội thoại: không tự đọc kịch bản, không rút gọn câu được
HYMT_REPO = "tencent/Hy-MT2-1.8B"
_hymt = None


class HymtWorker(VieneuWorker):
    """Tiến trình riêng chạy Hy-MT2 (trami/hymt_worker.py), cùng cách điều khiển với VieNeu / Qwen3."""
    title = "Hy-MT2"
    stall_call = 900.0   # 1 lô dịch không có tin gì quá 15 phút -> treo
    flag = "--hymt-worker"
    log_name = "hymt.txt"

    def __init__(self, repo: str, token: str = ""):
        super().__init__(repo)
        self.token = token

    def _extra_env(self) -> dict:
        return {"HF_TOKEN": self.token} if self.token else {}

    def call(self, req: dict, progress=None, cancel=None) -> dict:
        with self.lock:
            if not self.proc or self.proc.poll() is not None:
                self._start(progress, cancel)
            assert self.proc and self.proc.stdin
            self.proc.stdin.write(json.dumps(req, ensure_ascii=False) + "\n")
            self.proc.stdin.flush()
            r = self._read(progress, cancel, self.stall_call)
        if "error" in r:
            raise RuntimeError(r["error"])
        return r


def hymt_worker(settings: dict | None = None) -> HymtWorker:
    global _hymt
    st = settings or {}
    repo = st.get("hymt_model") or HYMT_REPO
    if _hymt is None or _hymt.mode != repo:
        _hymt = HymtWorker(repo, st.get("hf_token", ""))
        import atexit

        atexit.register(_hymt.stop)
    return _hymt


# ======================================================================= Translator

VIDEO_WIN = 600.0  # dịch có nhìn hình (2.1.25): mỗi lô ứng với 1 khúc video ≤ 10 phút
VIDEO_PAD = 3.0    # khúc video rộng hơn dòng đầu / cuối của lô 3 s
VIDEO_RULE_VI = """

VIDEO GỬI KÈM: bạn được XEM + NGHE khúc video chứa các dòng cần dịch (giờ từng dòng – giây tính từ đầu khúc – và người
nói ở mục THỜI ĐIỂM TRONG VIDEO). Dùng hình + giọng để: biết ai đang nói với ai (nam / nữ, già / trẻ, vai vế, thân / sơ)
mà chọn xưng hô đúng; hiểu câu ngắn theo hành động trên hình; đọc chữ trên màn hình (tin nhắn, biển hiệu, tên nhân vật)
làm ngữ cảnh. Vẫn dịch đúng CHỮ của từng dòng (tai nghe khác chữ dòng thì vẫn theo chữ dòng), giữ đủ mọi luật trên.
Thêm 1 khoá "_nguoi_noi" vào JSON: {"S1": {"gioi": "nam" | "nu", "ten": "<tên nhân vật nếu biết>"}} cho người nói có
trong lô – chỉ ghi khi thấy rõ người đó trên hình."""
VIDEO_RULE_EN = """

ATTACHED VIDEO: you can WATCH + HEAR the video clip containing these lines (each line's time – seconds from the start of
the clip – and speaker are in VIDEO TIMES). Use picture + voice to know who is talking to whom (male / female, age,
rank, closeness) and choose the right forms of address; understand short lines from the action on screen; read on-screen
text (messages, signs, character names) as context. Still translate the TEXT of each line (if what you hear differs from
the line, follow the line) and keep every rule above. Add one key "_speakers" to the JSON: {"S1": {"gender": "male" |
"female", "name": "<character name if known>"}} for speakers in this batch – only when clearly seen."""
CTX_BEFORE = 6     # số dòng ngay trước lô gửi kèm (đã dịch) – luật 2: đọc ≥ 2 dòng trước / sau mỗi dòng
CTX_AFTER = 4      # số dòng ngay sau lô gửi kèm
_HEAD_DROP = re.compile(r"[\W_]+")


def _head(text: str) -> str:
    """Vài ký tự đầu của dòng (bỏ ký hiệu độ dài, dấu câu, khoảng trắng) – để so với "dau" AI chép lại."""
    return _HEAD_DROP.sub("", BUDGET_RE.sub(" ", text or "")).lower()


def _answers(d: dict, n: int) -> tuple[list[str], list[str]]:
    """JSON của AI -> (bản dịch, "dau" từng dòng). Nhận cả dạng cũ {"1": "bản dịch"}."""
    res, heads = [], []
    for i in range(n):
        v = d.get(str(i + 1))
        h = ""
        if isinstance(v, dict):
            h = str(v.get("dau") or v.get("head") or v.get("src") or "")
            v = v.get("dich", v.get("text", v.get("translation")))
        res.append(v.strip() if isinstance(v, str) else "")
        heads.append(h)
    return res, heads


def shifted_lines(heads: list[str], lines: list[str]) -> set[int]:
    """Dòng bị LỆCH (AI gộp / bỏ 1 dòng làm các bản dịch sau trượt lên): "dau" của dòng i không khớp dòng i mà khớp đầu
    1 dòng bên cạnh (±3). "dau" trống / không khớp dòng nào -> không kết luận (AI không chép đúng thì thôi)."""
    hs = [_head(x) for x in lines]
    bad = set()
    for i, h in enumerate(heads):
        e = _head(h)[:3]
        if not e or i >= len(hs) or hs[i].startswith(e) or e in hs[i][:6]:
            continue
        if any(j != i and hs[j].startswith(e) for j in range(max(0, i - 3), min(len(hs), i + 4))):
            bad.add(i)
    return bad


def _runs(idx: list[int]) -> list[list[int]]:
    """[2, 3, 7] -> [[2, 3], [7]]"""
    out: list[list[int]] = []
    for i in sorted(idx):
        if out and i == out[-1][-1] + 1:
            out[-1].append(i)
        else:
            out.append([i])
    return out


class Translator:
    def __init__(self, settings: dict, engine: str | None = None, style: str | None = None,
                 glossary: dict[str, str] | None = None, cache: dict | None = None, target: str | None = None,
                 source: str | None = None, skill: str = ""):
        self.s = settings
        self.skill = skill or ""   # kỹ năng dịch của người dùng (skills.py) – gắn vào lời nhắc của mọi lần hỏi AI
        self.engine = engine or settings.get("translate_engine", "google")
        self.style = style or settings.get("translate_style", "auto")
        self.target = langs.norm(target or settings.get("target_lang"))
        self.source = source or "zh"
        # bảng thuật ngữ của bạn là tên Hán Việt -> chỉ dùng khi dịch sang tiếng Việt; tiếng khác dùng bảng riêng
        self.glossary = dict(parse_glossary(settings.get("glossary", ""))) if self.target == "vi" else {}
        if glossary:
            self.glossary.update(glossary)
        self.cache = cache if cache is not None else {}
        self.batch = engine_batch(settings, self.engine)   # số câu mỗi lần gửi (khung riêng từng công cụ ở Cài đặt)
        self.parallel = engine_parallel(settings, self.engine)   # gửi tất cả các lần cùng lúc (2.1.23)
        self.llm = None if self.engine in LOCAL_ENGINES else LLMClient(settings, self.engine)
        self.fit_dub = bool(settings.get("dub_fit", True))   # dịch vừa thời lượng để lồng tiếng khớp
        # câu ngắn hơn hẳn chỗ trống -> nói đầy đủ hơn (2.1.13: mặc định TẮT – người dùng không muốn AI tự thêm từ)
        self.fill_short = bool(settings.get("translate_fill", False))
        self.genre = genres.form(self.style, self.target, settings)   # form thể loại phim (sửa được) -> lời nhắc hệ thống
        self.fallback_lines = 0   # số câu AI trả lỗi định dạng, phải dùng Google bù
        # dịch có nhìn hình (2.1.25): gemvideo.VideoSource – mỗi lô gửi kèm khúc video của chính các dòng đó (chỉ Gemini)
        self.video = None
        self.video_batches = 0       # số lô đã gửi kèm video
        self.video_failed = 0        # số lô gửi video lỗi -> đã dịch chỉ bằng chữ
        self.video_error = ""
        self.speakers: dict[str, dict] = {}   # người nói -> {"g": male / female, "name"} Gemini thấy trên hình
        self._spk_lock = threading.Lock()

    # ------------------------------------------------------------- public
    def translate_segments(self, segs: list[Segment], progress: ProgressCB | None = None,
                           cancel: Callable[[], bool] | None = None, only_empty: bool = False,
                           auto_terms: bool = True, summary: str = "", rows: set[int] | None = None) -> list[Segment]:
        """rows: chỉ dịch các câu này (None = mọi câu)."""
        idx = [i for i, s in enumerate(segs) if s.text.strip() and (not only_empty or not s.trans.strip())
               and (rows is None or i in rows)]
        if not idx:
            return segs
        if self.llm and auto_terms:
            if progress:
                progress(0.01, "AI đang đọc toàn bộ kịch bản để thống nhất tên riêng, xưng hô…")
            try:
                summary = summary or self.analyze([segs[i].text for i in idx])
            except (Cancelled, net.NetworkDown):
                raise
            except Exception:
                summary = summary or ""
        texts = [segs[i].text for i in idx]
        if self.llm and self.fit_dub:
            # dịch vừa thời lượng: mỗi câu kèm giới hạn độ dài theo thời gian nhân vật nói (đơn vị theo tiếng đích)
            unit = langs.INFO[self.target]["unit_vi"] if self.target == "vi" else langs.INFO[self.target]["unit_ai"]
            texts = [f"{segs[i].text} {length_marker(segs[i].duration, self.target, unit, self.fill_short)}"
                     for i in idx]
        kw = {}
        if self.video is not None and self.llm is not None and self.engine == "gemini":
            kw["times"] = [(segs[i].start, segs[i].end, segs[i].speaker or "") for i in idx]
        results = self.translate_lines(texts, progress, cancel, summary=summary, **kw)
        for i, t in zip(idx, results):
            segs[i].trans = BUDGET_RE.sub(" ", t or "").strip()
            segs[i].extra.pop("trans_user", None)
            if segs[i].trans and not untranslated(segs[i].trans, self.target):   # nhớ theo câu gốc -> không tốn API
                self.cache.setdefault(cache_key(self.engine, self.style, self.target, segs[i].text), segs[i].trans)
        return segs

    def shorten(self, items: list[tuple[str, str, int]]) -> list[str]:
        """Rút gọn bản dịch quá dài so với thời gian nói. items: (câu gốc, bản dịch, số tiếng tối đa)."""
        if not self.llm or not items:
            return [vi for _, vi, _ in items]
        L = langs.INFO[self.target]
        if self.target == "vi":
            payload = {str(k + 1): {"goc": zh, "dich": vi, "toi_da": f"{n} tiếng"} for k, (zh, vi, n) in enumerate(items)}
            sysp, user = SHORTEN_PROMPT, "RÚT GỌN CÁC BẢN DỊCH SAU:\n" + json.dumps(payload, ensure_ascii=False, indent=0)
        else:
            payload = {str(k + 1): {"source": zh, "translation": vi, "max": f"{n} {L['unit_ai']}"}
                       for k, (zh, vi, n) in enumerate(items)}
            sysp = SHORTEN_PROMPT_EN.format(lang=L["english"], unit=L["unit_ai"])
            user = "SHORTEN THESE TRANSLATIONS:\n" + json.dumps(payload, ensure_ascii=False, indent=0)
        try:
            d = _extract_json(self.llm.chat(with_skill(sysp, self.skill), user))
        except (Cancelled, net.NetworkDown):
            raise
        except Exception:  # noqa: BLE001
            return [vi for _, vi, _ in items]
        out = []
        for k, (_, vi, n) in enumerate(items):
            v = d.get(str(k + 1)) if isinstance(d, dict) else None
            v = BUDGET_RE.sub(" ", v).strip() if isinstance(v, str) else ""
            v = self._post(v, items[k][0]) if v else ""
            out.append(v if v and syllables(v, self.target) < syllables(vi, self.target) else vi)
        return out

    def match_clauses(self, items: list[tuple[list[str], str]]) -> list[str]:
        """Sửa bản dịch cho đủ số vế như câu gốc (2.1.3). items: (các vế câu gốc theo chỗ ngắt hơi, bản dịch hiện tại).
        Chỉ AI làm được (Google / Hy-MT2 trả lại nguyên bản dịch); AI lỗi -> giữ bản cũ."""
        if not self.llm or not items:
            return [tr for _, tr in items]
        L = langs.INFO[self.target]
        if self.target == "vi":
            payload = {str(k + 1): {"ve_goc": cl, "dich": tr, "so_ve": len(cl)} for k, (cl, tr) in enumerate(items)}
            sysp, user = CLAUSE_PROMPT, "SỬA SỐ VẾ CÁC BẢN DỊCH SAU:\n" + json.dumps(payload, ensure_ascii=False, indent=0)
        else:
            payload = {str(k + 1): {"source_clauses": cl, "translation": tr, "clauses": len(cl)}
                       for k, (cl, tr) in enumerate(items)}
            sysp = CLAUSE_PROMPT_EN.format(lang=L["english"])
            user = "FIX THE CLAUSES OF THESE TRANSLATIONS:\n" + json.dumps(payload, ensure_ascii=False, indent=0)
        try:
            d = _extract_json(self.llm.chat(with_skill(sysp, self.skill), user))
        except (Cancelled, net.NetworkDown):
            raise
        except Exception:  # noqa: BLE001
            return [tr for _, tr in items]
        out = []
        for k, (_cl, tr) in enumerate(items):
            v = d.get(str(k + 1)) if isinstance(d, dict) else None
            v = BUDGET_RE.sub(" ", v).strip() if isinstance(v, str) else ""
            out.append(self._post(v, "，".join(_cl)) if v and not untranslated(v, self.target) else tr)
        return out

    def translate_lines(self, lines: list[str], progress: ProgressCB | None = None,
                        cancel: Callable[[], bool] | None = None, summary: str = "",
                        times: list[tuple[float, float, str]] | None = None) -> list[str]:
        """times: (đầu, cuối, người nói) từng dòng – có thì mỗi lô gửi kèm khúc video của chính các dòng đó (dịch có nhìn
        hình, 2.1.25): lô gom theo cả số câu lẫn độ dài khúc video (≤ VIDEO_WIN)."""
        out: list[str | None] = [None] * len(lines)
        todo = []
        for i, l in enumerate(lines):
            hit = self.cache.get(cache_key(self.engine, self.style, self.target, l))
            if hit and not untranslated(hit, self.target):   # bản dịch rỗng / còn chữ Hán (lần trước lỗi) -> dịch lại
                out[i] = hit
            else:
                todo.append(i)
        done = len(lines) - len(todo)
        if self.engine == "hymt":
            res = self._hymt(lines, todo, progress, cancel, done)
            for gi, r in zip(todo, res):
                out[gi] = r
                if r and not untranslated(r, self.target):
                    self.cache[cache_key(self.engine, self.style, self.target, lines[gi])] = r
        elif self.engine == "google":
            batches = self._google_batches([lines[i] for i in todo])
            pos = 0
            for chunk in batches:
                if cancel and cancel():
                    raise Cancelled()
                res = self._google(chunk)
                for j, r in enumerate(res):
                    gi = todo[pos + j]
                    out[gi] = r
                    if r:
                        self.cache[cache_key(self.engine, self.style, self.target, lines[gi])] = r
                pos += len(chunk)
                done += len(chunk)
                if progress:
                    progress(done / len(lines), f"Đang dịch {done}/{len(lines)} câu (Google)")
        else:
            def one(ids):
                ctx_before = [(lines[k], out[k] or "") for k in range(max(0, ids[0] - CTX_BEFORE), ids[0])]
                ctx_after = [lines[k] for k in range(ids[-1] + 1, min(len(lines), ids[-1] + 1 + CTX_AFTER))]
                return self._llm_batch([lines[i] for i in ids], ctx_before, ctx_after, summary,
                                       vt=[times[i] for i in ids] if times else None)

            def take(pos: int) -> list[int]:
                """Lô tiếp theo từ vị trí pos: ≤ số câu mỗi lần gửi; dịch có nhìn hình thì khúc video ≤ VIDEO_WIN."""
                ids = todo[pos:pos + self._size()]
                if times and ids:
                    a = times[ids[0]][0]
                    k = 1
                    while k < len(ids) and times[ids[k]][1] - a <= VIDEO_WIN:
                        k += 1
                    ids = ids[:k]
                return ids

            def keep(ids, res):
                for gi, r in zip(ids, res):
                    out[gi] = r
                    if r and not untranslated(r, self.target):
                        self.cache[cache_key(self.engine, self.style, self.target, lines[gi])] = r
            size = self._size()
            if self.parallel and (len(todo) > size or times):
                # gửi tất cả cùng lúc: các lô đi song song; lô sau chưa có bản dịch của lô trước -> ngữ cảnh chỉ có câu gốc
                groups, b = [], 0
                while b < len(todo):
                    g = take(b)
                    groups.append(g)
                    b += len(g)
                if progress:
                    progress(done / len(lines), f"AI đang dịch {len(todo)} câu – gửi {len(groups)} lần cùng lúc "
                                                f"({self.engine})")
                c0, report = net.current()
                with net.task(cancel or c0, report):     # nút Huỷ dừng được mọi lần gửi đang chạy
                    for ids, res in run_parallel(one, groups):
                        keep(ids, res)
                        done += len(ids)
                        if progress:
                            progress(done / len(lines), f"AI đang dịch {done}/{len(lines)} câu – gửi cùng lúc "
                                                        f"({self.engine})")
            else:
                pos = 0
                while pos < len(todo):
                    if cancel and cancel():
                        raise Cancelled()
                    ids = take(pos)   # model trả lời ngắn hơn lô (đã biết) -> lô sau nhỏ lại
                    keep(ids, one(ids))
                    pos += len(ids)
                    done += len(ids)
                    if progress:
                        progress(done / len(lines), f"AI đang dịch {done}/{len(lines)} câu ({self.engine})")
        return [self._post(o or "", BUDGET_RE.sub(" ", lines[k])) for k, o in enumerate(out)]

    # ------------------------------------------------------------- AI
    def analyze(self, texts: list[str]) -> str:
        """Đọc kịch bản, trích tên riêng + thuật ngữ -> Hán Việt, đoán thể loại & xưng hô. Cập nhật glossary."""
        assert self.llm
        full = "\n".join(texts)
        chunks, cur = [], ""
        for line in full.split("\n"):
            if len(cur) + len(line) > 7000:
                chunks.append(cur)
                cur = ""
            cur += line + "\n"
        if cur:
            chunks.append(cur)
        notes = []
        L = langs.INFO[self.target]
        for ch in chunks[:6]:  # đọc tối đa ~40k ký tự đầu là đủ nắm nhân vật
            if self.target == "vi":
                sysp = "Bạn là chuyên gia Hán Việt và biên dịch phim Trung Quốc. Chỉ trả JSON."
                prompt = (
                    "Đọc đoạn kịch bản phụ đề tiếng Trung dưới đây và trả về JSON với các khoá:\n"
                    '"genre": thể loại phim (cổ trang/tiên hiệp/hiện đại/review/...)\n'
                    '"terms": object ánh xạ TÊN RIÊNG tiếng Trung (tên người, biệt danh, địa danh, môn phái, tổ chức, '
                    "chiêu thức, bảo vật) -> phiên âm Hán Việt chuẩn. Chỉ lấy tên riêng thực sự, không lấy từ thông thường.\n"
                    '"relations": mô tả ngắn quan hệ giữa các nhân vật chính và cách xưng hô tiếng Việt phù hợp.\n\n'
                    "KỊCH BẢN:\n" + ch)
            else:
                sysp = f"You are an expert translator of Chinese films into {L['english']}. Return JSON only."
                prompt = (
                    "Read the subtitle script below and return JSON with the keys:\n"
                    '"genre": the film genre\n'
                    f'"terms": object mapping PROPER NAMES in the source (people, nicknames, places, sects, '
                    f"organizations, techniques, treasures) -> their standard {L['english']} rendering "
                    f"({_NAMES.get(self.target, '')}). Only real proper names, no common words.\n"
                    f'"relations": short description of the main characters\' relationships and suitable forms of '
                    f"address in {L['english']}.\n\nSCRIPT:\n" + ch)
            try:
                d = _extract_json(self.llm.chat(sysp, prompt, max_tokens=4000, temperature=0.1))
            except (Cancelled, net.NetworkDown):
                raise
            except Exception:
                continue
            for k, v in (d.get("terms") or {}).items():
                if isinstance(v, str) and k and v and k not in self.glossary and len(k) <= 12:
                    self.glossary[k] = v
            g, r = d.get("genre"), d.get("relations")
            if g or r:
                notes.append(f"Thể loại: {g}. Quan hệ/xưng hô: {r}")
        return "\n".join(notes[:2])

    def _size(self) -> int:
        """Số câu mỗi lần gửi: theo Cài đặt, nhưng không quá sức trả lời của model (API đã báo giới hạn token)."""
        cap = self.llm.out_cap() if self.llm else None
        if not cap:
            return self.batch
        room = cap - TOK_BASE - (THINK_TOK if self.llm.thinking else 0)
        return max(10, min(self.batch, room // TOK_LINE))

    def system(self) -> str:
        """Lời nhắc hệ thống khi dịch: luật chung + form thể loại phim + kỹ năng của bạn."""
        return with_skill(system_prompt(self.target, self.source, self.fit_dub and self.fill_short, self.genre),
                          self.skill)

    def _llm_batch(self, lines: list[str], ctx_before, ctx_after, summary: str, depth: int = 0,
                   vt: list[tuple[float, float, str]] | None = None, win: tuple[float, float] | None = None) -> list[str]:
        """Dịch 1 lô. ctx_before: [(câu gốc, bản dịch)] các dòng ngay trước lô; ctx_after: [câu gốc] các dòng ngay sau.
        Dòng thiếu / còn chữ Hán / bị LỆCH DÒNG ("dau" khớp dòng bên cạnh) được hỏi lại KÈM 2+ dòng trước / sau của chính
        nó (2.1.13 – trước đây hỏi lại 1 dòng lẻ với ngữ cảnh của cả lô, mất nghĩa)."""
        gl = {k: v for k, v in self.glossary.items() if any(k in l for l in lines)}
        vi = self.target == "vi"
        lab = (("THÔNG TIN PHIM", "BẢNG THUẬT NGỮ (bắt buộc dùng)",
                "NGỮ CẢNH TRƯỚC (các dòng ngay trước, đã dịch – chỉ để hiểu nghĩa, KHÔNG dịch lại)",
                "NGỮ CẢNH SAU (các dòng ngay sau – chỉ để hiểu nghĩa, KHÔNG dịch)") if vi else
               ("FILM NOTES", "GLOSSARY (must use)", "PREVIOUS CONTEXT (the lines right before, already translated – "
                "for meaning only, do NOT translate again)", "FOLLOWING CONTEXT (the lines right after – for meaning "
                "only, do NOT translate)"))
        parts = []
        if summary:
            parts.append(f"{lab[0]}:\n{summary}")
        if gl:
            parts.append(f"{lab[1]}:\n" + glossary_text(gl))
        if ctx_before:   # gửi cùng lúc: dòng trước chưa dịch xong -> chỉ có câu gốc
            parts.append(f"{lab[2]}:\n" + "\n".join(f"- {BUDGET_RE.sub(' ', a).strip()}" + (f" => {b}" if b else "")
                                                     for a, b in ctx_before))
        if ctx_after:
            parts.append(f"{lab[3]}:\n" + "\n".join(f"- {BUDGET_RE.sub(' ', a).strip()}" for a in ctx_after))
        payload = {str(i + 1): l for i, l in enumerate(lines)}
        n = len(lines)
        if vt and self.video is not None:     # dịch có nhìn hình: khúc video của chính các dòng trong lô
            if win is None:   # hỏi lại phần thiếu / chia đôi lô: dùng lại đúng khúc video đã tải lên của lô
                win = (max(0.0, min(t[0] for t in vt) - VIDEO_PAD), max(t[1] for t in vt) + VIDEO_PAD)
            a = win[0]
            parts.append(("THỜI ĐIỂM TRONG VIDEO (giây tính từ đầu khúc video gửi kèm | người nói):\n" if vi else
                          "VIDEO TIMES (seconds from the start of the attached clip | speaker):\n") +
                         "\n".join(f"{k + 1}: {max(0.0, t0 - a):.1f}–{max(0.0, t1 - a):.1f} | {sp or '?'}"
                                    for k, (t0, t1, sp) in enumerate(vt)))
        ask = (f"DỊCH {n} DÒNG SAU sang tiếng Việt – đúng {n} dòng, trả về JSON với đủ các khoá 1..{n}:" if vi else
               f"TRANSLATE THE FOLLOWING {n} LINES into {langs.INFO[self.target]['english']} – exactly {n} lines, "
               f"return JSON with every key 1..{n}:")
        parts.append(ask + "\n" + json.dumps(payload, ensure_ascii=False, indent=0))
        user = "\n\n".join(parts)
        tl = getattr(self.llm, "_tl", None)
        if tl is not None:
            tl.cut = False
        try:   # xin đủ chỗ trả lời cho cả lô (trước 2.1.23 cố định 8000 token: lô 200 câu bị cắt giữa chừng)
            reply = None
            if win is not None:
                reply = self._video_chat(user, win, TOK_BASE + TOK_LINE * n + 400)
                tl = getattr(getattr(self.video, "client", None), "_tl", None) if reply is not None else tl
            if reply is None:
                reply = self.llm.chat(self.system(), user, max_tokens=TOK_BASE + TOK_LINE * n)
            d = _extract_json(reply)
        except (Cancelled, net.NetworkDown):
            raise
        except (PermissionError, ApiError, ValueError) as e:
            if isinstance(e, ValueError) and "JSON" in str(e):
                d = None  # AI trả về sai định dạng -> chia nhỏ lô rồi thử lại
            else:
                raise     # lỗi kết nối / key / model: báo rõ, KHÔNG tự đổi sang Google
        except Exception:
            d = None
        if isinstance(d, dict):
            if win is not None:
                self._keep_speakers(d)
            res, heads = _answers(d, n)
            bad = shifted_lines(heads, lines)
            miss = [i for i, r in enumerate(res)
                    if lines[i].strip() and (i in bad or not r or untranslated(r, self.target))]
            if not miss:
                return res
            for i in miss:
                res[i] = ""
            k = n   # AI hết chỗ trả lời giữa chừng (lô dài hơn giới hạn của model): giữ phần đã trả, dịch tiếp phần sau
            if tl is not None and getattr(tl, "cut", False):
                k = max((i + 1 for i, r in enumerate(res) if r), default=0)
            head = [i for i in miss if i < k]
            if k and len(head) <= max(2, k // 8) and depth < 4:
                # hỏi lại từng cụm dòng thiếu, kèm các dòng ngay trước / sau của cụm; phần sau chỗ bị cắt = 1 lô ngắn hơn
                for grp in _runs(head) + ([list(range(k, n))] if k < n else []):
                    a, b = grp[0], grp[-1]
                    cb = (list(ctx_before) + [(lines[j], res[j]) for j in range(a)])[-CTX_BEFORE:]
                    ca = ([lines[j] for j in range(b + 1, n)] + list(ctx_after))[:CTX_AFTER]
                    sub = depth if a == k else depth + 1
                    for i, f in zip(grp, self._llm_batch([lines[i] for i in grp], cb, ca, summary, sub,
                                                         [vt[i] for i in grp] if vt else None, win)):
                        res[i] = f
                return res
        # lỗi hoặc thiếu nhiều dòng -> chia đôi lô rồi thử lại (mỗi nửa vẫn có ngữ cảnh của nửa kia)
        if n > 1 and depth < 4:
            mid = n // 2
            a = self._llm_batch(lines[:mid], ctx_before, (lines[mid:] + list(ctx_after))[:CTX_AFTER], summary, depth + 1,
                                vt[:mid] if vt else None, win)
            b = self._llm_batch(lines[mid:], (list(ctx_before) + list(zip(lines[:mid], a)))[-CTX_BEFORE:], ctx_after,
                                summary, depth + 1, vt[mid:] if vt else None, win)
            return a + b
        # cuối cùng dùng Google để không bị trống (đếm lại để cảnh báo người dùng)
        try:
            self.fallback_lines += n
            return self._google(lines)
        except (Cancelled, net.NetworkDown):
            raise
        except Exception:
            return [""] * n

    def _video_chat(self, user: str, win: tuple[float, float], max_tokens: int) -> str | None:
        """Hỏi Gemini kèm khúc video [win]. Lỗi (tải video lên hỏng, Gemini từ chối khúc này…) -> None: lô này dịch chỉ
        bằng chữ như trước. Huỷ / mất mạng quá lâu / key hỏng hết -> ném lỗi như dịch thường."""
        rule = VIDEO_RULE_VI if self.target == "vi" else VIDEO_RULE_EN
        try:
            reply = self.video.ask(self.system() + rule, user, win[0], win[1], max_tokens)
            with self._spk_lock:
                self.video_batches += 1
            return reply
        except (Cancelled, net.NetworkDown, PermissionError):
            raise
        except Exception as e:  # noqa: BLE001
            with self._spk_lock:
                self.video_failed += 1
                self.video_error = str(e)[:160]
            return None

    def _keep_speakers(self, d: dict) -> None:
        """Giới tính / tên người nói Gemini thấy trên hình ("_nguoi_noi" / "_speakers") -> self.speakers."""
        info = d.get("_nguoi_noi") or d.get("_speakers")
        if not isinstance(info, dict):
            return
        with self._spk_lock:
            for spk, v in info.items():
                if not isinstance(v, dict) or not str(spk).strip():
                    continue
                g = str(v.get("gioi") or v.get("gender") or "").lower()
                g = "female" if g in ("nu", "nữ", "female", "f") else "male" if g in ("nam", "male", "m") else ""
                cur = self.speakers.setdefault(str(spk).strip(), {"votes": {}})
                if g:
                    cur["votes"][g] = cur["votes"].get(g, 0) + 1
                name = str(v.get("ten") or v.get("name") or "").strip()
                if name and not cur.get("name"):
                    cur["name"] = name[:40]

    def speaker_genders(self) -> dict[str, dict]:
        """{người nói: {"g": male / female, "src": "gemini", "name"}} – theo số lần Gemini thấy (đa số, ≥ 2/3)."""
        out = {}
        for spk, v in self.speakers.items():
            votes = v.get("votes") or {}
            tot = sum(votes.values())
            if not tot:
                continue
            g, c = max(votes.items(), key=lambda x: x[1])
            if c / tot >= 2 / 3:
                out[spk] = {"g": g, "src": "gemini", **({"name": v["name"]} if v.get("name") else {})}
        return out

    # ------------------------------------------------------------- Hy-MT2 (trên máy)
    def _hymt(self, lines: list[str], todo: list[int], progress: ProgressCB | None, cancel, done0: int) -> list[str]:
        """Dịch bằng Hy-MT2 trong tiến trình riêng: mỗi câu kèm 3 câu trước làm ngữ cảnh + tên riêng có trong câu.
        Xong thì tắt tiến trình để trả bộ nhớ GPU cho bước lồng tiếng."""
        if not todo:
            return []
        w = hymt_worker(self.s)
        res: list[str] = []
        step = 32
        try:
            for b in range(0, len(todo), step):
                if cancel and cancel():
                    raise Cancelled()
                ids = todo[b:b + step]
                items = [{"text": lines[i],
                          "context": [lines[k] for k in range(max(0, i - 3), i) if lines[k].strip()],
                          "terms": [[k, v] for k, v in self.glossary.items() if k in lines[i]][:12]} for i in ids]
                base = done0 + b

                def pr(x: float, msg: str, base=base, n=len(ids)):
                    if progress and "dịch" in msg:
                        progress((base + x * n) / max(1, len(lines)), f"Hy-MT2 đang dịch {int(base + x * n)}/"
                                 f"{len(lines)} câu (trên máy)")
                    elif progress:
                        progress(base / max(1, len(lines)), msg)
                r = w.call({"items": items, "target": langs.INFO[self.target]["hymt"]}, pr, cancel)
                res += [str(x) for x in r.get("out", [])][:len(ids)] + [""] * max(0, len(ids) - len(r.get("out", [])))
        finally:
            w.stop()
        return res

    # ------------------------------------------------------------- Google
    def _apply_glossary_src(self, text: str) -> str:
        for k in sorted(self.glossary, key=len, reverse=True):
            if k in text:
                text = text.replace(k, f" {self.glossary[k]} ")
        return text

    def _google_batches(self, lines: list[str], limit: int = 4000) -> list[list[str]]:
        batches, cur, n = [], [], 0
        for l in lines:
            if cur and (n + len(l) + 1 > limit or len(cur) >= 80):
                batches.append(cur)
                cur, n = [], 0
            cur.append(l)
            n += len(l) + 1
        if cur:
            batches.append(cur)
        return batches

    def _gl(self) -> tuple[str, str]:
        """(nguồn, đích) cho Google: nguồn theo ngôn ngữ gốc của dự án (trước đây ghi cứng tiếng Trung); đang dịch câu
        tiếng khác (`_foreign`) -> Google tự nhận tiếng."""
        sl = "auto" if getattr(self, "_auto_sl", False) else \
            {"zh": "zh-CN", "yue": "zh-TW", "en": "en", "ja": "ja", "ko": "ko"}.get(self.source, "auto")
        return sl, langs.INFO[self.target]["google"]

    def _foreign(self, line: str) -> bool:
        """Câu gốc nói tiếng khác tiếng chính (phim Trung có câu tiếng Anh – bộ nghe chép bằng chữ Latin, 2.1.3)."""
        return self.source in ("zh", "yue") and not re.search(r"[㐀-鿿]", line) and \
            bool(re.search(r"[A-Za-z]{2,}(?:[\s'’-]+[A-Za-z]{2,})+", line))

    def _gtx(self, text: str) -> str:
        """Google Translate (endpoint miễn phí trả JSON)."""
        sl, tl = self._gl()
        r = net.session().post("https://translate.googleapis.com/translate_a/single",
                               params={"client": "gtx", "sl": sl, "tl": tl, "dt": "t", "ie": "UTF-8", "oe": "UTF-8"},
                               data={"q": text}, timeout=(8, 25),
                               headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
        r.raise_for_status()
        data = r.json()
        return "".join(part[0] for part in (data[0] or []) if part and part[0])

    def _clients5(self, text: str) -> str:
        sl, tl = self._gl()
        r = net.session().get("https://clients5.google.com/translate_a/t",
                              params={"client": "dict-chrome-ex", "sl": sl, "tl": tl, "q": text}, timeout=(8, 25),
                              headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
        r.raise_for_status()
        d = r.json()
        if isinstance(d, list) and d:
            x = d[0]
            return x[0] if isinstance(x, list) else str(x)
        if isinstance(d, dict) and d.get("sentences"):
            return "".join(s.get("trans", "") for s in d["sentences"])
        raise ValueError("Phản hồi Google không hợp lệ")

    def _deep(self, text: str) -> str:
        from deep_translator import GoogleTranslator

        sl, tl = self._gl()
        return GoogleTranslator(source=sl, target=tl).translate(text) or ""

    def _gt_one(self, text: str) -> str:
        errs: list[str] = []
        fails = 0
        while True:
            net_err = False
            for fn in (self._gtx, self._clients5, self._deep):
                try:
                    return net.call(fn, text)
                except Cancelled:
                    raise
                except Exception as e:  # noqa: BLE001
                    errs.append(f"{fn.__name__.strip('_')}: {str(e)[:150]}")
                    net_err = net_err or net.is_net_error(e)
            # mất mạng: chờ có mạng lại rồi dịch tiếp câu này (không tính là 1 lần hỏng)
            if net_err and net.wait_online("dịch bằng Google"):
                continue
            fails += 1
            if fails >= 3:
                raise RuntimeError("Không kết nối được Google Dịch (kiểm tra mạng hoặc bị giới hạn tạm thời, "
                                   "hãy thử lại sau hoặc dùng engine AI): " + errs[0][:200])
            net.sleep(2 * 2 ** (fails - 1))

    def _google(self, lines: list[str]) -> list[str]:
        alien = [i for i, l in enumerate(lines) if self._foreign(l)]
        if alien and len(alien) < len(lines):   # câu tiếng khác dịch riêng, Google tự nhận tiếng
            out = [""] * len(lines)
            rest = [i for i in range(len(lines)) if i not in set(alien)]
            for i, t in zip(rest, self._google([lines[i] for i in rest])):
                out[i] = t
            self._auto_sl = True
            try:
                for i, t in zip(alien, self._google([lines[i] for i in alien])):
                    out[i] = t
            finally:
                self._auto_sl = False
            return out
        if alien:
            self._auto_sl = True
        try:
            return self._google_lines(lines)
        finally:
            self._auto_sl = False

    def _google_lines(self, lines: list[str]) -> list[str]:
        src = [self._apply_glossary_src(l).replace("\n", " ").strip() for l in lines]
        try:
            parts = self._gt_one("\n".join(src)).split("\n")
            if len(parts) == len(lines):
                return [p.strip() for p in parts]
        except RuntimeError:
            raise
        # số dòng lệch -> dịch từng dòng
        return [self._gt_one(l).strip() if l else "" for l in src]

    # ------------------------------------------------------------- hậu xử lý
    def _post(self, t: str, src: str = "") -> str:
        """Dọn bản dịch: gộp dòng / khoảng trắng, bỏ ngoặc kép bao ngoài, viết hoa chữ đầu, bỏ dấu phẩy chen sai chỗ,
        thêm dấu câu cuối theo câu gốc (2.1.11 – trước đây BỎ dấu chấm cuối câu, mất cả ngữ điệu kết câu khi lồng)."""
        t = " ".join(t.replace("\n", " ").split())
        t = re.sub(r"\s+([,.!?;:])", r"\1", t)
        t = t.strip(" \"'“”")
        if t:
            t = t[0].upper() + t[1:]
        return punct_fix(t, src, self.target, self.names())

    def names(self) -> list[str]:
        """Tên riêng nhiều tiếng (theo bảng thuật ngữ) – không được chen dấu phẩy vào giữa."""
        return [v for v in self.glossary.values() if isinstance(v, str) and len(v.split()) >= 2]


def translate_text(text: str, settings: dict, engine: str | None = None, target: str | None = None,
                   source: str | None = None) -> str:
    return Translator(settings, engine, target=target, source=source).translate_lines([text])[0] if text.strip() else ""
