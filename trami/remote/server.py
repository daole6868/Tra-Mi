"""Máy chủ điều khiển từ xa chạy NGAY TRONG tool (2.3.0).

- aiohttp ở 1 luồng riêng (vòng sự kiện riêng) – không đụng luồng giao diện; việc nặng (đọc dự án, OCR thử…) chạy ở
  ThreadPoolExecutor; thay đổi dự án luôn qua hàng việc (api.py).
- Trong mạng nhà: https://<IP máy tính>:<cổng> (chứng chỉ tự ký – app Android ghim vân tay lấy từ mã QR; không có gói
  cryptography thì http). Ở ngoài: tool tự nối ra trạm VPS (tunnel.py) – điện thoại vào qua VPS, không mở cổng modem.
- Đăng nhập: quét mã QR ghép máy (mã 6 số dùng 1 lần, 10 phút) hoặc mật khẩu đặt ở Cài đặt → thẻ thiết bị (ngẫu nhiên
  32 byte, chỉ lưu bản băm) trong cookie HttpOnly / tiêu đề Authorization. Sai mật khẩu / mã: tối đa 8 lần / 10 phút mỗi
  địa chỉ, 30 lần / 10 phút tất cả.
- KHÔNG BAO GIỜ trả ra: API key, mật khẩu, cookie, tài khoản đăng video, đường dẫn tuỳ ý trên máy (chỉ file thuộc dự án:
  video gốc / bản xem trước, ảnh, bản lồng, giọng từng câu, file đã xuất)."""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import secrets
import socket
import ssl
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from pathlib import Path
from urllib.parse import quote, urlencode

from .. import APP_NAME, __version__
from ..config import DATA_DIR, err_text, load_settings, log_error, save_settings
from . import api
from .api import ApiError

WEB_DIR = Path(__file__).with_name("web")
REMOTE_DIR = DATA_DIR / "remote"
DEVICES_FILE = REMOTE_DIR / "devices.json"
CERT_FILE = REMOTE_DIR / "cert.pem"
KEY_FILE = REMOTE_DIR / "key.pem"
DEFAULT_PORT = 8765
COOKIE = "sv_t"
PAIR_TTL = 600            # mã ghép máy sống 10 phút
FAIL_WIN = 600
FAIL_IP, FAIL_ALL = 8, 30
PBKDF_ROUNDS = 200_000
ONLINE_SEC = 90           # điện thoại hỏi trong 90 s gần nhất = đang kết nối
FONTS = {"BeVietnamPro-Regular.ttf", "BeVietnamPro-Bold.ttf"}
PUBLIC = {"/api/ping", "/api/login", "/api/pair", "/api/session"}
RELAY_TOKEN = secrets.token_urlsafe(18)   # đường hầm VPS gắn kèm -> máy chủ tin địa chỉ điện thoại nó báo


# ================================================================== mật khẩu / thiết bị
def hash_pw(pw: str) -> str:
    salt = secrets.token_bytes(16)
    h = hashlib.pbkdf2_hmac("sha256", pw.encode("utf-8"), salt, PBKDF_ROUNDS)
    return f"pbkdf2${salt.hex()}${h.hex()}"


def check_pw(pw: str, stored: str) -> bool:
    try:
        kind, salt, h = (stored or "").split("$")
        if kind != "pbkdf2":
            return False
        got = hashlib.pbkdf2_hmac("sha256", (pw or "").encode("utf-8"), bytes.fromhex(salt), PBKDF_ROUNDS)
        return hmac.compare_digest(got.hex(), h)
    except (ValueError, TypeError):
        return False


def set_password(pw: str) -> None:
    pw = pw or ""
    if len(pw) < 6:
        raise ValueError("Mật khẩu ít nhất 6 ký tự")
    save_settings({"remote_pw_secret": hash_pw(pw)})


def has_password(st: dict | None = None) -> bool:
    return bool((st or load_settings()).get("remote_pw_secret"))


def _tok_hash(tok: str) -> str:
    return hashlib.sha256(("subvideo|" + (tok or "")).encode("utf-8")).hexdigest()


class Devices:
    """Điện thoại đã ghép (data/remote/devices.json): id, tên, băm thẻ, lúc ghép, lần cuối, địa chỉ, qua (lan / vps)."""

    def __init__(self, path: Path = DEVICES_FILE):
        self.path = path
        self.lock = threading.Lock()
        self._items: list[dict] | None = None
        self._saved = 0.0

    def _load(self) -> list[dict]:
        if self._items is None:
            try:
                self._items = [d for d in json.loads(self.path.read_text("utf-8")) if isinstance(d, dict)]
            except (OSError, ValueError):
                self._items = []
        return self._items

    def _save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._items or [], ensure_ascii=False, indent=1), "utf-8")
        tmp.replace(self.path)
        self._saved = time.time()

    def add(self, name: str, ip: str = "", via: str = "lan") -> tuple[str, dict]:
        tok = secrets.token_urlsafe(32)
        d = {"id": secrets.token_hex(6), "name": (name or "Điện thoại").strip()[:40], "hash": _tok_hash(tok),
             "created": time.time(), "seen": time.time(), "ip": ip, "via": via}
        with self.lock:
            self._load().append(d)
            self._save()
        return tok, dict(d)

    def check(self, tok: str) -> dict | None:
        if not tok or len(tok) > 200:
            return None
        h = _tok_hash(tok)
        with self.lock:
            for d in self._load():
                if hmac.compare_digest(d.get("hash", ""), h):
                    return d
        return None

    def touch(self, d: dict, ip: str, via: str):
        with self.lock:
            d["seen"], d["ip"], d["via"] = time.time(), ip, via
            if time.time() - self._saved > 60:
                self._save()

    def revoke(self, did: str | None = None) -> int:
        with self.lock:
            items = self._load()
            keep = [d for d in items if did and d.get("id") != did]
            n = len(items) - len(keep)
            self._items = keep
            self._save()
        return n

    def list(self) -> list[dict]:
        now = time.time()
        with self.lock:
            return [{k: d.get(k) for k in ("id", "name", "created", "seen", "ip", "via")}
                    | {"online": now - (d.get("seen") or 0) < ONLINE_SEC} for d in self._load()]


DEVICES = Devices()


class Pairing:
    """Mã ghép máy 6 số (hiện trong mã QR ở Cài đặt) – dùng 1 lần, sống PAIR_TTL giây."""

    def __init__(self):
        self.code, self.until = "", 0.0
        self.lock = threading.Lock()

    def new(self) -> str:
        with self.lock:
            self.code, self.until = f"{secrets.randbelow(1_000_000):06d}", time.time() + PAIR_TTL
            return self.code

    def take(self, code: str) -> bool:
        with self.lock:
            ok = bool(self.code) and time.time() < self.until and hmac.compare_digest(self.code, (code or "").strip())
            if ok:
                self.code, self.until = "", 0.0
            return ok

    def current(self) -> tuple[str, float]:
        with self.lock:
            return (self.code, self.until) if time.time() < self.until else ("", 0.0)


PAIRING = Pairing()


class Limiter:
    def __init__(self):
        self.lock = threading.Lock()
        self.fails: dict[str, list[float]] = {}

    def blocked(self, ip: str) -> bool:
        now = time.time()
        with self.lock:
            for k in list(self.fails):
                self.fails[k] = [t for t in self.fails[k] if now - t < FAIL_WIN]
                if not self.fails[k]:
                    del self.fails[k]
            total = sum(len(v) for v in self.fails.values())
            return len(self.fails.get(ip, [])) >= FAIL_IP or total >= FAIL_ALL

    def fail(self, ip: str):
        with self.lock:
            self.fails.setdefault(ip, []).append(time.time())


LIMITER = Limiter()


# ================================================================== chứng chỉ tự ký
def ensure_cert() -> str:
    """Tạo chứng chỉ tự ký (10 năm) lần đầu; trả vân tay SHA-256 (hex thường) – "" khi không có gói cryptography."""
    try:
        if CERT_FILE.exists() and KEY_FILE.exists():
            return cert_fingerprint(CERT_FILE)
        import datetime as dt

        from cryptography import x509
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import ec
        from cryptography.x509.oid import NameOID
    except (KeyboardInterrupt, SystemExit):
        raise
    except BaseException:  # noqa: BLE001 – thiếu gói / gói hỏng (lõi Rust báo PanicException) -> chạy http
        log_error("Điều khiển từ xa: tạo chứng chỉ")
        return ""
    REMOTE_DIR.mkdir(parents=True, exist_ok=True)
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, f"{APP_NAME} {socket.gethostname()[:40]}")])
    now = dt.datetime.now(dt.timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(x509.random_serial_number()).not_valid_before(now - dt.timedelta(days=1))
            .not_valid_after(now + dt.timedelta(days=3650))
            .add_extension(x509.SubjectAlternativeName([x509.DNSName("subvideo.local")]), critical=False)
            .sign(key, hashes.SHA256()))
    KEY_FILE.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                           serialization.NoEncryption()))
    CERT_FILE.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    return cert_fingerprint(CERT_FILE)


def cert_fingerprint(pem_file: Path) -> str:
    der = ssl.PEM_cert_to_DER_cert(pem_file.read_text("ascii"))
    return hashlib.sha256(der).hexdigest()


def lan_ips() -> list[str]:
    """Địa chỉ IPv4 trong mạng nhà của máy tính (đường ra mạng trước, rồi các card mạng khác)."""
    out: list[str] = []
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("10.255.255.255", 1))
        out.append(s.getsockname()[0])
        s.close()
    except OSError:
        pass
    try:
        for ip in socket.gethostbyname_ex(socket.gethostname())[2]:
            if ip not in out:
                out.append(ip)
    except OSError:
        pass
    priv = [ip for ip in out if ip.startswith(("192.168.", "10.")) or
            (ip.startswith("172.") and 16 <= int(ip.split(".")[1]) <= 31)]
    return priv or [ip for ip in out if not ip.startswith("127.")]


# ================================================================== giữ máy thức (Windows)
def _keep_awake(on: bool):
    if sys.platform != "win32":
        return
    try:
        import ctypes

        ES_CONTINUOUS, ES_SYSTEM_REQUIRED = 0x80000000, 0x00000001
        ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS | (ES_SYSTEM_REQUIRED if on else 0))
    except Exception:  # noqa: BLE001
        pass


# ================================================================== HTTP
def _client(request) -> tuple[str, str]:
    ip = request.remote or ""
    if ip in ("127.0.0.1", "::1") and hmac.compare_digest(request.headers.get("X-SV-Relay", ""), RELAY_TOKEN):
        return (request.headers.get("X-SV-Client") or "?")[:64], "vps"
    return ip, "lan"


def _token(request) -> str:
    a = request.headers.get("Authorization", "")
    if a.startswith("Bearer "):
        return a[7:].strip()
    return request.cookies.get(COOKIE, "")


def _json(data, status: int = 200):
    from aiohttp import web

    return web.json_response(data, status=status, dumps=lambda o: json.dumps(o, ensure_ascii=False))


def _err(msg: str, status: int = 400):
    return _json({"error": msg}, status)


def _file(path: Path, download: bool = False, name: str | None = None, ctype: str | None = None):
    from aiohttp import web

    r = web.FileResponse(path, chunk_size=256 * 1024)
    if ctype:
        r.content_type = ctype
    if download:
        nm = name or path.name
        r.headers["Content-Disposition"] = f"attachment; filename*=UTF-8''{quote(nm)}"
    r.headers["Cache-Control"] = "private, max-age=60"
    return r


SEC_HEADERS = {
    "X-Content-Type-Options": "nosniff", "Referrer-Policy": "no-referrer", "X-Frame-Options": "DENY",
    "Content-Security-Policy": "default-src 'self'; img-src 'self' data: blob:; media-src 'self' blob:; "
                               "style-src 'self' 'unsafe-inline'; script-src 'self'; connect-src 'self'; "
                               "font-src 'self' data:; frame-ancestors 'none'",
}


class RemoteServer:
    def __init__(self):
        self.loop: asyncio.AbstractEventLoop | None = None
        self.thread: threading.Thread | None = None
        self.runner = None
        self.port = 0
        self.https = False
        self.fp = ""
        self.error = ""
        self.tunnel = None
        self.pool = ThreadPoolExecutor(4, thread_name_prefix="remote")
        self.lock = threading.Lock()

    # ------------------------------------------------------------ chạy / dừng
    @property
    def running(self) -> bool:
        return bool(self.thread and self.thread.is_alive() and self.runner is not None)

    def start(self, port: int, https: bool = True) -> bool:
        with self.lock:
            if self.running and self.port == port and self.https == (https and bool(self.fp)):
                return True
            self._stop()
            self.error = ""
            self.fp = ensure_cert() if https else ""
            ready = threading.Event()
            self.thread = threading.Thread(target=self._main, args=(port, ready), name="remote-server", daemon=True)
            self.thread.start()
            ready.wait(10)
            return self.running

    def stop(self):
        with self.lock:
            self._stop()

    def _stop(self):
        loop, th = self.loop, self.thread
        if loop and th and th.is_alive():
            fut = asyncio.run_coroutine_threadsafe(self._shutdown(), loop)
            try:
                fut.result(10)
            except Exception:  # noqa: BLE001
                pass
            loop.call_soon_threadsafe(loop.stop)
            th.join(10)
        self.loop = self.thread = self.runner = None
        self.port = 0

    def _main(self, port: int, ready: threading.Event):
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self.loop = loop
        try:
            loop.run_until_complete(self._startup(port))
        except Exception as e:  # noqa: BLE001
            self.error = (f"Cổng {port} đang bị chương trình khác dùng – đổi cổng ở Cài đặt"
                          if isinstance(e, OSError) else err_text(e))
            log_error("Điều khiển từ xa: mở máy chủ")
            self.runner = None
            ready.set()
            loop.close()
            self.loop = None
            return
        ready.set()
        _keep_awake(bool(load_settings().get("remote_awake", True)))
        try:
            loop.run_forever()
        finally:
            _keep_awake(False)
            try:
                loop.run_until_complete(loop.shutdown_asyncgens())
            except Exception:  # noqa: BLE001
                pass
            loop.close()

    async def _startup(self, port: int):
        from aiohttp import web

        app = make_app(self)
        runner = web.AppRunner(app, access_log=None, handle_signals=False)
        await runner.setup()
        ctx = None
        if self.fp:
            ctx = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
            ctx.load_cert_chain(str(CERT_FILE), str(KEY_FILE))
        site = web.TCPSite(runner, "0.0.0.0", port, ssl_context=ctx, reuse_address=True)
        try:
            await site.start()
        except BaseException:
            await runner.cleanup()
            raise
        self.runner, self.port, self.https = runner, port, bool(ctx)

    async def _shutdown(self):
        if self.tunnel:
            await self.tunnel.close()
            self.tunnel = None
        if self.runner:
            await self.runner.cleanup()

    def submit(self, coro, timeout: float = 15):
        if not self.loop:
            return None
        return asyncio.run_coroutine_threadsafe(coro, self.loop).result(timeout)

    # ------------------------------------------------------------ đường hầm VPS
    def set_tunnel(self, url: str, key: str, fp: str):
        if not self.loop:
            return
        from .tunnel import Tunnel

        async def go():
            if self.tunnel and (self.tunnel.url, self.tunnel.key, self.tunnel.fp) == (url, key, fp):
                return
            if self.tunnel:
                await self.tunnel.close()
                self.tunnel = None
            if url and key:
                scheme = "https" if self.https else "http"
                self.tunnel = Tunnel(url, key, fp, f"{scheme}://127.0.0.1:{self.port}", RELAY_TOKEN)
                self.tunnel.start()
        self.submit(go())

    async def run(self, fn, *a, **kw):
        return await asyncio.get_running_loop().run_in_executor(self.pool, partial(fn, *a, **kw))


SERVER = RemoteServer()


def make_app(srv: RemoteServer):
    from aiohttp import web

    @web.middleware
    async def guard(request, handler):
        path = request.path
        if path.startswith("/api/") and path not in PUBLIC:
            dev = DEVICES.check(_token(request))
            if not dev:
                return _err("Chưa đăng nhập", 401)
            if request.method not in ("GET", "HEAD") and request.headers.get("X-SV") != "1":
                return _err("Yêu cầu không hợp lệ", 403)
            ip, via = _client(request)
            DEVICES.touch(dev, ip, via)
            request["dev"] = dev
        try:
            resp = await handler(request)
        except ApiError as e:
            resp = _err(str(e), e.code)
        except web.HTTPException:
            raise
        except (ConnectionResetError, asyncio.CancelledError):
            raise
        except Exception as e:  # noqa: BLE001
            log_error(f"Điều khiển từ xa: {request.method} {path}")
            resp = _err(err_text(e), 500)
        for k, v in SEC_HEADERS.items():
            resp.headers.setdefault(k, v)
        return resp

    app = web.Application(middlewares=[guard], client_max_size=4 * 1024 * 1024)
    r = app.router
    run = srv.run

    async def body(request) -> dict:
        try:
            d = await request.json()
        except Exception:  # noqa: BLE001
            return {}
        return d if isinstance(d, dict) else {}

    def set_cookie(resp, tok: str, request):
        resp.set_cookie(COOKIE, tok, max_age=3600 * 24 * 365, httponly=True, samesite="Lax",
                        secure=request.secure or _client(request)[1] == "vps")

    # ------------------------------------------------------------ trang
    async def index(request):
        f = WEB_DIR / "index.html"
        resp = web.FileResponse(f)
        resp.headers["Cache-Control"] = "no-cache"
        return resp

    async def static(request):
        name = request.match_info["name"]
        f = (WEB_DIR / name).resolve()
        if f.parent != WEB_DIR.resolve() or not f.is_file():
            raise web.HTTPNotFound()
        resp = web.FileResponse(f)
        resp.headers["Cache-Control"] = "no-cache"
        return resp

    async def font(request):
        name = request.match_info["name"]
        if name not in FONTS:
            raise web.HTTPNotFound()
        from ..config import BUNDLE_DIR

        resp = web.FileResponse(BUNDLE_DIR / "fonts" / name)
        resp.headers["Cache-Control"] = "public, max-age=604800"
        return resp

    r.add_get("/", index)
    r.add_get("/static/{name}", static)
    r.add_get("/font/{name}", font)

    # ------------------------------------------------------------ đăng nhập
    async def ping(request):
        st = load_settings()
        return _json({"app": APP_NAME, "version": __version__, "pc": socket.gethostname()[:40],
                      "password": has_password(st), "via": _client(request)[1]})

    async def login(request):
        ip, via = _client(request)
        if LIMITER.blocked(ip):
            return _err("Sai quá nhiều lần – đợi 10 phút rồi thử lại", 429)
        d = await body(request)
        stored = load_settings().get("remote_pw_secret", "")
        if not stored:
            return _err("Máy tính chưa đặt mật khẩu – quét mã QR ở Cài đặt → Điều khiển từ xa", 403)
        if not await run(check_pw, str(d.get("password") or ""), stored):
            LIMITER.fail(ip)
            await asyncio.sleep(0.6)
            return _err("Sai mật khẩu", 401)
        tok, dev = DEVICES.add(str(d.get("name") or "Trình duyệt"), ip, via)
        resp = _json({"token": tok, "device": dev["id"]})
        set_cookie(resp, tok, request)
        return resp

    async def pair(request):
        ip, via = _client(request)
        if LIMITER.blocked(ip):
            return _err("Sai quá nhiều lần – đợi 10 phút rồi thử lại", 429)
        d = await body(request)
        if not PAIRING.take(str(d.get("code") or "")):
            LIMITER.fail(ip)
            await asyncio.sleep(0.6)
            return _err("Mã ghép sai hoặc đã hết hạn – bấm 'Mã QR mới' trên máy tính", 401)
        tok, dev = DEVICES.add(str(d.get("name") or "Điện thoại"), ip, via)
        resp = _json({"token": tok, "device": dev["id"]})
        set_cookie(resp, tok, request)
        return resp

    async def session(request):
        """App Android đổi địa chỉ (mạng nhà <-> VPS): gửi thẻ đã có -> đặt cookie cho địa chỉ mới."""
        ip, _ = _client(request)
        if LIMITER.blocked(ip):
            return _err("Sai quá nhiều lần – đợi 10 phút rồi thử lại", 429)
        d = await body(request)
        tok = str(d.get("token") or "")
        if not DEVICES.check(tok):
            LIMITER.fail(ip)
            return _err("Thiết bị đã bị ngắt – ghép lại bằng mã QR", 401)
        resp = _json({"ok": True})
        set_cookie(resp, tok, request)
        return resp

    async def logout(request):
        DEVICES.revoke(request["dev"]["id"])
        resp = _json({"ok": True})
        resp.del_cookie(COOKIE)
        return resp

    async def me(request):
        d = request["dev"]
        st = load_settings()
        return _json({"id": d["id"], "name": d.get("name"), "pc": socket.gethostname()[:40], "version": __version__,
                      "notify": bool(st.get("remote_notify", True)), "via": _client(request)[1]})

    r.add_get("/api/ping", ping)
    r.add_post("/api/login", login)
    r.add_post("/api/pair", pair)
    r.add_post("/api/session", session)
    r.add_post("/api/logout", logout)
    r.add_get("/api/me", me)

    # ------------------------------------------------------------ chung
    def q(request, k, default=None, typ=str):
        v = request.query.get(k)
        if v is None:
            return default
        try:
            return typ(v)
        except ValueError:
            raise ApiError(f"Tham số sai: {k}") from None

    async def state(request):
        return _json(await run(api.state))

    async def events(request):
        return _json({"events": await run(api.events, q(request, "since", 0.0, float)), "now": time.time()})

    async def options(request):
        return _json(await run(api.options, request.query.get("pid") or None))

    async def settings(request):
        return _json(await run(api.set_settings, await body(request)))

    async def links(request):
        d = await body(request)
        return _json(await run(api.queue_links, str(d.get("text") or ""), bool(d.get("auto", True))))

    async def cancel(request):
        from ..jobs import manager

        jid = request.match_info["jid"]
        if not manager.get(jid):
            raise ApiError("Không thấy việc này", 404)
        manager.cancel(jid)
        return _json({"ok": True})

    async def cancel_queued(request):
        from ..jobs import manager

        n = 0
        for j in manager.list():
            if j["status"] == "queued":
                n += manager.cancel(j["id"])
        return _json({"cancelled": n})

    r.add_get("/api/state", state)
    r.add_get("/api/events", events)
    r.add_get("/api/options", options)
    r.add_post("/api/settings", settings)
    r.add_post("/api/links", links)
    r.add_post("/api/jobs/{jid}/cancel", cancel)
    r.add_post("/api/jobs/cancel_queued", cancel_queued)

    # ------------------------------------------------------------ dự án
    def pid_of(request) -> str:
        pid = request.match_info["pid"]
        if not api.PID_RE.match(pid):
            raise ApiError("Không thấy dự án", 404)
        return pid

    def P(path: str, fn, method: str = "GET"):
        (r.add_get if method == "GET" else r.add_post)(f"/api/p/{{pid}}{path}", fn)

    async def detail(request):
        return _json(await run(api.detail, pid_of(request)))

    async def thumb(request):
        pid = pid_of(request)
        f = await run(lambda: api.project(pid).dir / "thumb.jpg")
        if not f.is_file():
            raise web.HTTPNotFound()
        return _file(f)

    async def video(request):
        return _file(await run(api.video_file, pid_of(request)))

    async def run_step(request):
        d = await body(request)
        return _json(await run(api.run_step, pid_of(request), str(d.get("step") or "")))

    async def target(request):
        d = await body(request)
        return _json(await run(api.set_target, pid_of(request), str(d.get("lang") or "vi")))

    P("", detail)
    P("/thumb", thumb)
    P("/video", video)
    P("/run", run_step, "POST")
    P("/target", target, "POST")

    # vùng phụ đề
    async def frame(request):
        f = await run(api.frame, pid_of(request), q(request, "t", 0.0, float), q(request, "w", 960, int))
        return _file(f, ctype="image/jpeg")

    async def r_detect(request):
        return _json(await run(api.detect_region, pid_of(request)))

    async def r_read(request):
        d = await body(request)
        return _json(await run(api.read_region, pid_of(request), float(d.get("t") or 0), d.get("box")))

    async def r_seek(request):
        d = await body(request)
        return _json(await run(api.seek_text, pid_of(request), float(d.get("t") or 0), d.get("box"),
                               int(d.get("dir") or 1)))

    async def r_pick(request):
        d = await body(request)
        return _json(await run(api.pick_color, pid_of(request), float(d.get("t") or 0), float(d.get("x") or 0),
                               float(d.get("y") or 0)))

    async def r_save(request):
        d = await body(request)
        return _json(await run(api.save_region, pid_of(request), d.get("box")))

    async def r_color(request):
        d = await body(request)
        return _json(await run(api.use_color, str(d.get("fill") or "")))

    P("/frame", frame)
    P("/region/detect", r_detect, "POST")
    P("/region/read", r_read, "POST")
    P("/region/seek", r_seek, "POST")
    P("/region/pick", r_pick, "POST")
    P("/region/save", r_save, "POST")
    r.add_post("/api/region/color", r_color)

    # bảng câu + dịch
    async def lines(request):
        return _json(await run(api.lines, pid_of(request), q(request, "offset", 0, int), q(request, "limit", 60, int),
                               request.query.get("filter", "all"), request.query.get("q", "")))

    async def l_edit(request):
        d = await body(request)
        return _json(await run(api.edit_line, pid_of(request), int(d.get("i", -1)), d.get("text"),
                               str(d.get("trans") or "")))

    async def l_delete(request):
        d = await body(request)
        return _json(await run(api.delete_lines, pid_of(request), list(d.get("rows") or []), d.get("texts")))

    async def translate(request):
        d = await body(request)
        return _json(await run(api.translate, pid_of(request), str(d.get("mode") or "auto"), d.get("rows"),
                               d.get("texts")))

    P("/lines", lines)
    P("/lines/edit", l_edit, "POST")
    P("/lines/delete", l_delete, "POST")
    P("/translate", translate, "POST")

    # lồng tiếng
    async def dub(request):
        return _json(await run(api.dub_info, pid_of(request)))

    async def voices(request):
        d = await body(request)
        return _json(await run(api.set_voices, pid_of(request), d.get("voice_all"), d.get("speakers")))

    async def voice_test(request):
        d = await body(request)
        f = await run(api.preview_voice, pid_of(request), str(d.get("voice") or ""), str(d.get("speaker") or ""))
        return _file(f)

    async def cat(request):
        d = await body(request)
        return _json(await run(api.cat_action, pid_of(request), str(d.get("cat") or ""), str(d.get("action") or ""),
                               d.get("value")))

    async def a_dub(request):
        return _file(await run(api.dub_audio, pid_of(request)))

    async def a_line(request):
        return _file(await run(api.line_audio, pid_of(request), int(request.match_info["i"])))

    P("/dub", dub)
    P("/voices", voices, "POST")
    P("/voice_test", voice_test, "POST")
    P("/dub/cat", cat, "POST")
    P("/audio/dub", a_dub)
    P("/audio/line/{i:\\d+}", a_line)

    # xuất
    async def export(request):
        return _json(await run(api.export_info, pid_of(request)))

    async def export_set(request):
        return _json(await run(api.export_settings, pid_of(request), await body(request)))

    async def out(request):
        f = await run(api.output_file, pid_of(request), int(request.match_info["idx"]))
        return _file(f, download=request.query.get("dl") == "1")

    async def srt(request):
        f = await run(api.make_srt, pid_of(request))
        return _file(f, download=True, ctype="application/x-subrip")

    P("/export", export)
    P("/export", export_set, "POST")
    P("/out/{idx:\\d+}", out)
    P("/srt", srt)
    return app


# ================================================================== điều khiển từ giao diện trên máy
def apply_settings(st: dict | None = None) -> dict:
    """Bật / tắt / khởi động lại máy chủ + đường hầm theo Cài đặt (gọi khi mở tool và khi lưu thẻ Điều khiển từ xa)."""
    st = st or load_settings()
    if not st.get("remote_on"):
        SERVER.stop()
        return status(st)
    try:
        import aiohttp  # noqa: F401
    except ImportError:
        SERVER.error = "Thiếu gói aiohttp – chạy: .venv\\Scripts\\python -m pip install aiohttp"
        return status(st)
    port = int(st.get("remote_port") or DEFAULT_PORT)
    if SERVER.start(port, True):
        SERVER.set_tunnel(str(st.get("remote_vps_url") or "").strip(), str(st.get("remote_vps_secret") or "").strip(),
                          str(st.get("remote_vps_fp") or "").strip().lower().replace(":", ""))
    return status(st)


def lan_urls(st: dict | None = None) -> list[str]:
    if not SERVER.running:
        return []
    scheme = "https" if SERVER.https else "http"
    return [f"{scheme}://{ip}:{SERVER.port}" for ip in lan_ips()]


def status(st: dict | None = None) -> dict:
    st = st or load_settings()
    t = SERVER.tunnel
    tun = {"state": "off", "msg": "", "url": str(st.get("remote_vps_url") or "")}
    if t is not None:
        tun.update({"state": t.state, "msg": t.message})
    elif tun["url"] and SERVER.running:
        tun["state"] = "connecting"
    return {"on": bool(st.get("remote_on")), "running": SERVER.running, "port": SERVER.port, "https": SERVER.https,
            "fp": SERVER.fp, "error": SERVER.error, "lan": lan_urls(st), "tunnel": tun,
            "password": has_password(st), "devices": DEVICES.list()}


def pair_link(st: dict | None = None) -> str:
    """Nội dung mã QR ghép máy: subvideo://pair?lan=…&lfp=…&vps=…&vfp=…&c=<mã 6 số>&pc=<tên máy>."""
    st = st or load_settings()
    code, _ = PAIRING.current()
    code = code or PAIRING.new()
    q = {"c": code, "pc": socket.gethostname()[:40]}
    lan = lan_urls(st)
    if lan:
        q["lan"] = lan[0]
        if SERVER.fp:
            q["lfp"] = SERVER.fp
    vps = str(st.get("remote_vps_url") or "").strip().rstrip("/")
    if vps and st.get("remote_vps_secret"):
        q["vps"] = vps
        fp = str(st.get("remote_vps_fp") or "").strip().lower().replace(":", "")
        if fp:
            q["vfp"] = fp
    return "subvideo://pair?" + urlencode(q)


def parse_vps(text: str) -> dict:
    """Chuỗi kết nối file cài VPS in ra: subvideo-vps://<máy>[:cổng]?k=<mã>&fp=<vân tay>&s=https -> 3 ô Cài đặt."""
    from urllib.parse import parse_qs, urlparse

    t = (text or "").strip()
    if not t.startswith("subvideo-vps://"):
        raise ValueError("Dán đúng dòng 'subvideo-vps://…' mà file cài VPS in ra")
    u = urlparse(t)
    qs = parse_qs(u.query)
    host = u.netloc
    key = (qs.get("k") or [""])[0]
    if not host or len(key) < 16:
        raise ValueError("Chuỗi kết nối VPS thiếu địa chỉ hoặc mã kết nối")
    fp = (qs.get("fp") or [""])[0].lower().replace(":", "")
    return {"remote_vps_url": f"https://{host}", "remote_vps_secret": key, "remote_vps_fp": fp}


def vps_installer() -> str:
    """File cài VPS 1 file (cai_vps.sh + chương trình trạm relay.py gói ở cuối dạng base64) – nút "Lưu file cài VPS…"."""
    import base64

    from ..config import BUNDLE_DIR

    base = BUNDLE_DIR / "vps"
    sh = (base / "cai_vps.sh").read_text("utf-8")
    b64 = base64.encodebytes((base / "relay.py").read_bytes()).decode("ascii")
    block = "".join("#" + ln + "\n" for ln in b64.splitlines())
    head, _, _ = sh.partition("#__RELAY_BEGIN__\n")
    return head + "#__RELAY_BEGIN__\n" + block + "#__RELAY_END__\n"

