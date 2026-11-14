"""Kỹ năng dịch bằng AI (2.0.55) – dùng được với MỌI công cụ dịch AI (DeepSeek / Claude / GPT / Gemini / Ollama /
API tương thích OpenAI), không dùng với Google / Hy-MT2 (không phải AI hội thoại).

  1. Sửa lỗi nghe & nối câu (`clean_lines` -> `apply_cleanup`): máy nghe (ASR) hay nghe nhầm chữ đồng âm (升/生, 咬/摇,
     D神/地神…) và cắt câu sai chỗ (真 | 的假的, 最强 | AI。). AI đọc cả kịch bản, trả về chữ sửa + nhóm dòng cần gộp;
     tool kiểm tra (chữ mới phải gần chữ cũ, chỉ gộp dòng liền nhau, nghỉ ≤ 2,5 s, câu gộp ≤ 15 s) rồi mới áp dụng.
     Cùng lúc AI lọc chữ KHÔNG PHẢI lời thoại theo nội dung (không theo âm lượng): lời bài hát -> 🎵 (không lồng tiếng),
     câu máy tự bịa / chữ vô nghĩa -> bỏ, đoạn rác dính vào câu thoại -> xoá phần đó. mode "filter" = chỉ lọc (tự chạy
     trước khi dịch cả phim lần đầu, Cài đặt `ai_song_filter`).
  2. Hồ sơ phim (`make_profile`): thể loại, nhân vật, bảng xưng hô, tên riêng / meme – lưu trong dự án, sửa được, AI dùng
     cho mọi lần dịch sau (không đọc lại kịch bản mỗi lần).
  3. Kiểm tra bản dịch (`review_lines`): sai nghĩa, sót ý, xưng hô / tên không thống nhất, quá dài so với thời gian,
     còn chữ Hán -> sửa từng câu, ghi lý do.
  + Kỹ năng riêng của bạn (`skill_text`): luật viết tay (VD bộ kỹ năng đã soạn), gắn thêm vào lời nhắc của mọi bước trên.
"""
from __future__ import annotations

import difflib
import json
import re

from .subtitle import Segment

# kỹ năng có sẵn (chỉ đọc – muốn sửa thì chép thành kỹ năng của bạn)
BUILTIN = [
    {"id": "meme", "name": "Hài – meme mạng – AI / công nghệ", "builtin": True, "text": (
        "- Video hài / chế meme mạng Trung Quốc: giữ cái buồn cười, đổi chơi chữ sang cách nói Việt tương đương; câu đùa "
        "dịch xong phải vẫn buồn cười bằng tiếng Việt.\n"
        "- Từ lóng mạng: 我去 -> Trời đất / Ối giời; 不是哥们 -> Ủa alo; 嘚瑟 -> khoe / làm màu; 绝绝子 -> đỉnh của chóp; "
        "破防 -> vỡ tim / sập nguồn; 摆烂 -> buông xuôi; 内卷 -> cày cuốc; 躺平 -> nằm thẳng; 吃瓜 -> hóng hớt.\n"
        "- Tên công nghệ / AI viết đúng tên quốc tế: D神 = DeepSeek (nhân vật cá voi, gọi 'Thần D'), 小克 = Claude, GPT, "
        "Gemini, Kimi, 豆包 = Doubao, 通义 = Qwen; AGI, API, code giữ nguyên.\n"
        "- Tiếng cảm thán: 嗯 -> Ừm; 啊 -> Á / À / Ơ; 哦 -> Ờ; 嘎 -> Quác; 哈哈 -> Haha.\n"
        "- Bạn bè ngang hàng xưng tớ – cậu (hoặc tao – mày nếu cãi nhau), con – bố với người tạo ra / bố mẹ – chỉ khi dòng "
        "gốc không ghi rõ đại từ / cách gọi; ghi rõ (他 / 她, 哥 / 姐…) thì giữ đúng nam / nữ, vai vế.")},
    {"id": "dub_short", "name": "Lồng tiếng – vừa thời gian, dễ đọc", "builtin": True, "text": (
        "- Bản dịch để LỒNG TIẾNG: câu nói tự nhiên, độ dài theo ký hiệu ⟪…⟫ của từng dòng. Dài hơn mức tối đa -> VIẾT "
        "LẠI CẢ CÂU thành câu ngắn hơn, giữ ý chính, đúng nghĩa câu gốc (đổi cấu trúc câu, từ / thành ngữ ngắn, nói thẳng "
        "thay cách nói hình ảnh) – không xoá chữ làm câu cụt / vô nghĩa; không ngắn được thì giữ câu trọn nghĩa. "
        "Câu ngắn thì để ngắn – KHÔNG thêm từ đệm, tiếng cảm thán hay ý mới cho dài ra.\n"
        "- Tránh chuỗi số dài, ký hiệu, viết tắt khó đọc; số nhỏ viết bằng chữ.\n"
        "- Không thêm dấu ngoặc / chú thích trong câu; giữ dấu câu cuối (? ! .) để giọng đọc lên / xuống đúng.")},
    {"id": "co_trang_kn", "name": "Cổ trang – tiên hiệp (chuẩn truyện convert)", "builtin": True, "text": (
        "- Xưng hô cổ trang: ta – ngươi, huynh – đệ, tỷ – muội, sư phụ – đồ nhi, bổn tọa, lão phu, tại hạ, các hạ "
        "(dòng gốc ghi rõ 他 / 她 / 师兄 / 师姐 / 师弟 / 师妹… thì giữ đúng nam / nữ, vai vế đó).\n"
        "- Cảnh giới tu luyện, công pháp, pháp bảo, môn phái: Hán Việt (筑基 -> Trúc Cơ, 金丹 -> Kim Đan, 元婴 -> Nguyên Anh).\n"
        "- Giữ không khí cổ trang, tránh từ hiện đại (OK, xịn, đỉnh).")},
]

STD_ID = "chuan"
# kỹ năng dịch chung mặc định – SỬA ĐƯỢC (bấm "Sửa / thêm…" cạnh ô Kỹ năng dịch AI); xoá = về bản mặc định này
STD_SKILL = {"id": STD_ID, "name": "Dịch chuẩn – giữ đúng số câu, tên riêng chuẩn", "default": True, "text": (
    "- GIỮ ĐÚNG SỐ CÂU: mỗi dòng gốc dịch thành đúng 1 dòng, cùng số thứ tự. KHÔNG gộp dòng, KHÔNG tách dòng, KHÔNG bỏ "
    "dòng, KHÔNG chuyển chữ sang dòng bên cạnh – kể cả khi 1 câu bị cắt ngang giữa 2 dòng: phần nào ở dòng nào thì dịch "
    "ở dòng đó sao cho đọc liền mạch.\n"
    "- Dòng chỉ có tiếng cảm thán (嗯, 啊, 哦, 哈哈) vẫn dịch (Ừm, À, Ờ, Haha) – không để trống dòng nào.\n"
    "- Dịch theo NGỮ CẢNH: đọc ít nhất 2 dòng trước và 2 dòng sau mỗi dòng để hiểu ai đang nói với ai, đang nói chuyện "
    "gì; câu dịch phải khớp nghĩa với các dòng đó; câu nghe nhầm chữ đồng âm thì dịch theo ý đúng của ngữ cảnh.\n"
    "- TÊN RIÊNG: tên người, biệt danh, môn phái, tổ chức, chiêu thức, pháp bảo -> phiên âm Hán Việt chuẩn (李逍遥 -> Lý "
    "Tiêu Dao). ĐỊA DANH: có tên tiếng Việt quen dùng thì dùng tên đó (北京 -> Bắc Kinh, 上海 -> Thượng Hải, 长安 -> Trường "
    "An, 日本 -> Nhật Bản), không thì Hán Việt. Tên thương hiệu / công nghệ / người nước ngoài: đúng tên quốc tế (微信 -> "
    "WeChat, 抖音 -> Douyin, 马斯克 -> Musk).\n"
    "- Một tên dịch MỘT cách trong cả phim; theo đúng bảng thuật ngữ và hồ sơ phim nếu có.\n"
    "- XƯNG HÔ THEO ĐÚNG DÒNG GỐC: đại từ / cách gọi trong dòng gốc đã rõ (他 = nam, 她 = nữ, 哥 = anh, 姐 = chị, 弟 / 妹 "
    "= em…) thì giữ đúng giới tính, vai vế, ngôi – ngữ cảnh chỉ để chọn sắc thái (anh ấy / anh ta / hắn), KHÔNG đổi anh "
    "thành em, anh ta thành cô ta.\n"
    "- Dòng gốc chỉ có 你 / 我 hoặc bỏ chủ ngữ: xem người nói từng dòng (S1 · nam, S2 · nữ…) và các dòng trước – sau để "
    "biết ai nói với ai, dùng ĐÚNG cặp xưng hô 2 người đó đã dùng ở các dòng trước, nhất quán cả phim (nữ nói 随你 với "
    "nam -> \"Tùy anh.\", không phải \"Tùy em.\").\n"
    "- KHÔNG chắc ai nói với ai, nam hay nữ, ai lớn hơn: làm theo luật 5f – bỏ đại từ nếu câu vẫn tự nhiên, không bỏ được "
    "thì dùng cặp trung tính (hiện đại cậu – tớ, cổ trang / tu tiên ngươi – ta: 你炼体我画符修炼 -> \"Ngươi luyện thể, ta "
    "vẽ phù tu luyện.\"). KHÔNG đoán bừa anh / em, chị / em.\n"
    "- Dịch SÁT NGHĨA: không thêm từ, tiếng cảm thán, từ đệm, chú thích, ngoặc, giải thích; không bỏ ý; không thêm ý.")}

FILTER_RULES = """LỌC CHỮ KHÔNG PHẢI LỜI THOẠI – máy nghe chép cả nhạc nền, tiếng hát, tiếng động thành chữ. Chỉ dựa vào NỘI DUNG
chữ và GIỜ của dòng (không đoán theo âm lượng):
a) LỜI BÀI HÁT (nhạc nền, nhạc mở đầu / kết thúc phim, nhân vật hát) -> ghi vào "song". Dấu hiệu: câu văn vần / như thơ,
   nhiều dòng liền nhau dài đều nhau, vần ở cuối; lặp lại nguyên câu (điệp khúc); từ ngữ bài hát (爱, 心, 梦, 风, 雨, 月光,
   眼泪, 永远, 远方, 思念, 回忆, 等你, 天涯, 一生…) mà không ai trong cảnh đang nói về chuyện đó; KHÔNG trả lời, KHÔNG nối
   tiếp câu thoại trước / sau, không nhắc nhân vật / sự việc trong phim; cả cụm liền nhau ở đầu / cuối video; chữ ít mà
   kéo dài (dưới ~2 chữ mỗi giây – hát ngân); dòng tên bài hát / 作词 / 作曲 / 演唱.
b) KHÔNG PHẢI LỜI THOẠI -> ghi vào "noise": câu máy tự bịa khi chỉ có nhạc / tiếng ồn (请不吝点赞 订阅 转发 打赏…,
   字幕由…提供, 中文字幕, 优优独播剧场, 点赞关注, 谢谢观看 / 感谢收看 ở cuối video mà không nhân vật nào nói); chữ vô nghĩa
   không thành câu, không hợp ngữ cảnh; 1 chữ / 1 cụm lặp lại nhiều lần (啊啊啊啊啊, 嗯嗯嗯嗯) khi không ai đang la / cười.
c) Câu thoại có DÍNH một đoạn lời bài hát / chữ vô nghĩa ở đầu, cuối hoặc giữa -> ghi vào "fix" câu chỉ còn phần lời thoại
   (chỉ XOÁ bớt, không thêm chữ, không sửa chữ khác).
KHÔNG lọc: tiếng cảm thán thật trong lúc nói chuyện (嗯？, 啊！, 哈哈, 哇), câu trả lời ngắn (对, 好, 走), câu thoại ngắn mà có
ý; nhân vật đọc thơ / nói câu có vần trong lúc nói chuyện vẫn là lời thoại. Chưa chắc thì KHÔNG ghi."""

CLEAN_PROMPT = """Bạn là biên tập viên phụ đề tiếng Trung. Các dòng dưới đây do máy nghe (ASR) tạo ra: có thể nghe nhầm chữ
đồng âm / gần âm, cắt câu sai chỗ, và chép cả lời bài hát / tiếng động thành chữ. Đọc HẾT các dòng để nắm nội dung, nhân vật,
mạch chuyện, rồi:
1. Sửa chữ nghe nhầm theo ngữ cảnh (VD 升↔生, 咬↔摇, 胃袋↔尾带, D神↔地神, 北极熊↔北京雄, 得↔的). CHỈ sửa khi ngữ cảnh rõ
   ràng; không viết lại câu, không thêm ý, không dịch.
2. Nối câu bị cắt ngang: một từ / cụm bị tách sang dòng sau (VD dòng 3 "真", dòng 4 "的假的…"), hoặc đầu dòng sau thực ra là
   đuôi dòng trước ("我可是最强" | "AI。你先别…"). Cách sửa:
   - gộp 2–4 dòng LIỀN NHAU thành 1 (khoá "merge"), chữ của dòng gộp ghi ở "fix" của dòng ĐẦU nhóm; hoặc
   - chuyển phần chữ sang dòng bên cạnh (ghi "fix" cho CẢ 2 dòng).
   Chỉ gộp khi đúng là 1 câu bị cắt; không gộp 2 câu trọn vẹn của 2 người nói; không gộp dòng lời bài hát với lời thoại.
3. Bỏ chữ lặp do máy (的的). Giữ tiếng cảm thán (嗯, 啊, 嘎).
4. Chỗ sửa mà chưa chắc: ghi vào "unsure" kèm lý do ngắn bằng tiếng Việt.
5. """ + FILTER_RULES + """
Chỉ trả về JSON: {"fix": {"<số dòng>": "chữ mới"}, "merge": [[<số dòng>, <số dòng>]], "unsure": {"<số dòng>": "lý do"},
"song": {"<số dòng>": "lý do"}, "noise": {"<số dòng>": "lý do"}}. Lý do viết ngắn bằng tiếng Việt.
Dòng không sửa thì KHÔNG ghi. Không có gì thì trả {"fix": {}, "merge": [], "unsure": {}, "song": {}, "noise": {}}."""

FIX_PROMPT = """Bạn là biên tập viên phụ đề tiếng Trung. Các dòng dưới đây do máy nghe (ASR) tạo ra: có thể nghe nhầm chữ
đồng âm / gần âm và chép cả lời bài hát / tiếng động thành chữ. Đọc HẾT các dòng để nắm nội dung, nhân vật, mạch chuyện, rồi:
1. Sửa chữ nghe nhầm theo ngữ cảnh (VD 升↔生, 咬↔摇, 胃袋↔尾带, D神↔地神, 北极熊↔北京雄, 得↔的). CHỈ sửa khi ngữ cảnh rõ
   ràng; không viết lại câu, không thêm ý, không dịch.
2. Bỏ chữ lặp do máy (的的). Giữ tiếng cảm thán (嗯, 啊, 嘎).
3. KHÔNG nối / gộp dòng, KHÔNG chuyển chữ sang dòng bên cạnh – mỗi dòng sửa trong chính dòng đó.
4. Chỗ sửa mà chưa chắc: ghi vào "unsure" kèm lý do ngắn bằng tiếng Việt.
5. """ + FILTER_RULES + """
Chỉ trả về JSON: {"fix": {"<số dòng>": "chữ mới"}, "unsure": {"<số dòng>": "lý do"}, "song": {"<số dòng>": "lý do"},
"noise": {"<số dòng>": "lý do"}}. Lý do viết ngắn bằng tiếng Việt. Dòng không sửa thì KHÔNG ghi. Không có gì thì trả
{"fix": {}, "unsure": {}, "song": {}, "noise": {}}."""

FILTER_PROMPT = """Bạn là biên tập viên phụ đề tiếng Trung. Các dòng dưới đây do máy nghe (ASR) tạo ra. Đọc HẾT các dòng để
nắm nội dung, nhân vật, mạch chuyện, rồi CHỈ làm 1 việc:
""" + FILTER_RULES + """
Không sửa chữ nghe nhầm, không nối câu, không dịch.
Chỉ trả về JSON: {"song": {"<số dòng>": "lý do"}, "noise": {"<số dòng>": "lý do"}, "fix": {"<số dòng>": "câu đã xoá phần
không phải lời thoại"}}. Lý do viết ngắn bằng tiếng Việt. Dòng là lời thoại bình thường thì KHÔNG ghi. Không có gì thì trả
{"song": {}, "noise": {}, "fix": {}}."""

JOIN_PROMPT = """Bạn là biên tập viên phụ đề tiếng Trung. Các dòng dưới đây do máy nghe (ASR) tách câu, mỗi dòng có giờ và
khoảng nghỉ tới dòng sau ("nghỉ 0.4s"; "nói liền" = gần như không ngừng). Máy hay đặt dấu "。" SAI ở chỗ người nói ngừng
lấy hơi hoặc chỗ máy cắt khúc, làm 1 câu bị chặt thành 2 dòng (VD "这笔账。" | "你自己算。"; "三界难。" | "找第二个";
"就连今日的风。" | "都比前些日子轻了几分"; "沈小幽天资。" | "虽高，但若…"). Đọc HẾT các dòng để hiểu mạch chuyện, rồi CHỈ làm
1 việc: tìm các dòng LIỀN NHAU thực ra là MỘT câu / một ý của CÙNG một người và gộp lại.
- Gộp 2–3 dòng liền nhau khi dòng trước chưa nói hết ý: thiếu vế sau, thiếu tân ngữ / bổ ngữ, dòng sau là phần còn lại
  của câu trước. "Nói liền" mà dòng trước dừng giữa ý thì gần như chắc chắn là 1 câu.
- KHÔNG gộp 2 câu đã trọn vẹn; KHÔNG gộp lời của 2 người (hỏi – đáp, đổi người nói – xem nhãn người nói nếu có); không gộp
  lời bài hát với lời thoại; nghỉ trên 2 giây thì chỉ gộp khi chắc chắn.
- Mỗi nhóm gộp: ghi câu sau khi gộp vào "fix" ở dòng ĐẦU nhóm – chỉ được bỏ / đổi dấu câu ở chỗ nối (VD "这笔账你自己算。"),
  KHÔNG đổi, thêm hay bớt chữ nào.
- Chưa chắc thì KHÔNG gộp.
Chỉ trả về JSON: {"merge": [[<số dòng>, <số dòng>]], "fix": {"<số dòng>": "câu sau khi gộp"}}. Không có gì thì trả
{"merge": [], "fix": {}}."""

PROFILE_PROMPT = """Bạn là biên tập viên phim Trung Quốc. Đọc kịch bản phụ đề dưới đây và lập "HỒ SƠ PHIM" ngắn gọn bằng tiếng
Việt cho người dịch sang {lang}. Chỉ trả về JSON:
{{"the_loai": "thể loại + giọng điệu (VD: hoạt hình hài chế meme về AI)",
 "nhan_vat": ["tên gốc -> tên dùng khi dịch – vai trò / là ai", ...],
 "xung_ho": ["A với B: … – … (theo đúng cách 2 người gọi nhau trong kịch bản: 他 / 她 = nam / nữ, 哥 / 姐 = người lớn hơn)", ...],
 "thuat_ngu": {{"tên riêng / meme / từ lóng gốc": "cách dịch", ...}},
 "nghe_nham": ["chữ máy nghe có thể sai -> chữ đúng (nếu thấy)", ...],
 "ghi_chu": "những điều người dịch cần biết để dịch đúng ngữ cảnh (câu đùa, bối cảnh, ẩn ý)"}}"""

REVIEW_PROMPT = """Bạn là biên tập viên kiểm tra bản dịch phụ đề {src} -> {lang} để LỒNG TIẾNG. Mỗi mục (khoá = số thứ tự câu
trong phim) có câu gốc, bản dịch{len_intro}. Mỗi câu
đọc kèm ít nhất 2 câu trước và 2 câu sau nó (trong danh sách hoặc phần NGỮ CẢNH) để biết ai nói với ai, câu nào trả lời
câu nào. Kiểm tra và CHỈ sửa câu có lỗi thật – câu đã đúng nghĩa thì KHÔNG sửa chỉ vì muốn hay hơn; chỉ sửa trong
từng câu, không gộp / chuyển ý sang câu khác:
1. Dịch sai nghĩa, sót ý, không khớp nghĩa với câu trước / sau; TỰ THÊM từ, tiếng cảm thán, từ đệm hay ý không có trong
   câu gốc -> bỏ phần thêm; câu vắt qua 2 dòng đọc không liền mạch.
2. XƯNG HÔ SAI VAI: bản dịch đổi giới tính / vai vế / ngôi so với đại từ, cách gọi ghi trong câu gốc (他 thành cô ta,
   她 thành anh ta, 哥 thành em, 姐 thành anh, câu nói VỀ người khác thành câu nói VỚI người nghe…) -> sửa về đúng câu gốc.
   Câu gốc không ghi rõ đại từ / cách gọi thì xưng hô phải thống nhất với HỒ SƠ PHIM / các câu khác giữa đúng 2 người đó;
   "nguoi_noi" (S1, S2… + nam / nữ tool đo từ giọng, có thể sai; ? = chưa rõ) và nhãn [S1 · nam] ở NGỮ CẢNH cho biết ai
   đang nói với ai (người nghe thường là người nói khác ở câu trước / sau): nữ nói với nam mà bản dịch gọi người nghe là
   "em" (随你 -> "Tùy em") -> sửa ("Tùy anh"). "vai" / ⟨…⟩ = người nói→người nghe mà lúc dịch AI đã xác định (có thể
   sai – soát lại theo cách tự xưng / cách gọi trong câu gốc: 为师, 徒儿, 师尊, 本君, 在下…): xưng hô phải đúng cặp vai đó;
   cùng 1 cặp người giữ 1 cặp xưng hô, cùng 1 người được nhắc tới (ngôi 3) giữ 1 cách gọi trong đoạn ("hắn" câu này,
   "cậu ta" câu sau -> sửa cho thống nhất). Vẫn KHÔNG chắc ai nói với ai, nam hay nữ, ai lớn hơn thì câu đang bỏ đại từ
   / dùng cặp trung tính (cậu – tớ, ngươi – ta) để nguyên – KHÔNG đổi sang anh / em, chị / em khi chưa chắc.
   KHÔNG đổi xưng hô mà câu gốc đã ghi rõ chỉ để khớp hồ sơ phim / thể loại. Tên riêng, thuật ngữ không thống nhất với
   HỒ SƠ PHIM / các câu khác -> sửa.
{len_rule}
4. Còn chữ Hán, sai chính tả, sai dấu câu: dấu phẩy chen giữa 1 cụm từ ("một món, đồ tốt", "Thiên Kiếm, Tông") -> chuyển về
   ranh giới tự nhiên; thiếu dấu câu cuối (câu hỏi thiếu "?", câu kể thiếu ".") -> thêm.
5. Sai giọng điệu (phim hài mà câu đùa không còn buồn cười…).
6. Câu gốc là lời bài hát (văn vần, điệp khúc, không ăn nhập với lời thoại) mà bản dịch chưa có "♪ " ở đầu -> thêm "♪ ".
7. Bản dịch lặp lại 1 vế vì câu gốc NÓI VẤP / nói dở rồi nói lại (我不管闲，我不管闲事) hoặc bộ nghe lặp 1 cụm -> chỉ giữ
   1 lần. Lặp có chủ ý để nhấn mạnh / gọi / giục (让一让，让一让) thì để nguyên.
Chỉ trả về JSON: {{"<số>": {{"sua": "bản dịch mới", "ly_do": "lý do ngắn bằng tiếng Việt"}}}}. Câu đã ổn thì KHÔNG ghi."""


# mục độ dài của REVIEW_PROMPT: có / không "AI rút gọn bản dịch cho vừa thời gian nói" (Cài đặt dub_fit, 2.4.10)
REVIEW_LEN_INTRO = ' và độ dài nói vừa thời gian câu thoại ("≤N" = tối đa N; "M–N" = từ M tới N)'
REVIEW_LEN_RULE = """3. Độ dài: đếm số tiếng (âm tiết; số đếm theo cách đọc). Dài hơn N -> VIẾT LẠI CẢ CÂU thành câu ngắn hơn, giữ ý
   chính, đúng nghĩa câu gốc (đổi cấu trúc câu, từ / thành ngữ ngắn, nói thẳng thay cách nói hình ảnh) – không xoá chữ làm câu cụt / vô nghĩa /
   sai ngữ pháp, không mất phủ định / câu hỏi; không ngắn được mà vẫn trọn nghĩa thì để nguyên.
   Ngắn hơn M -> có thể dùng câu đủ thành phần (chủ ngữ, xưng hô, từ nối) – KHÔNG thêm từ đệm, tiếng cảm thán, ý mới,
   tên / thông tin. Câu chỉ có "≤N": ngắn thì để nguyên. Dư / thiếu 1–2 tiếng thì để nguyên."""
REVIEW_NOLEN_RULE = """3. Độ dài: KHÔNG sửa câu chỉ vì dài / ngắn – không rút gọn (bỏ từ, đổi sang cách nói ngắn hơn) bản dịch đã đúng
   nghĩa, không thêm từ cho dài ra. Câu phải sửa vì lỗi khác thì giữ độ dài như dịch sát câu gốc."""
REVIEW_PROMPT = REVIEW_PROMPT.replace("{len_intro}", REVIEW_LEN_INTRO).replace("{len_rule}", REVIEW_LEN_RULE)


# nội dung mặc định của "Dịch chuẩn" ở các bản trước (sha1 16 ký tự) – bản lưu y nguyên thì coi như chưa sửa
OLD_STD_SHA = {"e556f8bdae999f2d", "4dfe30bbd5ddbd34", "a33ee43fb842248f"}


def _sha(text: str) -> str:
    import hashlib

    return hashlib.sha1((text or "").strip().encode()).hexdigest()[:16]


def all_skills(settings: dict) -> list[dict]:
    """Kỹ năng có sẵn + kỹ năng của bạn (Cài đặt `translate_skills`)."""
    own = [dict(x, builtin=False) for x in (settings.get("translate_skills") or [])
           if isinstance(x, dict) and x.get("id") and isinstance(x.get("text"), str) and x["text"].strip()]
    for x in own:     # 2.3.7: tên hỏng (bản cũ: bấm "Thêm kỹ năng mới" lưu tên = False) -> tên mặc định
        if not isinstance(x.get("name"), str) or not x["name"].strip():
            x["name"] = "Kỹ năng mới"
        x["id"] = str(x["id"])
    std = next((x for x in own if x["id"] == STD_ID), None)   # đã sửa -> bản của bạn
    if std is not None and _sha(std["text"]) in OLD_STD_SHA:
        std = None    # bản lưu y nguyên nội dung mặc định cũ (bấm Lưu mà không sửa) -> dùng bản mặc định mới (2.4.0)
    std = std or dict(STD_SKILL, builtin=False)
    return [std] + BUILTIN + [x for x in own if x["id"] != STD_ID]


def skill_text(settings: dict, sid: str | None = None) -> str:
    sid = settings.get("translate_skill", "") if sid is None else sid
    if not sid:
        return ""
    return next((x["text"].strip() for x in all_skills(settings) if x["id"] == sid), "")


def with_skill(system: str, skill: str) -> str:
    """Gắn kỹ năng riêng vào lời nhắc hệ thống (ưu tiên hơn luật chung, trừ luật định dạng trả về)."""
    if not (skill or "").strip():
        return system
    return (system + "\n\nKỸ NĂNG DỊCH CỦA NGƯỜI DÙNG (làm theo; nếu trái với luật trên thì theo kỹ năng này, NGOẠI TRỪ "
            "luật về định dạng JSON, số dòng và xưng hô theo đúng đại từ / cách gọi của dòng gốc):\n" + skill.strip())


def _json(txt: str) -> dict:
    from .translate import _extract_json

    d = _extract_json(txt)
    return d if isinstance(d, dict) else {}


# ------------------------------------------------------------------ 1. sửa lỗi nghe & nối câu

CLEAN_CHUNK = 80


def clean_lines(llm, segs: list[Segment], profile: str = "", skill: str = "", progress=None, cancel=None,
                mode: str = "full") -> dict:
    """Hỏi AI theo từng phần (80 dòng, kèm 4 dòng sau làm ngữ cảnh). Trả về {"fix", "merge", "unsure", "song", "noise"}
    theo số dòng 1-based của CẢ danh sách. mode "filter": chỉ lọc lời bài hát / chữ không phải thoại; "fix": sửa chữ nghe
    nhầm + lọc, không nối câu; "join": chỉ nối câu theo nghĩa; "full": cả sửa lẫn nối."""
    from .ffmpeg import Cancelled

    prompt = {"filter": FILTER_PROMPT, "join": JOIN_PROMPT, "fix": FIX_PROMPT}.get(mode, CLEAN_PROMPT)
    out = {"fix": {}, "merge": [], "unsure": {}, "song": {}, "noise": {}, "mode": mode}
    n = len(segs)
    for a in range(0, n, CLEAN_CHUNK):
        if cancel and cancel():
            raise Cancelled()
        b = min(n, a + CLEAN_CHUNK)
        if mode == "join":   # kèm người nói + khoảng nghỉ tới dòng sau: AI biết dấu "。" nào đáng ngờ
            lines = {str(i + 1): (f"[{segs[i].start:.2f}–{segs[i].end:.2f}s]"
                                  + (f" ({segs[i].speaker})" if segs[i].speaker else "") + f" {segs[i].text}"
                                  + (_gap_note(segs[i + 1].start - segs[i].end) if i + 1 < n else ""))
                     for i in range(a, b)}
        else:
            lines = {str(i + 1): f"[{segs[i].start:.2f}–{segs[i].end:.2f}s] {segs[i].text}" for i in range(a, b)}
        after = [f"{i + 1}: {segs[i].text}" for i in range(b, min(n, b + 4))]
        parts = []
        if profile:
            parts.append("HỒ SƠ PHIM:\n" + profile)
        parts.append("CÁC DÒNG (số dòng: [giờ] chữ):\n" + "\n".join(f"{k}: {v}" for k, v in lines.items()))
        if after:
            parts.append("NGỮ CẢNH SAU (chỉ tham khảo, không sửa):\n" + "\n".join(after))
        try:
            d = _json(llm.chat(with_skill(prompt, skill), "\n\n".join(parts), max_tokens=6000, temperature=0.1))
        except ValueError:
            d = {}
        for k, v in (d.get("fix") or {}).items():
            if str(k).isdigit() and a < int(k) <= b and isinstance(v, str):
                out["fix"][int(k)] = v.strip()
        for g in d.get("merge") or []:
            try:
                g = sorted({int(x) for x in g})
            except (TypeError, ValueError):
                continue
            if len(g) >= 2 and all(a < x <= b for x in g):
                out["merge"].append(g)
        for key in ("unsure", "song", "noise"):
            for k, v in (d.get(key) or {}).items():
                if str(k).isdigit() and a < int(k) <= b:
                    out[key][int(k)] = str(v)[:200]
        if progress:
            progress(b / max(1, n), {"filter": f"AI đang lọc lời bài hát / chữ không phải thoại {b}/{n}",
                                     "join": f"AI đang nối câu theo nghĩa {b}/{n}",
                                     "fix": f"AI đang sửa lỗi nghe {b}/{n}"}.get(
                                         mode, f"AI đang sửa lỗi nghe & nối câu {b}/{n}"))
    return out


def _gap_note(gap: float) -> str:
    return "  → nói liền" if gap < 0.3 else f"  → nghỉ {gap:.1f}s"


def _words_only(t: str) -> str:
    return re.sub(r"[\W_]", "", t or "")


def _close(old: str, new: str) -> bool:
    """Chữ sửa phải gần chữ cũ (sửa vài chữ / chuyển vài chữ sang dòng bên cạnh), không phải câu khác hẳn."""
    if not new:
        return False
    if old in new or new in old:
        return True
    if len(new) == len(old) <= 6:   # nghe nhầm đồng âm giữ số chữ (mỗi tiếng 1 chữ): 北京雄起 -> 北极熊骑
        return True
    return difflib.SequenceMatcher(None, old, new).ratio() >= 0.45


def _is_trim(old: str, new: str) -> bool:
    """Chỉ XOÁ bớt chữ (đoạn lời bài hát / chữ vô nghĩa dính vào câu thoại): chữ mới là dãy con của chữ cũ, còn ≥ 1 chữ
    có nghĩa, không xoá quá 70 %."""
    new = (new or "").strip()
    if not new or len(new) >= len(old) or len(new) < 0.3 * len(old.strip()):
        return False
    it = iter(old)
    return all(ch in it for ch in new)


MAX_GAP = 2.5      # chỉ gộp 2 dòng cách nhau ≤ 2,5 s (câu bị cắt hay có chỗ nghỉ 1,5–2 s: "一只真" | "的鹈鹕")
MAX_MERGED = 15.0  # câu sau khi gộp ≤ 15 s


def apply_cleanup(segs: list[Segment], res: dict) -> tuple[list[Segment], dict]:
    """Áp dụng kết quả AI (đã kiểm tra an toàn). Trả về (danh sách câu mới, thống kê {"fixed", "merged", "changed"
    (số thứ tự mới của câu đổi), "unsure" (số thứ tự mới), "songs" (số thứ tự mới của câu đánh 🎵), "removed"
    [(giờ, chữ, lý do)] câu không phải lời thoại đã bỏ})."""
    from .align import JOIN_CHARS, JOIN_DUR, _cjk_count, soft_join, split_mates

    n = len(segs)
    trim_only = res.get("mode") == "filter"
    join_only = res.get("mode") == "join"   # AI nối câu: chỉ gộp (2–3 dòng, ≤ 36 chữ / 10 s), chữ giữ nguyên
    fix = {k: v for k, v in (res.get("fix") or {}).items() if 1 <= k <= n}
    noise = {k: v for k, v in (res.get("noise") or {}).items()
             if 1 <= k <= n and not segs[k - 1].extra.get("voice_ok") and segs[k - 1].text.strip()}
    song = {k: v for k, v in (res.get("song") or {}).items()
            if 1 <= k <= n and k not in noise and not segs[k - 1].extra.get("not_song")}
    groups: list[list[int]] = []
    used: set[int] = set()
    fix_only = res.get("mode") == "fix"     # AI sửa lỗi nghe (2.3.9): không gộp dòng
    for g in sorted([] if trim_only or fix_only else (res.get("merge") or []), key=lambda x: x[0]):
        g = [x for x in g if 1 <= x <= n]
        if len(g) < 2 or len(g) > (3 if join_only else 4) or g != list(range(g[0], g[0] + len(g))) or used & set(g):
            continue
        if any(x in noise or x in song for x in g):      # không gộp lời bài hát / chữ rác với lời thoại
            continue
        if any(split_mates(segs[x - 1], segs[x]) for x in g[:-1]):   # vế của câu dịch dài đã tách có chủ ý
            continue
        ss = [segs[x - 1] for x in g]
        if any(ss[k + 1].start - ss[k].end > MAX_GAP for k in range(len(ss) - 1)) or ss[-1].end - ss[0].start > MAX_MERGED:
            continue
        if join_only and (sum(_cjk_count(x.text) for x in ss) > JOIN_CHARS or ss[-1].end - ss[0].start > JOIN_DUR
                          or any(x.extra.get("song") for x in ss)
                          or len({x.speaker for x in ss if x.speaker}) > 1):
            continue
        groups.append(g)
        used |= set(g)
    first = {g[0]: g for g in groups}
    out: list[Segment] = []
    stats = {"fixed": 0, "merged": 0, "changed": [], "unsure": [], "songs": [], "removed": []}
    unsure = res.get("unsure") or {}
    k = 1
    while k <= n:
        s = segs[k - 1]
        if k in noise:
            stats["removed"].append((s.start, s.text, noise[k]))
            k += 1
            continue
        if k in first:
            g = first[k]
            parts = [segs[x - 1] for x in g]
            joined = "".join(p.text for p in parts)
            new_text = fix.get(k, joined)
            if join_only:   # chỉ được đổi dấu câu ở chỗ nối; AI không ghi / viết khác chữ -> tự bỏ "。" giả ở chỗ nối
                if k not in fix or _words_only(new_text) != _words_only(joined):
                    new_text = soft_join(parts)
            elif not _close(joined, new_text):
                new_text = joined
            ex = {kk: v for kk, v in parts[0].extra.items() if kk != "ct"}
            if all(len(p.extra.get("ct") or []) == _cjk_count(p.text) for p in parts) \
                    and _cjk_count(new_text) == sum(_cjk_count(p.text) for p in parts):
                ex["ct"] = [c for p in parts for c in p.extra["ct"]]   # mốc từng chữ nối liền (tách câu theo người nói)
            seg = Segment(parts[0].start, parts[-1].end, new_text, " ".join(p.trans.strip() for p in parts if p.trans.strip()),
                          parts[0].speaker, ex)
            seg.extra["src_before"] = " | ".join(p.text for p in parts)
            stats["merged"] += 1
            stats["changed"].append(len(out))
            if any(x in unsure for x in g):
                seg.extra["src_unsure"] = next(unsure[x] for x in g if x in unsure)
                stats["unsure"].append(len(out))
            out.append(seg)
            k = g[-1] + 1
            continue
        seg = Segment(s.start, s.end, s.text, s.trans, s.speaker, dict(s.extra))
        if join_only:      # AI nối câu không sửa chữ của dòng không gộp
            out.append(s)
            k += 1
            continue
        new = fix.get(k)
        if new and new != s.text and (_is_trim(s.text, new) if trim_only else _close(s.text, new)):
            seg.extra["src_before"] = s.text
            seg.text = new
            stats["fixed"] += 1
            stats["changed"].append(len(out))
        if k in unsure and not trim_only:
            seg.extra["src_unsure"] = unsure[k]
            stats["unsure"].append(len(out))
        if k in song:
            if not seg.extra.get("song"):
                stats["songs"].append(len(out))
            seg.extra["song"] = True                     # 🎵: không lồng tiếng, giữ giọng hát gốc
            seg.extra["ai_song"] = song[k]
        out.append(seg)
        k += 1
    return out, stats


# ------------------------------------------------------------------ 2. hồ sơ phim

def make_profile(llm, texts: list[str], target_name: str = "tiếng Việt", skill: str = "") -> tuple[str, dict]:
    """Đọc kịch bản (tối đa ~24k ký tự đầu) -> (hồ sơ phim dạng chữ, bảng thuật ngữ {gốc: dịch})."""
    script, size = [], 0
    for t in texts:
        if size > 24000:
            break
        script.append(t)
        size += len(t) + 1
    d = _json(llm.chat(with_skill(PROFILE_PROMPT.format(lang=target_name), skill), "KỊCH BẢN:\n" + "\n".join(script),
                       max_tokens=4000, temperature=0.1))
    lines = []
    if d.get("the_loai"):
        lines.append(f"Thể loại / giọng điệu: {d['the_loai']}")
    for key, title in (("nhan_vat", "Nhân vật"), ("xung_ho", "Xưng hô"), ("nghe_nham", "Máy có thể nghe nhầm")):
        items = [str(x) for x in (d.get(key) or []) if str(x).strip()]
        if items:
            lines.append(f"{title}:\n" + "\n".join(f"- {x}" for x in items))
    terms = {str(k).strip(): str(v).strip() for k, v in (d.get("thuat_ngu") or {}).items()
             if str(k).strip() and str(v).strip() and len(str(k)) <= 16}
    if terms:
        lines.append("Tên riêng / thuật ngữ / meme:\n" + "\n".join(f"- {k} = {v}" for k, v in terms.items()))
    if d.get("ghi_chu"):
        lines.append(f"Ghi chú: {d['ghi_chu']}")
    return "\n".join(lines), terms


# ------------------------------------------------------------------ 3. kiểm tra bản dịch

REVIEW_CHUNK = 60
_HAN = re.compile(r"[㐀-鿿]")


REVIEW_CTX = 2     # mỗi câu kiểm tra kèm 2 câu trước / sau (câu không nằm trong danh sách -> phần NGỮ CẢNH)


def review_lines(llm, items: list[tuple[int, str, str, int | str]], profile: str, skill: str, src_name: str,
                 target_name: str, unit: str, lang: str = "vi", progress=None, cancel=None,
                 ctx: list[tuple[str, str]] | None = None, genre: str = "",
                 parallel: bool = False, who: list[str] | None = None,
                 addr: str = "drop", fit: bool = True, roles: list[str] | None = None) -> dict[int, tuple[str, str]]:
    """items: (chỉ số câu, câu gốc, bản dịch, độ dài: số tối đa hoặc chữ "M–N tiếng"). ctx: (câu gốc, bản dịch) của MỌI
    câu trong phim – câu ±2 quanh câu kiểm tra mà không có trong lô được gửi kèm làm ngữ cảnh. genre: form thể loại phim.
    parallel: gửi tất cả các lô cùng lúc ("Gửi tất cả cùng lúc" của công cụ dịch, 2.1.23). who: nhãn người nói + nam / nữ
    MỌI câu trong phim ("S2 · nữ", 2.4.0); addr: cách làm khi không chắc xưng hô; fit False (tắt "AI rút gọn bản dịch cho
    vừa thời gian nói", 2.4.10): không gửi độ dài, AI không rút gọn câu. roles: "vai" (người nói→người nghe) AI dịch đã
    xác định MỌI câu trong phim (2.4.13). Trả về {chỉ số: (bản dịch mới, lý do)}."""
    from .ffmpeg import Cancelled
    from .translate import GENRE_HEAD_VI, address_tail, punct_fix, run_parallel

    out: dict[int, tuple[str, str]] = {}
    sysp = REVIEW_PROMPT.format(src=src_name, lang=target_name)
    if not fit:
        sysp = sysp.replace(REVIEW_LEN_INTRO, "").replace(REVIEW_LEN_RULE, REVIEW_NOLEN_RULE)
    if (genre or "").strip():
        sysp += f"\n\n{GENRE_HEAD_VI}:\n" + genre.strip()

    def one(a: int) -> dict[int, tuple[str, str]]:
        chunk = items[a:a + REVIEW_CHUNK]
        by_no = {i + 1: (i, zh, tr, mx) for i, zh, tr, mx in chunk}   # khoá = số thứ tự câu trong phim
        payload = {str(i + 1): {"goc": zh, "dich": tr,
                                **({"do_dai": mx if isinstance(mx, str) else f"≤{mx} {unit}"} if fit and mx else {}),
                                **({"nguoi_noi": who[i]} if who and i < len(who) else {}),
                                **({"vai": roles[i]} if roles and i < len(roles) and roles[i] else {})}
                   for i, zh, tr, mx in chunk}
        near = []
        if ctx:
            have = {i for i, *_ in chunk}
            near = sorted({j for i in have for j in range(i - REVIEW_CTX, i + REVIEW_CTX + 1)
                           if 0 <= j < len(ctx) and j not in have})
        user = (("HỒ SƠ PHIM:\n" + profile + "\n\n") if profile else "")
        if near:
            user += ("NGỮ CẢNH (câu trước / sau – chỉ để hiểu nghĩa, KHÔNG sửa):\n"
                     + "\n".join(f"{j + 1}: " + (f"[{who[j]}] " if who and j < len(who) else "")
                                 + f"{ctx[j][0]} => {ctx[j][1]}"
                                 + (f" ⟨{roles[j]}⟩" if roles and j < len(roles) and roles[j] else "")
                                 for j in near) + "\n\n")
        user += "KIỂM TRA CÁC CÂU SAU:\n" + json.dumps(payload, ensure_ascii=False, indent=0)
        try:
            d = _json(llm.chat(with_skill(sysp, skill) + address_tail("vi", addr), user, max_tokens=8000,
                               temperature=0.2))
        except ValueError:
            d = {}
        got: dict[int, tuple[str, str]] = {}
        for k, v in d.items():
            if not str(k).isdigit() or int(k) not in by_no or not isinstance(v, dict):
                continue
            i, zh, old, _ = by_no[int(k)]
            new = punct_fix(str(v.get("sua") or "").strip(), zh, lang)       # dấu phẩy chen sai / thiếu dấu câu cuối
            if new and new != old and (lang == "ja" or len(_HAN.findall(new)) < 2):   # tiếng Nhật có chữ Hán
                got[i] = (new, str(v.get("ly_do") or "")[:200])
        return got

    starts = list(range(0, len(items), REVIEW_CHUNK))
    done = 0
    if parallel and len(starts) > 1:
        from . import net

        if progress:
            progress(0.0, f"AI đang kiểm tra {len(items)} câu dịch – gửi {len(starts)} lần cùng lúc")
        c0, report = net.current()
        with net.task(cancel or c0, report):     # nút Huỷ dừng được mọi lần gửi đang chạy
            for a, got in run_parallel(one, starts):
                out.update(got)
                done += len(items[a:a + REVIEW_CHUNK])
                if progress:
                    progress(done / max(1, len(items)), f"AI đang kiểm tra bản dịch {done}/{len(items)} – gửi cùng lúc")
        return out
    for a in starts:
        if cancel and cancel():
            raise Cancelled()
        out.update(one(a))
        done += len(items[a:a + REVIEW_CHUNK])
        if progress:
            progress(min(1.0, done / max(1, len(items))), f"AI đang kiểm tra bản dịch {done}/{len(items)}")
    return out
