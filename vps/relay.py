#!/usr/bin/env python3
"""Trạm trung chuyển Subvideo trên VPS (2.3.0) – điện thoại ở ngoài mạng nhà điều khiển tool qua đây.

Tool trên máy tính TỰ NỐI RA trạm này bằng 1 WebSocket (/_sv_tunnel, mã kết nối trong config.json) – không phải mở cổng
modem. Mỗi yêu cầu HTTP của điện thoại được chuyển xuống tool qua kết nối đó (giao thức như trami/remote/tunnel.py) rồi gửi
trả lại. Trạm KHÔNG lưu gì: không mật khẩu, không dự án, không video (đăng nhập do chính tool kiểm).

Chạy: python3 relay.py --config /opt/subvideo-relay/config.json   (cai_vps.sh cài sẵn thành dịch vụ systemd)
config.json: {"secret": "...", "listen": "0.0.0.0", "port": 8443, "cert": "cert.pem", "key": "key.pem",
              "trust_proxy": false}
Chỉ cần Python 3.8+ và aiohttp ≥ 3.8 (Ubuntu 22.04: apt install python3-aiohttp)."""
from __future__ import annotations

import argparse
import asyncio
import hmac
import itertools
import json
import logging
import ssl
import struct
import time
from pathlib import Path

from aiohttp import WSMsgType, web

REQ_HEAD, REQ_BODY, REQ_END, RESP_HEAD, RESP_BODY, RESP_END, CANCEL, WINDOW = range(1, 9)
PATH = "/_sv_tunnel"
HOP = {"connection", "keep-alive", "proxy-authenticate", "proxy-authorization", "te", "trailer", "trailers",
       "transfer-encoding", "upgrade", "host", "content-length", "x-forwarded-for", "x-real-ip", "x-sv-relay",
       "x-sv-client", "forwarded"}
RESP_DROP = {"connection", "keep-alive", "transfer-encoding", "upgrade", "proxy-authenticate", "trailer"}
HEAD_TIMEOUT = 600          # tool có thể cần vài phút (nghe thử giọng AI lần đầu nạp mô hình)
BODY_MAX = 4 * 1024 * 1024
MAX_STREAMS = 96
FAIL_WIN, FAIL_MAX = 600, 10
log = logging.getLogger("subvideo-relay")

OFFLINE_HTML = """<!doctype html><html lang="vi"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Subvideo</title>
<style>body{margin:0;background:#0B0E14;color:#E8ECF3;font:15px/1.5 system-ui,sans-serif;display:grid;
place-items:center;min-height:100vh;padding:16px;box-sizing:border-box}div{max-width:420px;background:#141925;
border:1px solid #252C3A;border-radius:14px;padding:22px}b{color:#FF8A3D}</style></head><body><div>
<b>Máy tính chưa kết nối</b><p>Tool Subvideo trên máy tính đang tắt, máy đang ngủ hoặc mất mạng.
Mở tool → Cài đặt → Điều khiển từ xa (bật + có dòng "Đã nối VPS") rồi tải lại trang này.</p></div></body></html>"""


def frame(sid: int, kind: int, data: bytes = b"") -> bytes:
    return struct.pack(">IB", sid, kind) + data


class _Pending:
    def __init__(self):
        self.head: asyncio.Future = asyncio.get_running_loop().create_future()
        self.chunks: asyncio.Queue = asyncio.Queue()


class Relay:
    def __init__(self, secret: str, trust_proxy: bool):
        self.secret = secret
        self.trust_proxy = trust_proxy
        self.ws: web.WebSocketResponse | None = None
        self.pending: dict[int, _Pending] = {}
        self.ids = itertools.count(1)
        self.lock = asyncio.Lock()
        self.fails: dict[str, list[float]] = {}
        self.pc_since = 0.0

    def client_ip(self, request: web.Request) -> str:
        ip = request.remote or ""
        if self.trust_proxy and ip in ("127.0.0.1", "::1"):
            xff = request.headers.get("X-Forwarded-For", "")
            if xff:
                ip = xff.split(",")[-1].strip()
        return ip

    def _blocked(self, ip: str) -> bool:
        now = time.time()
        lst = [t for t in self.fails.get(ip, []) if now - t < FAIL_WIN]
        self.fails[ip] = lst
        return len(lst) >= FAIL_MAX

    async def send(self, sid: int, kind: int, data: bytes = b""):
        ws = self.ws
        if ws is None or ws.closed:
            raise ConnectionResetError
        async with self.lock:
            await ws.send_bytes(frame(sid, kind, data))

    # ------------------------------------------------------------ tool nối lên
    async def tunnel(self, request: web.Request):
        ip = self.client_ip(request)
        if self._blocked(ip):
            raise web.HTTPTooManyRequests()
        auth = request.headers.get("Authorization", "")
        tok = auth[7:].strip() if auth.startswith("Bearer ") else ""
        if not tok or not hmac.compare_digest(tok, self.secret):
            self.fails.setdefault(ip, []).append(time.time())
            await asyncio.sleep(1)
            ws = web.WebSocketResponse()
            await ws.prepare(request)
            await ws.close(code=4401, message=b"bad key")
            return ws
        ws = web.WebSocketResponse(heartbeat=25, max_msg_size=0)
        await ws.prepare(request)
        old = self.ws
        self.ws, self.pc_since = ws, time.time()
        if old is not None and not old.closed:
            await old.close(code=4000, message=b"replaced")
        self._fail_all()
        log.info("tool đã nối từ %s (bản %s)", ip, request.headers.get("X-SV-Version", "?"))
        try:
            async for msg in ws:
                if msg.type == WSMsgType.BINARY and len(msg.data) >= 5:
                    self._on_frame(msg.data)
                elif msg.type == WSMsgType.ERROR:
                    break
        finally:
            if self.ws is ws:
                self.ws = None
                self._fail_all()
                log.info("tool ngắt kết nối")
        return ws

    def _fail_all(self):
        for p in self.pending.values():
            if not p.head.done():
                p.head.set_exception(ConnectionResetError())
            p.chunks.put_nowait(None)
        self.pending.clear()

    def _on_frame(self, b: bytes):
        sid, kind = struct.unpack(">IB", b[:5])
        data = b[5:]
        p = self.pending.get(sid)
        if p is None:
            return
        if kind == RESP_HEAD and not p.head.done():
            try:
                p.head.set_result(json.loads(data.decode("utf-8")))
            except ValueError:
                p.head.set_exception(ConnectionResetError())
        elif kind == RESP_BODY:
            p.chunks.put_nowait(data)
        elif kind in (RESP_END, CANCEL):
            if not p.head.done():
                p.head.set_exception(ConnectionResetError())
            p.chunks.put_nowait(None)

    # ------------------------------------------------------------ điện thoại
    async def handle(self, request: web.Request):
        if request.path == PATH:
            return await self.tunnel(request)
        if self.ws is None or self.ws.closed:
            if request.path.startswith("/api/"):
                return web.json_response({"error": "Máy tính chưa kết nối VPS (tool tắt / máy ngủ / mất mạng)",
                                          "offline": True}, status=503)
            return web.Response(text=OFFLINE_HTML, content_type="text/html", status=503)
        if len(self.pending) >= MAX_STREAMS:
            return web.json_response({"error": "Trạm VPS đang bận – thử lại"}, status=503)
        if request.content_length and request.content_length > BODY_MAX:
            raise web.HTTPRequestEntityTooLarge(max_size=BODY_MAX, actual_size=request.content_length)
        body = await request.read()
        sid = next(self.ids) & 0xFFFFFFFF
        p = _Pending()
        self.pending[sid] = p
        head = {"m": request.method, "p": request.path_qs, "ip": self.client_ip(request),
                "h": [[k, v] for k, v in request.headers.items() if k.lower() not in HOP]}
        resp: web.StreamResponse | None = None
        try:
            await self.send(sid, REQ_HEAD, json.dumps(head).encode("utf-8"))
            for i in range(0, len(body), 256 * 1024):
                await self.send(sid, REQ_BODY, body[i:i + 256 * 1024])
            await self.send(sid, REQ_END)
            h = await asyncio.wait_for(p.head, HEAD_TIMEOUT)
            resp = web.StreamResponse(status=int(h.get("s") or 502))
            clen = None
            for k, v in h.get("h") or []:
                lk = k.lower()
                if lk in RESP_DROP:
                    continue
                if lk == "content-length":
                    clen = int(v)
                    continue
                resp.headers.add(k, v)
            if clen is not None:
                resp.content_length = clen
            await resp.prepare(request)
            while True:
                chunk = await p.chunks.get()
                if chunk is None:
                    break
                await resp.write(chunk)
                try:
                    await self.send(sid, WINDOW, struct.pack(">I", len(chunk)))
                except ConnectionResetError:
                    break
            await resp.write_eof()
            return resp
        except (asyncio.TimeoutError, ConnectionResetError):
            if resp is None:
                return web.json_response({"error": "Máy tính không trả lời (mất kết nối VPS)"}, status=502)
            try:      # điện thoại ngắt giữa chừng (aiohttp ≥ 3.9 báo bằng lỗi ghi) -> báo tool thôi gửi
                await self.send(sid, CANCEL)
            except ConnectionResetError:
                pass
            return resp
        except asyncio.CancelledError:   # điện thoại bỏ dở (tua video…) -> báo tool thôi gửi
            try:
                await self.send(sid, CANCEL)
            except ConnectionResetError:
                pass
            raise
        finally:
            self.pending.pop(sid, None)


def main():
    ap = argparse.ArgumentParser(description="Trạm trung chuyển Subvideo")
    ap.add_argument("--config", default=str(Path(__file__).with_name("config.json")))
    a = ap.parse_args()
    cfg_path = Path(a.config)
    cfg = json.loads(cfg_path.read_text("utf-8"))
    secret = str(cfg.get("secret") or "")
    if len(secret) < 16:
        raise SystemExit("config.json thiếu 'secret' (≥ 16 ký tự)")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    relay = Relay(secret, bool(cfg.get("trust_proxy")))
    app = web.Application(client_max_size=BODY_MAX)
    app.router.add_route("*", "/{tail:.*}", relay.handle)
    ctx = None
    if cfg.get("cert") and cfg.get("key"):
        base = cfg_path.parent
        ctx = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
        ctx.load_cert_chain(str(base / cfg["cert"]), str(base / cfg["key"]))
    web.run_app(app, host=cfg.get("listen", "0.0.0.0"), port=int(cfg.get("port", 8443)), ssl_context=ctx,
                access_log=None, print=lambda *x: log.info("trạm Subvideo đang chạy – cổng %s", cfg.get("port", 8443)))


if __name__ == "__main__":
    main()
