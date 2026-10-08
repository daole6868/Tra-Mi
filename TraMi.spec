# -*- mode: python ; coding: utf-8 -*-
# Đóng gói TraMi thành thư mục phần mềm (TraMi.exe). Chạy: DongGoi_EXE.bat  (hoặc: pyinstaller TraMi.spec)
import importlib.util

from PyInstaller.utils.hooks import collect_all

datas = [("fonts", "fonts"), ("assets", "assets")]
binaries = []
hiddenimports = ["trami", "trami.gui.main", "trami.asr", "trami.asr_funasr", "trami.diarize", "trami.separate",
                 "trami.tts", "trami.lama", "trami.ocr", "trami.inpaint", "trami.translate", "trami.video",
                 "PySide6.QtMultimedia", "PySide6.QtMultimediaWidgets", "PySide6.QtSvg"]

# Các gói có dữ liệu/mô-đun nạp động -> gom đầy đủ (chỉ gom gói nào đã cài)
for pkg in ("faster_whisper", "ctranslate2", "rapidocr_onnxruntime", "onnxruntime", "opencc", "edge_tts", "yt_dlp",
            "imageio_ffmpeg", "funasr", "modelscope", "audio_separator", "speechbrain", "vieneu", "sea_g2p",
            "librosa", "torchaudio", "kaldi_native_fbank", "hyperpyyaml"):
    if importlib.util.find_spec(pkg) is None:
        continue
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hiddenimports += h

a = Analysis(
    ["TraMi.py"],
    pathex=["."],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=["tkinter", "matplotlib", "IPython", "jupyter", "pytest"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="TraMi",
    console=False,                 # không hiện cửa sổ đen
    icon="assets/TraMi.ico",
    upx=False,
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="TraMi")
