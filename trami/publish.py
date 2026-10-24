"""Đăng video lên YouTube / Facebook (Trang) bằng 3 cách:

- **Zernio** (zernio.com – dịch vụ trung gian đã được YouTube / Meta duyệt): 1 API key, kết nối tài khoản trên web của
  Zernio -> video lên Công khai ngay, hẹn giờ được.
- **API riêng của bạn**: YouTube Data API (dự án Google Cloud của bạn – dự án chưa được Google duyệt thì YouTube khoá video
  ở Riêng tư) và Facebook Graph API (app Meta của bạn, chỉ đăng lên Trang bạn quản lý).
- **Chuẩn bị đăng**: ghi sẵn tiêu đề / mô tả / hashtag, mở trang tải lên của YouTube / Facebook, bạn tự kéo file vào.

AI (công cụ dịch AI trong Cài đặt) viết tiêu đề / mô tả / hashtag từ phụ đề đã dịch. Mọi lần gọi mạng: Huỷ dừng ngay, mất
mạng thì chờ có mạng rồi gửi tiếp (YouTube / Facebook gửi tiếp từ byte đã nhận, không gửi lại từ đầu).
"""
from __future__ import annotations

import base64
import hashlib
import json
import re
import secrets
import threading
import time
import uuid
import webbrowser
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Callable
from urllib.parse import parse_qs, urlencode, urlparse

from . import net
from .config import DATA_DIR
from .ffmpeg import Cancelled

Prog = Callable[[float, str], None]

ZERNIO_API = "https://zernio.com/api/v1"
GOOGLE_AUTH = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN = "https://oauth2.googleapis.com/token"
YT_API = "https://www.googleapis.com/youtube/v3"
YT_UPLOAD = "https://www.googleapis.com/upload/youtube/v3"
YT_SCOPES = "https://www.googleapis.com/auth/youtube.upload https://www.googleapis.com/auth/youtube.readonly"
FB_VER = "v23.0"
FB_GRAPH = "https://graph.facebook.com"
FB_DIALOG = "https://www.facebook.com"
FB_SCOPES = "pages_show_list,pages_read_engagement,pages_manage_posts,publish_video"
CHUNK = 8 * 1024 * 1024          # YouTube: mỗi lần gửi 8 MB (bội số của 256 KB)
LOGIN_WAIT = 300                 # chờ đăng nhập trên trình duyệt tối đa 5 phút
ZERNIO_WAIT = 20 * 60            # chờ Zernio đăng xong tối đa 20 phút (YouTube xử lý video dài lâu)

PLATFORMS = {"youtube": "YouTube", "facebook": "Facebook", "tiktok": "TikTok"}
KINDS = {"youtube": [("video", "Video thường"), ("short", "Shorts")],
         "facebook": [("video", "Video"), ("reel", "Reels")],
         "tiktok": [("video", "Video")]}
PRIVACY = [("public", "Công khai"), ("unlisted", "Không công khai"), ("private", "Riêng tư")]
YT_CATEGORIES = [("24", "Giải trí"), ("1", "Phim & hoạt hình"), ("23", "Hài kịch"), ("22", "Mọi người & blog"),
                 ("20", "Trò chơi"), ("27", "Giáo dục"), ("10", "Âm nhạc"), ("25", "Tin tức & chính trị")]
VIA_NAMES = {"zernio": "Zernio", "api": "API riêng", "browser": "Chrome điền sẵn", "manual": "Tự tải lên"}
UPLOAD_PAGES = {("youtube", "video"): "https://www.youtube.com/upload",
                ("youtube", "short"): "https://www.youtube.com/upload",
                ("facebook", "video"): "https://business.facebook.com/latest/composer",
                ("facebook", "reel"): "https://www.facebook.com/reels/create",
                ("tiktok", "video"): "https://www.tiktok.com/tiktokstudio/upload"}
REEL_MAX = 90.0                  # Reels Facebook đăng qua API: 3–90 giây, video dọc
SHORT_MAX = 180.0                # YouTube Shorts: ≤ 3 phút, dọc / vuông


class PublishError(RuntimeError):
    pass


# ------------------------------------------------------------------ tài khoản đã kết nối (data/accounts/*.json)
def accounts_dir() -> Path:
    d = DATA_DIR / "accounts"
    d.mkdir(parents=True, exist_ok=True)
    return d


def load_accounts(kind: str) -> list[dict]:
    """kind: youtube (API riêng) / facebook (API riêng – mỗi Trang 1 mục) / zernio (danh sách nhớ lần lấy gần nhất)."""
    f = accounts_dir() / f"{kind}.json"
    try:
        d = json.loads(f.read_text(encoding="utf-8"))
        return [x for x in d if isinstance(x, dict) and x.get("id")] if isinstance(d, list) else []
    except (OSError, ValueError):
        return []


def save_accounts(kind: str, items: list[dict]) -> None:
    f = accounts_dir() / f"{kind}.json"
    tmp = f.with_suffix(".tmp")
    tmp.write_text(json.dumps(items, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(f)


def _upsert(kind: str, acc: dict) -> None:
    items = [x for x in load_accounts(kind) if x.get("id") != acc["id"]]
    save_accounts(kind, items + [acc])


def remove_account(target_id: str) -> None:
    """Bỏ 1 tài khoản API riêng ("yt:<kênh>" / "fb:<trang>") khỏi danh sách (Zernio: ngắt ở web Zernio)."""
    kind, _, aid = target_id.partition(":")
    store = {"yt": "youtube", "fb": "facebook"}.get(kind)
    if store:
        save_accounts(store, [x for x in load_accounts(store) if x.get("id") != aid])


def targets(platform: str) -> list[dict]:
    """Nơi đăng của 1 nền tảng: tài khoản API riêng, tài khoản Zernio (danh sách lần lấy gần nhất), luôn có "Tự tải lên".
    Mỗi mục {"id", "via", "platform", "name", "account": {...}}."""
    out = []
    if platform == "youtube":
        out += [{"id": "yt:" + a["id"], "via": "api", "platform": platform, "name": a.get("name") or a["id"],
                 "account": a} for a in load_accounts("youtube")]
    elif platform == "facebook":
        out += [{"id": "fb:" + a["id"], "via": "api", "platform": platform, "name": a.get("name") or a["id"],
                 "account": a} for a in load_accounts("facebook")]
    out += [{"id": "zn:" + a["id"], "via": "zernio", "platform": platform, "name": a.get("name") or a["id"],
             "account": a} for a in load_accounts("zernio") if a.get("platform") == platform]
    from .browser_post import profiles

    out += [{"id": "br:" + b["id"], "via": "browser", "platform": platform, "name": b["name"], "account": b}
            for b in profiles()]
    out.append({"id": "manual:" + platform, "via": "manual", "platform": platform, "name": "Tự tải lên",
                "account": {}})
    return out


def target_label(t: dict) -> str:
    if t["via"] == "manual":
        return "Tự tải lên (mở trang đăng, tự kéo file vào)"
    if t["via"] == "browser":
        return f"Chrome · {t['name']} (điền sẵn)"
    return f"{t['name']} · {VIA_NAMES[t['via']]}"


# ------------------------------------------------------------------ gọi HTTP: Huỷ được, chờ mạng, lỗi dễ hiểu
def _err_text(r) -> str:
    """Lời báo lỗi của máy chủ (Google / Meta / Zernio) – không in token / đường dẫn có chữ ký."""
    try:
        d = r.json()
    except ValueError:
        return (r.text or "").strip()[:300] or f"HTTP {r.status_code}"
    e = d.get("error") if isinstance(d, dict) else None
    if isinstance(e, dict):
        msg = e.get("message") or e.get("error_user_msg") or ""
        errs = e.get("errors")
        if isinstance(errs, list) and errs and isinstance(errs[0], dict) and errs[0].get("reason"):
            msg = f"{msg} ({errs[0]['reason']})" if msg else errs[0]["reason"]
        return str(msg or e)[:400]
    if isinstance(d, dict):
        return str(d.get("error_description") or e or d.get("message") or d)[:400]
    return str(d)[:400]


def _http(method: str, url: str, *, what: str, ok=(200, 201), tries: int = 4, timeout=(15, 120), **kw):
    """1 lần gọi HTTP: mất mạng -> chờ có mạng rồi gọi lại (không tính là hỏng); 5xx / 429 -> thử lại (tối đa `tries`).
    Mã khác `ok` -> PublishError với lời báo của máy chủ (trừ khi ok=None: trả nguyên phản hồi)."""
    fails = 0
    while True:
        if net.cancelled():
            raise Cancelled()
        try:
            r = net.call(net.session().request, method, url, timeout=timeout, **kw)
        except Cancelled:
            raise
        except Exception as e:  # noqa: BLE001
            if not net.is_net_error(e):
                raise
            if not net.wait_online(what):
                fails += 1
                if fails >= tries:
                    raise PublishError(f"{what}: mạng lỗi ({type(e).__name__}) – thử lại sau") from e
                net.sleep(min(30, 2 ** fails))
            continue
        if ok is None or r.status_code in ok:
            return r
        if r.status_code in (429, 500, 502, 503, 504) and fails + 1 < tries:
            fails += 1
            net.sleep(min(30, 2 ** fails))
            continue
        raise PublishError(f"{what}: {_err_text(r)} (HTTP {r.status_code})")


class _Slice:
    """Đoạn [start, start+length) của file dạng file-like có độ dài -> requests gửi Content-Length (không chunked),
    đọc từng 256 KB để báo tiến độ + Huỷ giữa chừng."""

    def __init__(self, path: Path, start: int, length: int, report: Callable[[int], None] | None = None,
                 cancel=None):
        self.f = open(path, "rb")
        self.f.seek(start)
        self.left = length
        self.sent = start
        self.report = report
        self.cancel = cancel

    def __len__(self) -> int:
        return self.left

    def read(self, n: int = -1) -> bytes:
        if self.cancel and self.cancel():
            raise Cancelled()
        if self.left <= 0:
            return b""
        n = self.left if n is None or n < 0 else min(n, self.left, 256 * 1024)
        b = self.f.read(n)
        self.left -= len(b)
        self.sent += len(b)
        if self.report:
            self.report(self.sent)
        return b

    def close(self):
        self.f.close()


def _send_slice(method: str, url: str, path: Path, start: int, length: int, *, what: str, headers: dict,
                report=None, ok=(200, 201), timeout=(15, 300)):
    """Gửi 1 đoạn file 1 lần (không tự thử lại – người gọi hỏi máy chủ đã nhận tới đâu rồi gửi tiếp)."""
    cancel = net.current()[0]
    body = _Slice(path, start, length, report, cancel)
    try:
        return net.call(net.session().request, method, url, data=body, headers=headers, timeout=timeout)
    finally:
        body.close()


def _mb(n: float) -> str:
    return f"{n / 1048576:.1f} MB" if n < 1073741824 else f"{n / 1073741824:.2f} GB"


class _Rate:
    """Dòng tiến độ "37% · 120.5 MB / 330.0 MB · 2.4 MB/s"."""

    def __init__(self, progress: Prog | None, total: int, lo: float, hi: float, label: str):
        self.progress, self.total, self.lo, self.hi, self.label = progress, max(1, total), lo, hi, label
        self.t0, self.b0, self.last = time.time(), None, 0.0

    def __call__(self, sent: int):
        if not self.progress:
            return
        now = time.time()
        if self.b0 is None:
            self.b0 = sent
        if now - self.last < 0.4 and sent < self.total:
            return
        self.last = now
        rate = (sent - self.b0) / max(0.5, now - self.t0)
        frac = min(1.0, sent / self.total)
        self.progress(self.lo + (self.hi - self.lo) * frac,
                      f"{self.label} {frac * 100:.0f}% · {_mb(sent)} / {_mb(self.total)}"
                      + (f" · {_mb(rate)}/s" if rate > 0 else ""))


# ------------------------------------------------------------------ đăng nhập trên trình duyệt (OAuth, cổng trên máy)
class _Catcher:
    """Máy chủ nhỏ ở 127.0.0.1:<cổng ngẫu nhiên> nhận mã đăng nhập Google / Facebook chuyển về (redirect)."""

    def __init__(self):
        box: dict = {}
        done = threading.Event()

        class H(BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802
                q = {k: v[0] for k, v in parse_qs(urlparse(self.path).query).items()}
                if not q:            # favicon / tải lại trang
                    self.send_response(204)
                    self.end_headers()
                    return
                box.update(q)
                ok = "code" in q and "error" not in q
                msg = ("Đã đăng nhập – quay lại Subvideo để tiếp tục. Có thể đóng trang này."
                       if ok else "Đăng nhập không thành công – quay lại Subvideo để xem lý do.")
                html = (f"<html><head><meta charset='utf-8'><title>Subvideo</title></head><body style='font-family:"
                        f"sans-serif;background:#1d1b1f;color:#eee;text-align:center;padding-top:80px'>"
                        f"<h2>{'✓' if ok else '✗'} {msg}</h2></body></html>").encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(html)))
                self.end_headers()
                self.wfile.write(html)
                done.set()

            def log_message(self, *a):
                pass

        self.box, self.done = box, done
        self.server = HTTPServer(("127.0.0.1", 0), H)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True, name="trami-oauth").start()

    def wait(self, timeout: float = LOGIN_WAIT, cancel=None) -> dict:
        try:
            end = time.time() + timeout
            while not self.done.wait(0.2):
                if cancel and cancel():
                    raise Cancelled()
                if time.time() > end:
                    raise PublishError("Hết thời gian chờ đăng nhập trên trình duyệt (5 phút) – bấm đăng nhập lại.")
            return dict(self.box)
        finally:
            self.server.shutdown()
            self.server.server_close()


def _b64url(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode().rstrip("=")


# ------------------------------------------------------------------ YouTube (API riêng)
def read_client_secret(path: str) -> tuple[str, str]:
    """File client_secret_*.json tải từ Google Cloud (OAuth client kiểu "Ứng dụng dành cho máy tính") -> (id, secret)."""
    d = json.loads(Path(path).read_text(encoding="utf-8"))
    c = d.get("installed") or d.get("web") or d
    cid, sec = c.get("client_id", ""), c.get("client_secret", "")
    if not cid:
        raise ValueError("File này không có client_id – tải đúng file JSON của OAuth client (Ứng dụng dành cho máy tính).")
    return cid, sec


def yt_login(settings: dict, cancel=None, opener: Callable[[str], object] = webbrowser.open) -> dict:
    """Đăng nhập Google trên trình duyệt (OAuth cho ứng dụng máy tính + PKCE, chuyển về 127.0.0.1) -> lưu kênh."""
    cid, sec = settings.get("yt_client_id", "").strip(), settings.get("yt_client_secret", "").strip()
    if not cid:
        raise PublishError("Chưa có Client ID của Google – Cài đặt → Đăng video → YouTube (API riêng): nạp file "
                           "client_secret.json hoặc dán Client ID + Client secret.")
    verifier = _b64url(secrets.token_bytes(48))
    state = secrets.token_urlsafe(16)
    c = _Catcher()
    redirect = f"http://127.0.0.1:{c.port}/"
    opener(GOOGLE_AUTH + "?" + urlencode({
        "client_id": cid, "redirect_uri": redirect, "response_type": "code", "scope": YT_SCOPES,
        "access_type": "offline", "prompt": "consent", "state": state, "include_granted_scopes": "true",
        "code_challenge": _b64url(hashlib.sha256(verifier.encode()).digest()), "code_challenge_method": "S256"}))
    q = c.wait(cancel=cancel)
    if q.get("error"):
        raise PublishError("Google từ chối đăng nhập: " + q["error"] +
                           (" – thêm email của bạn vào 'Người dùng thử nghiệm' (Test users) của màn hình đồng ý OAuth"
                            if q["error"] == "access_denied" else ""))
    if q.get("state") != state or not q.get("code"):
        raise PublishError("Phản hồi đăng nhập không hợp lệ – bấm đăng nhập lại.")
    with net.task(cancel):
        tok = _http("POST", GOOGLE_TOKEN, what="Lấy mã truy cập Google", data={
            "code": q["code"], "client_id": cid, "client_secret": sec, "redirect_uri": redirect,
            "grant_type": "authorization_code", "code_verifier": verifier}).json()
        if not tok.get("refresh_token"):
            raise PublishError("Google không trả mã làm mới (refresh token) – gỡ quyền của app ở myaccount.google.com/"
                               "permissions rồi đăng nhập lại.")
        acc = {"access_token": tok["access_token"], "expires": time.time() + float(tok.get("expires_in", 3600)),
               "refresh_token": tok["refresh_token"], "client_id": cid}
        ch = _http("GET", YT_API + "/channels", what="Đọc thông tin kênh",
                   params={"part": "snippet", "mine": "true"},
                   headers={"Authorization": "Bearer " + acc["access_token"]}).json().get("items") or []
    if not ch:
        raise PublishError("Tài khoản Google này chưa có kênh YouTube – tạo kênh trên youtube.com rồi đăng nhập lại.")
    acc.update(id=ch[0]["id"], name=(ch[0].get("snippet") or {}).get("title") or ch[0]["id"], time=time.time())
    _upsert("youtube", acc)
    return acc


def _yt_token(settings: dict, acc: dict, force: bool = False) -> str:
    """Mã truy cập còn hạn (hết hạn / force -> làm mới bằng refresh token, lưu lại)."""
    if not force and acc.get("access_token") and acc.get("expires", 0) > time.time() + 120:
        return acc["access_token"]
    cid = acc.get("client_id") or settings.get("yt_client_id", "")
    sec = settings.get("yt_client_secret", "") if cid == settings.get("yt_client_id", "") else ""
    r = _http("POST", GOOGLE_TOKEN, what="Làm mới đăng nhập Google", ok=None, data={
        "client_id": cid, "client_secret": sec, "refresh_token": acc.get("refresh_token", ""),
        "grant_type": "refresh_token"})
    if r.status_code != 200:
        msg = _err_text(r)
        if "invalid_grant" in r.text:
            msg = ("đăng nhập đã hết hạn / bị gỡ quyền (dự án Google ở chế độ Thử nghiệm chỉ giữ đăng nhập 7 ngày – "
                   "đặt Trạng thái xuất bản = Đang hoạt động / In production)")
        raise PublishError(f"Kênh {acc.get('name')}: {msg} – vào Cài đặt → Đăng video → YouTube đăng nhập lại.")
    tok = r.json()
    acc["access_token"] = tok["access_token"]
    acc["expires"] = time.time() + float(tok.get("expires_in", 3600))
    _upsert("youtube", acc)
    return acc["access_token"]


def _clean_yt(text: str, limit: int) -> str:
    """YouTube không nhận < > trong tiêu đề / mô tả; cắt theo giới hạn."""
    t = re.sub(r"[<>]", "", text or "").strip()
    return t[:limit].rstrip()


def yt_upload(settings: dict, acc: dict, path: Path, meta: dict, progress: Prog | None = None) -> dict:
    """Tải lên kiểu nối tiếp (resumable) – rớt mạng / lỗi máy chủ thì hỏi YouTube đã nhận tới byte nào rồi gửi tiếp."""
    size = path.stat().st_size
    title = _clean_yt(meta.get("title") or path.stem, 100)
    desc = compose_text(meta, "youtube", meta.get("kind", "video"), with_title=False)
    tags, n = [], 0
    for t in meta.get("hashtags") or []:
        if n + len(t) + 1 > 450:
            break
        tags.append(t)
        n += len(t) + 1
    status = {"privacyStatus": meta.get("privacy") or "public",
              "selfDeclaredMadeForKids": bool(meta.get("made_for_kids")),
              "containsSyntheticMedia": bool(meta.get("synthetic"))}
    when = parse_when(meta.get("publish_at"))
    if when:
        status["privacyStatus"] = "private"      # YouTube: hẹn giờ = Riêng tư + publishAt, tới giờ tự Công khai
        status["publishAt"] = when.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    snippet = {"title": title, "description": _clean_yt(desc, 4900), "tags": tags,
               "categoryId": str(meta.get("category") or "24")}
    if meta.get("lang"):
        snippet["defaultLanguage"] = snippet["defaultAudioLanguage"] = meta["lang"]

    def auth(force=False):
        return {"Authorization": "Bearer " + _yt_token(settings, acc, force)}

    def start(force=False):
        return _http("POST", YT_UPLOAD + "/videos", what="Mở phiên tải lên YouTube", ok=None,
                     params={"uploadType": "resumable", "part": "snippet,status",
                             "notifySubscribers": "true" if meta.get("notify", True) else "false"},
                     headers={**auth(force), "Content-Type": "application/json; charset=UTF-8",
                              "X-Upload-Content-Length": str(size), "X-Upload-Content-Type": "video/mp4"},
                     data=json.dumps({"snippet": snippet, "status": status}))

    r = start()
    if r.status_code == 401:          # mã truy cập vừa bị thu hồi / hết hạn sớm -> làm mới 1 lần
        r = start(True)
    if r.status_code not in (200, 201):
        raise PublishError(_yt_hint(r))
    loc = r.headers.get("Location")
    if not loc:
        raise PublishError("YouTube không trả địa chỉ tải lên – thử lại.")
    rate = _Rate(progress, size, 0.05, 0.92, "Tải lên YouTube")
    off, fails, video = 0, 0, None
    while video is None:
        if net.cancelled():
            raise Cancelled()
        n = min(CHUNK, size - off)
        try:
            r = _send_slice("PUT", loc, path, off, n, what="Tải lên YouTube", report=rate,
                            headers={**auth(), "Content-Length": str(n),
                                     "Content-Range": f"bytes {off}-{off + n - 1}/{size}"})
        except Cancelled:
            raise
        except Exception as e:  # noqa: BLE001
            if not net.is_net_error(e):
                raise
            r = None
            if not net.wait_online("tải lên YouTube"):
                fails += 1
                if fails > 6:
                    raise PublishError("Tải lên YouTube: mạng lỗi nhiều lần – bấm Đăng lại sau.") from e
                net.sleep(min(30, 2 ** fails))
        if r is not None and r.status_code in (200, 201):
            video = r.json()
            break
        if r is not None and r.status_code == 308:
            rng = r.headers.get("Range", "")
            off = int(rng.rsplit("-", 1)[1]) + 1 if "-" in rng else 0
            continue
        if r is not None and r.status_code == 401:
            auth(True)
        elif r is not None and r.status_code not in (500, 502, 503, 504):
            raise PublishError(_yt_hint(r))
        # hỏi YouTube đã nhận tới đâu rồi gửi tiếp
        q = _http("PUT", loc, what="Hỏi YouTube đã nhận tới đâu", ok=None,
                  headers={**auth(), "Content-Length": "0", "Content-Range": f"bytes */{size}"})
        if q.status_code in (200, 201):
            video = q.json()
            break
        if q.status_code == 404:
            raise PublishError("Phiên tải lên YouTube đã hết hạn – bấm Đăng lại.")
        rng = q.headers.get("Range", "")
        off = int(rng.rsplit("-", 1)[1]) + 1 if "-" in rng else 0
    vid = video.get("id", "")
    warn = []
    want = status["privacyStatus"]
    got = (video.get("status") or {}).get("privacyStatus", want)
    if got != want:
        warn.append(f"YouTube để video ở chế độ '{got}' (bạn chọn '{want}') – dự án Google Cloud chưa được Google "
                    "duyệt thì video tải qua API bị khoá Riêng tư; dùng Zernio để đăng công khai ngay")
    thumb = meta.get("thumbnail")
    if thumb and Path(thumb).exists() and vid:
        if progress:
            progress(0.95, "Đặt ảnh bìa YouTube…")
        data = Path(thumb).read_bytes()
        r = _http("POST", YT_UPLOAD + "/thumbnails/set", what="Đặt ảnh bìa YouTube", ok=None,
                  params={"videoId": vid, "uploadType": "media"},
                  headers={**auth(), "Content-Type": "image/png" if thumb.lower().endswith(".png") else "image/jpeg"},
                  data=data)
        if r.status_code not in (200, 201):
            warn.append("chưa đặt được ảnh bìa: " + _err_text(r) +
                        (" (kênh cần xác minh số điện thoại ở youtube.com/verify)" if r.status_code == 403 else ""))
    short = meta.get("kind") == "short"
    return {"remote_id": vid, "url": (f"https://www.youtube.com/shorts/{vid}" if short else f"https://youtu.be/{vid}"),
            "status": "scheduled" if when else "published", "warn": "; ".join(warn)}


def _yt_hint(r) -> str:
    t = _err_text(r)
    low = r.text.lower()
    if "quotaexceeded" in low or "dailylimit" in low:
        return ("YouTube: hết hạn mức tải lên hôm nay của dự án Google Cloud (mặc định chỉ vài video / ngày) – "
                "thử lại ngày mai hoặc đăng qua Zernio.")
    if "uploadlimitexceeded" in low:
        return "YouTube: kênh đã tải lên quá nhiều video hôm nay – thử lại sau 24 giờ."
    if r.status_code == 403 and ("insufficient" in low or "scope" in low):
        return "YouTube: chưa đủ quyền tải lên – Cài đặt → Đăng video → YouTube, bấm đăng nhập lại."
    return f"YouTube: {t} (HTTP {r.status_code})"


# ------------------------------------------------------------------ Facebook (API riêng – chỉ Trang)
def _fb(path: str) -> str:
    return f"{FB_GRAPH}/{FB_VER}/{path.lstrip('/')}"


def fb_login(settings: dict, cancel=None, opener: Callable[[str], object] = webbrowser.open) -> list[dict]:
    """Đăng nhập Facebook trên trình duyệt (app Meta của bạn, chuyển về http://localhost – Meta tự cho phép khi app
    đang ở chế độ Phát triển) -> lưu các Trang bạn quản lý."""
    app, sec = settings.get("fb_app_id", "").strip(), settings.get("fb_app_secret", "").strip()
    if not app or not sec:
        raise PublishError("Chưa có App ID / App Secret của app Meta – Cài đặt → Đăng video → Facebook (API riêng).")
    state = secrets.token_urlsafe(16)
    c = _Catcher()
    redirect = f"http://localhost:{c.port}/"
    opener(f"{FB_DIALOG}/{FB_VER}/dialog/oauth?" + urlencode({
        "client_id": app, "redirect_uri": redirect, "state": state, "scope": FB_SCOPES, "response_type": "code"}))
    q = c.wait(cancel=cancel)
    if q.get("error"):
        raise PublishError("Facebook từ chối đăng nhập: " + (q.get("error_description") or q.get("error_reason")
                                                             or q["error"]))
    if q.get("state") != state or not q.get("code"):
        raise PublishError("Phản hồi đăng nhập không hợp lệ – bấm đăng nhập lại.")
    with net.task(cancel):
        tok = _http("GET", _fb("oauth/access_token"), what="Lấy mã truy cập Facebook", params={
            "client_id": app, "client_secret": sec, "redirect_uri": redirect, "code": q["code"]}).json()
        return fb_use_token(settings, tok["access_token"])


def fb_use_token(settings: dict, user_token: str) -> list[dict]:
    """Mã truy cập người dùng (đăng nhập hoặc dán từ Graph API Explorer) -> đổi sang mã dài hạn (60 ngày, cần App
    Secret) -> mã của từng Trang (không hết hạn) -> lưu."""
    user_token = user_token.strip()
    if not user_token:
        raise PublishError("Chưa có mã truy cập.")
    app, sec = settings.get("fb_app_id", "").strip(), settings.get("fb_app_secret", "").strip()
    if not app:
        app = str(_http("GET", _fb("app"), what="Đọc app của mã truy cập",
                        params={"access_token": user_token}).json().get("id", ""))
    if app and sec:
        r = _http("GET", _fb("oauth/access_token"), what="Đổi mã dài hạn", ok=None, params={
            "grant_type": "fb_exchange_token", "client_id": app, "client_secret": sec, "fb_exchange_token": user_token})
        if r.status_code == 200 and r.json().get("access_token"):
            user_token = r.json()["access_token"]
    me = _http("GET", _fb("me"), what="Đọc tài khoản Facebook",
               params={"fields": "id,name", "access_token": user_token}).json()
    pages, url, params = [], _fb("me/accounts"), {"fields": "id,name,access_token,tasks", "limit": 100,
                                                  "access_token": user_token}
    while url:
        d = _http("GET", url, what="Đọc danh sách Trang", params=params).json()
        pages += d.get("data") or []
        url, params = (d.get("paging") or {}).get("next"), None
    pages = [x for x in pages if x.get("access_token")]
    if not pages:
        raise PublishError("Tài khoản này không quản lý Trang nào (hoặc chưa cấp quyền Trang khi đăng nhập). Facebook chỉ "
                           "cho đăng lên Trang (Fanpage), không đăng lên trang cá nhân.")
    keep = [x for x in load_accounts("facebook") if x.get("user_id") != me.get("id")]
    new = [{"id": x["id"], "name": x.get("name") or x["id"], "token": x["access_token"], "user_id": me.get("id"),
            "user": me.get("name", ""), "user_token": user_token, "app_id": app, "time": time.time()} for x in pages]
    save_accounts("facebook", keep + new)
    return new


def fb_upload(settings: dict, acc: dict, path: Path, meta: dict, progress: Prog | None = None) -> dict:
    if meta.get("kind") == "reel":
        return _fb_reel(acc, path, meta, progress)
    return _fb_video(settings, acc, path, meta, progress)


def _fb_video(settings: dict, acc: dict, path: Path, meta: dict, progress: Prog | None) -> dict:
    """Video thường lên Trang: phiên tải lên nối tiếp (Resumable Upload API, mã người dùng) -> đăng lên Trang bằng
    mã của Trang (fbuploader_video_file_chunk)."""
    size = path.stat().st_size
    utok = acc.get("user_token", "")
    app = acc.get("app_id") or settings.get("fb_app_id", "")
    if not utok or not app:
        raise PublishError(f"Trang {acc.get('name')}: thiếu mã người dùng / App ID – Cài đặt → Đăng video → Facebook, "
                           "đăng nhập lại.")
    sess = _http("POST", _fb(f"{app}/uploads"), what="Mở phiên tải lên Facebook", params={
        "file_name": path.name, "file_length": size, "file_type": "video/mp4", "access_token": utok}).json()["id"]
    rate = _Rate(progress, size, 0.05, 0.9, "Tải lên Facebook")
    off, fails, handle = 0, 0, ""
    while not handle:
        if net.cancelled():
            raise Cancelled()
        try:
            r = _send_slice("POST", _fb(sess), path, off, size - off, what="Tải lên Facebook", report=rate,
                            headers={"Authorization": "OAuth " + utok, "file_offset": str(off)})
        except Cancelled:
            raise
        except Exception as e:  # noqa: BLE001
            if not net.is_net_error(e):
                raise
            r = None
            if not net.wait_online("tải lên Facebook"):
                fails += 1
                if fails > 6:
                    raise PublishError("Tải lên Facebook: mạng lỗi nhiều lần – bấm Đăng lại sau.") from e
                net.sleep(min(30, 2 ** fails))
        if r is not None and r.status_code == 200:
            try:
                handle = str(r.json().get("h") or "")
            except ValueError:
                handle = ""
            if handle:
                break
            raise PublishError("Facebook không trả mã file vừa tải lên: " + (r.text or "")[:200])
        if r is not None and r.status_code not in (500, 502, 503, 504):
            raise PublishError("Facebook: " + _err_text(r))
        q = _http("GET", _fb(sess), what="Hỏi Facebook đã nhận tới đâu", headers={"Authorization": "OAuth " + utok})
        off = int(q.json().get("file_offset") or 0)
    if progress:
        progress(0.93, "Đăng video lên Trang…")
    data = {"access_token": acc["token"], "title": (meta.get("title") or "")[:250],
            "description": compose_text(meta, "facebook", "video", with_title=False),
            "fbuploader_video_file_chunk": handle}
    when = parse_when(meta.get("publish_at"))
    if when:
        data.update(published="false", scheduled_publish_time=str(int(when.timestamp())))
    files = None
    thumb = meta.get("thumbnail")
    if thumb and Path(thumb).exists():
        files = {"thumb": (Path(thumb).name, Path(thumb).read_bytes(),
                           "image/png" if thumb.lower().endswith(".png") else "image/jpeg")}
    d = _http("POST", _fb(f"{acc['id']}/videos"), what="Đăng video lên Trang", data=data, files=files).json()
    vid = str(d.get("id") or d.get("video_id") or "")
    return {"remote_id": vid, "url": f"https://www.facebook.com/{acc['id']}/videos/{vid}" if vid else "",
            "status": "scheduled" if when else "published", "warn": ""}


def _fb_reel(acc: dict, path: Path, meta: dict, progress: Prog | None) -> dict:
    """Reels lên Trang: start -> gửi file tới rupload.facebook.com (nối tiếp theo offset) -> finish (PUBLISHED /
    SCHEDULED)."""
    size = path.stat().st_size
    ptok, page = acc["token"], acc["id"]
    st = _http("POST", _fb(f"{page}/video_reels"), what="Mở phiên Reels",
               params={"upload_phase": "start", "access_token": ptok}).json()
    vid, url = str(st.get("video_id", "")), st.get("upload_url") or ""
    if not vid or not url:
        raise PublishError("Facebook không trả địa chỉ tải lên Reels – thử lại.")
    rate = _Rate(progress, size, 0.05, 0.9, "Tải lên Reels")
    off, fails = 0, 0
    while True:
        if net.cancelled():
            raise Cancelled()
        try:
            r = _send_slice("POST", url, path, off, size - off, what="Tải lên Reels", report=rate,
                            headers={"Authorization": "OAuth " + ptok, "offset": str(off), "file_size": str(size)})
        except Cancelled:
            raise
        except Exception as e:  # noqa: BLE001
            if not net.is_net_error(e):
                raise
            r = None
            if not net.wait_online("tải lên Reels"):
                fails += 1
                if fails > 6:
                    raise PublishError("Tải lên Reels: mạng lỗi nhiều lần – bấm Đăng lại sau.") from e
                net.sleep(min(30, 2 ** fails))
        if r is not None and r.status_code == 200:
            break
        if r is not None and r.status_code not in (500, 502, 503, 504):
            raise PublishError("Facebook Reels: " + _err_text(r))
        q = _http("GET", _fb(vid), what="Hỏi Facebook đã nhận tới đâu",
                  params={"fields": "status", "access_token": ptok}).json()
        off = int((((q.get("status") or {}).get("uploading_phase") or {}).get("bytes_transferred")) or 0)
        if off >= size:
            break
    if progress:
        progress(0.93, "Đăng Reels…")
    params = {"upload_phase": "finish", "video_id": vid, "access_token": ptok,
              "description": compose_text(meta, "facebook", "reel", with_title=False)}
    if meta.get("title"):
        params["title"] = meta["title"][:250]
    when = parse_when(meta.get("publish_at"))
    if when:
        params.update(video_state="SCHEDULED", scheduled_publish_time=str(int(when.timestamp())))
    else:
        params["video_state"] = "PUBLISHED"
    d = _http("POST", _fb(f"{page}/video_reels"), what="Đăng Reels", params=params).json()
    if d.get("success") is False:
        raise PublishError("Facebook không nhận Reels: " + json.dumps(d, ensure_ascii=False)[:300])
    return {"remote_id": vid, "url": f"https://www.facebook.com/reel/{vid}",
            "status": "scheduled" if when else "processing", "warn": ""}


# ------------------------------------------------------------------ Zernio
class Zernio:
    def __init__(self, key: str, base: str | None = None):
        self.key = (key or "").strip()
        if not self.key:
            raise PublishError("Chưa có API key Zernio – Cài đặt → Đăng video → Zernio.")
        self.base = (base or ZERNIO_API).rstrip("/")

    def call(self, method: str, path: str, body: dict | None = None, query: dict | None = None,
             headers: dict | None = None, what: str = "Zernio"):
        h = {"Authorization": "Bearer " + self.key, "Accept": "application/json", **(headers or {})}
        r = _http(method, f"{self.base}/{path.lstrip('/')}", what=what, ok=None, params=query,
                  json=body, headers=h)
        if r.status_code == 401:
            raise PublishError("Zernio: API key sai hoặc đã bị thu hồi – kiểm tra lại ở Cài đặt → Đăng video.")
        if r.status_code >= 400:
            raise PublishError(f"{what}: {_err_text(r)} (HTTP {r.status_code})")
        try:
            d = r.json() if r.content else {}
        except ValueError as e:
            raise PublishError(f"{what}: Zernio trả dữ liệu không đọc được") from e
        if isinstance(d, dict) and isinstance(d.get("data"), (dict, list)):
            return d["data"]
        return d

    @staticmethod
    def _list(d, key: str) -> list:
        if isinstance(d, list):
            return d
        v = d.get(key) if isinstance(d, dict) else None
        return v if isinstance(v, list) else []

    def profile_id(self, settings: dict) -> str:
        """Hồ sơ Zernio để gắn tài khoản (dùng hồ sơ đầu tiên, chưa có thì tạo "Subvideo")."""
        pid = settings.get("zernio_profile_id", "")
        if pid:
            return pid
        items = self._list(self.call("GET", "/profiles", what="Đọc hồ sơ Zernio"), "profiles")
        if items:
            pid = str(items[0].get("_id") or items[0].get("id") or "")
        if not pid:
            d = self.call("POST", "/profiles", {"name": "Subvideo", "description": "Đăng video từ Subvideo"},
                          what="Tạo hồ sơ Zernio")
            d = d.get("profile", d) if isinstance(d, dict) else {}
            pid = str(d.get("_id") or d.get("id") or "")
        if not pid:
            raise PublishError("Zernio không trả hồ sơ (profile) – thử lại.")
        from .config import save_settings

        save_settings({"zernio_profile_id": pid})
        return pid

    def connect_url(self, settings: dict, platform: str) -> str:
        d = self.call("GET", f"/connect/{platform}", query={"profileId": self.profile_id(settings)},
                      what="Lấy trang kết nối tài khoản")
        url = d.get("authUrl") or d.get("url") or d.get("connectUrl") if isinstance(d, dict) else ""
        if not url:
            raise PublishError("Zernio không trả trang kết nối tài khoản.")
        return url

    def accounts(self) -> list[dict]:
        """Tài khoản YouTube / Facebook đã kết nối -> lưu danh sách (để chọn khi đăng mà không phải hỏi mạng)."""
        d = self.call("GET", "/accounts", query={"status": "connected", "includeOverLimit": "true"},
                      what="Đọc tài khoản Zernio")
        out = []
        for a in self._list(d, "accounts"):
            plat = str(a.get("platform") or "").lower()
            if plat not in ("youtube", "facebook"):
                continue
            aid = str(a.get("_id") or a.get("id") or "")
            name = a.get("displayName") or a.get("name") or a.get("username") or aid
            if aid:
                out.append({"id": aid, "platform": plat, "name": str(name)})
        save_accounts("zernio", out)
        return out

    def upload(self, path: Path, progress: Prog | None = None, lo: float = 0.05, hi: float = 0.8,
               content_type: str = "video/mp4") -> str:
        """Gửi file lên kho của Zernio (địa chỉ ký sẵn) -> địa chỉ công khai để gắn vào bài đăng."""
        size = path.stat().st_size
        pre = self.call("POST", "/media/presign", {"filename": path.name, "contentType": content_type, "size": size},
                        what="Xin chỗ tải lên Zernio")
        up, pub = pre.get("uploadUrl") or "", pre.get("publicUrl") or ""
        if not up or not pub:
            raise PublishError("Zernio không trả địa chỉ tải lên.")
        rate = _Rate(progress, size, lo, hi, "Tải lên Zernio")
        for i in range(3):
            try:
                r = _send_slice("PUT", up, path, 0, size, what="Tải lên Zernio", report=rate,
                                headers={"Content-Type": content_type, "Content-Length": str(size)})
            except Cancelled:
                raise
            except Exception as e:  # noqa: BLE001
                if not net.is_net_error(e) or i == 2:
                    raise PublishError(f"Tải lên Zernio lỗi: {type(e).__name__}") from e
                net.wait_online("tải lên Zernio")
                continue
            if 200 <= r.status_code < 300:
                return pub
            if r.status_code < 500 or i == 2:
                raise PublishError(f"Tải lên Zernio lỗi (HTTP {r.status_code}).")
        raise PublishError("Tải lên Zernio lỗi.")

    def create_post(self, platform: str, account_id: str, meta: dict, media_url: str, thumb_url: str = "") -> dict:
        kind = meta.get("kind", "video")
        if platform == "youtube":
            psd = {"title": _clean_yt(meta.get("title") or "Video", 100), "visibility": meta.get("privacy") or "public",
                   "madeForKids": bool(meta.get("made_for_kids")), "containsSyntheticMedia": bool(meta.get("synthetic")),
                   "notifySubscribers": bool(meta.get("notify", True)), "categoryId": str(meta.get("category") or "24")}
            content = compose_text(meta, platform, kind, with_title=False)
        else:
            psd = {"contentType": "reel", "title": (meta.get("title") or "")[:255]} if kind == "reel" else {}
            content = compose_text(meta, platform, kind, with_title=kind != "reel")
        item = {"type": "video", "url": media_url}
        if thumb_url:
            item["thumbnail"] = thumb_url
        body = {"content": content, "mediaItems": [item],
                "platforms": [{"platform": platform, "accountId": account_id, "platformSpecificData": psd}]}
        when = parse_when(meta.get("publish_at"))
        if when:
            body["scheduledFor"] = when.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            body["timezone"] = "UTC"
        else:
            body["publishNow"] = True
        d = self.call("POST", "/posts", body, headers={"x-request-id": meta.get("request_id") or uuid.uuid4().hex},
                      what="Tạo bài đăng Zernio")
        return d.get("post", d) if isinstance(d, dict) else {}

    def get_post(self, post_id: str) -> dict:
        d = self.call("GET", f"/posts/{post_id}", what="Đọc trạng thái bài đăng")
        return d.get("post", d) if isinstance(d, dict) else {}


def zernio_result(post: dict, platform: str) -> dict:
    """Trạng thái / link / lỗi của 1 nền tảng trong bài đăng Zernio (các kiểu gói dữ liệu khác nhau)."""
    entry = {}
    for key in ("platforms", "platformResults", "results"):
        v = post.get(key)
        if isinstance(v, dict):
            v = [dict(x, platform=k) if isinstance(x, dict) else {} for k, x in v.items()]
        for x in v or []:
            if isinstance(x, dict) and str(x.get("platform") or "").lower() == platform:
                entry = x
                break
        if entry:
            break
    status = str(entry.get("status") or post.get("status") or "publishing").lower()
    url = ""
    for src in (entry, post):
        for k in ("platformPostUrl", "publishedUrl", "permalink", "postUrl", "url"):
            v = src.get(k)
            if isinstance(v, str) and v.startswith("http") and "zernio" not in v:
                url = v
                break
        if url:
            break
    err = entry.get("error") or entry.get("errorMessage") or post.get("error") or ""
    if isinstance(err, dict):
        err = err.get("message") or err.get("code") or ""
    return {"status": status, "url": url, "error": str(err or "")}


ZN_DONE = {"published", "posted", "success", "completed"}
ZN_FAIL = {"failed", "error", "partial", "cancelled", "deleted"}


def _zernio_publish(settings: dict, target: dict, path: Path, meta: dict, progress: Prog | None) -> dict:
    zn = Zernio(settings.get("zernio_api_key", ""))
    platform = target["platform"]
    media = zn.upload(path, progress, 0.03, 0.82)
    thumb_url = ""
    thumb = meta.get("thumbnail")
    if thumb and Path(thumb).exists():
        try:
            thumb_url = zn.upload(Path(thumb), None, content_type="image/png" if thumb.lower().endswith(".png")
                                  else "image/jpeg")
        except PublishError:
            thumb_url = ""
    if progress:
        progress(0.85, f"Zernio đang đăng lên {PLATFORMS[platform]}…")
    post = zn.create_post(platform, target["account"]["id"], meta, media, thumb_url)
    pid = str(post.get("_id") or post.get("id") or "")
    res = zernio_result(post, platform)
    scheduled = bool(parse_when(meta.get("publish_at")))
    t0 = time.time()
    while pid and not scheduled and res["status"] not in ZN_DONE | ZN_FAIL and time.time() - t0 < ZERNIO_WAIT:
        net.sleep(6)
        try:
            res = zernio_result(zn.get_post(pid), platform)
        except PublishError:
            continue
        if progress:
            w = int(time.time() - t0)
            progress(0.85 + 0.13 * min(1.0, w / 300), f"{PLATFORMS[platform]} đang xử lý video… ({w // 60}:{w % 60:02d})")
    if res["status"] in ZN_FAIL:
        raise PublishError(f"{PLATFORMS[platform]} báo lỗi khi đăng qua Zernio: {res['error'] or res['status']}")
    status = "scheduled" if scheduled else ("published" if res["status"] in ZN_DONE else "processing")
    return {"remote_id": pid, "post_id": pid, "url": res["url"], "status": status, "warn": ""}


def refresh_status(settings: dict, rec: dict) -> dict:
    """Bài đăng qua Zernio còn "đang xử lý" -> hỏi lại trạng thái + link."""
    if rec.get("via") != "zernio" or not rec.get("post_id"):
        return rec
    res = zernio_result(Zernio(settings.get("zernio_api_key", "")).get_post(rec["post_id"]), rec["platform"])
    if res["status"] in ZN_DONE:
        rec["status"] = "published"
    elif res["status"] in ZN_FAIL:
        rec["status"], rec["warn"] = "failed", res["error"] or res["status"]
    if res["url"]:
        rec["url"] = res["url"]
    return rec


# ------------------------------------------------------------------ nội dung bài đăng
def norm_tags(text) -> list[str]:
    """'#phim hay, #hoathinh  vietsub' / list -> ['phimhay'?..]: tách theo dấu phẩy / khoảng trắng / '#', bỏ trùng."""
    if isinstance(text, (list, tuple)):
        raw = [str(x) for x in text]
    else:
        raw = re.split(r"[,\s]+", str(text or ""))
    out, seen = [], set()
    for t in raw:
        for part in t.split("#"):
            part = re.sub(r"[^\w]", "", part.strip(), flags=re.UNICODE)
            if part and part.lower() not in seen:
                seen.add(part.lower())
                out.append(part)
    return out[:30]


def compose_text(meta: dict, platform: str, kind: str, with_title: bool = False) -> str:
    """Nội dung bài đăng = (tiêu đề) + mô tả + hashtag; Shorts tự thêm #Shorts."""
    tags = norm_tags(meta.get("hashtags") or [])
    if platform == "youtube" and kind == "short" and not any(t.lower() == "shorts" for t in tags):
        tags = ["Shorts"] + tags
    parts = []
    if with_title and meta.get("title"):
        parts.append(meta["title"].strip())
    if (meta.get("description") or "").strip():
        parts.append(meta["description"].strip())
    if tags:
        parts.append(" ".join("#" + t for t in tags[:15]))
    return "\n\n".join(parts)


def parse_when(v) -> datetime | None:
    """Giờ hẹn đăng (ISO, có / không múi giờ – không có thì hiểu là giờ máy) -> datetime có múi giờ; quá khứ -> None."""
    if not v:
        return None
    if isinstance(v, datetime):
        dt = v
    else:
        try:
            dt = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.astimezone()
    return dt if dt.timestamp() > time.time() + 60 else None


def check_video(path: Path, platform: str, kind: str) -> tuple[str, str]:
    """(lỗi chặn đăng, cảnh báo) theo yêu cầu của nền tảng."""
    from .ffmpeg import probe

    if not path.exists():
        return f"Không tìm thấy file video: {path}", ""
    try:
        info = probe(path)
    except Exception:  # noqa: BLE001
        return "", ""
    d, w, h = info.duration, info.width, info.height
    if platform == "facebook" and kind == "reel":
        if d and (d < 3 or d > REEL_MAX):
            return (f"Reels Facebook chỉ nhận video 3–{REEL_MAX:.0f} giây (video này {d:.0f} giây) – chọn kiểu 'Video' "
                    "hoặc cắt ngắn (tab Xuất → Chia thành nhiều phần)."), ""
        if w and h and w >= h:
            return "", "Reels cần video dọc 9:16 – video này nằm ngang, Facebook có thể từ chối."
    if platform == "tiktok" and d > 600:
        return "", "TikTok trên máy tính nhận video tới 60 phút (một số tài khoản chỉ 10 phút)."
    if platform == "youtube" and kind == "short":
        if d > SHORT_MAX:
            return "", f"Shorts chỉ ≤ 3 phút – video {d / 60:.1f} phút sẽ thành video thường."
        if w and h and w > h:
            return "", "Shorts cần video dọc hoặc vuông – video nằm ngang sẽ thành video thường."
    return "", ""


def _videos(p) -> list[dict]:
    return [o for o in p.data.get("outputs", []) if o.get("kind") == "video" and Path(o.get("path", "")).exists()]


def latest_video(p) -> Path | None:
    """Video xuất gần nhất của dự án (còn trên đĩa) – bản chính (tỉ lệ chính) của lần xuất đó."""
    outs = _videos(p)
    if not outs:
        return None
    last = outs[-1]
    if last.get("batch"):
        last = next(o for o in outs if o.get("batch") == last["batch"])
    return Path(last["path"])


PORTRAIT_KINDS = {("youtube", "short"), ("facebook", "reel"), ("tiktok", "video")}


def _is_portrait(o: dict) -> bool:
    w, h = o.get("w"), o.get("h")
    if w and h:
        return h > w
    from .video import aspect_wh

    wh = aspect_wh(o.get("ratio"))
    return bool(wh) and wh[0] < wh[1]


def video_for(p, platform: str, kind: str, chosen: Path | None) -> Path | None:
    """Bản xuất hợp nền tảng trong CÙNG lần xuất với file đang chọn: Shorts / Reels / TikTok -> bản dọc (ưu tiên 9:16),
    YouTube / Facebook video -> bản ngang (ưu tiên 16:9). Không có bản hợp hơn -> giữ file đang chọn."""
    if not chosen:
        return chosen
    outs = _videos(p)
    me = next((o for o in outs if Path(o["path"]) == Path(chosen)), None)
    if not me or not me.get("batch"):
        return chosen
    same = [o for o in outs if o.get("batch") == me["batch"]]
    if len(same) < 2:
        return chosen
    want = (platform, kind) in PORTRAIT_KINDS
    if _is_portrait(me) == want:
        return chosen
    cand = [o for o in same if _is_portrait(o) == want]
    if not cand:
        return chosen
    pref = "9:16" if want else "16:9"
    return Path(next((o for o in cand if o.get("ratio") == pref), cand[0])["path"])


def batch_ratios(p, chosen: Path | None) -> list[str]:
    """Các tỉ lệ của lần xuất chứa file đang chọn (để báo "kèm bản 9:16 cho Shorts / Reels")."""
    if not chosen:
        return []
    outs = _videos(p)
    me = next((o for o in outs if Path(o["path"]) == Path(chosen)), None)
    if not me or not me.get("batch"):
        return []
    return [o.get("ratio") or "original" for o in outs if o.get("batch") == me["batch"] and o is not me]


# ------------------------------------------------------------------ AI viết tiêu đề / mô tả / hashtag
META_RULES = {
    ("youtube", "video"): (90, "3–5 câu tóm tắt hấp dẫn (không kể hết kết thúc), 1 câu mời đăng ký kênh", 8),
    ("youtube", "short"): (70, "1–2 câu ngắn gây tò mò", 5),
    ("facebook", "video"): (80, "2–3 câu cuốn hút + 1 câu hỏi gợi người xem bình luận", 5),
    ("facebook", "reel"): (60, "1–2 câu ngắn gây tò mò", 5),
    ("tiktok", "video"): (60, "1–2 câu ngắn gây tò mò, giọng trẻ trung", 5),
}

META_PROMPT = """Bạn viết nội dung đăng video cho {where}. Video dài {dur}, đã dịch và lồng tiếng {lang_name}.
Đọc phụ đề (và hồ sơ phim nếu có) rồi viết bằng {lang_name}:
- "title": tiêu đề hấp dẫn, ĐÚNG nội dung video, tối đa {tmax} ký tự; không bịa chi tiết không có trong video; không viết
  hoa toàn bộ; không dùng ký tự < >; không thêm hashtag vào tiêu đề.
- "description": {desc}.
- "hashtags": {ntag} hashtag liên quan (viết liền không dấu cách, không có dấu #), ưu tiên hashtag phổ biến của thể loại
  và tên phim / nhân vật nếu có.
Chỉ trả về JSON: {{"title": "...", "description": "...", "hashtags": ["...", "..."]}}"""


def pick_ai(settings: dict) -> str:
    """AI để viết nội dung: công cụ dịch đang chọn nếu là AI có key, không thì AI đầu tiên có key trong Cài đặt."""
    from .translate import AI_ENGINES, api_keys

    cur = settings.get("translate_engine", "")
    order = [cur] + [e for e in ("gemini", "deepseek", "anthropic", "openai") if e != cur]
    for e in order:
        if e in AI_ENGINES and (e == "ollama" and e == cur or e != "ollama" and api_keys(settings, e)):
            return e
    raise PublishError("AI viết nội dung cần 1 công cụ dịch AI có API key (Gemini / DeepSeek / Claude / OpenAI) – "
                       "thêm key ở Cài đặt → Công cụ dịch.")


def ai_metadata(p, settings: dict, platform: str, kind: str, engine: str | None = None) -> dict:
    """AI đọc phụ đề đã dịch (rải đều cả phim, ≤ ~7000 chữ) + hồ sơ phim -> {"title", "description", "hashtags"}."""
    from . import langs, pipeline
    from .translate import LLMClient, _extract_json

    eng = engine or pick_ai(settings)
    segs = [s for s in p.segments if (s.trans or s.text).strip() and not s.extra.get("song")]
    if not segs:
        raise PublishError("Dự án chưa có phụ đề để AI đọc.")
    step = max(1, len(segs) // 160)
    lines, n = [], 0
    for s in segs[::step]:
        t = (s.trans or s.text).strip().replace("\n", " ")
        if n + len(t) > 7000:
            break
        lines.append(t)
        n += len(t)
    target = pipeline.target_lang(p, settings)
    lang_name = langs.INFO[target]["name"].lower()
    tmax, desc, ntag = META_RULES.get((platform, kind), META_RULES[("youtube", "video")])
    dur = p.data.get("info", {}).get("duration") or (segs[-1].end if segs else 0)
    where = {"youtube": "YouTube Shorts" if kind == "short" else "YouTube",
             "facebook": "Facebook Reels" if kind == "reel" else "Facebook (Trang)", "tiktok": "TikTok"}[platform]
    system = META_PROMPT.format(where=where, dur=f"{int(dur) // 60} phút {int(dur) % 60} giây", lang_name=lang_name,
                                tmax=tmax, desc=desc, ntag=ntag)
    user = (f"TÊN DỰ ÁN: {p.data.get('name', '')}\n"
            + (f"HỒ SƠ PHIM:\n{p.data['film_profile'][:2500]}\n" if p.data.get("film_profile", "").strip() else "")
            + "PHỤ ĐỀ:\n" + "\n".join(lines))
    d = _extract_json(LLMClient(settings, eng).chat(system, user, max_tokens=2000, temperature=0.7))
    if not isinstance(d, dict) or not d.get("title"):
        raise PublishError("AI trả kết quả không đọc được – bấm AI viết lần nữa.")
    return {"title": str(d.get("title", "")).strip().strip('"')[:tmax + 20],
            "description": str(d.get("description", "")).strip(),
            "hashtags": norm_tags(d.get("hashtags") or [])[:ntag + 3]}


# ------------------------------------------------------------------ chuẩn bị đăng (tự tải lên)
def prepare_manual(path: Path, meta: dict, platform: str, kind: str) -> tuple[str, Path, str]:
    """Ghi <video>.<nền tảng>.txt (tiêu đề / mô tả / hashtag) cạnh video -> (nội dung để chép, file txt, trang tải lên)."""
    body = compose_text(meta, platform, kind, with_title=False)
    title = (meta.get("title") or "").strip()
    text = (f"TIÊU ĐỀ:\n{title}\n\nMÔ TẢ:\n{body}\n" if title else body + "\n")
    txt = path.with_name(f"{path.stem}.{platform}{'_' + kind if kind != 'video' else ''}.txt")
    txt.write_text(text, encoding="utf-8")
    return (title + "\n\n" + body if title else body), txt, UPLOAD_PAGES[(platform, kind)]


# ------------------------------------------------------------------ lịch sử đăng (posts.json riêng – không đụng project.json)
def posts_file(p) -> Path:
    return p.dir / "posts.json"


def load_posts(p) -> list[dict]:
    try:
        d = json.loads(posts_file(p).read_text(encoding="utf-8"))
        return d if isinstance(d, list) else []
    except (OSError, ValueError):
        return []


_posts_lock = threading.Lock()


def save_post(p, rec: dict) -> None:
    with _posts_lock:
        items = [x for x in load_posts(p) if x.get("id") != rec.get("id")]
        items.append(rec)
        f = posts_file(p)
        tmp = f.with_suffix(".tmp")
        tmp.write_text(json.dumps(items[-200:], ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(f)


def load_draft(p) -> dict:
    """Bản nháp bài đăng của dự án (tiêu đề / mô tả / hashtag / nơi đăng…) – file riêng, không đụng project.json."""
    try:
        d = json.loads((p.dir / "publish_draft.json").read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def save_draft(p, d: dict) -> None:
    f = p.dir / "publish_draft.json"
    try:
        f.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
    except OSError:
        pass


STATUS_NAMES = {"published": "✓ Đã đăng", "scheduled": "⏰ Đã hẹn giờ", "processing": "… Đang xử lý",
                "prepared": "📋 Đã chuẩn bị", "browser": "🌐 Điền sẵn trên Chrome", "failed": "✗ Lỗi"}


# ------------------------------------------------------------------ đăng (chạy trong việc nền "publish")
def publish(p, settings: dict, item: dict, progress: Prog | None = None) -> dict:
    """item: platform, kind, target (mục của `targets`), file, title, description, hashtags, privacy, publish_at,
    thumbnail, made_for_kids, synthetic, notify, category. Trả bản ghi đã lưu vào posts.json."""
    platform, kind, target = item["platform"], item.get("kind", "video"), item["target"]
    path = Path(item["file"])
    err, warn = check_video(path, platform, kind)
    if err:
        raise PublishError(err)
    meta = dict(item, hashtags=norm_tags(item.get("hashtags")), request_id=uuid.uuid4().hex)
    if progress:
        progress(0.01, f"Đăng lên {PLATFORMS[platform]} · {target_label(target)}…")
    if target["via"] == "zernio":
        res = _zernio_publish(settings, target, path, meta, progress)
    elif target["via"] == "api" and platform == "youtube":
        res = yt_upload(settings, target["account"], path, meta, progress)
    elif target["via"] == "api" and platform == "facebook":
        res = fb_upload(settings, target["account"], path, meta, progress)
    elif target["via"] == "browser":
        from . import browser_post

        res = browser_post.fill_and_wait(settings, target["account"]["id"], dict(meta, file=str(path)), progress)
    else:
        raise PublishError("Cách 'Tự tải lên' không chạy nền – dùng nút Chuẩn bị đăng.")
    rec = {"id": uuid.uuid4().hex[:10], "time": time.time(), "platform": platform, "kind": kind,
           "via": target["via"], "account": target["name"], "title": meta.get("title", ""), "file": str(path),
           "publish_at": meta.get("publish_at") or "", **res}
    rec["warn"] = "; ".join(x for x in (warn, res.get("warn")) if x)
    save_post(p, rec)
    return rec
