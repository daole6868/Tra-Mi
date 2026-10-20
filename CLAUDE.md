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
- `trami/asr_qwen.py` + `qwen_asr_worker.py` (Qwen3-ASR + Qwen3-ForcedAligner, tiến trình riêng `--qwen-asr-worker`,
  transformers ≥ 5.13, mặc định từ 2.0.29), `trami/asr.py` (faster-whisper), `asr_funasr.py` (SenseVoice/Paraformer, `mark_songs`), `diarize.py` (ECAPA),
  `separate.py` (audio-separator BS-RoFormer), `lama.py` + `inpaint.py` (xoá chữ), `ocr.py` (RapidOCR),
  `translate.py` (`LLMClient` gọi HTTP: anthropic / OpenAI-compatible / gemini / deepseek / ollama; Google gtx).
- `trami/jobs.py` – hàng đợi 1 luồng cho tác vụ dài; `trami/gui/*` – giao diện (QPainter), `common.run_async`,
  `WheelGuard` (lăn chuột không đổi ô chọn khi chưa bấm vào). Trang dự án bố cục kiểu trình dựng phim (từ 2.0.28):
  trái `IconTabs` + 8 tab, giữa trình phát, phải `gui/inspector.LineInspector` (câu đang chọn), dưới `gui/timeline.TrackTimeline`.
  Khoảng cách khung: `theme.GAP` / `theme.PANEL_PAD`; hàng nhiều nút dùng `widgets.flow()` (tự xuống dòng) thay `hbox`.
  Chụp ảnh kiểm tra giao diện phải nạp theme như `run()`: `theme.load_fonts(); app.setFont(theme.font(10));
  app.setStyleSheet(theme.stylesheet())`.
- `tests/test_core.py` – `python -m pytest -q tests` (61 test; 1 test cần gói AI; test OCR vẽ video bằng font chữ Hán).

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
- 2.0.29: chờ người dùng cài `transformers>=5.13 accelerate`, tải Qwen3-ASR (tab Tài liệu) và chạy lại "1. Nghe & tách câu";
  VRAM: 1.7B bf16 ~4 GB + bộ căn 0.6B; lỗi thiếu bộ nhớ -> chọn 0.6B. Chưa làm: AI chấm câu / sửa lời trước khi tách.
