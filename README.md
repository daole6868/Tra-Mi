# Subvideo – Dịch & lồng tiếng video

Phần mềm chạy trên máy tính: có cửa sổ riêng, giao diện tiếng Việt, không cần trình duyệt.
Dùng để dịch video tiếng Trung sang tiếng Việt (hoặc Anh / Thái / Nhật / Hàn), xoá phụ đề cũ, đè phụ đề mới, lồng
tiếng nhiều giọng và chỉnh sửa video trước khi đăng Facebook / YouTube / TikTok.
(Tên cũ: TraMi – mã nguồn bên trong vẫn là gói `trami`, file chạy `TraMi.py`.)

---

## 1. Cài đặt (Windows)

| Bước | Làm gì |
|---|---|
| 1 | Giải nén zip vào thư mục ngắn, không dấu, ví dụ `D:\Subvideo` |
| 2 | Nháy đúp **`CaiDat_Windows.bat`**. File này tự cài Python 3.11 nếu máy chưa có, cài thư viện, tải FFmpeg và tạo biểu tượng **Subvideo** trên Desktop |
| 3 | Khi được hỏi *"Cài AI nâng cao?"*, bấm **Y**. Bước này tự nhận card NVIDIA và cài PyTorch bản GPU, rồi cài các gói tách giọng, SenseVoice, phân người nói, VieNeu-TTS, LaMa. Cần thêm khoảng 5–8 GB |
| 4 | Mở **biểu tượng Subvideo** trên Desktop, hoặc chạy `ChayTool_Windows.bat` (tạo lại biểu tượng: `TaoBieuTuong.bat`) |

Có thể cài AI nâng cao sau, hoặc cài lại, bằng `CaiDat_AI_NangCao.bat`.
Các mô hình AI (khoảng 3–5 GB) tự tải về ở lần dùng đầu tiên.

**Các lệnh cài tay** (PowerShell, trong thư mục phần mềm). Phải cài đúng thứ tự:

```powershell
py -3.11 -m venv .venv
.venv\Scripts\python -m pip install --upgrade pip
.venv\Scripts\python -m pip install -r requirements.txt

# 1) PyTorch bản GPU (máy có card NVIDIA) – cài CẢ 3 gói cùng lúc
.venv\Scripts\python -m pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu128

# 2) Khoá phiên bản PyTorch để các gói AI không thay nó bằng bản CPU
.venv\Scripts\python -m pip freeze | Select-String "^torch(vision|audio)?==" | ForEach-Object { $_.Line } | Set-Content torch-constraints.txt

# 3) Các gói AI
.venv\Scripts\python -m pip install -r requirements-ai.txt -c torch-constraints.txt
.venv\Scripts\python -m pip install nvidia-cublas-cu12 "nvidia-cudnn-cu12>=9,<10"

# 4) Kiểm tra: phải thấy "+cu128 ... GPU: True"
.venv\Scripts\python -c "import torch; print(torch.__version__, 'GPU:', torch.cuda.is_available())"

# Chạy tool
.venv\Scripts\python TraMi.py
```

Máy không có card NVIDIA: ở bước 1, bỏ phần `--index-url ...` đi.

**Lỡ bị PyTorch bản CPU** (trang Cài đặt báo "PyTorch: CPU ⚠"): chạy lệnh sau rồi kiểm tra lại như bước 4.
```powershell
.venv\Scripts\python -m pip install --force-reinstall --no-deps torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu128
```

**Linux / macOS:** chạy `./install.sh --ai`, sau đó `./run.sh`.

### Đóng gói thành Subvideo.exe
Cài xong (kể cả AI nâng cao) thì chạy **`DongGoi_EXE.bat`** (15–40 phút, cần ~10–15 GB trống). Kết quả là thư mục
`dist\Subvideo\` chứa **Subvideo.exe** + biểu tượng Subvideo trên Desktop / Start menu.
- Gồm ĐỦ thư viện đã cài trong `.venv` (PyTorch GPU, nhận dạng, tách giọng, đọc giọng…), KHÔNG gồm mô hình AI – mô hình
  vẫn tải ở tab Tài liệu.
- Trên máy đóng gói, bản exe dùng chung thư mục `data` (dự án, cài đặt, mô hình đã tải) với bản chạy bằng Python
  (file `dist\Subvideo\datadir.txt`).
- Mang sang máy khác: nén cả thư mục `dist\Subvideo` (~5–8 GB vì có PyTorch). Máy đó chỉ cần driver NVIDIA, không cần
  Python; dữ liệu lưu ở `%LOCALAPPDATA%\Subvideo` (hoặc thư mục `data` đặt cạnh Subvideo.exe), mô hình tải lại ở tab
  Tài liệu.
- Gửi kèm mô hình đã tải (người nhận khỏi tải lại): chạy **`DongGoi_MoHinh.bat`** sau `DongGoi_EXE.bat` -> thư mục
  `dist\Subvideo_GuiBan\` = phần mềm + `data\models` (mô hình tách giọng, nhận dạng, đọc giọng, Qwen3, Hy-MT2… đã tải
  trên máy bạn). KHÔNG chép cài đặt (API key), tài khoản YouTube / Facebook, cookie, hồ sơ Chrome, giọng ghi âm, dự án.
  Đừng tự gửi cả thư mục `data` của bạn – trong đó có các thông tin riêng này.
- Sửa mã thì phải chạy lại `DongGoi_EXE.bat`.

---

## 2. Dùng nhanh

1. Ở **Trang chủ**, kéo thả video, bấm chọn file, hoặc dán link Douyin / Bilibili / YouTube / TikTok. Dán được cả dòng chia sẻ của app (`【tên】 https://b23.tv/…`).
   - Bilibili chưa đăng nhập chỉ tải được 480p: bấm **Đăng nhập Bilibili (quét mã QR)** rồi quét bằng app Bilibili trên điện thoại để tải 720p / 1080p.
   - Douyin và trang cần đăng nhập: chọn trình duyệt ở **Trang khác lấy đăng nhập từ** (Firefox chạy ổn nhất) hoặc chọn file cookies.txt.
   - Tải báo lỗi (HTTP 412, Unable to extract…): bấm **Cập nhật trình tải** rồi mở lại tool.
   - **Tải về & dịch tự động**: dán 1 hoặc nhiều link (mỗi dòng 1 link) rồi bấm nút này – mỗi link 1 dự án, tool tự tải →
     nghe → dịch → tách câu dài → lồng tiếng (nếu bật) rồi dừng trước bước xuất. Video sau được tải trước trong lúc video
     trước đang làm. Link đã tải rồi thì mở lại dự án cũ, không tải lại (lần trước tải chưa xong thì tự tải lại vào
     dự án đó; video nhiều phần `?p=2`, `?p=3` = mỗi phần 1 dự án). Thẻ dự án ở Trang chủ ghi trạng thái
     (Đang tải… / Đang làm… / Chờ kiểm tra & xuất / ✓ Đã xuất / ⚠ Lỗi). **Chỉ tải về** = chỉ tải, không dịch.
   - **Thiết lập…**: 1 bộ thiết lập tự động dùng chung cho nút trên, tab Tự động và Xử lý hàng loạt (đổi ở đâu cũng là 1).
2. Mở tab **Tự động** và chọn:
   - **Nguồn phụ đề**:
     - *Nhận dạng giọng nói*: dùng khi video chưa có chữ.
     - *OCR phụ đề cứng*: dùng khi video đã in sẵn chữ Trung. Cách này chính xác nhất.
   - **Công cụ dịch**: AI dịch hay hơn Google, ví dụ DeepSeek rẻ và giỏi tiếng Trung. Nhập API key ở trang **Cài đặt**. Hoặc chọn **Hy-MT2**: mô hình chuyên dịch chạy ngay trên máy, miễn phí, không cần mạng (tải 1 lần ~3,6 GB ở tab **Tài liệu**).
   - **Dịch sang**: tiếng Việt (mặc định), Anh, Thái, Nhật hoặc Hàn. Bản dịch, phụ đề ghép vào video và giọng lồng
     tiếng đều theo tiếng này (mỗi dự án chọn riêng; Edge và OmniVoice đọc được cả 5 tiếng, VieNeu chỉ đọc tiếng Việt).
   - **Thể loại**: quyết định cách xưng hô, ví dụ cổ trang dùng ta – ngươi, huynh – đệ.
   - **Phụ đề Trung cũ**: chọn *Xoá sạch bằng AI*.
   - **Lồng tiếng**: bật nếu muốn thêm giọng Việt (tắt thì chỉ dịch phụ đề, không đọc giọng).
   - **Giọng lồng**: 1 giọng cho mọi nhân vật, *Tự gán giọng nam / nữ*, hoặc để mặc định = giọng chọn ở tab Lồng tiếng mục 2.
   - **Vừa tách vừa dịch**: nghe tới đâu dịch tới đó (đủ "Số câu mỗi lần gửi" của công cụ dịch là gửi), câu dịch hiện
     ngay ở tab Phụ đề, Edge đọc trước giọng các câu đã dịch – nhanh hơn chạy lần lượt từng bước.
3. Bấm **Bắt đầu dịch tự động**: tool làm nghe → dịch → lồng tiếng (nếu bật) rồi **dừng** (không tự xuất). Kiểm tra phụ đề / giọng xong bấm **Xuất video** (nút trên cùng hoặc tab Xuất) để xoá phụ đề cũ và ghép video.
   Thanh tiến độ ở cuối cửa sổ hiện % hoàn thành và thời gian còn lại; mọi việc đang chạy xem ở mục **Việc đang làm**
   (thanh bên trái). Nhiều dự án chạy được cùng lúc, mỗi lúc 1 việc dùng card đồ hoạ.
4. Soát phụ đề ở tab **Phụ đề**, rồi mở tab **Xuất** để lấy video MP4 và file SRT.

Để dịch hàng loạt, chọn nhiều file cùng lúc ở Trang chủ, hoặc dùng trang **Xử lý hàng loạt** (file + link lẫn nhau, cùng bộ thiết lập tự động). Mặc định mỗi video dừng trước bước xuất (bật *Xuất video luôn* nếu muốn tự xuất).
Muốn dịch nhanh không cần mở giao diện, kéo thả video vào `DichNhanh_KeoTha.bat`.

---

## 3. Các chức năng

| Tab | Chức năng |
|---|---|
| **Tự động** | Chạy trọn gói các bước: tải video → tách giọng → nhận dạng → phân biệt người nói → dịch → lồng tiếng → xoá phụ đề cũ → xuất video |
| **Phụ đề** | Chọn ngay tại đây công cụ dịch, tiếng đích, văn phong, kỹ năng AI rồi bấm *Dịch câu chưa dịch* / *Dịch lại tất cả* / *Dịch câu đã tích*. Cột **Chọn** để tích câu (bấm chữ "Chọn" để tích hết): tích 2–3 câu bị cắt rồi bấm *Gộp*. Bảng sửa trực tiếp câu Trung, câu Việt, thời gian và người nói. Thẻ **Kỹ năng dịch AI**: AI sửa lỗi nghe & nối câu, AI kiểm tra bản dịch, hồ sơ phim. Bấm vào một dòng để tua video tới câu đó; video tự cuộn theo câu đang phát. Gộp, tách, thêm, xoá câu. Dời giờ, tìm & thay, dịch lại 1 câu, dịch các dòng trống. Nhập SRT/ASS. Hàng **Phụ đề**: xuất SRT gốc / đã dịch / song ngữ để dịch ở phần mềm khác, *Nạp SRT đã dịch…* (câu có dấu ♪ tự được đánh dấu bài hát 🎵). Có bảng tên riêng Hán Việt do AI tự điền |
| **Xoá phụ đề** | Khoanh vùng chữ ngay trên video (hoặc trên ảnh chụp khung), được nhiều vùng; kéo 4 chấm trên 4 cạnh để đổi cao / thấp / dài / ngắn, kéo giữa để di chuyển. Tự dò vùng phụ đề. **Xoá sạch**: chỗ bị chữ che được lấp bằng **nền thật lấy từ các khung trước / sau** (lúc chữ đổi câu, máy quay lia – có đo chuyển động), phần chưa từng lộ ra mới để AI LaMa vẽ → nét, không nhoè, không nhấp nháy; nhận chữ mọi màu. **Gỡ logo cố định** (khoanh riêng ở mục "Logo cố định"): tool đo logo trên ~200 khung rồi tính ngược lớp logo trong suốt → nền thật phía sau hiện lại. Cách khác: vá nền, làm mờ (mờ thường / mờ mịn / khảm ô vuông, **bo góc, viền mờ dần, phủ tối**), che dải màu (bo góc), delogo. Xem thử 1 khung; nút **Chạy xoá (cả video)** xoá ngay, trình phát chiếu bản đã xoá và lúc xuất dùng lại |
| **Chỉnh sửa** | Cắt đầu/cuối và xoá nhiều đoạn giữa, xem trên thanh thời gian. **Rút khoảng lặng & cắt bài hát**: tìm chỗ không có lời (hoặc tiếng gốc nhỏ hơn ngưỡng dB, hoặc cả hai) dài từ … đến … giây rồi rút còn … giây (0 = xoá hẳn; phần giữ lại chia đều 2 đầu / sau câu trước / trước câu sau), mẫu *Nhẹ / Vừa / Mạnh*, không cắt đoạn 🎵 / N giây đầu, cuối; nút **Tìm / cắt đoạn bài hát** tìm đúng cả bài (cụm câu 🎵 + nhạc dạo, nhạc kết) để cắt bỏ hết. Bảng xem lại từng đoạn: tích / bỏ tích, *Chỉ tích đoạn dài hơn…*, ▶ *Xem* chỗ nối, *Phát thử bản đã cắt*, rồi *Áp dụng cắt* (Ctrl+Z hoàn tác); đoạn chờ cắt hiện sọc đỏ trên timeline (bấm bật / tắt, kéo mép để chỉnh). **Khung hình**: chọn tỉ lệ **Dọc** (1:1, 3:4, 9:16, 2:3, 3:5, 4:5, 5:7) / **Ngang** (1:1, 4:3, 16:9, 3:2, 5:3, 5:4, 7:5) / Gốc – chung với tab Xuất; phần trống: nền mờ thật / viền đen / nền 1 màu / phóng cho đầy khung; vị trí phim (giữa, lệch lên / xuống để chừa chỗ tiêu đề, tự chỉnh), cỡ phim trong khung; *Hiện vùng bị TikTok / Reels che* + nút *Đưa phụ đề + chữ vào vùng an toàn*. Crop, xoay, lật gương, zoom, tốc độ 0.5–2×. Độ phân giải tới 4K. Chỉnh màu, làm nét, khử nhiễu, mờ dần đầu/cuối |
| **Chữ+Logo** | Thêm chữ theo mẫu: **Tiêu đề cọ vàng**, nền đen bo góc, viền trắng chữ đỏ, bong bóng thoại, gạch chân màu, không nền – viền đậm, chữ vàng viền đen, chữ chạy ngang. Sửa chữ nhiều dòng, font (13 font có đủ tiếng Việt + *Nạp font riêng* .ttf / .otf), cỡ, màu, viền, bóng đổ, nền (nét cọ / hộp / bo góc / bong bóng / gạch chân / hình tròn), nghiêng / xoay, độ rõ, hiệu ứng (hiện dần, trượt vào 4 hướng, chữ chạy). **Logo của bạn**: tải nhiều logo 1 lần (PNG, JPG, WebP, SVG, **GIF động**), bấm đúp hoặc **kéo thả vào video**, nhiều logo cùng lúc. **Logo mẫu** theo tên kênh. Kéo chữ / logo trên video để đặt chỗ, kéo 4 góc để phóng to / thu nhỏ. Mỗi chữ / logo có **1 dòng riêng trên timeline**: kéo thanh để dời lúc hiện, kéo 2 mép để đổi lúc bắt đầu / kết thúc (hít vào đầu phát, mép lớp khác); chuột phải: suốt video / nhân bản / lên trên / xuống dưới / xoá. Cắt video thì chữ / logo tự dời theo |
| **Kiểu chữ** | Font (có **Bangers** kiểu truyện tranh), cỡ, màu, viền, bóng, nền hộp (ôm từng dòng / 1 khối / dải cố định, **bo góc**), vị trí, VIẾT HOA, song ngữ. **Độ dài dòng**: đo chữ thật, chỉ xuống dòng khi câu thật sự dài hơn *Độ dài tối đa của dòng* (% khung); chọn số dòng tối đa, hoặc *thu nhỏ chữ câu đó cho vừa 1 dòng*. **Karaoke**: từng từ đổi màu đúng lúc được đọc (chạy theo giọng lồng tiếng), chọn màu, quét mượt hoặc đổi từng từ, dùng được với mọi font. Xem trước trực tiếp trên video, đúng cỡ và chỗ như video xuất. Kéo phụ đề trên video để đặt chỗ; nền hộp ôm chữ hoặc dải cố định, kéo 4 chấm trên cạnh nền để đổi chiều cao / độ dài. Có sẵn 5 mẫu nhanh, gồm *Karaoke vàng* |
| **Lồng tiếng** | ① **Tách giọng / nhạc nền** (Mel-RoFormer Kim, BS-RoFormer hoặc kết hợp cả hai): giữ nguyên nhạc và hiệu ứng gốc, chỉ thay lời thoại. ② **Mỗi người nói một giọng**: 25 giọng Việt của VieNeu (⭐ giọng tiêu biểu đứng đầu) + 6 giọng thu âm sạch + giọng mẫu của bạn, **23 giọng OmniVoice chọn sẵn** (nam / nữ trẻ, thiếu niên, trung niên, ông bà lão, trẻ con, thì thầm, hoạt hình – không cần file ghi âm), 2 giọng Microsoft, hoặc **nhái giọng nhân vật gốc**. Nghe thử từng giọng. "Tự gán giọng" tự nhận nhân vật giọng nam / nữ để chọn giọng hợp. Số đọc thành chữ, câu đọc hỏng (tiếng bíp, im lặng) tự đọc lại, âm lượng các câu được cân cho đều. ③ Mỗi câu phụ đề là 1 đoạn giọng riêng, ghép đúng lúc nhân vật nói, hiện trên dòng *Lồng tiếng* của timeline (kéo để dời, bấm đúp để nghe); bài hát trong phim được giữ nguyên: lời bài hát tự được đánh dấu 🎵 (nghe ra nốt ngân trên giọng đã tách, AI dịch nhận ra lời hát, file dịch có ♪) – nút *Tự tìm câu hát*, *Tích câu 🎵*, *Đặt / bỏ 🎵 câu đã tích*, *Bỏ lồng / lồng lại câu đã tích*. *Tích câu chưa lồng* tích các câu bị sót để tạo lại giọng. Lồng tiếng Việt tự xoá câu chỉ có tiếng cảm thán đứng lẻ ("A!", "Ừm.", "Ái!"), hoặc bấm *Lọc câu cảm thán* để xoá ngay – khôi phục được. *Xoá câu đã tích* xoá cả phụ đề lẫn giọng lồng của các câu đã tích. Video dài đọc mỗi đợt 1000 câu. **Tốc độ đọc** cho các câu đã tích (mức tối thiểu: câu đang đọc nhanh hơn giữ nguyên) + nút *Về tốc độ gốc*. ④ **Câu đọc dài hơn chỗ trống**: *Tự động* (tăng tốc nhẹ → đọc sớm vào khoảng lặng → tăng tốc thêm → câu sau lùi lại rồi đuổi kịp; nhiều câu dài liền nhau dùng chung 1 tốc độ; không cắt chữ), *Nối tiếp* (đọc hết rồi mới sang câu sau), *Đọc đè* (câu sau vẫn đúng giờ), *Đọc sớm* (lùi câu dài lên trước), *Giữ đúng giờ – cắt bớt* (cách cũ); chọn chung hoặc riêng từng câu, đổi là xếp lại ngay (không đọc lại). Phụ đề tiếng Việt tự hiện theo đúng lúc giọng đọc |
| **Âm thanh** | MỌI chỉnh âm thanh ở 1 chỗ: chọn nguồn tiếng gốc (nguyên bản / bản đã tách), âm lượng từng lớp – tiếng gốc, nhạc nền + hiệu ứng đã tách, giọng nói gốc tiếng Trung (đoạn hát giữ nguyên), giọng lồng tiếng Việt, nhạc thêm (tự nhỏ xuống khi có lời), âm lượng tổng. Mỗi lớp có nút tắt và *Nghe riêng*. Đổi cao độ giọng gốc. Bấm Phát trên video là nghe thay đổi ngay khi kéo thanh; video xuất ra trộn y như vậy. Tải MP3 bản trộn / giọng gốc / nhạc nền |
| **Timeline** (dưới cùng) | Bấm khối để chọn, Ctrl / Shift để chọn nhiều, kéo trên chỗ trống để quét chọn, bấm tên dòng để chọn hết. Bấm chỗ trống dòng Phụ đề rồi *Thêm phụ đề* để thêm câu đúng chỗ. Thanh công cụ đổi theo dòng đang chọn: Phụ đề (thêm, tách, gộp, xoá, dịch lại), Lồng tiếng (tạo lại giọng, tốc độ, cách xếp khi câu dài, bỏ lồng, về đúng chỗ – đoạn đọc đè hiện thành 2 làn, câu đọc sớm / bị lùi có vạch nối), Video (tách tại đầu phát, xoá / khôi phục đoạn, gộp đoạn, **chèn video khác**), Tiếng gốc (tắt / bật tiếng gốc từng đoạn). Áp dụng ngay trên trình phát và khi xuất |
| **Xuất** | MP4 (H.264 CPU / NVIDIA NVENC / H.265), tất cả trong 1 lần encode. Chọn tỉ lệ (chung với tab Chỉnh sửa) + **Xuất thêm tỉ lệ** (vd. bản 16:9 cho YouTube + bản 9:16 cho TikTok trong 1 lần bấm): nút 👁 xem thử từng tỉ lệ trên video, kéo chữ / logo / phụ đề khi đang xem thử = chỉnh riêng cho tỉ lệ đó; khi đăng, Shorts / Reels / TikTok tự dùng bản dọc, YouTube / Facebook video dùng bản ngang. Phụ đề SRT (bản dịch / Trung / song ngữ), ASS, VTT, TXT. Chia video dài thành nhiều phần, tách MP3. **Đăng video** lên YouTube (video thường / Shorts), Facebook (Trang: video / Reels) và TikTok: *AI viết tiêu đề, mô tả, hashtag* từ phụ đề đã dịch, quyền riêng tư, danh mục, hẹn giờ, ảnh bìa (chọn ảnh JPG / PNG / WebP hoặc lấy khung đang xem – tool tự đổi sang JPG ≤ 2 MB đặt cạnh video; Chrome điền sẵn tự gắn ở YouTube / TikTok, còn Facebook và Tự tải lên thì chỉ chỗ đặt + mở sẵn thư mục chứa ảnh), lịch sử *Đã đăng* có link. 4 cách đăng (thêm tài khoản ở Cài đặt → Đăng video): **Zernio** (đăng Công khai ngay, 2 tài khoản đầu miễn phí), **API riêng** của bạn (YouTube: dự án Google Cloud – chưa được Google duyệt thì video bị khoá Riêng tư; Facebook: app Meta, chỉ đăng lên Trang), **Mở Chrome & điền sẵn** (miễn phí, cả TikTok: tool mở Chrome với hồ sơ riêng – đăng nhập tay 1 lần –, gắn video, điền tiêu đề / mô tả / hashtag, chọn khán giả / chế độ hiển thị rồi viền cam nút Đăng để bạn tự bấm; cần `pip install playwright`), **Tự tải lên** (chép sẵn nội dung, mở trang đăng, bạn kéo file vào). Mạng rớt giữa lúc tải lên thì tự gửi tiếp từ chỗ dừng |

**Ngôn ngữ đích** (tab Tự động → *Dịch sang*): Việt, Anh, Thái, Nhật, Hàn. AI dịch theo đúng tiếng đó (tên riêng
phiên âm kiểu tiếng đích, kính ngữ Nhật / Hàn, độ dài câu vừa thời gian nhân vật nói), Google / Hy-MT2 cũng dịch đúng
tiếng. Phụ đề tiếng Thái / Nhật / Hàn tự dùng font có đủ chữ và ngắt dòng đúng kiểu chữ viết liền. Giọng đọc tự đổi theo
tiếng của từng câu: giọng Microsoft tiếng Anh (6 giọng), Thái, Nhật, Hàn (mỗi tiếng 1 nam 1 nữ); OmniVoice đọc đúng tiếng;
VieNeu chỉ đọc tiếng Việt nên câu tiếng khác tự đọc bằng Microsoft. Nạp file SRT đã dịch tiếng khác thì dự án tự đổi
*Dịch sang* theo.

**Khớp thoại** (2.0.96): sau khi nghe, đầu / cuối mỗi câu được **bám theo giọng đã tách** (nhìn độ to giọng mỗi
0,01 s – như sóng âm dòng *Tiếng gốc* trên timeline; dòng *Lồng tiếng* cũng vẽ mịn 0,01 s từ 2.0.97) để phụ đề hiện đúng lúc nhân vật bắt đầu / thôi nói; mốc từng chữ
được giữ lại (gộp / tách / kéo câu vẫn đúng). Dự án cũ: tab Phụ đề → Công cụ → *Sửa câu* → **Căn mép theo giọng** (bật
*đo lại mốc từng chữ* nếu dự án nghe trước 2.0.96). Câu chỉ có mốc ước lượng có viền nét đứt vàng trên timeline – nên
nghe lại. **Giờ hiện** (tab Phụ đề → Công cụ): phụ đề hiện theo giọng lồng – hiện trước 0,1 s, giữ thêm 0,3 s, ít nhất
1 s, nối khoảng hở < 0,3 s (chỉ đổi lúc hiện chữ, không đổi giờ nói / chỗ đặt giọng). **Nghỉ đúng chỗ ngắt hơi** (tab
Lồng tiếng mục 3): nhân vật ngừng giữa câu ở dấu phẩy / chấm thì giọng lồng cũng ngừng ở dấu tương ứng, phần sau vào đúng
lúc nhân vật nói tiếp (câu ngắn hay dài đều vậy, chỉ dùng chỗ trống trước câu sau). **Từ cảm thán cần lọc**: bạn tự điền
danh sách (a, ừm, hả, ha ha…) – câu chỉ gồm các từ đó bị xoá, vế cảm thán đứng riêng trong câu dài bị bỏ ("Ừm, đúng vậy."
→ "Đúng vậy."); từ không có trong danh sách giữ nguyên.

**Bộ nhận dạng giọng nói** (chọn ở tab Tự động hoặc trang Cài đặt):
- **Qwen3-ASR** (mặc định): nghe tiếng Trung chuẩn nhất, tự đặt dấu câu theo nghĩa, kèm bộ căn giờ từng chữ (lệch ~0,03 s)
  để tách câu đúng chỗ nghỉ. Cần `transformers` ≥ 5.13: `.venv\Scripts\python -m pip install -U "transformers>=5.13" accelerate`.
  GPU dưới 6 GB chọn bản 0.6B ở Cài đặt. Chưa cài thì tool tự dùng SenseVoice.
- **SenseVoice**: nhanh, giỏi tiếng Phổ thông và Quảng Đông, tự nhận biết đoạn đang hát.
- **Paraformer**: có dấu câu và tự phân biệt người nói ngay trong lúc nhận dạng.
- **Whisper large-v3**: đa ngôn ngữ, dùng cho tiếng Anh, Nhật, Hàn.

---

## 4. Gợi ý để có bản dịch chuẩn nhất

**API Gemini miễn phí:** vào aistudio.google.com/apikey (đăng nhập Google) → *Create API key* → chép key → trang **Cài
đặt** → khung **Gemini** → dán vào ô *API key* (bấm *Thêm key* để thêm key dự phòng từ tài khoản / dự án khác) → *Kiểm tra
các key* → *Lưu cài đặt*. Gói miễn phí giới hạn số lần gửi mỗi phút / mỗi ngày: để *Số lần gửi tối đa / phút* thấp hơn giới
hạn một chút, *Số câu mỗi lần gửi* 30–60; key hết lượt hôm nay tool tự chuyển sang key sau. Kỹ năng *Dịch chuẩn* (mặc
định) giữ đúng số câu đã tách, không gộp, tên riêng / địa danh chuẩn – sửa ở tab Tự động → *Kỹ năng dịch AI* → *Sửa / thêm…*.

- Video đã có chữ Trung in sẵn: dùng **OCR**, cho chữ và thời gian gần như tuyệt đối chính xác.
- Video chỉ có tiếng: bật **Tách giọng khỏi nhạc trước**, rồi dùng **Qwen3-ASR** (hoặc SenseVoice). Mỗi dòng phụ đề là
  1 câu trọn nghĩa: chỉ tách ở cuối câu (。！？) hoặc chỗ nghỉ thật, không bao giờ cắt giữa 1 từ; dòng bị cắt ngang câu
  được tự nối lại. **Kiểu tách câu** (tab Tự động / Cài đặt): *Câu trọn ý – hợp lồng tiếng* (mặc định) hoặc *Câu ngắn –
  hợp phụ đề đọc nhanh*. Dự án cũ: tab Phụ đề → **Nối câu bị cắt** (có *Hoàn tác nối*; dự án đã dịch thì câu mới được dịch luôn).
- Câu dịch quá dài (vd. "Hùng Đại, hắn nói giúp hắn quản lý tù nhân là ý gì? Ca, ca là quản sự trại lưu đày của
  chúng ta mà,"): sau khi dịch cả phim, tool **tự tách ở dấu câu giữa câu** – ưu tiên chỗ hết câu (? ! .), rồi dấu phẩy –
  thành nhiều dòng (chỗ tách phải có dấu câu ở cả câu gốc lẫn câu dịch, đúng vị trí tương ứng; mốc thời gian đặt đúng chỗ
  nhân vật ngắt hơi). Giọng lồng **nghỉ 0,25 s** giữa 2 vế (hết câu 0,4 s). Chỉnh ở tab Phụ đề: *Tự tách câu dịch dài ở
  dấu câu, câu dài hơn 14 tiếng*, *nghỉ giữa 2 vế*; dự án cũ bấm **Tách câu dài** (câu đã lồng tiếng được đọc lại), có
  *Hoàn tác tách* / Ctrl+Z. Các nút nối câu (*Nối câu bị cắt*, *AI nối câu*) không nối lại các vế đã tách.
  Tab Tự động có công tắc *Tách câu dịch dài* (cùng 1 công tắc với tab Phụ đề): dịch xong hết mới tách, tách xong
  hết mới lồng tiếng.
- Dịch ở phần mềm khác rồi **Nạp SRT đã dịch**: file dịch có ít câu hơn (gộp câu) thì tool gộp các câu gốc tương ứng,
  nhiều câu hơn (tách câu) thì nối các câu dịch vào câu gốc – không còn câu bị trống. Có tách giọng thì tool còn **nghe lại các đoạn có tiếng bị bộ chia đoạn bỏ sót**, nên không còn câu bị bỏ không dịch.
- Dùng **AI dịch** và chọn đúng **thể loại**. Sau lần dịch đầu, sửa bảng tên riêng nếu cần rồi bấm **Dịch lại tất cả**. Bản dịch cũ được lưu sẵn, nên dịch lại không tốn thêm tiền cho những câu không đổi.
- **Kỹ năng dịch AI** (dùng với mọi công cụ dịch AI: DeepSeek, Claude, GPT, Gemini, Ollama hoặc bất kỳ API tương thích
  OpenAI nào – điền *OpenAI base URL* + *OpenAI API key* ở Cài đặt):
  - *AI sửa lỗi nghe & nối câu* trước khi dịch: sửa chữ máy nghe nhầm (đồng âm) và nối câu bị cắt ngang; tool kiểm tra
    từng chỗ sửa (chỉ nhận sửa gần chữ cũ, chỉ nối câu liền nhau), có nút *Hoàn tác sửa*.
  - *Hồ sơ phim*: AI đọc cả kịch bản 1 lần, ghi thể loại, nhân vật, cách xưng hô, tên riêng / meme – mọi lần dịch sau
    dùng chung, sửa được ở tab Phụ đề.
  - *AI kiểm tra bản dịch*: soát câu sai nghĩa, xưng hô / tên không thống nhất, câu quá dài, còn chữ Hán rồi sửa (rê
    chuột vào câu để xem lý do). Câu bạn tự sửa không bị đụng tới.
  - *Kỹ năng của bạn*: tab Tự động → *Kỹ năng dịch AI* → *Sửa / thêm…*: viết luật riêng (xưng hô, cách dịch meme…) hoặc
    nạp file .txt / .md; có sẵn 3 kỹ năng mẫu (hài – meme – AI, lồng tiếng câu ngắn, cổ trang).
- Lồng tiếng tự nhiên nhất: **Tách giọng → Phân biệt người nói → Tự gán giọng**, hoặc chọn *Nhái giọng*.

## 5. Dòng lệnh
```bash
python TraMi.py                                   # mở giao diện
python TraMi.py dich phim.mp4 --engine deepseek --style co_trang
python TraMi.py dich phim.mp4 --ocr --xoa-phude inpaint --long-tieng -o D:\KetQua
python TraMi.py dich phim.mp4 --dich-sang en --long-tieng      # dịch + lồng tiếng Anh (th / ja / ko: Thái / Nhật / Hàn)
python TraMi.py dich phim.mp4 --long-tieng --xuat-video      # mặc định dừng trước bước xuất; --xuat-video = xuất luôn
```

## 6. Cấu trúc
```
Subvideo/
├─ TraMi.py            mở phần mềm (bản đóng gói: Subvideo.exe)
├─ trami/              phần xử lý: asr, asr_funasr, ocr, translate, separate, diarize,
│  │                   skills, tts, lama, inpaint, video, overlay (chữ / logo), silence
│  │                   (khoảng lặng / bài hát), pipeline, jobs, project
│  └─ gui/             giao diện PySide6 vẽ bằng QPainter (theme, icons, widgets,
│                      player, editors, project_page, main)
├─ fonts/  assets/     font Be Vietnam Pro, Bangers, Anton, Paytone One, Baloo 2, Lobster… (giấy phép
│                      SIL OFL – fonts/Fonts-more-OFL.txt), icon
├─ tests/              kiểm thử tự động:  python -m pytest tests
└─ Subvideo.spec       cấu hình đóng gói .exe (DongGoi_EXE.bat)
```

## 7. Bản quyền các mô hình
- Bộ tách giọng (UVR / audio-separator – github.com/nomadkaraoke/python-audio-separator): MIT. Cập nhật để có Mel-RoFormer Kim và chế độ kết hợp: `.venv\Scripts\python -m pip install -U "audio-separator>=0.44" -c torch-constraints.txt`
- Qwen3-ASR, Qwen3-ForcedAligner (github.com/QwenLM/Qwen3-ASR), FunASR / SenseVoice, LaMa: Apache-2.0.
- VieNeu-TTS: Apache-2.0, kèm watermark âm thanh chống lạm dụng.
- SpeechBrain: Apache-2.0.

**OmniVoice (nhái giọng y hệt từ file ghi âm mẫu):** tab Lồng tiếng → khung *Giọng mẫu để nhái*: thêm file ghi âm 3–10 giây
(hoặc ghi âm từ micro, hoặc lấy 8 giây từ video), gõ đúng lời nói trong đoạn đó, rồi chọn giọng mẫu cho từng người nói.
Chọn bản *G-OmniVoice* (tối ưu tiếng Việt) ở Cài đặt cần token HuggingFace và bấm đồng ý điều khoản trên trang mô hình.
Cài tay: `.venv\Scripts\python -m pip install omnivoice -c torch-constraints.txt`

**Nhái giọng:** chỉ nhái giọng khi bạn có quyền sử dụng giọng đó.
**Đăng lại video:** hãy tôn trọng bản quyền của tác giả video gốc.

## 8. Xử lý sự cố
- **Gặp lỗi:** mở bằng `ChayTool_XemLog.bat` để xem chi tiết lỗi.
- **Trang Cài đặt** cho biết gói AI nào đã cài (✓) hoặc chưa (✗).
- **"Không kết nối được Google Dịch":** có thể bị giới hạn tạm thời. Đợi vài phút, hoặc chuyển sang AI dịch.
- **Mạng lag / mất mạng giữa lúc dịch, lồng tiếng, tải video:** tool không treo – dòng trạng thái báo *"⚠ Mất mạng – … tạm dừng, có mạng lại sẽ tự làm tiếp"* và tự làm tiếp khi có mạng (chờ tối đa 10 phút). Bấm **Huỷ** là dừng ngay. Mất mạng lâu hơn thì phần đã làm xong (câu đã dịch, câu đã đọc giọng) vẫn được giữ – có mạng bấm lại, tool chỉ làm tiếp phần còn thiếu.
- **Chạy chậm khi không có GPU:**
  - Chọn mô hình tách giọng *MDX-Net*.
  - Chọn Whisper *medium*, hoặc dùng SenseVoice.
  - Chuyển xoá phụ đề sang *OpenCV*.
- **Phụ đề / giọng lồng càng về sau càng lệch khẩu hình (video dài, livestream, video ghép nhiều phần):** do giờ âm thanh trong file lệch dần so với hình. Từ 2.0.92 tool giải mã âm thanh theo đúng giờ của hình. Dự án làm trước đó: bấm nút **Căn giờ theo hình** (tab **Âm thanh** mục 1; mở dự án mà tool đo thấy lệch thì nút này hiện cả trên thanh trên cùng). Tool kéo giãn bản tách giọng, dời mốc câu và xếp lại bản lồng cho khớp hình – không phải nghe, dịch hay đọc lại. Bấm lại khi đã khớp thì tool chỉ báo "đã đúng giờ", không đổi gì.
- **Lỗi khi xuất video:** lệnh FFmpeg của lần xuất gần nhất nằm trong `data/projects/<dự án>/work/render/last_ffmpeg_cmd.txt`.
