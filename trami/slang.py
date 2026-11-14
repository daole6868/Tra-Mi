"""Từ lóng / tiếng mạng / cách nói hay bị dịch từng chữ (2.4.2) – bảng tự soạn cho dịch Trung -> Việt.

Máy dịch hay dịch từng chữ: 躺平 -> "nằm thẳng", 吃瓜群众 -> "quần chúng ăn dưa", yyds -> "yyds". Bảng này gửi nghĩa
quen dùng cho mọi công cụ dịch (chỉ khi dịch sang tiếng Việt, Cài đặt `trans_slang`):
- AI: mục "TỪ LÓNG / CÁCH NÓI MẠNG" trong lời nhắc – chỉ các từ có trong lô đang dịch, AI tự xét câu có dùng theo
  nghĩa lóng không (吃瓜 có thể là ăn dưa thật);
- Google / Hy-MT2: chỉ các từ CHẮC CHẮN là lóng (`strict`) được thay / gửi làm thuật ngữ trước khi dịch.
Bảng thuật ngữ của bạn luôn được ưu tiên (từ có trong bảng thuật ngữ không lấy nghĩa ở đây).
"""
from __future__ import annotations

import re
from functools import lru_cache

# (từ gốc, nghĩa tiếng Việt, chắc chắn là lóng) – "chắc chắn" = hầu như không bao giờ dùng theo nghĩa đen
SLANG: list[tuple[str, str, bool]] = [
    # khen / chê / cảm thán
    ("yyds", "đỉnh nhất mọi thời đại", True), ("永远的神", "đỉnh nhất mọi thời đại", True),
    ("绝绝子", "đỉnh của chóp", True), ("牛逼", "đỉnh thật", True), ("牛B", "đỉnh thật", True),
    ("牛批", "đỉnh thật", True), ("666", "đỉnh quá", False), ("六六六", "đỉnh quá", False),
    ("硬核", "chất thật sự", True), ("离谱", "vô lý hết sức", True), ("无语", "cạn lời", True),
    ("醉了", "bó tay", False), ("服了", "chịu thật", False), ("绝了", "tuyệt thật", False),
    ("卧槽", "Vãi thật", True), ("我靠", "Vãi", True), ("我去", "Trời đất", False), ("我的天", "Trời ơi", True),
    ("天哪", "Trời ơi", True), ("哎呀妈呀", "Ôi trời ơi", True), ("尼玛", "mẹ nó", True), ("他妈的", "mẹ kiếp", True),
    ("傻逼", "đồ ngu", True), ("装逼", "làm màu", True), ("笑死", "cười chết mất", True),
    ("笑不活了", "cười chết mất", True), ("xswl", "cười chết mất", True), ("蚌埠住了", "không nhịn nổi", True),
    ("绷不住了", "không nhịn nổi nữa", True), ("栓Q", "cảm ơn (mỉa mai)", True), ("芭比Q", "toang rồi", True),
    ("凉了", "toang rồi", False), ("emo了", "buồn rầu", True), ("破防", "suy sụp tinh thần", False),
    ("破大防", "suy sụp hoàn toàn", True), ("真香", "thơm thật (tự vả mình)", False), ("上头", "cuốn quá", False),
    ("嘤嘤嘤", "hu hu", True), ("么么哒", "chụt chụt", True), ("萌萌哒", "đáng yêu quá", True),
    ("有木有", "có không", True), ("酱紫", "như vậy", True), ("肿么了", "sao vậy", True), ("awsl", "đáng yêu chết mất", True),
    ("u1s1", "nói thật", True), ("有一说一", "nói thật lòng", True), ("dbq", "xin lỗi", True),
    ("nsdd", "bạn nói đúng", True),
    ("srds", "tuy là vậy nhưng mà", True), ("djll", "ai hiểu thì hiểu", True), ("懂的都懂", "ai hiểu thì hiểu", True),
    ("ssfd", "run lẩy bẩy", True), ("蓝瘦香菇", "buồn muốn khóc", True), ("雨女无瓜", "liên quan gì đến cô", True),
    ("爷青回", "thanh xuân trở lại", True), ("爷青结", "thanh xuân kết thúc rồi", True),
    # con người / tính cách
    ("打工人", "dân làm thuê", True), ("社畜", "nô lệ công sở", True), ("社恐", "sợ giao tiếp", True),
    ("社牛", "dạn dĩ không biết ngại", True), ("社死", "xấu hổ muốn độn thổ", True),
    ("佛系", "sống kiểu an nhiên mặc kệ", True),
    ("杠精", "kẻ thích cãi cố", True), ("键盘侠", "anh hùng bàn phím", True), ("网红", "người nổi tiếng trên mạng", True),
    ("舔狗", "kẻ lụy tình nịnh bợ", True), ("渣男", "trai tồi", True), ("渣女", "gái tồi", True),
    ("绿茶婊", "trà xanh", True), ("白莲花", "bạch liên hoa", False), ("海王", "kẻ bắt cá nhiều tay", False),
    ("备胎", "lốp dự phòng", True), ("单身狗", "hội FA", True), ("柠檬精", "kẻ hay ghen tị", True),
    ("老铁", "anh em chí cốt", True), ("家人们", "cả nhà ơi", True), ("宝子们", "các bạn ơi", True), ("宝子", "bạn ơi", True),
    ("集美", "chị em", True), ("小鲜肉", "trai trẻ đẹp", True), ("老司机", "tay lão luyện", False),
    ("高富帅", "cao phú soái", True), ("白富美", "bạch phú mỹ", True), ("土豪", "đại gia", True), ("学霸", "học bá", True),
    ("学渣", "học dốt", True), ("大佬", "đại lão", False), ("大神", "cao thủ", True), ("菜鸡", "gà mờ", True),
    ("菜鸟", "gà mờ", True), ("萌新", "người mới", True), ("呆萌", "ngố đáng yêu", True), ("傲娇", "kiêu kỳ", True),
    ("腹黑", "phúc hắc", True), ("中二病", "bệnh ảo tưởng sức mạnh", True), ("中二", "ảo tưởng sức mạnh", True),
    ("宅男", "trai ở nhà", True), ("宅女", "gái ở nhà", True), ("干饭人", "dân mê ăn", True),
    ("剁手党", "hội nghiện mua sắm", True),
    ("月光族", "dân tháng nào tiêu hết lương tháng đó", True), ("啃老族", "dân ăn bám bố mẹ", True),
    ("啃老", "ăn bám bố mẹ", True), ("妈宝男", "con trai bám váy mẹ", True), ("凤凰男", "trai nghèo đổi đời", True),
    ("小三", "tiểu tam", True), ("吃瓜群众", "dân hóng drama", True), ("黑粉", "anti-fan", True),
    ("猪队友", "đồng đội phá hoại", True), ("神队友", "đồng đội thần thánh", True), ("背锅侠", "người gánh tội thay", True),
    ("天选之子", "người được trời chọn", True), ("气运之子", "khí vận chi tử", True), ("龙傲天", "Long Ngạo Thiên", True),
    ("炮灰", "pháo hôi", True),
    # việc làm / hành động
    ("内卷", "cạnh tranh khốc liệt", True), ("躺平", "buông xuôi mặc kệ", True), ("摆烂", "mặc kệ cho hỏng luôn", True),
    ("躺赢", "thắng mà không cần làm gì", True), ("躺枪", "bị vạ lây", True), ("带飞", "gánh team", True),
    ("坑队友", "phá đồng đội", True), ("甩锅", "đổ lỗi", True), ("背锅", "gánh tội thay", True), ("打脸", "vả mặt", True),
    ("逆袭", "lội ngược dòng", True), ("开挂", "như bật hack", True), ("外挂", "hack", False), ("氪金", "nạp tiền", True),
    ("爆肝", "cày thâu đêm", True), ("白嫖", "hưởng miễn phí", True), ("薅羊毛", "săn đồ miễn phí", True),
    ("割韭菜", "lùa gà", False), ("吃瓜", "hóng drama", False), ("凡尔赛", "khoe khéo", False), ("吐槽", "châm chọc", True),
    ("打call", "cổ vũ nhiệt tình", True), ("种草", "bị thu hút muốn mua", False), ("剁手", "mua sắm điên cuồng", False),
    ("带货", "bán hàng livestream", True), ("恰饭", "kiếm cơm", True), ("干饭", "ăn cơm", False), ("摸鱼", "lười việc", False),
    ("撒狗粮", "phát cơm chó", True), ("秀恩爱", "khoe tình cảm", True), ("官宣", "công khai chính thức", True),
    ("网恋", "yêu qua mạng", True), ("奔现", "gặp nhau ngoài đời", True), ("嗑CP", "đẩy thuyền", True),
    ("磕到了", "ngọt quá", True), ("皮一下", "nghịch một chút", True), ("打榜", "cày bảng xếp hạng", True),
    ("出圈", "nổi tiếng rộng rãi", True), ("塌房", "sụp đổ hình tượng", False), ("人设崩了", "sụp đổ hình tượng", True),
    ("打怪升级", "đánh quái thăng cấp", True), ("扮猪吃老虎", "giả heo ăn thịt hổ", True), ("坑爹", "hại người", True),
    # sự vật / khái niệm
    ("人设", "hình tượng", True), ("颜值", "nhan sắc", True), ("狗血", "cẩu huyết", False), ("玛丽苏", "Mary Sue", True),
    ("饭圈", "giới fan", True), ("内娱", "giới giải trí Hoa ngữ", True), ("顶流", "ngôi sao hạng nhất", True),
    ("爆款", "hàng hot", True), ("智商税", "thuế IQ (tiền mất vì cả tin)", True), ("霸道总裁", "tổng tài bá đạo", True),
    ("金手指", "bàn tay vàng", True), ("网红打卡地", "địa điểm sống ảo", True), ("狗粮", "cơm chó", False),
]

_LATIN = re.compile(r"^[A-Za-z0-9]+$")


@lru_cache(maxsize=1)
def table() -> dict[str, tuple[str, bool]]:
    return {k: (v, s) for k, v, s in SLANG}


def _pat(key: str) -> re.Pattern:
    """Từ chữ Latin / số (yyds, 666) chỉ khớp nguyên từ, không phân biệt hoa thường; chữ Hán khớp chuỗi con."""
    if _LATIN.match(key):
        return re.compile(r"(?<![A-Za-z0-9])" + re.escape(key) + r"(?![A-Za-z0-9])", re.IGNORECASE)
    if re.search(r"[A-Za-z]", key):   # 栓Q, emo了, 嗑CP
        return re.compile(re.escape(key), re.IGNORECASE)
    return re.compile(re.escape(key))


@lru_cache(maxsize=512)
def _cpat(key: str) -> re.Pattern:
    return _pat(key)


def found(lines, skip: dict | None = None, strict: bool = False) -> dict[str, str]:
    """Các từ lóng có trong các dòng -> nghĩa. skip: bảng thuật ngữ (từ có trong đó bỏ qua, kể cả khi nằm trong 1 từ
    của bảng). strict: chỉ từ chắc chắn là lóng. Từ dài trước – 吃瓜群众 không ra thêm 吃瓜."""
    text = "\n".join(lines) if not isinstance(lines, str) else lines
    out: dict[str, str] = {}
    taken: list[tuple[int, int]] = []
    for k in sorted(table(), key=len, reverse=True):
        v, s = table()[k]
        if strict and not s:
            continue
        if skip and (k in skip or any(k in g for g in skip)):
            continue
        for m in _cpat(k).finditer(text):
            if any(a <= m.start() < b or a < m.end() <= b for a, b in taken):
                continue
            taken.append((m.start(), m.end()))
            out[k] = v
    return out


def apply_src(text: str, skip: dict | None = None) -> str:
    """Thay từ CHẮC CHẮN là lóng trong câu gốc bằng nghĩa tiếng Việt trước khi gửi Google (như bảng thuật ngữ)."""
    hits = found(text, skip, strict=True)
    for k in sorted(hits, key=len, reverse=True):
        text = _cpat(k).sub(f" {hits[k]} ", text)
    return text


def note(keys) -> str:
    keys = list(dict.fromkeys(keys))
    if not keys:
        return ""
    return (f"gặp {len(keys)} từ lóng / tiếng mạng ({', '.join(keys[:4])}{', …' if len(keys) > 4 else ''}) – đã gửi "
            "nghĩa quen dùng cho công cụ dịch")
