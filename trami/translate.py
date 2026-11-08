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
5. XƯNG HÔ THEO ĐÚNG DÒNG GỐC – KHÔNG ĐỔI VAI VẾ:
   a) Dòng gốc đã ghi rõ đại từ / cách gọi thì bản dịch giữ ĐÚNG giới tính, vai vế trên – dưới, ngôi và số ít / nhiều
      đó: 他 (nam) -> anh ấy / anh ta / hắn / cậu ta / ông ấy, KHÔNG thành cô ta / bà ấy; 她 (nữ) -> cô ấy / cô ta / chị
      ấy / bà ấy / nàng, KHÔNG thành anh ta / hắn; 它 -> nó; 哥 / 哥哥 / 大哥 / 师兄 / 学长 -> anh / huynh / sư huynh /
      đàn anh, KHÔNG thành em / chị; 姐 / 姐姐 / 师姐 / 学姐 -> chị / tỷ / sư tỷ / đàn chị, KHÔNG thành em / anh; 弟弟 /
      师弟 -> em (trai) / đệ / sư đệ; 妹妹 / 师妹 -> em (gái) / muội / sư muội; 爸 / 妈 / 爷爷 / 奶奶 / 叔 / 阿姨 / 老公 /
      老婆 -> bố / mẹ / ông / bà / chú / cô (dì) / chồng / vợ; 您 -> cách gọi kính trọng; 我 / 我们 / 咱们 (ngôi 1), 你 /
      你们 (ngôi 2), 他 / 她 / 他们 (ngôi 3) giữ đúng ngôi và số – không đổi câu nói VỀ người khác thành câu nói VỚI người
      nghe, không đổi "chúng ta" thành "tôi".
   b) Ngữ cảnh (luật 2) chỉ dùng để chọn SẮC THÁI trong đúng vai vế đó (他: anh ấy khi thân, hắn khi ghét, ông ấy khi là
      người già; 你: anh / chị / cậu / ngươi / mày theo quan hệ) – KHÔNG dùng ngữ cảnh để đổi nam ↔ nữ, trên ↔ dưới,
      anh ↔ em, ngôi 1 ↔ 2 ↔ 3 mà dòng gốc đã ghi.
   c) Chỉ khi dòng gốc KHÔNG có đại từ / cách gọi (tiếng Trung hay bỏ chủ ngữ) hoặc chỉ có 我 / 你 (không rõ nam nữ, trên
      dưới) mới chọn xưng hô theo người nói (e), ngữ cảnh, quan hệ, tuổi tác, địa vị (THỂ LOẠI PHIM, THÔNG TIN PHIM) – giữ
      ĐÚNG cặp xưng hô các dòng trước đã dùng giữa đúng 2 người đó, nhất quán cả phim; tự xưng và gọi người nghe trong
      cùng 1 câu phải cùng 1 cặp (哥，你去哪？ -> Anh, anh đi đâu? – không phải "Anh, em đi đâu?"). Vẫn chưa đủ để biết
      chắc thì làm theo f).
   d) THỂ LOẠI PHIM, THÔNG TIN PHIM, KỸ NĂNG, hình ảnh video chỉ là gợi ý cho trường hợp c); trái với đại từ / cách gọi
      ghi trong dòng gốc thì theo dòng gốc.
   e) NGƯỜI NÓI: mục "NGƯỜI NÓI TỪNG DÒNG" (nếu có) ghi người nói mỗi dòng (S1, S2… phân biệt theo giọng) kèm nam / nữ
      tool đo từ giọng ("?" = chưa rõ); dòng NGỮ CẢNH cũng có nhãn [S1 · nam]. Dùng nó để biết ai đang nói với ai: người
      nghe thường là người nói KHÁC ở dòng ngay trước / sau. Ví dụ dòng trước [S1 · nam] hỏi, dòng này S2 · nữ đáp 随你 ->
      nữ nói với nam -> "Tùy anh." (KHÔNG phải "Tùy em."); 你炼体我画符修炼 do sư muội nói với sư huynh -> "Huynh luyện
      thể, muội vẽ phù tu luyện.". Nam / nữ đo từ giọng có thể sai: đại từ ghi trong dòng gốc (a) và cặp xưng hô đã rõ ở
      các dòng trước giữa đúng 2 người đó được ưu tiên hơn.
   f) KHÔNG CHẮC XƯNG HÔ – dòng gốc chỉ có 你 / 我 hoặc bỏ chủ ngữ, mà chưa rõ người nói / người nghe là nam hay nữ, ai lớn
      hơn, và các dòng trước chưa có cặp xưng hô chắc chắn giữa đúng 2 người đó: {addr}
      Dòng nào phải làm theo f) thì thêm "xh": "?" vào mục JSON của dòng đó (luật 13).
6. Tên người, địa danh, tổ chức, chiêu thức: phiên âm Hán Việt chuẩn (李逍遥 -> Lý Tiêu Dao); địa danh có tên tiếng Việt quen
   dùng thì dùng tên đó (北京 -> Bắc Kinh); thương hiệu / người nước ngoài: tên quốc tế (微信 -> WeChat). Một tên chỉ một cách
   dịch. Tuân thủ tuyệt đối BẢNG THUẬT NGỮ. Thuật ngữ trong THỂ LOẠI PHIM chỉ dùng khi dòng gốc có đúng chữ đó.
7. Dòng gốc do máy nghe có thể sai vài chữ đồng âm: hiểu theo ngữ cảnh (luật 2) và dịch đúng ý. Đại từ 他 / 她 / 它 và
   cách gọi (哥, 姐, 弟, 妹…) KHÔNG coi là nghe nhầm – dịch đúng chữ đang ghi (luật 5).
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
   tra bản dịch nằm đúng dòng. Dòng phải dùng cách ở luật 5f (không chắc xưng hô) thêm "xh": "?" – {"dau": "…", "dich":
   "…", "xh": "?"}; dòng khác không ghi "xh"."""

# Luật 5f – khi KHÔNG CHẮC xưng hô (2.4.0, Cài đặt addr_unsure). Người dùng: "随你 nữ nói với nam mà tool dịch 'tùy em';
# câu không chắc thì dùng 'cậu' cho khỏi lệch vai" -> mặc định bỏ đại từ khi câu vẫn tự nhiên, không bỏ được thì cặp
# trung tính theo thể loại phim.
ADDR_MODES = ("drop", "neutral", "guess")
ADDR_LABELS = {"drop": "Bỏ đại từ, không được thì dùng cặp trung tính",
               "neutral": "Luôn dùng cặp trung tính (cậu – tớ / ngươi – ta)",
               "guess": "Để AI tự đoán theo ngữ cảnh (như cũ)"}
_NEUTRAL_VI = ("cặp TRUNG TÍNH không lộ nam / nữ, trên / dưới: phim hiện đại / đời thường / học đường \"cậu – tớ\"; cổ trang "
               "/ tiên hiệp / kiếm hiệp / huyền huyễn \"ngươi – ta\" (你炼体我画符修炼 -> \"Ngươi luyện thể, ta vẽ phù tu "
               "luyện.\"); người nghe rõ là bề trên / được kính trọng (您, sư phụ, cấp trên, người già) \"ngài – tôi\" (cổ "
               "trang: \"ngài – ta\")")
ADDR_RULE_VI = {
    "drop": ("ƯU TIÊN BỎ đại từ nếu câu tiếng Việt vẫn tự nhiên, đủ ý (随你 -> \"Tùy thôi.\" / \"Sao cũng được.\"; 你去哪？ -> "
             "\"Đi đâu thế?\"; 我不知道 -> \"Không biết nữa.\"). Câu không bỏ được đại từ (đối lập \"你… 我…\", phải nói rõ ai "
             "làm gì) thì dùng " + _NEUTRAL_VI + ". KHÔNG đoán bừa anh / em, chị / em, chàng / nàng."),
    "neutral": ("dùng " + _NEUTRAL_VI + " (随你 -> \"Tùy cậu.\"). KHÔNG đoán bừa anh / em, chị / em, chàng / nàng."),
    "guess": "chọn cặp xưng hô hợp nhất theo ngữ cảnh, quan hệ, tuổi tác, THỂ LOẠI PHIM (như c).",
}
ADDR_RULE_EN = {
    "drop": ("PREFER leaving the pronoun / form of address out when the sentence stays natural and complete in {lang} "
             "(随你 -> \"Whatever works.\" style). When it cannot be left out (a contrast \"你… 我…\" that must say who does "
             "what), use the most NEUTRAL form – one that implies no gender and no seniority{neutral}. NEVER guess a "
             "gendered / ranked form."),
    "neutral": "use the most NEUTRAL form – one that implies no gender and no seniority{neutral}. NEVER guess a gendered / "
               "ranked form.",
    "guess": "choose the most likely form of address from context, relationship, age and FILM GENRE (as in c).",
}
_NEUTRAL_EN = {"en": "", "th": " (e.g. คุณ / ฉัน; the polite particle ครับ / ค่ะ follows the SPEAKER's gender when it is known)",
               "ja": " (Japanese usually drops pronouns; otherwise あなた / 私)",
               "ko": " (Korean usually drops pronouns; otherwise polite forms without 오빠 / 형 / 누나 / 언니)"}
ADDR_TAIL_VI = {"drop": " Không chắc (chỉ có 你 / 我, chưa rõ ai nói với ai, nam hay nữ, ai lớn hơn): bỏ đại từ nếu câu vẫn "
                        "tự nhiên, không bỏ được thì dùng cặp trung tính (hiện đại cậu – tớ, cổ trang ngươi – ta) – KHÔNG "
                        "đoán bừa anh / em, chị / em.",
                "neutral": " Không chắc (chỉ có 你 / 我, chưa rõ ai nói với ai, nam hay nữ, ai lớn hơn): dùng cặp trung "
                           "tính (hiện đại cậu – tớ, cổ trang ngươi – ta) – KHÔNG đoán bừa anh / em, chị / em.",
                "guess": ""}
ADDR_TAIL_EN = {"drop": " When unsure (only 你 / 我, unclear who speaks to whom, gender or seniority): leave the form of "
                        "address out if natural, otherwise use the most neutral form – never guess a gendered / ranked one.",
                "neutral": " When unsure (only 你 / 我, unclear who speaks to whom, gender or seniority): use the most "
                           "neutral form of address – never guess a gendered / ranked one.",
                "guess": ""}


def addr_mode(settings: dict | None) -> str:
    """Cách xử lý khi không chắc xưng hô (Cài đặt addr_unsure)."""
    m = str((settings or {}).get("addr_unsure") or "drop")
    return m if m in ADDR_MODES else "drop"

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


# 2.3.4: người dùng – "phụ đề nghĩa anh thì không được dịch thành em, anh ta không được thành cô ta; xưng hô đúng ngữ
# cảnh là tốt nhưng không đổi vai vế phụ đề đã đúng – áp cho mọi form dịch". Luật 5 viết lại; form thể loại / kỹ năng /
# hồ sơ phim / hình video gắn SAU luật chung nên mỗi lời nhắc kết thúc bằng câu nhắc lại (AI hay theo phần cuối).
GENRE_HEAD_VI = ("THỂ LOẠI PHIM (xưng hô, thuật ngữ, giọng văn – cặp xưng hô ở đây chỉ dùng cho dòng gốc KHÔNG ghi rõ đại "
                 "từ / cách gọi, xem luật 5)")
GENRE_HEAD_EN = ("FILM GENRE (forms of address – only for lines without an explicit pronoun / kinship term, see rule 5 –, "
                 "terminology, tone)")
ADDRESS_TAIL_VI = """

NHẮC LẠI – XƯNG HÔ THEO DÒNG GỐC (ưu tiên hơn THỂ LOẠI PHIM, THÔNG TIN PHIM, HỒ SƠ PHIM, KỸ NĂNG và hình ảnh): dòng gốc đã
ghi rõ đại từ / cách gọi (他 / 她 / 它, 哥 / 姐 / 弟 / 妹, 爸 / 妈, 您…) thì bản dịch giữ đúng giới tính, vai vế trên – dưới,
ngôi và số ít / nhiều đó; ngữ cảnh chỉ để chọn sắc thái (anh ấy / anh ta / hắn), KHÔNG đổi anh ↔ em, anh ta ↔ cô ta,
chị ↔ em. Dòng gốc không ghi rõ mới chọn theo ngữ cảnh và giữ nhất quán."""
ADDRESS_TAIL_EN = """

REMINDER – FORMS OF ADDRESS FOLLOW THE SOURCE LINE (this outranks FILM GENRE, FILM NOTES, SKILL and the video): when the
source line states a pronoun / kinship or rank term (他 / 她 / 它, 哥 / 姐 / 弟 / 妹, 爸 / 妈, 您…), keep exactly its gender,
seniority, person and number; context only picks the tone, never switch he ↔ she, older ↔ younger brother / sister.
Only lines without such a word get their form of address from context, consistently."""


def address_tail(target: str, addr: str = "drop") -> str:
    """Câu nhắc luật xưng hô theo dòng gốc – gắn CUỐI mọi lời nhắc dịch / sửa bản dịch (sau form thể loại, kỹ năng). addr:
    cách làm khi không chắc xưng hô (2.4.0)."""
    addr = addr if addr in ADDR_MODES else "drop"
    if langs.norm(target) == "vi":
        return ADDRESS_TAIL_VI + ADDR_TAIL_VI[addr]
    return ADDRESS_TAIL_EN + ADDR_TAIL_EN[addr]


def system_prompt(target: str, source: str = "zh", fill: bool = False, genre: str = "", addr: str = "drop") -> str:
    """Lời nhắc hệ thống cho AI theo ngôn ngữ đích. fill: có ký hiệu ⟪M–N⟫ (Cài đặt "Câu ngắn nói đầy đủ hơn…"); genre:
    form thể loại phim (genres.form) gắn cuối; addr: cách làm khi không chắc xưng hô (luật 5f, ADDR_MODES)."""
    target = langs.norm(target)
    addr = addr if addr in ADDR_MODES else "drop"
    src_vi, src_en = SRC_NAMES.get(source, SRC_NAMES["zh"])
    if target == "vi":
        sp = SYSTEM_PROMPT.replace("từ tiếng Trung sang", f"từ {src_vi} sang", 1).replace("{fill}", FILL_RULE_VI if fill else "")
        sp = sp.replace("{addr}", ADDR_RULE_VI[addr])
        return sp + (f"\n\n{GENRE_HEAD_VI}:\n{genre.strip()}" if (genre or "").strip() else "")
    L = langs.INFO[target]
    fill_en = FILL_RULE_EN.format(unit=L["unit_ai"]) if fill else ""
    addr_rule = ADDR_RULE_EN[addr].format(lang=L["english"], neutral=_NEUTRAL_EN.get(target, ""))
    thai_note = " Thai: the polite particle ครับ / ค่ะ follows the SPEAKER's gender." if target == "th" else ""
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
5. FORMS OF ADDRESS FOLLOW THE SOURCE LINE – NEVER CHANGE WHO IS WHO:
   a) When the source line states a pronoun / kinship or rank term, keep exactly its gender, seniority (older / younger,
      higher / lower), person and number: 他 = he (male) – never she; 她 = she (female) – never he; 它 = it; 哥 / 哥哥 /
      大哥 / 师兄 = older brother / senior (male, older) – never younger or female; 姐 / 姐姐 / 师姐 = older sister /
      senior (female); 弟弟 / 师弟 = younger brother / junior; 妹妹 / 师妹 = younger sister / junior; 爸 / 妈 / 叔 / 阿姨
      = father / mother / uncle / auntie; 您 = respectful "you"; 我 / 我们 first person, 你 / 你们 second person, 他 / 她 /
      他们 third person – never turn a line ABOUT someone into a line TO the listener; keep singular / plural.
   b) Context (rule 2) only picks the tone within that same role{_ADDRESS[target]} – never use it to switch male ↔
      female, older ↔ younger, senior ↔ junior or 1st ↔ 2nd ↔ 3rd person that the source states.
   c) Only when the source line has no pronoun / term (Chinese often drops the subject) or only 我 / 你 (no gender or
      rank) choose the form of address from the speakers (e), context, relationship, age and status (FILM GENRE, FILM
      NOTES), keeping the same pair the earlier lines used between the same two people, consistently for the whole
      film. If that is still not enough to be sure, follow f).
   d) FILM GENRE, FILM NOTES, the user's SKILL and the video are only hints for case c); if they contradict a pronoun /
      term written in the source line, follow the source line.
   e) SPEAKERS: the section "SPEAKER OF EACH LINE" (when present) gives the speaker of each line (S1, S2… told apart by
      voice) with the gender measured from the voice ("?" = unknown); CONTEXT lines carry the same tag [S1 · male]. Use
      it to know who talks to whom: the listener is usually the OTHER speaker of the line right before / after. The
      measured gender can be wrong: a pronoun written in the source line (a) and a pair of address already clear in
      earlier lines between the same two people come first.{thai_note}
   f) UNSURE FORM OF ADDRESS – the source line has only 你 / 我 or no subject, and it is unclear whether the speaker /
      listener is male or female or who is older, and earlier lines have no settled pair between the same two people:
      {addr_rule}
      For every line handled by f) add "addr": "?" to that line's JSON entry (rule 13).
6. Names of people, places, organizations and techniques: use {_NAMES[target]}. One name, one rendering. ALWAYS follow
   the GLOSSARY. Terms listed in the FILM GENRE apply only when the source line contains that exact term.
7. Source lines may contain speech-recognition errors (homophones): use the context (rule 2) to get the intended meaning.
   Pronouns 他 / 她 / 它 and kinship / rank terms are NOT treated as recognition errors – translate what is written (rule 5).
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
   punctuation or spaces) – used to check that each translation sits on the right line. A line handled by rule 5f also
   gets "addr": "?" – {{"head": "…", "text": "…", "addr": "?"}}; other lines have no "addr"."""
    return sp + (f"\n\n{GENRE_HEAD_EN}:\n{genre.strip()}" if (genre or "").strip() else "")


SYL_PER_SEC = 4.6      # tiếng Việt lồng tiếng tự nhiên ~4–5 tiếng/giây (tiếng khác: langs.INFO[...]["rate"])
BUDGET_RE = re.compile(r"\s*⟪[^⟫]*⟫\s*")
HAN_RE = re.compile(r"[㐀-鿿]")

SHORTEN_PROMPT = """Bạn là biên tập viên lồng tiếng phim. Mỗi mục là 1 câu thoại: câu gốc tiếng Trung ("goc"), bản dịch
tiếng Việt hiện tại ("dich", dài "so_tieng" tiếng), số tiếng (âm tiết) TỐI ĐA được dùng ("toi_da"); có thể kèm thời gian
giọng lồng đang đọc ("giong_dang_doc"), thời gian nhân vật nói / chỗ trống phải vừa ("nhan_vat_noi" / "cho_trong") và số
tiếng cần bớt ("can_bot"). Giọng lồng phải đọc xong trong thời gian đó, nên câu mới KHÔNG ĐƯỢC dài hơn "toi_da" tiếng
(đếm từng tiếng cách nhau bằng dấu cách, kể cả từ đệm).
ĐƯỢC: bỏ từ thừa / từ đệm / chữ lặp, bỏ chủ ngữ hoặc tân ngữ khi đã rõ, dùng từ ngắn hơn cùng nghĩa, nói gọn hơn, gộp
2 ý nhỏ thành 1.
KHÔNG ĐƯỢC: bỏ ý chính, đổi nghĩa, thêm ý mới, đổi câu hỏi thành câu kể, bỏ dấu câu cuối, đổi / bỏ tên riêng, đổi xưng hô
(đại từ, cách gọi – không đổi nam / nữ, anh / em, ngôi).
Mỗi mục viết 2 phương án: "a" = ngắn vừa đủ (không quá "toi_da" tiếng, giữ được nhiều ý nhất), "b" = ngắn hơn nữa
(vẫn đủ ý chính). Chỉ trả về JSON {"1": {"a": "…", "b": "…"}, ...} không kèm giải thích."""

SHORTEN_RETRY_VI = ("CÁC BẢN SAU VẪN DÀI HƠN \"toi_da\" – \"ban_vua_viet\" là bản bạn vừa viết (dài \"ban_vua_viet_dai\"). "
                    "Viết lại NGẮN HƠN NỮA cho không quá \"toi_da\" tiếng, vẫn giữ ý chính:")


SHORTEN_PROMPT_EN = """You are a film dubbing script editor. Each item is one line: the source line ("source"), the current
{lang} translation ("translation", "length" {unit} long) and the MAXIMUM length in {unit} ("max"); it may also give how
long the dubbed voice reads now ("voice_now"), how long the character speaks / the room it must fit ("speech" / "room")
and how many {unit} to cut ("cut"). The voice must finish within that time, so the new line must NOT exceed "max".
ALLOWED: drop filler / repeated words, drop a subject or object that is already clear, use shorter words with the same
meaning, phrase it tighter, merge 2 small ideas into 1.
NOT ALLOWED: dropping the core meaning, changing the meaning, adding new ideas, turning a question into a statement,
dropping the final punctuation, changing / dropping names, changing forms of address (never change he / she, older /
younger, or the grammatical person).
Write 2 versions per item: "a" = just short enough (within "max", keeping as much meaning as possible), "b" = shorter
still (core meaning kept). Return ONLY JSON {{"1": {{"a": "…", "b": "…"}}, ...}} with no explanation."""

SHORTEN_RETRY_EN = ("THESE ARE STILL LONGER THAN \"max\" – \"your_last\" is the version you just wrote (\"your_last_length\" "
                    "long). Rewrite them SHORTER still, within \"max\", keeping the core meaning:")


CLAUSE_PROMPT = """Bạn là biên tập viên lồng tiếng phim. Mỗi mục có câu gốc đã chia thành các VẾ theo chỗ nhân vật ngắt hơi
("ve_goc") và bản dịch tiếng Việt hiện tại ("dich"). Viết lại bản dịch cho có ĐÚNG số vế như câu gốc: các vế nối nhau bằng
dấu phẩy (hoặc ? ! nếu vế gốc là câu hỏi / cảm thán), vế thứ i dịch nghĩa vế gốc thứ i, đúng thứ tự – để giọng lồng ngắt
nghỉ đúng lúc nhân vật ngắt hơi. Mỗi vế phải là 1 cụm TRỌN NGHĨA đọc lên tự nhiên: không chen dấu phẩy giữa lượng từ và
danh từ, danh từ và tính từ, động từ và tân ngữ, giữa các tiếng của tên riêng / thành ngữ – không làm được thì giữ nguyên
bản dịch cũ. Giữ nguyên ý, xưng hô (không đổi nam / nữ, anh / em, ngôi), tên riêng, dấu câu cuối, độ dài gần như cũ;
chỉ đổi trật tự / cách nói khi cần.
Chỉ trả về JSON {"1": "bản dịch mới", ...} không kèm giải thích."""

CLAUSE_PROMPT_EN = """You are a film dubbing script editor. Each item has the source line split into CLAUSES where the
character pauses for breath ("source_clauses") and the current {lang} translation ("translation"). Rewrite the translation
so it has EXACTLY the same number of clauses: join them with commas (or ? ! when the source clause is a question /
exclamation), clause i translates source clause i, in order – so the dubbed voice pauses with the character. Each clause
must be a COMPLETE, natural phrase: never put a comma between a determiner / numeral and its noun, a noun and its modifier,
a verb and its object, or inside a name / idiom – if that is impossible, return the current translation unchanged. Keep the
meaning, forms of address (never switch he / she, older / younger, person), names, final punctuation and about the
same length; only reorder / rephrase when needed.
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


def _sec_text(x: float) -> str:
    return f"{float(x):.1f}".replace(".", ",") + " giây"


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

    def _content(self, user: str, images: list | None):
        """Nội dung lời nhắn người dùng: chữ, hoặc (2.1.44 – AI xem ảnh) chữ + [(nhãn, ảnh JPEG)] xen kẽ."""
        if not images:
            return user
        import base64

        out: list[dict] = [{"type": "text", "text": user}]
        for lab, data in images:
            if lab:
                out.append({"type": "text", "text": lab})
            b64 = base64.b64encode(data).decode("ascii")
            if self.engine == "anthropic":
                out.append({"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": b64}})
            else:
                out.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}})
        return out

    def chat(self, system: str, user: str, max_tokens: int = 8000, temperature: float = 0.3,
             images: list | None = None) -> str:
        """Gửi 1 lần. max_tokens: độ dài trả lời cần (lô dịch dài xin nhiều hơn – 2.1.23); model có giới hạn thấp hơn (API
        báo 400) -> tự hạ theo đúng giới hạn đó. Trả lời bị cắt vì hết giới hạn -> `self._tl.cut` = True (theo luồng).
        images (2.1.44): [(nhãn, ảnh JPEG)] gửi kèm – chỉ AI xem được ảnh (Gemini / OpenAI / Claude / Ollama mô hình ảnh)."""
        last_err = None
        ucontent = self._content(user, images)
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
                            "system": system, "messages": [{"role": "user", "content": ucontent}]}
                    if self._send_temp:
                        body["temperature"] = temperature
                    r = net.call(self._http, "https://api.anthropic.com/v1/messages",
                                 {"x-api-key": self.api_key, "anthropic-version": "2023-06-01",
                                  "content-type": "application/json"}, body, read)
                    if r.status_code == 200:
                        d = r.json()
                        self._tl.cut = d.get("stop_reason") == "max_tokens"
                        _note_ok(self.api_key)
                        return "".join(b.get("text", "") for b in d.get("content", []) if b.get("type") == "text")
                else:
                    body = {"model": self.model,
                            "messages": [{"role": "system", "content": system}, {"role": "user", "content": ucontent}]}
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
                        if self.engine != "ollama":
                            _note_ok(self.api_key)
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
                    note_key(self.api_key, why_state(why), why, "dich")    # Cài đặt hiện cạnh key này
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
    """Thử 1 key: 1 dòng ✓ / ⚠ / ✗ + lý do (xem probe_key)."""
    return probe_key(settings, engine, key)["text"]


# ----------------------------------------------------------------------- kiểm tra key (2.1.26)
# Trạng thái 1 key: (biểu tượng, chữ ngắn). "Sống" = gọi được (kể cả đang bị giới hạn số lần / phút).
KEY_STATES = {
    "ok": ("✓", "Sống"),
    "busy": ("✓", "Sống – đang bị giới hạn số lần / phút"),
    "model": ("⚠", "Key sống – model không có"),
    "server": ("⚠", "Máy chủ đang bận / lỗi"),
    "net": ("⚠", "Không kết nối được"),
    "day": ("✗", "Hết lượt miễn phí hôm nay"),
    "money": ("✗", "Hết tiền / hết hạn mức"),
    "bad": ("✗", "Key sai / hết hạn / bị khoá"),
    "region": ("✗", "Không dùng được ở khu vực này"),
    "error": ("✗", "Lỗi"),
}
ALIVE = ("ok", "busy", "model")       # key còn dùng được (model sai thì chỉ cần chọn model khác)
DEAD = ("bad", "money")               # nên xoá / thay key
PROBE_READ = 40.0                     # chờ trả lời tối đa (model suy luận nghĩ hơi lâu)
_STATUS_LOCK = threading.Lock()
_OK_NOTED: dict[str, float] = {}      # key -> lần cuối ghi "sống" khi dịch (không ghi file mỗi lô)


def key_fp(key: str) -> str:
    """Dấu nhận ra key (không lưu chính key ra file trạng thái)."""
    import hashlib

    return hashlib.sha256((key or "").strip().encode()).hexdigest()[:16]


def _status_file():
    from .config import DATA_DIR

    return DATA_DIR / "key_status.json"


def key_status(key: str) -> dict | None:
    """Kết quả gần nhất của key: {"s": trạng thái, "m": chữ, "t": giờ, "src": "check" | "dich"}."""
    try:
        return json.loads(_status_file().read_text(encoding="utf-8")).get(key_fp(key))
    except (OSError, ValueError, AttributeError):
        return None


def note_key(key: str, state: str, msg: str = "", src: str = "check") -> None:
    """Ghi trạng thái key vào data/key_status.json (Cài đặt hiện cạnh từng key lần mở sau)."""
    if not key or key == "ollama":
        return
    f = _status_file()
    with _STATUS_LOCK:
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
            d = d if isinstance(d, dict) else {}
        except (OSError, ValueError):
            d = {}
        d[key_fp(key)] = {"s": state, "m": msg[:200], "t": round(time.time()), "src": src}
        if len(d) > 300:      # giữ 300 key gần nhất
            d = dict(sorted(d.items(), key=lambda x: x[1].get("t", 0))[-300:])
        try:
            f.parent.mkdir(parents=True, exist_ok=True)
            tmp = f.with_suffix(".tmp")
            tmp.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
            tmp.replace(f)
        except OSError:
            pass


def _note_ok(key: str) -> None:
    """Dịch được bằng key này -> ghi "sống" (tối đa 10 phút 1 lần / key, hoặc khi trước đó key bị ghi hỏng)."""
    now = time.time()
    if now - _OK_NOTED.get(key, 0) < 600:
        return
    _OK_NOTED[key] = now
    old = key_status(key) or {}
    if old.get("s") != "ok" or now - float(old.get("t") or 0) > 3600:
        note_key(key, "ok", "dịch được", "dich")


def why_state(why: str) -> str:
    """Lý do của LLMClient._key_failed -> trạng thái key."""
    return "day" if "hôm nay" in why else "money" if "tiền" in why else "bad"


def _err_msg(r) -> str:
    """Câu báo lỗi ngắn gọn trong trả lời lỗi của API (JSON {"error": {"message"}} / [{"error": …}] / chữ thường)."""
    try:
        d = r.json()
        if isinstance(d, list) and d:
            d = d[0]
        e = d.get("error") if isinstance(d, dict) else None
        m = (e.get("message") if isinstance(e, dict) else e) or (d.get("message") if isinstance(d, dict) else "")
        if m:
            return " ".join(str(m).split())[:180]
    except Exception:  # noqa: BLE001
        pass
    return " ".join((r.text or "").split())[:180]


def _when(t: float) -> str:
    lt, now = time.localtime(t), time.localtime()
    hm = time.strftime("%H:%M", lt)
    if lt.tm_yday == now.tm_yday and lt.tm_year == now.tm_year:
        return hm + " hôm nay"
    if time.time() - t < 2 * 86400 and (now.tm_yday - lt.tm_yday) % 366 == 1:
        return hm + " hôm qua"
    return time.strftime("%H:%M %d/%m", lt)


def status_text(st: dict | None) -> str:
    """Dòng hiện cạnh key: "✓ Sống · kiểm tra 10:32 hôm nay" (trạng thái đã lưu)."""
    if not st or st.get("s") not in KEY_STATES:
        return ""
    icon, name = KEY_STATES[st["s"]]
    how = "khi dịch" if st.get("src") == "dich" else "kiểm tra"
    extra = f" – {st['m']}" if st.get("m") and st["s"] not in ("ok",) else ""
    old = ""
    if st["s"] == "day" and time.localtime(st.get("t", 0)).tm_yday != time.localtime().tm_yday:
        old = " (đã sang ngày mới – có thể đã có lượt lại, bấm Kiểm tra)"
    return f"{icon} {name}{extra} · {how} {_when(float(st.get('t') or 0))}{old}"


def _probe_ollama(settings: dict, read: float) -> dict:
    base = (settings.get("ollama_base_url") or "http://localhost:11434/v1").rstrip("/")
    model = settings.get("ollama_model") or "qwen2.5:7b"
    root = base[:-3] if base.endswith("/v1") else base
    t0 = time.time()
    try:
        r = net.session().get(f"{root}/api/tags", timeout=(5, min(read, 15)))
    except requests.RequestException:
        return {"state": "net", "msg": f"Ollama chưa chạy ở {root} – mở ứng dụng Ollama trên máy", "model": model}
    names: list[str] = []
    if r.status_code == 200:
        try:
            names = [m.get("name") or m.get("model") or "" for m in r.json().get("models", [])]
        except (ValueError, AttributeError):
            names = []
    else:      # máy chủ tương thích OpenAI khác ở địa chỉ này
        try:
            r = net.session().get(f"{base}/models", timeout=(5, min(read, 15)))
            names = [m.get("id", "") for m in r.json().get("data", [])] if r.status_code == 200 else []
        except (requests.RequestException, ValueError, AttributeError):
            names = []
        if r.status_code != 200:
            return {"state": "error", "msg": f"HTTP {r.status_code}: {_err_msg(r)}", "model": model}
    short = {n.split(":")[0] for n in names}
    ms = int((time.time() - t0) * 1000)
    if model in names or f"{model}:latest" in names or (":" not in model and model in short):
        return {"state": "ok", "msg": f"Ollama đang chạy, có model {model}", "model": model, "ms": ms}
    have = ", ".join(names[:5]) or "chưa có model nào"
    return {"state": "model", "msg": f"Ollama đang chạy nhưng chưa có model '{model}' (có: {have}) – chạy lệnh "
                                     f"'ollama pull {model}' hoặc chọn model khác", "model": model, "ms": ms}


def probe_key(settings: dict, engine: str, key: str = "", read: float = PROBE_READ) -> dict:
    """Kiểm tra 1 key bằng ĐÚNG 1 lần gửi thật ngắn (không chờ, không gửi lại, không đổi key – khác lúc dịch) với model
    đang chọn. Trả {"state" (KEY_STATES), "msg", "model", "ms", "text"}. Cập nhật luôn danh sách key hỏng của phiên
    (key sống lại -> lần dịch sau dùng ngay; key hỏng -> bỏ qua) + ghi data/key_status.json."""
    if engine == "ollama":
        res = _probe_ollama(settings, read)
    else:
        res = _probe(settings, engine, key, read)
        st = res["state"]
        if st in ALIVE:
            _KEY_DEAD.pop(key, None)
        elif st in ("bad", "money", "day"):
            _KEY_DEAD[key] = (time.time() + (10 ** 9 if st == "bad" else DAY_BLOCK), KEY_STATES[st][1].lower())
        if st != "net":      # mất mạng: không phải lỗi của key -> không ghi đè kết quả cũ
            note_key(key, st, res.get("msg", ""), "check")
    icon, name = KEY_STATES.get(res["state"], ("✗", "Lỗi"))
    tail = f" ({res['ms'] / 1000:.1f} s)" if res.get("ms") and res["state"] == "ok" else ""
    res["text"] = f"{icon} {name}{tail}" + (f" – {res['msg']}" if res.get("msg") else "")
    return res


def _probe(settings: dict, engine: str, key: str, read: float) -> dict:
    st = dict(settings, **{f"{engine}_api_key": key, f"{engine}_api_keys": [key]})
    c = LLMClient(st, engine)
    model = c.model
    out = {"model": model}
    ask = 256 if c.thinking or engine == "anthropic" else 16
    max_key = c._max_key
    user = "Reply with the single word OK."
    t0 = time.time()
    r = None
    for _ in range(3):         # 400 vì tên tham số độ dài trả lời (max_tokens / max_completion_tokens) -> đổi, gửi lại
        try:
            if engine == "anthropic":
                r = net.session().post("https://api.anthropic.com/v1/messages",
                                       headers={"x-api-key": key, "anthropic-version": "2023-06-01",
                                                "content-type": "application/json"},
                                       json={"model": model, "max_tokens": ask,
                                             "messages": [{"role": "user", "content": user}]}, timeout=(10, read))
            else:
                r = net.session().post(f"{c.base}/chat/completions", headers={"Authorization": f"Bearer {key}"},
                                       json={"model": model, "messages": [{"role": "user", "content": user}],
                                             max_key: ask}, timeout=(10, read))
        except requests.RequestException as e:
            why = "hết thời gian chờ trả lời" if isinstance(e, requests.Timeout) else "mạng / tường lửa / máy chủ"
            return dict(out, state="net", msg=f"{why} – thử lại sau")
        t = r.text or ""
        if r.status_code == 400 and max_key == "max_tokens" and "max_completion_tokens" in t:
            max_key = "max_completion_tokens"
            continue
        if r.status_code == 400 and max_key == "max_completion_tokens" and "max_tokens" in t \
                and "max_completion_tokens" not in t:
            max_key = "max_tokens"
            continue
        break
    ms = int((time.time() - t0) * 1000)
    code, t = r.status_code, (r.text or "").lower()
    msg = _err_msg(r)
    if code == 200:
        return dict(out, state="ok", msg=f"model {model} trả lời được", ms=ms)
    if "location is not supported" in t or "not available in your country" in t or "unsupported_country" in t:
        return dict(out, state="region", msg=msg)
    why = c._key_failed(r)
    if why:
        return dict(out, state=why_state(why), msg=msg)
    if code == 429:
        wait = LLMClient._retry_after(r)
        return dict(out, state="busy", msg=f"chờ ~{wait:.0f} s là gửi tiếp được")
    if code == 404 or "model_not_found" in t or ("model" in t and ("not found" in t or "does not exist" in t)):
        hint = " hoặc Base URL sai" if engine == "openai" else ""
        return dict(out, state="model", msg=f"không có model '{model}'{hint} – bấm ☰ cạnh ô Model để chọn ({msg})")
    if code >= 500:
        return dict(out, state="server", msg=f"HTTP {code} – key có thể vẫn sống, thử lại sau ({msg})")
    return dict(out, state="error", msg=f"HTTP {code}: {msg}")


def probe_summary(engine: str, results: list[dict]) -> str:
    """"Gemini: 2/3 key sống (1 hết lượt hôm nay)"."""
    name = ENGINE_NAMES.get(engine, engine)
    if engine == "ollama":
        r = results[0] if results else {}
        return f"{name}: {KEY_STATES.get(r.get('state'), ('', 'chưa kiểm tra'))[1].lower()}"
    if not results:
        return f"{name}: chưa có key"
    alive = sum(r.get("state") in ALIVE for r in results)
    cnt: dict[str, int] = {}
    for r in results:
        if r.get("state") not in ("ok",):
            cnt[r.get("state", "error")] = cnt.get(r.get("state", "error"), 0) + 1
    det = ", ".join(f"{n} {KEY_STATES.get(s, ('', s))[1].lower()}" for s, n in cnt.items())
    return f"{name}: {alive}/{len(results)} key sống" + (f" ({det})" if det else "")


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
mà chọn xưng hô cho dòng KHÔNG ghi rõ đại từ / cách gọi (luật 5c) – dòng gốc đã ghi rõ (他 / 她, 哥 / 姐…) thì vẫn theo
dòng gốc dù hình cho thấy khác; hiểu câu ngắn theo hành động trên hình; đọc chữ trên màn hình (tin nhắn, biển hiệu, tên
nhân vật) làm ngữ cảnh. Vẫn dịch đúng CHỮ của từng dòng (tai nghe khác chữ dòng thì vẫn theo chữ dòng), giữ đủ mọi luật
trên.
Thêm 1 khoá "_nguoi_noi" vào JSON: {"S1": {"gioi": "nam" | "nu", "ten": "<tên nhân vật nếu biết>"}} cho người nói có
trong lô – chỉ ghi khi thấy rõ người đó trên hình."""
VIDEO_RULE_EN = """

ATTACHED VIDEO: you can WATCH + HEAR the video clip containing these lines (each line's time – seconds from the start of
the clip – and speaker are in VIDEO TIMES). Use picture + voice to know who is talking to whom (male / female, age,
rank, closeness) and choose the form of address for lines WITHOUT an explicit pronoun / kinship term (rule 5c) – a
pronoun / term written in the line (他 / 她, 哥 / 姐…) still wins over what the picture suggests; understand short lines
from the action on screen; read on-screen
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


XH_MARK = "\ue05f"   # dòng AI báo "không chắc xưng hô" (luật 5f) – đánh dấu đầu bản dịch trong lúc dịch, bỏ ở cuối
GENDER_KEY_RE = re.compile(r"\s*⟦[MF]⟧$")   # đuôi khoá bộ nhớ bản dịch theo nam / nữ người nói (2.4.0)


def _addr_flags(d: dict, n: int) -> list[bool]:
    """JSON của AI -> dòng nào có "xh" / "addr": "?" (không chắc xưng hô, luật 5f)."""
    out = []
    for i in range(n):
        v = d.get(str(i + 1))
        x = v.get("xh", v.get("addr")) if isinstance(v, dict) else None
        out.append(x is True or (isinstance(x, str) and x.strip().lower() in ("?", "khong_chac", "không chắc", "unsure")))
    return out


def speaker_tag(spk: str, gender: str, target: str = "vi") -> str:
    """Nhãn người nói gửi cho AI: "S2 · nữ" / "S1 · nam" / "S3 · ?" (tiếng khác: male / female)."""
    if langs.norm(target) == "vi":
        g = {"male": "nam", "female": "nữ"}.get(gender or "", "?")
    else:
        g = {"male": "male", "female": "female"}.get(gender or "", "?")
    return f"{spk or 'S1'} · {g}"


def _tag_gender(tag: str) -> str:
    g = (tag or "").rsplit("·", 1)[-1].strip()
    return "F" if g in ("nữ", "female") else "M" if g in ("nam", "male") else ""


def cache_text(key: str) -> str:
    """Câu gốc (bỏ ký hiệu độ dài ⟪⟫ và đuôi nam / nữ ⟦M⟧ / ⟦F⟧) trong 1 khoá bộ nhớ bản dịch."""
    return BUDGET_RE.sub("", GENDER_KEY_RE.sub("", cache_line(key))).strip()


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
        self.addr = addr_mode(settings)   # không chắc xưng hô: bỏ đại từ / cặp trung tính / AI tự đoán (luật 5f, 2.4.0)
        # nam / nữ từng người nói (p.data["speaker_gender"], pipeline đặt) -> gửi kèm mỗi dòng cho AI biết ai nói với ai
        self.genders: dict[str, str] = {}
        self.addr_flags: list[bool] = []   # translate_lines: dòng AI báo "không chắc xưng hô" (luật 5f)
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
        tags = self.speaker_tags(segs)
        who = [tags[i] for i in idx] if tags else None
        near = self._near(segs, idx, tags) if self.llm else None
        results = self.translate_lines(texts, progress, cancel, summary=summary, who=who, pos=idx, near=near, **kw)
        flags = self.addr_flags
        for n, (i, t) in enumerate(zip(idx, results)):
            segs[i].trans = BUDGET_RE.sub(" ", t or "").strip()
            segs[i].extra.pop("trans_user", None)
            if self.llm:   # AI báo không chắc xưng hô (luật 5f) -> tô màu ở bảng câu, nút "Tích câu xưng hô chưa chắc"
                if n < len(flags) and flags[n] and segs[i].trans:
                    segs[i].extra["addr_unsure"] = True
                else:
                    segs[i].extra.pop("addr_unsure", None)
            if segs[i].trans and not untranslated(segs[i].trans, self.target):   # nhớ theo câu gốc -> không tốn API
                f = n < len(flags) and flags[n]
                self.cache.setdefault(cache_key(self.engine, self.style, self.target,
                                                segs[i].text + self._gkey(who[n] if who else "")),
                                      (XH_MARK if f else "") + segs[i].trans)
        return segs

    def speaker_tags(self, segs: list[Segment]) -> list[str] | None:
        """Nhãn người nói + nam / nữ từng câu ("S2 · nữ") – chỉ khi dịch bằng AI và phim có ≥ 2 người nói (1 người nói
        = chưa phân biệt người nói: nhãn chỉ làm AI tưởng cả phim 1 người tự nói)."""
        if not self.llm or not self.s.get("translate_speakers", True):
            return None
        spks = {(x.speaker or "S1") for x in segs if x.text.strip()}
        if len(spks) < 2:
            return None
        return [speaker_tag(x.speaker or "S1", self.genders.get(x.speaker or "S1", ""), self.target) for x in segs]

    def _near(self, segs: list[Segment], idx: list[int], tags: list[str] | None) -> list | None:
        """Dịch 1 phần phim (câu đã tích / dòng trống): dòng liền trước / sau TRONG PHIM của mỗi câu mà không nằm ngay
        cạnh nó trong danh sách gửi đi -> mục "NGỮ CẢNH QUANH TỪNG DÒNG" (trước 2.4.0 AI coi câu đã tích bên cạnh là
        dòng trước / sau thật -> hiểu sai ai nói với ai)."""
        def show(j: int) -> str:
            x = segs[j]
            if not x.text.strip():
                return ""
            t = (f"[{tags[j]}] " if tags else "") + x.text.strip()
            return t + (f" => {x.trans.strip()}" if x.trans.strip() else "")

        out, any_ = [], False
        for n, i in enumerate(idx):
            b = "" if (n and idx[n - 1] == i - 1) or i == 0 else show(i - 1)
            a = "" if (n + 1 < len(idx) and idx[n + 1] == i + 1) or i + 1 >= len(segs) else show(i + 1)
            out.append((b, a) if (b or a) else None)
            any_ = any_ or bool(b or a)
        return out if any_ and len(idx) < len(segs) else None

    def _gkey(self, tag: str) -> str:
        """Đuôi khoá bộ nhớ bản dịch theo nam / nữ người nói: "随你" của nam và của nữ dịch riêng (2.4.0)."""
        g = _tag_gender(tag) if (self.llm and tag) else ""
        return f" ⟦{g}⟧" if g else ""

    def _shorten_call(self, items: list[tuple[str, str, int]], info: list, prev: list | None = None) -> list[list[str]]:
        """1 lần gửi AI rút gọn: trả mỗi mục các bản AI viết (đã làm sạch, bỏ bản còn chữ Hán). prev = bản lần trước
        vẫn dài (hỏi lại)."""
        L = langs.INFO[self.target]
        vi = self.target == "vi"
        payload = {}
        for k, (zh, tr, n) in enumerate(items):
            it = info[k] or {}
            cur = syllables(tr, self.target)
            if vi:
                d = {"goc": zh, "dich": tr, "so_tieng": cur, "toi_da": f"{n} tiếng"}
                if it.get("now"):
                    d["giong_dang_doc"] = _sec_text(it["now"])
                if it.get("have"):
                    d["cho_trong" if it.get("what") == "slot" else "nhan_vat_noi"] = _sec_text(it["have"])
                if it.get("cut"):
                    d["can_bot"] = f"khoảng {it['cut']} tiếng"
                if prev and prev[k]:
                    d["ban_vua_viet"], d["ban_vua_viet_dai"] = prev[k], f"{syllables(prev[k], 'vi')} tiếng"
            else:
                d = {"source": zh, "translation": tr, "length": f"{cur} {L['unit_ai']}", "max": f"{n} {L['unit_ai']}"}
                if it.get("now"):
                    d["voice_now"] = f"{it['now']:.1f} s"
                if it.get("have"):
                    d["room" if it.get("what") == "slot" else "speech"] = f"{it['have']:.1f} s"
                if it.get("cut"):
                    d["cut"] = f"about {it['cut']} {L['unit_ai']}"
                if prev and prev[k]:
                    d["your_last"] = prev[k]
                    d["your_last_length"] = f"{syllables(prev[k], self.target)} {L['unit_ai']}"
            payload[str(k + 1)] = d
        if vi:
            sysp = SHORTEN_PROMPT
            head = SHORTEN_RETRY_VI if prev else "RÚT GỌN CÁC BẢN DỊCH SAU:"
        else:
            sysp = SHORTEN_PROMPT_EN.format(lang=L["english"], unit=L["unit_ai"])
            head = SHORTEN_RETRY_EN if prev else "SHORTEN THESE TRANSLATIONS:"
        user = head + "\n" + json.dumps(payload, ensure_ascii=False, indent=0)
        d = _extract_json(self.llm.chat(with_skill(sysp, self.skill) + address_tail(self.target, self.addr), user))
        out = []
        for k in range(len(items)):
            v = d.get(str(k + 1)) if isinstance(d, dict) else None
            if isinstance(v, dict):
                vals = [v.get(x) for x in ("a", "b")] + [x for key, x in v.items() if key not in ("a", "b")]
            elif isinstance(v, list):
                vals = v
            else:
                vals = [v]
            got = []
            for x in vals:
                x = BUDGET_RE.sub(" ", x).strip() if isinstance(x, str) else ""
                x = self._post(x, items[k][0]) if x else ""
                if x and not untranslated(x, self.target) and x not in got:
                    got.append(x)
            out.append(got)
        return out

    def _best_short(self, cands: list[str], tr: str, n: int) -> tuple[str | None, bool]:
        """Chọn bản rút gọn: bản DÀI NHẤT mà không quá n (giữ nhiều ý nhất) -> (bản, True); không bản nào đủ ngắn ->
        bản ngắn nhất mà vẫn ngắn hơn bản cũ -> (bản, False); không có -> (None, False)."""
        cur = syllables(tr, self.target)
        ok = [c for c in cands if syllables(c, self.target) < cur]
        fit = [c for c in ok if syllables(c, self.target) <= n]
        if fit:
            return max(fit, key=lambda c: syllables(c, self.target)), True
        if ok:
            return min(ok, key=lambda c: syllables(c, self.target)), False
        return None, False

    def shorten(self, items: list[tuple[str, str, int]], info: list | None = None, retry: bool = True) -> list[str]:
        """Rút gọn bản dịch quá dài so với thời gian nói. items: (câu gốc, bản dịch, số tiếng tối đa); info (2.3.6):
        mỗi mục {"now": giây giọng đang đọc, "have": giây phải vừa, "cut": số tiếng cần bớt, "what": speech / slot}
        gửi kèm cho AI. AI viết 2 phương án -> chọn bản giữ ý nhiều nhất mà vẫn vừa số tiếng; bản nào vẫn quá số tiếng
        thì hỏi lại riêng các câu đó 1 lần (retry). Không bản nào ngắn hơn -> giữ bản cũ."""
        if not self.llm or not items:
            return [vi for _, vi, _ in items]
        info = list(info or []) + [None] * (len(items) - len(info or []))
        try:
            first = self._shorten_call(items, info)
        except (Cancelled, net.NetworkDown):
            raise
        except Exception:  # noqa: BLE001
            return [vi for _, vi, _ in items]
        out, redo, prev = [], [], []
        for k, (_zh, tr, n) in enumerate(items):
            best, ok = self._best_short(first[k], tr, n)
            out.append(best or tr)
            if not ok and retry:
                redo.append(k)
                prev.append(best or (min(first[k], key=lambda c: syllables(c, self.target)) if first[k] else None))
        if redo:
            try:
                second = self._shorten_call([items[k] for k in redo], [info[k] for k in redo], prev=prev)
            except (Cancelled, net.NetworkDown):
                raise
            except Exception:  # noqa: BLE001
                second = [[] for _ in redo]
            for j, k in enumerate(redo):
                tr, n = items[k][1], items[k][2]
                best, _ = self._best_short(second[j] + ([out[k]] if out[k] != tr else []), tr, n)
                if best:
                    out[k] = best
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
            d = _extract_json(self.llm.chat(with_skill(sysp, self.skill) + address_tail(self.target, self.addr), user))
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
                        times: list[tuple[float, float, str]] | None = None, who: list[str] | None = None,
                        pos: list[int] | None = None, near: list | None = None) -> list[str]:
        """times: (đầu, cuối, người nói) từng dòng – có thì mỗi lô gửi kèm khúc video của chính các dòng đó (dịch có nhìn
        hình, 2.1.25): lô gom theo cả số câu lẫn độ dài khúc video (≤ VIDEO_WIN). who: nhãn người nói + nam / nữ từng dòng
        ("S2 · nữ", 2.4.0); pos: vị trí từng dòng trong phim (ngữ cảnh trước / sau chỉ lấy dòng liền nhau thật); near:
        dòng liền trước / sau trong phim của dòng không nằm cạnh nó trong danh sách. Sau khi chạy: self.addr_flags = dòng
        AI báo không chắc xưng hô (luật 5f)."""
        out: list[str | None] = [None] * len(lines)
        keys = [cache_key(self.engine, self.style, self.target, l + self._gkey(who[i] if who else ""))
                for i, l in enumerate(lines)]
        todo = []
        for i, l in enumerate(lines):
            hit = self.cache.get(keys[i])
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
                    self.cache[keys[gi]] = r
        elif self.engine == "google":
            batches = self._google_batches([lines[i] for i in todo])
            pos_ = 0
            for chunk in batches:
                if cancel and cancel():
                    raise Cancelled()
                res = self._google(chunk)
                for j, r in enumerate(res):
                    gi = todo[pos_ + j]
                    out[gi] = r
                    if r:
                        self.cache[keys[gi]] = r
                pos_ += len(chunk)
                done += len(chunk)
                if progress:
                    progress(done / len(lines), f"Đang dịch {done}/{len(lines)} câu (Google)")
        else:
            def tagged(k: int) -> str:
                return f"[{who[k]}] {lines[k]}" if who else lines[k]

            def one(ids):
                # ngữ cảnh = các dòng ngay trước / sau trong danh sách – chỉ khi chúng liền nhau thật trong phim (pos)
                kb = []
                k = ids[0] - 1
                while k >= 0 and len(kb) < CTX_BEFORE and (pos is None or pos[k] == pos[k + 1] - 1):
                    kb.insert(0, k)
                    k -= 1
                ka = []
                k = ids[-1] + 1
                while k < len(lines) and len(ka) < CTX_AFTER and (pos is None or pos[k] == pos[k - 1] + 1):
                    ka.append(k)
                    k += 1
                ctx_before = [(tagged(k), out[k] or "") for k in kb]
                ctx_after = [tagged(k) for k in ka]
                return self._llm_batch([lines[i] for i in ids], ctx_before, ctx_after, summary,
                                       vt=[times[i] for i in ids] if times else None,
                                       who=[who[i] for i in ids] if who else None,
                                       near=[near[i] for i in ids] if near else None)

            def take(at: int) -> list[int]:
                """Lô tiếp theo từ vị trí at: ≤ số câu mỗi lần gửi; dịch có nhìn hình thì khúc video ≤ VIDEO_WIN."""
                ids = todo[at:at + self._size()]
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
                        self.cache[keys[gi]] = r
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
                at = 0
                while at < len(todo):
                    if cancel and cancel():
                        raise Cancelled()
                    ids = take(at)   # model trả lời ngắn hơn lô (đã biết) -> lô sau nhỏ lại
                    keep(ids, one(ids))
                    at += len(ids)
                    done += len(ids)
                    if progress:
                        progress(done / len(lines), f"AI đang dịch {done}/{len(lines)} câu ({self.engine})")
        flags, res = [], []
        for k, o in enumerate(out):
            o = o or ""
            flags.append(o.startswith(XH_MARK))
            res.append(self._post(o.replace(XH_MARK, ""), BUDGET_RE.sub(" ", lines[k])))
        self.addr_flags = flags
        return res

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
                    '"relations": mô tả ngắn quan hệ giữa các nhân vật chính (nam / nữ, ai lớn hơn – theo đúng đại từ '
                    "他 / 她 và cách gọi 哥 / 姐 / 弟 / 妹… trong kịch bản) và cách xưng hô tiếng Việt phù hợp.\n\n"
                    "KỊCH BẢN:\n" + ch)
            else:
                sysp = f"You are an expert translator of Chinese films into {L['english']}. Return JSON only."
                prompt = (
                    "Read the subtitle script below and return JSON with the keys:\n"
                    '"genre": the film genre\n'
                    f'"terms": object mapping PROPER NAMES in the source (people, nicknames, places, sects, '
                    f"organizations, techniques, treasures) -> their standard {L['english']} rendering "
                    f"({_NAMES.get(self.target, '')}). Only real proper names, no common words.\n"
                    f'"relations": short description of the main characters\' relationships (gender and who is older, '
                    f"exactly as the script's 他 / 她 and kinship terms 哥 / 姐 / 弟 / 妹 say) and suitable forms of "
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
        return with_skill(system_prompt(self.target, self.source, self.fit_dub and self.fill_short, self.genre, self.addr),
                          self.skill) + address_tail(self.target, self.addr)

    def _llm_batch(self, lines: list[str], ctx_before, ctx_after, summary: str, depth: int = 0,
                   vt: list[tuple[float, float, str]] | None = None, win: tuple[float, float] | None = None,
                   who: list[str] | None = None, near: list | None = None) -> list[str]:
        """Dịch 1 lô. ctx_before: [(câu gốc, bản dịch)] các dòng ngay trước lô; ctx_after: [câu gốc] các dòng ngay sau.
        Dòng thiếu / còn chữ Hán / bị LỆCH DÒNG ("dau" khớp dòng bên cạnh) được hỏi lại KÈM 2+ dòng trước / sau của chính
        nó (2.1.13 – trước đây hỏi lại 1 dòng lẻ với ngữ cảnh của cả lô, mất nghĩa)."""
        gl = {k: v for k, v in self.glossary.items() if any(k in l for l in lines)}
        vi = self.target == "vi"

        def tg(j: int) -> str:     # dòng trong lô dùng làm ngữ cảnh khi hỏi lại -> kèm nhãn người nói
            return f"[{who[j]}] {lines[j]}" if who and who[j] else lines[j]

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
            parts.append(f"{lab[2]}:\n" + "\n".join(f"- {BUDGET_RE.sub(' ', a).strip()}"
                                                     + (f" => {b.replace(XH_MARK, '')}" if b else "")
                                                     for a, b in ctx_before))
        if ctx_after:
            parts.append(f"{lab[3]}:\n" + "\n".join(f"- {BUDGET_RE.sub(' ', a).strip()}" for a in ctx_after))
        if near and any(near):   # dịch 1 phần phim: dòng liền trước / sau thật của từng dòng (2.4.0)
            rows = []
            for k, nb in enumerate(near):
                if nb:
                    b, a = nb
                    rows.append(f"{k + 1}: " + " | ".join(
                        x for x in ((("trước: " if vi else "before: ") + BUDGET_RE.sub(" ", b).strip()) if b else "",
                                    (("sau: " if vi else "after: ") + BUDGET_RE.sub(" ", a).strip()) if a else "") if x))
            parts.append(("NGỮ CẢNH QUANH TỪNG DÒNG (dòng liền trước / sau trong phim của dòng cần dịch – chỉ để hiểu ai nói "
                          "với ai, nghĩa, xưng hô; KHÔNG dịch):\n" if vi else
                          "CONTEXT AROUND EACH LINE (the film line right before / after it – only to know who speaks to "
                          "whom, meaning, forms of address; do NOT translate):\n") + "\n".join(rows))
        if who and any(who):     # người nói + nam / nữ từng dòng (2.4.0) – AI biết ai nói với ai mà chọn xưng hô
            parts.append(("NGƯỜI NÓI TỪNG DÒNG (S1, S2… = người nói phân biệt theo giọng; nam / nữ tool đo từ giọng, có "
                          "thể sai; ? = chưa rõ – xem luật 5e):\n" if vi else
                          "SPEAKER OF EACH LINE (S1, S2… told apart by voice; gender measured from the voice, may be "
                          "wrong; ? = unknown – see rule 5e):\n") + "\n".join(f"{k + 1}: {w or '?'}"
                                                                                for k, w in enumerate(who)))
        payload = {str(i + 1): l for i, l in enumerate(lines)}
        n = len(lines)
        if vt and self.video is not None:     # dịch có nhìn hình: khúc video của chính các dòng trong lô
            if win is None:   # hỏi lại phần thiếu / chia đôi lô: dùng lại đúng khúc video đã tải lên của lô
                win = (max(0.0, min(t[0] for t in vt) - VIDEO_PAD), max(t[1] for t in vt) + VIDEO_PAD)
            a = win[0]
            parts.append(("THỜI ĐIỂM TRONG VIDEO (giây tính từ đầu khúc video gửi kèm | người nói):\n" if vi else
                          "VIDEO TIMES (seconds from the start of the attached clip | speaker):\n") +
                         "\n".join(f"{k + 1}: {max(0.0, t0 - a):.1f}–{max(0.0, t1 - a):.1f} | "
                                    f"{(who[k] if who and who[k] else sp) or '?'}"
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
            xh = _addr_flags(d, n)
            res = [XH_MARK + r if (xh[i] and r) else r for i, r in enumerate(res)]   # không chắc xưng hô (luật 5f)
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
                    cb = (list(ctx_before) + [(tg(j), res[j]) for j in range(a)])[-CTX_BEFORE:]
                    ca = ([tg(j) for j in range(b + 1, n)] + list(ctx_after))[:CTX_AFTER]
                    sub = depth if a == k else depth + 1
                    for i, f in zip(grp, self._llm_batch([lines[i] for i in grp], cb, ca, summary, sub,
                                                         [vt[i] for i in grp] if vt else None, win,
                                                         [who[i] for i in grp] if who else None,
                                                         [near[i] for i in grp] if near else None)):
                        res[i] = f
                return res
        # lỗi hoặc thiếu nhiều dòng -> chia đôi lô rồi thử lại (mỗi nửa vẫn có ngữ cảnh của nửa kia)
        if n > 1 and depth < 4:
            mid = n // 2
            a = self._llm_batch(lines[:mid], ctx_before, ([tg(j) for j in range(mid, n)] + list(ctx_after))[:CTX_AFTER],
                                summary, depth + 1, vt[:mid] if vt else None, win, who[:mid] if who else None,
                                near[:mid] if near else None)
            b = self._llm_batch(lines[mid:], (list(ctx_before) + [(tg(j), a[j]) for j in range(mid)])[-CTX_BEFORE:],
                                ctx_after, summary, depth + 1, vt[mid:] if vt else None, win,
                                who[mid:] if who else None, near[mid:] if near else None)
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
