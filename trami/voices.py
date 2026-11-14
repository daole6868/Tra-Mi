"""Thư viện giọng mẫu để nhái (OmniVoice): file ghi âm 3–10 giây + lời nói trong file.

Lưu ở data/voices/<id>.wav, danh sách ở data/voices/index.json. Dùng chung cho mọi dự án.
"""
from __future__ import annotations

import json
import subprocess
import time
import uuid
from pathlib import Path

from . import ffmpeg as ff
from .config import DATA_DIR

VOICES_DIR = DATA_DIR / "voices"
INDEX = VOICES_DIR / "index.json"
SR = 24000
MAX_SEC = 10.0   # OmniVoice khuyên mẫu 3–10 giây; dài hơn chậm và nhái kém hơn


def _load() -> list[dict]:
    try:
        data = json.loads(INDEX.read_text("utf-8"))
        return [v for v in data if (VOICES_DIR / v["file"]).exists()]
    except Exception:  # noqa: BLE001
        return []


def _save(items: list[dict]) -> None:
    VOICES_DIR.mkdir(parents=True, exist_ok=True)
    INDEX.write_text(json.dumps(items, ensure_ascii=False, indent=1), "utf-8")


def list_samples() -> list[dict]:
    return _load()


def get(sid: str) -> dict | None:
    return next((v for v in _load() if v["id"] == sid), None)


def path_of(v: dict) -> Path:
    return VOICES_DIR / v["file"]


def add_sample(src: str | Path, name: str = "", text: str = "", start: float = 0.0,
               length: float | None = None) -> dict:
    """Chuyển file ghi âm (wav/mp3/m4a/… hoặc video) thành mẫu giọng mono 24 kHz, tối đa 10 giây."""
    VOICES_DIR.mkdir(parents=True, exist_ok=True)
    sid = uuid.uuid4().hex[:8]
    out = VOICES_DIR / f"{sid}.wav"
    dur = min(MAX_SEC, length or MAX_SEC)
    cmd = [ff.ffmpeg_bin(), "-hide_banner", "-loglevel", "error", "-y", "-ss", f"{max(0.0, start):.3f}",
           "-i", str(src), "-t", f"{dur:.3f}", "-vn", "-ac", "1", "-ar", str(SR),
           # bỏ khoảng lặng đầu/cuối, cân âm lượng cho đều
           "-af", "silenceremove=start_periods=1:start_threshold=-45dB,areverse,"
                  "silenceremove=start_periods=1:start_threshold=-45dB,areverse,loudnorm=I=-18:TP=-2",
           "-c:a", "pcm_s16le", str(out)]
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                       creationflags=ff._NO_WINDOW)
    if r.returncode != 0 or not out.exists():
        raise RuntimeError("Không đọc được file ghi âm: " + (r.stderr or "")[-300:])
    real = ff.probe(out).duration
    if real < 1.5:
        out.unlink(missing_ok=True)
        raise ValueError("Đoạn ghi âm quá ngắn (cần ít nhất 3 giây có tiếng nói).")
    items = _load()
    v = {"id": sid, "name": name.strip() or f"Giọng {len(items) + 1}", "text": text.strip(), "file": out.name,
         "dur": round(real, 1), "added": time.strftime("%d/%m/%Y")}
    items.append(v)
    _save(items)
    return v


def update(sid: str, **fields) -> None:
    items = _load()
    for v in items:
        if v["id"] == sid:
            v.update({k: (str(x).strip() if isinstance(x, str) else x) for k, x in fields.items()})
    _save(items)


def delete(sid: str) -> None:
    items = _load()
    for v in items:
        if v["id"] == sid:
            try:
                path_of(v).unlink(missing_ok=True)
            except OSError:
                pass
    _save([v for v in items if v["id"] != sid])
