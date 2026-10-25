"""Dòng lệnh TraMi.

  python -m trami   (hoặc python TraMi.py)   -> mở giao diện
  python -m trami dich video.mp4       -> dịch tự động 1 video (hoặc link)
  python -m trami dich a.mp4 b.mp4 --engine deepseek --ocr --xoa-phude inpaint --long-tieng
  Mặc định dừng trước bước xuất video (mở tool kiểm tra rồi bấm Xuất video); thêm --xuat-video để xuất luôn.
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

# Ẩn cảnh báo không cần thiết khi tải mô hình Whisper
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
os.environ.setdefault("HF_HUB_VERBOSITY", "error")
os.environ.setdefault("PYTHONUTF8", "1")

# Windows console hiển thị tiếng Việt
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
        sys.stderr.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:
        pass


def gui():
    from .gui.main import run

    return run()


def cli_translate(args):
    from . import pipeline
    from .config import load_settings
    from .project import Project

    opts = {
        "method": "ocr" if args.ocr else "asr",
        "engine": args.engine,
        "style": args.style,
        "target": args.dich_sang,
        "dub": args.long_tieng,
        "hardsub_mode": args.xoa_phude,
        "burn": not args.khong_ghep,
        "which": args.hien_thi,
        "render": bool(args.xuat_video) and not args.chi_phude,   # mặc định dừng trước bước xuất
        "denoise": args.loc_nhieu,
        "parallel": bool(load_settings().get("auto_parallel", False)),
    }
    from .sitelogin import extract_url

    for item in args.inputs:
        url = "" if Path(item).exists() else extract_url(item)   # link / dòng chia sẻ của app
        is_url = bool(url)
        p = Project.create(Path(item).stem if not is_url else "Video từ link")
        if is_url:
            opts["url"] = url
        else:
            pipeline.attach_source(p, Path(item))
        last = [0.0]

        def prog(x, m):
            if time.time() - last[0] > 0.5 or x >= 1:
                last[0] = time.time()
                bar = "#" * int(x * 30)
                print(f"\r[{bar:<30}] {x * 100:5.1f}%  {m[:60]:<60}", end="", flush=True)

        print(f"\n>>> {item}")
        res = pipeline.auto(p, dict(opts), prog)
        print()
        if res.get("srt"):
            print("  Phụ đề :", res["srt"])
        if res.get("video"):
            print("  Video  :", res["video"])
        elif opts["render"] is False and not args.chi_phude:
            print(f"  Chưa xuất video: mở tool -> dự án \"{p.data.get('name', '')}\" -> kiểm tra rồi bấm Xuất video "
                  "(hoặc chạy lại với --xuat-video)")
        if args.output and (res.get("video") or res.get("srt")):
            import shutil

            Path(args.output).mkdir(parents=True, exist_ok=True)
            for k in ("video", "srt"):
                if res.get(k):
                    shutil.copy2(res[k], args.output)


def main(argv=None):
    from .config import fix_std_streams

    fix_std_streams()
    ap = argparse.ArgumentParser(prog="trami", description="Subvideo - dịch & lồng tiếng video tự động")
    sub = ap.add_subparsers(dest="cmd")
    sub.add_parser("ui", help="Mở giao diện (mặc định)")

    d = sub.add_parser("dich", help="Dịch tự động video/link")
    d.add_argument("inputs", nargs="+", help="File video hoặc link (Douyin, Bilibili, YouTube...)")
    d.add_argument("--engine", default=None, help="google/openai/anthropic/gemini/deepseek/ollama")
    d.add_argument("--style", default=None, help="auto/co_trang/hien_dai/review/hoat_hinh/tai_lieu")
    d.add_argument("--dich-sang", default=None, choices=["vi", "en", "th", "ja", "ko"],
                   help="Ngôn ngữ đích: vi (Việt, mặc định) / en (Anh) / th (Thái) / ja (Nhật) / ko (Hàn)")
    d.add_argument("--ocr", action="store_true", help="Lấy phụ đề bằng OCR chữ Trung có sẵn trên video")
    d.add_argument("--xoa-phude", default="none", choices=["none", "blur", "box", "delogo", "inpaint", "patch", "stretch"],
                   help="Cách xử lý phụ đề Trung cũ trên video")
    d.add_argument("--long-tieng", action="store_true", help="Lồng tiếng (đọc bản dịch) bằng AI")
    d.add_argument("--khong-ghep", action="store_true", help="Không ghép phụ đề vào video")
    d.add_argument("--hien-thi", default="trans", choices=["trans", "both", "source"])
    d.add_argument("--chi-phude", action="store_true", help="Chỉ xuất file phụ đề, không xuất video")
    d.add_argument("--xuat-video", action="store_true",
                   help="Xuất video luôn (mặc định dừng trước bước xuất để kiểm tra rồi bấm Xuất video trong tool)")
    d.add_argument("--loc-nhieu", action="store_true", help="Lọc nhiễu âm thanh trước khi nhận dạng")
    d.add_argument("-o", "--output", default=None, help="Thư mục chép kết quả ra")

    args = ap.parse_args(argv)
    if args.cmd == "dich":
        cli_translate(args)
    else:
        sys.exit(gui())


if __name__ == "__main__":
    main()
