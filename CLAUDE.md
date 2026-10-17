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
- `tests/test_core.py` – `python -m pytest -q tests` (52 test; 1 test cần gói AI).

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

## Vấn đề đang mở
- Chờ người dùng xác nhận 2.0.9 nghe được giọng lồng trong trình phát và trong video xuất.
- OmniVoice chưa chạy thật trên máy người dùng (chỉ kiểm thử với bản giả lập) – chờ phản hồi.
- fa-zh (căn từng chữ) chưa chạy thật (sandbox không tải được ModelScope) – nếu lỗi tool tự dùng cách dự phòng theo độ to giọng.
- Đề xuất chưa làm: tải SenseVoice từ HuggingFace trước ModelScope; Bandit v2 (lời thoại/nhạc/tiếng động).
- 2.0.23/2.0.24: chờ người dùng chạy lại "1. Nghe & tách câu" xác nhận câu trọn vẹn, không sót; Mel-RoFormer Kim, fa-zh, ECAPA (gộp người nói ảo) chưa chạy thật (sandbox không có torch/ModelScope).
- 2.0.25: chờ người dùng thử bảng câu lồng tiếng + Tạo lại giọng các câu đã tích trên máy thật (VieNeu/OmniVoice).
