"""Tiện ích dùng chung cho giao diện: chạy hàm nền không đơ giao diện, danh sách lựa chọn."""
from __future__ import annotations

import traceback
from typing import Callable

from PySide6.QtCore import QCoreApplication, QEvent, QObject, QRunnable, Qt, QThreadPool, Signal
import shiboken6
from PySide6.QtWidgets import QAbstractScrollArea, QAbstractSlider, QAbstractSpinBox, QComboBox, QScrollBar, QWidget

from .. import genres

ENGINES = [("google", "Google Dịch (miễn phí)"),
           ("hymt", "Hy-MT2 – dịch trên máy, miễn phí, không cần mạng (tải ở tab Tài liệu)"),
           ("deepseek", "DeepSeek AI (giỏi tiếng Trung, rẻ)"),
           ("openai", "OpenAI GPT / API tương thích"), ("anthropic", "Claude (Anthropic)"),
           ("gemini", "Google Gemini"), ("ollama", "Ollama (offline trên máy)")]
STYLES = genres.choices()     # thể loại phim = form dịch theo thể loại (trami/genres.py, sửa được ở hộp "Sửa form")
ASR_ENGINES = [("qwen3", "Qwen3-ASR – nghe chuẩn nhất, căn giờ từng chữ (khuyên dùng, cần GPU)"),
               ("firered", "FireRedASR2 – rất chuẩn tiếng Phổ thông + giọng địa phương, lời hát (tải ở tab Tài liệu)"),
               ("sensevoice", "SenseVoice – nhanh, nhẹ"),
               ("paraformer", "Paraformer – có dấu câu + tự phân người nói"),
               ("whisper", "Whisper large-v3 – đa ngôn ngữ")]
LANGS = [("zh", "Tiếng Trung (Phổ thông)"), ("yue", "Tiếng Quảng Đông"), ("auto", "Tự nhận diện"),
         ("en", "Tiếng Anh"), ("ja", "Tiếng Nhật"), ("ko", "Tiếng Hàn")]
DUAL_HELP = ("Nghe xong bằng bộ chính, bộ còn lại (Qwen3-ASR ↔ FireRedASR2) nghe lại đúng các câu đó rồi so chữ từng "
             "câu: câu 2 bộ nghe khác nhau -> AI hỗ trợ nghe chọn bản đúng theo ngữ cảnh; không chắc -> chữ gốc tô xanh "
             "để bạn nghe lại. Cần tải FireRedASR2 ở tab Tài liệu; lâu hơn ~1,5–2 lần")
CHUNK_HELP = ("Bộ nghe chia phim thành khúc dài tối đa bấy nhiêu giây rồi nghe cả khúc 1 lần (thấy cả câu trước / sau). "
              "Mặc định 28. Dài hơn (40–60): nhiều ngữ cảnh hơn, ít chỗ cắt khúc, nhưng chỗ có nhạc dễ sót câu / lặp "
              "lời và tốn bộ nhớ card hơn. FireRedASR2 tối đa 55")
RECHECK_HELP = ("Bộ nghe tự chấm độ chắc từng chữ. Câu không chắc (chỗ lẫn nhạc, nói nhanh, câu nằm sát mép khúc) được nghe "
                "lại trong khúc mới: câu nằm giữa, thêm vài giây trước / sau làm ngữ cảnh; đã tách giọng thì nghe thêm "
                "trên tiếng gốc. Chỉ nhận bản nghe lại khi chắc hơn, giống bản cũ và không dài thêm (không thêm chữ lạ); "
                "nghe ra khác mà chưa chắc -> chữ gốc tô xanh. Thêm ~10–30 % thời gian nghe")
RELISTEN_HELP = ("Bộ nghe (Qwen3-ASR / FireRedASR2) nghe lại ĐÚNG các câu đã tích rồi tự điền lại chữ gốc: nghe 3 lần – "
                 "khúc rộng hơn (câu nằm giữa, có câu trước / sau làm ngữ cảnh) trên giọng đã tách, cùng khúc đó trên "
                 "tiếng gốc, và riêng câu – lấy bản nhiều lần nghe ra giống nhau / chắc nhất. Chưa đủ chắc -> chữ gốc "
                 "màu xanh (rê chuột xem bản nghe lại, đúng thì 'Dùng bản nghe lại'); không nghe ra lời -> có thể là "
                 "nhạc / tiếng động, xem rồi Xoá. Bản dịch / giọng lồng giữ nguyên – câu đổi chữ được tích sẵn để "
                 "'Dịch câu đã tích'. Ctrl+Z hoàn tác")
DROP_UNSURE_HELP = ("Lớp lọc CUỐI CÙNG của Nghe & tách câu, trước khi dịch: sau mọi bước nghe lại câu không chắc / nghe kỹ / "
                    "nghe 2 bộ, câu nào bộ nghe vẫn chấm độ chắc dưới mức đặt thì XOÁ hẳn (không dịch, không lồng "
                    "tiếng) – thường là nhạc / tiếng ồn / tiếng hét bị chép thành chữ. Không xoá: câu 🎵, câu đã được "
                    "lần nghe khác xác nhận (nghe lại ra y hệt / 2 bộ nghe ra giống nhau), câu chưa có độ chắc (OCR, "
                    "SenseVoice / Whisper). Chạy khi bấm '1. Nghe & tách câu' và 'Bắt đầu dịch tự động' (cả Trang chủ / "
                    "Hàng loạt). Mức độ chắc chung với tab Phụ đề. Lấy lại câu đã xoá: tab Phụ đề → Công cụ → Lời hát & "
                    "nghe nhầm → 'Khôi phục câu đã xoá'")
OVERLAP_HELP = ("Mọi câu (không chỉ câu không chắc) được nghe lần 2 trong khúc lệch với khúc lần đầu (câu nằm giữa khúc); "
                "2 lần ra khác nhau -> lấy bản chắc hơn theo cùng luật như 'Nghe lại câu không chắc'. Chính xác nhất, "
                "CHẬM GẤP ĐÔI – dùng cho phim khó (nhiều nhạc, nói nhanh)")
EDGE_FIX_HELP = ("Câu kết thúc (hoặc bắt đầu) khi nhân vật vẫn còn đang nói – nói chậm, ngừng nhiều lần giữa câu: giọng đã "
                 "tách còn tiếng ngay sau cuối / trước đầu câu (nối tiếp qua chỗ ngừng ≤ 1 s, không thuộc câu nào) -> bộ "
                 "nghe nghe lại khúc câu cũ + khúc đã nới; khúc nới nghe ra thêm chữ của chính câu, không thêm câu khác "
                 "-> nới mép (tối đa 4 s mỗi bên) và đo lại giờ từng chữ. Cần đã tách giọng và Qwen3-ASR / FireRedASR2. "
                 "Cũng chạy khi bấm 'Căn mép theo giọng' (tab Phụ đề)")
GEMINI_REVIEW_HELP = ("Gemini XEM + NGHE từng khúc video ~5 phút cùng phụ đề tiếng Trung máy nghe ra: câu nghe nhầm / sai tên "
                      "riêng (đọc được tên hiện trên màn hình) -> sửa khi chữ mới gần chữ cũ, khác hẳn thì tô xanh để bạn "
                      "chọn ('Dùng bản bộ nghe 2'); câu không phải lời nói -> tím, lời bài hát -> 🎵 (không tự xoá); câu "
                      "bị sót -> thêm (chữ cam, giờ ước lượng – có Qwen3 thì đo lại giờ từng chữ); câu Gemini xác nhận "
                      "đúng không bị bước 'Xoá câu không chắc' xoá. Mốc giờ vẫn của tool. Cần API key Gemini (Cài đặt "
                      "→ Công cụ dịch → Gemini); video được tải lên Google rồi xoá ngay sau khi xem. Phim 1 giờ ≈ 12 lần "
                      "gửi")
VIDEO_TRANSLATE_HELP = ("Chỉ khi Công cụ dịch = Gemini: mỗi lô câu gửi dịch kèm khúc video của chính các câu đó (480p, 1 khung"
                        " / giây) -> Gemini thấy ai nói với ai (nam / nữ, già / trẻ, vai vế) nên chọn xưng hô đúng hơn, "
                        "hiểu câu ngắn theo hành động trên hình, đọc chữ trên màn hình; ghi lại giới tính từng người nói "
                        "(Tự gán giọng nam / nữ dùng). Luật dịch / form thể loại / kỹ năng giữ nguyên. Lô gửi video lỗi -> "
                        "dịch chỉ bằng chữ như thường. Chậm hơn dịch thường (tải video lên), tốn thêm token video")
VIDEO_SPEECH_HELP = ("2.4.11 – đi kèm 'Dịch có nhìn hình' (Gemini): mỗi lô chỉ gửi CÁC ĐOẠN NHÂN VẬT NÓI (giờ từng câu ± "
                     "0,3 s, 2 câu cách ≤ 1 s giữ cả khoảng giữa) ghép thành 1 khúc ngắn, bỏ đoạn nhạc / cảnh không lời "
                     "-> bớt token video (phim nhiều nhạc / cảnh không lời bớt nhiều), tải lên nhanh hơn, mỗi lô được nhiều "
                     "câu hơn. Giờ từng câu trong khúc do tool tính nên không lệch. Đoạn có lời đã chiếm ≥ 80 % khúc -> gửi "
                     "khúc liền như cũ. Khúc đã cắt lưu ở work/gemvideo, dịch lại dùng lại. Tắt = gửi khúc liền như 2.1.25")
FRAMES_HELP = ("2.4.11 – mỗi câu gửi dịch kèm 1 ảnh khung hình chụp lúc nhân vật đang nói (ảnh nhỏ 448 px) cho AI xem được "
               "ảnh: Gemini, OpenAI, Claude, Ollama (mô hình xem ảnh, vd qwen2.5-vl) – không có với DeepSeek / Google / "
               "Hy-MT2. AI thấy ai đang nói, nam / nữ, nói với ai, bối cảnh -> chọn xưng hô đúng hơn cho câu không ghi rõ "
               "đại từ (câu gốc ghi rõ 他 / 她, 哥 / 姐… vẫn theo câu gốc). Ghi lại giới tính người nói AI thấy (Tự gán "
               "giọng nam / nữ dùng). Tốn thêm token ảnh (~100–250 token / câu tuỳ AI), mỗi lần gửi ≤ 40 câu. Ảnh lưu ở "
               "work/aiframes, dịch lại dùng lại. Gemini đang bật 'Dịch có nhìn hình' thì ảnh chỉ dùng khi lô gửi video "
               "lỗi. Model không nhận ảnh -> tự thôi gửi ảnh, dịch bằng chữ")
NAME_FIX_HELP = ("Chỉ khi dịch sang tiếng Việt, mọi công cụ dịch (Google, Hy-MT2, AI): TÊN RIÊNG máy dịch để nguyên pinyin "
                 "(chữ viết hoa: \"Lin Fan\", \"Tianjian Zong\", \"Changsha\") -> đổi theo chữ Hán của câu gốc (\"Lâm "
                 "Phàm\", \"Thiên Kiếm Tông\", \"Trường Sa\"). Ô bên cạnh chọn loại: cả tên người lẫn địa danh, môn "
                 "phái, đồ vật / chỉ tên người / chỉ địa danh, môn phái, đồ vật (tool đoán loại theo chữ Hán: bắt đầu bằng "
                 "họ = tên người; kết thúc bằng 宗 门 派 山 城 州 剑… = địa danh, môn phái, đồ vật). Từ viết thường "
                 "(\"jianghu\") là công tắc riêng. Không tốn API. Tên trong Bảng thuật ngữ dùng đúng bản trong bảng. "
                 "Tắt = dịch như cũ. Dự án đã dịch: tab Phụ đề → Công cụ → Sửa câu → Sửa tên pinyin (xem trước, chọn "
                 "từng tên)")
TERM_FIX_HELP = ("Chỉ khi dịch sang tiếng Việt: từ VIẾT THƯỜNG máy dịch để nguyên pinyin (\"jianghu\", \"dantian\", "
                 "\"xiuxian\") -> âm Hán Việt viết thường theo chữ Hán của câu gốc (\"giang hồ\", \"đan điền\", \"tu "
                 "tiên\"). Từ có thể là tiếng Việt không dấu (\"ta lai\", \"hai\") không bao giờ bị đổi. Tắt = để nguyên "
                 "(mặc định – chỉ tên riêng mới đổi khi bật công tắc Tên riêng)")
SLANG_HELP = ("Chỉ khi dịch sang tiếng Việt: câu gốc có từ lóng / tiếng mạng (躺平, 内卷, 吃瓜群众, yyds, 社恐…) -> gửi kèm "
              "nghĩa quen dùng cho AI (AI tự xét câu dùng nghĩa lóng hay nghĩa đen); Google / Hy-MT2 thay trước các từ "
              "chắc chắn là lóng. Tắt = dịch như cũ. Từ riêng của bạn thêm vào Bảng thuật ngữ")
LANG_CHECK_HELP = ("Khúc bộ nghe (Qwen3-ASR) không chắc -> nghe lại không ép tiếng Trung xem có phải câu tiếng Anh / Nhật… "
                   "không; ra tiếng khác mà chắc hơn thì dùng bản đó. Tắt nếu phim chỉ có tiếng Trung mà thấy câu tiếng "
                   "Anh / Nhật lạ ở đoạn nhạc (bộ nghe đoán lời bài hát thành tiếng nước ngoài)")
METHODS = [("asr", "Nhận dạng giọng nói"), ("ocr", "OCR phụ đề cứng có sẵn trên video"),
           ("hybrid", "OCR + nghe – chữ từ phụ đề in sẵn, giờ từ giọng nói (chuẩn nhất khi có phụ đề Trung)")]
SPLIT_STYLES = [("dub", "Câu trọn ý – hợp lồng tiếng (khuyên dùng)"), ("short", "Câu ngắn – hợp phụ đề đọc nhanh")]
SPLIT_HELP = ("Câu trọn ý: tin dấu câu của bộ nghe, chỉ cắt ở chỗ nghỉ rất dài (≥ 1,6 s) hoặc dấu phẩy + nghỉ ≥ 0,7 s, "
              "tự nối dòng chưa hết câu → dịch và lồng tiếng liền mạch. Câu ngắn: cắt ở mọi chỗ nghỉ ≥ 0,8 s / dấu phẩy + "
              "nghỉ ≥ 0,45 s (cách tách cũ). Cả 2 kiểu đều không cắt giữa 1 từ và tự nối câu bị cắt ngang")
OCR_SPEEDS = [("2", "Nhanh – kiểm tra 2 lần/giây (câu hiện lâu)"), ("4", "Chuẩn – 4 lần/giây (khuyên dùng)"),
              ("8", "Kỹ – 8 lần/giây (câu hiện rất nhanh, dưới 0,3 giây)")]
OCR_FPS_HELP = ("Khi lấy phụ đề bằng OCR: số lần mỗi giây tool kiểm tra chữ có đổi không. Giờ hiện / tắt câu luôn tìm "
                "đúng tới từng khung hình (chia đôi các khung ở giữa), nên mức này chỉ ảnh hưởng câu hiện cực ngắn: câu "
                "ngắn hơn 1 lần kiểm tra có thể bị sót")
OCR_BOX_HELP = ("Khung chữ nhật tool sẽ quét để đọc phụ đề. Dừng video ở chỗ đang có phụ đề rồi kéo chuột trên video bên "
                "phải (kéo giữa khung để dời, 4 cạnh / 4 góc để đổi cỡ), hoặc bấm \"Chụp khung & khoanh\" để khoanh trên "
                "ảnh lớn. Chừa chỗ cho câu dài nhất và câu 2 dòng. Khung càng sát chữ, quét càng nhanh và ít đọc nhầm "
                "chữ trong cảnh. Để trống: tool tự dò vùng phụ đề")
OCR_MODEL_HELP = ("PP-OCRv6 có sẵn (không phải tải) đủ tốt cho phần lớn video. PP-OCRv6 medium / PP-OCRv5 server đọc kỹ "
                  "hơn với chữ nhỏ / chữ kiểu nhưng chậm hơn – tải ở tab Tài liệu; nên có card (Cài đặt → OCR chạy trên). "
                  "PP-OCRv4 = bộ đọc cũ")
WORD_FILTER_HELP = ("Xoá đúng cụm chữ này ở mọi câu phụ đề – logo / watermark bị đọc dính vào câu (\"@纵横排队一个个的进来\" "
                    "-> \"排队一个个的进来\"); câu chỉ có cụm đó thì xoá cả câu. Giờ các câu không đổi. Bấm \"Lọc\" trước để "
                    "bảng chỉ hiện các câu có cụm đó (xem trước). Không phân biệt chữ hoa / thường, ＠ / @, khoảng trắng giữa "
                    "các chữ. Nhiều cụm cách nhau bằng dấu phẩy. OCR đọc logo mỗi lần 1 kiểu (@纵横 / @凯横 / @黄AGC): "
                    "@* = @ và tối đa 8 chữ sau nó, tới hết câu / dấu câu / khoảng trắng (logo dính cuối câu); @?? = @ "
                    "và đúng 2 chữ sau nó (logo dính đầu câu). Ctrl+Z hoàn tác")
OCR_EQ_HELP = ("Chỉnh ảnh trước khi bộ đọc chữ quét (độ sáng / tương phản / gamma như tab Chỉnh sửa → Màu sắc, + làm nét): "
               "chọn Mẫu là áp ngay, video bên phải hiện đúng ảnh bộ đọc chữ sẽ quét (ảnh xám – bão hoà màu không có tác "
               "dụng nên không có ô này). Phụ đề chữ trắng viền đen trên phim tối -> mẫu 'Chữ trắng viền đen – nền tối' "
               "(sáng -0,30 · tương phản 1,80 · gamma 0,50): nền tối hẳn, chữ trắng nổi rõ -> đọc chữ đúng hơn, tách câu "
               "đúng giờ hơn. CHỈ ảnh đưa vào OCR – video xuất không đổi. 'Về gốc' = bỏ hết chỉnh. 'Xem thử' = ảnh vùng "
               "quét trước / sau khi làm rõ")
OCR_UNSURE_HELP = ("Bộ đọc chữ chấm độ chắc từng câu (0–1; chữ rõ thường ≥ 0,9). Câu đọc chắc dưới mức này: chữ gốc tô đỏ "
                   "cam ở tab Phụ đề (rê chuột xem độ chắc), 'Tích câu không chắc' tích được. Mức cao hơn = nhiều câu hơn")
OCR_LISTEN_HELP = ("<b>Kiểm tra câu OCR không đạt</b> (2.1.42 nghe lại, 2.1.44 thêm AI đọc lại ảnh). Câu KHÔNG ĐẠT = OCR "
                   "đọc chắc dưới mức 'đọc chắc dưới', hoặc (bật 'câu không có tiếng nói') cả lúc câu hiện không có tiếng "
                   "nói nào trên giọng đã tách. Lúc lấy phụ đề (OCR / OCR + nghe / Bắt đầu dịch tự động):<br>"
                   "1. Bộ nghe (Qwen3-ASR / FireRedASR2) nghe đúng khúc giờ câu đó – ra đúng chữ OCR, đủ chắc -> câu đạt, xong.<br>"
                   "2. Câu còn lại: AI xem ảnh (Gemini / OpenAI / Claude / Ollama có mô hình xem ảnh) đọc lại ảnh dải phụ "
                   "đề của câu -> tool chọn theo <b>2 trên 3</b>: chữ OCR, chữ AI, lời nghe được (khớp theo chữ hoặc âm đọc "
                   "từ mức 'khớp từ'):<br>"
                   "• AI khớp lời nghe -> dùng chữ AI (chữ gốc xanh ngọc, rê chuột xem chữ OCR cũ);<br>"
                   "• AI = OCR mà không nghe thấy tiếng nói -> giữ, đánh dấu tím (lời dẫn / chữ trên hình?);<br>"
                   "• AI không thấy phụ đề + không có tiếng nói -> XOÁ câu;<br>"
                   "• 3 bản đều khác -> giữ chữ OCR, chữ xanh lơ (bấm 'Dùng bản bộ nghe 2' để lấy chữ AI).<br>"
                   "Không bao giờ xoá chỉ vì 1 nguồn. Giờ câu luôn giữ theo OCR. Câu xoá lấy lại ở tab Phụ đề → Công cụ → "
                   "Lời hát & nghe nhầm → 'Khôi phục câu đã xoá'. Không có AI xem ảnh -> cách 2.1.42: nghe chắc + đúng chữ "
                   "giữ, chắc + câu khác điền chữ nghe được, không ra chữ / không chắc thì xoá.")
OCR_COLOR_HELP = ("<b>Màu chữ phụ đề</b> (2.1.44): chỉ lấy chữ ĐÚNG màu chữ (+ màu viền nếu có) – chữ khác màu trong khung "
                  "quét (biển hiệu, chữ trong cảnh, HUD game, chữ chạy) bị bỏ ngay lúc quét nên không dính vào câu, không "
                  "làm tách câu sai.<br>"
                  "• <b>Tự lấy màu</b>: tool chụp ~36 khung rải khắp video, đọc chữ trong khung quét, nhóm theo màu – nhóm "
                  "có nhiều câu khác nhau nhất là phụ đề.<br>"
                  "• <b>Hút</b>: dừng video ở chỗ có phụ đề, bấm Hút rồi bấm vào nét chữ (hoặc viền) trên ảnh.<br>"
                  "• Phụ đề không viền / viền đổi màu theo nền -> chọn 'Không xét viền'. Phim có 2–3 kiểu phụ đề (lời "
                  "thoại trắng, lời dẫn vàng) -> '+ Thêm màu' (tối đa 3).<br>"
                  "• <b>Độ lệch</b>: Chặt = chỉ đúng màu (video nét), Vừa (mặc định), Rộng = video mờ / nén nhiều.<br>"
                  "• <b>Xem thử</b>: ảnh vùng quét tại chỗ đang dừng – xanh lá = nét được lấy, đỏ = nét bị bỏ.<br>"
                  "Bật lọc màu thì 'Làm rõ chữ khi quét' không dùng (lọc màu thay nó).")
OCR_CENTER_HELP = ("Khung quét có cả phụ đề (giữa) và chữ khác cùng hàng ở xa 2 bên (biển hiệu, logo góc): khối chữ cách "
                   "cụm chữ giữa khung quá 1,5 lần cỡ chữ bị tách ra và bỏ, chỉ giữ cụm có tâm khung. Phụ đề lệch hẳn 1 "
                   "bên (không qua giữa khung) thì không áp.")
OCR_TIMINGS = [("sub", "Theo giờ phụ đề trên hình"), ("mix", "Kết hợp: câu theo phụ đề + chỗ ngừng theo giọng nói")]
OCR_TIMING_HELP = ("Giờ câu khi lấy phụ đề bằng OCR. Theo giờ phụ đề: đúng lúc chữ hiện / tắt trên hình (nhanh, không cần "
                   "tách giọng). Kết hợp: chữ và cách chia câu giữ đúng phụ đề, nhưng tool tách giọng rồi đo từng chữ "
                   "trên giọng nói – đầu / cuối câu đúng lúc nhân vật bắt đầu / thôi nói (lệch tối đa 0,6 s so với giờ "
                   "hiện chữ), chỗ nhân vật ngừng giữa câu (\"说好要卖的……怎么又不卖了\") được thêm dấu phẩy để bản dịch "
                   "giữ 2 vế và giọng lồng nghỉ đúng chỗ. Chậm hơn (tách giọng + đo mốc chữ bằng Qwen3)")
HARDSUB = [("none", "Giữ nguyên"), ("inpaint", "Xoá sạch bằng AI (LaMa)"), ("blur", "Làm mờ vùng phụ đề"),
           ("box", "Che bằng dải màu"), ("delogo", "Delogo (nhanh)"),
           ("patch", "Vá nền (chép nền sát bên, nhanh)"), ("stretch", "Kéo dọc (kéo dòng sát mép phủ kín chữ)")]
WHICH = [("trans", "Chỉ tiếng Việt"), ("both", "Song ngữ Việt + Trung"), ("source", "Chỉ tiếng Trung"),
         ("none", "Không ghép (chỉ xuất SRT)")]
TTS_ENGINES = [("edge", "Microsoft Edge (giọng Việt / Anh / Thái / Nhật / Hàn, cần mạng)"),
               ("vieneu", "VieNeu-TTS (25 giọng + 6 giọng thu âm sạch + nhái giọng, offline – chỉ tiếng Việt)"),
               ("omnivoice", "OmniVoice (23 giọng chọn sẵn + nhái giọng từ file mẫu, offline, đọc được nhiều tiếng)"),
               ("voxcpm", "VoxCPM2 (📣 giọng thuyết minh + giọng tạo theo mô tả + nhái giọng, offline, card ≥ 8 GB)"),
               ("korva", "KorvaTTS (10 giọng Việt, chạy CPU – không chiếm card, offline, đọc được tiếng Anh xen kẽ)")]
TARGETS = [("vi", "Tiếng Việt"), ("en", "Tiếng Anh (English)"), ("th", "Tiếng Thái (ภาษาไทย)"),
           ("ja", "Tiếng Nhật (日本語)"), ("ko", "Tiếng Hàn (한국어)")]
OCR_READ_HELP = ("<b>Đọc chữ kỹ</b> (2.1.37)<br>"
                 "• <b>Sửa chữ dễ nhầm theo từ điển</b>: chữ bộ đọc chưa chắc mà đứng lẻ (không thành từ) được thử thay bằng "
                 "chữ giống hình (己/已, 未/末, 土/士, 人/入…) – chỉ đổi khi ghép thành từ có trong từ điển (\"己经\" → "
                 "\"已经\"). Tên riêng ở Bảng tên riêng / hồ sơ phim không bị đổi. Rê chuột chữ gốc để xem chữ đã sửa.<br>"
                 "• <b>Đọc lại câu kém chắc bằng bộ đọc lớn</b>: quét cả phim bằng bộ đọc nhỏ (nhanh), câu nào đọc kém chắc "
                 "thì cuối lần quét bộ đọc lớn đọc lại đúng ảnh chữ đó – gần chính xác như bộ lớn mà chỉ chậm thêm chút. "
                 "Cần tải PP-OCRv5 server (hoặc PP-OCRv6 medium) ở tab Tài liệu.<br>"
                 "Chữ bộ đọc không chắc được tô đỏ trong cột chữ gốc (từng chữ).")

# 2.4.10: rút gọn bản dịch – mặc định TẮT (giữ bản dịch sát nghĩa), bật mới rút gọn
FIT_HELP = ("Tắt (mặc định): bản dịch là bản dịch sát nghĩa gốc – AI KHÔNG rút gọn ở bước dịch lẫn bước 'AI kiểm tra lại "
            "bản dịch'. Bật: mỗi câu gửi kèm số tiếng tối đa nói vừa thời gian nhân vật nói, AI chọn cách nói ngắn hơn "
            "(vẫn đủ ý); 'AI kiểm tra lại bản dịch' rút gọn câu dài hơn mức đó. Chỉ với công cụ dịch AI. Câu đã dịch "
            "trước đó giữ nguyên – muốn đổi thì 'Dịch lại tất cả' / 'Dịch câu đã tích'")
AUTOSHORT_HELP = ("Tắt (mặc định): lồng tiếng đọc đúng bản dịch đang có, không đổi lời. Bật: khi đọc giọng (Tạo bản lồng "
                  "tiếng / 3. Lồng tiếng / Tạo lại giọng câu đã tích) câu vẫn dài hơn chỗ trống dù đã tăng tốc tối đa "
                  "-> AI viết lại ngắn hơn rồi đọc lại câu đó (cần công cụ dịch AI; câu bạn tự sửa lời không bị đổi). "
                  "Cùng 1 công tắc với tab Lồng tiếng mục 3")


def which_choices(lang: str = "vi") -> list[tuple[str, str]]:
    """Lựa chọn phụ đề ghép vào video theo tiếng đích ("Chỉ tiếng Anh", "Song ngữ Anh + Trung"…)."""
    from ..langs import INFO, norm

    short = INFO[norm(lang)]["short"]
    return [("trans", f"Chỉ tiếng {short}"), ("both", f"Song ngữ {short} + Trung"), ("source", "Chỉ tiếng Trung"),
            ("none", "Không ghép (chỉ xuất SRT)")]


def relabel(cb: QComboBox, choices: list[tuple[str, str]]):
    """Đổi chữ các mục của ô chọn (giữ mục đang chọn) – dùng khi đổi tiếng đích."""
    for i in range(cb.count()):
        for k, name in choices:
            if cb.itemData(i) == k:
                cb.setItemText(i, name)
ASPECTS = [("original", "Giữ nguyên"), ("9:16", "9:16 dọc – TikTok / Reels / Shorts"),
           ("16:9", "16:9 ngang – YouTube / Facebook"), ("1:1", "1:1 vuông"), ("4:5", "4:5 dọc – Facebook / Instagram"),
           ("3:4", "3:4 dọc"), ("2:3", "2:3 dọc – Pinterest"), ("3:5", "3:5 dọc"), ("5:7", "5:7 dọc"),
           ("4:3", "4:3 ngang"), ("3:2", "3:2 ngang"), ("5:3", "5:3 ngang"), ("5:4", "5:4 ngang"), ("7:5", "7:5 ngang")]
ASPECT_MODES = [("blur", "Nền mờ (từ chính hình phim)"), ("pad", "Viền đen"), ("color", "Nền 1 màu"),
                ("crop", "Phóng cho đầy khung (cắt bớt)")]
FILM_POS = [("c", "Giữa"), ("up", "Lệch lên – chừa chỗ chữ ở dưới"), ("down", "Lệch xuống – chừa chỗ tiêu đề ở trên"),
            ("left", "Lệch trái"), ("right", "Lệch phải"), ("custom", "Tự chỉnh")]
RESOLUTIONS = [("original", "Giữ nguyên"), ("480p", "480p"), ("720p", "720p HD"), ("1080p", "1080p Full HD"),
               ("1440p", "2K"), ("2160p", "4K")]
POSITIONS = [("top-right", "Trên phải"), ("top-left", "Trên trái"), ("top", "Trên giữa"),
             ("bottom-right", "Dưới phải"), ("bottom-left", "Dưới trái"), ("center", "Giữa")]


class _Signals(QObject):
    done = Signal(object)
    error = Signal(str)


class _Task(QRunnable):
    def __init__(self, fn, sig):
        super().__init__()
        self.fn, self.sig = fn, sig

    def run(self):
        try:
            res = self.fn()
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            from ..config import err_text, log_error

            log_error(getattr(self.fn, "__qualname__", ""))
            try:
                self.sig.error.emit(err_text(e))
            except RuntimeError:  # cửa sổ đã đóng trong lúc việc nền đang chạy
                pass
            return
        try:
            self.sig.done.emit(res)
        except RuntimeError:  # cửa sổ đã đóng trong lúc việc nền đang chạy
            pass


_keep: set = set()


def run_async(fn: Callable, on_done: Callable | None = None, on_error: Callable | None = None):
    """Chạy fn() ở luồng nền, gọi on_done(kết quả) / on_error(lỗi) trên luồng giao diện."""
    sig = _Signals()
    _keep.add(sig)

    def finish_ok(r):
        _keep.discard(sig)
        if on_done:
            on_done(r)

    def finish_err(m):
        _keep.discard(sig)
        if on_error:
            on_error(m)

    sig.done.connect(finish_ok)
    sig.error.connect(finish_err)
    QThreadPool.globalInstance().start(_Task(fn, sig))


_WHEEL_TYPES = (QComboBox, QAbstractSpinBox, QAbstractSlider)


class WheelGuard(QObject):
    """Lăn chuột lướt qua ô chọn / ô số / thanh trượt thì KHÔNG đổi giá trị mà cuộn trang.
    - Ô chọn (combobox): lăn chuột không bao giờ đổi lựa chọn – chỉ đổi khi bấm chọn trong danh sách.
    - Ô số / thanh trượt: chỉ nhận lăn chuột ngay sau khi vừa bấm vào chính nó; bấm chỗ khác là thôi."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.armed = None   # ô số / thanh trượt vừa được bấm vào

    @staticmethod
    def _owner(obj):
        """Ô điều khiển chứa obj (vd. ô gõ số bên trong ô số)."""
        w = obj
        while w is not None and hasattr(w, "parentWidget"):
            if isinstance(w, _WHEEL_TYPES) and not isinstance(w, QScrollBar):
                return w
            w = w.parentWidget()
        return None

    def eventFilter(self, obj, ev):
        try:
            return self._filter(obj, ev)
        except Exception:  # noqa: BLE001 – bộ lọc toàn cục tuyệt đối không được làm sập giao diện
            self.armed = None
            return False

    def _filter(self, obj, ev):
        t = ev.type()
        if t == QEvent.Type.MouseButtonPress:
            if self.armed is not None and not shiboken6.isValid(self.armed):
                self.armed = None   # ô cũ đã bị xoá khỏi giao diện
            own = self._owner(obj) if hasattr(obj, "parentWidget") else None
            if own is not None:
                self.armed = None if isinstance(own, QComboBox) else own
            elif self.armed is not None and isinstance(obj, QWidget) and obj.isAncestorOf(self.armed):
                pass   # cú bấm vừa rồi được chuyển tiếp lên khung chứa ô – vẫn giữ
            else:
                self.armed = None
            return False
        if t == QEvent.Type.Wheel and isinstance(obj, _WHEEL_TYPES) and not isinstance(obj, QScrollBar):
            if obj.focusPolicy() == Qt.FocusPolicy.WheelFocus:
                obj.setFocusPolicy(Qt.FocusPolicy.StrongFocus)  # lăn chuột không tự chọn ô
            allowed = (not isinstance(obj, QComboBox)) and obj is self.armed and obj.hasFocus()
            if not allowed:
                p = obj.parentWidget()
                while p is not None and not isinstance(p, QAbstractScrollArea):
                    p = p.parentWidget()
                if p is not None:  # chuyển cho khung cuộn phía ngoài để trang vẫn cuộn
                    QCoreApplication.sendEvent(p.viewport(), ev)
                return True
        return False


def install_wheel_guard(app):
    guard = WheelGuard(app)
    app.installEventFilter(guard)
    return guard
