"""Đổi tên riêng / thuật ngữ còn để phiên âm pinyin trong bản dịch tiếng Việt sang âm Hán Việt (2.4.2).

Google Dịch / Hy-MT2 (và đôi khi AI) để nguyên tên / thuật ngữ chưa có trong bảng thuật ngữ dưới dạng pinyin:
"林凡来了" -> "Lin Fan đến rồi", "Tianjian Zong", "jianghu". Hàm `fix_line` đối chiếu từng cụm từ của bản dịch với CHỮ
HÁN CỦA CÂU GỐC: cụm chỉ được đổi khi từng tiếng của nó khớp đúng âm pinyin (hoặc đã là âm Hán Việt) của các chữ Hán
liền nhau trong câu gốc -> "Lâm Phàm". Không gọi mạng, không tốn API; bảng âm Hán Việt tự soạn (`hanviet_data.py`), âm
pinyin lấy bằng pypinyin.

Luật để không đổi nhầm câu thường:
- cụm viết hoa (tên người, địa danh, môn phái, đồ vật, chiêu thức…) hoặc cụm viết thường ≥ 2 tiếng mà phần lớn từ
  không phải từ tiếng Việt ("jianghu" -> "giang hồ"; "ta lai" giữ nguyên); từ có dấu riêng của tiếng Việt (â ă ê ô
  ơ ư đ, dấu hỏi / ngã / nặng) chỉ khớp âm Hán Việt; từ có dấu sắc / huyền phải khớp ĐÚNG dấu thanh của pinyin ("Xin
  chào" không thành 新潮);
- ít nhất 1 tiếng còn là pinyin (khác âm Hán Việt); cụm 1 tiếng chỉ đổi khi là họ / tên đi với danh xưng ("ông Lin",
  "Lin tổng") hoặc chữ gốc có 老 / 小 / 阿 đứng trước;
- tên thương hiệu / tên nước ngoài viết giống pinyin ("Xiaomi", "Anna", "Linda") giữ nguyên;
- tên có trong bảng thuật ngữ dùng đúng bản trong bảng; chữ chưa có trong bảng âm -> giữ nguyên cả tên;
- chữ nhiều cách đọc lấy âm theo pinyin đã khớp (长 "Chang" -> Trường, "Zhang" -> Trưởng).
"""
from __future__ import annotations

import re
import unicodedata
from functools import lru_cache

# họ + tên ghép / địa danh / nhân vật có cách đọc quen dùng khác âm từng chữ
WORDS = {
    "诸葛": "Gia Cát", "欧阳": "Âu Dương", "司马": "Tư Mã", "上官": "Thượng Quan", "慕容": "Mộ Dung",
    "令狐": "Lệnh Hồ", "东方": "Đông Phương", "西门": "Tây Môn", "南宫": "Nam Cung", "皇甫": "Hoàng Phủ",
    "公孙": "Công Tôn", "宇文": "Vũ Văn", "夏侯": "Hạ Hầu", "轩辕": "Hiên Viên", "长孙": "Trưởng Tôn",
    "尉迟": "Uất Trì", "独孤": "Độc Cô", "端木": "Đoan Mộc", "百里": "Bách Lý", "呼延": "Hô Diên", "司徒": "Tư Đồ",
    "司空": "Tư Không", "澹台": "Đạm Đài", "申屠": "Thân Đồ", "赫连": "Hách Liên", "拓跋": "Thác Bạt",
    "钟离": "Chung Ly", "闻人": "Văn Nhân", "单于": "Thiền Vu", "万俟": "Mặc Kỳ", "太史": "Thái Sử",
    "曹操": "Tào Tháo", "貂蝉": "Điêu Thuyền", "嫦娥": "Hằng Nga", "释迦": "Thích Ca", "哪吒": "Na Tra",
    "重庆": "Trùng Khánh", "长安": "Trường An", "长沙": "Trường Sa", "长江": "Trường Giang", "长城": "Trường Thành",
    "长白": "Trường Bạch", "长春": "Trường Xuân", "香港": "Hồng Kông", "澳门": "Ma Cao", "厦门": "Hạ Môn",
    "乐山": "Lạc Sơn", "北斗": "Bắc Đẩu", "蚌埠": "Bạng Phụ", "台湾": "Đài Loan", "西藏": "Tây Tạng",
    "新疆": "Tân Cương", "峨眉": "Nga Mi", "崆峒": "Không Động", "武当": "Võ Đang", "昆仑": "Côn Lôn",
}

# họ thường gặp (1 chữ) – cụm 1 tiếng chỉ đổi khi là họ đi với danh xưng
SURNAMES = set(
    "王李张刘陈杨黄赵吴周徐孙马朱胡郭何高林罗郑梁谢宋唐许韩冯邓曹彭曾肖田董袁潘于蒋蔡余杜叶程苏魏吕丁任沈姚卢姜崔钟"
    "谭陆汪范金石廖贾夏韦付方白邹孟熊秦邱江尹薛闫段雷侯龙史陶黎贺顾毛郝龚邵万钱严覃武戴莫孔向汤常温康施文牛樊葛邢安"
    "齐易乔伍庞颜倪庄聂章鲁岳翟殷詹申欧耿关兰焦俞左柳甘祝包宁尚符舒阮柯纪梅童凌毕单季裴霍涂成苗谷盛曲翁冉骆蓝路游辛"
    "靳管柴蒙鲍华喻祁蒲房滕屈饶解牟艾尤阳时穆农司卓古吉缪简车项连芦麦褚娄窦戚岑景党宫费卜冷晏席卫米柏宗瞿桂全佟应臧"
    "闵苟邬边卞姬师和仇栾隋商刁沙荣巫寇桑郎甄丛仲虞敖巩明佘池查麻苑迟邝官封谈匡鞠惠荆乐冀郁胥南班储原栗燕楚鄢劳谌奚"
    "皮粟冼蔺楼盘满闻位厉伊仝区郜海阚花权强帅屠豆朴盖练廉禹井祖漆巴丰支卿国狄平计索宣晋相初门云容敬来扈晁芮都普阙浦"
    "戈伏鹿薄邸雍辜羊乌母裘亓修邰赫杭况那宿鲜印逯隆茹诸战慕危玉银亢嵇公湛宾戎勾茅利於呼居揭干但尉冶斯元束檀衣信展"
    "阴昝智幸奉植衡富尧由萧沐楚霍凤")
PREFIX_ZH = "老小阿"    # 老王 / 小李 / 阿明: chữ ngay sau là tên dù 1 tiếng

# ---- loại tên (2.4.4): tên người / địa danh – môn phái – đồ vật / từ thường (viết thường) – đoán theo chữ Hán
KINDS = ("person", "place", "term")
KIND_LABEL = {"person": "Tên người", "place": "Địa danh, môn phái, đồ vật", "term": "Từ thường"}
NOTE_LABEL = {"person": "tên người", "place": "tên địa danh / môn phái / đồ vật", "term": "từ thường"}
NAME_MODES = [("all", "Tên người + địa danh, môn phái, đồ vật"), ("people", "Chỉ tên người"),
              ("places", "Chỉ địa danh, môn phái, đồ vật")]
# họ ghép / nhân vật trong WORDS là tên người, còn lại là địa danh
PERSON_WORDS = {"诸葛", "欧阳", "司马", "上官", "慕容", "令狐", "东方", "西门", "南宫", "皇甫", "公孙", "宇文", "夏侯",
                "轩辕", "长孙", "尉迟", "独孤", "端木", "百里", "呼延", "司徒", "司空", "澹台", "申屠", "赫连", "拓跋",
                "钟离", "闻人", "单于", "万俟", "太史", "曹操", "貂蝉", "嫦娥", "释迦", "哪吒"}
PLACE_WORDS = {"江南", "中原", "西域", "南疆", "北境", "东海", "南海", "西海", "北海", "京城", "皇城", "天庭", "地府",
               "人间", "魔界", "仙界", "神界", "妖界", "九州", "中州", "东洲", "西洲", "南洲", "北洲"}
# chữ cuối gần như chỉ có ở địa danh / môn phái / tổ chức / đồ vật / chiêu thức (tên người hiếm khi kết thúc bằng)
PLACE_TAIL = set("宗门派州市县镇村乡府宫殿阁寺庙观庵岛国界域洞塔关桥街路堡寨营帮会盟教族团军馆店铺剑刀枪戟鞭扇珠鼎"
                 "炉镜旗经诀谱功法拳掌阵谷岭坡崖家院堂楼局部司厅号舰街坊")
# chữ cuối chỉ dòng họ / tổ chức / nơi chốn: họ + chữ này vẫn là địa danh / tổ chức (王家, 林府, 苏州)
GROUP_TAIL = set("宗门派家族帮会盟教府宫殿阁寺庙观国州市县镇村乡堡寨营馆店铺")
# chữ cuối hay gặp ở cả địa danh lẫn tên người (王海, 李江, 陈峰) – chỉ tính là địa danh khi chữ đầu không phải họ
PLACE_TAIL_WEAK = set("山峰江河湖海林城园庄台池泉渊原星洲川溪泽")


def name_kind(chars: str, prev: str = "", lower: bool = False) -> str:
    """Đoán loại của tên theo chữ Hán: "term" (bản dịch viết thường), "person" (tên người) hoặc "place" (địa danh,
    môn phái, tổ chức, đồ vật, chiêu thức…). prev = chữ Hán ngay trước tên trong câu gốc."""
    if lower:
        return "term"
    if chars in PERSON_WORDS:
        return "person"
    if chars in PLACE_WORDS or chars in WORDS:
        return "place"
    if len(chars) == 1 or (prev and prev in PREFIX_ZH) or (chars.endswith("儿") and len(chars) <= 3):
        return "person"     # tên 1 tiếng (đã có danh xưng đi kèm) / 老王 / 小李 / 婉儿
    last = chars[-1]
    if last in PLACE_TAIL:
        # họ + 1 chữ: tên người (陈剑, 李军) – trừ chữ chỉ dòng họ / tổ chức / nơi chốn (王家, 林府, 苏州)
        if chars[0] in SURNAMES and len(chars) == 2 and last not in GROUP_TAIL:
            return "person"
        return "place"
    if any(chars.startswith(w) for w in PERSON_WORDS if len(w) == 2 and w in WORDS and len(chars) <= 4):
        return "person"     # họ ghép: 诸葛亮, 欧阳锋, 东方不败
    if chars[0] in SURNAMES and len(chars) <= 3:
        return "person"
    return "place"


def kinds_from(settings: dict | None) -> set[str]:
    """Các loại được đổi sang Hán Việt khi dịch theo Cài đặt: `trans_name_fix` (tên riêng) + `trans_name_kinds`
    (all / people / places) + `trans_term_fix` (từ thường). Tắt hết -> tập rỗng = dịch như cũ."""
    st = settings or {}
    out: set[str] = set()
    if st.get("trans_name_fix"):
        mode = st.get("trans_name_kinds") or "all"
        if mode in ("all", "people"):
            out.add("person")
        if mode in ("all", "places"):
            out.add("place")
    if st.get("trans_term_fix"):
        out.add("term")
    return out
# danh xưng tiếng Việt đứng trước / sau tên 1 tiếng
TITLES_BEFORE = {
    "ông", "bà", "anh", "chị", "cô", "chú", "bác", "cậu", "dì", "thím", "mợ", "em", "lão", "tiểu", "a", "cụ", "ngài",
    "thầy", "sếp", "tổng", "đại", "huynh", "tỷ", "muội", "đệ", "nhà", "họ", "tên", "gã", "con", "thằng", "nàng",
    "chàng", "cháu", "bạn", "mr", "mrs", "ms", "miss", "đồng", "phó", "giám", "trưởng", "đạo", "sư", "tông",
}
TITLES_AFTER = {
    "tổng", "tiên", "lão", "tiểu", "đại", "thiếu", "công", "cô", "phu", "nữ", "ca", "tỷ", "huynh", "muội", "đệ",
    "gia", "thúc", "bá", "di", "sư", "chưởng", "giáo", "tướng", "thị",
}
# viết giống pinyin nhưng là thương hiệu / từ quen dùng / tên nước ngoài -> giữ nguyên
KEEP = {
    "xiaomi", "huawei", "alibaba", "taobao", "baidu", "weibo", "weixin", "douyin", "bilibili", "youku", "meituan",
    "pinduoduo", "kuaishou", "zhihu", "xiaohongshu", "didi", "haier", "geely", "chery", "hongqi", "wuling", "changan",
    "lenovo", "oppo", "vivo", "honor", "xiaopeng", "kungfu", "gongfu", "taichi", "taiji", "fengshui", "mahjong",
    "majiang", "wushu", "qigong", "tofu", "wonton", "ok",
    "anna", "linda", "lina", "luna", "nina", "hanna", "mona", "wanda", "rita", "tina", "dina", "lisa", "mina",
    "nana", "hana", "sana", "lena", "alan", "sean", "ken", "zen", "ben", "dan", "kai", "leo", "mia", "lia", "sasha",
    "diana", "mila", "lulu", "coco", "momo", "kiki", "gigi", "mimi", "nini", "lili", "sisi", "titi",
}
# từ tiếng Việt viết không dấu trùng vần pinyin: cụm viết thường chỉ đổi khi phần lớn từ KHÔNG phải từ tiếng Việt
_VN_WORDS = """
a ai am an anh ao ba ban bang banh bao bay be ben bi bia binh bo boi bom bon bong bu bua bui ca cai cam can canh cao
cay cha chai cham chan chang chanh chao chay che chi chia chim chin chinh cho choi chu chua chung co coi com con cong
cu cua cung da dai dam dang danh dao day de den di dia dinh do doi don dong du dua dung em ga gai gan gang gao gay ghe
gi gia giai gian giang giao giay gio go goi gom ha hai ham han hang hanh hao hay he hen hi hinh ho hoa hoai hoan hoang
hoi hom hon hong hu hua hung huy i im in ke keo khi kho khoa khoan khoe khong khu kia kim kinh la lai lam lan lang lanh
lao lay le len li lien linh lo loa loai loan loi lon long lu lua luan lui lung luon luong ly ma mai man mang manh mao
may me men mi mien minh mo moi mon mong mu mua muon my na nai nam nan nang nao nay ne nem ngan ngang ngay nghe nghi
nghia ngo ngoai ngon ngu nguoi nha nhan nhanh nhau nhe nhi nhieu nho nhu nhung ni no noi non nong nu nua o oi om on ong
pha phai phan phi pho phong phu qua quan quang que qui quy ra sa sai san sang sanh sao sau say se sen si sinh so soi
son song su sua sung ta tai tam tan tang tanh tao tay te tem ten thai tham than thang thanh thao thay the then thi
thich thien thinh tho thoi thom thon thu thua thuc thuong thuy ti tia tien tim tin tinh to toa toan toi tom ton tong
tra trai tram tran trang tranh trao trau tre tren tri trinh tro troi trong tru trung tu tua tui tung tuoi tuong tuy u
ung va vai van vang vao vay ve ven vi vien vinh vo voi von vong vu vua vui xa xanh xao xe xem xen xi xin xinh xo xoa
xong xu xua y yen yeu ok
"""
VN_PLAIN = frozenset(_VN_WORDS.split())
MAX_JOIN = 4      # 1 từ viết liền tối đa 4 tiếng ("Shimin", "Ouyangfeng")
_WORD = re.compile(r"[^\W\d_]+(?:['’][^\W\d_]+)*")
_HAN = re.compile(r"[㐀-䶿一-鿿]")
_VN_ONLY = {"̂", "̆", "̛", "̉", "̃", "̣"}   # ^ ˘ móc hỏi ngã nặng
_PY_ONLY = {"̄", "̌", "̈"}                                 # ngang ˇ ü
_AMBIG = {"́", "̀"}                                             # sắc huyền (cả 2 tiếng đều có)


@lru_cache(maxsize=1)
def table() -> dict[str, list[tuple[str, str]]]:
    """Chữ -> [(pinyin, âm)] (pinyin "" = mọi cách đọc). Đọc 1 lần từ hanviet_data."""
    from .hanviet_data import DATA

    out: dict[str, list[tuple[str, str]]] = {}
    for tok in DATA.split():
        tok = unicodedata.normalize("NFC", tok)
        ch, rd = tok[0], tok[1:]
        if not rd:
            continue
        if "=" in rd:
            out[ch] = [tuple(x.split("=", 1)) for x in rd.split("/") if "=" in x]   # type: ignore[misc]
        else:
            out[ch] = [("", rd)]
    return out


def reading(ch: str, py: str = "") -> str | None:
    """Âm Hán Việt của 1 chữ; py: pinyin không dấu đã khớp (chọn đúng âm của chữ nhiều cách đọc)."""
    r = table().get(ch)
    if not r:
        return None
    if py:
        for k, v in r:
            if k == py:
                return v
    return r[0][1]


def _strip(s: str) -> str:
    s = unicodedata.normalize("NFD", s.lower())
    s = "".join(c for c in s if not unicodedata.combining(c))
    return s.replace("v", "u").replace("'", "").replace("’", "")


@lru_cache(maxsize=8192)
def _py(ch: str) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """(pinyin không dấu, pinyin có dấu thanh) mọi cách đọc của 1 chữ Hán."""
    try:
        from pypinyin import Style, pinyin
    except Exception:  # noqa: BLE001 – chưa cài pypinyin: không đổi gì
        return (), ()
    plain = tuple(dict.fromkeys(_strip(x) for x in pinyin(ch, style=Style.NORMAL, heteronym=True)[0]))
    tone = tuple(dict.fromkeys(unicodedata.normalize("NFC", x.lower())
                               for x in pinyin(ch, style=Style.TONE, heteronym=True)[0]))
    return plain, tone


def available() -> bool:
    try:
        import pypinyin  # noqa: F401
        return True
    except Exception:  # noqa: BLE001
        return False


def _kind(w: str) -> str:
    """vi = chỉ có thể là tiếng Việt, tone = có dấu sắc / huyền (khớp đúng dấu thanh pinyin), py = dấu chỉ pinyin có,
    plain = không dấu."""
    low = w.lower()
    if "đ" in low:
        return "vi"
    marks = {c for c in unicodedata.normalize("NFD", low) if unicodedata.combining(c)}
    if marks & _VN_ONLY:
        return "vi"
    if marks & _PY_ONLY:
        return "py"
    if marks & _AMBIG:
        return "tone"
    return "plain"


def _match_word(w: str, kind: str, han: list[str], j: int):
    """Các cách 1 từ khớp chữ Hán bắt đầu ở han[j]: [(số chữ, [pinyin không dấu từng chữ] | None nếu khớp âm HV)]."""
    out = []
    low = unicodedata.normalize("NFC", w.lower())
    for _k, v in table().get(han[j], []):
        if v.lower() == low:
            out.append((1, None))
            break
    if kind == "vi":
        return out
    target_plain = _strip(w)
    target_tone = low.replace("'", "").replace("’", "")

    def walk(rest: str, k: int, acc: list[str], toned: bool):
        if k >= len(han) or len(acc) >= MAX_JOIN:
            return
        plain, tone = _py(han[k])
        for a, b in zip(plain, tone) if toned else ((x, x) for x in plain):
            pat = b if toned else a
            if rest.startswith(pat):
                nxt = rest[len(pat):]
                if not nxt:
                    out.append((len(acc) + 1, acc + [a]))
                else:
                    walk(nxt, k + 1, acc + [a], toned)
    if kind in ("plain", "py"):
        walk(target_plain, j, [], False)
    if kind in ("tone", "py"):
        walk(target_tone, j, [], True)
    return out


def _align(words: list[str], kinds: list[str], han: list[str], j: int):
    """Mọi cách khớp cả dãy từ vào các chữ Hán liền nhau từ han[j]:
    [(số chữ, [pinyin | None mỗi chữ], có tiếng pinyin)]."""
    res = []

    def go(i: int, k: int, acc: list, py_hit: bool):
        if i == len(words):
            res.append((k - j, acc, py_hit))
            return
        if k >= len(han):
            return
        for n, pys in _match_word(words[i], kinds[i], han, k):
            if pys is None:
                go(i + 1, k + 1, acc + [None], py_hit)
            else:
                go(i + 1, k + n, acc + pys, True)
    go(0, j, [], False)
    return res


def _han_runs(src: str) -> list[str]:
    """Các đoạn chữ Hán liền nhau của câu gốc."""
    runs, cur = [], []
    for c in src:
        if _HAN.match(c):
            cur.append(c)
        elif cur:
            runs.append("".join(cur))
            cur = []
    if cur:
        runs.append("".join(cur))
    return runs


def _build(chars: str, pys: list, glossary: dict | None) -> str | None:
    """Tên Hán Việt cho đoạn chữ: bảng thuật ngữ > WORDS > âm từng chữ (theo pinyin đã khớp); thiếu âm -> None."""
    if glossary:
        g = glossary.get(chars)
        if isinstance(g, str) and g.strip():
            return g.strip()
    out, i = [], 0
    while i < len(chars):
        hit = next((k for k in sorted(WORDS, key=len, reverse=True) if chars.startswith(k, i)), None)
        if hit:
            out.append(WORDS[hit])
            i += len(hit)
            continue
        r = reading(chars[i], pys[i] if i < len(pys) and pys[i] else "")
        if not r:
            return None
        out.append(r)
        i += 1
    return " ".join(out)


def fix_line(trans: str, src: str, glossary: dict | None = None,
             kinds=None) -> tuple[str, list[tuple[str, str]]]:
    """Đổi các tên còn để pinyin trong 1 câu dịch tiếng Việt sang Hán Việt theo chữ Hán của câu gốc.
    kinds: các loại được đổi (None = mọi loại). Trả (câu mới, [(cũ, mới)])."""
    out, done, _skip = fix_line_ex(trans, src, glossary, kinds)
    return out, [(x["old"], x["new"]) for x in done]


def fix_line_ex(trans: str, src: str, glossary: dict | None = None, kinds=None, pick=None):
    """Như fix_line nhưng trả (câu mới, [đã đổi], [bỏ qua vì loại đang tắt]) – mỗi mục {old, new, zh, kind}.
    pick(mục) -> chữ thay (hoặc None = để nguyên): người dùng chọn từng tên ở hộp xem trước (bỏ qua kinds)."""
    if not trans or not src or not _HAN.search(src) or not available():
        return trans, [], []
    runs = _han_runs(src)
    words = list(_WORD.finditer(trans))
    if not words:
        return trans, [], []
    # nhóm các từ liền nhau (chỉ cách nhau bằng khoảng trắng) cùng kiểu: viết hoa (tên người / địa danh / môn phái /
    # đồ vật…) hoặc viết thường (thuật ngữ Google để nguyên: "jianghu", "dantian")
    groups: list[list[int]] = []
    for i, m in enumerate(words):
        up = m.group()[0].isupper()
        if (groups and groups[-1][-1] == i - 1 and not trans[words[i - 1].end():m.start()].strip()
                and words[i - 1].group()[0].isupper() == up):
            groups[-1].append(i)
        else:
            groups.append([i])
    edits: list[tuple[int, int, dict]] = []   # (đầu, cuối, mục) trong bản dịch
    skipped: list[dict] = []
    for g in groups:
        used = [False] * len(g)
        for size in range(len(g), 0, -1):
            for a in range(len(g) - size + 1):
                if any(used[a:a + size]):
                    continue
                idx = g[a:a + size]
                ws = [words[i].group() for i in idx]
                if _strip("".join(ws)) in KEEP or any(_strip(w) in KEEP for w in ws):
                    continue
                wk = [_kind(w) for w in ws]
                if all(k == "vi" for k in wk):
                    continue
                lower = not ws[0][0].isupper()
                if lower:
                    # viết thường: chỉ thuật ngữ rõ là pinyin – không từ nào chỉ có thể là tiếng Việt, phần lớn từ không
                    # phải từ tiếng Việt viết không dấu ("ta lai" giữ, "jiang hu" / "dantian" đổi)
                    if "vi" in wk:
                        continue
                    nv = sum(1 for w in ws if _strip(w) not in VN_PLAIN)
                    if not nv or nv < len(ws) - nv:
                        continue
                cands: dict[str, tuple[str, str]] = {}    # tên mới -> (chữ Hán, chữ Hán đứng trước)
                for run in runs:
                    for j in range(len(run)):
                        for n, pys, py_hit in _align(ws, wk, list(run), j):
                            if not py_hit:
                                continue
                            chars = run[j:j + n]
                            if n == 1 and (lower or not _single_ok(trans, words, idx, run, j)):
                                continue
                            new = _build(chars, pys, glossary)
                            if new and lower and not (glossary and chars in glossary):
                                new = new.lower()
                            if new:
                                cands.setdefault(new, (chars, run[j - 1] if j else ""))
                if len(cands) != 1:
                    continue      # không khớp / khớp nhiều tên khác nhau -> để nguyên
                new, (zh, prev) = next(iter(cands.items()))
                s, e = words[idx[0]].start(), words[idx[-1]].end()
                old = trans[s:e]
                if new.lower() == old.lower():
                    continue
                item = {"old": old, "new": new, "zh": zh, "kind": name_kind(zh, prev, lower)}
                for k in range(a, a + size):
                    used[k] = True      # đã xét cụm này (đổi hoặc bỏ qua) – không đổi mẩu con của nó
                if pick is not None:
                    rep = pick(item)
                    if not rep or rep == old:
                        continue
                    item = {**item, "new": rep}
                elif kinds is not None and item["kind"] not in kinds:
                    skipped.append(item)
                    continue
                edits.append((s, e, item))
    if not edits:
        return trans, [], skipped
    out = trans
    for s, e, it in sorted(edits, key=lambda x: x[0], reverse=True):
        out = out[:s] + it["new"] + out[e:]
    return out, [it for _s, _e, it in sorted(edits, key=lambda x: x[0])], skipped


def _single_ok(trans: str, words, idx: list[int], run: str, j: int) -> bool:
    """Tên 1 tiếng: chữ gốc là họ (hoặc 老 / 小 / 阿 đứng trước) VÀ bản dịch có danh xưng ngay trước / sau
    ("ông Lin", "Lin tổng", "Tiểu Ming")."""
    prev_zh = run[j - 1] if j > 0 else ""
    if run[j] not in SURNAMES and not (prev_zh and prev_zh in PREFIX_ZH):
        return False
    first, last = idx[0], idx[-1]
    if first > 0:
        pw = words[first - 1]
        if not trans[pw.end():words[first].start()].strip() and pw.group().lower() in TITLES_BEFORE:
            return True
    if last + 1 < len(words):
        nw = words[last + 1]
        if not trans[words[last].end():nw.start()].strip() and nw.group().lower() in TITLES_AFTER:
            return True
    return False


def fix_segments(segs, glossary: dict | None = None, rows=None,
                 kinds=None) -> tuple[list[int], list[tuple[str, str]]]:
    """Sửa bản dịch các câu (dự án đã dịch – nút "Sửa tên pinyin"). Trả (câu đổi, [(cũ, mới)])."""
    changed, pairs = [], []
    for k in (range(len(segs)) if rows is None else rows):
        x = segs[k]
        if not (x.trans or "").strip():
            continue
        new, ps = fix_line(x.trans, x.text, glossary, kinds)
        if ps and new != x.trans:
            mark(x, ps)
            x.trans = new
            changed.append(k)
            pairs += ps
    return changed, pairs


def mark(seg, pairs) -> None:
    """Ghi các tên vừa đổi vào câu (tooltip bản dịch)."""
    seg.extra["name_fixed"] = (list(seg.extra.get("name_fixed") or []) + [list(p) for p in pairs])[-6:]


def candidates(segs, glossary: dict | None = None, rows=None) -> list[dict]:
    """Hộp xem trước của nút "Sửa tên pinyin" (2.4.4): mọi tên / từ còn để pinyin trong các câu, gom theo (cũ, mới):
    [{old, new, zh, kind, rows, count, example}] – nhiều câu trước."""
    agg: dict[tuple[str, str], dict] = {}
    for k in (range(len(segs)) if rows is None else rows):
        x = segs[k]
        if not (x.trans or "").strip():
            continue
        _out, done, _skip = fix_line_ex(x.trans, x.text, glossary)
        for it in done:
            key = (it["old"], it["new"])
            c = agg.setdefault(key, {**it, "rows": [], "count": 0, "example": x.trans})
            if k not in c["rows"]:
                c["rows"].append(k)
            c["count"] += 1
    return sorted(agg.values(), key=lambda c: (-c["count"], c["old"].lower()))


def apply_choice(segs, choice: dict, glossary: dict | None = None, rows=None):
    """Đổi đúng các tên người dùng đã tích ở hộp xem trước. choice {(cũ, mới đề xuất): chữ thay (có thể đã sửa tay)}.
    Trả (câu đổi, [(cũ, mới)])."""
    changed, pairs = [], []

    def pick(it):
        return choice.get((it["old"], it["new"]))

    for k in (range(len(segs)) if rows is None else rows):
        x = segs[k]
        if not (x.trans or "").strip():
            continue
        new, done, _skip = fix_line_ex(x.trans, x.text, glossary, pick=pick)
        if done and new != x.trans:
            ps = [(it["old"], it["new"]) for it in done]
            mark(x, ps)
            x.trans = new
            changed.append(k)
            pairs += ps
    return changed, pairs


def note(pairs: list[tuple[str, str]], lines: int, skipped=None, kinds=None) -> str:
    """'đổi 5 tên còn để pinyin sang Hán Việt ở 4 câu (Lin Fan → Lâm Phàm, …) · bỏ qua 3 từ thường (đang tắt)'"""
    parts = []
    if pairs:
        ex = list(dict.fromkeys(f"{o} → {n}" for o, n in pairs))[:3]
        parts.append(f"đổi {len(pairs)} tên còn để pinyin sang Hán Việt ở {lines} câu (" + ", ".join(ex)
                     + (", …" if len(set(pairs)) > 3 else "") + ")")
    if skipped:
        by: dict[str, int] = {}
        for it in skipped:
            by[it["kind"]] = by.get(it["kind"], 0) + 1
        bits = [f"{n} {NOTE_LABEL[k]}" for k, n in sorted(by.items(), key=lambda kv: KINDS.index(kv[0]))]
        parts.append("bỏ qua " + " và ".join(bits) + " còn để pinyin (loại này đang tắt)")
    return " · ".join(parts)
