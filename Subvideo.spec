# -*- mode: python ; coding: utf-8 -*-
# Đóng gói Subvideo thành thư mục phần mềm dist\Subvideo\ (Subvideo.exe). Chạy: DongGoi_EXE.bat  (hoặc: pyinstaller Subvideo.spec)
# Gồm ĐỦ thư viện đã cài trong .venv (PyTorch GPU, nhận dạng, tách giọng, đọc giọng, dịch trên máy…) nhưng KHÔNG gồm
# mô hình AI: mô hình tải ở tab Tài liệu vào thư mục dữ liệu (datadir.txt / data / %LOCALAPPDATA%\Subvideo) như bản Python.
import importlib.util

from PyInstaller.utils.hooks import collect_all, collect_submodules

datas = [("fonts", "fonts"), ("assets", "assets")]
binaries = []
hiddenimports = collect_submodules("trami") + ["PySide6.QtMultimedia", "PySide6.QtMultimediaWidgets", "PySide6.QtSvg"]

# Gói có dữ liệu / mô-đun nạp động -> gom đầy đủ (chỉ gom gói nào đã cài; gói lỗi khi gom thì bỏ qua, báo ra màn hình)
PKGS = ("faster_whisper", "ctranslate2", "rapidocr_onnxruntime", "onnxruntime", "opencc", "jieba", "edge_tts", "yt_dlp",
        "deep_translator", "imageio_ffmpeg", "funasr", "modelscope", "audio_separator", "speechbrain", "hyperpyyaml",
        "vieneu", "vieneu_utils", "sea_g2p", "omnivoice", "transformers", "tokenizers", "safetensors", "accelerate",
        "huggingface_hub", "hf_xet", "librosa", "soundfile", "torchaudio", "torchcodec", "kaldi_native_fbank",
        "sentencepiece", "einops", "rotary_embedding_torch", "beartype", "onnx", "pydub")
for pkg in PKGS:
    if importlib.util.find_spec(pkg) is None:
        continue
    try:
        d, b, h = collect_all(pkg)
    except Exception as e:  # noqa: BLE001
        print(f"[!] Bo qua goi {pkg}: {e}")
        continue
    datas += d
    binaries += b
    hiddenimports += h

kw = {}
try:   # transformers / torch đọc mã nguồn (.py) lúc chạy -> giữ cả file .py
    kw["module_collection_mode"] = {"transformers": "pyz+py", "torch": "pyz+py", "omnivoice": "pyz+py",
                                    "vieneu": "pyz+py", "funasr": "pyz+py", "speechbrain": "pyz+py"}
    a = Analysis(
        ["TraMi.py"],
        pathex=["."],
        binaries=binaries,
        datas=datas,
        hiddenimports=hiddenimports,
        hookspath=[],
        runtime_hooks=[],
        excludes=["tkinter", "matplotlib", "IPython", "jupyter", "notebook", "pytest", "gradio", "tensorflow", "keras",
                  "tensorboard"],
        noarchive=False,
        **kw,
    )
except TypeError:      # PyInstaller cũ chưa có module_collection_mode
    a = Analysis(["TraMi.py"], pathex=["."], binaries=binaries, datas=datas, hiddenimports=hiddenimports,
                 excludes=["tkinter", "matplotlib", "IPython", "jupyter", "notebook", "pytest", "gradio",
                           "tensorflow", "keras", "tensorboard"], noarchive=False)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Subvideo",
    console=False,                 # không hiện cửa sổ đen
    icon="assets/Subvideo.ico",
    upx=False,
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="Subvideo")
