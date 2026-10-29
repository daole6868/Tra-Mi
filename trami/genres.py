"""Form dịch theo THỂ LOẠI PHIM (2.1.13) – ô "Thể loại phim" ở tab Tự động / Phụ đề / Cài đặt.

Mỗi thể loại là 1 đoạn hướng dẫn gắn vào lời nhắc hệ thống của AI khi dịch (và khi AI kiểm tra bản dịch): xưng hô, thuật
ngữ chuẩn (chữ gốc -> cách dịch), giọng văn, điều cần tránh. Luật chung (đúng số dòng, đọc 2 dòng trước / sau, không
thêm bớt ý) nằm trong `translate.SYSTEM_PROMPT` – form thể loại chỉ thêm phần riêng của thể loại.

Bản tiếng Việt sửa được (hộp "Sửa form…", Cài đặt `genre_forms` [{"id", "text"}] – danh sách để xoá được 1 mục khi
lưu Cài đặt, vì dict bị gộp sâu); "Về mặc định" = xoá bản đã sửa. Dịch sang
tiếng khác dùng bản tiếng Anh `en` (ngắn, không có bảng Hán Việt). Mã cũ (auto / co_trang / hien_dai / review / hoat_hinh /
tai_lieu) giữ nguyên để dự án / bộ nhớ bản dịch cũ dùng tiếp.
"""
from __future__ import annotations

GENRES: list[dict] = [
    {"id": "auto", "name": "Tự nhận biết", "vi": (
        "- Tự nhận biết thể loại phim từ nội dung các dòng và THÔNG TIN PHIM, rồi dùng xưng hô, thuật ngữ, giọng văn "
        "đúng thể loại đó; giữ nhất quán suốt phim.\n"
        "- Phim cổ trang / tu tiên / kiếm hiệp: xưng hô cổ trang (ta – ngươi, huynh – đệ…), thuật ngữ Hán Việt. Phim hiện "
        "đại: xưng hô đời thường (anh – em, tôi – cậu, bố – con…)."),
     "en": "Infer the genre from the lines and the film notes; use the matching forms of address, terminology and "
           "tone, consistently for the whole film."},

    # ---------------------------------------------------------------- cổ trang / tu tiên
    {"id": "tu_tien", "name": "Tu tiên – tiên hiệp", "vi": (
        "- Bối cảnh: thế giới tu luyện thành tiên, tông môn, cảnh giới.\n"
        "- Xưng hô: ta – ngươi; sư tôn / sư phụ – đồ nhi / đệ tử; sư huynh – sư đệ, sư tỷ – sư muội; đạo hữu (người tu "
        "ngang hàng); tiền bối – vãn bối; tự xưng bổn tọa, lão phu, tại hạ; người phàm: công tử, cô nương.\n"
        "- Cảnh giới (viết hoa): 练气 Luyện Khí, 筑基 Trúc Cơ, 金丹 Kim Đan, 元婴 Nguyên Anh, 化神 Hóa Thần, 炼虚 Luyện Hư, "
        "合体 Hợp Thể, 大乘 Đại Thừa, 渡劫 Độ Kiếp; 初期 / 中期 / 后期 / 大圆满 sơ kỳ / trung kỳ / hậu kỳ / đại viên mãn; "
        "一层 tầng một; 突破 đột phá, 瓶颈 bình cảnh.\n"
        "- Thuật ngữ: 灵气 linh khí, 灵根 linh căn, 灵石 linh thạch, 丹药 đan dược, 炼丹 luyện đan, 法宝 pháp bảo, 法器 pháp "
        "khí, 飞剑 phi kiếm, 阵法 trận pháp, 符箓 phù lục, 神识 thần thức, 道心 đạo tâm, 心魔 tâm ma, 天劫 thiên kiếp, 雷劫 "
        "lôi kiếp, 飞升 phi thăng, 闭关 bế quan, 出关 xuất quan, 洞府 động phủ, 秘境 bí cảnh, 宗门 tông môn, 宗主 tông chủ, "
        "掌门 chưởng môn, 长老 trưởng lão, 内门 / 外门弟子 đệ tử nội môn / ngoại môn, 散修 tán tu, 魔修 ma tu, 妖兽 yêu thú.\n"
        "- Giọng văn cổ trang, trang trọng vừa phải, câu thoại vẫn dễ hiểu. Không dùng từ hiện đại (OK, xịn, đỉnh, sếp)."),
     "en": "Xianxia cultivation drama: consistent realm names (Qi Refining, Foundation Establishment, Golden Core, "
           "Nascent Soul, Spirit Severing…), sect titles (Sect Master, Elder, Senior/Junior Brother, Master, disciple), "
           "a period tone without modern slang."},

    {"id": "huyen_huyen", "name": "Huyền huyễn – dị giới", "vi": (
        "- Bối cảnh: đại lục, gia tộc, học viện, đế quốc, tu luyện đấu khí / hồn lực (kiểu Đấu Phá, Đấu La).\n"
        "- Xưng hô: ta – ngươi; lão phu, bổn tọa, tiểu tử; trong gia tộc: phụ thân, gia gia (ông nội), tộc trưởng, đại "
        "trưởng lão.\n"
        "- Thuật ngữ: 斗气 đấu khí, 斗者 Đấu Giả, 斗师 Đấu Sư, 大斗师 Đại Đấu Sư, 斗灵 Đấu Linh, 斗王 Đấu Vương, 斗皇 Đấu "
        "Hoàng, 斗宗 Đấu Tông, 斗尊 Đấu Tôn, 斗圣 Đấu Thánh, 斗帝 Đấu Đế; 武魂 võ hồn, 魂力 hồn lực, 魂环 hồn hoàn, 魂师 hồn "
        "sư, 魂骨 hồn cốt; 血脉 huyết mạch, 魔兽 ma thú, 魔核 ma hạch, 异火 dị hỏa, 炼药师 luyện dược sư, 斗技 đấu kỹ, 功法 "
        "công pháp, 天才 thiên tài, 废物 phế vật, 退婚 từ hôn, 家族 gia tộc, 学院 học viện, 大陆 đại lục.\n"
        "- Giọng văn hào hùng; câu khiêu khích / khinh thường giữ đúng độ gắt của câu gốc."),
     "en": "Eastern fantasy (battle-qi / spirit-power worlds): consistent rank names, clan and academy titles, a heroic "
           "tone; keep taunts as sharp as the source."},

    {"id": "he_thong", "name": "Hệ thống – game hoá", "vi": (
        "- Phim có \"hệ thống\" nói chuyện với nhân vật chính (系统流).\n"
        "- Lời HỆ THỐNG: máy móc, ngắn, trang trọng, giữ đúng cấu trúc thông báo: 叮！ -> Đinh!; 恭喜宿主… -> Chúc mừng ký "
        "chủ…; 获得… -> nhận được…; 任务发布 -> Phát hành nhiệm vụ; 任务完成 -> Hoàn thành nhiệm vụ; 任务失败 -> Nhiệm vụ "
        "thất bại; 奖励 -> Phần thưởng; 惩罚 -> Trừng phạt; 倒计时 -> đếm ngược.\n"
        "- Thuật ngữ: 系统 Hệ thống, 宿主 ký chủ, 积分 điểm tích lũy, 签到 điểm danh, 抽奖 rút thưởng, 新手大礼包 gói quà tân "
        "thủ, 属性面板 bảng thuộc tính, 等级 cấp độ, 经验值 điểm kinh nghiệm, 技能 kỹ năng, 商城 cửa hàng hệ thống, 暴击 bạo "
        "kích, …倍返还 hoàn trả gấp … lần, 解锁 mở khóa, 激活 kích hoạt, 绑定 liên kết.\n"
        "- Số cấp, số điểm, số lần giữ đúng con số.\n"
        "- Nhân vật chính nói với hệ thống trong đầu: xưng hô theo bối cảnh phim (cổ trang: ta; hiện đại: tôi)."),
     "en": "\"System\" genre: the System speaks in short, formal, game-like notices (\"Ding! Congratulations, host…\", "
           "mission issued / completed / failed, reward, penalty, points, check-in, lucky draw, level, attributes); keep "
           "every number exact."},

    {"id": "hoat_hinh", "name": "Hoạt hình 3D Trung Quốc (donghua)", "vi": (
        "- Phần lớn là tu tiên / huyền huyễn / võ hiệp: xưng hô cổ trang (ta – ngươi, sư huynh, tiền bối…), cảnh giới và "
        "chiêu thức Hán Việt.\n"
        "- Tên nhân vật, chiêu thức, pháp bảo: Hán Việt; phim đã có tên Việt phổ biến (Tiêu Viêm, Đường Tam, Thạch Hạo, Hàn "
        "Lập, Vương Lâm…) thì dùng đúng tên đó.\n"
        "- Câu thoại sinh động nhưng sát nghĩa; tiếng hô chiêu thức dịch gọn, mạnh, đúng tên chiêu (佛怒火莲 -> Phật Nộ Hỏa "
        "Liên)."),
     "en": "Chinese 3D animation (donghua), mostly xianxia / fantasy / wuxia: period forms of address, consistent realm "
           "and technique names, lively but faithful lines."},

    {"id": "kiem_hiep", "name": "Kiếm hiệp – võ hiệp", "vi": (
        "- Xưng hô: tại hạ – các hạ, huynh đài, cô nương, công tử, đại hiệp, thiếu hiệp, nữ hiệp, tiền bối – vãn bối, lão "
        "phu, sư phụ – đồ nhi; với kẻ thù: ngươi, lão tặc.\n"
        "- Thuật ngữ: 江湖 giang hồ, 武林 võ lâm, 盟主 minh chủ, 掌门 chưởng môn, 帮主 bang chủ, 门派 môn phái, 名门正派 danh "
        "môn chính phái, 魔教 ma giáo, 内力 nội lực, 内功 nội công, 轻功 khinh công, 点穴 điểm huyệt, 剑法 kiếm pháp, 掌法 "
        "chưởng pháp, 秘籍 bí kíp, 走火入魔 tẩu hỏa nhập ma, 镖局 tiêu cục, 客栈 khách điếm, 小二 tiểu nhị, 银子 bạc.\n"
        "- Giọng văn hào sảng, nghĩa khí; không dùng từ hiện đại."),
     "en": "Wuxia martial-arts drama: jianghu terms (martial world, sect, alliance leader, inner force, lightness skill), "
           "courteous period address, chivalrous tone, no modern slang."},

    {"id": "co_trang", "name": "Cổ trang chung – chính kịch, ngôn tình cổ trang", "vi": (
        "- Xưng hô: ta – ngươi; chàng – thiếp (vợ chồng, tình nhân); phụ thân – mẫu thân; huynh – muội; công tử, cô "
        "nương, tiểu thư, lão gia, phu nhân; quan lại: đại nhân, hạ quan, bản quan.\n"
        "- Tên người, địa danh, chức quan Hán Việt: 丞相 thừa tướng, 将军 tướng quân, 王爷 vương gia, 侯爷 hầu gia, 县令 huyện "
        "lệnh, 大理寺 Đại Lý Tự.\n"
        "- Giọng văn cổ trang dễ hiểu; không dùng từ hiện đại."),
     "en": "Period drama (historical / romance): period forms of address and official titles, a formal yet easy tone, "
           "no modern slang."},

    {"id": "cung_dau", "name": "Cung đấu – cung đình", "vi": (
        "- Hoàng đế tự xưng 朕 trẫm; gọi hoàng đế: 皇上 hoàng thượng, 陛下 bệ hạ.\n"
        "- Phi tần: tự xưng 本宫 bổn cung (với người dưới), 臣妾 thần thiếp (với hoàng đế); 皇后 hoàng hậu, 太后 thái hậu, 贵妃 "
        "quý phi, 娘娘 nương nương, 小主 tiểu chủ.\n"
        "- Cung nữ / thái giám: 奴婢 nô tỳ, 奴才 nô tài, 公公 công công; quan: 微臣 vi thần, 爱卿 ái khanh.\n"
        "- Hoàng tộc: 王爷 vương gia, 本王 bổn vương, 太子 thái tử, 皇子 hoàng tử, 公主 công chúa; triều Thanh: 阿哥 a ca, 格格 "
        "cách cách, 福晋 phúc tấn.\n"
        "- Thuật ngữ: 圣旨 thánh chỉ, 懿旨 ý chỉ, 冷宫 lãnh cung, 侍寝 thị tẩm, 翻牌子 lật thẻ bài, 请安 thỉnh an, 赐死 ban chết.\n"
        "- Giọng văn trang nghiêm; lời mỉa mai, ngầm ý giữ nguyên độ sâu cay, không nói thẳng hơn câu gốc."),
     "en": "Palace intrigue drama: imperial forms of address (the emperor's royal \"We\", Your Majesty, consorts, "
           "eunuchs, maids), court terms; keep veiled sarcasm as subtle as the source."},

    {"id": "trach_dau", "name": "Trạch đấu – gia đấu – điền văn", "vi": (
        "- Xưng hô: phụ thân, mẫu thân, tổ mẫu, đích mẫu; tiểu thư, thiếu gia, lão gia, phu nhân, di nương; con cái tự xưng "
        "con / nữ nhi với cha mẹ; ta – ngươi với người ngang hàng / người dưới.\n"
        "- Thuật ngữ: 嫡女 đích nữ, 庶女 thứ nữ, 嫡母 đích mẫu, 姨娘 di nương, 嬷嬷 ma ma, 丫鬟 nha hoàn, 侯府 hầu phủ, 相府 "
        "tướng phủ, 世子 thế tử, 嫁妆 của hồi môn, 聘礼 sính lễ, 和离 hòa ly, 休书 hưu thư, 正妻 chính thê, 妾 thiếp, 铺子 "
        "cửa tiệm.\n"
        "- Giọng văn mềm mại, tinh tế; câu đấu khẩu giữ đúng ý châm chọc."),
     "en": "Household intrigue / family saga set in ancient times: legitimate vs concubine-born daughters, consorts, "
           "matrons, maids, dowry; refined tone, keep the barbs."},

    {"id": "xuyen_khong", "name": "Xuyên không – trọng sinh", "vi": (
        "- Nhân vật hiện đại xuyên về cổ đại (hoặc ngược lại): lời nhân vật chính dùng từ hiện đại đúng như câu gốc; người "
        "cổ đại nói cổ trang (ta – ngươi).\n"
        "- Trọng sinh (sống lại): 重生 trọng sinh, 前世 / 上一世 kiếp trước, 这一世 kiếp này, 复仇 báo thù.\n"
        "- Độc thoại nội tâm giữ cùng cách xưng (ta / tôi) suốt phim."),
     "en": "Time travel / rebirth: the modern-born lead may use modern words exactly as in the source, period characters "
           "speak in period style; past life / this life / revenge terms consistent."},

    # ---------------------------------------------------------------- hiện đại
    {"id": "tong_tai", "name": "Tổng tài – hào môn – đô thị", "vi": (
        "- Xưng hô: yêu nhau / vợ chồng anh – em; xa cách / đối đầu tôi – cô, tôi – anh; cấp trên: Tổng giám đốc (总裁, "
        "姓+总: 陆总 -> Lục tổng / Tổng giám đốc Lục), Chủ tịch (董事长), sếp; 姓+少 (顾少) -> Cố thiếu; nhà giàu: thiếu gia, "
        "đại tiểu thư, thiếu phu nhân, lão phu nhân; 太太 -> phu nhân / bà theo ngữ cảnh.\n"
        "- Thuật ngữ: 集团 tập đoàn, 合同 hợp đồng, 契约婚姻 hôn nhân hợp đồng, 豪门 hào môn, 股份 cổ phần, 董事会 hội đồng "
        "quản trị, 继承人 người thừa kế, 助理 trợ lý, 秘书 thư ký.\n"
        "- Giọng văn hiện đại, tự nhiên; câu \"bá đạo\" giữ đúng khí chất, không làm quá lên."),
     "en": "CEO / rich-family urban romance: modern address that fits each relationship (lovers vs strangers vs boss), "
           "corporate terms; keep the domineering lines as they are."},

    {"id": "hien_dai", "name": "Hiện đại – ngôn tình – đời thường (chung)", "vi": (
        "- Xưng hô theo quan hệ: anh – em (yêu nhau, anh chị em), tôi – cậu / tớ – cậu (bạn bè), bố / mẹ – con, ông / bà – "
        "cháu, chú / cô – cháu; người lạ lịch sự: anh, chị, bác.\n"
        "- Tên người Hán Việt; thương hiệu, ứng dụng: tên quốc tế (微信 WeChat, 支付宝 Alipay, 抖音 Douyin).\n"
        "- Giọng văn tự nhiên đời thường, đúng sắc thái (dỗi, trêu, cãi nhau)."),
     "en": "Modern drama / romance / slice of life: natural everyday speech, forms of address that fit each "
           "relationship, international brand names."},

    {"id": "phim_ngan", "name": "Phim ngắn – nghịch tập – vả mặt", "vi": (
        "- Kiểu phim ngắn (短剧): chiến thần / long vương / thần y / con rể ở rể / tỷ phú giấu thân phận bị coi thường rồi "
        "lật ngược thế cờ.\n"
        "- Thuật ngữ: 战神 Chiến Thần, 龙王 Long Vương, 赘婿 / 上门女婿 con rể ở rể, 神医 thần y, 首富 người giàu nhất, 大小姐 "
        "đại tiểu thư, 少主 thiếu chủ, 退婚 từ hôn, 打脸 vả mặt, 废物 đồ phế vật, 有眼不识泰山 có mắt không nhìn thấy Thái "
        "Sơn, 跪下 quỳ xuống.\n"
        "- Xưng hô: kẻ khinh người -> mày – tao / ngươi; khi thân phận bị lộ -> ngài, thưa…; thuộc hạ với chủ -> thuộc hạ – "
        "chủ nhân / thiếu chủ.\n"
        "- Câu ngắn, dứt khoát, gắt đúng độ của câu gốc; không làm nhẹ đi, không phóng đại thêm."),
     "en": "Short-drama revenge / face-slapping plots (war god, dragon king, live-in son-in-law, hidden billionaire): "
           "short punchy lines exactly as harsh as the source, no softening, no exaggeration."},

    {"id": "hoc_duong", "name": "Thanh xuân – học đường", "vi": (
        "- Xưng hô: bạn học tớ – cậu (câu gốc suồng sã, đùa thân: tao – mày); 学长 / 学姐 đàn anh / đàn chị, 学弟 / 学妹 đàn "
        "em; giáo viên: thầy / cô – em.\n"
        "- Thuật ngữ: 班长 lớp trưởng, 班主任 giáo viên chủ nhiệm, 高考 thi đại học, 校草 nam thần của trường, 校花 hoa khôi, "
        "社团 câu lạc bộ, 宿舍 ký túc xá, 食堂 căng tin.\n"
        "- Giọng văn trẻ trung, trong sáng; tiếng lóng tuổi teen đổi sang cách nói tương đương của học sinh Việt."),
     "en": "Youth / campus drama: casual peer speech, seniors and juniors, teachers; teen slang rendered with natural "
           "equivalents."},

    {"id": "gia_dinh", "name": "Gia đình – đời thường", "vi": (
        "- Xưng hô gia đình Việt: bố – con, mẹ – con, ông – cháu, bà – cháu, anh – em; 婆婆 mẹ chồng, 公公 bố chồng, 丈母娘 "
        "mẹ vợ, 岳父 bố vợ, 媳妇 con dâu / vợ, 女婿 con rể, 老公 chồng (gọi: anh / ông xã), 老婆 vợ (gọi: em / bà xã), 亲家 "
        "thông gia, 小姑子 em gái chồng.\n"
        "- Giọng văn gần gũi, tự nhiên như người Việt nói trong nhà."),
     "en": "Family drama: natural family forms of address (in-laws, spouses, grandparents), warm everyday tone."},

    {"id": "trinh_tham", "name": "Trinh thám – hình sự – phá án", "vi": (
        "- Thuật ngữ: 刑警 cảnh sát hình sự, 警官 cảnh sát, 队长 đội trưởng, 局长 cục trưởng, 法医 pháp y, 嫌疑人 nghi phạm, "
        "凶手 hung thủ, 死者 nạn nhân, 案发现场 hiện trường vụ án, 不在场证明 chứng cứ ngoại phạm, 尸检 khám nghiệm tử thi, "
        "指纹 dấu vân tay, 监控 camera giám sát, 口供 lời khai, 审讯 thẩm vấn, 立案 lập án, 结案 khép hồ sơ.\n"
        "- Giữ CHÍNH XÁC mọi chi tiết manh mối: giờ, ngày, số, tên, địa điểm, đồ vật – không lược, không đổi.\n"
        "- Thẩm vấn: cảnh sát gọi nghi phạm anh / chị / cô / cậu (gắt: anh ta, hắn), nghi phạm xưng tôi.\n"
        "- Giọng văn rõ ràng, chặt chẽ."),
     "en": "Crime / detective drama: precise police and forensic terms; keep every clue detail (times, numbers, names, "
           "places, objects) exact."},

    {"id": "kinh_di", "name": "Kinh dị – linh dị", "vi": (
        "- Thuật ngữ: 鬼 ma / quỷ, 厉鬼 lệ quỷ, 冤魂 oan hồn, 鬼魂 hồn ma, 阴间 cõi âm, 阳间 dương gian, 阴气 âm khí, 道士 đạo "
        "sĩ, 天师 thiên sư, 符 bùa, 驱鬼 trừ tà, 僵尸 cương thi, 附身 nhập xác, 招魂 gọi hồn.\n"
        "- Giữ không khí rùng rợn: câu ngắn, ngắt quãng đúng như câu gốc; tiếng la hét, thở dốc dịch đúng tiếng cảm thán."),
     "en": "Horror / supernatural: ghost and exorcism terms, a tense atmosphere, short broken lines as in the source."},

    {"id": "mat_the", "name": "Mạt thế – zombie – sinh tồn", "vi": (
        "- Thuật ngữ: 末世 tận thế, 丧尸 xác sống (zombie), 异能 dị năng, 异能者 dị năng giả, 晶核 tinh hạch, 幸存者 người sống "
        "sót, 基地 căn cứ, 物资 vật tư, 变异 biến dị, 进化 tiến hóa, 囤货 tích trữ hàng, 避难所 nơi trú ẩn.\n"
        "- Xưng hô hiện đại (tôi – anh / cô; đối đầu: tao – mày); giọng văn căng thẳng, dứt khoát."),
     "en": "Apocalypse / zombie survival: consistent terms (zombies, abilities, crystal cores, base, supplies, mutation), "
           "tense direct speech."},

    {"id": "khoa_huyen", "name": "Khoa học viễn tưởng", "vi": (
        "- Thuật ngữ: 星际 liên hành tinh, 联邦 Liên bang, 帝国 Đế quốc, 星舰 chiến hạm vũ trụ, 机甲 cơ giáp, 舰长 hạm trưởng, "
        "跃迁 bước nhảy không gian, 外星人 người ngoài hành tinh, 虫族 Trùng tộc, 人工智能 trí tuệ nhân tạo (AI), 基因 gen, "
        "精神力 tinh thần lực, 能量 năng lượng.\n"
        "- Thuật ngữ khoa học dịch theo tiếng Việt phổ thông; số liệu, đơn vị giữ đúng."),
     "en": "Science fiction: consistent sci-fi terms (federation, starship, mecha, warp jump, aliens, AI, genes), exact "
           "numbers and units."},

    {"id": "vong_du", "name": "Võng du – game – thể thao điện tử", "vi": (
        "- Thuật ngữ game: 玩家 người chơi, 副本 phó bản, BOSS giữ nguyên, 装备 trang bị, 等级 cấp, 升级 lên cấp, 公会 bang "
        "hội, 大神 đại thần, 菜鸟 gà mờ, 外挂 hack, 氪金 nạp tiền.\n"
        "- Thể thao điện tử: 战队 đội tuyển, 上分 leo rank, 排位 đấu xếp hạng, 团战 giao tranh tổng, 打野 đi rừng, 中单 đường "
        "giữa, 上单 đường trên, 下路 đường dưới, 辅助 hỗ trợ, 五杀 pentakill, 翻盘 lật kèo, MVP giữ nguyên.\n"
        "- Từ tiếng Anh game thủ Việt quen dùng thì giữ (rank, team, buff, nerf, skill)."),
     "en": "Online game / esports: standard gamer terms (dungeon, boss, gear, guild, ranked, team fight, jungle, mid, "
           "support, pentakill, comeback)."},

    {"id": "quan_su", "name": "Quân sự – kháng chiến – lịch sử cận đại", "vi": (
        "- Xưng hô: 首长 thủ trưởng, 同志 đồng chí; 报告！ -> Báo cáo!; 是！ -> Rõ!; 班长 tiểu đội trưởng, 排长 trung đội "
        "trưởng, 连长 đại đội trưởng, 营长 tiểu đoàn trưởng, 团长 trung đoàn trưởng, 师长 sư đoàn trưởng, 军长 quân đoàn "
        "trưởng, 司令 tư lệnh, 政委 chính ủy, 参谋长 tham mưu trưởng.\n"
        "- Thuật ngữ quân sự chuẩn: 冲锋 xung phong, 撤退 rút lui, 掩护 yểm trợ, 阵地 trận địa, 弹药 đạn dược.\n"
        "- Giọng văn dứt khoát; mệnh lệnh ngắn gọn đúng kiểu quân đội."),
     "en": "Military / war drama: correct ranks and unit titles, \"Report!\" / \"Yes, sir!\", crisp military orders."},

    {"id": "hai", "name": "Hài – meme mạng", "vi": (
        "- Giữ cái buồn cười: chơi chữ / meme đổi sang cách nói Việt tương đương cùng nghĩa; KHÔNG thêm câu đùa mới.\n"
        "- Lóng mạng: 我去 Trời đất; 绝绝子 đỉnh của chóp; 破防 sập nguồn; 摆烂 buông xuôi; 内卷 cày cuốc; 躺平 nằm thẳng; 吃瓜 "
        "hóng hớt; 社死 quê độ; 666 đỉnh; YYDS đỉnh nhất.\n"
        "- Bạn bè: tớ – cậu (đùa thân, cãi nhau: tao – mày)."),
     "en": "Comedy / internet memes: keep the joke working with natural equivalent wording; never add new jokes."},

    {"id": "thieu_nhi", "name": "Hoạt hình thiếu nhi", "vi": (
        "- Câu đơn giản, trong sáng, dễ hiểu với trẻ em; không dùng từ thô tục.\n"
        "- Xưng hô: bạn – tớ / mình; con – bố mẹ; con vật / đồ vật biết nói xưng theo tuổi trong phim.\n"
        "- Nhân vật đã có tên Việt chính thức thì dùng tên đó; tiếng kêu, tiếng động dịch đúng như câu gốc."),
     "en": "Children's animation: simple, clean, friendly language; official localized character names."},

    {"id": "review", "name": "Review – tóm tắt phim – kể chuyện", "vi": (
        "- Lời người kể (ngôi thứ ba): mạch lạc, lôi cuốn, câu gọn; đúng thứ tự sự kiện, không thêm bình luận ngoài câu "
        "gốc.\n"
        "- Tên nhân vật Hán Việt; lời trích thoại của nhân vật trong phim dịch theo xưng hô của nhân vật đó.\n"
        "- Người kể nói với khán giả (大家, 你们) -> \"các bạn\"."),
     "en": "Movie recap / storytelling: clear engaging third-person narration in event order, no commentary beyond the "
           "source."},

    {"id": "vlog", "name": "Vlog – livestream – bán hàng trực tiếp", "vi": (
        "- Người nói xưng mình / tôi, gọi khán giả các bạn / mọi người; 家人们 -> cả nhà ơi; 宝宝们 -> các bạn; 点赞关注 -> "
        "like và theo dõi; 下单 -> đặt hàng; 链接 -> link.\n"
        "- Giữ đúng giá tiền, số lượng, tên món ăn / sản phẩm; không đổi đơn vị tiền.\n"
        "- Giọng thân thiện, tự nhiên."),
     "en": "Vlog / livestream / live selling: friendly first person to the viewers; prices, quantities and product "
           "names exact."},

    {"id": "tai_lieu", "name": "Tài liệu – hướng dẫn – khoa học", "vi": (
        "- Giọng văn trang trọng, chính xác; thuật ngữ chuyên ngành theo tiếng Việt chuẩn.\n"
        "- Số liệu, đơn vị, tên riêng, thông số giữ chính xác tuyệt đối.\n"
        "- Hướng dẫn: câu rõ ràng, đúng thứ tự các bước."),
     "en": "Documentary / tutorial / science: formal precise wording, standard terminology, exact numbers and units."},
]

_BY_ID = {g["id"]: g for g in GENRES}


def choices() -> list[tuple[str, str]]:
    """(mã, tên) cho ô chọn thể loại."""
    return [(g["id"], g["name"]) for g in GENRES]


def default_text(gid: str, target: str = "vi") -> str:
    g = _BY_ID.get(gid) or _BY_ID["auto"]
    return g["vi"] if target == "vi" else g["en"]


def edited(settings: dict | None) -> dict[str, str]:
    """{mã: chữ} các form bạn đã sửa (Cài đặt `genre_forms`)."""
    out = {}
    for x in (settings or {}).get("genre_forms") or []:
        if isinstance(x, dict) and x.get("id") in _BY_ID and str(x.get("text") or "").strip():
            out[x["id"]] = str(x["text"]).strip()
    return out


def to_setting(own: dict[str, str]) -> list[dict]:
    return [{"id": k, "text": v} for k, v in own.items() if k in _BY_ID and v.strip()]


def form(gid: str | None, target: str = "vi", settings: dict | None = None) -> str:
    """Form của thể loại cho tiếng đích (tiếng Việt: bản bạn đã sửa nếu có)."""
    gid = gid if gid in _BY_ID else "auto"
    if target == "vi":
        own = edited(settings).get(gid)
        if own:
            return own
    return default_text(gid, target)


def name(gid: str | None) -> str:
    return (_BY_ID.get(gid or "") or _BY_ID["auto"])["name"]
