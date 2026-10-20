# TraMi 2.0 – Phần mềm dịch video Trung → Việt

Phần mềm chạy trên máy tính: có cửa sổ riêng, giao diện tiếng Việt, không cần trình duyệt.
Dùng để dịch video tiếng Trung sang tiếng Việt, xoá phụ đề Trung cũ, đè phụ đề Việt, lồng tiếng Việt nhiều giọng
và chỉnh sửa video trước khi đăng Facebook / YouTube / TikTok.

---

## 1. Cài đặt (Windows)

| Bước | Làm gì |
|---|---|
| 1 | Giải nén zip vào thư mục ngắn, không dấu, ví dụ `D:\TraMi` |
| 2 | Nháy đúp **`CaiDat_Windows.bat`**. File này tự cài Python 3.11 nếu máy chưa có, cài thư viện, tải FFmpeg và tạo icon **TraMi** trên Desktop |
| 3 | Khi được hỏi *"Cài AI nâng cao?"*, bấm **Y**. Bước này tự nhận card NVIDIA và cài PyTorch bản GPU, rồi cài các gói tách giọng, SenseVoice, phân người nói, VieNeu-TTS, LaMa. Cần thêm khoảng 5–8 GB |
| 4 | Mở **icon TraMi** trên Desktop, hoặc chạy `ChayTool_Windows.bat` |

Có thể cài AI nâng cao sau, hoặc cài lại, bằng `CaiDat_AI_NangCao.bat`.
Các mô hình AI (khoảng 3–5 GB) tự tải về ở lần dùng đầu tiên.

**Các lệnh cài tay** (PowerShell, trong thư mục TraMi). Phải cài đúng thứ tự:

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

### Đóng gói thành file .exe
Chạy **`DongGoi_EXE.bat`**. Kết quả là thư mục `dist\TraMi\` chứa **TraMi.exe**.
Nén thư mục này lại là gửi được sang máy khác, máy đó không cần cài Python.
Dữ liệu dự án của bản exe được lưu ở `%LOCALAPPDATA%\TraMi`.

Bản exe kèm AI nâng cao khá nặng (2–4 GB) vì có PyTorch.

---

## 2. Dùng nhanh

1. Ở **Trang chủ**, kéo thả video, bấm chọn file, hoặc dán link Douyin / Bilibili / YouTube / TikTok.
2. Mở tab **Tự động** và chọn:
   - **Nguồn phụ đề**:
     - *Nhận dạng giọng nói*: dùng khi video chưa có chữ.
     - *OCR phụ đề cứng*: dùng khi video đã in sẵn chữ Trung. Cách này chính xác nhất.
   - **Công cụ dịch**: AI dịch hay hơn Google, ví dụ DeepSeek rẻ và giỏi tiếng Trung. Nhập API key ở trang **Cài đặt**. Hoặc chọn **Hy-MT2**: mô hình chuyên dịch chạy ngay trên máy, miễn phí, không cần mạng (tải 1 lần ~3,6 GB ở tab **Tài liệu**).
   - **Thể loại**: quyết định cách xưng hô, ví dụ cổ trang dùng ta – ngươi, huynh – đệ.
   - **Phụ đề Trung cũ**: chọn *Xoá sạch bằng AI*.
   - **Lồng tiếng**: bật nếu muốn thêm giọng Việt.
3. Bấm **Bắt đầu dịch tự động**. Thanh tiến độ ở cuối cửa sổ hiện % hoàn thành và thời gian còn lại.
4. Soát phụ đề ở tab **Phụ đề**, rồi mở tab **Xuất** để lấy video MP4 và file SRT.

Để dịch hàng loạt, chọn nhiều file cùng lúc ở Trang chủ, hoặc dùng trang **Xử lý hàng loạt**.
Muốn dịch nhanh không cần mở giao diện, kéo thả video vào `DichNhanh_KeoTha.bat`.

---

## 3. Các chức năng

| Tab | Chức năng |
|---|---|
| **Tự động** | Chạy trọn gói các bước: tải video → tách giọng → nhận dạng → phân biệt người nói → dịch → lồng tiếng → xoá phụ đề cũ → xuất video |
| **Phụ đề** | Bảng sửa trực tiếp câu Trung, câu Việt, thời gian và người nói. Bấm vào một dòng để tua video tới câu đó; video tự cuộn theo câu đang phát. Gộp, tách, thêm, xoá câu. Dời giờ, tìm & thay, dịch lại 1 câu, dịch các dòng trống. Nhập SRT/ASS. Cột 🎵 đánh dấu câu đang hát; cột "Bỏ lồng" để không lồng tiếng câu đó. Có bảng tên riêng Hán Việt do AI tự điền |
| **Xoá phụ đề** | Khoanh vùng chữ ngay trên video (hoặc trên ảnh chụp khung), được nhiều vùng; kéo 4 chấm trên 4 cạnh để đổi cao / thấp / dài / ngắn, kéo giữa để di chuyển. Tự dò vùng phụ đề. Có 5 cách: **AI LaMa** (vẽ lại nền sau chữ), **vá nền** (chép nền sát bên, nhanh), làm mờ, che dải màu, delogo. Xem thử 1 khung; nút **Chạy xoá bằng AI** xoá cả video ngay, trình phát chiếu bản đã xoá và lúc xuất dùng lại, không chạy lại |
| **Chỉnh sửa** | Cắt đầu/cuối và xoá nhiều đoạn giữa, xem trên thanh thời gian. Crop, xoay, lật gương, zoom, tốc độ 0.5–2×. Đổi khung 9:16 / 16:9 / 1:1 / 4:5 với nền mờ. Độ phân giải tới 4K. Chỉnh màu, làm nét, khử nhiễu. Logo, chữ tĩnh hoặc chữ chạy, mờ dần đầu/cuối |
| **Kiểu chữ** | Font, cỡ, màu, viền, bóng, nền hộp, vị trí, VIẾT HOA, song ngữ. Xem trước trực tiếp trên video, đúng cỡ và chỗ như video xuất. Kéo phụ đề trên video để đặt chỗ; nền hộp ôm chữ hoặc dải cố định, kéo 4 chấm trên cạnh nền để đổi chiều cao / độ dài. Có sẵn 4 mẫu nhanh |
| **Lồng tiếng** | ① **Tách giọng / nhạc nền** (Mel-RoFormer Kim, BS-RoFormer hoặc kết hợp cả hai): giữ nguyên nhạc và hiệu ứng gốc, chỉ thay lời thoại. ② **Mỗi người nói một giọng**: 25 giọng Việt của VieNeu (⭐ giọng tiêu biểu đứng đầu) + 6 giọng thu âm sạch + giọng mẫu của bạn, 2 giọng Microsoft, hoặc **nhái giọng nhân vật gốc**. Nghe thử từng giọng. "Tự gán giọng" tự nhận nhân vật giọng nam / nữ để chọn giọng hợp. Số đọc thành chữ, câu đọc hỏng (tiếng bíp, im lặng) tự đọc lại, âm lượng các câu được cân cho đều. ③ Mỗi câu phụ đề là 1 đoạn giọng riêng, ghép đúng lúc nhân vật nói, hiện trên dòng *Lồng tiếng* của timeline (kéo để dời, bấm đúp để nghe); bài hát trong phim được giữ nguyên |
| **Âm thanh** | MỌI chỉnh âm thanh ở 1 chỗ: chọn nguồn tiếng gốc (nguyên bản / bản đã tách), âm lượng từng lớp – tiếng gốc, nhạc nền + hiệu ứng đã tách, giọng nói gốc tiếng Trung (đoạn hát giữ nguyên), giọng lồng tiếng Việt, nhạc thêm (tự nhỏ xuống khi có lời), âm lượng tổng. Mỗi lớp có nút tắt và *Nghe riêng*. Đổi cao độ giọng gốc. Bấm Phát trên video là nghe thay đổi ngay khi kéo thanh; video xuất ra trộn y như vậy. Tải MP3 bản trộn / giọng gốc / nhạc nền |
| **Xuất** | MP4 (H.264 CPU / NVIDIA NVENC / H.265), tất cả trong 1 lần encode. Phụ đề SRT (Việt / Trung / song ngữ), ASS, VTT, TXT. Chia video dài thành nhiều phần, tách MP3 |

**Bộ nhận dạng giọng nói** (chọn ở tab Tự động hoặc trang Cài đặt):
- **Qwen3-ASR** (mặc định): nghe tiếng Trung chuẩn nhất, tự đặt dấu câu theo nghĩa, kèm bộ căn giờ từng chữ (lệch ~0,03 s)
  để tách câu đúng chỗ nghỉ. Cần `transformers` ≥ 5.13: `.venv\Scripts\python -m pip install -U "transformers>=5.13" accelerate`.
  GPU dưới 6 GB chọn bản 0.6B ở Cài đặt. Chưa cài thì tool tự dùng SenseVoice.
- **SenseVoice**: nhanh, giỏi tiếng Phổ thông và Quảng Đông, tự nhận biết đoạn đang hát.
- **Paraformer**: có dấu câu và tự phân biệt người nói ngay trong lúc nhận dạng.
- **Whisper large-v3**: đa ngôn ngữ, dùng cho tiếng Anh, Nhật, Hàn.

---

## 4. Gợi ý để có bản dịch chuẩn nhất
- Video đã có chữ Trung in sẵn: dùng **OCR**, cho chữ và thời gian gần như tuyệt đối chính xác.
- Video chỉ có tiếng: bật **Tách giọng khỏi nhạc trước**, rồi dùng **Qwen3-ASR** (hoặc SenseVoice). Mỗi dòng phụ đề là
  1 câu trọn nghĩa: chỉ tách ở cuối câu (。！？) hoặc chỗ nghỉ thật; dấu phẩy nói liền không bị tách. Có tách giọng thì tool còn **nghe lại các đoạn có tiếng bị bộ chia đoạn bỏ sót**, nên không còn câu bị bỏ không dịch.
- Dùng **AI dịch** và chọn đúng **thể loại**. Sau lần dịch đầu, sửa bảng tên riêng nếu cần rồi bấm **Dịch lại tất cả**. Bản dịch cũ được lưu sẵn, nên dịch lại không tốn thêm tiền cho những câu không đổi.
- Lồng tiếng tự nhiên nhất: **Tách giọng → Phân biệt người nói → Tự gán giọng**, hoặc chọn *Nhái giọng*.

## 5. Dòng lệnh
```bash
python TraMi.py                                   # mở giao diện
python TraMi.py dich phim.mp4 --engine deepseek --style co_trang
python TraMi.py dich phim.mp4 --ocr --xoa-phude inpaint --long-tieng -o D:\KetQua
```

## 6. Cấu trúc
```
TraMi/
├─ TraMi.py            mở phần mềm
├─ trami/              phần xử lý: asr, asr_funasr, ocr, translate, separate, diarize,
│  │                   tts, lama, inpaint, video, pipeline, jobs, project
│  └─ gui/             giao diện PySide6 vẽ bằng QPainter (theme, icons, widgets,
│                      player, editors, project_page, main)
├─ fonts/  assets/     font Be Vietnam Pro (giấy phép SIL OFL), icon
├─ tests/              kiểm thử tự động:  python -m pytest tests
└─ TraMi.spec          cấu hình đóng gói .exe
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
- **Chạy chậm khi không có GPU:**
  - Chọn mô hình tách giọng *MDX-Net*.
  - Chọn Whisper *medium*, hoặc dùng SenseVoice.
  - Chuyển xoá phụ đề sang *OpenCV*.
- **Lỗi khi xuất video:** lệnh FFmpeg của lần xuất gần nhất nằm trong `data/projects/<dự án>/work/render/last_ffmpeg_cmd.txt`.
