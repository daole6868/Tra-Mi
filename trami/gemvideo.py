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
                        _note_ok, api_keys, DAY_BLOCK, note_key, why_state)

API = "https://generativelanguage.googleapis.com"
CLIP_H = 480            # chiều cao bản nhẹ: đủ đọc chữ trên màn hình / phụ đề cứng, tải lên nhanh
CLIP_FPS = 1            # Gemini lấy 1 khung / giây -> gửi đúng 1 khung / giây (file nhỏ)
ACTIVE_WAIT = 600       # chờ Gemini xử lý video tối đa 10 phút
MAX_OUT = 65536
# 2.4.11 – dịch có nhìn hình CHỈ GỬI ĐOẠN CÓ LỜI: ghép các đoạn nhân vật nói (giờ từng câu ± SPEECH_PAD) thành 1 khúc ngắn
SPEECH_PAD = 0.3        # mỗi câu nới 0,3 s 2 bên (đủ thấy miệng mở / người quay sang)
SPEECH_JOIN = 1.0       # 2 đoạn có lời cách ≤ 1 s -> giữ cả khoảng giữa (không cắt vụn)
SPEECH_KEEP = 0.8       # đoạn có lời ≥ 80 % khúc -> gửi khúc liền như cũ (dùng lại khúc đã cắt, không lợi gì khi cắt)
SPEECH_MAX = 120        # tối đa 120 đoạn / khúc (lệnh FFmpeg ngắn) – nhiều hơn thì nối các đoạn cách nhau gần nhất
SPEECH_SEC = 1800.0     # khúc chỉ có lời: lô dịch trải tối đa 30 phút phim (tổng lời vẫn ≤ VIDEO_WIN)


class Clip:
    """Khúc video gửi Gemini: liền [a, b] (spans None) hoặc CHỈ các đoạn có lời `spans` [(đầu, cuối)] giờ gốc (đã làm
    tròn 0,1 s) ghép nối liền. `map(t)` = giây trong khúc của giây t phim gốc (giờ từng dòng gửi kèm lời nhắc)."""

    def __init__(self, a: float, b: float, spans: list[tuple[float, float]] | None = None):
        self.a, self.b = float(a), float(b)
        self.spans = spans or None

    @property
    def length(self) -> float:
        return sum(y - x for x, y in self.spans) if self.spans else self.b - self.a

    def map(self, t: float) -> float:
        if not self.spans:
            return max(0.0, t - self.a)
        off = 0.0
        for x, y in self.spans:
            if t < x:
                return off          # rơi vào chỗ đã bỏ -> đầu đoạn có lời kế tiếp
            if t <= y:
                return off + t - x
            off += y - x
        return off


def speech_spans(times: list[tuple[float, float]], total: float = 0.0, pad: float = SPEECH_PAD,
                 join: float = SPEECH_JOIN, max_spans: int = SPEECH_MAX) -> list[tuple[float, float]]:
    """Đoạn có lời của các dòng: mỗi dòng ± pad, gộp đoạn chồng / cách ≤ join, làm tròn ra ngoài 0,1 s (hình 10
    khung / giây + tiếng 10 ms / khung khi ghép -> đoạn hình và đoạn tiếng dài đúng bằng nhau, không lệch dần)."""
    import math

    iv = sorted((max(0.0, a - pad), (min(total, b + pad) if total else b + pad)) for a, b in times if b > a)
    out: list[list[int]] = []
    for a, b in iv:
        x, y = int(math.floor(a * 10 + 1e-6)), int(math.ceil(b * 10 - 1e-6))
        if y <= x:
            y = x + 1
        if out and x - out[-1][1] <= join * 10:
            out[-1][1] = max(out[-1][1], y)
        else:
            out.append([x, y])
    while len(out) > max_spans:                     # quá nhiều đoạn: nối chỗ cách nhau gần nhất
        k = min(range(len(out) - 1), key=lambda i: out[i + 1][0] - out[i][1])
        out[k][1] = out[k + 1][1]
        del out[k + 1]
    return [(x / 10.0, y / 10.0) for x, y in out]


def ready(settings: dict) -> tuple[bool, str]:
    """Có dùng được Gemini xem video không (cần API key Gemini – Cài đặt → khung Gemini)."""
    if not api_keys(settings, "gemini"):
        return False, "chưa có API key Gemini (Cài đặt → Công cụ dịch → khung Gemini)"
    return True, ENGINE_NAMES["gemini"]


class VideoSource:
    """Cắt khúc video nhẹ để gửi Gemini (mỗi khúc 1 file đệm, dùng lại trong phiên) + hỏi Gemini kèm khúc đó."""

    def __init__(self, src: str | Path, work: str | Path, settings: dict, low_res: bool = False,
                 speech: bool = False, total: float = 0.0):
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
        self.speech = bool(speech)   # 2.4.11: chỉ gửi đoạn có lời (ghép các đoạn nhân vật nói)
        self.total = float(total or 0.0)
        self.sent_sec = 0.0          # tổng giây video đã gửi (mỗi khúc tính 1 lần)
        self.full_sec = 0.0          # … nếu gửi khúc liền như trước
        self._counted: set = set()

    def plan(self, times: list[tuple[float, float]], pad: float = 3.0) -> Clip:
        """Khúc video cho 1 lô dịch: giờ các dòng (đầu, cuối). Liền = [dòng đầu − pad, dòng cuối + pad] như trước; chỉ có
        lời (`speech`) = các đoạn nói ghép lại – trừ khi đoạn có lời đã chiếm ≥ SPEECH_KEEP khúc (gửi khúc liền, dùng
        lại khúc đã cắt sẵn)."""
        a = max(0.0, min(t[0] for t in times) - pad)
        b = max(t[1] for t in times) + pad
        if self.total:
            b = min(b, self.total) if self.total > a + 1.0 else b
        if not self.speech:
            return Clip(a, b)
        spans = speech_spans(times, self.total)
        if not spans or sum(y - x for x, y in spans) >= SPEECH_KEEP * (spans[-1][1] - spans[0][0]):
            return Clip(a, b)      # lời gần như liền -> khúc liền như cũ (dùng lại khúc đã cắt từ trước nếu có)
        return Clip(spans[0][0], spans[-1][1], spans)

    def clip_spans(self, spans: list[tuple[float, float]], cancel=None) -> Path:
        """Khúc CHỈ CÓ LỜI: các đoạn `spans` (giờ gốc, tròn 0,1 s) cắt + ghép thành 1 file nhẹ. Cùng các đoạn -> dùng lại
        file đã có (cả lần dịch trước). Hình: 10 khung / s chọn đúng khung trong đoạn, đánh giờ liền lại rồi lấy 1 khung
        / s; tiếng: khung 10 ms chọn đúng trong đoạn -> hình và tiếng dài bằng nhau từng đoạn."""
        a0 = spans[0][0]
        key = hashlib.md5(";".join(f"{x:.1f}-{y:.1f}" for x, y in spans).encode()).hexdigest()[:10]
        out = self.work / f"g_{self._sig}_s{key}.mp4"
        with self._lock:
            p = self._clips.get(("s", key))
            if p and p.exists():
                return p
        if not (out.exists() and out.stat().st_size > 0):
            rel = [(round(x - a0, 1), round(y - a0, 1)) for x, y in spans]
            ev = "+".join(f"gte(t\\,{x - 0.05:.2f})*lt(t\\,{y - 0.05:.2f})" for x, y in rel)
            ea = "+".join(f"gte(t\\,{x - 0.005:.3f})*lt(t\\,{y - 0.005:.3f})" for x, y in rel)
            length = sum(y - x for x, y in rel)
            tmp = out.with_suffix(".tmp.mp4")
            ff.run(["-ss", f"{a0:.3f}", "-t", f"{spans[-1][1] - a0 + 0.3:.3f}", "-i", str(self.src.resolve()),
                    "-vf", f"fps=10,select={ev},setpts=N/10/TB,fps={CLIP_FPS},scale=-2:{CLIP_H}",
                    "-c:v", "libx264", "-preset", "veryfast", "-crf", "30", "-pix_fmt", "yuv420p"] +
                   ff.af_args(ff.audio_sync(self.src, 16000), "asetnsamples=n=160:p=0", f"aselect={ea}",
                              "asetpts=N/SR/TB") +
                   ["-c:a", "aac", "-b:a", "48k", "-ac", "1", "-ar", "16000", "-t", f"{length:.3f}",
                    "-movflags", "+faststart", str(tmp)],
                   length, None, cancel, "Đang cắt đoạn có lời gửi Gemini")
            ff.replace_file(tmp, out)
        with self._lock:
            self._clips[("s", key)] = out
        return out

    def ask_clip(self, system: str, user: str, clip: Clip, max_tokens: int = 8000, cancel=None) -> str:
        """Hỏi Gemini kèm khúc `clip` (liền hoặc chỉ có lời)."""
        path = self.clip_spans(clip.spans, cancel) if clip.spans else self.clip(clip.a, clip.b, cancel)
        with self._lock:
            if path not in self._counted:
                self._counted.add(path)
                self.sent_sec += clip.length
                self.full_sec += clip.b - clip.a + (6.0 if clip.spans else 0.0)
        self.asked += 1
        return self.client.ask(system, user, path, max_tokens)

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
                    _note_ok(key)
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
                    note_key(key, why_state(why), why, "dich")
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
