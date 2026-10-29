"""Mạng chập chờn / mất mạng giữa chừng: nhận ra lỗi mạng, tạm dừng chờ có mạng lại rồi làm tiếp, bấm Huỷ là dừng ngay.

Mọi tác vụ trong hàng đợi (jobs) chạy trong `task(cancel, report)` -> các hàm gọi mạng (dịch AI, Google, Edge TTS)
tự lấy được nút Huỷ và chỗ báo tin của tác vụ đó mà không cần truyền qua từng hàm.
"""
from __future__ import annotations

import errno
import socket
import threading
import time
import urllib.request
from contextlib import contextmanager
from typing import Callable

from .ffmpeg import Cancelled

NET_WAIT = 600.0       # mất mạng: chờ tối đa 10 phút rồi mới báo lỗi
NET_WAIT_QUICK = 45.0  # việc nhỏ ngoài hàng đợi (dịch 1 câu, nghe thử giọng): chờ ngắn thôi
CHECK_EVERY = 3.0      # kiểm tra lại mạng mỗi 3 giây khi đang chờ
# địa chỉ kiểm tra có mạng: Cloudflare / Google / AliDNS (vào được cả khi bị chặn 1 trong số đó)
PROBE_HOSTS = (("1.1.1.1", 443), ("8.8.8.8", 443), ("223.5.5.5", 443), ("www.bing.com", 443))


class NetworkDown(RuntimeError):
    """Mất mạng quá lâu – phần việc đã xong vẫn được giữ, có mạng lại thì chạy lại để làm tiếp."""


# ------------------------------------------------------------------ ngữ cảnh của tác vụ đang chạy
_ctx = threading.local()


@contextmanager
def task(cancel: Callable[[], bool] | None = None, report: Callable[[str], None] | None = None):
    """Gắn nút Huỷ + chỗ báo tin (dòng trạng thái của tác vụ) cho mọi lần gọi mạng trong luồng này."""
    old = getattr(_ctx, "v", None)
    _ctx.v = (cancel, report)
    try:
        yield
    finally:
        _ctx.v = old


def current() -> tuple[Callable[[], bool] | None, Callable[[str], None] | None]:
    return getattr(_ctx, "v", None) or (None, None)


def cancelled(cancel: Callable[[], bool] | None = None) -> bool:
    c = cancel or current()[0]
    return bool(c and c())


def sleep(seconds: float, cancel: Callable[[], bool] | None = None) -> None:
    """Ngủ nhưng bấm Huỷ là dừng ngay (không phải đợi hết thời gian chờ thử lại)."""
    end = time.time() + seconds
    while True:
        if cancelled(cancel):
            raise Cancelled()
        left = end - time.time()
        if left <= 0:
            return
        time.sleep(min(0.2, left))


# ------------------------------------------------------------------ nhận ra lỗi mạng
_WIN_NET = {10050, 10051, 10052, 10053, 10054, 10057, 10058, 10060, 10064, 10065, 11001, 11002, 11003, 11004}
_ERRNO_NET = {getattr(errno, n) for n in ("ENETDOWN", "ENETUNREACH", "ENETRESET", "ECONNABORTED", "ECONNRESET",
                                          "ETIMEDOUT", "EHOSTDOWN", "EHOSTUNREACH", "EPIPE") if hasattr(errno, n)}
_TEXT_NET = ("getaddrinfo failed", "name resolution", "name or service not known", "nodename nor servname",
             "no address associated", "network is unreachable", "network is down", "no route to host",
             "host is down", "timed out", "timeout", "connection reset", "connection aborted",
             "remotedisconnected", "remote end closed", "cannot connect to host", "server disconnected",
             "connection closed", "connection lost", "connection broken", "eof occurred in violation",
             "unexpected eof", "failed to establish a new connection", "max retries exceeded",
             "temporarily unavailable", "incompleteread", "connectionerror", "protocolerror")
_TEXT_LOCAL = ("refused", "10061", "localhost", "127.0.0.1", "[::1]")   # dịch vụ trên máy chưa bật – không phải mạng


def _chain(e: BaseException):
    seen = set()
    stack = [e]
    while stack:
        x = stack.pop()
        if x is None or id(x) in seen:
            continue
        seen.add(id(x))
        yield x
        stack += [x.__cause__, x.__context__]
        for a in (getattr(x, "reason", None), *(getattr(x, "args", ()) or ())[:2]):
            if isinstance(a, BaseException):
                stack.append(a)


def is_net_error(e: BaseException) -> bool:
    """Lỗi do mạng (mất mạng, mạng chập chờn, quá giờ chờ) – không phải lỗi API key / máy chủ từ chối / dữ liệu sai."""
    if isinstance(e, Cancelled):
        return False
    text = " ".join(f"{type(x).__name__} {x}" for x in _chain(e)).lower()
    if any(k in text for k in _TEXT_LOCAL):
        return False
    for x in _chain(e):
        if isinstance(x, ConnectionRefusedError):
            return False
        if isinstance(x, (socket.gaierror, socket.timeout, TimeoutError, ConnectionError)):
            return True
        if isinstance(x, OSError) and (getattr(x, "winerror", None) in _WIN_NET or x.errno in _ERRNO_NET):
            return True
        mod = type(x).__module__ or ""
        name = type(x).__name__
        if mod.startswith(("requests", "urllib3")) and name in (
                "ConnectionError", "ConnectTimeout", "ReadTimeout", "Timeout", "ChunkedEncodingError",
                "ProtocolError", "NewConnectionError", "ReadTimeoutError", "ConnectTimeoutError", "MaxRetryError",
                "IncompleteRead"):
            return True
        if mod.startswith("aiohttp") and name in (
                "ClientConnectionError", "ClientConnectorError", "ClientOSError", "ServerDisconnectedError",
                "ServerTimeoutError", "ClientPayloadError", "ConnectionTimeoutError", "SocketTimeoutError",
                "ClientConnectorDNSError"):
            return True
        if mod.startswith(("http.client", "ssl")) and name in ("IncompleteRead", "RemoteDisconnected",
                                                               "SSLEOFError", "SSLZeroReturnError"):
            return True
    return any(k in text for k in _TEXT_NET)


# ------------------------------------------------------------------ có mạng không
def _probe_targets(hosts=()) -> list[tuple[str, int]]:
    out: list[tuple[str, int]] = []
    try:   # máy dùng proxy (biến môi trường / cài đặt Windows) -> có mạng = nối được tới proxy
        for k in ("https", "http"):
            u = urllib.request.getproxies().get(k)
            if u:
                from urllib.parse import urlsplit

                sp = urlsplit(u if "://" in u else "http://" + u)
                if sp.hostname:
                    out.append((sp.hostname, sp.port or 8080))
                    break
    except Exception:  # noqa: BLE001
        pass
    return out + list(hosts) + list(PROBE_HOSTS)


def online(hosts=(), timeout: float = 3.0) -> bool:
    """Có mạng không: thử mở kết nối tới vài địa chỉ cùng lúc, được 1 cái là có mạng."""
    targets = _probe_targets(hosts)
    ok = threading.Event()
    left = [len(targets)]
    lock = threading.Lock()
    finished = threading.Event()

    def probe(h: str, port: int):
        try:
            with socket.create_connection((h, port), timeout=timeout):
                ok.set()
        except OSError:
            pass
        finally:
            with lock:
                left[0] -= 1
                if left[0] == 0:
                    finished.set()

    for h, port in targets:
        threading.Thread(target=probe, args=(h, port), daemon=True, name="trami-probe").start()
    end = time.time() + timeout + 0.5
    while time.time() < end and not ok.is_set() and not finished.is_set():
        ok.wait(0.05)
    return ok.is_set()


def wait_online(what: str, hosts=(), max_wait: float | None = None, cancel: Callable[[], bool] | None = None,
                report: Callable[[str], None] | None = None) -> bool:
    """Gọi ngay sau khi gặp lỗi mạng.
    - Mạng vẫn có (máy chủ chậm / lỗi tạm thời) -> trả False: người gọi tính là 1 lần thử hỏng.
    - Mất mạng -> báo "Mất mạng – đang chờ…", kiểm tra mỗi 3 s; có mạng lại -> True (thử lại, không tính là hỏng).
    - Mất mạng quá `max_wait` -> NetworkDown; bấm Huỷ -> Cancelled."""
    c0, r0 = current()
    cancel = cancel or c0
    report = report or r0
    if max_wait is None:
        max_wait = NET_WAIT if (cancel or report) else NET_WAIT_QUICK
    if online(hosts):
        return False
    t0 = time.time()
    while True:
        waited = time.time() - t0
        if report:
            report(f"⚠ Mất mạng – {what} tạm dừng, có mạng lại sẽ tự làm tiếp "
                   f"(đã chờ {int(waited) // 60}:{int(waited) % 60:02d}, bấm Huỷ để dừng)")
        if waited > max_wait:
            raise NetworkDown(f"Mất mạng quá {round(max_wait / 60) or 1} phút khi {what}. Phần đã làm xong được giữ "
                              "lại – có mạng thì bấm chạy lại để làm tiếp phần còn thiếu.")
        sleep(CHECK_EVERY, cancel)
        if online(hosts):
            if report:
                report(f"Đã có mạng lại – tiếp tục {what}…")
            return True


# ------------------------------------------------------------------ gọi mạng huỷ được
def call(fn: Callable, *a, cancel: Callable[[], bool] | None = None, **kw):
    """Chạy fn ở luồng phụ, luồng tác vụ chờ và kiểm tra nút Huỷ mỗi 0,2 s -> bấm Huỷ dừng ngay, không phải đợi
    hết thời gian chờ của mạng (luồng phụ tự kết thúc khi mạng trả lời / hết giờ, kết quả bỏ đi)."""
    cancel = cancel or current()[0]
    if not cancel:
        return fn(*a, **kw)
    box: dict = {}
    done = threading.Event()

    def run():
        try:
            box["r"] = fn(*a, **kw)
        except BaseException as e:  # noqa: BLE001
            box["e"] = e
        finally:
            done.set()

    threading.Thread(target=run, daemon=True, name="trami-net").start()
    while not done.wait(0.2):
        if cancel():
            raise Cancelled()
    if "e" in box:
        raise box["e"]
    return box["r"]


# ------------------------------------------------------------------ phiên HTTP giữ kết nối sống
def _keepalive_opts() -> list[tuple[int, int, int]]:
    """TCP keepalive: đang chờ AI trả lời lâu (không có dữ liệu) mà rớt mạng -> ~40 s là biết (không phải đợi 5 phút)."""
    want = [(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)]
    for name, val in (("TCP_KEEPIDLE", 20), ("TCP_KEEPINTVL", 5), ("TCP_KEEPCNT", 4)):
        if hasattr(socket, name):
            want.append((socket.IPPROTO_TCP, getattr(socket, name), val))
    ok = []
    try:
        s = socket.socket()
        try:
            for o in want:   # Windows cũ không nhận vài tuỳ chọn -> bỏ tuỳ chọn đó (không thì mọi kết nối lỗi)
                try:
                    s.setsockopt(*o)
                    ok.append(o)
                except OSError:
                    pass
        finally:
            s.close()
    except OSError:
        pass
    return ok


_sessions = threading.local()


def session():
    """requests.Session (mỗi luồng 1 phiên) có TCP keepalive."""
    s = getattr(_sessions, "s", None)
    if s is None:
        import requests
        from requests.adapters import HTTPAdapter
        from urllib3.connection import HTTPConnection

        opts = HTTPConnection.default_socket_options + _keepalive_opts()

        class _Adapter(HTTPAdapter):
            def init_poolmanager(self, *a, **kw):
                kw["socket_options"] = opts
                super().init_poolmanager(*a, **kw)

            def proxy_manager_for(self, proxy, **kw):
                kw["socket_options"] = opts
                return super().proxy_manager_for(proxy, **kw)

        s = requests.Session()
        s.mount("https://", _Adapter())
        s.mount("http://", _Adapter())
        _sessions.s = s
    return s
