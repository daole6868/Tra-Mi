"""Gemini XEM + NGHE video (2.1.25): soát phụ đề gốc bằng hình + tiếng (pipeline.gemini_review – trami/vreview.py) và
dịch có nhìn hình (translate.Translator.video).

Gemini nhận thẳng video (lấy ~1 khung / giây + tiếng). Không gửi cả phim: cắt từng khúc vài phút thành bản nhẹ
(`clip`: 480p, 1 khung / giây, tiếng mono 16 kHz ~ 1 MB / phút) -> tải lên bằng File API (`upload`, tải tiếp được) ->
chờ Gemini xử lý xong (`_wait_active`) -> hỏi kèm chữ (`ask`) -> xoá file trên máy chủ Google (`cleanup`). Giờ do
Gemini đoán chỉ chính xác ~0,5–1 s -> Gemini trả lời theo SỐ DÒNG, mốc giờ vẫn của tool.

Dùng chung key / lượt gửi mỗi phút / đổi key khi hết lượt / chờ mạng / nút Huỷ với công cụ dịch Gemini (LLMClient).
File tải lên thuộc về đúng key đã tải -> đổi key thì tải lại.
"""
from __future__ import annotations

import hashlib
import json
import threading
import time
from pathlib import Path

import requests

from . import ffmpeg as ff, net
from .translate import (ENGINE_NAMES, THINK_TOK, WAIT_LIMIT_429, ApiError, LLMClient, _KEY_DEAD, _key_tag,
                        api_keys, DAY_BLOCK)

API = "https://generativelanguage.googleapis.com"
CLIP_H = 480            # chiều cao bản nhẹ: đủ đọc chữ trên màn hình / phụ đề cứng, tải lên nhanh
CLIP_FPS = 1            # Gemini lấy 1 khung / giây -> gửi đúng 1 khung / giây (file nhỏ)
ACTIVE_WAIT = 600       # chờ Gemini xử lý video tối đa 10 phút
MAX_OUT = 65536


def ready(settings: dict) -> tuple[bool, str]:
    """Có dùng được Gemini xem video không (cần API key Gemini – Cài đặt → khung Gemini)."""
    if not api_keys(settings, "gemini"):
        return False, "chưa có API key Gemini (Cài đặt → Công cụ dịch → khung Gemini)"
    return True, ENGINE_NAMES["gemini"]


class VideoSource:
    """Cắt khúc video nhẹ để gửi Gemini (mỗi khúc 1 file đệm, dùng lại trong phiên) + hỏi Gemini kèm khúc đó."""

    def __init__(self, src: str | Path, work: str | Path, settings: dict, low_res: bool = False):
        self.src = Path(src)
        self.work = Path(work)
        self.work.mkdir(parents=True, exist_ok=True)
        self.client = GeminiVideo(settings, low_res=low_res)
        self._lock = threading.Lock()
        self._clips: dict[tuple[int, int], Path] = {}
        st = self.src.stat()
        self._sig = hashlib.md5(f"{self.src.resolve()}|{st.st_size}|{int(st.st_mtime)}".encode()).hexdigest()[:8]
        self.asked = 0       # số lần hỏi Gemini có kèm video
        self.failed = 0      # số lần hỏi kèm video lỗi (đã quay về chỉ gửi chữ)

    def clip(self, a: float, b: float, cancel=None) -> Path:
        """Khúc video [a, b] giây (giờ video gốc) – bản nhẹ để tải lên Gemini."""
        a, b = max(0.0, float(a)), max(float(a) + 1.0, float(b))
        key = (int(round(a * 10)), int(round(b * 10)))
        with self._lock:
            p = self._clips.get(key)
            if p and p.exists():
                return p
        out = self.work / f"g_{self._sig}_{key[0]}_{key[1]}.mp4"
        if not (out.exists() and out.stat().st_size > 0):
            tmp = out.with_suffix(".tmp.mp4")
            ff.run(["-ss", f"{a:.3f}", "-t", f"{b - a:.3f}", "-i", str(self.src.resolve()),
                    "-vf", f"fps={CLIP_FPS},scale=-2:{CLIP_H}", "-c:v", "libx264", "-preset", "veryfast", "-crf", "30",
                    "-pix_fmt", "yuv420p"] + ff.af_args(ff.audio_sync(self.src, 16000)) +
                   ["-c:a", "aac", "-b:a", "48k", "-ac", "1", "-ar", "16000", "-movflags", "+faststart", str(tmp)],
                   b - a, None, cancel, "Đang cắt khúc video gửi Gemini")
            ff.replace_file(tmp, out)
        with self._lock:
            self._clips[key] = out
        return out

    def ask(self, system: str, user: str, a: float, b: float, max_tokens: int = 8000, cancel=None) -> str:
        """Hỏi Gemini kèm khúc video [a, b]."""
        path = self.clip(a, b, cancel)
        self.asked += 1
        return self.client.ask(system, user, path, max_tokens)

    def cleanup(self, keep_clips: bool = True) -> None:
        """Xoá các file đã tải lên máy chủ Google (video không nằm lại ở đó); keep_clips=False xoá cả khúc đệm trên máy."""
        self.client.cleanup()
        if not keep_clips:
            for p in list(self._clips.values()):
                p.unlink(missing_ok=True)
            self._clips.clear()

    @staticmethod
    def drop_stale(work: str | Path, sig_keep: str = "") -> None:
        """Dọn khúc đệm của video cũ (đổi video gốc / cắt lại)."""
        for p in Path(work).glob("g_*.mp4"):
            if not sig_keep or not p.name.startswith(f"g_{sig_keep}_"):
                p.unlink(missing_ok=True)


class GeminiVideo(LLMClient):
    """Gọi thẳng API Gemini (không qua cổng giả lập OpenAI – cổng đó không nhận video)."""

    def __init__(self, settings: dict, low_res: bool = False):
        super().__init__(settings, "gemini")
        self.low_res = low_res
        self._uploaded: dict[tuple[str, str], dict] = {}   # (key, file) -> file trên máy chủ Google
        self._up_lock = threading.Lock()
        self._send_res = True
        self._send_mime = True

    # ------------------------------------------------------------------ HTTP (tách riêng để kiểm thử)
    def _req(self, method: str, url: str, read: float = 120, **kw):
        kw.setdefault("timeout", (15, read))
        return net.call(net.session().request, method, url, **kw)

    def _hdr(self, key: str, extra: dict | None = None) -> dict:
        h = {"x-goog-api-key": key}
        h.update(extra or {})
        return h

    # ------------------------------------------------------------------ File API
    def upload(self, path: str | Path, key: str) -> dict:
        """Tải 1 file lên (resumable). Trả {"name", "uri", "mimeType", "state"}."""
        path = Path(path)
        size = path.stat().st_size
        r = self._req("POST", f"{API}/upload/v1beta/files", headers=self._hdr(key, {
            "X-Goog-Upload-Protocol": "resumable", "X-Goog-Upload-Command": "start",
            "X-Goog-Upload-Header-Content-Length": str(size), "X-Goog-Upload-Header-Content-Type": "video/mp4",
            "Content-Type": "application/json"}), json={"file": {"display_name": f"subvideo_{path.stem}"}})
        if r.status_code != 200:
            return self._fail(r)
        url = r.headers.get("x-goog-upload-url") or r.headers.get("X-Goog-Upload-URL")
        if not url:
            raise ApiError("Gemini không trả địa chỉ tải video lên")
        with open(path, "rb") as f:
            data = f.read()
        r = self._req("POST", url, read=600, headers={"Content-Length": str(size), "X-Goog-Upload-Offset": "0",
                                                       "X-Goog-Upload-Command": "upload, finalize"}, data=data)
        if r.status_code != 200:
            return self._fail(r)
        return (r.json() or {}).get("file") or {}

    def _wait_active(self, f: dict, key: str) -> dict:
        t0 = time.time()
        while (f.get("state") or "ACTIVE") == "PROCESSING":
            if time.time() - t0 > ACTIVE_WAIT:
                raise ApiError("Gemini xử lý video quá lâu (> 10 phút)")
            net.sleep(2.0)
            r = self._req("GET", f"{API}/v1beta/{f['name']}", headers=self._hdr(key))
            if r.status_code != 200:
                return self._fail(r)
            f = r.json() or f
        if f.get("state") == "FAILED":
            raise ApiError("Gemini không đọc được khúc video: " + json.dumps(f.get("error") or {})[:200])
        return f

    def _file(self, path: str | Path, key: str) -> dict:
        """File đã tải lên bằng key này (tải lần đầu, các lần hỏi sau dùng lại)."""
        k = (key, str(path))
        with self._up_lock:
            f = self._uploaded.get(k)
        if f:
            return f
        f = self._wait_active(self.upload(path, key), key)
        with self._up_lock:
            self._uploaded[k] = f
        return f

    def cleanup(self) -> None:
        with self._up_lock:
            items = list(self._uploaded.items())
            self._uploaded.clear()
        for (key, _p), f in items:
            if not f.get("name"):
                continue
            try:
                self._req("DELETE", f"{API}/v1beta/{f['name']}", read=30, headers=self._hdr(key))
            except Exception:  # noqa: BLE001 – file tự hết hạn sau 48 giờ trên máy chủ Google
                pass

    # ------------------------------------------------------------------ hỏi
    def _fail(self, r):
        """Lỗi HTTP -> ném lỗi phù hợp để ask() xử lý (đổi key / chờ / báo)."""
        raise _HttpError(r)

    def ask(self, system: str, user: str, path: str | Path, max_tokens: int = 8000) -> str:
        """Hỏi Gemini kèm 1 file video. Trả lời bị cắt vì hết độ dài -> `self._tl.cut` = True."""
        name = ENGINE_NAMES["gemini"]
        fails = waits = 0
        last_err = None
        self._tl.cut = False
        while fails < 4:
            key = self.api_key
            try:
                self._pace()
                f = self._file(path, key)
                ask = min(MAX_OUT, max(16000, max_tokens + THINK_TOK) if self.thinking else max_tokens)
                cap = self.out_cap()
                ask = min(ask, cap) if cap else ask
                cfg = {"temperature": 0.2, "maxOutputTokens": ask}
                if self._send_mime:
                    cfg["responseMimeType"] = "application/json"
                if self._send_res and self.low_res:
                    cfg["mediaResolution"] = "MEDIA_RESOLUTION_LOW"
                body = {"systemInstruction": {"parts": [{"text": system}]},
                        "contents": [{"role": "user", "parts": [
                            {"fileData": {"mimeType": f.get("mimeType") or "video/mp4", "fileUri": f.get("uri")}},
                            {"text": user}]}],
                        "generationConfig": cfg}
                r = self._req("POST", f"{API}/v1beta/models/{self.model}:generateContent", read=max(300.0, ask / 20 + 180),
                              headers=self._hdr(key, {"Content-Type": "application/json"}), json=body)
                if r.status_code == 200:
                    d = r.json() or {}
                    fb = d.get("promptFeedback") or {}
                    if fb.get("blockReason"):
                        raise ApiError(f"Gemini từ chối khúc video này ({fb['blockReason']})")
                    cand = (d.get("candidates") or [{}])[0]
                    self._tl.cut = cand.get("finishReason") == "MAX_TOKENS"
                    parts = (cand.get("content") or {}).get("parts") or []
                    return "".join(p.get("text", "") for p in parts if not p.get("thought"))
                raise _HttpError(r)
            except _HttpError as he:
                r = he.r
                t = r.text or ""
                tl = t.lower().replace("_", "")
                if r.status_code == 400 and self._send_res and "mediaresolution" in tl:
                    self._send_res = False      # model / API cũ không nhận độ phân giải video thấp -> bỏ, gửi lại
                    continue
                if r.status_code == 400 and self._send_mime and "responsemimetype" in tl:
                    self._send_mime = False
                    continue
                if r.status_code == 404:
                    raise ValueError(f"Không tìm thấy model '{self.model}' ({t[:200]})") from None
                why = self._key_failed(r)
                if why:
                    tag = _key_tag("gemini", self.ki, len(self.keys))
                    _KEY_DEAD[key] = (time.time() + (DAY_BLOCK if "hôm nay" in why or "tiền" in why else 10 ** 9), why)
                    if self._switch(f"{tag} {why}"):
                        continue
                    raise ApiError(f"Tất cả {len(self.keys)} key {name} đều {why} – thêm key ở Cài đặt (khung Gemini)")
                if r.status_code == 429:
                    if self._switch(f"{_key_tag('gemini', self.ki, len(self.keys))} bị giới hạn số lần / phút"):
                        continue
                    if waits < WAIT_LIMIT_429:
                        waits += 1
                        wait = self._retry_after(r)
                        _, report = net.current()
                        if report:
                            report(f"{name} báo gửi quá nhanh – chờ {wait:.0f} s rồi gửi tiếp")
                        net.sleep(wait)
                        continue
                last_err = RuntimeError(f"HTTP {r.status_code}: {t[:300]}")
            except (ApiError, ValueError, PermissionError):
                raise
            except requests.RequestException as e:
                last_err = e
                if net.is_net_error(e) and net.wait_online("Gemini xem video"):
                    continue
            fails += 1
            if fails < 4:
                net.sleep(2 * 2 ** (fails - 1))
        raise ApiError(f"Không gửi được video cho Gemini ({self.model}): {str(last_err)[:250]}")


class _HttpError(Exception):
    def __init__(self, r):
        super().__init__(f"HTTP {r.status_code}")
        self.r = r


def rel(t: float, a: float) -> str:
    """Giây tính từ đầu khúc video, dạng 12.3"""
    return f"{max(0.0, t - a):.1f}"
