# TraMi – ghi nhớ cho Claude

Công cụ desktop (PySide6) dịch video tiếng Trung → tiếng Việt (từ 2.0.47 cả Anh / Thái / Nhật / Hàn): nhận dạng giọng / OCR phụ đề cứng → dịch (LLM hoặc Google)
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
  `installed()` dùng `find_spec` (không import – import funasr mất ~2 phút), `log_error()` ghi `data/logs/loi.txt`,
  `fix_std_streams()` (gọi đầu tiên khi mở tool: pythonw để sys.stdout/stderr = None -> ghi `data/logs/console.txt`).
- `trami/pipeline.py` – các bước: `attach_source`, `separate_audio`, `recognize` (tự chuyển Whisper nếu FunASR/torch lỗi),
  `diarize`, `translate`, `dub`, `render`, `auto`; `effective_style` (đặt phụ đề Việt vào đúng chỗ chữ Trung cũ).
- `trami/video.py` – `build_and_render`: 1 lần encode, `filter_complex`; cắt giữa dùng `select/aselect`;
  âm thanh ngoài qua `ext_audio` (`apad=whole_dur` để không treo); trộn: nhạc nền đã tách + giọng gốc (đoạn hát) + bản lồng.
  Lệnh FFmpeg cuối: `data/projects/<id>/work/render/last_ffmpeg_cmd.txt`.
- `trami/tts.py` – `synthesize_dub` (giọng mỗi người nói: `edge:<id>` / `vieneu:<preset>` / `clone`), `clean_tts_text`,
  `preview_voice`, `plan_layout` (xếp giọng theo thời gian, thuần – không đụng âm thanh; `OVERFLOW_MODES`). VieNeu API: `Vieneu(mode="v3turbo").infer(text, voice="<tên preset>" | ref_audio=...)` → numpy 48 kHz.
- `trami/asr_qwen.py` + `qwen_asr_worker.py` (Qwen3-ASR + Qwen3-ForcedAligner, tiến trình riêng `--qwen-asr-worker`,
  transformers ≥ 5.13, mặc định từ 2.0.29), `trami/asr.py` (faster-whisper), `asr_funasr.py` (SenseVoice/Paraformer, `mark_songs`), `diarize.py` (ECAPA),
  `separate.py` (audio-separator BS-RoFormer), `lama.py` + `inpaint.py` (xoá chữ), `ocr.py` (RapidOCR),
  `skills.py` (kỹ năng dịch AI: sửa lỗi nghe & nối câu, hồ sơ phim, kiểm tra bản dịch, kỹ năng riêng – `gui/skills_dialog.py`),
  `translate.py` (`LLMClient` gọi HTTP: anthropic / OpenAI-compatible / gemini / deepseek / ollama; Google gtx;
  Hy-MT2 trên máy qua `hymt_worker.py`), `voicefix.py` (đọc số, phát hiện giọng AI hỏng, cân âm lượng),
  `gender.py` (đoán giọng nam/nữ mỗi người nói), `karaoke.py` (thời điểm từng từ cho phụ đề karaoke),
  `omni_design.py` (23 giọng OmniVoice chọn sẵn tạo theo mô tả).
- `trami/audiomix.py` – quy tắc trộn âm thanh dùng chung cho xuất video (`video.build_and_render`) và bộ trộn trực tiếp
  `LiveMix` của trình phát (`player.LiveAudio` + `_Feeder` ở QThread riêng, QAudioSink); cài đặt ở `edit["audio"]`, tab `gui/audio_tab.py`.
- `trami/jobs.py` – hàng đợi NHIỀU luồng (từ 2.0.64, tối đa `MAX_RUNNING` 4): cùng dự án lần lượt (trừ `READONLY_KINDS`),
  mỗi lúc 1 việc GPU (`GPU_KINDS`, dịch Hy-MT2); `progress_hook` theo từng luồng; trang `main.JobsPage` "Việc đang làm".
  `trami/stream.py` – `LivePipeline` (tab Tự động "Vừa tách vừa dịch"); `trami/gui/*` – giao diện `trami/gui/*` – giao diện (QPainter), `common.run_async`,
  `WheelGuard` (lăn chuột không đổi ô chọn khi chưa bấm vào). Trang dự án bố cục kiểu trình dựng phim (từ 2.0.28):
  trái `IconTabs` + 9 tab (Âm thanh = tab 6, `TAB_AUDIO`…`TAB_EXPORT` = 8), giữa trình phát, phải `gui/inspector.LineInspector` (câu đang chọn), dưới `gui/timeline.TrackTimeline`.
  Khoảng cách khung: `theme.GAP` / `theme.PANEL_PAD`; hàng nhiều nút dùng `widgets.flow()` (tự xuống dòng) thay `hbox`.
  Chụp ảnh kiểm tra giao diện phải nạp theme như `run()`: `theme.load_fonts(); app.setFont(theme.font(10));
  app.setStyleSheet(theme.stylesheet())`.
- `trami/langs.py` – tiếng đích (vi/en/th/ja/ko): tên, mã Google, tên cho Hy-MT2, giọng Edge, tốc độ nói (`budget`),
  `units` (từ / chữ), `detect` (tiếng của 1 câu theo chữ viết), font phụ đề Thái/Nhật/Hàn (`subtitle_font`).
  `pipeline.target_lang(p)` (dự án `p.data["target_lang"]`, chưa có -> Cài đặt), `trans_lang(p)` (tiếng của bản dịch đang có).
- Tách câu (`align.py`): `plan_cuts` (dấu câu + chỗ nghỉ + ranh giới từ jieba), `split_timed`, `join_broken` (nối câu
  bị cắt ngang), kiểu tách câu `split_params` (Cài đặt `split_style`: dub / short).
- `trami/textfit.py` – đo bề rộng chữ phụ đề như libass (Qt khi có giao diện, không thì Pillow+fontTools, không thì ước
  lượng) + `fit_lines` (xếp câu vào dòng: % bề rộng, số dòng tối đa, thu nhỏ chữ). `subtitle.layout_event` dùng chung
  cho `to_ass` và khung xem trước (`player.SubtitleItem._layout`) -> xuống dòng / cỡ chữ / ô nền giống hệt.
- `trami/vfill.py` – xoá chữ bằng NỀN THẬT từ khung trước / sau (`BandFiller`: đo chuyển động bằng tương quan pha trên
  dải hình quanh vùng, so viền rồi lấp, khung đã lấp làm nguồn tiếp, cảnh đứng yên giữ phần đã vẽ; `subtitle_mask` mặt nạ
  chữ mọi màu). `trami/logo.py` – gỡ logo cố định (tính ngược lớp logo trong suốt từ ~200 khung). `inpaint.inpaint_video`
  ghép cả hai (`_Job`, 2 tiến trình giải mã: dải hình + khung đầy đủ).
- `trami/hf_download.py` – tải mô hình HuggingFace (tab Tài liệu: Qwen3, Hy-MT2, OmniVoice) ở tiến trình riêng
  `TraMi.py --hf-download <json>`: % / MB / MB/s / số file thật, Huỷ = giết tiến trình. Đường tải: HuggingFace Xet
  (đứng 45 s -> ghi `data/hf_no_xet.txt`, `config` đặt `HF_HUB_DISABLE_XET=1`) -> HuggingFace tải thường (60 s) ->
  ModelScope chính hãng (`MS_MIRRORS`, tải thẳng vào kho HF: snapshot `ms_commit` + `refs/main`; được thì ghi
  `data/hf_prefer_modelscope.txt` -> lần sau ModelScope trước). `models._hf_snapshot` ưu tiên snapshot theo refs/main.
- `trami/sitelogin.py` – tải video từ link: `extract_url` (link / dòng chia sẻ của app / mã BV), `normalize_url`
  (link điện thoại / rút gọn -> dạng yt-dlp có bộ tải riêng), đăng nhập Bilibili
  bằng mã QR (`bili_qr_start/poll/status`, cookie Netscape `data/cookies/bilibili.txt`), `BROWSERS` cho cookie
  trình duyệt; hộp mã QR `gui/link_login.py`; `pipeline.download_video` / `update_ytdlp` / `ytdlp_version`.
- `trami/net.py` – mạng chập chờn / mất mạng: `is_net_error`, `online` (thử nối tới proxy / 1.1.1.1 / 8.8.8.8 /
  223.5.5.5 / bing cùng lúc), `wait_online` (báo "⚠ Mất mạng – … tạm dừng", chờ ≤ 10 phút -> `NetworkDown`),
  `call` (gọi ở luồng phụ, Huỷ dừng ngay), `session` (requests có TCP keepalive). `jobs` chạy mọi tác vụ trong
  `net.task(cancel, report)` -> hàm gọi mạng tự lấy nút Huỷ + dòng trạng thái.
- `trami/songs.py` – đoán cụm câu đang HÁT (nốt ngân trên giọng đã tách) -> 🎵; `pipeline.mark_song_lines` (♪ trong bản dịch).
- `tests/test_core.py` – `python -m pytest -q tests` (116 test; 1 test cần gói AI; test OCR vẽ video bằng font chữ Hán).

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

- 2.0.10: VieNeu trong tool lỗi `cudaErrorStreamCaptureInvalidated` (CUDA graph "fused frame" bị việc GPU ở luồng khác
  – trình phát video, tách giọng – phá khi đang chụp). Đặt `VIENEU_FUSED_FRAME=0` trước khi import vieneu và khoá
  `_vn_infer_lock` để mỗi lúc chỉ 1 câu được đọc. Chạy độc lập (1 luồng) thì không lỗi.

- 2.0.11: sau 2.0.10 tool **tắt im lặng** khi VieNeu chạy (sập tầng C/GPU, không traceback). Chuyển VieNeu sang
  **tiến trình riêng**: `trami/vieneu_worker.py` (chạy bằng `TraMi.py --vieneu-worker <mode>`), giao thức JSON qua
  stdin/stdout với tiền tố `@@TRAMI@@`; `tts.VieneuWorker.synth(text, out, voice=, ref_audio=)`. Worker sập → báo lỗi và
  tự mở lại 1 lần; lỗi "capture" → chạy lại với `VIENEU_FUSED_FRAME=0`. Mặc định bật lại CUDA graph (nhanh) vì
  tiến trình riêng không đụng GPU của trình phát. Sập giao diện ghi `data/logs/sap.txt` (faulthandler).

- 2.0.12: sửa WinError 32 khi trộn lại bản nghe thử (file m4a cũ đang được trình phát mở → bỏ qua, xoá lần sau).
  Lưu nhiều bản lồng tiếng: mỗi lần tạo ghi `work/dubs/dub_<thời gian>.wav` + `p.data["dub_versions"]`
  (`pipeline.dub_versions / use_dub_version / delete_dub_version`); tab Lồng tiếng mục 4 liệt kê, nút Nghe / Dùng bản này / Xoá.

- 2.0.13: Phân biệt người nói lỗi WinError 1314 (SpeechBrain tạo symlink từ cache HF sang `data/models/spkrec-ecapa`,
  Windows cần quyền Admin) → `from_hparams(..., local_strategy=LocalStrategy.COPY)`.

- 2.0.14: xem trước trực tiếp các chỉnh sửa trên trình phát (`player.VideoView.set_edit` / `Player.set_edit`):
  khung đầu ra theo tỉ lệ khung + cách lấp đầy (nền mờ hiển thị nền xám, chưa làm mờ thật), crop, lật, xoay, phóng to
  (QTransform trên QGraphicsVideoItem lồng trong 2 QGraphicsRectItem cắt viền), màu (ColorEffect: LUT numpy giống `eq`),
  tốc độ (`setPlaybackRate`), bỏ qua đoạn cắt khi phát (`keep_ranges`). Phụ đề xem trước vẽ theo khung đầu ra
  (`view.out_h` = chiều cao thật khi xuất). `project_page._apply_live` (timer 40 ms) gọi khi đổi tab Chỉnh sửa / Kiểu chữ.
  Chưa xem trước: làm nét, khử nhiễu, logo, chữ chèn, nhạc nền thêm, mờ dần, xoá phụ đề cũ.

- 2.0.15: tiến độ thật từ thư viện AI: `trami/progress_hook.tqdm_progress(progress, lo, hi, label, cancel, download=)`
  vá `tqdm.update/refresh` → báo `n/total` (MB khi tải mô hình), bấm Huỷ ném `Cancelled` ngay trong vòng lặp thư viện.
  Dùng cho tách giọng (`separate.py`), SenseVoice/Paraformer, Whisper (tải mô hình), ECAPA.
  Tab Xoá phụ đề bố trí lại 3 bước: 1 khoanh vùng (Tự dò / Chụp khung / Xoá hết vùng), 2 cách xoá (PillTabs
  `hs_pills`, combo `hs_mode` ẩn giữ trạng thái, chỉ hiện tuỳ chọn của cách đang chọn), 3 Xem trước kết quả.
  `player.HardsubOverlay` (con của video item): che dải màu đúng như xuất; làm mờ / AI / delogo làm mờ gần đúng
  từ khung hình đang hiện (`videoSink().videoFrameChanged`); viền nét đứt khi đang ở tab Xoá phụ đề.

- 2.0.16: tab **Tài liệu** (giữa Lồng tiếng và Xuất; Xuất giờ là tab số 7): `trami/models.py` (MODELS, `status_all`,
  `is_ready`, `download`) + `gui/models_tab.py`. Tải bằng đúng cơ chế của từng thư viện vào đúng cache của nó
  (audio-separator `download_model_files` → data/models/separator, ModelScope `snapshot_download`, faster-whisper
  `download_model` → data/models, SpeechBrain → data/models/spkrec-ecapa, LaMa urllib, VieNeu qua tiến trình riêng
  `VieneuWorker.ensure_started` – worker gửi `{"progress","msg"}`). Tiến độ có %, MB, MB/s (`progress_hook`).
  Sửa thông báo gây hiểu nhầm "Đang tải … (lần đầu)" → "Đang nạp …" (mô hình KHÔNG tải lại mỗi lần chạy).

- 2.0.17: tab Tài liệu báo SenseVoice "Chưa tải" dù đã tải → `models.ms_location` tìm mô hình ModelScope ở mọi kiểu thư mục
  (`hub/<org>`, `hub/models/<org>`, tên có `.`→`___`, rồi tìm sâu `rglob`), chỉ cần có file trọng số > 1 MB.

- 2.0.18: **OmniVoice** (bộ đọc thứ 3, nhái giọng từ file ghi âm mẫu): `pip install omnivoice` (cần transformers>=5.3,
  không đổi torch). `trami/voices.py` thư viện giọng mẫu (data/voices/<id>.wav 24 kHz ≤10 s + index.json: name, text = lời
  trong file mẫu). `trami/omni_worker.py` tiến trình riêng (`TraMi.py --omni-worker <repo>`, `tts.OmniWorker` kế thừa
  VieneuWorker; HF_TOKEN qua env), `OmniVoice.from_pretrained(repo, device_map, dtype)`, `create_voice_clone_prompt`
  (cache theo file+lời; lời trống → `load_asr_model()` Whisper turbo), `generate(language="vi")` → 24 kHz.
  Mã giọng: `omni:<id>` (giọng mẫu) / `omniclone` (cắt mẫu ≤10 s từ câu nguyên vẹn của nhân vật + lời gốc,
  `tts.build_reference_text`). GUI: `gui/voice_samples.py` (thêm file, ghi âm micro QMediaRecorder, lấy 8 s từ video),
  Cài đặt: `omnivoice_model` (k2-fsa/OmniVoice | g-group-ai-lab/g-omnivoice – gated) + `hf_token`; tab Tài liệu có mục OmniVoice.

- 2.0.19: **khớp lồng tiếng 5 tầng** + sửa lăn chuột lần 2.
  * Lăn chuột: combobox KHÔNG BAO GIỜ đổi khi lăn; ô số/thanh trượt chỉ nhận lăn ngay sau khi vừa bấm vào chính nó
    (`WheelGuard.armed`; cú bấm được chuyển tiếp lên khung chứa không xoá `armed`). Lỗi cũ: ô giữ focus mãi sau 1 lần bấm.
  * T1 `trami/align.py.refine`: mốc từng chữ bằng FunASR `fa-zh` (`generate(input=(clip, "字 字"), data_type=("sound","text"))`),
    dự phòng `char_times_runs` (độ to giọng, cụm tiếng cách ≥0,35 s, ưu tiên cắt ở dấu câu); thu mốc câu, cắt ở chỗ nghỉ.
    Chạy trong `pipeline.recognize` (sau ASR) qua `align_segments`; `pipeline.realign` + nút "Căn khớp thoại" (tab Phụ đề).
  * T2 `align.split_speaker_changes` (ECAPA cửa sổ 1,2 s bước 0,4 s, cos < 0,3) trong `diarize.assign_speakers(split_changes=True)`.
  * T3 `translate`: câu gửi AI kèm `⟪≤N tiếng⟫` (N = thời lượng × 4,6 × 1,1, `budget_for`), SYSTEM_PROMPT luật 8, bỏ ký hiệu
    khỏi kết quả; cache thêm theo câu gốc. `Translator.shorten` + `pipeline.dub` tự rút gọn câu `too_long` rồi đọc lại 1 lần.
  * T4/T5 `tts.synthesize_dub` xếp giọng: không bao giờ chồng; mượn ≤0,15 s trước; câu sau lùi ≤0,25 s; tăng tốc ≤ `tts_max_speedup`
    (mặc định 1,3); OmniVoice đọc lại với `duration=` (worker nhận `duration`); vẫn thừa → nhỏ dần ≤0,3 s + cắt, `fit=long`.
    `seg.extra["fit"]` ok/fast/long → cột "Độ vừa" (`subs_model.C_FIT`); `p.data["dub_layout"]` → dải giọng lồng trên Timeline.
  * Cài đặt: `align_words`, `split_speaker_changes`, `dub_fit`, `dub_auto_shorten`; tab Tài liệu có "Căn thời gian từng chữ – FA-zh".

- 2.0.20: tool SẬP "Fatal Python error: libshiboken: seterror_argument failed to call update_mapping" sau khi phân biệt
  người nói: SpeechBrain để module lười (`speechbrain.integrations.k2_fsa`…) trong sys.modules, chạm vào là ImportError;
  khi bất kỳ lời gọi PySide6 nào sai kiểu tham số, shiboken duyệt sys.modules → chết. Kích hoạt bởi
  `WheelGuard` gọi `isAncestorOf(armed)` với ô đã bị xoá. Sửa: `diarize.drop_broken_lazy_modules()` (gọi sau khi nạp
  ECAPA + sau mỗi tác vụ trong `jobs`), `WheelGuard.eventFilter` bọc try/except + `shiboken6.isValid(armed)`.
  Đã tái hiện đúng lỗi trên sandbox và kiểm chứng bản sửa.

- 2.0.21: tab Tự động – thẻ "Làm từng bước" đổi theo đúng quy trình người dùng muốn: 1 "Nghe & tách câu" (ASR + căn mốc
  đầu–cuối câu + tách người nói), 2 "Dịch", 3 "Lồng tiếng" (chạy luôn, trước chỉ chuyển tab), 4 "Ghép vào video & xuất"
  (chạy `run_render`). Dưới mỗi nút có trạng thái ✓ (`_update_steps`). Chữ "&" trong nút phải viết "&&".

- 2.0.22: mô hình bị TẢI LẠI: (1) `MODELSCOPE_CACHE` chỉ được đặt trong `asr_funasr._model` (data/models/funasr) nhưng tab
  Tài liệu tải trước đó vào ~/.cache/modelscope -> 2 chỗ. Giờ đặt ngay khi import `config`; `models.ms_location` tìm cả 2;
  `models.ms_local_path` + `_model` truyền THƯ MỤC trên máy cho AutoModel (không hỏi mạng, không tải lại).
  (2) `align.refine` thử nạp fa-zh lại ở mỗi câu khi lỗi (chỉ tắt nếu lỗi ở câu 0) -> giờ `fa_usable()` thử đúng 1 lần
  (`_fa_error` ghi nhớ). Nơi cất mô hình: ModelScope -> data/models/funasr (hoặc ~/.cache/modelscope cũ);
  tách giọng/LaMa/Whisper/ECAPA -> data/models; VieNeu/OmniVoice -> ~/.cache/huggingface/hub.

- 2.0.23: "có câu dịch, câu không" (video anime có nhạc nền suốt). Chẩn đoán trên video người dùng (OCR phụ đề cứng làm
  chuẩn 48 câu, SenseVoice ONNX + tách giọng MDX trong sandbox): 10 câu KHÔNG có trong danh sách phụ đề vì bộ chia đoạn
  (VAD) bỏ sót, dù SenseVoice nghe đúng 100% khi đưa đúng đoạn -> không phải lỗi dịch / tách nền.
  * `align.missed_speech / rescue_missed` + `pipeline.rescue_segments` (cài đặt `asr_rescue`): sau ASR, tìm đoạn CÓ TIẾNG
    trên giọng đã tách (độ to) mà chưa câu nào phủ, nghe lại từng đoạn (`asr_funasr.sensevoice_decoder`, `asr.whisper_decoder`;
    Paraformer dùng SenseVoice nếu đã tải). Bỏ mẩu 1 chữ, tiếng thở/ho, câu trùng câu sát bên (±1 s, giống ≥0,75).
    Chỉ chạy khi đã tách giọng. Kết quả trên video mẫu: thiếu 10 -> 0 câu; khi phụ đề đủ chỉ thêm câu thật bị thiếu.
    `p.data["asr_rescued"]` -> thông báo "nghe lại được thêm N câu bị sót".
  * Dịch: `translate.untranslated` (rỗng / còn ≥2 chữ Hán) -> AI hỏi lại; cache KHÔNG lưu bản dịch rỗng/còn chữ Hán
    (trước đây 1 lần lỗi là câu đó rỗng mãi). `translate_warning` báo số câu chưa dịch + nhắc "Dịch dòng trống".
  * OCR: `ocr.static_marks/_is_mark` bỏ chữ đứng yên gần suốt video (watermark "冬冰 bilibili" từng dính vào mọi câu,
    kể cả khi đọc lệch "水"/"冬"/"冬冰b#"); `subtitle_height` bỏ chữ thấp < 70% chữ phụ đề chính (hoa văn áo đọc thành "米").
    Video mẫu: 89 dòng có rác -> 52 dòng sạch.
  * Tách nền: audio-separator ≥0.44 (`separate.UPGRADE_CMD`). Mặc định mới `vocals_mel_band_roformer.ckpt` (Mel-RoFormer Kim,
    SDR giọng 12,6 theo models-scores.json của thư viện, BS-RoFormer 11,8; 913 MB tải từ GitHub). Chế độ kết hợp
    `ens:<a>+<b>` (BS + Kim, `ensemble_algorithm="avg_wave"`). Sửa lỗi lấy nhầm stem: Mel-RoFormer ghi "(Other)" trước, tên
    mặc định chứa "vocals" (tên mô hình) -> giờ nhận theo đúng tên stem; "Other" -> instrumental (trừ Demucs .yaml).
    Bản thư viện cũ thiếu mô hình -> tự dùng BS-RoFormer. Tab Tài liệu thêm `sep_kim`.
  * Ứng viên chưa làm: Bandit v2 (kwatcharasupat/bandit-v2, Apache-2.0; checkpoint đa ngôn ngữ 149 MB có trên GitHub
    TRvlvr) tách lời thoại / nhạc / tiếng động cho phim – audio-separator chưa hỗ trợ, cần mã ZFTurbo/Music-Source-Separation-Training (MIT).

- 2.0.24: dịch "không liền mạch": tab Phụ đề toàn mẩu câu ("go给发" / "钱" / "对", "这臭中转站到底黑了我" | "们多少优惠啊")
  + 16 người nói -> Google dịch từng mẩu riêng. Tái hiện trên video mẫu (SenseVoice ONNX + căn theo độ to). Nguyên nhân:
  `split_long` chia đoạn VAD theo TỈ LỆ số chữ trước khi căn; `refine` cắt ở mọi khoảng nghỉ ≥0,35 s theo mốc ước lượng;
  `_split_text` dồn số/chữ Latin sau chỗ cắt vào câu trước ("爸最好了，6" | "块钱"); `split_speaker_changes` cắt tại chữ
  gần chỗ đổi giọng theo mốc chia đều (cắt đôi "我们"); mẩu ngắn -> ECAPA không tin cậy -> người nói ảo.
  * SenseVoice `split=False` khi bật căn thời gian (zh/yue/auto): giữ nguyên đoạn VAD, `align.refine(max_chars=)` tách câu
    bằng `plan_cuts`: cắt ở dấu câu (。！？ và ，；：), ở chỗ nghỉ ≥0,6 s CHỈ khi có mốc thật fa-zh (không có số/Latin xen
    giữa), gộp mẩu ≤2 chữ vào câu sát (nghỉ <0,6 s; qua 。！？ chỉ mẩu 1 chữ <0,3 s), câu > max_chars cắt ở chỗ nghỉ dài nhất
    (fa-zh) hoặc ranh giới từ jieba gần giữa. Căn lỗi -> `pipeline.split_lines` (cách cũ). Không đo được mốc mà dài -> split_long.
  * Mốc từng chữ fa-zh lưu tạm `extra["ct"]` cho `split_speaker_changes` (chỉ cắt ở dấu câu hoặc chỗ nghỉ ≥0,15 s trong
    ±0,8 s quanh chỗ đổi giọng; không có -> không cắt); xoá trước khi lưu (`align.drop_char_times`, `pipeline._drop_char_times`).
  * `rescue_missed`: câu ngắn (≤2,5 s, ≤8 chữ) nằm sát cửa sổ nghe lại -> nghe gộp cả hai, nếu dài hơn ≥2 chữ thì thay
    (extra["absorbed"] -> pipeline bỏ câu cũ): "不要再。" + "说。" -> "不要再说了。".
  * `diarize.cluster(durs=)`: cụm ≤2 câu hoặc <3 s gộp vào cụm lớn gần nhất nếu giống > ngưỡng−0,2; câu <0,8 s không lấy giọng.
  * Video mẫu sau sửa: 51 dòng, mỗi dòng 1 câu/vế trọn vẹn (OCR phụ đề gốc 52 dòng).

- 2.0.25: tab Lồng tiếng làm lại theo yêu cầu người dùng.
  * Mục 1: "Tải MP3 giọng / nhạc nền" (`_save_stem_mp3` -> `video.extract_audio_file`), "Nạp file giọng / nhạc nền…"
    (`pipeline.import_stems`: tách sẵn bằng CapCut/UVR; thiếu nhạc nền -> tiếng gốc − giọng bằng amerge+pan; model.txt
    "external"). Mục 3: "Tải MP3 bản lồng" (bản trộn `dub_preview_audio`). CapCut KHÔNG có API công khai cho tách giọng.
  * Mục 2: bảng người nói tự co theo số người (≤6 dòng) + BẢNG CÂU (`dub_table`, cao ≥480, dùng chung `SubsModel` với tab
    Phụ đề qua proxy riêng `dub_proxy`): Chọn | # | giờ | Người | Giọng | Tiếng Việt (sửa được, tooltip có câu gốc) | Độ vừa
    | Bỏ lồng. Cột mới của model: `C_SEL` (tích, chỉ trong phiên – `model.selected`, `picked` signal; bảng Phụ đề ẩn) và
    `C_VOICE` (giọng riêng câu = `extra["voice"]`, `VoiceDelegate`, chữ cam). Sửa ô Tiếng Việt -> `extra["trans_user"]`
    (AI tự rút gọn trong `dub` bỏ qua câu này; dịch lại bằng AI thì xoá cờ). Nút: Tích câu đang lọc / Tích câu ⚠ quá dài /
    Bỏ tích, Đổi giọng, AI rút gọn (`pipeline.shorten_lines`, cần engine AI), Nghe câu (`_play_line`, dừng ở `_stop_at`),
    "Tạo lại giọng các câu đã tích", Xuất SRT gốc / đã dịch / song ngữ, "Nạp SRT đã dịch…" (`pipeline.apply_translation`:
    cùng số câu + mốc lệch <1 s -> theo thứ tự, không thì theo thời gian trùng nhiều nhất; bỏ dòng chữ Hán của file song ngữ).
  * Sửa 1–2 câu video dài: `pipeline.redub(p, rows)` tăng `extra["take"]` của câu chọn (hash TTS có `take`) rồi
    `dub(inplace=True, only=rows)`: ghi đè bản lồng ĐANG DÙNG (file tạm `_new.wav` rồi replace; bị khoá -> bản mới),
    cập nhật mục trong `dub_versions`; câu khác dùng lại file đã đọc + `tts._decode_cached` (.npy int16 cạnh file) nên
    nhanh. Xong job "redub": `job_finished` -> trộn lại bản nghe thử (mtime đổi) -> `set_alt_audio` giữ vị trí ->
    tự phát câu đầu vừa làm (`_play_after_mix`). Đã chạy thử qua hàng đợi thật (TTS giả): chỉ đọc lại 2/30 câu.
  * Tab Tự động: công cụ dịch "Không dịch" (`pipeline.NO_TRANSLATE`): auto chỉ nghe + tách câu, xuất SRT gốc (.zh.srt),
    không dịch/lồng/xuất video; bấm Dịch khi đang chọn -> báo. `dub_preview_audio` dùng file tạm riêng mỗi lần trộn (2 lần
    trộn cùng lúc từng làm FileNotFoundError).

- 2.0.26: "nạp SRT mới, tạo lại câu đã tích nhưng không áp dụng vào video": job lỗi "Bản lồng tiếng tạo ra không có tiếng
  nào. Câu 6: 403 … Sec-MS-GEC-Version=1-143…". (1) Microsoft đổi xác thực Edge TTS sang Sec-MS-GEC v2 – edge-tts 7.3.0
  (13/10/2026) thêm v2; bản cũ bị 403 mọi câu -> requirements `edge-tts>=7.3.1`, `tts.EDGE_403` / `EDGE_UPGRADE` báo lệnh cập
  nhật, gặp 403 thì ngừng thử các câu còn lại. (Sandbox bị chặn speech.platform.bing.com nên không gọi thử được.)
  (2) Nạp SRT làm đổi lời MỌI câu -> redub đọc lại cả video (hash đổi). Giờ `synthesize_dub(only=)`: câu không tích dùng
  `extra["tts_file"]` (giọng đang có, lưu sau mỗi lần xếp) dù lời đã đổi; câu tích đọc lỗi -> giữ giọng cũ
  (`stats["kept_old"]`, `["redone"]`), bản lồng vẫn được cập nhật. Bấm "Tạo bản lồng tiếng" (đầy đủ) mới đọc mọi câu đổi lời.
  (3) `apply_translation` trả về chỉ số câu ĐỔI lời; GUI tích sẵn các câu đó sau khi nạp SRT.

- 2.0.27: người dùng hỏi "sửa câu có hiện bản mới ở mục 4 không?" – không: "Tạo lại giọng các câu đã tích" sửa THẲNG vào
  bản đang dùng. Mục 4 giờ ghi rõ "tạo <giờ> · đã sửa N lần, lần cuối <giờ>" (`dub_versions` thêm `created`, `edits`),
  tooltip báo số câu chưa có giọng; mô tả mục 4 nói rõ 2 nút khác nhau.

- 2.0.28: giao diện kiểu Concat (github.com/jub0t/concat – trình dựng video Rust, AGPL; chỉ học bố cục, không lấy mã).
  Trang dự án: thanh trên (tên · "✓ đã lưu HH:MM" · thư mục · xoá · nút "Xuất video" -> tab Xuất); QSplitter ngang 3 khung:
  TRÁI `widgets.IconTabs` (icon trên chữ, chia đều, hẹp thì 2 hàng – thay PillTabs vốn co hình mà không co vùng bấm) + stack
  8 tab cũ; GIỮA "Xem trước" (trình phát + thông số + hộp tiến độ); PHẢI `inspector.LineInspector` (trong scroll, thu gọn
  được): giờ, người nói, Trung, Việt (ghi qua SubsModel sau 0,7 s), giọng riêng, bỏ lồng, độ vừa, Nghe câu / Dịch lại /
  AI rút gọn / Tạo lại giọng câu này, ◀ ▶. DƯỚI (QSplitter dọc) `timeline.TrackTimeline`: track Phụ đề (khối màu người
  nói, chữ Việt), Giọng lồng (màu độ vừa), Video (dải ảnh đúng tỉ lệ), Tiếng gốc (sóng âm); bấm khối = chọn câu, bấm đúp =
  nghe, Ctrl+lăn = phóng, lăn = cuộn; thanh công cụ Thêm/Tách/Gộp/Xoá/Nghe/Tạo lại giọng, −/+/vừa khung; mở video dài
  hiện ~90 s đầu. `pipeline.timeline_assets` tạo `work/tl_thumbs_<n>.jpg` (≤160 ảnh, tile 1 hàng) + `work/tl_wave.npy`
  (sóng âm 0,05 s từ giọng đã tách). Câu chọn đồng bộ mọi nơi qua `project_page._select_line` (`_sel_row`; `_selected_row`
  ưu tiên nó). `main.Sidebar.set_compact` (chỉ icon, rộng 68) khi ở trang dự án. Màn <1600 px: khung phải mở đầu thu gọn,
  timeline ~30% chiều cao (`showEvent`). Icon mới `minus`, `fit`. Đã chạy thử offscreen 1920×1080 + 1366×768.

- 2.0.29: người dùng: "phải có công nghệ nghe và tách đúng để làm phụ đề" + "lồng tiếng chia theo câu phụ đề, ghép
  đúng vị trí, hiện dưới phụ đề trên bố cục mới".
  * **Qwen3-ASR** (Alibaba, 1/2026, Apache-2.0; WenetSpeech net/meeting 4,97/5,88 % so với Whisper-v3 9,86/19,11) + **Qwen3-
    ForcedAligner-0.6B** (mốc từng chữ lệch ~33 ms, fa-zh ~161 ms). Dùng bản `-hf` có sẵn trong transformers ≥ 5.13
    (`Qwen3ASRForConditionalGeneration`, `apply_transcription_request(audio=[np 16k], language=, prompt=)`,
    `decode(return_format="parsed")`; bộ căn `Qwen3ASRForTokenClassification`, `prepare_forced_aligner_inputs`,
    `decode_forced_alignment`). KHÔNG dùng gói `qwen-asr` (ghim transformers==4.57.6, xung đột OmniVoice ≥ 5.3).
    `asr_qwen.transcribe_qwen`: VAD (fsmn nếu có, không thì `align.speech_spans`) -> `align.chunk_spans` (gộp đoạn nghỉ ≤ 1 s
    thành khúc ≤ 28 s để nghe trọn câu; khúc dài cắt ở chỗ nghỉ dài nhất / chỗ lặng nhất) -> worker {"cmd":"asr", align}
    -> mỗi khúc 1 Segment có `extra["ct"]`. `align_qwen` đo mốc cho câu chưa có (SenseVoice cũ, câu nghe lại, nút Căn
    khớp thoại); `qwen_decoder` cho nghe lại đoạn sót; `hotwords` lấy chữ Trung trong bảng thuật ngữ làm gợi ý.
    Lỗi / chưa cài -> SenseVoice + `asr_warning` (hiện sau khi lấy phụ đề). `release()` tắt tiến trình sau khi căn xong.
    Đã chạy thật trên sandbox bằng mô hình tí hon trọng số ngẫu nhiên (transformers 5.19) – đúng API; mô hình thật chưa chạy.
  * SenseVoice: FunASR `output_timestamp=True` (CTC forced align, `_timestamps_ok` thử trước) -> cũng gộp khúc + `ct`.
  * **Tách câu mới** `align.plan_cuts`: luôn tách ở 。！？, ở dấu phẩy CHỈ khi sau nó nghỉ ≥ `PAUSE_COMMA` 0,45 s (hết câu /
    đổi lượt), chỗ nghỉ ≥ `PAUSE_HARD` 0,8 s (có mốc thật); câu > `asr_max_chars` (mặc định 22 -> 28) hoặc > `MAX_DUR` 8 s
    chia ở phẩy / chỗ nghỉ gần giữa (`best_cut`), không thì ranh giới từ jieba. `split_timed`, `fix_overlaps`,
    `times_from_words` (token nhiều chữ chia đều) + `plausible_times` (bỏ mốc vô lý > 12 chữ/s hoặc dồn 1 chỗ).
    Video mẫu (SenseVoice ONNX có mốc): câu gốc OCR bị cắt vụn 8 -> 2, 44 -> 39 dòng. `mark_songs` chạy sau khi tách
    (nhãn Sing của cả khúc: câu > 3,2 chữ/s không coi là hát; cờ `chunked` xoá trước khi lưu).
  * **Lồng tiếng từng đoạn**: (vốn đã đọc từng câu rồi xếp) giờ hiện rõ: timeline track "Lồng tiếng" cao 38, mỗi câu 1 khối có
    sóng âm thật (`tts.wave_envelope` -> `<dub>.env.npy`, `pipeline.dub_wave` tạo cho bản cũ), màu độ vừa, `#n`; câu không
    có giọng: khung nét đứt + lý do. Kéo khối -> `extra["dub_shift"]` -> `pipeline.relayout` (= `dub(inplace, only=[])`,
    không đọc lại câu nào) -> trộn lại bản nghe thử + phát câu đó. Chuột phải: Nghe / Đặt lại vị trí / Tạo lại giọng.
    `synthesize_dub` xếp theo `want = start + dub_shift`; câu `keep` chưa có `tts_file` (bản trước 2.0.26) dùng file hash.

- 2.0.30: "căn chỉnh lại khung bo và khoảng cách giữa các khung, nhìn không đều". Đo toạ độ khung (offscreen, CÓ
  stylesheet + font như `run()` – ảnh chụp thiếu theme làm ô chọn/nút trắng): khung Xem trước thụt 9 px (lề mặc định của
  `wrap(ccol)`), timeline dính sát hàng trên (stylesheet chỉ đặt `width` cho handle), khung phải thu gọn bằng kích thước 0
  vẫn để lại vách 8 px -> lệch mép phải; lề trong các khung 6/8/12/14 px. Sửa: `theme.GAP = 10` (khe ngang = dọc,
  `setHandleWidth`), `theme.PANEL_PAD = 10` cho mọi khung lớn, `ccol` lề 0, nút `panel_right` trên thanh đầu ẩn/hiện
  hẳn khung "Câu đang chọn" (`_show_inspector`; màn < 1600 mở đầu ẩn). `widgets.FlowLayout` / `flow()`: hàng nút tự
  xuống dòng (tab Phụ đề 2 hàng, tab Lồng tiếng: trạng thái tách, đổi giọng câu tích, SRT, nghe/tải bản lồng);
  dòng thống kê `d_status` và nhãn bản lồng `wrap=True` (trước đẩy thẻ rộng 1040 px, mất viền phải). Khung công cụ
  min 700 px. Inspector: 3 nút lưới 2 cột, ô Tiếng Việt `_TextBox` (sizeHint nhỏ -> không tràn thanh cuộn, giãn lấp
  khung). Timeline: các dòng co 0,7–giãn 1,3 theo chiều cao (`_Canvas._scale`), khung timeline không nhỏ hơn
  `minimumSizeHint`. Sidebar thu gọn: icon xếp sát nhau (`_filler`), không rải khắp chiều cao.
  Kiểm tra lại ở 1920×1080 / 1600×1000 / 1366×768: mọi khung cùng mép trên/dưới/phải, khe 10 px.

- 2.0.31: người dùng hỏi "2 mục Qwen3 ở tab Tài liệu tải xong rồi dịch có phải tải lại không?". Cùng kho: tab Tài liệu
  `snapshot_download(repo, allow_patterns=models.HF_FILES)` -> kho HuggingFace (`HF_HUB_CACHE`), tiến trình Qwen3 nạp theo
  tên repo -> cùng kho. Lỗi tìm ra (dựng kho giả + mô hình tí hon, mạng HF bị chặn): `from_pretrained(repo)` luôn hỏi mạng
  trước -> mạng chặn / chập chờn là LỖI dù đã tải. Sửa `qwen_asr_worker._load`: thử `local_files_only=True` trước, thiếu mới
  tải. `models._hf_complete`: có config.json + đủ file trọng số theo `model.safetensors.index.json` (tải dở -> "Chưa tải");
  tải xong mà thiếu file -> báo bấm Tải về lần nữa. Đổi mô hình 1.7B/0.6B ở Cài đặt thì phải tải bản kia.

- 2.0.32: OCR phụ đề cứng "quét nhầm / sót câu". Kiểm tra trên video mẫu (so với OCR MỌI khung) + video thử tự vẽ
  (drawtext, font Noto CJK): cách cũ sót 3/55 câu (cảnh sáng: mặt nạ chữ trắng/vàng chỉ đổi 14 % khi câu mới hiện ->
  coi là câu cũ), chữ HỒNG không bao giờ đọc (mặt nạ chỉ nhận trắng/vàng có viền), phụ đề 2–3 dòng chỉ đọc dòng dưới
  (vùng dò cao 1 dòng), vùng dò x=0,1 cắt câu dài, 2 câu liền giống ≥ 0,7 bị gộp. Sửa `ocr.py`:
  * `_text_signature` = mật độ biên Canny ô 4×4 (mọi màu) + `_changed` (≥ 3 ô đổi > 25 %); OCR lại ít nhất mỗi 1 s.
  * Đọc ngưỡng 0,45; chữ < 0,6 chỉ giữ khi lần đọc khác ±1 s ra giống (≥ 0,8). `_same_line`: cùng câu nếu khác ≤ 1 chữ / 6
    chữ (thay tỉ lệ 0,7). Chữ cùng nội dung mà mỗi lần đọc 1 chỗ (vật có chữ bay qua) -> bỏ.
  * `detect_subtitle_region`: 5 s đầu 4 khung/s, 5 s cuối 2 khung/s, 8 khung giữa; 5 s đầu không có chữ -> quét tới 30 s.
    Watermark = nét chữ y hệt ở khung xa nhất (IoU biên ≥ 0,35 hoặc tương quan ≥ 0,5; đo: watermark 0,52/0,56, phụ đề
    khác câu ≤ 0,19/0,13). Hàng phụ đề = hàng có nhiều câu khác nhau nhất; CÂU ĐẦU TIÊN làm mốc: cao dòng h -> vùng quét
    thêm `UP_LINES` 2,2·h trên + `DOWN_LINES` 1,2·h dưới, ngang 0..1. Trả thêm line_y/line_h (`_anchor_block`: chỉ giữ khối
    dòng liền nhau có 1 dòng đúng hàng phụ đề chính -> bỏ chữ trong cảnh), erase (khung vừa khít để xoá), first_t.
    `expand_region`: vùng người dùng khoanh -> vùng quét rộng tương tự. Vùng xoá gợi ý = `stats["text_box"]` (khung chữ
    thật đã đọc), KHÔNG dùng vùng quét. Cài đặt + tab Tự động: `ocr_fps` "2"/"4"/"8" (`C.OCR_SPEEDS`). `ocr_warning` khi
    0 câu. Kết quả: video mẫu 55/55 câu như OCR mọi khung, video thử nhiều màu 6/6, phụ đề 1/2/3 dòng + câu dài + mở đầu
    8 s 5/5, không lẫn watermark / biển hiệu. Chậm hơn ~1,5 lần (nền chuyển động -> OCR nhiều khung hơn).

- 2.0.33: "kéo phụ đề trên video, chỉnh chiều cao / độ dài nền phụ đề và vùng che bằng 4 chấm trên 4 cạnh, bấm gì để
  chạy xoá AI". Phát hiện: khung xem trước vẽ chữ TO HƠN video xuất ~1,5 lần (libass coi Fontsize = winAscent +
  winDescent của bảng OS/2, Be Vietnam Pro em = 0,655 × Fontsize). `player.ass_font` đọc bảng OS/2 (QRawFont) -> đúng cỡ;
  chiều cao dòng = Fontsize; ngắt dòng bằng `wrap_line` như khi xuất. Đo bằng ffmpeg/libass: hộp nền xem trước lệch ≤ 1 px.
  * `subtitle.py`: `sub_size`, `margin_v_px`, `block_anchor` (tâm ngang + chân khối chữ), `box_pads`, `fixed_box`,
    `cjk_font_name` dùng chung cho `to_ass` và xem trước. Vị trí "custom" (`pos_x`, `pos_y` = tâm ngang / chân khối
    chữ, 0..1 khung xuất) -> `{\an2\pos(x,y)}`. Nền ôm chữ: BorderStyle 3 + `\xbord`/`\ybord` (= `box_pad_x/y` × cỡ
    chữ, mặc định 0,18; lặp lại sau `\rSource`). Dải cố định (`box_fixed`, `box_w`/`box_h` theo khung): Dialogue lớp 0
    vẽ `\p1` hình chữ nhật tâm = tâm khối chữ, chữ lớp 1 không viền. `effective_style`: "custom" giữ chỗ, cỡ chữ tự
    động theo phụ đề cũ như "auto".
  * `player.SubtitleItem`: kéo chữ -> `position=custom` (hít vào giữa khung), 4 chấm trên cạnh nền đổi đệm (ôm chữ) /
    `box_w`,`box_h` (dải cố định), đổi đối xứng quanh tâm; thả chuột -> `Player.subtitleEdited` ->
    `project_page._sub_edited` ghi vào ô tab Kiểu chữ (Vị trí "Tự chọn", Ngang/Dọc %, Kiểu nền, Độ dài / Chiều cao nền).
    `_ovr` giữ giá trị vừa kéo tới khi `set_subtitles` nhận kiểu mới. Tab Kiểu chữ: khung nét đứt + chữ mẫu khi không có câu.
  * `player.HardsubOverlay` chỉnh được (tab Xoá phụ đề, `VideoView.set_tool(regions=)`): kéo trên chỗ trống vẽ vùng
    mới, 4 chấm giữa cạnh + góc đổi cỡ, kéo giữa di chuyển, × xoá; toạ độ item video nên đúng cả khi lật/xoay/zoom.
    -> `Player.regionsEdited` -> `_regions_edited` (đồng bộ `hs_editor`, lưu). Ở tab này phụ đề không nhận chuột.
    `editors.hit_region / drag_region / paint_handles / REGION_CURSORS` dùng chung với `RegionEditor` (cũng có 4 chấm).
    Làm mờ khi video đang dừng: lấy ngay `videoSink().videoFrame()` (trước chỉ mờ khi có khung mới).
  * Xoá AI: `video.inpaint_key/inpainted_file/inpaint_cached` -> `work/render/inpainted_<mã>.mp4` (mã theo video gốc,
    vùng, công nghệ, độ mạnh, file OCR, codec/crf; chỉ giữ 1 bản). `pipeline.hardsub_spec / inpainted_video /
    run_inpaint`; `build_and_render` dùng lại bản đã có ("Dùng lại video đã xoá phụ đề bằng AI"). Tab Xoá phụ đề mục 3
    "Kiểm tra & chạy": Xem thử 1 khung / Về ảnh gốc / **Chạy xoá bằng AI (cả video)** (job "inpaint") + công tắc "Xem bản
    đã xoá" + trạng thái (`_update_hs_status`) + "Đi tới Xuất video". `_sync_clean` (trong `_apply_live`): bản đã xoá khớp
    vùng hiện tại -> trình phát chiếu nó (`Player.load(start=)` giữ vị trí, `hs.clean` tắt hiệu ứng gần đúng), đổi vùng ->
    về video gốc. `HS_HELP` viết lại (HTML) từng cách xoá: dùng khi nào, chạy lúc nào.
  * Đã chạy thử offscreen có theme: kéo phụ đề dời đúng (−150, −200) px, hộp nền khớp libass, vẽ / kéo cạnh / di chuyển
    / × vùng trên video, xoá AI qua hàng đợi (OpenCV) -> trình phát đổi bản, xuất video dùng lại (3,5 s).

- 2.0.34: người dùng đưa github.com/MachHongHai/HaizFlow (app cùng loại; giấy phép "Source-Available" – CHỈ HỌC cách
  làm, KHÔNG chép mã) -> làm 10 mục + "VieNeu thêm giọng, giọng rõ hơn".
  * `trami/voicefix.py`: `spoken_vi` đọc số -> chữ cho bộ đọc AI (VieNeu / OmniVoice, không Edge; phụ đề giữ số):
    nghìn/triệu/tỷ, "linh", "mốt/tư/lăm", thập phân dấu phẩy, %, $, giờ 10:30, ngày d/m/yyyy (không năm thì cần chữ
    "ngày"/"hôm" đứng trước), "thứ 4" -> "thứ tư"; giữ nguyên 1.2.3 / 3.14 / 3/4 / iPhone15. `voice_problem`: im lặng,
    DC, bíp (năng lượng dồn ±2 vạch quanh đỉnh > 80 %, ≥ 500 Hz), còi ≥ 0,32 s 1–4 kHz, ≥ 2 bíp ở 1 s đầu – đo trên 6
    giọng VieNeu thật + 57 đoạn thoại phim: 0 báo nhầm. `speech_level` + `level_gains`: tăng câu nhỏ hơn trung vị cùng
    giọng > 1 dB (giọng nhỏ hơn mức chung > 3 dB cũng kéo lên), tối đa +9 dB, đỉnh ≤ -1 dBFS (`dub_level_match`).
  * `tts.synth_checked`: đọc -> kiểm tra -> hỏng thì đọc lại 1 lần với `seed` khác -> vẫn hỏng thì bỏ câu + báo lỗi.
    BỎ việc bắt OmniVoice đọc lại với `duration=` (HaizFlow đo: ép thời lượng -> tiếng bíp); câu dài chỉ tua bằng
    atempo. OmniVoice `fade_duration=0.0` (đoạn nhỏ dần 0,1 s mất phụ âm cuối); worker nhận `seed`. Mọi tiến trình
    (VieNeu/Omni/Qwen/Hy-MT2) có `HF_DEACTIVATE_ASYNC_LOAD=1` (transformers 5.x nạp song song từng sập ngầm Windows+CUDA).
  * VieNeu thêm giọng: preset xếp theo `featured` (⭐ 10 giọng tiêu biểu đầu), nhãn "nữ miền Bắc, tự nhiên / đọc tin
    tức, rất rõ"; `vieneu:mau:<tên>` = 6 file thu âm sạch trong gói vieneu (assets/samples, Bình/Tuyên/Vĩnh/Đoan/Ly/
    Ngọc) dùng làm ref_audio; `vieneu:lib:<id>` = thư viện giọng mẫu của người dùng (voices.py, khung đổi tên "Giọng
    mẫu của bạn (VieNeu + OmniVoice)"). Kiểu đọc `vieneu_style`: clear (temperature 0,6, top_k 20, top_p 0,9 – mặc định)
    / natural (mặc định VieNeu 0,8/25/0,95); worker nhận `params` (bản cũ TypeError -> bỏ). requirements vieneu>=3.8.3.
    API v3turbo: `infer(text, voice=tên|dict{speaker_emb,codes}, ref_audio=, temperature, top_k, top_p, ...)`.
  * Dịch trên máy **Hy-MT2-1.8B** (tencent, Apache-2.0, GGUF chính thức có sẵn): `trami/hymt_worker.py` (`--hymt-worker`,
    nạp local_files_only trước, bf16/fp16 GPU, fp32 CPU, lô 8, greedy + repetition 1.05), câu lệnh kiểu Hunyuan-MT
    (thường / ngữ cảnh 3 câu trước / thuật ngữ "参考下面的翻译"), `translate.HymtWorker`, engine "hymt" (`LOCAL_ENGINES`:
    không analyze, không rút gọn), tắt tiến trình sau mỗi lần dịch. Tab Tài liệu mục "hymt". Đã chạy thật với mô hình
    HunYuanDenseV1 tí hon trọng số ngẫu nhiên (transformers 5.19) – mô hình thật chưa chạy (sandbox chặn HF).
  * `trami/gender.py`: chất giọng nam/nữ mỗi người nói = mô hình ONNX ECAPA (Alice-Sabrina-Ivy/voice-gender-classifier-
    onnx-q8, MIT, 16 MB, input_values [1,N] 16 kHz -> logits [male, female]; tab Tài liệu "gender") 75 % + cao độ F0
    (YIN tự viết, vector hoá) 25 %; chưa tải mô hình -> chỉ cao độ (6 giọng thật: 5 đúng, 1 nữ trầm 167 Hz -> "chưa rõ").
    `pipeline.speaker_genders` (sau `diarize`, hoặc khi bấm Tự gán giọng) -> `p.data["speaker_gender"]`; bảng người
    nói "S1 · nam". `tts.auto_assign(..., genders)`: nam -> giọng nam, nữ -> nữ, chưa rõ -> xen kẽ; ⭐ dùng trước.
  * Che phụ đề **Vá nền** (`patch`): `video.patch_source` (dải sát trên vùng chữ, thiếu chỗ thì dưới; `patch_from`),
    FFmpeg crop + alphamerge mặt nạ geq viền mờ `patch_feather` + overlay theo từng khung; `inpaint.preview_frame`
    + `HardsubOverlay._paint_patch` xem trước. Thử: chữ 10.703 điểm ảnh -> 0, màu chênh 1,3/255.
  * Nhạc nền thêm tự giảm khi có lời: `bgm.duck` (mặc định bật, tab Chỉnh sửa) -> dub `asplit` làm sidechain,
    `sidechaincompress=threshold=0.02:ratio=8:attack=20:release=400`. Đo: nhạc -16 dB lúc có lời, 0 dB lúc không lời.

- 2.0.35: "tách tất cả chỗ chỉnh âm thanh thành 1 phần riêng (đang trùng nhau ở Chỉnh sửa / Lồng tiếng), chỉnh thật, tác dụng
  ngay trên video". Tab mới **Âm thanh** (`gui/audio_tab.py`, giữa Lồng tiếng và Tài liệu; Chỉnh sửa bỏ khung Âm thanh, Lồng
  tiếng mục 3 bỏ các thanh trộn + công tắc, thêm nút sang tab Âm thanh). 1 nơi lưu: `edit["audio"]` = src (auto/orig/stems),
  orig, bg, voice, songs, dub, bgm, duck, pitch, master, mute[] (`audiomix.audio_settings` suy ra từ khoá cũ cho dự án cũ:
  volume, mute_original, pitch_shift, dub.*_volume, bgm.volume/duck; `collect_edit` vẫn ghi bgm.volume/duck + dub.enabled).
  * `audiomix.gains` (dùng chung): nguồn bản tách -> bg + voice (đoạn hát 🎵 `voice_song` = max(voice, master)), không thì
    orig; × master; lớp tắt = 0. `video.build_and_render` dựng đồ thị âm thanh từ đó (giọng gốc `volume=if(...)` theo đoạn
    hát, nhạc thêm `-stream_loop -1` + sidechaincompress khi duck).
  * **Bộ trộn trực tiếp**: `pipeline.audio_sources/audio_state/live_mix` giải mã mỗi lớp 1 lần thành PCM 44,1 kHz
    (`work/live/<lớp>_<md5>.raw`, memmap) -> `audiomix.LiveMix.render` trộn từng khối ~0,1 s (solo, đoạn hát, nhạc thêm
    lặp theo thời gian SAU khi cắt + giảm −16 dB khi có lời, mờ dần, tốc độ) -> `player.LiveAudio` (QAudioSink đẩy PCM,
    timer 15 ms, đệm 0,2 s, bám video: lệch > 0,15 s thì nhảy). Có bộ trộn -> tắt tiếng video. Kéo thanh / tắt / Nghe riêng
    -> `project_page._apply_audio_now` -> đổi `mix.g` -> nghe ở khối kế tiếp. Đổi file / cao độ -> giải mã lại (vài giây).
    Bỏ `dub_preview_audio` + `set_alt_audio` (m4a trộn sẵn). Không có loa (máy thử) -> `LiveAudio.failed`, video tự bật tiếng.
  * Đo: xuất video vs bộ trộn trực tiếp lệch ≤ 0,44 dB mỗi lớp (nhạc nền, giọng gốc thường / đoạn hát, lồng tiếng, nhạc
    thêm có / không lời). Tải MP3 bản trộn = `pipeline.mix_audio_file` (đúng LiveMix, khối 10 s).
  * Lồng tiếng lần đầu khi chưa tách mà tiếng gốc > 50 % -> tự hạ tiếng gốc 15 % (như mặc định cũ).
  * `widgets.FlowLayout`: các ô trong 1 hàng căn giữa theo chiều dọc. Chữ hướng dẫn "video bên trái" -> "bên phải".

- 2.0.36: người dùng thích font phụ đề của HaizFlow -> đó là **Bangers** (Vernon Adams, OFL, Google Fonts; HaizFlow khoá cứng
  font này + karaoke trắng -> vàng #FFEF00, viền đen ~9 % cỡ chữ). Tải bản gốc từ google/fonts (md5 trùng bản HaizFlow) vào
  `fonts/` + `fonts/Bangers-OFL.txt`. Bangers chỉ có chữ gốc + dấu rời (không có à, ế…), HarfBuzz (libass và Qt) tự ghép
  dấu -> tiếng Việt hiện đúng cả khi xuất lẫn xem trước (đã chụp so sánh). Font chỉ có kiểu chữ hoa.
  * `trami/karaoke.py` (dùng chung xuất + xem trước): `tokens` (từ + phần nối; chữ Hán mỗi chữ 1 đơn vị), `word_times`
    (trọng số 1 + 0,12·chữ cái + 0,9·chữ số; nghỉ ở dấu câu `PAUSE`; có đoạn giọng lồng -> số chỗ ngắt hơi ≥ 0,18 s = số vế
    câu thì mỗi vế chạy trong đúng đoạn giọng của nó, không thì rải trên phần có tiếng; giọng dài hơn câu -> co lại),
    `progress` (\\kf quét dần / \\k đổi khi tới từ), `ass_text` (chờ bằng `{\\k n}` rỗng), `voiced` (đoạn có tiếng từ
    sóng âm 0,05 s). `pipeline.karaoke_spans` = đoạn giọng mỗi câu từ `dub_layout` + `dub_wave` (chỉ khi lồng tiếng bật,
    không tắt lớp, âm lượng > 0); `render` gắn `extra["kv"]`, `video` giữ lại, `remap_after_cut` / `scale_time` dời theo.
  * `subtitle.to_ass`: karaoke -> Default Primary = màu karaoke, Secondary = màu chữ. Nền ôm chữ + karaoke: libass vẽ nền
    riêng từng từ (chồng thành sọc tối) -> lớp 0 nền + chữ ẩn (`\\1a&HFF&`), lớp 1 kiểu `Kara`/`KaraSrc` (không nền).
    `video.py` chép font mới vào `work/render/fonts` mỗi lần xuất (trước chỉ chép khi thư mục chưa có).
  * Xem trước: `SubtitleItem.kara` (tiến độ từng từ) -> điểm cắt mỗi dòng theo bề rộng chữ, tô 2 màu bằng clip;
    `Player._kara_timer` 33 ms + đồng hồ nội suy khi phát; `project_page._karaoke_spans` tính ở luồng nền (nhớ theo bản
    lồng + bật/tắt). Đo: khung xem trước và video xuất cùng chỗ, cùng đoạn đã đổi màu.
  * Tab Kiểu chữ: font "Bangers – kiểu truyện tranh", "Karaoke – chữ đổi màu theo lời", Màu karaoke, Kiểu karaoke
    (Quét mượt / Đổi màu từng từ), mẫu nhanh "Karaoke vàng" (Bangers, viền 4,5, bóng 1,5, không đậm); mẫu khác tắt karaoke
    + bỏ Bangers. `_set_style_widgets` dùng chung khi mở dự án và mẫu nhanh. Cài đặt: `karaoke`, `karaoke_color`, `karaoke_mode`.

- 2.0.37: người dùng hỏi HaizFlow có giọng nào: OmniVoice 40 giọng "chọn sẵn" (thực chất ~18 mô tả `instruct`, nhiều giọng
  trùng mô tả chỉ khác hạt ngẫu nhiên; mỗi giọng đọc 1 câu mẫu rồi nhái lại cho cả video) + nhái giọng; VieNeu 25 preset (TraMi
  có đủ, cùng lấy từ gói vieneu); Edge 2 giọng Việt. Thêm cho TraMi **23 giọng OmniVoice chọn sẵn** (tự viết danh sách):
  * `trami/omni_design.py`: `PRESETS` (mã, tên, nam/nữ, mô tả, hạt, ⭐) chỉ dùng từ OmniVoice 0.2.1 hiểu (`_resolve_instruct`:
    male/female · child/teenager/young adult/middle-aged/elderly · very low…very high pitch · whisper; đã kiểm bằng chính
    hàm của gói, 23/23 hợp lệ, không trùng mô tả). `ensure_anchor`: đọc `ANCHOR_TEXT` (câu Việt ~6 s) với `instruct` + hạt cố
    định 1 lần -> `data/voices/design/<mã>_<key>.wav` (key = mô tả|hạt|mô hình|câu mẫu); câu mẫu hỏng (`voice_problem`)
    -> hạt khác, tối đa 3 lần. Mọi câu lồng tiếng nhái câu mẫu (`ref_audio` + `ref_text`) -> cả video 1 giọng.
  * `tts`: mã giọng `omnid:<mã>`; `list_voices` thêm sau giọng mẫu / nhái giọng; `auto_assign` (OmniVoice) = giọng mẫu
    của bạn + giọng chọn sẵn có nam/nữ (trước: chỉ giọng mẫu hoặc nhái giọng gốc); `synthesize_dub` hash có `omni_design.key`,
    câu mẫu lỗi nhớ lỗi (không thử lại mỗi câu); `preview_voice`; `VieneuWorker.synth(instruct=)` (omni_worker đã nhận).
  * Thử với OmniVoice giả (kiểm từ vựng như bản thật): mỗi giọng tạo câu mẫu 1 lần, các câu sau `voice_clone_prompt` từ câu
    mẫu, sửa 1 câu chỉ đọc lại câu đó, nghe thử chạy, giao diện Tự gán giọng S1·nữ -> Nữ trẻ, S2/S3·nam -> Nam trẻ / Nam trầm.

- 2.0.38: "nhiều câu mất chữ cuối rồi nhảy sang câu sau" (vd. "…làm máy treo tận 3 tiếng." chỉ nghe tới "tận 3"; cột Độ vừa ✓
  -> bộ xếp giọng KHÔNG cắt câu này). 3 nguyên nhân trong tool:
  * `player.LiveAudio` (2.0.35) đẩy PCM trên LUỒNG GIAO DIỆN, đệm 0,2 s: chuyển câu -> giao diện bận (cuộn bảng câu, vẽ
    timeline, karaoke) -> loa cạn tiếng -> `sync` thấy lệch > 0,15 s -> nhảy theo hình = BỎ QUA đuôi câu. Giờ `_Feeder`
    (QObject ở QThread riêng "trami-audio", ưu tiên TimeCritical) tạo QAudioSink + QTimer 10 ms (Precise) trong luồng đó;
    giao diện gửi lệnh qua `_Bridge` (signal begin/end/volume); `_audible` do luồng phát cập nhật; đệm 0,3 s, nhảy khi lệch
    > 0,25 s; `ready()` dựng luồng 1 lần (`aboutToQuit` -> `shutdown`). Mô phỏng loa thời gian thật + giao diện bận 0,5 s
    mỗi lần chuyển câu (Python giữ GIL): cách cũ cạn tiếng 6 lần / 1,54 s, cách mới 0.
  * "Nghe câu" (`_play_line`, cả nghe từ timeline / khung câu) dừng ở cuối PHỤ ĐỀ + 0,35 s, trong khi giọng Việt hay dài hơn
    (lấn tới đầu câu sau + 0,21 s) -> giờ dừng ở cuối đoạn giọng lồng thật (`dub_layout`) + 0,25 s.
  * `tts._trim_silence` ngưỡng cố định 0,01 + giữ 0,06 s đuôi: giọng nhỏ mất phụ âm / hơi cuối chữ -> ngưỡng
    min(0,01; 3 % đỉnh của đoạn), giữ 0,15 s đuôi, 0,03 s đầu.
  * Nếu chữ thiếu ngay trong file bản lồng ("Nghe bản lồng") thì là lỗi bộ đọc (VieNeu tự ngắt sớm) – chưa có cách tự phát
    hiện (đề xuất: kiểm tra lời đọc bằng Whisper tiếng Việt).

- 2.0.39: "đặt Cỡ chữ 22 thì vài câu giọng lồng bị giật, để 0 hoặc < 10 thì không". Đo `SubtitleItem.paint` (offscreen):
  không nền hộp mà có bóng đổ (> 0,3 px trên khung xem) -> 47–82 ms MỖI lần vẽ (Bangers 266 ms) do dựng viền + ghép bóng
  bằng `QPainterPath.united`; viền ≥ 1 px mà không bóng ~7 ms; nhỏ hơn ~1 ms -> nặng / nhẹ theo cỡ chữ, câu dài nặng hơn.
  Và phụ đề bị vẽ lại MỖI lần trình phát báo vị trí dù chữ không đổi -> luồng giao diện nghẽn, giữ GIL -> loa giật.
  * `SubtitleItem._sprite`: vẽ chữ 1 lần thành QImage (theo devicePixelRatio, khớp lưới điểm ảnh) – bóng = lớp đen đặc
    riêng (viền + chữ dời đi) phủ độ trong 0,6 (không cần `united`), viền, màu chữ; karaoke có thêm ảnh tô màu karaoke, dán
    theo vùng cắt từng dòng. Khoá ảnh = chữ / font / vị trí / viền / bóng / dpr / màu karaoke.
  * `SubtitleItem.set`: cùng câu + cùng kiểu -> không vẽ lại; chỉ karaoke đổi -> `update(vùng chữ)`. `clear_override`
    chỉ vẽ lại khi thật sự có giá trị kéo dở.
  * Đo lại: 0,05–0,12 ms mỗi lần vẽ; so từng điểm ảnh với cách cũ: lệch tối đa 17–33/255 ở vài điểm mép bóng (mắt không
    thấy); khớp hộp nền libass, kéo phụ đề, karaoke xem trước = video xuất như trước.

- 2.0.40: (1) **Tốc độ đọc** cho câu đã tích: `extra["speed"]` = mức TỐI THIỂU – `synthesize_dub` tốc độ = max(đặt tay,
  cần để vừa chỗ) (chậm < 1 khi còn chỗ), `fit` "ok" nếu không vượt mức đặt; chỉ xếp lại (`pipeline.relayout`, không đọc
  lại). Tab Lồng tiếng: ô `d_speed` + "Đặt tốc độ" / "Về tốc độ gốc" (`project_page._apply_speed`); cột Độ vừa "✓ ×1.30 ✎";
  khối giọng trên timeline ghi "#n ×1.30". (2) **Timeline** (`gui/timeline.py`): chọn nhiều (`sel_track`, `sel_rows`,
  `sel_pieces`, `sel_gap`, `sel_insert`; Ctrl bật/tắt, Shift dải, kéo quét trên chỗ trống `select_span`, bấm tên dòng
  `select_all`, Delete / Ctrl+A / Esc), bấm chỗ trống dòng Phụ đề = chọn khoảng trống -> "Thêm phụ đề" đúng chỗ
  (`_sub_add`, tối đa 2,5 s, focus ô Tiếng Việt). Thước = tua (trước kéo trên mọi chỗ trống). Thanh công cụ theo dòng
  (`TrackTimeline.TOOLS`, `_refresh_tools` – nút cũ phải hide + setParent(None) trước deleteLater, không thì chữ chồng).
  `project_page._timeline_action`: sub_add/split/merge/delete/translate, play/dub_redo/dub_speed/dub_speed_reset/
  dub_toggle (nodub)/dub_reset_pos (`_dub_rows_flag` + relayout), v_split (edit `vsplits`) / v_delete (`cuts`) / v_restore /
  v_unsplit / v_insert (`inserts` [{at, path, dur, name}], QFileDialog) / v_insert_del, a_mute / a_unmute (edit
  `mute_spans`); `audiomix.merge_spans / subtract_spans / mute_mask / mute_expr`.
  * Tắt tiếng gốc theo đoạn: `LiveMix.mute` (lớp orig/bg/voice, vào/ra 15 ms) qua `audio_state["mute"]`; xuất: `volume=0:
    enable=between(...)` trên [0:a] + bản tách nhạc nền / giọng gốc (mốc − seek0). Giọng lồng không bị tắt.
  * Chèn video (chỉ khi xuất, trình phát không chiếu – dấu + tím trên dòng Video): `video.out_time` (giây gốc -> giây xuất
    sau cắt/tốc độ); sau phụ đề tách video chính bằng trim tại các điểm chèn, video chèn scale+pad về TW×TH, fps nguồn;
    tiếng: [amx] apad/atrim -> asplit/atrim, đoạn chèn có tiếng riêng (không có -> anullsrc); concat 2k+1 đoạn; mờ dần áp
    sau khi ghép; logo / chữ chạy / nhạc thêm chỉ ở phần video chính. Thử: 10 s − cắt 1 s + chèn 2 s + 1,5 s = 12,5 s,
    tắt tiếng 1–2 s = 0, đoạn chèn đúng màu + tiếng.

- 2.0.41: "Tách giọng/nhạc nền: AttributeError: 'NoneType' object has no attribute 'write'" (lúc có lúc không). Mở tool bằng
  shortcut ngoài màn hình / `ChayTool_Windows.bat` = `pythonw.exe` (không cửa sổ đen) -> Python để `sys.stdout`/`sys.stderr`
  = None; audio-separator vẽ thanh tqdm (mdxc/mdx/vr/demucs, tải mô hình) ra stderr -> lỗi. Mở bằng cửa sổ đen
  (`python TraMi.py`, `ChayTool_XemLog.bat`) thì không lỗi -> "một số lần". Cùng lỗi có thể gặp ở SenseVoice, tải mô hình
  Whisper / HF. Sửa: `config.fix_std_streams()` (TraMi.py, `gui.main.run`, `__main__.main`) thay None bằng `_ConsoleLog`
  ghi `data/logs/console.txt` (ghi mới mỗi lần mở, ≤ 5 MB); `config.no_console()` (theo `sys.__stderr__`) cho tiến trình
  VieNeu/Omni/Qwen/Hy-MT2 vẫn ghi log ra file; báo "tắt đột ngột" chỉ đúng file log. Đã tái hiện đúng lỗi + chạy thử
  giao diện offscreen với stdout/stderr = None.

- 2.0.42: "câu quá dài bị ngắt khi sang câu sau – cho chọn đọc đè / nối tiếp / lùi câu; nhiều câu dài cùng lúc xử lý
  thế nào; thêm logic còn thiếu". `tts.plan_layout(items, total, mode, max_speedup, lead_max, max_lag)` (thuần, test
  được) thay vòng xếp cũ trong `synthesize_dub`. Cách xếp (`dub_overflow`, chung; `extra["overflow"]` riêng câu):
  * smart (mặc định, "Tự động"): tăng tốc tới `COMFY_SPEED` 1,15 → đọc sớm vào khoảng lặng trước (≤ `dub_lead_max` 1 s,
    không đè câu trước) → tăng tốc tới `tts_max_speedup` → câu sau lùi (≤ `dub_max_lag` 2 s, quá thì vào đúng mức đó và
    đè đuôi câu trước); không bao giờ cắt chữ. push "Nối tiếp": tăng tốc → câu sau lùi, trễ dồn, đuổi kịp ở chỗ nghỉ.
    overlap "Đọc đè": tăng tốc → câu sau vẫn đúng giờ. early "Đọc sớm": lùi câu dài lên trước tới `EARLY_MAX` 3 s →
    tăng tốc → câu sau lùi. cut: cách cũ (mượn 0,15 s, lùi 0,25 s, nhỏ dần + cắt).
  * Cụm câu dài (smart/push/early): các câu liên tiếp đều tràn sang câu sau gom thành cụm, chọn 1 tốc độ CHUNG nhỏ nhất
    (chia đôi) để không câu sau nào bị lùi; vị trí tính ngược từ câu cuối cụm (mỗi câu bắt đầu muộn nhất có thể mà vẫn
    chừa chỗ) -> đọc sớm chỉ đúng phần cần. Câu kéo tay (`dub_shift`) không đọc sớm. Thứ tự câu không bao giờ đảo
    (đã thử 4.000 tình huống ngẫu nhiên × 5 cách; 1.500 câu xếp < 0,02 s).
  * Mỗi câu ghi `dub_at` [bắt đầu, kết thúc giọng], `fit_shift` (âm = đọc sớm, dương = bị lùi), `fit_over` (giây đè câu
    sau); `fit` "long" = vẫn tràn > 0,3 s sau khi tăng tốc (bị cắt / làm câu sau lùi / đè) -> too_long (AI rút gọn).
    `stats["shift"]` (early/late/max_late/over/cut) -> `dub_summary`. 2 giọng đè: `soft_limit` nén mềm đỉnh > 0,9.
  * **Phụ đề chạy theo giọng lồng** (`dub_subs_follow`, mặc định bật): `subtitle.follow_dub` – câu giọng lệch > 0,3 s
    hiện từ lúc giọng bắt đầu tới lúc đọc xong, không đè câu sau; `pipeline.subs_follow_dub` dùng khi xuất (`render`)
    và xem trước (`_refresh_overlay`), chỉ khi đang nghe bản lồng và không phải phụ đề tiếng Trung. Karaoke nhận đoạn
    giọng theo `dub_at` (trước bỏ câu lùi > 1 s). `use_dub_version` cập nhật `dub_at` theo bản được chọn.
  * `pipeline.dub_clips` (đoạn giọng theo `dub_at` từng câu) cho timeline / Nghe câu -> hết lệch số câu khi thêm / xoá
    câu sau khi lồng; xoá câu -> tự xếp lại bản lồng (bỏ giọng câu đã xoá, không đọc lại).
  * GUI: tab Lồng tiếng mục 3 "Khi câu đọc dài hơn chỗ trống" (Cách xếp + giải thích, Tăng tốc tối đa, Đọc sớm tối đa,
    Câu sau lùi tối đa, Phụ đề chạy theo giọng lồng, AI tự rút gọn) – đổi là lưu + xếp lại sau 0,9 s (`_relayout_timer`,
    `_relayout`); mục 2 "Khi câu dài" + "Đặt cách xếp" cho câu đã tích (`_apply_overflow`); khung Câu đang chọn ô "Khi
    câu dài"; timeline nút "Cách xếp ▾" (menu), đoạn đè vẽ 2 làn, vạch xanh (đọc sớm) / cam (bị lùi) nối từ lúc nhân vật
    nói, nhãn "#n ×1.30 ◀0.8s ⧉0.4s"; cột Độ vừa `subs_model.fit_text/fit_tip` (◀ ▶ ⧉ ✎). Thanh công cụ timeline hẹp
    (1366 px) tự bỏ chữ các nút cuối (`_fit_tools`). Đã chạy thử offscreen: đổi 5 cách -> bản lồng xếp lại đúng, video
    xuất có phụ đề câu bị lùi khớp đoạn giọng (12,19–14,44 s vs giọng 12,19–14,43 s).

- 2.0.43: người dùng hỏi "căn khớp thoại xong câu đỏ có về xanh, tốc độ có về bình thường không". Tìm ra lỗi: `align.refine`
  / `split_timed` và `split_speaker_changes` chép NGUYÊN `extra` sang 2 nửa câu bị tách (trans rỗng) -> cả 2 nửa giữ
  `tts_file` + `dub_at` của CẢ câu cũ; sau "Dịch dòng trống" + xếp lại -> giọng cả câu bị đặt 2 lần; độ vừa / màu cũ
  không tính lại theo mốc mới. Sửa: `pipeline.forget_stale_voice(old_texts, segs, moved)` (câu có lời gốc khác mọi câu
  cũ -> bỏ tts_file/take/dub_at/fit*; moved -> bỏ dub_at/fit* mọi câu), `has_dub(p)`; `realign` gọi + đã có bản lồng thì
  `relayout` ngay (màu / tốc độ tính lại theo mốc mới, không đọc lại); `diarize` tách câu -> bỏ giọng nửa câu + xếp lại.
  Tốc độ đặt tay (`speed`), giọng riêng, cách xếp riêng vẫn giữ. Lưu ý: ô "Tốc độ đọc" (Chậm/Bình thường/Nhanh) ở mục 3
  là tốc độ BỘ ĐỌC khi đọc (nằm trong mã hash file giọng) -> đổi rồi Tạo bản lồng tiếng = đọc lại mọi câu.

- 2.0.44: lỗi "Tạo bản xem trước: FFmpegError … Error opening input file C:\Users\daole\Downloads\<tên>.mp4 … No such
  file or directory". Dự án chỉ LƯU ĐƯỜNG DẪN video gốc (không chép vào dự án) – file đã bị chuyển / đổi tên / xoá ->
  trình phát không mở được -> `_media_error` tưởng định dạng lạ -> tạo bản xem trước -> FFmpeg lỗi khó hiểu. Sửa:
  `pipeline.source_missing`, `find_moved_source` (file CÙNG TÊN + cùng độ dài ±max(1,5 s; 1 %) ở thư mục cũ, thư mục
  tool, Downloads/Desktop/Videos/Documents/OneDrive + thư mục con 1 cấp), `relink_source` (khác độ dài -> ValueError,
  giữ phụ đề / dịch / lồng tiếng), `VIDEO_EXTS`. GUI: `_load_media` tự tìm + nối lại (toast), không thấy -> nút đỏ
  "Tìm lại video gốc…" (`relink_btn`, `_relink_source`, QFileDialog) trên thanh đầu; `_media_error`, `_grab_frame`,
  `_load_timeline_assets` bỏ qua khi mất video. `ff.run`: `-i` là đường dẫn tuyệt đối không tồn tại -> FFmpegError
  "Không tìm thấy file … bấm 'Tìm lại video gốc…'" (mọi tác vụ: tách giọng, nghe, xuất…).

- 2.0.45: "AI rút gọn là gì, sao rút gọn cả câu đã vừa, rút gọn xong phải làm gì để tạo lại giọng". AI = công cụ dịch AI
  ở Cài đặt (DeepSeek / Claude / GPT / Gemini / Ollama qua `Translator.shorten`, `SHORTEN_PROMPT`; Google / Hy-MT2 không
  viết lại được). Lỗi cũ: `shorten_lines` rút gọn MỌI câu đã tích (đích = min(số tiếng − 1, theo thời lượng phụ đề)) và
  không đọc lại giọng. Sửa:
  * `synthesize_dub` ghi `fit_need` (giọng ở ×1) + `fit_have` (= `plan_layout` "slot": chỗ trống của CHÍNH câu từ lúc
    nhân vật nói tới câu sau, không tính bị đẩy). `pipeline.shorten_target(s, slot)` / `needs_shorten(s)`: ✓ xanh (kể cả
    đọc sớm mà vừa) và ⚡ ≤ ×1,15 (`SHORTEN_FROM`) -> không; ⚠ / ⚡ > ×1,15 -> rút gọn trừ khi need/slot ≤ max(1,15, tốc độ
    đặt tay) (chỉ bị câu trước đẩy); đích = số tiếng × slot × 1,1 (`SHORTEN_SPEED`) / need × 0,95. Chưa lồng: ước theo số
    tiếng vs `budget_for(chỗ tới câu sau, 1,1)`. `too_long` (AI tự rút gọn khi tạo bản lồng) cũng bỏ câu chỉ bị đẩy.
  * `shorten_lines` -> {"changed", "fit", "same"}; `unshorten_lines` (về `trans_long`, đặt `trans_user` để AI tự rút gọn
    không làm lại). GUI `_shorten_rows`: rút gọn rồi `redub` ĐÚNG các câu đổi lời (đã có bản lồng), job kind "redub",
    kết quả `pick` -> `job_finished` tích sẵn các câu đó; nút "Bỏ rút gọn" (`_unshorten_rows`); "Tích câu ⚠ quá dài" dùng
    `needs_shorten`. Thử GUI (AI + Edge giả): tích 5 câu -> rút gọn + đọc lại đúng 2 câu dài, câu bị đẩy giữ nguyên.

- 2.0.46: "tách phụ đề Trung rồi dịch ở tab Lồng tiếng hay Phụ đề được không". Tab Phụ đề có Dịch dòng trống / Dịch lại tất
  cả / Dịch câu chọn; tab Lồng tiếng chưa có -> thêm hàng "Dịch": "Dịch câu chưa dịch" + "Dịch lại câu đã tích"
  (`project_page._translate_rows`): dịch rồi, nếu đã có bản lồng (`has_dub`), `redub` ĐÚNG các câu đổi lời, tích sẵn chúng;
  nhãn `d_engine_lbl` "bằng: <công cụ>". `pipeline.translate(rows=)` + `Translator.translate_segments(rows=)`: chỉ dịch các
  câu đó; dịch LẠI (rows, không only_empty) xoá bản dịch nhớ sẵn của các câu đó trong `p.data["cache"]` (khoá
  "engine|style|câu gốc" có / không `⟪≤N tiếng⟫`). Lỗi tìm ra: nút Dịch dùng ô `a_engine` (tab Tự động, chỉ đọc Cài đặt
  lúc dựng trang) còn AI rút gọn dùng `translate_engine` trong Cài đặt -> đổi Cài đặt giữa phiên thì nút Dịch vẫn dùng
  công cụ cũ. Sửa: `sync_engine()` (mở dự án + `SettingsPage.save`), đổi ô tab Tự động -> `_engine_changed` lưu
  `translate_engine` / `translate_style` vào Cài đặt ("Không dịch" không lưu).

- 2.0.47: "thêm tiếng Anh, Thái, Nhật, Hàn, áp dụng vào tất cả, có lựa chọn chọn đích" + "tab Tự động hoạt động đúng chưa;
  Nghe & tách câu đã bật tách giọng mà sang tab Âm thanh chọn bản đã tách thì không thấy".
  * Tab Tự động: tách giọng trong `recognize` lỗi / chưa cài audio-separator trước đây IM LẶNG (nghe trên tiếng gốc, không
    có bản tách) -> `p.data["sep_warning"]` hiện sau "1. Nghe & tách câu" và trong `auto()`; xong thì báo "đã tách giọng /
    nhạc nền (dùng được ở tab Âm thanh)". Nguyên nhân hay gặp: lỗi pythonw (sửa ở 2.0.41) hoặc chưa cài gói tách giọng.
    Cũng sửa: chạy trọn gói bỏ qua vùng OCR đã khoanh (`opts["region"]`); bước 3 dùng ô bộ đọc ở tab Lồng tiếng thay vì
    ô ở tab Tự động (`a_tts` <-> `d_engine` đồng bộ); bước 2 luôn dịch lại tất cả (`_step_translate`: chưa dịch -> dịch
    hết, dịch dở -> chỉ dòng trống, đã dịch hết -> hỏi).
  * Dịch (`translate.py`): `Translator(target=, source=)`; `system_prompt(target)` (tiếng Việt giữ nguyên bản cũ, tiếng
    khác lời nhắc tiếng Anh: tên riêng pinyin / đọc Nhật / Hán Hàn, kính ngữ), `STYLES_EN`, `SHORTEN_PROMPT_EN`, phân tích
    phim theo tiếng; giới hạn `⟪≤N words / Thai characters / Japanese characters / Korean syllables⟫` theo `langs.budget`
    (vi 4,6 tiếng/s, en 2,7 từ/s, th 11, ja 6, ko 6 chữ/s); `untranslated(text, lang)` (tiếng Nhật: chỉ coi là chưa dịch
    khi không có kana mà ≥ 3 chữ Hán); bộ nhớ dịch `cache_key` (tiếng Việt giữ khoá cũ "máy|kiểu|câu", tiếng khác
    "máy|kiểu|tiếng|câu"), `cache_line`; Google `sl` theo tiếng gốc (`source_lang(p)` = `p.data["language"]`), `tl` theo
    đích; Hy-MT2 nhận `target` (泰语/日语/韩语/英语). Bảng thuật ngữ: `glossary` (Hán Việt) chỉ cho tiếng Việt, tiếng khác
    `glossary_<tiếng>` (`pipeline.glossary_key`); bảng chung ở Cài đặt chỉ áp cho tiếng Việt. `pipeline.translate`:
    tiếng đích khác tiếng của bản dịch đang có (`trans_lang`) -> dịch lại mọi câu (kể cả "chỉ dòng trống"); ghi
    `target_lang` + `trans_lang`. Rút gọn / AI tự rút gọn / `shorten_target` / `needs_shorten` đếm độ dài theo tiếng.
  * Giọng đọc (`tts.py`): mỗi câu nhận tiếng theo chữ viết (`langs.detect(trans, tiếng dự án)`) -> `clean_tts_text(text,
    lang)` giữ chữ Thái / kana + Hán / Hangul; giọng Edge tiếng khác -> `edge_for` đổi sang giọng tiếng đó cùng nam / nữ
    (Hoài My -> Aria / Premwadee / Nanami / Sun-Hi; Nam Minh -> Guy / Niwat / Keita / InJoon; en có 6 giọng); VieNeu
    (chỉ tiếng Việt) gặp câu tiếng khác -> Edge cùng giới tính (preset / `speaker_gender`), `stats["lang_edge"]` -> 
    `dub_summary`; OmniVoice nhận `language` (worker `req["language"]`, `VieneuWorker.synth(language=)`), giọng chọn sẵn có
    câu mẫu riêng từng tiếng (`omni_design.ANCHOR_TEXTS`, `key/anchor_path/ensure_anchor(..., lang)`, file
    `<mã>_<tiếng>_<key>.wav`; tiếng Việt giữ tên + mã cũ); `spoken_vi` chỉ cho câu tiếng Việt. `list_voices(settings,
    lang)` / `auto_assign(..., lang)` / `preview_voice(..., lang)` theo tiếng đích (không liệt kê VieNeu khi khác tiếng
    Việt; nghe thử VieNeu với tiếng khác -> báo). Sửa luôn lỗi cũ: tạo câu mẫu "nu_trung_nien" xoá nhầm câu mẫu
    "nu_trung_nien_tram" (glob) -> `_stale` so đúng mã + tiếng.
  * Phụ đề: `effective_style` ghi `st["lang"]`; `subtitle.line_font` -> `{\fn<font>}` mỗi câu Thái / Nhật / Hàn (Windows:
    Leelawadee UI / Yu Gothic / Malgun Gothic – font Latin như Be Vietnam Pro, Bangers không có các chữ này), chữ Hán
    chưa dịch -> font chữ Hán; karaoke + nền hộp: cả 2 lớp có `\fn`. `wrap_line` tính độ rộng hiển thị (`vis_len`: chữ
    Đông Á ×1,8, dấu kết hợp 0), tiếng viết liền (Nhật, Thái) ngắt giữa 2 chữ (`_wrap_chars`: không ngắt trước dấu
    thanh / dấu câu đóng / kana nhỏ, không sau nguyên âm đứng trước của chữ Thái, ưu tiên sau 、。). Xem trước
    (`player.SubtitleItem`) dùng cùng `line_font`. Karaoke chữ Thái: mỗi chữ + dấu đi kèm 1 đơn vị. Đã xuất thử bằng
    libass: Thái / Nhật (2 dòng) / Hàn / Việt hiện đúng, không ô vuông. SRT xuất đuôi `.<tiếng>.srt`.
  * Giao diện: tab Tự động ô **Dịch sang** (`a_target`, `_target_changed`: lưu `p.data["target_lang"]` + mặc định ở Cài
    đặt, đổi nhãn cột bản dịch (`SubsModel.set_trans_label`), ô khung Câu đang chọn (`inspector.set_lang`), lựa chọn
    phụ đề (`common.which_choices` / `relabel`), nút SRT, bảng thuật ngữ theo tiếng, danh sách giọng; đã có bản dịch tiếng
    khác -> nhắc bấm "2. Dịch"); Cài đặt "Dịch sang (dự án mới)"; Xử lý hàng loạt "Dịch sang"; CLI `--dich-sang`;
    `auto(opts["target"])`. Nạp SRT đã dịch tiếng khác (`pipeline.note_trans_lang` / `guess_trans_lang`) -> tự đổi "Dịch
    sang" theo (không thì bấm Dịch sẽ dịch đè mất bản vừa nạp); dòng tiếng Nhật có kana không bị coi là dòng Trung.

- 2.0.48: "làm mờ chỉnh được bo góc; nền phụ đề bo góc; cỡ chữ 30 bị xuống 2 dòng – cho tuỳ chỉnh; công cụ che / xoá logo,
  phụ đề đẹp hơn; logo cố định 1 chỗ xoá thế nào là chuẩn nhất; lấy nền từ khung trên / dưới chưa ổn".
  * Xuống dòng: công thức cũ `max_chars = bề rộng / (0,55 × cỡ chữ)` đoán mỗi ký tự 16,5 px ở cỡ 30, chữ thật ~10,3 px
    (Be Vietnam Pro, em = 0,655 × cỡ) -> xuống dòng khi mới dùng ~2/3 khung (video dọc 1080: 65 ký tự thay vì ~94). Giờ
    đo chữ thật (`textfit`), cài đặt kiểu chữ: `line_width` (mặc định 0,9 khung; lề trái phải ASS theo đó), `max_lines`
    (1–3), `line_fit` wrap / shrink (thu nhỏ chữ riêng câu cho vừa 1 dòng tới `min_scale`, `{\fsN}`), dài quá số dòng tối
    đa -> tự nhỏ lại. Chia dòng `split_balanced` (quy hoạch động, dòng dài nhất ngắn nhất, ưu tiên sau dấu câu / trợ từ
    Nhật, tránh dòng 1 từ). Đã đo libass: bề rộng đo 669 px, mực in 671 px.
  * Nền phụ đề bo góc: `box_radius` (0..0,5 chiều cao 1 dòng nền), `box_block` (1 khối chung). libass không bo được nền
    BorderStyle 3 -> nền tự vẽ lớp 0 (`\an7\pos` + `\p1`, `subtitle.rounded_rect_path` Bézier), chữ lớp 1 không viền
    (như dải cố định); nền từng dòng nối liền không chồng (chồng -> chỗ chồng đậm hơn). Bo góc 0 + từng dòng: giữ cách cũ.
    Karaoke + nền tự vẽ: không cần mẹo 2 lớp cũ. Xem trước vẽ `drawRoundedRect` theo cùng ô nền.
  * Làm mờ / dải che: `round`, `feather` (theo cạnh ngắn vùng), `blur_kind` box / gauss (gblur) / pixel (scale area ->
    neighbor), `darken` (lutyuv). Xuất: crop -> mờ -> alphamerge mặt nạ geq (`video.mask_expr` = `round_mask` numpy,
    khoảng cách tới hình chữ nhật bo góc) -> overlay; không bo / không mờ viền thì giữ lọc cũ. Xem trước
    `HardsubOverlay._shape_mask` + Xem thử 1 khung (`inpaint.preview_frame(hs=)`).
  * Xoá sạch: lấy NỀN THẬT từ khung trước / sau (`vfill.BandFiller`, bật mặc định `hs["temporal"]`): lượt 1 điểm nền thật
    chưa bị che ở khung khác (dày gần: ±1..8, thưa dần tới ±128 khung; tránh lỡ quãng ngắt ngắn giữa 2 câu), lượt 2 khung
    trước đã lấp (không bù sáng – bù mãi qua chuỗi khung sẽ trôi màu); chuyển động: tương quan pha từng cặp khung liền +
    đo thẳng với khung xa > 8; lấy mẫu lệch lẻ điểm ảnh; kiểm tra vòng cách mép mặt nạ 3–8 px (sai số ≤ 9, bù sáng trung
    vị nếu ≥ 4); còn sót: cảnh đứng yên dùng lại phần khung trước đã vẽ, không thì LaMa / OpenCV (`_fill_mask`). Mặt nạ
    `subtitle_mask`: nét chữ mọi màu (Lab so trung vị lân cận, Otsu) ∪ cách cũ, có khung OCR thì trong khung (khung OCR
    trống ở khung nào -> tự dò để bắt câu OCR đọc sót), `cover_box` xoá cả khung chữ. LỖI TÌM RA: cắt dải YUV 4:2:0 ở
    toạ độ / chiều cao lẻ bị FFmpeg làm tròn -> khung đọc ra lệch hàng dần; giờ `format=bgr24,crop` (đã kiểm khớp từng
    điểm ảnh). Video thử (OpenCV): nền nhiều chi tiết, phụ đề Trung đổi câu, máy lia + đứng yên: cách cũ sai số 17,6 ->
    mới 3,6 (lia) / 2,6 (đứng yên), không nhấp nháy.
  * Gỡ logo cố định (`trami/logo.py`, `hs["logos"]` – danh sách vùng riêng): ~200 khung rải khắp video (khung khoá, ít
    thì fps), viền logo = trung vị đạo hàm qua các khung (lấp lòng hình), hồi quy theo thời gian từng điểm trên YCrCb
    I = α·L + (1−α)·Ĵ với Ĵ = nền nội suy từ quanh logo -> (1−α), α·L; tính ngược J = (I − α·L)/(1−α); α > 0,88 hoặc nền
    không đổi -> "đặc" -> lấp bằng nền khung khác / AI; kênh màu bị phủ gần hết (β < 0,25) -> màu nội suy, độ sáng vẫn
    tính ngược. Toàn bộ logo còn là "soft" trong BandFiller: máy lia làm nền lộ ra thì dùng nền thật, không thì giữ bản
    tính ngược. Lưu mô hình `work/render/logo_<mã>.npz`. Thử: logo chữ trong suốt 60 % + bóng: sai số 30–40 -> ~5;
    logo khối 55 %: 86–102 -> 4–23; máy lia: logo sai số 4 (= nhiễu nén). Đã thử các cách khác: đo theo độ biến
    thiên (sai khi nền không đồng đều), sửa viền bằng Telea (nhoè nền chi tiết), lan độ đục từ mép (hỏng logo chữ).
  * `video.needs_clean(hs)` (xoá sạch hoặc có logo) -> bản trung gian `inpainted_<mã>.mp4` (mã thêm logos, temporal, cover,
    algo 2); các cách làm mờ / dải / vá nền / delogo vẫn áp lên trên khi xuất. GUI tab Xoá phụ đề: PillTabs "Phụ đề cũ /
    Logo cố định" (`_hs_lists`, `_hs_target`; RegionEditor `extra` vẽ mờ danh sách kia; HardsubOverlay `target`,
    `LOGO_COLOR`), ô mới: Lấy nền thật từ khung trước / sau, Dùng khung chữ OCR, Xoá cả khung chữ, Kiểu mờ, Phủ tối, Bo
    góc, Viền mờ dần; nút "Chạy xoá (cả video)" hiện khi Xoá sạch hoặc có logo. Tab Kiểu chữ: Độ dài tối đa của dòng, Số
    dòng tối đa, Khi câu dài hơn 1 dòng, Thu nhỏ tối đa còn, Kiểu nền (từng dòng / 1 khối / dải cố định), Bo góc nền.
  * Đã xem: video-subtitle-remover (Apache-2.0, dùng STTN / LaMa / ProPainter) – không lấy mã; ProPainter (S-Lab,
    phi thương mại) / E2FGVI (CC BY-NC) không dùng được cho video kiếm tiền.

- 2.0.49: "tải Hy-MT2 ở tab Tài liệu đứng ở 0/18, không chạy gì" (cửa sổ đen: "Downloading bytes: 0.00B" + cảnh báo
  symlink). huggingface_hub ≥ 1.0 tải file lớn qua Xet (hf_xet, máy chủ *.xethub.hf.co): mạng / VPN / tường lửa chặn thì
  đứng ở 0 byte mãi, không báo lỗi, bấm Huỷ không được (kẹt trong mã Rust); cách khắc phục phổ biến HF_HUB_DISABLE_XET=1.
  Tool lại không thấy tiến độ: "Fetching 18 files" chỉ tăng khi xong trọn 1 file (file trọng số 3,6 GB), thanh byte của
  thư viện tự tắt khi không có cửa sổ đen (disable=None + kiểm tra TTY). Sửa: `trami/hf_download.py` tải ở tiến trình
  riêng với lớp tqdm riêng (kế thừa tqdm gốc để thư viện không ép tắt; gom byte đã ghi / tổng, byte nhận qua mạng, số
  file -> `@@TRAMI@@` mỗi 0,5 s), hiện "37% · 1.32 GB / 3.60 GB · 8.5 MB/s · 17/18 file", "chờ dữ liệu N s"; Xet đứng
  im `STALL_XET` 45 s hoặc lỗi Xet -> giết, xoá `blobs/*.incomplete`, tải thường, ghi `data/hf_no_xet.txt`; tải thường
  đứng im `STALL_HTTP` 90 s -> thử lại 1 lần (nối tiếp) rồi báo lỗi mạng; 401/403/404/gated báo ngay. Cảnh báo symlink
  Windows tắt (`HF_HUB_DISABLE_SYMLINKS_WARNING`). Đã chạy thật với huggingface_hub 1.33 trên máy chủ HF giả: tải đủ
  18 file, đứng im -> chuyển cách tải -> xong, Huỷ dừng ngay (không sót tiến trình con), sai tên kho báo ngay.

- 2.0.50: 2.0.49 vẫn báo "Tải tencent/Hy-MT2-1.8B bị đứng: không nhận thêm dữ liệu nào trong 90 giây" -> cả Xet lẫn tải
  thường đều 0 byte: file lớn của HuggingFace nằm trên Xet, tải thường cũng chuyển hướng sang cas-bridge.xethub.hf.co ->
  mạng người dùng chặn / nghẽn máy chủ này thì không cách nào của HuggingFace tải được. Thêm nguồn ModelScope (Tencent
  đăng chính thức `Tencent-Hunyuan/Hy-MT2-1.8B`; ModelScope vốn tải được trên máy người dùng – SenseVoice):
  `hf_download._ms_download` = modelscope `snapshot_download(ms_id, local_dir=<kho HF>/snapshots/<sha1>,
  allow_file_pattern=HF_FILES)` + ghi `refs/main` -> tiến trình Hy-MT2 nạp `from_pretrained(repo, local_files_only)`
  như cũ. Tiến độ ModelScope: móc mọi thanh tqdm (`hook_all_tqdm`) + đo dung lượng thư mục (`dir_bytes`, cũng là tín
  hiệu "còn chạy" khi thư viện không báo). Tải thường 0 byte -> sang ModelScope luôn (nhận được dữ liệu rồi mới đứng
  thì thử lại 1 lần); lỗi cuối liệt kê từng nguồn + số MB nhận được. Kho ModelScope chỉ ghi kho chính hãng
  (`MS_MIRRORS`; tổ chức trùng tên cả 2 nơi: Qwen). Đã chạy thật: huggingface_hub 1.33 + modelscope 1.40.2 trên 2 máy
  chủ giả (HF đứng im cả 2 cách -> ModelScope tải xong, `hf_hub_download(local_files_only)` trỏ đúng bản ModelScope,
  `_hf_complete` = True, lần sau ModelScope trước).

- 2.0.51: "tải video từ Bilibili có tốt không" -> rà lại `download_video` (chưa từng chạy thật với Bilibili; sandbox chặn
  bilibili.com): khách chỉ được 480p, dán dòng chia sẻ "【tên】 https://b23.tv/…" bị báo "Link không hợp lệ", có thể tải về
  HEVC/AV1 (phải tạo bản xem trước), tải lỗi để lại dự án trống, tiến độ chạy 2 lần (hình rồi tiếng), yt-dlp cũ báo 412.
  * `trami/sitelogin.py`: `extract_url` (link đầu tiên, bỏ dấu câu Trung / Việt dính đuôi; chỉ mã BV -> link video),
    `is_bilibili`. Đăng nhập Bilibili bằng MÃ QR (API web passport `qrcode/generate` + `qrcode/poll`: 86101 chờ / 86090 đã
    quét / 86038 hết hạn / 0 xong; cookie từ Set-Cookie, thiếu thì từ query của data.url, giữ nguyên dạng %2C) -> file
    Netscape `data/cookies/bilibili.txt` (hạn theo Expires, mặc định 180 ngày) -> yt-dlp `cookiefile`; `bili_status` hỏi
    `x/web-interface/nav` (tên, VIP). Mã QR vẽ bằng `cv2.QRCodeEncoder` (dự phòng gói qrcode). Cookie Chrome / Edge trên
    Windows hay hỏng (App-Bound Encryption từ Chrome 127, file bị khoá khi trình duyệt mở) -> Bilibili không dựa vào nó.
  * `download_video(url, …, notes=, settings=)`: cookie Bilibili (QR) > `dl_cookie_file` > `dl_browser`
    (`cookiesfrombrowser`); lỗi đọc cookie trình duyệt -> tải lại không cookie + ghi chú; `format_sort`
    ["res:1080", "vcodec:h264", "acodec:aac"] (≤ 1080p, H.264 trước); tiến độ gộp các phần theo `requested_formats`
    (không biết cỡ: hình 85 % / tiếng 15 %), "(hình)/(tiếng)", MB, MB/s, "Đang ghép hình và tiếng…" (postprocessor_hooks),
    `noprogress`; `_dl_error` giải thích 412 / Douyin "Fresh cookies" / Unsupported URL / khu vực / VIP / cần đăng nhập;
    Bilibili không cookie mà ≤ 480p -> ghi chú nhắc đăng nhập (`p.data["dl_warning"]`, `auto` nối vào thông báo).
    `update_ytdlp` (`python -m pip install -U yt-dlp`, đã import yt_dlp thì nhắc mở lại tool; exe không cập nhật).
  * Trang chủ khung "Tải video từ link": ô nhận dòng chia sẻ, trạng thái Bilibili + "Đăng nhập Bilibili (quét mã QR)" /
    "Đăng xuất", "Trang khác lấy đăng nhập từ" (Không / Firefox / Edge / Chrome / Brave / Opera / Vivaldi / File cookies.txt…),
    "Trình tải yt-dlp <bản>" + "Cập nhật trình tải" (job "update"). Hàng loạt / dòng lệnh cũng nhận dòng chia sẻ.
    `MainWindow._drop_empty_project`: job download / auto lỗi hoặc huỷ mà dự án chưa có video + câu -> xoá, đang mở thì
    về Trang chủ. Thông báo xong có ⚠ -> toast "warn" 9 s.
  * Sửa `timeline.mouseMoveEvent` IndexError khi rê chuột qua dòng Video / Tiếng gốc lúc chưa biết độ dài video.
  * Đã chạy thật: yt-dlp 2026.08.19 tải từ máy chủ trên máy (dòng chia sẻ, cookie chrome lỗi -> tải không cookie), hộp mã
    QR (giải mã lại bằng cv2.QRCodeDetector ra đúng link), trạng thái đăng nhập, xoá dự án trống. Bilibili thật chưa chạy.

- 2.0.52: bấm Cập nhật trình tải vẫn lỗi "ERROR: The extracted extension ('html') is unusual and will be skipped for safety
  reasons". Nguyên nhân: yt-dlp chỉ có bộ tải Bilibili cho `(www.)bilibili.com/video/BV…` (+ bangumi / av), Douyin cho
  `www.douyin.com/video/<số>`; link điện thoại `m.bilibili.com/video/BV…`, link rút gọn `b23.tv` (không có bộ tải riêng),
  `v.douyin.com` -> `iesdouyin.com/share/video/…`, `douyin.com/?modal_id=…` rơi vào bộ tải CHUNG -> nhận trang web, coi
  là file ".html" -> lỗi trên (đã kiểm bằng `IE.suitable` của yt-dlp 2026.08.19). Sửa: `sitelogin.normalize_url`
  (`resolve_short` đi theo chuyển hướng của b23.tv / bili2233.cn / v.douyin.com / v.kuaishou.com / xhslink.com; Bilibili:
  có BV / av ở bất kỳ đâu -> `https://www.bilibili.com/video/BV…[?p=N]`, m. -> www. cho bangumi; Douyin: id số từ
  /video/, /share/video/, modal_id / vid / aweme_id -> `https://www.douyin.com/video/<id>`), gọi trong `download_video`
  sau `extract_url`. `_dl_error`: lỗi "html unusual" / "Unsupported URL" -> giải thích + in "Link đã thử" + cách chép
  link đúng (trình duyệt máy tính, thanh địa chỉ khi video đang phát). Douyin chuẩn hoá xong vẫn cần cookie (Firefox).

- 2.0.53: người dùng hỏi Cài đặt "Tách giọng & người nói" (Kết hợp BS + Mel-RoFormer, tách giọng trước khi nhận dạng bật,
  tự phân biệt người nói tắt, số người nói 1) thì "1. Nghe & tách câu" có chạy đúng không. Tìm ra: `run_recognize` / chạy
  trọn gói lấy "Tách giọng khỏi nhạc trước" + "Phân biệt người nói" từ 2 công tắc tab Tự động (ghi đè Cài đặt), mà 2 công
  tắc này chỉ đọc Cài đặt LÚC MỞ TOOL -> đổi Cài đặt khi tool đang mở thì bước 1 vẫn chạy theo giá trị cũ (cùng kiểu lỗi
  2.0.46); ô mô hình tách ở tab Lồng tiếng (`d_sepmodel`) cũng vậy. Và "Số người nói = 1" + bật phân biệt người nói vẫn đo
  ECAPA + `split_speaker_changes` (có thể cắt đôi câu). Sửa: `sync_engine` đặt cả `a_sep` / `a_spk` / `d_sepmodel` theo Cài
  đặt (mở dự án + Lưu cài đặt); đổi 3 ô này -> `_audio_opts_changed` lưu vào Cài đặt (1 giá trị chung). `recognize`:
  `diarize_speakers == 1` -> gán hết S1, không gọi diarize (không đo giọng, không tách câu); `pipeline.diarize` cũng vậy.
  Thông báo sau bước 1 ghi rõ đã chạy theo gì: `sep_model_name` (đọc work/stems/model.txt – mô hình thật đã chạy) +
  `speaker_note` ("phân biệt người nói: tắt" / "1 người nói (gán hết S1…)" / "người nói: N (tự đoán / đặt N)").

- 2.0.54: "sau khi gộp câu lỗi IndexError: list index out of range ở `_redub_rows`" (Tạo lại giọng các câu đã tích).
  `SubsModel.selected` (câu đã tích) nhớ theo SỐ THỨ TỰ; gộp / tách / thêm / xoá câu sửa thẳng `model.segs` rồi gọi
  `set_segments(cùng danh sách)` -> điều kiện "số câu đổi thì bỏ tích" so danh sách với chính nó -> không bao giờ bỏ ->
  tích trỏ ra ngoài danh sách (IndexError) hoặc TRỎ SAI CÂU (đặt tốc độ / dịch lại / tạo lại giọng nhầm câu, không báo
  lỗi). Sửa: tích nhớ theo đúng câu `_picked` {id(câu): câu}; `selected` là property tính số thứ tự hiện tại; `is_picked`;
  `set_segments`: cùng danh sách (sửa tại chỗ) -> giữ tích của câu còn lại, danh sách mới cùng số câu (đọc lại dự án sau
  tác vụ) -> giữ theo số thứ tự, khác -> bỏ. Gộp có câu đã tích -> câu gộp vẫn tích (`_sub_op` / `_seg_op`).
  `_redub_rows` lọc số thứ tự hợp lệ.

- 2.0.55: "thêm bộ skill dịch vào tool, áp dụng khi dịch bằng API của bất kỳ ai". `trami/skills.py` – dùng với MỌI công cụ
  dịch AI (DeepSeek / Claude / GPT / Gemini / Ollama / API tương thích OpenAI qua `openai_base_url`), không với Google /
  Hy-MT2 (`pipeline.ai_engine` báo lỗi rõ):
  * Kỹ năng (luật viết tay) `skill_text` -> `with_skill` gắn cuối lời nhắc hệ thống của dịch (`_llm_batch`), rút gọn
    (`shorten`, `shorten_lines`, AI tự rút gọn khi lồng), sửa lỗi nghe, hồ sơ phim, kiểm tra. 3 kỹ năng có sẵn (`BUILTIN`,
    chỉ đọc: hài – meme – AI, lồng tiếng câu ngắn, cổ trang) + kỹ năng của bạn (Cài đặt `translate_skills`
    [{id,name,text}], đang chọn `translate_skill`). Hộp `gui/skills_dialog.SkillsDialog`: thêm / chép / nạp .txt .md /
    sửa / xoá / Dùng kỹ năng này.
  * Sửa lỗi nghe & nối câu (`clean_lines` 80 dòng/lần + 4 dòng sau làm ngữ cảnh -> JSON fix/merge/unsure theo số dòng)
    -> `apply_cleanup` KIỂM TRA trước khi áp: chữ mới gần chữ cũ (`_close`: chứa nhau / cùng số chữ ≤ 6 – đồng âm /
    difflib ≥ 0,45), chỉ gộp 2–4 dòng LIỀN NHAU, nghỉ ≤ 1,5 s, câu gộp ≤ 15 s; `extra["src_before"]`, `src_unsure`.
    `pipeline.ai_cleanup` (lưu `cleanup_backup`, bỏ giọng câu đổi lời + xếp lại nếu đã lồng, `asr_cleaned`),
    `undo_cleanup`. Tự chạy trong `translate` khi dịch CẢ phim lần đầu (chưa câu nào có bản dịch, chưa sửa lần nào,
    Cài đặt `ai_clean_before_translate` mặc định bật).
  * Hồ sơ phim `make_profile` (JSON: thể loại, nhân vật, xưng hô, thuật ngữ, chữ hay nghe nhầm, ghi chú) ->
    `p.data["film_profile"]` (sửa được ở tab Phụ đề), thuật ngữ thêm vào bảng tên riêng; lập 1 lần khi dịch bằng AI rồi
    dùng thay `analyze` (THÔNG TIN PHIM của mọi lô dịch). `pipeline.film_profile(force=)`.
  * Kiểm tra bản dịch `review_lines` (60 câu/lần, câu gốc + bản dịch + độ dài tối đa) -> sửa câu lỗi, `extra
    ["trans_before_review"]`, `review_note`; bỏ qua câu `trans_user`; bỏ kết quả còn ≥ 2 chữ Hán (trừ tiếng Nhật).
    `pipeline.ai_review`; tự chạy sau khi dịch cả phim nếu `ai_review_after_translate` (mặc định tắt). Ghi chú kết quả
    `skill_note` / lỗi bước phụ `skill_warning` (không chặn việc dịch) -> thông báo Dịch + `auto`.
  * GUI: tab Tự động "Kỹ năng dịch AI" (`a_skill` + "Sửa / thêm…"), công tắc "AI sửa lỗi nghe & nối câu trước khi dịch",
    "AI kiểm tra lại bản dịch" (lưu Cài đặt, `sync_engine`). Tab Phụ đề: bảng câu + thẻ "Kỹ năng dịch AI · hồ sơ phim ·
    tên riêng" trong QSplitter dọc (`subs_split`), tab cuộn được khi màn thấp (bảng câu ≥ 170 px): nút AI sửa lỗi nghe &
    nối câu (job "clean", tích câu đổi) / Hoàn tác sửa / AI kiểm tra bản dịch (đã lồng -> redub đúng câu sửa) / AI lập lại
    hồ sơ phim; 2 ô Hồ sơ phim + Bảng tên riêng cạnh nhau, mở đầu ẩn (`_show_profile`). Bảng câu: chữ gốc AI sửa mà chưa
    chắc màu vàng; tooltip câu gốc (trước khi sửa / chưa chắc), câu dịch (lý do + bản trước khi AI kiểm tra).

- 2.0.56: "tách xong rồi thì dịch thế nào; sao tab Phụ đề không có lựa chọn dịch; bỏ 3 cột 🎵 / Bỏ lồng / Độ vừa, thay bằng
  cột tích – bắt tôi tích chọn mà không có chỗ tích". Tab Phụ đề:
  * Hàng "Dịch bằng / sang / Văn phong / Kỹ năng AI" (`s_engine`, `s_target`, `s_style`, `s_skill`) = ô gương của ô tab
    Tự động (`_mirror` / `_sync_mirrors`: đổi bên nào cũng đổi bên kia, ô gốc lưu Cài đặt như cũ; `_fill_skills` nạp lại).
  * Nút "Dịch câu chưa dịch" (= Dịch dòng trống cũ), "Dịch lại tất cả", "Dịch câu đã tích" (`_translate_picked` ->
    `_translate_rows`; chưa tích thì câu đang chọn; đã có bản lồng thì đọc lại giọng đúng các câu đó). Tab Lồng tiếng đổi
    tên "Dịch lại câu đã tích" -> "Dịch câu đã tích".
  * Bảng: cột "Chọn" (C_SEL) đầu bảng, bấm tiêu đề = tích / bỏ hết câu đang hiện (`_toggle_pick_all`, cả bảng tab Lồng
    tiếng); ẩn 🎵 / Bỏ lồng / Độ vừa (cột 🎵 chuyển sang bảng câu tab Lồng tiếng). Chọn nhiều dòng (Ctrl / Shift).
    "đã tích N" ở dòng đếm câu. Nút "Bỏ tích".
  * Gộp: ≥ 2 câu đã tích (hoặc bôi chọn) liền nhau -> gộp cả nhóm (`_multi_rows` -> `_sub_op`), câu gộp vẫn tích ->
    bấm "Dịch câu đã tích" ngay; không liền nhau -> báo. Xoá nhiều câu hỏi lại.
  * `subtitle.merge` nối thẳng chữ (trước chèn "，" -> "一只真，的鹈鹕"), chỉ chèn dấu cách giữa 2 chữ Latin / số.

- 2.0.57: "câu liền mạch bị tách thành 2–3 dòng -> dịch / lồng tiếng rời rạc" (`当当，一只真` | `的鹈鹕，…什么日` | `子。`,
  `那是当然，` | `我可是最强` | `AI。你先别…`, `放开我，不好` | `吃。`). Nguyên nhân: `plan_cuts` cắt ở MỌI chỗ nghỉ ≥ 0,8 s kể cả
  giữa 1 từ (bộ căn thời gian hay gán chữ cuối vào tiếng động phía sau -> tưởng nghỉ dài), mẩu ≤ 2 chữ chỉ gộp lại khi nghỉ
  < 0,6 s, phẩy + nghỉ 0,45 s đã cắt, khúc nghe gộp đoạn nghỉ ≤ 1 s; `_split_text` đẩy "AI。" sang câu sau. Sửa (`align.py`):
  * `split_params(settings)` / `SPLIT_STYLES` – Cài đặt `split_style` (Cài đặt + tab Tự động `a_split`, 1 giá trị chung):
    "dub" (mặc định, câu trọn ý – hợp lồng tiếng): phẩy cắt khi nghỉ ≥ 0,7 s, chỗ nghỉ không dấu câu ≥ 1,6 s, khúc nghe
    gộp nghỉ ≤ 1,5 s (`chunk_gap`, asr_qwen / asr_funasr), câu dài tới 10 s, nối dòng chưa hết câu; "short" = cách cũ.
  * `_word_info` (jieba.posseg) + `_cut_ok`: chỗ nghỉ không dấu câu chỉ cắt ở ranh giới từ, không sau từ treo
    (`HANG_TAGS` p/c/m/q trừ `ENDABLE` 一下…, `HANG_WORDS` 一只 / 因为 / 真…), không trước trợ từ (`HEAD_WORDS` 的了吗呢…);
    dùng cả trong `best_cut`. Không có jieba -> chỉ xét danh sách từ. requirements thêm `jieba`.
  * Mẩu ≤ 2 chữ ngăn bởi chỗ không dấu câu gộp lại dù nghỉ ≤ 3 s; `_pull_tail`: 1–2 chữ cuối câu cách phần trước > 0,8 s
    -> kéo mốc về sát chữ trước. `_split_text`: có dấu câu ở chỗ cắt -> cắt SAU dấu câu cuối ("最强AI。" | "你…"), không
    dấu câu -> số / Latin theo phần sau như cũ; `split_timed` nới mốc đầu / cuối câu cho chữ Latin / số (`_latin_dur`).
  * `join_broken(segs, params, max_chars, total)` – nối câu bị cắt ngang không cần AI rồi tách lại (chỉ ở dấu kết câu /
    câu quá dài): luôn nối khi `broken_junction` (cắt giữa từ / từ treo / trợ từ đầu / "AI。" đầu dòng / mẩu cụt ≤ 2 chữ,
    cách ≤ 3 s); kiểu "dub" thêm dòng không dấu câu cuối (≤ 2 s) và dòng mở đầu ≤ 4 chữ kết thúc bằng phẩy (≤ 1 s); cùng
    người nói, không phải 🎵, ≤ 36 chữ / 10 s; phụ đề không dấu câu (OCR, < 30 % dòng có dấu) chỉ nối khi cắt giữa từ.
    Câu không đổi giữ nguyên đối tượng (bản dịch, giọng). Tự chạy trong `recognize` sau căn thời gian (ASR, tiếng
    Trung) -> `p.data["asr_joined"]` -> thông báo bước 1 ("nối lại N câu…", "kiểu tách: …").
  * `pipeline.join_lines` / `undo_join` (`join_backup`, bỏ giọng câu đổi + xếp lại bản lồng) – tab Phụ đề nút "Nối câu bị
    cắt" (job "join", tích câu mới) / "Hoàn tác nối". `skills.MAX_GAP` 1,5 -> 2,5 s (AI nối được "一只真" | "的鹈鹕").
  * Thử trên đúng các dòng người dùng gửi: "那是当然，我可是最强AI。" | "你先别最强了，…", "当当，一只真的鹈鹕，…什么日子。",
    "放开我，不好吃。", "不，我不困了。"; câu trọn vẹn khác giữ nguyên.

- 2.0.58: (1) "nối câu xong một số câu bị trống": không phải mất chữ gốc (thử 3.000 bộ ngẫu nhiên: không câu rỗng, không mất
  chữ) mà cột bản dịch trống – câu tách lại sau khi nối mất bản dịch. Sửa: `align._whole_lines_trans` (câu mới = đúng các
  dòng cũ liền nhau ghép lại -> giữ bản dịch nối lại); `run_join` (dự án đã dịch) tự `translate(rows=câu mới chưa dịch)` bằng
  công cụ đang chọn, đã lồng thì `redub` đúng các câu đó (job "redub"). (2) "tách 10 câu, dịch ở ngoài còn 9 câu, nạp vào
  lòi 1 câu trống": `apply_translation` ghép mỗi câu dịch vào 1 câu gốc trùng giờ nhiều nhất -> câu gốc kia trống. Giờ khác số
  câu: câu gốc -> câu dịch phủ nó nhiều nhất (≥ 30 % hoặc ≥ 0,5 s); câu gốc liền nhau cùng 1 câu dịch -> GỘP thành 1 câu
  (chữ gốc nối thẳng, bỏ giọng cũ); câu dịch không làm chủ câu gốc nào -> nối vào câu gốc trùng nhiều nhất; không trùng câu
  nào -> thêm câu mới chỉ có lời dịch. `stats` {merged, joined, added} -> 1 thông báo khi nạp ("File dịch 9 câu, dự án 10
  câu: gộp 1 câu gốc…"). Sửa `segs` tại chỗ (`segs[:] = new`).

- 2.0.59: "mạng lag / mất mạng giữa lúc lồng tiếng thì tool treo luôn" + lồng 1000 câu/đợt, lọc câu cảm thán lẻ, bỏ cột
  🎵 / Bỏ lồng, tích câu chưa lồng, hàng SRT sang tab Phụ đề, lời bài hát bị dịch / lồng tiếng.
  * Nguyên nhân treo: Edge TTS không giới hạn thời gian cả câu, nút Huỷ chỉ xét trước mỗi câu, file giọng dở (rớt mạng
    giữa câu) vẫn bị coi là "đã đọc" (`have = exists and size > 0`) -> câu mất đuôi mãi; AI dịch `timeout=300` × 4 lần
    × chia đôi lô; tiến trình VieNeu/Omni/Qwen/Hy-MT2 đọc stdout không giới hạn, Huỷ không được.
  * `trami/net.py` (xem Cấu trúc). Dịch AI (`LLMClient.chat`): `net.call(_http)` + `session()` keepalive, timeout
    (15, 300); lỗi mạng -> `wait_online` -> có mạng lại thì gửi lại lô (không tính lần hỏng); `_llm_batch` / `analyze` /
    `shorten` / Google bù không nuốt `Cancelled` / `NetworkDown`. Google: `_gt_one` 3 cách × chờ mạng, timeout (8, 25).
    Edge: `_edge_save` ghi `.part` rồi đổi tên, `asyncio.wait_for(60 + 0,2 s/chữ)`, `connect_timeout=10`,
    `receive_timeout=20`; mất mạng -> 1 câu đi kiểm tra mạng, các câu khác chờ (`net_ok`), có mạng lại đọc lại câu dở;
    quá 10 phút -> `NetworkDown` ("còn N/M câu chưa đọc… bấm tạo lại, chỉ đọc tiếp câu còn thiếu"); Huỷ: vòng
    `asyncio.wait(timeout=0,2)` huỷ các câu đang đọc (KHÔNG dùng asyncio.sleep – test vá sleep thành no-op làm vòng
    quay mãi). Worker: luồng `_pump` đọc stdout vào queue, `_read(stall=)` – Huỷ chờ 3 s rồi `kill()`, không có tin
    `stall_start` 15 phút / `stall_call` (VieNeu 5, Omni 10, Hy-MT2 15, Qwen 30 phút) -> `WorkerStuck` -> mở lại 1 lần.
    yt-dlp: `socket_timeout` 20, retries 10, `net.call` + chờ mạng rồi tải tiếp (.part). Thử: rớt mạng 3 lần giữa 5
    câu Edge -> đủ 5 câu, không file cụt; AI lỗi mạng 3 lần -> chờ rồi dịch được; tiến trình treo -> Huỷ < 4 s.
  * Lồng tiếng: `tts.DUB_BATCH` 1000 – Edge chạy từng đợt 1000 câu ("Đợt 2/3: đọc câu 1001–2000"), VieNeu/Omni mở lại
    tiến trình mỗi 1000 câu. `voicefix.lone_interjection` (câu CHỈ có 1–2 tiếng trong `INTERJ_VI`: A!, Ừm., Ái!, Hả?,
    Ha ha – không có "ai", "ơi", "vâng"…) -> `pipeline.drop_interjections` trong `dub` (đầy đủ, tiếng Việt, Cài đặt
    `dub_drop_interj`) xoá khỏi phụ đề, cất `p.data["interj_removed"]`; `restore_interjections` (đặt `keep_interj`).
    `undubbed_rows` (đã dịch mà không có `dub_at`). `dub_summary` thêm "Bỏ lồng", số câu đã xoá, "N câu chưa có giọng".
  * Tab Lồng tiếng: ẩn cột 🎵 / Bỏ lồng (cột Độ vừa hiện "🎵 bài hát" / "bỏ lồng"), nút Tích câu chưa lồng, Tích câu 🎵,
    Tự tìm câu hát, Đặt / bỏ 🎵 câu đã tích (`not_song` khi bỏ), Bỏ lồng / lồng lại câu đã tích; mục 3: công tắc "Tự xoá
    câu chỉ có tiếng cảm thán", dòng "Giọng đọc – bộ đọc: … · S1: …" (`_update_voice_note`) + "Đổi giọng ở mục 2",
    "Khôi phục câu cảm thán đã xoá". Hàng Xuất SRT / Nạp SRT đã dịch chuyển sang tab Phụ đề (`bar3`, `s_srt_trans`).
  * Lời bài hát: file dịch người dùng đúng là có lời hát vì file Trung gốc (Qwen3-ASR) đã chép cả tiếng hát – Qwen3 không
    gắn nhãn Sing như SenseVoice. (1) `mark_song_lines` – bản dịch có ♪ ♫ 🎵 -> `song` (nạp SRT: `stats["songs"]`, sau
    dịch AI: `skill_note`); lời nhắc dịch luật 9: lời bài hát bắt đầu bằng "♪ ". (2) `songs.guess_song_lines`: tỉ lệ nốt
    ngân (YIN 20 ms bằng FFT; ≥ 0,3 s, lệch chuẩn < 0,45 nửa cung) mỗi câu, câu > 5,5 chữ/s là nói; dãy câu ≥ 35 % nối
    qua ≤ 2 câu (trừ câu lẻ ngắn), cụm ≥ 4 câu / ≥ 12 s / ≥ 70 % câu / trung bình ≥ 45 % -> 🎵, nối câu sát ≥ 20 %.
    Chạy trong `recognize` khi đã tách giọng (`song_detect`, `songs_found`), nút `detect_songs`. Đo thật (tách giọng MDX):
    bài hát librosa "Let's Go Fishin'" 28/34 câu, phim hoạt hình tiếng Trung của người dùng 0/47 câu nhầm, ghép
    thoại + bài hát + thoại: chỉ đánh dấu phần bài hát.

- 2.0.60: hàng nút trên bảng câu tab Lồng tiếng thêm "Lọc câu cảm thán" (`_drop_interj_now`: xoá ngay câu chỉ có 1–2 tiếng
  cảm thán – cả phụ đề lẫn giọng lồng qua `_sub_op("delete")` + xếp lại bản lồng; cất `interj_removed` để "Khôi phục câu cảm
  thán đã xoá") và "Xoá câu đã tích" (`_delete_picked`, hỏi lại, xoá phụ đề + giọng lồng). Lưu dự án ngay sau khi xoá.
  "Tạo lại giọng các câu đã tích" KHÔNG tự lọc câu cảm thán (chỉ "Tạo bản lồng tiếng" đầy đủ hoặc nút Lọc).

- 2.0.61: "mọi nút phẳng (Nghe giọng, Tải MP3…, Tích câu…) ở tất cả các tab cho vào khung bo": `theme.stylesheet`
  `QPushButton[flat="true"]` có viền `BORDER` 1 px + bo 9 px + padding 5/11 (nền trong suốt, rê chuột nền CARD2) – áp cho
  mọi `widgets.button(flat=True)` và nút "Nghe riêng" tab Âm thanh. Đã chụp 9 tab + timeline.

- 2.0.62: "lấy API Gemini gói free, số câu gửi mỗi lần, mỗi loại API 1 khung riêng, nhiều key (key 1 hết hạn -> key 2),
  skill dịch chung sửa được: đúng số câu, không gộp, đúng ngữ cảnh / tên riêng / địa danh".
  * `translate.py`: `api_keys(settings, eng)` (`<eng>_api_keys` + key cũ `<eng>_api_key`), `engine_batch` (`<eng>_batch`,
    chưa đặt -> `translate_batch`), `engine_rpm` (`<eng>_rpm`, mặc định gemini 8, khác 0 = không giới hạn). `LLMClient`:
    `keys`, `ki`; `_pace` (≤ rpm lần / 60 s MỖI key – key hết lượt phút này thì dùng key khác còn lượt, không có thì
    `net.sleep` + báo "Gói miễn phí … chờ N s"); `_key_failed` (401/403/400 "API key not valid"/"expired" -> sai / hết hạn
    (chặn cả phiên); 402 / insufficient_quota -> hết tiền; 429 có PerDay -> hết lượt hôm nay (chặn 1 giờ)) -> `_switch`
    sang key khác + báo "key Gemini số 1/2 … – chuyển sang key … số 2/2"; hết key -> ApiError "Tất cả N key … câu đã dịch
    được giữ lại"; 429 mỗi phút -> key khác, không có thì chờ `retryDelay` / "retry in Ns" (≤ 20 lần, không tính hỏng).
    Nhớ trong phiên: `_KEY_POS` (lần sau bắt đầu bằng key còn tốt), `_KEY_DEAD`, `_KEY_CALLS`. `check_key` (nút Kiểm tra các
    key), `list_models` (Gemini `v1beta/models` lọc generateContent; Anthropic `/v1/models`; OpenAI-compatible `/models`).
  * Cài đặt: khung "Dịch thuật" chung (công cụ mặc định, dịch sang, văn phong) + mục "Công cụ dịch"
    (`ENGINE_CARDS`: Gemini (hướng dẫn aistudio.google.com/apikey) / DeepSeek / Claude / OpenAI (Base URL) / Ollama): `KeyList`
    (mỗi dòng 1 key ẩn chữ, 👁, ×, "Thêm key", kết quả kiểm tra từng key), Model + nút danh sách model (QMenu), Số câu mỗi
    lần gửi, Số lần gửi tối đa / phút (mỗi key), Kiểm tra các key, Thử dịch 1 câu. `SettingsPage.eng_w`; `collect` ghi
    `<eng>_api_keys` + `<eng>_api_key` (= key 1, tương thích) + `<eng>_batch` + `<eng>_rpm`. Bỏ ô "Số câu mỗi lần gửi AI" chung.
  * `skills.STD_SKILL` "Dịch chuẩn – giữ đúng số câu, tên riêng chuẩn" (id `chuan`, mặc định `translate_skill`): đúng 1 dòng
    ra cho mỗi dòng vào, không gộp / tách / chuyển chữ sang dòng bên, dòng cảm thán vẫn dịch, theo ngữ cảnh, tên người
    Hán Việt, địa danh tên Việt quen dùng, thương hiệu tên quốc tế, nhất quán. SỬA ĐƯỢC (không phải builtin): chưa sửa thì
    không ghi vào Cài đặt; sửa -> lưu vào `translate_skills` cùng id; "Về mặc định" (nút Xoá đổi tên) xoá bản đã sửa.
    `ai_clean_before_translate` mặc định False (bước AI nối câu trước khi dịch không còn tự chạy).

- 2.0.63: người dùng không thích tiêu đề dài dòng -> tiêu đề ngắn: "Công cụ dịch", khung "Gemini" / "Claude" / "OpenAI" /
  "Ollama" (giải thích để ở dòng mô tả dưới tiêu đề), "Khi câu đọc dài hơn chỗ trống", thẻ "Kỹ năng dịch AI" (tab Phụ đề).
  Viết tiêu đề mới: chỉ tên, không thêm cụm giải thích.

- 2.0.64: "bấm Bắt đầu dịch tự động có làm trọn không" (có: nghe → dịch → lồng tiếng CHỈ khi bật "Lồng tiếng (đọc bản
  dịch)" → xoá phụ đề cũ → xuất), "chọn giọng như mục 2", "vừa tách vừa dịch, dịch xong hiện ngay tab Phụ đề, có phụ đề
  là đọc giọng luôn", "mục Việc đang làm", "mọi việc chạy đồng thời, tool tự chia luồng".
  * `jobs.JobManager` 4 luồng: `_why_wait` (≥ 4 việc / việc khác của cùng dự án / việc GPU khác) -> job.message "Đang chờ:
    …"; `READONLY_KINDS` (preview, tool) chạy cùng việc khác của dự án; `GPU_KINDS` auto/recognize/separate/diarize/inpaint/
    dub/redub/realign + translate khi Cài đặt dùng hymt; `clear_finished`, `counts`. `progress_hook` theo luồng
    (`_by_thread`, thanh tqdm ở luồng phụ của thư viện -> ngữ cảnh mới nhất). Dữ liệu dùng chung giữa 2 việc khác dự án:
    `_KEY_*` của LLMClient (list/dict, đủ an toàn), tiến trình VieNeu/Omni/Qwen (chỉ việc GPU dùng -> không chạy cùng lúc).
  * Thanh bên mục "Việc đang làm" (`JobsPage`: Đang chạy / Đang chờ (vì sao) / Đã xong, mỗi dòng `JobRow`: %, còn ~,
    thời gian chạy, GPU, Mở dự án, Huỷ; "Xoá danh sách đã xong"), số việc trên icon (`NavButton.set_badge`); thanh dưới
    "+N việc khác đang chạy" + nút "Việc đang làm"; khung tiến độ trang dự án "(+N việc)".
  * Tab Tự động: "Giọng lồng" `a_voice` = danh sách giọng của mục 2 (`_fill_auto_voices` trong `_fill_speakers`) + "Theo
    giọng ở tab Lồng tiếng mục 2" ("") + "Tự gán giọng nam / nữ" ("@auto"), nút nghe thử; `pipeline._auto_voices` (1 giọng
    -> `voices` mọi người nói; @auto -> `speaker_genders` + `tts.auto_assign`). Công tắc "Vừa tách vừa dịch" (`auto_parallel`,
    mặc định bật; hàng loạt + dòng lệnh theo Cài đặt).
  * Vừa tách vừa dịch (`opts["parallel"]`): `recognize(on_draft=)` – Qwen3 `transcribe_qwen(on_chunk=)` nghe theo nhóm
    `STREAM_GROUP` 6 khúc, mỗi nhóm -> `align.draft_lines` (split_timed theo ct + join_broken, bản sao) -> câu nháp; bộ
    khác: cả phim sau ASR; OCR: sau khi quét. `stream.LivePipeline`: luồng dịch gom đủ `Translator.batch` (= "Số câu mỗi
    lần gửi" của công cụ) rồi `translate_segments(auto_terms=False, summary=hồ sơ phim nếu có)`, ghi `work/live_draft.json`;
    luồng đọc trước (chỉ Edge, chỉ khi mọi câu chắc chắn cùng 1 giọng: chọn 1 giọng / 1 người nói / chưa chọn giọng riêng)
    `synthesize_dub(prepare_only=True)` nhóm ≤ 40 câu / 3 s -> kho work/tts (cùng mã hash với bước lồng tiếng). Nghe xong:
    `finish()` (bỏ câu nháp chưa gửi), gắn bản dịch vào câu cuối cùng trùng chữ gốc (`live_reused`), gộp bộ nhớ dịch,
    `translate(only_empty)` dịch câu bị đổi, `dub` dùng lại file đã đọc. Hy-MT2 / VieNeu / OmniVoice (GPU) không chạy
    song song với bộ nghe. Tab Phụ đề hiện câu nháp khi việc auto đang chạy (`_show_live_draft`, bảng chỉ xem,
    `_save_segs_now` không ghi đè; xong -> về phụ đề thật). Dòng tiến độ "· đã dịch N/M câu · đọc trước K câu".
  * `pipeline.dub_default_voice` tách từ `dub`. `_auto_steps` / `_start_live` tách từ `auto`.
  * Thử: hàng đợi (4 việc song song, GPU lần lượt, cùng dự án lần lượt), tqdm 2 luồng, Qwen theo nhóm, auto song song
    (gửi 3 lô 3 câu lúc đang nghe, chỉ dịch + đọc lại 1 câu bị đổi, 9 câu đọc trước được dùng lại), chụp giao diện 1600/1366.

- 2.0.65: "lồng tiếng chọn sai giọng, tạo đi tạo lại mãi chỉ có 1 giọng duy nhất". Giả lập lại trên giao diện (Edge thật
  qua `_edge_all` giả, VieNeu giả ghi tên giọng gửi vào bộ đọc; đã đọc mã vieneu 3.8.3 `v3turbo._resolve_ref` /
  omnivoice 0.2.1 `generate` – tool gọi đúng API): mục 2 / Tạo lại giọng câu đã tích / Nghe thử đều đọc đúng giọng. Các
  đường ra "cả phim 1 giọng" mà không báo: (1) nhiều người nói đều "(Mặc định của bộ đọc)" -> 1 giọng mặc định;
  (2) câu có giọng riêng (`extra["voice"]`, Đổi giọng câu đã tích / khung Câu đang chọn) đè giọng người nói -> đổi mục 2
  không có tác dụng; (3) chỉ 1 người nói (Số người nói = 1 / tắt phân biệt); (4) giọng VieNeu / OmniVoice mà bộ đọc không
  dùng được -> đổi im lặng sang Hoài My. Sửa: `dub` (đầy đủ, không truyền giọng) nhiều người nói mà chưa ai có giọng ->
  `tts.auto_assign` (nam / nữ theo `speaker_genders`) lưu `voices`, `stats["auto_voiced"]`; `synthesize_dub` ghi
  `extra["voice_used"]`, `stats["voices_used"]` {người nói: {giọng: số câu}} + `stats["fallback"]` -> `dub_summary` ghi
  "giọng – S1: Bình (VieNeu); S2: …" và "⚠ N câu chọn giọng X nhưng bộ đọc này chưa cài…" (`pipeline.voice_label`);
  `_set_voice` (mục 2): người nói có câu giọng riêng -> hỏi dùng giọng mới cho cả các câu đó; tab Tự động chọn giọng
  (`_auto_voices`) bỏ giọng riêng từng câu; dòng "Giọng đọc –" ở mục 3 báo "chỉ có 1 người nói…" / "chưa chọn giọng:
  tự gán…" / "N câu có giọng riêng (đè lên giọng người nói)". Lưu ý: Edge tiếng Việt chỉ có 2 giọng (Hoài My, Nam Minh).

## Vấn đề đang mở
- Chờ người dùng xác nhận 2.0.9 nghe được giọng lồng trong trình phát và trong video xuất.
- OmniVoice chưa chạy thật trên máy người dùng (chỉ kiểm thử với bản giả lập) – chờ phản hồi.
- fa-zh (căn từng chữ) chưa chạy thật (sandbox không tải được ModelScope) – nếu lỗi tool tự dùng cách dự phòng theo độ to giọng.
- Đề xuất chưa làm: tải SenseVoice từ HuggingFace trước ModelScope; Bandit v2 (lời thoại/nhạc/tiếng động).
- 2.0.23/2.0.24: chờ người dùng chạy lại "1. Nghe & tách câu" xác nhận câu trọn vẹn, không sót; Mel-RoFormer Kim, fa-zh, ECAPA (gộp người nói ảo) chưa chạy thật (sandbox không có torch/ModelScope).
- 2.0.25: chờ người dùng thử bảng câu lồng tiếng + Tạo lại giọng các câu đã tích trên máy thật (VieNeu/OmniVoice).
- 2.0.26: chờ người dùng cập nhật edge-tts>=7.3.1 và xác nhận Edge hết 403.
- 2.0.28: chờ người dùng xem giao diện mới trên máy thật (độ rộng các khung, cỡ chữ, timeline).
- 2.0.30: chờ người dùng xem bố cục mới trên máy thật.
- 2.0.32: chờ người dùng thử OCR mới trên video thật (tốc độ quét, vùng tự dò, phụ đề nhiều dòng / màu).
- 2.0.33: chờ người dùng thử kéo phụ đề / nền / vùng che trên video và nút Chạy xoá bằng AI (LaMa thật, GPU).
  Lưu ý: cỡ chữ xem trước giờ nhỏ hơn trước (đúng như video xuất) – nếu thấy nhỏ thì tăng Cỡ chữ.
- 2.0.34: chờ người dùng thử Hy-MT2 thật (tải 3,6 GB), mô hình giọng nam/nữ thật, VieNeu kiểu đọc "Rõ ràng"
  (temperature 0,6 chưa nghe thử trên mô hình thật), 6 giọng thu âm sạch (cần vieneu>=3.8.3), Vá nền trên video thật.
- 2.0.29: chờ người dùng cài `transformers>=5.13 accelerate`, tải Qwen3-ASR (tab Tài liệu) và chạy lại "1. Nghe & tách câu";
  VRAM: 1.7B bf16 ~4 GB + bộ căn 0.6B; lỗi thiếu bộ nhớ -> chọn 0.6B. Chưa làm: AI chấm câu / sửa lời trước khi tách.
- 2.0.35: chờ người dùng thử tab Âm thanh trên máy thật (loa Windows: kéo thanh khi đang phát nghe đổi ngay, không
  rè / lệch tiếng với hình; video xuất trộn đúng như nghe).
- 2.0.36: chờ người dùng thử karaoke trên máy thật (nhịp chữ theo giọng VieNeu/Edge thật, chữ quét mượt khi phát).
- 2.0.37: chờ người dùng nghe thử giọng OmniVoice chọn sẵn với tiếng Việt trên máy thật (mô hình thật chưa chạy ở sandbox).
- 2.0.38: chờ người dùng xác nhận hết mất chữ cuối khi nghe trong tool; nếu file bản lồng vẫn thiếu chữ -> làm kiểm tra lời đọc.
- 2.0.39: chờ người dùng xác nhận đổi cỡ chữ không còn làm giật giọng lồng khi phát.
- 2.0.40: chờ người dùng thử timeline mới (chọn nhiều, khoảng trống, công cụ theo dòng, chèn video) + tốc độ đọc.
- 2.0.51: chờ người dùng thử đăng nhập Bilibili bằng mã QR + tải video 720p/1080p (API QR web chưa gọi thật từ sandbox).
- 2.0.50: chờ người dùng tải lại Hy-MT2 – xem có tự chuyển sang ModelScope và tải được không (tốc độ ModelScope từ VN).
- 2.0.48: chờ người dùng thử Xoá sạch (nền khung trước / sau) + Gỡ logo cố định trên video thật 1080p (tốc độ CPU:
  sandbox ~0,05 s / khung dải 640×130; LaMa chỉ còn vẽ phần chưa từng lộ ra), bo góc làm mờ / nền phụ đề, độ dài dòng.
- 2.0.47: chờ người dùng thử dịch + lồng tiếng Anh / Thái / Nhật / Hàn trên máy thật (Edge giọng nước ngoài, OmniVoice
  `language` khác "vi" chưa chạy thật – nếu bản OmniVoice báo lỗi tham số language thì cần đổi mã sang tên tiếng),
  và xác nhận "1. Nghe & tách câu" có báo lỗi tách giọng (nếu có) để biết vì sao tab Âm thanh không có bản tách.
- 2.0.57: chờ người dùng chạy lại "1. Nghe & tách câu" (hoặc bấm "Nối câu bị cắt" ở tab Phụ đề) trên video thật – xem
  câu có liền mạch, có nối nhầm 2 lượt nói khác người không (khi để 1 người nói thì không phân biệt được).
- 2.0.55: chờ người dùng thử kỹ năng dịch AI với API thật (DeepSeek / GPT / Claude…): AI sửa lỗi nghe có sửa đúng
  không, số câu bị nối, hồ sơ phim, kiểm tra bản dịch (chỉ thử với AI giả lập ở sandbox).
- 2.0.59: chờ người dùng thử rút dây mạng / tắt wifi giữa lúc lồng tiếng Edge / dịch AI (dòng trạng thái báo mất mạng,
  có mạng lại tự làm tiếp), và "Tự tìm câu hát" trên phim thật (chỉ thử với 1 bài hát tiếng Anh + 1 phim hoạt hình).
- 2.0.62: chờ người dùng thử Gemini gói miễn phí thật (sandbox không gọi được Google API): thông báo 429 của Gemini
  qua endpoint tương thích OpenAI có đúng chữ "PerDay" / "retry in" như đã đoán không; `response_format` json_object có bị
  từ chối không (đã có tự bỏ khi 400).
- 2.0.65: chờ người dùng cho biết bộ đọc / giọng đang chọn khi bị "chỉ 1 giọng" (dòng "giọng – S1: …" sau khi lồng sẽ
  cho thấy giọng thật đã đọc); nếu VieNeu / OmniVoice thật đọc mọi giọng như nhau thì lỗi nằm ở mô hình, cần file log.
- 2.0.64: chờ người dùng thử "Vừa tách vừa dịch" trên phim thật (Qwen3 + Gemini / DeepSeek + Edge): câu dịch hiện dần ở tab
  Phụ đề, bao nhiêu câu phải dịch lại sau khi nghe xong; chạy 2 dự án cùng lúc (1 việc GPU + 1 việc mạng / xuất video).
- 2.0.42: chờ người dùng nghe thử các cách xếp giọng trên video thật (nhất là Tự động với cụm câu dài, Đọc đè 2 giọng
  cùng lúc) và xem phụ đề chạy theo giọng lồng có dễ theo dõi không.
