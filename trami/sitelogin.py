"""Đăng nhập trang video để tải chất lượng cao + nhận link từ dòng chia sẻ (2.0.51).

Bilibili chỉ cho khách (chưa đăng nhập) xem / tải tới 480p; 720p / 1080p cần đăng nhập. Lấy cookie từ Chrome / Edge trên
Windows thường hỏng (mã hoá "App-Bound" từ Chrome 127, trình duyệt đang mở thì khoá file cookie) -> đăng nhập Bilibili
ngay trong tool bằng MÃ QR (quét bằng app Bilibili trên điện thoại, không nhập mật khẩu vào tool), như trang web Bilibili:
  passport.bilibili.com/x/passport-login/web/qrcode/generate -> {url, qrcode_key} (vẽ url thành mã QR)
  .../qrcode/poll?qrcode_key= -> data.code 86101 chưa quét / 86090 đã quét chờ xác nhận / 86038 hết hạn / 0 xong
  (cookie SESSDATA, bili_jct, DedeUserID… ở Set-Cookie và trong data.url)
Cookie ghi file Netscape `data/cookies/bilibili.txt` -> yt-dlp `cookiefile`. Trang khác (Douyin, YouTube…): cookie từ
trình duyệt (`dl_browser`, Firefox chạy ổn nhất) hoặc file cookies.txt người dùng xuất (`dl_cookie_file`).
"""
from __future__ import annotations

import json
import re
import time
import urllib.request
from pathlib import Path

from .config import DATA_DIR

COOKIES_DIR = DATA_DIR / "cookies"
BILI_FILE = COOKIES_DIR / "bilibili.txt"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/130.0 Safari/537.36")
BILI_HOSTS = ("bilibili.com", "b23.tv", "bili2233.cn", "bilibili.tv")
QR_GEN = "https://passport.bilibili.com/x/passport-login/web/qrcode/generate"
QR_POLL = "https://passport.bilibili.com/x/passport-login/web/qrcode/poll?qrcode_key="
NAV = "https://api.bilibili.com/x/web-interface/nav"
BILI_COOKIES = ("SESSDATA", "bili_jct", "DedeUserID", "DedeUserID__ckMd5", "sid")

# trình duyệt yt-dlp đọc được cookie (Cốc Cốc không có trong danh sách của yt-dlp)
BROWSERS = (("", "Không dùng"), ("firefox", "Firefox (chạy ổn nhất)"), ("edge", "Microsoft Edge"),
            ("chrome", "Google Chrome"), ("brave", "Brave"), ("opera", "Opera"), ("vivaldi", "Vivaldi"))


# ------------------------------------------------------------------ link

_URL = re.compile(r"https?://[^\s<>\"'，。、！？】）」』《》]+", re.I)
_BV = re.compile(r"\b(BV[0-9A-Za-z]{10})\b")


def extract_url(text: str) -> str:
    """Lấy link từ ô dán: link thường, dòng chia sẻ của app ("【tên】 https://b23.tv/xx" / "7.43 复制打开抖音…
    https://v.douyin.com/xx/ …") hoặc chỉ mã BV của Bilibili. Không có -> ""."""
    text = (text or "").strip()
    m = _URL.search(text)
    if m:
        return m.group(0).rstrip(".,;:!?)]}>")
    m = _BV.search(text)
    if m:
        return f"https://www.bilibili.com/video/{m.group(1)}"
    return ""


def is_bilibili(url: str) -> bool:
    host = re.sub(r"^https?://", "", url or "", flags=re.I).split("/")[0].lower()
    return any(host == h or host.endswith("." + h) for h in BILI_HOSTS)


# ------------------------------------------------------------------ cookie file (định dạng Netscape cho yt-dlp)

def write_cookies(path: Path, cookies: dict[str, str], domain: str, expires: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = ["# Netscape HTTP Cookie File", "# Tạo bởi TraMi (đăng nhập bằng mã QR) – đừng gửi file này cho người khác", ""]
    for name, value in cookies.items():
        lines.append("\t".join((domain, "TRUE", "/", "FALSE", str(int(expires)), name, value)))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def read_cookies(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    try:
        for ln in path.read_text(encoding="utf-8").splitlines():
            if ln.startswith("#HttpOnly_"):
                ln = ln[len("#HttpOnly_"):]
            if not ln or ln.startswith("#"):
                continue
            f = ln.split("\t")
            if len(f) >= 7:
                exp = int(f[4] or 0) if f[4].lstrip("-").isdigit() else 0
                if exp and exp < time.time():
                    continue
                out[f[5]] = f[6]
    except OSError:
        pass
    return out


def bili_cookie_file() -> Path | None:
    return BILI_FILE if read_cookies(BILI_FILE).get("SESSDATA") else None


def bili_logout() -> None:
    BILI_FILE.unlink(missing_ok=True)


# ------------------------------------------------------------------ đăng nhập bằng mã QR

def _get(url: str, cookies: dict | None = None, timeout: float = 10):
    headers = {"User-Agent": UA, "Referer": "https://www.bilibili.com/"}
    if cookies:
        headers["Cookie"] = "; ".join(f"{k}={v}" for k, v in cookies.items())
    with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace")), r.headers


def bili_qr_start() -> dict:
    """{"url": nội dung mã QR, "key": qrcode_key}."""
    js, _ = _get(QR_GEN)
    d = js.get("data") or {}
    if js.get("code") != 0 or not d.get("url") or not d.get("qrcode_key"):
        raise RuntimeError(f"Bilibili không tạo được mã QR: {js.get('message') or js}")
    return {"url": d["url"], "key": d["qrcode_key"]}


def _cookies_from(headers, url: str) -> tuple[dict[str, str], int]:
    """Cookie đăng nhập từ Set-Cookie (giữ nguyên giá trị đã mã hoá như trình duyệt lưu) hoặc từ data.url."""
    got: dict[str, str] = {}
    expires = 0
    for sc in (headers.get_all("Set-Cookie") or []) if headers is not None else []:
        first = sc.split(";", 1)[0]
        if "=" in first:
            k, v = first.split("=", 1)
            if k.strip() in BILI_COOKIES and v:
                got[k.strip()] = v.strip()
    query = url.split("?", 1)[1] if "?" in url else ""
    for part in query.split("&"):
        if "=" in part:
            k, v = part.split("=", 1)
            if k in BILI_COOKIES and v and k not in got:
                got[k] = v      # SESSDATA trong link đã ở dạng %2C… giống cookie của trình duyệt
            elif k == "Expires" and v.isdigit():
                expires = int(v)
    if not expires or expires < time.time():
        expires = int(time.time()) + 180 * 86400   # SESSDATA của Bilibili sống khoảng 6 tháng
    return got, expires


def bili_qr_poll(key: str) -> dict:
    """Trạng thái: wait (chưa quét) / scanned (đã quét, chờ bấm xác nhận trên điện thoại) / expired / ok / error."""
    js, headers = _get(QR_POLL + key)
    d = js.get("data") or {}
    code = d.get("code", js.get("code"))
    if code == 86101:
        return {"state": "wait"}
    if code == 86090:
        return {"state": "scanned"}
    if code == 86038:
        return {"state": "expired"}
    if code == 0:
        cookies, expires = _cookies_from(headers, d.get("url") or "")
        if not cookies.get("SESSDATA"):
            return {"state": "error", "msg": "Bilibili không trả về cookie đăng nhập"}
        write_cookies(BILI_FILE, cookies, ".bilibili.com", expires)
        return {"state": "ok"}
    return {"state": "error", "msg": d.get("message") or js.get("message") or str(code)}


def bili_status() -> dict:
    """{"login": bool, "name": tên tài khoản, "vip": bool} theo cookie đã lưu (hỏi Bilibili)."""
    ck = read_cookies(BILI_FILE)
    if not ck.get("SESSDATA"):
        return {"login": False}
    try:
        js, _ = _get(NAV, ck, timeout=8)
    except Exception as e:  # noqa: BLE001 – mất mạng: vẫn coi là đã đăng nhập (cookie còn hạn)
        return {"login": True, "name": "", "offline": str(e)}
    d = js.get("data") or {}
    if js.get("code") == 0 and d.get("isLogin"):
        return {"login": True, "name": d.get("uname") or "", "vip": bool(d.get("vipStatus"))}
    return {"login": False, "expired": True}


def qr_matrix(text: str):
    """Ma trận mã QR (numpy bool, True = ô đen) – OpenCV có sẵn trong tool; không có thì gói qrcode."""
    import numpy as np

    try:
        import cv2

        img = cv2.QRCodeEncoder.create().encode(text)
        return np.asarray(img) < 128
    except Exception:  # noqa: BLE001
        import qrcode   # type: ignore

        q = qrcode.QRCode(border=0)
        q.add_data(text)
        q.make(fit=True)
        return np.array(q.get_matrix(), bool)
