# TraMi – ghi nhớ cho Claude

Công cụ desktop (PySide6) dịch video tiếng Trung → tiếng Việt: nhận dạng giọng / OCR phụ đề cứng → dịch (LLM hoặc Google)
→ xoá phụ đề cũ (LaMa / OpenCV) → đè phụ đề Việt → lồng tiếng (Edge TTS / VieNeu-TTS, nhái giọng) → xuất MP4 bằng FFmpeg.
Phiên bản hiện tại: xem `trami/__init__.py`.

## Người dùng & cách làm việc
- Người dùng nói tiếng Việt, chạy trên **Windows** (thư mục `C:\Users\daole\Downloads\TraMi`, venv `.venv`, Python 3.11),
  có card **NVIDIA**, PyTorch `2.11.0+cu128` (đã chạy GPU: True). Mục đích: dịch video đăng Facebook / YouTube.
- Chạy dạng tool: `.venv\Scripts\python TraMi.py` (chưa đóng gói exe – người dùng chưa muốn).
- **Chỉ gửi các file đã sửa** (kèm bảng "chép vào đâu"), không gửi zip đầy đủ. Mỗi lần sửa tăng `__version__`.
- Không commit tên model AI vào code/commit message ngoài giá trị cấu hình cần thiết.
- Trả lời ngắn gọn, bằng tiếng Việt, hướng dẫn từng bước bấm trên giao diện.

## Cấu trúc
- `TraMi.py` – mở giao diện; có tham số → CLI (`python TraMi.py dich phim.mp4 ...`).
- `trami/config.py` – `DATA_DIR` (`data/`, exe: `%LOCALAPPDATA%\TraMi`), `DEFAULT_SETTINGS`, settings chỉ lưu phần khác mặc định,
  `installed()` dùng `find_spec` (không import – import funasr mất ~2 phút), `log_error()` ghi `data/logs/loi.txt`.
- `trami/pipeline.py` – các bước: `attach_source`, `separate_audio`, `recognize` (tự chuyển Whisper nếu FunASR/torch lỗi),
  `diarize`, `translate`, `dub`, `render`, `auto`; `effective_style` (đặt phụ đề Việt vào đúng chỗ chữ Trung cũ).
- `trami/video.py` – `build_and_render`: 1 lần encode, `filter_complex`; cắt giữa dùng `select/aselect`;
  âm thanh ngoài qua `ext_audio` (`apad=whole_dur` để không treo); trộn: nhạc nền đã tách + giọng gốc (đoạn hát) + bản lồng.
  Lệnh FFmpeg cuối: `data/projects/<id>/work/render/last_ffmpeg_cmd.txt`.
- `trami/tts.py` – `synthesize_dub` (giọng mỗi người nói: `edge:<id>` / `vieneu:<preset>` / `clone`), `clean_tts_text`,
  `preview_voice`. VieNeu API: `Vieneu(mode="v3turbo").infer(text, voice="<tên preset>" | ref_audio=...)` → numpy 48 kHz.
- `trami/asr.py` (faster-whisper), `asr_funasr.py` (SenseVoice/Paraformer, `mark_songs`), `diarize.py` (ECAPA),
  `separate.py` (audio-separator BS-RoFormer), `lama.py` + `inpaint.py` (xoá chữ), `ocr.py` (RapidOCR),
  `translate.py` (`LLMClient` gọi HTTP: anthropic / OpenAI-compatible / gemini / deepseek / ollama; Google gtx).
- `trami/jobs.py` – hàng đợi 1 luồng cho tác vụ dài; `trami/gui/*` – giao diện (QPainter), `common.run_async`,
  `WheelGuard` (lăn chuột không đổi ô chọn khi chưa bấm vào).
- `tests/test_core.py` – `python -m pytest -q tests` (36 test; 1 test cần gói AI).

## Cài đặt trên Windows (bài học)
- Cài torch GPU **trước**, khoá phiên bản (`torch-constraints.txt`) rồi mới `pip install -r requirements-ai.txt -c torch-constraints.txt`,
  nếu không pip sẽ thay torch bằng bản CPU.
- `WinError 1114 c10.dll`: do file torch cũ sót lại → gỡ, xoá hẳn thư mục `site-packages\torch*`, cài lại cu128.
- `libtorchaudio.pyd` lỗi: torch/torchaudio lệch phiên bản.
- Model tải lần đầu: BS-RoFormer 639 MB, SenseVoice ~1 GB (ModelScope, chậm từ VN), VieNeu, ECAPA, LaMa 206 MB.

## Lịch sử sửa gần đây
- 2.0.5: lăn chuột lướt qua combobox/spinbox/slider không đổi giá trị (`gui/common.py` WheelGuard).
- 2.0.6: Anthropic không gửi `temperature` (model mới báo 400 "temperature is deprecated"); model mặc định `claude-opus-5-5`;
  API OpenAI-compatible tự bỏ `temperature` nếu bị từ chối.
- 2.0.7: lồng tiếng báo số câu đã lồng (`dub_stats`, `pipeline.dub_summary`); lỗi rõ khi 0 câu được lồng (vd. tất cả bị đánh dấu 🎵);
  `mark_songs` không đoán bài hát theo nhạc nền nếu >25% số câu.
- 2.0.8: `clean_tts_text` (bỏ chữ Hán sót, emoji); thông báo lỗi có tên lỗi (`err_text`); mọi lỗi ghi vào `data/logs/loi.txt`.

- 2.0.9: trình phát trong tool phát luôn bản lồng tiếng: `pipeline.dub_preview_audio` trộn sẵn `work/dub_preview_<hash>.m4a`
  (giọng Việt + nhạc nền/tiếng gốc theo các thanh âm lượng), `Player.set_alt_audio` phát song song, tắt tiếng video,
  đồng bộ khi play/pause/tua (bù lệch > 250 ms). Bật/tắt bằng công tắc "Dùng bản lồng tiếng (xem trước + khi xuất)".
  Nguyên nhân người dùng "không nghe thấy lồng tiếng": trước đây trình phát chỉ phát tiếng gốc; video XUẤT RA vẫn có
  giọng lồng (đã kiểm chứng bằng sóng thử 1 kHz, cả nhánh có/không nhạc nền tách).

## Vấn đề đang mở
- Chờ người dùng xác nhận 2.0.9 nghe được giọng lồng trong trình phát và trong video xuất.
- Đề xuất chưa làm: hiện %/MB khi tải mô hình; tải SenseVoice từ HuggingFace trước ModelScope.
