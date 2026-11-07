"""Đường hầm tool -> trạm VPS (2.3.0): tool TỰ NỐI RA VPS bằng 1 WebSocket (không cần mở cổng modem / IP tĩnh), trạm
VPS (vps/relay.py) chuyển từng yêu cầu của điện thoại xuống đây, tool hỏi máy chủ điều khiển trên chính máy rồi gửi trả.

Giao thức (khung nhị phân): 4 byte mã luồng + 1 byte loại + dữ liệu. Loại: 1 REQ_HEAD (JSON {m, p, h, ip}), 2 REQ_BODY,
3 REQ_END, 4 RESP_HEAD (JSON {s, h}), 5 RESP_BODY, 6 RESP_END, 7 CANCEL, 8 WINDOW (4 byte: trạm đã gửi xong bấy nhiêu
byte cho điện thoại -> tool gửi tiếp; mỗi luồng tối đa WINDOW byte chưa xác nhận – video dài không làm nghẽn luồng khác).
VPS có tên miền -> chứng chỉ thật (kiểm như thường); chỉ có IP -> chứng chỉ tự ký, ghim vân tay SHA-256."""
from __future__ import annotations

import asyncio
import json
import ssl
import struct

from .. import __version__

REQ_HEAD, REQ_BODY, REQ_END, RESP_HEAD, RESP_BODY, RESP_END, CANCEL, WINDOW = range(1, 9)
WINDOW_SIZE = 1 << 20
CHUNK = 64 * 1024
PATH = "/_sv_tunnel"
HOP = {"connection", "keep-alive", "proxy-authenticate", "proxy-authorization", "te", "trailer", "trailers",
       "transfer-encoding", "upgrade", "host", "content-length", "x-sv-relay", "x-sv-client"}


def frame(sid: int, kind: int, data: bytes = b"") -> bytes:
    return struct.pack(">IB", sid, kind) + data


def unframe(b: bytes) -> tuple[int, int, bytes]:
    sid, kind = struct.unpack(">IB", b[:5])
    return sid, kind, b[5:]


class _Stream:
    def __init__(self, head: dict):
        self.head = head
        self.body = bytearray()
        self.credit = WINDOW_SIZE
        self.more = asyncio.Event()
        self.task: asyncio.Task | None = None


class Tunnel:
    """Chạy trên vòng sự kiện của máy chủ điều khiển (server.py). state: connecting / on / error."""

    def __init__(self, url: str, key: str, fp: str, local: str, relay_token: str):
        self.url = url.rstrip("/")
        self.key, self.fp = key, fp
        self.local, self.relay_token = local, relay_token
        self.state, self.message = "connecting", "Đang nối tới VPS…"
        self.task: asyncio.Task | None = None
        self.ws = None
        self.streams: dict[int, _Stream] = {}
        self.send_lock: asyncio.Lock | None = None

    def start(self):
        self.task = asyncio.get_running_loop().create_task(self._loop())

    async def close(self):
        if self.task:
            self.task.cancel()
            try:
                await self.task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
        self.state, self.message = "off", ""

    def _ssl(self):
        import aiohttp

        if self.fp:
            try:
                return aiohttp.Fingerprint(bytes.fromhex(self.fp))
            except ValueError:
                raise ValueError("Vân tay VPS không hợp lệ – dán lại chuỗi kết nối VPS") from None
        return ssl.create_default_context()

    async def _loop(self):
        import aiohttp

        self.send_lock = asyncio.Lock()
        wait = 2.0
        ws_url = self.url.replace("https://", "wss://", 1).replace("http://", "ws://", 1) + PATH
        timeout = aiohttp.ClientTimeout(total=None, sock_connect=15, sock_read=None)
        async with aiohttp.ClientSession(timeout=timeout) as local:
            self.http = local
            while True:
                try:
                    self.state, self.message = "connecting", "Đang nối tới VPS…"
                    async with aiohttp.ClientSession(timeout=timeout) as s:
                        async with s.ws_connect(ws_url, ssl=self._ssl(), heartbeat=25, max_msg_size=0,
                                                headers={"Authorization": f"Bearer {self.key}",
                                                         "X-SV-Version": __version__}) as ws:
                            self.ws = ws
                            self.state, self.message = "on", "Đã nối VPS – điện thoại vào được từ mọi mạng"
                            wait = 2.0
                            async for msg in ws:
                                if msg.type == aiohttp.WSMsgType.BINARY:
                                    self._on_frame(msg.data)
                                elif msg.type in (aiohttp.WSMsgType.ERROR, aiohttp.WSMsgType.CLOSED):
                                    break
                            code = ws.close_code
                    self.ws = None
                    self._drop_all()
                    if code == 4401:
                        self.state, self.message = "error", "VPS từ chối mã kết nối – dán lại chuỗi kết nối VPS"
                        wait = 60.0
                    elif code == 4409:
                        self.state, self.message = "error", "Có máy tính khác đang nối VPS này bằng cùng mã"
                        wait = 30.0
                    else:
                        self.state, self.message = "connecting", "Mất kết nối VPS – đang nối lại…"
                except asyncio.CancelledError:
                    self._drop_all()
                    raise
                except aiohttp.ServerFingerprintMismatch:
                    self.state, self.message = "error", ("Vân tay chứng chỉ VPS không khớp – dán lại chuỗi kết nối "
                                                         "(VPS cài lại?) hoặc có kẻ chặn giữa đường")
                    wait = 60.0
                except Exception as e:  # noqa: BLE001
                    from ..config import err_text

                    self.ws = None
                    self._drop_all()
                    self.state, self.message = "error", f"Không nối được VPS: {err_text(e)[:160]} – thử lại sau {int(wait)} s"
                await asyncio.sleep(wait)
                wait = min(60.0, wait * 2)

    def _drop_all(self):
        for st in self.streams.values():
            if st.task:
                st.task.cancel()
        self.streams.clear()

    def _on_frame(self, b: bytes):
        if len(b) < 5:
            return
        sid, kind, data = unframe(b)
        st = self.streams.get(sid)
        if kind == REQ_HEAD:
            try:
                self.streams[sid] = _Stream(json.loads(data.decode("utf-8")))
            except ValueError:
                pass
        elif kind == REQ_BODY and st:
            if len(st.body) + len(data) <= 8 * 1024 * 1024:
                st.body += data
        elif kind == REQ_END and st and not st.task:
            st.task = asyncio.get_running_loop().create_task(self._serve(sid, st))
        elif kind == WINDOW and st and len(data) >= 4:
            st.credit += struct.unpack(">I", data[:4])[0]
            st.more.set()
        elif kind == CANCEL and st:
            if st.task:
                st.task.cancel()
            self.streams.pop(sid, None)

    async def _send(self, sid: int, kind: int, data: bytes = b""):
        ws = self.ws
        if ws is None or ws.closed:
            raise ConnectionResetError
        async with self.send_lock:
            await ws.send_bytes(frame(sid, kind, data))

    async def _serve(self, sid: int, st: _Stream):
        import aiohttp

        h = st.head
        headers = {k: v for k, v in (h.get("h") or []) if k.lower() not in HOP}
        headers["X-SV-Relay"] = self.relay_token
        headers["X-SV-Client"] = str(h.get("ip") or "")[:64]
        try:
            async with self.http.request(str(h.get("m") or "GET"), self.local + str(h.get("p") or "/"),
                                         headers=headers, data=bytes(st.body) if st.body else None, ssl=False,
                                         allow_redirects=False, auto_decompress=False) as r:
                hdr = [[k, v] for k, v in r.headers.items() if k.lower() not in HOP or k.lower() == "content-length"]
                await self._send(sid, RESP_HEAD, json.dumps({"s": r.status, "h": hdr}).encode("utf-8"))
                async for chunk in r.content.iter_chunked(CHUNK):
                    while st.credit <= 0:
                        st.more.clear()
                        await st.more.wait()
                    st.credit -= len(chunk)
                    await self._send(sid, RESP_BODY, chunk)
                await self._send(sid, RESP_END)
        except asyncio.CancelledError:
            raise
        except (ConnectionResetError, aiohttp.ClientError, OSError):
            try:
                await self._send(sid, CANCEL)
            except (ConnectionResetError, OSError, Exception):  # noqa: BLE001
                pass
        finally:
            self.streams.pop(sid, None)
